"""Gear advice (wowstats.gear) on the bundled data: the items from the
game's own tables (data/game-items.json), where they come from from
Polestar's gear data and forever-db, scored with ForeverDB.net's weights
(data/foreverdb-weights.json).

The numbers below are worked out by hand from ForeverDB's formula
(tests/test_gear_foreverdb.py checks it against their own scores item by
item). The character is a level 32 Gnome Priest, Shadow, Leveling weights,
wearing a two-handed staff and one trinket."""

import time

import pytest

from wowstats import gear

WORN = {2: 281321, 3: 5820, 5: 270261, 6: 3344, 7: 270036, 8: 4313, 9: 273808, 10: 270029, 11: 1116,
        12: 281304, 13: 273298, 15: 7356, 16: 1317, 18: 7001}


def sheet(**kw) -> dict:
    s = {"items": [{"slot": k, "item_id": v, "name": f"worn {v}"} for k, v in WORN.items()],
         "class": "PRIEST", "race": "Gnome", "faction": "Alliance", "spec": "Shadow",
         "professions": [{"name": "Tailoring", "rank": 160}]}
    return {**s, **kw}


@pytest.fixture(scope="module")
def ref():
    r = gear.reference()
    assert r.available, r.why
    return r


@pytest.fixture
def priest_gear(ref):
    w, _ = gear.weights_for(ref, "PRIEST", "shadow", "lvl")
    worn = gear.worn_from(ref, sheet()["items"], w)
    return w, worn


def test_the_file_is_polestars(ref):
    d = ref.data
    assert d["polestar"]["license"] == "GPL-3.0-only" and d["polestar"]["tag"].startswith("v")
    assert d["leveling"]["source"] == "ForeverLevelingBiS" and d["leveling"]["version"] == "v0.12.0-beta"
    assert d["leveling"]["licence"] == "MIT"
    assert len(d["items"]) > 9000 and len(d["sources"]) > 3000


def test_leveling_weights(ref):
    """Shadow's Leveling weights: ForeverDB's rule on the class guide's
    (Stamina 0.8 x spell power 1, hit 1.2 x 0.7, Spirit 0.2 x 0.6, armour
    0.02 for cloth, a wand's DPS 1.5), the same at every level."""
    w, info = gear.weights_for(ref, "PRIEST", "shadow", "lvl")
    assert info["source"] == "https://foreverdb.net" and info["mode"] == "lvl"
    assert w["dmgheal"] == 1 and w["sta"] == 0.8 and w["hitRating"] == 0.84 and w["spi"] == 0.12
    assert w["armor"] == 0.02 and w["wand"] == 1.5 and not w.get("dps")
    pve, _ = gear.weights_for(ref, "PRIEST", "shadow", "pve")
    assert pve["sta"] == 0.1 and pve["hitRating"] == 1.2 and not pve.get("wand")


def test_hand_checked_scores(ref, priest_gear):
    w, worn = priest_gear
    # Worn staff 1317, as Forever's client has it (no spell power since
    # Forever re-itemised it; Polestar's Classic numbers said 20): 9 Spirit x
    # 0.12, 1 Strength x 0; its melee DPS counts nothing for a priest.
    assert worn.score(16) == pytest.approx(1.08)
    # Staff 1155: 11 Spirit x 0.12.
    info = ref.item(1155)
    r = gear.compare(info, 1155, worn, w, False)
    assert (r["slot"], r["score"]) == (16, pytest.approx(1.32))
    # A two-hander replaces main hand + off hand (empty here).
    assert r["delta"] == pytest.approx(0.24) and r["pct"] == pytest.approx(0.24 / 1.08 * 100)
    # 2042: 5 Intellect x 0.3 + 6 Spirit x 0.12.
    r = gear.compare(ref.item(2042), 2042, worn, w, False)
    assert r["delta"] == pytest.approx(1.5 + 0.72 - 1.08)
    # Trinket 744 (8 Spirit): the empty trinket slot goes first, no percent.
    r = gear.compare(ref.item(744), 744, worn, w, False)
    assert (r["slot"], r["delta"], r["pct"], r["empty"]) == (14, pytest.approx(0.96), None, True)
    # Robe 2034 for the chest (3 + 12 x 0.12 + 43 armour x 0.02) against 270261 (8 + 0.48 + 0.7): a loss.
    r = gear.compare(ref.item(2034), 2034, worn, w, False)
    assert (r["slot"], r["delta"]) == (5, pytest.approx(5.3 - 9.18))


def test_school_bonus_adds_up():
    """School damage counts on top of spell power, every school (ForeverDB)."""
    info = {"inv": 1, "class": 4, "subclass": 1, "stats": {"spFire": 34, "spShadow": 34}}
    assert gear.score_with(info, {"sp": 1, "spFire": 0.6, "spShadow": 0.8}) == pytest.approx(34 * 1.4)


def test_usability(ref):
    priest = lambda iid, lv=32: gear.usability(ref, ref.item(iid), "PRIEST", lv)  # noqa: E731
    assert priest(1155)[0]
    mail = next(i for i, it in ref.data["items"].items() if it["class"] == 4 and it["subclass"] == 3
                and it["inv"] == 5)
    assert gear.usability(ref, ref.item(mail), "PRIEST", 32)[:2] == (False, "armor")
    # A Shaman learns mail at 40: usable, worn from 40.
    ok, _, at = gear.usability(ref, ref.item(mail), "SHAMAN", 32)
    assert ok and at == max(40, ref.item(mail)["req"])


