"""Writes data/foreverdb/item-sources.json: where each piece of gear comes
from, as ForeverDB.net's addon data says (https://foreverdb.net, GPL-3.0
with an attribution term: data/foreverdb/LICENSE.txt). The gear advice
(wowstats.gear) reads it before anything else for where an item comes from.

The source is ForeverDB's own list of its addon's current data files,
https://foreverdb.net/downloads/addon-manifest.json (no key; its robots.txt
offers it to tools), with each file's URL and SHA-256 (owner, 2026-11-24).
Only the files read here are downloaded:

- Data/GearSources.lua: every gear item's sources (quest, vendor, dungeon
  and creature drops, rares, crafts, reputation, PvP, world drops,
  containers), each labelled: s seen in Forever, c a clue (unconfirmed),
  f the Forever client, k Classic data (CMaNGOS classic-db). Plus the
  vendors' and rares' places and the instances.
- Data/Loot.lua: how many looted kills a dungeon drop's chance was measured
  over, and the boss's creature ID.
- Data/GearFinder.lua: the stat weights (tools/foreverdb_weights reads it;
  downloaded here so both exports read the same build).
- Data/Meta.lua: the data version and build, checked against the manifest.

And the addon itself (the manifest's `addon`, SHA-256 checked) for its
LICENSE.txt, NOTICE.txt and COPYING.txt only, copied byte for byte into
data/foreverdb/: the licence's term 7(b) asks that the notices travel with
the data. Nothing in the addon or the data files is run: the files are
read as text by a strict parser (lua.py) that refuses anything but
literals.

Every file must carry the same first line, "-- Data: ForeverDB.net
(https://foreverdb.net), build <build>. ...": it goes first in the JSON
(`_notice`) with the attribution the licence asks for. The other comment
lines of each file describe its format; their hash is the format contract
(`source.contract`): a file whose comments changed is refused until a
person reads them (tools/kb/bump.py foreverdb).

    uv run python tools/foreverdb_sources/export.py              # fetch, check, write
    uv run python tools/foreverdb_sources/export.py --files DIR  # from a verified download (bump.py)
    uv run python tools/foreverdb_sources/export.py --check      # offline: the committed files

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lua  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "foreverdb"
OUT = OUT_DIR / "item-sources.json"
ZONES = ROOT / "data" / "trainers-reference.json"  # uiMap -> zone name, from the client's tables
SITE = "https://foreverdb.net"
MANIFEST = SITE + "/downloads/addon-manifest.json"
UA = "wow-mcp foreverdb export (+https://github.com/sam-dumont/wow-mcp)"
READ = ("Data/GearSources.lua", "Data/Loot.lua", "Data/GearFinder.lua", "Data/Meta.lua")
LICENCE_FILES = ("LICENSE.txt", "NOTICE.txt", "COPYING.txt")
ADDON_DIR = "ForeverDB/"  # the addon's folder in its zip
NOTICE = re.compile(r"^-- Data: ForeverDB\.net \(https://foreverdb\.net\), build (\d+(?:\.\d+){3})\. .*$")
CREDIT = "Data from ForeverDB.net (https://foreverdb.net)"
MAX_BYTES = 8_000_000  # one data file; the biggest today is 1.5 MB
SCHEMA = 1

LABELS = {"s": "seen in Forever", "c": "clue (unconfirmed)", "f": "Forever client", "k": "Classic data"}
STANDINGS = {4: "Neutral", 5: "Friendly", 6: "Honored", 7: "Revered", 8: "Exalted"}
KINDS = ("quest", "vendor", "drop", "creature", "rare", "craft", "reputation", "pvp", "world", "object", "container")


class Refused(Exception):
    """The download or a file isn't what this export reads: nothing is written."""


# --- download ---------------------------------------------------------------


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read(MAX_BYTES * 2 + 1)
    if len(data) > MAX_BYTES * 2:
        raise Refused(f"{url}: bigger than {MAX_BYTES * 2} bytes")
    return data


