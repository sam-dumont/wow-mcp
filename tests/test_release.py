"""tools/release/release.py: the version a release button cuts, the release
commit it writes and the notes it publishes, on a copy of the real tree's
version files and a made-up CHANGELOG."""

from __future__ import annotations

import datetime as dt
import importlib.util
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("release", ROOT / "tools/release/release.py")
release = importlib.util.module_from_spec(_spec)
sys.modules["release"] = release  # dataclasses look their module up there
_spec.loader.exec_module(release)
V, Refused, decide = release.Version.parse, release.Refused, release.decide

CHANGELOG = """# Changelog

## Unreleased

**A new page.** It shows things.

## 1.2.0-rc.2 (2026-01-03)

**Second candidate fix.**

## 1.2.0-rc.1 (2026-01-02)

**First candidate feature.**

### Upgrading

Nothing to change.

## 1.1.0 (2026-01-01)

**Old news.**
"""


def plan(current, pending="x", tags=(), channel="stable", bump="auto"):
    p = decide(current=V(current), pending=pending, tags=set(tags), channel=channel, bump=bump)
    return p.mode, str(p.version)


@pytest.mark.parametrize("current,channel,bump,want", [
    ("0.6.1", "rc", "minor", "0.7.0-rc.1"),
    ("0.6.1", "stable", "minor", "0.7.0"),
    ("0.6.1", "stable", "auto", "0.6.2"),
    ("0.6.1", "rc", "major", "1.0.0-rc.1"),
    ("0.7.0-rc.1", "rc", "auto", "0.7.0-rc.2"),
    ("0.7.0-rc.2", "stable", "auto", "0.7.0"),
])
def test_next_version(current, channel, bump, want):
    assert plan(current, tags={f"v{current}"}, channel=channel, bump=bump) == ("prepare", want)


def test_a_final_promotes_its_candidate_with_nothing_new():
    assert plan("0.7.0-rc.2", pending="", tags={"v0.7.0-rc.2"}) == ("prepare", "0.7.0")


def test_an_untagged_release_commit_is_tagged_as_it_is():
    # Merged, never tagged (an interrupted run, a release PR made by hand).
    assert plan("0.7.0-rc.1", pending="", tags={"v0.6.1"}, channel="rc") == ("tag", "0.7.0-rc.1")
    assert plan("0.7.0", pending="", tags={"v0.6.1"}) == ("tag", "0.7.0")


@pytest.mark.parametrize("current,pending,tags,channel,bump,says", [
    ("0.6.1", "", {"v0.6.1"}, "stable", "auto", "nothing under"),
    ("0.7.0-rc.1", "", {"v0.7.0-rc.1"}, "rc", "auto", "nothing under"),
    ("0.7.0-rc.1", "x", {"v0.7.0-rc.1"}, "rc", "minor", "open release candidate"),
    ("0.7.0-rc.1", "", set(), "stable", "auto", "channel=rc"),
    ("0.7.0", "", set(), "stable", "minor", "bump must be auto"),
    ("0.6.1", "x", set(), "beta", "auto", "channel is"),
    ("0.6.1", "x", set(), "rc", "huge", "bump is"),
])
def test_refusals_say_what_to_press(current, pending, tags, channel, bump, says):
    with pytest.raises(Refused, match=says):
        decide(current=V(current), pending=pending, tags=tags, channel=channel, bump=bump)


