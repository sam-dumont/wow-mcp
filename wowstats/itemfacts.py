"""Item facts from each player's own game client, for the weapons and armour
no data we ship has yet.

Forever's newest items (a dungeon added in a patch: The Drowned City in
November 2026) reach the client as hotfixes, so neither forever-db, Polestar
nor wago.tools has them, and a dungeon's drops were known by name only (the
research notes, wowstats.plan.kbloot). The client keeps those rows in its
hotfix cache; the player's shipper reads it and sends, for the items this
server asks for, their facts: id, name, slot, class and subclass, quality,
item level, required level, stat ids with their allocations, speed,
binding, icon file id, vendor price, which classes may use it
(shipper/wow_stats.py ship_item_facts). With them it sends the client's
RandPropPoints table once per build: the stat budget per item level that
turns an allocation into the number the tooltip shows (stat = allocation x
budget / 10000, rounded; 91% of the primary stats of the 5,169 items in
both the cache and Polestar's data match exactly, the rest are items
Forever changed since).

The reference always wins: an item Polestar's gear data has is never asked
for and never read from here; these facts only fill its gaps. They are kept
per person like the 3D view's files (ITEM_FACTS_DIR; with several people,
users/<account>/items/, gone with the person), read only for that person's
pages, and everything a shipper sends is checked like any outside input:
types, ranges, sizes. Ratings (hit, crit, dodge, parry) stay out of the
numbers: the client turns them into percentages with tables we don't have;
so do armour and weapon damage, which come from other tables.

Routes, with the upload password like /ingest (wowstats.ui):

  POST /install/items/need?build=<client version>   {"items": [[id, hash], ...]}
       -> {"want": [ids], "tables": {name: file id}, "max_items": n}
  POST /install/items/facts?build=<client version>  {"items": [facts, ...]}
  POST /install/items/table/RandPropPoints?build=<client version>  the WDC5 file
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import math
import os
import re
import struct
import tempfile
import threading
import time
from pathlib import Path

BUILD = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,7}$")
MAX_BODY = 2 * 1024 * 1024       # one POST of facts or of the need list
MAX_NEED = 50_000                # ids in one need list (the real cache: ~6,500)
MAX_POST = 500                   # items in one facts POST
MAX_ITEMS = 20_000               # items kept per person
MAX_TABLE = 512 * 1024           # RandPropPoints is ~36 kB
TABLES = {"RandPropPoints": 1310245}  # the client table's file id (WoWDBDefs' manifest)
TABLE_MAGIC = (b"WDC3", b"WDC4", b"WDC5")
NAME_OK = re.compile(r"^[^\x00-\x1f\x7f]{1,120}$")

# The stat ids whose allocation becomes a number Polestar's stat keys name
# (gear.STAT_KEYS), checked against the 5,169 items both sources hold.
STAT_KEYS: dict[int, tuple[str, ...]] = {
    3: ("agi",), 4: ("str",), 5: ("int",), 6: ("spi",), 7: ("sta",), 12: ("defense",), 38: ("ap",), 39: ("rap",),
    41: ("heal",), 42: ("sp",), 43: ("mp5",), 45: ("sp", "heal"), 46: ("hp5",), 48: ("blockValue",),
    84: ("spHoly",), 85: ("spFire",), 86: ("spNature",), 87: ("spFrost",), 88: ("spShadow",), 89: ("spArcane",),
}
# The budget column of an inventory type (the game's own grouping): 0 head,
# chest, legs, two-hand; 1 shoulder, waist, feet, hands, trinket; 2 neck,
# wrist, finger, shield, back, held in off-hand; 3 one-hand weapons; 4 ranged.
SLOT_GROUP = {1: 0, 4: 0, 5: 0, 7: 0, 17: 0, 20: 0, 3: 1, 6: 1, 8: 1, 10: 1, 12: 1, 2: 2, 9: 2, 11: 2, 14: 2, 16: 2,
              23: 2, 13: 3, 21: 3, 22: 3, 15: 4, 25: 4, 26: 4, 28: 4}

# field -> (lowest, highest): every number a shipper may send.
RANGES = {"id": (1, 9_999_999), "inv": (0, 30), "class": (2, 4), "sub": (0, 31), "q": (0, 8), "ilvl": (1, 1000),
          "req": (0, 100), "speed": (0, 10_000), "bond": (0, 10), "icon": (0, 100_000_000), "sell": (0, 10**9),
          "unique": (0, 1), "allow": (-1, 65_535), "h": (0, 2**32 - 1)}


class Refused(Exception):
    """Input this server doesn't take: (HTTP status, why)."""

    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status = status


