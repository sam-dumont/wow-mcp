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
and the released image (`{{release_image}}`, tag@digest): read from the notes of the
GitHub release of the tree's own version (v<app_version>) when the site is built.

A published build (DOCS_PUBLISH=1, set by pages.yml for the build it deploys) gets
the real image or fails: it never falls back to a placeholder. The fallback used to
apply to every build, and 0.11.0's published quick start said
`...wow-mcp:0.11.0@sha256:<digest from the release notes>` (external review,
2026-11-25): the site build asked GitHub's API without a token, from a shared
runner address, and a refused or failed call was silently the placeholder. A
preview or local build (the pull request's artifact, `npm run build` at home) still
gets the placeholder, with a warning, since nobody installs from it. Standard
library only: the site build runs it without the project's packages.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
import urllib.error
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
        # A preview's stand-in; a published build replaces it (release_image) or fails.
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
RELEASE_IMAGE = re.compile(r"`(ghcr\.io/[a-z0-9_.-]+/wow-mcp:(\d[^`@\s]*)@sha256:[0-9a-f]{64})`")


class ReleaseImageError(RuntimeError):
    """The release of a version has no image line, or GitHub didn't say."""


def release_image(version: str, root: Path = ROOT, tries: int = 4, timeout: float = 10, wait: float = 5) -> str:
    """The main image of release v<version>, tag@digest, from its notes on GitHub.
    Retries a failed or refused call (a shared runner's address runs out of
    anonymous calls; GH_TOKEN or GITHUB_TOKEN lifts that); raises
    ReleaseImageError when there is no such release, its notes carry no image
    of that version, or GitHub never answered."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    url = f"https://api.github.com/repos/{_repo(root)}/releases/tags/v{version}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               **({"Authorization": f"Bearer {token}"} if token else {})})
    last = ""
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - a fixed https address
                body = json.loads(r.read()).get("body") or ""
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise ReleaseImageError(f"no GitHub release v{version} ({url})") from e
            last = f"HTTP {e.code}"
        except (OSError, ValueError) as e:
            last = str(e)
        if i + 1 < tries:
            time.sleep(wait * 2 ** i)
    else:
        raise ReleaseImageError(f"GitHub didn't answer for v{version} after {tries} tries: {last}")
    found = image_in_notes(body, version)
    if found is None:
        raise ReleaseImageError(f"release v{version}'s notes carry no wow-mcp:{version}@sha256 image line")
    return found


def image_in_notes(body: str, version: str) -> str | None:
    """The `wow-mcp:<version>@sha256:...` line of a release's notes (not the renderer's)."""
    return next((m[1] for m in RELEASE_IMAGE.finditer(body) if m[2] == version), None)


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
    if os.environ.get("DOCS_PUBLISH") == "1":
        try:
            out["release_image"] = release_image(out["app_version"])
        except ReleaseImageError as e:
            sys.exit(f"docs_values: a published build needs the released image: {e}")
    elif os.environ.get("DOCS_OFFLINE") != "1":
        try:
            out["release_image"] = release_image(out["app_version"], tries=1)
        except ReleaseImageError as e:
            print(f"docs_values: preview build, the pages say {out['release_image']}: {e}", file=sys.stderr)
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")
