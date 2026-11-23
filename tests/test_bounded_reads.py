"""One page load must never fan out into N whole-history reads (the events' VictoriaLogs,
1 GiB, was OOM-killed three times on 2026-11-18; the third by one load of
the full view, 130 queries).

The fake VictoriaLogs here answers LogsQL with wowstats.logsql over real
synthetic recorder events (tests/data/analysis-events.jsonl.gz), honours start, end
and limit, and counts requests and how many run at once."""

from __future__ import annotations

import concurrent.futures
import gzip
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wowstats import eventcache, logsql, stores, ui
from wowstats.stores import HISTORY_START, VL_PAGE, paged_read, parse_t

DATA = Path(__file__).resolve().parent / "data" / "analysis-events.jsonl.gz"
CHAR = "Testpriest-Fixture"
# What one full page load may cost on a cold server: the character list (3),
# the character's hourly count and its pages (1 + 2 for these 7k events),
# the account roster (2), the auction scans (2), the archive check (1), with
# room for one more. v0.3.1 made 130 for a 23k-event character.
MAX_QUERIES = 12


def events() -> list[dict]:
    with gzip.open(DATA) as f:
        rows = [json.loads(line) for line in f]
    # One more character snapshot after the last event, like a real character's logout.
    last = max(rows, key=lambda r: r["_time"])
    return rows + [{"_time": last["_time"], "feed": "wow-character", "char": CHAR, "level": "24",
                    "zone": "Duskwood", "class": "PRIEST", "race": "Gnome", "faction": "Alliance",
                    "source_id": f"character:{CHAR}:1"}]


class FakeVL:
    def __init__(self, rows: list[dict]):
        self.rows = sorted(rows, key=lambda r: parse_t(r.get("_time")))
        self.queries: list[tuple[str, int, int]] = []  # (query, limit, rows answered)
        self.lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0

    def __call__(self, url: str, params: dict | None = None, *a, **k) -> bytes:
        if "/select/logsql/query" not in url:
            return json.dumps({"data": {"result": []}}).encode()  # vm-wow: nothing
        with self.lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            time.sleep(0.005)  # long enough for parallel callers to overlap
            q = logsql.parse(params["query"])
            lo, hi, limit = parse_t(params["start"]), parse_t(params["end"]), int(params["limit"])
            rows = [r for r in self.rows if lo <= parse_t(r["_time"]) <= hi]
            out = q.run(rows)[:limit]
            with self.lock:
                self.queries.append((params["query"], limit, len(out)))
            return "\n".join(json.dumps(r) for r in out).encode()
        finally:
            with self.lock:
                self.inflight -= 1


@pytest.fixture
def fake_vl(monkeypatch):
    fake = FakeVL(events())
    monkeypatch.setattr(stores, "http_get", fake)
    monkeypatch.setattr(eventcache, "_SHARED", {})
    return fake


def test_one_full_page_load_reads_the_store_a_bounded_number_of_times(fake_vl, run_app, tmp_path, monkeypatch):
    app = ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": "http://vl.invalid",
                  "MODEL3D": "off", "DEFAULT_CHAR": CHAR, "HOME": str(tmp_path)})
    srv = run_app(app)
    assert srv.get("/api/chars")[0] == 200
    # web/app.js: the character with prices, then everything else at once.
    page = [f"/api/char?name={CHAR}", "/api/prices", f"/api/gear?name={CHAR}", f"/api/loot?name={CHAR}",
            f"/api/plan?char={CHAR}", f"/api/overview?name={CHAR}", f"/api/people?name={CHAR}",
            f"/api/journal?name={CHAR}", f"/api/timeline?name={CHAR}",
            # The Dungeons panel: the planner's gather (#9's one-per-character
            # lock) reads the same cached events, not VictoriaLogs again.
            f"/api/dungeons?name={CHAR}"]
    with concurrent.futures.ThreadPoolExecutor(len(page)) as pool:
        codes = dict(zip(page, pool.map(lambda p: srv.get(p)[0], page), strict=True))
    assert set(codes.values()) == {200}, codes
    qs = fake_vl.queries
    assert len(qs) <= MAX_QUERIES, "\n".join(q for q, _, _ in qs)
    assert fake_vl.max_inflight <= 2, "VL_GATE lets at most two requests run at once"
    # The character's events are read once, in pages of at most VL_PAGE rows.
    pages = [(q, n, got) for q, n, got in qs if "|" not in q]
    assert pages and all(n <= VL_PAGE + 1 for _, n, _ in pages)
    assert sum(got for _, _, got in pages) == len(fake_vl.rows)
    # A second load inside the freshness window reads nothing new.
    before = len(qs)
    app._cache.clear()
    with concurrent.futures.ThreadPoolExecutor(len(page)) as pool:
        list(pool.map(srv.get, page))
    assert all("char:in(" not in q for q, _, _ in fake_vl.queries[before:])
    # The play analysis' read (prep.query, the MCP's analysis tools) is
    # answered from the same cache.
    from wowstats.analysis import drive

    monkeypatch.setattr(drive, "_WS", {})
    before = len(fake_vl.queries)
    w = drive.workspace({"VL_URL": "http://vl.invalid", "HOME": str(tmp_path)}, CHAR)
    assert w.tables and all("char:in(" not in q for q, _, _ in fake_vl.queries[before:])


