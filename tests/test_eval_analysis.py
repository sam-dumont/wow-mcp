"""tools/eval_analysis: the seeded evaluation's scenarios and grader, without
a model.

Each injection does what its file says, measured on the product's own prep
(the spike is significant and the control has none, the gap level is on pace
in played time, the unrecorded levels have played time and no records, the
wrong-spec item is worn and the truth names the spec's best head pieces);
the files are checked on load; the grader's rules, its re-run of the kept
queries and its verdicts."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tools" / "eval_analysis"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import clients  # noqa: E402
import grade  # noqa: E402
import scenarios  # noqa: E402


def _load_run():
    """run.py under its own name: tools/3dcases has a run.py too, and the
    whole suite imports both."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("eval_analysis_run", EVAL / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run = _load_run()

ALL = ("death_spike", "healthy_control", "offline_gap", "recorder_off", "wrong_spec_gear")


@pytest.fixture(scope="module")
def facts() -> dict:
    return {n: scenarios.facts(scenarios.load(n)) for n in ALL}


# ---------------------------------------------------------------- the scenarios


def test_the_five_scenarios_load():
    assert tuple(scenarios.names()) == ALL
    for n in ALL:
        sc = scenarios.load(n)
        assert sc["truth"]["must_find"] or sc["truth"]["must_not_claim"]


def test_a_scenario_file_with_a_mistake_is_refused(tmp_path, monkeypatch):
    good = json.loads((scenarios.SCENARIOS / "death_spike.json").read_text())
    bad = {
        "wrong-name": {**good, "name": "other"},
        "no-truth": {k: v for k, v in good.items() if k != "truth"},
        "unknown-kind": {**good, "inject": [{"kind": "teleport"}]},
        "no-groups": {**good, "truth": {**good["truth"], "must_find": [{"id": "x", "all": []}]}},
        "empty-group": {**good, "truth": {**good["truth"], "must_find": [{"id": "x", "all": [[]]}]}},
        "bad-place": {**good, "truth": {**good["truth"], "must_find": [{"id": "x", "all": [["a"]], "in": "all"}]}},
        "bad-kind": {**good, "truth": {**good["truth"], "must_find": [{"id": "x", "all": [["a"]],
                                                                        "kinds": ["comment"]}]}},
        "runner-env": {**good, "env": {"ANALYSIS_PROVIDER": "openai"}},
        "store-env": {**good, "env": {"VL_URL": "http://elsewhere"}},
        "checks-nothing": {**good, "truth": {"expected": "x", "must_find": [], "must_not_claim": []}},
    }
    monkeypatch.setattr(scenarios, "SCENARIOS", tmp_path)
    for name, body in bad.items():
        body = {**body, "name": body.get("name", name) if name == "wrong-name" else name}
        (tmp_path / f"{name}.json").write_text(json.dumps(body))
        with pytest.raises(scenarios.ScenarioError):
            scenarios.load(name)
    with pytest.raises(scenarios.ScenarioError, match="no scenario"):
        scenarios.load("missing")


def test_the_events_are_the_same_every_time():
    sc = scenarios.load("death_spike")
    a, b = scenarios.build(sc), scenarios.build(sc)
    assert a == b
    assert len({r["source_id"] for r in a if "source_id" in r}) == sum(1 for r in a if "source_id" in r)


def test_the_control_is_the_demo_as_generated():
    assert scenarios.build(scenarios.load("healthy_control")) == sorted(
        scenarios.base_events(), key=lambda r: (r["_time"], r.get("source_id") or ""))


def test_the_control_has_no_death_pattern(facts):
    f = facts["healthy_control"]
    assert f["deaths"] == 12 and f["significant_death_zones"] == []
    assert f["recorded_levels"] == list(range(22, 35)) and f["longest_gap_days"] < 2


def test_the_death_spike_doubles_the_wetlands_and_nothing_else(facts):
    f, base = facts["death_spike"], facts["healthy_control"]
    w = f["death_zones"]["Wetlands"]
    assert w["per_hour"] >= 2 * w["rest_per_hour"] and w["p"] < 0.05
    assert f["significant_death_zones"] == ["Wetlands"]
    # Only Wetlands deaths were added; every other zone keeps its count.
    added = f["deaths"] - base["deaths"]
    assert added == w["deaths"] - base["death_zones"]["Wetlands"]["deaths"] > 0
    for z, v in base["death_zones"].items():
        if z != "Wetlands":
            assert f["death_zones"][z]["deaths"] == v["deaths"]
    # Each corpse run costs the level it happened at some played time, nothing more.
    for lv, v in base["levels"].items():
        assert f["levels"][lv]["played_s"] is None or f["levels"][lv]["played_s"] >= (v["played_s"] or 0)