def _int(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise Refused(400, f"{what}: a whole number from {lo} to {hi}")
    return v


def valid(raw) -> dict:
    """One item's facts as a shipper sent them, checked; Refused when any
    field is missing, of the wrong type or out of range."""
    if not isinstance(raw, dict) or set(raw) - set(RANGES) - {"name", "stats"}:
        raise Refused(400, "an item is an object with only the documented fields")
    out = {k: _int(raw.get(k), lo, hi, k) for k, (lo, hi) in RANGES.items()}
    if out["class"] == 3:
        raise Refused(400, "class: weapons (2) and armour (4) only")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_OK.match(name.strip()):
        raise Refused(400, "name: 1 to 120 printable characters")
    out["name"] = name.strip()
    stats = raw.get("stats")
    if not isinstance(stats, list) or len(stats) > 10:
        raise Refused(400, "stats: at most 10 [stat id, allocation] pairs")
    out["stats"] = []
    for pair in stats:
        if not isinstance(pair, list) or len(pair) != 2:
            raise Refused(400, "stats: at most 10 [stat id, allocation] pairs")
        out["stats"].append([_int(pair[0], 0, 255, "stat id"), _int(pair[1], -1_000_000, 1_000_000, "allocation")])
    return out


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".part-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def budget_rows(data: bytes, build: str) -> dict[str, list[int]]:
    """The RandPropPoints table -> {item level: [Good x5, Superior x5, Epic x5]};
    Refused when it isn't one or its numbers are out of range."""
    from wowstats import db2

    if data[:4] not in TABLE_MAGIC:
        raise Refused(415, "not a client table (WDC3 to WDC5)")
    try:
        rows = db2.Table(data, "RandPropPoints", build).rows
    except (db2.DB2Error, ValueError, KeyError, IndexError, struct.error) as e:
        raise Refused(400, f"RandPropPoints: {e}") from None
    out: dict[str, list[int]] = {}
    for rid, r in rows.items():
        try:
            vals = [int(v) for col in ("Good", "Superior", "Epic") for v in r[col]]
        except (KeyError, TypeError, ValueError):
            raise Refused(400, "RandPropPoints: not the columns this server reads") from None
        if not 1 <= int(rid) <= 1000 or len(vals) != 15 or any(not 0 <= v <= 100_000 for v in vals):
            raise Refused(400, "RandPropPoints: a value out of range")
        out[str(int(rid))] = vals
    if not out:
        raise Refused(400, "RandPropPoints: no rows")
    return out


class Facts:
    """One person's facts, read-only: what the pages and the planner ask."""

    def __init__(self, items: dict[str, dict] | None = None, budget: dict[str, list[int]] | None = None):
        self.items = items or {}
        self.budget = budget or {}
        self._names: dict[str, int] | None = None

    def __bool__(self) -> bool:
        return bool(self.items)

    def get(self, item_id) -> dict | None:
        return self.items.get(str(item_id))

    def by_name(self, name: str) -> int | None:
        if self._names is None:
            names: dict[str, int] = {}
            for k, f in sorted(self.items.items(), key=lambda kv: int(kv[0])):
                names.setdefault(f["name"].lower(), int(k))
            self._names = names
        return self._names.get((name or "").lower())

    def stats(self, f: dict) -> dict[str, int]:
        """The numbers of an item's stats (STAT_KEYS), {} without the budget."""
        row = self.budget.get(str(f.get("ilvl")))
        group = SLOT_GROUP.get(f.get("inv"))
        if not row or group is None:
            return {}
        col = 2 if f.get("q", 0) >= 4 else 1 if f.get("q") == 3 else 0
        points = row[col * 5 + group]
        out: dict[str, int] = {}
        for sid, alloc in f.get("stats") or []:
            v = math.floor(alloc * points / 10000 + 0.5)
            for key in STAT_KEYS.get(sid, ()):
                out[key] = out.get(key, 0) + v
        return {k: v for k, v in out.items() if v}

    def info(self, item_id) -> dict | None:
        """The item as Polestar's gear data shapes one (gear.Reference.item):
        what wowstats.gearfit reads. None when it isn't here."""
        f = self.get(item_id)
        if f is None:
            return None
        out = {"inv": f["inv"], "ilvl": f["ilvl"], "req": f["req"], "quality": f["q"], "class": f["class"],
               "subclass": f["sub"], "stats": self.stats(f)}
        if f["class"] == 2 and f.get("speed"):
            out["speed"] = f["speed"]
        if f.get("allow", -1) > 0:
            out["allow"] = f["allow"]
        return out

    def card(self, item_id, icons: dict[int, str] | None = None,
             subclasses: dict[tuple[int, int], str] | None = None) -> dict | None:
        """The tooltip card (wowstats.itemcard.build_cards), from these facts."""
        from wowstats import itemcard

        f = self.get(item_id)
        if f is None:
            return None
        sparse = {"ID": f["id"], "Display_lang": f["name"], "OverallQualityID": f["q"], "ItemLevel": f["ilvl"],
                  "RequiredLevel": f["req"], "InventoryType": f["inv"], "Bonding": f["bond"],
                  "MaxCount": 1 if f.get("unique") else 0, "SellPrice": f.get("sell", 0),
                  "ItemDelay": f.get("speed", 0)}
        item = {"ID": f["id"], "ClassID": f["class"], "SubclassID": f["sub"], "InventoryType": f["inv"],
                "IconFileDataID": f.get("icon", 0)}
        cards = itemcard.build_cards([sparse], [item], icons or {}, {}, subclasses or {},
                                     {str(f["id"]): {"stats": self.stats(f)}})
        return cards.get(f["id"])


class Store:
    """One person's facts on disk: <root>/facts.json, {"items": {id: facts},
    "budget": {...}, "budget_build": build}."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.file = self.root / "facts.json"
        self.lock = threading.Lock()
        self._cache: tuple[int, Facts, dict] | None = None

    def _raw(self) -> dict:
        try:
            st = self.file.stat()
        except OSError:
            self._cache = None  # removed (the person left): nothing, not the last copy read
            return {}
        if self._cache and self._cache[0] == st.st_mtime_ns:
            return self._cache[2]
        try:
            d = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(d, dict):
            return {}
        self._cache = (st.st_mtime_ns, Facts(d.get("items") or {}, d.get("budget") or {}), d)
        return d

    def facts(self) -> Facts:
        self._raw()
        return self._cache[1] if self._cache else Facts()

    def blob(self) -> bytes | None:
        try:
            return self.file.read_bytes()
        except OSError:
            return None

    def _save(self, d: dict) -> None:
        _write(self.file, json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode())

    def need(self, build: str, pairs, has_reference) -> dict:
        """What to send for a client on `build` holding `pairs` ([id, hash]):
        the items neither the reference nor this store has with that hash."""
        if not isinstance(pairs, list) or len(pairs) > MAX_NEED:
            raise Refused(400, f"items: a list of at most {MAX_NEED} [id, hash] pairs")
        stored = self._raw().get("items") or {}
        want = []
        for p in pairs:
            if not isinstance(p, list) or len(p) != 2:
                raise Refused(400, "items: [id, hash] pairs")
            iid = _int(p[0], *RANGES["id"], "id")
            h = _int(p[1], *RANGES["h"], "hash")
            if has_reference(iid):
                continue
            if (stored.get(str(iid)) or {}).get("h") != h:
                want.append(iid)
        d = self._raw()
        tables = {} if d.get("budget_build") == build and d.get("budget") else dict(TABLES)
        return {"build": build, "want": sorted(set(want)), "tables": tables, "max_items": MAX_POST}

    def put_items(self, items, has_reference) -> tuple[int, int]:
        """(stored, refused): an item that fails valid() is left out, the rest kept."""
        if not isinstance(items, list) or len(items) > MAX_POST:
            raise Refused(400, f"items: a list of at most {MAX_POST} items")
        got = []
        for x in items:
            with contextlib.suppress(Refused):
                got.append(valid(x))
        with self.lock:
            d = dict(self._raw())
            stored = dict(d.get("items") or {})
            n = 0
            for f in got:
                if has_reference(f["id"]):
                    continue  # the reference wins: never kept
                if str(f["id"]) not in stored and len(stored) >= MAX_ITEMS:
                    raise Refused(507, f"this server keeps at most {MAX_ITEMS} items per person")
                stored[str(f["id"])] = f
                n += 1
            d["items"] = stored
            if n:
                self._save(d)
        return n, len(items) - len(got)

    def put_table(self, name: str, build: str, data: bytes) -> None:
        if name not in TABLES:
            raise Refused(404, "not a table the item facts read")
        rows = budget_rows(data, build)
        with self.lock:
            d = dict(self._raw())
            d["budget"], d["budget_build"] = rows, build
            self._save(d)


class RemoteFacts:
    """A person's facts through the full view's share port (the publisher's
    pod can't mount that volume): GET <url>facts, read again after a minute."""

    def __init__(self, url: str, env: dict):
        self.url = url.rstrip("/") + "/"
        self.env = env
        self._got: tuple[float, Facts] | None = None

    def facts(self) -> Facts:
        if self._got and time.time() - self._got[0] < 60:
            return self._got[1]
        import urllib.error

        from wowstats import guard

        f = Facts()
        try:
            with guard.share_open(self.url + "facts", self.env, timeout=60) as r:
                body = r.read(64 * 1024 * 1024 + 1)
            d = json.loads(body) if len(body) <= 64 * 1024 * 1024 else {}
            if isinstance(d, dict):
                f = Facts(d.get("items") or {}, d.get("budget") or {})
        except (urllib.error.URLError, OSError, ValueError):
            pass
        self._got = (time.time(), f)
        return f


def share_route(h, root: Path) -> None:
    """The share port's side of RemoteFacts (wowstats.analysis.share_server)."""
    blob = Store(root).blob()
    return h.reply(200, blob, "application/json") if blob is not None else h.reply(404, b"")


def default_dir(env: dict) -> Path:
    d = (env.get("ITEM_FACTS_DIR") or "").strip()
    if d:
        return Path(d)
    cache = env.get("MODEL3D_CACHE_DIR") or str(Path(tempfile.gettempdir()) / "wow-stats-model3d")
    return Path(cache) / "item-facts"


def has_reference(item_id: int) -> bool:
    """Polestar's gear data has the item (and its stats): it wins."""
    from wowstats import gear

    ref = gear.reference()
    return bool(ref.available and (ref.data.get("items") or {}).get(str(item_id)))


class ItemFacts:
    """The routes above, and the reader, for one person's App."""

    def __init__(self, env: dict):
        url = (env.get("ITEM_FACTS_URL") or "").strip()
        self.store = None if url else Store(default_dir(env))
        self.remote = RemoteFacts(url, env) if url else None

    def facts(self) -> Facts:
        return self.remote.facts() if self.remote is not None else self.store.facts()

    def handle(self, h, rest: str, qs: dict, allows=None, spend=None) -> None:
        """/install/items/<rest>, for a caller the router already
        authenticated with an upload password. `allows(n)`: whether n more
        bytes fit the person's day; `spend(n)` counts what was stored."""
        if self.store is None or h.command != "POST":
            return h.not_found()
        build = (qs.get("build") or [""])[0]
        if not BUILD.match(build):
            return h.json(400, {"error": "build is the client's version, like 1.60.1.71037"})
        m = re.fullmatch(r"need|facts|table/([A-Za-z]{1,40})", rest)
        if not m:
            return h.not_found()
        limit = MAX_TABLE if m.group(1) else MAX_BODY
        body = h.read_body(limit)
        if body is None:
            return h.json(413, {"error": f"send a Content-Length of at most {limit} bytes"})
        if rest != "need" and allows is not None and not allows(len(body)):
            return h.json(429, {"error": "today's upload budget is used up; the rest goes tomorrow"})
        try:
            if m.group(1):
                import hashlib

                if (h.headers.get("X-Content-SHA256") or "").strip().lower() != hashlib.sha256(body).hexdigest():
                    raise Refused(400, "X-Content-SHA256 doesn't match the body")
                self.store.put_table(m.group(1), build, body)
                if spend is not None:
                    spend(len(body))
                return h.json(200, {"stored": m.group(1)})
            try:
                d = json.loads(body)
            except ValueError:
                raise Refused(400, "send JSON") from None
            if not isinstance(d, dict):
                raise Refused(400, "send a JSON object")
            if rest == "need":
                return h.json(200, self.store.need(build, d.get("items"), has_reference))
            n, bad = self.store.put_items(d.get("items"), has_reference)
            if spend is not None:
                spend(len(body))
            return h.json(200, {"stored": n, "refused": bad})
        except Refused as e:
            return h.json(e.status, {"error": str(e)})


# ---------------------------------------------------------------- the person whose answer is being worked out

# Set around one person's answer (ui.App.cached, the MCP's tool calls), so
# the code deep in the planner (wowstats.plan.kbloot) and the item cards read
# that person's facts and nobody else's.
_CURRENT: contextvars.ContextVar[Facts | None] = contextvars.ContextVar("item_facts", default=None)


def current() -> Facts | None:
    return _CURRENT.get()


@contextlib.contextmanager
def using(facts: Facts | None):
    token = _CURRENT.set(facts)
    try:
        yield facts
    finally:
        _CURRENT.reset(token)


_READERS: dict[str, ItemFacts] = {}
_READERS_LOCK = threading.Lock()


def for_env(env: dict) -> ItemFacts:
    """The person's ItemFacts for an environment (one per folder or URL)."""
    key = (env.get("ITEM_FACTS_URL") or "").strip() or str(default_dir(env))
    with _READERS_LOCK:
        got = _READERS.get(key)
        if got is None:
            got = _READERS[key] = ItemFacts(env)
        return got
