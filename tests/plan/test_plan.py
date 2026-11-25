"""The session planner on real-shaped inputs: Testpriest (priest, Alliance,
level 32, two dungeon trips and gold), Testhunter (hunter, level 1, nothing
measured), a Horde character made from Testhunter's, and a max-level one. The
inputs are facts.extract's output on synthetic events
(tools/fixtures/generate.py; no Battle.net data in them)."""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from wowstats.analysis import kb, prep
from wowstats.mcp import route
from wowstats.plan import estimate as E
from wowstats.plan import facts, store, write

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def priest(derived):
    return derived("plan-inputs.json.gz")


@pytest.fixture(scope="module")
def hunter(derived):
    return derived("plan-inputs-level1.json.gz")


def numbers_from_facts(e):
    """The fallback text holds no number that isn't in a fact's value."""
    text = write.fallback(e)
    assert write.rendered_ok(text, e["facts"]) == [], text
    return text


# ---------------------------------------------------------------- Testpriest


@pytest.mark.parametrize("hours", [3, 4])
def test_priest_plan_fills_the_hours_from_his_pace(priest, hours):
    e = E.estimate(priest, hours)
    assert e["supported"] and e["context"] == "levelling"
    assert e["budget_s"] == hours * 3600 and e["play_s"] + e["stops_s"] == e["budget_s"]
    blocks = e["blocks"]
    assert blocks and blocks[0]["start_s"] == 0
    for a, b in zip(blocks, blocks[1:], strict=False):
        assert b["start_s"] == a["start_s"] + a["dur_s"]  # no gaps, no overlaps
        assert b["level_to"] >= a["level_to"]
    assert e["end"]["played_s"] <= e["play_s"]
    assert all(b["dur_s"] <= 60 * 60 for b in blocks)  # short focused blocks
    # The pace is his: per kind, with the sample size.
    assert e["pace"]["questing"]["n"] >= 5 and e["pace"]["questing"]["from"] == "your steps"
    assert e["start"]["position"]["anchor_step"] > e["start"]["position"]["shown_step"]  # Polestar fell back
    assert any("Dark Iron War" in s["what"] for s in e["skip"])
    numbers_from_facts(e)


def test_priest_more_hours_go_further(priest):
    a, b = E.estimate(priest, 3), E.estimate(priest, 4)
    assert b["end"]["level"] > a["end"]["level"]


def test_constraints_in_plain_words(priest):
    c = E.parse_constraints("only 2h, no dungeons, I want to level tailoring")
    assert c["hours"] == 2 and c["no_dungeons"] and c["profession"] == "Tailoring"
    e = E.estimate(priest, 4, "only 2h, no dungeons, I want to level tailoring")
    assert e["hours"] == 2 and e["branch"] is None
    assert e["blocks"][0]["kind"] == "profession"
    g = E.estimate(priest, 3, "group with friends")
    assert g["blocks"][0]["kind"] == "dungeon"


def test_mount_tiers_from_sourced_rules(priest):
    e = E.estimate(priest, 3)
    tiers = e["mount"]["tiers"]
    assert [t["level"] for t in tiers] == [40, 60]
    assert all(t["source"]["source_url"].startswith("https://") for t in tiers)
    cur = e["mount"]["current"]
    assert cur["level"] == 40 and cur["left_copper"] == cur["need_copper"] - priest["gold_copper"]
    # 30-odd silver an hour is far behind 100g by level 40: priced gold options appear.
    assert e["mount"]["behind"] and e["gold_options"]


def test_dungeons_ranked_with_gear_and_redo(priest):
    e = E.estimate(priest, 3)
    names = [d["name"] for d in e["dungeons"]]
    fits = [d for d in e["dungeons"] if d["fits_now"]]
    assert fits and fits[0]["expected_gain_per_clear"] > 0
    assert all(d["top_items"] for d in fits[:3])
    assert "The Stockade" in names and "Blackfathom Deeps" in names  # done: always shown with a verdict
    for d in e["dungeons"]:
        if d.get("redo"):
            assert d["redo"]["verdict"] in ("worth one more run", "done, move on",
                                             "worth looping for gold toward the mount") or \
                d["redo"]["verdict"].startswith("only for ")
            assert d["redo"]["instance_limit"].startswith("unknown")  # the recorded inputs predate the rule


def test_stockade_trip_inside_time_and_loot(priest):
    trip = next(t for t in priest["trips"] if "Stockade" in t["dungeon"])
    assert trip["inside_s"] == 1376.0 and trip["loot"]["items"] == 43
    e = E.estimate(priest, 3)
    st = next(d for d in e["dungeons"] if d["name"] == "The Stockade")
    loop = st["redo"]["loop"]
    assert loop["run_s"] == 1376 and loop["gold_copper_per_hour"] > 0 and loop["runs_for_mount"] > 0
    assert "reset" in loop["note"]  # the walk out and back in isn't measured: said so
    assert any("Stockade" in g["what"] for g in e["gold_options"])


