"""Dungeons run in wings (wowstats.plan.wings, data/dungeons/wings.json):
each quest in the wings its objectives are in, on the real data; one line
per wing in the panel with its own range, quests, drops, keys and verdict;
a run counted for the wing its kills stayed in."""

from __future__ import annotations

import copy

import pytest

from wowstats.plan import dungeonview as D
from wowstats.plan import wings as W


@pytest.fixture(scope="module")
def priest(derived):
    return derived("plan-inputs.json.gz")


def wings_of(name, qid):
    q = W.quest(name, qid)
    return (q["wings"], q["how"]) if q else None


def test_scarlet_monastery_quests_by_wing():
    sm = "Scarlet Monastery"
    # A book on a Library shelf, a Library boss's drop.
    assert wings_of(sm, 1050) == (["Library"], "one")       # Mythology of the Titans
    assert wings_of(sm, 1160) == (["Library"], "one")       # Test of Lore
    assert wings_of(sm, 1951) == (["Library"], "one")       # Rituals of Power
    # Bosses in three wings: needs each.
    assert wings_of(sm, 1048) == (["Library", "Armory", "Cathedral"], "all")  # Into The Scarlet Monastery
    assert wings_of(sm, 1053) == (["Library", "Armory", "Cathedral"], "all")  # In the Name of the Light
    # Hearts of Zeal drop from Scarlet troops in every wing: any of them.
    assert wings_of(sm, 1113) == (["Graveyard", "Library", "Armory", "Cathedral"], "any")
    # Vorrel's ring drops outside; his ghost gives the quest in the Graveyard.
    assert wings_of(sm, 1051) == (["Graveyard"], "one")
    # A book with no item source: the knowledge base's Library note places it.
    assert wings_of(sm, 96800) == (["Library"], "one")       # Past Due
    assert W.quest(sm, 96800)["by"][-1][:2] == ["kb", "dungeons:KB-31:5"]


def test_dire_maul_quests_by_wing():
    dm = "Dire Maul"
    assert wings_of(dm, 7441) == (["East"], "one")          # Pusillin and the Elder Azj'Tordin
    assert wings_of(dm, 7488) == (["East"], "one")          # Lethtendris's Web
    assert wings_of(dm, 7461) == (["West"], "one")          # The Madness Within (Immol'thar)
    assert wings_of(dm, 5525) == (["North"], "one")         # Free Knot!
    assert wings_of(dm, 5528) == (["North"], "one")         # The Gordok Taste Test: the ogre who ends it
    assert wings_of(dm, 5526) == (["East"], "one")          # Shards of the Felvine (knowledge base)
    # A breadcrumb out to the camp is on no wing.
    assert wings_of(dm, 7492) == ([], "outside")


def test_maraudon_and_scripted_bosses():
    # Ramstein and Rend are summoned, never placed: their wing by name.
    assert wings_of("Stratholme", 6163) == (["Undead"], "one")         # Ramstein
    assert wings_of("Blackrock Spire", 4974) == (["Upper"], "one")     # For The Horde! (Rend)
    assert wings_of("Maraudon", 7046) == (["Purple"], "one")           # The Scepter of Celebras
    assert wings_of("Maraudon", 7064) == (["Inner"], "one")            # Corruption of Earth and Seed (Theradras)
    assert W.npc("Maraudon", 13601) == ["Inner"]                       # Tinkerer Gizlock, where cMaNGOS puts him


def test_the_panel_has_one_line_per_wing(priest):
    inp = copy.deepcopy(priest)
    inp["level"] = 38
    blocks = {b["name"]: b for b in D.build(inp)["dungeons"]}
    assert "Scarlet Monastery" not in blocks
    lib, gy, cath = (blocks[f"Scarlet Monastery: {w}"] for w in ("Library", "Graveyard", "Cathedral"))
    # Each its own range (the knowledge base's), area and wing.
    assert (lib["min"], lib["max"], gy["min"], cath["min"]) == (33, 41, 30, 38)
    assert lib["area_id"] == gy["area_id"] == 796 and lib["wing"] == "Library"
    # Its own quests; a quest of three wings on each of them, saying so.
    lq = {q["quest_id"]: q for q in lib["quests"]}
    assert 1050 in lq and 1050 not in {q["quest_id"] for q in gy["quests"]}
    assert lq[1053]["wings"] == {"in": ["Library", "Armory", "Cathedral"], "how": "all",
                                 "other_wings": ["Armory", "Cathedral"]}
    assert 1053 in {q["quest_id"] for q in cath["quests"]}
    # Its own drops: the Graveyard's bosses' items stay in the Graveyard.
    gy_bosses = {d["npc_id"] for x in gy["drops"] for d in x["drops"] if d.get("npc_id")}
    assert gy_bosses and all("Graveyard" in W.npc("Scarlet Monastery", n) for n in gy_bosses)
    # The Scarlet Key opens the Armory and the Cathedral, not the Graveyard.
    assert not gy["keys"] and [k["name"] for k in cath["keys"]] == ["The Scarlet Key"]
    # Its own verdict and line, with the kill model's wing.
    for b in (lib, gy, cath):
        assert b["verdict"] and b["gain"]["kill_xp"]["wing"] == b["wing"]
    assert gy["gain"]["kill_xp"]["xp"] != lib["gain"]["kill_xp"]["xp"]


