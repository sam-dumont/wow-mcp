"""The refresh job's gates: what a KB change must pass to merge on its own
(owner, 2026-11-20: "the goal is automated refreshes"; docs/kb.md, "The
gates").

    uv run python tools/kb/gates.py --base origin/main                # every gate, the test suite too
    uv run python tools/kb/gates.py --base origin/main --no-tests     # the KB gates only (seconds)

Compares the working tree with `--base` (a git ref: the main the job
started from) and checks:

- schema: every research file, rules.json and news.json against
  wowstats.analysis.kbschema;
- licence: every text that is new (no record of the base has it) is checked
  against the text the model pass read for its source (KB_SOURCES, written
  by tools/kb/web.remember): 12 words in a row, or a fifth of its 8-word
  phrases, is a copy (wowstats.analysis.verbatim); a new text with no source
  read in this run can't be checked and is refused; a quote over 15 words is
  refused; every record cites its source (the schema);
- multi-source: a new or changed list item or build has two sources, or
  `single_source` at low confidence, or an official page;
- instructions: no text in the KB reads like an instruction aimed at an
  assistant ("ignore previous instructions", "you must now", a role or tool
  call marker, a prompt delimiter), and no invisible character
  (wowstats.text.invisible: Tags block, bidi controls, zero-width ones) that
  could hide one. The MCP hands KB text to the players'
  assistants, so model-written text that merges on its own is a stored
  prompt-injection path (security review 2026-11-20, finding 7); the MCP also
  labels it as data (wowstats.mcp.tools KB_TEXT_TOOLS);
- size: data/kb/budget.json (tools/kb/lifecycle.over_budget);
- churn: statements the loader would no longer serve, against the base: at
  most CHURN_MAX in all and CHURN_SHARE of any lane (never a whole lane), so
  a broken fetch or a bad model answer can't wipe a lane;
- tests (unless --no-tests): ruff, the whole pytest suite (the MCP's
  24,000-character cap, tests/mcp/test_kb_caps.py, among them) and the
  fixtures check, as CI's test job runs them.

Writes {"ok", "refused": [reasons], "counts"} to --out and prints it; exits
1 when a gate refuses. It reads the tree and runs the tests; it holds no
credential (the workflow gives this step no token).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lifecycle  # noqa: E402, I001 (both tools/kb, beside this file)
import web  # noqa: E402

from wowstats.analysis import kbschema, verbatim  # noqa: E402
from wowstats.text import invisible  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
KB = ROOT / "data" / "kb"
CHURN_MAX = int(os.environ.get("KB_CHURN_MAX") or 30)
CHURN_SHARE = 0.10
CHURN_FLOOR = 5  # a small lane may always lose this many
TEXT_KEYS = ("statements", "contradictions", "builds")


def git_show(base: str, rel: str) -> str | None:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{base}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def base_files(base: str) -> dict[str, dict]:
    """{data/kb relative path: parsed JSON} of the research files, rules.json and news.json at `base`."""
    r = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", base, "data/kb"],
                       capture_output=True, text=True, check=True)
    out = {}
    for rel in r.stdout.split():
        if rel.endswith(".json") and (rel.startswith("data/kb/research/") or rel in ("data/kb/rules.json",
                                                                                   "data/kb/news.json")):
            text = git_show(base, rel)
            if text is not None:
                out[rel[len("data/kb/"):]] = json.loads(text)
    return out


def head_files(kb_dir: Path = KB) -> dict[str, dict]:
    out = {f"research/{p.name}": json.loads(p.read_text(encoding="utf-8"))
           for p in sorted((kb_dir / "research").glob("*.json"))}
    for name in ("rules.json", "news.json"):
        if (kb_dir / name).exists():
            out[name] = json.loads((kb_dir / name).read_text(encoding="utf-8"))
    return out


def records(files: dict[str, dict]):
    """(file, list key, record) for every text-carrying record: statements,
    contradictions, builds, a class's paths, and digest lines."""
    for name, doc in files.items():
        if not isinstance(doc, dict):
            continue
        for key in TEXT_KEYS:
            for r in doc.get(key) or []:
                if isinstance(r, dict):
                    yield name, key, r
        paths = doc.get("paths")
        for r in (paths.values() if isinstance(paths, dict) else paths or []):
            if isinstance(r, dict):
                yield name, "paths", r
        weeks = [doc.get("digest")] + list(doc.get("digests") or [])
        for w in weeks:
            for it in (w or {}).get("items") or [] if isinstance(w, dict) else []:
                if isinstance(it, dict):
                    yield name, "digest", it


