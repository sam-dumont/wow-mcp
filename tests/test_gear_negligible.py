"""Slots where nothing matters much at the character's level (owner,
2026-11-25): ForeverDB's formula counts on-use and procs as zero, so a
levelling trinket scores 0-1 points and the best of almost nothing read
"BiS", "empty → 100%" and "BiS, and within reach" (Lookie's Spyglass, a
healer trinket, on a level 37 Shadow priest). Under gear.NEGLIGIBLE_SHARE of
the worn gear's score a slot shows one plain line instead, the same on every
view, decided in one place (gear.mark_negligible). Real items: the test
priest wears Lookie's Spyglass and has a trinket slot free."""

from __future__ import annotations

from unittest import mock

import pytest

from tests import degraded as D
from tests.test_gear import sheet
from wowstats import gear, redact
from wowstats.mcp import tools

LOOKIES_SPYGLASS = 273298  # healer trinket, worn in slot 13 by tests/test_gear.py's priest
ROOGUGS_HEAD = 274152  # the best trinket a level 33 priest can get: 0.72 points
GUARDIAN_TALISMAN = 1490  # a trinket with stats: 7.2 points for the test priest
LINE = "No trinket matters much at your level: +0.7 points at best."


@pytest.fixture(scope="module")
def ref():
    r = gear.reference()
    assert r.available, r.why
    return r


def advise(ref, level: int) -> dict:
    return gear.advise(ref, sheet(), level=level, every=True, held={"bags": [], "bank": []})


def trinkets(a: dict) -> tuple[dict, dict]:
    t = [s for s in a["slots"] if s["slot"] in (13, 14)]
    return t[0], t[1]


def test_level_33_trinkets_say_one_plain_line(ref):
    a = advise(ref, 33)
    worn, empty = trinkets(a)
    assert worn["current"]["item_id"] == LOOKIES_SPYGLASS and empty["current"] is None
    for s in (worn, empty):
        assert s["negligible"] == LINE
        # No BiS, no pick against it, no "best possible" either.
        assert s["bis"] is None and s["pick"] is None and s["bis_unknown"] is None
    # The worn one keeps its numbers, without the "BiS" label (no doll badge).
    assert worn["standing"] == {"rank": 1, "pct": 100, "label": None, "negligible": LINE}
    best = empty["best"]
    assert best["item_id"] == ROOGUGS_HEAD and best["gain"] == 0.72
    assert (best["bis_pct"], best["bis_pct_worn"], best["delta_pct"], best["bis"]) == (None, None, None, None)
    assert best["negligible"] == LINE
    assert gear.gain_words(best) == "+0.7 points, no trinket matters much at your level"
    # Rows scored elsewhere (dungeon loot) read the same flag.
    assert a["bis_by_slot"]["13"]["negligible"] == LINE == a["bis_by_slot"]["14"]["negligible"]
    assert gear.bis_badge(a["bis_by_slot"]["14"], {"slot": 14, "new": 0.72, "empty": True})["bis_pct"] is None
    # Every other slot keeps its badges.
    assert [s["slot_name"] for s in a["slots"] if s["negligible"]] == ["Trinket", "Trinket"]


def test_the_share_decides(ref):
    """Gear score 98.92, best trinket 0.72 (0.73%): under 1%. With a lower
    share the same slots read BiS and "empty → 100%" again."""
    a = advise(ref, 33)
    assert a["gear_score"] == 98.92
    with mock.patch.object(gear, "NEGLIGIBLE_SHARE", 0.001):
        b = advise(ref, 33)
    worn, empty = trinkets(b)
    assert not worn["negligible"] and worn["standing"]["label"] == "BiS"
    assert gear.gain_words(empty["best"]) == "empty → 100% on your trinket"
    assert empty["pick"]["says"] == "BiS, and within reach."


def test_a_worn_trinket_with_nothing_to_measure_against_is_not_bis(ref):
    """At 20 no trinket is in reach: the BiS row named the worn Spyglass BiS."""
    worn, empty = trinkets(advise(ref, 20))
    assert worn["negligible"] == "No trinket matters much at your level: +0.1 points at best."
    assert worn["bis"] is None and worn["standing"]["label"] is None
    # Nothing in reach for the empty one: nothing to say there.
    assert empty["negligible"] is None and empty["bis"] is None and empty["best"] is None


