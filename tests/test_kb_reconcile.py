"""Every refresh run starts by reconciling what earlier runs left open
(tools/kb/reconcile.py), whatever its mode: a stale PR is rebased or made
again with its own kinds, and an issue whose condition holds on main closes.
2026-11-25: #249 sat CONFLICTING until a person rebased it, and #212 and #171
stayed open a day after their PRs merged: only the same mode's next run
looked again, and only the run that opened a PR could make it again."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools" / "kb"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"kbrec_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


reconcile, watch, land = load("reconcile"), load("watch"), load("land")
REQUIRED = {"test", "windows", "image"}


@pytest.fixture
def state():
    return json.loads((ROOT / "data" / "kb" / "watch.json").read_text())


def later(tag: str, n: int = 1) -> str:
    m = list(re.finditer(r"\d+", tag))[-1]
    return f"{tag[:m.start()]}{int(m.group()) + n}{tag[m.end():]}"


def questie(state) -> dict:
    return next(a for a in state["addons"] if a.get("repo"))


class GH:
    """Open PRs and issues in memory; every write is a call."""

    def __init__(self, prs=(), views=None, issues=(), states=None, merged=None):
        self._prs, self.views, self._issues = list(prs), views or {}, list(issues)
        self.states, self.merged, self.calls = states or {}, merged or {}, []

    def prs(self):
        return [dict(p) for p in self._prs]

    def view(self, n):
        return {"state": "OPEN", "mergeStateStatus": "BLOCKED", "autoMergeRequest": {"m": 1},
                "statusCheckRollup": [], **self.views.get(n, {})}

    def update(self, n):
        self.calls.append(("update", n))

    def arm(self, n):
        self.calls.append(("arm", n))

    def pr_state(self, n):
        return self.states.get(n, "OPEN")

    def merged_since(self, branch, since):
        return next((n for n, (b, at) in self.merged.items() if b == branch and at > since), None)

    def issues(self):
        return list(self._issues)

    def close(self, n, why):
        self.calls.append(("close", n, why))


def pr(n, branch, *labels):
    return {"number": n, "headRefName": branch, "labels": [{"name": x} for x in labels], "title": f"PR {n}"}


def clock():
    t = [0.0]
    return (lambda s: t.__setitem__(0, t[0] + s)), (lambda: t[0])


# ---------------------------------------------------------------- a stale watch PR, made again with its own kinds


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def stale_watch_pr(tmp_path, state):
    """origin: main with watch.json; branch kb-watch, yesterday's PR, carrying
    a client build, an addon release and a Polestar tag; then main moved (a
    review run accepted official pages). The reconcile job's clone is at main."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    for k, v in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
        _git(work, "config", k, v)
    f = work / "data" / "kb" / "watch.json"
    f.parent.mkdir(parents=True)

    def commit(s, msg):
        f.write_text(json.dumps(s, indent=2) + "\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-qm", msg)

    commit(state, "base")
    _git(work, "push", "-q", "origin", "HEAD:main")
    pr_state = copy.deepcopy(state)
    pr_state["client_build"]["known"] = later(state["client_build"]["known"])
    questie(pr_state)["known"] = later(questie(state)["known"])
    pr_state["polestar"]["known"] = "v9.9.9"
    _git(work, "switch", "-qc", "kb-watch")
    commit(pr_state, "Addon releases; Client build; Polestar v9.9.9")
    _git(work, "push", "-q", "origin", "HEAD:kb-watch")
    _git(work, "switch", "-q", "main")
    moved = copy.deepcopy(state)
    moved["official"][0]["known"] = "a later hash"
    commit(moved, "KB review: official pages")
    _git(work, "push", "-q", "origin", "HEAD:main")
    _git(work, "fetch", "-q", "origin")
    return {"work": work, "origin": origin}


