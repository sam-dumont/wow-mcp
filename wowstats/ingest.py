"""The shipper's NDJSON, screened before it reaches VictoriaLogs.

The same rules as a Vector receiver in front of a shared store, so an event
that comes in through the app's own /ingest is stored the way one through
the receiver is:

- a line whose feed isn't `wow-<1-24 lowercase letters>` is refused;
- keys must match `^[a-z][a-z0-9_]{0,63}$`; names VictoriaLogs or the
  receiver give a meaning to, and anything that looks like a credential or
  the Battle.net account, are dropped;
- nested values become one JSON string field each;
- `component` is `wow`, set here and never taken from the payload, and it
  is a stream field with `feed` (the receiver's `site,component,feed` but
  the site, which says where such a receiver runs): a reader whose
  LOGS_FILTER is `{component="wow"}` sees these events too;
- an unparsable or impossible `_time` (before 2004-11-23, or more than a day
  ahead) is kept at the time it arrived, under feed `wow-invalid`, with
  `feed_sent`, `ingest_error` and `time_sent` saying what to fix;
- `_msg` is capped at 4096 characters (empty: "<feed> <char>");
- other players' data is cut to what a feature reads, for shippers before
  0.7.0 that still send raw lines (wowstats.otherplayers.allowed);
- a line whose `char` can't be a file name (wowstats.names.valid_char:
  letters, space, hyphen, apostrophe) is refused: the publisher writes
  each character's files under that name.

With several people each one has a daily budget of accepted bytes
(INGEST_DAILY_MB, 512; 0: none): past it /ingest answers 429 and the
shipper keeps its spool for the next day (security review L7).

At most INGEST_CONCURRENCY uploads (4) are read and stored at once, each
held whole in memory (8 MiB at most, a few copies while it is screened):
a logout wave of a dozen shippers would otherwise hold them all together.
One more waits up to INGEST_WAIT_S (10) for a slot, then gets 503 with
Retry-After. The shipper keeps every file that didn't get a 2xx in its
spool and sends it again on its next run, 5 minutes later.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from datetime import UTC, datetime, timedelta

from wowstats import otherplayers
from wowstats.names import valid_char

FEED_RE = re.compile(r"^wow-[a-z]{1,24}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DROP = frozenset({"site", "layer", "component", "hostname", "app_name", "timestamp", "message", "path",
                  "source_type", "host"})
SECRETISH = re.compile(r"(pass|token|secret|account|bnet|battle_?tag|email)")
MSG_MAX = 4096
COMPONENT = "wow"
STREAM_FIELDS = "component,feed"
EARLIEST = 1100000000  # 2004-11-09: nothing in the game is older


def _parse_time(raw) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def screen(ev, now: datetime | None = None) -> dict | None:
    """One event as it may be stored, or None when it isn't a wow-stats event."""
    if not isinstance(ev, dict):
        return None
    feed = ev.get("feed")
    if not isinstance(feed, str) or not FEED_RE.match(feed):
        return None
    if "char" in ev and not valid_char(ev["char"]):
        return None
    now = now or datetime.now(UTC)
    raw_time = ev.get("_time")
    msg = ev.get("_msg")
    if msg is not None and not isinstance(msg, str):
        msg = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    out: dict = {}
    for k, v in ev.items():
        if k.startswith("_") or not KEY_RE.match(k) or k in DROP or SECRETISH.search(k):
            continue
        out[k] = json.dumps(v, ensure_ascii=False, separators=(",", ":")) if isinstance(v, dict | list) else v
    out["component"] = COMPONENT
    t = _parse_time(raw_time)
    if t is None:
        bad = "unparsable _time"
    elif t.timestamp() < EARLIEST or t > now + timedelta(days=1):
        bad = "_time out of range"
    else:
        bad = ""
    if bad:
        out.update({"feed": "wow-invalid", "feed_sent": feed, "ingest_error": bad,
                    "time_sent": str(raw_time)[:64] if raw_time is not None else ""})
        out["_time"] = now.isoformat().replace("+00:00", "Z")
    else:
        out["_time"] = raw_time
    msg = msg or f"{feed} {out.get('char') if isinstance(out.get('char'), str) else '?'}"
    out["_msg"] = msg[:MSG_MAX]
    return out


