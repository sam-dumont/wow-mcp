"""The semantic layer's executable caveats (external review 4, D, E and F),
on a tiny synthetic prep: made-up levels, snapshots and kills, no seed.

D: a route comparison reads one population on both sides (a level with a
   whole split and a planned time), while planned_s alone stays the total.
E: a money change needs two snapshots at different times: one is unknown,
   not a zero, and none is unavailable; the answer says which.
F: `limit` keeps `more` honest: the compiler asks for one row past it.
And the model's own shape: the fields the compiler reads are there."""

from __future__ import annotations

import warnings

import pytest

from wowstats.analysis import TABLES, duck, prep, semantic
from wowstats.analysis.sql import QueryError
from wowstats.mcp import route

warnings.filterwarnings("ignore", module="boring_semantic_layer")


def layer(levels=(), snapshots=(), kills=(), records=()):
    """A prep with only these rows, mirrored to DuckDB, the model on top."""
    db = prep.build([], "Synth-Test", None)
    db.executemany("INSERT INTO levels (level, ding_t, played_s, xp, partial, planned_s) VALUES (?, ?, ?, ?, ?, ?)",
                   levels)
    db.executemany("INSERT INTO snapshots (t, level, money_copper) VALUES (?, ?, ?)", snapshots)
    rows = [(i, 1500 + i, lv, zone, "kill", None) for i, (lv, zone) in enumerate(kills)]
    rows += [(100 + i, 1600 + i, lv, "Zone", kind, xp) for i, (lv, kind, xp) in enumerate(records)]
    db.executemany("INSERT INTO records (rec_id, t, level, zone, kind, xp_reward) VALUES (?, ?, ?, ?, ?, ?)", rows)
    con = duck.mirror(db, TABLES)
    semantic.prepare(con)
    lay, ro = semantic.Layer(con), duck.ReadOnlyDuck(con)

    def ask(**q):
        got = semantic.run(lay, ro, q)
        return got | {"table": [dict(zip(got["columns"], r, strict=True)) for r in got["rows"]]}
    return ask


# ---------------------------------------------------------------- D: route comparisons


DONE = (10, 1000, 100, 500, 0, 100)  # a whole split: 100 s played, 100 s planned
IN_PLAY = (11, None, None, None, None, 100)  # the level in play: no split, a planned time
PARTIAL = (9, 900, 50, 200, 1, 100)  # tracking began partway: 50 s of a 100 s level


def test_vs_route_pairs_the_levels_both_sides_read():
    ask = layer(levels=[DONE, IN_PLAY])
    row = ask(metrics=["played_s", "planned_s", "vs_route", "over_route_s"])["table"][0]
    # planned_s alone is the total target, the level in play included.
    assert (row["played_s"], row["planned_s"]) == (100, 200)
    # The comparison reads the observed level only: on plan, nothing over.
    assert row["vs_route"] == pytest.approx(1.0)
    assert row["over_route_s"] == pytest.approx(0)
    # Filtering to the observed level gives the same answer.
    one = ask(metrics=["vs_route", "over_route_s"], where=[{"dimension": "level", "op": "=", "value": 10}])
    assert (one["table"][0]["vs_route"], one["table"][0]["over_route_s"]) == (pytest.approx(1.0), pytest.approx(0))


def test_a_partial_split_stays_out_of_the_route_comparison():
    """The policy: a split that began partway is short against a whole
    level's plan, so the comparison leaves it out (its played_s still counts)."""
    ask = layer(levels=[PARTIAL, DONE, IN_PLAY])
    row = ask(metrics=["played_s", "vs_route", "over_route_s"])["table"][0]
    assert row["played_s"] == 150
    assert (row["vs_route"], row["over_route_s"]) == (pytest.approx(1.0), pytest.approx(0))
    by = {r["level"]: r for r in ask(metrics=["vs_route"], group_by=["level"])["table"]}
    assert by[9]["vs_route"] is None and by[11]["vs_route"] is None and by[10]["vs_route"] == pytest.approx(1.0)


def test_the_levels_tool_compares_only_whole_splits():
    """MCP `levels`: vs_route only on a whole, timed split; the route's own
    time is still shown beside the others."""
    ref = route.reference()
    if not ref.available:
        pytest.skip("no route reference")
    rows = ref.compare([{"level": 12, "seconds": 600, "partial": False},
                        {"level": 13, "seconds": 300, "partial": True},
                        {"level": 14, "seconds": 0, "partial": False}], {"faction": "Alliance", "class": "WARRIOR"})
    by = {r["level"]: r for r in rows}
    assert "vs_route" in by[12] and "vs_route" not in by[13] and "vs_route" not in by[14]
    assert all("route_s" in r for r in rows)