def test_a_stale_dirty_watch_pr_is_made_again_with_its_kinds_by_a_later_run_of_another_mode(
        stale_watch_pr, monkeypatch, tmp_path):
    # A review run starts: reconcile takes no mode, and runs before every mode's work.
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "kb-refresh.yaml").read_text())["jobs"]
    assert "inputs.mode" not in json.dumps(jobs["reconcile"])
    assert jobs["run"]["needs"] == "reconcile" and "always()" in jobs["run"]["if"]

    gh = GH(prs=[pr(249, "kb-watch", "kb-watch")], views={249: {"mergeStateStatus": "DIRTY"}})
    got = reconcile.triage(gh, REQUIRED)
    assert got["remake"]["number"] == 249 and got["waiting"] == [] and gh.calls == []

    m = stale_watch_pr
    monkeypatch.setattr(reconcile, "ROOT", m["work"])
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    main = _git(m["work"], "rev-parse", "origin/main")
    seen = {}

    def run(cmd, cwd, env):
        seen.update(cmd=cmd, kinds=env["KINDS"], tag=env["TAG"], gates_base=env["GATES_BASE"],
                    head=_git(cwd, "rev-parse", "HEAD"))
        return subprocess.CompletedProcess(cmd, 0)

    assert reconcile.remake("origin/kb-watch", run=run) == 0
    # The PR's own kinds, read from its branch; the review's official pages aren't one of them.
    assert seen["kinds"] == "client-build polestar addons" and seen["tag"] == "v9.9.9"
    # versions.sh runs on the current main, and gates against it.
    assert seen["head"] == main == seen["gates_base"]
    assert seen["cmd"] == ["bash", "tools/kb/versions.sh"]
    assert f"base={main}" in (tmp_path / "out").read_text()


def test_the_kinds_are_what_the_branch_changed_never_what_main_moved(state):
    moved = copy.deepcopy(state)
    moved["foreverdb"]["known"] = "another fingerprint"
    assert reconcile.changed_kinds(state, state) == {"kinds": [], "tag": "", "data": ""}
    assert reconcile.changed_kinds(state, moved) == {"kinds": ["foreverdb"], "tag": "", "data": ""}
    bad = copy.deepcopy(state)
    bad["polestar"]["known"] = "v1.0; rm -rf /"
    assert reconcile.changed_kinds(state, bad) == {"kinds": ["polestar"], "tag": "", "data": ""}


def test_open_prs_are_rebased_rearmed_or_left_to_a_person():
    gh = GH(prs=[pr(1, "kb-watch"), pr(2, "kb-review"), pr(3, "kb-sweep", "kb-refused"), pr(4, "kb-review"),
                 pr(5, "kb-watch")],
            views={1: {"mergeStateStatus": "BEHIND"}, 2: {"mergeStateStatus": "DIRTY"},
                   4: {"statusCheckRollup": [{"name": "test", "conclusion": "FAILURE"}]},
                   5: {"autoMergeRequest": None}})
    got = reconcile.triage(gh, REQUIRED)
    assert gh.calls == [("update", 1), ("arm", 1), ("arm", 5)]
    assert got["remake"] is None
    assert [w.split(" ")[1] for w in got["waiting"]] == ["#2", "#3", "#4"]


# ---------------------------------------------------------------- issues main settled


def change_issue(n, state, kind, now):
    body = watch.report(state, now, watch.known(state), {kind: "PR #211 is open, not merged."}, [kind])[kind]
    return {"number": n, "body": f"<!-- kb-watch:{kind} -->\n{body}", "updatedAt": "2026-11-24T06:23:20Z"}


