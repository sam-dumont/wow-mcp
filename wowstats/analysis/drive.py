"""The play analysis for any driver: the server holds the run and judges.

No model runs on the server. The user's own MCP client drives a run pass
by pass (wowstats.analysis.passes) through the tools in
wowstats.mcp.analysistools:

  brief()     the instructions, the neutral table profile, the schema and
              the knowledge base's contents, in sections and pages sized for
              small contexts (a local model with about 32k tokens);
  sql()       one read-only SELECT on the character's prep database, paged;
  propose()   the judge: verify.check re-runs the finding's SQL, checks every
              number (placeholders, `expect`, `holds`) and the KB ids, then
              stores it in the character's analysis or refuses it with why;
  submit()    a pass's answer (survey questions, a lane's answers and "ask
              next", the follow-up picks, the synthesis). The server cuts
              questions into lanes, adds the coverage round for tables no
              query read, and checks the synthesis in code: each clash's
              reconciling query runs, a merge stands only on a verified
              finding, nudges' verdicts go through analysis.feasible, the
              shared lines through verify.shareable;
  progress()  where the run stands and what to do next, so "continue" in a
              new chat picks it up.

The run lives in ANALYSIS_STATE_DIR/chars/<char>/drive.json, the kept
analysis next to it (analysis.json). Game data only: the prep database,
never vm-wow.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import tempfile
import threading
import time
from datetime import UTC, datetime

from . import passes

# Who drives the analysis: a player's MCP client unless the server's own
# runner (wowstats.analysis.auto) set it for its run. Kept on the run's
# record as `kind` ("client" or "server") and `driver`.
DRIVER: contextvars.ContextVar[dict | None] = contextvars.ContextVar("analysis_driver", default=None)
CLIENT_KINDS = ("client", "server")

CACHE_S = 1800  # one database for a whole run, and few reads of the store
PAGE_CHARS = 12000
SQL_PAGE = 40
SQL_MAX_ROWS = 1000
SQL_CHARS = 6000
PER_LANE = 3
PER_FOLLOWUP_LANE = 2
FOLLOWUPS = 8
MIN_QUESTIONS = 10
MAX_QUESTIONS = 40
RUN_FILE = "drive.json"
_LOCK = threading.RLock()
_WS: dict = {}


class Workspace:
    """One character's prep database (in memory, read-only for drivers)."""

    def __init__(self, env: dict, char: str, store=None):
        from wowstats.mcp import route
        from wowstats.plan.facts import store_for

        from . import TABLES, advice_for, kb, manifest, prep, scoring_for, tables_of

        st = store_for(env, store)
        self.store = st
        self.char = prep.norm(char)
        rows = prep.query(st, st.stream, self.char)
        if not rows:
            raise LookupError(f"no events for {self.char}")
        self.kb = kb.shared(env)
        ref = route.reference()
        self.db = prep.build(rows, self.char, ref, map_sizes=self.kb.meta.get("ui_map"),
                             advice=advice_for(rows, self.char, env))
        # What this build was computed with: each finding judged here keeps its id.
        self.manifest = manifest.build(self.db, rows, self.kb, ref, scoring=scoring_for(rows, self.char, env))
        # What the tables stand on (wowstats.coverage): every play_sql and
        # query_metric answer carries it, so a 0 isn't read as "never".
        from wowstats import coverage

        self.coverage = coverage.from_rows(rows, self.char)
        # play_sql, the judge and the semantic layer read a locked-down DuckDB
        # copy (wowstats.analysis.duck); the plan and the synthesis's checks
        # keep reading the SQLite build (self.db). The copy with the rows sits
        # in a child process with a memory cap (wowstats.analysis.duckproc),
        # so a query that builds a huge value takes down only that child; this
        # process keeps the empty tables to check and compile queries.
        from . import semantic
        from .duck import ReadOnlyDuck, mirror
        from .duckproc import Worker

        self.duck = mirror(self.db, TABLES, rows=False)
        semantic.prepare(self.duck)
        self.worker = Worker(self.db, TABLES)
        # play_sql and the judge read the views per grain (per_level...) and
        # the prep's tables; the model views (sem_*) stay query_metric's.
        readable = {*TABLES, *semantic.views()}
        self.ro = ReadOnlyDuck(self.duck, tables=readable, worker=self.worker)
        self.judge = ReadOnlyDuck(self.duck, tables=readable, worker=self.worker)
        self.metrics = ReadOnlyDuck(self.duck, tables=None, worker=self.worker)
        self._layer = None
        self.tables, self.ncols = tables_of(self.db)
        self.at = time.time()
        self._text: dict[str, str] = {}

    def text(self, name: str) -> str:
        from . import prep

        if name not in self._text:
            self._text[name] = {"schema": self._schema, "profile": lambda: prep.profile(self.db),
                                "kb": self.kb.contents}[name]()
        return self._text[name]

    def _schema(self) -> str:
        """The governed views per grain first, then the prep's tables as the
        fallback for what no view has (owner, 2026-11-22: "use the semantic
        model all the time")."""
        from . import prep, semantic

        return (semantic.views_text() + "\n\n# Raw tables (fallback: read a view above when it has the "
                "column)\n\n" + prep.schema_text(self.db))

    def layer(self):
        """The semantic layer compiled against this copy (once, on first use)."""
        from . import semantic

        with _LOCK:
            if self._layer is None:
                self._layer = semantic.Layer(self.duck)
            return self._layer

    def kb_has(self, i: str) -> bool:
        return not self.kb.get([i])[0].get("missing")


def workspace(env: dict, char: str, store=None) -> Workspace:
    from wowstats.stores import tenant_of

    key = (tenant_of(env, store), char.lower())
    with _LOCK:
        w = _WS.get(key)
        if w is None or time.time() - w.at > CACHE_S:
            w = _WS[key] = Workspace(env, char, store)
        return w


