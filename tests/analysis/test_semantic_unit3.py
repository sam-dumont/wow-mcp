"""The 0.7.0 data slice, unit 3: the metrics and views over the tables prep
11 added (reps, offers, quest_rewards, spells_known, flight_nodes,
attunement_steps, item_values, achievements_seen, loot_items'
wanted_quest_id, the timeline's group and ghost columns).

First: adding them moves no existing definition, so no kept finding that
doesn't read them revalidates (wowstats.analysis.definitions). Then each
number recounted from the prep's own rows on the docs site's demo character
(tests/degraded.py: made-up data), the parts the demo lacks on hand-made
Recorder events, and each one with its source taken away: unknown (NULL) or
0 with the caveat naming what gives more, never a made-up value."""

from __future__ import annotations

import copy
import json
import warnings

import pytest

from tests import degraded as D
from tests.analysis.test_prep_slice import CHAR, Rec
from wowstats import analysis
from wowstats.analysis import TABLES, definitions, kb, prep, semantic
from wowstats.mcp import route

warnings.filterwarnings("ignore", module="boring_semantic_layer")

NEW_MODELS = ("dings", "revives", "zone_levels", "offers", "quest_rewards", "quest_loot", "loot_value", "flight_nodes",
              "reputation", "attunements", "achievements", "class_spells", "riding")
NEW_TIMELINE = {"dimensions": ("group_size", "in_raid"),
                "measures": ("solo_s", "grouped_s", "group_unknown_s", "mounted_move_s", "unmounted_move_s",
                             "swim_s", "ghost_s"),
                "calculated_measures": ("group_share",)}
NEW_CROSS = ("loot_value_per_online_hour",)
NEW_VIEWS = ("per_faction", "per_flight_node", "per_quest_giver")
NEW_SQL = ("sem_dings", "sem_revives", "sem_zone_levels", "sem_offers", "sem_quest_rewards", "sem_quest_loot",
           "sem_loot_value", "sem_flight_nodes", "sem_attunement_steps", "sem_achievements_seen", "sem_reps",
           "sem_riding", "sem_class_spells")


def _built(rows, char=D.CHAR, **kw):
    db = prep.build([dict(r) for r in rows], char, route.reference(), advice=analysis.advice_for(rows, char, {}), **kw)
    layer, ro = semantic.local(db)

    def ask(**q):
        return semantic.rows(layer, ro, q)

    def view(name):
        cols, got, cut = ro.run(f"SELECT * FROM {name}", 10_000)
        assert not cut
        return [dict(zip(cols, r, strict=True)) for r in got]
    return {"db": db, "ask": ask, "view": view}


def _cards(iid):
    # Made-up vendor prices: two items in three have one.
    return {"sell": 7 * (iid % 50 + 1)} if iid % 3 else None


@pytest.fixture(scope="module")
def demo():
    return _built(D.base_rows(), cards=_cards)


@pytest.fixture(scope="module", params=["no-recorder", "no-syndicator", "polestar-only"])
def without(request):
    return request.param, _built(D.rows_for(request.param))


def one(rows: list[dict]) -> dict:
    assert len(rows) == 1, rows
    return rows[0]


def q1(db, sql, *args):
    return db.execute(sql, args).fetchone()[0]


# ------------------------------------------------------- nothing old moves


