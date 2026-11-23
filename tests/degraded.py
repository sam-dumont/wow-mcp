"""The demo character with sources taken away: one row set per missing
source, served by a VictoriaLogs stand-in that answers with wowstats.logsql,
for the degraded-mode tests (tests/test_degraded.py).

Every scenario is the docs site's demo profile (tools/fixtures/generate.py
build_demo) minus what a player without that source would never send. The
Battle.net store answers nothing in all of them: a friend without a linked
account is the common case, so it is the baseline here.
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import urllib.parse
from collections.abc import Iterator
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wowstats import logsql
from wowstats.stores import parse_t

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "fixtures"))

import generate  # noqa: E402
import time_machine  # noqa: E402

END = "2026-11-18"
CHAR = generate.DEMO


def _setup(rows: list[dict], *, drop_addons: tuple[str, ...] = (), recorder_on: str | None = None) -> list[dict]:
    out = []
    for r in rows:
        if r["feed"] == "wow-setup":
            r = dict(r)
            addons = {k: v for k, v in json.loads(r["addons"]).items() if k not in drop_addons}
            r["addons"] = json.dumps(addons)
            if recorder_on is not None:
                r["recorder_on"] = recorder_on
        out.append(r)
    return out


def _drop(rows: list[dict], *feeds: str) -> list[dict]:
    return [r for r in rows if r["feed"] not in feeds]


def _old(rows: list[dict]) -> list[dict]:
    """A shipper from before the setup report, the quest and bag feeds and
    the later character fields (bind, resting, sex)."""
    out = []
    for r in _drop(rows, "wow-setup", "wow-quests", "wow-bags", "wow-player"):
        if r["feed"] == "wow-character":
            r = {k: v for k, v in r.items() if k not in ("bind", "resting", "sex")}
        out.append(r)
    return out


# The switch-on day of the "-later" scenarios: splits, sheets and dungeon
# runs from the start, the source's own data from this day on (the friend
# who turned the Recorder on after a week of playing without it).
SWITCH = "2026-11-12T00:00:00Z"


GAP = ("2026-11-05T17:20:00Z", "2026-11-05T18:30:00Z")


def _before(rows: list[dict], t: str = SWITCH) -> list[dict]:
    """The account as it stood just before `t` (the setup report kept: it
    tells what was installed then)."""
    return [r for r in rows if r["feed"] == "wow-setup" or parse_t(r["_time"]) < parse_t(t)]


def _from(rows: list[dict], feeds: tuple[str, ...], t: str = SWITCH) -> list[dict]:
    """`feeds` only from `t` on: the source arrived that day."""
    return [r for r in rows if r["feed"] not in feeds or parse_t(r["_time"]) >= parse_t(t)]


SCENARIOS = {
    "full": lambda rows: rows,
    # The Recorder never installed: no events of it, and the report says so.
    "no-recorder": lambda rows: _setup(_drop(rows, "wow-recorder"), drop_addons=("recorder",)),
    # Installed, its switch never turned on (the friend who asked for a plan).
    "recorder-off": lambda rows: _setup(_drop(rows, "wow-recorder"), recorder_on="false"),
    "no-syndicator": lambda rows: _setup(_drop(rows, "wow-gear", "wow-bags"), drop_addons=("syndicator",)),
    "no-auctionator": lambda rows: _setup(_drop(rows, "wow-prices"), drop_addons=("auctionator",)),
    "no-quests-addon": lambda rows: _setup(_drop(rows, "wow-quests"), drop_addons=("questie", "altoholic")),
    "old-shipper": _old,
    # Polestar alone, Recorder off: splits, the character sheet, dungeon runs.
    "polestar-only": lambda rows: _setup([r for r in rows if r["feed"] in (
        "wow-level", "wow-character", "wow-dungeon", "wow-setup")], recorder_on="false",
        drop_addons=("auctionator", "questie", "altoholic")),
    "only-level-splits": lambda rows: [r for r in rows if r["feed"] == "wow-level"],
    # The Recorder switched on at SWITCH, after days without it: before and after.
    "recorder-later-before": lambda rows: _setup(_before(_drop(rows, "wow-recorder")), recorder_on="false"),
    "recorder-later": lambda rows: _from(rows, ("wow-recorder",)),
    # The Recorder off for an hour in the middle of one evening (/psrec off,
    # then on again): level 28's split is longer than the Recorder saw.
    "recorder-gap": lambda rows: [r for r in rows if r["feed"] != "wow-recorder" or not (
        parse_t(GAP[0]) <= parse_t(r["_time"]) < parse_t(GAP[1]))],
    # Level 32 finished with neither Polestar's split nor the Recorder's ding
    # out of it (the split lost, the Recorder off at the ding): its records
    # are there, the level in play is 34.
    "split-missing": lambda rows: [r for r in rows if not (
        r["feed"] == "wow-level" and str(r.get("level")) == "32"
        or r["feed"] == "wow-recorder" and r.get("kind") == "level" and str(r.get("level")) == "33")],
    # Syndicator installed at SWITCH.
    "syndicator-later-before": lambda rows: _setup(_before(_drop(rows, "wow-gear", "wow-bags")),
                                                   drop_addons=("syndicator",)),
    "syndicator-later": lambda rows: _from(rows, ("wow-gear", "wow-bags")),
}

_BASE: list[dict] = []


def base_rows() -> list[dict]:
    if not _BASE:
        _BASE.extend(generate.build_demo(END))
    return [dict(r) for r in _BASE]


def rows_for(scenario: str) -> list[dict]:
    return SCENARIOS[scenario](base_rows())


def now() -> datetime:
    """The demo's "now": an hour after its newest event (END's morning).
    Taken from the rows, never the wall clock."""
    return max(parse_t(r["_time"]) for r in base_rows()) + timedelta(hours=1)


@contextlib.contextmanager
def clock() -> Iterator[None]:
    """Every clock the app reads (time.time, datetime.now, date.today) set
    to now() and ticking from there. The demo days are fixed and the app's
    windows (the gold rate's 14 days of sheets, the loot's, the price days)
    count back from the clock: on the wall clock each window slid across the
    demo a little more every hour, and the gold-rate test went red when its
    oldest sheet fell out (2026-11-21 19:16 UTC; it passed at 19:09)."""
    with time_machine.travel(now(), tick=True):
        yield


class VL:
    """VictoriaLogs over `rows`, and a VictoriaMetrics that answers each
    query with the first `metrics` entry whose key is in it (else nothing),
    on a port."""

    def __init__(self, rows: list[dict], metrics: dict[str, list[dict]] | None = None):
        self.rows = sorted(rows, key=lambda r: parse_t(r.get("_time")))
        self.metrics = metrics or {}
        self.unsupported: list[str] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if u.path == "/select/logsql/query":
                    try:
                        parsed = logsql.parse(q.get("query", ""))
                    except logsql.Unsupported:
                        fake.unsupported.append(q.get("query", ""))
                        return self._send(400, b"unsupported in the stand-in")
                    lo = parse_t(q["start"]) if q.get("start") else None
                    hi = parse_t(q["end"]) if q.get("end") else None
                    rows = [r for r in fake.rows if (lo is None or lo <= parse_t(r["_time"]))
                            and (hi is None or parse_t(r["_time"]) <= hi)]
                    out = parsed.run(rows)[:int(q.get("limit") or 10 ** 9)]
                    return self._send(200, "".join(json.dumps(r) + "\n" for r in out).encode(),
                                      "application/stream+json")
                if u.path.startswith("/api/v1/"):
                    res = next((v for k, v in fake.metrics.items() if k in q.get("query", "")), [])
                    return self._send(200, json.dumps({"status": "success", "data": {"result": res}}).encode())
                self._send(404, b"")

            do_POST = do_GET

        srv = type("S", (ThreadingHTTPServer,), {"request_queue_size": 64, "daemon_threads": True})
        self.httpd = srv(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
