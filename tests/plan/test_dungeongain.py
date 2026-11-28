"""One more run of a dungeon (wowstats.plan.dungeongain): XP at the level now,
time, pace against questing, the quests to take along; and the three places
that say it (the Overview's move, the Dungeons panel, the MCP's
dungeon_options) agreeing, on Testpriest's synthetic planner inputs."""

from __future__ import annotations

import copy

import pytest

from wowstats.analysis import kb
from wowstats.mcp import plantools, tools
from wowstats.plan import dungeongain as G
from wowstats.plan import dungeonview as V
from wowstats.plan import dungeonxp as D
from wowstats.plan import estimate as E

RULE = G.kill_rule()


@pytest.fixture(scope="module")
def priest(derived):
    return derived("plan-inputs.json.gz")


def quest(qid, xp, state="new", min_level=20, blocked=False):
    return {"quest_id": qid, "name": f"Quest {qid}", "url": f"https://www.wowhead.com/forever/quest={qid}",
            "xp_now": xp, "state": state, "level": min_level + 2, "min_level": min_level, "blocked": blocked,
            "starts": [{"kind": "npc", "id": 1, "name": "Giver", "zone": "Wetlands", "x": 10.0, "y": 20.0}]}


def trip(level, xp_inside, kills=None, **kw):
    t = {"dungeon": "The Deadmines", "level_start": level, "xp_inside": xp_inside, "travel_to_s": 600,
         "inside_s": 2400, "travel_back_s": 300, "pickup_s": 900, "handin_s": 600}
    if kills is not None:
        t["kill_levels"] = kills
    return {**t, **kw}


def est(**kw):
    base = {"level": 20, "xp_max": 30000, "name": "The Deadmines", "runs": [], "others": [], "quests": [],
            "questing_xph": 12000, "rule": RULE}
    return G.estimate(**{**base, **kw})


def test_the_kill_formula_is_a_sourced_rule():
    r = kb.static_rule("xp.kill_formula")
    assert r and r["scope"] == "classic_fallback" and r["source_url"].startswith("https://warcraft.wiki.gg/")
    # The wiki's worked examples (character level 20, grey at 13).
    assert G.grey(20, RULE) == 13
    assert G.kill_xp(20, 20, False, RULE) == 145
    assert round(G.kill_xp(20, 18, False, RULE), 1) == round(145 * (1 - 2 / 11), 1)
    assert G.kill_xp(20, 13, False, RULE) == 0
    assert G.kill_xp(20, 30, True, RULE) == 145 * 1.2 * 2  # capped at four levels above, elite twice
    assert G.grey(45, RULE) == 35 and G.grey(60, RULE) == 51


def test_with_own_runs_at_the_same_level():
    g = est(runs=[trip(20, 6000)], quests=[quest(1, 4000), quest(2, 2000, state="done")])
    assert g["basis"] == "runs" and g["own_runs"] == 1 and not g["kills_unknown"]
    # Travel there and back, inside, and the pickups and hand-ins: a quest is left.
    assert g["run_s"] == 600 + 2400 + 300 + 900 + 600 + G.LFG_WAIT_S  # and the wait for a group
    assert g["xp_kills"] == 6000 and g["xp_quests"] == 4000 and g["xp"] == 10000
    assert g["level_pct"] == 33
    assert g["xp_per_hour"] == round(10000 / (4800 + G.LFG_WAIT_S) * 3600)
    assert g["vs_questing"] == pytest.approx(6000 / 12000, abs=0.001)
    assert g["summary"] == "~1h40 trip for ~10k XP, 0.5× your questing (15 min pickups · 40 min inside · 10 min hand-ins · 20 min to find a group (assumed))"
    assert g["lead"] == "~1h40 trip"
    assert [q["quest_id"] for q in g["quests"]] == [1]
    # His own runs: every part measured.
    assert {k: g["trip"][k]["label"] for k in ("pickup_s", "inside_s", "handin_s", "travel_s", "xp_kills")} == \
        dict.fromkeys(("pickup_s", "inside_s", "handin_s", "travel_s", "xp_kills"), "measured")
    assert g["trip"]["total_s"]["value"] == g["run_s"] and g["trip"]["xp_total"]["value"] == 10000


def test_runs_at_another_level_are_carried_by_the_creatures_killed():
    kills = [[18, 0]] * 30 + [[21, 1]] * 4
    then = sum(G.kill_xp(18, m, bool(e), RULE) for m, e in kills)
    now = sum(G.kill_xp(22, m, bool(e), RULE) for m, e in kills)
    g = est(level=22, runs=[trip(18, 5000, kills)])
    assert g["xp_kills"] == round(5000 * now / then) < 5000
    # No quests left: no pickups or hand-ins in the trip.
    assert g["run_s"] == 600 + 2400 + 300 + G.LFG_WAIT_S
    # Without the creatures' levels a run at another level can't be carried.
    g = est(level=22, runs=[trip(18, 5000)], quests=[quest(1, 3000)])
    assert g["kills_unknown"] and g["xp"] is None and g["xp_quests"] == 3000
    # Kills unknown: the quests alone can't say faster or slower than questing.
    assert g["summary"] == "~1h40 + quests worth ~3k XP (10% of a level)"
    assert g["vs_questing"] is None and g["lead"] == "~1h40 + quests worth ~3k XP"


