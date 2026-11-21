"""The play analysis over MCP, for any client: the user's own model does the
reasoning, the server holds the run and judges (wowstats.analysis.drive).
No model runs on the server.

  analyse            the `analyse` prompt as a tool, for clients without
                     prompt support (ChatGPT): the procedure and the next step
  dashboard_refresh  `analyse` for the character whose analysis is most out of
                     date, plus the server's automatic-refresh timers
  analysis_brief     the instructions, the schema, the profile and the KB's
                     contents, in sections and pages for small contexts
  play_sql           read-only SQL on the character's prep database, paged
  list_metrics       the semantic layer's catalogue: metrics, definitions, dimensions, grains
  query_metric       a metric question compiled to SQL and run: rows, the SQL, the definitions
  propose_finding    the judge: re-runs the SQL (or the query_metric call), stores or refuses with why
  analysis_submit    a pass's answer; the server answers with the next step
  analysis_progress  where the run stands, to go on in a new chat
  kb_get, kb_lookup  knowledge base documents and names by id
"""

from __future__ import annotations

import json

from . import tools as T

CHAR = T.CHAR


def _env(ctx: T.Ctx) -> dict:
    return ctx.environ()


def _char(ctx: T.Ctx, args: dict) -> str:
    return T.resolve_char(ctx, args).replace(" ", "-")


def _run_char(ctx: T.Ctx, args: dict) -> str:
    """The tools that read or change a run name its character every time:
    falling back to the default one sent a run about one character's
    questions to another's database (first Sonnet run, 2026-11-18)."""
    if not str(args.get("char") or "").strip():
        raise T.ToolError("char is required: the character this run is about, as analysis_brief and "
                          "analysis_progress name it")
    return _char(ctx, args)


