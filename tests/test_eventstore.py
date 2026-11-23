"""The event store (wowstats.eventstore): one way in and out for the events,
VictoriaLogs or the SQLite files. The queries themselves are checked on both
stores by tests/test_logsql.py, the degraded suite and the slices; here, what
is the store's own: how a line is kept, tenants, deletion, the settings, and
that nothing else talks to VictoriaLogs."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wowstats import admin, eventstore, ingest, stores, ui
from wowstats.stores import HISTORY_START

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 20, tzinfo=UTC)


def _store(tmp_path, tenant=0):
    where = f"sqlite:{tmp_path / 'events'}"
    return eventstore.get(where), stores.Store(where, account_id=tenant)


def _all(es, tenant=0) -> list[dict]:
    return es.query(tenant, "*", HISTORY_START, datetime.now(UTC), 10_000)


def test_nothing_but_the_event_store_speaks_to_victorialogs():
    """VictoriaLogs' API (its routes, the tenant headers) appears in
    wowstats/eventstore.py only: a call anywhere else would bypass the
    SQLite store, which then misses it silently."""
    api = re.compile(r"/select/logsql|/insert/jsonline|/delete/run_task|/internal/force_flush|[^f] tenant_headers\(")
    stray = []
    for p in sorted((ROOT / "wowstats").rglob("*.py")):
        if p.name == "eventstore.py":
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if api.search(line) and not line.lstrip().startswith("#"):
                stray.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not stray, "\n".join(stray)


def test_a_line_is_kept_as_victorialogs_keeps_it(tmp_path):
    es, _ = _store(tmp_path)
    lines, bad = ingest.parse(json.dumps({
        "_time": "2026-11-17T15:14:03.120+01:00", "feed": "wow-level", "char": "Testchar-Demo", "level": 12,
        "xp": 1.50, "on": True, "off": False, "none": None, "empty": "", "list": [1, "a"], "obj": {"b": 2}}).encode(),
        now=NOW)
    assert not bad and es.append(0, lines) == 1
    assert _all(es) == [{"_time": "2026-11-17T14:14:03.12Z", "feed": "wow-level", "char": "Testchar-Demo",
                         "level": "12", "xp": "1.5", "on": "true", "off": "false", "list": '[1,"a"]',
                         "obj": '{"b":2}', "component": "wow", "_msg": "wow-level Testchar-Demo"}]


def test_the_same_line_twice_is_stored_once_a_changed_record_is_kept(tmp_path):
    """A resent file adds nothing; a record sent again with a change (the
    current level's split, a higher rev) or a snapshot seen again later is
    a new line, as VictoriaLogs keeps it: stores.dedupe picks, not the store."""
    es, st = _store(tmp_path)
    split = {"_time": "2026-11-17T15:00:00Z", "feed": "wow-level", "char": "A-B", "source_id": "split:a:12",
             "level": 12, "rev": 1, "partial": True}
    assert es.append(0, [split]) == 1
    assert es.append(0, [split]) == 0
    assert es.append(0, [{**split, "rev": 2, "partial": False}]) == 1
    bags = {"_time": "2026-11-17T16:00:00Z", "feed": "wow-bags", "char": "A-B", "source_id": "bags:a:d1"}
    assert es.append(0, [bags, {**bags, "_time": "2026-11-18T16:00:00Z"}]) == 2
    got = st.vl_direct('feed:=wow-level char:="A-B" | fields source_id, rev')
    assert sorted(r["rev"] for r in got) == ["1", "2"]
    assert [r["rev"] for r in stores.dedupe(_all(es)) if r.get("rev")] == ["2"]


def test_each_tenant_reads_its_own_file_and_a_delete_removes_it(tmp_path):
    es, _ = _store(tmp_path)
    es.append(3, [{"_time": "2026-11-17T15:00:00Z", "feed": "wow-level", "char": "A-B"}])
    assert _all(es, 0) == [] and len(_all(es, 3)) == 1
    assert sorted(p.name for p in (tmp_path / "events").iterdir())[0] == "events-3.sqlite"
    out = es.delete(3)
    assert "events-3.sqlite" in out["deleted_files"] and _all(es, 3) == []
    assert not any(p.name.startswith("events-3.") for p in (tmp_path / "events").iterdir())
    # Written again later: a fresh file, nothing of the old one.
    es.append(3, [{"_time": "2026-11-18T15:00:00Z", "feed": "wow-level", "char": "C-D"}])
    assert [r["char"] for r in _all(es, 3)] == ["C-D"]


def test_the_query_range_and_limit_are_kept(tmp_path):
    es, _ = _store(tmp_path)
    es.append(0, [{"_time": f"2026-11-17T15:00:0{i}Z", "feed": "wow-x", "char": "A-B", "n": i} for i in range(6)])
    got = es.query(0, "feed:wow-* | fields n", datetime(2026, 11, 17, 15, 0, 1, tzinfo=UTC),
                   datetime(2026, 11, 17, 15, 0, 4, tzinfo=UTC), 2)
    assert [r["n"] for r in got] == ["1", "2"]
    assert es.query(0, "_time:>2026-11-17T15:00:04Z | fields n", HISTORY_START, NOW, 10) == [{"n": "5"}]


@pytest.mark.unanswerable_ok
def test_outside_the_subset_the_query_box_says_so(tmp_path):
    """The full view's LogsQL box: the asker's query, so a 400 with why,
    never the store's 502."""
    where = f"sqlite:{tmp_path / 'events'}"
    app = ui.App({"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "none", "EVENTS_STORE": where, "MODEL3D": "off",
                  "HOME": str(tmp_path), "ANALYSIS_STATE_DIR": str(tmp_path / "a")})
    with pytest.raises(ValueError, match="outside what the SQLite store answers"):
        app.views.logsql("* | top 3 by (zone)")
    assert app.views.logsql("* | stats count() as n") == [{"n": "0"}]


def test_settings(tmp_path):
    assert eventstore.events_location({}) == "http://victorialogs:9428"
    assert eventstore.events_location({"VL_URL": "http://vl:9428/"}) == "http://vl:9428"
    assert eventstore.events_location({"EVENTS_STORE": "vl", "VL_URL": "http://vl:9428"}) == "http://vl:9428"
    assert eventstore.events_location({"EVENTS_STORE": "sqlite:/data/events/", "VL_URL": "http://x"}) \
        == "sqlite:/data/events"
    for bad in ("sqlite:", "sqlite", "postgres://x"):
        with pytest.raises(ValueError, match="EVENTS_STORE"):
            eventstore.events_location({"EVENTS_STORE": bad})
    base = {"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "none", "HOME": str(tmp_path), "MODEL3D": "off"}
    with pytest.raises(ui.ConfigError, match="EVENTS_STORE"):
        ui.App({**base, "EVENTS_STORE": "mysql"})
    with pytest.raises(ui.ConfigError, match="VL_ARCHIVE_URL"):
        ui.App({**base, "EVENTS_STORE": f"sqlite:{tmp_path}", "VL_ARCHIVE_URL": "http://vl-archive:9428"})


def test_the_admin_page_checks_the_store_in_use(tmp_path):
    where = f"sqlite:{tmp_path / 'events'}"
    got = admin.checks({"EVENTS_STORE": where, "FRIENDS_VL_URL": "http://unused.invalid:9428"}, None)
    assert [c["name"] for c in got] == ["Store (SQLite files)"] and got[0]["ok"]
    assert eventstore.describe(where) == f"SQLite files in {tmp_path / 'events'}"
    assert eventstore.describe("http://u:p@vl:9428") == "VictoriaLogs at vl:9428"
