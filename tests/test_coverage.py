"""Coverage: what an answer stood on (wowstats.coverage, review 4).

- derived per source from what is stored, never assumed;
- the Recorder's gaps are exact: a finished level's Polestar split (the
  game's played time) against the Recorder's sessions in it;
- one `coverage` on the page's /api/char, the journal's sessions, every MCP
  answer on one character, query_metric and play_sql;
- a missing source reads unknown, never 0 (the MCP's deaths and summary,
  the journal's estimated logouts, Back in time's deaths).

The page and MCP cases run on the demo (tests/degraded.py), with the
Recorder off for an hour of one evening ("recorder-gap")."""

from __future__ import annotations

import pytest

from tests import degraded as D
from wowstats import coverage, journal, missing, ui
from wowstats.analysis import drive, prep
from wowstats.mcp import tools as T
from wowstats.mcp.store import Store as MStore

pytestmark = pytest.mark.usefixtures("demo_clock")


def ts(s: str) -> float:
    return coverage._ts(s)


# ---------------------------------------------------------------- derivation per source


def snap(per: dict, last: dict, setup: dict | None = None, acct: list | None = None) -> dict:
    acct = sorted(set(acct or []) | {s for v in per.values() for s in v})
    return {"per": per, "acct": acct, "setup": setup, "last": last}


SETUP = {"at": "2026-11-18T09:00:00Z", "addons": {"recorder": "1", "polestar": "1", "syndicator": "1",
                                                   "questie": "1", "auctionator": "1"},
         "recorder_on": True, "saved_chars": 1, "off": {}, "shipper": "0.7.0"}


def test_every_source_from_what_arrived():
    c = "Testchar-Demo"
    s = snap({c: ["wow-recorder", "wow-level", "wow-gear", "quests:questie", "wow-character"]},
             {c: {"wow-recorder": "2026-11-18T09:05:00Z", "wow-level": "2026-11-17T17:13:18Z",
                  "wow-dungeon": None, "wow-gear": "2026-11-17T19:20:00Z", "quests:questie": "2026-11-17T19:20:00Z"},
              "": {"wow-prices": "2026-11-17T17:50:00Z"}},
             SETUP, acct=["wow-prices"])
    cov = coverage.build(s, c, [], {"as_of": ts("2026-11-18T08:00:00Z"), "fallback": False})
    assert cov == {
        "recorder": {"state": "complete", "as_of": "2026-11-18T09:05:00Z"},
        "polestar": {"state": "complete", "as_of": "2026-11-17T17:13:18Z"},
        "gear": {"state": "unavailable", "fix": cov["gear"]["fix"]},  # wow-bags is the signal, not wow-gear
        "quests": {"state": "complete", "as_of": "2026-11-17T19:20:00Z"},
        "prices": {"state": "complete", "as_of": "2026-11-17T17:50:00Z"},  # the account's
        "battlenet": {"state": "complete", "as_of": "2026-11-18T08:00:00Z"},
    }
    assert cov["gear"]["fix"] == missing.WAIT  # Syndicator installed: its data just hasn't come


def test_a_missing_source_names_its_fix_and_nothing_at_all_says_so():
    c = "Testalt-Demo"
    cov = coverage.build(snap({c: ["wow-level"]}, {c: {"wow-level": "2026-11-10T19:00:00Z"}}, None), c)
    assert cov["recorder"]["state"] == "unavailable" and "Recorder" in cov["recorder"]["fix"]
    assert cov["polestar"] == {"state": "complete", "as_of": "2026-11-10T19:00:00Z"}
    none = coverage.build(snap({}, {}, None), "Nobody-Demo")
    assert {v["state"] for k, v in none.items() if k != "battlenet"} == {"unavailable"}
    assert none["recorder"]["fix"] == missing.NOTHING["fix"]


def test_the_recorder_is_partial_with_a_gap_or_switched_off():
    c = "Testchar-Demo"
    base = snap({c: ["wow-recorder"]}, {c: {"wow-recorder": "2026-11-18T09:05:00Z"}}, SETUP)
    gap = [{"level": 28, "from": "2026-11-05T17:11:28Z", "to": "2026-11-05T18:45:24Z", "off_s": 4200}]
    cov = coverage.build(base, c, gap)
    assert cov["recorder"] == {"state": "partial", "as_of": "2026-11-18T09:05:00Z", "gaps": gap,
                               "fix": coverage.RECORDER_OFF_FIX}
    off = coverage.build({**base, "setup": {**SETUP, "recorder_on": False}}, c, [])
    assert off["recorder"]["state"] == "partial" and "gaps" not in off["recorder"]


