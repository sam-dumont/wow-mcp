"""Writes data/dungeons/quests.json: which dungeon each quest belongs to,
so a dungeon quest handed in outside (City of Dalaran's at the Silverpine
grate, the Deadmines' in Westfall, the Stockade's in Stormwind) counts
toward the run it came from instead of the zone it was handed in
(wowstats.dungeonquests, owner 2026-11-24).

Every source is Forever's own or Polestar's word on Forever, never Classic
data: forever-db at its pinned release (tools/forever-db-tag, the one
data/forever-reference.json is exported from), the rest in the Polestar checkout at
tools/polestar-tag:

- `zone`: forever-db's zone for the quest is the dungeon's area (the place
  its work is done, wherever it is picked up), the instance's own area or
  the same-named one the client zones its quests in (Gnomeregan 133/721).
  The areas and each instance's own area are data/forever-reference.json's.
- `journal`: Polestar's cited table of dungeon quests no rule places
  (data/codex/journal-quests.json: a giver, ender and zone outside, the
  objective inside; each row cites Forever's quest data or players'
  recordings). Its `exclude` rows take a quest off (City of Dalaran's city
  quests, zoned in the dungeon's area).
- `route`: Polestar's dungeon runs (data/dungeon-runs.json): the quests a
  run picks up, works on inside, starts from an item inside, and hands in.

- `boss`: the quest kills one of the dungeon's bosses (forever-db's kill
  objectives, the creature's name among the instance's DungeonEncounter
  names in data/forever-reference.json): The Saltseer Captain is handed in
  at Booty Bay with no zone, its target Captain Dreadrise is The Drowned
  City's. Against every quest the other sources place, it agreed on all 31.

forever-db's own dungeon flag (quest type 81) is kept beside a quest that
one of the three places (`flag`); alone it doesn't say which dungeon.

The dungeons are data/dungeons/kill-xp.json's list (every dungeon the
Dungeons panel shows), with their instance map from Polestar's dungeon art
table (addon/Polestar/Dungeons/Art.lua: the five newest have no map in the
client tables we bundle). A dungeon with no quest says why: Polestar's own
reason when its dungeon runs give one, else NONE below. The test
tests/test_dungeonquests.py holds every dungeon to one or the other.

Stdlib only, the same bytes on every run:

    uv run python tools/dungeon_quests/export.py --polestar <checkout>
    uv run python tools/dungeon_quests/export.py --polestar <checkout> --check   # exit 1 when it differs
    ... [--forever-db-cache DIR]   # the forever reference export's, so forever-db isn't fetched twice
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "dungeons" / "quests.json"
DUNGEON_FLAG = 81  # QuestInfo 81: Dungeon

# A dungeon with no quest in any source and no reason from Polestar's runs.
NONE: dict[str, str] = {}

# Agamaggan's Blood moved outside Razorfen Downs on November 17. Keep the
# official change with the dataset so route and journal consumers agree.
OUTDOOR = {93604: "https://us.forums.blizzard.com/en/wow/t/2403040/12"}


def norm(name: str) -> str:
    """'The Stockade', 'Stormwind Stockade' and 'Stockade' alike."""
    n = re.sub(r"^(the |stormwind )", "", (name or "").lower().strip())
    return re.sub(r"[^a-z]", "", n)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower().replace("'", "")).strip("-")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def art_maps(ps: Path) -> dict[int, str]:
    """{instance map: name} from Polestar's dungeon art table."""
    out = {}
    for m in re.finditer(r"^\s*\[(\d+)\]\s*=\s*\{[^}]*\},\s*--\s*(.+?)\s*$",
                         (ps / "addon" / "Polestar" / "Dungeons" / "Art.lua").read_text(encoding="utf-8"), re.M):
        out[int(m.group(1))] = m.group(2)
    return out


