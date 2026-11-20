"""A made-up 3D world for the server-side tests: the hand-written client
tables of tests/test_character3d.py, their file ids moved onto the
synthetic files of tools/model3d_fixtures.py, so a recipe assembled from
the tables names files a person's store really holds and the viewer can
draw. No game file anywhere.

The gnome of test_character3d (race 7) is both sexes on the updated model,
female only on the original one. Her gear for the tests: boots (item 102, a
collection model) and a one-hand weapon (item 103, with a glow).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tests import test_character3d as tc3
from wowstats import assets, character3d

ROOT = Path(__file__).resolve().parent.parent
BUILD = "1.60.1.70959"


def _fixtures():
    spec = importlib.util.spec_from_file_location("model3d_fixtures", ROOT / "tools" / "model3d_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FX = _fixtures()
SCENE = FX.scene()
SD_M2 = 100901  # the original model: the same synthetic body under its own id
# test_character3d's file ids -> the synthetic files'
MOVE = {9001: FX.CHAR_M2, 9002: SD_M2, 8001: FX.TEX_BASE, 8002: FX.TEX_MULT, 8003: FX.TEX_PURPLE,
        8005: FX.TEX_OVERLAY, 9300: FX.BELT_M2, 8300: FX.TEX_CYAN, 9400: FX.ITEM_M2, 8400: FX.TEX_GREEN,
        9500: FX.VISUAL_M2}
FILE_COLUMNS = ("FileDataID", "AttachmentModelFileID", "CollectionsFileDataID")
KEYED_BY_FILE = ("TextureFileData", "ModelFileData", "ComponentTextureFileData", "ComponentModelFileData")
GNOME, PRIEST = tc3.GNOME, tc3.PRIEST
BOOTS, SWORD = 102, 103
GEAR = [{"slot": 8, "slot_name": "Feet", "item_id": BOOTS, "name": "Test Boots"},
        {"slot": 16, "slot_name": "Main Hand", "item_id": SWORD, "name": "Test Sword", "enchant_id": 0},
        {"slot": 2, "slot_name": "Neck", "item_id": 108, "name": "Test Amulet"}]


def world() -> dict:
    w = tc3.world()
    w["ChrRaces"] = {GNOME: {"Name_lang": "Gnome", "ClientFileString": "Gnome"},
                     99: {"Name_lang": "Gnome", "ClientFileString": "Gnome"}}  # a creature race: no model
    w["ChrRaceXChrModel"][2] = {"ChrRacesID": GNOME, "ChrModelID": tc3.HD, "Sex": 0}
    w["ChrCustomizationChoice"][1262] = {"ChrCustomizationOptionID": 87, "OrderIndex": 2}  # no original version
    for oid, name in ((85, "Skin Color"), (87, "Hair Style"), (89, "Markings"), (91, "Limb"), (95, "Hair Style")):
        w["ChrCustomizationOption"][oid]["Name_lang"] = name
    for name, rows in w.items():
        if name in KEYED_BY_FILE:
            rows = {MOVE.get(k, k): v for k, v in rows.items()}
        w[name] = {k: {c: (MOVE.get(v, v) if c in FILE_COLUMNS else v) for c, v in r.items()} for k, r in rows.items()}
    return w


def files() -> dict[int, bytes]:
    """Every synthetic file, plus the original model's copy."""
    out = {fid: data for fid, data in SCENE["files"].items()}
    out[SD_M2] = SCENE["files"][FX.CHAR_M2]
    return out


def fill(root: Path, with_files: bool = True) -> assets.Store:
    """A person's store at `root`: the build (the tables themselves come
    from world(), through patch_tables) and, `with_files`, every file."""
    s = assets.Store(root)
    (root / "tables").mkdir(parents=True, exist_ok=True)
    (root / "tables" / "build.json").write_text(json.dumps({"build": BUILD}))
    (root / "tables" / "ChrModel.db2").write_bytes(b"WDC5 made-up")
    if with_files:
        for fid, data in files().items():
            kind = assets.kind_of(data)
            if kind is None:  # a raw .anim with no chunk: the server refuses those too
                continue
            p = root / "files" / f"{fid}.{kind}"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
    return s


def patch_tables(monkeypatch) -> None:
    """character3d.load_tables reads world() for any store that has a
    build (the made-up rows can't be written as WDC5 files here)."""
    def load(store, build=None):
        return character3d.Tables(world(), BUILD) if store.build() else character3d.Tables({})

    monkeypatch.setattr(character3d, "load_tables", load)
