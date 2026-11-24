"""A dungeon quest's hand-in counts toward the run it came from, wherever it
was made (wowstats.dungeonquests, owner 2026-11-24): City of Dalaran's at
the Silverpine grate, the Stockade's in Stormwind and Duskwood."""

import gzip
import json
from pathlib import Path

from wowstats import dungeonquests as dq
from wowstats.analysis import prep, semantic

ROOT = Path(__file__).resolve().parents[2]
CHAR = "Testpriest-Fixture"
STOCKADE = 34
DALARAN = 2959


def test_every_dungeon_has_its_quests_or_says_why():
    """Each dungeon the Dungeons panel lists (kill-xp.json) is in the
    mapping, with at least one quest or the reason it has none."""
    kill = json.loads((ROOT / "data" / "dungeons" / "kill-xp.json").read_text())["dungeons"]
    d = dq.data()["dungeons"]
    names = {x["name"] for x in d.values()}
    assert set(kill) == names
    for m, x in d.items():
        n = sum(1 for v in dq.data()["quests"].values() for mm, _ in v if str(mm) == m)
        assert n == x["quests"]
        assert n >= 1 or x.get("none"), f"{x['name']}: no quest and no reason"
        assert not (n and x.get("none"))


def test_forever_dungeons_are_covered():
    """City of Dalaran's quests (the case that started it), and Forever's
    other new dungeons, from Forever's own data, never Classic's."""
    for q in (92456, 92457, 92458, 92489):
        assert dq.dungeons_of(q) == [DALARAN]
    assert "route" in dq.sources_of(92458) and "zone" in dq.sources_of(92458)
    # Dalaran's city quests are zoned in the same area: Polestar's journal takes them off.
    assert dq.dungeons_of(96609) == [] and dq.dungeons_of(92432) == []  # 92432: the lead-in, Silverpine's
    by_name = {x["name"]: x for x in dq.data()["dungeons"].values()}
    for name in ("Ruins of Lordaeron", "The Hall of Thanes", "Excavation Site: Wetlands", "The Drowned City",
                 "Alcaz Prison", "Krol'dok Stronghold", "Blackmaw Hold", "The Deadmines", "Wailing Caverns"):
        assert by_name[name]["quests"] >= 1, name
    assert dq.dungeons_of(95775) == [2970]  # The Drowned City (Horde): Polestar's cited journal


def test_attribution_rule():
    H = 3600
    runs = [(0, DALARAN, 1000, 2000), (1, DALARAN, 5000, 6000), (2, STOCKADE, 3000, 4000)]
    acc = {92458: [500.0], 92457: [500.0], 92456: [6500.0], 377: [100.0]}
    got = dq.attribute(runs, [
        ("outside-after", 2020, 92458),  # 20 s after leaving run 0 (Dalaran's grate)
        ("inside", 1500, 92457),  # handed in inside counts toward that run
        ("nearest", 6100, 92457),  # two runs before it: the nearest wins
        ("next-day", 6000 + 20 * H, 92458),  # within a day of run 1
        ("too-late", 6000 + 25 * H, 92458),  # more than a day: its zone's
        ("picked-after", 7000, 92456),  # accepted after the last run ended
        ("no-run", 900, 92458),  # before any run of its dungeon
        ("other-dungeon", 2500, 377),  # a Stockade quest, no Stockade run before it
        ("not-a-dungeon-quest", 2100, 92432),
    ], acc)
    assert got == {"outside-after": 0, "inside": 0, "nearest": 1, "next-day": 1}


def _stockade(drop_handin_steps=True):
    """The recorded Stockade trip (tests/analysis/stockade_trip.jsonl.gz):
    without its hand-in guide steps it ends at the exit as City of Dalaran's
    did, its six quests handed in outside in Stormwind, Duskwood and the
    Wetlands."""
    raw = gzip.decompress((Path(__file__).parent / "stockade_trip.jsonl.gz").read_bytes()).decode()
    rows = [json.loads(x) for x in raw.splitlines()]
    if drop_handin_steps:
        rows = [r for r in rows if not (r.get("kind") == "step" and "handin" in str(r.get("guide")))]
    return rows


ALL_SIX = 4950 + 5550 + 5550 + 4200 + 4400 + 5800


def test_handins_outside_after_the_run_count_toward_it():
    db = prep.build(_stockade(), CHAR)
    t = db.execute("SELECT xp_quests, quests_handed_in, quests_outside, xp_quests_outside, end_local, xp_inside "
                   "FROM dungeon_trips").fetchone()
    # The trip runs on to the hand-ins (each within 2 hours of the last) and counts them.
    assert t == (ALL_SIX, 6, 6, ALL_SIX, "2026-10-14T19:45:59+02:00", 3188)
    assert db.execute("SELECT COUNT(*) FROM records WHERE kind = 'turnin' AND trip_quest_id = 1").fetchone()[0] == 6
    assert db.execute("SELECT COUNT(*) FROM quests WHERE dungeon_id = 34 AND trip_id = 1").fetchone()[0] == 6
    # The hand-in zones keep none of it as their own quest XP, and nothing is counted twice.
    _, ro = semantic.local(db)
    cols, rows, _ = ro.run("SELECT zone, quest_xp, dungeon_quest_xp, zone_quest_xp FROM per_zone "
                           "WHERE quest_xp > 0 ORDER BY zone")
    assert [r[1:] for r in rows] == [(r[1], r[1], 0) for r in rows]
    assert sum(r[1] for r in rows) == ALL_SIX
    _, rows, _ = ro.run("SELECT trip_xp_quests, trip_quests_outside FROM per_trip")
    assert rows == [(ALL_SIX, 6)]


def test_a_handin_with_no_run_before_it_stays_its_zones():
    """A Stockade quest handed in more than a day after the run: its zone's
    quest XP, the dungeon it belongs to still said (dungeon_quest)."""
    rows = _stockade()
    for r in rows:
        if r.get("kind") == "turnin" and r.get("quest_id") == "378":
            r["_time"] = "2026-10-16T17:45:59.200Z"  # two days later, in the Wetlands
    db = prep.build(rows, CHAR)
    xq, n = db.execute("SELECT xp_quests, quests_handed_in FROM dungeon_trips").fetchone()
    assert (xq, n) == (ALL_SIX - 5800, 5)
    assert db.execute("SELECT trip_quest_id, dungeon_quest FROM records WHERE kind = 'turnin' AND quest_id = 378"
                      ).fetchone() == (None, STOCKADE)
    _, ro = semantic.local(db)
    _, got, _ = ro.run("SELECT zone_quest_xp, dungeon_quest_xp FROM per_zone WHERE zone = 'Wetlands'")
    assert got == [(5800, 0)]


def test_two_runs_of_a_dungeon_the_nearest_before_wins():
    rows = _stockade()
    run = next(r for r in rows if r.get("feed") == "wow-dungeon")
    # A second, short run of the Stockade between the first and the last hand-in.
    rows.append({**run, "_time": "2026-10-14T17:41:00.000Z", "left": "2026-10-14T17:42:00.000Z", "seconds": "60",
                 "xp": "0"})
    db = prep.build(rows, CHAR)
    got = db.execute("SELECT trip_id, xp_quests, quests_handed_in FROM dungeon_trips ORDER BY trip_id").fetchall()
    # 391, 387, 388, 386 were handed in before the second run; 377 (17:43:39) and 378 (17:45:59) after it.
    assert got == [(1, 4950 + 5550 + 5550 + 4200, 4), (2, 4400 + 5800, 2)]
