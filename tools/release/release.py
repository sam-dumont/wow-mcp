"""The release flow's decisions: which version, what changes in the tree, the notes.

.github/workflows/release.yml calls this; docs/development.md, "Releasing",
says which button to press. Three commands, all local and offline:

  plan  --channel stable|rc --bump auto|patch|minor|major [--kb-note TEXT]
        Reads pyproject.toml, CHANGELOG.md's "Unreleased" section and the
        repository's v* tags, and prints `key=value` lines (GITHUB_OUTPUT
        format): mode, version, tag, prerelease. Refuses with a message on
        stderr and exit 1 when there is nothing to release or the inputs
        don't fit the tree.
  apply VERSION [--date YYYY-MM-DD] [--kb-note TEXT]
        Writes VERSION everywhere the tree carries it and moves "Unreleased"
        under "## VERSION (date)".
  notes VERSION [--changelog-url URL]
        Prints the GitHub release notes for VERSION from CHANGELOG.md, cut
        to fit GitHub's release body with a link to URL when they don't.
  pr-body VERSION --changelog-url URL
        Prints the release pull request's body: the version, the section's
        opening paragraph and a link to URL (CHANGELOG.md at the release
        commit). Never the notes themselves: 0.7.0's, every candidate since
        0.6.1, broke GitHub's 65,536-character PR body limit.

Modes:
  prepare  "Unreleased" has entries: the release commit (apply) comes first,
           through a pull request like any other change, then the tag.
  tag      The tree already carries an untagged version and "Unreleased" is
           empty: a release commit that was merged but never tagged (an
           interrupted run, or one prepared by hand). Tag it as it is.

Versions are X.Y.Z or X.Y.Z-rc.N (a release candidate, a GitHub prerelease).
`bump` picks the next X.Y.Z from the current version (auto means patch);
while a candidate series is open, channel rc cuts the next candidate and
channel stable promotes it, and bump must stay auto.

--kb-note: the knowledge base refresh's line (tools/release/kb.py note, for
a run kb-release.yml started), added under "### Knowledge base" in the
release's section. It counts as an entry, so a refresh alone makes a
release; it never turns a `tag` run into a new version.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import docs_values  # noqa: E402  (tools/docs_values.py: the values the public pages quote)

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")
HEADING_RE = re.compile(r"^## (\S+)(?: \(\d{4}-\d{2}-\d{2}\))?[ \t]*$", re.M)


class Refused(Exception):
    """The inputs don't make a release: the message says why and what to press instead."""


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    rc: int | None = None

    @classmethod
    def parse(cls, text: str) -> Version:
        m = VERSION_RE.match(text.strip())
        if not m:
            raise Refused(f"not a release version: {text!r} (X.Y.Z or X.Y.Z-rc.N)")
        a, b, c, rc = m.groups()
        return cls(int(a), int(b), int(c), int(rc) if rc else None)

    @property
    def core(self) -> Version:
        return Version(self.major, self.minor, self.patch)

    def bumped(self, kind: str) -> Version:
        if kind == "major":
            return Version(self.major + 1, 0, 0)
        if kind == "minor":
            return Version(self.major, self.minor + 1, 0)
        return Version(self.major, self.minor, self.patch + 1)

    def candidate(self, n: int) -> Version:
        return Version(self.major, self.minor, self.patch, n)

    def pep440(self) -> str:
        """The normalized form packaging tools write (uv.lock): 0.7.0rc1."""
        core = f"{self.major}.{self.minor}.{self.patch}"
        return core if self.rc is None else f"{core}rc{self.rc}"

    def order(self) -> tuple:
        """Sort key: a candidate sorts below its final (0.7.0-rc.4 < 0.7.0)."""
        return (self.major, self.minor, self.patch, self.rc is None, self.rc or 0)

    def __str__(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        return core if self.rc is None else f"{core}-rc.{self.rc}"


@dataclass(frozen=True)
class Plan:
    mode: str  # prepare | tag
    version: Version

    def lines(self) -> list[str]:
        return [f"mode={self.mode}", f"version={self.version}", f"tag=v{self.version}",
                f"prerelease={'true' if self.version.rc is not None else 'false'}"]


# ------------------------------------------------------------------ reading


def current_version(root: Path) -> Version:
    m = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M)
    if not m:
        raise Refused("pyproject.toml has no version")
    return Version.parse(m.group(1))


def sections(changelog: str) -> list[tuple[str, int, int]]:
    """(name, start of the body, end of the body) for every `## ` heading."""
    heads = list(HEADING_RE.finditer(changelog))
    return [(h.group(1), h.end(), heads[i + 1].start() if i + 1 < len(heads) else len(changelog))
            for i, h in enumerate(heads)]


def body(changelog: str, name: str) -> str | None:
    for n, start, end in sections(changelog):
        if n == name:
            return changelog[start:end].strip()
    return None


