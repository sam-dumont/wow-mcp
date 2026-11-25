"""What earlier refresh runs left open, brought up to date: the first job of
every run, whatever its mode, before the run does its own work.

    uv run python tools/kb/reconcile.py --prs                  # rebase, re-arm, say which PR to make again
    uv run python tools/kb/reconcile.py --remake origin/kb-watch   # that PR's kinds, made again on main
    uv run python tools/kb/reconcile.py --settle               # stay with the PRs, close what main settled

Needs GH_TOKEN (the App's: pull requests and issues write) for --prs and
--settle; --remake runs tools/kb/versions.sh, which wants a read-only one.

Why (2026-11-25): a run only ever looked at its own mode's PR and issues, and
only the run that opened a PR could make it again. #249 (watch) went
CONFLICTING after its run's 45 minutes with it ran out, and sat there until a
person rebased it: the next watch run was a day away. #212 (addons) and #171
(a refused review) stayed open a day after their PRs merged, waiting for "the
next run of that mode that merges".

1. `--prs`: every open PR of the job (label kb-watch, a `kb-*` branch of this
   repository):
   - labelled kb-refused, or a required check failed: it waits for a person;
   - BEHIND: rebased on main (`gh pr update-branch --rebase`), auto-merge on;
   - DIRTY: the watch PR is made again (output `remake`); a model's PR can't
     be made again for free, so it waits for a person;
   - else: auto-merge back on if it went off.
2. `--remake REF`: the change kinds REF carries (its watch.json against its
   merge base with main: what the PR changed, never what this run saw), then
   the tree reset to main and tools/kb/versions.sh run with those kinds and
   gated. The workflow lands it with land.py. Also what land does when main
   moves under the PR it just made (REF is then its own commits).
3. `--settle`: the job stays with every open PR until it merges (land.py
   shepherd, one deadline for all), then closes every open kb-watch issue
   whose condition holds on main, with a one-line why:
   - a change kind's (client-build, polestar, addons, foreverdb, official):
     main's watch.json knows each value of the issue's table, or moved past it;
   - `refused-<mode>`: the PR it names merged, or one of that mode merged
     since the issue last changed, and none of that mode is open;
   - `budget`: main is under its size budget.
   The sweep's issue stays the sweep's: its condition needs every cited page
   fetched. Red while a PR still waits (the lab's scheduler reports it).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import land  # noqa: E402
import watch  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LABEL = "kb-watch"
REMAKE = "kb-watch"  # the only branch whose change is deterministic (the versions lane)
STATE = "data/kb/watch.json"
ROW = re.compile(r"^\| (.+?) \| `(.*)` \| `(.*)` \| .*\|$", re.M)
# Kinds whose values are release numbers: a later one on main settles the issue.
VERSIONED = {"client-build", "polestar", "addons"}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def state_at(ref: str) -> dict:
    return json.loads(git("show", f"{ref}:{STATE}"))


# ------------------------------------------------------------------ 1. the open PRs


class Live:
    """The PR and issue calls, through gh (GH_TOKEN, GH_REPO)."""

    def prs(self) -> list[dict]:
        out = json.loads(land.gh("pr", "list", "--state", "open", "--label", LABEL, "--limit", "50", "--json",
                                 "number,headRefName,isCrossRepository,labels,title").stdout or "[]")
        return [p for p in out if p["headRefName"].startswith("kb-") and not p.get("isCrossRepository")]

    def view(self, n: int) -> dict:
        v = land.pr_view(n)
        if v.get("mergeStateStatus") == "UNKNOWN":
            v["mergeStateStatus"] = land.merge_state(n)
        return v

    def update(self, n: int) -> None:
        land.update_branch(n)

    def arm(self, n: int) -> None:
        land.arm(n)

    def pr_state(self, n: int) -> str:
        return json.loads(land.gh("pr", "view", str(n), "--json", "state").stdout)["state"]

    def merged_since(self, branch: str, since: str) -> int | None:
        out = json.loads(land.gh("pr", "list", "--head", branch, "--state", "merged", "--limit", "20", "--json",
                                 "number,mergedAt").stdout or "[]")
        return next((p["number"] for p in out if (p.get("mergedAt") or "") > since), None)

    def issues(self) -> list[dict]:
        return json.loads(land.gh("issue", "list", "--state", "open", "--label", LABEL, "--limit", "100", "--json",
                                  "number,body,updatedAt").stdout or "[]")

    def close(self, n: int, why: str) -> None:
        land.gh("issue", "close", str(n), "--comment", why)


def refused(p: dict) -> bool:
    return any(lb.get("name") == land.REFUSED for lb in p.get("labels") or [])


def triage(gh, required: set[str]) -> dict:
    """{'remake': the watch PR to make again or None, 'waiting': [why, one per PR a person must see]}."""
    remake, waiting = None, []
    for p in gh.prs():
        n, branch = p["number"], p["headRefName"]
        if refused(p):
            waiting.append(f"PR #{n} ({branch}) is labelled {land.REFUSED}: a person merges or closes it")
            continue
        v = gh.view(n)
        if v.get("state") != "OPEN":
            continue
        if failed := land.failed_checks(v, required):
            waiting.append(f"PR #{n} ({branch}): required checks failed: {', '.join(failed)}")
            continue
        state = v.get("mergeStateStatus")
        if state == "BEHIND":
            gh.update(n)
            gh.arm(n)
        elif state == "DIRTY" and branch == REMAKE:
            remake = p
        elif state == "DIRTY":
            waiting.append(f"PR #{n} ({branch}) conflicts with main: a model's change can't be made again for free, "
                           "a person rebases it")
        elif not v.get("autoMergeRequest"):
            gh.arm(n)
    return {"remake": remake, "waiting": waiting}


# ------------------------------------------------------------------ 2. made again on main


def changed_kinds(was: dict, now: dict) -> dict:
    """The version kinds `now` moved from `was` (two watch.json), and the Polestar tag it holds."""
    a, b = watch.known(was), watch.known(now)
    kinds = [k for k in watch.VERSIONS if a[k] != b[k]]
    tag = b["polestar"]["release"] if "polestar" in kinds else ""
    return {"kinds": kinds, "tag": tag if tag and watch.TAG.match(tag) else ""}


def kinds_of(ref: str, main: str = "origin/main") -> dict:
    """What `ref` changed since it left main: its own kinds, not what a later run saw."""
    return changed_kinds(state_at(git("merge-base", ref, main)), state_at(ref))


def remake(ref: str, run=subprocess.run) -> int:
    """`ref`'s kinds made again on the current origin/main: the tree reset to
    it, then tools/kb/versions.sh with KINDS and TAG set (so it doesn't detect
    anything itself) and the gates (GATES_OUT, from the environment)."""
    git("fetch", "-q", "origin", "main")
    seen = kinds_of(ref)
    git("reset", "-q", "--hard", "origin/main")
    git("clean", "-qfd")
    base = git("rev-parse", "HEAD")
    print(f"{ref}'s kinds made again on main {base[:12]}: {' '.join(seen['kinds']) or 'none'}"
          + (f" (Polestar {seen['tag']})" if seen["tag"] else ""))
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"base={base}\nkinds={' '.join(seen['kinds'])}\n")
    env = {**os.environ, "KINDS": " ".join(seen["kinds"]), "TAG": seen["tag"], "GATES_BASE": base}
    return run(["bash", "tools/kb/versions.sh"], cwd=ROOT, env=env).returncode


# ------------------------------------------------------------------ 3. issues main settled


def change_settled(kind: str, body: str, main: dict) -> str | None:
    """Why a change kind's issue is settled on main (watch.known(main)), or None."""
    rows = ROW.findall(body)
    if not rows:
        return None
    have = main.get(kind) or {}
    said = []
    for key, old, new in rows:
        cur = have.get(key)
        if cur is None or str(cur) == old or (kind in VERSIONED and watch.older(str(cur), new)):
            return None
        said.append(f"{key} `{cur}`")
    return f"`{STATE}` on main knows {', '.join(said)}, at or past this issue's."


