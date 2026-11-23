"""Read-only clients for the two stores, and the small helpers every view uses.

VictoriaLogs holds the game events (the player's own data, kept as long as
they like). VictoriaMetrics holds the Battle.net API gauges, which Blizzard's
terms cap at 30 days: that one runs with a short retention of its own.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

from wowstats import timebounds as _timebounds

QUERY_TIMEOUT = 20
ROW_LIMIT = 20000
# Rows per request when reading events themselves (wowstats.eventcache, and
# any raw query that fills ROW_LIMIT). A VictoriaLogs with a 1 GiB limit was
# OOM-killed three times on 2026-11-18; a page this size is a few MB there.
VL_PAGE = 5000
# Game data is kept for good (the owner, 2026-11-18): every query reads the
# whole history. Nothing in the game is older than its release.
HISTORY_START = datetime(2004, 11, 23, tzinfo=UTC)
# At most this many VictoriaLogs requests at once from this process, whatever
# asks (the page's parallel calls, the planner, the MCP, the publisher).
VL_GATE = threading.BoundedSemaphore(max(1, int(os.environ.get("VL_CONCURRENCY", "2") or 2)))
HOUR = 3600
# Recorder kinds whose `level` is the player's (docs/contract.md, shipper
# KINDS): a ding and a chunk's session record. A kill's, a unit's or a
# rare's `level` is the creature's, an accept's or turn-in's the quest's.
PLAYER_LEVEL_KINDS = frozenset({"level", "session"})


class SourceError(Exception):
    pass


class IncompleteRead(SourceError):
    """A bounded read that couldn't hold its whole range: more records at one
    instant than one request may return. Raised instead of handing back part
    of the evidence (review 4, G); a SourceError, so every caller that shows
    a store failure shows this one too."""

    def __init__(self, gaps: dict[str, int], page: int):
        self.gaps, self.page = dict(gaps), page
        where = ", ".join(f"{n} at {t}" for t, n in sorted(self.gaps.items()))
        super().__init__(f"more records in one second than a page of {page} holds: {where}")


class ArchiveUnavailable(SourceError):
    """A read whose range reaches the archive's days (older than
    VL_ARCHIVE_AFTER_DAYS) while the archive doesn't answer. Raised rather
    than answering from the recent store alone: a history without its
    older days reads as fewer deaths, and the event cache took the missing
    hours for retention and dropped them (review 6). A SourceError, so
    every caller shows it as the store failing."""

    def __init__(self, detail: str):
        super().__init__(f"the archive of older days didn't answer: {detail}")


class SemanticError(SourceError):
    """The semantic layer couldn't compute a page's numbers
    (wowstats.analysis.pages). The page's numbers have one computation path
    (owner, 2026-11-22: "use the semantic model all the time"): a failure is
    an answer that failed, never numbers counted another way. A SourceError,
    so the prebuilt answers keep the last good set marked stale, a route
    answers 502 and the publisher keeps the previous sites."""


# How long the archive's answer to "do you hold older days?" is kept; a
# failed check is asked again sooner, so a read works again soon after the
# archive is back.
ARCHIVE_RECHECK_S = 600
ARCHIVE_RETRY_S = 30


# Blizzard's Developer API Terms: API data is kept 30 days at most.
BNET_KEEP_S = 30 * 86400
# Battle.net's class and race ids as the addons name them (the profile
# carries ids; its names follow BNET_LOCALE).
BNET_CLASSES = {"1": "WARRIOR", "2": "PALADIN", "3": "HUNTER", "4": "ROGUE", "5": "PRIEST", "7": "SHAMAN",
                "8": "MAGE", "9": "WARLOCK", "11": "DRUID"}
BNET_RACES = {"1": "Human", "2": "Orc", "3": "Dwarf", "4": "Night Elf", "5": "Undead", "6": "Tauren", "7": "Gnome",
              "8": "Troll", "10": "Blood Elf", "11": "Draenei"}


def public_error(e: BaseException, where: str = "") -> str:
    """What a page, the shipper or an AI client is told when a store fails:
    no host name, no store message (2026-11-20 server review, info 2: the
    VictoriaLogs host and its error text reached all three). The detail goes
    to this server's log under the same request id. An incomplete read is
    not a store that didn't answer: retrying won't help, so it says so."""
    rid = secrets.token_hex(4)
    print(f"store error [{rid}]{f' on {where}' if where else ''}: {e}", file=sys.stderr, flush=True)
    if isinstance(e, IncompleteRead):
        return (f"no answer rather than a partial one: one second holds more records than a read can return "
                f"({e.page}) (request {rid} in the server's log)")
    if isinstance(e, SemanticError):
        return (f"no answer rather than numbers counted another way: the page's numbers couldn't be computed "
                f"(request {rid} in the server's log)")
    if isinstance(e, ArchiveUnavailable):
        return (f"no answer rather than one without the older days: the archive that holds them didn't answer; "
                f"try again in a minute (request {rid} in the server's log)")
    return f"the data store didn't answer; try again in a minute (request {rid} in the server's log)"


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_us(t: datetime) -> str:
    """To the microsecond: range bounds that split a query must not lose
    the records inside a second."""
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def lq(s: str) -> str:
    """A LogsQL quoted string (Go-style escapes, which JSON's are a subset of)."""
    return json.dumps(s, ensure_ascii=False)


def num(v, default=0.0) -> float:
    """A number from a store row; `default` when there is none. VictoriaLogs
    answers sum() or max() over rows without the field as "NaN": no number
    (recovery drill, 2026-11-22: `zones` failed on it)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def opt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):  # "NaN" from the store: no number, and no JSON for one either
        return None
    return int(f) if f.is_integer() else f


def parse_t(s) -> datetime:
    if not s:
        return datetime.min.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def jlist(s) -> list:
    if isinstance(s, list):
        return s
    try:
        v = json.loads(s or "[]")
    except ValueError:
        return []
    return v if isinstance(v, list) else []


def truthy(v) -> bool:
    return str(v).lower() in ("true", "1", "yes")


# A play session ends when the Recorder writes nothing for more than this. It
# writes a record at least every 5 minutes while the game runs (its chunk
# header), so 330 s of silence means the game was closed or the client hung.
# One definition (wowstats.timebounds): the journal, the Overview and
# timeline, and the semantic layer's sessions (wowstats.analysis.prep).
SESSION_GAP_S = _timebounds.SESSION_GAP_S
# What split_sessions reads: the Recorder's records per minute, with the first
# and last record's time in each minute, so a gap is measured to the second.
SESSION_BUCKETS = "| stats by (_time:1m) count() as n, min(_time) as t0, max(_time) as t1"
SESSION_BUCKET_OPS = {"n": "sum", "t0": "mintime", "t1": "maxtime"}


def split_sessions(rows: list[dict], gap: float = SESSION_GAP_S) -> list[tuple[float, float]]:
    """(start, end) unix seconds of each session, from SESSION_BUCKETS rows:
    a silence over `gap` between two records ends one."""
    spans = sorted((parse_t(r.get("t0")).timestamp(), parse_t(r.get("t1")).timestamp()) for r in rows
                   if num(r.get("n")) > 0 and parse_t(r.get("t0")).year > 1 and parse_t(r.get("t1")).year > 1)
    out: list[tuple[float, float]] = []
    for a, b in spans:
        if out and a - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def record_order(r: dict) -> tuple:
    """How records with the same _time follow each other: a Recorder record
    (rec:<char id>:<chunk>:<index>) by its chunk sequence, then its index,
    as numbers; any other id as text. Sorted as text, rec:aa:1:10 came
    before rec:aa:1:2 and the earlier of two zone records at one time took
    the time of the later (second review of review 3)."""
    sid = r.get("source_id") or ""
    if sid.startswith("rec:"):
        cid, seq, idx = (sid[4:].rsplit(":", 2) + ["", ""])[:3]
        if seq.isdigit() and idx.isdigit():
            return ("rec:" + cid, int(seq), int(idx))
    return (sid, -1, -1)


# The zone records zone_time reads, after the Recorder's feed and character.
ZONE_RECORDS = "kind:=zone | fields _time, zone, subzone, source_id, rev"


def zone_time(zones: list[dict], spans: list[tuple[float, float]]) -> list[dict]:
    """Each zone record once (dedupe), oldest first, with `secs`: from it to
    the next zone record of its session, else to that session's last record.
    `spans` are split_sessions' (start, end); a record outside them gets 0.

    Worked out here from the records' own times, never from the shipper's
    duration_s: the shipper set that from the chunks it had read so far and
    never sent the record again, so a session read in two goes kept the
    first go's 5 s where one read gave 306 s (external review 3). Nothing of
    it is stored: an old store with the short values reads right as it is."""
    rows = sorted(({**r, "_t": parse_t(r.get("_time")).timestamp()} for r in dedupe(zones)),
                  key=lambda r: (r["_t"], *record_order(r)))
    spans = sorted(spans)
    out, j = [], 0
    for i, r in enumerate(rows):
        t = r.pop("_t")
        while j < len(spans) and spans[j][1] < t:
            j += 1
        if j == len(spans) or spans[j][0] > t:
            r["secs"] = 0.0
        else:
            end = spans[j][1]
            nxt = rows[i + 1]["_t"] if i + 1 < len(rows) else end
            r["secs"] = round(max(0.0, min(nxt, end) - t), 1)
        out.append(r)
    return out


# The flight records taxi_time reads, after the Recorder's feed and character.
FLIGHT_RECORDS = "kind:in(taxi, land) | fields _time, kind, zone, seconds, source_id, rev"


def taxi_time(rows: list[dict]) -> list[dict]:
    """Each taxi record once (dedupe), oldest first, with `secs` and the
    `land` record that ends it: the first land before the next take-off.
    `secs` is the land record's own `seconds` (the game's time in the air),
    else the two records' times; None while no land is stored.

    Worked out here, never from the taxi's duration_s: the shipper set that
    only when the land record came in the same run, so a flight across a
    chunk roll read in two runs had none (pipeline invariants, #86)."""
    recs = sorted(({**r, "_t": parse_t(r.get("_time")).timestamp()} for r in dedupe(rows)
                   if r.get("kind") in ("taxi", "land")), key=lambda r: (r["_t"], *record_order(r)))
    out: list[dict] = []
    for r in recs:
        t = r.pop("_t")
        if r["kind"] == "taxi":
            out.append({**r, "_t": t, "secs": None, "land": None})
        elif out and out[-1]["land"] is None:
            secs = num(r.get("seconds"))
            out[-1]["land"] = r
            out[-1]["secs"] = round(secs if secs and secs > 0 else max(0.0, t - out[-1]["_t"]), 1)
    for f in out:
        f.pop("_t")
    return out


def zone_totals(visits: list[dict]) -> list[dict]:
    """zone_time's rows summed per zone, the most time first: {zone, secs, visits}."""
    by: dict[str, dict] = {}
    for r in visits:
        if r.get("zone"):
            d = by.setdefault(r["zone"], {"zone": r["zone"], "secs": 0.0, "visits": 0})
            d["secs"] += r["secs"]
            d["visits"] += 1
    return sorted(({**d, "secs": int(d["secs"])} for d in by.values()), key=lambda d: -d["secs"])


def pick_splits(rows: list[dict]) -> dict[int, dict]:
    """Polestar's level split per level, from its wow-level events: a
    finished split beats a partial one, otherwise the newest wins. One rule
    for the pages and the analysis' `levels` table (the semantic layer)."""
    best: dict[int, dict] = {}
    for r in dedupe(rows):
        lvl = opt_num(r.get("level"))
        if lvl is None:
            continue
        cur = best.get(lvl)
        partial, cur_partial = truthy(r.get("partial")), cur is not None and truthy(cur.get("partial"))
        if cur is None or (cur_partial and not partial) or (
                cur_partial == partial and parse_t(r.get("_time")) >= parse_t(cur.get("_time"))):
            best[lvl] = r
    return best


def record_id(r: dict) -> str:
    """Which record a row is: its source_id. Shippers up to 0.7.0-rc.5 named
    a gear sheet by its content alone (gear:<char>:<digest>), so a sheet worn
    again later reused the id of its first occurrence; their capture time
    (_time) tells those apart, and a retry of the same line still has the
    same time. Newer shippers put the capture time in the id."""
    sid = r.get("source_id")
    if not sid:
        return json.dumps(r, sort_keys=True)
    if sid.startswith("gear:") and sid.count(":") == 2 or _content_id(sid):
        return f"{sid}:{r.get('_time')}"
    return sid


def _content_id(sid: str) -> bool:
    """A snapshot id made of its content alone, no time: the bags
    (bags:<char>:<digest>), a quest set (quests:<char>:<source>:<digest>)
    and the roster's sheet (character:<char>:<digest>; Polestar's own is
    character:<char>:<logout epoch>). The same content seen again later (the
    game rewrote the file at logout, a run after a lost state.json) came
    under the same id, and the newest copy erased when it was first seen:
    quest evidence then dated a reward from the later copy (evidence
    property (a), tests/invariants/test_evidence.py). Keyed by `_time` too,
    each observation stays; a resend of one line still has its time."""
    if sid.startswith(("bags:", "quests:")):
        return True
    if sid.startswith("character:"):
        return not sid.rpartition(":")[2].isdigit()
    return False


def dedupe(rows: list[dict]) -> list[dict]:
    """One row per record (record_id), the newest (then the highest rev): a
    timeout after the store wrote makes the shipper send lines twice, and a
    growing record (the current level's split) comes again under the same key."""
    best: dict[str, dict] = {}
    for r in rows:
        k = record_id(r)
        cur = best.get(k)
        if cur is None or (parse_t(r.get("_time")), num(r.get("rev"))) >= (
                parse_t(cur.get("_time")), num(cur.get("rev"))):
            best[k] = r
    return list(best.values())


# Which snapshot (a gear sheet, the bags, a character sheet) is the one in
# force, as LogsQL and as Python (snapshot_rank): one with an exact
# observation time before one whose time is only an upper bound or the
# run's clock (`at_basis`: its real time could be any time before, so it
# can't be shown newer than an exact one), then the newest, then the id, so
# two snapshots on one time give the same answer in either arrival order.
# Evidence properties (b) and (c), tests/invariants/test_evidence.py.
SNAPSHOT_SORT = "sort by (at_basis, _time desc, source_id desc)"


def snapshot_rank(r: dict) -> tuple:
    return (not r.get("at_basis"), parse_t(r.get("_time")), str(r.get("source_id") or ""))


def newest(rows: list[dict]) -> dict:
    """The row in force (snapshot_rank): the archive and the recent store
    each answer their own `limit 1`."""
    return max(rows, key=snapshot_rank, default={})


def part_rows(rows: list[dict], parts: tuple[str, ...]) -> dict[str, dict]:
    """{part: the row in force among those that carry it}: where each part
    of a snapshot source was last observed. A part no row carries is left
    out."""
    out: dict[str, dict] = {}
    for r in sorted(rows, key=snapshot_rank, reverse=True):
        for p in parts:
            if p not in out and r.get(p) not in (None, ""):
                out[p] = r
    return out


def newest_with(rows: list[dict], parts: tuple[str, ...]) -> dict:
    """newest(), with each of `parts` it doesn't carry taken from the newest
    row that does: a snapshot that lacks one (Syndicator's bank cache lost,
    a sheet without its money) doesn't erase it from an earlier complete
    one (evidence property (d)). A part no row carries stays absent:
    unknown, never empty (e). `part_at` is each part's own observation
    time ({part: _time}): a part taken from an older snapshot keeps that
    snapshot's time, never the newer row's (review 6)."""
    top = newest(rows)
    if not top:
        return {}
    out = dict(top)
    got = part_rows(rows, parts)
    for p, r in got.items():
        out[p] = r[p]
    out["part_at"] = {p: r.get("_time") for p, r in got.items()}
    return out


def in_force(read, head: str, fields: str, parts: tuple[str, ...]) -> list[dict]:
    """The rows that decide a snapshot source: the one in force
    (SNAPSHOT_SORT) and, for each part, the one in force among those that
    carry it (`part:*`). newest_with over these gives the same answer as
    over every snapshot ever sent.

    `read(query)` answers a LogsQL query (after the stream filter, if the
    reader adds it); `head` is the filter, `fields` the fields to keep (the
    parts among them). Review 6: the readers took a fixed window of the 10
    newest snapshots, so an eleventh partial one hid the last complete one:
    the page and the MCP lost the equipment, the bank and the money while
    the analysis, which reads every event, still had them. One query per
    part instead, however many partial snapshots came after."""
    tail = f"| fields {fields} | {SNAPSHOT_SORT} | limit 1"
    rows = list(read(f"{head} {tail}"))
    for p in parts:
        rows += read(f"{head} {p}:* {tail}")
    return rows


# The character sheet (wow-character, Polestar's logout sheet or the
# roster's) the hero line and the MCP read, after the character filter.
SHEET_FIELDS = ("_time, at_basis, source_id, level, xp, xp_max, money_copper, zone, race, class, faction, "
                "realm, resting, source")
SHEET_PARTS = ("money_copper",)
# Who the character is, from the newest sheet that says it: Altoholic's
# sheet (DataStore_Characters, source altoholic) carries the purse and XP
# but no class, race or side, and saves the same logout as Polestar's, often
# a second later; as the newest row it must not blank them (the hero line
# would then take Battle.net's, or none).
SHEET_IDENTITY = ("class", "race", "faction")


def sheet_rows(read, cf: str) -> list[dict]:
    """The character sheet's rows in force (in_force) for pick_sheet."""
    return in_force(read, f'feed:="wow-character" {cf} -kind:=played', SHEET_FIELDS, SHEET_PARTS + SHEET_IDENTITY)


def pick_sheet(rows: list[dict]) -> dict:
    """The character sheet in force; the money, class, race and side each
    from the newest sheet that has it."""
    return newest_with(rows, SHEET_PARTS + SHEET_IDENTITY)


def merge_rows(rows: list[dict], keys: tuple, ops: dict) -> list[dict]:
    """Stats rows from two stores (recent and archive) merged by `keys`."""
    out: dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r.get(f, "") for f in keys)
        cur = out.get(k)
        if cur is None:
            out[k] = dict(r)
            continue
        for f, op in ops.items():
            a, b = cur.get(f), r.get(f)
            if b in (None, ""):
                continue
            if a in (None, ""):
                cur[f] = b
            elif op == "sum":
                cur[f] = str(num(a) + num(b))
            elif op == "max":
                cur[f] = a if num(a) >= num(b) else b
            elif op == "min":
                cur[f] = a if num(a) <= num(b) else b
            elif op == "maxtime":
                cur[f] = a if parse_t(a) >= parse_t(b) else b
            elif op == "mintime":
                cur[f] = a if parse_t(a) <= parse_t(b) else b
            elif op == "union":
                cur[f] = json.dumps(sorted(set(jlist(a)) | set(jlist(b))))
    return list(out.values())


