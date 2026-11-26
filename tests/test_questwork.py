"""What a quest reward really takes (wowstats.questwork), on the shipped data
files: the route's hand-in, the chain, the foe, a dungeon, a hidden gate;
and the drops of a creature nobody names or measured (wowstats.gear).

Owner, 2026-11-25: "there are a few recos here that are easier said than
done". A level-37 Alliance character was sent to Big Game Hunter (King
Bangalash, a level-43 elite, a dozen quests in) and Greater Friend of the
Library as easy errands."""

from __future__ import annotations

from dataclasses import replace

import pytest

from wowstats import gear, quests, questwork
from wowstats.questwork import EASY, HARD, LONG_SHOT, SOME, Position

STV_40 = "forever/alliance/40-50/01-stranglethorn-vale"
ARATHI = "forever/alliance/30-40/05-arathi-highlands"
BIG_GAME_HUNTER, GREATER_FRIEND, CRONE, SWEET_AMBER = 208, 79536, 1101, 53
# Welcome to the Jungle (583), then Hemet Nesingwary's three Mastery lines
# before Big Game Hunter: Tiger (185-188) done, Panther and Raptor at their
# third parts, in the log.
TIGER = (185, 186, 187, 188)
DONE = {583, *TIGER, 190, 191, 194, 195}


def pos(level=37, guide=ARATHI, step=1, done=DONE, log=(192, 196)):
    return Position(level=level, side="Alliance", guide=guide, step=step, done=set(done), log=set(log))


def test_a_quest_on_the_route_counts_by_where_the_guide_does_it():
    f = questwork.facts(BIG_GAME_HUNTER, pos(), level=43)
    assert f["route"]["guide"] == STV_40 and f["route"]["group"]
    assert f["route"]["level"] <= 37 + questwork.SOON_LEVELS
    # The guide keeps the step for a group: some effort, never "hard" for the elite.
    assert f["effort"] == SOME
    c = f["chain"]
    assert (c["before"], c["done"], c["in_log"]) == (13, 9, 2)
    assert c["next"]["quest_id"] in (192, 196) and c["next"]["in_log"]
    line = questwork.says(f, 37)
    assert line.startswith("on your route: hand-in at about level 39")
    assert "a step the guide keeps for a group" in line
    assert "chain: 9 of 13 quests before it done" in line and "2 in your log" in line and "(in your log)" in line
    assert "King Bangalash (level 43 elite, 6 over you)" in line


def test_off_the_route_an_elite_over_your_level_is_hard():
    # Past the guide's hand-in step: the route won't do it for you.
    f = questwork.facts(BIG_GAME_HUNTER, pos(guide=STV_40, step=70), level=43)
    assert "route" not in f
    assert f["effort"] == HARD
    assert "a group quest; kill King Bangalash" in questwork.says(f, 37)


def test_a_route_that_gets_there_later_is_a_trip_off_it():
    f = questwork.facts(BIG_GAME_HUNTER, pos(level=33), level=43)
    assert f["route"]["level"] > 33 + questwork.SOON_LEVELS
    assert f["effort"] == HARD  # judged off the route: a group elite ten levels up
    assert "doing it now is a trip off the route" in questwork.says(f, 33)


def test_a_dungeon_quest_names_its_dungeon_and_is_some_effort():
    f = questwork.facts(CRONE, pos(log=(CRONE,)), level=34, dungeon="Razorfen Kraul")
    assert f["in_log"] and "chain" not in f
    assert f["effort"] == SOME
    line = questwork.says(f, 37)
    assert "in your quest log" in line and "a dungeon quest: Razorfen Kraul" in line
    assert "Charlga Razorflank (level 33 elite)" in line


def test_a_gate_the_quest_data_doesnt_say_comes_from_its_guide():
    # The release links Greater Friend to Friend but has no objectives for either.
    # Polestar's Library Books guide names the book hand-ins and the reward choices.
    assert questwork.hidden_gate(GREATER_FRIEND)
    f = questwork.facts(GREATER_FRIEND, pos(), level=35)
    assert questwork.hidden_gate(78150)  # Friend of the Library, before it: the same
    assert f["side_guide"]["title"] == "Library Books" and len(f["side_guide"]["before"]) >= questwork.CHAIN_HARD
    assert f["effort"] == HARD
    assert "Polestar's Library Books guide" in questwork.says(f, 37)


def test_a_reputation_guide_is_no_gate():
    # A quest with objectives and a zone keeps its own facts: the quests a
    # reputation guide hands in earlier are none of its work.
    assert not questwork.hidden_gate(BIG_GAME_HUNTER)


def test_a_chain_says_its_next_quest_and_part():
    f = questwork.facts(SWEET_AMBER, pos(), level=44)
    assert f["chain"]["done"] == 0 and f["effort"] == HARD  # quest level 44 at 37, a chain to do
    line = questwork.says(f, 37)
    assert "next: Sweet Amber, part 1 of" in line and "quest level 44, 7 over you" in line


@pytest.fixture(scope="module")
def ref():
    r = gear.reference()
    assert r.available, r.why
    return r