# ---------------------------------------------------------------- the run's state


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _run_path(env: dict, char: str):
    from . import char_dir

    d = char_dir(env, char)
    return (d / RUN_FILE) if d else None


def _load_run(env: dict, char: str) -> dict | None:
    p = _run_path(env, char)
    if p is not None:
        with contextlib.suppress(OSError, ValueError):
            r = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(r, dict) and r.get("id"):
                return r
    return None


def _save_run(env: dict, char: str, run: dict) -> None:
    from . import writing

    p = _run_path(env, char)
    if p is None:
        raise LookupError("the analysis is off here (ANALYSIS_STATE_DIR unset)")
    run["updated"] = _now()
    with writing(p.parent):
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".drive-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(run, f, ensure_ascii=False)
        os.replace(tmp, p)


def _new_run(goals: str) -> dict:
    return {"id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"), "started": _now(), "goals": goals,
            "pass": "survey", "questions": [], "lanes": [], "ask_next": [], "picks": [], "submits": 0,
            "queries": 0, "lane_queries": 0, "kb_calls": 0, "touched": [], "columns": [], "proposed": 0,
            "verified": {}, "new_keys": [], "refused": [], "coverage_done": False}


def _goals(env: dict, char: str, given: str | None) -> str:
    from . import load_state

    g = (given or "").strip()[:600]
    return g or str(load_state(env, char).get("goals") or "")


def _keep_goals(env: dict, char: str, goals: str) -> None:
    """The player's words stay with the character for the next runs."""
    from . import SCHEMA, load_state, save_state

    state = load_state(env, char)
    if state.get("goals") != goals:
        save_state(env, char, {"schema": SCHEMA, "char": char, "findings": {}, **state, "goals": goals})


def current(env: dict, char: str, goals: str | None = None, restart: bool = False) -> dict:
    """The open run, or a new one (none yet, the last one finished, or
    `restart`). Goals given now replace the run's and the character's."""
    with _LOCK:
        if goals and goals.strip():
            _keep_goals(env, char, goals.strip()[:600])
        run = _load_run(env, char)
        if run is None or restart or run.get("pass") == "done":
            run = _new_run(_goals(env, char, goals))
            _save_run(env, char, run)
        elif goals and goals.strip() and goals.strip()[:600] != run.get("goals"):
            run["goals"] = goals.strip()[:600]
            _save_run(env, char, run)
        return run


def _open_lanes(run: dict) -> list[dict]:
    return [ln for ln in run["lanes"] if not ln.get("done")]


def _q(run: dict, qid: str) -> dict | None:
    return next((q for q in run["questions"] if q["id"] == qid), None)


def _lane_view(run: dict, ln: dict) -> dict:
    return {"lane": ln["id"], "round": ln["round"],
            "questions": [{k: q[k] for k in ("id", "question", "tables")} for qid in ln["questions"]
                          if (q := _q(run, qid))]}


def next_step(run: dict) -> dict:
    """What the driver does now, in words and with what it needs."""
    p = run["pass"]
    if p == "survey":
        return {"pass": "survey", "do": "Read the brief sections schema, profile and kb, then survey; hand in "
                "25 to 40 questions with analysis_submit(pass=\"survey\")."}
    if p == "lanes":
        ln = _open_lanes(run)
        first = ln[0]
        return {"pass": "lane", "lanes_left": len(ln),
                "do": f"Investigate lane {first['id']} (brief section `lane` has the rules), propose each finding, "
                      f"then analysis_submit(pass=\"lane\", answer={{\"lane\": \"{first['id']}\", ...}}).",
                **_lane_view(run, first)}
    if p == "pick":
        return {"pass": "pick", "do": f"Read the brief section `followups` and pick at most {FOLLOWUPS} with "
                "analysis_submit(pass=\"pick\")."}
    if p == "synthesis":
        return {"pass": "synthesis", "do": "Read the brief section `synthesis` (every page), reconcile, then "
                "analysis_submit(pass=\"synthesis\")."}
    return {"pass": "done", "do": "The run is finished. Report to the player what was kept, the nudges and the "
            "refusals; a new run starts with analysis_brief(restart=true)."}


def next_for(env: dict, char: str) -> dict | None:
    """The open run's next step, or None (no run, or it is finished)."""
    run = _load_run(env, char)
    return next_step(run) if run and run.get("pass") != "done" else None


def run_files(env: dict, char: str) -> list:
    """The character's files a run writes: its kept analysis and the run's
    state, and the single file of 0.3.x that the first save removes. The
    server's runner puts them back when its run doesn't finish."""
    from . import STATE_FILE, char_dir, state_dir

    d = char_dir(env, char)
    return [d / STATE_FILE, d / RUN_FILE, state_dir(env) / STATE_FILE] if d else []


# ---------------------------------------------------------------- the brief


def _pages(text: str, size: int = PAGE_CHARS) -> list[str]:
    """Cut at headings and blank lines, a page at most `size` characters."""
    blocks, cur = [], []
    for line in text.split("\n"):
        if (line.startswith("## ") or not line.strip()) and cur:
            blocks.append("\n".join(cur))
            cur = []
        if line.strip() or cur:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    pages, page = [], ""
    for b in blocks:
        while len(b) > size:  # one block over a page: cut it
            if page:
                pages.append(page)
                page = ""
            pages.append(b[:size])
            b = b[size:]
        if page and len(page) + len(b) + 2 > size:
            pages.append(page)
            page = ""
        page = f"{page}\n\n{b}" if page else b
    if page:
        pages.append(page)
    return pages or [""]


def _paged(name: str, text: str, page: int) -> dict:
    ps = _pages(text)
    page = max(1, min(int(page or 1), len(ps)))
    out = {"section": name, "page": page, "pages": len(ps), "text": ps[page - 1]}
    if page < len(ps):
        out["next_page"] = f"analysis_brief(section=\"{name}\", page={page + 1})"
    return out


