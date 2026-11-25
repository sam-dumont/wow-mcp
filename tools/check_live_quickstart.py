"""Checks the PUBLISHED quick start: the command a reader copies names the released image.

    uv run --no-project python tools/check_live_quickstart.py [--version 0.11.0] [--wait 900]

Reads the live pages (the quick start and "Try it at home", both of which hand out
`WOW_STATS_IMAGE=...`), and passes only when every such value there is
`ghcr.io/<owner>/wow-mcp:<version>@sha256:<64 hex>` and equals the image line of
release v<version>'s notes (tools/docs_values.release_image). pages.yml runs it after
each deploy; GitHub Pages caches a page up to 10 minutes, so it retries until --wait
runs out. Why it exists: 0.11.0's published quick start said
`...@sha256:<digest from the release notes>` for hours and every check was green
(external review, 2026-11-25). Standard library only.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import docs_values  # noqa: E402

PAGES = ("quick-start/", "self-host/try-at-home/")
# The value a reader copies: up to a quote, a space, a backtick or the next tag.
IMAGE_VALUE = re.compile(r"WOW_STATS_IMAGE=([^\s'\"`<]+)")
REAL = re.compile(r"ghcr\.io/[a-z0-9_.-]+/wow-mcp:[^@\s]+@sha256:[0-9a-f]{64}")


def site_url() -> str:
    """https://<owner>.github.io/<repo>/, from wowstats.SOURCE_URL."""
    owner, repo = docs_values._repo(docs_values.ROOT).split("/")
    return f"https://{owner.lower()}.github.io/{repo}/"


def problems(page: str, expected: str) -> list[str]:
    """What's wrong with the image a page hands out ([] when it is `expected`)."""
    found = sorted(set(IMAGE_VALUE.findall(html.unescape(page))))
    if not found:
        return ["no WOW_STATS_IMAGE= on the page"]
    out = [f"{v} isn't a tag@sha256 digest" for v in found if not REAL.fullmatch(v)]
    out += [f"{v} isn't the release's {expected}" for v in found if REAL.fullmatch(v) and v != expected]
    return out


def fetch(url: str) -> str:
    # A query string of its own: the CDN keys on it, so each try reads the page as deployed now.
    req = urllib.request.Request(f"{url}?check={int(time.time())}", headers={"User-Agent": "wow-mcp-docs-check"})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - our own https site
        return r.read().decode("utf-8", "replace")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", default=docs_values.values()["app_version"])
    ap.add_argument("--site", default=site_url())
    ap.add_argument("--wait", type=float, default=0, help="seconds to keep retrying a failing page")
    a = ap.parse_args(argv)
    expected = docs_values.release_image(a.version)
    deadline = time.time() + a.wait
    while True:
        bad = {}
        for p in PAGES:
            try:
                why = problems(fetch(a.site + p), expected)
            except OSError as e:
                why = [str(e)]
            if why:
                bad[p] = why
        if not bad:
            print(f"live docs hand out {expected} on {', '.join(PAGES)}")
            return 0
        if time.time() >= deadline:
            for p, why in bad.items():
                print(f"{a.site}{p}: {'; '.join(why)}", file=sys.stderr)
            return 1
        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