def tenant_headers(account_id: int) -> dict:
    """VictoriaLogs' tenant (AccountID, ProjectID 0) on every request to it:
    the store, not the query text, keeps one person's events from another's
    (wowstats.tenancy). Tenant 0 is the store's default, so a single-person
    install sees no difference."""
    return {"AccountID": str(int(account_id)), "ProjectID": "0"}


def env_account(env: dict) -> int:
    v = str(env.get("VL_ACCOUNT_ID", "0") or "0").strip()
    if not v.isdigit():
        raise ValueError(f"VL_ACCOUNT_ID={v!r}: a whole number (the VictoriaLogs tenant)")
    return int(v)


def tenant_of(env: dict, store=None) -> int:
    """The tenant a cache entry belongs to: the store's when one is given,
    else the settings'. Caches of player data key on it, so two people's
    characters of the same name never share an answer."""
    if store is not None and getattr(store, "account_id", None) is not None:
        return int(store.account_id)
    return env_account(env)


def http_get(url: str, params: dict, timeout: int = QUERY_TIMEOUT, headers: dict | None = None) -> bytes:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read(300).decode("utf-8", "replace").strip()
        raise SourceError(f"{e.code} from {urllib.parse.urlsplit(url).hostname}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"{urllib.parse.urlsplit(url).hostname}: {e}") from None


def paged_read(fetch, query: str, start: datetime, end: datetime, limit: int, depth: int = 0) -> list[dict]:
    """fetch(query, start, end, limit) is one VictoriaLogs request. When its
    answer fills `limit`, the range is read again in pages: for a filter
    (with at most a `fields` pipe), pages of VL_PAGE rows planned from the
    hourly count, each hour read once; for anything else (stats, sort), the
    range split in two and the halves merged by the caller, as before.

    The old way split every full answer in two from 2004 on: with the events
    in the last days, each split re-read the same full page, 243k rows for
    the 23k events of one character (2026-11-18).

    A full answer that can't be split further (under 2 s, or 24 halvings
    deep) raises IncompleteRead: a bounded read is whole or says it isn't."""
    rows = fetch(query, start, end, limit)
    if len(rows) < limit:
        return rows
    if depth >= 24 or end - start < timedelta(seconds=2):
        raise IncompleteRead({iso(start): len(rows)}, limit)
    del rows  # let the full page go before the next reads
    from .logsql import split_pipes

    parts = split_pipes(query)
    if depth == 0 and all(p.startswith("fields ") for p in parts[1:]):
        hist = {int(parse_t(r.get("_time")).timestamp()): int(num(r.get("n")))
                for r in fetch(f"{parts[0]} | stats by (_time:1h) count() as n", start, end, 100_000)
                if r.get("_time")}
        out: list[dict] = []
        for lo, hi, n in hour_pages(sorted(hist), hist, HOUR, VL_PAGE):
            a = max(start, datetime.fromtimestamp(lo, UTC))
            z = min(end, datetime.fromtimestamp(hi, UTC) - timedelta(microseconds=1))
            out += paged_read(fetch, query, a, z, VL_PAGE + 1 if n <= VL_PAGE else VL_PAGE, depth + 1)
        return out
    mid = start + (end - start) / 2
    return (paged_read(fetch, query, start, mid, limit, depth + 1)
            + paged_read(fetch, query, mid + timedelta(microseconds=1), end, limit, depth + 1))


def hour_pages(buckets: list[int], counts: dict[int, int], step: int, page: int) -> list[tuple[int, int, int]]:
    """Contiguous runs of `buckets` (start seconds, sorted) in ranges of at
    most `page` rows: [(lo, hi, rows)], hi exclusive. A range covers the
    buckets between its ends too, so their rows count. A bucket alone over
    `page` is its own range (the caller splits it finer)."""
    allb = sorted(counts)
    out: list[list[int]] = []
    for b in buckets:
        n = counts.get(b, 0)
        if out:
            lo, hi, rows = out[-1]
            between = sum(counts[x] for x in allb[bisect.bisect_left(allb, hi):bisect.bisect_left(allb, b)])
            if rows + between + n <= page:
                out[-1] = [lo, b + step, rows + between + n]
                continue
        out.append([b, b + step, n])
    return [(lo, hi, n) for lo, hi, n in out]


class Store:
    """VictoriaLogs (plus, optionally, an archive VictoriaLogs for older
    days) and VictoriaMetrics, read only. Without VL_ARCHIVE_URL there is one
    store and it holds the whole history."""

    def __init__(self, vl_url: str, vl_archive_url: str = "", vm_url: str = "", logs_filter: str = "feed:wow-*",
                 hot_days: int = 89, profile_vm_url: str | None = None, account_id: int = 0):
        self.vl_url = vl_url.rstrip("/")
        # The VictoriaLogs tenant every logs request carries (tenant_headers).
        self.account_id = int(account_id)
        self.vl_archive_url = vl_archive_url.rstrip("/")
        self.vm_url = vm_url.rstrip("/")
        # Battle.net's character profile only (the look, the item level,
        # the talents): the owner allowed it in public (2026-11-18), so the
        # public view reads these from vm-wow and nothing else (no market).
        self.profile_vm_url = (vm_url if profile_vm_url is None else profile_vm_url).rstrip("/")
        self.stream = logs_filter
        self.hot_days = hot_days
        self._archive = {"checked": 0.0, "has_old": False, "error": None}
        self._archive_lock = threading.Lock()
        # A test's fake store answers vl() itself; the real one reads through the cache.
        self.use_cache = True
        # {character: when the exporter last read it}, as this process last
        # saw vm-wow answer (bnet_read_at): a time, no Battle.net data. When
        # vm-wow then fails, the answer is "as of" that read, never "not read".
        self._bnet_seen: dict[str, float] = {}

    # ------------------------------------------------------------ logs

    def vl_raw(self, base: str, query: str, start: datetime, end: datetime, limit: int = ROW_LIMIT) -> list[dict]:
        """One request, through the process' gate (VL_GATE), in this
        person's tenant (tenant_headers)."""
        with VL_GATE:
            body = http_get(f"{base}/select/logsql/query", {
                "query": query, "start": iso_us(start), "end": iso_us(end), "limit": str(limit),
            }, headers=tenant_headers(self.account_id))
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    def vl_paged(self, base: str, query: str, start: datetime, end: datetime, limit: int = ROW_LIMIT) -> list[dict]:
        """Like vl_raw, but an answer that fills the row limit is read again
        in pages, so years of history come back whole: no answer is ever
        silently cut at `limit` rows (paged_read)."""
        return paged_read(lambda q, a, b, n: self.vl_raw(base, q, a, b, n), query, start, end, limit)

    def archive_status(self) -> tuple[bool, str | None]:
        """Whether the archive holds events older than the recent store's
        window, and the error when it didn't answer; checked every
        ARCHIVE_RECHECK_S, every ARCHIVE_RETRY_S after a failure."""
        if not self.vl_archive_url:
            return False, None
        st = self._archive
        with self._archive_lock:
            if time.time() - st["checked"] < (ARCHIVE_RETRY_S if st["error"] else ARCHIVE_RECHECK_S):
                return st["has_old"], st["error"]
            now = datetime.now(UTC)
            try:
                rows = self.vl_raw(self.vl_archive_url, f"{self.stream} | limit 1",
                                   HISTORY_START, now - timedelta(days=self.hot_days), 1)
                st.update(has_old=bool(rows), error=None)
            except SourceError as e:
                st.update(has_old=False, error=str(e))
            st["checked"] = time.time()
            return st["has_old"], st["error"]

    @property
    def cache(self):
        """The process' event cache for these stores (wowstats.eventcache)."""
        from . import eventcache

        return eventcache.shared(self)

    def vl(self, query: str, keys: tuple = (), ops: dict | None = None, limit: int = ROW_LIMIT) -> list[dict]:
        """A LogsQL query over the whole history. One character's queries
        are answered from the event cache (wowstats.eventcache), read once
        and then only what changed; the rest goes to VictoriaLogs
        (vl_direct)."""
        rows = self.cache.answer(query) if self.use_cache else None
        if rows is not None:
            return rows
        return self.vl_direct(query, keys, ops, limit)

    def vl_direct(self, query: str, keys: tuple = (), ops: dict | None = None, limit: int = ROW_LIMIT,
                  start: datetime | None = None, end: datetime | None = None) -> list[dict]:
        """A LogsQL query to VictoriaLogs over [start, end] (default the whole
        history). Raw rows are concatenated; stats rows (ops given) are
        merged by `keys` with each field's op (which is also what makes a
        split range add up)."""
        rows = self._split(lambda b, a, z: self.vl_paged(b, query, a, z, limit), start, end)
        if ops is None:
            return rows
        return merge_rows(rows, keys, ops)

    def vl_range(self, query: str, start: datetime, end: datetime, limit: int) -> list[dict]:
        """One page: the rows of [start, end], at most `limit` (one request
        per store the range reaches)."""
        return self._split(lambda b, a, z: self.vl_raw(b, query, a, z, limit), start, end)

    def _split(self, read, start: datetime | None, end: datetime | None) -> list[dict]:
        """read(base, start, end) on the recent store, and on the archive for
        the part of the range older than hot_days when it holds any.

        A range that reaches the archive's days while the archive doesn't
        answer (its check or its read) raises ArchiveUnavailable: never the
        recent store's rows alone as if they were the whole history
        (review 6). A range inside the recent days doesn't need it."""
        now = datetime.now(UTC)
        start, end = start or HISTORY_START, end or now
        cutoff = now - timedelta(days=self.hot_days)
        use_archive, err = self.archive_status()
        if err and start < cutoff:
            raise ArchiveUnavailable(err)
        if not use_archive:
            return read(self.vl_url, start, end)
        rows = read(self.vl_url, max(start, cutoff), end) if end >= cutoff else []
        if start < cutoff:
            try:
                rows = read(self.vl_archive_url, start, min(end, cutoff - timedelta(microseconds=1))) + rows
            except IncompleteRead:
                raise
            except SourceError as e:
                with self._archive_lock:
                    self._archive.update(error=str(e), checked=time.time())
                raise ArchiveUnavailable(str(e)) from None
        return rows

    def sources(self) -> dict:
        has_old, err = self.archive_status()
        archive = "off" if not self.vl_archive_url else (
            "unreachable" if err else ("used" if has_old else "not needed"))
        return {"archive": archive, "archive_after_days": self.hot_days}

    # ------------------------------------------------------------ metrics

    def vm_query(self, q: str) -> list[dict]:
        body = json.loads(http_get(f"{self.vm_url}/api/v1/query", {"query": q}))
        return body.get("data", {}).get("result", [])

    def profile_query(self, q: str) -> list[dict]:
        """vm-wow for the character profile series (profile_vm_url)."""
        body = json.loads(http_get(f"{self.profile_vm_url}/api/v1/query", {"query": q}))
        return body.get("data", {}).get("result", [])

    def vm_range(self, q: str, start: datetime, end: datetime, step: str) -> list[dict]:
        body = json.loads(http_get(f"{self.vm_url}/api/v1/query_range",
                                   {"query": q, "start": str(int(start.timestamp())),
                                    "end": str(int(end.timestamp())), "step": step}))
        return body.get("data", {}).get("result", [])

    # The exporter's per-character series (wowstats.exporter.PROFILE_SERIES
    # and the found flag): everything of Battle.net's the static sites carry.
    PROFILE_SERIES = ("wow_character_profile", "wow_character_customization", "wow_character_equipped",
                      "wow_character_profile_refreshed_timestamp_seconds", "wow_character_profile_found",
                      "wow_character_item_level", "wow_character_equipped_stat",
                      "wow_character_talent_points", "wow_character_talent_rank", "wow_character_achievement",
                      "wow_character_snapshot")

    def profile_sel(self, name: str = "") -> str:
        """The selector of this person's profile series: the exporter labels
        each with the account whose character it is (wowstats.exporter), so
        a friend reads their own characters' Battle.net data only, never a
        series another person's character name happens to match (security
        review 0.6.0 S1)."""
        acct = f"account_id={json.dumps(str(self.account_id))}"
        return f"{{character={json.dumps(name)},{acct}}}" if name else f"{{{acct}}}"

    def snapshot_info(self, name: str = "", query=None) -> dict[tuple, tuple[str, bool]]:
        """{(account_id, character): (snapshot, fallback)}: the exporter's
        newest complete read of each character (wowstats.exporter.SNAPSHOT,
        the marker whose value is the latest, among the snapshots that carry
        a refreshed time). The readers' 2-day window rides out an exporter
        restart, but it also keeps a series the exporter dropped (a talent
        gone in a respec, an emptied slot) for 2 days: `latest` keeps one
        snapshot's series only (review 3).

        Complete: the exporter writes the refreshed time only once every
        part has answered since it started. After a restart, a first read
        that fails half way makes a newer snapshot with some parts and no
        refreshed time; reading it would show no Battle.net data at all
        until the next full read. The last complete one (still in the
        window, older than 30 days never: bnet_read_at) is read instead,
        `fallback` True so the answer says how old it is. No complete one at
        all: the newest, as before (bnet_read_at is then None)."""
        query = query or self.profile_query
        sel = self.profile_sel(name)
        marks: dict[tuple, list[tuple[float, str]]] = {}
        for r in query(f"last_over_time(wow_character_snapshot{sel}[2d])"):
            m = r.get("metric") or {}
            if m.get("snapshot"):
                marks.setdefault((m.get("account_id"), m.get("character")), []).append(
                    (float((r.get("value") or [0, 0])[1]), m["snapshot"]))
        if not marks:
            return {}
        done = {((m := r.get("metric") or {}).get("account_id"), m.get("character"), m.get("snapshot"))
                for r in query(f"last_over_time(wow_character_profile_refreshed_timestamp_seconds{sel}[2d])")}
        out = {}
        for k, cands in marks.items():
            newest = max(cands)
            whole = [c for c in cands if (*k, c[1]) in done]
            pick = max(whole) if whole else newest
            out[k] = (pick[1], pick != newest)
        return out

    def snapshots(self, name: str = "", query=None) -> dict[tuple, str]:
        """{(account_id, character): snapshot} to read (snapshot_info)."""
        return {k: sid for k, (sid, _f) in self.snapshot_info(name, query).items()}

    def bnet_state(self, name: str) -> dict | None:
        """{as_of: the read shown (epoch s), fallback: True when a newer
        read is incomplete and the last complete one is shown} or None when
        there is no Battle.net read to show (bnet_read_at)."""
        at = self.bnet_read_at(name)
        if at is None:
            return None
        info = self.snapshot_info(name)
        return {"as_of": at, "fallback": any(f for (_a, c), (_s, f) in info.items() if c == name)}

    @staticmethod
    def latest(rows: list[dict], snaps: dict[tuple, str]) -> list[dict]:
        """The rows of each character's newest snapshot (Store.snapshots). A
        character with no marker (an exporter older than the marker) keeps
        every row, as before."""
        out = []
        for r in rows:
            m = r.get("metric") or {}
            k = (m.get("account_id"), m.get("character"))
            if k not in snaps or m.get("snapshot") == snaps[k]:
                out.append(r)
        return out

    def profile_now(self, q: str, name: str = "", query=None) -> list[dict]:
        """`q` (a last_over_time over the profile series) on the exporter's
        newest snapshot only."""
        query = query or self.profile_query
        return self.latest(query(q), self.snapshots(name, query))

    def bnet_read_at(self, name: str) -> float | None:
        """When the exporter last read this character from Battle.net (its
        refreshed series), or None when it never did or that was 30 days ago
        or more: Blizzard's terms cap API data at 30 days, so a value older
        than that is never shown, whatever a store still holds."""
        rows = self.profile_now(
            f"last_over_time(wow_character_profile_refreshed_timestamp_seconds{self.profile_sel(name)}[2d])", name)
        if not rows:
            return None
        at = max(float((r.get("value") or [0, 0])[1]) for r in rows)
        if time.time() - at >= BNET_KEEP_S:
            return None
        self._bnet_seen[name] = at
        return at

    def bnet_last_read(self, name: str) -> float | None:
        """The last read bnet_read_at saw while vm-wow answered, for an
        answer made while it doesn't: a failed read is stale or an error,
        never "Battle.net hasn't read this character" (#92). None when this
        process never saw one (or it is 30 days old: Blizzard's cap)."""
        at = getattr(self, "_bnet_seen", {}).get(name)
        return at if at is not None and time.time() - at < BNET_KEEP_S else None

    def profile_fingerprint(self) -> str:
        """A hash of every profile, appearance and equipment value vm-wow
        holds now, as character_look reads them (last value over 2 days).
        It moves when the exporter's daily refresh changes or drops one, so
        the publisher rebuilds then: game events alone don't say so. "" when
        there is no vm-wow; raises SourceError when it doesn't answer."""
        if not self.vm_url:
            return ""
        rows = []
        for name in self.PROFILE_SERIES:
            for r in self.vm_query(f"last_over_time({name}{self.profile_sel()}[2d])"):
                rows.append([name, sorted((r.get("metric") or {}).items()), (r.get("value") or [None, None])[1]])
        rows.sort(key=lambda r: json.dumps(r))
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]

    def character_armory(self, name: str) -> dict:
        """What else the armory shows, from the exporter's gauges (gone with
        the rest of Battle.net's after 30 days): {"item_level": Blizzard's
        equipped item level or None}. Empty without vm-wow. No render: the
        page's picture is our own (wowstats.armory)."""
        out: dict = {"item_level": None}
        if not self.profile_vm_url:
            return out
        for r in self.profile_now(f"last_over_time(wow_character_item_level{self.profile_sel(name)}[2d])", name):
            if (r.get("metric") or {}).get("kind") == "equipped":
                out["item_level"] = float(r["value"][1])
        return out

    def character_equipment(self, name: str) -> dict:
        """What Battle.net says a character wears, from the exporter's gauges
        (read live, never kept: 23 days in vm-wow): {slot type ("back"):
        {"item_id", "stats": {"INTELLECT": 5, ...}}}, for scoring a worn
        random-suffix item on its real stats (wowstats.gear.worn_stats).
        Empty without vm-wow."""
        if not self.profile_vm_url or self.bnet_read_at(name) is None:
            return {}
        sel = self.profile_sel(name)
        snaps = self.snapshots(name)
        out: dict = {}
        for r in self.latest(self.profile_query(f"last_over_time(wow_character_equipped{sel}[2d])"), snaps):
            slot = (r.get("metric") or {}).get("slot")
            if slot:
                out[slot] = {"item_id": int(float(r["value"][1])), "stats": {}}
        for r in self.latest(self.profile_query(f"last_over_time(wow_character_equipped_stat{sel}[2d])"), snaps):
            m = r.get("metric") or {}
            if m.get("slot") in out and m.get("stat"):
                out[m["slot"]]["stats"][m["stat"]] = float(r["value"][1])
        return out

    def character_talents(self, name: str) -> dict | None:
        """Battle.net's talents for a character, from the exporter's gauges in
        vm-wow (23 days there, refreshed daily; never copied into a store
        that keeps them longer): {"trees": [{tree, points}] in the game's
        order, "ranks": [{spell, rank, name}], "refreshed"}, or None when
        vm-wow holds none."""
        if not self.profile_vm_url:
            return None
        snaps = self.snapshots(name)

        def q(query: str) -> list[dict]:
            return self.latest(self.profile_query(query), snaps)

        sel = self.profile_sel(name)
        pts = q(f"last_over_time(wow_character_talent_points{sel}[2d])")
        if not pts:
            return None
        rows = q(f"last_over_time(wow_character_talent_rank{sel}[2d])")
        when = q(f"last_over_time(wow_character_profile_refreshed_timestamp_seconds{sel}[2d])")
        trees = sorted(({"tree": r["metric"].get("tree"), "points": int(float(r["value"][1])),
                         "order": int(r["metric"].get("order") or 0)} for r in pts if r.get("metric", {}).get("tree")),
                       key=lambda t: t["order"])
        ranks = sorted(({"spell": int(r["metric"]["spell_id"]), "rank": int(float(r["value"][1])),
                         "name": r["metric"].get("talent") or None}
                        for r in rows if str(r.get("metric", {}).get("spell_id", "")).isdigit()),
                       key=lambda x: x["spell"])
        return {"trees": [{"tree": t["tree"], "points": t["points"]} for t in trees], "ranks": ranks,
                "refreshed": iso(datetime.fromtimestamp(float(when[0]["value"][1]), UTC)) if when else None}

    def profile_range(self, q: str, start: datetime, end: datetime, step: str) -> list[dict]:
        """vm-wow over time (query_range) for the character profile series."""
        body = json.loads(http_get(f"{self.profile_vm_url}/api/v1/query_range",
                                   {"query": q, "start": str(int(start.timestamp())),
                                    "end": str(int(end.timestamp())), "step": step}))
        return body.get("data", {}).get("result", [])

    # The talent history's window: vm-wow keeps 23 days (Blizzard's 30-day
    # cap, less VictoriaMetrics' merge delay), so 3 weeks are always whole.
    TALENT_HISTORY_DAYS = 21

    def talent_readings(self, name: str, days: int = TALENT_HISTORY_DAYS,
                        now: datetime | None = None) -> list[dict]:
        """The talents as vm-wow held them over the last `days`, one reading
        per UTC day (the day's last), oldest first: [{"t", "trees": [{tree,
        points, order}], "ranks": {key: {spell, name, rank}}}]. Read live from
        the exporter's own series and never kept (owner, 2026-11-20): the
        exporter reads /specializations once a day and pushes the build every
        minute, so the series themselves are the history.

        Sampled hourly over a 3-minute window (the push is every minute): a
        respec's dropped talents leave their series at once, so a sample is
        the build at that moment. One taken while a read lands can hold both
        builds; a day's reading is its last sample whose ranks add up to its
        points (Classic: every point is one rank), else its last sample. An
        hour with nothing (the exporter or vm-wow down) is no reading, never
        an empty build."""
        if not self.profile_vm_url:
            return []
        end = now or datetime.now(UTC)
        start = end - timedelta(days=days)
        sel = self.profile_sel(name)
        pts = self.profile_range(f"last_over_time(wow_character_talent_points{sel}[3m])", start, end, "1h")
        if not pts:
            return []
        rks = self.profile_range(f"last_over_time(wow_character_talent_rank{sel}[3m])", start, end, "1h")
        at: dict[int, dict] = {}
        for r in pts:
            m = r.get("metric") or {}
            if not m.get("tree"):
                continue
            for ts, v in r.get("values") or []:
                s = at.setdefault(int(float(ts)), {"trees": {}, "ranks": {}})
                s["trees"][m["tree"]] = {"tree": m["tree"], "points": int(float(v)),
                                         "order": int(m.get("order") or 0)}
        for r in rks:
            m = r.get("metric") or {}
            spell = str(m.get("spell_id") or "")
            if not spell.isdigit():
                continue
            # By the talent's name: a classic talent's ranks are different
            # spells, so a rank gained can change the series' spell id.
            key = (m.get("talent") or "").strip().lower() or spell
            for ts, v in r.get("values") or []:
                s = at.get(int(float(ts)))
                if s is None:
                    continue  # no points then: not a whole read
                rank = int(float(v))
                if rank > s["ranks"].get(key, {}).get("rank", 0):
                    s["ranks"][key] = {"spell": int(spell), "name": m.get("talent") or None, "rank": rank}
        last: dict[str, tuple[int, dict, bool]] = {}
        for ts in sorted(at):
            s = at[ts]
            day = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")
            whole = sum(t["points"] for t in s["trees"].values()) == sum(x["rank"] for x in s["ranks"].values())
            have = last.get(day)
            if have is None or whole or not have[2]:
                last[day] = (ts, s, whole)
        return [{"t": iso(datetime.fromtimestamp(ts, UTC)),
                 "trees": sorted(s["trees"].values(), key=lambda t: t["order"]), "ranks": s["ranks"]}
                for _, (ts, s, _w) in sorted(last.items())]

    def character_profile(self, name: str) -> dict | None:
        """Sex and guild as Battle.net's profile gives them (owner,
        2026-11-20: the API, not an addon), from the exporter's gauges in
        vm-wow (23 days, refreshed every 2 hours): {"sex": "male"|"female"|None,
        "guild": name or None, "guild_known": whether Battle.net said (a
        name, or no guild), "class", "race", "faction", "level" (for what
        the addons didn't send), "refreshed"}, or None when vm-wow holds no
        profile for the character read in the last 30 days (no Battle.net
        client, or the API doesn't know it): the page then says neither. A
        profile series from an exporter older than `in_guild` leaves the
        guild unknown, one older than `class_id` the class."""
        if not self.profile_vm_url:
            return None
        sel = self.profile_sel(name)
        prof = self.profile_now(f"last_over_time(wow_character_profile{sel}[2d])", name)
        if not prof:
            return None
        best = max(prof, key=lambda r: float((r.get("value") or [0, 0])[1]))
        m = best.get("metric") or {}
        when = self.bnet_read_at(name)
        if when is None:
            return None  # never read, or 30 days ago: Blizzard's cap
        sex = m.get("gender") if m.get("gender") in ("male", "female") else None
        guild = m.get("guild") or None
        level = float((best.get("value") or [0, 0])[1])
        return {"sex": sex, "guild": guild, "guild_known": bool(guild) or m.get("in_guild") == "false",
                # For what the addons didn't send (wowstats.views.fill_from_battlenet).
                "class": BNET_CLASSES.get(m.get("class_id") or ""), "race": BNET_RACES.get(m.get("race_id") or ""),
                "faction": (m.get("faction") or "").title() or None, "level": int(level) if level > 0 else None,
                "refreshed": iso(datetime.fromtimestamp(when, UTC))}

    def account_achievements(self) -> dict[int, str] | None:
        """Every achievement any of this person's characters completed, as
        Battle.net's /achievements gives them (the exporter's gauges in
        vm-wow, 23 days): {achievement id: when}, the earliest of the
        characters'. None without vm-wow, or when the exporter never read
        /achievements for the account (no wow_character_achievements_read:
        an older exporter, or the API failed): Legacy points then read
        "unknown", never 0. {} only when Battle.net answered with none."""
        if not self.profile_vm_url:
            return None
        sel = self.profile_sel()
        snaps = self.snapshots()
        if not self.latest(self.profile_query(f"last_over_time(wow_character_achievements_read{sel}[2d])"), snaps):
            return None
        out: dict[int, float] = {}
        for r in self.latest(self.profile_query(f"last_over_time(wow_character_achievement{sel}[2d])"), snaps):
            aid = str((r.get("metric") or {}).get("achievement_id") or "")
            if aid.isdigit():
                t = float(r["value"][1])
                out[int(aid)] = min(out.get(int(aid), t), t)
        return {k: iso(datetime.fromtimestamp(v, UTC)) for k, v in out.items()}

    def character_look(self, name: str) -> dict | None:
        """The look Battle.net's profile API gives for a character, from the
        exporter's gauges in vm-wow (kept 23 days there, refreshed daily):
        {"options": [{optionId, choiceId}], "gender", "refreshed"}, or None
        when the API doesn't know the character (or there is no vm-wow)."""
        if not self.vm_url:
            return None
        sel = self.profile_sel(name)
        snaps = self.snapshots(name, self.vm_query)

        def q(query: str) -> list[dict]:
            return self.latest(self.vm_query(query), snaps)

        opts = q(f"last_over_time(wow_character_customization{sel}[2d])")
        if not opts:
            return None
        prof = q(f"last_over_time(wow_character_profile{sel}[2d])")
        when = q(f"last_over_time(wow_character_profile_refreshed_timestamp_seconds{sel}[2d])")
        out = {"source": "battle.net", "gender": None, "refreshed": None,
               "options": sorted(({"optionId": int(r["metric"]["option_id"]), "choiceId": int(float(r["value"][1]))}
                                  for r in opts if r.get("metric", {}).get("option_id")),
                                 key=lambda o: o["optionId"])}
        if prof:
            out["gender"] = prof[0].get("metric", {}).get("gender") or None
        if when:
            out["refreshed"] = iso(datetime.fromtimestamp(float(when[0]["value"][1]), UTC))
        return out