def test_battlenet_states():
    assert coverage.battlenet(None) == {"state": "unavailable"}  # no exporter on this server
    assert coverage.battlenet({}) == {"state": "unavailable", "fix": coverage.BNET_NEVER}
    assert coverage.battlenet("unknown") == {"state": "unknown"}
    fb = coverage.battlenet({"as_of": ts("2026-11-18T06:00:00Z"), "fallback": True})
    assert fb == {"state": "partial", "as_of": "2026-11-18T06:00:00Z"}
    assert set(coverage.unknown()) == set(coverage.SOURCES)


# ---------------------------------------------------------------- the Recorder's gaps


LEVELS = [{"_time": "2026-11-05T10:00:00Z", "level": "27", "seconds": "9000"},
          {"_time": "2026-11-05T12:00:00Z", "level": "28", "seconds": "7200"},
          {"_time": "2026-11-05T14:00:00Z", "level": "29", "seconds": "7200"}]


def test_a_gap_is_a_level_longer_than_the_recorder_saw():
    # Level 28 ran 10:00-12:00 and took 7200 s; the Recorder saw 10:00-11:00.
    spans = [(ts("2026-11-05T09:00:00Z"), ts("2026-11-05T11:00:00Z")),
             (ts("2026-11-05T12:00:00Z"), ts("2026-11-05T14:00:00Z"))]
    assert coverage.recorder_gaps(spans, LEVELS) == [
        {"level": 28, "from": "2026-11-05T10:00:00Z", "to": "2026-11-05T12:00:00Z", "off_s": 3600}]
    # Under 15 minutes is rounding, not a gap.
    near = [(ts("2026-11-05T09:00:00Z"), ts("2026-11-05T11:50:00Z")), spans[1]]
    assert coverage.recorder_gaps(near, LEVELS) == []
    # A level that began before the Recorder's first record is its "on since".
    late = [(ts("2026-11-05T11:30:00Z"), ts("2026-11-05T14:00:00Z"))]
    assert [g["level"] for g in coverage.recorder_gaps(late, LEVELS)] == []
    # A partial split (tracking began in the level) is short by nature.
    part = [dict(r, partial="true") if r["level"] == "28" else r for r in LEVELS]
    assert coverage.recorder_gaps(spans, part) == []
    assert coverage.recorder_gaps([], LEVELS) == []


def test_a_gap_goes_to_the_newest_session_of_its_level_once():
    spans = [(ts("2026-11-05T09:00:00Z"), ts("2026-11-05T10:30:00Z")),
             (ts("2026-11-05T11:00:00Z"), ts("2026-11-05T11:20:00Z")),
             (ts("2026-11-05T12:00:00Z"), ts("2026-11-05T14:00:00Z"))]
    gaps = coverage.recorder_gaps(spans, LEVELS)
    assert coverage.attach(gaps, spans) == {1: {"off_s": gaps[0]["off_s"], "levels": [28]}}


def test_the_demo_has_no_gap():
    """The full demo: every level's split equals the Recorder's time."""
    rows = D.base_rows()
    assert coverage.from_rows(rows, D.CHAR)["recorder"]["state"] == "complete"


def test_from_raw_rows_finds_the_gap():
    cov = coverage.from_rows(D.rows_for("recorder-gap"), D.CHAR)
    rec = cov["recorder"]
    assert rec["state"] == "partial" and [g["level"] for g in rec["gaps"]] == [28]
    assert 60 * 60 <= rec["gaps"][0]["off_s"] <= 75 * 60
    assert "battlenet" not in cov and "prices" not in cov  # the prepared tables hold neither


# ---------------------------------------------------------------- the page and the MCP with a gap


@pytest.fixture(scope="module")
def gap(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("recorder-gap"))
    tmp = tmp_path_factory.mktemp("gap")
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "MODEL3D": "off",
           "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp), "WOW_ITEMS": "off"}
    T._MISSING.clear()
    with D.clock():
        yield env, ui.App(env)
    vl.close()
    mp.undo()
    T._MISSING.clear()


def test_the_page_says_what_the_recorder_missed(gap):
    env, app = gap
    cov = app.api_char(D.CHAR)["coverage"]
    assert cov["recorder"]["state"] == "partial" and [g["level"] for g in cov["recorder"]["gaps"]] == [28]
    assert cov["battlenet"] == {"state": "unavailable"}  # no vm-wow on this server
    sessions = app.api_journal(D.CHAR)["sessions"]
    marked = [s for s in sessions if s.get("recorder_off")]
    # Once, on the newest session of level 28 (the evening after the gap).
    assert len(marked) == 1 and marked[0]["recorder_off"]["levels"] == [28]
    assert marked[0]["start"].startswith("2026-11-05T18:3")


