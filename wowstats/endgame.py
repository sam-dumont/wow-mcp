"""What endgame content Forever has opened: the meta's endgame section
(data/kb/endgame.json), read by the gear advice.

Owner, 2026-11-25: "we're still very early: no raid yet, no high level
content still, so it should be explicit for end game, not 'we don't know so
we count as opened'". Classic's data lists every raid, world boss and event,
so endgame is closed by default:

- What is endgame comes from the data, not item by item: a drop in a raid
  (forever-db's areas with `instance: raid`, ForeverDB.net's raid
  instances), a quest done in a raid or following one (forever-db's quest
  zones and prerequisites), a world boss (a level-63 boss outside any
  instance, forever-db's creatures), and whatever an entry of the section
  matches (the Dungeon Set 2 chain from its first quest, a world event's
  chest, a raid drop's hand-in quest).
- An endgame source counts only when (a) an entry that matches it is `open`,
  or its `opens` day has come, or (b) ForeverDB.net saw it in Forever
  (label `s`). Any matching entry still shut wins over an open one (Lord
  Valthalak is in Blackrock Spire, which is open; his chain is not); an
  entry that names the source wins over one whose `rule` covers it (Ivus,
  a level-63 boss summoned in Alterac Valley, is no world boss).
  Endgame content no entry names is shut: nothing says it opened.
- Everything else (levelling dungeons, quests, vendors, crafts) is not
  endgame and counts as before.

The weekly meta pass (tools/kb/meta.py) proposes status changes from the
news; tools/kb/lifecycle.py folds them into the file. An announced day
reopens content by itself on that day, with no code change.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict

from wowstats import forever

KINDS = ("raid", "dungeon", "chain", "world-boss", "event")
STATUSES = ("open", "announced", "not-open")
WORLD_BOSS_LEVEL = "63"  # a skull-level boss: raid and world bosses, never a 5-man's

_lock = threading.Lock()
_cache: dict = {}


def section() -> dict:
    """The section's entries by key ({} when the file is missing), read once."""
    with _lock:
        if "doc" not in _cache:
            from wowstats.analysis import kb

            d = kb.data_file("kb")
            try:
                doc = json.loads((d / "endgame.json").read_text(encoding="utf-8")) if d else {}
            except (OSError, ValueError):
                doc = {}
            _cache["doc"] = doc if isinstance(doc, dict) else {}
        return _cache["doc"].get("entries") or {}


def reset() -> None:
    """Forget what was read (tests that swap the file or the data)."""
    with _lock:
        _cache.clear()


def is_open(entry: dict, today: str) -> bool:
    if entry.get("status") == "open":
        return True
    opens = entry.get("opens")
    return bool(opens) and entry.get("status") == "announced" and today >= opens


def describe(entry: dict) -> str:
    """"Molten Core (raid, not open)", "Onyxia's Lair (raid, opens 2026-12-09)"."""
    st = entry.get("status")
    when = (f"opens {entry['opens']}" if entry.get("opens") else "announced, no date") if st == "announced" \
        else "open" if st == "open" else "not open"
    return f"{entry.get('name')} ({entry.get('kind')}, {when})"


# ---------------------------------------------------------------- the data's structure


def _children() -> dict[str, set[str]]:
    if "kids" not in _cache:
        kids: dict[str, set[str]] = defaultdict(set)
        for q, row in (forever.data().get("pre") or {}).items():
            for p in [*(row[0] or []), *(row[1] or [])] if isinstance(row, list) and len(row) == 2 else []:
                kids[str(p)].add(str(q))
        _cache["kids"] = kids
    return _cache["kids"]


def follow_ups(roots) -> set[int]:
    """The quests and every quest that follows them (forever-db's prerequisites)."""
    kids = _children()
    seen = {str(r) for r in roots}
    todo = list(seen)
    while todo:
        for c in kids.get(todo.pop(), ()):
            if c not in seen:
                seen.add(c)
                todo.append(c)
    return {int(q) for q in seen if q.isdigit()}


def _zone_quests() -> dict[str, list[str]]:
    """{zone name: quests forever-db zones there}."""
    if "zq" not in _cache:
        by: dict[str, list[str]] = defaultdict(list)
        for q, row in (forever.data().get("quests") or {}).items():
            a = forever.area(row[3]) if isinstance(row, list) and len(row) > 3 else None
            if a and a.get("name"):
                by[a["name"]].append(q)
        _cache["zq"] = by
    return _cache["zq"]


def place_quests(place: str) -> set[int]:
    """Quests done in a place and the ones that follow them."""
    key = ("pq", place)
    if key not in _cache:
        _cache[key] = follow_ups(_zone_quests().get(place) or [])
    return _cache[key]


