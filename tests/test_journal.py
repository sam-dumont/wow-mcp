from datetime import UTC, datetime

from wowstats import journal


def t(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp()


def z(s: str) -> str:
    return s + "Z"


def minutes(start: str, n: int) -> list[float]:
    return [t(start) + 60 * i for i in range(n)]


def test_split_on_gap_and_login():
    a = minutes("2026-11-17T10:00:00", 30)  # 10:00-10:30
    b = minutes("2026-11-17T10:40:00", 10)  # 10 min later: same session
    c = minutes("2026-11-17T11:30:00", 5)  # 40 min gap: new session
    d = minutes("2026-11-17T11:36:00", 5)  # 1 min gap, but a login: new session
    spans = journal.split(a + b + c + d, logins=[t("2026-11-17T11:36:20")])
    assert spans == [(a[0], b[-1] + 60), (c[0], c[-1] + 60), (d[0], d[-1] + 60)]


def test_build_session_entry():
    mins = [{"_time": z(f"2026-11-17T10:{m:02d}:00"), "n": "5"} for m in range(0, 50)]
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
    [s] = journal.build(mins, xp, events, [], dungeons, players, snaps, own="Hero-Test")
    assert s["start"] == "2026-11-17T10:00:00Z" and s["end"] == "2026-11-17T10:50:00Z"
    assert s["played_s"] == 3000
    assert (s["level_start"], s["level_end"], s["levels_gained"]) == (30, 31, 1)
    assert s["zones"][0] == {"zone": "Arathi Highlands", "secs": 2340}  # clipped to the session end
    assert s["quests"][0]["name"] == "The Thandol Span"
    assert s["deaths"]["total"] == 1
    assert s["dungeons"][0]["name"] == "Stockade"
    assert [p["player"] for p in s["players"]] == ["Some-One"]  # never the character itself
    assert s["gold_delta_copper"] == 2500
    assert s["xp"] == 1500
