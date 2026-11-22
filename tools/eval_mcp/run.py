"""The MCP check: ten questions about the demo character, each with one
short answer, put to Claude Haiku through the wow MCP tools alone. If Haiku
finds the answer, the tools are clear enough. Graded by exact or normalized
match (grading.py), never by a model. Light and free on a Claude plan; the
seeded analysis evaluation (tools/eval_analysis) is the heavy one.

    uv run --extra mcp python tools/eval_mcp/run.py [--only ID ...] [--model haiku] [--timeout 180]
    make eval-mcp

Starts the demo's store once (tests/degraded.py VL, the docs site's demo
character), then one `claude -p` per question, all at once, each with only
this checkout's MCP server (--strict-mcp-config, no built-in tools), its own
state folder and the demo's frozen clock (server.py). The expected answers
are computed from the same data first (answers.py).

Out: a table of question, expected, got, pass, tools used and seconds, on
stdout and in eval-mcp-out/<batch>/summary.md (ignored by git; the data is
synthetic), plus each question's transcript. A question answered through
play_sql alone is flagged: the answer has no typed path.

Not in CI and not a release gate: it needs Claude Code and a login.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
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

The answer is one short value (a name, a number, Yes or No). Read-only: never ask the player anything. \
End your reply with one last line in exactly this form, with the value alone after the colon:
ANSWER: <value>"""


def load_questions(path: Path = HERE / "questions.yaml") -> list[dict]:
    qs = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = [q["id"] for q in qs]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"{path}: duplicate ids")
    for q in qs:
        missing = {"id", "question", "answer", "match"} - set(q)
        if missing:
            raise SystemExit(f"{path}: {q.get('id')} lacks {sorted(missing)}")
    return qs


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


def ask(q: dict, expected, workdir: Path, vl_url: str, now: str, model: str, timeout_s: int) -> dict:
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
    seconds = round(time.time() - t0, 1)
    (workdir / "transcript.jsonl").write_text(stream or "", encoding="utf-8")
    calls, result, meta = grading.tools_used(stream, SERVER)
    if meta.get("connected") is False:
        error = error or "the MCP server did not connect"
    got = grading.final_answer(result)
    ok = grading.grade(got, expected, q["match"], q.get("tolerance", 0))
    return {"id": q["id"], "area": q.get("area", ""), "question": q["question"].strip(), "expected": expected,
            "got": got, "pass": ok, "tools": calls, "sql_only": grading.sql_only(calls),
            "seconds": seconds, "error": error, "cost_usd": meta.get("cost_usd")}


def cell(v) -> str:
    return str(v if v is not None else "-").replace("|", "\\|").replace("\n", " ")


def table(results: list[dict]) -> str:
    rows = ["| question | expected | got | pass | tools used | s |", "|---|---|---|---|---|---|"]
    for r in results:
        tools = ", ".join(dict.fromkeys(r["tools"])) or "none"
        if r["sql_only"]:
            tools += " **(play_sql only)**"
        got = r["got"] if r["got"] is not None else (r["error"] or "no ANSWER line")
        rows.append(f"| {cell(r['id'])} | {cell(r['expected'])} | {cell(got)} | {'yes' if r['pass'] else 'NO'} "
                    f"| {cell(tools)} ({len(r['tools'])}) | {r['seconds']:.0f} |")
    return "\n".join(rows)


def summary(results: list[dict], model: str, wall_s: float) -> str:
    n = sum(r["pass"] for r in results)
    sql = [r["id"] for r in results if r["sql_only"]]
    any_sql = [r["id"] for r in results if "play_sql" in r["tools"]]
    return "\n".join([
        f"# MCP check: {n}/{len(results)} with {model}", "",
        f"Wall time {wall_s:.0f} s. play_sql only: {', '.join(sql) or 'none'}. "
        f"Used play_sql at all: {', '.join(any_sql) or 'none'}.", "", table(results), ""])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", action="append", default=[], help="one question id (repeatable)")
    ap.add_argument("--model", default=os.environ.get("EVAL_MCP_MODEL", "haiku"))
    ap.add_argument("--timeout", type=int, default=180, help="seconds per question (default 180)")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if not shutil.which("claude"):
        raise SystemExit("claude not found: install Claude Code")
    qs = [q for q in load_questions() if not a.only or q["id"] in a.only]
    if not qs:
        raise SystemExit(f"no question matches {a.only}")

    expected = answers.compute()
    rows = answers.D.rows_for("full")
    vl = answers.D.VL(rows)
    now = answers.D.now().isoformat()
    batch = a.out or OUT / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    batch.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=len(qs)) as pool:
            futs = [pool.submit(ask, q, expected[q["answer"]], batch / q["id"], vl.url, now, a.model, a.timeout)
                    for q in qs]
            results = [f.result() for f in futs]
    finally:
        vl.close()
    wall = time.time() - t0
    text = summary(results, a.model, wall)
    (batch / "summary.md").write_text(text, encoding="utf-8")
    (batch / "results.json").write_text(json.dumps({"model": a.model, "wall_s": round(wall, 1),
                                                    "results": results}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(text)
    print(f"(transcripts in {batch})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
