"""The gear page's weights as text for the Forever Leveling BiS addon.

Forever Leveling BiS (CurseForge `forever-leveling-bis`) ranks gear in the
game with weights of its own; it imports others as text, in two formats:
its own share string,

    FLB1;CLASS;spec;setup;60:key=value,...;Name

and a Pawn scale tag, ( Pawn: v1: "Name": Class=Priest, Spec=Shadow, ... ).
This writes both from ForeverDB.net's weights (data/foreverdb-weights.json,
the ones wowstats.gear scores with), so the addon ranks items the way the
gear page does. Nothing of the addon is in here: its licence keeps its code
and data its own, so only the text format it reads is followed, written from
what its own exported strings and in-game import say.

One bracket up to 60: ForeverDB's weights are the same at every level. The
name keeps "ForeverDB" (its licence's attribution term, GPL-3.0 7(b)); the
page says "Data from ForeverDB.net" next to the buttons.

Units. On Forever the gear's hit, crit, expertise, dodge, parry and block
are flat "+X%" lines. ForeverDB weighs them per point of rating and scores a
1% line as 10 hit, 14 crit, 10 expertise, 12 dodge, 15 parry or 5 block
rating (wowstats.gear.PCT_RATING); the addon weighs the line per 1%. So its
weight is ours times that rating. Item stats map one to one otherwise:
ForeverDB's "+X damage and healing" (dmgheal) is the addon's spell power
(splpwr), its spell damage only (sp) the addon's spldmg, healing only
(heal) splheal, school damage the school's power. Weapon DPS: the main hand
and two-hander at `dps`, an off-hand weapon at half (ForeverDB's rule), a
ranged weapon's at the hunter's weight, a wand's at the wand weight (only
hunters shoot bows and guns with a weight; only casters shoot wands).

tests/test_gearexport.py checks every ForeverDB key has an entry here and
that the addon's sum over the exported weights ranks real items as
wowstats.gear.score_with does.
"""

from __future__ import annotations

from dataclasses import dataclass

from wowstats import gear

FORMATS = ("pawn", "flb")
MAX_LEVEL = 60
CREDIT = {"name": "ForeverDB.net", "url": "https://foreverdb.net", "text": "Data from ForeverDB.net"}

# The addon's weapon setups, by the names its strings carry.
SETUPS = {"twohand": "Two-hander", "dualwield": "Dual wield", "shield": "One-hander and shield",
          "held": "One-hander and off hand"}
# ForeverDB's style per spec -> the setup the string names (the import
# window lets the player change it before importing).
STYLE_SETUP = {"2h": "twohand", "dw": "dualwield", "shield": "shield", "any": "twohand"}

# Pawn's class and spec names (Pawn's own, in its order).
PAWN_SPECS = {
    "WARRIOR": {"arms": "Arms", "fury": "Fury", "protection": "Protection"},
    "PALADIN": {"holy": "Holy", "protection": "Protection", "retribution": "Retribution"},
    "HUNTER": {"beastmastery": "BeastMastery", "marksmanship": "Marksmanship", "survival": "Survival"},
    "ROGUE": {"assassination": "Assassination", "combat": "Combat", "subtlety": "Subtlety"},
    "PRIEST": {"discipline": "Discipline", "holy": "Holy", "shadow": "Shadow"},
    "SHAMAN": {"elemental": "Elemental", "enhancement": "Enhancement", "restoration": "Restoration"},
    "MAGE": {"arcane": "Arcane", "fire": "Fire", "frost": "Frost"},
    "WARLOCK": {"affliction": "Affliction", "demonology": "Demonology", "destruction": "Destruction"},
    "DRUID": {"balance": "Balance", "feral": "Feral", "restoration": "Restoration"},
}


@dataclass(frozen=True)
class Map:
    """One ForeverDB weight key in the addon's terms: `flb` and `pawn` are
    (key, factor) pairs, the weight there being ours times the factor.
    `why`: why it maps so, or why nothing does."""

    flb: tuple = ()
    pawn: tuple = ()
    why: str = ""
    # Said under the buttons when the spec weighs it: what the string loses.
    note: str = ""


def _same(flb: str, *pawn: str, why: str = "one to one") -> Map:
    return Map(((flb, 1),), tuple((p, 1) for p in pawn), why)


def _pct(flb: str, rating: int, *pawn: str, also: str | None = None) -> Map:
    """A stat Forever items carry as "+X%": ours per point of rating, the
    addon's per 1% (`rating` points a percent). The addon reads Pawn's
    rating names as per 1% too (it pools hit and crit, melee and spell)."""
    return Map(((flb, rating),) + (((also, 1),) if also else ()), tuple((p, rating) for p in pawn),
               f"per point of rating -> per 1% ({rating} rating a percent)")


