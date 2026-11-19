"""Read-only SQL on the prep database, for the analysis' play_sql and for re-running
its findings.

A query must start with SELECT or WITH, and nothing is compiled before
SQLite's authorizer is in place: it allows reading and nothing else (no
PRAGMA, ATTACH, writes, temp tables or extensions). A flag PRAGMA takes
effect while SQLite prepares it, so one compiled first (an EXPLAIN for the
table check) stuck even though running it was refused (pre-publication
review L1, 2026-11-19). `query_only` is on as a second lock, a progress
handler stops a query after QUERY_SECONDS, and while a query runs no string
or blob may pass MAX_VALUE bytes: the review allocated about a gigabyte with
one `randomblob` or `printf` width, in the process every person's view
shares (L2). Those caps are SQLite's per-connection limits, put back after
each query: the server builds and reads the same database.

Not used: `PRAGMA hard_heap_limit`/`soft_heap_limit`. They are per process,
so a cap tight enough to matter would also fail another person's prep or
knowledge base in the same server.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time

QUERY_SECONDS = 5.0
MAX_ROWS = 60
MAX_CHARS = 9000
# sqlite3 authorizer action codes (sqlite3.h)
_ALLOWED = {20, 21, 31, 33}  # READ, SELECT, FUNCTION, RECURSIVE
_DENY_FUNCS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "randomblob", "zeroblob"}
# The largest string or blob a query may make (a row of the prep is a few
# hundred bytes; the answer shown is MAX_CHARS), and the longest query.
MAX_VALUE = 1_000_000
MAX_SQL = 100_000
# Leading blanks and comments, then the statement's first word.
_LEAD = re.compile(r"(?:\s+|--[^\n]*(?:\n|$)|/\*.*?(?:\*/|$))*", re.S)
_WORD = re.compile(r"[A-Za-z]+")
NOT_ALLOWED = "not allowed: SELECT (and WITH) only, on the tables described"


class QueryError(Exception):
    pass


class ReadOnly:
    def __init__(self, db: sqlite3.Connection, seconds: float = QUERY_SECONDS, tables=None):
        self.db, self.seconds = db, seconds
        # Tables a query may read (None: any). A read of any other is refused.
        self.tables = None if tables is None else frozenset(tables) | {"sqlite_master", "sqlite_schema"}
        self.lock = threading.Lock()
        # (table, column) pairs the queries read, for the run's coverage.
        self.touched: set[tuple[str, str]] = set()

    def _auth(self, action, arg1, arg2, dbname, source):
        if action == 31 and (arg2 or "").lower() in _DENY_FUNCS:
            return sqlite3.SQLITE_DENY
        if action == 20 and arg1 and dbname == "main":
            if self.tables is not None and arg1 not in self.tables:
                return sqlite3.SQLITE_DENY
            self.touched.add((arg1, arg2 or ""))
        if action in _ALLOWED:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def run(self, sql: str, max_rows: int = MAX_ROWS) -> tuple[list[str], list[tuple], bool]:
        """(columns, rows, cut). One statement only."""
        q = (sql or "").strip().rstrip(";").strip()
        if not q:
            raise QueryError("empty query")
        if ";" in q.replace("';'", ""):
            raise QueryError("one statement only")
        if len(q) > MAX_SQL:
            raise QueryError(f"query longer than {MAX_SQL} characters")
        if first_word(q) not in ("SELECT", "WITH"):
            raise QueryError(NOT_ALLOWED)
        deadline = time.monotonic() + self.seconds
        with self.lock:
            self.db.execute("PRAGMA query_only = ON")
            # Read before the authorizer: the server's own query, not the
            # caller's, so it counts in no coverage.
            roots = self._roots() if self.tables is not None else None
            limits = self._cap()
            self.db.set_authorizer(self._auth)
            self.db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
            try:
                if roots is not None:  # EXPLAIN compiles, runs nothing
                    self._check_tables(q, roots)
                cur = self.db.execute(q)
                cols = [d[0] for d in cur.description or []]
                rows = cur.fetchmany(max_rows + 1)
            except sqlite3.Error as e:
                msg = str(e)
                if "interrupted" in msg:
                    msg = f"stopped after {self.seconds:.0f} s: narrow the query"
                elif "not authorized" in msg:
                    msg = NOT_ALLOWED
                elif "too big" in msg:
                    msg = f"a value past {MAX_VALUE} bytes: aggregate or cut it"
                raise QueryError(msg) from None
            finally:
                self.db.set_progress_handler(None, 0)
                self.db.set_authorizer(None)
                for k, v in limits.items():
                    self.db.setlimit(k, v)
        return cols, rows[:max_rows], len(rows) > max_rows

    def _cap(self) -> dict[int, int]:
        """The query-time limits; returns the values to put back."""
        old = {}
        for k, v in ((sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE), (sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL + 64)):
            old[k] = self.db.setlimit(k, v)
        return old

    def _roots(self) -> dict[int, str]:
        return {r: t for r, t in self.db.execute("SELECT rootpage, tbl_name FROM sqlite_master WHERE rootpage > 0")}

    def _check_tables(self, q: str, roots: dict[int, str]) -> None:
        """Every b-tree the statement opens must belong to an allowed table.
        The authorizer alone misses reads that touch no column (COUNT(*)).
        Runs under the authorizer: EXPLAIN prepares the caller's statement."""
        for row in self.db.execute(f"EXPLAIN {q}"):
            # addr, opcode, p1 (cursor), p2 (root page), p3 (database: 0 is main)
            if row[1] == "OpenRead" and row[4] == 0 and roots.get(row[3], "sqlite_master") not in self.tables:
                raise sqlite3.DatabaseError("not authorized")

    def first_row(self, sql: str) -> dict:
        cols, rows, _ = self.run(sql, 1)
        if not rows:
            raise QueryError("no rows")
        return dict(zip(cols, rows[0], strict=False))


def first_word(q: str) -> str:
    """The statement's first keyword, past blanks and comments, upper case."""
    m = _WORD.match(q, _LEAD.match(q).end())
    return m.group(0).upper() if m else ""


def as_text(cols: list[str], rows: list[tuple], cut: bool, max_chars: int = MAX_CHARS) -> str:
    """A compact table for the model: a header line, tab-separated rows."""
    def cell(v):
        # Every digit the model may quote: the judge re-runs the SQL and
        # compares, so 13734.0 printed as 1.373e+04 made a true sum look wrong.
        if isinstance(v, float):
            if v.is_integer():
                return str(int(v))
            return f"{v:.4g}" if abs(v) < 0.001 else f"{v:.3f}".rstrip("0").rstrip(".")
        s = "" if v is None else str(v)
        return s if len(s) <= 160 else s[:157] + "..."

    lines = ["\t".join(cols)] + ["\t".join(cell(v) for v in r) for r in rows]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n... (cut: aggregate or LIMIT)"
    elif cut:
        out += f"\n... (more than {len(rows)} rows: aggregate or LIMIT)"
    return out
