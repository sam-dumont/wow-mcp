"""wowstats.timebounds at its edges: equal timestamps, midnight, both DST
changes in Europe/Brussels, an empty history (docs/guarantees.md, "Time
rules")."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from wowstats import stores
from wowstats import timebounds as tb

BXL = tb.zone("Europe/Brussels")[1]
SPRING, AUTUMN = date(2026, 3, 29), date(2026, 10, 25)


def test_one_session_rule():
    assert tb.SESSION_GAP_S == stores.SESSION_GAP_S == 330
    assert not tb.ends_session(None, 0)
    assert not tb.ends_session(100, 430)  # exactly 330 continues
    assert tb.ends_session(100, 430.001)


def test_a_window_is_half_open():
    assert tb.within(10, 10, 20)  # the start is in
    assert not tb.within(20, 10, 20)  # the end is not
    assert not tb.within(9.999, 10, 20)
    assert tb.within(10, 10, None) and tb.within(-1, None, 0) and not tb.within(0, None, 0)
    # Two windows that touch share no instant.
    assert [tb.within(20, 10, 20), tb.within(20, 20, 30)] == [False, True]
    a = datetime(2026, 11, 3, tzinfo=UTC)
    assert tb.within(a, a, None)


def test_a_span_between_records_is_closed():
    assert tb.in_span(10, 10, 20) and tb.in_span(20, 10, 20)
    assert tb.in_span(10, 10, 10)  # a one-record session
    assert not tb.in_span(20.001, 10, 20)


def test_at_or_before():
    times = [10, 20, 20, 30]
    assert tb.at_or_before(times, 9) == -1
    assert tb.at_or_before(times, 10) == 0  # a state written at x holds at x
    assert tb.at_or_before(times, 20) == 2  # equal times: the last one written
    assert tb.at_or_before(times, 29.9) == 2
    assert tb.at_or_before(times, 99) == 3
    assert tb.at_or_before([], 5) == -1
    hist = [(10, "a"), (20, "b"), (20, "c")]
    assert [tb.state_at(hist, x) for x in (9, 10, 19, 20, 21)] == [None, "a", "a", "c", "c"]
    assert tb.state_at([], 5, default="none") == "none"


@pytest.mark.parametrize("day, hours", [(SPRING, 23), (AUTUMN, 25), (date(2026, 11, 3), 24)],
                         ids=["dst-spring", "dst-autumn", "plain"])
def test_a_local_day(day, hours):
    start, end = tb.day_bounds(day, BXL)
    assert end - start == hours * 3600
    assert tb.local_day(start, BXL) == day  # midnight is the new day's
    assert tb.local_day(start - 0.001, BXL) < day
    assert tb.local_day(end, BXL) > day
    assert tb.local(start, BXL).hour == 0


def test_weeks_are_iso_weeks_in_local_time():
    assert tb.week_start(date(2026, 11, 8)) == date(2026, 11, 2)  # a Sunday: that week's Monday
    assert tb.week_start(date(2026, 11, 2)) == date(2026, 11, 2)
    assert [(lambda b: (b[1] - b[0]) / 3600)(tb.week_bounds(d, BXL)) for d in (SPRING, AUTUMN, date(2026, 11, 3))] \
        == [167, 169, 168]
    start, _ = tb.week_bounds(AUTUMN, BXL)
    assert tb.local(start, BXL).strftime("%a %H:%M") == "Mon 00:00"


def test_the_nightly_recheck_counts_local_days(monkeypatch):
    from wowstats.analysis import due, prep

    monkeypatch.setattr(prep, "TZ", BXL)
    env = {"ANALYSIS_HOUR": "4"}
    # Last run 23:30 local on the 2nd: due from 04:00 local on the 3rd, not before.
    last = datetime(2026, 11, 2, 23, 30, tzinfo=BXL).isoformat()
    assert due(env, last, datetime(2026, 11, 3, 4, 0, tzinfo=BXL))
    assert not due(env, last, datetime(2026, 11, 3, 3, 59, tzinfo=BXL))
    # A run on local midnight is that day's: nothing more until the next one.
    assert not due(env, datetime(2026, 11, 3, 0, 0, tzinfo=BXL).isoformat(), datetime(2026, 11, 3, 5, tzinfo=BXL))


def test_range_edges():
    assert tb.range_edges(None, None) == []
    assert tb.range_edges("2026-10-25", "2026-10-25") == [(">=", datetime(2026, 10, 25)),
                                                         ("<", datetime(2026, 10, 26))]
    assert tb.range_edges(None, "2026-10-25T12:00") == [("<=", datetime(2026, 10, 25, 12))]


def test_an_unknown_zone_falls_back_to_utc(capsys):
    assert tb.zone("Mars/Olympus")[0] == "UTC"
    assert "not a known timezone" in capsys.readouterr().err
