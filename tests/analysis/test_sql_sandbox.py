"""play_sql's sandbox against what the pre-publication review tried
(SECURITY-REVIEW.md L1, L2): a PRAGMA compiled before the authorizer stuck,
and one query could allocate about a gigabyte in the shared process.

Each test gets its own database: a pragma that sticks would leak into the
module-wide one the other analysis tests read."""

import sqlite3

import pytest

from wowstats import analysis
from wowstats.analysis.sql import MAX_VALUE, QueryError, ReadOnly


@pytest.fixture
def db():
    d = sqlite3.connect(":memory:", check_same_thread=False)
    for t in analysis.TABLES:
        d.execute(f'CREATE TABLE "{t}" (a INTEGER, b TEXT)')
    d.executemany('INSERT INTO "sessions" VALUES (?, ?)', [(i, f"s{i}") for i in range(4)])
    return d


def both(db):
    """The two sandboxes the analysis uses: the drivers' (listed tables,
    checked with EXPLAIN first) and the judge's (any table)."""
    return [ReadOnly(db, tables=analysis.TABLES), ReadOnly(db)]


def pragma(db, name):
    return db.execute(f"PRAGMA {name}").fetchone()[0]


@pytest.mark.parametrize("q,name,safe", [
    ("PRAGMA query_only = 0", "query_only", 1),
    ("PRAGMA writable_schema = 1", "writable_schema", 0),
    ("PRAGMA reverse_unordered_selects = 1", "reverse_unordered_selects", 0),
    ("PRAGMA recursive_triggers = 1", "recursive_triggers", 0),
    ("  -- a comment first\n PRAGMA query_only = 0", "query_only", 1),
    ("/* hidden */ PRAGMA writable_schema = 1", "writable_schema", 0),
    ("EXPLAIN PRAGMA query_only = 0", "query_only", 1),
])
def test_a_pragma_never_takes_effect(db, q, name, safe):
    for ro in both(db):
        db.execute(f"PRAGMA {name} = {safe}")
        with pytest.raises(QueryError, match="SELECT"):
            ro.run(q)
        assert pragma(db, name) == safe, (q, ro.tables is not None)


def test_case_sensitive_like_stays_off(db):
    # Write-only pragma: its effect is the only way to read it.
    for ro in both(db):
        with pytest.raises(QueryError):
            ro.run("PRAGMA case_sensitive_like = 1")
        assert ro.run("SELECT 'a' LIKE 'A' AS x")[1] == [(1,)]


@pytest.mark.parametrize("q", ["VALUES (1)", "EXPLAIN SELECT 1", "EXPLAIN QUERY PLAN SELECT * FROM sessions",
                               "REINDEX", "ANALYZE", "VACUUM", "BEGIN", "SAVEPOINT x"])
def test_only_select_and_with_compile(db, q):
    for ro in both(db):
        with pytest.raises(QueryError, match="SELECT"):
            ro.run(q)


@pytest.mark.parametrize("q", [
    "SELECT length(randomblob(400000000)) AS n",
    "SELECT length(printf('%.*c', 500000000, 'x')) AS n",
    "SELECT length(zeroblob(400000000)) AS n",
    "SELECT length(replace(hex(zeroblob(1000000)), '0', 'xx')) AS n",
    "WITH RECURSIVE c(s) AS (SELECT 'x' UNION ALL SELECT s || s FROM c WHERE length(s) < 400000000) "
    "SELECT max(length(s)) AS n FROM c",
])
def test_no_huge_value(db, q):
    """Refused, or cut at the cap (SQLite's printf truncates at the length
    limit rather than failing): either way nothing near the size asked for
    is allocated."""
    for ro in both(db):
        try:
            got = ro.run(q)[1][0][0]
        except QueryError:
            got = None
        assert got is None or got <= MAX_VALUE, got
        # The connection is as it was: a normal query still answers.
        assert ro.run("SELECT COUNT(*) AS n FROM sessions")[1] == [(4,)]


def test_limits_hold_only_during_a_query(db):
    """The prep database is built and read by the server too: the caps are
    the sandbox's, put back after each query."""
    before = db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    ro = ReadOnly(db)
    assert ro.run("SELECT length(printf('%.*c', 900000, 'x')) AS n")[1] == [(900000,)]
    assert db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == before
    assert db.execute("SELECT length(zeroblob(5000000))").fetchone()[0] == 5000000


def test_ordinary_queries_still_work(db):
    for ro in both(db):
        assert ro.run("WITH x AS (SELECT a FROM sessions) SELECT SUM(a) AS n FROM x")[1] == [(6,)]
        assert ro.run("\n  select b from sessions where a = 2")[1] == [("s2",)]
        assert ro.run("SELECT printf('%.1f', 3.14159) AS p")[1] == [("3.1",)]
