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
    Green Sample: Stormwind City, for City of Dalaran)."""
    try:
        row = (data().get("quests") or {}).get(str(int(qid)))
    except (TypeError, ValueError):
        return None
    aid = dungeon_area(row[3]) if row else None
    return int(aid) if aid else None


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


_by_map: dict[int, int] | None = None


def area_of_map(map_id) -> int | None:
    """The AreaTable id of an instance by its Map id (ForeverDB names
    instances by Map id; the areas carry `map`). None when unknown."""
    global _by_map
    if _by_map is None:
        by: dict[int, int] = {}
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
        by: dict[str, int] = {}
        for k, a in sorted((data().get("areas") or {}).items(), key=lambda kv: not kv[1].get("instance")):
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
