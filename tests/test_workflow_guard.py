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
