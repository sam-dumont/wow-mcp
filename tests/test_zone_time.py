"""Time in a zone is the server's, worked out from the stored records
(external review 3, item 2).

The shipper once set a zone record's duration_s from what it had read so
far, then never sent that record again: the first chunk alone gave 5 s, the
whole session in one go 306 s. The same finished history must give the same
facts whatever order its chunks reached the shipper in, so the pages and the
MCP read the time from the records' own times instead (stores.zone_time),
and an old store holding the short value reads right with no rebuild.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests import degraded as D
from wowstats import views as V
from wowstats.analysis import prep
from wowstats.mcp import tools as T
from wowstats.mcp.store import Store as MStore
from wowstats.stores import Store, zone_time, zone_totals

CHAR = "Testzone-Fixture"
T0 = 1794900000


def chunk(seq: int, epoch: int, lines: list[str]) -> tuple[int, list[str]]:
    return epoch, [f"chunk\t2\t{seq}\t{epoch}\tCID", *lines]


# One session: a zone record at T0, a position 5 s later (the end of chunk 1),
# then chunk 2 five minutes on with a position at T0+306.
CHUNK1 = chunk(1, T0, ["session\t0\tCID", "zone\t0\t1429\t4100\t6500\tElwynn Forest\tNorthshire Valley",
                       "pos\t50\t1429\t4200\t6500\t0\t7"])
CHUNK2 = chunk(2, T0 + 300, ["session\t0\tCID", "pos\t60\t1429\t4300\t6600\t0\t7"])


def ship(shipper, schedule: list[dict]) -> list[dict]:
    """Every event the shipper sends over runs that each see `schedule[i]`'s
    chunks (the SavedVariables as they stood then), with its state kept."""
    got: list[dict] = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    state: dict = {}
    for chunks in schedule:
        shipper.extract_recorder(shipper.sessions_of(chunks), {}, {"CID": CHAR}, state, out)
    return [{**e, "component": "wow"} for e in got]


def facts(rows: list[dict]) -> dict:
    vl = D.VL(rows)
    try:
        views = V.Views(Store(vl.url))
        ctx = T.Ctx(store=MStore(vl_url=vl.url), default_char=CHAR, env={})
        rng = {"since": "2026-11-01", "until": "2026-12-31"}
        return {
            "page": views.char(CHAR)["zones"],
            "journal": [s["zones"] for s in views.journal(CHAR)["sessions"]],
            "mcp_zones": [(z["zone"], z["secs"]) for z in T.t_zones(ctx, {"char": CHAR, **rng})["zones"]],
            "mcp_sessions": [s["zones"] for s in T.t_sessions(ctx, {"char": CHAR, **rng})["sessions"]],
        }
    finally:
        vl.close()


@pytest.fixture
def schedules(shipper):
    whole = ship(shipper, [{("CID", 1): CHUNK1, ("CID", 2): CHUNK2}])
    split = ship(shipper, [{("CID", 1): CHUNK1}, {("CID", 1): CHUNK1, ("CID", 2): CHUNK2}])
    return whole, split


def test_the_shipper_still_sends_what_it_saw(schedules):
    # The field stays for servers older than this one; nothing reads it here.
    whole, split = schedules
    z = {e["source_id"]: e["duration_s"] for e in whole if e["kind"] == "zone"}
    z2 = {e["source_id"]: e["duration_s"] for e in split if e["kind"] == "zone"}
    assert z == {"rec:CID:1:2": 306.0} and z2 == {"rec:CID:1:2": 5.0}


def test_same_history_same_zone_facts_whatever_the_chunk_order(schedules):
    whole, split = schedules
    a, b = facts(whole), facts(split)
    assert a == b
    assert a["page"] == [{"zone": "Elwynn Forest", "secs": 306, "visits": 1}]
    assert a["mcp_zones"] == [("Elwynn Forest", 306)]
    assert a["journal"] == [[{"zone": "Elwynn Forest", "secs": 306}]]


def test_an_old_store_with_the_short_value_reads_right(schedules):
    # Rows stored by an older server, duration_s 5 included: no rebuild.
    _, split = schedules
    assert facts(split)["page"][0]["secs"] == 306


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_zone_time_runs_to_the_next_zone_or_the_session_end():
    zones = [{"_time": _iso(T0 + s), "kind": "zone", "zone": z, "source_id": f"rec:1:1:{i}"}
             for i, (s, z) in enumerate([(0, "Durotar"), (100, "The Barrens"), (1000, "Durotar")])]
    # The same zone record sent twice (an older shipper's resend) counts once.
    zones.append({**zones[0], "duration_s": "5"})
    # Two sessions (330 s and more of silence between them), as split_sessions gives them.
    spans = [(T0, T0 + 250), (T0 + 1000, T0 + 1040)]
    got = [(r["zone"], r["secs"]) for r in zone_time(zones, spans)]
    assert got == [("Durotar", 100.0), ("The Barrens", 150.0), ("Durotar", 40.0)]
    assert zone_totals(zone_time(zones, spans)) == [{"zone": "The Barrens", "secs": 150, "visits": 1},
                                                    {"zone": "Durotar", "secs": 140, "visits": 2}]
    # A record outside every session (a range cut short) counts nothing.
    assert zone_time(zones[:1], [(T0 + 10, T0 + 20)])[0]["secs"] == 0.0


def test_the_analysis_zone_spans_use_the_same_time(schedules):
    got = []
    for rows in schedules:
        db = prep.build([dict(r) for r in rows], CHAR)
        got.append((db.execute("SELECT t0, t1, dur_s, detail FROM spans WHERE kind='zone'").fetchall(),
                    db.execute("SELECT seconds FROM records WHERE kind='zone'").fetchall()))
    assert got[0] == got[1]
    assert got[0][0] == [(T0, T0 + 306, 306.0, "Elwynn Forest|Northshire Valley")]
    assert got[0][1] == [(306.0,)]


# Records at the same time are ordered by chunk sequence, then index, as
# numbers (a second review of review 3): text order put rec:aa:1:10 before
# rec:aa:1:2, so the earlier zone record took the time of the later one.
def _rec(t: float, kind: str, sid: str, **kw) -> dict:
    return {"_time": _iso(t), "feed": "wow-recorder", "char": CHAR, "kind": kind, "source_id": sid, **kw}


TIE = [_rec(T0 - 30, "session", "rec:aa:1:1", level=20),
       _rec(T0, "zone", "rec:aa:1:2", zone="Earlier"),
       _rec(T0, "zone", "rec:aa:1:10", zone="Later"),
       _rec(T0 + 60, "pos", "rec:aa:1:11", map_id=1429, x=0.5, y=0.5)]


def test_a_tie_goes_to_the_later_record_by_number_not_text():
    zones = [r for r in TIE if r["kind"] == "zone"]
    assert [(r["zone"], r["secs"]) for r in zone_time(zones, [(T0 - 30, T0 + 60)])] == [("Earlier", 0.0),
                                                                                       ("Later", 60.0)]
    db = prep.build([dict(r) for r in TIE], CHAR)
    assert db.execute("SELECT t0, t1, dur_s, detail FROM spans WHERE kind='zone'").fetchall() == [
        (T0, T0 + 60, 60.0, "Later|")]
    assert db.execute("SELECT zone, seconds FROM records WHERE kind='zone' ORDER BY rec_id").fetchall() == [
        ("Earlier", 0.0), ("Later", 60.0)]


def _digits_chunks() -> dict:
    """Chunk sequences 9 -> 10 and 99 -> 100, record indexes 9 -> 10 (and,
    in chunk 100, 99 -> 100), zone records sharing a time inside a chunk
    and across a chunk roll."""
    def body(n: int, zone_at: dict[int, tuple[int, str]]) -> list[str]:
        out = ["session\t0\tCID"]
        for i in range(2, n + 1):
            if i in zone_at:
                dt, z = zone_at[i]
                out.append(f"zone\t{dt}\t1429\t4100\t6500\t{z}\t")
            else:
                out.append(f"pos\t{min(i * 10, 2990)}\t1429\t4200\t6500\t0\t7")
        return out

    s2 = T0 + 5000  # a later login: its own session
    return {
        # Chunk 9: zones A and B both at +100 s (indexes 9 and 10), C at its
        # last record, +300 s, the same time as chunk 10's first zone D.
        ("CID", 9): chunk(9, T0, body(12, {9: (1000, "Zone A"), 10: (1000, "Zone B"), 12: (3000, "Zone C")})),
        ("CID", 10): chunk(10, T0 + 300, body(11, {2: (0, "Zone D")})),
        ("CID", 99): chunk(99, s2, body(11, {9: (500, "Zone E"), 10: (500, "Zone F")})),
        ("CID", 100): chunk(100, s2 + 300, body(101, {99: (900, "Zone G"), 100: (900, "Zone H")})),
    }


def test_same_facts_across_digit_boundaries_and_equal_times(shipper):
    ch = _digits_chunks()
    order = [("CID", 9), ("CID", 10), ("CID", 99), ("CID", 100)]
    whole = ship(shipper, [ch])
    # One chunk more on each run, as the Recorder writes them.
    split = ship(shipper, [{k: ch[k] for k in order[:n]} for n in range(1, 5)])
    a, b = facts(whole), facts(split)
    assert a == b
    # The later record of each tie gets the time; A, C, E and G none.
    # B: +100 s to C at +300 s. D: +300 s (chunk 10's first) to its last record
    # at +311 s. F: +50 s to G at +90 s of chunk 100 (+390 s). H: to +101 s.
    assert {z["zone"]: z["secs"] for z in a["page"]} == {
        "Zone A": 0, "Zone B": 200, "Zone C": 0, "Zone D": 11, "Zone E": 0, "Zone F": 340, "Zone G": 0, "Zone H": 11}
    got = {z["zone"]: z["secs"] for z in a["page"]}
    spans = []
    for rows in (whole, split):
        db = prep.build([dict(r) for r in rows], CHAR)
        spans.append(db.execute("SELECT detail, dur_s FROM spans WHERE kind='zone' ORDER BY t0, detail").fetchall())
    assert spans[0] == spans[1]
    assert {d.split("|")[0]: s for d, s in spans[0]} == {z: float(s) for z, s in got.items() if s}
