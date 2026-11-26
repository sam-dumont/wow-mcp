"""The Dungeons panel (wowstats.plan.dungeonview) on the planner's real-shaped
inputs: Testpriest (Gnome priest 32, two dungeons run) and Testhunter (Dwarf hunter
1, nothing run, every slot nearly empty), plus the gear and the quests it
reads (wowstats.gear.instance_loot, facts.dungeon_quests)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from wowstats import gear
from wowstats.analysis import kb
from wowstats.plan import dungeonview as V
from wowstats.plan import facts


def load(name):
    """A derived fixture (tests/derived.py), for helpers outside a fixture."""
    from tests.derived import load as derived

    return derived(name)


@pytest.fixture(scope="module")
def priest(derived):
    return derived("plan-inputs.json.gz")


@pytest.fixture(scope="module")
def hunter(derived):
    return derived("plan-inputs-level1.json.gz")


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
    # Another run: the shared estimate's, the same number as the line.
    g = v["The Stockade"]["gain"]
    assert (stock["again_s"], stock["again_xp"], stock["again_xp_per_hour"]) == (g["run_s"], g["xp"],
                                                                               g["xp_per_hour"])
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


def test_forevers_new_dungeons_come_from_the_client(priest):
    """The Drowned City was missing from the client tables we read (forever-db's
    build, 1.60.1.70532) and came from the KB's dungeon.forever_new; the
    newest build lists it, so its range is the Group Finder's (35-42) and its
    way in Polestar's recording, with the KB's "what to bring" on top. A level
    32 sees it opening at 35."""
    names = by_name(V.build(priest))
    d = names["The Drowned City"]
    assert (d["min"], d["max"], d["state"], d["opens_at"], d["area_id"]) == (35, 42, "soon", 35, 16550)
    assert d["entrance"]["zone"] == "Stranglethorn Vale" and (d["entrance"]["x"], d["entrance"]["y"]) == (21.6, 27.7)
    assert d["entrance"]["flight"]["name"] == "Booty Bay"  # Testpriest is Alliance
    assert "underwater" in d["entrance"]["prep"]
    assert d["next"] == {"do": "level", "level": 35, "to_go": 3, "meanwhile": None}
    # Every dungeon of the KB's list is the client's now: no "kb:" row left.
    assert V.kb_areas() == {}


def test_a_dungeon_the_client_lacks_comes_from_the_kb():
    """The KB's rows for a dungeon no client table lists (the case before
    build 1.60.1.70888): range, zone, the way in, the citation."""
    d = V.kb_areas({})["kb:drownedcity"]
    assert (d["levels"], d["zone"], d["map"]) == ([35, 40], "Stranglethorn Vale", None)
    assert (d["entrance"]["x"], d["entrance"]["y"]) == (21, 28)
    assert d["rule"]["source_url"].startswith("https://worldofwarcraft.blizzard.com/")
    way = V.kb_entrance(d, "Alliance")
    assert way["flight"]["name"] == "Booty Bay" and "underwater" in way["prep"]


def test_no_kb_rule_the_client_still_lists_them(priest, monkeypatch):
    monkeypatch.setattr(kb, "static_rule", lambda n: None)
    assert V.kb_areas() == {} and "The Drowned City" in by_name(V.build(priest))


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
    assert "found" not in n  # a creature drops the letter: nothing lies in the world


def test_an_item_lying_in_the_world_is_found_not_dropped():
    """Lonebrow's Journal (1100) starts from Henrig Lonebrow's Journal, an
    item whose only source is an object of the same name (the journal on
    the ground): it goes under "found", never under "drops", so no page says
    the journal drops from the journal."""
    k = kb.build({"KB_GAME_TABLES": "off"})
    fresh = facts.dungeon_quests(k, "Alliance", {})
    crone = next(q for d in fresh.values() for q in d["quests"] if q["quest_id"] == 1101)
    how = crone["pre"]["1100"]
    assert [(w["kind"], w["id"]) for w in how["starts"]] == [("item", 5791)]
    assert how["drops"] == []
    assert [(w["kind"], w["id"], w["name"]) for w in how["found"]] == \
        [("object", 19861, "Henrig Lonebrow's Journal")]

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
    # ForeverDB's Leveling weights (Stamina first): the cloak beats his, the
    # Violet Sash (Intellect and spell power) doesn't.
    cloak = next(r for r in rw if r["name"] == "Vine Pruner's Cloak")
    assert cloak["quest"]["name"] == "A Green Sample" and cloak["quest"]["state"] in ("new", "open")
    assert cloak["quest"]["starts"] and cloak["url"].endswith("item=279835")
    # One per quest, and the quest says it pays an upgrade.
    assert len({r["quest"]["quest_id"] for r in rw}) == len(rw)
    q = next(q for q in b["quests"] if q["quest_id"] == 92456)
    assert q["rewards"] and q["rewards"][0]["state"] == "missing"
    # The Overview's dungeon move gets the same list.
    move = {"kind": "dungeon", "zone": {"name": "City of Dalaran"}, "text": "City of Dalaran drops 2 upgrades."}
    m = V.with_gain([move], V.build(inp))[0]
    assert m["quest_rewards"] == rw and "Vine Pruner's Cloak" in m["text"]


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


# Forever's newest dungeons that no item data covers yet: their loot from
# the research notes (data/kb/research/dungeons.json, read as shipped).
KB_ONLY = ("The Drowned City", "Blackmaw Hold", "Krol'dok Stronghold", "Alcaz Prison", "Shaper's Terrace")


def test_a_dungeon_without_loot_data_lists_the_notes_drops(priest, monkeypatch):
    # The notes' words alone: the game's own tables (data/game-items.json)
    # know these drops since 2026-11-24, and this is about a dungeon whose
    # loot no item data covers. The names as they were: forever-db's and
    # Polestar's.
    import json

    from wowstats import forever, redact
    from wowstats.plan import kbloot

    root = Path(__file__).resolve().parents[2]
    polestar = json.loads((root / "data" / "gear-reference.json").read_text(encoding="utf-8"))["names"]
    known: dict[str, int] = {}
    for k, v in [*((forever.data() or {}).get("items") or {}).items(), *polestar.items()]:
        known.setdefault(str(v).lower(), int(k))
    # forever-db 2026.11.2 names every one of the dungeon's drops: all but
    # one left out, so both paths (known by name, the notes' word alone) run.
    notes = {d["name"].lower() for d in kbloot.drops("The Drowned City")}
    known = {n: i for n, i in known.items() if n not in notes or n == "briny sash"}
    monkeypatch.setattr(kbloot, "_ids", lambda: known)

    # forever-db 2026.11.2 has the Drowned City's loot: taken out, so the
    # dungeon stands for one no item data covers yet.
    inp = copy.deepcopy(priest)
    inp["dungeon_loot"] = {k: v for k, v in (inp.get("dungeon_loot") or {}).items() if k != "16550"}
    b = by_name(V.build(inp))["The Drowned City"]
    assert b["drops"] == [] and b["state"] == "soon"
    kbd = {d["name"]: d for d in b["kb_drops"]}
    assert len(kbd) == 23 and {d["confidence"] for d in kbd.values()} == {"low"}
    assert [n for n, d in kbd.items() if d["boss"] == "Zul'Alai"] == [
        "Berserker Shackles", "Oathbound Bracers", "Bloodslash Axe"]
    # "The wiki adds" an item to a boss: still that boss's.
    assert kbd["Linken's Boots of Iron"]["boss"] == "Primeval Elemental"
    assert kbd["Gill's Gills"]["boss"] == "Gill"  # "Rare Gill"
    # Known by name: its ID; the rest by name and boss, no ID, not dropped.
    assert kbd["Briny Sash"]["item_id"] == 274605
    assert kbd["Serpentbane Crown"]["item_id"] is None and kbd["Serpentbane Crown"]["boss"] == "Min'loth the Serpent"
    # A shadow priest at 32: cloth and caster pieces are his; mail, a tank
    # shield and the melee ring aren't; a saber the notes say nothing of: unknown.
    assert kbd["Robe of the Drowned"]["fit"] is True and kbd["Priestess Cloak"]["fit"] is True
    assert kbd["Viscera Spaulders"]["fit"] is False and kbd["Maw of Min'loth"]["fit"] is False
    assert kbd["Knucklebone Ring"]["fit"] is False and kbd["Dreadfrost Saber"]["fit"] is None
    fits = [d["fit"] for d in b["kb_drops"]]
    assert fits == sorted(fits, key=lambda f: {True: 0, None: 1, False: 2}[f])
    # 6 since 2026-11-24: Briny Sash, known by its ID, is judged on the game's
    # own stats now (a healer's sash), not the notes' words.
    assert b["kb_fit"] == 6
    assert (b["verdict"], b["why"], b["value"]) == ("opens at 35", "waiting there: 6 drops made for you", "some")
    # The sealed page keeps the list (wowstats.redact).
    z = redact.zogzog("dungeons", {"dungeons": [b]})["dungeons"][0]
    assert z["kb_fit"] == 6 and z["kb_drops"][0].keys() >= {"name", "boss", "item_id", "fit", "confidence"}


def test_a_dungeon_with_loot_data_never_takes_the_notes(priest):
    view = by_name(V.build(copy.deepcopy(priest)))
    for name in ("City of Dalaran", "Excavation Site: Wetlands"):
        assert view[name]["drops"] and view[name]["kb_drops"] == []


def test_the_notes_drops_for_a_warrior_are_plate_and_tank_pieces(priest):
    inp = copy.deepcopy(priest)
    inp["level"] = 44
    inp["who"] = {**inp["who"], "class": "WARRIOR"}
    inp["gear_spec"] = "Protection"
    inp["dungeon_loot"] = {}
    b = by_name(V.build(inp)).get("Krol'dok Stronghold")
    assert b is not None
    mine = {d["name"] for d in b["kb_drops"] if d["fit"]}
    assert {"Krol'dok Warplate Chest", "Warplate Legplates", "Zarmok's Plated Signet"} <= mine
    assert not mine & {"Shadecaster's Leggings", "Shadowlight Athame", "Chain of Binding"}


@pytest.mark.parametrize("name", KB_ONLY)
def test_every_kb_only_dungeon_reads_as_items(name):
    from wowstats.plan import kbloot

    got = kbloot.drops(name)
    assert len(got) >= 15
    names = [d["name"] for d in got]
    assert len(names) == len(set(names))
    for d in got:
        # Items, not the notes' headings, recipes or role words.
        assert d["name"][0].isupper() and ":" not in d["name"] and " / " not in d["name"]
        assert d["boss"] is None or d["boss"].lower() not in kbloot.GROUPS | kbloot.RECIPES
        assert d["armour"] in (None, *kbloot.ARMOUR)


@pytest.mark.parametrize("cls,eligible", [("MAGE", True), ("PRIEST", False), (None, False)])
def test_arcane_refreshment_in_the_checklist_and_trip(priest, cls, eligible):
    inp = {**priest, "level": 60, "xp": 0, "xp_max": 0, "who": {**priest["who"], "class": cls},
           "open_quests": [], "quests_done": []}
    east = by_name(V.build(inp))["Dire Maul: East"]
    assert (7463 in {q["quest_id"] for q in east["quests"]}) == eligible
    assert (7463 in {q["quest_id"] for q in east["gain"]["quests"]}) == eligible
