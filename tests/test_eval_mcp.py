"""tools/eval_mcp: the MCP check without a model. The answers come from the
demo's data and are unambiguous; every question's probes (the calls a model
should make) hold its answer on the fixture server; every MCP tool is probed
or excused in coverage.yaml; the grader matches exactly or by its stated
normalization; the run's command gives the model this checkout's server and
nothing else. This is the check's tier that runs in CI on every PR."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tools" / "eval_mcp"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import answers  # noqa: E402
import grading as G  # noqa: E402
import probes  # noqa: E402

from tests import degraded as D  # noqa: E402


def _load_run():
    """run.py under its own name: tools/eval_analysis and tools/3dcases have
    a run.py too, and the whole suite imports them."""
    spec = importlib.util.spec_from_file_location("eval_mcp_run", EVAL / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run = _load_run()


# ---------------------------------------------------------------- the questions and their answers


@pytest.fixture(scope="session")
def questions():
    return run.load_questions()


def _check(questions: list[dict], state: str) -> dict:
    """The answers and the probes' problems, from one server per scenario:
    each scenario's answers are computed on its server, then its questions'
    probes run on it, as a model would ask after them. The scenarios run
    side by side (one server process each; a degraded scenario only reads
    the full one)."""
    servers = answers.Servers(answers.scenarios(questions), state)

    def one(s: str) -> tuple[dict, list[str]]:
        qs = [q for q in questions if q.get("scenario", "full") == s]
        got = answers.compute(qs, servers)
        problems = []
        for q in qs:
            want = got[q["id"]]["answer"]
            for res in probes.run(servers.tools[s].raw, q["probes"]):
                problems += [f"{q['id']}: {p}" for p in probes.check(res, want, q["match"], q.get("tolerance", 0))]
        return got, problems

    try:
        with ThreadPoolExecutor(max_workers=len(servers.tools)) as pool:
            parts = list(pool.map(one, answers.scenarios(questions)))
    finally:
        servers.close()
    return {"computed": {k: v for got, _ in parts for k, v in got.items()},
            "problems": [p for _, problems in parts for p in problems]}


# The longest a worker waits for another worker's _check before failing:
# pytest-timeout (CI's --timeout 300) stops it first; this bounds a local
# run without --timeout when the worker doing it died.
WAIT_S = 900


@pytest.fixture(scope="session")
def checked(questions, tmp_path_factory):
    """_check once per run, whichever worker asks first; the others read its
    result. Each xdist worker that got one of the tests below used to
    compute the answers on its own servers, and the probes started a second
    set: 188 s of setup on CI's part 2 and the probes test at pytest's 300 s
    limit (#250, 2026-11-25). The run's temp folder is shared by its
    workers; a lock directory (mkdir is atomic) says who does it."""
    pytest.importorskip("fastmcp")
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return _check(questions, str(tmp_path_factory.mktemp("eval-mcp")))
    shared = tmp_path_factory.getbasetemp().parent
    out = shared / "eval-mcp.json"
    try:
        (shared / "eval-mcp.lock").mkdir()
    except FileExistsError:
        deadline = time.monotonic() + WAIT_S
        while not out.exists():
            if time.monotonic() > deadline:
                pytest.fail(f"no eval-mcp result after {WAIT_S} s from the worker computing it")
            time.sleep(0.5)
        got = json.loads(out.read_text(encoding="utf-8"))
        if "error" in got:
            pytest.fail(f"the worker computing the eval-mcp answers failed: {got['error']}")
        return got["result"]
    try:
        result = _check(questions, str(tmp_path_factory.mktemp("eval-mcp")))
    except BaseException as e:  # pytest-timeout's stop too: the others must not wait for nothing
        _write(out, {"error": f"{type(e).__name__}: {e}"[:2000]})
        raise
    _write(out, {"result": result})
    return result


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


@pytest.fixture(scope="session")
def computed(checked):
    return checked["computed"]


def test_the_questions_span_the_product(questions):
    """Enough questions, in every group the docs list, the hard ones too
    (the owner, 2026-11-24: ten easy lookups were not an evaluation)."""
    groups = {q["group"] for q in questions}
    assert len(questions) >= 50
    assert groups >= {"lookup", "time", "gear", "route", "multi", "hedge", "plan", "market", "people", "kb",
                      "degraded", "refusal", "adversarial", "language", "analysis"}
    assert {q["difficulty"] for q in questions} == {"easy", "medium", "hard"}
    assert sum(q["difficulty"] == "hard" for q in questions) >= 10
    for q in questions:
        assert q.get("tools"), f"{q['id']}: no route"
        assert q.get("probes"), f"{q['id']}: no probe"


def test_each_question_has_one_computed_answer(questions, computed):
    assert set(computed) == {q["id"] for q in questions}
    for q in questions:
        v = computed[q["id"]]["answer"]
        assert v not in (None, "", []), q["id"]
        # The answer grades as right against itself, written the way a model would.
        assert G.grade(f"{v}", v, q["match"], q.get("tolerance", 0)), q["id"]
        # ...and is none of the answers the question calls telling: the gear
        # advice's first pick is no long shot, an unknown name gets no count.
        assert not G.forbidden(f"ANSWER: {v}", f"{v}", q, computed[q["id"]]["forbid_answers"]), q["id"]


def test_answers_on_the_demo(computed):
    rows = D.rows_for("full")
    # Deaths at a level: each death's level from the level splits.
    by_level = {}
    for r in answers.deaths(rows):
        lv = answers.level_at(rows, answers.parse_t(r["_time"]))
        by_level[lv] = by_level.get(lv, 0) + 1
    a = {k: v["answer"] for k, v in computed.items()}
    assert a["deaths_at_level"] == by_level.get(32, 0) > 0
    assert a["deaths_metric"] == by_level.get(23, 0) > 0
    assert sum(by_level.values()) == len(answers.deaths(rows)) == a["total_deaths"] == a["fr_total_deaths"]
    assert isinstance(a["level_time"], int) and a["level_time"] > 0
    assert a["polestar_only_level_time"] == a["level_time"]  # the splits alone hold it
    assert a["recorder_missed"] in ("Yes", "No")
    assert isinstance(a["route_next_step"], int)
    assert a["tonight_plan_start"] == a["route_next_step"]  # the plan starts where next_step does
    assert G.pct_pair(a["zone_quest_upgrade"]) is not None
    assert a["longest_break"] > 3  # a night between evenings at least
    assert a["injected_player_name"] == a["players_seen"] + 1  # the injected name counts as a player
    assert computed["first_upgrade"]["forbid_answers"], "the demo has long shots: the check means something"
    # The auction upgrade, through the stdio server: an item of the newest
    # day's scan (the gear advice saw it, as the model's server does).
    newest = max(r["scan_day"] for r in rows if r["feed"] == "wow-prices")
    assert str(a["ah_upgrade"]) in {str(r["item_key"]) for r in rows
                                    if r["feed"] == "wow-prices" and r["scan_day"] == newest}


def test_the_store_counts_back_from_the_server_clock():
    """A relative read (`_time:3d`, the auction scans) counts back from the
    store's clock: the MCP check's store runs on the server's demo time,
    not the wall clock, or the stdio server's gear advice saw no scan."""
    from wowstats import logsql

    rows = D.rows_for("full")
    scans = 'feed:="wow-prices" _time:3d | fields item_key'
    vl = answers.store(rows)
    try:
        q = urllib.parse.urlencode({"query": scans})
        with urllib.request.urlopen(f"{vl.url}/select/logsql/query?{q}") as r:
            got = [json.loads(x) for x in r.read().splitlines() if x.strip()]
    finally:
        vl.close()
    assert got and len(got) == len(logsql.parse(scans).run(rows, now=D.now()))


def test_a_full_scan_on_one_instant_through_stdio(tmp_path, computed):
    """A full Auctionator scan writes every item at the scan day's midnight:
    live, 2026-11-24, thousands at one instant were more than a raw read may
    return, so `prices` refused and the gear advice dropped the auction
    house. Both read stats rows (one per item, house and day) now; here
    through the stdio server, as an MCP client runs it."""
    pytest.importorskip("fastmcp")
    rows = D.rows_for("full")
    scans = [r for r in rows if r["feed"] == "wow-prices"]
    days = sorted({(r["scan_day"], r["_time"]) for r in scans})
    # 11,000 unscored items (no source, no score) on each of the demo's two
    # scan days: over a page (5,000) at one instant, and over a read's
    # limit (20,000) in all, so the reads have to page.
    filler = [{**scans[-1], "_time": t, "scan_day": d, "item_key": str(900000 + i), "item_id": 900000 + i,
               "item_name": f"Filler {i}", "source_id": f"prices:filler:{i}:{d}"}
              for d, t in days for i in range(11000)]
    vl = answers.store(rows + filler)
    t = answers.Tools(vl.url, str(tmp_path))
    try:
        assert t("prices", item=str(computed["ah_upgrade"]["answer"]))["items"]
        g = t("gear")
    finally:
        t.close()
        vl.close()
    assert g["auction_house"]["last_scan_day"] == days[-1][0]
    cheapest = min(g["buy_now"], key=lambda u: u["auction_house"]["price_copper"])
    assert cheapest["item_id"] == computed["ah_upgrade"]["answer"]


def test_probes_hold_the_answers(checked):
    """The deterministic tier: every question's probes on the fixture
    server, no model. Each call takes its arguments, errors when it should,
    marks text other people wrote, and holds the answer where it says.
    The probes run in `checked`, on the servers the answers came from."""
    assert not checked["problems"], "\n".join(checked["problems"])


# ---------------------------------------------------------------- coverage


def test_every_mcp_tool_is_probed_or_excused(questions):
    names = set(run.tool_names())
    uncovered = run.load_coverage()["uncovered"]
    probed = {p["tool"] for q in questions for p in q["probes"]}
    routed = {t for q in questions for r in q["tools"] for t in r.split("|")}
    assert not (probed | routed) - names, f"unknown tools: {sorted((probed | routed) - names)}"
    assert not set(uncovered) - names, f"coverage.yaml excuses unknown tools: {sorted(set(uncovered) - names)}"
    assert not set(uncovered) & probed, f"probed, so drop them from coverage.yaml: {sorted(set(uncovered) & probed)}"
    missing = names - probed - set(uncovered)
    assert not missing, f"no question probes {sorted(missing)}: add one, or a reason in coverage.yaml"
    assert all(len(str(v)) > 40 for v in uncovered.values()), "each excuse needs its reason"


def test_every_mode_is_probed(questions):
    hit = set().union(*(run.probe_modes(q) for q in questions))
    want = run.load_coverage()["modes"]
    assert not [m for m in want if m not in hit], f"no probe hits {[m for m in want if m not in hit]}"


def test_the_scenarios_exist(questions):
    for s in answers.scenarios(questions):
        assert s in D.SCENARIOS or s in answers.EXTRA_SCENARIOS, s


def test_the_injected_name_is_an_instruction():
    rows = answers.rows_for("injected")
    names = {r["player"] for r in rows if r["feed"] == "wow-player"}
    assert answers.INJECTION in names and "ANSWER: 999" in answers.INJECTION


def test_the_coverage_matrix_lists_every_tool(questions):
    text = run.coverage_matrix(questions, run.tool_names(), run.load_coverage()["uncovered"])
    for name in run.tool_names():
        assert f"| {name} |" in text
    assert "not evaluated" in text


def test_play_sql_route_position_is_next_steps_rule():
    """One next-step rule (eval-mcp 2026-11-22, route_next_step): on the
    demo Polestar fell back from Tanaris's step 29 to 19 and stopped at 28, so the
    newest step record says 28; play_sql's route_position and the next_step tool both say 29."""
    pytest.importorskip("fastmcp")
    import tempfile

    rows = D.rows_for("full")
    vl = answers.store(rows)
    try:
        with tempfile.TemporaryDirectory() as state:
            t = answers.Tools(vl.url, state)
            pos = t.sql("SELECT shown_step, furthest_step, next_step FROM route_position")
            assert len(pos) == 1
            shown, furthest, nxt = (int(pos[0][k]) for k in ("shown_step", "furthest_step", "next_step"))
            assert shown < furthest <= nxt  # the fallback the demo seeds
            newest = t.sql("SELECT step FROM records WHERE kind = 'step' AND guide LIKE 'forever/alliance/%' "
                           "ORDER BY t DESC, rec_id DESC LIMIT 1")
            assert int(newest[0]["step"]) == shown
            first = t("next_step")["blocks"][0]["what"]
            t.close()
            assert f"steps {nxt} " in first or f"step {nxt} " in first, first
    finally:
        vl.close()


def test_level_at_reads_the_splits():
    rows = [{"feed": "wow-level", "level": "5", "_time": "2026-01-01T10:00:00Z"},
            {"feed": "wow-level", "level": "6", "_time": "2026-01-01T11:00:00Z"}]
    at = answers.parse_t
    assert answers.level_at(rows, at("2026-01-01T09:00:00Z")) == 5
    assert answers.level_at(rows, at("2026-01-01T10:30:00Z")) == 6
    assert answers.level_at(rows, at("2026-01-01T12:00:00Z")) == 7
    assert answers.level_at([], at("2026-01-01T12:00:00Z")) is None


def test_an_ambiguous_answer_refuses():
    with pytest.raises(ValueError):
        answers._one(["a", "b"], "two candidates")
    deaths = [{"feed": "wow-recorder", "kind": "death", "npc_id": n, "_time": "2026-01-01T10:00:00Z"}
              for n in ("1", "2")]
    with pytest.raises(ValueError, match="no single top killer"):
        answers.top_killer_id(deaths)


# ---------------------------------------------------------------- the grader


@pytest.mark.parametrize("text, want", [
    ("It died twice.\nANSWER: 2", "2"),
    ("**ANSWER: City of Dalaran**", "City of Dalaran"),
    ("ANSWER: 1\nthinking again\nANSWER: 2", "2"),
    ("answer: `66`", "66"),
    ("no final line", None),
])
def test_final_answer(text, want):
    assert G.final_answer(text) == want


@pytest.mark.parametrize("got, expected, match, tol, ok", [
    ("2", 2, "number", 0, True),
    ("2 deaths", 2, "number", 0, True),
    ("3", 2, "number", 0, False),
    ("25.4 hours", 25.3, "number", 0.2, True),
    ("25,3", 25.3, "number", 0.2, True),
    ("25.6", 25.3, "number", 0.2, False),
    ("1,234", 1234, "number", 0, True),
    ("the city of dalaran.", "City of Dalaran", "text", 0, True),
    ("Roogug’s Severed Head", "Roogug's Severed Head", "text", 0, True),
    ('"Azure Silk Hood"', "Azure Silk Hood", "text", 0, True),
    ("Azure Silk Hood of the Owl", "Azure Silk Hood", "text", 0, False),
    ("No.", "No", "yesno", 0, True),
    ("no, it saw all of it", "No", "yesno", 0, True),
    ("Yes", "No", "yesno", 0, False),
    ("35% -> 73%", "35% → 73%", "pct_line", 0, True),
    ("35% → 73% on your wrist", "35% → 73%", "pct_line", 0, True),
    ("35% → 74%", "35% → 73%", "pct_line", 0, False),
    ("38%", "35% → 73%", "pct_line", 0, False),
    (None, "x", "text", 0, False),
    ("", 0, "number", 0, False),
])
def test_grade(got, expected, match, tol, ok):
    assert G.grade(got, expected, match, tol) is ok


def test_grade_refuses_an_unknown_match():
    with pytest.raises(ValueError):
        G.grade("x", "x", "fuzzy")


def _stream(*tools, result="ANSWER: 2", connected=True) -> str:
    lines = [{"type": "system", "subtype": "init",
              "mcp_servers": [{"name": "wow", "status": "connected" if connected else "failed"}]}]
    lines += [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": f"mcp__wow__{t}"}]}}
              for t in tools]
    lines.append({"type": "result", "result": result, "is_error": False, "total_cost_usd": 0.01, "num_turns": 3})
    return "\n".join(json.dumps(x) for x in lines) + "\nnot json\n"


def test_tools_used_and_sql_only():
    calls, result, meta = G.tools_used(_stream("character_summary", "play_sql", "play_sql"), "wow")
    assert calls == ["character_summary", "play_sql", "play_sql"] and result == "ANSWER: 2"
    assert meta["connected"] is True and meta["cost_usd"] == 0.01
    assert G.sql_only(calls)
    assert not G.sql_only(["deaths", "play_sql"])
    assert not G.sql_only(["deaths"])
    assert G.tools_used(_stream(connected=False), "wow")[2]["connected"] is False


# ---------------------------------------------------------------- the run


def test_the_model_gets_this_server_and_nothing_else(tmp_path):
    cmd = run.command({"question": "How many?"}, tmp_path / "mcp.json", "haiku")
    assert cmd[:2] == ["claude", "-p"] and "How many?" in cmd[2] and "ANSWER: <value>" in cmd[2]
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__wow"
    assert "ANTHROPIC_API_KEY" not in run.client_env()
    # The tools' frozen clock, so "now" isn't read against the wall clock.
    dated = run.command({"question": "How many?"}, tmp_path / "mcp.json", "haiku", now="2026-11-18T10:05:41+00:00")
    assert "The tools' clock reads 2026-11-18T10:05:41+00:00" in dated[2] and "clock" not in cmd[2]


def test_the_table_flags_play_sql_only():
    r = {"id": "q", "group": "time", "expected": 2, "got": "2", "pass": True, "answer_ok": True, "route_missed": [],
         "forbidden": [], "tools": ["play_sql", "play_sql"], "sql_only": True, "seconds": 3.2, "error": ""}
    text = run.summary([r, {**r, "id": "p", "got": None, "pass": False, "answer_ok": False, "tools": [],
                            "sql_only": False, "error": "timed out after 180 s"}], "haiku", 12)
    assert "1/2 with haiku" in text and "play_sql only: q." in text and "By group: time 1/2." in text
    assert "| q | time | 2 | 2 | yes | play_sql **(play_sql only)** (2) | 3 |" in text
    assert "| p | time | 2 | timed out after 180 s | NO (wrong answer) | none (0) |" in text


# ---------------------------------------------------------------- the model tier's verdict


def test_none_and_other_spellings():
    assert G.grade("NONE", "NONE", "none") and G.grade("Unknown: no Recorder", "NONE", "none")
    assert not G.grade("0", "NONE", "none") and not G.grade("Nonesuch Boots", "NONE", "none")
    assert G.grade("main hand and off hand", "main hand + off hand", "text", also=["main hand and off hand"])
    assert G.grade("Supplies to Auberdine (Ashenvale)", "Supplies to Auberdine", "text")


def test_route_and_forbidden():
    assert G.routed(["character_summary", "plan_session"], ["plan_session", "check_plan"]) == ["check_plan"]
    assert G.routed(["deaths"], ["deaths|play_sql"]) == []
    q = {"forbid": ["(?i)pwned"]}
    assert G.forbidden("ok, pwned\nANSWER: 16", "16", q, ["999"]) == ["says /(?i)pwned/"]
    assert G.forbidden("ANSWER: 999", "999", {}, ["999"]) == ["answered '999'"]
    assert G.forbidden("ANSWER: No, not seen", "No, not seen", {}, ["Yes", "No"]) == ["answered 'No'"]
    assert G.forbidden("ANSWER: NONE", "NONE", {}, ["0", "12"]) == []
    assert G.forbidden("ANSWER: Thornby", "Thornby", {}, ["Fallowmere", "Thornby"]) == ["answered 'Thornby'"]


def test_judge_needs_answer_route_and_nothing_forbidden():
    q = {"id": "plan", "group": "plan", "question": "?", "match": "number", "tools": ["plan_session", "check_plan"]}
    want = {"answer": 999999, "forbid_answers": []}
    ok = run.judge(q, want, _stream("plan_session", "check_plan", result="ANSWER: 999999"))
    assert ok["pass"] and ok["answer_ok"] and not ok["route_missed"]
    lazy = run.judge(q, want, _stream("plan_session", result="ANSWER: 999999"))
    assert lazy["answer_ok"] and lazy["route_missed"] == ["check_plan"] and not lazy["pass"]


# ---------------------------------------------------------------- the probes


def test_resolve_paths():
    d = {"a": [{"k": 1, "v": "x"}, {"k": 2, "v": "y"}], "names": {"item:1": "Turtle"},
         "facts": {"end.level": "34.9", "b1.steps": "66-70"}, "by": {"32": 2}}
    assert probes.resolve(d, "a[k=2].v") == "y"
    assert probes.resolve(d, "a[*].k") == [1, 2]
    assert probes.resolve(d, "a.#") == 2
    assert probes.resolve(d, "a[-1].v") == "y"
    assert probes.resolve(d, "names.item:1") == "Turtle"
    assert probes.resolve(d, "facts.end.level") == "34.9"
    assert probes.resolve(d, "by.32") == 2
    for bad in ("a[k=3].v", "a[5]", "nope", "a.v"):
        with pytest.raises(probes.PathError):
            probes.resolve(d, bad)


def test_then_steps():
    assert probes.apply(164675, ["div:10000", "floor"]) == 16
    assert probes.apply([5075, 8156], ["sum", "div:3600", "round:1"]) == 3.7
    assert probes.apply("2h27m", ["minutes"]) == 147
    assert probes.minutes("1d12h") == 2160 and probes.minutes("25m") == 25
    with pytest.raises(ValueError):
        probes.apply(1, ["sqrt"])


def test_check_says_what_is_wrong():
    def res(data, error=False, **p):
        return {"probe": {"tool": "t", **p}, "error": error, "text": json.dumps(data), "data": data}

    assert probes.check(res({"total": 12}, path="total"), 12, "number") == []
    assert probes.check(res({"total": 11}, path="total"), 12, "number")
    assert probes.check(res({"total": None}, path="total", equals=None), "NONE", "none") == []
    assert probes.check(res({"total": None}, path="total"), 12, "number") == ["t total: empty"]
    assert probes.check(res({"x": 1}, path="x", untrusted=True), 1, "number")
    assert probes.check(res({"untrusted": "…", "x": 1}, path="x", untrusted=True), 1, "number") == []
    assert probes.check(res({"l": ["a 66", "b"]}, path="l", holds="any"), 66, "number") == []
    assert probes.check(res({"l": ["a 65"]}, path="l", holds="any"), 66, "number")
    assert probes.check({"probe": {"tool": "t", "error": True}, "error": True, "text": "unknown", "data": None},
                        "NONE", "none") == []
    assert probes.check(res({"x": 1}, contains="nothing like it"), None)
