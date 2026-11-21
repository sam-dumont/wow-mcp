"""Gear advice: Polestar's scoring, on the server, over Polestar's gear data.

What each slot holds now, the best item the character can get at its level
(and the next one coming within a few levels), the gain in percent, and where
to get it: the quest and zone, the dungeon and boss, the craft and profession.

The data is data/gear-reference.json, exported from a Polestar release by
tools/gear_reference/ (docs/gear-reference.md): every equippable item's
stats, where leveling gear comes from (Polestar's leveling data, generated
from ForeverLevelingBiS v0.12.0-beta, MIT), the stat weights per class, spec
and level band (Polestar's own, blended with wowsims, MIT) and the class rules.
GEAR_REFERENCE_FILE points at another file; `off` turns gear advice off.

The scoring is a port of Polestar's addon/Polestar/Gear/Gear.lua (and
Adjust.lua, Rewards.lua): each function names the one it follows.
tests/test_gear_parity.py runs Polestar's own Lua next to it. What the
server can't see, it leaves out the way Polestar does when the client can't
say: no talent ranks (Mental Dexterity in withTalentStats), no weapon
skills (knowsWeaponSkill counts every weapon the class can train), no random
"of the Owl" suffix stats (the client's C_Item.GetItemStats has them, the
item table does not), and no Forever Leveling BiS. The bags and the bank
come from the shipper's wow-bags (Syndicator): an item held there is a
candidate like in Gear.lua refreshBags, its only source the bag or the bank
(`held`), since getting it again is no errand.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from wowstats import ahsource, forever, quests

_HERE = Path(__file__).resolve()
# An installed wheel carries it as wowstats/data/; a checkout has it at the root.
BUNDLED = (_HERE.parent / "data" / "gear-reference.json", _HERE.parents[1] / "data" / "gear-reference.json")

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
OFFHAND_DPS = 0.5  # off-hand weapons hit for half (weights without ohDps)
MELEE_DPS = 0.01  # withMeleeDps: a tiebreaker for casters and Druids
AP_PER_DPS = 14  # withMeleeDps: Feral's weapon DPS is 14x its attack power weight
FOREVER_ONLY = ("haste", "expertise", "armorPen")  # withForeverRatings
SCHOOLS = ("spArcane", "spFire", "spFrost", "spHoly", "spNature", "spShadow")
SLOT_NAMES = {1: "Head", 2: "Neck", 3: "Shoulder", 5: "Chest", 6: "Waist", 7: "Legs", 8: "Feet", 9: "Wrist",
              10: "Hands", 11: "Finger", 12: "Finger", 13: "Trinket", 14: "Trinket", 15: "Back", 16: "Main hand",
              17: "Off hand", 18: "Ranged"}
# ChrRaces IDs (quest race masks are 1 << (id - 1); Skyborne is Forever's 33).
RACE_IDS = {"human": 1, "orc": 2, "dwarf": 3, "nightelf": 4, "night elf": 4, "scourge": 5, "undead": 5,
            "tauren": 6, "gnome": 7, "troll": 8, "skyborne": 33}
SIDES = {"alliance": 1, "horde": 2}
SOON_LEVELS = 3  # "the next one coming": within this many levels
PER_SLOT = 3  # upgrades listed per slot
HELD = frozenset({"bags", "bank"})  # the source kinds of an item the character already has
PAIRS = ((11, 12), (13, 14))
PAIRED = {11, 12, 13, 14}


class GearError(Exception):
    """No advice: the reason says what's missing."""


@dataclass
class Reference:
    available: bool
    why: str = ""
    data: dict = field(default_factory=dict)
    # likely_suffix's index, built on first use.
    peers: dict | None = field(default=None, repr=False, compare=False)

    @property
    def gear(self) -> dict:
        return self.data.get("gear") or {}

    def item(self, item_id) -> dict | None:
        return (self.data.get("items") or {}).get(str(item_id))

    def meta(self) -> dict:
        d = self.data
        return {"polestar": (d.get("polestar") or {}).get("version"), "tag": (d.get("polestar") or {}).get("tag"),
                "leveling": d.get("leveling"), "item_stats": d.get("item_stats")}


_LOCK = threading.Lock()
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
                ref = Reference(True, data=data)
                break
        _CACHE[key] = ref
        return ref


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
    1b. the server's WEIGHTS setting for this character ("server": the
       player's choice, set where the site runs);
    2. Polestar's Stat weights setting when it names a spec ("setting");
    3. the spec the player picked in a gear addon, ForeverLevelingBiS or
       GearQuestForever (the shipper's `spec`: "picked");
    4. only when nothing above says, the tree with the most talent points
       (Gear.lua DetectSpec), from Battle.net's API (the exporter's daily
       /specializations read, which the caller puts in `talents`): a hint,
       "talents";
    5. the class's leveling default ("default").

    The player's choice comes first (owner, 2026-11-18, restored
    2026-11-20): a Shadow priest leveling with most points in Discipline
    on the way to Shadowform wants Shadow weights. Polestar in game also
    puts Forever Leveling BiS's pick before the talents."""
    if override:
        k = spec_key(ref, cls, override)
        if k:
            return k, "page"
    k = spec_key(ref, cls, weights)
    if k:
        return k, "server"
    k = spec_key(ref, cls, sheet.get("polestar_spec"))
    if k:
        return k, "setting"
    k = spec_key(ref, cls, sheet.get("spec"))
    if k:
        return k, "picked"
    k = most_points(ref, cls, sheet.get("talents"))
    if k:
        return k, "talents"
    meta = (ref.gear.get("CLASSES") or {}).get(cls) or {}
    return meta.get("default"), "default"


