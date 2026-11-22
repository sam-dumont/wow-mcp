"""The analysis workspaces are bounded (load estimate for the lab,
2026-11-22): idle ones close, the least recently used close past the cap,
none closes while a request holds it, and a closed one comes back with the
same answers."""

from __future__ import annotations

import gzip
import json
import threading
import time
import weakref
from pathlib import Path

import pytest

from wowstats.analysis import drive, prep
from wowstats.mcp import tools

DATA = Path(__file__).resolve().parent.parent / "data" / "analysis-events.jsonl.gz"
CHAR = "Testpriest-Fixture"
QUERIES = ["SELECT COUNT(*) AS n FROM levels",
           "SELECT level, played_s FROM levels ORDER BY level",
           "SELECT COUNT(*) AS n FROM records WHERE kind = 'death'",
           "SELECT zone, zone_s, deaths FROM per_zone ORDER BY zone_s DESC, zone"]  # a semantic view too
FAR = 10 ** 6  # seconds: past any idle time


@pytest.fixture()
def env(tmp_path, monkeypatch):
    with gzip.open(DATA) as f:
        rows = [json.loads(x) for x in f]
    monkeypatch.setattr(prep, "query", lambda store, stream, char: [{**r, "char": char} for r in rows])
    drive._WS.clear()
    e = {"ANALYSIS_STATE_DIR": str(tmp_path), "KB_GAME_TABLES": "off", "VL_URL": "http://unused",
         "ANALYSIS_WORKSPACE_IDLE_S": "60", "ANALYSIS_WORKSPACES_MAX": "2"}
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    yield e
    for w in list(drive._WS.values()):
        w.worker.close()
    drive._WS.clear()


def answers(w) -> list:
    return [w.judge.run(q, 100) for q in QUERIES]


def child_of(w):
    return w.worker._cell[0]


def test_an_idle_workspace_closes_and_comes_back_with_the_same_answers(env):
    w = drive.workspace(env, CHAR)
    before = answers(w)
    proc = child_of(w)
    assert proc is not None and proc.poll() is None
    assert drive.sweep(now=time.monotonic() + 30) == 0  # not idle long enough
    assert drive.sweep(now=time.monotonic() + 61) == 1
    assert drive._WS == {} and child_of(w) is None and proc.poll() is not None  # its DuckDB child is gone
    gone = weakref.ref(w)
    del w
    assert gone() is None  # nothing else keeps the prep in memory
    again = drive.workspace(env, CHAR)
    assert answers(again) == before


def test_past_the_cap_the_least_recently_used_closes(env):
    a = drive.workspace(env, "Alpha-Fixture")
    b = drive.workspace(env, "Bravo-Fixture")
    first = answers(b)
    assert drive.workspace(env, "Alpha-Fixture") is a  # Alpha is now the more recent
    c = drive.workspace(env, "Charlie-Fixture")
    assert {k[1] for k in drive._WS} == {"alpha-fixture", "charlie-fixture"}
    assert child_of(b) is None  # Bravo's DuckDB child ended with it
    assert drive.health()["analysis_workspaces"] == 2 and drive.health()["analysis_workspaces_max"] == 2
    assert answers(drive.workspace(env, "Bravo-Fixture")) == first  # rebuilt, same answers
    assert {k[1] for k in drive._WS} == {"charlie-fixture", "bravo-fixture"}  # Alpha was the oldest
    assert any(x is c for x in drive._WS.values())


def test_a_held_workspace_never_closes_mid_request(env):
    with drive.holding():
        w = drive.workspace(env, CHAR)
        before = answers(w)
        starts = w.worker.starts
        assert drive.sweep(now=time.monotonic() + FAR) == 0  # idle by the clock, but held
        # Other requests, meanwhile (a thread has none of this one's scope):
        # the second makes three, past the cap; the one not held goes.
        other = threading.Thread(target=lambda: [drive.workspace(env, n) for n in ("Other-Fixture", "Third-Fixture")])
        other.start()
        other.join()
        assert {k[1] for k in drive._WS} == {CHAR.lower(), "third-fixture"}
        assert any(x is w for x in drive._WS.values())
        assert drive.health()["analysis_workspaces_held"] == 1
        assert answers(w) == before and w.worker.starts == starts  # the same child answered throughout
    assert w.users == 0 and {k[1] for k in drive._WS} == {CHAR.lower(), "third-fixture"}
    assert drive.sweep(now=time.monotonic() + FAR) == 2 and drive._WS == {}


def test_an_mcp_call_holds_its_workspace(env, monkeypatch):
    """tools.call is the scope: a sweep while play_sql runs closes nothing it uses."""
    seen = []
    real = drive.sql

    def sql_then_sweep(*a, **k):
        out = real(*a, **k)
        seen.append(drive.sweep(now=time.monotonic() + FAR))
        return out

    monkeypatch.setattr(drive, "sql", sql_then_sweep)

    class Store:
        vl_url, vl_archive_url, logs_filter, hot_days, stream = "http://unused", "", "feed:wow-*", 89, "feed:wow-*"

    got = tools.call(tools.Ctx(store=Store(), default_char=CHAR), "play_sql",
                     {"char": CHAR, "query": "SELECT COUNT(*) AS n FROM levels"})
    assert got["result"].startswith("n")
    assert seen == [0]
    assert drive.sweep(now=time.monotonic() + FAR) == 1  # released once the call returned


def test_the_limits_come_from_the_environment():
    assert drive.read_limits({}) == [drive.WORKSPACES_MAX, drive.WORKSPACE_IDLE_S]
    assert drive.read_limits({"ANALYSIS_WORKSPACES_MAX": "3", "ANALYSIS_WORKSPACE_IDLE_S": "0"}) == [3, 0]
    with pytest.raises(ValueError, match="ANALYSIS_WORKSPACES_MAX"):
        drive.read_limits({"ANALYSIS_WORKSPACES_MAX": "lots"})
