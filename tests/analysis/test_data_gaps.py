"""The data-gap check of 2026-11-24: four places the prep and the semantic
layer read the stored events wrong, each on made-up events shaped like the
history that showed it.

- A level whose Polestar split a crash lost (or left partial) is timed from
  the game's /played at its two starts: Altoholic's saves (played -
  playedThisLevel) and the Recorder's dings (played_s). A save written
  after a ding but answered before it names the new level with the old
  level's start, and is dropped.
- The walk forgets the zone at a logout: a death state at login took the
  previous session's zone.
- flight_nodes keeps the newest list per continent: the flight map lists
  its own continent's nodes only.
- Zone time leaves the time in the air out, and the game's zone text
  flickering back within 3 s is no visit.
(kill_xp summing the kills that say their XP: test_semantic_slice.py.)"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wowstats.analysis import pages, prep, semantic
from wowstats.mcp import tools as T
from wowstats.stores import zone_time, zone_totals

CHAR = "Testgap-Fixture"
T0 = 1794900000.0


def iso(s: float) -> str:
    return datetime.fromtimestamp(T0 + s, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def rec(s: float, kind: str, **f) -> dict:
    return {"_time": iso(s), "char": CHAR, "feed": "wow-recorder", "kind": kind,
            "source_id": f"rec:gap:{int(s)}:{kind}", **f}


def split(s: float, level: int, seconds: int, xp: int, partial: bool) -> dict:
    return {"_time": iso(s), "char": CHAR, "feed": "wow-level", "source_id": f"level:{CHAR}:{level}",
            "level": level, "seconds": seconds, "xp": xp, "quests": 3, "kills": 4, "partial": partial}


def played(s: float, level: int, total: int, in_level: int) -> dict:
    """The shipper's kind=played event (shipper.extract_played)."""
    return {"_time": iso(s), "char": CHAR, "feed": "wow-character", "kind": "played", "source": "altoholic",
            "source_id": f"played:{CHAR}:{total}", "played_total_s": total, "played_level_s": in_level,
            "level": level, "logged_out": True}


# Level 31 split whole; the ding to 33 lost to a crash with level 32's split;
# level 33's split partial (1,901 s of a 12,000 s level); the Recorder's ding
# to 34 carries /played. Level 32 began at /played 100,000, 33 at 110,000,
# 34 at 122,000.
HISTORY = [
    rec(0, "session", level=31), rec(50, "zone", zone="Wetlands"), rec(100, "level", level=32),
    split(100, 31, 9000, 50800, False),
    rec(200, "xp", xp=1000, xp_max=54500), rec(250, "kill", npc_id=1, xp_reward=300),
    played(260, 32, 100430, 430),
    rec(7200, "session", level=32), rec(7205, "zone", zone="Stormwind City"),
    rec(7210, "xp", xp=2000, xp_max=54500), rec(7300, "kill", npc_id=2),
    played(7310, 32, 100516, 516),
    # After the crash: logged in dead, before the zone record.
    rec(20000, "session", level=33), rec(20005, "death"), rec(20010, "zone", zone="Thousand Needles"),
    rec(20100, "xp", xp=500, xp_max=58600),
    played(21000, 33, 113000, 3000),
    rec(24000, "xp", xp=30000, xp_max=58600),
    played(25000, 33, 115000, 5000),
    rec(29900, "xp", xp=58000, xp_max=58600),
    rec(30000, "level", level=34, played_s=122000), split(30000, 33, 1901, 14341, True),
    # Saved after the ding, answered before it: level 34 with level 33's start.
    played(30100, 34, 117100, 7100),
    rec(30200, "xp", xp=100, xp_max=62800), rec(30300, "zone", zone="Dustwallow Marsh"),
]


@pytest.fixture(scope="module")
def db():
    return prep.build([dict(r) for r in HISTORY], CHAR)


def test_a_level_with_no_split_or_a_partial_one_is_timed_from_played(db):
    got = {r[0]: r[1:] for r in db.execute(
        "SELECT level, played_s, xp, partial, played_from, in_play FROM levels ORDER BY level")}
    assert got[31] == (9000.0, 50800, 0, "polestar", 0)
    # 110,000 - 100,000; the save after the ding (110,000 again) never makes level 34 start there.
    assert got[32] == (10000.0, 54500, 0, "altoholic", 0)
    # 122,000 (the Recorder's ding) - 110,000: the whole level, its XP the bar's size, not the split's 14,341.
    assert got[33] == (12000.0, 58600, 0, "altoholic", 0)
    assert got[34] == (None, None, None, None, 1)


def test_a_finished_level_whose_ding_nobody_saw_has_no_silence(db):
    """Level 32's end was lost: its window used to run to the last record,
    over levels 33 and 34, and gave it their longest silence."""
    row = db.execute("SELECT ding_t, calendar_s, longest_gap_s, recorder_missed_s FROM levels "
                     "WHERE level = 32").fetchone()
    assert row == (None, None, None, None)


