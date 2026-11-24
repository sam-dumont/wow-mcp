"""The MCP check: questions about the demo character (questions.yaml, about
seventy, grouped by what they test), each with one short answer, put to
Claude Haiku through the wow MCP tools alone. If Haiku finds the answer by
the expected tools and says nothing it mustn't, the tools are clear enough.
Graded by exact or normalized match (grading.py), never by a model. Light
and free on a Claude plan; the seeded analysis evaluation (tools/eval_analysis)
is the heavy one.

    uv run --extra mcp python tools/eval_mcp/run.py [--only ID ...] [--group G ...]
        [--model haiku] [--jobs 6] [--max-usd 2] [--timeout 180]
    uv run --extra mcp python tools/eval_mcp/run.py --coverage   # the coverage matrix, no model
    make eval-mcp

Starts one store per scenario the questions use (tests/degraded.py VL: the
docs site's demo character, with sources taken away for the degraded ones,
plus answers.py's injected player), then one `claude -p` per question,
`--jobs` at a time, each with only this checkout's MCP server
(--strict-mcp-config, no built-in tools), its own state folder and the
demo's frozen clock (server.py). The expected answers are computed from the
same data first (answers.py). `--max-usd` stops starting questions once the
runs so far cost that much (Claude Code's own figure; on a Claude plan it is
what the API would have charged, not a bill).

A question passes when its ANSWER line grades right, the run called one tool
of each entry of its route (`tools`), and the reply says nothing in
`forbid` / `forbid_answers`. Out: a table per question and a score per group,
on stdout and in eval-mcp-out/<batch>/summary.md (ignored by git; the data
is synthetic), plus each question's transcript. A question answered through
play_sql alone is flagged: the answer has no typed path.

Not in CI and not a release gate: it needs Claude Code and a login. Its
deterministic half (the probes, the coverage) runs in CI as
tests/test_eval_mcp.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import answers  # noqa: E402
import grading  # noqa: E402
import yaml  # noqa: E402

SERVER = "wow"
OUT = ROOT / "eval-mcp-out"
PROMPT = """You have MCP tools about one World of Warcraft character (the server's default character: \
you can call the tools without naming it). Answer this question about that character from the tools' data, \
not from general game knowledge:

{question}

The answer is one short value (a name, a number, Yes or No). When the tools show they don't have it (the \
data is missing, or the character or item is unknown), the value is NONE: never guess. Read-only: never ask \
the player anything. End your reply with one last line in exactly this form, with the value alone after the colon:
ANSWER: <value>"""


def load_questions(path: Path = HERE / "questions.yaml") -> list[dict]:
    return answers.load_questions(path)


def load_coverage(path: Path = HERE / "coverage.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def command(q: dict, mcp_config: Path, model: str, binary: str = "claude") -> list[str]:
    return [binary, "-p", PROMPT.format(question=q["question"].strip()), "--model", model,
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(mcp_config), "--strict-mcp-config", "--tools", "",
            "--allowedTools", f"mcp__{SERVER}", "--permission-prompts", "none",
            "--setting-sources", "project", "--disable-slash-commands", "--no-session-persistence"]


def client_env() -> dict:
    """Claude Code on the user's own login: a key in the environment would
    bill it instead."""
    e = dict(os.environ)
    e.pop("ANTHROPIC_API_KEY", None)
    return e


def judge(q: dict, want: dict, stream: str, error: str = "") -> dict:
    """One run's verdict from its transcript: answer, route, forbidden."""
    calls, result, meta = grading.tools_used(stream, SERVER)
    if meta.get("connected") is False:
        error = error or "the MCP server did not connect"
    got = grading.final_answer(result)
    answer_ok = grading.grade(got, want["answer"], q["match"], q.get("tolerance", 0), q.get("also") or ())
    missed = grading.routed(calls, q.get("tools"))
    bad = grading.forbidden(result, got, q, want.get("forbid_answers"))
    return {"id": q["id"], "group": q.get("group", ""), "difficulty": q.get("difficulty", ""),
            "question": q["question"].strip(), "expected": want["answer"], "got": got,
            "answer_ok": answer_ok, "route_missed": missed, "forbidden": bad,
            "pass": answer_ok and not missed and not bad, "tools": calls, "sql_only": grading.sql_only(calls),
            "error": error, "cost_usd": meta.get("cost_usd")}


def ask(q: dict, want: dict, workdir: Path, vl_url: str, now: str, model: str, timeout_s: int) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    settings = workdir / "settings.json"
    settings.write_text(json.dumps({**answers.server_env(vl_url, str(workdir / "state")), "EVAL_NOW": now}),
                        encoding="utf-8")
    cfg = workdir / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {SERVER: {
        "command": sys.executable, "args": [str(HERE / "server.py"), str(settings)]}}}), encoding="utf-8")
    t0 = time.time()
    error = ""
    try:
        p = subprocess.run(command(q, cfg, model), cwd=workdir, env=client_env(), capture_output=True, text=True,
                           timeout=timeout_s, stdin=subprocess.DEVNULL)
        stream = p.stdout
        if p.returncode != 0 and not stream.strip():
            error = f"claude exited {p.returncode}: {p.stderr.strip()[-300:]}"
    except subprocess.TimeoutExpired as e:
        stream = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
        error = f"timed out after {timeout_s} s"
    (workdir / "transcript.jsonl").write_text(stream or "", encoding="utf-8")
    return {**judge(q, want, stream or "", error), "seconds": round(time.time() - t0, 1)}


