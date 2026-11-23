"""The session journal: one entry per play session.

A session ends when the Recorder writes nothing for more than GAP_S (330 s):
the rule of the Overview, the timeline and the semantic layer's sessions
(wowstats.stores.split_sessions). A /reload or a quick relog inside that
doesn't end one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from wowstats.stores import SESSION_GAP_S, num, opt_num, parse_t, split_sessions, zone_time

GAP_S = SESSION_GAP_S


def _ts(s) -> float | None:
    t = parse_t(s)
    return None if t.year == 1 else t.timestamp()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(minutes: list[dict], xp: list[dict], events: list[dict], dungeons: list[dict],
          players: list[dict], snaps: list[dict], own: str = "") -> list[dict]:
    """Newest session first. `minutes`: the Recorder's SESSION_BUCKETS rows.
    `players` and gold are kept here; the public view drops them
    (wowstats.redact)."""
    spans = split_sessions(minutes)
    if not spans:
        return []
    sessions = [{"start": a, "end": b, "xp": 0, "levels": [], "level_start": None, "level_end": None,
                 "zones": {}, "quests": [], "deaths": {}, "dungeons": [], "players": {}} for a, b in spans]

    def find(t: float | None):
        if t is None:
            return None
        # Bisect would do; a play history is a few hundred sessions at most.
        for s in sessions:
            if s["start"] - 1 <= t < s["end"] + 1:
                return s
        return None

    for r in xp:  # one row per XP reading that gained any (prep.xp_gains)
        s = find(_ts(r.get("_time")))
        if s:
            s["xp"] += int(num(r.get("xp")))
    for r in sorted(events, key=lambda e: parse_t(e.get("_time"))):
        t = _ts(r.get("_time"))
        s = find(t)
        if not s:
            continue
        kind = r.get("kind")
        lvl = opt_num(r.get("level"))
        if kind in ("session", "level") and lvl is not None:
            if kind == "session" and s["level_start"] is None:
                s["level_start"] = lvl
            s["level_end"] = max(s["level_end"] or lvl, lvl)
        if kind == "level" and lvl is not None:
            s["levels"].append({"level": lvl, "t": r.get("_time")})
            if s["level_start"] is None:
                s["level_start"] = lvl - 1
        elif kind == "turnin":
            s["quests"].append({"quest_id": opt_num(r.get("quest_id")), "name": r.get("quest_name") or None,
                                "zone": r.get("zone") or None, "t": r.get("_time")})
        elif kind == "death":
            zone = r.get("zone") or "Unknown"
            s["deaths"][zone] = s["deaths"].get(zone, 0) + 1
    # A zone's time runs to the next zone record or the session's end
    # (stores.zone_time: from the records' times, not the shipper's field).
    for r in zone_time([e for e in events if e.get("kind") == "zone"], spans):
        s = find(_ts(r.get("_time")))
        if s and r.get("zone"):
            s["zones"][r["zone"]] = s["zones"].get(r["zone"], 0.0) + r["secs"]
    for r in dungeons:
        s = find(_ts(r.get("_time")))
        if s:
            s["dungeons"].append({"name": r.get("name"), "level": opt_num(r.get("level")),
                                  "seconds": opt_num(r.get("seconds")), "xp": opt_num(r.get("xp")),
                                  "t": r.get("_time")})
    for r in players:
        p = r.get("player")
        s = find(_ts(r.get("_time")))
        if not s or not p or p.replace(" ", "-") == own:
            continue
        d = s["players"].setdefault(p, {"player": p, "sightings": 0, "grouped": False})
        d["sightings"] += 1
        if r.get("context") in ("grouped", "dungeon"):
            d["grouped"] = True

    # Gold: the account snapshot is saved at logout, so the one right after a
    # session ends against the last one before it started.
    snap_t = []
    for r in snaps:
        t = _ts(r.get("_time"))
        if t is not None and opt_num(r.get("money_copper")) is not None:
            snap_t.append((t, r))
    snap_t.sort(key=lambda x: x[0])
    out = []
    starts = sorted(x["start"] for x in sessions)
    for s in sessions:
        before = [r for t, r in snap_t if t <= s["start"] + 60]
        # Money doesn't move while logged out: any snapshot up to the next
        # session's start closes this one.
        nxt = next((t for t in starts if t > s["start"]), None)
        until = max(s["end"] + 1800, nxt + 60) if nxt is not None else float("inf")
        after = [r for t, r in snap_t if s["end"] - 300 <= t <= until]
        gold = None
        if before and after:
            gold = int(num(after[0]["money_copper"]) - num(before[-1]["money_copper"]))
        if s["level_start"] is not None and s["level_end"] is None:
            s["level_end"] = s["level_start"]
        out.append({
            "start": _iso(s["start"]),
            "end": _iso(s["end"]),
            "played_s": int(s["end"] - s["start"]),
            "level_start": s["level_start"],
            "level_end": s["level_end"],
            "levels_gained": len(s["levels"]),
            "dings": s["levels"],
            "xp": s["xp"],
            "zones": [{"zone": z, "secs": int(v)} for z, v in sorted(s["zones"].items(), key=lambda kv: -kv[1])],
            "quests": s["quests"],
            "deaths": {"total": sum(s["deaths"].values()),
                       "by_zone": [{"zone": z, "n": n} for z, n in sorted(s["deaths"].items(), key=lambda kv: -kv[1])]},
            "dungeons": s["dungeons"],
            "players": sorted(s["players"].values(), key=lambda p: (-p["grouped"], -p["sightings"], p["player"])),
            "gold_delta_copper": gold,
        })
    out.reverse()
    return out


# ---------------------------------------------------------------- without the Recorder


def played_between(levels: list[dict], a: tuple, b: tuple) -> float | None:
    """Played seconds from (level, xp) `a` to `b`, from Polestar's level
    splits ({level, seconds, xp, partial}): each level crossed at its own
    seconds per XP (the nearest lower measured level's for one not
    measured). None when no level is measured, `b` isn't later, or a level
    crossed has no known size."""
    (la, xa), (lb, xb) = a, b
    if la is None or lb is None or (lb, xb or 0) <= (la, xa or 0):
        return None
    per = {int(r["level"]): r["seconds"] / r["xp"] for r in levels if (r.get("seconds") or 0) > 0 and
           (r.get("xp") or 0) > 0}
    if not per:
        return None
    need = {int(r["level"]): r["xp"] for r in levels if r.get("xp") and not r.get("partial")}
    total = 0.0
    for lv in range(int(la), int(lb) + 1):
        rate = per.get(lv) or per[max((k for k in per if k <= lv), default=min(per))]
        lo = (xa or 0) if lv == la else 0
        hi = (xb or 0) if lv == lb else need.get(lv)
        if hi is None:
            return None
        total += max(0, hi - lo) * rate
    return total


def by_logout(snaps: list[dict], levels: list[dict], dungeons: list[dict], until: float | None = None) -> list[dict]:
    """Newest first: one entry per logout sheet (Polestar's, feed
    wow-character) that moved the level or the gold, for a character with
    no Recorder events. The time played since the sheet before comes from
    the level splits (played_between), so `start` is an estimate: the
    logout minus that time; `estimated` says so. Zones, quests and deaths
    need the Recorder and stay empty; dungeon runs and dings are in.
    `until`: the Recorder's first record, for a character that switched it
    on later; only the logouts before it, so no play is counted twice."""
    rows = []
    for r in snaps:
        t = _ts(r.get("_time"))
        if t is not None and opt_num(r.get("level")) is not None:
            rows.append((t, r))
    rows.sort(key=lambda x: x[0])
    lv_rows = [{"level": int(n), "seconds": num(r.get("seconds")), "xp": num(r.get("xp")),
                "partial": str(r.get("partial")).lower() == "true", "t": _ts(r.get("_time"))}
               for r in levels if (n := opt_num(r.get("level"))) is not None]
    out = []
    for (t0, a), (t1, b) in zip(rows, rows[1:], strict=False):
        if until is not None and t1 > until:
            break
        la, lb = int(num(a["level"])), int(num(b["level"]))
        xa, xb = opt_num(a.get("xp")), opt_num(b.get("xp"))
        ga, gb = opt_num(a.get("money_copper")), opt_num(b.get("money_copper"))
        played = played_between(lv_rows, (la, xa), (lb, xb))
        gold = int(gb - ga) if ga is not None and gb is not None else None
        if not played and not gold:
            continue  # a sheet saved again without play
        played = played or 0.0
        start = max(t0, t1 - played)
        runs = [{"name": d.get("name"), "level": opt_num(d.get("level")), "seconds": opt_num(d.get("seconds")),
                 "xp": opt_num(d.get("xp"))} for d in dungeons if t0 < (_ts(d.get("_time")) or 0) <= t1]
        dings = sorted(r["level"] + 1 for r in lv_rows if not r["partial"] and r["t"] and t0 < r["t"] <= t1)
        out.append({"start": _iso(start), "end": _iso(t1), "played_s": int(played), "level_start": la,
                    "level_end": lb, "levels_gained": max(0, lb - la), "dings": dings,
                    # Deaths need the Recorder: unknown here, never 0.
                    "xp": None, "zones": [], "quests": [], "deaths": {"total": None, "by_zone": []},
                    "dungeons": runs, "players": [], "gold_delta_copper": gold, "estimated": True})
    out.reverse()
    return out


def with_history(sessions: list[dict], snaps: list[dict], levels: list[dict], dungeons: list[dict]) -> list[dict]:
    """The Recorder's sessions (newest first), then the logouts from before
    its first record (by_logout): a character that switched the Recorder on
    later keeps its earlier history, estimated, and one without it gets the
    logouts alone."""
    first = min((t for x in sessions if (t := _ts(x["start"])) is not None), default=None)
    return sessions + by_logout(snaps, levels, dungeons, until=first)


def mark_gaps(sessions: list[dict], gaps: list[dict], spans: list[tuple[float, float]]) -> None:
    """`recorder_off` ({off_s, levels}) on the Recorder session each gap
    goes to (wowstats.coverage.attach: the newest in the gap's level): about
    that much play in that level the Recorder didn't see, so the counts of
    the sessions in it are a floor. The estimated logouts need none: they
    say so already."""
    from wowstats.coverage import attach

    by_start = {_iso(spans[i][0]): g for i, g in attach(gaps, spans).items()}
    for s in sessions:
        g = None if s.get("estimated") else by_start.get(s["start"])
        if g:
            s["recorder_off"] = dict(g)
