"""What an answer stood on: one small value per source (review 4).

An empty list or a 0 reads as "never happened" unless the answer says the
source behind it was off. Every character answer (the page's /api/char, the
journal, each MCP tool on one character, query_metric and play_sql) carries
one `coverage`:

    {source: {"state": ..., "as_of": iso?, "gaps": [...]?, "fix": str?}}

Sources: `recorder` (Polestar's Recorder: sessions, zones, deaths, loot),
`polestar` (the level splits and logout sheets), `gear` (Syndicator's gear
and bags), `quests` (Questie or Altoholic: quests done before installing),
`prices` (Auctionator, the account's) and `battlenet` (the exporter's read).

States:
- complete: it arrived, and nothing stored shows a hole in it;
- partial: it arrived with a hole: play the Recorder missed (`gaps`), the
  Recorder switched off now, Battle.net shown from an older complete read;
- unavailable: nothing arrived (or no exporter here); `fix` is the one
  thing that brings it (wowstats.missing's list, the same the pages show);
- unknown: the store didn't answer, so nothing can be said.

`gear` and `polestar` also carry `parts`: {part: iso} for each part of
their snapshots (the equipped `items`, the `bags`, the `bank`; the logout
sheet's `money_copper`), each the time of the snapshot it was read from.
A newer snapshot that lacks a part leaves it to an older one, and the part
keeps that older time, so an answer can say "the bank as of a week ago"
(review 6). A part no snapshot carries is left out: unknown.

Everything is derived from what is stored, never assumed: `as_of` is the
newest record, a gap is a finished level whose Polestar split (the game's
played time) is longer than the Recorder's sessions in it (without Polestar
the Recorder reads complete, as far as anyone can tell). Nothing new is
stored.
"""

from __future__ import annotations

from datetime import UTC, datetime

from wowstats import missing
from wowstats.stores import opt_num, parse_t, split_sessions

# Source -> the kind in steps.json's list (wowstats.missing): its signals
# date the source, its fix is the one action that brings it.
KINDS = {"recorder": "play", "polestar": "levels", "gear": "bags", "quests": "quests", "prices": "prices"}
STATES = ("complete", "partial", "unavailable", "unknown")
SOURCES = (*KINDS, "battlenet")

# Smaller differences between a level's played time and the Recorder's are
# its own rounding (a chunk's last seconds, a crash before the logout).
GAP_MIN_S = 15 * 60
GAP_SHARE = 0.10  # nor under a tenth of the level's time
RECORDER_OFF_FIX = "Type /psrec on once in game, then /reload."
BNET_NEVER = "Battle.net reads each character within 2 hours of its first upload."
BNET_DOWN = "Battle.net's store didn't answer: this is its last read. It comes back on its own."


def entry(state: str, as_of=None, gaps: list | None = None, fix: str | None = None) -> dict:
    out: dict = {"state": state}
    if as_of:
        out["as_of"] = as_of
    if gaps:
        out["gaps"] = gaps
    if fix:
        out["fix"] = fix
    return out


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(v) -> float | None:
    t = parse_t(v)
    return None if t.year == 1 else t.timestamp()


# ---------------------------------------------------------------- Recorder gaps


