"""wowstats/plan/estimate.py parse_constraints reads text a player posts
(the plan request): bounded, whitespace folded, so no input makes its
patterns backtrack for long."""

from __future__ import annotations

import time

from wowstats.plan import estimate


def test_a_pathological_request_parses_fast():
    for text in (" " * 200_000 + "x", "only" + " " * 200_000 + "2", "1" * 200_000 + " h", "for 2" + "\t" * 100_000):
        t0 = time.perf_counter()
        out = estimate.parse_constraints(text)
        assert time.perf_counter() - t0 < 0.5
        assert len(out["text"]) <= estimate.MAX_CONSTRAINTS


def test_the_usual_requests_still_parse():
    assert estimate.parse_constraints("only  2h, no dungeons")["hours"] == 2
    assert estimate.parse_constraints("about 90 minutes")["hours"] == 1.5
    out = estimate.parse_constraints("I want to level   tailoring with friends")
    assert out["profession"] == "Tailoring" and out["group"] is True
    assert estimate.parse_constraints("no professions")["no_professions"] is True