SECTIONS = {
    "start": "the procedure, the goals, the finding rules and where the run stands",
    "schema": "the views per grain (read first), then every table and column, with their descriptions (paged)",
    "profile": "a neutral profile of every table: counts, spans, distinct and most frequent values (paged)",
    "kb": "the knowledge base's contents: kinds and the documents that aren't single game records (paged)",
    "survey": "the survey pass: task and answer shape",
    "lane": "the lane pass: task, finding shape and answer shape",
    "followups": "the questions lanes proposed to ask next, numbered, and the pick task (paged)",
    "coverage": "the tables no query has read yet",
    "synthesis": "this run's verified findings, the questions asked, the synthesis task and answer shape (paged)",
}


def brief(env: dict, char: str, section: str = "start", page: int = 1, goals: str | None = None,
          restart: bool = False, store=None) -> dict:
    from . import semantic

    w = workspace(env, char, store)
    run = current(env, w.char, goals, restart)
    section = (section or "start").strip().lower()
    if section not in SECTIONS:
        raise ValueError(f"unknown section {section!r}: {', '.join(SECTIONS)}")
    if section == "start":
        out = {"char": w.char, "run": {k: run[k] for k in ("id", "started", "pass")},
               "intro": passes.INTRO, "goals": run.get("goals") or passes.GOALS_UNKNOWN,
               "procedure": passes.PROCEDURE, "finding_rules": passes.FINDING_RULES,
               "sections": {k: {"about": v, **({"pages": len(_pages(w.text(k)))} if k in ("schema", "profile", "kb")
                                               else {})} for k, v in SECTIONS.items() if k != "start"},
               "views": list(semantic.views()), "tables": w.tables, "facts": facts(w.db, w.layer(), w.metrics),
               "next": next_step(run)}
        stale = to_revalidate(env, w.char)
        if stale:
            out["to_revalidate"] = {"findings": stale, "do": REVALIDATE_DO}
        return out
    if section in ("schema", "profile", "kb"):
        return {"char": w.char, **_paged(section, w.text(section), page)}
    if section == "survey":
        return {"char": w.char, "task": passes.SURVEY_TASK, "answer_shape": passes.SURVEY_SCHEMA,
                "tables": w.tables, "next": next_step(run)}
    if section == "lane":
        return {"char": w.char, "task": passes.LANE_TASK, "finding_shape": passes.FINDING,
                "answer_shape": passes.LANE_SCHEMA, "next": next_step(run)}
    if section == "followups":
        listing = "\n".join(f"{i}. {a['question']} (why: {a.get('why', '')}; tables: "
                            f"{', '.join(a.get('tables') or []) or 'any'})" for i, a in enumerate(run["ask_next"]))
        return {"char": w.char, "task": passes.PICK_TASK.format(n=FOLLOWUPS), "answer_shape": passes.PICK_SCHEMA,
                **_paged("followups", listing or "(no question was proposed to ask next)", page)}
    if section == "coverage":
        return {"char": w.char, "task": passes.COVERAGE_TASK, "untouched": _untouched(w, run),
                "next": next_step(run)}
    listing = "## Verified findings of this run\n\n" + "\n\n".join(
        json.dumps(f, ensure_ascii=False) for f in run["verified"].values())
    listing += "\n\n## Questions asked\n\n" + "\n".join(
        f"- [{q['id']}] {q['question']} (answered: {q.get('answered')})" for q in run["questions"])
    return {"char": w.char, "task": passes.SYNTH_TASK, "answer_shape": passes.SYNTH_SCHEMA,
            **_paged("synthesis", listing, page)}


# A silence this long inside a level is a break worth naming beside its pace:
# the demo's evenings leave at most about a day between sessions.
BREAK_S = 3 * 86400
# Worn items listed against the spec they're scored for: built for someone
# else, or under this share of the slot's best.
WEAK_PCT = 25
FACTS_ROWS = 8


# The best upgrades listed under each worn item of gear_fit, wearable now.
UPGRADES_PER_SLOT = 3


def facts(db, layer=None, ro=None) -> dict:
    """Measurements every run gets in the start section, the same whatever
    the client: the levels whose calendar holds a long silence (played
    against calendar time), and the worn items that do least for the spec
    the gear is scored for, each with the best upgrades for its slot under
    that spec, where to get them and their Wowhead pages. Facts, not
    findings: the client still checks them (query_metric, play_sql) and
    decides what they mean.

    Both read the semantic layer (the levels model's calendar metrics, the
    models `worn` and `upgrades`) through `layer` and `ro`, the same compiled
    queries query_metric runs:
    a client gets the decision data without knowing a table (owner,
    2026-11-22: the wrong_spec_gear scenario's three Holy head upgrades were
    reachable through play_sql only). Without them, a copy of `db` of its own
    (semantic.local)."""
    from . import semantic

    if layer is None:
        layer, ro = semantic.local(db)

    def h(v, unit):
        return None if v is None else round(v / unit, 1)

    lv = semantic.rows(layer, ro, {"metrics": ["played_s", "planned_s", "calendar_s", "offline_s", "longest_gap_s",
                                               "longest_gap_from", "longest_gap_to", "recorder_missed_s"],
                                   "group_by": ["level"]})
    long = sorted((r for r in lv if (r["longest_gap_s"] or 0) >= BREAK_S),
                  key=lambda r: (-r["longest_gap_s"], r["level"]))[:FACTS_ROWS]
    breaks = [{"level": r["level"], "played_h": h(r["played_s"], 3600), "planned_h": h(r["planned_s"], 3600),
               "calendar_days": h(r["calendar_s"], 86400), "offline_days": h(r["offline_s"], 86400),
               "longest_gap_days": h(r["longest_gap_s"], 86400), "gap_from": r["longest_gap_from"],
               "gap_to": r["longest_gap_to"], "recorder_missed_h": h(r["recorder_missed_s"], 3600)} for r in long]
    spec, worn, query = gear_fit(layer, ro)
    return {
        "level_calendar": {
            "about": f"levels with a silence of {BREAK_S // 86400} days or more between two sessions (levels: "
                     "calendar_s, offline_s, longest_gap_s, recorder_missed_s): calendar time that isn't play "
                     "unless recorder_missed_h says the Recorder missed some",
            "levels": breaks or "none"},
        "gear_fit": {
            "scored_for": spec or "no gear sheet: unknown",
            "about": f"worn items built for another spec or class (fit), or under {WEAK_PCT}% of the best the "
                     "slot can hold now for that spec (bis_pct). Each has `upgrades`: the best items for that "
                     f"slot wearable now under the scored spec (up to {UPGRADES_PER_SLOT}, `more` when the gear "
                     "advice has others), with `line` in the page's words (say it as is), where to get each "
                     "(`where`, what blocks it included) and the Wowhead pages. `query` is the query_metric "
                     "call that lists them: re-run it, or give it as a finding's metric_query",
            "worn": worn or "none",
            **({"query": query} if query else {})},
    }


