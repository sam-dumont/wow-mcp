"""Checks the analysis' findings by running their SQL again, renders their
text from the re-run values, and screens the lines meant for the shared
page.

A finding's measured numbers never come from the model's prose: its title,
claim and action name the query's result instead, as typed placeholders
(`{col}`, `{col:dur}`, `{col:.1f}` over the first row, `{rows[2].col}` for a
cell of a later row), and the text is rendered from the re-run, at accept
time and again at every recheck. Any other number in the prose (a level, a
top-N, a patch) is declared in the finding's `constants`, each with its
kind. The judge refuses a number that is neither, a constant that equals a
placeholder's value (a measure written by hand) or contradicts it (a
constant labelled like the column a placeholder reads, with another value),
a placeholder its query doesn't return, a re-run that disagrees with
`expect`, and a `holds` condition that is false (external review,
2026-11-22: "You died 3 times." was kept next to a query that counted 5,
because bare numbers up to 100 passed).

The query must read the character's play history: `SELECT 3 AS n` re-runs
to 3 every time, so a constant passed the re-run while saying nothing about
the data (external review of 0.7.0-rc.5: "You died {n} times." kept with 3,
five deaths on record). That closes this one hole only: a query that reads a
table can still compute the wrong thing, which no re-run catches, and a
number spelled in words ("three") isn't a number to the judge.
"""

from __future__ import annotations

import ast
import math
import re
from datetime import datetime

from .sql import QueryError, ReadOnly

REL_TOL = 0.02
ABS_TOL = 0.51
# A placeholder: a column of the first row, or a cell of a later one
# (rows[i].col), with an optional type (dur, pct, int, 1f or .1f).
PLACEHOLDER = re.compile(r"\{((?:rows\[\d{1,2}\]\.)?[A-Za-z_][A-Za-z0-9_]*)(?::(\.?[a-z0-9]+))?\}")
CELL = re.compile(r"rows\[(\d{1,2})\]\.([A-Za-z_][A-Za-z0-9_]*)")
# The shared page's nudges may name small numbers typed by hand (a level,
# "top 3"); a finding may not (CONSTANT_KINDS below).
LITERAL_MAX = 100
NUMBER = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+(?![\d.,])|\d+(?:[.,]\d+)?)")
# What a number written into a finding's prose may be when the query didn't
# measure it: declared in `constants`, each with one of these kinds.
CONSTANT_KINDS = {
    "level": "a level the query filters on (\"at level 30\": WHERE level = 30)",
    "top-n": "how many of a ranked list the query keeps (\"the top 3\": LIMIT 3)",
    "threshold": "a cut-off the query applies (\"sessions over 2 hours\": WHERE online_s > 2 * 3600)",
    "game-rule": "a fixed rule of the game from a knowledge base document the finding cites in `kb`",
    "patch": "a game version or patch (\"patch 1.12\")",
    "group-size": "a group or raid size (\"a 5-player group\")",
    "ordinal": "a position in a sequence that isn't measured (\"the 2nd boss\")",
}
# These kinds are the query's own parameters: the value must be written in
# its SQL (or bound in its metric_query). "You died 3 times." declared as
# level 3 is refused for that: the deaths query filters on no level.
IN_QUERY = ("level", "top-n", "threshold")
MAX_CONSTANTS = 12
# The rows a cell placeholder may reach (rows[0] to rows[19]).
MAX_CELL_ROWS = 20
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def fmt(v, spec: str | None) -> str:
    spec = spec[1:] if spec and spec.startswith(".") else spec
    if v is None:
        return "n/a"
    if isinstance(v, str):
        return v
    f = float(v)
    if spec == "dur":
        s = int(round(f))
        h, m = divmod(abs(s) // 60, 60)
        sign = "-" if s < 0 else ""
        return f"{sign}{h}h{m:02d}" if h else f"{sign}{m} min" if m else f"{sign}{abs(s)} s"
    if spec == "pct":
        return f"{f * 100:.0f}%" if abs(f) <= 1.5 else f"{f:.0f}%"
    if spec == "int":
        return f"{int(round(f)):,}".replace(",", " ")
    if spec and re.fullmatch(r"\d+f", spec):
        return f"{f:.{int(spec[:-1])}f}"
    if f.is_integer() or abs(f) >= 100:
        return f"{int(round(f)):,}".replace(",", " ")
    return f"{f:.2g}" if abs(f) < 1 else f"{f:.1f}"


_GONE = object()


def value(ref: str, row: dict, rows: list[dict] | None = None):
    """A placeholder's value: `col` from the first row, `rows[i].col` from
    row i. _GONE when the result has no such column or row."""
    m = CELL.fullmatch(ref)
    if m is None:
        return row.get(ref, _GONE)
    i = int(m.group(1))
    have = rows if rows is not None else [row]
    return have[i].get(m.group(2), _GONE) if i < len(have) else _GONE


def missing(text: str, row: dict, rows: list[dict] | None = None) -> list[str]:
    """The placeholders in `text` the result doesn't hold."""
    return [m.group(1) for m in PLACEHOLDER.finditer(text or "") if value(m.group(1), row, rows) is _GONE]


def cell_rows(*texts) -> int:
    """How many rows the placeholders in `texts` reach (1: the first row only)."""
    return max([1] + [int(c.group(1)) + 1 for t in texts for m in PLACEHOLDER.finditer(t or "")
                      for c in [CELL.fullmatch(m.group(1))] if c])


def render(text: str, row: dict, rows: list[dict] | None = None) -> str:
    def one(m):
        v = value(m.group(1), row, rows)
        return fmt(None if v is _GONE else v, m.group(2))

    return PLACEHOLDER.sub(one, text or "")


def result(ro, sql: str, params: dict | None, n: int) -> tuple[dict, list[dict]]:
    """The first row as a dict, and the first `n` rows when a placeholder
    reaches past it (cells). Raises QueryError when there is no row."""
    if n <= 1:
        row = ro.first_row(sql, params)
        return row, [row]
    if params is not None:
        cols, got, _ = ro.run(sql, min(n, MAX_CELL_ROWS), params)
    else:
        cols, got, _ = ro.run(sql, min(n, MAX_CELL_ROWS))
    if not got:
        raise QueryError("no rows")
    rows = [dict(zip(cols, r, strict=False)) for r in got]
    return rows[0], rows


def _num(text: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", text):  # 28,000: a thousands separator
        return float(text.replace(",", ""))
    return float(text.replace(",", "."))


# Digits the prose writes that aren't a quantity, for new proposals and kept
# findings alike (prose_numbers): a calendar date, a clock time, an
# identifier (a guide key like forever/alliance/30-40/01-hillsbrad, a file
# name, item_id=6948) and an item, quest, spell or zone id. A range ("levels
# 23-33"), a unit ("100g", "4x", "2h"), a group size ("5-player") or a
# number dressed up ("`3`", "#3", "3/5") stays a number.
_MONTH = (r"(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|"
          r"apr|jun|jul|aug|sep|sept|oct|nov|dec)")
NOT_QUANTITY = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?)?\b"
    rf"|\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}}\b)?"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b\.?(?:,?\s+\d{{4}}\b)?"
    rf"|\b{_MONTH}\s+\d{{4}}\b"
    r"|(?<![\w.:])(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?![\w:])|(?<=\}):[0-5]\d(?![\w:])"
    r"|\b\d{1,2}\s*(?:am|pm)\b",
    re.I)
