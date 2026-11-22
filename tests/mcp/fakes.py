"""A fake VictoriaLogs and VictoriaMetrics with canned answers shaped like
the data contract (feeds wow-recorder, wow-level, wow-dungeon,
wow-character, wow-player, wow-prices), plus a tiny server to run them."""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHAR = "Testpriest-Fixture"
ALT = "Twig-Fixture"


def _t(day: int, h: int, m: int = 0, s: int = 0) -> str:
    return f"2026-11-{day:02d}T{h:02d}:{m:02d}:{s:02d}.000Z"


LEVELS = [
    {"_time": _t(14, 20), "level": 10, "seconds": 2400, "xp": 7600, "quests": 9, "kills": 61, "partial": False,
     "source_id": f"level:{CHAR}:10"},
    {"_time": _t(15, 21), "level": 11, "seconds": 3000, "xp": 8800, "quests": 10, "kills": 70, "partial": False,
     "source_id": f"level:{CHAR}:11"},
    {"_time": _t(16, 21), "level": 12, "seconds": 4800, "xp": 10100, "quests": 8, "kills": 95, "partial": False,
     "source_id": f"level:{CHAR}:12"},
    {"_time": _t(17, 20), "level": 13, "seconds": 3300, "xp": 11400, "quests": 12, "kills": 64, "partial": False,
     "source_id": f"level:{CHAR}:13"},
    # The current level, sent twice as it grows: the newest wins.
    {"_time": _t(17, 21), "level": 14, "seconds": 600, "xp": 2000, "quests": 2, "kills": 10, "partial": True,
     "source_id": f"level:{CHAR}:14"},
    {"_time": _t(17, 21, 30), "level": 14, "seconds": 1500, "xp": 5200, "quests": 4, "kills": 22, "partial": True,
     "source_id": f"level:{CHAR}:14"},
]

SNAP = [{"_time": _t(17, 21, 40), "level": 14, "xp": 5200, "xp_max": 12300, "money_copper": 123456,
         "zone": "The Barrens"}]
LAST_REC = [{"_time": _t(17, 21, 35), "kind": "xp", "level": 14, "xp": 5100, "xp_max": 12300, "zone": "The Barrens"}]
COUNTS = [{"kind": "kill", "n": "1234"}, {"kind": "turnin", "n": "87"}]
CHARS = [{"char": CHAR, "last_seen": _t(17, 21, 40)}, {"char": ALT, "last_seen": _t(10, 12)}]

# Two sessions: the 16th 19:00-20:30 and the 17th 20:00-21:40.
BUCKETS = ([{"_time": _t(16, 19, m), "n": "40", "t0": _t(16, 19, m), "t1": _t(16, 19, m)} for m in range(0, 60, 5)]
           + [{"_time": _t(16, 20, m), "n": "30", "t0": _t(16, 20, m), "t1": _t(16, 20, m)} for m in range(0, 30, 5)]
           + [{"_time": _t(17, 20, m), "n": "50", "t0": _t(17, 20, m), "t1": _t(17, 20, m)} for m in range(0, 60, 5)]
           + [{"_time": _t(17, 21, m), "n": "45", "t0": _t(17, 21, m), "t1": _t(17, 21, m)} for m in range(0, 40, 5)])