def manifest_entries(man: dict) -> dict[str, dict]:
    files = {f.get("path"): f for f in (man.get("data") or {}).get("files") or [] if isinstance(f, dict)}
    missing = [p for p in READ if p not in files]
    if missing:
        raise Refused(f"the manifest doesn't list {', '.join(missing)}")
    for p in READ:
        f = files[p]
        if not re.fullmatch(r"[0-9a-f]{64}", str(f.get("sha256") or "")) or not str(f.get("url") or "").startswith(
                "downloads/addon-data/"):
            raise Refused(f"the manifest's entry for {p} has no SHA-256 or an unexpected URL")
    return {p: files[p] for p in READ}


# The files whose content is the data; Data/Meta.lua (the version stamp)
# changes with every build of the site and says nothing new on its own.
CONTENT = tuple(p for p in READ if p != "Data/Meta.lua")


def fingerprint(man: dict) -> str:
    """What the refresh watches (tools/kb/watch.py, kind foreverdb): one
    short hash of the manifest's SHA-256s of the files with the data. A new
    site build that leaves them alone is not a change."""
    files = manifest_entries(man)
    return hashlib.sha256("".join(f"{p} {files[p]['sha256']}\n" for p in CONTENT).encode()).hexdigest()[:16]


def check_bytes(name: str, data: bytes, sha: str) -> None:
    if hashlib.sha256(data).hexdigest() != sha:
        raise Refused(f"{name}: its SHA-256 isn't the manifest's")


def download(into: Path, get=_get) -> dict:
    """The manifest, the files this export reads and the addon's licence
    files into `into`, each checked against the manifest's SHA-256. Returns
    the manifest."""
    raw = get(MANIFEST)
    man = json.loads(raw)
    into.mkdir(parents=True, exist_ok=True)
    (into / "addon-manifest.json").write_bytes(raw)
    for path, f in manifest_entries(man).items():
        data = get(f"{SITE}/{f['url']}")
        check_bytes(path, data, f["sha256"])
        (into / path).parent.mkdir(parents=True, exist_ok=True)
        (into / path).write_bytes(data)
    addon = man.get("addon") or {}
    if not re.fullmatch(r"[0-9a-f]{64}", str(addon.get("sha256") or "")) or not str(addon.get("url") or "").startswith(
            "downloads/"):
        raise Refused("the manifest's addon entry has no SHA-256 or an unexpected URL")
    z = get(f"{SITE}/{addon['url']}")
    check_bytes("the addon", z, addon["sha256"])
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        for name in LICENCE_FILES:
            try:
                data = zf.read(ADDON_DIR + name)
            except KeyError as e:
                raise Refused(f"the addon has no {ADDON_DIR}{name}") from e
            (into / "licence").mkdir(exist_ok=True)
            (into / "licence" / name).write_bytes(data)
    return man


# --- read -------------------------------------------------------------------


def read_files(src: Path) -> tuple[dict, dict, dict]:
    """(manifest, {path: (fields, comments)}, {licence file: bytes}) from a
    download, every data file checked again against the manifest."""
    man = json.loads((src / "addon-manifest.json").read_bytes())
    parsed = {}
    for path, f in manifest_entries(man).items():
        data = (src / path).read_bytes()
        check_bytes(path, data, f["sha256"])
        parsed[path] = lua.read(data.decode("utf-8"), path)
    lic = {n: (src / "licence" / n).read_bytes() for n in LICENCE_FILES}
    return man, parsed, lic


