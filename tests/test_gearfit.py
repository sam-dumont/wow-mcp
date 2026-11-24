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


def w(ref, cls, key, level, mode="lvl"):
    return gearfit.spec_weights(ref, cls, key, mode)


def score(ref, stats, cls, key, level, inv=7):
    return gear.score_with({"inv": inv, "class": 4, "subclass": 1, "stats": stats}, w(ref, cls, key, level), False)


def label(ref, item_id, cls, key, level, how="picked", sources=None, mode="lvl"):
    info = ref.item(item_id)
    return gearfit.fit(ref, info, cls, key, how, level, w(ref, cls, key, level, mode), sources, mode=mode)


# --- the stats each spec can use ----------------------------------------------
#
# Who an item is built for reads ForeverDB.net's weights in the mode the
# character scores in (Leveling unless picked otherwise): the same at every
# level, never Polestar's (owner, 2026-11-24).


@pytest.mark.parametrize("cls,key,stat", [
    ("WARRIOR", "fury", "spi"), ("WARRIOR", "arms", "spi"), ("ROGUE", "combat", "spi"),
    ("WARRIOR", "fury", "int"), ("MAGE", "frost", "heal"), ("ROGUE", "combat", "heal"),
    ("PRIEST", "shadow", "heal"), ("DRUID", "restoration", "ap"),
])
def test_a_stat_the_spec_cant_use_is_worth_nothing(ref, cls, key, stat):
    assert w(ref, cls, key, 30).get(stat, 0) == 0


@pytest.mark.parametrize("cls,key,stat", [
    ("PRIEST", "holy", "heal"), ("HUNTER", "marksmanship", "rap"), ("PALADIN", "retribution", "ap"),
    ("HUNTER", "beastmastery", "agi"), ("SHAMAN", "enhancement", "str"), ("DRUID", "feral", "agi"),
])
def test_a_stat_the_spec_uses_keeps_its_weight(ref, cls, key, stat):
    assert w(ref, cls, key, 45)[stat] == gear.weights_for(ref, cls, key, "lvl")[0][stat] > 0


def test_healing_power_counts_for_healers_only(ref):
    healing = {"heal": 22}
    # Leveling weighs a healer's healing at half the guide's.
    assert score(ref, healing, "PRIEST", "holy", 45) == pytest.approx(11)
    for cls, key in (("PRIEST", "shadow"), ("MAGE", "frost"), ("WARLOCK", "affliction"), ("WARRIOR", "fury")):
        assert score(ref, healing, cls, key, 30) == 0, (cls, key)


def test_damage_and_healing_is_one_stat(ref):
    """"+14 damage and healing" is one line, at the spec's dmgheal weight."""
    both = {"sp": 14, "heal": 14}
    assert score(ref, both, "MAGE", "frost", 38) == pytest.approx(14)
    assert score(ref, both, "PRIEST", "holy", 38) == pytest.approx(14)
    assert score(ref, both, "PALADIN", "retribution", 38) == pytest.approx(14 * 0.3)


def test_school_damage_counts_for_the_schools_a_spec_casts(ref):
    frost = {"spFrost": 19}
    assert score(ref, frost, "MAGE", "frost", 50) == pytest.approx(19)
    for cls, key in (("MAGE", "fire"), ("WARLOCK", "affliction"), ("PRIEST", "holy")):
        assert score(ref, frost, cls, key, 50) == 0, (cls, key)


def test_the_advice_scores_with_the_mode(ref):
    """The page's scores and its fit labels use the mode's weights
    (Leveling by default)."""
    a = gear.advise(ref, sheet(), level=32)
    assert a["mode"]["key"] == "lvl" and a["weights"]["sta"] == 0.8
    pve = gear.advise(ref, sheet(), level=32, mode="pve")
    assert pve["mode"]["key"] == "pve" and pve["weights"]["sta"] == 0.1


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
    # A warrior: against the class.
    war = label(ref, 9407, "WARRIOR", "fury", 37)
    assert war["for_you"] == "class" and war["factor"] < at30["factor"]