def test_spec_order(ref):
    s = sheet(spec="Shadow")
    assert gear.resolve_spec(ref, "PRIEST", s) == ("shadow", "picked")
    # Polestar's Stat weights setting is never read (owner, 2026-11-24).
    assert gear.resolve_spec(ref, "PRIEST", {**s, "polestar_spec": "holy"}) == ("shadow", "picked")
    assert gear.resolve_spec(ref, "PRIEST", {**s, "polestar_spec": "holy"}, "discipline") == ("discipline", "page")
    assert gear.resolve_spec(ref, "PRIEST", {}) == ("shadow", "default")
    assert gear.resolve_spec(ref, "DRUID", {"spec": "Bear"}) == ("feral", "picked")
    assert gear.resolve_spec(ref, "HUNTER", {"spec": "Beast Mastery"}) == ("beastmastery", "picked")



def test_spec_from_talents(ref):
    """Polestar's mostPoints: the tree with the most points, by name, else
    by position. Only a hint, when nothing the player chose says a spec
    (owner, 2026-11-18, restored 2026-11-20)."""
    t = [{"tree": "Discipline", "points": 5}, {"tree": "Holy", "points": 14}, {"tree": "Shadow", "points": 3}]
    assert gear.resolve_spec(ref, "PRIEST", sheet(spec=None, talents=t)) == ("holy", "talents")
    assert gear.resolve_spec(ref, "PRIEST", {"talents": t, "polestar_spec": "shadow"}) == ("holy", "talents")
    s = sheet(spec="Shadow", talents=t)
    assert gear.resolve_spec(ref, "PRIEST", s) == ("shadow", "picked")
    assert gear.resolve_spec(ref, "PRIEST", s, "discipline") == ("discipline", "page")


def test_shadow_priest_with_most_points_in_discipline_is_scored_shadow(ref):
    """Leveling Discipline on the way to Shadowform: whatever the player
    chose (a gear addon's pick, the site's WEIGHTS) beats the points; the points decide only when nothing does."""
    disc = [{"tree": "Discipline", "points": 16}, {"tree": "Holy", "points": 0}, {"tree": "Shadow", "points": 7}]
    assert gear.resolve_spec(ref, "PRIEST", sheet(spec="Shadow", talents=disc)) == ("shadow", "picked")
    assert gear.resolve_spec(ref, "PRIEST", {"talents": disc}, None, "shadow") == ("shadow", "server")
    assert gear.resolve_spec(ref, "PRIEST", {"talents": disc}) == ("discipline", "talents")
    # A tie goes to the first tree; no names: position; no points: next step.
    assert gear.most_points(ref, "PRIEST", [{"tree": "Discipline", "points": 7}, {"tree": "Shadow", "points": 7}]) \
        == "discipline"
    assert gear.most_points(ref, "MAGE", [{"tree": None, "points": 0}, {"tree": None, "points": 11}]) == "fire"
    assert gear.resolve_spec(ref, "PRIEST", sheet(spec="Shadow", talents=[{"tree": "Holy", "points": 0}])) \
        == ("shadow", "picked")
    # A stored sheet's talents (Vulcan SimBench's, before 2026-11-20) aren't
    # read: talents come from Battle.net, which Views puts in.
    assert gear.sheet({"talents": '[{"tree": "Holy", "points": 14}]', "class": "PRIEST"})["talents"] is None


def test_advice(ref):
    a = gear.advise(ref, sheet(), level=32, zone="Hillsbrad Foothills")
    assert a["spec"]["key"] == "shadow" and a["spec"]["source"] == "picked"
    assert [s["slot"] for s in a["slots"]] == list(gear.EQUIPPED_SLOTS)
    for s in a["slots"]:
        for b in (s["best"], s["next"]):
            if b:
                assert b["gain"] > 0 and b["sources"]
        if s["best"]:
            assert s["best"]["level"] <= 32
        if s["next"]:
            assert 32 < s["next"]["level"] <= 35
            if s["best"]:
                assert s["next"]["score"] > s["best"]["score"]
    # The two trinket slots never suggest the same item.
    t = {s["slot"]: s["best"] and s["best"]["item_id"] for s in a["slots"] if s["slot"] in (13, 14)}
    assert t[13] is None or t[13] != t[14]
    ups = a["upgrades"]
    assert ups and all(u["gain"] > 0 for u in ups)
    now = [u for u in ups if u["now"]]
    assert all(u["level"] <= 32 for u in now) and ups[: len(now)] == now
    # One reward per quest offering a choice: the pick (Rewards.lua RankChoices).
    seen = set()
    for u in ups:
        for s in u["sources"]:
            if s["kind"] == "quest" and s["choices"] > 1:
                assert s["quest_id"] not in seen
                seen.add(s["quest_id"])
    # Only this side's quests, none of another class.
    for u in ups:
        for s in u["sources"]:
            if s["kind"] == "quest":
                q = ref.data["quests"][str(s["quest_id"])]
                assert q["side"] in (1, 3)
                assert not q["class"] or q["class"] & 16


def test_quests_handed_in_are_gone(ref):
    a = gear.advise(ref, sheet(), level=32)
    quest = next(s["quest_id"] for u in a["upgrades"] for s in u["sources"] if s["kind"] == "quest")
    b = gear.advise(ref, sheet(), level=32, done={quest})
    assert all(s.get("quest_id") != quest for u in b["upgrades"] for s in u["sources"])