_ID_BEFORE = re.compile(r"(?:\b(?:id|ids)\s*[:=#]?|#|\b(?:item|quest|spell|npc|zone|map|achievement|faction)"
                        r"\s*(?:id\s*)?[:#=]?)\s*$", re.I)
_ID_LIST = re.compile(r"^\s*(?:,|and|or|&|/)\s*(?:and\s+|or\s+)?$", re.I)
_EDGE = "()[]{}<>\"'`*\u201c\u201d\u2018\u2019,;!?"


def _identifier(token: str) -> bool:
    """A token that names something: it has a letter and a path, key or
    file shape. Without a letter ("3/5", "#3", "_3_") it's a number."""
    return re.search(r"[A-Za-z]", token) is not None and (
        re.search(r"[/\\_=#@]|\.[A-Za-z]|[A-Za-z]:\d|^0\d+-[A-Za-z]", token) is not None or token.count("-") >= 2)


def prose_numbers(text: str) -> list[tuple[int, int, str]]:
    """The quantities `text` writes, as (start, end, number), outside its
    placeholders and NOT_QUANTITY. The one tokenizer of a finding's prose,
    for a new proposal (check) and a kept one (unbound, the migration)."""
    text = text or ""
    masked = NOT_QUANTITY.sub(lambda m: " " * len(m.group(0)), text)
    masked = PLACEHOLDER.sub(lambda m: " " * len(m.group(0)), masked)
    out, last_id = [], None
    for m in NUMBER.finditer(masked):
        a, b = m.start(), m.end()
        while a > 0 and not masked[a - 1].isspace():
            a -= 1
        while b < len(masked) and not masked[b].isspace():
            b += 1
        token = masked[a:b].strip(_EDGE).rstrip(".:")
        if _identifier(token):
            continue
        lead = masked[max(0, m.start() - 24):m.start()]
        if len(m.group(0)) >= 3 and (_ID_BEFORE.search(lead) or (
                last_id is not None and _ID_LIST.match(masked[last_id:m.start()]))):
            last_id = m.end()
            continue
        out.append((m.start(), m.end(), m.group(0)))
    return out


