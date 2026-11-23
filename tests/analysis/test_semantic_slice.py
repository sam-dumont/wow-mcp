"""The 0.7.0 data slice's metrics and views over the tables the prep already
has (semantic.yaml): XP by source and its rate per online hour, XP per
active hour, dungeon trips against questing, dungeon farming pace, the gear
sheets over time, slots at their best, the yards and the money of the whole
history, and the views per_death and per_week.

On the docs site's demo character (tests/degraded.py: made-up data), each
number is recounted from the prep's own rows; then each one with its source
taken away (the degraded scenarios, the old seed without kill XP), where it
must read unknown (NULL) or say why, never a made-up 0."""

from __future__ import annotations

import collections
import datetime as dt
import warnings

import pytest

from tests import degraded as D
from wowstats import analysis
from wowstats.analysis import prep, semantic
from wowstats.mcp import route

warnings.filterwarnings("ignore", module="boring_semantic_layer")

NEW = ("explore_xp", "kills_without_xp", "zero_xp_kills", "rides", "distance_yd", "gold_earned_copper",
       "slots_at_bis", "slots_scored", "slots_unscored", "sheets", "sheet_items", "first_worn_t", "last_worn_t",
       "gear_changes", "kill_xp_per_online_hour", "quest_xp_per_online_hour", "explore_xp_per_online_hour",
       "xp_per_active_hour", "dungeon_vs_quest_xp_ratio", "runs_per_online_hour")


def _built(rows, char=D.CHAR):
    db = prep.build([dict(r) for r in rows], char, route.reference(), advice=analysis.advice_for(rows, char, {}))
    layer, ro = semantic.local(db)

    def ask(**q):
        got = semantic.rows(layer, ro, q)
        return got

    def view(name):
        cols, got, cut = ro.run(f"SELECT * FROM {name}", 10_000)
        assert not cut
        return [dict(zip(cols, r, strict=True)) for r in got]
    return {"db": db, "ask": ask, "view": view}


@pytest.fixture(scope="module")
def demo():
    return _built(D.base_rows())


@pytest.fixture(scope="module", params=["no-recorder", "no-syndicator", "polestar-only"])
def without(request):
    return request.param, _built(D.rows_for(request.param))


def one(rows: list[dict]) -> dict:
    assert len(rows) == 1, rows
    return rows[0]


# ---------------------------------------------------------------- the catalogue


def test_the_new_metrics_are_listed_with_their_contract():
    from wowstats.mcp import analysistools

    got = {m["name"]: m for m in analysistools.t_list_metrics(None, {"names": sorted(NEW)})["metrics"]}
    for name in NEW:
        m = got[name]
        assert m["description"] and m["caveats"] and m["unit"], name
        assert m["aggregation"] in semantic.AGGREGATIONS and m["evidence_basis"] in semantic.EVIDENCE_BASES, name
        if m["aggregation"] == "ratio_of_sums":
            assert m["eligibility"]["where"] and m["eligibility"]["means"], name
    views = semantic.views()
    assert {"per_death", "per_week"} <= set(views)
    assert {"week", "death_t"} <= {d["name"] for d in semantic.catalogue()["dimensions"]}


# ---------------------------------------------------------------- XP by source


def test_xp_by_source_recounts_the_records(demo):
    db, ask = demo["db"], demo["ask"]
    want = dict(db.execute("SELECT kind, SUM(xp_reward) FROM records WHERE kind IN ('kill', 'turnin', 'explore') "
                           "GROUP BY kind").fetchall())
    got = one(ask(metrics=["kill_xp", "quest_xp", "explore_xp", "kills_without_xp", "zero_xp_kills"]))
    assert (got["kill_xp"], got["quest_xp"], got["explore_xp"]) == (want["kill"], want["turnin"], want["explore"])
    assert want["explore"] > 0
    assert got["kills_without_xp"] == db.execute(
        "SELECT COUNT(*) FROM records WHERE kind = 'kill' AND xp_reward IS NULL").fetchone()[0] == 0
    assert got["zero_xp_kills"] == db.execute(
        "SELECT COUNT(*) FROM records WHERE kind = 'kill' AND xp_reward = 0").fetchone()[0] > 0