# --- weights (Gear.lua Gear.WeightsFor, Adjust.lua Adjust.Apply) -------------


def _with_forever_ratings(w: dict, authored: dict | None) -> dict:
    """Gear.lua withForeverRatings: Forever's own ratings, which no band
    weighs, from the authored table."""
    if not authored:
        return w
    c = dict(w)
    for k in FOREVER_ONLY:
        if w.get(k) is None and authored.get(k):
            c[k] = authored[k]
    return c


def _with_formula_stats(ref: Reference, w: dict, cls: str, authored: dict | None, level: int) -> dict:
    """Gear.lua withFormulaStats: health and mana from Stamina's and
    Intellect's weights, haste and expertise from the band's crit and hit at
    the spec's own ratios, armor penetration from crit and a same-level mob's
    armor (Weights.lua Gear.ArmorShare, Gear.IntPerSpellCrit)."""
    if not (w and authored):
        return w
    g = ref.gear
    c = dict(w)
    if (c.get("sta") or 0) > 0:
        c["health"] = c["sta"] / g["HEALTH_PER_STA"]
    lv = max(1, min(60, int(level)))
    per_crit = ((g.get("INT_PER_SPELL_CRIT") or {}).get(cls) or [None] * 60)[lv - 1]
    if per_crit and (c.get("int") or 0) > 0:
        c["mana"] = max(0, c["int"] - (c.get("spellCrit") or 0) / per_crit) / g["MANA_PER_INT"]
    crit, ref_crit = "crit", authored.get("crit") or 0
    if ref_crit <= 0:
        crit, ref_crit = "spellCrit", authored.get("spellCrit") or 0
    if authored.get("haste") and ref_crit > 0 and (c.get(crit) or 0) > 0:
        c["haste"] = c[crit] * authored["haste"] / ref_crit
    if authored.get("expertise") and (authored.get("hit") or 0) > 0 and (c.get("hit") or 0) > 0:
        c["expertise"] = c["hit"] * authored["expertise"] / authored["hit"]
    if (authored.get("crit") or 0) > 0 and (c.get("crit") or 0) > 0:
        # Gear.ArmorShare floors the level and clamps it to the table (1..60).
        share = g["ARMOR_SHARE"][max(1, min(len(g["ARMOR_SHARE"]), math.floor(level))) - 1]
        c["armorPen"] = c["crit"] * share
    return c


def _with_melee_dps(w: dict, cls: str, key: str) -> dict:
    """Gear.lua withMeleeDps: a small melee weapon DPS weight where the spec
    has none; Feral's is 14x its attack power weight."""
    if not w or w.get("dps") is not None:
        return w
    c = dict(w)
    c["dps"] = AP_PER_DPS * w["ap"] if (cls == "DRUID" and key == "feral" and w.get("ap")) else MELEE_DPS
    return c


def weights_for(ref: Reference, cls: str, key: str, level: int) -> tuple[dict | None, dict | None]:
    """Gear.lua Gear.WeightsFor: the first band whose upTo covers the level
    (the last band past the end), else the authored table; then the formula
    weights and the melee DPS weight. -> weights, band info."""
    g = ref.gear
    authored = ((g.get("WEIGHTS") or {}).get(cls) or {}).get(key)
    bands = ((g.get("WEIGHT_BANDS") or {}).get(cls) or {}).get(key) or []
    if bands:
        pick = bands[-1]
        for b in bands:
            if level <= b["upTo"]:
                pick = b
                break
        w = _with_forever_ratings(pick["weights"], authored)
        info = {k: pick.get(k) for k in ("upTo", "source", "label", "note")}
    else:
        w, info = authored, ({"upTo": 60, "source": "polestar"} if authored else None)
    if not w:
        return None, None
    return _with_melee_dps(_with_formula_stats(ref, w, cls, authored, level), cls, key), info


def adjust_apply(weights: dict, edits: dict | None) -> tuple[dict, int]:
    """Adjust.lua Adjust.Apply: the player's own edits on top, an edit being
    a multiple of the default ({x}) or a plain value ({v}). -> weights, count."""
    if not isinstance(edits, dict) or not edits:
        return weights, 0
    out, n = dict(weights), 0
    for k, e in edits.items():
        if not isinstance(e, dict):
            continue
        if isinstance(e.get("x"), (int, float)):
            out[k] = (weights.get(k) or 0) * e["x"]
        else:
            out[k] = e.get("v") if isinstance(e.get("v"), (int, float)) else 0
        n += 1
    return out, n


# --- scoring (Gear.lua scoreWith, racialBonus, usability) --------------------


def _weapon_share(info: dict, w: dict) -> float:
    """Gear.lua weaponShare."""
    melee, ranged = w.get("dps") or 0, w.get("rdps") or 0
    top = max(melee, ranged)
    if top <= 0:
        return 1
    return (ranged if info["inv"] in RANGED_INV else melee) / top


def _racial_bonus(info: dict, w: dict, as_offhand: bool, racial: dict | None) -> float:
    """Gear.lua racialBonus."""
    if not racial:
        return 0
    s = 0.0
    wc = racial.get("weaponCrit")
    if wc and info["class"] == 2 and not as_offhand and str(info.get("subclass")) in (wc.get("subclasses") or {}):
        s += ((w.get("crit") or 0) + (w.get("spellCrit") or 0)) * wc["pct"] * _weapon_share(info, w)
    for k, mult in (racial.get("statMult") or {}).items():
        v = info["stats"].get(k)
        if v:
            s += (w.get(k) or 0) * v * (mult - 1)
    return s


def _school_cap(w: dict, top: float) -> float:
    """Gear.lua schoolCap."""
    w_top = w.get("sp") or 0
    for k in SCHOOLS:
        w_top = max(w_top, w.get(k) or 0)
    return w_top * top


