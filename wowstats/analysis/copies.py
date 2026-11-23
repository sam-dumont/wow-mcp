"""One analytical copy per character: the semantic layer's DuckDB copy of the
character's prep, shared by everything in the process that reads a metric.

Before this, each reader built its own copy of the same character: the MCP
tools' (8 kept), the pages' narrow one (4 kept), the planner's (one per
plan, plus a module global for its levels) and the gear answer's (one per
call). On the demo (25,655 events) each build took the process up about
350 MB that the allocator never gave back, and three characters asked
through the page and the MCP held up to seven of them: 2.4 GB after four
uploads, against the compose kit's 1 GiB (measured on 0.8.0).

Now one manager holds them:
- one Copy per (stores, tenant, character), built from every event the
  analysis reads (prep.query, the event cache) with the KB's map sizes and
  no gear advice;
- replaced when the events changed (`probe`: the prep's version, the number
  of events and the newest one's time; the event cache answers both from
  memory), the new one swapped in before the old closes when the budget has
  room for both, else the old one closed first;
- at most `cap()` open (wowstats.analysis.budget: from the container's
  memory limit; ANALYSIS_COPIES_MAX), the least recently used closed past
  it;
- one build at a time in the process: a build's transient (the rows as
  JSON for DuckDB to parse) is the biggest thing the process allocates,
  and two at once doubled the high-water mark. Each build ends with
  budget.trim(), so the heap gives that transient back;
- a request (`holding`: tools.call, the page's request, the page's own
  computation) keeps the copies it read open until it ends: a copy evicted
  meanwhile closes when its last holder lets go;
- gear advice is scored in the copy itself (`scored`): the advice's worn
  items and upgrades replace the copy's `gear` and `upgrade_candidates`
  rows for the time of one reader, under the copy's lock, and its own rows
  go back after. No second copy per gear answer.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import os
import threading
import time
import warnings
from collections import OrderedDict

from wowstats.stores import SemanticError

# The tables gear advice is scored into (prep.build's `advice`): the rest of
# the copy never depends on the advice.
SCORED = ("gear", "upgrade_candidates")


class Closed(SemanticError):
    """A copy was closed under a reader that didn't hold it (`holding`)."""


class Copy:
    """One character's DuckDB copy and its semantic layer."""

    def __init__(self, key: tuple, probe: tuple, con, as_of: str | None):
        from . import duck, semantic

        self.key, self.probe, self.con, self.as_of = key, probe, con, as_of
        try:
            semantic.prepare(con)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # BSL's own deprecation notes
                self.layer = semantic.Layer(con)
        except Exception:
            con.close()
            raise
        self.ro = duck.ReadOnlyDuck(con)
        self.lock = threading.RLock()
        self.users = 0
        self.closed = False
        self.used = time.monotonic()
        # The copy's own rows of the scored tables, put back after each `scored`.
        self._own = {t: con.execute(f'SELECT * FROM "{t}"').fetchall() for t in SCORED}

    @property
    def sem(self) -> tuple:
        """(layer, read-only runner): what semantic.run and the planner take."""
        return self.layer, self.ro

    def _check(self) -> None:
        if self.closed:
            raise Closed(f"the copy of {self.key[-1]} was closed: ask again")

    def run(self, q: dict, max_rows: int | None = None) -> dict:
        """semantic.run on this copy, one question at a time."""
        from . import semantic

        with self.lock:
            self._check()
            self.used = time.monotonic()
            return semantic.run(self.layer, self.ro, q, *(() if max_rows is None else (max_rows,)))

    def rows(self, q: dict) -> list[dict]:
        from . import semantic

        with self.lock:
            self._check()
            return semantic.rows(self.layer, self.ro, q)

    def execute(self, sql: str):
        """A plain read of the copy's own (the planner's `meta` header), as
        sqlite3's execute: a cursor to fetch from."""
        with self.lock:
            self._check()
            return self.con.cursor().execute(sql)

    @contextlib.contextmanager
    def scored(self, advice: dict | None, char: str, upgrades: list[dict] | None = None, worn: str = "own"):
        """The copy with this gear advice scored in, for the time of the
        block: `upgrades` (the advice's `upgrades_all` when None) as
        upgrade_candidates, and the worn items scored for the advice's spec.
        `worn`: "own" keeps the copy's worn items (the events' sheet, as
        prep.build scores them), "advice" takes the advice's (the MCP's gear
        answer: the live sheet, Battle.net's items). The copy's lock is held
        throughout: no other reader sees the advice."""
        from . import duck, prep

        ups = (advice or {}).get("upgrades_all") or [] if upgrades is None else upgrades
        with self.lock:
            self._check()
            own = None
            if worn == "own":
                cols = [r[0] for r in self.con.execute('DESCRIBE "gear"').fetchall()]
                own = [dict(zip(cols, r, strict=True)) for r in self._own["gear"]]
            stage = prep.advice_db(advice or {}, char, (advice or {}).get("class"), ups, worn=own)
            try:
                for t in SCORED:
                    cols = [r[0] for r in self.con.execute(f'DESCRIBE "{t}"').fetchall()]
                    got = stage.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}"')
                    duck.replace(self.con, t, got.fetchall())
            finally:
                stage.close()
            try:
                yield self.sem
            finally:
                for t in SCORED:
                    duck.replace(self.con, t, self._own[t])

    def close(self) -> None:
        """Close now (waits for a question running on it)."""
        with self.lock:
            if not self.closed:
                self.closed = True
                self.con.close()


