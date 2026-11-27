"""The eight alts play on the same clock as Nibbet. Their snapshots come from those sessions."""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from functools import cache
from pathlib import Path

import roster
from generate import DEMO_REALM, MAPS, SEED, XP_MAX, Game, Player, _epoch, dungeon, shifted, shipper_shapes

from wowstats import forever, gear
from wowstats.plan import tripcost

# The first level is when this character installed the Recorder. Before it,
# Altoholic has the game's lifetime /played but there are no recorded kills or splits.
ZONES = {
    "WARRIOR": ((55, "Western Plaguelands"), (58, "Eastern Plaguelands")),
    "PALADIN": ((47, "Tanaris"), (50, "Un'Goro Crater")),
    "HUNTER": ((27, "Wetlands"), (30, "Stranglethorn Vale")),
    "ROGUE": ((9, "Dun Morogh"), (11, "Loch Modan")),
    "SHAMAN": ((22, "The Barrens"), (26, "Thousand Needles")),
    "MAGE": ((1, "Durotar"),),
    "WARLOCK": ((16, "Silverpine Forest"), (21, "Hillsbrad Foothills")),
    "DRUID": ((32, "Stranglethorn Vale"), (36, "Dustwallow Marsh")),
}
# A party that cleared these bosses. The trash comes from their actual instance spawns.
RUNS = {
    "WARRIOR": ((56, "Blackrock Depths", (9018, 9024, 9537)), (58, "Stratholme", (10808, 10997, 10811))),
    "PALADIN": ((47, "Zul'Farrak", (8127, 7275, 7604, 7267)), (50, "Maraudon", (13601, 12225, 12201)),
                (52, "Sunken Temple", (5719, 5722, 8443, 5709))),
    "HUNTER": ((31, "Gnomeregan", (7079, 6235, 6229, 7800)), (33, "Scarlet Monastery", (3974, 6487))),
    "ROGUE": ((16, "The Deadmines", (644, 643, 1763)),),
    "SHAMAN": ((23, "Shadowfang Keep", (3887, 3927, 4275)), (25, "Blackfathom Deeps", (4831, 4830, 4829)),
               (29, "Razorfen Kraul", (4424, 4420, 4421))),
    "WARLOCK": ((21, "Shadowfang Keep", (3927, 4274, 4275)), (23, "Blackfathom Deeps", (4832, 4831, 4829))),
    "DRUID": ((37, "Razorfen Downs", (7355, 7357, 7358)), (39, "Uldaman", (6910, 7228))),
}
CAP_RUNS = {
    1: ("Blackrock Depths", (9024, 9938, 9019)),
    2: ("Stratholme", (10436, 10437, 10438, 10440)),
    3: ("Scholomance", (10505, 10504, 1853)),
    5: ("Blackrock Spire", (10430, 10363)),
    6: ("Blackrock Spire", (10430, 10363)),
}
# Drops actually won on these runs. The Blackstone Ring is already worn
# when the warrior starts recording at 55.
LOOT = {
    "WARRIOR": {10808: (13400,), 9019: (11815,), 10440: (22411,), 10504: (18680,), 10363: (13098, 13142)},
    "PALADIN": {8127: (9641,), 12201: (17713,), 5719: (12465,)},
    "HUNTER": {7079: (5002,), 3974: (7710,)},
    "SHAMAN": {3887: (6321,), 3927: (6314,), 4830: (6902,), 4420: (6687,)},
    "ROGUE": {1763: (10402,)},
    "WARLOCK": {3927: (3748,), 4274: (6340, 5001), 4275: (6392,)},
    "DRUID": {7228: (9408,)},
    "MAGE": {276105: (286754,)},
}
# Hand-ins outside the dungeon are recorded after the party leaves.
REWARDS = {("HUNTER", "Gnomeregan"): (2929, 9624), ("PALADIN", "Sunken Temple"): (3528, 10749),
           ("PALADIN", "Zul'Farrak"): (2768, 9533), ("WARLOCK", "Blackfathom Deeps"): (1740, 15109)}
PROFESSIONS = {
    "WARRIOR": ((186, "Mining"), (164, "Blacksmithing")),
    "PALADIN": ((186, "Mining"), (164, "Blacksmithing")),
    "HUNTER": ((393, "Skinning"), (165, "Leatherworking")),
    "ROGUE": ((393, "Skinning"), (165, "Leatherworking")),
    "SHAMAN": ((182, "Herbalism"), (171, "Alchemy")),
    "MAGE": ((197, "Tailoring"), (333, "Enchanting")),
    "WARLOCK": ((197, "Tailoring"), (333, "Enchanting")),
    "DRUID": ((393, "Skinning"), (165, "Leatherworking")),
}


