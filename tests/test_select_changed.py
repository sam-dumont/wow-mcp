"""tools/test_changed.py picks the tests a change can break.

A snapshot test imports `ui` and runs `dev/seed.py`, and reaches the analysis
modules only through them: a change there (PRs #110 to #112) skipped
tests/test_layer_pages.py and CI caught the stale snapshot. The selection
follows the import graph, so a module's tests are every test that reaches it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("test_changed_tool", ROOT / "tools" / "test_changed.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def picked(*paths: str) -> set[str]:
    return tool.select(list(paths))[1]


def test_an_analysis_module_selects_the_snapshot_that_reaches_it():
    assert "tests/test_layer_pages.py" in picked("wowstats/analysis/semantic.py")
    assert "tests/test_layer_pages.py" in picked("wowstats/analysis/duck.py")


def test_the_seed_script_selects_the_snapshot_it_feeds():
    assert "tests/test_layer_pages.py" in picked("dev/seed.py")


def test_a_docs_only_change_skips_the_slow_suites():
    tests = picked("docs/development.md")
    assert "tests/test_layer_pages.py" not in tests
    assert not any("browser" in t for t in tests)
    assert not tool.select(["docs/development.md"])[0]
