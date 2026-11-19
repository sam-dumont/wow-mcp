"""The manual against the code: generated pages are current, every variable
the programs read is documented (and nothing documented is read nowhere),
defaults agree, every route the servers answer is in the endpoint table,
every page says who it's for, and every link between the Markdown files
resolves, anchors included. Offline: no network, no docker."""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("manual_generate", ROOT / "tools" / "manual" / "generate.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _needs_mcp():
    pytest.importorskip("fastmcp", reason="the MCP extra renders the tools page")


# ---------------------------------------------------------------- generated pages

def test_generated_pages_are_current():
    _needs_mcp()
    stale = [str(p.relative_to(ROOT)) for p, want in gen.render_all().items()
             if not p.exists() or p.read_text(encoding="utf-8") != want]
    assert not stale, f"run `uv run python tools/manual/generate.py`: stale {stale}"


def test_every_block_marker_is_known():
    for p in gen.block_pages():
        for name in re.findall(r"<!-- generated:([a-z-]+) -->", p.read_text(encoding="utf-8")):
            assert name in gen.BLOCKS, f"{p.relative_to(ROOT)}: unknown block {name}"


# ---------------------------------------------------------------- environment

def test_every_variable_the_code_reads_is_documented():
    assert len(gen.scan_env()) > 100, "the scan found almost nothing: it lost its patterns"
    missing = sorted(set(gen.scan_env()) - set(gen.load_env()))
    assert not missing, f"add to tools/manual/env.toml: {missing}"


def test_every_documented_variable_is_read_somewhere():
    env = gen.load_env()
    interp, _ = gen.compose_settings()
    read = set(gen.scan_env())
    stale = sorted(n for n, e in env.items()
                   if n not in read and not (e["read_by"] == ["compose"] and n in interp))
    assert not stale, f"documented but read by nothing: {stale}"


def test_every_entry_is_complete():
    groups = {g for g, _, _ in gen.GROUPS}
    known = {"ui", "publish", "static", "exporter", "render", "users", "shipper", "installer", "compose"}
    for name, e in gen.load_env().items():
        assert {"group", "read_by", "type", "default", "example", "what"} <= set(e), name
        assert e["group"] in groups, f"{name}: group {e['group']}"
        assert set(e["read_by"]) <= known, f"{name}: read_by {e['read_by']}"
        assert e["what"].endswith((".", ")")), f"{name}: end the description with a full stop"


def test_literal_defaults_agree_with_the_code():
    env = gen.load_env()
    for name, found in gen.scan_env().items():
        lits = {d for d in found["defaults"] if d.strip()}
        if len(lits) != 1:
            continue
        lit = lits.pop()
        assert f"`{lit}`" in env[name]["default"], \
            f"{name}: the code defaults to {lit!r}, env.toml says {env[name]['default']!r}"


def test_compose_settings_are_documented_and_attributed():
    pytest.importorskip("yaml")
    env = gen.load_env()
    interp, services = gen.compose_settings()
    assert not sorted(interp - set(env)), "compose.yaml interpolates undocumented variables"
    assert not sorted(gen.env_example_names() - set(env)), ".env.example names undocumented variables"
    for n in interp:
        assert "compose" in env[n]["read_by"] or any(n in keys for keys in services.values()), n
    for svc, keys in services.items():
        prog = gen.COMPOSE_SERVICE.get(svc)
        if prog is None:
            continue
        for k in keys:
            assert prog in env[k]["read_by"], f"compose service {svc} sets {k}: add {prog} to its read_by"


# ---------------------------------------------------------------- HTTP endpoints

ROUTE_FILES = ["wowstats/ui.py", "wowstats/account.py", "wowstats/static.py", "wowstats/renderer.py",
               "wowstats/exporter.py", "wowstats/analysis/__init__.py", "wowstats/model3d.py"]
PATH_NAMES = {"path", "clean", "p", "self.path", "u.path"}
INTERNAL = {"/.", "/data/model3d/"}  # file checks inside a handler, not routes


def _route_literals() -> set[str]:
    out = set()
    for rel in ROUTE_FILES:
        for n in ast.walk(ast.parse((ROOT / rel).read_text(encoding="utf-8"))):
            if isinstance(n, ast.Compare) and ast.unparse(n.left) in PATH_NAMES:
                for c in n.comparators:
                    vals = c.elts if isinstance(c, (ast.Tuple, ast.Set, ast.List)) else [c]
                    out |= {v.value for v in vals if isinstance(v, ast.Constant) and isinstance(v.value, str)}
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "startswith"
                  and ast.unparse(n.func.value) in PATH_NAMES):
                out |= {a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    return {p for p in out if p.startswith("/") and p not in INTERNAL}


def _patterns() -> list[str]:
    return [p for _, _, rows in gen.ENDPOINTS for paths, _, _ in rows for p in paths]


def _covered(lit: str, pats: list[str]) -> bool:
    for p in pats:
        if p == lit or p.rstrip("/") == lit.rstrip("/"):
            return True
        stem = p[:-1] if p.endswith("*") else (p[:p.index("<")] if "<" in p else None)
        if stem and (lit.startswith(stem) or lit == stem.rstrip("/")):
            return True
    return False


def test_every_route_in_the_code_is_in_the_endpoint_table():
    pats = _patterns()
    missing = sorted(r for r in _route_literals() if not _covered(r, pats))
    assert not missing, f"add to ENDPOINTS in tools/manual/generate.py: {missing}"


def test_every_path_in_the_endpoint_table_exists_in_the_code():
    _needs_mcp()
    from wowstats.mcp import server

    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "wowstats").rglob("*.py"))
    vl = {"/select/*", "/insert/*"}  # VictoriaLogs' own API
    for p in _patterns():
        if p in vl:
            continue
        stem = p.split("<")[0].rstrip("*")
        assert stem in src or any(stem.startswith(r) for r in server.ROUTES), f"{p} is in the table but nowhere in the code"
    table = set(_patterns())
    assert set(server.ROUTES) <= table, "server.ROUTES has a path the endpoint table lacks"


