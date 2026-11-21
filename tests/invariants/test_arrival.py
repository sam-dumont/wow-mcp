"""Arrival-schedule, duplicate and source-order invariants (properties 3, 4, 5).

The same finished history must give the same answers however it reached
the server: in one batch, in chunks, out of order, late, or sent twice.
Two places see the schedule: the server's event cache (wowstats.eventcache,
which re-reads the hours whose count moved) and the shipper (which reads the
Recorder's chunks as the game wrote them). Both are driven here over the
generated histories (tests/invariants/histories.py), and the answers compared
are the prep's tables row for row and every metric of the semantic layer.

Builds on tests/test_zone_time.py (one hand-made schedule per case) and
tests/test_bounded_reads.py (the cache's pages): here the schedules are
drawn from a seed, over the whole demo history.
"""

from __future__ import annotations

import random

import pytest

from tests import degraded as D
from tests.invariants import histories as H
from tests.test_zone_time import CHAR as ZCHAR
from tests.test_zone_time import _digits_chunks, ship
from tests.test_zone_time import facts as zone_facts
from wowstats import stores
from wowstats.analysis import prep
from wowstats.stores import parse_t, record_order

SEEDS = (1, 2)
# Small pages: the cache's busy hours are read by the minute, some by the second.
PAGE = 120


def _analysed(rows: list[dict]) -> list[dict]:
    """What prep.query asks the stores for: the character's events, no
    other players, auction scans or combat log lines."""
    return [r for r in rows if r.get("char") in (H.CHAR, H.CHAR.replace("-", " "))
            and r["feed"] not in ("wow-player", "wow-prices", "wow-combatlog")]


def through_cache(batches: list[list[dict]], monkeypatch) -> list[dict]:
    """Each batch lands in the store, then the analysis reads the character
    (the cache refreshes on every read); the last read's rows."""
    monkeypatch.setattr(stores, "VL_PAGE", PAGE)
    vl = D.VL([])
    try:
        store = stores.Store(vl.url)
        got: list[dict] = []
        for b in batches:
            vl.rows = sorted(vl.rows + [dict(r) for r in b], key=lambda r: parse_t(r["_time"]))
            store.cache.expire()
            got = prep.query(store, store.stream, H.CHAR)
        return got
    finally:
        vl.close()


