"""Golden game files: a made-up client for the 3D view, written from scratch
in the formats a player's shipper sends (wowstats.assets): client tables
(WDC5 .db2), models (.m2 with their .skin) and textures (.blp).

    uv run python tools/fixtures/golden3d.py <store dir>   # a person's store: tables/, files/

They serve twice:

- the docs site's demo (site/scripts/demo.py) draws Nibbet with them, live,
  through the product's own path: the store, character3d's assembly, the
  publisher's copy into the bundle and the viewer;
- the 3D tests (tests/test_golden3d.py in CI, tests/test_golden3d_browser.py
  in the local browser gate), which can never have real game files,
  assemble and draw them end to end in Chromium and WebKit.

Nothing in them comes from the game: the meshes are boxes, the textures
are drawn here pixel by pixel, and every id the files and tables make up
is in a range the game doesn't use (files from GOLDEN_FILE, rows from
GOLDEN_ROW). The only real numbers are the ones a player's own data
carries anyway: the race (7, Gnome), the class constants, and the item ids
of the demo character's gear, which the Item tables map to made-up looks.
The table layouts are WoWDBDefs' (data/dbd/, CC BY-SA 4.0), so the
product's reader takes them as it takes a real client's.

The character: a stylised gnome (male), a big head on a short body, with
the geosets the assembly works with: hair styles (group 0), facial hair
(1), gloves (4), boots (5), ears (7), sleeves (8), the robe's skirt (13)
and the cape (15); attachment points for both hands, the shield, both
shoulders, the helmet and the back. Six options: skin, hair style, hair
colour, facial hair, ears and markings (a face layer on its own section).
The items: every piece the demo character wears in his history, plus a
helmet and a shield the tests put on (TEST_HELM, TEST_SHIELD).

Deterministic: the same bytes every run (tests/test_golden3d.py checks).
Small (about 200 kB in all), so only this generator is committed; the demo
build and the tests run it.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from wowstats import assets, db2  # noqa: E402


def _fixtures():
    spec = importlib.util.spec_from_file_location("model3d_fixtures", ROOT / "tools" / "model3d_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FX = _fixtures()
BUILD = "1.60.1.70959"

# ---------------------------------------------------------------- ids

GOLDEN_FILE = 1_900_000_000  # file ids: far above the game's (under 10 million)
GOLDEN_ROW = 1_900_000       # table rows of our own (but MODEL_DATA)
GNOME = 7                    # ChrRaces: the race the character's own data names
MALE = 0


def fid(n: int) -> int:
    return GOLDEN_FILE + n


def rid(n: int) -> int:
    return GOLDEN_ROW + n


CHAR_M2, CHAR_SKIN = fid(1), fid(2)
# CreatureDisplayInfo.ModelID is 16 bits: the model data row sits at the top
# of that range instead, as far from the game's as it goes.
CHR_MODEL, DISPLAY, MODEL_DATA, LAYOUT = rid(1), rid(2), 65_000, rid(4)

# The items the tests put on besides the demo character's own: made-up ids.
TEST_HELM, TEST_SHIELD = rid(900), rid(901)

# ---------------------------------------------------------------- textures

SKINS = ((236, 196, 164), (214, 168, 132), (176, 128, 100))
HAIRS = ((232, 120, 44), (220, 220, 236), (92, 160, 236))  # ginger, white, gnome blue
EYE, MOUTH, FRECKLE = (40, 70, 110), (150, 70, 70), (196, 120, 90)


def _canvas(w: int, h: int, rgb, a: int = 255) -> list[list]:
    return [[(*rgb, a) for _ in range(w)] for _ in range(h)]


def _rect(px, x0, y0, x1, y1, rgba) -> None:
    for y in range(y0, y1):
        for x in range(x0, x1):
            px[y][x] = rgba


def _blp(px) -> bytes:
    h, w = len(px), len(px[0])
    return FX.blp_bgra(w, h, [p for row in px for p in row])


def _shade(rgb, k: float):
    return tuple(max(0, min(255, round(c * k))) for c in rgb)


def skin_texture(rgb) -> bytes:
    """The body's base layer (the whole 512x512 composite at 1/8): the
    skin, and the face in the face section's right half (FACE)."""
    px = _canvas(64, 64, rgb)
    # The face section (x 32-63, y 48-63): its left half is the head's
    # sides and back, the right half its front.
    fx0, fy0 = 48, 48
    _rect(px, fx0 + 2, fy0 + 4, fx0 + 6, fy0 + 7, (255, 255, 255, 255))   # eye whites
    _rect(px, fx0 + 10, fy0 + 4, fx0 + 14, fy0 + 7, (255, 255, 255, 255))
    _rect(px, fx0 + 3, fy0 + 4, fx0 + 5, fy0 + 7, (*EYE, 255))           # pupils
    _rect(px, fx0 + 11, fy0 + 4, fx0 + 13, fy0 + 7, (*EYE, 255))
    _rect(px, fx0 + 7, fy0 + 7, fx0 + 9, fy0 + 10, (*_shade(rgb, 0.85), 255))  # nose
    _rect(px, fx0 + 5, fy0 + 12, fx0 + 11, fy0 + 13, (*MOUTH, 255))      # mouth
    # A plain undergarment where the robe and trousers go, so a bare body
    # never looks unfinished.
    _rect(px, 32, 16, 64, 24, (*_shade(rgb, 0.55), 255))
    _rect(px, 0, 32, 32, 40, (*_shade(rgb, 0.55), 255))
    return _blp(px)


