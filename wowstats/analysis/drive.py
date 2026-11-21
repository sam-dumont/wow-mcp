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

        from . import TABLES, kb, prep, tables_of, upgrades_for

        st = store_for(env, store)
        self.store = st
        self.char = prep.norm(char)
        rows = prep.query(st, st.stream, self.char)
        if not rows:
            raise LookupError(f"no events for {self.char}")
        self.kb = kb.shared(env)
        self.db = prep.build(rows, self.char, route.reference(), map_sizes=self.kb.meta.get("ui_map"),
                             upgrades=upgrades_for(rows, self.char, env))
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
        self.ro = ReadOnlyDuck(self.duck, tables=TABLES, worker=self.worker)
        self.judge = ReadOnlyDuck(self.duck, tables=TABLES, worker=self.worker)
        self.metrics = ReadOnlyDuck(self.duck, tables=None, worker=self.worker)
        self._layer = None
        self.tables, self.ncols = tables_of(self.db)
        self.at = time.time()
        self._text: dict[str, str] = {}

    def text(self, name: str) -> str:
        from . import prep

        if name not in self._text:
            self._text[name] = {"schema": lambda: prep.schema_text(self.db), "profile": lambda: prep.profile(self.db),
                                "kb": self.kb.contents}[name]()
        return self._text[name]

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
    p = _run_path(env, char)
    if p is None:
        raise LookupError("the analysis is off here (ANALYSIS_STATE_DIR unset)")
    p.parent.mkdir(parents=True, exist_ok=True)
    run["updated"] = _now()
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
    "schema": "every table and column with its description (paged)",
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
    w = workspace(env, char, store)
    run = current(env, w.char, goals, restart)
    section = (section or "start").strip().lower()
    if section not in SECTIONS:
        raise ValueError(f"unknown section {section!r}: {', '.join(SECTIONS)}")
    if section == "start":
        return {"char": w.char, "run": {k: run[k] for k in ("id", "started", "pass")},
                "intro": passes.INTRO, "goals": run.get("goals") or passes.GOALS_UNKNOWN,
                "procedure": passes.PROCEDURE, "finding_rules": passes.FINDING_RULES,
                "sections": {k: {"about": v, **({"pages": len(_pages(w.text(k)))} if k in ("schema", "profile", "kb")
                                                else {})} for k, v in SECTIONS.items() if k != "start"},
                "tables": w.tables, "next": next_step(run)}
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


def _untouched(w: Workspace, run: dict) -> list[str]:
    return sorted(set(w.tables) - set(run["touched"]))


# ---------------------------------------------------------------- SQL


def sql(env: dict, char: str, query: str, page: int = 1, page_size: int = SQL_PAGE, store=None) -> dict:
    from .sql import QueryError, as_text

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
        touched = set(w.ro.touched)
        run = current(env, w.char)
        run["queries"] += 1
        run["lane_queries"] += 1
        run["touched"] = sorted(({*run["touched"], *(t for t, _ in touched)} | passes.tables_in(query, w.tables))
                                & set(w.tables)) if err is None else run["touched"]
        run["columns"] = sorted({*run["columns"], *(f"{t}.{c}" for t, c in touched if c)})
        _save_run(env, w.char, run)
    if err is not None:
        return {"error": err}
    got = rows[skip:]
    out = {"result": as_text(cols, got, False, SQL_CHARS), "page": page, "rows": len(got), "more": bool(cut)}
    if cut:
        out["next"] = f"play_sql with page={page + 1} (or aggregate)"
    if run["pass"] == "lanes":
        out["lane_queries"] = run["lane_queries"]
    return out


def note_kb(env: dict, char: str) -> None:
    """A knowledge base call, for the run's coverage."""
    with _LOCK, contextlib.suppress(LookupError, OSError):
        run = _load_run(env, char)
        if run and run.get("pass") != "done":
            run["kb_calls"] += 1
            _save_run(env, char, run)


# ---------------------------------------------------------------- the judge


def _store_finding(env: dict, w: Workspace, run: dict, got: dict) -> bool:
    from . import SCHEMA, keep, load_state, save_state, score

    now = _now()
    state = load_state(env, w.char)
    kept = dict(state.get("findings") or {})
    new = keep(kept, got, now)
    kept[got["key"]]["score"] = score(kept[got["key"]])
    save_state(env, w.char, {**state, "schema": SCHEMA, "char": w.char, "findings": kept, "updated": now,
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
    from .sql import QueryError, as_text

    w = workspace(env, char, store)
    try:
        got = semantic.run(w.layer(), w.metrics, query)
    except QueryError as e:
        return {"error": str(e)}
    out = {"char": w.char, "result": as_text(got["columns"], [tuple(r) for r in got["rows"]], got["more"], SQL_CHARS),
           "rows": len(got["rows"]), "more": got["more"], "query": got["query"], "definitions": got["definitions"],
           "sql": got["sql"], "params": got["params"],
           "evidence": "propose_finding takes this call as evidence: finding.metric_query = query"}
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
    answered = 0
    for a in answer.get("answers") or []:
        if isinstance(a, dict) and a.get("question_id") in ln["questions"]:
            q = _q(run, a["question_id"])
            q["answered"] = bool(a.get("answered"))
            q["note"] = str(a.get("note") or "")[:300]
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
            ran = True
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
    if not keep_keys:
        keep_keys = [k for k in verified if k not in replaced]
    dropped = []
    for k in list(verified):
        gone = k in replaced or (k not in keep_keys and k in run["new_keys"])
        if gone and kept.pop(k, None) is not None:
            dropped.append(f"{k}: " + ("merged after a clash" if k in replaced else "left out of keep"))
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
    nudges, feas = feasible(raw, current_level(w.db), w.kb_has, dropped)
    patterns = [{"text": str(p.get("text") or "")[:240], "findings": cites(p)}
                for p in (answer.get("patterns") or [])[:6] if isinstance(p, dict)]
    lvl = current_level(w.db)
    nudges = bind(nudges, kept, lvl, dropped, "nudge")
    patterns = bind(patterns, kept, lvl, dropped, "pattern")
    asked = run["questions"]
    drv = DRIVER.get() or {}
    info = {"at": _now(), "kind": drv.get("kind") or "client",
            "driver": str(drv.get("driver") or answer.get("driver") or "")[:80] or None,
            "run": run["id"], "passes": run["submits"] + 1,
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
                 patterns, w.db, w.kb, other_players(w.store, w.store.stream, w.char), info, info["at"])
    run["pass"] = "done"
    run["result"] = info
    # The player's analysis is fresh again, whoever drove it: their timer
    # for the server's own runs starts over (wowstats.analysis.auto).
    with contextlib.suppress(Exception):
        from . import auto

        auto.mark_refreshed(env, drv.get("by") or "player", drv.get("via") or "client", drv.get("played_s"))
    return {"accepted": True, "kept": sorted(out["findings"]),
            "nudges": [{k: n.get(k) for k in ("text", "verdict", "at_level", "shareable", "why_not")}
                       for n in out["nudges"]],
            "patterns": [{k: p.get(k) for k in ("text", "shareable", "why_not")} for p in out["patterns"]],
            "clashes": clashes, "feasibility": feas, "dropped": out["runs"][-1]["dropped"],
            "refused": _refusals(run)[-20:]}


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
    out = {"char": w.char, "kept_findings": len(state.get("findings") or {}),
           "last_recheck": {k: recheck.get(k) for k in ("at", "confirmed", "faded")} if recheck else None,
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
