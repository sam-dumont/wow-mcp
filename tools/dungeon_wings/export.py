"""Writes data/dungeons/wings.json: which wing of a dungeon run in parts
each creature, object and quest belongs to, so the Dungeons panel, the
Overview and the MCP give each wing its own line (owner, 2026-11-23:
"it's well known that people do different parts").

A wing is a part of a dungeon the game itself separates: its own entrance,
or its own way in behind a key (Scarlet Monastery's four, Dire Maul's three,
Blackrock Spire's Lower and Upper, Stratholme's Live and Undead sides,
Maraudon's Orange, Purple and Inner). The wings and their bosses are
tools/dungeon_xp/export.py's WINGS, so the kill model and this file cut
the same lines: each creature and object spawn goes to the wing of the
nearest boss spawn (cMaNGOS has no wing column).

A quest goes to the wings where its objectives are: a kill or talk target's
spawns, an object's, or for an item the creatures and objects that hold it
(forever-db's item sources). Each objective says the wings it can be done
in; a quest needs a wing when one of its objectives can be done there only
("all": every such wing on one trip, Into The Scarlet Monastery), else it
can be done in any of them ("any": Hearts of Zeal). A quest with no
objective inside the dungeon (a ring from a ghost outside, a talk quest)
is "outside": it belongs to no wing.

Inputs, both pinned:

- the cMaNGOS classic-db dump tools/dungeon_xp/export.py names (creature
  and gameobject spawns, with the pools' entries);
- a Polestar checkout at data/route-reference.json's tag, for forever-db's
  quests, objectives and item sources (forever-db/data/*.jsonl).

    uv run python tools/dungeon_wings/export.py --cmangos ClassicDB.sql.gz --polestar <checkout>
    uv run python tools/dungeon_wings/export.py --cmangos ... --polestar ... --check   # exit 1 when it differs

Stdlib only, the same bytes on every run.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dungeon_xp"))
import export as X  # noqa: E402 - tools/dungeon_xp/export.py: the wings, the dump's parser

ROOT = X.ROOT
OUT = ROOT / "data" / "dungeons" / "wings.json"

# The KB rule each wing's level range comes from (data/kb/research/dungeons.json);
# none: the dungeon's own range (Maraudon: "no Forever source gives separate
# ranges for the wings").
RULES = {
    189: {"Graveyard": "dungeon.sm_graveyard", "Library": "dungeon.sm_library", "Armory": "dungeon.sm_armory",
          "Cathedral": "dungeon.sm_cathedral"},
    429: {"East": "dungeon.dire_maul_east", "West": "dungeon.dire_maul_west", "North": "dungeon.dire_maul_north"},
    229: {"Lower": "dungeon.lbrs", "Upper": "dungeon.ubrs"},
    329: {"Live": "dungeon.strat_live", "Undead": "dungeon.strat_undead"},
    349: {},
}

# Quests whose objective the data places nowhere (a book with no item
# source, a summoned or scripted target, a timed rescue), placed by the
# knowledge base's research statement that names the wing. Only for a
# quest the objectives leave "outside"; the rest stay outside.
CITED = {
    78676: (["Library"], "dungeons:KB-31:7"), 96800: (["Library"], "dungeons:KB-31:5"),
    4729: (["Lower"], "dungeons:KB-51:6"), 4862: (["Lower"], "dungeons:KB-51:6"),
    4866: (["Lower"], "dungeons:KB-51:6"),
    5214: (["Live"], "dungeons:KB-57:5"), 5282: (["Live"], "dungeons:KB-57:5"),
    8945: (["Undead"], "dungeons:KB-58:4"),
    7029: (["Orange"], "dungeons:KB-37b:2"), 7041: (["Orange"], "dungeons:KB-37b:2"),
    7066: (["Inner"], "dungeons:KB-37:3"),
    5526: (["East"], "dungeons:KB-53:5"),
}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def spawns(txt: str, maps: set[int]) -> dict[str, dict[int, list[tuple]]]:
    """{kind: {id: [(map, x, y, z)]}} for every creature and gameobject placed on these maps."""
    entries: dict[int, list[int]] = {}
    for r in X._rows(txt, "creature_spawn_entry"):
        entries.setdefault(int(r["guid"]), []).append(int(r["entry"]))
    out: dict[str, dict[int, list[tuple]]] = {"npc": {}, "object": {}}
    for table, kind in (("creature", "npc"), ("gameobject", "object")):
        for r in X._rows(txt, table):
            m = int(r["map"])
            if m not in maps:
                continue
            at = (m, float(r["position_x"]), float(r["position_y"]), float(r["position_z"]))
            ids = [int(r["id"])] if int(r["id"]) else (entries.get(int(r["guid"]), []) if kind == "npc" else [])
            for i in ids:
                out[kind].setdefault(i, []).append(at)
    return out


def build(cm_path: Path, polestar: Path) -> dict:
    txt = X._sql(cm_path)
    ref = json.loads((ROOT / "data" / "forever-reference.json").read_text())
    areas = ref["areas"]
    # Each instance map's own area (`maps`), never any area on the map: every
    # room of a dungeon is an area too since #266, and Dire Maul came out as
    # "The Maul UNUSED" (the same fix as the route trips').
    by_map = {int(mid): (int(m["area"]), areas[str(m["area"])]["name"]) for mid, m in ref["maps"].items()
              if m.get("area") and areas.get(str(m["area"]), {}).get("instance") == "dungeon"}
    sp = spawns(txt, set(X.WINGS))
    npc_name = {}
    fdb = polestar / "forever-db" / "data"
    for r in jsonl(fdb / "npc.jsonl"):
        npc_name[r["id"]] = r.get("name")
    quests = {r["id"]: r for r in jsonl(fdb / "quest.jsonl")}
    objectives: dict[int, list[dict]] = {}
    for r in jsonl(fdb / "quest_objective.jsonl"):
        objectives.setdefault(r["quest"], []).append(r)
    who: dict[str, dict[int, list[tuple[str, int]]]] = {"start": {}, "end": {}}
    for k in who:
        for r in jsonl(fdb / f"quest_{k}.jsonl"):
            who[k].setdefault(r["quest"], []).append((r["kind"], r["id"]))
    sources: dict[int, list[tuple[str, int]]] = {}
    for r in jsonl(fdb / "item_source.jsonl"):
        sources.setdefault(r["item"], []).append((r["kind"], r["id"]))
    trips = json.loads((ROOT / "data" / "route-reference.json").read_text())["trips"]["dungeons"]
    out = {}
    for mid, wings in sorted(X.WINGS.items()):
        aid, name = by_map[mid]
        anchors = [(w, s[1:]) for w, names in wings.items() for n in names
                   for i, nm in npc_name.items() if nm == n for s in sp["npc"].get(i, []) if s[0] == mid]
        missing = [n for w, names in wings.items() for n in names if not any(a for a in anchors if a[0] == w)]
        if not anchors or any(not [a for a in anchors if a[0] == w] for w in wings):
            raise SystemExit(f"{name}: a wing has no boss spawn ({missing})")

        def wing_of(at: tuple) -> str:
            return min(anchors, key=lambda a: math.dist(a[1], at[1:]))[0]  # noqa: B023 - this map's anchors

        placed = {kind: {i: sorted({wing_of(s) for s in ss if s[0] == mid}, key=list(wings).index)
                         for i, ss in sp[kind].items() if any(s[0] == mid for s in ss)} for kind in sp}

        boss_wing = {n: w for w, names in wings.items() for n in names}

        def where(kind: str, i: int) -> list[str]:
            """The wings a creature or object is placed in; a boss the
            game summons by script (Ramstein, Rend, Urok) has no placed
            spawn: its wing by name."""
            got = placed.get(kind, {}).get(i)
            if not got and kind == "npc" and npc_name.get(i) in boss_wing:
                got = [boss_wing[npc_name[i]]]
            return got or []

        page = {q for q, r in quests.items() if r.get("zone_area") == aid} | set(trips[str(mid)]["page"])
        qs = {}
        for qid in sorted(page):
            by = []
            for o in sorted(objectives.get(qid) or [], key=lambda o: o.get("n") or 0):
                t = o.get("target")
                if o["kind"] in ("kill", "talk"):
                    ws = where("npc", t)
                elif o["kind"] == "object":
                    ws = where("object", t)
                elif o["kind"] == "item":
                    ws = sorted({w for k, i in sources.get(t) or []
                                 for w in where("npc" if k == "npc" else "object", i)}, key=list(wings).index)
                else:
                    ws = []
                by.append([o["kind"], t, ws])
            inside = [ws for *_, ws in by if ws]
            if not inside:
                # No objective placed inside: the one who takes it back, or
                # gives it, when that one stands in a wing (The Gordok Taste
                # Test); else the quest is done outside every wing.
                for k in ("end", "start"):
                    at = sorted({w for kind, i in who[k].get(qid) or [] if kind in ("npc", "object")
                                 for w in where(kind, i)}, key=list(wings).index)
                    if at:
                        by.append([k, None, at])
                        inside = [at]
                        break
            if not inside and qid in CITED:
                by.append(["kb", CITED[qid][1], CITED[qid][0]])
                inside = [CITED[qid][0]]
            if not inside:
                qs[str(qid)] = {"wings": [], "how": "outside", "by": by}
                continue
            need = {w for ws in inside if len(ws) == 1 for w in ws}
            every = sorted({w for ws in inside for w in ws}, key=list(wings).index)
            how = "one" if len(every) == 1 else "all" if len(need) > 1 else "any"
            # "all" counts the wings it can only be done in, plus any an
            # either-wing objective needs when none of those covers it.
            if how == "all":
                every = sorted(need | {ws[0] for ws in inside if not set(ws) & need}, key=list(wings).index)
            qs[str(qid)] = {"wings": every, "how": how, "by": by}
        out[str(mid)] = {
            "name": name, "area": aid,
            "wings": {w: {"name": f"{name}: {w}", "rule": RULES[mid].get(w),
                          "bosses": sorted({i for i, nm in npc_name.items() if nm in names and i in placed["npc"]}),
                          **({"party": X.WING_PARTY[(mid, w)]} if (mid, w) in X.WING_PARTY else {})}
                      for w, names in wings.items()},
            "npcs": {str(i): ws for i, ws in sorted(placed["npc"].items())},
            "objects": {str(i): ws for i, ws in sorted(placed["object"].items())},
            "quests": qs,
        }
    return {
        "about": "The wings of the dungeons run in parts: each creature and object placed inside to the wing of "
                 "the nearest boss, each quest to the wings its objectives are in (how: one, all = needs each, "
                 "any = either, outside = none inside; by: [objective kind, target, wings], or [start|end, null, "
                 "wings] for the giver or ender standing in a wing, or [kb, statement id, wings] for a quest the "
                 "knowledge base places). Read by "
                 "wowstats/plan/wings.py; built by tools/dungeon_wings/export.py, do not edit.",
        "as_of": X.AS_OF,
        "sources": {"cmangos": {"repo": X.CMANGOS_REPO, "commit": X.CMANGOS_COMMIT, "file": X.CMANGOS_FILE,
                                "licence": "GPL-3.0", "what": "creature and gameobject spawns"},
                    "forever_db": {"what": "quests, objectives and item sources",
                                   # The release read (tools/kb/checkout.py's overlay), not the
                                   # one the addon bundles for the route times.
                                   "version": json.loads((polestar / "forever-db" / "index.json")
                                                         .read_text())["version"],
                                   "polestar": json.loads((ROOT / "data" / "route-reference.json")
                                                          .read_text())["polestar"]}},
        "dungeons": out,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cmangos", type=Path, required=True)
    ap.add_argument("--polestar", type=Path, required=True)
    ap.add_argument("--check", action="store_true", help="compare with the committed file, write nothing")
    a = ap.parse_args()
    text = json.dumps(build(a.cmangos, a.polestar), ensure_ascii=False, indent=1) + "\n"
    if a.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: rerun without --check", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
