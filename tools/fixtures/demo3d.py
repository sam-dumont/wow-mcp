"""Build the demo's 3D asset store from a local Forever install.

    python tools/fixtures/demo3d.py build-roster OUT [--wow-root ROOT]
    python tools/fixtures/demo3d.py build OUT [--wow-root ROOT]
    uv run --group demo-art python tools/fixtures/demo3d.py build-icons OUT [--wow-root ROOT]

The shipper reads .build.info and Data/. The gear and customization belong
to the invented demo roster. Keep both model styles, the viewer's animation
files, the two companions, known mounts and spells for each recorded level.
Reassemble every outfit from the trimmed tables before packing any files.

build-roster writes store/ and roster-3d.tar.gz with its SHA-256. The store
includes roster.json (version, client build, and choices keyed by character
name). build writes the older Nibbet-only archive. Both keep look.json for
consumers of Nibbet's gear history. Generated assets stay outside git.

build-icons adds the demo's item, spell and talent icons from the same client
to OUT/store/icons (see demo_icons.py) and packs roster-3d.tar.gz again, so
the icons ship in the same release as the 3D art. Run it after build-roster.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tarfile
import tempfile
from collections import defaultdict
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "fixtures")]

import demo_icons  # noqa: E402
import generate  # noqa: E402
import golden3d  # noqa: E402
import roster  # noqa: E402

from wowstats import assets, character3d, db2, gear, history, insights, itemcard, mounts  # noqa: E402

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
    mount_spell: int | None = None
    levels: tuple[int, ...] = (1,)


NIBBET = Profile(generate.DEMO, "Gnome", 0, "PRIEST")


def profiles() -> list[Profile]:
    companions = {"HUNTER": (1, roster.BEAR_DISPLAY), "WARLOCK": (2, 1132)}
    return [NIBBET, *(Profile(c.name, c.race, c.sex - 2, c.cls, companions.get(c.cls), c.mount_spell)
                      for c in roster.CHARACTERS)]


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

def outfit_phases(sheets: list[list[dict]]) -> list[list[dict]]:
    """The distinct outfits and each item on its own, as when the player toggles a slot."""
    seen, out = set(), []
    for items in sheets:
        key = tuple(sorted((it["slot"], it["item_id"], it.get("enchant_id") or 0) for it in items))
        if key not in seen:
            seen.add(key)
            out.append(items)
    every = {(it["slot"], it["item_id"], it.get("enchant_id") or 0): it for items in out for it in items}
    return out + [[it] for key, it in sorted(every.items()) if (key,) not in seen]


def outfits() -> list[list[dict]]:
    return outfit_phases([json.loads(r["items"]) for r in generate.build_demo("2026-01-31") if r["feed"] == "wow-gear"])


def recorded_outfits(rows: list[dict]) -> list[list[dict]]:
    """The saved gear and the inferred outfits the shipper can request from the same records."""
    by_kind = defaultdict(list)
    for row in rows:
        by_kind[row["kind"] if row["feed"] == "wow-recorder" else row["feed"]].append(row)
    gear_rows = sorted(by_kind["wow-gear"], key=lambda r: r["_time"])
    sheets = [json.loads(r["items"]) for r in gear_rows]
    levels = [{"level": int(r["level"]), "at": r["_time"]} for r in by_kind["wow-level"]]
    dings = insights.dings_from(levels, by_kind["level"])
    ref = gear.reference()
    checkpoints = [{"_time": r["_time"], "items": items} for r, items in zip(gear_rows, sheets, strict=True)]
    reconstructed = history.build(ref, gear_rows[-1], by_kind["turnin"], by_kind["loot"], by_kind["craft"],
                                  dings, checkpoints)
    if reconstructed:
        replay, _, names = reconstructed
        times = sorted({span.t for spans in replay.slots.values() for span in spans})
        sheets.extend(history.items_at(ref, replay, t, names) for t in times)
    return sheets


@cache
def roster_outfits() -> dict[Profile, list[list[dict]]]:
    rows = defaultdict(list)
    for r in generate.demo_roster("2026-01-31"):
        if r["feed"] in ("wow-gear", "wow-level", "wow-character") or (
            r["feed"] == "wow-recorder" and r.get("kind") in ("level", "turnin", "loot", "craft")
        ):
            rows[r["char"]].append(r)
    result = {}
    for p in profiles():
        history_rows = rows[p.name]
        levels = {int(r["level"]) for r in history_rows if r.get("level") and
                  (r["feed"] in ("wow-level", "wow-character") or r.get("kind") == "level")}
        result[replace(p, levels=tuple(sorted(levels)))] = outfit_phases(recorded_outfits(history_rows))
    return result


def outfit_levels(p: Profile, outfits: list[list[dict]]):
    """Spell ranks depend on level. A ranged shot also depends on its weapon and ammunition."""
    current = p.levels[-1]
    ranged_outfits = {}
    for items in outfits:
        yield items, current
        ranged = tuple(sorted((i["slot"], i["item_id"]) for i in items if i["slot"] in (0, 18)))
        ranged_outfits.setdefault(ranged, items)
    for level in p.levels[:-1]:
        for items in ranged_outfits.values():
            yield items, level


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
    "mounts": ("Mount", lambda r: r.get("SourceSpellID", 0)),
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
        riding = character3d.mounts(tables, mounts.resolve(ix.mounts, [p.mount_spell])) if p.mount_spell else []
        if p.mount_spell and not riding:
            raise ValueError(f"{p.name}: no mount model for spell {p.mount_spell}")
        for items, level in outfit_levels(p, outfits):
            worn = [{"slot": i["slot"], "item_id": i["item_id"], "name": i.get("name"), "enchant": i.get("enchant_id")}
                    for i in items]
            for model in ("hd", "sd"):
                result = character3d.assemble(tables, rid, p.sex, cid, look, worn, model, level=level)
                recipe = result["recipe"]
                if not recipe:
                    raise ValueError(f"{p.name}: {result['why']}")
                if pet:
                    recipe["companion"] = pet
                if riding:
                    recipe["mounts"] = riding
                if pet or riding:
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
    ix.spell_art.used = {}
    tables = character3d.Tables(dict(ix._rows), "")
    tables.index = ix
    unknown = [a for a, v in vars(ix).items() if isinstance(v, dict) and a not in MAPS and a != "_rows"]
    if unknown:
        raise SystemExit(f"demo3d: Index maps this tool doesn't trim yet: {unknown}")
    answers(tables, looks, sheets)
    keep: dict[str, set[int]] = {n: ix.spell_art.used.get(n, set()) for n in rows}
    for name, t in ix._rows.items():
        keep[name] |= set(t) if t.walked else {k for k in t.asked if dict.__contains__(t, k)}
    for attr, (name, key) in MAPS.items():
        m = getattr(ix, attr)
        if m.walked:  # race_id walks the playable races: that table stays whole
            keep[name] |= set(rows[name])
            continue
        keep[name] |= {i for i, r in rows[name].items() if (i if key is None else key(r)) in m.asked}
    # Rows the Index folds in from the kept ones.
    keep["MountXDisplay"].update(i for i, r in rows["MountXDisplay"].items() if r.get("MountID") in keep["Mount"])
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


def read_looks(root: Path, with_icons: bool = False) -> dict[str, list[dict]]:
    """Read the personalized roster used by the demo. Reject incomplete or legacy stores.
    `with_icons`: the demo build also needs the client icons (build-icons)."""
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
    # Follow both model styles, gear history, spells, companions, mounts and animations.
    if with_icons:
        read_icons(root, build)
    assembled = answers(tables, looks, sheets)
    require_equipment(assembled)
    need = {f for answer in assembled for f in answer.get("needs", ())}
    _, missing = assets.closure(need, store.read)
    if missing:
        raise ValueError(f"demo3d: {root} is missing client files {sorted(missing)[:20]}; rerun build-roster")
    return looks


def read_icons(root: Path, build: str) -> None:
    """The store's client icons: from this build, every named file present."""
    try:
        meta = json.loads((root / "icons.json").read_text())
        names = meta["names"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f"demo3d: {root} has no client icons; run demo3d.py build-icons ({exc})") from exc
    if meta.get("build") != build:
        raise ValueError(f"demo3d: the icons are from client {meta.get('build')}, the store's tables from {build}")
    gone = [f"{size}/{n}" for n in names for size in demo_icons.SIZES
            if not (root / "icons" / size / f"{n}.jpg").is_file()]
    if gone:
        raise ValueError(f"demo3d: {root} is missing icon files {gone[:20]}; rerun build-icons")


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
        # export includes nine characters and the effects of their spells.
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
    n_recipes = sum("recipe" in a for a in full)
    print(f"demo3d: client {casc.version}, {len(looks)} characters, {sum(map(len, sheets.values()))} outfits, "
          f"{n_recipes} HD/SD recipes across their recorded levels")
    print(f"demo3d: {len(small)} tables, {n_rows} rows (of {sum(map(len, rows.values()))})")
    print(f"demo3d: {len(have)} files, {sum(have.values()) / 1e6:.1f} MB; {archive} "
          f"{(out / archive).stat().st_size / 1e6:.1f} MB, sha256 {digest}")
    return 0


