"""What one more run of a dungeon pays, in the numbers a player weighs
against questing: the trip's time in its parts (pickups, travel, inside,
hand-ins), its XP from quests and kills, its XP per hour against his own
questing, and the dungeon's quests to pick up first.

One function, `estimate`, for the three places that say it: the Overview's
dungeon move, the Dungeons panel and the MCP's dungeon_options. They agree
because they call it with the same inputs (wowstats.plan.dungeonview).

Where the numbers come from, each part labelled (`trip`):

1. his own runs of that dungeon, "measured": the trip's time (travel there
   and back, inside, and the pickups and hand-ins while quests are left)
   and the kill XP inside, carried from the level he ran it at to his level
   now with the KB's kill formula (xp.kill_formula) over the creatures he
   killed there. Forever's dungeon bonus and the group split multiply every
   kill alike, so they cancel in that ratio. A run at another level without
   the creatures' levels can't be carried: its kill XP stays unknown;
2. otherwise:
   - the pickups, the travel and the hand-ins from Polestar's pick-up and
     hand-in rules on his own quests, from where he was last seen
     (wowstats.plan.tripcost, "route"); without route data for the dungeon,
     the means of his other trips ("measured", the old flat mean);
   - the time inside and the kill XP from the kill estimate
     (wowstats.plan.dungeonxp: kills x the seconds a kill took in the
     reference runs, "estimate"), else the KB's clear time for the time
     (dungeon.<name> clear_min, "estimate");
3. the quests' XP: those the trip takes and hands in (forever-db's XP, the
   KB's hand-in share at his level).

The kill estimate counts in the run's XP, its pace and the verdict, labelled
(owner, 2026-11-23: it replaced the rule that an estimate never ranks a
dungeon). What isn't known is None, never guessed: with no kill estimate
either the XP is the quests' alone and says so, with no pace against
questing (vs_questing None) and no verdict.
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


LABEL_RANK = {"measured": 0, "route": 1, "estimate": 2}


def _worst(*labels: str | None) -> str:
    """The weakest of the labels a number is made of."""
    known = [x for x in labels if x]
    return max(known, key=LABEL_RANK.__getitem__) if known else "estimate"


def _ids(q: dict) -> set[int]:
    return {q["quest_id"], *(q.get("variants") or [])}


def estimate(*, level: int, xp_max: int | None, name: str, runs: list[dict], others: list[dict],
             quests: list[dict], questing_xph: float | None, rule: dict | None = None,
             clear: float | None = None, route: dict | None = None) -> dict:
    """One more run at `level`. `runs`: his trips of this dungeon
    (facts.trips); `others`: every trip he made (their means stand in for
    the pickups and travel when there is no route); `quests`: the dungeon's
    quests as dungeonview.quests_for gives them; `questing_xph`: his
    questing XP per hour (estimate.rate); `route`: the trip Polestar's rules
    make from where he stands (dungeonview.route_trip: tripcost.trip plus
    the XP of its quests), None when the reference has none."""
    if route:
        ids = set(route["quests"]) | set(route.get("prereqs") or [])
        todo = [q for q in quests if _ids(q) & ids and q["state"] != "done"]
        xp_q = float(route["xp_quests"])
    else:
        todo = [q for q in quests if q["state"] == "open"
                or (q["state"] == "new" and not q.get("blocked") and (q.get("min_level") or 0) <= level)]
        xp_q = float(sum(q.get("xp_now") or 0 for q in todo))
    left = bool(todo or (route and route["quests"]))
    parts: dict[str, tuple[float, str]] = {}
    kills = None
    if runs:
        basis = "runs"
        parts["inside_s"] = (_mean(runs, "inside_s"), "measured")
        parts["travel_s"] = (_mean(runs, "travel_to_s") + _mean(runs, "travel_back_s"), "measured")
        if left:
            parts["pickup_s"] = (_mean(runs, "pickup_s"), "measured")
            parts["handin_s"] = (_mean(runs, "handin_s"), "measured")
        scaled = [kills_now(t, level, rule) for t in runs]
        known = [x for x in scaled if x is not None]
        xp_k = sum(known) / len(known) if known else None
        k_label = "measured"
    else:
        # Time inside: the kill estimate's (kills x seconds a kill), else the
        # KB's clear time. Owner, 2026-11-23: Gnomeregan's guide says ~40 min,
        # the model ~1h15, a real run "probably ~1h": the model is closer.
        from . import dungeonxp

        kills = dungeonxp.estimate(name, level, rule)
        model = float(kills["inside_s"]) if clear is None and kills and kills.get("inside_s") else None
        basis = "kill_model" if model else None
        clear = (model or clear_s(name)) if clear is None else clear
        basis = basis or ("rules" if clear else None)
        if clear:
            parts["inside_s"] = (clear, "estimate")
        if route:
            parts["pickup_s"] = (route["pickup_s"], "route")
            parts["travel_s"] = (route["travel_to_s"] + route["travel_back_s"], "route")
            parts["handin_s"] = (route["handin_s"], "route")
        elif others:
            parts["travel_s"] = (_mean(others, "travel_to_s") + _mean(others, "travel_back_s"), "measured")
            if left:
                parts["pickup_s"] = (_mean(others, "pickup_s"), "measured")
                parts["handin_s"] = (_mean(others, "handin_s"), "measured")
        xp_k = float(kills["xp"]) if kills and kills.get("label") == "estimate" and kills.get("xp") is not None \
            else None
        k_label = "estimate"
    inside = parts.get("inside_s")
    run_s = sum(v for v, _ in parts.values()) if inside else None
    time = ("trip" if len(parts) > 1 else "clear") if inside else None
    xp = xp_q + xp_k if xp_k is not None else None
    shown = xp if xp is not None else xp_q
    xph = per_hour(shown, run_s) if run_s and shown else None
    qx = questing_xph or None
    vs = round(xph / qx, 3) if xph and qx and xp_k is not None else None
    t_label = _worst(*(lbl for _, lbl in parts.values()))
    xp_label = _worst(k_label, "route" if route else "estimate") if xp_k is not None else None
    out = {
        "basis": basis, "own_runs": len(runs), "level": level, "run_s": round(run_s) if run_s else None, "time": time,
        "xp_kills": round(xp_k) if xp_k is not None else None, "xp_quests": round(xp_q),
        "xp": round(xp) if xp is not None else None,
        "level_pct": round(shown / xp_max * 100) if xp_max and shown else None,
        "xp_per_hour": xph, "kills_unknown": xp_k is None,
        "questing_xp_per_hour": round(qx) if qx else None,
        # With the kills unknown (no run, no kill estimate) the pace is the
        # quests' alone: no ratio to rank or judge on.
        "vs_questing": vs,
        "questing_s": round(shown / qx * 3600) if qx and shown else None,
        "quests": [{**{k: q.get(k) for k in ("quest_id", "name", "url", "xp_now", "state", "level")},
                    "starts": q.get("starts") or []} for q in todo],
        "quests_shown": SHOWN,
        "upgrades": sum(1 for q in todo if any(r.get("state") == "missing" for r in q.get("rewards") or [])),
    }

    def part(v, label):
        return {"value": round(v) if v is not None else None, "label": label if v is not None else None}

    trip = {k: part(*parts[k]) if k in parts else part(None, None)
            for k in ("pickup_s", "inside_s", "handin_s", "travel_s")}
    trip.update({"total_s": part(run_s, t_label if run_s else None),
                 "xp_quests": part(xp_q, "route" if route else "estimate"),
                 "xp_kills": part(xp_k, k_label),
                 "xp_total": part(xp, xp_label),
                 "xp_per_hour": part(xph if xp is not None else None, _worst(xp_label, t_label)),
                 "vs_questing": {"value": vs, "label": _worst(xp_label, t_label) if vs is not None else None},
                 "from": route.get("from") if route else None,
                 "stops": {"pickup": len(route["pickup"]), "handin": len(route["handin"])} if route else None})
    out["trip"] = trip
    out["kill_xp"] = run_kills(name, level, runs, xp_k, rule) if runs else kills
    out["summary"] = summary(out)
    return out


def model_inside(name: str, level: int, rule: dict | None) -> float | None:
    """A run's time inside by the kill estimate (wowstats.plan.dungeonxp)."""
    from . import dungeonxp

    e = dungeonxp.estimate(name, level, rule)
    return float(e["inside_s"]) if e and e.get("inside_s") else None


