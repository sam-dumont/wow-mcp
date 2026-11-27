"""The estimator: every number of a session plan, computed from the inputs
(wowstats.plan.facts), never by a model.

`estimate(inputs, hours, constraints)` returns the plan as data: blocks with
start offsets and durations, each with its reasons and where they came
from; checkpoints; the end state; one batched town stop; where to log out;
what to skip or abandon; a dungeon branch; the mount goals; the dungeon
ranking with the "redo it?" verdicts; the gear options per slot. `facts`
flattens every number into named, formatted values: the model's text may
use those and nothing else (wowstats.plan.write checks it).

The pace is the player's own: the route's planned time per step scaled by a
factor per kind of step (questing, travel, town, dungeon) measured on the
steps he played, with the sample size; the XP per hour of each kind from
the same steps; the time lost to unplanned stops from his sessions' AFK
share; and, once earlier plans have been compared with what happened
(wowstats.plan.store), their median error on top.
"""

from __future__ import annotations

import math
import re

from wowstats import ahsource, classguide, forever, gearfit, riding
from wowstats import gear as gearmod
from wowstats.rates import per_hour

from .facts import CITIES

KINDS = ("questing", "travel", "town", "dungeon")
BLOCK_S = 45 * 60          # short focused blocks: cut a long stretch at about this
MIN_BLOCK_S = 10 * 60      # shorter runs of steps join the next block
LONG_STEP_S = 25 * 60      # a single step longer than this is worth a look before doing it
FACTOR_MIN, FACTOR_MAX = 0.5, 6.0
MIN_STEPS = 5              # under this many measured steps, a kind takes the overall factor
DEFAULT_HOURS = 3.0
STALE_S = 24 * 3600        # a quest from a zone he left a day of play ago or more


# ---------------------------------------------------------------- formatting


def fdur(s) -> str:
    s = int(round(max(0, s or 0) / 60.0)) * 60
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}" if h else f"{m} min"


def fgold(c) -> str:
    if c is None:
        return "n/a"
    c = int(round(c))
    sign = "-" if c < 0 else ""
    g, rest = divmod(abs(c), 10000)
    s = rest // 100
    if g >= 100:
        return f"{sign}{g}g"
    return f"{sign}{g}g {s:02d}s" if g else f"{sign}{s}s"


def fint(n) -> str:
    return f"{int(round(n or 0)):,}"


def fpct(x) -> str:
    return f"{x * 100:.0f}%"


def flevel(lv: float) -> str:
    return f"{lv:.1f}"


# ---------------------------------------------------------------- constraints


# A request is a sentence or two. The cap keeps the patterns below (some
# backtrack on long runs of spaces) linear in practice, whatever is posted.
MAX_CONSTRAINTS = 500


def parse_constraints(text: str) -> dict:
    """Plain text to switches: 'no dungeons', 'only 2h', 'I want to level
    tailoring', 'group with friends', 'no professions'. What isn't
    understood is kept and said back. Only the first MAX_CONSTRAINTS
    characters are read, with runs of whitespace folded to one space."""
    t = " ".join((text or "")[: MAX_CONSTRAINTS * 4].split())[:MAX_CONSTRAINTS]
    low = t.lower()
    out: dict = {"text": t}
    m = re.search(r"(?:only|just|max(?:imum)?|about|for)? ?(\d+(?:[.,]\d+)?) ?(h|hours?|hrs?|min|minutes?)\b", low)
    if m:
        v = float(m.group(1).replace(",", "."))
        out["hours"] = v / 60 if m.group(2).startswith("m") else v
    if re.search(r"\bno (dungeons?|instances?|groups?)\b|\bsolo\b|without (a )?(group|dungeon)", low):
        out["no_dungeons"] = True
    if re.search(r"\b(group|friends?|party|with (my )?(mates|guild))\b", low) and not out.get("no_dungeons"):
        out["group"] = True
    goal = re.search(r"\b(?:for|prioriti[sz]e|focus on) (xp|gear)\b|\b(xp|gear) only\b", low)
    if goal:
        out["dungeon_goal"] = goal.group(1) or goal.group(2)
        low = low[:goal.start()] + " " + low[goal.end():]
    m = re.search(r"\b(level|skill|train|work on|do|push)\s+(?:up\s+)?(?:my\s+)?(tailoring|enchanting|cooking|"
                  r"first aid|fishing|alchemy|blacksmithing|leatherworking|engineering|herbalism|mining|"
                  r"skinning)\b", low)
    if m:
        out["profession"] = m.group(2).title()
    if re.search(r"\bno (professions?|crafting)\b", low):
        out["no_professions"] = True
    known = re.sub(r"(only|just|max(imum)?|about|for)? ?\d+(?:[.,]\d+)? ?(h|hours?|hrs?|min|minutes?)\b|"
                   r"no (dungeons?|instances?|groups?|professions?|crafting)|solo|without (a )?(group|dungeon)|"
                   r"group|friends?|party|i want to|level|skill|train|work on|push|tailoring|enchanting|cooking|"
                   r"first aid|fishing|alchemy|blacksmithing|leatherworking|engineering|herbalism|mining|"
                   r"skinning|with|my|and|,|\.|up", " ", low)
    rest = " ".join(w for w in known.split() if len(w) > 2)
    if rest:
        out["not_understood"] = rest
    return out


# ---------------------------------------------------------------- pace


def pace(inp: dict) -> dict:
    """Per kind of step: his non-AFK time over the route's planned time, and
    the XP per hour he made on those steps. Ratio of sums over the steps he
    spent time on (a skipped step counts as the time it really took)."""
    hist = [h for h in inp.get("step_history") or [] if (h.get("planned_s") or 0) > 0]
    tot_a = sum(h["actual_s"] for h in hist)
    tot_p = sum(h["planned_s"] for h in hist)
    # Under MIN_STEPS steps of his, the route's own pace (1.0), said so.
    overall = tot_a / tot_p if tot_p and len(hist) >= MIN_STEPS else 1.0
    out = {"overall": {"factor": round(overall, 2), "n": len(hist), "actual_s": round(tot_a),
                       "planned_s": round(tot_p)}}
    for k in KINDS:
        hs = [h for h in hist if h["kind"] == k]
        a, p = sum(h["actual_s"] for h in hs), sum(h["planned_s"] for h in hs)
        f = a / p if p else None
        used = f if f is not None and len(hs) >= MIN_STEPS else overall
        out[k] = {"factor": round(min(FACTOR_MAX, max(FACTOR_MIN, used)), 2), "measured": round(f, 2) if f else None,
                  "n": len(hs), "actual_s": round(a), "planned_s": round(p),
                  "from": "your steps" if f is not None and len(hs) >= MIN_STEPS else "your overall pace"
                  if len(hist) >= MIN_STEPS else "the route's own pace (too few of your steps measured)"}
    # XP per hour by kind, over every measured step (planned or not).
    allh = inp.get("step_history") or []
    for k in KINDS:
        hs = [h for h in allh if h["kind"] == k and h["actual_s"] > 0]
        a = sum(h["actual_s"] for h in hs)
        out[k]["xp_per_hour"] = round(sum(h["xp"] for h in hs) / a * 3600) if a > 600 else None
    cal = inp.get("calibration") or {}
    out["calibration"] = {"factor": cal.get("factor") or 1.0, "n": cal.get("n") or 0}
    # His last 5 finished levels against the route's clock: the Recorder's
    # planned time where it ran, the route reference's for the same level,
    # side and class before it (switched on later): one time per level,
    # so a level the two both cover counts once.
    lv = [{**x, "planned_s": x.get("planned_s") or route_seconds(inp, x["level"]), "rec": bool(x.get("planned_s"))}
          for x in inp.get("levels") or [] if x.get("played_s") and not x.get("partial") and x["level"] < inp["level"]]
    lv = [x for x in lv if x["planned_s"]][-5:]
    rec = sum(x["rec"] for x in lv)
    basis = ("the Recorder's route times" if lv and rec == len(lv) else
             "your level times against Polestar's" if not rec else
             "your level times against Polestar's, the Recorder's where it ran")
    out["level_factor"] = {"factor": round(sum(x["played_s"] for x in lv) / sum(x["planned_s"] for x in lv), 2)
                           if lv else None, "n": len(lv), "levels": [x["level"] for x in lv],
                           "from": basis if lv else None}
    lvx = [x for x in inp.get("levels") or [] if x.get("played_s") and x.get("xp") and not x.get("partial")
           and x["level"] < inp["level"]][-5:]
    out["xp_per_hour"] = {"value": round(sum(x["xp"] for x in lvx) / sum(x["played_s"] for x in lvx) * 3600)
                          if lvx else None, "n": len(lvx), "levels": [x["level"] for x in lvx]}
    ss = [s for s in inp.get("sessions") or [] if (s.get("online_s") or 0) > 600]
    on = sum(s["online_s"] for s in ss)
    out["stops"] = {"share": round(sum(s.get("afk_s") or 0 for s in ss) / on, 3) if on else 0.0, "n": len(ss)}
    # Nothing of his measured yet (a new character): the route's own clock,
    # a level's XP over its planned time, said so.
    planned = (inp.get("route_levels") or {}).get(str(inp["level"]))
    out["route_xp_per_hour"] = (round(xp_need(inp, inp["level"])[0] / planned * 3600) if planned else None)
    return out


def route_level(inp: dict, level: int) -> dict | None:
    """Polestar's route at `level` for this side, race and class (its time
    and its guide); None without a side: both sides' guides differ."""
    from wowstats.mcp import route

    who = inp.get("who") or {}
    if not who.get("faction"):
        return None
    return route.reference().level(level, who.get("faction"), who.get("class"))


def route_seconds(inp: dict, level: int) -> float | None:
    return (route_level(inp, level) or {}).get("expected_s")