def test_horde_gets_horde_quests(ref):
    a = gear.advise(ref, sheet(race="Troll", faction="Horde", spec=None), level=32)
    for u in a["upgrades"]:
        for s in u["sources"]:
            if s["kind"] == "quest":
                assert ref.data["quests"][str(s["quest_id"])]["side"] in (2, 3)


def test_errors(ref, monkeypatch):
    with pytest.raises(gear.GearError, match="class"):
        gear.advise(ref, sheet(**{"class": None}), level=32)
    with pytest.raises(gear.GearError, match="level"):
        gear.advise(ref, sheet(), level=None)
    monkeypatch.setenv("GEAR_REFERENCE_FILE", "off")
    assert not gear.reference().available


def test_sheet_from_stored_row():
    row = {"items": '[{"slot": 16, "item_id": 1317}]', "class": "PRIEST", "spec": "Shadow",
           "gear_adjust": '[{"spec": "shadow", "stat": "sta", "x": 1.5}]', "min_upgrade": "3",
           "polestar_spec": "holy"}
    s = gear.sheet(row, {"race": "Gnome", "faction": "Alliance"})
    assert s["items"] == [{"slot": 16, "item_id": 1317}] and s["race"] == "Gnome"
    # An older shipper's Polestar gear settings stay stored, never read (owner, 2026-11-24).
    assert not {"gear_adjust", "min_upgrade", "polestar_spec"} & set(s)


def test_weapons_come_as_setups(ref):
    """Against a worn two-hander (the staff 1317), a one-hander only ever comes
    with the off hand it is worn with, and the gain is the whole setup's:
    both hands now against both hands before (Gear.lua pairs it with the best
    bag piece; the server pairs it with the best one the character can get)."""
    a = gear.advise(ref, sheet(), level=32, zone="Hillsbrad Foothills")
    worn = next(s for s in a["slots"] if s["slot"] == 16)["current"]["score"]
    for u in a["upgrades"] + [s[k] for s in a["slots"] for k in ("best", "next") if s[k]]:
        inv = ref.item(u["item_id"])["inv"]
        if inv in (13, 21, 14, 22, 23):
            assert u["with"] and u["with"]["kind"] in ("get", "empty", "worn"), u
            if u["with"]["kind"] == "get":
                partner = u["with"]
                # The gain is both pieces against the staff.
                assert u["gain"] == pytest.approx(u["score"] + partner["score"] - worn, abs=0.02)
                assert u["with"]["sources"]
    # A setup is listed once, from whichever of its two items comes first.
    pairs = [frozenset((u["item_id"], u["with"].get("item_id"))) for u in a["upgrades"] if u.get("with")]
    assert len(pairs) == len(set(pairs))


def test_gain_in_points_and_share(ref):
    a = gear.advise(ref, sheet(), level=32)
    assert a["gear_score"] == pytest.approx(sum((s["current"] or {}).get("score") or 0 for s in a["slots"]), abs=0.1)
    for u in a["upgrades"]:
        assert u["gain_share_pct"] == pytest.approx(u["gain"] / a["gear_score"] * 100, abs=0.06)
    # Within the same share of BiS, ordered by points (the gain, less for an
    # item built for someone else: wowstats.gearfit), not by percent over a
    # weak item.
    now = [u for u in a["upgrades"] if u["now"] and not u["vs_unknown"]]
    for k in {gear.bis_order(u) for u in now}:
        same = [u["value"] for u in now if gear.bis_order(u) == k]
        assert same == sorted(same, reverse=True), k
    assert all(u["value"] == pytest.approx(u["gain"] * u["fit"]["factor"], abs=0.02) for u in a["upgrades"])


def test_one_line_per_source(ref):
    """Bloodband Bracers: the data has The Captain's Chest twice (one copy
    without a zone): the player reads it once, with its zone."""
    who = gear.Who(cls="PRIEST", level=35, side=1, race_id=7, professions={}, done=set(), zone=None)
    info = ref.item(11469)
    srcs = gear._dedupe([x for x in (gear._source(ref, s, who, info) for s in ref.data["sources"]["11469"]) if x])
    assert [(s["name"], s["zone"]) for s in srcs] == [("The Captain's Chest", "Stranglethorn Vale")]


def test_spec_said_in_plain_words(ref):
    t = [{"tree": "Discipline", "points": 5}, {"tree": "Holy", "points": 0}, {"tree": "Shadow", "points": 21}]
    a = gear.advise(ref, sheet(spec=None, talents=t), level=32)
    assert a["spec"]["why"].startswith("Scored for Shadow because most of your talent points are in Shadow "
                                       "(Shadow 21, Discipline 5")
    a = gear.advise(ref, sheet(spec_from="ForeverLevelingBiS"), level=32)
    assert a["spec"]["why"] == "Scored for Shadow because you picked Shadow in ForeverLevelingBiS."
    a = gear.advise(ref, sheet(spec=None), level=32)
    assert a["spec"]["source"] == "default" and "Battle.net" in a["spec"]["why"]
    assert "simx" not in a["spec"]["why"]


