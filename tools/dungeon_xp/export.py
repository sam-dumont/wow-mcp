"""Writes the dungeon kill-XP data: who a run kills, at what level, elite or
not, so wowstats.plan.dungeonxp can estimate a dungeon the player hasn't run.

Two outputs, under two licences, never merged (THIRD_PARTY.md):

- data/dungeons/kill-xp.json (GPL-3.0-only, like the program): the
  original dungeons' creatures from cMaNGOS classic-db (GPL-3.0, pinned
  below), the new Forever dungeons' levels and kills per logged run from
  EG Link (CC BY 4.0, credited in the file), and the reference runs that
  calibrate the model (measured on one character, anonymised: level, party
  size, kill XP and time only).
- data/dungeons/forever-spawns/forever-spawns.json (GPL-2.0-only, its own
  LICENSE and README next to it): what advocaite/TrinityCore's forever
  branch alone gives for the new dungeons, the creatures' rank (elite or
  not) and the placed spawn count. The program reads it at run time; none
  of it is copied into the GPL-3.0 file or the code. A row that branch
  copied from EG Link counts as EG Link, one from Everlake as Everlake.

Inputs, both pinned:

- the cMaNGOS classic-db full dump at commit CMANGOS_COMMIT
  (Full_DB/ClassicDB_1_12_1_z2815.sql.gz, about 13 MB):
      curl -LO https://github.com/cmangos/classic-db/raw/<commit>/Full_DB/ClassicDB_1_12_1_z2815.sql.gz
- the research rosters of the nine new dungeons (one JSON per dungeon, each
  value with its source, commit and agreement; built from EG Link @5d76a9e5,
  advocaite/TrinityCore @25be61f5 and Everlake @685c5296).

    uv run python tools/dungeon_xp/export.py --cmangos ClassicDB.sql.gz --rosters rosters/
    uv run python tools/dungeon_xp/export.py --cmangos ... --rosters ... --check   # exit 1 when the files differ

Stdlib only, the same bytes on every run.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "dungeons" / "kill-xp.json"
OUT_FOREVER = ROOT / "data" / "dungeons" / "forever-spawns" / "forever-spawns.json"

CMANGOS_REPO = "https://github.com/cmangos/classic-db"
CMANGOS_COMMIT = "28ef6259c782928b08a8dc9cacf6bc02e64f2b29"
CMANGOS_FILE = "Full_DB/ClassicDB_1_12_1_z2815.sql.gz"
EG = {"repo": "https://github.com/JIVESCORP/eg-link-output-wowf",
      "commit": "5d76a9e5bbba33b97a98774103ccc607ad2463c1", "licence": "CC BY 4.0",
      "credit": "Data from Efficient Games (https://efficient.games), licensed under CC BY 4.0 "
                "(https://creativecommons.org/licenses/by/4.0/)."}
ADVOCAITE = {"repo": "https://github.com/advocaite/TrinityCore", "branch": "forever",
             "commit": "25be61f58f48b6e7bc8eda1146af8e1c73c82470", "licence": "GPL-2.0-only"}
AS_OF = "2026-11-23"

# The original dungeons: forever-reference.json's areas with a map under the
# new dungeons' ids. A dungeon run in wings gets one estimate per wing, each
# creature in the wing of the nearest boss spawn (cMaNGOS has no wing column).
WINGS = {
    189: {"Graveyard": ["Interrogator Vishas", "Bloodmage Thalnos"],
          "Library": ["Houndmaster Loksey", "Arcanist Doan"],
          "Armory": ["Herod"],
          "Cathedral": ["High Inquisitor Fairbanks", "Scarlet Commander Mograine", "High Inquisitor Whitemane"]},
    429: {"East": ["Lethtendris", "Alzzin the Wildshaper", "Hydrospawn", "Zevrim Thornhoof"],
          "West": ["Tendris Warpwood", "Illyanna Ravenoak", "Magister Kalendris", "Immol'thar",
                   "Prince Tortheldrin"],
          "North": ["Guard Mol'dar", "Stomper Kreeg", "Guard Fengus", "Guard Slip'kik", "Captain Kromcrush",
                    "King Gordok"]},
    229: {"Lower": ["Highlord Omokk", "Shadow Hunter Vosh'gajin", "War Master Voone", "Mother Smolderweb",
                    "Urok Doomhowl", "Quartermaster Zigris", "Overlord Wyrmthalak"],
          "Upper": ["Pyroguard Emberseer", "Solakar Flamewreath", "Goraluk Anvilcrack", "Warchief Rend Blackhand",
                    "The Beast", "General Drakkisath"]},
    329: {"Live": ["Timmy the Cruel", "Malor the Zealous", "Cannon Master Willey", "Archivist Galford",
                   "Balnazzar"],
          "Undead": ["Baroness Anastari", "Nerub'enkan", "Maleki the Pallid", "Magistrate Barthilas",
                     "Ramstein the Gorger", "Baron Rivendare"]},
}
# Upper Blackrock Spire was a 10-player raid in Classic: its own party size.
WING_PARTY = {(229, "Upper"): 10}

# Creatures that pay no XP or that nobody fights: critters, totems,
# non-combat pets, gas clouds; flagged unselectable or not attackable;
# flagged no-XP; friendly (gossip, quests, vendors); rares (Rank 2 rare
# elite, 4 rare), which are up on some runs only. A boss of the map's
# encounter list stays whatever its flags.
NO_XP_TYPES = {8, 11, 12, 13}
UNIT_NOT_ATTACKABLE = 0x2 | 0x2000000
EXTRA_NO_XP = 0x40


# ------------------------------------------------------------------ cMaNGOS


def _sql(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _cols(txt: str, table: str) -> list[str]:
    m = re.search(rf"CREATE TABLE `{table}` \((.*?)\) ENGINE", txt, re.S)
    return re.findall(r"^\s*`(\w+)`", m.group(1), re.M) if m else []


def _rows(txt: str, table: str):
    cols = _cols(txt, table)
    for m in re.finditer(rf"^INSERT INTO `{table}` VALUES (.*?);$", txt, re.M | re.S):
        for r in re.finditer(r"\(((?:'(?:[^'\\]|\\.)*'|[^()'])*)\)", m.group(1)):
            vals = [v[1:-1].replace("\\'", "'") if v.startswith("'") else v
                    for v in re.findall(r"'(?:[^'\\]|\\.)*'|[^,]+", r.group(1))]
            yield dict(zip(cols, vals, strict=False))


def cmangos(path: Path, maps: dict[int, str]) -> dict[str, dict]:
    txt = _sql(path)
    tmpl = {int(r["Entry"]): r for r in _rows(txt, "creature_template")}
    bosses_by_map: dict[int, set[str]] = {}
    for r in _rows(txt, "instance_dungeon_encounters"):
        names = {r[k] for k in r if k.startswith("EncounterName") and r[k] and r[k] != "NULL"}
        bosses_by_map.setdefault(int(r["MapId"]), set()).update(names)
    entries: dict[int, list[int]] = {}
    for r in _rows(txt, "creature_spawn_entry"):
        entries.setdefault(int(r["guid"]), []).append(int(r["entry"]))
    pool_of, chance = {}, {}
    for r in _rows(txt, "pool_creature"):
        pool_of[int(r["guid"])] = int(r["pool_entry"])
        chance[int(r["guid"])] = float(r["chance"])
    limit = {int(r["entry"]): int(r["max_limit"]) for r in _rows(txt, "pool_template")}
    mother = {int(r["pool_id"]): int(r["mother_pool"]) for r in _rows(txt, "pool_pool")}
    spawns = [r for r in _rows(txt, "creature") if int(r["map"]) in maps]
    members: dict[int, list[int]] = {}
    for s in spawns:
        p = pool_of.get(int(s["guid"]))
        if p is not None:
            members.setdefault(p, []).append(int(s["guid"]))
    children: dict[int, int] = {}
    for m in mother.values():
        children[m] = children.get(m, 0) + 1

    def weight(guid: int) -> float:
        """The expected number of creatures at this spawn point: a pool spawns
        max_limit of its members (0: all of them), each by its chance, the
        members without one sharing what is left; a pool inside a mother pool
        is one of its children."""
        p = pool_of.get(guid)
        if p is None:
            return 1.0
        if not limit.get(p):
            w = 1.0
        else:
            ms = members[p]
            fixed = sum(chance[g] for g in ms)
            free = [g for g in ms if not chance[g]]
            c = chance[guid] if chance[guid] else max(0.0, 100 - fixed) / len(free)
            w = min(1.0, limit[p] * c / 100)
        m = mother.get(p)
        if m is not None and limit.get(m):
            w *= min(1.0, limit[m] / children[m])
        return w

    def fights(t: dict, bosses: set[str]) -> bool:
        if int(t["CreatureType"]) in NO_XP_TYPES or int(t["UnitFlags"]) & UNIT_NOT_ATTACKABLE \
                or int(t["ExtraFlags"]) & EXTRA_NO_XP or int(t["Faction"]) == 35:
            return False
        # A boss can carry gossip (Fairbanks) or a rare's rank (VanCleef): the
        # encounter list wins.
        return t["Name"] in bosses or not (int(t["NpcFlags"]) or int(t["Rank"]) in (2, 4))

    out: dict[str, dict] = {}
    for mid, name in sorted(maps.items()):
        bosses = bosses_by_map.get(mid, set())
        rows: dict[int, dict] = {}
        for s in spawns:
            if int(s["map"]) != mid:
                continue
            g = int(s["guid"])
            ids = [int(s["id"])] if int(s["id"]) else entries.get(g, [])
            ts = [tmpl[i] for i in ids if i in tmpl]
            ts = [t for t in ts if fights(t, bosses)]
            if not ts:
                continue
            w = weight(g) * len(ts) / len(ids)
            for t in ts:
                e = int(t["Entry"])
                r = rows.setdefault(e, {"id": e, "name": t["Name"], "lmin": int(t["MinLevel"]),
                                        "lmax": int(t["MaxLevel"]), "elite": int(t["Rank"]) in (1, 3),
                                        "boss": t["Name"] in bosses or int(t["Rank"]) == 3, "n": 0.0,
                                        "at": []})
                r["n"] += w / len(ts)
                r["at"].append((float(s["position_x"]), float(s["position_y"]), float(s["position_z"]), w / len(ts)))
        for r in rows.values():
            # A boss placed at several points (Targorr, Dextren) is up at one.
            if r["boss"]:
                r["at"] = [(*a[:3], a[3] / r["n"]) for a in r["at"]]
                r["n"] = 1.0
        out[name] = {"map": mid, "rows": rows}
    return out


def _mob(r: dict, n: float) -> list:
    return [r["id"], r["name"], r["lmin"], r["lmax"], int(r["elite"]), int(r["boss"]), round(n, 2)]


def split_wings(mid: int, rows: dict[int, dict]) -> dict[str, list]:
    """Each creature's spawns to the wing of the nearest boss spawn."""
    anchors = []
    for wing, names in WINGS[mid].items():
        for r in rows.values():
            if r["name"] in names:
                anchors += [(wing, a[:3]) for a in r["at"]]
    out: dict[str, dict[int, float]] = {w: {} for w in WINGS[mid]}
    for r in rows.values():
        for *xyz, w in r["at"]:
            wing = min(anchors, key=lambda a: math.dist(a[1], xyz))[0]
            out[wing][r["id"]] = out[wing].get(r["id"], 0.0) + w
    return {wing: [_mob(rows[i], n) for i, n in sorted(ids.items()) if n >= 0.005] for wing, ids in out.items()}


