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
import copy
import functools
import math
import re
import secrets
from datetime import date, datetime
from pathlib import Path

from wowstats import timebounds

from .sql import QueryError

MODEL_FILE = Path(__file__).with_name("semantic.yaml")
GRAINS = {"day": "TIME_GRAIN_DAY", "week": "TIME_GRAIN_WEEK", "month": "TIME_GRAIN_MONTH"}
OPS = ("=", "!=", "<", "<=", ">", ">=", "in", "not in", "is null", "is not null")
# Six dimensions: an upgrade row is slot, item, its id and how it fits; its
# source is the text, the kind and the page (the brief's gear_fit reads both).
MAX_METRICS, MAX_GROUP, MAX_WHERE, MAX_IN, MAX_TEXT = 8, 6, 8, 50, 200
DEFAULT_LIMIT, MAX_LIMIT = 50, 500
# The app's own readers (the plan's facts, the tools) read every key: run()'s max_rows.
EVERY_ROW = 100_000
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

# The views the model reads (semantic.yaml `table`): the prep's table with its
# time in the player's timezone, and the character's name and class. The
# calendar time per level is the prep's own column (levels.calendar_s).
_WHO = ("(SELECT value FROM meta WHERE key = 'char') AS character, "
        "(SELECT value FROM meta WHERE key = 'class') AS char_class")