def run_kills(name: str, level: int, runs: list[dict], xp_k: float | None, rule: dict | None) -> dict | None:
    """A run's kill XP and time inside, labelled: "measured" from his own
    runs there (carried to his level), else the estimate for a dungeon he
    hasn't run (wowstats.plan.dungeonxp: "estimate" or "not_enough_data"),
    which the run's XP and its verdict count too, labelled."""
    if runs and xp_k is not None:
        inside = _mean(runs, "inside_s")
        return {"label": "measured", "xp": round(xp_k), "inside_s": round(inside) if inside else None,
                "xp_per_hour": per_hour(xp_k, inside) if inside else None, "own_runs": len(runs)}
    from . import dungeonxp

    return dungeonxp.estimate(name, level, rule)


# The verdict against his own questing: at or above FASTER it's worth the
# trip, under SLOWER only the drops are (owner wording, 2026-11-19).
FASTER, SLOWER = 1.2, 0.8


def verdict(vs: float | None) -> str | None:
    """"faster", "same" or "slower" from the pace against questing."""
    if vs is None:
        return None
    return "faster" if vs >= FASTER else "slower" if vs < SLOWER else "same"


def _upgrades(n: int) -> str:
    return f" + {n} upgrade{'s' if n != 1 else ''}" if n else ""