# ------------------------------------------------------------------ Forever rosters

# The research verdicts (rosters/summary.md): two of the nine have enough
# logged runs for an estimate; City of Dalaran's comes from the reference
# runs below; the rest say what would unlock one.
FOREVER = {
    "Ruins of Lordaeron": {"label": "estimate"},
    "The Drowned City": {"label": "estimate"},
    "City of Dalaran": {"label": "estimate"},
    "The Hall of Thanes": {"label": "not_enough_data",
                           "unlock": "One logged run so far; two more logged runs would give a spread."},
    "Excavation Site: Wetlands": {"label": "not_enough_data",
                                  "unlock": "The four logs look like one run seen by several players; two more "
                                            "runs from other groups."},
    "Krol'dok Stronghold": {"label": "not_enough_data",
                            "unlock": "Half the XP is in waves that grow with time spent; three more runs with "
                                      "their duration, to count waves per minute."},
    "Alcaz Prison": {"label": "not_enough_data", "unlock": "Two runs by a single player; runs from a second player."},
    "Blackmaw Hold": {"label": "not_enough_data", "unlock": "No logged run yet; any logged run."},
    "Shaper's Terrace": {"label": "not_enough_data", "unlock": "No logged run yet; any logged run."},
}
SLUGS = {"Ruins of Lordaeron": "ruins-of-lordaeron", "The Drowned City": "the-drowned-city",
         "City of Dalaran": "city-of-dalaran"}
