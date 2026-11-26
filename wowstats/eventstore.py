"""Where the game events live: VictoriaLogs (the default) or one SQLite file
per person. Every read, write, deletion and health check of the events goes
through here; nothing else in the app speaks to VictoriaLogs' HTTP API
(tests/test_eventstore.py fails on a stray one).

EVENTS_STORE picks it (events_location):

- `vl` (the default): VictoriaLogs at VL_URL, each person a tenant
  (AccountID, stores.tenant_headers), older days optionally in a second
  VictoriaLogs (VL_ARCHIVE_URL), friends on FRIENDS_VL_URL.
- `sqlite:/data/events`: a folder with one file per person,
  events-<account id>.sqlite. A query is answered by wowstats.logsql over
  the rows the file's index narrows it to (character, feed, time); there is
  no VictoriaLogs to fall back on, so the app's queries all stay inside the
  subset (tests/test_eventstore.py, the logsql conformance and the slices
  run on this store too). No archive, no VictoriaLogs UI or Grafana; a
  backup is a copy of the folder with the app stopped.

The rows read back are shaped like VictoriaLogs' answers, from either:
every value a string, empty values left out, `_time` in RFC 3339 UTC.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from . import stores
from .stores import QUERY_TIMEOUT, SourceError, iso_us, parse_t, tenant_headers

DEFAULT_VL_URL = "http://victorialogs:9428"
SQLITE = "sqlite:"


class Unanswerable(SourceError):
    """A query outside what the SQLite store answers (wowstats.logsql's
    subset). A SourceError: an app query that hits it fails like a store
    that didn't answer, never with a guess."""


def events_location(env: dict) -> str:
    """The events' store from the settings: VL_URL, or `sqlite:<folder>`
    with EVENTS_STORE=sqlite:<folder>."""
    choice = (env.get("EVENTS_STORE") or "vl").strip()
    if choice == "vl":
        return (env.get("VL_URL") or DEFAULT_VL_URL).strip().rstrip("/")
    if choice.startswith(SQLITE) and len(choice) > len(SQLITE):
        return SQLITE + choice[len(SQLITE):].rstrip("/")
    raise ValueError(f"EVENTS_STORE={choice!r}: `vl` (VictoriaLogs at VL_URL) or sqlite:<folder>, "
                     "e.g. sqlite:/data/events")


def is_sqlite(location: str) -> bool:
    return location.startswith(SQLITE)


def describe(location: str) -> str:
    """For the log and the admin page: the folder, or VictoriaLogs' host and
    port (a user:password@ in the URL stays out)."""
    if is_sqlite(location):
        return f"SQLite files in {location[len(SQLITE):]}"
    u = urllib.parse.urlsplit(location)
    return f"VictoriaLogs at {u.hostname}{f':{u.port}' if u.port else ''}"


_OPEN: dict[str, object] = {}
_OPEN_LOCK = threading.Lock()

# The write generation of each (store, tenant): moved by every write this
# process makes (append, delete), after the rows are readable. Every cache
# of what the events say keeps the generation it read at and is out of date
# once it moved (wowstats.eventcache, ui.App's answers, wowstats.prebuilt,
# the MCP's character list, the plan's inputs). Testing #239, a character
# ingested for the first time was "no events for X" until those caches'
# clocks ran out. Writes from another process (the demo seeded straight to
# VictoriaLogs) don't move it: the page's minute check (prebuilt) still
# finds those.
_GEN: dict[tuple[str, int], int] = {}
_GEN_LOCK = threading.Lock()


def changed(location: str, tenant: int) -> None:
    """Invalidate local readers after a write or an observed external change."""
    key = (location.rstrip("/"), int(tenant))
    with _GEN_LOCK:
        _GEN[key] = _GEN.get(key, 0) + 1


def generation(location: str, tenant: int) -> int:
    """How many writes this process made to `location` for `tenant`."""
    return _GEN.get((location.rstrip("/"), int(tenant)), 0)


