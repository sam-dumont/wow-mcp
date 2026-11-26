"""Forever's quest, item and creature names and its instances
(data/forever-reference.json, docs/forever-reference.md; creatures from the
knowledge base's game data), so every quest, item, creature and dungeon the
page shows has a name, whatever the shipper sent.

The shipper names quests from the data addons on the player's PC and the
Recorder's own `text` records of the same run; records shipped before it
learned to (or from a run that never saw the quest's text) carry only the
id, and the page used to print "quest 631". Here the server names them:
the record's own name, else forever-db's title, else the title of a
Recorder `text` record in the store (Views.quest_titles), else nothing,
logged, and the page says "Unknown quest (631)".
"""

from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent
BUNDLED = (_HERE / "data" / "forever-reference.json", _HERE.parent / "data" / "forever-reference.json")

_lock = threading.Lock()
_data: dict | None = None


def data() -> dict:
    global _data
    with _lock:
        if _data is None:
            _data = {"quests": {}, "items": {}, "areas": {}}
            for p in BUNDLED:
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("schema") == 1:
                    _data = d
                    break
        return _data


def quest(qid) -> dict | None:
    """{name, level, min_level, zone, part} for a quest id, or None. `part`
    is [n, of] in a chain of quests that share the title, else None."""
    try:
        row = (data().get("quests") or {}).get(str(int(qid)))
    except (TypeError, ValueError):
        return None
    if not row:
        return None
    title, level, min_level, area_id = row
    a = area(area_id)
    return {"name": title, "level": level or None, "min_level": min_level or None,
            "zone": a["name"] if a else None, "part": (data().get("parts") or {}).get(str(int(qid)))}


def quest_gates(qid) -> tuple[int, int, int]:
    """Side, race mask and class mask from the same Forever quest record."""
    d = data()
    q = str(qid)
    row = (d.get("gear_quests") or {}).get(q) or (d.get("quest_gates") or {}).get(q)
    return tuple(row[:3]) if row else (0, 0, 0)


def dungeon_area(aid) -> str | None:
    """The dungeon an area ID stands for, as its instance area ID: itself,
    or the same-named area the client zones the dungeon's quests in. Four
    dungeons have two IDs: Razorfen Kraul is 491 as an instance and 1717 as
    its quests' zone (Gnomeregan 721/133, Uldaman 1337/1517, Zul'Farrak
    1176/978), so matching on the instance ID alone found none of their
    quests."""
    areas = data().get("areas") or {}
    a = areas.get(str(aid)) if aid is not None else None
    if not a:
        return None
    if a.get("instance") == "dungeon":
        return str(aid)
    twin = area_id(a.get("name"))  # the instance, when the name is twice in the table
    return str(twin) if twin and (area(twin) or {}).get("instance") == "dungeon" else None


def quest_dungeon(qid) -> int | None:
    """The dungeon a quest belongs to (its instance AreaTable id), when
    forever-db zones it in one: the place its work is done, wherever it is
    picked up. Polestar's quest table zones a quest by its giver instead (A
    Green Sample: Stormwind City, for City of Dalaran). A quest forever-db
    zones nowhere, or doesn't have yet (Forever's newest content), takes
    its one dungeon from data/dungeons/quests.json (a boss it kills,
    Polestar's journal or its dungeon runs)."""
    try:
        row = (data().get("quests") or {}).get(str(int(qid)))
    except (TypeError, ValueError):
        return None
    if row and row[3]:
        aid = dungeon_area(row[3])
        return int(aid) if aid else None
    from wowstats import dungeonquests

    maps = dungeonquests.dungeons_of(qid)
    return area_of_map(maps[0]) if len(maps) == 1 else None


def quest_zone(qid, giver_map=None) -> str | None:
    """Where a quest is done, by name: forever-db's zone, else its dungeon
    (quest_dungeon), else the zone of the map its giver stands on (ForeverDB
    places every giver: `giver_map` is a UiMap id). None when no source
    places it."""
    q = quest(qid) or {}
    if q.get("zone"):
        return q["zone"]
    return (area(quest_dungeon(qid)) or {}).get("name") or ui_map(giver_map)


def item_name(item_id) -> str | None:
    """forever-db's name for an item: Forever's own ItemSparse names only the
    items Forever changed, Polestar's gear data only those with a source."""
    try:
        return (data().get("items") or {}).get(str(int(item_id)))
    except (TypeError, ValueError):
        return None


def area(area_id) -> dict | None:
    """{name, instance?, levels?} for an AreaTable id, or None."""
    try:
        return (data().get("areas") or {}).get(str(int(area_id))) if area_id else None
    except (TypeError, ValueError):
        return None


def ui_map(map_id) -> str | None:
    """A world map's name by its UiMap id (where ForeverDB.net places a quest
    giver: Riverglades is 2548), or None when the client table doesn't have it."""
    try:
        return (data().get("ui_maps") or {}).get(str(int(map_id))) if map_id else None
    except (TypeError, ValueError):
        return None


def faction(faction_id) -> dict | None:
    """{name, side?} for a reputation's Faction id (the client's table; no
    `side`: neutral), or None when the table doesn't have it."""
    try:
        return (data().get("factions") or {}).get(str(int(faction_id))) if faction_id else None
    except (TypeError, ValueError):
        return None


_by_map: dict[int, int] | None = None


