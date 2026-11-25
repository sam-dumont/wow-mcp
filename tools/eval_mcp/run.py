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
    uv run --extra mcp python tools/eval_mcp/run.py --skills [--only ID ...]   # with and without skills
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

`--skills` measures the server's skills (wowstats/mcp/skills): each question
runs twice on the same tools, the wow server plus Claude Code's Skill,
WebSearch and WebFetch (web tools offered but never allowed: a call is
recorded and refused), once with no skill and once with the server's skills.
Claude Code loads MCP-served skills only in an interactive session, never in
`claude -p` (skill_mirror.py says how that was checked), so the skills are fetched
from the server over MCP (skills/list and resources/read, digests checked)
and installed as project skills in the question's folder: "skills via
filesystem mirror, not MCP delivery". Per question: the skills loaded, the
tools called, any web search or fetch, any play_sql where the route names a
typed tool.

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
import skill_mirror  # noqa: E402
import yaml  # noqa: E402

SERVER = "wow"
# --skills: Claude Code's Skill tool, and the web tools the skills should
# make unnecessary (offered, so a reach for them shows; never allowed).
SKILL_TOOLS = "Skill,WebSearch,WebFetch"
OUT = ROOT / "eval-mcp-out"
PROMPT = """You have MCP tools about one World of Warcraft character (the server's default character: \
you can call the tools without naming it). Answer this question about that character from the tools' data, \
not from general game knowledge:

{question}
{clock}
The answer is one short value (a name, a number, Yes or No). When the tools show they don't have it (the \
data is missing, or the character or item is unknown), the value is NONE: never guess. Read-only: never ask \
the player anything. End your reply with one last line in exactly this form, with the value alone after the colon:
ANSWER: <value>"""


def load_questions(path: Path = HERE / "questions.yaml") -> list[dict]:
    return answers.load_questions(path)


def load_coverage(path: Path = HERE / "coverage.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def command(q: dict, mcp_config: Path, model: str, binary: str = "claude", now: str = "",
            skills: bool = False) -> list[str]:
    # The tools run on the demo's frozen clock (server.py), the model on the
    # wall clock: without the tools' date it read "now" as days after the
    # data and answered NONE (zone_now, 2026-11-24, six days after the demo).
    clock = (f"\nThe tools' clock reads {now}: 'now' and 'today' mean that moment, whatever date you were "
             "given elsewhere.\n") if now else ""
    return [binary, "-p", PROMPT.format(question=q["question"].strip(), clock=clock), "--model", model,
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(mcp_config), "--strict-mcp-config", "--tools", SKILL_TOOLS if skills else "",
            "--allowedTools", f"mcp__{SERVER}" + (" Skill" if skills else ""), "--permission-prompts", "none",
            # --disable-slash-commands turns skills off too: kept out of --skills.
            "--setting-sources", "project", *([] if skills else ["--disable-slash-commands"]),
            "--no-session-persistence"]


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
            "skills": grading.skills_loaded(stream), "web": grading.web_calls(calls),
            "sql_typed": grading.sql_despite_typed(calls, q.get("tools")),
            "error": error, "cost_usd": meta.get("cost_usd")}


