"""The public view: what anonymous visitors may see, by allow-list.

Four things, for every character (owner, 2026-11-18, after v0.2.0 showed
"way too much data"):

1. the character card: the 3D model in its gear, name, level, race and
   class, item level, equipment, professions; its spec, talents (the frame
   and points per tree, from Battle.net) and Legacy points; and its
   class at that level (owner, 2026-11-19: the trainer spells to buy and
   the class trainers of its side, the next talent points, habits: built
   from the card's own class, race, side, level and talents);
2. the climb: played time per level against Polestar's route, totals per
   level only;
3. the dungeons: which one, when, at what level;
4. the level snapshots: the gear (and the 3D look) at each past level.

Plus each worn slot's BiS standing on the paperdoll (owner, 2026-11-23):
the badge only, its rank, percent and label (wowstats.gear.standing_out),
never the gear advice behind it (the upgrades, what to wear, where to get it).

Plus one state under the equipment: whether it may be out of date
(wowstats.coverage.public; owner, 2026-11-22), never when it was read.

Everything else stays in the full content (/zogzog/, /full/): the Overview's
advice, the current zone and guide step, deaths and killers, the journal,
guide steps, flights, zones, the timeline's events, tracked and next
upgrades, gear advice, prices, gold, market data and other players.

PUBLIC holds one shape per answer. A shape names every key that may leave
the server: {key: None} is a plain value (a string, a number, a bool),
{key: {...}} an object with its own shape, {key: [shape]} a list of those.
public() builds a NEW object from a shape, so a field added to a full answer,
or a whole new answer, stays private until someone lists it here.
tests/test_publish.py walks the built public bundle against its own copy of
this list.
"""

from __future__ import annotations

# Every item's badge (wowstats.gear.bis_badge): its share of BiS and the
# change from the worn one. /zogzog/ only: the public view has no advice,
# only the worn slots' standing (STANDING).
from wowstats.gear import BADGE

SCALAR = (str, int, float, bool, type(None))

ITEM = {k: None for k in ("slot", "slot_name", "item_id", "name", "quality", "item_level", "req_level", "enchant_id",
                          "enchant")}
# An item's tooltip (wowstats.itemcard): game data only, the same for every
# player. A bundle holds the cards of the items its own answers name.
CARD = {**{k: None for k in ("id", "n", "q", "i", "b", "u", "s", "t", "ar", "sp", "dps", "rl", "il", "set", "de")},
        "w": [None], "g": [None]}
# A worn slot's standing against BiS (wowstats.gear.standing_out), the
# doll's badge: public since 2026-11-23 (owner), numbers and the label only.
# A negligible slot (wowstats.gear.mark_negligible) has no label, so no
# badge; its line carries the advice's points and stays out.
STANDING = {k: None for k in ("slot", "rank", "pct", "label")}
PROFESSION = {k: None for k in ("skill_id", "name", "rank", "max_rank", "max_rank_from")}
# A past level's item: from a sheet ("exact") or the gear replay ("likely",
# wowstats.history). Public: how it was got, not where or from whom.
SNAP_ITEM = {**ITEM, "certainty": None, "source": {"kind": None}}
M3D_ITEM = {k: None for k in ("slot", "slot_name", "item_id", "name", "why")}
# The 3D view's recipe (wowstats.character3d, v1): file ids, geoset ids and
# numbers only, game data with nothing of the player's but how they look.
M3D_GEOSETS = {"set": {}, "groups": {}}
M3D_RECIPE = {
    "v": None, "model": None, "geosets": M3D_GEOSETS, "animation": None, "textures": {},
    "materials": [{"type": None, "width": None, "height": None,
                   "layers": [{k: None for k in ("file", "target", "x", "y", "w", "h", "blend")}]}],
    "skinned": [{"model": None, "textures": {}, "geosets": M3D_GEOSETS}],
    "attachments": [{"slot": None, "attach": None, "model": None, "textures": {}, "scale": None,
                     "visuals": [{"model": None, "attach": None}]}],
    "collections": [{"slot": None, "model": None, "textures": {}, "geosets": M3D_GEOSETS}],
}
# The card's talents (wowstats.talents): the frame the game draws, filled in
# from Battle.net's API.
TREE_POINTS = {"tree": None, "points": None}
TALENTS = {
    **{k: None for k in ("available", "source", "at", "level", "spec", "why", "unmatched")},
    "points": [TREE_POINTS],
    "sections": [{"name": None, "points": None,
                  "nodes": [{k: None for k in ("col", "row", "name", "icon", "rank", "max", "desc")}],
                  # The prerequisite arrows, cell to cell (talents.py, TraitEdge).
                  "edges": [{k: None for k in ("fc", "fr", "tc", "tr")}]}],
}
# What the gear is scored for, without its sanity_check (the full view's
# planning note).
SPEC_CARD = {"name": None, "source": None, "split": None, "points": [TREE_POINTS],
             "scored_for": {"key": None, "label": None, "source": None}}
