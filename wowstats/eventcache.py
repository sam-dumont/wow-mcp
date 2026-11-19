"""One character's events, read once and kept in the process.

On 2026-11-18 one load of the full view OOM-killed the events' VictoriaLogs (1 GiB): the page
asks for its overview, gear, people, loot and plan at once, and each of
those ran 4 to 40 LogsQL queries over the character's whole history, 130 in
all, plus the plan's read of every event, which the old range splitting
read ten times over (243k rows for 23k events). Now:

- Every query scoped to one character (`char:=` or `char:in()` of one
  character's spellings) is answered from this cache by wowstats.logsql;
  only the rest (the character list, prices, the roster) still goes to
  VictoriaLogs.
- A character's events are read in pages of at most VL_PAGE rows, planned
  from an hourly count, so no request asks for more and none is repeated.
- After that, only what changed: the shipper sends a session's events
  after the fact (`_time` is when they happened, not when they arrived), so
  a time cursor would miss late ones. The cursor is the hourly count
  instead: each refresh asks VictoriaLogs for it (one small stats query)
  and re-reads only the hours whose count moved.
- One refresh at a time per character (single-flight): the page's
  parallel requests wait for the same read. A refresh is reused for
  FRESH_S seconds.
- Every VictoriaLogs request of the process goes through one gate,
  stores.VL_GATE (VL_CONCURRENCY, 2 by default).

Left out of the cache: the auction scans (wow-prices, not a character's
history) and raw combat log lines (wow-combatlog). A query that could reach
them goes to VictoriaLogs.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from . import logsql
from .stores import HISTORY_START, hour_pages, lq, num, parse_t

EXCLUDED = ("wow-prices", "wow-combatlog")
FRESH_S = 30
MAX_CHARS = 8
HOUR = 3600
# The hourly count of a character: one row per hour with events (a year of
# play every hour is under 9,000).
HIST_LIMIT = 100_000
# Fields that repeat across rows: one string each in memory.
_INTERN_MAX = 40
_NO_INTERN = frozenset({"_time", "_msg", "source_id", "items", "item_names", "talents", "professions", "wanted"})
_DROP = frozenset({"_stream", "_stream_id"})


def norm_char(c: str) -> str:
    return c.strip().replace(" ", "-")


def spellings(char: str) -> list[str]:
    """Polestar writes `Name Realm`, the WTF folder `Name-Realm`: one character."""
    c = norm_char(char)
    return sorted({c, c.replace("-", " ")})


@dataclass
class _Entry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    counts: dict[int, int] = field(default_factory=dict)  # hour (epoch s) -> rows held
    hours: dict[int, list] = field(default_factory=dict)
    rows: tuple = ()
    at: float = 0.0  # time.monotonic() of the last refresh


class EventCache:
    def __init__(self, store, page: int | None = None, fresh_s: float = FRESH_S, max_chars: int = MAX_CHARS):
        from .stores import VL_PAGE

        self.store = store
        self.page = page or VL_PAGE
        self.fresh_s = fresh_s
        self.max_chars = max_chars
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._guard = threading.Lock()
        self.reads = 0  # VictoriaLogs requests made for the cache (tests, the log)

    # ------------------------------------------------------------ queries

    def answer(self, query: str) -> list[dict] | None:
        """The rows VictoriaLogs would give for `query`, from the cache; None
        when the query isn't one character's or isn't in the subset."""
        stream = self.store.stream
        if not query.startswith(stream + " "):
            return None
        try:
            q = logsql.parse(query[len(stream) + 1:])
        except logsql.Unsupported:
            return None
        char = scope(q.where)
        if char is None:
            return None
        return q.run(self.rows(char))

    # ------------------------------------------------------------ the rows

    def rows(self, char: str) -> tuple:
        """Every cached event of the character (both spellings), refreshed
        when older than fresh_s."""
        char = norm_char(char)
        with self._guard:
            e = self._entries.get(char)
            if e is None:
                e = self._entries[char] = _Entry()
            self._entries.move_to_end(char)
            while len(self._entries) > self.max_chars:
                self._entries.popitem(last=False)
        with e.lock:  # single-flight: the others wait for this read
            if e.at and time.monotonic() - e.at < self.fresh_s:
                return e.rows
            self._refresh(char, e)
            e.at = time.monotonic()
            return e.rows

    def _base(self, char: str) -> str:
        cf = "char:in(" + ", ".join(lq(n) for n in spellings(char)) + ")"
        return f"{self.store.stream} {cf} " + " ".join(f"-feed:{f}" for f in EXCLUDED)

    def _refresh(self, char: str, e: _Entry) -> None:
        base = self._base(char)
        hist = self._hist(base, HISTORY_START, datetime.now(UTC), "1h")
        changed = sorted(h for h in set(hist) | set(e.counts) if hist.get(h, 0) != e.counts.get(h, 0))
        if not changed:
            return
        for h in changed:
            if not hist.get(h):  # gone (retention): nothing to read
                e.counts.pop(h, None)
                e.hours.pop(h, None)
        todo = [h for h in changed if hist.get(h)]
        for lo, hi, n in hour_pages(todo, hist, HOUR, self.page):
            if n > self.page:  # one hour over a page: by the minute
                e.hours.pop(lo, None)
                sub = self._hist(base, _dt(lo), _dt(hi) - timedelta(microseconds=1), "1m")
                for lo2, hi2, _ in hour_pages(sorted(sub), sub, 60, self.page):
                    self._read(base, lo2, hi2, e)
            else:
                self._read(base, lo, hi, e)
        self._snapshot(e)

    def _hist(self, base: str, start: datetime, end: datetime, step: str) -> dict[int, int]:
        self.reads += 1
        rows = self.store.vl_direct(f"{base} | stats by (_time:{step}) count() as n", ("_time",), {"n": "sum"},
                                    limit=HIST_LIMIT, start=start, end=end)
        return {int(parse_t(r.get("_time")).timestamp()): int(num(r.get("n"))) for r in rows if r.get("_time")}

    def _read(self, base: str, lo: int, hi: int, e: _Entry) -> None:
        """Every row in [lo, hi) seconds, in one request of at most `page` rows."""
        self.reads += 1
        got = self.store.vl_range(base, _dt(lo), _dt(hi) - timedelta(microseconds=1), self.page + 1)
        full = len(got) > self.page  # more than planned arrived since the count: read again next time
        fresh: dict[int, list] = {h: [] for h in range(lo - lo % HOUR, hi, HOUR)}
        for raw in got:
            r = {sys.intern(k): (sys.intern(v) if k not in _NO_INTERN and isinstance(v, str) and len(v) <= _INTERN_MAX
                                 else v) for k, v in raw.items() if k not in _DROP}
            t = int(parse_t(r.get("_time")).timestamp())
            fresh.setdefault(t - t % HOUR, []).append(r)
        for h, rs in fresh.items():
            if lo <= h and h + HOUR <= hi:  # whole hours read: replace them
                e.hours[h] = rs
            else:  # part of an hour (a minute-planned one): add to what the other parts brought
                keep = [r for r in e.hours.get(h, []) if not lo <= parse_t(r.get("_time")).timestamp() < hi]
                e.hours[h] = keep + rs
            e.counts[h] = -1 if full else len(e.hours[h])
            if not e.hours[h]:
                e.hours.pop(h)
                e.counts.pop(h)

    @staticmethod
    def _snapshot(e: _Entry) -> None:
        e.rows = tuple(r for h in sorted(e.hours) for r in e.hours[h])

    def expire(self) -> None:
        """The next read of each character asks VictoriaLogs what changed,
        however recent the last one (wowstats.prebuilt: a build after new
        data must not reuse rows read just before they arrived)."""
        with self._guard:
            for e in self._entries.values():
                e.at = 0.0

    def stats(self) -> dict:
        with self._guard:
            return {c: len(e.rows) for c, e in self._entries.items()}


