"""What /ingest keeps of other players' data (security review H1).

Other players' data leaves a player's PC only where a feature uses it. The
shipper builds its events that way since 0.7.0; this screens the events a
0.6.x shipper still sends until it updates, before anything is stored:

- `wow-combatlog`: the parsed fields only (CL_KEYS), never the raw line.
  Another player is ANOTHER_PLAYER; their name, realm and GUID are dropped.
  A raw line (a 0.6.x `_msg`) is parsed again with the shipper's own code.
- `wow-recorder`: the kind's known fields (KINDS) plus what the shipper
  derives (REC_DERIVED); `args` (fields past a kind's spec, or a kind the
  shipper didn't know) is dropped and `_msg` is cut to the known fields.
- `wow-player`: what the People tab, the journal and the MCP read
  (PLAYER_KEYS); `player_realm` and `map_id` are dropped.

- `wow-altoholic` (0.7.0+), and `wow-character` snapshots of source
  `altoholic`: their kind's fields (ALTO_KEYS) only. Altoholic's friends
  list, calendar, guild and mail tables are never read by the shipper; a
  shipper that sent any of it anyway has it dropped here.

Every other feed passes unchanged. The constants and functions below are
copies of the shipper's (shipper/wow_stats.py, a standalone stdlib script):
tests/test_otherplayers.py keeps them equal, so change both.
"""

from __future__ import annotations

import csv
import re
import unicodedata

# Recorder records: shipper/wow_stats.py KINDS, REC_DERIVED, EVENT_BASE, rec_keys, rec_line.
KINDS = {
    "session": ["char_id", "race", "class", "faction", "sex", "level", "locale", "build", "interface",
                "region", "ruleset", "recorder_version", "polestar_version", "packs"],
    "dropped": ["chunks_dropped"],
    "map": ["map", "map_type", "parent_map_id", "continent", "top", "left", "bottom", "right"],
    "mark": ["P", "text"],
    "where": ["where_kind", "where_id", "subzone", "indoors"],
    "poi": ["quest_id", "done", "P"],
    "imap": ["world_map_id", "inst_map_id", "group", "floor"],
    "open": ["spell_id", "P", "npc_kind", "npc_id"],
    "rep": ["faction_id", "reaction", "standing", "rep_gained"],
    "skill": ["skill_id", "rank", "max_rank"],
    "explore": ["area_id", "xp_reward", "P"],
    "craft": ["spell_id", "item_id", "count"],
    "learn": ["spell_id"],
    "achieve": ["achievement_id"],
    "step": ["guide", "step", "reason", "P"],
    "combat": ["on", "P"],
    "afk": ["on"],
    "revive": ["P", "how"],
    "objectives": ["quest_id", "objectives"],
    "escort": ["quest_id", "on", "P"],
    "progress": ["quest_id", "objective", "have", "need", "P"],
    "complete": ["quest_id"],
    "fail": ["quest_id", "P"],
    "offer": ["npc_kind", "npc_id", "P", "quests_available", "quests_active", "quests_log", "quests_done",
              "quests_not_offered"],
    "text": ["quest_id", "title", "objective", "text"],
    "detail": ["quest_id", "npc_kind", "npc_id", "P", "item_id"],
    "accept": ["quest_id", "npc_kind", "npc_id", "P", "level", "xp_reward", "money_copper", "items", "choices"],
    "turnin": ["quest_id", "npc_kind", "npc_id", "P", "level", "xp_reward", "money_copper", "item"],
    "abandon": ["quest_id"],
    "gossip": ["npc_kind", "npc_id", "P", "gossip_id", "text"],
    "unit": ["npc_id", "level", "classification", "reaction", "P", "seen_by"],
    "unitx": ["npc_id", "spawn_uid", "P"],
    "rare": ["npc_id", "level", "classification", "P", "seen_by"],
    "treasure": ["object_id", "P"],
    "kill": ["npc_id", "level", "classification", "P", "xp_reward", "rested_bonus", "tapped", "quests"],
    "spawn": ["npc_id", "spawn_uid", "P"],
    "engage": ["encounter_id", "difficulty", "size"],
    "encounter": ["encounter_id", "success", "difficulty", "size"],
    "gather": ["object_id", "P", "how", "skill_line", "skill_rank"],
    "loot": ["npc_kind", "npc_id", "P", "gather", "items", "wanted"],
    "enter": ["instance_id", "instance_kind", "difficulty", "P"],
    "pos": ["P", "flags", "speed"],
    "ride": ["on", "P"],
    "zone": ["P", "zone", "subzone"],
    "xp": ["xp", "xp_max", "rested_xp"],
    "level": ["level", "P", "played_s"],
    "death": ["P", "npc_id", "npc_level"],
    "flightmaster": ["npc_id", "P", "taxi_node", "known_nodes"],
    "taxi": ["from_node", "to_node"],
    "land": ["to_node", "seconds", "P"],
    "bind": ["npc_id", "P"],
    "trainer": ["npc_id", "P", "services"],
    "vendor": ["npc_id", "P", "items"],
    "cast": ["spell_id", "item_id", "P", "npc_kind", "npc_id"],
    "rest": ["rest_kind", "seconds"],
    "group": ["size", "raid"],
    "refuse": ["P", "npc_kind", "npc_id", "error"],
}
REC_DERIVED = frozenset({"map_id", "x", "y", "login", "quest_name", "duration_s", "xp_gained", "npc_name",
                         "item_names", "zone"})
