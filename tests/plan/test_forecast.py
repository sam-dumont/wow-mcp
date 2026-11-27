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
    options = [decision, *decision["alternatives"]]
    assert len(options) > 1 and len({o["dungeon"] for o in options}) == len(options)
    assert all(0 < o["time_s"] <= est["play_s"] for o in options)
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
    assert result["answer"] is None and "checkpoint" in result["why"]
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
    for bad in ({"exit_t": None}, {"end_t": None}, {"xp_inside": None}, {"inside_s": 0}, {"start_t": made}):
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
    assert forecast.report([p], [at(5100)], progress)["counts"]["ambiguous"] == 1
    assert not forecast.report([p], [at(7200)], [progress[-1]])["reviews"][0]["observations"]


def test_unknown_handin_excludes_the_personal_xp_subtotal(priest):
    from wowstats.plan import dungeongain

    trip = next(t for t in priest["trips"] if "Stockade" in t["dungeon"])
    quests = [{"quest_id": i, "name": str(i), "state": "open", "xp_now": xp} for i, xp in ((1, 400), (2, None))]
    g = dungeongain.estimate(level=trip["level_start"], xp_max=30000, name=trip["dungeon"], runs=[trip],
                             others=[], quests=quests, questing_xph=1000)
    assert g["xp"] == trip["xp_inside"] + 400 and g["unknown_handin_xp"] == 1 and not g["kills_unknown"]
    f = forecast.decision(priest, {"name": trip["dungeon"], "goals": ["xp"], "gain": g}, "xp", "group")
    assert f["xp"] is None and f["known_xp"] == g["xp"] and f["unknown_handin_xp"] == 1
    made = priest["now_t"]
    report = forecast.report([{"made_t": made, "hours": 3, "dungeon_forecast": f}],
                             [future_trip(trip, made + 1000)], [])
    assert report["counts"]["incomplete"] == 1 and report["by_basis"]["personal_runs"]["n"] == 0
    assert report["reviews"][0]["missing"] == ["forecast XP"]


def test_shorter_retained_capture_cannot_replace_an_archived_pair(tmp_path, priest):
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    char = priest["who"]["char"]
    est = plan.est_mod.estimate(priest, 3)
    store.save(env, char, est)
    stock = next(t for t in priest["trips"] if "Stockade" in t["dungeon"])
    trip = future_trip(stock, est["made_t"] + 800, est["dungeon_forecast"]["dungeon"])
    assert trip["handin_ids"] and trip["quests_handed_in"] > 0 and trip["xp_quests"] > 0 and trip["run_xp"] > 0

    def archive(t):
        return store.dungeon_review(env, {"who": {"char": char}, "trips": [t]}, archive=True)

    first = archive(trip)
    assert first["counts"]["matched"] == 1
    saved = store.path(env).read_bytes()
    # All of these still have numeric XP and positive online time. Even
    # a longer bracket can hide the loss of online time in the old overlap.
    for loss in (
        {"start_t": trip["start_t"] + 30, "pickup_s": trip["pickup_s"] - 30, "online_s": trip["online_s"] - 30},
        {"end_t": trip["end_t"] - 30, "handin_s": trip["handin_s"] - 30},
        {"online_s": trip["online_s"] - 1},
        {"quests_handed_in": trip["quests_handed_in"] - 1},
        {"handin_ids": [*trip["handin_ids"][1:], 99999]},
        {"xp_quests": trip["xp_quests"] - 1},
        {"end_t": trip["end_t"] + 120, "online_s": trip["online_s"] + 60},
    ):
        assert archive({**trip, **loss}) == first
        assert store.path(env).read_bytes() == saved

    # A new hand-in after the previous end, with the full earlier interval
    # still covered. Its extra 120 seconds and 400 XP count on the pair.
    extended = {**trip, "end_t": trip["end_t"] + 120, "online_s": trip["online_s"] + 120,
                "handin_s": trip["handin_s"] + 120, "xp_quests": trip["xp_quests"] + 400,
                "quests_handed_in": trip["quests_handed_in"] + 1, "quests_outside": trip["quests_outside"] + 1,
                "handin_ids": [*trip["handin_ids"], 99999]}
    row, = archive(extended)["reviews"]
    assert row["error_s"] == pytest.approx(first["reviews"][0]["error_s"] + 120)
    assert row["error_xp"] == first["reviews"][0]["error_xp"] + 400
    # A lower bar gain explicitly reported by Polestar can correct the XP
    # when the Recorder still accounts for the whole trip and its hand-ins.
    corrected = {**extended, "xp_inside": extended["xp_inside"] - 5, "run_xp": extended["run_xp"] - 5}
    assert archive(corrected)["reviews"][0]["error_xp"] == row["error_xp"] - 5


def session_pair(made, *, goal="xp", level=38, rival_basis="kill_model"):
    def option(name, xp, basis):
        return {"dungeon": name, "time_s": 1000, "xp": xp, "basis": basis, "priority": "high",
                "legs": {k: {"label": "route"} for k in forecast.LEGS}}

    predicted = {**option("Library", 800, "kill_model"), "state": {"level": level}, "objective": goal,
                 "kind": "group", "personal_runs": 0, "alternatives": [option("Armory", 500, rival_basis)]}
    return {"made_t": made, "hours": 2, "dungeon_forecast": predicted}