class SlowStore:
    """Counts what the cache asks; answers from `rows`."""

    stream = "feed:wow-*"
    vl_url, vl_archive_url, hot_days = "http://vl.invalid", "", 89

    def __init__(self, rows):
        self.rows = rows
        self.hist_calls = 0
        self.reads: list[tuple[datetime, datetime]] = []

    def vl_direct(self, query, keys=(), ops=None, limit=0, start=None, end=None):
        self.hist_calls += 1
        time.sleep(0.05)
        lo, hi = start or HISTORY_START, end or datetime.now(UTC)
        q = logsql.parse(query[len(self.stream) + 1:])
        return q.run([r for r in self.rows if lo <= parse_t(r["_time"]) <= hi])

    def vl_range(self, query, start, end, limit):
        self.reads.append((start, end))
        q = logsql.parse(query[len(self.stream) + 1:])
        return q.run([r for r in self.rows if start <= parse_t(r["_time"]) <= end])[:limit]


def _ev(t: datetime, sid: str, char: str = CHAR, feed: str = "wow-recorder", **f) -> dict:
    return {"_time": t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "char": char, "feed": feed, "source_id": sid,
            **f}


def test_parallel_callers_share_one_read():
    t0 = datetime(2026, 11, 12, 10, tzinfo=UTC)
    rows = [_ev(t0 + timedelta(minutes=i), f"r{i}", kind="kill") for i in range(300)]
    st = SlowStore(rows)
    cache = eventcache.EventCache(st)
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        got = list(pool.map(lambda _: cache.rows(CHAR), range(16)))
    assert st.hist_calls == 1 and len(st.reads) == 1
    assert all(g is got[0] for g in got) and len(got[0]) == 300


def test_a_refresh_reads_only_the_hours_that_changed():
    t0 = datetime(2026, 11, 12, 10, tzinfo=UTC)
    rows = [_ev(t0 + timedelta(minutes=10 * i), f"r{i}", kind="kill") for i in range(100)]
    st = SlowStore(rows)
    cache = eventcache.EventCache(st, fresh_s=0)
    assert len(cache.rows(CHAR)) == 100
    st.reads.clear()
    # The shipper sends a session after the fact: an event from an old hour.
    late = t0 + timedelta(hours=3, minutes=5)
    rows.append(_ev(late, "late", kind="death"))
    got = cache.rows(CHAR)
    assert len(got) == 101 and any(r["source_id"] == "late" for r in got)
    assert st.reads == [(t0 + timedelta(hours=3), t0 + timedelta(hours=4) - timedelta(microseconds=1))]
    # Nothing new: the hourly count alone.
    st.reads.clear()
    cache.rows(CHAR)
    assert st.reads == []
    # An hour gone from the store (retention) leaves the cache too.
    rows[:] = [r for r in rows if parse_t(r["_time"]) >= t0 + timedelta(hours=1)]
    assert len(cache.rows(CHAR)) == 101 - 6 and st.reads == []


def test_an_hour_over_a_page_is_read_by_the_minute():
    t0 = datetime(2026, 11, 12, 10, tzinfo=UTC)
    rows = [_ev(t0 + timedelta(seconds=i * 0.5), f"r{i}", kind="pos") for i in range(7000)]
    st = SlowStore(rows)
    cache = eventcache.EventCache(st, page=1000)
    got = cache.rows(CHAR)
    assert len(got) == 7000 and len({r["source_id"] for r in got}) == 7000
    assert len(st.reads) >= 7


