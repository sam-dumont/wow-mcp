"""MCP answers an outside review (2026-11-19) found wrong, each reproduced
here first: a session's level range mixed in creature and quest levels,
character_summary counted deaths another way than `deaths`, XP per hour was
truncated. Fixtures for a low-level hunter, not only the priest."""

from __future__ import annotations

import unittest

from tests.mcp import fakes
from wowstats.mcp import tools
from wowstats.mcp.store import Store

HUNTER = "Testhunter-Stonehand"


def _t(h: int, m: int = 0) -> str:
    return f"2026-11-18T{h:02d}:{m:02d}:00.000Z"


# One session, 19:00-19:45, of a level 9 hunter who dings 10 in it. Kills of
# a level 3 boar and a level 14 elite, a quest of level 7: none of those is
# the player's level.
H_BUCKETS = [{"_time": _t(19, m), "n": "20", "t0": _t(19, m), "t1": _t(19, m)} for m in range(0, 45, 5)]
H_EVENTS = [
    {"_time": _t(19, 0), "kind": "session", "level": 9, "class": "HUNTER", "source_id": "rec:9:1:1"},
    {"_time": _t(19, 5), "kind": "kill", "level": 3, "zone": "Dun Morogh", "source_id": "rec:9:1:2"},
    {"_time": _t(19, 10), "kind": "kill", "level": 14, "zone": "Dun Morogh", "source_id": "rec:9:1:3"},
    {"_time": _t(19, 15), "kind": "turnin", "level": 7, "quest_id": 313, "zone": "Dun Morogh",
     "source_id": "rec:9:1:4"},
    {"_time": _t(19, 20), "kind": "level", "level": 10, "zone": "Dun Morogh", "source_id": "rec:9:1:5"},
    {"_time": _t(19, 25), "kind": "accept", "level": 12, "quest_id": 400, "zone": "Dun Morogh",
     "source_id": "rec:9:1:6"},
]
# Two deaths stored without a source_id (an older shipper, a hand-made
# event): `deaths` counts them, count_uniq(source_id) gave 0.
H_DEATHS = [{"_time": _t(19, 12), "zone": "Dun Morogh", "level": 9, "_msg": "death|9"},
            {"_time": _t(19, 30), "zone": "Dun Morogh", "level": 10, "_msg": "death|10"}]
H_COUNTS = [{"kind": "kill", "n": "2"}, {"kind": "turnin", "n": "1"}]
# 1000 XP in 7 s, 2000 in 13 s, 1500 in 11 s: 514285.7, 553846.2, 490909.1 XP/h.
H_LEVELS = [
    {"_time": _t(18, 1), "level": 7, "seconds": 7, "xp": 1000, "partial": False, "source_id": f"level:{HUNTER}:7"},
    {"_time": _t(18, 2), "level": 8, "seconds": 13, "xp": 2000, "partial": False, "source_id": f"level:{HUNTER}:8"},
    {"_time": _t(19, 20), "level": 9, "seconds": 11, "xp": 1500, "partial": False, "source_id": f"level:{HUNTER}:9"},
]
H_SNAP = [{"_time": _t(19, 40), "level": 10, "xp": 100, "xp_max": 7600, "money_copper": 1234, "zone": "Dun Morogh"}]
# A zone record 7 s before the session's last record (19:40): 7 s there
# (stores.zone_time).
H_ZONES = [{"_time": "2026-11-18T19:39:53.000Z", "kind": "zone", "zone": "Dun Morogh", "source_id": "rec:9:1:9"}]
H_ZONE_ACT = [{"zone": "Dun Morogh", "kind": "xp", "n": "1", "xp": "1000"}]
H_DUNGEONS = [{"_time": _t(18, 30), "name": "The Deadmines", "level": 18, "seconds": 7, "xp": 1000,
               "source_id": f"dungeon:{HUNTER}:1"}]
# The same events raw, for the prep behind the semantic layer (levels,
# zones, sessions, the summary): the XP bar's last reading, 1000 XP in Dun
# Morogh at 19:40, ends the session 7 s after the zone record.
H_XP = [{"_time": _t(19, 40), "kind": "xp", "level": 10, "xp": 100, "xp_max": 7600, "xp_gained": 1000,
         "zone": "Dun Morogh", "source_id": "rec:9:1:10"}]
