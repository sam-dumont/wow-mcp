"""How hard an item is to get, weighed against a few points (owner,
2026-11-24: "Convenience and easiness before a quarter of a point"): the
effort classes, the pick per slot, long shots kept apart while levelling
(at level 60 the score alone decides), the auction house
lifting a rare drop to a purchase, reputations against the recorded
standings, and the weekly ForeverDB watch."""

from __future__ import annotations

import dataclasses
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
NECROTIC_WAND = 7708  # wand: an easy dungeon drop
DUSTFALL_ROBES = 15455  # chest: a quest the route hands in at about 37, easy from 34
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
    # At 34: below that the robes' quest is a trip off the route (questwork).
    a = advise(caster(), level=34)
    chest = next(s for s in a["slots"] if s["slot_name"] == "Chest")
    gown = next(u for u in a["upgrades_all"] if u["item_id"] == SHADOW_GOWN)
    robes = chest["best"]
    assert robes["item_id"] == DUSTFALL_ROBES and robes["effort"] == "easy"
    assert 0 < gown["score"] - robes["score"] < 1 and gown["value"] < robes["value"]
    # Tailoring 119 clears the pattern's reading rank, but the fixture has
    # neither the pattern nor a recipe list, and no trainer offer is recorded.
    craft = next(s for s in gown["sources"] if s["kind"] == "craft")
    learn = craft["plan"]["learn"]
    assert craft["your_rank"] == 119 and (learn["pattern_id"], learn["pattern_skill"]) == (253968, 110)
    assert learn["kind"] is None and learn["trainer_skill"] is None
    assert [g["kind"] for g in gown["gates"]] == ["check_recipe"] and gown["effort"] == "hard"


def test_a_long_shot_is_never_an_upgrade_while_levelling():
    a = advise(caster(), level=34)
    assert all(u["effort"] != "long shot" for u in a["upgrades_all"])
    assert EARTHEN_ROD not in {u["item_id"] for u in a["upgrades_all"]}
    ranged = next(s for s in a["slots"] if s["slot_name"] == "Ranged")
    best = ranged["bis_unknown"]
    assert best["item_id"] == EARTHEN_ROD and best["effort"] == "long shot"
    assert "0.01%" in best["where"] and "about 10,000 runs" in best["where"]
    # The standing measures against what can be got: the easy wand (Kodo
    # Brander's quest is 4 levels over, some effort, for 0.16 more).
    assert ranged["best"]["item_id"] == NECROTIC_WAND
    assert ranged["standing"]["pct"] == gear._share(ranged["current"]["score"], ranged["best"]["score"])


EASY_CHEST = 220535  # Garments of the Atal'ai Prophet, built for a shadow priest
RARE_CHEST = 13314  # Alanna's Embrace, about 2% more for a shadow priest


def two_chests() -> gear.Reference:
    """The real items and weights with two sources only: the easy chest at a
    vendor, the slightly better one a 0.03% drop (a long shot)."""
    ref = gear.reference()
    vendor = {"via": "foreverdb", "label": "s", "kind": "vendor", "npc": 1, "npc_name": "A Vendor",
              "zone_name": "Stormwind City", "map": 1453, "x": 50.0, "y": 50.0, "price": 100}
    rare = {"via": "foreverdb", "label": "s", "kind": "drop", "npc": None, "npc_name": "A Rare", "pct": 0.03,
            "kills": 20000, "zone": 139, "zone_name": "Eastern Plaguelands", "raid": False}
    return dataclasses.replace(ref, data={**ref.data, "sources": {str(EASY_CHEST): [vendor], str(RARE_CHEST): [rare]}},
                               peers=None, nowhere=None)


def test_at_60_the_score_alone_decides():
    """Owner, 2026-11-24: the effort ranking holds "only during end game at
    lvl 60 where marginal gains are the norm", read as everywhere but there.
    At 59 the easy chest is the pick and the drop stays the slot's best
    possible; at 60 the drop is the pick and an upgrade, its odds on it."""
    ref = two_chests()

    def chest(level):
        a = gear.advise(ref, caster(), level=level, every=True, held={"bags": [], "bank": []})
        return a, next(s for s in a["slots"] if s["slot_name"] == "Chest")

    a, s = chest(59)
    assert s["best"]["item_id"] == EASY_CHEST and s["best"]["effort"] == "easy"
    assert s["bis_unknown"]["item_id"] == RARE_CHEST and s["bis_unknown"]["effort"] == "long shot"
    assert RARE_CHEST not in {u["item_id"] for u in a["upgrades_all"]}

    a, s = chest(60)
    assert s["best"]["item_id"] == RARE_CHEST and s["best"]["effort"] == "long shot"
    rare = next(u for u in a["upgrades_all"] if u["item_id"] == RARE_CHEST)
    easy = next(u for u in a["upgrades_all"] if u["item_id"] == EASY_CHEST)
    assert rare["value"] > easy["value"] and rare["effort"] == "long shot"
    src = rare["sources"][0]
    assert src["chance_pct"] == 0.03 and src["expected_runs"] == 3333
    assert "0.03% over 20000 kills, about 3,333 kills" in gear.source_text(src)
    assert (s["bis_unknown"] or {}).get("item_id") != RARE_CHEST
    assert gear.effort_penalty(gear.HARD, 59) == gear.EFFORT_PENALTY[gear.HARD]
    assert gear.effort_penalty(gear.HARD, 60) == 0.0


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
    assert gear.source_text(pvp) == "PvP: rank 3 reward (rank 3 needed, you have no PvP rank)"


# --- the weekly watch


class Fetch:
    def __init__(self):
        self.calls = 0

    def get(self, url):
        self.calls += 1
        return 503, b""


def test_foreverdb_is_read_every_day(monkeypatch):
    """No weekly hold since 2026-11-25: taken yesterday, read again today."""
    state = json.loads(watch.STATE.read_text(encoding="utf-8"))
    known = state["foreverdb"]["known"]
    today = date(2026, 12, 1)
    monkeypatch.setattr(watch, "today", lambda: today)
    state["foreverdb"]["taken"] = (today - timedelta(days=1)).isoformat()
    f = Fetch()
    assert watch.current(state, f, None, ["foreverdb"])["foreverdb"]["data"] is None and f.calls == 1
    # `taken` still records the day new data was accepted.
    watch.accept(state, {k: {} for k in watch.TITLES} | {"foreverdb": {"data": known}}, ["foreverdb"])
    assert state["foreverdb"]["taken"] == (today - timedelta(days=1)).isoformat()
    watch.accept(state, {k: {} for k in watch.TITLES} | {"foreverdb": {"data": "0" * 16}}, ["foreverdb"])
    assert state["foreverdb"]["taken"] == today.isoformat()
