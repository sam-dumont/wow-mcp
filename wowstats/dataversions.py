"""What the bundled static data was built from: each outside source's
version as the shipped files record it, so anyone can see how fresh the
server's data is (owner, 2026-11-25). /api/config and the MCP's
character_summary carry it as `data_versions`.

The refresh job rebuilds these files every day from the newest release of
each source (tools/kb/rebuild.py, docs/kb.md "The refresh job") and every
merged rebuild becomes a release, so on a current image these are at most a
day or two behind upstream. Read from the files themselves, never from the
watch state: a value here is what the server actually serves.

- `foreverdb`: ForeverDB.net's addon data (item sources, stat weights): its
  data version (a date and a hash) and the client build it names;
- `forever_db`: Polestar's quest database release and the client build it
  was made from;
- `polestar`: the Polestar addon release the route, gear, trainer and class
  exports read;
- `client_build`: the Forever client build each set of client tables was
  read at (`zones`: zones, dungeons and factions; `spells`: spells, trainers
  and talents; `items`: the item table, rebuilt by hand with a client's
  hotfix cache; `crafting`: recipes, patterns and bindings);
- `checked`: the day the refresh job last landed a watch change.

A file that is missing or unreadable leaves its field out.
"""

from __future__ import annotations

import gzip
import json
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DATA_DIRS = (_HERE / "data", _HERE.parent / "data")
_LOCK = threading.Lock()
_CACHE: dict[tuple, dict] = {}


def _dir() -> Path | None:
    return next((d for d in DATA_DIRS if (d / "forever-reference.json").is_file()), None)


def _read(path: Path) -> dict:
    try:
        raw = path.read_bytes()
        return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    except (OSError, ValueError):
        return {}


def _get(d: dict, *keys: str):
    for k in keys:
        d = d.get(k) if isinstance(d, dict) else None
    return d if isinstance(d, (str, int)) else None


FILES = ("foreverdb/item-sources.json", "foreverdb-weights.json", "forever-reference.json", "route-reference.json",
         "class-reference.json", "game-items.json", "craft-reference.json", "kb/watch.json")


def build(data: Path) -> dict:
    """The versions from the files in `data` (see the module's doc)."""
    src = _read(data / "foreverdb" / "item-sources.json").get("source") or {}
    weights = _read(data / "foreverdb-weights.json").get("source") or {}
    fref = _read(data / "forever-reference.json").get("source") or {}
    route = _read(data / "route-reference.json")
    cref = _read(data / "class-reference.json").get("source") or {}
    items = _read(data / "game-items.json").get("source") or {}
    craft = _read(data / "craft-reference.json").get("source") or {}
    watch = _read(data / "kb" / "watch.json")
    out: dict = {}
    fdb = {k: v for k, v in (("data_version", _get(src, "data_version")), ("build", _get(src, "build")),
                             ("weights_data_version", _get(weights, "data_version"))) if v}
    if fdb:
        out["foreverdb"] = fdb
    fd = {k: v for k, v in (("version", _get(fref, "forever_db", "version")),
                            ("client_build", _get(fref, "forever_db", "client_build"))) if v}
    if fd:
        out["forever_db"] = fd
    tag = _get(route, "polestar", "tag")
    if tag:
        out["polestar"] = tag
    builds = {k: v for k, v in (("zones", _get(fref, "client_tables", "build")),
                                ("spells", _get(cref, "client", "build")), ("items", _get(items, "build")),
                                ("crafting", _get(craft, "client_tables", "build"))) if v}
    if builds:
        out["client_build"] = builds
    if _get(watch, "checked"):
        out["checked"] = watch["checked"]
    return out


def versions() -> dict:
    """`build` on the bundled data folder, read again only when a file changed."""
    data = _dir()
    if data is None:
        return {}
    key = (str(data), *(_mtime(data / f) for f in FILES))
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is None:
            _CACHE.clear()
            hit = _CACHE[key] = build(data)
    return dict(hit)


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0
