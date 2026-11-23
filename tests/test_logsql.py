"""wowstats.logsql against a real VictoriaLogs: the same rows, the same
queries, the same answers.

The event cache answers most queries in Python (wowstats.logsql); every
other test's "VictoriaLogs" is that same engine, so without this file it is
only ever checked against itself. Here the rows go into the VictoriaLogs
version the compose kit pins, in a tenant of their own, and every filter and
pipe the engine supports is asked of both: VictoriaLogs directly, and the
cache (the production path: wowstats.stores.Store.vl, read once, answered in
Python).

Needs a VictoriaLogs: WOW_TEST_VL_URL=http://127.0.0.1:9428 (`make
test-vl` starts one in Docker). Skipped without it; CI's `logsql` job runs it
against a container of the pinned image, where a missing one fails
(WOW_TEST_VL_REQUIRED=1). Nothing else of the store is touched: the rows
go to a random tenant (AccountID).
"""

from __future__ import annotations

import gzip
import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests import testchar
from wowstats import logsql, stores
from wowstats.stores import HISTORY_START

ROOT = Path(__file__).resolve().parent.parent
VL_URL = os.environ.get("WOW_TEST_VL_URL", "").rstrip("/")
# The stream fields the server's /ingest uses (wowstats.ingest.STREAM_FIELDS).
STREAM_FIELDS = "component,feed"
SCOPE = '-feed:wow-prices -feed:wow-combatlog'


def _reachable() -> bool:
    if not VL_URL:
        return False
    try:
        return urllib.request.urlopen(f"{VL_URL}/health", timeout=3).read().strip() == b"OK"
    except (OSError, urllib.error.URLError):
        return False


if os.environ.get("WOW_TEST_VL_REQUIRED") == "1":  # CI's logsql job: never a silent skip
    assert _reachable(), f"no VictoriaLogs at {VL_URL!r}"
pytestmark = pytest.mark.skipif(not _reachable(), reason="no VictoriaLogs: set WOW_TEST_VL_URL (make test-vl)")

# The three rows of the hand-checked cases (GOLDEN below), first recorded
# against v1.54.0 by hand in tests/test_bounded_reads.py.
GOLDEN_CHAR = "Golden-Test"
GOLDEN_ROWS = [
    {"_time": "2026-11-12T10:12:59.9Z", "kind": "xp", "zone": "Elwynn", "secs": "6000.5", "level": "10", "src": "a"},
    {"_time": "2026-11-12T10:13:51Z", "kind": "xp", "zone": "Elwynn", "secs": "122.4", "src": "b"},
    {"_time": "2026-11-12T10:13:54.7Z", "kind": "death", "level": "9", "src": "c"},
]

# Values the shipper could send that the views' own data rarely has:
# fractions, negatives, accents, an empty string, number-like words.
EDGE_CHAR = "Edgecase-Test"
EDGE_ROWS = [
    {"_time": "2026-11-15T08:00:00Z", "k": "1.5", "kind": "loot", "zone": "Dun Morogh", "n": "1.5", "w": "10", "name": "Ébène",
     "tag": "x-1"},
    {"_time": "2026-11-15T08:00:00.25Z", "k": "-3", "kind": "loot", "zone": "Dun Morogh", "n": "-3", "w": "9", "name": "ébène",
     "tag": "x-2"},
    {"_time": "2026-11-15T08:00:01Z", "k": "0.1", "kind": "loot", "zone": "Ironforge", "n": "0.1", "w": "abc", "name": "Zed",
     "tag": "y_1"},
    {"_time": "2026-11-15T08:00:59.999Z", "k": "0.2", "kind": "drop", "zone": "Ironforge", "n": "0.2", "w": "", "name": "zed",
     "tag": "x"},
    {"_time": "2026-11-15T09:30:00Z", "k": "1000", "kind": "drop", "n": "1000", "w": "100", "name": "Quel'Thalas", "tag": "x-1"},
    {"_time": "2026-11-16T23:59:59Z", "kind": "loot", "zone": "Loch Modan", "n": "inf", "w": "nan", "name": "Inf",
     "tag": "inf"},
    {"_time": "2026-11-17T00:00:00Z", "k": "7", "kind": "loot", "zone": "Loch Modan", "n": "7", "w": "x10", "name": "Hex",
     "tag": "0x10"},
    {"_time": "2026-11-17T00:00:01Z", "k": "-0.5", "kind": "note", "n": "-0.5", "w": "1_000", "name": "<a&b> \\ \"q\" é", "tag": "ok"},
    {"_time": "2026-11-17T00:00:02Z", "k": "10", "kind": "note", "n": "x6", "w": "1.0", "name": "\u2028 😀", "tag": "ok"},
    {"_time": "2026-11-17T00:00:03Z", "k": "2.50", "kind": "note", "n": "2.50", "w": "12abc", "name": "tab\tnl\n", "tag": "ok"},
]


