"""The gear at past levels, reconstructed from the Recorder's records
(wowstats.history): what counts as got, the replay, the anchor on the first
real sheet, and what each tier shows of it. Against the bundled gear data
(the game's items, Polestar's sources), as a Gnome Priest."""

from datetime import UTC, datetime, timedelta

import pytest

from wowstats import gear as gm
from wowstats import history as hi
from wowstats import redact
from wowstats.stores import iso

T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
SHEET = {"class": "PRIEST", "race": "Gnome", "faction": "Alliance", "spec": "Shadow"}


def at(h: float) -> str:
    return iso(T0 + timedelta(hours=h))


@pytest.fixture(scope="module")
def ref():
    r = gm.reference()
    if not r.available:
        pytest.skip("no bundled gear data")
    return r


def dings(*pairs):
    return [(T0 + timedelta(hours=h), lv) for h, lv in pairs]


def test_what_counts_as_got(ref):
    """A turn-in's picked reward and the quest's fixed rewards, every looted
    item, a recipe's product; a craft whose spell isn't the item's recipe is
    an enchant on an item already owned; vendor offers never count."""
    acqs = hi.acquisitions(
        ref,
        turnins=[{"_time": at(1), "quest_id": "386", "quest_name": "What Comes Around...", "item": "1317",
                  "zone": "Redridge Mountains"}],
        loots=[{"_time": at(2), "items": "2592:3;271096:1", "zone": "Hall of Thanes", "npc_name": "Faldrim"}],
        crafts=[{"_time": at(3), "spell_id": "3843", "item_id": "4310"},
                {"_time": at(4), "spell_id": "13501", "item_id": "273808"}])
    got = [(a.item_id, a.kind) for a in acqs]
    assert got == [(1317, "quest"), (2592, "loot"), (271096, "loot"), (4310, "craft"), (273808, "enchant")]
    assert acqs[0].source() == {"kind": "quest", "quest": "What Comes Around...", "zone": "Redridge Mountains",
                                "t": at(1)}
    assert acqs[2].npc == "Faldrim"


def run(ref, acqs_rows, ds, first=None):
    turnins, loots, crafts = acqs_rows
    return hi.build(ref, SHEET, turnins, loots, crafts, ds, first)


def test_replay_wears_what_scores_better_once_it_can(ref):
    rows = ([], [{"_time": at(1), "items": "2584:1"},       # Woolen Cape, back, req 11
                 {"_time": at(2), "items": "5542:1"},       # Pearl-clasped Cloak, better
                 {"_time": at(3), "items": "273808:1"}],    # Bridgebreaker Bindings, req 24
            [])
    rp, check, _ = run(ref, rows, dings((0, 22), (10, 24)))
    assert check is None
    assert rp.start == T0 + timedelta(hours=1)
    assert rp.at(T0 + timedelta(hours=1, minutes=30))[15].item_id == 2584
    assert rp.at(T0 + timedelta(hours=2, minutes=30))[15].item_id == 5542
    # Got at 22, wearable from 24: worn from the ding, not before.
    assert 9 not in rp.at(T0 + timedelta(hours=9))
    assert rp.at(T0 + timedelta(hours=10))[9].item_id == 273808
    # Nothing is known before the first record.
    assert rp.at(T0) == {}


def test_an_enchant_is_worn_gear_owned_since_before(ref):
    """An enchant on an item with no other record: owned before the records
    began, worn then and since the start."""
    rows = ([], [{"_time": at(1), "items": "2592:2"}], [{"_time": at(5), "spell_id": "13501", "item_id": "271096"}])
    rp, _, _ = run(ref, rows, dings((0, 22)))
    s = rp.at(T0 + timedelta(hours=2))[9]
    assert s.item_id == 271096 and s.source["kind"] == "enchant"


def test_two_hander_against_main_hand(ref):
    # Staff of Westfall (5 Intellect, 6 Spirit) over Fang of Magmatus (4 Intellect).
    rows = ([{"_time": at(3), "quest_id": "166", "item": "2042"}], [{"_time": at(1), "items": "271095:1"}], [])
    rp, _, _ = run(ref, rows, dings((0, 25)))
    assert rp.at(T0 + timedelta(hours=2))[16].item_id == 271095
    now = rp.at(T0 + timedelta(hours=4))
    assert now[16].item_id == 2042 and 17 not in now


