"""The Dungeons panel (wowstats.plan.dungeonview) on the planner's real-shaped
inputs: Testpriest (Gnome priest 32, two dungeons run) and Testhunter (Dwarf hunter
1, nothing run, every slot nearly empty), plus the gear and the quests it
reads (wowstats.gear.instance_loot, facts.dungeon_quests)."""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from wowstats import gear
from wowstats.analysis import kb
from wowstats.plan import dungeonview as V
from wowstats.plan import facts

DATA = Path(__file__).resolve().parent.parent / "data"


def load(name):
    return json.loads(gzip.open(DATA / name).read())


@pytest.fixture(scope="module")
def priest():
    return load("plan-inputs.json.gz")


@pytest.fixture(scope="module")
def hunter():
    return load("plan-inputs-level1.json.gz")


def by_name(view):
    return {b["name"]: b for b in view["dungeons"]}


def test_priest_one_block_per_dungeon_that_matters(priest):
    v = V.build(priest)
    names = by_name(v)
    # Both dungeons he ran, Polestar's names matched to the game's.
    assert {"Blackfathom Deeps", "The Stockade"} <= set(names)
    for n in ("Blackfathom Deeps", "The Stockade"):
        assert names[n]["state"] == "run" and names[n]["history"]
    # In range now, or opening within five levels; nothing far above.
    for b in v["dungeons"]:
        assert b["state"] == "run" or b["min"] <= 32 + V.OPENS_WITHIN
        assert b["state"] != "soon" or b["opens_at"] == b["min"] > 32
        # The entrance links the zone page; a Forever dungeon the client's
        # tables don't list (the KB's), a search.
        if b["area_id"] is None:
            assert b["zone_url"] == "https://www.wowhead.com/forever/search?q=" + b["name"].replace(" ", "%20")
        else:
            assert b["zone_url"] == f"https://www.wowhead.com/forever/zone={b['area_id']}"
        # The name links Wowhead's dungeon guide when there is exactly one.
        if len(b["guides"]) == 1:
            assert b["url"] == b["guides"][0]["url"]
            assert b["url"].startswith("https://www.wowhead.com/forever/guide/")
        else:
            assert b["url"] == b["zone_url"]
    json.dumps(v)


def test_drops_obtained_or_missing_with_boss_and_chance(priest):
    for b in V.build(priest)["dungeons"]:
        for d in b["drops"]:
            assert d["state"] in ("obtained", "missing")
            assert (d["state"] == "obtained") == bool(d["where"])
            if d["state"] == "missing":
                assert d["gain"] > 0  # only real upgrades are "missing"
            assert d["url"] == f"https://www.wowhead.com/forever/item={d['item_id']}"
            for src in d["drops"]:
                assert src["npc_id"] is None or src["url"].endswith(f"npc={src['npc_id']}")
        missing = [d for d in b["drops"] if d["state"] == "missing"]
        # The missing ones lead, the ones he can wear now first, by the gain
        # weighed by who the item is built for (wowstats/gearfit.py).
        assert b["drops"][:len(missing)] == missing
        now = [d["value"] for d in missing if d["now"]]
        assert now == sorted(now, reverse=True)
        assert all(d["value"] <= d["gain"] + 0.01 and d["fit"]["label"] for d in missing)
    bfd = by_name(V.build(priest))["Blackfathom Deeps"]
    # The scepter he wears is a BFD quest reward: obtained, worn.
    assert any(d["state"] == "obtained" and d["where"] == "worn" for d in bfd["drops"])


def test_quests_have_a_state_and_who_gives_them(priest):
    stock = by_name(V.build(priest))["The Stockade"]
    states = {q["state"] for q in stock["quests"]}
    assert "done" in states
    for q in stock["quests"]:
        assert q["state"] in ("done", "open", "new")
        assert q["url"] == f"https://www.wowhead.com/forever/quest={q['quest_id']}"
        assert q["starts"] and all(w["url"] for w in q["starts"] if w["kind"] == "npc")
        assert q["xp_now"] is None if q["state"] == "done" else True