def ask(q: dict, want: dict, workdir: Path, vl_url: str, now: str, model: str, timeout_s: int,
        skills: bool = False, mirror: Path | None = None) -> dict:
    """One question. skills: the --skills tools; mirror: a folder whose
    .claude/skills becomes this run's project skills."""
    workdir.mkdir(parents=True, exist_ok=True)
    if mirror is not None:
        shutil.copytree(mirror / ".claude", workdir / ".claude", dirs_exist_ok=True)
    settings = workdir / "settings.json"
    settings.write_text(json.dumps({**answers.server_env(vl_url, str(workdir / "state")), "EVAL_NOW": now}),
                        encoding="utf-8")
    cfg = workdir / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {SERVER: {
        "command": sys.executable, "args": [str(HERE / "server.py"), str(settings)]}}}), encoding="utf-8")
    t0 = time.time()
    error = ""
    try:
        p = subprocess.run(command(q, cfg, model, now=now, skills=skills), cwd=workdir, env=client_env(),
                           capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
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


def flags(r: dict) -> str:
    out = []
    if r.get("web"):
        out.append("WEB " + ",".join(dict.fromkeys(r["web"])))
    if r.get("sql_typed"):
        out.append("play_sql")
    return "; ".join(out) or "-"


def compare(without: list[dict], with_: list[dict], model: str, wall_s: float, loaded: list[str],
            skipped: list[str] = ()) -> str:
    """Markdown: each question without and with the skills."""
    w = {r["id"]: r for r in with_}
    both = [(r, w[r["id"]]) for r in without if r["id"] in w]

    def tally(rs: list[dict]) -> str:
        cost = sum(float(r.get("cost_usd") or 0) for r in rs)
        return (f"{sum(r['pass'] for r in rs)}/{len(rs)} pass, "
                f"a skill loaded in {sum(bool(r.get('skills')) for r in rs)}, "
                f"web in {sum(bool(r.get('web')) for r in rs)}, play_sql with a typed tool in "
                f"{sum(bool(r.get('sql_typed')) for r in rs)}, ${cost:.2f}")

    rows = ["| question | without: pass | without: flags | with: pass | with: skill loaded | with: flags "
            "| with: tools |",
            "|---|---|---|---|---|---|---|"]
    for a, b in both:
        tools = ", ".join(dict.fromkeys(t for t in b["tools"] if t != "Skill")) or "none"
        rows.append(f"| {cell(a['id'])} | {'yes' if a['pass'] else 'NO'} | {cell(flags(a))} | "
                    f"{'yes' if b['pass'] else 'NO'} | {cell(', '.join(b.get('skills') or []) or '-')} | "
                    f"{cell(flags(b))} | {cell(tools)} |")
    lines = [f"# MCP check with and without skills, {model}", "",
             f"Mode: {skill_mirror.MODE}. Skills fetched from the server over MCP: {', '.join(loaded)}.", "",
             f"Without: {tally([a for a, _ in both])}.", f"With: {tally([b for _, b in both])}.",
             f"Wall time {wall_s:.0f} s."]
    if skipped:
        lines += ["", f"Not run (cost cap reached): {', '.join(skipped)}."]
    return "\n".join(lines + ["", "\n".join(rows), ""])


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


def fetch_skills(dest: Path, vl_url: str, now: str) -> list[str]:
    """The skills of the server the model gets (server.py over stdio), as
    project skills under dest/.claude/skills."""
    from fastmcp.client.transports import StdioTransport

    dest.mkdir(parents=True, exist_ok=True)
    settings = dest / "settings.json"
    settings.write_text(json.dumps({**answers.server_env(vl_url, str(dest / "state")), "EVAL_NOW": now}),
                        encoding="utf-8")
    return skill_mirror.mirror(StdioTransport(command=sys.executable, args=[str(HERE / "server.py"), str(settings)]),
                              dest)


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
    ap.add_argument("--skills", action="store_true",
                    help="each question without and with the server's skills (filesystem mirror)")
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
    stores = {s: answers.store(answers.rows_for(s)) for s in answers.scenarios(qs)}
    now = answers.D.now().isoformat()
    batch = a.out or OUT / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    batch.mkdir(parents=True, exist_ok=True)
    spent, skipped, lock = [0.0], [], threading.Lock()
    mirror, loaded = None, []
    if a.skills:
        mirror = batch / "skills-mirror"
        loaded = fetch_skills(mirror, next(iter(stores.values())).url, now)
    runs = [(q, arm) for arm in ((None, mirror) if a.skills else (None,)) for q in qs]

    def one(job: tuple[dict, Path | None]) -> dict | None:
        q, arm = job
        with lock:
            if spent[0] >= a.max_usd:
                skipped.append(q["id"] + ("" if not a.skills else " (with)" if arm else " (without)"))
                return None
        where = batch / ("with" if arm else "without") / q["id"] if a.skills else batch / q["id"]
        r = ask(q, expected[q["id"]], where, stores[q.get("scenario", "full")].url, now, a.model, a.timeout,
                skills=a.skills, mirror=arm)
        with lock:
            spent[0] += float(r.get("cost_usd") or 0)
        return {**r, "arm": "with" if arm else "without"} if a.skills else r

    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
            results = [r for r in pool.map(one, runs) if r is not None]
    finally:
        for vl in stores.values():
            vl.close()
    wall = time.time() - t0
    if a.skills:
        text = compare([r for r in results if r["arm"] == "without"], [r for r in results if r["arm"] == "with"],
                       a.model, wall, loaded, skipped)
        (batch / "summary.md").write_text(text, encoding="utf-8")
        (batch / "results.json").write_text(json.dumps({"model": a.model, "mode": skill_mirror.MODE, "skills": loaded,
                                                        "wall_s": round(wall, 1), "skipped": skipped,
                                                        "results": results}, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
        print(text)
        print(f"(transcripts in {batch})")
        return 0
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