def _post(path: str, body: bytes, tenant: int, **params) -> None:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{VL_URL}{path}?{q}", data=body, method="POST",
                                 headers={"Content-Type": "application/stream+json", **stores.tenant_headers(tenant)})
    urllib.request.urlopen(req, timeout=60).read()


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    tenant = random.randint(1_000_000, 2_000_000_000)
    # Testchar's hand-written history (tests/testchar.py): the cases below
    # name its zones and its records' exact times.
    rows = testchar.rows()
    with gzip.open(ROOT / "tests" / "data" / "analysis-events.jsonl.gz") as f:
        rows += [json.loads(line) for line in f]
    for char, extra in ((GOLDEN_CHAR, GOLDEN_ROWS), (EDGE_CHAR, EDGE_ROWS)):
        for i, r in enumerate(extra):
            rows.append({"feed": "wow-test", "char": char, "_msg": f"{char} {i}", "source_id": f"{char}:{i}", **r})
    _post("/insert/jsonline", "\n".join(json.dumps(r) for r in rows).encode(), tenant,
          _stream_fields=STREAM_FIELDS, _time_field="_time", _msg_field="_msg")
    urllib.request.urlopen(urllib.request.Request(f"{VL_URL}/internal/force_flush", data=b"", method="POST"),
                           timeout=60).read()
    s = stores.Store(VL_URL, account_id=tenant)
    assert len(s.vl_raw(s.vl_url, "*", HISTORY_START, datetime.now(UTC), 100_000)) == len(rows)
    return s


def scoped(char: str, q: str) -> str:
    """A query the cache answers: the store's stream, one character, the
    feeds it holds."""
    head, sep, rest = q.partition("|")
    head = head.strip()
    head = "" if head == "*" else " " + head
    return f'feed:wow-* char:="{char}" {SCOPE}{head}' + (f" |{rest}" if sep else "")


def both(store, query: str) -> tuple[list[dict], list[dict]]:
    """(VictoriaLogs' answer, the cache's answer) to `query`."""
    want = store.vl_raw(store.vl_url, query, HISTORY_START, datetime.now(UTC), 100_000)
    # The cache keeps no stream fields (eventcache._DROP).
    want = [{k: v for k, v in r.items() if k not in ("_stream", "_stream_id")} for r in want]
    got = store.cache.answer(query)
    assert got is not None, f"not answered by the cache: {query}"
    return want, got


def _key(r: dict) -> str:
    return json.dumps(r, sort_keys=True, ensure_ascii=False)


def same(want: list[dict], got: list[dict], ordered: bool) -> None:
    if ordered:
        assert got == want
    else:
        assert sorted(got, key=_key) == sorted(want, key=_key)