def instance_areas(kind: str | None = None) -> dict[str, dict]:
    """Each dungeon's or raid's own area (one per instance map), by area id;
    `kind` "dungeon" or "raid" keeps those. Name lookups go through these:
    every room of an instance is an area too, and some carry a world zone's
    name (the Emerald Dream map's Feralas)."""
    d = data() or {}
    areas = d.get("areas") or {}
    own = {str(m["area"]) for m in (d.get("maps") or {}).values() if m.get("area")}
    return {k: a for k, a in areas.items() if a.get("instance") and (kind is None or a["instance"] == kind)
            and (not own or k in own)}


def area_of_map(map_id) -> int | None:
    """The AreaTable id of an instance by its Map id (ForeverDB names
    instances by Map id; the areas carry `map`). None when unknown."""
    global _by_map
    if _by_map is None:
        # The map's own area (the export's `maps`), else its first instance area.
        by: dict[int, int] = {int(m): int(x["area"]) for m, x in (data().get("maps") or {}).items() if x.get("area")}
        for k, a in sorted((data().get("areas") or {}).items(), key=lambda kv: (not kv[1].get("instance"), int(kv[0]))):
            if a.get("map") is not None:
                by.setdefault(int(a["map"]), int(k))
        _by_map = by
    try:
        return _by_map.get(int(map_id)) if map_id is not None else None
    except (TypeError, ValueError):
        return None


_by_name: dict[str, int] | None = None


def area_id(name: str | None) -> int | None:
    """The AreaTable id of a zone or dungeon by its name (what the records
    and the gear data carry), for a Wowhead link. A name that is twice in
    the table (Razorfen Kraul: the dungeon and its entrance) gives the
    instance. A dungeon wing ("Scarlet Monastery: Library") gives its
    dungeon when the wing itself isn't in the table. None when unknown."""
    global _by_name
    if not name:
        return None
    if _by_name is None:
        # An instance's own area first (Razorfen Kraul: the dungeon, not its
        # entrance), then a plain area (Stranglethorn Vale the zone, not an
        # event's instanced copy), lowest id first: the zone before a sub-area.
        areas = data().get("areas") or {}
        own = {int(x["area"]) for x in (data().get("maps") or {}).values()
               if x.get("area") and (areas.get(str(x["area"])) or {}).get("instance")}
        by: dict[str, int] = {}
        for k, a in sorted((data().get("areas") or {}).items(),
                           key=lambda kv: (int(kv[0]) not in own, bool(kv[1].get("instance")), int(kv[0]))):
            by.setdefault(a.get("name"), int(k))
        _by_name = by
    return _by_name.get(name) or (_by_name.get(name.split(":", 1)[0].strip()) if ":" in name else None)


_npcs: dict[int, str] | None = None


def npc_name(npc_id) -> str | None:
    """forever-db's name for a creature (the knowledge base's game data,
    data/kb-reference.json.gz, read once): the recorder logs a creature's
    id, and its name only when the unit was targeted or moused over."""
    global _npcs
    try:
        nid = int(npc_id)
    except (TypeError, ValueError):
        return None
    with _lock:
        if _npcs is None:
            from wowstats.analysis.kb import packaged

            docs, _ = packaged()
            _npcs = {int(d["id"][4:]): d["title"] for d in docs
                     if d.get("kind") == "npc" and d.get("title") and d.get("id", "")[4:].isdigit()}
    return _npcs.get(nid)


_ranks: dict[int, tuple[str, str]] | None = None
_RANK = re.compile(r"\bLevel ([\d?-]+), ([a-z ]+)\.")


def npc_rank(npc_id) -> tuple[str, str] | None:
    """(level, rank) of a creature as forever-db gives it ("63", "boss";
    "58-60", "elite"), from the same game data as npc_name; None when unknown.
    wowstats.endgame tells world bosses by it."""
    global _ranks
    try:
        nid = int(npc_id)
    except (TypeError, ValueError):
        return None
    with _lock:
        if _ranks is None:
            from wowstats.analysis.kb import packaged

            docs, _ = packaged()
            out = {}
            for d in docs:
                if d.get("kind") == "npc" and d.get("id", "")[4:].isdigit():
                    m = _RANK.search(d.get("body") or "")
                    if m:
                        out[int(d["id"][4:])] = (m.group(1), m.group(2))
            _ranks = out
    return _ranks.get(nid)


def log_unknown(what: str, ids) -> None:
    ids = sorted({i for i in ids if i is not None})
    if ids:
        print(f"wow-stats: {len(ids)} {what} without a name in any source: {', '.join(map(str, ids))}",
              file=sys.stderr, flush=True)


def name_quests(rows: list[dict], titles: dict[int, str] | None = None) -> list[dict]:
    """Fills `name`, `level`, `zone` and `part` of quest rows ({quest_id, name?}) in
    place: the row's own name first, then forever-db, then `titles` (the
    Recorder's own text records). Returns the rows."""
    unknown = []
    for r in rows:
        q = quest(r.get("quest_id"))
        if not r.get("name"):
            r["name"] = (q or {}).get("name") or (titles or {}).get(r.get("quest_id")) or None
        if q:
            r.setdefault("level", q["level"])
            if q["part"] and r.get("name") == q["name"]:
                r["part"] = q["part"]
            if not r.get("zone"):
                r["zone"] = q["zone"]
        if not r.get("name"):
            unknown.append(r.get("quest_id"))
    log_unknown("quests", unknown)
    return rows
