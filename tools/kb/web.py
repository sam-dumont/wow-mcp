"""What the KB refresh tools share: fetching a page as normalised text, its
hash, and the GitHub calls (through `gh`) that keep one issue per change.

Pages are untrusted data: their text is hashed, or handed to the model as
data (tools/kb/refresh.py); nothing in it is run or followed. Stdlib only,
so the watcher runs without the project's dependencies.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "wow-stats-kb-watch (+https://github.com/sam-dumont/wow-mcp)"
TIMEOUT = 30


class Fetcher:
    """GET with a short timeout and one polite pause per call. `get` returns
    (status, body); a network error is status 0."""

    def __init__(self, pause: float = 0.5):
        self.pause = pause

    def get(self, url: str) -> tuple[int, bytes]:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        time.sleep(self.pause)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - https URLs from our own lists
                return r.status, r.read(5_000_000)
        except urllib.error.HTTPError as e:
            return e.code, b""
        except (urllib.error.URLError, TimeoutError, OSError):
            return 0, b""


def text_of(body: bytes) -> str:
    """A page's readable text: the <article> (else <main>, else the whole
    page) without scripts, styles and tags, whitespace collapsed. Stable
    across fetches of an unchanged page (checked on Blizzard's hotfix page)."""
    t = body.decode("utf-8", "replace")
    m = re.search(r"<article\b[^>]*>(.*?)</article>", t, re.S | re.I) or re.search(
        r"<main\b[^>]*>(.*?)</main>", t, re.S | re.I)
    b = m.group(1) if m else t
    b = re.sub(r"<(script|style|noscript|svg)\b.*?</\1>", " ", b, flags=re.S | re.I)
    b = re.sub(r"<[^>]+>", " ", b)
    return re.sub(r"\s+", " ", html.unescape(b)).strip()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def remember(url: str, text: str) -> None:
    """Keep the text a model pass read for `url` (one JSON line in the file
    KB_SOURCES names, a temp path in the workflow), so the gates
    (tools/kb/gates.py) check the KB's new wording against the very text it
    came from. No KB_SOURCES: nothing kept."""
    path = os.environ.get("KB_SOURCES")
    if path and url and text:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"url": url, "text": text}, ensure_ascii=False) + "\n")


def remembered(path: str | None) -> dict[str, str]:
    """{url: every text read for it, joined} from a KB_SOURCES file."""
    out: dict[str, str] = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            out[r["url"]] = (out.get(r["url"], "") + "\n\n" + r["text"]).strip()
    return out


def repo() -> str | None:
    """The repository the job's branch, PRs and issues go to: KB_REPO when the
    job runs in the CI mirror (.github/workflows/kb-refresh.yaml), else the
    one it runs in."""
    return os.environ.get("KB_REPO") or os.environ.get("GITHUB_REPOSITORY")


class GitHub:
    """The few calls the tools make, through the gh CLI (GH_TOKEN in the
    environment). One issue per change kind, found by its marker."""

    def __init__(self, repo: str | None = None):
        self.repo = repo

    def _gh(self, *args: str, stdin: str | None = None) -> str:
        cmd = ["gh", *args] + (["--repo", self.repo] if self.repo and args[0] in ("issue", "pr") else [])
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=True).stdout

    def find_issue(self, marker: str, label: str) -> int | None:
        out = json.loads(self._gh("issue", "list", "--state", "open", "--label", label, "--limit", "100",
                                  "--json", "number,body") or "[]")
        return next((i["number"] for i in out if marker in (i.get("body") or "")), None)

    def create_issue(self, title: str, body: str, labels: list[str]) -> None:
        args = ["issue", "create", "--title", title, "--body-file", "-"]
        for lb in labels:
            args += ["--label", lb]
        self._gh(*args, stdin=body)

    def edit_issue(self, number: int, title: str, body: str) -> None:
        self._gh("issue", "edit", str(number), "--title", title, "--body-file", "-", stdin=body)

    def close_issue(self, number: int, comment: str) -> None:
        self._gh("issue", "close", str(number), "--comment", comment)

    def latest_release(self, repo: str) -> str | None:
        try:
            return self._gh("api", f"repos/{repo}/releases/latest", "--jq", ".tag_name").strip() or None
        except subprocess.CalledProcessError:
            return None


def keep_issue(gh, kind: str, label: str, title: str, body: str | None,
               closing: str = "Back to what the KB knows (data/kb/watch.json): nothing to review.") -> str:
    """Open, edit or close the one issue of a change kind: body None means
    nothing changed (close it if open). Returns what it did.

    With KB_ISSUES_OUT set (the refresh job's `run`, which holds no write
    token), the call is written there as a JSON line instead, and `land`
    replays it with the App's token: `web.py --replay-issues FILE`."""
    out = os.environ.get("KB_ISSUES_OUT")
    if out:
        rec = {"kind": kind, "label": label, "title": title, "body": body, "closing": closing}
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return "deferred"
    marker = f"<!-- kb-watch:{kind} -->"
    existing = gh.find_issue(marker, label)
    if body is None:
        if existing:
            gh.close_issue(existing, closing)
            return "closed"
        return "none"
    body = f"{marker}\n{body}"
    if existing:
        gh.edit_issue(existing, title, body)
        return "edited"
    gh.create_issue(title, body, [label])
    return "opened"


def replay_issues(path: str, gh) -> list[str]:
    """The issue calls a run deferred (KB_ISSUES_OUT), made for real."""
    done = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            done.append(f"{r['kind']}: " + keep_issue(gh, r["kind"], r["label"], r["title"], r["body"], r["closing"]))
    return done


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Replay the issue calls a refresh run deferred (GH_TOKEN).")
    ap.add_argument("--replay-issues", required=True, metavar="FILE")
    args = ap.parse_args()
    os.environ.pop("KB_ISSUES_OUT", None)
    print("\n".join(replay_issues(args.replay_issues, GitHub(repo()))))
