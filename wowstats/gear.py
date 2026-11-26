"""Gear advice: ForeverDB.net's scoring, on the server, over the game's own
item data.

What each slot holds now, the best item the character can get at its level
(and the next one coming within a few levels), the gain in percent, and where
to get it: the quest and zone, the dungeon and boss, the craft and profession.

Three data files. data/game-items.json, written by tools/game_items/ from the
Forever client's tables (docs/gear-reference.md): every weapon's and armour
piece's stats, level, slot and class rules, worked out the way the game does
(owner, 2026-11-24: "can we get them from the game data instead? We do that
already for 3D"). data/gear-reference.json, exported from a Polestar release
by tools/gear_reference/: where gear comes from (its quests, drops and
crafts; Polestar's leveling data, generated from ForeverLevelingBiS
v0.12.0-beta, MIT) and the class rules (who wears what); its own item table
isn't read. The quest rewards it leaves out come from forever-db
(data/forever-reference.json `gear_quests`). data/foreverdb-weights.json,
written by tools/foreverdb_weights/: the stat weights per class, spec and
mode (Leveling, PvP, PvE) and each set piece's share of its set's bonuses,
from ForeverDB.net (credited wherever a score shows: CREDIT).
GEAR_REFERENCE_FILE points at another gear file; `off` turns gear advice off.

The score is ForeverDB's (score_with): each stat times its weight, "% hit"
and "% crit" lines as ratings (PCT_RATING), school damage on top of spell
power, weapon DPS for the hands (ranged for hunters), a wand's DPS where the
mode weighs it, armour, the set share; procs and on-use effects count zero.
tests/test_gear_foreverdb.py checks it against ForeverDB's own scores. What
the server can't see stays out: no weapon skills (every weapon the class can
train counts), no random "of the Owl" suffix stats (the client's
C_Item.GetItemStats has them, the item table does not). The bags and the bank
come from the shipper's wow-bags (Syndicator): an item held there is a
candidate, its only source the bag or the bank (`held`), since getting it
again is no errand.

Nothing of Polestar's scoring is read (owner, 2026-11-24: "Ignore polestar
entirely for bis"): no weights or gear settings of its own, in the data file
or from the player's SavedVariables. Polestar's data gives the sources and
the class rules only.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from wowstats import ahsource, classguide, crafting, endgame, forever, quests, questwork, stores

_HERE = Path(__file__).resolve()
# An installed wheel carries it as wowstats/data/; a checkout has it at the root.
BUNDLED = (_HERE.parent / "data" / "gear-reference.json", _HERE.parents[1] / "data" / "gear-reference.json")
WEIGHTS_FILES = (_HERE.parent / "data" / "foreverdb-weights.json", _HERE.parents[1] / "data" / "foreverdb-weights.json")
ITEMS_FILES = (_HERE.parent / "data" / "game-items.json", _HERE.parents[1] / "data" / "game-items.json")
# Where gear comes from, ForeverDB.net's addon data (GPL-3.0 with an attribution
# term): tools/foreverdb_sources/export.py, docs/gear-reference.md.
SOURCES_FILES = (_HERE.parent / "data" / "foreverdb" / "item-sources.json",
                 _HERE.parents[1] / "data" / "foreverdb" / "item-sources.json")

# --- Gear.lua's tables -------------------------------------------------------

# Inventory type -> equipment slot(s) (Gear.lua SLOTS_OF).
SLOTS_OF = {1: (1,), 2: (2,), 3: (3,), 5: (5,), 20: (5,), 6: (6,), 7: (7,), 8: (8,), 9: (9,), 10: (10,),
            11: (11, 12), 12: (13, 14), 16: (15,), 13: (16, 17), 21: (16,), 17: (16,), 22: (17,), 14: (17,),
            23: (17,), 15: (18,), 25: (18,), 26: (18,), 28: (18,)}
EQUIPPED_SLOTS = (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)
MAIN, OFF = 16, 17
RANGED_INV = {15, 25, 26}
MELEE_INV = {13, 17, 21, 22}
OFFHAND_INV = {14, 22, 23}
ANY_ARMOR_INV = {2, 11, 12, 16, 23}  # every class wears these whatever their subclass
OFFHAND_DPS = 0.5  # an off-hand weapon hits for half (ForeverDB: half its weapon-DPS part)
WAND_SUBCLASS = 19
# An item's "% hit" (or crit, dodge...) line as the rating ForeverDB scores
# it with: 10 hit rating per 1% hit, 14 crit, 12 dodge, 15 parry, 5 block.
PCT_RATING = {"hit": ("hitRating", 10), "spellHit": ("hitRating", 10), "crit": ("critRating", 14),
              "spellCrit": ("critRating", 14), "dodge": ("dodgeRating", 12), "parry": ("parryRating", 15),
              "block": ("blockRating", 5)}
# The modes the weights come in (owner, 2026-11-23): Leveling first, the default.
MODES = {"lvl": "Leveling", "pvp": "PvP", "pve": "PvE"}
DEFAULT_MODE = "lvl"
MODE_NAMES = {"lvl": "lvl", "leveling": "lvl", "levelling": "lvl", "pvp": "pvp", "pve": "pve", "raid": "pve"}
CREDIT = {"name": "ForeverDB.net", "url": "https://foreverdb.net", "text": "Weights: ForeverDB.net"}
# Where an item comes from, when ForeverDB's data says it (every source with
# `via: foreverdb`): the licence's attribution, with its link, wherever one
# shows (wowstats.mcp.tools.call adds it as `where_source`; web/app.js
# sourceEl links it).
SOURCE_CREDIT = {"name": "ForeverDB.net", "url": "https://foreverdb.net", "text": "Source: ForeverDB.net"}
# How sure a ForeverDB source is, in the line's words (its label).
BASIS = {"s": "seen in Forever", "f": "Forever client", "k": "Classic data, may differ in Forever",
         "c": "a clue, unconfirmed"}
# Which of an item's sources to show first: what players saw in Forever, then
# the client, then Classic's data, then a clue.
LABEL_ORDER = {"s": 0, "f": 1, "k": 2, "c": 3}
# ForeverDB's availability rule: an item with no required level counts from
# its item level minus 5; a quest reward from its quest's level minus 5.
NO_REQ_BELOW = 5
QUEST_AHEAD = 5
SLOT_NAMES = {1: "Head", 2: "Neck", 3: "Shoulder", 5: "Chest", 6: "Waist", 7: "Legs", 8: "Feet", 9: "Wrist",
              10: "Hands", 11: "Finger", 12: "Finger", 13: "Trinket", 14: "Trinket", 15: "Back", 16: "Main hand",
              17: "Off hand", 18: "Ranged"}
SIDES = {"alliance": 1, "horde": 2}
SOON_LEVELS = 3  # "the next one coming": within this many levels
MIN_UPGRADE = 1.0  # the least gain, in percent, that counts as an upgrade
PER_SLOT = 3  # upgrades listed per slot
# A slot whose best item at this level scores under this share of the worn
# gear's score shows no BiS, no percent and no "within reach": one plain line
# with the points instead (negligible_says). ForeverDB's formula counts
# on-use and procs as zero, so every levelling trinket scores 0-1 points and
# the top of almost nothing read "BiS" and "empty → 100%" (owner,
# 2026-11-25: Lookie's Spyglass, a healer trinket, called BiS on a Shadow
# priest at 37). Measured: those trinket slots sit at 0.1-0.7% (that priest
# 0.5%, the level-33 test priest 0.7%, the demo's past levels 35-44 0.3-0.6%);
# the lowest slot that matters is 2.0% (a trinket at 45, the demo's at 46),
# every other slot 3.9% and up.
NEGLIGIBLE_SHARE = 0.01
# What a slot holds, for the negligible line ("No trinket matters much...").
SLOT_NOUNS = {1: "helm", 2: "necklace", 3: "shoulder piece", 5: "chest piece", 6: "belt", 7: "leg piece",
              8: "pair of boots", 9: "bracer", 10: "pair of gloves", 11: "ring", 12: "ring", 13: "trinket",
              14: "trinket", 15: "cloak", 16: "weapon", 17: "weapon", 18: "ranged weapon"}
HELD = frozenset({"bags", "bank"})  # the source kinds of an item the character already has
# How hard an item is to get (owner, 2026-11-24: "weight 'is this easy' vs
# 'this is a pipe dream'. Convenience and easiness before a quarter of a
# point"), from its easiest source (source_effort), in ForeverDB's own
# buckets' words. While levelling a long shot is never a pick or an
# upgrade: it stays visible as the slot's best possible (bis_unknown), with
# its odds. At ENDGAME_LEVEL none of this applies (effort_penalty).
EFFORT = ("easy", "some effort", "hard", "long shot")
EASY, SOME, HARD, LONG_SHOT = range(4)
# What an easier item is worth against a harder one when picking a slot's
# best and ranking upgrades: a harder item wins only when it scores more
# than 1 / (1 - penalty) times the easier one: 5.3% more for "some
# effort", 17.6% more for "hard". A quarter of a point never does.
EFFORT_PENALTY = (0.0, 0.05, 0.15, 0.15)
# From this level the advice ranks on the score alone: a long shot can be the
# pick or an upgrade, its effort and odds shown (owner, 2026-11-24: the
# effort ranking holds "only during end game at lvl 60 where marginal gains
# are the norm", read as: everywhere but there). classguide.MAX_LEVEL.
ENDGAME_LEVEL = 60
# Content Forever hasn't opened (owner, 2026-11-25: "it proposes arena
# rewards for me even though it's not opened yet", then "it should be
# explicit for end game, not 'we don't know so we count as opened'"):
# endgame is shut unless the meta's endgame section opens it
# (wowstats.endgame, data/kb/endgame.json). A shut source is never a way to
# an item; an item with no other source is no candidate at all, not a
# "where: unknown". A source players saw in Forever (ForeverDB's "s") counts.
# What gear is a PvP reward besides a PvP rank's (data/kb/rules.json): the
# battleground reputations' and the PvP vendors' (the Premier rank weapons
# come from Sergeant Thunderhorn with no rank in ForeverDB's data: an "easy"
# free axe for a level 60 who never queued).
PVP_RULE = "pvp_rewards.sources"
# A drop's chance in percent: at least the first for easy, the second for
# some effort, the third for hard; under it a long shot.
DROP_EASY, DROP_SOME, DROP_HARD = 25.0, 8.0, 1.0
STANDING_IDS = {"Neutral": 4, "Friendly": 5, "Honored": 6, "Revered": 7, "Exalted": 8}
STANDING_NAMES = {v: k for k, v in STANDING_IDS.items()} | {1: "Hated", 2: "Hostile", 3: "Unfriendly"}
# Reputation points from the start of Neutral where each tier starts (the
# game's own, as the reps' points count them).
REP_FLOOR = {4: 0, 5: 3000, 6: 9000, 7: 21000, 8: 42000}
PAIRS = ((11, 12), (13, 14))
PAIRED = {11, 12, 13, 14}


class GearError(Exception):
    """No advice: the reason says what's missing."""


@dataclass
class Reference:
    available: bool
    why: str = ""
    data: dict = field(default_factory=dict)
    # data/foreverdb-weights.json (weights_data()).
    weights: dict = field(default_factory=dict, repr=False)
    # likely_suffix's index, built on first use.
    peers: dict | None = field(default=None, repr=False, compare=False)
    # unsourced()'s list, built on first use.
    nowhere: list | None = field(default=None, repr=False, compare=False)

    @property
    def gear(self) -> dict:
        return self.data.get("gear") or {}

    def item(self, item_id) -> dict | None:
        return (self.data.get("items") or {}).get(str(item_id))

    def meta(self) -> dict:
        d = self.data
        src = (self.weights or {}).get("source") or {}
        return {"polestar": (d.get("polestar") or {}).get("version"), "tag": (d.get("polestar") or {}).get("tag"),
                "leveling": d.get("leveling"), "item_stats": d.get("item_stats"),
                "weights": {k: src.get(k) for k in ("name", "url", "build", "fetched")}}

    def spec_weights(self, cls: str, key: str) -> dict | None:
        """The spec's entry in the weights file: {pve, lvl, pvp, style, role, ...}."""
        return ((self.weights.get("specs") or {}).get(cls) or {}).get(key)


_LOCK = threading.Lock()
_WLOCK = threading.Lock()
_CACHE: dict[str, Reference] = {}


def reference() -> Reference:
    """The gear data, read once per file (GEAR_REFERENCE_FILE, else the bundled one)."""
    setting = os.environ.get("GEAR_REFERENCE_FILE", "").strip()
    if setting.lower() == "off":
        return Reference(False, "gear advice is turned off (GEAR_REFERENCE_FILE=off)")
    paths = [Path(setting)] if setting else list(BUNDLED)
    key = "|".join(str(p) for p in paths)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        ref = Reference(False, "no gear data file (data/gear-reference.json) in this install")
        for p in paths:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("schema") == 1 and data.get("items"):
                fdb = weights_data()
                if fdb is None:
                    ref = Reference(False, "no stat weights file (data/foreverdb-weights.json) in this install")
                    break
                game = game_items()
                if game is None:
                    ref = Reference(False, "no item data file (data/game-items.json) in this install")
                    break
                ref = Reference(True, data=with_game_items(data, game, fdb), weights=fdb)
                break
        _CACHE[key] = ref
        return ref


def with_game_items(polestar: dict, game: dict, fdb: dict) -> dict:
    """Polestar's gear data with the items from the game's own tables
    (owner, 2026-11-24: "can we get them from the game data instead?"):
    data/game-items.json, tools/game_items/export.py. Every item's stats,
    level, slot and class rules are the client's; Polestar's item table
    isn't read. Where gear comes from is ForeverDB.net's addon data first
    (data/foreverdb/item-sources.json, owner 2026-11-24: quests, vendors,
    drops with their measured chance, crafts, reputation, PvP); for an item
    it has nothing for, Polestar's sources (quest, drop, craft) and
    forever-db's quest rewards (data/forever-reference.json `gear_quests`).
    Polestar still says who wears what (`gear`). Each set piece carries its share of the
    set's bonuses as ForeverDB lists it (the bonus total over the set's size,
    on every piece whether the others are worn or not)."""
    items: dict[str, dict] = {}
    names: dict[str, str] = dict(polestar.get("names") or {})
    sets = fdb.get("sets") or {}
    era_only: list[str] = []
    old = polestar.get("items") or {}
    for iid, e in (game.get("items") or {}).items():
        names[iid] = e["name"]
        items[iid] = {k: v for k, v in e.items() if k not in ("name", "dmg", "from")}
        # Who may wear it: the client's class mask, else Polestar's. The
        # client leaves about 50 class items open to every class (Tier 3, Atiesh,
        # the Zul'Gurub bracers: the server enforces those), Polestar knows.
        if not e.get("allow") and (old.get(iid) or {}).get("allow"):
            items[iid]["allow"] = old[iid]["allow"]
        if e.get("from") == "era":
            era_only.append(iid)
        if iid in sets:
            items[iid]["set"] = sets[iid]
    quests = dict(polestar.get("quests") or {})
    gear_quests = forever.data().get("gear_quests") or {}
    # ForeverDB's sources first (owner, 2026-11-24); Polestar's and
    # forever-db's only for an item ForeverDB has none for.
    # Where a creature stands: ForeverDB's creature drops name the creature
    # only, Polestar's drop sources say its zone (Scorn: Scarlet Monastery).
    npc_zone = {x["npc"]: (x.get("zone"), x.get("zone_name")) for ss in (polestar.get("sources") or {}).values()
                for x in ss if x.get("kind") == "drop" and x.get("npc") and (x.get("zone") or x.get("zone_name"))}
    fdb_items, fdb_meta = foreverdb_sources(items, quests, gear_quests, sources_data(), npc_zone)
    sources = dict(fdb_items)
    for iid, ss in (polestar.get("sources") or {}).items():
        if iid in items and iid not in fdb_items:
            sources[iid] = list(ss)
    for qid, (side, races, classes, rewards, choices) in gear_quests.items():
        mine = [str(x) for x in (*rewards, *choices) if str(x) in items and str(x) not in fdb_items]
        if not mine:
            continue
        if qid not in quests:
            quests[qid] = _forever_quest(qid, side, races, classes, rewards, choices)
        for iid in mine:
            got = sources.setdefault(iid, [])
            if not any(x.get("kind") == "quest" and str(x.get("quest")) == qid for x in got):
                got.append({"kind": "quest", "quest": int(qid)})
    # All gear sources share one quest record. Fill the requirements even
    # when Polestar or a ForeverDB source installed a record first.
    for qid in quests:
        side, races, classes = forever.quest_gates(qid)
        q = quests[qid]
        quests[qid] = {**q, "side": side if side in (1, 2) else q.get("side", 3),
                       "race": races or q.get("race", 0), "class": classes or q.get("class", 0),
                       "req": (forever.quest(qid) or {}).get("min_level") or q.get("req", 0)}
    return {**polestar, "items": items, "names": names, "sources": sources, "quests": quests,
            "era_only": era_only, "item_stats": {k: v for k, v in (game.get("source") or {}).items() if k != "tables"},
            "foreverdb": fdb_meta}


def _forever_quest(qid: str, side, races, classes, rewards, choices) -> dict:
    fq = forever.quest(qid) or {}
    return {"name": fq.get("name"), "level": fq.get("level") or 0, "req": fq.get("min_level") or 0,
            "side": side if side in (1, 2) else 3, "zone_name": forever.quest_zone(qid), "race": races,
            "class": classes, "dungeon": forever.quest_dungeon(qid) is not None,
            "choices": list(choices), "items": list(rewards), "from": "forever-db"}


_SIDE = {"Alliance": 1, "Horde": 2}


def foreverdb_sources(items: dict, quests: dict, gear_quests: dict, data: dict | None,
                      npc_zone: dict | None = None) -> tuple[dict, dict]:
    """({item: [raw source]}, the file's source block) from
    data/foreverdb/item-sources.json, in the raw shape _source reads, each
    with `via: foreverdb` and its `label`. A quest ForeverDB names that the
    quest table hasn't is added from forever-db (its race and class masks)
    or from ForeverDB itself; `quests` is filled in place. npc_zone: {npc:
    (area id, zone name)} for creature drops ForeverDB places nowhere."""
    if not data:
        return {}, {}
    npcs, insts = data.get("npcs") or {}, data.get("instances") or {}
    out: dict[str, list] = {}
    for iid, e in (data.get("items") or {}).items():
        if iid not in items:
            continue
        if e.get("side") in _SIDE:
            items[iid]["side"] = _SIDE[e["side"]]
        raw = []
        gate = next((x.get("faction") for x in e.get("sources") or [] if x.get("kind") == "reputation"), None)
        sided, gate_side = side_of_gate(e.get("sources") or [])
        for x in sorted(e.get("sources") or [], key=lambda x: LABEL_ORDER.get(x.get("label"), 9)):
            k, base = x.get("kind"), {"via": "foreverdb", "label": x.get("label")}
            if k == "quest":
                q = str(x["quest"])
                if q not in quests:
                    gq = gear_quests.get(q)
                    if gq:
                        quests[q] = _forever_quest(q, *gq)
                    else:
                        # A quest Forever added after forever-db's release
                        # (Saving Sunnyglade, 94877) is zoned where ForeverDB
                        # places its giver, unless a dungeon claims it.
                        fq = forever.quest(q) or {}
                        quests[q] = {"name": fq.get("name") or x.get("name"), "level": fq.get("level") or 0,
                                     "req": fq.get("min_level") or x.get("min_level") or 0,
                                     "side": _SIDE.get(x.get("side"), 3),
                                     "zone_name": forever.quest_zone(q, (x.get("giver") or {}).get("map")),
                                     "dungeon": forever.quest_dungeon(q) is not None, "from": "foreverdb"}
                raw.append({**base, "kind": "quest", "quest": int(q)})
            elif k == "drop" and not x.get("boss"):
                # Seen dropping in an instance, from no boss: a random drop
                # players saw there (Gloom Reaper in Scarlet Monastery), not
                # a reason to run that dungeon.
                inst = insts.get(str(x.get("instance"))) or {}
                raw.append({**base, "kind": "drop", "world": True, "pct": x.get("pct"),
                            "zone_name": inst.get("name") or (forever.area(forever.area_of_map(x.get("instance")))
                                                              or {}).get("name")})
            elif k == "drop":
                inst = insts.get(str(x.get("instance"))) or {}
                aid = forever.area_of_map(x.get("instance"))
                raw.append({**base, "kind": "drop", "npc": x.get("npc"), "npc_name": x.get("boss"),
                            "pct": x.get("pct"), "kills": x.get("kills"), "zone": aid,
                            "instance_map": x.get("instance"),
                            "zone_name": (forever.area(aid) or {}).get("name") or inst.get("name"),
                            "raid": bool(x.get("raid"))})
            elif k in ("creature", "rare"):
                aid, zname = (npc_zone or {}).get(x.get("npc")) or (None, None)
                raw.append({**base, "kind": "drop", "npc": x.get("npc"), "npc_name": x.get("name"),
                            "pct": x.get("pct"), "rare": k == "rare", "zone": aid,
                            "zone_name": zname or (npcs.get(str(x.get("npc"))) or {}).get("zone")})
            elif k == "world":
                raw.append({**base, "kind": "drop", "world": True, "level_min": x.get("level_min"),
                            "level_max": x.get("level_max")})
            elif k == "craft":
                raw.append({**base, "kind": "craft", "spell": x.get("spell"), "profession": x.get("profession"),
                            "skill": x.get("skill")})
            elif k == "vendor":
                n = npcs.get(str(x.get("npc"))) or {}
                # Next to a reputation the vendor is its quartermaster: it
                # sells to the reputation's side and is that gate (is_pvp).
                raw.append({**base, "kind": "vendor", "npc": x.get("npc"), "npc_name": n.get("name"),
                            "zone_name": n.get("zone"), "map": n.get("map"), "x": n.get("x"), "y": n.get("y"),
                            "price": x.get("price"),
                            "side": gate_side if sided else x.get("side"),
                            **({"reputation": gate} if gate is not None else {})})
            elif k == "reputation":
                raw.append({**base, "kind": "reputation", "faction": x.get("faction"),
                            "faction_name": x.get("faction_name"), "standing": x.get("standing"),
                            "side": gate_side if sided else _vendor_side(e.get("sources") or [])})
            elif k == "pvp":
                raw.append({**base, "kind": "pvp", "rank": x.get("rank"), "side": x.get("side")})
            elif k in ("container", "object"):
                raw.append({**base, "kind": "container", "name": x.get("name"), "item": x.get("item"),
                            "object": k == "object", "pct": x.get("pct")})
        if raw:
            out[iid] = raw
    src = data.get("source") or {}
    return out, {k: src.get(k) for k in ("name", "url", "build", "data_version")}


