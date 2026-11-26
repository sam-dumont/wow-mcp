"""The part of LogsQL the views use, run in Python over rows already read.

One character's events are read once into wowstats.eventcache; every
endpoint's query is then answered here instead of by VictoriaLogs. Only
what the code asks is supported: anything else raises Unsupported and the
caller asks VictoriaLogs (wowstats.stores.Store.vl), so an unusual query is
slower, never wrong. The SQLite event store (wowstats.eventstore) answers
every query with this engine and has no VictoriaLogs to ask: there the app's
queries must all fit (tests/test_eventstore.py).

Answers are shaped like VictoriaLogs' (tests/test_logsql.py asks both
engines the same queries over the same rows, in the version the compose kit
pins): every value a string, empty fields left out and a row left with none
not answered, stats numbers as VictoriaLogs prints them (a sum of no number
is NaN), `_time` buckets as 2026-11-12T10:38:00Z, row_max and uniq_values as
JSON strings, text in natural order (item 9 before item 10). Where
VictoriaLogs' own answer depends on how it stored the rows, the engine
leaves it to VictoriaLogs: a field mixing numbers and text in a sort, min,
max or row_max raises Unsupported when it runs (under `lenient()`, the
SQLite store's, it is ordered as numbers where both are, else as text).

Filters: `field:=v`, `field:in(a, b)`, `field:*`, `field:word`, `field:pre*`,
`_time:>T`, `_time:>=T`, `_time:<T`, `_time:[T1, T2]`, `_time:30d`, `-x`,
`!x`, `not x`, `(a or b)`, `a and b`, and the stream filter `{f="v", g="w"}`
(LOGS_FILTER's `{component="wow"}`). Pipes: `fields`, `rm`, `sort by (f [desc])`,
`limit`/`head`, `uniq by (...)`, `stats [by (f, _time:1m)] count(),
count_uniq(f), sum(f), min(f), max(f), uniq_values(f) [limit N],
row_max(f, a, ...)`.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .stores import parse_t


class Unsupported(ValueError):
    """Outside the subset: ask VictoriaLogs."""


_LENIENT: contextvars.ContextVar[bool] = contextvars.ContextVar("logsql_lenient", default=False)


@contextlib.contextmanager
def lenient():
    """Inside, a field mixing numbers and text is ordered anyway (_cmp:
    as numbers where both values are, else as text) instead of raising
    Unsupported: the SQLite store has no VictoriaLogs to leave it to."""
    tok = _LENIENT.set(True)
    try:
        yield
    finally:
        _LENIENT.reset(tok)


# ------------------------------------------------------------------ lexing

_WORD = re.compile(r"[^\s(),|:]+")
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_TIME = re.compile(r"[^\s(),|\[\]]+")


class _Lex:
    def __init__(self, s: str):
        self.s, self.i = s, 0

    def ws(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def peek(self, n: int = 1) -> str:
        self.ws()
        return self.s[self.i:self.i + n]

    def eat(self, tok: str) -> bool:
        self.ws()
        if self.s.startswith(tok, self.i):
            self.i += len(tok)
            return True
        return False

    def need(self, tok: str):
        if not self.eat(tok):
            raise Unsupported(f"expected {tok!r} at {self.s[self.i:self.i + 30]!r}")

    def at_keyword(self, kw: str) -> bool:
        """`kw` next, as a whole word."""
        self.ws()
        j = self.i + len(kw)
        return self.s[self.i:j].lower() == kw and (j >= len(self.s) or not (self.s[j].isalnum() or self.s[j] in "_-:"))

    def keyword(self, kw: str) -> bool:
        if self.at_keyword(kw):
            self.i += len(kw)
            return True
        return False

    def value(self) -> str:
        """A quoted string or a bare word."""
        self.ws()
        if self.s[self.i:self.i + 1] in ("'", "`"):
            # Read as a bare word, 'x' matched the quotes too: no row, silently.
            raise Unsupported("single-quoted or backquoted string")
        if self.i < len(self.s) and self.s[self.i] == '"':
            try:
                v, end = json.JSONDecoder().raw_decode(self.s, self.i)
            except ValueError as e:
                raise Unsupported("bad quoted string") from e
            self.i = end
            return v
        m = _WORD.match(self.s, self.i)
        if not m:
            raise Unsupported(f"expected a value at {self.s[self.i:self.i + 30]!r}")
        self.i = m.end()
        return m.group(0)

    def time_value(self) -> str:
        """A time (quoted or bare, colons and all) or a duration."""
        self.ws()
        if self.s[self.i:self.i + 1] == '"':
            return self.value()
        m = _TIME.match(self.s, self.i)
        if not m:
            raise Unsupported(f"expected a time at {self.s[self.i:self.i + 30]!r}")
        self.i = m.end()
        return m.group(0)

    def done(self) -> bool:
        self.ws()
        return self.i >= len(self.s)


def split_pipes(q: str) -> list[str]:
    """`filter | pipe | pipe`, split on the `|` outside quotes."""
    out, cur, i, inq = [], [], 0, False
    while i < len(q):
        c = q[i]
        if inq:
            cur.append(c)
            if c == "\\" and i + 1 < len(q):
                cur.append(q[i + 1])
                i += 1
            elif c == '"':
                inq = False
        elif c == '"':
            inq = True
            cur.append(c)
        elif c == "|":
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur).strip())
    return out


# ------------------------------------------------------------------ filters

_DUR = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)$")
_DUR_S = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400, "y": 365 * 86400}


def dur_s(s: str) -> float:
    m = _DUR.match(s)
    if not m:
        raise Unsupported(f"duration {s!r}")
    return float(m.group(1)) * _DUR_S[m.group(2)]


def _ts(s: str) -> datetime:
    t = parse_t(s)
    if t == datetime.min.replace(tzinfo=UTC):
        raise Unsupported(f"time {s!r}")
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _end_of(s: str) -> datetime:
    """An inclusive upper bound covers its whole last unit, as in VictoriaLogs:
    `_time:[a, 2026-11-12T10:13:54Z]` holds 10:13:54.7."""
    t = _ts(s)
    m = re.search(r"T\d\d:\d\d(?::\d\d(?:\.(\d+))?)?", s)
    if not m:
        raise Unsupported(f"time {s!r}")
    if ":" not in m.group(0)[4:]:
        step = timedelta(minutes=1)
    elif m.group(1) is None:
        step = timedelta(seconds=1)
    else:
        step = timedelta(seconds=10 ** -len(m.group(1)))
    return t + step - timedelta(microseconds=1)


def _boundary(v: str, i: int) -> bool:
    return i <= 0 or i >= len(v) or not (v[i - 1].isalnum() or v[i - 1] == "_")


def _word_at(v: str, w: str, prefix: bool) -> bool:
    """`w` in `v` starting at a word boundary (and ending at one, unless a prefix)."""
    if not w:
        return True
    i = v.find(w)
    while i >= 0:
        end = i + len(w)
        if _boundary(v, i) and (prefix or end >= len(v) or not (v[end].isalnum() or v[end] == "_")):
            return True
        i = v.find(w, i + 1)
    return False


@dataclass
class Term:
    field: str
    op: str  # eq, in, any, word, prefix, time
    values: tuple = ()
    lo: datetime | None = None  # time: inclusive bounds (None: open)
    hi: datetime | None = None
    lo_open: bool = False
    hi_open: bool = False
    rel_s: float | None = None  # time: the last rel_s seconds before now

    def match(self, r: dict, now: datetime) -> bool:
        if self.op == "time":
            t = parse_t(r.get("_time"))
            if self.rel_s is not None:
                return now - timedelta(seconds=self.rel_s) < t <= now
            if self.lo is not None and (t <= self.lo if self.lo_open else t < self.lo):
                return False
            return not (self.hi is not None and (t >= self.hi if self.hi_open else t > self.hi))
        v = r.get(self.field)
        v = "" if v is None else str(v)
        if self.op == "eq":
            return v == self.values[0]
        if self.op == "in":
            return v in self.values
        if self.op == "any":
            return v != ""
        if self.op == "word":
            return _word_at(v, self.values[0], False)
        return _word_at(v, self.values[0], True)


@dataclass
class Not:
    x: object

    def match(self, r, now):
        return not self.x.match(r, now)


@dataclass
class And:
    xs: list = field(default_factory=list)

    def match(self, r, now):
        return all(x.match(r, now) for x in self.xs)


@dataclass
class Or:
    xs: list

    def match(self, r, now):
        return any(x.match(r, now) for x in self.xs)


def _parse_or(lx: _Lex):
    xs = [_parse_and(lx)]
    while lx.keyword("or"):
        xs.append(_parse_and(lx))
    return xs[0] if len(xs) == 1 else Or(xs)


def _parse_and(lx: _Lex):
    xs = []
    while not lx.done() and lx.peek() != ")" and not lx.at_keyword("or"):
        if lx.keyword("and"):
            continue
        x = _parse_unary(lx)
        xs.extend(x.xs if isinstance(x, _Stream) else [x])
    if not xs:
        raise Unsupported("empty filter")
    return xs[0] if len(xs) == 1 else And(xs)


def _parse_unary(lx: _Lex):
    if lx.eat("-") or lx.eat("!") or lx.keyword("not"):
        return Not(_parse_unary(lx))
    if lx.eat("("):
        x = _parse_or(lx)
        lx.need(")")
        return x
    if lx.eat("*"):
        return And([])
    if lx.eat("{"):
        return _parse_stream(lx)
    return _parse_term(lx)


def _parse_stream(lx: _Lex):
    """`{f="v", g="w"}`: exact values of stream fields, which are fields of
    every row too (`f:="v" g:="w"`). Only `=`: no regexp, no `!=`."""
    terms = []
    while not lx.eat("}"):
        lx.ws()
        m = _FIELD.match(lx.s, lx.i)
        if not m:
            raise Unsupported("stream filter")
        lx.i = m.end()
        if not lx.eat("=") or lx.peek() in ("~", "!"):
            raise Unsupported("stream filter other than f=\"v\"")
        terms.append(Term(m.group(0), "eq", (lx.value(),)))
        if not lx.eat(","):
            lx.need("}")
            break
    if not terms:
        raise Unsupported("empty stream filter")
    return _Stream(terms)


class _Stream(And):
    """A stream filter's terms: part of the filter's top-level AND."""


def _parse_term(lx: _Lex) -> Term:
    lx.ws()
    m = _WORD.match(lx.s, lx.i)
    if not m or lx.s[m.end():m.end() + 1] != ":":
        raise Unsupported("a filter without a field")
    f = m.group(0)
    lx.i = m.end() + 1
    if f == "_time":
        return _parse_time(lx)
    if lx.s.startswith(("=", "in("), lx.i) or lx.s.startswith("in (", lx.i):
        if lx.eat("="):
            if lx.eat("~"):
                raise Unsupported("regexp")
            return Term(f, "eq", (lx.value(),))
        lx.keyword("in")
        lx.need("(")
        vals = []
        while not lx.eat(")"):
            vals.append(lx.value())
            lx.eat(",")
        return Term(f, "in", tuple(vals))
    if lx.s.startswith(("~", ">", "<", "!", "[", "("), lx.i):
        raise Unsupported(f"filter {f}:{lx.s[lx.i:lx.i + 10]}")
    if lx.eat("*"):
        return Term(f, "any")
    quoted = lx.s[lx.i:lx.i + 1] == '"'
    v = lx.value()
    if not quoted and v.endswith("*"):
        return Term(f, "prefix", (v[:-1],))
    if lx.s[lx.i:lx.i + 1] == "*":  # "quoted prefix"*
        lx.i += 1
        return Term(f, "prefix", (v,))
    return Term(f, "word", (v,))


def _parse_time(lx: _Lex) -> Term:
    if lx.peek() in ("[", "("):
        lo_open = lx.s[lx.i] == "("
        lx.i += 1
        a = lx.time_value()
        lx.need(",")
        b = lx.time_value()
        lx.ws()
        c = lx.s[lx.i:lx.i + 1]
        if c not in ("]", ")"):
            raise Unsupported("time range")
        lx.i += 1
        hi_open = c == ")"
        return Term("_time", "time", lo=_ts(a), hi=_ts(b) if hi_open else _end_of(b), lo_open=lo_open,
                    hi_open=hi_open)
    for op in (">=", "<=", ">", "<"):
        if lx.eat(op):
            v = lx.time_value()
            if _DUR.match(v):
                raise Unsupported("relative _time comparison")
            t = _ts(v)
            if op[0] == ">":
                return Term("_time", "time", lo=t, lo_open=op == ">")
            return Term("_time", "time", hi=t if op == "<" else _end_of(v), hi_open=op == "<")
    v = lx.time_value()
    if _DUR.match(v):
        return Term("_time", "time", rel_s=dur_s(v))
    raise Unsupported(f"_time:{v}")


def parse_filter(s: str):
    lx = _Lex(s)
    x = _parse_or(lx)
    if not lx.done():
        raise Unsupported(f"trailing {lx.s[lx.i:lx.i + 30]!r}")
    return x


def top_terms(x) -> list:
    """The filter's top-level AND terms (negations included, as Not)."""
    return list(x.xs) if isinstance(x, And) else [x]