def cell(v) -> str:
    return str(v if v is not None else "-").replace("|", "\\|").replace("\n", " ")


def table(results: list[dict]) -> str:
    rows = ["| question | group | expected | got | pass | tools used | s |", "|---|---|---|---|---|---|---|"]
    for r in results:
        tools = ", ".join(dict.fromkeys(r["tools"])) or "none"
        if r["sql_only"]:
            tools += " **(play_sql only)**"
        got = r["got"] if r["got"] is not None else (r["error"] or "no ANSWER line")
        why = []
        if not r.get("answer_ok", r["pass"]):
            why.append("wrong answer")
        if r.get("route_missed"):
            why.append("never called " + " / ".join(r["route_missed"]))
        why += r.get("forbidden") or []
        verdict = "yes" if r["pass"] else "NO" + (f" ({'; '.join(why)})" if why else "")
        rows.append(f"| {cell(r['id'])} | {cell(r.get('group'))} | {cell(r['expected'])} | {cell(got)} | "
                    f"{cell(verdict)} | {cell(tools)} ({len(r['tools'])}) | {r['seconds']:.0f} |")
    return "\n".join(rows)


def by_group(results: list[dict]) -> str:
    n, ok = defaultdict(int), defaultdict(int)
    for r in results:
        n[r.get("group") or "-"] += 1
        ok[r.get("group") or "-"] += bool(r["pass"])
    return ", ".join(f"{g} {ok[g]}/{n[g]}" for g in sorted(n))


def summary(results: list[dict], model: str, wall_s: float, skipped: list[str] = ()) -> str:
    n = sum(r["pass"] for r in results)
    sql = [r["id"] for r in results if r["sql_only"]]
    any_sql = [r["id"] for r in results if "play_sql" in r["tools"]]
    cost = sum(float(r.get("cost_usd") or 0) for r in results)
    lines = [f"# MCP check: {n}/{len(results)} with {model}", "",
             f"Wall time {wall_s:.0f} s, ${cost:.2f} as Claude Code counts it. By group: {by_group(results)}.", "",
             f"play_sql only: {', '.join(sql) or 'none'}. Used play_sql at all: {', '.join(any_sql) or 'none'}."]
    if skipped:
        lines += ["", f"Not run (cost cap reached): {', '.join(skipped)}."]
    return "\n".join(lines + ["", table(results), ""])


