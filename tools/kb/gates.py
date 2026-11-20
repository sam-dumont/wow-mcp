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
        long_quotes = [q for q in verbatim.quoted_runs(t) if len(verbatim.words(q)) > verbatim.QUOTE_MAX]
        if long_quotes:
            out.append(f"licence: `{name}` {rid}: quotes {len(verbatim.words(long_quotes[0]))} words (15 at most)")
        read = [sources[u] for u in kbschema.urls(r) if u in sources]
        if not read:
            out.append(f"licence: `{name}` {rid}: new wording with no source read in this run to check it against")
            continue
        why = next((w for w in (verbatim.copied(t, s) for s in read) if w), "")
        if why:
            out.append(f"licence: `{name}` {rid}: {why}")
    return out, n


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


def test_gate() -> list[str]:
    """ruff, the suite and the fixtures check, as CI's test job; the failing
    tests' names when red."""
    out = []
    steps = [("ruff", ["uv", "run", "--frozen", "ruff", "check", "."]),
             ("pytest", ["uv", "run", "--frozen", "pytest", "-q", "-rfEs", "-p", "no:cacheprovider"]),
             ("fixtures", ["uv", "run", "--frozen", "python", "tools/fixtures/generate.py", "--check"])]
    for name, cmd in steps:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
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