@pytest.mark.parametrize("srcs,want", [
    ([{"kind": "craft", "profession": "Tailoring", "skill": 200, "your_rank": 119, "can_make": False}],
     [{"kind": "level", "need": 35, "have": 32},
      {"kind": "skill", "profession": "Tailoring", "need": 200, "have": 119}]),
    ([{"kind": "craft", "profession": "Tailoring", "skill": 200, "your_rank": None, "can_make": False}],
     [{"kind": "level", "need": 35, "have": 32},
      {"kind": "buy", "profession": "Tailoring", "need": 200, "have": None}]),
    ([{"kind": "craft", "profession": "Tailoring", "skill": 200, "your_rank": 119, "can_make": False},
      {"kind": "drop", "zone": "Uldaman"}], [{"kind": "level", "need": 35, "have": 32}]),
])
def test_gates_name_the_real_blocker(srcs, want):
    """Icy Cloak: level 35 to wear, and Tailoring 200 to make (Testpriest has
    119). Without the profession it's a buy, not a wait; another source lifts
    the skill gate."""
    assert gear.gates(35, 32, srcs) == want


# --- random suffixes ---------------------------------------------------------

ELDERS_CLOAK = 7356  # uncommon cloak, item level 30: armor only, the rest is its suffix
WOOLEN_CAPE = 2584  # fixed stats: +1 healing, +1 spell damage, +1 Spirit


def suffix_sheet(**kw) -> dict:
    s = sheet(**kw)
    for it in s["items"]:
        if it["slot"] == 15:
            it["name"] = "Elder's Cloak of the Elder"
    return s


def test_worn_random_suffix_scored_on_its_likely_suffix(ref):
    """No Battle.net stats: the cloak is scored on the median suffix of the
    fixed-stat uncommon cloaks around item level 30 for a Shadow priest,
    not on its 21 armor alone, so the cheap capes aren't upgrades."""
    a = gear.advise(ref, suffix_sheet(), level=32, every=True)
    back = next(s for s in a["slots"] if s["slot"] == 15)
    cur = back["current"]
    assert cur["random_suffix"] is True and cur["stats"] == {"armor": 21}  # its own stats shown, not the guess
    w, _ = gear.weights_for(ref, "PRIEST", "shadow", "lvl")
    assert gear.likely_suffix(ref, ref.item(ELDERS_CLOAK), w)
    assert cur["score"] > 3  # 0.42 on armor alone
    assert not any(u["item_id"] == WOOLEN_CAPE for u in a["upgrades_all"])
    assert all(u["gain_pct"] is None for u in a["upgrades_all"] if u["slot"] == 15)

    # On its base stats alone (before), the cape was an upgrade.
    before = gear.advise(ref, sheet(), level=32, every=True)
    assert any(u["item_id"] == WOOLEN_CAPE for u in before["upgrades_all"])


def test_worn_random_suffix_scored_on_battlenet_stats(ref):
    """Battle.net's equipment answer for the same item: its real stats,
    a plain comparison (gain_pct back); for another item (changed since the
    daily refresh) it's ignored."""
    real = gear.worn_stats({"back": {"item_id": ELDERS_CLOAK, "stats": {"INTELLECT": 1.0, "ARMOR": 21.0}}})
    assert real == {15: {"item_id": ELDERS_CLOAK, "stats": {"int": 1.0}}}
    w, _ = gear.weights_for(ref, "PRIEST", "shadow", "lvl")
    worn = gear.worn_from(ref, suffix_sheet()["items"], w, real)
    assert 15 not in worn.unknown
    assert worn.score(15) == pytest.approx(gear.score_with(
        {**ref.item(ELDERS_CLOAK), "stats": {"armor": 21, "int": 1.0}}, w, False))
    a = gear.advise(ref, suffix_sheet(worn_stats=real), level=32, every=True)
    cur = next(s for s in a["slots"] if s["slot"] == 15)["current"]
    assert cur["random_suffix"] is False and cur["stats"] == {"armor": 21, "int": 1.0}
    cape = next(u for u in a["upgrades_all"] if u["item_id"] == WOOLEN_CAPE)  # a weak roll: the cape wins
    assert cape["gain_pct"] is not None and cape["vs_unknown"] is False

    other = gear.worn_from(ref, suffix_sheet()["items"], w, {15: {"item_id": 1, "stats": {"int": 9}}})
    assert 15 in other.unknown


def test_candidate_random_suffix_only_for_drops(ref):
    """An uncommon with only armor or damage that only drops rolls a suffix
    (scored on the likely one); a quest or crafted one is a fixed item
    (a proc, no stat)."""
    srcs = ref.data["sources"]
    drop = next(int(k) for k, v in ref.data["items"].items() if v["quality"] == 2 and set(v["stats"]) <= {"armor"}
                and k not in srcs)
    quest = next(int(k) for k, v in ref.data["items"].items() if v["quality"] == 2
                 and set(v["stats"]) <= {"armor", "dps"} and any(s["kind"] == "quest" for s in srcs.get(k, [])))
    w, _ = gear.weights_for(ref, "PRIEST", "shadow", "lvl")
    assert gear.suffix_base(ref, drop, ref.item(drop))
    assert gear.item_for(ref, drop, w)["suffix"] == "likely"
    assert not gear.suffix_base(ref, quest, ref.item(quest))
    assert gear.item_for(ref, quest, w) == ref.item(quest)


