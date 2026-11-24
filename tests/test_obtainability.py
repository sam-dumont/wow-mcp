"""How hard an item is to get, weighed against a few points (owner,
2026-11-24: "Convenience and easiness before a quarter of a point"): the
effort classes, the pick per slot, long shots kept apart, the auction house
lifting a rare drop to a purchase, reputations against the recorded
standings, and the weekly ForeverDB watch."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from tests.test_advice_truth import advise, caster
from tests.test_ahsource import market, sale
from wowstats import gear

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "kb"))
import watch  # noqa: E402

EARTHEN_ROD = 9381  # wand: Shadowforge Relic Hunter, 0.01% in Classic's data, never seen in Forever
KODO_BRANDER = 15692  # wand: a quest reward
DUSTFALL_ROBES = 15455  # chest: easy
SHADOW_GOWN = 253967  # chest: a Tailoring craft the fixture can't make yet


def drop(pct, label="s", **kw):
    return {"kind": "drop", "chance_pct": pct, "label": label, **kw}


def test_each_source_has_its_class():
    e = gear.source_effort
    assert [gear.EFFORT[e({"kind": k})] for k in ("quest", "vendor", "ah", "bags")] == ["easy"] * 4
    assert gear.EFFORT[e(drop(30))] == "easy"
    assert gear.EFFORT[e(drop(10))] == "some effort"
    assert gear.EFFORT[e(drop(10, "k"))] == "hard"  # Classic's rate, never seen in Forever: one class harder
    assert gear.EFFORT[e(drop(3))] == "hard"
    assert gear.EFFORT[e(drop(None))] == "hard"  # seen dropping, no rate measured
    assert gear.EFFORT[e(drop(None, "k", world=True))] == "long shot"  # Classic's alone, no rate
    assert gear.EFFORT[e(drop(30, world=True))] == "hard"
    assert gear.EFFORT[e(drop(0.5))] == "long shot"
    assert gear.EFFORT[e(drop(0.03, "k"))] == "long shot"
    assert gear.EFFORT[e({"kind": "reputation", "standing": "Honored", "met": True})] == "easy"
    assert gear.EFFORT[e({"kind": "reputation", "standing": "Friendly", "met": False})] == "some effort"
    assert gear.EFFORT[e({"kind": "reputation", "standing": "Revered", "met": False})] == "hard"
    assert gear.EFFORT[e({"kind": "pvp", "rank": 3, "met": True})] == "some effort"
    assert gear.EFFORT[e({"kind": "pvp", "rank": 3, "met": None})] == "long shot"
    # The auction house makes anything a purchase.
    assert gear.item_effort([drop(0.03, "k"), {"kind": "ah"}]) == gear.EASY
    assert gear.runs_for(0.03) == 3333
    assert "about 3,333 runs" in gear.chance_words({"chance_pct": 0.03, "instance": "dungeon"})


def test_an_easier_item_wins_a_near_tie():
    a = advise(caster())
    chest = next(s for s in a["slots"] if s["slot_name"] == "Chest")
    gown = next(u for u in a["upgrades_all"] if u["item_id"] == SHADOW_GOWN)
    # The gown scores 0.9 more and needs effort; the robes are easy.
    assert gown["score"] > chest["best"]["score"] and gown["effort"] == "some effort"
    assert chest["best"]["item_id"] == DUSTFALL_ROBES and chest["best"]["effort"] == "easy"


def test_a_long_shot_is_never_an_upgrade():
    a = advise(caster())
    assert all(u["effort"] != "long shot" for u in a["upgrades_all"])
    assert EARTHEN_ROD not in {u["item_id"] for u in a["upgrades_all"]}
    ranged = next(s for s in a["slots"] if s["slot_name"] == "Ranged")
    best = ranged["bis_unknown"]
    assert best["item_id"] == EARTHEN_ROD and best["effort"] == "long shot"
    assert "0.01%" in best["where"] and "about 10,000 runs" in best["where"]
    # The standing measures against what can be got: the quest wand.
    assert ranged["best"]["item_id"] == KODO_BRANDER
    assert ranged["standing"]["pct"] == gear._share(ranged["current"]["score"], ranged["best"]["score"])


def test_the_auction_house_lifts_a_long_shot():
    a = gear.advise(gear.reference(), caster(), level=33, every=True, held={"bags": [], "bank": []},
                    market=market({EARTHEN_ROD: sale(25000)}))
    rod = next(u for u in a["upgrades_all"] if u["item_id"] == EARTHEN_ROD)
    assert rod["effort"] == "easy" and rod["sources"][0]["kind"] == "ah"
    assert rod["sources"][0]["price_copper"] == 25000
    assert rod["now"] and rod["value"] > 0
    # No longer out of reach: the slot's best possible is gone.
    assert (next(s for s in a["slots"] if s["slot_name"] == "Ranged")["bis_unknown"] or {}).get("item_id") != EARTHEN_ROD


# --- reputations and PvP


def who(reps=None, pvp=None) -> gear.Who:
    return gear.Who(cls="PRIEST", level=33, side=2, race_id=8, professions={}, done=set(), zone=None,
                    reps=reps, pvp_rank=pvp)


def test_a_reputation_against_the_standing():
    timbermaw = 576
    assert gear.rep_check(who(), timbermaw, "Friendly") == {"met": None}
    assert gear.rep_check(who({}), timbermaw, "Friendly") == {"met": False, "your_standing": None}
    assert gear.rep_check(who({timbermaw: (6, 10000)}), timbermaw, "Honored") == {"met": True,
                                                                                  "your_standing": "Honored"}
    got = gear.rep_check(who({timbermaw: (5, 4500)}), timbermaw, "Revered")
    assert got == {"met": False, "your_standing": "Friendly", "rep_to_go": 16500}
    words = gear.source_text({"kind": "reputation", "name": "Timbermaw Hold", "standing": "Revered", **got})
    assert words == "reputation: Timbermaw Hold Revered (you're Friendly: 16,500 rep to go)"
    honored = gear.source_text({"kind": "reputation", "name": "Timbermaw Hold", "standing": "Honored",
                                "met": True, "your_standing": "Honored"})
    assert honored == "reputation: Timbermaw Hold Honored (you're Honored)"


def test_standings_come_from_the_recorder_and_altoholic():
    rows = [
        {"feed": "wow-altoholic", "kind": "reps", "_time": "2026-11-20T10:00:00Z",
         "factions": json.dumps([[54, 6, 10171], [576, 5, 3200]])},
        {"feed": "wow-recorder", "kind": "rep", "_time": "2026-11-21T10:00:00Z", "faction_id": "576",
         "reaction": "5", "standing": "4100"},
        {"feed": "wow-altoholic", "kind": "stats", "_time": "2026-11-21T10:00:00Z", "honorable_kills": "0"},
    ]
    reps, rank = gear.standings_of(rows)
    assert reps == {54: (6, 10171), 576: (5, 4100)} and rank == 0
    assert gear.standings_of([]) == (None, None)
    pvp = {"kind": "pvp", "rank": 3, "your_rank": 0, "met": False}
    assert gear.source_text(pvp) == "PvP: rank 3 reward (you have no PvP rank: 0 honorable kills)"


# --- the weekly watch


class Fetch:
    def __init__(self):
        self.calls = 0

    def get(self, url):
        self.calls += 1
        return 503, b""


def test_foreverdb_is_taken_once_a_week(monkeypatch):
    state = json.loads(watch.STATE.read_text(encoding="utf-8"))
    known = state["foreverdb"]["known"]
    today = date(2026, 12, 1)
    monkeypatch.setattr(watch, "today", lambda: today)
    state["foreverdb"]["taken"] = (today - timedelta(days=6)).isoformat()
    f = Fetch()
    assert watch.current(state, f, None, ["foreverdb"])["foreverdb"]["data"] == known and f.calls == 0
    state["foreverdb"]["taken"] = (today - timedelta(days=7)).isoformat()
    assert watch.current(state, f, None, ["foreverdb"])["foreverdb"]["data"] is None and f.calls == 1
    # Accepting new data restarts the week; accepting the same doesn't.
    watch.accept(state, {k: {} for k in watch.TITLES} | {"foreverdb": {"data": known}}, ["foreverdb"])
    assert state["foreverdb"]["taken"] == (today - timedelta(days=7)).isoformat()
    watch.accept(state, {k: {} for k in watch.TITLES} | {"foreverdb": {"data": "0" * 16}}, ["foreverdb"])
    assert state["foreverdb"]["taken"] == today.isoformat()
