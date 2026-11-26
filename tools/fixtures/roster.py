"""The eight shorter sessions in the demo. Nibbet's history lives in generate.py.

Race/class combinations: Blizzard's Forever character creation matrix
(https://worldofwarcraft.blizzard.com/en-us/news/24304075/). Equipment comes
from the checked-in Forever item tables and observed loot. The talent strings
are WoWSims Exporter's format. The spell IDs for their nonzero digits come
from the Forever 1.60.1.71139 talent catalog and data/wowsims-talents.json.
Their builds follow the level picks in data/kb/research/classes.json.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass

from wowstats import classguide, gear


@dataclass(frozen=True)
class Character:
    name: str
    cls: str
    race: str
    race_id: int
    faction: str
    sex: int  # UnitSex (2 male, 3 female)
    level: int
    spec: str
    zone: str
    inn: str
    equipment: tuple[int, ...]
    talents: str
    spells: tuple[int, ...]  # learned spells in the order of nonzero digits


# Different surnames, all on the same invented realm as Nibbet.
CHARACTERS = (
    Character("Brakka-Ironvale", "WARRIOR", "Orc", 2, "Horde", 3, 35, "Arms", "Arathi Highlands", "Hammerfall",
              (6686, 7731, 6404, 7688, 10329, 6402, 4073, 4438, 10331, 6693, 4716, 9459, 3037),
              "023232131302121", (16462, 12286, 12285, 12295, 12290, 12296, 12834, 1310222, 12163, 1289682,
                                  12292, 1290261, 12862)),
    Character("Hesta-Bellforge", "PALADIN", "Human", 1, "Alliance", 3, 33, "Retribution", "Hillsbrad Foothills",
              "Southshore", (274291, 274068, 3231, 4074, 6200, 9871, 273042, 10333, 6906, 2039, 4726, 7689),
              "--552330012012", (20060, 20101, 25956, 20117, 1311074, 20375, 26022, 1311087, 20111)),
    Character("Flintwick-Bearhide", "HUNTER", "Dwarf", 3, "Alliance", 2, 32, "Beast Mastery", "Stranglethorn Vale",
              "Booty Bay", (252513, 7731, 4721, 4057, 6911, 6690, 10582, 4049, 7756, 5001, 6340, 7710, 9456),
              "51202214021012", (19552, 19583, 1223755, 24443, 19559, 19596, 19616, 19598, 1293241, 19577, 19590)),
    Character("Valeleaf-Quickwing", "ROGUE", "Skyborne", 95, "Alliance", 3, 29, "Combat", "Wetlands",
              "Menethil Harbor", (3392, 286995, 4709, 273805, 6398, 3056, 1121, 3230, 720, 1076, 5193,
                                  8226, 6681, 273843),
              "02-32103302100111", (14144, 14162, 13732, 13712, 13713, 13705, 13742, 14251, 1310711, 30919, 18427)),
    Character("Flintstep-Thunderhoof", "SHAMAN", "Tauren", 6, "Horde", 2, 31, "Enhancement", "Thousand Needles",
              "Freewind Post", (252515, 274068, 282717, 273026, 274042, 273647, 6601, 6902, 7690, 6321, 6314, 6687),
              "-0530303100212002", (16255, 17485, 415140, 16266, 1223030, 16256, 17364, 16262, 1223031)),
    Character("Sleetpin-Rimefang", "MAGE", "Troll", 8, "Horde", 3, 30, "Frost", "Ashenvale",
              "Splintertree Post", (7357, 6695, 6395, 2034, 4714, 3067, 9454, 9448, 286989, 273030, 274149,
                                   273040, 5214),
              "--0534300001013001", (11070, 29438, 11207, 11071, 1312002, 11958, 11170, 400647)),
    Character("Duskwick-Cinderbell", "WARLOCK", "Undead", 5, "Horde", 2, 28, "Affliction", "Hillsbrad Foothills",
              "Tarren Mill", (273645, 5003, 3748, 6324, 6392, 6910, 271214, 273036, 14377, 273806, 14582,
                             274158, 5243),
              "145020030202", (18182, 18174, 17810, 437032, 17783, 427712, 18094)),
    Character("Mossbranch-Fernshade", "DRUID", "Night Elf", 4, "Alliance", 2, 34, "Feral Combat", "Dustwallow Marsh",
              "Theramore Isle", (6688, 7731, 282717, 6396, 6421, 273022, 14589, 4059, 6397, 6693, 4732, 2280),
              "-532300021221121", (16934, 17056, 17002, 16947, 16998, 1238122, 16942, 16972, 1328617, 1328613,
                                   16958, 17007)),
)
BEAR_DISPLAY = 822  # Brown bear, CreatureDisplayInfo in the Forever client


def learned(c: Character) -> list[tuple[str, int, int, str]]:
    talents = classguide.reference()["classes"][c.cls]["talents"]
    by_spell = {v["spell"]: (name, v["tree"]) for name, v in talents.items()}
    ranks = (int(n) for n in c.talents if n not in "-0")
    return [(by_spell[spell][0], spell, rank, by_spell[spell][1]) for spell, rank in zip(c.spells, ranks, strict=True)]


def worn(c: Character) -> list[dict]:
    from generate import BNET_SLOTS

    ref = gear.reference()
    items = []
    slots = set()
    for iid in c.equipment:
        info = ref.item(iid)
        slot = next(s for s in gear.SLOTS_OF[info["inv"]] if s not in slots)
        slots.add(slot)
        items.append({"slot": slot, "slot_name": BNET_SLOTS[slot].replace("_", " ").title(), "item_id": iid,
                      "name": ref.data["names"][str(iid)], "item_level": info["ilvl"],
                      "req_level": info["req"] or None, "quality": info["quality"]})
    return items


def sessions(last: float) -> list[dict]:
    from generate import DEMO_REALM, SEED, Game, Player, shipper_shapes

    game = Game()
    rows = []
    for i, c in enumerate(CHARACTERS, 1):
        rnd = random.Random(SEED + i)
        p = Player(game, rnd, c.name, cls=c.cls, race=c.race, faction=c.faction, sex=c.sex, level=c.level,
                   xp=rnd.randint(1200, 8500), money=rnd.randint(13000, 65000), bind=c.inn)
        p.login(last - (i + 2) * 3600, c.zone)
        if c.cls in ("HUNTER", "WARLOCK"):
            kind = c.cls.lower()
            p.other("wow-companion", kind=kind, display_id=BEAR_DISPLAY if kind == "hunter" else 1132,
                    source_id=f"companion:{c.name}:{int(p.t)}", _msg=f"{c.name}: {kind} companion")
        for npc in rnd.choices(game.zone_mobs(c.zone, c.level), k=3 + i % 4):
            p.kill(npc)
            p.wait(rnd.randint(20, 80))
        p.wait(30, moving=False)
        p.xp_rec()
        p.snapshot(realm=DEMO_REALM)
        items = worn(c)
        p.other("wow-gear", **p.who, spec=c.spec, items=items, professions=[],
                item_level=round(sum(it["item_level"] for it in items) / len(items), 1))
        p.other("wow-talents", source="wowsims", talents=c.talents, level=c.level, **{"class": c.cls}, spec=c.spec,
                addon_version="1.2.3", source_id=f"talents:{c.name}:{c.talents}:{int(p.t)}")
        for row in shipper_shapes(p.out):
            if row["feed"] == "wow-recorder":
                row["source_id"] = row["source_id"].replace("rec:1:", f"rec:{i + 1}:")
            rows.append(row)
    return rows


def series(rows: list[dict], now: float, looks: dict[str, list[dict]] | None = None) -> list[dict]:
    """Two daily armory reads per alt. The public profile and talent history
    query the same metrics the Battle.net exporter writes for a real account."""
    from generate import BNET_SLOTS

    out = []
    for c in CHARACTERS:
        sheet = next(r for r in rows if r.get("char") == c.name and r["feed"] == "wow-gear")
        items = json.loads(sheet["items"])
        # The short outing records an already-equipped character: the earlier
        # daily read carries the same outfit and talent allocation.
        stamps = [int(now - 86400) * 1000, int(now) * 1000]

        def sample(metric: str, value: int, **labels) -> None:
            out.append({"metric": {"__name__": f"wow_character_{metric}", "character": c.name, "account_id": "0",
                                   **labels}, "values": [value, value], "timestamps": stamps})

        sample("profile", c.level, race_id=str(c.race_id), gender="male" if c.sex == 2 else "female", in_guild="false")
        for option in (looks or {}).get(c.name, ()):
            sample("customization", option["choiceId"], option_id=str(option["optionId"]))
        sample("profile_found", 1)
        sample("profile_refreshed_timestamp_seconds", int(now))
        out[-1]["values"] = [t // 1000 for t in stamps]
        sample("achievements_read", 0)
        for it in items:
            sample("equipped", it["item_id"], slot=BNET_SLOTS[it["slot"]])
        for kind in ("equipped", "average"):
            sample("item_level", sum(it["item_level"] for it in items) // 16, kind=kind)
        total = Counter()
        for name, spell, rank, tree in learned(c):
            total[tree] += rank
            sample("talent_rank", rank, spell_id=str(spell), talent=name)
        trees = sorted({t["tree"] for t in classguide.reference()["classes"][c.cls]["talents"].values()})
        for order, tree in enumerate(trees):
            sample("talent_points", total[tree], tree=tree, order=str(order))
    return out


def bundled_talent_catalog() -> dict:
    """An offline demo can draw the real talent grid from the references in
    the checkout. The saved client table fixture also has the Priest/Mage
    tooltips and Legacy challenges that Nibbet's full history uses."""
    from pathlib import Path

    from wowstats import talents, wowsims

    fixture = next((Path(__file__).resolve().parents[2] / "tests" / "data").glob("wowf-talents-*.json"))
    tables = json.loads(fixture.read_text())
    catalog = talents.catalog_from(lambda name: tables.get(name, []))
    have = {tr.get("class") for tr in catalog["trees"]}
    for c in CHARACTERS:
        if c.cls in have:
            continue
        nodes = classguide.reference()["classes"][c.cls]["talents"]
        # Match the definitions the simulated addon actually saved. Other
        # nodes on the grid are still shown from their real spell and position.
        definitions = wowsims.table()[c.cls]
        digits = [definitions[t][i] for t, tree in enumerate(c.talents.split("-"))
                  for i, digit in enumerate(tree) if digit != "0"]
        learned_defs = dict(zip(c.spells, digits, strict=True))
        sections = {}
        for name, node in nodes.items():
            tree = node["tree"]
            section = sections.setdefault(tree, {"name": tree, "nodes": [], "edges": []})
            spell = node["spell"]
            catalog["spells"][str(spell)] = {"name": name}
            section["nodes"].append({"id": spell, "row": node["row"], "col": node["col"], "max": node["max"],
                                     "spells": [str(spell)], "defs": [learned_defs[spell]] if spell in learned_defs
                                     else [], "ranks": []})
            if need := nodes.get(node["needs"]):
                section["edges"].append({"fc": need["col"], "fr": need["row"], "tc": node["col"], "tr": node["row"]})
        trees = [{**sections[name], "order": i} for i, name in enumerate(sorted(sections))]
        catalog["trees"].append({"kind": "class", "class": c.cls, "sections": trees})
    return catalog
