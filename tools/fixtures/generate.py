"""Synthetic test fixtures: every recorded-looking fixture under tests/ comes
from here, made up from scratch with a seeded random generator and the
repository's own game data (route-reference, forever-reference, the KB). No
real account, character, guild, host or place of play is in them.

    uv run python tools/fixtures/generate.py          # rewrite the fixtures
    uv run python tools/fixtures/generate.py --check  # fail if they differ
    uv run python tools/fixtures/generate.py --demo events.ndjson [--end-day YYYY-MM-DD]
                                                      # the docs site's demo character (site/)

The output is byte-for-byte reproducible (fixed seed, fixed dates, gzip with
no timestamp). Derived fixtures (the planner's inputs, the gear answer) are
made by the product's own code from the generated events, so a change to
that code can change them: rerun, read the diff, commit both.

What it plays: Testpriest-Fixture, a Gnome priest (Alliance), from level 22
to 32 over fifteen evenings of October 2026 (Europe/Brussels), following
Polestar's levelling guides, with two dungeon trips (Blackfathom Deeps at
23, The Stockade at 31), three professions, deaths, rests, flights and
boats; and Testhunter-Fixture, a level 1 Dwarf hunter who has done nothing.

    tests/data/analysis-events.jsonl.gz   the priest's first four sessions
    tests/analysis/stockade_trip.jsonl.gz the Stockade trip, plus one stray
                                          event of the hunter
    tests/data/plan-inputs.json.gz        wowstats.plan.facts.extract on all
                                          fifteen sessions
    tests/data/plan-inputs-level1.json.gz the same for the hunter
    tests/data/gear-priest.json.gz        the full view's gear answer
    tests/data/altoholic-forever/*.lua,
    tests/data/quests-forever/*.lua       addon saves in the client's layout

The independent check (tests/analysis/test_independent_check.py), on the
first four sessions (analysis-events): 4 sessions from 5 to 8 October,
levels 22 to 26; 5 deaths (2 in Darkshore, 1 in Stonetalon Mountains, 1 in
Blackfathom Deeps, 1 in the Wetlands), the same 5 in character_summary and
in `deaths`; one Blackfathom Deeps run, entered at 23, of BFD_SECONDS =
4,980 s for BFD_XP = 18,211 XP: 18,211 / 4,980 * 3,600 = 13,164.58 XP an
hour, which rounds to 13,165 (truncating would say 13,164). Anyone can
recount those from the events with jq or SQL.

The other numbers the tests pin come out of the run, not out of constants:
rerun, then read them in the failing assertions.

A Polestar release moves the guides' steps, and the story holds anyway: the
steps, their titles and kinds are Polestar's, but each stretch of them lasts
exactly the hours the script gives it, ends at the level and XP the script
gives it (`reach`), dies where the script says, and draws from its own
random streams. So the sessions, their online time, the levels and the
dungeon runs are the same on any Polestar release; only what happens inside
a stretch moves (2026-11-22: Polestar 0.4.2.1 rebuilt its routes and the
generator, then tuned by hand to the old steps, stopped).
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import gzip
import hashlib
import io
import json
import random
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TZ = ZoneInfo("Europe/Brussels")
PRIEST = "Testpriest-Fixture"
HUNTER = "Testhunter-Fixture"
SEED = 20261005
VERBOSE = False  # --levels: each logout's level and XP
# The independent check's dungeon run (first four sessions).
BFD_SECONDS = 4980
BFD_XP = 18211
# A stretch of guide steps ends where the script says (`reach`: level and XP
# into it), whatever steps Polestar's guides hold: kill XP is paced to get
# there. A hand-tuned XP scale per stretch stopped the generator on Polestar
# 0.4.2.1 (2026-11-22), whose rebuilt guides moved the steps under it and
# took the priest to 33 where the script says 32. Each mark sits mid-level:
# the guides' quest XP lands on top of it, and the level must hold.
KILL_S = 75.0  # seconds per kill, pulls and walks between them: the pace's estimate of the kills left
# Classic's XP to the next level.
XP_MAX = {1: 400, 21: 26000, 22: 27300, 23: 29400, 24: 31700, 25: 34000, 26: 36400, 27: 38900, 28: 41400,
          29: 44300, 30: 47400, 31: 50800, 32: 54500, 33: 58600, 34: 62800, 35: 67100}
SESSION_FIELDS = {"build": "70888", "interface": "16001", "locale": "enUS", "polestar_version": "0.4.1",
                  "recorder_version": "0.4.1", "region": "3"}
MAPS = {"Darkshore": 1439, "Ashenvale": 1440, "Stonetalon Mountains": 1442, "Wetlands": 1437, "Ironforge": 1455,
        "Stormwind City": 1453, "Elwynn Forest": 1429, "Duskwood": 1431, "Redridge Mountains": 1433,
        "Westfall": 1436, "Darnassus": 1457, "Teldrassil": 1438, "Hillsbrad Foothills": 1424, "Moonglade": 1450,
        "Blackfathom Deeps": 221, "Stormwind Stockade": 225, "Dun Morogh": 1426,
        # The demo's last week (build_demo): never visited by the fixtures.
        "Alterac Mountains": 1416, "Arathi Highlands": 1417, "Dustwallow Marsh": 1445, "The Barrens": 1413,
        "City of Dalaran": 1416, "Razorfen Kraul": 301}
# A map's frame in world yards (the Recorder's `map` record): what makes
# positions into distances.
FRAMES = {1439: (-16000.0, -19200.0, 7466.6, 1600.0), 1440: (1700.0, -4066.6, 4672.9, 829.1),
          1442: (3245.8, -1637.5, 2916.7, -339.6), 1437: (-389.6, -4525.0, -2147.9, -4904.2),
          1455: (-713.6, -1504.2, -4569.2, -5096.8), 1453: (1722.2, -14.6, -7995.8, -9154.2),
          1429: (1535.4, -1935.4, -7939.6, -10254.2), 1431: (833.3, -1866.7, -9716.7, -11516.7),
          1433: (-1570.8, -3741.7, -8575.0, -10022.9), 1436: (1600.0, -2200.0, -9933.3, -12466.7),
          1457: (2938.4, 1880.0, 10238.3, 9532.6), 1438: (3814.6, -1277.1, 11831.3, 8437.5),
          1424: (1066.7, -2133.3, 400.0, -1733.3), 1450: (-1381.3, -3689.6, 8491.7, 6952.1),
          1426: (1802.1, -3122.9, -3877.1, -7160.4)}
# Areas (forever-reference) of the zones the priest quests in.
AREAS = {"Darkshore": 148, "Ashenvale": 331, "Stonetalon Mountains": 406, "Wetlands": 11, "Duskwood": 10,
         "Redridge Mountains": 44, "Elwynn Forest": 12, "Hillsbrad Foothills": 267, "Westfall": 40,
         "Stormwind City": 1519, "Ironforge": 1537, "Darnassus": 1657, "Teldrassil": 141,
         "Alterac Mountains": 36, "Arathi Highlands": 45}
TAXI = {"Auberdine": 26, "Astranaar": 28, "Stormwind City": 2, "Ironforge": 6, "Menethil Harbor": 7,
        "Darkshire": 12, "Lakeshire": 5, "Sentinel Hill": 4, "Rut'theran Village": 27, "Southshore": 14}
# Professions (skill line IDs) and what levels them.
TAILORING, ENCHANTING, COOKING = 197, 333, 185
CRAFTS = {TAILORING: (2963, 2996), ENCHANTING: (13419, 11082), COOKING: (2540, 2681)}
PROF_NAMES = {TAILORING: "Tailoring", ENCHANTING: "Enchanting", COOKING: "Cooking"}
BFD_MOBS = [4802, 4803, 4807, 4815, 4809, 4810]
BFD_BOSSES = [4887, 4831, 6243, 4832]
STOCKADE_MOBS = [1706, 1711, 1715, 1708]
STOCKADE_BOSSES = [1696, 1666, 1717, 1663, 1716]


def iso(t: float) -> str:
    return datetime.fromtimestamp(round(t, 1), UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def local(y: int, m: int, d: int, h: int, mi: int) -> float:
    return datetime(y, m, d, h, mi, tzinfo=TZ).timestamp()


def sval(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return str(v)


# ---------------------------------------------------------------- game data


class Game:
    """The repository's game data, read once: quests and creatures by area,
    the route's guides and their step kinds."""

    def __init__(self):
        from wowstats.analysis import kb
        from wowstats.mcp import route
        from wowstats.plan import facts

        self.kb = kb.build({"KB_GAME_TABLES": "off"})
        self.ref = route.reference()
        self.facts = facts
        self.npc_level: dict[int, tuple[int, str]] = {}
        self.npc_name: dict[int, str] = {}
        for _id, body in self.kb.db.execute("SELECT id, body FROM docs WHERE kind='npc'"):
            m = re.match(r"NPC (\d+): (.*?)(?: <[^>]*>)?\. Level (\d+)(?:-(\d+))?, ([a-z ]+)\.", body)
            if m:
                n = int(m.group(1))
                self.npc_level[n] = (int(m.group(4) or m.group(3)), "e" if "elite" in m.group(5) else "n")
                self.npc_name[n] = m.group(2)
        self.quests: dict[int, dict] = {}
        for _id, body in self.kb.db.execute("SELECT id, body FROM docs WHERE kind='quest'"):
            m = re.match(r"Quest (\d+): (.*?)\. Level (-?\d+), needs level (\d+), XP (\d+), side (\w+), area (\d+)",
                         body)
            if not m:
                continue
            q = {"id": int(m.group(1)), "name": m.group(2), "level": int(m.group(3)), "min": int(m.group(4)),
                 "xp": int(m.group(5)), "side": m.group(6), "area": int(m.group(7)),
                 "money": int((re.search(r"Money (\d+) copper", body) or [0, 0])[1]),
                 "starts": int((re.search(r"Starts: NPC (\d+)", body) or [0, 0])[1]),
                 "ends": int((re.search(r"Ends: NPC (\d+)", body) or [0, 0])[1]),
                 "kills": [(int(a), int(b)) for a, b in re.findall(r"kill NPC (\d+) [^;.]*? x(\d+)", body)],
                 "items": [(int(a), int(b)) for a, b in re.findall(r"(?<!choice )item (\d+) [^;.]*? x(\d+)", body)],
                 "choices": [int(a) for a in re.findall(r"choice item (\d+)", body)]}
            self.quests[q["id"]] = q

    def zone_quests(self, zone: str, level: int, used: set[int]) -> list[dict]:
        area = AREAS.get(zone)
        return sorted((q for q in self.quests.values() if q["area"] == area and q["side"] in ("Alliance", "both")
                       and level - 4 <= q["level"] <= level + 3 and q["min"] <= level and q["id"] not in used
                       and q["xp"] > 0), key=lambda q: q["id"])

    def zone_mobs(self, zone: str, level: int) -> list[int]:
        area = AREAS.get(zone)
        mobs = sorted({n for q in self.quests.values() if q["area"] == area for n, _ in q["kills"]
                       if n in self.npc_level and self.npc_level[n][1] == "n"
                       and level - 6 <= self.npc_level[n][0] <= level + 2})
        if not mobs:
            mobs = sorted({n for q in self.quests.values() if q["area"] == area for n, _ in q["kills"]
                           if n in self.npc_level})
        return mobs

    def steps(self, guide: str) -> list[tuple[int, float, str, str]]:
        st = self.facts.guide_steps(self.ref, guide)
        kinds = self.facts.kinds_for(guide, st)
        return [(n, p, t, kinds[n]) for n, p, t in st]


# ---------------------------------------------------------------- the player