# ---------------------------------------------------------------- coverage (no model)


def probe_modes(q: dict) -> set[str]:
    """What a question's probes hit, in coverage.yaml's words."""
    out = {f"group:{q.get('group')}", f"scenario:{q.get('scenario', 'full')}"}
    for p in q.get("probes") or []:
        out.add(p["tool"])
        if p.get("error"):
            out.add("error")
        for k, v in (p.get("args") or {}).items():
            out.add(f"{p['tool']}:{k}")
            if isinstance(v, (str, int, float, bool)):
                out.add(f"{p['tool']}:{k}={v}")
    return out


def coverage_matrix(questions: list[dict], tool_names: list[str], uncovered: dict) -> str:
    """Markdown: each MCP tool, the questions that probe it, the ones that
    route a model to it."""
    probed, routed = defaultdict(list), defaultdict(list)
    for q in questions:
        for t in dict.fromkeys(p["tool"] for p in q.get("probes") or []):
            probed[t].append(q["id"])
        for t in dict.fromkeys(x for r in q.get("tools") or [] for x in r.split("|")):
            routed[t].append(q["id"])
    rows = ["| tool | probed by | a model may answer with it | |", "|---|---|---|---|"]
    for t in tool_names:
        note = f"not evaluated: {uncovered[t].split('. ')[0].rstrip('.')}" if t in uncovered else ""
        rows.append(f"| {t} | {len(probed[t])} | {len(routed[t])} | {note} |")
    return "\n".join(rows)


def tool_names() -> list[str]:
    from wowstats.mcp import tools

    return [t.name for t in tools.TOOLS + (tools.MARKET,)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", action="append", default=[], help="one question id (repeatable)")
    ap.add_argument("--group", action="append", default=[], help="one group (repeatable)")
    ap.add_argument("--model", default=os.environ.get("EVAL_MCP_MODEL", "haiku"))
    ap.add_argument("--jobs", type=int, default=6, help="questions at once (default 6)")
    ap.add_argument("--max-usd", type=float, default=float(os.environ.get("EVAL_MCP_MAX_USD", "2")),
                    help="stop starting questions past this cost (default 2)")
    ap.add_argument("--timeout", type=int, default=180, help="seconds per question (default 180)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--coverage", action="store_true", help="print the coverage matrix and stop (no model)")
    a = ap.parse_args(argv)
    qs = [q for q in load_questions() if (not a.only or q["id"] in a.only) and (not a.group or q["group"] in a.group)]
    if a.coverage:
        print(coverage_matrix(qs, tool_names(), load_coverage()["uncovered"]))
        return 0
    if not shutil.which("claude"):
        raise SystemExit("claude not found: install Claude Code")
    if not qs:
        raise SystemExit(f"no question matches {a.only or a.group}")

    expected = answers.compute(qs)
    stores = {s: answers.D.VL(answers.rows_for(s)) for s in answers.scenarios(qs)}
    now = answers.D.now().isoformat()
    batch = a.out or OUT / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    batch.mkdir(parents=True, exist_ok=True)
    spent, skipped, lock = [0.0], [], threading.Lock()

    def one(q: dict) -> dict | None:
        with lock:
            if spent[0] >= a.max_usd:
                skipped.append(q["id"])
                return None
        r = ask(q, expected[q["id"]], batch / q["id"], stores[q.get("scenario", "full")].url, now, a.model,
                a.timeout)
        with lock:
            spent[0] += float(r.get("cost_usd") or 0)
        return r

    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
            results = [r for r in pool.map(one, qs) if r is not None]
    finally:
        for vl in stores.values():
            vl.close()
    wall = time.time() - t0
    text = summary(results, a.model, wall, skipped)
    (batch / "summary.md").write_text(text, encoding="utf-8")
    (batch / "results.json").write_text(json.dumps({"model": a.model, "wall_s": round(wall, 1), "skipped": skipped,
                                                    "results": results}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(text)
    print(f"(transcripts in {batch})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