# Each case: (character, query, ordered). A query with `sort` compares in
# order; its sort keys are unique, so the order is VictoriaLogs' own.
DEMO = "Testchar-Demo"
PRIEST = "Testpriest-Fixture"
CASES = [
    # filters
    (DEMO, "kind:=xp | fields _time, level, xp", False),
    (DEMO, 'kind:="xp" | fields _time, xp', False),
    (DEMO, "kind:in(death, level) | fields _time, kind, level", False),
    (DEMO, 'kind:in("taxi", "land", nothere) | fields _time, kind', False),
    (DEMO, "quest_id:* | stats count() as n", False),
    (DEMO, "-zone:* | stats by (feed) count() as n", False),
    (DEMO, "!zone:* kind:=kill | stats count() as n", False),
    (DEMO, "not kind:=combat | stats by (kind) count() as n", False),
    (DEMO, "zone:Vale | stats by (zone) count() as n", False),
    (DEMO, 'zone:"Stranglethorn Vale" | stats by (zone) count() as n', False),
    (DEMO, "zone:Thorn | stats count() as n", False),
    (DEMO, "zone:Strangle* | stats by (zone) count() as n", False),
    (DEMO, "zone:Mon* | stats by (zone) count() as n", False),
    (DEMO, 'zone:"Scarlet Monastery"* | stats by (zone) count() as n', False),
    (DEMO, "zone:Graveyard | stats by (zone) count() as n", False),
    (DEMO, "source_id:rec* | stats count() as n", False),
    (DEMO, "(kind:=death or kind:=level) | fields _time, kind", False),
    (DEMO, "kind:=xp and level:=25 | fields _time, xp", False),
    (DEMO, "(zone:Wetlands or zone:Desolace) -kind:=combat | stats by (zone, kind) count() as n", False),
    (DEMO, "on:=true | stats count() as n", False),
    (DEMO, "_time:>2026-11-14T18:00:00Z kind:=xp | stats count() as n", False),
    (DEMO, "_time:>=2026-11-12T17:50:00Z kind:=xp | stats count() as n, min(_time) as t", False),
    (DEMO, "_time:>2026-11-12T17:50:00Z kind:=xp | stats count() as n, min(_time) as t", False),
    (DEMO, "_time:<2026-11-12T18:00:00Z kind:=xp | stats count() as n, max(_time) as t", False),
    (DEMO, "_time:<=2026-11-12T18:00:00Z kind:=xp | stats count() as n, max(_time) as t", False),
    (DEMO, "_time:<=2026-11-12T18:00Z kind:=xp | stats count() as n, max(_time) as t", False),
    (DEMO, "_time:[2026-11-13T00:00:00Z, 2026-11-13T23:59:59Z] | stats by (kind) count() as n", False),
    (DEMO, "_time:(2026-11-12T17:50:00Z, 2026-11-12T19:00:00Z) kind:=xp | fields _time, xp", False),
    (DEMO, '_time:["2026-11-12T17:50:00Z", "2026-11-12T19:00:00Z"] kind:=xp | fields _time, xp', False),
    (DEMO, "_time:30d | stats count() as n", False),
    (EDGE_CHAR, "name:Ébène | fields name", False),
    (EDGE_CHAR, "name:=ébène | fields name", False),
    (EDGE_CHAR, "name:Quel | fields name", False),
    (EDGE_CHAR, "tag:x | fields tag", False),
    (EDGE_CHAR, "tag:x* | fields tag", False),
    (EDGE_CHAR, "tag:y | fields tag", False),
    (EDGE_CHAR, "tag:1 | fields tag", False),
    (EDGE_CHAR, "w:* | fields w", False),
    (EDGE_CHAR, "-w:* | fields tag", False),
    # fields, sort, limit, uniq
    (DEMO, "kind:=level | fields _time, level, nothere", False),
    (DEMO, "kind:=xp | sort by (_time) | limit 5", True),
    (DEMO, "kind:=xp | sort by (_time desc) | limit 1 | fields _time, xp", True),
    (DEMO, "kind:=xp | sort by (_time) desc | head 3 | fields _time", True),
    (DEMO, "kind:=xp | sort by (_time) limit 4 | fields _time, xp", True),
    (DEMO, "kind:=level | sort by (level desc) | fields level", True),
    (DEMO, "kind:=level | fields _time, level | sort by (_time desc) | limit 1", True),
    (DEMO, "kind:=xp | uniq by (level)", False),
    (DEMO, "kind:=xp | uniq by (level, zone)", False),
    (DEMO, "* | uniq by (kind, nothere)", False),
    (DEMO, "kind:=xp | uniq by (_time, source_id, xp_gained) | stats count() as n", False),
    # stats
    (DEMO, "* | stats count() as n", False),
    (DEMO, "* | stats count() n", False),
    (DEMO, "* | stats count()", False),
    (DEMO, "* | stats count(quest_id) as n", False),
    (DEMO, "* | stats by (feed) count() as n, min(_time) as first", False),
    (DEMO, "* | stats by (kind) count_uniq(source_id) as n", False),
    (DEMO, "* | stats by (kind, quest_id) count() as n", False),
    (DEMO, "kind:=xp | stats sum(xp_gained) as xp, min(xp) as lo, max(xp) as hi", False),
    (DEMO, "kind:=xp | stats by (level) sum(xp_gained) as xp", False),
    (DEMO, "* | stats sum(nothere) as s, max(nothere) as m, count_uniq(nothere) as u", False),
    (DEMO, "* | stats min(_time) as first, max(_time) as last", False),
    (DEMO, "* | stats by (_time:1h) count() as n", False),
    (DEMO, "* | stats by (_time:1m) count() as n, min(_time) as t0, max(_time) as t1", False),
    (DEMO, "kind:=xp | stats by (_time:1m) sum(xp_gained) as xp", False),
    (DEMO, "* | stats by (_time:10s) count() as n", False),
    (DEMO, "* | stats by (_time:1d) count() as n", False),
    (DEMO, "player:* | stats by (player, context, _time:1d) count() as n", False),
    (DEMO, "* | stats by (zone) count() as n", False),
    (DEMO, "quest_id:* | stats by (quest_id) max(quest_name) as title", False),
    (DEMO, "* | stats uniq_values(zone) as zones", False),
    (DEMO, "* | stats uniq_values(kind) limit 50 as kinds", False),
    (DEMO, "* | stats by (kind) uniq_values(level) as levels", False),
    (DEMO, "* | stats row_max(_time, level, zone) as latest", False),
    (DEMO, "kind:=level | stats row_max(level, _time) as top", False),
    (DEMO, "* | stats by (char) count() as events, max(_time) as last_seen", False),
    (PRIEST, "* | stats by (feed, kind) count() as n, max(_time) as last", False),
    (PRIEST, "* | stats by (_time:1h) count() as n", False),
    (PRIEST, "* | stats sum(xp_gained) as xp, max(level) as top, min(level) as low", False),
    (PRIEST, "* | stats by (zone) count_uniq(source_id) as n, max(level) as top", False),
    (PRIEST, "* | stats min(level) as low, count(level) as c", False),
    (PRIEST, "level:* | stats by (kind) min(level) as low", False),
    (EDGE_CHAR, "* | stats sum(n) as s, sum(w) as sw, sum(k) as sk", False),
    (EDGE_CHAR, "* | stats by (kind) sum(n) as s, sum(w) as sw", False),
    (EDGE_CHAR, "* | stats min(k) as lo, max(k) as hi", False),
    (EDGE_CHAR, "k:* | stats min(k) as lo, max(k) as hi, row_max(k, name) as top, row_min(k, name) as low", False),
    (EDGE_CHAR, "k:* | stats by (kind) min(k) as lo, max(k) as hi", False),
    (EDGE_CHAR, "k:* | sort by (k) | fields k", True),
    (EDGE_CHAR, "k:* | sort by (k desc) | fields k, name", True),
    (EDGE_CHAR, "* | stats min(name) as lo, max(name) as hi, uniq_values(name) as names", False),
    (EDGE_CHAR, "* | stats by (zone) count() as n, count(zone) as z", False),
    (EDGE_CHAR, "* | stats by (_time:1m) count() as n", False),
    (EDGE_CHAR, "* | stats by (_time:1s) count() as n", False),
    (EDGE_CHAR, "* | stats by (_time:1d) count() as n", False),
    (EDGE_CHAR, "kind:in(drop, note) | stats row_max(_time, name, n) as r, row_min(_time, name) as f", False),
    (EDGE_CHAR, "* | stats uniq_values(kind) limit 5, sum(nothere), count(), row_max(_time, tag)", False),
    (EDGE_CHAR, "kind:=note | stats sum(n) as s, sum(w) as sw", False),
    (EDGE_CHAR, "kind:=note | stats by (name) count() as n", False),
    (EDGE_CHAR, 'name:"<a&b>" | fields name', False),
    (DEMO, "* | stats by (_time:1w) count() as n", False),
    (DEMO, "* | stats by (_time:1y) count() as n", False),
    (EDGE_CHAR, "* | fields _time, w, nothere", False),
    (EDGE_CHAR, "* | sort by (zone, _time) | fields zone, _time", True),
    (EDGE_CHAR, "* | sort by (name) | fields name", True),
    (EDGE_CHAR, "* | stats by (kind) min(name) as lo, max(name) as hi, row_max(name, tag) as r", False),
]