def generation_of(store) -> int:
    """The write generation of a store object (wowstats.stores.Store, the
    MCP's Store): its events' location and tenant. 0 for one without them
    (a test's fake)."""
    location = getattr(store, "vl_url", None)
    if not isinstance(location, str):
        return 0
    return generation(location, getattr(store, "account_id", 0) or 0)


def get(location: str):
    """The store at `location` (an http(s) URL or sqlite:<folder>), one per
    process."""
    location = location.rstrip("/")
    with _OPEN_LOCK:
        s = _OPEN.get(location)
        if s is None:
            s = _OPEN[location] = SQLiteEvents(location[len(SQLITE):]) if is_sqlite(location) \
                else VictoriaLogs(location)
        return s


# ---------------------------------------------------------------- VictoriaLogs


class VictoriaLogs:
    """VictoriaLogs' HTTP API, in the caller's tenant."""

    kind = "vl"
    archive = True  # VL_ARCHIVE_URL may name a second one for older days

    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def query(self, tenant: int, q: str, start: datetime, end: datetime, limit: int) -> list[dict]:
        """One request, through the process' gate (stores.VL_GATE)."""
        from .stores import VL_GATE

        with VL_GATE:
            body = stores.http_get(f"{self.url}/select/logsql/query", {
                "query": q, "start": iso_us(start), "end": iso_us(end), "limit": str(limit),
            }, headers=tenant_headers(tenant))
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    def append(self, tenant: int, lines: list[bytes]) -> None:
        """Screened NDJSON lines (wowstats.ingest) to the jsonline API, which
        answers 200 and stores nothing unless the content type is
        application/stream+json."""
        from .ingest import STREAM_FIELDS

        q = urllib.parse.urlencode({"_stream_fields": STREAM_FIELDS, "_time_field": "_time", "_msg_field": "_msg"})
        req = urllib.request.Request(f"{self.url}/insert/jsonline?{q}", data=b"\n".join(lines) + b"\n",
                                     method="POST", headers={"Content-Type": "application/stream+json",
                                                             **tenant_headers(tenant)})
        at = describe(self.url)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
        except urllib.error.HTTPError as e:
            raise SourceError(f"{at} answered {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise SourceError(f"{at} unreachable: {e}") from None
        self.flush(tenant)
        changed(self.url, tenant)

    def flush(self, tenant: int = 0) -> None:
        """Make what was just stored searchable now: VictoriaLogs holds new
        rows in memory for up to a few seconds first, and a read in between
        (the page right after /ingest, its write generation moved) would
        cache the answer without them. Best effort: a VictoriaLogs that
        refuses it (-forceFlushAuthKey) is only that late."""
        try:
            # A POST (v1.54.0 answers a GET 405, so the flush silently didn't
            # happen: #240's logsql job), in the caller's tenant like every
            # request here (it flushes every tenant's rows all the same).
            req = urllib.request.Request(f"{self.url}/internal/force_flush", data=b"", method="POST",
                                         headers=tenant_headers(tenant))
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        except (urllib.error.URLError, OSError):
            pass

    def delete(self, tenant: int, auth_key: str = "", before: datetime | None = None) -> dict:
        """Every event of the tenant, or only those older than `before`: a
        delete task (VictoriaLogs needs -delete.enable, and -deleteAuthKey
        when `auth_key` is given)."""
        flt = "*" if before is None else f"_time:<{iso_us(before)}"
        body = urllib.parse.urlencode({"filter": flt, **({"authKey": auth_key} if auth_key else {})}).encode()
        req = urllib.request.Request(f"{self.url}/delete/run_task", data=body, method="POST",
                                     headers={**tenant_headers(tenant),
                                              "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise SourceError(f"VictoriaLogs refused the delete: {e.code} "
                              f"{e.read(200).decode('utf-8', 'replace')}") from None
        changed(self.url, tenant)
        return out

    def health(self, timeout: float = 5) -> dict:
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=timeout) as r:  # noqa: S310 - our own env's
                r.read(4096)
            return {"ok": True, "ms": round((time.monotonic() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001 - any failure is the answer
            return {"ok": False, "ms": round((time.monotonic() - t0) * 1000), "error": type(e).__name__}


# ---------------------------------------------------------------- SQLite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    t_us INTEGER NOT NULL,
    char TEXT,
    feed TEXT,
    source_id TEXT,
    line_key BLOB NOT NULL UNIQUE,
    row TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_char_feed_time ON events (char, feed, t_us);
CREATE INDEX IF NOT EXISTS events_time ON events (t_us);
"""


def stored(ev: dict, now: datetime | None = None) -> dict:
    """An event as VictoriaLogs stores and answers it: every value a string
    (numbers in the text they were sent as, booleans true/false, nested
    values as compact JSON), empty and null values left out, `_time` in
    RFC 3339 UTC."""
    from .logsql import fmt_time

    out: dict[str, str] = {}
    for k, v in ev.items():
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, (int, float)):
            v = json.dumps(v)
        elif isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        out[k] = str(v)
    t = parse_t(out.get("_time"))
    if t.year < 2:
        t = now or datetime.now(UTC)
    out["_time"] = fmt_time(t if t.tzinfo else t.replace(tzinfo=UTC))
    return out


def _us(t: datetime) -> int:
    t = t if t.tzinfo else t.replace(tzinfo=UTC)
    return int((t - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1_000_000)


def _prefilter(where) -> tuple[list[str], list, int | None, int | None]:
    """SQL conditions that keep at least every row the filter can match:
    the positive top-level terms on the indexed columns (character, feed)
    and the absolute time bounds. wowstats.logsql then applies the whole
    filter to what's left."""
    from .logsql import Term, top_terms

    conds, args, lo, hi = [], [], None, None
    for t in top_terms(where):
        if not isinstance(t, Term):
            continue
        if t.field in ("char", "feed") and t.op in ("eq", "in"):
            conds.append(f"{t.field} IN ({', '.join('?' * len(t.values))})")
            args += list(t.values)
        elif t.field == "feed" and t.op == "prefix" and t.values[0]:
            # A range on the text: `wow-*` is every feed from "wow-" up to,
            # not including, "wow." ("-" plus one).
            p = t.values[0]
            conds.append("feed >= ? AND feed < ?")
            args += [p, p[:-1] + chr(ord(p[-1]) + 1)]
        elif t.op == "time" and t.rel_s is None:
            if t.lo is not None:
                lo = max(lo or -(2 ** 62), _us(t.lo))
            if t.hi is not None:
                hi = min(hi if hi is not None else 2 ** 62, _us(t.hi))
    return conds, args, lo, hi


class SQLiteEvents:
    """One SQLite file per person in `folder` (events-<account id>.sqlite),
    in WAL mode. The same line sent twice is stored once (the shipper
    resends a file whose answer it didn't get); a record sent again with a
    change (a growing level split, a higher `rev`) is a new line, kept like
    VictoriaLogs keeps it, and the readers' dedupe picks the newest."""

    kind = "sqlite"
    archive = False

    def __init__(self, folder: str):
        self.location = SQLITE + folder  # as named, the write generation's key (Store.vl_url)
        self.folder = Path(folder)
        self._made: set[Path] = set()
        self._lock = threading.Lock()

    def path(self, tenant: int) -> Path:
        return self.folder / f"events-{int(tenant)}.sqlite"

    @contextlib.contextmanager
    def _conn(self, tenant: int, create: bool):
        p = self.path(tenant)
        if not create and not p.exists():
            yield None
            return
        if create:
            self.folder.mkdir(parents=True, exist_ok=True)
        try:
            con = sqlite3.connect(p, timeout=QUERY_TIMEOUT, isolation_level=None)
        except sqlite3.Error as e:
            raise SourceError(f"{p}: {e}") from None
        try:
            if p not in self._made:
                with self._lock:
                    con.execute("PRAGMA journal_mode=WAL")
                    con.executescript(_SCHEMA)
                    self._made.add(p)
            con.execute("PRAGMA synchronous=NORMAL")
            yield con
        except sqlite3.Error as e:
            raise SourceError(f"{p}: {e}") from None
        finally:
            con.close()

    def append(self, tenant: int, lines: list[bytes] | list[dict]) -> int:
        """Screened lines (wowstats.ingest) or event dicts; how many were new."""
        now = datetime.now(UTC)
        rows = []
        for line in lines:
            ev = line if isinstance(line, dict) else json.loads(line, parse_int=str, parse_float=str,
                                                                  parse_constant=str)
            r = stored(ev, now)
            text = json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            rows.append((_us(parse_t(r["_time"])), r.get("char"), r.get("feed"), r.get("source_id"),
                         hashlib.blake2b(text.encode(), digest_size=16).digest(), text))
        if not rows:
            return 0
        with self._conn(tenant, create=True) as con:
            con.execute("BEGIN IMMEDIATE")
            before = con.total_changes
            con.executemany("INSERT OR IGNORE INTO events (t_us, char, feed, source_id, line_key, row) "
                            "VALUES (?, ?, ?, ?, ?, ?)", rows)
            con.execute("COMMIT")
            new = con.total_changes - before
        if new:
            changed(self.location, tenant)
        return new

    def query(self, tenant: int, q: str, start: datetime, end: datetime, limit: int) -> list[dict]:
        from . import logsql

        try:
            parsed = logsql.parse(q)
        except logsql.Unsupported as e:
            raise Unanswerable(f"LogsQL outside what the SQLite store answers ({e}): {q[:200]}") from None
        conds, args, lo, hi = _prefilter(parsed.where)
        lo = max(_us(start), lo) if lo is not None else _us(start)
        hi = min(_us(end), hi) if hi is not None else _us(end)
        if hi < lo:
            lo = hi + 1  # an empty range: no row, but a stats pipe still answers (count 0)
        rows: list[dict] = []
        with self._conn(tenant, create=False) as con:
            if con is not None:  # None: nothing stored yet for this person, an empty store
                sql = " AND ".join(["t_us >= ?", "t_us <= ?", *conds])
                cur = con.execute(f"SELECT row FROM events WHERE {sql} ORDER BY t_us, id", [lo, hi, *args])  # noqa: S608
                rows = [json.loads(r) for (r,) in cur]
        try:
            with logsql.lenient():
                out = parsed.run(rows)
        except logsql.Unsupported as e:
            raise Unanswerable(f"LogsQL outside what the SQLite store answers ({e}): {q[:200]}") from None
        return out[:max(0, int(limit))]

    def delete(self, tenant: int, auth_key: str = "", before: datetime | None = None) -> dict:
        """Every event of the person: their file, gone. With `before`, only
        the rows older than it."""
        if before is not None:
            with self._conn(tenant, create=False) as con:
                if con is None:
                    return {"deleted_rows": 0}
                cur = con.execute("DELETE FROM events WHERE t_us < ?", (_us(before),))
            changed(self.location, tenant)
            return {"deleted_rows": cur.rowcount}
        gone = []
        for suffix in ("", "-wal", "-shm"):
            p = Path(f"{self.path(tenant)}{suffix}")
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
                gone.append(p.name)
        self._made.discard(self.path(tenant))
        changed(self.location, tenant)
        return {"deleted_files": gone}

    def health(self, timeout: float = 5) -> dict:
        t0 = time.monotonic()
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            ok = os.access(self.folder, os.W_OK)
            with self._conn(0, create=False) as con:
                if con is not None:
                    con.execute("SELECT 1 FROM events LIMIT 1").fetchall()
            out = {"ok": ok, "ms": round((time.monotonic() - t0) * 1000)}
            return out if ok else {**out, "error": "read-only folder"}
        except Exception as e:  # noqa: BLE001 - any failure is the answer
            return {"ok": False, "ms": round((time.monotonic() - t0) * 1000), "error": type(e).__name__}
