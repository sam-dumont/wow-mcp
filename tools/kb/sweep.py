"""The knowledge base's monthly sweep: every cited URL fetched once, no model.

    uv run python tools/kb/sweep.py              # write data/kb/sweep.json, print the summary
    uv run python tools/kb/sweep.py --github     # and keep the one sweep issue (label kb-watch)

For each URL a rule, statement or contradiction cites (data/kb/rules.json,
data/kb/research/*.json), records its HTTP status and the hash of its text
in data/kb/sweep.json, and compares with the last sweep:

- gone: 404 or 410 (a 403, 429 or network error is "unreadable": many
  sites refuse robots, which says nothing about the page);
- changed: the text hash differs from last month's;
- stale: statements whose last_verified is older than STALE_DAYS.

The summary goes to one issue (closed when there is nothing to look at);
the workflow opens a PR with the new sweep.json so next month compares
against this one. Costs nothing: no model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import web  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
KB = ROOT / "data" / "kb"
STALE_DAYS = 120
LABEL = "kb-watch"


def cited(kb_dir: Path = KB) -> dict[str, list[str]]:
    """{url: [the ids that cite it]} over every KB file."""
    out: dict[str, list[str]] = defaultdict(list)

    def add(rec: dict, rid: str) -> None:
        for u in [rec.get("source_url")] + list(rec.get("sources") or []):
            if isinstance(u, str) and u.startswith("https://") and rid not in out[u]:
                out[u].append(rid)

    for name, r in json.loads((kb_dir / "rules.json").read_text(encoding="utf-8"))["rules"].items():
        add(r, f"rule:{name}")
    for p in sorted((kb_dir / "research").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for name, r in (d.get("rules") or {}).items():
            add(r, f"rule:{name}")
        for k in ("statements", "contradictions"):
            for r in d.get(k) or []:
                add(r, r["id"])
    return dict(out)


def stale(kb_dir: Path = KB, today: date | None = None) -> list[str]:
    today = today or datetime.now(UTC).date()
    old = []
    for p in sorted((kb_dir / "research").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for r in d.get("statements") or []:
            try:
                if (today - date.fromisoformat(r["last_verified"])).days > STALE_DAYS:
                    old.append(r["id"])
            except (KeyError, ValueError):
                old.append(r.get("id", "?"))
    return old


def sweep(urls: dict[str, list[str]], fetch, was: dict) -> tuple[dict, dict]:
    """(the new sweep state {url: {status, hash}}, what to look at {gone, changed, unreadable})."""
    now, look = {}, {"gone": [], "changed": [], "unreadable": []}
    for u in sorted(urls):
        status, body = fetch.get(u)
        h = web.digest(web.text_of(body)) if status == 200 and body else None
        now[u] = {"status": status, "hash": h or (was.get(u) or {}).get("hash")}
        if status in (404, 410):
            look["gone"].append(u)
        elif status != 200:
            look["unreadable"].append(u)
        elif h and (was.get(u) or {}).get("hash") and was[u]["hash"] != h:
            look["changed"].append(u)
    return now, look


def body_of(look: dict, urls: dict[str, list[str]], old: list[str], day: str) -> str | None:
    if not (look["gone"] or look["changed"] or old):
        return None

    def rows(us: list[str]) -> str:
        return "\n".join(f"- {u} (cited by {', '.join(urls[u][:5])}{' ...' if len(urls[u]) > 5 else ''})"
                         for u in us[:60]) + (f"\n- ... and {len(us) - 60} more" if len(us) > 60 else "")

    parts = [f"Sweep of {len(urls)} cited URLs on {day} (`tools/kb/sweep.py`, workflow kb-refresh mode sweep)."]
    if look["gone"]:
        parts.append(f"## Gone ({len(look['gone'])})\nFind the page's new home or another source, else drop the "
                     f"statement to an open question.\n{rows(look['gone'])}")
    if look["changed"]:
        parts.append(f"## Changed since the last sweep ({len(look['changed'])})\nRe-read the page against the "
                     f"statements that cite it; bump their `last_verified` when they still hold.\n"
                     f"{rows(look['changed'])}")
    if old:
        parts.append(f"## Not checked for {STALE_DAYS} days ({len(old)} statements)\n"
                     + ", ".join(old[:80]) + (" ..." if len(old) > 80 else ""))
    parts.append(f"Unreadable this time (robots refused, rate limit, network; not counted): {len(look['unreadable'])}.")
    parts.append("This issue closes itself on a sweep with nothing to look at.")
    return "\n\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--github", action="store_true", help="open, edit or close the sweep issue (GH_TOKEN)")
    ap.add_argument("--pause", type=float, default=0.5, help="seconds between two fetches")
    a = ap.parse_args(argv)
    path = KB / "sweep.json"
    was = json.loads(path.read_text(encoding="utf-8")).get("urls", {}) if path.exists() else {}
    urls = cited()
    now, look = sweep(urls, web.Fetcher(a.pause), was)
    day = datetime.now(UTC).date().isoformat()
    path.write_text(json.dumps({"swept": day, "urls": now}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    old = stale()
    body = body_of(look, urls, old, day)
    did = web.keep_issue(web.GitHub(os.environ.get("GITHUB_REPOSITORY")), "sweep", LABEL,
                         "KB sweep: cited sources to look at", body) if a.github else ("changed" if body else "none")
    print(json.dumps({"urls": len(urls), **{k: len(v) for k, v in look.items()}, "stale": len(old), "issue": did}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
