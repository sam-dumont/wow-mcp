"""The eight other characters in the demo. Nibbet's history lives in generate.py.

Race/class combinations: Blizzard's Forever character creation matrix
(https://worldofwarcraft.blizzard.com/en-us/news/24304075/). Equipment comes
from the checked-in Forever item tables and observed loot. The talent strings
are WoWSims Exporter's format. The spell IDs for their nonzero digits come
from the Forever 1.60.1.71139 talent catalog and data/wowsims-talents.json.
Their builds follow the level picks in data/kb/research/classes.json.
"""

from __future__ import annotations

import json
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
    spells: tuple[int, ...]  # talent spells in the order of nonzero digits
    mount_spell: int | None = None  # learned mount summon


# One character of each class, all on the same invented realm as Nibbet.
CHARACTERS = (
    Character("Brakka-Ironvale", "WARRIOR", "Orc", 2, "Horde", 3, 60, "Arms", "Eastern Plaguelands",
              "Light's Hope Chapel", (22411, 15411, 273946, 12618, 13142, 273944, 273947, 13400, 273945, 13098, 17713,
                                      11815, 19120, 18509, 12784, 18680),
              "05323213132215201-05320005", (16462, 12286, 12285, 12295, 12290, 12296, 12834, 1310222, 12163,
                                              16493, 1289682, 12292, 1290261, 12862, 12294, 12320, 1323964, 12322,
                                              12317),
              mount_spell=23251),  # Swift Timber Wolf
    Character("Hesta-Bellforge", "PALADIN", "Human", 1, "Alliance", 3, 53, "Retribution", "Un'Goro Crater",
              "Gadgetzan", (10749, 9641, 12428, 250540, 12424, 10740, 10707, 12425, 250589, 17713, 9533,
                            270275, 12465, 12776),
              "05021--55233011201301351", (20262, 1310899, 20224, 20060, 20101, 25956, 20117, 1311074, 9453,
                                            20375, 26022, 1311087, 20111, 20066, 20049, 1311084, 1310735),
              mount_spell=13819),  # Summon Warhorse
    Character("Flintwick-Bearhide", "HUNTER", "Dwarf", 3, "Alliance", 2, 34, "Beast Mastery", "Stranglethorn Vale",
              "Booty Bay", (252513, 5002, 4721, 4057, 6911, 9624, 10582, 4059, 4254, 4535, 6340, 7710, 9456),
              "51202214041012", (19552, 19583, 1223755, 24443, 19559, 19596, 19616, 19598, 1293241, 19577, 19590)),
    Character("Valeleaf-Quickwing", "ROGUE", "Skyborne", 95, "Alliance", 3, 16, "Combat", "Loch Modan",
              "Thelsamar", (252490, 252429, 7282, 10402, 7281, 2312, 6466, 1951, 3848, 11304),
              "02-32", (14144, 14162, 13732)),
    Character("Flintstep-Thunderhoof", "SHAMAN", "Tauren", 6, "Horde", 2, 29, "Enhancement", "Thousand Needles",
              "Freewind Post", (15129, 282717, 4255, 4456, 4830, 6601, 6902, 4253, 6321, 6314, 6687),
              "-0530303100212", (16255, 17485, 415140, 16266, 1223030, 16256, 17364, 16262)),
    Character("Sleetpin-Rimefang", "MAGE", "Troll", 8, "Horde", 3, 10, "Frost", "Durotar",
              "Razor Hill", (6242, 253885, 4309, 2569, 4308, 4307, 2308, 286754, 11287),
              "--01", (11070,)),
    Character("Duskwick-Cinderbell", "WARLOCK", "Undead", 5, "Horde", 2, 23, "Affliction", "Hillsbrad Foothills",
              "Tarren Mill", (253955, 3748, 1486, 6392, 4317, 4321, 14375, 4318, 5001, 6340, 15109, 5210),
              "14502002", (18182, 18174, 17810, 437032, 17783)),
    Character("Mossbranch-Fernshade", "DRUID", "Night Elf", 4, "Alliance", 2, 39, "Feral Combat", "Dustwallow Marsh",
              "Theramore Isle", (8176, 7731, 8192, 8175, 252460, 4108, 9398, 4059, 252525, 271910, 4114, 9408),
              "-54230002123112121", (16934, 17056, 17002, 16947, 16998, 1238122, 16942, 16972, 1328617, 1328613,
                                      16958, 17007, 1223242, 1223246)),
)
# What the alts had when their Recorder history starts. Ordinary crafted pieces
# can be bought. The Troll Mage's four visible starter items are exactly
# Forever CharacterLoadout 2494, read from build 1.60.1.71139.
TROLL_MAGE_LOADOUT = (6140, 1395, 55, 35)  # robe, pants, boots, Bent Staff
STARTER_ITEMS = {
    "WARRIOR": (12410, 12428, 12405, 12424, 7926, 7936, 12425, 250589, 13860, 7960),
    "PALADIN": (7937, 12428, 250540, 12424, 7926, 7936, 7919, 9413),
    "HUNTER": (4251, 6603, 4250, 5961, 3057, 3202, 4254, 7958, 3024),
    "ROGUE": (5957, 4237, 2303, 2302, 2494),  # handstitched leathers, Stiletto from Zephras Isle
    "SHAMAN": (4251, 2317, 4250, 5961, 3057, 3202, 4253, 6505),
    "MAGE": TROLL_MAGE_LOADOUT,
    "WARLOCK": (2585, 4309, 2583, 4308, 4307, 2584, 2030, 11287),
    "DRUID": (4251, 4057, 4257, 7373, 9828, 4259, 4254, 3185),
}