# ---------------------------------------------------------------- pages and links

def _md_files() -> list[Path]:
    files = [ROOT / n for n in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "THIRD_PARTY.md")
             if (ROOT / n).exists()]
    files += sorted(p for p in (ROOT / "docs").rglob("*.md") if "private" not in p.relative_to(ROOT / "docs").parts)
    files += sorted((ROOT / "deploy").rglob("*.md"))
    return files


def _manual_pages() -> list[Path]:
    """The pages a reader lands on: docs/ but its data subfolders."""
    return [p for p in _md_files() if p.parent != ROOT and "videos" not in p.parts and "deploy" not in p.parts]


FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]]*\]\(([^)\s]+)\)")


def slug(heading: str) -> str:
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def anchors(md: Path) -> set[str]:
    text = FENCE.sub("", md.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    out = set()
    for m in re.finditer(r"^#{1,6}\s+(.*?)\s*#*$", text, re.M):
        s = slug(m.group(1))
        n = seen.get(s, 0)
        out.add(s if n == 0 else f"{s}-{n}")
        seen[s] = n + 1
    out |= set(re.findall(r'<a (?:name|id)="([^"]+)"', text))
    return out


def test_every_internal_link_resolves():
    bad = []
    cache: dict[Path, set[str]] = {}
    for md in _md_files():
        text = INLINE_CODE.sub("", FENCE.sub("", md.read_text(encoding="utf-8")))
        for m in LINK.finditer(text):
            target = m.group(1) or m.group(2)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("<"):
                continue
            path, _, frag = target.partition("#")
            dest = (md.parent / path).resolve() if path else md
            rel = md.relative_to(ROOT)
            if not dest.exists():
                bad.append(f"{rel}: {target} (no such file)")
                continue
            if frag and dest.suffix == ".md" and frag not in cache.setdefault(dest, anchors(dest)):
                bad.append(f"{rel}: {target} (no such heading)")
    assert not bad, "\n".join(bad)


def test_every_page_has_one_title_and_says_who_it_is_for():
    for md in _manual_pages():
        text = FENCE.sub("", md.read_text(encoding="utf-8"))
        h1 = re.findall(r"^# ", text, re.M)
        rel = md.relative_to(ROOT)
        assert len(h1) == 1, f"{rel}: {len(h1)} top-level titles"
        head = "\n".join(text.splitlines()[:6])
        assert "**For:**" in head, f"{rel}: say who it's for in the first lines (**For:** ...)"


def test_the_index_lists_every_page():
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    linked = {(ROOT / "docs" / m).resolve() for m in re.findall(r"\]\(([^)#\s]+\.md)", index)}
    pages = {p.resolve() for p in _manual_pages() if p.is_relative_to(ROOT / "docs") and p.name != "README.md"
             or p.parent != ROOT / "docs" and p.name == "README.md"}
    missing = sorted(str(p.relative_to(ROOT)) for p in pages if p not in linked)
    assert not missing, f"docs/README.md doesn't link: {missing}"