VIEWS = {
    "sem_levels": "SELECT l.*, {ts:l.ding_t} AS at_local, {who} FROM levels l",
    # The guide's and the step's titles from the route reference (route_steps,
    # one row per guide and step: MIN only guards against a duplicate).
    "sem_timeline": ("SELECT t.*, rs.guide_title, rs.step_title, {ts:t.t0} AS at_local, {who} FROM timeline t "
                     "LEFT JOIN (SELECT guide, step, MIN(guide_title) AS guide_title, MIN(title) AS step_title "
                     "FROM route_steps GROUP BY guide, step) rs ON rs.guide = t.guide AND rs.step = t.step"),
    # level_whole: the record's level has a whole split (played time, not
    # partial), for the ratios that pair records with the level's split.
    # elite: a kill's creature an elite, rare elite or boss (the record's
    # classification), for the kill XP a run would pay at another level
    # (wowstats.plan.dungeongain). run_start: the dungeon run the record
    # falls in (start to leave), as dungeon_runs names it. skill_id and
    # skill_rank: a skill record's own (records.extra), for the Riding rank
    # the mount goal reads.
    # at_guide, at_step: the guide step in force at the record, a step (or
    # skip) record's own, else the one of the online interval the record closes
    # ((t0, t1]: the intervals of a session follow each other, so one at
    # most). step_jump: the steps a step record passed over, counted from the
    # guide's previous step record (picks included), 2 to 25 steps ahead
    # (wowstats.mcp.tools t_guide_steps' rule: further is a new start).
    # The interval is found with an ASOF join (the latest interval starting
    # before the record, kept when it hasn't ended): one row per record by
    # construction, and a range join here made every query joining records
    # to another model 30 times slower (0.01 s to 0.33 s on a 10k-record
    # history; the views per grain hit their 5 s cap in a parallel test
    # run). run_start is a join for the same reason (a correlated subquery
    # beside the other joins).
    "sem_records": ("SELECT r.*, CASE WHEN l.played_s > 0 AND l.partial = 0 THEN 1 ELSE 0 END AS level_whole, "
                    "CASE WHEN json_extract_string(r.extra, '$.classification') IN ('e', 'R', 'b') THEN 1 ELSE 0 "
                    "END AS elite, TRY_CAST(json_extract_string(r.extra, '$.skill_id') AS BIGINT) AS skill_id, "
                    "TRY_CAST(json_extract_string(r.extra, '$.rank') AS BIGINT) AS skill_rank, ru.run_start, "
                    "CASE WHEN r.kind IN ('step', 'skip') THEN r.guide WHEN r.t <= tl.t1 THEN tl.guide END "
                    "AS at_guide, "
                    "CASE WHEN r.kind IN ('step', 'skip') THEN r.step WHEN r.t <= tl.t1 THEN tl.step END AS at_step, "
                    "COALESCE(j.jump, 0) AS step_jump, {ts:r.t} AS at_local, {who} FROM records r "
                    "LEFT JOIN levels l ON l.level = r.level "
                    "ASOF LEFT JOIN timeline tl ON r.t > tl.t0 "
                    "LEFT JOIN (SELECT rec_id, CASE WHEN COALESCE(reason, '') <> 'pick' AND d BETWEEN 2 AND 25 "
                    "THEN d - 1 END AS jump FROM (SELECT rec_id, reason, step - LAG(step) OVER (PARTITION BY guide "
                    "ORDER BY t, rec_id) AS d FROM records WHERE kind = 'step' AND step IS NOT NULL)) j "
                    "ON j.rec_id = r.rec_id "
                    "LEFT JOIN (SELECT r2.rec_id, MIN(d.start_t) AS run_start FROM records r2 JOIN dungeon_runs d "
                    "ON r2.t BETWEEN d.start_t AND d.left_t GROUP BY r2.rec_id) ru ON ru.rec_id = r.rec_id"),
    # The XP bar's readings with the guide step of the interval each closes.
    "sem_xp": ("SELECT x.*, CASE WHEN x.t <= tl.t1 THEN tl.guide END AS guide, "
               "CASE WHEN x.t <= tl.t1 THEN tl.step END AS step, {ts:x.t} AS at_local, {who} FROM xp x "
               "ASOF LEFT JOIN timeline tl ON x.t > tl.t0"),
    "sem_sessions": "SELECT *, {ts:start_t} AS at_local, {who} FROM sessions",
    # A zone record's time (stores.zone_time), kept by the prep as a `zone`
    # span: its detail is "zone|subzone".
    "sem_zone_time": ("SELECT s.*, NULLIF(split_part(s.detail, '|', 1), '') AS zone, {ts:s.t0} AS at_local, {who} "
                      "FROM spans s WHERE s.kind = 'zone'"),
    # trip_id: the trip around the run (its dungeon, the run starting inside it).
    "sem_dungeon_runs": ("SELECT d.*, (SELECT MIN(t.trip_id) FROM dungeon_trips t WHERE t.dungeon = d.name AND "
                         "d.start_t BETWEEN t.start_t AND t.end_t) AS trip_id, {ts:d.start_t} AS at_local, {who} "
                         "FROM dungeon_runs d"),
    "sem_dungeon_trips": "SELECT *, {ts:start_t} AS at_local, {who} FROM dungeon_trips",
    "sem_snapshots": "SELECT *, {ts:t} AS at_local, {who} FROM snapshots",
    # complete_t: the newest complete record of the quest between its accept
    # (any time before, when the accept wasn't seen) and its close.
    "sem_quests": ("SELECT *, {ts:at_t} AS at_local, {who} FROM (SELECT q.*, "
                   "COALESCE(q.closed_t, q.accepted_t) AS at_t, "
                   "COALESCE(q.quest_name, 'Quest ' || CAST(q.quest_id AS VARCHAR)) AS quest, "
                   "(SELECT MAX(c.t) FROM records c WHERE c.kind = 'complete' AND c.quest_id = q.quest_id "
                   "AND (q.accepted_t IS NULL OR c.t >= q.accepted_t) AND (q.closed_t IS NULL OR c.t <= q.closed_t)) "
                   "AS complete_t FROM quests q)"),
    "sem_gold_moves": "SELECT *, {ts:t0} AS at_local, {who} FROM gold_moves",
    # Each interval between two XP records where the rested pool went down,
    # with the kill XP inside it outside dungeon trips (kill_xp) and inside
    # them (trip_kill_xp): what rested_per_kill_xp pairs.
    "sem_rested": ("SELECT x.t, x.level, x.zone, x.session_id, x.pr - x.r AS rested_used, "
                   "CAST((SELECT COALESCE(SUM(k.xp_reward), 0) FROM records k WHERE k.kind = 'kill' AND "
                   "k.dungeon_trip_id IS NULL AND k.t > x.pt AND k.t <= x.t) AS BIGINT) AS kill_xp, "
                   "CAST((SELECT COALESCE(SUM(k.xp_reward), 0) FROM records k WHERE k.kind = 'kill' AND "
                   "k.dungeon_trip_id IS NOT NULL AND k.t > x.pt AND k.t <= x.t) AS BIGINT) AS trip_kill_xp, "
                   "{ts:x.t} AS at_local, {who} FROM (SELECT t, level, zone, session_id, rested_xp AS r, "
                   "LAG(t) OVER (ORDER BY t) AS pt, LAG(rested_xp) OVER (ORDER BY t) AS pr FROM xp) x "
                   "WHERE x.pr > x.r"),
    # The loot with the record it came in: its trip, level and zone.
    "sem_loot": ("SELECT li.item_id, li.count AS n, li.t, r.dungeon_trip_id, r.level, r.zone, r.session_id, "
                 "{ts:li.t} AS at_local, {who} FROM loot_items li JOIN records r ON r.rec_id = li.rec_id"),
    # The gear advice's upgrades (one row per item and source) with each
    # item's place in its slot among those wearable now: 1 is the biggest
    # gain. Rows of one item share its rank (same gain, same id).
    "sem_upgrades": ("SELECT u.*, CASE WHEN u.now = 1 THEN DENSE_RANK() OVER (PARTITION BY u.slot_name, u.now "
                     "ORDER BY u.gain DESC, u.item_id) END AS rank_now, {who} FROM upgrade_candidates u"),
    # The worn items on the newest sheet, as the gear advice scores them.
    # weak_rank 1 is the item an upgrade replaces: the slot's only one, or
    # the weaker of two rings or trinkets (wowstats.gear.bis_badge's rule).
    "sem_gear": ("SELECT *, ROW_NUMBER() OVER (PARTITION BY slot_name ORDER BY score ASC NULLS LAST, slot) "
                 "AS weak_rank, {who} FROM gear WHERE name IS NOT NULL"),
}
CROSS_KEY = "cross_model_metrics"
# The views per grain (semantic.yaml `views`): per_level, per_session...,
# compiled from the metrics by view_sql and added beside VIEWS by prepare.
VIEWS_KEY = "views"
# The store models (semantic.yaml `store_models`): what a tool reads from the
# store itself rather than the prep (other players, the player's price scans,
# talents, the groups in a dungeon run). Each declares its relation's
# `columns`; `read` loads the rows the tool fetched for one answer into an
# in-memory DuckDB for that call only and runs the question there, with the
# same compiler and contract. Nothing is kept: no copy, no cache of rows,
# the store's own retention untouched (Battle.net's 30 days above all).
STORE_KEY = "store_models"
OURS = (CROSS_KEY, VIEWS_KEY, STORE_KEY)

