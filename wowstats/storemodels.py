"""The tools' and pages' reads of the store models (semantic.yaml
`store_models`): other players, the player's price scans, the talent changes
and the groups in a dungeon run, which live in the store rather than the
analysis' prep (owner, 2026-11-22: "use the semantic model all the time").

Each function takes the rows its caller fetched from the store for one answer
(the fetch, its filters and its time range stay the caller's), shapes them
into the model's relation and asks wowstats.analysis.semantic.read: every
sum, count, latest, low and change is that model's metric, its contract
included. Nothing is kept: the relation lives for the call (read).
"""

from __future__ import annotations

import json

from wowstats.stores import num, opt_num, parse_t

GROUPED = ("grouped", "dungeon")


def _read(model: str, rows: list[dict], q: dict) -> list[dict]:
    from wowstats.analysis import semantic

    return semantic.read(model, rows, q)["rows"]


def _text(v) -> str | None:
    """A list as the relation keeps it (JSON text); a JSON string as is."""
    if v is None or isinstance(v, str):
        return v
    return json.dumps(v)


# ---------------------------------------------------------------- players


def players(days: list[dict], info: list[dict], keep) -> dict[str, dict]:
    """Per other player (the `players` model): days seen, days and sightings
    in a group, sightings, the contexts with their sightings, the days seen
    and grouped, last seen and level, and the classes, races and zones as the
    store listed them (JSON text). `days`: the store's (player, context,
    day) rows with their sightings `n`; `info`: its per-player summary;
    `keep(player)`: whether the player counts (not the character itself, a
    name filter)."""
    meta = {r.get("player"): r for r in info}
    rel = []
    for r in days:
        p = r.get("player")
        if not p or not keep(p):
            continue
        m = meta.get(p, {})
        rel.append({"player": p, "context": r.get("context") or "seen", "day": (r.get("_time") or "")[:10],
                    "sightings": int(num(r.get("n"))), "last_seen": m.get("last_seen"),
                    "level": opt_num(m.get("level")), "classes": _text(m.get("classes")),
                    "races": _text(m.get("races")), "zones": _text(m.get("zones"))})
    out: dict[str, dict] = {}
    for r in _read("players", rel, {"metrics": ["player_days", "player_group_days", "player_grouped_sightings",
                                                "player_sightings", "player_last_seen", "player_level"],
                                    "group_by": ["player", "classes", "races", "zones"]}):
        out[r["player"]] = {"days": r["player_days"], "group_days_n": r["player_group_days"],
                            "grouped": r["player_grouped_sightings"] or 0, "sightings": r["player_sightings"] or 0,
                            "last_seen": r["player_last_seen"], "level": r["player_level"],
                            "classes": r["classes"], "races": r["races"], "zones": r["zones"],
                            "contexts": {}, "day_list": [], "group_days": []}
    for r in _read("players", rel, {"metrics": ["player_sightings"], "group_by": ["player", "context"],
                                    "order_by": ["player", "context"]}):
        out[r["player"]]["contexts"][r["context"]] = r["player_sightings"] or 0
    for r in _read("players", rel, {"metrics": ["player_grouped_sightings"], "group_by": ["player", "day"],
                                    "order_by": ["player", "day"]}):
        d = out[r["player"]]
        d["day_list"].append(r["day"])
        if r["player_grouped_sightings"]:
            d["group_days"].append(r["day"])
    return out


# ---------------------------------------------------------------- prices


def prices(rows: list[dict]) -> dict[str, dict]:
    """Per item (the `prices` model) over the scans `rows` (one per item and
    scan day, the caller's newest revision: _time, item_key, item_name,
    item_id, price_copper, price_high_copper; a row without a price left
    out): its name, scan days, the newest day's low and high and time, the
    lowest and highest low, and the change from the first day (None with one
    scan day: min_evidence)."""
    rel = []
    for r in rows:
        key = str(r.get("item_id") or r.get("item_key") or "")
        p = opt_num(r.get("price_copper"))
        if not key or p is None:
            continue
        rel.append({"item_key": key, "item": r.get("item_name"), "item_id": opt_num(r.get("item_id")),
                    "t": parse_t(r.get("_time")).timestamp(), "price_copper": p,
                    "price_high_copper": opt_num(r.get("price_high_copper"))})
    return {r["item_key"]: r for r in _read("prices", rel, {
        "metrics": ["price_item", "price_scan_days", "price_latest_copper", "price_latest_high_copper",
                    "price_latest_t", "price_min_copper", "price_max_copper", "price_change_copper"],
        "group_by": ["item_key", "item_id"]})}


# ---------------------------------------------------------------- talents


def talent_changes(changes: list[dict]) -> list[dict]:
    """wowstats.talents.history's changes read back through the
    `talent_changes` model, newest first: each change's day, time, trees
    before and after, talents gained and dropped."""
    rel = [{"t": c.get("t"), "date": c.get("date"), "before": json.dumps(c.get("before")),
            "after": json.dumps(c.get("after")), "gained": json.dumps(c.get("gained")),
            "dropped": json.dumps(c.get("dropped")),
            "ranks_gained": sum((g.get("to") or 0) - (g.get("from") or 0) for g in c.get("gained") or []),
            "ranks_dropped": sum((d.get("from") or 0) - (d.get("to") or 0) for d in c.get("dropped") or [])}
           for c in changes]
    got = _read("talent_changes", rel, {"metrics": ["talent_change_count"],
                                        "group_by": ["change", "date", "before", "after", "gained", "dropped"],
                                        "order_by": ["-change"]})
    return [{"date": r["date"], "t": r["change"], "before": json.loads(r["before"]), "after": json.loads(r["after"]),
             "gained": json.loads(r["gained"]), "dropped": json.loads(r["dropped"])} for r in got]