def notice_and_contract(parsed: dict) -> tuple[str, str, dict]:
    """(the notice line every file starts with, the build, {path: hash of its
    other comment lines})."""
    lines = set()
    contract = {}
    for path, (_, comments) in parsed.items():
        if not comments or not NOTICE.match(comments[0]):
            raise Refused(f"{path} doesn't start with ForeverDB's data notice")
        lines.add(comments[0])
        contract[path] = hashlib.sha256("\n".join(comments[1:]).encode()).hexdigest()
    if len(lines) != 1:
        raise Refused("the files don't share one notice line (different builds?)")
    line = lines.pop()
    return line, NOTICE.match(line).group(1), contract


def b36(s: str) -> int:
    return int(s, 36)


def num(s: str):
    if s in ("", None):
        return None
    v = float(s)
    return int(v) if v == int(v) else v


def side(s: str) -> str | None:
    return {"a": "Alliance", "h": "Horde"}.get(s or "")


def label(s: str) -> str:
    if s not in LABELS:
        raise Refused(f"an unknown source label: {s!r}")
    return s


def loot_index(loot: dict) -> dict:
    """{(instance id, boss name): {"npc", "items": {item: (pct, src, kills, seen)}}} from Loot.lua."""
    out = {}
    for inst in loot.get("inst") or []:
        for b in inst.get("bosses") or []:
            items = {}
            for e in str(b.get("loot") or "").split(";"):
                p = e.split(":")
                if len(p) >= 3 and p[0].isdigit():
                    items[int(p[0])] = (num(p[1]), p[2], num(p[4]) if len(p) > 4 else None,
                                        num(p[5]) if len(p) > 5 else None)
            out[(inst.get("id"), b.get("name"))] = {"npc": b.get("npc"), "items": items}
    return out


def source_of(p: list[str], item: int, loot: dict) -> dict:
    """One ForeverDB source (GearFinder.lua GF.item's fields) in plain keys."""
    k = p[0]
    if k == "q":
        s = {"kind": "quest", "quest": int(p[1]), "name": p[2], "min_level": num(p[3]), "side": side(p[4]),
             "label": label(p[5])}
        if len(p) >= 10 and num(p[6]):
            s["giver"] = {"map": int(p[6]), "x": num(p[7]), "y": num(p[8]), "label": label(p[9])}
        return s
    if k == "V":
        return {"kind": "vendor", "npc": int(p[1]), "price": num(p[2]), "side": side(p[3]), "label": label(p[4])}
    if k == "d":
        s = {"kind": "drop", "instance": int(p[1]), "boss": p[2], "pct": num(p[3]) or None, "raid": p[4] == "1",
             "label": label(p[5])}
        b = loot.get((s["instance"], s["boss"]))
        if b:
            if b["npc"]:
                s["npc"] = b["npc"]
            hit = b["items"].get(item)
            # The kills a measured chance stands on: only Forever's (f), never Classic's.
            if hit and hit[1] == "f" and hit[2]:
                s["kills"] = hit[2]
        return s
    if k in ("m", "R"):
        return {"kind": "creature" if k == "m" else "rare", "npc": int(p[1]), "name": p[2], "pct": num(p[3]) or None,
                "label": label(p[4])}
    if k == "c":
        return {"kind": "craft", "profession": p[1], "skill": num(p[2]), "spell": int(p[3]), "label": label(p[4])}
    if k == "w":
        return {"kind": "world", "level_min": num(p[1]) or None, "level_max": num(p[2]) or None, "label": label(p[3])}
    if k == "o":
        return {"kind": "object", "name": p[1], "pct": num(p[2]) or None, "label": label(p[3])}
    if k == "x":
        return {"kind": "container", "item": int(p[1]), "name": p[2], "label": label(p[3])}
    if k == "p":
        return {"kind": "pvp", "rank": int(p[1]), "side": side(p[2]), "label": label(p[3])}
    if k == "r":
        st = int(p[2])
        return {"kind": "reputation", "faction": int(p[1]), "standing": STANDINGS.get(st, str(st)),
                "faction_name": p[3], "label": label(p[4])}
    raise Refused(f"item {item}: an unknown source kind {k!r}")


