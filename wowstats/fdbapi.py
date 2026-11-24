"""What the ForeverDB.net API told us at release time: data/foreverdb/api.json
(tools/foreverdb_api/export.py, run by the release workflow with the
project's key and bundled into the image; never committed, no key at
runtime).

It fills what the game client and the GPL data can't: the vendors that sell
each weapon and armour piece (where they stand, the price, the reputation
that discounts it, Merchant's Favor offers), and who gives and takes each
quest and where they stand. Wherever an answer or the page shows any of it,
it credits ForeverDB.net with the link (CREDIT, `data_credit`), as the API's
terms ask.

No file (a checkout, a fork's image, a release whose fetch failed): every
reader gets None or empty, and the gear advice falls back to Polestar's and
forever-db's sources alone.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_HERE = Path(__file__).resolve()
FILES = (_HERE.parent / "data" / "foreverdb" / "api.json", _HERE.parents[1] / "data" / "foreverdb" / "api.json")
CREDIT = {"name": "ForeverDB.net", "url": "https://foreverdb.net", "text": "Data from ForeverDB.net"}
# What marks a source or a quest end as the API's: the answers carry it, the
# MCP adds `data_credit` and the page shows the link (tests/test_credit_surfaces.py).
MARK = "foreverdb"
# The same mark in words, for answers that say a source as text
# (wowstats.gear.source_text).
TAG = "(data: ForeverDB.net)"

_LOCK = threading.Lock()
_DATA: list = []


def data() -> dict:
    """The file, read once; {} when there is none (FOREVERDB_DATA_FILE=off too)."""
    with _LOCK:
        if not _DATA:
            setting = os.environ.get("FOREVERDB_DATA_FILE", "").strip()
            paths = [] if setting.lower() == "off" else ([Path(setting)] if setting else list(FILES))
            got: dict = {}
            for p in paths:
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("schema") == 1:
                    got = d
                    break
            _DATA.append(got)
        return _DATA[0]


def reset() -> None:
    """Forget the file read (tests)."""
    with _LOCK:
        _DATA.clear()


def source() -> dict | None:
    """The file's `source` (build, fetch time, credit) for `data.*` meta."""
    s = data().get("source")
    return {k: s.get(k) for k in ("name", "url", "build", "fetched")} if s else None


def vendor(vid) -> dict | None:
    return (data().get("vendors") or {}).get(str(vid))


def sales(item_id) -> list[dict]:
    """Every vendor offer of an item: gold ones (`price`, copper) and
    Merchant's Favor ones (`favor`, `rank`)."""
    d = data()
    return [*(d.get("sold") or {}).get(str(item_id), ()), *(d.get("favor") or {}).get(str(item_id), ())]


def quest(qid) -> dict | None:
    return (data().get("quests") or {}).get(str(qid))


def in_forever(item_id) -> bool | None:
    """Whether the Forever client has the weapon or armour piece; None when
    the file doesn't say (no file, or a sample without the item list)."""
    ids = data().get("in_forever")
    if not ids:
        return None
    s = _ids()
    return int(item_id) in s


_IDS: list = []


def _ids() -> set:
    with _LOCK:
        if not _IDS or _IDS[0][0] is not data():
            _IDS[:] = [(data(), {int(i) for i in data().get("in_forever") or ()})]
        return _IDS[0][1]


def place(at: dict | None) -> dict:
    """A place as the answers carry it: zone, x, y (map percent)."""
    at = at or {}
    return {k: at[k] for k in ("zone", "x", "y") if at.get(k) is not None}


def spot(p: dict) -> str:
    """' in Ashenvale (73.8, 61.5)': a place in words."""
    xy = f" ({p['x']:.1f}, {p['y']:.1f})" if p.get("x") is not None and p.get("y") is not None else ""
    return f" in {p['zone']}{xy}" if p.get("zone") else xy


def end(e: dict | None) -> dict | None:
    """A quest's giver or taker as the answers carry it."""
    if not e or not (e.get("name") or e.get("at")):
        return None
    out = {"type": e.get("type"), "id": e.get("id"), "name": e.get("name"), **place(e.get("at")),
           "seen_in_forever": bool(e.get("seen")), "data_source": MARK}
    if e.get("instance"):
        out["instance"] = e["instance"]
    return {k: v for k, v in out.items() if v is not None}


def uses(tree) -> bool:
    """Whether an answer shows anything from the file (a vendor source, a
    quest's giver): then it carries `data_credit`."""
    stack = [tree]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            if v.get("data_source") == MARK:
                return True
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
        elif isinstance(v, str) and TAG in v:
            return True
    return False