def test_a_wing_trip_takes_its_own_quests(priest):
    inp = copy.deepcopy(priest)
    inp["level"] = 38
    lib = D.gain_named(inp, "Scarlet Monastery: Library", 20000)
    gy = D.gain_named(inp, "Scarlet Monastery: Graveyard", 20000)
    assert {1050, 96800} <= {q["quest_id"] for q in lib["quests"]}
    assert not {1050, 96800} & {q["quest_id"] for q in gy["quests"]}


def test_a_run_counts_for_the_wing_its_kills_stayed_in(priest):
    lib = {"3974": 1, "3975": 0, "4289": 12, "4291": 8}  # Loksey and Library troops
    gy = W.dungeon("Scarlet Monastery")["npcs"]
    only_gy = [n for n, ws in gy.items() if ws == ["Graveyard"]][:3]
    only_lib = [n for n, ws in gy.items() if ws == ["Library"]][:3]
    t = {"dungeon": "Scarlet Monastery", "kill_npcs": {n: 10 for n in only_lib}}
    assert W.trip_name(t) == "Scarlet Monastery: Library"
    # Half and half: no wing's measure.
    t = {"dungeon": "Scarlet Monastery", "kill_npcs": {**{n: 10 for n in only_lib}, **{n: 10 for n in only_gy}}}
    assert W.trip_name(t) == "Scarlet Monastery"
    # A dungeon run whole keeps its name; no kills known: the dungeon's.
    assert W.trip_name({"dungeon": "Gnomeregan", "kill_npcs": lib}) == "Gnomeregan"
    assert W.trip_name({"dungeon": "Scarlet Monastery"}) == "Scarlet Monastery"
    # The panel measures the wing on that run.
    inp = copy.deepcopy(priest)
    inp["level"] = 36
    trip = copy.deepcopy(next(t for t in inp["trips"] if "Stockade" in t["dungeon"]))
    trip.update(dungeon="Scarlet Monastery", kill_npcs={n: 10 for n in only_lib})
    inp["trips"].append(trip)
    blocks = {b["name"]: b for b in D.build(inp)["dungeons"]}
    assert blocks["Scarlet Monastery: Library"]["state"] == "run"
    assert blocks["Scarlet Monastery: Library"]["gain"]["basis"] == "runs"
    assert blocks["Scarlet Monastery: Graveyard"]["gain"]["basis"] != "runs"


def _sm_at_38(priest, open_quests, trips=()):
    """The external review's case: level 38, In the Name of the Light (1053)
    in the log after its breadcrumb (1052), no runs of his own."""
    inp = copy.deepcopy(priest)
    inp.update(level=38, trips=list(trips), open_quests=open_quests)
    inp["quests_done"] = list((set(inp["quests_done"]) | {1052}) - {1053})
    return {w: D.gain_named(inp, f"Scarlet Monastery: {w}", 20_000) for w in ("Armory", "Library", "Cathedral")}


def _xp_1053(priest) -> int:
    """What 1053 pays when its hand-in does: forever-db's number, which moves
    with its releases (2026.11.2: 4,700 -> 15,759), so read, never typed."""
    xp = _sm_at_38(priest, [{"quest_id": 1053, "complete": True, "accepted_t": 1.0}])["Armory"]["xp_quests"]
    assert xp > 0
    return xp


def test_a_quest_of_every_wing_pays_only_the_trip_that_finishes_it(priest):
    # 1053 needs a boss in each of the Library, the Armory and the
    # Cathedral: one wing's trip doesn't finish it, so none of them is paid
    # its XP, its reward or its hand-in. Each still lists it apart.
    xp = _xp_1053(priest)
    g = _sm_at_38(priest, [{"quest_id": 1053, "complete": False, "accepted_t": 1.0}])
    for w, x in g.items():
        assert 1053 not in {q["quest_id"] for q in x["quests"]}, w
        (wait,) = x["quests_needing_other_wings"]
        assert wait["quest_id"] == 1053 and wait["xp_now"] == xp
        assert wait["other_wings"] == [o for o in ("Library", "Armory", "Cathedral") if o != w]
    assert g["Armory"]["xp_quests"] == 0 and g["Cathedral"]["xp_quests"] == 0
    assert not g["Armory"]["trip"]["stops"]["handin"]
    # Complete in his log: whichever wing he goes to, the hand-in pays.
    g = _sm_at_38(priest, [{"quest_id": 1053, "complete": True, "accepted_t": 1.0}])
    for w, x in g.items():
        assert 1053 in {q["quest_id"] for q in x["quests"]}, w
        assert "quests_needing_other_wings" not in x
    assert g["Armory"]["xp_quests"] == xp