def test_the_layer_pages_and_levels_tool_read_the_played_time(db):
    layer, ro = semantic.local(db)
    rows = {r["level"]: r for r in T.level_rows(T.Metrics(layer, ro))}
    assert (rows[32]["seconds"], rows[32]["partial"], rows[32]["from"]) == (10000, False, "altoholic")
    assert (rows[33]["seconds"], rows[33]["xp_per_hour"]) == (12000, round(58600 / 12000 * 3600))
    assert "from" not in rows[31]
    # The page: level 32 has no split, so views.build_levels doesn't list it; the layer adds it.
    n = pages._compute(lambda **q: semantic.rows(layer, ro, q))
    listed = [{"level": lv, "seconds": None, "xp": None, "quests": None, "kills": None, "partial": p,
               "at": None, "xp_per_hour": None} for lv, p in ((31, False), (33, True))]
    pages.apply_rows(listed, [], n)
    assert [(lv["level"], lv["seconds"], lv["partial"]) for lv in listed] == [
        (31, 9000, False), (32, 10000, False), (33, 12000, False)]


def test_played_starts_need_two_saves_or_the_ding_before_the_save_before():
    """One save alone may be answered before a ding it names: kept only
    when the ding into its level came before the save before it."""
    rows = [{**played(s, lv, total, in_level), "_t": T0 + s}
            for s, lv, total, in_level in ((100, 32, 50000, 1000), (500, 33, 60000, 2000))]
    assert prep._level_starts(rows, [], {32: T0 + 300}) == {}  # ding into 33 after the save before: maybe stale
    assert prep._level_starts(rows, [], {32: T0 + 50}) == {33: (58000.0, "altoholic")}


def test_the_zone_resets_at_a_logout(db):
    """A death state written at login, before the session's zone record,
    has no zone: it took the last session's (Stormwind City)."""
    assert db.execute("SELECT zone FROM records WHERE kind = 'death'").fetchall() == [(None,)]


def test_known_flight_nodes_keep_each_continents_newest_map():
    rows = [rec(0, "session", level=40),
            rec(10, "flightmaster", taxi_node=2, known_nodes="1;2;3"),
            rec(20, "flightmaster", taxi_node=10, known_nodes="10;11"),
            rec(30, "flightmaster", taxi_node=4, known_nodes="1;2;3;4"),
            rec(40, "pos", map_id=1, x=1, y=1)]
    db = prep.build(rows, CHAR)
    assert [n for (n,) in db.execute("SELECT node_id FROM flight_nodes WHERE known_now = 1 ORDER BY node_id")] == [
        1, 2, 3, 4, 10, 11]


def _z(s: float, zone: str) -> dict:
    return rec(s, "zone", zone=zone)


# A flight from A over B to C, then D flickering to "" and back in 2 s.
FLIGHT = [_z(0, "A"), rec(100, "taxi"), _z(120, "B"), _z(150, "C"), rec(200, "land", seconds=100),
          _z(300, "D"), _z(400, ""), _z(402, "D")]


def test_zone_time_leaves_the_air_and_the_flicker_out():
    got = [(r["zone"], r["secs"], r["visit"]) for r in zone_time(FLIGHT, [(T0, T0 + 1000)])]
    assert got == [("A", 100.0, True), ("B", 0.0, False), ("C", 100.0, True), ("D", 102.0, True),
                   ("", 0.0, False), ("D", 598.0, False)]
    assert zone_totals(zone_time(FLIGHT, [(T0, T0 + 1000)])) == [
        {"zone": "D", "secs": 700, "visits": 1}, {"zone": "A", "secs": 100, "visits": 1},
        {"zone": "C", "secs": 100, "visits": 1}]
    # A flight not landed yet: its end unknown, nothing cut.
    no_land = [r for r in FLIGHT if r["kind"] != "land"]
    assert [r["secs"] for r in zone_time(no_land, [(T0, T0 + 1000)])][:3] == [120.0, 30.0, 150.0]


def test_the_prep_zone_records_and_spans_use_the_same_rule():
    db = prep.build([rec(-10, "session", level=20), *[dict(r) for r in FLIGHT], rec(700, "pos", map_id=1, x=1,
                                                                                   y=1)], CHAR)
    got = db.execute("SELECT zone, seconds, zone_visit FROM records WHERE kind = 'zone' ORDER BY rec_id").fetchall()
    # The flicker's empty zone text reads the zone before it (records.zone: the last zone named).
    assert got == [("A", 100.0, 1), ("B", 0.0, 0), ("C", 100.0, 1), ("D", 102.0, 1), ("D", 0.0, 0),
                   ("D", 298.0, 0)]
    layer, ro = semantic.local(db)
    z = {r["zone"]: r for r in semantic.rows(layer, ro, {"metrics": ["zone_s", "zone_visits"],
                                                       "group_by": ["zone"]})}
    assert (z["D"]["zone_s"], z["D"]["zone_visits"]) == (400, 1)
    assert z["B"]["zone_visits"] == 0