def test_anchor_trusts_the_sheet_and_walks_back(ref):
    """The first sheet: Nimbus Boots in the replay, Red Woolen Boots on the
    sheet (made before the records): the sheet wins back to the start. The
    sheet's ring came from a quest in the records: worn from that turn-in.
    Its trinket the records never saw: "no_record", carried back."""
    rows = ([{"_time": at(2), "quest_id": "1199", "item": "6998"},
             {"_time": at(4), "quest_id": "98216", "item": "281304"}],
            [{"_time": at(1), "items": "2584:1"}], [])
    first = {"_time": at(8), "items": [{"slot": 8, "item_id": 4313}, {"slot": 11, "item_id": 281304},
                                       {"slot": 15, "item_id": 2584}, {"slot": 13, "item_id": 273298}]}
    rp, check, _ = run(ref, rows, dings((0, 26)), first)
    assert check["slots"] == {"Feet": "differs", "Finger 1": "agree", "Trinket 1": "no_record", "Back": "agree"}
    assert (check["agree"], check["differs"], check["no_record"], check["total"]) == (2, 1, 1, 4)
    early = rp.at(T0 + timedelta(hours=3))
    assert early[8].item_id == 4313 and early[8].source["kind"] == "sheet"
    assert 11 not in early and 12 not in early  # the ring: not got yet
    assert rp.at(T0 + timedelta(hours=5))[11].item_id == 281304
    assert early[13].item_id == 273298 and early[13].source == {"kind": "sheet", "t": at(8)}


def test_recorded_reward_can_be_worn_before_its_estimated_advice_level(ref):
    # Death from Below opens at 35. Its ring has item level 46 and no equip gate.
    rows = ([{"_time": at(2), "quest_id": "667", "item": "4549"}], [], [])
    first = {"_time": at(5), "items": [{"slot": 11, "item_id": 4549}]}
    rp, _, _ = run(ref, rows, dings((0, 35)), first)
    assert 11 not in rp.at(T0 + timedelta(hours=1))
    assert rp.at(T0 + timedelta(hours=2))[11].item_id == 4549


def test_sheet_and_enchant_start_only_when_the_item_can_be_worn(ref):
    # Picked up at 13, usable at 24, confirmed by a later sheet or enchant.
    braces = 273808
    rows = ([], [{"_time": at(1), "items": "271096:1"}, {"_time": at(2), "items": f"{braces}:1"}],
            [{"_time": at(3), "spell_id": "13501", "item_id": str(braces)}])
    start, ding = dings((0, 13), (8, 24))
    first = {"_time": at(10), "items": [{"slot": 9, "item_id": braces}, {"slot": 5, "item_id": 6242}]}
    rp, _, _ = run(ref, rows, [start, ding], first)
    assert rp.at(ding[0] - timedelta.resolution)[9].item_id == 271096
    assert rp.at(ding[0])[9].item_id == braces
    # The sheet is the only evidence for the robe (requires 9); the enchant
    # is the only evidence for the braces. Neither starts in the level-2 history.
    early = dings((0, 2), (4, 9), (8, 24))
    rp, _, _ = run(ref, ([], [{"_time": at(1), "items": "2592:1"}], rows[2]), early, first)
    assert rp.at(early[1][0] - timedelta.resolution) == {}
    assert set(rp.at(early[1][0])) == {5}
    assert set(rp.at(early[2][0])) == {5, 9}


def test_anchored_two_hander_replaces_the_old_set_at_its_first_wearable_level(ref):
    # Real loot at 13 for both hands. Only the level-19 sheet shows the Staff
    # of Westfall. Its inventory type is two-handed, and its level floor is 19.
    rows = ([], [{"_time": at(1), "items": "271095:1;15945:1"}], [])
    early, ding = dings((0, 13), (5, 19))
    first = {"_time": at(8), "items": [{"slot": 16, "item_id": 2042}]}
    rp, _, _ = run(ref, rows, [early, ding], first)
    assert {s: sp.item_id for s, sp in rp.at(ding[0] - timedelta.resolution).items()} == {16: 271095, 17: 15945}
    assert {s: sp.item_id for s, sp in rp.at(ding[0]).items()} == {16: 2042}