# Killed by script, not placed (rosters/summary.md): never in a run's XP.
NOT_TRASH = {269133}  # Dalaran's necromancer Skeletons: summons, their XP unmeasured
ADV_GROUPS = ("ADV-SNIFF", "ADV-CACHE", "ANOCORE")


def _rank(n: dict) -> tuple[str | None, str | None, str | None]:
    """The rank and where it comes from: advocaite's own rows first (sniff,
    players' caches, AnoCore), else the health check on EG's numbers."""
    for s in n.get("rank_sources") or []:
        if s.get("group") in ADV_GROUPS:
            return s["value"], s["group"], s.get("url_or_commit")
    r = n.get("rank") or ""
    if "derived from health" in r:
        return r.split(" ")[0], "health", None
    return None, None, None


def forever(rosters: Path) -> tuple[dict, dict]:
    """(the GPL-3.0 part: EG levels and kills per logged run; the GPL-2.0
    part: advocaite's ranks and placed counts), per dungeon."""
    eg, adv = {}, {}
    for name, slug in SLUGS.items():
        d = json.loads((rosters / f"{slug}.json").read_text())
        census = any(s.get("group") in ("ADV-SNIFF", "ANOCORE")
                     for n in d["npcs"] for s in n.get("count_sources") or [])
        mobs, ranks = [], []
        for n in d["npcs"]:
            if n.get("id") is None or n.get("level_min") is None or n["id"] in NOT_TRASH:
                continue
            rank, group, url = _rank(n)
            if rank is None or rank == "critter":
                continue
            count = next((s["value"] for s in n.get("count_sources") or []
                          if s.get("group") in ("ADV-SNIFF", "ANOCORE") and isinstance(s.get("value"), int | float)),
                         None)
            boss = bool(n.get("boss_encounter"))
            if boss and not count:
                count = 1
            kills = n.get("kills_per_run")
            # Where a census exists, a creature it doesn't place is summoned
            # (its XP unmeasured): out of the logged runs too.
            # and a creature killed more often than it is placed rises again or
            # comes in waves (the Ruins' grave Ghouls): its placed count only.
            if kills and not (census and not count):
                mobs.append([n["id"], n["name"], n["level_min"], n["level_max"], int(boss),
                             round(min(kills, count) if census else kills, 2)])
            ranks.append([n["id"], "elite" if boss or rank.startswith("elite") else "normal", group,
                          count if census else None])
        eg[name] = sorted(mobs)
        adv[name] = {"census": census, "ranks": sorted(ranks, key=lambda r: r[0])}
    return eg, adv


