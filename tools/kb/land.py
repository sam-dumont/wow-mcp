"""The refresh job's last step: the change becomes a PR, merged on its own
when the gates passed (tools/kb/gates.py), left open with the reasons when
they refused (owner, 2026-11-20: "the goal is automated refreshes").

    uv run python tools/kb/land.py --mode watch --branch kb-watch --base <sha> \
        --title "..." --body-file body.md --gates gates.json [--kinds "official polestar"]
    uv run python tools/kb/land.py --check-open --branch kb-watch   # an earlier run's PR still open?

Needs GH_TOKEN (contents, pull requests and issues write); it runs no model
and reads no page. In order:

1. Nothing committed since `--base`: nothing to land.
2. main moved since the run started: rebase on it and run the gates again
   (a subprocess without the token), so what merges is what was tested; a
   conflict refuses.
3. Push the branch (one per mode, force-pushed), open or edit its PR, the
   gates' verdict in the body.
4. Gates passed: `gh pr merge --auto --squash --match-head-commit <sha>`,
   then the job stays with the PR until it merges (`shepherd`): main's
   ruleset wants the branch up to date with main, so each time main moves
   the PR goes BEHIND and is rebased on it (`gh pr update-branch --rebase`,
   with the App's token, so its checks run again), and auto-merge is turned
   back on if it went off. Refused: the PR keeps label `kb-refused` and stays
   open for a person.
5. The step ends red unless the PR merged (or there was nothing to land): a
   refusal, a failed required check, a conflict, or no merge within
   SHEPHERD_S. The lab's scheduler waits for the run and reports a red one
   (its CronJob fails: CronJobNotSucceeding). Before 2026-11-22 a red PR
   left the run green and nobody saw it (#99).
6. An earlier run's PR still open (this run then landed nothing): the same
   shepherding, so a PR the last run left BEHIND still merges; one labelled
   `kb-refused` keeps the run red until a person merges or closes it.
7. What the issues need: in watch mode, `--why-out` ({kind: why it isn't
   merged}) and `--state-out` (the watch.json the issues compare with: the
   merged one, else the base's), for `tools/kb/watch.py --github`; in the
   other modes, one issue per mode (label kb-watch), opened on a refusal and
   closed by the next run that merges.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import web  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LABEL = "kb-watch"
REFUSED = "kb-refused"
TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN")
# How long the job waits for its PR to merge: main's checks take about 6
# minutes, plus a rebase or two when main moves meanwhile.
SHEPHERD_S = 45 * 60
POLL_S = 60
FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}


def sh(*cmd: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=check, env=env)


def git(*args: str, check: bool = True) -> str:
    return sh("git", *args, check=check).stdout.strip()


def gh(*args: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True, check=check, input=stdin)


def open_pr(branch: str) -> int | None:
    out = json.loads(gh("pr", "list", "--head", branch, "--state", "open", "--json", "number").stdout or "[]")
    return out[0]["number"] if out else None


def regate(base: str, out: str, md: str) -> dict:
    """The gates again, after a rebase, without the token in reach."""
    env = {k: v for k, v in os.environ.items() if k not in TOKEN_VARS}
    sh("uv", "run", "--frozen", "python", "tools/kb/gates.py", "--base", base, "--out", out, "--markdown", md,
       check=False, env=env)
    return json.loads(Path(out).read_text(encoding="utf-8"))


def merge_state(n: int, tries: int = 12) -> str:
    """The PR's mergeStateStatus once GitHub has computed it (UNKNOWN first)."""
    state = "UNKNOWN"
    for _ in range(tries):
        state = json.loads(gh("pr", "view", str(n), "--json", "mergeStateStatus").stdout)["mergeStateStatus"]
        if state != "UNKNOWN":
            break
        time.sleep(5)
    return state


def merge(n: int, sha: str) -> tuple[str, str]:
    """('merged' | 'auto' | 'failed', detail)."""
    state = merge_state(n)
    r = gh("pr", "merge", str(n), "--auto", "--squash", "--match-head-commit", sha, check=False)
    view = json.loads(gh("pr", "view", str(n), "--json", "state,autoMergeRequest").stdout)
    if view["state"] == "MERGED":
        return "merged", ""
    if view.get("autoMergeRequest"):
        return "auto", f"auto-merge is on (merge state {state}), waiting for main's required checks"
    detail = " ".join((r.stderr or r.stdout).split())[:300]
    return "failed", f"GitHub didn't merge it (merge state {state}): {detail}"