def level_guide(inp: dict, level: int) -> str | None:
    """The title of Polestar's guide the route plays at `level`."""
    from wowstats.mcp import route

    g = (route_level(inp, level) or {}).get("guide")
    return ((route.reference().guides or {}).get(g) or {}).get("title") if g else None


# The one line that turns the estimate into a step-by-step plan.
UNLOCK = "Switch on Polestar's Recorder (/psrec on, then /reload) for a step-by-step plan."
UNLOCK_ADD = "Add Polestar's Recorder, then switch it on (/psrec on, then /reload) for a step-by-step plan."


# The Recorder's events arrive, but no guide step yet (just switched on):
# the step plan starts with the first step it sees.
UNLOCK_STEP = ("Open Polestar's guide and play a step with the Recorder on, then /reload: the step-by-step plan "
               "starts there.")
# Switched on in the setup report, nothing arrived yet.
UNLOCK_WAIT = ("Polestar's Recorder is on but nothing arrived yet: play a few minutes, then /reload, for a "
               "step-by-step plan.")


def unlock(inp: dict) -> str:
    """The one line under a plan by level, by what has arrived (facts.recorder_state)."""
    return {"missing": UNLOCK_ADD, "on": UNLOCK_STEP, "waiting": UNLOCK_WAIT}.get(inp.get("recorder") or "", UNLOCK)


def step_s(p: dict, kind: str, planned: float) -> float:
    return planned * p[kind]["factor"] * p["calibration"]["factor"]


def rate(p: dict, kind: str) -> float:
    """XP per hour of a kind of step: his, else his overall, else the
    route's (questing only; travel and town pay none unless he measured it),
    over the calibration (earlier plans' time needed over time planned)."""
    cal = p["calibration"]["factor"] or 1.0
    if p[kind].get("xp_per_hour") is not None:
        return p[kind]["xp_per_hour"] / cal
    if kind != "questing":
        return 0
    return (p["xp_per_hour"]["value"] or p.get("route_xp_per_hour") or 0) / cal


# ---------------------------------------------------------------- levels and XP


def xp_need(inp: dict, level: int) -> tuple[int, bool]:
    """XP level `level` needs: recorded, else extrapolated from the last two
    recorded levels (Forever's table isn't in the data); bool: extrapolated."""
    if level >= classguide.MAX_LEVEL:
        return 0, False
    t = {int(k): v for k, v in (inp.get("xp_table") or {}).items()}
    if level in t:
        return t[level], False
    ks = sorted(k for k in t if k < level)
    if len(ks) >= 2:
        a, b = t[ks[-2]], t[ks[-1]]
        # Never less than the last recorded level: a level in play whose XP
        # bar wasn't read gives its XP so far, and a falling line reached 0
        # and below, where add_xp never got to the end of a gain.
        return max(b, int(b + (b - a) * (level - ks[-1]))), True
    return (t[ks[-1]] if ks else 50000), True


def add_xp(inp: dict, level: int, xp: float, gain: float) -> tuple[int, float, bool]:
    guessed = False
    while gain > 0 and level < classguide.MAX_LEVEL:
        need, g = xp_need(inp, level)
        guessed |= g
        if xp + gain < need:
            return level, xp + gain, guessed
        gain -= need - xp
        level, xp = level + 1, 0.0
    return level, xp, guessed


def lfloat(inp: dict, level: int, xp: float) -> float:
    return min(classguide.MAX_LEVEL, level + xp / max(1, xp_need(inp, level)[0]))


# ---------------------------------------------------------------- dungeons


def dkey(name: str) -> str:
    """dungeonview.key's rule: a wing of a dungeon run in wings keeps its wing."""
    from .dungeonview import key

    return key(name)


def expected_best(items: list[dict]) -> float:
    """Expected gain of one clear: per slot, the chance-weighted best of the
    items that can drop for it (a better one dropping makes a worse one moot)."""
    by_slot: dict[str, list] = {}
    for it in items:
        if it.get("chance"):
            by_slot.setdefault(it["slot"] or "", []).append(it)
    total = 0.0
    for its in by_slot.values():
        miss = 1.0
        for it in sorted(its, key=lambda x: -x["gain"]):
            p = it["chance"] / 100.0
            total += it["gain"] * p * miss
            miss *= 1 - p
    return total


def dungeon_items(inp: dict) -> dict[str, dict]:
    """Upgrades from instance drops and dungeon quests, by instance (wing)."""
    from . import wings

    out: dict[str, dict] = {}
    for u in inp.get("upgrades") or []:
        for s in u.get("sources") or []:
            if s["kind"] == "quest":  # the dungeon it's done in, not its giver's zone (forever.quest_dungeon)
                aid = forever.quest_dungeon(s.get("quest_id"))
                area = forever.area(aid) or {}
                # A dungeon run in wings: each wing the quest is done in
                # (wowstats.plan.wings), with the wing's range.
                q = wings.quest(area.get("name") or "", s.get("quest_id")) if area else None
                places = [(w["name"], w["levels"]) for _, w in wings.split(str(aid), area)
                          if q and w["wing"] in q["wings"]] if q and area.get("levels") else []
                if not places and area.get("name") and not (q and q["how"] == "outside"):
                    places = [(area["name"], s.get("levels"))]
            else:
                inst = s.get("zone") if (s.get("instance") == "dungeon" or s.get("dungeon")) else None
                places = [(inst, s.get("levels"))] if inst else []
            for inst, lv in places:
                _dungeon_item(out, inst, lv, u, s)
    return out


def _dungeon_item(out: dict, inst: str, levels, u: dict, s: dict) -> None:
    """One upgrade's source under its instance (or wing) in dungeon_items."""
    d = out.setdefault(inst, {"name": inst, "levels": levels, "drops": [], "quests": []})
    row = {"item": u.get("name"), "item_id": u.get("item_id"), "slot": u.get("slot_name"),
           "gain": u.get("value", u.get("gain")) or 0, "req_level": u.get("level"),
           "now": bool(u.get("now")), **gearmod.badge_of(u), "line": gearmod.gain_words(u)}
    if s["kind"] == "drop":
        d["drops"].append({**row, "boss": s.get("boss") or "trash", "chance": s.get("chance_pct")})
    elif s["kind"] == "quest":
        d["quests"].append({**row, "quest": s.get("name"), "quest_id": s.get("quest_id")})


def seen_mean(trips: list[dict], k: str) -> float | None:
    """A trip leg's mean over the trips that saw it (dungeon_trips: None
    for a leg the records didn't see, never 0), None when none did."""
    seen = [t[k] for t in trips or [] if t.get(k) is not None]
    return sum(seen) / len(seen) if seen else None


def trip_stats(trips: list[dict]) -> dict:
    """His dungeon trips, phase by phase (means over the trips that saw
    each phase, None when none did), with how many back each."""
    keys = ("pickup_s", "travel_to_s", "inside_s", "travel_back_s", "handin_s", "online_s")
    n = len(trips)
    if not n:
        return {"n": 0}
    out = {k: seen_mean(trips, k) for k in keys}
    out["online_s"] = out["online_s"] or 0
    inside = sum(t.get("inside_s") or 0 for t in trips)
    xp_in = sum(t.get("xp_inside") or 0 for t in trips)
    whole = sum((t.get("xp_inside") or 0) + (t.get("xp_quests") or 0) for t in trips)
    out.update(n=n, xp_inside_per_hour=xp_in / inside * 3600 if inside else None,
               xp_whole_per_hour=whole / out["online_s"] / n * 3600 if out["online_s"] else None,
               # the dungeons' quests handed in outside after the runs, in the whole
               quests_outside=sum(t.get("quests_outside") or 0 for t in trips),
               dungeons=[t["dungeon"] for t in trips],
               runs_text=", ".join(f"{t['dungeon']} at level {t.get('level_start')}" for t in trips))
    return out