def test_trip_value_is_measured_or_says_not(priest):
    v = by_name(V.build(priest))
    stock = v["The Stockade"]["trip"]
    trip = next(t for t in priest["trips"] if "Stockade" in t["dungeon"])
    assert stock["measured"] and stock["n"] == 1
    assert stock["inside_s"] == round(trip["inside_s"]) and stock["xp_kills"] == trip["xp_inside"]
    # Another run: the kills and the quests still to do, never the ones done.
    assert stock["again_xp"] == trip["xp_inside"] + stock["quest_xp_left"]
    for b in v.values():
        if b["state"] != "run":
            assert "xp_per_run" not in b["trip"]  # Forever's XP isn't Classic's: only his own measure


def test_verdicts_weigh_more_than_xp(priest):
    v = by_name(V.build(priest))
    for b in v.values():
        assert b["value"] in ("high", "some", "none") and b["verdict"] and b["why"]
        assert "questing" not in b["verdict"]
    bfd = v["Blackfathom Deeps"]
    assert bfd["verdict"] == "worth a loop for gear" and "missing upgrade" in bfd["why"]
    # Nothing left: the plain "done".
    g = copy.deepcopy(priest)
    g["dungeon_loot"] = {}
    g["quests_done"] = sorted({*g["quests_done"], *(q["quest_id"] for d in g["dungeon_quests"].values()
                                                    for q in d["quests"])})
    g["mount"] = {}
    b = by_name(V.build(g))["Blackfathom Deeps"]
    assert (b["verdict"], b["why"], b["value"]) == ("done", "nothing left for you", "none")


def test_hunter_low_level_dwarf_hunter(hunter):
    v = V.build(hunter)
    assert v["class"] == "HUNTER" and v["faction"] == "Alliance"
    blocks = v["dungeons"]
    # Below every dungeon: the first ones that open for his side.
    assert len(blocks) == V.NEXT_UP and all(b["state"] == "soon" and b["opens_at"] for b in blocks)
    assert "Ragefire Chasm" not in by_name(v)  # the Horde's city dungeon
    assert all(b["verdict"] == f"opens at {b['min']}" for b in blocks)
    drops = [d for b in blocks for d in b["drops"]]
    assert drops and all(d["state"] == "missing" for d in drops)
    assert any(d["slot_name"] == "Ranged" for d in drops)  # a hunter's
    quests = [q for b in blocks for q in b["quests"]]
    assert quests and all(q["state"] == "new" for q in quests)


def test_instance_loot_scores_everything_a_dungeon_gives():
    ref = gear.reference()
    if not ref.available:
        pytest.skip(ref.why)
    sheet = {"class": "HUNTER", "race": "Dwarf", "faction": "Alliance", "items": []}
    loot = gear.instance_loot(ref, sheet, level=1)
    wc = loot["718"]  # Wailing Caverns
    assert wc and all(i["fills_empty"] and i["gain"] == i["score"] for i in wc)
    assert all(i["drops"] or i["quests"] for i in wc)
    # A level-60 dungeon's items too, whatever his level.
    assert "2017" in loot


def test_dungeon_quests_by_side():
    k = kb.build({"KB_GAME_TABLES": "off"})
    ally = facts.dungeon_quests(k, "Alliance", {})
    horde = facts.dungeon_quests(k, "Horde", {})
    assert not ally["2437"]["quests"] and ally["2437"]["other_side"]  # Ragefire Chasm
    assert horde["2437"]["quests"]
    assert all(q["side"] in ("Alliance", "Both", None) for d in ally.values() for q in d["quests"])
    q166 = next(q for q in ally["1581"]["quests"] if q["quest_id"] == 166)  # The Defias Brotherhood
    assert q166["starts"] == [{"kind": "npc", "id": 234, "name": "Gryan Stoutmantle"}] and q166["xp"]


# ---------------------------------------------------------------- actionable: where, what blocks, next


def test_every_block_says_where_and_what_next(priest, hunter):
    for inp in (priest, hunter):
        for b in V.build(inp)["dungeons"]:
            e = b["entrance"]
            # Polestar's curated entrance, with its source, for every dungeon shown here; the KB's
            # for the new ones the client doesn't list (its source stays in the data, off the page).
            assert e and e["zone"] and (e["x"] is not None and e["source"] or b["area_id"] is None)
            assert b["next"]["do"] in ("handin", "pickup", "level", "go", "none")
            if b["state"] == "soon":
                assert b["next"] == {"do": "level", "level": b["min"], "to_go": b["min"] - inp["level"],
                                     "meanwhile": b["next"]["meanwhile"]}
            for q in b["quests"]:
                assert q["blocked"] == bool(q["needs"] or (q["state"] == "new"
                                                           and (q["min_level"] or 0) > inp["level"]))


