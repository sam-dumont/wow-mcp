"""What one more run of a dungeon pays, in the numbers a player weighs
against questing: the XP of a run at his level now, the share of the level
it is, how long it takes, its XP per hour against his own questing, and the
dungeon's quests to pick up first.

One function, `estimate`, for the three places that say it: the Overview's
dungeon move, the Dungeons panel and the MCP's dungeon_options. They agree
because they call it with the same inputs (wowstats.plan.dungeonview).

Where the numbers come from, in order:

1. his own runs of that dungeon: the trip's time (travel there and back,
   inside, and the pickups and hand-ins while quests are left) and the kill
   XP inside, carried from the level he ran it at to his level now with the
   KB's kill formula (xp.kill_formula) over the creatures he killed there.
   Forever's dungeon bonus and the group split multiply every kill alike,
   so they cancel in that ratio. A run at another level without the
   creatures' levels can't be carried: its kill XP stays unknown;
2. otherwise the KB's clear time for the dungeon (dungeon.<name>
   clear_min), plus his own measured travel and quest time from his other
   trips when he has some. Kill XP stays unknown: no source gives a
   dungeon's creatures, and Forever's dungeon XP isn't Classic's;
3. the dungeon's quests he can do now (forever-db's XP, the KB's hand-in
   share at his level) in both cases.

What isn't known is None, never guessed: with the kills unknown the XP is
the quests' alone and says so, with no pace against questing (vs_questing
None) and no verdict.
"""

from __future__ import annotations

import re

from wowstats.rates import per_hour

from .estimate import fdur

SHOWN = 3  # quests the move lists before "N more" (the page's rule)


# ---------------------------------------------------------------- kill XP


def kill_rule() -> dict | None:
    from wowstats.analysis.kb import static_rule

    r = static_rule("xp.kill_formula")
    return (r or {}).get("value") or None


def _band(table, level: int):
    for row in table:
        if row[0] <= level <= row[1]:
            return row
    return None


def grey(level: int, rule: dict) -> int:
    """The highest creature level that pays nothing at `level`."""
    if level >= 60:
        return int(rule.get("grey_at_60") or 51)
    row = _band(rule["grey"], level)
    if not row or row[2] is None:
        return 0
    return level - level // row[2] - row[3]


def kill_xp(level: int, mob: int, elite: bool, rule: dict) -> float:
    """One kill's XP by Classic's formula: the shape of it, not its size."""
    if mob <= grey(level, rule):
        return 0.0
    a, b = rule["base"]
    base = a * level + b
    if mob >= level:
        x = base * (1 + rule["above_per_level"] * min(mob - level, rule["above_cap"]))
    else:
        zd = (_band(rule["zero_difference"], level) or rule["zero_difference"][-1])[2]
        x = max(0.0, base * (1 - (level - mob) / zd))
    return x * (rule["elite_factor"] if elite else 1)


def kills_now(trip: dict, level: int, rule: dict | None) -> float | None:
    """The run's kill XP inside, as it would pay at `level`: as measured at
    the level it was run, else scaled by the formula over the creatures he
    killed; None when that can't be done (no creature levels, no rule)."""
    xp = trip.get("xp_inside")
    if xp is None:
        return None
    ran = trip.get("level_start")
    if ran == level:
        return float(xp)
    kills = trip.get("kill_levels") or []
    if not rule or not ran or not kills:
        return None
    then = sum(kill_xp(ran, m, bool(e), rule) for m, e in kills)
    now = sum(kill_xp(level, m, bool(e), rule) for m, e in kills)
    if then <= 0:
        return None
    return xp * now / then


# ---------------------------------------------------------------- clear time


def rule_name(name: str) -> str:
    """The KB's rule for a dungeon: "The Deadmines" -> dungeon.deadmines,
    "Excavation Site: Wetlands" -> dungeon.excavation_site_wetlands."""
    n = re.sub(r"^(the |stormwind )", "", (name or "").lower()).replace("'", "")
    return "dungeon." + re.sub(r"[^a-z]+", "_", n).strip("_")


def clear_s(name: str) -> float | None:
    """A full clear's time from the KB's dungeon.<name> rule: the median of
    recorded clears when it gives one, else the middle of its range."""
    from wowstats.analysis.kb import static_rule

    c = (static_rule(rule_name(name)) or {}).get("clear_min")
    if isinstance(c, dict):
        m = c.get("foreverdb_median")
        if isinstance(m, int | float):
            return float(m) * 60
        c = next((v for v in c.values() if isinstance(v, list) and len(v) == 2), None)
    if isinstance(c, list) and len(c) == 2:
        return (c[0] + c[1]) / 2 * 60
    return None


# ---------------------------------------------------------------- the estimate


def _mean(rows: list[dict], k: str) -> float:
    return sum(r.get(k) or 0 for r in rows) / len(rows) if rows else 0.0


def about_xp(n: float) -> str:
    """XP as the move says it: ~18k, ~5.4k, ~820."""
    n = round(n)
    if n >= 10000:
        return f"{round(n / 1000)}k"
    if n >= 1000:
        return f"{round(n / 100) / 10:g}k"
    return f"{round(n, -1):,}"


