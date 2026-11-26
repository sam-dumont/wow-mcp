"""`as_of` on every answer that reads a character's data: the newest event of
the snapshot it was computed from (review, 2026-11-23: the page and the AI
could answer from different data, and nothing in an answer said which).

The MCP reads through the page's event cache (wowstats.mcp.store), so both
sides stand on one snapshot; these tests run every tool on that real path
(the fixture's stores over HTTP, nothing patched)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.mcp import fakes
from wowstats import analysis, eventcache, plan, ui
from wowstats.analysis import copies, drive
from wowstats.mcp import tools
from wowstats.mcp.store import Store

CHAR = fakes.CHAR
NEWEST = max(datetime.fromisoformat(r["_time"].replace("Z", "+00:00")) for r in fakes.RAW).astimezone(UTC).strftime(
    "%Y-%m-%dT%H:%M:%SZ")

# Every tool that reads the character's events, with what it needs first.
BRIEF = ("analysis_brief", {"goals": "Gear for dungeons."})
READERS = [
    ("character_summary", {}, ()), ("levels", {}, ()), ("sessions", {"since": "2026-11-01"}, ()), ("zones", {}, ()),
    ("guide_steps", {}, ()), ("deaths", {}, ()), ("dungeons", {}, ()), ("dungeons", {"options": True}, ()),
    ("players", {}, ()), ("players", {"groups": True}, ()), ("inventory", {}, ()), ("gear", {}, ()), ("gear", {"options": True}, ()),
    ("professions", {}, ()),
    ("next_moves", {}, ()), ("sanity_check", {}, ()), ("route_reference", {}, ()), ("class_guide", {}, ()),
    ("class_meta", {}, ()), ("talent_changes", {}, ()), ("news", {}, ()), ("dashboard_refresh", {}, ()),
    ("plan_session", {"hours": 2}, ()), ("next_step", {}, ()), ("mount_goal", {}, ()), ("how_did_my_plan_go", {}, ()),
    ("check_plan", {"text": "No dungeons."}, (("plan_session", {"hours": 2}),)),
    ("analyse", {"goals": "Gear for dungeons."}, ()), BRIEF + ((),),
    ("analysis_progress", {}, (BRIEF,)), ("play_sql", {"query": "SELECT COUNT(*) AS n FROM levels"}, (BRIEF,)),
    ("query_metric", {"metrics": ["played_s"], "group_by": ["level"]}, (BRIEF,)),
    ("propose_finding", {"finding": {"key": "levels-count", "title": "{n} levels", "claim": "You have {n} levels.",
                                     "sql": "SELECT COUNT(*) AS n FROM levels", "relevance": 1, "action": None}},
     (BRIEF,)),
    ("analysis_submit", {"pass": "survey", "answer": {"questions": [
        {"question": f"How long does level {n} take?", "tables": ["levels"]} for n in range(10, 22)]}}, (BRIEF,)),
]
# Answers that read no event of the character: the knowledge base, the
# metric catalogue, the account's auction scans (not a character's history).
NOT_READERS = [("data_status", {}), ("list_metrics", {}), ("kb_search", {"query": "riding"}), ("kb_get", {"ids": ["note:xp-sources"]}),
               ("kb_lookup", {"ids": ["quest:56"]}), ("kb_rule", {"name": "riding.apprentice"}), ("kb_topics", {}),
               ("prices", {}), ("market", {})]


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    """The fixture's stores, every process cache empty (each answer here is
    the first of its process)."""
    for mod, name in ((eventcache, "_SHARED"), (tools, "_MISSING"), (tools, "_VIEWS"),
                      (plan, "_CACHE"), (drive, "_WS")):
        monkeypatch.setattr(mod, name, {})
    copies.clear()
    for k, v in {"ANALYSIS_STATE_DIR": str(tmp_path / "state"), "KB_GAME_TABLES": "off"}.items():
        monkeypatch.setenv(k, v)
    raw = list(fakes.RAW)
    # The page's "anything new?" check (ui.App.data_mark), over the same events.
    mark = ("| stats max(_time) as t, count() as n",
            lambda q: [{"t": max(r["_time"] for r in raw), "n": str(len(raw))}])
    # And its character list (with counts: the MCP's fakes have none).
    chars = ("stats by (char) count() as events", [{**c, "events": "120"} for c in fakes.CHARS])
    fake = fakes.FakeStores(raw=raw, logs=[mark, chars, *fakes.LOGS])
    ctx = tools.Ctx(store=Store(vl_url=fake.url, vm_url=fake.url), default_char=CHAR, bnet=True)
    yield ctx, raw, fake, tmp_path
    fake.close()


def call(ctx, name, args):
    spec = tools.BY_NAME.get(name, tools.MARKET)
    return tools.call(ctx, name, {"char": CHAR, **args} if "char" in spec.input_schema["properties"] else args,
                      tools.mcp_tools(ctx))


@pytest.mark.parametrize(("name", "args", "before"), READERS, ids=[f"{n}-{'-'.join(a)}" for n, a, _ in READERS])
def test_every_character_answer_says_the_data_it_read(lab, name, args, before):
    ctx = lab[0]
    for n, a in before:
        call(ctx, n, a)
    out = call(ctx, name, args)
    assert out["as_of"] == NEWEST, (name, {k: out[k] for k in list(out)[:6]})


def test_every_tool_is_sorted():
    names = {n for n, *_ in READERS} | {n for n, _ in NOT_READERS} | {"play_analysis", "whats_new"}
    assert names == {t.name for t in (*tools.TOOLS, tools.MARKET)}


@pytest.mark.parametrize(("name", "args"), NOT_READERS, ids=[n for n, _ in NOT_READERS])
def test_an_answer_that_read_no_event_has_no_as_of(lab, name, args):
    assert "as_of" not in call(lab[0], name, args)


def test_kept_findings_say_their_builds_data_and_old_ones_say_nothing(lab):
    ctx, _, fake, home = lab
    call(ctx, *BRIEF)
    n = int(call(ctx, "play_sql", {"query": "SELECT COUNT(*) AS n FROM levels"})["result"].split()[-1])
    finding = next(a for name, a, _ in READERS if name == "propose_finding")["finding"]
    assert call(ctx, "propose_finding", {"finding": {**finding, "expect": {"n": n}}})["stored"]
    # No run has finished yet: the findings say theirs, the state nothing it never had.
    got = call(ctx, "play_analysis", {})
    assert got["findings"][0]["as_of"] == NEWEST and "as_of" not in got
    assert "as_of" not in call(ctx, "whats_new", {})
    # The nightly recheck reads the events: the state and every finding it ran say that data.
    env = {"VL_URL": fake.url, "DEFAULT_CHAR": CHAR, "ANALYSIS_STATE_DIR": str(home / "state"),
           "KB_GAME_TABLES": "off"}
    assert analysis.run(env, CHAR)["as_of"] == NEWEST
    got = call(ctx, "play_analysis", {})
    assert got["as_of"] == NEWEST and got["findings"][0]["as_of"] == NEWEST
    assert call(ctx, "whats_new", {})["as_of"] == NEWEST
    # A state kept before as_of: nothing invented.
    s = analysis.load_state(env, CHAR)
    analysis.save_state(env, CHAR, {k: v for k, v in s.items() if k != "as_of"}
                        | {"findings": {k: {x: y for x, y in f.items() if x != "as_of"}
                                        for k, f in s["findings"].items()}})
    got = call(ctx, "play_analysis", {})
    assert "as_of" not in got and "as_of" not in got["findings"][0]


def test_a_new_event_moves_the_page_and_every_answer_to_the_same_data(lab):
    ctx, raw, fake, home = lab
    app = ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": fake.url, "MODEL3D": "off",
                  "DEFAULT_CHAR": CHAR, "HOME": str(home / "ui")})
    assert app.prebuilt.check() == "first build"
    asked = [("deaths", {}), ("dungeons", {}), ("gear", {}), ("players", {}),
             ("query_metric", {"metrics": ["played_s"], "group_by": ["level"]})]
    call(ctx, *BRIEF)
    assert app.api_char(CHAR)["as_of"] == NEWEST
    assert {n: call(ctx, n, a)["as_of"] for n, a in asked} == {n: NEWEST for n, _ in asked}

    later = "2026-11-17T22:10:07Z"
    raw.append({"_time": later, "feed": "wow-recorder", "char": CHAR, "kind": "death", "zone": "The Barrens",
                "level": 14, "_msg": "death|14|The Barrens", "source_id": "rec:1:3:1"})
    eventcache.shared(app.store).expire()  # past FRESH_S: the next read asks what changed
    assert app.prebuilt.check() == "new data"
    assert app.api_char(CHAR)["as_of"] == later
    assert {n: call(ctx, n, a)["as_of"] for n, a in asked} == {n: later for n, _ in asked}
    # And the answers read it: the new death is there.
    assert call(ctx, "deaths", {})["recent"][0]["t"] == later


# ---------------------------------------------------------------- the reads that moved

def _reads(lab):
    ctx, *_ = lab
    return ctx.store, tools.rec(CHAR)


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_one_characters_read_comes_from_the_cache_not_victorialogs(lab):
    st, rec = _reads(lab)
    fake = lab[2]
    rows = st.logs(f"{rec} kind:=death | fields _time, zone, level, _msg, source_id")
    assert {r["source_id"] for r in rows} == {d["source_id"] for d in fakes.DEATHS}
    assert not [q for q in fake.log_queries() if "kind:=death" in q]
    # What the cache can't answer (every character at once) still asks VictoriaLogs.
    assert st.logs("char:* | stats by (char) max(_time) as last_seen") == fakes.CHARS


def test_a_range_keeps_its_ends_as_victorialogs_does(lab):
    st, rec = _reads(lab)
    q = f"{rec} kind:=death | fields _time, source_id"
    day = st.logs(q, _t("2026-11-16T00:00:00Z"), _t("2026-11-16T23:59:59Z"))
    assert {r["source_id"] for r in day} == {"rec:1:1:7"}
    at = _t(fakes.DEATHS[0]["_time"])
    assert st.logs(q, at, at), "both ends inclusive, to the microsecond"
    assert "rec:1:1:7" not in {r["source_id"] for r in st.logs(q, None, at - _one_us())}
    assert {r["source_id"] for r in st.logs(q, _t("2026-11-16T00:00:00Z"))} == {"rec:1:1:7"}
    assert {r["source_id"] for r in st.logs(q, None, _t("2026-11-15T23:00:00Z"))} == {"rec:1:0:1"}


def _one_us():
    return timedelta(microseconds=1)


def test_sort_and_limit_read_as_before(lab):
    st, rec = _reads(lab)
    last = st.logs(f"{rec} kind:in(xp, level, zone) | fields _time, kind, level, xp, zone "
                   "| sort by (_time desc) | limit 1", limit=1)
    assert [(r["_time"], str(r["xp"])) for r in last] == [(fakes.LAST_REC[0]["_time"], str(fakes.LAST_REC[0]["xp"]))]
    assert len(st.logs(f"{rec} | fields _time", limit=3)) == 3
    assert st.logs(f"{rec} | limit 1 | fields _time", _t("2030-01-01T00:00:00Z"), limit=1) == []