# ------------------------------------------------------------------ pipes


def _names(lx: _Lex) -> list[str]:
    lx.need("(")
    out = []
    while not lx.eat(")"):
        out.append(lx.value())
        lx.eat(",")
    return out


def _int(lx: _Lex) -> int:
    v = lx.value()
    if not v.isdigit():
        raise Unsupported(f"limit {v!r}")
    return int(v)


@dataclass
class Fields:
    names: list
    remove: bool = False

    def run(self, rows, now):
        if self.remove:
            return [{k: v for k, v in r.items() if k not in self.names} for r in rows]
        return [{k: r[k] for k in self.names if r.get(k) not in (None, "")} for r in rows]


@dataclass
class Sort:
    keys: list  # [(field, desc)]
    limit: int | None = None

    def run(self, rows, now):
        out = list(rows)
        for f, desc in reversed(self.keys):
            out.sort(key=_value_key(f, out), reverse=desc)
        return out[:self.limit] if self.limit is not None else out


def _digits(s: str, i: int) -> int:
    j = i
    while j < len(s) and "0" <= s[j] <= "9":
        j += 1
    return j


def _natural(a: str, b: str) -> int:
    """VictoriaLogs' order for text: runs of digits by their value, the rest
    character by character ("item 9" before "item 10", "B" before "b")."""
    i = j = 0
    while i < len(a) and j < len(b):
        if "0" <= a[i] <= "9" and "0" <= b[j] <= "9":
            i2, j2 = _digits(a, i), _digits(b, j)
            x, y = int(a[i:i2]), int(b[j:j2])
            if x != y:
                return -1 if x < y else 1
            i, j = i2, j2
            continue
        if a[i] != b[j]:
            return -1 if a[i] < b[j] else 1
        i, j = i + 1, j + 1
    return (len(a) - i > len(b) - j) - (len(a) - i < len(b) - j)


