"""Dungeon forecasts the player was actually offered, and the trips that can test them."""

from __future__ import annotations

import statistics
from bisect import bisect_left
from operator import itemgetter

from . import wings
from .dungeonview import key

LEGS = ("pickup_s", "travel_to_s", "inside_s", "travel_back_s", "handin_s")


def decision(inp: dict, dungeon: dict, goal: str | None, kind: str) -> dict:
    """Freeze the selected trip. An XP floor from quests alone is not a total-XP forecast."""
    g = dungeon["gain"]
    return {"dungeon": dungeon["name"], "objective": goal or "levelling", "kind": kind,
            "goals": dungeon["goals"], "basis": g["basis"], "personal_runs": g["own_runs"],
            "time_s": g["run_s"], "xp": None if g.get("unknown_handin_xp") else g["xp"], "quest_xp": g["xp_quests"],
            "known_xp": g["xp"] if g["xp"] is not None else g["xp_quests"],
            "unknown_handin_xp": g.get("unknown_handin_xp", 0), "kills_unknown": g["kills_unknown"],
            "legs": {k: g["trip"][k] for k in LEGS},
            "state": {k: inp.get(k) for k in ("who", "level", "xp", "xp_max", "rested_xp", "zone",
                                              "last_position", "data_until_t")},
            "last_known_entry_t": max((t["entry_t"] for t in inp.get("trips") or []
                                       if t.get("entry_t") is not None), default=None),
            "quests": [{"quest_id": q["quest_id"], "state": q["state"]} for q in g["quests"]]}


def _observation(trip: dict) -> dict:
    xp_in, xp_q = trip.get("xp_inside"), trip.get("xp_quests")
    return {"dungeon": wings.trip_name(trip), "entry_t": trip["entry_t"], "exit_t": trip.get("exit_t"),
            "start_t": trip.get("start_t"), "end_t": trip.get("end_t"), "time_s": trip.get("online_s"),
            "xp": xp_in + xp_q if xp_in is not None and xp_q is not None else None,
            "run_xp": trip.get("run_xp"), "quests_outside": trip.get("quests_outside"),
            "quests_handed_in": trip.get("quests_handed_in"), "handin_ids": trip.get("handin_ids") or [],
            "legs": {k: trip.get(k) for k in LEGS}}


def _missing(forecast: dict, actual: dict, made: float) -> list[str]:
    missing = [k for k in LEGS if forecast["legs"][k]["label"] and actual["legs"][k] is None]
    if actual["quests_outside"] and actual["legs"]["handin_s"] is None and "handin_s" not in missing:
        missing.append("handin_s")
    if actual["exit_t"] is None or actual["exit_t"] <= actual["entry_t"]:
        missing.append("exit")
    if actual["start_t"] is None or actual["start_t"] < made:
        missing.append("start after decision")
    if not actual["time_s"] or not actual["legs"]["inside_s"]:
        missing.append("online time")
    if actual["xp"] is None:
        missing.append("observed XP")
    if forecast["xp"] is None:
        missing.append("forecast XP")
    return missing


def _observed_again(old: dict, new: dict) -> dict:
    """Replace a retained run only with evidence covering the old observation.

    Trip times are rounded to tenths. An extension of the bracket can
    contribute at most its wall duration to the new online time. If the
    remaining seconds cannot cover the old bracket, keep the archived pair.
    """
    start, end = new["start_t"], new["end_t"]
    if (start is None or end is None or (old["start_t"] is not None and start > old["start_t"])
            or (old["end_t"] is not None and end < old["end_t"])):
        return old
    extra = ((old["start_t"] - start if old["start_t"] is not None else 0)
             + (end - old["end_t"] if old["end_t"] is not None else 0))
    if old["time_s"]:
        # An unfinished run has no established inside interval yet.
        required = old["time_s"] + (extra if old["exit_t"] and old["legs"]["inside_s"] else 0)
        if new["time_s"] is None or new["time_s"] < round(required, 1):
            return old
    if old.get("run_xp") is not None and new.get("run_xp") is None:
        return old
    for k in ("exit_t", "quests_outside", "quests_handed_in"):
        if old.get(k) is not None and (new.get(k) is None or new[k] < old[k]):
            return old
    if not set(old.get("handin_ids") or ()).issubset(new.get("handin_ids") or ()):
        return old
    inside = old["legs"]["inside_s"]
    if inside is not None and (new["legs"]["inside_s"] is None or new["legs"]["inside_s"] < inside):
        return old
    # A newly attributed late hand-in adds XP but can leave its time
    # unseen. Other losses of observed phases do not improve the record.
    late_handin = (new["quests_outside"] or 0) > (old["quests_outside"] or 0)
    for k in LEGS:
        if old["legs"][k] is not None and new["legs"][k] is None and not (k == "handin_s" and late_handin):
            return old
    if old["xp"] is not None:
        if new["xp"] is None:
            return old
        if new["xp"] < old["xp"]:
            # A lower aggregate alone can come from a lost reward. Accept
            # a downward correction backed by Polestar's own run XP when
            # that accounts for the drop and the other evidence survived.
            old_run, new_run = old.get("run_xp"), new.get("run_xp")
            if old_run is None or new_run is None or old_run - new_run < old["xp"] - new["xp"]:
                return old
    return new


