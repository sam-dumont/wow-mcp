"""The hosted demo's item, spell and talent icons, read from the game client.

demo3d.py build-icons calls this. It collects every icon name the demo's own
data can show, finds each one's file in the client (ManifestInterfaceData maps
a FileDataID to its icon file name), decodes the BLP and writes JPEGs where
wowstats.upstream.cached_icon looks: <store>/icons/<size>/<name>.jpg. The
build is recorded in <store>/icons.json. A store with that file makes the
publisher take icons from it and from nowhere else.

Pillow (the `demo-art` dependency group) decodes the BLP and encodes the JPEG:
    uv run --group demo-art python tools/fixtures/demo3d.py build-icons OUT
"""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

from wowstats import db2, itemcard
from wowstats.upstream import ICON_NAME

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 1375801  # ManifestInterfaceData's file id (the same in every build)
# Written from the file's own header (2 string fields, ID kept apart); Forever 1.60.1.71139.
MANIFEST_DBD = """COLUMNS
int ID
string FilePath
string FileName

LAYOUT 618AD009
BUILD 1.60.1.71139
$noninline,id$ID<32>
FilePath
FileName
"""
# The sizes the page asks for, in pixels (Wowhead's own: large 56, medium 36).
SIZES = {"large": 56, "medium": 36}
QUALITY = 90
# The columns read from the client's item tables.
COLUMNS = {"Item": ("IconFileDataID",), "ItemAppearance": ("DefaultIconFileDataID",),
           "ItemModifiedAppearance": ("ItemID", "ItemAppearanceModifierID", "ItemAppearanceID", "OrderIndex")}


def jpegs(blp: bytes) -> dict[str, bytes]:
    """A BLP icon as one JPEG per size. Alpha is laid over black."""
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("demo icons: Pillow is missing. Run with `uv run --group demo-art python ...`") from None
    if blp[:4] == b"BLP2" and blp[8] == 3:  # raw BGRA: Pillow's reader doesn't take it
        w, h, offset = struct.unpack_from("<II", blp, 12) + struct.unpack_from("<I", blp, 20)
        img = Image.frombytes("RGBA", (w, h), blp[offset:offset + w * h * 4], "raw", "BGRA")
    else:
        img = Image.open(io.BytesIO(blp))
        img.load()
    img = img.convert("RGBA")
    flat = Image.new("RGB", img.size, (0, 0, 0))
    flat.paste(img, mask=img.getchannel("A"))
    out = {}
    for size, px in SIZES.items():
        buf = io.BytesIO()
        flat.resize((px, px), Image.LANCZOS).save(buf, "JPEG", quality=QUALITY, optimize=True)
        out[size] = buf.getvalue()
    return out


def icon_files(manifest_rows: dict[int, dict]) -> dict[str, int]:
    """icon name -> FileDataID, from ManifestInterfaceData rows (the lowest id
    when two rows share a name)."""
    out: dict[str, int] = {}
    for fdid in sorted(manifest_rows):
        r = manifest_rows[fdid]
        if "icons" in (r.get("FilePath") or "").lower():
            # rstrip("."): a few files have a doubled dot ("..blp"), as talents.py reads them.
            name = itemcard.icon_name((r.get("FileName") or "").rsplit(".", 1)[0].rstrip(".") + ".blp")
            if name:
                out.setdefault(name, fdid)
    return out


def item_icon_names(ids, item_rows, modified_rows, appearance_rows, by_fdid: dict[int, str]) -> dict[int, str]:
    """item id -> icon name, as itemcard.build_cards picks it: the Item row's
    icon, else its appearance's. An item the tables give no icon gets none."""
    app = itemcard.appearance_icons(list(modified_rows.values()), list(appearance_rows.values()))
    out = {}
    for iid in ids:
        row = item_rows.get(iid) or {}
        name = by_fdid.get(int(row.get("IconFileDataID") or 0)) or by_fdid.get(app.get(iid, 0))
        if name:
            out[iid] = name
    return out



def talent_icon_names(classes) -> set[str]:
    """Every talent icon of these classes' frames (data/class-reference.json,
    read from the client's tables)."""
    ref = json.loads((ROOT / "data" / "class-reference.json").read_text(encoding="utf-8"))["classes"]
    names = {t.get("icon") for c in classes for t in (ref.get(c) or {}).get("talents", {}).values()}
    return {n for n in names if isinstance(n, str) and ICON_NAME.fullmatch(n)}


def manifest_rows(blob: bytes, build: str) -> dict[int, dict]:
    return db2.Table(blob, "ManifestInterfaceData", build, dbd=MANIFEST_DBD).rows


def write(store: Path, build: str, names: dict[str, int], blps: dict[int, bytes]) -> tuple[int, int]:
    """JPEGs for every name into <store>/icons, then icons.json. (files, bytes)."""
    icons = store / "icons"
    files = total = 0
    for name, fdid in sorted(names.items()):
        for size, body in jpegs(blps[fdid]).items():
            path = icons / size / f"{name}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            files += 1
            total += len(body)
    (store / "icons.json").write_text(json.dumps({"build": build, "names": sorted(names)}, indent=1) + "\n")
    return files, total
