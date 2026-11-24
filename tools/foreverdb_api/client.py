"""A small client for the ForeverDB.net API (https://foreverdb.net/developers),
used at release time only (tools/foreverdb_api/export.py). Stdlib only.

What the API allows and what this does about it (measured 2026-11-24):

- No batching: no endpoint takes several IDs (`ids=`, repeated `id=` and
  `items/1,2` are ignored or refused), so every detail is one request.
  Lists take `limit` up to 100: every list is read 100 at a time.
- No conditional requests: answers carry no ETag or Last-Modified, and
  `If-None-Match`/`If-Modified-Since` still get a full 200, counted. What
  saves calls is the cache below: one file per request, kept per game build
  (`meta.build`), so a second run on the same build costs the one `/meta`
  call. The cache is also the resume point: a run stopped by the daily
  quota picks up where it stopped.
- Limits (free tier): 30 requests a minute and 5,000 a day per account.
  Requests are spaced to stay under the minute, the rate headers are
  obeyed (`x-ratelimit-remaining` 0: wait for `x-ratelimit-reset`), a 429
  waits for its Retry-After and is retried at most MAX_429 times in a run,
  and the run stops cleanly (QuotaStop) when `x-daily-remaining` falls to
  the reserve, or on a 402. Errors count against the quota too (a 404 or
  400 lowers `x-daily-remaining`), so nothing is retried blind.
- gzip: asked for, about 4x smaller.
- The key comes from the environment (FOREVERDB_API_KEY), is sent in the
  X-API-Key header and never logged. Cloudflare refuses default library
  User-Agents (error 1010): every request names this project.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://foreverdb.net/api/v1/"
UA = "wow-mcp data export (+https://github.com/sam-dumont/wow-mcp)"
PAGE = 100  # the most a list gives per request
PER_MINUTE = 30
RESERVE = 100  # requests left for the day that a run never spends
MAX_429 = 5  # 429s in one run before it gives up
MAX_5XX = 2  # retries of one request on a server error


class ApiError(Exception):
    """A request that failed for a reason a retry won't fix."""


class QuotaStop(Exception):
    """The daily quota (or the account's plan) says stop. Everything fetched
    so far is in the cache: the next run resumes from there."""


class Client:
    def __init__(self, key: str, cache: Path | None = None, *, base: str = BASE, per_minute: int = PER_MINUTE,
                 reserve: int = RESERVE, max_calls: int | None = None, opener=None, sleep=time.sleep,
                 clock=time.time):
        if not key:
            raise ApiError("no API key (FOREVERDB_API_KEY)")
        self._key = key
        self.cache = cache
        self.base = base
        self.gap = 60.0 / per_minute
        self.reserve = reserve
        self.max_calls = max_calls
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._clock = clock
        self._last = 0.0
        self.build: str | None = None
        self.calls = 0  # requests sent in this run
        self.cached = 0  # requests answered from the cache
        self.too_many = 0
        self.daily_left: int | None = None

    def __repr__(self) -> str:  # never the key
        return f"Client(base={self.base!r}, build={self.build!r}, calls={self.calls})"

    # --- the cache, per build ------------------------------------------------

    def _cache_file(self, path: str) -> Path | None:
        if not self.cache or not self.build:
            return None
        h = hashlib.sha1(path.encode()).hexdigest()[:20]
        return self.cache / self.build / f"{h}.json"

    def get(self, path: str, params: dict | None = None, *, fresh: bool = False) -> dict:
        """One request's JSON ({data, meta}), from the cache when this build
        already has it."""
        q = urllib.parse.urlencode(sorted((k, v) for k, v in (params or {}).items() if v is not None))
        path = path.lstrip("/") + (f"?{q}" if q else "")
        f = None if fresh else self._cache_file(path)
        if f and f.exists():
            self.cached += 1
            return json.loads(f.read_text(encoding="utf-8"))
        body = self._request(path)
        f = self._cache_file(path)
        if f:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
        return body

    def meta(self) -> dict:
        """/meta, always asked (one request): its build keys the cache."""
        d = self.get("meta", fresh=True)
        self.build = (d.get("meta") or {}).get("build") or (d.get("data") or {}).get("build")
        if not self.build:
            raise ApiError("/meta names no build")
        return d

    def pages(self, path: str, params: dict | None = None) -> list:
        """Every row of a list, PAGE at a time."""
        rows: list = []
        offset = 0
        while True:
            d = self.get(path, {**(params or {}), "limit": PAGE, "offset": offset})
            got = d.get("data") or []
            rows.extend(got)
            m = d.get("meta") or {}
            offset += len(got)
            if not got or not m.get("next") or (m.get("total") is not None and offset >= m["total"]):
                return rows

    # --- the wire ---------------------------------------------------------------

    def _pace(self) -> None:
        wait = self._last + self.gap - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last = self._clock()

    def _request(self, path: str) -> dict:
        errors = 0
        while True:
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise QuotaStop(f"--max-calls {self.max_calls} reached")
            if self.daily_left is not None and self.daily_left <= self.reserve:
                raise QuotaStop(f"{self.daily_left} requests left today, keeping {self.reserve}")
            self._pace()
            req = urllib.request.Request(self.base + path, headers={
                "X-API-Key": self._key, "User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip"})
            self.calls += 1
            try:
                with self._open(req, timeout=60) as r:
                    status, headers, raw = r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                status, headers, raw = e.code, e.headers, e.read()
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                errors += 1
                if errors > MAX_5XX:
                    raise ApiError(f"{path}: {e.__class__.__name__}") from None
                self._sleep(5 * errors)
                continue
            self._note(headers)
            if status == 200:
                if (headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
            if status == 429:
                self.too_many += 1
                if self.too_many > MAX_429:
                    raise QuotaStop(f"{self.too_many} rate-limit answers in this run")
                self._sleep(_retry_after(headers, default=60.0))
                continue
            if status == 402:
                raise QuotaStop("402: the account's daily requests are used up")
            if status >= 500:
                errors += 1
                if errors > MAX_5XX:
                    raise ApiError(f"{path}: HTTP {status}")
                self._sleep(5 * errors)
                continue
            raise ApiError(f"{path}: HTTP {status} {raw[:200]!r}")

    def _note(self, headers) -> None:
        left = _int(headers.get("x-daily-remaining"))
        if left is not None:
            self.daily_left = left
        if _int(headers.get("x-ratelimit-remaining")) == 0:
            reset = _int(headers.get("x-ratelimit-reset"))
            now = self._clock()
            if reset and reset > now:
                self._sleep(min(reset - now, 61))


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _retry_after(headers, default: float) -> float:
    v = _int(headers.get("Retry-After"))
    return float(min(max(v, 1), 300)) if v is not None else default