EVENT_BASE = frozenset({"_time", "feed", "char", "kind", "source_id", "_msg", "rev", "demo"})


def rec_keys(kind: str) -> frozenset:
    """The fields an event of this Recorder kind may carry, besides EVENT_BASE."""
    spec = KINDS.get(kind)
    return frozenset(n for n in spec if n != "P") | REC_DERIVED if spec else frozenset()


def rec_line(kind: str, line: str) -> str:
    """A record line cut to the fields its kind is known to have: the kind,
    the time offset and KINDS' fields ("P" is three)."""
    spec = KINDS.get(kind) or []
    return "\t".join(line.split("\t")[:2 + sum(3 if n == "P" else 1 for n in spec)])


# Combat log lines: shipper/wow_stats.py CL_LINE, CL_GUID, ANOTHER_PLAYER, CL_KEYS, PLAYER_KEYS,
# cl_standard, cl_fields.
CL_LINE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{4}))? (\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?([+-]\d{1,4})?\s+([A-Z0-9_]+)(?:,(.*))?$")  # noqa: E501
CL_GUID = re.compile(r"^(?:[A-Za-z]+-\d|0000000000000000$)")
AFFIL_MINE = 0x1
ANOTHER_PLAYER = "another player"
CL_KEYS = frozenset({"event", "zone", "source_name", "dest_name"})
PLAYER_KEYS = frozenset({"player", "context", "zone", "level", "class", "race", "dungeon", "role", "source"})


def cl_standard(cols: list[str]) -> bool:
    """The standard source/dest prefix: GUID, name, flags, raid flags, twice
    (COMBATANT_INFO also opens with a player's GUID, then numbers)."""
    return len(cols) >= 8 and bool(CL_GUID.match(cols[0])) and cols[2][:2] == cols[6][:2] == "0x"


def cl_fields(event: str, cols: list[str], mine: set[str]) -> dict:
    """A combat log line's sent fields (CL_KEYS and `_msg`) from its event and
    columns. `mine`: our characters, in the character-folder form."""
    ev = {"event": event}
    if event == "ZONE_CHANGE" and len(cols) >= 2:
        ev["zone"] = cols[1]
    if cl_standard(cols):
        for key, guid, name, flags in (("source_name", *cols[0:3]), ("dest_name", *cols[4:7])):
            if not name or name == "nil":
                continue
            if guid.startswith("Player-"):
                try:
                    own = int(flags, 16) & AFFIL_MINE
                except ValueError:
                    own = 0
                folder = unicodedata.normalize("NFC", name.partition("-")[0].strip()).replace(" ", "-")
                if not own and folder not in mine:
                    name = ANOTHER_PLAYER
            ev[key] = name
    who = " > ".join(ev[k] for k in ("source_name", "dest_name") if k in ev)
    ev["_msg"] = " ".join(x for x in (event, ev.get("zone"), who) if x)
    return ev


# Altoholic's modules: shipper/wow_stats.py ALTO_STATS, ALTO_KEYS.
ALTO_STATS = ("health_max", "power_type", "power_max", "strength", "agility", "stamina", "intellect", "armor",
              "melee_dmg_min", "melee_dmg_max", "melee_speed", "melee_ap", "melee_hit_rating", "melee_crit_pct",
              "expertise", "ranged_dmg_min", "ranged_dmg_max", "ranged_speed", "ranged_ap", "ranged_hit_rating",
              "ranged_crit_pct", "spell_bonus_dmg", "spell_bonus_heal", "spell_hit_rating", "spell_crit_pct",
              "spell_haste_rating", "mp5", "dodge_pct", "parry_pct", "block_pct", "resilience",
              "honorable_kills", "dishonorable_kills")
ALTO_KEYS = {
    "spells": frozenset({"source", "at_basis", "class", "spells", "known", "future"}),
    "reps": frozenset({"source", "at_basis", "factions", "n"}),
    "stats": frozenset({"source", "at_basis", *ALTO_STATS}),
    "lockouts": frozenset({"source", "at_basis", "lockouts", "n", "weekly_reset_day", "weekly_reset_hour"}),
    "sheet": frozenset({"source", "at_basis", "level", "xp", "xp_max", "rested_xp", "money_copper", "zone",
                        "subzone", "bind", "logged_out"}),
}


def _folder(name: str) -> str:
    return unicodedata.normalize("NFC", name.strip()).replace(" ", "-")


def allowed(ev):
    """One event as the shipper of this version would have sent it: other
    players' data cut to what a feature reads. Anything that isn't a dict,
    or a feed not listed above, is returned as it came (ingest.screen
    judges it)."""
    if not isinstance(ev, dict):
        return ev
    feed, msg = ev.get("feed"), ev.get("_msg")
    if feed == "wow-player":
        keys = PLAYER_KEYS
    elif feed == "wow-companion":
        keys = ("display_id",)
    elif feed == "wow-recorder":
        kind = ev.get("kind")
        keys = rec_keys(kind) if isinstance(kind, str) else frozenset()
        if isinstance(kind, str) and isinstance(msg, str):
            ev = {**ev, "_msg": rec_line(kind, msg)}
    elif feed == "wow-combatlog":
        keys = CL_KEYS
        m = CL_LINE.match(msg) if isinstance(msg, str) else None
        if m:  # a 0.6.x raw line: parsed again, its names screened
            rest = m.group(10) or ""
            cols = next(csv.reader([rest]), []) if rest else []
            char = ev.get("char")
            mine = {_folder(char)} if isinstance(char, str) else set()
            ev = {k: v for k, v in ev.items() if k not in ("source_name", "dest_name")}
            fields = cl_fields(m.group(9), cols, mine)
            if "zone" in ev and "zone" not in fields:
                fields["zone"] = ev["zone"]
            ev = {**ev, **fields}
    elif feed == "wow-altoholic":
        kind = ev.get("kind")
        keys = ALTO_KEYS.get(kind, frozenset()) if isinstance(kind, str) and kind != "sheet" else frozenset()
    elif feed == "wow-character" and ev.get("source") == "altoholic" and "kind" not in ev:
        keys = ALTO_KEYS["sheet"]
    else:
        return ev
    return {k: v for k, v in ev.items() if k in EVENT_BASE or k in keys}