def test_adding_them_moves_no_existing_definition():
    """Today's graph against the same model with this unit's names taken
    out: every node the old graph has is the same, digest and dependencies,
    so a kept finding that doesn't read the new names keeps its hash."""
    now = semantic._file()
    old = copy.deepcopy(now)
    for m in NEW_MODELS:
        del old[m]
    for section, names in NEW_TIMELINE.items():
        for n in names:
            del old["timeline"][section][n]
    for n in NEW_CROSS:
        del old[semantic.CROSS_KEY][n]
    for n in NEW_VIEWS:
        del old[semantic.VIEWS_KEY][n]
    old_sql = {k: v for k, v in semantic.VIEWS.items() if k not in NEW_SQL}
    g_old = definitions.graph(old, old_sql, semantic._WHO, TABLES)
    g_new = definitions.graph(now, semantic.VIEWS, semantic._WHO, TABLES)
    moved = sorted(k for k, node in g_old.items() if g_new.get(k) != node)
    assert moved == []
    assert len(g_new) > len(g_old)
    # And a finding over an old metric hashes the same in both.
    assert definitions.hash_of(["metric:online_s", "view:per_level"], g_old) == \
        definitions.hash_of(["metric:online_s", "view:per_level"], g_new)


def test_quest_text_feeds_no_model():
    for name, sql in semantic.VIEWS.items():
        assert "quest_text" not in definitions._words(sql), name


# ------------------------------------------------- the demo, recounted


def test_group_time_splits_online_time(demo):
    got = one(demo["ask"](metrics=["solo_s", "grouped_s", "group_unknown_s", "online_s", "group_share"]))
    assert got["solo_s"] + got["grouped_s"] + got["group_unknown_s"] == pytest.approx(got["online_s"])
    db = demo["db"]
    assert got["grouped_s"] == pytest.approx(q1(db, "SELECT SUM(dur_s) FROM timeline WHERE group_size > 0"))
    assert got["grouped_s"] > 0 and got["solo_s"] > 0
    assert got["group_share"] == pytest.approx(got["grouped_s"] / (got["grouped_s"] + got["solo_s"]))


def test_travel_modes_stay_inside_online_time(demo):
    got = one(demo["ask"](metrics=["mounted_move_s", "unmounted_move_s", "swim_s", "ghost_s", "taxi_s", "online_s"]))
    assert got["ghost_s"] == pytest.approx(q1(demo["db"], "SELECT SUM(dur_s) FROM timeline WHERE ghost = 1"))
    assert got["unmounted_move_s"] > 0
    assert sum(got[k] for k in ("mounted_move_s", "unmounted_move_s", "swim_s", "ghost_s", "taxi_s")) \
        <= got["online_s"] + 1e-6
    # Never more than 20 s past a position: a stop doesn't read as walking.
    cap = q1(demo["db"], "SELECT SUM(MIN(dur_s, MAX(20 - since_pos_s, 0))) FROM timeline WHERE speed > 0 "
                         "AND mounted = 0 AND taxi = 0 AND ride = 0 AND swimming = 0 AND ghost = 0 AND dead = 0")
    assert got["unmounted_move_s"] == pytest.approx(cap)


def test_revives_and_corpse_runs(demo):
    db = demo["db"]
    got = one(demo["ask"](metrics=["revives", "corpse_runs", "spirit_res", "player_res", "revive_s", "corpse_run_s",
                                   "avg_corpse_run_s", "dead_s"]))
    hows = dict(db.execute("SELECT detail, COUNT(*) FROM spans WHERE kind = 'dead' GROUP BY 1").fetchall())
    assert got["corpse_runs"] == hows.get("c", 0) > 0
    assert got["spirit_res"] == hows.get("s", 0)
    assert got["revives"] == got["corpse_runs"] + got["spirit_res"] + got["player_res"]
    assert got["revive_s"] == pytest.approx(got["dead_s"])
    assert 0 < got["corpse_run_s"] <= got["revive_s"]
    assert got["avg_corpse_run_s"] == pytest.approx(got["corpse_run_s"] / got["corpse_runs"])
    # Per death, joined on the records model's death_t.
    per = demo["ask"](metrics=["deaths", "revive_s"], group_by=["death_t"])
    assert all(r["deaths"] == 1 for r in per if r["death_t"] is not None)
    assert sum(r["deaths"] for r in per) == got["revives"]