def bare_numbers(text: str) -> list[str]:
    """The quantities written into `text` outside its placeholders (prose_numbers)."""
    return [n for _, _, n in prose_numbers(text)]


def constants_of(f: dict) -> tuple[list[dict], str]:
    """The finding's declared constants ([{value, label}]), or why they
    can't be read."""
    raw = f.get("constants")
    if raw in (None, "", []):
        return [], ""
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except ValueError:
            return [], "constants must be a list of {value, label}"
    if not isinstance(raw, list) or len(raw) > MAX_CONSTANTS:
        return [], f"constants must be a list of at most {MAX_CONSTANTS} {{value, label}}"
    out = []
    for c in raw:
        if not isinstance(c, dict):
            return [], "each constant is an object {value, label}"
        v, label = c.get("value"), str(c.get("label") or "").strip().lower().replace("_", "-").replace(" ", "-")
        if isinstance(v, str):
            try:
                v = _num(v.strip())
            except ValueError:
                v = None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            return [], f"constant {c.get('value')!r}: value must be a number"
        if label not in CONSTANT_KINDS:
            return [], (f"constant {v:g}: label {c.get('label')!r} isn't one of {', '.join(CONSTANT_KINDS)}. A "
                        "count, a time, a share or any other thing the data measures is a placeholder, never a "
                        "constant")
        out.append({"value": int(v) if float(v).is_integer() else float(v), "label": label})
    return out, ""


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def undeclared(text: str, constants: list[dict]) -> list[str]:
    """The numbers in `text` that are neither a placeholder nor a declared constant."""
    return [n for n in bare_numbers(text) if not any(_same(_num(n), float(c["value"])) for c in constants)]


def _undeclared_why(name: str, bad: list[str]) -> str:
    return (f"{', '.join(dict.fromkeys(bad))} in the {name} is neither a placeholder nor a declared "
            "constant: bind what the query measures ({col}, {col:dur}, {rows[1].col}), and declare a "
            "number it doesn't measure in `constants` ({value, label}: " + ", ".join(CONSTANT_KINDS) + ")")


def numbers_why(parts: dict[str, str], constants: list[dict]) -> str:
    """Why the prose types a number that is neither a placeholder nor a
    declared constant ("" when it doesn't). `parts`: {name: template}."""
    for name, text in parts.items():
        bad = undeclared(text, constants)
        if bad:
            return _undeclared_why(name, bad)
    return ""


def used_constants(parts: dict[str, str], constants: list[dict]) -> list[dict]:
    """The constants the prose actually types."""
    return [c for c in constants if any(_same(_num(n), float(c["value"])) for t in parts.values()
                                        for n in bare_numbers(t))]


# What a query's SQL holds that isn't a numeric literal: comments, string
# literals ('2026-11-01', '' escaped) and quoted identifiers ("col 2").
SQL_NOT_NUMERIC = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", re.S)
SQL_NUMBER = re.compile(r"(?<![\w.])(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?![\w.])")


def sql_numbers(sql_text: str, params: dict | None) -> set[float]:
    """The numeric literals a query writes (`level = 32`, `LIMIT 3`,
    `2 * 3600`), as numbers, plus the numbers bound in its params. A digit
    inside a string, a comment or an identifier isn't one, and `IN (30,31)`
    is 30 and 31."""
    out = {float(n) for n in SQL_NUMBER.findall(SQL_NOT_NUMERIC.sub(" ", sql_text or ""))}
    for v in (params or {}).values():
        vals = v if isinstance(v, (list, tuple)) else [v]
        out |= {float(x) for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool)}
    return out


def constant_why(c: dict, parts: dict[str, str], in_query: set[float], cites: list[str],
                 row: dict | None, rows: list[dict] | None = None) -> str:
    """Why one constant the prose types can't stand ("" when it can): it
    equals a placeholder's value (a measure typed by hand), contradicts one
    (labelled like the column the placeholder reads, another value), is a
    level, top-N or threshold the query doesn't apply (`in_query`:
    sql_numbers), or a game rule no cited document backs. Without a row the
    placeholder comparison is skipped."""
    for name, text in parts.items():
        for m in PLACEHOLDER.finditer(text or "") if row is not None else ():
            ref = m.group(1)
            v = value(ref, row, rows)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            col = (CELL.fullmatch(ref).group(2) if CELL.fullmatch(ref) else ref).lower().replace("_", "-")
            shown = fmt(v, m.group(2))
            if _same(float(v), float(c["value"])) or shown == f"{c['value']:g}":
                return (f"constant {c['value']:g} ({c['label']}) equals {{{ref}}} in the {name}: it is "
                        "measured, so write the placeholder and leave it out of `constants`")
            if c["label"] == col:
                return (f"constant {c['value']:g} ({c['label']}) contradicts {{{ref}}} in the {name}, which "
                        f"the query gives as {shown}")
    if c["label"] in IN_QUERY and not any(_same(float(c["value"]), q) for q in in_query):
        return (f"constant {c['value']:g} ({c['label']}) isn't in the query: a {c['label']} is a parameter "
                "the query applies (WHERE, LIMIT), written in its SQL. A number the query counts or sums is "
                "a placeholder")
    if c["label"] == "game-rule" and not cites:
        return (f"constant {c['value']:g} (game-rule): cite in `kb` the knowledge base document the rule "
                "comes from")
    return ""