def test_store_reads_battlenet_equipment():
    """stores.character_equipment from the exporter's gauges, then
    worn_stats: the slot types to inventory slots, the stats to the item
    table's keys (armor and the rest the scoring doesn't take dropped)."""
    from wowstats.stores import Store

    class Fake(Store):
        def profile_query(self, q):
            if "refreshed" in q:  # read an hour ago (30 days at most: Blizzard's terms)
                return [{"metric": {}, "value": [1, str(time.time() - 3600)]}]
            if "wow_character_equipped_stat" in q:
                return [{"metric": {"slot": "back", "stat": "INTELLECT"}, "value": [1, "4"]},
                        {"metric": {"slot": "back", "stat": "FIRE_RESISTANCE"}, "value": [1, "5"]}]
            return [{"metric": {"slot": "back"}, "value": [1, "7356"]},
                    {"metric": {"slot": "main_hand"}, "value": [1, "1317"]}]

    rows = Fake("http://vl.invalid", profile_vm_url="http://vm.invalid").character_equipment("Testpriest-Fixture")
    assert rows == {"back": {"item_id": 7356, "stats": {"INTELLECT": 4.0, "FIRE_RESISTANCE": 5.0}},
                    "main_hand": {"item_id": 1317, "stats": {}}}
    assert gear.worn_stats(rows) == {15: {"item_id": 7356, "stats": {"int": 4.0}}, 16: {"item_id": 1317, "stats": {}}}
    assert Store("http://vl.invalid", profile_vm_url="").character_equipment("X") == {}
    assert gear.armory_worn(object(), "X") == {}  # a store without it: the likely suffix


# --- where the worn item stands against BiS (owner, 2026-11-21) -------------


def _worn(scores: dict, unknown=()) -> gear.Worn:
    return gear.Worn(ids={sl: 100 + sl for sl in scores}, infos={sl: {"inv": 1} for sl in scores},
                     scores=dict(scores), unknown=set(unknown))


def _now(*items) -> dict:
    """per_slot's "now" list: (delta, -id, candidate, option) for (id, score)."""
    return {"now": [(0, -i, {"id": i}, {"new": v}) for i, v in items], "soon": []}


def _standings(worn, per_slot):
    return gear.standings(per_slot, worn, gear.WeaponSetups([], worn, {}, False, 32), 32)


def test_standing_bis_second_percent():
    worn = _worn({1: 20.0, 3: 15.0, 5: 15.0, 6: 4.0})
    st = _standings(worn, {1: _now((1, 18.0), (2, 12.0)), 3: _now((3, 20.0)), 5: _now((4, 20.0), (5, 18.0)),
                           6: _now((6, 12.0))})
    assert gear.standing_out(st[1]) == {"rank": 1, "pct": 100, "label": "BiS"}
    assert gear.standing_out(st[3]) == {"rank": 2, "pct": 75, "label": "2nd"}
    assert gear.standing_out(st[5]) == {"rank": None, "pct": 75, "label": "75% of BiS"}
    # Never 100% for something that isn't BiS.
    assert gear.standing_out(st[6])["pct"] == 33


def test_standing_nothing_rather_than_a_made_up_number():
    worn = _worn({1: 20.0, 3: 15.0}, unknown={3})
    st = _standings(worn, {3: _now((3, 20.0))})
    assert st[1] is None  # no item for the slot: no BiS list to stand against
    assert st[3] is None  # a random suffix scored on a guess
    assert st[16] is None and st[17] is None  # no weapon to measure the hands against
    # Nothing worn known (no sheet): no standing at all, not 0% everywhere.
    assert _standings(gear.Worn(), {1: _now((1, 5.0))}) == {}


def test_standing_rings_count_the_best_two():
    # Worn 10 (BiS pair: 12 and 10) and 4; candidates 12, 8.
    worn = _worn({11: 10.0, 12: 4.0})
    st = _standings(worn, {11: _now((1, 12.0), (2, 8.0)), 12: _now((1, 12.0), (2, 8.0))})
    assert st[11]["rank"] == 1
    # The weaker ring against the BiS ring it would make room for (12); no
    # "2nd" for a pair.
    assert st[12]["rank"] is None and st[12]["pct"] == 33


def test_upgrades_ranked_by_share_of_bis(ref):
    """The slot furthest from BiS first, then (the same start, the same
    BiS) the points: the item that closes most of the gap; an upgrade
    without a BiS to measure against after (owner, 2026-11-21: "the percent
    of BiS should be used to order and rank")."""
    a = gear.advise(ref, sheet(), level=32)
    stand = {s["slot"]: s["standing"] for s in a["slots"]}
    now = [u for u in a["upgrades"] if u["now"]]
    keys = [gear.bis_order(u) for u in now]
    assert keys == sorted(keys)
    ranked = [u for u in now if u["bis"]]
    for pct in {u["bis"]["from_pct"] for u in ranked}:
        same = [u for u in ranked if u["bis"]["from_pct"] == pct and not u["vs_unknown"]]
        assert [u["value"] for u in same] == sorted((u["value"] for u in same), reverse=True)
    assert ranked and ranked[0]["bis"]["from_pct"] == min(u["bis"]["from_pct"] for u in ranked)
    for u in ranked:
        b = u["bis"]
        assert 0 <= b["from_pct"] <= b["to_pct"] <= 100
        if stand.get(u["slot"]):
            assert b["from_pct"] == stand[u["slot"]]["pct"]
    # Each slot's standing, where there is one, says BiS, 2nd or a percent.
    labels = [s["standing"]["label"] for s in a["slots"] if s["standing"]]
    assert labels and all(x in ("BiS", "2nd") or x.endswith("% of BiS") for x in labels)


