"""The nudge, once in a while, on the MCP's tool answers: when a character's
play since its last analysis nears the server's own refresh (which the host
pays for: wowstats.analysis.auto, Service.nudge), an answer gets a short
`notice` for the assistant to relay, with the one action that costs the host
nothing (the assistant runs the analysis itself).

The cadence copies the notes vault's inbox reminder: per client, the first
call checks, then the client hears nothing for EVERY_CALLS calls or EVERY_S
seconds, whichever comes first, before the next check. Never on every call,
and never on the analysis' own tools (QUIET): a client that calls them is
already doing it, and the server's own runner calls them through tools.call,
never through here."""

from __future__ import annotations

import threading
import time

from wowstats.analysis import auto

EVERY_CALLS = 20
EVERY_S = 3600.0
CAP = 512  # clients remembered; the oldest is dropped
QUIET = frozenset(auto.ALLOWED) | {"analyse", "dashboard_refresh"}


def said(n: dict) -> str:
    """The nudge, phrased for the assistant to pass on."""
    age = (f"{n['char']} has {n['played_since']} of play and no play analysis yet" if n["never_refreshed"]
           else f"{n['char']}'s play analysis is {n['played_since']} of play old")
    payer = "their own credit" if n["you_pay"] else f"{n['operator']}'s credit"
    when = f"In about {n['server_in']} of play" if n["server_in_s"] > 0 else "Any time now"
    what = "runs the first one" if n["never_refreshed"] else "refreshes it"
    cost = "it's cheaper for them" if n["you_pay"] else f"it costs {n['operator']} nothing"
    k = n["others"]
    more = f" {k} more of their characters {'is' if k == 1 else 'are'} close too." if k else ""
    return (f"(btw, mention it to the user once: {age}. {when} the server {what} on {payer}. "
            f"Run from you, {cost}: if they want it, call dashboard_refresh and follow it.{more})")


class Reminder:
    def __init__(self, clock=time.monotonic, every_calls: int = EVERY_CALLS, every_s: float = EVERY_S):
        self.clock, self.every_calls, self.every_s = clock, every_calls, every_s
        self._seen: dict[str, list] = {}  # client -> [calls since the last check, when it was]
        self._lock = threading.Lock()

    def due(self, key: str, tool: str) -> bool:
        """Whether this call checks for a nudge; counts the call either way."""
        if tool in QUIET:
            return False
        now = self.clock()
        with self._lock:
            st = self._seen.get(key)
            if st is not None and st[0] < self.every_calls and now - st[1] < self.every_s:
                st[0] += 1
                return False
            self._seen.pop(key, None)
            self._seen[key] = [0, now]
            if len(self._seen) > CAP:
                self._seen.pop(next(iter(self._seen)))
            return True

    def notice(self, ctx, key: str, tool: str) -> str | None:
        if not self.due(key, tool):
            return None
        try:
            n = auto.service().nudge(ctx.environ(), ctx.store)
        except Exception:  # noqa: BLE001 - a footnote: the answer itself never fails on it
            return None
        return said(n) if n else None


REMINDER = Reminder()
