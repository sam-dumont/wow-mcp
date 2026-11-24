"""An upload is on the page and in the MCP's answers the moment /ingest says
200, in both event stores (wowstats.eventstore's write generation).

Testing #239 on a local server, a character ingested for the first time got
"no events for X" from the page until a restart or the caches' expiry: the
page's answers built before the upload (wowstats.prebuilt, the App's
CACHE_TTL cache) still listed the characters without it, and the event cache
(wowstats.eventcache) kept its empty read of the character for FRESH_S. Every
one of those now checks the store's write generation, which an append moves.

A real server on a port and real stores: the VictoriaLogs stand-in
(tests/degraded.py), the SQLite files, and a real VictoriaLogs when
WOW_TEST_VL_URL names one (CI's logsql job), in a random tenant."""

from __future__ import annotations

import base64
import json
import os
import random
import urllib.parse
import urllib.request

import pytest

from tests import degraded as D
from tests.mcp import fakes
from wowstats import eventstore, prebuilt, ui
from wowstats.mcp import tools
from wowstats.mcp.store import Store as McpStore

OLD = fakes.CHAR
NEW = "Freshling-Fixture"
PW = "ingest-password-for-tests"
VL_URL = os.environ.get("WOW_TEST_VL_URL", "").rstrip("/")


def _reachable() -> bool:
    if not VL_URL:
        return False
    try:
        return urllib.request.urlopen(f"{VL_URL}/health", timeout=3).read().strip() == b"OK"
    except OSError:
        return False


if os.environ.get("WOW_TEST_VL_REQUIRED") == "1":  # CI's logsql job: never a silent skip
    assert _reachable(), f"no VictoriaLogs at {VL_URL!r}"

BACKENDS = [*D.BACKENDS, pytest.param("victorialogs", marks=pytest.mark.skipif(
    not _reachable(), reason="no VictoriaLogs: set WOW_TEST_VL_URL (make test-vl)"))]


def _rows(char: str) -> list[dict]:
    return [{**r, "char": char, "component": "wow",
             **({"source_id": r["source_id"].replace(fakes.CHAR, char)} if r.get("source_id") else {})}
            for r in fakes.RAW]


@pytest.fixture(params=BACKENDS)
def served(request, tmp_path, run_app, monkeypatch):
    """A full view taking uploads, its answers built once over OLD alone (as
    a server that has been up a while), and the MCP on the same process."""
    monkeypatch.setattr(prebuilt, "INGEST_WAKE_S", 3600.0)  # no builder runs here: the answers must not need it
    env = {"VIEW": "full", "BIND": "127.0.0.1", "MODEL3D": "off", "WOW_ITEMS": "off", "INGEST_PASSWORD": PW,
           "DEFAULT_CHAR": OLD, "HOME": str(tmp_path / "home"), "CACHE_TTL": "3600"}
    stand_in = None
    if request.param == "victorialogs":
        env |= {"VL_URL": VL_URL, "VL_ACCOUNT_ID": str(random.randint(10 ** 6, 10 ** 9))}
        app0 = ui.App(env)
        eventstore.get(VL_URL).append(app0.store.account_id, [json.dumps(r).encode() for r in _rows(OLD)])
        eventstore.get(VL_URL).flush()
    else:
        store_env, stand_in = D.store_env(request.param, _rows(OLD), tmp_path)
        env |= store_env
    app = ui.App(env)
    srv = run_app(app)
    assert app.prebuilt.check() == "first build"
    ctx = tools.Ctx(store=McpStore.from_env(env), default_char=OLD)
    yield app, srv, ctx
    if stand_in is not None:
        stand_in.close()


def _get(srv, path: str) -> tuple[int, dict]:
    status, _, body = srv.get(path)
    return status, json.loads(body or b"{}")


def _ingest(srv, rows: list[dict]) -> None:
    auth = "Basic " + base64.b64encode(f"wow:{PW}".encode()).decode()
    body = b"".join(json.dumps(r).encode() + b"\n" for r in rows)
    status, _, out = srv.get("/ingest", method="POST", data=body,
                             headers={"Authorization": auth, "Content-Type": "application/x-ndjson"})
    assert status == 200, out


def _summary(ctx, char: str) -> dict:
    return tools.call(ctx, "character_summary", {"char": char}, tools.mcp_tools(ctx))


def test_a_new_character_is_on_the_page_and_in_the_mcp_at_once(served):
    app, srv, ctx = served
    q = urllib.parse.quote(NEW)
    # Asked before it exists, as the start page and an assistant do: every
    # cache on the way now holds "no such character".
    assert _get(srv, f"/api/char?name={q}")[0] == 404
    assert NEW not in [c["char"] for c in _get(srv, "/api/chars")[1]["chars"]]
    with pytest.raises(Exception, match="no events|unknown character"):
        _summary(ctx, NEW)

    _ingest(srv, _rows(NEW))

    assert NEW in [c["char"] for c in _get(srv, "/api/chars")[1]["chars"]]
    status, char = _get(srv, f"/api/char?name={q}")
    assert status == 200, char
    assert char["header"]["level"] == 14
    assert _summary(ctx, NEW)["level"] == 14


def test_new_events_of_a_known_character_show_at_once(served):
    app, srv, ctx = served
    q = urllib.parse.quote(OLD)
    assert _get(srv, f"/api/char?name={q}")[1]["header"]["level"] == 14
    assert _summary(ctx, OLD)["level"] == 14

    t = "2026-11-17T22:30:00.000Z"
    _ingest(srv, [{"_time": t, "feed": "wow-level", "char": OLD, "component": "wow", "level": 15, "seconds": 3600,
                   "xp": 0, "quests": 0, "kills": 0, "partial": True, "source_id": f"level:{OLD}:15"},
                  {"_time": t, "feed": "wow-character", "char": OLD, "component": "wow", "level": 15, "xp": 10,
                   "xp_max": 13700, "money_copper": 123456, "zone": "The Barrens"}])

    assert _get(srv, f"/api/char?name={q}")[1]["header"]["level"] == 15
    assert _summary(ctx, OLD)["level"] == 15