# The fields a metric's metadata carries beside kind, unit and caveats: the
# caveats the compiler enforces instead of only printing (review 4, 2026-11-21).
# aggregation: how the rows become the number (max: the largest value in
# the group, for a per-item fact the model repeats on each of the item's rows;
# min: the smallest, a price's low or the first of a window; earliest: the
# value on the group's first row in time, as latest is on its last; count_distinct:
# how many different values, a player's days; top: the value on the slot's
# top row, its best upgrade or weakest worn item). time_meaning: what the model's
# `time` is for this metric. evidence_basis: how the number was obtained.
# eligibility: {where, means}, the rows every side of a two-sided metric reads
# (a predicate over the model's row; for a cross-model metric one per model).
# min_evidence: {count, at_least, means}: fewer observations in a row than
# at_least and the value is NULL, with the count beside it (<name>_evidence).
AGGREGATIONS = ("sum", "ratio_of_sums", "latest", "earliest", "endpoint_difference", "max", "min", "count_distinct",
                "top")
TIME_MEANINGS = ("interval_start", "level_completion", "snapshot_observation", "quest_closure", "event")
EVIDENCE_BASES = ("observed", "reconstructed", "allocated", "inferred", "estimated")
FIELDS = ("aggregation", "time_meaning", "evidence_basis", "eligibility", "min_evidence")
_AGG_CALL = re.compile(r"\.(sum|count|nunique|mean|min|max|argmax|argmin)\(")


def prepare(con, tz: str | None = None) -> None:
    """Add the model's views to a DuckDB copy of the prep (before any query),
    then the views per grain compiled from them (view_sql)."""
    from . import prep

    zone = (tz or prep.TZ_NAME).replace("'", "''")
    for name, body in VIEWS.items():
        sql = re.sub(r"\{ts:([a-z_.0-9]+)\}", lambda m: f"timezone('{zone}', to_timestamp({m.group(1)}))", body)
        con.execute(f"CREATE VIEW {name} AS {sql.replace('{who}', _WHO)}")
    for name, sql in view_sql(con).items():
        con.execute(f"CREATE VIEW {name} AS {sql}")


def signature(con, models: dict | None = None) -> tuple:
    """The model's views' columns and types on this copy (or `models`'
    tables): all the compiler reads from it, so the key its output is
    cached under."""
    cur = con.cursor()
    return tuple((cfg["table"], tuple((c[0], c[1]) for c in cur.execute(f'DESCRIBE "{cfg["table"]}"').fetchall()))
                 for cfg in (spec() if models is None else models).values())


def view_sql(con) -> dict[str, str]:
    """Each view per grain as the SQL the compiler writes for its metrics at
    its entity (no limit, no parameters), for this copy's column types."""
    return dict(_view_sql(signature(con)))


@functools.lru_cache(maxsize=8)
def _view_sql(sig: tuple) -> tuple:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # BSL's own deprecation notes
        layer = Layer(sig=sig)
    out = []
    for name, v in views().items():
        c = layer.compile({"metrics": v["metrics"], "group_by": [v["entity"]], "where": v["where"]},
                          attrs=v["dimensions"], view=True)
        if c["params"]:
            raise ValueError(f"view {name}: a view binds no value (its where tests for null only)")
        out.append((name, c["sql"]))
    return tuple(out)


def local(db, tz: str | None = None):
    """The layer and a read-only runner on an in-process DuckDB copy of a
    prep build, rows included: for a caller holding the SQLite build only
    (the seeded evaluation's facts, tests). The workspace keeps its own."""
    import warnings

    from . import TABLES, duck

    con = duck.mirror(db, TABLES)
    prepare(con, tz)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # BSL's own deprecation notes
        layer = Layer(con)
    return layer, duck.ReadOnlyDuck(con)


@functools.cache
def _file() -> dict:
    from boring_semantic_layer.io import read_yaml_file

    return read_yaml_file(str(MODEL_FILE))


@functools.cache
def spec() -> dict:
    """semantic.yaml's models, as BSL reads them (our cross-model section left
    out), plus the measures the compiler adds: for a metric with eligibility,
    each measure it reads restricted to the eligible rows (`<measure>__for_<metric>`);
    for one with min_evidence, its evidence count (`<metric>__evidence`).
    Neither is in the catalogue: a caller can't name them."""
    models = _expand({k: v for k, v in _file().items() if k not in OURS})
    for name, m in (_file().get(CROSS_KEY) or {}).items():
        where = ((m.get("metadata") or {}).get("eligibility") or {}).get("where") or {}
        for model, pred in where.items():
            cfg = models[model]
            for base in re.findall(r"[a-z_][a-z0-9_]*", str(m.get("expr") or "")):
                if base in (cfg.get("measures") or {}):
                    _paired(cfg, name, f"_.{base}", pred)
    return models