def test_the_last_wing_of_an_all_quest_is_paid_once_the_others_are_seen_done(priest):
    # Loksey (3974, Library) and Mograine and Whitemane (3976, 3977,
    # Cathedral) killed on trips since he took it: the Armory's Herod (3975)
    # finishes it, so the Armory's trip pays it; the other two still wait.
    since = 1_000.0
    trips = [{**copy.deepcopy(priest["trips"][0]), "dungeon": "Scarlet Monastery", "start_t": since + 10,
              "end_t": since + 20, "kill_npcs": {"3974": 1, "3976": 1, "3977": 1}}]
    g = _sm_at_38(priest, [{"quest_id": 1053, "complete": False, "accepted_t": since}], trips)
    assert 1053 in {q["quest_id"] for q in g["Armory"]["quests"]}
    assert g["Armory"]["xp_quests"] == _xp_1053(priest) and "quests_needing_other_wings" not in g["Armory"]
    assert g["Library"]["quests_needing_other_wings"][0]["other_wings"] == ["Armory"]
    # The same kills before he took it say nothing about this quest.
    g = _sm_at_38(priest, [{"quest_id": 1053, "complete": False, "accepted_t": since + 30}], trips)
    assert 1053 not in {q["quest_id"] for q in g["Armory"]["quests"]}


def test_a_quest_any_wing_finishes_stays_on_each(priest):
    # Twisted Evils (7028): Theradric crystal carvings in any Maraudon wing.
    inp = copy.deepcopy(priest)
    inp.update(level=46, trips=[], open_quests=[{"quest_id": 7028, "complete": False, "accepted_t": 1.0}])
    for w in ("Orange", "Purple"):
        b = D.gain_named(inp, f"Maraudon: {w}", 20_000)
        assert 7028 in {q["quest_id"] for q in b["quests"]}, w
        # Legends of Maraudon (7044) needs both wings' items: it waits.
        assert {q["quest_id"] for q in b.get("quests_needing_other_wings") or []} == {7044}

@pytest.mark.parametrize("complete", [False, True])
def test_orb_of_lorica_is_a_handin_and_never_an_armory_drop(priest, complete, monkeypatch, tmp_path):
    from wowstats import redact
    from wowstats.mcp import plantools, tools

    inp = copy.deepcopy(priest)
    inp.update(level=40, trips=[], open_quests=[{"quest_id": 1053, "complete": complete}])
    inp["quests_done"] = list((set(inp["quests_done"]) | {1052}) - {1053})
    orb = next(d for d in inp["dungeon_loot"]["796"] if d["item_id"] == 11262)
    assert orb["name"] == "Orb of Lorica" and not orb["drops"]
    # Leave only the orb so an extra drop cannot hide behind a real boss roll.
    inp["dungeon_loot"]["796"] = [orb]
    block = next(b for b in D.build(inp)["dungeons"] if b.get("wing") == "Armory")
    monkeypatch.setattr(plantools, "_inputs", lambda ctx, args: inp)
    response = plantools.t_dungeon_options(tools.Ctx(store=None, env={"ANALYSIS_STATE_DIR": str(tmp_path)}), {})
    option = next(d for d in response["dungeons"] if d["name"] == block["name"])
    saved = redact.zogzog("dungeons", {"dungeons": [block]})["dungeons"][0]
    for row in (block, saved, option):
        assert row["gain"]["drop_upgrades"] == 0 and row["gain"]["drop_chance_pct"] is None
        assert row["gain"]["upgrades"] == int(complete)
        assert [r["item_id"] for r in row["quest_rewards"]] == ([11262] if complete else [])
        waiting = row["deferred_quest_rewards"]
        if complete:
            assert waiting == []
        else:
            assert [(r["item_id"], r["quest"]["other_wings"]) for r in waiting] == [(11262, ["Library", "Cathedral"])]
    assert block["missing_now"] == 0 and option["top_items"] == []
    if complete:
        reward = option["quest_rewards"][0]
        assert reward["slot"] == orb["slot_name"] and reward["req_level"] == orb["level"]
        assert reward["line"] == block["quest_rewards"][0]["line"]


def test_a_locked_reward_is_visible_without_counting_on_the_trip(priest):
    inp = copy.deepcopy(priest)
    inp.update(level=32, open_quests=[], trips=[])
    inp["quests_done"] = []
    # A real reward, with a level gate above the character and no planned hand-in.
    area = {"name": "The Stockade", "levels": [24, 32]}
    q = next(q for q in inp["dungeon_quests"]["717"]["quests"] if q["quest_id"] == 377)
    item = next(d for d in inp["dungeon_loot"]["717"] if any(x["quest_id"] == 377 for x in d["quests"]))
    inp["dungeon_loot"]["717"] = [{**item, "equipped": False, "gain": 10}]
    inp["dungeon_quests"]["717"]["quests"] = [{**q, "min_level": 50}]
    inp.update(owned={}, upgrades=[])
    # No pickup route for this dungeon in this fixture: use the quest's recorded gate.
    from unittest.mock import patch

    with patch.object(D, "route_trip", return_value=None):
        b = D.block(inp, "717", area, [], {}, 10000, {})
    assert b["missing_now"] == b["gain"]["upgrades"] == 0 and b["quest_rewards"] == []
    assert b["deferred_quest_rewards"][0]["quest"]["min_level"] == 50
