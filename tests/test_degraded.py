"""Every feature with each data source missing (tests/degraded.py): every
page's API and every MCP tool answers, never raises, and the parts that used
to blank out now give an estimate and the one action that unlocks more
(owner, 2026-11-20: "everything needs to be able to run in a degraded mode").
The plan's own cases are in tests/test_degraded_plan.py."""

from __future__ import annotations

import pytest

from tests import degraded as D
from wowstats import plan as planmod
from wowstats import ui
from wowstats.mcp import tools as T
from wowstats.mcp.store import Store as MStore

# The demo days are fixed: read them on the demo's clock, not the wall clock.
pytestmark = pytest.mark.usefixtures("demo_clock")

# Tools that take no character or need a free-form input of their own.
SKIP_TOOLS = {"propose_finding", "analysis_submit", "check_plan", "kb_get", "kb_lookup", "kb_rule", "kb_topics",
              "kb_search", "route_reference", "play_sql", "query_metric"}


@pytest.fixture(scope="module", params=[(sc, b) for b in D.BACKENDS for sc in D.SCENARIOS],
                ids=lambda p: f"{p[0]}-{p[1]}")
def served(request, tmp_path_factory, monkeypatch_module):
    sc, backend = request.param
    monkeypatch_module.setenv("WOW_ITEMS", "off")
    tmp = tmp_path_factory.mktemp(sc)
    stores, vl = D.store_env(backend, D.rows_for(sc), tmp)
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", **stores,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp),
           "WOW_ITEMS": "off"}
    planmod._CACHE.clear()
    T._MISSING.clear()
    yield sc, env, ui.App(env), ui.App({**env, "VIEW": "public", "HOME": str(tmp / "p")})
    vl.close()
    planmod._CACHE.clear()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_every_page_answers(served):
    sc, env, app, pub = served
    c = D.CHAR
    for name, fn in [("char", lambda: app.api_char(c)), ("timeline", lambda: app.timeline(c)),
                     ("snapshot", lambda: app.snapshot(c, 30)), ("overview", lambda: app.overview(c)),
                     ("journal", lambda: app.api_journal(c)), ("dungeons", lambda: app.dungeons(c)),
                     ("loot", lambda: app.loot(c)), ("people", lambda: app.people(c)), ("plan", lambda: app.plan(c)),
                     ("meta", lambda: app.meta(c)), ("gear", lambda: app.gear(c, None)), ("news", lambda: app.news(c)),
                     ("public char", lambda: pub.api_char(c)), ("public timeline", lambda: pub.timeline(c))]:
        out = fn()
        assert isinstance(out, dict) and out, f"{sc}: {name} answered nothing"


def test_every_mcp_tool_answers(served):
    sc, env, app, pub = served
    ctx = T.Ctx(store=MStore.from_env(env), default_char=D.CHAR, env=env)
    for t in T.TOOLS:
        if t.name in SKIP_TOOLS:
            continue
        args = {"char": D.CHAR} if "char" in t.input_schema["properties"] else {}
        if t.name == "plan_session":
            args["hours"] = 2
        out = T.call(ctx, t.name, args)  # a ToolError fails the test
        assert out, f"{sc}: {t.name} answered nothing"


def test_every_gear_score_credits_foreverdb(served):
    """Owner, 2026-11-24: "Their link every time as we reverse engineered
    the tables". Every MCP answer that shows a gear score, standing, BiS or
    upgrade gain (wowstats.gear.scored) links ForeverDB.net, and the gear
    page's answer carries the link it shows (tests/test_credit_surfaces.py
    checks the page's code)."""
    from wowstats import gear as G

    sc, env, app, pub = served
    ctx = T.Ctx(store=MStore.from_env(env), default_char=D.CHAR, env=env)
    seen = []
    for t in T.TOOLS:
        if t.name in SKIP_TOOLS:
            continue
        args = {"char": D.CHAR} if "char" in t.input_schema["properties"] else {}
        if t.name == "plan_session":
            args["hours"] = 2
        out = T.call(ctx, t.name, args)
        if G.scored({k: v for k, v in out.items() if k != "score_source"}):
            seen.append(t.name)
            assert out["score_source"]["url"] == "https://foreverdb.net", f"{sc}: {t.name}"
    if sc == "full":
        assert {"gear", "next_moves", "plan_session"} <= set(seen), seen
    g = app.gear(D.CHAR, None)
    if g.get("available"):
        assert g["source"]["url"] == "https://foreverdb.net"