def _num(v):
    """A whole number as an int (the prep's REAL columns: 17.0 -> 17)."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


def gear_fit(layer, ro) -> tuple[str | None, list[dict], dict | None]:
    """The scored spec, the worn items that fit it least with their slots'
    best upgrades, and the upgrades' query_metric call (None when no item
    is listed), all from the semantic layer."""
    from wowstats import gear
    from wowstats.links import wowhead_url

    from . import semantic

    def ask(**q) -> list[dict]:
        got = semantic.run(layer, ro, {"limit": semantic.MAX_LIMIT, **q})
        return [dict(zip(got["columns"], r, strict=True)) for r in got["rows"]]

    rows = ask(metrics=["worn_bis_pct"], group_by=["slot", "item_id", "item", "fit", "built_for", "scored_for"])
    spec = next((r["scored_for"] for r in rows if r["scored_for"]), None)
    order = {name: n for n, name in gear.SLOT_NAMES.items()}
    weak = sorted((r for r in rows if r["fit"] in ("spec", "class")
                   or (r["worn_bis_pct"] is not None and r["worn_bis_pct"] < WEAK_PCT)),
                  key=lambda r: (r["worn_bis_pct"] is None, r["worn_bis_pct"] or 0, order.get(r["slot"], 99),
                                 r["item_id"]))[:FACTS_ROWS]
    if not weak:
        return spec, [], None
    query = {"metrics": ["upgrade_gain", "upgrade_bis_pct", "upgrade_bis_pct_worn"],
             "group_by": ["slot", "item_id", "item", "fit"],
             "where": [{"dimension": "slot", "op": "in", "values": sorted({r["slot"] for r in weak})},
                       {"dimension": "rank_now", "op": "<=", "value": UPGRADES_PER_SLOT + 1}],
             "order_by": ["slot", "-upgrade_gain", "item_id"]}
    ups = ask(**query)
    where: dict[int, list[tuple[str, str | None]]] = {}
    if ups:
        for r in ask(metrics=["upgrade_sources"], group_by=["item_id", "source", "source_wowhead"],
                     where=[{"dimension": "item_id", "op": "in", "values": sorted({u["item_id"] for u in ups})}],
                     order_by=["item_id", "source"]):
            where.setdefault(r["item_id"], []).append((r["source"], r["source_wowhead"]))
    by_slot: dict[str, list[dict]] = {}
    for u in ups:
        line = gear.gain_words({"slot_name": u["slot"], "gain": u["upgrade_gain"],
                                "bis_pct": _num(u["upgrade_bis_pct"]), "bis_pct_worn": _num(u["upgrade_bis_pct_worn"])})
        src = where.get(u["item_id"]) or []
        by_slot.setdefault(u["slot"], []).append({
            "item": u["item"], "item_id": u["item_id"], "wowhead": wowhead_url("item", u["item_id"]),
            "gain_points": u["upgrade_gain"], "line": line, "fit": u["fit"],
            "where": [t for t, _ in src], "where_wowhead": [w for _, w in src]})
    worn = []
    for r in weak:
        got = by_slot.get(r["slot"]) or []
        worn.append({"slot": r["slot"], "item": r["item"], "item_id": r["item_id"],
                     "wowhead": wowhead_url("item", r["item_id"]), "bis_pct": _num(r["worn_bis_pct"]),
                     "fit": r["fit"], "built_for": r["built_for"],
                     "upgrades": got[:UPGRADES_PER_SLOT] or "none wearable now in the gear advice",
                     "more": len(got) > UPGRADES_PER_SLOT})
    return spec, worn, query


REVALIDATE_DO = ("Kept findings judged under other definitions (prep version, semantic model or reference data): "
                 "the nightly recheck no longer runs them and the page shows them as to revalidate. In a lane, "
                 "check each with play_sql or query_metric under today's tables and propose it again with the "
                 "same key if it still holds; one you don't propose stays marked.")


def to_revalidate(env: dict, char: str) -> list[dict]:
    """The kept findings marked to revalidate: key, title, why, and the evidence they were judged on."""
    from . import load_state

    found = (load_state(env, char).get("findings") or {}).values()
    return [{"key": f.get("key"), "title": f.get("title"), "why": (f.get("revalidate") or {}).get("why"),
             **({"metric_query": f["metric_query"]} if f.get("metric_query") else {"sql": f.get("sql")})}
            for f in found if f.get("revalidate")]


def _untouched(w: Workspace, run: dict) -> list[str]:
    return sorted(set(w.tables) - set(run["touched"]))


# ---------------------------------------------------------------- SQL


def sql(env: dict, char: str, query: str, page: int = 1, page_size: int = SQL_PAGE, store=None) -> dict:
    from .sql import QueryError, as_text_cut

    w = workspace(env, char, store)
    size = max(1, min(int(page_size or SQL_PAGE), 60))
    page = max(1, int(page or 1))
    skip = (page - 1) * size
    if skip + size > SQL_MAX_ROWS:
        return {"error": f"past row {SQL_MAX_ROWS}: aggregate or add a WHERE instead of paging further"}
    with _LOCK:
        w.ro.touched.clear()
        try:
            cols, rows, cut = w.ro.run(query, skip + size)
            err = None
        except QueryError as e:
            cols, rows, cut, err = [], [], False, str(e)
        run = _count_query(env, w, set(w.ro.touched), query, err)
    if err is not None:
        return {"error": err}
    got = rows[skip:]
    text, short = as_text_cut(cols, got, False, SQL_CHARS)
    out = {"result": text, "page": page, "rows": len(got), "more": bool(cut) or short}
    if cut:
        out["next"] = f"play_sql with page={page + 1} (or aggregate)"
    if run["pass"] == "lanes":
        out["lane_queries"] = run["lane_queries"]
    if page == 1:  # once per query, not on every page
        out["coverage"] = w.coverage
    return out


def _count_query(env: dict, w: Workspace, touched: set[tuple[str, str]], query: str, err: str | None) -> dict:
    """One query of the run, play_sql's or query_metric's: the counters and
    the tables and columns it read, for the coverage. A metric reads the
    semantic layer's views (sem_*), a view per grain (per_level...) the
    sem_* views under it: each counts as the prep tables under those
    (semantic.reads), so a table read through a metric or a view isn't left
    "untouched" (external review of 0.7.0-rc.5). Call with _LOCK held."""
    from .semantic import reads

    real = set(w.tables)
    flat: set[tuple[str, str]] = set()
    for t, c in touched:
        under = reads({t}) - {t}
        if not under:  # a prep table (or nothing the resolver knows)
            flat.add((t, c))
            continue
        for base in under & real:
            if not c or c in w.ro.columns(base):
                flat.add((base, c))
            flat.add((base, ""))
    run = current(env, w.char)
    run["queries"] += 1
    run["lane_queries"] += 1
    if err is None:
        run["touched"] = sorted(({*run["touched"], *(t for t, _ in flat)} | passes.tables_in(query, w.tables)) & real)
        run["columns"] = sorted({*run["columns"], *(f"{t}.{c}" for t, c in flat if c and t in real)})
    _save_run(env, w.char, run)
    return run


def note_kb(env: dict, char: str) -> None:
    """A knowledge base call, for the run's coverage."""
    with _LOCK, contextlib.suppress(LookupError, OSError):
        run = _load_run(env, char)
        if run and run.get("pass") != "done":
            run["kb_calls"] += 1
            _save_run(env, char, run)