def test_answers_come_from_the_cache_for_one_character_only():
    t0 = datetime(2026, 11, 12, 10, tzinfo=UTC)
    st = SlowStore([_ev(t0, "a", kind="death", zone="Duskwood"),
                    _ev(t0 + timedelta(minutes=1), "b", char="Testpriest Fixture", kind="death", zone="Westfall")])
    cache = eventcache.EventCache(st)
    cf = 'char:in("Testpriest Fixture", "Testpriest-Fixture")'
    rows = cache.answer(f'feed:wow-* feed:="wow-recorder" {cf} kind:=death | stats by (zone) count() as n')
    assert sorted((r["zone"], r["n"]) for r in rows) == [("Duskwood", "1"), ("Westfall", "1")]
    # Not one character's, or reaching the feeds the cache doesn't hold: VictoriaLogs.
    assert cache.answer("feed:wow-* char:* | stats by (char) count() as n") is None
    assert cache.answer(f'feed:wow-* {cf} | stats count() as n') is None
    assert cache.answer(f'feed:wow-* feed:="wow-prices" {cf} | fields _time') is None
    assert cache.answer(f'feed:wow-* {cf} feed:="wow-recorder" | top 3 by (zone)') is None
    # The caller may change its rows; the cache's stay as they were.
    raw = cache.answer(f'feed:wow-* feed:="wow-recorder" {cf}')
    raw[0]["zone"] = "changed"
    assert {r["zone"] for r in cache.rows(CHAR)} == {"Duskwood", "Westfall"}


def test_a_full_answer_is_read_again_in_pages_not_halves():
    now = datetime.now(UTC)
    rows = [{"_time": stores.iso_us(now - timedelta(days=d, hours=1)), "n": str(d)} for d in range(3650)]
    calls = []

    def fetch(q, a, z, n):
        calls.append((q, n))
        return logsql.parse(q).run([r for r in rows if a <= parse_t(r["_time"]) <= z])[:n]

    got = paged_read(fetch, "*", HISTORY_START, now, 500)
    assert len(got) == 3650 and len({r["_time"] for r in got}) == 3650
    # the full answer, the hourly count, one page of VL_PAGE: not 2^k halves
    assert len(calls) == 3 and calls[1][0].endswith("| stats by (_time:1h) count() as n")


@pytest.mark.parametrize(("query", "want"), [
    # an inclusive end covers its whole second, as in VictoriaLogs v1.54
    ("_time:[2026-11-12T10:13:50Z, 2026-11-12T10:13:54Z] | fields src", [{"src": "b"}, {"src": "c"}]),
    ("_time:(2026-11-12T10:13:50Z, 2026-11-12T10:13:54Z) | fields src", [{"src": "b"}]),
    ("_time:>2026-11-12T10:13:54Z | fields src", [{"src": "c"}]),
    ("_time:<=2026-11-12T10:13:54Z | fields src", [{"src": "a"}, {"src": "b"}, {"src": "c"}]),
    ("kind:=xp | stats by (zone) sum(secs) as secs, count() n",
     [{"zone": "Elwynn", "secs": "6122.9", "n": "2"}]),
    ("* | stats by (_time:1m) count() as n", [{"_time": "2026-11-12T10:12:00Z", "n": "1"},
                                                {"_time": "2026-11-12T10:13:00Z", "n": "2"}]),
    ("* | stats row_max(_time, level, zone) as latest, uniq_values(zone) limit 3 as zones, "
     "count_uniq(kind) as kinds, max(level) as top",
     [{"latest": '{"level":"9"}', "zones": '["Elwynn"]', "kinds": "2", "top": "10"}]),
    ("kind:=xp | fields src, nothere", [{"src": "a"}, {"src": "b"}]),
    ("kind:in(xp, death) -zone:Elwynn | fields src", [{"src": "c"}]),
    ("(zone:* or level:*) kind:death | fields src", [{"src": "c"}]),
    ("* | sort by (_time desc) | limit 1 | fields src", [{"src": "c"}]),
    ("kind:=xp | uniq by (zone)", [{"zone": "Elwynn"}]),
])
def test_logsql_answers_like_victorialogs(query, want):
    """Shapes checked against VictoriaLogs v1.54.0 with the same rows."""
    rows = [{"_time": "2026-11-12T10:12:59.9Z", "kind": "xp", "zone": "Elwynn", "secs": "6000.5", "level": "10",
             "src": "a"},
            {"_time": "2026-11-12T10:13:51Z", "kind": "xp", "zone": "Elwynn", "secs": "122.4", "src": "b"},
            {"_time": "2026-11-12T10:13:54.7Z", "kind": "death", "level": "9", "src": "c"}]
    got = logsql.parse(query).run(rows)
    key = lambda r: json.dumps(r, sort_keys=True)  # noqa: E731
    assert sorted(got, key=key) == sorted(want, key=key)


def test_queries_outside_the_subset_go_to_victorialogs():
    for q in ("error", 'zone:~"Ell"', "* | top 3 by (zone)", "* | stats avg(secs)", "{a=~\"b\"} x:1",
              "{a!=\"b\"}"):
        with pytest.raises(logsql.Unsupported):
            logsql.parse(q)


def test_every_request_holds_the_gate(monkeypatch):
    held = []

    def http_get(url, params=None, *a, **k):
        held.append(stores.VL_GATE._value)
        return b""

    monkeypatch.setattr(stores, "http_get", http_get)
    free = stores.VL_GATE._value
    s = stores.Store("http://vl.invalid")
    s.vl_raw(s.vl_url, "*", HISTORY_START, datetime.now(UTC))
    assert held == [free - 1]  # one slot taken while the request runs
    assert stores.VL_GATE._value == free