def test_issues_close_when_main_matches_with_a_one_line_why(state):
    now = watch.known(state)
    q = questie(state)["name"]
    now["addons"][q] = later(questie(state)["known"])
    addons = change_issue(212, state, "addons", now)  # #212: an addon release, its PR merged since
    ps = watch.known(state)
    ps["polestar"]["release"] = "v9.9.9"
    polestar = change_issue(300, state, "polestar", ps)  # main doesn't know it yet: stays
    review = {"number": 171, "updatedAt": "2026-11-23T12:30:00Z",
              "body": "<!-- kb-watch:refused-review -->\nPR #189 is open, not merged: the gates refused it."}
    watch_refused = {"number": 172, "updatedAt": "2026-11-23T12:30:00Z",
                     "body": "<!-- kb-watch:refused-sweep -->\nThe sweep run failed before it could land anything."}
    failed_watch = {"number": 173, "updatedAt": "2026-11-23T12:30:00Z",
                    "body": "<!-- kb-watch:refused-refresh -->\nPR #190 is open, not merged: the gates refused it."}
    sweep = {"number": 174, "updatedAt": "2026-11-01T00:00:00Z", "body": "<!-- kb-watch:sweep -->\nGone (3)"}
    budget = {"number": 175, "updatedAt": "2026-11-01T00:00:00Z", "body": "<!-- kb-watch:budget -->\nOver"}
    gh = GH(issues=[addons, polestar, review, watch_refused, failed_watch, sweep, budget],
            states={189: "MERGED"}, merged={180: ("kb-sweep", "2026-11-24T00:00:00Z")})

    main = copy.deepcopy(state)
    questie(main)["known"] = later(questie(state)["known"], 2)  # main moved past the issue's release
    reconcile.close_settled(gh, main, open_branches={"kb-refresh"}, over=[])
    closed = {c[1]: c[2] for c in gh.calls if c[0] == "close"}
    assert set(closed) == {212, 171, 172, 175}
    assert q in closed[212] and "at or past" in closed[212]
    assert "PR #189 merged" in closed[171] and "PR #180" in closed[172]
    assert all("\n" not in why for why in closed.values())


def test_an_addon_that_moved_back_settles_when_main_knows_the_older_release(state):
    a = questie(state)
    now = watch.known(state)
    now["addons"][a["name"]] = "v0.0.1"
    body = change_issue(1, state, "addons", now)["body"]
    assert reconcile.change_settled("addons", body, watch.known(state)) is None
    main = copy.deepcopy(state)
    questie(main)["known"] = "v0.0.1"
    assert reconcile.change_settled("addons", body, watch.known(main))


# ---------------------------------------------------------------- nothing stale, nothing done


def test_nothing_is_done_when_nothing_is_stale(state):
    pending = watch.known(state)
    pending["client-build"]["build"] = later(state["client_build"]["known"])
    issues = [change_issue(1, state, "client-build", pending),
              {"number": 2, "updatedAt": "2026-11-25T00:00:00Z",
               "body": "<!-- kb-watch:refused-watch -->\nPR #9 is open, not merged: the gates refused it."},
              {"number": 3, "updatedAt": "2026-11-25T00:00:00Z", "body": "<!-- kb-watch:budget -->\nOver"}]
    gh = GH(prs=[pr(9, "kb-watch")], issues=issues)  # up to date, auto-merge on, checks running
    assert reconcile.triage(gh, REQUIRED) == {"remake": None, "waiting": []}
    assert gh.calls == []
    sleep, now = clock()
    waiting = reconcile.settle(gh, REQUIRED, lambda: state, lambda: ["total: over"], deadline_s=120,
                               sleep=sleep, now=now)
    assert gh.calls == [] and waiting and "kb-watch" in waiting[0]

    quiet = GH()
    assert reconcile.triage(quiet, REQUIRED) == {"remake": None, "waiting": []}
    assert reconcile.settle(quiet, REQUIRED, lambda: state, lambda: [], deadline_s=60) == []
    assert quiet.calls == []


def test_settle_stays_with_the_prs_then_closes_what_merged(state):
    gh = GH(prs=[pr(7, "kb-review")], views={7: {"state": "MERGED"}},
            issues=[{"number": 8, "updatedAt": "2026-11-23T12:30:00Z",
                     "body": "<!-- kb-watch:refused-review -->\nPR #7 is open, not merged."}],
            states={7: "MERGED"})
    sleep, now = clock()
    assert reconcile.settle(gh, REQUIRED, lambda: state, lambda: [], sleep=sleep, now=now) == []
    assert [c[:2] for c in gh.calls] == [("close", 8)]
