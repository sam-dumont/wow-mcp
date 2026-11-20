"""Writes data/dbd/: the WoWDBDefs definitions of the client tables the 3D
view and the item facts read, trimmed to Forever's builds (wowstats.db2
reads them).

WoWDBDefs (github.com/wowdev/WoWDBDefs, CC BY-SA 4.0, THIRD_PARTY.md) has
one .dbd file per table with every layout since 2004. The server only meets
Forever's client (1.60.x), so each file keeps its COLUMNS section and the
definition blocks whose BUILD lines name a 1.60 build; everything else is
dropped. Nothing is fetched: point it at a WoWDBDefs checkout's
definitions folder.

    uv run python tools/model3d_dbd.py /path/to/WoWDBDefs/definitions

A table with no 1.60 block stops the run: data/dbd/ must cover every table
in TABLES.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "dbd"
PREFIX = "1.60."

# The tables the 3D view reads (the shipper sends these: wowstats/assets.py).
TABLES = (
    "AnimationData", "CharComponentTextureLayouts", "CharComponentTextureSections", "ChrCustomizationBoneSet",
    "ChrCustomizationChoice", "ChrCustomizationCondModel", "ChrCustomizationDisplayInfo", "ChrCustomizationElement",
    "ChrCustomizationGeoset", "ChrCustomizationMaterial", "ChrCustomizationOption", "ChrCustomizationSkinnedModel",
    "ChrModel", "ChrModelMaterial", "ChrModelTextureLayer", "ChrRaces", "ChrRaceXChrModel", "ComponentModelFileData",
    "ComponentTextureFileData", "CreatureDisplayInfo", "CreatureModelData", "HelmetGeosetData", "Item",
    "ItemAppearance", "ItemDisplayInfo", "ItemDisplayInfoMaterialRes", "ItemDisplayInfoModelMatRes",
    "ItemModifiedAppearance", "ItemVisuals", "ItemVisualsXEffect", "ModelFileData", "SpellItemEnchantment",
    "TextureFileData",
    # The item facts' stat budget per item level (wowstats/itemfacts.py).
    "RandPropPoints",
)


def _version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.strip().split("."))


def _is_forever(build: str) -> bool:
    """One BUILD entry (a version or a range "a-b") names a 1.60 build."""
    build = build.strip()
    if "-" in build:
        lo, hi = (_version(x) for x in build.split("-", 1))
        return lo[:2] <= (1, 60) <= hi[:2]
    return build.startswith(PREFIX)


def trim(text: str) -> str | None:
    """The .dbd text with only the COLUMNS and the 1.60 blocks, or None when
    no block is for 1.60."""
    chunks = [c.strip("\n") for c in text.replace("\r\n", "\n").split("\n\n")]
    chunks = [c for c in chunks if c.strip()]
    if not chunks or not chunks[0].startswith("COLUMNS"):
        raise ValueError("no COLUMNS section")
    keep = []
    for c in chunks[1:]:
        builds = [b for ln in c.splitlines() if ln.startswith("BUILD ") for b in ln[6:].split(",")]
        if any(_is_forever(b) for b in builds):
            keep.append(c)
    if not keep:
        return None
    return "\n\n".join([chunks[0], *keep]) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("definitions", type=Path, help="WoWDBDefs' definitions folder")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    missing = []
    for name in TABLES:
        src = a.definitions / f"{name}.dbd"
        out = trim(src.read_text(encoding="utf-8")) if src.is_file() else None
        if out is None:
            missing.append(name)
            continue
        (a.out / f"{name}.dbd").write_text(out, encoding="utf-8")
    for p in a.out.glob("*.dbd"):
        if p.stem not in TABLES:
            p.unlink()
    if missing:
        print("no 1.60 definition for: " + ", ".join(missing), file=sys.stderr)
        return 1
    print(f"wrote {len(TABLES)} definitions to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
