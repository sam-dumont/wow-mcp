"""The views per grain (semantic.yaml `views`: per_level, per_session,
per_zone, per_dungeon, per_trip, per_slot): compiled from the metrics, never hand SQL.

Three histories: the demo seed (dev/seed.py, Testchar-Demo: no gear sheet),
the docs site's demo character (tests/degraded.py, with its gear advice, so
per_slot has rows) and a seeded variation of it (tests/invariants/histories.py:
records on one millisecond across the digit boundaries). On each, every column
of every view equals query_metric for the same metric at the view's entity,
and no key comes out twice. Guarantee 24 in docs/guarantees.md."""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from tests import degraded as D
from tests.invariants import histories as H
from wowstats import analysis
from wowstats.analysis import TABLES, drive, duck, prep, semantic
from wowstats.analysis.sql import QueryError
from wowstats.mcp import route, tools
from wowstats.plan import facts

ROOT = Path(__file__).resolve().parents[2]
SEED_CHAR = "Testchar-Demo"
warnings.filterwarnings("ignore", module="boring_semantic_layer")


def _seed_rows(tmp: Path) -> list[dict]:
    out = tmp / "seed.ndjson"
    subprocess.run([sys.executable, str(ROOT / "dev" / "seed.py"), "--out", str(out)], check=True,
                   capture_output=True)
    return [json.loads(x) for x in out.read_text().splitlines() if x.strip()]


@pytest.fixture(scope="module", params=["seed", "demo", "variant"])
def built(request, tmp_path_factory):
    if request.param == "seed":
        rows = [r for r in _seed_rows(tmp_path_factory.mktemp("seed")) if r.get("char") == SEED_CHAR]
        char = SEED_CHAR
    elif request.param == "demo":
        rows, char = D.base_rows(), D.CHAR
    else:
        rows, char = H.rows_of(7), H.CHAR
    db = prep.build([dict(r) for r in rows], char, route.reference(), advice=analysis.advice_for(rows, char, {}))
    con = duck.mirror(db, TABLES)
    semantic.prepare(con)
    return {"name": request.param, "db": db, "con": con, "layer": semantic.Layer(con), "ro": duck.ReadOnlyDuck(con)}


def view_rows(b, name: str) -> list[dict]:
    cols, rows, cut = b["ro"].run(f"SELECT * FROM {name}", 10_000)
    assert not cut
    return [dict(zip(cols, r, strict=True)) for r in rows]


def same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


# ---------------------------------------------------------------- the guarantees


def test_every_view_column_equals_query_metric_at_its_grain(built):
    """Each metric of a view, asked alone through query_metric grouped by the
    view's entity (with the view's where): the same value on every key. A
    key the metric has no row for is NULL in the view (0 for an evidence
    count), never a made-up number."""
    checked = 0
    for name, v in semantic.views().items():
        got = {r[v["entity"]]: r for r in view_rows(built, name)}
        for m in v["metrics"]:
            ans = semantic.run(built["layer"], built["ro"], {"metrics": [m], "group_by": [v["entity"]],
                                                             "where": v["where"], "limit": semantic.MAX_LIMIT})
            assert not ans["more"], (name, m)
            want = {r[0]: dict(zip(ans["columns"], r, strict=True)) for r in ans["rows"]}
            for key, row in got.items():
                for col in [c for c in ans["columns"] if c != v["entity"]]:
                    expect = want[key][col] if key in want else (0 if col.endswith("_evidence") else None)
                    assert same(row[col], expect) or row[col] is expect, (built["name"], name, key, col,
                                                                          row[col], expect)
                    checked += 1
            assert set(want) <= set(got), (name, m)
    assert checked > 200