# ---------------------------------------------------------------- the judge


def _store_finding(env: dict, w: Workspace, run: dict, got: dict) -> bool:
    from . import SCHEMA, data_mark, keep, load_state, manifest, save_state, score

    now = _now()
    state = load_state(env, w.char)
    kept = dict(state.get("findings") or {})
    # Judged under this build's definitions: a "to revalidate" mark from an
    # older build goes (keep replaces the finding with this one).
    got = {**got, "manifest": w.manifest["id"]}
    new = keep(kept, got, now, data_mark(w.db))
    kept[got["key"]]["score"] = score(kept[got["key"]])
    save_state(env, w.char, {**state, "schema": SCHEMA, "char": w.char, "findings": kept, "updated": now,
                             "manifests": manifest.kept(state.get("manifests"), kept, w.manifest),
                             "goals": run.get("goals") or state.get("goals") or ""})
    run["verified"][got["key"]] = {k: got.get(k) for k in ("key", "title", "text", "sql", "row", "kb", "metric_query")}
    if new and got["key"] not in run["new_keys"]:
        run["new_keys"].append(got["key"])
    return new


def _judge(w: Workspace, finding) -> tuple[dict | None, str, list[str]]:
    from . import verify

    if not isinstance(finding, dict):
        return None, "a finding is an object (analysis_brief section `lane` has its shape)", []
    cited = [str(c) for c in finding.get("kb") or [] if isinstance(c, str)]
    # A metric query reads the layer's views (sem_*), which play_sql can't.
    runner = w.metrics if finding.get("metric_query") else w.judge
    got, why = verify.check(finding, runner, kb_ids=w.kb_has)
    unknown = [c for c in cited if got is not None and c not in got["kb"]]
    return got, why, unknown


def _with_metric_query(w: Workspace, finding):
    """A finding whose evidence is a query_metric call: compiled here, so the
    judge re-runs the same SQL with the same bound values, and the recheck
    keeps both."""
    from .sql import QueryError

    if not isinstance(finding, dict) or not finding.get("metric_query"):
        return finding, None
    if str(finding.get("sql") or "").strip():
        return None, "give sql or metric_query, not both: the judge re-runs one piece of evidence"
    try:
        c = w.layer().compile(finding["metric_query"])
    except QueryError as e:
        return None, f"metric_query: {e}"
    return {**finding, "sql": c["sql"], "sql_params": c["params"], "metric_query": c["query"]}, None


