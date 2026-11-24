"""Writes data/foreverdb/api.json: what the ForeverDB.net API knows that the
game client and the GPL data don't, for the gear advice. Run at release
time with the key (the release workflow's `foreverdb` job), bundled into
the image, never committed: ForeverDB asks developers to "fetch the data
yourself and bundle it with your releases", and nothing at runtime needs
the key.

What it fetches (tools/foreverdb_api/client.py says how cheaply):

- vendors: every vendor (where they stand, their side, the faction whose
  reputation discounts them, seen in Forever or Classic data) and what each
  sells, with the price; the weapons and armour they sell become
  `sold` (item -> vendors). 11 list pages + one request per vendor.
- merchants_favor: the Azeroth Commerce Authority and Durotar Supply and
  Logistics vendors and what they sell for Merchant's Favor, with the
  reputation rank each needs. One request.
- quests: who gives and who takes each quest, where they stand (map
  coordinates), and whether players saw it in Forever. The quest list
  carries the whole record, so no request per quest. 46 pages.
- items: which weapon and armour IDs the Forever client has (`in_forever`),
  and the stats of the ones it has that our game-items file fills from
  Classic Era or lacks (`stats`). 127 pages + one request per such item.
- dungeons: every instance's level range. One page.

About 1,300 requests for a new game build (45 minutes at the free tier's
30 a minute); a second run on the same build costs one (the cache).

    FOREVERDB_API_KEY=... uv run python tools/foreverdb_api/export.py --cache .cache/foreverdb-api
    uv run python tools/foreverdb_api/export.py --build-only      # prints the build (one request)
    uv run python tools/foreverdb_api/export.py --check [FILE]    # offline: the file's shape

The key is read from the environment only. The answers carry ForeverDB's
invisible per-key marks; the file keeps the text as served, and every MCP
answer and page drops invisible characters on the way out
(wowstats.text.invisible).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from client import ApiError, Client, QuotaStop  # noqa: E402

OUT = ROOT / "data" / "foreverdb" / "api.json"
GAME_ITEMS = ROOT / "data" / "game-items.json"
SITE = "https://foreverdb.net"
CREDIT = "Data from ForeverDB.net"
GEAR_CLASSES = (2, 4)  # weapons, armour
MAX_ITEM_DETAILS = 600  # a cap on the per-item requests of one build

# ForeverDB's stat IDs (the game's ItemModType) -> the names wowstats.gear
# scores with (tools/foreverdb_weights/export.py STATS). 45 is "+X damage and
# healing", which data/game-items.json carries as sp and heal.
STATS = {3: "agi", 4: "str", 5: "int", 6: "spi", 7: "sta", 12: "defense", 13: "dodgeRating", 14: "parryRating",
         15: "blockRating", 31: "hitRating", 32: "critRating", 36: "haste", 37: "expertise", 38: "ap", 39: "rap",
         41: "heal", 42: "sp", 43: "mp5", 46: "hp5", 47: "spellPen", 48: "blockValue",
         84: "spHoly", 85: "spFire", 86: "spNature", 87: "spFrost", 88: "spShadow", 89: "spArcane"}
SIDES = {"alliance": 1, "a": 1, "horde": 2, "h": 2}


def side(v) -> int | None:
    return SIDES.get(str(v or "").strip().lower())


def place(loc: dict | None) -> dict | None:
    if not loc:
        return None
    out = {"map": loc.get("uiMapId") or loc.get("mapId"), "zone": loc.get("zone"), "x": loc.get("x"), "y": loc.get("y")}
    return {k: v for k, v in out.items() if v is not None} or None


def vendor_row(v: dict) -> dict:
    rep = v.get("reputationFaction") or None
    out = {"name": v.get("name"), "title": v.get("title") or None, "side": side(v.get("side")),
           "at": place({"uiMapId": v.get("mapId"), "zone": v.get("zone"), "x": v.get("x"), "y": v.get("y")}),
           "seen": bool(v.get("seenInForever")),
           "rep": {"id": rep.get("id"), "name": rep.get("name")} if rep else None}
    return {k: x for k, x in out.items() if x is not None}


def sale(vid: int, s: dict) -> dict:
    out = {"vendor": vid, "price": s.get("price"), "stack": s.get("stack") if (s.get("stack") or 1) > 1 else None,
           "limited": s.get("limitedStockCount") if s.get("limited") else None,
           "seen": bool(s.get("seenInForever")), "costs": s.get("costs") or None}
    return {k: x for k, x in out.items() if x is not None}


def end(e: dict | None) -> dict | None:
    """A quest's giver or taker: who (npc, object, item), where, seen how."""
    if not e:
        return None
    out = {"type": e.get("type"), "id": e.get("id"), "name": e.get("name"), "at": place(e.get("location")),
           "instance": e.get("instance") or None, "seen": "Forever" in str(e.get("source") or ""),
           "players": e.get("players")}
    return {k: x for k, x in out.items() if x is not None}