def refused_settled(kind: str, issue: dict, gh, open_branches: set[str]) -> str | None:
    mode = kind.removeprefix("refused-")
    mode = "review" if mode == "meta" else mode
    branch = f"kb-{mode}"
    if branch in open_branches:
        return None
    m = re.search(r"PR #(\d+)", issue.get("body") or "")
    if m and gh.pr_state(int(m.group(1))) == "MERGED":
        return f"PR #{m.group(1)} merged and no {mode} PR is open."
    if n := gh.merged_since(branch, issue.get("updatedAt") or ""):
        return f"PR #{n} of the {mode} run merged since, and no {mode} PR is open."
    return None


def settled(issue: dict, gh, main: dict, open_branches: set[str], over: list[str]) -> str | None:
    """Why an open kb-watch issue's condition holds on main, or None (it stays)."""
    m = re.search(r"<!-- kb-watch:([\w-]+) -->", issue.get("body") or "")
    kind = m.group(1) if m else ""
    if kind in watch.TITLES:
        return change_settled(kind, issue["body"], main)
    if kind.startswith("refused-"):
        return refused_settled(kind, issue, gh, open_branches)
    if kind == "budget" and not over:
        return "main is under its size budget (`data/kb/budget.json`)."
    return None


def close_settled(gh, main_state: dict, open_branches: set[str], over: list[str]) -> list[str]:
    done = []
    for issue in gh.issues():
        why = settled(issue, gh, watch.known(main_state), open_branches, over)
        if why:
            gh.close(issue["number"], f"Closed by the refresh job ({watch.run_link()}): {why}")
            done.append(f"#{issue['number']}: {why}")
    return done