def test_no_sheet_no_standing(ref):
    a = gear.advise(ref, sheet(items=[]), level=32)
    assert all(s["standing"] is None for s in a["slots"])
    assert all(u["bis"] is None for u in a["upgrades"])


# --- the other hand of a weapon setup (blind review, 2026-11-21) ------------
#
# Made-up items on a level 33 character, spell power only: an off hand is
# scored with the main hand worn, never with the best main hand on the
# list; only against a worn two-hander does it need a partner, and then one
# wearable now.

SP = {"sp": 1}


def _cand(item_id: int, inv: int, sp: float, at: int) -> dict:
    return {"id": item_id, "at": at, "sources": [{"kind": "drop"}],
            "info": {"inv": inv, "class": 2 if inv != 23 else 4, "subclass": 4, "stats": {"sp": sp}}}


def _hands(main_inv: int, main_score: float, off_score: float | None = None) -> gear.Worn:
    ids, infos, scores = {16: 900}, {16: {"inv": main_inv}}, {16: main_score}
    if off_score is not None:
        ids[17], infos[17], scores[17] = 901, {"inv": 23}, off_score
    return gear.Worn(ids=ids, infos=infos, scores=scores)


OFF_NOW = _cand(1, 23, 15, 30)  # held in the off hand, wearable now
OFF_SOON = _cand(2, 23, 16, 35)  # the same, from level 35
MAIN_NOW = _cand(3, 21, 20, 30)  # a main hand better than the one worn
MAIN_SOON = _cand(4, 21, 40, 35)  # better again, from level 35
MAIN_55 = _cand(5, 21, 90, 55)  # the level 55 weapon a level 33 can't wear


def _setups(worn, *cands):
    return gear.WeaponSetups(list(cands), worn, SP, False, 33)


def test_an_off_hand_is_scored_with_the_main_hand_worn():
    s = _setups(_hands(21, 10.0), OFF_NOW, OFF_SOON, MAIN_NOW, MAIN_SOON, MAIN_55)
    for off in (OFF_NOW, OFF_SOON):
        r = s.compare(off)
        # Worn one-hander (10) + the off hand, against the one-hander alone:
        # the gain is the off hand's own, not MAIN_NOW's or MAIN_55's too.
        assert r["slot"] == 17 and r["delta"] == pytest.approx(off["info"]["stats"]["sp"])
        assert r["setup_score"] == pytest.approx(10 + off["info"]["stats"]["sp"])
        assert r["with_part"]["c"] is None and r["with_part"]["id"] == 900
    # And a main hand with the off hand worn, the same way round.
    s = _setups(_hands(21, 10.0, 12.0), OFF_NOW, MAIN_NOW)
    r = s.compare(MAIN_NOW)
    assert r["delta"] == pytest.approx(20 - 10) and r["with_part"]["id"] == 901
    assert s.compare(OFF_NOW)["delta"] == pytest.approx(15 - 12)


def test_against_a_two_hander_the_partner_is_wearable_now():
    s = _setups(_hands(17, 30.0), OFF_NOW, OFF_SOON, MAIN_NOW, MAIN_SOON, MAIN_55)
    for off in (OFF_NOW, OFF_SOON):
        r = s.compare(off)
        # A one-hander is needed: MAIN_NOW, never the level 35 or 55 one.
        assert r["with_part"]["c"] is MAIN_NOW
        assert r["setup_score"] == pytest.approx(20 + off["info"]["stats"]["sp"])
    # A main hand from level 35 against the staff: with an off hand wearable now.
    assert s.compare(MAIN_SOON)["with_part"]["c"] is OFF_NOW


def test_weapon_bis_counts_only_what_is_wearable_now():
    worn = _hands(17, 30.0)
    s = _setups(worn, OFF_NOW, OFF_SOON, MAIN_NOW, MAIN_SOON, MAIN_55)
    best = s.best_setup(33)
    assert best["total"] == pytest.approx(35) and best["main"]["c"] is MAIN_NOW and best["off"]["c"] is OFF_NOW
    st = gear.standings({}, worn, s, 33)
    # The staff (30) against MAIN_NOW + OFF_NOW (35), the only pair wearable now.
    assert st[16] == {"rank": 2, "pct": 86, "target": pytest.approx(35)}
    kinds = s.by_kind(33)
    assert kinds["pair"][0] == pytest.approx(35) and kinds["two"][0] == pytest.approx(30)


# --- the badge every item carries (owner, 2026-11-21) -------------------------
#
# bis_badge: the item's share of the slot's BiS (the doll's number) and the
# change from the worn one, in the same points. Made-up items, spell power
# only, scored by Polestar's compare.


def _item(inv: int, sp: float) -> dict:
    return {"inv": inv, "stats": {"sp": sp}}


def _badge(worn, st, item_id, info):
    return gear.bis_badge(st.get(gear.compare(info, item_id, worn, SP, False)["slot"]), gear.compare(
        info, item_id, worn, SP, False))


