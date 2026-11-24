"""The demo's real 3D model: Nibbet's own game files, read from an installed
WoW Forever client by the shipper's CASC reader, as the store a player's
shipper fills (wowstats.assets.Store), packed for a release asset.

    uv run python tools/fixtures/demo3d.py build <out dir> [--wow-root <folder with .build.info>]

The owner's call (2026-11-22): the public demo shows a real model, like
Wowhead's dressing room, instead of golden3d's block figure. Blizzard's
files are never committed: this tool writes them to <out dir>, the owner
publishes the archive as the `demo-3d` release (tag `demo-3d-2`), and
the Pages build downloads it (site/scripts/demo3d-asset.sh), checks its
SHA-256 and builds the demo from it; without it the demo keeps golden3d.

What it reads from the install, read-only: `.build.info` and `Data/`
(the shipper's Casc). Nothing else.

What it keeps, Nibbet's alone:
- the files his recipes name (both models, every outfit of his gear
  history), and what those models and skeletons name;
- the client tables, trimmed to the rows his assembly looks up: every
  lookup the assembly makes is recorded, the rows behind it kept, and the
  recipes worked out again from the trimmed tables must match the full
  ones exactly, or the build fails.

The look (look.json) is the Battle.net customization the demo's profile
series carries (tools/fixtures/generate.py bnet_series), as real option and
choice ids of the gnome male model.

Output: <out dir>/store/ (tables/, files/, look.json) and
<out dir>/nibbet-3d.tar.gz with its .sha256 (byte-identical for the same
install: sorted entries, fixed times and owners).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "fixtures")]

import generate  # noqa: E402
import golden3d  # noqa: E402

from wowstats import assets, character3d, db2  # noqa: E402

DEFAULT_WOW = {"darwin": "/Applications/World of Warcraft",
               "win32": r"C:\Program Files (x86)\World of Warcraft"}.get(sys.platform, "")
RACE, SEX, CLASS = "Gnome", "male", "PRIEST"  # build_demo's and bnet_series' Nibbet
# His look: option name -> the choice's place in the option (OrderIndex order).
LOOK = {"Skin Color": 1, "Face": 2, "Hair Style": 3, "Hair Color": 6, "Facial Hair": 2, "Eyebrows": 1,
        "Eye Color": 0, "Eye Style": 0}
ARCHIVE = "nibbet-3d.tar.gz"


def shipper():
    spec = importlib.util.spec_from_file_location("wow_stats_shipper", ROOT / "shipper" / "wow_stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the outfits

def outfits() -> list[list[dict]]:
    """Every gear sheet of the demo's history (build_demo's wow-gear), each
    once, then the last one with each item alone: a recipe's files and rows
    are per item, so the reconstructed outfits in between need nothing more."""
    rows = generate.build_demo("2026-01-31")
    seen, out = set(), []
    for r in sorted((r for r in rows if r["feed"] == "wow-gear"), key=lambda r: r["_time"]):
        items = [{"slot": it["slot"], "item_id": it["item_id"], "name": it.get("name"),
                  "enchant_id": it.get("enchant_id")} for it in json.loads(r["items"])]
        key = tuple(sorted((i["slot"], i["item_id"]) for i in items))
        if key not in seen:
            seen.add(key)
            out.append(items)
    every = {(i["slot"], i["item_id"]): i for sheet in out for i in sheet}
    out += [[i] for _k, i in sorted(every.items())]
    return out


# ------------------------------------------------------------------ recording lookups

class Seen(dict):
    """A dict that remembers which keys were asked for, and whether anyone
    walked all of it."""

    def __init__(self, *a):
        super().__init__(*a)
        self.asked: set = set()
        self.walked = False

    def get(self, k, default=None):
        self.asked.add(k)
        return super().get(k, default)

    def __getitem__(self, k):
        self.asked.add(k)
        return super().__getitem__(k)

    def __contains__(self, k):
        self.asked.add(k)
        return super().__contains__(k)

    def __iter__(self):
        self.walked = True
        return super().__iter__()

    def items(self):
        self.walked = True
        return super().items()

    def values(self):
        self.walked = True
        return super().values()


def _first(v):
    return v[0] if isinstance(v, list) else v


# Index map -> (source table, the key the Index files a row under): the
# same keys as character3d.Index.__init__.
MAPS = {
    "race_models": ("ChrRaceXChrModel", lambda r: (r.get("ChrRacesID", 0), r.get("Sex", 0))),
    "model_by_display": ("ChrModel", lambda r: r.get("DisplayID")),
    "options": ("ChrCustomizationOption", lambda r: r.get("ChrModelID", 0)),
    "choices": ("ChrCustomizationChoice", lambda r: r.get("ChrCustomizationOptionID", 0)),
    "choice_option": ("ChrCustomizationChoice", None),  # filed under the row's own id
    "elements": ("ChrCustomizationElement", lambda r: r.get("ChrCustomizationChoiceID", 0)),
    "choice_geosets": ("ChrCustomizationElement", lambda r: r.get("ChrCustomizationChoiceID", 0)),
    "textures": ("TextureFileData", lambda r: r.get("MaterialResourcesID", 0) if r.get("UsageType", 0) == 0 else None),
    "models": ("ModelFileData", lambda r: r.get("ModelResourcesID", 0)),
    "layers": ("ChrModelTextureLayer", lambda r: (r.get("CharComponentTextureLayoutsID", 0),
                                                  _first(r.get("ChrModelTextureTargetID")))),
    "layout_layers": ("ChrModelTextureLayer", lambda r: r.get("CharComponentTextureLayoutsID", 0)),
    "materials": ("ChrModelMaterial", lambda r: (r.get("CharComponentTextureLayoutsID", 0), r.get("TextureType", 0))),
    "sections": ("CharComponentTextureSections", lambda r: r.get("CharComponentTextureLayoutID", 0)),
    "appearance": ("ItemModifiedAppearance", lambda r: r.get("ItemID", 0)),
    "body_textures": ("ItemDisplayInfoMaterialRes", lambda r: r.get("ItemDisplayInfoID", 0)),
    "model_textures": ("ItemDisplayInfoModelMatRes", lambda r: r.get("ItemDisplayInfoID", 0)),
    "helmet_hides": ("HelmetGeosetData", lambda r: (r.get("HelmetGeosetVisDataID", 0), r.get("RaceID", 0))),
    "effects": ("ItemVisualsXEffect", lambda r: r.get("ItemVisualsID", 0)),
}


def parse(blobs: dict[str, bytes], build: str) -> dict[str, dict[int, dict]]:
    return {name: db2.Table(blobs[name], name, build, columns=character3d.TABLES[name]).rows
            for name in character3d.TABLES}


def answers(tables, look: list[dict], sheets: list[list[dict]]) -> list[dict]:
    """What the server works out for each outfit, both models: the recipes,
    what they miss, the Classic gaps and what is drawn (wowstats.model3d)."""
    rid, cid, sex = character3d.race_id(tables, RACE), character3d.class_id(CLASS), 0
    out = []
    for items in sheets:
        worn = [{"slot": i["slot"], "item_id": i["item_id"], "name": i.get("name"), "enchant": i.get("enchant_id")}
                for i in items]
        for models in ("hd", "sd"):
            out.append(character3d.assemble(tables, rid, sex, cid, look, worn, models))
        out.append({"gaps": character3d.classic_gaps(tables, rid, sex, look),
                    "worn": character3d.worn(tables, worn, cid)})
    ix = character3d.index(tables)
    out.append({"options": [o for o, _r in ix.options.get(ix.race_models.get((rid, sex)), [])]})
    return out


def pick_look(rows: dict[str, dict[int, dict]]) -> list[dict]:
    ix = character3d.Index(rows)
    rid = character3d.race_id(rows, RACE)
    model = ix.race_models[(rid, 0)]
    out = []
    for oid, r in ix.options.get(model, []):
        name = str(r.get("Name_lang") or "")
        if name in LOOK and ix.choices.get(oid):
            ch = ix.choices[oid]
            out.append({"optionId": oid, "choiceId": ch[min(LOOK[name], len(ch) - 1)]})
    return out


def trim(rows: dict[str, dict[int, dict]], look: list[dict], sheets: list[list[dict]]) -> dict[str, dict[int, dict]]:
    """The rows Nibbet's assembly looks up, and the rows behind them."""
    ix = character3d.Index(rows)
    for attr in MAPS:
        setattr(ix, attr, Seen(getattr(ix, attr)))
    ix._rows = {n: Seen(t) for n, t in ix._rows.items()}
    tables = character3d.Tables(dict(ix._rows), "")
    tables.index = ix
    unknown = [a for a, v in vars(ix).items() if isinstance(v, dict) and a not in MAPS and a != "_rows"]
    if unknown:
        raise SystemExit(f"demo3d: Index maps this tool doesn't trim yet: {unknown}")
    answers(tables, look, sheets)
    keep: dict[str, set[int]] = {n: set() for n in rows}
    for name, t in ix._rows.items():
        keep[name] |= set(t) if t.walked else {k for k in t.asked if dict.__contains__(t, k)}
    for attr, (name, key) in MAPS.items():
        m = getattr(ix, attr)
        if m.walked:  # race_id walks the playable races: that table stays whole
            keep[name] |= set(rows[name])
            continue
        keep[name] |= {i for i, r in rows[name].items() if (i if key is None else key(r)) in m.asked}
    # Rows the Index folds in from the kept ones.
    for i in keep["ItemModifiedAppearance"]:
        keep["ItemAppearance"].add(rows["ItemModifiedAppearance"][i].get("ItemAppearanceID", 0))
    for i in keep["ChrCustomizationElement"]:
        e = rows["ChrCustomizationElement"][i]
        for col, table in (("ChrCustomizationGeosetID", "ChrCustomizationGeoset"),
                           ("ChrCustomizationMaterialID", "ChrCustomizationMaterial"),
                           ("ChrCustomizationSkinnedModelID", "ChrCustomizationSkinnedModel"),
                           ("ChrCustomizationCondModelID", "ChrCustomizationCondModel")):
            if e.get(col):
                keep[table].add(e[col])
    for i in keep["ChrCustomizationOption"]:
        keep["ChrCustomizationChoice"] |= {c for c, r in rows["ChrCustomizationChoice"].items()
                                           if r.get("ChrCustomizationOptionID") == i}
    return {n: {i: rows[n][i] for i in sorted(keep[n]) if i in rows[n]} for n in rows}


