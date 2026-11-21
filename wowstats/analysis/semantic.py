"""The semantic layer: the analysis' metrics, declared once in semantic.yaml
(Boring Semantic Layer's format), compiled to DuckDB SQL and run on the
character's DuckDB copy of the prep (wowstats.analysis.duck).

A question is (metrics, group_by, where, grain, since, until, order_by,
limit). Every name comes from the model's closed list, the join path from the
model's joins, and the values from the caller are bound as parameters: each one
goes into the compiler as a random sentinel and comes out as `$p0`, `$p1`...
(BSL compiles through ibis and sqlglot, which would otherwise write the value
into the SQL as a quoted literal). Dates (since, until) are parsed to datetimes
before they reach the compiler. The SQL then runs through the same read-only
runner as play_sql: same checks, timeout and row cap.

The answer carries the rows, the SQL that made them, its parameters and each
metric's definition and caveats, so the reader can check it; propose_finding
takes the same call as evidence and re-runs it.

BSL is only a compiler here: the tables handed to it are unbound ibis tables
(name and schema), so no ibis backend, pandas or pyarrow is loaded.
"""

from __future__ import annotations

import ast
import functools
import math
import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from .sql import QueryError

MODEL_FILE = Path(__file__).with_name("semantic.yaml")
GRAINS = {"day": "TIME_GRAIN_DAY", "week": "TIME_GRAIN_WEEK", "month": "TIME_GRAIN_MONTH"}
OPS = ("=", "!=", "<", "<=", ">", ">=", "in", "not in", "is null", "is not null")
MAX_METRICS, MAX_GROUP, MAX_WHERE, MAX_IN, MAX_TEXT = 8, 4, 8, 50, 200
DEFAULT_LIMIT, MAX_LIMIT = 50, 500
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

# The views the model reads (semantic.yaml `table`): the prep's table with its
# time in the player's timezone, and the character's name and class. The
# calendar time per level needs the previous level's ding, so levels gets it here.
_WHO = ("(SELECT value FROM meta WHERE key = 'char') AS character, "
        "(SELECT value FROM meta WHERE key = 'class') AS char_class")
VIEWS = {
    "sem_levels": ("SELECT l.*, CASE WHEN LAG(l.level) OVER w = l.level - 1 THEN l.ding_t - LAG(l.ding_t) OVER w END "
                   "AS calendar_s, {ts:l.ding_t} AS at_local, {who} FROM levels l WINDOW w AS (ORDER BY l.level)"),
    # The guide's and the step's titles from the route reference (route_steps,
    # one row per guide and step: MIN only guards against a duplicate).
    "sem_timeline": ("SELECT t.*, rs.guide_title, rs.step_title, {ts:t.t0} AS at_local, {who} FROM timeline t "
                     "LEFT JOIN (SELECT guide, step, MIN(guide_title) AS guide_title, MIN(title) AS step_title "
                     "FROM route_steps GROUP BY guide, step) rs ON rs.guide = t.guide AND rs.step = t.step"),
    "sem_records": "SELECT *, {ts:t} AS at_local, {who} FROM records",
    "sem_sessions": "SELECT *, {ts:start_t} AS at_local, {who} FROM sessions",
    "sem_dungeon_runs": "SELECT *, {ts:start_t} AS at_local, {who} FROM dungeon_runs",
    "sem_dungeon_trips": "SELECT *, {ts:start_t} AS at_local, {who} FROM dungeon_trips",
    "sem_snapshots": "SELECT *, {ts:t} AS at_local, {who} FROM snapshots",
    "sem_quests": ("SELECT *, {ts:at_t} AS at_local, {who} FROM (SELECT *, COALESCE(closed_t, accepted_t) AS at_t, "
                   "COALESCE(quest_name, 'Quest ' || CAST(quest_id AS VARCHAR)) AS quest FROM quests)"),
    "sem_gold_moves": "SELECT *, {ts:t0} AS at_local, {who} FROM gold_moves",
}
CROSS_KEY = "cross_model_metrics"


def prepare(con, tz: str | None = None) -> None:
    """Add the model's views to a DuckDB copy of the prep (before any query)."""
    from . import prep

    zone = (tz or prep.TZ_NAME).replace("'", "''")
    for name, body in VIEWS.items():
        sql = re.sub(r"\{ts:([a-z_.0-9]+)\}", lambda m: f"timezone('{zone}', to_timestamp({m.group(1)}))", body)
        con.execute(f"CREATE VIEW {name} AS {sql.replace('{who}', _WHO)}")


