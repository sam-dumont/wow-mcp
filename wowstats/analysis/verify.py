"""Checks the analysis' findings by running their SQL again, renders their
text from the re-run values, and screens the lines meant for the shared
page.

A finding's numbers never come from the model's prose: its `claim` names
the columns of its query's first row (`{col}`, `{col:dur}`...), and the
text is filled from the re-run. `expect` is what the model saw: a re-run
that gives other numbers drops the finding, and so does a literal number in
the text, a placeholder its query doesn't return, or a `holds` condition
that is false.

The query must read the character's play history: `SELECT 3 AS n` re-runs
to 3 every time, so a constant passed the re-run while saying nothing about
the data (external review of 0.7.0-rc.5: "You died {n} times." kept with 3,
five deaths on record). That closes this one hole only: a query that reads a
table can still compute the wrong thing, which no re-run catches.
"""

from __future__ import annotations

import ast
import math
import re
from datetime import datetime

from .sql import QueryError, ReadOnly

REL_TOL = 0.02
ABS_TOL = 0.51
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::([a-z0-9]+))?\}")
# Literal numbers allowed in a claim's own words: small ones (levels, "top 3").
LITERAL_MAX = 100
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def fmt(v, spec: str | None) -> str:
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


def render(text: str, row: dict) -> str:
    return PLACEHOLDER.sub(lambda m: fmt(row.get(m.group(1)), m.group(2)), text or "")


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


def action_of(f: dict, key: str, row: dict) -> tuple[dict | None, dict | None, str]:
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
        if literal_numbers(text):
            return None, None, f"{key}: number written into action.{p} instead of a placeholder"
        missing = [m.group(1) for m in PLACEHOLDER.finditer(text) if m.group(1) not in row]
        if missing:
            return None, None, f"{key}: action.{p} names columns the query doesn't return: {', '.join(missing)}"
    if _words(tpl["do"]) < 3:
        return None, None, (f"{key}: action.do isn't concrete: say what to do and with what (an item, a quest, a "
                            "place, a skill), or send action null to keep it as an observation")
    if _words(tpl["next_step"]) < 3:
        return None, None, f"{key}: action.next_step is missing: the first thing to do, where"
    return {p: render(t, row) for p, t in tpl.items()}, tpl, ""


def literal_numbers(text: str) -> list[str]:
    bare = PLACEHOLDER.sub("", text or "")
    return [n for n in re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?", bare) if float(n.replace(",", ".")) > LITERAL_MAX]


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
    """The play-history tables the runner's last query read: the prep's game
    tables (wowstats.analysis.TABLES) and the semantic layer's views over them."""
    from . import TABLES

    read = getattr(ro, "last_read", None) or set()
    return {t for t in read if TABLES.get(t) == "game" or t.startswith("sem_")}


def check(f: dict, ro: ReadOnly, kb_ids=None) -> tuple[dict | None, str]:
    """(the finding with `row` and rendered `text`, "") or (None, why)."""
    if not isinstance(f, dict):
        return None, "not an object"
    key = str(f.get("key") or "").strip().lower()
    if not KEY_RE.match(key):
        return None, f"bad key {key!r}"
    sql_text = str(f.get("sql") or "")
    params = f.get("sql_params") if isinstance(f.get("sql_params"), dict) else None
    try:
        row = ro.first_row(sql_text, params)
    except QueryError as e:
        return None, f"{key}: query failed on re-run: {e}"
    if not history_read(ro):
        return None, f"{key}: {NO_HISTORY}"
    for part in ("title", "claim"):
        if literal_numbers(str(f.get(part) or "")):
            return None, f"{key}: number written into the {part} instead of a placeholder"
        missing = [m.group(1) for m in PLACEHOLDER.finditer(str(f.get(part) or "")) if m.group(1) not in row]
        if missing:
            return None, f"{key}: {part} names columns the query doesn't return: {', '.join(missing)}"
    action, action_tpl, why = action_of(f, key, row)
    if why:
        return None, why
    for part, text in (("title", f.get("title")), ("claim", f.get("claim")),
                       *((f"action.{p}", t) for p, t in (action_tpl or {}).items())):
        if gendered(str(text or "")):
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
    cites = [str(c) for c in f.get("kb") or [] if isinstance(c, str)]
    if kb_ids is not None:
        cites = [c for c in cites if kb_ids(c)]

    def num_of(name):
        v = row.get(name) if isinstance(name, str) else None
        return float(v) if isinstance(v, (int, float)) else 0.0

    out = {
        "key": key, "title": render(str(f.get("title") or key), row)[:120],
        "text": render(str(f.get("claim") or ""), row)[:1200],
        "title_template": str(f.get("title") or key)[:200], "claim_template": str(f.get("claim") or "")[:1500],
        # A metric query's SQL is the compiler's, kept whole so the recheck runs it as judged.
        "sql": sql_text if params else sql_text[:6000], "sql_params": params,
        "metric_query": f.get("metric_query") if params is not None else None,
        "engine": getattr(ro, "engine", "sqlite"), "expect": {k: row[k] for k in exp}, "holds": cond or None,
        "kb": cites[:12], "effect_col": f.get("effect") if isinstance(f.get("effect"), str) else None,
        "sample_col": f.get("sample") if isinstance(f.get("sample"), str) else None,
        "relevance": max(1, min(3, int(f.get("relevance") or 1))) if str(f.get("relevance") or "1").isdigit() else 1,
        "row": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()},
        "action": action, "action_template": action_tpl, "observation": action is None,
    }
    out["effect"] = abs(num_of(out["effect_col"]))
    out["sample"] = abs(num_of(out["sample_col"])) or 1.0
    return out, ""


def recheck(stored: dict, ro: ReadOnly) -> tuple[dict | None, str]:
    """A kept finding on today's data: the query runs, its `holds` is still
    true; the text is rendered again from today's numbers (no `expect`:
    the numbers are allowed to move)."""
    try:
        row = ro.first_row(stored["sql"], stored.get("sql_params"))
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
    tpl = stored.get("action_template") or {}
    for text in (stored.get("title_template"), stored.get("claim_template"), *tpl.values()):
        if any(m.group(1) not in row for m in PLACEHOLDER.finditer(text or "")):
            return None, "a placeholder is gone"
    upd = dict(stored)
    upd["row"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}
    upd["title"] = render(stored["title_template"], row)[:120]
    upd["text"] = render(stored["claim_template"], row)[:1200]
    if tpl:
        upd["action"] = {p: render(t, row) for p, t in tpl.items()}
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