def side_of_gate(srcs: list[dict]) -> tuple[bool, str | None]:
    """(known, the side of the reputation an item's ForeverDB sources ask
    for: Alliance, Horde or None for a neutral one) from the client's
    Faction table (forever.faction). Not known when there is no reputation, one
    the table doesn't know, or one only Classic's data names (label k or c):
    then each source keeps ForeverDB's side.

    ForeverDB gives a vendor a side but a reputation none, and its vendor
    sides can be wrong: Theramore's quartermaster Bryanna Embreeze is the
    Horde's there, so Volunteer's Lucky Seal never reached an Alliance
    character and was offered to the Horde (2026-11-25); Ratchet's and Booty
    Bay's are too, neutral towns. Only a reputation Forever's own data
    names (f, s) decides: Classic's names one side's faction for gear both
    quartermasters sell (Rune of Duty: Warsong Outriders, seen sold by
    Silverwing's Illiyana Moonblaze)."""
    reps = [x.get("faction") for x in srcs if x.get("kind") == "reputation" and x.get("label") in ("f", "s")]
    known = [forever.faction(f) for f in reps]
    if not reps or any(f is None for f in known) or len({f.get("side") for f in known}) != 1:
        return False, None
    return True, known[0].get("side")


def _vendor_side(srcs: list[dict]) -> str | None:
    """A reputation's side when the Faction table doesn't know it: its
    quartermaster's, as ForeverDB gives it (one side for every vendor)."""
    sides = {x.get("side") for x in srcs if x.get("kind") == "vendor"}
    return next(iter(sides)) if len(sides) == 1 else None


_SOURCES: list = []


def sources_data() -> dict | None:
    """data/foreverdb/item-sources.json (tools/foreverdb_sources/), read once."""
    with _WLOCK:
        if not _SOURCES:
            got = None
            for p in SOURCES_FILES:
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("schema") == 1 and d.get("items"):
                    got = d
                    break
            _SOURCES.append(got)
        return _SOURCES[0]


_GAME: list = []


def game_items() -> dict | None:
    """data/game-items.json (tools/game_items/export.py), read once."""
    with _WLOCK:
        if not _GAME:
            got = None
            for p in ITEMS_FILES:
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("schema") == 1 and d.get("items"):
                    got = d
                    break
            _GAME.append(got)
        return _GAME[0]


_WEIGHTS: list = []


def weights_data() -> dict | None:
    """data/foreverdb-weights.json (tools/foreverdb_weights/), read once."""
    with _WLOCK:
        if not _WEIGHTS:
            got = None
            for p in WEIGHTS_FILES:
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("schema") == 1 and d.get("specs"):
                    got = d
                    break
            _WEIGHTS.append(got)
        return _WEIGHTS[0]


# --- class and spec ----------------------------------------------------------


def class_token(v) -> str | None:
    t = str(v or "").strip().upper().replace(" ", "")
    return t or None


def spec_key(ref: Reference, cls: str, name) -> str | None:
    """A spec key of this class from a key, a label or a talent column name
    (Gear.lua specKeyOf: lower case, letters only, against each spec's
    names). Forever Leveling BiS calls Feral's bear "bear"."""
    meta = (ref.gear.get("CLASSES") or {}).get(cls)
    if not meta or not isinstance(name, str):
        return None
    n = "".join(c for c in name.lower() if c.isalpha())
    if n == "bear" and cls == "DRUID":
        n = "feral"
    for spec in meta.get("specs") or []:
        if n == spec["key"] or n in (spec.get("names") or []) or n == "".join(
                c for c in spec["label"].lower() if c.isalpha()):
            return spec["key"]
    return None


def specs_of(ref: Reference, cls: str) -> list[dict]:
    meta = (ref.gear.get("CLASSES") or {}).get(cls) or {}
    return [{"key": s["key"], "label": s["label"]} for s in meta.get("specs") or []]


def most_points(ref: Reference, cls: str, talents) -> str | None:
    """The spec of the tree with the most points (Gear.lua mostPoints): the
    first tree with strictly more than every one before it, named by its
    tree name (specKeyOf: lower case, letters only, against each spec's
    names), else by its position. None before the first point."""
    meta = (ref.gear.get("CLASSES") or {}).get(cls) or {}
    specs = meta.get("specs") or []
    best, top = None, 0
    for i, t in enumerate(talents if isinstance(talents, list) else []):
        pts = t.get("points") if isinstance(t, dict) else None
        if not isinstance(pts, (int, float)) or pts <= top:
            continue
        n = "".join(c for c in str(t.get("tree") or "").lower() if c.isalpha())
        key = next((sp["key"] for sp in specs if n and n in (sp.get("names") or [])), None)
        key = key or (specs[i]["key"] if i < len(specs) else None)
        if key:
            best, top = key, pts
    return best


def resolve_spec(ref: Reference, cls: str, sheet: dict, override: str | None = None,
                 weights: str | None = None) -> tuple[str | None, str]:
    """The spec to score for and where it came from (Gear.lua resolveSpec):

    1. the page's choice ("page");
    2. the server's WEIGHTS setting for this character ("server": the
       player's choice, set where the site runs);
    3. the spec the player picked in a gear addon, ForeverLevelingBiS or
       GearQuestForever (the shipper's `spec`: "picked");
    4. only when nothing above says, the tree with the most talent points
       (Gear.lua DetectSpec), from Battle.net's API (the exporter's daily
       /specializations read, which the caller puts in `talents`): a hint,
       "talents";
    5. the class's leveling default ("default").

    The player's choice comes first (owner, 2026-11-18, restored
    2026-11-20): a Shadow priest leveling with most points in Discipline
    on the way to Shadowform wants Shadow weights. Polestar's own Stat
    weights setting is not read (owner, 2026-11-24)."""
    if override:
        k = spec_key(ref, cls, override)
        if k:
            return k, "page"
    k = spec_key(ref, cls, weights)
    if k:
        return k, "server"
    k = spec_key(ref, cls, sheet.get("spec"))
    if k:
        return k, "picked"
    k = most_points(ref, cls, sheet.get("talents"))
    if k:
        return k, "talents"
    meta = (ref.gear.get("CLASSES") or {}).get(cls) or {}
    return meta.get("default"), "default"


# --- weights (ForeverDB.net) ---------------------------------------------------


def mode_key(mode) -> str:
    """'lvl', 'pvp' or 'pve' from a key or a name (Leveling, PvP, PvE);
    Leveling for anything else."""
    return MODE_NAMES.get(str(mode or "").strip().lower(), DEFAULT_MODE)


def modes_out(mode: str) -> dict:
    """The mode as an answer carries it: its key, its name and the choices."""
    return {"key": mode, "label": MODES[mode], "choices": [{"key": k, "label": v} for k, v in MODES.items()]}


def weights_for(ref: Reference, cls: str, key: str, mode: str = DEFAULT_MODE) -> tuple[dict | None, dict | None]:
    """ForeverDB's weights for the spec in this mode, ready for score_with:
    weapon DPS counts on ranged weapons for hunters and on melee weapons for
    everyone else (`rdps` / `dps`). The same table at every level (ForeverDB
    has one per spec and mode). -> weights, where they come from."""
    spec = ref.spec_weights(cls, key)
    mode = mode_key(mode)
    if not spec or not spec.get(mode):
        return None, None
    w = dict(spec[mode])
    if cls == "HUNTER":
        w["rdps"] = w.pop("dps", 0)
    info = {"mode": mode, "label": f"{MODES[mode]} weights", "source": CREDIT["url"], "style": spec.get("style"),
            "note": "ForeverDB.net's " + ("class-guide weights" if mode == "pve" else
                                         "Leveling weights, made from the class guide's by its rule" if mode == "lvl"
                                         else "PvP-guide weights")}
    return w, info


# --- scoring (ForeverDB's formula) --------------------------------------------


def stat_weight(w: dict, k: str) -> float:
    """The weight of one point of an item stat: a "% hit" line counts as its
    rating (PCT_RATING); hit that only works with a bow, gun or crossbow
    (`rangedHit`) counts for a hunter alone (`rdps`: weights_for)."""
    if k == "rangedHit":
        return (w.get("hitRating") or 0) if "rdps" in w else 0
    if k in PCT_RATING:
        r, per = PCT_RATING[k]
        return (w.get(r) or 0) * per
    return w.get(k) or 0


def score_with(info: dict, w: dict, as_offhand: bool = False) -> float:
    """ForeverDB's score: each stat times its weight. Weapon DPS on the
    weapons the weights say (ranged for hunters, `rdps`; melee for the
    rest, `dps`), an off-hand weapon's at half; a wand's DPS times the wand
    weight. "+X damage and healing" (spell power equal to healing in the
    item table) is one stat, `dmgheal`; school damage counts on top, never
    capped. The set share (reference()) counts on every piece. Procs and
    on-use effects aren't in the item table: zero."""
    st = info["stats"]
    sp, heal = st.get("sp") or 0, st.get("heal") or 0
    s = 0.0
    for k, v in st.items():
        if k == "dps":
            if info.get("class") == 2 and info.get("subclass") == WAND_SUBCLASS:
                s += (w.get("wand") or 0) * v
            elif info["inv"] in RANGED_INV:
                s += (w.get("rdps") or 0) * v
            elif info["inv"] in MELEE_INV:
                s += (w.get("dps") or 0) * v * (OFFHAND_DPS if as_offhand else 1)
        elif k in ("sp", "heal") and sp == heal:
            continue
        else:
            s += stat_weight(w, k) * v
    if sp and sp == heal:
        s += (w.get("dmgheal") or 0) * sp
    for k, v in (info.get("set") or {}).items():
        s += (w.get(k) or 0) * v
    return s


def _export(cls: str, key: str, mode: str) -> dict | None:
    from wowstats import gearexport

    try:
        return gearexport.export(cls, key, mode)
    except gearexport.ExportError:
        return None


def available_level(info: dict) -> int:
    """ForeverDB's level an item counts from: its required level, else its
    item level minus 5 (no item asks for more than the content it drops in).
    Required level 1 is no requirement: Forever's own items say 1 where
    Classic's say 0 (Marshdweller's Pants, item level 55: ForeverDB lists it
    from 50)."""
    if (info.get("req") or 0) > 1:
        return int(info["req"])
    return max(1, int(info.get("ilvl") or 0) - NO_REQ_BELOW) if info.get("ilvl") else 1


def behind_pct(best: float, alt: float) -> int:
    """ForeverDB's "-N% vs best": 0 when as good, else at least 1."""
    if best <= 0 or alt >= best - EPS:
        return 0
    return max(1, math.floor((best - alt) / best * 100 + 0.5))


def usability(ref: Reference, info: dict, cls: str, level: int, who: Who | None = None) -> tuple[bool, str | None, int]:
    """Gear.lua usability -> usable, reason, the level it counts from
    (available_level: ForeverDB's rule). Without the client's weapon skills,
    every weapon the class can train counts (knowsWeaponSkill's own
    fallback); Dual Wield from the level the class trains it."""
    g = ref.gear
    prof = (g.get("PROFICIENCY") or {}).get(cls)
    meta = (g.get("CLASSES") or {}).get(cls)
    min_level = available_level(info)
    if info.get("side") and (who is None or info["side"] != who.side):
        return False, "faction", min_level
    skill = info.get("required_skill")
    if skill and (who is None or who.skill_ranks.get(skill, 0) < max(1, info.get("required_skill_rank") or 0)):
        return False, "skill", min_level
    if not (prof and meta):
        return True, None, min_level
    allow = info.get("allow") or 0
    if allow > 0 and (allow // meta["bit"]) % 2 != 1:
        return False, "class", min_level
    sub, inv = info.get("subclass"), info["inv"]
    if info["class"] == 4 and sub is not None and inv not in ANY_ARMOR_INV:
        if 1 <= sub <= 4:
            if sub > prof["armor"]:
                if not (prof.get("armorAt40") and sub <= prof["armorAt40"]):
                    return False, "armor", min_level
                min_level = max(min_level, 40)
        elif sub == 6:
            if not prof.get("shield"):
                return False, "armor", min_level
        elif sub in (7, 8, 9) and prof.get("relic") != sub:
            return False, "armor", min_level
    elif info["class"] == 2 and sub is not None:
        if sub == 20:
            return False, "weapon", min_level
        if sub == 14:
            return False, None, min_level
        if str(sub) not in (prof.get("weapons") or {}):
            return False, "weapon", min_level
        if inv == 22 and not can_dual_wield(prof, level):
            return False, "dualwield", min_level
    return True, None, min_level


def can_dual_wield(prof: dict | None, level: int) -> bool:
    """Gear.lua canDualWield, without the spell book."""
    return bool(prof and prof.get("dualWield") and level >= prof["dualWield"])


# --- comparison (Gear.lua compare) -------------------------------------------


@dataclass
class Worn:
    """What is worn: per slot the item ID (or None), its info and score
    (Gear.lua refreshEquipped: an item without data still fills its slot,
    with a score of 0)."""

    ids: dict = field(default_factory=dict)
    infos: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)
    # Slots whose item has random stats ("of the Owl") the item table can't
    # see: Polestar reads them from the client, the server can't.
    unknown: set = field(default_factory=set)

    def score(self, slot: int) -> float:
        return self.scores.get(slot, 0)


def worn_from(ref: Reference, items: list[dict], w: dict, real: dict | None = None) -> Worn:
    """real: {slot: {"item_id", "stats"}}, what Battle.net's equipment
    answer says each worn item really has (worn_stats()); a random-suffix
    item is scored on those when they're for the same item, else on its
    likely suffix (likely_suffix) and its slot is in `unknown`."""
    worn = Worn()
    for it in items:
        slot = it.get("slot")
        if slot not in EQUIPPED_SLOTS or not it.get("item_id"):
            continue
        info = ref.item(int(it["item_id"]))
        if random_suffix(it.get("name"), info):
            got = (real or {}).get(slot) or {}
            if got.get("item_id") == int(it["item_id"]) and set(got.get("stats") or {}) - _FIXED:
                extra = {k: v for k, v in got["stats"].items() if k not in _FIXED}
                info = {**info, "stats": {**info["stats"], **extra}, "suffix": "real"}
            else:
                info = with_likely_suffix(ref, info, w)
                worn.unknown.add(slot)
        worn.ids[slot] = int(it["item_id"])
        worn.infos[slot] = info
        worn.scores[slot] = score_with(info, w, slot == OFF) if info else 0
    return worn


_SUFFIX = re.compile(r" of (the )?[A-Z][a-z]+$")
_FIXED = {"armor", "dps"}
# Battle.net's equipment answer: slot types to inventory slots, stat types
# to the item table's stat keys (wowstats.exporter puts them in vm-wow).
BNET_SLOTS = {"head": 1, "neck": 2, "shoulder": 3, "chest": 5, "waist": 6, "legs": 7, "feet": 8, "wrist": 9,
              "hands": 10, "finger_1": 11, "finger_2": 12, "trinket_1": 13, "trinket_2": 14, "back": 15,
              "main_hand": 16, "off_hand": 17, "ranged": 18}
BNET_STATS = {"STRENGTH": "str", "AGILITY": "agi", "STAMINA": "sta", "INTELLECT": "int", "SPIRIT": "spi"}
# Peers of a random-suffix item: the same slot (a robe is a chest), the same
# quality, within this many item levels.
SUFFIX_ILVL = 2
_INV_GROUP = {20: 5}


def suffix_base(ref: Reference, item_id: int, info: dict | None) -> bool:
    """An item that rolls a random suffix ("of the Owl"): uncommon, no stat
    beyond armor or weapon damage in the item table, and no quest, craft or
    vendor source (those give fixed items; an uncommon with a proc and no
    stat is one of them)."""
    if not info or info.get("quality") != 2 or set(info.get("stats") or {}) - _FIXED:
        return False
    return all(s.get("kind") == "drop" for s in (ref.data.get("sources") or {}).get(str(item_id)) or [])


def _peers(ref: Reference) -> dict:
    """The stats beyond armor and damage of every fixed-stat item, by
    (slot group, quality): [(item level, stats)]."""
    with _LOCK:
        if ref.peers is None:
            peers: dict = {}
            for it in (ref.data.get("items") or {}).values():
                extra = {k: v for k, v in (it.get("stats") or {}).items() if k not in _FIXED}
                if extra:
                    inv = _INV_GROUP.get(it["inv"], it["inv"])
                    peers.setdefault((inv, it.get("quality")), []).append((it.get("ilvl") or 0, extra))
            ref.peers = peers
        return ref.peers


