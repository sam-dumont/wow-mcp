"""The weights export for Forever Leveling BiS (wowstats.gearexport).

The addon's score of an item is the sum of its stats times their weights,
plus its weapon DPS times `dps` (main hand, two-hander), `ohDps` (an off-hand
weapon) or `rangedDps` (bows, guns, thrown, wands); "+X%" lines are their own
stats (critPct, hitPct...) weighed per 1%. It is written out here from that
description, not taken from the addon (its licence forbids copying), and run
on our items translated into its stat names, the way its item data spells
them (Gordok's Gauntlets: our critRating 14 is its critPct 1, our sp 11 = heal
11 its splpwr 11).

Worked example, Gordok's Gauntlets (item 18367) for a Shadow priest, Leveling:
  ours:  armor 221 x 0.02 + critRating 14 x 0.5 + int 10 x 0.3 + dmgheal 11 x 1 + sta 10 x 0.8 = 33.42
  addon: armor 221 x 0.02 + critPct 1 x 7       + int 10 x 0.3 + splpwr 11 x 1  + sta 10 x 0.8 = 33.42

Checked once by hand against the addon's own item list (not in the repo):
504,711 item x spec x mode scores, 98 apart, every one an item whose "+X%"
line the two data sets round differently (10 crit rating vs 0.7%).
"""

from __future__ import annotations

import re

import pytest

from wowstats import gear
from wowstats import gearexport as gx

# Our item stat -> the addon's item stat and how many of ours make one of its.
ITEM_KEYS = {"str": ("str", 1), "agi": ("agi", 1), "sta": ("sta", 1), "int": ("int", 1), "spi": ("spi", 1),
             "ap": ("atkpwr", 1), "rap": ("rgdatkpwr", 1), "armor": ("armor", 1),
             "critRating": ("critPct", 14), "hitRating": ("hitPct", 10), "rangedHit": ("rhitPct", 10),
             "expertise": ("expPct", 10), "dodgeRating": ("dodgePct", 12), "parryRating": ("parryPct", 15),
             "blockRating": ("blockPct", 5), "haste": ("hastertng", 1), "defense": ("defrtng", 1),
             "blockValue": ("block", 1), "mp5": ("manargn", 1), "hp5": ("healthrgn", 1),
             "spellPen": ("splpen", 1), "sp": ("spldmg", 1), "heal": ("splheal", 1), "dmgheal": ("splpwr", 1),
             "spArcane": ("arcsplpwr", 1), "spFire": ("firsplpwr", 1), "spFrost": ("frosplpwr", 1),
             "spHoly": ("holsplpwr", 1), "spNature": ("natsplpwr", 1), "spShadow": ("shasplpwr", 1)}
RANGED = {15, 25, 26}
CASTERS = {"PRIEST", "MAGE", "WARLOCK"}
CASES = [("PRIEST", "shadow", "lvl"), ("PRIEST", "holy", "pvp"), ("ROGUE", "combat", "lvl"),
         ("WARRIOR", "protection", "pve"), ("HUNTER", "marksmanship", "lvl"), ("SHAMAN", "enhancement", "pvp"),
         ("DRUID", "feral", "lvl"), ("WARLOCK", "destruction", "pvp"), ("PALADIN", "holy", "lvl")]


def addon_item(it: dict) -> dict:
    """Our item's stats in the addon's names: "+X damage and healing" (sp
    equal to heal) is one stat, splpwr."""
    st = dict(it["stats"])
    out: dict[str, float] = {}
    if st.get("sp") and st.get("sp") == st.get("heal"):
        out["splpwr"] = st.pop("sp")
        st.pop("heal")
    for k, v in st.items():
        if k == "dps":
            continue
        name, per = ITEM_KEYS[k]
        out[name] = out.get(name, 0) + v / per
    return out


def addon_score(it: dict, w: dict, off_hand: bool = False) -> float:
    """The addon's sum (above) for one item and a stats table."""
    s = sum(v * (w.get(k) or 0) for k, v in addon_item(it).items())
    dps = it["stats"].get("dps")
    if dps:
        if it["inv"] in RANGED:
            s += dps * (w.get("rangedDps") or 0)
        elif off_hand:
            s += dps * (w.get("ohDps") if "ohDps" in w else 0.5 * (w.get("dps") or 0))
        else:
            s += dps * (w.get("dps") or 0)
    return s


