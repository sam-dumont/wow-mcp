"""The seeded evaluation of the play analysis: run one scenario, or all, with
a real MCP client, and grade what it kept.

    uv run --extra mcp --extra analysis-openai python tools/eval_analysis/run.py \\
        [--scenario NAME ...] [--client claude|openai] [--model M] [--out DIR] [--judge]
    make eval-analysis                      # every scenario, Claude Code
    make eval-analysis SCENARIO=death_spike

Per scenario: build its events (scenarios.py), start a throwaway server on
them (server.py, its own state folder), let the client run the whole
analysis through MCP (clients.py), then re-run every kept finding's query on
a fresh build and grade the kept lines against the truth (grade.py). One
scenario at a time: each run is a full analysis, tens of minutes.

Where it goes: --out, else `eval-analysis-out/` in this checkout (ignored by
git; the data is synthetic). Per scenario: events.jsonl, the server's
settings and state, transcript.jsonl (what the client did), analysis.json
(what it kept), report.json and report.md. summary.md covers the batch.

Not in CI: a run needs a model client, and hosted CI has none.
"""

from __future__ import annotations

import argparse
import json
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


def one(name: str, client, out: Path, timeout_s: float, use_judge: bool) -> dict:
    sc = scenarios.load(name)
    d = out / name
    if d.exists():
        shutil.rmtree(d)
    (d / "work").mkdir(parents=True)
    state = d / "state"
    state.mkdir()
    rows = scenarios.build(sc)
    scenarios.write_events(rows, d / "events.jsonl")
    settings = server_settings(sc, state)
    (d / "settings.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
    facts = scenarios.facts(sc, rows)
    print(f"[{name}] {len(rows)} events; running {client.name}", flush=True)

    def is_done() -> bool:
        run, _ = run_state(state)
        return bool(run) and run.get("pass") == "done"

    cmd = [sys.executable, str(HERE / "server.py"), str(d / "settings.json"), str(d / "events.jsonl")]
    job = clients.Job(char=scenarios.CHAR, goals=sc["goals"], server=cmd,
                      workdir=d / "work", transcript=d / "transcript.jsonl", is_done=is_done, timeout_s=timeout_s)
    res = client.run(job).as_dict()
    run, analysis = run_state(state)
    reruns = None
    if res["done"] and analysis:
        w = workspace(rows, settings)
        try:
            reruns = grade.rerun(analysis, w.judge)
        finally:
            w.worker.close()
    verdict = grade.grade(sc, analysis if res["done"] else None, res, reruns)
    report = {"scenario": name, "about": sc["about"], "expected": sc["truth"]["expected"], **verdict,
              "client": res, "facts": facts, "at": datetime.now(UTC).isoformat(timespec="seconds"),
              "run": (run or {}).get("result"),
              "kept": [ln.__dict__ for ln in grade.lines(analysis)] if analysis and res["done"] else []}
    if use_judge and analysis and res["done"]:
        import judge

        report["judge"] = judge.judge(sc, analysis)
    (d / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (d / "report.md").write_text(markdown(report), encoding="utf-8")
    print(f"[{name}] {verdict['verdict']}", flush=True)
    return report


def _tokens(c: dict) -> str:
    t = c.get("tokens") or {}
    if not t:
        return "unknown"
    parts = [f"{k} {int(v):,}" for k, v in t.items() if isinstance(v, (int, float)) and v]
    return ", ".join(parts) or "unknown"


def markdown(r: dict) -> str:
    c = r["client"]
    out = [f"# {r['scenario']}: {r['verdict']}", "", r["about"], "", f"**Expected:** {r['expected']}", "",
           f"**Client:** {c['client']} ({c.get('model') or 'model unknown'}), {c['rounds']} conversation(s), "
           f"{c['calls']} tool calls, {c['seconds'] / 60:.0f} min. **Tokens:** {_tokens(c)}."
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
    out = ["| Scenario | Verdict | Failed checks | Kept lines | Client | Minutes | Tokens |",
           "|---|---|---|---|---|---|---|"]
    for r in reports:
        c = r["client"]
        bad = ", ".join(ch["check"] for ch in r["checks"] if not ch["ok"]) or "none"
        out.append(f"| {r['scenario']} | {r['verdict']} | {bad} | {len(r['kept'])} | {c['client']} "
                   f"({c.get('model') or '?'}) | {c['seconds'] / 60:.0f} | {_tokens(c)} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", action="append", choices=scenarios.names(), help="one scenario (repeatable); "
                    "default: all")
    ap.add_argument("--client", default="claude", choices=("claude", "openai"))
    ap.add_argument("--model", default="", help="the client's model (default: the client's own)")
    ap.add_argument("--out", type=Path, default=ROOT / "eval-analysis-out")
    ap.add_argument("--timeout-min", type=float, default=90.0, help="per scenario (default 90)")
    ap.add_argument("--judge", action="store_true", help="also ask a model about the wording (never the verdict)")
    a = ap.parse_args(argv)
    client = clients.make(a.client, a.model)
    names = a.scenario or scenarios.names()
    out = a.out / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True)
    reports = []
    t0 = time.time()
    for n in names:
        reports.append(one(n, client, out, a.timeout_min * 60, a.judge))
        (out / "summary.md").write_text(summary(reports), encoding="utf-8")
    print(summary(reports))
    print(f"{out} ({(time.time() - t0) / 60:.0f} min)")
    return 0 if all(r["verdict"] == "pass" for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
