"""The MCP's tool surface after the 2026-11-23 consolidation: one tool per
question (gear, dungeons, players carry what gear_options, dungeon_options
and people answered), a list_metrics index that fits a client's output
limit, errors that say they are errors, and lookups annotated as reads."""

from __future__ import annotations

import json

import pytest

from tests.mcp import fakes, inmem
from tests.mcp.test_annotations import lab  # noqa: F401 - the fixture
from wowstats.analysis import semantic
from wowstats.mcp import analysistools, server, tools
from wowstats.mcp.store import Store

CHAR = fakes.CHAR
# Claude Code's default MCP output limit is about 25,000 tokens; the server's
# own analysis runner cuts a tool result at 40,000 characters (auto.RESULT_CHARS).
INDEX_BUDGET = 32_000


def size(v) -> int:
    return len(json.dumps(v, ensure_ascii=False, separators=(",", ":")))


def test_the_paired_tools_are_one_each():
    names = {t.name for t in tools.TOOLS}
    assert not names & {"gear_options", "dungeon_options", "people"}
    assert {"gear", "dungeons", "players"} <= names
    props = {n: tools.BY_NAME[n].input_schema["properties"] for n in ("gear", "dungeons", "players")}
    assert props["gear"]["options"]["type"] == "boolean"
    assert props["dungeons"]["options"]["type"] == "boolean"
    assert props["players"]["groups"]["type"] == "boolean"


def test_options_and_groups_answer_what_the_old_tools_did(lab):  # noqa: F811
    ctx, _ = lab
    g = tools.call(ctx, "gear", {"char": CHAR, "options": True, "limit": 20})
    assert g["slots"] and all("options" in s for s in g["slots"]) and "kinds" in g and "zone" in g
    d = tools.call(ctx, "dungeons", {"char": CHAR, "options": True})
    assert d["dungeons"] and {"rank", "fits_now", "gain"} <= set(d["dungeons"][0])
    assert "questing_xp_per_hour" in d
    runs = tools.call(ctx, "dungeons", {"char": CHAR})
    assert "runs" in runs and "total" in runs
    p = tools.call(ctx, "players", {"char": CHAR, "groups": True})
    assert {"players", "dungeon_runs", "rosters", "sources"} <= set(p) and p["untrusted"]
    assert "dungeon_runs" not in tools.call(ctx, "players", {"char": CHAR})


def test_options_score_in_the_mode_asked(lab, monkeypatch):  # noqa: F811
    """gear with options in PvE scores PvE's weights and says so (MCP eval
    pve_shoulder, 2026-11-24: it answered the Leveling picks, no `mode`).
    The eval's probe checks the picks on the demo; here, that the mode
    reaches the planner's inputs."""
    from wowstats import plan

    ctx, _ = lab
    fake, seen = plan.inputs, []
    monkeypatch.setattr(plan, "inputs", lambda *a, mode=None, **kw: seen.append(mode) or fake(*a, **kw))
    lvl = tools.call(ctx, "gear", {"char": CHAR, "options": True})
    pve = tools.call(ctx, "gear", {"char": CHAR, "options": True, "mode": "pve"})
    assert seen == [None, "pve"]
    assert lvl["mode"]["key"] == "lvl" and pve["mode"]["key"] == "pve" and pve["mode_from"] == "asked"


@pytest.mark.parametrize("tool, args, says", [
    ("gear", {"options": True, "spec": "holy"}, "spec"),
    ("dungeons", {"options": True, "since": "7d"}, "since"),
])
def test_arguments_that_dont_go_with_options_are_refused(lab, tool, args, says):  # noqa: F811
    with pytest.raises(tools.ToolError, match=says):
        tools.call(lab[0], tool, {"char": CHAR, **args})


def test_list_metrics_without_a_filter_is_a_compact_index():
    out = analysistools.t_list_metrics(None, {})
    assert size(out) < INDEX_BUDGET, size(out)
    cat = semantic.catalogue()["metrics"]
    listed = {n for ms in out["models"].values() for n in ms}
    assert listed == {m["name"] for m in cat}
    assert set(out["models"]) == {m["model"] for m in cat}
    assert all(isinstance(d, str) and len(d) <= 120 for ms in out["models"].values() for d in ms.values())
    assert "names" in out["how"] and "model" in out["how"]


def test_list_metrics_filters_return_full_definitions():
    got = analysistools.t_list_metrics(None, {"names": ["played_s", "per_level"]})
    m = {x["name"]: x for x in got["metrics"]}
    assert set(m) == {"played_s"} and {"caveats", "group_by", "aggregation"} <= set(m["played_s"])
    assert [v["name"] for v in got["views"]] == ["per_level"] and got["views"][0]["columns"]
    by_model = analysistools.t_list_metrics(None, {"model": "levels"})
    assert by_model["metrics"] and {x["model"] for x in by_model["metrics"]} == {"levels"}
    assert size(by_model) < INDEX_BUDGET
    found = analysistools.t_list_metrics(None, {"search": "death"})
    assert found["metrics"] and all("death" in (x["name"] + x["description"]).lower() for x in found["metrics"])
    store = analysistools.t_list_metrics(None, {"model": "players"})
    assert store["tool_metrics"] and not store["metrics"]


@pytest.mark.parametrize("args", [{"names": ["no_such_metric"]}, {"model": "no_such_model"}])
def test_list_metrics_refuses_an_unknown_name(args):
    with pytest.raises(tools.ToolError, match="list_metrics"):
        analysistools.t_list_metrics(None, args)


@pytest.fixture()
def fake_ctx():
    # What arrived per character (wowstats.missing.ARRIVED_Q): both fixture characters.
    arrived = [{"char": c, "feed": "wow-level", "n": 3, "last": "2026-11-17T21:40:00Z"} for c in (CHAR, fakes.ALT)]
    fake = fakes.FakeStores(logs=[("feed:* | stats by (char, feed", arrived)] + fakes.PREP + fakes.LOGS)
    yield tools.Ctx(store=Store(vl_url=fake.url, vm_url=fake.url))
    fake.close()


def test_an_unknown_character_is_an_error(fake_ctx):
    r = inmem.call(fake_ctx, "levels", {"char": "Nobody-Nowhere"})
    assert r.is_error
    text = r.content[0].text
    assert "Nobody-Nowhere" in text and CHAR in text and len(text) < 300
    # Known, spelled with a space: still that character.
    assert not inmem.call(fake_ctx, "levels", {"char": CHAR.replace("-", " ")}).is_error


def test_a_known_character_with_no_data_yet_is_not_an_error(fake_ctx):
    ctx = tools.Ctx(store=fake_ctx.store, default_char="Newbie-Fixture")
    r = inmem.call(ctx, "character_summary", {"char": "Newbie-Fixture"})
    assert not r.is_error, r.content[0].text


def test_an_unknown_metric_is_an_error(lab):  # noqa: F811
    r = inmem.call(lab[0], "query_metric", {"char": CHAR, "metrics": ["no_such_metric"]})
    assert r.is_error and "list_metrics" in r.content[0].text


def test_lookups_are_annotated_as_reads():
    for name in ("kb_get", "kb_lookup", "kb_search", "play_sql", "query_metric"):
        a = server.annotations(tools.BY_NAME[name])
        assert a.read_only_hint is True, name
