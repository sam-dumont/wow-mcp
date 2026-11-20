"""The analysis' query engine: the prep's tables in an in-memory DuckDB, read
only, for play_sql, the judge and the semantic layer (wowstats.analysis.semantic).

The prep itself still builds in SQLite (the standard library; the plan and
the dashboard read it there). `mirror` copies its tables into DuckDB once per
workspace, column types from SQLite's declared types and the values actually
stored. The rows go over as JSON text in one parameter per chunk: DuckDB parses
it itself, about 100 times faster than executemany, and nothing touches a file.

DuckDB has no authorizer, so a query is checked before it runs, on DuckDB's own
parse tree (`json_serialize_sql`):
- one statement, and a SELECT (WITH allowed);
- tables: the prep's own and the query's CTEs; table functions: range,
  generate_series and unnest only (no read_csv, read_parquet, query(),
  glob()...);
- no function that grows a value from a number in the query (repeat, lpad,
  rpad, printf, format, scalar range, list_resize, bar, to_base, replace,
  regexp_replace): DuckDB's memory limit doesn't count a single value, and
  `lpad('x', 500000000)` made 1.6 GB, `range(300000000)` 34 GB (tried
  2026-11-20 on DuckDB 2.0.0).
And the connection is locked down at creation: no file or network access
(`enable_external_access`), no extension install or autoload, no Python
replacement scans, no spilling to disk, a memory limit, and the configuration
locked so a query can't change it. A query stops after QUERY_SECONDS
(`interrupt` from a timer).

Dialect, for the tool descriptions: `/` divides as decimals (7/2 = 3.5, `//`
is integer division), strftime takes the time first
(`strftime(to_timestamp(t), '%H')`), booleans sum only after a cast
(`SUM(CAST(kind = 'kill' AS INT))` or `COUNT(*) FILTER (WHERE ...)`).
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import math
import re
import sqlite3
import threading
import uuid

from .sql import MAX_ROWS, MAX_SQL, NOT_ALLOWED, QueryError, first_word

QUERY_SECONDS = 5.0
MEMORY_LIMIT = "256MB"
CHUNK = 20000
ENGINE = "duckdb"
# Functions that make a value larger than anything in the database from a
# number in the query: refused before the query runs.
GROWERS = frozenset({"repeat", "lpad", "rpad", "printf", "format", "range", "generate_series", "list_resize",
                     "array_resize", "bar", "to_base", "replace", "regexp_replace"})
# Table functions a query may use: rows from numbers, streamed (the timeout
# bounds them), and unnest of a list the query made.
TABLE_FUNCS = frozenset({"range", "generate_series", "unnest"})
_TYPES = {"INTEGER": "BIGINT", "REAL": "DOUBLE", "TEXT": "VARCHAR"}


def available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ImportError:
        return False
    return True


def _config() -> dict:
    return {"enable_external_access": False, "autoinstall_known_extensions": False,
            "autoload_known_extensions": False, "allow_community_extensions": False,
            "python_enable_replacements": False, "threads": 2, "memory_limit": MEMORY_LIMIT,
            "temp_directory": ""}


def _column_type(db: sqlite3.Connection, table: str, col: str, declared: str) -> str:
    """BIGINT, DOUBLE or VARCHAR: what the column really holds (SQLite lets
    an INTEGER column keep a float or a string), else what it declares."""
    held = {t for (t,) in db.execute(f'SELECT DISTINCT typeof("{col}") FROM "{table}"')} - {"null"}
    if "text" in held or "blob" in held:
        return "VARCHAR"
    if "real" in held:
        return "DOUBLE"
    if "integer" in held:
        return "BIGINT"
    d = (declared or "").upper()
    return next((v for k, v in _TYPES.items() if k in d), "DOUBLE" if "NUM" in d or "FLOA" in d else "VARCHAR")


def _finite(v):
    return None if isinstance(v, float) and not math.isfinite(v) else v


def mirror(db: sqlite3.Connection, tables=None):
    """An in-memory DuckDB with the prep's tables (all of them, or `tables`),
    same names, columns and order, locked down."""
    import duckdb

    con = duckdb.connect(":memory:", config=_config())
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")]
    for t in names:
        if tables is not None and t not in tables:
            continue
        cols = [(c[1], _column_type(db, t, c[1], c[2])) for c in db.execute(f'PRAGMA table_info("{t}")')]
        con.execute(f'CREATE TABLE "{t}" (' + ", ".join(f'"{c}" {ty}' for c, ty in cols) + ")")
        sel = ", ".join(f"CAST(j->>{i} AS {ty})" for i, (_, ty) in enumerate(cols))
        ins = f'INSERT INTO "{t}" SELECT {sel} FROM (SELECT unnest(CAST(? AS JSON[])) AS j)'
        cur = db.execute(f'SELECT * FROM "{t}" ORDER BY rowid' if _has_rowid(db, t) else f'SELECT * FROM "{t}"')
        while part := cur.fetchmany(CHUNK):
            try:
                text = json.dumps(part, allow_nan=False)
            except ValueError:  # an infinity or NaN: no JSON for it, NULL in DuckDB
                text = json.dumps([[_finite(v) for v in r] for r in part])
            con.execute(ins, [text])
    con.execute("SET lock_configuration = true")
    return con


def _has_rowid(db, t: str) -> bool:
    try:
        db.execute(f'SELECT rowid FROM "{t}" LIMIT 0')
        return True
    except sqlite3.Error:
        return False


def _walk(node, found: dict, in_table_fn: bool = False) -> None:
    if isinstance(node, list):
        for x in node:
            _walk(x, found)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("type")
    if kind == "BASE_TABLE":
        found["tables"].add((node.get("catalog_name") or "", node.get("schema_name") or "", node.get("table_name")))
    elif kind == "TABLE_FUNCTION":
        fn = node.get("function") or {}
        found["table_funcs"].add(str(fn.get("function_name") or "").lower())
        _walk(fn.get("arguments"), found)
        for k, v in node.items():
            if k != "function":
                _walk(v, found)
        return
    elif node.get("class") == "FUNCTION" and not in_table_fn:
        found["funcs"].add(str(node.get("function_name") or "").lower())
    if "cte_map" in node:
        for m in (node.get("cte_map") or {}).get("map") or []:
            found["ctes"].add(m.get("key"))
    for v in node.values():
        _walk(v, found)


def plan_check(con, q: str, tables) -> set[str]:
    """Refuse a query before it runs (QueryError); returns the tables it reads."""
    try:
        stmts = con.extract_statements(q)
    except Exception as e:  # duckdb.ParserException and friends
        raise QueryError(_short(e)) from None
    if len(stmts) != 1:
        raise QueryError("one statement only")
    if stmts[0].type.name != "SELECT":
        raise QueryError(NOT_ALLOWED)
    raw = con.execute("SELECT json_serialize_sql(?)", [q]).fetchone()[0]
    ast = json.loads(raw)
    if ast.get("error"):
        raise QueryError(str(ast.get("error_message") or NOT_ALLOWED))
    found = {"tables": set(), "table_funcs": set(), "funcs": set(), "ctes": set()}
    _walk(ast.get("statements"), found)
    bad_fn = sorted(found["table_funcs"] - TABLE_FUNCS)
    if bad_fn:
        raise QueryError(f"{NOT_ALLOWED} (table function {bad_fn[0]})")
    grow = sorted(found["funcs"] & GROWERS)
    if grow:
        raise QueryError(f"{grow[0]}() is not allowed: it can build values bigger than the database "
                         f"(refused: {', '.join(sorted(GROWERS))})")
    read = set()
    for cat, schema, name in found["tables"]:
        if name in found["ctes"] and not cat and not schema:
            continue
        if cat or schema not in ("", "main") or (tables is not None and name not in tables):
            raise QueryError(NOT_ALLOWED)
        read.add(name)
    return read


def _short(e: Exception) -> str:
    return str(e).splitlines()[0][:300]


def plain(v):
    """A DuckDB value as the SQLite engine gave it: numbers, text, None."""
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2 ** 63 else float(v)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float) and not math.isfinite(v):  # 0/0 in a float ratio: no JSON for it, no number either
        return None
    if isinstance(v, _dt.datetime | _dt.date | _dt.time):
        return v.isoformat()
    if isinstance(v, _dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, bytes | bytearray | memoryview):
        return bytes(v).hex()
    if isinstance(v, list | tuple | dict):
        return json.dumps(v, default=str)
    return v


class ReadOnlyDuck:
    """play_sql's and the judge's runner on the DuckDB mirror; the same
    interface as sql.ReadOnly (run, first_row, touched)."""

    engine = ENGINE

    def __init__(self, con, seconds: float = QUERY_SECONDS, tables=None):
        self.con, self.seconds = con, seconds
        self.tables = None if tables is None else frozenset(tables)
        self.lock = threading.Lock()
        self.touched: set[tuple[str, str]] = set()
        self._cols: dict[str, list[str]] = {}

    def columns(self, table: str) -> list[str]:
        if table not in self._cols:
            self._cols[table] = [r[0] for r in self.con.cursor().execute(f'DESCRIBE "{table}"').fetchall()]
        return self._cols[table]

    def run(self, sql: str, max_rows: int = MAX_ROWS, params: dict | None = None
            ) -> tuple[list[str], list[tuple], bool]:
        """(columns, rows, cut). One statement only; `params` binds $name."""
        q = (sql or "").strip().rstrip(";").strip()
        if not q:
            raise QueryError("empty query")
        if len(q) > MAX_SQL:
            raise QueryError(f"query longer than {MAX_SQL} characters")
        if first_word(q) not in ("SELECT", "WITH"):
            raise QueryError(NOT_ALLOWED)
        with self.lock:
            cur = self.con.cursor()
            read = plan_check(cur, q, self.tables)
            words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", q))
            for t in read:
                self.touched.update((t, c) for c in self.columns(t) if c in words)
                self.touched.add((t, ""))
            timer = threading.Timer(self.seconds, cur.interrupt)
            timer.start()
            try:
                res = cur.execute(q, params) if params else cur.execute(q)
                cols = [d[0] for d in res.description or []]
                rows = res.fetchmany(max_rows + 1)
            except Exception as e:
                name = type(e).__name__
                if name == "InterruptException":
                    msg = f"stopped after {self.seconds:.0f} s: narrow the query"
                elif name == "PermissionException":
                    msg = NOT_ALLOWED
                elif name == "OutOfMemoryException":
                    msg = "the query needs more memory than a query may use: aggregate or narrow it"
                else:
                    msg = _short(e)
                raise QueryError(msg) from None
            finally:
                timer.cancel()
                cur.close()
        rows = [tuple(plain(v) for v in r) for r in rows]
        return cols, rows[:max_rows], len(rows) > max_rows

    def first_row(self, sql: str, params: dict | None = None) -> dict:
        cols, rows, _ = self.run(sql, 1, params)
        if not rows:
            raise QueryError("no rows")
        return dict(zip(cols, rows[0], strict=False))