def _expand(models: dict) -> dict:
    """A copy of the models with the compiler's own measures added (spec)."""
    models = copy.deepcopy(models)
    for cfg in models.values():
        for section in ("measures", "calculated_measures"):
            for name, m in list((cfg.get(section) or {}).items()):
                meta = m.get("metadata") or {}
                if meta.get("eligibility") and section == "calculated_measures":
                    m["expr"] = _paired(cfg, name, m["expr"], meta["eligibility"]["where"])
                if meta.get("min_evidence"):
                    cfg["measures"][f"{name}__evidence"] = {
                        "expr": meta["min_evidence"]["count"], "description": f"{name}: its evidence count."}
    return models


@functools.cache
def store_spec() -> dict:
    """semantic.yaml's store models as BSL reads them (their `columns` left
    out), with the compiler's own measures added as in spec()."""
    return _expand({k: {f: v for f, v in cfg.items() if f != "columns"}
                    for k, cfg in (_file().get(STORE_KEY) or {}).items()})


def _paired(cfg: dict, metric: str, expr: str, pred: str) -> str:
    """`expr` (a calculated measure) reading `<m>__for_<metric>` instead of
    each measure m it names, a copy of m restricted to the rows `pred` holds:
    both sides of a ratio or difference count the same rows."""
    measures = cfg.setdefault("measures", {})
    calc = cfg.get("calculated_measures") or {}

    def swap(mo):
        base = mo.group(1)
        if base in calc:
            raise ValueError(f"{metric}: eligibility reads measures only, {base} is calculated")
        if base not in measures:
            return mo.group(0)
        part = f"{base}__for_{metric}"
        measures[part] = {"expr": _with_where(measures[base]["expr"], pred),
                          "description": f"{base}, the rows {metric} compares."}
        return f"_.{part}"

    return re.sub(r"_\.([a-z_][a-z0-9_]*)\b(?!\()", swap, expr)


def _with_where(expr: str, pred: str) -> str:
    """Each aggregation call of a measure's expression with `pred` added to
    its where: `_.x.sum()` -> `_.x.sum(where=(pred))`, an existing where kept
    and both required."""
    calls = list(_AGG_CALL.finditer(expr))
    if not calls:
        raise ValueError(f"no aggregation to restrict in {expr!r}")
    for m in reversed(calls):
        start = m.end()
        depth, i = 1, start
        while depth:
            depth += {"(": 1, ")": -1}.get(expr[i], 0)
            i += 1
        inner = expr[start:i - 1].strip()
        if not inner:
            new = f"where=({pred})"
        elif inner.startswith("where="):
            new = f"where=({pred}) & ({inner[6:]})"
        elif "where=" in inner:
            head, _, w = inner.partition("where=")
            new = f"{head}where=({pred}) & ({w})"
        else:
            new = f"{inner}, where=({pred})"
        expr = expr[:start] + new + expr[i - 1:]
    return expr


def check_metric(m: dict) -> None:
    """A metric's own fields, checked when the catalogue is built: a ratio or
    difference (two sides) has eligibility, an endpoint difference has
    min_evidence, and every value is one the compiler knows."""
    name = m["name"]
    if m.get("aggregation") not in AGGREGATIONS:
        raise ValueError(f"{name}: aggregation is one of {', '.join(AGGREGATIONS)}")
    tm = m.get("time_meaning")
    if not all(t in TIME_MEANINGS for t in (tm if isinstance(tm, list) else [tm])):
        raise ValueError(f"{name}: time_meaning is one of {', '.join(TIME_MEANINGS)}")
    if m.get("evidence_basis") not in EVIDENCE_BASES:
        raise ValueError(f"{name}: evidence_basis is one of {', '.join(EVIDENCE_BASES)}")
    two_sided = m["model"] == "cross" or m.get("calculated") or m["aggregation"] == "ratio_of_sums"
    el = m.get("eligibility")
    if two_sided and not (isinstance(el, dict) and el.get("where") and el.get("means")):
        raise ValueError(f"{name}: a metric read from two sides needs eligibility {{where, means}}")
    if m["model"] == "cross" and el and set(el["where"]) != set(m["models"]):
        raise ValueError(f"{name}: eligibility names a predicate for each of {', '.join(m['models'])}")
    ev = m.get("min_evidence")
    if m["aggregation"] == "endpoint_difference" and not ev:
        raise ValueError(f"{name}: an endpoint difference needs min_evidence")
    if ev and not (isinstance(ev, dict) and ev.get("count") and isinstance(ev.get("at_least"), int)
                   and ev["at_least"] >= 1 and ev.get("means")):
        raise ValueError(f"{name}: min_evidence is {{count, at_least, means}}")


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


def _listed(s: dict) -> tuple[list[dict], dict]:
    """The models' metrics and dimensions as written (catalogue)."""
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
                                **{f: meta.get(f) for f in FIELDS}, "calculated": section == "calculated_measures",
                                "expr": m.get("expr"), "group_by": sorted(own)})
    return metrics, dims


