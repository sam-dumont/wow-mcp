"""Time boundaries: one set of edge rules for the analysis' prep, the semantic
layer, the pages and the MCP (docs/guarantees.md, "Time rules").

- A window is half-open, `[start, end)`: its start is in it, its end is not.
  Two windows that touch share no instant, so a record on the edge counts
  once: a death on a ding's second belongs to the new level, an event at
  local 00:00:00 to the new day. `None` for an end is open on that side.
- A span between two records (a session: its first and last Recorder record)
  is closed, `[first, last]`: both ends are records, so both are in it.
- A session ends after more than `SESSION_GAP_S` seconds without a Recorder
  record; exactly that many continues it.
- Days are local days in the configured timezone (`WOW_TIMEZONE`), weeks ISO
  weeks (Monday 00:00 local). Their bounds come from the calendar, never
  from adding 86400: a local day lasts 23 hours in March and 25 in October.
- State history: the state at `x` is the latest one with `t <= x` (a state
  written at `x` already holds at `x`); before the first one there is none.
- Ranges asked for by date (`query_metric`'s `since`/`until`): `since` is
  included; `until` as a date includes that whole day (the window ends at the
  next local midnight), as a date and time it is included.

Times are Unix seconds (float) or aware datetimes; the functions take either
as long as one call doesn't mix them. Durations are always real seconds:
subtract the timestamps, never wall-clock fields.
"""

from __future__ import annotations

import bisect
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "Europe/Brussels"
# The Recorder writes a `session` record every 300 s while the game runs
# (Polestar_Recorder CHUNK_SECONDS); 30 s on top for a slow write. Equal to
# wowstats.stores.SESSION_GAP_S (tests/test_timebounds.py checks it).
SESSION_GAP_S = 330


def zone(name: str | None = None) -> tuple[str, ZoneInfo]:
    """The player's timezone: `name`, else WOW_TIMEZONE (an IANA name), else
    Europe/Brussels. A name the system doesn't know falls back to UTC, said
    once on stderr."""
    name = (name or os.environ.get("WOW_TIMEZONE") or DEFAULT_ZONE).strip()
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        print(f"WOW_TIMEZONE={name!r} is not a known timezone: using UTC", file=sys.stderr, flush=True)
        return "UTC", ZoneInfo("UTC")


def within(t, start, end) -> bool:
    """`t` in the half-open window `[start, end)`; a `None` bound is open."""
    return (start is None or start <= t) and (end is None or t < end)


def in_span(t, first, last) -> bool:
    """`t` in the closed span `[first, last]` between two records."""
    return first <= t <= last


def ends_session(prev, t, gap: float = SESSION_GAP_S) -> bool:
    """A record at `t` after one at `prev` starts a new session: more than
    `gap` seconds apart (exactly `gap` continues). No `prev`: no session to end."""
    return prev is not None and t - prev > gap


def at_or_before(times: Sequence, x) -> int:
    """The index of the latest of the sorted `times` with `t <= x`, -1 when
    every one is after `x` (or there are none)."""
    return bisect.bisect_right(times, x) - 1


def state_at(history: Sequence[tuple], x, default=None):
    """The value of the latest `(t, value)` of the sorted `history` with
    `t <= x`; `default` before the first one or with no history."""
    i = at_or_before([h[0] for h in history], x)
    return history[i][1] if i >= 0 else default


def local(t, tz: ZoneInfo) -> datetime:
    """`t` (Unix seconds or an aware datetime) on the player's clock."""
    return t.astimezone(tz) if isinstance(t, datetime) else datetime.fromtimestamp(t, tz)


def local_day(t, tz: ZoneInfo) -> date:
    """The local day `t` falls in."""
    return local(t, tz).date()


def day_bounds(day: date, tz: ZoneInfo) -> tuple[float, float]:
    """The local day as the half-open `[00:00, next 00:00)`, in Unix seconds."""
    start = datetime.combine(day, time(0), tz)
    return start.timestamp(), datetime.combine(day + timedelta(days=1), time(0), tz).timestamp()


def week_start(day: date) -> date:
    """The Monday of `day`'s ISO week."""
    return day - timedelta(days=day.weekday())


def week_bounds(day: date, tz: ZoneInfo) -> tuple[float, float]:
    """`day`'s ISO week as the half-open `[Monday 00:00, next Monday 00:00)`
    local, in Unix seconds (167, 168 or 169 hours)."""
    monday = week_start(day)
    return day_bounds(monday, tz)[0], day_bounds(monday + timedelta(days=7), tz)[0]


def range_edges(since: str | None, until: str | None) -> list[tuple[str, datetime]]:
    """`query_metric`'s since/until as (operator, local datetime) filters on
    a local time: `since` included; `until` as a date up to the next
    midnight excluded, as a date and time included."""
    out = []
    if since:
        out.append((">=", datetime.fromisoformat(since)))
    if until:
        end = datetime.fromisoformat(until)
        out.append(("<", end + timedelta(days=1)) if "T" not in until else ("<=", end))
    return out
