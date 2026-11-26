"""Original box models for tests of the class demo and viewer."""

from __future__ import annotations

from pathlib import Path

import golden3d as g
import roster

from wowstats import gear

# Each race has its own proportions and skin. Additional blocks give the
# Tauren its muzzle and horns, the Troll and Orc their tusks, the Skyborne wings.
SHAPES = {
    "Orc": ((1.38, 1.5, 1.55), (93, 135, 77)),
    "Human": ((1.04, 1.04, 1.55), (172, 117, 87)),
    "Dwarf": ((1.35, 1.30, 1.12), (184, 135, 106)),
    "Skyborne": ((1.02, 1.02, 1.52), (129, 165, 190)),
    "Tauren": ((1.65, 1.7, 1.87), (151, 109, 78)),
    "Troll": ((1.05, 1.08, 1.83), (103, 161, 166)),
    "Undead": ((.86, .87, 1.40), (147, 146, 134)),
    "Night Elf": ((1.02, 1.08, 1.79), (148, 127, 181)),
}
# Cloth/leather/mail tints for each outfit, including the alternate trim used
# on boots, gloves, and belts. A shared item always gets the same appearance.
DYES = (
    ((122, 77, 65), (183, 167, 143)), ((61, 95, 138), (216, 180, 91)),
    ((93, 111, 70), (169, 130, 82)), ((69, 79, 103), (137, 147, 159)),
    ((133, 87, 51), (86, 153, 155)), ((73, 127, 165), (185, 209, 224)),
    ((91, 63, 115), (160, 114, 134)), ((97, 112, 76), (145, 111, 75)),
)


# Embedded original poses. The clip numbers are the M2 animation IDs the
# viewer asks for. Angles rotate the right and left arm from the shoulder.
POSES = {17: (108, 15), 18: (116, 77), 19: (94, 82), 46: (63, 94), 49: (87, 68), 51: (125, 110),
         52: (91, 40), 53: (95, 12), 54: (49, 82), 55: (118, 118), 95: (80, -24), 124: (125, 135)}
CLIPS = {"WARRIOR": (18, 55, 46), "PALADIN": (18, 52, 54), "HUNTER": (19, 49, 53), "ROGUE": (17, 95, 46),
         "SHAMAN": (18, 51, 53), "MAGE": (19, 51, 53), "WARLOCK": (19, 53, 124), "DRUID": (19, 52, 54),
         "PRIEST": (19, 53, 54)}


def gestures(cls):
    return [(clip, *POSES[clip]) for clip in CLIPS[cls]]


def weapon_parts(kind):
    box = g._b
    if kind == "gun":
        return [box((-.06, -.035, -.20), (.06, .035, .02)), box((-.026, -.025, .02), (.026, .025, .47)),
                box((.01, -.026, -.06), (.12, .026, .01))]
    if kind == "bow":
        return [box((-.025, -.02, -.19), (.025, .02, .19)), box((-.07, -.02, .18), (.012, .02, .37)),
                box((-.07, -.02, -.37), (.012, .02, -.18)), box((-.077, -.005, -.37), (-.069, .005, .37))]
    if kind == "axe":
        return [box((-.022, -.024, -.17), (.022, .024, .61)), box((-.13, -.033, .40), (.13, .033, .59))]
    length = .28 if kind == "dagger" else .61
    return [box((-.019, -.017, -.12), (.019, .017, .02)), box((-.10, -.02, .02), (.10, .02, .047)),
            box((-.034, -.012, .047), (.034, .012, length))]


def item_looks():
    by_slot = {1: "helm", 2: "jewelry", 3: "shoulders", 5: "chest", 6: "belt", 7: "trousers", 8: "boots",
               9: "wrists", 10: "gloves", 11: "jewelry", 15: "cloak"}
    by_weapon = {1: "axe", 2: "bow", 3: "gun", 5: "mace", 7: "blade", 8: "blade", 10: "staff",
                 15: "dagger", 19: "wand"}
    out = {}
    for c, colors in zip(roster.CHARACTERS, DYES, strict=True):
        for it in roster.worn(c):
            iid = it["item_id"]
            info = gear.reference().item(iid)
            kind = by_weapon[info["subclass"]] if info["class"] == 2 else (
                "robe" if info["inv"] == 20 else by_slot[it["slot"]])
            out.setdefault(iid, (kind, colors))
    return out