class Player:
    def __init__(self, game: Game, rnd: random.Random, char: str, *, cls: str, race: str, faction: str, sex: int,
                 level: int, xp: int, money: int, bind: str):
        self.g, self.rnd, self.char = game, rnd, char
        self.walk = random.Random(rnd.random())  # positions: their own stream, so their pace leaves the play alone
        self.who = {"class": cls, "race": race, "faction": faction, "sex": str(sex)}
        self.level, self.xp, self.rested, self.money, self.bind = level, xp, 0, money, bind
        self.t = 0.0
        self.out: list[dict] = []
        self.zone = self.subzone = None
        self.map_id = None
        self.x, self.y = 50.0, 50.0
        self.maps_seen: set[int] = set()
        self.next_chunk = self.next_xp = 0.0
        self.online = False
        self.guide = None
        self.step_t = None
        self.used: set[int] = set()
        self.log: dict[int, dict] = {}
        self.done: list[int] = []
        self.level_t0 = 0.0  # online seconds at the start of this level
        self.played = 0.0
        self.level_kills = self.level_quests = 0
        self.first_level = True
        self.skills = {TAILORING: [100, 150], ENCHANTING: [120, 150], COOKING: [90, 150]}
        self.instance = None
        self.flags = 0  # the pos records' flags (32: dead or a ghost, on the corpse run)
        self.worn: dict[int, int] = {}
        self.packs = "guides:0.4.1;wow-forever-books:1.0.0"
        self.gained = 0  # every XP point, for the dungeon summaries
        self.xp_seen = None  # self.gained at the last xp record
        self.goal: tuple[int, float] | None = None  # a stretch's (self.gained to reach, by when)
        # The demo times each guide step from the session's own start, as
        # Polestar does; the fixtures keep the clock running across logouts
        # (their pinned numbers come from that).
        self.fresh_steps = False

    # -------------------------------------------------------- output

    def rec(self, kind: str, **f) -> dict:
        e = {"_time": iso(self.t), "char": self.char, "component": "wow", "feed": "wow-recorder", "kind": kind,
             "layer": "app"}
        e.update({k: sval(v) for k, v in f.items() if v is not None})
        self.out.append(e)
        return e

    def here(self) -> dict:
        return {"map_id": self.map_id, "x": round(self.x, 2), "y": round(self.y, 2), "zone": self.zone}

    def other(self, feed: str, **f) -> dict:
        e = {"_time": iso(self.t), "char": self.char, "component": "wow", "feed": feed, "layer": "app"}
        e.update({k: (v if isinstance(v, str) else sval(v) if not isinstance(v, (list, dict)) else
                      json.dumps(v, separators=(",", ":"))) for k, v in f.items() if v is not None})
        self.out.append(e)
        return e

    # -------------------------------------------------------- time

    def wait(self, dt: float, moving: bool = True) -> None:
        """Let dt seconds pass: the 5-minute chunk headers and XP records,
        and a position every 4-7 s while moving."""
        end = self.t + dt
        while self.t < end:
            nxt = min(end, self.next_chunk, self.next_xp, self.t + self.walk.uniform(4, 7) if moving else end)
            self.played += nxt - self.t
            self.t = nxt
            if self.t >= self.next_chunk:
                self.chunk()
            if self.t >= self.next_xp:
                self.xp_rec()
            if moving and self.t < end and self.instance != "taxi":
                self.x = min(95.0, max(5.0, self.x + self.walk.uniform(-1.2, 1.2)))
                self.y = min(95.0, max(5.0, self.y + self.walk.uniform(-1.2, 1.2)))
                self.rec("pos", flags=self.flags, map_id=self.map_id, speed=7.0, x=round(self.x, 2), y=round(self.y, 2),
                         zone=self.zone)

    def chunk(self, login: bool = False) -> None:
        self.rec("session", **SESSION_FIELDS, **self.who, level=self.level, login=login,
                 packs=self.packs + (f";guide:{self.guide}" if self.guide else ""),
                 zone=None if login else self.subzone or self.zone)
        self.next_chunk = self.t + 300

    def xp_rec(self) -> None:
        # xp_gained: the change since the previous xp record (none on the first).
        gained = None if self.xp_seen is None else self.gained - self.xp_seen
        self.xp_seen = self.gained
        self.rec("xp", rested_xp=self.rested or None, xp=self.xp, xp_max=XP_MAX[self.level], xp_gained=gained,
                 zone=self.subzone or self.zone)
        self.next_xp = self.t + 300

    # -------------------------------------------------------- places

    def go(self, zone: str, subzone: str | None = None, x: float | None = None, y: float | None = None) -> None:
        m = MAPS[zone]
        if m not in self.maps_seen and m in FRAMES:
            left, right, top, bottom = FRAMES[m]
            self.rec("map", bottom=bottom, continent=0 if m in (1426, 1429, 1431, 1433, 1436, 1437, 1453, 1455,
                                                                1424) else 1, left=left, map_id=m, map_type=3,
                     parent_map_id=1415, right=right, top=top)
            self.maps_seen.add(m)
        self.zone, self.map_id = zone, m
        if x is not None:
            self.x, self.y = x, y
        dur = self.rnd.uniform(2, 40)
        self.subzone = subzone
        self.rec("zone", duration_s=round(dur, 1), map_id=m, subzone=subzone, x=round(self.x, 2), y=round(self.y, 2),
                 zone=zone)

    # -------------------------------------------------------- sessions

    def login(self, t: float, zone: str, subzone: str | None = None) -> None:
        self.t = t
        self.online = True
        self.next_xp = self.t
        if self.fresh_steps:
            self.step_t = None
        self.chunk(login=True)
        self.go(zone, subzone)
        self.rec("group", raid=False, size=0, zone=zone)
        self.xp_rec()

    def logout(self) -> None:
        self.wait(self.rnd.uniform(20, 60), moving=False)
        self.xp_rec()
        self.snapshot()
        self.online = False
        self.guide = None
        self.t += 1
        if VERBOSE:
            print(f"  logout {iso(self.t)} level {self.level} xp {self.xp}", file=sys.stderr)

    def snapshot(self, **extra) -> None:
        self.other("wow-character", level=self.level, xp=self.xp, xp_max=XP_MAX[self.level],
                   money_copper=self.money, zone=self.zone, bind=self.bind,
                   resting=self.zone in ("Stormwind City", "Ironforge", "Darnassus") or self.subzone in (
                       "Auberdine", "Astranaar", "Darkshire", "Lakeshire", "Menethil Harbor", "Southshore"),
                   **self.who, **extra)

    # -------------------------------------------------------- xp

    def gain(self, xp: int, kill: bool = False) -> int:
        bonus = 0
        if kill and self.rested > 0:
            bonus = min(xp, self.rested)
            self.rested -= bonus
        got = xp + bonus
        self.xp += got
        self.gained += got
        while self.xp >= XP_MAX[self.level]:
            self.xp -= XP_MAX[self.level]
            self.ding()
        return got

    def ding(self) -> None:
        secs = round(self.played - self.level_t0)
        self.other("wow-level", kills=self.level_kills, level=self.level, partial=self.first_level,
                   quests=self.level_quests, seconds=secs,
                   xp=XP_MAX[self.level] if not self.first_level else XP_MAX[self.level] - 15555)
        self.level += 1
        self.first_level = False
        self.level_t0 = self.played
        self.level_kills = self.level_quests = 0
        self.rec("level", level=self.level, **self.here())
        self.xp_rec()

    # -------------------------------------------------------- actions

    def step(self, guide: str, n: int | None, reason: str = "auto") -> None:
        dur = 0.0 if self.step_t is None else self.t - self.step_t
        self.guide = guide
        self.rec("step", duration_s=round(dur, 1), guide=guide, map_id=self.map_id, reason=reason, step=n,
                 x=round(self.x, 2), y=round(self.y, 2))
        self.step_t = self.t

    def kill_xp(self, lv: int, cl: str) -> int:
        """A kill's XP before rested XP."""
        diff = lv - self.level
        base = (self.level * 5 + 45)
        xp = max(0, round(base * (1 + 0.05 * diff) if diff >= 0 else base * (1 - (-diff) / (5 + self.level / 10))))
        return xp * 2 if cl == "e" else xp

    def kill(self, npc: int, *, elite: bool | None = None, quest: int | None = None, xp: int | None = None) -> None:
        lv, cl = self.g.npc_level.get(npc, (self.level, "n"))
        if elite is not None:
            cl = "e" if elite else cl
        self.rec("combat", on=True, **self.here())
        if self.rnd.random() < 0.6:
            self.rec("unit", classification=cl, level=lv, npc_id=npc, reaction="h", **self.here())
        self.wait(self.rnd.uniform(15, 35), moving=False)
        forced = xp is not None
        if not forced:
            xp = self.kill_xp(lv, cl)
            if self.goal:
                # What's left of the stretch's XP over the kills left; rested XP doubles a kill's.
                need = self.goal[0] - self.gained
                per = need / max(1.0, (self.goal[1] - self.t) / KILL_S) / (2 if self.rested > 0 else 1)
                xp = max(0, round(per))
        uid = f"{self.rnd.getrandbits(40):010X}"
        self.rec("spawn", npc_id=npc, spawn_uid=uid, **self.here())
        got = self.gain(xp, kill=not forced) if xp else 0
        self.rec("kill", classification=cl, level=lv, npc_id=npc, quests=quest, tapped=False, xp_reward=got,
                 **self.here())
        self.level_kills += 1
        self.rec("combat", on=False, **self.here())
        if self.rnd.random() < 0.65:
            item = self.rnd.choice([4306, 4306, 2589, 4338, 1205, 3770, 858, 4592, 2592])
            self.rec("loot", items=f"{item}:{self.rnd.randint(1, 3)}", npc_id=npc, npc_kind="c",
                     wanted=self.wanted(), **self.here())
            self.money += self.rnd.randint(20, 160)

    def wanted(self) -> str | None:
        """The Recorder's `wanted`: quest:objective of every unfinished item
        objective in the log (a quest's objectives are its kills, else its items)."""
        return ";".join(f"{qid}:{i}" for qid, q in self.log.items() if not q["kills"]
                        for i in range(1, len(q["items"]) + 1)) or None

    def accept(self, q: dict, npc: int | None = None) -> None:
        npc = npc or q["starts"] or None
        if npc:
            self.rec("offer", npc_id=npc, npc_kind="c", quests_available=q["id"],
                     quests_log=";".join(str(x) for x in self.log), **self.here())
            self.rec("detail", npc_id=npc, npc_kind="c", quest_id=q["id"], **self.here())
            self.rec("text", objective=f"Do what {q['name']} asks.", quest_id=q["id"],
                     text="(Synthetic quest text: the fixtures carry none of the game's.)")
        self.rec("accept", level=self.level, money_copper=q["money"] or None, npc_id=npc, npc_kind="c" if npc else None,
                 quest_id=q["id"], quest_name=q["name"], xp_reward=q["xp"],
                 choices=";".join(f"{c}:1" for c in q["choices"]) or None, **self.here())
        objs = ";".join(f"monster:{c}:" for _, c in q["kills"]) or ";".join(f"item:{c}:" for _, c in q["items"])
        if objs:
            self.rec("objectives", objectives=objs, quest_id=q["id"], zone=self.zone)
        self.log[q["id"]] = q
        self.used.add(q["id"])

    def work(self, q: dict, mobs: list[int]) -> None:
        """Kill for a quest's objectives, then it is complete."""
        for i, (npc, need) in enumerate(q["kills"][:2] or [(self.rnd.choice(mobs), 0)] if mobs else []):
            for have in range(1, min(need, 4) + 1 if need else self.rnd.randint(2, 4)):
                self.wait(self.rnd.uniform(20, 60))
                self.kill(npc, quest=q["id"] if need else None)
                if need:
                    self.rec("progress", have=have, need=min(need, 4), objective=i + 1, quest_id=q["id"],
                             **self.here())
        self.rec("complete", quest_id=q["id"], quest_name=q["name"], zone=self.zone)

    def turnin(self, q: dict, reward: int | None = None) -> None:
        npc = q["ends"] or None
        if npc and self.rnd.random() < 0.5:
            self.rec("gossip", gossip_id=self.rnd.randint(90000, 99999), npc_id=npc, npc_kind="c", **self.here())
        got = self.gain(q["xp"])
        self.money += q["money"]
        self.rec("turnin", item=reward, level=self.level, money_copper=q["money"], npc_id=npc,
                 npc_kind="c" if npc else None, quest_id=q["id"], quest_name=q["name"], xp_reward=got, **self.here())
        self.log.pop(q["id"], None)
        self.done.append(q["id"])
        self.level_quests += 1

    def died_here(self) -> None:
        mobs = self.g.zone_mobs(self.zone, self.level)
        self.die(mobs[0] if mobs else None)

    def die(self, npc: int | None = None, how: str = "c") -> None:
        lv = self.g.npc_level.get(npc, (self.level + 1, "n"))[0] if npc else None
        self.rec("death", npc_id=npc, npc_level=lv, **self.here())
        self.flags = 32
        self.wait(self.rnd.uniform(60, 140), moving=True)
        self.flags = 0
        self.rec("revive", how=how, **self.here())
        self.rest()

    def rest(self) -> None:
        secs = self.rnd.randint(12, 24)
        self.wait(secs, moving=False)
        self.rec("rest", rest_kind=self.rnd.choice("fdb"), seconds=secs, zone=self.zone)

    def fly(self, to_zone: str, to_node: str, subzone: str, frm: str) -> None:
        npc = 4267
        self.rec("flightmaster", known_nodes=";".join(str(v) for v in sorted(TAXI.values())), npc_id=npc,
                 taxi_node=TAXI.get(frm, 2), **self.here())
        secs = self.rnd.randint(90, 240)
        self.rec("taxi", duration_s=secs, from_node=TAXI.get(frm, 2), to_node=TAXI[to_node], zone=self.zone)
        self.instance = "taxi"
        self.wait(secs, moving=False)
        self.instance = None
        self.go(to_zone, subzone)
        self.rec("land", seconds=secs, to_node=TAXI[to_node], **self.here())
        self.money -= self.rnd.randint(50, 150)

    def boat(self, to_zone: str, subzone: str) -> None:
        self.rec("ride", on=True, **self.here())
        self.wait(self.rnd.uniform(70, 110), moving=False)
        self.go(to_zone, subzone)
        self.rec("ride", on=False, **self.here())

    def profession(self, skill: int, n: int) -> None:
        """A few crafts in town: Polestar's profession guide, skill-ups."""
        guide = f"forever/professions/{PROF_NAMES[skill].lower()}"
        self.step(guide, self.rnd.randint(5, 20), "pick")
        spell, item = CRAFTS[skill]
        for _ in range(n):
            self.wait(self.rnd.uniform(4, 9), moving=False)
            self.rec("craft", count=1, item_id=item, spell_id=spell, zone=self.zone)
            self.rec("cast", item_id=item, spell_id=spell, **self.here())
            rank, cap = self.skills[skill]
            if rank < cap and self.rnd.random() < 0.7:
                self.skills[skill][0] = rank + 1
                self.rec("skill", max_rank=cap, rank=rank + 1, skill_id=skill, zone=self.zone)

    def train(self, npc: int = 376) -> None:
        self.rec("trainer", npc_id=npc, **self.here())
        for spell in (591, 8092, 2944)[: self.rnd.randint(1, 3)]:
            self.rec("learn", spell_id=spell, zone=self.zone)
        self.money -= 1800

    def town(self) -> None:
        npc = self.rnd.choice([4182, 1312, 3658, 5520])
        self.rec("vendor", items="159:25:5:-1;1179:125:5:-1;4592:20:1:-1;2589:13:20:-1", npc_id=npc, **self.here())
        self.rec("cast", spell_id=self.rnd.choice([1243, 2052, 17]), **self.here())
        self.money += self.rnd.randint(150, 900)  # grey items sold

    # -------------------------------------------------------- a guide

    def xp_until(self, level: int, xp: int) -> int:
        """XP from where the player stands to `xp` into `level`."""
        return sum(XP_MAX[lv] for lv in range(self.level, level)) - self.xp + xp

    def guide_steps(self, guide: str, first: int, last: int, zones: dict[int, tuple], *, reach: tuple[int, int],
                    deaths: tuple = (), hours: float = 1.0, quests_per_step: float = 0.35) -> None:
        """Walk Polestar's guide from step `first` to `last`: the step
        records, the kills and quests a step of its kind takes, zones as
        `zones` says (step -> (zone, subzone[, how])), deaths at the listed
        steps, the whole lasting exactly `hours` and ending `reach` (level,
        XP into it): the steps are Polestar's, the time and levels the
        script's. The stretch draws from its own random streams, so however
        many steps Polestar's guide has there, the rest of the story (the
        sessions, their online time, the dungeons) is the same."""
        outer = self.rnd, self.walk
        self.rnd = random.Random(outer[0].random())
        self.walk = random.Random(outer[0].random())
        try:
            self._stretch(guide, first, last, zones, reach, deaths, hours, quests_per_step)
        finally:
            self.rnd, self.walk = outer

    def fitting(self, qs: list[dict]) -> list[dict]:
        """The quests whose XP fits in what's left of the stretch's mark:
        the kills make up the rest, so the stretch ends on it."""
        left = self.goal[0] - self.gained if self.goal else None
        return qs if left is None else [q for q in qs if q["xp"] <= left]

    STRETCH_SPARE = 300.0  # seconds kept at a stretch's end for the last pull and a death on a missing step

    def _stretch(self, guide, first, last, zones, reach, deaths, hours, quests_per_step) -> None:
        steps = [s for s in self.g.steps(guide) if first <= s[0] <= last]
        t0, total = self.t, hours * 3600
        # A stretch that starts past its mark (the last one's quests) keeps it.
        self.goal = (self.gained + max(0, self.xp_until(*reach)), t0 + total - self.STRETCH_SPARE)
        # Each step's end, absolute: its share of the stretch by Polestar's planned time.
        weights = [max(20.0, s[1]) * self.rnd.uniform(0.7, 1.3) for s in steps]
        span, ends, acc = total - self.STRETCH_SPARE, [], 0.0
        for w in weights:
            acc += w
            ends.append(t0 + span * acc / max(1e-9, sum(weights)))
        for (n, _planned, _title, kind), end in zip(steps, ends, strict=True):
            if n in zones:
                z = zones[n]
                how = z[2] if len(z) > 2 else "walk"
                if how == "fly":
                    self.fly(z[0], z[3], z[1], z[4])
                elif how == "boat":
                    self.boat(z[0], z[1])
                else:
                    self.go(z[0], z[1])
            self.step(guide, n, self.rnd.choice(["auto", "auto", "next", "done"]))
            if kind == "questing":
                qs = self.fitting(self.g.zone_quests(self.zone, self.level, self.used))
                mobs = self.g.zone_mobs(self.zone, self.level)
                if qs and self.rnd.random() < quests_per_step:
                    q = qs[self.rnd.randrange(len(qs))]
                    self.accept(q)
                    self.work(q, mobs)
                    self.wait(self.rnd.uniform(10, 40))
                    self.turnin(q)
                while self.t < end - 90 and mobs:
                    self.wait(self.rnd.uniform(20, 50))
                    self.kill(self.rnd.choice(mobs))
                if self.rnd.random() < 0.2:
                    self.rest()
                if self.rnd.random() < 0.08:
                    self.rec("explore", area_id=self.rnd.randint(100, 1000), xp_reward=self.gain(self.level * 7),
                             **self.here())
            elif kind == "town":
                self.town()
                qs = self.fitting(self.g.zone_quests(self.zone, self.level, self.used))
                if qs and self.rnd.random() < quests_per_step:
                    q = qs[0]
                    self.accept(q)
                    self.wait(20)
                    self.turnin(q)
            if n in deaths:
                self.died_here()
            self.wait(max(0.0, end - self.t))
        # The script's deaths happen whatever kind of step Polestar's guide
        # has there, and a step it no longer has dies at the stretch's end.
        for _n in sorted(set(deaths) - {s[0] for s in steps}):
            self.died_here()
        # The last pull makes up what the kills' estimate left (quest XP is
        # the guides'; a stretch already past its mark keeps it).
        goal, self.goal = self.goal[0], None
        if goal > self.gained:
            mobs = (self.g.zone_mobs(self.zone, self.level)
                    or [m for z in AREAS for m in self.g.zone_mobs(z, self.level)])
            self.kill(mobs[0], xp=goal - self.gained)
        self.wait(max(0.0, t0 + total - self.t))