def text_of(r: dict) -> str:
    return " ".join(str(r.get(k) or "") for k in ("title", "text", "note")).strip()


def schema_gate(files: dict[str, dict], allow: dict) -> list[str]:
    out = []
    for name, doc in sorted(files.items()):
        out += [f"schema: `{name}`: {e}" for e in kbschema.check_file(doc, allow, strict_type=name != "rules.json")]
    return out


def licence_of(t: str, r: dict, sources: dict[str, str]) -> list[str]:
    """Why a new text breaks the licence rules: a quote over 15 words, no
    source read in this run to check it against, or a copy of one."""
    out = []
    long_quotes = [q for q in verbatim.quoted_runs(t) if len(verbatim.words(q)) > verbatim.QUOTE_MAX]
    if long_quotes:
        out.append(f"quotes {len(verbatim.words(long_quotes[0]))} words (15 at most)")
    read = [sources[u] for u in kbschema.urls(r) if u in sources]
    if not read:
        return out + ["new wording with no source read in this run to check it against"]
    why = next((w for w in (verbatim.copied(t, s) for s in read) if w), "")
    return out + ([why] if why else [])


def licence_gate(base: dict[str, dict], head: dict[str, dict], sources: dict[str, str]) -> tuple[list[str], int]:
    """Refusals, and how many new texts were checked."""
    known = {text_of(r) for _, _, r in records(base)}
    out, n = [], 0
    for name, _key, r in records(head):
        t = text_of(r)
        if not t or t in known:
            continue
        n += 1
        rid = r.get("id") or r.get("spec") or "?"
        out += [f"licence: `{name}` {rid}: {w}" for w in licence_of(t, r, sources)]
    return out, n


# Instruction-shaped text aimed at an assistant, never game facts: each is
# something a page or a model answer would say only to steer the reader's
# model. Kept narrow on purpose (tests/test_kb_gates.py runs the whole KB
# through it): "you must" alone is game advice, "you must now ignore" isn't.
INSTRUCTION_PATTERNS = [re.compile(p, re.I) for p in (
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|any|your|the)\b.{0,20}"
    r"\b(instructions?|prompts?|messages?|rules|guidelines|context)\b",
    r"\byou (must|should|will|are to) now\b",
    r"\b(from now on|henceforth),? (you|the assistant)\b",
    r"\byou are (now )?(an?|the) (\w+ ){0,2}(ai|assistant|language model|llm|chatbot)\b",
    r"\b(new|updated|real|hidden|secret) (system )?(instructions?|prompt)\b",
    r"\bsystem prompt\b",
    r"\b(do not|don't|never) (tell|inform|show|mention (this|it) to) the (user|player)\b",
    r"\b(as|to) (an? )?(ai|assistant|language model)\b.{0,30}\b(must|should|need)\b",
    r"\b(call|use|invoke|run|execute) the (\w+ )?(tool|function|command)\b",
    # A chat role at the start of a line ("Human" is a race here: left out).
    r"(?m)^\s*(assistant|system|user)\s*:",
    r"</?\s*(system|assistant|user|human|instructions?|tool_call|tool_use|tool_result|function_calls?|invoke|"
    r"parameter|antml:[a-z_]+)\b[^>]*>",
    r"<\|[a-z_]+\|>|\[/?INST\]|<<\s*/?SYS\s*>>|^\s*#{2,}\s*(system|instructions?|assistant)\b",
    r"\{\s*\"(tool|name|function)\"\s*:\s*\"[^\"]+\"\s*,\s*\"(arguments|input|parameters)\"",
)]


def strings(v, path: str = ""):
    """(path, text) for every string in a KB document, URLs and ids left out."""
    if isinstance(v, dict):
        for k, x in v.items():
            if k in ("id", "source_url", "sources", "url", "urls", "origin", "link"):
                continue
            yield from strings(x, f"{path}.{k}" if path else str(k))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from strings(x, f"{path}[{i}]")
    elif isinstance(v, str) and not v.startswith(("http://", "https://")):
        yield path, v