def test_gear_without_syndicator(served):
    """No Syndicator sheet: the best items for the level, said to be an
    estimate, with the one action that turns it into upgrades."""
    sc, env, app, pub = served
    if sc not in ("no-syndicator", "polestar-only"):
        pytest.skip("Syndicator's sheet is there")
    g = app.gear(D.CHAR, None)
    assert g["available"] and g["estimated"] and g["worn_from"] is None and "Syndicator" in g["unlock"]
    assert any(sl.get("best") for sl in g["slots"])
    ctx = T.Ctx(store=MStore.from_env(env), default_char=D.CHAR, env=env)
    m = T.call(ctx, "gear", {"char": D.CHAR})
    assert m["estimated"] and m["next_upgrades"] and m["unlock"] == g["unlock"]


def test_without_the_recorder(served):
    """The journal per logout, the level's quests and kills from Polestar's
    split, the summary's level and counts from the splits."""
    sc, env, app, pub = served
    if sc not in ("recorder-off", "no-recorder", "polestar-only"):
        pytest.skip("the Recorder's events are there, or no sheet to build on")
    j = app.api_journal(D.CHAR)
    assert j["sessions"] and all(s["estimated"] and s["played_s"] >= 0 for s in j["sessions"])
    assert any(s["levels_gained"] for s in j["sessions"])
    assert app.snapshot(D.CHAR, 30)["counts"]
    ctx = T.Ctx(store=MStore.from_env(env), default_char=D.CHAR, env=env)
    s = T.call(ctx, "sessions", {"char": D.CHAR})
    assert s["estimated"] and s["sessions"] and "/psrec on" in s["unlock"]


def test_level_splits_alone(served):
    sc, env, app, pub = served
    if sc != "only-level-splits":
        pytest.skip("more than the splits")
    ctx = T.Ctx(store=MStore.from_env(env), default_char=D.CHAR, env=env)
    cs = T.call(ctx, "character_summary", {"char": D.CHAR})
    done = cs["counts"]["completed_levels"]
    assert cs["level"] and "level" in cs["from_level_splits"] and done["kills"] and done["quests"]
    for tool in ("class_guide", "class_meta", "gear"):
        out = T.call(ctx, tool, {"char": D.CHAR})
        assert out["answer"] is None and out["why"] and out["unlock"]
    assert "class isn't known" in app.meta(D.CHAR)["why"]


# ---------------------------------------------------------------- switched on later
#
# Owner, 2026-11-20: a player runs degraded for a while, then switches the
# Recorder on (or installs Syndicator, or links Battle.net). Each period
# uses the best data it has: the history before stays, the hints go once the
# data arrives, the plan becomes the step-by-step one.


def _ctx(env):
    return T.Ctx(store=MStore.from_env(env), default_char=D.CHAR, env=env)