def test_badge_bis_item_is_100_and_the_delta_is_against_the_worn_one():
    worn = _worn({1: 10.0})
    st = _standings(worn, {1: _now((1, 20.0), (2, 15.0))})  # BiS 20: the worn 10 is 50%
    assert st[1]["pct"] == 50
    assert _badge(worn, st, 1, _item(1, 20)) == {"bis_pct": 100, "bis_pct_worn": 50, "delta_pct": 50}
    assert _badge(worn, st, 2, _item(1, 15)) == {"bis_pct": 75, "bis_pct_worn": 50, "delta_pct": 25}
    # Above BiS (one that opens in a few levels): still BiS, never over 100.
    assert _badge(worn, st, 3, _item(1, 30)) == {"bis_pct": 100, "bis_pct_worn": 50, "delta_pct": 50}
    # Weaker than the worn one: its own share, a negative change.
    assert _badge(worn, st, 4, _item(1, 6)) == {"bis_pct": 30, "bis_pct_worn": 50, "delta_pct": -20}
    # Never "100%" for something just short of BiS.
    assert _badge(worn, st, 5, _item(1, 19.95))["bis_pct"] == 99
    # The worn item itself: its standing, no change.
    assert _badge(worn, st, 101, _item(1, 10)) == {"bis_pct": 50, "bis_pct_worn": 50, "delta_pct": 0}


def test_badge_empty_slot_starts_from_zero():
    worn = _worn({1: 10.0})
    st = _standings(worn, {3: _now((1, 8.0))})
    # Nothing worn: no worn share ("empty → 100%"), the change from zero.
    assert _badge(worn, st, 1, _item(3, 8)) == {"bis_pct": 100, "bis_pct_worn": None, "delta_pct": 100}


@pytest.mark.parametrize("pair", [(11, 12), (13, 14)])
def test_badge_rings_and_trinkets_against_the_weaker_worn(pair):
    a, b = pair
    inv = 11 if a == 11 else 12
    # Worn 10 and 4; the best two on the list are 12 and 10 (the worn one):
    # the weaker slot measures against 12 (33%), as the gear code ranks it.
    worn = _worn({a: 10.0, b: 4.0})
    st = _standings(worn, {a: _now((1, 12.0), (2, 8.0)), b: _now((1, 12.0), (2, 8.0))})
    assert st[b]["pct"] == 33 and st[a]["rank"] == 1
    # A new piece replaces the weaker worn one (Gear.lua compare) and is
    # measured in that slot, from its 33%.
    assert gear.compare(_item(inv, 8), 2, worn, SP, False)["slot"] == b
    assert _badge(worn, st, 2, _item(inv, 8)) == {"bis_pct": 67, "bis_pct_worn": 33, "delta_pct": 34}
    assert _badge(worn, st, 1, _item(inv, 12)) == {"bis_pct": 100, "bis_pct_worn": 33, "delta_pct": 67}


def test_badge_none_rather_than_a_guess():
    worn = _worn({1: 10.0, 3: 5.0}, unknown={3})
    st = _standings(worn, {3: _now((1, 8.0))})
    # No candidate for the slot, a random suffix worn: no standing, no badge.
    assert gear.bis_badge(st.get(1), {"slot": 1, "new": 12.0}) == gear.NO_BADGE
    assert gear.bis_badge(st.get(3), {"slot": 3, "new": 8.0}) == gear.NO_BADGE
    # A one-hander alone isn't a weapon setup: nothing to set against the hands' BiS.
    hands = {"pct": 50, "target": 20.0}
    assert gear.bis_badge(hands, {"slot": 16, "new": 9.0}) == gear.NO_BADGE
    assert gear.bis_badge(hands, {"slot": 16, "new": 9.0, "setup_score": 15.0}) == {"bis_pct": 75, "bis_pct_worn": 50, "delta_pct": 25}
    assert gear.gain_words({**gear.NO_BADGE, "gain": 4.3, "slot_name": "Chest"}) == "+4.3 points"


def test_badge_on_every_advice_row_and_the_dungeon_loot(ref):
    """One number per item wherever it shows: the advice's rows (bis_badge in
    out()), its slots' best, and the same items in instance_loot (the
    Dungeons panel), read from the advice's bis_by_slot."""
    a = gear.advise(ref, sheet(), level=32)
    rows = [u for u in a["upgrades"] if u["bis"]]
    assert rows
    for u in rows:
        assert u["bis_pct"] == u["bis"]["to_pct"] and u["delta_pct"] == u["bis"]["to_pct"] - u["bis"]["from_pct"]
        assert u["bis_pct_worn"] == (None if u["fills_empty"] else u["bis"]["from_pct"])
        assert 0 < u["bis_pct"] <= 100
    # The random-suffix guesses and the rows with no BiS carry none.
    assert all(u["bis_pct"] is None for u in a["upgrades"] if u["vs_unknown"] or not u["bis"])
    # Unwearable items never show up (usability), so they carry nothing.
    loot = gear.instance_loot(ref, sheet(), level=32, bis=a["bis_by_slot"])
    by_id = {u["item_id"]: u for u in a["upgrades"] if u["slot"] not in (16, 17)}
    both = [r for rows in loot.values() for r in rows if r["item_id"] in by_id and not r["equipped"]]
    assert both
    for r in both:
        assert gear.badge_of(r) == gear.badge_of(by_id[r["item_id"]])
    # Without the advice's BiS, the loot has no badge (never a guess).
    bare = gear.instance_loot(ref, sheet(), level=32)
    assert all(r["bis_pct"] is None for rows in bare.values() for r in rows)
    # A worn item in the loot reads its own standing, no change.
    stand = {s["slot"]: s["standing"] for s in a["slots"]}
    for r in (r for rows in loot.values() for r in rows if r["equipped"] and r["slot"] not in (16, 17)):
        if stand.get(r["slot"]) and r["bis_pct"] is not None:
            assert r["delta_pct"] == 0