def test_instance_limit_from_the_kb_caps_a_loop(priest):
    """instance.entries_per_hour: five entries an hour make a 12-minute
    floor under a loop's run, so a fast clear doesn't promise more."""
    assert facts.instance_limit()["per_hour"] == 5
    inp = {**priest, "instance_limit": {"per_hour": 2, "source": {}}}  # 30 minutes a run at best
    st = next(d for d in E.estimate(inp, 3)["dungeons"] if d["name"] == "The Stockade")
    assert st["redo"]["loop"]["run_s"] == 1800 and "at most 2 entries an hour" in st["redo"]["loop"]["note"]
    plain = next(d for d in E.estimate(priest, 3)["dungeons"] if d["name"] == "The Stockade")
    assert plain["redo"]["loop"]["gold_copper_per_hour"] > st["redo"]["loop"]["gold_copper_per_hour"]


def test_gear_options_carry_their_cost(priest):
    p = E.pace(priest)
    m = E.mounts(priest, p)
    slots = E.gear_options(priest, p, E.dungeons(priest, p, m), m)
    kinds = {o["kind"] for s in slots for o in s["options"]}
    assert {"quest", "dungeon"} <= kinds
    for s in slots:
        for o in s["options"]:
            assert o["cost"]
            if o["kind"] == "world drop":
                assert o["lucky_bonus"]


def test_unknown_rules_mean_no_numbers(priest):
    inp = copy.deepcopy(priest)
    inp["rules"] = {"quest_xp_share": None, "rested_accrual": None}
    inp["mount"] = {"tiers": [], "discount": None, "unknown": ["a riding tier: the KB has no sourced rule for it"],
                    "riding_skill": 0}
    e = E.estimate(inp, 3)
    assert e["mount"]["tiers"] == [] and e["mount"]["unknown"]
    assert e["logout"]["rested_per_8h"] is None and "logout.rested" not in e["facts"]
    assert all(a.get("share") is not None or "group" in a["why"] for a in e["abandon"])


# ---------------------------------------------------------------- any character


def test_level_one_hunter_uses_the_route_clock(hunter):
    assert hunter["who"]["class"] == "HUNTER" and hunter["level"] == 1
    e = E.estimate(hunter, 2)
    assert e["supported"] and e["end"]["route_clock"] and e["end"]["xp_gained"] is None
    assert e["pace"]["questing"]["factor"] == 1.0 and "route's own" in e["pace"]["questing"]["from"]
    assert 1 < e["end"]["level"] < 20
    assert e["start"]["position"]["anchor_step"] == e["start"]["position"]["shown_step"]  # nothing done yet
    assert "end.xp" not in e["facts"]
    numbers_from_facts(e)


def horde(hunter):
    ref = route.reference()
    inp = copy.deepcopy(hunter)
    inp["who"] = {"char": "Test-Horde", "class": "WARRIOR", "race": "Orc", "faction": "Horde"}
    seq = facts.guide_sequence(ref, "Horde", 1)
    g = seq[0]
    steps = facts.guide_steps(ref, g)
    kinds = facts.kinds_for(g, steps)
    inp["route_ahead"] = [{"guide": g, "guide_title": ref.guides[g].get("title"), "step": n, "planned_s": p,
                           "title": t, "kind": kinds[n]} for n, p, t in steps]
    inp["position"] = {"guide": g, "shown_step": 1, "furthest_step": 1, "anchor_step": 1, "passed_over": [],
                       "shown_behind": []}
    inp["route_levels"] = facts.route_levels(ref, {"faction": "Horde", "class": "WARRIOR"}, 1)
    inp["zone"], inp["bind"] = None, None
    return inp


def test_horde_character(hunter):
    inp = horde(hunter)
    assert inp["route_ahead"] and "horde" in inp["route_ahead"][0]["guide"]
    e = E.estimate(inp, 3)
    assert e["supported"] and e["blocks"] and e["end"]["level"] > 1
    assert all(c not in b["title"] for b in e["blocks"] for c in ("Stormwind", "Ironforge", "Darnassus"))
    numbers_from_facts(e)


def test_no_route_plans_by_level_and_max_level_says_so(hunter):
    # No route step known (the Recorder off): a plan by level on the route's
    # clock, said to be an estimate, with the line that unlocks the full one.
    inp = copy.deepcopy(hunter)
    inp["route_ahead"] = []
    e = E.estimate(inp, 3)
    assert e["supported"] and e["estimated"] and e["mode"] == "by_level"
    assert e["blocks"] and e["end"]["level"] > e["start"]["level"]
    assert "/psrec on" in e["unlock"] and e["unlock"] in numbers_from_facts(e)
    # Nothing to plan from at all (no level measured, no route clock): one
    # line on what to do, never an empty plan.
    inp["route_levels"] = {}
    e = E.estimate(inp, 3)
    assert not e["supported"] and "finish one level" in e["message"] and "/psrec on" in e["message"]
    top = copy.deepcopy(hunter)
    top["level"] = 60
    top["route_levels"] = {"58": 1, "59": 1}
    assert E.default_context(top) == "max-level-pve"
    for ctx in ("max-level-pve", "pvp"):
        out = E.estimate(hunter, 3, "", ctx)
        assert not out["supported"] and "isn't built yet" in out["message"]