# Position records every 5 minutes keep it one session (H_BUCKETS).
H_POS = [{"_time": _t(19, m), "kind": "pos", "source_id": f"rec:9:1:{20 + m}"} for m in range(0, 45, 5)]
RAW = ([{**r, "feed": "wow-recorder"} for r in H_EVENTS + H_ZONES + H_XP + H_POS]
       + [{**r, "feed": "wow-recorder", "kind": "death"} for r in H_DEATHS]
       + [{**r, "feed": "wow-level"} for r in H_LEVELS] + [{**r, "feed": "wow-dungeon"} for r in H_DUNGEONS]
       + [{**r, "feed": "wow-character"} for r in H_SNAP])

# The semantic layer's narrow prep (wowstats.analysis.pages ROWS_QUERY):
# the same session, splits, run and deaths, each with its feed.
H_LAYER = ([{**r, "feed": "wow-recorder", "char": HUNTER} for r in H_EVENTS if r["kind"] in ("session", "level")]
           + [{**r, "feed": "wow-level", "char": HUNTER} for r in H_LEVELS]
           + [{**r, "feed": "wow-dungeon", "char": HUNTER} for r in H_DUNGEONS]
           + [{**r, "feed": "wow-recorder", "kind": "death", "char": HUNTER} for r in H_DEATHS])

LOGS = [
    ("feed:in(wow-level, wow-dungeon, wow-recorder", H_LAYER),
    ("stats by (char) max(_time) as last_seen", [{"char": HUNTER, "last_seen": _t(19, 40)}]),
    ('feed:="wow-character"', H_SNAP),
    ("kind:in(xp, level, zone) | fields", []),
    ("kind:in(kill, turnin) | uniq", H_COUNTS),
    ('feed:="wow-level"', H_LEVELS),
    ("min(_time) as t0", H_BUCKETS),
    ("kind:in(zone, step, skip, kill", H_EVENTS),
    ('feed:="wow-dungeon"', H_DUNGEONS),
    ("fields _time, player, context", []),
    ("kind:=zone | fields", H_ZONES),
    ("kind:in(xp, kill, turnin, death) | uniq", H_ZONE_ACT),
    ("kind:=death", H_DEATHS),
]


class HunterAnswers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fakes.FakeStores(logs=LOGS, metrics=[], raw=RAW, char=HUNTER)
        cls.ctx = tools.Ctx(store=Store(vl_url=cls.fake.url))

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def call(self, name, **args):
        return tools.call(self.ctx, name, {"char": HUNTER, **args})

    def test_session_levels_are_the_players_own(self):
        out = self.call("sessions", since="2026-11-18T00:00:00Z", until="2026-11-19T00:00:00Z")
        self.assertEqual(len(out["sessions"]), 1)
        self.assertEqual(out["sessions"][0]["levels"], "9->10")

    def test_summary_deaths_agree_with_deaths(self):
        summary = self.call("character_summary")
        deaths = self.call("deaths")
        self.assertEqual(deaths["total"], 2)
        self.assertEqual(summary["deaths"], deaths["total"])

    def test_xp_per_hour_is_rounded_everywhere(self):
        lv = {x["level"]: x for x in self.call("levels")["levels"]}
        self.assertEqual(lv[7]["xp_per_hour"], 514286)
        self.assertEqual(lv[8]["xp_per_hour"], 553846)
        # 4500 XP in 31 s over the last 3 levels: 522580.6
        self.assertEqual(self.call("character_summary")["xp_per_hour_last_3_levels"], 522581)
        self.assertEqual(self.call("zones")["zones"][0]["xp_per_hour"], 514286)
        self.assertEqual(self.call("dungeons")["runs"][0]["xp_per_hour"], 514286)


class TestpriestDeaths(unittest.TestCase):
    """The priest's fixture: the summary and `deaths` read the same rows."""

    @classmethod
    def setUpClass(cls):
        cls.fake = fakes.FakeStores()
        cls.ctx = tools.Ctx(store=Store(vl_url=cls.fake.url, vm_url=cls.fake.url))

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def test_same_count(self):
        s = tools.call(self.ctx, "character_summary", {"char": fakes.CHAR})
        d = tools.call(self.ctx, "deaths", {"char": fakes.CHAR})
        self.assertEqual(s["deaths"], d["total"])


if __name__ == "__main__":
    unittest.main()