def quest_row(q: dict) -> dict:
    out = {"name": q.get("name"), "level": q.get("level"), "req": q.get("requiredLevel"), "side": side(q.get("side")),
           "zone": (q.get("zone") or {}).get("n") if isinstance(q.get("zone"), dict) else q.get("zone"),
           "seen": bool(q.get("knownInForever")), "starts": end(q.get("starts")), "ends": end(q.get("ends"))}
    return {k: x for k, x in out.items() if x is not None}


def item_stats(d: dict) -> dict:
    """An item's detail in data/game-items.json's encoding (stats by our
    names; weapon damage, speed in ms and DPS)."""
    stats: dict = {}
    for s in d.get("stats") or []:
        sid, val = s.get("statId"), s.get("value")
        if val is None:
            continue
        if sid == 45:
            for k in ("sp", "heal"):
                stats[k] = stats.get(k, 0) + val
        elif sid in STATS:
            stats[STATS[sid]] = stats.get(STATS[sid], 0) + val
    out = {"name": d.get("name"), "inv": d.get("inventoryTypeId"), "ilvl": d.get("itemLevel"),
           "req": d.get("requiredLevel") or 0, "quality": d.get("qualityId"), "class": d.get("classId"),
           "subclass": d.get("subclassId"), "stats": stats}
    dmg, speed = d.get("damage") or None, d.get("speed")
    if dmg and speed and dmg.get("max"):
        out["speed"] = round(speed * 1000)
        out["dmg"] = [dmg.get("min"), dmg.get("max")]
        stats["dps"] = (dmg["min"] + dmg["max"]) / 2 / speed
    return out


def game_items() -> dict:
    try:
        return json.loads(GAME_ITEMS.read_text(encoding="utf-8")).get("items") or {}
    except (OSError, ValueError):
        return {}


PARTS = ("items", "vendors", "favor", "quests", "dungeons")


