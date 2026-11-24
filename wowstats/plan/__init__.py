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

from wowstats import eventcache

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


def inputs(env: dict, char: str | None = None, store=None, battlenet=None, gear_inputs=None) -> dict:
    """facts.gather, kept two minutes: the MCP commands of one conversation
    read the same inputs. One gather per character at a time: the full
    view's page asks for the plan's status and the Dungeons panel at once,
    and two gathers side by side would read every event twice. Keyed by
    the person too (wowstats.tenancy): two people may name a character alike.
    `battlenet`: the item ids Battle.net says it wears now, read live by the
    caller (wowstats.quests.with_battlenet); in the key, so an answer made
    with them never reaches a caller without them (Battle.net to the AI off).
    `gear_inputs`: a callable returning the doll's gear inputs
    (wowstats.views.Views.gear_inputs), called only when the cache misses;
    in the key too, so an answer scored like the doll never reaches a caller
    that didn't ask for it. And the spec the gear is scored for (WEIGHTS):
    the page and the MCP once shared an entry whichever of them filled it,
    each with its own spec (review-3)."""
    from wowstats.stores import tenant_of
    from wowstats.views import parse_weights

    bn = tuple(sorted(set(battlenet or ())))
    scoring = tuple(sorted(parse_weights(env.get("WEIGHTS") or "").items()))
    key = (tenant_of(env, store), (char or env.get("DEFAULT_CHAR") or "").lower(), bn, gear_inputs is not None,
           scoring)
    with _LOCK:
        lock = _KEY_LOCKS[hash(key) % len(_KEY_LOCKS)]
    with lock:
        with _LOCK:
            hit = _CACHE.get(key)
            if hit and time.time() - hit[0] < CACHE_S:
                eventcache.replay(hit[2])  # the answer says the data it was gathered from (as_of)
                return hit[1]
        inp, seen = eventcache.computed(lambda: facts.gather(
            env, char, store=store, battlenet=bn, gear_inputs=gear_inputs() if gear_inputs is not None else None))
        with _LOCK:
            now = time.time()
            # Expired entries go as soon as anything is kept: a plan's inputs
            # hold a character's whole state, and nobody reads them again.
            for k in [k for k, v in _CACHE.items() if now - v[0] >= CACHE_S]:
                del _CACHE[k]
            _CACHE[key] = (now, inp, seen)
            while len(_CACHE) > MAX_CACHED:
                _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
        return inp


def compute(env: dict, char: str | None = None, hours: float | None = None, constraints: str = "",
            context: str | None = None, store=None, battlenet=None) -> dict:
    return est_mod.estimate(inputs(env, char, store, battlenet), hours, constraints, context)


def plan(env: dict, char: str | None = None, hours: float | None = None, constraints: str = "",
         context: str | None = None, store=None, battlenet=None) -> dict:
    """The plan: {markdown, writer, checked, estimate}."""
    from . import write as writer

    inp, seen = eventcache.computed(lambda: inputs(env, char, store, battlenet))
    e = est_mod.estimate(inp, hours, constraints, context)
    if not e.get("supported"):
        return {"markdown": e["message"], "writer": "estimator", "checked": True, "estimate": e}
    at = eventcache.as_of(seen, inp["who"]["char"])
    plan_store.save(env, inp["who"]["char"], {**e, "as_of": at} if at else e)
    return {"markdown": writer.fallback(e), "writer": "estimator", "checked": True, "estimate": e}


def status(env: dict, char: str | None = None, store=None, battlenet=None, gear_inputs=None) -> dict:
    """For the full view: the mount goal and how the last plan went."""
    inp = inputs(env, char, store, battlenet, gear_inputs)
    p = est_mod.pace(inp)
    m = est_mod.mounts(inp, p)
    revs = inp.get("plan_reviews") or []
    tiers = [{"level": t["level"], "need": est_mod.fgold(t["need_copper"]), "left": est_mod.fgold(t["left_copper"]),
              "bought": t["bought"], "may_be_trained": t.get("may_be_trained", False), "behind": t["behind"],
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