def build(src: Path, zones: dict | None = None) -> tuple[dict, dict]:
    """(item-sources.json, {licence file: bytes}) from a verified download."""
    man, parsed, lic = read_files(src)
    line, build_id, contract = notice_and_contract(parsed)
    meta = parsed["Data/Meta.lua"][0].get("meta") or {}
    version = (man.get("data") or {}).get("version")
    if meta.get("version") != version or meta.get("build") != build_id:
        raise Refused("Data/Meta.lua's version or build isn't the manifest's and the notice's")
    gs = parsed["Data/GearSources.lua"][0]
    loot = loot_index(parsed["Data/Loot.lua"][0].get("lootDB") or {})
    zones = zones if zones is not None else json.loads(ZONES.read_text(encoding="utf-8")).get("zones") or {}
    items, vendors, rares, insts = {}, set(), set(), set()
    for ln in str(gs.get("gfItemText") or "").splitlines():
        f = ln.split("|")
        if len(f) != 8:
            raise Refused(f"GearSources.lua: an item line with {len(f)} fields, not 8")
        iid = b36(f[0])
        # A chance of 0 is ForeverDB's "unknown": left out, like every empty field.
        srcs = [{k: v for k, v in source_of(x.split("^"), iid, loot).items() if v is not None}
                for x in f[7].split("~") if x]
        if not srcs:
            continue
        for s in srcs:
            {"vendor": vendors, "rare": rares, "drop": insts}.get(s["kind"], set()).add(
                s.get("instance") if s["kind"] == "drop" else s.get("npc"))
        e = {"name": f[1], "sources": srcs}
        if side(f[4]):
            e["side"] = side(f[4])
        items[str(iid)] = e
    npcs = {}
    for nid in sorted(vendors):
        v = str((gs.get("gfVendor") or {}).get(nid) or "").split("^")
        if len(v) == 4:
            npcs[str(nid)] = {"name": v[0], "map": int(v[1]), "zone": zones.get(v[1]), "x": num(v[2]), "y": num(v[3])}
    for nid in sorted(rares):
        raw = str((gs.get("gfRare") or {}).get(nid) or "")
        if "^" in raw:
            name, spots = raw.split("^", 1)
            pts = [[int(a), num(b), num(c)]
                   for a, b, c in (x.split(",") for x in spots.split(";") if x.count(",") == 2)]
            npcs[str(nid)] = {"name": name, "spots": pts,
                              "zone": zones.get(str(pts[0][0])) if pts else None}
    instances = {}
    for iid in sorted(insts):
        v = str((gs.get("gfInst") or {}).get(iid) or "").split("^")
        if len(v) >= 8:
            instances[str(iid)] = {"name": v[0], "kind": v[1], "map": int(v[2]) if v[2] else None, "x": num(v[3]),
                                   "y": num(v[4]), "zone": v[5] or None, "label": v[6] or None}
    entries = manifest_entries(man)
    out = {
        "_notice": [line, CREDIT,
                    "GPL-3.0 with an attribution term (section 7(b)): data/foreverdb/LICENSE.txt, NOTICE.txt, "
                    "COPYING.txt. Written by tools/foreverdb_sources/export.py from ForeverDB's addon data files; "
                    "zone names from the game client's tables (data/trainers-reference.json)."],
        "schema": SCHEMA,
        "source": {
            "name": "ForeverDB.net", "url": SITE, "build": build_id, "data_version": version,
            "addon_version": (man.get("addon") or {}).get("version"), "manifest": MANIFEST,
            "fingerprint": fingerprint(man),
            "files": {p: {"url": f"{SITE}/{f['url']}", "sha256": f["sha256"], "bytes": f.get("bytes")}
                      for p, f in entries.items()},
            "contract": contract,
            "licence": {n: hashlib.sha256(b).hexdigest() for n, b in lic.items()},
        },
        "labels": LABELS,
        "items": dict(sorted(items.items(), key=lambda kv: int(kv[0]))),
        "npcs": npcs,
        "instances": instances,
    }
    return out, lic


