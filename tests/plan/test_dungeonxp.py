"""Kill XP of a dungeon not run yet (wowstats.plan.dungeonxp): the model,
the labels per dungeon, the licence split of its data, the checks against
recorded runs, and the MCP answer that carries it."""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

from wowstats.mcp import plantools, tools
from wowstats.plan import dungeongain as G
from wowstats.plan import dungeonview as V
from wowstats.plan import dungeonxp as D

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "dungeons"
RULE = G.kill_rule()
MAIN = json.loads((DATA / "kill-xp.json").read_text())
FS = json.loads((DATA / "forever-spawns" / "forever-spawns.json").read_text())

ESTIMATED_NEW = {"Ruins of Lordaeron", "The Drowned City", "City of Dalaran"}
NOT_YET = {"The Hall of Thanes", "Excavation Site: Wetlands", "Krol'dok Stronghold", "Alcaz Prison",
           "Blackmaw Hold", "Shaper's Terrace"}


# ---------------------------------------------------------------- the model


def test_one_kill_is_classic_times_elite_share_and_factor():
    # Level 20, a same-level elite: 145 x 2.5 x 1.4/5 x 0.6.
    assert D.per_kill(20, 20, 20, True, RULE) == pytest.approx(145 * 2.5 * 1.4 / 5 * 0.6)
    assert D.per_kill(20, 20, 20, False, RULE) == pytest.approx(145 * 1.4 / 5 * 0.6)
    # The level range is taken evenly.
    lo, hi = D.per_kill(30, 28, 28, True, RULE), D.per_kill(30, 30, 30, True, RULE)
    assert D.per_kill(30, 28, 30, True, RULE) == pytest.approx((lo + D.per_kill(30, 29, 29, True, RULE) + hi) / 3)
    # No boss multiplier: a boss is an elite.
    assert D.per_kill(31, 29, 29, True, RULE) < 75
    # A grey mob pays nothing; so does one more than 12 under the party's highest.
    assert D.per_kill(31, 23, 23, True, RULE) == 0
    assert D.per_kill(30, 25, 25, True, RULE) > 0 and D.per_kill(30, 25, 25, True, RULE, top=38) == 0
    # A raid: no group bonus, its own size.
    assert D.per_kill(58, 58, 58, True, RULE, party=10) == pytest.approx(335 * 2.5 / 10 * 0.6)


def test_the_constants_are_the_approved_ones():
    assert (D.FACTOR, D.ELITE, D.GROUP_RATE[5], D.GAP, D.SHARE) == (0.6, 2.5, 1.4, 12, 0.85)
    # Classic's zero difference is kept: 12 for levels 30-39 (the 11 lead is in the module's notes).
    assert [r for r in RULE["zero_difference"] if r[0] == 30][0][2] == 12


def test_the_estimate_shape():
    e = D.estimate("The Stockade", 28, RULE)
    assert e["label"] == "estimate" and e["basis"] == "spawns" and e["share"] == 0.85
    assert e["xp"] > 0 and e["kills"] > 0 and e["inside_s"] == pytest.approx(e["kills"] * 1292 / 96, abs=20)
    assert e["xp_per_hour"] == round(e["xp"] / e["inside_s"] * 3600)
    assert e["provenance"]["commit"] == "28ef6259c782928b08a8dc9cacf6bc02e64f2b29" and e["as_of"]
    # Higher level, greyer mobs: less XP.
    assert D.estimate("The Stockade", 32, RULE)["xp"] < e["xp"]
    # Any of the dungeon's names.
    assert D.estimate("Stormwind Stockade", 28, RULE)["xp"] == e["xp"]


def test_a_dungeon_in_wings_has_one_number_per_wing():
    e = D.estimate("Scarlet Monastery: Library", 36, RULE)
    assert e["per"] == "wing" and {w["wing"] for w in e["wings"]} == {"Graveyard", "Library", "Armory", "Cathedral"}
    assert e["xp"] == round(sum(w["xp"] for w in e["wings"]) / 4)
    brs = D.estimate("Blackrock Spire", 58, RULE)
    assert {w["wing"]: w["party"] for w in brs["wings"]} == {"Lower": 5, "Upper": 10}


# ---------------------------------------------------------------- labels


def test_labels_per_dungeon():
    for name, e in MAIN["dungeons"].items():
        if name not in ESTIMATED_NEW | NOT_YET:
            assert e["label"] == "estimate" and e["basis"] == "spawns", name
    assert {n for n, e in MAIN["dungeons"].items() if e["label"] == "not_enough_data"} == NOT_YET
    for n, level in (("Ruins of Lordaeron", 18), ("The Drowned City", 38), ("City of Dalaran", 32)):
        assert MAIN["dungeons"][n]["label"] == "estimate"
        assert D.estimate(n, level, RULE)["xp"] > 0
        # A new dungeon's kills take Dalaran's time per kill.
        e = D.estimate(n, level, RULE)
        assert e["inside_s"] == pytest.approx(e["kills"] * D.seconds_per_kill("new"), rel=0.02)
    for n in NOT_YET:
        e = D.estimate(n, 40, RULE)
        assert e["label"] == "not_enough_data" and e["xp"] is None and e["unlock"]
    assert D.estimate("Nowhere Keep", 30, RULE) is None


