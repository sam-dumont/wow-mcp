"""The semantic layer over Altoholic's character sheet stats and lockouts
(owner, 2026-11-22): each metric with and without the altoholic rows, the
Recorder's estimate where one exists, and the caveat naming Altoholic as
what gives more. Adding them moves no existing definition, so a kept
finding that reads none of them keeps its hash (#157)."""

from __future__ import annotations

import copy
import json
import warnings

import pytest

from tests.analysis.test_prep_altoholic import CHAR, NOW, polestar, rec, shipped
from wowstats.analysis import TABLES, definitions, prep, semantic

warnings.filterwarnings("ignore", module="boring_semantic_layer")

NEW_MODELS = ("char_stats", "lockouts")
NEW_SNAPSHOTS = {"measures": ("rested_xp",), "dimensions": ("snapshot_source",)}
NEW_SQL = ("sem_char_stats", "sem_lockouts")
NEW_TABLES = ("char_stats", "lockouts")


def ask(rows, **q):
    db = prep.build(rows, CHAR, None, now=NOW)
    return semantic.rows(*semantic.local(db), q)


def one(rows):
    assert len(rows) == 1, rows
    return rows[0]


@pytest.fixture
def alto(shipper, tmp_path):
    return shipped(shipper, tmp_path)


def test_adding_them_moves_no_existing_definition():
    now = semantic._file()
    old = copy.deepcopy(now)
    for m in NEW_MODELS:
        del old[m]
    for section, names in NEW_SNAPSHOTS.items():
        for n in names:
            del old["snapshots"][section][n]
    old_sql = {k: v for k, v in semantic.VIEWS.items() if k not in NEW_SQL}
    old_tables = {k: v for k, v in TABLES.items() if k not in NEW_TABLES}
    g_old = definitions.graph(old, old_sql, semantic._WHO, old_tables)
    g_new = definitions.graph(now, semantic.VIEWS, semantic._WHO, TABLES)
    assert sorted(k for k, node in g_old.items() if g_new.get(k) != node) == []
    assert len(g_new) > len(g_old)
    assert definitions.hash_of(["metric:money_copper"], g_old) == definitions.hash_of(["metric:money_copper"], g_new)


def test_character_sheet_with_altoholic(alto):
    got = one(ask(alto, metrics=["sheet_strength", "sheet_intellect", "spell_crit_pct", "melee_crit_pct",
                                 "spell_hit_rating", "honorable_kills_lifetime", "stat_saves"]))
    assert got["stat_saves"] == 1 and got["sheet_strength"] == 45
    assert got["spell_crit_pct"] == 6.5 and got["honorable_kills_lifetime"] == 12
    assert got["spell_hit_rating"] is not None


def test_honorable_kills_gained_needs_two_saves(alto):
    got = one(ask(alto, metrics=["honorable_kills_gained"]))
    assert got["honorable_kills_gained"] is None and got["honorable_kills_gained_evidence"] == 1
    later = [dict(r) for r in alto if r.get("kind") == "stats"]
    for r in later:
        r.update(_time="2026-11-21T20:00:00Z", honorable_kills=20, source_id=r["source_id"] + "b")
    got = one(ask(alto + later, metrics=["honorable_kills_gained"]))
    assert got["honorable_kills_gained"] == 8


def test_lockouts_with_altoholic(alto):
    got = one(ask(alto, metrics=["lockouts_active", "lockouts_seen", "raid_lockouts_active", "lockout_bosses_left",
                                 "lockout_next_reset_t", "weekly_reset_day"], group_by=["lockouts_basis"]))
    assert got["lockouts_basis"] == "altoholic"
    assert (got["lockouts_active"], got["lockouts_seen"], got["raid_lockouts_active"]) == (1, 2, 1)
    assert got["weekly_reset_day"] == 2 and got["lockout_next_reset_t"] > NOW.timestamp()
    by = {r["instance"]: r for r in ask(alto, metrics=["lockouts_active", "lockout_bosses_left"],
                                         group_by=["instance"])}
    assert by["Onyxia's Lair"]["lockouts_active"] == 1 and by["Onyxia's Lair"]["lockout_bosses_left"] == 0