# The hand-checked cases (tests/test_bounded_reads.py, which keeps them
# offline), on their three rows.
GOLDEN = [
    "_time:[2026-11-12T10:13:50Z, 2026-11-12T10:13:54Z] | fields src",
    "_time:(2026-11-12T10:13:50Z, 2026-11-12T10:13:54Z) | fields src",
    "_time:>2026-11-12T10:13:54Z | fields src",
    "_time:<=2026-11-12T10:13:54Z | fields src",
    "kind:=xp | stats by (zone) sum(secs) as secs, count() n",
    "* | stats by (_time:1m) count() as n",
    "* | stats row_max(_time, level, zone) as latest, uniq_values(zone) limit 3 as zones, "
    "count_uniq(kind) as kinds, max(level) as top",
    "kind:=xp | fields src, nothere",
    "kind:in(xp, death) -zone:Elwynn | fields src",
    "(zone:* or level:*) kind:death | fields src",
    "* | sort by (_time desc) | limit 1 | fields src",
    "kind:=xp | uniq by (zone)",
]


@pytest.mark.parametrize(("char", "query", "ordered"), CASES, ids=[f"{c}: {q}" for c, q, _ in CASES])
def test_same_answer_as_victorialogs(store, char, query, ordered):
    want, got = both(store, scoped(char, query))
    same(want, got, ordered)