def test_places_come_from_the_guides_not_a_guess(priest):
    bfd = by_name(V.build(priest))["Blackfathom Deeps"]
    assert bfd["entrance"]["zone"] == "Ashenvale" and bfd["entrance"]["flight"]["name"] == "Astranaar"
    q = next(q for q in bfd["quests"] if q["quest_id"] == 1275)
    # Gershala Nightwhisper in Auberdine (the Darkshore guide's point): the
    # Felwood map's rectangle holds that spot too, and isn't the answer.
    assert q["starts"][0]["zone"] == "Darkshore"
    assert bfd["next"] == {"do": "pickup", "quest_id": 1275, "name": "Researching the Corruption"}


def test_unmet_prerequisites_block_a_quest(priest):
    stock = by_name(V.build(priest))["The Stockade"]
    q = next(q for q in stock["quests"] if q["name"] == "Bazil Thredd")
    assert q["blocked"] and q["needs"] and all(n["state"] != "done" for n in q["needs"])


def test_new_dungeons_the_client_lacks_come_from_the_kb(priest):
    """The Drowned City (35-40) isn't in the client's dungeon table: the
    KB's dungeon.forever_new gives its range and zone, its entrances rule
    the way in, so a level 32 sees it opening at 35, with what to bring."""
    names = by_name(V.build(priest))
    d = names["The Drowned City"]
    assert (d["min"], d["max"], d["state"], d["opens_at"], d["area_id"]) == (35, 40, "soon", 35, None)
    assert d["entrance"]["zone"] == "Stranglethorn Vale" and (d["entrance"]["x"], d["entrance"]["y"]) == (21, 28)
    assert d["entrance"]["flight"]["name"] == "Booty Bay"  # Testpriest is Alliance
    assert "underwater" in d["entrance"]["prep"]
    assert d["rule"]["source_url"].startswith("https://worldofwarcraft.blizzard.com/")
    assert d["next"] == {"do": "level", "level": 35, "to_go": 3, "meanwhile": None}
    # The client's own table wins for a dungeon it lists: City of Dalaran keeps the Group Finder's 28-35.
    assert "kb:cityofdalaran" not in V.kb_areas() and "kb:drownedcity" in V.kb_areas()


def test_no_kb_rule_no_extra_dungeon(priest, monkeypatch):
    monkeypatch.setattr(kb, "static_rule", lambda n: None)
    assert V.kb_areas() == {} and "The Drowned City" not in by_name(V.build(priest))


def _stockade_with_pre(priest):
    """The Stockade for Testpriest with the quest facts rebuilt from the KB
    (the saved inputs predate the prerequisites' starters)."""
    inp = copy.deepcopy(priest)
    k = kb.build({"KB_GAME_TABLES": "off"})
    # The saved quests plus the prerequisites' starters the KB gives. Bazil
    # Thredd is in the saved Stockade (Polestar's route picks it up); the
    # KB alone files it under Stormwind, so ask for it through a run.
    fresh = facts.dungeon_quests(k, "Alliance", {"The Stockade": {"pickups": [389]}})
    pre = {q["quest_id"]: q["pre"] for d in fresh.values() for q in d["quests"]}
    for d in inp["dungeon_quests"].values():
        for q in d["quests"]:
            q["pre"] = pre.get(q["quest_id"], {})
    stock = by_name(V.build(inp))["The Stockade"]
    return next(q for q in stock["quests"] if q["name"] == "Bazil Thredd")


def test_a_blocked_quest_says_how_to_get_its_prerequisite(priest):
    q = _stockade_with_pre(priest)
    assert q["blocked"]
    n = next(n for n in q["needs"] if n["name"] == "The Unsent Letter")
    # The letter starts it, and Edwin VanCleef drops the letter.
    assert [(w["kind"], w["name"]) for w in n["starts"]] == [("item", "An Unsent Letter")]
    assert [(w["kind"], w["name"]) for w in n["drops"]] == [("npc", "Edwin VanCleef")]
    assert n["url"].endswith("quest=373")

