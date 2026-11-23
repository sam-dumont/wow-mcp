"""The analysis workspace follows new data (review of ce8239e, bug 3).

The workspace (the SQLite prep and its DuckDB child) was rebuilt only once
older than drive.CACHE_S (30 minutes): after an ingest the page showed the
new data within a minute while query_metric and play_sql answered from the
old snapshot, and a finding proposed then was judged on it. Now the next
request after the event cache saw the character's events change gets a new
build; a request already holding one keeps it to its end."""

from __future__ import annotations

import warnings

import pytest

from tests.test_layer_pages import SeedVL, seed_rows
from wowstats import eventcache, stores
from wowstats.analysis import drive, pages
from wowstats.mcp import tools as T
from wowstats.mcp.store import Store as MStore

CHAR = "Testchar-Demo"
warnings.filterwarnings("ignore", module="boring_semantic_layer")
DEATH = {"_time": "2026-11-17T20:10:00.000Z", "feed": "wow-recorder", "char": CHAR, "_msg": "death Duskwood",
         "source_id": "rec:test:fresh:1", "kind": "death", "zone": "Duskwood", "map_id": 1431, "level": 32,
         "x": 0.5, "y": 0.5, "demo": True}


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    fake = SeedVL(seed_rows(tmp_path))
    monkeypatch.setattr(stores, "http_get", fake)
    monkeypatch.setattr(MStore, "_get", lambda self, url, params, extra=None: fake(url, params))
    monkeypatch.setattr(eventcache, "_SHARED", {})
    for name, empty in (("_memo", type(pages._memo)()), ("_copies", type(pages._copies)()), ("_failing", {}),
                        ("_last_ok", None)):
        monkeypatch.setattr(pages, name, empty)
    monkeypatch.setattr(T, "_LAYERS", {})
    for k, v in {"ANALYSIS_STATE_DIR": str(tmp_path), "KB_GAME_TABLES": "off"}.items():
        monkeypatch.setenv(k, v)
    drive._WS.clear()
    yield fake
    for w in list(drive._WS.values()):
        w.worker.close()
    drive._WS.clear()


def ingest(fake: SeedVL, row: dict) -> None:
    """A new event lands, and the event cache's FRESH_S has passed."""
    fake.rows = sorted([*fake.rows, row], key=lambda r: stores.parse_t(r["_time"]))
    for c in eventcache._SHARED.values():
        c.expire()


def deaths_metric(ctx) -> int:
    got = T.call(ctx, "query_metric", {"char": CHAR, "metrics": ["deaths"]})
    return int(got["result"].strip().splitlines()[-1].split()[-1])


def test_new_events_reach_query_metric_on_the_next_request(seeded):
    ctx = T.Ctx(store=MStore(vl_url="http://vl.invalid"), default_char=CHAR)
    before = deaths_metric(ctx)
    assert before == T.call(ctx, "deaths", {"char": CHAR})["total"]  # the page's path agrees
    w = drive._WS[next(iter(drive._WS))]
    assert deaths_metric(ctx) == before and drive._WS[next(iter(drive._WS))] is w  # nothing new: same build

    ingest(seeded, DEATH)
    after = deaths_metric(ctx)
    assert after == before + 1  # long before CACHE_S
    assert after == T.call(ctx, "deaths", {"char": CHAR})["total"]
    assert drive._WS[next(iter(drive._WS))] is not w


def test_a_request_keeps_its_workspace_whatever_lands_meanwhile(seeded):
    env = {"ANALYSIS_STATE_DIR": drive.os.environ["ANALYSIS_STATE_DIR"], "KB_GAME_TABLES": "off",
           "VL_URL": "http://vl.invalid"}
    with drive.holding():
        w = drive.workspace(env, CHAR)
        ingest(seeded, DEATH)
        assert drive.workspace(env, CHAR) is w  # never swapped under a running request
    assert drive.workspace(env, CHAR) is not w  # the next request gets the new data