def required_checks() -> set[str]:
    """The checks main's rules require (empty when they can't be read: then
    any failed check counts)."""
    r = gh("api", f"repos/{web.repo()}/rules/branches/main", check=False)
    try:
        rules = json.loads(r.stdout or "[]")
    except ValueError:
        return set()
    return {c["context"] for x in rules if x.get("type") == "required_status_checks"
            for c in (x.get("parameters") or {}).get("required_status_checks") or []}


def pr_view(n: int) -> dict:
    """The PR's state, merge state, auto-merge and checks. Reading the checks
    needs the App's checks permission: without it, the state alone (a failed
    check then shows as a PR that doesn't merge by the deadline)."""
    r = gh("pr", "view", str(n), "--json", "state,mergeStateStatus,autoMergeRequest,statusCheckRollup", check=False)
    if r.returncode:
        r = gh("pr", "view", str(n), "--json", "state,mergeStateStatus,autoMergeRequest")
    return json.loads(r.stdout)


def update_branch(n: int) -> None:
    gh("pr", "update-branch", str(n), "--rebase", check=False)


def arm(n: int) -> None:
    gh("pr", "merge", str(n), "--auto", "--squash", check=False)


def shepherd(n: int, required: set[str], deadline_s: int = SHEPHERD_S, poll_s: int = POLL_S, view=pr_view,
             update=update_branch, arm=arm, sleep=time.sleep, now=time.monotonic) -> tuple[str, str]:
    """Stay with PR `n` until it merges: ('merged' | 'red' | 'stuck', why)."""
    end = now() + deadline_s
    state = "UNKNOWN"
    while True:
        v = view(n)
        if v["state"] == "MERGED":
            return "merged", ""
        if v["state"] == "CLOSED":
            return "red", f"PR #{n} was closed without merging"
        failed = sorted({c.get("name") or c.get("context") or "?" for c in v.get("statusCheckRollup") or []
                         if (c.get("conclusion") or c.get("state") or "").upper() in FAILED})
        failed = [c for c in failed if not required or c in required]
        if failed:
            return "red", f"PR #{n}'s required checks failed: {', '.join(failed)}"
        state = v.get("mergeStateStatus") or "UNKNOWN"
        if state == "DIRTY":
            return "red", f"PR #{n} conflicts with main"
        if state == "BEHIND":
            update(n)
            arm(n)
        elif not v.get("autoMergeRequest"):
            arm(n)
        if now() >= end:
            return "stuck", f"PR #{n} didn't merge in {deadline_s // 60} minutes (merge state {state})"
        sleep(poll_s)


def exit_code(outcome: str) -> int:
    """The step's status: red unless the PR merged or there was nothing to land."""
    return 0 if outcome in ("merged", "nothing") else 1