def parse(body: bytes, now: datetime | None = None) -> tuple[list[bytes], int]:
    """(the lines to store, how many were refused). All or nothing is the
    caller's choice: the shipper keeps a file that got a 400."""
    lines, bad = [], 0
    now = now or datetime.now(UTC)
    for raw in body.splitlines():
        if not raw.strip():
            continue
        try:
            ev = screen(otherplayers.allowed(json.loads(raw)), now)  # security review H1
        except (ValueError, RecursionError):  # RecursionError: JSON nested too deep (a 500 before, review info 1)
            ev = None
        if ev is None:
            bad += 1
            continue
        lines.append(json.dumps(ev, ensure_ascii=False, separators=(",", ":")).encode())
    return lines, bad


def length(headers) -> int:
    raw = headers.get("Content-Length") or "0"
    return int(raw) if raw.isdigit() else 0


class Budget:
    """Accepted bytes per person per UTC day, in memory (a restart starts the
    day again: a ceiling against a flood, not an account of use)."""

    def __init__(self, daily_mb: str | float | None):
        try:
            mb = float(512 if daily_mb in (None, "") else daily_mb)
        except ValueError:
            raise SystemExit(f"INGEST_DAILY_MB={daily_mb!r}: a number of megabytes (0: no limit)") from None
        self.limit = int(mb * 1024 * 1024)
        self.used: dict[str, tuple[str, int]] = {}
        self.lock = threading.Lock()

    def _today(self, who: str) -> int:
        day = datetime.now(UTC).date().isoformat()
        d, n = self.used.get(who, (day, 0))
        return n if d == day else 0

    def allows(self, who: str, n: int) -> bool:
        with self.lock:
            return self.limit <= 0 or self._today(who) + n <= self.limit

    def spend(self, who: str, n: int) -> None:
        with self.lock:
            self.used[who] = (datetime.now(UTC).date().isoformat(), self._today(who) + n)


def _number(name: str, raw, default: float, low: float) -> float:
    try:
        v = float(default if raw in (None, "") else raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r}: a number") from None
    if v < low:
        raise ValueError(f"{name}={raw!r}: at least {low:g}")
    return v


class Gate:
    """How many uploads are read and stored at once (INGEST_CONCURRENCY),
    and how long one more waits for a slot (INGEST_WAIT_S) before the 503.
    `running` and `waiting` are for /healthz."""

    def __init__(self, concurrency=None, wait_s=None):
        self.limit = int(_number("INGEST_CONCURRENCY", concurrency, 4, 1))
        self.wait_s = _number("INGEST_WAIT_S", wait_s, 10, 0)
        self._slots = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self.running = self.waiting = 0

    @contextlib.contextmanager
    def slot(self):
        """Yields True with a slot held, False when none came free in time."""
        with self._lock:
            self.waiting += 1
        try:
            got = self._slots.acquire(timeout=self.wait_s) if self.wait_s else self._slots.acquire(blocking=False)
        finally:
            with self._lock:
                self.waiting -= 1
        if not got:
            yield False
            return
        with self._lock:
            self.running += 1
        try:
            yield True
        finally:
            with self._lock:
                self.running -= 1
            self._slots.release()

    def health(self) -> dict:
        with self._lock:
            return {"uploads_running": self.running, "uploads_waiting": self.waiting,
                    "uploads_max": self.limit}


def drain(rfile, n: int, chunk: int = 1 << 16, deadline_s: float = 30.0) -> None:
    """Read and drop an upload's body, a chunk at a time, so the sender
    reads the answer instead of a reset connection."""
    end = time.monotonic() + deadline_s
    while n > 0 and time.monotonic() < end:
        got = rfile.read(min(chunk, n))
        if not got:
            return
        n -= len(got)
