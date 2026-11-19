"""Gear advice (wowstats.gear) on the bundled data/gear-reference.json.

The numbers below were worked out by hand from Polestar's formulas and match
what Polestar's own Gear.lua answers for the same character (run with
tools/gear_reference/parity.lua; tests/test_gear_parity.py does that for
every item when a Polestar checkout is at hand). The character is a level
32 Gnome Priest, Shadow, wearing a two-handed staff and one trinket."""

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
    w, _ = gear.weights_for(ref, "PRIEST", "shadow", 32)
    racial = ref.gear["RACIALS"]["7"]
    worn = gear.worn_from(ref, sheet()["items"], w, racial)
    return w, racial, worn


def test_the_file_is_polestars(ref):
    d = ref.data
    assert d["polestar"]["license"] == "GPL-3.0-only" and d["polestar"]["tag"].startswith("v")
    assert d["leveling"]["source"] == "ForeverLevelingBiS" and d["leveling"]["version"] == "v0.12.0-beta"
    assert d["leveling"]["licence"] == "MIT"
    assert len(d["items"]) > 9000 and len(d["sources"]) > 3000


def test_weights_at_32(ref):
    """Shadow's level band to 32 (Polestar leveling), its formula weights:
    health = Stamina / 10; mana = (Intellect - spell crit / Intellect per 1%
    spell crit at 32) / 15; a melee DPS tiebreaker for a caster."""
    w, band = gear.weights_for(ref, "PRIEST", "shadow", 32)
    assert band["source"] == "polestar"
    assert w["sp"] == 1 and w["spi"] == 0.9 and w["dps"] == 0.01 and w["haste"] == 4
    assert w["health"] == pytest.approx(0.065)
    per_crit = 59.2 * (0.2 + 0.8 * 31 / 59)
    assert w["mana"] == pytest.approx((0.85 - 3 / per_crit) / 15)
    assert w["mana"] == pytest.approx(0.051220646876384585)  # Polestar's Gear.lua, same level


def test_hand_checked_scores(ref, priest_gear):
    w, racial, worn = priest_gear
    # Worn staff 1317: str 1, spi 9, sp 20, heal 20, 13.6 DPS -> 8.1 + 20 + 1 + 0.136.
    assert worn.score(16) == pytest.approx(29.236)
    # Staff 1155: spi 11, sp 32, heal 32, (39 + 60) / 2 / 2.8 = 17.7 DPS -> 9.9 + 32 + 1.6 + 0.177.
    info = ref.item(1155)
    r = gear.compare(info, 1155, worn, w, racial, False)
    assert (r["slot"], r["score"]) == (16, pytest.approx(43.677))
    # A two-hander replaces main hand + off hand (empty here).
    assert r["delta"] == pytest.approx(14.441) and r["pct"] == pytest.approx(49.394582022164471)
    # 2042, a worse staff for Shadow: a loss.
    r = gear.compare(ref.item(2042), 2042, worn, w, racial, False)
    assert r["delta"] == pytest.approx(-0.8215)
    # Trinket 744 (spi 8): the empty trinket slot goes first, no percent.
    r = gear.compare(ref.item(744), 744, worn, w, racial, False)
    assert (r["slot"], r["delta"], r["pct"], r["empty"]) == (14, pytest.approx(7.2), None, True)
    # Robe 2034 for the chest: +1.99, 16.3%.
    r = gear.compare(ref.item(2034), 2034, worn, w, racial, False)
    assert (r["slot"], r["delta"], r["pct"]) == (5, pytest.approx(1.99), pytest.approx(16.344969199178646))


def test_racial_stat_multiplier(ref):
    """A Gnome's Intellect counts 5% more (SpecData.lua RACIALS[7])."""
    info = {"inv": 1, "class": 4, "subclass": 1, "stats": {"int": 10}}
    w = {"int": 1}
    assert gear.score_with(info, w, False, None) == 10
    assert gear.score_with(info, w, False, ref.gear["RACIALS"]["7"]) == pytest.approx(10.5)


def test_school_bonus_capped():
    """Two schools of +34 are worth one +34 at the spell power weight (#1324)."""
    info = {"inv": 1, "class": 4, "subclass": 1, "stats": {"spFire": 34, "spShadow": 34}}
    assert gear.score_with(info, {"sp": 1, "spFire": 0.6, "spShadow": 0.8}, False) == pytest.approx(34)


def test_usability(ref):
    priest = lambda iid, lv=32: gear.usability(ref, ref.item(iid), "PRIEST", lv)  # noqa: E731
    assert priest(1155)[0]
    mail = next(i for i, it in ref.data["items"].items() if it["class"] == 4 and it["subclass"] == 3
                and it["inv"] == 5)
    assert gear.usability(ref, ref.item(mail), "PRIEST", 32)[:2] == (False, "armor")
    # A Shaman learns mail at 40: usable, worn from 40.
    ok, _, at = gear.usability(ref, ref.item(mail), "SHAMAN", 32)
    assert ok and at == max(40, ref.item(mail)["req"])


def test_adjust_edits():
    w, n = gear.adjust_apply({"sta": 0.5, "int": 1}, {"sta": {"x": 1.5}, "spellHit": {"v": 2}})
    assert (w["sta"], w["int"], w["spellHit"], n) == (0.75, 1, 2, 2)
    assert gear.adjust_apply({"sta": 0.5}, None) == ({"sta": 0.5}, 0)