@pytest.fixture
def tree(tmp_path):
    """The real files that carry the version, a made-up CHANGELOG, a git repo."""
    for rel, *_ in release.TARGETS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_apply_writes_every_version_and_moves_unreleased(tree):
    changed = release.apply(tree, V("1.2.0-rc.3"), dt.date(2026, 1, 4))
    assert set(changed) == {rel for rel, *_ in release.TARGETS} | {"CHANGELOG.md"}
    assert tomllib.loads((tree / "pyproject.toml").read_text())["project"]["version"] == "1.2.0-rc.3"
    assert '__version__ = "1.2.0-rc.3"' in (tree / "wowstats/__init__.py").read_text()
    assert re.search(r'^VERSION = "1\.2\.0-rc\.3"$', (tree / "shipper/wow_stats.py").read_text(), re.M)
    lock = (tree / "uv.lock").read_text()
    assert '[[package]]\nname = "wow-stats"\nversion = "1.2.0rc3"' in lock  # PEP 440, as uv writes it
    assert 'placeholder: "wow-stats 1.2.0-rc.3"' in (tree / ".github/ISSUE_TEMPLATE/bug.yml").read_text()
    text = (tree / "CHANGELOG.md").read_text()
    assert text.startswith("# Changelog\n\n## Unreleased\n\n## 1.2.0-rc.3 (2026-01-04)\n\n**A new page.**")
    assert release.body(text, "Unreleased") == ""
    assert release.current_version(tree) == V("1.2.0-rc.3")
    # The next press: nothing new, so no next candidate.
    with pytest.raises(Refused, match="nothing under"):
        release.decide(current=V("1.2.0-rc.3"), pending="", tags={"v1.2.0-rc.3"}, channel="rc", bump="auto")


def test_apply_refuses_a_version_already_in_the_changelog(tree):
    with pytest.raises(Refused, match="already has"):
        release.apply(tree, V("1.2.0-rc.2"), dt.date(2026, 1, 4))


def test_a_final_with_nothing_new_still_gets_its_heading(tree):
    (tree / "CHANGELOG.md").write_text(CHANGELOG.replace("**A new page.** It shows things.\n", ""))
    release.apply(tree, V("1.2.0"), dt.date(2026, 1, 5))
    assert release.body((tree / "CHANGELOG.md").read_text(), "1.2.0") == "No changes since the last release candidate."


def test_notes(tree):
    assert release.notes(tree, V("1.2.0-rc.1")) == "**First candidate feature.**\n\n### Upgrading\n\nNothing to change.\n"
    release.apply(tree, V("1.2.0"), dt.date(2026, 1, 5))
    final = release.notes(tree, V("1.2.0"))
    # A final carries its whole series, newest candidate first; never older finals.
    assert final.index("**A new page.**") < final.index("### From 1.2.0-rc.2") < final.index("### From 1.2.0-rc.1")
    assert "Old news" not in final
    with pytest.raises(Refused, match="no 9.9.9 section"):
        release.notes(tree, V("9.9.9"))


def test_plan_reads_the_tree_and_the_tags(tree):
    release.apply(tree, V("1.2.0-rc.3"), dt.date(2026, 1, 4))
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-qm", "r",
                    "--allow-empty"], cwd=tree, check=True)
    assert release.plan(tree, "rc", "auto") == release.Plan("tag", V("1.2.0-rc.3"))
    subprocess.run(["git", "tag", "v1.2.0-rc.3"], cwd=tree, check=True)
    assert release.plan(tree, "stable", "auto") == release.Plan("prepare", V("1.2.0"))
    assert release.Plan("prepare", V("1.2.0-rc.4")).lines() == [
        "mode=prepare", "version=1.2.0-rc.4", "tag=v1.2.0-rc.4", "prerelease=true"]


def test_the_real_changelog_has_an_unreleased_section():
    release.unreleased(ROOT)  # raises when the heading is gone
    assert release.current_version(ROOT) is not None


def test_the_server_announces_a_candidate_shipper(tree):
    """The server reads the shipper's VERSION line to announce updates
    (wowstats/account.py): a candidate's must read too, or every install
    sees "the server names no version"."""
    from wowstats import account

    release.apply(tree, V("1.2.0-rc.3"), dt.date(2026, 1, 4))
    m = account._SHIPPER_VERSION.search((tree / "shipper/wow_stats.py").read_bytes())
    assert m and m.group(1) == b"1.2.0-rc.3"


# ------------------------------------------------- GitHub's size limits
# 0.7.0's first stable run failed: its notes (every candidate since 0.6.1,
# 108 kB) went into the release PR's body, and GitHub refuses one over
# 65,536 characters. The PR body now links the notes; the release body
# carries them, cut with a link should a series ever pass 125,000.

URL = "https://github.com/o/r/blob/abc123/CHANGELOG.md"


