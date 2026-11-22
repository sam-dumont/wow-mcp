"""The seeded evaluation of the play analysis: run one scenario, or all, with
a real MCP client, and grade what it kept.

    uv run --extra mcp --extra analysis-openai python tools/eval_analysis/run.py \\
        [--scenario NAME ...] [--client claude|openai|server[:MODEL] ...] [--repeats N] [--jobs N]
        [--max-usd X] [--model M] [--out DIR] [--judge]
    make eval-analysis                      # every scenario once, Claude Code
    make eval-analysis SCENARIO=death_spike
    ... --client claude:claude-haiku-5-5 --client server:gpt-6-luna   # a client matrix

Per scenario: build its events (scenarios.py), start a throwaway server on
them (server.py, its own state folder), let the client run the whole
analysis through MCP (clients.py), then re-run every kept finding's query on
a fresh build and grade the kept lines against the truth (grade.py). One
run per process, all of them at once by default (--jobs): a run mostly
waits on the model, and each has its own server, client and folder.

--repeats runs each scenario N times per client: a model doesn't answer the
same way twice, so one run says little. A scenario passes when at least
two thirds of its runs pass (GATE); release.md gives the rate per client
and scenario.

Clients: `--client kind:model` names a model per column, so one batch is a
matrix (claude:claude-haiku-5-5, claude:claude-sonnet-5-5, server:gpt-6-luna,
server:glm-5.3 with EVAL_SERVER_BASE_URL...). The analysis runs in the
player's own AI: the evaluation asks whether what MCP hands a client is
enough for the clients people use. It's an optional tool, not a release
gate (owner, 2026-11-22: "we provide good data; if a model doesn't act
properly we accept it"). A scenario below two thirds on most clients is
the brief's or the MCP's problem, not one model's: release.md flags it.

Where it goes: --out, else `eval-analysis-out/` in this checkout (ignored by
git; the data is synthetic), one folder per batch, then
<client>/<scenario>/r<N>/ per run: events.jsonl, the server's settings and
state, transcript.jsonl (what the client did), report.json and report.md.
summary.md and release.md cover the batch.

Not in CI: a run needs a model client, and hosted CI has none.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import clients  # noqa: E402
import grade  # noqa: E402
import scenarios  # noqa: E402
import server  # noqa: E402

CLOSED_PORT_URL = "http://127.0.0.1:9"  # nothing listens: a read the scenario doesn't answer fails at once
# A scenario passes the gate when this share of its runs pass: 2 of 3 by
# default. Below it, the release table flags it.
GATE = 2 / 3
# The server client's runs at once (--server-jobs): one gpt-6.1-sol run sends
# about 1M tokens a minute, cached prompt included, and fifteen side by side
# crossed a 4M tokens-per-minute limit within a minute: twelve of fifteen
# stopped on a 429 (2026-11-22). Claude Code runs on a login, not a key.
SERVER_JOBS = 3


def server_settings(sc: dict, state: Path) -> dict:
    import os

    return {"ANALYSIS_STATE_DIR": str(state), "VL_URL": CLOSED_PORT_URL, "DEFAULT_CHAR": scenarios.CHAR,
            "VIEW": "full", "WOW_TIMEZONE": "Europe/Brussels", "FASTMCP_CHECK_FOR_UPDATES": "off",
            "KB_GAME_TABLES": os.environ.get("EVAL_KB_GAME_TABLES", "off"), "PYTHONUNBUFFERED": "1",
            **(sc.get("env") or {})}


def run_state(state: Path) -> tuple[dict | None, dict | None]:
    """(the run's drive.json, the kept analysis.json) in a server's state folder."""
    from wowstats.analysis import char_dir

    d = char_dir({"ANALYSIS_STATE_DIR": str(state)}, scenarios.CHAR)
    out = []
    for name in ("drive.json", "analysis.json"):
        p = d / name if d else None
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")) if p and p.is_file() else None)
        except (OSError, ValueError):
            out.append(None)
    return out[0], out[1]


def workspace(rows: list[dict], settings: dict):
    """A fresh build of the scenario's database, in this process: the
    grader's own, separate from the server the client talked to."""
    from wowstats.analysis import drive

    server.serve_events(rows)
    return drive.Workspace(dict(settings), scenarios.CHAR)


def regrade(d: Path) -> dict:
    """Grade a finished scenario folder again, with today's rules, without a
    client: the kept analysis, its events and settings are all there."""
    old = json.loads((d / "report.json").read_text(encoding="utf-8"))
    sc = scenarios.load(old["scenario"])
    rows = scenarios.read_events(d / "events.jsonl")
    settings = json.loads((d / "settings.json").read_text(encoding="utf-8"))
    run, analysis = run_state(d / "state")
    reruns = None
    if old["client"]["done"] and analysis:
        w = workspace(rows, settings)
        try:
            reruns = grade.rerun(analysis, w.judge, w.metrics)
        finally:
            w.worker.close()
    report = {**old, **grade.grade(sc, analysis if old["client"]["done"] else None, old["client"], reruns),
              "expected": sc["truth"]["expected"], "reached": (run or {}).get("pass"),
              "regraded": datetime.now(UTC).isoformat(timespec="seconds")}
    (d / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (d / "report.md").write_text(markdown(report), encoding="utf-8")
    return report


def layout(out: Path, client: str, name: str, repeat: int = 1) -> dict:
    """Everything one run owns, under out/<client>/<scenario>/r<N>/: nothing
    is shared with another run going at the same time. Its server talks to
    its own client over stdio (no port at all); VL_URL is a closed port
    nothing ever answers."""
    d = out / client.replace(":", "_") / name / f"r{repeat}"
    return {"dir": d, "work": d / "work", "state": d / "state", "events": d / "events.jsonl",
            "settings": d / "settings.json", "transcript": d / "transcript.jsonl"}


def one(name: str, client, out: Path, timeout_s: float, use_judge: bool, repeat: int = 1,
        col: str = "") -> dict:
    sc = scenarios.load(name)
    col = col or client.name
    p = layout(out, col, name, repeat)
    d, state = p["dir"], p["state"]
    if d.exists():
        shutil.rmtree(d)
    p["work"].mkdir(parents=True)
    state.mkdir()
    rows = scenarios.build(sc)
    scenarios.write_events(rows, p["events"])
    settings = server_settings(sc, state)
    p["settings"].write_text(json.dumps(settings, indent=1), encoding="utf-8")
    facts = scenarios.facts(sc, rows)
    tag = f"{col}/{name}/r{repeat}"
    print(f"[{tag}] {len(rows)} events; running", flush=True)

    def is_done() -> bool:
        run, _ = run_state(state)
        return bool(run) and run.get("pass") == "done"

    cmd = [sys.executable, str(HERE / "server.py"), str(p["settings"]), str(p["events"])]
    job = clients.Job(char=scenarios.CHAR, goals=sc["goals"], server=cmd,
                      workdir=p["work"], transcript=p["transcript"], is_done=is_done, timeout_s=timeout_s,
                      settings=p["settings"], events=p["events"])
    res = {**client.run(job).as_dict(), "column": col}
    run, analysis = run_state(state)
    reruns = None
    if res["done"] and analysis:
        w = workspace(rows, settings)
        try:
            reruns = grade.rerun(analysis, w.judge, w.metrics)
        finally:
            w.worker.close()
    verdict = grade.grade(sc, analysis if res["done"] else None, res, reruns)
    report = {"scenario": name, "repeat": repeat, "about": sc["about"], "expected": sc["truth"]["expected"], **verdict,
              "client": res, "facts": facts, "at": datetime.now(UTC).isoformat(timespec="seconds"),
              "run": (run or {}).get("result"), "reached": (run or {}).get("pass"),
              "kept": [ln.__dict__ for ln in grade.lines(analysis)] if analysis and res["done"] else []}
    if use_judge and analysis and res["done"]:
        import judge

        report["judge"] = judge.judge(sc, analysis)
    (d / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (d / "report.md").write_text(markdown(report), encoding="utf-8")
    print(f"[{tag}] {verdict['verdict']} ({res['seconds'] / 60:.1f} min)", flush=True)
    return report


def _tokens(c: dict) -> str:
    t = c.get("tokens") or {}
    if not t:
        return "unknown"
    parts = [f"{k} {int(v):,}" for k, v in t.items() if isinstance(v, (int, float)) and v]
    return ", ".join(parts) or "unknown"


def _tools(c: dict) -> str:
    t = c.get("tools") or {}
    return ", ".join(f"{k} {v}" for k, v in sorted(t.items(), key=lambda kv: -kv[1])) or "none"


def markdown(r: dict) -> str:
    c = r["client"]
    out = [f"# {r['scenario']}: {r['verdict']}", "", r["about"], "", f"**Expected:** {r['expected']}", "",
           f"**Client:** {c['client']} ({c.get('model') or 'model unknown'}), {c['rounds']} conversation(s), "
           f"{c['calls']} tool calls, {c['seconds'] / 60:.1f} min, reached the {r.get('reached') or '?'} pass. "
           f"**Calls per tool:** {_tools(c)}. **Tokens:** {_tokens(c)}."
           + (f" **List-price cost the client reported:** ${c['cost_usd']} (a subscription login isn't billed "
              "it)." if c.get("cost_usd") is not None else ""),
           "", "| Check | OK | Detail |", "|---|---|---|"]
    for ch in r["checks"]:
        out.append(f"| {ch['check']} | {'yes' if ch['ok'] else '**no**'} | {ch.get('detail', '')} |")
    for ch in r["checks"]:
        if ch.get("evidence"):
            out += ["", f"**{ch['check']}**:", *[f"- {e}" for e in ch["evidence"]]]
    if r.get("judge"):
        out += ["", f"**Judge (wording only):** {r['judge']}"]
    if r["kept"]:
        out += ["", "## What the analysis kept", ""]
        out += [f"- **{k['kind']} {k['key']}**: {k['text']}" for k in r["kept"]]
    return "\n".join(out) + "\n"


def summary(reports: list[dict]) -> str:
    out = ["| Scenario | Run | Verdict | Failed checks | Kept lines | Client | Minutes | Tool calls | Reached | "
           "Cost | Tokens |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reports:
        c = r["client"]
        bad = ", ".join(ch["check"] for ch in r["checks"] if not ch["ok"]) or "none"
        cost = f"${c['cost_usd']:.2f}" if c.get("cost_usd") is not None else "-"
        out.append(f"| {r['scenario']} | {r.get('repeat', 1)} | {r['verdict']} | {bad} | {len(r['kept'])} | "
                   f"{c['client']} ({c.get('model') or '?'}) | {c['seconds'] / 60:.1f} | {c.get('calls', 0)} | "
                   f"{r.get('reached') or '?'} | {cost} | {_tokens(c)} |")
    return "\n".join(out) + "\n"


def who(r: dict) -> str:
    c = r["client"]
    return f"{c['client']} ({c['model']})" if c.get("model") else c["client"]


def col_of(r: dict) -> str:
    """The report's column: its client and model as asked (`claude:claude-haiku-5-5`); older reports, the kind."""
    return r["client"].get("column") or r["client"]["client"]


def rates(reports: list[dict]) -> dict[tuple[str, str], tuple[int, int]]:
    """(column, scenario) -> (runs passed, runs)."""
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for r in reports:
        k = (col_of(r), r["scenario"])
        p, n = out.get(k, (0, 0))
        out[k] = (p + (r["verdict"] == "pass"), n + 1)
    return out


def gate_ok(passed: int, runs: int) -> bool:
    return runs > 0 and passed >= math.ceil(GATE * runs - 1e-9)


def release_table(reports: list[dict], wall_min: float | None = None, spent: float | None = None) -> str:
    """The pass rate per client and scenario: a scenario under two
    thirds is flagged."""
    r8 = rates(reports)
    cols = list(dict.fromkeys(col_of(r) for r in reports))
    names = list(dict.fromkeys(r["scenario"] for r in reports))
    label = {c: c if ":" in c else next(who(r) for r in reports if col_of(r) == c) for c in cols}
    out = ["| Scenario | " + " | ".join(label[c] for c in cols) + " |", "|---|" + "---|" * len(cols)]
    for n in names:
        cells = []
        for c in cols:
            p, k = r8.get((c, n), (0, 0))
            cells.append(f"{p}/{k}" + ("" if gate_ok(p, k) else " **below 2/3**") if k else "-")
        out.append(f"| {n} | " + " | ".join(cells) + " |")
    out += ["", "A scenario passes when at least two thirds of its runs pass (2 of 3)."
            + (f" Wall time {wall_min:.0f} min." if wall_min is not None else " Regraded with today's rules.")
            + (f" The server's models cost ${spent:.2f}." if spent is not None else "")]
    for n, bad in widespread(reports).items():
        out += ["", f"**{n} is below 2/3 on {len(bad)} of {len(cols)} clients** ({', '.join(bad)}): most "
                "clients miss it, so look at what MCP hands them (the brief, its facts, the tools' answers) "
                "before blaming a model."]
    return "\n".join(out) + "\n"


def widespread(reports: list[dict]) -> dict[str, list[str]]:
    """Scenarios below two thirds on most clients (more than half, three
    clients at least): a brief or MCP problem, not one model's."""
    r8 = rates(reports)
    cols = list(dict.fromkeys(col_of(r) for r in reports))
    out = {}
    for n in dict.fromkeys(r["scenario"] for r in reports):
        bad = [c for c in cols if r8.get((c, n), (0, 0))[1] and not gate_ok(*r8[(c, n)])]
        if len(cols) >= 3 and len(bad) * 2 > len(cols):
            out[n] = bad
    return out


def gate(reports: list[dict]) -> bool:
    """The batch passes: every column's every scenario at two thirds."""
    return all(gate_ok(*v) for v in rates(reports).values())


def _one_alone(name: str, kind: str, model: str, out: Path, timeout_s: float, use_judge: bool,
               repeat: int, opts: dict) -> dict:
    """one() in a process of its own: the grader's build points prep.query at
    this scenario's events (server.serve_events), a process-wide seam two
    runs must not share."""
    col = clients.column(kind, model)
    return one(name, clients.make(kind, model, **spend_opts(opts, kind, col)), out, timeout_s, use_judge, repeat,
               col)


def spend_opts(opts: dict, kind: str, col: str) -> dict:
    """Each server model's runs count against a cap of their own: one
    spend file per column (--max-usd is per model batch)."""
    if kind != "server" or "out" not in opts:
        return {k: v for k, v in opts.items() if k != "out"}
    return {"max_usd": opts["max_usd"], "spend_file": opts["out"] / f"spend-{col.replace(':', '_')}.json"}


def spent_all(out: Path) -> float:
    return sum(clients.spent(p) for p in out.glob("spend-*.json"))


def units(names: list[str], cols: list[tuple[str, str]], repeats: int) -> list[tuple[str, str, str, int]]:
    """Every run of the batch: (kind, model, scenario, repeat)."""
    return [(k, m, n, i) for k, m in cols for n in names for i in range(1, repeats + 1)]


def run_all(names: list[str], cols: list[tuple[str, str]], out: Path, timeout_s: float, use_judge: bool,
            jobs: int, repeats: int = 1, opts: dict | None = None, server_jobs: int = SERVER_JOBS) -> list[dict]:
    """Every run, `jobs` at a time (the server client's `server_jobs` at
    most, all its models together: one provider's rate limit), each in its
    own process with its own server and client (a run mostly waits on the
    model; the local load is one small server on synthetic data). Reports
    in the order asked."""
    todo = units(names, cols, repeats)
    opts = opts or {}
    if jobs <= 1 or len(todo) <= 1:
        reports = []
        for k, m, n, i in todo:
            reports.append(_one_alone(n, k, m, out, timeout_s, use_judge, i, opts))
            (out / "summary.md").write_text(summary(reports), encoding="utf-8")
        return reports
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    ctx = multiprocessing.get_context("spawn")
    kinds = list(dict.fromkeys(k for k, _ in cols))
    pools = {k: ProcessPoolExecutor(max_workers=min(jobs, server_jobs) if k == "server" else jobs, mp_context=ctx)
             for k in kinds}
    try:
        futs = [pools[k].submit(_one_alone, n, k, m, out, timeout_s, use_judge, i, opts) for k, m, n, i in todo]
        return [f.result() for f in futs]
    finally:
        for p in pools.values():
            p.shutdown()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", action="append", choices=scenarios.names(), help="one scenario (repeatable); "
                    "default: all")
    ap.add_argument("--client", action="append", metavar="KIND[:MODEL]",
                    help="claude (default), openai, server: the server's own runner; :model names the model "
                    "(repeatable: one column each)")
    ap.add_argument("--model", default="", help="the client's model (default: the client's own)")
    ap.add_argument("--repeats", type=int, default=1, help="runs per scenario and client (the release: 3)")
    ap.add_argument("--jobs", type=int, default=0, help="runs at once (default: all of them)")
    ap.add_argument("--server-jobs", type=int, default=SERVER_JOBS,
                    help=f"server client runs at once, for its rate limit (default {SERVER_JOBS})")
    ap.add_argument("--max-usd", type=float, default=clients.MAX_USD,
                    help=f"each server model's spend cap for the batch, checked before each call "
                    f"(default {clients.MAX_USD:g})")
    ap.add_argument("--out", type=Path, default=ROOT / "eval-analysis-out")
    ap.add_argument("--timeout-min", type=float, default=clients.TIMEOUT_MIN,
                    help=f"per run (default {clients.TIMEOUT_MIN})")
    ap.add_argument("--judge", action="store_true", help="also ask a model about the wording (never the verdict)")
    ap.add_argument("--regrade", type=Path, metavar="RUN_DIR", help="grade a finished batch folder again with "
                    "today's rules (no client, no model)")
    a = ap.parse_args(argv)
    if a.regrade:
        reports = [regrade(p.parent) for p in sorted(a.regrade.rglob("report.json"))]
        table = release_table(reports)
        (a.regrade / "summary.md").write_text(summary(reports) + "\n" + table, encoding="utf-8")
        (a.regrade / "release.md").write_text(table, encoding="utf-8")
        print(summary(reports))
        print(table)
        return 0 if gate(reports) else 1
    cols = list(dict.fromkeys(clients.parse(c, a.model) for c in a.client or ["claude"]))
    names = a.scenario or scenarios.names()
    out = a.out / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True)
    opts = {"out": out, "max_usd": a.max_usd}
    for k, m in cols:  # a missing setting fails here, before anything starts
        clients.make(k, m, **spend_opts(opts, k, clients.column(k, m)))
    t0 = time.time()
    reports = run_all(names, cols, out, a.timeout_min * 60, a.judge,
                      a.jobs or len(units(names, cols, a.repeats)), a.repeats, opts, a.server_jobs)
    wall = (time.time() - t0) / 60
    spent = spent_all(out) if any(k == "server" for k, _ in cols) else None
    table = release_table(reports, wall, spent)
    (out / "summary.md").write_text(summary(reports) + "\n" + table, encoding="utf-8")
    (out / "release.md").write_text(table, encoding="utf-8")
    print(summary(reports))
    print(table)
    print(f"{out} (wall time {wall:.1f} min)")
    return 0 if gate(reports) else 1


if __name__ == "__main__":
    sys.exit(main())