def _sources(ref, item_id, who):
    srcs = (ref.data.get("sources") or {}).get(str(item_id)) or []
    out = [x for x in (gear._source(ref, s, who, ref.item(item_id) or {}, item_id) for s in srcs) if x]
    return gear.by_effort(gear._dedupe(out))


def _who(level=37):
    return gear.Who(cls="PRIEST", level=level, side=1, race_id=1, professions={}, done=set(DONE), zone=None,
                    today="2026-11-25", pos=pos(level))


@pytest.mark.parametrize(("item_id", "npc_id", "effort", "words"), [
    # Both observed sources of Ogre Magi's Mantle lack a level, zone and measured rate.
    (287008, 268423, "long shot", "Zalfan Moonshadow, not placed yet, no drop rate known"),
    (287008, 258687, "long shot", "Krol'dok Brute, not placed yet, no drop rate known"),
    (1716, 4329, "hard", "Firemane Scout (level 41-42), no drop rate known"),
])
def test_a_creature_drop_says_who_and_the_rate(ref, item_id, npc_id, effort, words):
    srcs = _sources(ref, item_id, _who())
    src = next(s for s in srcs if s["npc_id"] == npc_id)
    assert src["effort"] == effort
    assert f"NPC {src['npc_id']}" not in gear.source_text(src)
    assert words in gear.source_text(src)
    assert gear.EFFORT[gear.item_effort(srcs)] == effort


def test_library_ring_names_the_guide_and_checks_the_documented_side(ref):
    ally = _who()
    sources = _sources(ref, 281635, ally)
    assert len(sources) == 1
    ring = sources[0]
    assert (ring["quest_id"], ring["choices"], ring["effort"]) == (79536, 2, "hard")
    assert ring["basis"] == "Polestar's Library Books guide"
    assert ring["work"]["chain"]["next"]["quest_id"] == 78150
    assert not _sources(ref, 281635, replace(ally, side=2, pos=replace(ally.pos, side="Horde")))


def test_a_quest_source_carries_its_work(ref):
    s = next(x for x in _sources(ref, 270084, _who()) if x["kind"] == "quest")  # Master Hunter's Wand
    assert s["effort"] == "some effort" and s["work"]["route"]["guide"] == STV_40
    assert "on your route" in gear.source_text(s)


def test_the_quest_log_and_the_guide_step_from_records():
    rows = [{"kind": "accept", "quest_id": 1, "_time": "2026-11-20T10:00:00Z"},
            {"kind": "turnin", "quest_id": 1, "_time": "2026-11-20T11:00:00Z"},
            {"kind": "accept", "quest_id": 2, "_time": "2026-11-20T10:00:00Z"},
            {"kind": "abandon", "quest_id": 3, "t": "2026-11-20T12:00:00Z"},
            {"kind": "accept", "quest_id": 3, "t": "2026-11-20T10:00:00Z"},
            {"kind": "accept", "quest_id": 4, "t": "2026-11-20T13:00:00Z"},
            {"kind": "abandon", "quest_id": 4, "t": "2026-11-20T12:00:00Z"},
            {"kind": "accept", "quest_id": 5, "_time": "2026-11-20T10:00:00Z"}]
    assert quests.in_log(rows, done={5}) == {2, 4}
    steps = [{"kind": "step", "guide": ARATHI, "step": "3", "_time": "2026-11-20T10:00:00Z"},
             {"kind": "step", "guide": STV_40, "step": "12", "_time": "2026-11-21T10:00:00Z"}]
    assert quests.position(steps) == {"guide": STV_40, "step": 12}
    assert quests.position([]) is None


def test_effort_rules():
    lv = 37
    assert questwork.effort({}, lv) == EASY
    assert questwork.effort({"route": {"level": 38, "group": False}, "foe": {"level": 45, "elite": True}}, lv) == EASY
    assert questwork.effort({"foe": {"level": 37, "elite": True}}, lv) == SOME
    assert questwork.effort({"foe": {"level": 38, "elite": True}}, lv) == HARD
    assert questwork.effort({"foe": {"level": 40}}, lv) == SOME
    assert questwork.effort({"foe": {"level": 42}}, lv) == HARD
    assert questwork.effort({"chain": {"before": 3, "done": 2}}, lv) == SOME
    assert questwork.effort({"chain": {"before": 9, "done": 4}}, lv) == HARD
    assert questwork.effort({"bring": ["Bundle"]}, lv) == SOME
    assert questwork.effort({"type": questwork.RAID}, lv) == HARD
    assert questwork.effort({"dungeon": "Razorfen Kraul"}, lv) == SOME
    assert LONG_SHOT > HARD


def test_a_new_dungeons_quest_is_a_dungeon_quest(ref):
    # The Saltseer Captain: forever-db 2026.11.2 zones it in The Drowned City.
    s = next(x for x in _sources(ref, 279883, _who()) if x["kind"] == "quest")  # Mutinous Cuffs
    assert s["effort"] == "some effort" and s["work"]["dungeon"] == "The Drowned City"
    assert "a dungeon quest: The Drowned City" in gear.source_text(s)