def test_no_fan_out_one_row_per_grain_key(built):
    """A view has exactly one row per key of its entity: the keys any of its
    metrics has, each once (a dimension that held two values for one key
    would split it)."""
    for name, v in semantic.views().items():
        rows = view_rows(built, name)
        keys = [r[v["entity"]] for r in rows]
        union = set()
        for m in v["metrics"]:
            ans = semantic.run(built["layer"], built["ro"], {"metrics": [m], "group_by": [v["entity"]],
                                                             "where": v["where"], "limit": semantic.MAX_LIMIT})
            union |= {r[0] for r in ans["rows"]}
        assert len(keys) == len(set(keys)) == len(union), (built["name"], name)
        assert None not in union, name
    assert built["name"] == "seed" or view_rows(built, "per_slot"), "the demo's gear gives per_slot rows"


def test_recorder_coverage_is_a_plain_column_of_per_level(built):
    """Played time the Recorder saw, per level: (played_s - recorder_missed_s)
    / played_s where the gap is measured, NULL (unknown) where it isn't."""
    lv = {r["level"]: r for r in view_rows(built, "per_level")}
    raw = {lvl: (p, m) for lvl, p, m in built["db"].execute("SELECT level, played_s, recorder_missed_s FROM levels")}
    measured = 0
    for level, row in lv.items():
        p, m = raw.get(level, (None, None))
        if m is None or not p:
            assert row["recorder_coverage"] is None, level
        else:
            assert row["recorder_coverage"] == pytest.approx((p - m) / p), level
            measured += 1
    assert measured >= 3
    assert lv[max(lv)]["recorder_coverage"] is None  # the level in play: no split, unknown


def test_the_step_metrics_recount_polestars_step_records(built):
    """per_guide's step columns (records model, semantic wave 3) say what the
    guide_steps tool said when it read the events itself: steps done
    (picks left out), Polestar's step timer, the explicit skips plus the
    steps a record jumped over (2 to 25 ahead of the guide's previous step
    record, picks included), the picks. Recounted here from the prep's
    step records with that rule."""
    want: dict[str, dict] = {}
    last: dict[str, int] = {}
    for g, kind, step, reason, secs in built["db"].execute(
            "SELECT guide, kind, step, reason, seconds FROM records WHERE kind IN ('step', 'skip') AND guide IS NOT "
            "NULL ORDER BY t, rec_id"):
        w = want.setdefault(g, {"steps_done": 0, "step_s": 0.0, "skipped": 0, "step_picks": 0})
        if kind == "skip":
            w["skipped"] += 1
            continue
        if reason == "pick" or step is None:
            w["step_picks"] += 1
            if step is not None:
                last[g] = step
            continue
        w["steps_done"] += 1
        w["step_s"] += secs or 0
        if g in last and last[g] + 1 < step <= last[g] + 25:
            w["skipped"] += step - last[g] - 1
        last[g] = step
    got = {r["guide"]: r for r in view_rows(built, "per_guide")}
    for g, w in want.items():
        r = got[g]
        assert (r["steps_done"], r["steps_skipped"] + r["steps_jumped"], r["step_picks"]) == (
            w["steps_done"], w["skipped"], w["step_picks"]), (built["name"], g)
        assert same(r["step_s"], w["step_s"]), (built["name"], g)
    assert built["name"] == "seed" or any(w["skipped"] for w in want.values()), "the demo jumps over steps"


def test_the_interval_joins_keep_one_row_per_record_and_reading(built):
    """sem_records and sem_xp take the guide step of the online interval a
    record closes ((t0, t1]): one interval at most, so no record or XP
    reading is lost or counted twice, and the XP by guide and step adds up
    to the bar's whole gain inside the intervals."""
    ro = built["ro"]
    for model, table in (("sem_records", "records"), ("sem_xp", "xp")):
        _, got, _ = ro.run(f"SELECT (SELECT COUNT(*) FROM {model}), (SELECT COUNT(*) FROM {table})", 1)
        assert got[0][0] == got[0][1], (built["name"], model)
    total = sum(r["xp_gained"] or 0 for r in semantic.rows(built["layer"], ro, {"metrics": ["xp_gained"]}))
    _, raw, _ = ro.run("SELECT COALESCE(SUM(xp_gained), 0) FROM xp", 1)
    assert total == raw[0][0]
    by_step = semantic.rows(built["layer"], ro, {"metrics": ["xp_gained"], "group_by": ["guide", "step"]})
    assert sum(r["xp_gained"] or 0 for r in by_step) == total


