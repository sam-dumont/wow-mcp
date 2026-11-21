"""Truthful incompleteness (property 7): a cap, a read that can't be whole,
a store that doesn't answer and a question outside a metric's scope always
say so. Never a silent partial.

- Caps: every metric of the catalogue, grouped so it has rows to cut, says
  `more` exactly when a row is left out; play_sql and query_metric say it
  when their text is cut too.
- IncompleteRead and outages: every MCP tool, on the demo history, either
  refuses with the public error or gives the answer it gives on a healthy
  store; the page's answers refuse (the HTTP layer turns that into 502).
- Unsupported scopes: a metric asked by a dimension it doesn't have is
  refused, every pair of the catalogue.

Builds on tests/test_bounded_reads.py (the cache's IncompleteRead) and
tests/analysis/test_semantic_evidence.py (F: `more` on one metric).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import degraded as D
from tests.invariants import histories as H
from wowstats import stores
from wowstats.analysis.sql import QueryError

pytestmark = pytest.mark.usefixtures("demo_clock")

DOWN = "http://127.0.0.1:9"  # nothing listens: connection refused
PARTIAL = "no answer rather than a partial one"
DIDNT = "the data store didn't answer"


# ---------------------------------------------------------------- caps


@pytest.fixture(scope="module")
def demo_layer():
    return H.layer(H.build(H.rows_of(None)))


def _grouping(m: dict) -> list[str]:
    for d in ("zone", "level", "time", "session_id", "kind", "dungeon"):
        if d in m["group_by"]:
            return [d]
    return []


def test_more_is_true_exactly_when_rows_are_left_out(demo_layer):
    checked = 0
    for m in H.catalogue():
        by = _grouping(m)
        q = {"metrics": [m["name"]], "group_by": by} | ({"grain": "day"} if by == ["time"] else {})
        every = H.ask(*demo_layer, **q, limit=500)
        n = len(every["rows"])
        assert not every["more"] or n == 500, m["name"]
        if n < 2:
            continue
        cut = H.ask(*demo_layer, **q, limit=n - 1)
        assert (len(cut["rows"]), cut["more"]) == (n - 1, True), m["name"]
        whole = H.ask(*demo_layer, **q, limit=n)
        assert (len(whole["rows"]), whole["more"]) == (n, False), m["name"]
        checked += 1
    assert checked >= 20


@pytest.fixture
def analysis_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("full"))
    env = {"ANALYSIS_STATE_DIR": str(tmp_path), "WOW_ITEMS": "off", "DEFAULT_CHAR": D.CHAR, "VL_URL": vl.url}
    yield env, vl
    vl.close()


def _mcp(url: str, env: dict):
    from wowstats.mcp import tools as T
    from wowstats.mcp.store import Store as MStore

    # Each store its own analysis state: a run one store started isn't the other's.
    state = Path(env["ANALYSIS_STATE_DIR"]) / str(abs(hash(url)))
    state.mkdir(exist_ok=True)
    ctx = T.Ctx(store=MStore(vl_url=url, vm_url=url), default_char=D.CHAR, env={**env, "ANALYSIS_STATE_DIR": str(state)})
    return lambda name, **a: T.call(ctx, name, a)


def test_a_cut_text_says_more(analysis_env):
    """play_sql pages its rows; its text is also cut at a size. Either cut
    says `more`."""
    env, vl = analysis_env
    call = _mcp(vl.url, env)
    call = (lambda c: lambda name, **a: c(name, char=D.CHAR, **a))(call)
    wide = "SELECT rec_id, " + ", ".join(["time_local || ' ' || time_utc || ' ' || kind"] * 6) + \
        " AS w FROM records ORDER BY rec_id LIMIT 40"
    got = call("play_sql", query=wide, page_size=40)
    assert "(cut:" in got["result"]
    assert got["more"] is True
    few = call("play_sql", query="SELECT kind, COUNT(*) AS n FROM records GROUP BY kind ORDER BY n DESC LIMIT 5")
    assert few["more"] is False and "(cut" not in few["result"]


# ---------------------------------------------------------------- outages and incomplete reads


def _fresh() -> None:
    """No answer kept from an earlier store: each run reads its own."""
    from wowstats import plan as planmod
    from wowstats.analysis import drive
    from wowstats.mcp import tools as T

    planmod._CACHE.clear()
    T._VIEWS.clear()
    T._MISSING.clear()
    drive._WS.clear()  # a prep the healthy store built


ARGS = {"play_sql": {"query": "SELECT COUNT(*) AS n FROM records"}, "query_metric": {"metrics": ["deaths"]},
        "kb_search": {"query": "ragefire"}, "check_plan": {"text": "Quest in Darkshore."},
        "kb_get": {"ids": ["zone:148"]}, "kb_lookup": {"ids": ["zone:148"]}}


# Moments, not answers: when a run or an answer was made (the clock ticks).
MOMENTS = {"started", "generated_at", "built_at"}


def _still(v, key: str = ""):
    if isinstance(v, dict):
        return {k: _still(x, k) for k, x in v.items() if k not in MOMENTS and not (key == "run" and k == "id")}
    if isinstance(v, list):
        return [_still(x) for x in v]
    return v


# Tools that keep what they're given: nothing to read back here.
STORING = {"propose_finding", "analysis_submit"}


def _every_tool(call) -> dict:
    from wowstats.mcp import tools as T

    out = {}
    # check_plan reads the plan plan_session keeps: ask it before one is made,
    # so both stores start from the same state.
    for t in sorted(T.TOOLS, key=lambda t: t.name != "check_plan"):
        if t.name in STORING:
            continue
        args = ARGS.get(t.name, {}) | ({"char": D.CHAR} if "char" in t.input_schema["properties"] else {})
        try:
            got = call(t.name, **args)
            # The missing-sources footnote is dropped when the store fails (tools.missing_for);
            # the coverage note is checked on its own (below).
            out[t.name] = ("answer", _still({k: v for k, v in got.items() if k not in ("missing", "coverage")})
                           if isinstance(got, dict) else got)
            if isinstance(got, dict) and got.get("coverage"):
                out[t.name] += (got["coverage"],)
        except T.ToolError as e:
            out[t.name] = ("refused", str(e))
    return out


def _overfull(rows: list[dict]) -> list[dict]:
    """The demo plus one second holding more of the character's records
    than a read returns (stores.VL_PAGE is set below it)."""
    t = rows[len(rows) // 2]["_time"]
    burst = [{"_time": t, "char": D.CHAR, "component": "wow", "feed": "wow-recorder", "kind": "pos",
              "layer": "app", "source_id": f"rec:9:9999:{i}"} for i in range(80)]
    return rows + burst


@pytest.mark.parametrize("trouble", ["store down", "a second over a page"])
def test_every_tool_refuses_or_answers_whole(analysis_env, monkeypatch, trouble):
    env, vl = analysis_env
    _fresh()
    healthy = _every_tool(_mcp(vl.url, env))
    _fresh()
    if trouble == "store down":
        got = _every_tool(_mcp(DOWN, env))
        why = DIDNT
    else:
        monkeypatch.setattr(stores, "VL_PAGE", 60)
        bad = D.VL(_overfull(D.rows_for("full")))
        try:
            got = _every_tool(_mcp(bad.url, env))
        finally:
            bad.close()
        why = PARTIAL
    refused = [n for n, (k, _, *_c) in got.items() if k == "refused" and why in got[n][1]]
    # An answer made without the store never says a source was read whole.
    if trouble == "store down":
        for name, (_k, _v, *cov) in got.items():
            states = {n: c.get("state") for n, c in (cov[0] if cov else {}).items() if isinstance(c, dict)}
            assert "complete" not in states.values(), (name, states)
    # The cache answers the page-shaped reads; the MCP's own stats queries
    # aren't cut by a page, so fewer tools meet the overfull second.
    assert len(refused) >= (15 if trouble == "store down" else 8), refused
    for name, (kind, value, *_cov) in got.items():
        if kind == "refused" and why in value:
            continue
        if (kind, value) != healthy[name][:2]:
            assert kind == "answer" and why in json.dumps(value), \
                f"{name} answered something else with {trouble}, and didn't say so"


@pytest.mark.parametrize("trouble", ["store down", "a second over a page"])
def test_the_page_refuses_rather_than_shows_a_part(tmp_path, monkeypatch, trouble):
    from wowstats import ui

    monkeypatch.setenv("WOW_ITEMS", "off")
    if trouble == "store down":
        url, vl = DOWN, None
    else:
        monkeypatch.setattr(stores, "VL_PAGE", 60)
        vl = D.VL(_overfull(D.rows_for("full")))
        url = vl.url
    try:
        app = ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": url, "MODEL3D": "off",
                      "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp_path), "WOW_ITEMS": "off"})
        for answer in (lambda: app.api_char(D.CHAR), lambda: app.timeline(D.CHAR), lambda: app.dungeons(D.CHAR)):
            with pytest.raises(stores.SourceError) as e:
                answer()
            assert (PARTIAL if vl else DIDNT) in stores.public_error(e.value)
    finally:
        if vl:
            vl.close()


# ---------------------------------------------------------------- unsupported scopes


def test_a_dimension_outside_a_metric_is_refused(demo_layer):
    from wowstats.analysis import semantic

    dims = [d["name"] for d in semantic.catalogue()["dimensions"]]
    pairs = 0
    for m in H.catalogue():
        for d in dims:
            if d in m["group_by"]:
                continue
            pairs += 1
            for q in ({"group_by": [d]}, {"where": [{"dimension": d, "op": "is not null"}]}):
                with pytest.raises(QueryError):
                    H.ask(*demo_layer, metrics=[m["name"]], **q)
    assert pairs > 50
