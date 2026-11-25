"""BiS and the realistic pick, per slot (owner, 2026-11-25: "this is what
is the BiS, here is the realistic pick"): the item that scores most at the
character's level whatever it takes, the ranking's pick next to it with what
makes it win, shown once when they're the same item, and an auction house
BiS priced against the gold and the riding goal."""

from __future__ import annotations

from tests.test_advice_truth import advise, caster
from tests.test_ahsource import market, sale
from tests.test_obtainability import EARTHEN_ROD, EASY_CHEST, KODO_BRANDER, RARE_CHEST, two_chests
from wowstats import gear
from wowstats.mcp import tools as T


def chest(level, mkt=None):
    ref = two_chests()
    a = gear.advise(ref, caster(), level=level, every=True, held={"bags": [], "bank": []}, market=mkt)
    return next(s for s in a["slots"] if s["slot_name"] == "Chest")


def test_same_item_shows_once():
    a = advise(caster())
    same = [s for s in a["slots"] if s["pick"] and s["pick"]["same"]]
    assert same, "no slot whose pick is its BiS on the fixture"
    for s in same:
        assert s["bis"]["item_id"] == s["best"]["item_id"]
        assert s["pick"]["behind_bis_pct"] == 0 and s["pick"]["reasons"] == []
        if s["bis"]["effort"] in ("easy", "some effort") and not (s["bis"].get("cost") or {}).get("short_copper"):
            assert s["pick"]["within_reach"] and s["pick"]["says"] == "BiS, and within reach."


def test_hard_bis_against_easy_pick():
    """At 59 the 0.03% drop scores about 2% more than the vendor chest: it is
    BiS, the vendor chest the pick, and the pick says why with the numbers."""
    s = chest(59)
    b, p = s["bis"], s["pick"]
    assert b["item_id"] == RARE_CHEST and b["effort"] == "long shot"
    assert gear.odds(b) == {"chance_pct": 0.03, "runs": 3333}
    assert s["best"]["item_id"] == EASY_CHEST and p["item_id"] == EASY_CHEST and not p["same"]
    assert p["behind_bis_pct"] == gear.behind_pct(b["score"], s["best"]["score"]) and 0 < p["behind_bis_pct"] < 10
    assert p["reasons"][0] == "easy where BiS is a long shot (about 3,333 runs)"
    assert p["says"] == f"{p['behind_bis_pct']}% behind BiS: easy where BiS is a long shot (about 3,333 runs)."


def test_a_long_shot_bis_with_its_runs():
    a = advise(caster())
    ranged = next(s for s in a["slots"] if s["slot_name"] == "Ranged")
    assert ranged["bis"]["item_id"] == EARTHEN_ROD and ranged["pick"]["item_id"] == KODO_BRANDER
    assert "long shot (about 10,000 runs)" in ranged["pick"]["says"]


def test_auction_house_bis_against_the_gold_and_riding():
    """Staff of Jordan's case: an 84g listing with 16g 72s in the bags and
    riding at 40 still to pay. Out of the gold the listing is some effort,
    so the vendor chest stays the pick; the BiS carries the price line and
    one action."""
    mkt = market({RARE_CHEST: sale(840000)}, money=167200)
    mkt["mount"] = {"name": "riding.apprentice", "level": 40, "need_copper": 1000000, "left_copper": 1000000 - 167200}
    s = chest(59, mkt)
    b, p = s["bis"], s["pick"]
    assert b["item_id"] == RARE_CHEST and b["effort"] == "some effort"
    assert b["cost"]["says"] == "84g on the auction house: you have 16g 72s; riding at 40 needs 100g."
    assert b["cost"]["action"] == "Save 67g 28s more, then buy it at the auction house."
    assert b["cost"]["short_copper"] == 840000 - 167200
    assert p["item_id"] == EASY_CHEST
    assert p["reasons"][0] == "easy where BiS is some effort (84g, 67g 28s more than you have)"

    # With the gold: the listing is easy, the BiS is the pick, shown once.
    mkt = market({RARE_CHEST: sale(840000)}, money=900000)
    s = chest(59, mkt)
    assert s["bis"]["item_id"] == RARE_CHEST and s["bis"]["effort"] == "easy"
    assert s["pick"]["same"] and s["pick"]["says"] == "BiS, and within reach."
    assert s["bis"]["cost"]["action"] == "Buy it at the auction house."


def test_at_60_the_pick_is_bis():
    s = chest(60)
    assert s["bis"]["item_id"] == RARE_CHEST and s["pick"]["same"]
    # A long shot: the pick, its odds shown, not "within reach".
    assert not s["pick"]["within_reach"] and s["pick"]["says"] == "BiS, and your pick."


def test_worn_bis_has_no_pick():
    a = advise(caster())
    for s in a["slots"]:
        if s["bis"] and s["bis"].get("worn"):
            assert s["pick"] is None and s["bis"]["item_id"] == s["current"]["item_id"]
        if s["slot"] == gear.OFF:
            assert s["bis"] is None and s["pick"] is None


def test_mcp_carries_the_pair():
    s = chest(59)
    assert T._bis_out(s["bis"])["effort"] == "long shot"
    assert T._bis_out(s["bis"])["odds"]["runs"] == 3333
    assert T._pick_out(s["pick"]) == {"item": s["pick"]["name"], "item_id": EASY_CHEST, "same_as_bis": False,
                                      "within_reach": True, "behind_bis_pct": s["pick"]["behind_bis_pct"],
                                      "why": s["pick"]["reasons"], "says": s["pick"]["says"]}


def test_gear_nobody_says_where_to_get_is_never_bis():
    """Every other item has no source in two_chests: they stay in the best
    possible list (bis_unknown), never the BiS (no action to give, and an
    unknown source is no open content)."""
    s = chest(60)
    assert s["bis_unknown"] and s["bis_unknown"]["item_id"] != RARE_CHEST
    assert s["bis"]["item_id"] == RARE_CHEST and s["bis"]["sources"]


def test_the_overview_names_the_bis_beside_the_pick():
    from wowstats import insights

    a = gear.advise(two_chests(), caster(), level=59, every=True, held={"bags": [], "bank": []})
    u = next(x for x in a["upgrades"] if x["item_id"] == EASY_CHEST)
    assert insights._bis_text(a, u) == " BiS: Alanna's Embrace, a long shot (about 3,333 runs)."
    rare = {**u, "item_id": RARE_CHEST}
    assert insights._bis_text(a, rare) == ""  # not the slot's pick: nothing to add
