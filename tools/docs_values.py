"""The values the public pages quote, from their sources: one place, read at build time.

    uv run --no-project python tools/docs_values.py         # JSON, for site/scripts/prepare.mjs

A page writes `{{name}}` (`{{polestar_version}}`, `{{route_hours_min}}`...) and the docs
site's build fills it in (site/scripts/prepare.mjs); `tools/release/release.py apply`
fills the CHANGELOG's "Unreleased" the same way when it freezes a release, so a
release's notes keep the values it shipped with. tests/test_docs_values.py fails on a
public page that types one of these values instead (a stale copy is how the docs said
Polestar 0.4.2.1 while the data was 0.4.2.2).

Sources: the Polestar tag (tools/polestar-tag, moved by the knowledge base's bump),
the app's version (pyproject.toml, moved by the release), and the route times
(data/route-reference.json, rebuilt by route-reference.yml with each Polestar tag).
Standard library only: the site build runs it without the project's packages.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
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
    return {
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
    }


PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def fill(text: str, vals: dict[str, str]) -> str:
    """`{{name}}` -> its value; an unknown name is an error, never left in a page."""

    def one(m: re.Match) -> str:
        if m[1] not in vals:
            raise KeyError(f"unknown docs value {{{{{m[1]}}}}} (tools/docs_values.py)")
        return vals[m[1]]

    return PLACEHOLDER.sub(one, text)


if __name__ == "__main__":
    json.dump(values(), sys.stdout, indent=1)
    sys.stdout.write("\n")