SCHOOLS = {"spArcane": ("arcsplpwr", "ArcaneSpellDamage"), "spFire": ("firsplpwr", "FireSpellDamage"),
           "spFrost": ("frosplpwr", "FrostSpellDamage"), "spHoly": ("holsplpwr", "HolySpellDamage"),
           "spNature": ("natsplpwr", "NatureSpellDamage"), "spShadow": ("shasplpwr", "ShadowSpellDamage")}

# Every key ForeverDB weighs (data/foreverdb-weights.json), in the addon's terms.
# `dps`, `wand` and `dmgheal` are worked out per class in weights_for (below).
MAPPING: dict[str, Map] = {
    "str": _same("str", "Strength"), "agi": _same("agi", "Agility"), "sta": _same("sta", "Stamina"),
    "int": _same("int", "Intellect"), "spi": _same("spi", "Spirit"),
    "ap": _same("atkpwr", "Ap"), "rap": _same("rgdatkpwr", "Rap"),
    # Our item table's armour is the whole of it, base and bonus: the addon
    # keeps the bonus apart, at the same weight here. Pawn's Armor alone
    # fills both on import.
    "armor": Map((("armor", 1), ("armorbonus", 1)), (("Armor", 1),), "base and bonus armour at one weight"),
    "critRating": _pct("critPct", 14, "CritRating", "SpellCritRating", also="critstrkrtng"),
    "hitRating": _pct("hitPct", 10, "HitRating", "SpellHitRating", also="hitrtng"),
    "expertise": Map((("expPct", 10),), (("ExpertiseRating", 1),),
                     "per point -> per 1% (10 a percent); Pawn's expertise stays per point (the addon converts)"),
    "dodgeRating": _pct("dodgePct", 12, "DodgeRating"),
    "parryRating": _pct("parryPct", 15, "ParryRating"),
    "blockRating": _pct("blockPct", 5, "BlockRating"),
    "haste": _same("hastertng", "HasteRating", "SpellHasteRating", why="haste rating per point, both sides"),
    "defense": _same("defrtng", "DefenseRating"), "blockValue": _same("block", "BlockValue"),
    "mp5": _same("manargn", "Mp5"), "hp5": _same("healthrgn", "Hp5"),
    "spellPen": _same("splpen", "SpellPenetration"),
    "sp": _same("spldmg", "SpellDamage", why="spell damage only"),
    "heal": _same("splheal", "Healing", why="healing only"),
    # Pawn has no "damage and healing": the addon makes its spell power the
    # sum SpellDamage + Healing + SpellPower, so SpellPower carries the rest.
    "dmgheal": Map((("splpwr", 1),), (("SpellPower", 1),),
                   "+X damage and healing is the addon's splpwr; in Pawn, SpellPower = ours - sp - heal"),
    **{k: _same(f, p, why="school damage on top of spell power") for k, (f, p) in SCHOOLS.items()},
    "dps": Map((("dps", 1), ("ohDps", gear.OFFHAND_DPS)), (("MeleeDps", 1), ("OffHandDps", gear.OFFHAND_DPS - 1)),
               "melee weapon DPS, the off hand's at half; a hunter's goes on ranged weapons (rangedDps / RangedDps)"),
    "wand": Map((("rangedDps", 1),), (("RangedDps", 1),),
                "a wand's DPS: the ranged weapon DPS of a class that shoots only wands"),
    # ForeverDB's stat ids 50 and 125: no item in the game data carries either,
    # and wowstats.gear scores them zero too. Nothing to rank on.
    "stat50": Map(why="ForeverDB stat id 50: no item carries it; zero in our score too"),
    "stat125": Map(why="ForeverDB stat id 125: no item carries it; zero in our score too"),
}
# Hit that works with ranged weapons only (Trueaim Gauntlets): ours counts it
# for hunters at the hit weight, the addon has its own key; Pawn has none.
RANGED_HIT = Map((("rhitPct", 10),), (), "ranged-only hit, per 1%",
                 note="Pawn has no ranged-only hit: items with \"+% hit with ranged weapons\" score lower there. "
                      "The Forever Leveling BiS string keeps it.")
# What both strings leave to the addon (said under the buttons).
ADDON_OWN = ("Weights only: the addon keeps its own item list, and adds what it knows on top (item effects, random "
             "suffixes, racial weapon bonuses, set bonuses at 60), so a few items can rank differently.")
PAWN_SCALE = ("The addon rescales a Pawn scale to its own spec scale on import: the order stays the same, the "
              "numbers change.")


class ExportError(ValueError):
    """No string: the reason says why."""


def _fmt(v: float) -> str:
    """Up to 3 decimals, no trailing zeros (the addon keeps 3)."""
    s = f"{round(v, 3):.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _weights(cls: str, spec: str, mode: str) -> tuple[dict, dict]:
    data = gear.weights_data() or {}
    entry = ((data.get("specs") or {}).get(cls) or {}).get(spec)
    if not entry:
        raise ExportError(f"no ForeverDB weights for {cls} {spec}")
    w = entry.get(mode)
    if not w:
        raise ExportError(f"no {gear.MODES.get(mode, mode)} weights for {cls} {spec}")
    return w, entry


