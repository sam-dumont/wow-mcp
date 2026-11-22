"""Writes data/trainers-reference.json: who trains each profession rank and
riding, where they stand, and what the guides say it costs, so a move like
"Train Expert Enchanting" names the trainer, the zone and the coordinates.

One source, read as it ships: a Polestar release checkout (GPL-3.0). Its
guide pack's LICENSE-DATA.txt names where the trainer lists and spots come
from (the Forever TrinityCore world database's sniffed trainer lists and
spawns, GPL-2.0, used as facts); nothing here comes from a website.

- Professions: the `train` goals of the profession guides the addon ships
  (addon/Polestar_Guides/Guides.lua, forever/professions/*). Each goal lists
  the trainers who teach that rank on Forever (Polestar checks them against
  the Forever TrinityCore world database's trainer lists, its
  tests/profession-trainers.test.js), each with its uiMap, map coordinates
  and side ("A", "H" or "AH"). The rank's character level is the same step's
  `level` goal; its price is the step note's own ("for 50s (Classic's
  price; Forever's is not in the data)"), kept with that scope. A rank whose
  guide has no train goal (Artisan Enchanting: its trainer is inside
  Uldaman, with no spawn in the data) is left out: the server then says the
  location isn't in our data.
- Riding: the addon's riding reminder (addon/Polestar/QoL/Riding.lua): the
  trainer of each race and the base cost of each tier.
- Zone names: the guides' own names table (`["zone:1429"] = "Elwynn
  Forest"`); of two spellings, the longer ("Stormwind City", "The Barrens"),
  which is the game's AreaTable name.

Stdlib only, the same file byte for byte on every run:

    git clone --depth 1 --branch v0.4.2.1 https://github.com/fitzforge/polestar /tmp/polestar
    uv run python tools/trainers/export.py --polestar /tmp/polestar
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "data" / "trainers-reference.json"
RANK_NAMES = {1: "Apprentice", 2: "Journeyman", 3: "Expert", 4: "Artisan"}

# One candidate: an NPC at a spot. In a list line, inline ones end at
# their faction; in the records table, one a line, more may follow it.
RECORD = re.compile(r'\{ npc = (\d+), npcName = "((?:[^"\\]|\\.)*)", map = (\d+), x = ([\d.]+), y = ([\d.]+)'
                    r'[^{}]*?faction = "(A|H|AH)"')
TRAIN = re.compile(r't = "train", skill = "([^"]+)", skillLine = (\d+), rank = (\d), rankMax = (\d+), '
                   r'candidates = (.*) \},?$', re.M)
PRICE = re.compile(r"(?:raises the cap from \d+ to \d+|lets you [^.]*?) for (\d+[gsc](?: \d+[gsc])*)"
                   r"(?: \(([^)]*)\))?")
# The gathering guides: "raises the cap to 225; it needs skill 125 and no
# character level, and costs 50s".
COSTS = re.compile(r"costs (\d+[gsc](?: \d+[gsc])*)(?: \(([^)]*)\))?")
COIN = {"g": 10000, "s": 100, "c": 1}


def copper(text: str) -> int:
    return sum(int(n) * COIN[u] for n, u in re.findall(r"(\d+)([gsc])", text))


def zone_names(lua: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for zid, name in re.findall(r'\["zone:(\d+)"\] = "([^"]+)"', lua):
        z = int(zid)
        if len(name) > len(out.get(z, "")):
            out[z] = name
    return out


def trainer(m: re.Match) -> dict:
    return {"npc": int(m[1]), "name": m[2].replace('\\"', '"'), "map": int(m[3]), "x": float(m[4]),
            "y": float(m[5]), "side": m[6]}


def professions(lua: str, zones: dict[int, str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    parts = re.split(r"^(-- Source: .*)$", lua, flags=re.M)
    for head, block in zip(parts[1::2], parts[2::2], strict=True):
        if "forever/professions/" not in head:
            continue
        recs_m = re.search(r"local candidateRecords = \{\n(.*?)\n\t\t\}\n", block, re.S)
        records = [trainer(RECORD.search(line)) if RECORD.search(line) else None
                   for line in recs_m[1].split("\n")] if recs_m else []

        def resolve(text: str) -> list[dict]:
            got = []
            for m in re.finditer(r"candidateRecords\[(\d+)\]|" + RECORD.pattern, text):
                got.append(records[int(m[1]) - 1] if m[1] else trainer(re.match(RECORD.pattern, m[0])))
            return [t for t in got if t]

        lists_m = re.search(r"local candidateLists = \{\n(.*?)\n\t\t\}\n", block, re.S)
        lists = [resolve(line) for line in lists_m[1].split("\n")] if lists_m else []
        # The steps, each with its note and goals: a train goal's price and
        # level come from its own step.
        for step in re.split(r"\n\t\t\t\t\{\n", block):
            for m in TRAIN.finditer(step):
                skill, line, rank, rank_max, cands = m[1], int(m[2]), int(m[3]), int(m[4]), m[5]
                ref = re.match(r"candidateLists\[(\d+)\]", cands)
                people = lists[int(ref[1]) - 1] if ref else resolve(cands)
                seen, ts = set(), []
                for t in people:
                    if t["npc"] in seen:
                        continue
                    seen.add(t["npc"])
                    ts.append({**t, "zone": zones.get(t["map"])})
                note = re.search(r'note = "((?:[^"\\]|\\.)*)"', step)
                price = (PRICE.search(note[1]) or COSTS.search(note[1])) if note else None
                level = re.search(r'\{ t = "level", level = (\d+) \}', step)
                no_level = bool(note and "no character level" in note[1])
                p = out.setdefault(str(line), {"name": skill, "ranks": {}})
                p["ranks"][str(rank)] = {
                    "name": RANK_NAMES.get(rank), "max": rank_max,
                    "level": int(level[1]) if level else (0 if no_level else None),
                    "price_copper": copper(price[1]) if price else None,
                    # The note says whose price it is; without a word on
                    # it, the guide's figure stands as the guide's.
                    "price_scope": ("Classic Era price" if price and "Classic" in (price[2] or "")
                                    else ("Polestar's guide" if price else None)),
                    "trainers": sorted(ts, key=lambda t: (t["zone"] or "", t["name"])),
                }
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def riding(src: str, zones: dict[int, str]) -> dict:
    table = re.search(r"local TRAINERS = \{\n(.*?)\n\}", src, re.S)[1]
    by_race = {}
    for m in re.finditer(r"(\w+)\s*= \{ map = (\d+), x = ([\d.]+),\s*y = ([\d.]+),\s*npc = (\d+), "
                         r'npcName = "([^"]+)"', table):
        by_race[m[1]] = {"npc": int(m[5]), "name": m[6], "map": int(m[2]), "x": float(m[3]), "y": float(m[4]),
                         "zone": zones.get(int(m[2]))}
    sky = re.search(r"local TRAINERS_SKYBORNE = \{\n(.*?)\n\}", src, re.S)[1]
    for m in re.finditer(r"(\w+)\s*= \{ map = (\d+), x = ([\d.]+), y = ([\d.]+), npc = (\d+), "
                         r'npcName = "([^"]+)"', sky):
        by_race[f"Skyborne:{m[1]}"] = {"npc": int(m[5]), "name": m[6], "map": int(m[2]), "x": float(m[3]),
                                       "y": float(m[4]), "zone": zones.get(int(m[2]))}
    cost = re.search(r"local BASE_COST = \{ \[1\] = (\d+), \[2\] = (\d+) \}", src)
    return {"tiers": {"1": {"level": 40, "price_copper": int(cost[1])},
                      "2": {"level": 60, "price_copper": int(cost[2])}},
            "price_scope": "Polestar's riding reminder, before a reputation discount",
            "trainers": dict(sorted(by_race.items()))}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--polestar", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    root = a.polestar
    lua = (root / "addon" / "Polestar_Guides" / "Guides.lua").read_text(encoding="utf-8")
    zones = zone_names(lua)
    tag = subprocess.run(["git", "-C", str(root), "describe", "--tags", "--always"], capture_output=True,
                         text=True).stdout.strip()
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                            text=True).stdout.strip()
    profs = professions(lua, zones)
    doc = {
        "schema": 1,
        "source": {"polestar": {"tag": tag, "commit": commit, "licence": "GPL-3.0",
                                "files": ["addon/Polestar_Guides/Guides.lua", "addon/Polestar/QoL/Riding.lua"]},
                   "note": "trainer lists: Forever's, as Polestar's guides ship them; prices as each note scopes them"},
        # skill line -> {name, ranks: {rank: {name, max, level, price_copper,
        # price_scope, trainers: [{npc, name, map, x, y, side, zone}]}}}
        "professions": profs,
        "riding": riding((root / "addon" / "Polestar" / "QoL" / "Riding.lua").read_text(encoding="utf-8"), zones),
        "zones": {str(k): v for k, v in sorted(zones.items())},
    }
    a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    for line, p in profs.items():
        print(f"{p['name']} ({line}): " + ", ".join(
            f"{r['name']} {len(r['trainers'])} trainers"
            + (f" {r['price_copper']}c" if r["price_copper"] is not None else " no price")
            for r in p["ranks"].values()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
