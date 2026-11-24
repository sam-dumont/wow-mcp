"""Every workflow job runs in one repository only (docs/development.md, "The
CI mirror"). mirror.yml force-pushes main, workflows included, to the private
CI mirror: a job there could bill its minutes, and in this public repository
a job of the mirror's would hold its credentials. So every job carries its
repository's id as a top-level `&&` term of its `if:`, where no `||` can
route around it: this repository's for every workflow but the mirror-only
ones, the mirror's for those. Ids, not names: a rename keeps the id."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PUBLIC = "github.repository_id == '1464458020'"  # sam-dumont/wow-mcp
MIRROR = "github.repository_id == '1465633168'"  # sam-dumont/wow-mcp-ci
MIRROR_ONLY = {"kb-refresh.yaml"}


def and_terms(expression: str) -> list[str]:
    """The top-level `&&` terms of an expression, whitespace collapsed."""
    terms, depth, quoted, start = [], 0, False, 0
    for i, char in enumerate(expression):
        if char == "'":
            quoted = not quoted
        elif not quoted and char in "()":
            depth += 1 if char == "(" else -1
        elif not quoted and depth == 0 and expression.startswith("&&", i):
            terms.append(expression[start:i])
            start = i + 2
    terms.append(expression[start:])
    return [" ".join(term.split()) for term in terms]


def guarded(condition: object, guard: str) -> bool:
    if not isinstance(condition, str):
        return False
    expression = condition.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2]
    return guard in and_terms(expression)


def test_and_terms():
    assert guarded(f"{PUBLIC} && (a || b)", PUBLIC)
    assert guarded(f"${{{{ {PUBLIC} && !cancelled() }}}}", PUBLIC)
    assert not guarded(f"{PUBLIC} || true", PUBLIC)
    assert not guarded(f"(a || {PUBLIC})", PUBLIC)
    assert not guarded(f"x == '{PUBLIC} && y'", PUBLIC)
    assert not guarded(None, PUBLIC)


def test_every_job_runs_in_its_own_repository():
    missing = []
    paths = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
    assert {p.name for p in paths} >= MIRROR_ONLY | {"mirror.yml", "release.yml", "ci.yml"}
    for path in paths:
        guard = MIRROR if path.name in MIRROR_ONLY else PUBLIC
        jobs = (yaml.safe_load(path.read_text()) or {}).get("jobs") or {}
        missing += [f"{path.name}: {name}" for name, job in jobs.items() if not guarded(job.get("if"), guard)]
    assert not missing, f"add `<repository id> && (...)` to each job's if: {missing}"


def test_the_mirror_never_runs_for_a_pull_request():
    """A fork's pull request must never reach the mirror's credentials."""
    on = yaml.safe_load((WORKFLOWS / "mirror.yml").read_text())[True]  # YAML 1.1 reads `on:` as True
    assert set(on) == {"push", "workflow_dispatch"}
    for name in MIRROR_ONLY:
        on = yaml.safe_load((WORKFLOWS / name).read_text())[True]
        assert set(on) <= {"workflow_dispatch", "repository_dispatch"}, name


# ---------------------------------------------------------------- required checks

# main's ruleset requires these check names (the committed copy: the test
# can't call GitHub's API; docs/development.md says to keep it in sync). A
# required check is satisfied by any check run of that name, skipped ones
# included: release.yml's job `test` calling ci.yml was skipped on every
# pull request, and that skipped `test` satisfied the required check before
# ci.yml's own `test` had run (#202; #203 renamed the job to `ci`).
REQUIRED = WORKFLOWS.parent / "required-checks.txt"
PR_EVENTS = {"pull_request", "pull_request_target"}


def required() -> list[str]:
    lines = (line.strip() for line in REQUIRED.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def check_names(job_id: str, job: dict) -> list[str]:
    """The names of the top-level check runs a job makes: its `name:` or
    its id; a matrix job without a name gets "id (value, ...)" per
    combination, as GitHub writes it. A reusable-workflow call job makes
    one under its own name too when it's skipped (the called jobs' are
    "<caller> / <job>", never equal to a bare required name)."""
    matrix = (job.get("strategy") or {}).get("matrix")
    combos: list[dict] = [{}]
    if isinstance(matrix, dict):
        for key, values in matrix.items():
            if key in ("include", "exclude") or not isinstance(values, list):
                continue
            combos = [{**c, key: v} for c in combos for v in values]
        combos += [i for i in matrix.get("include") or [] if isinstance(i, dict)]
    name = job.get("name")
    out = []
    for combo in combos:
        if isinstance(name, str):
            text = name
            for key, value in combo.items():
                text = text.replace(f"${{{{ matrix.{key} }}}}", str(value))
            out.append(text)
        elif combo:
            out.append(f"{job_id} ({', '.join(str(v) for v in combo.values())})")
        else:
            out.append(job_id)
    return out


def pr_jobs() -> list[tuple[str, str, dict]]:
    """(workflow file, job id, job) for every job of a workflow a pull request triggers."""
    out = []
    for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
        doc = yaml.safe_load(path.read_text()) or {}
        on = doc.get(True, doc.get("on"))  # YAML 1.1 reads `on:` as True
        events = {on} if isinstance(on, str) else set(on or [])
        if events & PR_EVENTS:
            out += [(path.name, job_id, job) for job_id, job in (doc.get("jobs") or {}).items()]
    return out


def test_check_names():
    assert check_names("test", {}) == ["test"]
    assert check_names("analyze", {"strategy": {"matrix": {"language": ["python", "go"]}}}) == [
        "analyze (python)", "analyze (go)"]
    assert check_names("build", {"name": "build (${{ matrix.os }})", "strategy": {"matrix": {"os": ["a"]}}}) == [
        "build (a)"]
    assert check_names("ci", {"name": "test", "uses": "./.github/workflows/ci.yml"}) == ["test"]


def test_the_required_list_is_the_rulesets_seven():
    assert len(required()) == len(set(required())) == 7


def test_no_reusable_call_job_takes_a_required_name():
    """A call job's own check run (its skipped one) must never be a
    required check: it passes the gate without running anything (#202)."""
    want = set(required())
    clash = [f"{wf}: {job_id} -> {n}" for wf, job_id, job in pr_jobs() if job.get("uses")
             for n in check_names(job_id, job) if n in want]
    assert not clash, f"rename these reusable-workflow call jobs: {clash}"


def test_each_required_check_comes_from_one_pull_request_job():
    """Each required name is made by exactly one job of the workflows a
    pull request runs: none means the gate waits forever, two means the
    first to finish (or be skipped) decides."""
    made: dict[str, list[str]] = {n: [] for n in required()}
    for wf, job_id, job in pr_jobs():
        for n in check_names(job_id, job):
            if n in made:
                made[n].append(f"{wf}: {job_id}")
    wrong = {n: jobs for n, jobs in made.items() if len(jobs) != 1}
    assert not wrong, f"each required check needs exactly one job: {wrong}"
