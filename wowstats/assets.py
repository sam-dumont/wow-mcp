"""The 3D view's game files, sent by each player's own shipper.

The 3D view draws a character from the game's own files: client tables
(.db2) that say how a race is built and how each item dresses it, and the
models (.m2, .skin, .skel, .anim) and textures (.blp) they name. Nothing
here fetches them from anywhere: the player's shipper reads them from their
own WoW install (shipper/wow_stats.py Casc, read-only) and sends them to
their own server, which keeps them in that person's own folder
(MODEL3D_ASSETS_DIR; with several people, users/<account>/assets/, which
goes when the person is removed: wowstats.tenancy.delete_tenant). One
person's files are never read for another's pages.

Routes, with the upload password like /ingest (any Host, no page login):

  GET  /install/assets/need?build=<client version>
       what this server still wants: {"build", "tables": {name: file id},
       "files": [file id...], "max_file": bytes}: the tables, the files
       the characters need (worked out from the tables), and what the
       stored models name (skin, skeleton, animation), round by round.
  POST /install/assets/table/<Name>?build=<client version>
       one client table from TABLES (a WDC3-5 file).
  POST /install/assets/file/<file id>
       one model, skin, skeleton, animation or texture this server asked
       for (or that a model it has names), with X-Content-SHA256.

Limits: one file at most MAX_FILE bytes (a character model is 2-9 MB), the
person's daily upload budget shared with /ingest (INGEST_DAILY_MB), and
MODEL3D_ASSETS_MB in all per person (512); a file whose first bytes aren't
the kind it claims is refused. A file id already stored is never stored
twice. Losing the folder is harmless: the next shipper run sends the files
again.

Environment:
  MODEL3D_ASSETS_DIR  where the files are kept (default <MODEL3D_CACHE_DIR>/assets)
  MODEL3D_ASSETS_MB   the most one person's files may take (512)
  MODEL3D_ASSETS_URL  the publisher's, set per person by publish.user_env:
                      read the person's files from the full view's share port
                      (RemoteStore) instead of a folder it can't reach
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

MAX_FILE = 16 * 1024 * 1024
DEFAULT_MB = 512
BUILD = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,7}$")
# What a file is, by its first four bytes: the kinds the 3D view reads.
KINDS = {b"MD21": "m2", b"SKIN": "skin", b"SKL1": "skel", b"AFM2": "anim", b"AFSA": "anim", b"AFSB": "anim",
         b"BLP2": "blp"}
TABLE_MAGIC = (b"WDC3", b"WDC4", b"WDC5")
STAND, HANDS_CLOSED = 0, 15  # AnimationData ids the viewer plays
# The client tables the 3D view reads, with their file ids (the same in
# every build; WoWDBDefs' manifest.json, see THIRD_PARTY.md). Forever's
# root has no file names, so the shipper finds a table by this id only.
TABLES = {
    "AnimationData": 1375431, "CharComponentTextureLayouts": 1360262, "CharComponentTextureSections": 1360263,
    "ChrCustomizationBoneSet": 3460181, "ChrCustomizationChoice": 3450554, "ChrCustomizationCondModel": 3460182,
    "ChrCustomizationDisplayInfo": 3593032, "ChrCustomizationElement": 3512765, "ChrCustomizationGeoset": 3456171,
    "ChrCustomizationMaterial": 3459652, "ChrCustomizationOption": 3384247, "ChrCustomizationSkinnedModel": 3460183,
    "ChrModel": 3384313, "ChrModelMaterial": 3566562, "ChrModelTextureLayer": 3548976, "ChrRaces": 1305311,
    "ChrRaceXChrModel": 3490304, "ComponentModelFileData": 1349053, "ComponentTextureFileData": 1278239,
    "CreatureDisplayInfo": 1108759, "CreatureModelData": 1365368, "HelmetGeosetData": 2821752, "Item": 841626,
    "ItemAppearance": 982462, "ItemDisplayInfo": 1266429, "ItemDisplayInfoMaterialRes": 1280614,
    "ItemDisplayInfoModelMatRes": 4050937, "ItemModifiedAppearance": 982457, "ItemVisuals": 1343605,
    "ItemVisualsXEffect": 3043873, "ModelFileData": 1337833, "SpellItemEnchantment": 1362771,
    "TextureFileData": 982459,
}


class Refused(Exception):
    """An upload this server doesn't take: (HTTP status, why)."""

    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status = status