ONE_LINE = ("items", "npcs", "instances")


def dump(out: dict) -> str:
    """Indented, except one line per item, NPC and instance: a refresh's
    diff reads as the items whose sources changed."""
    parts = []
    for k, v in out.items():
        if k in ONE_LINE:
            rows = ",\n".join(f"  {json.dumps(i)}: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}"
                              for i, e in v.items())
            parts.append(f" {json.dumps(k)}: {{\n{rows}\n }}")
        else:
            body = json.dumps(v, indent=1, ensure_ascii=False).replace("\n", "\n ")
            parts.append(f" {json.dumps(k)}: {body}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def write(out: dict, lic: dict, root: Path = OUT_DIR) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "item-sources.json").write_text(dump(out), encoding="utf-8")
    for n, b in lic.items():
        (root / n).write_bytes(b)


# --- the committed files ------------------------------------------------------


def problems(d: dict, root: Path = OUT_DIR) -> list[str]:
    """What is wrong with item-sources.json and the licence files beside it
    (the export's --check and the refresh's gates)."""
    bad = []
    n = d.get("_notice") or []
    m = NOTICE.match(n[0]) if n and isinstance(n[0], str) else None
    if not m or len(n) < 2 or n[1] != CREDIT:
        bad.append("_notice: not ForeverDB's notice line then the attribution")
    src = d.get("source") or {}
    if m and m.group(1) != src.get("build"):
        bad.append("_notice: its build isn't source.build")
    if d.get("schema") != SCHEMA:
        bad.append("schema: not 1")
    if set(src.get("files") or {}) != set(READ) or set(src.get("contract") or {}) != set(READ):
        bad.append("source: files or contract don't name the files read")
    for name, sha in (src.get("licence") or {}).items():
        p = root / name
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            bad.append(f"{name}: missing, or not the bytes the export took")
    if set(src.get("licence") or {}) != set(LICENCE_FILES):
        bad.append("source.licence: not the three licence files")
    items = d.get("items") or {}
    if not items:
        bad.append("items: empty")
    for iid, e in items.items():
        if not iid.isdigit() or not isinstance(e.get("name"), str) or not e.get("sources"):
            bad.append(f"items.{iid}: no name or no source")
            continue
        for s in e["sources"]:
            if s.get("kind") not in KINDS or s.get("label") not in LABELS:
                bad.append(f"items.{iid}: a source of kind {s.get('kind')!r}, label {s.get('label')!r}")
        if len(bad) > 20:
            break
    return bad


def check() -> int:
    """Offline: the committed file reads, is in the export's format, and its
    notice, schema and licence files hold."""
    d = json.loads(OUT.read_text(encoding="utf-8"))
    bad = problems(d)
    if OUT.read_text(encoding="utf-8") != dump(d):
        bad.append("not in the export's format: rerun the export")
    for b in bad:
        print(b, file=sys.stderr)
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--files", type=Path, help="read a download made by download() (tools/kb/bump.py) here")
    ap.add_argument("--download", type=Path, help="fetch the manifest's files into this folder, then export")
    ap.add_argument("--check", action="store_true", help="offline: check the committed files")
    a = ap.parse_args(argv)
    if a.check:
        return check()
    try:
        src = a.files
        if src is None:
            import tempfile
            src = a.download or Path(tempfile.mkdtemp(prefix="foreverdb-"))
            download(src)
        out, lic = build(src)
    except (Refused, lua.LuaRefused, OSError, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    write(out, lic)
    n = sum(len(e["sources"]) for e in out["items"].values())
    print(f"wrote {OUT.relative_to(ROOT)}: {len(out['items'])} items, {n} sources, build {out['source']['build']}, "
          f"data {out['source']['data_version']}, {OUT.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
