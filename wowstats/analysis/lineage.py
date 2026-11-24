"""The unit of each column a query returns, from the query's structure.

The judge refuses a `{n:pct}` or `{n:dur}` placeholder on a value whose unit
doesn't fit (#175). It used to look the unit up by the output column's name,
so an alias lost it: `SELECT played_s AS n FROM levels` with `{n:pct}` was
kept as "Level 34 took 7100%" (pre-release check, 2026-11-23). Here DuckDB's
own parser (`json_serialize_sql`) gives the expression behind each output
column, and its unit is worked out from the columns it reads: an alias, a
cast, a sum or an average, a CASE, a CTE or a subquery keep the unit;
scaling by a number keeps it; one unit over the same unit is a ratio; two
timestamps apart are seconds. Anything else is unknown (None).

Where a column's unit comes from: a view per grain's column carries its
metric's unit (semantic.yaml); a prep table's column follows the prep's
naming (`*_s` and `seconds` are seconds, `*_pct` a percent, `*_copper`
copper, `t`, `t0`, `t1` and `*_t` unix seconds)."""

from __future__ import annotations

import json
import threading

CONST = "number"  # a literal: scales a unit, has none of its own
RATIO = "ratio"
TIME = "unix seconds"

# Functions whose value is in the unit of their (first) value argument.
SAME = {"sum", "avg", "mean", "min", "max", "median", "mode", "any_value", "first", "last", "arbitrary",
        "quantile", "quantile_cont", "quantile_disc", "percentile_cont", "percentile_disc", "fsum", "sumkahan",
        "abs", "round", "floor", "ceil", "ceiling", "trunc", "arg_max", "arg_min", "argmax", "argmin", "max_by",
        "min_by", "nullif", "first_value", "last_value", "nth_value", "lag", "lead", "stddev", "stddev_pop",
        "stddev_samp", "mad", "even"}
# Every argument in one unit (literals aside).
ALL = {"coalesce", "ifnull", "greatest", "least"}
COUNTS = {"count", "count_star", "row_number", "rank", "dense_rank"}

_lock = threading.Lock()
_con = None


def raw_unit(column: str) -> str | None:
    """A prep table column's unit, by the prep's naming."""
    if column == "seconds" or column.endswith("_s"):
        return "seconds"
    if column.endswith("_pct"):
        return "percent"
    if column.endswith("_copper"):
        return "copper"
    if column in ("t", "t0", "t1") or column.endswith("_t"):
        return TIME
    return None