def dungeons(inp: dict, p: dict, mount: dict) -> list[dict]:
    """Every dungeon in range now or within 5 levels, valued as a whole trip
    from his own trips, with the gear it holds for him and, for those he has
    done, the value of one more run and of looping it."""
    from . import vsguide, wings
    from .dungeongain import drop_value
    from .dungeonview import build as panel
    from .dungeonview import drop_items, gain_named, known_areas, other_sides
    from .dungeonview import key as pkey

    level = inp["level"]
    # Polestar's list against each (wowstats.plan.vsguide), as the Dungeons
    # panel has it: one answer for the panel, the Overview and the MCP.
    blocks = {pkey(b["name"]): b for b in panel(inp)["dungeons"]}
    by_inst = dungeon_items(inp)
    ts = trip_stats(inp.get("trips") or [])
    quest_xph = rate(p, "questing") if level < classguide.MAX_LEVEL else 0
    open_ids = {o["quest_id"]: o for o in inp.get("open_quests") or []}
    kb_runs = {dkey(k): v for k, v in (inp.get("dungeon_runs_kb") or {}).items()}
    names = {}
    for d in inp.get("dungeons") or []:
        # A dungeon run in wings: one entry per wing (wowstats.plan.wings).
        aid = forever.area_id(d["name"])
        whole = {**(forever.area(aid) or {}), "name": d["name"], "levels": [d["min"], d["max"]]}
        split = wings.split(str(aid), whole) if wings.dungeon(d["name"]) and ":" not in d["name"] else []
        for _, w in split or [("", None)]:
            e = {**d, "name": w["name"], "min": w["levels"][0], "max": w["levels"][1]} if w else d
            names[dkey(e["name"])] = e
    for inst, d in by_inst.items():  # wings and Forever's own instances the area list names once
        k = dkey(inst)
        if k not in names and d.get("levels"):
            names[k] = {"name": inst, "min": d["levels"][0], "max": d["levels"][1]}
    for k, b in blocks.items():  # older inputs can predate a dungeon in the current panel
        names.setdefault(k, {"name": b["name"], "min": b["min"], "max": b["max"]})
    known = known_areas()
    theirs = {pkey(a["name"]) for aid, a in known.items() if other_sides(inp, known, aid)}
    order = {k: i for i, k in enumerate(blocks)}
    out = []
    for k, d in names.items():
        lo, hi = d["min"], d["max"]
        if hi < level - 2 or lo > level + 5 or pkey(d["name"]) in theirs:
            continue
        b = blocks.get(k)
        rewards = b["quest_rewards"] if b else []
        given = {r["item_id"] for r in rewards}
        gear = ([g for g in drop_items(b["drops"]) if g["item_id"] not in given] if b else
                [g for inst, v in by_inst.items() if dkey(inst) == k for g in v["drops"]])
        now_items = [g for g in gear if g["now"]]
        later = [g for g in gear if not g["now"]]
        # Keep the planner's item/quest field names for its callers.
        qrew = [{**r, "item": r["name"], "slot": r["slot_name"], "req_level": r["level"],
                 "quest_id": r["quest"]["quest_id"], "quest": r["quest"]["name"]}
                for r in rewards]
        odds = b["gain"] if b else drop_value(now_items)
        run = kb_runs.get(k) or {}
        quests_open = [open_ids[qq] for qq in (run.get("objectives") or []) + (run.get("pickups") or [])
                       if qq in open_ids]
        quest_xp = sum((o.get("xp_full") or 0) * (share(inp, level, o.get("level")) or 0) for o in quests_open)
        mine = [t for t in inp.get("trips") or [] if dkey(wings.trip_name(t)) == k]
        v = vsguide.named((b or {}).get("vs_guide"), d["name"])
        # His quests of it in the log count whether or not Polestar has a
        # run of it (the panel's quests: forever-db's zone, Polestar's run).
        quest_xp = max(quest_xp, (v or {}).get("numbers", {}).get("quest_xp_in_log") or 0)
        # Fits now: on Polestar's list for his level, or a reason measured
        # on him to go anyway (vsguide); a dungeon under Polestar's floor
        # waits for it otherwise, whatever its gear.
        fits = b["fits_now"] if b else lo <= level <= hi
        entry = {"name": d["name"], "min": lo, "max": hi, "fits_now": fits, "vs_guide": v,
                 "opens_at": lo if lo > level else None, "needs_group": True,
                 "verdict": b["verdict"] if b else None, "why": b["why"] if b else None,
                 "priority": b["value"] if b else None, "goals": b["goals"] if b else [],
                 "top_items": sorted(now_items + later, key=lambda g: -(g["gain"] * (g["chance"] or 0)))[:4],
                 "expected_gain_per_clear": odds["expected_gain_per_clear"],
                 "known_gain_per_clear": odds["known_gain_per_clear"], "items_now": len(now_items),
                 "items_later": len(later), "quest_rewards": qrew[:3],
                 "deferred_quest_rewards": b["deferred_quest_rewards"] if b else [],
                 "quests_open": [{"quest_id": o["quest_id"], "name": o["name"]} for o in quests_open],
                 "quest_xp_open": round(quest_xp)}
        # One more run, the same estimate the Overview's move and the
        # Dungeons panel show (wowstats.plan.dungeongain): his runs there,
        # else the kill estimate's time inside and Polestar's pick-up and
        # hand-in rules on his quests (wowstats.plan.tripcost); the kill
        # estimate counts in the XP and the ranking, labelled.
        g = b["gain"] if b else gain_named(inp, d["name"], quest_xph)
        entry["gain"] = g
        if g and g["run_s"]:
            xp_trip = g["xp"] if g["xp"] is not None else g["xp_quests"]
            legs = ("pickup_s", "travel_to_s", "inside_s", "travel_back_s", "handin_s")
            entry["trip"] = {"time_s": g["run_s"], "xp": xp_trip, "xp_per_hour": g["trip"]["xp_per_hour"]["value"],
                             "kills_unknown": g["kills_unknown"], "from": _trip_from(g, ts), "n": g["own_runs"],
                             "phases_s": {leg: g["trip"][leg]["value"] for leg in legs}}
        else:
            entry["trip"] = None
        if mine:
            entry["redo"] = redo(inp, mine, entry, mount, quests_open, g)
        g_per_h = (round(entry["expected_gain_per_clear"] * 3600 / entry["trip"]["time_s"], 1)
                   if entry.get("trip") and entry["expected_gain_per_clear"] is not None else None)
        entry["value"] = {"xp_per_hour": g["xp_per_hour"] if g and not g["kills_unknown"] else None,
                          "gear_per_hour": g_per_h, "vs_questing_xp_per_hour": round(quest_xph)}
        out.append(entry)
    # The same order as the panel. Its whole-trip XP/h already counts quest hand-ins.
    out.sort(key=lambda e: order.get(dkey(e["name"]), len(order)))
    for i, e in enumerate(out, 1):
        e["rank"] = i
    return out


def gear_estimate(points, known) -> str:
    if points is not None:
        return f"+{points} points"
    return f"+{known} points from known rates" if known is not None else "drop rates unknown"


def _trip_from(g: dict, ts: dict) -> str:
    """Where one more run's time comes from, in words: his runs there or
    the time inside's estimate, then where the legs outside come from."""
    head = (f"your {g['own_runs']} run(s) there" if g["basis"] == "runs" else
            "the kill estimate's time inside" if g["basis"] == "kill_model" else "the knowledge base's clear time")
    t = g["trip"]
    labels = {(t.get(k) or {}).get("label") for k in ("pickup_s", "travel_s", "handin_s")}
    if labels & {"route", "your pace"}:
        what = ("the legs your runs didn't see from Polestar's rules, from where you are" if g["basis"] == "runs"
                else "the pickups, travel and hand-ins Polestar's rules plan from where you are")
        return f"{head}, plus {what}{_at_pace(t.get('pace'))}"
    if g["basis"] != "runs" and ts["n"] and g["time"] == "trip":
        return f"{head}, plus the travel and quest time of your {ts['n']} dungeon trips"
    return head


def _at_pace(p: dict | None) -> str:
    """", at your pace (2.1x the straight run, from 2 trips)": how the
    route's legs were scaled (dungeonview.pace); nothing for the straight run."""
    if not p or not p.get("trips"):
        return ""
    n = p["trips"]
    return f", at your pace ({p['factor']:g}x the straight run, from {n} trip{'s' if n != 1 else ''})"


def share(inp: dict, level: int, qlevel) -> float | None:
    """The hand-in share by the KB's rule, None when the KB has none."""
    from .facts import xp_share

    if level >= classguide.MAX_LEVEL:
        return 0.0
    return xp_share(level, qlevel, (inp.get("rules") or {}).get("quest_xp_share"))


def redo(inp: dict, mine: list[dict], entry: dict, mount: dict, quests_open: list,
         gain: dict | None = None) -> dict:
    """One more run of a dungeon he has done, from his runs of it: the time
    (travel there, inside, travel back; pickup and hand-in only while quests
    remain), the XP of the run (`gain`, the shared estimate: the kills
    carried to his level now, None when they can't be), the gear still to
    drop, the loot's value, and looping it from the door."""
    n = len(mine)
    mean = lambda k: sum(t.get(k) or 0 for t in mine) / n  # noqa: E731
    inside = mean("inside_s")
    # Each leg over the runs that saw it; one none saw leaves the trip's
    # time unknown, never a walk in 0 s.
    legs = [seen_mean(mine, k) for k in ("travel_to_s", "travel_back_s",
                                         *(("pickup_s", "handin_s") if quests_open else ()))]
    run_s: float | None = inside + sum(legs) if None not in legs else None
    xp_in: float | None = mean("xp_inside")
    xph_run = per_hour(xp_in, run_s) if run_s else None
    if gain is not None:
        # The shared estimate's (wowstats.plan.dungeongain): the kills at
        # his level now, the trip with the quests it still has, its unseen
        # legs from the rules.
        run_s = gain["run_s"]
        xp_in = gain["xp_kills"]
        xph_run = gain["xp_per_hour"] if not gain["kills_unknown"] else None
    lvls = sorted({t.get("level_start") for t in mine})
    loot = [t.get("loot") or {} for t in mine]
    items_n = sum(x.get("items") or 0 for x in loot) / n
    vendor = sum(x.get("vendor_copper") or 0 for x in loot) / n
    ah = [x.get("ah_copper") for x in loot if x.get("ah_copper")]
    boe = [b for x in loot for b in x.get("boe") or []]
    gold_run = vendor + (sum(ah) / len(ah) if ah else 0)
    # A loop can't go faster than the instance limit lets it re-enter
    # (instance.entries_per_hour in the KB): five an hour means 12 minutes a run
    # at best, however fast the clear.
    cap = (inp.get("instance_limit") or {}).get("per_hour") if isinstance(inp.get("instance_limit"), dict) \
        else None
    loop_s = max(inside, 3600 / cap) if inside and cap else inside
    hours_loop = loop_s / 3600 if loop_s else None
    out = {"runs": n, "levels": lvls, "time_s": round(run_s) if run_s else None, "inside_s": round(inside),
           "xp_inside": round(xp_in) if xp_in is not None else None, "xp_per_hour_run": xph_run,
           "loot_items_per_run": round(items_n, 1), "vendor_copper_per_run": round(vendor),
           "boe_seen": [{"name": b.get("name"), "quality": b.get("quality"), "ah_copper": b.get("ah_copper")}
                        for b in boe][:5],
           "ah_copper_per_run": round(sum(ah) / len(ah)) if ah else None, "quests_left": len(quests_open),
           "instance_limit": inp.get("instance_limit") or "unknown (no Forever source in the knowledge base)"}
    if hours_loop:
        out["loop"] = {"run_s": round(loop_s), "note": "inside time only; the walk out and back in to reset "
                                                        "isn't measured" + (
                                                            f"; at most {cap} entries an hour to this dungeon, "
                                                            "so a run takes "
                                                            f"{round(3600 / cap / 60)} minutes at best"
                                                            if cap and loop_s > inside else ""),
                       "xp_per_hour": per_hour(xp_in, loop_s) if xp_in is not None else None,
                       "gold_copper_per_hour": round(gold_run / hours_loop),
                       "gear_per_hour": (round(entry["expected_gain_per_clear"] / hours_loop, 1)
                                         if entry["expected_gain_per_clear"] is not None else None)}
        goal = mount.get("current") or {}
        left = goal.get("left_copper")
        if left and gold_run > 0:
            out["loop"]["runs_for_mount"] = math.ceil(left / gold_run)
            out["loop"]["time_for_mount_s"] = round(math.ceil(left / gold_run) * loop_s)
    best = max(entry["top_items"], key=lambda g: g["gain"] * (g["chance"] or 0), default=None)
    if best and best.get("chance"):
        out["best_item"] = {**best, "expected_runs": round(100 / best["chance"], 1),
                            "expected_time_s": round(100 / best["chance"] * run_s) if run_s else None}
    # This verdict is for the next whole trip. The repeatable loop from the door
    # has its own XP and gold rates above, without the one-time quest hand-ins.
    out["verdict"] = entry["verdict"] or "trip value unknown"
    out["why"] = entry["why"] or "no whole-trip verdict for this dungeon"
    return out


