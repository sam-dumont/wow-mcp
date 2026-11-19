"""The knowledge base through the MCP tools: kb_topics lists the research
lanes, kb_rule shows a lane's differing version as a conflict, kb_search
finds sourced statements with their last_verified."""

from __future__ import annotations

import pytest

from wowstats.mcp import tools


@pytest.fixture(scope="module")
def ctx():
    return tools.Ctx(store=None, env={"KB_GAME_TABLES": "off"})


def test_kb_topics_lists_the_lanes_and_one_lane_in_full(ctx):
    out = tools.call(ctx, "kb_topics", {})
    assert {"dungeons", "game-rules", "levelling", "economy-professions", "pvp", "raids-endgame"} <= set(out["lanes"])
    assert all(v["last_verified"] and v["statements"] for v in out["lanes"].values())
    d = tools.call(ctx, "kb_topics", {"lane": "dungeons"})
    assert d["contradictions"] and all(c["source_url"].startswith("https://") for c in d["contradictions"])
    assert d["open_questions"] and "unknown" in d["note"]
    assert tools.call(ctx, "kb_topics", {"lane": "nope"})["known"]


def test_kb_rule_names_its_conflicts(ctx):
    r = tools.call(ctx, "kb_rule", {"name": "rested.cap"})["rule"]
    assert r["last_verified"] and r["source_url"].startswith("https://")
    assert [c["name"] for c in r["conflicts"]] == ["rested.cap@levelling"]
    assert tools.call(ctx, "kb_rule", {"name": "riding.apprentice"})["rule"]["value"]["training_copper"] == 1000000


def test_kb_search_finds_statements_with_their_dates(ctx):
    out = tools.call(ctx, "kb_search", {"query": "dungeon kill XP twelve levels party", "kind": "kb-statement"})
    hits = out["results"] or tools.call(ctx, "kb_search", {"query": "12 levels above", "kind": "kb-statement"})[
        "results"]
    assert hits and all(h["last_verified"] and h["scope"] in ("forever", "classic_fallback") for h in hits)