LEGACY = {**{k: None for k in ("available", "earned_points", "total", "spent", "why")}, "earned": [None]}
# "Your class at level N" (wowstats.classguide): what to train now and next,
# the class trainers in the capitals of the character's side (race capital
# first, never by distance), the next talent points, a habit or two. Built
# from the class, race, side, level and talents only: public.
CG_SPELL = {k: None for k in ("id", "name", "rank", "level", "cost_copper", "url", "needs_talent")}
CG_TRAIN = {"level": None, "spells": [CG_SPELL]}
CLASS_GUIDE = {
    **{k: None for k in ("class", "level", "spec")},
    "train": {"now": CG_TRAIN, "next": CG_TRAIN},
    "trainers": [{**{k: None for k in ("npc", "name", "url", "x", "y")},
                  "zone": {k: None for k in ("name", "id", "url")}}],
    "talents": [{k: None for k in ("level", "talent", "tree", "rank", "max", "url", "now")}],
    "habits": [None],
    "respec": {k: None for k in ("level", "action")},
}

# "State of the meta" (wowstats.classmeta.paragraph): the knowledge base's
# words for the class and spec, and when they were last checked. Built from
# the class, spec and level only: no data of the player's. /zogzog/ only
# (owner, 2026-11-20); the full description and the adherence are the live
# full view's (/api/meta). `soon`: one line when an event that concerns the
# class at its level starts within two weeks (wowstats.news.soon_line; owner,
# 2026-11-20), from the class, spec and level too.
META_PARAGRAPH = {k: None for k in ("class", "spec", "text", "checked", "stale", "soon")}

DATA_VERSIONS = {"foreverdb": {}, "forever_db": {}, "polestar": None, "client_build": {}, "checked": None}

LEVEL_SPLIT = {k: None for k in ("level", "seconds", "partial", "route_s")}

PUBLIC: dict[str, dict] = {
    # What the page may turn on: nothing beyond the four parts above.
    # What the static data was built from (wowstats.dataversions): source
    # versions, nothing about a person.
    "config": {"view": None, "static": None, "data_versions": DATA_VERSIONS},
    # The character switcher.
    "chars": {"chars": [{"char": None, "level": None, "demo": None}]},
    # The card, the climb and the dungeons. No zone, no "last seen", no
    # guild, no gold, no kills or quests, no pace or ETA: only what the card
    # and the climb draw.
    "char": {
        "char": None, "generated_at": None, "stale": None, "route_source": None,
        "header": {k: None for k in (
            "char", "name", "race", "class", "faction", "level", "xp", "xp_max", "polestar", "title",
            # The climb: played time (Polestar's splits, Altoholic's /played)
            # and how far into the level in play.
            "played_s", "played_total_s", "first_level", "first_partial", "time_in_level_s")},
        "levels": [LEVEL_SPLIT],
        # Cleared, when, at what level: no group, no bosses, no time or XP.
        "dungeons": [{"name": None, "level": None, "t": None}],
        "gear": {"item_level": None, "armory_item_level": None, "armory_item_level_source": None,
                 # Battle.net's items when Syndicator sent none, and the line that says so.
                 "items_from": None, "items_note": None,
                 "render": {"ext": None, "pending": None}, "items": [ITEM],
                 "professions": [PROFESSION],
                 # The doll's badges: each worn slot's standing (worn_standing).
                 "standing": [STANDING]},
        "spec": SPEC_CARD, "talents": TALENTS, "legacy": LEGACY, "class_guide": CLASS_GUIDE,
        # Whether the equipment may be out of date (wowstats.coverage.public):
        # a state, never when it was read (owner, 2026-11-22).
        "coverage": {"battlenet": None},
    },
    # The level slider's stops: no events.
    "timeline": {"char": None, "generated_at": None, "levels": [None]},
    # The gear at a past level: no time, zones, quests, deaths or upgrades.
    # Its picture (wowstats.pictures): drawn (the file's extension) or pending.
    "snapshot": {"char": None, "level": None, "available": None, "why": None, "generated_at": None,
                 "gear_reconstructed": None, "gear": {"item_level": None, "items": [SNAP_ITEM]},
                 "picture": {"ext": None, "pending": None}},
    # The cards of the items the answers above name (their tooltips).
    "items": {"items": [CARD]},
    # The 3D viewer's answer (wowstats.model3d): the recipes to draw (the
    # updated model, and the original one for the page's Classic toggle),
    # the look's source and the gear it wears.
    "model3d": {
        "race": None, "sex": None, "sex_known": None, "class": None, "level": None, "why": None, "models": None,
        "recipe": M3D_RECIPE, "recipe_sd": M3D_RECIPE,
        # The options with no original version (the page's note under Classic).
        "no_classic": [None],
        "appearance": {"source": None, "refreshed": None},
        "items": [M3D_ITEM], "missing": [M3D_ITEM],
    },
}


