"""Writes data/model3d-reference.json: how Forever dresses each armor
display, so the 3D view draws the armor Forever added and Wowhead's model
data doesn't have (docs/model3d-reference.md).

Wowhead's viewer dresses a character from one meta per display
(meta/armor/<inventory type>/<display>.json). Forever added displays of its
own (Violet Sash 714142, Graveweave Bindings 715413, Ephemeral Grips 737496
...): Wowhead answers 404 for them, and the model was drawn without them.
Their textures and models are the game's older files, which Wowhead does
serve, so the meta can be written from Forever's own client tables, the
same tables the game reads:

- Item, ItemModifiedAppearance, ItemAppearance: which displays Forever's
  armor uses (the base appearance of each item, as wowstats.model3d reads
  them at runtime), and the item class and subclass of the first item that
  wears it.
- ItemDisplayInfo: geoset groups, models and their materials, helmet
  geoset visibility.
- ItemDisplayInfoMaterialRes: each body section's material.
- TextureFileData, ComponentTextureFileData: a material's texture files and
  who they are for (gender, class, race).
- ModelFileData, ComponentModelFileData: a model's files and who they are
  for (gender, class, race, left or right).
- HelmetGeosetData: what a helmet hides, per race.

Fetched once from wago.tools' CSV export of Forever's client tables, pinned
to BUILD; nothing is fetched at runtime for it. Only the columns above are
kept, for armor displays only. Stdlib only, gzipped, the same file byte for byte
on every run:

    uv run python tools/model3d_reference/export.py
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import urllib.request
from pathlib import Path

WAGO = "https://wago.tools/db2/{table}/csv?build={build}"
PRODUCT = "wowf"  # WoW Forever on wago.tools
BUILD = "1.60.1.70959"
OUT = Path(__file__).resolve().parents[2] / "data" / "model3d-reference.json.gz"
# Inventory types drawn from a meta/armor/ file (wowstats.model3d.ARMOR).
ARMOR = {1, 3, 4, 5, 6, 7, 8, 9, 10, 16, 19, 20}


def table(name: str, build: str) -> list[dict]:
    req = urllib.request.Request(WAGO.format(table=name, build=build), headers={"User-Agent": "wow-stats export"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        raise SystemExit(f"wago.tools has no {name} rows for {build}")
    return rows


def i(v) -> int:
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def build(t: dict[str, list[dict]], build_id: str) -> dict:
    """The reference from the tables (name -> rows), for the armor displays
    Forever's items wear."""
    item = {i(r["ID"]): (i(r["InventoryType"]), i(r["ClassID"]), i(r["SubclassID"])) for r in t["Item"]}
    appearance = {i(r["ID"]): i(r["ItemDisplayInfoID"]) for r in t["ItemAppearance"]}
    # Each item's base look (modifier 0 first, then the order), the first
    # with a display: the rule wowstats.model3d.build_table reads at runtime.
    shown: dict[int, int] = {}
    for r in sorted(t["ItemModifiedAppearance"], key=lambda r: (i(r["ItemAppearanceModifierID"]),
                                                                 i(r["OrderIndex"]))):
        iid, d = i(r["ItemID"]), appearance.get(i(r["ItemAppearanceID"]), 0)
        if iid in item and d and iid not in shown:
            shown[iid] = d
    wanted: dict[int, tuple[int, int]] = {}  # display -> (class, subclass) of its first item
    for iid in sorted(shown):
        inv, cls, sub = item[iid]
        if inv in ARMOR and shown[iid] not in wanted:
            wanted[shown[iid]] = (cls, sub)

    sections: dict[int, dict[str, int]] = {}
    for r in t["ItemDisplayInfoMaterialRes"]:
        d = i(r["ItemDisplayInfoID"])
        if d in wanted and i(r["MaterialResourcesID"]):
            sections.setdefault(d, {})[str(i(r["ComponentSection"]))] = i(r["MaterialResourcesID"])

    displays, mats, models, vis = {}, set(), set(), set()
    for r in t["ItemDisplayInfo"]:
        d = i(r["ID"])
        if d not in wanted:
            continue
        m = [i(r["ModelResourcesID_0"]), i(r["ModelResourcesID_1"])]
        mm = [i(r["ModelMaterialResourcesID_0"]), i(r["ModelMaterialResourcesID_1"])]
        hv = [i(r["HelmetGeosetVis_0"]), i(r["HelmetGeosetVis_1"])]
        displays[str(d)] = {
            "class": list(wanted[d]),
            "geosets": [i(r[f"GeosetGroup_{k}"]) for k in range(6)],
            "attach": [i(r[f"AttachmentGeosetGroup_{k}"]) for k in range(6)],
            "override": i(r["GeosetGroupOverride"]),
            "models": m, "model_materials": mm, "helmet_vis": hv,
            "sections": dict(sorted((sections.get(d) or {}).items(), key=lambda kv: int(kv[0]))),
        }
        mats.update(x for x in mm if x)
        mats.update((sections.get(d) or {}).values())
        models.update(x for x in m if x)
        vis.update(x for x in hv if x)

    ctex = {i(r["ID"]): (i(r["GenderIndex"]), i(r["ClassID"]), i(r["RaceID"])) for r in t["ComponentTextureFileData"]}
    textures: dict[str, list] = {}
    for r in t["TextureFileData"]:
        m = i(r["MaterialResourcesID"])
        if m in mats:
            fid = i(r["FileDataID"])
            g, c, ra = ctex.get(fid, (3, 0, 0))
            textures.setdefault(str(m), []).append([fid, g, c, ra])
    cmod = {i(r["ID"]): (i(r["GenderIndex"]), i(r["ClassID"]), i(r["RaceID"]), i(r["PositionIndex"]))
            for r in t["ComponentModelFileData"]}
    model_files: dict[str, list] = {}
    for r in t["ModelFileData"]:
        m = i(r["ModelResourcesID"])
        if m in models:
            fid = i(r["FileDataID"])
            g, c, ra, pos = cmod.get(fid, (2, 0, 0, -1))
            model_files.setdefault(str(m), []).append([fid, g, c, ra, pos])
    helmet: dict[str, list] = {}
    for r in t["HelmetGeosetData"]:
        v = i(r["HelmetGeosetVisDataID"])
        if v in vis:
            helmet.setdefault(str(v), []).append([i(r["RaceID"]), i(r["HideGeosetGroup"]), i(r["RaceBitSelection"])])

    def ordered(d: dict) -> dict:
        return {k: sorted(v) for k, v in sorted(d.items(), key=lambda kv: int(kv[0]))}

    return {
        "schema": 1,
        "source": {"via": "wago.tools", "product": PRODUCT, "build": build_id,
                   "tables": sorted(t), "note": "Game data (c) Blizzard Entertainment, used as facts"},
        "displays": dict(sorted(displays.items(), key=lambda kv: int(kv[0]))),
        "textures": ordered(textures),
        "models": ordered(model_files),
        "helmet": ordered(helmet),
    }


TABLES = ("Item", "ItemModifiedAppearance", "ItemAppearance", "ItemDisplayInfo", "ItemDisplayInfoMaterialRes",
          "TextureFileData", "ComponentTextureFileData", "ModelFileData", "ComponentModelFileData",
          "HelmetGeosetData")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", default=BUILD, help=f"Forever's client build on wago.tools (default {BUILD})")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    ref = build({name: table(name, a.build) for name in TABLES}, a.build)
    body = (json.dumps(ref, separators=(",", ":")) + "\n").encode()
    # mtime 0 and no file name: the same bytes on every run.
    a.out.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))
    print(f"{a.out}: {len(ref['displays'])} armor displays, {len(ref['textures'])} materials, "
          f"{len(ref['models'])} models", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