def likely_suffix(ref: Reference, info: dict, w: dict) -> dict:
    """The stats a random-suffix item most likely adds, for these weights.

    The item table can't say which suffix an item has (it's in the item
    link) nor which ones it can roll (the server's loot tables): scored by
    the most likely suffix, not a range, so every comparison (options,
    weapon setups, the per-slot pick) stays one number. Blizzard gives a
    suffix the stat budget of a fixed-stat item of the same slot, quality
    and level, so the likely suffix is the median, by this character's
    weights, of what those items carry beyond armor and damage: half the
    suffixes it could roll are worth less to him, half more. {} without
    peers (the item keeps its base stats)."""
    pool = _peers(ref).get((_INV_GROUP.get(info["inv"], info["inv"]), info.get("quality"))) or []
    ilvl = info.get("ilvl") or 0
    near = [st for lv, st in pool if abs(lv - ilvl) <= SUFFIX_ILVL]
    if not near:
        return {}
    near.sort(key=lambda st: (score_with({"inv": info["inv"], "class": None, "stats": st}, w, False),
                              sorted(st.items())))
    return dict(near[(len(near) - 1) // 2])


def item_for(ref: Reference, item_id: int, w: dict) -> dict | None:
    """A candidate's item table entry, a random-suffix one with its likely
    suffix (a drop or a held item: which suffix it has isn't known)."""
    info = ref.item(item_id)
    return with_likely_suffix(ref, info, w) if suffix_base(ref, item_id, info) else info


def with_likely_suffix(ref: Reference, info: dict, w: dict) -> dict:
    """The item with its likely suffix's stats added (likely_suffix)."""
    extra = likely_suffix(ref, info, w)
    return {**info, "stats": {**info["stats"], **extra}, "suffix": "likely"} if extra else info


def armory_worn(store, char: str) -> dict:
    """worn_stats() of what the store's Battle.net equipment says; {} when
    vm-wow is down or absent (the likely suffix then)."""
    try:
        return worn_stats(store.character_equipment(char))
    except Exception:  # noqa: BLE001
        return {}


def sheet_has_items(row: dict | None) -> bool:
    """The shipper's gear event lists equipped items. Without Syndicator it
    still sends one, with the professions and no item (2026-11-20)."""
    return any(isinstance(i, dict) and _num(i.get("item_id")) for i in _jlist((row or {}).get("items")))


def no_sheet(fallback: dict, worn: dict, row: dict | None = None) -> tuple[dict, str | None]:
    """A sheet whose equipped items Syndicator never saved (sheet()'s
    input): what Battle.net says it wears (armory_worn), else nothing worn,
    every slot open, so the advice is the best items for its level. `row`:
    the shipper's gear event without items, whose professions, spec and
    the rest stay. The whole set comes from Battle.net, never slot by slot:
    a sheet with items lists every slot worn, so an empty one there is
    really empty. Class, race and faction from the row, else `fallback`
    (Polestar's character sheet). Returns the row and where the worn items
    come from ("battle.net", or None: unknown)."""
    items = [{"slot": slot, "item_id": r["item_id"]} for slot, r in sorted((worn or {}).items())
             if isinstance(r.get("item_id"), int)]
    row = {**(row or {}), "items": items,
           **{k: (row or {}).get(k) or fallback.get(k) for k in ("class", "race", "faction")}}
    return row, ("battle.net" if items else None)


def battlenet_items(worn: dict) -> list[dict]:
    """The page's equipped items from Battle.net's equipment (armory_worn):
    slot, id, name, quality and item level, as build_gear gives the
    shipper's. vm-wow keeps 23 days and the store reads the last 2, so
    nothing older than Blizzard's 30 days ever shows."""
    from wowstats import items as itemnames

    ref = reference()
    out = []
    for slot, r in sorted((worn or {}).items()):
        iid = r.get("item_id")
        if not isinstance(iid, int):
            continue
        info = ref.item(iid) or {}
        try:
            name, quality = itemnames.shared().get(iid)
        except Exception:  # noqa: BLE001 - no name is no reason to lose the slot
            name, quality = None, info.get("quality")
        out.append({"slot": slot, "item_id": iid, "name": name, "quality": quality if quality is not None
                    else info.get("quality"), "item_level": info.get("ilvl"), "req_level": info.get("req"),
                    "enchant_id": None, "enchant": None, "slot_name": None})
    return out


# The one action that turns the estimate into upgrades over what is worn.
SHEET_UNLOCK = "Tick Syndicator in AddOns at character select, log in with this character, then log out."


def unknown_worn(worn_from: str | None) -> dict:
    """What an advice made without a character sheet adds: where the worn
    items come from, whether it is an estimate, and what unlocks the rest."""
    return {"worn_from": worn_from, "estimated": worn_from is None, "unlock": SHEET_UNLOCK}


def worn_stats(rows: dict) -> dict:
    """worn_from()'s `real` from the store's Battle.net equipment
    (stores.character_equipment): {slot: {"item_id", "stats"}}."""
    out = {}
    for slot_type, r in (rows or {}).items():
        slot = BNET_SLOTS.get(str(slot_type).lower())
        if slot and isinstance(r.get("item_id"), int):
            out[slot] = {"item_id": r["item_id"],
                         "stats": {BNET_STATS[k]: v for k, v in (r.get("stats") or {}).items() if k in BNET_STATS}}
    return out


def random_suffix(name, info: dict | None) -> bool:
    """An item worn with a random suffix ("Elder's Cloak of the Elder"):
    its item table entry holds no stat beyond armor or weapon damage, the
    suffix's stats are in the item link only."""
    return bool(isinstance(name, str) and _SUFFIX.search(name) and info is not None
                and not (set(info["stats"]) - _FIXED))


def options(info: dict, item_id: int, worn: Worn, w: dict, dual: bool, every_slot: bool = False) -> list[dict]:
    """Gear.lua compare's choices for a candidate: [{slot, old, new, empty}].
    Rings and trinkets replace the weaker of the two (a unique item only its
    own copy); a two-hander is weighed against main hand + off hand; a
    one-hander or an off-hand piece against a worn two-hander is scored
    alone (0 for the other hand, as with useBags off): the advice's weapon
    setups (WeaponSetups) pair it, held pieces included.
    every_slot: both ring or trinket slots, for the per-slot view."""
    inv = info["inv"]
    slots = SLOTS_OF[inv]
    as_off = inv in OFFHAND_INV
    score = score_with(info, w, as_off)
    out = []

    def opt(slot, old, new, empty):
        out.append({"slot": slot, "old": old, "new": new, "empty": bool(empty), "score": score})

    if inv in (11, 12):
        a, b = slots
        if every_slot:
            opt(a, worn.score(a), score, a not in worn.ids)
            opt(b, worn.score(b), score, b not in worn.ids)
        elif info.get("unique") and worn.ids.get(a) == item_id:
            opt(a, worn.score(a), score, False)
        elif info.get("unique") and worn.ids.get(b) == item_id:
            opt(b, worn.score(b), score, False)
        else:
            if a not in worn.ids:
                slot = a
            elif b not in worn.ids:
                slot = b
            else:
                slot = b if worn.score(b) < worn.score(a) else a
            opt(slot, worn.score(slot), score, slot not in worn.ids)
    elif inv == 17:
        opt(MAIN, worn.score(MAIN) + worn.score(OFF), score, MAIN not in worn.ids and OFF not in worn.ids)
    elif inv in (13, 21) or inv in OFFHAND_INV:
        main = worn.infos.get(MAIN)
        two_hander = bool(main and main["inv"] == 17)
        if inv in (13, 21):
            if two_hander:
                opt(MAIN, worn.score(MAIN), score, False)
            else:
                opt(MAIN, worn.score(MAIN), score, MAIN not in worn.ids)
        if inv in OFFHAND_INV or (inv == 13 and dual):
            as_off_score = score if inv in OFFHAND_INV else score_with(info, w, True)
            if two_hander:
                opt(OFF, worn.score(MAIN), as_off_score, False)
            else:
                opt(OFF, worn.score(OFF), as_off_score, OFF not in worn.ids)
    else:
        slot = slots[0]
        opt(slot, worn.score(slot), score, slot not in worn.ids)
    for o in out:
        o["delta"] = o["new"] - o["old"]
        o["pct"] = o["delta"] / o["old"] * 100 if o["old"] > 0 and not o["empty"] else None
    return out


def compare(info: dict, item_id: int, worn: Worn, w: dict, dual: bool) -> dict:
    """Gear.lua compare: the option with the largest gain (the first on a tie)."""
    # The last slot wearing this very item (a pair of the same dagger: the off hand).
    worn_at = [s for s in SLOTS_OF[info["inv"]] if worn.ids.get(s) == item_id]
    if worn_at:
        s = worn_at[-1]
        return {"slot": s, "old": worn.score(s), "new": worn.score(s), "delta": 0, "pct": 0,
                "equipped": True, "empty": False, "score": score_with(info, w, info["inv"] in OFFHAND_INV)}
    best = None
    for o in options(info, item_id, worn, w, dual):
        if best is None or o["delta"] > best["delta"]:
            best = o
    return {**best, "equipped": False}


def is_upgrade(r: dict, min_level: int, level: int, min_upgrade: float) -> bool:
    """Gear.lua Gear.IsUpgrade: wearable now and at least minUpgrade percent
    better (any gain fills an empty slot)."""
    if r.get("equipped"):
        return False
    up = r["delta"] > 0 and min_level <= level
    if up and r.get("pct") is not None:
        up = r["pct"] >= min_upgrade
    return up


# --- sources -----------------------------------------------------------------


@dataclass
class Who:
    cls: str
    level: int
    side: int | None
    race_id: int | None
    professions: dict  # name (lower) -> rank
    done: set  # quest IDs handed in
    zone: str | None
    # Recipe spell IDs known per profession (lower-case name), from
    # DataStore_Crafts through the shipper's `recipes`; a profession missing
    # here is unknown, never "not learned".
    recipes: dict = field(default_factory=dict)
    skill_ranks: dict = field(default_factory=dict)  # SkillLine ID -> observed rank for equip requirements
    bags: set = field(default_factory=set)  # item IDs in the bags (wow-bags)
    # {faction id: (reaction 1-8, points from the start of Neutral)}, from
    # the Recorder's rep records and Altoholic's (reputations); None when
    # neither has sent any (unknown, never "not met").
    reps: dict | None = None
    # The PvP rank held (0-14): ForeverDB's armory snapshot, else 0 when
    # Altoholic's lifetime honorable kills are 0; None when unknown.
    pvp_rank: int | None = None
    # PvP rewards (is_pvp) count as sources: the player's toggle, default
    # off (owner, 2026-11-25: "PvP should be toggable too when leveling"),
    # always on in the PvP mode, where they are easy (pvp_mode).
    pvp: bool = False
    pvp_mode: bool = False
    today: str = ""  # YYYY-MM-DD, for closed_content
    # Where the character stands for a quest's work (wowstats.questwork):
    # its level, side, guide step, quests done and in the log.
    pos: questwork.Position | None = None


def _wing_area(area: dict, npc_id) -> dict | None:
    """The wing of a dungeon run in wings a creature stands in (its first, by
    the wings' order), as wowstats.plan.wings.split gives it; None for a
    dungeon run whole or a creature placed in none."""
    from wowstats.plan import wings

    if not npc_id or not wings.dungeon(area.get("name") or ""):
        return None
    at = wings.npc(area["name"], npc_id)
    aid = forever.area_id(area["name"])
    return next((w for _, w in wings.split(str(aid), area) if w["wing"] in at[:1]), None)


def _source(ref: Reference, s: dict, who: Who, info: dict, item_id: int | None = None) -> dict | None:
    """One source of an item in the answer's shape, or None when this
    character can't take it (the other side's quest, another race's or
    class's, a quest already handed in), from content Forever hasn't opened
    (closed_content), or a PvP reward while PvP rewards don't count. Item-level equip requirements are checked
    by usability before any acquisition route is considered."""
    # A quest's zone as forever-db gives it: where it is done (a raid's
    # tier quest is handed in outside, its zone is the raid's).
    qz = (forever.quest(s["quest"]) or {}).get("zone") if s["kind"] == "quest" else None
    if closed_content(s, who.today or _today(), qz):
        return None
    pvp = is_pvp(s)
    if pvp and not (who.pvp or who.pvp_mode):
        return None
    tag = {"pvp": True, **({"by_playing": True} if who.pvp_mode else {})} if pvp else {}
    if s["kind"] == "quest":
        q = (ref.data.get("quests") or {}).get(str(s["quest"]))
        if not q:
            return None
        if who.side and q.get("side") in (1, 2) and q["side"] != who.side:
            return None
        if q.get("race") and who.race_id and not (q["race"] >> (who.race_id - 1)) & 1:
            return None
        bit = ((ref.gear.get("CLASSES") or {}).get(who.cls) or {}).get("bit")
        if q.get("class") and bit and not (q["class"] // bit) % 2:
            return None
        if s["quest"] in who.done:
            return None
        fq = forever.quest(s["quest"]) or {}
        # What the quest really takes (wowstats.questwork): where the route
        # does it, its chain, its foe, its dungeon; its effort is that work's.
        dungeon = (forever.area(forever.quest_dungeon(s["quest"])) or {}).get("name") if q.get("dungeon") else None
        work = questwork.facts(s["quest"], who.pos or questwork.Position(level=who.level, done=who.done),
                               level=q.get("level") or fq.get("level"),
                               dungeon=dungeon or ("a dungeon" if q.get("dungeon") else None))
        work_effort = work.pop("effort")
        return {"kind": "quest", "quest_id": s["quest"], "name": q.get("name") or fq.get("name"),
                "zone": q.get("zone_name") or forever.quest_zone(s["quest"]), "part": fq.get("part"),
                "level": q.get("level"), "req_level": q.get("req"), "dungeon": bool(q.get("dungeon")),
                "choices": len(q.get("choices") or []), "work": work,
                "work_says": questwork.says(work, who.level), "work_effort": work_effort, **_basis(s)}
    if s["kind"] == "drop":
        # Whether the zone is a dungeon or a raid, and its level range as the
        # game's Group Finder gives it (data/forever-reference.json): Forever's
        # own instances (City of Dalaran) included.
        a = forever.area(s.get("zone")) or {}
        out = {"kind": "drop", "boss": s.get("npc_name") if s.get("npc") or s.get("via") else None,
               "npc_id": s.get("npc") or None, "zone": s.get("zone_name") or a.get("name"),
               "chance_pct": s.get("pct") or None, "dungeon": a.get("instance") == "dungeon", **_basis(s)}
        if s.get("world"):  # a random world drop: no creature, maybe a level range
            out.update(world=True, levels_drop=[s["level_min"], s["level_max"]] if s.get("level_max") else None)
        elif s.get("npc") and not a.get("instance"):
            # A creature in the open world: its name, levels and rank from
            # forever-db (questwork.creature); ForeverDB.net names a creature
            # it has no name for "NPC 268423", which is no name.
            foe = questwork.creature(s["npc"])
            if not out["boss"] or re.fullmatch(r"NPC \d+", str(out["boss"])):
                out["boss"] = foe.get("name")
            out["zone"] = out["zone"] or foe.get("zone")
            if foe.get("name"):
                out["foe"] = {k: foe[k] for k in ("levels", "rank", "elite") if k in foe}
            # No zone and no level in the data: it spawns nowhere yet
            # (forever-db 2026.11.2 names Zalfan Moonshadow, places it nowhere).
            out["unplaced"] = not (out["zone"] or foe.get("level"))
        if s.get("rare"):
            out["rare"] = True
        if a.get("instance"):
            out["instance"] = a["instance"]
            if a.get("levels"):
                out["levels"] = a["levels"]
            if a.get("floor") is not None:  # Polestar's "For your level" starts there (wowstats.plan.vsguide)
                out["floor"] = a["floor"]
            # A dungeon run in wings: the wing the boss stands in, with its
            # own range (wowstats.plan.wings), so the Overview's move names
            # the Dungeons panel's line.
            w = _wing_area(a, s.get("npc"))
            if w:
                out.update(zone=w["name"], levels=w["levels"], **({"floor": w["floor"]} if "floor" in w else {}))
        return out
    if s["kind"] == "craft":
        prof = s.get("profession")
        rank = who.professions.get(str(prof).lower()) if prof else None
        skill_ok = rank is not None and rank >= (s.get("skill") or 0)
        known = recipe_known(who, prof, s.get("spell"))
        name = (ref.data.get("names") or {}).get(str(item_id)) or forever.item_name(item_id) if item_id else None
        pattern = pattern_in_bags(who, name) if known is not True else None
        # "Make it now" needs the recipe as well as the skill: a pattern in
        # the bags is learnt first, an unknown one is a find, and with no
        # recipe list (no DataStore_Crafts) it is never claimed.
        return {"kind": "craft", "profession": prof, "skill": s.get("skill"), "spell_id": s.get("spell"),
                "your_rank": rank, "skill_ok": skill_ok, "known": known, "pattern_item_id": pattern,
                "can_make": bool(skill_ok and known is True), **_basis(s)}
    # ForeverDB's other kinds: a vendor, a reputation's quartermaster, a PvP
    # rank's, a container. The other side's are left out.
    if s.get("side") and who.side and _SIDE.get(s["side"]) not in (None, who.side):
        return None
    if s["kind"] == "vendor":
        return {"kind": "vendor", "npc_id": s.get("npc"), "name": s.get("npc_name"), "zone": s.get("zone_name"),
                "map": s.get("map"), "x": s.get("x"), "y": s.get("y"), "price_copper": s.get("price") or None,
                **({"reputation_id": s["reputation"]} if s.get("reputation") else {}), **_basis(s), **tag}
    if s["kind"] == "reputation":
        return {"kind": "reputation", "faction_id": s.get("faction"), "name": s.get("faction_name"),
                "standing": s.get("standing"), **rep_check(who, s.get("faction"), s.get("standing")), **_basis(s),
                **tag}
    if s["kind"] == "pvp":
        rank = s.get("rank")
        have = who.pvp_rank
        return {"kind": "pvp", "rank": rank, "your_rank": have,
                "met": None if have is None or rank is None else have >= rank, **_basis(s), **tag}
    if s["kind"] == "container":
        return {"kind": "container", "name": s.get("name"), "container_item_id": s.get("item"),
                "object": bool(s.get("object")), "chance_pct": s.get("pct") or None, **_basis(s)}
    return None


def rep_check(who: Who, faction, standing) -> dict:
    """A reputation source against the character's standing: `met`,
    `your_standing` and the points still to go; `met` None when no
    reputation was ever sent (Altoholic or the Recorder), False with
    `your_standing` None for a faction the character hasn't met."""
    need = STANDING_IDS.get(str(standing or ""))
    if who.reps is None or need is None:
        return {"met": None}
    have = who.reps.get(faction) or who.reps.get(str(faction))
    if not have:
        return {"met": False, "your_standing": None}
    reaction, points = have
    out = {"met": reaction >= need, "your_standing": STANDING_NAMES.get(reaction)}
    if reaction < need and points is not None:
        out["rep_to_go"] = max(0, REP_FLOOR[need] - points)
    return out


def runs_for(pct) -> int | None:
    """Expected kills (or runs) for a drop at `pct` percent: 1 / p."""
    return max(1, round(100 / pct)) if pct else None


_RULE_VALUES: dict[str, dict] = {}


def _rule_value(name: str) -> dict:
    """A knowledge-base rule's value, read once per process (every source of
    every item asks)."""
    if name not in _RULE_VALUES:
        from wowstats.analysis import kb

        _RULE_VALUES[name] = ((kb.static_rule(name) or {}).get("value")) or {}
    return _RULE_VALUES[name]


def closed_content(s: dict, today: str, quest_zone=None) -> str | None:
    """What shut content a raw source belongs to, None when it counts
    (wowstats.endgame.closed: endgame is shut unless the meta's endgame
    section opens it, or ForeverDB.net saw the source in Forever).
    `quest_zone` is forever-db's zone name for a quest, `today` YYYY-MM-DD."""
    return endgame.closed(s, today, quest_zone)


def is_pvp(s: dict) -> bool:
    """A raw source that is a PvP reward: a PvP rank's, a battleground
    reputation's or its quartermaster's, or a PvP vendor's (PVP_RULE)."""
    k, v = s.get("kind"), _rule_value(PVP_RULE)
    if k == "pvp":
        return True
    if k == "reputation":
        return str(s.get("faction")) in (v.get("reputations") or {})
    # A vendor next to a battleground reputation is its quartermaster.
    return k == "vendor" and (str(s.get("npc")) in (v.get("vendors") or {})
                              or str(s.get("reputation")) in (v.get("reputations") or {}))


def _today() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


def _drop_effort(pct, label, harder: bool = False) -> int:
    if pct is None:
        # No rate anyone measured: seen dropping in Forever is hard; only in
        # Classic's data (never seen in Forever, a world drop's Augural
        # Shroud) a long shot.
        e = LONG_SHOT if label in ("k", "c") else HARD
    else:
        e = EASY if pct >= DROP_EASY else SOME if pct >= DROP_SOME else HARD if pct >= DROP_HARD else LONG_SHOT
        if label == "k":  # Classic's rate, never seen in Forever: one bucket harder
            e = min(LONG_SHOT, e + 1)
    return max(e, HARD) if harder else e


def source_effort(s: dict) -> int:
    """How hard one source is (EFFORT): something to buy, hand in or make,
    or already held, is easy; a drop by its chance (the rate players saw in
    Forever over Classic's); a reputation or a PvP rank by what the
    character holds."""
    k = s.get("kind")
    if k == "vendor" and s.get("pvp") and not s.get("by_playing") and not s.get("reputation_id"):
        # A PvP vendor's gear outside the PvP weights: the honor and the rank
        # it asks aren't in the data, like a rank nothing recorded. A
        # battleground reputation's quartermaster is its reputation's effort
        # (by_effort, item_effort).
        return LONG_SHOT
    if k == "ah" and s.get("affordable") is False:
        # A listing the character can't pay for yet (owner, 2026-11-25:
        # Staff of Jordan at 84g with 16g 72s in the bags): saving the gold
        # first is some effort, never "easy". Gold not known: easy.
        return SOME
    if k == "quest":
        # The quest's work (wowstats.questwork): the route's, else its
        # chain, foe, dungeon and items to bring. Without it (a source built
        # by hand), a hand-in.
        return s.get("work_effort", EASY)
    if k in ("bags", "bank", "ah", "vendor") or s.get("by_playing"):
        # by_playing: a PvP reward while the gear is scored for PvP (owner,
        # 2026-11-25: "for pvp specific gear at 60 it'll automatically come
        # from PvP so it's easy").
        return EASY
    if k == "craft":
        # The plan weighs the real state: recipe held or not, gold, the
        # auction house the other way (wowstats.crafting.effort).
        if (s.get("plan") or {}).get("effort") is not None:
            return s["plan"]["effort"]
        return EASY if s.get("can_make") else SOME
    if k == "drop":
        if s.get("npc_id") and (not s.get("boss") or s.get("unplaced")) and not s.get("instance"):
            # A creature nobody names or places: nothing to go and kill.
            return LONG_SHOT
        return _drop_effort(s.get("chance_pct"), s.get("label"),
                            harder=bool(s.get("world") or s.get("instance") == "raid"))
    if k == "container":
        return _drop_effort(s.get("chance_pct"), s.get("label"))
    if k == "reputation":
        if s.get("met"):
            return EASY
        need = STANDING_IDS.get(str(s.get("standing") or ""), 8)
        return SOME if s.get("met") is False and need <= STANDING_IDS["Honored"] else HARD
    if k == "pvp":
        # A rank held is some effort (the quartermaster's price); one not
        # held, or not recorded (no ForeverDB snapshot), is never an
        # errand: the best possible, said with what is missing.
        return SOME if s.get("met") else LONG_SHOT
    return SOME


def effort_penalty(effort: int, level: int) -> float:
    """The share of an item's score its effort costs it in the ranking:
    EFFORT_PENALTY while levelling, nothing at ENDGAME_LEVEL."""
    return 0.0 if level >= ENDGAME_LEVEL else EFFORT_PENALTY[effort]


def ranking_value(gain: float, score: float, effort: int, factor: float, level: int) -> float:
    """What the upgrades sort on: the gain less the effort's share of the
    item's whole score (effort_penalty), times how it fits the spec
    (gearfit's factor)."""
    return (gain - score * effort_penalty(effort, level)) * factor


def item_effort(srcs: list[dict]) -> int:
    """An item's effort: its easiest source's (an auction house listing
    makes anything easy: buy it). No source: a long shot. A vendor next to
    a reputation or a PvP rank is that gate's quartermaster (ForeverDB lists
    both: Master Sergeant's Insignia, Officer Areyn and rank 3): the gate's
    effort, not a vendor's."""
    gated = any(x["kind"] in ("reputation", "pvp") for x in srcs)
    return min((source_effort(x) for x in srcs if not (gated and x["kind"] == "vendor")), default=LONG_SHOT)


def _listing(sale: dict, money: int | None) -> dict:
    """An auction house source as the effort reads it: whether the
    character's gold covers it (None: the gold isn't known)."""
    return {"kind": "ah", **sale,
            "affordable": None if money is None else (sale.get("price_copper") or 0) <= money}


def action_source(sources: list[dict], level: int | None) -> dict | None:
    """First easy or moderate acquisition the character can take at this level."""
    gated = any(s.get("kind") in ("pvp", "reputation") and s.get("met") is not True for s in sources)
    for s in sources:
        kind = s.get("kind")
        if s.get("effort") in EFFORT[HARD:] or source_effort(s) > SOME:
            continue
        if kind == "ah" and (not s.get("price_copper") or s.get("affordable") is False
                             or s.get("seen_days_ago") not in (0, 1)):
            continue
        if kind == "craft" and not s.get("can_make"):
            continue
        if kind in ("reputation", "pvp") and s.get("met") is not True or kind == "vendor" and gated:
            continue
        if level is not None:
            if kind == "quest" and ((s.get("req_level") or 0) > level or (s.get("level") or 0) > level + 3):
                continue
            if kind == "drop" and s.get("levels") and s["levels"][0] > level + 3:
                continue
        return s
    return None


def by_effort(srcs: list[dict]) -> list[dict]:
    """The sources easiest first (each tagged with its `effort`, a drop
    with its expected `runs`), ForeverDB's label order among equals."""
    out = []
    gated = [source_effort(x) for x in srcs if x["kind"] in ("reputation", "pvp")]
    for x in srcs:
        e = max(source_effort(x), min(gated)) if gated and x["kind"] == "vendor" else source_effort(x)
        x = {**x, "effort": EFFORT[e]}
        if x["kind"] in ("drop", "container") and x.get("chance_pct"):
            x["expected_runs"] = runs_for(x["chance_pct"])
        out.append(x)
    return sorted(out, key=lambda x: (EFFORT.index(x["effort"]), LABEL_ORDER.get(x.get("label") or "", 0)))


def _basis(s: dict) -> dict:
    """A ForeverDB source's credit and how sure it is (its label), in the
    answer: `via`, `label`, `basis` (words), and a drop's looted kills."""
    if s.get("via") != "foreverdb":
        return {}
    out = {"via": "foreverdb", "label": s.get("label"), "basis": BASIS.get(s.get("label") or "")}
    if s.get("kills"):
        out["kills"] = s["kills"]
    return out


def basis_words(s: dict) -> str:
    """How sure a source is, for a line: "seen in Forever", "Classic data,
    may differ in Forever"; "" for a source without a label."""
    return s.get("basis") or ""


def chance_words(s: dict) -> str:
    """A drop's chance, with the looted kills it was measured over when
    players saw it in Forever: "43.75% over 16 kills"; "" when unknown."""
    if not s.get("chance_pct"):
        return ""
    out = f"{s['chance_pct']}%" + (f" over {s['kills']} kills" if s.get("kills") else "")
    runs = runs_for(s["chance_pct"])
    if runs and runs > 1:
        # What 1 / p means at the keyboard (owner, 2026-11-24): "about 3,333 runs".
        out += f", about {runs:,} {'runs' if s.get('instance') else 'kills'}"
    return out


def craft_status(s: dict) -> str:
    """What stands between the character and making a craft source, in
    words, for every reader (MCP, planner, coach): the skill, then the recipe.
    The reagents aren't in the data, so "make it" always says to check them."""
    if (s.get("plan") or {}).get("line"):
        return s["plan"]["line"]
    prof, need, rank = s.get("profession") or "the profession", s.get("skill"), s.get("your_rank")
    if rank is None:
        return f"a {prof} crafter makes it ({prof} {need}): buy it or ask one"
    steps = []
    if not s.get("skill_ok", rank >= (need or 0)):
        steps.append(f"reach {prof} {need} (you have {rank})")
    pattern = s.get("pattern_item_id")
    if s.get("known") is False:
        steps.append(f"learn {forever.item_name(pattern) or 'the pattern'}, in your bags" if pattern
                     else "find and learn its recipe (you don't know it)")
    elif s.get("known") is None:
        steps.append(f"learn {forever.item_name(pattern) or 'the pattern'} in your bags if you haven't" if pattern
                     else "check you know the recipe (no recipe list: Altoholic saves it)")
    if steps:
        return "first " + ", then ".join(steps)
    return f"you know the recipe and have {prof} {rank}: make it once you have the materials (not checked)"


# Recipe items are named after what they teach: "Pattern: Shadow Circlet".
RECIPE_PREFIX = re.compile(r"^(Pattern|Formula|Plans|Recipe|Schematic|Manual|Design|Technique|Tome): ")


def recipe_known(who: Who, profession, spell) -> bool | None:
    """Whether the character knows the recipe: True or False from its
    profession's recipe list, None when there is no list for it."""
    got = who.recipes.get(str(profession or "").lower())
    if got is None or not spell:
        return None
    return int(spell) in got


def pattern_in_bags(who: Who, item_name: str | None) -> int | None:
    """The ID of a recipe item in the bags that teaches `item_name`."""
    if not item_name:
        return None
    for i in sorted(who.bags):
        n = forever.item_name(i) or ""
        if RECIPE_PREFIX.match(n) and RECIPE_PREFIX.sub("", n) == item_name:
            return i
    return None


def _says(q: dict) -> tuple:
    return bool(q.get("zone")), bool(q.get("boss")), -(q.get("req_level") or 0)


def _same(x: dict):
    """What makes two sources the same line for a player: a quest by its
    name (the data has some quests twice, a copy with no zone under another
    ID), a drop by its boss and zone, a craft by its recipe."""
    if x["kind"] == "quest":
        return ("quest", x.get("name") or x.get("quest_id"))
    if x["kind"] == "drop":
        return ("drop", x.get("boss") or "", x.get("zone") or "")
    if x["kind"] == "craft":
        return ("craft", x.get("profession"), x.get("spell_id") or x.get("skill"))
    return (x["kind"], x.get("npc_id") or x.get("faction_id") or x.get("rank") or x.get("name"))


def _dedupe(srcs: list[dict]) -> list[dict]:
    """One line per source as the player reads it; of two copies, the one
    that says the most (a zone, a boss)."""
    best: dict = {}
    out: list[dict] = []
    for x in srcs:
        k = _same(x)
        if k not in best:
            best[k] = x
            out.append(x)
        elif _says(x) > _says(best[k]):
            out[out.index(best[k])] = x
            best[k] = x
    return out


def buyable(info: dict, level: int) -> bool:
    """Whether an auction house listing of this item's ID is an upgrade to
    buy at this level. Not an uncommon or better item whose only stats are
    armor or weapon damage: a random-suffix base ("of the Owl" lives in the
    link, and the ID's price is the cheapest suffix listed). Not an item
    with no required level in the item table (Forever's own items, many
    endgame ones) before the level it counts from (available_level)."""
    if (info.get("quality") or 0) >= 2 and not (set(info.get("stats") or {}) - _FIXED):
        return False
    return bool(info.get("req")) or available_level(info) <= level


def _source_level(src: dict) -> int:
    """ForeverDB's quest rule: a reward counts once its quest is at most
    QUEST_AHEAD levels above (the game lets some be taken ten levels early,
    nobody does them there), and never before the quest can be taken."""
    if src["kind"] != "quest":
        return 0
    return max(src.get("req_level") or 0, (src.get("level") or 0) - QUEST_AHEAD)


def gates(at: int, level: int, sources: list[dict]) -> list[dict]:
    """What stands between the character and the item, each with what it
    needs and what he has: the level to wear it (or to take its quest), and
    for an item only a craft gives, the profession's skill when he has the
    profession ("skill", Icy Cloak: Tailoring 200, he has 119) or a crafter
    or the auction house when he hasn't ("buy", not a wait). A vendor's price
    is no gate; an item only a reputation or a PvP rank gives never gets
    here (a long shot: item_effort)."""
    out = []
    if at > level:
        out.append({"kind": "level", "need": at, "have": level})
    crafts = [s for s in sources if s["kind"] == "craft"]
    if crafts and len(crafts) == len(sources) and not any(s.get("can_make") for s in crafts):
        s = min(crafts, key=lambda s: (s.get("skill") or 0) - (s.get("your_rank") or 0))
        if s.get("your_rank") is None:
            out.append({"kind": "buy", "profession": s.get("profession"), "need": s.get("skill"), "have": None})
            return out
        if not s.get("skill_ok", s["your_rank"] >= (s.get("skill") or 0)):
            out.append({"kind": "skill", "profession": s.get("profession"), "need": s.get("skill"),
                        "have": s["your_rank"]})
        # The recipe: learnt from the pattern in the bags, or still to find.
        # Unknown (no recipe list) is no gate, but no "make it now" either.
        if s.get("known") is False:
            out.append({"kind": "learn" if s.get("pattern_item_id") else "recipe",
                        "profession": s.get("profession"), "need": s.get("pattern_item_id"), "have": None})
    return out


def in_zone(src: dict, zone: str | None) -> bool:
    """A source in the zone the character is in now: the advice's `here`,
    gear_options' `in_current_zone`, one rule."""
    return bool(zone and src.get("zone") and src["zone"].lower() == zone.lower())


# --- the character sheet -----------------------------------------------------


def _jlist(v) -> list:
    if isinstance(v, list):
        return v
    try:
        v = json.loads(v or "[]")
    except (TypeError, ValueError):
        return []
    return v if isinstance(v, list) else []


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def sheet(row: dict, fallback: dict | None = None) -> dict:
    """advise()'s sheet from a stored wow-gear event (its lists arrive as
    JSON strings); class, race and faction fall back to `fallback`."""
    fb = fallback or {}
    items = [i for i in _jlist(row.get("items")) if isinstance(i, dict)]
    for i in items:
        for k in ("slot", "item_id"):
            i[k] = _num(i.get(k))
    return {
        "items": items,
        "professions": [p for p in _jlist(row.get("professions")) if isinstance(p, dict)],
        "spec": row.get("spec") or None, "spec_from": row.get("spec_from") or None,
        # Talents come from Battle.net only (Views puts them in); an old
        # sheet's SimBench `talents` stays stored, unread.
        "talents": None,
        "recipes": [r for r in _jlist(row.get("recipes")) if isinstance(r, dict)],
        "class": row.get("class") or fb.get("class"), "race": row.get("race") or fb.get("race"),
        "faction": row.get("faction") or fb.get("faction"),
    }


# What a sheet or a bags event may lack and an older one still says: a
# shipper from 0.7.0 leaves `items` out when Syndicator didn't know the
# character on that run, and `bank` when Syndicator holds no bank (never
# opened, or its cache lost). Older shippers sent [] for both.
SHEET_PARTS = ("items",)
BAGS_PARTS = ("bags", "bank")
# Each part's counts ([item ID, count] pairs, shippers after 0.11.2) ride with
# its ID list in the same event: read from the row the list came from,
# never a part of their own (coverage.py counts the lists only).
BAGS_COUNTS = {"bags": "bags_n", "bank": "bank_n"}
# The stored events the advice needs, after a character filter: the sheet
# in force and, for each part, the newest sheet that has it
# (stores.in_force), for pick_sheet; the bags and bank in force, for
# advise(held=...) through newest_bags.
SHEET_FIELDS = "_time, at_basis, source_id, items, professions, recipes, spec, spec_from, class, race, faction"
HELD_FIELDS = "_time, at_basis, source_id, feed, bags, bank, bags_n, bank_n"


def sheet_rows(read, cf: str, fields: str = SHEET_FIELDS) -> list[dict]:
    """The gear sheet's rows in force (stores.in_force) for pick_sheet."""
    return stores.in_force(read, f'feed:="wow-gear" {cf}', fields, SHEET_PARTS)


def held_rows(read, cf: str) -> list[dict]:
    """The bags and bank rows in force (stores.in_force) for newest_bags."""
    return stores.in_force(read, f'feed:="wow-bags" {cf}', HELD_FIELDS, BAGS_PARTS)


def pick_sheet(rows: list[dict]) -> dict | None:
    """The gear sheet in force among wow-gear rows (stores.newest_with), None without one."""
    return stores.newest_with(rows, SHEET_PARTS) or None


# --- the advice --------------------------------------------------------------


def _item_out(ref: Reference, item_id: int, info: dict | None, name: str | None = None) -> dict:
    names = ref.data.get("names") or {}
    # A held item may have no source in Polestar's data, so no name there.
    # A likely suffix (likely_suffix) is a score, not stats to show: the
    # item's own stats then, and random_suffix says its score is a guess.
    likely = bool(info and info.get("suffix") == "likely")
    shown = ref.item(item_id) if likely else info
    return {"item_id": item_id, "name": name or names.get(str(item_id)) or forever.item_name(item_id),
            "quality": info.get("quality") if info else None, "item_level": info.get("ilvl") if info else None,
            "req_level": (info.get("req") or None) if info else None,
            "stats": dict(shown.get("stats") or {}) if shown else {}, "random_suffix": likely}


def _r(v, n=1):
    return None if v is None else round(v, n)


@dataclass
class _Ctx:
    cls: str
    level: int
    key: str
    how: str
    w: dict
    scoring: dict | None
    dual: bool
    who: Who
    worn: Worn
    worn_names: dict
    points: int | None = None  # talent points in the spec's tree (gearfit.capstone)
    mode: str = DEFAULT_MODE
    style: str = "any"  # ForeverDB's weapon style for the spec (WeaponSetups)


def _context(ref: Reference, sheet: dict, level, zone: str | None, done, spec: str | None,
             weights: str | None, mode: str | None = None, pvp: bool = False, log=None,
             route: dict | None = None) -> _Ctx:
    """What advise() and instance_loot() score with: the class, the spec's
    weights in the mode, what is worn."""
    if not ref.available:
        raise GearError(ref.why)
    cls = class_token(sheet.get("class"))
    if not cls or cls not in (ref.weights.get("specs") or {}):
        raise GearError("no class on the character sheet yet" if not cls else f"no stat weights for {cls}")
    if not level:
        raise GearError("no level for this character yet")
    level = int(level)
    key, how = resolve_spec(ref, cls, sheet, spec, weights)
    mode = mode_key(mode)
    w, scoring = weights_for(ref, cls, key, mode)
    if w is None:
        raise GearError(f"no stat weights for {cls} {key}")
    from wowstats import gearfit

    points = gearfit.spec_points(ref, cls, key, sheet)
    race_id = classguide.race_bit(sheet.get("race"), sheet.get("faction")).bit_length() or None
    prof = (ref.gear.get("PROFICIENCY") or {}).get(cls)
    dual = can_dual_wield(prof, level)
    who = Who(cls=cls, level=level, side=SIDES.get(str(sheet.get("faction") or "").lower()), race_id=race_id,
              professions={str(p.get("name") or "").lower(): p.get("rank") for p in sheet.get("professions") or []
                           if p.get("name") and isinstance(p.get("rank"), (int, float))},
              skill_ranks={int(sid): int(p["rank"]) for p in sheet.get("professions") or []
                          if (sid := _num(p.get("skill_id")) or crafting.line_of(p.get("name")))
                          and isinstance(p.get("rank"), (int, float))},
              done=set(done or ()), zone=zone, recipes=recipe_lists(sheet.get("recipes")),
              reps=sheet.get("reps"), pvp_rank=sheet.get("pvp_rank"), pvp=bool(pvp), pvp_mode=mode == "pvp",
              today=_today())
    side = next((k.title() for k, v in SIDES.items() if v == who.side), None)
    who.pos = questwork.Position(level=level, side=side, guide=(route or {}).get("guide"),
                                 step=(route or {}).get("step"), done=who.done, log=set(log or ()))

    items = [i for i in sheet.get("items") or [] if isinstance(i, dict)]
    worn = worn_from(ref, items, w, sheet.get("worn_stats"))
    worn_names = {i.get("slot"): i.get("name") for i in items}
    return _Ctx(cls, level, key, how, w, scoring, dual, who, worn, worn_names, points, mode,
                (scoring or {}).get("style") or "any")


def recipe_lists(rows) -> dict[str, set]:
    """The shipper's `recipes` ([{profession, known: [spell IDs]}], from
    DataStore_Crafts) -> {profession (lower case): known spell IDs}. Only a
    profession whose recipe window was scanned is listed."""
    out: dict[str, set] = {}
    for r in rows or []:
        if isinstance(r, dict) and r.get("profession") and isinstance(r.get("known"), list):
            out[str(r["profession"]).lower()] = {int(n) for x in r["known"] if isinstance(n := _num(x), int)}
    return out


def _json_list(v):
    """A list as the store holds it (a JSON string after /ingest), else None."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, list) else None


def item_counts(v) -> dict[int, int] | None:
    """A wow-bags `bags_n`/`bank_n` ([[item ID, count], ...]) -> {item ID:
    count}, None when the event has none (a shipper before the counts):
    unknown, never zero."""
    pairs = _json_list(v)
    if pairs is None:
        return None
    out: dict[int, int] = {}
    for p in pairs:
        if isinstance(p, list) and len(p) == 2 and isinstance(i := _num(p[0]), int) \
                and isinstance(n := _num(p[1]), int) and n > 0:
            out[i] = out.get(i, 0) + n
    return out


def newest_bags(rows: list[dict]) -> dict | None:
    """The newest wow-bags event's items ({bags, bank, counts, t, at}),
    None when the shipper never sent one (before 0.3.2, or no Syndicator).
    `bags`/`bank` are sorted item IDs; `counts` is {bags, bank}: each
    {item ID: count} from the same event as its list, None when that event
    has no counts (a shipper before them) or the list itself is unknown.
    `t` is the newest event's time, `at` each part's own ({bags, bank}:
    _time): a bank from an older event keeps that event's time (review 6)."""
    from wowstats.stores import parse_t

    mine = [r for r in rows if r.get("feed") == "wow-bags"]
    r = stores.newest_with(mine, BAGS_PARTS)
    if not r:
        return None
    src = stores.part_rows(mine, BAGS_PARTS)
    ids, counts = {}, {}
    for k in BAGS_PARTS:
        v = _json_list(r.get(k))
        # Never read (no list): unknown, not empty (evidence property (e)).
        ids[k] = sorted({int(n) for x in v if isinstance(n := _num(x), int)}) if v is not None else None
        counts[k] = item_counts((src.get(k) or {}).get(BAGS_COUNTS[k])) if ids[k] is not None else None
    return {**ids, "counts": counts, "t": parse_t(r.get("_time")).timestamp(), "at": dict(r.get("part_at") or {})}


def held_items(held: dict | None) -> list[dict]:
    """One row per item the bags or the bank hold (newest_bags' answer):
    {item_id, name, bags, bank, total}, by item id. `bags`/`bank` are the
    counts there (0 when the list is known and lacks it), None when that
    container's counts are unknown (a shipper before them, or a bank never
    opened). `total` is bags + bank over the containers read; None when a
    container read has no counts."""
    if not held:
        return []
    counts = held.get("counts") or {}
    ids = sorted(set(held.get("bags") or ()) | set(held.get("bank") or ()))
    out = []
    for i in ids:
        row = {"item_id": i, "name": forever.item_name(i)}
        known = True
        for k in BAGS_PARTS:
            c = counts.get(k)
            if held.get(k) is None:
                row[k] = None
            elif c is None:
                row[k], known = None, False
            else:
                # In the list but not in the counts: one (a slot without a count).
                row[k] = c.get(i, 1 if i in held[k] else 0)
        row["total"] = (row["bags"] or 0) + (row["bank"] or 0) if known else None
        out.append(row)
    return out


def is_held(u: dict) -> bool:
    """An upgrade the character already has in its bags or bank, with the
    other hand's part too when it is a weapon setup."""
    w = u.get("with") or {}
    partner = w.get("kind") != "get" or all(s.get("kind") in HELD for s in w.get("sources") or [{}])
    return bool(u.get("sources")) and u["sources"][0].get("kind") in HELD and partner


def on_sale(u: dict) -> bool:
    """An upgrade the auction house sells now (wowstats.ahsource)."""
    return any(s.get("kind") == "ah" for s in u.get("sources") or [])


def held_where(held: dict | None) -> dict[int, str]:
    """item ID -> "bags" or "bank" from a wow-bags event's lists
    (wowstats.plan.facts.newest_bags' shape); the bags win when both hold it."""
    out: dict[int, str] = {}
    for k in ("bank", "bags"):
        for i in (held or {}).get(k) or []:
            if (n := _num(i)) is not None:
                out[int(n)] = k
    return out


# Names of items that were never meant to be worn (test, placeholder and
# creature-only rows the client's tables keep).
_NOT_LOOT = re.compile(r"^\(?OLD|^(?i:monster|deprecated|qa ?test|\[ph|placeholder|unused|test(?!ament))"
                       r"|(?i:\b(test|debug)\b)")


def unsourced(ref: Reference) -> list[tuple[int, dict]]:
    """Gear with stats and no source we know (owner, 2026-11-24: shown in the
    BiS like ForeverDB does, "where: unknown", never as an upgrade to go and
    get): uncommon or better, not a random-suffix base (its stats are a
    guess), not a placeholder, and in Forever's own client (an item only
    Classic Era describes may not exist there)."""
    if ref.nowhere is None:
        sources = ref.data.get("sources") or {}
        era = set(ref.data.get("era_only") or ())
        names = ref.data.get("names") or {}
        out = []
        for sid, info in (ref.data.get("items") or {}).items():
            if (sid in sources or sid in era or not 2 <= (info.get("quality") or 0) <= 5 or not info.get("stats")
                    or _NOT_LOOT.search(names.get(sid) or "") or suffix_base(ref, int(sid), info)):
                continue
            out.append((int(sid), info))
        ref.nowhere = out
    return ref.nowhere


def advise(ref: Reference, sheet: dict, *, level: int, zone: str | None = None, done: set | None = None,
           spec: str | None = None, limit: int = 25, weights: str | None = None, every: bool = False,
           held: dict | None = None, market: dict | None = None, mode: str | None = None,
           pvp: bool = False, log=None, route: dict | None = None) -> dict:
    """The gear advice for one character.

    sheet: the newest wow-gear event (items, class, race, faction, spec,
    professions). every: also
    return `upgrades_all`, every upgrade before the per-slot cap (the
    analysis' upgrade_candidates and the planner's dungeon ranking need the
    drops a capped list leaves out). held: the newest wow-bags lists
    ({bags, bank}); an item held there has one source, {"kind": "bags"} or
    {"kind": "bank"}: equip it, or take it out of the bank first. market:
    wowstats.ahsource.market(): an item on sale now gets an "ah" source (one
    with no other source the character can use becomes a candidate too),
    with its price against his gold; the answer gets `ah`. mode: the
    weights' mode, Leveling (the default), PvP or PvE (mode_key). pvp: PvP
    rewards (a rank's or a battleground reputation's gear) count as sources;
    off by default, always on in the PvP mode (Who.pvp). log: the quests in
    the character's log; route: its guide position ({guide, step}, the
    Recorder's newest step): a quest source's work reads both
    (wowstats.questwork)."""
    cx = _context(ref, sheet, level, zone, done, spec, weights, mode, pvp, log, route)
    cls, level, key, how, w, scoring = cx.cls, cx.level, cx.key, cx.how, cx.w, cx.scoring
    dual, min_upgrade, who, worn = cx.dual, MIN_UPGRADE, cx.who, cx.worn
    worn_names = cx.worn_names

    # Every item this character can get, with its best option (compare) and
    # its options per slot.
    cands = []
    gated: list[tuple[int, dict, list]] = []
    holding = held_where(held)
    who.bags = {i for i, where in holding.items() if where == "bags"}
    sale = (market or {}).get("items") or {}
    money = (market or {}).get("money_copper")
    # What each craft takes, priced (wowstats.crafting): the recipe, the
    # skill-up on Polestar's route, the mats against the bags and bank, the
    # auction house the other way. Once per recipe and item.
    crafter = crafting.who_from(sheet, held, market)
    plans: dict[tuple, dict | None] = {}
    for item_id, where in holding.items():
        info = item_for(ref, item_id, w)
        if not info:  # not gear, or gear the advice doesn't score
            continue
        usable, _, min_level = usability(ref, info, cls, level, who)
        if usable and min_level <= level + SOON_LEVELS:
            cands.append({"id": item_id, "info": info, "at": min_level, "sources": [{"kind": where}],
                          "effort": EASY, "here": False})
    for sid, srcs in (ref.data.get("sources") or {}).items():
        item_id = int(sid)
        base = ref.item(item_id)
        if not base or item_id in holding:
            continue
        info = item_for(ref, item_id, w)
        usable, _, min_level = usability(ref, info, cls, level, who)
        if not usable:
            continue
        # The other side's reputation and its quartermaster, and PvP rewards
        # while they don't count, are left out source by source (_source:
        # foreverdb_sources gives a reputation's vendor its side and its
        # reputation, so Talisman of Arathor isn't an "easy" 72s buy for a
        # character who never queued).
        here_srcs = _dedupe([x for x in (_source(ref, s, who, info, item_id) for s in srcs) if x])
        for x in here_srcs:
            if x["kind"] == "craft":
                k = (x.get("spell_id"), item_id)
                if k not in plans:
                    plans[k] = crafting.brief(crafting.plan(x, item_id, crafter))
                if plans[k]:
                    x["plan"] = plans[k]
        if not (who.pvp or who.pvp_mode) and any(is_pvp(s) for s in srcs):
            # A PvP rank's quartermaster goes with its rank (ForeverDB lists
            # both: Master Sergeant's Insignia, Officer Areyn and rank 3).
            here_srcs = [x for x in here_srcs if x["kind"] != "vendor"]
        if item_id in sale and buyable(base, level):
            here_srcs.append(_listing(sale[item_id], money))
        if not here_srcs:
            continue
        here_srcs = by_effort(here_srcs)
        selected = action_source(here_srcs, level)
        if selected and selected is not here_srcs[0]:
            here_srcs = [selected, *(s for s in here_srcs if s is not selected)]
        effort = item_effort(here_srcs)
        if effort == LONG_SHOT and level < ENDGAME_LEVEL:
            # A pipe dream (a 0.03% Classic drop, a PvP rank out of reach)
            # with no auction house listing: while levelling, the slot's
            # best possible, shown with its odds, never an upgrade to chase
            # (owner, 2026-11-24). At ENDGAME_LEVEL it competes on its score.
            gated.append((item_id, info, here_srcs))
            continue
        at = max(min_level, min(_source_level(x) for x in here_srcs))
        if at > level + SOON_LEVELS:
            continue
        cands.append({"id": item_id, "info": info, "at": at, "sources": here_srcs, "effort": effort,
                      "here": any(in_zone(x, zone) for x in here_srcs)})

    # On sale, and in no source Polestar knows (most world-drop greens).
    for item_id, s in sale.items():
        info = ref.item(item_id)
        if (not info or item_id in holding or str(item_id) in (ref.data.get("sources") or {})
                or not buyable(info, level)):
            continue
        usable, _, min_level = usability(ref, info, cls, level, who)
        if usable and min_level <= level + SOON_LEVELS:
            srcs = by_effort([_listing(s, money)])
            cands.append({"id": item_id, "info": info, "at": min_level, "sources": srcs,
                          "effort": item_effort(srcs), "here": False})

    gear_score = sum(worn.score(sl) for sl in EQUIPPED_SLOTS)
    from wowstats import gearfit

    fits: dict[int, dict] = {}

    def fit_of(c: dict) -> dict:
        # Who the item is built for and how it fits this spec (wowstats.gearfit):
        # a label on every row, and the factor the ranking applies.
        if c["id"] not in fits:
            fits[c["id"]] = gearfit.fit(ref, c["info"], cls, key, how, level, w, c["sources"], cx.points, cx.mode)
        return fits[c["id"]]

    def priced(srcs: list[dict], gain) -> list[dict]:
        return [ahsource.source(s, gain, market or {}) if s["kind"] == "ah" else s for s in srcs]

    stand: dict[int, dict] = {}

    def out(c: dict, r: dict) -> dict:
        # Against a random-suffix item without Battle.net's stats the gain is
        # against its likely suffix: no percent for a guess.
        vs = {r["slot"]} | ({OFF} if c["info"]["inv"] == 17 else set())
        unknown = bool(vs & worn.unknown)
        return {**_item_out(ref, c["id"], c["info"]), "level": c["at"], "slot": r["slot"],
                "bis": None if unknown else bis_gain(stand.get(r["slot"]), r),
                **(dict(NO_BADGE) if unknown else bis_badge(stand.get(r["slot"]), r)),
                "slot_name": SLOT_NAMES.get(r["slot"]), "score": _r(r["new"], 2), "gain": _r(r["delta"], 2),
                # The gain as a share of everything worn: "+13.6, 4% of your
                # gear". Against a weak item the percent alone reads +427%.
                "gain_share_pct": _r(r["delta"] / gear_score * 100) if gear_score > 0 else None,
                "gain_pct": None if unknown else _r(r["pct"]), "fills_empty": r["empty"], "vs_unknown": unknown,
                "here": c["here"], "sources": priced(c["sources"], _r(r["delta"], 2)),
                "gates": gates(c["at"], level, c["sources"]), "fit": fit_of(c),
                "effort": EFFORT[c.get("effort", EASY)],
                # What the ranking sorts on: the gain, a little less for an item
                # built for someone else, less again when a group usually hands
                # it to them, less again the harder it is to get while levelling
                # (effort_penalty, on its whole score). The gain shown stays the gain.
                "value": _r(ranking_value(r["delta"], r["new"], c.get("effort", EASY), fit_of(c)["factor"],
                                          level), 2)}

    # Weapons are set against weapons: a two-hander against main hand + off
    # hand; a one-hand piece with what the other hand wears, and against a
    # worn two-hander with the best other hand the character holds or can
    # get and wear now (Gear.lua pairs it with the best piece in the bags).
    setups = WeaponSetups(cands, worn, w, dual, level, worn_names, cx.style)

    # Per slot: the best now and the best coming within SOON_LEVELS.
    per_slot: dict[int, dict] = {}
    for c in cands:
        if c["info"]["inv"] in WEAPON_INV:
            continue
        for o in options(c["info"], c["id"], worn, w, dual, every_slot=True):
            if worn.ids.get(o["slot"]) == c["id"]:
                continue
            bucket = "now" if c["at"] <= level else "soon"
            slot = per_slot.setdefault(o["slot"], {"now": [], "soon": []})
            slot[bucket].append((o["delta"], -c["id"], c, o))

    # The BiS also counts gear nobody says where to get (unsourced): its own
    # bucket, so it never becomes an upgrade or a pick. Weapons stay out (a
    # setup needs both hands' sources to mean anything).
    unknown_hands: list[tuple[float, int, dict, int, list]] = []
    for item_id, info, srcs in [(i, x, []) for i, x in unsourced(ref)] + gated:
        if item_id in holding:
            continue
        usable, _, at = usability(ref, info, cls, level, who)
        if not usable or at > level:
            continue
        if info["inv"] in WEAPON_INV:
            if info["inv"] in (13, 17, 21):  # a two-hander, or a main hand with the best off hand there is
                unknown_hands.append((score_with(info, w), item_id, info, at, srcs))
            continue
        c = {"id": item_id, "info": info, "at": at, "sources": srcs, "here": False}
        for o in options(info, item_id, worn, w, dual, every_slot=True):
            if worn.ids.get(o["slot"]) != item_id:
                per_slot.setdefault(o["slot"], {"now": [], "soon": []}).setdefault("unknown", []).append(
                    (o["delta"], -item_id, c, o))

    def pick(slot: int, bucket: str, exclude: frozenset = frozenset()):
        # ForeverDB's order: the score, then the higher item level, then the
        # lower ID; each score less its effort's penalty (effort_penalty):
        # while levelling an easier item wins unless the harder one is
        # clearly better; at ENDGAME_LEVEL the score alone.
        def adj(t) -> float:
            return t[3]["new"] * (1 - effort_penalty(t[2].get("effort", EASY), level))

        ranked = [t for t in sorted((per_slot.get(slot) or {}).get(bucket) or [],
                                    key=lambda t: (adj(t), t[2]["info"].get("ilvl") or 0, t[1]), reverse=True)
                  if t[2]["id"] not in exclude]
        better = [t for t in ranked if t[0] > 0]
        if not better:
            return max(ranked, key=lambda t: (t[0], t[2]["info"].get("ilvl") or 0, t[1])) if ranked else None
        # Among the close ones, the one the upgrade list ranks first: its
        # value (ranking_value: the gain less the effort's share, times
        # gearfit's factor). Only those within the largest factor can change
        # places. The factor once multiplied the whole score here, so a 43%
        # dungeon drop (group loot, x0.85) lost 15% of its whole score and a
        # craft the character can't make yet beat it on a smaller gain (MCP
        # eval pve_shoulder, 2026-11-24: Green Silken Shoulders over Bloodmage Mantle).
        close = [t for t in better if adj(t) >= adj(better[0]) * gearfit.CLASS * gearfit.GROUP]
        return max(close, key=lambda t: (ranking_value(t[0], t[3]["new"], t[2].get("effort", EASY),
                                                       fit_of(t[2])["factor"], level),
                                         t[0], t[2]["info"].get("ilvl") or 0, t[1]))

    picks: dict[tuple, tuple | None] = {}
    for bucket in ("now", "soon"):
        for slot in EQUIPPED_SLOTS:
            if slot not in PAIRED:
                picks[(slot, bucket)] = pick(slot, bucket)
        for a, b in PAIRS:
            # Gear.lua compare: an empty slot first, else the weaker of the two
            # takes the best item; the other gets the best one left (a quest
            # reward is one item, worn once).
            # Never the ring the other hand already wears: compare() calls it
            # equipped, and _pair's BiS leaves it out (the doll offered the
            # worn trinket again for an empty trinket slot).
            weak = a if a not in worn.ids else b if b not in worn.ids else (b if worn.score(b) < worn.score(a) else a)
            strong = b if weak == a else a
            on = frozenset(worn.ids[x] for x in (a, b) if x in worn.ids)
            pw = picks[(weak, bucket)] = pick(weak, bucket, on)
            picks[(strong, bucket)] = pick(strong, bucket, on | ({pw[2]["id"]} if pw else set()))
    # Where each worn item stands against the best the slot can hold now
    # (owner, 2026-11-21: "show bis or second or a percentage of bis").
    stand.update(standings(per_slot, worn, setups, level, gear_score))
    # The weapons' best possible (unsourced, a long shot), shown apart: the
    # standing measures against what can be got (owner, 2026-11-24).
    hands_unknown = _unknown_hands(ref, unknown_hands, setups, level)
    weapon_rows = {"now": setups.row("now", out), "soon": setups.row("soon", out)}

    # Per slot, the pair the page leads with (owner, 2026-11-25: "this is
    # what is the BiS, here is the realistic pick"): the item that scores
    # most at this level whatever it takes (a long shot, gear nobody says
    # where to get, an auction listing out of the gold), and the ranking's
    # pick with what makes it win.
    def bis_item(c: dict, r: dict) -> dict:
        row = out(c, r)
        row["effort"] = EFFORT[c.get("effort", LONG_SHOT)]  # the unknown bucket's are long shots
        return row

    def top_of(slot: int, exclude: set) -> tuple | None:
        # Gear nobody says where to get stays out: no action to give, and
        # unknown is not open (endgame content, owner 2026-11-25).
        rows = [t for b in ("now", "unknown") for t in (per_slot.get(slot) or {}).get(b) or ()
                if t[2]["id"] not in exclude and t[2]["sources"]]
        return max(rows, key=lambda t: (t[3]["new"], t[2]["info"].get("ilvl") or 0, t[1]), default=None)

    def worn_bis(slot: int) -> dict:
        return {**_item_out(ref, worn.ids[slot], worn.infos.get(slot), worn_names.get(slot)),
                "slot": slot, "slot_name": SLOT_NAMES.get(slot), "score": _r(worn.score(slot), 2), "worn": True}

    bis_rows: dict[int, dict | None] = {}
    for slot in EQUIPPED_SLOTS:
        if slot in PAIRED or slot in (MAIN, OFF):
            continue
        t = top_of(slot, set())
        if slot in worn.ids and (t is None or t[3]["new"] <= worn.score(slot) + EPS):
            bis_rows[slot] = worn_bis(slot)
        else:
            bis_rows[slot] = bis_item(t[2], t[3]) if t else None
    for a, b in PAIRS:
        # The best two different items, as _pair: a worn one among them is
        # its slot's BiS; the others go weakest slot first, as the picks.
        on = {worn.ids[x] for x in (a, b) if x in worn.ids}
        first = None
        got: dict[int, dict | None] = {}
        entries = [(worn.score(x), 1, x) for x in (a, b) if x in worn.ids]
        t1 = max([x for x in (top_of(a, on), top_of(b, on)) if x], key=lambda t: t[3]["new"], default=None)
        if t1:
            first = t1[2]["id"]
            t2 = max([x for x in (top_of(a, on | {first}), top_of(b, on | {first})) if x],
                     key=lambda t: t[3]["new"], default=None)
            entries += [(t[3]["new"], 0, t) for t in (t1, t2) if t]
        top2 = sorted(entries, key=lambda e: -e[0])[:2]
        for e in top2:
            if e[1] == 1:
                got[e[2]] = worn_bis(e[2])
        free = [e[2] for e in top2 if e[1] == 0]
        rest = sorted([x for x in (a, b) if x not in got], key=lambda x: worn.score(x) if x in worn.ids else -1)
        for slot, t in zip(rest, free, strict=False):
            r = next((o for o in options(t[2]["info"], t[2]["id"], worn, w, dual, every_slot=True)
                      if o["slot"] == slot), t[3])
            got[slot] = bis_item(t[2], r)
        # Where the picks put the same two items the other way round, follow them.
        pa, pb = (picks.get((x, "now")) for x in (a, b))
        ida, idb = ((p[2]["id"] if p and p[3]["delta"] > 0 else None) for p in (pa, pb))
        ga, gb = ((got.get(x) or {}).get("item_id") for x in (a, b))
        if (ida and ida == gb and ga != ida) or (idb and idb == ga and gb != idb):
            got[a], got[b] = got.get(b), got.get(a)
            for x in (a, b):
                if got.get(x) and not got[x].get("worn"):
                    got[x] = {**got[x], "slot": x}
        for x in (a, b):
            bis_rows[x] = got.get(x)
    raw_weapons = setups.row("now", out, raw=True)
    raw_total = (raw_weapons or {}).get("setup_score") or setups.current
    if hands_unknown and hands_unknown["sources"] and hands_unknown["score"] > raw_total + EPS:
        bis_rows[MAIN] = {**hands_unknown, "slot": MAIN, "slot_name": SLOT_NAMES[MAIN],
                          "setup_score": hands_unknown["score"]}
    elif raw_weapons is not None:
        bis_rows[MAIN] = raw_weapons
    elif worn.ids.get(MAIN) or worn.ids.get(OFF):
        hand = MAIN if worn.ids.get(MAIN) else OFF
        bis_rows[MAIN] = {**worn_bis(hand), "setup_score": _r(setups.current, 2)}
    else:
        bis_rows[MAIN] = None
    bis_rows[OFF] = None
    for row in bis_rows.values():
        if row and not row.get("worn"):
            row["cost"] = ah_cost(row.get("sources"), market)

    def part_out(p: dict | None) -> dict | None:
        if p is None:
            return None
        if p["c"] is None:
            return {"item_id": p["id"], "name": worn_names.get(MAIN if p["role"] != "off" else OFF)
                    or forever.item_name(p["id"]), "score": _r(p["score"], 2), "have": "worn"}
        srcs = p["c"]["sources"]
        held_in = srcs[0]["kind"] if srcs and all(x["kind"] in HELD for x in srcs) else None
        return {**_item_out(ref, p["c"]["id"], p["c"]["info"]), "score": _r(p["score"], 2),
                "have": held_in or "get", "level": p["c"]["at"], "sources": [] if held_in else srcs[:2]}

    kinds = setups.by_kind(level)
    weapon_setups = []
    if "two" in kinds:
        weapon_setups.append({"setup": "two-hander", "score": _r(kinds["two"][0], 2), "worn": setups.worn_two,
                              "parts": [part_out(kinds["two"][1])]})
    if "pair" in kinds:
        weapon_setups.append({"setup": "main hand + off hand", "score": _r(kinds["pair"][0], 2),
                              "worn": not setups.worn_two and bool(worn.ids.get(MAIN)),
                              "parts": [x for x in (part_out(kinds["pair"][1]), part_out(kinds["pair"][2])) if x]})
    slots_out = []
    for slot in EQUIPPED_SLOTS:
        cur_id = worn.ids.get(slot)
        cur_info = worn.infos.get(slot)
        if slot in (MAIN, OFF):
            now, soon = weapon_rows["now"], weapon_rows["soon"]
            best = now if now and now["slot"] == slot else None
            nxt = soon if soon and soon["slot"] == slot and (
                not now or soon["setup_score"] > now["setup_score"]) else None
        else:
            now, soon = picks[(slot, "now")], picks[(slot, "soon")]
            best = out(now[2], now[3]) if now and now[3]["delta"] > 0 else None
            # The next one counts when it beats both what is worn and the best now.
            nxt = soon if soon and soon[3]["delta"] > 0 and (not now or soon[3]["new"] > now[3]["new"]) else None
            nxt = out(nxt[2], nxt[3]) if nxt else None
        bis = bis_rows.get(slot)
        # Nothing for the slot matters much (mark_negligible): no BiS and no
        # pick against it, the slot's line instead; best/next keep their points.
        negligible = (stand.get(slot) or {}).get("negligible")
        if negligible:
            bis = None
        pick_row = (weapon_rows["now"] if slot == MAIN else None) if slot in (MAIN, OFF) else best
        if pick_row is not None and not pick_row.get("cost"):
            pick_row["cost"] = ah_cost(pick_row.get("sources"), market)
        slots_out.append({
            # The slot's BiS (the most points at this level, whatever it
            # takes) and the pick against it (pair_why); weapons on the main
            # hand, as one setup.
            "bis": bis, "pick": pair_why(bis, pick_row, level) if bis else None,
            # The slot's BiS when it is gear nobody says where to get: shown
            # with "where": "unknown", never as an upgrade (unsourced).
            "bis_unknown": (None if negligible else hands_unknown if slot == MAIN
                            else None if slot == OFF
                            else _unknown_bis(ref, per_slot.get(slot), slot, worn, picks.get((slot, "now")))),
            "slot": slot, "slot_name": SLOT_NAMES[slot], "negligible": negligible,
            "standing": standing_out(stand.get(slot)) if cur_id else None,
            "current": ({**_item_out(ref, cur_id, cur_info, worn_names.get(slot)),
                         "score": _r(worn.score(slot), 2), "known": cur_info is not None,
                         "random_suffix": slot in worn.unknown,
                         # How the worn item fits the spec it's scored for, the
                         # same label an upgrade carries: a Shadow circlet on a
                         # Holy priest says so (seeded evaluation, wrong_spec_gear).
                         "fit": gearfit.fit(ref, cur_info, cls, key, how, level, w, [], cx.points, cx.mode)
                         if cur_info else None} if cur_id else None),
            "best": best, "next": nxt})

    # Next upgrades: Polestar's compare per item, one pick per quest's reward
    # choices (Rewards.lua RankChoices: an upgrade first, then the larger
    # gain, the higher score, the lower item ID). Weapons come as setups.
    ups = []
    for c in cands:
        if c["info"]["inv"] in WEAPON_INV:
            r = setups.compare(c)
        else:
            r = compare(c["info"], c["id"], worn, w, dual)
        if r is None or r.get("equipped") or r["delta"] <= 0:
            continue
        if c["at"] <= level and not is_upgrade(r, c["at"], level, min_upgrade):
            continue
        if c["at"] > level and r.get("pct") is not None and r["pct"] < min_upgrade:
            continue
        ups.append((c, r))
    best_of_quest: dict[int, tuple] = {}
    for c, r in ups:
        for s in c["sources"]:
            if s["kind"] == "quest" and s["choices"] > 1:
                cur = best_of_quest.get(s["quest_id"])
                rank = (r["delta"], r["score"], -c["id"])
                if cur is None or rank > cur[0]:
                    best_of_quest[s["quest_id"]] = (rank, c["id"])
    upgrades = []
    shown_setups: set = set()
    for c, r in ups:
        lead = r.get("lead") or c
        at = max(c["at"], lead["at"])
        c = lead
        srcs = [s for s in c["sources"]
                if not (s["kind"] == "quest" and s["choices"] > 1 and best_of_quest.get(s["quest_id"], (0, c["id"]))[1]
                        != c["id"])]
        if not srcs:
            continue
        u = {**out({**c, "at": at, "sources": srcs}, r), "now": at <= level}
        if "with_part" in r:
            u["with"] = setups.partner(r, out)
            mate = u["with"] or {}
            if (level < ENDGAME_LEVEL and mate.get("kind") == "get" and mate.get("effort")
                    and EFFORT.index(mate["effort"]) > EFFORT.index(u["effort"])):
                # Half a weapon setup is as hard as the other half to get
                # (Orb of the Forgotten Seer needs a one-hander first): the
                # setup takes the harder effort, and ranks on it while
                # levelling (at ENDGAME_LEVEL the score decides).
                e = EFFORT.index(mate["effort"])
                u["effort"] = EFFORT[e]
                u["value"] = _r(ranking_value(r["delta"], r["new"], e, (u.get("fit") or {}).get("factor", 1.0),
                                              level), 2)
            # A main hand and its off hand are one setup: listed once.
            pair = frozenset((c["id"], (u["with"] or {}).get("item_id")))
            if pair in shown_setups:
                continue
            shown_setups.add(pair)
        upgrades.append(u)
    # By the share of BiS (owner, 2026-11-21): the slot furthest from its
    # best first (bis_order); in a slot, and for slots with no BiS to measure
    # against, the biggest gain in points (a percent says little against a
    # weak item), gains against a random-suffix item (scored on its base
    # only) after those.
    upgrades.sort(key=lambda u: (not u["now"], bis_order(u), 1 if u["vs_unknown"] else 0, -(u["value"] or 0),
                                 u["item_id"]))
    alternatives(upgrades)

    # At most PER_SLOT a slot (a pair of ring or trinket slots counts as one):
    # the list is for going out and getting things, twenty hats aren't.
    seen: dict[str, int] = {}
    capped = []
    bought: set[str] = set()
    # One already in the bags or the bank stays whatever the cap: it is the
    # quickest way to that gain. So does the best one on sale a slot: a
    # purchase away, often below the drops in points.
    for u in upgrades:
        k = SLOT_NAMES.get(u["slot"], "")
        sale_here = on_sale(u) and k not in bought
        if is_held(u) or seen.get(k, 0) < PER_SLOT or sale_here:
            seen[k] = seen.get(k, 0) + 1
            capped.append(u)
            if on_sale(u):
                bought.add(k)

    label = next((s["label"] for s in specs_of(ref, cls) if s["key"] == key), key)
    extra = {"upgrades_all": upgrades} if every else {}
    if market is not None:
        extra["ah"] = ahsource.summary(market, capped)
    return {**extra,
        "class": cls, "level": level, "zone": zone,
        "spec": {"key": key, "label": label, "source": how, "choices": specs_of(ref, cls),
                 # None: no spec picked and no talents, for a class whose specs
                 # differ in role (the fit labels then ask to pick one).
                 "role": gearfit.known_role(ref, cls, key, how),
                 "why": spec_why(ref, cls, key, label, how, sheet)},
        "weights": {k: _r(v, 4) for k, v in sorted(w.items()) if v},
        # The same weights as text for the Forever Leveling BiS addon
        # (wowstats.gearexport): the page's copy buttons, MCP gear `export`.
        "export": _export(cls, key, cx.mode),
        # Leveling, PvP or PvE (owner, 2026-11-23), and whose weights they are:
        # every score shown credits ForeverDB.net.
        "mode": modes_out(cx.mode), "scoring": scoring, "source": dict(CREDIT),
        # Whether PvP rewards count (the gear page's toggle, the MCP's `pvp`):
        # `forced` in the PvP mode, where they always do.
        "pvp_rewards": {"on": who.pvp or who.pvp_mode, "forced": who.pvp_mode},
        "min_upgrade_pct": min_upgrade, "soon_levels": SOON_LEVELS,
        # Each setup's best on its own, wearable now: what a BiS list per
        # setup shows next to the one best setup the upgrades rank.
        "weapon_setups": weapon_setups,
        "gear_score": _r(gear_score, 2),
        # What each slot's BiS scores, for rows scored elsewhere
        # (instance_loot): the same badge as the advice's.
        "bis_by_slot": bis_by_slot(stand),
        "slots": slots_out,
        # The top `limit`, plus whatever is in the current zone, already in
        # the bags or bank, or on sale below that: the page can put those first.
        "upgrades": capped[:limit] + [u for u in capped[limit:] if u["here"] or is_held(u) or on_sale(u)],
        "upgrades_total": len(capped),
        "data": ref.meta(),
    }


# --- BiS and the pick -----------------------------------------------------------


def _setup_score(u: dict) -> float:
    """What a pair row scores in its slot: a weapon row its whole setup."""
    return u.get("setup_score") if u.get("setup_score") is not None else (u.get("score") or 0.0)


def _same_item(u: dict | None, v: dict | None) -> bool:
    if not u or not v or u.get("item_id") != v.get("item_id"):
        return False
    return ((u.get("with") or {}).get("item_id") or 0) == ((v.get("with") or {}).get("item_id") or 0)


def odds(u: dict) -> dict | None:
    """The easiest drop of an item: its chance and the runs it takes."""
    s = (u.get("sources") or [None])[0]  # easiest first (by_effort)
    return {"chance_pct": s.get("chance_pct"), "runs": s["expected_runs"]} if s and s.get("expected_runs") else None


def ah_cost(srcs: list[dict] | None, market: dict | None) -> dict | None:
    """An auction house listing against the character's gold and the next
    gold goal (the riding training, ahsource.mount_left): the facts and one
    action, the same words on the page and in the MCP. None without a listing."""
    ah = next((x for x in srcs or [] if x.get("kind") == "ah" and x.get("price_copper")), None)
    if ah is None:
        return None
    price = ah["price_copper"]
    money = (market or {}).get("money_copper")
    goal = (market or {}).get("mount") or None
    short = None if money is None else max(0, price - money)
    says = f"{ahsource.price_text(price)} on the auction house"
    if money is not None:
        says += f": you have {ahsource.price_text(money)}"
    if goal and goal.get("need_copper"):
        says += f"; riding at {goal['level']} needs {ahsource.price_text(goal['need_copper'])}"
    if not short:
        action = "Buy it at the auction house."
    else:
        action = f"Save {ahsource.price_text(short)} more, then buy it at the auction house."
    return {"price_copper": price, "money_copper": money, "short_copper": short,
            "goal": {"what": "riding", "level": goal["level"], "need_copper": goal["need_copper"],
                     "left_copper": goal.get("left_copper")} if goal and goal.get("need_copper") else None,
            "says": says + ".", "action": action}


def effort_detail(u: dict) -> str:
    """An item's effort with what makes it so: the runs of a drop, the gold
    of a listing, an unknown source."""
    e = u.get("effort") or ""
    o = odds(u)
    if o and e in ("hard", "long shot"):
        return f"{'a ' if e == 'long shot' else ''}{e} (about {o['runs']:,} runs)"
    cost = u.get("cost")
    if cost and cost.get("short_copper"):
        price, short = ahsource.price_text(cost["price_copper"]), ahsource.price_text(cost["short_copper"])
        return f"{e} ({price}, {short} more than you have)"
    return f"{'a ' if e == 'long shot' else ''}{e}"


def pair_why(bis: dict | None, pick: dict | None, level: int) -> dict | None:
    """The slot's pick against its BiS (owner, 2026-11-25): the same item
    ("BiS, and within reach" when nothing blocks it), or the pick, how far
    behind BiS it scores and the facts that make it win: an easier source,
    BiS's odds or price, who BiS is built for. None when nothing beats what
    is worn (the BiS row then says what it would take)."""
    if not bis or bis.get("worn") or pick is None:
        return None
    blocked = bool((bis.get("cost") or {}).get("short_copper")) or bis.get("effort") in ("hard", "long shot")
    if _same_item(bis, pick):
        return {"item_id": pick["item_id"], "name": pick.get("name"), "same": True, "behind_bis_pct": 0,
                "within_reach": not blocked,
                "reasons": [], "says": "BiS, and within reach." if not blocked else "BiS, and your pick."}
    behind = behind_pct(_setup_score(bis), _setup_score(pick))
    reasons = []
    pe, be = pick.get("effort"), bis.get("effort")
    if be and pe and EFFORT.index(pe) < EFFORT.index(be):
        reasons.append(f"{pe} where BiS is {effort_detail(bis)}")
    elif (bis.get("cost") or {}).get("short_copper"):
        reasons.append(f"BiS costs {ahsource.price_text(bis['cost']['short_copper'])} more than you have")
    bf, pf = bis.get("fit") or {}, pick.get("fit") or {}
    if bf.get("for_you") in ("spec", "class") and pf.get("for_you") in ("yes", "any"):
        reasons.append(f"BiS is built for {bf.get('built_for') or 'another role'}")
    elif bf.get("tip") and not pf.get("tip") and (bf.get("factor") or 1) < (pf.get("factor") or 1):
        reasons.append("others usually roll first on BiS")
    if not reasons and level >= ENDGAME_LEVEL:
        reasons.append("the score decides at level 60")
    says = f"{behind}% behind BiS" + (f": {'; '.join(reasons)}." if reasons else ".")
    return {"item_id": pick["item_id"], "name": pick.get("name"), "same": False, "behind_bis_pct": behind,
            "within_reach": True, "reasons": reasons, "says": says[0].upper() + says[1:]}


# --- standing against BiS ------------------------------------------------------

EPS = 1e-6


def _unknown_bis(ref: Reference, slot_pool: dict | None, slot: int, worn: Worn, now: tuple | None) -> dict | None:
    """The best unsourced item for the slot (or one only a reputation or a
    PvP rank gives out of reach: a long shot, item_effort), when it beats both what is worn and the best item
    with a source: {item, score, "where": "unknown" or its source's words,
    sources}."""
    other = {worn.ids.get(x) for pair in PAIRS if slot in pair for x in pair if x != slot}
    rows = [t for t in (slot_pool or {}).get("unknown") or () if t[2]["id"] not in other]
    if not rows:
        return None
    top = max(rows, key=lambda t: (t[3]["new"], t[2]["info"].get("ilvl") or 0, t[1]))
    known = max(worn.score(slot) if slot in worn.ids else 0.0, now[3]["new"] if now else 0.0)
    if top[3]["new"] <= known + EPS:
        return None
    srcs = top[2]["sources"]
    return {**_item_out(ref, top[2]["id"], top[2]["info"]), "score": _r(top[3]["new"], 2), "level": top[2]["at"],
            "where": source_text(srcs[0]) if srcs else "unknown", "sources": srcs,
            "effort": EFFORT[LONG_SHOT] if srcs else None}


def _unknown_hands(ref: Reference, hands: list, setups: WeaponSetups, level: int) -> dict | None:
    """The weapons' unsourced BiS: a two-hander, or a main hand with the
    best off hand the character can get, when it beats both the worn setup
    and the best setup with a source. Its score is the whole setup's."""
    kinds = setups.by_kind(level)
    pair = kinds.get("pair")
    off = pair[2]["score"] if pair and pair[2] else 0.0
    best = None
    for sc, item_id, info, at, srcs in hands:
        total = sc if info["inv"] == 17 else sc + off
        if best is None or (total, info.get("ilvl") or 0, -item_id) > (best[0], best[2].get("ilvl") or 0, -best[1]):
            best = (total, item_id, info, at, srcs)
    known = max([setups.current, *(k[0] for k in kinds.values())])
    if best is None or best[0] <= known + EPS:
        return None
    total, item_id, info, at, srcs = best
    return {**_item_out(ref, item_id, info), "score": _r(total, 2), "level": at,
            "where": source_text(srcs[0]) if srcs else "unknown", "sources": srcs,
            "effort": EFFORT[LONG_SHOT] if srcs else None,
            "setup": "two-hander" if info["inv"] == 17 else "main hand + off hand"}


def _bis_pool(slot: dict | None) -> list:
    """What a slot's BiS is measured against: everything wearable now that
    can be got (owner, 2026-11-24: "percent of BiS and standings use the
    achievable pick"). Gear with no known source and long shots (advise's
    "unknown" bucket) are the slot's best possible, shown apart
    (bis_unknown)."""
    return list((slot or {}).get("now") or ())


def _single(pool: dict[int, float], cur: float | None) -> dict | None:
    """One slot: BiS is the best score among what is worn and every item
    the character can wear now; the rank counts the items above the worn one."""
    if not pool:
        return None
    top = max([*pool.values(), cur or 0.0])
    if top <= 0:
        return None
    if cur is None:
        return {"rank": None, "pct": 0, "target": top}
    above = sum(1 for v in pool.values() if v > cur + EPS)
    return _rank(above, cur, top)


def _rank(above: int, cur: float, target: float) -> dict:
    if above == 0 or cur >= target - EPS:
        return {"rank": 1, "pct": 100, "target": target}
    # ForeverDB's "-N% vs best": never "100%" for something that isn't BiS.
    pct = 100 - behind_pct(target, cur) if target > 0 else 0
    return {"rank": 2 if above == 1 else None, "pct": pct, "target": target}


def _pair(a: int, b: int, per_slot: dict, worn: Worn) -> dict[int, dict | None]:
    """Rings or trinkets: BiS is the best two different items. A worn one
    among those two is BiS; the rest measure against the BiS item they'd
    make room for. The best item left goes where compare() and advise()'s
    picks put it: an empty slot, else the weaker worn one; the stronger
    measures against the next. The other way round (until rc.7) the weaker
    ring could read the higher share (40% next to 34%), and every ring line
    started from it while the doll said the replaced ring was the 40% one."""
    pool: dict[int, float] = {}
    for slot in (a, b):
        for _d, _i, c, o in _bis_pool(per_slot.get(slot)):
            pool[c["id"]] = max(pool.get(c["id"], 0.0), o["new"])
    pool = {i: v for i, v in pool.items() if i not in (worn.ids.get(a), worn.ids.get(b))}
    if not pool:
        return {a: None, b: None}
    for slot in (a, b):
        if slot in worn.ids:
            pool[worn.ids[slot]] = max(pool.get(worn.ids[slot], 0.0), worn.score(slot))
    ranked = sorted(pool.items(), key=lambda kv: -kv[1])
    top2 = [i for i, _ in ranked[:2]]
    out: dict[int, dict | None] = {}
    rest = []
    for slot in (a, b):
        i = worn.ids.get(slot)
        if i is not None and i in top2:
            out[slot] = {"rank": 1, "pct": 100, "target": worn.score(slot)}
        else:
            rest.append(slot)
    free = [pool[i] for i in top2 if i not in {worn.ids.get(x) for x in (a, b) if out.get(x)}]
    rest.sort(key=lambda x: worn.score(x) if x in worn.ids else -1)
    for slot, target in zip(rest, free, strict=False):
        if target <= 0:
            out[slot] = None
        elif slot not in worn.ids:
            out[slot] = {"rank": None, "pct": 0, "target": target}
        else:
            cur = worn.score(slot)
            above = sum(1 for i, v in pool.items() if i != worn.ids[slot] and v > cur + EPS)
            # No "2nd" for a pair: the runner-up to the best two reads "2nd"
            # next to a low percent (17% of BiS, 2nd), so a pair says the percent.
            out[slot] = _rank(max(above, 2), cur, target)
    for slot in (a, b):
        out.setdefault(slot, None)
    return out


def _weapons(setups: WeaponSetups, worn: Worn, level: int) -> dict | None:
    """Both hands as one setup: BiS is the best two-hander or main hand +
    off hand pair wearable now; the rank counts the setups above the worn one."""
    if not any(p["c"]["at"] <= level for p in setups.parts):
        return None
    best = setups.best_setup(level)
    if not best or best["total"] <= 0:
        return None
    cur = setups.current
    if not worn.ids.get(MAIN) and not worn.ids.get(OFF):
        return {"rank": None, "pct": 0, "target": best["total"]}
    above = 0
    for t in setups._pool("two", level):
        if t["score"] > cur + EPS:
            above += 1
    for m in setups._pool("main", level):
        if above >= 2:
            break
        for o in [None, *setups._pool("off", level, exclude=_pid(m))]:
            if m["score"] + (o["score"] if o else 0) > cur + EPS:
                above += 1
                if above >= 2:
                    break
    return _rank(above, cur, best["total"])


def negligible_says(slot: int, points: float, other: bool = False) -> str:
    """The one line a negligible slot shows in place of BiS and percents;
    `other` when the paired slot (a ring or trinket) does matter."""
    noun = SLOT_NOUNS.get(slot, "item here")
    return f"No {'other ' if other else ''}{noun} matters much at your level: +{points:.1f} points at best."


def mark_negligible(out: dict[int, dict | None], worn: Worn, gear_score: float) -> None:
    """Flag the standings of slots whose best item is worth almost nothing
    next to the worn gear (NEGLIGIBLE_SHARE): `negligible` holds the line to
    show. A slot's best is its standing's target (BiS), or what is worn when
    nothing can be got: a worn slot with nothing to measure against gets a
    standing here, or its BiS row would call the worn trinket BiS. Rings and
    trinkets go slot by slot: a real trinket next to an empty slot whose
    best is a 0.7-point one keeps its badge, and the empty one says "No
    other trinket..."; when both are negligible both say the pair's best.
    The one place that decides it: the badges (bis_badge), the slot's BiS
    and pick, the doll and the MCP read the flag."""
    if gear_score <= 0:
        return

    def best(s: int) -> float | None:
        if s in worn.unknown:
            return None
        if out.get(s):
            return out[s]["target"]
        if s in worn.ids and worn.infos.get(s) is not None:
            return worn.score(s)
        return None

    tops = {s: best(s) for s in EQUIPPED_SLOTS}
    low = {s for s, v in tops.items() if v is not None and v < NEGLIGIBLE_SHARE * gear_score}
    partner = {a: b for x, y in PAIRS for a, b in ((x, y), (y, x))}
    for s in low:
        p = partner.get(s)
        other = p is not None and p not in low and tops.get(p) is not None
        points = max(tops[x] for x in (s, p) if x in low) if p is not None else tops[s]
        says = negligible_says(s, points, other)
        out[s] = {**out[s], "negligible": says} if out.get(s) else {
            "rank": None, "pct": None, "target": tops[s], "negligible": says}


def standings(per_slot: dict, worn: Worn, setups: WeaponSetups, level: int,
              gear_score: float = 0.0) -> dict[int, dict | None]:
    """Each slot's standing against BiS (the best it can hold at this level,
    scored like the upgrades): {"rank": 1 (BiS), 2 (runner-up) or None,
    "pct": the worn score over BiS's, "target": BiS's score}. None where
    there is nothing to measure against (no item for the slot, a
    random-suffix item scored on a guess) or nothing worn is known at all
    (no sheet: every slot would read 0%)."""
    out: dict[int, dict | None] = {}
    if not worn.ids:
        return out
    for slot in EQUIPPED_SLOTS:
        if slot in PAIRED or slot in (MAIN, OFF):
            continue
        cur = worn.score(slot) if slot in worn.ids else None
        if slot in worn.unknown or (slot in worn.ids and worn.infos.get(slot) is None):
            out[slot] = None
            continue
        pool: dict[int, float] = {}
        for _d, _i, c, o in _bis_pool(per_slot.get(slot)):
            pool[c["id"]] = max(pool.get(c["id"], 0.0), o["new"])
        out[slot] = _single(pool, cur)
    for a, b in PAIRS:
        got = _pair(a, b, per_slot, worn)
        for slot in (a, b):
            bad = slot in worn.unknown or (slot in worn.ids and worn.infos.get(slot) is None)
            out[slot] = None if bad else got[slot]
    hands = {MAIN, OFF}
    if hands & worn.unknown or any(s in worn.ids and worn.infos.get(s) is None for s in hands):
        w = None
    else:
        w = _weapons(setups, worn, level)
    out[MAIN] = w
    out[OFF] = w
    mark_negligible(out, worn, gear_score)
    return out


def standing_out(st: dict | None) -> dict | None:
    """The slot's standing as the page and the MCP show it."""
    if not st:
        return None
    if st.get("negligible"):
        # No label (no doll badge, no "BiS"): the line says why; the numbers stay.
        return {"rank": st["rank"], "pct": st["pct"], "label": None, "negligible": st["negligible"]}
    label = "BiS" if st["rank"] == 1 else "2nd" if st["rank"] == 2 else f"{st['pct']}% of BiS"
    return {"rank": st["rank"], "pct": st["pct"], "label": label}


def _bis_new(r: dict) -> float | None:
    """The score a compare result (compare(), WeaponSetups.compare) puts in
    its slot, on BiS's scale: a weapon counts as its whole setup, so a lone
    one-hander or off hand has none (a two-hander is a setup on its own)."""
    if r["slot"] in (MAIN, OFF):
        return r.get("setup_score")
    return r["new"]


def _share(new: float, target: float) -> int:
    # Never "100%" for something that isn't BiS (as _rank).
    return 100 - behind_pct(target, new)


def bis_gain(st: dict | None, r: dict) -> dict | None:
    """An upgrade in BiS terms: the worn item's share of BiS and the
    candidate's ("62% -> 100% of BiS"). Weapons count as whole setups."""
    b = bis_badge(st, r)
    if b["bis_pct"] is None:
        return None
    return {"from_pct": st["pct"], "to_pct": max(b["bis_pct"], st["pct"])}


# `negligible`: the slot's plain line when nothing for it matters much at
# this level (mark_negligible), shown in place of the percents; only on the
# rows of such a slot.
BADGE = ("bis_pct", "bis_pct_worn", "delta_pct", "negligible")
NO_BADGE = {"bis_pct": None, "bis_pct_worn": None, "delta_pct": None}


def bis_badge(st: dict | None, r: dict) -> dict:
    """The badge every item carries wherever it is shown (owner, 2026-11-21):
    `bis_pct`, the item's score as a share of the best its slot can hold at
    this level (the paperdoll's number: 100 is BiS); `bis_pct_worn`, the
    same share for what is worn there now (None for an empty slot); and
    `delta_pct`, the difference. The page says "70% → 91% on your chest"
    (gain_words), never "+21%", which read as 21% more stats. Rings and
    trinkets measure in the slot compare() picks, the weaker of the two
    worn (Gear.lua). st: the slot's standing (standings(); `pct` and
    `target`), r: the item's compare result. All None without a BiS to
    measure against (no candidate, a random-suffix item worn, a lone
    one-hander): no badge, never a guess. A negligible slot
    (mark_negligible): no percents either, its line in `negligible`."""
    if st and st.get("negligible"):
        return {**NO_BADGE, "negligible": st["negligible"]}
    if not st or not st.get("target"):
        return dict(NO_BADGE)
    new = _bis_new(r)
    if new is None:
        return dict(NO_BADGE)
    pct = _share(new, st["target"])
    worn = None if r.get("empty") else st["pct"]
    return {"bis_pct": pct, "bis_pct_worn": worn, "delta_pct": pct - (worn or 0)}


def badge_of(u: dict | None) -> dict:
    """The badge fields of a row, for the answers that copy a few keys."""
    return {k: (u or {}).get(k) for k in BADGE}


def source_text(s: dict, short: bool = False) -> str:
    """One source in a few words: 'quest Tharil'zun (Redridge Mountains, req 18)'.
    A ForeverDB source says how sure it is: "seen in Forever, 16 kills",
    "Classic data, may differ in Forever" (basis_words). short: a craft says
    its next step and its gold, not its whole plan (the analysis' tables and
    brief, which have a size; the professions tool has the rest)."""
    basis = basis_words(s)
    if s["kind"] == "bags":
        return "in your bags: equip it"
    if s["kind"] == "bank":
        return "in your bank: take it out at a banker and equip it"
    if s["kind"] == "quest":
        where = ", ".join(x for x in (s.get("zone"), f"req {s['req_level']}" if s.get("req_level") else None,
                                      "dungeon quest" if s.get("dungeon") else None,
                                      f"one of {s['choices']} rewards" if (s.get("choices") or 0) > 1 else None,
                                      basis or None) if x)
        work = f": {s['work_says']}" if s.get("work_says") else ""
        return f"quest {s.get('name') or s.get('quest_id')} ({where}){work}"
    if s["kind"] == "drop":
        chance = f", {chance_words(s)}" if s.get("chance_pct") else ""
        tail = f" ({basis})" if basis else ""
        if s.get("world"):
            lv = s.get("levels_drop")
            seen = f", seen in {s['zone']}" if s.get("zone") else ""
            return f"world drop{f' from level {lv[0]}-{lv[1]} creatures' if lv else ''}{seen}{chance}{tail}"
        inst = ""
        if s.get("instance"):
            lv = s.get("levels")
            inst = f" ({s['instance']}{f', levels {lv[0]}-{lv[1]}' if lv else ''})"
        who = s.get("boss") or ("an unnamed creature, not placed yet" if s.get("npc_id") and not s.get("instance")
                                else "trash")
        foe = s.get("foe") or {}
        lv = foe.get("levels") or []
        if lv and lv[-1]:
            rank = f" {foe['rank']}" if foe.get("rank") not in (None, "normal") else ""
            who = f"{who} (level {lv[0]}{f'-{lv[1]}' if lv[1] != lv[0] else ''}{rank})"
        elif s.get("rare"):
            who = f"{who} (rare)"
        if s.get("boss") and s.get("unplaced"):
            who = f"{who}, not placed yet"
        where = f" in {s['zone']}" if s.get("zone") else ""
        if not s.get("chance_pct") and not s.get("instance") and s.get("npc_id"):
            chance = ", no drop rate known"
        return f"drop: {who}{where}{inst}{chance}{tail}"
    if s["kind"] == "ah":
        share = f", {s['gold_share_pct']:g}% of your gold" if s.get("gold_share_pct") is not None else ""
        return (f"auction house: {ahsource.price_text(s['price_copper'])}{share}, "
                f"{ahsource.seen_text(s.get('seen_days_ago'))}"
                + (f" ({s['seen_day']})" if s.get("seen_day") else "") + (" (cheap)" if s.get("cheap") else ""))
    tail = f" ({basis})" if basis else ""
    if s["kind"] == "vendor":
        price = f", {ahsource.price_text(s['price_copper'])}" if s.get("price_copper") else ""
        zone = f" in {s['zone']}" if s.get("zone") else ""
        return f"vendor: {s.get('name') or 'a vendor'}{zone}{price}{tail}"
    if s["kind"] == "reputation":
        return f"reputation: {s.get('name') or 'a faction'} {s.get('standing') or ''}".rstrip() + rep_words(s) + tail
    if s["kind"] == "pvp":
        return f"PvP: rank {s.get('rank')} reward{pvp_words(s)}{tail}"
    if s["kind"] == "container":
        what = "object" if s.get("object") else "container"
        chance = f", {s['chance_pct']}%" if s.get("chance_pct") else ""
        return f"{what}: {s.get('name') or '?'}{chance}{tail}"
    if s["kind"] != "craft":  # a kind the gear data names that has no words here
        return f"{s['kind']}: {s.get('name') or s.get('zone') or '?'}"
    p = s.get("plan") or {}
    if short and p.get("action"):
        gold = f" In all ~{ahsource.price_text(p['craft_copper'])}." if p.get("craft_copper") else ""
        buy = f" Or buy it: {ahsource.price_text(p['buy_copper'])}." if p.get("buy_copper") else ""
        return f"craft: {s.get('profession')} {s.get('skill')}; {p['action']}{gold}{buy}{tail}"
    return f"craft: {s.get('profession')} {s.get('skill')}; {craft_status(s)}{tail}"


def rep_words(s: dict) -> str:
    """What the character holds against a reputation source, in words."""
    if s.get("met"):
        return f" (you're {s.get('your_standing')})"
    if s.get("met") is None:
        return " (your standing isn't recorded: Altoholic saves it)"
    if not s.get("your_standing"):
        return " (not met yet)"
    to_go = f": {s['rep_to_go']:,} rep to go" if s.get("rep_to_go") else ""
    return f" (you're {s['your_standing']}{to_go})"


def pvp_words(s: dict) -> str:
    """What the character holds against a PvP rank source, in words."""
    if s.get("your_rank") is None:
        return " (your PvP rank isn't recorded yet)"
    if s.get("met"):
        return f" (you hold rank {s['your_rank']})"
    you = "you have no PvP rank" if s["your_rank"] == 0 else f"you're rank {s['your_rank']}"
    return f" (rank {s.get('rank')} needed, {you})"


def source_link(s: dict) -> str | None:
    """The Wowhead page of a source: the quest, the boss or vendor, the craft's
    spell, the container."""
    from wowstats.links import wowhead_url

    kind, i = {"quest": ("quest", s.get("quest_id")), "drop": ("npc", s.get("npc_id")),
               "vendor": ("npc", s.get("npc_id")), "craft": ("spell", s.get("spell_id")),
               "container": ("item", s.get("container_item_id"))}.get(s.get("kind"), (None, None))
    return wowhead_url(kind, i) if kind and isinstance(i, int) and i > 0 else None


def sources_credited(tree) -> bool:
    """Whether an answer shows a source from ForeverDB's data anywhere (a
    `via: foreverdb` source): then it carries SOURCE_CREDIT (wowstats.mcp.tools.call)."""
    stack = [tree]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            if v.get("via") == "foreverdb":
                return True
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return False


def gain_words(u: dict) -> str:
    """An item's gain in the page's words (web/app.js upText): the slot's
    BiS share from what's worn to this item, "70% → 91% on your chest",
    "empty → 91% on your chest" when nothing is worn there. Weapons are one
    setup: "on your weapons". The points when it has no badge ("+4.3
    points"); with the reason on a negligible slot (mark_negligible): "+0.7
    points, no trinket matters much at your level" (no brackets: lines sit
    inside them, "Roogug's Severed Head (+0.7 points, ...)")."""
    if u.get("negligible"):
        why = u["negligible"].split(":")[0]
        return f"+{u.get('gain') or 0:.1f} points, {why[0].lower()}{why[1:]}"
    if u.get("bis_pct") is None:
        return f"+{u.get('gain') or 0:.1f} points"
    name = u.get("slot_name") or "gear"
    slot = "weapons" if name in ("Main hand", "Off hand") else name.lower()
    worn = u.get("bis_pct_worn")
    return f"{'empty' if worn is None else f'{worn}%'} → {u['bis_pct']}% on your {slot}"


# What marks an answer as carrying a gear score (scored): a badge or BiS
# field, or the words gain_words writes.
SCORE_KEYS = frozenset({"bis_pct", "bis_pct_worn", "gear_score", "bis_by_slot", "bis_unknown", "gain_points",
                        "upgrade_gain"})
_SCORE_WORDS = re.compile(r"\d+% → \d+% on your|empty → \d+% on your|\d+% of BiS|\+\d+(\.\d)? points")


def scored(tree) -> bool:
    """Whether an answer shows a gear score anywhere (a badge, a standing, a
    BiS, an upgrade's gain): then it credits ForeverDB.net (owner,
    2026-11-24: "Their link every time as we reverse engineered the
    tables"). wowstats.mcp.tools.call adds `score_source` to such answers."""
    stack = [tree]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            for k, x in v.items():
                if k in SCORE_KEYS or (k == "standing" and isinstance(x, dict) and "pct" in x):
                    return True
                stack.append(x)
        elif isinstance(v, list):
            stack.extend(v)
        elif isinstance(v, str) and _SCORE_WORDS.search(v):
            return True
    return False


def bis_by_slot(stand: dict[int, dict | None]) -> dict[str, dict]:
    """The standings as an answer carries them (string keys, JSON), for the
    rows scored outside advise(): instance_loot() reads them back."""
    return {str(k): {"pct": v["pct"], "target": v["target"],
                     **({"negligible": v["negligible"]} if v.get("negligible") else {})}
            for k, v in stand.items() if v}


def bis_order(u: dict | None) -> tuple:
    """The ranking by BiS (owner, 2026-11-21): the slot furthest from BiS
    first; anything without a BiS to measure against after, in its old
    order. Within a slot every candidate starts from the same item and
    measures against the same BiS, so the points that break the tie (the
    gain, less for an item built for someone else: wowstats.gearfit) rank
    the one closing most of the gap first, an on-spec item before a
    slightly bigger one made for a healer."""
    b = (u or {}).get("bis")
    if not b:
        return (1, 0)
    return (0, b["from_pct"])


def _slot_group(u: dict) -> str:
    return "Weapon" if u.get("slot") in (MAIN, OFF) else SLOT_NAMES.get(u.get("slot"), "")


def alternatives(upgrades: list[dict]) -> None:
    """On each upgrade built for someone else, the best upgrade for the
    same slot that is built for this spec (or fits any), wearable as soon:
    its name, gain, level and where it comes from. The list is in ranking
    order, so the first one found is the best."""
    for u in upgrades:
        f = u.get("fit") or {}
        if f.get("for_you") not in ("spec", "class"):
            continue
        alt = next((a for a in upgrades if a is not u and a["item_id"] != u["item_id"]
                    and _slot_group(a) == _slot_group(u) and (a.get("fit") or {}).get("for_you") in ("yes", "any")
                    and (a["now"] or not u["now"])), None)
        u["fit"] = {**f, "alternative": None if alt is None else {
            **{k: alt.get(k) for k in ("item_id", "name", "quality", "gain", "level", "now", "slot_name", *BADGE)},
            "label": alt["fit"]["label"], "sources": alt.get("sources", [])[:2]}}


# --- weapons -----------------------------------------------------------------

WEAPON_INV = {13, 14, 17, 21, 22, 23}
MAIN_INV = {13, 21}


def _pid(part: dict) -> int | None:
    return part["c"]["id"] if part.get("c") else part.get("id")


def _held_part(part: dict | None) -> bool:
    """A part the character already has: worn, or in its bags or bank."""
    return part is None or part["c"] is None or all(x["kind"] in HELD for x in part["c"]["sources"])


def _lead(part: dict, partner: dict | None) -> tuple[dict, dict | None]:
    """Which of a setup's two parts the row is about: the one to go and get.
    An off hand in the bags beside a one-hander that drops in a dungeon is
    the dungeon's upgrade, worn with what the bags hold, never "equip it
    now" (a two-hander leaves the off hand nothing to hold it)."""
    if partner is not None and _held_part(part) and not _held_part(partner):
        return partner, part
    return part, partner


class WeaponSetups:
    """What goes in the hands, weighed as a whole: the best two-hander
    against the best main hand + off hand pair (Gear.lua compare: a
    two-hander against main hand + off hand; a one-hander against a worn
    two-hander together with an off-hand piece). Each part is an item the
    character can get (a candidate) or the one it wears."""

    def __init__(self, cands: list[dict], worn: Worn, w: dict, dual: bool, level: int,
                 names: dict | None = None, style: str = "any"):
        self.worn, self.level, self.names, self.style = worn, level, names or {}, style
        main = worn.infos.get(MAIN)
        self.worn_two = bool(main and main["inv"] == 17)
        self.current = worn.score(MAIN) + worn.score(OFF)
        self.parts: list[dict] = []  # {c, role, score, at}
        for c in cands:
            inv = c["info"]["inv"]
            keep = 1 - effort_penalty(c.get("effort", EASY), level)
            if inv == 17:
                sc = score_with(c["info"], w, False)
                self.parts.append({"c": c, "role": "two", "score": sc, "adj": sc * keep})
            if inv in MAIN_INV:
                sc = score_with(c["info"], w, False)
                self.parts.append({"c": c, "role": "main", "score": sc, "adj": sc * keep})
            if self._off_ok(c["info"], dual):
                sc = score_with(c["info"], w, True)
                self.parts.append({"c": c, "role": "off", "score": sc, "adj": sc * keep})
        # What is worn can stay.
        if worn.ids.get(MAIN):
            role = "two" if self.worn_two else "main"
            self.kept = {role: {"c": None, "role": role, "score": worn.score(MAIN), "adj": worn.score(MAIN),
                                "id": worn.ids[MAIN]}}
        else:
            self.kept = {}
        if worn.ids.get(OFF):
            self.kept["off"] = {"c": None, "role": "off", "score": worn.score(OFF), "adj": worn.score(OFF),
                                "id": worn.ids[OFF]}

    def _off_ok(self, info: dict, dual: bool) -> bool:
        """What the spec holds in the off hand (ForeverDB's styles): a
        dual-wielder a weapon, a shield tank a shield, anyone else whatever
        it can hold."""
        inv = info["inv"]
        if not (inv in (14, 23) or (inv in (13, 22) and dual)):
            return False
        if self.style == "dw":
            return info.get("class") == 2
        if self.style == "shield":
            return inv == 14 and info.get("subclass") == 6
        return True

    def _pool(self, role: str, max_at: int, exclude: int | None = None) -> list[dict]:
        out = [p for p in self.parts if p["role"] == role and p["c"]["at"] <= max_at and p["c"]["id"] != exclude
               and not (role == "off" and p["c"]["id"] == self.worn.ids.get(OFF))]
        if role in self.kept and self.kept[role]["id"] != exclude:
            out.append(self.kept[role])
        return out

    def _best(self, role: str, max_at: int, exclude: int | None = None, raw: bool = False) -> dict | None:
        pool = self._pool(role, max_at, exclude)
        # The score less its effort's penalty (effort_penalty), or the score
        # alone for the BiS (raw); ForeverDB's order on a tie: the higher
        # item level, then the lower ID.
        by = "score" if raw else "adj"
        return max(pool, key=lambda p: (p[by], p["c"] is None, (p["c"] or {}).get("info", {}).get("ilvl") or 0,
                                        -(_pid(p) or 0)), default=None)

    def best_setup(self, max_at: int, raw: bool = False) -> dict | None:
        """The best whole setup with parts available by level max_at,
        ForeverDB's way: the best two-hander against the best main hand and
        the best other off hand (an off-hand weapon at half its damage), a tie to the
        two-hander; a dual-wielder or a shield tank takes the pair whenever
        it has both hands, a two-hand spec the two-hander whenever it has one."""
        two = self._best("two", max_at, raw=raw)
        two = {"total": two["score"], "adj": two["adj"], "main": two, "off": None} if two else None
        # The best main hand, then the best off hand that isn't that item.
        m = self._best("main", max_at, raw=raw)
        o = self._best("off", max_at, exclude=_pid(m), raw=raw) if m else None
        pair = {"total": m["score"] + (o["score"] if o else 0), "adj": m["adj"] + (o["adj"] if o else 0),
                "main": m, "off": o} if m else None
        if raw:
            for x in (two, pair):
                if x:
                    x["adj"] = x["total"]
        if two is None or pair is None:
            return two or pair
        if self.style == "2h":
            return two
        if self.style in ("dw", "shield") and pair["off"] is not None:
            return pair
        return two if two["adj"] >= pair["adj"] else pair

    def by_kind(self, max_at: int) -> dict:
        """The best two-hander and the best main hand + off hand pair, each
        on its own (a levelling BiS list ranks each setup apart): {"two":
        (score, part), "pair": (score, main, off)}, a kind with no part left
        out."""
        out: dict = {}
        two = self._best("two", max_at)
        if two:
            out["two"] = (two["score"], two)
        for m in self._pool("main", max_at):
            o = self._best("off", max_at, exclude=_pid(m))
            total = m["score"] + (o["score"] if o else 0)
            if "pair" not in out or total > out["pair"][0]:
                out["pair"] = (total, m, o)
        return out

    def _with(self, part: dict | None, out) -> dict | None:
        """The partner in a setup, in the answer's shape."""
        if part is None:
            return {"kind": "empty"}
        if part["c"] is None:
            slot = OFF if part["role"] == "off" else MAIN
            return {"kind": "worn", "item_id": part["id"], "slot_name": SLOT_NAMES[slot],
                    "name": self.names.get(slot) or forever.item_name(part["id"])}
        return {"kind": "get", **out(part["c"], {"slot": OFF if part["role"] == "off" else MAIN, "new": part["score"],
                                                  "delta": part["score"], "pct": None, "empty": False})}

    def _r(self, slot: int, part: dict, total: float) -> dict:
        delta = total - self.current
        return {"slot": slot, "old": self.current, "new": part["score"], "delta": delta,
                "pct": delta / self.current * 100 if self.current > 0 else None,
                "empty": self.current == 0, "score": part["score"], "equipped": False, "setup_score": total}

    def row(self, bucket: str, out, raw: bool = False) -> dict | None:
        """The equipment panel's weapon line for `bucket` (now / soon): the
        part to get, on the main hand row (with its partner) or, when only
        the off hand changes, on the off-hand row. raw: the setup that
        scores most whatever it takes to get (the slot's BiS)."""
        best = self.best_setup(self.level if bucket == "now" else self.level + SOON_LEVELS, raw)
        if not best or best["total"] <= self.current + 1e-9:
            return None
        m, o = best["main"], best["off"]
        # "Next" only names a setup that needs something not yet in reach.
        if bucket == "soon" and not any(p and p["c"] is not None and p["c"]["at"] > self.level for p in (m, o)):
            return None
        if m["c"] is not None and o is not None and _lead(m, o)[0] is o:
            r = self._r(OFF, o, best["total"])
            row = out(o["c"], r)
            row["with"] = self._with(m, out)
        elif m["c"] is not None:
            r = self._r(MAIN, m, best["total"])
            row = out(m["c"], r)
            if m["role"] == "main":
                row["with"] = self._with(o, out)
        elif o and o["c"] is not None:
            r = self._r(OFF, o, best["total"])
            row = out(o["c"], r)
            row["with"] = self._with(m, out)
        else:
            return None
        row["setup_score"] = _r(best["total"], 2)
        return row

    def _partner(self, p: dict) -> dict | None:
        """The other hand for a one-hand part p. Beside a worn one-hander
        (or nothing in the hands) it is what the other hand holds now: an
        off hand is scored with the main hand worn, never with the best main
        hand on the list, which put that main hand's gain on every off hand
        (a level 33 character's 30 off hands, all scored with one unworn
        weapon). Only against a worn two-hander does a one-hand part need a
        partner it doesn't wear, and then one it can wear now."""
        other = "off" if p["role"] == "main" else "main"
        if not self.worn_two:
            kept = self.kept.get(other)
            return kept if kept and kept["id"] != p["c"]["id"] else None
        return self._best(other, self.level, exclude=p["c"]["id"])

    def compare(self, c: dict) -> dict | None:
        """compare() for one weapon: the best setup it is part of, against
        what is worn in both hands."""
        best = None
        for p in self.parts:
            if p["c"] is not c:
                continue
            if p["role"] == "two":
                total, partner = p["score"], None
            else:
                partner = self._partner(p)
                total = p["score"] + (partner["score"] if partner else 0)
            if best is None or total > best[0]:
                best = (total, p, partner)
        if best is None:
            return None
        total, p, partner = best
        lead, other = _lead(p, partner)
        r = self._r(OFF if lead["role"] == "off" else MAIN, lead, total)
        r["lead"] = lead["c"]
        if lead["role"] != "two":
            r["with_part"] = other
        return r

    def partner(self, r: dict, out) -> dict | None:
        return self._with(r.get("with_part"), out) if "with_part" in r else None


SPEC_WHY = {
    "page": "you picked it on this page",
    "server": "the site's WEIGHTS setting picks {label} for this character",
    "picked": "you picked {label} in {addon}",
    "default": "it's the usual leveling spec for a {cls}",
}


SHORT_SOURCE = {"page": "this page", "server": "site setting",
                "talents": "most talent points", "picked": "your pick", "default": "leveling default"}


def scored_for(ref: Reference, cls, sheet: dict, weights: str | None = None) -> dict | None:
    """What the gear advice scores for, for the character card: {key, label,
    source} (the advice's own order, without a page choice). None when gear
    advice is off or the class is unknown."""
    token = class_token(cls)
    if not ref.available or not token or token not in (ref.gear.get("CLASSES") or {}):
        return None
    key, how = resolve_spec(ref, token, sheet, None, weights)
    if not key:
        return None
    label = next((s["label"] for s in specs_of(ref, token) if s["key"] == key), key)
    return {"key": key, "label": label, "source": SHORT_SOURCE.get(how, how)}


def capstone_rule() -> tuple[int, int] | None:
    """(level, points) of a tree's last talent, from the knowledge base's
    talents.points: one point a level from first_level, so capstone_points
    by first_level + capstone_points - 1 (31 by 40 on Forever). None when
    the KB has no such rule: no check."""
    from wowstats.analysis import kb

    v = (kb.static_rule("talents.points") or {}).get("value") or {}
    if not v.get("first_level") or not v.get("capstone_points"):
        return None
    return v["first_level"] + v["capstone_points"] - 1, v["capstone_points"]


def capstone_check(ref: Reference, cls, key: str, level, tree_points: dict[str, int],
                   capstone: str | None = None) -> str | None:
    """One neutral line when the weights' tree can't reach its 31-point
    talent by level 40 without a respec: only from level 30, only when the
    talents are known. tree_points: tree name -> points (the frame's names).
    The respec cost isn't in any data the kit reads, so it isn't given."""
    token = class_token(cls)
    rule = capstone_rule()
    if rule is None:
        return None
    CAPSTONE_LEVEL, CAPSTONE_POINTS = rule  # noqa: N806 - the names the line reads
    if not isinstance(level, int) or not (CAPSTONE_LEVEL - 10 <= level < CAPSTONE_LEVEL) or not tree_points:
        return None
    mine = sum(p for t, p in tree_points.items() if spec_key(ref, token, t) == key)
    if mine + (CAPSTONE_LEVEL - level) >= CAPSTONE_POINTS:
        return None
    label = next((s["label"] for s in specs_of(ref, token) if s["key"] == key), key)
    talent = capstone or f"{label}'s 31-point talent"
    return (f"{talent} at {CAPSTONE_LEVEL} = full respec into {label}: {mine} point{'s' if mine != 1 else ''} "
            f"there now, {CAPSTONE_LEVEL - level} more by level {CAPSTONE_LEVEL}, {CAPSTONE_POINTS} needed.")


def spec_why(ref: Reference, cls: str, key: str, label: str, how: str, sheet: dict) -> str:
    """Where the spec came from, in plain words: "Scored for Shadow because
    most of your talent points are there (Shadow 21, Discipline 5, as
    Battle.net last read them)"."""
    if how == "talents":
        pts = [t for t in sheet.get("talents") or [] if isinstance(t, dict) and t.get("points")]
        shown = ", ".join(f"{t.get('tree') or '?'} {t['points']}" for t in sorted(pts, key=lambda t: -t["points"]))
        why = (f"most of your talent points are in {label} ({shown}, as Battle.net last read them) and nothing "
               f"else picks a spec: pick one on the gear page or set the site's WEIGHTS to score for another")
    else:
        addon = sheet.get("spec_from") or "a gear addon (ForeverLevelingBiS or GearQuestForever)"
        why = SPEC_WHY.get(how, how).format(label=label, addon=addon, cls=cls.title())
        if how == "default":
            why += (". No talents known yet: once Battle.net's read (every 2 hours) knows the character, "
                    "the tree with the most points is the hint")
    return f"Scored for {label} because {why}."


def advise_rows(rows: list[dict], weights: str | None = None, battlenet=None, mode: str | None = None,
                pvp: bool = False) -> dict | None:
    """advise() from one character's stored events as the analysis' prep
    reads them (wowstats.analysis.prep.query): the newest sheet, the level
    and zone of the newest record, the quests seen handed in, the newest
    bags and bank. Every upgrade
    is kept (`upgrades_all`). None without a sheet or a level; a GearError
    passes through. `battlenet`: the item ids Battle.net says it wears now
    (wowstats.quests.with_battlenet), read live by the caller."""
    got = newest_sheet(rows, battlenet)
    if got is None:
        return None
    sh, level, zone, done = got
    return advise(reference(), sh, level=level, zone=zone, done=done, weights=weights, every=True,
                  held=newest_bags(rows), mode=mode, pvp=pvp, log=quests.in_log(rows, done),
                  route=quests.position(r for r in rows if r.get("kind") == "step"))


# --- what each dungeon holds for one character -------------------------------


def instance_loot(ref: Reference, sheet: dict, *, level: int, spec: str | None = None,
                  weights: str | None = None, bis: dict | None = None,
                  mode: str | None = None) -> dict[str, list[dict]]:
    """Every item a dungeon gives this character, by the dungeon's area ID:
    its bosses' drops (with the chance) and the rewards of its own quests
    (the quests whose zone is the dungeon), each scored with the character's
    weights and set against what it wears (Gear.lua compare). Unlike
    advise(), whatever the item's level, whether it is an upgrade or worn
    already, and whether the quest was handed in: the dungeon panel marks
    each one obtained or missing (wowstats.plan.dungeonview). Weapons are
    compared one by one here; the advice's setups (WeaponSetups) give the
    gain for the ones it lists, which the panel prefers. bis: the advice's
    `bis_by_slot` (advise()), so each row carries the same badge
    (bis_badge); a one-hander, scored alone here, has none."""
    from wowstats import gearfit

    cx = _context(ref, sheet, level, None, None, spec, weights, mode)
    quests = ref.data.get("quests") or {}
    out: dict[str, list[dict]] = {}
    for sid, srcs in (ref.data.get("sources") or {}).items():
        item_id = int(sid)
        info = item_for(ref, item_id, cx.w)
        if not info:
            continue
        usable, _, min_level = usability(ref, info, cx.cls, cx.level, cx.who)
        if not usable:
            continue
        by_area: dict[str, dict] = {}
        for s in srcs:
            if s.get("kind") == "drop":
                a = forever.area(s.get("zone")) or {}
                if a.get("instance") != "dungeon":
                    continue
                d = by_area.setdefault(str(s["zone"]), {"drops": [], "quests": []})
                drop = {"boss": s.get("npc_name") if s.get("npc") else None, "npc_id": s.get("npc") or None,
                        "chance_pct": s.get("pct") or None,
                        "wing": (_wing_area(a, s.get("npc")) or {}).get("name") or s.get("zone_name") or a.get("name")}
                if drop not in d["drops"]:
                    d["drops"].append(drop)
            elif s.get("kind") == "quest":
                # The dungeon is forever-db's zone of the quest, where it is
                # done; Polestar's zone is where its giver stands (a city
                # for most dungeon quests), so it only names the dungeon for
                # a quest picked up inside.
                q = quests.get(str(s.get("quest"))) or {}
                zone = forever.quest_dungeon(s.get("quest"))
                if zone is None and (forever.area(q.get("zone")) or {}).get("instance") == "dungeon":
                    zone = q["zone"]
                if zone is None:
                    continue
                src = _source(ref, s, cx.who, info, item_id)  # who.done is empty: handed-in quests stay
                if src:
                    d = by_area.setdefault(str(zone), {"drops": [], "quests": []})
                    if all(x["quest_id"] != src["quest_id"] for x in d["quests"]):
                        d["quests"].append({k: src.get(k) for k in ("quest_id", "name", "level", "req_level",
                                                                    "choices")})
        if not by_area:
            continue
        r = compare(info, item_id, cx.worn, cx.w, cx.dual)
        at = max([min_level] + [_source_level({"kind": "quest", **q}) for d in by_area.values() for q in d["quests"]])
        vs = {r["slot"]} | ({OFF} if info["inv"] == 17 else set())
        # A dungeon item its quests don't give is a group drop: the factor
        # for an item others usually roll for first (wowstats.gearfit).
        group = [{"kind": "drop", "dungeon": True}] if not any(d["quests"] for d in by_area.values()) else \
            [{"kind": "quest"}]
        f = gearfit.fit(ref, info, cx.cls, cx.key, cx.how, cx.level, cx.w, group, cx.points, cx.mode)
        # A two-hander is a whole setup; any other weapon alone isn't one.
        rb = {**r, "setup_score": r["new"]} if info["inv"] == 17 else r
        badge = dict(NO_BADGE) if vs & cx.worn.unknown else bis_badge((bis or {}).get(str(r["slot"])), rb)
        row = {**_item_out(ref, item_id, info), "level": at, "slot": r["slot"], **badge,
               "slot_name": SLOT_NAMES.get(r["slot"]), "score": _r(r["score"], 2), "gain": _r(r["delta"], 2),
               "gain_pct": None if vs & cx.worn.unknown else _r(r.get("pct")), "fills_empty": bool(r["empty"]),
               "equipped": bool(r.get("equipped")), "fit": f, "value": _r(r["delta"] * f["factor"], 2)}
        for area_id, d in by_area.items():
            d["drops"].sort(key=lambda x: -(x["chance_pct"] or 0))
            out.setdefault(area_id, []).append({**row, **d})
    for items in out.values():
        items.sort(key=lambda x: (-(x["score"] or 0), x["item_id"]))
    return out


def _when(r: dict):
    return str(r.get("_time") or r.get("_t") or "")


def _ints(v) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return v if isinstance(v, list) else []


def _int_or_none(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def standings_of(rows: list[dict]) -> tuple[dict | None, int | None]:
    """(reputations, PvP rank) from the character's events: {faction id:
    (reaction 1-8, points from the start of Neutral)}, the newest of the
    Recorder's rep records (kind rep) and Altoholic's snapshots (kind reps)
    per faction, None when neither sent any. The PvP rank is the newest
    wow-foreverdb kind pvp event (the game's own number, from Syndicator's or
    Altoholic's currency 3473 or ForeverDB's armory snapshot, so it wins);
    without one, 0 when Altoholic's lifetime
    honorable kills are 0, else None (unknown)."""
    seen: dict[int, tuple] = {}
    any_reps = False
    hk = None
    rank = None
    for r in sorted(rows, key=_when):
        if r.get("feed") == "wow-recorder" and r.get("kind") == "rep":
            any_reps = True
            fid, reaction = _int_or_none(r.get("faction_id")), _int_or_none(r.get("reaction"))
            if fid is not None and reaction is not None:
                seen[fid] = (reaction, _int_or_none(r.get("standing")))
        elif r.get("feed") == "wow-altoholic" and r.get("kind") == "reps":
            any_reps = True
            for f in _ints(r.get("factions")):
                if isinstance(f, list) and len(f) == 3:
                    fid, reaction, pts = (_int_or_none(x) for x in f)
                    if fid is not None and reaction is not None:
                        seen[fid] = (reaction, pts)
        elif r.get("feed") == "wow-altoholic" and r.get("kind") == "stats":
            v = _int_or_none(r.get("honorable_kills"))
            if v is not None:
                hk = v
        elif r.get("feed") == "wow-foreverdb" and r.get("kind") == "pvp":
            v = _int_or_none(r.get("pvp_rank"))
            if v is not None and 0 <= v <= 14:
                rank = v
    if rank is None and hk == 0:
        rank = 0
    return (seen if any_reps else None), rank


def standing_rows(q, cf: str) -> list[dict]:
    """The events standings_of() reads, for one character: the Recorder's
    rep records, Altoholic's newest reputation snapshot and stats."""
    return [*q(f'feed:="wow-recorder" kind:="rep" {cf} | fields _time, feed, kind, faction_id, reaction, standing'),
            *q(f'feed:="wow-altoholic" kind:="reps" {cf} | fields _time, feed, kind, factions '
               f'| sort by (_time desc) | limit 1'),
            *q(f'feed:="wow-altoholic" kind:="stats" {cf} | fields _time, feed, kind, honorable_kills '
               f'| sort by (_time desc) | limit 1'),
            *q(f'feed:="wow-foreverdb" kind:="pvp" {cf} | fields _time, feed, kind, pvp_rank '
               f'| sort by (_time desc) | limit 1')]


def newest_sheet(rows: list[dict], battlenet=None) -> tuple[dict, int, str | None, set] | None:
    """(sheet, level, zone, quests done: wowstats.quests) from one character's stored
    events (wowstats.analysis.prep.query), or None without a sheet or a level."""
    from wowstats.stores import PLAYER_LEVEL_KINDS

    def newest(pred):
        return stores.newest([r for r in rows if pred(r)]) or None

    g = pick_sheet([r for r in rows if r.get("feed") == "wow-gear"])
    # The character's own level: Polestar's sheet, a ding or a session
    # record. Any Recorder event's `level` once put the advice at a guard's
    # 55 for a level-33 character (a kill's is the creature's): BiS from 20
    # levels up, every badge on the Dungeons panel a third of the doll's.
    lv = newest(lambda r: _num(r.get("level")) and (r.get("feed") == "wow-character" or (
        r.get("feed") == "wow-recorder" and r.get("kind") in PLAYER_LEVEL_KINDS)))
    if g is None or lv is None:
        return None
    zone = newest(lambda r: r.get("feed") in ("wow-recorder", "wow-character") and r.get("zone"))
    who = newest(lambda r: r.get("feed") == "wow-recorder" and r.get("kind") == "session") or {}
    done = set(quests.completed(rows, battlenet=battlenet))
    out = sheet(g, who)
    out["reps"], out["pvp_rank"] = standings_of(rows)
    return out, int(_num(lv.get("level"))), (zone or {}).get("zone"), done


def instance_loot_rows(rows: list[dict], weights: str | None = None, bis: dict | None = None,
                       mode: str | None = None) -> dict[str, list[dict]]:
    """instance_loot() from stored events; {} without a sheet or a level.
    bis: the advice's `bis_by_slot`, for the badges."""
    got = newest_sheet(rows)
    if got is None:
        return {}
    sh, level, _, _ = got
    return instance_loot(reference(), sh, level=level, weights=weights, bis=bis, mode=mode)

