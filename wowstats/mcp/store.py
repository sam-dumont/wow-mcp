"""Reads the stored events (VictoriaLogs) and the 30-day API gauges
(VictoriaMetrics) for the MCP tools and the coach.

The events go through the same path as the page (wowstats.stores.Store.vl):
a query on one character is answered from the process' event cache
(wowstats.eventcache), so the page and the AI read one snapshot and an
answer can say which (`as_of`). What the cache's engine can't answer (the
character list, the auction scans, an unusual query) still goes to
VictoriaLogs. There used to be a second, uncached path here; answers read
through it could stand on newer or older data than the page (review,
2026-11-23).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from wowstats import stores as _stores
from wowstats.stores import HISTORY_START, SourceError, env_account, iso_us

QUERY_TIMEOUT = 20
ROW_LIMIT = 20000


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def lq(s: str) -> str:
    """A LogsQL quoted string (Go-style escapes, which JSON's are a subset of)."""
    return json.dumps(s, ensure_ascii=False)


def parse_t(s) -> datetime:
    if not s:
        return datetime.min.replace(tzinfo=UTC)
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


_REL = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$")


def parse_when(s: str | None, now: datetime, default: datetime) -> datetime:
    """'7d', '36h', '90m', '2w', an ISO date or datetime; empty -> default."""
    if not s:
        return default
    m = _REL.match(str(s))
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return now - {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n),
                      "w": timedelta(weeks=n)}[unit]
    t = parse_t(s)
    if t == datetime.min.replace(tzinfo=UTC):
        raise ValueError(f"can't read the time {s!r}: use 7d, 36h, 2w or an ISO date")
    return t


def _window(start: datetime | None, end: datetime | None) -> str:
    """[start, end] as a LogsQL filter (both ends inclusive, to the
    microsecond, as VictoriaLogs reads the request's own start and end)."""
    if start and end:
        return f"_time:[{iso_us(start)}, {iso_us(end)}] "
    if start:
        return f"_time:>={iso_us(start)} "
    if end:
        return f"_time:<={iso_us(end)} "
    return ""


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or "?"


@dataclass
class Store:
    vl_url: str
    vl_archive_url: str = ""
    vm_url: str = ""
    logs_filter: str = "feed:wow-*"
    hot_days: int = 89
    timeout: float = QUERY_TIMEOUT
    headers: dict = field(default_factory=dict)
    account_id: int = 0  # the VictoriaLogs tenant (wowstats.stores.tenant_headers)

    @classmethod
    def from_env(cls, env=os.environ) -> Store:
        return cls(
            vl_url=env.get("VL_URL", "http://victorialogs:9428").rstrip("/"),
            vl_archive_url=env.get("VL_ARCHIVE_URL", "").rstrip("/"),
            vm_url=env.get("VM_URL", "").rstrip("/"),
            logs_filter=env.get("LOGS_FILTER", "feed:wow-*"),
            hot_days=int(env.get("VL_ARCHIVE_AFTER_DAYS", "89")),
            account_id=env_account(env),
        )

    # ------------------------------------------------------------ http

    def _get(self, url: str, params: dict, extra: dict | None = None) -> bytes:
        full = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full, headers={"Accept": "application/json", **self.headers, **(extra or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read(300).decode("utf-8", "replace").strip()
            raise SourceError(f"{e.code} from {_host(url)}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceError(f"{_host(url)}: {e}") from None

    @property
    def events(self) -> _stores.Store:
        """The page's store over the same VictoriaLogs (no vm-wow): its event
        cache is shared with the page (wowstats.eventcache.shared)."""
        key = (self.vl_url, self.vl_archive_url, self.logs_filter, self.hot_days, self.account_id)
        got = self.__dict__.get("_events")
        if got is None or got[0] != key:
            got = (key, _stores.Store(self.vl_url, self.vl_archive_url, "", self.logs_filter, self.hot_days,
                                      profile_vm_url="", account_id=self.account_id))
            self.__dict__["_events"] = got
        return got[1]

    # ------------------------------------------------------------ api

    def window(self) -> tuple[datetime, datetime]:
        """The whole history: game data is kept for good."""
        return HISTORY_START, datetime.now(UTC)

    def logs(self, query: str, start: datetime | None = None, end: datetime | None = None,
             limit: int = ROW_LIMIT) -> list[dict]:
        """`query` goes after the stream filter, over [start, end] (default:
        the whole history), at most `limit` rows. One character's query is
        answered from the event cache, the range as a `_time` filter; the
        rest goes to VictoriaLogs: a small `limit` is one request (a sample),
        a full one reads every page. Rows from the archive (older than
        hot_days) come first when it is configured and the range reaches it."""
        ev = self.events
        if ev.use_cache:
            rows = ev.cache.answer(f"{self.logs_filter} {_window(start, end)}{query}")
            if rows is not None:
                return list(rows[:limit])
        lo, hi = self.window()
        start, end = start or lo, end or hi
        q = f"{self.logs_filter} {query}"
        if limit < ROW_LIMIT:
            return ev.vl_range(q, start, end, limit)
        return ev.vl_direct(q, limit=limit, start=start, end=end)

    def metrics(self, q: str) -> list[dict]:
        if not self.vm_url:
            raise SourceError("no VM_URL: the Battle.net exporter's store is not configured")
        body = json.loads(self._get(f"{self.vm_url}/api/v1/query", {"query": q}))
        return body.get("data", {}).get("result", [])

    def metrics_range(self, q: str, start: datetime, end: datetime, step: str) -> list[dict]:
        if not self.vm_url:
            raise SourceError("no VM_URL: the Battle.net exporter's store is not configured")
        body = json.loads(self._get(f"{self.vm_url}/api/v1/query_range", {
            "query": q, "start": str(int(start.timestamp())), "end": str(int(end.timestamp())), "step": step}))
        return body.get("data", {}).get("result", [])