# ------------------------------------------------------------ /zogzog/

# The full content friends open with the link (owner, 2026-11-18): the public
# view, plus the loot history and the Overview's moves (owner, 2026-11-18:
# they name the item, the dungeon, the profession, the zone), plus the rest
# of the full view, minus
# - other players: the People view, group members, the players around a
#   session, a run's party size (those stay in /full/ only);
# - where the character is now: its zone, when it was last seen, the guide
#   step it's on, the "Right now" lines;
# - the deaths' killers: deaths are counts per level and zone at most;
# - the advice's lines about creatures (the Overview's moves keep items,
#   dungeons, professions, zones, guide steps: ZOGZOG_MOVE_KINDS).
# Same shapes as PUBLIC: a key not listed here never reaches the bundle.


def _keys(*names: str) -> dict:
    return {k: None for k in names}


CLAIM = _keys("text", "link", "cite", "kind", "item_id", "skill_id")
# What the two questions looked at; their lines are the Overview's moves.
CHECKS = {"checks": [None], "not_possible_yet": [None]}
# The Overview's moves (wowstats.insights.overview_moves), named: the item
# and its slot, the dungeon, the profession's next rank, its trainers and
# price, riding, the zone where deaths pile up with what they had in common
# (counts: levels, how many above, how many against several). Only these
# kinds: a death to a creature, or to one well above the character's level,
# names or comes from a killer. Trainers come in the data's order, by zone,
# never by distance from the character.
ZOGZOG_MOVE_KINDS = frozenset({"upgrade", "dungeon", "profession", "riding", "deaths_zone", "slow_steps", "talents",
                               "enchant"})
PLACE = {**_keys("name", "id", "url"), "levels": [None]}
MOVE_ITEM = {**_keys("item_id", "name", "quality", "slot", "slot_name", "gain", "gain_share_pct", "url", *BADGE),
             "with": _keys("item_id", "name", "slot_name", "kind", "url")}
TRAINER = {**_keys("npc", "name", "url", "x", "y"), "zone": PLACE}
# One more run of a dungeon (wowstats.plan.dungeongain): time, XP, pace
# against the character's own questing, the quests to pick up and who gives
# them (game data: the NPC's spawn, never the character's place).
WHO = _keys("kind", "id", "name", "url", "zone", "x", "y", "spawns", "inside")
GAIN = {**_keys("own_runs", "level", "run_s", "time", "xp_kills", "xp_quests", "xp", "level_pct", "xp_per_hour",
                "kills_unknown", "questing_xp_per_hour", "vs_questing", "questing_s", "quests_shown", "summary",
                "lead", "upgrades"),
        "quests": [{**_keys("quest_id", "name", "url", "xp_now", "state", "level"), "starts": [WHO]}],
        # A wing's quests its trip doesn't finish (game data: the wings it still needs).
        "quests_needing_other_wings": [{**_keys("quest_id", "name", "url", "xp_now", "state"),
                                        "other_wings": [None]}],
        # The trip's parts, each with its label (measured / route / estimate).
        # `from` says where it starts ("you", "entrance"), never the place.
        "trip": {**{k: _keys("value", "label") for k in ("pickup_s", "inside_s", "handin_s", "travel_s", "total_s",
                                                         "xp_quests", "xp_kills", "xp_total", "xp_per_hour",
                                                         "vs_questing")},
                 "from": None, "stops": _keys("pickup", "handin")}}