def refused_numbers(parts: dict[str, str], constants: list[dict], sql_text: str, params: dict | None,
                    cites: list[str], row: dict | None, rows: list[dict] | None = None) -> dict[str, str]:
    """{number: why} for every number the prose types that can't stand, {}
    when each is a placeholder or a constant that passes constant_why. The
    one rule for a new proposal (check) and a kept finding (unbound): a
    number declared by several constants is refused when any of them is."""
    out: dict[str, str] = {}
    for name, text in parts.items():
        bad = undeclared(text, constants)
        for n in bad:
            out.setdefault(n, _undeclared_why(name, bad))
    in_query = sql_numbers(sql_text, params)
    for c in used_constants(parts, constants):
        why = constant_why(c, parts, in_query, cites, row, rows)
        if why:
            for n in (n for t in parts.values() for n in bare_numbers(t) if _same(_num(n), float(c["value"]))):
                out.setdefault(n, why)
    return out


# Owner, 2026-11-18: text about a character is gender-neutral ("the
# character", the name, or "you"), never he or she.
GENDERED = re.compile(r"\b(he|she|him|his|her|hers|himself|herself)\b", re.I)
# Owner, 2026-11-18: everything shown must be actionable. A finding's
# action: what to do, where, what blocks it, the next step. One without an
# action is kept as an observation, shown apart.
ACTION_PARTS = ("do", "where", "blocked_by", "next_step")


def gendered(text: str) -> list[str]:
    return GENDERED.findall(PLACEHOLDER.sub("", text or ""))


def _words(text: str) -> int:
    return len(re.findall(r"[^\W\d_]{2,}", PLACEHOLDER.sub("x", text or "")))


def action_of(f: dict, key: str, row: dict, rows: list[dict] | None = None
              ) -> tuple[dict | None, dict | None, str]:
    """(rendered action, its templates, "") or (None, None, "") for an
    observation, or (None, None, why) when the action is there but not
    concrete enough to act on."""
    act = f.get("action")
    if act is None or (isinstance(act, dict) and not str(act.get("do") or "").strip()):
        return None, None, ""
    if not isinstance(act, dict):
        return None, None, f"{key}: action must be an object (do, where, blocked_by, next_step) or null"
    tpl = {p: str(act.get(p) or "").strip()[:300] for p in ACTION_PARTS}
    for p, text in tpl.items():
        gone = missing(text, row, rows)
        if gone:
            return None, None, f"{key}: action.{p} names columns the query doesn't return: {', '.join(gone)}"
    if _words(tpl["do"]) < 3:
        return None, None, (f"{key}: action.do isn't concrete: say what to do and with what (an item, a quest, a "
                            "place, a skill), or send action null to keep it as an observation")
    if _words(tpl["next_step"]) < 3:
        return None, None, f"{key}: action.next_step is missing: the first thing to do, where"
    return {p: render(t, row, rows) for p, t in tpl.items()}, tpl, ""


def literal_numbers(text: str) -> list[str]:
    """Numbers above LITERAL_MAX typed into a nudge or pattern line (their
    own rule: a finding's prose goes through refused_numbers)."""
    return [n for n in bare_numbers(text) if _num(n) > LITERAL_MAX]


def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(float(a)) or math.isnan(float(b)):
            return False
        return abs(a - b) <= max(ABS_TOL, REL_TOL * max(abs(a), abs(b)))
    try:
        return close(float(a), float(b))
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


