"""Writes data/forever-reference.json: Forever's quest names and its
instances, so the server names every quest and dungeon it shows.

Two sources, both facts about the game:

- forever-db (Polestar's quest database, GPL-3.0, in the Polestar checkout at
  tools/polestar-tag): each quest's title, level, minimum level and zone, its part
  in a chain of quests with the same title (from the prerequisites), the
  quests it needs done first (breadcrumbs left out), each item's name
  (Forever's ItemSparse holds only the items Forever changed), and the
  gear only one quest gives (wowstats.quests: holding it means the quest
  was handed in).
- Forever's client tables on wago.tools, at the client build the knowledge
  base was last checked against (data/kb/watch.json `client_build.known`,
  moved by the refresh job's client-build lane, which reruns this export):
  AreaTable (zone names), Map (which areas are dungeons or raids), UiMap
  (the map ids ForeverDB.net places quest givers on), DungeonEncounter (each
  instance's bosses), Faction (each
  reputation's side: a faction
  under Alliance or Alliance Forces is the Alliance's, under Horde or Horde
  Forces the Horde's, the rest neutral) and GroupFinderActivity (a dungeon's level
  range, as the game's Group Finder shows it, the rule Polestar's Codex
  uses), with the Codex's named exceptions (data/codex/levels.json, `max`).
- Polestar's floor for each dungeon (its Codex rule, journal.rs levels_of):
  the range's bottom or players' recorded 25th percentile entry level
  (data/codex/recorded.json, 5 people with a boss kill or more), whichever
  is higher, or the named exception's `floor`. Polestar's Dungeons hub lists
  a dungeon "For your level" from its floor to its top; wowstats.plan.vsguide
  sets the advice against that list.

Stdlib only, the same file byte for byte on every run:

    uv run python tools/forever_reference/export.py --polestar /tmp/polestar
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

WAGO = "https://wago.tools/db2/{table}/csv?build={build}"
OUT = Path(__file__).resolve().parents[2] / "data" / "forever-reference.json"
WATCH = OUT.parent / "kb" / "watch.json"
INSTANCE = {"1": "dungeon", "2": "raid"}
# The client's Faction roots of each side: Alliance and Alliance Forces (the
# battleground reputations), Horde and Horde Forces.
SIDE_ROOTS = {"469": "Alliance", "891": "Alliance", "67": "Horde", "892": "Horde"}


def table(name: str, build: str) -> list[dict]:
    req = urllib.request.Request(WAGO.format(table=name, build=build), headers={"User-Agent": "wow-stats export"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        raise SystemExit(f"wago.tools has no {name} rows for {build}")
    return rows


def i(v) -> int:
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower().replace("'", "")).strip("-")


# Polestar's FLOOR_PEOPLE (rust/crates/forge-data/src/codex/journal.rs): fewer
# recorded people and the floor is the range's bottom.
FLOOR_PEOPLE = 5


def floor_of(levels: list[int], recorded: dict | None, override: dict | None) -> float:
    """Polestar's floor for a dungeon (journal.rs levels_of): the named
    exception's, else players' 25th percentile entry level to one decimal
    (half away from zero, as Rust rounds) when enough of them were
    recorded, never under the range's bottom."""
    if override and isinstance(override.get("floor"), (int, float)):
        return float(override["floor"])
    lo = float(levels[0])
    r = recorded or {}
    if (r.get("people") or 0) >= FLOOR_PEOPLE and isinstance(r.get("p25"), (int, float)):
        p = int(r["p25"] * 10 + 0.5) / 10
        return max(p, lo)
    return lo