@pytest.fixture(scope="module")
def switch(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("WOW_ITEMS", "off")
    out = {}
    for sc in ("recorder-later-before", "recorder-later", "syndicator-later-before", "syndicator-later"):
        vl = D.VL(D.rows_for(sc))
        tmp = tmp_path_factory.mktemp(sc)
        env = {"VIEW": "full", "AUTH": "none", "VL_URL": vl.url, "VM_URL": vl.url, "MODEL3D": "off",
               "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp), "WOW_ITEMS": "off"}
        out[sc] = (vl, env, ui.App(env))
    yield out
    for vl, _, _ in out.values():
        vl.close()
    planmod._CACHE.clear()


def _missing(app) -> str:
    return " ".join(str(m) for m in (app.api_char(D.CHAR).get("missing") or []))


def test_recorder_switched_on_later_journal(switch):
    """Before: logouts only. After: the Recorder's sessions from the switch,
    the logouts before it still there, nothing counted twice."""
    _, _, before = switch["recorder-later-before"]
    _, _, after = switch["recorder-later"]
    b = before.api_journal(D.CHAR)["sessions"]
    a = after.api_journal(D.CHAR)["sessions"]
    assert b and all(s["estimated"] for s in b)
    rec = [s for s in a if not s.get("estimated")]
    early = [s for s in a if s.get("estimated")]
    assert rec and early, "both periods show"
    first = min(s["start"] for s in rec)
    assert first >= D.SWITCH[:10] and all(s["end"] <= first for s in early), "no overlap"
    assert a == sorted(a, key=lambda s: s["start"], reverse=True), "newest first, one order"
    # The logouts before the switch are the same ones the player saw before it.
    assert [s["end"] for s in early] == [s["end"] for s in b if s["end"] <= first]


def test_recorder_switched_on_later_timeline_and_sessions(switch):
    _, env_b, before = switch["recorder-later-before"]
    _, env_a, after = switch["recorder-later"]
    tb, ta = before.timeline(D.CHAR)["events"], after.timeline(D.CHAR)["events"]
    lv_b = [e["t"] for e in tb if e["type"] == "level"]
    lv_a = [e["t"] for e in ta if e["type"] == "level"]
    assert lv_b and set(lv_b) <= set(lv_a), "the levels before the switch stay"
    marks = [e for e in ta if e.get("marker") == "tracking"]
    assert len(marks) == 1 and not any(e.get("marker") for e in tb)
    s = T.call(_ctx(env_a), "sessions", {"char": D.CHAR, "limit": 50})
    est = [x for x in s["sessions"] if x.get("estimated")]
    assert est and s["tracking_from"] and all(x["logout"] <= s["tracking_from"] for x in est)


def test_recorder_switched_on_later_hints_and_plan(switch):
    """The Recorder hint goes once its data arrived; the plan becomes the
    step-by-step one; the pace blends the splits before and the Recorder
    after, one time per level."""
    _, env_b, before = switch["recorder-later-before"]
    _, env_a, after = switch["recorder-later"]
    assert "psrec" in _missing(before) and "psrec" not in _missing(after)
    planmod._CACHE.clear()
    pb = planmod.plan(env_b, D.CHAR, 2)
    planmod._CACHE.clear()
    pa = planmod.plan(env_a, D.CHAR, 2)
    eb, ea = pb["estimate"], pa["estimate"]
    assert eb["supported"] and eb["estimated"] and "/psrec on" in pb["markdown"]
    assert ea["supported"] and not ea["estimated"] and ea["mode"] == "route"
    assert "/psrec" not in pa["markdown"] and not ea.get("unlock")
    lf = ea["pace"]["level_factor"]
    assert lf["n"] == len(set(lf["levels"])) and lf["factor"]


def test_recorder_on_without_a_step_yet_says_what_is_next():
    """Data arrived but no guide step: never the stale "switch it on"."""
    from wowstats.plan import estimate

    assert estimate.unlock({"recorder": "on"}) == estimate.UNLOCK_STEP and "psrec on" not in estimate.UNLOCK_STEP
    assert estimate.unlock({"recorder": "waiting"}) == estimate.UNLOCK_WAIT
    assert estimate.unlock({"recorder": "off"}) == estimate.UNLOCK


def test_summary_counts_span_the_switch(switch):
    _, env_a, _ = switch["recorder-later"]
    cs = T.call(_ctx(env_a), "character_summary", {"char": D.CHAR})
    splits = [r for r in D.rows_for("only-level-splits")]
    # Polestar's per-level counts, the whole history (the Recorder's own
    # would start at the switch).
    assert cs["counts"]["completed_levels"]["kills"] >= sum(int(float(r.get("kills") or 0)) for r in splits
                                                            if r["_time"] < D.SWITCH)


def test_syndicator_installed_later(switch):
    _, env_b, before = switch["syndicator-later-before"]
    _, env_a, after = switch["syndicator-later"]
    gb, ga = before.gear(D.CHAR, None), after.gear(D.CHAR, None)
    assert gb["available"] and gb["estimated"] and gb["unlock"]
    assert ga["available"] and not ga.get("estimated") and not ga.get("unlock")
    # (The demo profile ships no wow-bags, so its "bags" line stays in both.)
    assert "Syndicator" in gb["unlock"]


def test_battlenet_linked_later(served, monkeypatch):
    """No Syndicator sheet: before Battle.net, the best for the level
    (estimated); once linked, upgrades over what it says is worn."""
    from wowstats import gear as gearmod

    sc, env, app, pub = served
    if sc != "no-syndicator":
        pytest.skip("one scenario is enough")
    g0 = app.gear(D.CHAR, None)
    assert g0["estimated"] and g0["worn_from"] is None
    worn = {"head": {"item_id": 10504, "stats": {}}, "chest": {"item_id": 14152, "stats": {}}}
    monkeypatch.setattr(gearmod, "armory_worn", lambda store, char: gearmod.worn_stats(worn))
    g1 = app.views.gear(D.CHAR, None)  # the page's answer is cached a while; the view underneath
    assert g1["worn_from"] == "battle.net" and not g1["estimated"]
    m = T.call(_ctx(env), "gear", {"char": D.CHAR})
    assert m["worn_from"] == "battle.net" and not m["estimated"]