def test_spec_order(ref):
    s = sheet(spec="Shadow")
    assert gear.resolve_spec(ref, "PRIEST", s) == ("shadow", "picked")
    assert gear.resolve_spec(ref, "PRIEST", {**s, "polestar_spec": "holy"}) == ("holy", "setting")
    assert gear.resolve_spec(ref, "PRIEST", {**s, "polestar_spec": "auto"}) == ("shadow", "picked")
    assert gear.resolve_spec(ref, "PRIEST", {**s, "polestar_spec": "holy"}, "discipline") == ("discipline", "page")
    assert gear.resolve_spec(ref, "PRIEST", {}) == ("shadow", "default")
    assert gear.resolve_spec(ref, "DRUID", {"spec": "Bear"}) == ("feral", "picked")
    assert gear.resolve_spec(ref, "HUNTER", {"spec": "Beast Mastery"}) == ("beastmastery", "picked")



def test_spec_from_talents(ref):
    """Polestar's mostPoints: the tree with the most points, by name, else
    by position. After Polestar's own setting, before an addon's saved pick."""
    t = [{"tree": "Discipline", "points": 5}, {"tree": "Holy", "points": 14}, {"tree": "Shadow", "points": 3}]
    s = sheet(spec="Shadow", talents=t)
    assert gear.resolve_spec(ref, "PRIEST", s) == ("holy", "talents")
    assert gear.resolve_spec(ref, "PRIEST", {**s, "polestar_spec": "shadow"}) == ("shadow", "setting")
    assert gear.resolve_spec(ref, "PRIEST", {**s, "polestar_spec": "auto"}) == ("holy", "talents")
    assert gear.resolve_spec(ref, "PRIEST", s, "discipline") == ("discipline", "page")
    # A tie goes to the first tree; no names: position; no points: next step.
    assert gear.most_points(ref, "PRIEST", [{"tree": "Discipline", "points": 7}, {"tree": "Shadow", "points": 7}]) \
        == "discipline"
    assert gear.most_points(ref, "MAGE", [{"tree": None, "points": 0}, {"tree": None, "points": 11}]) == "fire"
    assert gear.resolve_spec(ref, "PRIEST", sheet(spec="Shadow", talents=[{"tree": "Holy", "points": 0}])) \
        == ("shadow", "picked")
    assert gear.sheet({"talents": '[{"tree": "Holy", "points": 14}]', "class": "PRIEST"})["talents"] \
        == [{"tree": "Holy", "points": 14}]


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
           "polestar_spec": "auto"}
    s = gear.sheet(row, {"race": "Gnome", "faction": "Alliance"})
    assert s["items"] == [{"slot": 16, "item_id": 1317}] and s["race"] == "Gnome"
    assert s["gear_adjust"] == {"shadow": {"sta": {"x": 1.5}}} and s["min_upgrade"] == 3


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
    # Ordered by points, not by percent over a weak item.
    now = [u["gain"] for u in a["upgrades"] if u["now"] and not u["vs_unknown"]]
    assert now == sorted(now, reverse=True)


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
    assert "you picked Shadow in ForeverLevelingBiS" in a["spec"]["why"] and "/simx" in a["spec"]["why"]


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
    w, _ = gear.weights_for(ref, "PRIEST", "shadow", 32)
    assert gear.likely_suffix(ref, ref.item(ELDERS_CLOAK), w)
    assert cur["score"] > 5  # 0.1 on armor alone
    assert not any(u["item_id"] == WOOLEN_CAPE for u in a["upgrades_all"])
    assert all(u["gain_pct"] is None for u in a["upgrades_all"] if u["slot"] == 15)

    # On its base stats alone (before), the cape was an upgrade.
    before = gear.advise(ref, sheet(), level=32, every=True)
    assert any(u["item_id"] == WOOLEN_CAPE for u in before["upgrades_all"])


def test_worn_random_suffix_scored_on_battlenet_stats(ref):
    """Battle.net's equipment answer for the same item: its real stats,
    a plain comparison (gain_pct back); for another item (changed since the
    daily refresh) it's ignored."""
    real = gear.worn_stats({"back": {"item_id": ELDERS_CLOAK, "stats": {"INTELLECT": 1.0, "STAMINA": 1.0,
                                                                         "ARMOR": 21.0}}})
    assert real == {15: {"item_id": ELDERS_CLOAK, "stats": {"int": 1.0, "sta": 1.0}}}
    w, _ = gear.weights_for(ref, "PRIEST", "shadow", 32)
    racial = ref.gear["RACIALS"]["7"]
    worn = gear.worn_from(ref, suffix_sheet()["items"], w, racial, real)
    assert 15 not in worn.unknown
    assert worn.score(15) == pytest.approx(gear.score_with(
        {**ref.item(ELDERS_CLOAK), "stats": {"armor": 21, "int": 1.0, "sta": 1.0}}, w, False, racial))
    a = gear.advise(ref, suffix_sheet(worn_stats=real), level=32, every=True)
    cur = next(s for s in a["slots"] if s["slot"] == 15)["current"]
    assert cur["random_suffix"] is False and cur["stats"] == {"armor": 21, "int": 1.0, "sta": 1.0}
    cape = next(u for u in a["upgrades_all"] if u["item_id"] == WOOLEN_CAPE)  # a weak roll: the cape wins
    assert cape["gain_pct"] is not None and cape["vs_unknown"] is False

    other = gear.worn_from(ref, suffix_sheet()["items"], w, racial, {15: {"item_id": 1, "stats": {"int": 9}}})
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
    w, _ = gear.weights_for(ref, "PRIEST", "shadow", 32)
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
