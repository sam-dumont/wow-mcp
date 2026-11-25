"""The refresh job's check of a model pass's records, before they are folded
and committed (.github/workflows/kb-refresh.yaml; docs/kb.md, "The refresh
job").

    uv run python tools/kb/vet.py --dropped dropped.md   # KB_SOURCES: the texts the pass read

A pass (tools/kb/refresh.py, tools/kb/meta.py) writes a dated file
(research/refresh-<date>.json, meta-<date>.json) that the lifecycle folds
into the lanes. This folds a copy of the KB the same way (no network, no
model), runs the gates' per-record checks (tools/kb/gates.py
record_problems: the schema with its lane's shape, the licence rules, the
multi-source rule, instruction-shaped text) on every record the fold added
or changed, and drops from the dated file each of the pass's records that
fails, until none does. What it dropped is never committed; it is listed in
the PR under "Dropped". Why: on 2026-11-22 (#99) the pass replaced two
shaman class-guide records with flat ones (no class), the PR opened red and
sat there for hours.

A record the pass didn't write (a merge's result, say) isn't dropped here:
the gates refuse it over the whole tree, as before.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gates  # noqa: E402, I001 (tools/kb, beside this file)
import lifecycle  # noqa: E402
import web  # noqa: E402

from wowstats.analysis import kbschema  # noqa: E402

KB = gates.KB
ROUNDS = 10  # a drop can only uncover a record that leans on it; never more rounds than that


def _ids(doc: dict) -> set[str]:
    return {str(r.get("id")) for _, _, r in gates.records({"x": doc}) if r.get("id")}


def _drop(doc: dict, ids: set[str]) -> None:
    for key in ("statements", "contradictions", "builds", "endgame"):
        if isinstance(doc.get(key), list):
            doc[key] = [r for r in doc[key] if not (isinstance(r, dict) and str(r.get("id")) in ids)]
    for w in [doc.get("digest")] + list(doc.get("digests") or []):
        if isinstance(w, dict) and isinstance(w.get("items"), list):
            w["items"] = [it for it in w["items"] if not (isinstance(it, dict) and str(it.get("id")) in ids)]


def _folded(kb_dir: Path, dated: dict[str, dict], today: date) -> dict[str, dict]:
    """The KB files as the lifecycle's fold would leave them, from a copy."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "kb"
        shutil.copytree(kb_dir, tmp)
        for name, doc in dated.items():
            (tmp / "research" / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tree = lifecycle.load(tmp)
        lifecycle.run(tree, today)
        lifecycle.write(tree, tmp)
        return gates.head_files(tmp)


def vet(kb_dir: Path, sources: dict[str, str], today: date) -> list[dict]:
    """Drop the pass's failing records from its dated files in `kb_dir`;
    [{"id", "file", "why"}] of what went."""
    research = kb_dir / "research"
    dated = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(research.glob("*.json"))
             if lifecycle.DATED.match(p.stem)}
    if not dated:
        return []
    allow = kbschema.load_allow(kb_dir / "sources.json")
    before = {k: v for k, v in gates.head_files(kb_dir).items() if Path(k).stem not in dated}
    dropped: list[dict] = []
    for _ in range(ROUNDS):
        ours = set().union(*(_ids(d) for d in dated.values()))
        bad: dict[str, tuple[str, str]] = {}
        for name, rid, why in gates.record_problems(before, _folded(kb_dir, dated, today), sources, allow):
            if rid in ours:
                bad.setdefault(rid, (name, why))
            else:
                print(f"not the pass's record, left to the gates: {name} {rid}: {why}", file=sys.stderr)
        if not bad:
            break
        for doc in dated.values():
            _drop(doc, set(bad))
        dropped += [{"id": rid, "file": name, "why": why} for rid, (name, why) in bad.items()]
    for name, doc in dated.items():
        (research / f"{name}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    return dropped


def _safe(t: str) -> str:  # a reason can carry a model's words: no link syntax, no @mention
    return re.sub(r"[\[\]<>`]", "", " ".join(str(t).split())[:240]).replace("@", "@​")


def markdown(dropped: list[dict]) -> str:
    if not dropped:
        return ""
    return ("## Dropped\n\nThe model pass proposed these; the checks this PR must pass refused them, so they are "
            "not in it (tools/kb/vet.py). Their page is still marked read: a person re-proposes one with mode "
            "`refresh` once the cause is fixed.\n\n"
            + "\n".join(f"- `{_safe(d['id'])}` (`{_safe(d['file'])}`): {_safe(d['why'])}" for d in dropped) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sources", default=os.environ.get("KB_SOURCES"), help="the texts the model passes read")
    ap.add_argument("--dropped", help="append the PR body's Dropped section here")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today, UTC)")
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.today) if a.today else datetime.now(UTC).date()
    dropped = vet(KB, web.remembered(a.sources), today)
    print(json.dumps({"dropped": dropped}, indent=1, ensure_ascii=False))
    if a.dropped and dropped:
        with open(a.dropped, "a", encoding="utf-8") as f:
            f.write("\n" + markdown(dropped))
    if gh_out := os.environ.get("GITHUB_OUTPUT"):
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"dropped={len(dropped)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