def about_s(s: float) -> str:
    """A duration to five minutes: 1h20, 35 min."""
    return fdur(max(300, round(s / 300) * 300))


def ratio(x: float) -> str:
    """A pace against questing, as the Overview writes ratios: 1.4×."""
    return f"{x:.1f}×" if x >= 0.05 else "under 0.1×"


def estimate(*, level: int, xp_max: int | None, name: str, runs: list[dict], others: list[dict],
             quests: list[dict], questing_xph: float | None, rule: dict | None = None,
             clear: float | None = None) -> dict:
    """One more run at `level`. `runs`: his trips of this dungeon
    (facts.trips); `others`: every trip he made (their travel and quest
    time stand in for a dungeon he never ran); `quests`: the dungeon's
    quests as dungeonview.quests_for gives them; `questing_xph`: his
    questing XP per hour (estimate.rate)."""
    todo = [q for q in quests if q["state"] == "open"
            or (q["state"] == "new" and not q.get("blocked") and (q.get("min_level") or 0) <= level)]
    xp_q = sum(q.get("xp_now") or 0 for q in todo)
    if runs:
        basis = "runs"
        trips = sum(_mean(runs, k) for k in ("travel_to_s", "inside_s", "travel_back_s"))
        run_s = trips + (_mean(runs, "pickup_s") + _mean(runs, "handin_s") if todo else 0)
        scaled = [kills_now(t, level, rule) for t in runs]
        known = [x for x in scaled if x is not None]
        xp_k = sum(known) / len(known) if known else None
        time = "trip"
    else:
        clear = clear_s(name) if clear is None else clear
        basis = "rules" if clear else None
        xp_k = None
        if clear and others:
            run_s = clear + _mean(others, "travel_to_s") + _mean(others, "travel_back_s") + (
                _mean(others, "pickup_s") + _mean(others, "handin_s") if todo else 0)
            time = "trip"
        else:
            run_s, time = clear, "clear" if clear else None
    xp = xp_q + xp_k if xp_k is not None else None
    shown = xp if xp is not None else xp_q
    xph = per_hour(shown, run_s) if run_s and shown else None
    qx = questing_xph or None
    out = {
        "basis": basis, "own_runs": len(runs), "level": level, "run_s": round(run_s) if run_s else None, "time": time,
        "xp_kills": round(xp_k) if xp_k is not None else None, "xp_quests": round(xp_q),
        "xp": round(xp) if xp is not None else None,
        "level_pct": round(shown / xp_max * 100) if xp_max and shown else None,
        "xp_per_hour": xph, "kills_unknown": xp_k is None,
        "questing_xp_per_hour": round(qx) if qx else None,
        # With the kills unknown the pace is the quests' alone: no ratio to
        # rank or judge on, the kill XP could flip it.
        "vs_questing": round(xph / qx, 3) if xph and qx and xp_k is not None else None,
        "questing_s": round(shown / qx * 3600) if qx and shown else None,
        "quests": [{**{k: q.get(k) for k in ("quest_id", "name", "url", "xp_now", "state", "level")},
                    "starts": q.get("starts") or []} for q in todo],
        "quests_shown": SHOWN,
    }
    out["summary"] = summary(out)
    return out


# The verdict against his own questing: at or above FASTER it's worth the
# trip, under SLOWER only the drops are (owner wording, 2026-11-19).
FASTER, SLOWER = 1.2, 0.8


def verdict(vs: float | None) -> str | None:
    """"faster", "same" or "slower" from the pace against questing."""
    if vs is None:
        return None
    return "faster" if vs >= FASTER else "slower" if vs < SLOWER else "same"


def summary(g: dict) -> str | None:
    """The estimate in one line, the way all three places say it. g["lead"]
    is the part shown bold: the verdict, or the time and quests."""
    g["lead"] = None
    of = f" ({g['level_pct']}% of a level)" if g.get("level_pct") is not None else ""
    vs = g.get("vs_questing")
    v = verdict(vs)
    if g["xp"] is not None and g["run_s"]:
        if not g["xp"]:
            return f"~{about_s(g['run_s'])}, no XP at your level"
        body = f"~{about_s(g['run_s'])} for ~{about_xp(g['xp'])} XP{of}"
        if v == "faster":
            g["lead"] = "Worth it:"
            return f"Worth it: {body}, {ratio(vs)} faster than your questing"
        if v == "same":
            g["lead"] = "About the same as questing:"
            return f"About the same as questing: {body}"
        if v == "slower":
            g["lead"] = "Slower than questing:"
            return f"Slower than questing: go for the drops only. {body}, {ratio(vs)} your questing pace"
        return body
    quests = g["xp_quests"]
    if not quests:
        return None
    if g["run_s"]:
        # Kills unknown: no verdict. The quests alone can't say faster or
        # slower than questing, the run's kill XP could flip it.
        g["lead"] = f"~{about_s(g['run_s'])} + quests worth ~{about_xp(quests)} XP"
        return f"{g['lead']}{of}"
    worth = f", what {about_s(g['questing_s'])} of your questing pays" if g.get("questing_s") else ""
    return f"Its quests pay ~{about_xp(quests)} XP{of}{worth}"