def t_analyse(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import drive

    from . import prompts

    char = _char(ctx, args)
    return {"char": char, "instructions": prompts.analyse_text(char, str(args.get("goals") or "")),
            "progress": drive.progress(_env(ctx), char, ctx.store)}


def t_analysis_brief(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import drive

    try:
        return drive.brief(_env(ctx), _char(ctx, args), str(args.get("section") or "start"), int(args.get("page") or 1),
                           str(args.get("goals") or "") or None, bool(args.get("restart")), ctx.store)
    except ValueError as e:
        raise T.ToolError(str(e)) from None


def t_play_sql(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import drive

    q = str(args.get("query") or "")
    if not q.strip():
        raise T.ToolError("query is required")
    return drive.sql(_env(ctx), _run_char(ctx, args), q, int(args.get("page") or 1), int(args.get("page_size") or 40),
                     ctx.store)


def t_list_metrics(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import semantic

    cat = semantic.catalogue()
    return {"metrics": [{k: m[k] for k in ("name", "kind", "unit", "description", "caveats", "group_by")}
                        for m in cat["metrics"]],
            "dimensions": [{k: d[k] for k in ("name", "description", "models") if k in d} | (
                {"grains": d["grains"]} if "grains" in d else {}) for d in cat["dimensions"]],
            "grains": cat["grains"], "ops": cat["ops"],
            "how": "query_metric with metrics (names above), group_by (dimensions), where ([{dimension, op, "
                   "value}] or values for in), grain (day, week, month: groups by time), since/until "
                   "(YYYY-MM-DD), order_by (names, '-name' for descending), limit. Metrics from different "
                   "models are each aggregated, then joined on group_by."}


def t_query_metric(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import drive

    q = {k: _obj(args[k]) if k in ("where",) else args[k] for k in
         ("metrics", "group_by", "where", "grain", "since", "until", "order_by", "limit") if k in args}
    return drive.metric(_env(ctx), _run_char(ctx, args), q, ctx.store)


def _obj(v):
    """Some clients send nested objects as JSON text."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def t_propose_finding(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import drive

    f = _obj(args.get("finding"))
    if not isinstance(f, dict):
        raise T.ToolError("finding must be an object (analysis_brief section `lane` has its shape)")
    if args.get("sql"):
        f = {**f, "sql": args["sql"]}
    if args.get("metric_query"):
        f = {**f, "metric_query": _obj(args["metric_query"])}
    return drive.propose(_env(ctx), _run_char(ctx, args), f, ctx.store)


def t_analysis_submit(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import drive

    return drive.submit(_env(ctx), _run_char(ctx, args), str(args.get("pass") or ""), _obj(args.get("answer")),
                        ctx.store)


def t_analysis_progress(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import drive

    return drive.progress(_env(ctx), _char(ctx, args), ctx.store)


def t_dashboard_refresh(ctx: T.Ctx, args: dict) -> dict:
    """Refresh the caller's dashboard on the client: the caller's own model
    runs the analysis (`analyse`'s procedure, the same tools), so it costs
    the server nothing. It used to queue a run of the server's model, and the
    owner's own AI calling it spent the host's provider money in the lab
    (2026-11-21). The server's automatic refreshes stay for players who never
    refresh themselves (wowstats.analysis.auto); this only reads their timers."""
    from wowstats.analysis import auto

    server = auto.service().status(ctx)
    timers = (server.get("timer") or {}).get("chars") or []
    # The most out of date first: due, then most play since its refresh.
    order = [t["char"] for t in sorted(timers, key=lambda t: (not t["due"], -t["played_since_s"]))
             if t["played_since_s"] > 0]
    char = _char(ctx, args) if str(args.get("char") or "").strip() else (order or [_char(ctx, {})])[0]
    out = t_analyse(ctx, {"char": char, "goals": args.get("goals")})
    do = (f"Run the analysis now for {char}: follow `instructions` (analysis_brief section start first), pass by "
          f"pass, and hand each pass in with analysis_submit. The accepted synthesis refreshes the dashboard (How "
          f"you play) and resets {char}'s timer.")
    if server.get("state") == "running":
        do = (f"{server['said'][0].upper()}{server['said'][1:]}: let it end (a few minutes; call dashboard_refresh "
              f"again) before starting your own run, which would share its run state. Then: {do}")
    rest = [c for c in order if c != char]
    return {"runs_on": "client",
            "server_cost": "none: your own model does the analysis; this call starts nothing on the server",
            "do": do, **({"then": rest, "then_do": "the same for each, one character per run"} if rest else {}),
            **out, "server_automatic_refresh": server}


def _ids(args: dict, cap: int) -> list[str]:
    ids = args.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise T.ToolError("ids is a list of kind:id strings, e.g. [\"npc:1234\", \"quest:56\"]")
    return [str(i) for i in ids][:cap]


def _note_kb(ctx: T.Ctx, args: dict) -> None:
    from wowstats.analysis import drive

    if str(args.get("char") or "").strip():
        drive.note_kb(_env(ctx), _char(ctx, args))


def t_kb_get(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import kb

    ids = _ids(args, 10)
    _note_kb(ctx, args)
    return {"documents": kb.shared(_env(ctx)).get(ids)}


def t_kb_lookup(ctx: T.Ctx, args: dict) -> dict:
    from wowstats.analysis import kb

    ids = _ids(args, 100)
    _note_kb(ctx, args)
    k = kb.shared(_env(ctx))
    out = {}
    for i in range(0, len(ids), 10):
        for d in k.get(ids[i:i + 10], cap=0):
            out[d["id"]] = None if d.get("missing") else d.get("title")
    return {"names": out, "note": "null: the knowledge base doesn't have it"}


def _guard(fn):
    def wrapped(ctx, args):
        try:
            return fn(ctx, args)
        except LookupError as e:
            return {"answer": None, "why": str(e)}
    wrapped.__name__ = fn.__name__
    return wrapped


S = T.schema
GOALS = {"type": "string", "maxLength": 600,
         "description": "The player's own words for what they want from the game; kept for the next runs."}
ANALYSIS_TOOLS: tuple[T.Tool, ...] = (
    T.Tool("analyse", "Analyse my play",
           "Start or continue the play analysis of a character: you (the model) run it pass by pass with the tools "
           "below, the server judges every finding. Returns the procedure and where the run stands. The same as "
           "the `analyse` prompt, for clients without prompts; dashboard_refresh returns it for the character whose "
           "analysis is most out of date.",
           S({"char": CHAR, "goals": GOALS}), _guard(t_analyse)),
    T.Tool("analysis_brief", "Analysis brief",
           "The play analysis' brief, in sections sized for small contexts: start (procedure, goals, finding rules, "
           "where the run stands), schema, profile and kb (paged), survey, lane, followups, coverage, synthesis. "
           "The first call opens a run; `restart` starts a new one.",
           S({"char": CHAR, "section": {"type": "string", "enum": ["start", "schema", "profile", "kb", "survey",
                                                                    "lane", "followups", "coverage", "synthesis"],
                                    "description": "Which part of the brief: start, then the schema, profile and "
                                                   "kb pages, then the instructions for the pass the run is on."},
              "page": {"type": "integer", "minimum": 1, "maximum": 50,
                       "description": "Which page of that section (12,000 characters a page)."}, "goals": GOALS,
              "restart": {"type": "boolean", "description": "Drop the open run and start a new one."}}),
           _guard(t_analysis_brief), writes="replaces"),
    T.Tool("play_sql", "Query the play database",
           "One read-only DuckDB SELECT (WITH allowed) on the character's analysis database (analysis_brief "
           "section schema). DuckDB, not SQLite: 7/2 = 3.5 (// for whole numbers), strftime(time, fmt), "
           "COUNT(*) FILTER (WHERE ...); no files, ATTACH or repeat/lpad/printf/replace. Paged: page_size rows "
           "(default 40, at most 60), `more` when there's more. Named metrics: list_metrics, query_metric.",
           S({"char": CHAR, "query": {"type": "string", "maxLength": 8000,
                                 "description": "One read-only DuckDB SELECT (WITH allowed); analysis_brief "
                                                "describes the tables."},
              "page": {"type": "integer", "minimum": 1, "maximum": 25,
                       "description": "Which page of the result."},
              "page_size": {"type": "integer", "minimum": 1, "maximum": 60,
                            "description": "Rows per page (default 40)."}}, ("char", "query")),
           _guard(t_play_sql), writes="adds"),
    T.Tool("list_metrics", "Metric catalogue",
           "The semantic layer's catalogue: every named metric with its kind (simple, ratio, derived), unit, "
           "one-line definition and caveats, the dimensions it can be grouped and filtered by, and the time "
           "grains. Four metrics count time at a level four ways: played_s (Polestar's split), online_s (the "
           "Recorder's timeline), active_s (online, AFK off) and calendar_s (ding to ding, offline included).",
           S({}), _guard(t_list_metrics)),
    T.Tool("query_metric", "Query a metric",
           "Named metrics (list_metrics) grouped by dimensions, filtered, cut by a time grain: the server "
           "compiles the question from the declared model and runs it read-only on the character's data. "
           "Returns the rows, the SQL with its bound parameters (values never go into the SQL) and each "
           "metric's definition and caveats. propose_finding takes the same call as evidence (metric_query).",
           S({"char": CHAR,
              "metrics": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8,
                          "description": "Metric names from list_metrics, e.g. [\"played_s\", \"online_s\"]."},
              "group_by": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                           "description": "Dimension names, e.g. [\"level\"] or [\"zone\"]."},
              "where": {"type": "array", "maxItems": 8, "items": {"type": "object"},
                        "description": "Conditions [{dimension, op, value}]: op is =, !=, <, <=, >, >=, in, "
                                       "not in (with values: [...]), is null, is not null."},
              "grain": {"type": "string", "enum": ["day", "week", "month"],
                        "description": "Group by time at this grain (local time)."},
              "since": {"type": "string", "description": "From this date, YYYY-MM-DD (local time)."},
              "until": {"type": "string", "description": "Through this date, YYYY-MM-DD (local time)."},
              "order_by": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                           "description": "Output names; prefix with - for descending, e.g. [\"-deaths\"]."},
              "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                        "description": "Rows at most (default 50)."}}, ("char", "metrics")),
           _guard(t_query_metric), writes="adds"),
    T.Tool("propose_finding", "Propose a finding",
           "Hand one finding to the judge: it re-runs the SQL (or the finding's metric_query, a query_metric "
           "call), checks every number (placeholders, expect, holds), "
           "the action and the knowledge base ids, then stores it in the character's analysis or says why not. "
           "The SQL must read the play history: a constant is refused. "
           "A finding with `action: null` is kept as an observation, shown apart.",
           S({"char": CHAR, "finding": {"type": "object", "description": "key, title, claim, sql, expect, "
                                        "relevance, action ({do, where, blocked_by, next_step} or null), and "
                                        "optionally holds, effect, sample, kb"},
              "sql": {"type": "string", "maxLength": 8000,
                      "description": "The SQL behind the finding, re-run by the judge."},
              "metric_query": {"type": "object",
                               "description": "Instead of sql: the query_metric arguments behind the finding "
                                              "(metrics, group_by, where, grain...); the judge compiles and re-runs "
                                              "it, and the finding's placeholders name its output columns."}},
             ("char", "finding")),
           _guard(t_propose_finding), writes="replaces"),
    T.Tool("analysis_submit", "Hand in a pass",
           "Hand in what a pass produced: pass survey ({questions}), lane ({lane, answers, ask_next}), pick "
           "({picks}) or synthesis ({keep, findings, nudges, patterns, clashes, driver}; add early: true to "
           "finish before the lanes are done). The server checks it and answers with the next step.",
           S({"char": CHAR, "pass": {"type": "string", "enum": ["survey", "lane", "pick", "synthesis"],
                                "description": "The pass this answer is for (analysis_progress says which is next)."},
              "answer": {"type": "object", "description": "The pass's answer, in the shape analysis_brief gives."}},
             ("char", "pass", "answer")), _guard(t_analysis_submit), writes="replaces"),
    T.Tool("analysis_progress", "Analysis progress",
           "Where the character's analysis run stands (pass, lanes open and done, queries, tables touched and "
           "untouched, findings proposed, verified, refused with why), the next step, the last finished run and "
           "the last nightly recheck. Call it first to continue in a new chat.",
           S({"char": CHAR}), _guard(t_analysis_progress)),
    T.Tool("dashboard_refresh", "Refresh my dashboard",
           "Refresh your dashboard (How you play) on the client: you, the calling model, run the analysis; it "
           "costs the server nothing and starts nothing there. Returns `analyse`'s procedure for the character "
           "most out of date (or `char`), `do`, and `then` for the others; the accepted synthesis refreshes the "
           "dashboard and resets its timer. Also: the server's automatic refreshes, the install's "
           "budget and yours.",
           S({"char": CHAR, "goals": GOALS}), _guard(t_dashboard_refresh)),
    T.Tool("kb_get", "Knowledge base documents",
           "Full knowledge base documents by id (kind:id, e.g. npc:1234, quest:56, note:xp-sources), up to 10, each "
           "with its source.",
           S({"char": CHAR, "ids": {"type": "array", "items": {"type": "string"}, "maxItems": 10,
                               "description": "Up to 10 kind:id strings, e.g. npc:1234, quest:56."}}, ("ids",)),
           _guard(t_kb_get), writes="adds"),
    T.Tool("kb_lookup", "Names for ids",
           "Names for up to 100 ids at once (kind:id: spell, item, quest, npc, zone...); null when the knowledge "
           "base doesn't have it.",
           S({"char": CHAR, "ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100,
                               "description": "Up to 100 kind:id strings, e.g. npc:1234, quest:56."}}, ("ids",)),
           _guard(t_kb_lookup), writes="adds"),
)
