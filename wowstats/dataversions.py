"""What the bundled static data was built from: each outside source's
version as the shipped files record it, so anyone can see how fresh the
server's data is. Dashboards, snapshots and MCP all read status().

Bundled revisions come from their files. The watch date advances when a
change is accepted. It cannot establish when an upstream last changed.

- `foreverdb`: ForeverDB.net's addon data (item sources, stat weights): its
  data version, addon version, manifest fingerprint and client build;
- `forever_db`: Polestar's quest database release and the client build it
  was made from;
- `polestar`: the Polestar addon release the route, gear, trainer and class
  exports read;
- `client_build`: the Forever client build each set of client tables was
  read at (`zones`: zones, dungeons and factions; `spells`: spells, trainers
  and talents; `items`: the item table, rebuilt by hand with a client's
  hotfix cache; `crafting`: recipes, patterns and bindings);
- `wowsims`: the exporter and sim revisions in the bundled talent order;
- `checked_addons`: the client addon releases the watch last accepted;
- `checked`: the day the refresh job last landed a watch change.

A file that is missing or unreadable leaves its field out.
"""

from __future__ import annotations

import gzip
import json
import threading
from pathlib import Path

from wowstats import __version__

_HERE = Path(__file__).resolve().parent
DATA_DIRS = (_HERE / "data", _HERE.parent / "data")
_LOCK = threading.Lock()
_CACHE: dict[tuple, dict] = {}


def _dir() -> Path | None:
    return next((d for d in DATA_DIRS if (d / "forever-reference.json").is_file()), None)


def _read(path: Path) -> dict:
    try:
        raw = path.read_bytes()
        value = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
        return value if isinstance(value, dict) else {}
    except (EOFError, OSError, ValueError):
        return {}


def _get(d: dict, *keys: str):
    for k in keys:
        d = d.get(k) if isinstance(d, dict) else None
    return d if isinstance(d, (str, int)) else None


FILES = ("foreverdb/item-sources.json", "foreverdb-weights.json", "forever-reference.json", "route-reference.json",
         "class-reference.json", "game-items.json", "craft-reference.json", "wowsims-talents.json",
         "kb/watch.json", "kb/news.json", "kb/community.json")


def _research_files(data: Path) -> list[Path]:
    return sorted((data / "kb/research").glob("*.json"))


def build(data: Path) -> dict:
    """The versions from the files in `data` (see the module's doc)."""
    src = _read(data / "foreverdb" / "item-sources.json").get("source") or {}
    weights = _read(data / "foreverdb-weights.json").get("source") or {}
    fref = _read(data / "forever-reference.json").get("source") or {}
    route = _read(data / "route-reference.json")
    cref = _read(data / "class-reference.json").get("source") or {}
    items = _read(data / "game-items.json").get("source") or {}
    craft = _read(data / "craft-reference.json").get("source") or {}
    wowsims = _read(data / "wowsims-talents.json")
    watch = _read(data / "kb" / "watch.json")
    out: dict = {}
    fdb = {k: v for k, v in (("data_version", _get(src, "data_version")), ("build", _get(src, "build")),
                             ("weights_data_version", _get(weights, "data_version")),
                             ("addon_version", _get(src, "addon_version")),
                             ("fingerprint", _get(src, "fingerprint")),
                             ("weights_fetched", _get(weights, "fetched"))) if v}
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
    revisions = {k: wowsims[k] for k in ("exporter", "sim") if _get(wowsims, k)}
    if revisions:
        out["wowsims"] = revisions
    addons = {a["name"]: a["known"] for a in watch.get("addons") or []
              if _get(a, "name") and _get(a, "repo") and _get(a, "known")}
    if addons:
        out["checked_addons"] = addons
    if _get(watch, "checked"):
        out["checked"] = watch["checked"]
    if _get(watch, "client_build", "known"):
        out["watched_client_build"] = watch["client_build"]["known"]
    if _get(cref, "client", "hotfix", "push"):
        out["spell_hotfix_push"] = cref["client"]["hotfix"]["push"]
    weeks = {}
    for source, key in (("news", "digests"), ("community", "weeks")):
        rows = _read(data / "kb" / f"{source}.json").get(key) or []
        dates = [row["week"] for row in rows if isinstance(_get(row, "week"), str)]
        if dates:
            weeks[source] = max(dates)
    if weeks:
        out["kb_digest_week"] = weeks
    reviews = [date for path in _research_files(data) for row in _read(path).get("statements") or []
               if isinstance(date := _get(row, "last_verified"), str) and date]
    if reviews:
        out["kb_statement_reviews"] = {"oldest": min(reviews), "newest": max(reviews)}
    return out


def status() -> dict:
    """The app release and its bundled source revisions; independent of character data."""
    return {"version": __version__, "data_versions": versions()}


def versions() -> dict:
    """`build` on the bundled data folder, read again only when a file changed."""
    data = _dir()
    if data is None:
        return {}
    key = (str(data), *(_mtime(data / f) for f in FILES), *((f.name, _mtime(f)) for f in _research_files(data)))
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