def played(name, entry, xp, seconds=1000, level=38):
    return {"dungeon": name, "entry_t": entry, "level_start": level, "start_t": entry - 50,
            "end_t": entry + seconds - 50, "exit_t": entry + seconds - 100,
            "online_s": seconds, "xp_inside": xp, "xp_quests": 0, "quests_outside": 0,
            "inside_s": seconds - 100, "pickup_s": 25, "travel_to_s": 25,
            "travel_back_s": 25, "handin_s": 25}


def test_xp_order_uses_two_subsequent_whole_trips_and_keeps_the_alternative(tmp_path):
    import json

    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    p = session_pair(100)
    store.path(env).write_text(json.dumps({"schema": 1, "plans": {"A": [p]}}))
    # Library's 800 and Armory's 500 XP were each budgeted for 1000 s.
    # Library actually pays 600 in 1000 s. Armory pays 400 in 500 s and wins.
    inp = {"who": {"char": "A"}, "trips": [played("Library", 200, 600), played("Armory", 1500, 400, 500)]}
    r = store.dungeon_review(env, inp, archive=True)
    assert r["by_basis"]["modeled"] == {"n": 2, "mean_absolute_error_s": 250.0, "mean_absolute_error_xp": 150.0}
    assert r["xp_order"]["modeled"] == {"held": 0, "reversed": 1, "tied": 0, "changed_forecast": 0}
    assert r["counts"]["matched"] == 1  # one first pick, two independently measured runs
    assert store.dungeon_review(env, {**inp, "trips": []}) == r
    assert store.dungeon_review(env, {**inp, "who": {"char": "B"}})["timing"]["modeled"]["n"] == 0


def test_rank_comparisons_across_plans_need_common_options_and_goal():
    first, second = session_pair(100), session_pair(1600, rival_basis="runs")
    trips = [played("Library", 300, 600), played("Armory", 2000, 600)]

    def order():
        return forecast.report([first, second], trips, [])["xp_order"]

    assert order()["modeled"]["tied"] == 1
    assert sum(order()["mixed"].values()) == 0  # the first forecast used two modeled options
    trips[1]["xp_inside"] = 400
    assert order()["modeled"]["held"] == 1
    second["dungeon_forecast"]["alternatives"][0]["xp"] = 900
    assert order()["modeled"] == {"held": 1, "reversed": 0, "tied": 0, "changed_forecast": 1}
    trips[0]["xp_inside"] = 200
    assert order()["modeled"] == {"held": 0, "reversed": 1, "tied": 0, "changed_forecast": 1}
    for change in ({"kind": "branch"}, {"objective": "gear"}, {"state": {"level": 39}}, {"alternatives": []}):
        p = {**second, "dungeon_forecast": {**second["dungeon_forecast"], **change}}
        out = forecast.report([first, p], trips, [])
        assert not any(sum(o.values()) for o in out["xp_order"].values())
    second["dungeon_forecast"]["alternatives"][0]["xp"] = None
    assert order()["modeled"]["reversed"] == 1  # the original forecast and both actual XP readings still exist
    assert order()["modeled"]["changed_forecast"] == 0
    for field, value in (("xp", None), ("priority", "some")):
        first["dungeon_forecast"]["alternatives"][0][field] = value
        assert not any(sum(o.values()) for o in order().values())
        first = session_pair(100)
    assert order()["modeled"]["reversed"] == 1

    def demote(x):  # the later plan rates both options lower: still alike, but not the forecast made
        if isinstance(x, dict):
            x.update({"priority": "some"} if "priority" in x else {})
            for v in x.values():
                demote(v)
        elif isinstance(x, list):
            for v in x:
                demote(v)
    demote(second["dungeon_forecast"])
    assert not any(sum(o.values()) for o in order().values())


def test_an_unplayed_option_has_no_rank_and_partial_xp_still_has_timing():
    p = session_pair(100)
    p["dungeon_forecast"]["xp"] = None
    r = forecast.report([p], [played("Armory", 50, 850), played("Library", 200, 600, 800)], [])
    assert r["by_basis"]["modeled"]["n"] == 0
    assert r["timing"]["modeled"] == {"n": 1, "mean_absolute_error_s": 200}
    assert not any(sum(o.values()) for o in r["xp_order"].values())
    assert r["reviews"][0]["missing"] == ["forecast XP"]


def test_overlapping_whole_trip_windows_cannot_establish_an_xp_order():
    p = session_pair(100)
    trips = [played("Library", 300, 800), played("Armory", 1350, 500)]
    trips[1]["start_t"] = trips[0]["end_t"] - 150  # picked up a quest during the previous return leg
    r = forecast.report([p], trips, [])
    assert r["by_basis"]["modeled"]["n"] == 2
    assert not any(sum(o.values()) for o in r["xp_order"].values())
    trips[1]["start_t"] = trips[0]["end_t"]
    assert forecast.report([p], trips, [])["xp_order"]["modeled"]["held"] == 1
