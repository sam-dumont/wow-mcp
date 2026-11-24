"""The auction house in the analysis data (prep 14): ah_prices, the price
list's own read of the Auctionator scans (wowstats.ahshared.price_rows), and
ah_upgrades, the gear advice's upgrades with their price. On the docs site's
demo character (tests/degraded.py, made-up data): its scans price three of
its upgrades. "Which upgrades are cheapest on the Auction House" went
unanswered before (lab run on 0.9.0, 2026-11-24)."""

from __future__ import annotations

import json
import warnings

import pytest

from tests import degraded as D
from tests.analysis.test_analysis import env_for
from wowstats import ahsource, analysis
from wowstats.analysis import drive, manifest, prep, semantic
from wowstats.mcp import route
from wowstats.plan.facts import store_for

warnings.filterwarnings("ignore", module="boring_semantic_layer")

SKIP = ("wow-prices", "wow-player", "wow-combatlog")  # prep.query's own filter


def _mine(rows):
    return [dict(r) for r in rows if r.get("char") == D.CHAR and r["feed"] not in SKIP]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    rows = D.base_rows()
    with D.clock():
        env, _vl = D.store_env("sqlite", rows, tmp_path_factory.mktemp("ah"))
        prices = analysis.prices_for(store_for(env), env)
        mine = _mine(rows)
        db = prep.build(mine, D.CHAR, route.reference(), advice=analysis.advice_for(mine, D.CHAR, {}), prices=prices)
        layer, ro = semantic.local(db)
        yield {"rows": rows, "prices": prices, "db": db, "layer": layer, "ro": ro,
               "line": drive.ah_line(layer, ro)}


def test_the_prices_are_the_price_lists_read(demo):
    scans = [r for r in demo["rows"] if r["feed"] == "wow-prices"]
    days = {(r["item_key"], r["scan_day"]) for r in scans}
    assert len(demo["prices"]) == len(days) > 0
    got = demo["db"].execute("SELECT COUNT(*), COUNT(DISTINCT scan_day), MIN(ruleset), MIN(faction) "
                             "FROM ah_prices").fetchone()
    assert got == (len(days), 2, "Dawnwatch", "Alliance")
    one = next(r for r in scans if r["item_key"] == "2589" and r["scan_day"] == "2026-11-17")
    assert demo["db"].execute("SELECT price_copper, price_high_copper, listed FROM ah_prices "
                              "WHERE item_key = '2589' AND scan_day = '2026-11-17'").fetchone() == (
        int(one["price_copper"]), int(one["price_high_copper"]), int(one["available"]))


def test_no_row_says_who_scanned(demo):
    db = demo["db"]
    for t in ("ah_prices", "ah_upgrades"):
        cols = {c[1] for c in db.execute(f'PRAGMA table_info("{t}")')}
        assert not cols & {"char", "character", "source_id", "player", "_msg"}, t
        assert cols == set(prep.DICTIONARY[t][1]), t
        for row in db.execute(f'SELECT * FROM "{t}"'):
            assert D.CHAR not in json.dumps(row), t
    # Even when a read hands a scanner's name along, nothing keeps it.
    raw = [{**r, "char": "Someone-Else", "source_id": "x"} for r in demo["prices"]]
    other = prep.build(_mine(demo["rows"])[:50], D.CHAR, None, prices=raw)
    assert "Someone-Else" not in json.dumps(other.execute("SELECT * FROM ah_prices").fetchall())


def test_the_join_prices_the_upgrades_as_the_gear_advice_does(demo):
    db = demo["db"]
    got = {r[0]: r[1:] for r in db.execute("SELECT item_id, price_copper, on_sale, effort_elsewhere, gain_per_gold "
                                           "FROM ah_upgrades")}
    ups = {r[0] for r in db.execute("SELECT item_id FROM upgrade_candidates")}
    priced = {r[0] for r in db.execute("SELECT item_id FROM ah_prices WHERE item_key = CAST(item_id AS TEXT)")}
    assert set(got) == ups & priced == {4323, 9779, 9786}
    with D.clock():
        import time

        sale = ahsource.on_sale(demo["prices"], "Alliance", time.time(), ruleset="Dawnwatch")["items"]
    for iid, (price, on, effort, per_gold) in got.items():
        assert on == 1 and price == sale[iid]["price_copper"]
        gain = db.execute("SELECT MAX(gain) FROM upgrade_candidates WHERE item_id = ?", (iid,)).fetchone()[0]
        assert per_gold == round(gain / (price / 10000), 2)
        assert effort in ("easy", "some effort", "hard")


def test_one_query_answers_the_cheapest_upgrade_on_the_ah(demo):
    out = semantic.rows(demo["layer"], demo["ro"], drive.AH_QUERY)
    assert [(r["item"], r["ah_upgrade_price_copper"]) for r in out] == [
        ("Bandit Cloak", 1854), ("Raider's Cloak", 2113), ("Shadow Hood", 14500)]
    assert out[0]["slot"] == "Back" and out[0]["effort_elsewhere"] == "hard"
    # And a price over time, by item: two scan days give a change.
    p = semantic.rows(demo["layer"], demo["ro"], {"metrics": ["ah_price_latest_copper", "ah_price_change_copper"],
                                                  "group_by": ["item_id"],
                                                  "where": [{"dimension": "item_id", "op": "=", "value": 9779}]})
    assert p == [{"item_id": 9779, "ah_price_latest_copper": 1854, "ah_price_change_copper": 11,
                  "ah_price_change_copper_evidence": 2}]


def test_the_judge_can_cite_them():
    # verify.history_read counts "game" tables only: a finding on the auction
    # house must stand on them; a WEIGHTS change revalidates the join.
    assert analysis.TABLES["ah_prices"] == analysis.TABLES["ah_upgrades"] == "game"
    assert semantic.reads({"sem_ah_upgrades"}) == {"ah_upgrades"}
    assert semantic.reads({"sem_ah_prices"}) == {"ah_prices"}
    assert manifest.reads_scoring({"sql": "SELECT MIN(price_copper) FROM ah_upgrades"})
    assert not manifest.reads_scoring({"sql": "SELECT MIN(price_copper) FROM ah_prices"})


def test_the_brief_says_it_in_one_line(demo, tmp_path, monkeypatch):
    assert demo["line"].startswith("newest scan 0 days old, ")
    assert "3 upgrades priced, 3 on sale now" in demo["line"] and "\n" not in demo["line"]
    rows, prices = _mine(demo["rows"]), demo["prices"]
    monkeypatch.setattr(prep, "query", lambda store, stream, char: [dict(r) for r in rows])
    monkeypatch.setattr(analysis, "other_players", lambda store, stream, char: set())
    monkeypatch.setattr(analysis, "prices_for", lambda store, env: prices)
    drive._WS.clear()
    with D.clock():
        b = drive.brief({**env_for(tmp_path), "DEFAULT_CHAR": D.CHAR}, D.CHAR)
    drive._WS.clear()
    assert b["facts"]["auction_house"] == demo["line"]
    assert len(json.dumps(b)) < 16000


def test_without_scans_the_tables_are_empty_and_the_line_says_so():
    class Down:
        def vl_direct(self, *a, **k):
            raise OSError("down")

    assert analysis.prices_for(None, {}) == [] and analysis.prices_for(Down(), {}) == []
    db = prep.build([], "Nobody", None)
    assert db.execute("SELECT (SELECT COUNT(*) FROM ah_prices) + (SELECT COUNT(*) FROM ah_upgrades)").fetchone() == (0,)
    assert drive.ah_line(*semantic.local(db)).startswith("no Auctionator scan")