def kind_of(data: bytes) -> str | None:
    return KINDS.get(data[:4])


def model_refs(data: bytes) -> set[int]:
    """The files a model (.m2) or skeleton (.skel) names that the 3D view
    draws with it, as the shipper follows them (shipper/wow_stats.py
    model_refs; a test keeps the two equal): the first skin, its textures,
    its skeleton or parent skeleton, its standing animation (0) and its
    closed hands (15, HandsClosed: the viewer plays it on a hand that holds
    a weapon)."""
    out: set[int] = set()
    pos = 0
    while pos + 8 <= len(data):
        tag, n = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + n]
        if tag == b"SFID" and len(body) >= 4:
            out.add(struct.unpack_from("<I", body, 0)[0])
        elif tag in (b"TXID", b"SKID"):
            out.update(struct.unpack_from(f"<{len(body) // 4}I", body))
        elif tag == b"AFID":
            for k in range(0, len(body) - 7, 8):
                anim, _sub, fid = struct.unpack_from("<HHI", body, k)
                if anim in (STAND, HANDS_CLOSED):
                    out.add(fid)
        elif tag == b"SKPD" and len(body) >= 12:
            out.add(struct.unpack_from("<I", body, 8)[0])
        pos += 8 + n
    out.discard(0)
    return out


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".part-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def closure(ids, read: Callable[[int], bytes | None], limit: int = 20000) -> tuple[dict[int, int], set[int]]:
    """Every file the viewer loads to draw `ids` (a recipe's needs): those
    and what their models and skeletons name (model_refs), followed to the
    end. `read(fdid)` -> the file or None. Returns ({file id: size} of the
    ones there, {file ids missing})."""
    have: dict[int, int] = {}
    missing: set[int] = set()
    todo = sorted({int(i) for i in ids if i})
    while todo and len(have) + len(missing) < limit:
        fdid = todo.pop()
        if fdid in have or fdid in missing:
            continue
        data = read(fdid)
        if data is None:
            missing.add(fdid)
            continue
        have[fdid] = len(data)
        if kind_of(data) in ("m2", "skel"):
            todo.extend(r for r in model_refs(data) if r not in have and r not in missing)
    return have, missing


class Store:
    """One person's files on disk: tables/<Name>.db2 with tables/build.json,
    files/<id>.<kind>."""

    def __init__(self, root: Path, max_mb: int = DEFAULT_MB):
        self.root = Path(root)
        self.limit = max_mb * 1024 * 1024
        self.lock = threading.Lock()
        self._refs: set[int] | None = None  # what the stored models name, read once

    # -- tables

    def build(self) -> str:
        try:
            return str(json.loads((self.root / "tables" / "build.json").read_text())["build"])
        except (OSError, ValueError, KeyError, TypeError):
            return ""

    def table(self, name: str) -> bytes | None:
        try:
            return (self.root / "tables" / f"{name}.db2").read_bytes() if name in TABLES else None
        except OSError:
            return None

    def tables_needed(self, build: str) -> dict[str, int]:
        """The tables still to send for a client on `build`: all of them for
        a build other than the stored one."""
        have = self.build() == build
        return {n: f for n, f in TABLES.items() if not (have and (self.root / "tables" / f"{n}.db2").is_file())}

    def put_table(self, name: str, build: str, data: bytes) -> None:
        if name not in TABLES:
            raise Refused(404, "not a table the 3D view reads")
        if not BUILD.match(build or ""):
            raise Refused(400, "build is the client's version, like 1.60.1.70959")
        if data[:4] not in TABLE_MAGIC:
            raise Refused(415, "not a client table (WDC3 to WDC5)")
        with self.lock:
            self._room(len(data))
            d = self.root / "tables"
            if self.build() != build:  # another build: its tables replace these, one by one
                for p in d.glob("*.db2") if d.is_dir() else ():
                    p.unlink(missing_ok=True)
                _write(d / "build.json", json.dumps({"build": build}).encode())
            _write(d / f"{name}.db2", data)

    # -- files

    def path(self, fdid: int) -> Path | None:
        d = self.root / "files"
        for kind in set(KINDS.values()):
            p = d / f"{int(fdid)}.{kind}"
            if p.is_file():
                return p
        return None

    def file(self, fdid: int) -> tuple[bytes, str] | None:
        p = self.path(fdid)
        if p is None:
            return None
        try:
            return p.read_bytes(), p.suffix[1:]
        except OSError:
            return None

    def read(self, fdid: int) -> bytes | None:
        got = self.file(fdid)
        return got[0] if got else None

    def stamp(self) -> tuple:
        """What changes when files or tables arrive: the build, and the
        count and newest change of each folder (no file is read)."""
        out: list = [self.build()]
        for sub in ("tables", "files"):
            try:
                st = (self.root / sub).stat()
                out += [st.st_mtime_ns, sum(1 for _ in (self.root / sub).iterdir())]
            except OSError:
                out += [0, 0]
        return tuple(out)

    def have(self) -> set[int]:
        d = self.root / "files"
        out = set()
        for p in d.iterdir() if d.is_dir() else ():
            stem = p.name.split(".", 1)[0]
            if stem.isdigit():
                out.add(int(stem))
        return out

    def refs(self) -> set[int]:
        """What the stored models and skeletons name."""
        if self._refs is not None:
            return set(self._refs)
        out: set[int] = set()
        d = self.root / "files"
        for pat in ("*.m2", "*.skel"):
            for p in d.glob(pat) if d.is_dir() else ():
                try:
                    out |= model_refs(p.read_bytes())
                except OSError:
                    continue
        self._refs = out
        return set(out)

    def put_file(self, fdid: int, data: bytes) -> str:
        kind = kind_of(data)
        if kind is None:
            raise Refused(415, "not a model, skin, skeleton, animation or texture")
        with self.lock:
            if self.path(fdid) is None:  # dedupe by file id: a file id's content never changes
                self._room(len(data))
                _write(self.root / "files" / f"{int(fdid)}.{kind}", data)
                if self._refs is not None and kind in ("m2", "skel"):
                    self._refs |= model_refs(data)
        return kind

    def used(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()) if self.root.is_dir() else 0

    def _room(self, n: int) -> None:
        if self.used() + n > self.limit:
            raise Refused(507, f"the 3D files already take {self.limit // (1024 * 1024)} MB on this server")