# ---------------------------------------------------------------- dungeon runs


def run_groups(runs: list[dict], groups: list[dict], players: list[dict] | None = None,
               own: str | None = None) -> list[dict]:
    """Per dungeon run (the `run_groups` model), newest first: party size,
    bosses won and lost, deaths, and who was in the party. `runs`: wow-dungeon
    rows, oldest first; `groups`: the Recorder's group, encounter and death
    records around them, oldest first; `players`: wow-player rows around
    them (party). A run holds the group record in force when it began, the
    group records inside it and the encounters and deaths inside it (from 5 s
    before its start)."""
    from datetime import timedelta

    rel, spans = [], {}
    for r in runs:
        t0 = parse_t(r.get("_time"))
        t1 = t0 + timedelta(seconds=num(r.get("seconds")))
        spans[r.get("_time")] = (t0, t1)
        base = {"run": r.get("_time"), "dungeon": r.get("name"), "level": opt_num(r.get("level")),
                "seconds": int(num(r.get("seconds"))), "xp": int(num(r.get("xp")))}
        inside = [x for x in groups if t0 - timedelta(seconds=5) <= parse_t(x.get("_time")) <= t1]
        prior = [x for x in groups if x.get("kind") == "group" and parse_t(x.get("_time")) < t0][-1:]
        rel.append({**base, "kind": None, "size": None, "success": None})
        for x in prior + inside:
            if x.get("kind") == "group" and not x.get("size"):
                continue
            rel.append({**base, "kind": x.get("kind"), "size": int(num(x.get("size"))) if x.get("size") else None,
                        "success": x.get("success")})
    got = _read("run_groups", rel, {"metrics": ["run_party_size", "run_bosses", "run_wipes", "run_deaths"],
                                    "group_by": ["run", "dungeon", "level", "seconds", "xp"], "order_by": ["-run"]})
    sizes = [x for x in groups if x.get("kind") == "group"]
    out = []
    for r in got:
        size = r["run_party_size"]
        t0, t1 = spans[r["run"]]
        party = run_party(t0, t1, sizes, players or [], own)
        # A raid's size counts the character too (Polestar_Recorder World.lua recordGroup).
        held = [x for x in sizes if parse_t(x.get("_time")) < t0][-1:] + \
            [x for x in sizes if t0 <= parse_t(x.get("_time")) <= t1]
        raid = any(str(x.get("raid")).lower() in ("true", "1") for x in held)
        others = None if size is None else size - 1 if raid else size
        out.append({"t": r["run"], "name": r["dungeon"], "level": opt_num(r["level"]), "seconds": r["seconds"],
                    "xp": r["xp"], "party_size": size, "bosses": r["run_bosses"], "wipes": r["run_wipes"],
                    "deaths": r["run_deaths"], "party": party, "names_known": party_known(party, others)})
    return out


# A party with no Recorder group record to date it: the names from this long
# before the run on (parties form at the entrance, or in town first).
PARTY_LEAD_S = 1800


def run_party(t0, t1, sizes: list[dict], players: list[dict], own: str | None = None) -> list[dict]:
    """Who was in the party of the run from t0 to t1: the groupmates
    (wow-player, context grouped or dungeon) named from the start of the
    stretch the character stayed grouped in (the Recorder's group records:
    size above 0 without a break) to the run's end. ForeverDB_Helper names
    each groupmate once a day, so a second run with the same party finds the
    names from the first. Without group records, from PARTY_LEAD_S before.
    Each player once, the newest reading: name, level, class, race, role."""
    from datetime import timedelta

    before = [x for x in sizes if parse_t(x.get("_time")) <= t0]
    start = t0 - timedelta(seconds=PARTY_LEAD_S)
    if before:
        if num(before[-1].get("size")) > 0:
            i = len(before) - 1
            while i > 0 and num(before[i - 1].get("size")) > 0:
                i -= 1
            start = parse_t(before[i].get("_time"))
        else:  # alone when it began: the party formed inside
            start = t0
    seen: dict[str, dict] = {}
    for p in players:
        name = p.get("player")
        if not name or name == own or p.get("context") not in GROUPED:
            continue
        if start - timedelta(seconds=60) <= parse_t(p.get("_time")) <= t1:
            seen[name] = {"player": name, "level": opt_num(p.get("level")), "class": p.get("class") or None,
                          "race": p.get("race") or None, "role": p.get("role") or None}
    return sorted(seen.values(), key=lambda p: p["player"])


def party_known(party: list[dict], size) -> str | None:
    """"N of M names known" when the Recorder's party size says some are
    missing (`size`: the others in the party). Only beside names: with
    none, every run before ForeverDB_Helper would say "0 of 4"."""
    if not party or size is None or len(party) >= size:
        return None  # no names at all: the roster note says why, once
    return f"{len(party)} of {int(size)} names known"
