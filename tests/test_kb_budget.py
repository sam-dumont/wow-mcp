"""The knowledge base's size budget (data/kb/budget.json): every file and the
whole of data/kb stay under their caps, so the KB is consolidated (merged,
shortened, superseded, expired: tools/kb/lifecycle.py) instead of grown. The
weekly job keeps one issue open while it is over."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "kb"))

import lifecycle  # noqa: E402

DATA = ROOT / "data" / "kb"
BUDGET = json.loads((DATA / "budget.json").read_text(encoding="utf-8"))


def test_every_kb_file_and_the_total_are_under_budget():
    assert lifecycle.over_budget(DATA, BUDGET) == []


def test_the_budget_is_well_formed():
    assert set(BUDGET) >= {"about", "total", "default", "files"}
    assert all(isinstance(n, int) and n > 0 for n in [BUDGET["total"], BUDGET["default"], *BUDGET["files"].values()])
    # A cap for a file that's gone is a cap nobody checks.
    gone = [f for f in BUDGET["files"] if not (DATA / f).exists() and f not in ("digest-history.json", "sweep.json")]
    assert gone == []


def test_over_budget_names_the_file_and_the_total(tmp_path):
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "dungeons.json").write_text("x" * 300)
    (tmp_path / "new.json").write_text("x" * 50)
    over = lifecycle.over_budget(tmp_path, {"total": 320, "default": 40, "files": {"research/dungeons.json": 200}})
    assert over == ["`new.json`: 50 bytes, budget 40 (default)", "`research/dungeons.json`: 300 bytes, budget 200",
                    "total: 350 bytes, budget 320"]
    assert "Consolidate" in lifecycle.budget_body(over) and lifecycle.budget_body([]) is None