# ---------------------------------------------------------------- the playthrough


def sessions_priest(p: Player) -> dict:
    """Fifteen evenings, 22 to 32. Returns the marks the slices need."""
    g, rnd = p.g, p.rnd
    marks = {}
    q = g.quests
    p.rested = 4200
    p.log[971] = q[971]  # picked up before the recording starts
    p.used.add(971)
    # Never done: The Unsent Letter (373) and Bazil Thredd (389), the
    # Stockade quest it unlocks, stay open for the dungeon panel.
    p.used |= {373, 389}

    # 1. Mon 5 Oct: Darkshore, 22 -> 23. Two deaths.
    p.login(local(2026, 10, 5, 18, 32), "Darkshore", "Auberdine")
    p.guide_steps("forever/alliance/03-darkshore-return", 1, 22,
                  {1: ("Darkshore", "Auberdine"), 2: ("Darkshore", None), 12: ("Darkshore", "Auberdine"),
                   14: ("Darkshore", "The Ruins of Mathystra")}, reach=(23, 8000), deaths=(8, 18), hours=2.1)
    p.rec("bind", **p.here())
    p.bind = "Auberdine"
    p.rec("escort", on=True, quest_id=5321, **p.here())
    p.wait(90)
    p.rec("escort", on=False, quest_id=5321, **p.here())
    p.rec("fail", quest_id=5321, quest_name="The Sleeper Has Awakened", **p.here())
    p.rec("rep", faction_id=69, reaction=5, rep_gained=75, standing=4210, zone=p.zone)
    p.profession(TAILORING, 6)
    p.logout()

    # 2. Tue 6 Oct: Ashenvale and Stonetalon, 23 -> 24. One death, an AFK.
    p.rested += 2600
    p.login(local(2026, 10, 6, 19, 5), "Darkshore", "Auberdine")
    p.guide_steps("forever/alliance/06-ashenvale", 1, 24,
                  {2: ("Ashenvale", None), 6: ("Ashenvale", "Astranaar"), 11: ("Stonetalon Mountains", None),
                   20: ("Ashenvale", "Astranaar"), 22: ("Ashenvale", "The Zoram Strand")}, reach=(23, 18000),
                  deaths=(16,), hours=2.0, quests_per_step=0.15)
    p.rec("afk", on=True, zone=p.zone)
    p.wait(420, moving=False)
    p.rec("afk", on=False, zone=p.zone)
    stale = g.zone_quests("Ashenvale", p.level, p.used)[0]
    p.accept(stale)  # still open at the end: the planner's open quests
    abandoned = g.zone_quests("Ashenvale", p.level, p.used)[0]
    p.accept(abandoned)
    p.rec("abandon", quest_id=abandoned["id"], quest_name=abandoned["name"], zone=p.zone)
    p.logout()

    # 3. Wed 7 Oct: Blackfathom Deeps at 23 (dings 24 inside), then Darnassus.
    # One death inside. Its quests: 1198 handed in inside, 1199 picked up
    # and handed in inside (Nimbus Boots, worn), 1200 handed in in Darnassus
    # (Gravestone Scepter, worn), 971 done inside and handed in the next day
    # in Ironforge; 1275 never picked up (the panel's next quest).
    p.rested += 1800
    p.login(local(2026, 10, 7, 19, 20), "Ashenvale", "The Zoram Strand")
    p.step("lookup/pickup/blackfathom-deeps", 1, "auto")
    p.accept(q[1198], 4786)
    p.wait(160)
    p.step("lookup/pickup/blackfathom-deeps", None, "done")
    p.wait(60)
    p.rec("group", raid=False, size=4, zone=p.zone)
    p.rec("enter", difficulty=0, instance_id=48, instance_kind="party", **p.here())
    t0 = p.t
    marks["bfd"] = t0
    bfd_level = p.level
    p.go("Blackfathom Deeps", "Blackfathom Deeps", 44.0, 35.0)
    p.instance = "Blackfathom Deeps"
    g0 = p.gained
    kills = 0
    end = t0 + BFD_SECONDS
    p.turnin(q[1198])  # Argent Guard Thaelrid, inside
    p.accept(q[1200], 4787)
    p.accept(q[1199], 4784)
    # The bosses give their own XP, no rested (how much rested is left here
    # depends on the stretch before, whose steps are Polestar's): the trash
    # stops 1,200 short of what they and the hand-in leave, and Aku'mai
    # makes up the rest.
    boss_xp = [p.kill_xp(*g.npc_level.get(b, (p.level, "n"))) for b in BFD_BOSSES]
    while p.gained - g0 < BFD_XP - q[1199]["xp"] - sum(boss_xp) - 1200:
        p.wait(rnd.uniform(100, 150))
        p.kill(rnd.choice(BFD_MOBS))
        kills += 1
        if kills == 9:
            p.die(4810, how="s")
        if kills % 3 == 0:
            n = kills // 3
            p.rec("loot", items="5879:1", npc_id=4809, npc_kind="c", **p.here())
            p.rec("progress", have=min(n, 10), need=10, objective=1, quest_id=1199, **p.here())
        if kills == 6:
            p.rec("loot", items="5359:1", npc_kind="o", **p.here())  # the manuscript, from a chest
            p.rec("progress", have=1, need=1, objective=1, quest_id=971, **p.here())
            p.rec("complete", quest_id=971, quest_name=q[971]["name"], zone=p.zone)
    if kills < 9:  # the run's one death, however short the trash came out
        p.die(4810, how="s")
    p.rec("complete", quest_id=1199, quest_name=q[1199]["name"], zone=p.zone)
    for i, (boss, bxp) in enumerate(zip(BFD_BOSSES, boss_xp, strict=True)):
        p.wait(rnd.uniform(60, 120))
        p.kill(boss, xp=bxp)
        p.rec("encounter", difficulty=1, encounter_id=2910 + i, size=5, success=True, zone=p.zone)
    p.rec("loot", items="5881:1;6463:1", npc_id=4832, npc_kind="c", **p.here())  # Deep Fathom Ring: worn
    p.rec("progress", have=1, need=1, objective=1, quest_id=1200, **p.here())
    p.rec("complete", quest_id=1200, quest_name=q[1200]["name"], zone=p.zone)
    p.turnin(q[1199], reward=6998)  # Argent Guard Manados, inside
    p.worn[8] = 6998
    rest = BFD_XP - (p.gained - g0)
    assert 0 < rest < 1500, rest
    p.wait(rnd.uniform(60, 120))
    p.kill(4829, xp=rest)  # Aku'mai: the run's XP comes to BFD_XP exactly
    p.rec("encounter", difficulty=1, encounter_id=2914, size=5, success=True, zone=p.zone)
    p.rec("rare", classification="r", level=-1, npc_id=4887, **p.here())
    assert p.t < end - 60, (p.t, end)
    p.wait(end - p.t)
    p.instance = None
    p.other("wow-dungeon", dungeon_id=48, dungeon_kind="party", left=iso(end), level=bfd_level,
            name="Blackfathom Deeps", resumed=None, seconds=BFD_SECONDS, xp=BFD_XP)
    p.out[-1]["_time"] = iso(t0)
    p.go("Ashenvale", "The Zoram Strand", 14.0, 31.0)
    p.rec("group", raid=False, size=0, zone=p.zone)
    p.step("lookup/handin/blackfathom-deeps", 1, "auto")
    p.fly("Darnassus", "Rut'theran Village", "Rut'theran Village", "Astranaar")
    p.go("Darnassus", "Cenarion Enclave")
    p.turnin(q[1200], reward=7001)
    p.worn[18] = 7001
    p.step("lookup/handin/blackfathom-deeps", None, "done")
    p.train(4091)
    p.rec("achieve", achievement_id=62000, zone=p.zone)
    p.profession(ENCHANTING, 5)
    p.logout()

    # 4. Thu 8 Oct: Wetlands, Ironforge, Stormwind, then Duskwood, 24/25 -> 26. One death.
    p.rested += 2200
    p.login(local(2026, 10, 8, 19, 0), "Darnassus", "Cenarion Enclave")
    p.fly("Darkshore", "Auberdine", "Auberdine", "Rut'theran Village")
    p.guide_steps("forever/alliance/06-ashenvale", 31, 51,
                  {31: ("Wetlands", "Menethil Harbor", "boat"), 35: ("Wetlands", "Bluegill Marsh"),
                   45: ("Wetlands", "Menethil Harbor"), 46: ("Ironforge", "The Great Forge", "fly", "Ironforge",
                                                             "Menethil Harbor")}, reach=(25, 15000), deaths=(38,),
                  hours=1.4)
    p.rec("unitx", npc_id=5595, spawn_uid="4B1D0A0001", **p.here())
    p.rec("where", indoors=False, subzone="The Great Forge", where_id=1573, where_kind="c", zone=p.zone)
    p.turnin(q[971], reward=6743)  # Gerrig Bonegrip: the manuscript from Blackfathom Deeps
    p.boat("Stormwind City", "Trade District")
    p.guide_steps("forever/alliance/04-redridge-mountains", 1, 25,
                  {1: ("Stormwind City", "Trade District"), 4: ("Westfall", "Sentinel Hill", "fly", "Sentinel Hill",
                                                                 "Stormwind City"),
                   6: ("Elwynn Forest", None), 10: ("Duskwood", None), 11: ("Duskwood", "Darkshire"),
                   15: ("Duskwood", "Tranquil Gardens Cemetery"), 24: ("Duskwood", "Darkshire")}, reach=(26, 10000),
                  hours=1.6)
    p.go("Duskwood", "Scarlet Raven Tavern")
    p.gear_sheet()
    p.logout()
    marks["four"] = p.t

    # 5-9: Redridge, Duskwood, Wetlands, Ashenvale: 26 -> 31.
    days = [(9, 18, 50), (10, 15, 10), (11, 19, 0), (12, 19, 15), (13, 18, 40)]
    plan = [
        ("forever/alliance/04-redridge-mountains", 26, 45,
         {26: ("Redridge Mountains", None), 29: ("Redridge Mountains", "Lakeshire"),
          34: ("Redridge Mountains", "Render's Valley")}, (36,), (27, 15000)),
        ("forever/alliance/05-duskwood", 1, 25,
         {1: ("Duskwood", "Darkshire", "fly", "Darkshire", "Lakeshire"), 19: ("Westfall", None),
          22: ("Elwynn Forest", None), 25: ("Stormwind City", "Trade District")}, (), (28, 5000)),
        ("forever/alliance/06-wetlands", 11, 45,
         {11: ("Wetlands", "Menethil Harbor", "fly", "Menethil Harbor", "Stormwind City"),
          17: ("Wetlands", "Bluegill Marsh"), 20: ("Wetlands", "Raptor Ridge"),
          40: ("Darkshore", "Auberdine", "boat"), 44: ("Ashenvale", "Astranaar", "fly", "Astranaar", "Auberdine")},
         (22,), (29, 10000)),
        ("forever/alliance/06-wetlands", 46, 88,
         {46: ("Ashenvale", "Astranaar"), 56: ("Ashenvale", "The Dor'Danil Barrow Den"),
          68: ("Ashenvale", "Astranaar"), 82: ("Darkshore", "Auberdine", "fly", "Auberdine", "Astranaar"),
          83: ("Wetlands", "Menethil Harbor", "boat"),
          85: ("Ironforge", "The Great Forge", "fly", "Ironforge", "Menethil Harbor")}, (60,), (30, 15000)),
        ("forever/alliance/07-thousand-needles", 1, 21,
         {1: ("Duskwood", None), 2: ("Duskwood", "Darkshire"), 11: ("Duskwood", "Vul'Gol Ogre Mound"),
          17: ("Duskwood", "Darkshire")}, (), (31, 15000)),
    ]
    zones_at = ["Redridge Mountains", "Redridge Mountains", "Stormwind City", "Ironforge", "Stormwind City"]
    for (d, h, m), (guide, a, b, zones, deaths, reach), z0 in zip(days, plan, zones_at, strict=True):
        p.rested += 2500
        p.login(local(2026, 10, d, h, m), z0)
        p.guide_steps(guide, a, b, zones, reach=reach, deaths=deaths, hours=2.4)
        if d == 10:
            p.train(376)
            p.profession(TAILORING, 8)
            p.profession(COOKING, 10)
            for item, price in ((4306, 205), (4338, 410), (2592, 64), (3356, 95), (4234, 160)):
                p.other("wow-prices", item_id=item, price_copper=price,
                        source_id=f"price:{item}:{d}")
        if d == 12:
            p.guide_steps("forever/alliance/06-wetlands", 89, 96,
                          {89: ("Stormwind City", "Trade District", "boat"),
                           91: ("Stormwind City", "Cathedral Square")}, reach=(30, 25000), hours=0.4)
            p.profession(ENCHANTING, 8)
        p.logout()

    assert p.level == 31, p.level

    # 10. Wed 14 Oct: The Stockade at 31, from Wetlands and back.
    p.rested += 1400
    p.login(local(2026, 10, 14, 18, 20), "Wetlands", "Menethil Harbor")
    p.guide_steps("forever/alliance/30-40/01-hillsbrad-foothills", 1, 12,
                  {1: ("Wetlands", "Angerfang Encampment"), 6: ("Wetlands", "Dragonmaw Gates"),
                   10: ("Wetlands", "The Green Belt")}, reach=(31, 25000), hours=0.7)
    marks["stockade_from"] = p.t
    p.step("lookup/pickup/stormwind-stockade", 1, "auto")
    p.wait(40)
    p.fly("Ironforge", "Ironforge", "The Great Forge", "Menethil Harbor")
    p.step("lookup/pickup/stormwind-stockade", 2, "next")
    p.boat("Stormwind City", "Trade District")
    p.go("Stormwind City", "Stormwind Keep")
    p.accept(q[377], 270)
    p.step("lookup/pickup/stormwind-stockade", 2, "auto")
    p.accept(q[386], 859)
    p.accept(q[378], 1719)
    p.accept(q[388], 1721)
    p.wait(30)
    p.accept(q[387], 1719)
    p.accept(q[391], 1719)
    p.step("lookup/pickup/stormwind-stockade", 3, "auto")
    p.wait(60)
    p.step("lookup/pickup/stormwind-stockade", None, "done")
    p.go("Stormwind City", "The Canals", 52.3, 69.9)
    p.rec("group", raid=False, size=4, zone=p.zone)
    p.wait(300)
    p.rec("enter", difficulty=0, instance_id=34, instance_kind="party", **p.here())
    t0 = p.t
    marks["stockade"] = t0
    g0 = p.gained
    p.go("Stormwind Stockade", "Stormwind Stockade", 50.0, 50.0)
    p.instance = "Stormwind Stockade"
    # Discovery XP inside: the run's XP is more than its kills'.
    p.rec("explore", area_id=717, xp_reward=p.gain(310), **p.here())
    for i in range(16):
        p.wait(rnd.uniform(25, 45))
        p.kill(rnd.choice(STOCKADE_MOBS), quest=387)
        if i % 4 == 3:
            p.rec("loot", items="2909:2", npc_id=1711, npc_kind="c", **p.here())
    for b in STOCKADE_BOSSES:
        p.wait(rnd.uniform(40, 70))
        p.kill(b)
        p.rec("loot", items=f"{rnd.choice([3628, 3630, 2926, 2909, 1958, 3400])}:1", npc_id=b, npc_kind="c",
              **p.here())
    for qid in (386, 377, 378, 387, 388, 391):
        p.rec("progress", have=1, need=1, objective=1, quest_id=qid, **p.here())
        p.rec("complete", quest_id=qid, quest_name=q[qid]["name"], zone=p.zone)
    inside = round(p.t - t0) + 30
    p.wait(t0 + inside - p.t)
    p.instance = None
    p.other("wow-dungeon", dungeon_id=34, dungeon_kind="party", left=iso(t0 + inside), level=31,
            name="Stormwind Stockade", seconds=inside, xp=p.gained - g0)
    p.out[-1]["_time"] = iso(t0)
    p.go("Stormwind City", "The Canals", 52.3, 69.9)
    p.rec("group", raid=False, size=0, zone=p.zone)
    p.step("lookup/handin/stormwind-stockade", 1, "auto")
    p.wait(70)
    p.go("Stormwind City", "Stormwind Keep")
    p.turnin(q[391])
    p.turnin(q[387])
    p.step("lookup/handin/stormwind-stockade", 2, "auto")
    p.turnin(q[388])
    p.wait(120)
    p.step("lookup/handin/stormwind-stockade", 3, "auto")
    p.turnin(q[386], reward=1317)
    p.worn[16] = 1317
    p.fly("Duskwood", "Darkshire", "Darkshire", "Stormwind City")
    p.turnin(q[377], reward=270029)
    p.worn[10] = 270029
    p.step("lookup/handin/stormwind-stockade", 4, "auto")
    # A walk to Darkshire's flight master: the next flight starts after the
    # landing, not in the same second (the Flights list pairs them by time).
    p.wait(45, moving=True)
    p.fly("Wetlands", "Menethil Harbor", "Menethil Harbor", "Darkshire")
    p.turnin(q[378], reward=270036)
    p.worn[7] = 270036
    p.step("lookup/handin/stormwind-stockade", None, "done")
    p.gear_sheet()
    p.logout()
    marks["stockade_logout"] = p.t

    # 11-13. Wetlands to Menethil: Hillsbrad guide 13 -> 37, 31 -> 32.
    for d, (a, b), reach in ((15, (13, 22), (32, 20000)), (16, (23, 31), (32, 30000)), (17, (32, 37), (32, 40000))):
        p.rested += 2400
        p.login(local(2026, 10, d, 19, 0), "Wetlands", "Menethil Harbor")
        p.guide_steps("forever/alliance/30-40/01-hillsbrad-foothills", a, b,
                      {13: ("Wetlands", "Dun Modr"), 17: ("Wetlands", "Direforge Hill"),
                       26: ("Wetlands", "The Green Belt"), 31: ("Wetlands", "Menethil Harbor")},
                      reach=reach, deaths=(18,) if d == 15 else (), hours=2.0)
        if d == 16:
            for item, price in ((4306, 190), (4338, 395), (7909, 2400), (3864, 1100)):
                p.other("wow-prices", item_id=item, price_copper=price, source_id=f"price:{item}:{d}")
        p.logout()
    assert p.level == 32, p.level

    # 14. Sun 18 Oct: the boat to Southshore, not followed in the guide: bound there.
    p.login(local(2026, 10, 18, 17, 30), "Wetlands", "Menethil Harbor")
    p.step("forever/alliance/30-40/01-hillsbrad-foothills", 38, "manual")
    p.wait(120)
    p.boat("Hillsbrad Foothills", "Southshore")
    p.rec("bind", **p.here())
    p.bind = "Southshore"
    p.town()
    p.gear_sheet()
    p.logout()

    # 15. Mon 19 Oct, two short logins: Polestar shows step 16 again (an open quest from there).
    for h, m in ((18, 2), (21, 40)):
        p.login(local(2026, 10, 19, h, m), "Hillsbrad Foothills", "Southshore")
        p.step("forever/alliance/30-40/01-hillsbrad-foothills", 16, "login")
        p.wait(rnd.uniform(110, 200), moving=False)
        p.logout()
    marks["end"] = p.t
    return marks