# ------------------------------------------------------------------ the archive

def pack(store: Path, archive: Path) -> str:
    """A tar.gz of `store` that is the same bytes every time."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for p in sorted(store.rglob("*")):
            info = tar.gettarinfo(str(p), arcname=str(p.relative_to(store.parent)))
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            info.mode = 0o755 if p.is_dir() else 0o644
            if p.is_file():
                with p.open("rb") as f:
                    tar.addfile(info, f)
            else:
                tar.addfile(info)
    with archive.open("wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as gz:
        gz.write(buf.getvalue())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
    return digest


def build(wow_root: Path, out: Path) -> int:
    ship = shipper()
    casc = ship.Casc(wow_root)
    want = {n: assets.TABLES[n] for n in character3d.TABLES}
    got, by_table, missing = ship.casc_files(casc, [], want, follow=False)
    lost = [n for n in character3d.TABLES if n not in by_table]
    if lost:
        raise SystemExit(f"demo3d: tables not in this install: {lost} ({missing})")
    rows = parse({n: got[f] for n, f in by_table.items()}, casc.version)
    look = pick_look(rows)
    sheets = outfits()
    full = answers(character3d.Tables(rows, casc.version), look, sheets)
    small = trim(rows, look, sheets)

    store = out / "store"
    shutil.rmtree(store, ignore_errors=True)
    (store / "tables").mkdir(parents=True)
    (store / "files").mkdir()
    # golden3d's writer: the layout of data/dbd/ for its build, the same
    # layout hash as this client's (the reader picks the definition by it).
    for name, r in small.items():
        (store / "tables" / f"{name}.db2").write_bytes(golden3d.wdc5(name, r))
    (store / "tables" / "build.json").write_text(json.dumps({"build": casc.version}))
    (store / "look.json").write_text(json.dumps(look, indent=1) + "\n")

    again = answers(character3d.load_tables(store / "tables"), look, sheets)
    if json.dumps(again, sort_keys=True, default=list) != json.dumps(full, sort_keys=True, default=list):
        raise SystemExit("demo3d: the trimmed tables give other recipes than the client's: not written")

    needs = set()
    for a in full:
        if a.get("recipe"):
            needs |= set(a["needs"])
    files, _t, absent = ship.casc_files(casc, sorted(needs), (), follow=True)
    for f, data in sorted(files.items()):
        kind = assets.kind_of(data)
        if kind is None:
            raise SystemExit(f"demo3d: file {f} isn't a kind the viewer reads")
        (store / "files" / f"{f}.{kind}").write_bytes(data)
    have, gone = assets.closure(needs, assets.Store(store).read)
    if gone:
        raise SystemExit(f"demo3d: files the recipes need that the install lacks: {sorted(gone)[:20]} {absent}")
    extra = set(files) - set(have)
    for f in extra:  # read by casc_files' follow but named by nothing the viewer loads
        assets.Store(store).path(f).unlink()

    digest = pack(store, out / ARCHIVE)
    n_rows = sum(len(r) for r in small.values())
    size = sum(p.stat().st_size for p in (store / "files").iterdir())
    print(f"demo3d: client {casc.version}, {len(sheets)} outfits, {len(look)} look options")
    print(f"demo3d: {len(small)} tables, {n_rows} rows (of {sum(len(r) for r in rows.values())})")
    print(f"demo3d: {len(have)} files, {size / 1e6:.1f} MB; {ARCHIVE} "
          f"{(out / ARCHIVE).stat().st_size / 1e6:.1f} MB, sha256 {digest}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("out", type=Path)
    b.add_argument("--wow-root", type=Path, default=Path(os.environ.get("WOW_ROOT") or DEFAULT_WOW))
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    return build(a.wow_root, a.out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