@functools.cache
def _file() -> dict:
    from boring_semantic_layer.io import read_yaml_file

    return read_yaml_file(str(MODEL_FILE))


@functools.cache
def spec() -> dict:
    """semantic.yaml's models, as BSL reads them (our cross-model section left out)."""
    return {k: v for k, v in _file().items() if k != CROSS_KEY}


_ARITH = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def _cross_terms(name: str, expr: str, known: dict[str, str]) -> tuple[ast.expr, list[str]]:
    """A cross-model expression parsed and checked: metric names, numbers,
    + - * / and parentheses only. Returns the tree and the metrics it reads."""
    try:
        tree = ast.parse(expr, mode="eval").body
    except SyntaxError:
        raise ValueError(f"{name}: not an expression: {expr!r}") from None
    used: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in known:
                raise ValueError(f"{name}: {node.id!r} is not a metric of a model")
            used.append(node.id)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int | float):
                raise ValueError(f"{name}: only numbers as constants")
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ARITH):
                raise ValueError(f"{name}: only + - * /")
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, ast.USub):
                raise ValueError(f"{name}: only unary minus")
        elif not isinstance(node, ast.expr_context | ast.operator | ast.unaryop):
            raise ValueError(f"{name}: {type(node).__name__} is not allowed")
    used = list(dict.fromkeys(used))
    if len({known[u] for u in used}) < 2:
        raise ValueError(f"{name}: a cross-model metric reads metrics of two models at least")
    return tree, used


def _sql_of(node: ast.expr, col) -> str:
    """The checked expression as DuckDB SQL over the joined columns; a
    division by zero gives NULL, like the models' own ratios."""
    if isinstance(node, ast.Name):
        return col(node.id)
    if isinstance(node, ast.Constant):
        return repr(float(node.value)) if isinstance(node.value, float) else str(int(node.value))
    if isinstance(node, ast.UnaryOp):
        return f"(-{_sql_of(node.operand, col)})"
    a, b = _sql_of(node.left, col), _sql_of(node.right, col)
    if isinstance(node.op, ast.Div):
        return f"(CAST({a} AS DOUBLE) / NULLIF({b}, 0))"
    return f"({a} {dict(((ast.Add, '+'), (ast.Sub, '-'), (ast.Mult, '*')))[type(node.op)]} {b})"


@functools.cache
def catalogue() -> dict:
    """list_metrics' answer: every metric with its definition, the
    dimensions it can be grouped and filtered by, and the time grains."""
    s = spec()
    metrics, dims = [], {}
    for model, cfg in s.items():
        own = cfg.get("dimensions") or {}
        for name, d in own.items():
            e = dims.setdefault(name, {"name": name, "description": d.get("description"), "models": [],
                                       "entity": bool(d.get("is_entity"))})
            e["models"].append(model)
            if d.get("is_time_dimension"):
                e["grains"] = list(GRAINS)
        for section in ("measures", "calculated_measures"):
            for name, m in (cfg.get(section) or {}).items():
                meta = m.get("metadata") or {}
                metrics.append({"name": name, "model": model, "kind": meta.get("kind"), "unit": meta.get("unit"),
                                "description": m.get("description"), "caveats": meta.get("caveats"),
                                "expr": m.get("expr"), "group_by": sorted(own)})
    owner = {m["name"]: m["model"] for m in metrics}
    for name, m in (_file().get(CROSS_KEY) or {}).items():
        meta = m.get("metadata") or {}
        _, used = _cross_terms(name, str(m.get("expr") or ""), owner)
        models = list(dict.fromkeys(owner[u] for u in used))
        shared = set.intersection(*(set(s[mo].get("dimensions") or {}) for mo in models))
        if m.get("dimensions"):
            shared &= set(m["dimensions"])
        metrics.append({"name": name, "model": "cross", "models": models, "of": used, "kind": meta.get("kind"),
                        "unit": meta.get("unit"), "description": m.get("description"),
                        "caveats": meta.get("caveats"), "expr": m.get("expr"), "group_by": sorted(shared)})
    return {"metrics": metrics, "dimensions": sorted(dims.values(), key=lambda d: d["name"]),
            "grains": list(GRAINS), "ops": list(OPS)}