def addon_weights(cls: str, spec: str, mode: str) -> tuple[dict, dict, list[str]]:
    """Our weights in the addon's terms -> (flb {key: value}, pawn {name:
    value}, notes). Keys come out in MAPPING's order."""
    w, _entry = _weights(cls, spec, mode)
    hunter = cls == "HUNTER"
    flb: dict[str, float] = {}
    pawn: dict[str, float] = {}
    notes: list[str] = []

    def put(out: dict, pairs, v: float) -> None:
        for k, f in pairs:
            out[k] = out.get(k, 0) + v * f

    for key, m in MAPPING.items():
        v = w.get(key) or 0
        if not v:
            continue
        if key == "dps" and hunter:
            # ForeverDB's dps is a hunter's ranged weapon (wowstats.gear weights_for: rdps).
            put(flb, (("rangedDps", 1),), v)
            put(pawn, (("RangedDps", 1),), v)
            continue
        if key == "dmgheal":
            put(flb, m.flb, v)
            pawn["SpellPower"] = v - (w.get("sp") or 0) - (w.get("heal") or 0)
            continue
        put(flb, m.flb, v)
        put(pawn, m.pawn, v)
        if m.note and m.note not in notes:
            notes.append(m.note)
    if hunter and w.get("hitRating"):
        put(flb, RANGED_HIT.flb, w["hitRating"])
        notes.append(RANGED_HIT.note)
    unknown = sorted(k for k in w if k not in MAPPING)
    if unknown:  # tests/test_gearexport.py keeps this empty
        raise ExportError("no mapping for ForeverDB weight " + ", ".join(unknown))
    return ({k: v for k, v in flb.items() if v}, {k: v for k, v in pawn.items() if v}, notes)


def default_setup(cls: str, spec: str) -> str:
    """The setup the string names: ForeverDB's style for the spec (casters,
    healers and hunters: a two-hander, as the addon shows first for them)."""
    _w, entry = _weights(cls, spec, gear.DEFAULT_MODE)
    return STYLE_SETUP.get(entry.get("style") or "any", "twohand")


def profile_name(mode: str) -> str:
    """ "ForeverDB Leveling": the credit stays in the name (the addon keeps
    24 characters and refuses ; | and ")."""
    return f"ForeverDB {gear.MODES[mode]}"


def flb_string(cls: str, spec: str, mode: str, setup: str | None = None) -> str:
    mode = gear.mode_key(mode)
    setup = setup or default_setup(cls, spec)
    if setup not in SETUPS:
        raise ExportError("setup is " + ", ".join(SETUPS))
    flb, _pawn, _notes = addon_weights(cls, spec, mode)
    stats = ",".join(f"{k}={_fmt(v)}" for k, v in flb.items())
    return f"FLB1;{cls};{spec};{setup};{MAX_LEVEL}:{stats};{profile_name(mode)}"


def pawn_string(cls: str, spec: str, mode: str) -> str:
    mode = gear.mode_key(mode)
    _flb, pawn, _notes = addon_weights(cls, spec, mode)
    head = [f"Class={cls[:1]}{cls[1:].lower()}"]
    if PAWN_SPECS.get(cls, {}).get(spec):
        head.append(f"Spec={PAWN_SPECS[cls][spec]}")
    parts = head + [f"{k}={_fmt(v)}" for k, v in pawn.items() if _fmt(v) != "0"]
    return f'( Pawn: v1: "{profile_name(mode)}": {", ".join(parts)} )'


def export(cls: str, spec: str, mode: str, fmt: str | None = None, setup: str | None = None) -> dict:
    """Both strings (or the one `fmt` names) for the spec and mode, with what
    each loses and where to paste it."""
    cls = str(cls or "").upper()
    mode = gear.mode_key(mode)
    setup = setup or default_setup(cls, spec)
    _flb, _pawn, notes = addon_weights(cls, spec, mode)
    out = {"class": cls, "spec": spec, "mode": mode, "setup": setup, "setup_label": SETUPS.get(setup, setup),
           "name": profile_name(mode), "credit": dict(CREDIT),
           "how": "In the game: /bis, Stat weights (top right of the window), Import, paste, check the spec and "
                  "weapon setup, Import.",
           "notes": [ADDON_OWN]}
    if fmt in (None, "flb"):
        out["flb"] = flb_string(cls, spec, mode, setup)
    if fmt in (None, "pawn"):
        out["pawn"] = pawn_string(cls, spec, mode)
        out["pawn_notes"] = [*notes, PAWN_SCALE]
    return out