def test_the_recorder_gap_leaves_played_time_without_records(facts):
    f, base = facts["recorder_off"], facts["healthy_control"]
    assert {27, 28, 29}.isdisjoint(f["recorded_levels"])
    for lv in (27, 28, 29):
        assert f["levels"][lv]["played_s"] == base["levels"][lv]["played_s"] > 0
    assert f["sessions"] == base["sessions"] - 4 and f["deaths"] < base["deaths"]


def test_the_offline_gap_is_calendar_time_only(facts):
    f, base = facts["offline_gap"], facts["healthy_control"]
    lv33 = f["levels"][33]
    assert lv33["calendar_days"] > 20 > base["levels"][33]["calendar_days"]
    assert lv33["played_s"] == base["levels"][33]["played_s"]
    assert 0.9 <= lv33["played_s"] / lv33["planned_s"] <= 1.1  # on the guide's pace
    assert {lv: v["played_s"] for lv, v in f["levels"].items()} == {
        lv: v["played_s"] for lv, v in base["levels"].items()}
    assert f["longest_gap_days"] > 20


def test_the_wrong_spec_item_is_worn_and_the_truth_names_the_spec_s_best(facts):
    f = facts["wrong_spec_gear"]
    assert f["worn_head"] == (253981, "Filigreed Shadow Circlet")
    sc = scenarios.load("wrong_spec_gear")
    assert sc["env"]["WEIGHTS"].endswith(":holy")
    rule = next(r for r in sc["truth"]["must_find"] if r["id"] == "head-upgrade-for-the-spec")
    assert sorted(rule["all"][0]) == sorted(u["name"].lower() for u in f["top_upgrades"]["Head"])
    # Scored for Holy, the circlet is next to nothing over the hood it replaced.
    db = scenarios.prep_db(scenarios.base_events(), sc["env"])
    circlet = db.execute("SELECT gain FROM upgrade_candidates WHERE item_id = 253981").fetchone()[0]
    assert circlet < 0.1 * f["top_upgrades"]["Head"][0]["gain"]


def test_the_brief_names_the_break_and_the_off_spec_head_only_where_injected(facts):
    """What analysis_brief's start hands every client (drive.facts): the
    break shows in offline_gap alone, the circlet against Holy in
    wrong_spec_gear alone."""
    for n in ALL:
        cal = facts[n]["brief_facts"]["level_calendar"]["levels"]
        if n == "offline_gap":
            assert [x["level"] for x in cal] == [33]
            lv = cal[0]
            assert lv["longest_gap_days"] > 20 and lv["played_h"] == lv["planned_h"] and lv["recorder_missed_h"] == 0
        else:
            assert cal == "none", n
        gear = facts[n]["brief_facts"]["gear_fit"]
        head = [x for x in gear["worn"] if x["slot"] == "Head"]
        if n == "wrong_spec_gear":
            assert gear["scored_for"].startswith("Holy")
            assert head[0]["item"] == "Filigreed Shadow Circlet" and head[0]["fit"] == "spec"
            assert head[0]["bis_pct"] < 25
        else:
            assert gear["scored_for"] == "Shadow" and all(x["item_id"] != 253981 for x in head)



def test_the_brief_hands_the_spec_s_best_head_upgrades(facts):
    """The owner's ask (2026-11-22): the three Holy head upgrades the truth
    names were reachable through play_sql only. The brief's gear_fit now
    carries them under the circlet, from the semantic layer, in the page's
    words, with where to get each and the Wowhead pages; the same three the
    prep's own table ranks first (facts' top_upgrades, plain SQL)."""
    f = facts["wrong_spec_gear"]
    head = next(x for x in f["brief_facts"]["gear_fit"]["worn"] if x["slot"] == "Head")
    rule = next(r for r in scenarios.load("wrong_spec_gear")["truth"]["must_find"]
                if r["id"] == "head-upgrade-for-the-spec")
    ups = head["upgrades"]
    assert [u["item"].lower() for u in ups] == sorted(rule["all"][0], key=[
        u["name"].lower() for u in f["top_upgrades"]["Head"]].index)
    assert [u["item_id"] for u in ups] == [u["item_id"] for u in f["top_upgrades"]["Head"]]
    for u in ups:
        assert u["line"].startswith(f"{head['bis_pct']}% → ") and u["line"].endswith("% on your head"), u
        assert u["wowhead"] == f"https://www.wowhead.com/forever/item={u['item_id']}"
        assert u["where"] and len(u["where_wowhead"]) == len(u["where"]) and u["fit"] == "yes"
    assert ups[0]["where"] == ["drop: Fallen Champion in Scarlet Monastery: Library (dungeon, levels 30-38), 38.5%"]