def test_without_runs_the_kill_estimates_time_and_his_own_travel():
    # Owner, 2026-11-23: the kill estimate's time inside, not the guide's clear.
    qs = [quest(1, 9400), quest(2, 9400), quest(3, 9400, state="open")]
    other = trip(30, 3000, dungeon="Blackfathom Deeps")
    inside = D.estimate("City of Dalaran", 32, RULE)["inside_s"]
    g = est(level=32, xp_max=54500, name="City of Dalaran", quests=qs, others=[other], questing_xph=15107)
    # Owner, 2026-11-23: the kill estimate counts in the XP and the verdict, labelled.
    kx = D.estimate("City of Dalaran", 32, RULE)["xp"]
    assert g["basis"] == "kill_model" and not g["kills_unknown"] and g["xp_kills"] == kx
    assert g["xp"] == 28200 + kx and g["vs_questing"] is not None
    assert g["trip"]["xp_kills"]["label"] == "estimate" and g["trip"]["xp_total"]["label"] == "estimate"
    assert g["trip"]["inside_s"]["label"] == "estimate" and g["trip"]["pickup_s"]["label"] == "measured"
    assert g["run_s"] == inside + 600 + 300 + 900 + 600 + G.LFG_WAIT_S
    assert g["kill_xp"]["inside_s"] == inside
    # Gnomeregan: ~1h15 inside by its kills, where the guide said ~40 min.
    g = est(level=34, xp_max=54500, name="Gnomeregan", quests=qs, questing_xph=15107, others=[other])
    gi = D.estimate("Gnomeregan", 34, RULE)["inside_s"]
    assert g["basis"] == "kill_model" and g["trip"]["inside_s"]["value"] == gi
    assert gi > G.clear_s("Gnomeregan") or G.clear_s("Gnomeregan") is None


def test_without_runs_or_a_kill_estimate_the_kb_clear_time():
    qs = [quest(1, 9400), quest(2, 9400), quest(3, 9400, state="open")]
    clear = G.clear_s("City of Dalaran")
    assert clear == 56 * 60  # the recorded median, ahead of a guide's range
    assert G.clear_s("Excavation Site: Wetlands") and D.estimate("Excavation Site: Wetlands", 30, RULE)["xp"] is None
    g = est(level=30, name="Excavation Site: Wetlands")
    assert g["basis"] == "rules" and g["trip"]["inside_s"]["value"] == G.clear_s("Excavation Site: Wetlands")
    # Nothing times the walk there (no route, no trip that saw it): no trip
    # time rather than a walk in 0 s.
    assert g["run_s"] is None and g["trip"]["unknown_legs"] == ["travel_to_s", "travel_back_s"]
    other = trip(30, 3000, dungeon="Blackfathom Deeps")
    g = est(level=32, xp_max=54500, name="City of Dalaran", quests=qs, others=[other], questing_xph=15107,
            clear=clear)
    assert g["basis"] == "rules" and not g["kills_unknown"]
    assert g["run_s"] == clear + 600 + 300 + 900 + 600 + G.LFG_WAIT_S
    assert g["xp_quests"] == 28200
    # No kill estimate (a dungeon the model has no data for): the quests alone, no verdict.
    g = est(level=30, xp_max=54500, name="Excavation Site: Wetlands", quests=qs, questing_xph=15107, others=[other])
    assert g["kills_unknown"] and g["xp"] is None and g["vs_questing"] is None
    from wowstats.plan import forecast

    # A group may still choose the dungeon for gear. The planner can show
    # the quest XP floor, but it has no whole-trip XP to score afterwards.
    frozen = forecast.decision({}, {"name": "Excavation Site: Wetlands", "gain": g, "goals": ["gear"]}, "gear", "group")
    assert frozen["xp"] is None and frozen["quest_xp"] == 28200 and g["level_pct"] == 52
    assert g["time"] == "trip" and g["run_s"] == G.clear_s("Excavation Site: Wetlands") + 600 + 300 + 900 + 600 + G.LFG_WAIT_S
    assert g["xp_per_hour"] == per_hour(28200, g["run_s"])
    assert g["summary"] == f"~{G.about_s(g['run_s'])} + quests worth ~28k XP (52% of a level)"
    assert g["lead"] == f"~{G.about_s(g['run_s'])} + quests worth ~28k XP"
    # The quests without anything to time their pickups and hand-ins: their
    # XP, no time and no rate (28,200 XP over the clear time alone was the bug).
    g = est(level=30, xp_max=54500, name="Excavation Site: Wetlands", quests=qs, questing_xph=15107)
    assert g["run_s"] is None and g["xp_per_hour"] is None and g["xp_quests"] == 28200
    assert g["summary"] == "Its quests pay ~28k XP (52% of a level), what 1h50 of your questing pays"


def per_hour(x, s):
    from wowstats.rates import per_hour as ph

    return ph(x, s)