def test_per_slot_says_worn_against_best_upgrade():
    """The demo priest's head: the Shadow-scored Azure Silk Hood (made for
    another class) at 17%, the Embalmed Shroud at 100% wearable now: the
    page's '17% → 100% on your head'. Rings: two worn, the weaker named."""
    rows = D.base_rows()
    db = prep.build([dict(r) for r in rows], D.CHAR, route.reference(), advice=analysis.advice_for(rows, D.CHAR, {}))
    layer, ro = semantic.local(db)
    cols, got, _ = ro.run("SELECT * FROM per_slot", 100)
    slot = {r[0]: dict(zip(cols, r, strict=True)) for r in got}
    head = slot["Head"]
    assert (head["worn_item"], head["worn_fit"], head["upgrade_bis_pct_worn"]) == ("Azure Silk Hood", "class", 17)
    assert (head["best_upgrade"], head["best_upgrade_bis_pct"]) == ("Embalmed Shroud", 100)
    assert slot["Finger"]["worn_items"] == 2
    weaker = db.execute("SELECT name FROM gear WHERE slot_name = 'Finger' ORDER BY score, slot LIMIT 1").fetchone()[0]
    assert slot["Finger"]["worn_item"] == weaker


def test_a_metric_that_cant_be_read_at_the_grain_is_refused(monkeypatch):
    """played_s has no slot: a view asking for it fails when the catalogue
    loads, never ships a faked column."""
    bad = copy.deepcopy(semantic._file())
    bad["views"]["per_slot"]["metrics"].append("played_s")
    monkeypatch.setattr(semantic, "_file", lambda: bad)
    semantic.views.cache_clear()
    try:
        with pytest.raises(ValueError, match="played_s can't be read by slot"):
            semantic.views()
    finally:
        semantic.views.cache_clear()


def _old_progress(db) -> list[dict]:
    """The plan's review series as facts.progress computed it with its own
    SQL before it read the layer (semantic.yaml `progress`): each XP reading,
    and the timeline intervals in start order up to the first one the
    reading hadn't seen end."""
    segs = db.execute("SELECT t0, t1, dur_s FROM timeline ORDER BY t0").fetchall()
    out, on, i = [], 0.0, 0
    for t, level, xp, xp_max in db.execute("SELECT t, level, xp, xp_max FROM xp WHERE level IS NOT NULL ORDER BY t"):
        while i < len(segs) and segs[i][1] <= t:
            on += segs[i][2] or 0
            i += 1
        out.append({"t": t, "level": round(level + (xp or 0) / xp_max, 3) if xp_max else float(level),
                    "online_s": round(on)})
    return out


def test_the_progress_series_is_the_plans_old_series_and_query_metric_by_day(built):
    """The level over time (semantic.yaml `progress`, what a plan is reviewed
    against): read at its reading grain it is exactly the series the plan
    built with its own SQL (the XP readings, and the snapshots apart); by
    day, query_metric gives each day's last reading, its level and the
    online total then."""
    sem = (built["layer"], built["ro"])
    assert facts.progress(built["db"], sem) == _old_progress(built["db"]) != []
    snaps = built["db"].execute("SELECT t, level, xp, xp_max FROM snapshots WHERE level IS NOT NULL "
                                "ORDER BY t").fetchall()
    got = facts.series(sem, "snapshot")
    assert [(r["progress_t"], r["level"], r["progress_xp"], r["progress_online_s"]) for r in got] == [
        (t, lv, xp, None) for t, lv, xp, _ in snaps]
    assert [r["progress_level"] for r in got] == [lv + (xp or 0) / mx if mx else float(lv) for _, lv, xp, mx in snaps]
    # By day: query_metric's answer is the series' last reading of each local day.
    xp = [{"dimension": "source", "op": "=", "value": "xp"}]
    days = semantic.rows(*sem, {"metrics": ["progress_t"], "group_by": ["reading"], "grain": "day", "where": xp})
    reading = {r["reading"]: r for r in facts.series(sem, "xp")}
    last: dict = {}
    for r in days:
        if r["reading"] > last.get(r["day"], 0):
            last[r["day"]] = r["reading"]
    ans = semantic.run(*sem, {"metrics": ["progress_level", "progress_online_s", "progress_readings"],
                              "grain": "day", "where": xp, "limit": semantic.MAX_LIMIT})
    assert not ans["more"] and len(ans["rows"]) == len(last)
    for day, level, online, n in ans["rows"]:
        want = reading[last[str(day)]]
        assert (level, online) == (want["progress_level"], want["progress_online_s"]), (built["name"], day)
        assert n == sum(1 for r in days if r["day"] == str(day))