def metric(env: dict, char: str, query: dict, store=None) -> dict:
    """query_metric: the semantic layer's answer, with its SQL and definitions."""
    from . import semantic
    from .sql import QueryError, as_text_cut

    w = workspace(env, char, store)
    with _LOCK:
        w.metrics.touched.clear()
        try:
            got = semantic.run(w.layer(), w.metrics, query)
            err = None
        except QueryError as e:
            got, err = None, str(e)
        _count_query(env, w, set(w.metrics.touched), (got or {}).get("sql") or "", err)
    if err is not None:
        return {"error": err}
    text, short = as_text_cut(got["columns"], [tuple(r) for r in got["rows"]], got["more"], SQL_CHARS)
    out = {"char": w.char, "result": text, "rows": len(got["rows"]), "more": got["more"] or short,
           "query": got["query"], "definitions": got["definitions"],
           "sql": got["sql"], "params": got["params"],
           "evidence": "propose_finding takes this call as evidence: finding.metric_query = query"}
    if got.get("evidence"):
        # An empty cell is unknown or unavailable, never a zero: say which, per metric.
        out["min_evidence"] = got["evidence"]
    out["coverage"] = w.coverage
    return out


def propose(env: dict, char: str, finding: dict, store=None) -> dict:
    """Judge one finding and store it in the character's analysis, or
    refuse it with the reason."""
    w = workspace(env, char, store)
    finding, why_mq = _with_metric_query(w, finding)
    if why_mq:
        return {"stored": False, "why": why_mq, "do": "fix it and propose it once more, or move on"}
    with _LOCK:
        got, why, unknown = _judge(w, finding)
        run = current(env, w.char)
        run["proposed"] += 1
        if got is None:
            run["refused"] = (run["refused"] + [why])[-100:]
            _save_run(env, w.char, run)
            return {"stored": False, "why": why, "do": "fix it and propose it once more, or move on"}
        new = _store_finding(env, w, run, got)
        _save_run(env, w.char, run)
    out = {"stored": True, "new": new, "finding": {k: got[k] for k in ("key", "title", "text", "action", "row")}}
    if got["observation"]:
        out["kept_as"] = ("an observation: no action, so it is shown apart from the findings to act on. Propose it "
                          "again with an action (do, where, blocked_by, next_step) if there is one")
    if unknown:
        out["kb_unknown"] = unknown
        out["note"] = "these KB ids don't exist and were left out: cite ids from kb_search or kb_get"
    return out


# ---------------------------------------------------------------- the passes


def _clean_questions(raw, tables: list[str]) -> list[dict]:
    out = []
    for q in raw or []:
        if isinstance(q, str):
            q = {"question": q, "tables": []}
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        ts = [str(t) for t in q.get("tables") or [] if isinstance(t, str)]
        out.append({"question": str(q["question"]).strip()[:400], "tables": [t for t in ts if t in tables][:6]})
    return out


def _add_lanes(run: dict, qs: list[dict], tables: list[str], per: int, round_: str) -> None:
    n = len(run["lanes"])
    for i, group in enumerate(passes.lanes_of(qs, tables, per)):
        run["lanes"].append({"id": f"L{n + i + 1}", "round": round_, "questions": [q["id"] for q in group],
                             "done": False})
    run["pass"] = "lanes"


def _add_questions(run: dict, qs: list[dict], round_: str, prefix: str) -> list[dict]:
    base = sum(1 for q in run["questions"] if q["id"].startswith(prefix))
    out = []
    for i, q in enumerate(qs):
        q = {"id": f"{prefix}{base + i + 1}", "question": q["question"], "tables": q["tables"], "round": round_,
             **({"added": True} if q.get("added") else {})}
        run["questions"].append(q)
        out.append(q)
    return out


def _after_round(w: Workspace, run: dict) -> None:
    """All lanes handed in: follow-ups after the survey's round, then a
    coverage round for unread tables, then the synthesis."""
    if _open_lanes(run):
        return
    if not run.get("picked") and run["ask_next"]:
        run["pass"] = "pick"
        return
    untouched = _untouched(w, run)
    if untouched and not run["coverage_done"]:
        run["coverage_done"] = True
        cq = []
        for t in untouched:
            q = next((q for q in run["questions"] if t in q["tables"] and q["round"] == "survey"), None)
            cq.append({"question": (q or {}).get("question") or
                       f"What does the {t} table show that bears on the goals?", "tables": [t]})
        _add_lanes(run, _add_questions(run, cq, "coverage", "c"), w.tables, 1, "coverage")
        return
    run["pass"] = "synthesis"


def submit(env: dict, char: str, pass_: str, answer: dict, store=None) -> dict:
    w = workspace(env, char, store)
    pass_ = (pass_ or "").strip().lower()
    if not isinstance(answer, dict):
        return {"accepted": False, "why": "answer must be an object (analysis_brief has each pass's answer_shape)"}
    with _LOCK:
        run = current(env, w.char)
        want = {"survey": "survey", "lanes": "lane", "pick": "pick", "synthesis": "synthesis"}.get(run["pass"])
        early = pass_ == "synthesis" and run["pass"] in ("lanes", "pick") and answer.get("early") is True
        if pass_ != want and not early:
            return {"accepted": False, "why": f"the run is at {run['pass']}, not {pass_}", "next": next_step(run)}
        fn = {"survey": _submit_survey, "lane": _submit_lane, "pick": _submit_pick,
              "synthesis": _submit_synthesis}[pass_]
        out = fn(env, w, run, answer)
        if out.get("accepted"):
            run["submits"] += 1
        _save_run(env, w.char, run)
        return {**out, "next": next_step(run)}


def _submit_survey(env, w, run, answer):
    qs = _clean_questions(answer.get("questions"), w.tables)
    if len(qs) < MIN_QUESTIONS:
        return {"accepted": False, "why": f"{len(qs)} questions: write at least {MIN_QUESTIONS} (25 to 40 is the aim)"}
    qs = qs[:MAX_QUESTIONS]
    covered = {t for q in qs for t in q["tables"]}
    for t in w.tables:  # a table no question names gets one, in the same neutral words
        if t not in covered:
            qs.append({"question": f"What does the {t} table show that bears on the goals?", "tables": [t],
                       "added": True})
    added = _add_questions(run, qs, "survey", "q")
    _add_lanes(run, added, w.tables, PER_LANE, "survey")
    run["lane_queries"] = 0
    return {"accepted": True, "questions": len(added), "added_for_coverage": sum(1 for q in added if q.get("added")),
            "lanes": len(run["lanes"])}


