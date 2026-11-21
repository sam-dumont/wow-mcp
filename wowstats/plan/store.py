"""Plans kept, and compared with what happened.

Each plan is saved to ANALYSIS_STATE_DIR/plans.json (the MCP state volume)
with what a later comparison needs: when it was made, the start, the
checkpoints (played offset and level) and the end. Nothing in it comes from
Battle.net (the planner never reads vm-wow; the worn item ids a caller
passes for the quests done stay in the inputs' two-minute cache), so it
needs no 30-day expiry; the newest MAX_PLANS per character are kept.

`review()` lays a plan over the XP the character really made after it: the
online time at which he reached each checkpoint's level against the time
the plan gave it. `calibration()` turns the reviewed plans into one factor
(the median of time actually needed over time planned) that the estimator
multiplies its step times by, with how many plans it rests on.
"""

from __future__ import annotations

import contextlib
import json
import os
import statistics
import tempfile
import threading
from pathlib import Path

FILE = "plans.json"
SCHEMA = 1
MAX_PLANS = 30
CAL_MIN, CAL_MAX = 0.5, 2.0
_LOCK = threading.Lock()


def path(env: dict) -> Path | None:
    d = (env.get("ANALYSIS_STATE_DIR") or "").strip()
    return Path(d) / FILE if d else None


def load(env: dict) -> dict:
    p = path(env)
    if p is None:
        return {"schema": SCHEMA, "plans": {}}
    with contextlib.suppress(OSError, ValueError):
        s = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(s, dict) and s.get("schema") == SCHEMA:
            return s
    return {"schema": SCHEMA, "plans": {}}


def _save(env: dict, s: dict) -> None:
    p = path(env)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".plans-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def record(est: dict) -> dict:
    """What is kept of a plan: numbers and labels the estimator derived from
    game data, nothing else."""
    return {"made_t": est["made_t"], "hours": est["hours"], "context": est.get("context"),
            "constraints": (est.get("constraints") or {}).get("text") or "",
            "stops_share": est["pace"]["stops"]["share"],
            "start": {k: est["start"].get(k) for k in ("level", "xp", "gold_copper", "zone")},
            "checkpoints": [{"at_s": c["at_s"], "level": c["level"], "step": c.get("step")}
                            for c in est["checkpoints"]],
            "end": {k: est["end"].get(k) for k in ("level", "gold_copper", "played_s")},
            "pace": {k: est["pace"][k]["factor"] for k in ("questing", "travel", "town", "dungeon")},
            "facts": est.get("facts") or {}}  # for check_plan: a client's write-up against these numbers


def save(env: dict, char: str, est: dict) -> None:
    if not est.get("supported"):
        return
    with _LOCK:
        s = load(env)
        plans = s.setdefault("plans", {}).setdefault(char, [])
        plans.append(record(est))
        del plans[:-MAX_PLANS]
        _save(env, s)


def latest(env: dict, char: str) -> dict | None:
    plans = (load(env).get("plans") or {}).get(char) or []
    return plans[-1] if plans else None


def review(plan: dict, progress: list[dict]) -> dict | None:
    """One plan against the progress after it: `progress` is the XP records'
    (t, level as a float, online seconds since the first record) in order.
    None until he has played past the plan's first checkpoint."""
    after = [p for p in progress if p["t"] >= plan["made_t"]]
    if len(after) < 2:
        return None
    on0 = after[0]["online_s"]
    played = after[-1]["online_s"] - on0
    stop = 1 - (plan.get("stops_share") or 0)
    rows = []
    for c in plan["checkpoints"]:
        planned_online = c["at_s"] / stop if stop > 0 else c["at_s"]
        hit = next((p for p in after if p["level"] >= c["level"]), None)
        actual = (hit["online_s"] - on0) if hit else None
        if actual is None and played < planned_online:
            break  # not played that far yet: no verdict on this one
        rows.append({"level": c["level"], "step": c.get("step"), "planned_online_s": round(planned_online),
                     "actual_online_s": round(actual) if actual is not None else None,
                     "ratio": round(actual / planned_online, 2) if actual and planned_online else None})
    if not rows:
        return None
    end_planned = plan["end"]["played_s"] / stop if stop > 0 else plan["end"]["played_s"]
    at_end = next((p for p in after if p["online_s"] - on0 >= end_planned), None)
    ratios = [r["ratio"] for r in rows if r["ratio"]]
    return {"made_t": plan["made_t"], "hours": plan["hours"], "constraints": plan.get("constraints"),
            "start_level": plan["start"]["level"], "played_since_s": round(played), "checkpoints": rows,
            "end_planned_level": plan["end"]["level"],
            "end_actual_level": round(at_end["level"], 2) if at_end else None,
            "ratio": round(statistics.median(ratios), 2) if ratios else None,
            "missed": sum(1 for r in rows if r["actual_online_s"] is None)}


def reviews(env: dict, char: str, progress: list[dict]) -> list[dict]:
    plans = (load(env).get("plans") or {}).get(char) or []
    out = []
    for i, p in enumerate(plans):
        nxt = plans[i + 1]["made_t"] if i + 1 < len(plans) else None
        prog = [x for x in progress if nxt is None or x["t"] < nxt]  # a newer plan replaced it from there
        r = review(p, prog)
        if r:
            out.append(r)
    return out


def calibration(env: dict, char: str, progress: list[dict] | None = None) -> dict:
    """The median ratio of time needed over time planned, over reviewed
    plans, clamped; 1.0 with n=0 before any plan was reviewed."""
    rs = [r["ratio"] for r in reviews(env, char, progress or []) if r.get("ratio")]
    if not rs:
        return {"factor": 1.0, "n": 0}
    return {"factor": round(min(CAL_MAX, max(CAL_MIN, statistics.median(rs))), 2), "n": len(rs),
            "ratios": rs[-5:]}