def test_replay_carries_observed_outfits_across_days_without_a_sheet(ref):
    # Three days offline. The cape is the only new loot, but the caster still
    # has the robe, staff and boots in the last sheet. The later outfit is
    # the first evidence that the boots were removed and the weapon changed.
    starter = {"_time": at(0), "items": [
        {"slot": 5, "item_id": 6242}, {"slot": 8, "item_id": 4313}, {"slot": 16, "item_id": 2042}]}
    later = {"_time": at(80), "items": [
        {"slot": 5, "item_id": 6242}, {"slot": 16, "item_id": 271095}, {"slot": 15, "item_id": 2584}]}
    loot = [{"_time": at(72), "items": "2584:1"}, {"_time": at(160), "items": "271096:1"}]
    rp, _, _ = hi.build(ref, SHEET, [], loot, [], dings((0, 32)), [starter, later])
    between = rp.at(T0 + timedelta(hours=75))
    assert {s: v.item_id for s, v in between.items()} == {5: 6242, 8: 4313, 15: 2584, 16: 2042}
    assert between[5].source == {"kind": "sheet", "t": at(0)}
    assert between[15].source["kind"] == "loot"
    after = rp.at(T0 + timedelta(hours=162))
    assert {s: v.item_id for s, v in after.items()} == {5: 6242, 9: 271096, 15: 2584, 16: 271095}
    assert after[16].source == {"kind": "sheet", "t": at(80)}


def test_one_handed_loot_cannot_fill_a_two_handers_off_hand(ref):
    # A warrior can dual wield at this level. A one-handed quest sword still
    # has to beat the equipped two-handed axe in the main hand.
    outfit = {"_time": at(0), "items": [{"slot": 16, "item_id": 12784}]}  # Arcanite Reaper
    sheet = {"class": "WARRIOR", "race": "Orc", "faction": "Horde", "spec": "Arms"}
    rows = [{"_time": at(2), "quest_id": "6024", "item": "15814"}]
    rp, _, _ = hi.build(ref, sheet, rows, [], [], dings((0, 60)), [outfit])
    worn = rp.at(T0 + timedelta(hours=3))
    assert worn[16].item_id == 12784 and 17 not in worn


@pytest.mark.parametrize(("cls", "spec", "expected"), [
    ("PRIEST", "Shadow", {16: 271095}), ("ROGUE", "Combat", {16: 271095, 17: 5093})])
def test_only_a_dual_wielder_can_add_a_second_one_hand_weapon(ref, cls, spec, expected):
    outfit = {"_time": at(0), "items": [{"slot": 16, "item_id": 271095}]}
    sheet = {"class": cls, "race": "Human", "faction": "Alliance", "spec": spec}
    rp, _, _ = hi.build(ref, sheet, [], [{"_time": at(1), "items": "5093:1"}], [], dings((0, 32)), [outfit])
    assert {s: sp.item_id for s, sp in rp.at(T0 + timedelta(hours=2)).items()} == expected


def test_completed_level_stops_before_the_next_ding_for_exact_and_reconstructed_gear(ref):
    from wowstats import insights

    before, ding = dings((0, 22), (4, 24))
    old = {"_time": at(1), "items": [{"slot": 9, "item_id": 271096}]}
    new = {"_time": at(4), "items": [{"slot": 9, "item_id": 273808}]}
    reads = []

    def query(q):
        reads.append(q)
        return []

    snap = insights.snapshot(22, {}, (before[0], ding[0]), [old, new], query, "", None, {}, [])
    assert snap["gear"] == old
    assert all(("13:00:00.000000Z]" if insights.SESSION_BUCKETS in q else "13:00:00.000000Z)") in q for q in reads)
    rows = ([], [{"_time": at(1), "items": "271096:1"}, {"_time": at(2), "items": "273808:1"}], [])
    rp, _, _ = run(ref, rows, [before, ding], new)
    assert hi.items_at(ref, rp, insights.snapshot_as_of(ding[0]))[0]["item_id"] == 271096
    assert hi.items_at(ref, rp, ding[0])[0]["item_id"] == 273808