def _cmp(a: str, b: str) -> int:
    """VictoriaLogs' comparison of two values (sort, min, max, row_max):
    as numbers when both are, else in natural text order. A missing value
    is "", the smallest."""
    if _isnum(a) and _isnum(b):
        x, y = float(a), float(b)
        return (x > y) - (x < y)
    return _natural(a, b)


def _one_kind(vals) -> None:
    """Numbers and text mixed in one field: VictoriaLogs' order then depends
    on how it stored the values (v1.54 sorted 100, 1_000, 12abc; its min of
    -3, " 6", 1.5 was 1.5). No answer here can match: it's VictoriaLogs'
    to give (Unsupported, at run time). Empty values don't count."""
    if _LENIENT.get():
        return
    kinds = {_isnum(v) for v in vals if v not in (None, "")}
    if len(kinds) > 1:
        raise Unsupported("numbers and text in one field")


def _value_key(f: str, rows):
    """A sort key for `rows`' field `f`."""
    if f == "_time":
        return lambda r: parse_t(r.get(f)).timestamp() if r.get(f) else float("-inf")
    _one_kind(str(r.get(f)) for r in rows if r.get(f) is not None)
    k = functools.cmp_to_key(_cmp)
    return lambda r: k("" if r.get(f) is None else str(r.get(f)))