@pytest.fixture
def real_tree(tree):
    """The tree with the real CHANGELOG, its current series' final applied
    when it isn't there yet (before 0.7.0's release: the 0.7.0 that broke)."""
    for rel in ("CHANGELOG.md", "tools/polestar-tag", "data/route-reference.json"):
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tree / rel)
    final = release.current_version(ROOT).core
    if release.body((tree / "CHANGELOG.md").read_text(), str(final)) is None:
        release.apply(tree, final, dt.date(2026, 11, 22))
    return tree, final


def test_the_real_release_pr_body_is_short(real_tree):
    tree, final = real_tree
    pr = release.pr_body(tree, final, URL)
    assert release.size(pr) < 4_000, release.size(pr)
    assert f"v{final}" in pr and f"({URL}#" in pr
    # The notes it links are the whole section, uncut, under the release body's limit.
    notes = release.notes(tree, final, URL)
    assert release.size(notes) <= release.NOTES_LIMIT < release.RELEASE_BODY_LIMIT


@pytest.mark.parametrize(("opening", "quotes"), [
    ("One change for players.\nIt spans two lines.", ["> One change for players. It spans two lines."]),
    ("### Fixed", []),
    ("### Knowledge base", []),
])
def test_release_pr_quotes_only_an_opening_paragraph(tree, opening, quotes):
    section = f"{opening}\n\n- A fix for players."
    (tree / "CHANGELOG.md").write_text(CHANGELOG.replace("**A new page.** It shows things.", section))
    version = V("1.2.0")
    release.apply(tree, version, dt.date(2026, 1, 5))
    pr = release.pr_body(tree, version, URL)
    assert [line for line in pr.splitlines() if line.startswith("> ")] == quotes
    assert "- A fix for players." not in pr
    if not quotes:
        assert opening not in pr
    assert release.notes(tree, version, URL).startswith(section)


def test_the_pr_body_does_not_grow_with_the_notes(tree):
    big = "\n".join(f"- Entry {i}: " + "x" * 200 for i in range(2_000))  # ~420 kB
    (tree / "CHANGELOG.md").write_text(CHANGELOG.replace("**Second candidate fix.**", big))
    release.apply(tree, V("1.2.0"), dt.date(2026, 1, 5))
    pr = release.pr_body(tree, V("1.2.0"), URL)
    assert release.size(pr) < 2_000
    assert "**1.2.0**: 2000 entries, gathering 1.2.0-rc.1 to 1.2.0-rc.2." in pr
    assert f"({URL}#120-2026-01-05)" in pr  # GitHub's anchor for "## 1.2.0 (2026-01-05)"
    assert "Entry 1:" not in pr


def test_notes_past_the_limit_stop_at_a_paragraph_and_link_the_rest(tree):
    big = "\n\n".join(f"- Entry {i}: " + "é" * 300 for i in range(1_000))  # multibyte: bytes count
    (tree / "CHANGELOG.md").write_text(CHANGELOG.replace("**Second candidate fix.**", big))
    release.apply(tree, V("1.2.0"), dt.date(2026, 1, 5))
    notes = release.notes(tree, V("1.2.0"), URL)
    assert release.size(notes) <= release.NOTES_LIMIT
    assert notes.endswith(f"All of them: [CHANGELOG.md]({URL}#120-2026-01-05).\n")
    kept = notes.rsplit("\n\n**The notes stop here", 1)[0]
    assert kept.startswith("**A new page.**") and kept.rsplit("\n\n", 1)[1].endswith("é" * 300)
    # Under the limit nothing changes, link or not.
    assert release.notes(tree, V("1.2.0-rc.1"), URL) == release.notes(tree, V("1.2.0-rc.1"))


def test_the_release_workflow_links_the_notes_and_owns_its_branch():
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]
    prepare = next(s["run"] for s in jobs["prepare"]["steps"] if s.get("id") == "pr")
    assert "release pr-body" in prepare and "release notes" not in prepare
    # A leftover branch is replaced only when it is this workflow's own commit, as read.
    assert "git push -qf" not in prepare
    assert '--force-with-lease="refs/heads/$branch:$old"' in prepare
    assert '"Release_$VERSION"' in prepare and "github-actions[bot]" in prepare
    publish = " ".join(s.get("run", "") for s in jobs["publish"]["steps"])
    assert "release.py notes \"$VERSION\" \\\n" in publish and "--changelog-url" in publish
