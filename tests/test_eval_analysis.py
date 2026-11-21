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

import grade  # noqa: E402
import scenarios  # noqa: E402

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
        "wrong_spec_gear": "Replace the Filigreed Shadow Circlet with the Embalmed Shroud.",
        "healthy_control": "Your deaths are spread out: too few to call any zone dangerous.",
    }
    wrong = {
        "death_spike": "Your deaths are evenly spread.",
        "recorder_off": "You had no deaths at levels 27 to 29.",
        "offline_gap": "Level 33 was your slowest level.",
        "wrong_spec_gear": "Keep the Filigreed Shadow Circlet: it's an upgrade.",
        "healthy_control": "The Wetlands are your most dangerous zone: most of your deaths happen there.",
    }
    for n in ALL:
        sc = scenarios.load(n)
        for text, want in ((right[n], "pass"), (wrong[n], "fail")):
            nudge = {"text": text, "gist": "", "where": "", "blocked_by": "", "next_step": ""}
            v = grade.grade(sc, {"findings": {}, "nudges": [nudge], "patterns": []}, {"done": True}, [])
            assert v["verdict"] == want, (n, text, v["checks"])