def score_with(info: dict, w: dict, as_offhand: bool, racial: dict | None = None) -> float:
    """Gear.lua scoreWith: each stat times its weight; weapon DPS by kind;
    spell school bonuses capped at the largest single one; racial traits."""
    s, school, top = 0.0, 0.0, 0
    for k, v in info["stats"].items():
        if k == "dps":
            if info["inv"] in RANGED_INV:
                s += (w.get("rdps") or 0) * v
            elif info["inv"] in MELEE_INV:
                wd = (w.get("ohDps") or (w.get("dps") or 0) * OFFHAND_DPS) if as_offhand else (w.get("dps") or 0)
                s += wd * v
        elif k in SCHOOLS:
            school += (w.get(k) or 0) * v
            top = max(top, v)
        else:
            s += (w.get(k) or 0) * v
    if school > 0:
        school = min(school, _school_cap(w, top))
    return s + school + _racial_bonus(info, w, as_offhand, racial)


def usability(ref: Reference, info: dict, cls: str, level: int) -> tuple[bool, str | None, int]:
    """Gear.lua usability -> usable, reason, the level it can first be worn.
    Without the client's weapon skills, every weapon the class can train
    counts (knowsWeaponSkill's own fallback); Dual Wield from the level the
    class trains it."""
    g = ref.gear
    prof = (g.get("PROFICIENCY") or {}).get(cls)
    meta = (g.get("CLASSES") or {}).get(cls)
    min_level = info.get("req") or 0
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


def worn_from(ref: Reference, items: list[dict], w: dict, racial: dict | None,
              real: dict | None = None) -> Worn:
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
        worn.scores[slot] = score_with(info, w, slot == OFF, racial) if info else 0
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


def options(info: dict, item_id: int, worn: Worn, w: dict, racial: dict | None, dual: bool,
            every_slot: bool = False) -> list[dict]:
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
    score = score_with(info, w, as_off, racial)
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
            as_off_score = score if inv in OFFHAND_INV else score_with(info, w, True, racial)
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


def compare(info: dict, item_id: int, worn: Worn, w: dict, racial: dict | None, dual: bool) -> dict:
    """Gear.lua compare: the option with the largest gain (the first on a tie)."""
    # The last slot wearing this very item (a pair of the same dagger: the off hand).
    worn_at = [s for s in SLOTS_OF[info["inv"]] if worn.ids.get(s) == item_id]
    if worn_at:
        s = worn_at[-1]
        return {"slot": s, "old": worn.score(s), "new": worn.score(s), "delta": 0, "pct": 0,
                "equipped": True, "empty": False, "score": score_with(info, w, info["inv"] in OFFHAND_INV, racial)}
    best = None
    for o in options(info, item_id, worn, w, racial, dual):
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
    bags: set = field(default_factory=set)  # item IDs in the bags (wow-bags)


def _source(ref: Reference, s: dict, who: Who, info: dict, item_id: int | None = None) -> dict | None:
    """One source of an item in the answer's shape, or None when this
    character can't take it (the other side's quest, another race's or
    class's, a quest already handed in). A craft counts when the character
    makes it at its rank, or when the item has a level to wear it at (gear
    others make and sell); a craft with no level (Engineering's goggles)
    asks for the profession to wear it, which the item table doesn't say."""
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
        return {"kind": "quest", "quest_id": s["quest"], "name": q.get("name") or fq.get("name"),
                "zone": q.get("zone_name") or fq.get("zone"), "part": fq.get("part"),
                "level": q.get("level"), "req_level": q.get("req"), "dungeon": bool(q.get("dungeon")),
                "choices": len(q.get("choices") or [])}
    if s["kind"] == "drop":
        # Whether the zone is a dungeon or a raid, and its level range as the
        # game's Group Finder gives it (data/forever-reference.json): Forever's
        # own instances (City of Dalaran) included.
        a = forever.area(s.get("zone")) or {}
        out = {"kind": "drop", "boss": s.get("npc_name") if s.get("npc") else None, "npc_id": s.get("npc") or None,
               "zone": s.get("zone_name") or a.get("name"), "chance_pct": s.get("pct") or None,
               "dungeon": a.get("instance") == "dungeon"}
        if a.get("instance"):
            out["instance"] = a["instance"]
            if a.get("levels"):
                out["levels"] = a["levels"]
            if a.get("floor") is not None:  # Polestar's "For your level" starts there (wowstats.plan.vsguide)
                out["floor"] = a["floor"]
        return out
    if s["kind"] == "craft":
        prof = s.get("profession")
        rank = who.professions.get(str(prof).lower()) if prof else None
        skill_ok = rank is not None and rank >= (s.get("skill") or 0)
        if not skill_ok and not info.get("req"):
            return None
        known = recipe_known(who, prof, s.get("spell"))
        name = (ref.data.get("names") or {}).get(str(item_id)) or forever.item_name(item_id) if item_id else None
        pattern = pattern_in_bags(who, name) if known is not True else None
        # "Make it now" needs the recipe as well as the skill: a pattern in
        # the bags is learnt first, an unknown one is a find, and with no
        # recipe list (no DataStore_Crafts) it is never claimed.
        return {"kind": "craft", "profession": prof, "skill": s.get("skill"), "spell_id": s.get("spell"),
                "your_rank": rank, "skill_ok": skill_ok, "known": known, "pattern_item_id": pattern,
                "can_make": bool(skill_ok and known is True)}
    return None