def _online_bounds(progress: list[dict], t: float) -> tuple[float, float]:
    """Bounds on the Recorder's online clock between readings: at most one second per wall second."""
    i = bisect_left(progress, t, key=itemgetter("t"))
    before = progress[i - 1] if i else None
    after = progress[i] if i < len(progress) else None
    low, high = 0.0, float("inf")
    if before:
        low = before["online_s"]
        high = low + t - before["t"]
    if after:
        high = min(high, after["online_s"])
        low = max(low, after["online_s"] - (after["t"] - t))
    return low, high


def _compare(plan: dict, observations: list[dict], replaced: float | None, progress: list[dict]) -> dict:
    forecast = plan["dungeon_forecast"]
    made = plan["made_t"]
    # The budget is online time, as for the rest of plan review. Without
    # readings to prove remaining play, only join an entry inside the wall-clock budget.
    budget = plan["hours"] * 3600
    low, high = _online_bounds(progress, made)
    deadline = next((p["t"] for p in progress if p["t"] >= made and p["online_s"] - high >= budget), None)
    deadline = min((t for t in (deadline, plan.get("budget_end_t")) if t is not None), default=None)
    until = min((t for t in (replaced, deadline) if t is not None), default=None)
    cutoff = max(made, forecast.get("last_known_entry_t") or made)
    target = key(forecast["dungeon"])
    # A trip number is renumbered by a late upload. The instance entry
    # timestamp is its durable identity. New evidence can resolve its wing.
    kept = plan.get("dungeon_observations") or []
    archived = {o["entry_t"]: o for o in kept}
    by_entry = archived | {o["entry_t"]: o for o in observations}
    candidates = []
    for entry, obs in sorted(by_entry.items()):
        if entry <= cutoff or (until is not None and entry >= until):
            continue
        name = key(obs["dungeon"])
        if name != target and not (":" in target and name == target.split(":")[0]):
            continue
        previous = archived.get(entry)
        uncertain = False
        if entry - made >= budget:
            # Only recorded play can extend the wall-clock window. Sparse
            # readings can bracket both sides of its endpoint.
            if progress and progress[0]["t"] <= made and entry <= progress[-1]["t"]:
                entry_low, entry_high = _online_bounds(progress, entry)
                if entry_low - high >= budget:
                    continue
                uncertain = entry_high - low >= budget and (not previous or previous.get("window_uncertain", False))
            elif previous:
                uncertain = previous.get("window_uncertain", False)
            else:
                continue
        if previous:
            # The kill evidence for the wing can age out independently
            # of the timing and XP of this same instance entry.
            wing_name = obs["dungeon"] if name == target else previous["dungeon"]
            obs = {**_observed_again(previous, obs), "dungeon": wing_name}
        candidates.append({**obs, "window_uncertain": uncertain})
    row = {"made_t": made, "forecast": forecast, "observations": candidates, "budget_end_t": deadline}
    if not candidates:
        return {**row, "status": "abandoned" if until is not None else "pending"}
    if len(candidates) != 1 or key(candidates[0]["dungeon"]) != target or candidates[0]["window_uncertain"]:
        return {**row, "status": "ambiguous"}
    actual = candidates[0]
    missing = _missing(forecast, actual, made)
    if missing:
        return {**row, "status": "incomplete", "missing": missing}
    return {**row, "status": "matched", "error_s": actual["time_s"] - forecast["time_s"],
            "error_xp": actual["xp"] - forecast["xp"]}


def report(plans: list[dict], trips: list[dict], progress: list[dict]) -> dict:
    """Each decision owns entries only up to the next plan, including a replacement with no dungeon."""
    decisions = [(i, p) for i, p in enumerate(plans) if p.get("dungeon_forecast")]
    observations = [_observation(t) for t in trips if t.get("entry_t") is not None] if decisions else []
    online = [p for p in progress if not p.get("estimated")] if decisions else []
    reviews = [_compare(p, observations, plans[i + 1]["made_t"] if i + 1 < len(plans) else None, online)
               for i, p in decisions]
    by_basis = {}
    for basis in ("modeled", "personal_runs"):
        rows = [r for r in reviews if r["status"] == "matched"
                and ("personal_runs" if r["forecast"]["basis"] == "runs" else "modeled") == basis]
        by_basis[basis] = {"n": len(rows), **{
            "mean_absolute_" + k: round(statistics.mean(abs(r[k]) for r in rows), 1) if rows else None
            for k in ("error_s", "error_xp")}}
    return {"by_basis": by_basis, "counts": {s: sum(r["status"] == s for r in reviews)
            for s in ("pending", "abandoned", "incomplete", "ambiguous", "matched")}, "reviews": reviews}


def summary(report: dict) -> dict:
    """All decisions count. The page and MCP show the three latest, with at most two candidate entries per decision."""
    reviews = []
    for r in report["reviews"][-3:]:
        f = r["forecast"]
        candidates = r["observations"]
        reviews.append({k: r[k] for k in ("made_t", "status", "missing", "error_xp", "error_s") if k in r} | {
            "forecast": {k: f[k] for k in ("dungeon", "objective", "basis", "kind", "personal_runs", "time_s", "xp")},
            "candidates": len(candidates),
            "observations": [{k: o[k] for k in ("dungeon", "entry_t", "exit_t", "time_s", "xp")}
                             for o in candidates[:2]]})
    return {**report, "decisions": len(report["reviews"]), "reviews": reviews}