@functools.cache
def catalogue() -> dict:
    """list_metrics' answer: every metric with its definition, the
    dimensions it can be grouped and filtered by, and the time grains."""
    s = {k: v for k, v in _file().items() if k not in OURS}  # as written: no generated parts
    metrics, dims = _listed(s)
    owner = {m["name"]: m["model"] for m in metrics}
    for name, m in (_file().get(CROSS_KEY) or {}).items():
        meta = m.get("metadata") or {}
        _, used = _cross_terms(name, str(m.get("expr") or ""), owner)
        if any(next(x for x in metrics if x["name"] == u).get("min_evidence") for u in used):
            raise ValueError(f"{name}: a cross-model metric can't read a metric with min_evidence")
        models = list(dict.fromkeys(owner[u] for u in used))
        shared = set.intersection(*(set(s[mo].get("dimensions") or {}) for mo in models))
        if m.get("dimensions"):
            shared &= set(m["dimensions"])
        metrics.append({"name": name, "model": "cross", "models": models, "of": used, "kind": meta.get("kind"),
                        "unit": meta.get("unit"), "description": m.get("description"),
                        "caveats": meta.get("caveats"), **{f: meta.get(f) for f in FIELDS}, "calculated": True,
                        "expr": m.get("expr"), "group_by": sorted(shared)})
    for m in metrics:
        check_metric(m)
    return {"metrics": metrics, "dimensions": sorted(dims.values(), key=lambda d: d["name"]),
            "grains": list(GRAINS), "ops": list(OPS)}


TYPES = ("INTEGER", "REAL", "TEXT")


@functools.cache
def store_catalogue() -> dict:
    """The store models' metrics and dimensions, checked as catalogue()'s
    (each metric's contract fields) and named apart from them: a name means
    one thing across both. list_metrics lists them; their tools read them
    (read), query_metric doesn't (no prep holds their rows)."""
    raw = _file().get(STORE_KEY) or {}
    for model, cfg in raw.items():
        cols = cfg.get("columns") or {}
        if not cols or any(t not in TYPES for t in cols.values()):
            raise ValueError(f"store model {model}: columns {{name: {' | '.join(TYPES)}}}")
    metrics, dims = _listed(raw)
    taken = {m["name"] for m in catalogue()["metrics"]}
    for m in metrics:
        if m["name"] in taken:
            raise ValueError(f"{m['name']}: a store metric's name is its own, not a prep metric's")
        m["source"] = "store"
        check_metric(m)
    return {"metrics": metrics, "dimensions": sorted(dims.values(), key=lambda d: d["name"]),
            "grains": list(GRAINS), "ops": list(OPS)}


def read(model: str, rows: list[dict], q: dict) -> dict:
    """A store model's question over `rows`, the relation a tool read from
    the store for this one answer (one dict per row, the model's `columns`):
    loaded into an in-memory DuckDB (locked down, as the prep's copy) for
    this call only, compiled and run as query_metric runs a question, and
    closed. No row limit but the rows given: the tool reads every key."""
    import sqlite3

    from . import duck

    cfg = (_file().get(STORE_KEY) or {}).get(model)
    if cfg is None:
        raise QueryError(f"unknown store model {model!r}")
    cols = cfg["columns"]
    table = cfg["table"]
    db = sqlite3.connect(":memory:")
    con = None
    try:
        db.execute(f'CREATE TABLE "{table}" (' + ", ".join(f'"{c}" {t}' for c, t in cols.items()) + ")")
        db.executemany(f'INSERT INTO "{table}" VALUES (' + ",".join("?" * len(cols)) + ")",
                       [tuple(r.get(c) for c in cols) for r in rows])
        con = duck.mirror(db)
        models = {model: store_spec()[model]}
        layer = _store_layer(signature(con, models))
        c = layer.compile(q, max_limit=max(len(rows), 1))
        out_cols, got, _ = duck.ReadOnlyDuck(con).run(c["sql"], max(len(rows), 1), c["params"])
        return {"columns": out_cols, "rows": [dict(zip(out_cols, r, strict=True)) for r in got],
                "definitions": c["definitions"], "query": c["query"], "sql": c["sql"], "params": c["params"]}
    finally:
        if con is not None:
            con.close()
        db.close()


