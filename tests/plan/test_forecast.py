"""A decision saved before entry against the Recorder and Polestar trip evidence."""

from __future__ import annotations

import pytest

from wowstats import plan
from wowstats.mcp import plantools, tools
from wowstats.plan import forecast, store


@pytest.fixture(scope="module")
def priest(derived):
    return derived("plan-inputs.json.gz")


def future_trip(trip, entry, dungeon=None):
    shift = entry - trip["entry_t"]
    return {**trip, **{k: trip[k] + shift for k in ("start_t", "entry_t", "exit_t", "end_t")},
            "dungeon": dungeon or trip["dungeon"]}


def test_saved_decision_and_private_review(tmp_path, monkeypatch, priest):
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    char = priest["who"]["char"]
    inp = {**priest}
    monkeypatch.setattr(plan, "inputs", lambda *a, **kw: inp)
    # The cache's clock is old. A player can ask for a fresh plan before its inputs expire.
    made = priest["now_t"] + 90
    monkeypatch.setattr(plan.time, "time", lambda: made)
    est = plan.plan(env, char, 3, "group for gear")["estimate"]
    decision = est["dungeon_forecast"]
    assert est["made_t"] == made and decision == store.latest(env, char)["dungeon_forecast"]
    assert decision["dungeon"] == est["blocks"][0]["dungeon"]
    assert decision["kind"] == "group" and decision["objective"] == "gear" and decision["state"]["xp"] == inp["xp"]
    assert decision["basis"] == "kill_model" and decision["time_s"] == est["blocks"][0]["dur_s"]
    assert decision["xp"] == est["blocks"][0]["xp"] and decision["quests"]

    # Shift a whole captured Stockade trip to the new decision and a
    # second copy to just before it, as if its upload had arrived late.
    stockade = next(t for t in priest["trips"] if "Stockade" in t["dungeon"])
    earlier = future_trip(stockade, made - 1, decision["dungeon"])
    actual = future_trip(stockade, made + 800, decision["dungeon"])
    inp.update(trips=[earlier, actual], progress=[], plan_reviews=[])
    monkeypatch.setattr(plantools, "_inputs", lambda *a: inp)
    before = store.path(env).read_bytes()
    ctx = tools.Ctx(store=None, env=env)
    result = plantools.t_how_did_my_plan_go(ctx, {"char": char})
    report = plan.status(env, char)["dungeon_forecasts"]
    assert report == result["dungeon_forecasts"] and result["latest"] is None
    assert store.path(env).read_bytes() == before  # GET, MCP and option browsing are read-only
    row, = report["reviews"]
    obs, = row["observations"]
    assert row["status"] == "matched" and obs["entry_t"] == actual["entry_t"] and obs["exit_t"] == actual["exit_t"]
    assert obs["xp"] == actual["xp_inside"] + actual["xp_quests"] and obs["time_s"] == actual["online_s"]
    err = report["by_basis"]["modeled"]
    assert err == {"n": 1, "mean_absolute_error_s": round(abs(actual["online_s"] - decision["time_s"]), 1),
                   "mean_absolute_error_xp": abs(obs["xp"] - decision["xp"])}
    assert report["by_basis"]["personal_runs"]["n"] == 0
    assert plan.status(env, char, archive=True)["dungeon_forecasts"] == report
    archived, = store.latest(env, char)["dungeon_observations"]
    assert all(archived[k] == v for k, v in obs.items())
    inp["trips"] = [{**actual, "handin_s": None, "xp_inside": None}]
    assert plan.status(env, char, archive=True)["dungeon_forecasts"] == report
    # Durable after the event store's history rolls off, and private to this character.
    inp["trips"] = []
    assert plan.status(env, char)["dungeon_forecasts"] == report
    assert not store.dungeon_review(env, {**inp, "who": {"char": "Another"}})["reviews"]
    assert not store.dungeon_review({}, inp)["reviews"]


def test_optional_branch_and_solo(priest):
    est = plan.est_mod.estimate(priest, 3)
    assert est["dungeon_forecast"]["dungeon"] == est["branch"]["dungeon"]
    assert est["dungeon_forecast"]["kind"] == "branch"
    assert "dungeon_forecast" not in plan.est_mod.estimate(priest, 3, "no dungeons")