def _parse(sql: str) -> dict | None:
    global _con
    import duckdb

    with _lock:
        if _con is None:
            _con = duckdb.connect()
        try:
            out = json.loads(_con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
        except Exception:  # noqa: BLE001 (a parser error is an unknown unit, never a crash)
            return None
    if out.get("error") or len(out.get("statements") or []) != 1:
        return None
    return out["statements"][0]["node"]


class _Rel:
    """One relation in a FROM: {column: unit} and whether its column list is
    complete (a view per grain, a subquery) or a prep table's naming rule."""

    def __init__(self, cols: dict[str, str | None] | None, raw: bool = False):
        self.cols, self.raw = cols or {}, raw

    def has(self, c: str) -> bool:
        return c in self.cols

    def unit(self, c: str) -> str | None:
        if c in self.cols:
            return self.cols[c]
        return raw_unit(c) if self.raw else None


def _table(name: str, ctes: dict) -> _Rel:
    from . import TABLES, prep, semantic

    if name in ctes:
        return _Rel(ctes[name])
    per = semantic.views()
    if name in per:
        return _Rel({c["name"]: c.get("unit") for c in per[name]["columns"]})
    if name in TABLES:
        cols = (prep.DICTIONARY.get(name) or ("", {}))[1]
        return _Rel({c: raw_unit(c) for c in cols}, raw=True)
    return _Rel(None)


def _from(node: dict | None, ctes: dict) -> dict[str, _Rel]:
    """{name or alias: relation} for a FROM clause."""
    if not node:
        return {}
    kind = node.get("type")
    if kind == "BASE_TABLE":
        name = node.get("table_name") or ""
        return {node.get("alias") or name: _table(name, ctes)}
    if kind == "SUBQUERY":
        return {node.get("alias") or "": _Rel(_query(node["subquery"]["node"], ctes))}
    if kind == "JOIN":
        return {**_from(node.get("left"), ctes), **_from(node.get("right"), ctes)}
    return {"": _Rel(None)}


def _column(names: list[str], scope: dict[str, _Rel]) -> str | None:
    if len(names) >= 2:
        rel = scope.get(names[-2])
        return rel.unit(names[-1]) if rel else None
    c = names[0] if names else ""
    found = {r.unit(c) for r in scope.values() if r.has(c)}
    if not found and len(scope) == 1:
        found = {next(iter(scope.values())).unit(c)}
    return found.pop() if len(found) == 1 else None


def _args(e: dict) -> list[dict]:
    return [a.get("expression", a) for a in e.get("arguments") or e.get("children") or []]


def _one(units: list[str | None]) -> str | None:
    """The unit every non-literal shares (a literal when there are only
    literals), else None."""
    real = {u for u in units if u != CONST}
    if not real:
        return CONST if units else None
    return real.pop() if len(real) == 1 else None


def _expr(e: dict | None, scope: dict[str, _Rel], ctes: dict) -> str | None:
    if not e:
        return None
    cls = e.get("class")
    if cls == "COLUMN_REF":
        return _column(e.get("column_names") or [], scope)
    if cls == "CONSTANT":
        return CONST if (e.get("literal") or {}).get("kind") == "NUMERIC" or isinstance(
            e.get("value"), int | float) else None
    if cls == "CAST":
        return _expr(e.get("child"), scope, ctes)
    if cls == "CASE":
        parts = [c.get("then_expr") for c in e.get("case_checks") or []] + [e.get("else_expr")]
        parts = [p for p in parts if p and (p.get("literal") or {}).get("kind") != "NULL_LITERAL"]
        return _one([_expr(p, scope, ctes) for p in parts])
    if cls == "SUBQUERY" and e.get("subquery_type") == "SCALAR":
        outs = list(_query(e["subquery"]["node"], ctes).values())
        return outs[0] if len(outs) == 1 else None
    if cls == "OPERATOR" and e.get("type") == "OPERATOR_COALESCE":
        return _one([_expr(a, scope, ctes) for a in _args(e)])
    if cls not in ("FUNCTION", "WINDOW"):
        return None
    fn = (e.get("function_name") or "").lower()
    args = _args(e)
    if fn in COUNTS:
        return "rows"
    units = [_expr(a, scope, ctes) for a in args]
    if fn in ("+", "-"):
        if len(units) == 1:
            return units[0]
        a, b = units[0], units[1]
        if fn == "-" and a == TIME and b == TIME:
            return "seconds"
        if TIME in (a, b) and {a, b} <= {TIME, "seconds", CONST}:
            return TIME
        return _one(units)
    if fn == "*":
        real = [u for u in units if u != CONST]
        return (real[0] if len(real) == 1 else None) if real else CONST
    if fn in ("/", "//", "divide"):
        if len(units) != 2 or None in units:
            return None
        a, b = units
        if b == CONST:
            return a
        if a == CONST:
            return None
        return RATIO if a == b else f"{a}/{b}"
    if fn in SAME:
        return units[0] if units else None
    if fn in ALL:
        return _one(units)
    return None


def _query(node: dict, ctes: dict) -> dict[str, str | None]:
    """{output column: unit} for a query node."""
    ctes = dict(ctes)
    for item in (node.get("cte_map") or {}).get("map") or []:
        ctes[item["key"]] = _query(item["value"]["query_node"], ctes)
    if node.get("type") == "SET_OPERATION_NODE":
        kids = [k for k in (node.get("children") or [node.get("left"), node.get("right")]) if k]
        outs = [_query(k, ctes) for k in kids]
        if not outs:
            return {}
        first = outs[0]
        out = {}
        for i, name in enumerate(first):
            units = {list(o.values())[i] if i < len(o) else None for o in outs}
            out[name] = units.pop() if len(units) == 1 else None
        return out
    if node.get("type") != "SELECT_NODE":
        return {}
    scope = _from(node.get("from_table"), ctes)
    out: dict[str, str | None] = {}

    def put(name: str, unit: str | None):
        out[name] = unit if name not in out or out[name] == unit else None

    for i, e in enumerate(node.get("select_list") or []):
        if e.get("class") == "STAR":
            rels = [scope[e["relation_name"]]] if e.get("relation_name") in scope else list(scope.values())
            skip = {x if isinstance(x, str) else (x.get("column_names") or [""])[-1]
                    for x in (e.get("exclude_list") or []) + (e.get("qualified_exclude_list") or [])}
            for rel in rels:
                for c, u in rel.cols.items():
                    if c not in skip:
                        put(c, u)
            continue
        name = e.get("alias") or ((e.get("column_names") or [None])[-1] if e.get("class") == "COLUMN_REF" else None)
        # An unnamed expression keeps its place for a scalar subquery or a
        # UNION's first branch; units() leaves it out.
        put(name or f"\0{i}", _expr(e, scope, ctes))
    return {k: (None if v == CONST else v) for k, v in out.items()}


def units(sql: str) -> dict[str, str | None]:
    """{output column: unit or None (unknown)} for a SELECT; {} when DuckDB
    can't parse it."""
    node = _parse((sql or "").strip().rstrip(";"))
    return {k: v for k, v in _query(node, {}).items() if not k.startswith("\0")} if node else {}
