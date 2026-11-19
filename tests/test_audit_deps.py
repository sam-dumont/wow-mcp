"""tools/audit_deps.py: the allowlist takes nothing without a reason."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_deps", ROOT / "tools/audit_deps.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_committed_allowlist_is_valid():
    audit.allowed()


def test_entries_need_an_id_and_a_reason(tmp_path):
    f = tmp_path / "a.toml"
    f.write_text('[[ignore]]\nid = "GHSA-aaaa-bbbb-cccc"\nreason = "Only the --foo CLI path; never called here."\n')
    assert audit.allowed(f) == ["GHSA-aaaa-bbbb-cccc"]
    f.write_text('[[ignore]]\nid = "GHSA-aaaa-bbbb-cccc"\nreason = "n/a"\n')
    with pytest.raises(ValueError, match="needs a reason"):
        audit.allowed(f)
    f.write_text('[[ignore]]\nreason = "Only the --foo CLI path; never called here."\n')
    with pytest.raises(ValueError, match="no id"):
        audit.allowed(f)


def test_no_file_is_an_empty_list(tmp_path):
    assert audit.allowed(tmp_path / "missing.toml") == []