def test_a_dungeon_above_his_level_counts_only_the_quests_he_can_take():
    qs = [quest(1, 5000, min_level=24), quest(2, 4000, min_level=28), quest(3, 3000, blocked=True)]
    g = est(level=25, name="Alcaz Prison", quests=qs)
    assert [q["quest_id"] for q in g["quests"]] == [1]
    assert g["xp_quests"] == 5000 and g["run_s"] is None  # no clear time in the KB, no kill estimate, no run
    assert g["summary"] == "Its quests pay ~5k XP (17% of a level), what 25 min of your questing pays"


def test_a_dungeon_below_his_level_pays_nothing_for_grey_creatures():
    kills = [[20, 0]] * 40 + [[22, 1]] * 5
    g = est(level=34, runs=[trip(22, 7000, kills)])
    assert G.grey(34, RULE) == 26
    assert g["xp_kills"] == 0 and g["xp"] == 0
    assert g["summary"] == "~1h15 trip, no XP at your level (40 min inside · 20 min to find a group (assumed))"


def test_no_quest_data():
    # Nothing ran, no quests, no clear time: nothing to say, nothing made up.
    g = est(name="Blackmaw Hold")
    assert g["summary"] is None and g["xp"] is None and g["run_s"] is None and g["quests"] == []
    # His runs alone still give the kills.
    g = est(runs=[trip(20, 6000)])
    assert g["xp"] == 6000 and g["summary"] == "~1h15 trip for ~6k XP, 0.4× your questing (40 min inside · 20 min to find a group (assumed))"


def test_rounding():
    assert [G.about_xp(x) for x in (18420, 5430, 999, 815, 0)] == ["18k", "5.4k", "1,000", "820", "0"]
    assert [G.about_s(s) for s in (4790, 2100, 60)] == ["1h20", "35 min", "5 min"]
    assert G.ratio(1.449) == "1.4×" and G.ratio(0.02) == "under 0.1×"


def with_kill_levels(inp):
    """Testpriest's trips with the creatures' levels a gather now records."""
    inp = copy.deepcopy(inp)
    for t in inp["trips"]:
        lv = t["level_start"]
        t["kill_levels"] = [[lv + (i % 3) - 1, 1 if i % 5 == 0 else 0] for i in range(t.get("kills_inside") or 0)]
    return inp


def test_the_panel_the_move_and_the_mcp_agree(monkeypatch, tmp_path, priest):
    inp = with_kill_levels(priest)
    panel = V.build(inp)
    blocks = {b["name"]: b for b in panel["dungeons"]}
    # The Stockade, run at 31, now 32: carried by its creatures.
    st = blocks["The Stockade"]["gain"]
    # At most what its 21 creatures give at 32 if none were grey (5 x 32 + 45 each).
    assert st["basis"] == "runs" and not st["kills_unknown"] and 0 < st["xp_kills"] < 21 * (5 * 32 + 45)
    # BFD, run at 23: most of its creatures are grey at 32.
    assert blocks["Blackfathom Deeps"]["gain"]["xp_kills"] < 18211 / 10
    # A move for City of Dalaran gets the panel's estimate, word for word.
    move = {"kind": "dungeon", "text": "City of Dalaran (levels 28-35) drops 2 upgrades for you (...).",
            "zone": {"name": "City of Dalaran"}, "items": []}
    out = V.with_gain([move, {"kind": "upgrade", "text": "x"}], panel)
    g = blocks["City of Dalaran"]["gain"]
    assert out[0]["gain"] == g and g["summary"] in out[0]["text"] and "gain" not in out[1]
    # The MCP's dungeon_options: the same estimate, and its trip and value say the same numbers.
    monkeypatch.setattr(plantools, "_inputs", lambda ctx, args: inp)
    do = plantools.t_dungeon_options(tools.Ctx(store=None, env={"ANALYSIS_STATE_DIR": str(tmp_path)}),
                                     {"char": "Testpriest-Fixture"})
    seen = 0
    for d in do["dungeons"]:
        b = blocks.get(d["name"]) or next((x for x in panel["dungeons"] if V.key(x["name"]) == V.key(d["name"])),
                                          None)
        if not b or not d.get("gain"):
            continue
        seen += 1
        assert d["gain"] == b["gain"], d["name"]
        if d["gain"]["run_s"]:
            assert d["trip"]["time_s"] == d["gain"]["run_s"]
        if not d["gain"]["kills_unknown"]:
            assert d["value"]["xp_per_hour"] == d["gain"]["xp_per_hour"]
            assert d["trip"]["xp"] == d["gain"]["xp"]
        if d.get("redo") and not d["gain"]["kills_unknown"]:
            assert d["redo"]["xp_per_hour_run"] == d["gain"]["xp_per_hour"]
    assert seen >= 4
    # The verdict reads the same quests as the numbers under it.
    cd = blocks["City of Dalaran"]
    assert f"({cd['gain']['xp_quests']:,} XP)" in cd["why"]


