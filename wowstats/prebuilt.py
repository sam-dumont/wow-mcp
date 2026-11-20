"""The page's answers, built once per new data and served as they are.

A plain open of the page asks for a dozen answers (the character, its
gear, overview, dungeons, loot, people, plan, the market...). Each used to
be worked out when asked, from the stores, and kept CACHE_TTL seconds, so
nearly every open waited on VictoriaLogs ("Reading the logs"). Now one
background thread per process builds every answer a plain open asks for
(ui.App.page_answers: the routes' own methods, so nothing is shaped twice)
when the data changed, and the routes serve that set until the next one is
complete. Opening the page never waits on a store once the first build is
in; before it (the process just started), answers are worked out as before.

"Changed" is what the publisher checks too: the newest event's time, plus
here the number of events (a late upload of an old session moves no
newest time), and the Battle.net fingerprint in vm-wow. One small stats
query and one vm-wow read per check, every CHECK_S, per person. The
token and the ruleset status come from VictoriaMetrics on their own clock:
those answers alone are rebuilt every MARKET_S. Everything is rebuilt at
least every MAX_AGE_S (Battle.net data in an answer stays far inside the
API terms' 30 days).

Per person: with several people (wowstats.tenancy) each App holds its own
set, built from its own stores, and the routes answer it only after their
usual login checks; nothing of it is on disk or on the share port.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime

from wowstats.stores import SourceError, iso

CHECK_S = 60
MARKET_S = 300
MAX_AGE_S = 86400

# Wakes the builder early: a plan was made, the next build shouldn't wait.
WAKE = threading.Event()
# New events came in through /ingest: the builder checks INGEST_WAKE_S after
# the last upload of a burst (VictoriaLogs makes rows searchable within a
# second or so). Without it /start turned green with the character while "My
# page" still said "No character yet" until the next CHECK_S pass.
INGEST_WAKE_S = 3.0
_soon: threading.Timer | None = None
_soon_lock = threading.Lock()


def soon(delay: float | None = None) -> None:
    """Wake the builder `delay` seconds after the last call (a shipper sends
    its events in several uploads: one check after the burst, not one per
    upload)."""
    global _soon
    with _soon_lock:
        if _soon is not None:
            _soon.cancel()
        _soon = threading.Timer(INGEST_WAKE_S if delay is None else delay, WAKE.set)
        _soon.daemon = True
        _soon.start()


def log(*a) -> None:
    print(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "prebuilt:", *a, flush=True)


class Prebuilt:
    """One App's set of answers (None until the first build)."""

    def __init__(self, app):
        self.app = app
        self.answers: dict[str, dict] | None = None
        self.mark: tuple | None = None
        self.built_at = 0.0
        self.market_at = 0.0
        self.checked_at = ""
        self.error: str | None = None
        self.dirty = False
        self.seconds = 0.0
        self._lock = threading.Lock()  # one build at a time per App

    def get(self, key: str) -> dict | None:
        """The answer as a plain open gets it; "as of" the last check that
        found nothing new, and marked stale while the stores don't answer."""
        answers = self.answers
        hit = answers.get(key) if answers is not None else None
        if hit is None:
            return None
        out = {**hit, "generated_at": self.checked_at or hit.get("generated_at")}
        if self.error:
            out.update(stale=True, error=self.error)
        return out

    def changed(self, prefix: str = "") -> None:
        """Something the answers read besides the events changed (a plan was
        made): drop those answers, so they are worked out when asked, and
        rebuild everything at the next check, which comes now."""
        answers = self.answers
        if answers is not None and prefix:
            self.answers = {k: v for k, v in answers.items() if not k.startswith(prefix)}
        self.dirty = True
        WAKE.set()

    def check(self, now: float | None = None) -> str | None:
        """Rebuild when the data moved; returns why it did, or None."""
        now = time.time() if now is None else now
        with self._lock:
            try:
                mark = self.app.data_mark(self.mark)
            except SourceError as e:
                self.error = str(e)
                return None
            why = ("first build" if self.answers is None else "new data" if mark != self.mark
                   else "changed" if self.dirty else "daily rebuild" if now - self.built_at >= MAX_AGE_S
                   else "market" if self.app.view == "full" and now - self.market_at >= MARKET_S else None)
            if why is None:
                self.checked_at, self.error = iso(datetime.now(UTC)), None
                return None
            t0 = time.monotonic()
            self.dirty = False
            try:
                if why == "market":
                    self.answers = self.app.page_answers(only="market:", keep=self.answers)
                else:
                    self.answers = self.app.page_answers()
                    self.mark, self.built_at = mark, now
            except SourceError as e:
                # A store down mid-build: the last set stays, marked stale.
                self.error = str(e)
                self.dirty = self.dirty or why == "changed"
                log(f"{why}: kept the last answers: {e}")
                return None
            self.market_at = now
            self.checked_at, self.error = iso(datetime.now(UTC)), None
            self.seconds = round(time.monotonic() - t0, 1)
            if why != "market":
                size = len(json.dumps(self.answers, separators=(",", ":"), default=str)) / 2**20
                log(f"{why}: {len(self.answers)} answers, {size:.1f} MiB, in {self.seconds} s")
            return why


def start(apps, every: float = CHECK_S) -> threading.Thread:
    """The builder: every `every` seconds (or when woken), each App that
    apps() lists, one after the other, so a process never builds two sets
    at once."""
    def loop():
        while True:
            WAKE.clear()
            try:
                todo = list(apps())
            except Exception as e:  # noqa: BLE001 - the list comes back at the next pass
                log(f"no list of people: {e!r}")
                todo = []
            for app in todo:
                try:
                    app.prebuilt.check()
                except Exception as e:  # noqa: BLE001 - one person's bug doesn't stop the others
                    log(f"build failed: {e!r}")
            WAKE.wait(every)

    t = threading.Thread(target=loop, name="prebuilt", daemon=True)
    t.start()
    return t