def test_zone_time_by_level_sums_to_zone_time(demo):
    by_zone = {r["zone"]: r for r in demo["ask"](metrics=["zone_level_s", "zone_s"], group_by=["zone"])}
    for z, r in by_zone.items():
        assert r["zone_level_s"] == pytest.approx(r["zone_s"], abs=1e-3), z
    cut = demo["ask"](metrics=["zone_level_s"], group_by=["zone", "level"])
    assert len(cut) > len(by_zone)
    # A level's parts sit between its dings.
    lv = dict(demo["db"].execute("SELECT level, ding_t FROM levels WHERE ding_t IS NOT NULL").fetchall())
    for r in cut:
        if r["level"] in lv and r["level"] - 1 in lv:
            assert r["zone_level_s"] <= lv[r["level"]] - lv[r["level"] - 1] + 1e-3


def test_quest_givers_and_rewards(demo):
    db = demo["db"]
    got = one(demo["ask"](metrics=["giver_windows", "quests_offered", "quests_skipped", "quests_held_back"]))
    assert got["quests_offered"] == q1(db, "SELECT COUNT(DISTINCT quest_id) FROM offers WHERE state = 'available'")
    assert got["giver_windows"] == q1(db, "SELECT COUNT(DISTINCT rec_id) FROM offers")
    rows = demo["view"]("per_quest_giver")
    assert {r["npc_id"] for r in rows} == {n for (n,) in db.execute("SELECT DISTINCT npc_id FROM offers")}
    assert all(r["giver_zone"] for r in rows)
    rw = one(demo["ask"](metrics=["reward_choice_quests", "rewards_offered", "rewards_picked"]))
    assert rw["reward_choice_quests"] == q1(db, "SELECT COUNT(DISTINCT quest_id) FROM quest_rewards "
                                                "WHERE kind = 'choice'")
    assert rw["rewards_picked"] == q1(db, "SELECT COUNT(*) FROM quest_rewards WHERE chosen = 1")


def test_wanted_loot_and_vendor_value(demo):
    db = demo["db"]
    got = one(demo["ask"](metrics=["wanted_looted_items", "loot_vendor_copper", "loot_items_unpriced",
                                   "loot_value_per_online_hour", "online_s"]))
    assert got["wanted_looted_items"] == q1(db, "SELECT SUM(count) FROM loot_items WHERE wanted_quest_id IS NOT NULL")
    assert got["loot_vendor_copper"] == q1(db, "SELECT SUM(li.count * v.vendor_copper) FROM loot_items li "
                                               "JOIN item_values v USING (item_id)") > 0
    assert got["loot_items_unpriced"] > 0
    assert got["loot_value_per_online_hour"] == pytest.approx(got["loot_vendor_copper"] / (got["online_s"] / 3600))
    by_quest = demo["ask"](metrics=["wanted_looted_items"], group_by=["quest_id"])
    assert all(r["quest_id"] is not None for r in by_quest)


def test_flight_nodes(demo):
    db = demo["db"]
    got = one(demo["ask"](metrics=["known_flight_nodes", "flight_maps_opened"]))
    assert got["known_flight_nodes"] == q1(db, "SELECT COUNT(*) FROM flight_nodes WHERE known_now = 1") > 0
    rows = demo["view"]("per_flight_node")
    assert len(rows) == q1(db, "SELECT COUNT(*) FROM flight_nodes")
    assert sum(r["node_flights_from"] for r in rows) == q1(db, "SELECT SUM(flights_from) FROM flight_nodes")


def test_reputation_on_the_demo(demo):
    rows = demo["view"]("per_faction")
    r = one(rows)
    want = demo["db"].execute("SELECT faction_id, rep_points, reaction, rep_gained FROM reps").fetchone()
    assert (r["faction_id"], r["rep_standing_now"], r["rep_reaction_now"], r["rep_gained"]) == want
    # One change: no window, so no pace.
    assert r["rep_online_s"] is None and r["rep_per_online_hour"] is None and r["rep_to_next_s"] is None