def craft_status(s: dict) -> str:
    """What stands between the character and making a craft source, in
    words, for every reader (MCP, planner, coach): the skill, then the recipe.
    The reagents aren't in the data, so "make it" always says to check them."""
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
    return ("craft", x.get("profession"), x.get("spell_id") or x.get("skill"))


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
    endgame ones) above the character's level: an item never asks for more
    than its item level, so only up to his own is it surely wearable."""
    if (info.get("quality") or 0) >= 2 and not (set(info.get("stats") or {}) - _FIXED):
        return False
    return bool(info.get("req")) or (info.get("ilvl") or 0) <= level


def _source_level(src: dict) -> int:
    return src.get("req_level") or 0 if src["kind"] == "quest" else 0


def gates(at: int, level: int, sources: list[dict]) -> list[dict]:
    """What stands between the character and the item, each with what it
    needs and what he has: the level to wear it (or to take its quest), and
    for an item only a craft gives, the profession's skill when he has the
    profession ("skill", Icy Cloak: Tailoring 200, he has 119) or a crafter
    or the auction house when he hasn't ("buy", not a wait). The data has no
    reputation or price gate for these items, so none is made up."""
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


def _here(src: dict, zone: str | None) -> bool:
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


def adjust_map(rows: list) -> dict:
    """The shipper's gear_adjust list -> Polestar's PolestarDB.gearAdjust[CLASS]
    shape, {spec: {stat: {x} | {v}}} (Adjust.lua)."""
    out: dict = {}
    for a in rows:
        if not isinstance(a, dict) or not a.get("spec") or not a.get("stat"):
            continue
        e = {k: float(a[k]) for k in ("x", "v") if isinstance(a.get(k), (int, float))}
        if e:
            out.setdefault(str(a["spec"]), {})[str(a["stat"])] = e
    return out


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
        "polestar_spec": row.get("polestar_spec") or None,
        "gear_adjust": adjust_map(_jlist(row.get("gear_adjust"))),
        # Talents come from Battle.net only (Views puts them in); an old
        # sheet's SimBench `talents` stays stored, unread.
        "talents": None,
        "recipes": [r for r in _jlist(row.get("recipes")) if isinstance(r, dict)],
        "min_upgrade": _num(row.get("min_upgrade")),
        "class": row.get("class") or fb.get("class"), "race": row.get("race") or fb.get("race"),
        "faction": row.get("faction") or fb.get("faction"),
    }


# The stored events the advice needs, as LogsQL pipes after a character filter.
Q_SHEET = ("fields _time, items, professions, recipes, spec, spec_from, polestar_spec, gear_adjust, "
           "min_upgrade, "
           "class, race, faction | sort by (_time desc) | limit 1")
# The newest bags and bank (wow-bags), for advise(held=...) through
# wowstats.plan.facts.newest_bags.
Q_HELD = 'feed:="wow-bags" {cf} | fields _time, feed, bags, bank | sort by (_time desc) | limit 1'


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
    band: dict | None
    adjusted: int
    racial: dict | None
    dual: bool
    min_upgrade: float
    who: Who
    worn: Worn
    worn_names: dict
    points: int | None = None  # talent points in the spec's tree (gearfit.capstone)


def _context(ref: Reference, sheet: dict, level, zone: str | None, done, spec: str | None,
             weights: str | None) -> _Ctx:
    """What advise() and instance_loot() score with: the class, the spec's
    weights (with the player's edits), the racial traits, what is worn."""
    if not ref.available:
        raise GearError(ref.why)
    cls = class_token(sheet.get("class"))
    if not cls or cls not in (ref.gear.get("WEIGHTS") or {}):
        raise GearError("no class on the character sheet yet" if not cls else f"Polestar has no weights for {cls}")
    if not level:
        raise GearError("no level for this character yet")
    level = int(level)
    key, how = resolve_spec(ref, cls, sheet, spec, weights)
    w, band = weights_for(ref, cls, key, level)
    if w is None:
        raise GearError(f"Polestar has no weights for {cls} {key}")
    # The stats the class can't use count zero (a warrior's Spirit), the
    # ones only this spec doesn't use count partially until its 31-point
    # talent (a shadow priest's healing power): wowstats.gearfit. The
    # player's own edits still come on top.
    from wowstats import gearfit

    points = gearfit.spec_points(ref, cls, key, sheet)
    w = gearfit.model_weights(ref, w, cls, key, level, points)
    adjust = (sheet.get("gear_adjust") or {}).get(key) if isinstance(sheet.get("gear_adjust"), dict) else None
    w, adjusted = adjust_apply(w, adjust)
    race_id = RACE_IDS.get(str(sheet.get("race") or "").strip().lower())
    racial = (ref.gear.get("RACIALS") or {}).get(str(race_id)) if race_id else None
    prof = (ref.gear.get("PROFICIENCY") or {}).get(cls)
    dual = can_dual_wield(prof, level)
    min_upgrade = sheet.get("min_upgrade")
    min_upgrade = float(min_upgrade) if isinstance(min_upgrade, (int, float)) else 1.0
    who = Who(cls=cls, level=level, side=SIDES.get(str(sheet.get("faction") or "").lower()), race_id=race_id,
              professions={str(p.get("name") or "").lower(): p.get("rank") for p in sheet.get("professions") or []
                           if p.get("name") and isinstance(p.get("rank"), (int, float))},
              done=set(done or ()), zone=zone, recipes=recipe_lists(sheet.get("recipes")))

    items = [i for i in sheet.get("items") or [] if isinstance(i, dict)]
    worn = worn_from(ref, items, w, racial, sheet.get("worn_stats"))
    worn_names = {i.get("slot"): i.get("name") for i in items}
    return _Ctx(cls, level, key, how, w, band, adjusted, racial, dual, min_upgrade, who, worn, worn_names, points)


def recipe_lists(rows) -> dict[str, set]:
    """The shipper's `recipes` ([{profession, known: [spell IDs]}], from
    DataStore_Crafts) -> {profession (lower case): known spell IDs}. Only a
    profession whose recipe window was scanned is listed."""
    out: dict[str, set] = {}
    for r in rows or []:
        if isinstance(r, dict) and r.get("profession") and isinstance(r.get("known"), list):
            out[str(r["profession"]).lower()] = {int(n) for x in r["known"] if isinstance(n := _num(x), int)}
    return out


def newest_bags(rows: list[dict]) -> dict | None:
    """The newest wow-bags event's item IDs ({bags, bank, t}), None when the
    shipper never sent one (before 0.3.2, or no Syndicator)."""
    from wowstats.stores import parse_t

    r = max((r for r in rows if r.get("feed") == "wow-bags"), key=lambda r: parse_t(r.get("_time")), default=None)
    if r is None:
        return None
    ids = {}
    for k in ("bags", "bank"):
        v = r.get(k)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = None
        ids[k] = sorted({int(n) for x in v if isinstance(n := _num(x), int)}) if isinstance(v, list) else []
    return {**ids, "t": parse_t(r.get("_time")).timestamp()}


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


def advise(ref: Reference, sheet: dict, *, level: int, zone: str | None = None, done: set | None = None,
           spec: str | None = None, limit: int = 25, weights: str | None = None, every: bool = False,
           held: dict | None = None, market: dict | None = None) -> dict:
    """The gear advice for one character.

    sheet: the newest wow-gear event (items, class, race, faction, spec,
    polestar_spec, gear_adjust, min_upgrade, professions). every: also
    return `upgrades_all`, every upgrade before the per-slot cap (the
    analysis' upgrade_candidates and the planner's dungeon ranking need the
    drops a capped list leaves out). held: the newest wow-bags lists
    ({bags, bank}); an item held there has one source, {"kind": "bags"} or
    {"kind": "bank"}: equip it, or take it out of the bank first. market:
    wowstats.ahsource.gather(): an item on sale now gets an "ah" source (one
    with no other source the character can use becomes a candidate too),
    with its price against his gold; the answer gets `ah`."""
    cx = _context(ref, sheet, level, zone, done, spec, weights)
    cls, level, key, how, w, band, adjusted = cx.cls, cx.level, cx.key, cx.how, cx.w, cx.band, cx.adjusted
    racial, dual, min_upgrade, who, worn = cx.racial, cx.dual, cx.min_upgrade, cx.who, cx.worn
    worn_names = cx.worn_names

    # Every item this character can get, with its best option (compare) and
    # its options per slot.
    cands = []
    holding = held_where(held)
    who.bags = {i for i, where in holding.items() if where == "bags"}
    sale = (market or {}).get("items") or {}
    for item_id, where in holding.items():
        info = item_for(ref, item_id, w)
        if not info:  # not gear, or gear the advice doesn't score
            continue
        usable, _, min_level = usability(ref, info, cls, level)
        if usable and min_level <= level + SOON_LEVELS:
            cands.append({"id": item_id, "info": info, "at": min_level, "sources": [{"kind": where}],
                          "here": False})
    for sid, srcs in (ref.data.get("sources") or {}).items():
        item_id = int(sid)
        base = ref.item(item_id)
        if not base or item_id in holding:
            continue
        info = item_for(ref, item_id, w)
        usable, _, min_level = usability(ref, info, cls, level)
        if not usable:
            continue
        here_srcs = _dedupe([x for x in (_source(ref, s, who, info, item_id) for s in srcs) if x])
        if item_id in sale and buyable(base, level):
            here_srcs.append({"kind": "ah", **sale[item_id]})
        if not here_srcs:
            continue
        at = max(min_level, min(_source_level(x) for x in here_srcs))
        if at > level + SOON_LEVELS:
            continue
        cands.append({"id": item_id, "info": info, "at": at, "sources": here_srcs,
                      "here": any(_here(x, zone) for x in here_srcs)})

    # On sale, and in no source Polestar knows (most world-drop greens).
    for item_id, s in sale.items():
        info = ref.item(item_id)
        if (not info or item_id in holding or str(item_id) in (ref.data.get("sources") or {})
                or not buyable(info, level)):
            continue
        usable, _, min_level = usability(ref, info, cls, level)
        if usable and min_level <= level + SOON_LEVELS:
            cands.append({"id": item_id, "info": info, "at": min_level, "sources": [{"kind": "ah", **s}],
                          "here": False})

    gear_score = sum(worn.score(sl) for sl in EQUIPPED_SLOTS)
    from wowstats import gearfit

    fits: dict[int, dict] = {}

    def fit_of(c: dict) -> dict:
        # Who the item is built for and how it fits this spec (wowstats.gearfit):
        # a label on every row, and the factor the ranking applies.
        if c["id"] not in fits:
            fits[c["id"]] = gearfit.fit(ref, c["info"], cls, key, how, level, w, c["sources"], cx.points)
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
                "slot_name": SLOT_NAMES.get(r["slot"]), "score": _r(r["new"], 2), "gain": _r(r["delta"], 2),
                # The gain as a share of everything worn: "+13.6, 4% of your
                # gear". Against a weak item the percent alone reads +427%.
                "gain_share_pct": _r(r["delta"] / gear_score * 100) if gear_score > 0 else None,
                "gain_pct": None if unknown else _r(r["pct"]), "fills_empty": r["empty"], "vs_unknown": unknown,
                "here": c["here"], "sources": priced(c["sources"], _r(r["delta"], 2)),
                "gates": gates(c["at"], level, c["sources"]), "fit": fit_of(c),
                # What the ranking sorts on: the gain, a little less for an item
                # built for someone else, less again when a group usually hands
                # it to them. The gain shown stays the gain.
                "value": _r(r["delta"] * fit_of(c)["factor"], 2)}

    # Weapons are set against weapons: a two-hander against main hand + off
    # hand, a one-hander only together with the off hand it would be worn
    # with (Gear.lua pairs it with the best piece in the bags; here the best
    # one the character holds or can get, or the one worn).
    setups = WeaponSetups(cands, worn, w, racial, dual, level)

    # Per slot: the best now and the best coming within SOON_LEVELS.
    per_slot: dict[int, dict] = {}
    for c in cands:
        if c["info"]["inv"] in WEAPON_INV:
            continue
        for o in options(c["info"], c["id"], worn, w, racial, dual, every_slot=True):
            if worn.ids.get(o["slot"]) == c["id"]:
                continue
            bucket = "now" if c["at"] <= level else "soon"
            slot = per_slot.setdefault(o["slot"], {"now": [], "soon": []})
            slot[bucket].append((o["delta"], -c["id"], c, o))

    def pick(slot: int, bucket: str, exclude: int | None = None):
        ranked = [t for t in sorted((per_slot.get(slot) or {}).get(bucket) or [], key=lambda t: (t[0], t[1]),
                                    reverse=True) if t[2]["id"] != exclude]
        if not ranked or ranked[0][0] <= 0:
            return ranked[0] if ranked else None
        # Among the close ones, the one built for the spec (gearfit's factor):
        # only those within the largest factor can change places.
        close = [t for t in ranked if t[0] >= ranked[0][0] * gearfit.CLASS * gearfit.GROUP]
        return max(close, key=lambda t: (t[0] * fit_of(t[2])["factor"], t[0], t[1]))

    picks: dict[tuple, tuple | None] = {}
    for bucket in ("now", "soon"):
        for slot in EQUIPPED_SLOTS:
            if slot not in PAIRED:
                picks[(slot, bucket)] = pick(slot, bucket)
        for a, b in PAIRS:
            # Gear.lua compare: an empty slot first, else the weaker of the two
            # takes the best item; the other gets the best one left (a quest
            # reward is one item, worn once).
            weak = a if a not in worn.ids else b if b not in worn.ids else (b if worn.score(b) < worn.score(a) else a)
            strong = b if weak == a else a
            pw = picks[(weak, bucket)] = pick(weak, bucket)
            picks[(strong, bucket)] = pick(strong, bucket, pw[2]["id"] if pw else None)
    # Where each worn item stands against the best the slot can hold now
    # (owner, 2026-11-21: "show bis or second or a percentage of bis").
    stand.update(standings(per_slot, worn, setups, level))
    weapon_rows = {"now": setups.row("now", out), "soon": setups.row("soon", out)}

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
        slots_out.append({
            "slot": slot, "slot_name": SLOT_NAMES[slot],
            "standing": standing_out(stand.get(slot)) if cur_id else None,
            "current": ({**_item_out(ref, cur_id, cur_info, worn_names.get(slot)),
                         "score": _r(worn.score(slot), 2), "known": cur_info is not None,
                         "random_suffix": slot in worn.unknown} if cur_id else None),
            "best": best, "next": nxt})

    # Next upgrades: Polestar's compare per item, one pick per quest's reward
    # choices (Rewards.lua RankChoices: an upgrade first, then the larger
    # gain, the higher score, the lower item ID). Weapons come as setups.
    ups = []
    for c in cands:
        if c["info"]["inv"] in WEAPON_INV:
            r = setups.compare(c)
        else:
            r = compare(c["info"], c["id"], worn, w, racial, dual)
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
        "spec": {"key": key, "label": label, "source": how, "adjusted": adjusted, "choices": specs_of(ref, cls),
                 # None: no spec picked and no talents, for a class whose specs
                 # differ in role (the fit labels then ask to pick one).
                 "role": gearfit.known_role(ref, cls, key, how),
                 "why": spec_why(ref, cls, key, label, how, sheet, adjusted)},
        "weights": {k: _r(v, 4) for k, v in sorted(w.items()) if v},
        "band": band, "min_upgrade_pct": min_upgrade, "soon_levels": SOON_LEVELS,
        # Each setup's best on its own, wearable now: what a BiS list per
        # setup shows next to the one best setup the upgrades rank.
        "weapon_setups": weapon_setups,
        "gear_score": _r(gear_score, 2),
        "slots": slots_out,
        # The top `limit`, plus whatever is in the current zone, already in
        # the bags or bank, or on sale below that: the page can put those first.
        "upgrades": capped[:limit] + [u for u in capped[limit:] if u["here"] or is_held(u) or on_sale(u)],
        "upgrades_total": len(capped),
        "data": ref.meta(),
    }