_LOCK = threading.Lock()
_BUILD = threading.Lock()  # one build at a time in the process
_COPIES: OrderedDict[tuple, Copy] = OrderedDict()
_KEY_LOCKS: dict[tuple, threading.Lock] = {}
_HELD: contextvars.ContextVar[list | None] = contextvars.ContextVar("copies_held", default=None)
_RETIRED: list[Copy] = []  # evicted while held: closed when the last holder lets go
STATS = {"builds": 0, "closed": 0}


def cap(env: dict | None = None) -> int:
    """How many copies stay open: ANALYSIS_COPIES_MAX, else the budget's."""
    from .budget import current

    raw = str((os.environ if env is None else env).get("ANALYSIS_COPIES_MAX") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            raise ValueError(f"ANALYSIS_COPIES_MAX={raw!r}: a whole number") from None
    return current()["copies"]


def key_of(store, char: str) -> tuple:
    """The copy's key: the stores and the person (wowstats.tenancy), the character."""
    from . import prep

    return (getattr(store, "vl_url", None), getattr(store, "vl_archive_url", None), getattr(store, "stream", None)
            or getattr(store, "logs_filter", None), getattr(store, "account_id", 0) or 0, prep.norm(char))


def probe_of(rows: list[dict]) -> tuple:
    """What says the events changed: the prep's version, how many, the newest,
    and a digest of each one's id, revision and time (a late event, a
    retention cut and a new revision all move it; 25k events in about 10 ms)."""
    import hashlib

    from . import prep

    h = hashlib.blake2b(digest_size=16)
    for r in rows:
        h.update(f"{r.get('source_id')}|{r.get('rev')}|{r.get('_time')}|{r.get('char')}\n".encode())
    return prep.PREP_VERSION, len(rows), max((str(r.get("_time") or "") for r in rows), default=""), h.hexdigest()


@functools.cache
def map_sizes() -> dict:
    """The KB's map frames (kb.packaged's ui_map), as the analysis builds with."""
    from . import kb

    return kb.packaged()[1].get("ui_map") or {}


def of(store, char: str, rows: list[dict] | None = None) -> Copy | None:
    """The character's copy, built when there is none or its events changed
    (None when the character has no events). `rows`: prep.query's answer,
    when the caller already read it."""
    from wowstats import eventcache

    from . import prep

    key = key_of(store, char)
    if rows is None:
        with eventcache.reading() as seen:
            rows = prep.query(store, store.stream, key[-1])
        eventcache.replay(seen)
        as_of = eventcache.as_of(seen, key[-1])
    else:
        as_of = None
    if not rows:
        return None
    probe = probe_of(rows)
    with _LOCK:
        cp = _COPIES.get(key)
        if cp is not None and cp.probe == probe and not cp.closed:
            return _hand_out(key, cp)
        klock = _KEY_LOCKS.setdefault(key, threading.Lock())
    with klock:  # one build per character: the page's parallel requests wait for it
        with _LOCK:
            cp = _COPIES.get(key)
            if cp is not None and cp.probe == probe and not cp.closed:
                return _hand_out(key, cp)
            old = _COPIES.get(key)
            # Room for the new one beside the old one? Else the old one goes first.
            if old is not None and len(_COPIES) + 1 > cap():
                _evict(key)
                old = None
        new = _build(key, probe, rows, as_of)
        with _LOCK:
            if key in _COPIES:
                _evict(key)
            _COPIES[key] = new
            out = _hand_out(key, new)
            over = [k for k in _COPIES if k != key][:max(0, len(_COPIES) - cap())]
            for k in over:
                _evict(k)
        return out


def build(rows: list[dict], char: str, key: tuple | None = None, probe: tuple | None = None,
          as_of: str | None = None) -> Copy:
    """A copy of these events, as `of` builds one (one build at a time in the
    process), outside the manager when called directly (tests)."""
    from wowstats.mcp import route

    from . import TABLES, budget, duck, prep

    with _BUILD:
        db = prep.build(rows, char, route.reference(), map_sizes=map_sizes())
        try:
            con = duck.mirror(db, TABLES)
        finally:
            db.close()
        try:
            cp = Copy(key or ("built", prep.norm(char)), probe_of(rows) if probe is None else probe, con, as_of)
        finally:
            budget.trim()
        STATS["builds"] += 1
        return cp


def _build(key: tuple, probe: tuple, rows: list[dict], as_of: str | None) -> Copy:
    return build(rows, key[-1], key, probe, as_of)


def _hand_out(key: tuple, cp: Copy) -> Copy:
    """(under _LOCK) Most recently used, and held by the current request."""
    _COPIES.move_to_end(key)
    cp.used = time.monotonic()
    held = _HELD.get()
    if held is not None and not any(x is cp for x in held):
        held.append(cp)
        cp.users += 1
    return cp


def _evict(key: tuple) -> None:
    """(under _LOCK) Out of the map; closed now unless a request holds it."""
    cp = _COPIES.pop(key, None)
    if cp is None:
        return
    if cp.users > 0:
        _RETIRED.append(cp)
    else:
        _close(cp)


def _close(cp: Copy) -> None:
    cp.close()
    STATS["closed"] += 1


@contextlib.contextmanager
def holding():
    """One request's scope: every copy it reads stays open until it ends.
    Nested scopes share the outer one."""
    if _HELD.get() is not None:
        yield
        return
    held: list[Copy] = []
    token = _HELD.set(held)
    try:
        yield
    finally:
        _HELD.reset(token)
        with _LOCK:
            for cp in held:
                cp.users -= 1
            done = [cp for cp in _RETIRED if cp.users <= 0]
            _RETIRED[:] = [cp for cp in _RETIRED if cp.users > 0]
        for cp in done:
            _close(cp)


def live() -> list[Copy]:
    """Every copy still open: the kept ones and the retired ones a request holds."""
    with _LOCK:
        return [cp for cp in [*_COPIES.values(), *_RETIRED] if not cp.closed]


def stats() -> dict:
    """For /healthz: how many copies are open, the cap, the builds so far."""
    return {"analysis_copies": len(live()), "analysis_copies_max": cap(), "analysis_copy_builds": STATS["builds"]}


def clear() -> None:
    """Close every copy (tests; a process reset)."""
    with _LOCK:
        gone = [*_COPIES.values(), *_RETIRED]
        _COPIES.clear()
        _RETIRED.clear()
    for cp in gone:
        cp.close()


_EMPTY: list[Copy] = []


def empty() -> Copy:
    """A copy of an empty prep, built once and never evicted: what the page
    reads for a character with no events (every metric NULL, never 0)."""
    from wowstats.mcp import route

    from . import TABLES, duck, prep

    with _LOCK:
        if not _EMPTY:
            db = prep.build([], "", route.reference())
            try:
                _EMPTY.append(Copy(("empty",), (), duck.mirror(db, TABLES), None))
            finally:
                db.close()
        return _EMPTY[0]


@contextlib.contextmanager
def temporary(db):
    """A copy of a prep build of the caller's own (no events of a character:
    the gear answer of a character the stores never saw), closed after."""
    from . import TABLES, duck

    con = duck.mirror(db, TABLES)
    cp = Copy(("temporary",), (), con, None)
    try:
        yield cp
    finally:
        cp.close()