def test_each_source_per_online_hour_by_level_and_zone(demo):
    """kill, quest and explore XP over the same online hours, by level and
    zone: the records' and the timeline's zones are the same names."""
    db, ask = demo["db"], demo["ask"]
    xp = collections.defaultdict(lambda: collections.Counter())
    for lv, zone, kind, x in db.execute("SELECT level, zone, kind, xp_reward FROM records WHERE session_id IS NOT "
                                        "NULL AND kind IN ('kill', 'turnin', 'explore')"):
        xp[(lv, zone)][kind] += x or 0
    hours = {(lv, zone): s / 3600 for lv, zone, s in db.execute(
        "SELECT level, zone, SUM(dur_s) FROM timeline WHERE session_id IS NOT NULL GROUP BY 1, 2")}
    rows = ask(metrics=["kill_xp_per_online_hour", "quest_xp_per_online_hour", "explore_xp_per_online_hour"],
               group_by=["level", "zone"])
    checked = 0
    for r in rows:
        key = (r["level"], r["zone"])
        if not hours.get(key):
            assert r["kill_xp_per_online_hour"] is None, key
            continue
        for kind, col in (("kill", "kill_xp_per_online_hour"), ("turnin", "quest_xp_per_online_hour"),
                          ("explore", "explore_xp_per_online_hour")):
            assert r[col] == pytest.approx(xp[key][kind] / hours[key]), (key, col)
            checked += 1
    assert checked > 30


def test_xp_per_active_hour_is_the_bar_over_active_time(demo):
    ask = demo["ask"]
    a = {r["level"]: r for r in ask(metrics=["xp_gained", "active_s"], group_by=["level"],
                                    where=[{"dimension": "session_id", "op": "is not null"}])}
    got = {r["level"]: r["xp_per_active_hour"] for r in ask(metrics=["xp_per_active_hour"], group_by=["level"])}
    assert len(got) >= 5
    for level, v in got.items():
        if v is not None:
            assert v == pytest.approx(a[level]["xp_gained"] / (a[level]["active_s"] / 3600)), level


def test_dungeon_trips_against_questing_by_level(demo):
    """trip_xp_per_hour over quest_xp_per_played_hour at each level: NULL at
    a level without a trip, never 0."""
    ask = demo["ask"]
    trip = {r["level"]: r["trip_xp_per_hour"] for r in ask(metrics=["trip_xp_per_hour"], group_by=["level"])}
    quest = {r["level"]: r["quest_xp_per_played_hour"] for r in ask(metrics=["quest_xp_per_played_hour"],
                                                                       group_by=["level"])}
    got = {r["level"]: r["dungeon_vs_quest_xp_ratio"] for r in ask(metrics=["dungeon_vs_quest_xp_ratio"],
                                                                    group_by=["level"])}
    measured = 0
    for level, v in got.items():
        if trip.get(level) and quest.get(level):
            assert v == pytest.approx(trip[level] / quest[level]), level
            measured += 1
        else:
            assert v is None, level
    assert measured >= 2 and any(v is None for v in got.values())


def test_runs_per_hour_inside_each_dungeon(demo):
    db, ask = demo["db"], demo["ask"]
    runs = dict(db.execute("SELECT name, COUNT(*) FROM dungeon_runs GROUP BY name").fetchall())
    inside = dict(db.execute("SELECT instance, SUM(dur_s) FROM timeline WHERE instance IS NOT NULL AND session_id "
                             "IS NOT NULL GROUP BY instance").fetchall())
    got = {r["dungeon"]: r["runs_per_online_hour"] for r in ask(metrics=["runs_per_online_hour"],
                                                                 group_by=["dungeon"]) if r["dungeon"]}
    assert set(got) == set(runs)
    for name, v in got.items():
        assert v == pytest.approx(runs[name] / (inside[name] / 3600)), name


