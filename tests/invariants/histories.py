"""Generated histories for the pipeline invariants (tests/invariants/).

Every history here is the docs site's demo character
(tools/fixtures/generate.py build_demo, through tests/degraded.py) or a
small seeded variation of it: made up from scratch, no real account. A
variation pulls chosen Recorder records back onto the time of the record
before them, so equal timestamps cross the 9 -> 10 and 99 -> 100 record
index and chunk sequence boundaries, and a zone record shares its second
with its neighbour. The history stays a valid one: times never go
backwards in the Recorder's own order.

`facts` is what the analysis reads (every prep table, row for row, in the
order the prep wrote it); `metrics` every metric of the semantic layer on
it. Two histories "give the same answers" when both are equal.
"""

from __future__ import annotations

import functools
import random
import warnings

from tests import degraded as D
from wowstats.stores import parse_t, record_order

warnings.filterwarnings("ignore", module="boring_semantic_layer")

CHAR = D.CHAR
# The one moving part of a prep: when it was built.
VOLATILE_META = {"built_at"}


def demo() -> list[dict]:
    return D.base_rows()


def _seq_idx(r: dict) -> tuple[int, int]:
    _, seq, idx = record_order(r)
    return seq, idx


def _iso(t: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def variant(seed: int) -> list[dict]:
    """The demo with ties pulled across the digit boundaries (and a few
    random ones, `seed`), oldest first."""
    rows = demo()
    rnd = random.Random(seed)
    rec = sorted((r for r in rows if r["feed"] == "wow-recorder"), key=lambda r: (parse_t(r["_time"]),
                                                                                  *record_order(r)))
    pos = {id(r): i for i, r in enumerate(rec)}
    by = {_seq_idx(r): r for r in rec}
    seqs = sorted({s for s, _ in by})

    def pull(r: dict, n: int = 1) -> None:
        """r and the n - 1 records after it onto the time of the one before r."""
        i = pos[id(r)]
        if i == 0:
            return
        t = rec[i - 1]["_time"]
        for k in range(i, min(i + n, len(rec))):
            rec[k]["_time"] = t

    # Record index 9 -> 10 and 99 -> 100 inside a chunk, chunk 9 -> 10 and 99 -> 100.
    long = [s for s in seqs if (s, 100) in by]
    for seq in rnd.sample(long, min(2, len(long))) + rnd.sample(seqs, 3):
        if (seq, 10) in by:
            pull(by[(seq, 10)], rnd.choice((1, 2)))
        if (seq, 100) in by:
            pull(by[(seq, 100)], rnd.choice((1, 2)))
    for seq in (10, 100):
        first = min((k for k in by if k[0] == seq), default=None)
        if first:
            pull(by[first], 2)
    # A zone record on its neighbour's second, a few times.
    zones = [r for r in rec if r.get("kind") == "zone"]
    for z in rnd.sample(zones, min(6, len(zones))):
        pull(z, rnd.choice((1, 2)))
    return sorted(rows, key=lambda r: parse_t(r["_time"]))


@functools.cache
def variant_cached(seed: int) -> tuple[dict, ...]:
    return tuple(variant(seed))


def rows_of(seed: int | None) -> list[dict]:
    """A fresh copy (the prep writes into its rows)."""
    src = demo() if seed is None else variant_cached(seed)
    return [dict(r) for r in src]


# ---------------------------------------------------------------- what the analysis reads


def build(rows: list[dict], char: str = CHAR):
    from wowstats.analysis import prep

    return prep.build([dict(r) for r in rows], char)


def facts(db) -> dict[str, list[tuple]]:
    """Every table of a prep, its rows in the order the prep wrote them."""
    tables = [t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out = {t: db.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}
    out["meta"] = [r for r in out["meta"] if r[0] not in VOLATILE_META]
    return out


def layer(db, tz: str | None = None):
    """The semantic layer on a DuckDB copy of the prep: (layer, read-only runner)."""
    from wowstats.analysis import TABLES, duck, semantic

    con = duck.mirror(db, TABLES)
    semantic.prepare(con, tz)
    return semantic.Layer(con), duck.ReadOnlyDuck(con)


def ask(lay, ro, **q) -> dict:
    from wowstats.analysis import semantic

    return semantic.run(lay, ro, q)


def catalogue() -> list[dict]:
    from wowstats.analysis import semantic

    return semantic.catalogue()["metrics"]


def metrics(db, tz: str | None = None) -> dict:
    """Every metric's answer: on its own, by level where it has one, and by
    day where it has a time."""
    lay, ro = layer(db, tz)
    out = {}
    for m in catalogue():
        for by in ([], ["level"], ["time"]):
            if not set(by) <= set(m["group_by"]):
                continue
            q = {"metrics": [m["name"]], "group_by": by, "limit": 500} | ({"grain": "day"} if by == ["time"] else {})
            got = ask(lay, ro, **q)
            out[(m["name"], *by)] = (got["columns"], got["rows"], got["more"])
    return out