@functools.lru_cache(maxsize=32)
def _store_layer(sig: tuple):
    """The layer for one store model at these column types: no rows in it
    (ibis' unbound tables), so caching it keeps nothing of the data."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # BSL's own deprecation notes
        return Layer(sig=sig, store=True)


@functools.cache
def views() -> dict[str, dict]:
    """semantic.yaml's views per grain, checked: each metric can be grouped by
    the view's entity (else refused: a metric that can't be read at that
    grain is left out, never faked), each dimension belongs to one of the
    view's models, and the columns with what each one means. A metric's
    rules (eligibility, min_evidence, time_meaning) hold in the view: it is
    the same compiled measure."""
    from . import TABLES

    cat = catalogue()
    by_name = {m["name"]: m for m in cat["metrics"]}
    dims = {d["name"]: d for d in cat["dimensions"]}
    out = {}
    for name, v in (_file().get(VIEWS_KEY) or {}).items():
        if not re.match(r"^per_[a-z_]{2,30}$", name) or name in VIEWS or name in TABLES:
            raise ValueError(f"view {name}: a name per_<grain>, not a table's or a model view's")
        entity, metrics = v.get("entity"), list(v.get("metrics") or [])
        if entity not in dims or entity == "time":
            raise ValueError(f"view {name}: entity {entity!r} is not a dimension (time has grains: query_metric)")
        if not metrics or len(set(metrics)) != len(metrics):
            raise ValueError(f"view {name}: a list of distinct metrics")
        models: set[str] = set()
        for n in metrics:
            m = by_name.get(n)
            if m is None:
                raise ValueError(f"view {name}: {n!r} is not a metric")
            if entity not in m["group_by"]:
                raise ValueError(f"view {name}: {n} can't be read by {entity}: leave it out")
            models.update(m.get("models") or [m["model"]])
        attrs = list(v.get("dimensions") or [])
        for a in attrs:
            if a not in dims or a in (entity, "time") or not models & set(dims[a]["models"]):
                raise ValueError(f"view {name}: dimension {a!r} isn't one of its models'")
        where = [dict(w) for w in v.get("where") or []]
        if any(w.get("op") not in ("is null", "is not null") for w in where):
            raise ValueError(f"view {name}: where tests for null only (a view binds no value)")
        cols = [{"name": entity, "description": dims[entity]["description"], "grain": True}]
        cols += [{"name": a, "description": dims[a]["description"]} for a in attrs]
        for n in metrics:
            m = by_name[n]
            cols.append({"name": n, **{k: m.get(k) for k in ("kind", "unit", "description", "caveats", *FIELDS)}})
            if m.get("min_evidence"):
                cols.append({"name": f"{n}_evidence", "unit": "observations",
                             "description": f"How many observations {n} rests on: below "
                                            f"{m['min_evidence']['at_least']} it is NULL (0: none, unavailable)."})
        out[name] = {"name": name, "entity": entity, "description": v.get("description"), "metrics": metrics,
                     "dimensions": attrs, "where": where, "models": sorted(models), "columns": cols}
    return out


def views_text() -> str:
    """The views per grain for the schema listing (analysis_brief section
    schema) and play_sql: read first, the prep's tables after them."""
    out = ["# Views per grain (governed: read these first)", "",
           "Compiled from the semantic layer's metrics, one row per key (no fan-out). Each metric column is the "
           "list_metrics metric of that name, its rules included: a ratio reads its eligible rows, a metric with "
           "min_evidence is NULL below it with <name>_evidence beside it, and NULL is unknown, never 0. Join "
           "them on their key (level, session_id, zone, dungeon, trip, guide, quest_id, slot) with each other or a raw "
           "table."]
    # Each column is described once, in the first view that has it: the
    # listing has to fit the schema section's first page (12,000 characters)
    # with the raw tables' heading after it, and per_session and per_zone
    # repeat most of per_level's columns.
    seen: set[str] = set()
    for v in views().values():
        out += ["", f"## {v['name']}: one row per {v['entity']}", v["description"] or ""]
        again = []
        for c in v["columns"]:
            if c["name"] in seen:
                again.append(c["name"])
                continue
            seen.add(c["name"])
            unit = f" ({c['unit']})" if c.get("unit") else ""
            out.append(f"- {c['name']}{unit}: {c['description']}")
        if again:
            out.append(f"- also, as described above: {', '.join(again)}")
    return "\n".join(out)


def view_reads(name: str) -> set[str]:
    """The model views (sem_*) a view per grain reads."""
    spec_ = spec()
    return {spec_[mo]["table"] for mo in views()[name]["models"]}


def relations_in(sql: str) -> set[str]:
    """The relations a query's text names: prep tables, model views (sem_*)
    and views per grain, string literals left out. For SQL no runner has
    read (a kept finding's); a runner's `last_read` is exact."""
    from . import TABLES

    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", re.sub(r"'(?:[^']|'')*'", "''", sql or "")))
    return {w for w in words if w in TABLES or w in VIEWS or w in views()}


def reads(names) -> set[str]:
    """The prep tables (wowstats.analysis.TABLES) the relations `names` stand
    on, transitively: a prep table itself, a model view (sem_*) the tables
    and views its SQL names, a view per grain its models' views. The one
    dependency resolver: the run's coverage (drive._count_query), the
    judge's evidence test (verify.history_read) and the definition check
    (manifest.reads_scoring) all ask it, so a view declared in semantic.yaml
    is seen through everywhere (external review, 2026-11-22: each kept its
    own list of names, and none knew the per_* views). A name it doesn't
    know (a CTE, a store model) stands on nothing."""
    from . import TABLES

    per = views()
    out: set[str] = set()
    seen: set[str] = set()
    todo = list(names)
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        if n in TABLES:
            out.add(n)
        elif n in VIEWS:
            todo += relations_in(VIEWS[n]) - {n}
        elif n in per:
            todo += view_reads(n)
    return out


def _metric(name: str, cat: dict | None = None) -> dict:
    m = next((x for x in (cat or catalogue())["metrics"] if x["name"] == name), None)
    if m is None:
        raise QueryError(f"unknown metric {name!r}: list_metrics names them")
    return m


