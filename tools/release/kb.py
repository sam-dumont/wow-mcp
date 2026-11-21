"""A knowledge base refresh merged on main becomes a stable patch release
(owner, 2026-11-21): the KB is baked into the image (Dockerfile, `COPY
data/kb`), and Renovate or Dependabot only see a new version. Not a
`-kb.<date>` prerelease (it sorts below the release) nor `+build` metadata
(ignored, and not a valid Docker tag).

.github/workflows/kb-release.yml asks `guard` and `check`, release.yml's
plan asks `note`. All three read git and GitHub's API (gh, GH_TOKEN):

  guard SHA   Is SHA the merge of a kb-refresh PR? Prints kb=1|0 (and pr,
              mode). Only a PR the refresh job opened counts (`is_kb_pr`), not
              any push that touches data/kb.
  check       On the checked-out main: release=true|false, version, reason,
              note. No release while a candidate series is open (the KB rides
              the next candidate or the final), nor on an untagged version
              (an interrupted release: the button finishes it).
  note        The release's "Knowledge base" line: every kb-refresh merge
              since the last v* tag, by PR number and mode, and what moved.

The note is made from what the repository holds after the gates passed
(data/kb/watch.json, the changed file names, a Polestar tag that is a plain
version), never from a PR's title or body, a page or a release note.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release import ROOT, VERSION_RE, Version, current_version, git_tags  # noqa: E402

BRANCH_RE = re.compile(r"^kb-(watch|refresh|sweep|meta)$")
LABEL = "kb-watch"  # tools/kb/land.py puts it on every PR it opens
TAG_RE = re.compile(r"^v\d+(?:\.\d+){0,3}$")  # Polestar's: v0.4.1.1
BUILD_RE = re.compile(r"^\d+(?:\.\d+){1,4}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")  # an official page's key
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .-]{0,29}$")  # an addon's name: "WoWSims Exporter"
WATCH = "data/kb/watch.json"
LANES = re.compile(r"^data/kb/research/([a-z0-9][a-z0-9-]{0,39})\.json$")
FILES = {
    "data/kb/rules.json": "rules",
    "data/kb/news.json": "news digest",
    "data/kb/digest-history.json": "news digest",
    "data/kb/community.json": "community corner",
    "data/kb/sweep.json": "source sweep",
}


@dataclass(frozen=True)
class Change:
    """One merged kb-refresh PR: watch.json before and after, the files it changed."""

    number: int
    mode: str
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    release: bool
    reason: str
    version: str = ""

    def lines(self, note: str = "") -> list[str]:
        return [f"release={'true' if self.release else 'false'}", f"version={self.version}",
                f"reason={self.reason}", f"note={note}"]


# ----------------------------------------------------------------- deciding


def is_kb_pr(pr: dict, repo: str) -> str | None:
    """The mode of a merged PR the refresh job opened, else None: its branch
    kb-<mode> in this repository (never a fork's), opened by a bot (the CI
    App, tools/kb/land.py), labelled kb-watch, merged into main."""
    head = pr.get("head") or {}
    m = BRANCH_RE.match(head.get("ref") or "")
    if not (m and pr.get("merged_at") and (pr.get("base") or {}).get("ref") == "main"):
        return None
    if ((head.get("repo") or {}).get("full_name") or "").lower() != repo.lower():
        return None
    if (pr.get("user") or {}).get("type") != "Bot":
        return None
    if LABEL not in {label.get("name") for label in pr.get("labels") or []}:
        return None
    return m.group(1)


def blocked(*, current: Version, tags: set[str]) -> str | None:
    """Why no automatic release can go out now, whatever merged; None when one can."""
    versions = [Version.parse(t[1:]) for t in tags if t.startswith("v") and VERSION_RE.match(t[1:])]
    newest = max(versions, key=Version.order, default=None)
    if current.rc is not None or (newest is not None and newest.rc is not None):
        # A candidate tag newer than the last final, or a candidate prepared on main.
        series = (current if current.rc is not None else newest).core
        return (f"the release candidate series {series} is open (main carries {current}, newest tag "
                f"{f'v{newest}' if newest else 'none'}): no release. The knowledge base rides the next "
                f"candidate or {series}.")
    if f"v{current}" not in tags:
        return (f"main carries {current}, untagged: an interrupted release. The release button "
                f"(channel stable, bump auto) finishes it, knowledge base included.")
    return None


def decide(*, current: Version, tags: set[str], changes: list[Change]) -> Decision:
    """Whether the KB merges since the last release make a stable patch release."""
    why = blocked(current=current, tags=tags)
    if why:
        return Decision(False, why)
    if not changes:
        return Decision(False, f"no knowledge base merge since v{current}: nothing to release.")
    return Decision(True, f"{len(changes)} knowledge base merge(s) since v{current}.", str(current.bumped("patch")))


# ------------------------------------------------------------------- the note


def _known(watch: dict, *path: str):
    for key in path:
        watch = watch.get(key) if isinstance(watch, dict) else None
    return watch


def _changed_entries(before: dict, after: dict, key: str, name: str, shape: re.Pattern) -> list[str]:
    """The `name` of each entry of list `key` whose `known` moved, when it has the expected shape."""
    was = {e.get(name): e.get("known") for e in before.get(key) or [] if isinstance(e, dict)}
    return [e[name] for e in after.get(key) or []
            if isinstance(e, dict) and isinstance(e.get(name), str) and shape.match(e[name])
            and e.get("known") is not None and e.get("known") != was.get(e[name])]


def parts(changes: list[Change]) -> list[str]:
    """What moved, in a fixed order, from trusted values only."""
    out: list[str] = []

    def add(text: str):
        if text not in out:
            out.append(text)

    moves = [c for c in changes if _known(c.before, "polestar", "known") != _known(c.after, "polestar", "known")]
    if moves:
        old, new = _known(moves[0].before, "polestar", "known"), _known(moves[-1].after, "polestar", "known")
        safe = all(isinstance(t, str) and TAG_RE.match(t) for t in (old, new))
        add(f"Polestar {old} to {new}" if safe else "Polestar release")
    builds = [c for c in changes
              if _known(c.before, "client_build", "known") != _known(c.after, "client_build", "known")]
    if builds:
        build = _known(builds[-1].after, "client_build", "known")
        add(f"client build {build}" if isinstance(build, str) and BUILD_RE.match(build) else "client build")
    for label, key, name, shape in (("official pages", "official", "key", KEY_RE),
                                    ("addons", "addons", "name", NAME_RE)):
        names: list[str] = []
        for c in changes:
            names += [n for n in _changed_entries(c.before, c.after, key, name, shape) if n not in names]
        if names:
            add(f"{label} {', '.join(names)}")
    lanes = sorted({m.group(1) for c in changes for f in c.files if (m := LANES.match(f))})
    if lanes:
        add(f"lanes {', '.join(lanes)}")
    for c in changes:
        for f in c.files:
            if f in FILES:
                add(FILES[f])
    return out


def note(changes: list[Change]) -> str:
    if not changes:
        return ""
    prs = ", ".join(f"#{c.number} {c.mode}" for c in changes)
    moved = parts(changes)
    return f"Knowledge base refresh ({prs})" + (f": {'; '.join(moved)}." if moved else ".")


# ------------------------------------------------------------------- reading


def git(root: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check).stdout.strip()


def watch_at(root: Path, rev: str) -> dict:
    text = git(root, "show", f"{rev}:{WATCH}", check=False)
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}


def pulls(repo: str, sha: str) -> list[dict]:
    out = subprocess.run(["gh", "api", f"repos/{repo}/commits/{sha}/pulls"], capture_output=True, text=True,
                         check=True).stdout
    return json.loads(out or "[]")


def kb_pr(repo: str, sha: str) -> tuple[int, str] | None:
    for pr in pulls(repo, sha):
        mode = is_kb_pr(pr, repo)
        if mode:
            return int(pr["number"]), mode
    return None


def last_tag(root: Path) -> str:
    return git(root, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD", check=False)


def changes_since(root: Path, repo: str, since: str) -> list[Change]:
    """The kb-refresh merges on main's first-parent line after `since`, oldest first."""
    span = f"{since}..HEAD" if since else "HEAD"
    found = []
    for sha in git(root, "rev-list", "--reverse", "--first-parent", span, "--", "data/kb").split():
        pr = kb_pr(repo, sha)
        if pr:
            files = tuple(git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", f"{sha}^", sha).split("\n"))
            found.append(Change(pr[0], pr[1], watch_at(root, f"{sha}^"), watch_at(root, sha), files))
    return found


# ---------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("guard")
    g.add_argument("sha")
    sub.add_parser("check")
    sub.add_parser("note")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    a = ap.parse_args(argv)
    if not a.repo:
        ap.error("--repo or GITHUB_REPOSITORY")
    if a.cmd == "guard":
        pr = kb_pr(a.repo, a.sha)
        print("\n".join([f"kb={1 if pr else 0}", f"pr={pr[0] if pr else ''}", f"mode={pr[1] if pr else ''}"]))
    elif a.cmd == "check":
        tags, current = git_tags(a.root), current_version(a.root)
        # The API only when a release can go out at all.
        changes = [] if blocked(current=current, tags=tags) else changes_since(a.root, a.repo, last_tag(a.root))
        d = decide(current=current, tags=tags, changes=changes)
        print("\n".join(d.lines(note(changes) if d.release else "")))
    else:
        print(note(changes_since(a.root, a.repo, last_tag(a.root))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