def test_without_altoholic_unknown_or_the_recorders_estimate():
    base = [polestar("2026-11-20T18:39:30Z", 5)]
    got = one(ask(base, metrics=["sheet_strength", "honorable_kills_lifetime", "stat_saves", "lockouts_active",
                                 "lockout_next_reset_t", "weekly_reset_day", "rested_xp"]))
    assert got["stat_saves"] == 0 and got["sheet_strength"] is None and got["honorable_kills_lifetime"] is None
    assert got["lockouts_active"] == 0 and got["lockout_next_reset_t"] is None and got["weekly_reset_day"] is None
    assert got["rested_xp"] is None
    # A raid boss killed 2 days ago, a five-player one, and one a month ago.
    kills = [rec("2026-11-20T21:00:00Z", "encounter", encounter_id=1084, success=1, size=40),
             rec("2026-11-20T21:30:00Z", "encounter", encounter_id=1085, success=1, size=40),
             rec("2026-11-19T20:00:00Z", "encounter", encounter_id=2000, success=1, size=5),
             rec("2026-10-20T21:00:00Z", "encounter", encounter_id=1084, success=1, size=40),
             rec("2026-11-20T22:00:00Z", "encounter", encounter_id=1086, success=0, size=40)]
    got = one(ask(base + kills, metrics=["lockouts_active", "lockout_bosses_killed", "lockout_bosses_left",
                                         "lockout_next_reset_t"], group_by=["lockouts_basis"]))
    assert got["lockouts_basis"] == "recorder" and got["lockouts_active"] == 1
    assert got["lockout_bosses_killed"] == 2 and got["lockout_bosses_left"] is None
    assert got["lockout_next_reset_t"] is None


def test_altoholics_lockouts_replace_the_estimate(alto):
    kills = [rec("2026-11-20T21:00:00Z", "encounter", encounter_id=1084, success=1, size=40)]
    assert {r["lockouts_basis"] for r in ask(alto + kills, metrics=["lockouts_seen"],
                                              group_by=["lockouts_basis"])} == {"altoholic"}


def test_every_new_metric_names_altoholic():
    new = {n for m in NEW_MODELS for s in ("measures", "calculated_measures")
           for n in (semantic._file()[m].get(s) or {})} | set(NEW_SNAPSHOTS["measures"])
    cat = {m["name"]: m for m in semantic.catalogue()["metrics"]}
    assert sorted(n for n in new if "Altoholic" not in json.dumps(cat[n])) == []


# ------------------------------------------- class_spells and reputation


def test_the_spellbook_makes_class_spells_exact_and_future_spells_missing(alto):
    """The priest's book: 9 known, 3 to come (6074 at 14, 2055 at 20, 8092 at
    10). A spell still to come is never learned; with the book no rank reads
    unknown."""
    rows = alto + [polestar("2026-11-20T18:39:30Z", 5, level=32) | {"class": "PRIEST"}]
    by = {r["spell_status"]: r["spell_ranks_learned"] + r["spells_unknown"] for r in ask(
        rows, metrics=["spell_ranks_learned", "spells_unknown"], group_by=["spell_status"])}
    assert "unknown" not in by
    basis = one(ask(rows, metrics=["spells_unknown"], group_by=["spells_basis"]))
    assert basis["spells_basis"] == "altoholic" and basis["spells_unknown"] == 0
    db = prep.build(rows, CHAR, None, now=NOW)
    layer, ro = semantic.local(db)
    _, got, _ = ro.run("SELECT spell_id, status FROM sem_class_spells WHERE spell_id IN (6074, 2055, 8092)", 100)
    assert got and all(st != "learned" for _, st in got)


def test_reputation_without_the_recorder(alto):
    by = {r["faction_id"]: r for r in ask(alto, metrics=["rep_changes", "rep_standing_now", "rep_reaction_now"],
                                          group_by=["faction_id"])}
    assert by[72]["rep_reaction_now"] == 5 and by[72]["rep_standing_now"] == 4200
    assert by[69]["rep_reaction_now"] == 6 and all(r["rep_changes"] == 0 for r in by.values())
    assert {r["rep_source"] for r in ask(alto, metrics=["rep_gained"], group_by=["rep_source"])} == {"altoholic"}


def test_without_altoholic_reputation_is_the_recorders():
    rows = [polestar("2026-11-20T18:39:30Z", 5),
            rec("2026-11-20T18:00:00Z", "rep", faction_id=72, reaction=5, standing=4000, rep_gained=25)]
    got = one(ask(rows, metrics=["rep_changes", "rep_standing_now"], group_by=["rep_source"]))
    assert got == {"rep_source": "recorder", "rep_changes": 1, "rep_standing_now": 4000}