def freckles_texture() -> bytes:
    """The Markings layer, over the whole face section: freckles on the
    cheeks (the right half, the head's front), the rest clear."""
    px = _canvas(32, 8, (0, 0, 0), 0)
    for x, y in ((19, 4), (20, 5), (21, 4), (26, 4), (27, 5), (28, 4)):
        px[y][x] = (*FRECKLE, 255)
    return _blp(px)


def hair_texture(rgb) -> bytes:
    px = _canvas(16, 16, rgb)
    for x in range(0, 16, 3):
        _rect(px, x, 0, x + 1, 16, (*_shade(rgb, 0.8), 255))
    return _blp(px)


def cloth_texture(rgb, trim=None, band: str = "bottom") -> bytes:
    """An item's piece of the body texture: its colour with a weave and a
    trim (bottom, top or none)."""
    px = _canvas(16, 16, rgb)
    for y in range(0, 16, 2):
        for x in range((y // 2) % 2, 16, 2):
            px[y][x] = (*_shade(rgb, 0.9), 255)
    if trim:
        rows = range(13, 16) if band == "bottom" else range(0, 3) if band == "top" else ()
        for y in rows:
            _rect(px, 0, y, 16, y + 1, (*trim, 255))
    return _blp(px)


def belt_texture(rgb, buckle) -> bytes:
    """A band across the top of the torso's lower section, a buckle in its
    middle; clear below, so the robe shows."""
    px = _canvas(16, 16, (0, 0, 0), 0)
    _rect(px, 0, 0, 16, 4, (*rgb, 255))
    _rect(px, 6, 0, 10, 4, (*buckle, 255))
    _rect(px, 7, 1, 9, 3, (*rgb, 255))
    return _blp(px)


def solid_texture(rgb, accent=None) -> bytes:
    px = _canvas(8, 8, rgb)
    if accent:
        _rect(px, 0, 0, 8, 2, (*accent, 255))
    return _blp(px)


# ---------------------------------------------------------------- meshes

# The composite's sections (CharComponentTextureSections, 512x512):
# SectionType -> x, y, w, h. 0-8 as the game numbers them (arms to feet,
# accessory); 9 is the face, ours.
SECTIONS = {0: (0, 0, 128, 128), 1: (128, 0, 128, 128), 2: (256, 0, 128, 128), 3: (0, 128, 256, 128),
            4: (256, 128, 256, 128), 5: (0, 256, 256, 128), 6: (256, 256, 256, 128), 7: (0, 384, 128, 128),
            8: (128, 384, 128, 128), 9: (256, 384, 256, 128)}
CANVAS = 512
FACE = 9


def uv(section: int, part=(0.0, 0.0, 1.0, 1.0)) -> tuple[float, float, float, float]:
    x, y, w, h = SECTIONS[section]
    a, b, c, d = part
    return ((x + a * w) / CANVAS, (y + b * h) / CANVAS, (x + c * w) / CANVAS, (y + d * h) / CANVAS)


def box(lo, hi, rect, front=None, bone: int = 0):
    """FX.box with the front face (+x, towards the camera) on its own uv
    rectangle: the head's face."""
    verts, idx = FX.box(lo, hi, bone, rect)
    if front:
        u0, v0, u1, v1 = front
        for k, (u, v) in enumerate(((u0, v0), (u1, v0), (u1, v1), (u0, v1))):
            p, n, _uv, b = verts[k]
            verts[k] = (p, n, (u, v), b)
    return verts, idx


def merge(*parts):
    verts, idx = [], []
    for v, i in parts:
        base = len(verts)
        verts += v
        idx += [base + k for k in i]
    return verts, idx


def mirror(lo, hi):
    """The same box on the other side (y flipped)."""
    return (lo[0], -hi[1], lo[2]), (hi[0], -lo[1], hi[2])


def pair(lo, hi, rect):
    return merge(box(lo, hi, rect), box(*mirror(lo, hi), rect))


HEAD_LO, HEAD_HI = (-0.15, -0.16, 0.78), (0.15, 0.16, 1.12)
HEAD_CENTRE = (0.0, 0.0, 0.95)
# Attachment points (M2 ids): where items and glows go. WoW's axes: x
# forward, y to the character's left, z up (the camera, in front, sees +y
# on its right).
ATTACH = {
    0: (0.0, 0.27, 0.46),     # shield: the left forearm's outside
    1: (0.06, -0.22, 0.31),   # right hand
    2: (0.06, 0.22, 0.31),    # left hand
    5: (0.0, -0.21, 0.79),    # right shoulder
    6: (0.0, 0.21, 0.79),     # left shoulder
    11: HEAD_CENTRE,          # helmet
    12: (-0.12, 0.0, 0.7),    # back
}


def character() -> dict[int, bytes]:
    """The gnome: (geoset, mesh, texture unit) per submesh."""
    sides = uv(FACE, (0.0, 0.0, 0.5, 1.0))
    face = uv(FACE, (0.5, 0.0, 1.0, 1.0))
    body = merge(
        box(HEAD_LO, HEAD_HI, sides, front=face),
        box((-0.05, -0.06, 0.74), (0.05, 0.06, 0.79), uv(FACE, (0.0, 0.0, 0.2, 0.2))),           # neck
        box((-0.11, -0.17, 0.6), (0.11, 0.17, 0.78), uv(3)),                                      # chest
        box((-0.1, -0.15, 0.45), (0.1, 0.15, 0.6), uv(4)),                                        # belly
        pair((-0.06, 0.03, 0.27), (0.06, 0.13, 0.45), uv(5)),                                     # thighs
        pair((-0.05, 0.04, 0.08), (0.05, 0.12, 0.27), uv(6)),                                     # shins
        pair((-0.07, 0.03, 0.0), (0.13, 0.13, 0.08), uv(7)),                                      # feet
        pair((-0.05, 0.17, 0.57), (0.05, 0.25, 0.78), uv(0)),                                     # upper arms
        pair((-0.045, 0.175, 0.37), (0.045, 0.245, 0.57), uv(1)),                                 # forearms
        pair((-0.04, 0.18, 0.27), (0.06, 0.24, 0.37), uv(2)),                                     # hands
    )
    hair = uv(FACE, (0.0, 0.0, 1.0, 1.0))  # hair takes its own texture (type 6): any rectangle
    top = box((-0.17, -0.18, 1.08), (0.13, 0.18, 1.16), hair)
    subs = [
        (0, body, 0),
        (1, top, 1),                                                                       # a short crop
        (2, merge(top, box((-0.2, -0.17, 0.8), (-0.14, 0.17, 1.12), hair)), 1),            # long at the back
        (3, merge(top, box((-0.08, -0.05, 1.16), (0.04, 0.05, 1.32), hair),                # a tall tuft
                  box((-0.06, -0.04, 1.32), (0.02, 0.04, 1.4), hair)), 1),
        (4, merge(box((-0.08, -0.05, 1.1), (0.04, 0.05, 1.22), hair),                      # two buns
                  pair((-0.06, 0.1, 1.04), (0.04, 0.2, 1.14), hair)), 1),
        (102, box((0.1, -0.12, 0.62), (0.17, 0.12, 0.83), hair), 1),                      # a beard
        (103, merge(box((0.1, -0.12, 0.62), (0.17, 0.12, 0.83), hair),                    # beard and moustache
                    pair((0.15, 0.01, 0.84), (0.18, 0.1, 0.87), hair)), 1),
        (402, pair((-0.055, 0.17, 0.33), (0.075, 0.25, 0.4), uv(2)), 0),                 # glove cuffs
        (502, pair((-0.065, 0.025, 0.06), (0.065, 0.135, 0.2), uv(6, (0, 0.5, 1, 1))), 0),  # boot tops
        (702, pair((-0.03, 0.16, 0.92), (0.03, 0.32, 1.0), sides), 0),                     # long ears
        (703, pair((-0.03, 0.16, 0.9), (0.03, 0.22, 1.0), sides), 0),                      # round ears
        (802, pair((-0.06, 0.165, 0.37), (0.06, 0.255, 0.46), uv(1)), 0),                  # robe sleeves
        (1302, box((-0.12, -0.16, 0.12), (0.12, 0.16, 0.46), uv(5)), 0),                   # robe skirt
        (1502, box((-0.15, -0.16, 0.2), (-0.12, 0.16, 0.78), uv(8)), 2),                   # cape
    ]
    skin_file, verts = FX.skin([(g, v, i) for g, (v, i), _t in subs], [(k, t) for k, (_g, _m, t) in enumerate(subs)])
    m2 = FX.m2(verts=verts, textures=[(1, 0), (6, 0), (2, 0)], bounds=((-0.22, -0.34, 0.0), (0.2, 0.34, 1.42)),
               skin_id=CHAR_SKIN, bones=[lambda d: FX.bone(d, (0.0, 0.0, 0.0), -1, 0)],
               sequences=[FX.sequence(0, 2000, 0x20)],
               attachments=[(a, 0, pos) for a, pos in sorted(ATTACH.items())], material_flags=0x4)
    return {CHAR_M2: m2, CHAR_SKIN: skin_file}


def item_model(n: int, parts, textures=((2, 0),), attachments=()) -> dict[int, bytes]:
    """An item model (file ids fid(n), fid(n + 1)): boxes around its
    attachment point. parts: (verts, idx, texture unit)."""
    subs = [(0, v, i) for v, i, _t in parts]
    skin_file, verts = FX.skin(subs, [(k, t) for k, (_v, _i, t) in enumerate(parts)])
    pts = [p for p, _n, _uv, _b in verts]
    lo = tuple(min(p[k] for p in pts) for k in range(3))
    hi = tuple(max(p[k] for p in pts) for k in range(3))
    m2 = FX.m2(verts=verts, textures=list(textures), bounds=(lo, hi), skin_id=fid(n + 1),
               bones=[lambda d: FX.bone(d, (0.0, 0.0, 0.0), -1, 0)], sequences=[FX.sequence(0, 1000, 0x20)],
               attachments=list(attachments), material_flags=0x4)
    return {fid(n): m2, fid(n + 1): skin_file}


WHOLE = (0.0, 0.0, 1.0, 1.0)


def _b(lo, hi, unit=0):
    v, i = FX.box(lo, hi, 0, WHOLE)
    return (v, i, unit)


def pauldron(side: int):
    """A shoulder pad and its spike, on the left (-1) or right (1)."""
    return [_b((-0.08, -0.07, -0.03), (0.08, 0.07, 0.06)),
            _b((-0.02, -0.02 + 0.03 * side, 0.06), (0.02, 0.02 + 0.03 * side, 0.13))]


def glow_model() -> bytes:
    """The rod's glow: violet sparks rising from its tip."""
    colours = ((0.0, (190.0, 120.0, 255.0)), (1.0, (90.0, 40.0, 200.0)))
    return FX.m2(verts=[], textures=[(0, fid(299))], bounds=((-0.1, -0.1, -0.1), (0.1, 0.1, 0.3)), skin_id=0,
                 bones=[lambda d: FX.bone(d, (0.0, 0.0, 0.0), -1, 0)], sequences=[FX.sequence(0, 1000, 0x20)],
                 particles=[lambda d: FX.particle(d, rate=14.0, color=colours, speed=0.25, lifespan=0.9,
                                                  scale=((0.0, (0.05, 0.05)),), vrange=0.4)])


# ---------------------------------------------------------------- the looks

# What each made-up look is: (kind, colours). Kinds: robe, trousers, belt,
# boots, wrists, gloves, shirt, tabard, cloak (body and cape textures); shoulders, staff,
# mace, rod, helm, shield (models).
ITEMS: dict[int, tuple[str, tuple]] = {
    6264: ("robe", ((96, 112, 150), (214, 214, 230))),   # Greater Adept's Robe: slate blue, white trim
    273044: ("robe", ((110, 52, 150), (228, 184, 70))),  # Violet Sorcerer's Robes: violet, gold trim
    270036: ("trousers", ((52, 60, 96), None)),
    7000: ("belt", ((116, 76, 40), (220, 190, 80))),
    6998: ("boots", ((128, 84, 48), (70, 44, 24))),
    273808: ("wrists", ((170, 120, 60), None)),
    270029: ("gloves", ((196, 168, 120), None)),
    4311: ("cloak", ((120, 120, 112), None)),
    273045: ("cloak", ((40, 140, 150), (200, 230, 240))),
    4315: ("shoulders", ((150, 150, 140), None)),
    6685: ("shoulders", ((70, 50, 90), (210, 200, 220))),
    1312: ("staff", ((150, 110, 70), (120, 200, 120))),
    1317: ("staff", ((112, 80, 52), (140, 220, 120))),
    2816: ("mace", ((90, 80, 100), (150, 60, 200))),
    279838: ("rod", ((60, 70, 140), (180, 140, 255))),
    4323: ("helm", ((60, 50, 70), None)),                # Shadow Hood: on the auction house today
    7048: ("helm", ((60, 110, 190), None)),              # Azure Silk Hood: his own tailoring
    2576: ("shirt", ((236, 232, 220), None)),            # White Linen Shirt
    5976: ("tabard", ((40, 90, 60), (220, 190, 80))),    # Guild Tabard: green, a gold band
    TEST_HELM: ("helm", ((150, 40, 40), None)),
    TEST_SHIELD: ("shield", ((150, 150, 160), (200, 60, 40))),
    7001: ("wand", ((0, 0, 0), None)),                   # a wand in the ranged slot: never drawn
}
# Item (class, subclass, inventory type) for the made-up test items; the
# others' come from the repository's item data (wowstats.gear).
TEST_ITEMS = {TEST_HELM: (4, 4, 1), TEST_SHIELD: (4, 6, 14)}
# Item ids the repository's item data doesn't know: as the game has them.
KNOWN = {1312: (2, 10, 17), 2576: (4, 0, 4), 5976: (4, 0, 19)}
BODY = {"robe": (0, 1, 3, 4, 5), "trousers": (5, 6), "belt": (4,), "boots": (6, 7), "wrists": (1,),
        "gloves": (2,), "shirt": (0, 3), "tabard": (3, 4)}
GEOSETS = {"robe": [1, 0, 1], "boots": [1], "gloves": [1], "cloak": [1]}
HIDE_HAIR = rid(700)  # HelmetGeosetVis: a helmet that hides the hair


def item_kind_rows(gear_ref=None) -> dict[int, tuple[int, int, int]]:
    from wowstats import gear

    ref = gear_ref or gear.reference()
    out = {}
    for item in ITEMS:
        if item in TEST_ITEMS:
            out[item] = TEST_ITEMS[item]
        elif item in KNOWN:
            out[item] = KNOWN[item]
        else:
            info = ref.item(item) or {}
            out[item] = (info["class"], info["subclass"], info["inv"])
    return out


def build() -> dict:
    """{"tables": {name: rows}, "files": {file id: bytes}, "look": [{optionId, choiceId}]}"""
    files: dict[int, bytes] = dict(character())
    t: dict[str, dict[int, dict]] = {n: {} for n in assets.TABLES}
    n_tex = [100]

    def texture(data: bytes) -> int:
        """A texture file and its own MaterialResourcesID."""
        f = fid(n_tex[0])
        n_tex[0] += 1
        files[f] = data
        mat = rid(5000 + f - GOLDEN_FILE)
        t["TextureFileData"][f] = {"FileDataID": f, "UsageType": 0, "MaterialResourcesID": mat}
        return mat

    files[fid(299)] = FX.blp_bgra(4, 4, [(255, 255, 255, 255)] * 16)

    # The race and its model.
    t["ChrRaces"][GNOME] = {"ClientFileString": "Gnome", "Name_lang": "Gnome", "ClientPrefix": "Gn"}
    t["ChrModel"][CHR_MODEL] = {"Sex": MALE, "DisplayID": DISPLAY, "CharComponentTextureLayoutID": LAYOUT}
    t["ChrRaceXChrModel"][rid(10)] = {"ChrRacesID": GNOME, "ChrModelID": CHR_MODEL, "Sex": MALE}
    t["CreatureDisplayInfo"][DISPLAY] = {"ModelID": MODEL_DATA}
    t["CreatureModelData"][MODEL_DATA] = {"FileDataID": CHAR_M2}
    t["CharComponentTextureLayouts"][LAYOUT] = {"Width": CANVAS, "Height": CANVAS}
    for st, (x, y, w, h) in SECTIONS.items():
        t["CharComponentTextureSections"][rid(20 + st)] = {
            "CharComponentTextureLayoutID": LAYOUT, "SectionType": st, "X": x, "Y": y, "Width": w, "Height": h}
    t["ChrModelMaterial"][rid(40)] = {"CharComponentTextureLayoutsID": LAYOUT, "TextureType": 1,
                                      "Width": CANVAS, "Height": CANVAS}
    t["ChrModelMaterial"][rid(41)] = {"CharComponentTextureLayoutsID": LAYOUT, "TextureType": 6, "Width": 64,
                                      "Height": 64}
    # Texture targets: 1 the skin (whole), 5 the face paint (the face
    # section), 10 the hair (texture type 6, whole).
    for k, (target, ttype, blend, mask) in enumerate(((1, 1, 1, -1), (5, 1, 15, 1 << FACE), (10, 6, 1, -1))):
        t["ChrModelTextureLayer"][rid(50 + k)] = {"CharComponentTextureLayoutsID": LAYOUT, "TextureType": ttype,
                                                  "ChrModelTextureTargetID": [target, 0], "BlendMode": blend,
                                                  "TextureSectionTypeBitMask": mask, "Layer": k}

    # The options: (name, flags, [(choice elements)]). An element is
    # ("geoset", type, id) or ("material", target, texture bytes).
    options = [
        ("Skin Color", 0, [[("material", 1, skin_texture(c))] for c in SKINS]),
        ("Hair Style", 0, [[("geoset", 0, k)] for k in (1, 2, 3, 4)]),
        ("Hair Color", 0, [[("material", 10, hair_texture(c))] for c in HAIRS]),
        ("Facial Hair", 0, [[("geoset", 1, 1)], [("geoset", 1, 2)], [("geoset", 1, 3)]]),
        ("Ears", 0, [[("geoset", 7, 2)], [("geoset", 7, 3)]]),
        ("Markings", 0, [[], [("material", 5, freckles_texture())]]),
    ]
    look = []
    picks = {"Skin Color": 1, "Hair Style": 2, "Hair Color": 0, "Facial Hair": 2, "Ears": 0, "Markings": 1}
    el = 0
    for k, (name, flags, choices) in enumerate(options):
        oid = rid(100 + k)
        t["ChrCustomizationOption"][oid] = {"Name_lang": name, "ChrModelID": CHR_MODEL, "Flags": flags,
                                            "OrderIndex": k, "ChrCustomizationID": k + 1}
        for c, elements in enumerate(choices):
            cid = rid(200 + 10 * k + c)
            t["ChrCustomizationChoice"][cid] = {"ChrCustomizationOptionID": oid, "OrderIndex": c,
                                                "Name_lang": f"{name} {c + 1}"}
            if c == picks[name]:
                look.append({"optionId": oid, "choiceId": cid})
            for kind, a, b in elements:
                el += 1
                row = {"ChrCustomizationChoiceID": cid}
                if kind == "geoset":
                    gid = rid(400 + el)
                    t["ChrCustomizationGeoset"][gid] = {"GeosetType": a, "GeosetID": b}
                    row["ChrCustomizationGeosetID"] = gid
                else:
                    mid = rid(600 + el)
                    t["ChrCustomizationMaterial"][mid] = {"ChrModelTextureTargetID": a,
                                                          "MaterialResourcesID": texture(b)}
                    row["ChrCustomizationMaterialID"] = mid
                t["ChrCustomizationElement"][rid(300 + el)] = row

    # The items.
    t["HelmetGeosetData"][rid(710)] = {"HelmetGeosetVisDataID": HIDE_HAIR, "RaceID": GNOME, "HideGeosetGroup": 0}
    n_model = [1000]

    def model(parts, textures=((2, 0),), attachments=()) -> int:
        """One model under a ModelResourcesID of its own."""
        n = n_model[0]
        n_model[0] += 10
        res = rid(800 + n // 10)
        files.update(item_model(n, parts, textures, attachments))
        t["ModelFileData"][fid(n)] = {"FileDataID": fid(n), "ModelResourcesID": res}
        return res

    glow = fid(990)
    files[glow] = glow_model()
    t["ItemVisuals"][rid(950)] = {"ModelFileID": [0, 0, 0, 0, 0]}
    t["ItemVisualsXEffect"][rid(951)] = {"ItemVisualsID": rid(950), "AttachmentID": 1, "DisplayWeaponSubclassID": -1,
                                         "AttachmentModelFileID": glow}

    kinds = item_kind_rows()
    for k, (item, (kind, (main, accent))) in enumerate(sorted(ITEMS.items())):
        cls, sub, inv = kinds[item]
        t["Item"][item] = {"ClassID": cls, "SubclassID": sub, "InventoryType": inv}
        if kind == "wand":
            continue
        display, app = rid(1000 + k), rid(1100 + k)
        t["ItemModifiedAppearance"][rid(1200 + k)] = {"ItemID": item, "ItemAppearanceModifierID": 0,
                                                      "ItemAppearanceID": app, "OrderIndex": 0}
        t["ItemAppearance"][app] = {"ItemDisplayInfoID": display}
        d = {"GeosetGroup": (GEOSETS.get(kind, []) + [0] * 6)[:6], "AttachmentGeosetGroup": [0] * 6,
             "ModelResourcesID": [0, 0], "ModelMaterialResourcesID": [0, 0], "HelmetGeosetVis": [0, 0],
             "ItemVisual": 0}
        if kind in BODY:
            band = "top" if kind == "boots" else "bottom"
            mat = texture(belt_texture(main, accent) if kind == "belt" else cloth_texture(main, accent, band))
            for s in BODY[kind]:
                t["ItemDisplayInfoMaterialRes"][rid(1300 + 10 * k + s)] = {
                    "ItemDisplayInfoID": display, "ComponentSection": s, "MaterialResourcesID": mat}
        elif kind == "cloak":
            d["ModelMaterialResourcesID"] = [texture(cloth_texture(main, accent, "bottom")), 0]
        else:
            mat = texture(solid_texture(main, accent))
            if kind == "shoulders":
                # ModelResourcesID[0] is the left shoulder, [1] the right.
                d["ModelResourcesID"] = [model(pauldron(1)), model(pauldron(-1))]
                d["ModelMaterialResourcesID"] = [mat, mat]
            elif kind == "staff":
                res = model([_b((-0.025, -0.025, -0.28), (0.025, 0.025, 0.86)),
                              _b((-0.06, -0.06, 0.86), (0.06, 0.06, 0.98))],
                            attachments=[(1, 0, (0.0, 0.0, 0.92))])
                d["ModelResourcesID"], d["ModelMaterialResourcesID"] = [res, 0], [mat, 0]
            elif kind == "mace":
                res = model([_b((-0.02, -0.02, -0.05), (0.02, 0.02, 0.22)),
                              _b((-0.05, -0.05, 0.22), (0.05, 0.05, 0.32))],
                            attachments=[(1, 0, (0.0, 0.0, 0.27))])
                d["ModelResourcesID"], d["ModelMaterialResourcesID"] = [res, 0], [mat, 0]
            elif kind == "rod":
                res = model([_b((-0.018, -0.018, -0.04), (0.018, 0.018, 0.2)),
                              _b((-0.035, -0.035, 0.2), (0.035, 0.035, 0.26))],
                            attachments=[(1, 0, (0.0, 0.0, 0.26))])
                d["ModelResourcesID"], d["ModelMaterialResourcesID"] = [res, 0], [mat, 0]
                d["ItemVisual"] = rid(950)
            elif kind == "helm":
                # A hood: crown, back and sides around the head, the face left open.
                res = model([_b((-0.17, -0.18, 0.15), (0.17, 0.18, 0.22)),
                              _b((-0.2, -0.18, -0.2), (-0.15, 0.18, 0.2)),
                              _b((-0.17, 0.16, -0.18), (0.12, 0.2, 0.2)),
                              _b((-0.17, -0.2, -0.18), (0.12, -0.16, 0.2))])
                d["ModelResourcesID"], d["ModelMaterialResourcesID"] = [res, 0], [mat, 0]
                d["HelmetGeosetVis"] = [HIDE_HAIR, HIDE_HAIR]
            elif kind == "shield":
                res = model([_b((-0.11, 0.0, -0.14), (0.11, 0.03, 0.14)),
                              _b((-0.03, 0.03, -0.03), (0.03, 0.045, 0.03))])
                d["ModelResourcesID"], d["ModelMaterialResourcesID"] = [res, 0], [mat, 0]
        t["ItemDisplayInfo"][display] = d
    return {"tables": t, "files": files, "look": look}


# ---------------------------------------------------------------- WDC5


def _block(table: str) -> tuple[str, list[db2.Field]]:
    """The layout hash and fields of `table` for BUILD, from data/dbd/."""
    cols, blocks = db2._shipped(table)
    block = next(b for b in blocks if BUILD in b["builds"])
    layout = sorted(block["layouts"])[0]
    return layout, db2.definition(table, layout, BUILD)


def wdc5(table: str, rows: dict[int, dict]) -> bytes:
    """One table as a WDC5 file in one section: every inline field stored
    as is (no packing), strings in the string block, a non-inline id in the
    id list and a non-inline relation in the relationship map: the subset
    of the format the game's own files use that wowstats.db2 reads."""
    layout, fields = _block(table)
    inline = [f for f in fields if f.inline]
    id_field = next((f for f in fields if f.is_id), None)
    outside_id = id_field is not None and not id_field.inline
    rel = next((f for f in fields if f.relation and not f.inline), None)
    ids = sorted(rows)
    sizes = [(f.size // 8) * (f.count or 1) for f in inline]
    record = sum(sizes)
    offsets = [sum(sizes[:k]) for k in range(len(inline))]
    total = record * len(ids)
    strings = bytearray(b"\0")
    at: dict[str, int] = {"": 0}
    recs = bytearray()
    for i, r in enumerate(ids):
        row = rows[r]
        for f, off in zip(inline, offsets, strict=True):
            n = f.count or 1
            v = r if f.is_id else row.get(f.name, [0] * n if f.count else 0)
            vals = v if f.count else [v]
            if len(vals) != n:
                raise ValueError(f"{table}.{f.name}: {n} values, got {vals!r}")
            if f.kind == "string":
                for j, s in enumerate(vals):
                    s = str(s or "")
                    if s not in at:
                        at[s] = len(strings)
                        strings += s.encode() + b"\0"
                    # db2.Table: an offset from where it is stored, as if every
                    # record came before the strings.
                    ofs = at[s] + total - i * record - (off + 4 * j) if s else 0
                    recs += struct.pack("<I", ofs)
                continue
            code = "f" if f.kind == "float" else db2._INT[(f.size, f.signed)]
            for x in vals:
                try:
                    recs += struct.pack("<" + code, x)
                except struct.error as e:
                    raise ValueError(f"{table}.{f.name} = {x!r}: {e}") from None
    id_list = struct.pack(f"<{len(ids)}I", *ids) if outside_id and ids else b""
    rel_map = b""
    if rel is not None and ids:
        ents = b"".join(struct.pack("<II", int(rows[r].get(rel.name, 0)), i) for i, r in enumerate(ids))
        rel_map = struct.pack("<III", len(ids), 0, 0) + ents
    info = b"".join(struct.pack("<HHIIIII", off * 8, size * 8, 0, db2.NONE, 0, 0, 0)
                    for off, size in zip(offsets, sizes, strict=True)) if ids else b""
    structure = b"".join(struct.pack("<hH", 0, off) for off in offsets) if ids else b""
    id_index = next((k for k, f in enumerate(inline) if f.is_id), 0)
    n_fields = len(inline) if ids else 0
    head = struct.pack("<IIIIIIIIIHHIIIIIII", len(ids), n_fields, record, len(strings), 0, int(layout, 16),
                       min(ids, default=0), max(ids, default=0), 0, 0, id_index, n_fields, 0, 0, len(info), 0, 0, 1)
    pre = 4 + 4 + 128 + len(head) + 40 + len(structure) + len(info)
    section = struct.pack("<QIIIIIIII", 0, pre, len(ids), len(strings), pre + total, len(id_list), len(rel_map), 0, 0)
    return (b"WDC5" + struct.pack("<I", 5) + bytes(128) + head + section + structure + info
            + bytes(recs) + bytes(strings) + id_list + rel_map)


# ---------------------------------------------------------------- the store


def write_store(root: Path, golden: dict | None = None) -> dict:
    """A person's store (wowstats.assets.Store) at `root`: the tables, the
    build and every file. Returns build()'s answer."""
    g = golden or build()
    root = Path(root)
    (root / "tables").mkdir(parents=True, exist_ok=True)
    (root / "files").mkdir(parents=True, exist_ok=True)
    (root / "tables" / "build.json").write_text(json.dumps({"build": BUILD}))
    for name, rows in sorted(g["tables"].items()):
        (root / "tables" / f"{name}.db2").write_bytes(wdc5(name, rows))
    for f, data in sorted(g["files"].items()):
        (root / "files" / f"{f}.{assets.kind_of(data)}").write_bytes(data)
    return g


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    g = write_store(Path(argv[0]))
    size = sum(len(b) for b in g["files"].values())
    print(f"golden3d: {len(g['tables'])} tables, {len(g['files'])} files ({size / 1000:.0f} kB) in {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