def instruction_shaped(text: str) -> str:
    """The first instruction-shaped part of `text`, "" when there is none.
    Invisible characters count as one (they can spell or hide one), and the
    patterns read the text without them ("ig\u200bnore" is "ignore")."""
    seen, n = invisible.clean(text)
    if n:
        return f"{n} invisible character(s)"
    for p in INSTRUCTION_PATTERNS:
        m = p.search(seen)
        if m:
            return m.group(0)
    return ""


def instructions_gate(head: dict[str, dict]) -> list[str]:
    out = []
    for name, doc in sorted(head.items()):
        for path, text in strings(doc):
            hit = instruction_shaped(text)
            if hit:
                out.append(f"instructions: `{name}` {path}: reads like an instruction to an assistant ({hit[:60]!r})")
    return out


def multi_source_gate(base: dict[str, dict], head: dict[str, dict], allow: dict) -> list[str]:
    was = {(name, key, json.dumps(r, sort_keys=True)) for name, key, r in records(base)}
    out = []
    for name, key, r in records(head):
        if not kbschema.is_list_or_build(r, key) or (name, key, json.dumps(r, sort_keys=True)) in was:
            continue
        why = kbschema.multi_source(r, allow)
        if why:
            out.append(f"multi-source: `{name}` {r.get('id') or r.get('spec')}: {why}")
    return out


def schema_of(doc: dict, key: str, r: dict, allow: dict, strict_type: bool = True) -> list[str]:
    """The schema's refusals of one record, its lane's shape included."""
    if key in ("statements", "contradictions"):
        mini = {"lane": doc.get("lane"), key: [r]}
    elif key == "builds":
        mini = {"builds": [r]}
    elif key == "digest":
        mini = {"digest": {"items": [r]}}
    else:  # a class's path: wowstats.classguide.check_path, in the suite
        return []
    return kbschema.check_file(mini, allow, strict_type)


def record_problems(base: dict[str, dict], head: dict[str, dict], sources: dict[str, str],
                    allow: dict) -> list[tuple[str, str, str]]:
    """(file, record id, why) for every record new or changed against `base`
    that a gate refuses: the schema with its lane's shape, the licence rules,
    the multi-source rule, instruction-shaped text. tools/kb/vet.py drops a
    model pass's records on these before the PR; the gates above refuse the
    same over the whole tree."""
    was = {(name, key, json.dumps(r, sort_keys=True)) for name, key, r in records(base)}
    known = {text_of(r) for _, _, r in records(base)}
    out = []
    for name, key, r in records(head):
        if (name, key, json.dumps(r, sort_keys=True)) in was:
            continue
        rid = str(r.get("id") or r.get("spec") or "?")
        whys = [f"schema: {e}" for e in schema_of(head.get(name) or {}, key, r, allow, name != "rules.json")]
        t = text_of(r)
        if t and t not in known:
            whys += [f"licence: {w}" for w in licence_of(t, r, sources)]
        if kbschema.is_list_or_build(r, key):
            m = kbschema.multi_source(r, allow)
            whys += [f"multi-source: {m}"] if m else []
        hit = next((h for _, x in strings(r) if (h := instruction_shaped(x))), "")
        whys += [f"instructions: reads like an instruction to an assistant ({hit[:60]!r})"] if hit else []
        out += [(name, rid, w) for w in whys]
    return out


def served(files: dict[str, dict], today) -> dict[str, str]:
    """{statement id: its lane} the loader serves (retired ones left out)."""
    every = [(name, r) for name, doc in files.items() if name.startswith("research/")
             for r in doc.get("statements") or [] if isinstance(r, dict)]
    gone = kbschema.retired_ids([r for _, r in every], today)
    return {r.get("id"): Path(name).stem for name, r in every if r.get("id") not in gone}


def churn_gate(base: dict[str, dict], head: dict[str, dict], today) -> tuple[list[str], int]:
    a, b = served(base, today), served(head, today)
    removed = [i for i in a if i not in b]
    per: dict[str, int] = {}
    for i in removed:
        per[a[i]] = per.get(a[i], 0) + 1
    size = {}
    for lane in a.values():
        size[lane] = size.get(lane, 0) + 1
    out = []
    if len(removed) > CHURN_MAX:
        out.append(f"churn: {len(removed)} statements would leave the KB, more than {CHURN_MAX} in one run")
    for lane, n in sorted(per.items()):
        if n >= size[lane]:
            out.append(f"churn: lane `{lane}` would lose all its {n} statements")
        elif n > max(CHURN_FLOOR, int(size[lane] * CHURN_SHARE)):
            out.append(f"churn: lane `{lane}` would lose {n} of its {size[lane]} statements "
                       f"(more than {CHURN_SHARE:.0%})")
    return out, len(removed)