# --- standing against BiS ------------------------------------------------------

EPS = 1e-6


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
    # Never "100%" for something that isn't BiS.
    pct = min(99, round(cur / target * 100)) if target > 0 else 0
    return {"rank": 2 if above == 1 else None, "pct": pct, "target": target}


def _pair(a: int, b: int, per_slot: dict, worn: Worn) -> dict[int, dict | None]:
    """Rings or trinkets: BiS is the best two different items. A worn one
    among those two is BiS; the rest measure against the BiS item they'd
    make room for (the stronger worn one against the best left, the weaker
    against the next)."""
    pool: dict[int, float] = {}
    for slot in (a, b):
        for _d, _i, c, o in (per_slot.get(slot) or {}).get("now") or []:
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
    rest.sort(key=lambda x: -(worn.score(x) if x in worn.ids else -1))
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


def standings(per_slot: dict, worn: Worn, setups: WeaponSetups, level: int) -> dict[int, dict | None]:
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
        for _d, _i, c, o in (per_slot.get(slot) or {}).get("now") or []:
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
    return out


def standing_out(st: dict | None) -> dict | None:
    """The slot's standing as the page and the MCP show it."""
    if not st:
        return None
    label = "BiS" if st["rank"] == 1 else "2nd" if st["rank"] == 2 else f"{st['pct']}% of BiS"
    return {"rank": st["rank"], "pct": st["pct"], "label": label}


