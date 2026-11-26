"""The auction house scans everyone on one install shares (owner,
2026-11-24: "scan from AH can be used across users, per faction, basically
a scan of the AH per ruleset").

Auctionator's Classic client keys a price database by realm (the ruleset
on Forever) and the player's faction. The key identifies the addon's
price history. It does not record which auctioneer the player visited.

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

A key with only a ruleset has no faction. Its prices stay private.
Earlier versions inferred a side before stripping the scanner. The shared
store cannot distinguish those rows from genuine Alliance or Horde keys.
Reads of those sides require the provenance field. A Neutral label was
always explicit. Backfill can recover labelled rows from the original scans.

Reads (scans): the caller's own characters' (ruleset, side) pairs, from
their own store (homes), each with the neutral house of that ruleset.
A character record naming only the ruleset can use its neutral house. A Horde
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
from wowstats.eventcache import norm_char, spellings
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
# A wow-player race belongs to someone the character met. A wow-prices faction belongs to the house.
_SIDE_FEEDS = ("wow-character", "wow-gear", "wow-recorder")
_CHARACTER_SIDE = f'feed:in({", ".join(map(lq, _SIDE_FEEDS))}) (faction:in("Alliance", "Horde") or race:*)'
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


def shared_event(ev: dict) -> dict | None:
    """The shared copy of one wow-prices event: its price fields, the
    ruleset (the event's `realm`) and the house's faction. None when either
    is unknown."""
    ruleset = str(ev.get("realm") or "").strip()
    house = str(ev.get("faction") or "").strip()
    if not ruleset or house not in (*SIDES, NEUTRAL) or not ev.get("_time"):
        return None
    out = {"_time": ev["_time"], "feed": FEED, "component": "wow", "ruleset": ahsource.ruleset_key(ruleset),
           "faction": house, "house_from": "auctionator_key",
           **{k: ev[k] for k in SHARED_FIELDS if ev.get(k) not in (None, "")}}
    out["_msg"] = f"{out.get('item_name') or out.get('item_key') or '?'}: lowest {out.get('price_copper')} copper"
    return out


def _char_filter(char: str) -> str:
    return "char:in(" + ", ".join(lq(n) for n in spellings(char)) + ")"


def _side_in_store(own, char: str) -> str | None:
    """The character's side from the person's own store: the newest event
    with a faction, else with a race."""
    rows = own.vl_direct(f'{own.stream} {_char_filter(char)} {_CHARACTER_SIDE} '
                         "| stats by (faction, race) max(_time) as t", ("faction", "race"), {"t": "maxtime"},
                         limit=1000)
    for r in sorted(rows, key=lambda r: r.get("t") or "", reverse=True):
        if s := side(r.get("faction"), r.get("race")):
            return s
    return None


def share(loc: str, lines: list[bytes]) -> int:
    """At ingest, after the person's own store took `lines`: the price
    events among them, stripped (shared_event), to the shared space. How
    many went."""
    from wowstats import eventstore

    if not loc:
        return 0
    out = []
    for line in lines:
        ev = json.loads(line)
        if ev.get("feed") == FEED and (s := shared_event(ev)) is not None:
            out.append(json.dumps(s, ensure_ascii=False, separators=(",", ":")).encode())
    if out:
        eventstore.get(loc).append(ACCOUNT, out)
    return len(out)


def homes(own) -> set[tuple[str, str]]:
    """The (ruleset, side) pairs of the person's own characters: each
    character's ruleset from its records' `realm` (Altoholic's character
    records, its own scans), its side from its faction or race. A known
    character realm alone grants access to its neutral house."""
    st = own.stream
    realms = own.vl_direct(f'{st} feed:in("wow-character", "{FEED}") realm:* char:* '
                           "| stats by (char, realm, faction, feed) max(_time) as t",
                           ("char", "realm", "faction", "feed"), {"t": "maxtime"}, limit=10000)
    sides = own.vl_direct(f'{st} char:* {_CHARACTER_SIDE} | stats by (char, faction, race) max(_time) as t',
                          ("char", "faction", "race"), {"t": "maxtime"}, limit=10000)
    side_of: dict[str, tuple[str, str]] = {}
    for r in sides:
        s, t = side(r.get("faction"), r.get("race")), r.get("t") or ""
        char = norm_char(r["char"])
        if s and t > side_of.get(char, ("", ""))[1]:
            side_of[char] = (s, t)
    out = set()
    for r in realms:
        s = str(r.get("faction") or "").strip().title()
        if r["feed"] == "wow-character" and s not in SIDES:
            s = side_of.get(norm_char(r["char"]), (None,))[0]
        if not s and r["feed"] == "wow-character":
            s = NEUTRAL  # Both sides can use a known ruleset's neutral house.
        if r.get("realm") and s in (*SIDES, NEUTRAL):
            # The ruleset as Auctionator spells it: a character record's
            # realm is the client's long name (wowstats.ahsource.ruleset_key).
            out.add((ahsource.ruleset_key(r["realm"]), s))
    return out


def one_house(rows: list[dict], home: tuple[str, str | None] | None, *, exact: bool = False) -> tuple[dict, list[dict]]:
    """Keep every day of one house together for trends and loot valuations."""
    houses: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (ahsource.ruleset_key(row.get("ruleset")), str(row.get("faction") or "").title())
        houses.setdefault(key, []).append(row)
    if home is None:
        # Without a character, retain the single private, unlabelled history
        # older single-player callers read.
        if len(houses) != 1 or next(iter(houses))[1]:
            return {}, []
        home = next(iter(houses))
    realm, faction = home
    realm = ahsource.ruleset_key(realm)
    # A realm-only key is a separate private history. When a character's
    # scans identify that exact key, use it even if an alt has a labelled key.
    choices = [faction] if exact or faction == "" else [faction, NEUTRAL, ""]
    key = next(((realm, f) for f in choices if (realm, f) in houses), None)
    if key is None:
        return {}, []
    return {"ruleset": key[0] or None, "faction": key[1] or None}, houses[key]


def character_house(own, char: str) -> tuple[str, str | None] | None:
    """Use the character's realm and side first. Without its realm record,
    use a single Auctionator key attached to that character. Multiple keys
    need an explicit selection; other characters cannot select a default."""
    rows = own.vl_direct(f'{own.stream} {_char_filter(char)} feed:="wow-character" realm:* '
                         "| stats by (char, realm, faction, race) max(_time) as t",
                         ("char", "realm", "faction", "race"), {"t": "maxtime"})
    current = max((r for r in rows if r.get("realm")), key=lambda r: r.get("t") or "", default=None)
    if current:
        faction = side(current.get("faction"), current.get("race")) or _side_in_store(own, current["char"])
        return ahsource.ruleset_key(current["realm"]), faction
    scans = own.vl_direct(f'{own.stream} {_char_filter(char)} feed:="{FEED}" realm:* '
                          "| stats by (realm, faction) max(_time) as t", ("realm", "faction"), {"t": "maxtime"})
    keys = {(ahsource.ruleset_key(r["realm"]), str(r.get("faction") or "").strip().title())
            for r in scans if r.get("realm")}
    if len(keys) != 1:
        return None
    realm, label = keys.pop()
    if label not in (*SIDES, NEUTRAL, ""):
        return None
    if label in SIDES and (actor_side := _side_in_store(own, char)) and actor_side != label:
        return None
    return realm, label


def for_character(own, char: str, rows: list[dict]) -> tuple[dict, list[dict]]:
    """Prices from the selected character's bucket, empty if it is ambiguous."""
    home = character_house(own, char) if rows else None
    return one_house(rows, home) if home else ({}, [])


def house_filter(pairs: set[tuple[str, str]]) -> str:
    """The LogsQL filter for the houses of `pairs`: each ruleset's side
    house and its neutral one."""
    return " or ".join(f'(ruleset:={lq(r)} faction:in({lq(s)}, "{NEUTRAL}"))' for r, s in sorted(pairs))


def scans(own, loc: str, agg: str, ops: dict, start: datetime | None = None, end: datetime | None = None,
          limit: int = 200000) -> list[dict]:
    """The scans' stats rows, one per item, house (ruleset, faction) and
    scan day: `agg` is the stats functions after `stats by (item_key,
    scan_day, ruleset, faction)`, `ops` how split answers merge. The person's
    private scans plus any explicitly labelled shared scans in their scope."""
    end = end or datetime.now(UTC)
    keys = ("item_key", "scan_day", "realm", "faction")
    rows = own.vl_direct(f'{own.stream} feed:="{FEED}" | stats by ({", ".join(keys)}) {agg}',
                         keys, ops, limit=limit, start=start, end=end)
    for r in rows:
        r["ruleset"] = ahsource.ruleset_key(r.pop("realm", None))
    pairs = homes(own) if loc else set()
    if pairs:
        confirmed = f'(house_from:="auctionator_key" or faction:="{NEUTRAL}")'
        rows += store(loc).vl_direct(f'feed:="{FEED}" {confirmed} ({house_filter(pairs)}) | '
                                     f'stats by ({", ".join(BY)}) {agg}', BY, ops, limit=limit, start=start, end=end)
    return merge_rows(rows, BY, ops)


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
        rows = own.vl_direct(f'{own.stream} feed:="{FEED}" | stats by (realm, faction, item_key, scan_day) '
                             "min(price_copper) as price_copper, max(price_high_copper) as price_high_copper, "
                             "max(available) as available, max(item_name) as item_name, max(item_id) as item_id, "
                             "max(_time) as t",
                             ("realm", "faction", "item_key", "scan_day"),
                             {"price_copper": "min", "price_high_copper": "max", "available": "max",
                              "item_name": "max", "item_id": "max", "t": "maxtime"}, limit=1_000_000)
        lines = []
        for r in rows:
            r = {k: v for k, v in r.items() if v not in (None, "", "NaN")}
            r["_time"] = r.pop("t", None)
            if (s := shared_event(r)) is not None:
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
