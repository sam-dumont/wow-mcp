"""The independent check, on the synthetic priest (tools/fixtures/generate.py
says what it played): count the first four sessions by hand from the events,
then ask the MCP tools the same questions through a fake VictoriaLogs that
answers their LogsQL from the same events. Four sessions, five deaths, one
Blackfathom Deeps run of 4,980 s for 18,211 XP: 13,164.6 XP an hour, which
the tools must round (13,165), not truncate (13,164); and the summary and
`deaths` must give the same count."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tests.test_bounded_reads import FakeVL
from wowstats import stores
from wowstats.analysis import prep
from wowstats.mcp import tools
from wowstats.mcp.store import Store

DATA = Path(__file__).resolve().parents[1] / "data" / "analysis-events.jsonl.gz"
CHAR = "Testpriest-Fixture"
SECONDS, XP = 4980, 18211  # tools/fixtures/generate.py: BFD_SECONDS, BFD_XP


@pytest.fixture(scope="module")
def rows():
    with gzip.open(DATA) as f:
        return [json.loads(line) for line in f]


@pytest.fixture
def call(rows, monkeypatch):
    fake = FakeVL(rows)
    monkeypatch.setattr(Store, "_get", lambda self, url, params, extra=None: fake(url, params))
    # The prep behind the semantic layer reads through wowstats.stores (its event cache).
    monkeypatch.setattr(stores, "http_get", fake)
    ctx = tools.Ctx(store=Store(vl_url="http://vl.invalid"))
    return lambda name, **args: tools.call(ctx, name, {"char": CHAR, **args})


def test_the_events_by_hand(rows):
    """What anyone recounts with jq: the generator's numbers are in the data."""
    deaths = [r for r in rows if r.get("kind") == "death"]
    runs = [r for r in rows if r.get("feed") == "wow-dungeon"]
    assert len(deaths) == 5
    assert [(r["name"], int(r["seconds"]), int(r["xp"])) for r in runs] == [("Blackfathom Deeps", SECONDS, XP)]
    assert pytest.approx(13164.58, abs=0.01) == XP * 3600 / SECONDS
    db = prep.build(rows, CHAR)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 4
    assert db.execute("SELECT SUM(deaths) FROM sessions").fetchone()[0] == 5


def test_the_tools_agree_with_the_events(call):
    sessions = call("sessions", since="2026-10-01T00:00:00Z", until="2026-10-20T00:00:00Z")["sessions"]
    assert len(sessions) == 4
    assert sum(s["deaths"] for s in sessions) == 5
    deaths = call("deaths")
    assert deaths["total"] == 5
    assert call("character_summary")["deaths"] == deaths["total"]
    runs = call("dungeons")["runs"]
    assert [(r["name"], r["seconds"], r["xp"]) for r in runs] == [("Blackfathom Deeps", SECONDS, XP)]
    assert runs[0]["xp_per_hour"] == 13165  # rounded; truncating says 13164
