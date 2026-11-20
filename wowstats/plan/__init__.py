"""Session plans: "what should I do for the next N hours".

`plan(env, char, hours, constraints, context)` gathers the character's state
and history (wowstats.plan.facts, game data only), lets the estimator
compute every number (wowstats.plan.estimate) and keeps the plan for a later
comparison (wowstats.plan.store). The text is the estimator's own; the
client's model writes it up over MCP (plan_session's brief, check_plan).
No model runs on the server.

Only levelling is planned so far; max-level PvE and PvP answer "not
supported yet" and point to the knowledge base.
"""

from __future__ import annotations

import threading
import time

from . import estimate as est_mod
from . import facts
from . import store as plan_store

_LOCK = threading.Lock()
_CACHE: dict = {}
CACHE_S = 120


# Striped, and the cache capped: one lock and one entry per name asked for
# grew without limit (review 0.6.0, S5).
_KEY_LOCKS = [threading.Lock() for _ in range(64)]
MAX_CACHED = 64


def inputs(env: dict, char: str | None = None, store=None) -> dict:
    """facts.gather, kept two minutes: the MCP commands of one conversation
    read the same inputs. One gather per character at a time: the full
    view's page asks for the plan's status and the Dungeons panel at once,
    and two gathers side by side would read every event twice. Keyed by
    the person too (wowstats.tenancy): two people may name a character alike."""
    from wowstats.stores import tenant_of

    key = (tenant_of(env, store), (char or env.get("DEFAULT_CHAR") or "").lower())
    with _LOCK:
        lock = _KEY_LOCKS[hash(key) % len(_KEY_LOCKS)]
    with lock:
        with _LOCK:
            hit = _CACHE.get(key)
            if hit and time.time() - hit[0] < CACHE_S:
                return hit[1]
        inp = facts.gather(env, char, store=store)
        with _LOCK:
            _CACHE[key] = (time.time(), inp)
            while len(_CACHE) > MAX_CACHED:
                _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
        return inp


def compute(env: dict, char: str | None = None, hours: float | None = None, constraints: str = "",
            context: str | None = None, store=None) -> dict:
    return est_mod.estimate(inputs(env, char, store), hours, constraints, context)


def plan(env: dict, char: str | None = None, hours: float | None = None, constraints: str = "",
         context: str | None = None, store=None) -> dict:
    """The plan: {markdown, writer, checked, estimate}."""
    from . import write as writer

    inp = inputs(env, char, store)
    e = est_mod.estimate(inp, hours, constraints, context)
    if not e.get("supported"):
        return {"markdown": e["message"], "writer": "estimator", "checked": True, "estimate": e}
    plan_store.save(env, inp["who"]["char"], e)
    return {"markdown": writer.fallback(e), "writer": "estimator", "checked": True, "estimate": e}


def status(env: dict, char: str | None = None, store=None) -> dict:
    """For the full view: the mount goal and how the last plan went."""
    inp = inputs(env, char, store)
    p = est_mod.pace(inp)
    m = est_mod.mounts(inp, p)
    revs = inp.get("plan_reviews") or []
    tiers = [{"level": t["level"], "need": est_mod.fgold(t["need_copper"]), "left": est_mod.fgold(t["left_copper"]),
              "bought": t["bought"], "behind": t["behind"],
              "pct": round(100 * (1 - t["left_copper"] / t["need_copper"])) if t["need_copper"] else None,
              "eta": est_mod.fdur(t["eta_played_s"]) if t.get("eta_played_s") is not None else None,
              "eta_level": t.get("eta_level"), "source": (t.get("source") or {}).get("source_type")}
             for t in m["tiers"]]
    gr = (inp.get("gold_rate") or {})
    last = revs[-1] if revs else None
    return {"available": True, "char": inp["who"]["char"], "gold": est_mod.fgold(m["gold_copper"]),
            "gold_rate": (est_mod.fgold(gr["copper_per_hour"]) + "/h") if gr.get("copper_per_hour") is not None
            else None, "gold_rate_n": gr.get("n"), "tiers": tiers, "unknown": m.get("unknown") or [],
            "review": None if not last else {
                "hours": last["hours"], "ratio": last["ratio"], "end_planned": last["end_planned_level"],
                "end_actual": last["end_actual_level"], "checkpoints": len(last["checkpoints"]),
                "missed": last["missed"]},
            "calibration": inp.get("calibration")}