def test_the_analysis_reads_what_the_store_query_would():
    rows = [{"char": "Nibbet-Gearholt", "feed": "wow-recorder"}, {"char": "Nibbet Gearholt", "feed": "wow-level"},
            {"char": "Nibbet-Gearholt", "feed": "wow-prices"}, {"char": "Other-Realm", "feed": "wow-recorder"},
            {"feed": "wow-setup"}]
    assert [r["feed"] for r in scenarios.analysis_rows(rows)] == ["wow-recorder", "wow-level"]


def test_poisson_tail():
    assert scenarios.poisson_tail(0, 3.0) == 1.0
    assert scenarios.poisson_tail(8, 3.5) == pytest.approx(0.0267, abs=1e-3)
    assert scenarios.poisson_tail(1, 0.0) == 0.0


# ---------------------------------------------------------------- the grader


ANALYSIS = {
    "findings": {
        "wetlands-deaths": {"title": "Deaths in the Wetlands", "text": "You died 8 times in the Wetlands.",
                            "action": {"do": "Pull one mob at a time", "where": "Wetlands", "blocked_by": "",
                                       "next_step": "Rest to full before each pull"}},
        "levels": {"title": "13 levels", "text": "You have 13 levels on record.", "action": None},
    },
    "nudges": [{"text": "Get the Embalmed Shroud", "gist": "Head upgrade", "where": "Razorfen Kraul",
                "blocked_by": "", "next_step": "Queue for the dungeon"}],
    "patterns": [{"text": "Most of your deaths come in the evening."}],
}


def test_lines_read_every_kept_line():
    ls = grade.lines(ANALYSIS)
    assert [(ln.kind, ln.key) for ln in ls] == [("finding", "levels"), ("finding", "wetlands-deaths"),
                                                  ("nudge", "1"), ("pattern", "1")]
    assert ls[0].action == "" and "Pull one mob" in ls[1].action and ls[3].action == ""


def test_a_rule_needs_every_group_and_respects_unless_and_place():
    ls = grade.lines(ANALYSIS)
    death = {"id": "d", "all": [["wetlands"], ["\\bdie[ds]?\\b", "\\bdeaths?\\b"]]}
    assert [m.key for m in grade.matches(death, ls)] == ["wetlands-deaths"]
    assert not grade.matches({**death, "unless": ["mob at a time"]}, ls)
    head = {"id": "h", "all": [["embalmed shroud"]]}
    assert [m.kind for m in grade.matches({**head, "in": "actions"}, ls)] == ["nudge"]
    # A pattern tells nobody to do anything: never matched in "actions".
    evening = {"id": "e", "all": [["evening"]]}
    assert grade.matches(evening, ls) and not grade.matches({**evening, "in": "actions"}, ls)


def _ro():
    from wowstats.analysis.sql import ReadOnly

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE levels (level INTEGER, played_s REAL)")
    db.executemany("INSERT INTO levels VALUES (?, ?)", [(22, 3000.0), (23, 4000.0), (24, 5000.0)])
    return ReadOnly(db)


def _finding(sql: str, expect: dict) -> dict:
    return {"sql": sql, "expect": expect, "title_template": "{n} levels", "claim_template": "You have {n} levels.",
            "holds": None, "action_template": None}


def test_rerun_checks_each_query_against_what_it_was_judged_on():
    got = grade.rerun({"findings": {
        "ok": _finding("SELECT COUNT(*) AS n FROM levels", {"n": 3}),
        "moved": _finding("SELECT COUNT(*) AS n FROM levels", {"n": 5}),
        "broken": _finding("SELECT COUNT(*) AS n FROM nowhere", {"n": 3}),
        "constant": _finding("SELECT 3 AS n", {"n": 3}),
    }}, _ro())
    by = {r["key"]: r for r in got}
    assert by["ok"]["ok"] and not by["ok"]["why"]
    assert not by["moved"]["ok"] and "judged 5" in by["moved"]["why"]
    assert not by["broken"]["ok"] and "query failed" in by["broken"]["why"]
    assert not by["constant"]["ok"] and "no play history" in by["constant"]["why"]


def test_the_verdicts():
    sc = {"truth": {"expected": "x",
                    "must_find": [{"id": "wetlands", "all": [["wetlands"]]}],
                    "must_not_claim": [{"id": "evening", "all": [["evening"]]}]}}
    ok = [{"key": "k", "ok": True, "why": ""}]
    # The run never finished: the client or the harness, not graded.
    v = grade.grade(sc, ANALYSIS, {"done": False, "error": "timed out"}, None)
    assert v["verdict"] == "error" and v["checks"][0]["detail"] == "timed out"
    v = grade.grade(sc, ANALYSIS, {"done": True}, ok)
    assert v["verdict"] == "fail"
    bad = next(c for c in v["checks"] if c["check"] == "must_not_claim evening")
    assert not bad["ok"] and bad["evidence"][0].startswith("pattern 1:")
    sc["truth"]["must_not_claim"] = []
    assert grade.grade(sc, ANALYSIS, {"done": True}, ok)["verdict"] == "pass"
    v = grade.grade(sc, ANALYSIS, {"done": True}, [{"key": "k", "ok": False, "why": "n: 4 on re-run, judged 3"}])
    assert v["verdict"] == "fail" and v["checks"][1]["evidence"] == ["k: n: 4 on re-run, judged 3"]


