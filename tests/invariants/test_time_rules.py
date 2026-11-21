"""Temporal boundaries (property 9): midnight, the DST changes of
Europe/Brussels (last Sunday of March and of October) and a long offline
gap follow the rules docs/guarantees.md writes down:

- A session ends after more than 330 s without a Recorder record; exactly
  330 s continues it. The page, the MCP and the analysis split alike.
- Durations are real seconds: a session across a DST change lasts its
  end minus its start, never an hour more or less.
- A day is the player's local day (WOW_TIMEZONE), from 00:00:00 included to
  the next 00:00:00 excluded: 23 hours in March, 25 in October. Per-day
  answers add up to the total.
- query_metric's `since` is included; `until` as a date includes that whole
  day, as a date and time it is included.
- Time in a zone and online time stop at a session's last record: an
  offline gap counts in neither.

Synthetic records only, made here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from tests import degraded as D
from tests.invariants import histories as H
from wowstats import ui
from wowstats.analysis import prep

CHAR = "Testclock-Fixture"
BXL = ZoneInfo("Europe/Brussels")
# 2026's changes in Brussels: 29 March 01:00 UTC (02:00 -> 03:00), 25 October 01:00 UTC (03:00 -> 02:00).
SPRING = datetime(2026, 3, 29, 1, 0, tzinfo=UTC).timestamp()
AUTUMN = datetime(2026, 10, 25, 1, 0, tzinfo=UTC).timestamp()
MIDNIGHT = datetime(2026, 11, 3, 0, 0, tzinfo=BXL).timestamp()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Recorder:
    """Recorder records with source ids in chunk order, oldest first."""

    def __init__(self):
        self.rows: list[dict] = []
        self.seq = 0
        self.idx = 0

    def rec(self, t: float, kind: str, **f) -> None:
        if kind == "session":
            self.seq, self.idx = self.seq + 1, 0
        self.idx += 1
        self.rows.append({"_time": _iso(t), "char": CHAR, "component": "wow", "feed": "wow-recorder", "kind": kind,
                          "layer": "app", "source_id": f"rec:cc:{self.seq}:{self.idx}", **f})

    def play(self, t0: float, t1: float, zone: str, step: float = 60) -> None:
        """Online from t0 to t1: a chunk header, a zone, a position every `step` s."""
        self.rec(t0, "session", level="20")
        self.rec(t0, "zone", zone=zone)
        t = t0 + step
        while t < t1:
            self.rec(t, "pos", map_id="1429", x="0.5", y="0.5", zone=zone)
            t += step
        self.rec(t1, "pos", map_id="1429", x="0.5", y="0.5", zone=zone)


def three_ways(rows: list[dict], tmp_path, tz: str = "Europe/Brussels") -> dict:
    """The sessions as the page, the MCP and the analysis split them."""
    from wowstats.mcp import tools as T
    from wowstats.mcp.store import Store as MStore

    vl = D.VL(rows)
    try:
        app = ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "MODEL3D": "off",
                      "DEFAULT_CHAR": CHAR, "HOME": str(tmp_path), "WOW_ITEMS": "off"})
        page = [(s["start"], s["end"]) for s in app.views.journal(CHAR)["sessions"]]
        ctx = T.Ctx(store=MStore(vl_url=vl.url), default_char=CHAR, env={})
        got = T.call(ctx, "sessions", {"char": CHAR, "since": "2000-01-01", "limit": 50})
        mcp = [(s["start"], s["end"]) for s in got["sessions"]]
    finally:
        vl.close()
    db = prep.build([dict(r) for r in rows], CHAR)
    fmt = lambda t: datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    analysis = [(fmt(a), fmt(b)) for a, b in db.execute("SELECT start_t, end_t FROM sessions ORDER BY start_t")]
    return {"page": sorted(page), "mcp": sorted(mcp), "analysis": analysis, "db": db}


@pytest.mark.parametrize("at", [MIDNIGHT, SPRING, AUTUMN], ids=["midnight", "dst-spring", "dst-autumn"])
@pytest.mark.parametrize("gap, sessions", [(329, 1), (330, 1), (331, 2)])
def test_the_session_rule_at_the_edges(tmp_path, at, gap, sessions):
    """A gap right on a midnight or a DST change: 330 s continues, 331 ends."""
    r = Recorder()
    r.play(at - 1200, at - gap / 2, "Elwynn Forest")
    r.rec(at + gap / 2, "pos", map_id="1429", x="0.5", y="0.5", zone="Elwynn Forest")
    r.play(at + gap / 2 + 60, at + 1500, "Elwynn Forest")
    got = three_ways(r.rows, tmp_path)
    assert got["page"] == got["mcp"] == got["analysis"], got
    assert len(got["page"]) == sessions


@pytest.mark.parametrize("at", [SPRING, AUTUMN], ids=["dst-spring", "dst-autumn"])
def test_a_session_across_a_dst_change_lasts_its_real_seconds(tmp_path, at):
    r = Recorder()
    r.play(at - 1800, at + 1800, "Westfall")
    got = three_ways(r.rows, tmp_path)
    db = got["db"]
    assert db.execute("SELECT duration_s FROM sessions").fetchall() == [(3600.0,)]
    assert db.execute("SELECT SUM(dur_s) FROM timeline").fetchone()[0] == 3600.0
    assert got["page"] == [(_iso(at - 1800)[:19] + "Z", _iso(at + 1800)[:19] + "Z")]
    # The local hours: none skipped or doubled by a fixed offset.
    hours = list(dict.fromkeys(h for (h,) in db.execute("SELECT hour_local FROM timeline ORDER BY t0")))
    assert hours == ([1, 3] if at == SPRING else [2])


def _deaths(times: list[float]) -> list[dict]:
    r = Recorder()
    for t in times:
        r.play(t - 30, t + 30, "Duskwood")
        r.rec(t, "death", zone="Duskwood")
    return r.rows


def _local(y, m, d, h=0, mi=0, s=0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=BXL).timestamp()


@pytest.fixture
def brussels(monkeypatch):
    monkeypatch.setattr(prep, "TZ", BXL)
    monkeypatch.setattr(prep, "TZ_NAME", "Europe/Brussels")


@pytest.mark.parametrize("day", [(3, 29), (10, 25), (11, 3)], ids=["dst-spring", "dst-autumn", "plain"])
def test_a_day_is_the_local_day_midnight_included(brussels, day):
    m, d = day
    # Deaths at 23:59:59 the day before, 00:00:00, 23:59:59 and the next 00:00:00, local.
    times = [_local(2026, m, d, 0) - 1, _local(2026, m, d, 0), _local(2026, m, d + 1, 0) - 1,
             _local(2026, m, d + 1, 0)]
    lay, ro = H.layer(H.build(_deaths(times), CHAR), "Europe/Brussels")
    by_day = H.ask(lay, ro, metrics=["deaths"], group_by=["time"], grain="day")
    got = {str(r[0]): r[1] for r in by_day["rows"]}
    want = {datetime.fromtimestamp(times[0], BXL).date().isoformat(): 1, f"2026-{m:02d}-{d:02d}": 2,
            datetime.fromtimestamp(times[3], BXL).date().isoformat(): 1}
    assert got == want
    assert sum(got.values()) == H.ask(lay, ro, metrics=["deaths"])["rows"][0][0] == 4
    # The day's length in real seconds: 23 h in March, 25 h in October.
    length = _local(2026, m, d + 1) - _local(2026, m, d)
    assert length == {(3, 29): 23, (10, 25): 25}.get(day, 24) * 3600


@pytest.mark.parametrize("day", [(3, 29), (10, 25), (11, 3)], ids=["dst-spring", "dst-autumn", "plain"])
def test_since_and_until_edges(brussels, day):
    m, d = day
    times = [_local(2026, m, d, 0) - 1, _local(2026, m, d, 0), _local(2026, m, d, 12),
             _local(2026, m, d + 1, 0) - 1, _local(2026, m, d + 1, 0)]
    lay, ro = H.layer(H.build(_deaths(times), CHAR), "Europe/Brussels")
    date = f"2026-{m:02d}-{d:02d}"

    def n(**q):
        rows = H.ask(lay, ro, metrics=["deaths"], **q)["rows"]
        return rows[0][0] if rows else 0

    assert n(since=date, until=date) == 3  # the whole local day, its midnight included, the next one not
    assert n(since=date) == 4
    assert n(until=date) == 4
    assert n(since=f"{date}T12:00:00", until=f"{date}T12:00:00") == 1  # a time is included on both ends


def test_a_long_offline_gap_counts_as_offline(tmp_path, brussels):
    """A zone record, then three days away: the zone's time and the online
    time stop at the session's last record; the next login is a new
    session, and its zone record starts a new visit."""
    r = Recorder()
    t0 = MIDNIGHT - 3600
    r.play(t0, t0 + 600, "Redridge Mountains")
    r.play(t0 + 3 * 86400, t0 + 3 * 86400 + 300, "Duskwood")
    got = three_ways(r.rows, tmp_path)
    assert len(got["page"]) == len(got["mcp"]) == len(got["analysis"]) == 2
    db = got["db"]
    assert db.execute("SELECT detail, dur_s FROM spans WHERE kind='zone' ORDER BY t0").fetchall() == [
        ("Redridge Mountains|", 600.0), ("Duskwood|", 300.0)]
    assert db.execute("SELECT SUM(dur_s), MAX(dur_s) FROM timeline").fetchone() == (900.0, 60.0)
    assert db.execute("SELECT SUM(duration_s) FROM sessions").fetchone()[0] == 900.0
