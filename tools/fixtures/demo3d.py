"""Build the demo's 3D asset store from a local Forever install.

    python tools/fixtures/demo3d.py build-roster OUT [--wow-root ROOT]
    python tools/fixtures/demo3d.py build OUT [--wow-root ROOT]

The shipper reads .build.info and Data/. The gear and customization belong
to the invented demo roster. Keep both model styles, the viewer's animation
files, and the two companions. Reassemble every outfit from the trimmed
tables before packing any files.

build-roster writes store/ and roster-3d.tar.gz with its SHA-256. The store
includes roster.json (version, client build, and choices keyed by character
name). build writes the older Nibbet-only archive. Both keep look.json for
consumers of Nibbet's gear history. Generated assets stay outside git.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "fixtures")]

import generate  # noqa: E402
import golden3d  # noqa: E402
import roster  # noqa: E402

from wowstats import assets, character3d, db2  # noqa: E402

DEFAULT_WOW = {"darwin": "/Applications/World of Warcraft",
               "win32": r"C:\Program Files (x86)\World of Warcraft"}.get(sys.platform, "")
# His look: option name -> the choice's place in the option (OrderIndex order).
LOOK = {"Skin Color": 1, "Face": 2, "Hair Style": 3, "Hair Color": 6, "Facial Hair": 2, "Eyebrows": 1,
        "Eye Color": 0, "Eye Style": 0}
ARCHIVE = "nibbet-3d.tar.gz"

ROSTER_ARCHIVE = "roster-3d.tar.gz"


@dataclass(frozen=True)
class Profile:
    name: str
    race: str
    sex: int  # ChrRaceXChrModel, 0 male / 1 female
    cls: str
    companion: tuple[int, int] | None = None


NIBBET = Profile(generate.DEMO, "Gnome", 0, "PRIEST")


def profiles() -> list[Profile]:
    companions = {"HUNTER": (1, roster.BEAR_DISPLAY), "WARLOCK": (2, 1132)}
    return [NIBBET, *(Profile(c.name, c.race, c.sex - 2, c.cls, companions.get(c.cls)) for c in roster.CHARACTERS)]


# Zero-based positions among this race and sex's choices. Options absent
# on a model take the client's defaults. Nibbet keeps his original look.
STYLES = {
    "WARRIOR": {"Skin Color": 5, "Face": 4, "Hair Style": 10, "Hair Color": 1, "Piercings": 3},
    "PALADIN": {"Skin Color": 8, "Face": 3, "Hair Style": 7, "Hair Color": 4, "Eyebrows": 2},
    "HUNTER": {"Skin Color": 2, "Face": 5, "Hair Style": 4, "Hair Color": 3, "Beard": 6, "Mustache": 3},
    "ROGUE": {"Skin Color": 5, "Face": 2, "Hair Style": 8, "Hair Color": 4, "Feathers": 1, "Feather Color": 3},
    "SHAMAN": {"Skin Color": 2, "Face": 3, "Horn Style": 4, "Horn Color": 1, "Facial Hair": 2, "Hair": 2},
    "MAGE": {"Skin Color": 3, "Face": 2, "Hair Style": 6, "Hair Color": 3, "Tusks": 3},
    "WARLOCK": {"Skin Color": 2, "Face": 4, "Hair Style": 4, "Hair Color": 1, "Jaw Features": 3},
    "DRUID": {"Skin Color": 4, "Face": 2, "Hair Style": 3, "Hair Color": 5, "Facial Hair": 4, "Eyebrows": 2},
    "PRIEST": LOOK,
}


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


def roster_outfits() -> dict[Profile, list[list[dict]]]:
    return {NIBBET: outfits(), **{p: [roster.worn(c)] for p, c in zip(profiles()[1:], roster.CHARACTERS, strict=True)}}


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


def answers(tables, looks: dict[str, list[dict]], sheets: dict[Profile, list[list[dict]]]) -> list[dict]:
    """Record the same assembly, wearability and appearance lookups as the server."""
    out = []
    ix = character3d.index(tables)
    for p, outfits in sheets.items():
        rid, cid = character3d.race_id(tables, p.race), character3d.class_id(p.cls)
        look = looks[p.name]
        pet = character3d.companion(tables, p.companion)
        if p.companion and not pet:
            raise ValueError(f"{p.name}: no companion for display {p.companion[1]}")
        for items in outfits:
            worn = [{"slot": i["slot"], "item_id": i["item_id"], "name": i.get("name"), "enchant": i.get("enchant_id")}
                    for i in items]
            for model in ("hd", "sd"):
                result = character3d.assemble(tables, rid, p.sex, cid, look, worn, model)
                recipe = result["recipe"]
                if not recipe:
                    raise ValueError(f"{p.name}: {result['why']}")
                if pet:
                    recipe["companion"] = pet
                    result["needs"] = sorted(character3d.needs(recipe))
                out.append(result)
            out.append({"gaps": character3d.classic_gaps(tables, rid, p.sex, look),
                        "worn": character3d.worn(tables, worn, cid)})
        out.append({"options": [o for o, _r in ix.options.get(ix.race_models.get((rid, p.sex)), [])]})
    return out


def pick_look(tables, p: Profile) -> list[dict]:
    ix = character3d.index(tables)
    model = ix.race_models[(character3d.race_id(tables, p.race), p.sex)]
    style = STYLES[p.cls]
    out = []
    for oid, row in ix.options.get(model, []):
        name = row.get("Name_lang")
        choices = ix.choices.get(oid)
        if name in style and choices:
            out.append({"optionId": oid, "choiceId": choices[min(style[name], len(choices) - 1)]})
    if not out:
        raise ValueError(f"{p.name}: no customization choices for {p.race} model {model}")
    return out


def trim(rows: dict[str, dict[int, dict]], looks: dict[str, list[dict]], sheets: dict[Profile, list[list[dict]]]
         ) -> dict[str, dict[int, dict]]:
    """Keep every row read while assembling the requested profiles and outfits."""
    ix = character3d.Index(rows)
    for attr in MAPS:
        setattr(ix, attr, Seen(getattr(ix, attr)))
    ix._rows = {n: Seen(t) for n, t in ix._rows.items()}
    tables = character3d.Tables(dict(ix._rows), "")
    tables.index = ix
    unknown = [a for a, v in vars(ix).items() if isinstance(v, dict) and a not in MAPS and a != "_rows"]
    if unknown:
        raise SystemExit(f"demo3d: Index maps this tool doesn't trim yet: {unknown}")
    answers(tables, looks, sheets)
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
    keep["ChrCustomizationChoice"].update(c for c, r in rows["ChrCustomizationChoice"].items()
                                          if r.get("ChrCustomizationOptionID") in keep["ChrCustomizationOption"])
    return {n: {i: rows[n][i] for i in sorted(keep[n]) if i in rows[n]} for n in rows}


# ------------------------------------------------------------------ the archive

def pack(store: Path, archive: Path) -> str:
    """A tar.gz of `store` that is the same bytes every time."""
    with (
        archive.open("wb") as f,
        gzip.GzipFile(fileobj=f, mode="wb", compresslevel=9, mtime=0, filename="") as gz,
        tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as tar,
    ):
        for p in sorted(store.rglob("*")):
            info = tar.gettarinfo(str(p), arcname=str(p.relative_to(store.parent)))
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            info.mode = 0o755 if p.is_dir() else 0o644
            if p.is_file():
                with p.open("rb") as src:
                    tar.addfile(info, src)
            else:
                tar.addfile(info)
    with archive.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
    return digest


def read_looks(root: Path) -> dict[str, list[dict]]:
    """Read the personalized roster used by the demo. Reject incomplete or legacy stores."""
    store = assets.Store(root)
    try:
        meta = json.loads((root / "roster.json").read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"demo3d: {root} needs roster.json from demo3d.py build-roster: {exc}") from exc
    build = store.build()
    if not isinstance(meta, dict) or meta.get("version") != 1 or not build or meta.get("build") != build:
        raise ValueError("demo3d: roster.json needs version 1 and the client build from tables/build.json")
    sheets = roster_outfits()
    looks = meta.get("looks")
    if not isinstance(looks, dict) or set(looks) != {p.name for p in sheets}:
        raise ValueError("demo3d: roster.json needs the appearance of every demo character")
    tables = character3d.load_tables(store)
    if tables.problems:
        raise ValueError(f"demo3d: unreadable roster tables: {tables.problems}")
    ix = character3d.index(tables)
    for p in sheets:
        rid = character3d.race_id(tables, p.race)
        model = ix.race_models.get((rid, p.sex))
        options = {oid for oid, _ in ix.options.get(model, [])}
        choices = looks[p.name]
        if not isinstance(choices, list) or not choices or any(
            not isinstance(c, dict) or not isinstance(c.get("optionId"), int) or c["optionId"] not in options
            or c.get("choiceId") not in ix.choices.get(c["optionId"], ()) for c in choices
        ):
            raise ValueError(f"demo3d: {p.name} needs choices for its own model")
    # A local store may be from an older roster or may have lost a file.
    # Follow both model styles, gear history, companion textures and animations.
    assembled = answers(tables, looks, sheets)
    require_equipment(assembled)
    need = {f for answer in assembled for f in answer.get("needs", ())}
    _, missing = assets.closure(need, store.read)
    if missing:
        raise ValueError(f"demo3d: {root} is missing client files {sorted(missing)[:20]}; rerun build-roster")
    return looks


def require_equipment(assembled: list[dict]) -> None:
    missing = {(m["item_id"], m["why"]) for a in assembled for m in a.get("missing", ())}
    if missing:
        raise ValueError(f"demo3d: unresolved roster equipment: {sorted(missing)}")


def build(wow_root: Path, out: Path, *, roster_models: bool = False) -> int:
    ship = shipper()
    casc = ship.Casc(wow_root)
    want = {n: assets.TABLES[n] for n in character3d.TABLES}
    got, by_table, missing = ship.casc_files(casc, [], want, follow=False)
    lost = [n for n, f in want.items() if f not in got]
    if lost:
        raise SystemExit(f"demo3d: tables not in this install: {lost} ({missing})")
    rows = parse({n: got[f] for n, f in by_table.items()}, casc.version)
    sheets = roster_outfits() if roster_models else {NIBBET: outfits()}
    tables = character3d.Tables(rows, casc.version)
    looks = {p.name: pick_look(tables, p) for p in sheets}
    full = answers(tables, looks, sheets)
    if roster_models:
        require_equipment(full)
    small = trim(rows, looks, sheets)
    archive = ROSTER_ARCHIVE if roster_models else ARCHIVE

    # Keep the previous store usable if this install lacks any recipe or file.
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as temp:
        stage = Path(temp)
        store = stage / "store"
        (store / "tables").mkdir(parents=True)
        (store / "files").mkdir()
        for name, r in small.items():
            (store / "tables" / f"{name}.db2").write_bytes(golden3d.wdc5(name, r))
        (store / "tables" / "build.json").write_text(json.dumps({"build": casc.version}))
        (store / "look.json").write_text(json.dumps(looks[NIBBET.name], indent=1) + "\n")
        if roster_models:
            meta = {"version": 1, "build": casc.version, "looks": looks}
            (store / "roster.json").write_text(json.dumps(meta, indent=1) + "\n")
        again = answers(character3d.load_tables(store / "tables"), looks, sheets)
        if again != full:
            raise SystemExit("demo3d: the trimmed tables give other recipes than the client's")

        needs = {f for a in full for f in a.get("needs", ())}
        # The shipper limits each ordinary upload to 512 MiB. This offline
        # export can contain nine HD models, all their outfits and both pets.
        files, _t, absent = ship.casc_files(casc, sorted(needs), (), follow=True, max_bytes=2 * 1024**3)
        anims = set().union(*(assets.anim_refs(d) for d in files.values() if assets.kind_of(d) in ("m2", "skel")))
        have, gone = assets.closure(needs, files.get)
        if gone:
            raise SystemExit(f"demo3d: missing files in client: { {f: absent.get(f) for f in sorted(gone)} }")
        for f in sorted(have):
            data = files[f]
            kind = assets.kind_of(data, anim=f in anims)
            if kind is None:
                raise SystemExit(f"demo3d: file {f} isn't a kind the viewer reads")
            (store / "files" / f"{f}.{kind}").write_bytes(data)
        if roster_models:
            read_looks(store)
        digest = pack(store, stage / archive)
        if (out / "store").exists():
            (out / "store").rename(stage / "previous")
        store.rename(out / "store")
        for name in (archive, archive + ".sha256"):
            (stage / name).replace(out / name)

    n_rows = sum(map(len, small.values()))
    print(f"demo3d: client {casc.version}, {len(looks)} characters, {sum(map(len, sheets.values()))} outfits")
    print(f"demo3d: {len(small)} tables, {n_rows} rows (of {sum(map(len, rows.values()))})")
    print(f"demo3d: {len(have)} files, {sum(have.values()) / 1e6:.1f} MB; {archive} "
          f"{(out / archive).stat().st_size / 1e6:.1f} MB, sha256 {digest}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("build", "build-roster"))
    ap.add_argument("out", type=Path)
    ap.add_argument("--wow-root", type=Path, default=Path(os.environ.get("WOW_ROOT") or DEFAULT_WOW))
    a = ap.parse_args(argv)
    return build(a.wow_root, a.out, roster_models=a.cmd == "build-roster")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