def build(ps: Path, db: Path | None = None) -> dict:
    """`db`: an unpacked forever-db (its `forever-db/` directory), by default
    the one in the Polestar checkout; main() passes the release
    data/forever-reference.json was exported from."""
    db = db or ps / "forever-db"
    ref = json.loads((ROOT / "data" / "forever-reference.json").read_text(encoding="utf-8"))
    kill = json.loads((ROOT / "data" / "dungeons" / "kill-xp.json").read_text(encoding="utf-8"))["dungeons"]
    art = art_maps(ps)
    by_norm = {norm(n): m for m, n in art.items()}
    dungeons: dict[int, str] = {}
    for name, d in kill.items():
        m = d.get("map") or by_norm.get(norm(name))
        if m is None:
            raise SystemExit(f"{name}: no instance map in kill-xp.json or Polestar's Art.lua")
        dungeons[int(m)] = name
    map_of_norm = {norm(n): m for m, n in dungeons.items()}

    # The instance areas and their same-named twins (forever.dungeon_area's rule).
    areas = ref["areas"]
    inst_area = {int(m): int(x["area"]) for m, x in (ref.get("maps") or {}).items()
                 if x.get("area") and (areas.get(str(x["area"])) or {}).get("instance") == "dungeon"}
    # A twin carries the name of the dungeon's own area (Razorfen Kraul 1717
    # outside, 491 inside), never the name of any area on its map: the
    # client's Deadmines map has an area named Westfall, City of Dalaran's
    # one named Hillsbrad Foothills, and the zones' quests aren't theirs.
    by_name = {areas[str(a)]["name"]: m for m, a in inst_area.items()}
    map_of_area = {}
    for k, a in areas.items():
        m = int(a["map"]) if a.get("instance") == "dungeon" and a.get("map") else by_name.get(a["name"])
        if m is not None and m in dungeons:
            map_of_area[int(k)] = m

    # Codex instance keys: entrances.json, else the art table's name as a slug.
    codex = ps / "data" / "codex"
    key_map = {e["key"]: int(e["instance"]) for e in json.loads((codex / "entrances.json").read_text())["entrances"]}
    for m, n in art.items():
        key_map.setdefault(slug(n), m)
        key_map.setdefault(slug(re.sub(r"^The ", "", n)), m)

    fdb = db / "data"
    quests = {q["id"]: q for q in jsonl(fdb / "quest.jsonl")}
    found: dict[int, dict[int, set[str]]] = {}

    def add(qid, m, src):
        # A journal row's quest can be newer than forever-db (Alcaz Prison's,
        # Blackmaw Hold's): the row cites Forever's quest data or recordings.
        if qid not in OUTDOOR and (qid in quests or src == "journal") and m in dungeons:
            found.setdefault(int(qid), {}).setdefault(int(m), set()).add(src)

    for qid, q in quests.items():
        m = map_of_area.get(q.get("zone_area"))
        if m is not None:
            add(qid, m, "zone")
    boss_map: dict[str, set[int]] = {}
    for a in areas.values():
        if a.get("instance") == "dungeon" and a.get("map") in dungeons:
            for b in a.get("bosses") or []:
                boss_map.setdefault(b, set()).add(int(a["map"]))
    npc = {n["id"]: n.get("name") for n in jsonl(fdb / "npc.jsonl")}
    for o in jsonl(fdb / "quest_objective.jsonl"):
        if o.get("kind") == "kill":
            for m in boss_map.get(npc.get(o.get("target")), ()):
                add(o["quest"], m, "boss")
    journal = json.loads((codex / "journal-quests.json").read_text(encoding="utf-8"))["quests"]
    for r in journal:
        m = key_map.get(r["instance"])
        if m is None or r.get("exclude"):
            continue
        add(r["quest"], m, "journal")
    for r in journal:  # an exclude row wins over every rule, as in the Codex
        m = key_map.get(r["instance"])
        if r.get("exclude") and m is not None:
            found.get(r["quest"], {}).pop(m, None)
            if r["quest"] in found and not found[r["quest"]]:
                del found[r["quest"]]
    runs = json.loads((ps / "data" / "dungeon-runs.json").read_text(encoding="utf-8"))
    for r in runs["runs"]:
        m = map_of_norm.get(norm(r["dungeon"]))
        run = r.get("run") or {}
        ids = ([p.get("quest") for p in r.get("pickups") or []] + list(run.get("objectives") or [])
               + [x.get("quest") for x in run.get("itemStarts") or []]
               + [h.get("quest") for h in r.get("handins") or []])
        for qid in ids:
            if isinstance(qid, int) and m is not None:
                add(qid, m, "route")
    for qid, ms in found.items():
        if (quests.get(qid) or {}).get("type") == DUNGEON_FLAG:
            for s in ms.values():
                s.add("flag")

    why_not = {}
    for r in runs.get("notInRoute") or []:
        m = map_of_norm.get(norm(r["dungeon"]))
        if m is not None:
            why_not.setdefault(m, r["why"])
    count = {m: 0 for m in dungeons}
    for ms in found.values():
        for m in ms:
            count[m] += 1
    out_d = {}
    for m in sorted(dungeons):
        d = {"name": dungeons[m], "quests": count[m]}
        if m in inst_area:
            d["area"] = inst_area[m]
        if not count[m]:
            why = NONE.get(dungeons[m]) or why_not.get(m)
            if not why:
                raise SystemExit(f"{dungeons[m]} ({m}): no quest in any source and no reason: add one to NONE")
            d["none"] = why
        out_d[str(m)] = d
    order = ("zone", "boss", "journal", "route", "flag")
    pkg = json.loads((db / "datapackage.json").read_text(encoding="utf-8"))
    return {
        "about": "Which dungeon (instance map) each quest belongs to, so its hand-in counts toward the run it came "
                 "from (wowstats.dungeonquests). quests: {quest id: [[map, [sources]]]}; sources: zone (forever-db "
                 "zones it in the dungeon), boss (it kills one of the dungeon's bosses), journal (Polestar's cited "
                 "journal-quests.json), route (Polestar's dungeon runs), flag (forever-db's dungeon type, beside "
                 "one of the others). dungeons: {map: {name, area, quests, none?}}: `none` says why a dungeon has "
                 "no quest. Written by tools/dungeon_quests/export.py.",
        "source": {"polestar": (ROOT / "tools" / "polestar-tag").read_text().strip(),
                   "forever_db": pkg.get("version"), "client_build": pkg.get("clientBuild"),
                   "licence": "GPL-3.0 (forever-db and Polestar's data, THIRD_PARTY.md)"},
        "outdoor": OUTDOOR,
        "dungeons": out_d,
        "quests": {str(q): [[m, [s for s in order if s in found[q][m]]] for m in sorted(found[q])]
                   for q in sorted(found)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--polestar", required=True, type=Path, help="a Polestar checkout at tools/polestar-tag")
    ap.add_argument("--check", action="store_true", help="exit 1 when data/dungeons/quests.json differs")
    ap.add_argument("--forever-db-cache", type=Path,
                    help="where tools/forever_reference/export.py unpacked forever-db (default: fetch it again)")
    a = ap.parse_args()
    # forever-db at its pin, the forever reference's, not necessarily the copy
    # in Polestar's checkout: the two files must agree on the quests.
    sys.path.insert(0, str(ROOT / "tools" / "forever_reference"))
    import tempfile

    import upstream

    db = upstream.forever_db(upstream.pinned()["forever_db"],
                             a.forever_db_cache or Path(tempfile.mkdtemp(prefix="forever-db-")), a.polestar)
    # indent 1 (one key per line, like data/dungeons/wings.json): git merges disjoint changes, where the
    # one-line file conflicted on every PR that touched it (2026-11-25).
    text = json.dumps(build(a.polestar, db), ensure_ascii=False, indent=1) + "\n"
    if a.check:
        if OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.relative_to(ROOT)} is not the export: rerun tools/dungeon_quests/export.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    d = json.loads(text)
    for m, x in d["dungeons"].items():
        print(f"{m:>5} {x['name']}: {x['quests']}" + (f" (none: {x['none'][:60]})" if "none" in x else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
