"""The refresh job stays with its PR until it merges (tools/kb/land.py
shepherd): auto-merge on, the branch rebased on main whenever main moves
(main's ruleset wants it up to date, so a BEHIND PR would wait forever),
and the run ends red when a required check fails or the PR doesn't merge,
so the lab's scheduler reports it. 2026-11-22: #99 sat red for hours with
nobody watching."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------- main moved under a watch PR (2026-11-22)
# #140, Polestar v0.4.2.2, was refused: semantic wave 1 had regenerated the
# same plan inputs while the run worked, and the rebase conflicted. A watch
# change is deterministic (tools/kb/versions.sh), so a conflict is `moved`:
# nothing pushed, the workflow makes it again on the current main.


def test_a_conflict_is_moved_when_the_change_can_be_made_again():
    assert PR([{"mergeStateStatus": "DIRTY"}]).go()[0] == "red"
    pr = PR([{"mergeStateStatus": "DIRTY"}])
    outcome = land.shepherd(7, REQUIRED, deadline_s=600, poll_s=60, view=pr.view, update=pr.update, arm=pr.arm,
                            sleep=pr.sleep, now=pr.now, regen=True)
    assert outcome[0] == "moved"


def test_moved_is_green_until_the_last_try():
    assert land.exit_code("moved", last=False) == 0
    assert land.exit_code("moved", last=True) == 1
    assert land.exit_code("moved") == 1


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def moved_main(tmp_path, monkeypatch):
    """An origin whose main moved after the run's base, with a change to the
    same file as the run's commit; the run's clone at its commit; gh faked."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    for k, v in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
        _git(work, "config", k, v)
    (work / "plan-inputs.json").write_text('{"48": 9388}\n')
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "base")
    _git(work, "push", "-q", "origin", "HEAD:main")
    base = _git(work, "rev-parse", "HEAD")
    # main moves: another PR regenerates the same fixture
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    for k, v in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
        _git(other, "config", k, v)
    (other / "plan-inputs.json").write_text('{"48": 9390}\n')
    _git(other, "commit", "-qam", "semantic wave 1")
    _git(other, "push", "-q", "origin", "HEAD:main")
    # the run's commit: the bump, on the old base
    _git(work, "switch", "-qc", "kb-watch")
    (work / "plan-inputs.json").write_text('{"48": 9381}\n')
    _git(work, "commit", "-qam", "Polestar v0.4.2.2: the tag, the exports and the test data")
    gates = tmp_path / "gates.json"
    gates.write_text(json.dumps({"ok": True}))
    calls = []

    def fake_gh(*args, check=True, stdin=None):
        calls.append(args)
        out = {("pr", "list"): "[]", ("pr", "create"): "https://github.com/o/r/pull/9"}.get(args[:2], "")
        return subprocess.CompletedProcess(["gh", *args], 0, out, "")

    real_sh = land.sh

    def fake_sh(*cmd, check=True, env=None):
        if cmd[0] == "gh":
            calls.append(cmd[1:])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_sh(*cmd, check=check, env=env)

    monkeypatch.setattr(land, "ROOT", work)
    monkeypatch.setattr(land, "gh", fake_gh)
    monkeypatch.setattr(land, "sh", fake_sh)
    out = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    return {"base": base, "gates": gates, "calls": calls, "out": out, "origin": origin, "work": work,
            "why": tmp_path / "why.json"}


def _land(m, *extra):
    return land.main(["--mode", "watch", "--branch", "kb-watch", "--base", m["base"], "--title", "KB watch",
                      "--gates", str(m["gates"]), "--kinds", "polestar", "--why-out", str(m["why"]), *extra])


