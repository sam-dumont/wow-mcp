"""The analysis tools over MCP: the prompt as a tool, the brief, the judge."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from wowstats.analysis import drive, load_state, prep
from wowstats.mcp import prompts, tools

DATA = Path(__file__).resolve().parent.parent / "data" / "analysis-events.jsonl.gz"
CHAR = "Testpriest-Fixture"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    with gzip.open(DATA) as f:
        rows = [json.loads(x) for x in f]
    monkeypatch.setattr(prep, "query", lambda store, stream, char: [dict(r) for r in rows])
    drive._WS.clear()
    e = {"ANALYSIS_STATE_DIR": str(tmp_path), "KB_GAME_TABLES": "off", "VL_URL": "http://unused"}
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    return e


class FakeStore:
    vl_url, vl_archive_url, logs_filter, hot_days, stream = "http://unused", "", "feed:wow-*", 89, "feed:wow-*"


def call(name, args, char=CHAR):
    return tools.call(tools.Ctx(store=FakeStore(), default_char=CHAR), name, {"char": char, **args} if char else args)


def test_the_tools_drive_a_run(env):
    a = call("analyse", {"goals": "Gear for dungeons."})
    assert "analysis_brief" in a["instructions"] and "Gear for dungeons." in a["instructions"]
    b = call("analysis_brief", {"goals": "Gear for dungeons."})
    assert b["goals"] == "Gear for dungeons." and b["next"]["pass"] == "survey"
    assert "levels" in call("analysis_brief", {"section": "schema"})["text"]
    q = call("play_sql", {"query": "SELECT level, played_s FROM levels ORDER BY level LIMIT 3"})
    assert q["result"].startswith("level\tplayed_s") and not q["more"]
    sql = "SELECT COUNT(*) AS n FROM levels"
    n = drive.workspace(env, CHAR).judge.first_row(sql)["n"]
    good = {"key": "levels-count", "title": "{n} levels", "claim": "You have {n} levels on record.", "sql": sql,
            "expect": {"n": n}, "relevance": 1, "kb": ["note:nothing-here"]}
    ok = call("propose_finding", {"finding": json.dumps(good)})  # JSON text, as some clients send it
    assert ok["stored"] and ok["new"] and str(n) in ok["finding"]["text"] and ok["kb_unknown"] == ["note:nothing-here"]
    bad = call("propose_finding", {"finding": {**good, "key": "levels-wrong", "expect": {"n": n + 5}}})
    assert not bad["stored"] and "re-run" in bad["why"]
    typed = call("propose_finding", {"finding": {**good, "key": "levels-typed", "claim": "You have 4210 levels."}})
    assert not typed["stored"] and "number written" in typed["why"]
    assert list(load_state(env, CHAR)["findings"]) == ["levels-count"]
    # A refusal fixed on the retry stops counting as one.
    fixed = call("propose_finding", {"finding": {**good, "key": "levels-wrong"}})
    assert fixed["stored"]
    call("kb_lookup", {"ids": ["quest:0"]})
    pr = call("analysis_progress", {})["run"]
    assert pr["queries"] == 1 and pr["proposed"] == 4 and pr["verified"] == 2 and len(pr["refused"]) == 1
    assert pr["fixed_on_retry"] == 1
    assert pr["kb_calls"] == 1 and "levels" not in pr["untouched"] and pr["untouched"]
    # A run's tools name the character every time: no silent default.
    with pytest.raises(tools.ToolError, match="char is required"):
        call("play_sql", {"query": "SELECT 1"}, char=None)
    few = call("analysis_submit", {"pass": "survey", "answer": {"questions": ["Only one?"]}})
    assert not few["accepted"] and "at least" in few["why"]
    # The kept analysis is the character's own.
    assert {f["key"] for f in call("play_analysis", {})["findings"]} == {"levels-count", "levels-wrong"}
    assert {f["key"] for f in call("whats_new", {})["new"]} == {"levels-count", "levels-wrong"}


def test_the_prompt_and_the_ask_links_carry_no_data():
    text = prompts.analyse_text(CHAR, "")
    assert CHAR in text and "analysis_progress" in text
    msg = prompts.analyse_messages(CHAR, "be quick")[0]["content"]["text"]
    assert "be quick" in msg
    assert "analyse" in {p["name"] for p in prompts.PROMPTS}


def test_the_store_is_read_in_small_pages():
    """The events' VictoriaLogs may run with a 1 GiB limit: one character, small pages, nothing cut."""
    seen = []

    class Store:
        def vl(self, query, keys=(), ops=None, limit=0):
            seen.append((query, limit))
            return []

    prep.query(Store(), "feed:wow-*", CHAR)
    assert seen == [(f'feed:wow-* char:in("Testpriest Fixture", "{CHAR}") -feed:wow-player -feed:wow-prices '
                     '-feed:wow-combatlog', prep.VL_PAGE)] and prep.VL_PAGE <= 5000