class RosterPlayer(Player):
    def trash_drop(self) -> int:
        # Ordinary loot follows the level of the encounter (no runecloth in Durotar).
        return next(item for ceiling, item in ((15, 2589), (25, 2592), (39, 4306), (50, 4338), (60, 14047))
                    if self.level <= ceiling)

    def kill_xp(self, lv: int, cl: str) -> int:
        xp = super().kill_xp(lv, cl)
        return round(xp * 1.4 / 5) if self.instance else xp

    def snapshot(self, *, logged_out=True, **extra):
        super().snapshot(realm=DEMO_REALM, **extra)
        self.other("wow-character", kind="played", source="altoholic", level=self.level, logged_out=logged_out,
                   played_total_s=round(self.played), played_level_s=round(self.played - self.level_t0),
                   source_id=f"played:{self.char}:{round(self.played)}")


@cache
def instance_spawns():
    source = Path(__file__).resolve().parents[2] / "data/dungeons/kill-xp.json"
    return json.loads(source.read_text())["dungeons"]


def group_run(p, c, name, bosses):
    ref = instance_spawns()[name]
    mobs = []
    wing = {"Blackrock Spire": "Upper", "Stratholme": "Undead" if 10440 in bosses else "Live",
            "Scarlet Monastery": "Library", "Maraudon": "Inner"}.get(name)
    floor = ref["wings"][wing] if wing else ref
    for npc, _, lo, hi, elite, boss, count in floor["mobs"]:
        if not boss and elite and count >= 2 and lo <= p.level + 3 and hi >= p.level - 6:
            mobs.append(npc)
        # These levels come from the dungeon, including bosses missing from quest objectives.
        p.g.npc_level[npc] = (hi, "e" if elite else "n")
    before = p.zone
    entrance = p.g.ref.places["entrances"][str(ref["map"])]
    zone = next(z for z, map_id in MAPS.items() if map_id == entrance["zone"])
    p.wait(p.rnd.randint(180, 420) if zone == before else p.rnd.randint(700, 1300))
    p.go(zone, name, entrance["x"], entrance["y"])
    loot = {}
    reward = REWARDS.get((c.cls, name))
    if c.cls == "WARLOCK" and name == "Shadowfang Keep":
        p.go("The Barrens")
        p.accept(p.g.quests[1740], 6247)
        p.wait(900)
        p.go(zone, name, entrance["x"], entrance["y"])
    if reward and reward[0] != 1740:
        p.accept(p.g.quests[reward[0]])
    slots = {it["item_id"]: it["slot"] for it in roster.worn(c)}
    for boss in bosses:
        for iid in LOOT[c.cls].get(boss, ()):
            if iid not in p.worn.values():
                loot.setdefault(boss, {})[slots[iid]] = iid
                if boss == 10363:  # the ring on the first run and the belt on the second
                    break
    fragments = ()
    if 1740 in p.log:
        if name == "Shadowfang Keep":
            fragments = ((1740, 3855, 6915, 1),)
        elif name == "Blackfathom Deeps":
            fragments = ((1740, 4809, 6914, 3),)
    if reward and reward[0] in (3528, 2768):
        boss = 8443 if reward[0] == 3528 else 7604
        loot.setdefault(boss, {})[0] = p.g.quests[reward[0]]["items"][0][0]
    dungeon(p, name, ref["map"], name, mobs, bosses, trash=p.rnd.randint(22, 34), loot=loot,
            party_size=10 if name == "Blackrock Spire" else 5, quest_loot=fragments)
    p.go(zone, name, entrance["x"], entrance["y"])
    p.wait(p.rnd.randint(150, 450))
    p.go(before)
    if reward:
        quest = p.g.quests[reward[0]]
        giver = p.g.ref.places["npc"].get(str(quest["ends"]))
        if giver:
            handin = next(z for z, map_id in MAPS.items() if map_id == giver[0])
            p.go(handin, x=giver[1], y=giver[2])
        p.rec("complete", quest_id=quest["id"], quest_name=quest["name"], zone=p.zone)
        p.turnin(quest, reward=reward[1])
        if p.zone != before:
            p.wait(900)
            p.go(before)


@cache
def quest_pool(zone, level, cls):
    c = next(c for c in roster.CHARACTERS if c.cls == cls)
    available = game().zone_quests(zone, level, set(), c.faction)
    unavailable = forever.data()["unavailable_quests"]
    return [q for q in available if q["id"] not in unavailable
            and tripcost.eligible(*forever.quest_gates(q["id"]), tripcost.SIDE_BIT[c.faction],
                                  tripcost.race_bit(c.race, c.faction), tripcost.CLASS_BIT[cls])
            and not forever.quest_dungeon(q["id"])]