def test_one_estimate_whatever_the_questing_rate_source(priest):
    # The panel and the planner take the questing rate the same way.
    p = E.pace(priest)
    q = E.rate(p, "questing") or p["xp_per_hour"]["value"]
    assert V.build(priest)["questing_xp_per_hour"] == round(q)
    for b in V.build(priest)["dungeons"]:
        assert b["gain"]["questing_xp_per_hour"] == round(q)


def test_the_verdict_bands():
    assert (G.FASTER, G.SLOWER) == (1.2, 0.8)
    assert [G.verdict(x) for x in (None, 1.2, 1.19, 0.8, 0.79)] == [None, "faster", "same", "same", "slower"]


def test_the_line_says_the_trip_and_its_parts():
    # The owner's example (2026-11-23), word for word: the quests' reward
    # upgrades first, then the drops with the chance one drops in a run.
    t = {"pickup_s": {"value": 1200}, "inside_s": {"value": 4500}, "handin_s": {"value": 900}}
    g = dict(xp=58000, xp_quests=40000, run_s=6900, level_pct=99, upgrades=4, drop_upgrades=9, drop_chance_pct=88,
             vs_questing=2.1, trip=t)
    assert G.summary(g) == "~1h55 trip for ~58k XP + 4 quest upgrades, 9 drop upgrades (88% a run), 2.1× your " \
                           "questing (20 min pickups · 1h15 inside · 15 min hand-ins)"
    assert g["lead"] == "~1h55 trip"
    # One quest upgrade and no drop left, no pickups left: a part with none is left out.
    g = {**g, "upgrades": 1, "drop_upgrades": 0, "drop_chance_pct": None, "trip": {**t, "pickup_s": {"value": None}}}
    assert G.summary(g) == "~1h55 trip for ~58k XP + 1 quest upgrade, 2.1× your questing (1h15 inside · 15 min hand-ins)"
    # Drops only, chance unknown.
    g = {**g, "upgrades": 0, "drop_upgrades": 1}
    assert G.summary(g) == ("~1h55 trip for ~58k XP + 1 drop upgrade (drop chance unknown), "
                            "2.1× your questing (1h15 inside · 15 min hand-ins)")
    # Without a kill estimate the time and quests lead and no verdict follows:
    # the unknown kill XP could flip it.
    nk = dict(xp=None, xp_quests=10000, run_s=2100, level_pct=29, time="clear")
    for vs in (None, 0.5, 1.0, 1.5):
        g = {**nk, "vs_questing": vs}
        assert G.summary(g) == "~35 min + quests worth ~10k XP (29% of a level)"
        assert g["lead"] == "~35 min + quests worth ~10k XP"


def test_a_kill_estimate_ranks_a_dungeon():
    # Owner, 2026-11-23: the kill estimate counts in the verdict. A dungeon
    # whose estimated kills beat questing comes out faster.
    walk = {**ROUTE, "pickup_s": 0, "handin_s": 0, "quests": [], "pickup": [], "handin": [], "xp_quests": 0}
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=1000, route=walk)
    assert g["xp_kills"] and G.verdict(g["vs_questing"]) == "faster"
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=10 ** 7, route=walk)
    assert G.verdict(g["vs_questing"]) == "slower"


def test_a_route_trip_replaces_the_flat_means():
    route = {"pickup_s": 1500, "travel_to_s": 240, "travel_back_s": 120, "handin_s": 1200, "quests": [1, 2],
             "prereqs": [], "pickup": ["gn1"], "handin": ["tn1"], "from": "you", "xp_quests": 9000}
    qs = [quest(1, 5000), quest(2, 4000, state="open"), quest(3, 7000)]
    other = trip(30, 3000, dungeon="Blackfathom Deeps")
    g = est(level=32, xp_max=54500, name="City of Dalaran", quests=qs, others=[other], questing_xph=15107,
            route=route)
    inside = D.estimate("City of Dalaran", 32, RULE)["inside_s"]
    assert g["run_s"] == inside + 1500 + 240 + 120 + 1200 + G.LFG_WAIT_S
    assert g["xp_quests"] == 9000 and [q["quest_id"] for q in g["quests"]] == [1, 2]
    assert {k: g["trip"][k]["label"] for k in ("pickup_s", "travel_s", "handin_s", "inside_s")} == \
        {"pickup_s": "route", "travel_s": "route", "handin_s": "route", "inside_s": "estimate"}
    assert g["trip"]["from"] == "you" and g["trip"]["stops"] == {"pickup": 1, "handin": 1}


# ---------------------------------------------------------------- his pace

ROUTE = {"pickup_s": 1500, "travel_to_s": 240, "travel_back_s": 120, "handin_s": 1200, "quests": [1, 2],
         "prereqs": [], "pickup": ["gn1"], "handin": ["tn1"], "from": "you", "xp_quests": 9000}


def legs(planned, pickup, travel, handin):
    """A past trip: its measured legs and what the rules planned for it."""
    return trip(30, 3000, dungeon="Blackfathom Deeps", route_s=planned, pickup_s=pickup, travel_to_s=travel,
                travel_back_s=0, handin_s=handin)


