"""No public page types a value that has a source: it writes `{{name}}` (tools/docs_values.py).

The docs said Polestar 0.4.2.1 for a day while the tag and the data were 0.4.2.2, and
route times from the release before. A page that quotes a value the build can fill
goes stale the day the source moves; this test makes that a red check instead, on
the pull request that moves the source (a knowledge base bump, a release) or adds the
page. The pages: the README, THIRD_PARTY.md, docs/, the site's own pages, and the
CHANGELOG's "Unreleased" (older sections are history, frozen by release.py apply).

THIRD_PARTY.md and the clone lines name the tag outright (GitHub shows them raw): the
bump moves them with the tag (tools/kb/bump.py move_tag), so they must equal it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import docs_values  # noqa: E402

VALUES = docs_values.values()


def _unreleased() -> str:
    text = (ROOT / "CHANGELOG.md").read_text()
    start = text.index("## Unreleased")
    nxt = re.search(r"^## ", text[start + 3:], re.M)
    return text[start:start + 3 + nxt.start()] if nxt else text[start:]


def public_pages() -> dict[str, str]:
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "README.md", "THIRD_PARTY.md", "docs/*.md",
                            "docs/**/*.md", "site/pages/*.md"], capture_output=True, text=True, check=True).stdout
    pages = {f: (ROOT / f).read_text() for f in sorted(set(files.split()))}
    pages["CHANGELOG.md (Unreleased)"] = _unreleased()
    return pages


PAGES = public_pages()
# "Polestar 0.4.2.2", "Polestar v0.4.2.2", a clone line's `--branch v0.4.2.2 .../polestar`.
POLESTAR = re.compile(r"\bPolestar\s+v?(\d+(?:\.\d+){2,3})\b|--branch\s+v?(\d+(?:\.\d+){2,3})\s+\S*fitzforge/polestar")
# A route to 60 in hours: the totals sit between 60 and 150 h, nothing else the pages say does.
ROUTE_HOURS = re.compile(r"\b(?:[6-9]\d|1[0-4]\d)(?:\.\d)?\s?(?:h|hours)\b")
# A released image by its version, which every release moves.
IMAGE = re.compile(r"\bwow-mcp(?:-render)?:\d")


@pytest.mark.parametrize("name", list(PAGES))
def test_polestar_version_is_the_tag(name):
    stale = [m[0] for m in POLESTAR.finditer(PAGES[name]) if (m[1] or m[2]) != VALUES["polestar_version"]]
    assert not stale, f"{name} names another Polestar than {VALUES['polestar_tag']}: {stale}; write {{{{polestar_version}}}}"


@pytest.mark.parametrize("name", [n for n in PAGES if not n.startswith("CHANGELOG")])
def test_route_hours_come_from_the_data(name):
    typed = ROUTE_HOURS.findall(PAGES[name])
    assert not typed, f"{name} types route hours {typed}: write {{{{route_hours_min}}}} and its kin (tools/docs_values.py)"


@pytest.mark.parametrize("name", list(PAGES))
def test_no_image_version_typed(name):
    assert not IMAGE.search(PAGES[name]), f"{name} pins an image version: write <version>, the release notes carry the digests"


@pytest.mark.parametrize("name", list(PAGES))
def test_placeholders_are_known(name):
    unknown = {m[1] for m in docs_values.PLACEHOLDER.finditer(PAGES[name])} - VALUES.keys()
    assert not unknown, f"{name} uses unknown values {sorted(unknown)} (tools/docs_values.py)"


def test_seed_event_count_not_typed(tmp_path):
    """The local demo's seed grows with the product: the pages say "a few thousand"."""
    events = tmp_path / "events.ndjson"
    subprocess.run([sys.executable, str(ROOT / "dev" / "seed.py"), "--out", str(events)], check=True,
                   capture_output=True)
    n = len(events.read_bytes().splitlines())
    spelled = re.compile(rf"\b{n:,}\b|\b{n}\b".replace(",", ",?"))
    hits = [name for name, text in PAGES.items() if spelled.search(text)]
    assert not hits, f"{hits} type the seed's {n} events"


# A friend's data waits FRIENDS_PURGE_DAYS after they leave the list
# (wowstats.tenancy.PURGE_DAYS): "7 days later (`FRIENDS_PURGE_DAYS`)",
# "`FRIENDS_PURGE_DAYS` (7 days)", "`FRIENDS_PURGE_DAYS` (7)". The self-host
# page said "deletes their data on the spot" for a week after the purge
# delay came in (2026-11-23).
PURGE = re.compile(r"(\d+)\s+days?\s+later\s+\(`FRIENDS_PURGE_DAYS`\)|`FRIENDS_PURGE_DAYS`\s+\((\d+)")
ON_THE_SPOT = re.compile(r"(?:deletes?|deleted|goes|removes?)\s+(?:their|the)\s+data\s+(?:on the spot|at once|"
                         r"right away|immediately)", re.I)


@pytest.mark.parametrize("name", [n for n in PAGES if not n.startswith("CHANGELOG")])
def test_the_purge_delay_is_the_codes(name):
    from wowstats import tenancy

    days = [m[1] or m[2] for m in PURGE.finditer(PAGES[name])]
    assert all(d == str(tenancy.PURGE_DAYS) for d in days), \
        f"{name} says FRIENDS_PURGE_DAYS is {days}, the code's default is {tenancy.PURGE_DAYS}"
    assert not ON_THE_SPOT.search(PAGES[name]), \
        f"{name} says a friend's data goes at once: it waits FRIENDS_PURGE_DAYS ({tenancy.PURGE_DAYS} days)"


def test_the_self_host_page_names_the_purge_delay():
    page = PAGES["docs/self-host/how-i-run-it.md"]
    assert PURGE.search(page) and "users delete --yes" in page