# A dungeon's quest rewards he could use (wowstats.plan.dungeonview
# quest_rewards): the item, its gain, the quest and who gives it.
QUEST_REWARD = {**_keys("item_id", "name", "quality", "slot_name", "gain", "gain_pct", "fills_empty", "level", "now",
                        "url", "of", *BADGE),
                "quest": {**_keys("quest_id", "name", "url", "state", "complete"), "starts": [WHO]}}
# Our dungeon against Polestar's dungeons for the level (wowstats.plan.vsguide):
# dungeon names, levels and the character's own counts and paces.
VS_GUIDE = {**_keys("agrees", "we_say", "reason", "under_floor", "text"), "guide_says": [None],
            "numbers": _keys("level", "floor", "from_level", "max", "quests_ready", "quests_in_log",
                             "quest_xp_in_log", "own_runs", "run_xp_per_hour", "questing_xp_per_hour",
                             "vs_questing")}
MOVE = {**_keys("text", "link", "cite", "kind", "title", "detail", "action", "blocker", "item_id", "skill_id",
                "weight"),
        "item": MOVE_ITEM, "items": [MOVE_ITEM], "zone": PLACE, "quest_rewards": [QUEST_REWARD],
        "source": {**_keys("kind", "boss", "profession", "blocker", "price_copper", "seen_day", "seen_days_ago"), "quest": _keys("id", "name", "url"),
                   "zone": PLACE},
        "skill": {**_keys("skill_id", "name", "rank", "max_rank", "next", "need_level", "level_ok", "need_skill",
                          "skill_ok", "cost_copper", "cost_scope", "money_copper", "search_url"),
                  "trainers": [TRAINER]},
        "deaths": {**_keys("n", "above", "crowd", "come_back_at"), "levels": [None]}, "gain": GAIN,
        "vs_guide": VS_GUIDE}
HEADER_Z = _keys("char", "name", "full_name", "race", "class", "faction", "guild", "guild_known", "spec", "title",
                 "sex", "level",
                 "xp",
                 "xp_max", "rested_xp", "time_in_level_s", "played_s", "played_total_s", "played_total_from",
                 "played_total_at", "first_partial", "first_level", "avg_level_s", "avg_levels", "xp_per_hour",
                 "eta_s", "polestar", "sources", "money_copper", "money_at") | {
    # The climb's kills and quests in their populations (wowstats.analysis.pages.climb).
    "counts": {"completed_levels": _keys("from_level", "to_level", "kills", "quests"),
               "current_level": _keys("level", "kills", "quests"),
               "including_current_level": _keys("kills", "quests")}}
# An "ah" source (wowstats.ahsource): the price his own scan saw today or
# yesterday, and what it costs against his gold.
SOURCE = _keys("kind", "quest_id", "name", "zone", "level", "req_level", "dungeon", "choices", "boss", "npc_id",
               "chance_pct", "instance", "profession", "skill", "spell_id", "your_rank",
               "can_make", "skill_ok", "known", "pattern_item_id", "floor", "price_copper", "seen_day",
               "seen_days_ago", "listed", "gain_per_gold", "gold_share_pct", "affordable", "mount_share_pct",
               "cheap",
               # ForeverDB's sources (wowstats.gear foreverdb_sources): game data and
               # how sure it is, nothing about the character.
               "via", "label", "basis", "kills", "world", "rare", "standing", "faction_id", "rank",
               "container_item_id", "object", "map", "x", "y",
               # How hard it is (wowstats.gear source_effort) and a drop's expected runs.
               "effort", "expected_runs",
               # What a quest takes (wowstats.questwork says): the route's
               # hand-in, the chain, the foe; and a creature's levels and rank.
               "work_says", "unplaced") | {"foe": {**_keys("rank", "elite"), "levels": [None]},
                               "levels": [None], "part": [None], "levels_drop": [None],
    # What a craft takes, priced (wowstats.crafting.brief): its line, the next
    # step, the gold to craft it and the auction house price the other way.
    "plan": _keys("line", "action", "effort", "recipe", "craft_copper", "buy_copper", "buy_days_ago",
                  "bind_on_pickup", "can_make")}