def test_the_cache_is_per_tenant(monkeypatch):
    """Two people with a character of the same name on one VictoriaLogs
    (wowstats.tenancy): each one's cache holds only their tenant's rows."""
    from wowstats.analysis import prep

    mine, theirs = events(), events()[:300]
    fakes = {"0": FakeVL(mine), "7": FakeVL(theirs)}

    def by_tenant(url, params=None, *a, headers=None, **k):
        return fakes[(headers or {}).get("AccountID", "missing")](url, params)

    monkeypatch.setattr(stores, "http_get", by_tenant)
    monkeypatch.setattr(eventcache, "_SHARED", {})
    a = stores.Store("http://vl.invalid", "", "", "feed:wow-*", 89, profile_vm_url="", account_id=0)
    b = stores.Store("http://vl.invalid", "", "", "feed:wow-*", 89, profile_vm_url="", account_id=7)
    assert a.cache is not b.cache
    got_a, got_b = prep.query(a, a.stream, CHAR), prep.query(b, b.stream, CHAR)
    assert len(got_b) < len(got_a)
    key = lambda r: (r.get("_time"), r.get("feed"), r.get("source_id"))  # noqa: E731
    assert {key(r) for r in got_b} <= {key(r) for r in theirs}


def test_an_overfull_minute_is_read_by_the_second():
    """Review 4, G: a minute over a page was read once, cut at the page, and
    kept that way (page 3, 12 events in one minute: 4 rows, every time)."""
    t0 = datetime(2026, 11, 12, 10, tzinfo=UTC)
    rows = [_ev(t0 + timedelta(seconds=i * 4), f"r{i}", kind="pos") for i in range(12)]
    st = SlowStore(rows)
    cache = eventcache.EventCache(st, page=3, fresh_s=0)
    for _ in range(2):
        got = cache.rows(CHAR)
        assert sorted(r["source_id"] for r in got) == sorted(f"r{i}" for i in range(12))


def test_more_same_second_events_than_a_page_is_an_explicit_failure():
    """An irreducible bucket (more records in one second than a page) can't
    be read whole: the cache says so, never hands back part of it."""
    t0 = datetime(2026, 11, 12, 10, tzinfo=UTC)
    rows = [_ev(t0 + timedelta(seconds=30), f"same{i}", kind="pos") for i in range(12)]
    rows.append(_ev(t0 + timedelta(minutes=5), "other", kind="death"))
    st = SlowStore(rows)
    cache = eventcache.EventCache(st, page=3, fresh_s=0)
    for _ in range(2):
        with pytest.raises(stores.IncompleteRead) as err:
            cache.rows(CHAR)
        assert "2026-11-12T10:00:30Z" in str(err.value)
    assert isinstance(err.value, stores.SourceError)  # every caller already surfaces those
    # What a page or an AI client is told names the gap, not "try again".
    assert "partial" in stores.public_error(err.value)
    # Once the bucket fits (retention, a fix upstream), the reads are whole again.
    rows[:] = [r for r in rows if r["source_id"] in ("same0", "same1", "other")]
    assert {r["source_id"] for r in cache.rows(CHAR)} == {"same0", "same1", "other"}


def test_events_that_arrive_after_the_count_are_read_whole():
    """The page planned from the count comes back over full (late events
    landed in between): read finer, never keep the cut page."""
    t0 = datetime(2026, 11, 12, 10, tzinfo=UTC)
    rows = [_ev(t0 + timedelta(seconds=i * 7), f"r{i}", kind="pos") for i in range(3)]
    st = SlowStore(rows)
    late = [_ev(t0 + timedelta(seconds=i * 7 + 1), f"late{i}", kind="pos") for i in range(5)]
    hist = st.vl_direct

    def counted_then_more(*a, **k):
        out = hist(*a, **k)
        if not any(r["source_id"].startswith("late") for r in rows):
            rows.extend(late)
        return out

    st.vl_direct = counted_then_more
    cache = eventcache.EventCache(st, page=3, fresh_s=0)
    assert len(cache.rows(CHAR)) == 8


def test_paged_read_says_when_it_cannot_split_further():
    t = datetime(2026, 11, 12, 10, tzinfo=UTC)
    rows = [{"_time": stores.iso_us(t), "n": str(i)} for i in range(10)]

    def fetch(q, a, z, n):
        return logsql.parse(q).run([r for r in rows if a <= parse_t(r["_time"]) <= z])[:n]

    with pytest.raises(stores.IncompleteRead):
        paged_read(fetch, "* | sort by (n)", t - timedelta(seconds=1), t + timedelta(seconds=1), 4)