def bis_gain(st: dict | None, r: dict) -> dict | None:
    """An upgrade in BiS terms: the worn item's share of BiS and the
    candidate's ("62% -> 100% of BiS"). Weapons count as whole setups."""
    if not st or not st.get("target"):
        return None
    if r["slot"] in (MAIN, OFF):
        if "setup_score" not in r:
            return None
        new = r["setup_score"]
    else:
        new = r["new"]
    to = 100 if new >= st["target"] - EPS else min(99, round(new / st["target"] * 100))
    return {"from_pct": st["pct"], "to_pct": max(to, st["pct"])}


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
            **{k: alt.get(k) for k in ("item_id", "name", "quality", "gain", "level", "now", "slot_name")},
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

    def __init__(self, cands: list[dict], worn: Worn, w: dict, racial, dual: bool, level: int):
        self.worn, self.level = worn, level
        main = worn.infos.get(MAIN)
        self.worn_two = bool(main and main["inv"] == 17)
        self.current = worn.score(MAIN) + worn.score(OFF)
        self.parts: list[dict] = []  # {c, role, score, at}
        for c in cands:
            inv = c["info"]["inv"]
            if inv == 17:
                self.parts.append({"c": c, "role": "two", "score": score_with(c["info"], w, False, racial)})
            if inv in MAIN_INV:
                self.parts.append({"c": c, "role": "main", "score": score_with(c["info"], w, False, racial)})
            if inv in (14, 23) or (inv == 22 and dual) or (inv == 13 and dual):
                self.parts.append({"c": c, "role": "off", "score": score_with(c["info"], w, True, racial)})
        # What is worn can stay.
        if worn.ids.get(MAIN):
            role = "two" if self.worn_two else "main"
            self.kept = {role: {"c": None, "role": role, "score": worn.score(MAIN), "id": worn.ids[MAIN]}}
        else:
            self.kept = {}
        if worn.ids.get(OFF):
            self.kept["off"] = {"c": None, "role": "off", "score": worn.score(OFF), "id": worn.ids[OFF]}

    def _pool(self, role: str, max_at: int, exclude: int | None = None) -> list[dict]:
        out = [p for p in self.parts if p["role"] == role and p["c"]["at"] <= max_at and p["c"]["id"] != exclude
               and not (role == "off" and p["c"]["id"] == self.worn.ids.get(OFF))]
        if role in self.kept and self.kept[role]["id"] != exclude:
            out.append(self.kept[role])
        return out

    def _best(self, role: str, max_at: int, exclude: int | None = None) -> dict | None:
        pool = self._pool(role, max_at, exclude)
        return max(pool, key=lambda p: (p["score"], p["c"] is None), default=None)

    def best_setup(self, max_at: int) -> dict | None:
        """The best whole setup with parts available by level max_at."""
        two = self._best("two", max_at)
        best = {"total": two["score"], "main": two, "off": None} if two else None
        for m in self._pool("main", max_at):
            o = self._best("off", max_at, exclude=_pid(m))
            total = m["score"] + (o["score"] if o else 0)
            if best is None or total > best["total"]:
                best = {"total": total, "main": m, "off": o}
        return best

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
            return {"kind": "worn", "item_id": part["id"]}
        return {"kind": "get", **out(part["c"], {"slot": OFF if part["role"] == "off" else MAIN, "new": part["score"],
                                                  "delta": part["score"], "pct": None, "empty": False})}

    def _r(self, slot: int, part: dict, total: float) -> dict:
        delta = total - self.current
        return {"slot": slot, "old": self.current, "new": part["score"], "delta": delta,
                "pct": delta / self.current * 100 if self.current > 0 else None,
                "empty": self.current == 0, "score": part["score"], "equipped": False, "setup_score": total}

    def row(self, bucket: str, out) -> dict | None:
        """The equipment panel's weapon line for `bucket` (now / soon): the
        part to get, on the main hand row (with its partner) or, when only
        the off hand changes, on the off-hand row."""
        best = self.best_setup(self.level if bucket == "now" else self.level + SOON_LEVELS)
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

    def compare(self, c: dict) -> dict | None:
        """compare() for one weapon: the best setup it is part of, against
        what is worn in both hands."""
        by = max(self.level, c["at"])
        best = None
        for p in self.parts:
            if p["c"] is not c:
                continue
            if p["role"] == "two":
                total, partner = p["score"], None
            elif p["role"] == "main":
                partner = self._best("off", by, exclude=c["id"])
                total = p["score"] + (partner["score"] if partner else 0)
            else:
                partner = self._best("main", by, exclude=c["id"])
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
    "setting": "Polestar's Stat weights setting is {label}",
    "picked": "you picked {label} in {addon}",
    "default": "it's Polestar's default for a leveling {cls}",
}