AH = {**_keys("available", "scan_day", "scan_days_ago", "on_sale", "upgrades_on_sale", "money_copper",
              "cheap_under_copper", "notice"), "mount": _keys("name", "level", "need_copper", "left_copper")}
# `stats`: the item's stats as the scoring reads them (game data), for the
# side-by-side comparison.
ADVICE_ITEM = {**_keys("item_id", "name", "quality", "item_level", "req_level", "score", "known", "random_suffix"),
               "stats": {}}
# Who an item is built for and how it fits the spec (wowstats.gearfit):
# game data and the character's spec, nothing about his place or people.
FIT = {**_keys("for_you", "label", "built_for", "wasted_pct", "tip", "factor"), "notes": [None],
       "wasted_stats": [None], "armor": _keys("item", "best"),
       "alternative": {**_keys("item_id", "name", "quality", "gain", "level", "now", "slot_name", "label", *BADGE),
                       "sources": [SOURCE]}}
# No "here" (in the zone the character is in now).
UPGRADE = {**ADVICE_ITEM, **_keys("level", "slot", "slot_name", "gain", "gain_pct", "gain_share_pct",
                                  "fills_empty", "now", "vs_unknown", "setup_score", "value", "effort", *BADGE),
           "sources": [SOURCE],
           "bis": _keys("from_pct", "to_pct"),
           "gates": [_keys("kind", "profession", "need", "have")], "fit": FIT}
UPGRADE_PAIR = {**UPGRADE, "with": {**UPGRADE, "kind": None}}
# A slot's BiS and the pick against it (wowstats.gear pair_why, ah_cost):
# the item, how hard it is, and on the auction house its price against his
# gold and the riding goal (gold stays in the full content).
COST = {**_keys("price_copper", "money_copper", "short_copper", "says", "action"),
        "goal": _keys("what", "level", "need_copper", "left_copper")}
BIS = {**UPGRADE_PAIR, **_keys("worn", "setup"), "cost": COST}
PICK = {**_keys("item_id", "name", "same", "within_reach", "behind_bis_pct", "says"), "reasons": [None]}
SPEC = {**_keys("key", "label", "source", "why", "role"), "choices": [_keys("key", "label")]}
GEAR_SHEET = {**_keys("at", "item_level", "guild", "spec", "class", "race", "faction", "sex", "armory_item_level",
                      "armory_item_level_source", "items_from", "items_note"),
              "render": _keys("ext", "pending"),
              "items": [ITEM], "professions": [PROFESSION],
              "wanted": [_keys("item_id", "name", "item_level", "req_level", "slot_name", "level")]}
# per_level: the ratio and how many levels it read, not which ones (which
# levels the Recorder saw whole is coverage's to say: COVERAGE_Z below).
DEATH_COUNTS = {"total": None, "by_zone": [_keys("zone", "n")], "by_level": {},
                "per_level": _keys("value", "deaths", "n")}
# The full view's coverage lines (wowstats.coverage, owner 2026-11-22): what
# the Recorder missed, by level and length, and when Battle.net last read the
# equipment. Only what the page's lines read: no gap's times.
COVERAGE_Z = {"recorder": {**_keys("state", "fix"), "gaps": [_keys("level", "off_s")]},
              "battlenet": _keys("state", "as_of")}
LOOT_ITEM = _keys("item_id", "name", "quality", "count", "t", "zone")