def _submit_lane(env, w, run, answer):
    lid = str(answer.get("lane") or "")
    ln = next((x for x in run["lanes"] if x["id"] == lid), None)
    if ln is None or ln.get("done"):
        open_ = [x["id"] for x in _open_lanes(run)]
        return {"accepted": False, "why": f"no open lane {lid!r}; open lanes: {', '.join(open_) or 'none'}"}
    answers = [a for a in answer.get("answers") or []
               if isinstance(a, dict) and a.get("question_id") in ln["questions"]]
    # An answer's note never reaches the synthesis, only findings do: a
    # question answered with nothing verified behind it goes back once
    # (gpt-6.1-sol noted "Wetlands has eight deaths" and proposed nothing,
    # 2026-11-22). The second hand-in is taken as it is.
    unbacked = [a for a in answers if a.get("answered") and not
                [k for k in a.get("findings") or [] if isinstance(k, str) and k in run["verified"]]]
    if unbacked and not ln.get("sent_back"):
        ln["sent_back"] = True
        listed = "; ".join(f"{a['question_id']} (your note: {str(a.get('note') or '')[:200]!r})" for a in unbacked)
        return {"accepted": False, "unbacked": [a["question_id"] for a in unbacked],
                "why": f"answered without a finding behind it: {listed}. Only findings reach the player; a note "
                       "is dropped. For each, propose_finding what the data showed (with action null when there "
                       "is nothing to act on: it is kept as an observation), then hand the lane in again with "
                       "the finding keys in that answer's `findings`; or set answered false if the data didn't "
                       "answer it."}
    answered = 0
    for a in answers:
        q = _q(run, a["question_id"])
        q["answered"] = bool(a.get("answered"))
        q["note"] = str(a.get("note") or "")[:300]
        q["findings"] = [k for k in a.get("findings") or [] if isinstance(k, str) and k in run["verified"]][:10]
        answered += q["answered"]
    if ln["round"] == "survey":  # only the first round's "ask next" feeds the follow-ups
        for a in (answer.get("ask_next") or [])[:5]:
            if isinstance(a, dict) and str(a.get("question") or "").strip():
                run["ask_next"].append({"question": str(a["question"]).strip()[:400],
                                        "why": str(a.get("why") or "")[:200],
                                        "tables": [t for t in a.get("tables") or [] if t in w.tables][:6]})
    ln["done"] = True
    ln["queries"] = run["lane_queries"]
    run["lane_queries"] = 0
    _after_round(w, run)
    return {"accepted": True, "lane": lid, "answered": answered, "lanes_left": len(_open_lanes(run))}


def _submit_pick(env, w, run, answer):
    picks = [i for i in answer.get("picks") or [] if isinstance(i, int) and 0 <= i < len(run["ask_next"])]
    picks = list(dict.fromkeys(picks))[:FOLLOWUPS]
    run["picks"], run["picked"] = picks, True
    fq = [{"question": run["ask_next"][i]["question"], "tables": run["ask_next"][i]["tables"]} for i in picks]
    if fq:
        _add_lanes(run, _add_questions(run, fq, "followup", "f"), w.tables, PER_FOLLOWUP_LANE, "followup")
    else:
        _after_round(w, run)
    return {"accepted": True, "followups": len(fq)}