def schedules(rows: list[dict], seed: int) -> dict[str, list[list[dict]]]:
    rnd = random.Random(seed)
    n = len(rows)
    cuts = sorted(rnd.sample(range(1, n), 7))
    chunks = [rows[a:b] for a, b in zip([0, *cuts], [*cuts, n], strict=True)]
    shuffled = chunks[:]
    rnd.shuffle(shuffled)
    late = set(rnd.sample(range(n), n // 7))
    return {
        "one batch": [rows],
        "in chunks": chunks,
        "out of order": shuffled,
        "late": [[r for i, r in enumerate(rows) if i not in late], [rows[i] for i in sorted(late)]],
    }


@pytest.fixture(scope="module", params=SEEDS)
def history(request):
    rows = H.rows_of(request.param)
    db = H.build(_analysed(rows))
    # The metrics read nothing but these tables, so equal facts give equal
    # metrics; asked once per seed (1.7 s a time) to prove it end to end.
    return request.param, rows, H.facts(db), (lambda: H.metrics(db))


@pytest.mark.usefixtures("demo_clock")
@pytest.mark.parametrize("schedule", ["one batch", "in chunks", "out of order", "late"])
def test_arrival_schedule_changes_no_answer(history, schedule, monkeypatch):
    seed, rows, facts, metrics = history
    got = through_cache(schedules(rows, seed)[schedule], monkeypatch)
    db = H.build(got)
    assert H.facts(db) == facts, f"seed {seed}, {schedule}: the prep differs"
    if schedule == "late" and seed == SEEDS[0]:
        assert H.metrics(db) == metrics()


@pytest.mark.usefixtures("demo_clock")
def test_a_resent_batch_changes_no_total(history, monkeypatch):
    """Property 4: a timeout after the store wrote makes the shipper send a
    batch again; every line of it is then stored twice."""
    seed, rows, facts, metrics = history
    rnd = random.Random(seed)
    a, b = sorted(rnd.sample(range(len(rows)), 2))
    batch = rows[a:b]
    got = through_cache([rows, batch], monkeypatch)
    assert len(got) > len(_analysed(rows)), "the resend is in the store"
    db = H.build(got)
    assert H.facts(db) == facts
    if seed == SEEDS[-1]:
        assert H.metrics(db) == metrics()


# ---------------------------------------------------------------- the shipper's side


def _schedule(rnd: random.Random, keys: list, grow: bool) -> list[list]:
    """Runs of the shipper as the game file holds the chunks: in the order
    the Recorder wrote them, each run reading every chunk not acked yet (an
    upload that failed leaves a chunk for the next run too), and with
    `grow` the newest one first read while the game was still writing it."""
    runs: list[list] = []
    pending: list = []
    for k in sorted(keys):
        if grow and rnd.random() < 0.6:
            runs.append([*pending, (k, "part")])
        pending.append(k)
        if rnd.random() < 0.7:  # a run after this chunk; else it waits for the next
            runs.append(pending[:])
            pending = [x for x in pending if rnd.random() < 0.3]  # acked, mostly
    runs.append(pending + [sorted(keys)[-1]])
    return runs


def _rich_chunks() -> dict:
    """tests/test_zone_time.py's chunks (sequences 9, 10, 99, 100; zone
    records sharing seconds inside and across chunks) plus guide steps and
    kills on both sides of each chunk roll: the records whose zone and step
    time come from the records before them."""
    ch = _digits_chunks()

    def put(key, i: int, kind: str, *fields: str) -> None:
        epoch, lines = ch[key]
        dt = lines[i].split("\t")[1]
        lines = lines[:]
        lines[i] = "\t".join([kind, dt, *fields])
        ch[key] = (epoch, lines)

    step = lambda n: ("forever/alliance/gnome/02", str(n), "auto", "1429", "4100", "6500")  # noqa: E731
    kill = ("3", "12", "normal", "1429", "4100", "6500", "45", "0", "1", "")
    put(("CID", 9), 3, "step", *step(1))
    put(("CID", 9), 11, "kill", *kill)
    put(("CID", 10), 3, "step", *step(2))
    put(("CID", 10), 4, "kill", *kill)
    put(("CID", 99), 3, "step", *step(3))
    put(("CID", 100), 3, "kill", *kill)
    put(("CID", 100), 5, "step", *step(4))
    return ch


def _view(ch: dict, run: list) -> dict:
    out = {}
    for item in run:
        if isinstance(item, tuple) and item[1] == "part":
            epoch, lines = ch[item[0]]
            out[item[0]] = (epoch, lines[:max(2, len(lines) // 2)])
        else:
            out[item] = ch[item]
    return out


@pytest.mark.parametrize("seed", range(10))
def test_any_chunk_schedule_gives_the_same_facts(shipper, seed):
    """The Recorder's chunks (sequences 9, 10, 99, 100; zone records on
    shared seconds inside and across chunks) reaching the shipper in a
    random schedule: the page's, the journal's and the MCP's zone facts,
    and the prep's tables, are the ones of one run that saw them all."""
    ch = _rich_chunks()
    whole = ship(shipper, [ch])
    rnd = random.Random(seed)
    runs = [_view(ch, run) for run in _schedule(rnd, sorted(ch), grow=seed % 2 == 1)]
    split = ship(shipper, runs)
    assert zone_facts(split) == zone_facts(whole)
    # What the shipper sends for each record is the same, not just what the
    # pages make of it. A zone record's duration_s is the one exception: the
    # shipper still sends what it saw, and no reader uses it (stores.zone_time).
    def sent(rows):
        return {r["source_id"]: {k: v for k, v in r.items() if not (k == "duration_s" and r["kind"] == "zone")}
                for r in rows}
    assert sent(split) == sent(whole)
    assert H.facts(prep.build([dict(r) for r in split], ZCHAR)) == H.facts(prep.build([dict(r) for r in whole], ZCHAR))


# ---------------------------------------------------------------- property 5: source order


@pytest.mark.parametrize("seed", SEEDS)
def test_equal_times_keep_the_recorders_order(seed):
    """Records on one second follow each other as the Recorder wrote them
    (chunk sequence, then record index, as numbers), whatever order they
    arrive in: the prep's record ids and every table built on them."""
    rows = _analysed(H.rows_of(seed))
    want = H.facts(H.build(rows))
    rec = [r for r in rows if r["feed"] == "wow-recorder"]
    ties = {}
    for r in rec:
        ties.setdefault(r["_time"], []).append(r)
    crossing = [g for g in ties.values() if len(g) > 1 and len({len(str(record_order(r)[1])) for r in g}
                                                                | {len(str(record_order(r)[2])) for r in g}) > 1]
    assert crossing, "the history has ties across a digit boundary"
    for k in range(3):
        mixed = rows[:]
        random.Random(seed * 10 + k).shuffle(mixed)
        db = H.build(mixed)
        assert H.facts(db) == want
        got = db.execute("SELECT t, rec_id FROM records ORDER BY rec_id").fetchall()
        assert [t for t, _ in got] == sorted(t for t, _ in got)
    # The records and positions tables together are the Recorder's order,
    # chunk and index compared as numbers.
    db = H.build(rows)
    got = db.execute("SELECT rec_id, t FROM records UNION ALL SELECT rec_id, t FROM positions ORDER BY rec_id").fetchall()
    expect = sorted(rec, key=lambda r: (parse_t(r["_time"]), *record_order(r)))
    assert len(got) == len(expect)
    assert [t for _, t in got] == [parse_t(r["_time"]).timestamp() for r in expect]