def test_the_shipped_truths_grade_a_right_and_a_wrong_answer():
    """Each scenario's rules on two hand-written answers: one that says the
    right thing passes, one that falls in the trap fails."""
    right = {
        "death_spike": "You died 8 times in the Wetlands, more than twice your rate anywhere else.",
        "recorder_off": "Levels 27 to 29 were not recorded: the Recorder was off, so deaths there are unknown.",
        "offline_gap": "Level 33 took three weeks on the calendar because of a break; in played time it was on pace.",
        "wrong_spec_gear": "Scored for Holy, the Filigreed Shadow Circlet is 12% of the best head: replace it with "
                           "the Embalmed Shroud.",
        "healthy_control": "Your deaths are spread out: too few to call any zone dangerous.",
    }
    wrong = {
        "death_spike": "Your deaths are evenly spread.",
        "recorder_off": "You had no deaths at levels 27 to 29.",
        "offline_gap": "Level 33 was your slowest level.",
        "wrong_spec_gear": "Keep the Filigreed Shadow Circlet: it's an upgrade.",
        "healthy_control": "The Wetlands are your most dangerous zone: most of your deaths happen there.",
    }
    # Seen in the first run: "offline gaps" between evenings is no break.
    wrong["offline_gap"] = "Inn logouts gave no more rested XP over similar offline gaps."
    for n in ALL:
        sc = scenarios.load(n)
        for text, want in ((right[n], "pass"), (wrong[n], "fail")):
            v = grade.grade(sc, _answer(text), {"done": True}, [])
            assert v["verdict"] == want, (n, text, v["checks"])


def _answer(text: str, finding: bool = True) -> dict:
    """One line said as a verified finding with that action, and as a nudge."""
    nudge = {"text": text, "gist": "", "where": "", "blocked_by": "", "next_step": ""}
    f = {"title": text, "text": "", "action": {"do": text, "where": "", "blocked_by": "", "next_step": ""}}
    return {"findings": {"k": f} if finding else {}, "nudges": [nudge], "patterns": []}


def test_wrong_spec_gear_needs_a_finding_that_names_the_spec():
    """The first run's weak pass: a nudge named a Holy head upgrade, and no
    finding said the worn circlet is wrong for the spec the gear is scored for."""
    sc = scenarios.load("wrong_spec_gear")
    upgrade = "Run Scarlet Monastery: Library for the Embalmed Shroud."
    for text, finding, want in (
            (upgrade, False, "fail"),
            (upgrade, True, "fail"),  # a finding, but no circlet and no spec
            ("The Filigreed Shadow Circlet is a Shadow item: get the Embalmed Shroud instead.", True, "fail"),
            ("Your head slot is 12% of its best for Holy: get the Embalmed Shroud.", True, "pass"),
            ("For Holy, swap the Filigreed Shadow Circlet for the Earthen Silk Hood.", True, "pass"),
            ("For Holy, swap the Filigreed Shadow Circlet for the Earthen Silk Hood.", False, "fail")):
        v = grade.grade(sc, _answer(text, finding), {"done": True}, [])
        assert v["verdict"] == want, (text, finding, v["checks"])


def test_a_finding_that_faults_the_circlet_isnt_praise():
    """A release-eval run (2026-11-22) failed on praises-the-circlet: its
    finding named the circlet next to the word "upgrades" while saying it was
    built for damage casters at 17% of the best. A false negative; a finding
    that does call the circlet an upgrade still fails."""
    sc = scenarios.load("wrong_spec_gear")
    faulted = ("Scarlet Monastery: Library drops 10 upgrades you can wear now. Its best is Embalmed Shroud for the "
               "Head slot, where you wear the Filigreed Shadow Circlet at 17% of the best for Holy, an item built "
               "for damage casters.")
    praised = "The Filigreed Shadow Circlet is a good pick: keep it, it is an upgrade over most hoods at your level."
    checks = lambda text: {c["check"]: c["ok"] for c in  # noqa: E731
                           grade.grade(sc, _answer(text), {"done": True}, [])["checks"]}
    assert checks(faulted)["must_not_claim praises-the-circlet"]
    assert not checks(praised)["must_not_claim praises-the-circlet"]


