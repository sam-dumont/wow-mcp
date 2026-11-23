"""The Recorder fields the server already receives but kept only in
records.extra, parsed into tables (the 0.7.0 data slice, unit 1): each
parser on hand-made records shaped like the store's (every value a string,
as VictoriaLogs returns them) and on the generated fixtures, each table
empty without its source, and the prep version that says the tables moved."""

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wowstats import analysis
from wowstats.analysis import prep
from wowstats.mcp import route

DATA = Path(__file__).resolve().parent.parent / "data" / "analysis-events.jsonl.gz"
FIXTURE_CHAR = "Testpriest-Fixture"
CHAR = "Synth-Slice"
T0 = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)
NEW = ("reps", "offers", "quest_text", "quest_rewards", "spells_known", "flight_nodes", "attunement_steps",
       "item_values", "achievements_seen")


class Rec:
    """Recorder events of one character, one second apart unless told."""

    def __init__(self, faction="Alliance"):
        self.t = T0
        self.out: list[dict] = []
        self.faction = faction

    def __call__(self, kind, wait=1, **f):
        self.t += timedelta(seconds=wait)
        e = {"_time": self.t.isoformat().replace("+00:00", "Z"), "char": CHAR, "feed": "wow-recorder",
             "kind": kind, "source_id": f"rec:x:1:{len(self.out)}", "zone": "Durotar"}
        e.update({k: str(v) for k, v in f.items() if v is not None})
        self.out.append(e)
        return e

    def session(self, wait=1):
        return self("session", wait, level=30, faction=self.faction, race="Orc", **{"class": "PRIEST"})


def build(rows, **kw):
    return prep.build([dict(r) for r in rows], CHAR, None, **kw)


def rows_of(db, sql, *args):
    return db.execute(sql, args).fetchall()


@pytest.fixture(scope="module")
def fixture_db():
    with gzip.open(DATA) as f:
        rows = [json.loads(line) for line in f]
    return prep.build(rows, FIXTURE_CHAR, route.reference())


# ---------------------------------------------------------------- the contract


def test_the_prep_version_says_the_tables_moved():
    assert prep.PREP_VERSION == 13