def unreleased(root: Path) -> str:
    text = (root / "CHANGELOG.md").read_text()
    got = body(text, "Unreleased")
    if got is None:
        raise Refused('CHANGELOG.md has no "## Unreleased" section')
    return got


def git_tags(root: Path) -> set[str]:
    out = subprocess.run(["git", "tag", "--list", "v*"], cwd=root, check=True,
                         capture_output=True, text=True).stdout
    return set(out.split())


# ----------------------------------------------------------------- deciding


def decide(*, current: Version, pending: str, tags: set[str], channel: str, bump: str, kb_note: str = "") -> Plan:
    if channel not in ("stable", "rc"):
        raise Refused(f"channel is stable or rc, not {channel!r}")
    if bump not in ("auto", "patch", "minor", "major"):
        raise Refused(f"bump is auto, patch, minor or major, not {bump!r}")
    written = bool(pending.strip())
    has_entries = written or bool(kb_note.strip())
    tagged = f"v{current}" in tags

    if not tagged and not written:
        # A release commit is on main without its tag: finish that release.
        if (channel == "rc") != (current.rc is not None):
            raise Refused(f"main carries {current}, untagged, ready to release; run channel="
                          f"{'rc' if current.rc is not None else 'stable'} to tag it")
        if bump != "auto":
            raise Refused(f"main carries {current}, untagged, ready to release; bump must be auto")
        return Plan("tag", current)

    if current.rc is not None:
        if bump != "auto":
            raise Refused(f"{current} is an open release candidate series: bump=auto with channel=rc "
                          f"cuts the next candidate, channel=stable releases {current.core}")
        if channel == "stable":
            return Plan("prepare", current.core)
        if not has_entries:
            raise Refused(f'nothing under "Unreleased" since {current}')
        return Plan("prepare", current.candidate(current.rc + 1))

    if not has_entries:
        raise Refused(f'nothing under "Unreleased" since {current}')
    core = current.bumped("patch" if bump == "auto" else bump)
    return Plan("prepare", core.candidate(1) if channel == "rc" else core)


def plan(root: Path, channel: str, bump: str, kb_note: str = "") -> Plan:
    return decide(current=current_version(root), pending=unreleased(root), tags=git_tags(root),
                  channel=channel, bump=bump, kb_note=kb_note)


# ----------------------------------------------------------------- applying

# Every file that carries the version, with the line that holds it.
# tests/test_shipper_update.py keeps pyproject, the package and the shipper
# equal; uv.lock holds the project's own entry in PEP 440 form.
TARGETS = (
    ("pyproject.toml", r'^(version = ")[^"]+(")', str),
    ("wowstats/__init__.py", r'^(__version__ = ")[^"]+(")', str),
    ("shipper/wow_stats.py", r'^(VERSION = ")[^"]+(")', str),
    ("uv.lock", r'(\[\[package\]\]\nname = "wow-stats"\nversion = ")[^"]+(")', Version.pep440),
    (".github/ISSUE_TEMPLATE/bug.yml", r'^(\s*placeholder: "wow-stats )[^"]+(")', str),
)


def apply(root: Path, version: Version, date: dt.date, kb_note: str = "") -> list[str]:
    changed = []
    for rel, pattern, form in TARGETS:
        path = root / rel
        text = path.read_text()
        new, n = re.subn(pattern, lambda m: f"{m.group(1)}{form(version)}{m.group(2)}", text, count=1, flags=re.M)
        if n != 1:
            raise Refused(f"{rel}: no version line to rewrite")
        if new != text:
            path.write_text(new)
            changed.append(rel)

    path = root / "CHANGELOG.md"
    text = path.read_text()
    if body(text, str(version)) is not None:
        raise Refused(f"CHANGELOG.md already has a {version} section")
    for name, start, end in sections(text):
        if name == "Unreleased":
            entries = text[start:end].strip()
            # The notes keep the values this release ships with: "Polestar {{polestar_version}}"
            # in Unreleased (filled live by the docs site) is frozen here, after the version
            # moved above, so the section never changes when the tag moves later.
            if docs_values.PLACEHOLDER.search(entries):
                entries = docs_values.fill(entries, docs_values.values(root))
            if kb_note.strip():  # one line, whatever it was given
                entries = f"{entries}\n\n### Knowledge base\n\n- {' '.join(kb_note.split())}".strip()
            if not entries and version.rc is None:
                # A final that promotes its last candidate as it is (decide() allows it).
                entries = "No changes since the last release candidate."
            if not entries:
                raise Refused('nothing under "Unreleased"')
            text = f"{text[:start]}\n\n## {version} ({date.isoformat()})\n\n{entries}\n\n{text[end:].lstrip()}"
            break
    path.write_text(text)
    return [*changed, "CHANGELOG.md"]


# -------------------------------------------------------------------- notes