def build(c: Client, log=print, parts=PARTS) -> dict:
    """Every part by default; fewer for a sample (`--parts`)."""
    meta = c.meta()
    m = meta.get("data") or {}
    log(f"build {c.build}: {m.get('vendors')} vendors, {m.get('quests')} quests, {m.get('items')} items")

    # Items first: which gear IDs the Forever client has.
    ours = game_items()
    listed: dict[int, dict] = {}
    stats = {}
    if "items" in parts:
        for cls in GEAR_CLASSES:
            for r in c.pages("items", {"class": cls}):
                listed[int(r["id"])] = r
        want = sorted(i for i, r in listed.items()
                      if (r.get("qualityId") or 0) >= 2 and (str(i) not in ours or ours[str(i)].get("from") == "era"))
        for i in want[:MAX_ITEM_DETAILS]:
            stats[str(i)] = item_stats(c.get(f"items/{i}")["data"])
        log(f"items: {len(listed)} weapons and armour, {len(stats)} details ({len(want)} wanted)")
    # Without the item list (a sample), gear is what the game-items file holds.
    gear = set(listed) or {int(i) for i in ours}

    vendors: dict[str, dict] = {}
    mf = c.get("merchants-favor")["data"] if "favor" in parts else {}
    favor: dict[str, list] = {}
    for v in mf.get("vendors") or []:
        vid = v.get("npcId")
        if not vid:
            continue
        vendors.setdefault(str(vid), {k: x for k, x in {
            "name": v.get("name"), "side": side(v.get("faction")), "at": place(v.get("location")),
            "seen": True}.items() if x is not None})
        for s in v.get("sells") or []:
            if s.get("itemId") in gear:
                favor.setdefault(str(s["itemId"]), []).append(
                    {k: x for k, x in {"vendor": vid, "favor": s.get("favor"), "rank": s.get("reputation")}.items()
                     if x is not None})
    log(f"merchant's favor: {len(favor)} weapons and armour")

    quests = {str(q["id"]): quest_row(q) for q in c.pages("quests")} if "quests" in parts else {}
    log(f"quests: {len(quests)}")

    dungeons = {str(d["id"]): {k: x for k, x in {
        "name": d.get("name"), "kind": d.get("kind"), "levels": d.get("levelRange"), "level": d.get("level"),
        "new": bool(d.get("newInForever"))}.items() if x is not None}
        for d in (c.pages("dungeons") if "dungeons" in parts else [])}

    # Vendors last: one request each, the bulk of a run; a sample stops here.
    sold: dict[str, list] = {}
    if "vendors" in parts:
        for v in c.pages("vendors"):
            vid = int(v["id"])
            try:
                d = c.get(f"vendors/{vid}")["data"]
            except QuotaStop:
                if "sample" not in parts:
                    raise
                break
            vendors[str(vid)] = {**vendors.get(str(vid), {}), **vendor_row(d)}
            for s in d.get("sells") or []:
                iid = s.get("itemId")
                if iid in gear:
                    sold.setdefault(str(iid), []).append(sale(vid, s))
        log(f"vendors: {len(vendors)}, selling {len(sold)} weapons and armour")

    return {
        "schema": 1,
        "source": {"name": "ForeverDB.net", "url": SITE, "credit": CREDIT, "build": c.build,
                   "fetched": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "attribution": (meta.get("meta") or {}).get("attribution"),
                   "requests": {"sent": c.calls, "cached": c.cached},
                   "endpoints": ["/meta", "/items", "/items/{id}", "/vendors", "/vendors/{id}", "/merchants-favor",
                                 "/quests", "/dungeons"],
                   "note": "From ForeverDB.net's API: the WoW Forever client's data, what players' ForeverDB addon "
                           "saw in the game (seen), and Classic data where Forever's isn't known yet."},
        "parts": [p for p in PARTS if p in parts],
        "sample": "sample" in parts,
        "in_forever": sorted(listed),
        "stats": stats,
        "vendors": vendors,
        "sold": sold,
        "favor": favor,
        "quests": quests,
        "dungeons": dungeons,
    }


def check(path: Path) -> list[str]:
    """Offline: the file's shape, its credit and source."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{path}: {e}"]
    bad = []
    if d.get("schema") != 1:
        bad.append("schema is not 1")
    s = d.get("source") or {}
    if s.get("url") != SITE or s.get("credit") != CREDIT or not s.get("build") or not s.get("fetched"):
        bad.append("source: url, credit, build and fetched are required")
    for k in ("in_forever", "vendors", "sold", "quests"):
        if not d.get(k):
            bad.append(f"{k} is empty")
    for iid, rows in (d.get("sold") or {}).items():
        for r in rows:
            if str(r.get("vendor")) not in (d.get("vendors") or {}):
                bad.append(f"item {iid}: vendor {r.get('vendor')} not listed")
                break
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--cache", type=Path, help="one file per request, kept per build (resumes a stopped run)")
    ap.add_argument("--max-calls", type=int, help="stop after this many requests")
    ap.add_argument("--parts", default=",".join(PARTS),
                    help="comma-separated parts to fetch; add `sample` to keep what a --max-calls stop got")
    ap.add_argument("--build-only", action="store_true", help="print the game build ForeverDB serves, and stop")
    ap.add_argument("--check", nargs="?", const=OUT, type=Path, help="offline: check a written file")
    a = ap.parse_args(argv)
    if a.check:
        bad = check(a.check)
        for b in bad:
            print(b, file=sys.stderr)
        return 1 if bad else 0
    try:
        c = Client(os.environ.get("FOREVERDB_API_KEY", "").strip(), a.cache, max_calls=a.max_calls)
        if a.build_only:
            c.meta()
            print(c.build)
            return 0
        out = build(c, log=lambda s: print(s, file=sys.stderr), parts=tuple(a.parts.split(",")))
    except QuotaStop as e:
        print(f"stopped: {e}. Fetched so far is cached; rerun to resume.", file=sys.stderr)
        return 3
    except ApiError as e:
        print(f"failed: {e}", file=sys.stderr)
        return 2
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
                     encoding="utf-8")
    print(f"wrote {a.out}: {len(out['sold'])} items sold by {len(out['vendors'])} vendors, {len(out['quests'])} "
          f"quests; {c.calls} requests sent, {c.cached} from the cache", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