# ---------------------------------------------------------------- the whole history


def test_lifetime_yards_money_and_rides(demo):
    db, ask = demo["db"], demo["ask"]
    got = one(ask(metrics=["distance_yd", "gold_earned_copper", "rides"]))
    assert got["distance_yd"] == pytest.approx(db.execute("SELECT SUM(dist_yd) FROM positions").fetchone()[0])
    assert got["gold_earned_copper"] == db.execute(
        "SELECT SUM(delta_copper) FROM gold_moves WHERE delta_copper > 0").fetchone()[0] > 0
    assert got["rides"] == db.execute("SELECT COUNT(*) FROM records WHERE kind = 'ride' AND on_flag = 1").fetchone()[
        0] > 0


# ---------------------------------------------------------------- gear over time


def test_the_sheets_give_the_gear_history(demo):
    """gear_changes recounted slot by slot from the sheets, first_worn_t the
    item's first sheet, sheets the sheets saved."""
    db, ask = demo["db"], demo["ask"]
    rows = db.execute("SELECT sheet_t, slot, item_id, name FROM gear_sheets ORDER BY slot, sheet_t, item_id").fetchall()
    changes, last = collections.Counter(), {}
    for t, slot, item, _ in rows:
        if slot in last and last[slot] != item:
            changes[t] += 1
        last[slot] = item
    got = one(ask(metrics=["sheets", "sheet_items", "gear_changes"]))
    assert got["sheets"] == len({r[0] for r in rows}) > 1
    assert got["sheet_items"] == len(rows)
    assert got["gear_changes"] == sum(changes.values()) > 0
    first = {}
    for t, _, _, name in sorted(rows):
        first.setdefault(name, t)
    for r in ask(metrics=["first_worn_t"], group_by=["item"]):
        assert r["first_worn_t"] == first[r["item"]], r["item"]


def test_slots_at_their_best():
    """A synthetic sheet: two slots at 100%, one below, one the reference
    can't score."""
    db = prep.build([], "Synth-Test", None)
    db.executemany("INSERT INTO gear (slot, slot_name, item_id, name, bis_pct) VALUES (?, ?, ?, ?, ?)",
                   [(1, "Head", 10, "Hat", 100), (5, "Chest", 11, "Robe", 100), (7, "Legs", 12, "Pants", 60),
                    (13, "Trinket", 13, "Charm", None)])
    layer, ro = semantic.local(db)
    got = one(semantic.rows(layer, ro, {"metrics": ["slots_at_bis", "slots_scored", "slots_unscored"]}))
    assert got == {"slots_at_bis": 2, "slots_scored": 3, "slots_unscored": 1}


# ---------------------------------------------------------------- the views


def test_per_death_is_one_row_per_death_record(demo):
    db = demo["db"]
    want = {t: (zone, lv, npc, npc_lv) for t, zone, lv, npc, npc_lv in db.execute(
        "SELECT t, zone, level, npc_id, npc_level FROM records WHERE kind = 'death'")}
    got = demo["view"]("per_death")
    assert len(got) == len(want) > 3
    for r in got:
        assert (r["zone"], r["level"], r["npc_id"], r["npc_level"]) == want[r["death_t"]]
        assert r["deaths"] == 1
        if r["npc_level"] is not None:
            assert r["level_gap_max"] == r["npc_level"] - r["level"]


def test_per_week_adds_up_to_the_whole(demo):
    """Each week is named by its Monday, and the additive columns of the
    weeks sum to the history's total."""
    weeks = demo["view"]("per_week")
    assert len(weeks) >= 2
    for w in weeks:
        assert dt.date.fromisoformat(str(w["week"])).weekday() == 0
    total = one(demo["ask"](metrics=["online_s", "kills", "xp_gained", "quests_done", "gold_earned_copper",
                                     "gear_changes", "distance_yd", "run_count"]))
    for col, v in total.items():
        assert sum(w[col] or 0 for w in weeks) == pytest.approx(v), col