def test_his_pace_from_his_trips():
    # Owner, 2026-11-23: his trips take about twice the straight run. Summed
    # over the trips: 9,000 s walked for 4,000 s planned.
    trips = [legs(3000, 2000, 1000, 3000), legs(1000, 1000, 500, 1500)]
    assert V.pace(trips) == {"factor": 2.25, "trips": 2, "label": "your pace", "raw": 2.25}
    # Time-weighted: a 1-minute rerun walked in 11 doesn't swing it.
    assert V.pace([*trips, legs(60, 0, 660, 0)])["factor"] == 2.38
    # A trip the rules can't replay (no route data, no start position), or
    # one with no legs outside (logged at the door), doesn't count.
    assert V.pace([*trips, legs(None, 900, 0, 0), legs(500, 0, 0, 0)])["trips"] == 2


def test_no_trips_is_the_straight_run():
    assert V.pace([]) == V.pace(None) == {"factor": 1.0, "trips": 0, "label": "straight run"}
    assert V.pace([legs(None, 900, 0, 900)])["trips"] == 0


def test_the_pace_is_clamped():
    lo, hi = V.PACE_RANGE
    assert (lo, hi) == (1.0, 4.0)
    quick = V.pace([legs(4000, 500, 500, 0)])
    assert quick["factor"] == 1.0 and quick["raw"] == 0.25  # quicker than the rules: their miss, not his pace
    slow = V.pace([legs(600, 3000, 0, 3000)])
    assert slow["factor"] == 4.0 and slow["raw"] == 10.0


def test_his_pace_scales_the_routes_legs_and_not_the_time_inside():
    straight = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, route=ROUTE)
    p = {"factor": 2.0, "trips": 3, "label": "your pace"}
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, route=ROUTE, pace=p)
    t, s = g["trip"], straight["trip"]
    assert t["inside_s"] == s["inside_s"] and t["inside_s"]["label"] == "estimate"
    assert t["pickup_s"] == {"value": 3000, "label": "your pace"}
    assert t["travel_s"] == {"value": 720, "label": "your pace"}
    assert t["handin_s"] == {"value": 2400, "label": "your pace"}
    assert g["run_s"] == straight["run_s"] + 1500 + 240 + 120 + 1200
    assert t["pace"] == p and s["pace"] == {"factor": 1.0, "trips": 0, "label": "straight run"}
    # The line's split says the scaled minutes, nothing more.
    assert "(50 min pickups · " in g["summary"] and " · 40 min hand-ins · 20 min to find a group (assumed))" in g["summary"]
    assert "pace" not in g["summary"]
    # Before his first trip: the straight run, labelled the rules'.
    one = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, route=ROUTE,
              pace={"factor": 1.0, "trips": 0, "label": "straight run"})
    assert one["trip"]["pickup_s"] == {"value": 1500, "label": "route"}


# ---------------------------------------------------------------- legs seen or not
#
# An outside review, the third time it came back: a run logged at the door
# (1,922 s inside, nothing seen outside) made every leg outside 0 s, and the
# estimate took those zeros over the route's while it added the route's
# quest XP. Expected values below are worked by hand, never the estimator's.

DOOR = {"dungeon": "City of Dalaran", "level_start": 32, "xp_inside": 5258, "inside_s": 1922,
        "pickup_s": None, "travel_to_s": None, "travel_back_s": None, "handin_s": None}


def route_of(pickup, to, back, handin, xp=9682):
    return {"pickup_s": pickup, "travel_to_s": to, "travel_back_s": back, "handin_s": handin, "quests": [1],
            "prereqs": [], "pickup": ["gn1"], "handin": ["tn1"], "from": "you", "xp_quests": xp}


def test_a_run_seen_inside_only_takes_every_leg_outside_from_the_route():
    # The reviewer's case: the observation held, only the route's time
    # outside changes. 0, 35 and 120 minutes outside: the total grows by
    # exactly those, 1,922 s + 0 / 2,100 / 7,200 s. The bug gave 1,922 s
    # (32m02s) all three times.
    totals = {}
    for minutes, legs in ((0, (0, 0, 0, 0)), (35, (600, 300, 300, 900)), (120, (2400, 1200, 1200, 2400))):
        g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, runs=[DOOR],
                quests=[quest(1, 9682)], route=route_of(*legs))
        totals[minutes] = g["run_s"]
        assert g["xp_quests"] == 9682 and g["xp_kills"] == 5258 and g["xp"] == 14940
        assert g["trip"]["inside_s"] == {"value": 1922, "label": "measured"}
        assert g["trip"]["pickup_s"]["label"] == "route" and g["trip"]["handin_s"]["label"] == "route"
        assert g["trip"]["unknown_legs"] == []
    assert totals == {0: 1922 + G.LFG_WAIT_S, 35: 4022 + G.LFG_WAIT_S, 120: 9122 + G.LFG_WAIT_S}
    # 14,940 XP over 9,122 s and 20 min for a group: 5,211 XP an hour, not 14,940 over 1,922 s (27,983).
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, runs=[DOOR],
            quests=[quest(1, 9682)], route=route_of(2400, 1200, 1200, 2400))
    assert g["xp_per_hour"] == 5211