# ---------------------------------------------------------------- mounts


def mounts(inp: dict, p: dict) -> dict:
    """Both riding goals: what they cost, the gold now, his net gold per
    hour, and when he'd have it, in played hours and in levels at his pace.
    The savings roll over to the epic once the first is bought."""
    m = inp.get("mount") or {}
    rate_c = (inp.get("gold_rate") or {}).get("copper_per_hour")
    gold = inp.get("gold_copper")
    lf = p["level_factor"]["factor"] or p["overall"]["factor"]
    route = {int(k): v for k, v in (inp.get("route_levels") or {}).items()}
    level, xp = inp["level"], inp["xp"]
    tiers = []
    have = m.get("riding_skill") or 0
    # Only Altoholic's spellbook reads riding from the game (wowstats.riding):
    # the Recorder misses a training it didn't see, so a tier not seen
    # trained at or past its level may be (2026-11-24, the profession caps'
    # blind spot). Under its level it can't be.
    seen_in_game = m.get("riding_from") == "game"
    committed = 0
    missing: list[str] = []
    log = {o["quest_id"] for o in inp.get("open_quests") or []}
    for t in m.get("tiers") or []:
        quest = riding.class_route(t.get("skill"), inp.get("who") or {}, inp.get("quests_done") or (), log)
        paid = t["training_copper"] + (0 if t.get("comes_with_mount") else (t.get("vendor_mount_copper") or 0))
        need = quest["remaining_copper"] if quest else paid
        bought = bool(t.get("skill") and have >= t["skill"] or quest and quest["fulfilled"])
        if quest and need is None and not bought:
            missing.append(f"remaining quest costs for {quest['name']}")
        hrs_to_level = played_to(inp, route, lf, t["level"])
        # No route time to that level (the route stops before it): what is
        # known still comes, the gold at that level is unknown, never guessed.
        at_level_gold = (max(0, gold + rate_c * hrs_to_level / 3600 - committed)
                         if None not in (gold, rate_c, hrs_to_level, committed) else None)
        if hrs_to_level is None and not bought:
            missing.append(f"time to level {t['level']}: the route has no clock that far")
        target = committed + need if need is not None and committed is not None else None
        left = max(0, target - gold) if gold is not None and target is not None else None
        if bought or target == 0:
            left = 0
        eta_s = 0 if left == 0 else left / rate_c * 3600 if left is not None and rate_c and rate_c > 0 else None
        tiers.append({"name": t["name"], "level": t["level"], "need_copper": need,
                      # Forever's training comes with a mount: the vendor's
                      # price is then a second, optional mount, not part of need.
                      "training_copper": 0 if quest else t["training_copper"],
                      **({"quest": quest, "trainer_alternative_copper": paid} if quest else {}),
                      "mount_copper": None if t.get("comes_with_mount") else t.get("vendor_mount_copper"),
                      "extra_mount_copper": t.get("vendor_mount_copper") if t.get("comes_with_mount") else None,
                      "comes_with_mount": bool(t.get("comes_with_mount")), "bought": bought,
                      "may_be_trained": not bought and not seen_in_game and level >= t["level"],
                      "source": t.get("source"),
                      "prior_cost_copper": committed, "target_copper": target, "left_copper": left,
                      "eta_played_s": round(eta_s) if eta_s is not None else None,
                      "eta_level": level_at(inp, route, lf, eta_s) if eta_s is not None else None,
                      "played_to_level_s": round(hrs_to_level) if hrs_to_level is not None else None,
                      "gold_at_level_copper": round(at_level_gold) if at_level_gold is not None else None,
                      "behind": bool(not bought and None not in (at_level_gold, need) and at_level_gold < need),
                      "discounted_copper": round(need * (1 - m["discount"]["pct"] / 100))
                      if m.get("discount") and not quest else None})
        if not bought:
            committed = target
    current = next((t for t in tiers if not t["bought"]), None)
    if current and current["may_be_trained"]:
        missing.append(f"whether {current['name']} is trained: no read saw it (Altoholic's spellbook, read at each "
                       "login, would; Polestar Recorder misses a training it didn't see), so it counts as not "
                       "trained yet")
    return {"tiers": tiers, "current": current, "gold_copper": gold, "gold_rate": inp.get("gold_rate"),
            "behind": bool(current and current["behind"]), "discount": m.get("discount"),
            "unknown": (m.get("unknown") or []) + missing, "level": level, "xp": xp}


def played_to(inp: dict, route: dict, factor: float, target: int) -> float | None:
    """Played seconds to reach `target` at his pace: the route's time for
    each level left (his class), times his measured level factor."""
    level = inp["level"]
    if target <= level:
        return 0.0
    frac = 1 - inp["xp"] / max(1, xp_need(inp, level)[0])
    secs = 0.0
    for lv in range(level, target):
        r = route.get(lv)
        if r is None:
            return None
        secs += r * (frac if lv == level else 1)
    return secs * (factor or 1)


def level_at(inp: dict, route: dict, factor: float, played_s: float) -> float | None:
    level = inp["level"]
    frac = 1 - inp["xp"] / max(1, xp_need(inp, level)[0])
    left = played_s
    lv = level
    while lv in route and lv < classguide.MAX_LEVEL:
        r = route.get(lv)
        if r is None:
            return None
        cost = r * (frac if lv == level else 1) * (factor or 1)
        if left < cost:
            start = (1 - frac) if lv == level else 0
            return round(lv + start + (left / (r * (factor or 1))), 1)
        left -= cost
        lv += 1
    return float(lv) if lv == classguide.MAX_LEVEL else None


# ---------------------------------------------------------------- gear options