def test_attunements_and_achievements(demo):
    db = demo["db"]
    rows = demo["ask"](metrics=["attunement_steps", "attunement_steps_done", "attunement_next_quest"],
                       group_by=["attunement"])
    for r in rows:
        assert r["attunement_steps"] == q1(db, "SELECT COUNT(*) FROM attunement_steps WHERE attunement = ?",
                                           r["attunement"])
        assert r["attunement_next_quest"] == q1(db, "SELECT quest_id FROM attunement_steps WHERE attunement = ? "
                                                    "AND done = 0 ORDER BY step LIMIT 1", r["attunement"])
    assert one(demo["ask"](metrics=["achievements_seen"]))["achievements_seen"] == \
        q1(db, "SELECT COUNT(*) FROM achievements_seen")


def test_class_spells_partition_the_trainer_list(demo):
    db = demo["db"]
    by = {r["spell_status"]: r for r in demo["ask"](
        metrics=["spells_trainable_now", "spells_unknown", "spell_ranks_learned"], group_by=["spell_status"])}
    assert set(by) <= {"learned", "missing", "talent", "unknown"}
    assert by["learned"]["spell_ranks_learned"] >= q1(db, "SELECT COUNT(*) FROM spells_known")
    assert by["missing"]["spells_trainable_now"] > 0 and by["unknown"]["spells_unknown"] > 0
    first = q1(db, "SELECT level FROM records WHERE level IS NOT NULL ORDER BY t, rec_id LIMIT 1")
    now = q1(db, "SELECT MAX(level) FROM levels")
    levels = demo["ask"](metrics=["spells_trainable_now"], group_by=["spell_level"])
    assert all(first < r["spell_level"] <= now for r in levels if r["spells_trainable_now"])
    caveat = next(m for m in semantic.catalogue()["metrics"] if m["name"] == "spells_unknown")["caveats"]
    assert "Ranks learned before the Recorder unknown" in caveat


def test_riding_gap_against_the_newest_money(demo):
    db = demo["db"]
    money = q1(db, "SELECT money_copper FROM snapshots WHERE money_copper IS NOT NULL ORDER BY t DESC LIMIT 1")
    rules, _ = kb.load_rules(kb.rules_paths())
    rows = {r["tier"]: r for r in demo["ask"](metrics=["riding_cost_copper", "riding_gold_gap_copper"],
                                              group_by=["tier"])}
    for tier in ("riding.apprentice", "riding.journeyman"):
        cost = rules[tier]["value"]["training_copper"]
        assert rows[tier]["riding_cost_copper"] == cost
        assert rows[tier]["riding_gold_gap_copper"] == max(cost - money, 0)


def test_riding_rank_reads_the_spellbook_too(demo):
    """Apprentice Riding trained while the Recorder wasn't recording: no
    skill record, but Altoholic's spellbook (DataStore_Spells, the shipper's
    wow-altoholic kind=spells) lists 33388 known. The rank is 75 and the
    Apprentice gap is 0 (wowstats.riding, 2026-11-24)."""
    before = one(demo["ask"](metrics=["riding_rank", "riding_book_rank"]))
    assert before == {"riding_rank": 0, "riding_book_rank": 0}
    book = {"_time": "2026-01-05T18:00:00.000Z", "feed": "wow-altoholic", "kind": "spells", "char": D.CHAR,
            "source": "altoholic", "spells": "[[6603,0],[33388,0],[33391,60]]"}
    got = _built([*D.base_rows(), book])
    assert one(got["ask"](metrics=["riding_rank", "riding_book_rank"])) == {"riding_rank": 75,
                                                                             "riding_book_rank": 75}
    gaps = {r["tier"]: r["riding_gold_gap_copper"] for r in got["ask"](metrics=["riding_gold_gap_copper"],
                                                                        group_by=["tier"])}
    assert gaps["riding.apprentice"] == 0 and gaps["riding.journeyman"] > 0