# Quest and stance abilities the class trainer's price list doesn't include.
CLASS_ABILITIES = {"WARRIOR": (2457,), "HUNTER": (883, 982, 1515), "WARLOCK": (688, 697), "DRUID": (5487, 768)}
# The roster's talent picks that grant a button in the spellbook. The class
# trainer table explicitly excludes talent spells, including these first ranks.
ACTIVE_TALENTS = {"Spearing Strike", "Sweeping Strikes", "Mortal Strike", "Seal of Command", "Repentance",
                  "Summon Hawk", "Intimidation", "Stormstrike", "Feral Charge", "Lacerate"}
BEAR_DISPLAY = 822  # Brown bear, CreatureDisplayInfo in the Forever client


def learned(c: Character) -> list[tuple[str, int, int, str]]:
    talents = classguide.reference()["classes"][c.cls]["talents"]
    by_spell = {v["spell"]: (name, v["tree"]) for name, v in talents.items()}
    ranks = (int(n) for n in c.talents if n not in "-0")
    return [(by_spell[spell][0], spell, rank, by_spell[spell][1]) for spell, rank in zip(c.spells, ranks, strict=True)]


def worn(c: Character) -> list[dict]:
    return equipment_rows(c.equipment)


def starter_gear(c: Character) -> list[dict]:
    return equipment_rows(STARTER_ITEMS[c.cls])


def equipment_rows(item_ids: tuple[int, ...]) -> list[dict]:
    from generate import BNET_SLOTS

    ref = gear.reference()
    items = []
    slots = set()
    for iid in item_ids:
        info = ref.item(iid)
        slot = next(s for s in gear.SLOTS_OF[info["inv"]] if s not in slots)
        slots.add(slot)
        items.append({"slot": slot, "slot_name": BNET_SLOTS[slot].replace("_", " ").title(), "item_id": iid,
                      "name": ref.data["names"][str(iid)], "item_level": info["ilvl"],
                      "req_level": info["req"] or None, "quality": info["quality"]})
    return items


def spellbook(c: Character) -> list[list[int]]:
    """The character's learned spells and next training, as Altoholic records them."""
    trained = [s for level in range(1, c.level + 1) for s in classguide.taught(c.cls, level, c.race, c.faction)]
    known = {s["id"] for s in trained}
    known.update(spell for name, spell, _, _ in learned(c) if name in ACTIVE_TALENTS)
    # Starting abilities and quest/talent-granted first ranks have no trainer price.
    table = classguide.reference()["classes"][c.cls]["spells"]
    known.update(s["prev"] for rows in table.values() for s in rows if s["id"] in known and s.get("prev"))
    known.add(6603)  # Auto Attack
    known.update(CLASS_ABILITIES.get(c.cls, ()))
    if c.mount_spell:
        known.update((33388, c.mount_spell))
        if c.level == 60:
            known.add(33391)
    next_training = classguide.train(c.cls, c.level, c.race, c.faction)["next"]
    book = {s["id"]: s["level"] for s in (next_training or {}).get("spells", ())}
    book.update(dict.fromkeys(known, 0))
    return [[spell, level] for spell, level in sorted(book.items())]


def sessions(last: float, priest: list[dict] | None = None) -> list[dict]:
    from roster_history import histories

    return histories(last, priest or [])


