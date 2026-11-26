"""The professions tool on the fake stores (tests/mcp/fakes.py): a Tailoring
60 character with no recipe list, no bag counts and no scan."""

import pytest

from tests.mcp import fakes
from wowstats import ahsource
from wowstats.mcp import tools
from wowstats.mcp.store import Store


@pytest.fixture(scope="module")
def ctx():
    f = fakes.FakeStores()
    yield tools.Ctx(store=Store(vl_url=f.url))
    f.close()


def test_the_skill_up_follows_polestars_guide_and_says_what_it_lacks(ctx):
    out = tools.call(ctx, "professions", {"char": fakes.CHAR, "profession": "tailoring", "target": 100})
    (p,) = out["professions"]
    assert (p["profession"], p["skill"], p["target"]) == ("Tailoring", 60, 100)
    crafts = p["route"]["crafts"]
    assert crafts[0]["craft"] == "Linen Belt" and crafts[0]["first_make"][0]["craft"] == "Bolt of Linen Cloth"
    assert [c["skill_from"] for c in crafts] == sorted(c["skill_from"] for c in crafts)
    assert crafts[-1]["skill_to"] == 100
    # Journeyman (cap 150) at 75, from the trainers data.
    assert [(t["rank"], t["copper"]) for t in p["route"]["train"]] == [(2, 500)]
    assert p["recipes_known"] is None and p["recipes_unlock"]
    assert out["inventory"]["counts_known"] is False and out["inventory"]["unlock"]
    assert out["auction_house"]["prices_known"] is False
    assert "Linen Cloth" in p["gold"]["unpriced"]
    assert p["gold"]["line"].startswith("Tailoring 60 → 100: ~")
    assert p["next_step"].startswith("Make 32 Bolt of Linen Cloth first")


def test_one_item_craft_or_buy(ctx):
    out = tools.call(ctx, "professions", {"char": fakes.CHAR, "profession": "Tailoring",
                                          "item": "Gloves of Meditation"})
    it = out["professions"][0]["item"]
    assert (it["item_id"], it["spell_id"], it["needs_skill"]) == (4318, 3852, 105)
    assert it["recipe"]["trainer_skill"] == 105
    assert it["recipe"]["selected"]["source"].endswith("/1.60.1.71091/trainers.json")
    assert it["bind_on_pickup"] is False


def test_a_bad_profession_is_refused(ctx):
    with pytest.raises(tools.ToolError, match="profession is one of"):
        tools.call(ctx, "professions", {"char": fakes.CHAR, "profession": "Basket weaving"})


def test_latest_keeps_an_old_price_with_its_day():
    rows = [{"item_key": "4306", "lo": 150, "scan_day": "2026-11-01", "t": "2026-11-01T10:00:00Z",
             "ruleset": "PvE", "faction": "Alliance"},
            {"item_key": "4306", "lo": 120, "scan_day": "2026-11-20", "t": "2026-11-20T10:00:00Z",
             "ruleset": "PvE", "faction": "Alliance"},
            {"item_key": "2592", "lo": 60, "scan_day": "2026-11-01", "t": "2026-11-01T10:00:00Z",
             "ruleset": "PvE", "faction": "Alliance"}]
    now = 1795435200.0  # 2026-11-23
    got = ahsource.latest(rows, "Alliance", now)
    assert got[4306]["price_copper"] == 120 and got[4306]["seen_day"] == "2026-11-20"
    assert got[2592]["days_ago"] >= 20  # gone from on_sale, still a price for a craft
    assert ahsource.latest(rows, "Horde", now) == {}
