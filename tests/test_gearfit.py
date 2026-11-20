"""The gear fit model (wowstats.gearfit): per-spec value, who an item is
built for, the labels, and how the ranking uses them (owner, 2026-11-20:
an upgrade is an upgrade, but say who it's built for)."""

import json
from pathlib import Path

import pytest

from tests.test_gear import sheet
from wowstats import gear, gearfit, insights

REFS = json.loads((Path(__file__).parent / "data" / "gearfit-refs.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ref():
    r = gear.reference()
    assert r.available, r.why
    return r


def w(ref, cls, key, level):
    return gearfit.spec_weights(ref, cls, key, level)


def score(ref, stats, cls, key, level, inv=7):
    return gear.score_with({"inv": inv, "class": 4, "subclass": 1, "stats": stats}, w(ref, cls, key, level), False)


def label(ref, item_id, cls, key, level, how="picked", sources=None):
    info = ref.item(item_id)
    return gearfit.fit(ref, info, cls, key, how, level, w(ref, cls, key, level), sources)


# --- the stats each spec can use ----------------------------------------------


@pytest.mark.parametrize("cls,key,level,stat", [
    ("WARRIOR", "fury", 30, "spi"), ("WARRIOR", "arms", 60, "spi"), ("ROGUE", "combat", 45, "spi"),
    ("WARRIOR", "fury", 30, "int"), ("MAGE", "frost", 30, "heal"), ("ROGUE", "combat", 30, "heal"),
])
def test_a_stat_the_class_cant_use_is_worth_nothing(ref, cls, key, level, stat):
    """Level 1, against the class: zero at every level."""
    assert w(ref, cls, key, level).get(stat, 0) == 0


@pytest.mark.parametrize("cls,key,stat", [
    ("PRIEST", "shadow", "heal"), ("DRUID", "balance", "heal"), ("PALADIN", "retribution", "heal"),
    ("SHAMAN", "elemental", "ap"), ("DRUID", "restoration", "ap"),
])
def test_a_stat_the_class_uses_counts_partly_until_the_31_point_talent(ref, cls, key, stat):
    """Level 2, the class's but not the spec's: a generalist's share before
    the spec's 31-point talent (40 at the earliest), the spec's own after."""
    at30, at50 = w(ref, cls, key, 30).get(stat, 0), w(ref, cls, key, 50).get(stat, 0)
    assert at30 > at50 >= 0 and at30 >= 0.24
    assert at50 == (gear.weights_for(ref, cls, key, 50)[0].get(stat) or 0)
    # Talent-aware: at 45 with 20 points in the tree it hasn't the talent yet.
    raw45 = gear.weights_for(ref, cls, key, 45)[0]
    assert gearfit.model_weights(ref, raw45, cls, key, 45, points=20)[stat] > \
        gearfit.model_weights(ref, raw45, cls, key, 45, points=31).get(stat, 0)


@pytest.mark.parametrize("cls,key,level,stat", [
    ("PRIEST", "holy", 45, "heal"), ("HUNTER", "marksmanship", 45, "rap"), ("PALADIN", "retribution", 45, "ap"),
    ("HUNTER", "beastmastery", 45, "spi"), ("SHAMAN", "enhancement", 45, "int"), ("DRUID", "feral", 45, "int"),
])
def test_a_stat_the_spec_uses_keeps_its_weight(ref, cls, key, level, stat):
    assert w(ref, cls, key, level)[stat] == gear.weights_for(ref, cls, key, level)[0][stat] > 0


def test_healing_power_counts_for_classes_that_heal(ref):
    healing = {"heal": 22}
    assert score(ref, healing, "PRIEST", "holy", 45) > score(ref, healing, "PRIEST", "shadow", 30) \
        > score(ref, healing, "PRIEST", "shadow", 50) > 0
    for cls, key in (("MAGE", "frost"), ("WARLOCK", "affliction"), ("WARRIOR", "fury"), ("ROGUE", "combat")):
        assert score(ref, healing, cls, key, 30) == 0, (cls, key)


def test_damage_and_healing_counts_for_both(ref):
    both = {"sp": 14, "heal": 14}
    assert score(ref, both, "MAGE", "frost", 38) == pytest.approx(14 * 1.1)
    assert score(ref, both, "PRIEST", "holy", 38) == pytest.approx(14 * 0.3 + 14 * 0.7)
    assert score(ref, both, "PRIEST", "shadow", 50) == pytest.approx(14 * (1.0 + 0.025))


def test_school_damage_counts_for_the_schools_a_spec_casts(ref):
    frost = {"spFrost": 19}
    assert score(ref, frost, "MAGE", "frost", 50) > score(ref, frost, "MAGE", "fire", 50) > 0
    assert score(ref, frost, "WARLOCK", "affliction", 50) == 0
    assert score(ref, frost, "PRIEST", "holy", 50) == 0


def test_the_advice_scores_with_the_model(ref):
    """A shadow priest at 32 still heals himself: the healing line of a
    healer's piece counts, at half a holy priest's weight."""
    a = gear.advise(ref, sheet(), level=32)
    assert a["weights"]["heal"] == pytest.approx(0.35)
    u = next(u for u in a["upgrades"] if u["name"] == "Embalmed Shroud")
    assert u["gain"] > 0 and u["fit"]["for_you"] == "spec"


# --- who it's built for --------------------------------------------------------


def test_healer_cloth_for_a_mage_is_against_the_class(ref):
    f = label(ref, 9407, "MAGE", "frost", 37)  # Stoneweaver Leggings: healing 20, damage 7
    assert f["for_you"] == "class" and f["built_for"] == "healers"
    assert gearfit.text(f) == ("Made for healers, not for a mage. "
                               "Healer item: in a group, let the healer roll first.")
    assert label(ref, 9407, "PRIEST", "holy", 37)["for_you"] == "yes"


def test_healer_cloth_for_a_shadow_priest_is_fine_while_levelling(ref):
    """Owner, 2026-11-20: a shadow priest heals himself until Shadowform."""
    at30 = label(ref, 9407, "PRIEST", "shadow", 30)
    assert at30["for_you"] == "spec"
    assert gearfit.text(at30) == ("Not made for your spec. Fine while levelling. "
                                  "In a group, don't roll Need if someone it's made for is there.")
    at50 = label(ref, 9407, "PRIEST", "shadow", 50)
    assert at50["for_you"] == "spec" and at50["label"] == "Not made for your spec: maybe not the best for you"
    # More of it counts at 30 than at 50.
    assert gearfit.credit(w(ref, "PRIEST", "shadow", 30), "healonly") > \
        gearfit.credit(w(ref, "PRIEST", "shadow", 50), "healonly")
    # A warrior: against the class.
    war = label(ref, 9407, "WARRIOR", "fury", 37)
    assert war["for_you"] == "class" and war["factor"] < at30["factor"]


def test_equal_power_cloth_is_a_caster_piece(ref):
    for cls, key in (("MAGE", "frost"), ("PRIEST", "shadow"), ("WARLOCK", "destruction"), ("PRIEST", "holy")):
        assert label(ref, 10009, cls, key, 38)["for_you"] == "yes", (cls, key)  # Red Mageweave Pants


def test_school_damage_gloves_belong_to_that_school(ref):
    f = label(ref, 254097, "WARLOCK", "affliction", 50)  # Frothing Gloves: Frost damage
    assert f["for_you"] == "class" and f["built_for"] == "mages"
    assert "About a third of its stats do nothing for an Affliction warlock" in f["notes"]
    assert label(ref, 254097, "MAGE", "fire", 50)["for_you"] == "yes"


def test_lower_armour_is_said_and_the_wearers_roll_first(ref):
    f = label(ref, 15064, "HUNTER", "marksmanship", 50)  # Warbear Harness: leather Agility
    assert f["armor"] == {"item": "leather", "best": "mail"}
    assert "Leather: you can wear mail" in f["notes"]
    assert f["tip"] == "In a group, Feral druids and rogues usually get first pick"
    rogue = label(ref, 15064, "ROGUE", "subtlety", 50)
    assert rogue["for_you"] == "yes" and rogue["tip"] is None and rogue["armor"] is None


def test_agility_mail_goes_to_hunters_before_a_warrior(ref):
    f = label(ref, 15076, "WARRIOR", "fury", 48)  # Heavy Scorpid Vest
    assert f["tip"] == "In a group, hunters usually get first pick"
    assert "Mail: you can wear plate" in f["notes"]


def test_wasted_stats_on_a_fury_warrior(ref):
    f = label(ref, 250601, "WARRIOR", "fury", 56)  # Breastplate of Salvation
    assert f["for_you"] == "class" and f["label"] == "Made for Holy paladins, not for a warrior"
    assert "Most of its stats do nothing for a Fury warrior" in f["notes"]
    assert "gear-fit:no-mana" in f["kb"] and "gear-fit:class-no-heals" in f["kb"]
    # The same plate for a Retribution paladin: his class's, not his spec's.
    assert label(ref, 250601, "PALADIN", "retribution", 56)["for_you"] == "spec"


def test_an_arms_warrior_wears_a_rogues_agility_piece(ref):
    """Role first: a melee Agility item is built for a warrior too, and the
    tip says who usually rolls first."""
    f = label(ref, 15064, "WARRIOR", "arms", 50)
    assert f["for_you"] == "yes" and f["tip"] == "In a group, Feral druids and rogues usually get first pick"


def test_stamina_alone_fits_anyone(ref):
    info = {"inv": 2, "class": 4, "subclass": 0, "stats": {"sta": 10}}
    f = gearfit.fit(ref, info, "MAGE", "frost", "picked", 30, w(ref, "MAGE", "frost", 30))
    assert f["for_you"] == "any" and f["label"] == "Fits any spec" and f["tip"] is None


def test_every_trap_is_flagged(ref):
    for t in REFS["traps"]:
        f = label(ref, t["item_id"], t["class"], t["spec"], t["level"])
        assert f["for_you"] in ("spec", "class") or f["wasted_pct"] >= 50 or (f["armor"] and f["tip"]), (t, f)


# --- degraded: no spec known ---------------------------------------------------


def test_role_unknown_asks_for_it(ref):
    f = label(ref, 9407, "PRIEST", "shadow", 37, how="default")
    assert f["for_you"] == "unknown" and f["label"] == "Role unknown: pick your role in settings"
    assert f["factor"] == 1.0
    # A mage's specs are all damage casters: the class says the role.
    assert label(ref, 9407, "MAGE", "frost", 37, how="default")["for_you"] == "class"
    a = gear.advise(ref, sheet(spec=None), level=32)
    assert a["spec"]["source"] == "default" and a["spec"]["role"] is None
    assert {u["fit"]["for_you"] for u in a["upgrades"]} <= {"unknown", "any"}


# --- the ranking ---------------------------------------------------------------


def test_group_drops_for_someone_else_come_after(ref):
    drop = label(ref, 9407, "MAGE", "frost", 37, sources=[{"kind": "drop", "dungeon": True}])
    quest = label(ref, 9407, "MAGE", "frost", 37, sources=[{"kind": "quest"}])
    assert drop["factor"] == pytest.approx(gearfit.CLASS * gearfit.GROUP, abs=1e-3) and quest["factor"] == gearfit.CLASS
    shadow = label(ref, 9407, "PRIEST", "shadow", 30, sources=[{"kind": "quest"}])
    assert shadow["factor"] == gearfit.OTHER
    mine = label(ref, 9407, "PRIEST", "holy", 37, sources=[{"kind": "drop", "dungeon": True}])
    assert mine["factor"] == 1.0


def test_close_options_rank_the_on_spec_one_first(ref):
    """Same slot, nearly the same gain: the head piece built for healers
    gains more for a shadow priest but ranks after the one built for him,
    and names it as the alternative, with where it comes from."""
    a = gear.advise(ref, sheet(), level=32)
    ups = a["upgrades"]
    healer = next(u for u in ups if u["name"] == "Embalmed Shroud")
    mine = next(u for u in ups if u["name"] == "Electromagnetic Gigaflux Reactivator")
    assert healer["gain"] > mine["gain"]
    assert ups.index(mine) < ups.index(healer)
    assert healer["fit"]["for_you"] == "spec" and mine["fit"]["for_you"] == "yes"
    alt = healer["fit"]["alternative"]
    assert alt["item_id"] == mine["item_id"] and alt["sources"]
    # The healer ranks it first, with no alternative to offer.
    holy = gear.advise(ref, sheet(), level=32, spec="holy")
    hu = next(u for u in holy["upgrades"] if u["name"] == "Embalmed Shroud")
    assert hu["fit"]["for_you"] == "yes" and hu["value"] == hu["gain"]


def test_same_gain_different_fit_different_order():
    ups = [{"name": "healer piece", "item_id": 1, "slot": 1, "slot_name": "Head", "gain": 20.0, "now": True,
            "vs_unknown": False, "fit": {"for_you": "spec", "factor": gearfit.OTHER * gearfit.GROUP}},
           {"name": "my piece", "item_id": 2, "slot": 1, "slot_name": "Head", "gain": 20.0, "now": True,
            "vs_unknown": False, "fit": {"for_you": "yes", "factor": 1.0, "label": "Built for your spec"}}]
    for u in ups:
        u["value"] = u["gain"] * u["fit"]["factor"]
    ups.sort(key=lambda u: (not u["now"], u["vs_unknown"], -u["value"], u["item_id"]))
    assert [u["name"] for u in ups] == ["my piece", "healer piece"]
    gear.alternatives(ups)
    assert ups[1]["fit"]["alternative"]["name"] == "my piece"


def test_the_suggested_dungeon_follows_the_spec(ref):
    """The same character at 32: Shadow is sent to the City of Dalaran,
    Holy to Razorfen Kraul, whose drops are built for a healer."""
    def first(spec):
        a = gear.advise(ref, sheet(), level=32, spec=spec, every=True)
        return [c["title"] for c in insights.dungeon_claims(32, [(u, {}) for u in a["upgrades_all"] if u["now"]])]
    assert first("shadow")[0] == "City of Dalaran"
    assert first("holy")[0] == "Razorfen Kraul"


def test_a_dungeon_of_someone_elses_drops_ranks_below_one_of_mine():
    def up(name, zone, factor):
        return ({"name": name, "item_id": hash(name) % 1000, "slot": 1, "gain": 20.0, "value": 20.0 * factor,
                 "sources": [{"kind": "drop", "dungeon": True, "zone": zone, "levels": [20, 30], "chance_pct": 20}]},
                {})
    reach = [up("healer robe", "Healer Hall", gearfit.OTHER * gearfit.GROUP), up("my robe", "My Hall", 1.0)]
    assert [c["title"] for c in insights.dungeon_claims(25, reach)] == ["My Hall", "Healer Hall"]