def parse_flb(text: str) -> dict:
    parts = text.split(";")
    assert parts[0] == "FLB1" and len(parts) == 6, text
    up_to, body = parts[4].split(":", 1)
    assert up_to == "60"
    return {k: float(v) for k, v in (p.split("=") for p in body.split(","))}


def pawn_import(text: str, cls: str, setup: str) -> dict:
    """A Pawn tag read as the addon's import describes it: one to one names;
    hit, crit and haste pooled melee/spell (the larger); expertise per point
    -> per 1%; Armor also as bonus armour; spell power = SpellDamage +
    Healing + SpellPower; weapon DPS summed by the hands it counts on."""
    m = re.fullmatch(r'\( Pawn: v1: "([^"]*)": (.*) \)', text)
    assert m, text
    p = {k: v for k, v in (x.split("=") for x in m.group(2).split(", "))}
    assert p.pop("Class") == cls.title()
    p.pop("Spec", None)
    p = {k: float(v) for k, v in p.items()}
    one = {"Strength": "str", "Agility": "agi", "Stamina": "sta", "Intellect": "int", "Spirit": "spi",
           "Ap": "atkpwr", "Rap": "rgdatkpwr", "SpellDamage": "spldmg", "Healing": "splheal", "Mp5": "manargn",
           "Hp5": "healthrgn", "DefenseRating": "defrtng", "SpellPenetration": "splpen", "BlockValue": "block",
           "DodgeRating": "dodgePct", "ParryRating": "parryPct", "BlockRating": "blockPct", "Armor": "armor",
           **{s[1]: s[0] for s in gx.SCHOOLS.values()}}
    w = {one[k]: v for k, v in p.items() if k in one}
    for a, b, ours in (("HitRating", "SpellHitRating", "hitPct"), ("CritRating", "SpellCritRating", "critPct"),
                       ("HasteRating", "SpellHasteRating", "hastertng")):
        if a in p or b in p:
            w[ours] = max(p.get(a, p.get(b)), p.get(b, p.get(a)))
    if "ExpertiseRating" in p:
        w["expPct"] = p["ExpertiseRating"] * 10
    if "Armor" in p:
        w["armorbonus"] = p["Armor"]
    if any(k in p for k in ("SpellDamage", "Healing", "SpellPower")):
        w["splpwr"] = p.get("SpellDamage", 0) + p.get("Healing", 0) + p.get("SpellPower", 0)
    if "MeleeDps" in p:
        w["dps"] = p["MeleeDps"]
        w["ohDps"] = p["MeleeDps"] + p.get("OffHandDps", 0)
    if "RangedDps" in p:
        w["rangedDps"] = p["RangedDps"]
    return w


def items_for(cls: str) -> list[dict]:
    """Every item of the game data the class's scores can tell apart: ranged
    weapons only of the kind it shoots (only casters shoot wands); no test
    weapon in a glove slot (ForeverDB counts DPS on weapon slots only)."""
    out = []
    for it in gear.game_items()["items"].values():
        if not it.get("stats"):
            continue
        if it["stats"].get("dps") and it["inv"] not in RANGED | gear.MELEE_INV:
            continue
        if it["inv"] in RANGED and it["stats"].get("dps") and (it["subclass"] == gear.WAND_SUBCLASS) != (
                cls in CASTERS):
            continue
        out.append(it)
    return out


def ours(cls: str, spec: str, mode: str) -> dict:
    ref = type("R", (), {"spec_weights": staticmethod(lambda c, k: gear.weights_data()["specs"][c][k])})()
    w, _ = gear.weights_for(ref, cls, spec, mode)
    return w


def info(it: dict) -> dict:
    return {"stats": it["stats"], "inv": it["inv"], "class": it["class"], "subclass": it["subclass"]}


