"""The auction house as a source of gear upgrades: what is on sale now, from
the Auctionator scans (feed wow-prices: the player's own, or on an install
with several people anyone's scans of the character's ruleset and faction,
wowstats.ahshared), and what it costs against the gold he has.

WoW Forever has no realms, only rulesets: Auctionator's "realm" is the
client's realm name, which on Forever names the ruleset. Here it is the
ruleset.

What Auctionator keeps (Source/Database/Mixin.lua, the legacy AH it runs on
in Forever): per item key and scan day, the day's lowest and highest
minimum buyout and the most it saw listed; no time of day. A full scan
(GetAll) writes every item listed at that moment and nothing for the rest;
a search writes the items it found. So "listed in the latest scan" is a
day: an item with a price on the house's newest full-scan day was on sale
then, one whose newest price is older was not in that scan.

The rules, each from the game or the data, not a taste:

- On sale: the item's newest scan day is the house's newest full-scan day
  or later (a search after the scan also saw it listed), and that day is
  today or yesterday (FRESH_DAYS). Auctions last 24 hours at most (2, 8 or
  24 h), so a listing seen on an older day has run out or was relisted,
  which no scan we have says. Nothing older is shown, not even as "last
  seen".
- A full scan: a day on which the house's scans priced at least a third of
  the items of its biggest day of the window read (and FULL_SCAN_MIN). A
  search prices one term's results; a GetAll prices the whole house. Only
  the days that can still count are read (today, yesterday, the day
  before): an older full scan can't hide anything newer.
- Tradeable: an item in the scan was listed on the auction house, so it is
  not soulbound (a bound item can't be listed). Only the item's own key
  (its ID) counts: "gr:<id>:<suffix>" keys are one random suffix, and an
  item whose stats come from a random suffix is left out, since the price
  of its ID is the cheapest of every suffix and the score has none of them.
- Cheap: the price is at most what the character nets in one level, measured
  from his own logout snapshots over the last GOLD_DAYS (gold at the newest
  snapshot less gold at the oldest, over the levels between: what he earned
  less what he spent). "You make that back within a level." Without a level
  gained in that window nothing is called cheap; the price still shows, as
  a share of his gold and of what his next riding training still needs.
"""

from __future__ import annotations

import re
import time

from wowstats import missing, riding
from wowstats.stores import num, parse_t

FRESH_DAYS = 1
FULL_SCAN_SHARE = 1 / 3
FULL_SCAN_MIN = 50
GOLD_DAYS = 30
RIDING_SKILL = 762
_ID = re.compile(r"^\d+$")
# Forever's client names its realm "<cluster> Realm <n> <ruleset>" ("... Realm 3
# PvE": Polestar's and Altoholic's records), while Auctionator keys its scans
# by the ruleset alone ("PvE", "PvE Alliance"). Seen 2026-11-25: the two never
# matched, so no scan priced anything (prices complete, gear "scan the AH").
_REALM_PREFIX = re.compile(r"^.*\bRealm \d+\s+(?=\S)")


def ruleset_key(name) -> str:
    """The ruleset a realm name or an Auctionator key names, in Auctionator's
    spelling: "CAMWOW EU5 Realm 3 PvE" and "PvE" are both "PvE"."""
    return _REALM_PREFIX.sub("", str(name or "").strip())


def same_ruleset(a, b) -> bool:
    """Whether two realm names name the same ruleset (ruleset_key)."""
    return ruleset_key(a) == ruleset_key(b)

# The scans read: the newest day and the two before it, enough to tell a
# full scan's day from a search's (on_sale). The scans themselves are the
# price list's read (wowstats.views.price_rows: one stats row per item,
# house and scan day); a full scan puts thousands of items on one instant,
# more than a raw read may return.
WINDOW_DAYS = FRESH_DAYS + 2
# The LogsQL pipes, after the stream filter.
# The character's ruleset comes with its gold: the same records carry `realm`
# (the client's realm name, the ruleset's on Forever).
Q_MONEY = 'feed:="wow-character" {cf} -kind:=played money_copper:* | fields _time, level, money_copper, realm'
Q_SKILLS = "{rec} kind:=skill | fields _time, skill_id, rank, max_rank, source_id"