class Layer:
    """The model compiled against one character's DuckDB copy (its column
    types), built once per workspace."""

    def __init__(self, con=None, *, sig: tuple | None = None, store: bool = False):
        import ibis
        from boring_semantic_layer import from_config

        types = {"BIGINT": "int64", "INTEGER": "int64", "DOUBLE": "float64", "VARCHAR": "string",
                 "TIMESTAMP": "timestamp", "BOOLEAN": "boolean"}
        tables = {name: ibis.table({c: types.get(t, "string") for c, t in cols}, name=name)
                  for name, cols in (sig if sig is not None else signature(con))}
        # store: the store models whose tables `sig` names (read), else the prep's.
        models = {k: v for k, v in store_spec().items() if v["table"] in tables} if store else spec()
        self.cat = store_catalogue() if store else None
        self.models = from_config(models, tables=tables)

    def _one(self, model: str, measures: list[str], label: str, q: dict, values: list, attrs=()) -> str:
        """One model's part: its metrics aggregated to the question's
        group_by (and a view's dimensions this model has), filters on its own
        dimensions; columns named plainly."""
        import ibis

        sem = self.models[model]
        own = set(sem.get_dimensions())
        missing = [d for d in q["group_by"] + [w["dimension"] for w in q["where"]] if d not in own]
        if missing:
            raise QueryError(f"{label} can't be grouped or filtered by {missing[0]!r}: "
                             f"its dimensions are {', '.join(sorted(own))}")
        filters = []
        for w, v in zip(q["where"], values, strict=True):
            if w["op"] in ("is null", "is not null"):
                filters.append({"field": w["dimension"], "operator": w["op"]})
            elif w["op"] in ("in", "not in"):
                filters.append({"field": w["dimension"], "operator": w["op"], "values": v})
            else:
                filters.append({"field": w["dimension"], "operator": w["op"], "value": v})
        # Parsed datetimes, never caller text: the compiler writes them as
        # timestamp literals. The edges are wowstats.timebounds' rule.
        for op, value in timebounds.range_edges(q["since"], q["until"]):
            filters.append({"field": "time", "operator": op, "value": value})
        kw = {"time_grain": GRAINS[q["grain"]]} if q["grain"] else {}
        try:
            dims = q["group_by"] + [a for a in attrs if a in own]
            expr = sem.query(dimensions=dims or None, measures=measures,
                             filters=filters or None, **kw)
            return str(ibis.to_sql(expr.to_untagged(), dialect="duckdb"))
        except QueryError:
            raise
        except Exception as e:  # BSL's and ibis' own errors: the caller's mix doesn't fit the model
            raise QueryError(f"can't compile that: {str(e).splitlines()[0][:240]}") from None

    def compile(self, q: dict, attrs=(), view: bool = False, max_limit: int | None = None) -> dict:
        """The query checked and compiled: {sql, params, columns, definitions, query}.
        One model: its aggregate. Several: each model's aggregate, joined on
        the group_by columns (a full outer join, so a level with time but no
        kills keeps its row). For a view per grain (`view`): any number of
        metrics, no limit, and `attrs`, dimensions grouped in the models that
        have them and joined on group_by only (one value per key: the views'
        test checks no key comes out twice)."""
        q = normalise(q, max_metrics=None if view else MAX_METRICS, cat=self.cat, max_limit=max_limit)
        cat = self.cat

        def _m(n: str) -> dict:
            return _metric(n, cat)

        metrics = [_m(n) for n in q["metrics"]]
        # A cross-model metric is computed after the join from the models'
        # own metrics: those go into their models' parts (unshown if not asked).
        # Each metric's parts, as (model, measure): a cross metric with
        # eligibility reads its terms' paired copies (spec), a metric with
        # min_evidence its evidence count too.
        def parts_of(m: dict) -> dict[str, tuple[str, str]]:
            if m["model"] == "cross":
                tag = f"__for_{m['name']}" if m.get("eligibility") else ""
                return {n: (_m(n)["model"], n + tag) for n in m["of"]}
            own = {m["name"]: (m["model"], m["name"])}
            if m.get("min_evidence"):
                own["__evidence"] = (m["model"], f"{m['name']}__evidence")
            return own

        by_model: dict[str, list[str]] = {}
        label: dict[str, str] = {}
        for m in metrics:
            for model, measure in parts_of(m).values():
                if measure not in by_model.setdefault(model, []):
                    by_model[model].append(measure)
                label.setdefault(model, m["name"])
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
        parts = {model: self._one(model, ms, label[model], q, values, attrs) for model, ms in by_model.items()}
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

        def part_col(model: str, measure: str) -> str:
            return f"m{names.index(model)}.{_q(measure)}"

        sel = [col(d) for d in dims]
        columns = [out(d) for d in dims]
        for a in attrs:
            has = [i for i, mo in enumerate(names) if a in self.models[mo].get_dimensions()]
            cells = ", ".join(f"m{i}.{_q(a)}" for i in has)
            sel.append(f"{f'COALESCE({cells})' if len(has) > 1 else cells} AS {_q(a)}")
            columns.append(a)
        for m in metrics:
            p = parts_of(m)
            if m["model"] == "cross":
                tree, _ = _cross_terms(m["name"], m["expr"], {n: _m(n)["model"] for n in m["of"]})
                sel.append(f"{_sql_of(tree, lambda n, p=p: part_col(*p[n]))} AS {_q(m['name'])}")
            elif m.get("min_evidence"):
                # Too few observations: NULL, never the number they happen to
                # give (one snapshot's difference with itself is 0), and the
                # count beside it says why.
                ev = f"COALESCE({part_col(*p['__evidence'])}, 0)"
                need = int(m["min_evidence"]["at_least"])
                sel.append(f"CASE WHEN {ev} >= {need} THEN {part_col(*p[m['name']])} END AS {_q(m['name'])}")
                sel.append(f"{ev} AS {_q(m['name'] + '_evidence')}")
                columns.append(m["name"])
                columns.append(m["name"] + "_evidence")
                continue
            else:
                sel.append(f"{part_col(*p[m['name']])} AS {_q(m['name'])}")
            columns.append(m["name"])
        frm = "m0"
        for i in range(1, len(names)):
            # On the keys of everything joined before, not m0's alone: a level
            # m0 lacks would otherwise match nothing in m2 and split in two
            # (review 2026-11-21, A).
            on = " AND ".join(f"{key(d, i)} IS NOT DISTINCT FROM m{i}.{_q(d)}" for d in dims) or "TRUE"
            frm += f" FULL OUTER JOIN m{i} ON {on}"
        order = q["order_by"] or [(d, "asc") for d in dims]
        ob = ", ".join(f"{_q(out(n))} {d.upper()} NULLS LAST" for n, d in order)
        sql = f"WITH {ctes}\nSELECT {', '.join(sel)}\nFROM {frm}" + (f"\nORDER BY {ob}" if ob else "")
        if not view:
            sql += f"\nLIMIT {int(q['limit']) + 1}"  # one row past the limit: run() says `more` from it
        return {"sql": sql, "params": params, "columns": columns,
                "definitions": {m["name"]: {k: m[k] for k in ("kind", "unit", "description", "caveats", *FIELDS,
                                                             "model", "models", "of", "expr") if k in m and (
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


def normalise(q: dict, max_metrics: int | None = MAX_METRICS, cat: dict | None = None,
              max_limit: int | None = None) -> dict:
    """The query's shape checked, names against the closed lists: a QueryError
    says what's wrong. Values are only type-checked: they are bound later.
    max_metrics None: a view per grain's own compile, every metric it lists.
    cat: a store model's catalogue (read); max_limit: read's own row count."""
    if not isinstance(q, dict):
        raise QueryError("a query is an object: metrics, group_by, where, grain, order_by, limit")
    cat = cat or catalogue()
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

    metrics = names(q.get("metrics"), "metrics", known_m, max_metrics or len(known_m))
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
    top = max_limit or MAX_LIMIT
    limit = q.get("limit", top if max_limit else DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= top:
        raise QueryError(f"limit: 1 to {top}")
    return {"metrics": metrics, "group_by": group, "where": where, "grain": grain,
            "since": _when(q.get("since"), "since"), "until": _when(q.get("until"), "until"),
            "order_by": order, "limit": limit}


def run(layer: Layer, ro, q: dict, max_rows: int = MAX_LIMIT) -> dict:
    """Compile and run: rows plus the SQL, parameters and definitions. The SQL
    asks for one row past the limit, so `more` is true only when one exists.
    A metric with min_evidence gets an `evidence` entry: how many rows are
    measured, unknown (some evidence, not enough) and unavailable (none)."""
    # A reader of the app's own (not a caller's question) may ask for every
    # key: max_rows past MAX_LIMIT lifts the limit to it.
    c = layer.compile(q, max_limit=max_rows if max_rows > MAX_LIMIT else None)
    cols, rows, cut = ro.run(c["sql"], min(max_rows, c["query"]["limit"]), c["params"])
    out = {"columns": cols, "rows": [list(r) for r in rows], "more": cut, "sql": c["sql"], "params": c["params"],
           "definitions": c["definitions"], "query": c["query"]}
    evidence = {}
    for name, d in c["definitions"].items():
        ev = d.get("min_evidence")
        if not ev:
            continue
        i = cols.index(name + "_evidence")
        n = [r[i] or 0 for r in rows]
        evidence[name] = {"needs": ev["means"], "at_least": ev["at_least"],
                          "measured": sum(x >= ev["at_least"] for x in n),
                          "unknown": sum(0 < x < ev["at_least"] for x in n), "unavailable": sum(x == 0 for x in n),
                          "column": name + "_evidence"}
    if evidence:
        out["evidence"] = evidence
    return out


def rows(layer: Layer, ro, q: dict, max_rows: int = 100_000) -> list[dict]:
    """Every row of a question, as dicts: for the product's own readers (the
    brief's facts, the plan's inputs, the typed tools), which need all of a
    character's guide steps or quests where query_metric pages at
    MAX_LIMIT. Compiled exactly like query_metric (same metrics, rules and
    bound values), without the page limit; more than `max_rows` is refused
    rather than cut."""
    c = layer.compile(q, view=True)
    cols, got, cut = ro.run(c["sql"], max_rows, c["params"])
    if cut:
        raise QueryError(f"more than {max_rows} rows: group or filter further")
    return [dict(zip(cols, r, strict=True)) for r in got]
