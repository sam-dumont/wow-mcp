# /// script
# requires-python = ">=3.11"
# ///
"""DEMO DATA: seed a local VictoriaLogs (and optionally VictoriaMetrics) with
made-up events shaped like the shipper's contract (docs/contract.md), to try
the UI without a game client. Every event carries demo=true and the
characters are called Testchar and Testalt: the UI shows a "DEMO DATA"
banner on them, so nobody mistakes this for a real character.

    docker run -d --rm --name vl-dev -p 127.0.0.1:9428:9428 \
        docker.io/victoriametrics/victoria-logs:v1.54.0 -retentionPeriod=100y
    uv run dev/seed.py [--vl URL] [--vm URL] [--metrics]
    uv run dev/seed.py --out events.ndjson   # write the events, send nothing (demo/stack/seed_friend.py)
    VL_URL=http://127.0.0.1:9428 uv run wow-stats ui     # http://127.0.0.1:8080

--ingest URL sends them through a running ui's /ingest instead (user wow, or
INGEST_USER; password INGEST_PASSWORD, from the environment), the shipper's
way: the page lists the characters seconds later, where a write straight to
VictoriaLogs waits for the ui's next check, a minute or more. The compose
kit's demo does that from inside the ui container, which has the password.

(VictoriaLogs' default retention, 7 days, drops older events on arrival.)
Testchar-Demo goes 22 -> 32 over six evenings; Testalt-Demo is a level 11
alt with almost nothing, to see the empty states. Invented numbers.
Three more for the 3D view, dressed in real Classic items: Testhunt-Demo (a
female Night Elf hunter only Syndicator knows, a bow and a staff, one item
the tables don't have), Testgun-Demo (a male Dwarf hunter whose gun and
sword want the same hand, and gloves Forever added that Wowhead's model data
lacks, drawn from Forever's own tables) and Testsky-Demo (a Skyborne priest
whose sex was never saved).
"""

import base64
import json
import os
import random
import sys
import urllib.request
from datetime import UTC, datetime, timedelta

VL = sys.argv[sys.argv.index("--vl") + 1] if "--vl" in sys.argv else "http://127.0.0.1:9428"
VM = sys.argv[sys.argv.index("--vm") + 1] if "--vm" in sys.argv else "http://127.0.0.1:8428"
INGEST = sys.argv[sys.argv.index("--ingest") + 1] if "--ingest" in sys.argv else ""
CHAR = "Testchar-Demo"
ALT = "Testalt-Demo"
rnd = random.Random(1117)
events: list[dict] = []


