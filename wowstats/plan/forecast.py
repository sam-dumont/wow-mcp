"""Dungeon forecasts the player was actually offered, and the trips that can test them."""

from __future__ import annotations

import statistics

from . import wings
from .dungeonview import key

LEGS = ("pickup_s", "travel_to_s", "inside_s", "travel_back_s", "handin_s")


def decision(inp: dict, dungeon: dict, goal: str | None, kind: str) -> dict:
    """Freeze the selected trip. An XP floor from quests alone is not a total-XP forecast."""
    g = dungeon["gain"]
    return {"dungeon": dungeon["name"], "objective": goal or "levelling", "kind": kind,
            "goals": dungeon["goals"], "basis": g["basis"], "personal_runs": g["own_runs"],
            "time_s": g["run_s"], "xp": g["xp"], "quest_xp": g["xp_quests"],
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
            "quests_outside": trip.get("quests_outside"),
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


def _online_bounds(progress: list[dict], t: float) -> tuple[float, float]:
    """Bounds on the Recorder's online clock between readings: at most one second per wall second."""
    before = next((p for p in reversed(progress) if p["t"] <= t), None)
    after = next((p for p in progress if p["t"] >= t), None)
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
    deadline = deadline or plan.get("budget_end_t")
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
        if not previous and entry - made >= budget and _online_bounds(progress, entry)[1] - low >= budget:
            continue
        # Retention may drop part of the Recorder session, or the kills that
        # identified the wing. Keep a complete pair unless a new hand-in
        # actually extended the observed trip.
        if (previous and key(previous["dungeon"]) == target and not _missing(forecast, previous, made)
                and (name != target or _missing(forecast, obs, made))
                and (obs["end_t"] or 0) <= (previous["end_t"] or 0)
                and (obs["quests_outside"] or 0) <= (previous["quests_outside"] or 0)):
            obs = previous
        candidates.append(obs)
    row = {"made_t": made, "forecast": forecast, "observations": candidates, "budget_end_t": deadline}
    if not candidates:
        return {**row, "status": "abandoned" if until is not None else "pending"}
    if len(candidates) != 1 or key(candidates[0]["dungeon"]) != target:
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