def test_quest_xp_comes_with_the_time_to_earn_it():
    # Seen inside only and nothing to time the pickups and hand-ins (no
    # route, no other trip that saw them): the quests' XP is said, the trip's
    # time and its XP an hour are unknown. Never 14,940 XP over 1,922 s.
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, runs=[DOOR],
            quests=[quest(1, 9682)])
    assert g["xp_quests"] == 9682 and g["run_s"] is None and g["xp_per_hour"] is None
    assert g["vs_questing"] is None
    assert g["trip"]["unknown_legs"] == ["travel_to_s", "travel_back_s", "pickup_s", "handin_s"]
    # Another trip that saw its legs fills them: 1,922 + 600 + 300 + 900 + 600 = 4,322 s.
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, runs=[DOOR],
            quests=[quest(1, 9682)], others=[trip(30, 3000, dungeon="Blackfathom Deeps")])
    assert g["run_s"] == 4322 + G.LFG_WAIT_S and g["trip"]["pickup_s"] == {"value": 900, "label": "measured"}


def test_a_fully_seen_run_is_his_time_whatever_the_route_says():
    own = trip(32, 3000, dungeon="City of Dalaran")  # 900 + 600 + 2,400 + 300 + 600 seen
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, route=ROUTE, runs=[own],
            quests=[quest(1, 5000)], pace={"factor": 2.0, "trips": 3, "label": "your pace"})
    assert g["run_s"] == 4800 + G.LFG_WAIT_S
    assert {k: g["trip"][k] for k in ("pickup_s", "travel_s", "handin_s")} == {
        "pickup_s": {"value": 900, "label": "measured"}, "travel_s": {"value": 900, "label": "measured"},
        "handin_s": {"value": 600, "label": "measured"}}
    assert g["trip"]["pace"] is None  # nothing came from the route


def test_a_partly_seen_run_mixes_his_legs_and_the_routes():
    # Pickup and the walk there seen (900 s, 600 s), the walk back and the
    # hand-ins not: those from the route (120 s, 1,200 s) at his pace 2x.
    # 900 + 600 + 2,400 + (120 + 1,200) x 2 = 6,540 s.
    own = trip(32, 3000, dungeon="City of Dalaran", travel_back_s=None, handin_s=None)
    p = {"factor": 2.0, "trips": 3, "label": "your pace"}
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, route=ROUTE, runs=[own],
            quests=[quest(1, 5000)], pace=p)
    assert g["run_s"] == 6540 + G.LFG_WAIT_S
    assert g["trip"]["pickup_s"] == {"value": 900, "label": "measured"}
    assert g["trip"]["handin_s"] == {"value": 2400, "label": "your pace"}
    assert g["trip"]["travel_s"] == {"value": 840, "label": "your pace"}  # 600 seen + 120 x 2
    assert g["trip"]["pace"] == p
    # Two runs, one saw the pickups (900 s) and one didn't: the mean is
    # 900 s over the run that saw it, not 450 s.
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, route=ROUTE,
            runs=[own, {**own, "pickup_s": None, "travel_to_s": None}], quests=[quest(1, 5000)], pace=p)
    assert g["trip"]["pickup_s"] == {"value": 900, "label": "measured"}


def test_the_pace_compares_only_the_legs_a_trip_saw():
    # A trip that saw its pickups (2,000 s, planned 1,000) and walk there
    # (1,000 s, planned 500) but not its hand-ins (planned 3,000): 3,000 s
    # over 1,500 s, 2x. Summing the planned hand-ins too read 3,000 over
    # 4,500: 0.67x, clamped to the straight run.
    t = {**legs(None, 2000, 1000, 0), "travel_back_s": None, "handin_s": None,
         "route_legs": {"pickup_s": 1000, "travel_to_s": 500, "travel_back_s": 0, "handin_s": 3000}}
    assert V.pace([t]) == {"factor": 2.0, "trips": 1, "label": "your pace", "raw": 2.0}
    # Without the planned legs one by one, only a trip that saw all four counts.
    assert V.pace([{**t, "route_legs": None, "route_s": 4500}])["trips"] == 0


def test_each_fixture_trip_is_replayed_at_its_start(priest):
    # facts.trip_pace replays both of Testpriest's trips on the rules from
    # where each started; the synthetic trips took fewer quests than the
    # rules plan, so the pace is clamped to the straight run.
    assert all(t["route_s"] and t["route_s"] > 0 for t in priest["trips"])
    p = V.pace(priest["trips"])
    assert p["trips"] == 2 and p["raw"] < 1 and p["factor"] == 1.0
    g = V.gain_named(priest, "Gnomeregan", 12000)
    assert g["trip"]["pace"] == {"factor": 1.0, "trips": 2, "label": "your pace"}
    assert g["trip"]["pickup_s"]["label"] == "your pace"