_OPS = {ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b, ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def holds(expr: str, row: dict) -> bool:
    """A small condition over the row's columns: comparisons, and/or/not,
    numbers, + - * /. Anything else raises ValueError."""
    tree = ast.parse(expr, mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return not ev(n.operand)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, right in zip(n.ops, n.comparators, strict=True):
                r = ev(right)
                if type(op) not in _OPS or left is None or r is None or not _OPS[type(op)](left, r):
                    return False
                left = r
            return True
        if isinstance(n, ast.BinOp) and type(n.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div):
            a, b = ev(n.left), ev(n.right)
            if a is None or b is None:
                return None
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            return a / b if b else None
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name):
            if n.id not in row:
                raise ValueError(f"no column {n.id}")
            return row[n.id]
        raise ValueError("only comparisons of columns and numbers")

    return bool(ev(tree))


NO_HISTORY = ("the query reads none of the character's tables (a constant, or a FROM on the documentation "
              "tables only): it isn't evidence. Select the numbers from the play history, or cite a "
              "query_metric call (list_metrics has deaths, kills, time played and more)")


def history_read(ro) -> set[str]:
    """The play-history tables the runner's last query stood on: the prep's
    game tables (wowstats.analysis.TABLES) it read, directly or through any
    view over them (sem_*, per_*), as semantic.reads resolves them."""
    from . import TABLES, semantic

    read = getattr(ro, "last_read", None) or set()
    return {t for t in semantic.reads(read) if TABLES[t] == "game"}


# The formats that write a unit, and the semantic layer's units they fit
# (owner, 2026-11-23: "{n:pct}" was kept on a plain count, rendered "2%").
# A value whose unit the judge can't tell gets neither: an alias used to
# lose it (`played_s AS n` kept as "7100%", pre-release check 2026-11-23).
FORMAT_UNITS = {
    "pct": ({"percent", "share", "ratio", "xp/xp"}, "a share (0 to 1) or a percent",
            "select the share itself (one count over another) as a column"),
    "dur": ({"seconds"}, "a duration in seconds", "select the time in seconds as a column"),
}


def column_units(metric_query, sql: str) -> dict[str, str | None]:
    """{column: unit, None when unknown} for what the evidence returns: the
    metrics a metric_query names (and their `_evidence` counts), else each
    column of the raw query as its expression gives it (lineage.units: an
    alias, a cast, a sum or a ratio keep the unit of the columns they read)."""
    from . import lineage, semantic

    if isinstance(metric_query, dict):
        units = {m["name"]: m.get("unit") for m in semantic.catalogue()["metrics"]}
        out = {}
        for n in metric_query.get("metrics") or []:
            out[n] = units.get(n)
            out[f"{n}_evidence"] = "observations"
        return out
    return lineage.units(sql)


def units_why(parts: dict[str, str], units: dict[str, str | None]) -> str:
    """Why a placeholder's format doesn't fit its column's unit, or the unit
    can't be told ("" when every one fits)."""
    for name, text in parts.items():
        for m in PLACEHOLDER.finditer(text or ""):
            spec = (m.group(2) or "").lstrip(".")
            ref = m.group(1)
            col = CELL.fullmatch(ref).group(2) if CELL.fullmatch(ref) else ref
            if spec not in FORMAT_UNITS:
                continue
            unit = units.get(col)
            if unit is None:
                return (f"{{{ref}:{spec}}} in the {name} formats {col}, and the judge can't tell its unit from "
                        f"the query: {spec} is for {FORMAT_UNITS[spec][1]}. Write {{{ref}}} (or {{{ref}:int}}), "
                        f"or {FORMAT_UNITS[spec][2]} (a column of a per_* view, a *_s column, or one over another)")
            if unit in FORMAT_UNITS[spec][0]:
                continue
            fits = [s for s, (us, *_) in FORMAT_UNITS.items() if unit in us]
            fix = f"write {{{ref}:{fits[0]}}}" if fits else f"write {{{ref}}} (or {{{ref}:int}})"
            return (f"{{{ref}:{spec}}} in the {name} formats {col}, which is in {unit}: {spec} is for "
                    f"{FORMAT_UNITS[spec][1]}. {fix[0].upper()}{fix[1:]}, or {FORMAT_UNITS[spec][2]}")
    return ""


def _prose(f: dict) -> dict[str, str]:
    """A proposal's prose by part: title, claim and the action's parts."""
    act = f.get("action") if isinstance(f.get("action"), dict) else {}
    if not str(act.get("do") or "").strip():  # an observation: its action is shown nowhere
        act = {}
    return {"title": str(f.get("title") or ""), "claim": str(f.get("claim") or ""),
            **{f"action.{p}": str(act.get(p) or "").strip()[:300] for p in ACTION_PARTS if act.get(p)}}


def templates(stored: dict) -> dict[str, str]:
    """A kept finding's prose templates by part."""
    return {"title": str(stored.get("title_template") or ""), "claim": str(stored.get("claim_template") or ""),
            **{f"action.{p}": str(t or "") for p, t in (stored.get("action_template") or {}).items()}}


def _rounded(row: dict) -> dict:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}