def _metric(name: str) -> dict:
    m = next((x for x in catalogue()["metrics"] if x["name"] == name), None)
    if m is None:
        raise QueryError(f"unknown metric {name!r}: list_metrics names them")
    return m


class Layer:
    """The model compiled against one character's DuckDB copy (its column
    types), built once per workspace."""

    def __init__(self, con):
        import ibis
        from boring_semantic_layer import from_config

        types = {"BIGINT": "int64", "INTEGER": "int64", "DOUBLE": "float64", "VARCHAR": "string",
                 "TIMESTAMP": "timestamp", "BOOLEAN": "boolean"}
        tables = {}
        for cfg in spec().values():
            name = cfg["table"]
            cols = con.cursor().execute(f'DESCRIBE "{name}"').fetchall()
            tables[name] = ibis.table({c[0]: types.get(c[1], "string") for c in cols}, name=name)
        self.models = from_config(spec(), tables=tables)

    def _one(self, model: str, metrics: list[dict], q: dict, values: list) -> str:
        """One model's part: its metrics aggregated to the question's
        group_by, filters on its own dimensions; columns named plainly."""
        import ibis

        sem = self.models[model]
        own = set(sem.get_dimensions())
        missing = [d for d in q["group_by"] + [w["dimension"] for w in q["where"]] if d not in own]
        if missing:
            raise QueryError(f"{metrics[0]['name']} can't be grouped or filtered by {missing[0]!r}: "
                             f"its dimensions are {', '.join(sorted(own))}")
        filters = []
        for w, v in zip(q["where"], values, strict=True):
            if w["op"] in ("is null", "is not null"):
                filters.append({"field": w["dimension"], "operator": w["op"]})
            elif w["op"] in ("in", "not in"):
                filters.append({"field": w["dimension"], "operator": w["op"], "values": v})
            else:
                filters.append({"field": w["dimension"], "operator": w["op"], "value": v})
        if q["since"]:
            # Parsed datetimes, never caller text: the compiler writes them as timestamp literals.
            filters.append({"field": "time", "operator": ">=", "value": datetime.fromisoformat(q["since"])})
        if q["until"]:
            end = datetime.fromisoformat(q["until"])
            date_only = "T" not in q["until"]
            filters.append({"field": "time", "operator": "<" if date_only else "<=",
                            "value": end + timedelta(days=1) if date_only else end})
        kw = {"time_grain": GRAINS[q["grain"]]} if q["grain"] else {}
        try:
            expr = sem.query(dimensions=q["group_by"] or None, measures=[m["name"] for m in metrics],
                             filters=filters or None, **kw)
            return str(ibis.to_sql(expr.to_untagged(), dialect="duckdb"))
        except QueryError:
            raise
        except Exception as e:  # BSL's and ibis' own errors: the caller's mix doesn't fit the model
            raise QueryError(f"can't compile that: {str(e).splitlines()[0][:240]}") from None

    def compile(self, q: dict) -> dict:
        """The query checked and compiled: {sql, params, columns, definitions, query}.
        One model: its aggregate. Several: each model's aggregate, joined on
        the group_by columns (a full outer join, so a level with time but no
        kills keeps its row)."""
        q = normalise(q)
        metrics = [_metric(n) for n in q["metrics"]]
        # A cross-model metric is computed after the join from the models'
        # own metrics: those go into their models' parts (unshown if not asked).
        base = list(dict.fromkeys(n for m in metrics for n in (m["of"] if m["model"] == "cross" else [m["name"]])))
        by_model: dict[str, list[dict]] = {}
        for n in base:
            b = _metric(n)
            by_model.setdefault(b["model"], []).append(b)
        for m in metrics:
            if m["model"] == "cross":
                bad = [d for d in q["group_by"] + [w["dimension"] for w in q["where"]] if d not in m["group_by"]]
                if bad:
                    raise QueryError(f"{m['name']} can't be grouped or filtered by {bad[0]!r}: its dimensions "
                                     f"(shared by {', '.join(m['models'])}) are {', '.join(m['group_by'])}")
        nonce = secrets.token_hex(6)
        params: dict[str, object] = {}
        tokens: dict[str, str] = {}

        def sentinel(v):
            key = f"p{len(params)}"
            params[key] = v
            if isinstance(v, str):
                tok = f"wsq{nonce}{key}"
                tokens[f"'{tok}'"] = f"${key}"
                return tok
            tok = int(f"7{int(nonce, 16) % 10**12:012d}{len(params):04d}")
            tokens[str(tok)] = f"${key}"
            return tok

        values = [[sentinel(x) for x in w["values"]] if "values" in w else sentinel(w["value"]) if "value" in w
                  else None for w in q["where"]]
        parts = {model: self._one(model, ms, q, values) for model, ms in by_model.items()}
        for tok, ph in tokens.items():
            hits = [m for m, sql in parts.items() if tok in sql]
            if len(hits) != len(parts):
                raise QueryError("a filter value didn't reach the query (refused rather than run unfiltered)")
            parts = {m: sql.replace(tok, ph) for m, sql in parts.items()}
        def out(d: str) -> str:
            return q["grain"] if d == "time" else d

        dims = q["group_by"]
        names = list(parts)
        ctes = ",\n".join(f'm{i} AS (\n{parts[m]}\n)' for i, m in enumerate(names))
        def key(d: str, n: int) -> str:
            """Dimension d of the row joined so far from m0..m(n-1): the first
            model that has it. A sparse model (dungeon runs) leaves its own
            column NULL on the others' rows."""
            return f'COALESCE({", ".join(f"m{i}.{_q(d)}" for i in range(n))})' if n > 1 else f"m0.{_q(d)}"

        def col(d: str) -> str:
            c = key(d, len(names))
            # A day, week or month comes out as its first day: 2026-11-09, not a midnight timestamp.
            return f"CAST({c} AS DATE) AS {_q(out(d))}" if d == "time" else f"{c} AS {_q(out(d))}"

        def base_col(n: str) -> str:
            return f"m{names.index(_metric(n)['model'])}.{_q(n)}"

        sel = [col(d) for d in dims]
        for m in metrics:
            if m["model"] == "cross":
                tree, _ = _cross_terms(m["name"], m["expr"], {n: _metric(n)["model"] for n in m["of"]})
                sel.append(f"{_sql_of(tree, base_col)} AS {_q(m['name'])}")
            else:
                sel.append(f"{base_col(m['name'])} AS {_q(m['name'])}")
        frm = "m0"
        for i in range(1, len(names)):
            # On the keys of everything joined before, not m0's alone: a level
            # m0 lacks would otherwise match nothing in m2 and split in two
            # (review 2026-11-21, A).
            on = " AND ".join(f"{key(d, i)} IS NOT DISTINCT FROM m{i}.{_q(d)}" for d in dims) or "TRUE"
            frm += f" FULL OUTER JOIN m{i} ON {on}"
        order = q["order_by"] or [(d, "asc") for d in dims]
        ob = ", ".join(f"{_q(out(n))} {d.upper()} NULLS LAST" for n, d in order)
        sql = f"WITH {ctes}\nSELECT {', '.join(sel)}\nFROM {frm}" + (f"\nORDER BY {ob}" if ob else "") + \
            f"\nLIMIT {int(q['limit'])}"
        return {"sql": sql, "params": params, "columns": [out(d) for d in dims] + [m["name"] for m in metrics],
                "definitions": {m["name"]: {k: m[k] for k in ("kind", "unit", "description", "caveats", "model",
                                                             "models", "of", "expr") if k in m and (
                                    m["model"] == "cross" or k not in ("expr",))}
                                for m in metrics},
                "query": q}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _scalar(v, what: str):
    if isinstance(v, bool) or v is None:
        raise QueryError(f"{what}: a number or a text")
    if isinstance(v, int):
        if abs(v) > 2 ** 62:
            raise QueryError(f"{what}: number too large")
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise QueryError(f"{what}: a finite number")
        return v
    if isinstance(v, str):
        if len(v) > MAX_TEXT:
            raise QueryError(f"{what}: text longer than {MAX_TEXT} characters")
        return v
    raise QueryError(f"{what}: a number or a text")


