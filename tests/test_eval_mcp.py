"""tools/eval_mcp: the MCP check's expected answers and its grader, without
a model. The answers come from the demo's data and are unambiguous; the
grader matches exactly or by its stated normalization; the run's command
gives the model this checkout's server and nothing else."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tools" / "eval_mcp"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import answers  # noqa: E402
import grading as G  # noqa: E402

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


@pytest.fixture(scope="module")
def computed():
    pytest.importorskip("fastmcp")
    return answers.compute()


def test_ten_questions_each_with_a_computed_answer(computed):
    qs = run.load_questions()
    assert len(qs) == 10
    assert {q["answer"] for q in qs} == set(computed)
    for q in qs:
        assert q["match"] in ("text", "number", "yesno", "pct_line"), q["id"]
        v = computed[q["answer"]]
        assert v not in (None, ""), q["id"]
        # The answer grades as right against itself, written the way a model would.
        assert G.grade(f"{v}", v, q["match"], q.get("tolerance", 0)), q["id"]


def test_answers_on_the_demo(computed):
    rows = D.rows_for("full")
    # Deaths at a level: each death's level from the level splits.
    by_level = {}
    for r in answers.deaths(rows):
        lv = answers.level_at(rows, answers.parse_t(r["_time"]))
        by_level[lv] = by_level.get(lv, 0) + 1
    assert computed["deaths_at_level_32"] == by_level.get(32, 0) > 0
    assert sum(by_level.values()) == len(answers.deaths(rows))
    assert isinstance(computed["level_27_minutes"], int) and computed["level_27_minutes"] > 0
    assert computed["recorder_missed_32"] in ("Yes", "No")
    assert isinstance(computed["next_guide_step"], int)
    assert G.pct_pair(computed["zone_quest_upgrade_line"]) is not None
    assert computed["longest_break_h"] > 3  # a night between evenings at least


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


def test_the_table_flags_play_sql_only():
    r = {"id": "q", "expected": 2, "got": "2", "pass": True, "tools": ["play_sql", "play_sql"],
         "sql_only": True, "seconds": 3.2, "error": ""}
    text = run.summary([r, {**r, "id": "p", "got": None, "pass": False, "tools": [], "sql_only": False,
                            "error": "timed out after 180 s"}], "haiku", 12)
    assert "1/2 with haiku" in text and "play_sql only: q." in text
    assert "| q | 2 | 2 | yes | play_sql **(play_sql only)** (2) | 3 |" in text
    assert "| p | 2 | timed out after 180 s | NO | none (0) |" in text