def test_per_level_and_per_session_carry_the_new_columns(demo):
    lv = demo["view"]("per_level")
    assert {"active_s", "afk_s", "afk_share", "kill_xp", "quest_xp", "explore_xp", "money_copper", "gear_changes",
            "dungeon_vs_quest_xp_ratio"} <= set(lv[0])
    assert any(r["money_copper"] for r in lv) and any(r["explore_xp"] for r in lv)
    s = demo["view"]("per_session")
    assert {"afk_s", "combat_share", "dead_s", "taxi_s", "ride_s", "distance_yd", "xp_per_active_hour"} <= set(s[0])
    z = demo["view"]("per_zone")
    assert {"explore_xp", "kill_xp_per_online_hour", "rides", "zero_xp_kills"} <= set(z[0])


# ---------------------------------------------------------------- degraded


def test_without_the_source_the_metric_is_unknown(without):
    """No Recorder: every rate per online or active hour is NULL, the counts
    of its records 0. No Syndicator: no sheet, so no gear history (NULL),
    and no slot to score."""
    scenario, b = without
    got = one(b["ask"](metrics=["kill_xp_per_online_hour", "xp_per_active_hour", "runs_per_online_hour", "rides",
                                "kills_without_xp", "distance_yd"]))
    gear = one(b["ask"](metrics=["sheets", "gear_changes", "first_worn_t", "slots_scored"]))
    if scenario in ("no-recorder", "polestar-only"):
        assert (got["kill_xp_per_online_hour"], got["xp_per_active_hour"], got["runs_per_online_hour"]) == (
            None, None, None)
        assert got["rides"] == got["kills_without_xp"] == 0
        assert b["view"]("per_death") == []
    if scenario == "no-syndicator":
        assert gear["sheets"] == 0 and gear["gear_changes"] is None and gear["first_worn_t"] is None
        assert gear["slots_scored"] == 0
        assert got["kill_xp_per_online_hour"] is not None


def test_kills_that_dont_say_their_xp_make_kill_xp_unknown():
    """An older Recorder's kills carry no xp_reward: kill_xp and the kill
    XP rate are NULL (unknown), kills_without_xp says how many; a group
    without any kill is a measured 0."""
    db = prep.build([], "Synth-Test", None)
    db.executemany("INSERT INTO records (rec_id, t, level, zone, kind, xp_reward, session_id) VALUES "
                   "(?, ?, ?, ?, ?, ?, 1)",
                   [(1, 100, 10, "A", "kill", None), (2, 110, 10, "A", "kill", 50), (3, 120, 11, "B", "kill", 60),
                    (4, 130, 12, "C", "turnin", 400)])
    db.executemany("INSERT INTO timeline (seg_id, session_id, t0, t1, dur_s, level, zone) VALUES (?, 1, ?, ?, ?, ?, ?)",
                   [(1, 90, 125, 35, 10, "A"), (2, 125, 135, 10, 11, "B"), (3, 135, 140, 5, 12, "C")])
    layer, ro = semantic.local(db)
    by = {r["level"]: r for r in semantic.rows(layer, ro, {
        "metrics": ["kill_xp", "kills_without_xp", "kill_xp_per_online_hour"], "group_by": ["level"]})}
    assert (by[10]["kill_xp"], by[10]["kills_without_xp"], by[10]["kill_xp_per_online_hour"]) == (None, 1, None)
    assert by[11]["kill_xp"] == 60 and by[11]["kill_xp_per_online_hour"] == pytest.approx(60 / (10 / 3600))
    assert (by[12]["kill_xp"], by[12]["kill_xp_per_online_hour"]) == (0, 0)
    assert one(semantic.rows(layer, ro, {"metrics": ["kill_xp"]}))["kill_xp"] is None