def chain_parts(db: Path, titles: dict[str, str]) -> dict[str, list[int]]:
    """{quest id: [part, parts]} for chains of quests that share a title
    ("The Missing Diplomat" 1 to 17), from forever-db's prerequisites. Only
    straight chains: one with a fork has no single order, so it gets none."""
    nxt: dict[str, set] = {}
    prv: dict[str, set] = {}
    for line in (db / "data" / "quest_prereq.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        a, b = str(r["pre"]), str(r["quest"])
        if a != b and titles.get(a) and titles.get(a) == titles.get(b):
            nxt.setdefault(a, set()).add(b)
            prv.setdefault(b, set()).add(a)
    out: dict[str, list[int]] = {}
    seen: set[str] = set()
    for n in sorted(set(nxt) | set(prv), key=int):
        if n in seen:
            continue
        comp, todo = set(), [n]
        while todo:
            x = todo.pop()
            if x not in comp:
                comp.add(x)
                todo += [*nxt.get(x, ()), *prv.get(x, ())]
        seen |= comp
        if any(len(nxt.get(x, ())) > 1 or len(prv.get(x, ())) > 1 for x in comp):
            continue
        roots = [x for x in comp if not prv.get(x)]
        if len(roots) != 1:
            continue
        path = roots
        while nxt.get(path[-1]):
            path = [*path, next(iter(nxt[path[-1]]))]
        if len(path) == len(comp):
            out.update({x: [i + 1, len(path)] for i, x in enumerate(path)})
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def only_rewards(db: Path, gear: dict) -> dict[str, int]:
    """{item id: quest id} for the gear exactly one quest gives and nothing
    else does, so a character holding it handed that quest in (owner,
    2026-11-19: "it should see that I turned it in and got the reward").
    Kept: an item or choice reward of one quest only, with no creature or
    object that drops it (forever-db's item sources), no craft, drop or other
    quest in Polestar's gear data, and green or better (the gear data's quality).
    forever-db has no vendors and no binding; the quality floor keeps out
    what vendors sell (white goods, potions, bags). Anything another way
    gives could have reached the bags without the quest.

    The quests are forever-db's rewards and the gear data's quest sources
    together: forever-db has no reward rows for Forever's new quests (City
    of Dalaran's eight, 2026.11.1) while the gear data names them, so its
    rewards read as nobody's and two Dalaran quests handed in with the
    Recorder off stayed "open" with their reward worn."""
    reward_of: dict[int, set[int]] = {}
    for line in (db / "data" / "quest_reward.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("kind") in ("item", "choice"):
            reward_of.setdefault(int(r["id"]), set()).add(int(r["quest"]))
    for item, gs in (gear.get("sources") or {}).items():
        for s in gs:
            if s.get("kind") == "quest" and s.get("quest"):
                reward_of.setdefault(int(item), set()).add(int(s["quest"]))
    sourced = {int(json.loads(line)["item"])
               for line in (db / "data" / "item_source.jsonl").read_text(encoding="utf-8").splitlines()}
    out = {}
    for item, qs in reward_of.items():
        info = (gear.get("items") or {}).get(str(item)) or {}
        gs = (gear.get("sources") or {}).get(str(item)) or []
        if any(s.get("kind") != "quest" for s in gs):
            continue
        if len(qs) == 1 and item not in sourced and (info.get("quality") or 0) >= 2:
            out[str(item)] = next(iter(qs))
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def reward_quests(db: Path, quests: dict) -> dict[str, list]:
    """{quest id: [side, race mask, class mask, [items], [choices]]} for every
    quest with an item reward: where gear comes from for the items Polestar's
    gear data has no source for (wowstats.gear, owner 2026-11-24: "knowing
    where to get the item is the important"). Side 0 both, 1 Alliance, 2
    Horde; a mask of 0 is everyone."""
    who = {}
    for line in (db / "data" / "quest.jsonl").read_text(encoding="utf-8").splitlines():
        q = json.loads(line)
        who[str(q["id"])] = [q.get("side") or 0, q.get("races") or 0, q.get("classes") or 0]
    out: dict[str, list] = {}
    for line in (db / "data" / "quest_reward.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        q = str(r["quest"])
        if r.get("kind") not in ("item", "choice") or q not in quests or q not in who:
            continue
        row = out.setdefault(q, [*who[q], [], []])
        row[3 if r["kind"] == "item" else 4].append(int(r["id"]))
    return {k: [*v[:3], sorted(set(v[3])), sorted(set(v[4]))]
            for k, v in sorted(out.items(), key=lambda kv: int(kv[0]))}


def faction_sides(rows: list[dict]) -> dict[str, dict]:
    """{faction id: {name, side}} for every faction with a reputation bar
    (ReputationIndex not -1), its side from its parents (SIDE_ROOTS), no
    `side` for a neutral one (the Steamwheedle Cartel's towns, Timbermaw
    Hold). A reputation's quartermaster sells to its side only: ForeverDB.net
    gives a vendor a side but no reputation one, and had Theramore's
    quartermaster as the Horde's (wowstats.gear.foreverdb_sources)."""
    by_id = {r["ID"]: r for r in rows}
    out: dict[str, dict] = {}
    for fid, r in by_id.items():
        if i(r["ReputationIndex"]) < 0 or not r.get("Name_lang"):
            continue
        side, at, seen = None, fid, set()
        while at and at != "0" and at not in seen:
            seen.add(at)
            if at in SIDE_ROOTS and at != fid:
                side = SIDE_ROOTS[at]
                break
            at = (by_id.get(at) or {}).get("ParentFactionID")
        out[fid] = {"name": r["Name_lang"], **({"side": side} if side else {})}
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--polestar", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    db = a.polestar / "forever-db"
    index = json.loads((db / "index.json").read_text())
    # The client tables at the newest build we know, not forever-db's: Forever
    # adds zones and dungeons faster than forever-db is published, and
    # forever-db 2026.11.1 (client 1.60.1.70532) predates The Drowned City
    # (Map 2970), so its area had no name and its quests no zone.
    build = json.loads(WATCH.read_text(encoding="utf-8"))["client_build"]["known"]

    quests: dict[str, list] = {}
    used_areas: set[int] = set()
    for line in (db / "data" / "quest.jsonl").read_text(encoding="utf-8").splitlines():
        q = json.loads(line)
        if not q.get("title"):
            continue
        zone = q.get("zone_area") or 0
        if zone > 0:
            used_areas.add(zone)
        quests[str(q["id"])] = [q["title"], q.get("level") or 0, q.get("min_level") or 0, zone if zone > 0 else 0]

    parts = chain_parts(db, {k: v[0] for k, v in quests.items()})
    gear_quests = reward_quests(db, quests)

    # What a quest needs done first (forever-db, breadcrumbs left out: they
    # only point the way): {quest id: [[all of these], [any one of these]]}.
    pre: dict[str, list[list[int]]] = {}
    for line in (db / "data" / "quest_prereq.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("breadcrumb") or str(r["quest"]) not in quests:
            continue
        both = pre.setdefault(str(r["quest"]), [[], []])
        both[1 if r.get("mode") == "any" else 0].append(int(r["pre"]))
    pre = {k: [sorted(set(v[0])), sorted(set(v[1]))] for k, v in sorted(pre.items(), key=lambda kv: int(kv[0]))}

    items = {}
    for line in (db / "data" / "item.jsonl").read_text(encoding="utf-8").splitlines():
        it = json.loads(line)
        if it.get("name"):
            items[str(it["id"])] = it["name"]

    # The gear data's drop zones, so a drop says when it is a dungeon's.
    gear = json.loads((OUT.parent / "gear-reference.json").read_text())
    rewards = only_rewards(db, gear)
    for srcs in (gear.get("sources") or {}).values():
        for s in srcs:
            if s.get("kind") == "drop" and s.get("zone"):
                used_areas.add(int(s["zone"]))

    areas = table("AreaTable", build)
    maps = {r["ID"]: r for r in table("Map", build)}
    by_area = {r["AreaTableID"]: r for r in maps.values() if INSTANCE.get(r["InstanceType"]) and i(r["AreaTableID"])}
    gfa = table("GroupFinderActivity", build)
    # Each instance's boss encounters by name: a quest that kills one is that
    # dungeon's (tools/dungeon_quests/export.py `boss`), wherever forever-db
    # zones it (The Saltseer Captain: no zone, its giver in Booty Bay).
    bosses: dict[str, set[str]] = {}
    for r in table("DungeonEncounter", build):
        if r.get("Name_lang"):
            bosses.setdefault(r["MapID"], set()).add(r["Name_lang"])
    factions = faction_sides(table("Faction", build))
    overrides = {r["key"]: r for r in json.loads((a.polestar / "data" / "codex" / "levels.json").read_text())["levels"]}
    recorded = json.loads((a.polestar / "data" / "codex" / "recorded.json").read_text()).get("dungeons") or {}

    def levels(map_id: str, kind: str) -> list[int] | None:
        rows = [r for r in gfa if r["MapID"] == map_id and i(r["MinLevel"])]
        if kind == "dungeon":
            rows = [r for r in rows if r["DifficultyID"] == "1"] or rows
        if not rows:
            return None
        r = min(rows, key=lambda r: (i(r["MinLevel"]), i(r["ID"])))
        return [i(r["MinLevel"]), max(i(r["MinLevel"]), i(r["MaxLevelSuggestion"]))]

    # Every zone of the world map and every dungeon's and raid's own area,
    # named by a quest yet or not: a zone Forever adds (Riverglades) or a
    # dungeon (The Drowned City) resolves the day a source names it. A zone
    # is a continent's top area that the world map (UiMap, zone type) shows;
    # the table's leftovers (UNUSED, GM Island) stay out. An instance's own
    # area is Map.AreaTableID, else the area on its map that carries the
    # map's name (Ruins of Lordaeron), for an instance the Group Finder
    # lists (a level range): the event maps (an instanced Deadwind Pass)
    # stay out, so a zone's name never resolves to one of them.
    ui = table("UiMap", build)
    zone_names = {r["Name_lang"] for r in ui if r["Type"] == "3"}
    # An instance a quest or a drop already names keeps that area (Sunken
    # Temple is 1417, not its Map's 1477, The Temple of Atal'Hakkar).
    named = {r["ContinentID"] for r in areas if i(r["ID"]) in used_areas}
    named |= {by_area[r["ID"]]["ID"] for r in areas if i(r["ID"]) in used_areas and r["ID"] in by_area}
    own_area = {}
    for mid, m in maps.items():
        if (not INSTANCE.get(m["InstanceType"]) or mid in named
                or not levels(mid, INSTANCE[m["InstanceType"]])):
            continue
        if i(m["AreaTableID"]):
            own_area[m["AreaTableID"]] = mid
            continue
        same = sorted((r for r in areas if r["ContinentID"] == mid and r["ParentAreaID"] == "0"
                       and r["AreaName_lang"] == m["MapName_lang"]), key=lambda r: i(r["ID"]))
        if same:
            own_area[same[0]["ID"]] = mid
    for r in areas:
        if r["ID"] in own_area or (r["ParentAreaID"] == "0" and r["ContinentID"] in ("0", "1")
                                   and r["AreaName_lang"] in zone_names):
            used_areas.add(i(r["ID"]))

    out_areas: dict[str, dict] = {}
    for r in areas:
        aid = i(r["ID"])
        m = maps.get(own_area.get(r["ID"]) or r["ContinentID"]) or {}
        if not INSTANCE.get(m.get("InstanceType", "")):
            # An instance whose area sits on the continent's map (Blackrock
            # Spire): the instance map names it as its own area.
            m = by_area.get(r["ID"]) or m
        kind = INSTANCE.get(m.get("InstanceType", ""))
        map_id = m.get("ID", r["ContinentID"])
        # Only the areas a quest or a drop names: the server shows nothing else.
        if aid not in used_areas:
            continue
        name = r.get("AreaName_lang") or ""
        if not name:
            continue
        a_out: dict = {"name": name}
        if kind:
            a_out["instance"] = kind
            # The instance's Map id: Polestar's entrances and keys are keyed on it
            # (route-reference.json `places.entrances`).
            a_out["map"] = i(map_id)
            lv = levels(map_id, kind)
            o = overrides.get(slug(m.get("MapName_lang") or name)) or {}
            if lv and o.get("max"):
                lv = [lv[0], min(lv[1], int(o["max"]))]
            if bosses.get(str(map_id)):
                a_out["bosses"] = sorted(bosses[str(map_id)])
            if lv:
                a_out["levels"] = lv
                if kind == "dungeon":
                    a_out["floor"] = floor_of(lv, recorded.get(str(map_id)), o)
        out_areas[str(aid)] = a_out
    unnamed = sorted({str(v[3]) for v in quests.values() if v[3]} - set(out_areas), key=int)
    if unnamed:
        raise SystemExit(f"forever-db zones quests in areas client {build} doesn't name: {', '.join(unnamed)}")

    # uiMap id -> zone name: where ForeverDB.net places a quest's giver, for
    # a quest forever-db doesn't have yet (wowstats.forever.ui_map).
    ui_maps = {r["ID"]: r["Name_lang"] for r in ui if r.get("Name_lang")}

    doc = {
        "schema": 1,
        "source": {
            "forever_db": {"version": index.get("version"), "client_build": index.get("clientBuild"),
                           "producer_commit": index.get("producerCommit"), "licence": "GPL-3.0"},
            "client_tables": {"via": "wago.tools", "product": "wowf", "build": build,
                              "tables": ["AreaTable", "Map", "UiMap", "GroupFinderActivity", "DungeonEncounter",
                                         "Faction"],
                              "note": "Game data (c) Blizzard Entertainment, used as facts"},
            "levels_exceptions": "Polestar data/codex/levels.json (max, floor)",
            "floors": "Polestar data/codex/recorded.json (players' 25th percentile entry level)",
        },
        # quest id -> [title, level, minimum level, zone area id (0: none)]
        "quests": dict(sorted(quests.items(), key=lambda kv: int(kv[0]))),
        # quest id -> [part, parts] of a chain of quests with one title
        "parts": parts,
        # quest id -> [[every one of these first], [any one of these first]]
        "pre": pre,
        # item id -> name
        "items": dict(sorted(items.items(), key=lambda kv: int(kv[0]))),
        # item id -> the one quest that gives it (only_rewards)
        "rewards": rewards,
        # quest id -> [side, race mask, class mask, [item rewards], [choice rewards]] (reward_quests)
        "gear_quests": gear_quests,
        # area id -> {name, instance: dungeon|raid, map: instance Map id, bosses: [encounter names],
        # levels: [min, max], floor (dungeons): where Polestar's "For your level" starts}
        "areas": dict(sorted(out_areas.items(), key=lambda kv: int(kv[0]))),
        # faction id -> {name, side: Alliance|Horde (none: neutral)} (faction_sides)
        "factions": factions,
        # uiMap id -> its name (ForeverDB.net's quest givers' maps)
        "ui_maps": dict(sorted(ui_maps.items(), key=lambda kv: int(kv[0]))),
    }
    a.out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n",
                     encoding="utf-8")
    print(f"{a.out}: {len(quests)} quests ({len(parts)} in chains), {len(items)} items "
          f"({len(rewards)} only from one quest), {len(out_areas)} areas "
          f"({sum(1 for x in out_areas.values() if 'instance' in x)} in instances), {len(factions)} factions, "
          f"build {build}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
