"""Our dungeon advice against Polestar's dungeons for the level
(wowstats.plan.vsguide), on synthetic cases and the synthetic priest
(Testpriest, Gnome priest 32; tests/data/plan-inputs.json.gz).

The case behind it (owner, 2026-11-19): at 32 the advice said Scarlet
Monastery: Library, Polestar had Razorfen Kraul. Scarlet Monastery opens at
30 in the Group Finder, but players start it at 35.1, so Polestar lists it
from 36: nothing measured on the character justified it.
"""

import copy
import gzip
import json
from pathlib import Path

import pytest

from wowstats import insights
from wowstats.mcp import plantools, tools
from wowstats.plan import dungeonview as D
from wowstats.plan import estimate as E
from wowstats.plan import vsguide as V

DATA = Path(__file__).resolve().parents[1] / "data"

RFK = {"name": "Razorfen Kraul", "instance": "dungeon", "levels": [29, 38], "floor": 31.1}
SM = {"name": "Scarlet Monastery", "instance": "dungeon", "levels": [30, 38], "floor": 35.1}
SFK = {"name": "Shadowfang Keep", "instance": "dungeon", "levels": [20, 30], "floor": 22.9}
GNOMER = {"name": "Gnomeregan", "instance": "dungeon", "levels": [29, 38], "floor": 31.4}
AREAS = {"491": RFK, "796": SM, "209": SFK, "721": GNOMER}


@pytest.fixture(scope="module")
def priest():
    return json.loads(gzip.open(DATA / "plan-inputs.json.gz").read())


def q(qid, state="new", complete=False, xp=5000):
    return {"quest_id": qid, "name": f"Quest {qid}", "state": state, "complete": complete, "xp_now": xp}


# ------------------------------------------------------------- the rule


def test_polestars_list_runs_from_the_floor_to_the_top():
    assert V.for_level(32, AREAS) == ["Gnomeregan", "Razorfen Kraul"]
    assert V.fits(RFK, 32) and not V.fits(SM, 32) and not V.fits(SFK, 32)
    assert V.from_level(SM) == 36 and V.from_level(RFK) == 32
    assert V.for_level(36, AREAS) == ["Scarlet Monastery", "Gnomeregan", "Razorfen Kraul"]
    # The ones with nothing left for him go, unless nothing else is left.
    assert V.for_level(32, AREAS, skip={"721"}) == ["Razorfen Kraul"]
    assert V.for_level(32, AREAS, skip={"721", "491"}) == ["Gnomeregan", "Razorfen Kraul"]
    # A dungeon without a recorded floor starts at its range's bottom.
    assert V.floor({"levels": [28, 35]}) == 28.0


def test_route_says_rfk_we_say_rfk_agrees():
    v = V.compare(32, "Razorfen Kraul", RFK, ["Razorfen Kraul"], recommended=True, why="16 missing upgrades",
                  quests=[], gain=None)
    assert v["agrees"] and v["reason"] is None and v["text"] is None
    assert V.agree_text(v) == "On Polestar's list for level 32 too."


def test_route_says_rfk_and_gear_alone_doesnt_beat_it():
    # Scarlet Monastery at 32 for its drops only: under Polestar's floor, no line, no reason.
    v = V.compare(32, "Scarlet Monastery", SM, ["Razorfen Kraul"], recommended=True,
                  why="9 missing upgrades at 94% a clear", quests=[q(1050), q(1053)], gain={"own_runs": 0})
    assert not v["agrees"] and v["under_floor"] and v["reason"] is None and v["text"] is None
    assert v["numbers"]["from_level"] == 36 and v["numbers"]["floor"] == 35.1