def test_a_watch_change_that_conflicts_with_a_moved_main_is_made_again_not_refused(moved_main):
    m = moved_main
    assert _land(m, "--regen") == 0
    assert "outcome=moved" in m["out"].read_text()
    # nothing pushed, no PR opened or labelled kb-refused: the workflow makes it again on main
    assert not _git(m["origin"], "branch", "--list", "kb-watch")
    assert not any(c[:2] in (("pr", "create"), ("pr", "edit")) for c in m["calls"])
    assert not m["why"].exists()
    # the rebase was aborted: the work tree is clean, on the run's commit
    assert _git(m["work"], "status", "--porcelain") == ""


def test_the_last_try_is_red_and_says_why(moved_main):
    m = moved_main
    assert _land(m, "--regen", "--last") == 1
    assert "main kept moving" in json.loads(m["why"].read_text())["polestar"]
    assert not any(c[:2] == ("pr", "create") for c in m["calls"])


def test_without_regen_a_conflict_still_refuses(moved_main):
    """The model's modes (review, refresh) can't be made again for free."""
    m = moved_main
    assert _land(m) == 1
    assert any(c[:2] == ("pr", "create") for c in m["calls"])
    assert any(c[:3] == ("pr", "edit", "9") and "kb-refused" in c for c in m["calls"])


def test_the_lane_made_again_on_main_never_holds_the_apps_token():
    """The versions lane runs Polestar's code: where it runs again (`land`
    when main moves under its PR, `reconcile` for an earlier run's PR), its
    step gets the mirror's read-only token, never the App's write token."""
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "kb-refresh.yaml").read_text())["jobs"]
    for job, n in (("land", 2), ("reconcile", 1)):
        lane = [s for s in jobs[job]["steps"] if "tools/kb/reconcile.py --remake" in s.get("run", "")]
        assert len(lane) == n, job
        for s in lane:
            assert "steps.token" not in json.dumps(s), s["name"]
    steps = jobs["land"]["steps"]
    lands = [s for s in steps if "tools/kb/land.py --mode" in s.get("run", "")]
    assert len(lands) == 3 and "--last" in lands[-1]["run"]
    assert all("--last" not in s["run"] for s in lands[:-1])


def test_a_rebase_done_in_its_own_step_lands_without_fetching_or_gating_again(moved_main, tmp_path):
    """The rebase and its gates run before the App's token is minted
    (--rebase-out); the landing step reads the result (--prepared) and only
    pushes, opens the PR and stays with it, well inside the token's hour.
    2026-11-24: the push after an hour of gates failed on an expired token."""
    m = moved_main
    prep = tmp_path / "prep.json"
    assert land.main(["--branch", "kb-watch", "--base", m["base"], "--rebase-out", str(prep)]) == 0
    assert json.loads(prep.read_text())["state"] == "conflict"
    assert m["calls"] == []  # no GitHub call: this step holds no token
    assert _land(m, "--prepared", str(prep)) == 1  # without --regen a conflict refuses
    assert any(c[:2] == ("pr", "create") for c in m["calls"])


def test_every_app_token_is_minted_right_before_the_steps_that_use_it():
    """An installation token lasts an hour. No step that can take that long
    (the gates, the lane made again) sits between a token's minting and a
    step that uses it, and no step holding the token runs the gates itself
    (land.py gets --prepared). 2026-11-24 and 11-25: kb-refresh's push and
    issues failed on a token minted before an hour or more of gates."""
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "kb-refresh.yaml").read_text())["jobs"]
    long = ("tools/kb/gates.py", "reconcile.py --remake", "--rebase-out")
    for job in ("reconcile", "land"):
        steps = jobs[job]["steps"]
        minted = {}
        for i, s in enumerate(steps):
            if "create-github-app-token" in s.get("uses", ""):
                minted[s["id"]] = i
                continue
            text = json.dumps(s)
            for tid, at in minted.items():
                if f"steps.{tid}.outputs.token" in text:
                    between = [x.get("name") for x in steps[at + 1:i] if any(k in x.get("run", "") for k in long)]
                    assert not between, (job, s.get("name"), tid, between)
            if "tools/kb/land.py --mode" in s.get("run", ""):
                assert "--prepared" in s["run"], (job, s.get("name"))
