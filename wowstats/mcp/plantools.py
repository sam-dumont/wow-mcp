"""The planner's MCP tools (wowstats.plan) and the knowledge base's.

plan_session, how_did_my_plan_go, next_step, gear_options,
dungeon_options, mount_goal, whats_new: the session planner's numbers,
from game data only (the planner never reads vm-wow). kb_search and
kb_rule, kb_topics: the one knowledge base the analysis and the planner
read, every statement with where it comes from and when it was checked.

Every number in these answers comes from the estimator; a client model
should quote them as they are and say "unknown" where an answer has none.
plan_session answers with the estimator's own text and a brief; the client's
model writes it up and check_plan lists any number no fact holds. No model
runs on the server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import tools as T

CHAR = T.CHAR
CONTEXT = {"type": "string", "enum": ["levelling", "max-level-pve", "pvp"],
           "description": "What the session is for. Default: levelling until the route's last level, then "
                          "max-level PvE. Only levelling is planned so far."}
CITE = ("Game rules and facts here come from the knowledge base with their source and the day it was checked "
        "(kb_search, kb_rule, kb_topics): say 'rule as of <last_verified>, <source>', call a classic_fallback a "
        "fallback, and say 'unknown' where there is none.")


def _env(ctx: T.Ctx) -> dict:
    return ctx.environ()


def _inputs(ctx: T.Ctx, args: dict) -> dict:
    from wowstats import plan

    char = T.resolve_char(ctx, args).replace(" ", "-")
    return plan.inputs(_env(ctx), char, store=ctx.store)


def _no_data(char: str, why: str) -> dict:
    return {"char": char, "answer": None, "why": why}


def answers(fn):
    """No events for the character is an answer ("nothing yet"), not an error."""
    def wrapped(ctx: T.Ctx, args: dict) -> dict:
        try:
            return fn(ctx, args)
        except LookupError as e:
            return _no_data(str(args.get("char") or ctx.default_char or ""), str(e))
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


def t_plan_session(ctx: T.Ctx, args: dict) -> dict:
    from wowstats import plan

    char = T.resolve_char(ctx, args).replace(" ", "-")
    try:
        out = plan.plan(_env(ctx), char, T.opt_num(args.get("hours")), str(args.get("constraints") or "")[:300],
                        args.get("context") or None, store=ctx.store)
    except LookupError as e:
        return _no_data(char, str(e))
    e = out["estimate"]
    from wowstats.plan import write

    return {"char": char, "context": e.get("context"), "supported": e.get("supported"),
            "estimated": bool(e.get("estimated")), "unlock": e.get("unlock"),
            "brief": (write.CLIENT_BRIEF + (write.ESTIMATED_BRIEF if e.get("estimated") else ""))
            if e.get("supported") else None,
            "markdown": out["markdown"], "facts": e.get("facts") or {},
            "blocks": [{"id": b["id"], "keys": sorted(k for k in e["facts"] if k.startswith(b["id"] + "."))}
                       for b in e.get("blocks") or []],
            "constraints": e.get("constraints"), "coming_up": _near_events(ctx, char),
            "note": "markdown is the estimator's own text; write it up per brief, from facts only. " + CITE
                    + " coming_up: Forever events within 4 weeks that concern the character (news tool), with "
                      "their announced day; mention one when it changes what the session should do."}


def _near_events(ctx: T.Ctx, char: str) -> list[dict]:
    """The events within news.NEAR_DAYS that concern the character, sourced:
    so a plan can make room for an attunement before a raid opens."""
    from wowstats import news

    from .classtools import _who

    try:
        who = _who(ctx, char)
    except Exception:  # noqa: BLE001 - no class or level: no events to weigh
        return []
    rows = news.upcoming(char={**who, "spec": (who.get("talents") or {}).get("spec")
                               if isinstance(who.get("talents"), dict) else None}, cite=True)
    return [r for r in rows if r.get("concerns_you") and r.get("in_days") is not None
            and r["in_days"] <= news.NEAR_DAYS]


def t_check_plan(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.plan import store, write

    char = T.resolve_char(ctx, args).replace(" ", "-")
    p = store.latest(_env(ctx), char)
    if not p or not p.get("facts"):
        return _no_data(char, "no plan to check against: call plan_session first")
    from wowstats.plan import estimate as E

    # The player's own words from the session request, kept with the plan:
    # the write-up must keep them too, not only quote the facts' numbers.
    cons = E.parse_constraints(p.get("constraints") or "")
    return {"char": char, "plan_made": _iso(p["made_t"]),
            **write.check(str(args.get("text") or ""), p["facts"], cons, p.get("hours"))}


@answers
def t_how_did_my_plan_go(ctx: T.Ctx, args: dict) -> dict:
    inp = _inputs(ctx, args)
    revs = inp.get("plan_reviews") or []
    char = inp["who"]["char"]
    if not revs:
        return _no_data(char, "no plan to compare yet: make one (plan_session), play past its first checkpoint, "
                              "then ask again")
    last = revs[-1]
    return {"char": char, "latest": {**last, "made": _iso(last["made_t"]), "played_since": T.dur(
        last["played_since_s"]), "checkpoints": [{**c, "planned": T.dur(c["planned_online_s"]),
                                                  "actual": T.dur(c["actual_online_s"])} for c in
                                                 last["checkpoints"]]},
            "earlier": [{"made": _iso(r["made_t"]), "ratio": r["ratio"]} for r in revs[:-1]],
            "calibration": inp.get("calibration"),
            "note": "ratio: online time the character needed over the time the plan gave, per checkpoint "
            "(1.0: on plan). The "
                    "median over reviewed plans scales the next plans (calibration)."}


@answers
def t_next_step(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.plan import estimate as E

    inp = _inputs(ctx, args)
    e = E.estimate(inp, 0.75, "")
    if not e.get("supported"):
        return {"char": inp["who"]["char"], "answer": None, "why": e["message"], "unlock": e.get("unlock")}
    f = e["facts"]
    return {"char": inp["who"]["char"], "start_level": f["start.level"],
            "estimated": bool(e.get("estimated")), "unlock": e.get("unlock"),
            "blocks": [{"when": f"{f[b['id'] + '.start']} to {f[b['id'] + '.end']}", "what": b["title"],
                        "level_after": f[b["id"] + ".level"],
                        "why": [f"{w['text']} ({w['from']})" if w.get("from") else w["text"] for w in b["why"]],
                        "town_stop": b.get("town_stop", False)} for b in e["blocks"]],
            "town_errands": [f[k] for k in sorted(f) if k.startswith("town.errand")]
            if any(b.get("town_stop") for b in e["blocks"]) else [],
            "drop": [f[k] for k in sorted(f) if k.startswith("abandon")][:2]}


@answers
def t_gear_options(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.plan import estimate as E

    inp = _inputs(ctx, args)
    p = E.pace(inp)
    m = E.mounts(inp, p)
    dl = E.dungeons(inp, p, m)
    slots = E.gear_options(inp, p, dl, m)
    return {"char": inp["who"]["char"], "level": inp["level"], "spec": inp.get("gear_spec"),
            "slots": slots[:int(args.get("limit") or 8)],
            "kinds": sorted({o["kind"] for s in slots for o in s["options"]}),
            "note": "Gains are Polestar's gear points for this character's class and spec; bis_pct (the item's share "
                    "of its slot's best at this level, 100 = BiS) and delta_pct (the change from the worn one, in the "
                    "same points) are the page's badge, null when there's nothing to measure against. Each option "
                    "says what it "
                    "costs: time (a quest on the route, dungeon trips at the drop chance), luck (a world drop: a "
                    "bonus, never a plan step; the player's own AH price when they scanned it) or a craft. " + CITE}


def _dungeon_links(name: str | None) -> dict:
    """A dungeon's Wowhead Forever page (its guide, else its zone, else a
    search, as the page links it) and, for one split in wings, each wing's
    guide."""
    from wowstats import forever, trainers
    from wowstats.links import dungeon_guides, dungeon_url

    gs = dungeon_guides(name)
    return {"url": dungeon_url(name, forever.area_id(name) if name else None) or (
        trainers.search_url(name) if name else None),
            **({"guides": gs} if len(gs) > 1 else {})}


@answers
def t_dungeon_options(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.plan import estimate as E

    inp = _inputs(ctx, args)
    p = E.pace(inp)
    m = E.mounts(inp, p)
    dl = E.dungeons(inp, p, m)
    keep = ("rank", "name", "min", "max", "fits_now", "opens_at", "expected_gain_per_clear", "top_items",
            "quest_rewards", "quests_open", "gain", "trip", "value", "redo", "vs_guide")
    return {"char": inp["who"]["char"], "level": inp["level"],
            "questing_xp_per_hour": round(E.rate(p, "questing")),
            "dungeons": [{**{k: d.get(k) for k in keep}, **_dungeon_links(d.get("name"))} for d in dl],
            "instance_limit": inp.get("instance_limit") or "unknown: no sourced Forever rule in the knowledge base",
            "note": "A dungeon needs a group: offer it as an option. 'gain' is one more run at the character's "
                    "level now, the same estimate the Overview and the Dungeons panel show (its 'summary' is their "
                    "line): from the character's own runs there (basis 'runs': trip time, kill XP carried to the "
                    "current level by the kill formula over the creatures killed), else the knowledge base's "
                    "clear time plus the character's own travel and quest time (basis 'rules'), plus the quests "
                    "doable now. kills_unknown: no run there to measure them, so the XP is the quests' alone; "
                    "never fill it in. 'trip' and 'value' carry the same numbers for the planner. Expected gear "
                    "per clear is the chance-weighted best upgrade per slot. 'redo' is one more run of a dungeon "
                    "the character did, and looping it from the door. 'vs_guide' sets each against Polestar's "
                    "dungeons for the level (from a dungeon's floor, where players start it, to its top): agrees, "
                    "guide_says, we_say, the measured reason when they differ, and its numbers. Polestar is the "
                    "default: without a reason, follow it; quote the reason when offering a dungeon off its "
                    "list. " + CITE}


@answers
def t_mount_goal(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.plan import estimate as E

    inp = _inputs(ctx, args)
    p = E.pace(inp)
    m = E.mounts(inp, p)
    dl = E.dungeons(inp, p, m)
    return {"char": inp["who"]["char"], "level": inp["level"], "gold": T.gold(m["gold_copper"]),
            "gold_rate": inp.get("gold_rate"),
            "tiers": [{**t, "need": T.gold(t["need_copper"]), "left": T.gold(t["left_copper"]),
                       "eta_played": T.dur(t["eta_played_s"]), "gold_at_level": T.gold(t["gold_at_level_copper"])}
                      for t in m["tiers"]],
            "current": (m.get("current") or {}).get("name"), "behind": m["behind"], "discount": m.get("discount"),
            "unknown": m.get("unknown"), "gold_options": E.gold_options(inp, m, dl),
            "note": "need is a tier's whole cost. When comes_with_mount is true the training includes the mount; "
                    "extra_mount_copper is a second vendor mount, optional and not in need: never quote it as the "
                    "price of riding. Net gold per online hour between logout snapshots (n given). The savings roll "
                    "over to the next tier once one is bought. Gold options are priced, never required. " + CITE}


def t_whats_new(ctx: T.Ctx, args: dict) -> dict:
    from wowstats import analysis

    days = int(args.get("days") or 7)
    char = T.resolve_char(ctx, args).replace(" ", "-")
    s = analysis.load_state({k: v for k, v in ctx.environ().items() if k.startswith("ANALYSIS_")}, char)
    if not s:
        return {"answer": None, "why": "no play analysis yet for this character (the analyse tool runs one)"}
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    fs = [f for f in (s.get("findings") or {}).values() if not f.get("doubtful")]
    pick = ("key", "title", "text", "first_seen", "last_confirmed", "confirmations")
    new = [{k: f.get(k) for k in pick} for f in fs if (f.get("first_seen") or "") >= since]
    still = [{k: f.get(k) for k in pick} for f in fs if (f.get("first_seen") or "") < since <=
             (f.get("last_confirmed") or "")]
    dropped = [d for r in s.get("runs") or [] if (r.get("at") or "") >= since for d in r.get("dropped") or []]
    return {"since": since, "new": new, "confirmed_again": still[:10], "dropped": dropped[:10],
            "nudges": s.get("nudges") or [], "updated": s.get("updated"),
            "note": "Findings are verified each night: their SQL re-run on the latest data."}


def t_kb_search(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import kb

    q = str(args.get("query") or "").strip()
    if not q:
        raise T.ToolError("query is required")
    k = kb.shared(_env(ctx))
    rows = k.search(q, args.get("kind") or None, limit=T.limit_arg(args, 8, 25), scope=args.get("scope") or None)
    if args.get("char"):  # an analysis lane: counted in its coverage
        from wowstats.analysis import drive

        drive.note_kb(_env(ctx), str(args["char"]).replace(" ", "-"))
    return {"query": q, "results": rows,
            "note": "Each result carries its source (url, source_type, date, confidence, scope: forever or "
                    "classic_fallback). Cite it; a Classic fallback is said to be one. kb_rule gives a static rule."}


def t_kb_rule(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import kb

    k = kb.shared(_env(ctx))
    name = str(args.get("name") or "").strip()
    r = k.rule(name) if name else None
    if r is None:
        return {"name": name or None, "rule": None, "known": sorted(k.rules()),
                "why": "no sourced rule of that name: unknown" if name else "give a name from `known`"}
    return {"name": name, "rule": r}


def t_kb_topics(ctx: T.Ctx, args: dict) -> dict:
    """The research catalogue by lane: what each covers, as of when, and its
    open questions; with `lane`, that lane's documents, contradictions and
    open questions in full."""
    from wowstats.analysis import kb

    k = kb.shared(_env(ctx))
    lanes = k.meta.get("research") or {}
    lane = str(args.get("lane") or "").strip()
    if not lane:
        return {"lanes": {n: {x: v[x] for x in ("title", "last_verified", "statements", "rules", "contradictions",
                                                 "open_questions")} for n, v in lanes.items()},
                "note": "kb_topics with a lane lists its documents, contradictions and open questions; kb_search "
                        "(kind kb-statement) finds statements, kb_rule a static rule. Every statement carries its "
                        "source, scope (forever or classic_fallback), confidence and last_verified: say 'rule as of "
                        "<last_verified>, <source_type> <source_url>' and call a classic_fallback one a fallback."}
    if lane not in lanes:
        return {"lane": lane, "known": sorted(lanes), "why": "no research lane of that name"}
    rows = k.db.execute("SELECT id, kind, title, body, url, source_type, date, confidence, scope, last_verified "
                        "FROM docs WHERE kind IN ('kb-contradiction', 'kb-open-question') AND id LIKE ? ORDER BY id",
                        (lane + ":%",)).fetchall()
    pick = lambda kind: [{"id": r[0], "text": r[3], **({"source_url": r[4], "source_type": r[5], "date": r[6],  # noqa: E731
                                                      "confidence": r[7], "scope": r[8], "last_verified": r[9]}
                                                     if kind == "kb-contradiction" else {})}
                         for r in rows if r[1] == kind]
    lim = T.limit_arg(args, 15, 50)  # a slice: kb_search finds the rest
    con, opn = pick("kb-contradiction"), pick("kb-open-question")
    return {"lane": lane, **lanes[lane], "contradictions": con[:lim], "open_questions": opn[:lim],
            "truncated": len(con) > lim or len(opn) > lim,
            "note": "Open questions are unknowns: no source answers them, so the answer is 'unknown'."}


def _iso(t) -> str | None:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="minutes") if t else None


S = T.schema
PLAN_TOOLS: tuple[T.Tool, ...] = (
    T.Tool("plan_session", "Plan my next hours",
           "A plan for the next N hours (default 3): blocks, checkpoints, the level, XP and gold at "
           "the end, a town stop, where to log out, what to skip, a dungeon branch, the mount, from the character's "
           "own pace. No Recorder events: a plan by level and the line that unlocks the full one. Constraints "
           "in plain words ('no dungeons', 'only 2h'). Kept for how_did_my_plan_go.",
           S({"char": CHAR, "hours": {"type": "number", "minimum": 0.5, "maximum": 8,
                                 "description": "How long to plan for, in hours."},
              "constraints": {"type": "string", "maxLength": 300,
                              "description": "Limits in plain words: 'no dungeons', 'only 2h', 'level tailoring'."},
              "context": CONTEXT}), t_plan_session, writes="replaces"),
    T.Tool("check_plan", "Check a plan write-up",
           "Checks a write-up of the latest plan: lists every number in the text that none of the plan's facts "
           "holds, and every sentence that breaks the player's own constraints from plan_session (an activity they "
           "ruled out, such as 'no dungeons', or more time than they gave), naming the constraint. Call it before "
           "showing a plan you wrote.",
           S({"char": CHAR, "text": {"type": "string", "maxLength": 12000,
                                "description": "Your write-up of the latest plan."}}, ("text",)), t_check_plan),
    T.Tool("how_did_my_plan_go", "Plan vs actual",
           "The latest plan against what happened: each checkpoint's planned and actual online time, the end "
           "level, and the calibration it feeds into the next plans.", S({"char": CHAR}), t_how_did_my_plan_go),
    T.Tool("next_step", "Next 45 minutes",
           "The first 45 minutes of a plan: the block(s), why, a town stop if it falls there, the two quests "
           "most worth dropping.", S({"char": CHAR}), t_next_step),
    T.Tool("gear_options", "Gear options per slot",
           "Per slot, every way to an upgrade with its cost: quest (on the route or not), dungeon drop (trip time "
           "at the drop chance), world drop (luck, plus the player's own AH price), craft, and any other "
           "source kind the "
           "gear data names.", S({"char": CHAR, "limit": T.lim(8, 20)}), t_gear_options),
    T.Tool("dungeon_options", "Dungeons worth it",
           "Dungeons in range now or soon, ranked: one more run at the character's level now (time, XP, share "
           "of the level, XP/h against its questing, quests to pick up; the Overview's line), expected gear "
           "per clear with the top items (boss, drop chance); for the ones it did, one more run and looping "
           "it (gold per hour, runs for the mount).",
           S({"char": CHAR}), t_dungeon_options),
    T.Tool("mount_goal", "Mount progress",
           "Each riding tier from the knowledge base: gold needed, gold now, net gold per hour, ETA in played hours "
           "and in levels, whether the character is behind, and priced gold options.", S({"char": CHAR}), t_mount_goal),
    T.Tool("whats_new", "New this week",
           "The play analysis' findings new in the last days, those confirmed again, those dropped, and the "
           "nudges.", S({"char": CHAR, "days": {"type": "integer", "minimum": 1, "maximum": 30,
                                         "description": "How many days back."}}), t_whats_new),
    T.Tool("kb_search", "Search the knowledge base",
           "Full-text search of the knowledge base (Forever client tables, Polestar's data and guides, the "
           "research catalogue, static rules). Every result has its source: url, source_type, date, confidence "
           "and scope (forever or classic_fallback).",
           S({"query": {"type": "string", "maxLength": 200, "description": "Words to search for."},
              "kind": {"type": "string", "maxLength": 20,
                       "description": "One kind of document (spell, item, quest, npc, zone, talent, note...)."},
              "scope": {"type": "string", "enum": ["forever", "classic_fallback"],
                        "description": "Only Forever's own sources, or only the Classic ones used where Forever "
                                       "has none."},
              "limit": T.lim(8, 25),
              "char": {**CHAR, "description": "During an analysis run: the character, so the call counts in its "
                                              "coverage."}},
             ("query",)), t_kb_search, writes="adds"),
    T.Tool("kb_rule", "A static game rule",
           "One static rule the code uses (e.g. riding.apprentice, rested.accrual), with its value, source and "
           "last_verified; `conflicts` holds any other lane's differing version. Lists the known names when the "
           "name is unknown.",
           S({"name": {"type": "string", "maxLength": 80,
                       "description": "The rule's name, e.g. riding.apprentice. Unknown: the answer lists the "
                                      "names."}}), t_kb_rule),
    T.Tool("kb_topics", "What the knowledge base covers",
           "The research catalogue by lane (game rules, dungeons, levelling, economy and professions, PvP, raids): "
           "statements, rules, contradictions and open questions, each lane as of the day it was checked. With a "
           "lane: its documents, its contradictions with their sources, its open questions.",
           S({"lane": {"type": "string", "maxLength": 40,
                       "description": "A research lane's name (without it: the list of lanes), e.g. dungeons."},
              "limit": T.lim(15, 50)}), t_kb_topics),
)