def why_text(outcome: str, n: int | None, detail: str, gates_md: str) -> str:
    pr = f"PR #{n}" if n else "The PR"
    if outcome == "refused":
        return f"{pr} is open, not merged: the gates refused it.\n\n{gates_md}"
    if outcome == "auto":
        return f"{pr} passed the gates; {detail}."
    if outcome == "refused-open":
        return f"{pr} from an earlier run is open with label {REFUSED}: a person merges or closes it."
    return f"{pr} passed the gates but wasn't merged: {detail}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", default="watch")
    ap.add_argument("--branch", required=True)
    ap.add_argument("--base", help="the commit the run started from")
    ap.add_argument("--title", default="")
    ap.add_argument("--body-file")
    ap.add_argument("--gates", help="tools/kb/gates.py --out")
    ap.add_argument("--gates-md", help="tools/kb/gates.py --markdown")
    ap.add_argument("--kinds", default="", help="watch: the change kinds in this PR")
    ap.add_argument("--why-out", help="watch: merge {kind: why it isn't merged} into this file")
    ap.add_argument("--state-out", help="watch: write the watch.json the issues compare with here")
    ap.add_argument("--check-open", action="store_true", help="only say whether the branch has an open PR")
    ap.add_argument("--failed", action="store_true", help="the run failed before landing: keep the mode's issue")
    a = ap.parse_args(argv)
    gh_out = os.environ.get("GITHUB_OUTPUT")

    def output(**kv):
        print(json.dumps(kv))
        if gh_out:
            with open(gh_out, "a", encoding="utf-8") as f:
                f.writelines(f"{k}={v}\n" for k, v in kv.items())

    if a.check_open:
        n = open_pr(a.branch)
        output(pr=n or "")
        return 0
    if a.failed:
        import watch

        print(web.keep_issue(web.GitHub(web.repo()), f"refused-{a.mode}", LABEL,
                             f"KB {a.mode}: a change wasn't merged",
                             f"The {a.mode} run failed before it could land anything ({watch.run_link()}): read its "
                             f"failed step. The next {a.mode} run that merges closes this."))
        return 0

    kinds = a.kinds.split()
    state_rel = "data/kb/watch.json"
    seen = {"main": a.base}  # main as last fetched: what an issue compares with when nothing merged

    def finish(outcome: str, n: int | None = None, detail: str = "", gates_md: str = "") -> int:
        landed = outcome == "merged"
        if landed:  # main now holds the PR, rebased or not
            git("fetch", "-q", "origin", "main", check=False)
            seen["main"] = git("rev-parse", "origin/main")
        if a.state_out:
            text = git("show", f"{seen['main']}:{state_rel}")
            Path(a.state_out).write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
        reason = "" if landed or outcome == "nothing" else why_text(outcome, n, detail, gates_md)
        if a.why_out and reason:
            p = Path(a.why_out)
            why = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            why.update({k: reason for k in kinds if k not in why})
            p.write_text(json.dumps(why, indent=1) + "\n", encoding="utf-8")
        if a.mode != "watch" and outcome != "nothing":
            did = web.keep_issue(web.GitHub(web.repo()), f"refused-{a.mode}", LABEL,
                                 f"KB {a.mode}: a change wasn't merged",
                                 None if landed else f"{reason}\n\nThe next {a.mode} run that merges closes this.",
                                 "The next run merged.")
            print(f"issue: {did}")
        output(outcome=outcome, pr=n or "", landed=1 if landed else 0)
        if exit_code(outcome):  # red: the lab's scheduler waits for this run and reports it
            print(f"::error::{' '.join((detail or reason).split())[:400]}")
        return exit_code(outcome)

    if not git("rev-list", f"{a.base}..HEAD"):
        earlier = open_pr(a.branch)
        if not earlier:
            print("nothing committed: nothing to land")
            return finish("nothing")
        labels = json.loads(gh("pr", "view", str(earlier), "--json", "labels").stdout).get("labels") or []
        if any(lb.get("name") == REFUSED for lb in labels):
            return finish("refused-open", earlier, f"PR #{earlier} is open with label {REFUSED}")
        print(f"nothing committed; PR #{earlier} from an earlier run is open: staying with it")
        outcome, detail = shepherd(earlier, required_checks())
        return finish(outcome, earlier, detail)
    result = json.loads(Path(a.gates).read_text(encoding="utf-8"))
    gates_md = Path(a.gates_md).read_text(encoding="utf-8") if a.gates_md else ""
    sh("gh", "auth", "setup-git")  # the push's credentials, from this step's GH_TOKEN only
    git("fetch", "-q", "origin", "main")
    main_now = seen["main"] = git("rev-parse", "origin/main")
    if main_now != git("rev-parse", a.base):
        r = sh("git", "rebase", "origin/main", check=False)
        if r.returncode:
            sh("git", "rebase", "--abort", check=False)
            result = {"ok": False, "refused": ["main moved during the run and the change conflicts with it"]}
            gates_md = "**Gates: refused.**\n\n- main moved during the run and the change conflicts with it\n"
        else:
            out = os.environ.get("RUNNER_TEMP", "/tmp") + "/gates-rebased.json"
            md = out.replace(".json", ".md")
            result = regate(main_now, out, md)
            gates_md = Path(md).read_text(encoding="utf-8")
    sha = git("rev-parse", "HEAD")
    git("push", "-f", "origin", f"HEAD:refs/heads/{a.branch}")
    body = (Path(a.body_file).read_text(encoding="utf-8") if a.body_file else "") + "\n\n" + gates_md
    n = open_pr(a.branch)
    if n:
        gh("pr", "edit", str(n), "--title", a.title, "--body-file", "-", stdin=body)
    else:
        url = gh("pr", "create", "--base", "main", "--head", a.branch, "--title", a.title, "--body-file", "-",
                 "--label", LABEL, stdin=body).stdout.strip()
        n = int(url.rstrip("/").rsplit("/", 1)[-1])
    if not result["ok"]:
        gh("label", "create", REFUSED, "--color", "b60205", "--force",
           "--description", "KB refresh job: the gates refused it, a person decides", check=False)
        gh("pr", "edit", str(n), "--add-label", REFUSED, check=False)
        return finish("refused", n, gates_md=gates_md)
    gh("pr", "edit", str(n), "--remove-label", REFUSED, check=False)
    outcome, detail = merge(n, sha)
    if outcome == "auto":
        outcome, detail = shepherd(n, required_checks())
    if outcome == "merged":
        gh("api", "-X", "DELETE", f"repos/{web.repo() or ''}/git/refs/heads/{a.branch}",
           check=False)
    return finish(outcome, n, detail)


if __name__ == "__main__":
    raise SystemExit(main())