def _submit_synthesis(env, w, run, answer):
    from . import bind, current_level, feasible, finish, load_state, other_players
    from .sql import QueryError
    from .verify import history_read

    # New or merged findings handed in with the synthesis: judged like any,
    # and kept with the ones it names.
    handed = []
    for f in answer.get("findings") or []:
        got, why, _ = _judge(w, f)
        run["proposed"] += 1
        if got is None:
            run["refused"] = (run["refused"] + [why])[-100:]
        else:
            _store_finding(env, w, run, got)
            handed.append(got["key"])
    state = load_state(env, w.char)
    kept = dict(state.get("findings") or {})
    verified = run["verified"]
    clashes = {"found": 0, "merged": 0, "doubtful": 0}
    replaced: set[str] = set()
    into: dict[str, str] = {}  # a merged finding replaces the ones it reconciles, for the lines citing them too
    for c in answer.get("clashes") or []:
        if not isinstance(c, dict):
            continue
        keys = [k for k in c.get("findings") or [] if k in verified]
        if len(keys) < 2:
            continue
        clashes["found"] += 1
        try:
            w.ro.run(str(c.get("query") or ""), 1)
            ran = bool(history_read(w.ro))  # a constant reconciles nothing
        except QueryError:
            ran = False
        mk = str(c.get("merged_key") or "")
        if c.get("outcome") == "merged" and ran and mk in verified and mk in kept and mk not in keys:
            clashes["merged"] += 1
            replaced.update(keys)
            into.update(dict.fromkeys(keys, mk))
            kept[mk]["reconciles"] = keys
            continue
        clashes["doubtful"] += 1
        for k in keys:
            if k in kept:
                kept[k]["doubtful"] = str(c.get("subject") or "")[:120] or True
    keep_keys = [k for k in answer.get("keep") or [] if k in verified and k not in replaced]
    keep_keys += [k for k in handed if k not in keep_keys and k not in replaced]
    keep_keys += [k for k in verified if k in kept and kept[k].get("doubtful") and k not in keep_keys]
    # A finding leaves only when the synthesis says why (`drop`): one it names
    # nowhere stays. claude-sonnet-5-5 left "Almost half the deaths are in
    # Wetlands" out of keep with no reason, and the deaths went unsaid
    # (2026-11-22).
    drops = {str(d.get("key")): str(d.get("why") or "no reason given")[:160]
             for d in answer.get("drop") or [] if isinstance(d, dict) and d.get("key") in verified}
    unlisted = [k for k in verified if k not in keep_keys and k not in drops and k not in replaced]
    keep_keys += unlisted
    dropped = []
    for k in list(verified):
        gone = k in replaced or (k not in keep_keys and k in run["new_keys"])
        if gone and kept.pop(k, None) is not None:
            dropped.append(f"{k}: " + ("merged after a clash" if k in replaced else f"dropped ({drops[k]})"))
    def cites(line):
        return list(dict.fromkeys(into.get(str(k), str(k)) for k in line.get("findings") or []))

    raw = []
    for n in answer.get("nudges") or []:
        if not isinstance(n, dict):
            continue
        if len(str(n.get("next_step") or "").split()) < 3:
            dropped.append(f"nudge \"{str(n.get('text') or '')[:60]}\": no next_step (the first thing to do, where)")
            continue
        raw.append({**n, "findings": cites(n)})
    lvl = current_level(w.db, w.layer(), w.metrics)
    nudges, feas = feasible(raw, lvl, w.kb_has, dropped)
    patterns = [{"text": str(p.get("text") or "")[:240], "findings": cites(p)}
                for p in (answer.get("patterns") or [])[:6] if isinstance(p, dict)]
    nudges = bind(nudges, kept, lvl, dropped, "nudge")
    patterns = bind(patterns, kept, lvl, dropped, "pattern")
    asked = run["questions"]
    drv = DRIVER.get() or {}
    # Who refreshed it and through what: the server's timer for this
    # character starts over from here (wowstats.analysis.auto, last_refresh).
    info = {"at": _now(), "kind": drv.get("kind") or "client", "by": drv.get("by") or "player",
            "via": drv.get("via") or "client",
            "driver": str(drv.get("driver") or answer.get("driver") or "")[:80] or None,
            "run": run["id"], "manifest": w.manifest["id"], "passes": run["submits"] + 1,
            "questions": {"asked": len(asked), "answered": sum(1 for q in asked if q.get("answered")),
                          "added_for_coverage": sum(1 for q in asked if q.get("added")),
                          "followups": sum(1 for q in asked if q["round"] == "followup"),
                          "coverage_round": sum(1 for q in asked if q["round"] == "coverage")},
            "coverage": {"tables": len(set(run["touched"])), "tables_total": len(w.tables),
                         "untouched": _untouched(w, run), "columns": len(run["columns"]), "columns_total": w.ncols,
                         "sql_calls": run["queries"], "kb_calls": run["kb_calls"]},
            "findings": {"proposed": run["proposed"], "verified": len(verified),
                         "kept": len([k for k in keep_keys if k in kept])},
            "clashes": clashes, "feasibility": feas, "new": len([k for k in run["new_keys"] if k in kept]),
            "confirmed": 0, "faded": 0, "dropped": dropped, "refused": len(_refusals(run)),
            "seconds_total": round((datetime.now(UTC) - datetime.fromisoformat(run["started"])).total_seconds())}
    out = finish(env, w.char, {**state, "goals": run.get("goals") or state.get("goals") or ""}, kept, nudges,
                 patterns, w.db, w.kb, other_players(w.store, w.store.stream, w.char), info, info["at"],
                 w.manifest, (w.layer(), w.metrics))
    run["pass"] = "done"
    run["result"] = info
    return {"accepted": True, "kept": sorted(out["findings"]),
            "nudges": [{k: n.get(k) for k in ("text", "verdict", "at_level", "shareable", "why_not")}
                       for n in out["nudges"]],
            "patterns": [{k: p.get(k) for k in ("text", "shareable", "why_not")} for p in out["patterns"]],
            "clashes": clashes, "feasibility": feas, "dropped": out["runs"][-1]["dropped"],
            "kept_unlisted": unlisted, "refused": _refusals(run)[-20:]}


# ---------------------------------------------------------------- progress


def _refusals(run: dict) -> list[str]:
    """Refusals still standing: one whose key a later proposal got verified
    was fixed on the retry."""
    return [r for r in run["refused"] if r.split(":", 1)[0] not in run["verified"]]


def progress(env: dict, char: str, store=None) -> dict:
    from . import load_state

    w = workspace(env, char, store)
    run = _load_run(env, w.char)
    state = load_state(env, w.char)
    runs = state.get("runs") or []
    recheck = next((r for r in reversed(runs) if r.get("kind") == "recheck"), None)
    last = next((r for r in reversed(runs) if r.get("kind") in CLIENT_KINDS), None)
    kept = (state.get("findings") or {}).values()
    out = {"char": w.char, "kept_findings": len(state.get("findings") or {}),
           "to_revalidate": sum(1 for f in kept if f.get("revalidate")),
           "last_recheck": {k: recheck.get(k) for k in ("at", "confirmed", "still", "faded", "revalidate")}
           if recheck else None,
           "last_analysis": last}
    if run is None:
        return {**out, "run": None, "next": {"pass": "survey", "do": "No run yet: call analysis_brief to start one."}}
    asked = run["questions"]
    return {**out, "run": {
        "id": run["id"], "started": run["started"], "pass": run["pass"], "goals": run.get("goals") or None,
        "questions": {"asked": len(asked), "answered": sum(1 for q in asked if q.get("answered"))},
        "lanes": {"total": len(run["lanes"]), "done": sum(1 for x in run["lanes"] if x.get("done")),
                  "open": [x["id"] for x in _open_lanes(run)]},
        "queries": run["queries"], "kb_calls": run["kb_calls"],
        "tables_touched": len(run["touched"]), "tables_total": len(w.tables), "untouched": _untouched(w, run),
        "proposed": run["proposed"], "verified": len(run["verified"]), "refused": _refusals(run)[-20:],
        "fixed_on_retry": len(run["refused"]) - len(_refusals(run))},
        "next": next_step(run)}
