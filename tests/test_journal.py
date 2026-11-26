from datetime import UTC, datetime

from wowstats import journal
from wowstats.stores import SESSION_GAP_S, split_sessions


def t(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp()


def z(s: str) -> str:
    return s + "Z"


def bucket(first: str, last: str, n: int = 5) -> dict:
    """One SESSION_BUCKETS row: a minute with its first and last record."""
    return {"_time": z(first[:16] + ":00"), "n": str(n), "t0": z(first), "t1": z(last)}


def test_a_silence_over_330_s_ends_a_session_and_nothing_else_does():
    rows = [bucket("2026-11-17T10:00:05", "2026-11-17T10:00:50"),
            bucket("2026-11-17T10:06:20", "2026-11-17T10:06:30"),  # 330 s after 10:00:50: same session
            bucket("2026-11-17T10:12:01", "2026-11-17T10:12:01"),  # 331 s after 10:06:30: a new one
            bucket("2026-11-17T10:13:00", "2026-11-17T10:13:40"),  # a login here (a relog): same session
            bucket("2026-11-17T10:14:00", "2026-11-17T10:14:10", n=0)]  # no record: ignored
    assert split_sessions(rows) == [(t("2026-11-17T10:00:05"), t("2026-11-17T10:06:30")),
                                    (t("2026-11-17T10:12:01"), t("2026-11-17T10:13:40"))]
    assert journal.GAP_S == SESSION_GAP_S == 330


def test_build_session_entry():
    mins = [bucket(f"2026-11-17T10:{m:02d}:00", f"2026-11-17T10:{m:02d}:59") for m in range(0, 50)]
    events = [
        {"_time": z("2026-11-17T10:00:01"), "kind": "session", "level": "30"},
        {"_time": z("2026-11-17T10:01:00"), "kind": "zone", "zone": "Wetlands", "duration_s": "600"},
        {"_time": z("2026-11-17T10:11:00"), "kind": "zone", "zone": "Arathi Highlands", "duration_s": "99999"},
        {"_time": z("2026-11-17T10:20:00"), "kind": "turnin", "quest_id": "631", "quest_name": "The Thandol Span"},
        {"_time": z("2026-11-17T10:30:00"), "kind": "level", "level": "31"},
        {"_time": z("2026-11-17T10:35:00"), "kind": "death", "zone": "Arathi Highlands"},
    ]
    snaps = [{"_time": z("2026-11-16T20:00:00"), "money_copper": "1000"},
             {"_time": z("2026-11-17T10:52:00"), "money_copper": "3500"}]
    players = [{"_time": z("2026-11-17T10:05:00"), "player": "Some-One", "context": "grouped"},
               {"_time": z("2026-11-17T10:06:00"), "player": "Hero-Test", "context": "seen"}]
    dungeons = [{"_time": z("2026-11-17T10:40:00"), "name": "Stockade", "level": "30", "seconds": "600"}]
    xp = [{"_time": z("2026-11-17T10:10:00"), "xp": "1500"}]
    [s] = journal.build(mins, xp, events, dungeons, players, snaps, own="Hero-Test")
    assert s["start"] == "2026-11-17T10:00:00Z" and s["end"] == "2026-11-17T10:49:59Z"
    assert s["played_s"] == 2999
    assert (s["level_start"], s["level_end"], s["levels_gained"]) == (30, 31, 1)
    assert s["zones"][0] == {"zone": "Arathi Highlands", "secs": 2339}  # clipped to the last record
    assert s["quests"][0]["name"] == "The Thandol Span"
    assert s["deaths"]["total"] == 1
    assert s["dungeons"][0]["name"] == "Stockade"
    assert [p["player"] for p in s["players"]] == ["Some-One"]  # never the character itself
    assert s["gold_delta_copper"] == 2500
    assert s["xp"] == 1500


def test_money_across_two_sessions_needs_a_reading_between_them():
    mins = [bucket(f"2026-11-17T10:{m:02d}:00", f"2026-11-17T10:{m:02d}:00")
            for m in (*range(11), *range(17, 26))]
    def snap(time, copper):
        return {"_time": z("2026-11-17T" + time), "money_copper": str(copper)}

    snaps = [snap("09:59:00", 1000), snap("10:25:00", 6000)]
    def deltas():
        return [s["gold_delta_copper"] for s in journal.build(mins, [], [], [], [], snaps)]

    assert deltas() == [None, None]
    snaps.append(snap("10:11:00", 3000))
    assert deltas() == [3000, 2000]


def test_a_sessions_first_reading_keeps_its_xp():
    """XP comes one row per reading at its own time (prep.xp_gains), the
    sessions to the second: a reading on a session's first record counts
    (0.7.0's A/B check found a login's XP dropped when it came per minute),
    one between two sessions counts for neither."""
    mins = [bucket("2026-11-19T15:28:42", "2026-11-19T15:28:53", n=9),
            bucket("2026-11-19T15:40:10", "2026-11-19T15:41:30")]
    xp = [{"_time": z("2026-11-19T15:28:42"), "xp": "590"}, {"_time": z("2026-11-19T15:40:10"), "xp": "10"},
          {"_time": z("2026-11-19T15:35:00"), "xp": "7"}]  # between the two: no session's
    new, old = journal.build(mins, xp, [], [], [], [])
    assert (old["start"], old["xp"]) == ("2026-11-19T15:28:42Z", 590)
    assert new["xp"] == 10