EVENTS = [
    # The chunk's session record: the player's level (a kill's is the creature's).
    {"_time": _t(16, 19, 0), "kind": "session", "level": 12, "source_id": "rec:1:1:0"},
    {"_time": _t(16, 19, 2), "kind": "zone", "zone": "Durotar", "duration_s": 1800, "source_id": "rec:1:1:1"},
    {"_time": _t(16, 19, 10), "kind": "step", "guide": "Durotar 10-12", "step": 4, "duration_s": 300,
     "zone": "Durotar", "source_id": "rec:1:1:2"},
    {"_time": _t(16, 19, 20), "kind": "kill", "zone": "Durotar", "level": 12, "source_id": "rec:1:1:3"},
    {"_time": _t(16, 19, 21), "kind": "xp", "zone": "Durotar", "level": 12, "xp_gained": 120, "source_id": "rec:1:1:4"},
    {"_time": _t(16, 19, 40), "kind": "turnin", "zone": "Durotar", "quest_id": 1884, "level": 12,
     "source_id": "rec:1:1:5"},
    {"_time": _t(16, 19, 41), "kind": "xp", "zone": "Durotar", "level": 12, "xp_gained": 850, "source_id": "rec:1:1:6"},
    {"_time": _t(16, 20, 1), "kind": "death", "zone": "Durotar", "level": 12, "source_id": "rec:1:1:7"},
    {"_time": _t(16, 20, 5), "kind": "taxi", "zone": "Durotar", "duration_s": 240, "source_id": "rec:1:1:8"},
    {"_time": _t(17, 20, 3), "kind": "zone", "zone": "The Barrens", "duration_s": 5400, "source_id": "rec:1:2:1"},
    {"_time": _t(17, 20, 10), "kind": "step", "guide": "Barrens 12-15", "step": 1, "duration_s": 200,
     "zone": "The Barrens", "source_id": "rec:1:2:2"},
    {"_time": _t(17, 20, 30), "kind": "step", "guide": "Barrens 12-15", "step": 2, "duration_s": 1200,
     "zone": "The Barrens", "source_id": "rec:1:2:3"},
    {"_time": _t(17, 20, 50), "kind": "step", "guide": "Barrens 12-15", "step": 6, "duration_s": 400,
     "zone": "The Barrens", "source_id": "rec:1:2:4"},
    {"_time": _t(17, 21, 0), "kind": "level", "level": 14, "zone": "The Barrens", "source_id": "rec:1:2:5"},
    {"_time": _t(17, 21, 5), "kind": "xp", "zone": "The Barrens", "level": 14, "xp_gained": 900,
     "source_id": "rec:1:2:6"},
]
STEPS = [e for e in EVENTS if e["kind"] in ("step", "skip")] + [
    {"_time": _t(17, 21, 10), "kind": "skip", "guide": "Barrens 12-15", "step": 7, "zone": "The Barrens",
     "source_id": "rec:1:2:9"}]
# The zone records: the server works their time out from these and the
# sessions (stores.zone_time): Durotar 19:02 to the session's end at 20:25
# (4980 s), The Barrens 20:03 to 21:35 (5520 s).
ZONE_RECS = [e for e in EVENTS if e["kind"] == "zone"]
ZONE_ACT = [{"zone": "Durotar", "kind": "xp", "n": "2", "xp": "970"}, {"zone": "Durotar", "kind": "kill", "n": "1"},
            {"zone": "Durotar", "kind": "death", "n": "1"}, {"zone": "The Barrens", "kind": "xp", "n": "1",
                                                              "xp": "900"}]
DEATHS = [{"_time": _t(16, 20, 1), "zone": "Durotar", "level": 12, "_msg": "death|12|Durotar|Bristleback",
           "source_id": "rec:1:1:7"},
          {"_time": _t(15, 18, 0), "zone": "Durotar", "level": 11, "_msg": "death|11", "source_id": "rec:1:0:1"}]
DUNGEONS = [{"_time": _t(17, 20, 20), "name": "Ragefire Chasm", "dungeon_id": 389, "level": 14, "seconds": 1800,
             "xp": 4500, "source_id": f"dungeon:{CHAR}:1"}]
MET = [{"_time": _t(17, 20, 25), "player": "Thrall-Fixture", "context": "dungeon"},
       {"_time": _t(17, 20, 26), "player": "Jaina-Fixture", "context": "seen"}]
PLAYER_DAYS = [{"player": "Thrall-Fixture", "context": "grouped", "_time": "2026-11-16T00:00:00Z", "n": "3"},
               {"player": "Thrall-Fixture", "context": "dungeon", "_time": "2026-11-17T00:00:00Z", "n": "1"},
               {"player": "Jaina-Fixture", "context": "seen", "_time": "2026-11-17T00:00:00Z", "n": "1"},
               {"player": CHAR, "context": "seen", "_time": "2026-11-17T00:00:00Z", "n": "9"}]
PLAYER_INFO = [{"player": "Thrall-Fixture", "last_seen": _t(17, 20, 25), "level": "15", "classes": '["Shaman"]',
                "zones": '["The Barrens","Ragefire Chasm"]'}]
PRICES = [{"_time": _t(10, 12), "item_name": "Linen Cloth", "item_id": 2589, "item_key": "2589", "price_copper": 15,
           "price_high_copper": 30, "rev": 0, "source_id": "prices:Fixture:2589:2026-11-10"},
          {"_time": _t(17, 12), "item_name": "Linen Cloth", "item_id": 2589, "item_key": "2589", "price_copper": 25,
           "rev": 0, "source_id": "prices:Fixture:2589:2026-11-17"},
          # the same day again, its numbers moved: the higher rev wins
          {"_time": _t(17, 12), "item_name": "Linen Cloth", "item_id": 2589, "item_key": "2589", "price_copper": 22,
           "price_high_copper": 40, "rev": 1, "source_id": "prices:Fixture:2589:2026-11-17"},
          {"_time": _t(17, 12), "item_name": "Copper Ore", "item_id": 2770, "item_key": "2770", "price_copper": 90,
           "rev": 0, "source_id": "prices:Fixture:2770:2026-11-17"}]