def test_every_foreverdb_key_has_a_mapping():
    keys = {k for specs in gear.weights_data()["specs"].values() for e in specs.values()
            for m in gear.MODES for k in e.get(m, {})}
    missing = keys - set(gx.MAPPING)
    assert not missing, f"no gearexport.MAPPING entry for {sorted(missing)}"
    for k in keys:
        m = gx.MAPPING[k]
        assert m.why, k
        if not m.flb:  # not representable: said why
            assert "no item" in m.why or m.note, k


def test_every_spec_and_mode_exports():
    for cls, specs in gear.weights_data()["specs"].items():
        for spec in specs:
            for mode in gear.MODES:
                e = gx.export(cls, spec, mode)
                assert e["flb"].startswith(f"FLB1;{cls};{spec};{e['setup']};60:")
                assert e["flb"].endswith(";ForeverDB " + gear.MODES[mode])
                assert e["pawn"].startswith(f'( Pawn: v1: "ForeverDB {gear.MODES[mode]}": Class={cls.title()}, ')
                assert len(e["name"]) <= 24 and not set(e["name"]) & set(';|"')


def test_worked_example_gordoks_gauntlets():
    it = gear.game_items()["items"]["18367"]
    w = ours("PRIEST", "shadow", "lvl")
    flb = parse_flb(gx.flb_string("PRIEST", "shadow", "lvl"))
    assert gear.score_with(info(it), w) == pytest.approx(33.42)
    assert addon_score(it, flb) == pytest.approx(33.42)


@pytest.mark.parametrize(("cls", "spec", "mode"), CASES)
def test_flb_scores_items_as_we_do(cls, spec, mode):
    w = ours(cls, spec, mode)
    flb = parse_flb(gx.flb_string(cls, spec, mode, "dualwield"))
    items = items_for(cls)
    for it in items:
        assert addon_score(it, flb) == pytest.approx(gear.score_with(info(it), w), abs=1e-6), it["name"]
        if it["inv"] in (13, 22) and it["stats"].get("dps"):
            assert addon_score(it, flb, off_hand=True) == pytest.approx(
                gear.score_with(info(it), w, as_offhand=True), abs=1e-6), it["name"]


@pytest.mark.parametrize(("cls", "spec", "mode"), CASES)
def test_pawn_ranks_items_as_we_do(cls, spec, mode):
    """Same order: the addon rescales an imported Pawn scale (one factor),
    which can't change a ranking. Ranged-only hit is the one stat Pawn has
    no name for (its note says so): those items are left out for hunters."""
    w = ours(cls, spec, mode)
    pw = pawn_import(gx.pawn_string(cls, spec, mode), cls, "dualwield")
    items = [it for it in items_for(cls) if not (cls == "HUNTER" and "rangedHit" in it["stats"])]
    for it in items:
        assert addon_score(it, pw) == pytest.approx(gear.score_with(info(it), w), abs=1e-3), it["name"]


def test_hunter_pawn_says_what_it_loses():
    e = gx.export("HUNTER", "marksmanship", "lvl")
    assert any("ranged" in n for n in e["pawn_notes"])
    assert "rhitPct=" in e["flb"]


def test_owner_strings():
    """The owner's case (a Shadow priest leveling with a two-hander)."""
    e = gx.export("PRIEST", "shadow", "lvl")
    assert e["setup"] == "twohand"
    assert e["flb"] == ("FLB1;PRIEST;shadow;twohand;60:sta=0.8,int=0.3,spi=0.12,armor=0.02,armorbonus=0.02,"
                        "critPct=7,critstrkrtng=0.5,hitPct=8.4,hitrtng=0.84,hastertng=0.8,splpen=0.6,spldmg=1,"
                        "splpwr=1,shasplpwr=1,rangedDps=1.5;ForeverDB Leveling")
    assert "SpellDamage=1" in e["pawn"] and "ShadowSpellDamage=1" in e["pawn"] and "RangedDps=1.5" in e["pawn"]


def test_bad_setup_refused():
    with pytest.raises(gx.ExportError):
        gx.flb_string("PRIEST", "shadow", "lvl", "nope")