def raid_places() -> set[str]:
    """Every raid the data knows: forever-db's areas and ForeverDB.net's instances."""
    if "raids" not in _cache:
        # Each raid map's own area: the areas list every room of a raid too,
        # and the client's Emerald Dream and Naxxramas maps carry areas named
        # after world zones (Feralas, Eastern Plaguelands), which aren't raids.
        names = {a["name"] for a in forever.instance_areas("raid").values() if a.get("name")}
        from wowstats import gear

        fdb = gear.sources_data() or {}
        names |= {i["name"] for i in (fdb.get("instances") or {}).values() if i.get("kind") == "raid" and i.get("name")}
        _cache["raids"] = names
    return _cache["raids"]


def _raid_maps() -> set[int]:
    if "raid_maps" not in _cache:
        from wowstats import gear

        fdb = gear.sources_data() or {}
        maps = {int(k) for k, i in (fdb.get("instances") or {}).items() if i.get("kind") == "raid" and k.isdigit()}
        maps |= {int(a["map"]) for a in (forever.data().get("areas") or {}).values()
                 if a.get("instance") == "raid" and a.get("map") is not None}
        _cache["raid_maps"] = maps
    return _cache["raid_maps"]


def _place_of(s: dict, quest_zone=None) -> str | None:
    if s.get("kind") == "quest":
        return quest_zone
    a = forever.area(s.get("zone")) if s.get("zone") else None
    return (a or {}).get("name") or s.get("zone_name")


def in_instance(s: dict) -> bool:
    if s.get("instance_map") is not None:
        return True
    a = forever.area(s.get("zone")) if s.get("zone") else None
    return bool(a and a.get("instance"))


def world_boss(s: dict) -> bool:
    """A drop from a level-63 boss outside any instance (Azuregos, Kazzak,
    the Emerald Dragons, the Silithus lords)."""
    if s.get("kind") != "drop" or not s.get("npc") or in_instance(s):
        return False
    r = forever.npc_rank(s["npc"])
    return bool(r) and r[1] == "boss" and r[0] == WORLD_BOSS_LEVEL


def raid(s: dict, quest_zone=None) -> str | None:
    """The raid a source belongs to by the data's structure, None when none."""
    if s.get("kind") == "drop":
        place = _place_of(s)
        if s.get("raid") or (s.get("instance_map") is not None and int(s["instance_map"]) in _raid_maps()) \
                or place in raid_places():
            return place or "a raid"
        return None
    if s.get("kind") == "quest" and s.get("quest") is not None:
        q = int(s["quest"])
        for place in sorted(raid_places()):
            if q in place_quests(place):
                return place
    return None


def matches(entry: dict, s: dict, quest_zone=None) -> bool:
    """Whether an entry names the source: by its chest, creature, faction,
    quest (and the quests after it) or place, or by its `rule` (world_boss:
    every level-63 boss outside an instance)."""
    m = entry.get("match") or {}
    k = s.get("kind")
    if m.get("rule") == "world_boss":
        return world_boss(s)
    if k in ("container", "object") and s.get("name") in (m.get("objects") or ()):
        return True
    if k == "drop" and s.get("npc") is not None and int(s["npc"]) in set(m.get("npcs") or ()):
        return True
    if k == "reputation" and s.get("faction") is not None and int(s["faction"]) in set(m.get("factions") or ()):
        return True
    if k == "quest" and s.get("quest") is not None:
        q = int(s["quest"])
        if m.get("quests") and q in _follow_cached(tuple(m["quests"])):
            return True
        return any(q in place_quests(p) for p in m.get("places") or ())
    if k == "drop":
        return _place_of(s) in set(m.get("places") or ())
    return False


def _follow_cached(roots: tuple) -> set[int]:
    key = ("fu", roots)
    if key not in _cache:
        _cache[key] = follow_ups(roots)
    return _cache[key]


def closed(s: dict, today: str, quest_zone=None) -> str | None:
    """Why a raw gear source is shut (an entry's description), None when it
    counts: not endgame, an open entry, or seen in Forever (label `s`)."""
    if s.get("label") == "s":
        return None
    entries = section().values()
    # An entry that names the source wins over one that only covers it by
    # a rule (Ivus in Alterac Valley, open, is a level-63 boss too).
    hit = [e for e in entries if not (e.get("match") or {}).get("rule") and matches(e, s, quest_zone)] \
        or [e for e in entries if (e.get("match") or {}).get("rule") and matches(e, s, quest_zone)]
    shut = [e for e in hit if not is_open(e, today)]
    if shut:
        return describe(shut[0])
    if hit:
        return None
    r = raid(s, quest_zone)
    if r:
        return f"{r} (raid, not in the endgame section: nothing says it is open)"
    if world_boss(s):
        return "a world boss (not in the endgame section: nothing says it is open)"
    return None


SLUG = re.compile(r"[^a-z0-9]+")


def key_of(name: str) -> str:
    return SLUG.sub("-", name.lower().replace("'", "")).strip("-")[:60]