@pytest.mark.parametrize("query", GOLDEN)
def test_the_hand_checked_cases(store, query):
    want, got = both(store, scoped(GOLDEN_CHAR, query))
    same(want, got, "sort by" in query)


def test_a_limit_without_sort_keeps_that_many_of_the_rows(store):
    """Which rows a bare `limit` keeps is VictoriaLogs' choice: the same
    number, all of them real rows."""
    q = scoped(DEMO, "kind:=xp | fields _time, xp")
    _, every = both(store, q)
    want, got = both(store, q + " | limit 7")
    assert len(want) == len(got) == 7
    assert all(r in every for r in got)


def test_the_cases_cover_every_construct_the_docstring_lists():
    """A construct the engine claims but no case asks is untested."""
    text = " ".join(q for _, q, _ in CASES) + " " + " ".join(GOLDEN)
    for construct in (":=", ":in(", ":*", "* |", "_time:>", "_time:>=", "_time:<", "_time:<=", "_time:[", "_time:(",
                      "_time:30d", " -", "!", "not ", " or ", " and ", "| fields", "sort by", " desc", "| limit",
                      "| head", "uniq by", "stats by", "count()", "count_uniq(", "sum(", "min(", "max(",
                      "uniq_values(", ") limit", "row_max(", "row_min(", "_time:1m", "_time:1h"):
        assert construct in text, construct
    assert "tests/test_logsql.py" in logsql.__doc__


def test_uniq_values_over_its_limit_keeps_that_many(store):
    """Over the limit VictoriaLogs keeps any N of the values (sorted); the
    engine keeps the first N."""
    _, every = both(store, scoped(DEMO, "* | stats by (kind) uniq_values(zone) as z"))
    every = {r.get("kind"): set(json.loads(r["z"])) for r in every}
    want, got = both(store, scoped(DEMO, "* | stats by (kind) uniq_values(zone) limit 2 as z"))
    assert {r.get("kind") for r in want} == {r.get("kind") for r in got}
    for answer in (want, got):
        for r in answer:
            z = json.loads(r["z"])
            assert z == sorted(z) and len(z) == min(2, len(every[r.get("kind")])) and set(z) <= every[r.get("kind")]


@pytest.mark.parametrize(("char", "func"), [(PRIEST, "row_max"), (PRIEST, "row_min"), (DEMO, "row_max"),
                                            (DEMO, "row_min")])
def test_row_max_on_a_tie_picks_one_of_the_tied_rows(store, char, func):
    """Events in the same millisecond (a session and its first xp): which one
    row_max picks is VictoriaLogs' choice. Both answers are one of them."""
    want, got = both(store, scoped(char, f"* | stats by (zone) {func}(_time, level, kind) as r"))
    rows = store.cache.rows(char)
    for answer in (want, got):
        assert len(answer) == len({r.get("zone") for r in rows})
        for r in answer:
            group = [x for x in rows if x.get("zone", "") == r.get("zone", "")]
            top = (max if func == "row_max" else min)(x["_time"] for x in group)
            fits = [{f: x[f] for f in ("level", "kind") if x.get(f)} for x in group if x["_time"] == top]
            assert json.loads(r["r"]) in fits, r


@pytest.mark.parametrize("query", ["w:* | stats min(w) as lo", "* | stats by (kind) max(n) as hi",
                                   "* | sort by (w) | fields w", "* | stats row_max(n, name) as top"])
def test_numbers_and_text_in_one_field_go_to_victorialogs(store, query):
    """VictoriaLogs orders a field mixing numbers and text by how it stored
    them (v1.54: 100 before 1_000 before 12abc, and min(-3, " 6", 1.5) was
    1.5): the cache doesn't guess, VictoriaLogs answers."""
    q = scoped(EDGE_CHAR, query)
    assert store.cache.answer(q) is None
    assert store.vl(q)


def test_outside_the_subset_goes_to_victorialogs(store):
    """What the engine refuses, the cache refuses: Store.vl asks VictoriaLogs."""
    for q in ("error", 'zone:~"Vale"', "* | top 3 by (zone)", "* | stats avg(xp)", "level:>20",
              "kind:='xp' | fields _time", "kind:=`xp` | fields _time", "* | stats row_max(_time) as last"):
        assert store.cache.answer(scoped(DEMO, q)) is None, q
