"""The session journal: one entry per play session.

A session ends when the Recorder goes quiet for more than GAP_S, or when the
shipper marked a login (`login=true`, the first record after a logout). The
Recorder writes a `session` record at the top of every 5-minute chunk, so
those can't mark logins by themselves; they still carry the level.
"""

from __future__ import annotations

from datetime import UTC, datetime

from wowstats.stores import num, opt_num, parse_t

GAP_S = 20 * 60
BUCKET_S = 60


def _ts(s) -> float | None:
    t = parse_t(s)
    return None if t.year == 1 else t.timestamp()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def split(minutes: list[float], logins: list[float], gap: float = GAP_S) -> list[tuple[float, float]]:
    """(start, end) of each session, from the starts of the 1-minute buckets
    that hold any record and the times of login records."""
    ms = sorted(set(minutes))
    if not ms:
        return []
    login_buckets = sorted({t - t % BUCKET_S for t in logins})
    out = []
    start = prev = ms[0]
    li = 0
    for t in ms[1:]:
        while li < len(login_buckets) and login_buckets[li] <= prev:
            li += 1
        login_between = li < len(login_buckets) and prev < login_buckets[li] <= t
        if t - (prev + BUCKET_S) > gap or login_between:
            out.append((start, prev + BUCKET_S))
            start = t
        prev = t
    out.append((start, prev + BUCKET_S))
    return out


def build(minutes: list[dict], xp: list[dict], events: list[dict], logins: list[dict], dungeons: list[dict],
          players: list[dict], snaps: list[dict], own: str = "") -> list[dict]:
    """Newest session first. `players` and gold are kept here; the public
    view drops them (wowstats.redact)."""
    mins = [t for r in minutes if num(r.get("n")) > 0 and (t := _ts(r.get("_time"))) is not None]
    login_t = [t for t in (_ts(r.get("_time")) for r in logins) if t is not None]
    spans = split(mins, login_t)
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

    for r in xp:
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
        elif kind == "zone" and r.get("zone"):
            # A zone's time runs to the next zone change or the end of the
            # chunk group: clip it to this session.
            secs = min(num(r.get("duration_s")), max(0.0, s["end"] - t))
            z = s["zones"].setdefault(r["zone"], 0.0)
            s["zones"][r["zone"]] = z + secs
        elif kind == "turnin":
            s["quests"].append({"quest_id": opt_num(r.get("quest_id")), "name": r.get("quest_name") or None,
                                "zone": r.get("zone") or None, "t": r.get("_time")})
        elif kind == "death":
            zone = r.get("zone") or "Unknown"
            s["deaths"][zone] = s["deaths"].get(zone, 0) + 1
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
