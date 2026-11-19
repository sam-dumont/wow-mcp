"""Reads the stored events (VictoriaLogs) and the 30-day API gauges
(VictoriaMetrics) for the MCP tools and the coach.

Small on purpose: one GET per query, rows as dicts, nothing cached. The
UI server has its own views; these tools want different cuts (a time range,
a limit) and compact answers.
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

from wowstats.stores import HISTORY_START, VL_GATE, env_account, iso_us, paged_read, tenant_headers

QUERY_TIMEOUT = 20
ROW_LIMIT = 20000


class SourceError(Exception):
    """A store could not answer. The message names the host, never a URL with credentials."""


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

    def _logs_one(self, base: str, query: str, start: datetime, end: datetime, limit: int) -> list[dict]:
        """One store, one range, through the process' gate
        (wowstats.stores.VL_GATE); an answer that fills `limit` is read
        again in pages (wowstats.stores.paged_read), unless the caller asked
        for a small sample."""

        def fetch(q: str, a: datetime, z: datetime, n: int) -> list[dict]:
            with VL_GATE:
                body = self._get(f"{base}/select/logsql/query",
                                 {"query": q, "start": iso_us(a), "end": iso_us(z), "limit": str(n)},
                                 tenant_headers(self.account_id))
            return [json.loads(line) for line in body.splitlines() if line.strip()]

        if limit < ROW_LIMIT:
            return fetch(query, start, end, limit)
        return paged_read(fetch, query, start, end, limit)

    # ------------------------------------------------------------ api

    def window(self) -> tuple[datetime, datetime]:
        """The whole history: game data is kept for good."""
        return HISTORY_START, datetime.now(UTC)

    def logs(self, query: str, start: datetime | None = None, end: datetime | None = None,
             limit: int = ROW_LIMIT) -> list[dict]:
        """`query` goes after the stream filter. Rows from the archive (older
        than hot_days) come first when it is configured and the range reaches it."""
        lo, hi = self.window()
        start, end = start or lo, end or hi
        q = f"{self.logs_filter} {query}"
        cutoff = hi - timedelta(days=self.hot_days)
        if not self.vl_archive_url or start >= cutoff:
            return self._logs_one(self.vl_url, q, start, end, limit)
        rows = self._logs_one(self.vl_archive_url, q, start, min(end, cutoff), limit) if start < cutoff else []
        if end > cutoff:
            rows += self._logs_one(self.vl_url, q, max(start, cutoff), end, limit)
        return rows

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