@cache
def game():
    g = Game()
    # The frozen area-based quest fixture skips class quests without an area.
    # The same Polestar forever-db quest, objectives and reward records have 1740.
    g.quests[1740] = {"id": 1740, "name": forever.quest(1740)["name"], "level": 25, "min": 20, "area": 0,
                      "side": "both", "xp": 5100, "money": 0, "starts": 6247, "ends": 6247, "kills": [],
                      "items": [(6914, 3), (6915, 1)], "choices": [6898, 15109]}
    return g


@cache
def turnin_steps(c):
    # Match the actual quest handed in to its Polestar step; a player can
    # select a different guide, or quest without a guide entirely.
    by_quest = defaultdict(list)
    for guide, rows in game().ref.turnins.items():
        cfg = game().ref.guides.get(guide)
        if not cfg or cfg["faction"] not in (None, c.faction):
            continue
        parts = guide.split("/")
        starts = {"dwarf", "gnome", "troll", "orc", "undead", "night-elf", "tauren", "human"}
        if len(parts) > 2 and parts[2] in starts and parts[2] != c.race.lower().replace(" ", "-"):
            continue
        for qid, step, level, _ in rows:
            by_quest[qid].append((level, guide, step))
    return by_quest


def outdoor(p, c):
    zone = next(z for level, z in reversed(ZONES[c.cls]) if p.level >= level)
    if zone != p.zone:
        p.wait(p.rnd.randint(180, 420))
        p.go(zone)
    mobs = [n for n in p.g.zone_mobs(zone, p.level) if p.g.npc_level[n][1] == "n"
            and p.level - 5 <= p.g.npc_level[n][0] <= p.level + 3]
    if not mobs and zone == "Durotar" and p.level < 5:
        mobs = [3098, 3101]  # Mottled Boar and Vile Familiar in the Valley of Trials
    if not mobs:
        raise ValueError(f"No outdoor mobs for {c.name}, {zone}, level {p.level}")
    qs = []
    for q in quest_pool(zone, p.level, c.cls):
        all_of, any_of = forever.data()["pre"].get(str(q["id"]), ([], []))
        if (q["id"] not in p.used and all(n in p.used for n in all_of)
                and (not any_of or any(n in p.used for n in any_of))
                and all(n in p.g.npc_level and p.g.npc_level[n][1] == "n"
                        and p.g.npc_level[n][0] <= p.level + 3 for n, _ in q["kills"])):
            qs.append(q)
    if qs and p.rnd.random() < 0.55:
        q = p.rnd.choice(qs)
        p.accept(q)
        p.wait(p.rnd.randint(45, 150))
        for i, (npc, need) in enumerate(q["kills"], 1):
            for j in range(1, need + 1):
                p.wait(p.rnd.randint(9, 28))
                p.kill(npc, quest=q["id"])
                p.rec("progress", quest_id=q["id"], objective=i, have=j, need=need, **p.here())
        for i, (item, count) in enumerate(q["items"], len(q["kills"]) + 1):
            p.wait(p.rnd.randint(15, 65))
            p.rec("loot", items=f"{item}:{count}", wanted=p.wanted(), **p.here())
            p.rec("progress", quest_id=q["id"], objective=i, have=count, need=count, **p.here())
        if not q["kills"] and not q["items"]:
            p.wait(p.rnd.randint(60, 200))
        if steps := turnin_steps(c).get(q["id"]):
            _, guide, step = min(steps, key=lambda s: abs(s[0] - p.level))
            p.step(guide, step, "manual")
        p.rec("complete", quest_id=q["id"], quest_name=q["name"], zone=zone)
        p.turnin(q)
    else:
        p.wait(p.rnd.randint(8, 35))
        p.kill(p.rnd.choice(mobs))