def test_every_new_table_is_allowed_and_every_column_described(fixture_db):
    names = {r[0] for r in fixture_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == set(analysis.TABLES)
    for t in (*NEW, "timeline", "loot_items"):
        cols = [c[1] for c in fixture_db.execute(f'PRAGMA table_info("{t}")')]
        assert set(cols) == set(prep.DICTIONARY[t][1]), t


def test_quest_text_and_vendor_prices_are_never_evidence():
    # verify.history_read counts only "game" tables: a finding can't stand on
    # the quest's text or on the client's prices.
    assert analysis.TABLES["quest_text"] != "game"
    assert analysis.TABLES["item_values"] != "game"


def test_every_new_table_is_empty_without_its_source():
    for rows in ([], [Rec().session()]):
        db = build(rows)
        for t in NEW:
            if t != "attunement_steps" or not rows:  # its source is forever-db (below)
                assert rows_of(db, f'SELECT COUNT(*) FROM "{t}"') == [(0,)], t
        assert rows_of(db, "SELECT COUNT(wanted_quest_id) FROM loot_items") == [(0,)]


# ---------------------------------------------------------------- timeline


def test_group_state_is_unknown_until_the_sessions_group_record():
    r = Rec()
    r.session()
    r("xp", 10, xp=1, xp_max=100)  # before any group record: unknown
    r("group", 10, size=2, raid="false")
    r("xp", 10, xp=2, xp_max=100)
    r("group", 10, size=12, raid="true")
    r("xp", 10, xp=3, xp_max=100)
    r.session(wait=1000)  # a new session: unknown again until its own record
    r("xp", 10, xp=4, xp_max=100)
    r("group", 10, size=0, raid="false")
    r("xp", 10, xp=5, xp_max=100)
    got = rows_of(build(r.out), "SELECT session_id, group_size, in_raid, SUM(dur_s) FROM timeline "
                                "GROUP BY 1, 2, 3 ORDER BY 1, MIN(t0)")
    assert got == [(0, None, None, 20.0), (0, 2, 0, 20.0), (0, 12, 1, 10.0),
                   (1, None, None, 20.0), (1, 0, 0, 10.0)]


def test_swimming_indoors_and_ghost_come_from_the_position_flags():
    r = Rec()
    r.session()
    r("pos", 5, flags=2, speed=40, map_id=1411, x=50, y=50)  # swimming
    r("pos", 5, flags=4 + 1, speed=70, map_id=1411, x=51, y=50)  # indoors, mounted
    r("death", 5)
    r("pos", 5, flags=32, speed=70, map_id=1411, x=52, y=50)  # the corpse run
    r("revive", 5, how="c")
    r("pos", 5, flags=0, speed=70, map_id=1411, x=53, y=50)
    r("xp", 5, xp=1, xp_max=100)
    got = rows_of(build(r.out), "SELECT opened_by, swimming, indoors, ghost, mounted FROM timeline ORDER BY t0")
    assert got == [("session", 0, 0, 0, 0), ("pos", 1, 0, 0, 0), ("pos", 0, 1, 0, 1), ("death", 0, 1, 0, 1),
                   ("pos", 0, 0, 1, 0), ("revive", 0, 0, 0, 0), ("pos", 0, 0, 0, 0)]


def test_the_fixtures_corpse_runs_are_ghost_time(fixture_db):
    ghost, dead = fixture_db.execute("SELECT SUM(dur_s * ghost), SUM(dur_s * dead) FROM timeline").fetchone()
    assert ghost > 0 and ghost <= dead
    assert {s for (s,) in fixture_db.execute("SELECT DISTINCT group_size FROM timeline")} >= {0, 4}


# ---------------------------------------------------------------- the tables


def test_reps_one_row_per_change():
    r = Rec()
    r.session()
    r("rep", faction_id=2740, reaction=4, standing=1695, rep_gained=10)
    r("rep", faction_id=2740, reaction=4, standing=1720, rep_gained=25)
    r("rep", reaction=4)  # no faction: not a row
    got = rows_of(build(r.out), "SELECT faction_id, reaction, rep_points, rep_gained, zone FROM reps ORDER BY t")
    assert got == [(2740, 4, 1695, 10, "Durotar"), (2740, 4, 1720, 25, "Durotar")]


def test_offers_one_row_per_quest_and_state_without_the_log():
    r = Rec()
    r.session()
    r("offer", npc_kind="c", npc_id=3139, quests_available="840;841", quests_active="806",
      quests_log="806;99;100", quests_done="788", quests_not_offered="842")
    got = rows_of(build(r.out), "SELECT npc_kind, npc_id, quest_id, state FROM offers ORDER BY state, quest_id")
    assert got == [("c", 3139, 806, "active"), ("c", 3139, 840, "available"), ("c", 3139, 841, "available"),
                   ("c", 3139, 788, "done"), ("c", 3139, 842, "not_offered")]


def test_quest_text_keeps_the_first_text_and_the_profile_never_quotes_it():
    r = Rec()
    r.session()
    r("text", quest_id=840, title="Synthetic Title", objective="Do the synthetic thing.",
      text="A synthetic story for $N.")
    r("text", quest_id=840, title="Later", objective="Later", text="Later")
    r("text", quest_id=841)  # nothing to keep
    db = build(r.out)
    assert rows_of(db, "SELECT quest_id, title, objective, text FROM quest_text") == [
        (840, "Synthetic Title", "Do the synthetic thing.", "A synthetic story for $N.")]
    prof = prep.profile(db)
    assert "quest_text: 1 rows" in prof
    assert "Synthetic" not in prof and "story" not in prof


def test_quest_rewards_offered_and_picked():
    r = Rec()
    r.session()
    r("accept", quest_id=1, choices="100:1;101:1", items="200:5")
    r("accept", quest_id=2, choices="300:1;301:1")
    r("accept", quest_id=3, choices="400:1;401:1")
    r("accept", quest_id=4)  # no rewards: no rows
    r("turnin", quest_id=1, item=101)
    r("turnin", quest_id=2)  # handed in, the pick unknown
    got = rows_of(build(r.out), "SELECT quest_id, item_id, count, kind, handed_in, chosen FROM quest_rewards "
                                "ORDER BY quest_id, item_id")
    assert got == [(1, 100, 1, "choice", 1, 0), (1, 101, 1, "choice", 1, 1), (1, 200, 5, "reward", 1, None),
                   (2, 300, 1, "choice", 1, None), (2, 301, 1, "choice", 1, None),
                   (3, 400, 1, "choice", 0, None), (3, 401, 1, "choice", 0, None)]


def test_spells_known_one_row_per_spell_first_seen():
    r = Rec()
    r.session()
    r("trainer", npc_id=3044)
    r("learn", spell_id=591)
    r("learn", spell_id=8092)
    r("learn", 60, spell_id=591)
    got = rows_of(build(r.out), "SELECT spell_id, level, source FROM spells_known ORDER BY t, spell_id")
    assert got == [(591, 30, "learn"), (8092, 30, "learn")]


def test_flight_nodes_known_opened_and_flown():
    r = Rec()
    r.session()
    r("flightmaster", npc_id=3310, taxi_node=23, known_nodes="23;25")
    r("taxi", from_node=23, to_node=25)
    r("land", 60, to_node=25, seconds=60)
    r("flightmaster", npc_id=3615, taxi_node=25, known_nodes="23;25;77")
    got = rows_of(build(r.out), "SELECT node_id, known_now, opened_here, flights_from, flights_to FROM flight_nodes "
                                "ORDER BY node_id")
    assert got == [(23, 1, 1, 1, 0), (25, 1, 1, 0, 1), (77, 1, 0, 0, 0)]
    first = rows_of(build(r.out), "SELECT node_id FROM flight_nodes ORDER BY first_known_t, node_id")
    assert [n for (n,) in first] == [23, 25, 77]


def test_achievements_seen():
    r = Rec()
    r.session()
    r("achieve", achievement_id=62000)
    r("achieve")
    assert rows_of(build(r.out), "SELECT achievement_id, level FROM achievements_seen") == [(62000, 30)]


def test_attunement_steps_follow_the_side_and_the_chain_order():
    r = Rec(faction="Horde")
    r.session()
    r("turnin", quest_id=4903)
    db = build(r.out)
    got = rows_of(db, "SELECT attunement, faction, step, quest_id, done, done_source FROM attunement_steps "
                      "ORDER BY step")
    assert {g[1] for g in got} == {"Horde"} and got[-1][3] == 6602
    assert got[0] == ("Onyxia's Lair", "Horde", 1, 4903, 1, "turnin")
    assert all(g[4] == 0 for g in got[1:])
    order = {q: s for _, _, s, q, _, _ in got}
    for q, needs in rows_of(db, "SELECT quest_id, needs FROM attunement_steps"):
        assert all(order[n] < order[q] for n in json.loads(needs)), q
    # The three Tests of Skulls all come before Axtroz, which needs them.
    assert order[6585] > max(order[6582], order[6583], order[6584])


def test_attunement_steps_cover_both_sides_when_the_side_is_unknown():
    r = Rec(faction=None)
    r.session()
    got = rows_of(build(r.out), "SELECT DISTINCT faction FROM attunement_steps ORDER BY 1")
    assert got == [("Alliance",), ("Horde",)]


def test_attunement_steps_are_empty_without_forever_db(monkeypatch):
    from wowstats import forever

    monkeypatch.setattr(forever, "_data", {"quests": {}, "items": {}, "areas": {}})
    r = Rec()
    r.session()
    assert rows_of(build(r.out), "SELECT COUNT(*) FROM attunement_steps") == [(0,)]


def test_wanted_quest_by_the_objectives_item_name_or_its_progress():
    r = Rec()
    r.session()
    r("objectives", quest_id=10, objectives="item:5:Synthetic Fang")
    r("objectives", quest_id=11, objectives="item:3:")
    r("objectives", quest_id=12, objectives="monster:4:Synthetic Wolf slain;item:2:")
    # By name: the loot's own item names.
    r("loot", items="900:1;901:2", item_names=json.dumps({"900": "Synthetic Fang"}), wanted="10:1;11:1;12:2")
    # By progress right after a one-item loot (no names): 902 is quest 11's.
    r("loot", items="902:1", wanted="10:1;11:1;12:2")
    r("progress", 0, quest_id=11, objective=1, have=1, need=3)
    # A monster objective's progress never names an item; 12:1 isn't wanted.
    r("loot", items="903:1", wanted="10:1;11:1;12:2")
    r("progress", 0, quest_id=12, objective=1, have=1, need=4)
    # Learned once, 902 counts for quest 11 in a two-item loot too.
    r("loot", 30, items="902:1;904:1", wanted="11:1;12:2")
    # Too late after the loot: no evidence.
    r("loot", 30, items="905:1", wanted="11:1;12:2")
    r("progress", 10, quest_id=12, objective=2, have=1, need=2)
    # Quest 11 no longer wanted: 902 counts for nothing here.
    r("loot", 30, items="902:1", wanted="12:2")
    got = rows_of(build(r.out), "SELECT item_id, wanted_quest_id FROM loot_items ORDER BY rec_id, item_id")
    assert got == [(900, 10), (901, None), (902, 11), (903, None), (902, 11), (904, None), (905, None),
                   (902, None)]


def test_wanted_quest_is_null_when_two_quests_fit():
    r = Rec()
    r.session()
    r("objectives", quest_id=10, objectives="item:5:Synthetic Hide")
    r("objectives", quest_id=11, objectives="item:5:Synthetic Hide")
    r("loot", items="900:1", item_names=json.dumps({"900": "Synthetic Hide"}), wanted="10:1;11:1")
    r("loot", items="900:1", item_names=json.dumps({"900": "Synthetic Hide"}), wanted="11:1")
    got = rows_of(build(r.out), "SELECT wanted_quest_id FROM loot_items ORDER BY rec_id")
    assert got == [(None,), (11,)]


def test_item_values_vendor_prices_only_from_the_cards():
    r = Rec()
    r.session()
    r("loot", items="900:2;901:1;902:1")
    r("turnin", quest_id=1, item=903)
    r("accept", quest_id=2, choices="904:1")
    cards = {900: {"sell": 25}, 901: {"q": 0}, 903: {"sell": 1200}, 904: {"sell": 7, "ah": 99999}}

    def card(iid):
        if iid == 902:
            raise ValueError("a broken card")
        return cards.get(iid)

    db = build(r.out, cards=card)
    assert rows_of(db, "SELECT item_id, vendor_copper FROM item_values ORDER BY item_id") == [
        (900, 25), (903, 1200), (904, 7)]
    assert [c[1] for c in db.execute('PRAGMA table_info("item_values")')] == ["item_id", "vendor_copper"]
    # No cards and no catalog loaded in this process: empty, no error.
    assert rows_of(build(r.out), "SELECT COUNT(*) FROM item_values") == [(0,)]


def test_the_fixtures_fill_the_tables(fixture_db):
    n = dict(fixture_db.execute("SELECT 'offers', COUNT(*) FROM offers UNION ALL "
                                "SELECT 'quest_text', COUNT(*) FROM quest_text UNION ALL "
                                "SELECT 'quest_rewards', COUNT(*) FROM quest_rewards UNION ALL "
                                "SELECT 'reps', COUNT(*) FROM reps UNION ALL "
                                "SELECT 'flight_nodes', COUNT(*) FROM flight_nodes UNION ALL "
                                "SELECT 'spells_known', COUNT(*) FROM spells_known UNION ALL "
                                "SELECT 'achievements_seen', COUNT(*) FROM achievements_seen").fetchall())
    assert all(v > 0 for v in n.values()), n
    # Every choice of a quest handed in with a pick: exactly one picked.
    for q, picked in fixture_db.execute("SELECT quest_id, SUM(chosen) FROM quest_rewards WHERE kind = 'choice' "
                                        "AND chosen IS NOT NULL GROUP BY 1"):
        assert picked == 1, q