def test_a_metric_finding_reruns_on_the_metrics_reader():
    """query_metric's findings (engine duckdb) read the semantic views, which
    the judge's reader refuses: the grader re-runs them as the server's
    recheck does, on the reader without the table list."""
    calls = []

    class R:
        def __init__(self, name):
            self.name = name

        def first_row(self, sql, params=None):
            calls.append((self.name, sql))
            return {"n": 1}

    import grade as g

    from wowstats.analysis import verify

    orig = verify.history_read
    verify.history_read = lambda ro: True
    try:
        a = {"findings": {"m": {"sql": "SELECT 1 AS n FROM sem_levels", "engine": "duckdb", "expect": {"n": 1},
                                "title_template": "x", "claim_template": "{n}"},
                          "q": {"sql": "SELECT 1 AS n FROM levels", "expect": {"n": 1},
                                "title_template": "x", "claim_template": "{n}"}}}
        out = g.rerun(a, R("judge"), R("metrics"))
    finally:
        verify.history_read = orig
    assert all(r["ok"] for r in out), out
    assert dict((sql.split()[-1], who) for who, sql in calls) == {"sem_levels": "metrics", "levels": "judge"}


def test_recorder_off_reads_the_meaning_not_one_wording():
    """The review-5 run's finding said the Recorder missed levels 26 to 30 in
    words the first rule didn't know: a false negative. A finding that
    names the Recorder's gap (or the coverage) as why evidence is missing
    passes; a vague one, or the same words only in a nudge, doesn't."""
    sc = scenarios.load("recorder_off")
    said = {"title": "The Recorder missed most of levels 26 to 30",
            "text": "Polestar counts 11h12 of play that the Recorder never saw, over 5 levels (levels 26 to 30): 32% "
                    "of the played time measured since level 23. Those levels have no kills, deaths, routes or zone "
                    "times on record, so they can't be checked for wasted time.",
            "action": {"do": "Keep the Polestar Recorder switched on for every session so the rest of the climb to "
                             "40 can be measured", "where": "In game, at the start of the next session",
                       "blocked_by": "", "next_step": "Type /psrec on once in game, then /reload, and check that "
                                                      "the next session shows up"}}
    v = grade.grade(sc, {"findings": {"recorder-off-levels-26-30": said}, "nudges": [], "patterns": []},
                    {"done": True}, [])
    assert v["verdict"] == "pass", v["checks"]
    for text, finding in (("Levels 26 to 30 look different from the rest of the history.", True),
                          ("Some levels have less to show than others.", True),
                          ("The Recorder missed levels 26 to 30.", False)):
        v = grade.grade(sc, _answer(text, finding), {"done": True}, [])
        assert v["verdict"] == "fail", (text, finding)


def test_a_rule_can_be_limited_to_findings():
    ls = grade.lines(ANALYSIS)
    head = {"id": "h", "all": [["embalmed shroud"]]}
    assert grade.matches(head, ls) and not grade.matches({**head, "kinds": ["finding"]}, ls)
    assert [m.key for m in grade.matches({"id": "w", "all": [["wetlands"]], "kinds": ["finding"]}, ls)] == [
        "wetlands-deaths"]


# ---------------------------------------------------------------- the client


def _tool_use(name: str) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": f"mcp__wow__{name}"}]}})


def test_a_run_cut_by_the_cap_still_reports_its_calls(tmp_path):
    """Review 5: two runs hit a 10-minute cap and reported 0 calls and no
    model, so nobody could tell a loop from a slow run. The partial stream
    counts: calls per tool, and the model."""
    lines = [json.dumps({"type": "system", "subtype": "init", "model": "test-model",
                         "mcp_servers": [{"name": "wow", "status": "connected"}]}),
             *[_tool_use("play_sql")] * 3, _tool_use("analysis_brief")]
    fake = tmp_path / "claude"
    fake.write_text(f"#!{sys.executable}\nimport sys, time\n"
                    f"sys.stdout.write({chr(10).join(lines)!r} + '\\n'); sys.stdout.flush(); time.sleep(30)\n")
    fake.chmod(0o755)
    (tmp_path / "work").mkdir()
    job = clients.Job(char="Nibbet-Gearholt", goals="", server=["true"], workdir=tmp_path / "work",
                      transcript=tmp_path / "t.jsonl", is_done=lambda: False, timeout_s=2)
    res = clients.ClaudeCode(binary=str(fake)).run(job)
    assert res.error.startswith("timed out") and not res.done
    assert res.calls == 4 and res.tools == {"play_sql": 3, "analysis_brief": 1} and res.model == "test-model"


def test_the_default_cap_leaves_room_for_a_normal_run():
    assert clients.TIMEOUT_MIN >= 20 and clients.Job("c", "", [], Path(), Path(), lambda: True).timeout_s == 1200


