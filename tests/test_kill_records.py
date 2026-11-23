"""Kill records from the Recorder to the analysis database: every field the
addon writes gets through. What it leaves empty (the rested split on this
client; the level, class and position inside an instance, where unit
identity is secret) stays empty: the shipper doesn't make it up."""

import gzip
import json
from pathlib import Path

from wowstats.analysis import prep

# Polestar_Recorder 0.4.1.1 ns.RecAt(t, "kill", npc, lvl, cls, P, xp, rested, tapped, quests).
OUTSIDE_RESTED = "kill\t812\t1711\t25\te\t225\t4926\t4813\t208\t104\t0\t387:1"
INSIDE_TODAY = "kill\t1290\t1708\t\t\t\t\t\t39\t\t\t"  # an instance kill as the Recorder writes it now


def test_the_shipper_keeps_every_kill_field(shipper):
    f = OUTSIDE_RESTED.split("\t")
    assert shipper.parse_record(f[0], f[2:]) == {
        "npc_id": 1711, "level": 25, "classification": "e", "map_id": 225, "x": 49.26, "y": 48.13,
        "xp_reward": 208, "rested_bonus": 104, "tapped": False, "quests": "387:1"}
    f = INSIDE_TODAY.split("\t")
    assert shipper.parse_record(f[0], f[2:]) == {"npc_id": 1708, "xp_reward": 39}
    assert {"rested_bonus", "level", "xp_reward"} <= shipper.rec_keys("kill")


def test_prep_keeps_the_rested_split_and_the_kills_level():
    raw = gzip.decompress((Path(__file__).parent / "analysis" / "stockade_trip.jsonl.gz").read_bytes()).decode()
    rows = [json.loads(x) for x in raw.splitlines()]
    kills = [r for r in rows if r.get("kind") == "kill"]
    kills[0]["xp_reward"], kills[0]["rested_bonus"] = "208", "104"
    db = prep.build(rows, "Testpriest-Fixture")
    got = db.execute("SELECT xp_reward, npc_level, json_extract(extra, '$.rested_bonus') FROM records "
                     "WHERE kind = 'kill' ORDER BY t LIMIT 1").fetchone()
    assert got == (208, 25, "104")
    assert db.execute("SELECT COUNT(*) FROM records WHERE kind = 'kill'").fetchone()[0] == len(kills)