def test_route_says_rfk_we_say_sm_for_his_quests():
    quests = [q(1050, "open", complete=True, xp=8900), q(96800, "open", xp=6750), q(1053)]
    v = V.compare(32, "Scarlet Monastery", SM, ["Razorfen Kraul"], recommended=True, why="x", quests=quests,
                  gain=None)
    assert v["reason"] == "1 of your quests there is ready to hand in (8,900 XP)"
    assert v["text"] == ("Polestar's dungeons for level 32: Razorfen Kraul. Scarlet Monastery instead: "
                         "1 of your quests there is ready to hand in (8,900 XP).")
    assert v["numbers"]["quests_ready"] == 1 and v["numbers"]["quests_in_log"] == 2
    # In his log, none ready: the count and their XP.
    v = V.compare(32, "Scarlet Monastery", SM, ["Razorfen Kraul", "Gnomeregan"], recommended=True, why="x",
                  quests=[q(1050, "open", xp=8900), q(96800, "open", xp=6750)], gain=None)
    assert v["text"] == ("Polestar's dungeons for level 32: Razorfen Kraul and Gnomeregan. Scarlet Monastery "
                         "instead: 2 of your quests are there (16k XP).")


def test_route_says_rfk_we_say_sm_for_his_own_pace():
    gain = {"own_runs": 2, "xp_per_hour": 20800, "questing_xp_per_hour": 13000, "kills_unknown": False}
    v = V.compare(32, "Scarlet Monastery", SM, ["Razorfen Kraul"], recommended=True, why="x", quests=[], gain=gain)
    assert v["reason"] == "your runs there give 1.6× your questing XP per hour"
    assert v["numbers"]["vs_questing"] == 1.6 and v["numbers"]["run_xp_per_hour"] == 20800
    # Slower than his questing: no reason, follow Polestar.
    slow = {**gain, "xp_per_hour": 9000}
    assert V.compare(32, "Scarlet Monastery", SM, ["Razorfen Kraul"], recommended=True, why="x", quests=[],
                     gain=slow)["reason"] is None
    # Kills not measured (no run of his there): no pace to quote.
    unknown = {**gain, "kills_unknown": True}
    assert V.compare(32, "Scarlet Monastery", SM, ["Razorfen Kraul"], recommended=True, why="x", quests=[],
                     gain=unknown)["reason"] is None


def test_outleveled_keeps_the_panels_numbers_as_its_reason():
    v = V.compare(32, "Shadowfang Keep", SFK, ["Razorfen Kraul"], recommended=True,
                  why="2 quests left (3,400 XP), one pays an upgrade", quests=[], gain=None)
    assert not v["agrees"] and not v["under_floor"]
    assert v["text"] == ("Polestar's dungeons for level 32: Razorfen Kraul. Shadowfang Keep instead: 2 quests left "
                         "(3,400 XP), one pays an upgrade.")
    # Not something we recommend: nothing to explain.
    assert V.compare(32, "Shadowfang Keep", SFK, ["Razorfen Kraul"], recommended=False, why="done", quests=[],
                     gain=None)["text"] is None


def test_a_wing_or_a_move_names_it_its_own_way():
    v = V.compare(32, "Scarlet Monastery", SM, ["Razorfen Kraul"], recommended=True, why="x",
                  quests=[q(1050, "open", xp=8900)], gain=None)
    w = V.named(v, "Scarlet Monastery: Library")
    assert w["we_say"] == "Scarlet Monastery: Library" and "Scarlet Monastery: Library instead:" in w["text"]
    assert V.named(v, None) is v and V.named(None, "x") is None


# ------------------------------------------------- the panel, the move, the MCP


def test_the_priest_at_32_follows_polestar(priest):
    panel = D.build(priest)
    blocks = {b["name"]: b for b in panel["dungeons"]}
    sm, rfk = blocks["Scarlet Monastery: Graveyard"], blocks["Razorfen Kraul"]
    # Scarlet Monastery's Graveyard waits for Polestar's floor (the dungeon's,
    # inside the wing's range); its gear alone is no reason.
    assert sm["verdict"] == "from level 36" and sm["value"] == "some"
    assert sm["why"] == "Polestar lists it from level 36; you're 32"
    assert sm["next"]["do"] == "level" and sm["next"]["level"] == 36
    assert sm["vs_guide"]["under_floor"] and sm["vs_guide"]["text"] is None
    # Razorfen Kraul is on Polestar's list, and so is every dungeon we value now.
    assert rfk["vs_guide"]["agrees"] and "Razorfen Kraul" in rfk["vs_guide"]["guide_says"]
    assert all(b["vs_guide"]["agrees"] for b in panel["dungeons"] if b["value"] == "high")
    # The planner and dungeon_options rank it with what doesn't fit now.
    p = E.pace(priest)
    dl = E.dungeons(priest, p, E.mounts(priest, p))
    by = {d["name"]: d for d in dl}
    assert "Scarlet Monastery" not in by
    assert not by["Scarlet Monastery: Graveyard"]["fits_now"] and not by["Scarlet Monastery: Library"]["fits_now"]
    assert by["Razorfen Kraul"]["fits_now"] and by["Razorfen Kraul"]["vs_guide"]["agrees"]
    assert dl[0]["fits_now"] and dl[0]["vs_guide"]["agrees"]
    assert E.branch(dl)["dungeon"] != "Scarlet Monastery: Library"


