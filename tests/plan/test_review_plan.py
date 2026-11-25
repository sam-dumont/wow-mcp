"""Planner answers an outside review (2026-11-19) found wrong, reproduced
first: mount_goal and dungeon_options failed when a gold rate was measured
but the time to a riding level wasn't known, and check_plan passed a
write-up that broke the player's own constraints."""

from __future__ import annotations

import copy

import pytest

from wowstats.mcp import plantools, tools
from wowstats.plan import estimate as E
from wowstats.plan import store, write


@pytest.fixture(scope="module")
def priest(derived):
    return derived("plan-inputs.json.gz")


@pytest.fixture(scope="module")
def hunter(derived):
    return derived("plan-inputs-level1.json.gz")


def short_route(inp, last):
    """The route's clock stops before the riding levels: the time to them is
    unknown (a Polestar pack that ends early, a class it doesn't time)."""
    out = copy.deepcopy(inp)
    out["route_levels"] = {k: v for k, v in out["route_levels"].items() if int(k) < last}
    return out


@pytest.mark.parametrize("who", ["priest", "hunter"])
def test_mount_goal_with_a_gold_rate_and_no_time_to_level(request, who):
    inp = short_route(request.getfixturevalue(who), 35)
    if inp["gold_rate"].get("copper_per_hour") is None:  # the hunter: give him a measured rate
        inp["gold_rate"] = {"n": 3, "copper_per_hour": 2500, "net_copper": 5000, "online_s": 7200}
    p = E.pace(inp)
    m = E.mounts(inp, p)
    for t in m["tiers"]:
        assert t["played_to_level_s"] is None and t["gold_at_level_copper"] is None
        assert not t["behind"]  # unknown, never guessed
    # Known numbers still come: the cost, what's left, the ETA in played hours.
    cur = m["current"]
    assert cur["need_copper"] > 0 and cur["eta_played_s"] is not None
    assert any("level" in u for u in m["unknown"]), m["unknown"]
    dl = E.dungeons(inp, p, m)
    assert isinstance(dl, list)
    E.gold_options(inp, m, dl)


def test_mount_and_dungeon_tools_answer(monkeypatch, tmp_path, priest):
    inp = short_route(priest, 35)
    monkeypatch.setattr(plantools, "_inputs", lambda ctx, args: inp)
    ctx = tools.Ctx(store=None, env={"ANALYSIS_STATE_DIR": str(tmp_path)})
    mg = plantools.t_mount_goal(ctx, {"char": "Testpriest-Fixture"})
    assert mg.get("answer", "x") is not None, mg
    assert mg["tiers"][0]["gold_at_level"] is None and mg["unknown"]
    do = plantools.t_dungeon_options(ctx, {"char": "Testpriest-Fixture"})
    assert do.get("answer", "x") is not None and do["dungeons"], do


# ---------------------------------------------------------------- check_plan


def stored_plan(tmp_path, inp, hours, constraints):
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    e = E.estimate(inp, hours, constraints)
    assert e["supported"]
    store.save(env, "Testpriest-Fixture", e)
    return env, e


def check(env, text):
    ctx = tools.Ctx(store=None, env=env)
    return plantools.t_check_plan(ctx, {"char": "Testpriest-Fixture", "text": text})


def test_check_plan_refuses_a_dungeon_after_no_dungeons(tmp_path, priest):
    env, _ = stored_plan(tmp_path, priest, 3, "no dungeons")
    out = check(env, "Spend the entire session in a dungeon.")
    assert out["ok"] is False
    assert any("no dungeons" in p for p in out["problems"]), out
    # A dungeon the facts name, without the word "dungeon":
    out = check(env, "Head to City of Dalaran and run it.")
    assert out["ok"] is False and any("no dungeons" in p for p in out["problems"]), out
    # Saying no to dungeons is fine.
    assert check(env, "No dungeons this time, as you asked.")["ok"] is True


def test_check_plan_refuses_more_time_than_asked(tmp_path, priest):
    env, e = stored_plan(tmp_path, priest, 3, "only 2h")
    assert e["hours"] == 2
    # (5 hours: no fact of the plan says it; a dungeon trip's 3h00 does.)
    out = check(env, "Play for 5 hours in the Wetlands.")
    assert out["ok"] is False
    assert any("time budget" in p and "2h00" in p for p in out["problems"]), out
    out = check(env, "Play for 4h tonight.")
    assert out["ok"] is False and any("time budget" in p for p in out["problems"]), out
    # The plan's own numbers pass, a fact longer than the session (the mount's ETA) too.
    f = e["facts"]
    ok = check(env, f"Your next {f['hours']}: {f['b1.title']} until {f['b1.end']}. Mount in {f['mount1.eta']}.")
    assert ok["ok"] is True, ok


def test_check_plan_without_constraints_still_checks_numbers(tmp_path, priest):
    env, e = stored_plan(tmp_path, priest, 3, "")
    assert check(env, "If you get a group, City of Dalaran.")["ok"] is True
    # Past any level and any share of BiS: the item lines' "45% → 99%" made 99 a fact.
    assert check(env, "Level 101 by the end")["ok"] is False


def test_write_check_names_the_constraint():
    facts = {"hours": "1h00", "d1.name": "The Stockade"}
    out = write.check("Run The Stockade twice.", facts, {"text": "solo please", "no_dungeons": True}, 1)
    assert out["ok"] is False and out["constraints"][0]["constraint"] == "no dungeons"
    assert "solo please" in out["problems"][0]


@pytest.mark.parametrize("who", ["priest", "hunter"])
@pytest.mark.parametrize("asked", ["no dungeons", "only 1h", "no professions, only 90 min", "",
                                   "solo, 2h, I want to level tailoring"])
def test_the_estimators_own_text_keeps_the_constraints(request, who, asked):
    """The check found the estimator itself offering a dungeon loop for gold
    after "no dungeons" and a profession trainer after "no professions"."""
    e = E.estimate(request.getfixturevalue(who), 3, asked)
    out = write.check(write.fallback(e), e["facts"], E.parse_constraints(asked), e["hours"])
    assert out["ok"], out["problems"]
    if "no dungeons" in asked:
        assert not any(g["what"].startswith("loop ") for g in e["gold_options"])
