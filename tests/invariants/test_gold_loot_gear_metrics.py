"""The prep metrics the gold, loot, rested, riding and gear readers moved onto
(owner, 2026-11-22: every tool and brief fact reads through the semantic
layer): skill_rank, the snapshots' first and last level and XP, the gold
moves' online time, changes, window edges and rate, the rested intervals,
looted_items and upgrade_gain_share_pct.

On the three histories of test_views (the dev seed, the docs site's demo
character with its gear advice, a seeded variation), each metric equals the
SQL its reader ran before (or what the contract changed: rested_per_kill_xp
and gold_per_online_hour read their eligible rows on both sides), and asking
it by level fans nothing out: one row per level, and an additive metric's
levels add up to its total."""

from __future__ import annotations

import math
import warnings

import pytest

from tests import degraded as D
from tests.invariants import histories as H
from tests.invariants.test_views import SEED_CHAR, _seed_rows
from wowstats import analysis
from wowstats.analysis import prep, semantic
from wowstats.analysis.kb import RIDING
from wowstats.mcp import route

warnings.filterwarnings("ignore", module="boring_semantic_layer")
NEW = ["skill_rank", "snapshot_first_level", "snapshot_first_xp", "snapshot_last_level", "snapshot_last_xp",
       "gold_move_online_s", "gold_changes", "gold_first_t", "gold_last_t", "gold_per_online_hour", "rested_intervals",
       "rested_used_xp", "rested_kill_xp", "rested_per_kill_xp", "looted_items", "upgrade_gain_share_pct"]


@pytest.fixture(scope="module", params=["seed", "demo", "variant"])
def built(request, tmp_path_factory):
    if request.param == "seed":
        rows = [r for r in _seed_rows(tmp_path_factory.mktemp("seed")) if r.get("char") == SEED_CHAR]
        char = SEED_CHAR
    elif request.param == "demo":
        rows, char = D.base_rows(), D.CHAR
    else:
        rows, char = H.rows_of(7), H.CHAR
    db = prep.build([dict(r) for r in rows], char, route.reference(), advice=analysis.advice_for(rows, char, {}))
    return {"name": request.param, "db": db, "sem": semantic.local(db)}


def ask(b, q: dict) -> list[dict]:
    got = semantic.run(*b["sem"], q, semantic.EVERY_ROW)
    assert not got["more"]
    return [dict(zip(got["columns"], r, strict=True)) for r in got["rows"]]


def one(b, q: dict) -> dict:
    rows = ask(b, q)
    return rows[0] if rows else {}