def recorder_gaps(spans: list[tuple[float, float]], levels: list[dict]) -> list[dict]:
    """Play the Recorder missed, oldest first: [{level, from, to, off_s}].

    Polestar's split for a level is the game's own played time in it, and
    its `_time` the ding that ended it (docs/contract.md), so the level ran
    from the previous split's ding to this one's. The Recorder's sessions
    (`spans`, unix seconds) cover part of that window; the rest, when it is
    over GAP_MIN_S and GAP_SHARE of the level's time, is play with the
    Recorder off: an evening it never saw, or the part of one after a
    /psrec off. Exact, no estimate: both times come from the game. Only
    whole levels the Recorder was already on for (a `partial` split, or a
    level that began before its first record, is the journal's "Recorder on
    since" already); the level in play has no split yet."""
    if not spans:
        return []
    first = spans[0][0]
    rows = sorted(((t, n, r) for r in levels if (t := _ts(r.get("_time"))) is not None
                   and (n := opt_num(r.get("level"))) is not None), key=lambda x: x[0])
    out = []
    for (t0, n0, _a), (t1, n1, b) in zip(rows, rows[1:], strict=False):
        if n1 != n0 + 1 or t0 < first or str(b.get("partial")).lower() == "true":
            continue
        played = opt_num(b.get("seconds")) or 0
        seen = sum(max(0.0, min(e, t1) - max(s, t0)) for s, e in spans)
        off = played - seen
        if off >= GAP_MIN_S and off >= GAP_SHARE * played:
            out.append({"level": int(n1), "from": _iso(t0), "to": _iso(t1), "off_s": int(off)})
    return out


def attach(gaps: list[dict], spans: list[tuple[float, float]]) -> dict[int, dict]:
    """{index of a span: {off_s, levels}}: each gap goes to the newest
    Recorder session inside its level's window (the one a player reads
    first), so a gap is said once, never on every session of a long level;
    two levels' gaps on one session add up."""
    out: dict[int, dict] = {}
    for g in gaps:
        a, b = _ts(g["from"]) or 0, _ts(g["to"]) or 0
        inside = [i for i, (s, e) in enumerate(spans) if s < b and a < e]
        if inside:
            m = out.setdefault(max(inside, key=lambda i: spans[i][0]), {"off_s": 0, "levels": []})
            m["off_s"] += g["off_s"]
            m["levels"].append(g["level"])
    return out


# ---------------------------------------------------------------- the value


def _newest(times: dict, sigs) -> str | None:
    got = [times[s] for s in sigs if times.get(s)]
    return max(got, key=parse_t) if got else None


def build(snap: dict, char: str, gaps: list[dict] | None = None, bnet: dict | str | None = None) -> dict:
    """The coverage of one character from missing.snapshot (what arrived,
    when, the setup report), the Recorder's gaps (recorder_gaps) and the
    Battle.net state: a dict from Store.bnet_state ({as_of epoch, fallback}),
    {} when the exporter never read the character, None when this server has
    no exporter, {error, as_of} or "unknown" when vm-wow didn't answer
    (bnet_of)."""
    who = missing.norm(char)
    gone = {m["id"]: m for m in missing.for_char(snap, char)}
    last = snap.get("last") or {}
    mine, acct = last.get(who) or {}, last.get("") or {}
    rep = snap.get("setup")
    out: dict = {}
    for src, kid in KINDS.items():
        kind = next((k for k in missing.kinds() if k["id"] == kid), None)
        if kind is None:
            continue
        if kid in gone or "nothing" in gone:
            out[src] = entry("unavailable", fix=(gone.get(kid) or gone.get("nothing"))["fix"])
            continue
        at = _newest(acct if kind.get("scope") == "account" else mine, kind["signals"])
        out[src] = entry("complete", at)
    rec = out.get("recorder")
    if rec and rec["state"] == "complete":
        if gaps:
            out["recorder"] = entry("partial", rec.get("as_of"), gaps, RECORDER_OFF_FIX)
        elif rep and rep.get("recorder_on") is False:
            out["recorder"] = entry("partial", rec.get("as_of"), fix=RECORDER_OFF_FIX)
    out["battlenet"] = battlenet(bnet)
    return out


def battlenet(bnet: dict | str | None) -> dict:
    if bnet == "unknown":
        return entry("unknown")
    if isinstance(bnet, dict) and bnet.get("error"):  # vm-wow failed after a read this process saw
        return entry("unavailable", _iso(float(bnet["as_of"])), fix=BNET_DOWN)
    if bnet is None:
        return entry("unavailable")  # no exporter here: nothing a player can do about it
    if not bnet:
        return entry("unavailable", fix=BNET_NEVER)
    at = _iso(float(bnet["as_of"]))
    return entry("partial" if bnet.get("fallback") else "complete", at)