def gear_options(inp: dict, p: dict, dlist: list[dict], mount: dict, zone: str | None = None) -> list[dict]:
    """Per slot, every way to an upgrade with what it costs: already in his
    bags or bank (no trip, first), a quest (on his route or not), a dungeon
    drop (the trip's time and the chance), a world drop (luck, and his own
    AH price when he scanned it, weighed against the mount savings), a
    craft. Ranked by gain per hour where there is a time, luck last.

    Each option says whether its source is in `zone` (the zone the
    character is in now; the inputs' own when not given), by the advice's
    rule: without it a model read the zone off sessions and counted three
    quest upgrades where `gear` marked one (eval-mcp, zone_quest_upgrade)."""
    zone = inp.get("zone") if zone is None else zone
    trip = {dkey(d["name"]): d for d in dlist}
    ahead = {s["title"] for s in inp.get("route_ahead") or []}
    level = inp["level"]
    slots: dict[str, list] = {}
    for u in inp.get("upgrades") or []:
        for s in u.get("sources") or []:
            # "now" is usable now: wearable at this level and, from a craft,
            # makeable now (skill and recipe). A craft still to skill up is
            # some effort (gear.source_effort), never "now" (MCP eval
            # pve_shoulder, 2026-11-24: Green Silken Shoulders, Tailoring 141
            # of 165, said "available": "now").
            craft_wait = s["kind"] == "craft" and not s.get("can_make")
            now = bool(u.get("now")) and not craft_wait
            row = {"item": u.get("name"), "item_id": u.get("item_id"), "gain": round(u.get("gain") or 0, 1),
                   **gearmod.badge_of(u), "line": gearmod.gain_words(u), "req_level": u.get("level"),
                   "now": now,
                   "available": "now" if now else f"at {u.get('level')}" if not u.get("now")
                   else "once you can make it",
                   # The ranking's value and who it's built for (wowstats.gearfit).
                   "value": u.get("value", u.get("gain")) or 0,
                   "fit": gearfit.text(u.get("fit")), "in_current_zone": gearmod.in_zone(s, zone)}
            if with_text(u):
                row["worn_with"] = with_text(u)
            if s["kind"] in ("bags", "bank") and not partner_to_get(u):
                row.update(kind=s["kind"], source=f"in your {s['kind']}",
                           cost="equip it" if s["kind"] == "bags" else "take it out at a banker and equip it")
            elif s["kind"] in ("bags", "bank"):  # half a weapon setup: the other half is the errand
                row.update(kind="setup", source=f"in your {s['kind']}", cost=f"first get {with_text(u)}")
            elif s["kind"] == "quest":
                # What it really takes (wowstats.questwork): where the route
                # hands it in, the chain and its next quest, the foe, the
                # dungeon; a plain quest is a hand-in in its zone.
                on = bool((s.get("work") or {}).get("route")) or s.get("name") in ahead
                row.update(kind="quest", source=s.get("name"), zone=s.get("zone"),
                           cost=s.get("work_says") or ("on your route" if on else
                                                       f"a quest in {s.get('zone') or 'another zone'}"),
                           effort=s.get("effort"), on_route=on, choices=s.get("choices"),
                           dungeon_quest=bool(s.get("dungeon")))
            elif s["kind"] == "drop" and (s.get("instance") == "dungeon" or s.get("dungeon")):
                d = trip.get(dkey(s.get("zone") or ""))
                t = ((d or {}).get("trip") or {}).get("time_s")
                ch = s.get("chance_pct")
                cost_s = round(t * 100 / ch) if t and ch else None
                cost = f"{fdur(cost_s)} of dungeon trips on average" if cost_s else "a dungeon trip"
                row.update(kind="dungeon", source=f"{s.get('boss') or 'trash'} in {s.get('zone')}", chance=ch,
                           cost_s=cost_s, cost=cost + (f" ({ch:g}% per clear)" if ch is not None
                                                       else " (drop rate unknown)"))
            elif s["kind"] == "drop" and s.get("instance") in (None, ""):
                ch = s.get("chance_pct")
                row.update(kind="world drop", source=gearmod.source_text(s), chance=ch,
                           cost=f"luck ({ch}% a kill)" if ch else "luck, no drop rate known", effort=s.get("effort"),
                           lucky_bonus=True)
                if u.get("ah_copper"):
                    left = ((mount.get("current") or {}).get("left_copper")) or 0
                    row["ah_copper"] = u["ah_copper"]
                    row["ah_note"] = ("buying it delays the mount" if left and u["ah_copper"] > 0 else "")
            elif s["kind"] == "craft":
                # "now" is the level to wear it; making it also needs the
                # skill and the recipe (wowstats.gear.craft_status).
                row.update(kind="craft", source=f"{s.get('profession')} {s.get('skill')}", can_make=s.get("can_make"),
                           cost=gearmod.craft_status(s))
            else:  # ForeverDB's other kinds (vendor, container...): the gear advice's words
                row.update(kind=s["kind"], source=gearmod.source_text(s),
                           cost=(f"buy it ({ahsource.price_text(s['price_copper'])})"
                                 if s["kind"] == "vendor" and s.get("price_copper") else "see the source"))
            if s.get("basis"):
                row["basis"] = s["basis"]
            slots.setdefault(u.get("slot_name") or "?", []).append(row)
    out = []
    for slot, rows in slots.items():
        def key(r):
            luck = r["kind"] == "world drop"
            hours = (r.get("cost_s") or 0) / 3600
            # An errand counts thrice an hour's worth, unless its work makes it
            # more than a hand-in (wowstats.questwork).
            errand = r["kind"] == "craft" or (r["kind"] == "quest" and r.get("effort") in (None, "easy"))
            per_h = r["value"] / hours if hours else r["value"] * (3 if errand else 1)
            unmeasured = r["kind"] == "dungeon" and r["cost_s"] is None
            return (not r["now"], r["kind"] not in ("bags", "bank"), luck, unmeasured, -per_h,
                    r.get("chance") is None, -(r.get("chance") or 0))

        rows.sort(key=key)
        kept = {}
        for r in rows:
            k = (r["item_id"], r["kind"])
            if k not in kept:
                kept[k] = r
            elif r["kind"] == "dungeon" and r["source"] != kept[k]["source"]:
                alternatives = kept[k].setdefault("alternatives", [])
                if not any(a["source"] == r["source"] for a in alternatives):
                    alternatives.append({field: r.get(field) for field in
                                         ("source", "chance", "cost_s", "cost", "basis")})
        out.append({"slot": slot, "options": list(kept.values())[:4]})
    out.sort(key=lambda s: -max((o["value"] for o in s["options"] if o["now"]), default=0))
    _ = level
    return out


# ---------------------------------------------------------------- the plan


CONTEXTS = ("levelling", "max-level-pve", "pvp")


def default_context(inp: dict) -> str:
    """Levelling while the route still has a level ahead of him; past the
    route's last level, max-level PvE."""
    top = max((int(k) for k in (inp.get("route_levels") or {})), default=None)
    levelling = inp["level"] < classguide.MAX_LEVEL and (top is None or inp["level"] <= top)
    return "levelling" if levelling else "max-level-pve"


def unsupported(inp: dict, context: str) -> dict:
    return {"schema": 1, "char": (inp.get("who") or {}).get("char"), "made_t": inp.get("now_t"),
            "context": context, "supported": False,
            "message": (f"Planning for {context} isn't built yet: only levelling is. The knowledge base has what "
                        f"is known about it (kb_search with scope forever)."), "facts": {}}


