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
    # Prospector's Signet has no required level and item level 31: wearable from 26.
    rp, check, _ = run(ref, rows, dings((0, 26)), first)
    assert check["slots"] == {"Feet": "differs", "Finger 1": "agree", "Trinket 1": "no_record", "Back": "agree"}
    assert (check["agree"], check["differs"], check["no_record"], check["total"]) == (2, 1, 1, 4)
    early = rp.at(T0 + timedelta(hours=3))
    assert early[8].item_id == 4313 and early[8].source["kind"] == "sheet"
    assert 11 not in early and 12 not in early  # the ring: not got yet
    assert rp.at(T0 + timedelta(hours=5))[11].item_id == 281304
    assert early[13].item_id == 273298 and early[13].source == {"kind": "sheet", "t": at(8)}


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
