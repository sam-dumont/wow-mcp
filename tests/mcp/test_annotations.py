"""Each MCP tool's annotations say what it does to the person's state, and
running it shows it: every tool used to say readOnlyHint=true, the ones that
open, change or restart an analysis run and plan_session too (review,
2026-11-19)."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from tests.mcp import fakes
from wowstats import plan
from wowstats.analysis import drive, prep
from wowstats.mcp import server, tools
from wowstats.mcp.store import Store

DATA = Path(__file__).resolve().parent.parent / "data"
CHAR = fakes.CHAR

# Kept by hand: a new tool fails here until someone says what it does.
# "counts": only counts the call in an open run (annotated read-only, 2026-11-23);
# "replaces": can overwrite or drop.
WRITERS = {
    "analysis_brief": "replaces",  # opens a run; restart drops the open one; goals replace the kept ones
    "play_sql": "counts",  # counts the query and the tables in the run's coverage
    "query_metric": "counts",  # the same bookkeeping as play_sql
    "propose_finding": "replaces",  # a finding with a kept key replaces it
    "analysis_submit": "replaces",  # the synthesis replaces the kept findings
    "kb_get": "counts",  # with char: counts a KB call in the open run
    "kb_lookup": "counts",
    "kb_search": "counts",
    "plan_session": "replaces",  # keeps the plan check_plan reads; the oldest drop past 30
}
# Writes after the call returns, in the background. None since
# dashboard_refresh moved to the client (2026-11-21): it starts nothing on the
# server and reaches no AI provider.
LATER: set[str] = set()
OPEN_WORLD: set[str] = set()
READ_ONLY = {"list_metrics", "character_summary", "levels", "sessions", "zones", "guide_steps", "deaths", "dungeons", "players",
             "prices", "inventory", "gear", "next_moves", "sanity_check", "route_reference", "market", "play_analysis",
             "check_plan", "how_did_my_plan_go", "next_step", "mount_goal",
             "whats_new", "kb_rule", "kb_topics", "analyse", "analysis_progress", "dashboard_refresh", "class_guide", "class_meta", "news", "talent_changes"}
ALL = (*tools.TOOLS, tools.MARKET)


def test_every_tool_is_classified():
    names = {t.name for t in ALL}
    assert names == set(WRITERS) | READ_ONLY, names ^ (set(WRITERS) | READ_ONLY)
    assert not set(WRITERS) & READ_ONLY
    for t in ALL:
        assert t.writes == WRITERS.get(t.name, ""), t.name


def test_the_server_lists_each_tools_own_annotations():
    async def listed():
        return {t.name: t.annotations for t in await server.build_server(
            server.Who(base=tools.Ctx(store=None))).list_tools()}

    ann = asyncio.run(listed())  # market is listed only to a person with the Battle.net setting on
    assert set(ann) == {t.name for t in tools.TOOLS}
    ann["market"] = server.annotations(tools.MARKET)
    for name, a in ann.items():
        w = WRITERS.get(name, "")
        assert a.read_only_hint is (w in ("", "counts")), name
        if w == "replaces":
            assert a.destructive_hint is (w == "replaces"), name
            assert a.idempotent_hint is False, name
        assert a.open_world_hint is (name in OPEN_WORLD), name


def snapshot(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*"))
            if p.is_file()}


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    with gzip.open(DATA / "analysis-events.jsonl.gz") as f:
        rows = [json.loads(x) for x in f]
    monkeypatch.setattr(prep, "query", lambda store, stream, char: [dict(r) for r in rows])
    with gzip.open(DATA / "plan-inputs.json.gz") as f:
        priest = json.loads(f.read())
    monkeypatch.setattr(plan, "inputs", lambda env, char=None, store=None, battlenet=None, gear_inputs=None, mode=None, pvp=None: {**priest, "who": {
        **priest["who"], "char": CHAR}})
    drive._WS.clear()
    state = tmp_path / "state"
    for k, v in {"ANALYSIS_STATE_DIR": str(state), "KB_GAME_TABLES": "off"}.items():
        monkeypatch.setenv(k, v)
    fake = fakes.FakeStores()
    ctx = tools.Ctx(store=Store(vl_url=fake.url, vm_url=fake.url), default_char=CHAR, bnet=True)
    yield ctx, state
    fake.close()


# Every tool, in an order that gives the writers something to work on.
CALLS = [
    ("analyse", {"goals": "Gear for dungeons."}),
    ("analysis_progress", {}),
    ("analysis_brief", {"goals": "Gear for dungeons."}),
    ("analysis_brief", {"section": "schema"}),
    ("play_sql", {"query": "SELECT COUNT(*) AS n FROM levels"}),
    ("list_metrics", {}),
    ("query_metric", {"metrics": ["played_s", "online_s"], "group_by": ["level"]}),
    ("propose_finding", {"finding": {"key": "levels-count", "title": "{n} levels", "claim": "You have {n} levels.",
                                     "sql": "SELECT COUNT(*) AS n FROM levels", "relevance": 1, "action": None}}),
    ("kb_search", {"query": "riding", "char": CHAR}),
    ("kb_get", {"ids": ["note:xp-sources"]}),
    ("kb_lookup", {"ids": ["quest:56"]}),
    ("analysis_submit", {"pass": "survey", "answer": {"questions": [
        {"question": f"How long does level {n} take?", "tables": ["levels"]} for n in range(10, 22)]}}),
    ("analysis_progress", {}),
    ("play_analysis", {}),
    ("whats_new", {}),
    ("plan_session", {"hours": 2, "constraints": "no dungeons"}),
    ("check_plan", {"text": "No dungeons."}),
    ("how_did_my_plan_go", {}),
    ("next_step", {}),
    ("gear", {"options": True}),
    ("dungeons", {"options": True}),
    ("mount_goal", {}),
    ("kb_rule", {"name": "riding.apprentice"}),
    ("kb_topics", {}),
    ("kb_search", {"query": "riding"}),
    ("class_guide", {}),
    ("class_meta", {}),
    ("talent_changes", {}),
    ("news", {}),
    ("character_summary", {}), ("levels", {}), ("sessions", {"since": "2026-11-01"}), ("zones", {}),
    ("guide_steps", {}), ("deaths", {}), ("dungeons", {}), ("players", {}), ("prices", {}), ("inventory", {}), ("gear", {}),
    ("next_moves", {}), ("sanity_check", {}), ("players", {"groups": True}), ("route_reference", {}), ("market", {}),
    ("dashboard_refresh", {}),
]


def test_running_each_tool_matches_its_annotation(lab):
    ctx, state = lab
    assert {n for n, _ in CALLS} == {t.name for t in ALL}
    registry = tools.mcp_tools(ctx)
    wrote: set[str] = set()
    for name, args in CALLS:
        before = snapshot(state) if state.exists() else {}
        out = tools.call(ctx, name, {"char": CHAR, **args} if "char" in tools.BY_NAME.get(
            name, tools.MARKET).input_schema["properties"] else args, registry)
        # A real answer, not an early refusal that would prove nothing.
        # (how_did_my_plan_go needs play past a checkpoint, which no fixture has.)
        assert isinstance(out, dict) and (out.get("answer", True) is not None or name == "how_did_my_plan_go"), (
            name, out)
        after = snapshot(state) if state.exists() else {}
        if after != before:
            wrote.add(name)
            assert name in WRITERS, f"{name} changed the state but says it is read-only"
    # Each writer did write in this run: none is marked a writer for nothing.
    assert wrote == set(WRITERS) - LATER, set(WRITERS) - LATER - wrote