SHORT_SOURCE = {"page": "this page", "server": "site setting", "setting": "Polestar setting",
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


def spec_why(ref: Reference, cls: str, key: str, label: str, how: str, sheet: dict, adjusted: int) -> str:
    """Where the spec came from, in plain words: "Scored for Shadow because
    most of your talent points are there (Shadow 21, Discipline 5, as
    Battle.net last read them)"."""
    if how == "talents":
        pts = [t for t in sheet.get("talents") or [] if isinstance(t, dict) and t.get("points")]
        shown = ", ".join(f"{t.get('tree') or '?'} {t['points']}" for t in sorted(pts, key=lambda t: -t["points"]))
        why = (f"most of your talent points are in {label} ({shown}, as Battle.net last read them) and nothing "
               f"else picks a spec: set Polestar's Stat weights or the site's WEIGHTS to score for another")
    else:
        addon = sheet.get("spec_from") or "a gear addon (ForeverLevelingBiS or GearQuestForever)"
        why = SPEC_WHY.get(how, how).format(label=label, addon=addon, cls=cls.title())
        if how == "default":
            why += (". No talents known yet: once Battle.net's read (every 2 hours) knows the character, "
                    "the tree with the most points is the hint")
    if adjusted:
        why += f". Your {adjusted} edit{'s' if adjusted > 1 else ''} in Polestar's Adjust weights count"
    return f"Scored for {label} because {why}."


def advise_rows(rows: list[dict], weights: str | None = None) -> dict | None:
    """advise() from one character's stored events as the analysis' prep
    reads them (wowstats.analysis.prep.query): the newest sheet, the level
    and zone of the newest record, the quests seen handed in, the newest
    bags and bank. Every upgrade
    is kept (`upgrades_all`). None without a sheet or a level; a GearError
    passes through."""
    got = newest_sheet(rows)
    if got is None:
        return None
    sh, level, zone, done = got
    return advise(reference(), sh, level=level, zone=zone, done=done, weights=weights, every=True,
                  held=newest_bags(rows))


# --- what each dungeon holds for one character -------------------------------


def instance_loot(ref: Reference, sheet: dict, *, level: int, spec: str | None = None,
                  weights: str | None = None) -> dict[str, list[dict]]:
    """Every item a dungeon gives this character, by the dungeon's area ID:
    its bosses' drops (with the chance) and the rewards of its own quests
    (the quests whose zone is the dungeon), each scored with the character's
    weights and set against what it wears (Gear.lua compare). Unlike
    advise(), whatever the item's level, whether it is an upgrade or worn
    already, and whether the quest was handed in: the dungeon panel marks
    each one obtained or missing (wowstats.plan.dungeonview). Weapons are
    compared one by one here; the advice's setups (WeaponSetups) give the
    gain for the ones it lists, which the panel prefers."""
    from wowstats import gearfit

    cx = _context(ref, sheet, level, None, None, spec, weights)
    quests = ref.data.get("quests") or {}
    out: dict[str, list[dict]] = {}
    for sid, srcs in (ref.data.get("sources") or {}).items():
        item_id = int(sid)
        info = item_for(ref, item_id, cx.w)
        if not info:
            continue
        usable, _, min_level = usability(ref, info, cx.cls, cx.level)
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
                        "chance_pct": s.get("pct") or None, "wing": s.get("zone_name") or a.get("name")}
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
        r = compare(info, item_id, cx.worn, cx.w, cx.racial, cx.dual)
        at = max([min_level] + [q.get("req_level") or 0 for d in by_area.values() for q in d["quests"]])
        vs = {r["slot"]} | ({OFF} if info["inv"] == 17 else set())
        # A dungeon item its quests don't give is a group drop: the factor
        # for an item others usually roll for first (wowstats.gearfit).
        group = [{"kind": "drop", "dungeon": True}] if not any(d["quests"] for d in by_area.values()) else \
            [{"kind": "quest"}]
        f = gearfit.fit(ref, info, cx.cls, cx.key, cx.how, cx.level, cx.w, group, cx.points)
        row = {**_item_out(ref, item_id, info), "level": at, "slot": r["slot"],
               "slot_name": SLOT_NAMES.get(r["slot"]), "score": _r(r["score"], 2), "gain": _r(r["delta"], 2),
               "gain_pct": None if vs & cx.worn.unknown else _r(r.get("pct")), "fills_empty": bool(r["empty"]),
               "equipped": bool(r.get("equipped")), "fit": f, "value": _r(r["delta"] * f["factor"], 2)}
        for area_id, d in by_area.items():
            d["drops"].sort(key=lambda x: -(x["chance_pct"] or 0))
            out.setdefault(area_id, []).append({**row, **d})
    for items in out.values():
        items.sort(key=lambda x: (-(x["score"] or 0), x["item_id"]))
    return out