def series(rows: list[dict], now: float, looks: dict[str, list[dict]] | None = None) -> list[dict]:
    """The daily armory read uses only the last addon save available at that time."""
    from bisect import bisect_right

    from generate import BNET_SLOTS, _epoch

    out = []
    for c in CHARACTERS:
        mine = [r for r in rows if r.get("char") == c.name]
        sheets = sorted((_epoch(r["_time"]), r) for r in mine if r["feed"] == "wow-gear")
        levels = sorted((_epoch(r["_time"]), int(r["level"])) for r in mine
                        if r["feed"] == "wow-character" and r.get("kind") != "played")
        samples = {}
        sheet_times = [t for t, _ in sheets]
        for day in range(20, -1, -1):
            at = int(now - day * 86400)
            level_index = bisect_right(levels, (at, 61)) - 1
            if level_index < 0:
                continue
            level = levels[level_index][1]

            def sample(metric, value, **labels):
                key = (metric, tuple(labels.items()))
                s = samples.setdefault(key, {"metric": {"__name__": f"wow_character_{metric}", "character": c.name,
                                                        "account_id": "0", **labels}, "values": [], "timestamps": []})
                s["values"].append(value)
                s["timestamps"].append(at * 1000)

            sample("profile", level, race_id=str(c.race_id), gender="male" if c.sex == 2 else "female",
                   in_guild="false")
            sample("profile_found", 1)
            sample("profile_refreshed_timestamp_seconds", at)
            sample("achievements_read", 0)
            for option in (looks or {}).get(c.name, ()):
                sample("customization", option["choiceId"], option_id=str(option["optionId"]))
            idx = bisect_right(sheet_times, at) - 1
            if idx >= 0:
                items = json.loads(sheets[idx][1]["items"])
                for it in items:
                    sample("equipped", it["item_id"], slot=BNET_SLOTS[it["slot"]])
                for kind in ("equipped", "average"):
                    sample("item_level", sum(it["item_level"] for it in items) // 16, kind=kind)
            ranks = talent_ranks(c, level)
            total = Counter()
            for name, spell, _, tree in learned(c):
                if rank := ranks[name]:
                    sample("talent_rank", rank, spell_id=str(spell), talent=name)
                    total[tree] += rank
            trees = sorted({t["tree"] for t in classguide.reference()["classes"][c.cls]["talents"].values()})
            for order, tree in enumerate(trees):
                sample("talent_points", total[tree], tree=tree, order=str(order))
        out.extend(samples.values())
    return out


def talent_ranks(c: Character, level: int) -> Counter:
    return Counter(p["talent"] for p in classguide.path(c.cls)["picks"] if p["level"] <= level)


def talent_string(c: Character, level: int) -> str:
    ranks = talent_ranks(c, level)
    names = iter(name for name, _, _, _ in learned(c))
    return "-".join("".join(str(ranks[next(names)]) if digit != "0" else "0" for digit in tree).rstrip("0")
                    for tree in c.talents.split("-")).rstrip("-")


def bundled_talent_catalog() -> dict:
    """An offline demo can draw the real talent grid from the references in
    the checkout. The saved client table fixture also has the Priest/Mage
    tooltips and Legacy challenges that Nibbet's full history uses."""
    from pathlib import Path

    from wowstats import talents

    fixture = next((Path(__file__).resolve().parents[2] / "tests" / "data").glob("wowf-talents-*.json"))
    tables = json.loads(fixture.read_text())
    catalog = talents.catalog_from(lambda name: tables.get(name, []))
    have = {tr.get("class") for tr in catalog["trees"]}
    for c in CHARACTERS:
        if c.cls in have:
            continue
        nodes = classguide.reference()["classes"][c.cls]["talents"]
        sections = {}
        for name, node in nodes.items():
            tree = node["tree"]
            section = sections.setdefault(tree, {"name": tree, "nodes": [], "edges": []})
            spell = node["spell"]
            catalog["spells"][str(spell)] = {"name": name, "icon": node["icon"]}
            section["nodes"].append({"id": spell, "row": node["row"], "col": node["col"], "max": node["max"],
                                     "spells": [str(spell)], "defs": node["defs"], "ranks": []})
            if need := nodes.get(node["needs"]):
                section["edges"].append({"fc": need["col"], "fr": need["row"], "tc": node["col"], "tr": node["row"]})
        trees = [{**sections[name], "order": i} for i, name in enumerate(sorted(sections))]
        catalog["trees"].append({"kind": "class", "class": c.cls, "sections": trees})
    return catalog