def estimate(inp: dict, hours: float | None = None, constraints: str = "", context: str | None = None) -> dict:
    context = context or default_context(inp)
    if context not in CONTEXTS:
        raise ValueError(f"context is one of {', '.join(CONTEXTS)}")
    if inp["level"] >= classguide.MAX_LEVEL and context == "levelling":
        context = "max-level-pve"
    if context != "levelling":
        return unsupported(inp, context)
    # Without the Recorder there is no step to plan from: the plan goes by
    # level instead, from his level times (or the route's clock), and says
    # it is an estimate and what unlocks the step-by-step one.
    by_level = not inp.get("route_ahead")
    p = pace(inp)
    if by_level and not p["xp_per_hour"]["value"] and not inp.get("route_levels"):
        out = unsupported(inp, context)
        out["message"] = ("Not enough to plan from yet: finish one level with Polestar on, then log out. "
                          + unlock(inp))
        out["unlock"] = unlock(inp)
        return out
    cons = parse_constraints(constraints)
    hours = float(cons.get("hours") or hours or DEFAULT_HOURS)
    hours = max(0.5, min(hours, 8.0))
    level, xp = inp["level"], float(inp["xp"])
    need0, _ = xp_need(inp, level)
    budget = hours * 3600
    stops = p["stops"]["share"]
    play = budget * (1 - stops)
    mount = mounts(inp, p)
    dl = dungeons(inp, p, mount)
    rested = float(inp.get("rested_xp") or 0)
    # Rested XP his kills use, as measured (rested used per kill XP); none
    # measured: no bonus counted (unknown, not assumed).
    burn = (inp.get("rested_burn") or {}).get("per_kill_xp")
    kill_share = (inp.get("kill_xp_share") or 0.0) * (burn or 0.0)
    gold_rate = (inp.get("gold_rate") or {}).get("copper_per_hour")

    # Steps ahead, with his time for each.
    steps = []
    for s in inp.get("route_ahead") or []:
        steps.append({**s, "est_s": step_s(p, s["kind"], s["planned_s"] or 0)})

    long_ahead = []
    for s in steps[:120]:
        if s["est_s"] > LONG_STEP_S and s["kind"] == "questing":
            long_ahead.append(s)

    # Group into blocks, cut at travel and at about BLOCK_S.
    groups: list[list[dict]] = []
    cur: list[dict] = []
    acc = 0.0
    for s in steps:
        brk = cur and (s["kind"] == "travel" or acc >= BLOCK_S or s["guide"] != cur[-1]["guide"])
        if brk and acc >= MIN_BLOCK_S:
            groups.append(cur)
            cur, acc = [], 0.0
        cur.append(s)
        acc += s["est_s"]
    if cur:
        groups.append(cur)

    blocks: list[dict] = []
    # A character with no finished level measured: levels from the route's clock.
    route_lv = {int(k): v for k, v in (inp.get("route_levels") or {}).items()}
    route_mode = p["xp_per_hour"]["value"] is None and route_lv != {}
    t = 0.0
    lv, x = level, xp
    group_mode = cons.get("group") and not cons.get("no_dungeons")
    prof = cons.get("profession")
    fitting_trips = session_trips(dl, play, cons.get("dungeon_goal"), group_mode)
    best_d = next(iter(fitting_trips), None)

    def add_block(kind, title, dur, xp_gain, why, extra=None):
        nonlocal t, lv, x, rested
        dur = min(dur, play - t)
        if dur <= 0 or (dur <= 60 and not (extra or {}).get("ding")) or lv >= classguide.MAX_LEVEL:
            return False
        frac = dur / extra.pop("_full", dur) if extra and "_full" in extra else 1.0
        if route_mode:  # levels from the route's clock; XP amounts unknown (no XP table past this level)
            to = level_at(inp, route_lv, 1.0 * p["calibration"]["factor"], t + dur)
            gain = bonus = None
            lv2, x2, guessed = (int(to), (to - int(to)) * xp_need(inp, int(to))[0], False) if to else (lv, x, False)
        else:
            gain = xp_gain * frac
            bonus = min(rested, gain * kill_share)
            lv2, x2, guessed = add_xp(inp, lv, x, gain + bonus)
            if lv2 == classguide.MAX_LEVEL:
                room = sum(xp_need(inp, i)[0] for i in range(lv, lv2)) - x
                gain = min(gain, room)
                bonus = min(bonus, room - gain)
            rested -= bonus
        b = {"id": f"b{len(blocks) + 1}", "kind": kind, "title": title, "start_s": round(t),
             "dur_s": round(t + dur) - round(t),
             "xp": round(gain) if gain is not None else None,
             "rested_bonus": round(bonus) if bonus is not None else None,
             "level_from": round(lfloat(inp, lv, x), 2), "route_clock": route_mode,
             "level_to": round(lfloat(inp, lv2, x2), 2), "xp_table_extrapolated": guessed, "why": why}
        if extra:
            b.update(extra)
        blocks.append(b)
        t += dur
        lv, x = lv2, x2
        return True

    if group_mode and best_d:
        tr = best_d["trip"]
        add_block("dungeon", f"{best_d['name']} with your group", tr["time_s"], tr["xp"],
                  [{"text": f"{best_d['verdict']}. Whole trip about {fdur(tr['time_s'])}", "from": tr["from"]},
                   {"text": ("expected gear per clear: " + gear_estimate(best_d["expected_gain_per_clear"],
                                                                        best_d.get("known_gain_per_clear"))),
                    "from": "your gear advice, drop chances"}], {"dungeon": best_d["name"]})

    if prof and not cons.get("no_professions"):
        pr = next((x for x in inp.get("professions") or [] if (x.get("name") or "").lower() == prof.lower()), None)
        r = (inp.get("profession_rate") or {}).get(prof) or {}
        if pr:
            per_h = r["skill_ups"] / r["seconds"] * 3600 if r.get("seconds") else None
            dur = 30 * 60
            add_block("profession", f"{prof}: from {pr['rank']} toward {pr['max_rank']}", dur, 0,
                      [{"text": f"{prof} {pr['rank']}/{pr['max_rank']}", "from": "your character sheet"}] + (
                          [{"text": f"about {per_h:.0f} skill-ups an hour when you craft", "from":
                            "your profession guide steps"}] if per_h else []),
                      {"profession": prof, "skill_ups": round(per_h * dur / 3600) if per_h else None})

    if by_level:
        level_blocks(inp, p, play, add_block, lambda: (t, lv, x), route_mode)

    for g in [] if by_level else groups:
        if t >= play - 60:
            break
        left = play - t
        last_block = sum(s["est_s"] for s in g) > left
        if last_block:  # the last block: only the steps that fit
            acc, cut = 0.0, []
            for s in g:
                if cut and acc + s["est_s"] > left:
                    break
                cut.append(s)
                acc += s["est_s"]
            g = cut
        dur = sum(s["est_s"] for s in g)
        kinds = {s["kind"] for s in g}
        xp_gain = sum(s["est_s"] / 3600 * rate(p, s["kind"]) for s in g)
        kind = "travel" if kinds == {"travel"} else "town" if "town" in kinds and len(g) <= 3 else "questing"
        first, last = g[0], g[-1]
        title = (f"{first['guide_title']}: steps {first['step']} to {last['step']} "
                 f"({first['title']} to {last['title']})" if len(g) > 1 else
                 f"{first['guide_title']}: step {first['step']} ({first['title']})")
        why = [{"text": f"Polestar plans {fdur(sum(s['planned_s'] for s in g))}; at your pace about {fdur(dur)}",
                "from": "the route reference times your pace per kind of step: " + ", ".join(
                    f"{k} {p[k]['factor']}x ({p[k]['from']}, {p[k]['n']} steps)" for k in KINDS if k in kinds)}]
        extra = {"guide": first["guide"], "step_from": first["step"], "step_to": last["step"], "_full": dur,
                 "town": "town" in kinds, "capital": any(c in (s["title"] or "") for s in g for c in CITIES)}
        add_block(kind, title, dur, xp_gain, why, extra)
        if last_block:
            break

    used = t
    buffer = budget - used
    gold = inp.get("gold_copper")
    gold_end = round(gold + (gold_rate or 0) * used / 3600) if gold is not None else None
    town = town_stop(inp, p, blocks, mount, cons)
    checkpoints = [{"after": b["id"], "at_s": b["start_s"] + b["dur_s"], "level": b["level_to"],
                    "step": (f"{b.get('guide', '').split('/')[-1]} step {b['step_to']}" if b.get("step_to") else None),
                    **({"ding": True} if b.get("ding") else {})}
                   for b in blocks]
    end = {"level": round(lfloat(inp, lv, x), 2), "level_int": lv, "xp": round(x), "route_clock": route_mode,
           "xp_gained": None if route_mode else round(sum(b["xp"] + b["rested_bonus"] for b in blocks)),
           "rested_used": 0 if route_mode else round(sum(b["rested_bonus"] for b in blocks)),
        "rested_left": round(rested), "gold_copper": gold_end, "played_s": round(used)}
    est = {
        "schema": 1, "char": inp["who"].get("char"), "made_t": inp["now_t"], "hours": hours, "constraints": cons,
        "context": context, "supported": True,
        # by_level: estimated from level times, no route step known;
        # `unlock` is what makes it the step-by-step plan.
        "mode": "by_level" if by_level else "route", "estimated": by_level,
        "unlock": unlock(inp) if by_level else None,
        "start": {"level": round(lfloat(inp, level, xp), 2), "level_int": level, "xp": round(xp), "xp_max": need0,
                  "xp_pct": round(xp / need0 * 100) if need0 else None, "rested_xp": round(inp.get("rested_xp") or 0),
                  "rested_estimated": bool(inp.get("offline_s")), "gold_copper": inp.get("gold_copper"),
                  "zone": inp.get("zone"), "subzone": inp.get("subzone"), "bind": inp.get("bind"),
                  "position": inp.get("position")},
        "pace": p, "budget_s": round(budget), "play_s": round(play), "stops_s": round(budget) - round(play),
        "blocks": blocks, "town_stop": town, "checkpoints": checkpoints, "end": end, "buffer_s": round(buffer),
        "skip": skips(inp, p, long_ahead), "abandon": abandons(inp, p), "mount": mount,
        "dungeons": [d for i, d in enumerate(dl) if i < 6 or d.get("redo")],
        "branch": None if cons.get("no_dungeons") or group_mode else branch(fitting_trips),
        # A dungeon loop isn't a gold option for a player who ruled dungeons out.
        "gold_options": gold_options(inp, mount, [] if cons.get("no_dungeons") else dl) if mount.get("behind") else [],
        "logout": logout(inp, blocks), "findings": inp.get("findings") or [], "nudges": inp.get("nudges") or [],
        "gear_now": gear_now(inp), "gear_mode": inp.get("gear_mode"), "gear_spec": inp.get("gear_spec"),
    }
    if best_d and (est["branch"] or any(b.get("dungeon") for b in blocks)):
        from .forecast import decision

        est["dungeon_forecast"] = decision(inp, best_d, cons.get("dungeon_goal"),
                                           "group" if group_mode else "branch", fitting_trips[1:])
    est["facts"] = facts(est)
    return est


def level_blocks(inp: dict, p: dict, play: float, add_block, now, route_mode: bool) -> None:
    """The session by level, for a character with no route step known: one
    block per level, at his XP an hour (his recent levels) or the route's
    clock for that level; each ends at a level-up (a checkpoint) or at the
    end of the session, and names Polestar's guide for that level when the
    side is known. The pace's reasons go on the first block only."""
    lf = p["level_factor"]
    xph = rate(p, "questing")
    first = True
    for _ in range(60):  # a session of at most 8 hours: never more than a few levels
        t, lv, x = now()
        left = play - t
        if left <= 60 or lv >= classguide.MAX_LEVEL:
            return
        need = xp_need(inp, lv)[0]
        r = route_seconds(inp, lv)
        if not route_mode and xph > 0:
            to_ding = max(0.0, need - x) / xph * 3600
        elif r:
            to_ding = r * max(0.0, 1 - x / max(1, need)) * (p["calibration"]["factor"] or 1.0)
        else:
            return
        ding = to_ding <= left
        dur = to_ding if ding else left
        guide = level_guide(inp, lv)
        title = (f"Level {lv} to {lv + 1}" if ding else f"Level {lv}") + (
            f": Polestar's {guide} guide" if guide else ": quests at your level")
        why = []
        if first:
            # The text says where each number is from: no separate source.
            why.append({"text": f"your last {p['xp_per_hour']['n']} levels: about {fint(xph)} XP an hour, "
                                "breaks included", "from": ""} if not route_mode else
                       {"text": f"Polestar's time for level {lv}: {fdur(r)}", "from": ""})
            if lf.get("factor") and lf.get("from") == "your level times against Polestar's":
                why.append({"text": f"your levels take {lf['factor']}x Polestar's time", "from": ""})
        # The ding takes exactly what the level needs (floats never leave a
        # level a hair short of it).
        gain = max(0.0, need - x) + 1e-6 if ding else dur / 3600 * xph
        if not add_block("questing", title, dur, gain, why, {"level_guide": guide, "ding": ding}):
            return
        first = False