def test_two_runs_share_no_folder_and_no_port(tmp_path):
    """Run at the same time (run_all), each run owns its folder, state, work
    dir and transcript, two repeats of one scenario too, and its server
    talks over stdio: no port to collide on. The only address in the
    settings is the closed one nothing answers."""
    a = run.layout(tmp_path, "claude", "death_spike", 1)
    b = run.layout(tmp_path, "claude", "offline_gap", 1)
    c = run.layout(tmp_path, "claude", "death_spike", 2)
    for x, y in ((a, b), (a, c), (b, c)):
        assert set(map(str, x.values())).isdisjoint(map(str, y.values()))
    assert all(a["dir"] in [v, *v.parents] for v in a.values())
    sa = run.server_settings(scenarios.load("death_spike"), a["state"])
    sb = run.server_settings(scenarios.load("offline_gap"), b["state"])
    assert sa["ANALYSIS_STATE_DIR"] != sb["ANALYSIS_STATE_DIR"]
    urls = {k: v for k, v in {**sa, **sb}.items() if "URL" in k or "PORT" in k or "HOST" in k}
    assert urls == {"VL_URL": run.CLOSED_PORT_URL}


def test_runs_go_in_processes_of_their_own(tmp_path, monkeypatch):
    """The grader's rebuild points prep.query at one scenario's events, a
    process-wide seam: run_all gives each run a process (spawn) once more
    than one runs at a time, every client x scenario x repeat, in the order
    asked."""
    seen = {"runs": [], "workers": []}

    class Pool:
        def __init__(self, max_workers, mp_context):
            seen["workers"].append(max_workers)
            seen["method"] = mp_context.get_start_method()

        def shutdown(self):
            pass

        def submit(self, fn, name, kind, model, out, timeout_s, judge, repeat, opts):
            from concurrent.futures import Future

            seen["runs"].append((clients.column(kind, model), name, repeat))
            f = Future()
            f.set_result({"scenario": name, "repeat": repeat, "verdict": "pass", "checks": [], "kept": [],
                          "client": {"client": kind, "column": clients.column(kind, model), "seconds": 60,
                                     "calls": 1}})
            return f

    import concurrent.futures

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", Pool)
    cols = [("claude", ""), ("server", ""), ("server", "gpt-6-luna")]
    got = run.run_all(["offline_gap", "death_spike"], cols, tmp_path, 60, False, 8, 2)
    # The server client's runs are held to SERVER_JOBS at once, all its models
    # together: one provider's rate limit.
    assert seen["method"] == "spawn" and seen["workers"] == [8, run.SERVER_JOBS]
    assert len(set(seen["runs"])) == 12
    assert [(run.col_of(r), r["scenario"], r["repeat"]) for r in got] == seen["runs"]


def _report(client, name, verdict):
    return {"scenario": name, "verdict": verdict, "checks": [], "kept": [],
            "client": {"client": client, "model": "m", "seconds": 60, "calls": 1}}


def test_a_scenario_passes_the_gate_at_two_runs_of_three():
    assert run.gate_ok(3, 3) and run.gate_ok(2, 3)
    assert not run.gate_ok(1, 3) and not run.gate_ok(0, 3) and not run.gate_ok(0, 0)
    assert run.gate_ok(1, 1) and not run.gate_ok(0, 1)
    reports = [_report("claude", "death_spike", v) for v in ("pass", "fail", "pass")]
    reports += [_report("server", "death_spike", v) for v in ("fail", "error", "pass")]
    t = run.release_table(reports, 14, 6.5)
    row = next(ln for ln in t.splitlines() if ln.startswith("| death_spike"))
    assert row == "| death_spike | 2/3 | 1/3 **below 2/3** |"
    assert "claude (m)" in t and "$6.50" in t


def _col(col, name, verdict):
    kind = col.partition(":")[0]
    return {"scenario": name, "verdict": verdict, "checks": [], "kept": [],
            "client": {"client": kind, "column": col, "model": "m", "seconds": 60, "calls": 1}}


def test_a_matrix_flags_what_most_clients_miss():
    """The analysis runs in the player's own AI: a scenario most clients
    miss points at what MCP hands them, not at one model."""
    assert clients.parse("claude:claude-haiku-5-5") == ("claude", "claude-haiku-5-5")
    assert clients.parse("server", "gpt-6-luna") == ("server", "gpt-6-luna")
    with pytest.raises(SystemExit, match="unknown client"):
        clients.parse("gemini:x")
    ref, others = "claude:claude-sonnet-5-5", ["claude:claude-haiku-5-5", "server:gpt-6-luna", "server:glm-5.3"]
    reports = [_col(ref, "death_spike", "pass"), _col(ref, "offline_gap", "pass")]
    reports += [_col(c, "death_spike", "fail") for c in others]
    reports += [_col(c, "offline_gap", v) for c, v in zip(others, ("pass", "fail", "pass"), strict=True)]
    t = run.release_table(reports)
    assert ref in t.splitlines()[0] and "server:glm-5.3" in t.splitlines()[0] and "gate" not in t.splitlines()[0]
    assert list(run.widespread(reports)) == ["death_spike"]
    assert "**death_spike is below 2/3 on 3 of 4 clients**" in t and "offline_gap is below" not in t
    assert not run.gate(reports) and run.gate([_col(ref, "death_spike", "pass")])