# ---------------------------------------------------------------- the write-up check


def test_a_write_up_is_checked_against_the_facts():
    f = {"b1.dur": "45 min", "end.level": "33.0"}
    assert write.rendered_ok("level 33.0 in 45 min", f) == []
    assert write.rendered_ok("level 34.0", f) == ["numbers not in the facts: 34.0"]
    assert write.check("Play 45 min to 34.0", f) == {"ok": False, "problems": ["numbers not in the facts: 34.0"],
                                                     "constraints": []}


# ---------------------------------------------------------------- plans vs actual


def test_review_and_calibration(tmp_path, priest):
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    e = E.estimate(priest, 3)
    store.save(env, "Testpriest-Fixture", e)
    t0 = e["made_t"]
    cps = e["checkpoints"]
    stop = 1 - e["pace"]["stops"]["share"]
    # He took 1.5x the planned online time for every checkpoint.
    prog = [{"t": t0, "level": e["start"]["level"], "online_s": 0}]
    for c in cps:
        on = c["at_s"] / stop * 1.5
        prog.append({"t": t0 + on, "level": c["level"], "online_s": round(on)})
    r = store.reviews(env, "Testpriest-Fixture", prog)
    assert len(r) == 1 and abs(r[0]["ratio"] - 1.5) < 0.05
    cal = store.calibration(env, "Testpriest-Fixture", prog)
    assert cal["n"] == 1 and abs(cal["factor"] - 1.5) < 0.05
    inp = copy.deepcopy(priest)
    inp["calibration"] = cal
    slower = E.estimate(inp, 3)
    assert slower["end"]["level"] < e["end"]["level"]
    saved = json.loads((tmp_path / "plans.json").read_text())
    assert "market" not in json.dumps(saved)  # game-derived numbers only


def test_review_waits_for_the_first_checkpoint(tmp_path, priest):
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    e = E.estimate(priest, 3)
    store.save(env, "Testpriest-Fixture", e)
    prog = [{"t": e["made_t"], "level": 32.0, "online_s": 0}, {"t": e["made_t"] + 60, "level": 32.0, "online_s": 60}]
    assert store.reviews(env, "Testpriest-Fixture", prog) == []


# ---------------------------------------------------------------- extract on recorded events


def test_extract_on_recorded_events():
    with gzip.open(DATA / "analysis-events.jsonl.gz") as fh:
        rows = [json.loads(x) for x in fh]
    char = "Testpriest-Fixture"
    k = kb.build({"KB_GAME_TABLES": "off"})
    db = prep.build(rows, char, route.reference())
    inp = facts.extract(db, k, route.reference(), cards=False)
    for key in ("level", "xp", "position", "route_ahead", "step_history", "mount", "rules", "progress"):
        assert key in inp
    assert inp["rules"]["quest_xp_share"]["source_url"].startswith("https://")
    json.dumps(inp)  # JSON only
    e = E.estimate(inp, 2)
    assert e.get("supported") in (True, False)


# ---------------------------------------------------------------- the KB's rules


def test_rules_need_a_full_source(tmp_path):
    good = {"statement": "x", "value": 1, "source_url": "https://www.warcrafttavern.com/forever/guides/x/",
            "source_type": "t", "date": "2026-11-18", "confidence": "low", "scope": "forever",
            "last_verified": "2026-11-18"}
    (tmp_path / "rules.json").write_text(json.dumps({"rules": {
        "a.good": good, "b.no_url": {**good, "source_url": ""}, "c.bad_scope": {**good, "scope": "retail"},
        "d.off_list": {**good, "source_url": "https://example.org/a"}, "e.unverified": {**good, "last_verified": ""},
        "f.too_sure": {**good, "source_url": "https://conquestcapped.com/x", "confidence": "high"}}}))
    rules, bad = kb.load_rules([tmp_path / "rules.json"])
    assert list(rules) == ["a.good"] and len(bad) == 5
    k = kb.build({"KB_GAME_TABLES": "off"})
    assert k.rule("riding.apprentice")["value"]["level"] == 40
    assert k.rule("no.such.rule") is None
    hit = k.search("riding apprentice")
    assert any(h["url"] and h["source_type"] for h in hit)


def test_a_falling_xp_table_still_ends_a_gain():
    """A level in play without its bar read gives its XP so far (5200 after
    11400): the line through them fell below 0 at the next level and
    add_xp never finished (the MCP fakes, 2026-11-22). The extrapolation
    stays at the last level's need at least."""
    inp = {"xp_table": {"13": 11400, "14": 5200}}
    assert E.xp_need(inp, 15) == (5200, True)
    level, xp, guessed = E.add_xp(inp, 14, 0.0, 50000)
    assert level > 14 and guessed


def test_the_skip_section_says_to_look_before_skipping(priest):
    text = write.fallback(E.estimate(priest, 3))
    assert "### Look at, maybe skip" in text and "Skip or drop" not in text
    assert "skip it only if no later step needs it" in text
