"""When each person last used the install: one small record per person.

The owner's overview (wowstats.admin) shows who uses the install without
showing anyone's game: these few fields are all it adds to what the server
already kept. One file per person, users/<account>/activity.json in
WOW_STATE_DIR, overwritten in place (never a log), and deleted with the rest
of their state folder when they leave the friends list:

  full       the last request to the full view (any page or API call)
  mcp        the last MCP call or token refresh
  upload     {at, events}: the last /ingest that carried events
  install    {at, os, version}: the last install or update command they ran
             (the server's own version then, and macOS or Windows from the
             command's client)

"Seen" times are written at most every SEEN_EVERY_S per person, so a page
that polls doesn't rewrite the file every few seconds. No path, page, tool,
query, character or address is kept: a time, a count, a version, an OS.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

FILE = "activity.json"
SEEN_EVERY_S = 300
KINDS = ("full", "mcp")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Activity:
    """The records of one install's people (wowstats.tenancy.Tenancy.activity)."""

    def __init__(self, user_dir):
        self.user_dir = user_dir  # User -> their state folder (Tenancy.user_dir)
        self._seen: dict[tuple[int, str], float] = {}
        self._lock = threading.Lock()

    def _path(self, u) -> Path:
        return Path(self.user_dir(u)) / FILE

    def get(self, u) -> dict:
        try:
            d = json.loads(self._path(u).read_text())
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def _update(self, u, **fields) -> None:
        with self._lock:
            p = self._path(u)
            d = self.get(u)
            d.update(fields)
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_name(f".{FILE}.{os.getpid()}.tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(d, f, sort_keys=True)
                os.replace(tmp, p)
            except OSError as e:  # a full disk loses a timestamp, never a request
                print(f"activity: not written ({type(e).__name__})", flush=True)

    def seen(self, u, kind: str, now: float | None = None) -> None:
        """A request from the person on the full view or the MCP."""
        if kind not in KINDS:
            raise ValueError(kind)
        now = time.time() if now is None else now
        key = (u.account_id, kind)
        with self._lock:
            if now - self._seen.get(key, 0.0) < SEEN_EVERY_S:
                return
            self._seen[key] = now
        self._update(u, **{kind: now_iso()})

    def uploaded(self, u, events: int) -> None:
        if events > 0:
            self._update(u, upload={"at": now_iso(), "events": int(events)})

    def installed(self, u, os_name: str, version: str) -> None:
        self._update(u, install={"at": now_iso(), "os": os_name, "version": version})

    def forget(self, u) -> None:
        with self._lock:
            for k in KINDS:
                self._seen.pop((u.account_id, k), None)