def test_the_priest_with_sm_quests_ready_goes_with_the_reason(priest, monkeypatch, tmp_path):
    # At 33, where the Library's own range starts (at 32 it opens next level
    # and says so, the hand-in still its next step).
    inp = copy.deepcopy(priest)
    inp["open_quests"].append({"quest_id": 1050, "name": "Mythology of the Titans", "level": 38, "min_level": 28,
                               "complete": True, "xp_full": 8900})
    early = next(b for b in D.build(inp)["dungeons"] if b["name"] == "Scarlet Monastery: Library")
    assert early["verdict"] == "opens at 33" and early["next"]["do"] == "handin"
    inp["level"] = 33
    panel = D.build(inp)
    sm = next(b for b in panel["dungeons"] if b["name"] == "Scarlet Monastery: Library")
    v = sm["vs_guide"]
    assert sm["value"] == "high" and sm["next"]["do"] == "handin"
    assert v["reason"].startswith("1 of your quests there is ready to hand in")
    assert v["text"].startswith("Polestar's dungeons for level 33: ") and \
        "Scarlet Monastery: Library instead: 1 of your" in v["text"]
    # Mythology of the Titans is a Library quest: the Graveyard has no reason.
    gy = next(b for b in panel["dungeons"] if b["name"] == "Scarlet Monastery: Graveyard")
    assert gy["verdict"] == "from level 36" and 1050 not in {q["quest_id"] for q in gy["quests"]}
    assert {q["quest_id"]: q["wings"] for q in sm["quests"]}[1050] == {"in": ["Library"], "how": "one"}
    # The Overview's move for the wing carries the same answer, in its own name.
    move = {"kind": "dungeon", "text": "Scarlet Monastery: Library (levels 30-38) drops 2 upgrades for you (...).",
            "zone": {"name": "Scarlet Monastery: Library"}, "items": []}
    out = D.with_gain([move], panel)[0]
    assert out["vs_guide"]["we_say"] == "Scarlet Monastery: Library"
    assert "Scarlet Monastery: Library instead: 1 of your quests" in out["text"]
    # dungeon_options: the structured field, and it fits now for that reason.
    monkeypatch.setattr(plantools, "_inputs", lambda ctx, args: inp)
    do = plantools.t_dungeon_options(tools.Ctx(store=None, env={"ANALYSIS_STATE_DIR": str(tmp_path)}),
                                     {"char": "Testpriest-Fixture"})
    lib = next(d for d in do["dungeons"] if d["name"] == "Scarlet Monastery: Library")
    assert lib["fits_now"] and set(lib["vs_guide"]) >= {"guide_says", "we_say", "reason", "numbers", "agrees"}
    assert lib["vs_guide"]["we_say"] == "Scarlet Monastery: Library" and lib["vs_guide"]["numbers"]["quests_ready"] == 1
    # The session plan ranks it with what fits now (his quest's XP counts),
    # and its facts carry the line.
    est = E.estimate(inp, 2)
    f = est["facts"]
    k = next(k for k, val in f.items() if k.endswith(".name") and val == "Scarlet Monastery: Library")[:-5]
    assert f[f"{k}.vs_guide"].startswith("Polestar's dungeons for level 33")
    assert f[f"{k}.polestar"] == "Polestar lists it from level 36"