def build_icons(wow_root: Path, out: Path) -> int:
    """Add the icons the demo can show to OUT/store and repack the archive."""
    ship = shipper()
    store = out / "store"
    casc = ship.Casc(wow_root)
    if store.joinpath("tables", "build.json").is_file() and assets.Store(store).build() != casc.version:
        raise SystemExit(f"demo3d: {store} is client {assets.Store(store).build()}, this install {casc.version}")
    want = {n: assets.TABLES[n] for n in ("Item", "ItemModifiedAppearance", "ItemAppearance")}
    got, by_table, missing = ship.casc_files(casc, [], {**want, "ManifestInterfaceData": demo_icons.MANIFEST},
                                             follow=False)
    lost = [n for n in (*want, "ManifestInterfaceData") if n not in by_table]
    if lost:
        raise SystemExit(f"demo3d: tables not in this install: {lost} ({missing})")
    tab = {n: db2.Table(got[f], n, casc.version, columns=demo_icons.COLUMNS[n]).rows for n, f in by_table.items()
           if n in want}
    manifest = demo_icons.manifest_rows(got[demo_icons.MANIFEST], casc.version)
    by_name = demo_icons.icon_files(manifest)
    by_fdid = {f: n for n, f in by_name.items()}

    sheets = roster_outfits()
    # Every item in the client, not the ones the roster names: the demo's
    # bags, market and advice pages show far more items than the outfits.
    ids = {*tab["Item"], *(int(r.get("ItemID") or 0) for r in tab["ItemModifiedAppearance"].values())} - {0}
    item_names = demo_icons.item_icon_names(ids, tab["Item"], tab["ItemModifiedAppearance"], tab["ItemAppearance"],
                                            by_fdid)
    talents = demo_icons.talent_icon_names({p.cls for p in sheets})
    names = {*item_names.values(), *talents, itemcard.QUESTION}
    unknown = sorted(n for n in names if n not in by_name)
    no_icon = sorted(i for i in ids if i not in item_names)
    blps, _tables, _absent = ship.casc_files(casc, [by_name[n] for n in names if n in by_name], (), follow=False)
    unread = sorted(n for n in names if n in by_name and by_name[n] not in blps)
    if unknown or unread:
        print(f"demo3d: icon names not found in the client: {unknown}; files not readable: {unread}")
    keep = {n: by_name[n] for n in names if n in by_name and by_name[n] in blps}
    shutil.rmtree(store / "icons", ignore_errors=True)
    store.mkdir(parents=True, exist_ok=True)
    files, size = demo_icons.write(store, casc.version, keep, blps)
    print(f"demo3d: client {casc.version}, {len(ids)} items ({len(no_icon)} without an icon in the tables: "
          f"{no_icon[:20]}), {len(talents)} talent icons")
    print(f"demo3d: {len(keep)} icons, {files} files, {size / 1e6:.2f} MB; not found: {unknown + unread}")
    if (store / "roster.json").is_file():
        digest = pack(store, out / ROSTER_ARCHIVE)
        print(f"demo3d: {ROSTER_ARCHIVE} sha256 {digest}")
    else:
        print("demo3d: no roster.json in the store, so no archive was packed (run build-roster first)")
    return 1 if unknown or unread else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("build", "build-roster", "build-icons"))
    ap.add_argument("out", type=Path)
    ap.add_argument("--wow-root", type=Path, default=Path(os.environ.get("WOW_ROOT") or DEFAULT_WOW))
    a = ap.parse_args(argv)
    if a.cmd == "build-icons":
        return build_icons(a.wow_root, a.out)
    return build(a.wow_root, a.out, roster_models=a.cmd == "build-roster")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