# The Dungeons panel (wowstats.plan.dungeonview): game data, his own runs
# (no party), what he has of each dungeon's items, the gold its loot made.
QREF = {**_keys("quest_id", "name", "state", "url", "any"), "starts": [WHO], "drops": [WHO], "found": [WHO]}
DUNGEON = {
    **_keys("area_id", "name", "min", "max", "state", "opens_at", "url", "zone_url", "value", "verdict", "why",
            "drops_shown", "missing_now", "obtained", "expected_gain_per_clear", "expected_gain_share_pct",
            "chance_one_per_clear_pct"),
    "drops": [{**_keys("item_id", "name", "quality", "item_level", "slot_name", "score", "gain", "gain_pct",
                       "fills_empty", "level", "now", "state", "where", "url", "value", *BADGE),
               "drops": [_keys("boss", "npc_id", "chance_pct", "wing", "url")], "quests": [None], "fit": FIT}],
    "kb_drops": [{**_keys("name", "boss", "armour", "item_id", "url", "fit", "confidence", "item_level", "quality",
                          "slot_name"), "roles": [None], "stats": [None]}],
    "kb_fit": None,
    "quests": [{**_keys("quest_id", "name", "level", "min_level", "xp", "money_copper", "route", "state", "complete",
                        "xp_now", "url", "reward_choices"),
                "starts": [WHO], "ends": [WHO], "needs": [QREF], "blocked": None,
                "rewards": [_keys("item_id", "name", "quality", "gain", "state", "where", "url", *BADGE)]}],
    "quest_rewards": [QUEST_REWARD],
    "trip": {**_keys("measured", "n", "inside_s", "travel_s", "quests_s", "total_s", "xp_kills", "xp_quests",
                     "xp_per_run", "xp_per_hour", "again_s", "again_xp", "again_xp_per_hour", "vendor_copper",
                     "ah_copper", "quest_money_copper", "quest_xp_left"),
             "boe": [_keys("item_id", "name", "quality", "ah_copper")]},
    "gain": GAIN,
    "history": [_keys("t", "level", "inside_s", "total_s", "xp_kills", "xp_quests", "quests_handed_in",
                      "vendor_copper", "items")],
    "entrance": {**_keys("zone", "x", "y", "source", "prep"), "flight": _keys("name", "x", "y")},
    "guides": [_keys("name", "url")],
    "keys": [{**_keys("name", "kind", "need", "note", "item_id", "url", "have"), "quests": [QREF]}],
    "next": _keys("do", "quest_id", "name", "level", "to_go", "meanwhile", "where"),
    "vs_guide": VS_GUIDE,
}

