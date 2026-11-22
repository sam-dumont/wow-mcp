"""Prep: one character's play history as SQLite tables, facts only.

The model driving the analysis (wowstats.analysis.drive) asks its own questions of these
tables. So they hold what the records say and neutral sums of it: when,
where, what kind of record, with which ids, and how long between records.
No record is grouped into a category that would answer a question before
it is asked (owner, 2026-11-18: "raw and aggregate data", the conclusions
are the model's). Every table and column is described in `dictionary`.

The database lives in memory and is rebuilt on every run from the stores:
nothing here is ever written to disk. Battle.net data (vm-wow, 30 days at
most by Blizzard's terms) stays out of it entirely, so it has no talents:
since 2026-11-20 they come from Battle.net's API only (no add-on).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from wowstats import gear, profcaps
from wowstats import quests as questsmod
from wowstats import timebounds as tb
from wowstats.stores import (
    SESSION_GAP_S,
    VL_PAGE,
    dedupe,
    parse_t,
    pick_splits,
    record_id,
    record_order,
    taxi_time,
    truthy,
    zone_time,
)

# What the tables mean. Bump it in the same change as anything that makes a
# query give another answer on the same events: a table, column or unit, a
# gap or interval, the order rows are read in, a dedupe rule. A finding
# judged under another PREP_VERSION is shown "to revalidate" instead of
# being rechecked (wowstats.analysis.manifest). A pure refactor keeps it.
# 2: a zone record's time from the records' own times (stores.zone_time), not
# the shipper's duration_s; same-time records by chunk and index as numbers.
# 3: a taxi record's time from its land record (stores.taxi_time), not the
# shipper's duration_s, which a flight across two shipper runs lacked.
# 4: the gear in force picked like the page's (an exact sheet first, the
# items from the newest sheet with any); a snapshot whose id is its content
# (bags, quest sets) kept per observation time, so quest evidence keeps
# when it was first seen (evidence properties, tests/invariants/test_evidence.py).
# 5: levels gets its calendar time, offline time and longest silence (the
# view's calendar_s moved here); gear gets the worn items' score and fit for
# the spec the advice scores (seeded evaluation: offline_gap, wrong_spec_gear).
# 6: upgrade_candidates gets each item's BiS share and the worn one's (the
# page's "17% → 100% on your head"), its fit for the scored spec, and each
# source in the gear advice's words with its Wowhead page: the brief and the
# semantic layer's `upgrades` read them (decision data without play_sql).
# 7: levels.planned_s for a character the Recorder never saw uses the class
# and side its character sheets give (as meta does), not the median over
# classes.
# 8: upgrade_candidates gets the inventory slot its gain is measured in, so
# an item that fits both rings or trinkets is one row per slot it was
# measured against (the MCP's gear reads its numbers by item and slot).
# 9: quests gets `failed` (a fail record closes the quest) and the quests in
# the log accepted before tracking (objectives seen done, no accept): the
# plan's open quests read the quests model (semantic wave 3).
# 10: levels gets in_play (the level being played) and climb_kills and
# climb_quests (the split's counts, else the Recorder's records at that
# level): one population-labelled count for the page and the MCP (external
# review, 2026-11-22: 1,188 kills on the page, 1,262 in character_summary).
PREP_VERSION = 10

# A step that is the trip somewhere ("Fly to ...", "Hearth to ..."): the
# route position starts past it when the character already stands there.
TRAVEL = re.compile(r"^(fly|boat|tram|zeppelin|portal|to|back to|hearth|take the|ride)\b", re.I)


def _zone() -> tuple[str, ZoneInfo]:
    """The player's local time for hours, weekdays and the nightly recheck
    (wowstats.timebounds.zone: WOW_TIMEZONE, Europe/Brussels when unset)."""
    return tb.zone()


TZ_NAME, TZ = _zone()
# The Recorder opens a chunk with a `session` record every 300 s while the
# game runs (Polestar_Recorder CHUNK_SECONDS): a silence longer than this is
# time logged out.
OFFLINE_S = SESSION_GAP_S  # one session rule with the pages (wowstats.stores)
# A dungeon trip's guide steps chain while each comes within this of the next
# (or of the instance): an older pickup step belongs to an earlier attempt.
TRIP_GAP_S = 7200
# Fields every row has, or that only the transport adds: not part of `extra`.
COMMON = {"_msg", "_time", "_stream", "_stream_id", "component", "layer", "site", "feed", "kind", "char",
          "source_id", "migrated_from", "rev", "demo"}
# Never in the database: other players' names (the records' `player`).
DROP = {"player", "seen_by_player"}

# What each Recorder record kind is, from Polestar's docs/RECORDING.md
# (schema 2), shortened. The analysis reads it from the `record_kinds` table.
KINDS = {
    "session": "the Recorder's chunk header: at login, /reload and every new 5-minute chunk while the game runs",
    "map": "a map's frame (top/left/bottom/right in world yards), once per map seen",
    "level": "a level up (ding)",
    "xp": "the XP bar: every 5 minutes and at a level up; xp_gained is the change since the previous xp record",
    "group": "group size changed (0 solo, 1-4 party members, raid size); never who",
    "pos": "a position: after 25 yd and 2 s since the last, or 4 yd and 20 s (in the positions table)",
    "zone": "the zone or subzone text changed",
    "ride": "moved while the player's own speed is 0 (boat, zeppelin, lift, tram) on=1, and when that stops on=0",
    "taxi": "a flight took off (from_node, to_node)",
    "land": "a flight landed (seconds in the air)",
    "death": "the player died; npc_id/npc_level: the last hostile creature that targeted or hit the player within "
             "10 s, when readable",
    "revive": "alive again (how: s spirit healer, c corpse, r resurrected)",
    "combat": "entering (on=1) and leaving (on=0) combat",
    "afk": "the game's AFK flag turned on (on=1) or off (on=0)",
    "rest": "an eat or drink aura ended (rest_kind f food, d drink, b both; seconds it lasted)",
    "offer": "a gossip, quest greeting or quest detail window opened",
    "detail": "a quest's detail shown",
    "text": "the first time this character saw a quest's text",
    "accept": "a quest accepted",
    "objectives": "a quest's objectives, at accept",
    "progress": "a quest objective's count changed (have/need)",
    "complete": "all objectives of a quest done",
    "turnin": "a quest handed in (xp_reward and money paid; item_id the chosen reward)",
    "abandon": "a quest abandoned",
    "fail": "a quest in the log failed",
    "gossip": "a gossip option chosen",
    "refuse": "the game refused something for a requirement (error is the game's constant name)",
    "cast": "the player used a quest or usable item, or cast a spell that is not a class spell on the action bars; "
            "never combat rotation spells (spell_id, item_id)",
    "escort": "an escort or event quest started (on=1) or ended (on=0)",
    "kill": "a creature the player or pet killed (level, classification n/e/r/R/b, xp_reward, tapped: another player "
            "had the tag, quests: objective ticked within 3 s)",
    "spawn": "right after a kill: the creature's spawn id",
    "loot": "a loot window, one per source (items in loot_items; gather h herb, m mine, s skin, f fish)",
    "gather": "a world object gathered with a skill",
    "unit": "a creature seen on a nameplate, target or mouseover (once per id and level per session)",
    "unitx": "a creature seen, with its spawn id",
    "rare": "a rare or rare elite sighted",
    "enter": "entering an instance (the position is the last one outside)",
    "encounter": "a boss encounter ended (success 1/0)",
    "trainer": "a trainer window opened",
    "vendor": "a merchant window opened",
    "flightmaster": "the flight map opened",
    "bind": "the hearthstone was bound",
    "rep": "reputation changed (faction_id, rep_gained)",
    "skill": "a skill's rank changed (skill_id, rank, max_rank)",
    "explore": "a new area discovered (xp_reward)",
    "learn": "a spell learned",
    "craft": "an item made from the trade skill window (spell_id, item_id, count)",
    "achieve": "an achievement",
    "step": "the guide's step or guide changed (reason next, skip, back, auto (also at login and reload), pick, "
            "done)",
    "where": "the subzone text and indoors flag at a quest NPC, once per NPC per session",
    "poi": "the quest log's own map point for a quest (not the player's position)",
    "mark": "a note the player typed with /psrec mark",
    "dropped": "the Recorder dropped chunks for size",
    "open": "the player opened a world object",
    "treasure": "a treasure vignette",
}

DICTIONARY: dict[str, tuple[str, dict[str, str]]] = {
    "meta": ("Facts about this database: the character and the data's range.", {
        "key": "name", "value": "value (text)"}),
    "record_kinds": ("What each Recorder record kind means (from Polestar's recording contract).", {
        "kind": "the record kind", "meaning": "when the Recorder writes it", "n": "rows of this kind in records"}),
    "records": ("Every Recorder record of the character except positions, in time order. One row per record, as "
                "the game addon wrote it; the ids are the game's (look them up with the kb tools).", {
        "rec_id": "row number in time order (also the order of positions)",
        "t": "unix seconds (UTC)", "time_utc": "ISO time, UTC", "time_local": "ISO time, local (meta.timezone)",
        "hour_local": "hour of day 0-23, local", "weekday_local": "Mon..Sun, local",
        "session_id": "the login span it belongs to (sessions)",
        "level": "the character's level at that moment",
        "kind": "record kind (record_kinds)",
        "zone": "zone name where the record says or the last zone record said",
        "subzone": "subzone text of the last zone record", "map_id": "the game's uiMapID, when the record has one",
        "x": "map x in percent", "y": "map y in percent",
        "quest_id": "quest id", "quest_name": "quest title, when the record names it",
        "npc_id": "creature or object id (npc_kind c creature, o object)", "npc_kind": "c creature, o object",
        "npc_level": "level of the creature (kill: its level, death: the killer's)",
        "item_id": "item id (turnin: the chosen reward; craft: the item made; cast: the item used)",
        "spell_id": "spell id (cast, craft, learn)",
        "xp_reward": "XP the record says was gained (kill, turnin, explore)",
        "money_copper": "money paid (turnin) or offered (accept), copper",
        "on_flag": "1 on / 0 off for combat, afk, ride, escort",
        "guide": "Polestar guide id (step records)",
        "step": "guide step number (step records): where Polestar's pointer was, which falls back to an earlier "
                "step at login; the step to do next is route_position.next_step",
        "reason": "why the step changed (step records)",
        "seconds": "a duration: rest (aura length), land (flight), step (seconds since the previous step), "
                   "zone (seconds to the next zone record of the session, else to the session's last record)",
        "count": "craft: items made", "extra": "the record's other fields, JSON (use json_extract)",
        "dungeon_trip_id": "dungeon_trips.trip_id when the record falls inside a trip, else NULL"}),
    "positions": ("Position records: the Recorder writes one after the player moves 25 yd (2 s apart at most "
                  "often) or 4 yd after 20 s. Without movement no record is written.", {
        "rec_id": "row number in time order, shared with records", "t": "unix seconds",
        "time_local": "ISO time, local (meta.timezone)", "session_id": "login span", "level": "level then",
        "map_id": "uiMapID", "zone": "zone name", "x": "map x %", "y": "map y %",
        "speed": "the player's own speed, yards/s (0 on a boat or flight)",
        "flags": "bits: 1 mounted, 2 swimming, 4 indoors, 8 flying, 16 falling, 32 dead or ghost, 64 trail sample",
        "dt_s": "seconds since the previous position in the same session",
        "dist_yd": "straight-line yards from the previous position on the same map and session (NULL across maps "
                   "or when the map's size is unknown)"}),
    "loot_items": ("One row per item in a loot window.", {
        "rec_id": "the loot record", "t": "unix seconds", "item_id": "item id", "count": "how many"}),
    "sessions": ("Login spans: a silence of more than 330 s between Recorder records ends one (the Recorder "
                 "writes a record at least every 300 s while the game runs).", {
        "session_id": "number, in time order", "start_t": "unix seconds", "end_t": "unix seconds (last record)",
        "start_local": "ISO time, local (meta.timezone)", "end_local": "ISO time, local (meta.timezone)",
        "weekday_local": "Mon..Sun of the start", "start_hour_local": "hour of the start",
        "duration_s": "end_t - start_t", "records": "Recorder records in it, positions included",
        "level_start": "level at start", "level_end": "level at end",
        "xp_gained": "sum of xp_gained of its xp records", "kills": "kill records", "deaths": "death records",
        "turnins": "turnin records", "distance_yd": "sum of positions.dist_yd",
        "first_zone": "zone at start", "last_zone": "zone at end", "last_subzone": "subzone at end",
        "last_kind": "kind of the last record that is not session/xp/pos/unit/unitx/spawn",
        "gap_before_s": "seconds offline before it (NULL for the first)"}),
    "timeline": ("The online time cut at every record (positions included): each row is the interval from one "
                 "record to the next inside a session, with the state the records had set at its start. Sum "
                 "dur_s over rows to get time.", {
        "seg_id": "row number", "session_id": "login span", "t0": "start, unix seconds", "t1": "end, unix seconds",
        "dur_s": "t1 - t0", "time_local": "start, ISO local time", "hour_local": "hour of t0",
        "level": "level", "zone": "zone", "subzone": "subzone", "map_id": "uiMapID of the last record with one",
        "guide": "the active Polestar guide (last step record)", "step": "its step",
        "instance": "dungeon name while inside a dungeon run (dungeon_runs), else NULL",
        "dungeon_trip_id": "dungeon_trips.trip_id when the segment starts inside a trip, else NULL",
        "afk": "1 while the AFK flag is on", "combat": "1 while in combat", "dead": "1 from death to revive",
        "taxi": "1 from taxi to land", "ride": "1 while the ride flag is on",
        "mounted": "flags bit 1 of the last position", "speed": "speed of the last position, yards/s",
        "since_pos_s": "seconds from the last position record to t0 (NULL before the first)",
        "opened_by": "kind of the record that starts the interval"}),
    "spans": ("Things the records give a start and an end for (on/off pairs and durations), as observed.", {
        "kind": "afk, combat, ride, escort, dead (death to revive), taxi (taxi to land), rest (eat/drink aura), "
                "instance (a dungeon run), zone (a zone record to the next one or the session's end)",
        "t0": "start, unix seconds", "t1": "end, unix seconds", "dur_s": "t1 - t0",
        "session_id": "login span of t0", "detail": "what (instance name, rest kind, zone|subzone, quest id)"}),
    "xp": ("The xp records: the XP bar every 5 minutes and at each ding.", {
        "t": "unix seconds", "session_id": "login span", "level": "level then", "zone": "zone then",
        "hour_local": "hour of day, local", "xp": "XP into the level",
        "xp_max": "XP the level needs", "xp_gained": "change since the previous xp record",
        "rested_xp": "rested XP left"}),
    "levels": ("Per level: Polestar's own split when the level is done (played seconds), the ding time, the "
               "route planner's time for the level (route_plan), and the calendar around it: a level's pace is "
               "played_s against planned_s; calendar_s also counts the days the game was closed.", {
        "level": "level", "ding_t": "unix seconds the level ended (NULL while in play)",
        "played_s": "played seconds in the level (Polestar split)", "xp": "XP of the level",
        "quests": "quests handed in (split)", "kills": "kills (split)",
        "partial": "1 when tracking started partway through the level",
        "planned_s": "the route planner's seconds for this level, this class",
        "planned_median_s": "the planner's median over classes", "planned_guide": "the guide the level ends in",
        "calendar_s": "wall-clock seconds from the previous level's ding to this one, offline time included "
                      "(NULL for the first tracked level and the level in play)",
        "offline_s": "calendar_s - played_s: seconds the game was closed during the level, both from the game "
                     "(NULL without a whole split)",
        "longest_gap_s": "the longest stretch inside the level (previous ding to this ding, or to the last record "
                         "for the level in play) with no Recorder session: time offline, or play the Recorder "
                         "missed when recorder_missed_s is above 0",
        "longest_gap_from_local": "its start, local time", "longest_gap_to_local": "its end, local time",
        "recorder_missed_s": "played seconds the Recorder's sessions don't cover (the coverage's Recorder gap "
                             "for the level, 0 when none; NULL when it can't be measured)",
        "in_play": "1 for the level being played (no ding out of it, no whole split), else 0",
        "climb_kills": "kills: the split's count, else the Recorder's kill records at the level (NULL: neither)",
        "climb_quests": "quests handed in: the split's count, else the Recorder's turn-ins at the level"}),
    "route_position": ("Where the character is in Polestar's levelling route, one row: the step to do next "
                       "(next_step) by the rule the plan and the next_step tool use. Read it for the next step; "
                       "the newest step record is only where Polestar's pointer was (it falls back to the first "
                       "unfinished step at login).", {
        "guide": "the levelling guide of the newest step record", "guide_title": "its title",
        "shown_step": "the step Polestar showed last", "shown_title": "its title",
        "reason": "why it changed (step records' reason)",
        "furthest_step": "the furthest step the pointer reached in that guide",
        "next_step": "the step to do next: furthest_step, or the first step after it named after where the "
                     "character stands when Polestar fell back", "next_title": "its title",
        "next_why": "why that step, in words", "subzone": "the newest subzone", "zone": "the newest zone"}),
    "route_steps": ("Polestar's route planner: planned seconds per guide step (a model of a quick player who "
                    "knows the route, uncalibrated).", {
        "guide": "guide id", "guide_title": "guide title", "step": "step number from 1", "planned_s": "seconds",
        "title": "the step's title"}),
    "dungeon_runs": ("Polestar's instance runs.", {
        "name": "dungeon", "dungeon_id": "instance id", "level": "level at entry", "start_t": "entered, unix s",
        "left_t": "left, unix s", "seconds": "time inside", "xp": "XP gained inside"}),
    "dungeon_trips": ("A dungeon trip as the Polestar lookup guides bracket it: from the first step of "
                      "lookup/pickup/<dungeon> to the last step of lookup/handin/<dungeon> around one run "
                      "(dungeon_runs), steps chained while each comes within 2 hours of the next; the instance's "
                      "enter and leave when no guide step is there. Times are online time (timeline). records and "
                      "timeline carry dungeon_trip_id for rows inside a trip.", {
        "trip_id": "trip number (records.dungeon_trip_id, timeline.dungeon_trip_id)", "dungeon": "dungeon name",
        "dungeon_id": "instance id", "level_start": "level at the trip's start", "start_t": "start, unix s",
        "end_t": "end, unix s", "start_local": "start, local time", "end_local": "end, local time",
        "bracket": "guides (lookup guide steps) or instance (enter and leave only)",
        "pickup_s": "online time from the start to the last accept of one of the trip's quests",
        "travel_to_s": "from there to entering the instance", "inside_s": "online time inside the instance",
        "travel_back_s": "from leaving to the first hand-in of one of the trip's quests",
        "handin_s": "from there to the end", "online_s": "online time start to end",
        "afk_s": "timeline time with afk=1 inside the trip",
        "xp_kills_inside": "sum of kill records' xp_reward inside the instance",
        "xp_inside": "XP gained inside (dungeon_runs.xp)",
        "xp_quests": "sum of xp_reward over the trip's quests handed in during the trip",
        "quest_ids": "JSON list: quests with objective progress, completion or a hand-in inside the instance, "
                     "or accepted as a pickup guide step advanced",
        "quests_handed_in": "how many of them were handed in during the trip",
        "loot_inside": "JSON list of [item_id, count] looted inside", "loot_inside_n": "items looted inside",
        "rewards": "JSON list of reward item ids picked at the trip's hand-ins (records.item_id on turnin)",
        "still_worn": "JSON list of the loot and reward item ids on the newest gear sheet (gear)",
        "quest_money_copper": "money paid by the trip's hand-ins",
        "gold_before_copper": "money on the last logout snapshot before the start, if any",
        "gold_after_copper": "money on the first snapshot after the end, if any",
        "steps": "JSON list of the guide steps used, as guide#step"}),
    "gear": ("The equipped items on the newest character sheet, with the records that name the same item.", {
        "slot": "inventory slot 1-19", "slot_name": "slot", "item_id": "item id", "name": "item name",
        "quality": "0 poor .. 5 legendary", "item_level": "item level", "req_level": "required level",
        "sheet_t": "time of the sheet, unix seconds",
        "turnin_quest_id": "a turnin record whose chosen reward is this item (quest id)",
        "turnin_t": "its time", "loot_t": "first loot record holding this item", "craft_t": (
            "first craft record naming this item id (an enchanting craft names the item it enchanted)"),
        "vendor_seen": "1 when a vendor window listed it",
        "first_t": "earliest of turnin_t, loot_t and craft_t (null when no record names the item)",
        "first_level": "the character's level at first_t",
        "scored_for": "the spec the gear advice scores for (the player's WEIGHTS setting, else the talents)",
        "score": "the item's score for that spec (Polestar's points; NULL when the item isn't known)",
        "bis_pct": "the item's score as a percent of the best the slot can hold at this level for that spec "
                   "(100: the best; NULL when there is nothing to measure against)",
        "fit": "how the item fits that spec: yes (built for it), any (fits every spec), spec (built for another "
               "spec of the class), class (made for another class), unknown",
        "built_for": "who the item's stats are made for, in words"}),
    "gear_sheets": ("Every character sheet the shipper sent: the equipped items at that time, one row per slot.", {
        "sheet_t": "time of the sheet, unix s", "time_local": "ISO time, local (meta.timezone)",
        "level": "the character's level then (newest record at or before the sheet)", "slot": "inventory slot 1-19",
        "slot_name": "slot", "item_id": "item id", "name": "item name", "quality": "0 poor .. 5 legendary",
        "item_level": "item level", "req_level": "required level"}),
    "professions": ("Professions on the newest sheet (ranks from Polestar).", {
        "skill_id": "skill line id", "name": "name", "rank": "rank",
        "max_rank": "cap: the Recorder's newest skill record, else the trainer tier the rank sits under",
        "max_rank_from": "recorder (read in game) or tiers (a floor from the trainer tiers)"}),
    "snapshots": ("The character snapshots the shipper sends at logout and /reload (from the game's saved "
                  "variables): money, XP and where the hearthstone is bound.", {
        "t": "unix seconds", "time_local": "ISO time, local (meta.timezone)", "level": "level",
        "xp": "XP into the level", "xp_max": "XP the level needs", "money_copper": "money carried, copper",
        "zone": "zone", "bind": "the inn or place the hearthstone is bound to",
        "resting": "1 when the character was in a rested area (inn or city), 0 when not, NULL when unknown",
        "online_s_since_prev": "online time (timeline) between the previous snapshot and this one"}),
    "quests": ("Each quest the log held: its accept paired with its hand-in or abandon (records by quest_id, in "
               "time order), and the online time it took. A quest's time is quest_s: every online second while it "
               "was in the log, shared equally among the quests open then, so the quests' times add up to the "
               "online time with a quest open (nothing counted twice).", {
        "quest_row": "row number", "quest_id": "quest id", "quest_name": "title, when a record names it",
        "status": "done (accept and hand-in seen), abandoned, failed (the game failed it: can't be handed in), "
                  "open (still in the log; no accept_t when only its objectives were seen done), untimed (handed "
                  "in, accepted before tracking began) or 'done (inferred)': still in the log but handed in with "
                  "the Recorder off, as another source shows (done_why; the quests_done table)",
        "accepted_t": "accept, unix s", "closed_t": "hand-in, abandon or fail, unix s",
        "accepted_local": "accept, local time", "closed_local": "hand-in or abandon, local time",
        "level_accepted": "level at the accept", "level": "level at the hand-in or abandon (else at the accept)",
        "zone": "zone of the hand-in (else of the accept)", "xp": "XP the hand-in paid",
        "money_copper": "money the hand-in paid, copper", "wall_s": "calendar seconds from accept to close",
        "open_online_s": "online seconds while in the log (each open quest counts them in full)",
        "quest_s": "online seconds while in the log, each shared among the quests open then",
        "done_why": "for 'done (inferred)': the evidence (a reward only this quest gives worn, in the bags or "
                    "bank; Questie's or Altoholic's completed list; the game's flag; a later quest that needs it); "
                    "closed_t is when that evidence was first seen after the accept, NULL when unknown (its time "
                    "then runs to the last record, as an open quest's)"}),
    "quests_done": ("Every quest the character has done, whatever saw it: the Recorder's hand-ins, the game's "
                    "flags, Questie's and Altoholic's completed lists, rewards only that quest gives held at any "
                    "snapshot, and the quests those need first. The one answer the planner and the pages use; "
                    "quests handed in before the Recorder was installed are here and not in quests.", {
        "quest_id": "quest id", "quest_name": "title (forever-db), when known",
        "source": "turnin, flagged, questie, altoholic, reward or inferred (the first, in that order of trust)",
        "why": "the evidence in words", "seen_t": "first time that source said so, unix s (NULL when unknown)",
        "seen_local": "seen_t, local time"}),
    "gold_moves": ("The purse between two consecutive snapshots (logout or /reload): what it moved and the part "
                   "the records name (quest money). No addon the kit reads saves spending (repairs, vendors, "
                   "training), so other_copper is everything else together.", {
        "t0": "first snapshot, unix s", "t1": "second snapshot, unix s", "time_local": "first snapshot, local",
        "level": "level at the first snapshot", "level_end": "level at the second",
        "money_before": "money at the first, copper", "money_after": "money at the second, copper",
        "delta_copper": "money_after - money_before",
        "quest_copper": "money paid by hand-ins after the first snapshot, up to the second",
        "other_copper": "delta_copper - quest_copper: loot coin, sales and mail in, less repairs, "
                        "purchases, training, flights and mail out",
        "dings": "level-ups strictly between the two snapshots",
        "online_s": "online time (timeline) between the two"}),
    "upgrade_candidates": ("Every gear upgrade the player's own gear advice finds (Polestar's scoring with the "
                           "weights the player chose, items within 3 levels), one row per item and source. Facts "
                           "of the advice, no ranking: join on instance or zone to compare places.", {
        "item_id": "item id", "name": "item name", "quality": "0 poor .. 5 legendary",
        "slot_name": "the slot it would go in", "gain": "score gain over what is worn (Polestar's points)",
        "gain_share_pct": "the gain as a share of the whole worn gear's score, percent",
        "req_level": "level the item (or its source) needs", "now": "1 when wearable at the current level",
        "fills_empty": "1 when the slot is empty now", "with_item_id": "for a weapon: the item it is paired with",
        "source_kind": "where the item comes from, as the gear data names it (today quest, drop or craft; "
                       "open-ended: a reputation, PvP rank or vendor source keeps its own kind); bags or bank "
                       "when the character already holds it there (the shipper's last wow-bags), its only row",
        "instance": "dungeon or raid for an instance drop or quest",
        "instance_min_level": "the instance's lowest level (Group Finder)",
        "instance_max_level": "the instance's highest suggested level",
        "zone": "zone of the drop or the quest", "boss": "the creature that drops it (NULL: trash)",
        "npc_id": "that creature's id", "drop_chance_pct": "drop chance in percent, when known",
        "quest_id": "the quest that rewards it", "quest_name": "its title", "quest_choices": "rewards to choose from",
        "profession": "the profession that makes it", "skill": "the skill it needs",
        "can_make": "1 when the character's rank is enough",
        "bis_pct": "the item's score as a percent of the best the slot can hold at this level for the scored spec "
                   "(100: the best; NULL when there is nothing to measure against)",
        "bis_pct_worn": "the same percent for what is worn in that slot now (NULL: the slot is empty)",
        "fit": "how the item fits the scored spec: yes, any, spec (built for another spec of the class), class "
               "(made for another class), unknown",
        "source_text": "this source in the gear advice's words, as the gear tool says it",
        "source_wowhead": "the source's Wowhead page (the quest, the boss, the craft's spell), when it has one",
        "slot": "the inventory slot 1-19 the gain is measured in (gear.slot): which ring or trinket it replaces"}),
    "agg_session": ("Neutral sums per login span, from timeline, records and positions.", {}),
    "agg_level": ("Neutral sums per level, from timeline, records and positions.", {}),
    "agg_zone": ("Neutral sums per zone, from timeline, records and positions.", {}),
    "agg_hour": ("Neutral sums per local hour of day, from timeline, records and positions.", {}),
}
AGG_COLS = {
    "online_s": "sum of timeline.dur_s", "afk_s": "timeline time with afk=1", "combat_s": "with combat=1",
    "dead_s": "with dead=1", "taxi_s": "with taxi=1", "ride_s": "with ride=1", "instance_s": "inside a dungeon run",
    "mounted_s": "with mounted=1", "distance_yd": "sum of positions.dist_yd", "positions": "position records",
    "xp_gained": "sum of xp.xp_gained", "kills": "kill records", "deaths": "death records",
    "turnins": "turnin records", "accepts": "accept records", "loots": "loot records", "casts": "cast records",
    "crafts": "craft records", "records": "records (positions excluded)"}
for _t in ("agg_session", "agg_level", "agg_zone", "agg_hour"):
    key = {"agg_session": "session_id", "agg_level": "level", "agg_zone": "zone", "agg_hour": "hour_local"}[_t]
    DICTIONARY[_t][1].update({key: "group key", **AGG_COLS})

PASSIVE_LAST = {"session", "xp", "pos", "unit", "unitx", "spawn"}
# Fields copied into a column of `records`; the rest goes into `extra`.
COLUMN_OF = {"quest_id": "quest_id", "quest_name": "quest_name", "npc_id": "npc_id", "npc_kind": "npc_kind",
             "npc_level": "npc_level", "item_id": "item_id", "spell_id": "spell_id", "xp_reward": "xp_reward",
             "money_copper": "money_copper", "guide": "guide", "step": "step", "reason": "reason", "count": "count",
             "map_id": "map_id", "x": "x", "y": "y", "zone": "zone"}
INT_COLS = {"quest_id", "npc_id", "npc_level", "item_id", "spell_id", "xp_reward", "money_copper", "step", "count",
            "map_id"}


def _int(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if math.isfinite(f) else None


def _flt(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _on(v):
    s = str(v).lower()
    return 1 if s in ("true", "1") else 0 if s in ("false", "0") else None


def _local(t: float) -> datetime:
    return tb.local(t, TZ)


def _iso(t: float, tz=UTC) -> str:
    return datetime.fromtimestamp(t, tz).isoformat(timespec="seconds")


def norm(c: str) -> str:
    return (c or "").strip().replace(" ", "-")


# Rows per VictoriaLogs request for the analysis' read of one character's
# whole history, when the event cache can't answer it (stores.Store.vl
# answers a one-character query from wowstats.eventcache, read in pages of
# this size, stores.VL_PAGE): small pages keep each request's memory on the
# server small (a VictoriaLogs with a 1 GiB limit was OOM-killed, 2026-11-18).
def query(store, stream: str, char: str) -> list[dict]:
    """Every event of the character the analysis reads (other players, auction
    scans and raw combat log lines are left out), one character and one
    small page at a time."""
    names = sorted({char, char.replace("-", " ")})
    cf = "char:in(" + ", ".join(json.dumps(n) for n in names) + ")"
    return store.vl(f"{stream} {cf} -feed:wow-player -feed:wow-prices -feed:wow-combatlog", limit=VL_PAGE)


class _Frames:
    """Map sizes in yards (map records first, then the KB's ui_map frames)."""

    def __init__(self, extra: dict | None = None):
        self.size: dict[int, tuple[float, float]] = dict(extra or {})

    def add(self, r: dict) -> None:
        m = _int(r.get("map_id"))
        vals = [_flt(r.get(k)) for k in ("left", "right", "top", "bottom")]
        if m is not None and None not in vals:
            left, right, top, bottom = vals
            self.size[m] = (abs(left - right), abs(top - bottom))

    def dist(self, m, a: tuple, b: tuple):
        s = self.size.get(m)
        if not s or None in a or None in b:
            return None
        return math.hypot((a[0] - b[0]) * s[0] / 100.0, (a[1] - b[1]) * s[1] / 100.0)


# Fields the store or a previous build adds to a line, not the game.
_STORE_FIELDS = ("_stream", "_stream_id", "_t")


def input_mark(rows: list[dict]) -> str:
    """Which source events a build read, whatever their order or their
    copies (rows already one per record: dedupe): each event's identity
    (stores.record_id), its observation time and its revision. Nothing
    prep, the gear scoring or a setting derives goes in, so a definition
    change never moves it; a new event, a later observation or a grown
    record always does. A field the shipper computes from what one run saw
    (a zone's duration_s) doesn't: the same play read in other runs is the
    same input. analysis.data_mark reads it (review 6: the old mark counted
    the prepared tables' rows, and a WEIGHTS change that moved the upgrade
    candidates read as new play)."""
    one = sorted(json.dumps([record_id({k: v for k, v in r.items() if k not in _STORE_FIELDS}),
                             str(r.get("_time")), str(r.get("rev") or "")]) for r in rows)
    return hashlib.sha256("\n".join(one).encode()).hexdigest()[:16]


def build(rows: list[dict], char: str, route=None, map_sizes: dict | None = None,
          now: datetime | None = None, advice: dict | None = None) -> sqlite3.Connection:
    """The database for one character. `route` is wowstats.mcp.route's
    Reference (or None); `map_sizes` {uiMapID: (width_yd, height_yd)} from
    the KB when the records lack a map's frame; `advice` the gear advice
    with every upgrade (wowstats.gear.advise_rows): its `upgrades_all` for
    upgrade_candidates, its spec and slots for the worn items' fit."""
    char = norm(char)
    rows = [r for r in dedupe(rows) if norm(r.get("char", "")) == char]
    mark = input_mark(rows)
    for r in rows:
        r["_t"] = parse_t(r.get("_time")).timestamp()
    rows.sort(key=lambda r: (r["_t"], *record_order(r)))
    rec = [r for r in rows if r.get("feed") == "wow-recorder"]
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("PRAGMA foreign_keys=OFF")
    _schema(db)

    frames = _Frames(map_sizes)
    for r in rec:
        if r.get("kind") == "map":
            frames.add(r)

    # Levels over time: dings, and the level the session records carry.
    marks = sorted((r["_t"], lv, r.get("kind")) for r in rec if r.get("kind") in ("level", "session")
                   and (lv := _int(r.get("level"))))
    mark_t = [m[0] for m in marks]

    def level_at(t: float):
        # The level written at or before t: a ding at t already holds at t.
        i = tb.at_or_before(mark_t, t)
        if i >= 0:
            return marks[i][1]
        if not marks:
            return None
        # Before the first mark: a ding to N means the character was N - 1
        # until then (records that start mid-level, with no session record).
        return marks[0][1] - 1 if marks[0][2] == "level" else marks[0][1]

    # Sessions: split at silences over OFFLINE_S.
    sess_of: list[int] = []
    sid, prev = 0, None
    for r in rec:
        if tb.ends_session(prev, r["_t"], OFFLINE_S):
            sid += 1
        sess_of.append(sid)
        prev = r["_t"]

    # A zone record's time: to the next zone record of its session, else the
    # session's last record (stores.zone_time, the pages' rule), never the
    # shipper's duration_s, which depended on how the chunks arrived.
    bounds: dict[int, list[float]] = {}
    for r, s_id in zip(rec, sess_of, strict=True):
        b = bounds.setdefault(s_id, [r["_t"], r["_t"]])
        b[1] = r["_t"]
    zone_secs = {(z.get("source_id"), z.get("_time")): z["secs"]
                 for z in zone_time([r for r in rec if r.get("kind") == "zone"], [tuple(b) for b in bounds.values()])}
    # A taxi record's time: from its land record (stores.taxi_time, the
    # page's flights), never the shipper's duration_s, which it set only when
    # the land record came in the same run.
    taxi_secs = {(f.get("source_id"), f.get("_time")): f["secs"] for f in taxi_time(rec)}

    # Dungeon runs (Polestar's instanceRuns).
    runs = []
    for r in rows:
        if r.get("feed") == "wow-dungeon":
            t0 = r["_t"]
            left = parse_t(r.get("left")).timestamp() if r.get("left") else None
            secs = _flt(r.get("seconds"))
            t1 = left if left and left > t0 else (t0 + secs if secs else None)
            runs.append((t0, t1, r.get("name"), _int(r.get("dungeon_id")), _int(r.get("level")), secs,
                         _int(r.get("xp"))))
    runs.sort(key=lambda x: x[0])
    db.executemany("INSERT INTO dungeon_runs VALUES (?,?,?,?,?,?,?)",
                   [(n, d, lv, t0, t1, s, xp) for t0, t1, n, d, lv, s, xp in runs])

    def instance_at(t: float):
        for t0, t1, name, *_ in runs:
            if t1 and tb.within(t, t0, t1):
                return name
        return None

    # Walk the records once: state, tables.
    st = {"zone": None, "subzone": None, "map_id": None, "guide": None, "step": None, "afk": 0, "combat": 0,
          "dead": 0, "taxi": 0, "ride": 0, "mounted": 0, "speed": None, "pos_t": None}
    open_spans: dict[str, tuple[float, str | None]] = {}
    last_pos: dict | None = None
    rec_rows, pos_rows, loot_rows, xp_rows, tl_rows, span_rows = [], [], [], [], [], []

    def close(kind: str, t: float, sid_: int):
        o = open_spans.pop(kind, None)
        if o:
            span_rows.append((kind, o[0], t, t - o[0], o[2], o[1]))

    def opn(kind: str, t: float, detail, sid_: int):
        if kind not in open_spans:
            open_spans[kind] = (t, detail, sid_)

    for i, r in enumerate(rec):
        t, k, s_id = r["_t"], r.get("kind") or "", sess_of[i]
        if i and s_id != sess_of[i - 1]:
            # Logged out: what doesn't survive a logout ends at the last record.
            tlast = rec[i - 1]["_t"]
            for kk in list(open_spans):
                if kk != "dead":
                    close(kk, tlast, sess_of[i - 1])
            for f in ("afk", "combat", "taxi", "ride"):
                st[f] = 0
            last_pos = None
        if r.get("zone") and k != "map":
            st["zone"] = r.get("zone")
        if k == "zone":
            st["subzone"] = r.get("subzone") or None
            d = zone_secs.get((r.get("source_id"), r.get("_time")))
            if d:
                span_rows.append(("zone", t, t + d, d, s_id, f"{r.get('zone') or ''}|{r.get('subzone') or ''}"))
        m = _int(r.get("map_id"))
        if m is not None and k not in ("map", "poi"):
            st["map_id"] = m
        lv = level_at(t)
        if k == "pos":
            x, y, spd = _flt(r.get("x")), _flt(r.get("y")), _flt(r.get("speed"))
            flags = _int(r.get("flags")) or 0
            dt = dist = None
            if last_pos is not None:
                dt = t - last_pos["t"]
                if last_pos["m"] == m and m is not None:
                    dist = frames.dist(m, (x, y), (last_pos["x"], last_pos["y"]))
            pos_rows.append((i, t, _iso(t, TZ), s_id, lv, m, r.get("zone"), x, y, spd, flags, dt, dist))
            last_pos = {"t": t, "m": m, "x": x, "y": y}
            st["speed"], st["mounted"], st["pos_t"] = spd, flags & 1, t
        else:
            cols = {c: None for c in COLUMN_OF.values()}
            extra = {}
            for f, v in r.items():
                if f in COMMON or f in DROP or f.startswith("_"):
                    continue
                if f in COLUMN_OF:
                    cols[COLUMN_OF[f]] = _int(v) if COLUMN_OF[f] in INT_COLS else (
                        _flt(v) if f in ("x", "y") else v)
                elif f == "item" and k == "turnin":
                    cols["item_id"] = _int(v)
                elif f == "level" and k == "kill":
                    cols["npc_level"] = _int(v)
                elif f in ("duration_s", "seconds", "on", "level"):
                    continue
                else:
                    extra[f] = v
            secs = zone_secs.get((r.get("source_id"), r.get("_time"))) if k == "zone" else \
                taxi_secs.get((r.get("source_id"), r.get("_time"))) if k == "taxi" else (
                _flt(r.get("duration_s")) if r.get("duration_s") not in (None, "") else _flt(r.get("seconds")))
            lt = _local(t)
            rec_rows.append((i, t, _iso(t), lt.isoformat(timespec="seconds"), lt.hour, lt.strftime("%a"), s_id, lv,
                             k, cols["zone"] or st["zone"], st["subzone"], cols["map_id"], cols["x"], cols["y"],
                             cols["quest_id"], cols["quest_name"], cols["npc_id"], cols["npc_kind"],
                             cols["npc_level"], cols["item_id"], cols["spell_id"], cols["xp_reward"],
                             cols["money_copper"], _on(r.get("on")), cols["guide"], cols["step"], cols["reason"],
                             secs, cols["count"], json.dumps(extra, ensure_ascii=False) if extra else None))
            if k == "loot":
                for part in str(r.get("items") or "").split(";"):
                    a, _, b = part.partition(":")
                    if a.isdigit():
                        loot_rows.append((i, t, int(a), _int(b) or 1))
            elif k == "xp":
                xp_rows.append((t, s_id, lv, st["zone"], lt.hour, _int(r.get("xp")), _int(r.get("xp_max")),
                                _int(r.get("xp_gained")), _int(r.get("rested_xp"))))
            elif k == "step":
                st["guide"], st["step"] = r.get("guide") or None, _int(r.get("step"))
            on = _on(r.get("on"))
            if k in ("afk", "combat", "ride", "escort"):
                if k != "escort":
                    st[k] = 1 if on else 0
                if on:
                    opn(k, t, r.get("quest_id") if k == "escort" else None, s_id)
                else:
                    close(k, t, s_id)
            elif k == "death":
                st["dead"] = 1
                opn("dead", t, None, s_id)
            elif k == "revive":
                st["dead"] = 0
                o = open_spans.get("dead")
                if o:
                    open_spans["dead"] = (o[0], r.get("how"), o[2])
                close("dead", t, s_id)
            elif k == "taxi":
                st["taxi"] = 1
                opn("taxi", t, f"{r.get('from_node') or ''}>{r.get('to_node') or ''}", s_id)
            elif k == "land":
                st["taxi"] = 0
                close("taxi", t, s_id)
            elif k == "rest":
                d = _flt(r.get("seconds"))
                if d:
                    span_rows.append(("rest", t - d, t, d, s_id, r.get("rest_kind")))
        # The interval from this record to the next, inside the session.
        if i + 1 < len(rec) and sess_of[i + 1] == s_id:
            t1 = rec[i + 1]["_t"]
            if t1 > t:
                tl_rows.append((s_id, t, t1, t1 - t, _local(t).isoformat(timespec="seconds"), _local(t).hour, lv,
                                st["zone"], st["subzone"], st["map_id"], st["guide"], st["step"], instance_at(t),
                                st["afk"], st["combat"], st["dead"], st["taxi"], st["ride"], st["mounted"],
                                st["speed"], (t - st["pos_t"]) if st["pos_t"] is not None else None, k))
    if rec:
        for kk in list(open_spans):
            close(kk, rec[-1]["_t"], sess_of[-1])
    for t0, t1, name, *_ in runs:
        if t1:
            span_rows.append(("instance", t0, t1, t1 - t0, None, name))

    db.executemany("INSERT INTO records VALUES (" + ",".join("?" * 30) + ",NULL)", rec_rows)
    db.executemany("INSERT INTO positions VALUES (" + ",".join("?" * 13) + ")", pos_rows)
    db.executemany("INSERT INTO loot_items VALUES (?,?,?,?)", loot_rows)
    db.executemany("INSERT INTO xp VALUES (?,?,?,?,?,?,?,?,?)", xp_rows)
    db.executemany("INSERT INTO timeline (session_id,t0,t1,dur_s,time_local,hour_local,level,zone,subzone,map_id,"
                   "guide,step,instance,afk,combat,dead,taxi,ride,mounted,speed,since_pos_s,opened_by) VALUES ("
                   + ",".join("?" * 22) + ")", tl_rows)
    db.executemany("INSERT INTO spans VALUES (?,?,?,?,?,?)", span_rows)
    db.execute("UPDATE spans SET session_id = (SELECT r.session_id FROM records r WHERE r.t <= spans.t0 "
               "ORDER BY r.t DESC LIMIT 1) WHERE session_id IS NULL")

    _sessions(db)
    _levels(db, rows, rec, route)
    _route_steps(db, route)
    _gear(db, rows)
    _dungeon_trips(db, rows)
    _snapshots(db, rows)
    _route_position(db)
    _quests(db, questsmod.explain(rows))
    _gold_moves(db)
    _upgrades(db, (advice or {}).get("upgrades_all") or [])
    _gear_fit(db, advice)
    _meta(db, char, rows, rec, now, mark)
    _aggregates(db)
    _dictionary(db)
    db.commit()
    return db


def _schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE record_kinds (kind TEXT PRIMARY KEY, meaning TEXT, n INTEGER);
    CREATE TABLE records (rec_id INTEGER PRIMARY KEY, t REAL, time_utc TEXT, time_local TEXT, hour_local INTEGER,
        weekday_local TEXT, session_id INTEGER, level INTEGER, kind TEXT, zone TEXT, subzone TEXT, map_id INTEGER,
        x REAL, y REAL, quest_id INTEGER, quest_name TEXT, npc_id INTEGER, npc_kind TEXT, npc_level INTEGER,
        item_id INTEGER, spell_id INTEGER, xp_reward INTEGER, money_copper INTEGER, on_flag INTEGER, guide TEXT,
        step INTEGER, reason TEXT, seconds REAL, count INTEGER, extra TEXT, dungeon_trip_id INTEGER);
    CREATE INDEX records_kind ON records(kind, t);
    CREATE INDEX records_t ON records(t);
    CREATE TABLE positions (rec_id INTEGER PRIMARY KEY, t REAL, time_local TEXT, session_id INTEGER,
        level INTEGER, map_id INTEGER, zone TEXT, x REAL, y REAL, speed REAL, flags INTEGER, dt_s REAL,
        dist_yd REAL);
    CREATE INDEX positions_t ON positions(t);
    CREATE TABLE loot_items (rec_id INTEGER, t REAL, item_id INTEGER, count INTEGER);
    CREATE TABLE xp (t REAL, session_id INTEGER, level INTEGER, zone TEXT, hour_local INTEGER, xp INTEGER,
        xp_max INTEGER, xp_gained INTEGER, rested_xp INTEGER);
    CREATE TABLE timeline (seg_id INTEGER PRIMARY KEY, session_id INTEGER, t0 REAL, t1 REAL, dur_s REAL,
        time_local TEXT, hour_local INTEGER, level INTEGER, zone TEXT, subzone TEXT, map_id INTEGER, guide TEXT,
        step INTEGER, instance TEXT, afk INTEGER, combat INTEGER, dead INTEGER, taxi INTEGER, ride INTEGER,
        mounted INTEGER, speed REAL, since_pos_s REAL, opened_by TEXT, dungeon_trip_id INTEGER);
    CREATE INDEX timeline_t ON timeline(t0);
    CREATE TABLE spans (kind TEXT, t0 REAL, t1 REAL, dur_s REAL, session_id INTEGER, detail TEXT);
    CREATE TABLE sessions (session_id INTEGER PRIMARY KEY, start_t REAL, end_t REAL, start_local TEXT,
        end_local TEXT, weekday_local TEXT, start_hour_local INTEGER, duration_s REAL, records INTEGER,
        level_start INTEGER, level_end INTEGER, xp_gained INTEGER, kills INTEGER, deaths INTEGER, turnins INTEGER,
        distance_yd REAL, first_zone TEXT, last_zone TEXT, last_subzone TEXT, last_kind TEXT, gap_before_s REAL);
    CREATE TABLE levels (level INTEGER PRIMARY KEY, ding_t REAL, played_s REAL, xp INTEGER, quests INTEGER,
        kills INTEGER, partial INTEGER, planned_s REAL, planned_median_s REAL, planned_guide TEXT,
        calendar_s REAL, offline_s REAL, longest_gap_s REAL, longest_gap_from_local TEXT,
        longest_gap_to_local TEXT, recorder_missed_s REAL, in_play INTEGER, climb_kills INTEGER,
        climb_quests INTEGER);
    CREATE TABLE route_steps (guide TEXT, guide_title TEXT, step INTEGER, planned_s REAL, title TEXT);
    CREATE TABLE route_position (guide TEXT, guide_title TEXT, shown_step INTEGER, shown_title TEXT, reason TEXT,
        furthest_step INTEGER, next_step INTEGER, next_title TEXT, next_why TEXT, subzone TEXT, zone TEXT);
    CREATE TABLE dungeon_runs (name TEXT, dungeon_id INTEGER, level INTEGER, start_t REAL, left_t REAL,
        seconds REAL, xp INTEGER);
    CREATE TABLE dungeon_trips (trip_id INTEGER PRIMARY KEY, dungeon TEXT, dungeon_id INTEGER, level_start INTEGER,
        start_t REAL, end_t REAL, start_local TEXT, end_local TEXT, bracket TEXT, pickup_s REAL, travel_to_s REAL,
        inside_s REAL, travel_back_s REAL, handin_s REAL, online_s REAL, afk_s REAL, xp_kills_inside INTEGER,
        xp_inside INTEGER, xp_quests INTEGER, quest_ids TEXT, quests_handed_in INTEGER, loot_inside TEXT,
        loot_inside_n INTEGER, rewards TEXT, still_worn TEXT, quest_money_copper INTEGER,
        gold_before_copper INTEGER, gold_after_copper INTEGER, steps TEXT);
    CREATE TABLE gear (slot INTEGER, slot_name TEXT, item_id INTEGER, name TEXT, quality INTEGER,
        item_level INTEGER, req_level INTEGER, sheet_t REAL, turnin_quest_id INTEGER, turnin_t REAL, loot_t REAL,
        craft_t REAL, vendor_seen INTEGER, first_t REAL, first_level INTEGER, scored_for TEXT, score REAL,
        bis_pct REAL, fit TEXT, built_for TEXT);
    CREATE TABLE gear_sheets (sheet_t REAL, time_local TEXT, level INTEGER, slot INTEGER, slot_name TEXT,
        item_id INTEGER, name TEXT, quality INTEGER, item_level INTEGER, req_level INTEGER);
    CREATE TABLE professions (skill_id INTEGER, name TEXT, rank INTEGER, max_rank INTEGER, max_rank_from TEXT);
    CREATE TABLE snapshots (t REAL, time_local TEXT, level INTEGER, xp INTEGER, xp_max INTEGER,
        money_copper INTEGER, zone TEXT, bind TEXT, resting INTEGER, online_s_since_prev REAL);
    CREATE TABLE upgrade_candidates (item_id INTEGER, name TEXT, quality INTEGER, slot_name TEXT, gain REAL,
        gain_share_pct REAL, req_level INTEGER, now INTEGER, fills_empty INTEGER, with_item_id INTEGER,
        source_kind TEXT, instance TEXT, instance_min_level INTEGER, instance_max_level INTEGER, zone TEXT, boss TEXT,
        npc_id INTEGER, drop_chance_pct REAL, quest_id INTEGER, quest_name TEXT, quest_choices INTEGER,
        profession TEXT, skill INTEGER, can_make INTEGER, bis_pct REAL, bis_pct_worn REAL, fit TEXT,
        source_text TEXT, source_wowhead TEXT, slot INTEGER);
    CREATE TABLE quests (quest_row INTEGER PRIMARY KEY, quest_id INTEGER, quest_name TEXT, status TEXT,
        accepted_t REAL, closed_t REAL, accepted_local TEXT, closed_local TEXT, level_accepted INTEGER,
        level INTEGER, zone TEXT, xp INTEGER, money_copper INTEGER, wall_s REAL, open_online_s REAL,
        quest_s REAL, done_why TEXT);
    CREATE TABLE quests_done (quest_id INTEGER PRIMARY KEY, quest_name TEXT, source TEXT, why TEXT, seen_t REAL,
        seen_local TEXT);
    CREATE TABLE gold_moves (t0 REAL, t1 REAL, time_local TEXT, level INTEGER, level_end INTEGER,
        money_before INTEGER, money_after INTEGER, delta_copper INTEGER, quest_copper INTEGER,
        other_copper INTEGER, dings INTEGER, online_s REAL);
    CREATE TABLE dictionary (table_name TEXT, column_name TEXT, description TEXT);
    """)


def _sessions(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT session_id, MIN(t), MAX(t), COUNT(*) FROM (SELECT session_id, t FROM records "
                      "UNION ALL SELECT session_id, t FROM positions) GROUP BY session_id ORDER BY session_id"
                      ).fetchall()
    prev_end = None
    out = []
    for sid, t0, t1, n in rows:
        def one(q, *a):
            r = db.execute(q, a).fetchone()
            return r[0] if r else None
        lv0 = one("SELECT level FROM records WHERE session_id=? AND level IS NOT NULL ORDER BY t LIMIT 1", sid)
        lv1 = one("SELECT level FROM records WHERE session_id=? AND level IS NOT NULL ORDER BY t DESC LIMIT 1", sid)
        last = db.execute("SELECT kind, zone, subzone FROM records WHERE session_id=? AND kind NOT IN "
                          f"({','.join('?' * len(PASSIVE_LAST))}) ORDER BY t DESC LIMIT 1",
                          (sid, *PASSIVE_LAST)).fetchone() or (None, None, None)
        lz = db.execute("SELECT zone, subzone FROM records WHERE session_id=? AND zone IS NOT NULL ORDER BY t DESC "
                        "LIMIT 1", (sid,)).fetchone() or (None, None)
        out.append((sid, t0, t1, _iso(t0, TZ), _iso(t1, TZ), _local(t0).strftime("%a"), _local(t0).hour, t1 - t0, n,
                    lv0, lv1, one("SELECT COALESCE(SUM(xp_gained),0) FROM xp WHERE session_id=?", sid),
                    one("SELECT COUNT(*) FROM records WHERE session_id=? AND kind='kill'", sid),
                    one("SELECT COUNT(*) FROM records WHERE session_id=? AND kind='death'", sid),
                    one("SELECT COUNT(*) FROM records WHERE session_id=? AND kind='turnin'", sid),
                    one("SELECT COALESCE(SUM(dist_yd),0) FROM positions WHERE session_id=?", sid),
                    one("SELECT zone FROM records WHERE session_id=? AND zone IS NOT NULL ORDER BY t LIMIT 1", sid),
                    lz[0], lz[1], last[0], (t0 - prev_end) if prev_end is not None else None))
        prev_end = t1
    db.executemany("INSERT INTO sessions VALUES (" + ",".join("?" * 21) + ")", out)


def _levels(db, rows, rec, route) -> None:
    splits = {lv: r for lv, r in pick_splits([r for r in rows if r.get("feed") == "wow-level"]).items()
              if isinstance(lv, int) and lv}
    dings = {}
    for r in rec:
        if r.get("kind") == "level" and (lv := _int(r.get("level"))):
            dings.setdefault(lv - 1, r["_t"])
    who = _who(rows, rec)
    side, cls = who.get("faction"), who.get("class")
    seen = set(splits) | set(dings)
    cur = db.execute("SELECT MAX(level) FROM records").fetchone()[0]
    if cur:
        seen.add(cur)
    # The level being played: the highest level known, unless the Recorder
    # saw the ding out of it or Polestar closed a whole split for it (a
    # partial split is one still growing). Without the Recorder or a split
    # for it there is no such row: its counts are unknown, never 0.
    top = max(seen, default=None)
    in_play = top if top is not None and top not in dings and (
        top not in splits or truthy(splits[top].get("partial"))) else None
    # The climb's counts, one population per row (pages.climb): Polestar's
    # split counts the whole level, so it wins; a level without one (the
    # level in play, or Polestar off) counts the Recorder's records at it.
    recorded = {lv: (k, q) for lv, k, q in db.execute(
        "SELECT level, COUNT(*) FILTER (WHERE kind='kill'), COUNT(*) FILTER (WHERE kind='turnin') "
        "FROM records WHERE level IS NOT NULL GROUP BY level")}
    out = []
    for lv in sorted(seen):
        s = splits.get(lv) or {}
        plan = med = guide = None
        if route is not None and getattr(route, "available", False):
            # The pages' and the MCP's route time (Route.level), and beside it the median over classes.
            exp = route.level(lv, side, cls) if cls else None
            plan = exp.get("expected_s") if exp else None
            med_row = route.level(lv, side)
            med, guide = (med_row.get("expected_s"), med_row.get("guide")) if med_row else (None, None)
        ding = dings.get(lv) or (s.get("_t") if s else None)
        if s:
            ck, cq = _int(s.get("kills")), _int(s.get("quests"))
        else:
            ck, cq = recorded.get(lv, (None, None))
        out.append((lv, ding, _flt(s.get("seconds")), _int(s.get("xp")), _int(s.get("quests")), _int(s.get("kills")),
                    (1 if truthy(s.get("partial")) else 0) if s else None, plan, med, guide,
                    1 if lv == in_play else 0, ck, cq))
    db.executemany("INSERT INTO levels VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,?,?,?)", out)
    _level_calendar(db, rows)


def _level_calendar(db, rows) -> None:
    """Each level's calendar beside its played time: a level that took three
    weeks with its played time on plan was a break, not slow play (seeded
    evaluation, offline_gap). The window is the previous ding to this one
    (timebounds: half-open), or to the last record for the level in play;
    the silences are the gaps between the sessions table's spans."""
    from wowstats import coverage

    spans = db.execute("SELECT start_t, end_t FROM sessions ORDER BY start_t").fetchall()
    last = db.execute("SELECT MAX(t) FROM records").fetchone()[0]
    missed = {g["level"]: g["off_s"] for g in coverage.recorder_gaps(
        [(a, b) for a, b in spans], [r for r in rows if r.get("feed") == "wow-level"])}
    lv_rows = db.execute("SELECT level, ding_t, played_s, partial FROM levels ORDER BY level").fetchall()
    dings = {lv: d for lv, d, _p, _q in lv_rows}
    first = spans[0][0] if spans else None
    out = []
    for lv, ding, played, partial in lv_rows:
        a = dings.get(lv - 1)
        b = ding if ding is not None else last
        cal = ding - a if a is not None and ding is not None else None
        off = max(0.0, cal - played) if cal is not None and played is not None and partial == 0 else None
        gap = gap_from = gap_to = None
        if a is not None and b is not None and b > a:
            cursor = a
            for s0, s1 in spans:
                if s1 < a or s0 >= b:
                    continue
                if s0 > cursor and (gap is None or s0 - cursor > gap):
                    gap, gap_from, gap_to = s0 - cursor, cursor, s0
                cursor = max(cursor, s1)
            if b > cursor and (gap is None or b - cursor > gap):
                gap, gap_from, gap_to = b - cursor, cursor, b
        # The coverage's own rule (wowstats.coverage.recorder_gaps): measured
        # only for a whole level the Recorder was already on for.
        whole = cal is not None and played is not None and partial == 0 and first is not None and a >= first
        out.append((cal, off, gap, _iso(gap_from, TZ) if gap is not None else None,
                    _iso(gap_to, TZ) if gap is not None else None, float(missed.get(lv, 0)) if whole else None, lv))
    db.executemany("UPDATE levels SET calendar_s=?, offline_s=?, longest_gap_s=?, longest_gap_from_local=?, "
                   "longest_gap_to_local=?, recorder_missed_s=? WHERE level=?", out)


def _route_steps(db, route) -> None:
    if route is None or not getattr(route, "available", False):
        return
    used = {g for (g,) in db.execute("SELECT DISTINCT guide FROM records WHERE kind='step' AND guide IS NOT NULL")}
    out = []
    for g in sorted(used):
        row = route.guides.get(g)
        if not row:
            continue
        for st in row.get("steps") or []:
            out.append((g, row.get("title"), st[0], st[1], st[2] if len(st) > 2 else None))
    db.executemany("INSERT INTO route_steps VALUES (?,?,?,?,?)", out)


def _route_position(db) -> None:
    """Where the player is in Polestar's route: the guide and step Polestar
    shows, the furthest step its pointer reached in that guide (the step he
    was on, not yet done), and the step to do next. Polestar falls back to
    the first unfinished step at login (an open quest from earlier): when it
    shows a step before the furthest one, the next step is the furthest
    instead, or the first step after it named after where he stands (past it
    when that step is the trip there). One rule for the plan, the next_step
    tool and play_sql: a model that read the newest step record answered 45
    where next_step said 66 (eval-mcp, route_next_step)."""
    last = db.execute("SELECT guide, step, reason FROM records WHERE kind='step' AND guide LIKE 'forever/%' "
                      "AND guide NOT LIKE 'forever/professions/%' ORDER BY t DESC, rec_id DESC LIMIT 1").fetchone()
    if not last:
        return
    guide, step, reason = last
    furthest = db.execute("SELECT MAX(step) FROM records WHERE kind='step' AND guide=? AND reason IN "
                          "('next','done','skip','auto')", (guide,)).fetchone()[0] or step or 1
    step = step or furthest
    sub = db.execute("SELECT subzone, zone FROM records WHERE subzone IS NOT NULL OR zone IS NOT NULL ORDER BY t "
                     "DESC, rec_id DESC LIMIT 1").fetchone() or (None, None)
    bind = (db.execute("SELECT bind FROM snapshots ORDER BY t DESC LIMIT 1").fetchone() or (None,))[0]
    titles = {int(n): t or "" for n, t in db.execute("SELECT step, title FROM route_steps WHERE guide=?", (guide,))}
    gtitle = (db.execute("SELECT guide_title FROM route_steps WHERE guide=? LIMIT 1", (guide,)).fetchone()
              or (None,))[0]
    anchor, why = furthest, "the step Polestar's pointer reached last"
    names = list(dict.fromkeys(x for x in (sub[0], bind) if x))
    if step < furthest:
        for n in range(furthest, furthest + 11):
            t = titles.get(n, "")
            if any(x.lower() in t.lower() or t.lower() in x.lower() for x in names if t):
                there = TRAVEL.match(t) and any(x.lower() in t.lower() for x in names) and n + 1 in titles
                anchor = n + 1 if there else n
                why = (f"Polestar fell back to step {step}; you are at {', '.join(names)}, so the plan starts "
                       f"{'after' if there else 'at'} step {n} ({t})")
                break
    db.execute("INSERT INTO route_position VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (guide, gtitle, step, titles.get(step), reason, furthest, anchor, titles.get(anchor), why, *sub))


def _items(g: dict) -> list:
    items = g.get("items")
    try:
        items = json.loads(items) if isinstance(items, str) else (items or [])
    except ValueError:
        items = []
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


def _gear(db, rows) -> None:
    sheets = sorted((r for r in rows if r.get("feed") == "wow-gear"), key=lambda r: r["_t"])
    if not sheets:
        return
    # The shipper can send the same sheet twice (same time): one each.
    sheets = [sh for i, sh in enumerate(sheets) if i == 0 or sh["_t"] != sheets[i - 1]["_t"]]
    hist = []
    for sh in sheets:
        lv = db.execute("SELECT level FROM records WHERE t <= ? AND level IS NOT NULL ORDER BY t DESC LIMIT 1",
                        (sh["_t"],)).fetchone()
        for it in _items(sh):
            hist.append((sh["_t"], _local(sh["_t"]).isoformat(timespec="seconds"), lv[0] if lv else None,
                         _int(it.get("slot")), it.get("slot_name"), _int(it.get("item_id")), it.get("name"),
                         _int(it.get("quality")), _int(it.get("item_level")), _int(it.get("req_level"))))
    db.executemany("INSERT INTO gear_sheets VALUES (?,?,?,?,?,?,?,?,?,?)", hist)
    # The sheet in force, as the page and the MCP pick it (gear.pick_sheet):
    # an exact one before one dated by a commit or the run, and the items
    # of the newest sheet that has any when the newest has none to say.
    g = gear.pick_sheet(sheets)
    out = []
    for it in _items(g):
        iid = _int(it.get("item_id"))
        if iid is None:
            continue
        ti = db.execute("SELECT quest_id, t FROM records WHERE kind='turnin' AND item_id=? ORDER BY t LIMIT 1",
                        (iid,)).fetchone() or (None, None)
        lt = db.execute("SELECT MIN(t) FROM loot_items WHERE item_id=?", (iid,)).fetchone()[0]
        ct = db.execute("SELECT MIN(t) FROM records WHERE kind='craft' AND item_id=?", (iid,)).fetchone()[0]
        vs = db.execute("SELECT COUNT(*) FROM records WHERE kind='vendor' AND (extra LIKE ? OR extra LIKE ?)",
                        (f'%"{iid}:%', f'%;{iid}:%')).fetchone()[0]
        ft = min((x for x in (ti[1], lt, ct) if x is not None), default=None)
        fl = None
        if ft is not None:
            r = db.execute("SELECT level FROM records WHERE t <= ? AND level IS NOT NULL ORDER BY t DESC LIMIT 1",
                           (ft,)).fetchone()
            fl = r[0] if r else None
        out.append((_int(it.get("slot")), it.get("slot_name"), iid, it.get("name"), _int(it.get("quality")),
                    _int(it.get("item_level")), _int(it.get("req_level")), g["_t"], ti[0], ti[1], lt, ct,
                    1 if vs else 0, ft, fl))
    db.executemany("INSERT INTO gear VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL)", out)
    profs = g.get("professions")
    try:
        profs = json.loads(profs) if isinstance(profs, str) else (profs or [])
    except ValueError:
        profs = []
    skills = profcaps.newest_skills([r for r in rows if r.get("feed") == "wow-recorder" and r.get("kind") == "skill"],
                                    "_t")
    db.executemany("INSERT INTO professions VALUES (?,?,?,?,?)",
                   [(_int(p.get("skill_id")), p.get("name"), _int(p.get("rank")), _int(p.get("max_rank")),
                     p.get("max_rank_from"))
                    for p in profcaps.fill([p for p in profs if isinstance(p, dict)], skills)])


def _snapshots(db, rows) -> None:
    snaps = sorted((r for r in rows if r.get("feed") == "wow-character" and r.get("kind") != "played"
                    and _int(r.get("money_copper")) is not None), key=lambda r: r["_t"])
    out, prev = [], None
    for r in snaps:
        if prev is not None and r["_t"] == prev:
            continue
        rest = _on(r.get("resting"))
        out.append((r["_t"], _iso(r["_t"], TZ), _int(r.get("level")), _int(r.get("xp")), _int(r.get("xp_max")),
                    _int(r.get("money_copper")), r.get("zone"), r.get("bind"), rest,
                    _online(db, prev, r["_t"]) if prev is not None else None))
        prev = r["_t"]
    db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", out)


def _quests(db, done: dict[int, dict] | None = None) -> None:
    """Each quest the log held, from its accept to its hand-in or abandon
    (records paired by quest_id, in time order), and the online time it took.

    Time on a quest is `quest_s`: every online second (timeline) while the
    quest was in the log, shared equally among the quests open in that
    second. Wall time from accept to hand-in (`wall_s`) counts the nights in
    between, and online time while open (`open_online_s`) counts the same
    second once per open quest: ten quests in the log would each claim the
    whole evening. Shared, the quests' times add up to the online time with
    at least one quest open, nothing counted twice. It doesn't know which
    quest the player was working on: a quest that sits in the log while
    others get done takes its share of their time.

    A hand-in with no accept before it (accepted before tracking began) is
    `untimed`; an accept while the same quest is already open keeps the
    first; an abandon closes it; a fail closes it as `failed` (the log still
    shows it, but it can't be handed in); what is still in the log is
    `open`, its time counted up to the last record. Objectives seen done
    (a complete record) with no accept before them open the quest too,
    untimed: it was accepted before tracking began and is in the log, ready
    to hand in (`untimed` once handed in).

    `done` is wowstats.quests.explain of the character's events. A quest
    still in the log that it says was handed in some other way (a reward
    only that quest gives, worn or in the bags; Questie's list; ...) was
    handed in while the Recorder was off: it closes as `done (inferred)`,
    with the reason, when that evidence was first seen after the accept."""
    done = done or {}
    from wowstats import forever

    db.executemany("INSERT INTO quests_done VALUES (?,?,?,?,?,?)",
                   [(q, (forever.quest(q) or {}).get("name"), e["from"], e["why"], e["t"],
                     _iso(e["t"], TZ) if e["t"] is not None else None) for q, e in sorted(done.items())])
    evs = db.execute("SELECT t, kind, quest_id, quest_name, level, zone, xp_reward, money_copper FROM records "
                     "WHERE kind IN ('accept', 'turnin', 'abandon', 'complete', 'fail') AND quest_id IS NOT NULL "
                     "ORDER BY t, rec_id").fetchall()
    rows: list[dict] = []
    open_: dict[int, dict] = {}
    closed_at: dict[int, float] = {}
    for t, kind, qid, name, lv, zone, xp, money in evs:
        q = open_.get(qid)
        if kind == "accept":
            if q is None:
                q = {"quest_id": qid, "quest_name": name, "status": "open", "accepted_t": t, "closed_t": None,
                     "level_accepted": lv, "level": lv, "zone": zone, "xp": None, "money_copper": None}
                open_[qid] = q
                rows.append(q)
        elif kind == "complete":
            # Done objectives of a quest whose accept came before tracking (or
            # in a Recorder gap): in the log all the same. Not the complete
            # written with the hand-in that just closed it.
            if q is None and closed_at.get(qid) != t:
                q = {"quest_id": qid, "quest_name": name, "status": "open", "accepted_t": None, "closed_t": None,
                     "level_accepted": None, "level": lv, "zone": zone, "xp": None, "money_copper": None}
                open_[qid] = q
                rows.append(q)
        elif kind == "turnin":
            closed_at[qid] = t
            if q is None:
                rows.append({"quest_id": qid, "quest_name": name, "status": "untimed", "accepted_t": None,
                             "closed_t": t, "level_accepted": None, "level": lv, "zone": zone, "xp": xp,
                             "money_copper": money})
                continue
            q.update(status="done" if q["accepted_t"] is not None else "untimed", closed_t=t, level=lv,
                     zone=zone or q["zone"], xp=xp, money_copper=money, quest_name=q["quest_name"] or name)
            del open_[qid]
        elif q is not None:  # abandon, or failed by the game
            closed_at[qid] = t
            q.update(status="abandoned" if kind == "abandon" else "failed", closed_t=t, level=lv,
                     quest_name=q["quest_name"] or name)
            del open_[qid]
    for qid, q in open_.items():
        e = done.get(qid)
        if questsmod.settles_open(e, q["accepted_t"]):
            first = e["t"] if e["t"] is not None and (q["accepted_t"] is None or e["t"] >= q["accepted_t"]) else None
            q.update(status=questsmod.INFERRED, closed_t=first, done_why=e["why"])
    # The time: one sweep over the timeline, quests opened as their accept passes.
    timed = sorted((q for q in rows if q["accepted_t"] is not None), key=lambda q: q["accepted_t"])
    for q in timed:
        q["open_online_s"] = q["quest_s"] = 0.0
    active: list[dict] = []
    i = 0
    for t0, dur in db.execute("SELECT t0, dur_s FROM timeline ORDER BY t0"):
        while i < len(timed) and timed[i]["accepted_t"] <= t0:
            active.append(timed[i])
            i += 1
        active = [q for q in active if tb.within(t0, q["accepted_t"], q["closed_t"])]
        if active and dur:
            share = dur / len(active)
            for q in active:
                q["open_online_s"] += dur
                q["quest_s"] += share
    out = []
    for n, q in enumerate(rows, 1):
        a, c = q["accepted_t"], q["closed_t"]
        out.append((n, q["quest_id"], q["quest_name"], q["status"], a, c, _iso(a, TZ) if a else None,
                    _iso(c, TZ) if c else None, q["level_accepted"], q["level"], q["zone"], q["xp"],
                    q["money_copper"], (c - a) if a is not None and c is not None else None,
                    q.get("open_online_s"), q.get("quest_s"), q.get("done_why")))
    db.executemany("INSERT INTO quests VALUES (" + ",".join("?" * 17) + ")", out)


def _gold_moves(db) -> None:
    """The purse between two logout snapshots: what it moved, the part the
    records name (quest money handed in after the first and up to the
    second), and the rest. Nothing the addons save records spending, so the
    rest is never called repairs: it is loot coin, vendor and auction sales
    and mail in, less repairs, purchases, training, flights and mail out."""
    snaps = db.execute("SELECT t, level, money_copper FROM snapshots ORDER BY t").fetchall()
    out = []
    for (t0, lv0, m0), (t1, lv1, m1) in zip(snaps, snaps[1:], strict=False):
        quest = db.execute("SELECT COALESCE(SUM(money_copper), 0) FROM records WHERE kind = 'turnin' AND t > ? "
                           "AND t <= ?", (t0, t1)).fetchone()[0]
        dings = db.execute("SELECT COUNT(*) FROM records WHERE kind = 'level' AND t > ? AND t < ?",
                           (t0, t1)).fetchone()[0]
        out.append((t0, t1, _iso(t0, TZ), lv0, lv1, m0, m1, m1 - m0, quest, m1 - m0 - quest, dings,
                    _online(db, t0, t1)))
    db.executemany("INSERT INTO gold_moves VALUES (" + ",".join("?" * 12) + ")", out)


def _upgrades(db, ups: list[dict]) -> None:
    out = []
    for u in ups:
        base = (_int(u.get("item_id")), u.get("name"), _int(u.get("quality")), u.get("slot_name"), _flt(u.get("gain")),
                _flt(u.get("gain_share_pct")), _int(u.get("level")), 1 if u.get("now") else 0,
                1 if u.get("fills_empty") else 0, _int((u.get("with") or {}).get("item_id")))
        badge = (_flt(u.get("bis_pct")), _flt(u.get("bis_pct_worn")), (u.get("fit") or {}).get("for_you"))
        for s in u.get("sources") or []:
            lv = s.get("levels") or [None, None]
            out.append(base + (s.get("kind"), s.get("zone") if (s.get("instance") or s.get("dungeon")) else None,
                               _int(lv[0]), _int(lv[1]), s.get("zone"), s.get("boss"), _int(s.get("npc_id")),
                               _flt(s.get("chance_pct")), _int(s.get("quest_id")), s.get("name"),
                               _int(s.get("choices")), s.get("profession"), _int(s.get("skill")),
                               None if s.get("can_make") is None else int(bool(s.get("can_make"))))
                       + badge + (gear.source_text(s), gear.source_link(s), _int(u.get("slot"))))
    db.executemany("INSERT INTO upgrade_candidates VALUES (" + ",".join("?" * 30) + ")", out)


def advice_db(advice: dict, char: str, cls: str | None, upgrades: list[dict]) -> sqlite3.Connection:
    """The gear advice alone as a prep build holds it: the worn items (gear,
    scored by _gear_fit), `upgrades` (upgrade_candidates, one row per item
    and source) and the character's name and class (meta), every other
    table empty. For a tool that scores the gear live, with its own inputs
    (the MCP's gear: the level, zone, quests done, bags, auction house), and
    reads its numbers through the `worn` and `upgrades` models (semantic.local)."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    _schema(db)
    worn = [(_int(sl.get("slot")), sl.get("slot_name"), _int(cur.get("item_id")), cur.get("name"),
             _int(cur.get("quality"))) for sl in advice.get("slots") or [] for cur in [sl.get("current") or {}]
            if cur.get("item_id")]
    db.executemany("INSERT INTO gear (slot, slot_name, item_id, name, quality) VALUES (?,?,?,?,?)", worn)
    _upgrades(db, upgrades)
    _gear_fit(db, advice)
    db.executemany("INSERT INTO meta VALUES (?,?)", [("char", norm(char)), ("class", cls)])
    db.commit()
    return db


def _gear_fit(db, advice: dict | None) -> None:
    """The worn items as the gear advice scores them: for which spec, how
    many points, their share of the slot's best, and how they fit the spec
    (wowstats.gearfit). Without it a Shadow circlet worn by a Holy priest
    reads as any other head (seeded evaluation, wrong_spec_gear)."""
    if not advice:
        return
    spec = advice.get("spec") or {}
    label = spec.get("label") or spec.get("key")
    out = []
    for sl in advice.get("slots") or []:
        cur = sl.get("current") or {}
        if not cur.get("item_id"):
            continue
        f = cur.get("fit") or {}
        st = sl.get("standing") or {}
        out.append((label, _flt(cur.get("score")) if cur.get("known") else None, _flt(st.get("pct")),
                    f.get("for_you"), f.get("built_for"), _int(sl.get("slot")), _int(cur.get("item_id"))))
    db.executemany("UPDATE gear SET scored_for=?, score=?, bis_pct=?, fit=?, built_for=? WHERE slot=? AND item_id=?",
                   out)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _online(db, t0: float, t1: float) -> float:
    if t1 <= t0:
        return 0.0
    return db.execute("SELECT COALESCE(SUM(MIN(t1, ?) - MAX(t0, ?)), 0) FROM timeline WHERE t1 > ? AND t0 < ?",
                      (t1, t0, t0, t1)).fetchone()[0]


def _dungeon_trips(db, rows) -> None:
    """dungeon_trips: one row per dungeon run, widened to the Polestar lookup
    guides' pickup and hand-in steps around it. Observable facts only."""
    runs = db.execute("SELECT name, dungeon_id, level, start_t, COALESCE(left_t, start_t + COALESCE(seconds, 0)), "
                      "xp FROM dungeon_runs ORDER BY start_t").fetchall()
    if not runs:
        return
    recs = db.execute("SELECT rec_id, t, kind, guide, step, quest_id, xp_reward, item_id, money_copper, reason "
                      "FROM records ORDER BY t, rec_id").fetchall()
    steps = [(r[1], r[3], r[4], r[9]) for r in recs if r[2] == "step" and r[3]]
    snaps = sorted((r["_t"], _int(r.get("money_copper"))) for r in rows
                   if r.get("feed") == "wow-character" and _int(r.get("money_copper")) is not None)
    worn = {r[0] for r in db.execute("SELECT item_id FROM gear WHERE item_id IS NOT NULL")}

    def chain(ts: list[float], anchor: float, backwards: bool) -> list[float]:
        out, edge = [], anchor
        for t in sorted(ts, reverse=backwards):
            if abs(t - edge) > TRIP_GAP_S:
                break
            out.append(t)
            edge = t
        return sorted(out)

    trips = []
    for i, (name, did, lv, r0, r1, xp_in) in enumerate(runs):
        s = slug(name)
        prev_end = max((x[4] for x in runs[:i] if slug(x[0]) == s), default=float("-inf"))
        next_start = min((x[3] for x in runs[i + 1:]), default=float("inf"))
        # A pickup up to the entry, the entry's second included; a hand-in from
        # the exit up to the next run, [r1, next_start): a step on an edge
        # belongs to one trip only.
        pick_t = chain([t for t, g, *_ in steps if g == f"lookup/pickup/{s}" and prev_end < t <= r0], r0, True)
        hand_t = chain([t for t, g, *_ in steps
                        if g == f"lookup/handin/{s}" and tb.within(t, r1, next_start)], r1, False)
        start = pick_t[0] if pick_t else r0
        end = hand_t[-1] if hand_t else r1
        used = [f"{g}#{st if st is not None else ''}" for t, g, st, _ in steps
                if g in (f"lookup/pickup/{s}", f"lookup/handin/{s}") and start <= t <= end]
        # The trip's quests: worked on inside (objective progress, completion,
        # a hand-in), or accepted as a pickup guide step advanced (within 3 s).
        auto_t = [t for t, g, st, why in steps if g == f"lookup/pickup/{s}" and why in ("auto", "done")
                  and start <= t <= r0]
        qset = set()
        for _, t, kind, _, _, qid, *_ in recs:
            if qid is not None and ((r0 <= t <= r1 and kind in ("progress", "complete", "objectives", "turnin"))
                                    or (kind == "accept" and start <= t <= r0
                                        and any(0 <= a - t <= 3 for a in auto_t))):
                qset.add(qid)
        rewards, money, xp_q, handed = [], 0, 0, set()
        acc_last = hand_first = None
        for _, t, kind, _, _, qid, xpr, iid, cop, _ in recs:
            if qid not in qset or not start <= t <= end:
                continue
            if kind == "accept" and t <= r0:
                acc_last = t
            elif kind == "turnin" and qid not in handed:
                handed.add(qid)
                xp_q += xpr or 0
                money += cop or 0
                if iid:
                    rewards.append(iid)
                if t >= r1 and hand_first is None:
                    hand_first = t
        pick_end = min(acc_last if acc_last is not None else (pick_t[-1] if pick_t else start), r0)
        back_end = hand_first if hand_first is not None else (hand_t[0] if hand_t else r1)
        back_end = max(back_end, r1)
        loot = db.execute("SELECT item_id, SUM(count) FROM loot_items WHERE t >= ? AND t <= ? GROUP BY item_id "
                          "ORDER BY item_id", (r0, r1)).fetchall()
        xk = db.execute("SELECT COALESCE(SUM(xp_reward), 0) FROM records WHERE kind = 'kill' AND t >= ? AND t <= ?",
                        (r0, r1)).fetchone()[0]
        afk = db.execute("SELECT COALESCE(SUM(MIN(t1, ?) - MAX(t0, ?)), 0) FROM timeline WHERE afk = 1 AND "
                         "t1 > ? AND t0 < ?", (end, start, start, end)).fetchone()[0]
        lvs = db.execute("SELECT level FROM records WHERE t <= ? AND level IS NOT NULL ORDER BY t DESC LIMIT 1",
                         (start,)).fetchone()
        before = [m for t, m in snaps if t <= start]
        after = [m for t, m in snaps if t >= end]
        items = [x[0] for x in loot] + rewards
        trips.append((i + 1, name, did, lvs[0] if lvs else lv, start, end,
                      _local(start).isoformat(timespec="seconds"), _local(end).isoformat(timespec="seconds"),
                      "guides" if pick_t or hand_t else "instance", _online(db, start, pick_end),
                      _online(db, pick_end, r0), _online(db, r0, r1), _online(db, r1, back_end),
                      _online(db, back_end, end), _online(db, start, end), afk, xk, xp_in, xp_q,
                      json.dumps(sorted(qset)), len(handed), json.dumps([list(x) for x in loot]),
                      sum(x[1] for x in loot), json.dumps(rewards),
                      json.dumps(sorted({x for x in items if x in worn})), money,
                      before[-1] if before else None, after[0] if after else None, json.dumps(used)))
    db.executemany("INSERT INTO dungeon_trips VALUES (" + ",".join("?" * 29) + ")", trips)
    for x in trips:
        db.execute("UPDATE records SET dungeon_trip_id = ? WHERE t >= ? AND t <= ?", (x[0], x[4], x[5]))
        db.execute("UPDATE timeline SET dungeon_trip_id = ? WHERE t0 >= ? AND t0 < ?", (x[0], x[4], x[5]))


def _who(rows, rec) -> dict:
    """The character's class, race and side: its latest Recorder session
    record. A character the recorder never saw (its folder, Syndicator,
    Polestar's alts only) still has them in its snapshots and gear sheet:
    without them the dungeons list mixed both sides' cities, and the route
    planner's time per level fell back to the median over classes."""
    who = next((r for r in reversed(rec) if r.get("kind") == "session"), {})
    known = sorted((r for r in rows if r.get("feed") in ("wow-character", "wow-gear") and r.get("kind") != "played"),
                   key=lambda r: str(r.get("_time") or ""))
    for k in ("class", "race", "faction"):
        if not who.get(k):
            who = {**who, k: next((r[k] for r in reversed(known) if r.get(k)), None)}
    return who


def _meta(db, char, rows, rec, now, mark: str | None = None) -> None:
    who = _who(rows, rec)
    now = now or datetime.now(UTC)
    meta = {"char": char, "class": who.get("class"), "race": who.get("race"), "faction": who.get("faction"),
            "first_record": _iso(rec[0]["_t"]) if rec else None, "last_record": _iso(rec[-1]["_t"]) if rec else None,
            "built_at": now.isoformat(timespec="seconds"), "timezone": TZ_NAME,
            "polestar_version": who.get("polestar_version"), "game_build": who.get("build"), "input_mark": mark}
    db.executemany("INSERT INTO meta VALUES (?,?)", [(k, None if v is None else str(v)) for k, v in meta.items()])
    counts = dict(db.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind").fetchall())
    counts["pos"] = db.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    db.executemany("INSERT INTO record_kinds VALUES (?,?,?)",
                   [(k, KINDS.get(k, "(not described in the recording contract)"), n)
                    for k, n in sorted(counts.items())])


def _aggregates(db) -> None:
    for name, key, tl_key, rec_key in (("agg_session", "session_id", "session_id", "session_id"),
                                       ("agg_level", "level", "level", "level"),
                                       ("agg_zone", "zone", "zone", "zone"),
                                       ("agg_hour", "hour_local", "hour_local", "hour_local")):
        pos_key = {"hour_local": "CAST(strftime('%H', time_local) AS INTEGER)"}.get(key, key)
        db.execute(f"""CREATE TABLE {name} AS
        WITH tl AS (SELECT {tl_key} AS k, SUM(dur_s) online_s, SUM(dur_s*afk) afk_s, SUM(dur_s*combat) combat_s,
                      SUM(dur_s*dead) dead_s, SUM(dur_s*taxi) taxi_s, SUM(dur_s*ride) ride_s,
                      SUM(CASE WHEN instance IS NOT NULL THEN dur_s ELSE 0 END) instance_s,
                      SUM(dur_s*mounted) mounted_s FROM timeline GROUP BY 1),
             p AS (SELECT {pos_key} AS k, SUM(dist_yd) distance_yd, COUNT(*) positions FROM positions GROUP BY 1),
             r AS (SELECT {rec_key} AS k, SUM(kind='kill') kills, SUM(kind='death') deaths,
                      SUM(kind='turnin') turnins, SUM(kind='accept') accepts, SUM(kind='loot') loots,
                      SUM(kind='cast') casts, SUM(kind='craft') crafts, COUNT(*) records FROM records GROUP BY 1),
             x AS (SELECT {key} AS k,
                      SUM(xp_gained) xp_gained FROM xp GROUP BY 1),
             keys AS (SELECT k FROM tl UNION SELECT k FROM r)
        SELECT keys.k AS {key}, COALESCE(online_s,0) online_s, COALESCE(afk_s,0) afk_s,
               COALESCE(combat_s,0) combat_s, COALESCE(dead_s,0) dead_s, COALESCE(taxi_s,0) taxi_s,
               COALESCE(ride_s,0) ride_s, COALESCE(instance_s,0) instance_s, COALESCE(mounted_s,0) mounted_s,
               COALESCE(distance_yd,0) distance_yd, COALESCE(positions,0) positions,
               COALESCE(x.xp_gained,0) xp_gained, COALESCE(kills,0) kills, COALESCE(deaths,0) deaths,
               COALESCE(turnins,0) turnins, COALESCE(accepts,0) accepts, COALESCE(loots,0) loots,
               COALESCE(casts,0) casts, COALESCE(crafts,0) crafts, COALESCE(records,0) records
        FROM keys LEFT JOIN tl ON tl.k IS keys.k LEFT JOIN p ON p.k IS keys.k LEFT JOIN r ON r.k IS keys.k
        LEFT JOIN x ON x.k IS keys.k ORDER BY 1""")


def _dictionary(db) -> None:
    out = []
    for table, (desc, cols) in DICTIONARY.items():
        out.append((table, None, desc))
        for c, d in cols.items():
            out.append((table, c, d))
    db.executemany("INSERT INTO dictionary VALUES (?,?,?)", out)


def schema_text(db: sqlite3.Connection) -> str:
    """Every table with its columns, row count and descriptions: the
    analysis' view of the database."""
    desc: dict[tuple, str] = {}
    for t, c, d in db.execute("SELECT table_name, column_name, description FROM dictionary"):
        desc[(t, c)] = d
    lines = []
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")]
    for t in tables:
        if t == "dictionary":
            continue
        n = db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        lines.append(f"\n## {t} ({n} rows)" + (f": {desc[(t, None)]}" if (t, None) in desc else ""))
        for _, col, typ, *_ in db.execute(f'PRAGMA table_info("{t}")'):
            d = desc.get((t, col))
            lines.append(f"- {col} {typ or ''}" + (f": {d}" if d else ""))
    return "\n".join(lines).strip()


PROFILE_TOP = 5
PROFILE_CHARS = 60


def profile(db: sqlite3.Connection) -> str:
    """A neutral profile of every table: rows, time span, and per column the
    number of distinct values, the most frequent ones and min/max. Counts
    only: what the data holds, not what it means."""
    out = []
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")]
    for t in tables:
        if t in ("dictionary", "record_kinds", "meta"):
            continue
        n = db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        cols = [(c, (typ or "").upper()) for _, c, typ, *_ in db.execute(f'PRAGMA table_info("{t}")')]
        head = f"## {t}: {n} rows"
        tcol = next((c for c, _ in cols if c in ("t", "t0", "start_t", "sheet_t")), None)
        if tcol and n:
            lo, hi = db.execute(f'SELECT MIN("{tcol}"), MAX("{tcol}") FROM "{t}"').fetchone()
            if lo is not None:
                head += f", {_local(lo).isoformat(timespec='minutes')} to {_local(hi).isoformat(timespec='minutes')}"
        out.append(head)
        if not n:
            continue
        for c, typ in cols:
            nn, nd = db.execute(f'SELECT COUNT("{c}"), COUNT(DISTINCT "{c}") FROM "{t}"').fetchone()
            line = f"- {c}: {nn} set, {nd} distinct"
            if nn and typ in ("INTEGER", "REAL") and nd > PROFILE_TOP:
                lo, hi = db.execute(f'SELECT MIN("{c}"), MAX("{c}") FROM "{t}"').fetchone()
                line += f", min {lo:.6g}, max {hi:.6g}" if isinstance(lo, (int, float)) else ""
            if nn and (typ == "TEXT" or nd <= PROFILE_TOP) and c not in ("extra", "time_utc", "time_local"):
                top = db.execute(f'SELECT "{c}", COUNT(*) n FROM "{t}" WHERE "{c}" IS NOT NULL GROUP BY 1 '
                                 f"ORDER BY n DESC LIMIT {PROFILE_TOP}").fetchall()
                line += "; top: " + ", ".join(f"{str(v)[:PROFILE_CHARS]} ({k})" for v, k in top)
            out.append(line)
    return "\n".join(out)
