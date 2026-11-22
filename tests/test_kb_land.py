"""The refresh job stays with its PR until it merges (tools/kb/land.py
shepherd): auto-merge on, the branch rebased on main whenever main moves
(main's ruleset wants it up to date, so a BEHIND PR would wait forever),
and the run ends red when a required check fails or the PR doesn't merge,
so the lab's scheduler reports it. 2026-11-22: #99 sat red for hours with
nobody watching."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("kbland_land", ROOT / "tools" / "kb" / "land.py")
land = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = land
spec.loader.exec_module(land)

REQUIRED = {"test", "windows", "image"}


class PR:
    """A PR whose views come from a script, one per poll."""

    def __init__(self, views: list[dict]):
        self.views, self.calls, self.t = views, [], 0.0

    def view(self, n):
        v = self.views.pop(0) if len(self.views) > 1 else self.views[0]
        return {"state": "OPEN", "mergeStateStatus": "BLOCKED", "autoMergeRequest": {"m": 1},
                "statusCheckRollup": [], **v}

    def update(self, n):
        self.calls.append("update")

    def arm(self, n):
        self.calls.append("arm")

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t

    def go(self, deadline=600):
        return land.shepherd(7, REQUIRED, deadline_s=deadline, poll_s=60, view=self.view, update=self.update,
                             arm=self.arm, sleep=self.sleep, now=self.now)


def check(name, conclusion="", status="COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def test_a_behind_pr_is_rebased_and_merges():
    pr = PR([{"mergeStateStatus": "BEHIND"}, {"mergeStateStatus": "BLOCKED"}, {"state": "MERGED"}])
    assert pr.go() == ("merged", "")
    assert pr.calls == ["update", "arm"]


def test_auto_merge_is_turned_back_on_when_it_is_off():
    pr = PR([{"autoMergeRequest": None}, {"state": "MERGED"}])
    assert pr.go()[0] == "merged" and pr.calls == ["arm"]


def test_a_failed_required_check_is_red():
    pr = PR([{"statusCheckRollup": [check("test", "FAILURE"), check("windows", status="IN_PROGRESS")]}])
    outcome, detail = pr.go()
    assert outcome == "red" and "test" in detail


def test_a_failed_check_main_doesnt_require_waits_for_the_merge():
    pr = PR([{"statusCheckRollup": [check("scorecard", "FAILURE")]}, {"state": "MERGED"}])
    assert pr.go()[0] == "merged"


def test_a_conflict_or_a_close_is_red():
    assert PR([{"mergeStateStatus": "DIRTY"}]).go()[0] == "red"
    assert PR([{"state": "CLOSED"}]).go()[0] == "red"


def test_a_pr_that_never_merges_is_stuck_at_the_deadline():
    outcome, detail = PR([{}]).go(deadline=300)
    assert outcome == "stuck" and "5 minutes" in detail


def test_the_run_is_red_unless_the_pr_merged():
    assert [land.exit_code(o) for o in ("merged", "nothing", "refused", "red", "stuck", "failed")] == [0, 0, 1, 1, 1, 1]