def _caster_at_30(priest):
    """Testpriest's inputs at level 30, his own worn gear (the advice answer's
    slots), the dungeon items scored afresh for that sheet, and none of
    City of Dalaran's or the Stockade's quests handed in."""
    ref = gear.reference()
    if not ref.available:
        pytest.skip(ref.why)
    worn = load("gear-priest.json.gz")
    items = [{"slot": s["slot"], "item_id": s["current"]["item_id"]} for s in worn["slots"] if s.get("current")]
    sheet = {"class": "PRIEST", "race": "Gnome", "faction": "Alliance", "items": items}
    inp = copy.deepcopy(priest)
    inp["level"], inp["upgrades"] = 30, []
    inp["dungeon_loot"] = gear.instance_loot(ref, sheet, level=30)
    theirs = {q["quest_id"] for aid in ("16544", "717") for q in inp["dungeon_quests"].get(aid, {}).get("quests", [])}
    inp["quests_done"] = [q for q in inp["quests_done"] if q not in theirs]
    return inp


def test_dalaran_quest_rewards_for_a_caster_at_30(priest):
    """City of Dalaran's quests are picked up in Stormwind and Silverpine
    (Polestar zones them by their giver); forever-db zones them in the
    dungeon, so their rewards are the dungeon's: the panel lists the ones
    that beat what he wears, with the quest and who gives it."""
    inp = _caster_at_30(priest)
    dal = [i for i in inp["dungeon_loot"]["16544"] if i["quests"]]
    assert {q["quest_id"] for i in dal for q in i["quests"]} & {92456, 92489}
    b = by_name(V.build(inp))["City of Dalaran"]
    rw = b["quest_rewards"]
    assert rw and all(r["gain"] > 0 for r in rw)
    sash = next(r for r in rw if r["name"] == "Violet Sash")  # cloth, int and spell power
    assert sash["quest"]["name"] == "Power Overwhelming" and sash["quest"]["state"] in ("new", "open")
    assert sash["quest"]["starts"] and sash["url"].endswith("item=251962")
    # One per quest, and the quest says it pays an upgrade.
    assert len({r["quest"]["quest_id"] for r in rw}) == len(rw)
    q = next(q for q in b["quests"] if q["quest_id"] == 92489)
    assert q["rewards"] and q["rewards"][0]["state"] == "missing"
    # The Overview's dungeon move gets the same list.
    move = {"kind": "dungeon", "zone": {"name": "City of Dalaran"}, "text": "City of Dalaran drops 2 upgrades."}
    m = V.with_gain([move], V.build(inp))[0]
    assert m["quest_rewards"] == rw and "Violet Sash" in m["text"]


def test_classic_dungeon_quests_given_outside_pay_too(priest):
    """The same for a Classic dungeon whose quests are given in town: the
    Stockade's (Redridge, Duskwood, Wetlands givers). Testpriest wears all
    three rewards, so they are his (worn), and nothing is left to take."""
    b = by_name(V.build(_caster_at_30(priest)))["The Stockade"]
    worn = {q for d in b["drops"] if d["where"] == "worn" for q in d["quests"]}
    assert {377, 378, 386} <= worn and not b["quest_rewards"]
    assert next(q for q in b["quests"] if q["quest_id"] == 377)["rewards"][0]["where"] == "worn"


def test_planner_files_quest_rewards_under_their_dungeon():
    """The MCP's dungeon list (estimate.dungeon_items) files a quest reward
    under the dungeon the quest is done in, not its giver's city."""
    from wowstats.plan import estimate as E

    up = {"name": "Violet Sash", "item_id": 251962, "gain": 14.5, "sources": [
        {"kind": "quest", "quest_id": 92489, "name": "Power Overwhelming", "zone": "Stormwind City", "dungeon": True}]}
    d = E.dungeon_items({"upgrades": [up]})
    assert list(d) == ["City of Dalaran"] and d["City of Dalaran"]["quests"][0]["quest_id"] == 92489