def test_items_are_likely_never_exact(ref):
    rows = ([], [{"_time": at(1), "items": "2584:1", "zone": "Westfall", "npc_name": "Some Mob"}], [])
    rp, _, names = run(ref, rows, dings((0, 20)))
    items = hi.items_at(ref, rp, T0 + timedelta(hours=2), names)
    assert items == [{"slot": 15, "slot_name": "Back", "item_id": 2584, "name": "Woolen Cape", "quality": 2,
                      "item_level": 16, "req_level": 11, "certainty": "likely",
                      "source": {"kind": "loot", "zone": "Westfall", "npc": "Some Mob", "t": at(1)}}]


def test_no_records_no_reconstruction(ref):
    assert run(ref, ([], [], []), dings((0, 20))) is None
    assert hi.build(ref, {**SHEET, "class": None}, [], [{"_time": at(1), "items": "2584:1"}], [], [], None) is None


def snap(item_source):
    return {"char": "Hero-Test", "level": 23, "available": True, "gear_reconstructed": True,
            "gear_records_from": at(1), "gear_check": {"agree": 1},
            "gear": {"item_level": None, "items": [{"slot": 15, "slot_name": "Back", "item_id": 2584,
                                                    "name": "Woolen Cape", "quality": 2, "item_level": 16,
                                                    "req_level": 11, "certainty": "likely",
                                                    "source": item_source}]}}


def test_tiers_of_a_reconstructed_snapshot():
    """Public: likely and how (the kind); zogzog: also the quest and the zone;
    the creature only in the full view."""
    src = {"kind": "loot", "zone": "Westfall", "npc": "Some Mob", "quest": None, "t": at(1)}
    pub = redact.public("snapshot", snap(src))
    assert pub["gear_reconstructed"] is True and "gear_records_from" not in pub and "gear_check" not in pub
    assert pub["gear"]["items"][0]["certainty"] == "likely"
    assert pub["gear"]["items"][0]["source"] == {"kind": "loot"}
    zz = redact.zogzog("snapshot", snap(src))
    assert zz["gear_records_from"] == at(1) and "gear_check" not in zz
    assert zz["gear"]["items"][0]["source"] == {"kind": "loot", "zone": "Westfall", "quest": None, "t": at(1)}


def test_views_read_the_records_once_a_minute(ref, monkeypatch):
    """Views.reconstruction: the four Recorder queries, the replay anchored
    on the first sheet of gear_history, cached for every level's snapshot."""
    from wowstats import items as itemsmod
    from wowstats.stores import Store
    from wowstats.views import Views

    calls = []

    class Recs(Store):
        def __init__(self):
            super().__init__("http://vl.invalid")

        def vl(self, query, keys=(), ops=None, limit=0):
            calls.append(query)
            if "kind:=turnin" in query:
                return [{"_time": at(2), "quest_id": "1199", "item": "6998", "source_id": "a"}]
            if "kind:=loot" in query:
                return [{"_time": at(1), "items": "2584:1", "zone": "Westfall", "npc_name": "Mob", "source_id": "b",
                         "item_names": '{"2584": "Woolen Cape"}'}]
            if "kind:=level" in query:
                return [{"_time": at(0), "level": "22", "source_id": "c"}]
            return []

    monkeypatch.setattr(itemsmod, "shared", lambda: type("N", (), {"get": staticmethod(lambda i: (None, None))})())
    v = Views(Recs())
    data = {"header": {"class": "Priest", "race": "Gnome", "faction": "Alliance"}, "levels": []}
    gh = [{"_time": at(5), "items": [{"slot": 8, "item_id": 4313}], "spec": "Shadow"}]
    rp, check, names = v.reconstruction("Hero-Test", data, gh)
    assert len(calls) == 4 and all("wow-recorder" in q for q in calls)
    assert check["slots"] == {"Feet": "differs"}
    assert rp.at(T0 + timedelta(hours=3))[8].item_id == 4313  # the sheet wins
    assert rp.at(T0 + timedelta(hours=3))[15].item_id == 2584 and names["2584"] == "Woolen Cape"
    v.reconstruction("Hero-Test", data, gh)
    assert len(calls) == 4, "cached"