@dataclass
class Limit:
    n: int

    def run(self, rows, now):
        return list(rows)[:self.n]


@dataclass
class Uniq:
    names: list
    limit: int | None = None

    def run(self, rows, now):
        seen, out = set(), []
        for r in rows:
            k = tuple("" if r.get(f) is None else str(r.get(f)) for f in self.names)
            if k not in seen:
                seen.add(k)
                out.append({f: v for f, v in zip(self.names, k, strict=True) if v != ""})
        return out[:self.limit] if self.limit is not None else out


def fmt_num(f: float) -> str:
    if not math.isfinite(f):
        return {math.inf: "+Inf", -math.inf: "-Inf"}.get(f, "NaN")
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


def fmt_time(t: datetime) -> str:
    """VictoriaLogs' RFC 3339: the fraction only when there is one, trailing zeros cut."""
    s = t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    if t.microsecond:
        s += "." + f"{t.microsecond:06d}".rstrip("0")
    return s + "Z"


_NUM = re.compile(r"-?\d+(?:_\d+)*(?:\.\d+)?")


def _isnum(v) -> bool:
    """A number the way every VictoriaLogs stats path reads one: 12, -3, 0.25,
    1_000. Not " 6", "inf" or "nan", which Python's float() takes. "1e3",
    "+5", ".5", "1." and "0x10" are numbers to v1.54's `stats sum()` but
    not to its `stats by (...) sum()` (tests/test_logsql.py): no answer
    matches both, and the shipper never writes them (JSON numbers)."""
    if not isinstance(v, str) or not _NUM.fullmatch(v):
        return False
    return math.isfinite(float(v))