def close(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def test_the_new_metrics_carry_the_contract():
    by = {m["name"]: m for m in semantic.catalogue()["metrics"]}
    for name in NEW:
        m = by[name]
        assert m["aggregation"] in semantic.AGGREGATIONS and m["time_meaning"] and m["evidence_basis"], name
        assert m["caveats"] and m["unit"], name
    assert by["gold_per_online_hour"]["eligibility"]["where"] == "_.online_s > 0"
    assert by["rested_per_kill_xp"]["min_evidence"]["at_least"] == 3


def test_skill_rank_is_the_records_highest_rank_per_skill(built):
    db = built["db"]
    want = {int(s): r for s, r in db.execute(
        "SELECT json_extract(extra, '$.skill_id'), MAX(CAST(json_extract(extra, '$.rank') AS INTEGER)) FROM records "
        "WHERE kind = 'skill' AND json_extract(extra, '$.skill_id') IS NOT NULL GROUP BY 1")}
    got = {r["skill_id"]: r["skill_rank"] for r in ask(built, {"metrics": ["skill_rank"], "group_by": ["skill_id"],
                                                            "where": [{"dimension": "skill_id", "op": "is not null"}]})}
    assert got == want
    riding = one(built, {"metrics": ["skill_rank"], "where": [{"dimension": "skill_id", "op": "=", "value": int(RIDING)}]})
    assert riding.get("skill_rank") == want.get(int(RIDING))


def test_the_snapshot_edges_and_the_gold_moves_equal_the_snapshots(built):
    """What gold_rate read from snapshots before: the count, the first and
    last level and XP, the online time between them (each later snapshot's
    online_s_since_prev), the purse's change and the moves that changed it."""
    db = built["db"]
    snaps = db.execute("SELECT t, money_copper, online_s_since_prev, level, xp FROM snapshots ORDER BY t").fetchall()
    s = one(built, {"metrics": ["snapshot_count", "snapshot_first_level", "snapshot_first_xp", "snapshot_last_level",
                                "snapshot_last_xp"]})
    assert s["snapshot_count"] == len(snaps)
    if not snaps:
        return
    assert (s["snapshot_first_level"], s["snapshot_first_xp"]) == (snaps[0][3], snaps[0][4])
    assert (s["snapshot_last_level"], s["snapshot_last_xp"]) == (snaps[-1][3], snaps[-1][4])
    g = one(built, {"metrics": ["gold_moved_copper", "gold_move_online_s", "gold_changes", "gold_first_t",
                                "gold_last_t", "gold_per_online_hour"]})
    if len(snaps) < 2:
        return
    assert g["gold_moved_copper"] == snaps[-1][1] - snaps[0][1]
    assert close(g["gold_move_online_s"], sum(r[2] or 0 for r in snaps[1:]))
    assert g["gold_changes"] == sum(1 for a, b in zip(snaps, snaps[1:], strict=False) if a[1] != b[1])
    assert (g["gold_first_t"], g["gold_last_t"]) == (snaps[0][0], snaps[-1][0])
    # The rate: only the moves with online time, on both sides.
    seen = [(b[1] - a[1], b[2]) for a, b in zip(snaps, snaps[1:], strict=False) if (b[2] or 0) > 0]
    hours = sum(o for _, o in seen) / 3600
    assert close(g["gold_per_online_hour"], sum(d for d, _ in seen) / hours if hours else None)


def test_the_rested_intervals_equal_the_xp_records(built):
    db = built["db"]
    n, used, kill = db.execute(
        """WITH x AS (SELECT t, rested_xp r, LAG(t) OVER (ORDER BY t) pt, LAG(rested_xp) OVER (ORDER BY t) pr FROM xp)
           SELECT COUNT(*), COALESCE(SUM(pr - r), 0), (SELECT COALESCE(SUM(k.xp_reward), 0) FROM records k
           WHERE k.kind = 'kill' AND k.dungeon_trip_id IS NULL AND EXISTS (SELECT 1 FROM x x2 WHERE k.t > x2.pt
           AND k.t <= x2.t AND x2.pr > x2.r)) FROM x WHERE pr > r""").fetchone()
    r = one(built, {"metrics": ["rested_intervals", "rested_used_xp", "rested_kill_xp", "rested_per_kill_xp"]})
    assert (r["rested_intervals"] or 0, r["rested_used_xp"] or 0, r["rested_kill_xp"] or 0) == (n, used, kill)
    # The burn: the intervals with no kill inside a dungeon trip, on both sides.
    rows = db.execute(
        """WITH x AS (SELECT t, rested_xp r, LAG(t) OVER (ORDER BY t) pt, LAG(rested_xp) OVER (ORDER BY t) pr FROM xp)
           SELECT pr - r, (SELECT COALESCE(SUM(xp_reward), 0) FROM records k WHERE k.kind = 'kill' AND
           k.dungeon_trip_id IS NULL AND k.t > x.pt AND k.t <= x.t), (SELECT COALESCE(SUM(xp_reward), 0) FROM records
           k WHERE k.kind = 'kill' AND k.dungeon_trip_id IS NOT NULL AND k.t > x.pt AND k.t <= x.t) FROM x
           WHERE pr > r""").fetchall()
    ok = [(u, k) for u, k, inside in rows if inside == 0]
    kills = sum(k for _, k in ok)
    assert r["rested_per_kill_xp_evidence"] == len(ok)
    assert close(r["rested_per_kill_xp"], sum(u for u, _ in ok) / kills if kills and len(ok) >= 3 else None)


def test_looted_items_equal_the_loot_outside_trips(built):
    db = built["db"]
    want = dict(db.execute("SELECT li.item_id, SUM(li.count) FROM loot_items li JOIN records r ON r.rec_id = li.rec_id "
                           "WHERE r.dungeon_trip_id IS NULL AND li.item_id IS NOT NULL GROUP BY li.item_id"))
    got = {r["item_id"]: r["looted_items"] for r in ask(built, {
        "metrics": ["looted_items"], "group_by": ["item_id"],
        "where": [{"dimension": "trip_id", "op": "is null"}, {"dimension": "item_id", "op": "is not null"}]})}
    assert got == want


def test_upgrade_gain_share_is_the_advice_s_per_item_and_slot(built):
    db = built["db"]
    want = {(i, s, w): g for i, s, w, g in db.execute(
        "SELECT item_id, slot, with_item_id, MAX(gain_share_pct) FROM upgrade_candidates GROUP BY 1, 2, 3")}
    got = {(r["item_id"], r["slot_id"], r["with_item_id"]): r["upgrade_gain_share_pct"] for r in ask(built, {
        "metrics": ["upgrade_gain_share_pct"], "group_by": ["item_id", "slot_id", "with_item_id"]})}
    assert got == want
    assert built["name"] == "seed" or want, "the demo's advice has upgrades"
    # One row per item, slot and partner: the advice lists each once.
    n = db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT item_id, slot, with_item_id, gain FROM upgrade_candidates)")
    assert n.fetchone()[0] == len(want)


@pytest.mark.parametrize("metric", ["gold_move_online_s", "gold_changes", "gold_moved_copper", "rested_intervals",
                                    "rested_used_xp", "rested_kill_xp", "looted_items"])
def test_no_fan_out_the_levels_add_up_to_the_total(built, metric):
    """By level: one row per level, and the levels' values add up to the
    metric asked alone, also beside metrics of other models (each model
    aggregates on its own, the parts join on level)."""
    total = one(built, {"metrics": [metric]}).get(metric) or 0
    for others in ([], ["online_s", "deaths"], ["played_s", "looted_items", "rested_used_xp"]):
        ms = list(dict.fromkeys([metric, *others]))
        rows = ask(built, {"metrics": ms, "group_by": ["level"]})
        keys = [r["level"] for r in rows]
        assert len(keys) == len(set(keys)), (metric, others)
        assert close(sum(r[metric] or 0 for r in rows), total), (metric, others)


def test_no_fan_out_the_ratios_by_level(built):
    """The two rates by level are their own parts' ratio on each level, one
    row per level, whatever is asked beside them."""
    rows = ask(built, {"metrics": ["gold_per_online_hour", "rested_per_kill_xp", "online_s", "looted_items"],
                       "group_by": ["level"]})
    assert len({r["level"] for r in rows}) == len(rows)
    alone = {r["level"]: r for r in ask(built, {"metrics": ["gold_per_online_hour"], "group_by": ["level"]})}
    for r in rows:
        assert close(r["gold_per_online_hour"], (alone.get(r["level"]) or {}).get("gold_per_online_hour"))