def _dt(s: int) -> datetime:
    return datetime.fromtimestamp(s, UTC)


def scope(where) -> str | None:
    """The one character a filter is limited to, when it also keeps out the
    feeds the cache doesn't hold; else None."""
    terms = logsql.top_terms(where)
    names = None
    for t in terms:
        if isinstance(t, logsql.Term) and t.field == "char" and t.op in ("eq", "in"):
            names = {norm_char(v) for v in t.values}
            break
    if not names or len(names) != 1:
        return None
    ok = False
    negated = set()
    for t in terms:
        if isinstance(t, logsql.Term) and t.field == "feed" and t.op in ("eq", "in", "word") \
                and all(v.startswith("wow-") and v not in EXCLUDED for v in t.values):
            ok = True
        if isinstance(t, logsql.Not) and isinstance(t.x, logsql.Term) and t.x.field == "feed" \
                and t.x.op in ("eq", "word"):
            negated.add(t.x.values[0])
    if ok or set(EXCLUDED) <= negated:
        return names.pop()
    return None


_SHARED: dict[tuple, EventCache] = {}
_SHARED_LOCK = threading.Lock()


def shared(store) -> EventCache:
    """One cache per set of stores in the process: the UI's Store, the
    planner's and the MCP's Views all read the same one. Per person too
    (wowstats.tenancy): each tenant's events are its own cache, so one
    person's answers never come from another's rows."""
    key = (store.vl_url, store.vl_archive_url, store.stream, store.hot_days, getattr(store, "account_id", 0))
    with _SHARED_LOCK:
        c = _SHARED.get(key)
        if c is None:
            c = _SHARED[key] = EventCache(store)
        return c
