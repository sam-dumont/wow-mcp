"""The auction house scans everyone on one install shares (owner,
2026-11-24: "scan from AH can be used across users, per faction, basically
a scan of the AH per ruleset").

WoW Forever has no realms, only rulesets. An Auctionator scan is a
snapshot of one auction house: one ruleset, one faction's house (or the
neutral one). Whoever scanned it, the prices are the same for every
character of that ruleset and faction, so the newest scan of anyone on the
install serves them all.

Where: one more tenant, ACCOUNT, in the friends' event store
(Tenancy.friends_vl: FRIENDS_VL_URL, or the SQLite folder, where it is the
file events-<ACCOUNT>.sqlite). Not the owner's store: in the lab that one is
kept forever and copied to an archive that never deletes. Without several
people (no WOW_STATE_DIR) there is nothing to share: each install reads its
own scans, as before. wowstats.tenancy.env_for hands the location to every
person's App as AH_SHARED_STORE.

What it holds: the price fields of each wow-prices event and nothing else
(SHARED_FIELDS: item, prices, quantity, scan day and time, ruleset,
faction). Never `char`, `source_id`, the shipper's `_msg` or anything that
says who scanned. Since no row knows its scanner, a friend's deletion
(wowstats.tenancy.delete_tenant) can't take their past scans out of it:
the prices they scanned stay, as game data (docs/self-host/privacy.md).

The ruleset: Auctionator keys its prices by the client's realm name
("<name>" or "<name> <faction>"), which on Forever is the ruleset's name; the
shipper sends it as `realm` (shipper/wow_stats.py extract_auctionator), the
same text Altoholic's character records carry as `realm`. Both are read here
as the ruleset.

The faction: the event's own when Auctionator's key names it (the faction
auction houses). Otherwise (a key with the ruleset alone) the scanning
character's side, read server-side at ingest: its newest `faction`
(Alliance or Horde) in the upload or in the person's own store, else its
race (RACE_SIDE). A scan whose side stays unknown (a Skyborne without a
faction record, say) is kept in the person's own store only.

Reads (scans): the caller's own characters' (ruleset, side) pairs, from
their own store (homes), each with the neutral house of that ruleset; the
shared rows of exactly those houses, merged with nothing else. A Horde
character never reads an Alliance house, two rulesets never mix.

Retention: PRICE_DAYS (90) days, then pruned (prune_daily, from the
builder's pass, at most once a day: a delete task on the friends'
VictoriaLogs, a DELETE on the SQLite file). A friend's deletion still takes
none of it.

Existing scans, sent before this: `wowstats users ah-share` copies every
person's stored scans into the shared space (backfill). It can run again:
the copies are the same rows (the SQLite store keeps one, VictoriaLogs'
readers take min and max per day, so a second copy changes nothing).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from wowstats import ahsource
from wowstats.stores import lq, merge_rows

# A tenant no person ever gets: the registry counts friends up from 1
# (wowstats.tenancy.Registry.ensure), VictoriaLogs' AccountID is a uint32.
ACCOUNT = 4_000_000_000
ENV = "AH_SHARED_STORE"
# Days of scans the readers look at (PRICE_DAYS, views.settings_from_env) and
# the days the shared space keeps: older rows are pruned (owner, 2026-11-24).
PRICE_DAYS = 90
PRUNE_EVERY_S = 86400
FEED = "wow-prices"
SHARED_FIELDS = ("item_id", "item_key", "item_name", "price_copper", "price_high_copper", "available", "scan_day")
SIDES = ("Alliance", "Horde")
NEUTRAL = "Neutral"
# Classic's eight races, as the addons save them (with and without the space).
RACE_SIDE = {"human": "Alliance", "dwarf": "Alliance", "night elf": "Alliance", "nightelf": "Alliance",
             "gnome": "Alliance", "orc": "Horde", "undead": "Horde", "scourge": "Horde", "tauren": "Horde",
             "troll": "Horde"}
BY = ("item_key", "scan_day", "ruleset", "faction")


def location(env: dict) -> str:
    return (env.get(ENV) or "").strip().rstrip("/")


def side(faction=None, race=None) -> str | None:
    """Alliance or Horde, from a faction field, else from a race."""
    f = str(faction or "").strip().title()
    if f in SIDES:
        return f
    return RACE_SIDE.get(str(race or "").strip().lower())


def store(loc: str):
    """The shared space as a read-only Store, in its own tenant."""
    from wowstats.stores import Store

    s = Store(loc, "", "", f'feed:="{FEED}"', account_id=ACCOUNT)
    s.use_cache = False  # the event cache is one character's; nothing here is
    return s


def shared_event(ev: dict, faction: str | None) -> dict | None:
    """The shared copy of one wow-prices event: its price fields, the
    ruleset (the event's `realm`) and the house's faction. None when either
    is unknown."""
    ruleset = str(ev.get("realm") or "").strip()
    house = str(ev.get("faction") or "").strip() or faction
    if not ruleset or house not in (*SIDES, NEUTRAL) or not ev.get("_time"):
        return None
    out = {"_time": ev["_time"], "feed": FEED, "component": "wow", "ruleset": ruleset, "faction": house,
           **{k: ev[k] for k in SHARED_FIELDS if ev.get(k) not in (None, "")}}
    out["_msg"] = f"{out.get('item_name') or out.get('item_key') or '?'}: lowest {out.get('price_copper')} copper"
    return out


def _side_in_store(own, char: str) -> str | None:
    """The character's side from the person's own store: the newest event
    with a faction, else with a race."""
    rows = own.vl_direct(f'{own.stream} char:={lq(char)} -feed:="{FEED}" (faction:in("Alliance", "Horde") or race:*) '
                         f"| stats by (faction, race) max(_time) as t", ("faction", "race"), {"t": "maxtime"},
                         limit=1000)
    for r in sorted(rows, key=lambda r: r.get("t") or "", reverse=True):
        if s := side(r.get("faction"), r.get("race")):
            return s
    return None


def share(loc: str, lines: list[bytes], own) -> int:
    """At ingest, after the person's own store took `lines`: the price
    events among them, stripped (shared_event), to the shared space. How
    many went."""
    from wowstats import eventstore

    evs = [json.loads(x) for x in lines]
    prices = [e for e in evs if e.get("feed") == FEED]
    if not loc or not prices:
        return 0
    known: dict[str, str | None] = {}
    for e in evs:  # the upload's own records first: a session or a sheet names the side
        if e.get("feed") != FEED and isinstance(e.get("char"), str) and (s := side(e.get("faction"), e.get("race"))):
            known.setdefault(e["char"], s)
    out = []
    for e in prices:
        f = None
        if not str(e.get("faction") or "").strip():
            ch = e.get("char")
            if isinstance(ch, str) and ch not in known:
                known[ch] = _side_in_store(own, ch)
            f = known.get(ch) if isinstance(ch, str) else None
        if (s := shared_event(e, f)) is not None:
            out.append(json.dumps(s, ensure_ascii=False, separators=(",", ":")).encode())
    if out:
        eventstore.get(loc).append(ACCOUNT, out)
    return len(out)


def homes(own) -> set[tuple[str, str]]:
    """The (ruleset, side) pairs of the person's own characters: each
    character's ruleset from its records' `realm` (Altoholic's character
    records, its own scans), its side from its faction or race."""
    st = own.stream
    realms = own.vl_direct(f'{st} feed:in("wow-character", "{FEED}") realm:* char:* | stats by (char, realm, faction) '
                           f"max(_time) as t", ("char", "realm", "faction"), {"t": "maxtime"}, limit=10000)
    sides = own.vl_direct(f'{st} char:* (faction:in("Alliance", "Horde") or race:*) | stats by (char, faction, race) '
                          f"max(_time) as t", ("char", "faction", "race"), {"t": "maxtime"}, limit=10000)
    side_of: dict[str, tuple[str, str]] = {}
    for r in sides:
        s, t = side(r.get("faction"), r.get("race")), r.get("t") or ""
        if s and t > side_of.get(r["char"], ("", ""))[1]:
            side_of[r["char"]] = (s, t)
    out = set()
    for r in realms:
        s = side(r.get("faction")) or side_of.get(r.get("char"), (None,))[0]
        if r.get("realm") and s:
            # The ruleset as Auctionator spells it: a character record's
            # realm is the client's long name (wowstats.ahsource.ruleset_key).
            out.add((ahsource.ruleset_key(r["realm"]), s))
    return out


def one_house(rows: list[dict], home: tuple[str, str] | None, *, exact: bool = False) -> tuple[dict, list[dict]]:
    """Keep every day of one house together for trends and loot valuations."""
    houses: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (ahsource.ruleset_key(row.get("ruleset")), str(row.get("faction") or "").title())
        houses.setdefault(key, []).append(row)
    if home:
        realm, faction = home
        realm = ahsource.ruleset_key(realm)
        choices = [faction] if exact else [faction, NEUTRAL]
        key = next(((realm, f) for f in choices if (realm, f) in houses), None)
    else:
        # Old single-house exports sometimes lack a faction. A labelled house
        # needs a known character or an explicit selection.
        key = next(iter(houses), None)
        if len(houses) != 1 or key[1]:
            key = None
    if key is None:
        return {}, []
    return {"ruleset": key[0] or None, "faction": key[1] or None}, houses[key]


def character_house(own, char: str) -> tuple[str, str] | None:
    """Read the character's own realm and side. Scanning a house can't change either."""
    names = sorted({char, char.replace("-", " "), char.replace(" ", "-")})
    filt = "char:in(" + ", ".join(lq(n) for n in names) + ")"
    rows = own.vl_direct(f'{own.stream} {filt} feed:="wow-character" realm:* '
                         "| stats by (char, realm, faction, race) max(_time) as t",
                         ("char", "realm", "faction", "race"), {"t": "maxtime"})
    current = max((r for r in rows if r.get("realm")), key=lambda r: r.get("t") or "", default=None)
    if current:
        faction = side(current.get("faction"), current.get("race")) or _side_in_store(own, current["char"])
        if faction:
            return ahsource.ruleset_key(current["realm"]), faction
    return None


def house_filter(pairs: set[tuple[str, str]]) -> str:
    """The LogsQL filter for the houses of `pairs`: each ruleset's side
    house and its neutral one."""
    return " or ".join(f'(ruleset:={lq(r)} faction:in({lq(s)}, "{NEUTRAL}"))' for r, s in sorted(pairs))


def scans(own, loc: str, agg: str, ops: dict, start: datetime | None = None, end: datetime | None = None,
          limit: int = 200000, *, identify_houses: bool = False) -> list[dict]:
    """The scans' stats rows, one per item, house (ruleset, faction) and
    scan day: `agg` is the stats functions after `stats by (item_key,
    scan_day, ruleset, faction)`, `ops` how split answers merge. From the
    shared space for the person's own houses when there is one (`loc`),
    else from their own store (one person: their scans are all there is)."""
    end = end or datetime.now(UTC)
    if loc:
        pairs = homes(own)
        if not pairs:
            return []
        return store(loc).vl_direct(f'feed:="{FEED}" ({house_filter(pairs)}) | stats by ({", ".join(BY)}) {agg}',
                                    BY, ops, limit=limit, start=start, end=end)
    keys = ("item_key", "scan_day", "realm", "faction") + (("char",) if identify_houses else ())
    rows = own.vl_direct(f'{own.stream} feed:="{FEED}" | stats by ({", ".join(keys)}) {agg}',
                         keys, ops, limit=limit, start=start, end=end)
    sides = {}
    for r in rows:
        r["ruleset"] = ahsource.ruleset_key(r.pop("realm", None))
        scanner = r.pop("char", None)
        if identify_houses and scanner and not r.get("faction"):
            if scanner not in sides:
                sides[scanner] = _side_in_store(own, scanner)
            r["faction"] = sides[scanner]
    return merge_rows(rows, BY, ops) if identify_houses else rows


# The one read of the scans the price list, the gear advice's Auction House
# source and the analysis' ah_prices share: one stats row per item, house and
# scan day. Within a day the shipper only sends wider numbers (rev+1), so
# min/max over every rev is the latest rev's value. Grouped by item and house
# only: no row says who scanned.
PRICE_AGG = ("min(price_copper) as lo, max(price_high_copper) as hi, max(available) as av, "
             "max(item_name) as name, max(item_id) as item_id, max(_time) as t")
PRICE_OPS = {"lo": "min", "hi": "max", "av": "max", "name": "max", "item_id": "max", "t": "maxtime"}


def price_rows(own, loc: str, start: datetime | None = None) -> list[dict]:
    """The scans as the price list reads them (scans() with PRICE_AGG):
    item_key, scan_day, ruleset, faction, lo, hi, av, name, item_id, t."""
    return scans(own, loc, PRICE_AGG, PRICE_OPS, start=start)


def backfill(loc: str, people: list[tuple[str, object]], out=sys.stdout) -> dict:
    """Every person's stored scans into the shared space: [(who, their
    Store)]. One row per item, house and scan day (its lowest and highest
    price, the most listed, its newest time), stripped like at ingest."""
    from wowstats import eventstore

    done = {}
    for who, own in people:
        rows = own.vl_direct(f'{own.stream} feed:="{FEED}" | stats by (char, realm, faction, item_key, scan_day) '
                             "min(price_copper) as price_copper, max(price_high_copper) as price_high_copper, "
                             "max(available) as available, max(item_name) as item_name, max(item_id) as item_id, "
                             "max(_time) as t",
                             ("char", "realm", "faction", "item_key", "scan_day"),
                             {"price_copper": "min", "price_high_copper": "max", "available": "max",
                              "item_name": "max", "item_id": "max", "t": "maxtime"}, limit=1_000_000)
        known: dict[str, str | None] = {}
        lines = []
        for r in rows:
            r = {k: v for k, v in r.items() if v not in (None, "", "NaN")}
            r["_time"] = r.pop("t", None)
            f = None
            if not r.get("faction") and isinstance(r.get("char"), str):
                if r["char"] not in known:
                    known[r["char"]] = _side_in_store(own, r["char"])
                f = known[r["char"]]
            if (s := shared_event(r, f)) is not None:
                lines.append(json.dumps(s, ensure_ascii=False, separators=(",", ":")).encode())
        for i in range(0, len(lines), 5000):
            eventstore.get(loc).append(ACCOUNT, lines[i:i + 5000])
        done[who] = {"scans": len(rows), "shared": len(lines)}
        print(json.dumps({"who": who, **done[who]}), file=out, flush=True)
    return done


_last_prune = 0.0
_warned = False


def prune(loc: str, key_file: str = "", days: int = PRICE_DAYS, now: datetime | None = None) -> dict:
    """The shared rows older than `days` days, deleted. VictoriaLogs needs
    its delete API (-delete.enable, and the key in `key_file`)."""
    from wowstats import eventstore

    key = Path(key_file).read_text().strip() if key_file and not eventstore.is_sqlite(loc) else ""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    return eventstore.get(loc).delete(ACCOUNT, auth_key=key, before=cutoff)


def prune_daily(loc: str, key_file: str = "", every: float = PRUNE_EVERY_S, log=print) -> bool:
    """prune, at most once per `every` seconds per process. A store that
    refuses (no delete API) is logged once and skipped: the readers ignore
    rows past PRICE_DAYS anyway. True when it ran."""
    global _last_prune, _warned
    from wowstats.stores import SourceError

    if not loc or (_last_prune and time.monotonic() - _last_prune < every):
        return False
    _last_prune = time.monotonic()
    try:
        prune(loc, key_file)
    except (SourceError, OSError) as e:
        if not _warned:
            _warned = True
            log(f"shared auction scans: not pruned ({e}); rows past {PRICE_DAYS} days are ignored by reads")
        return False
    return True
