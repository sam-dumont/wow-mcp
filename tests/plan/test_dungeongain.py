"""One more run of a dungeon (wowstats.plan.dungeongain): XP at the level now,
time, pace against questing, the quests to take along; and the three places
that say it (the Overview's move, the Dungeons panel, the MCP's
dungeon_options) agreeing, on Testpriest's synthetic planner inputs."""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from wowstats.analysis import kb
from wowstats.mcp import plantools, tools
from wowstats.plan import dungeongain as G
from wowstats.plan import dungeonview as V
from wowstats.plan import estimate as E

DATA = Path(__file__).resolve().parent.parent / "data"
RULE = G.kill_rule()


@pytest.fixture(scope="module")
def priest():
    return json.loads(gzip.open(DATA / "plan-inputs.json.gz").read())


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
    assert g["run_s"] == 600 + 2400 + 300 + 900 + 600
    assert g["xp_kills"] == 6000 and g["xp_quests"] == 4000 and g["xp"] == 10000
    assert g["level_pct"] == 33
    assert g["xp_per_hour"] == round(10000 / 4800 * 3600)
    assert g["vs_questing"] == pytest.approx(7500 / 12000, abs=0.001)
    assert g["summary"] == "Slower than questing: go for the drops only. ~1h20 for ~10k XP (33% of a level), 0.6× your questing pace"
    assert [q["quest_id"] for q in g["quests"]] == [1]


def test_runs_at_another_level_are_carried_by_the_creatures_killed():
    kills = [[18, 0]] * 30 + [[21, 1]] * 4
    then = sum(G.kill_xp(18, m, bool(e), RULE) for m, e in kills)
    now = sum(G.kill_xp(22, m, bool(e), RULE) for m, e in kills)
    g = est(level=22, runs=[trip(18, 5000, kills)])
    assert g["xp_kills"] == round(5000 * now / then) < 5000
    # No quests left: no pickups or hand-ins in the trip.
    assert g["run_s"] == 600 + 2400 + 300
    # Without the creatures' levels a run at another level can't be carried.
    g = est(level=22, runs=[trip(18, 5000)], quests=[quest(1, 3000)])
    assert g["kills_unknown"] and g["xp"] is None and g["xp_quests"] == 3000
    assert g["summary"].startswith("~1h20 + quests worth ~3k XP (10% of a level)")
    assert g["summary"].endswith("slower than questing: go for the drops only")


def test_without_runs_the_kb_clear_time_and_his_own_travel():
    qs = [quest(1, 9400), quest(2, 9400), quest(3, 9400, state="open")]
    clear = G.clear_s("City of Dalaran")
    assert clear == 56 * 60  # the recorded median, ahead of a guide's range
    other = trip(30, 3000, dungeon="Blackfathom Deeps")
    g = est(level=32, xp_max=54500, name="City of Dalaran", quests=qs, others=[other], questing_xph=15107)
    assert g["basis"] == "rules" and g["kills_unknown"] and g["xp_kills"] is None
    assert g["run_s"] == clear + 600 + 300 + 900 + 600
    assert g["xp_quests"] == 28200 and g["level_pct"] == 52
    assert "+ quests worth ~28k XP (52% of a level)" in g["summary"]
    # No trip of his anywhere: the clear alone, and what the quests are worth in questing time.
    g = est(level=32, xp_max=54500, name="City of Dalaran", quests=qs, questing_xph=15107)
    assert g["time"] == "clear" and g["xp_per_hour"] == per_hour(28200, clear)
    assert g["summary"] == "~55 min + quests worth ~28k XP (52% of a level), 2.0× faster than your questing"
    assert g["lead"] == "~55 min + quests worth ~28k XP"


def per_hour(x, s):
    from wowstats.rates import per_hour as ph

    return ph(x, s)


def test_a_dungeon_above_his_level_counts_only_the_quests_he_can_take():
    qs = [quest(1, 5000, min_level=24), quest(2, 4000, min_level=28), quest(3, 3000, blocked=True)]
    g = est(level=25, name="Razorfen Downs", quests=qs)
    assert [q["quest_id"] for q in g["quests"]] == [1]
    assert g["xp_quests"] == 5000 and g["run_s"] is None  # no clear time in the KB, no run
    assert g["summary"] == "Its quests pay ~5k XP (17% of a level), what 25 min of your questing pays"


def test_a_dungeon_below_his_level_pays_nothing_for_grey_creatures():
    kills = [[20, 0]] * 40 + [[22, 1]] * 5
    g = est(level=34, runs=[trip(22, 7000, kills)])
    assert G.grey(34, RULE) == 26
    assert g["xp_kills"] == 0 and g["xp"] == 0
    assert g["summary"] == "~55 min, no XP at your level"


def test_no_quest_data():
    # Nothing ran, no quests, no clear time: nothing to say, nothing made up.
    g = est(name="Scarlet Monastery")
    assert g["summary"] is None and g["xp"] is None and g["run_s"] is None and g["quests"] == []
    # His runs alone still give the kills.
    g = est(runs=[trip(20, 6000)])
    assert g["xp"] == 6000 and g["summary"] == "Slower than questing: go for the drops only. ~55 min for ~6k XP (20% of a level), 0.5× your questing pace"


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
    assert st["basis"] == "runs" and not st["kills_unknown"] and 0 < st["xp_kills"] < 1350
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
    base = dict(xp=10000, xp_quests=10000, run_s=2100, level_pct=29, level=24)
    faster = G.summary({**base, "vs_questing": 1.4})
    assert faster == "Worth it: ~35 min for ~10k XP (29% of a level), 1.4× faster than your questing"
    assert G.summary({**base, "vs_questing": 1.0}) == "About the same as questing: ~35 min for ~10k XP (29% of a level)"
    assert G.summary({**base, "vs_questing": 0.5}).startswith("Slower than questing: go for the drops only. ")
    # Without a kill estimate the time and quests lead, the verdict closes.
    nk = {**base, "xp": None, "time": "clear"}
    assert G.summary({**nk, "vs_questing": 1.0}) == "~35 min + quests worth ~10k XP (29% of a level), about the same as questing"
    assert G.summary({**nk, "vs_questing": 0.5}).endswith(", slower than questing: go for the drops only")
    assert G.summary({**nk, "vs_questing": 1.5}).endswith(", 1.5× faster than your questing")
    g = {**base, "vs_questing": 1.4}
    G.summary(g)
    assert g["lead"] == "Worth it:"