def ts(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def ev(t: datetime, feed: str, char: str, msg: str, sid: str, **fields) -> None:
    e = {"_time": ts(t), "feed": feed, "char": char, "_msg": msg, "source_id": sid}
    e.update(fields)
    e["demo"] = True
    events.append(e)


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


# Evenings mostly, a long Saturday afternoon (UTC).
sessions = [
    ("2026-11-12T17:40", "2026-11-12T22:55"),
    ("2026-11-13T18:10", "2026-11-13T23:20"),
    ("2026-11-14T12:30", "2026-11-14T18:45"),
    ("2026-11-15T13:15", "2026-11-15T17:30"),
    ("2026-11-16T18:30", "2026-11-16T22:10"),
    ("2026-11-17T17:50", "2026-11-17T21:00"),
]
sessions = [(utc(a), utc(b)) for a, b in sessions]

# level -> (seconds, kills, quests); level 24 is the one the owner quoted.
splits = {
    22: (6420, 96, 9), 23: (7310, 121, 8), 24: (8520, 175, 4), 25: (6980, 88, 11),
    26: (9400, 142, 7), 27: (7720, 64, 13), 28: (10150, 131, 9), 29: (8860, 97, 12),
    30: (11240, 156, 10), 31: (9930, 118, 11),
}
xp_max = {22: 32000, 23: 34600, 24: 37200, 25: 40000, 26: 42800, 27: 45700, 28: 48700,
          29: 51800, 30: 55000, 31: 58300, 32: 61700}
zones_by_level = {
    22: ["Stranglethorn Vale", "Duskwood"], 23: ["Duskwood", "Wetlands"], 24: ["Wetlands", "Hillsbrad Foothills"],
    25: ["Hillsbrad Foothills", "Alterac Mountains"], 26: ["Ashenvale", "Stonetalon Mountains"],
    27: ["Stranglethorn Vale", "Ashenvale"], 28: ["Desolace", "Stranglethorn Vale"],
    29: ["Arathi Highlands", "Stranglethorn Vale"], 30: ["Arathi Highlands", "Desolace"],
    31: ["Thousand Needles", "Stranglethorn Vale"], 32: ["Stranglethorn Vale", "Thousand Needles"],
}
map_ids = {"Stranglethorn Vale": 1434, "Duskwood": 1431, "Wetlands": 1437, "Hillsbrad Foothills": 1424,
           "Alterac Mountains": 1416, "Ashenvale": 1440, "Stonetalon Mountains": 1442, "Desolace": 1443,
           "Arathi Highlands": 1417, "Thousand Needles": 1441, "Menethil Harbor": 1437}
regulars = [  # name, class, race, days they show up on (index into sessions), grouped?
    ("Demoplayer-One", "Warrior", "Orc", [0, 1, 3, 4, 6], True),
    ("Demoplayer-Two", "Priest", "Night Elf", [1, 2, 3, 5], True),
    ("Demoplayer-Three", "Paladin", "Dwarf", [2, 4, 6], False),
    ("Demoplayer-Four", "Rogue", "Gnome", [0, 2, 5, 6], True),
    ("Demoplayer-Five", "Mage", "Human", [3, 6], False),
]
oneoffs = [f"Demopasserby-{n}" for n in ("A", "B", "C", "D", "E", "F", "G", "H")]

def advance(gen_state, seconds):
    """Move the played-time cursor by `seconds`, skipping the gaps between sessions."""
    t, si = gen_state
    left = seconds
    while left > 0:
        a, b = sessions[si]
        room = (b - t).total_seconds()
        if left < room:
            return (t + timedelta(seconds=left), si)
        left -= room
        si += 1
        if si >= len(sessions):
            return (sessions[-1][1], len(sessions) - 1)
        t = sessions[si][0]
    return (t, si)


# A few Auctionator scans (the player's own prices).
for d, a in enumerate(sessions[-3:]):
    for iid, (item, base) in {"2592": ("Wool Cloth", 38), "4306": ("Silk Cloth", 96), "2589": ("Linen Cloth", 12)}.items():
        day = a[0].strftime("%Y-%m-%d")
        lo = int(base * rnd.uniform(0.8, 1.2))
        ev(a[0], "wow-prices", CHAR, f"{item}: lowest {lo} copper on {day}", f"price:Demo:{iid}:{d}",
           rev=1, item_id=int(iid), item_key=iid, item_name=item, price_copper=lo, price_high_copper=lo + 5,
           available=rnd.randint(5, 200), scan_day=day, realm="Demo")

state = (sessions[0][0], 0)
seq = 0
# Polestar's own guide ids (data/route-reference.json): the route and the
# session plan (the MCP's plan_session) know where the character stands.
G2030, G3040 = "forever/alliance/06-wetlands", "forever/alliance/30-40/01-hillsbrad-foothills"
step_no = {G2030: 41, G3040: 1}
money = 41_250  # 4g 12s 50c
deaths_at = {22: 1, 24: 2, 26: 1, 28: 1, 29: 2, 31: 1}
taxi_routes = [("Booty Bay", "Stranglethorn Vale", "Darkshire", "Duskwood", 312),
               ("Menethil Harbor", "Wetlands", "Southshore", "Hillsbrad Foothills", 248),
               ("Southshore", "Hillsbrad Foothills", "Refuge Pointe", "Arathi Highlands", 196),
               ("Astranaar", "Ashenvale", "Stonetalon Peak", "Stonetalon Mountains", 141),
               ("Nijel's Point", "Desolace", "Thalanaar", "Feralas", 233),
               ("Booty Bay", "Stranglethorn Vale", "Stormwind", "Elwynn Forest", 404)]
dungeons = {26: ("Razorfen Kraul", 491, 2140, 7100), 28: ("Scarlet Monastery: Graveyard", 1004, 1830, 9600),
            30: ("Scarlet Monastery: Library", 1004, 2650, 14800), 31: ("Razorfen Downs", 129, 3120, 16900)}


def rec(t, kind, msg, **f):
    global seq
    seq += 1
    ev(t, "wow-recorder", CHAR, msg, f"rec:7:{seq // 500}:{seq % 500}", kind=kind, **f)


ev(state[0], "wow-character", CHAR, f"{CHAR} 22", f"character:{CHAR}:a",
   level=22, xp=1200, xp_max=xp_max[22], money_copper=money, zone="Stranglethorn Vale")

FIRST_LEVEL = 22
dings: list[datetime] = []  # when each level ended, for the session records below
# Per level: its start and the quests handed in (time, id), for the accept records below.
quest_log: list[tuple[int, datetime, list[tuple[datetime, int]]]] = []
for level in range(FIRST_LEVEL, 33):
    secs, kills, quests = splits.get(level, (5400, 74, 5))  # 32: still going
    start_t = state[0]
    zl = zones_by_level[level]
    zone_cut = rnd.uniform(0.4, 0.7)
    # zone records with durations
    z1 = int(secs * zone_cut)
    rec(start_t, "zone", f"zone {zl[0]}", zone=zl[0], map_id=map_ids.get(zl[0], 0), duration_s=z1)
    zt, _ = advance(state, z1)
    rec(zt, "zone", f"zone {zl[1]}", zone=zl[1], map_id=map_ids.get(zl[1], 0), duration_s=secs - z1)
    # guide steps: one every 5-15 min, some steps jumped over
    guide = G2030 if level < 30 else G3040
    t_off = 0
    while True:
        gap = rnd.randint(240, 1100)
        if level in (24, 28) and rnd.random() < 0.15:
            gap = rnd.randint(1500, 2700)  # stuck on a step
        if t_off + gap > secs:
            break
        t_off += gap
        step_no[guide] += 2 if rnd.random() < 0.12 else 1
        tt, _ = advance(state, t_off)
        rec(tt, "step", f"step {guide} {step_no[guide]}", guide=guide, step=step_no[guide], duration_s=gap,
            zone=zl[0] if t_off < z1 else zl[1])
    # kills and quest turn-ins, spread over the level
    for i in range(kills):
        tt, _ = advance(state, int(secs * (i + rnd.random()) / max(kills, 1)))
        # The creature's level and class around his own, from its id (no
        # draw of rnd: the rest of the demo stays the same).
        nid = rnd.randint(600, 4600)
        rec(tt, "kill", "kill", npc_id=nid, zone=zl[0] if tt < zt else zl[1], level=level + nid % 4 - 2,
            classification="e" if nid % 9 == 0 else "n")
    handed: list[tuple[datetime, int]] = []
    for i in range(quests):
        tt, _ = advance(state, int(secs * (i + 0.5) / max(quests, 1)))
        reward = rnd.randint(1400, 3900)
        qid = rnd.randint(200, 1200)
        paid = rnd.randint(800, 4200)  # drawn in the same order as before: the rest of the demo is unchanged
        rec(tt, "turnin", "turnin", quest_id=qid, quest_name=f"Demo quest {qid}", xp_reward=reward,
            money_copper=paid, zone=zl[0] if tt < zt else zl[1])
        money += paid
        handed.append((tt, qid))
    quest_log.append((level, start_t, handed))
    # deaths
    for _ in range(deaths_at.get(level, 0)):
        tt, _ = advance(state, int(secs * rnd.uniform(0.1, 0.9)))
        z = zl[0] if tt < zt else zl[1]
        rec(tt, "death", f"death {z}", zone=z, map_id=map_ids.get(z, 0), level=level,
            x=round(rnd.random(), 3), y=round(rnd.random(), 3))
    # a flight most levels
    if level in (22, 24, 25, 26, 28, 30, 32):
        frm, fz, to, tz, dur = taxi_routes[[22, 24, 25, 26, 28, 30, 32].index(level) % len(taxi_routes)]
        tt, _ = advance(state, int(secs * 0.3))
        rec(tt, "taxi", f"taxi {frm} -> {to}", zone=fz, args=f"{frm}|{to}", duration_s=dur)
        rec(tt + timedelta(seconds=dur), "land", f"land {to}", zone=tz, args=to)
        money -= rnd.randint(80, 260)
    # xp records (one every ~10 min)
    t_off = 0
    prev = 0
    while t_off < secs:
        t_off += 600
        tt, _ = advance(state, min(t_off, secs - 1))
        xp = int(xp_max[level] * min(t_off, secs) / secs) if level < 32 else int(xp_max[32] * 0.62 * min(t_off, secs) / secs)
        rec(tt, "xp", f"xp {xp}/{xp_max[level]}", level=level, xp=xp, xp_max=xp_max[level], xp_gained=xp - prev,
            money_copper=money)
        prev = xp
    # dungeon
    if level in dungeons:
        name, did, dsecs, dxp = dungeons[level]
        tt, _ = advance(state, int(secs * 0.5))
        ev(tt, "wow-dungeon", CHAR, f"{name} {dsecs // 60} min", f"dungeon:{CHAR}:{int(tt.timestamp())}",
           dungeon_id=did, name=name, level=level, seconds=dsecs, xp=dxp)
        rec(tt, "enter", f"enter {name}", zone=name)
        for p in rnd.sample([r for r in regulars if r[4]], 2) + [(rnd.choice(oneoffs), "Hunter", "Troll", [], True)]:
            ev(tt + timedelta(seconds=5), "wow-player", CHAR, f"{p[0]} in {name}",
               f"player:dungeon:{p[0]}:{int(tt.timestamp())}", player=p[0], context="dungeon",
               dungeon=name, zone=name, level=level + rnd.randint(-1, 2), **{"class": p[1]}, race=p[2])
    state = advance(state, secs)
    # Polestar writes a split when a level ends, none for the level in
    # progress; `partial` marks the first one, where tracking began mid-level.
    if level < 32:
        rec(state[0], "level", f"level {level + 1}", level=level + 1)
        dings.append(state[0])
        ev(state[0], "wow-level", CHAR, f"level {level}: {secs // 3600}h{secs % 3600 // 60:02d}",
           f"level:{CHAR}:{level}", level=level, seconds=secs, xp=xp_max[level], quests=quests, kills=kills,
           partial=level == FIRST_LEVEL)
    ev(state[0], "wow-character", CHAR, f"{CHAR} {level}", f"character:{CHAR}:{int(state[0].timestamp())}",
       level=level + (1 if level < 32 else 0), xp=0 if level < 32 else int(xp_max[32] * 0.62),
       xp_max=xp_max[min(level + 1, 32)], money_copper=money, zone=zl[1])

# The shipper marks the first record after a logout. The Recorder's session
# record carries the level (shipper/wow_stats.py, the `session` fields):
# without it the analysis can't tell level 22's first evening from 23's.
for a, _ in sessions:
    ev(a, "wow-recorder", CHAR, "session", f"rec:login:{int(a.timestamp())}", kind="session", login=True,
       level=FIRST_LEVEL + sum(d <= a for d in dings))

# Quest accepts, so the analysis can pair them with the hand-ins: each quest
# is picked up when the one two before it is handed in (the level's start
# for the first two), so two or three are open at once. Level 22's first
# two were accepted before tracking began (no accept: untimed). One quest
# abandoned at 27, one still open at 32. Placed on existing record times
# and drawn without rnd: the rest of the demo stays as it was.
for level, start_t, handed in quest_log:
    for i, (_, qid) in enumerate(handed):
        if level == FIRST_LEVEL and i < 2:
            continue
        at = handed[i - 2][0] if i >= 2 else start_t
        ev(at, "wow-recorder", CHAR, "accept", f"rec:accept:{level}:{i}", kind="accept", quest_id=qid,
           quest_name=f"Demo quest {qid}", zone=zones_by_level[level][0])
    if level == 27 and handed:
        ev(start_t, "wow-recorder", CHAR, "accept", "rec:accept:27:x", kind="accept", quest_id=1999,
           quest_name="Demo quest 1999", zone=zones_by_level[level][0])
        ev(handed[-1][0], "wow-recorder", CHAR, "abandon", "rec:abandon:27:x", kind="abandon", quest_id=1999,
           quest_name="Demo quest 1999", zone=zones_by_level[level][1])
    if level == 32:
        ev(start_t, "wow-recorder", CHAR, "accept", "rec:accept:32:x", kind="accept", quest_id=1998,
           quest_name="Demo quest 1998", zone=zones_by_level[level][0])

# Other players across the sessions.
for i, (a, b) in enumerate(sessions):
    for name, cls, race, days, grouped in regulars:
        if i not in days:
            continue
        for k in range(rnd.randint(2, 6)):
            t = a + (b - a) * rnd.random()
            lvl = 22 + int(10 * (t - sessions[0][0]) / (sessions[-1][1] - sessions[0][0]))
            z = rnd.choice(zones_by_level[min(32, lvl)])
            ctx = "grouped" if grouped and k % 2 == 0 else rnd.choice(["seen", "quest-nearby"])
            ev(t, "wow-player", CHAR, f"{name} {ctx}", f"player:rec:{name}:{int(t.timestamp())}",
               player=name, context=ctx, zone=z, map_id=map_ids.get(z, 0), level=lvl + rnd.randint(-2, 2),
               **{"class": cls}, race=race)
    for name in rnd.sample(oneoffs, 3):
        t = a + (b - a) * rnd.random()
        ev(t, "wow-player", CHAR, f"{name} seen", f"player:probed:{name}:{int(t.timestamp())}",
           player=name, context="seen", zone="Stranglethorn Vale", level=rnd.randint(18, 40))

# The alt: a snapshot and two levels, nothing else.
alt_t = utc("2026-11-10T19:00")
ev(alt_t, "wow-level", ALT, "level 10", f"level:{ALT}:10", level=10, seconds=3120, xp=7600, quests=14,
   kills=61, partial=False)
ev(alt_t + timedelta(minutes=50), "wow-character", ALT, "Testalt 11", f"character:{ALT}:1", level=11,
   xp=2900, xp_max=8800, money_copper=6235, zone="Dun Morogh")

# The 3D view's characters: gear sheets in real Classic item ids.
def gear(t: datetime, char: str, items: list[tuple[int, str, int, str]], **fields) -> None:
    ev(t, "wow-gear", char, f"{char}: {len(items)} items equipped", f"gear:{char}:1",
       items=[{"slot": sl, "slot_name": sn, "item_id": iid, "name": nm, "quality": 2} for sl, sn, iid, nm in items],
       **fields)


g_t = utc("2026-11-12T21:00")
ev(g_t, "wow-character", "Testhunt-Demo", "Testhunt: Syndicator only", "character:Testhunt-Demo:1",
   race="Night Elf", **{"class": "HUNTER"}, sex="female", faction="Alliance", money_copper=31337, source="syndicator")
gear(g_t, "Testhunt-Demo", [
    (3, "Shoulder", 4709, "Forest Leather Mantle"), (5, "Chest", 1489, "Gloomshroud Armor"),
    (6, "Waist", 1215, "Support Girdle"), (7, "Legs", 1934, "Stonemason Trousers"), (8, "Feet", 1121, "Feet of the Lynx"),
    (9, "Wrist", 3202, "Forest Leather Bracers"), (10, "Hands", 1302, "Black Whelp Gloves"),
    (11, "Finger", 1116, "Ring of Pure Silver"), (15, "Back", 2059, "Sentry Cloak"),
    (16, "Main hand", 911, "Ironwood Treebranch"), (18, "Ranged", 3021, "Ranger Bow"),
    (19, "Tabard", 999999, "Tabard of Nowhere")],
    race="Night Elf", sex="female", **{"class": "HUNTER"}, faction="Alliance")
ev(g_t, "wow-character", "Testgun-Demo", "Testgun 24", "character:Testgun-Demo:1", level=24, race="Dwarf",
   **{"class": "HUNTER"}, faction="Alliance", zone="Wetlands")
gear(g_t, "Testgun-Demo", [
    (5, "Chest", 1273, "Forest Chain"), (7, "Legs", 1943, "Goblin Mail Leggings"),
    (10, "Hands", 273034, "Ephemeral Grips"), (16, "Main hand", 935, "Night Watch Shortsword"), (18, "Ranged", 4369, "Deadly Blunderbuss")],
    race="Dwarf", sex="male", **{"class": "HUNTER"}, faction="Alliance")
ev(g_t, "wow-character", "Testsky-Demo", "Testsky: Syndicator only", "character:Testsky-Demo:1",
   race="Skyborne", **{"class": "PRIEST"}, faction="Alliance", source="syndicator")
gear(g_t, "Testsky-Demo", [
    (5, "Chest", 2585, "Gray Woolen Robe"), (16, "Main hand", 880, "Staff of Horrors"),
    (18, "Ranged", 5069, "Fire Wand")],
    race="Skyborne", **{"class": "PRIEST"}, faction="Alliance")

# Combat: each kill's fight from the record before it to the kill, both on
# existing record times (the online time and the sessions stay as they were).
recs = sorted((e for e in events if e["feed"] == "wow-recorder" and e["char"] == CHAR), key=lambda e: e["_time"])
for prev, e in zip(recs, recs[1:], strict=False):
    if e.get("kind") == "kill" and prev["_time"] < e["_time"]:
        k = e["source_id"].split(":", 1)[1]
        events.append({"_time": prev["_time"], "feed": "wow-recorder", "char": CHAR, "_msg": "combat on",
                       "source_id": f"rec:combat1:{k}", "kind": "combat", "on": True, "demo": True})
        events.append({"_time": e["_time"], "feed": "wow-recorder", "char": CHAR, "_msg": "combat off",
                       "source_id": f"rec:combat0:{k}", "kind": "combat", "on": False, "demo": True})

body = "\n".join(json.dumps(e) for e in events).encode()
if "--out" in sys.argv:
    with open(sys.argv[sys.argv.index("--out") + 1], "wb") as f:
        f.write(body + b"\n")
    print(f"{len(events)} events written")
    sys.exit(0)
if INGEST:
    pw = os.environ.get("INGEST_PASSWORD", "")
    if not pw:
        sys.exit("--ingest: set INGEST_PASSWORD (the ui's upload password)")
    auth = "Basic " + base64.b64encode(f"{os.environ.get('INGEST_USER') or 'wow'}:{pw}".encode()).decode()
    lines = body.split(b"\n")
    # /ingest takes 8 MiB a post: 2,000 events are well under it.
    for i in range(0, len(lines), 2000):
        urllib.request.urlopen(urllib.request.Request(
            INGEST, data=b"\n".join(lines[i:i + 2000]) + b"\n", method="POST",
            headers={"Content-Type": "application/x-ndjson", "Authorization": auth}), timeout=60).read()
    print(f"{len(events)} events into {INGEST}")
else:
    req = urllib.request.Request(
        f"{VL}/insert/jsonline?_stream_fields=feed&_time_field=_time&_msg_field=_msg",
        # Without this content type VictoriaLogs answers 200 and stores nothing.
        data=body, method="POST", headers={"Content-Type": "application/stream+json"})
    urllib.request.urlopen(req, timeout=30).read()
    urllib.request.urlopen(urllib.request.Request(f"{VL}/internal/force_flush", data=b"", method="POST"),
                           timeout=30).read()
    print(f"{len(events)} events into {VL}")

if "--metrics" in sys.argv:
    # Shaped like the exporter pushes them: Retail's token (Forever has none),
    # one realm series per ruleset, auction prices per Alliance house. Every
    # 5 minutes for two days, hourly before.
    end = int(datetime.now(UTC).timestamp()) // 300 * 300
    lines = []
    price = 281_400.0
    steps = [end - h * 3600 for h in range(24 * 14, 48, -1)] + [end - 48 * 3600 + i * 300 for i in range(48 * 12 + 1)]
    for t in steps:
        h = (end - t) // 3600
        t *= 1000
        price = max(240_000, price + rnd.uniform(-1800, 2100))
        lines.append(f'wow_token_price_gold{{game="retail"}} {price:.0f} {t}')
        lines.append(f'wow_realm_up{{realm="Normal",ruleset_id="2"}} {0 if h in (30, 31) else 1} {t}')
    urllib.request.urlopen(urllib.request.Request(f"{VM}/api/v1/import/prometheus",
                                                  data="\n".join(lines).encode(), method="POST"), timeout=30).read()
    print(f"{len(lines)} samples into {VM}")