def save(p, c, outfit, final, cap_sessions):
    level = p.level
    cap = next(cap for until, cap in ((9, 75), (19, 150), (34, 225), (60, 300)) if level <= until)
    profs = [{"name": name, "skill_id": skill, "rank": min(cap, level * 5 + cap_sessions)}
             for skill, name in PROFESSIONS[c.cls] if level >= 5]
    # An item with no equip level can still be locked behind a quest or a boss.
    items = []
    earned = {iid for ids in LOOT[c.cls].values() for iid in ids}
    rewards = {iid: qid for (cls, _), (qid, iid) in REWARDS.items() if cls == c.cls}
    for it in outfit:
        if it["item_id"] in earned and it["item_id"] not in p.worn.values():
            continue
        if it["item_id"] in rewards and rewards[it["item_id"]] not in p.done:
            continue
        src = gear.reference().data["sources"].get(str(it["item_id"]), [])
        gates = [(forever.quest(s["quest"]) or {}).get("min_level") or 0 if s.get("quest") else 0 for s in src]
        if level < max(it["req_level"] or 1, min(gates, default=1)):
            continue
        if it["quality"] >= 4 and c.level == 60 and cap_sessions < (it["slot"] % 4) + 1:
            continue
        items.append(it)
        craft = next((s for s in src if s["kind"] == "craft" and any(pr["name"] == s.get("profession")
                      and pr["rank"] >= s.get("skill", 0) for pr in profs)), None)
        if craft and it["item_id"] not in p.crafted:
            p.rec("craft", count=1, item_id=it["item_id"], spell_id=craft["spell"], zone=p.zone)
            p.crafted.add(it["item_id"])
    if c.cls == "DRUID" and level >= 38 and not p.rep_saved:
        p.rec("rep", faction_id=2799, reaction=5, standing=3100, zone=p.zone)
        p.rep_saved = True
    if final and len(items) != len(outfit):
        raise ValueError(f"{c.name} cannot wear the final outfit at level {level}")
    by_slot = {} if final else {it["slot"]: it for it in roster.starter_gear(c)}
    by_slot.update({it["slot"]: it for it in items})
    items = [by_slot[slot] for slot in sorted(by_slot)]
    p.other("wow-gear", **p.who, spec=c.spec, items=items, professions=profs,
            item_level=round(sum(it["item_level"] for it in items) / len(items), 1))
    talent = roster.talent_string(c, level)
    if talent and talent != getattr(p, "last_talent", None):
        p.other("wow-talents", source="wowsims", talents=talent, level=level, **{"class": c.cls}, spec=c.spec,
                addon_version="1.2.3", source_id=f"talents:{c.name}:{talent}")
        p.last_talent = talent
    equipped = {it["item_id"] for it in items}
    p.held.update(p.last_equipped - equipped)
    for event in p.out:
        if event.get("kind") == "loot":
            p.held.update(int(part.split(":")[0]) for part in event["items"].split(";"))
        elif event.get("kind") == "turnin":
            q = p.g.quests[int(event["quest_id"])]
            p.held.difference_update(iid for iid, _ in q["items"])
            if event.get("item"):
                p.held.add(int(event["item"]))
    p.held.difference_update(equipped)
    p.last_equipped = equipped
    p.other("wow-bags", bags=sorted(p.held), source_id=f"bags:{c.name}:{round(p.played)}")
    if final:
        completed = set(p.done)
        # Questie also knows the quests that awarded the equipment, including pre-Recorder quests.
        for it in items:
            sources = gear.sources_data()["items"].get(str(it["item_id"]), {}).get("sources", [])
            if sources and all(s["kind"] == "quest" for s in sources):
                candidates = [s["quest"] for s in sources if tripcost.eligible(*forever.quest_gates(s["quest"]),
                              tripcost.SIDE_BIT[c.faction], tripcost.race_bit(c.race, c.faction),
                              tripcost.CLASS_BIT[c.cls])]
                if candidates:
                    completed.add(candidates[0])
        p.other("wow-quests", kind="completed", source="questie", quest_ids=sorted(completed), n=len(completed),
                source_id=f"quests:{c.name}:questie")
        book = roster.spellbook(c)
        body = {"class": c.cls, "spells": book}
        digest = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
        known = sum(level == 0 for _, level in book)
        p.other("wow-altoholic", kind="spells", source="altoholic", **body, known=known, future=len(book) - known,
                source_id=f"altoholic:spells:{c.name}:{digest}")
        if c.cls in ("HUNTER", "WARLOCK"):
            p.other("wow-companion", kind=c.cls.lower(), display_id=roster.BEAR_DISPLAY if c.cls == "HUNTER" else 1132,
                    source_id=f"companion:{c.name}:{round(p.played)}")