def settle(gh, required: set[str], main_state, over, deadline_s: int = land.SHEPHERD_S,
           sleep=time.sleep, now=time.monotonic) -> list[str]:
    """Stay with each open PR until it merges, then close what main settled.
    `main_state`, `over`: callables, read once the PRs are done. What still waits."""
    end = now() + deadline_s
    waiting, open_branches = [], set()
    for p in gh.prs():
        n, branch = p["number"], p["headRefName"]
        if refused(p):
            outcome, detail = "refused", f"PR #{n} is labelled {land.REFUSED}: a person merges or closes it"
        else:
            outcome, detail = land.shepherd(n, required, deadline_s=max(0, int(end - now())), view=gh.view,
                                            update=gh.update, arm=gh.arm, sleep=sleep, now=now)
        if outcome != "merged":
            open_branches.add(branch)
            waiting.append(f"{branch}: {detail}")
    for line in close_settled(gh, main_state(), open_branches, over()):
        print(f"closed {line}")
    return waiting


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prs", action="store_true", help="rebase and re-arm the open PRs; output the one to make again")
    g.add_argument("--remake", metavar="REF", help="REF's version kinds made again on the current main")
    g.add_argument("--settle", action="store_true", help="stay with the open PRs, close the settled issues")
    a = ap.parse_args(argv)
    if a.remake:
        return remake(a.remake)
    gh = Live()
    if a.prs:
        got = triage(gh, land.required_checks())
        p = got["remake"]
        if out := os.environ.get("GITHUB_OUTPUT"):
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"remake={p['number'] if p else ''}\n")
        print(json.dumps({"remake": p and p["number"], "waiting": got["waiting"]}, indent=1))
        return 0

    def main_state() -> dict:
        git("fetch", "-q", "origin", "main")
        git("checkout", "-qf", "--detach", "origin/main")  # the budget reads the tree
        return state_at("origin/main")

    def over() -> list[str]:
        import lifecycle

        return lifecycle.over_budget()

    waiting = settle(gh, land.required_checks(), main_state, over)
    for w in waiting:
        print(f"::error::Waits for a person or the next run: {' '.join(w.split())[:400]}")
    return 1 if waiting else 0


if __name__ == "__main__":
    raise SystemExit(main())
