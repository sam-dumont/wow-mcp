"""The auction house as an upgrade source (wowstats.ahsource): only what the
player's own newest Auctionator scan saw listed, today or yesterday, with
its price against his gold. Synthetic scans: no real prices here."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from tests.mcp import fakes
from wowstats import ahsource, gear
from wowstats.mcp import tools
from wowstats.mcp.store import Store

NOW = time.time()
SPELLBINDER_VEST = 2969  # chest, req 12: no source in Polestar's data (a world-drop green)
ROBE_OF_EVOCATION = 14150  # chest, Ragefire Chasm, Jergosh 36.7%
CRYSTALLINE_CUFFS = 14148  # wrist, Ragefire Chasm
ADEPTS_GLOVES = 4768  # in the fixture's bags (fakes.BAGS)


def midnight(days_ago: int) -> str:
    """A scan day's _time as the shipper writes it: local midnight."""
    d = datetime.fromtimestamp(NOW).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return d.astimezone(UTC).isoformat().replace("+00:00", "Z")


def day(days_ago: int) -> str:
    return (datetime.fromtimestamp(NOW) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def scans(*days: tuple[int, int], realm="Fixture", faction=None) -> list[dict]:
    """Filler price_rows rows (wowstats.views): (days ago, items priced that day), keys 900000+."""
    return [price(900000 + i, a, 100, realm=realm, faction=faction) for a, n in days for i in range(n)]


def price(item, days_ago, copper, realm="Fixture", faction=None, listed=3) -> dict:
    """A price_rows row (wowstats.views)."""
    return {"realm": realm, "faction": faction, "item_key": str(item), "scan_day": day(days_ago),
            "lo": str(copper), "av": str(listed), "t": midnight(days_ago)}


def test_on_sale_is_the_newest_full_scan_today_or_yesterday():
    items = [*scans((0, 300), (1, 400)),
             price(1, 0, 500), price(2, 1, 700),  # 2: yesterday, not in today's full scan
             price(3, 0, 900), price(3, 1, 100),  # 3: today's price, not the older lower one
             {**price(4, 0, 50), "item_key": "gr:4:of the Owl"}]  # one suffix: not the item
    got = ahsource.on_sale(items, "Alliance", NOW)
    assert got["scan"]["day"] == day(0) and got["scan"]["full_day"] == day(0)
    assert set(got["items"]) - set(range(900000, 901000)) == {1, 3}
    assert got["items"][3] == {"price_copper": 900, "seen_day": day(0), "seen_days_ago": 0, "listed": 3}


def test_a_search_after_the_full_scan_also_says_listed():
    """Yesterday's full scan, today's search: both saw what they priced."""
    items = [*scans((1, 300)), price(1, 1, 500), price(2, 0, 300), price(3, 3, 10)]
    got = ahsource.on_sale(items, None, NOW)
    assert got["scan"]["full_day"] == day(1) and got["scan"]["day"] == day(0)
    assert set(got["items"]) - set(range(900000, 901000)) == {1, 2}
    assert got["items"][1]["seen_days_ago"] == 1


def test_stale_prices_are_never_shown():
    """Auctions last 24 hours at most: a scan two days old says nothing about now."""
    got = ahsource.on_sale([*scans((2, 300)), price(1, 2, 500)], None, NOW)
    assert got["items"] == {}
    s = ahsource.summary({**got, "money_copper": 10000}, [])
    assert "your last scan is 2 days old" in s["notice"]


def test_no_scan_at_all_is_one_line_to_scan():
    got = ahsource.on_sale([], "Horde", NOW)
    assert got == {"scan": None, "items": {}}
    assert ahsource.summary(got, [])["notice"] == ahsource.notice_none()


def test_the_other_factions_house_is_not_his():
    items = [price(1, 0, 100, faction="Horde"), price(2, 1, 200, faction="Alliance")]
    assert set(ahsource.on_sale(items, "Alliance", NOW)["items"]) == {2}


def test_gold_per_level_and_the_mount():
    snaps = [{"_time": midnight(3), "level": 30, "money_copper": 22597},
             {"_time": midnight(1), "level": 32, "money_copper": 34042}]
    assert ahsource.gold_per_level(snaps, NOW) == 5722
    # A loss, or no level gained: nothing is called cheap.
    assert ahsource.gold_per_level([{**snaps[0], "level": 32}, snaps[1]], NOW) is None
    m = ahsource.mount_left(34042, [])
    assert m["name"] == "riding.apprentice" and m["left_copper"] == m["need_copper"] - 34042
    assert ahsource.mount_left(0, [{"skill_id": "762", "rank": "75"}])["name"] == "riding.journeyman"


def test_price_text():
    assert ahsource.price_text(124037) == "12g 40s"
    assert ahsource.price_text(31000) == "3g 10s"
    assert ahsource.price_text(4521) == "45s 21c"
    assert ahsource.price_text(7) == "7c"


# --- in the gear advice -------------------------------------------------------


def sheet() -> dict:
    return {"items": [{"slot": 5, "item_id": 6098, "name": "Neophyte's Robe"},
                      {"slot": 16, "item_id": 36, "name": "Worn Mace"}],
            "class": "PRIEST", "race": "Troll", "faction": "Horde", "spec": "Shadow"}


def market(items: dict, money=123456, per_level=5000) -> dict:
    return {"scan": {"day": day(0), "days_ago": 0}, "items": items, "money_copper": money,
            "gold_per_level_copper": per_level, "mount": {"left_copper": 1000000 - money}}


def sale(copper, days_ago=0):
    return {"price_copper": copper, "seen_day": day(days_ago), "seen_days_ago": days_ago, "listed": 2}


def ah_of(u):
    return next((s for s in u["sources"] if s["kind"] == "ah"), None)


@pytest.fixture(scope="module")
def ref():
    r = gear.reference()
    assert r.available, r.why
    return r


def test_a_cheap_boe_upgrade_is_on_the_list(ref):
    a = gear.advise(ref, sheet(), level=14, market=market({SPELLBINDER_VEST: sale(3100)}), every=True)
    u = next(u for u in a["upgrades_all"] if u["item_id"] == SPELLBINDER_VEST)
    s = ah_of(u)
    # Buying it first: the auction house makes it easy (ForeverDB's two
    # world-drop sightings, no rate, come after).
    assert [x["kind"] for x in u["sources"]] == ["ah", "drop", "drop"] and u["gates"] == []
    assert u["effort"] == "easy"
    assert s["price_copper"] == 3100 and s["seen_days_ago"] == 0 and s["listed"] == 2
    assert s["gain_per_gold"] == pytest.approx(u["gain"] / 0.31, abs=0.01)
    assert s["gold_share_pct"] == pytest.approx(2.5) and s["affordable"] and s["cheap"]
    assert a["ah"]["upgrades_on_sale"] >= 1 and "notice" not in a["ah"]


def test_cheap_is_at_most_a_level_of_gold(ref):
    a = gear.advise(ref, sheet(), level=14, market=market({SPELLBINDER_VEST: sale(5001)}), every=True)
    s = ah_of(next(u for u in a["upgrades_all"] if u["item_id"] == SPELLBINDER_VEST))
    assert not s["cheap"] and s["affordable"]
    a = gear.advise(ref, sheet(), level=14, market=market({SPELLBINDER_VEST: sale(3100)}, per_level=None),
                    every=True)
    assert not ah_of(next(u for u in a["upgrades_all"] if u["item_id"] == SPELLBINDER_VEST))["cheap"]


def test_a_drop_on_sale_keeps_its_drop_and_gets_the_price(ref):
    a = gear.advise(ref, sheet(), level=14, market=market({ROBE_OF_EVOCATION: sale(9000)}), every=True)
    u = next(u for u in a["upgrades_all"] if u["item_id"] == ROBE_OF_EVOCATION)
    assert [x["kind"] for x in u["sources"]] == ["drop", "ah"]


def test_what_no_scan_lists_is_never_an_ah_source(ref):
    """A soulbound item can't be listed, so it is never in a scan: its
    sources stay its own. An item held (bags or bank) stays an equip away,
    and a random-suffix base is never priced by its ID."""
    a = gear.advise(ref, sheet(), level=14, held={"bags": [ADEPTS_GLOVES], "bank": []},
                    market=market({ADEPTS_GLOVES: sale(100)}), every=True)
    assert not any(ah_of(u) for u in a["upgrades_all"])
    u = next(u for u in a["upgrades_all"] if u["item_id"] == ADEPTS_GLOVES)
    assert [x["kind"] for x in u["sources"]] == ["bags"]
    assert all(x["kind"] == "drop" for u in a["upgrades_all"] if u["item_id"] == CRYSTALLINE_CUFFS
               for x in u["sources"])
    assert not gear.buyable({"quality": 2, "req": 10, "stats": {"armor": 20}}, 14)
    assert gear.buyable({"quality": 2, "req": 10, "stats": {"armor": 20, "int": 3}}, 14)
    assert gear.buyable({"quality": 1, "req": 10, "stats": {"armor": 20}}, 14)
    # No required level known: only up to his own level.
    assert gear.buyable({"quality": 2, "req": 0, "ilvl": 14, "stats": {"int": 3}}, 14)
    assert not gear.buyable({"quality": 4, "req": 0, "ilvl": 60, "stats": {"int": 3}}, 14)


def test_without_a_market_the_answer_is_as_before(ref):
    a = gear.advise(ref, sheet(), level=14)
    assert "ah" not in a and not any(ah_of(u) for u in a["upgrades"])


# --- the MCP ------------------------------------------------------------------


def test_mcp_gear_carries_the_auction_house():
    logs = [("stats by (item_key, scan_day, realm, faction) min(price_copper) as lo",
             [*scans((0, 60)), price(SPELLBINDER_VEST, 0, 3100), price(CRYSTALLINE_CUFFS, 3, 50)]),
            *fakes.LOGS]
    fake = fakes.FakeStores(logs=logs)
    try:
        out = tools.call(tools.Ctx(store=Store(vl_url=fake.url)), "gear", {"char": fakes.CHAR, "limit": 40})
    finally:
        fake.close()
    vest = next(u for u in out["buy_now"] if u["item_id"] == SPELLBINDER_VEST)
    ah = vest["auction_house"]
    assert ah["price"] == "31s" and ah["price_copper"] == 3100 and ah["on_sale"] == "on sale today"
    assert ah["share_of_your_gold_pct"] == pytest.approx(2.5) and ah["affordable"] is True
    assert ah["gain_per_gold"] == pytest.approx(vest["gain_points"] / 0.31, abs=0.01)
    assert {"cheap", "listed", "seen_day", "share_of_next_riding_left_pct"} <= set(ah)
    assert any(w.startswith("auction house: 31s") for w in vest["where"])
    assert out["auction_house"]["upgrades_on_sale"] >= 1 and "to_do" not in out["auction_house"]
    # The stale price (3 days) is nowhere.
    assert not any(u["item_id"] == CRYSTALLINE_CUFFS and "auction_house" in u for u in out["next_upgrades"])


def test_mcp_gear_without_scans_says_to_scan():
    logs = [r for r in fakes.LOGS if r[1] is not fakes.PRICES and r[1] is not fakes.PRICE_DAYS]
    fake = fakes.FakeStores(logs=logs)
    try:
        out = tools.call(tools.Ctx(store=Store(vl_url=fake.url)), "gear", {"char": fakes.CHAR})
    finally:
        fake.close()
    assert out["auction_house"]["to_do"] == ahsource.notice_none()
    assert not any("auction_house" in u for u in out["next_upgrades"])


def test_price_list_one_point_a_day_across_faction_houses():
    """The shared price read (views.price_rows) groups by faction too, for
    the Auction House source; the price list still shows one point a day
    per realm, the lowest of its houses."""
    from wowstats.views import build_prices

    rows = [{"item_key": "2592", "scan_day": "2026-11-17", "realm": "R", "faction": "Alliance", "lo": "50", "hi": "80",
             "av": "3", "name": "Wool Cloth", "item_id": "2592"},
            {"item_key": "2592", "scan_day": "2026-11-17", "realm": "R", "faction": "Horde", "lo": "40", "hi": "60",
             "av": "9", "name": "Wool Cloth", "item_id": "2592"}]
    it = build_prices(rows)["items"][0]
    assert it["series"] == [{"day": "2026-11-17", "lo": 40.0, "hi": 80.0, "available": 9.0}]
    assert it["price"] == 40 and it["scans"] == 1