ZOGZOG: dict[str, dict] = {
    "config": {**_keys("view", "static", "players", "gold", "market", "prices", "debug", "mcp", "journal",
                       "people", "loot"), "data_versions": DATA_VERSIONS},
    "chars": {"chars": [_keys("char", "events", "level", "demo")]},
    "char": {
        **_keys("char", "generated_at", "stale", "route_source"),
        "header": HEADER_Z,
        "levels": [{**LEVEL_SPLIT, **_keys("xp", "quests", "kills", "at", "xp_per_hour")}],
        "climb": [_keys("level", "t")],
        "zones": [_keys("zone", "secs", "visits")],
        "steps": {**_keys("skipped_total", "total", "median_s", "picks_total"), "guides": [None],
                  "slowest": [_keys("guide", "step", "secs", "n", "zone", "title", "guide_title")],
                  "skipped": [{**_keys("guide", "t", "zone", "how", "guide_title"), "steps": [None]}],
                  "picks": [_keys("guide", "title", "step", "t")]},
        "dungeons": [_keys("name", "level", "seconds", "xp", "t", "xp_per_hour")],
        "deaths": DEATH_COUNTS,
        "flights": {**_keys("count", "total_s"),
                    "longest": _keys("from_zone", "to_zone", "secs", "t"),
                    "recent": [_keys("from_zone", "to_zone", "secs", "t")]},
        "gear": GEAR_SHEET,
        "spec": SPEC_CARD, "talents": TALENTS, "legacy": LEGACY, "class_guide": CLASS_GUIDE,
        "meta": META_PARAGRAPH, "coverage": COVERAGE_Z,
    },
    "journal": {**_keys("char", "gap_s", "generated_at"), "sessions": [{
        **_keys("start", "end", "played_s", "level_start", "level_end", "levels_gained", "xp", "gold_delta_copper",
                "estimated"),
        "recorder_off": {"off_s": None, "levels": [None]},
        "dings": [_keys("level", "t")], "zones": [_keys("zone", "secs")],
        "quests": [_keys("quest_id", "name", "level", "zone", "t") | {"part": [None]}],
        "deaths": {"total": None, "by_zone": [_keys("zone", "n")]},
        "dungeons": [_keys("name", "level", "seconds", "xp", "t")]}]},
    "gear": {
        **_keys("char", "name", "polestar", "available", "why", "at", "class", "level", "min_upgrade_pct",
                "soon_levels", "upgrades_total", "generated_at", "stale", "gear_score"),
        # The weights' mode and whose they are: the page credits ForeverDB.net.
        "mode": {**_keys("key", "label"), "choices": [_keys("key", "label")]},
        "scoring": _keys("mode", "label", "note", "source", "style"),
        "source": _keys("name", "url", "text"),
        "spec": SPEC, "weights": {}, "talents": [{}],
        # The weights as text for Forever Leveling BiS (wowstats.gearexport):
        # the same numbers as `weights`, nothing of the character's.
        "export": {**_keys("class", "spec", "mode", "setup", "setup_label", "name", "how", "flb", "pawn"),
                   "credit": _keys("name", "url", "text"), "notes": [None], "pawn_notes": [None]},
        # negligible: the slot's line when nothing for it matters much
        # (wowstats.gear.mark_negligible), shown in place of BiS and percents.
        "slots": [{**_keys("slot", "slot_name", "negligible"), "standing": _keys("rank", "pct", "label", "negligible"),
                   "current": ADVICE_ITEM, "best": {**UPGRADE_PAIR, "cost": COST},
                   "next": UPGRADE_PAIR, "bis": BIS, "pick": PICK}],
        "upgrades": [UPGRADE_PAIR],
        "ah": AH,
    },
    "overview": {
        **_keys("char", "generated_at", "route_source", "levels_seen"),
        "header": HEADER_Z,
        "on_track": {**_keys("route_ratio", "played_s", "route_s", "last_level", "last_s", "own_median_s",
                             "own_ratio", "eta_s", "verdict", "level_played_s"),
                     "route_levels": [None], "claims": [CLAIM],
                     "per_level": [_keys("level", "seconds", "route_s")]},
        # No "current": the step the character is on now.
        "guide": {**_keys("total", "skipped", "jumped", "in_order", "over_plan_s"), "claims": [CLAIM],
                  "over_plan": [_keys("guide", "step", "secs", "route_s", "title", "guide_title", "zone")]},
        "moves": [MOVE],
        "next_moves": CHECKS,
        "sanity_check": CHECKS,
    },
    "timeline": {**_keys("char", "generated_at", "gear_history_since"), "kinds": [None], "levels": [None],
                 "events": [_keys("t", "type", "text", "link", "seconds")]},
    "snapshot": {
        **_keys("char", "level", "available", "why", "generated_at", "from", "to", "seconds", "so_far",
                "partial_time", "gear_partial", "gear_at", "route_s", "deaths", "gear_reconstructed",
                "gear_records_from"),
        "counts": {}, "levels": [None], "zones": [_keys("zone", "secs")],
        # The quest and the zone, never the creature (as the loot list).
        "gear": {"item_level": None, "professions": [PROFESSION],
                 "items": [{**ITEM, "certainty": None, "source": _keys("kind", "quest", "zone", "t")}]},
        "looted_gear": [_keys("item_id", "name", "inv", "item_level")],
        "bis": {"gear_score": None, "spec": _keys("key", "label", "source", "why"),
                "upgrades": [UPGRADE_PAIR]},
        "picture": PUBLIC["snapshot"]["picture"],
    },
    "dungeons": {**_keys("char", "level", "class", "faction", "spec", "questing_xp_per_hour", "inventory_known",
                         "available", "why", "generated_at", "stale"),
                 "dungeons": [DUNGEON]},
    "loot": {**_keys("char", "generated_at", "total", "notable", "quality_known"),
             "groups": [{**_keys("kind", "name", "level", "t", "notable"), "items": [LOOT_ITEM]}]},
    "model3d": PUBLIC["model3d"],
    "items": PUBLIC["items"],
    # The play analysis: the nudges the pipeline screened (wowstats.analysis
    # .verify.shareable_action: actions with where, what blocks them and the
    # next step; no player, creature, time, date or where the character is
    # now) and the time per level. Pattern lines describe: never here.
    # The talent changes from WoWSims Exporter's saves (views.talent_history,
    # source "addon" only: publish never bundles Battle.net's).
    "talents": {**_keys("char", "days", "available", "why", "source", "since"),
                "changes": [{**_keys("date", "t"), "before": [_keys("tree", "points")],
                             "after": [_keys("tree", "points")],
                             "gained": [_keys("name", "spell", "from", "to")],
                             "dropped": [_keys("name", "spell", "from", "to")]}],
                "not_recorded": [None]},
    "analysis": {**_keys("available", "char"),
                 "nudges": [_keys("text", "where", "blocked_by", "next_step", "verdict", "at_level")],
                 "time_budget": [_keys("level", "online_s", "instance_s", "taxi_s", "dead_s", "afk_s", "planned_s")]},
    "market": {**_keys("configured", "reason", "generated_at"),
               "token": {**_keys("label", "gold", "change_7d", "at"), "series": [[None]]},
               "realms": [_keys("name", "up", "dipped_24h")]},
    "prices": {**_keys("generated_at", "last_scan", "scan_days", "total"),
               "items": [{**_keys("available", "change", "day", "item_id", "item_key", "max", "min", "name", "price",
                                  "realm", "scans"),
                          "series": [_keys("available", "day", "hi", "lo")]}]},
}


