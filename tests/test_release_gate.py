"""The browser suite is a release gate and nothing else (owner, 2026-11-21):
release.yml runs it before anything is signed, merged, pushed or tagged; no
other workflow runs browsers (owner, 2026-11-19: an install hung CI for 30
minutes), and every step that can hang has a timeout."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def jobs(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())["jobs"]


def test_release_runs_the_browser_suite_before_publishing():
    release = jobs("release.yml")
    gate = release["browser"]
    assert "github.event_name != 'pull_request'" in gate["if"]
    assert gate["runs-on"].startswith("ubuntu-"), "hosted runners only"
    assert 0 < gate["timeout-minutes"] <= 60
    steps = gate["steps"]
    run = " ".join(s.get("run", "") for s in steps)
    assert "playwright install chromium webkit" in run
    assert "-m browser" in run
    assert gate["env"]["WOW_BROWSER_GATE"] == "1"
    for s in steps:
        if "playwright install" in s.get("run", "") or "-m browser" in s.get("run", ""):
            assert s.get("timeout-minutes"), s
    cache = next(s for s in steps if s.get("uses", "").startswith("actions/cache@"))
    assert "steps.pw.outputs.version" in cache["with"]["key"]
    for job in ("sign", "prepare", "build"):
        assert "browser" in release[job]["needs"], job
    assert "needs.browser.result == 'success'" in release["build"]["if"]


def test_no_other_workflow_runs_browsers():
    for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
        if path.name in {"release.yml", "pages.yml"}:  # pages: the demo's one Chromium check
            continue
        for name, job in jobs(path.name).items():
            run = " ".join(s.get("run", "") for s in job.get("steps") or []).lower()
            assert "webkit" not in run and "-m browser" not in run, f"{path.name}: {name}"
            assert "release.yml" not in str(job.get("uses", "")), f"{path.name}: {name}"