def gear_sheet(p: Player) -> None:
    """The newest character sheet (Syndicator and friends): what is worn,
    the professions, the spec."""
    from wowstats import gear

    ref = gear.reference()
    names = ref.data.get("names") or {}
    items = []
    # Drops and crafts only, besides the quest rewards the script hands in:
    # an item only a quest gives would mark that quest done (wowstats.quests).
    base = {2: 273457, 3: 4315, 5: 6264, 6: 7000, 9: 273808, 11: 6463, 12: 1156, 13: 273298, 15: 4311}
    worn = {**base, **p.worn}
    slot_names = {1: "Head", 2: "Neck", 3: "Shoulder", 4: "Shirt", 5: "Chest", 6: "Waist", 7: "Legs", 8: "Feet",
                  9: "Wrist", 10: "Hands", 11: "Finger", 12: "Finger", 13: "Trinket", 14: "Trinket", 15: "Back",
                  16: "Main hand", 17: "Off hand", 18: "Ranged", 19: "Tabard"}
    for slot in sorted(worn):
        iid = worn[slot]
        info = ref.item(iid) or {}
        items.append({"item_id": iid, "item_level": info.get("ilvl"), "name": names.get(str(iid)),
                      "quality": info.get("quality"), "req_level": info.get("req") or None, "slot": slot,
                      "slot_name": slot_names[slot]})
    ilvl = [i["item_level"] for i in items if i["item_level"]]
    # As the shipper sends them since 2026-11-20: Polestar's ranks, no cap
    # (the Recorder's skill records carry it) and no talents (Battle.net's).
    profs = [{"name": PROF_NAMES[s], "rank": rank, "skill_id": s} for s, (rank, _cap) in sorted(p.skills.items())]
    p.other("wow-gear", **p.who, item_level=round(sum(ilvl) / len(ilvl), 1), items=items, professions=profs,
            spec="Shadow")


