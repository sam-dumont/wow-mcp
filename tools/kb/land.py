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
4. Gates passed: `gh pr merge --auto --squash --match-head-commit <sha>`.
   The workflow token's PR starts no `ci` run, so the gates ran the suite
   here; with no required check on main gh merges at once, and with one it
   turns auto-merge on. Refused: the PR keeps label `kb-refused` and stays
   open for a person.
5. What the issues need: in watch mode, `--why-out` ({kind: why it isn't
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


def why_text(outcome: str, n: int | None, detail: str, gates_md: str) -> str:
    pr = f"PR #{n}" if n else "The PR"
    if outcome == "refused":
        return f"{pr} is open, not merged: the gates refused it.\n\n{gates_md}"
    if outcome == "auto":
        return f"{pr} passed the gates; {detail}."
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
        if a.state_out:
            text = ((ROOT / state_rel).read_text(encoding="utf-8") if landed
                    else git("show", f"{seen['main']}:{state_rel}"))
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
        return 0

    if not git("rev-list", f"{a.base}..HEAD"):
        print("nothing committed: nothing to land")
        return finish("nothing")
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
    if outcome == "merged":
        gh("api", "-X", "DELETE", f"repos/{web.repo() or ''}/git/refs/heads/{a.branch}",
           check=False)
    return finish(outcome, n, detail)


if __name__ == "__main__":
    raise SystemExit(main())