# ---------------------------------------------------------------- his quest state on the route
#
# Owner, 2026-11-25: a leg the runs didn't see comes from Polestar's route,
# on his quests now: one in the log needs no pickup (no walk to its giver),
# a done one costs neither pickup nor hand-in and pays no XP. A world on one
# line, walked at 1 yard a second with no detour, so every second is a yard:
# start at 0, giver and ender of A at 100, of B (in the log) at 200, of C
# (done) at 300, the entrance at 1,000. Talking to an NPC: 6 s.

def line_world():
    from wowstats.mcp import route

    rules = {**copy.deepcopy(route.reference().trips["rules"]), "run_speed": 1, "walk_detour": 1,
             "detour": {"0": 1.0}, "talk_s": 6}

    def npc(y, key):
        return [0, 0.0, float(y), 3, key]

    q = {1: {"level": 30, "min": 25, "xp": 1000, "g": [npc(100, "nA")], "e": [npc(100, "nA")]},
         2: {"level": 30, "min": 25, "xp": 2000, "g": [npc(200, "nB")], "e": [npc(200, "nB")]},
         3: {"level": 30, "min": 25, "xp": 4000, "g": [npc(300, "nC")], "e": [npc(300, "nC")]}}
    return {"rules": rules, "frames": {}, "graph": {"taxi": [], "legs": [], "transports": []},
            "dungeons": {"9001": {"key": "line-keep", "name": "Line Keep", "min": 25, "entrance": [0, 0.0, 1000.0],
                                  "page": [1, 2, 3], "tours": {}}},
            "quests": {str(k): v for k, v in q.items()}}


def test_the_route_costs_only_what_his_quest_state_leaves(monkeypatch):
    from wowstats.plan import tripcost as tc

    w = line_world()
    t = tc.trip(w, 9001, side="Alliance", level=30, done={3}, log={2}, at=(0, 0.0, 0.0))
    # Pickups: walk 0 -> 100 and talk to A's giver: 106 s (B is carried, C done).
    # There: 100 -> 1,000: 900 s. Back: 1,000 -> 200 (B's ender, the nearer
    # first): 800 s. Hand-ins: 6 at B, 200 -> 100: 100, 6 at A: 112 s.
    assert (t["pickup_s"], t["travel_to_s"], t["travel_back_s"], t["handin_s"]) == (106, 900, 800, 112)
    assert sorted(t["quests"]) == [1, 2]  # C is done: off the trip
    # The XP: A and B (1,000 + 2,000), never C's 4,000.
    monkeypatch.setattr(V, "trip_at", lambda *a, **k: t)
    rows = [quest(1, 1000), quest(2, 2000, state="open"), quest(3, 4000, state="done")]
    r = V.route_trip({"level": 30}, "Line Keep", rows)
    assert r["xp_quests"] == 3000
    # A run there seen inside only (1,922 s): every leg outside from this
    # route. 1,922 + 106 + 900 + 800 + 112 = 3,840 s for 3,000 quest XP + its kills.
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, runs=[DOOR], quests=rows, route=r)
    assert g["run_s"] == 3840 + G.LFG_WAIT_S and g["xp_quests"] == 3000
    assert [q["quest_id"] for q in g["quests"]] == [1, 2]
    # Everything in the log: no pickup at all, whatever an old trip measured.
    t2 = tc.trip(w, 9001, side="Alliance", level=30, done={3}, log={1, 2}, at=(0, 0.0, 0.0))
    assert (t2["pickup_s"], t2["travel_to_s"]) == (0, 1000)
    seen = {**DOOR, "pickup_s": 1500, "travel_to_s": 300}
    g = est(level=32, xp_max=54500, name="City of Dalaran", questing_xph=15107, runs=[seen], quests=rows,
            route={**t2, "xp_quests": 3000})
    assert g["trip"]["pickup_s"] == {"value": None, "label": None}
    # 1,922 inside + 300 there (seen) + 800 back + 112 hand-ins (the route's).
    assert g["run_s"] == 1922 + 300 + 800 + 112 + G.LFG_WAIT_S


def test_repeatable_variants_share_one_handin_but_distinct_chain_steps_pay(monkeypatch):
    # Two item variants of the same reward plus two distinct steps with a
    # shared title. A route ID can also occur in both lists.
    t = {"quests": [20, 21, 22, 23], "prereqs": [21, 22]}
    monkeypatch.setattr(V, "trip_at", lambda *args: t)
    rows = [{**quest(20, 400), "variants": [20, 21]},
            {**quest(22, 100), "name": "A chain"}, {**quest(23, 300), "name": "A chain"}]
    route = V.route_trip({"level": 30}, "Line Keep", rows)
    assert [(q["quest_id"], q["xp"], q["prerequisite"]) for q in route["handins"]] == [
        (20, 400, True), (22, 100, True), (23, 300, False)]
    assert route["xp_quests"] == 800
    rows[0]["state"] = "done"
    assert V.route_trip({"level": 30}, "Line Keep", rows)["xp_quests"] == 400
    # A repeatable the panel filters out still has a variant group in its
    # dungeon's catalogue. Completing one ID removes its siblings as well.
    catalogue = {"717": {"quests": [{"quest_id": 20, "variants": [20, 21], "xp": 400}]}}
    inp = {"level": 30, "dungeon_quests": catalogue, "quests_done": [20]}
    unlisted = V.route_trip(inp, "The Stockade", rows[1:])
    assert {q["quest_id"] for q in unlisted["handins"]} == {22, 23}