# The character sheet: a level 14 Troll Priest in starter gear, one quest
# handed in (its reward is gone).
GEAR = [{"_time": _t(17, 21, 40), "class": "PRIEST", "race": "Troll", "faction": "Horde", "spec": "Shadow",
         "polestar_spec": "auto", "gear_adjust": "[]",
         "items": json.dumps([{"slot": 5, "item_id": 6098, "name": "Neophyte's Robe"},
                              {"slot": 16, "item_id": 36, "name": "Worn Mace"}]),
         "professions": json.dumps([{"name": "Tailoring", "rank": 60}])}]
DONE = [{"quest_id": "1884", "n": "1"}]
# The bags and the bank (wow-bags, IDs only, lists as stored): Adept's Gloves
# (a vendor item, no source in Polestar's data) in the bags, Seer's Cuffs in
# the bank, the Hearthstone (not gear).
BAGS = [{"_time": _t(17, 21, 40), "feed": "wow-bags", "bags": json.dumps([4768, 6948]), "bank": json.dumps([3645])}]

# What the semantic layer's narrow prep reads (wowstats.analysis.pages
# ROWS_QUERY): the same splits, runs and deaths, each with its feed.
LAYER = ([{**r, "feed": "wow-level", "char": CHAR} for r in LEVELS]
         + [{**r, "feed": "wow-dungeon", "char": CHAR} for r in DUNGEONS]
         + [{**r, "feed": "wow-recorder", "kind": "death", "char": CHAR} for r in DEATHS])

# (substring, rows): the first match answers. Specific before general.
LOGS = [
    ("feed:in(wow-level, wow-dungeon, wow-recorder", LAYER),
    ('feed:="wow-bags"', BAGS),
    ('feed:="wow-gear"', GEAR),
    ("kind:=turnin quest_id:*", DONE),
    ("stats by (char) max(_time) as last_seen", CHARS),
    ('feed:="wow-character"', SNAP),
    ("kind:in(xp, level, zone) | fields", LAST_REC),
    ("kind:in(kill, turnin) | uniq", COUNTS),
    ('feed:="wow-level"', LEVELS),
    ("min(_time) as t0", BUCKETS),
    ("kind:in(zone, step, skip, kill", EVENTS),
    ('feed:="wow-dungeon"', DUNGEONS),
    ("stats by (player, context, _time:1d)", PLAYER_DAYS),
    ("stats by (player) max(_time)", PLAYER_INFO),
    ("fields _time, player, context", MET),
    ("kind:=zone | fields", ZONE_RECS),
    ("kind:in(xp, kill, turnin, death) | uniq", ZONE_ACT),
    ("kind:in(step, skip)", STEPS),
    ("kind:=death", DEATHS),
    ('feed:="wow-prices"', PRICES),
]


def _vec(metric: dict, v: float) -> dict:
    return {"metric": metric, "value": [1763400000, str(v)]}


METRICS = [
    ("offset 7d", [_vec({"game": "retail"}, 250000)]),
    ("wow_token_price_gold", [_vec({"game": "retail"}, 251234.6)]),
    ("wow_realm_up", [_vec({"realm": "Forever", "ruleset_id": "2"}, 1)]),
]


class FakeStores:
    """Both stores on one port: /select/logsql/query and /api/v1/query."""

    def __init__(self, logs=None, metrics=None):
        self.logs_rules = logs if logs is not None else LOGS
        self.metric_rules = metrics if metrics is not None else METRICS
        self.queries: list[dict] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                fake.queries.append({"path": u.path, **q})
                if u.path == "/select/logsql/query":
                    rows = next((r for s, r in fake.logs_rules if s in q.get("query", "")), [])
                    body = "".join(json.dumps(r) + "\n" for r in rows).encode()
                    ctype = "application/stream+json"
                elif u.path == "/api/v1/query":
                    res = next((r for s, r in fake.metric_rules if s in q.get("query", "")), [])
                    body = json.dumps({"status": "success", "data": {"resultType": "vector", "result": res}}).encode()
                    ctype = "application/json"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        # A backlog over the default 5: the Overview's rules (next_moves,
        # sanity_check) send their queries 8 at a time, like the web page.
        server = type("Server", (ThreadingHTTPServer,), {"request_queue_size": 64})
        self.httpd = server(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def log_queries(self) -> list[str]:
        return [q.get("query", "") for q in self.queries if q["path"] == "/select/logsql/query"]