def test_the_mcp_answer_carries_the_coverage(gap):
    env, _ = gap
    ctx = T.Ctx(store=MStore(vl_url=env["VL_URL"]), default_char=D.CHAR, env=env)
    d = T.call(ctx, "deaths", {"char": D.CHAR})
    assert d["coverage"]["recorder"]["state"] == "partial" and d["total"] is not None
    s = T.call(ctx, "sessions", {"char": D.CHAR, "since": "2026-11-01", "until": "2026-11-07", "limit": 20})
    marked = [x for x in s["sessions"] if x.get("recorder_off")]
    assert [x["recorder_off"]["in_levels"] for x in marked] == [[28]]
    # An answer that isn't about one character carries none.
    assert "coverage" not in T.call(ctx, "kb_topics", {})


def test_query_metric_and_play_sql_carry_the_coverage(gap, monkeypatch, tmp_path):
    rows = [r for r in D.rows_for("recorder-gap") if r.get("char") in (D.CHAR, None)]
    monkeypatch.setattr(prep, "query", lambda store, stream, char: [dict(r) for r in rows])
    monkeypatch.setenv("ANALYSIS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KB_GAME_TABLES", "off")
    drive._WS.clear()

    class Fake:
        vl_url, vl_archive_url, logs_filter, hot_days, stream = "http://unused", "", "feed:wow-*", 89, "feed:wow-*"

    ctx = T.Ctx(store=Fake(), default_char=D.CHAR)
    m = T.call(ctx, "query_metric", {"char": D.CHAR, "metrics": ["deaths"]})
    assert m["coverage"]["recorder"]["state"] == "partial"
    q = T.call(ctx, "play_sql", {"char": D.CHAR, "query": "SELECT COUNT(*) AS n FROM levels"})
    assert q["coverage"] == m["coverage"]
    assert "coverage" not in T.call(ctx, "play_sql", {"char": D.CHAR, "query": "SELECT 1 AS n", "page": 2})
    drive._WS.clear()


# ---------------------------------------------------------------- zero vs unknown vs unavailable


@pytest.fixture(scope="module")
def no_recorder(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("no-recorder"))
    tmp = tmp_path_factory.mktemp("norec")
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "MODEL3D": "off",
           "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp), "WOW_ITEMS": "off"}
    T._MISSING.clear()
    with D.clock():
        yield env, ui.App(env)
    vl.close()
    mp.undo()
    T._MISSING.clear()


def test_mcp_deaths_without_the_recorder_are_unknown(no_recorder):
    env, _ = no_recorder
    ctx = T.Ctx(store=MStore(vl_url=env["VL_URL"]), default_char=D.CHAR, env=env)
    d = T.call(ctx, "deaths", {"char": D.CHAR})
    assert d["total"] is None and "unknown" in d["note"]
    assert d["coverage"]["recorder"]["state"] == "unavailable" and "Recorder" in d["coverage"]["recorder"]["fix"]
    assert T.call(ctx, "character_summary", {"char": D.CHAR})["deaths"] is None


def test_estimated_logouts_have_no_death_count(no_recorder):
    _, app = no_recorder
    est = [s for s in app.api_journal(D.CHAR)["sessions"] if s.get("estimated")]
    assert est and all(s["deaths"]["total"] is None for s in est)
    assert app.api_char(D.CHAR)["coverage"]["recorder"]["state"] == "unavailable"


def test_back_in_time_deaths_without_the_recorder_are_not_recorded(no_recorder):
    _, app = no_recorder
    sn = app.snapshot(D.CHAR, 30)
    assert sn["deaths"] is None


def test_estimated_logouts_from_the_journal_itself():
    snaps = [{"_time": "2026-11-05T10:00:00Z", "level": "27", "xp": "0", "money_copper": "100"},
             {"_time": "2026-11-05T12:00:00Z", "level": "28", "xp": "0", "money_copper": "300"}]
    levels = [{"_time": "2026-11-05T11:00:00Z", "level": "27", "seconds": "3600", "xp": "1000"}]
    out = journal.by_logout(snaps, levels, [])
    assert out and out[0]["deaths"]["total"] is None


# ---------------------------------------------------------------- a finished level with no split