def test_the_overview_move_puts_polestars_list_first():
    up = lambda name, gain, slot: {"name": name, "item_id": hash(name) % 10000, "gain": gain, "slot": slot,  # noqa: E731
                                   "slot_name": slot}

    def src(zone, lv, floor, pct):
        return {"kind": "drop", "dungeon": True, "zone": zone, "levels": lv, "floor": floor, "chance_pct": pct,
                "boss": "B"}

    lib = [(up(f"Lib {i}", 30, f"s{i}"), src("Scarlet Monastery: Library", [30, 38], 35.1, 20)) for i in range(5)]
    rfk = [(up("Shank", 35, "mh"), src("Razorfen Kraul", [29, 38], 31.1, 15))]
    # At 32 the Library (Polestar's from 36) is out of reach: Polestar's own comes.
    out = insights.dungeon_claims(32, [({**u, "sources": [s]}, s) for u, s in [*lib, *rfk]])
    assert [c["title"] for c in out] == ["Razorfen Kraul"] and out[0]["weight"] == 2.5 and not out[0]["blocker"]
    # At 33 it is in reach but after Polestar's, saying from which level.
    out = insights.dungeon_claims(33, [({**u, "sources": [s]}, s) for u, s in [*lib, *rfk]])
    assert [c["title"] for c in out] == ["Razorfen Kraul", "Scarlet Monastery: Library"]
    assert out[1]["blocker"] == "Polestar lists it from level 36" and out[1]["weight"] == 1.5
    # Among Polestar's own, the drop chance counts: one sure +20 beats three +30 at 1%.
    sure = [(up("Sure", 20, "a"), src("Gnomeregan", [29, 38], 31.4, 100))]
    rare = [(up(f"Rare {i}", 30, f"r{i}"), src("Razorfen Kraul", [29, 38], 31.1, 1)) for i in range(3)]
    out = insights.dungeon_claims(32, [({**u, "sources": [s]}, s) for u, s in [*rare, *sure]])
    assert [c["title"] for c in out] == ["Gnomeregan", "Razorfen Kraul"]


# ------------------------------------------------- the dungeons' own quests


@pytest.mark.parametrize("side,mine,theirs", [("Alliance", {"The Crone of the Kraul", "Mortality Wanes"},
                                               {"A Vengeful Fate", "Going, Going, Guano!"}),
                                              ("Horde", {"A Vengeful Fate", "Going, Going, Guano!"},
                                               {"The Crone of the Kraul", "Mortality Wanes"})])
def test_razorfen_kraul_has_each_sides_quests(side, mine, theirs):
    from wowstats.analysis import kb
    from wowstats.plan import facts

    rfk = facts.dungeon_quests(kb.build({"KB_GAME_TABLES": "off"}), side, {})["491"]
    names = {q["name"] for q in rfk["quests"]}
    assert mine <= names and not theirs & names and rfk["other_side"] == 2
    assert {"Willix the Importer", "Blueleaf Tubers"} <= names  # both sides'


def test_a_repeatable_hand_in_is_one_quest():
    from wowstats.plan import facts

    def row(qid, name, xp):
        return {"quest_id": qid, "name": name, "side": "Both", "xp": xp}

    out = facts._one_per_title([row(2951, "The Sparklematic 5200!", None), row(2952, "The Sparklematic 5200!", 2450),
                                row(2904, "A Fine Mess", 5800), row(4601, "The Sparklematic 5200!", None)])
    assert [(q["quest_id"], q["xp"]) for q in out] == [(2952, 2450), (2904, 5800)]
    assert out[0]["variants"] == [2951, 2952, 4601]


def test_the_priest_sees_razorfen_krauls_quests(priest):
    rfk = next(b for b in D.build(priest)["dungeons"] if b["name"] == "Razorfen Kraul")
    assert {q["name"] for q in rfk["quests"]} >= {"The Crone of the Kraul", "Mortality Wanes"}
    assert rfk["value"] == "high" and rfk["vs_guide"]["agrees"] and rfk["gain"]["xp_quests"] > 0