class RemoteStore:
    """A person's Store read through the full view's share port (the
    publisher's pod can't mount the full view's volume): GET
    <url>build, <url>table/<Name>, <url>file/<id>, with the share token
    (wowstats.guard.share_open). Read-only. The tables are kept in `cache`
    for their build (read on every publish pass); files are not kept here
    (the publisher reuses its last build's copies: wowstats.publish)."""

    def __init__(self, url: str, env: dict, cache: Path | None):
        self.url = url.rstrip("/") + "/"
        self.env = env
        key = hashlib.sha256(self.url.encode()).hexdigest()[:16]
        self.cache = cache / "assets-remote" / key if cache else None
        self._build: tuple[float, str] | None = None

    def _get(self, rel: str) -> bytes | None:
        import urllib.error

        from wowstats import guard

        try:
            with guard.share_open(self.url + rel, self.env, timeout=60) as r:
                data = r.read(MAX_FILE + 1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise OSError(f"the share port answered {e.code} for {rel}") from None
        if len(data) > MAX_FILE:
            raise OSError(f"{rel}: larger than {MAX_FILE} bytes")
        return data

    def build(self) -> str:
        if self._build and self._build[0] > time.time() - 60:
            return self._build[1]
        try:
            b = str(json.loads(self._get("build") or b"{}").get("build") or "")
        except (OSError, ValueError, AttributeError):
            b = ""
        self._build = (time.time(), b)
        return b

    def table(self, name: str) -> bytes | None:
        if name not in TABLES:
            return None
        build = self.build()
        if not build:
            return None
        local = self.cache / build / f"{name}.db2" if self.cache else None
        if local and local.is_file():
            return local.read_bytes()
        try:
            data = self._get(f"table/{name}")
        except OSError:
            return None
        if data is not None and local is not None:
            for old in self.cache.iterdir() if self.cache.is_dir() else ():
                if old.name != build and old.is_dir():
                    for p in old.iterdir():
                        p.unlink(missing_ok=True)
                    old.rmdir()
            _write(local, data)
        return data

    def read(self, fdid: int) -> bytes | None:
        try:
            data = self._get(f"file/{int(fdid)}")
        except OSError:
            return None
        return data if data and kind_of(data) else None

    def file(self, fdid: int) -> tuple[bytes, str] | None:
        data = self.read(fdid)
        return (data, kind_of(data)) if data else None

    def stamp(self) -> tuple:
        return (self.build(),)

    def have(self) -> set[int] | None:
        return None  # unknown here: every file is asked for when it's needed


def share_route(h, root: Path, rest: str) -> None:
    """The share port's side of RemoteStore, for one person's folder
    (wowstats.analysis.share_server): build, table/<Name>, file/<id>."""
    store = Store(root)
    if rest == "build":
        return h.reply(200, json.dumps({"build": store.build()}).encode(), "application/json")
    m = re.fullmatch(r"table/([A-Za-z]{1,40})", rest)
    if m:
        data = store.table(m.group(1))
        return h.reply(200, data) if data is not None else h.reply(404, b"")
    m = re.fullmatch(r"file/(\d{1,10})", rest)
    if m:
        data = store.read(int(m.group(1)))
        return h.reply(200, data) if data is not None else h.reply(404, b"")
    return h.reply(404, b"")


class Assets:
    """The routes above for one person's App. `wanted` gives the file ids
    the person's characters need (the character assembly: wowstats.model3d
    sets it); until it says, only the tables are asked for."""

    def __init__(self, env: dict, cache_dir: Path | None):
        d = (env.get("MODEL3D_ASSETS_DIR") or "").strip()
        root = Path(d) if d else (cache_dir / "assets" if cache_dir else None)
        try:
            mb = int(env.get("MODEL3D_ASSETS_MB") or DEFAULT_MB)
        except ValueError:
            raise SystemExit(f"MODEL3D_ASSETS_MB={env.get('MODEL3D_ASSETS_MB')!r}: a number of megabytes") from None
        url = (env.get("MODEL3D_ASSETS_URL") or "").strip()
        self.store = RemoteStore(url, env, cache_dir) if url else Store(root, mb) if root else None
        self.wanted: Callable[[], set[int]] = lambda: set()

    def need(self, build: str) -> dict:
        if self.store is None:
            return {"build": build, "tables": {}, "files": [], "max_file": MAX_FILE}
        tables = self.store.tables_needed(build)
        # Files don't wait for the tables: what the characters need comes
        # from the tables (empty until they're here), and a table a build
        # lacks must not hold the rest back.
        files = sorted((self.wanted() | self.store.refs()) - self.store.have())
        return {"build": build, "tables": tables, "files": files, "max_file": MAX_FILE}

    def accepts(self, fdid: int) -> bool:
        return fdid in self.wanted() or fdid in self.store.refs()

    def handle(self, h, rest: str, qs: dict, allows: Callable[[int], bool] | None = None,
               spend: Callable[[int], None] | None = None) -> None:
        """/install/assets/<rest>, for a caller the router already
        authenticated with an upload password. `allows(n)`: whether n more
        bytes fit the person's day; `spend(n)` counts what was stored."""
        if self.store is None:
            return h.json(503, {"error": "no folder for the 3D files (MODEL3D_ASSETS_DIR)"})
        build = (qs.get("build") or [""])[0]
        if rest == "need":
            if h.command not in ("GET", "HEAD"):
                return h.not_found()
            if not BUILD.match(build):
                return h.json(400, {"error": "build is the client's version, like 1.60.1.70959"})
            return h.json(200, self.need(build))
        m = re.fullmatch(r"(table|file)/([A-Za-z]{1,40}|\d{1,10})", rest)
        if not m or h.command != "POST":
            return h.not_found()
        body = h.read_body(MAX_FILE)
        if body is None:
            return h.json(413, {"error": f"send a Content-Length of at most {MAX_FILE} bytes"})
        if allows is not None and not allows(len(body)):
            return h.json(429, {"error": "today's upload budget is used up; the rest goes tomorrow"})
        try:
            if m.group(1) == "table":
                self.store.put_table(m.group(2), build, body)
                if spend is not None:
                    spend(len(body))
                return h.json(200, {"stored": m.group(2)})
            if not m.group(2).isdigit():
                return h.not_found()
            fdid = int(m.group(2))
            want = (h.headers.get("X-Content-SHA256") or "").strip().lower()
            if want != hashlib.sha256(body).hexdigest():
                raise Refused(400, "X-Content-SHA256 doesn't match the body")
            if not self.accepts(fdid):
                raise Refused(409, "this server didn't ask for that file")
            kind = self.store.put_file(fdid, body)
            if spend is not None:
                spend(len(body))
            return h.json(200, {"stored": fdid, "kind": kind})
        except Refused as e:
            return h.json(e.status, {"error": str(e)})
