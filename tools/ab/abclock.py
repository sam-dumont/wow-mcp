"""A frozen clock and seeded randomness for the A/B check (tools/ab).

Both versions under test must see the same "now": the plan's pace, "3 weeks
of talent changes", the ages on the page and every store query's time range
all hang on it, and the recording only answers the queries made at the
recorded instant. `install(at)` makes the code under test believe it is
`at`, for good:

- `datetime.datetime` and `datetime.date` in the datetime module are swapped
  for subclasses whose now(), utcnow() and today() answer `at`. Code that
  imports them afterwards (wowstats) gets the frozen ones; isinstance()
  still works both ways (the metaclass).
- `time.time()`, `time.time_ns()`, and the no-argument `time.gmtime()`,
  `time.localtime()`, `time.strftime()` answer `at` when wowstats (or the
  driver) asks. Anything else (FastMCP's client, the HTTP server's log line)
  gets the real clock moved to `at`, so a wait on a deadline still ends.
  `time.monotonic()` and `time.sleep()` are untouched.
- `secrets.token_*`, `secrets.choice` and `random` come from one seeded
  generator: the zogzog nonces and the semantic layer's parameter names are
  the same in every run.

Standard library only: it runs inside the image under test as well as in a
checkout (any Python from 3.11). Not for production: the point is to lie.
"""

from __future__ import annotations

import datetime as _dt
import os
import random as _random
import secrets as _secrets
import sys
import time as _time

_REAL_DT = _dt.datetime
_REAL_DATE = _dt.date
_REAL_TIME = _time.time
_REAL_TIME_NS = _time.time_ns
_REAL_GMTIME = _time.gmtime
_REAL_LOCALTIME = _time.localtime
_REAL_STRFTIME = _time.strftime
_REAL_MONO = _time.monotonic

# Callers that see the frozen instant through time.time(): the app and the
# driver. Everything else sees a real clock shifted to the instant.
FROZEN_PREFIXES = ("wowstats", "__main__", "driver", "abclock")
_state: dict = {}


def _caller_frozen(depth: int = 2) -> bool:
    try:
        f = sys._getframe(depth)
    except ValueError:
        return True
    name = f.f_globals.get("__name__", "") or ""
    return name.startswith(FROZEN_PREFIXES)


def _now_ts() -> float:
    return _state["ts"]


def _shifted_ts() -> float:
    return _state["ts"] + (_REAL_MONO() - _state["mono0"])


class _DateMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATE)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _REAL_DATE)


class _DatetimeMeta(_DateMeta):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DT)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _REAL_DT)


class FrozenDate(_REAL_DATE, metaclass=_DateMeta):
    @classmethod
    def today(cls):
        t = _REAL_DT.fromtimestamp(_now_ts())
        return cls(t.year, t.month, t.day)


class FrozenDatetime(_REAL_DT, metaclass=_DatetimeMeta):
    @classmethod
    def now(cls, tz=None):
        t = _REAL_DT.fromtimestamp(_now_ts(), tz)
        return cls.combine(t.date(), t.timetz()) if tz is not None else cls.combine(t.date(), t.time())

    @classmethod
    def utcnow(cls):
        t = _REAL_DT.fromtimestamp(_now_ts(), _dt.UTC).replace(tzinfo=None)
        return cls.combine(t.date(), t.time())

    @classmethod
    def today(cls):
        return cls.now()


def _time_fn():
    return _now_ts() if _caller_frozen() else _shifted_ts()


def _time_ns_fn():
    return int((_now_ts() if _caller_frozen() else _shifted_ts()) * 1_000_000_000)


def _gmtime_fn(secs=None):
    return _REAL_GMTIME(_now_ts() if secs is None and _caller_frozen() else secs)


def _localtime_fn(secs=None):
    return _REAL_LOCALTIME(_now_ts() if secs is None and _caller_frozen() else secs)


def _strftime_fn(fmt, t=None):
    if t is None:
        t = _REAL_LOCALTIME(_now_ts() if _caller_frozen() else None)
    return _REAL_STRFTIME(fmt, t)


def parse_at(at: str) -> _REAL_DT:
    t = _REAL_DT.fromisoformat(at.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"AB_FROZEN_AT={at!r}: give a time zone (Z or +00:00)")
    return t


def install(at: str, seed: int = 0) -> None:
    """Freeze the clock at `at` (ISO 8601 with a zone) and seed randomness.
    Call it before importing the code under test. Twice is harmless."""
    t = parse_at(at)
    _state["ts"] = t.timestamp()
    _state["mono0"] = _REAL_MONO()
    if not _state.get("installed"):
        _dt.datetime = FrozenDatetime
        _dt.date = FrozenDate
        _time.time = _time_fn
        _time.time_ns = _time_ns_fn
        _time.gmtime = _gmtime_fn
        _time.localtime = _localtime_fn
        _time.strftime = _strftime_fn
        _state["installed"] = True
    rng = _random.Random(seed)
    _random.seed(seed)
    _secrets.token_bytes = lambda n=32: rng.randbytes(n if n is not None else 32)
    _secrets.token_hex = lambda n=32: rng.randbytes(n if n is not None else 32).hex()

    def token_urlsafe(n=32):
        import base64

        return base64.urlsafe_b64encode(rng.randbytes(n if n is not None else 32)).rstrip(b"=").decode("ascii")

    _secrets.token_urlsafe = token_urlsafe
    _secrets.choice = rng.choice
    _secrets.randbelow = rng.randrange
    _secrets.randbits = rng.getrandbits


def install_from_env(env=os.environ) -> bool:
    at = (env.get("AB_FROZEN_AT") or "").strip()
    if not at:
        return False
    install(at, int(env.get("AB_SEED", "0") or 0))
    return True