# ---------------------------------------------------------------- E: one snapshot


def test_one_snapshot_is_an_unknown_change_not_a_zero():
    got = ask_gold(snapshots=[(2000, 11, 5000)])
    row = got["table"][0]
    assert row["gold_delta_copper"] is None and row["gold_delta_copper_evidence"] == 1
    assert got["evidence"]["gold_delta_copper"]["unknown"] == 1
    assert "two" in got["evidence"]["gold_delta_copper"]["needs"]


def test_two_snapshots_at_one_time_are_still_one_observation():
    row = ask_gold(snapshots=[(2000, 11, 5000), (2000, 11, 5000)])["table"][0]
    assert row["gold_delta_copper"] is None and row["gold_delta_copper_evidence"] == 1


def test_a_measured_zero_stays_a_zero():
    got = ask_gold(snapshots=[(2000, 11, 5000), (3000, 11, 5000)])
    row = got["table"][0]
    assert row["gold_delta_copper"] == 0 and row["gold_delta_copper_evidence"] == 2
    assert got["evidence"]["gold_delta_copper"]["measured"] == 1


def test_no_snapshot_is_unavailable():
    got = ask_gold(snapshots=[], levels=[DONE])
    assert all(r["gold_delta_copper"] is None for r in got["table"])
    assert got["evidence"]["gold_delta_copper"]["unavailable"] == len(got["rows"]) >= 1


def test_a_snapshot_without_money_is_not_evidence():
    row = ask_gold(snapshots=[(2000, 11, 5000), (3000, 11, None)])["table"][0]
    assert row["gold_delta_copper"] is None and row["gold_delta_copper_evidence"] == 1


def ask_gold(snapshots, levels=()):
    return layer(levels=levels, snapshots=snapshots)(metrics=["gold_delta_copper"])


# ---------------------------------------------------------------- F: limit and more


def test_limit_keeps_more_honest():
    ask = layer(kills=[(10, "A"), (10, "B"), (10, "C")])
    one = ask(metrics=["kills"], group_by=["zone"], limit=1)
    assert len(one["rows"]) == 1 and one["more"] is True
    assert "LIMIT 2" in one["sql"]
    every = ask(metrics=["kills"], group_by=["zone"], limit=3)
    assert len(every["rows"]) == 3 and every["more"] is False
    assert ask(metrics=["kills"], group_by=["zone"], limit=2)["more"] is True


# ---------------------------------------------------------------- the model's own fields


def test_every_metric_carries_the_fields_the_compiler_reads():
    for m in semantic.catalogue()["metrics"]:
        semantic.check_metric(m)  # raises on a missing or unknown value
        assert m["aggregation"] in semantic.AGGREGATIONS, m["name"]
        assert m["evidence_basis"] in semantic.EVIDENCE_BASES, m["name"]


@pytest.mark.parametrize("drop, why", [("eligibility", "eligibility"), ("min_evidence", "min_evidence")])
def test_a_metric_without_its_guard_is_refused(drop, why):
    cat = {m["name"]: m for m in semantic.catalogue()["metrics"]}
    m = dict(cat["vs_route" if drop == "eligibility" else "gold_delta_copper"])
    m.pop(drop)
    with pytest.raises(ValueError, match=why):
        semantic.check_metric(m)
    ratio = dict(cat["kills_per_combat_hour"])
    ratio.pop("eligibility")
    with pytest.raises(ValueError, match="eligibility"):
        semantic.check_metric(ratio)


def test_list_metrics_shows_the_five_fields():
    from wowstats.mcp import analysistools

    got = analysistools.t_list_metrics(None, {})
    by = {m["name"]: m for m in got["metrics"]}
    for name in ("vs_route", "gold_delta_copper", "played_s"):
        assert {"min_evidence", "eligibility", "aggregation", "time_meaning", "evidence_basis"} <= set(by[name])
    assert by["vs_route"]["eligibility"]["means"] and by["gold_delta_copper"]["min_evidence"]["at_least"] == 2
    assert by["played_s"]["eligibility"] is None and by["played_s"]["aggregation"] == "sum"


def test_paired_parts_are_not_metrics_a_caller_can_name():
    names = {m["name"] for m in semantic.catalogue()["metrics"]}
    assert not any("__" in n for n in names)
    with pytest.raises(QueryError):
        semantic.normalise({"metrics": ["played_s__for_vs_route"]})
