"""Every file a Dockerfile COPYs or the wheel force-includes must pass the .dockerignore allowlist.

CI found these two at a time (the clips, class-reference.json), after a merge. This fails locally instead.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _allowlist() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    return [ln[1:].strip().rstrip("/") for ln in lines if ln.startswith("!")]


def _allowed(path: str) -> bool:
    path = path.removeprefix("./").rstrip("/")
    return any(path == a or path.startswith(a + "/") for a in _allowlist())


def _copy_sources() -> list[tuple[str, str]]:
    out = []
    for n, line in enumerate((ROOT / "Dockerfile").read_text().splitlines(), 1):
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        args = [a for a in line.split()[1:] if not a.startswith("--")]
        out += [(f"Dockerfile:{n}", src) for src in args[:-1]]
    return out


def test_dockerignore_starts_closed():
    assert (ROOT / ".dockerignore").read_text().splitlines()[0] == "*"


def test_copy_sources_are_allowlisted():
    missing = [f"{where} {src}" for where, src in _copy_sources() if not _allowed(src)]
    assert not missing, f"COPY sources the .dockerignore drops: {missing}"


def test_copy_sources_exist():
    gone = [src for _, src in _copy_sources() if not (ROOT / src).exists()]
    assert not gone, gone


def test_force_include_sources_are_allowlisted():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    inc = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    missing = [src for src in inc if not _allowed(src)]
    assert not missing, f"force-include paths the .dockerignore drops: {missing}"


def test_every_stage_that_builds_the_wheel_has_the_force_includes():
    """Each stage running `uv sync` must COPY (or mkdir) every force-include source."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    inc = list(cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"])
    stages = re.split(r"(?m)^FROM ", (ROOT / "Dockerfile").read_text())[1:]
    for st in stages:
        if "uv sync" not in st:
            continue
        copied = [c.split()[-2].removeprefix("./").rstrip("/") for c in st.splitlines() if c.startswith("COPY ")]
        made = re.findall(r"mkdir -p (\S+)", st)
        for src in inc:
            ok = any(src == c or src.startswith(c + "/") for c in copied + made)
            assert ok, f"stage {st.splitlines()[0]!r} builds the wheel without {src}"


def _closure(lock: dict, names: list[str]) -> set[str]:
    pkgs = {p["name"]: p for p in lock["package"]}
    seen: set[str] = set()
    todo = list(names)
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo += [d["name"] for d in pkgs[n].get("dependencies", [])]
    return seen


def test_the_renderer_leaves_out_the_semantic_layer_and_only_it():
    """The semantic layer is core (one computation path for the pages); the
    renderer image never imports it, so its stage skips exactly what only
    the core dependencies pull in: nothing the render extra needs, nothing
    left behind."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    me = next(p for p in lock["package"] if p["name"] == "wow-stats")
    core = _closure(lock, [d["name"] for d in me.get("dependencies", [])])
    render = _closure(lock, [d["name"] for d in me["optional-dependencies"]["render"]])
    stage = re.split(r"(?m)^FROM ", (ROOT / "Dockerfile").read_text())
    builder = next(st for st in stage if st.startswith("render-base AS render-builder"))
    skipped = set(re.findall(r"--no-install-package (\S+)", builder))
    assert core and skipped == core - render