def test_own_runs_override_with_the_measure():
    runs = [{"dungeon": "The Deadmines", "level_start": 20, "xp_inside": 5000, "inside_s": 1800,
             "travel_to_s": 0, "travel_back_s": 0}]
    g = G.estimate(level=20, xp_max=30000, name="The Deadmines", runs=runs, others=[], quests=[],
                   questing_xph=12000, rule=RULE)
    assert g["kill_xp"] == {"label": "measured", "xp": 5000, "inside_s": 1800, "xp_per_hour": 10000, "own_runs": 1}
    # Not run: the estimate, labelled, counted in the XP and the verdict
    # (owner, 2026-11-23).
    g = G.estimate(level=20, xp_max=30000, name="The Deadmines", runs=[], others=[], quests=[],
                   questing_xph=12000, rule=RULE)
    assert g["kill_xp"]["label"] == "estimate" and not g["kills_unknown"] and g["vs_questing"] is not None
    assert g["xp_kills"] == g["kill_xp"]["xp"] and g["trip"]["xp_kills"]["label"] == "estimate"


# ---------------------------------------------------------------- licences


def test_the_gpl2_data_is_a_separate_file_with_its_licence():
    lic = (DATA / "forever-spawns" / "LICENSE").read_text()
    assert "GNU GENERAL PUBLIC LICENSE" in lic and "Version 2, June 1991" in lic
    readme = (DATA / "forever-spawns" / "README.md").read_text()
    assert "25be61f58f48b6e7bc8eda1146af8e1c73c82470" in readme and "GPL-2.0-only" in readme
    assert FS["source"]["licence"] == "GPL-2.0-only"
    # The GPL-3.0 file holds no rank or placed count of the new dungeons:
    # its logged-run rows are id, name, levels, boss, kills per run.
    for n in ESTIMATED_NEW:
        assert all(len(m) == 6 for m in MAIN["dungeons"][n]["mobs"]), n
    assert not re.search(r'"(ADV-SNIFF|ADV-CACHE|ANOCORE|health)"', (DATA / "kill-xp.json").read_text())


def test_every_source_is_pinned_and_credited():
    s = MAIN["sources"]
    assert s["cmangos"]["commit"] == "28ef6259c782928b08a8dc9cacf6bc02e64f2b29"
    assert s["eg_link"]["commit"].startswith("5d76a9e5") and "CC BY 4.0" in s["eg_link"]["credit"]
    third = (ROOT / "THIRD_PARTY.md").read_text()
    for needle in ("cmangos/classic-db", "28ef6259c782", "COPYRIGHT.md", "eg-link-output-wowf",
                   "Data from Efficient Games", "advocaite/TrinityCore", "data/dungeons/forever-spawns/"):
        assert needle in third, needle


def test_the_package_and_both_images_carry_the_data():
    assert '"data/dungeons" = "wowstats/data/dungeons"' in (ROOT / "pyproject.toml").read_text()
    assert (ROOT / "Dockerfile").read_text().count("COPY data/dungeons ./data/dungeons") == 2


# ---------------------------------------------------------------- against recorded runs


def test_the_stockade_full_clear_within_ten_percent():
    # One character's full clear at 31 in a party of five: 2,850 XP inside,
    # up to 112 of it rested, so 2,738-2,850 of kill XP (sleeping bag on).
    model = D.full_clear("The Stockade", 31, rule=RULE)
    assert abs(model / ((2738 + 2850) / 2) - 1) <= 0.10, model


def test_dalaran_reference_within_ten_percent_of_its_runs():
    # Two runs at 32: every boss in each, the trash once across both. So
    # every placed creature once is both runs' kill XP minus one set of bosses.
    runs = MAIN["dungeons"]["City of Dalaran"]["reference"]["runs"]
    measured = sum(r["kill_xp"] for r in runs) - D.bosses_xp("City of Dalaran", 32, RULE)
    model = D.full_clear("City of Dalaran", 32, rule=RULE)
    assert abs(model / measured - 1) <= 0.10, (model, measured)
    # A run's estimate at the runs' level is their mean.
    assert D.estimate("City of Dalaran", 32, RULE)["xp"] == round(sum(r["kill_xp"] for r in runs) / len(runs))


# ---------------------------------------------------------------- the MCP


@pytest.fixture(scope="module")
def priest():
    return json.loads(gzip.open(ROOT / "tests" / "data" / "plan-inputs.json.gz").read())


def test_dungeon_options_carries_the_labelled_kill_xp(monkeypatch, tmp_path, priest):
    monkeypatch.setattr(plantools, "_inputs", lambda ctx, args: priest)
    out = plantools.t_dungeon_options(tools.Ctx(store=None, env={"ANALYSIS_STATE_DIR": str(tmp_path)}),
                                      {"char": "Testpriest-Fixture"})
    assert "gain.kill_xp" in out["note"] and "quests and gear" in out["note"]
    seen = {}
    for d in out["dungeons"]:
        k = (d.get("gain") or {}).get("kill_xp")
        if k:
            seen[d["name"]] = k
            assert k["label"] in ("measured", "estimate", "not_enough_data")
            if k["label"] == "estimate":
                assert k["provenance"] and k["as_of"] and k["xp"] is not None
                # The estimate makes the run's XP and its pace, labelled.
                assert not d["gain"]["kills_unknown"] and d["gain"]["xp_kills"] == k["xp"]
                assert d["gain"]["trip"]["xp_kills"]["label"] == "estimate"
    assert any(k["label"] == "measured" for k in seen.values())
    assert any(k["label"] == "estimate" for k in seen.values())
    # The panel says the same.
    panel = {V.key(b["name"]): b["gain"].get("kill_xp") for b in V.build(priest)["dungeons"]}
    for name, k in seen.items():
        if V.key(name) in panel:
            assert panel[V.key(name)] == k, name