def check(f: dict, ro: ReadOnly, kb_ids=None) -> tuple[dict | None, str]:
    """(the finding with `row` and rendered `text`, "") or (None, why)."""
    if not isinstance(f, dict):
        return None, "not an object"
    key = str(f.get("key") or "").strip().lower()
    if not KEY_RE.match(key):
        return None, f"bad key {key!r}"
    constants, why = constants_of(f)
    if why:
        return None, f"{key}: {why}"
    prose = _prose(f)
    # Before the query: a number the prose types and nothing declares is
    # refused whatever the query returns.
    why = numbers_why(prose, constants)
    if why:
        return None, f"{key}: {why}"
    sql_text = str(f.get("sql") or "")
    params = f.get("sql_params") if isinstance(f.get("sql_params"), dict) else None
    try:
        row, rows = result(ro, sql_text, params, cell_rows(*prose.values()))
    except QueryError as e:
        return None, f"{key}: query failed on re-run: {e}"
    if not history_read(ro):
        return None, f"{key}: {NO_HISTORY}"
    for part in ("title", "claim"):
        gone = missing(prose[part], row, rows)
        if gone:
            return None, f"{key}: {part} names columns the query doesn't return: {', '.join(gone)}"
    action, action_tpl, why = action_of(f, key, row, rows)
    if why:
        return None, why
    cites = [str(c) for c in f.get("kb") or [] if isinstance(c, str)]
    if kb_ids is not None:
        cites = [c for c in cites if kb_ids(c)]
    refused = refused_numbers(prose, constants, sql_text, params, cites, row, rows)
    if refused:
        return None, f"{key}: {next(iter(refused.values()))}"
    why = units_why(prose, column_units(f.get("metric_query") if params is not None else None, sql_text))
    if why:
        return None, f"{key}: {why}"
    for part, text in prose.items():
        if gendered(text):
            return None, (f"{key}: {part} says he or she: write \"the character\", the name or \"you\" "
                          "(for anyone else, their name)")
    exp = f.get("expect") or {}
    if not isinstance(exp, dict) or not exp:
        return None, f"{key}: no expect"
    for col, want in exp.items():
        if col not in row:
            return None, f"{key}: expect names {col}, which the query doesn't return"
        if not close(row[col], want):
            return None, f"{key}: {col} is {row[col]!r} on re-run, the proposal said {want!r}"
    cond = str(f.get("holds") or "").strip()
    if cond:
        try:
            if not holds(cond, row):
                return None, f"{key}: holds ({cond}) is false"
        except (ValueError, SyntaxError, ZeroDivisionError, TypeError) as e:
            return None, f"{key}: holds unreadable: {e}"
    used = used_constants(prose, constants)

    def num_of(name):
        v = row.get(name) if isinstance(name, str) else None
        return float(v) if isinstance(v, (int, float)) else 0.0

    n_rows = cell_rows(*prose.values())
    out = {
        "key": key, "title": render(prose["title"] or key, row, rows)[:120],
        "text": render(prose["claim"], row, rows)[:1200],
        "title_template": (prose["title"] or key)[:200], "claim_template": prose["claim"][:1500],
        # A metric query's SQL is the compiler's, kept whole so the recheck runs it as judged.
        "sql": sql_text if params else sql_text[:6000], "sql_params": params,
        "metric_query": f.get("metric_query") if params is not None else None,
        "engine": getattr(ro, "engine", "sqlite"), "expect": {k: row[k] for k in exp}, "holds": cond or None,
        "kb": cites[:12], "effect_col": f.get("effect") if isinstance(f.get("effect"), str) else None,
        "sample_col": f.get("sample") if isinstance(f.get("sample"), str) else None,
        "relevance": max(1, min(3, int(f.get("relevance") or 1))) if str(f.get("relevance") or "1").isdigit() else 1,
        "row": _rounded(row), "action": action, "action_template": action_tpl, "observation": action is None,
        # The numbers the prose names without measuring them, with their kind.
        "constants": used, "contract": CONTRACT,
    }
    if n_rows > 1:
        out["rows"] = [_rounded(r) for r in rows[:n_rows]]
    out["effect"] = abs(num_of(out["effect_col"]))
    out["sample"] = abs(num_of(out["sample_col"])) or 1.0
    return out, ""


# The prose contract a kept finding was judged under: 2 binds every
# measured number to the query and declares the rest (2026-11-22). A finding
# kept before it is migrated by `unbound` (wowstats.analysis.migrate).
CONTRACT = 2
UNBOUND_WHY = "its text has numbers neither bound to its query nor declared"


def unbound(stored: dict) -> list[str]:
    """The numbers a kept finding's prose types that can't stand: [] for one
    that meets today's contract. A finding kept before it had no
    `constants`, so a number its own SQL writes as a numeric literal
    (`level = 32`, `LIMIT 3`) counts as declared, as a level, a top-N and a
    threshold at once, and then goes through exactly the rule a new proposal
    does (refused_numbers): one that equals or contradicts a placeholder on
    the row it was kept with under any of those readings stays unbound, and
    so does every number when no row was kept (owner, 2026-11-22: 883 of
    2,202 kept findings in the eval states were flagged, mostly typed
    levels)."""
    consts = [c for c in stored.get("constants") or [] if isinstance(c, dict) and isinstance(c.get("label"), str)
              and isinstance(c.get("value"), (int, float)) and not isinstance(c.get("value"), bool)]
    tpls = templates(stored)
    sql_text = str(stored.get("sql") or "")
    params = stored.get("sql_params") if isinstance(stored.get("sql_params"), dict) else None
    row = stored.get("row") if isinstance(stored.get("row"), dict) else None
    rows = stored.get("rows") if isinstance(stored.get("rows"), list) else None
    implied = []
    if row is not None:
        in_query = sql_numbers(sql_text, params)
        typed = dict.fromkeys(_num(n) for t in tpls.values() for n in undeclared(t, consts))
        implied = [{"value": v, "label": k} for v in typed if any(_same(v, q) for q in in_query) for k in IN_QUERY]
    cites = [str(c) for c in stored.get("kb") or [] if isinstance(c, str)]
    return list(refused_numbers(tpls, consts + implied, sql_text, params, cites, row, rows))