def _age_days(t, now: float) -> int | None:
    """Whole days since a scan day's local midnight (the event's _time):
    0 for today, 1 for yesterday, in the player's own calendar."""
    d = parse_t(t)
    if d.year < 2000:
        return None
    return max(0, int((now - d.timestamp()) // 86400))


def on_sale(items: list[dict], faction: str | None, now: float | None = None, ruleset: str | None = None) -> dict:
    """{"scan": {...}, "items": {item_id: {...}}}: what the house's newest
    scans saw listed, as of now. The house (ruleset, faction) is the one
    scanned most recently that sells to this faction (a faction-less key,
    the faction's own, or a neutral house), on the character's ruleset when
    it is known."""
    now = now or time.time()
    ok = {None, "", "Neutral", faction} if faction else None

    def sells(r):
        return ((ok is None or (r.get("faction") or None) in ok)
                and (not ruleset or not r.get("ruleset") or same_ruleset(r["ruleset"], ruleset)))

    by_house: dict[tuple, dict] = {}
    for r in items:
        if not sells(r) or not r.get("scan_day"):
            continue
        k = (r.get("ruleset") or "", r.get("faction") or "")
        n, t = by_house.setdefault(k, {}).get(r["scan_day"], (0, None))
        by_house[k][r["scan_day"]] = (n + 1, r.get("t") if parse_t(r.get("t")) > parse_t(t) else t)
    if not by_house:
        return {"scan": None, "items": {}}
    house = max(by_house, key=lambda k: max(by_house[k]))  # scan_day is YYYY-MM-DD
    ds = by_house[house]
    biggest = max(n for n, _ in ds.values())
    full = [d for d, (n, _) in ds.items() if n >= max(FULL_SCAN_MIN, biggest * FULL_SCAN_SHARE)]
    last_day = max(ds)
    last_full = max(full) if full else None
    scan = {"day": last_day, "days_ago": _age_days(ds[last_day][1], now), "full_day": last_full,
            "full_days_ago": _age_days(ds[last_full][1], now) if last_full else None,
            "ruleset": house[0] or None, "faction": house[1] or None}
    floor = last_full or ""
    newest: dict[int, dict] = {}
    for r in items:
        if (r.get("ruleset") or "", r.get("faction") or "") != house:
            continue
        key, day, lo = str(r.get("item_key") or ""), r.get("scan_day") or "", r.get("lo")
        if not _ID.match(key) or lo in (None, "") or day < floor:
            continue
        age = _age_days(r.get("t"), now)
        if age is None or age > FRESH_DAYS:
            continue
        iid = int(key)
        cur = newest.get(iid)
        if cur is None or day > cur["seen_day"] or (day == cur["seen_day"] and num(lo) < cur["price_copper"]):
            av = r.get("av")
            newest[iid] = {"price_copper": int(num(lo)), "seen_day": day, "seen_days_ago": age,
                           "listed": int(num(av)) if av not in (None, "") else None}
    return {"scan": scan, "items": newest}


def latest(items: list[dict], faction: str | None, now: float | None = None,
           ruleset: str | None = None) -> dict[int, dict]:
    """Every item's newest price in the house on_sale would pick, at any
    age in the rows given: what a craft's materials cost (wowstats.crafting),
    where a listing gone since is still the best estimate. Each says its
    day, so the answer can say how old it is. {} with no scan."""
    now = now or time.time()
    house = (on_sale(items, faction, now, ruleset=ruleset).get("scan") or {})
    if not house:
        return {}
    key = (house.get("ruleset") or "", house.get("faction") or "")
    out: dict[int, dict] = {}
    for r in items:
        k, day, lo = str(r.get("item_key") or ""), r.get("scan_day") or "", r.get("lo")
        if (r.get("ruleset") or "", r.get("faction") or "") != key or not _ID.match(k) or lo in (None, ""):
            continue
        iid, cur = int(k), out.get(int(k))
        if cur is None or day > cur["seen_day"] or (day == cur["seen_day"] and num(lo) < cur["price_copper"]):
            out[iid] = {"price_copper": int(num(lo)), "seen_day": day, "days_ago": _age_days(r.get("t"), now)}
    return out


def gold_per_level(snaps: list[dict], now: float | None = None) -> int | None:
    """What the character nets in a level: gold at the newest logout
    snapshot less gold at the oldest of the last GOLD_DAYS, over the levels
    between. None without a level gained in the window (or at a loss)."""
    now = now or time.time()
    rows = []
    for r in snaps:
        t, lv, m = parse_t(r.get("_time")).timestamp(), r.get("level"), r.get("money_copper")
        if lv in (None, "") or m in (None, "") or t < now - GOLD_DAYS * 86400:
            continue
        rows.append((t, num(lv), num(m)))
    if len(rows) < 2:
        return None
    rows.sort()
    (_, l0, m0), (_, l1, m1) = rows[0], rows[-1]
    if l1 <= l0 or m1 <= m0:
        return None
    return round((m1 - m0) / (l1 - l0))


def mount_left(money: int | None, skills: list[dict], book: list[dict] | None = None,
               learned: list[dict] | None = None) -> dict | None:
    """The next riding training not yet known (the knowledge base's
    riding.apprentice / riding.journeyman, with the vendor mount when the
    training doesn't come with one), its price and what is still missing.
    Known: the highest riding rank any read saw (wowstats.riding.state:
    Altoholic's spellbook, the Recorder's learn and skill records)."""
    from wowstats.analysis import kb

    rec = max((r for r in skills or [] if str(r.get("skill_id")) == str(RIDING_SKILL)),
              key=lambda r: int(num(r.get("rank"))), default=None)
    rank = riding.state({"rank": rec.get("rank"), "t": rec.get("_time")} if rec else None, book, learned)["rank"]
    for name in ("riding.apprentice", "riding.journeyman"):
        rule = kb.static_rule(name)
        if not rule:
            return None
        v = rule["value"]
        if rank >= (v.get("skill") or 0):
            continue
        need = v["training_copper"]
        if not v.get("comes_with_mount"):
            vendor = (kb.static_rule("riding.vendor_mounts") or {}).get("value") or {}
            need += vendor.get(str(v["level"])) or 0
        have = money or 0
        return {"name": name, "level": v["level"], "need_copper": need, "left_copper": max(0, need - have)}
    return None


def queries(cf: str, rec: str) -> dict[str, tuple]:
    """The reads market() needs besides the scans, as (LogsQL pipe after the
    stream filter, stats keys, merge ops), so a caller can run them side by
    side."""
    return {"money": (Q_MONEY.format(cf=cf), (), None), "skills": (Q_SKILLS.format(rec=rec), (), None),
            **{k: (v, (), None) for k, v in riding.queries(cf, rec).items()}}


def market(res: dict, *, faction: str | None, money: int | None,
           now: float | None = None) -> dict:
    """advise()'s `market` from the answers to queries(), with the scans
    (wowstats.views.price_rows, any window from WINDOW_DAYS up) as
    res["items"]."""
    now = now or time.time()
    # Only the scans' window: a wider read (the price list's) holds older days.
    recent = [r for r in res.get("items") or [] if (a := _age_days(r.get("t"), now)) is not None and a < WINDOW_DAYS]
    homes = sorted((r for r in res.get("money") or [] if r.get("realm")), key=lambda r: parse_t(r.get("_time")))
    sale = on_sale(recent, faction, now, ruleset=homes[-1]["realm"] if homes else None)
    # Prices at any age for the crafts' materials (owner, 2026-11-25: "if we
    # have AH prices"): the scans of the same house, newest per item.
    every = latest(res.get("items") or [], faction, now, ruleset=homes[-1]["realm"] if homes else None)
    return {**sale, "latest": every, "money_copper": money,
            "gold_per_level_copper": gold_per_level(res.get("money") or [], now),
            "mount": mount_left(money, res.get("skills") or [], res.get("riding_book"), res.get("riding_learn"))}


def notice_none() -> str:
    """The line with no scan at all: the preflight's own fix for missing
    prices (steps.json's `data`, wowstats.missing), one wording everywhere."""
    k = next((k for k in missing.kinds() if k.get("id") == "prices"), None)
    return k["fix"] if k and k.get("fix") else "Scan the auction house with Auctionator, then log out."


def summary(market: dict | None, upgrades: list[dict]) -> dict:
    """The gear answer's `ah`: whether there is anything to say, and the one
    line to scan again when the scans can't say what is on sale now."""
    if market is None:
        return {"available": False}
    scan = market.get("scan")
    out = {"available": True, "scan_day": (scan or {}).get("day"), "scan_days_ago": (scan or {}).get("days_ago"),
           "on_sale": len(market.get("items") or {}),
           "upgrades_on_sale": sum(1 for u in upgrades if any(s["kind"] == "ah" for s in u.get("sources") or [])),
           "money_copper": market.get("money_copper"), "cheap_under_copper": market.get("gold_per_level_copper"),
           "mount": market.get("mount")}
    if scan is None:
        out["notice"] = notice_none()
    elif (scan.get("days_ago") or 0) > FRESH_DAYS:
        out["notice"] = (f"Scan the Auction House with Auctionator again to see what's for sale: your last scan "
                         f"is {scan['days_ago']} days old.")
    return out


def source(sale: dict, gain: float | None, market: dict) -> dict:
    """One upgrade's auction house source, with what it costs against his
    gold: the gain per gold (to rank the cheap ones), the share of the gold
    he has and of what the next riding training still needs."""
    p = sale["price_copper"]
    money = market.get("money_copper")
    mount = market.get("mount") or {}
    cheap_under = market.get("gold_per_level_copper")
    out = {"kind": "ah", **sale,
           "gain_per_gold": round(gain / (p / 10000), 2) if gain and p > 0 else None,
           "gold_share_pct": round(p / money * 100, 1) if money else None,
           "affordable": money is not None and p <= money,
           "mount_share_pct": round(p / mount["left_copper"] * 100, 1) if mount.get("left_copper") else None,
           "cheap": bool(cheap_under and p <= cheap_under and (money is None or p <= money))}
    return out


def price_text(copper: int) -> str:
    """12g 40s: gold and silver, copper only under a gold (as a player
    reads a price tag)."""
    g, rest = divmod(int(copper), 10000)
    s, c = divmod(rest, 100)
    parts = [f"{g}g" if g else "", f"{s}s" if s else "", f"{c}c" if c and not g else ""]
    return " ".join(p for p in parts if p) or "0c"


def seen_text(days_ago: int | None) -> str:
    return {0: "on sale today", 1: "on sale yesterday"}.get(days_ago, f"on sale {days_ago} days ago")