def test_the_new_views_have_one_row_per_key(demo):
    for name, key in (("per_faction", "faction_id"), ("per_flight_node", "node_id"), ("per_quest_giver", "npc_id")):
        rows = demo["view"](name)
        assert rows and len({r[key] for r in rows}) == len(rows), name


def test_quest_text_never_reaches_a_view(demo):
    titles = {t for (t,) in demo["db"].execute("SELECT title FROM quest_text WHERE title IS NOT NULL")}
    for name in NEW_VIEWS:
        for r in demo["view"](name):
            assert not titles & {v for v in r.values() if isinstance(v, str)}, name


# ------------------------------------------------- hand-made Recorder events


def _rec_built(r, **kw):
    return _built(r.out, char=CHAR, **kw)


def test_played_total_at_the_ding():
    r = Rec()
    r.session()
    r("level", 60, level=31, played_s=360000)
    r("level", 60, level=32, played_s=380000)
    r("level", 60, level=33)  # no played time on the record
    b = _rec_built(r)
    assert one(b["ask"](metrics=["dings_seen"]))["dings_seen"] == 3
    by = {x["level"]: x["played_total_at_ding_s"] for x in b["ask"](metrics=["played_total_at_ding_s"],
                                                                      group_by=["level"])}
    assert by == {31: 360000, 32: 380000, 33: None}


def test_rep_pace_and_time_to_the_next_tier():
    r = Rec()
    r.session()
    r("rep", faction_id=2740, reaction=5, standing=3000 + 2000, rep_gained=10)
    for _ in range(30):  # 30 online minutes, a position a minute: one login span
        r("pos", 60, flags=0, speed=0, map_id=1411, x=50, y=50)
    r("rep", faction_id=2740, reaction=5, standing=3000 + 2600, rep_gained=600)
    row = one(_rec_built(r)["view"]("per_faction"))
    assert row["rep_changes"] == 2 and row["rep_gained"] == 610
    # Only the second change has a window: about 1,800 online seconds.
    assert row["rep_online_s"] == pytest.approx(1801, abs=2)
    # The pace reads the eligible change only: the first has no window.
    assert row["rep_per_online_hour"] == pytest.approx(600 / (row["rep_online_s"] / 3600))
    # Friendly ends at 3,000 + 6,000 (the KB's widths): 3,400 to go.
    assert row["rep_to_next_points"] == 3400
    assert row["rep_to_next_s"] == pytest.approx(3400 / (600 / row["rep_online_s"]))


def test_a_quest_never_taken_reads_skipped():
    r = Rec()
    r.session()
    r("offer", npc_kind="c", npc_id=3139, quests_available="840;841", quests_not_offered="842")
    r("accept", quest_id=840)
    got = one(_rec_built(r)["view"]("per_quest_giver"))
    assert (got["npc_id"], got["quests_offered"], got["quests_skipped"], got["quests_held_back"]) == (3139, 2, 1, 1)


def test_loot_value_is_the_vendor_price_only():
    r = Rec()
    r.session()
    r("loot", items="900:2;901:1")
    cards = {900: {"sell": 25, "ah": 99999}}
    got = one(_rec_built(r, cards=cards.get)["ask"](metrics=["loot_vendor_copper", "loot_items_unpriced"]))
    assert got == {"loot_vendor_copper": 50, "loot_items_unpriced": 1}
    desc = next(m for m in semantic.catalogue()["metrics"] if m["name"] == "loot_vendor_copper")
    assert "Vendor value only" in desc["caveats"]


def test_a_spell_learned_counts_its_earlier_ranks():
    r = Rec()
    r.session()
    rep = semantic.VIEWS["sem_class_spells"]
    assert "PRIEST" in rep
    r("learn", spell_id=984)  # Smite rank 4 (level 22): ranks 2 and 3 are learned too
    b = _rec_built(r)
    rows = b["ask"](metrics=["spell_ranks_learned", "spells_unknown"], group_by=["spell", "rank"])
    smite = {x["rank"]: (x["spell_ranks_learned"], x["spells_unknown"]) for x in rows if x["spell"] == "Smite"}
    assert smite[2] == smite[3] == smite[4] == (1, 0)
    # Rank 5 is from level 30, the Recorder's first: learned before it, or not.
    assert smite[5] == (0, 1)
    assert 6 not in smite  # level 38: above the character's


