"""The values the public pages quote, from their sources: one place, read at build time.

    uv run --no-project python tools/docs_values.py         # JSON, for site/scripts/prepare.mjs

A page writes `{{name}}` (`{{polestar_version}}`, `{{route_hours_min}}`...) and the docs
site's build fills it in (site/scripts/prepare.mjs); `tools/release/release.py apply`
fills the CHANGELOG's "Unreleased" the same way when it freezes a release, so a
release's notes keep the values it shipped with. tests/test_docs_values.py fails on a
public page that types one of these values instead (a stale copy is how the docs said
Polestar 0.4.2.1 while the data was 0.4.2.2).

Sources: the Polestar tag (tools/polestar-tag, moved by the knowledge base's bump),
the server analysis' timer and the friends' purge delay (their defaults in the code),
the app's version (pyproject.toml, moved by the release), the route times
(data/route-reference.json, rebuilt by route-reference.yml with each Polestar tag),
and the released image (`{{release_image}}`, tag@digest): read from the latest
release's notes on GitHub when the site is built (the command line; release.yml
builds the site right after it publishes), else a placeholder that says where to
copy it from. Standard library only: the site build runs it without the project's packages.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _hours(seconds: float) -> int:
    return round(seconds / 3600)


def values(root: Path = ROOT) -> dict[str, str]:
    tag = (root / "tools" / "polestar-tag").read_text().strip()
    version = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M)[1]
    ref = json.loads((root / "data" / "route-reference.json").read_text())
    runs = [r for r in ref["routes"] if r.get("level_end") == 60]
    totals = [r["total_s"] for r in runs]
    by_class: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        by_class[r["class"]].append(r["total_s"])
    means = {c: statistics.mean(v) for c, v in by_class.items()}
    fastest, slowest = min(means, key=means.get), max(means, key=means.get)
    auto = (root / "wowstats" / "analysis" / "auto.py").read_text()
    tenancy = (root / "wowstats" / "tenancy.py").read_text()
    return {
        "release_image": f"ghcr.io/{_repo(root).split('/')[0]}/wow-mcp:{version}"
                         "@sha256:<digest from the release notes>",
        "polestar_version": tag.removeprefix("v"),
        "polestar_tag": tag,
        "app_version": version,
        "route_runs": str(len(runs)),
        "route_hours_min": str(_hours(min(totals))),
        "route_hours_max": str(_hours(max(totals))),
        "route_hours_median": str(_hours(statistics.median(totals))),
        "route_fastest_class": fastest.title(),
        "route_fastest_hours": str(_hours(means[fastest])),
        "route_slowest_class": slowest.title(),
        "route_slowest_hours": str(_hours(means[slowest])),
        # The server analysis' default timer (ANALYSIS_STALE_PLAYED_HOURS) and a removed
        # friend's grace period (FRIENDS_PURGE_DAYS), as the code defaults them.
        "analysis_hours": re.search(r'"ANALYSIS_STALE_PLAYED_HOURS",\s*"(\d+)"', auto)[1],
        "purge_days": re.search(r"^PURGE_DAYS = (\d+)$", tenancy, re.M)[1],
    }


def _repo(root: Path) -> str:
    """owner/name, from wowstats.SOURCE_URL."""
    return re.search(r'^SOURCE_URL = "https://github\.com/([^"]+)"', (root / "wowstats" / "__init__.py").read_text(),
                     re.M)[1]


# The "### Images" line of a release's notes (release.yml's publish job).
RELEASE_IMAGE = re.compile(r"`(ghcr\.io/[a-z0-9_.-]+/wow-mcp:\d[^`@\s]*@sha256:[0-9a-f]{64})`")


def latest_release_image(root: Path = ROOT, timeout: float = 10) -> str | None:
    """The main image of the latest (non-candidate) release, tag@digest, from its
    notes on GitHub; None when GitHub doesn't answer or the notes carry none."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    req = urllib.request.Request(f"https://api.github.com/repos/{_repo(root)}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - a fixed https address
            body = json.loads(r.read()).get("body") or ""
    except (OSError, ValueError):
        return None
    m = RELEASE_IMAGE.search(body)
    return m[1] if m else None


PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def fill(text: str, vals: dict[str, str]) -> str:
    """`{{name}}` -> its value; an unknown name is an error, never left in a page."""

    def one(m: re.Match) -> str:
        if m[1] not in vals:
            raise KeyError(f"unknown docs value {{{{{m[1]}}}}} (tools/docs_values.py)")
        return vals[m[1]]

    return PLACEHOLDER.sub(one, text)


if __name__ == "__main__":
    out = values()
    # The site build: the released image as GitHub has it now (DOCS_OFFLINE=1: the placeholder).
    if os.environ.get("DOCS_OFFLINE") != "1":
        out["release_image"] = latest_release_image() or out["release_image"]
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")
