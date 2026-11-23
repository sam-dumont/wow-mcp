"""The analysis' query engine: the prep's tables in an in-memory DuckDB, read
only, for play_sql, the judge and the semantic layer (wowstats.analysis.semantic).

The prep itself still builds in SQLite (the standard library; the plan and
the dashboard read it there). `mirror` copies its tables into DuckDB once per
workspace, column types from SQLite's declared types and the values actually
stored. The rows go over a part at a time, one list parameter per column:
about as fast as DuckDB parsing JSON text, a fifth of the memory, and
nothing touches a file.

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
(`interrupt` from a timer). DuckDB's memory limit doesn't count one value a
scalar function builds (`x || x || ...` on a string_agg went past 8 GB), so
play_sql, the judge and the recheck run their queries in a child process with
a memory cap (`worker`, wowstats.analysis.duckproc).

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

TOO_BIG = "the query needs more memory than a query may use: aggregate or narrow it"

QUERY_SECONDS = 5.0
MEMORY_LIMIT = "256MB"
CHUNK = 2000
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
    """Locked down, and sized from the process' memory budget
    (wowstats.analysis.budget: the container's limit; MEMORY_LIMIT and two
    threads without one)."""
    from .budget import current, duckdb_size

    b = current()
    return {"enable_external_access": False, "autoinstall_known_extensions": False,
            "autoload_known_extensions": False, "allow_community_extensions": False,
            "python_enable_replacements": False, "threads": b["threads"],
            "memory_limit": duckdb_size(b["copy_limit"]) if b["limit"] else MEMORY_LIMIT,
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


def mirror(db: sqlite3.Connection, tables=None, rows: bool = True):
    """An in-memory DuckDB with the prep's tables (all of them, or `tables`),
    same names, columns and order, locked down. `rows=False`: the tables
    empty, for a parent whose queries run in a child (wowstats.analysis.duckproc)
    and which only checks and compiles them."""
    import duckdb

    con = duckdb.connect(":memory:", config=_config())
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")]
    for t in names:
        if tables is not None and t not in tables:
            continue
        cols = [(c[1], _column_type(db, t, c[1], c[2])) for c in db.execute(f'PRAGMA table_info("{t}")')]
        con.execute(f'CREATE TABLE "{t}" (' + ", ".join(f'"{c}" {ty}' for c, ty in cols) + ")")
        if not rows:
            continue
        cur = db.execute(f'SELECT * FROM "{t}" ORDER BY rowid' if _has_rowid(db, t) else f'SELECT * FROM "{t}"')
        _insert(con, t, [ty for _, ty in cols], iter(lambda: cur.fetchmany(CHUNK), []))
    con.execute("SET lock_configuration = true")
    return con


def _insert(con, table: str, types: list[str], parts) -> None:
    """Rows into a DuckDB table, a part at a time, one list parameter per
    column (DuckDB's Python binding takes a list as is), zipped back into
    rows by unnest. One JSON text per part, parsed by DuckDB, was the first
    way (about 100 times faster than executemany): its parse took the
    process up 230 MB on a 9,600-row records table and the allocator kept
    it; the columns as lists take 50 MB, as fast (2026-11-23, DuckDB 2.0.0)."""
    sel = ", ".join(f"unnest(CAST(? AS {ty}[]))" for ty in types)
    ins = f'INSERT INTO "{table}" SELECT {sel}'
    for part in parts:
        if not part:
            continue
        con.execute(ins, [_column(ty, c) for ty, c in zip(types, zip(*part, strict=True), strict=True)])


def _column(ty: str, values) -> list:
    """One column's values as its DuckDB type takes them: text for VARCHAR
    (SQLite lets a column mix numbers and text), floats for DOUBLE, and
    NULL for an infinity or NaN (no number for it, as before)."""
    if ty == "VARCHAR":
        return [None if v is None else v if isinstance(v, str) else _text(v) for v in values]
    if ty == "DOUBLE":
        return [None if v is None else _finite(float(v)) for v in values]
    return list(values)


def _text(v) -> str:
    if isinstance(v, float):
        return json.dumps(v) if math.isfinite(v) else None
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def replace(con, table: str, rows: list) -> None:
    """A table's rows replaced by `rows` (tuples in its column order), in
    one transaction: wowstats.analysis.copies scores the gear advice in a
    character's copy this way, and puts the copy's own rows back after."""
    types = [r[1] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(f'DELETE FROM "{table}"')
        _insert(con, table, types, [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


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

    def __init__(self, con, seconds: float = QUERY_SECONDS, tables=None, worker=None):
        self.con, self.seconds, self.worker = con, seconds, worker
        self.tables = None if tables is None else frozenset(tables)
        self.lock = threading.Lock()
        self.touched: set[tuple[str, str]] = set()
        # The tables (and the semantic layer's views) the last query read:
        # the judge refuses evidence that reads none.
        self.last_read: set[str] = set()
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
            self.last_read = set()
            read = plan_check(cur, q, self.tables)
            self.last_read = set(read)
            words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", q))
            for t in read:
                self.touched.update((t, c) for c in self.columns(t) if c in words)
                self.touched.add((t, ""))
            if self.worker is not None:  # a child process with a memory cap runs it (wowstats.analysis.duckproc)
                cur.close()
                return self.worker.run(q, max_rows, params, self.tables)
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
                    msg = TOO_BIG
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