Player.gear_sheet = gear_sheet


# ---------------------------------------------------------------- outputs


def gz(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as f:
        f.write(data)
    return buf.getvalue()


def jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode()


def synthetic_card(iid: int) -> dict:
    """An item card the product would read from the client's tables (sell
    price, quality, binding), made up from the gear reference so the
    generator runs offline: what the fixtures' loot values come from."""
    from wowstats import gear

    info = gear.reference().item(iid) or {}
    q = info.get("quality", 1)
    lvl = info.get("ilvl") or 10
    return {"sell": int(lvl * lvl * (q + 1) / 4), "q": q, "n": (gear.reference().data.get("names") or {}).get(
        str(iid)), "b": "Binds when equipped" if q >= 2 else None}


def plan_inputs(rows: list[dict], char: str, now: float) -> dict:
    """wowstats.plan.facts.gather's work on these events, without a store:
    the same calls, the item cards from synthetic_card."""
    from wowstats import gear
    from wowstats.analysis import kb, prep
    from wowstats.mcp import route
    from wowstats.plan import facts

    knowledge = kb.build({"KB_GAME_TABLES": "off"})
    advice, loot = None, {}
    with contextlib.suppress(gear.GearError):
        advice = gear.advise_rows(rows, None)
        loot = gear.instance_loot_rows(rows, None, (advice or {}).get("bis_by_slot"))
    mine = [r for r in rows if r.get("char") == char]
    db = prep.build(mine, char, route.reference(), map_sizes=knowledge.meta.get("ui_map"),
                    advice=advice, now=datetime.fromtimestamp(now, UTC))
    scans = {}
    for r in sorted((r for r in rows if r.get("feed") == "wow-prices"), key=lambda r: r["_time"]):
        scans[int(r["item_id"])] = {"t": datetime.fromisoformat(r["_time"].replace("Z", "+00:00")).timestamp(),
                                    "copper": int(r["price_copper"]), "name": r.get("item_name")}
    inp = facts.extract(db, knowledge, route.reference(), advice=advice, scans=scans, state={}, now=now, loot=loot,
                        bags=gear.newest_bags(mine), cards=synthetic_card)
    inp["who"]["char"] = inp["who"].get("char") or char
    # As recorded before quests.completed's inference (#43): the hand-ins the
    # Recorder saw, nothing inferred, and no instance-entry rule yet. The
    # tests that add the inference and the rule start from here.
    inp["quests_done"] = sorted({int(r["quest_id"]) for r in mine if r.get("kind") == "turnin"})
    inp["instance_limit"] = None
    inp["calibration"] = {"factor": 1.0, "n": 0}
    inp["plan_reviews"] = []
    return json.loads(json.dumps(inp))


def gear_answer(rows: list[dict], char: str) -> dict:
    """The full view's /api/gear answer (wowstats.views.Views.gear) for the
    newest sheet: game data and the addon's sheet only."""
    from wowstats import gear

    sh, level, zone, done = gear.newest_sheet(rows)
    advice = gear.advise(gear.reference(), sh, level=level, zone=zone, done=done)
    sheet_t = max(r["_time"] for r in rows if r.get("feed") == "wow-gear")
    out = {"char": char, "name": char.split("-")[0], "polestar": True, "available": True, "at": sheet_t,
           "talents": None, **advice}
    out["generated_at"] = sheet_t
    return json.loads(json.dumps(out))


# ---------------------------------------------------------------- addon saves


class LuaArray(dict):
    """A Lua table whose keys 1..n are written as a list, the rest as [k]."""


def lua(v, ind: str = "") -> str:
    if isinstance(v, LuaArray):
        n = 0
        while n + 1 in v:
            n += 1
        body = "".join(f"{lua(v[k])},\n" for k in range(1, n + 1))
        body += "".join(f"[{k}] = {lua(x)},\n" for k, x in v.items() if not 1 <= k <= n)
        return "{\n" + body + "}"
    if isinstance(v, dict):
        body = "".join(f"{ind}[{k if isinstance(k, int) else json.dumps(k)}] = {lua(x, ind)},\n" for k, x in v.items())
        return "{\n" + body + ind + "}"
    if isinstance(v, list):
        return "{\n" + "".join(f"{ind}{lua(x, ind)},\n" for x in v) + ind + "}"
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "nil"
    if isinstance(v, float):
        return f"{v:.16g}"
    return json.dumps(v) if isinstance(v, str) else str(v)


def lua_file(globals_: dict) -> bytes:
    return "".join(f"{k} = {lua(v)}\n" for k, v in globals_.items()).encode()


def quest_bits(done: list[int], rnd: random.Random) -> dict:
    """DataStore_Quests' history: 64 quests a double, slot k holding quests
    64(k-1)+1 .. 64k (bit 0 is quest 64k), 2^bit added one quest at a time
    in no particular order (as the addon does), so a dense slot loses its
    low bits; the IDs far above the rest as sparse [n] keys."""
    slots: dict[int, float] = {}
    order = list(done)
    rnd.shuffle(order)
    for q in order:
        k, b = (q // 64, 0) if q % 64 == 0 else (q // 64 + 1, q % 64)
        slots[k] = slots.get(k, 0.0) + float(2 ** b)
    out = LuaArray()
    for k in sorted(slots):
        v = slots[k]
        out[k] = int(v) if v < 2 ** 53 else v
    return out


def addon_saves(rnd: random.Random) -> dict[str, bytes]:
    """The Altoholic and Questie saves of an account with both characters,
    for a made-up set of quests (tests/data/quests-forever/truth.txt)."""
    # 373 and 389 done (the Stockade panel's prerequisites), 391 not; one
    # ID far above the rest (a sparse slot).
    truth = sorted((set(rnd.sample(range(1, 390), 258)) | {373, 389, 98463}) - {391})
    questie = [q for q in truth if q <= 213 or q == 373]
    pn, ps = PRIEST.split("-")
    hn, hs = HUNTER.split("-")
    realm = "Fixture Realm"
    out = {}
    out["altoholic-forever/DataStore.lua"] = lua_file({
        "DataStore_GuildIDs": {"List": [f"Default.{realm}.Some Guild"], "Count": 1,
                               "Set": {f"Default.{realm}.Some Guild": 1}},
        "DataStore_GuildFactions": ["Alliance"],
        "DataStore_CharacterIDs": {"List": [f"Default.{realm}.{pn} {ps}", f"Default.{realm}.{hn} {hs}"], "Count": 2,
                                   "Set": {f"Default.{realm}.{pn} {ps}": 1, f"Default.{realm}.{hn} {hs}": 2}},
        "DataStore_CharacterGUIDs": ["Player-0000-00000001", "Player-0000-00000002"],
        "DataStore_CharacterGuilds": [1], "DataStore_AltGroups": {}, "DataStore_ConnectedRealms": {},
        "DataStore_RealmNames": {realm.replace(" ", ""): realm}})
    t1, t2 = int(local(2026, 10, 19, 21, 44)), int(local(2026, 10, 19, 21, 45))
    out["altoholic-forever/DataStore_Characters.lua"] = lua_file({
        "DataStore_Characters_Info": [
            {"playedThisLevel": 516, "lastLogoutTimestamp": t1, "lastUpdate": t1, "name": f"{pn} {ps}",
             "BaseInfo": 2636448, "played": 168240},
            {"playedThisLevel": 2, "played": 2, "lastUpdate": t2, "name": f"{hn} {hs}", "lastLogoutTimestamp": t2,
             "BaseInfo": 530817}],
        "DataStore_Characters_GuildRanks": [{3: "Member"}],
        "DataStore_Characters_Options": {"RequestPlayTime": True, "HideRealPlayTime": False}})
    out["altoholic-forever/DataStore_Talents.lua"] = lua_file({
        "DataStore_Talents_Characters": [{"Glyphs": {}}, {"Glyphs": {}}],
        "DataStore_Talents_Specializations": [41, 73],
        "DataStore_Talents_SpecializationInfos": {
            "Roles": {"List": ["DAMAGER"], "Count": 1, "Set": {"DAMAGER": 1}},
            "Names": {"List": ["Priest", "Hunter"], "Count": 2, "Set": {"Priest": 1, "Hunter": 2}}},
        "DataStore_Talents_Covenant": None, "DataStore_Talents_Conduits": None, "DataStore_Talents_ConduitSpecs": None,
        "DataStore_Talents_Soulbinds": None, "DataStore_Talents_Reasons": None})
    history = quest_bits(truth, rnd)
    out["quests-forever/DataStore_Quests.lua"] = lua_file({
        "DataStore_Quests_Characters": [
            {"lastUpdate": t1 - 25, "QuestHeaders": {}, "Rewards": {}, "Quests": {}, "Emissaries": {}},
            {"lastUpdate": t2, "QuestHeaders": {}, "Rewards": {}, "Quests": {}, "Emissaries": {}}],
        "DataStore_Quests_History": [{"LastUpdate": t1 - 25, "Size": len(truth), "Quests": history, "Build": "1.60.1"},
                                     {"LastUpdate": t2, "Size": 0, "Quests": {}, "Build": "1.60.1"}],
        "DataStore_Quests_Progress": None, "DataStore_Quests_Dailies": {}, "DataStore_Quests_Weeklies": None,
        "DataStore_Quests_Options": {"TrackTurnIns": True, "AutoUpdateHistory": True, "DailyResetHour": 3}})
    out["quests-forever/Questie.lua"] = lua_file({
        "QuestieConfig": {
            "char": {f"{hn} {hs}": {"townsfolkClass": "HUNTER", "guid": "Player-0000-00000002"},
                     f"{pn} {ps}": {"townsfolkClass": "PRIEST", "guid": "Player-0000-00000001",
                                    "complete": {q: True for q in questie}}},
            "profileKeys": {f"{hn} {hs}": "Default", f"{pn} {ps}": "Default"},
            "global": {"journeyKeybindDefaultApplied": True},
            "profiles": {"Default": {"migrationVersion": 1}}},
        "QuestieProfilerEnabled": None})
    out["quests-forever/truth.txt"] = (" ".join(str(q) for q in truth) + "\n").encode()
    return out


# ---------------------------------------------------------------- the public demo


# The docs site's demo (site/, /demo/): the fixtures' priest under an
# invented name, one more week of play (City of Dalaran, Razorfen Kraul),
# the auction house scans and Questie's quest list. Nothing of a real
# account: the same seeded script as the fixtures, then the extra evenings.
DEMO = "Nibbet-Gearholt"
DEMO_REALM = "Dawnwatch"
AH_UPGRADE, AH_PRICE = 4323, 14500  # Shadow Hood, a tailor's hood: bound when equipped
DALARAN_MOBS = [247003, 247001, 246600]  # Starving Arcane's three kinds
DALARAN_BOSSES = [245999, 247126, 246003, 246008, 246016]
RFK_MOBS = [4435, 4436, 4437, 4438]
RFK_BOSSES = [4424, 4428, 4420, 4422, 4421]
# What the auction house lists, besides the upgrade: cloth, herbs, the
# enchanting dusts, green items around the level. Deterministic.
AH_STOCK = [2589, 2592, 4306, 4338, 2453, 3355, 3356, 3357, 3818, 3820, 3821, 785, 2450, 2452, 3369, 10940, 10938,
            10939, 11083, 11082, 11134, 11137, 2996, 2997, 4305, 4339, 2321, 2604, 4291, 2605, 6260, 2324, 3371,
            3372, 8925, 1179, 1645, 2681, 2684, 5465, 5467, 5469, 5471, 3667, 3712, 4655, 5503, 5504, 6889, 769,
            2672, 2673, 3173, 3174, 1475, 6264, 7000, 6268, 6267, 9770, 9779, 9786, 9800]


def sessions_demo(p: Player, end_marks: dict) -> None:
    """Five more sessions after the fixtures' fifteen: Hillsbrad, City of
    Dalaran and Razorfen Kraul at 33, Arathi to 34, a morning at the inn."""
    rnd, q = p.rnd, p.g.quests

    # 16. Tue 20 Oct: Southshore and the Hillsbrad fields. One death.
    p.rested += 2600
    p.login(local(2026, 10, 20, 19, 5), "Hillsbrad Foothills", "Southshore")
    p.guide_steps("forever/alliance/30-40/01-hillsbrad-foothills", 40, 54,
                  {40: ("Hillsbrad Foothills", "Southshore"), 42: ("Hillsbrad Foothills", "Darrow Hill"),
                   50: ("Hillsbrad Foothills", "Hillsbrad Fields"), 54: ("Hillsbrad Foothills", "Southshore")},
                  reach=(32, 45000), deaths=(46,), hours=2.0)
    p.profession(TAILORING, 6)
    p.logout()

    # 17. Wed 21 Oct: Alterac Mountains, then City of Dalaran at 33.
    p.rested += 2200
    p.login(local(2026, 10, 21, 18, 45), "Hillsbrad Foothills", "Southshore")
    p.guide_steps("forever/alliance/30-40/05-arathi-highlands", 22, 26,
                  {22: ("Hillsbrad Foothills", "Southshore"), 25: ("Alterac Mountains", None),
                   26: ("Alterac Mountains", "Gallows' Corner")}, reach=(33, 8000), hours=0.9)
    p.go("Alterac Mountains", "Dalaran")
    p.accept(q[92432], 247264)
    p.wait(200)
    p.step("forever/alliance/30-40/05-arathi-highlands", 63, "auto")
    p.turnin(q[92432])
    p.accept(q[92457], 269127)
    p.accept(q[92458], 269127)
    p.step("forever/alliance/30-40/05-arathi-highlands", 64, "auto")
    p.rec("group", raid=False, size=5, zone=p.zone)
    p.wait(240)
    p.rec("enter", difficulty=0, instance_id=2959, instance_kind="party", **p.here())
    t0, g0, lvl = p.t, p.gained, p.level
    p.go("City of Dalaran", "City of Dalaran", 30.0, 60.0)
    p.instance = "City of Dalaran"
    for i, npc in enumerate(DALARAN_MOBS * 4):
        p.wait(rnd.uniform(40, 80))
        p.kill(npc, elite=True, quest=92457)
        p.rec("progress", have=min(8, i // 3 * 2 + 2), need=8, objective=DALARAN_MOBS.index(npc) + 1,
              quest_id=92457, **p.here())
    p.rec("complete", quest_id=92457, quest_name=q[92457]["name"], zone=p.zone)
    for b in DALARAN_BOSSES:
        p.wait(rnd.uniform(90, 160))
        p.kill(b, elite=True)
        p.rec("encounter", difficulty=1, encounter_id=3100 + DALARAN_BOSSES.index(b), size=5, success=True,
              zone=p.zone)
    # The quest's Arcane Mote (251961) isn't in the loot line: Forever's
    # client tables have no icon for it, and the demo shows no question mark.
    p.rec("loot", items="273044:1;273045:1", npc_id=246016, npc_kind="c", **p.here())
    p.rec("progress", have=1, need=1, objective=1, quest_id=92458, **p.here())
    p.rec("complete", quest_id=92458, quest_name=q[92458]["name"], zone=p.zone)
    p.worn[5] = 273044
    p.worn[15] = 273045
    p.die(246008, how="s")
    inside = round(p.t - t0) + 40
    p.wait(t0 + inside - p.t)
    p.instance = None
    p.other("wow-dungeon", dungeon_id=2959, dungeon_kind="party", left=iso(t0 + inside), level=lvl,
            name="City of Dalaran", seconds=inside, xp=p.gained - g0)
    p.out[-1]["_time"] = iso(t0)
    p.go("Alterac Mountains", "Dalaran")
    p.rec("group", raid=False, size=0, zone=p.zone)
    p.step("forever/alliance/30-40/05-arathi-highlands", 65, "auto")
    p.turnin(q[92457], reward=279838)  # into the bags: the staff takes both hands
    p.step("forever/alliance/30-40/05-arathi-highlands", 66, "auto")
    p.turnin(q[92458])
    p.gear_sheet()
    p.logout()

    # 18. Thu 22 Oct: boats to Theramore, Razorfen Kraul at 33. Mortality
    # Wanes stays open (its hand-in is in Darnassus).
    p.rested += 2400
    p.login(local(2026, 10, 22, 19, 10), "Alterac Mountains", "Dalaran")
    p.go("Hillsbrad Foothills", "Southshore")
    p.boat("Wetlands", "Menethil Harbor")
    p.town()
    p.boat("Dustwallow Marsh", "Theramore Isle")
    p.go("The Barrens", "Razorfen Kraul")
    p.rec("group", raid=False, size=5, zone=p.zone)
    p.wait(180)
    p.rec("enter", difficulty=0, instance_id=47, instance_kind="party", **p.here())
    t0, g0, lvl = p.t, p.gained, p.level
    p.go("Razorfen Kraul", "Razorfen Kraul", 50.0, 20.0)
    p.instance = "Razorfen Kraul"
    p.accept(q[1144], 4508)
    p.accept(q[1142], 4510)
    for i in range(14):
        p.wait(rnd.uniform(35, 70))
        p.kill(rnd.choice(RFK_MOBS))
        if i == 6:
            p.rec("loot", items="5825:1", npc_kind="o", **p.here())
            p.rec("progress", have=1, need=1, objective=1, quest_id=1142, **p.here())
            p.rec("complete", quest_id=1142, quest_name=q[1142]["name"], zone=p.zone)
    for b in RFK_BOSSES:
        p.wait(rnd.uniform(80, 140))
        p.kill(b)
        p.rec("encounter", difficulty=1, encounter_id=3200 + RFK_BOSSES.index(b), size=5, success=True,
              zone=p.zone)
        if b == 4428:
            p.rec("loot", items="6685:1;2816:1", npc_id=b, npc_kind="c", **p.here())
            p.worn[3] = 6685
            # The scepter frees the off hand for Dalaran's rod: the staff goes.
            p.worn[16] = 2816
            p.worn[17] = 279838
    p.rec("complete", quest_id=1144, quest_name=q[1144]["name"], zone=p.zone)
    p.turnin(q[1144], reward=6750)
    p.worn[12] = 6750
    inside = round(p.t - t0) + 60
    p.wait(t0 + inside - p.t)
    p.instance = None
    p.other("wow-dungeon", dungeon_id=47, dungeon_kind="party", left=iso(t0 + inside), level=lvl,
            name="Razorfen Kraul", seconds=inside, xp=p.gained - g0)
    p.out[-1]["_time"] = iso(t0)
    p.go("The Barrens", "Razorfen Kraul")
    p.rec("group", raid=False, size=0, zone=p.zone)
    p.go("Dustwallow Marsh", "Theramore Isle")
    p.rec("bind", **p.here())
    p.bind = "Theramore Isle"
    p.gear_sheet()
    p.logout()

    # 19. Fri 23 Oct: back north, Arathi Highlands to 34.
    p.rested += 2800
    p.login(local(2026, 10, 23, 18, 30), "Dustwallow Marsh", "Theramore Isle")
    p.boat("Wetlands", "Menethil Harbor")
    p.guide_steps("forever/alliance/30-40/05-arathi-highlands", 27, 45,
                  {27: ("Arathi Highlands", None), 28: ("Arathi Highlands", "Refuge Pointe"),
                   29: ("Arathi Highlands", "Northfold Manor"), 35: ("Arathi Highlands", "Witherbark Village"),
                   43: ("Arathi Highlands", "Refuge Pointe"), 45: ("Arathi Highlands", "Faldir's Cove")},
                  reach=(34, 30000), deaths=(35,), hours=2.4)
    p.go("Arathi Highlands", "Refuge Pointe")
    p.rec("bind", **p.here())
    p.bind = "Refuge Pointe"
    p.profession(ENCHANTING, 6)
    p.profession(COOKING, 5)
    p.gear_sheet()
    p.logout()

    # 20. Sat 24 Oct, morning: a night at Refuge Pointe's inn banked rested
    # XP; a hand-in, tailoring and the mailbox, no fighting (quest XP leaves
    # rested alone), so it is still there at logout.
    p.rested += 5800
    p.login(local(2026, 10, 24, 10, 40), "Arathi Highlands", "Refuge Pointe")
    p.town()
    p.accept(q[663])  # Land Ho!: a word to the pirates' lookout, and back
    p.wait(300, moving=True)
    p.turnin(q[663])
    p.profession(TAILORING, 45)  # to 140: Black Slippers open (the Tailoring crafts list)
    p.wait(420, moving=False)  # the mailbox, the bank
    p.profession(ENCHANTING, 4)
    p.wait(480, moving=False)
    p.gear_sheet()
    p.logout()
    end_marks["end"] = p.t


def ah_scans(p: Player, days: list[float], upgrade: int, price: int) -> None:
    """Auctionator's scans (feed wow-prices): a full scan on each day, the
    upgrade listed in the last one only."""
    for k, t in enumerate(days):
        day = datetime.fromtimestamp(t, TZ).date()
        midnight = datetime(day.year, day.month, day.day, tzinfo=TZ).timestamp()
        stock = list(AH_STOCK) + ([upgrade] if k == len(days) - 1 else [])
        for item in stock:
            lo = price if item == upgrade else 20 + (item * 37 + k * 11) % 2400
            p.t = midnight
            p.other("wow-prices", item_id=item, item_key=str(item), price_copper=lo,
                    price_high_copper=lo + lo // 4, available=1 + item % 7, scan_day=day.isoformat(),
                    realm=DEMO_REALM, faction="Alliance", source_id=f"price:{DEMO_REALM}:{item}:{day.isoformat()}")


class DemoPlayer(Player):
    """The demo's player: the shipper sends a character sheet whenever the
    outfit or a profession rank changed since the last one (its digest),
    at the next run after a logout, so the gear history has every outfit."""

    def gear_sheet(self) -> None:
        key = (tuple(sorted(self.worn.items())), tuple((s, r) for s, (r, _c) in sorted(self.skills.items())))
        if key == getattr(self, "_sheet", None):
            return
        self._sheet = key
        gear_sheet(self)

    def logout(self) -> None:
        super().logout()
        self.t -= 1
        self.gear_sheet()
        self.t += 1


# The demo character's talents, one point per entry, in the order he took
# them: Discipline while levelling with a group, then a respec into Shadow
# when he reached RESPEC_LEVEL. (WoWSims tree, talent name, max rank.)
DISC_ORDER = [("Wand Specialization", 2), ("Twin Disciplines", 3), ("Mental Strength", 5), ("Inner Focus", 1),
              ("Meditation", 3), ("Martyrdom", 1), ("Improved Power Word: Shield", 3), ("Mental Agility", 2)]
SHADOW_ORDER = [("Spirit Tap", 5), ("Shadow Focus", 3), ("Improved Shadow Word: Pain", 2), ("Improved Mind Blast", 3),
                ("Mind Flay", 1), ("Improved Mind Flay", 2), ("Vampiric Embrace", 1), ("Shadow Weaving", 3),
                ("Dark Inspiration", 3), ("Shadow Reach", 2), ("Blackout", 2)]
RESPEC_LEVEL = 30
# Battle.net's names for the three trees, in its order.
BNET_TREES = ("Discipline", "Shadow Magic", "Holy")
GUILD = "Cogs of Dawnwatch"  # made up, like the realm
LEGACY_PRIEST_25 = 62000  # Legacy challenge: a Priest reaching 25 (the KB's TraitCurrencySource)


@functools.cache
def priest_talents() -> dict[str, dict]:
    """Talent name -> {tree (0-2), digit (its place in WoWSims' string),
    spell, definition}: data/wowsims-talents.json's Priest trees, named
    from the talent catalog the tests keep (tests/data/wowf-talents-*.json)."""
    w = json.loads((ROOT / "data" / "wowsims-talents.json").read_text())["classes"]["PRIEST"]
    cat = json.loads(next((ROOT / "tests" / "data").glob("wowf-talents-*.json")).read_text())
    names = {r["ID"]: r["Name_lang"] for r in cat["SpellName"]}
    defs = {int(r["ID"]): r["SpellID"] for r in cat["TraitDefinition"]}
    out = {}
    for tree, ids in enumerate(w):
        for digit, d in enumerate(ids):
            spell = defs.get(d)
            if spell and names.get(spell):
                out[names[spell]] = {"tree": tree, "digit": digit, "spell": int(spell), "definition": d}
    return out


def talent_build(level: int, shadow: bool) -> dict[str, int]:
    """Talent name -> rank for a level's points (level - 9)."""
    points = max(0, level - 9)
    out: dict[str, int] = {}
    for name, n in SHADOW_ORDER if shadow else DISC_ORDER:
        for _ in range(n):
            if points <= 0:
                return out
            out[name] = out.get(name, 0) + 1
            points -= 1
    return out


def wowsims_string(build: dict[str, int]) -> str:
    """The build as WoWSims Exporter saves it: a digit per talent, "-"
    between the trees, trailing zeros dropped."""
    info = priest_talents()
    trees = [[0] * 20 for _ in range(3)]
    for name, rank in build.items():
        t = info[name]
        trees[t["tree"]][t["digit"]] = rank
    return "-".join("".join(map(str, tr)).rstrip("0") for tr in trees).rstrip("-")


def level_at(rows: list[dict], t: float, first: int = 22) -> int:
    """The character's level at `t`: one past the newest level it finished."""
    done = [int(r["level"]) for r in rows if r["feed"] == "wow-level" and _epoch(r["_time"]) <= t]
    return max(done) + 1 if done else first


def respec_time(rows: list[dict]) -> float:
    """When he reached RESPEC_LEVEL (finished the level before it)."""
    return min(_epoch(r["_time"]) for r in rows if r["feed"] == "wow-level" and int(r["level"]) == RESPEC_LEVEL - 1)


def _epoch(t: str) -> float:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()


def wowsims_events(p: Player) -> None:
    """WoWSims Exporter's saves, as the shipper sends them (wow-talents):
    one when the recording starts, one at each level (a new point spent),
    and the respec."""
    rows = sorted(p.out, key=lambda r: r["_time"])
    first = min(_epoch(r["_time"]) for r in rows if r["feed"] == "wow-recorder")
    respec = respec_time(rows)
    times = [first + 60] + [_epoch(r["_time"]) + 30 for r in rows if r["feed"] == "wow-level"]
    last = None
    for t in sorted(times):
        lv = level_at(rows, t)
        text = wowsims_string(talent_build(lv, t >= respec))
        if text == last:
            continue
        last = text
        p.t = t
        digest = hashlib.sha1(json.dumps([text, lv]).encode()).hexdigest()[:12]
        p.other("wow-talents", source="wowsims", source_id=f"talents:{p.char}:{digest}:{int(t)}",
                _msg=f"{p.char}: talents {text} at level {lv} (WowSims)", talents=text, level=lv,
                **{"class": "PRIEST"}, spec="Shadow" if t >= respec else "Discipline", addon_version="1.2.3")


def shipper_shapes(rows: list[dict]) -> list[dict]:
    """What the shipper adds that the fixtures' events leave out
    (shipper/wow_stats.py): a source_id on every event (the pages count and
    dedupe by it), and each zone record's time in the zone, up to the next
    zone record or the end of the session (a gap over 330 s)."""
    out = [dict(r) for r in rows]
    rec = [r for r in out if r["feed"] == "wow-recorder"]
    ts = [_epoch(r["_time"]) for r in rec]
    # Each record's session end (a gap over 330 s ends one): walk back from the last.
    ends = list(ts)
    stop = ts[-1] if ts else 0
    for k in range(len(ts) - 1, -1, -1):
        if k + 1 < len(ts) and ts[k + 1] - ts[k] > 330:
            stop = ts[k]
        ends[k] = stop
    nxt = None
    for k in range(len(rec) - 1, -1, -1):
        r = rec[k]
        if r.get("kind") == "zone":
            until = ends[k] if nxt is None or ts[nxt] > ends[k] else ts[nxt]
            r["duration_s"] = sval(round(max(0.0, until - ts[k]), 1))
            nxt = k
    seq = idx = 0
    for r in rec:
        if r.get("kind") == "session":
            seq, idx = seq + 1, 0
        idx += 1
        r.setdefault("source_id", f"rec:1:{seq}:{idx}")
    for r in out:
        if "source_id" in r:
            continue
        f, c, t = r["feed"], r.get("char"), _epoch(r["_time"])
        if f == "wow-character":
            r["source_id"] = f"character:{c}:{int(t)}"
        elif f == "wow-level":
            r["source_id"] = f"level:{c}:{r['level']}"
        elif f == "wow-dungeon":
            r["source_id"] = f"dungeon:{c}:{int(t)}"
        elif f == "wow-gear":
            digest = hashlib.sha1(json.dumps([r.get("items"), r.get("professions")]).encode()).hexdigest()[:12]
            r["source_id"] = f"gear:{c}:{digest}"
        elif f == "wow-setup":
            digest = hashlib.sha256(r.get("addons", "").encode()).hexdigest()[:16]
            r["source_id"] = f"setup:{digest}:{int(t)}"
    return out


def build_demo(end_day: str | None = None) -> list[dict]:
    """The demo character's events, shifted by whole days so its last
    session is on `end_day` (YYYY-MM-DD; default: as generated)."""
    from datetime import date

    rnd = random.Random(SEED)
    game = Game()
    # Male (the Recorder's UnitSex 2), as his Battle.net profile says (bnet_series).
    p = DemoPlayer(game, rnd, DEMO, cls="PRIEST", race="Gnome", faction="Alliance", sex=2, level=22, xp=15555,
                   money=21000, bind="Auberdine")
    p.worn[16] = 1312
    # Every slot filled: his own Azure Silk Hood (the Shadow Hood on the
    # auction house beats it), a shirt, the guild's tabard, a second trinket.
    p.worn.update({1: 7048, 4: 2576, 14: 2919, 19: 5976})
    p.fresh_steps = True  # draws nothing from the seed: the same evenings as the fixtures'
    marks = sessions_priest(p)
    sessions_demo(p, marks)
    end = p.t
    # The fixtures' few price rows predate the scan fields (realm, scan_day):
    # the demo's auction house is ah_scans' alone.
    p.out = [r for r in p.out if r["feed"] != "wow-prices"]
    # The upgrade on sale: Shadow-scored, bound when equipped, for the
    # empty head slot.
    ah_scans(p, [end - 86400, end], upgrade=AH_UPGRADE, price=AH_PRICE)
    wowsims_events(p)
    # Questie's list: the hand-ins, and a made-up past before the recording.
    done = sorted(set(p.done) | {qid for qid, x in sorted(game.quests.items())
                                 if x["side"] in ("Alliance", "both") and 1 <= x["level"] <= 20
                                 and random.Random(qid).random() < 0.35})
    p.t = end - 30
    p.other("wow-quests", kind="completed", source="questie", quest_ids=done, n=len(done),
            source_id=f"quests:{DEMO}:questie")
    # The shipper's setup report: every addon but Syndicator, so the pages
    # show one "Missing:" line (the bags), as a friend's would: the demo's
    # one example of a degraded source, said so on its page.
    p.other("wow-setup", addons={"polestar": "0.4.1", "recorder": "0.4.1", "auctionator": "11.0.5",
                                 "questie": "11.18.0", "altoholic": "11.1.0", "wowsims": "1.2.3"},
            recorder_on=True, saved_chars=1)
    p.out[-1].pop("char", None)
    rows = shipper_shapes(sorted(p.out, key=lambda r: r["_time"]))
    if end_day:
        last = datetime.fromtimestamp(end, TZ).date()
        shift = (date.fromisoformat(end_day) - last).days * 86400
        rows = [shifted(r, shift) for r in rows]
    return rows


# ---------------------------------------------------------------- Battle.net

# Battle.net's slot types (the exporter's `slot` label) by inventory slot.
BNET_SLOTS = {1: "head", 2: "neck", 3: "shoulder", 4: "shirt", 5: "chest", 6: "waist", 7: "legs", 8: "feet",
              9: "wrist", 10: "hands", 11: "finger_1", 12: "finger_2", 13: "trinket_1", 14: "trinket_2", 15: "back",
              16: "main_hand", 17: "off_hand", 18: "ranged", 19: "tabard"}
BNET_READ_HOUR = 6  # the exporter's daily read, UTC
BNET_STEP = 120     # seconds between pushes (the exporter pushes every minute)


def bnet_series(rows: list[dict], now: float, days: int = 21, look: list[dict] | None = None) -> list[dict]:
    """The Battle.net exporter's series for the demo character
    (wowstats.exporter.poll_profile), as VictoriaMetrics' /api/v1/import
    takes them: the profile, the look (golden3d's options), the equipment,
    the item level, the talents and the Legacy achievement, each read once
    a day at BNET_READ_HOUR and pushed every BNET_STEP seconds, over the
    last `days` (the talent history reads them back). `rows`: build_demo's,
    already shifted."""
    import golden3d

    who = {"character": DEMO, "account_id": "0"}
    respec = respec_time(rows)
    info = priest_talents()
    gear = sorted((r for r in rows if r["feed"] == "wow-gear"), key=lambda r: r["_time"])
    ding25 = min(_epoch(r["_time"]) for r in rows if r["feed"] == "wow-level" and int(r["level"]) == 24)
    t0 = int((now - days * 86400) // BNET_STEP * BNET_STEP)
    stamps = list(range(t0, int(now), BNET_STEP))
    day = 86400
    read = {t: (t - BNET_READ_HOUR * 3600) // day * day + BNET_READ_HOUR * 3600 for t in stamps}
    series: dict[tuple, dict] = {}

    def put(name: str, labels: dict, t: int, value: float) -> None:
        key = (name, tuple(sorted(labels.items())))
        s = series.setdefault(key, {"metric": {"__name__": name, **labels}, "values": [], "timestamps": []})
        s["values"].append(value)
        s["timestamps"].append(t * 1000)

    # The game files' own option and choice ids: golden3d's, or the real
    # model's (tools/fixtures/demo3d.py look.json) when the demo draws that.
    look = golden3d.build()["look"] if look is None else look
    recent = now - 3 * day  # the profile's series: last_over_time(...[2d]) needs the last two days
    for t in stamps:
        r = read[t]
        lv = level_at(rows, r)
        build = talent_build(lv, r >= respec)
        spent = {name: 0 for name in BNET_TREES}
        for name, rank in build.items():
            spent[BNET_TREES[(0, 2, 1)[info[name]["tree"]]]] += rank
        for k, tree in enumerate(BNET_TREES):
            put("wow_character_talent_points", {**who, "tree": tree, "order": str(k)}, t, spent[tree])
        for name, rank in build.items():
            put("wow_character_talent_rank", {**who, "spell_id": str(info[name]["spell"]), "talent": name}, t, rank)
        if t < recent:
            continue
        put("wow_character_profile", {**who, "race_id": "7", "gender": "male", "in_guild": "true",
                                      "guild": GUILD}, t, lv)
        put("wow_character_profile_found", who, t, 1)
        put("wow_character_profile_refreshed_timestamp_seconds", who, t, r)
        for o in look:
            put("wow_character_customization", {**who, "option_id": str(o["optionId"])}, t, o["choiceId"])
        sheet = [g for g in gear if _epoch(g["_time"]) <= r]
        items = json.loads(sheet[-1]["items"]) if sheet else []
        for it in items:
            put("wow_character_equipped", {**who, "slot": BNET_SLOTS[int(it["slot"])]}, t, int(it["item_id"]))
        ilvls = [int(it["item_level"]) for it in items if it.get("item_level")]
        # Blizzard's equipped item level counts an empty slot as 0 (16 slots).
        put("wow_character_item_level", {**who, "kind": "equipped"}, t, sum(ilvls) // 16)
        put("wow_character_item_level", {**who, "kind": "average"}, t, sum(ilvls) // 16)
        put("wow_character_achievements_read", who, t, 1)
        put("wow_character_achievement", {**who, "achievement_id": str(LEGACY_PRIEST_25)}, t, int(ding25))
    return [s for _k, s in sorted(series.items())]


def shifted(r: dict, secs: int) -> dict:
    """One event moved by `secs` (whole days): its time, and the fields that
    hold a time or a day."""
    from datetime import timedelta

    out = dict(r)
    # `left` is a time on a dungeon run only (a map record's is a coordinate).
    for k in ("_time", "left") if r.get("feed") == "wow-dungeon" else ("_time",):
        if out.get(k):
            out[k] = iso(datetime.fromisoformat(out[k].replace("Z", "+00:00")).timestamp() + secs)
    if out.get("scan_day"):
        old = out["scan_day"]
        new = (datetime.fromisoformat(old) + timedelta(seconds=secs)).date().isoformat()
        out["scan_day"] = new
        out["source_id"] = out["source_id"].replace(old, new)
    return out


# ---------------------------------------------------------------- main


def build() -> dict[str, bytes]:
    rnd = random.Random(SEED)
    game = Game()
    p = Player(game, rnd, PRIEST, cls="PRIEST", race="Gnome", faction="Alliance", sex=3, level=22, xp=15555,
               money=21000, bind="Auberdine")
    p.worn[16] = 1312
    marks = sessions_priest(p)
    rows = sorted(p.out, key=lambda r: r["_time"])
    files: dict[str, bytes] = {}
    files["tests/data/analysis-events.jsonl.gz"] = gz(jsonl([r for r in rows if r["_time"] < iso(marks["four"])]))

    # The Stockade trip: its records, the character snapshots around it, and
    # one event of another character (prep.build keeps only its own).
    lo, hi = iso(marks["stockade_from"] - 60), iso(marks["stockade_logout"] + 5)
    trip = [r for r in rows if lo <= r["_time"] <= hi]
    trip += [r for r in rows if r.get("feed") == "wow-character" and r["_time"] < lo][-1:]
    trip.append({"_time": iso(marks["stockade"] + 600), "char": HUNTER, "component": "wow", "feed": "wow-character",
                 "layer": "app", "level": None, "money_copper": None})
    files["tests/analysis/stockade_trip.jsonl.gz"] = gz(jsonl(sorted(trip, key=lambda r: r["_time"])))

    now = marks["end"] + 4 * 3600
    files["tests/data/plan-inputs.json.gz"] = gz(json.dumps(plan_inputs(rows, PRIEST, now), sort_keys=True).encode())
    files["tests/data/gear-priest.json.gz"] = gz(json.dumps(gear_answer(rows, PRIEST), sort_keys=True).encode())

    h = Player(game, random.Random(SEED + 1), HUNTER, cls="HUNTER", race="Dwarf", faction="Alliance", sex=2, level=1,
               xp=0, money=0, bind="Coldridge Valley")
    h.login(local(2026, 10, 19, 21, 44), "Dun Morogh", "Coldridge Valley")
    h.step("forever/alliance/dwarf/01-dun-morogh", 1, "auto")
    h.wait(30, moving=False)
    h.other("wow-gear", **h.who, item_level=None, items=[], professions=[], spec=None)
    h.logout()
    hrows = sorted(h.out, key=lambda r: r["_time"])
    files["tests/data/plan-inputs-level1.json.gz"] = gz(json.dumps(plan_inputs(hrows, HUNTER, now),
                                                                   sort_keys=True).encode())
    for name, data in addon_saves(random.Random(SEED + 2)).items():
        files[f"tests/data/{name}"] = data
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="compare with the committed files, write nothing")
    ap.add_argument("--levels", action="store_true", help="print each logout's level and XP")
    ap.add_argument("--demo", metavar="FILE", help="write the docs site's demo character's events (NDJSON), "
                                                   "nothing else")
    ap.add_argument("--end-day", metavar="YYYY-MM-DD", help="with --demo: shift the play so it ends that day")
    a = ap.parse_args()
    global VERBOSE
    VERBOSE = a.levels
    if a.demo:
        Path(a.demo).write_bytes(jsonl(build_demo(a.end_day)))
        return 0
    files = build()
    bad = []
    for rel, data in files.items():
        path = ROOT / rel
        if a.check:
            if not path.is_file() or path.read_bytes() != data:
                bad.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"{rel}: {len(data)} bytes")
    if bad:
        print("differ from the generator:", *bad, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