def story(c, i):
    g = game()
    rnd = random.Random(SEED + i)
    first, zone = ZONES[c.cls][0]
    # This pre-Recorder stretch exists only as the opening /played reading.
    prior = sum(round(g.ref.level(n, c.faction, c.cls)["expected_s"] * rnd.uniform(1.25, 1.8))
                for n in range(1, first))
    money = 10_870_000 if c.cls == "WARRIOR" else first ** 3 * rnd.randint(4, 9)
    p = RosterPlayer(g, rnd, c.name, cls=c.cls, race=c.race, faction=c.faction, sex=c.sex,
                     level=first, xp=0, money=money, bind=c.inn)
    p.held = set()
    p.last_equipped = set()
    p.crafted = set()
    p.rep_saved = False
    p.fresh_steps = True
    p.played = p.level_t0 = prior
    p.first_level = False  # the Recorder was on when this level began
    target = XP_MAX[c.level] * rnd.uniform(0.28, 0.51)
    runs = list(RUNS.get(c.cls, ()))
    outfit = roster.worn(c)
    parts = []
    cap_sessions = 0
    while True:
        p.out = []
        p.login(1_700_000_000 + len(parts) * 86400, zone)
        if not parts:
            save(p, c, outfit, False, 0)
        seconds = rnd.randint(1900, 4200) if c.level <= 16 else rnd.randint(3200, 7600)
        until = p.t + seconds
        p.rested = min(XP_MAX[p.level] // 2, p.rested + round(XP_MAX[p.level] * rnd.uniform(0.08, 0.22)))
        if p.level == 60:
            cap_sessions += 1
            if cap_sessions == 1:
                p.money -= 9_000_000  # the wolf and fast riding, from savings before the recording
            if run := CAP_RUNS.get(cap_sessions):
                runs.append((60, *run))
        while p.t < until:
            if runs and p.level >= runs[0][0]:
                _, name, bosses = runs.pop(0)
                group_run(p, c, name, bosses)
            elif c.cls == "MAGE" and p.level == 10 and 286754 not in p.worn.values():
                npc = 276105  # Stormherald Ukta
                p.g.npc_level[npc] = (12, "n")
                p.rec("group", raid=False, size=2, zone=p.zone)
                p.rec("rare", classification="r", npc_id=npc, level=12, **p.here())
                p.kill(npc)
                p.rec("loot", items="286754:1", npc_id=npc, npc_kind="c", **p.here())
                p.worn[16] = 286754
                p.rec("group", raid=False, size=0, zone=p.zone)
            elif c.level != 60 and p.level == c.level and p.xp >= target and not runs:
                break
            else:
                outdoor(p, c)
            if rnd.random() < 0.005:
                p.died_here()
        final = (cap_sessions >= 6 if c.level == 60 else p.level == c.level and p.xp >= target and not runs)
        if p.level > c.level or len(parts) > 150:
            raise ValueError(f"{c.name} overshot level {c.level} or did not finish")
        zone = p.zone
        if final:
            # A /reload after training gives DataStore a /played reading at
            # this level even when the last outdoor session included a ding.
            p.snapshot(logged_out=False)
            p.chunk(login=False)
            p.wait(rnd.randint(120, 360), moving=False)
        p.logout()
        p.t -= 1
        save(p, c, outfit, final, cap_sessions)
        parts.append(p.out)
        if final:
            return parts


def histories(last, priest):
    # Give each login its own slot on the shared account, working backward from
    # the priest's latest save. Never put an alt in the middle of a Nibbet session.
    reserved = []
    for r in priest:
        if r.get("kind") == "session" and r.get("login") == "true":
            t = _epoch(r["_time"])
            reserved.append((t, t))
        elif r.get("feed") == "wow-character" and r.get("kind") != "played" and reserved:
            reserved[-1] = (reserved[-1][0], _epoch(r["_time"]))
    parts = [story(c, i) for i, c in enumerate(roster.CHARACTERS, 1)]
    assigned = defaultdict(list)
    end = last - 3600
    while any(parts):
        for i, sessions in enumerate(parts):
            if not sessions:
                continue
            records = sessions.pop()
            start, stop = _epoch(records[0]["_time"]), _epoch(records[-1]["_time"])
            duration = stop - start
            # Three possible sessions a day, with gaps for lunch, work and sleep.
            while any(a < end and b > end - duration for a, b in reserved):
                end -= 8 * 3600
            assigned[i].append([shifted(r, end - stop) for r in records])
            end -= 8 * 3600
    rows = []
    for i in range(len(parts)):
        ordered = sorted((r for session in reversed(assigned[i]) for r in session), key=lambda r: r["_time"])
        seen = set()
        for row in shipper_shapes(ordered):
            if row["feed"] == "wow-recorder":
                row["source_id"] = row["source_id"].replace("rec:1:", f"rec:{i + 2}:")
            if row["source_id"] not in seen:
                rows.append(row)
                seen.add(row["source_id"])
    return rows
