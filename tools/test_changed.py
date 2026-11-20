#!/usr/bin/env python3
"""Run only the tests a change can break (`make test-changed`).

A full run was 20-40 minutes on the owner's Mac, mostly the browser suites
(2026-11-20). This diffs against the merge-base with origin/main (committed,
staged, unstaged and untracked files) and maps each path to test files with
the RULES table below. A path no rule knows, or one that can change every
test (conftest, pyproject, the lock), runs the full non-browser suite: never
a silent skip.

Browser tests (files named *browser*, marked `browser` in tests/conftest.py)
run only when a rule names them. Run the whole browser suite once before a
release: `make test-browser`.

    uv run python tools/test_changed.py            # run
    uv run python tools/test_changed.py --list     # show the selection only
    uv run python tools/test_changed.py --paths web/app.css shipper/wow_stats.py
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FULL = "FULL"  # the whole non-browser suite
OWN = "OWN"  # tests/test_<module>*.py plus every test that imports the module
BROWSER = "BROWSER"  # every browser test
NONE = "NONE"  # nothing to run

# First match wins. (regex on the repo-relative path, what it selects): globs
# are relative to the repo root; FULL, OWN, BROWSER and NONE are above.
RULES: list[tuple[str, list[str]]] = [
    # Can change every test: the safe fallback.
    (r"^(tests/conftest\.py|tests/__init__\.py|pyproject\.toml|uv\.lock|Dockerfile|Makefile)$", [FULL]),
    # A test file: itself.
    (r"^tests/.*\.py$", ["SELF"]),
    # Browser-facing code: the pages, their styles and scripts, what renders them.
    (r"^web/model3d/", ["tests/test_*model3d*.py", "tests/test_m3d*.py", "tests/test_3dcases.py"]),
    (r"^web/", [BROWSER]),
    (r"^wowstats/(ui|views|static|pictures)\.py$", [BROWSER, OWN]),
    (r"^wowstats/mcp/helppage\.(css|js|py)$", ["tests/test_help_full_browser.py", "tests/mcp/test_helppage.py"]),
    (r"^site/", ["tests/test_browser.py", "tests/test_links.py"]),
    # The knowledge base: its tests only.
    (r"^(data/kb/|data/kb-reference|tools/kb/)",
     ["tests/test_kb_*.py", "tests/mcp/test_kb_*.py", "tests/analysis/test_kb_offline.py"]),
    # The shipper (the Windows end-to-end test runs in CI only).
    (r"^shipper/", ["tests/test_shipper*.py", "tests/test_recorder_install.py"]),
    # Package code: the module's own tests plus every test importing it.
    (r"^wowstats/.*\.py$", [OWN]),
    # Data files and their exporters.
    (r"^data/", [FULL]),
    (r"^tools/fixtures/", [FULL]),
    (r"^tools/", ["tests/test_audit_deps.py", "tests/test_release.py", "tests/test_workflow_guard.py"]),
    # Docs and repo text: the tests that read them.
    (r"^(docs/|[^/]+\.md$)", ["tests/test_links.py", "tests/test_public_scan.py", "tests/test_license.py",
                              "tests/test_manual.py", "tests/test_privacy_table.py", "tests/test_release.py"]),
    (r"^(\.github/|deploy/)", ["tests/test_workflow_guard.py", "tests/test_deploy_shapes.py", "tests/test_release.py"]),
    (r"^(\.gitignore|\.pre-commit-config\.yaml|\.dockerignore|LICENSE)$", [NONE]),
]


# Half the cores, like a laptop with other agents running (CI uses `-n auto`).
WORKERS = os.environ.get("TEST_WORKERS") or str(max(1, (os.cpu_count() or 2) // 2))
BROWSER_WORKERS = os.environ.get("BROWSER_WORKERS") or str(min(4, int(WORKERS)))


def sh(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def changed_paths(base: str) -> list[str]:
    try:
        mb = sh("git", "merge-base", base, "HEAD").strip()
    except subprocess.CalledProcessError:
        sys.exit(f"no merge-base with {base}: run `git fetch origin main`, or pass --paths")
    out = sh("git", "diff", "--name-only", mb) + sh("git", "ls-files", "--others", "--exclude-standard")
    return sorted({p for p in out.splitlines() if p})


def is_browser(test: str) -> bool:
    return "browser" in Path(test).name


def all_tests() -> list[str]:
    return sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py"))


def importers(path: str) -> list[str]:
    """Test files that import wowstats/<...>/<module>.py (or the package <module>/)."""
    parts = Path(path).with_suffix("").parts[1:]  # drop "wowstats"
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return []
    dotted = ".".join(("wowstats", *parts))
    parent = ".".join(("wowstats", *parts[:-1]))
    pat = re.compile(rf"(\b{re.escape(dotted)}\b|from {re.escape(parent)} import [^\n]*\b{re.escape(parts[-1])}\b)")
    return [t for t in all_tests() if pat.search((ROOT / t).read_text(errors="replace"))]


def globs(pattern: str) -> list[str]:
    return sorted(str(p.relative_to(ROOT)) for p in ROOT.glob(pattern))


def select(paths: list[str]) -> tuple[bool, set[str], list[str]]:
    """(full non-browser run?, test files, why per path)."""
    full, tests, why = False, set(), []
    for path in paths:
        # An unknown path runs the full suite.
        actions = next((acts for rx, acts in RULES if re.search(rx, path)), [FULL])
        picked: set[str] = set()
        for a in actions:
            if a == FULL:
                full = True
            elif a == "SELF":
                picked.add(path)
            elif a == BROWSER:
                picked |= {t for t in all_tests() if is_browser(t)}
            elif a == OWN:
                mod = Path(path).stem
                picked |= set(globs(f"tests/test_{mod}*.py")) | set(importers(path))
            elif a != NONE:
                picked |= set(globs(a))
        tests |= picked
        why.append(f"{path} -> " + ("FULL non-browser suite" if FULL in actions else f"{len(picked)} test files"))
    return full, tests, why


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--paths", nargs="+", help="pretend these are the changed paths")
    ap.add_argument("--list", action="store_true", help="print the selection, run nothing")
    args = ap.parse_args()

    paths = args.paths or changed_paths(args.base)
    if not paths:
        print("no changes against", args.base)
        return 0
    full, tests, why = select(paths)
    print("\n".join(why))
    fast = sorted(t for t in tests if not is_browser(t))
    browser = sorted(t for t in tests if is_browser(t))
    print(f"non-browser: {'the whole suite' if full else f'{len(fast)} files'}; browser: {len(browser)} files")
    for t in (*fast, *browser):
        print("  ", t)
    if args.list:
        return 0

    rc = 0
    if full or fast:
        # pyproject's addopts keep browser and ci_only tests out of this run.
        cmd = ["uv", "run", "pytest", "-q", "-rs", "-n", WORKERS, *([] if full else fast)]
        rc |= subprocess.run(cmd, cwd=ROOT).returncode
    if browser:
        # A whole file per worker: each file shares one server and one browser per engine.
        cmd = ["uv", "run", "pytest", "-q", "-rs", "-m", "browser", "-n", BROWSER_WORKERS,
               "--dist", "loadfile", *browser]
        rc |= subprocess.run(cmd, cwd=ROOT).returncode
    return rc


if __name__ == "__main__":
    sys.exit(main())