# --- the wording (owner, 2026-11-21): "70% → 91% on your chest" ---------------
#
# The slot's BiS share for what's worn, then with the item. "+21% on your
# chest" read as 21% more stats. gain_words is the server's one helper; the
# page's (web/app.js upText) is checked under node in test_bis_words_node.


def test_words_from_worn_to_this_item():
    worn = _worn({5: 14.0})
    st = _standings(worn, {5: _now((1, 20.0))})  # BiS 20: the worn 14 is 70%
    r = gear.compare(_item(5, 18.2), 7, worn, SP, False)
    u = {**gear.bis_badge(st[5], r), "slot_name": "Chest", "gain": 4.2}
    assert gear.gain_words(u) == "70% → 91% on your chest"
    assert gear.gain_words({**u, "bis_pct": 100, "delta_pct": 30}) == "70% → 100% on your chest"


def test_words_empty_slot():
    worn = _worn({1: 10.0})
    st = _standings(worn, {5: _now((1, 20.0))})
    r = gear.compare(_item(5, 18.2), 7, worn, SP, False)
    assert r["empty"]
    assert gear.gain_words({**gear.bis_badge(st[5], r), "slot_name": "Chest"}) == "empty → 91% on your chest"


def test_words_ring_pair_from_the_weaker_worn():
    worn = _worn({11: 10.0, 12: 4.0})
    st = _standings(worn, {11: _now((1, 12.0), (2, 8.0)), 12: _now((1, 12.0), (2, 8.0))})
    r = gear.compare(_item(11, 8), 2, worn, SP, False)
    assert r["slot"] == 12  # the weaker of the two
    assert gear.gain_words({**gear.bis_badge(st[12], r), "slot_name": "Finger"}) == "33% → 67% on your finger"
    # A free ring slot is the one it fills.
    one = _worn({11: 10.0})
    st1 = _standings(one, {11: _now((1, 12.0), (2, 8.0)), 12: _now((1, 12.0), (2, 8.0))})
    r1 = gear.compare(_item(11, 8), 2, one, SP, False)
    assert r1["empty"]
    assert gear.gain_words({**gear.bis_badge(st1[r1["slot"]], r1), "slot_name": "Finger"}).startswith("empty → ")


@pytest.mark.parametrize("pair,name", [((11, 12), "finger"), ((13, 14), "trinket")])
def test_words_pair_from_the_ring_it_replaces(pair, name):
    """rc.7 on a live character: the doll read 40% and 34% for the two
    rings and every ring line said "40% → ...", though the 40% ring was the
    stronger of the two in points. _pair measured the stronger worn ring
    against the best item left and the weaker against the next, so the
    weaker read the higher share, while compare() and the advice's picks put
    the best item in the weaker slot. The weaker worn one is the one a new
    piece replaces, and the doll must say it is the weaker."""
    a, b = pair
    inv = 11 if a == 11 else 12
    # Worn 36.8 and 34 points; the best two on the list are 100 and 80.
    worn = _worn({a: 36.8, b: 34.0})
    lst = _now((1, 100.0), (2, 80.0), (3, 70.0))
    st = _standings(worn, {a: lst, b: lst})
    assert (st[b]["pct"], st[a]["pct"]) == (34, 46)
    r = gear.compare(_item(inv, 70), 3, worn, SP, False)
    assert r["slot"] == b
    u = {**gear.bis_badge(st[r["slot"]], r), "slot_name": name.capitalize()}
    assert gear.gain_words(u) == f"34% → 70% on your {name}"
    # The best item reads BiS going into the slot it replaces.
    assert _badge(worn, st, 1, _item(inv, 100))["bis_pct"] == 100


def test_pair_standing_follows_the_points():
    """Whatever the list, the weaker worn ring never reads a higher share of
    BiS than the stronger: the doll and every item line agree on which one a
    new ring replaces."""
    import itertools

    for sa, sb, x, y in itertools.product((5.0, 9.0, 12.0), (4.0, 8.5, 11.0), (10.0, 13.0, 20.0), (6.0, 9.5, 12.5)):
        if sa == sb:
            continue
        worn = _worn({11: sa, 12: sb})
        lst = _now((1, x), (2, y))
        st = _standings(worn, {11: lst, 12: lst})
        weak, strong = (11, 12) if sa < sb else (12, 11)
        if st[weak] and st[strong]:
            assert st[weak]["pct"] <= st[strong]["pct"], (sa, sb, x, y, st[11], st[12])


def test_words_weapon_setup():
    hands = {"pct": 50, "target": 20.0}
    u = {**gear.bis_badge(hands, {"slot": 16, "new": 9.0, "setup_score": 15.0, "empty": False}),
         "slot_name": "Main hand"}
    assert gear.gain_words(u) == "50% → 75% on your weapons"
    off = {**gear.bis_badge({"pct": 0, "target": 20.0}, {"slot": 17, "new": 9.0, "setup_score": 18.2, "empty": True}),
           "slot_name": "Off hand"}
    assert gear.gain_words(off) == "empty → 91% on your weapons"


def test_words_without_a_badge_are_the_points():
    assert gear.gain_words({**gear.NO_BADGE, "gain": 4.3, "slot_name": "Chest"}) == "+4.3 points"
