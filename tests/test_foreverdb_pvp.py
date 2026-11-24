"""The PvP rank from ForeverDB's armory snapshot (owner, 2026-11-24): rank
gear held is no longer a long shot.

tests/data/foreverdb/ForeverDB.lua: a made-up ForeverDB_DB in the shape the
Forever client wrote with ForeverDB 0.75.1 (a client without UnitPVPRank: no
`rank`, `renown` 0, `dk` -64), plus one character on a client that has the
rank. Every name is invented; the guild roster is filled on purpose and must
never leave the shipper.
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

from wowstats import gear, ingest

DATA = Path(__file__).parent / "data" / "foreverdb"
ROSTER = {"Testpriest-Fixture": {}, "Testhunter-Fixture": {}}


def account(tmp_path) -> Path:
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    (acct / "SavedVariables").mkdir(parents=True)
    shutil.copy(DATA / "ForeverDB.lua", acct / "SavedVariables" / "ForeverDB.lua")
    return acct


def run(shipper, acct, state) -> list[dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        shipper.extract_foreverdb(acct, ROSTER, state, shipper.Out(dry=True))
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def test_the_rank_and_the_forever_track(shipper, tmp_path):
    got = shipper.foreverdb_pvp(account(tmp_path))
    # No UnitPVPRank in the client: the rank is the Renown track; dk is no count.
    assert got["Testpriest-Fixture"] == {"pvp_rank": 0, "rank_from": "renown", "renown": 0, "honorable_kills": 0,
                                         "honor_points": 0, "at": 1795519161}
    assert got["Testhunter-Fixture"] == {"pvp_rank": 3, "rank_from": "rank", "renown": 2, "highest_rank": 3,
                                         "honorable_kills": 212, "honor_points": 4100, "at": 1795600000}


def test_events_roster_only_allowlisted_and_sent_once(shipper, tmp_path):
    acct, state = account(tmp_path), {}
    sent = run(shipper, acct, state)
    assert sorted(e["char"] for e in sent) == ["Testhunter-Fixture", "Testpriest-Fixture"]
    hunter = next(e for e in sent if e["char"] == "Testhunter-Fixture")
    assert hunter["feed"] == "wow-foreverdb" and hunter["kind"] == "pvp" and hunter["source"] == "foreverdb"
    assert hunter["pvp_rank"] == 3 and hunter["_time"] == "2026-11-25T09:46:40.000Z"
    assert set(hunter) <= shipper.EVENT_BASE | shipper.FDB_KEYS
    text = json.dumps(sent)
    for never in ("Made Up Guild", "Strangerfoo", "Strangerbar", "Sergeant", "Warsong", "dk"):
        assert never not in text
    assert run(shipper, acct, state) == []  # unchanged: nothing again
    # /ingest stores it as sent.
    stored = ingest.screen(hunter)
    assert stored["pvp_rank"] == 3 and stored["feed"] == "wow-foreverdb"


def test_foreverdb_is_archived(shipper):
    assert shipper.ARCHIVED_SOURCES.match("Account/ACCT/SavedVariables/ForeverDB.lua")


def test_no_file_no_event(shipper, tmp_path):
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    (acct / "SavedVariables").mkdir(parents=True)
    assert shipper.foreverdb_pvp(acct) == {} and run(shipper, acct, {}) == []


def rows(rank=None, hk=None):
    out = []
    if hk is not None:
        out.append({"feed": "wow-altoholic", "kind": "stats", "_time": "2026-11-21T10:00:00Z", "honorable_kills": str(hk)})
    if rank is not None:
        out += [{"feed": "wow-foreverdb", "kind": "pvp", "_time": "2026-11-20T10:00:00Z", "pvp_rank": "1"},
                {"feed": "wow-foreverdb", "kind": "pvp", "_time": "2026-11-22T10:00:00Z", "pvp_rank": str(rank)}]
    return out


def test_standings_take_the_newest_foreverdb_rank():
    assert gear.standings_of(rows(rank=3, hk=0))[1] == 3  # the game's number wins over the kill count
    assert gear.standings_of(rows(hk=0))[1] == 0
    assert gear.standings_of(rows(hk=12))[1] is None
    assert gear.standings_of(rows(rank=0, hk=12))[1] == 0


def who(rank):
    return gear.Who(cls="PRIEST", level=33, side=2, race_id=8, professions={}, done=set(), zone=None, pvp_rank=rank)


def test_rank_gear_held_is_some_effort_else_says_what_is_missing():
    ref = gear.reference()
    for rank, effort, words in (
            (3, gear.SOME, "PvP: rank 3 reward (you hold rank 3)"),
            (1, gear.LONG_SHOT, "PvP: rank 3 reward (rank 3 needed, you're rank 1)"),
            (0, gear.LONG_SHOT, "PvP: rank 3 reward (rank 3 needed, you have no PvP rank)"),
            (None, gear.LONG_SHOT, "PvP: rank 3 reward (your PvP rank isn't recorded yet)")):
        s = gear._source(ref, {"kind": "pvp", "rank": 3}, who(rank), {})
        assert gear.source_effort(s) == effort
        assert gear.source_text(s) == words