def shape(value, spec):
    """value cut down to spec (see the module's docstring)."""
    if spec is None:
        return value if isinstance(value, SCALAR) else None
    if isinstance(spec, list):
        return [shape(v, spec[0]) for v in value] if isinstance(value, list) else []
    if not isinstance(value, dict):
        return None
    if not spec:  # {}: a map of plain values (counts by kind, weights by stat)
        return {str(k): v for k, v in value.items() if isinstance(v, (int, float, str, bool))}
    return {k: shape(value.get(k), sub) for k, sub in spec.items()}


def public(what: str, value: dict) -> dict:
    """The public answer `what`; KeyError for an answer that has none."""
    return shape(value, PUBLIC[what])


def zogzog(what: str, value: dict) -> dict:
    """The /zogzog/ answer `what`; KeyError for an answer it doesn't have
    (people). Deaths lose their killer here, before the shape: a timeline
    event's text and a snapshot's death list name it, and the Overview
    keeps only the moves of ZOGZOG_MOVE_KINDS."""
    if what == "timeline":
        value = {**value, "events": [{**e, "text": "Died"} if e.get("type") == "death" else e
                                     for e in value.get("events") or [] if isinstance(e, dict)]}
    elif what == "snapshot" and isinstance(value.get("deaths"), list):
        value = {**value, "deaths": len(value["deaths"])}
    elif what == "overview":
        value = {**value, "moves": [m for m in value.get("moves") or []
                                    if isinstance(m, dict) and m.get("kind") in ZOGZOG_MOVE_KINDS]}
    return shape(value, ZOGZOG[what])


TIERS = {"public": public, "zogzog": zogzog}


def worn_standing(advice: dict | None) -> list[dict]:
    """The worn slots' standing against BiS from a gear advice answer
    (wowstats.gear.advise: slots[].standing), for the public doll's badges
    (owner, 2026-11-23). Nothing else of the advice: the shape (STANDING)
    keeps the slot, rank, percent and label."""
    out = []
    for sl in (advice or {}).get("slots") or []:
        st = sl.get("standing") if isinstance(sl, dict) else None
        if isinstance(st, dict) and sl.get("current"):
            out.append(shape({**st, "slot": sl.get("slot")}, STANDING))
    return out


def char(d: dict, route: dict[int, int] | None = None, route_source: str | None = None) -> dict:
    """The character page's answer, with the route's time for each level
    (Polestar's route clock, wowstats.mcp.route) next to the played one."""
    if route is None and route_source is None:
        return public("char", d)
    route = route or {}
    levels = [{**lv, "route_s": route.get(lv.get("level"))} for lv in d.get("levels") or [] if isinstance(lv, dict)]
    return public("char", {**d, "levels": levels, "route_source": route_source})
