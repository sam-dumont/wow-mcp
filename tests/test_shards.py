"""CI's pytest parts (tests/conftest.py --shard K/N): whole files, split by
their measured time so no part runs much longer than the others (#235: the
hash of the path gave one part 17 minutes and another 5)."""

from __future__ import annotations

import json

from tests import conftest


def test_the_parts_are_balanced_by_measured_time(tmp_path, monkeypatch):
    table = tmp_path / "shard_times.json"
    table.write_text(json.dumps({"tests/a.py": 700, "tests/b.py": 300, "tests/c.py": 300, "tests/d.py": 100}))
    monkeypatch.setattr(conftest, "SHARD_TIMES", table)
    part = conftest._shards({"tests/a.py", "tests/b.py", "tests/c.py", "tests/d.py"}, 2)
    assert part == {"tests/a.py": 1, "tests/b.py": 2, "tests/c.py": 2, "tests/d.py": 2}


def test_a_new_file_weighs_the_median_and_browser_files_split_apart(tmp_path, monkeypatch):
    table = tmp_path / "shard_times.json"
    table.write_text(json.dumps({"tests/a.py": 10, "tests/b.py": 10, "tests/c.py": 1}))
    monkeypatch.setattr(conftest, "SHARD_TIMES", table)
    part = conftest._shards({"tests/a.py", "tests/b.py", "tests/new.py", "tests/x_browser.py",
                             "tests/y_browser.py"}, 2)
    assert part["tests/new.py"] != part["tests/a.py"] or part["tests/new.py"] != part["tests/b.py"]
    assert {part["tests/x_browser.py"], part["tests/y_browser.py"]} == {1, 2}


def test_the_table_lists_real_files():
    """A renamed or deleted file left in the table only costs balance, but a
    table of nothing but stale names would split by the median alone."""
    names = json.loads(conftest.SHARD_TIMES.read_text())
    real = [n for n in names if (conftest.ROOT / n).exists()]
    assert len(real) >= 0.9 * len(names)