def town_stop(inp: dict, p: dict, blocks: list[dict], mount: dict, cons: dict | None = None) -> dict | None:
    """One stop for every errand: hand-ins, class and profession training,
    the AH, crafting. At the plan's first town step (or its end)."""
    errands = []
    tr = inp.get("trainer") or {}
    lvl = inp["level"]
    ends = {b["level_to"] for b in blocks}
    reach = int(max(ends)) if ends else lvl
    if tr.get("last_level") is not None and reach > tr["last_level"]:
        errands.append({"what": "class trainer", "why": f"last visit at level {tr['last_level']}, you will be {reach}"})
    for pr in [] if (cons or {}).get("no_professions") else inp.get("professions") or []:
        if pr.get("rank") is not None and pr.get("max_rank") and pr["rank"] >= pr["max_rank"]:
            errands.append({"what": f"{pr['name']} trainer", "why": f"{pr['name']} is at its current cap "
                                                                    f"({pr['rank']}/{pr['max_rank']})"})
    crafts = [] if (cons or {}).get("no_professions") else [
        u for u in inp.get("upgrades") or [] if u.get("now") and any(
            s["kind"] == "craft" and s.get("can_make") for s in u.get("sources") or [])]
    for u in crafts[:2]:
        s = next(s for s in u.get("sources") or [] if s["kind"] == "craft" and s.get("can_make"))
        errands.append({"what": f"craft {u['name']}", "why": f"{gearmod.gain_words(u)}; {gearmod.craft_status(s)}"})
    if mount.get("behind") and (inp.get("loot_value") or {}).get("ah_per_hour"):
        errands.append({"what": "auction house", "why": "sell the cloth and trade goods you loot (mount savings)"})
    if not errands:
        return None
    # Trainers are in the capitals: the first block that goes to one, else
    # the first with a town step, else the end of the session.
    trainers = any("trainer" in e["what"] for e in errands)
    b = next((b for b in blocks if b.get("capital")), None) if trainers else None
    b = b or next((b for b in blocks if b.get("town")), None)
    if b:
        b["town_stop"] = True
    hist = [h["actual_s"] for h in inp.get("step_history") or [] if h["kind"] == "town" and h["actual_s"] > 30]
    med = sorted(hist)[len(hist) // 2] if hist else None
    return {"block": b["id"] if b else None, "where": b["title"] if b else "the end of the session", "errands": errands,
            "median_town_step_s": round(med) if med else None, "town_steps_measured": len(hist)}


def skips(inp: dict, p: dict, long_ahead: list[dict]) -> list[dict]:
    out = []
    pos = inp.get("position") or {}
    open_names = {(o.get("name") or "").lower(): o for o in inp.get("open_quests") or []}
    for s in pos.get("shown_behind") or []:
        o = open_names.get((s["title"] or "").lower())
        out.append({"what": f"step {s['step']} ({s['title']})", "est_s": round(step_s(p, s["kind"], s["planned_s"])),
                    "why": "Polestar shows it because a quest from there is still open; you are past it; "
                           "skip it only if no later step needs it"
                           + ("; it is a group quest with elites" if o and o.get("elite") else "")})
    for s in long_ahead[:3]:
        o = open_names.get((s["title"] or "").lower())
        out.append({"what": f"step {s['step']} ({s['title']})", "est_s": round(s["est_s"]),
                    "why": "a long step at your pace" + ("; a group quest with elite targets" if o and o.get("elite")
                                                         else "")
                    + "; look at what holds it up, skip it only if no later step needs it"})
    return out


def abandons(inp: dict, p: dict) -> list[dict]:
    """Open quests from zones he left a while ago that pay little now, or
    want a group he doesn't have: what each is still worth in his minutes."""
    xph = rate(p, "questing")
    level = inp["level"]
    zone = inp.get("zone")
    last_t = inp.get("data_until_t") or inp["now_t"]
    out = []
    for o in inp.get("open_quests") or []:
        if o.get("zone") == zone or o.get("complete"):
            continue
        away = last_t - (o.get("last_in_zone_t") or 0)
        sh = share(inp, level, o.get("level"))
        if (sh is None or o.get("xp_full") is None) and not o.get("elite"):
            continue  # no sourced XP rule: its worth now is unknown, so no advice on it
        worth = o["xp_full"] * sh if o.get("xp_full") is not None and sh is not None else None
        mins = worth / xph * 60 if worth is not None and xph > 0 else None
        if away >= STALE_S and ((sh is not None and sh <= 0.6) or o.get("elite")
                                or (mins is not None and mins < 5)):
            out.append({"quest": o.get("name") or f"quest {o['quest_id']}", "quest_id": o["quest_id"],
                        "zone": o.get("zone"), "quest_level": o.get("level"),
                        "xp_now": round(worth) if worth is not None else None,
                        "minutes_of_your_xp": round(mins) if mins is not None else None, "share": sh,
                        "why": "needs a group (elite targets)" if o.get("elite") else
                        f"pays {fpct(sh)} of its XP at your level" if sh < 1 else "little XP for the trip back"})
    out.sort(key=lambda a: (a["xp_now"] is None, a["xp_now"]))
    return out[:6]


def session_trips(dl: list[dict], seconds: float, goal: str | None, group: bool) -> list[dict]:
    """Whole trips that fit this session. A tag-along needs an existing group."""
    trips = [d for d in dl if d["fits_now"] and d.get("trip") and d["trip"]["time_s"] <= seconds
             and (d["priority"] == "high" or (group and "group_only" in d["goals"]))
             and (not goal or goal in d["goals"])]
    if goal == "gear":
        # Unknown rates still have a known portion. A quest-only upgrade can
        # qualify a trip even when no drop there would improve the character.
        def gear_rate(d):
            guaranteed = max((r["gain"] for r in d["quest_rewards"] if r["now"]), default=0)
            return max(guaranteed, d["known_gain_per_clear"] or 0) / d["trip"]["time_s"]

        trips.sort(key=gear_rate, reverse=True)
    return trips


def branch(dl: list[dict]) -> dict | None:
    d = next((d for d in dl if d["fits_now"] and d.get("trip") and d["priority"] == "high"), None)
    if not d:
        return None
    return {"dungeon": d["name"], "time_s": d["trip"]["time_s"], "xp": d["trip"]["xp"],
            "xp_per_hour": d["trip"]["xp_per_hour"], "kills_unknown": d["trip"]["kills_unknown"],
            "expected_gain": d["expected_gain_per_clear"],
            "known_gain": d.get("known_gain_per_clear"), "verdict": d["verdict"], "goals": d["goals"],
            "top_items": d["top_items"][:2], "from": d["trip"]["from"], "phases_s": d["trip"]["phases_s"],
            "vs_guide": d.get("vs_guide")}


def gold_options(inp: dict, mount: dict, dl: list[dict]) -> list[dict]:
    out = []
    lv = inp.get("loot_value") or {}
    if lv.get("ah_per_hour"):
        out.append({"what": "sell the cloth and trade goods you loot on the AH", "copper_per_hour": lv["ah_per_hour"],
                    "items": [i["name"] for i in lv.get("ah_items") or []][:3],
                    "from": "your loot over the last 14 days at your own Auctionator scan prices"})
    for d in dl:
        lp = (d.get("redo") or {}).get("loop")
        if lp and lp.get("gold_copper_per_hour"):
            out.append({"what": f"loop {d['name']} from the door", "copper_per_hour": lp["gold_copper_per_hour"],
                        "xp_per_hour": lp["xp_per_hour"], "run_s": lp["run_s"], "runs_for_mount": lp.get(
                            "runs_for_mount"), "from": f"your {d['redo']['runs']} run(s) there: loot at vendor price"})
    out.sort(key=lambda o: -o["copper_per_hour"])
    return out[:3]


ROUTE_NEAR = 15  # steps past the plan's end that count as "on the route" for the hearth


def logout(inp: dict, blocks: list[dict]) -> dict:
    """Where to log out for rested XP: in town when the plan ends there;
    else at the hearthstone's inn when that place is on the route the plan
    walks or the next steps after it; else an inn on the route, never a
    hearth to a place off it (a Theramore bind on a Hillsbrad route makes
    the next session start with the trip back)."""
    bind = inp.get("bind")
    last = blocks[-1] if blocks else None
    lvl_xp = xp_need(inp, inp["level"])[0]
    acc = (inp.get("rules") or {}).get("rested_accrual")
    town_last = last and last.get("kind") == "town"
    ahead = inp.get("route_ahead") or []
    end = next((i for i, s in enumerate(ahead) if last and s["guide"] == last.get("guide")
                and s["step"] == last.get("step_to")), None)
    near = ahead[:end + 1 + ROUTE_NEAR] if end is not None else ahead[:ROUTE_NEAR]
    on_route = bool(bind) and any(bind.lower() in (s.get("title") or "").lower() for s in near)
    town = next((s for s in ahead[end + 1:end + 1 + ROUTE_NEAR] if s.get("kind") == "town"), None) \
        if end is not None else None
    if town_last:
        where, how = last["title"], "you end in town"
    elif not bind:
        where, how = "an inn or a city", "a rest area: the most rested XP while you're away"
    elif on_route or not ahead:
        where, how = f"the inn at {bind}", "hearth there (your hearthstone's bind)"
    else:
        where = (f"the next town on your route: step {town['step']} ({town['title']})" if town
                 else "an inn or a city on your route")
        how = (f"your hearthstone is set to {bind}, off the route ahead: hearthing there means travelling back "
               f"next session; log out in a rest area on the route, or set your hearth there")
    return {"where": where, "how": how, "bind": bind, "bind_on_route": on_route,
            "rested_per_8h": round(lvl_xp * acc["value"]["per_8h_rested_area"]) if acc else None,
            "rested_source": {k: acc.get(k) for k in ("name", "source_url", "confidence", "scope")} if acc else None}


def gear_now(inp: dict) -> list[dict]:
    rows = []
    for u in inp.get("upgrades") or []:
        if not u.get("now"):
            continue
        srcs = u.get("sources") or []
        rows.append({"item": u.get("name"), "slot": u.get("slot_name"), "gain": round(u.get("gain") or 0, 1),
                     "line": gearmod.gain_words(u),
                     "where": src_text(srcs[0]) if srcs else None, "worn_with": with_text(u),
                     "fit": gearfit.text(u.get("fit")), "value": u.get("value", u.get("gain")) or 0})
    rows.sort(key=lambda r: -r["value"])
    return rows[:5]


def partner_to_get(u: dict) -> bool:
    """A weapon setup whose other hand the character doesn't have yet."""
    w = u.get("with") or {}
    return w.get("kind") == "get" and any(s.get("kind") not in ("bags", "bank") for s in w.get("sources") or [])


def with_text(u: dict) -> str | None:
    """The other hand of a weapon setup in words: 'Ponderous Orb (off hand,
    in your bags)'; None for an item worn alone."""
    w = u.get("with") or {}
    if w.get("kind") == "worn":
        return "what you wear in the other hand"
    if w.get("kind") == "empty":
        return "nothing in the other hand"
    if w.get("kind") != "get":
        return None
    srcs = w.get("sources") or []
    return (f"{w.get('name') or 'item ' + str(w.get('item_id'))} ({(w.get('slot_name') or '').lower()}, "
            f"{src_text(srcs[0]) if srcs else 'source unknown'})")


def src_text(s: dict) -> str:
    if s["kind"] == "quest":
        return f"quest {s.get('name')} ({s.get('zone')})"
    if s["kind"] == "drop":
        ch = f", {s['chance_pct']:.0f}%" if s.get("chance_pct") else ""
        return f"{s.get('boss') or 'trash'} in {s.get('zone')}{ch}" + (f" ({s['basis']})" if s.get("basis") else "")
    if s["kind"] in ("bags", "bank"):
        return f"in your {s['kind']}"
    if s["kind"] == "craft":
        return f"craft: {s.get('profession')} {s.get('skill')}; {gearmod.craft_status(s)}"
    return gearmod.source_text(s)


# ---------------------------------------------------------------- facts


def facts(e: dict) -> dict[str, str]:
    """Every number the prose may use, formatted, by name."""
    f: dict[str, str] = {}
    s, p, end = e["start"], e["pace"], e["end"]
    f["hours"] = fdur(e["hours"] * 3600)
    f["start.level"] = flevel(s["level"])
    f["start.xp_pct"] = f"{s['xp_pct']}%" if s.get("xp_pct") is not None else "n/a"
    f["start.rested"] = fint(s["rested_xp"])
    f["start.gold"] = fgold(s["gold_copper"])
    f["play"] = fdur(e["play_s"])
    f["stops"] = fdur(e["stops_s"])
    f["stops.share"] = fpct(p["stops"]["share"])
    f["stops.n"] = str(p["stops"]["n"])
    f["buffer"] = fdur(e["buffer_s"])
    pos = s.get("position") or {}
    if pos:
        f["pos.shown_step"] = str(pos.get("shown_step"))
        f["pos.furthest_step"] = str(pos.get("furthest_step"))
        f["pos.anchor_step"] = str(pos.get("anchor_step"))
    for k in KINDS:
        f[f"pace.{k}"] = f"{p[k]['factor']}x"
        f[f"pace.{k}.n"] = str(p[k]["n"])
        if p[k].get("xp_per_hour"):
            f[f"xph.{k}"] = fint(p[k]["xp_per_hour"])
    if p["level_factor"]["factor"]:
        f["pace.level"] = f"{p['level_factor']['factor']}x"
        f["pace.level.n"] = str(p["level_factor"]["n"])
    if p["xp_per_hour"]["value"]:
        f["xph"] = fint(p["xp_per_hour"]["value"])
        f["xph.n"] = str(p["xp_per_hour"]["n"])
    f["calibration"] = f"{p['calibration']['factor']}x"
    f["calibration.n"] = str(p["calibration"]["n"])
    for b in e["blocks"]:
        i = b["id"]
        f[f"{i}.start"] = "+" + fdur(b["start_s"]) if b["start_s"] else "start"
        f[f"{i}.dur"] = fdur(b["dur_s"])
        f[f"{i}.end"] = "+" + fdur(b["start_s"] + b["dur_s"])
        if b["xp"] is not None:
            f[f"{i}.xp"] = fint(b["xp"] + b["rested_bonus"])
            f[f"{i}.rested"] = fint(b["rested_bonus"])
        f[f"{i}.level"] = flevel(b["level_to"])
        f[f"{i}.title"] = b["title"]
        if b.get("step_from") is not None:
            f[f"{i}.steps"] = (f"{b['step_from']}-{b['step_to']}" if b["step_from"] != b["step_to"]
                               else str(b["step_from"]))
        for j, w in enumerate(b.get("why") or [], 1):
            f[f"{i}.why{j}"] = f"{w['text']} ({w['from']})" if w.get("from") else w["text"]
    for j, c in enumerate([c for c in e["checkpoints"] if c.get("ding")], 1):
        f[f"cp{j}.at"] = "+" + fdur(c["at_s"])
        f[f"cp{j}.level"] = str(int(round(c["level"])))
    if e.get("unlock"):
        f["unlock"] = e["unlock"]
    f["end.level"] = flevel(end["level"])
    if end["xp_gained"] is not None:
        f["end.xp"] = fint(end["xp_gained"])
    f["end.rested_used"] = fint(end["rested_used"])
    f["end.gold"] = fgold(end["gold_copper"])
    gr = (e["mount"].get("gold_rate") or {})
    f["gold.rate"] = (fgold(gr["copper_per_hour"]) + "/h") if gr.get("copper_per_hour") is not None else "n/a"
    f["gold.rate.n"] = str(gr.get("n") or 0)
    for i, t in enumerate(e["mount"]["tiers"], 1):
        k = f"mount{i}"
        f[f"{k}.level"] = str(t["level"])
        f[f"{k}.need"] = fgold(t["need_copper"])
        f[f"{k}.left"] = fgold(t["left_copper"])
        if quest := t.get("quest"):
            f[f"{k}.quest"] = f"{quest['name']}. {quest['action']}"
            f[f"{k}.trainer"] = fgold(t["trainer_alternative_copper"])
        if t.get("eta_played_s") is not None:
            f[f"{k}.eta"] = fdur(t["eta_played_s"])
        if t.get("eta_level") is not None:
            f[f"{k}.eta_level"] = flevel(t["eta_level"])
        if t.get("gold_at_level_copper") is not None:
            f[f"{k}.gold_at_level"] = fgold(t["gold_at_level_copper"])
        if t.get("discounted_copper"):
            f[f"{k}.discounted"] = fgold(t["discounted_copper"])
    ts = e.get("town_stop")
    if ts:
        f["town.where"] = ts["where"]
        for j, er in enumerate(ts["errands"], 1):
            f[f"town.errand{j}"] = f"{er['what']}: {er['why']}"
    for j, sk in enumerate(e["skip"], 1):
        f[f"skip{j}"] = f"{sk['what']}, about {fdur(sk['est_s'])}: {sk['why']}"
    for j, a in enumerate(e["abandon"], 1):
        minutes = a["minutes_of_your_xp"]
        pace_text = f", about {minutes} min of your XP rate" if minutes is not None else ""
        xp_text = f"{fint(a['xp_now'])} XP now" if a["xp_now"] is not None else "XP unknown"
        f[f"abandon{j}"] = f"{a['quest']} ({a['zone']}): {xp_text}{pace_text}; {a['why']}"
    br = e.get("branch")
    if br:
        f["branch.dungeon"] = br["dungeon"]
        f["branch.time"] = fdur(br["time_s"])
        f["branch.xp"] = fint(br["xp"])
        f["branch.xph"] = fint(br["xp_per_hour"]) if br["xp_per_hour"] is not None else "unknown"
        f["branch.gear"] = gear_estimate(br["expected_gain"], br.get("known_gain"))
        f["branch.from"] = br["from"]
        f["branch.verdict"] = br["verdict"]
        if (br.get("vs_guide") or {}).get("text"):
            f["branch.vs_guide"] = br["vs_guide"]["text"]
    for j, d in enumerate(e["dungeons"], 1):
        k = f"d{j}"
        f[f"{k}.name"] = d["name"]
        if d.get("verdict"):
            f[f"{k}.verdict"] = d["verdict"]
        f[f"{k}.range"] = f"{d['min']}-{d['max']}"
        f[f"{k}.gear"] = gear_estimate(d["expected_gain_per_clear"], d.get("known_gain_per_clear"))
        if d.get("trip"):
            f[f"{k}.time"] = fdur(d["trip"]["time_s"])
            xph = d["trip"]["xp_per_hour"]
            f[f"{k}.xph"] = fint(xph) if xph is not None else "unknown"
        if d.get("opens_at"):
            f[f"{k}.opens"] = str(d["opens_at"])
        v = d.get("vs_guide")
        if v:
            # Polestar's list against it (wowstats.plan.vsguide): on it, from
            # which level, or the measured reason to go anyway.
            f[f"{k}.polestar"] = (f"on Polestar's list for level {v['numbers']['level']}" if v["agrees"] else
                                  f"Polestar lists it from level {v['numbers']['from_level']}" if v["under_floor"]
                                  else f"Polestar's list for level {v['numbers']['level']} is past it")
            if v.get("text"):
                f[f"{k}.vs_guide"] = v["text"]
        for m, it in enumerate(d["top_items"][:3], 1):
            f[f"{k}.item{m}"] = (f"{it['item']} ({it['line']}, {it['boss']}"
                                 + (f", {it['chance']:g}%" if it.get("chance") is not None else "") + ")")
        r = d.get("redo")
        if r:
            f[f"{k}.redo"] = f"{r['verdict']}: {r['why']}"
            f[f"{k}.redo.runs"] = str(r["runs"])
            f[f"{k}.redo.time"] = fdur(r["time_s"])
            if r.get("xp_per_hour_run") is not None:
                f[f"{k}.redo.xph"] = fint(r["xp_per_hour_run"])
            lp = r.get("loop")
            if lp:
                f[f"{k}.loop.run"] = fdur(lp["run_s"])
                if lp.get("xp_per_hour") is not None:
                    f[f"{k}.loop.xph"] = fint(lp["xp_per_hour"])
                f[f"{k}.loop.gold"] = fgold(lp["gold_copper_per_hour"]) + "/h"
                if lp.get("runs_for_mount"):
                    f[f"{k}.loop.mount_runs"] = str(lp["runs_for_mount"])
            bi = r.get("best_item")
            if bi:
                f[f"{k}.redo.item"] = bi["item"]
                f[f"{k}.redo.item_runs"] = f"{bi['expected_runs']}"
                f[f"{k}.redo.item_time"] = fdur(bi["expected_time_s"])
    for j, g in enumerate(e["gold_options"], 1):
        f[f"goldopt{j}"] = (f"{g['what']}: about {fgold(g['copper_per_hour'])}/h"
                            + (f" (≈ {g['runs_for_mount']} runs for the next mount)" if g.get("runs_for_mount")
                               else "") + f" ({g['from']})")
    lo = e["logout"]
    f["logout.where"] = lo["where"]
    f["logout.how"] = lo["how"]
    if lo.get("rested_per_8h"):
        f["logout.rested"] = f"about {fint(lo['rested_per_8h'])} rested XP per 8 hours offline"
    for j, g in enumerate(e["gear_now"], 1):
        f[f"gear{j}"] = (f"{g['item']} ({g['line']}): {g['where']}"
                         + (f"; worn with {g['worn_with']}" if g.get("worn_with") else ""))
    for j, fd in enumerate(e["findings"][:6], 1):
        f[f"finding{j}"] = fd["text"] or fd["title"] or ""
    for j, n in enumerate(e["nudges"][:4], 1):
        f[f"nudge{j}"] = n["text"] or ""
    return f
