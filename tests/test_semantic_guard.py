"""Every tool and brief fact reads the play data through the semantic layer
(owner, 2026-11-22: "use the semantic model all the time"), never SQL of
its own. This test reads the package's source and fails on any place that
runs SQL or opens a database outside the allow list below: the compiler,
the prep build (ingest), play_sql's executor and the store plumbing.

A new reader needs a metric or a model in wowstats/analysis/semantic.yaml
and wowstats.analysis.semantic.rows (or query_metric), not a line here. An
entry that no longer matches anything fails too, so the list only shrinks.

What counts as running SQL: a DB-API call (execute, executemany,
executescript), opening a database (sqlite3.connect, duckdb.connect, a
DuckDB mirror of the prep, a raw read-only runner) and SQL text (a string
that starts with SELECT, WITH, PRAGMA...) outside a docstring."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "wowstats"

EXEC = {"execute", "executemany", "executescript"}
OPEN = {"connect", "mirror", "ReadOnlyDuck"}
SQL_TEXT = re.compile(r"^\s*(SELECT|WITH|PRAGMA|INSERT|CREATE|UPDATE|DELETE|EXPLAIN|ATTACH)\s+\S")

# Whole modules that are the plumbing: each with why.
MODULES = {
    "wowstats/analysis/semantic.py": "the semantic compiler itself: its model views, the copy it compiles on, "
                                     "the store models' in-memory relation",
    "wowstats/analysis/prep.py": "ingest: builds the prep tables from the events, and the schema text and "
                                 "neutral profile the analysis brief describes them with",
    "wowstats/analysis/duck.py": "the DuckDB mirror of the prep and the read-only runner (play_sql and the "
                                 "layer run through it)",
    "wowstats/analysis/duckproc.py": "play_sql's query process: the runner in a child process",
    "wowstats/analysis/sql.py": "play_sql's checks and its SQLite runner",
    "wowstats/eventstore.py": "the SQLite event store: the raw events as they arrived (a store like "
                              "VictoriaLogs, read with LogsQL), never the prep or a number",
    "wowstats/analysis/kb.py": "the knowledge base's own store (documents, full-text search), not play data",
    "wowstats/analysis/copies.py": "the one DuckDB copy per character every reader shares: opens it, keeps "
                                   "its gear rows and swaps an advice's in and out (plumbing, no metric)",
}

# Single functions: module:qualname -> why.
FUNCTIONS = {
    "wowstats/analysis/lineage.py:_parse": "parses a finding's SQL with DuckDB's parser (json_serialize_sql) on an "
                                           "empty in-memory database: reads no data, gives each column's unit",
    "wowstats/analysis/__init__.py:tables_of": "which tables hold rows and their column count: the run's "
                                               "coverage denominator, not a fact",
    "wowstats/analysis/__init__.py:data_mark": "the build's input mark (meta): which events a finding was "
                                               "checked on",
    "wowstats/analysis/__init__.py:finish": "the build's newest event time (meta) stamped on the run's state",
    "wowstats/analysis/__init__.py:_duck_runner": "the workspace's DuckDB copy and runner for play_sql and the "
                                                  "layer",
    "wowstats/analysis/manifest.py:build": "the build's newest event time (meta) in the run's manifest",
    "wowstats/analysis/drive.py:Workspace.__init__": "play_sql's workspace: the copy and its read-only runners",
    "wowstats/analysis/verify.py:banned_names": "privacy: the places and creatures a public text must never "
                                                "name, a vocabulary rather than a fact",
    "wowstats/analysis/verify.py:banned_people": "privacy: the creatures a public text must never name",
    "wowstats/mcp/plantools.py:t_kb_topics": "the knowledge base's documents, not play data",
    "wowstats/plan/facts.py:header": "the build's header (character, class, race, faction): the meta rows the "
                                     "layer's character and class columns read too",
    "wowstats/plan/facts.py:dungeon_quests": "the knowledge base's quest documents, not play data",
    "wowstats/plan/facts.py:dungeon_runs_kb": "the knowledge base's Polestar dungeon documents, not play data",
}


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _name(call: ast.Call) -> str | None:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None


def scan(source: str) -> list[tuple[str, int, str]]:
    """(qualname of the enclosing function or <module>, line, what) for every
    place in `source` that runs SQL or opens a database."""
    tree = ast.parse(source)
    docs = _docstrings(tree)
    hits = []

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = [*scope, child.name]
            where = ".".join(inner) or "<module>"
            if isinstance(child, ast.Call):
                n = _name(child)
                if n in EXEC:
                    hits.append((where, child.lineno, f"{n}()"))
                elif n in OPEN:
                    hits.append((where, child.lineno, f"opens a database: {n}()"))
            elif isinstance(child, ast.Constant) and isinstance(child.value, str) and id(child) not in docs:
                if SQL_TEXT.match(child.value):
                    hits.append((where, child.lineno, f"SQL text: {child.value[:40]!r}"))
            elif isinstance(child, ast.JoinedStr):
                text = "".join(v.value if isinstance(v, ast.Constant) else "x" for v in child.values)
                if SQL_TEXT.match(text):
                    hits.append((where, child.lineno, f"SQL text: {text[:40]!r}"))
                continue  # its parts are the same string
            visit(child, inner)

    visit(tree, [])
    return hits


def _all_hits() -> dict[str, list[tuple[str, int, str]]]:
    out = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        got = scan(path.read_text(encoding="utf-8"))
        if got:
            out[rel] = got
    return out


def _allowed(rel: str, where: str) -> bool:
    """An allowed function covers the functions nested in it, never the
    rest of its class or module."""
    return any(key.startswith(rel + ":") and (where == (fn := key.split(":", 1)[1]) or where.startswith(fn + "."))
               for key in FUNCTIONS)


def test_no_reader_runs_its_own_sql():
    """Outside the plumbing, nothing in the package runs SQL against the
    store or the prep: every tool and fact goes through the semantic layer."""
    bad = []
    for rel, hits in _all_hits().items():
        if rel in MODULES:
            continue
        for where, line, what in hits:
            if not _allowed(rel, where):
                bad.append(f"{rel}:{line} in {where}: {what}")
    assert not bad, ("SQL outside the semantic layer (add a metric to semantic.yaml and read it with "
                     "semantic.rows; plumbing goes in this test's allow list, with why):\n" + "\n".join(bad))


def test_the_allow_list_only_names_what_is_there():
    """An allowed module or function that no longer runs SQL leaves the
    list: it only shrinks."""
    hits = _all_hits()
    stale = [m for m in MODULES if m not in hits]
    for key in FUNCTIONS:
        rel, fn = key.split(":", 1)
        if not any(where == fn or where.startswith(fn + ".") for where, _, _ in hits.get(rel, [])):
            stale.append(key)
    assert not stale, f"allowed but running no SQL any more: {stale}"


def test_the_guard_sees_a_reader_that_runs_sql():
    """The scanner itself: a hand-written query, a cursor call, a raw copy
    and an f-string query are all caught; a docstring that shows SQL isn't."""
    src = '''
def reader(db, con):
    """Reads: SELECT level FROM levels (an example in a docstring)."""
    db.execute("SELECT level FROM levels")
    q = f"SELECT {1} FROM xp"
    rows = con.fetchall()
    copy = duck.mirror(db)
    return "WITH a AS (SELECT 1) SELECT * FROM a"
'''
    got = scan(src)
    assert [(w, what.split(":")[0]) for w, _, what in got] == [
        ("reader", "execute()"), ("reader", "SQL text"), ("reader", "SQL text"), ("reader", "opens a database"),
        ("reader", "SQL text")]
