"""The Deaths section's "Per level" (review 6, open since review 5).

The question it answers: in a level the Recorder watched, how many times
does the character die? The page divided every Recorder death by Polestar's
level count, so a Recorder switched on late (or off for an evening) put
levels it never saw in the denominator: the demo with the Recorder from
2026-11-12 on read 3 deaths / 12 levels = 0.25, for a character that died
once in the one level the Recorder saw whole.

Now both sides read the same levels (paired eligibility, like
semantic.yaml's ratios): a level counts when the Recorder saw it whole, from
the ding into it (on or after its first record) to the ding out of it, with
no gap in it (wowstats.coverage.recorder_gaps). The level in play and the
ones that began before the Recorder stay out of both sides. The answer says
which levels it read.
"""

from __future__ import annotations

import pytest

from tests import degraded as D
from wowstats import redact, ui, views


def _t(day: int, hour: int = 18) -> str:
    return f"2026-03-{day:02d}T{hour:02d}:00:00Z"


def test_a_hand_counted_case():
    """Dings into 11 to 15 on days 1, 3, 5, 7 and 9; the Recorder on from
    day 4 (the middle of level 12) to now; an evening off in level 14.
    Deaths by level: 12 -> 3 (seen after day 4), 13 -> 2, 14 -> 4, 15 -> 1.

    Level 11 and 12 began before the Recorder, 14 has a gap, 15 is in play:
    only 13 was seen whole, with 2 deaths. 2 / 1 = 2.0. The page's old
    reading: 10 deaths / 4 finished levels = 2.5. Without the gap, 13 and 14:
    (2 + 4) / 2 = 3.0."""
    climb = [{"level": n, "t": _t(d)} for n, d in ((11, 1), (12, 3), (13, 5), (14, 7), (15, 9))]
    spans = [(views.parse_t(_t(4)).timestamp(), views.parse_t(_t(10)).timestamp())]
    by_level = {"12": 3, "13": 2, "14": 4, "15": 1}
    gap = [{"level": 14, "from": _t(7), "to": _t(9), "off_s": 3600}]
    assert views.deaths_per_level(by_level, climb, spans, gap) == {"value": 2.0, "deaths": 2, "n": 1, "levels": [13]}
    assert views.deaths_per_level(by_level, climb, spans, []) == {"value": 3.0, "deaths": 6, "n": 2, "levels": [13, 14]}
    # The Recorder's first record on the ding's own second: that level is seen whole.
    on_ding = [(views.parse_t(_t(3)).timestamp(), spans[0][1])]
    assert views.deaths_per_level(by_level, climb, on_ding, [])["levels"] == [12, 13, 14]
    # No level seen whole (no Recorder, or only the level in play): no ratio, never 0.
    assert views.deaths_per_level(by_level, climb, [], []) is None
    assert views.deaths_per_level({"15": 1}, climb, [(views.parse_t(_t(9, 19)).timestamp(),) * 2], []) is None


@pytest.mark.parametrize("scenario, want", [
    # Dings into 23 to 46 seen, first record before the ding into 23 (level 22
    # is partial, 46 in play): 23..45, 23 levels, 22 deaths in them.
    ("full", {"value": 22 / 23, "deaths": 22, "levels": list(range(23, 46))}),
    # The Recorder from 11-12 on: 40 began on 11-10, 46 is in play. Levels 41
    # to 45, four deaths (three in Stranglethorn, one in Feralas).
    ("recorder-later", {"value": 0.8, "deaths": 4, "levels": [41, 42, 43, 44, 45]}),
    # An hour off in level 28 (its split is longer than the Recorder saw):
    # 28 and its death leave both sides. 21 deaths over 22 levels.
    ("recorder-gap", {"value": 21 / 22, "deaths": 21, "levels": [lv for lv in range(23, 46) if lv != 28]}),
    ("no-recorder", None),
])
def test_the_page_reads_deaths_and_levels_over_the_same_levels(tmp_path, scenario, want):
    vl = D.VL(D.rows_for(scenario))
    try:
        with D.clock():
            app = ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "MODEL3D": "off",
                          "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp_path), "WOW_ITEMS": "off"})
            full = app.api_char(D.CHAR)
    finally:
        vl.close()
    got = full["deaths"]["per_level"]
    if want is None:
        assert got is None
        return
    assert got == {**want, "n": len(want["levels"]), "value": pytest.approx(want["value"], abs=0.005)}
    # Every death it counts is in by_level at a level it read.
    assert got["deaths"] == sum(full["deaths"]["by_level"].get(str(lv), 0) for lv in got["levels"])
    # /zogzog/ shows the same section: the same ratio and count, not which
    # levels (that is coverage, kept off the static tiers).
    assert redact.zogzog("char", full)["deaths"]["per_level"] == {k: got[k] for k in ("value", "deaths", "n")}