def _when(v, what: str) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v.isoformat()
    if not isinstance(v, str) or not _DATE.match(v):
        raise QueryError(f"{what}: a date, YYYY-MM-DD (or YYYY-MM-DDTHH:MM)")
    try:
        datetime.fromisoformat(v)
    except ValueError:
        raise QueryError(f"{what}: not a real date") from None
    return v


def normalise(q: dict) -> dict:
    """The query's shape checked, names against the closed lists: a QueryError
    says what's wrong. Values are only type-checked: they are bound later."""
    if not isinstance(q, dict):
        raise QueryError("a query is an object: metrics, group_by, where, grain, order_by, limit")
    cat = catalogue()
    known_m = {m["name"] for m in cat["metrics"]}
    known_d = {d["name"] for d in cat["dimensions"]}

    def names(v, what, known, cap):
        v = [v] if isinstance(v, str) else (v or [])
        if not isinstance(v, list) or len(v) > cap or not all(isinstance(x, str) for x in v):
            raise QueryError(f"{what}: a list of at most {cap} names")
        bad = [x for x in v if x not in known or not _NAME.match(x)]
        if bad:
            raise QueryError(f"{what}: unknown {bad[0]!r} (list_metrics names them)")
        return list(dict.fromkeys(v))

    metrics = names(q.get("metrics"), "metrics", known_m, MAX_METRICS)
    if not metrics:
        raise QueryError("metrics: at least one (list_metrics names them)")
    group = names(q.get("group_by"), "group_by", known_d, MAX_GROUP)
    grain = q.get("grain") or None
    if grain is not None and grain not in GRAINS:
        raise QueryError(f"grain: one of {', '.join(GRAINS)}")
    if grain and "time" not in group:
        group.append("time")
    if "time" in group and not grain:
        grain = "day"
    where = []
    raw = q.get("where") or []
    if not isinstance(raw, list) or len(raw) > MAX_WHERE:
        raise QueryError(f"where: a list of at most {MAX_WHERE} conditions")
    for i, w in enumerate(raw):
        if not isinstance(w, dict):
            raise QueryError(f"where[{i}]: an object {{dimension, op, value}}")
        d, op = w.get("dimension"), str(w.get("op") or "=").lower().strip()
        if d not in known_d or d == "time":
            raise QueryError(f"where[{i}]: dimension {d!r} unknown (time: use since and until)")
        if op not in OPS:
            raise QueryError(f"where[{i}]: op is one of {', '.join(OPS)}")
        c = {"dimension": d, "op": op}
        if op in ("in", "not in"):
            vals = w.get("values", w.get("value"))
            if not isinstance(vals, list) or not 0 < len(vals) <= MAX_IN:
                raise QueryError(f"where[{i}]: values is a list of 1 to {MAX_IN}")
            c["values"] = [_scalar(v, f"where[{i}]") for v in vals]
        elif op not in ("is null", "is not null"):
            c["value"] = _scalar(w.get("value"), f"where[{i}]")
        where.append(c)
    order = []
    for o in q.get("order_by") or []:
        name, desc = (o, False) if isinstance(o, str) else (None, False)
        if isinstance(o, str) and o.startswith("-"):
            name, desc = o[1:], True
        if isinstance(o, dict):
            name, desc = o.get("name"), str(o.get("dir") or "asc").lower() == "desc"
        out_names = {*metrics, *group, *([grain] if grain else [])}
        if name == grain:
            name = "time"
        if name not in out_names | {"time"}:
            raise QueryError(f"order_by: {name!r} is not a metric or group_by of this query")
        order.append((name, "desc" if desc else "asc"))
    limit = q.get("limit", DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise QueryError(f"limit: 1 to {MAX_LIMIT}")
    return {"metrics": metrics, "group_by": group, "where": where, "grain": grain,
            "since": _when(q.get("since"), "since"), "until": _when(q.get("until"), "until"),
            "order_by": order, "limit": limit}


def run(layer: Layer, ro, q: dict, max_rows: int = MAX_LIMIT) -> dict:
    """Compile and run: rows plus the SQL, parameters and definitions."""
    c = layer.compile(q)
    cols, rows, cut = ro.run(c["sql"], min(max_rows, c["query"]["limit"]), c["params"])
    return {"columns": cols, "rows": [list(r) for r in rows], "more": cut, "sql": c["sql"], "params": c["params"],
            "definitions": c["definitions"], "query": c["query"]}