# The refresh job's own variables and GitHub's file commands: the suite never
# sees them, as CI's test job doesn't. KB_ISSUES_OUT reaching it turned every
# issue test's "opened" into "deferred" and refused every watch PR (2026-11-22,
# #99); a test calling a tool's main() would write the step's outputs.
JOB_VARS = ("WHY", "MODE", "BRANCH", "BASE", "GH_REPO", "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_OUTPUT", "GITHUB_ENV",
            "GITHUB_PATH", "GITHUB_STEP_SUMMARY")


def suite_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("KB_") and k not in JOB_VARS}


def test_gate() -> list[str]:
    """ruff, the suite and the fixtures check, as CI's test job runs them
    (.github/workflows/ci.yml: every core, no browser suites); the failing
    tests' names when red."""
    out = []
    steps = [("ruff", ["uv", "run", "--frozen", "ruff", "check", "."]),
             ("pytest", ["uv", "run", "--frozen", "pytest", "-q", "-rfEs", "-p", "no:cacheprovider", "-n", "auto",
                         "-m", "not browser"]),
             ("fixtures", ["uv", "run", "--frozen", "python", "tools/fixtures/generate.py", "--check"])]
    env = suite_env()
    for name, cmd in steps:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
        sys.stderr.write(r.stdout[-6000:] + r.stderr[-3000:])
        if r.returncode:
            failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", r.stdout, re.M)
            detail = ", ".join(f"`{f}`" for f in failed[:15]) or (r.stdout + r.stderr).strip().splitlines()[-1:]
            out.append(f"tests: {name} failed" + (f": {detail}" if isinstance(detail, str) else
                                                    f": {' '.join(detail)[:300]}"))
    return out


def run(base_ref: str, sources_path: str | None, tests: bool, kb_dir: Path = KB) -> dict:
    today = datetime.now(UTC).date()
    allow = kbschema.load_allow(kb_dir / "sources.json")
    base, head = base_files(base_ref), head_files(kb_dir)
    sources = web.remembered(sources_path)
    refused = schema_gate(head, allow)
    lic, checked = licence_gate(base, head, sources)
    refused += lic
    refused += multi_source_gate(base, head, allow)
    # community.json too: the meta pass writes its one-line summaries.
    community = {"community.json": json.loads((kb_dir / "community.json").read_text(encoding="utf-8"))} \
        if (kb_dir / "community.json").exists() else {}
    refused += instructions_gate({**head, **community})
    refused += [f"size: {o}" for o in lifecycle.over_budget(kb_dir)]
    ch, removed = churn_gate(base, head, today)
    refused += ch
    if tests:
        refused += test_gate()
    return {"ok": not refused, "refused": refused,
            "counts": {"new_texts_checked": checked, "statements_removed": removed, "sources_read": len(sources),
                       "tests": tests}}


def markdown(result: dict) -> str:
    c = result["counts"]
    head = (f"New texts checked against their source: {c['new_texts_checked']}; statements leaving the KB: "
            f"{c['statements_removed']}; tests run: {'yes' if c['tests'] else 'no'}.")
    if result["ok"]:
        return f"**Gates: passed.** {head}\n"
    return f"**Gates: refused.** {head}\n\n" + "\n".join(f"- {r}" for r in result["refused"][:60]) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="the git ref to compare with (the main the run started from)")
    ap.add_argument("--sources", default=os.environ.get("KB_SOURCES"), help="the texts the model passes read")
    ap.add_argument("--no-tests", action="store_true", help="the KB gates only")
    ap.add_argument("--out", help="write the result here (JSON)")
    ap.add_argument("--markdown", help="write the result here (Markdown, for the PR and the issue)")
    a = ap.parse_args(argv)
    result = run(a.base, a.sources, not a.no_tests)
    print(json.dumps(result, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    if a.markdown:
        Path(a.markdown).write_text(markdown(result), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
