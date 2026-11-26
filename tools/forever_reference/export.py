"""Writes data/forever-reference.json: Forever's quest names and its
instances, so the server names every quest and dungeon it shows.

Two upstreams, both facts about the game, each at the version recorded in
the file's `source` (tools/forever_reference/upstream.py, pins below):

- forever-db, Polestar's open quest database (GPL-3.0), at its own newest
  release (`data-v*` on GitHub, a zip checked against its SHA256SUMS), not
  the copy inside Polestar's addon release: forever-db is published ahead
  of the addon (2026.11.2 had The Drowned City's and Riverglades' quests
  while Polestar v0.4.2.2 still shipped 2026.11.1). Each quest's title,
  level, minimum level and zone, its part in a chain of quests with the
  same title (from the prerequisites), the quests it needs done first
  (breadcrumbs left out), each item's name (Forever's ItemSparse holds only
  the items Forever changed), and the gear only one quest gives
  (wowstats.quests: holding it means the quest was handed in).
- Forever's client tables on wago.tools at the newest client build, read
  whole: AreaTable (every area's name), Map (every map: which are dungeons
  or raids, each instance's own area), UiMap (the map ids ForeverDB.net
  places quest givers on), DungeonEncounter (each instance's bosses),
  Faction (each reputation's side: a faction under Alliance or Alliance
  Forces is the Alliance's, under Horde or Horde Forces the Horde's, the
  rest neutral) and GroupFinderActivity (a dungeon's level range, as the
  game's Group Finder shows it, the rule Polestar's Codex uses), with the
  Codex's named exceptions (data/codex/levels.json, `max`).
- Polestar's floor for each dungeon (its Codex rule, journal.rs levels_of):
  the range's bottom or players' recorded 25th percentile entry level
  (data/codex/recorded.json, 5 people with a boss kill or more), whichever
  is higher, or the named exception's `floor`. Polestar's Dungeons hub lists
  a dungeon "For your level" from its floor to its top; wowstats.plan.vsguide
  sets the advice against that list.

Both at their pins, recorded in the file's `source`: forever-db at
tools/forever-db-tag, the client build at data/kb/watch.json
`client_build.known` (the watch's client-build kind moves it daily and
reruns this export). Stdlib only, the same file byte for byte for the same
pins:

    uv run python tools/forever_reference/export.py --polestar <checkout>            # at the pins
    uv run python tools/forever_reference/export.py --polestar <checkout> --latest   # forever-db's newest first

`--latest` moves the forever-db pin to its newest release, then exports.
The Polestar checkout (tools/polestar-tag) gives only the Codex's level
exceptions and floors, and its forever-db when it is the pinned release (no
download then). `--forever-db-cache` keeps the unpacked forever-db for the
exports that read it next (tools/dungeon_quests/export.py).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import upstream  # noqa: E402

WAGO = "https://wago.tools/db2/{table}/csv?build={build}"
OUT = Path(__file__).resolve().parents[2] / "data" / "forever-reference.json"
INSTANCE = {"1": "dungeon", "2": "raid"}
# Map.InstanceType, for the maps table.
MAP_TYPE = {"0": "world", "1": "dungeon", "2": "raid", "3": "battleground", "4": "arena", "5": "scenario"}
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


def same_name(a: str | None, b: str | None) -> bool:
    """Deadmines and The Deadmines alike."""
    def n(x):
        return re.sub(r"^the ", "", (x or "").lower().strip())
    return bool(a and b) and n(a) == n(b)


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


def reward_quests(db: Path, who: dict) -> dict[str, list]:
    """{quest id: [side, race mask, class mask, [items], [choices]]} for every
    quest with an item reward: where gear comes from for the items Polestar's
    gear data has no source for (wowstats.gear, owner 2026-11-24: "knowing
    where to get the item is the important"). Side 0 both, 1 Alliance, 2
    Horde; a mask of 0 is everyone."""
    out: dict[str, list] = {}
    for line in (db / "data" / "quest_reward.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        q = str(r["quest"])
        if r.get("kind") not in ("item", "choice") or q not in who:
            continue
        row = out.setdefault(q, [*who[q], [], []])
        row[3 if r["kind"] == "item" else 4].append(int(r["id"]))
    return {k: [*v[:3], sorted(set(v[3])), sorted(set(v[4]))]
            for k, v in sorted(out.items(), key=lambda kv: int(kv[0]))}


# forever-db's quest types the gear advice weighs (Classic's QuestInfo: 1
# Group, 41 PvP, 62 Raid, 81 Dungeon); 0 and the rest are plain quests.
WORK_TYPES = {1, 41, 62, 81}


def quest_work(db: Path, wanted: set[str], sourced_npcs: set[int],
               ui_names: dict[str, str] | None = None) -> tuple[dict, dict]:
    """({quest id: [type, foe, bring, objectives]}, {npc id: [name, level min, level max,
    rank]}) for the quests in `wanted` (the ones that give gear): what a
    quest really takes, for the gear advice (wowstats.questwork, owner
    2026-11-25: "a few recos here are easier said than done"). `type` is
    forever-db's quest type (WORK_TYPES, else 0); `foe` the strongest
    creature its objectives send the player to (a kill target, or the one
    creature that drops a collected item), by rank then level, as an npc id;
    `bring` the items it asks for that no creature drops and no object
    gives (made, bought, or carried from another quest); `objectives` how
    many it lists (0: a hand-in, or a gate the quest data doesn't say, like
    Library Books' milestones). The npcs map names
    every foe, and every creature ForeverDB.net names as a drop source
    (`sourced_npcs`): its level and rank (0 normal, 1 elite, 2 rare elite, 3
    boss, 4 rare) and, for a drop source, the zone it lives in: the one
    zone map (forever-db's ui_map frames, named by the client's UiMap)
    holding every spawn, else null (frames overlap their neighbours: a
    creature only places when one frame alone holds all its spawns)."""
    def rows(name):
        return [json.loads(line) for line in (db / "data" / name).read_text(encoding="utf-8").splitlines()]

    npc = {r["id"]: r for r in rows("npc.jsonl")}
    qtype = {str(r["id"]): r.get("type") or 0 for r in rows("quest.jsonl")}
    drops: dict[int, set[int]] = {}
    looted: set[int] = set()  # items an object gives (a chest, a bundle on the ground)
    for r in rows("item_source.jsonl"):
        if r.get("kind") == "npc":
            drops.setdefault(int(r["item"]), set()).add(int(r["id"]))
        elif r.get("kind") == "object":
            looted.add(int(r["item"]))
    objs: dict[str, list] = {}
    for r in rows("quest_objective.jsonl"):
        objs.setdefault(str(r["quest"]), []).append(r)

    def strength(n: int) -> tuple:
        x = npc.get(n) or {}
        rank = x.get("rank") or 0
        return (rank in (1, 2, 3), x.get("level_max") or 0, -n)

    out: dict[str, list] = {}
    used: set[int] = set()
    for q in sorted(wanted, key=int):
        foes, bring = set(), []
        for o in objs.get(q, []):
            t = o.get("target")
            if o.get("kind") == "kill" and t in npc:
                foes.add(int(t))
            elif o.get("kind") == "item" and t is not None:
                by = {n for n in drops.get(int(t), ()) if n in npc}
                if len(by) == 1:
                    foes |= by
                elif not drops.get(int(t)) and int(t) not in looted and int(t) not in bring:
                    bring.append(int(t))
        foe = max(foes, key=strength) if foes else None
        kind = qtype.get(q, 0) if qtype.get(q, 0) in WORK_TYPES else 0
        n = len(objs.get(q, []))
        if not (kind or foe or bring) and n:
            continue
        out[q] = [kind, foe, bring, n]
        if foe:
            used.add(foe)
    frames = [f for f in rows("ui_map.jsonl") if (ui_names or {}).get(str(f["ui_map"]))]
    spawns: dict[int, list] = {}
    for r in rows("spawn.jsonl"):
        if r.get("kind") == "npc" and r.get("id") in sourced_npcs:
            spawns.setdefault(r["id"], []).append(r)

    def zone_of(n: int) -> str | None:
        at = None
        for sp in spawns.get(n, []):
            hits = {f["ui_map"] for f in frames if f["map"] == sp["map"] and f["bottom"] <= sp["x"] <= f["top"]
                    and f["right"] <= sp["y"] <= f["left"]}
            at = hits if at is None else at & hits
        return ui_names[str(at.pop())] if at and len(at) == 1 else None

    npcs = {}
    for n in sorted(used | {x for x in sourced_npcs if x in npc}):
        x = npc[n]
        if x.get("name"):
            npcs[str(n)] = [x["name"], x.get("level_min") or 0, x.get("level_max") or 0, x.get("rank") or 0,
                            zone_of(n) if n in sourced_npcs else None]
    return out, npcs


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


def dungeon_drops(drops: dict, item_sources: Path, rosters: dict, maps: dict,
                  gear_items: set[str], missing: set[str], rewards: dict) -> dict:
    """Use NPC ids placed in a single dungeon. Prefer trusted recorded boss loot,
    then a designed forever-db drop for gear the other sources cannot place."""
    creatures, bosses = {}, set()
    for name, dungeon in rosters["dungeons"].items():
        candidates = [m for mid, m in maps.items() if m["type"] == "dungeon" and m.get("area") and
                      (int(mid) == dungeon.get("map") or (not dungeon.get("map") and same_name(name, m["name"])))]
        columns = rosters["mob_columns"].get(dungeon.get("basis"))
        if len(candidates) != 1 or not columns:
            continue
        for part in (dungeon, *(dungeon.get("wings") or {}).values()):
            for mob in part.get("mobs") or []:
                creatures.setdefault(str(mob[0]), set()).add((mob[1], candidates[0]["area"]))
                if mob[columns.index("boss")]:
                    bosses.add(str(mob[0]))
    result = {}
    placed = {npc: next(iter(locations)) for npc, locations in creatures.items() if len(locations) == 1}
    for npc in bosses & placed.keys():
        name, area = placed[npc]
        for item, seen in drops.get(npc, {}).get("items", {}).items():
            if item not in gear_items or seen.get("trusted") is not True:
                continue
            # A second way of getting an item invalidates possession as proof of a quest hand-in.
            rewards.pop(item, None)
            if item in missing:
                result.setdefault(item, []).append({"kind": "drop", "npc": int(npc), "npc_name": name, "zone": area,
                                                    "pct": seen.get("chance"), "voters": seen["voters"],
                                                    "via": "polestar-recording"})
    unplaced = missing - result.keys()
    for line in item_sources.read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        item, npc = str(s["item"]), str(s["id"])
        if item in unplaced and s["kind"] == "npc" and npc in placed:
            name, area = placed[npc]
            result.setdefault(item, []).append({"kind": "drop", "npc": int(npc), "npc_name": name, "zone": area,
                                                "pct": s.get("chance"), "via": "forever-db"})
    return {k: sorted(v, key=lambda d: d["npc"]) for k, v in sorted(result.items(), key=lambda i: int(i[0]))}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--polestar", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--latest", action="store_true",
                    help="forever-db's newest release, written to tools/forever-db-tag first")
    ap.add_argument("--forever-db-cache", type=Path, help="where to keep the unpacked forever-db (default: a temp dir)")
    a = ap.parse_args(argv)
    if a.latest:
        upstream.pin_forever_db(upstream.latest_forever_db())
    v = upstream.pinned()
    cache = a.forever_db_cache or Path(tempfile.mkdtemp(prefix="forever-db-"))
    db = upstream.forever_db(v["forever_db"], cache, a.polestar)
    index = json.loads((db / "index.json").read_text())
    # The client tables at the newest build, not forever-db's: Forever adds
    # zones and dungeons faster than forever-db is published, and forever-db
    # 2026.11.1 (client 1.60.1.70532) predates The Drowned City (Map 2970).
    build = v["client_build"]

    quests: dict[str, list] = {}
    quest_gates = {}
    for line in (db / "data" / "quest.jsonl").read_text(encoding="utf-8").splitlines():
        q = json.loads(line)
        if not q.get("title"):
            continue
        quest_gates[str(q["id"])] = [q.get("side") or 0, q.get("races") or 0, q.get("classes") or 0]
        zone = q.get("zone_area") or 0
        quests[str(q["id"])] = [q["title"], q.get("level") or 0, q.get("min_level") or 0, zone if zone > 0 else 0]

    parts = chain_parts(db, {k: v[0] for k, v in quests.items()})
    gear_quests = reward_quests(db, quest_gates)

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
    # The quests that give gear (the gear data's, forever-db's rewards and
    # ForeverDB.net's quest sources) and ForeverDB.net's creature sources:
    # what quest_work describes.
    fdb = json.loads((OUT.parent / "foreverdb" / "item-sources.json").read_text())
    wanted = set(gear_quests) | set(gear.get("quests") or {})
    sourced_npcs = set()
    for e in (fdb.get("items") or {}).values():
        for x in e.get("sources") or []:
            if x.get("kind") == "quest" and x.get("quest"):
                wanted.add(str(x["quest"]))
            elif x.get("kind") in ("creature", "rare", "drop") and x.get("npc"):
                sourced_npcs.add(int(x["npc"]))
    ui = table("UiMap", build)
    # Zone maps only (UiMap type 3): a continent's frame holds every zone.
    ui_names = {r["ID"]: r["Name_lang"] for r in ui if r.get("Name_lang") and r["Type"] == "3"}
    work, npcs = quest_work(db, {q for q in wanted if q in quests}, sourced_npcs, ui_names)

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
    observations = json.loads((a.polestar / "data" / "codex" / "recorded.json").read_text())
    recorded = observations.get("dungeons") or {}

    def levels(map_id: str, kind: str) -> list[int] | None:
        rows = [r for r in gfa if r["MapID"] == map_id and i(r["MinLevel"])]
        if kind == "dungeon":
            rows = [r for r in rows if r["DifficultyID"] == "1"] or rows
        if not rows:
            return None
        r = min(rows, key=lambda r: (i(r["MinLevel"]), i(r["ID"])))
        return [i(r["MinLevel"]), max(i(r["MinLevel"]), i(r["MaxLevelSuggestion"]))]

    # Every area and every map, named by a quest yet or not: a zone Forever
    # adds (Riverglades) or a dungeon (The Drowned City) resolves the day a
    # source names it. An area is a dungeon's or a raid's when its map is an
    # instance the Group Finder lists (a level range); the other instanced
    # maps (an event's copy of Stranglethorn Vale, Deadwind Pass) are in
    # `maps` with their type, their areas plain names. An instance's own
    # area (maps' `area`, forever.area_of_map) is the one that carries the
    # map's name: Map.AreaTableID (Blackrock Spire's 1583, on the continent),
    # else a top area of the map (Sunken Temple 1417, the zone forever-db's
    # quests use, not Map.AreaTableID's 1477, The Temple of Atal'Hakkar);
    # else Map.AreaTableID (The Stockade, Stormwind Stockade's). None else:
    # Nightmare Grove's lowest area is named Azshara, which isn't a raid.
    by_id = {r["ID"]: r for r in areas}
    on_map: dict[str, list[dict]] = {}
    for r in areas:
        on_map.setdefault(r["ContinentID"], []).append(r)
    out_maps: dict[str, dict] = {}
    for mid, m in maps.items():
        kind = INSTANCE.get(m["InstanceType"])
        top = sorted((r for r in on_map.get(mid, []) if r["ParentAreaID"] == "0" and r["AreaName_lang"]),
                     key=lambda r: i(r["ID"]))
        at = m["AreaTableID"] if i(m["AreaTableID"]) else None
        same = lambda r: same_name(r.get("AreaName_lang"), m["MapName_lang"])  # noqa: E731
        own = ((at if same(by_id.get(at) or {}) else None)
               or next((r["ID"] for r in top if same(r)), None)
               or at)
        mo = {"name": m["MapName_lang"], "type": MAP_TYPE.get(m["InstanceType"], m["InstanceType"])}
        if kind and own:
            mo["area"] = i(own)
        out_maps[mid] = mo

    out_areas: dict[str, dict] = {}
    for r in areas:
        aid = i(r["ID"])
        name = r.get("AreaName_lang") or ""
        if not name:
            continue
        m = maps.get(r["ContinentID"]) or {}
        if not INSTANCE.get(m.get("InstanceType", "")):
            # An instance whose area sits on the continent's map (Blackrock
            # Spire): the instance map names it as its own area.
            m = by_area.get(r["ID"]) or m
        kind = INSTANCE.get(m.get("InstanceType", ""))
        map_id = m.get("ID", r["ContinentID"])
        lv = levels(map_id, kind) if kind else None
        a_out: dict = {"name": name}
        if kind and lv:
            a_out["instance"] = kind
            # The instance's Map id: Polestar's entrances and keys are keyed on it
            # (route-reference.json `places.entrances`).
            a_out["map"] = i(map_id)
            o = overrides.get(slug(m.get("MapName_lang") or name)) or {}
            if o.get("max"):
                lv = [lv[0], min(lv[1], int(o["max"]))]
            if bosses.get(str(map_id)):
                a_out["bosses"] = sorted(bosses[str(map_id)])
            a_out["levels"] = lv
            if kind == "dungeon":
                a_out["floor"] = floor_of(lv, recorded.get(str(map_id)), o)
        out_areas[str(aid)] = a_out
    unnamed = sorted({str(v[3]) for v in quests.values() if v[3]} - set(out_areas), key=int)
    if unnamed:
        raise SystemExit(f"forever-db zones quests in areas client {build} doesn't name: {', '.join(unnamed)}")

    # uiMap id -> its name: where ForeverDB.net places a quest's giver, for a
    # quest forever-db doesn't have yet (wowstats.forever.ui_map).
    ui_maps = {r["ID"]: r["Name_lang"] for r in ui if r.get("Name_lang")}
    missing = sorted({str(s["zone"]) for ss in (gear.get("sources") or {}).values() for s in ss
                      if s.get("kind") == "drop" and s.get("zone")} - set(out_areas), key=int)
    if missing:
        raise SystemExit(f"the gear data's drop zones client {build} doesn't name: {', '.join(missing)}")

    gear_items = set(json.loads((OUT.parent / "game-items.json").read_text())["items"])
    known = {k for k, v in fdb["items"].items() if v.get("sources")} | set(gear["sources"])
    known.update(str(item) for q in gear_quests.values() for item in (*q[3], *q[4]))
    rosters = json.loads((OUT.parent / "dungeons" / "kill-xp.json").read_text())
    loot = dungeon_drops(observations["drops"], db / "data" / "item_source.jsonl", rosters, out_maps,
                         gear_items, gear_items - known, rewards)

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
            "dungeon_drops": "Polestar trusted boss recordings and forever-db item_source (table rates), "
                             "matched to NPCs in kill-xp.json",
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
        "dungeon_drops": loot,
        # quest id -> [side, race mask, class mask, [item rewards], [choice rewards]] (reward_quests)
        "gear_quests": gear_quests,
        # Quests without a reward row still gate gear found by other sources.
        "quest_gates": {k: quest_gates[k] for k in sorted(wanted & quest_gates.keys(), key=int)
                        if k not in gear_quests and any(quest_gates[k])},
        # quest id -> [type (1 group, 41 PvP, 62 raid, 81 dungeon, else 0), foe npc id, [items to bring],
        # objectives]
        # for the quests that give gear (quest_work)
        "quest_work": work,
        # npc id -> [name, level min, level max, rank (0 normal, 1 elite, 2 rare elite, 3 boss, 4 rare),
        # zone of a drop source or null]
        "npcs": npcs,
        # area id -> {name, instance: dungeon|raid, map: instance Map id, bosses: [encounter names],
        # levels: [min, max], floor (dungeons): where Polestar's "For your level" starts}: every area
        "areas": dict(sorted(out_areas.items(), key=lambda kv: int(kv[0]))),
        # map id -> {name, type: world|dungeon|raid|battleground|arena|scenario, area: an instance's own area}
        "maps": dict(sorted(out_maps.items(), key=lambda kv: int(kv[0]))),
        # faction id -> {name, side: Alliance|Horde (none: neutral)} (faction_sides)
        "factions": factions,
        # uiMap id -> its name (ForeverDB.net's quest givers' maps)
        "ui_maps": dict(sorted(ui_maps.items(), key=lambda kv: int(kv[0]))),
    }
    # indent 1 (one key per line, like data/dungeons/wings.json): git merges disjoint changes, where the
    # one-line file conflicted on every PR that touched it (2026-11-25).
    a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{a.out}: {len(quests)} quests ({len(parts)} in chains), {len(items)} items "
          f"({len(rewards)} only from one quest), {len(out_areas)} areas "
          f"({sum(1 for x in out_areas.values() if 'instance' in x)} in instances), {len(out_maps)} maps, "
          f"{len(factions)} factions, forever-db {index.get('version')}, build {build}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