def _json(v) -> str:
    """JSON as VictoriaLogs writes it in row_max and uniq_values: no spaces,
    UTF-8 kept, but < and ' escaped."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("'", "\\u0027")


class _Func:
    def __init__(self, name: str, args: list, limit: int | None):
        if name not in ("count", "count_uniq", "sum", "min", "max", "uniq_values", "row_max", "row_min"):
            raise Unsupported(f"stats {name}()")
        if name not in ("count",) and not args:
            raise Unsupported(f"{name}() without a field")
        if name in ("row_max", "row_min") and len(args) == 1:
            # The whole row: VictoriaLogs' has _stream and _stream_id, which
            # the cache doesn't keep.
            raise Unsupported(f"{name}() of the whole row")
        self.name, self.args, self.limit = name, args, limit

    def value(self, rows: list[dict]) -> str | None:
        n, a = self.name, self.args
        if n == "count":
            if a:
                return str(sum(1 for r in rows if any(r.get(f) not in (None, "") for f in a)))
            return str(len(rows))
        vals = [str(r.get(a[0])) for r in rows if r.get(a[0]) not in (None, "")]
        if n == "count_uniq":
            return str(len(set(vals)))
        if n == "sum":
            nums = [float(v) for v in vals if _isnum(v)]
            # No number at all: VictoriaLogs says NaN (stores.num reads it as none).
            return fmt_num(sum(nums)) if nums else "NaN"
        if n == "uniq_values":
            u = sorted(set(vals))
            # Over the limit VictoriaLogs keeps any `limit` of them; these are the first.
            return _json(u[:self.limit] if self.limit is not None else u)
        if n in ("min", "max"):
            if not vals:
                return None
            if n == "min" and len(vals) < len(rows):
                # A row without the field counts as "", the smallest value:
                # VictoriaLogs' min is then empty (stats min(level) over every
                # event, most of them without a level).
                return None
            pick = max if n == "max" else min
            if a[0] == "_time":
                return pick(vals, key=lambda v: parse_t(v))
            _one_kind(vals)
            return pick(vals, key=functools.cmp_to_key(_cmp))
        # row_max / row_min: the listed fields of the row with the highest (lowest) first field
        cand = [r for r in rows if r.get(a[0]) not in (None, "")]
        if not cand:
            return None
        r = (max if n == "row_max" else min)(cand, key=_value_key(a[0], cand))
        want = a[1:]
        return _json({f: str(r[f]) for f in want if r.get(f) not in (None, "")})


@dataclass
class Stats:
    by: list  # [(field, bucket_s or None)]
    funcs: list  # [(out name, _Func)]

    def run(self, rows, now):
        groups: dict[tuple, list] = {}
        for r in rows:
            k = []
            for f, step in self.by:
                if step is not None:
                    v = r.get(f)
                    if v in (None, ""):
                        k.append("")
                        continue
                    t = parse_t(v).timestamp()
                    k.append(fmt_time(datetime.fromtimestamp((t // step) * step, UTC)))
                else:
                    v = r.get(f)
                    k.append("" if v is None else str(v))
            groups.setdefault(tuple(k), []).append(r)
        if not self.by and not groups:
            groups[()] = []
        out = []
        for k, rs in groups.items():
            row = {f: v for (f, _), v in zip(self.by, k, strict=True) if v != ""}
            for name, fn in self.funcs:
                v = fn.value(rs)
                if v not in (None, ""):
                    row[name] = v
            out.append(row)
        return out


def _parse_stats(lx: _Lex) -> Stats:
    by = []
    if lx.keyword("by"):
        lx.need("(")
        while not lx.eat(")"):
            f = lx.value()
            step = None
            if lx.s[lx.i:lx.i + 1] == ":":
                lx.i += 1
                step = dur_s(lx.value())
            by.append((f, step))
            lx.eat(",")
    funcs = []
    while not lx.done():
        m = re.compile(r"[a-z_]+").match(lx.s, lx.i)
        if not m:
            raise Unsupported("stats function")
        name = m.group(0)
        lx.i = m.end()
        args = _names(lx)
        limit = _int(lx) if lx.keyword("limit") else None
        fn = _Func(name, args, limit)
        if lx.keyword("as") or not lx.done() and lx.peek() != ",":
            out = lx.value()
        else:
            # VictoriaLogs' own name for it: count(*), uniq_values(f) limit 3.
            out = f"{name}({', '.join(args) or '*'})" + (f" limit {limit}" if limit is not None else "")
        funcs.append((out, fn))
        if not lx.eat(","):
            break
    if not lx.done() or not funcs:
        raise Unsupported("stats")
    return Stats(by, funcs)


def parse_pipe(s: str):
    lx = _Lex(s)
    remove = lx.keyword("rm")
    if remove or lx.keyword("fields"):
        names = []
        while not lx.done():
            names.append(lx.value())
            lx.eat(",")
        if remove and (not names or any("*" in name for name in names)):
            raise Unsupported("rm expects literal field names")
        return Fields(names, remove)
    if lx.keyword("sort"):
        lx.need("by")
        lx.need("(")
        keys = []
        while not lx.eat(")"):
            f = lx.value()
            desc = lx.keyword("desc")
            if not desc:
                lx.keyword("asc")
            keys.append((f, desc))
            lx.eat(",")
        desc_all = lx.keyword("desc")
        if desc_all:
            keys = [(f, not d) for f, d in keys]
        limit = _int(lx) if lx.keyword("limit") else None
        if not lx.done():
            raise Unsupported("sort")
        return Sort(keys, limit)
    if lx.keyword("limit") or lx.keyword("head"):
        n = _int(lx)
        if not lx.done():
            raise Unsupported("limit")
        return Limit(n)
    if lx.keyword("uniq"):
        lx.need("by")
        names = _names(lx)
        limit = _int(lx) if lx.keyword("limit") else None
        if not lx.done():
            raise Unsupported("uniq")
        return Uniq(names, limit)
    if lx.keyword("stats"):
        return _parse_stats(lx)
    raise Unsupported(f"pipe {s[:20]!r}")


@dataclass
class Query:
    where: object
    pipes: list

    def run(self, rows, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(UTC)
        out = [r for r in rows if self.where.match(r, now)]
        for p in self.pipes:
            out = p.run(out, now)
        if not any(isinstance(p, (Fields, Uniq, Stats)) for p in self.pipes):
            out = [dict(r) for r in out]  # the caller may change its rows: never the cache's
        # VictoriaLogs answers no line for a row left with no field (`fields
        # x` on a row without x); a pipe in between still saw it.
        return [r for r in out if r]


def parse(q: str) -> Query:
    parts = split_pipes(q)
    return Query(parse_filter(parts[0]) if parts[0] else And([]), [parse_pipe(p) for p in parts[1:]])