def features(race):
    boxes = {
        "Tauren": [((.1, -.12, .83), (.23, .12, .96)),
                   ((-.04, .12, 1.04), (.04, .29, 1.11)), ((-.03, .24, 1.10), (.03, .30, 1.23))],
        "Troll": [((.115, .07, .84), (.195, .10, .96)), ((.17, .07, .94), (.198, .10, 1.01))],
        "Orc": [((.13, .065, .84), (.172, .095, .95))],
        "Skyborne": [((-.17, .04, .44), (-.10, .30, .73)), ((-.21, .23, .49), (-.13, .42, .68))],
        "Night Elf": [((-.07, .14, .95), (.02, .35, 1.01))],
    }.get(race, [])
    return boxes + [g.mirror(lo, hi) for lo, hi in boxes if lo[1] > 0]


def add_body(golden, c, index):
    t, files = golden["tables"], golden["files"]
    base = 10_000 + index * 100
    model, display, opt, choice, mat = (g.rid(base + n) for n in range(5))
    file_id, texture = g.fid(base), g.fid(base + 2)
    data = 64_000 + index
    scale, rgb = SHAPES[c.race]
    sex = c.sex - 2
    files.update(g.character(model_id=file_id, skin_id=file_id + 1, scale=scale, features=features(c.race),
                             gestures=gestures(c.cls)))
    files[texture] = g.skin_texture(rgb)
    t["ChrRaces"][c.race_id] = {"ClientFileString": c.race.replace(" ", ""), "Name_lang": c.race}
    t["ChrRaceXChrModel"][model] = {"ChrRacesID": c.race_id, "Sex": sex, "ChrModelID": model}
    t["ChrModel"][model] = {"Sex": sex, "DisplayID": display, "CharComponentTextureLayoutID": g.LAYOUT}
    t["CreatureDisplayInfo"][display] = {"ModelID": data}
    t["CreatureModelData"][data] = {"FileDataID": file_id}
    t["ChrCustomizationOption"][opt] = {"Name_lang": "Skin Color", "ChrModelID": model, "ChrCustomizationID": 1}
    t["ChrCustomizationChoice"][choice] = {"ChrCustomizationOptionID": opt, "Name_lang": "Skin"}
    t["ChrCustomizationElement"][mat] = {"ChrCustomizationChoiceID": choice, "ChrCustomizationMaterialID": mat}
    t["ChrCustomizationMaterial"][mat] = {"ChrModelTextureTargetID": 1, "MaterialResourcesID": mat}
    t["TextureFileData"][texture] = {"FileDataID": texture, "UsageType": 0, "MaterialResourcesID": mat}


def add_pets(golden):
    box = g._b
    bear = [box((-.32, -.17, .21), (.25, .17, .51)), box((.16, -.14, .35), (.42, .14, .58)),
            box((.35, -.10, .36), (.49, .10, .47))]
    for side in (-1, 1):
        y = .13 * side
        bear.append(box((.23, y-.035, .54), (.31, y+.035, .64)))
        for x in (-.23, .17):
            bear.append(box((x-.07, y-.055, 0), (x+.07, y+.055, .26)))
    void = [box((-.12, -.11, .10), (.12, .11, .40)), box((-.17, -.23, .34), (.17, .23, .66)),
            box((-.11, -.13, .66), (.11, .13, .88))]
    for y in (-.27, .27):
        void.extend([box((-.12, y-.075, .40), (.12, y+.075, .65)), box((-.09, y-.065, .22), (.11, y+.065, .43))])
    for index, (display, parts, color) in enumerate(((roster.BEAR_DISPLAY, bear, (135, 89, 56)),
                                                     (1132, void, (64, 72, 162)))):
        n, model = 13_000 + index * 10, 64_100 + index
        texture = g.fid(n+2)
        golden["files"].update(g.item_model(n, parts, textures=[(11, 0)]))
        golden["files"][texture] = g.solid_texture(color, g._shade(color, .75))
        golden["tables"]["CreatureModelData"][model] = {"FileDataID": g.fid(n)}
        golden["tables"]["CreatureDisplayInfo"][display] = {"ModelID": model,
                                                           "TextureVariationFileDataID": [texture, 0, 0, 0]}


def write_store(root: Path):
    """Write an isolated fixture in the test FileDataID range."""
    shapes = {kind: weapon_parts(kind) for kind in ("bow", "gun", "blade", "axe", "dagger")}
    golden = g.build(item_looks(), shapes)
    golden["files"].update(g.character(gestures=gestures("PRIEST")))
    for index, c in enumerate(roster.CHARACTERS, 1):
        add_body(golden, c, index)
    add_pets(golden)
    g.write_store(root, golden)
    return golden