def test_views_hold_in_the_query_process(built):
    """play_sql runs in a child process (wowstats.analysis.duckproc): the
    views are there too, with the same rows."""
    from wowstats.analysis import duckproc

    w = duckproc.Worker(built["db"], TABLES)
    try:
        play = duck.ReadOnlyDuck(built["con"], tables={*TABLES, *semantic.views()}, worker=w)
        for name in semantic.views():
            assert play.run(f"SELECT * FROM {name}", 1000) == built["ro"].run(f"SELECT * FROM {name}", 1000), name
        with pytest.raises(QueryError):
            play.run("SELECT * FROM sem_levels")  # the model views stay query_metric's
    finally:
        w.close()


# ---------------------------------------------------------------- over MCP


class DemoStore:
    vl_url, vl_archive_url, logs_filter, hot_days, stream = "http://unused", "", "feed:wow-*", 89, "feed:wow-*"
    account_id = 0

    def logs(self, *a, **k):
        return []


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    rows = D.base_rows()
    monkeypatch.setattr(prep, "query", lambda store, stream, char: [dict(r) for r in rows if r.get("char") == char])
    monkeypatch.setenv("ANALYSIS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KB_GAME_TABLES", "off")
    drive._WS.clear()
    yield DemoStore()
    drive._WS.clear()


def call(store, name, args):
    return tools.call(tools.Ctx(store=store, default_char=D.CHAR), name, args)


def test_list_metrics_lists_the_views_and_play_sql_reads_them_first(lab):
    cat = call(lab, "list_metrics", {})
    names = [v["name"] for v in cat["views"]]
    assert names == ["per_level", "per_session", "per_zone", "per_dungeon", "per_trip", "per_guide", "per_quest",
                     "per_slot"]
    level = next(v for v in cat["views"] if v["name"] == "per_level")
    assert level["one_row_per"] == "level" and "recorder_coverage" in [c["name"] for c in level["columns"]]
    desc = next(t for t in tools.TOOLS if t.name == "play_sql").description
    assert desc.index("per_level") < desc.index("raw tables")
    # The schema is paged (drive.PAGE_CHARS): the views fill the first page or more.
    first = call(lab, "analysis_brief", {"char": D.CHAR, "section": "schema"})
    schema = first["text"] + "".join(
        call(lab, "analysis_brief", {"char": D.CHAR, "section": "schema", "page": p})["text"]
        for p in range(2, first["pages"] + 1))
    assert schema.startswith("# Views per grain") and schema.index("## per_slot") < schema.index("# Raw tables")
    out = call(lab, "play_sql", {"char": D.CHAR, "query": "SELECT level, recorder_coverage FROM per_level "
                                                          "WHERE level = 30"})
    assert out["result"].splitlines() == ["level\trecorder_coverage", "30\t1"]
    # Read through a view, the tables under it count as read for the run's coverage.
    run = drive.current(dict(os.environ), prep.norm(D.CHAR))
    assert {"levels", "records", "timeline", "snapshots"} <= set(run["touched"])
