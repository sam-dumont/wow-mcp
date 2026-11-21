"""Paired comparisons (property 6): every ratio in semantic.yaml reads its
numerator and its denominator over one population, its `eligibility`.

Checked for every ratio of the catalogue, not a list: the metric's answer
on the whole history must equal its own expression computed from the plain
measures over the eligible rows alone (each model's view cut to the rows
`eligibility.where` holds). A side that read rows the other doesn't
changes the ratio, and the two answers part.

Builds on tests/analysis/test_semantic_evidence.py (vs_route by hand).
"""

from __future__ import annotations

import ast
import math
import re

import pytest

from tests.invariants import histories as H

RATIOS = [m for m in H.catalogue() if m["aggregation"] == "ratio_of_sums"]


def _filtered(db, preds: dict[str, str], tz: str | None = None):
    """The layer over a copy where each named model's view holds only the
    rows its predicate keeps; how many rows each cut dropped."""
    import ibis

    from wowstats.analysis import TABLES, duck, semantic

    con = duck.mirror(db, TABLES)
    semantic.prepare(con, tz)
    spec = semantic.spec()
    dropped = {}
    for model, pred in preds.items():
        view = spec[model]["table"]
        cols = con.execute(f'DESCRIBE "{view}"').fetchall()
        types = {"BIGINT": "int64", "INTEGER": "int64", "DOUBLE": "float64", "VARCHAR": "string",
                 "TIMESTAMP": "timestamp", "TIMESTAMP WITH TIME ZONE": "timestamp", "BOOLEAN": "boolean"}
        t = ibis.table({c[0]: types.get(c[1], "string") for c in cols}, name=f"{view}__all")
        sql = ibis.to_sql(t.filter(eval(pred, {"_": ibis._})), dialect="duckdb")  # noqa: S307 (our own yaml)
        con.execute(f'CREATE TABLE "{view}__all" AS SELECT * FROM "{view}"')
        con.execute(f'DROP VIEW "{view}"')
        con.execute(f'CREATE VIEW "{view}" AS {sql}')
        n = lambda v: con.execute(f'SELECT COUNT(*) FROM "{v}"').fetchone()[0]  # noqa: E731
        dropped[model] = n(f"{view}__all") - n(view)
    return semantic.Layer(con), duck.ReadOnlyDuck(con), dropped


def ineligible(db, seed: int) -> None:
    """Rows each ratio must leave out, added to a prep: for every column an
    eligibility reads, a copy of a real row of that model's table with only
    that column emptied. The other columns keep their values, so a side
    that read the copy would move."""
    import random

    from wowstats.analysis import semantic

    rnd = random.Random(seed)
    spec = semantic.spec()
    for m in RATIOS:
        preds = m["eligibility"]["where"]
        for model, pred in (preds if isinstance(preds, dict) else {m["model"]: preds}).items():
            table = spec[model]["table"].removeprefix("sem_")
            cols = [c[1] for c in db.execute(f'PRAGMA table_info("{table}")')]
            pk = next((c[1] for c in db.execute(f'PRAGMA table_info("{table}")') if c[5]), None)
            rows = db.execute(f'SELECT * FROM "{table}"').fetchall()
            if not rows:
                continue
            for col in dict.fromkeys(re.findall(r"_\.([a-z_][a-z0-9_]*)", pred)):
                if col not in cols or col == pk:
                    continue  # a column of the view only, or the key (never empty)
                row = dict(zip(cols, rnd.choice(rows), strict=True))
                row[col] = None
                if pk:  # a new key: the next free one (a new level, session, trip)
                    row[pk] = db.execute(f'SELECT COALESCE(MAX("{pk}"), 0) + 100 FROM "{table}"').fetchone()[0]
                db.execute(f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
                           list(row.values()))
    db.commit()


# A predicate no row can fail: the records' own key is never empty.
ALWAYS_ELIGIBLE = {"deaths_per_100_kills"}


def _value(expr: str, values: dict):
    """A metric's expression (`_.a / (_.b / 3600)` or `a / (b / 3600)`) over
    measure values, NULL in, NULL out, a division by zero NULL, as in SQL."""
    tree = ast.parse(expr.replace("_.", ""), mode="eval").body

    def ev(n):
        if isinstance(n, ast.Name):
            return values[n.id]
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return None if v is None else -v
        a, b = ev(n.left), ev(n.right)
        if a is None or b is None:
            return None
        if isinstance(n.op, ast.Div):
            return None if b == 0 else a / b
        return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b}[type(n.op)]

    return ev(tree)


def _terms(m: dict) -> list[str]:
    if m["model"] == "cross":
        return list(m["of"])
    return list(dict.fromkeys(re.findall(r"_\.([a-z_][a-z0-9_]*)", m["expr"])))


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


def test_every_ratio_is_a_calculated_pair():
    """A ratio the test can't split into its sides would pass unchecked."""
    assert RATIOS
    for m in RATIOS:
        assert m["calculated"], f"{m['name']}: a ratio written as one measure can't be checked for pairing"
        assert m.get("eligibility"), m["name"]


@pytest.fixture(scope="module", params=["demo", "variant3+ineligible"])
def prep_db(request):
    if request.param == "demo":
        return H.build(H.rows_of(None))
    db = H.build(H.rows_of(3))
    ineligible(db, 3)
    return db


@pytest.fixture(scope="module")
def whole(prep_db):
    return H.layer(prep_db)


@pytest.mark.parametrize("metric", [m["name"] for m in RATIOS])
def test_both_sides_read_the_eligible_rows(prep_db, whole, metric, request):
    m = next(x for x in RATIOS if x["name"] == metric)
    preds = m["eligibility"]["where"]
    preds = preds if isinstance(preds, dict) else {m["model"]: preds}
    lay, ro, dropped = _filtered(prep_db, preds)
    if "ineligible" in request.node.callspec.id and metric not in ALWAYS_ELIGIBLE:
        assert sum(dropped.values()) > 0, f"{metric}: no row its eligibility leaves out, the check proves nothing"
    terms = _terms(m)
    for by in ([], ["level"]):
        if not set(by) <= set(m["group_by"]):
            continue
        got = H.ask(*whole, metrics=[metric], group_by=by, limit=500)
        sides = H.ask(lay, ro, metrics=terms, group_by=by, limit=500)
        want = {tuple(r[:len(by)]): _value(m["expr"], dict(zip(sides["columns"][len(by):], r[len(by):],
                                                                strict=True)))
                for r in sides["rows"]}
        have = {tuple(r[:len(by)]): r[len(by)] for r in got["rows"]}
        keys = {k for k, v in have.items() if v is not None} | {k for k, v in want.items() if v is not None}
        bad = {k: (have.get(k), want.get(k)) for k in keys if not _close(have.get(k), want.get(k))}
        assert not bad, f"{metric} by {by or 'nothing'}: the ratio differs from its sides over the eligible rows {bad}"