# A number typed after "level" is a level: it binds to a level column only.
_LEVEL_BEFORE = re.compile(r"\b(?:levels?|lvl)\s*$", re.I)
# How a bound number may be written: the first of these whose rendering of
# the result value is exactly what the prose typed.
BIND_SPECS = (None, "int", ".0f", ".1f", ".2f")


def bind_results(stored: dict, numbers: list[str]) -> tuple[dict[str, str], list[dict]]:
    """A kept finding's templates with each of `numbers` that equals exactly
    one value of its own kept result (`row`, then `rows[i]` for a later row)
    written as that value's placeholder, so a recheck renders it again; the
    format is the first of BIND_SPECS that renders the value as typed, so
    today's text doesn't change. A number that matches no value, or several
    (two columns with the same value), is left as typed, and so is one typed
    after "level" whose only match isn't a level column ("At level 32"
    next to an XP-per-kill of 32). Returns (the
    templates by part, [{number, placeholder}] bound)."""
    tpls = templates(stored)
    row = stored.get("row") if isinstance(stored.get("row"), dict) else {}
    rows = stored.get("rows") if isinstance(stored.get("rows"), list) else []
    cells = [(c, v) for c, v in row.items()]
    cells += [(f"rows[{i}].{c}", v) for i, r in enumerate(rows[1:MAX_CELL_ROWS], 1) if isinstance(r, dict)
              for c, v in r.items()]
    bound = []
    for n in numbers:
        as_level = any(_LEVEL_BEFORE.search(t[max(0, a - 12):a]) for t in tpls.values()
                       for a, _, x in prose_numbers(t) if x == n)
        hits = {}
        for ref, v in cells:
            col = CELL.fullmatch(ref).group(2) if CELL.fullmatch(ref) else ref
            if as_level and not re.search(r"level|lvl", col, re.I):
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not _same(float(v), _num(n)):
                continue
            spec = next((s for s in BIND_SPECS if fmt(v, s) == n), False)
            if spec is not False and PLACEHOLDER.fullmatch("{" + ref + "}"):
                hits[ref] = spec
        if len(hits) != 1:
            continue
        ref, spec = next(iter(hits.items()))
        ph = "{" + ref + (f":{spec}" if spec else "") + "}"
        for part, t in tpls.items():
            for a, b, x in reversed(prose_numbers(t)):
                if x == n:
                    t = t[:a] + ph + t[b:]
            tpls[part] = t
        bound.append({"number": n, "placeholder": ph})
    return tpls, bound


def set_templates(stored: dict, tpls: dict[str, str]) -> None:
    """Writes templates by part (templates) back into a kept finding."""
    stored["title_template"], stored["claim_template"] = tpls["title"], tpls["claim"]
    act = {k.split(".", 1)[1]: t for k, t in tpls.items() if k.startswith("action.")}
    if act:
        stored["action_template"] = {**(stored.get("action_template") or {}), **act}


def recheck(stored: dict, ro: ReadOnly) -> tuple[dict | None, str]:
    """A kept finding on today's data: the query runs, its `holds` is still
    true; the text is rendered again from today's numbers (no `expect`:
    the numbers are allowed to move)."""
    tpls = templates(stored)
    n_rows = cell_rows(*tpls.values())
    try:
        row, rows = result(ro, stored["sql"], stored.get("sql_params"), n_rows)
    except QueryError as e:
        return None, f"query failed: {e}"
    if not history_read(ro):  # kept before the judge asked for it
        return None, "the query reads no play history"
    if stored.get("holds"):
        try:
            if not holds(stored["holds"], row):
                return None, "no longer holds"
        except (ValueError, SyntaxError, ZeroDivisionError, TypeError) as e:
            return None, f"holds unreadable: {e}"
    if any(missing(t, row, rows) for t in tpls.values()):
        return None, "a placeholder is gone"
    tpl = stored.get("action_template") or {}
    upd = dict(stored)
    upd["row"] = _rounded(row)
    if n_rows > 1:
        upd["rows"] = [_rounded(r) for r in rows[:n_rows]]
    upd["title"] = render(stored["title_template"], row, rows)[:120]
    upd["text"] = render(stored["claim_template"], row, rows)[:1200]
    if tpl:
        upd["action"] = {p: render(t, row, rows) for p, t in tpl.items()}
    upd["effect"] = abs(float(row.get(stored.get("effect_col") or "", 0) or 0)) \
        if isinstance(row.get(stored.get("effect_col") or ""), (int, float)) else stored.get("effect", 0.0)
    upd["sample"] = abs(float(row.get(stored.get("sample_col") or "", 0) or 0)) or 1.0 \
        if isinstance(row.get(stored.get("sample_col") or ""), (int, float)) else stored.get("sample", 1.0)
    return upd, ""