def split(g: dict) -> str:
    """The trip's parts as the line says them: 25 min pickups · 1h15 inside
    · 20 min hand-ins (a part with nothing in it left out; the travel to the
    door and back is in the total only)."""
    t = g.get("trip") or {}
    words = (("pickup_s", "pickups"), ("inside_s", "inside"), ("handin_s", "hand-ins"))
    return " · ".join(f"{about_s(t[k]['value'])} {w}" for k, w in words if (t.get(k) or {}).get("value"))


def summary(g: dict) -> str | None:
    """The estimate in one line, the way all three places say it: "~2h10
    trip for ~54k XP + 3 upgrades, 1.6× your questing (25 min pickups ·
    1h15 inside · 20 min hand-ins)". g["lead"] is the part shown bold: the
    trip's time, or the time and quests."""
    g["lead"] = None
    of = f" ({g['level_pct']}% of a level)" if g.get("level_pct") is not None else ""
    vs = g.get("vs_questing")
    if g["xp"] is not None and g["run_s"]:
        g["lead"] = f"~{about_s(g['run_s'])} trip"
        parts = split(g)
        parts = f" ({parts})" if parts else ""
        if not g["xp"]:
            return f"{g['lead']}, no XP at your level{parts}"
        pace = f", {ratio(vs)} your questing" if vs is not None else ""
        return f"{g['lead']} for ~{about_xp(g['xp'])} XP{_upgrades(g.get('upgrades') or 0)}{pace}{parts}"
    quests = g["xp_quests"]
    if not quests:
        return None
    if g["run_s"]:
        # Kills unknown (no run, no kill estimate): no pace, no verdict. The
        # quests alone can't say faster or slower than questing.
        g["lead"] = f"~{about_s(g['run_s'])} + quests worth ~{about_xp(quests)} XP"
        return f"{g['lead']}{of}"
    worth = f", what {about_s(g['questing_s'])} of your questing pays" if g.get("questing_s") else ""
    return f"Its quests pay ~{about_xp(quests)} XP{of}{worth}"