def test_modeled_and_personal_errors_use_only_comparable_trips(priest):
    stock = next(t for t in priest["trips"] if "Stockade" in t["dungeon"])
    blocks = plan.est_mod.estimate(priest, 3)["dungeons"]
    dungeon = next(d for d in blocks if "Stockade" in d["name"])
    f = forecast.decision(priest, dungeon, "xp", "group")
    assert f["basis"] == "runs" and f["personal_runs"] >= 1
    made = priest["now_t"]
    def planned(offset, **changes):
        return {"made_t": made + offset, "hours": 3, "dungeon_forecast": {**f, **changes}}
    # A replacement cuts even a long forecast off at its new decision.
    plans = [planned(0), planned(10), planned(10_000, basis="kill_model"), planned(20_000),
             planned(30_000), planned(40_000, dungeon="Scarlet Monastery: Library"), planned(50_000)]
    trips = [future_trip(stock, made + t) for t in (1000, 11_000, 21_000, 31_000, 34_000)]
    trips[2]["handin_s"] = None
    trips[2]["quests_outside"] = 1
    trips.append(future_trip(stock, made + 41_000, "Scarlet Monastery") | {"kill_npcs": {}})
    report = forecast.report(plans, trips, [])
    assert [r["status"] for r in report["reviews"]] == [
        "abandoned", "matched", "matched", "incomplete", "ambiguous", "ambiguous", "pending"]
    assert report["counts"] == {"abandoned": 1, "matched": 2, "incomplete": 1, "ambiguous": 2, "pending": 1}
    assert report["by_basis"]["modeled"]["n"] == report["by_basis"]["personal_runs"]["n"] == 1
    assert "handin_s" in report["reviews"][3]["missing"]
    # Unknown XP is distinct from zero. Open instances cannot count as whole trips.
    one = [plans[1]]
    zero = {**trips[0], "xp_inside": 0, "xp_quests": 0}
    assert forecast.report(one, [zero], [])["reviews"][0]["status"] == "matched"
    for bad in ({"exit_t": None}, {"xp_inside": None}, {"inside_s": 0}, {"start_t": made}):
        r = forecast.report(one, [{**trips[0], **bad}], [])
        assert r["counts"]["incomplete"] == 1 and r["by_basis"]["personal_runs"]["n"] == 0
    # Schema-1 plans from before forecasts have no pair to reconstruct.
    assert not forecast.report([{"made_t": made}], trips, [])["reviews"]
    progress = [{"t": made + 50_001, "online_s": 10}, {"t": made + 62_000, "online_s": 12_000}]
    assert forecast.report(plans[-1:], [], progress)["counts"]["abandoned"] == 1


def test_forecast_window_and_mcp_report_size(tmp_path, monkeypatch, priest):
    import json

    # Thirty retained plans, each with three possible trips. Report all 30
    # in the totals without sending ninety trips or thirty quest logs.
    char = priest["who"]["char"]
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    est = plan.est_mod.estimate(priest, 3)
    f = est["dungeon_forecast"]
    made = est["made_t"]
    stock = next(t for t in priest["trips"] if "Stockade" in t["dungeon"])
    entries = [made + 4_000 * i for i in range(store.MAX_PLANS)]
    plans = [{"made_t": t, "hours": 1, "dungeon_forecast": f} for t in entries]
    store.path(env).write_text(json.dumps({"schema": 1, "plans": {char: plans}}))
    trips = [future_trip(stock, t + dt, f["dungeon"]) for t in entries for dt in (800, 1200, 1600)]
    inp = {"who": {"char": char}, "trips": trips, "progress": []}
    monkeypatch.setattr(plantools, "_inputs", lambda *a: inp)
    ctx = tools.Ctx(store=None, env=env)
    out = tools.call(ctx, "how_did_my_plan_go", {"char": char})
    report = out["dungeon_forecasts"]
    assert report["decisions"] == report["counts"]["ambiguous"] == store.MAX_PLANS
    assert len(report["reviews"]) == 3 and all(len(r["observations"]) == 2 for r in report["reviews"])
    assert len(json.dumps(report)) < 4000

    # Strict pre-entry and exclusive replacement and budget endpoints.
    p = plans[0]
    def at(seconds):
        return future_trip(stock, made + seconds, f["dungeon"])
    for seconds in (0, 3600, 7200):
        assert not forecast.report([p], [at(seconds)], [])["reviews"][0]["observations"]
    assert not forecast.report([p, {"made_t": made + 1200}], [at(1200)], [])["reviews"][0]["observations"]
    # Online time can prove that there was still budget after a logout.
    progress = [{"t": made, "online_s": 1000}, {"t": made + 7200, "online_s": 1800}]
    assert forecast.report([p], [at(7200)], progress)["counts"]["matched"] == 1
    # Two sparse readings bracketing the point where time ran out cannot
    # prove the trip was inside the plan.
    progress[-1]["online_s"] = 5200
    assert not forecast.report([p], [at(5100)], progress)["reviews"][0]["observations"]