# ---------------------------------------------------------------- the shared page's lines

CLOCK = re.compile(r"\b([01]?\d|2[0-3])\s*[:h.]\s*[0-5]\d\b|\b\d{1,2}\s*(am|pm)\b|\b(noon|midnight)\b", re.I)
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|"
                  r"nov|dec)[a-z]*\.?\s+\d{1,2}\b|\b\d{1,2}(st|nd|rd|th)?\s+(of\s+)?(jan|feb|mar|apr|may|jun|jul|"
                  r"aug|sep|oct|nov|dec)[a-z]*\b|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                  r"yesterday|today|tonight|last night)\b", re.I)


def shareable(text: str, banned: set[str]) -> tuple[bool, str]:
    """Whether a line may go on the shared (/zogzog/) page: no clock time,
    no date or weekday, no place, creature or player name from the
    character's own records, no number at all, no he or she."""
    t = text or ""
    if CLOCK.search(t):
        return False, "clock time"
    if DATE.search(t):
        return False, "date"
    if re.search(r"\d", t):
        return False, "a number"
    # The records say nothing about who plays (GENDERED above): a line
    # kept by an older run that guessed is caught here too.
    if gendered(t):
        return False, "a gendered word"
    low = t.lower()
    for name in banned:
        n = name.strip().lower()
        if len(n) >= 4 and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", low):
            return False, "a name"
    return True, ""


def banned_names(db, kb=None, extra: set[str] | None = None) -> set[str]:
    """Zones, subzones, creatures and dungeons named in this character's
    records, and other players' names (passed in: they never enter the
    database)."""
    out = banned_people(db, kb, extra)
    for (z,) in db.execute("SELECT DISTINCT zone FROM records UNION SELECT DISTINCT subzone FROM records "
                           "UNION SELECT DISTINCT name FROM dungeon_runs UNION SELECT DISTINCT zone FROM positions"):
        if z:
            out.add(str(z))
    return out


def banned_people(db, kb=None, extra: set[str] | None = None) -> set[str]:
    """Other players' names (passed in: they never enter the database) and
    the creatures named in this character's records: what /zogzog/ never
    shows (its killers among them). Places are allowed there."""
    out = set(extra or ())
    if kb is not None:
        ids = [f"npc:{i}" for (i,) in db.execute("SELECT DISTINCT npc_id FROM records WHERE npc_id IS NOT NULL")]
        for i in range(0, len(ids), 10):
            out.update(d["title"] for d in kb.get(ids[i:i + 10], cap=10) if d.get("title"))
    return out


# "You are in Duskwood", "currently at the inn": where the character is now,
# which /zogzog/ never says.
NOW_AT = re.compile(r"\b(you are|you're|currently|right now|at the moment|still)\s+(in|at|on|near)\b", re.I)
SHARED_NUDGE = ("text", "where", "blocked_by", "next_step", "verdict", "at_level")


def shareable_action(n: dict, banned: set[str]) -> tuple[bool, str]:
    """Whether a nudge may go on the shared (/zogzog/) page as an action:
    what to do, where, what blocks it, the next step. Zones, dungeons, items
    and levels are allowed there (the tier shows them); clock times, dates,
    he or she, other players, creatures from the records, where the
    character is now, and a number above 100 typed by hand are not."""
    if _words(str(n.get("next_step") or "")) < 3:
        return False, "no next step"
    for part in ("text", "where", "blocked_by", "next_step"):
        t = str(n.get(part) or "")
        if CLOCK.search(t):
            return False, f"{part}: clock time"
        if DATE.search(t):
            return False, f"{part}: date"
        if gendered(t):
            return False, f"{part}: a gendered word"
        if literal_numbers(t):
            return False, f"{part}: a number above {LITERAL_MAX}"
        if NOW_AT.search(t):
            return False, f"{part}: where the character is now"
        low = t.lower()
        for name in banned:
            nm = name.strip().lower()
            if len(nm) >= 4 and re.search(r"(?<!\w)" + re.escape(nm) + r"(?!\w)", low):
                return False, f"{part}: a player or creature name"
    return True, ""


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