def test_equal_power_cloth_is_a_caster_piece(ref):
    for cls, key in (("MAGE", "frost"), ("PRIEST", "shadow"), ("WARLOCK", "destruction"), ("PRIEST", "holy")):
        assert label(ref, 10009, cls, key, 38)["for_you"] == "yes", (cls, key)  # Red Mageweave Pants


def test_school_damage_gloves_belong_to_that_school(ref):
    f = label(ref, 254097, "WARLOCK", "affliction", 50)  # Frothing Gloves: Frost damage
    assert f["for_you"] == "class" and f["built_for"] == "mages"
    assert "About half its stats do little for an Affliction warlock" in f["notes"]
    # In PvE (the guide's weights), the Frost damage is all that's lost.
    pve = label(ref, 254097, "WARLOCK", "affliction", 50, mode="pve")
    assert "About a third of its stats do nothing for an Affliction warlock" in pve["notes"]
    assert label(ref, 254097, "MAGE", "fire", 50)["for_you"] == "yes"


def test_lower_armour_is_said_and_the_wearers_roll_first(ref):
    f = label(ref, 15064, "HUNTER", "marksmanship", 50)  # Warbear Harness: leather Agility
    assert f["armor"] == {"item": "leather", "best": "mail"}
    assert "Leather: you can wear mail" in f["notes"]
    assert f["tip"] == "In a group, rogues and Feral druids usually get first pick"
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
    assert f["for_you"] == "yes" and f["tip"] == "In a group, rogues and Feral druids usually get first pick"


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
    """A head piece built for healers ranks at 90% of its gain for a shadow
    priest and names the one built for him as the alternative, with where
    it comes from; a holy priest gets it at full value."""
    a = gear.advise(ref, sheet(), level=32)
    healer = next(u for u in a["upgrades"] if u["name"] == "Filigreed Pristine Circlet")
    assert healer["fit"]["for_you"] == "spec" and healer["value"] == pytest.approx(
        gear.ranking_value(healer["gain"], healer["score"], gear.EFFORT.index(healer["effort"]), gearfit.OTHER), abs=0.01)
    alt = healer["fit"]["alternative"]
    assert alt["name"] == "Filigreed Shadow Circlet" and alt["sources"]
    holy = gear.advise(ref, sheet(), level=32, spec="holy")
    hu = next(u for u in holy["upgrades"] if u["name"] == "Filigreed Pristine Circlet")
    assert hu["fit"]["for_you"] == "yes" and hu["value"] == pytest.approx(
        gear.ranking_value(hu["gain"], hu["score"], gear.EFFORT.index(hu["effort"]), 1.0), abs=0.01)


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
    """The same character at 32 on PvE weights: Shadow is sent to the City
    of Dalaran; Holy never there, Razorfen Kraul's healer drops in its first
    two (the Wetlands excavation first since the items come from the game's
    tables, 2026-11-24)."""
    def first(spec):
        a = gear.advise(ref, sheet(), level=32, spec=spec, every=True, mode="pve")
        return [c["title"] for c in insights.dungeon_claims(32, [(u, {}) for u in a["upgrades_all"] if u["now"]])]
    assert first("shadow")[0] == "City of Dalaran"
    holy = first("holy")
    assert "City of Dalaran" not in holy[:2] and "Razorfen Kraul" in holy[:2]


def test_a_dungeon_of_someone_elses_drops_ranks_below_one_of_mine():
    def up(name, zone, factor):
        return ({"name": name, "item_id": hash(name) % 1000, "slot": 1, "gain": 20.0, "value": 20.0 * factor,
                 "sources": [{"kind": "drop", "dungeon": True, "zone": zone, "levels": [20, 30], "chance_pct": 20}]},
                {})
    reach = [up("healer robe", "Healer Hall", gearfit.OTHER * gearfit.GROUP), up("my robe", "My Hall", 1.0)]
    assert [c["title"] for c in insights.dungeon_claims(25, reach)] == ["My Hall", "Healer Hall"]