def test_a_finished_level_without_a_split_keeps_its_row():
    """Level 32 finished with no Polestar split and no Recorder ding out of
    it, its records still there: the prep keeps a row for it, done and
    with nothing Polestar counted (NULL, never 0), so no total over the
    climb skips it silently."""
    db = prep.build(D.rows_for("split-missing"), D.CHAR)
    got = db.execute("SELECT level, ding_t, played_s, partial, in_play FROM levels WHERE level = 32").fetchall()
    assert got == [(32, None, None, None, 0)]
    lv = [r[0] for r in db.execute("SELECT level FROM levels ORDER BY level")]
    assert lv == list(range(lv[0], lv[-1] + 1)), "no level between the first and the one in play is left out"


def test_a_finished_level_without_a_split_makes_polestar_partial():
    cov = coverage.from_rows(D.rows_for("split-missing"), D.CHAR)
    assert cov["polestar"]["state"] == "partial" and cov["polestar"]["gaps"] == [{"level": 32}]
    # The level in play has no split yet by design, and the full demo has every one.
    assert coverage.from_rows(D.base_rows(), D.CHAR)["polestar"]["state"] == "complete"


def test_the_mcp_says_the_split_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("split-missing"))
    T._MISSING.clear()
    try:
        env = {"VL_URL": vl.url, "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp_path),
               "ANALYSIS_STATE_DIR": str(tmp_path), "WOW_ITEMS": "off"}
        ctx = T.Ctx(store=MStore(vl_url=vl.url), default_char=D.CHAR, env=env)
        with D.clock():
            for tool in ("levels", "character_summary"):
                pol = T.call(ctx, tool, {"char": D.CHAR})["coverage"]["polestar"]
                assert pol["state"] == "partial" and pol["gaps"] == [{"level": 32}], tool
    finally:
        vl.close()
        T._MISSING.clear()


def test_the_levels_tool_lists_the_level_without_a_split(tmp_path, monkeypatch):
    """Level 32 has no split: `levels` lists it with its time and counts
    unknown (null, never 0), and the window averages that cross it are
    unknown too, instead of skipping it (owner, 2026-11-23)."""
    monkeypatch.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("split-missing"))
    T._MISSING.clear()
    try:
        env = {"VL_URL": vl.url, "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp_path),
               "ANALYSIS_STATE_DIR": str(tmp_path), "WOW_ITEMS": "off"}
        ctx = T.Ctx(store=MStore(vl_url=vl.url), default_char=D.CHAR, env=env)
        with D.clock():
            out = T.call(ctx, "levels", {"char": D.CHAR})
            s = T.call(ctx, "character_summary", {"char": D.CHAR})
        row = next(lv for lv in out["levels"] if lv["level"] == 32)
        assert row["seconds"] is None and row["dur"] is None and row["xp"] is None, row
        assert row["quests"] is None and row["kills"] is None and row["partial"] is False, row
        assert [lv["level"] for lv in out["levels"]] == sorted({lv["level"] for lv in out["levels"]})
        assert s["level"] - 1 - 32 < 3, "the fixture's window holds level 32"
        assert s["avg_last_5_levels"] is None and s["xp_per_hour_last_3_levels"] is None, s
        assert s["eta_next_level"] is None
        assert out["coverage"]["polestar"]["gaps"] == [{"level": 32}]
    finally:
        vl.close()
        T._MISSING.clear()


def test_deaths_in_a_window_the_recorder_never_saw_say_so(gap):
    env, _ = gap
    ctx = T.Ctx(store=MStore(vl_url=env["VL_URL"]), default_char=D.CHAR, env=env)
    d = T.call(ctx, "deaths", {"char": D.CHAR, "since": "2026-10-01", "until": "2026-10-08"})
    assert d["total"] is None and d["note"] == "The Recorder wasn't on for this period."


def test_deaths_in_a_window_the_recorder_never_saw_are_unknown(gap):
    """The history starts on 2026-10-30: the week before it has no Recorder
    record, so its deaths are unknown, never 0; the whole history has some."""
    env, _ = gap
    ctx = T.Ctx(store=MStore(vl_url=env["VL_URL"]), default_char=D.CHAR, env=env)
    d = T.call(ctx, "deaths", {"char": D.CHAR, "since": "2026-10-01", "until": "2026-10-08"})
    assert d["total"] is None and d["by_level"] == {}
    assert T.call(ctx, "deaths", {"char": D.CHAR})["total"] > 0


def test_the_page_says_the_split_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("split-missing"))
    try:
        env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "MODEL3D": "off",
               "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp_path), "ANALYSIS_STATE_DIR": str(tmp_path), "WOW_ITEMS": "off"}
        with D.clock():
            pol = ui.App(env).api_char(D.CHAR)["coverage"]["polestar"]
        assert pol["state"] == "partial" and pol["gaps"] == [{"level": 32}]
    finally:
        vl.close()