# ------------------------------------------------- degraded


def test_without_a_source_unknown_or_zero_never_made_up(without):
    scenario, b = without
    got = one(b["ask"](metrics=["revives", "known_flight_nodes", "giver_windows", "rep_changes", "group_share",
                                "loot_vendor_copper", "spells_trainable_now", "spell_ranks_learned",
                                "achievements_seen"]))
    if scenario in ("no-recorder", "polestar-only"):
        assert got["revives"] == 0 and got["known_flight_nodes"] == 0 and got["giver_windows"] == 0
        assert got["rep_changes"] == 0 and got["achievements_seen"] == 0
        assert got["group_share"] is None and got["loot_vendor_copper"] is None
        # No Recorder: nothing seen learned, nothing called missing.
        assert got["spells_trainable_now"] == 0 and got["spell_ranks_learned"] == 0
        assert b["view"]("per_faction") == [] and b["view"]("per_quest_giver") == []
    else:
        assert got["revives"] > 0 and got["known_flight_nodes"] > 0


def test_every_new_metric_names_what_gives_more():
    hints = ("/psrec on", "Altoholic", "Questie", "flight map", "log out once", "trainer visit", "Battle.net",
             "the KB", "KB's", "update the Recorder", "dashboard_refresh")
    new = {n for m in NEW_MODELS for s in ("measures", "calculated_measures")
           for n in (semantic._file()[m].get(s) or {})}
    new |= {n for names in NEW_TIMELINE.values() for n in names if n not in NEW_TIMELINE["dimensions"]}
    new |= set(NEW_CROSS)
    cat = {m["name"]: m for m in semantic.catalogue()["metrics"]}
    lacking = sorted(n for n in new if not any(h in json.dumps(cat[n]) for h in hints))
    assert lacking == []


def test_altoholics_spellbook_makes_the_trainer_list_exact():
    """With Altoholic's spellbook in spells_known (source altoholic: another
    change adds it to the prep; rows put in by hand here), no rank reads
    unknown: every rank not known is missing, from any level."""
    r = Rec()
    r.session()
    r("learn", spell_id=984)  # Smite rank 4, seen by the Recorder
    db = prep.build([dict(x) for x in r.out], CHAR, None)
    est = semantic.rows(*semantic.local(db), {"metrics": ["spells_unknown", "spells_trainable_now"],
                                               "group_by": ["spells_basis"]})
    assert one(est)["spells_basis"] == "recorder" and one(est)["spells_unknown"] > 0
    # Altoholic's spellbook: Power Word: Fortitude rank 2 (spell 1244, level 12).
    db.execute("INSERT INTO spells_known (spell_id, t, level, source) VALUES (1244, 0, 30, 'altoholic')")
    layer, ro = semantic.local(db)
    got = one(semantic.rows(layer, ro, {"metrics": ["spells_unknown", "spells_trainable_now", "spell_ranks_learned"],
                                        "group_by": ["spells_basis"]}))
    assert got["spells_basis"] == "altoholic" and got["spells_unknown"] == 0
    assert got["spells_trainable_now"] == one(est)["spells_trainable_now"] + one(est)["spells_unknown"] - 2
    by = {(x["spell"], x["rank"]): x["spell_status"] for x in semantic.rows(
        layer, ro, {"metrics": ["spell_ranks_learned"], "group_by": ["spell", "rank", "spell_status"]})}
    assert by[("Power Word: Fortitude", 2)] == by[("Smite", 4)] == "learned"
    assert by[("Smite", 5)] == "missing"