def test_a_real_trinket_keeps_its_badge_next_to_a_negligible_slot(ref):
    """A trinket with stats worn (Guardian Talisman, 7.2 points) and the
    other slot's best a 0.72-point one: the worn one stays BiS, the empty
    one says no OTHER trinket matters (the demo between 35 and 44)."""
    s = sheet()
    s["items"] = [{**i, "item_id": GUARDIAN_TALISMAN} if i["slot"] == 13 else i for i in s["items"]]
    a = gear.advise(ref, s, level=33, every=True, held={"bags": [], "bank": []})
    worn, empty = trinkets(a)
    assert not worn["negligible"] and worn["standing"]["label"] == "BiS"
    assert empty["negligible"] == "No other trinket matters much at your level: +0.7 points at best."
    assert empty["best"]["item_id"] == ROOGUGS_HEAD and empty["bis"] is None and empty["pick"] is None
    assert gear.gain_words(empty["best"]) == "+0.7 points, no other trinket matters much at your level"


@pytest.mark.parametrize("level", [45, 60])
def test_trinkets_that_score_keep_their_badges(ref, level):
    a = advise(ref, level)
    assert not [s for s in a["slots"] if s["negligible"]]
    worn, empty = trinkets(a)
    assert worn["standing"]["label"].endswith("% of BiS") and empty["bis"] is not None


def test_the_demo_has_nothing_negligible():
    a = gear.advise_rows(D.rows_for("full"))
    assert not [s["slot_name"] for s in a["slots"] if s["negligible"]]
    assert all(st.get("negligible") is None for st in a["bis_by_slot"].values())


def test_the_mcp_marks_the_slot(ref):
    real = gear.advise
    with mock.patch.object(gear, "advise", lambda r, _s, level, **kw: real(r, sheet(), level=33, **kw)):
        from tests.mcp import fakes
        from wowstats.mcp.store import Store

        fake = fakes.FakeStores()
        try:
            out = tools.call(tools.Ctx(store=Store(vl_url=fake.url)), "gear", {"char": fakes.CHAR, "limit": 40})
        finally:
            fake.close()
    worn, empty = (s for s in out["slots"] if s["slot"] == "Trinket")
    for s in (worn, empty):
        assert s["negligible"] is True and s["negligible_why"] == LINE
        assert s["bis"] is None and s["pick"] is None
    assert worn["worn"] == f"worn {LOOKIES_SPYGLASS}" and worn["worn_standing"] is None
    best = empty["best_now"]
    assert best["item_id"] == ROOGUGS_HEAD and best["negligible"] is True and best["gain_points"] == 0.72
    assert "bis_pct" not in best and best["line"] == "+0.7 points, no trinket matters much at your level"
    assert "negligible_why" in out["how"]
    # The other slots carry no flag.
    assert all("negligible" not in s for s in out["slots"] if s["slot"] != "Trinket")


def test_zogzog_says_it_and_the_public_doll_shows_no_badge(ref):
    a = advise(ref, 33)
    z = redact.zogzog("gear", {"char": "Priest-Fixture", "available": True, **a})
    worn, empty = (s for s in z["slots"] if s["slot"] in (13, 14))
    assert worn["negligible"] == LINE and worn["standing"]["negligible"] == LINE and worn["standing"]["label"] is None
    assert empty["best"]["negligible"] == LINE
    pub = {x["slot"]: x for x in redact.worn_standing(a)}
    assert pub[13] == {"slot": 13, "rank": 1, "pct": 100, "label": None}


def test_the_page_says_the_same_item_line():
    """web/app.js upShort/upText (under node) against gear.gain_words."""
    from tests.test_bis_words_node import NODE, words

    if NODE is None:
        pytest.skip("node is not installed")
    u = {"slot_name": "Trinket", "slot": 14, "gain": 0.72, "bis_pct": None, "bis_pct_worn": None,
         "delta_pct": None, "negligible": LINE}
    assert words([u]) == [("+0.7", gear.gain_words(u))]