def test_missing_handin_xp_marks_the_known_subtotal_and_the_verdict():
    for known, amount in (([], "XP unknown"), ([quest(2, 4000)], "4,000 known XP")):
        quests = [{**q, "rewards": []} for q in [quest(1, None), *known]]
        g = est(runs=[trip(20, 0)], quests=quests)
        assert g["unknown_handin_xp"] == 1 and g["xp_quests"] == (4000 if known else 0)
        assert "known XP" in g["summary"] if known else "quest XP unknown" in g["summary"]
        _, why, _, _ = V.judge({"level": 20}, "now", 20, [], quests, g, 1000, {}, {})
        assert f"({amount})" in why


def test_gnomeregan_route_preserves_unknown_handin_xp_without_panel_rows():
    # The pinned forever-db gives Gnogaine 3,950 XP and omits XP for the Sparklematic.
    inp = {"level": 30, "who": {"faction": "Alliance", "race": "Gnome", "class": "Warrior"},
           "open_quests": [{"quest_id": 2926}, {"quest_id": 2951}]}
    run = V.route_trip(inp, "Gnomeregan", [])
    handins = {q["quest_id"]: q["xp"] for q in run["handins"]}
    assert handins[2926] == 3950 and handins[2951] is None
    assert run["xp_quests"] == sum(x for x in handins.values() if x is not None)


@pytest.mark.parametrize(("version", "expected"), [(None, None), (2, 0)])
def test_zero_xp_on_current_and_legacy_routes(monkeypatch, version, expected):
    from types import SimpleNamespace

    from wowstats.mcp import route

    trips = {"quests": {"2951": {"level": 30, "xp": 0}}}
    if version is not None:
        trips["schema"] = version
    monkeypatch.setattr(route, "reference", lambda: SimpleNamespace(trips=trips))
    monkeypatch.setattr(V, "trip_at", lambda *args: {"prereqs": [], "quests": [2951]})
    assert V.route_trip({"level": 30}, "Gnomeregan", [])["handins"][0]["xp"] == expected

    # A known zero in the catalogue still settles a legacy export's ambiguous zero.
    catalogue = {"721": {"quests": [{"quest_id": 2951, "name": "The Sparklematic 5200!", "xp": 0, "level": 30}]}}
    inp = {"level": 30, "dungeon_quests": catalogue, "open_quests": [{"quest_id": 2951}]}
    q, = V.quests_for(inp, "721", [])
    assert q["xp_now"] == 0
    assert V.route_trip(inp, "Gnomeregan", [])["handins"][0]["xp"] == 0


@pytest.mark.parametrize(("rates", "total", "known", "unknown"), [
    ([], None, None, 0), ([None], None, None, 1), ([0], 0, 0, 0), ([0, None], None, 0, 1),
    ([0.03], 0.03, 0.03, 0), ([20, 50], 60, 60, 0), ([20, 50, None], None, 60, 1),
    ([None, 100], 100, 100, 1),
])
def test_random_drop_odds_preserve_unknown_and_measured_zero(rates, total, known, unknown):
    items = [{"item_id": i, "now": True, "slot": "Chest", "gain": 10, "chance": rate}
             for i, rate in enumerate(rates)]
    rollup = G._drop_upgrades(items, [])
    assert rollup["drop_upgrades"] == len(rates)
    assert rollup["drop_chance_pct"] == total
    assert rollup["known_drop_chance_pct"] == known
    assert rollup["unknown_drop_rates"] == unknown
    assert (rollup["expected_gain_per_clear"] is None) == bool(unknown)


def test_only_the_reward_chosen_is_removed_from_random_drops():
    items = [{"item_id": i, "now": i != 3, "slot": "Chest", "gain": 10, "chance": 20} for i in (1, 2, 3)]
    q = {"quest_id": 1, "rewards": [{"item_id": 2, "state": "missing"}]}
    rollup = G._drop_upgrades(items, [q])
    assert rollup["drop_upgrades"] == 1 and rollup["drop_chance_pct"] == 20


def test_finding_a_group_is_in_the_trip_and_the_ratio_and_said_so():
    """An assumption, not a measurement: 20 minutes for a group counts in the
    trip's time, its XP an hour and so "N x your questing", labelled."""
    assert G.LFG_WAIT_MIN == 20
    g = est(runs=[trip(20, 6000)], quests=[])
    t = g["trip"]
    assert t["lfg_s"] == {"value": 1200, "label": "estimate"}
    # The measured legs stay measured.
    assert t["total_s"]["label"] == "measured"
    assert g["run_s"] == 600 + 2400 + 300 + 1200
    assert g["xp_per_hour"] == round(6000 / g["run_s"] * 3600)
    assert g["vs_questing"] == pytest.approx(g["xp_per_hour"] / 12000, abs=0.001)
    assert "20 min to find a group (assumed)" in g["summary"]