# ------------------------------------------------------------------ reference runs

# Measured on one character (anonymised: level, party size, kill XP inside
# and time inside). Kill XP is the XP bar's gain inside minus hand-ins inside
# (#182), rested bonus taken out through the rested pool's drain; the
# sleeping bag (+3%) is left in, as the player had it.
REFERENCE = {
    "The Stockade": {"what": "full_clear", "runs": [
        {"level": 31, "party": 5, "kill_xp": [2738, 2850], "inside_s": 1292, "kills": 96}]},
    # Both runs killed every boss; the trash was cleared once across the two
    # (the second visit finished it).
    "City of Dalaran": {"what": "runs", "bosses_each_run": True, "trash_once_across": True, "runs": [
        {"level": 32, "party": 5, "kill_xp": 6870, "inside_s": 3836},
        {"level": 32, "party": 5, "kill_xp": 5258, "inside_s": 1996}]},
}


def build(cm_path: Path, rosters: Path) -> tuple[dict, dict]:
    areas = json.loads((ROOT / "data" / "forever-reference.json").read_text())["areas"]
    originals = {int(a["map"]): a["name"] for a in areas.values()
                 if a.get("instance") == "dungeon" and a.get("map") and int(a["map"]) < 2000}
    cm = cmangos(cm_path, originals)
    dungeons = {}
    for name, d in sorted(cm.items()):
        mid, rows = d["map"], d["rows"]
        e = {"map": mid, "label": "estimate", "basis": "spawns", "source": "cmangos"}
        if mid in WINGS:
            e["wings"] = {w: {"mobs": mobs, **({"party": WING_PARTY[(mid, w)]} if (mid, w) in WING_PARTY else {})}
                          for w, mobs in split_wings(mid, rows).items()}
        else:
            e["mobs"] = [_mob(r, r["n"]) for _, r in sorted(rows.items()) if r["n"] >= 0.005]
        if name in REFERENCE:
            e["reference"] = REFERENCE[name]
        dungeons[name] = e
    eg, adv = forever(rosters)
    maps = {a["name"]: int(a["map"]) for a in areas.values() if a.get("map")}
    for name, v in FOREVER.items():
        e = {"map": maps.get(name), **v}
        if name in eg:
            e.update(basis="reference_runs" if name in REFERENCE else "logged_runs", source="eg_link",
                     mobs=eg[name])
        if name in REFERENCE:
            e["reference"] = REFERENCE[name]
        dungeons[name] = e
    main = {
        "about": "Who a dungeon run kills, for the kill-XP estimate of a dungeon not run yet "
                 "(wowstats/plan/dungeonxp.py). Built by tools/dungeon_xp/export.py; do not edit.",
        "as_of": AS_OF,
        "mob_columns": {"spawns": ["npc_id", "name", "level_min", "level_max", "elite", "boss", "spawns"],
                        "logged_runs": ["npc_id", "name", "level_min", "level_max", "boss", "kills_per_run"]},
        "sources": {
            "cmangos": {"repo": CMANGOS_REPO, "commit": CMANGOS_COMMIT, "file": CMANGOS_FILE, "licence": "GPL-3.0",
                        "what": "creature_template (levels, rank), creature, creature_spawn_entry and the pools: "
                                "the creatures placed in each original dungeon"},
            "eg_link": {**EG, "what": "levels seen and kills per logged run (combatLog.deaths / encounters.runs) "
                                      "in the new Forever dungeons"},
            "forever_spawns": {**ADVOCAITE, "file": "data/dungeons/forever-spawns/forever-spawns.json",
                               "what": "rank and placed count of the new dungeons' creatures, read at run time"},
            "reference_runs": {"what": "one character's runs, anonymised: level, party size, kill XP and time "
                                       "inside", "licence": "GPL-3.0-only (ours)"},
        },
        "dungeons": dungeons,
    }
    fs = {"about": "Rank (elite or normal) and placed spawn count of the new Forever dungeons' creatures, from "
                   "advocaite/TrinityCore's forever branch: its sniffed spawn files (ADV-SNIFF), the classifications "
                   "players' caches gave it (ADV-CACHE) and its AnoCore rows (ANOCORE); 'health' marks a rank read "
                   "from EG Link's health against that branch's health curve. GPL-2.0-only: see LICENSE and "
                   "README.md next to this file.",
          "source": ADVOCAITE, "as_of": AS_OF, "columns": ["npc_id", "rank", "from", "placed"], "dungeons": adv}
    return main, fs


def dump(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cmangos", type=Path, required=True)
    ap.add_argument("--rosters", type=Path, required=True)
    ap.add_argument("--check", action="store_true", help="compare with the committed files, write nothing")
    a = ap.parse_args()
    main_d, fs = build(a.cmangos, a.rosters)
    bad = 0
    for path, d in ((OUT, main_d), (OUT_FOREVER, fs)):
        text = dump(d)
        if a.check:
            if not path.exists() or path.read_text() != text:
                print(f"{path.relative_to(ROOT)} is out of date: rerun without --check", file=sys.stderr)
                bad = 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    return bad


if __name__ == "__main__":
    sys.exit(main())