def newest_sheet(rows: list[dict]) -> tuple[dict, int, str | None, set] | None:
    """(sheet, level, zone, quests done: wowstats.quests) from one character's stored
    events (wowstats.analysis.prep.query), or None without a sheet or a level."""
    from wowstats.stores import parse_t

    def newest(pred):
        return max((r for r in rows if pred(r)), key=lambda r: parse_t(r.get("_time")), default=None)

    g = newest(lambda r: r.get("feed") == "wow-gear")
    lv = newest(lambda r: r.get("feed") in ("wow-recorder", "wow-character") and _num(r.get("level")))
    if g is None or lv is None:
        return None
    zone = newest(lambda r: r.get("feed") in ("wow-recorder", "wow-character") and r.get("zone"))
    who = newest(lambda r: r.get("feed") == "wow-recorder" and r.get("kind") == "session") or {}
    return sheet(g, who), int(_num(lv.get("level"))), (zone or {}).get("zone"), set(quests.completed(rows))


def instance_loot_rows(rows: list[dict], weights: str | None = None) -> dict[str, list[dict]]:
    """instance_loot() from stored events; {} without a sheet or a level."""
    got = newest_sheet(rows)
    if got is None:
        return {}
    sh, level, _, _ = got
    return instance_loot(reference(), sh, level=level, weights=weights)
