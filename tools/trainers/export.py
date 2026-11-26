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

Python and Lua, the same file byte for byte on every run:

    git clone --depth 1 --branch v0.4.3 https://github.com/fitzforge/polestar /tmp/polestar
    uv run python tools/trainers/export.py --polestar /tmp/polestar
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.craft_reference.export import guides  # noqa: E402

OUT = ROOT / "data" / "trainers-reference.json"
RANK_NAMES = {1: "Apprentice", 2: "Journeyman", 3: "Expert", 4: "Artisan"}

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


def professions(compiled: list[dict], zones: dict[int, str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for guide in compiled:
        for step in guide["steps"]:
            goals = step.get("goals") or []
            note = step.get("note") or ""
            level = next((g["level"] for g in goals if g["t"] == "level"), None)
            price = PRICE.search(note) or COSTS.search(note)
            for g in goals:
                if g["t"] != "train" or not g.get("rank") or not g.get("candidates"):
                    continue
                # Book vendors also use a train goal, with the book to buy in its text.
                if g.get("text", "").startswith("Buy and read {item:"):
                    continue
                people = {}
                for t in g["candidates"]:
                    people.setdefault(t["npc"], {
                        "npc": t["npc"], "name": t["npcName"], "map": t["map"], "x": float(t["x"]), "y": float(t["y"]),
                        "side": t["faction"], "zone": zones.get(t["map"]),
                    })
                p = out.setdefault(str(g["skillLine"]), {"name": g["skill"], "ranks": {}})
                p["ranks"][str(g["rank"])] = {
                    "name": RANK_NAMES.get(g["rank"]), "max": g["rankMax"],
                    "level": level if level is not None else (0 if "no character level" in note else None),
                    "price_copper": copper(price[1]) if price else None,
                    "price_scope": ("Classic Era price" if price and "Classic" in (price[2] or "")
                                    else ("Polestar's guide" if price else None)),
                    "trainers": sorted(people.values(), key=lambda t: (t["zone"] or "", t["name"])),
                }
    if not out:
        raise SystemExit("no profession trainers in the compiled Polestar guides")
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
    ap.add_argument("--lua", default="lua", help="the Lua interpreter for the compiled guides")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    root = a.polestar
    lua = (root / "addon" / "Polestar_Guides" / "Guides.lua").read_text(encoding="utf-8")
    zones = zone_names(lua)
    tag = subprocess.run(["git", "-C", str(root), "describe", "--tags", "--always"], capture_output=True,
                         text=True).stdout.strip()
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                            text=True).stdout.strip()
    profs = professions(guides(root, a.lua), zones)
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