def test_a_server_model_named_on_the_command_line_is_priced_as_itself(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-test", encoding="utf-8")
    env = {"EVAL_SERVER_KEY_FILE": str(key)}
    luna = clients.ServerRunner.from_env(env, model="gpt-6-luna")
    assert luna.prices["ANALYSIS_PRICE_INPUT_PER_MTOK"] == clients.MODEL_PRICES["gpt-6-luna"]["INPUT"]
    with pytest.raises(SystemExit, match="PRICE"):
        clients.ServerRunner.from_env(env, model="unknown-model")
    # A local endpoint may price itself at zero: still priced, so still capped.
    local = clients.ServerRunner.from_env({"EVAL_SERVER_BASE_URL": "http://127.0.0.1:9999/v1",
                                           "EVAL_SERVER_PRICE_INPUT_PER_MTOK": "0",
                                           "EVAL_SERVER_PRICE_OUTPUT_PER_MTOK": "0"}, model="local")
    assert local.prices["ANALYSIS_PRICE_INPUT_PER_MTOK"] == "0"
    # Each server model of a batch has its own cap and spend file.
    o = {"out": tmp_path, "max_usd": 12.0}
    a, b = run.spend_opts(o, "server", "server:gpt-6-luna"), run.spend_opts(o, "server", "server:glm-5.3")
    assert a["spend_file"] != b["spend_file"] and a["max_usd"] == 12.0 == clients.MAX_USD
    assert run.spend_opts(o, "claude", "claude") == {"max_usd": 12.0}


class _Fake:
    """An OpenAI-compatible SDK client that asks for analysis_progress
    forever, each answer billed $1 at the test's prices."""

    def __init__(self):
        from types import SimpleNamespace as N

        self.n = 0

        def create(**kw):
            self.n += 1
            call = N(id=f"c{self.n}", function=N(name="analysis_progress", arguments="{}"))
            return N(choices=[N(message=N(content="", tool_calls=[call], refusal=None), finish_reason="tool_calls")],
                     usage=N(prompt_tokens=1000, completion_tokens=0, prompt_tokens_details=None), model="stub")

        self.chat = N(completions=N(create=create))


def _server_env(tmp_path, budget):
    env = run.server_settings(scenarios.load("healthy_control"), tmp_path / "state")
    env.update({"ANALYSIS_PROVIDER": "openai", "ANALYSIS_MODEL": "stub", "ANALYSIS_BASE_URL": "http://127.0.0.1:9/v1",
                "ANALYSIS_PRICE_INPUT_PER_MTOK": "1000", "ANALYSIS_PRICE_OUTPUT_PER_MTOK": "1000",
                "ANALYSIS_MONTHLY_BUDGET": str(budget)})
    return env


def test_the_server_client_stops_at_the_batch_cap(tmp_path, monkeypatch):
    """The production runner, its provider stubbed: the batch's cap (the
    shared spend file) stops it before the call that would cross it, and
    the transcript has each call even though the run was cut."""
    import server_run

    from wowstats import analysis
    from wowstats.analysis import prep

    monkeypatch.setattr(prep, "query", prep.query)
    monkeypatch.setattr(analysis, "other_players", analysis.other_players)
    sc = scenarios.load("healthy_control")
    spend = tmp_path / "spend.json"
    fake = _Fake()
    r = server_run.run(_server_env(tmp_path, 3), scenarios.build(sc), scenarios.CHAR, tmp_path / "t.jsonl", spend,
                       client=fake)
    assert r["status"] == "stopped" and "budget spent" in r["why"], r
    assert fake.n == 3 and r["calls"] == 3
    assert clients.spent(spend) == pytest.approx(3.0)
    res = clients.Result("server")
    clients.ServerRunner.read_transcript(tmp_path / "t.jsonl", res)
    assert res.tools == {"analysis_progress": 3} and res.tokens == {"input": 3000, "output": 0, "cache_read": 0,
                                                                     "cache_write": 0}


def test_runs_going_at_once_add_to_one_spend(tmp_path):
    """Two Spend objects on one file, as two processes hold them: the file
    lock keeps every addition."""
    import threading

    import server_run

    path = tmp_path / "spend.json"
    a, b = server_run.shared_spend(path), server_run.shared_spend(path)

    def add(s):
        for _ in range(40):
            s.add(0.01, {"input": 1}, "0")

    ts = [threading.Thread(target=add, args=(s,)) for s in (a, b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert a.this_month()["calls"] == 80 and clients.spent(path) == pytest.approx(0.8)


def test_the_server_client_takes_only_a_key_file_and_needs_prices(tmp_path):
    key = tmp_path / "key"
    key.write_text("sk-test", encoding="utf-8")
    with pytest.raises(SystemExit, match="EVAL_SERVER_KEY_CMD"):
        clients.ServerRunner.from_env({"OPENAI_API_KEY": "sk-from-env"})
    with pytest.raises(SystemExit, match="PRICE"):
        clients.ServerRunner.from_env({"EVAL_SERVER_KEY_FILE": str(key), "EVAL_SERVER_MODEL": "other"})
    c = clients.ServerRunner.from_env({"EVAL_SERVER_KEY_FILE": str(key)}, max_usd=10)
    assert c.model == clients.SERVER_MODEL and c.provider == "openai"
    assert c.prices["ANALYSIS_PRICE_INPUT_PER_MTOK"] == clients.SERVER_PRICES["INPUT"]
    s = c.settings({"ANALYSIS_STATE_DIR": "/x", "ANALYSIS_WIF_TOKEN_FILE": "/var/run/token"})
    assert not [k for k in s if k.startswith("ANALYSIS_WIF_")]
    assert s["ANALYSIS_API_KEY_FILE"] == str(key) and "sk-test" not in json.dumps(s)
    assert s["ANALYSIS_MONTHLY_BUDGET"] == "10"


def test_the_server_client_reads_the_host_not_a_substring():
    """OpenAI's own host needs the evaluator's key; a host that only contains
    its name is a local endpoint like any other (CodeQL on #115)."""
    prices = {"EVAL_SERVER_PRICE_INPUT_PER_MTOK": "1", "EVAL_SERVER_PRICE_OUTPUT_PER_MTOK": "1",
              "EVAL_SERVER_MODEL": "m"}
    with pytest.raises(SystemExit, match="EVAL_SERVER_KEY_CMD"):
        clients.ServerRunner.from_env({**prices, "EVAL_SERVER_BASE_URL": "https://api.openai.com/v1"})
    c = clients.ServerRunner.from_env({**prices, "EVAL_SERVER_BASE_URL": "http://api.openai.com.example:8080/v1"})
    assert c.base_url == "http://api.openai.com.example:8080/v1"


def test_the_server_client_end_to_end_on_a_stub_endpoint(tmp_path):
    """The whole client path, its own process included (settings file,
    replaced environment, result on stdout, transcript), against a local
    OpenAI-compatible stub: the batch cap cuts it after three $1 calls."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    n = {"calls": 0, "auth": set()}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            n["calls"] += 1
            n["auth"].add(self.headers.get("Authorization"))
            body = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "stub", "choices": [{
                "index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                "tool_calls": [{"id": f"c{n['calls']}", "type": "function",
                                "function": {"name": "analysis_progress", "arguments": "{}"}}]}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 0, "total_tokens": 1000}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = clients.ServerRunner.from_env({
            "EVAL_SERVER_MODEL": "stub", "EVAL_SERVER_BASE_URL": f"http://127.0.0.1:{srv.server_port}/v1",
            "EVAL_SERVER_PRICE_INPUT_PER_MTOK": "1000", "EVAL_SERVER_PRICE_OUTPUT_PER_MTOK": "1000",
            "EVAL_SERVER_KEY_CMD": "printf ' sk-eval-%s\\n' 4242"},
            spend_file=tmp_path / "spend.json", max_usd=3)
        p = run.layout(tmp_path, "server", "healthy_control")
        p["work"].mkdir(parents=True)
        sc = scenarios.load("healthy_control")
        scenarios.write_events(scenarios.build(sc), p["events"])
        p["settings"].write_text(json.dumps(run.server_settings(sc, p["state"])), encoding="utf-8")
        job = clients.Job(char=scenarios.CHAR, goals="", server=[], workdir=p["work"], transcript=p["transcript"],
                          is_done=lambda: False, timeout_s=120, settings=p["settings"], events=p["events"])
        res = c.run(job)
    finally:
        srv.shutdown()
    assert n["calls"] == 3
    assert "budget spent" in res.error and res.cost_usd == pytest.approx(3.0)
    assert res.tools == {"analysis_progress": 3} and res.model == "openai:stub" and not res.done
    # The key from EVAL_SERVER_KEY_CMD reached the endpoint, and no file the run wrote.
    assert n["auth"] == {"Bearer sk-eval-4242"}
    for f in tmp_path.rglob("*"):
        assert not f.is_file() or b"sk-eval-4242" not in f.read_bytes(), f


def test_a_failing_key_command_shows_nothing_it_printed(tmp_path):
    c = clients.ServerRunner.from_env({"EVAL_SERVER_KEY_CMD": "echo sk-partial; exit 3"})
    with pytest.raises(SystemExit) as e:
        c.key()
    assert "exit 3" in str(e.value) and "sk-partial" not in str(e.value)