def unknown() -> dict:
    """The store didn't answer: nothing can be said about any source."""
    return {s: entry("unknown") for s in SOURCES}


def bnet_of(store, char: str) -> dict | str | None:
    """build()'s `bnet` from a Store: None without vm-wow; when it doesn't
    answer, {error, as_of} of the last read this process saw, else
    "unknown". Never {} (never read) for an error (#92)."""
    from wowstats.stores import SourceError

    if not getattr(store, "profile_vm_url", None):
        return None
    try:
        return store.bnet_state(char) or {}
    except (SourceError, OSError, ValueError):
        last = store.bnet_last_read(char) if hasattr(store, "bnet_last_read") else None
        return {"error": True, "as_of": last} if last is not None else "unknown"


# ---------------------------------------------------------------- snapshot parts


def parts_of(rows: list[dict]) -> dict[str, dict[str, str]]:
    """{source: {part: iso}}: each snapshot part's own observation time
    (stores.part_rows) among wow-gear, wow-bags and wow-character rows."""
    from wowstats import gear, stores

    def times(feed: str, parts: tuple[str, ...]) -> dict[str, str]:
        mine = [r for r in rows if r.get("feed") == feed and r.get("kind") != "played"]
        return {p: _iso(parse_t(r.get("_time")).timestamp()) for p, r in stores.part_rows(mine, parts).items()}

    return {"gear": {**times("wow-gear", gear.SHEET_PARTS), **times("wow-bags", gear.BAGS_PARTS)},
            "polestar": times("wow-character", stores.SHEET_PARTS)}


def read_parts(read, cf: str) -> dict[str, dict[str, str]]:
    """parts_of() from a store: `read(query)` answers LogsQL after the
    stream filter, `cf` the character filter (the rows in force only,
    stores.in_force)."""
    from wowstats import gear, stores

    rows = [{**r, "feed": "wow-gear"} for r in gear.sheet_rows(read, cf, "_time, at_basis, source_id, items")]
    rows += [{**r, "feed": "wow-character"} for r in stores.sheet_rows(read, cf)]
    return parts_of(rows + gear.held_rows(read, cf))


def with_parts(cov: dict, parts: dict[str, dict[str, str]]) -> dict:
    """`cov` with each source's `parts` (parts_of), where it has any."""
    for src, got in parts.items():
        if got and src in cov:
            cov[src] = {**cov[src], "parts": got}
    return cov


# ---------------------------------------------------------------- from raw events


def from_rows(rows: list[dict], char: str) -> dict:
    """The coverage of one character's raw events (the analysis's prep
    input: every feed but other players, prices and combat log lines). The
    same rules as build(), without a setup report (prep doesn't read it),
    and without `battlenet` or `prices`: the prepared tables hold neither."""
    agg: dict[tuple, dict] = {}
    for r in rows:
        k = (r.get("char"), r.get("feed"), r.get("source"), r.get("kind") if r.get("kind") == "played" else None)
        a = agg.setdefault(k, {"char": k[0], "feed": k[1], "source": k[2], "kind": k[3], "n": 0, "last": None})
        a["n"] += 1
        t = r.get("_time")
        if t and (a["last"] is None or parse_t(t) > parse_t(a["last"])):
            a["last"] = t
    last: dict = {}
    per, acct = missing.arrived(lambda _q: list(agg.values()), last)
    snap = {"per": {c: sorted(s) for c, s in per.items()}, "acct": sorted(acct), "setup": None, "last": last}
    rec = [{"n": 1, "t0": r["_time"], "t1": r["_time"]} for r in rows if r.get("feed") == "wow-recorder"
           and r.get("_time")]
    levels = [r for r in rows if r.get("feed") == "wow-level"]
    out = with_parts(build(snap, char, recorder_gaps(split_sessions(rec), levels)), parts_of(rows))
    out.pop("battlenet", None)
    out.pop("prices", None)
    return out