# GitHub's limits are in characters; size() counts UTF-8 bytes, never fewer.
PR_BODY_LIMIT = 65_536
RELEASE_BODY_LIMIT = 125_000
# The notes' share of the release body: publish appends the "### Images"
# lines (two tag@digest refs, a few hundred bytes) after them.
NOTES_LIMIT = RELEASE_BODY_LIMIT - 5_000


def size(text: str) -> int:
    return len(text.encode())


def candidates(text: str, version: Version) -> list[tuple[str, str]]:
    """(name, entries) of each candidate of a final, as CHANGELOG.md orders them (newest first)."""
    if version.rc is not None:
        return []
    return [(n, text[s:e].strip()) for n, s, e in sections(text)
            if VERSION_RE.match(n) and Version.parse(n).core == version and Version.parse(n).rc]


def anchor(text: str, version: Version) -> str:
    """GitHub's anchor for VERSION's heading: "## 0.7.0 (2026-11-22)" is #070-2026-11-22."""
    for h in HEADING_RE.finditer(text):
        if h.group(1) == str(version):
            title = h.group(0)[3:].strip().lower()
            return "#" + re.sub(r"[^\w\- ]", "", title).replace(" ", "-")
    raise Refused(f"CHANGELOG.md has no {version} section")


def notes(root: Path, version: Version, changelog_url: str = "", limit: int = NOTES_LIMIT) -> str:
    """The section of VERSION; a final also carries its candidates' sections.

    Past `limit`, the notes stop at the last paragraph that fits and link
    the whole section instead (the CHANGELOG has no limit).
    """
    text = (root / "CHANGELOG.md").read_text()
    own = body(text, str(version))
    if own is None:
        raise Refused(f"CHANGELOG.md has no {version} section")
    parts = [own, *(f"### From {name}\n\n{entries}" for name, entries in candidates(text, version))]
    full = "\n\n".join(parts).strip() + "\n"
    if size(full) <= limit:
        return full
    where = f"[CHANGELOG.md]({changelog_url}{anchor(text, version)})" if changelog_url else "CHANGELOG.md"
    tail = f"\n\n**The notes stop here: GitHub caps a release's text.** All of them: {where}.\n"
    budget = limit - size(tail)
    cut = full.encode()[:budget].decode(errors="ignore")
    # A paragraph boundary, so no list item, table or code fence is cut in half.
    cut = cut[:cut.rfind("\n\n")] if "\n\n" in cut else ""
    return cut.rstrip() + tail


def pr_body(root: Path, version: Version, changelog_url: str) -> str:
    """The release PR's body, whatever the release's size: nothing in it grows with the notes."""
    text = (root / "CHANGELOG.md").read_text()
    own = body(text, str(version))
    if own is None:
        raise Refused(f"CHANGELOG.md has no {version} section")
    lede = own.split("\n\n", 1)[0].strip()
    if size(lede) > 1_000 or lede.startswith(("-", "#", "|", "`")):
        lede = ""  # a section that opens on its entries has no summary to quote
    series = candidates(text, version)
    entries = sum(1 for _, e in [("", own), *series] for line in e.splitlines() if line.startswith("- "))
    gathers = ""
    if series:
        names = [n for n, _ in series]
        gathers = f", gathering {names[-1]} to {names[0]}" if len(names) > 1 else f", gathering {names[0]}"
    lines = [
        f"The release commit for v{version}: the version in every file that carries it, and "
        "CHANGELOG.md's \"Unreleased\" under its heading. The release run merges it once the checks "
        "pass, then builds, tags and publishes.",
        "",
        f"**{version}**: {entries} entries{gathers}.",
    ]
    if lede:
        lines += ["", "> " + " ".join(lede.split())]
    lines += ["", f"The notes: [CHANGELOG.md at this commit]({changelog_url}{anchor(text, version)}). "
              "The GitHub release carries them once published."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--channel", default="stable")
    p.add_argument("--bump", default="auto")
    p.add_argument("--kb-note", default="")
    a = sub.add_parser("apply")
    a.add_argument("version")
    a.add_argument("--date", type=dt.date.fromisoformat, default=dt.datetime.now(dt.UTC).date())
    a.add_argument("--kb-note", default="")
    n = sub.add_parser("notes")
    n.add_argument("version")
    n.add_argument("--changelog-url", default="")
    b = sub.add_parser("pr-body")
    b.add_argument("version")
    b.add_argument("--changelog-url", required=True)
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "plan":
            print("\n".join(plan(args.root, args.channel, args.bump, args.kb_note).lines()))
        elif args.cmd == "apply":
            print("\n".join(apply(args.root, Version.parse(args.version), args.date, args.kb_note)))
        elif args.cmd == "notes":
            sys.stdout.write(notes(args.root, Version.parse(args.version), args.changelog_url))
        else:
            sys.stdout.write(pr_body(args.root, Version.parse(args.version), args.changelog_url))
    except Refused as e:
        print(f"release: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
