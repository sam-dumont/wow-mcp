"""The store models (semantic.yaml `store_models`: players, prices,
talent_changes, run_groups): each metric equals the plain count, sum,
latest or low the tools computed by hand before (owner, 2026-11-22: every
tool reads through the semantic layer), on synthetic rows, and grouping by
the model's key fans nothing out: one row per key, sums that add up to the
total. Nothing is kept: semantic.read loads the relation for the call."""

from __future__ import annotations

import json
import random

import pytest

from wowstats import storemodels
from wowstats.analysis import semantic


def _players(seed: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    names = [f"Player{i}" for i in range(12)]
    days = [{"player": rng.choice(names), "context": rng.choice(["seen", "grouped", "dungeon", "whisper"]),
             "_time": f"2026-11-{rng.randint(1, 20):02d}T10:00:00Z", "n": rng.randint(1, 9)} for _ in range(120)]
    info = [{"player": p, "last_seen": f"2026-11-{rng.randint(1, 20):02d}T12:00:00Z", "level": rng.randint(10, 40),
             "classes": json.dumps([rng.choice(["MAGE", "PRIEST"])]), "races": json.dumps(["Human"]),
             "zones": json.dumps(["Duskwood", "Westfall"])} for p in names]
    return days, info


@pytest.mark.parametrize("seed", range(5))
def test_players_equal_the_hand_counts(seed):
    days, info = _players(seed)
    got = storemodels.players(days, info, lambda p: p != "Player0")
    want: dict[str, dict] = {}
    for r in days:
        if r["player"] == "Player0":
            continue
        d = want.setdefault(r["player"], {"days": set(), "gd": set(), "grouped": 0, "sightings": 0, "ctx": {}})
        day = r["_time"][:10]
        d["days"].add(day)
        d["sightings"] += r["n"]
        d["ctx"][r["context"]] = d["ctx"].get(r["context"], 0) + r["n"]
        if r["context"] in storemodels.GROUPED:
            d["grouped"] += r["n"]
            d["gd"].add(day)
    assert set(got) == set(want)
    meta = {r["player"]: r for r in info}
    for p, w in want.items():
        g = got[p]
        assert (g["days"], g["sightings"], g["grouped"]) == (len(w["days"]), w["sightings"], w["grouped"])
        assert g["day_list"] == sorted(w["days"]) and g["group_days"] == sorted(w["gd"])
        assert g["group_days_n"] == len(w["gd"]) and g["contexts"] == w["ctx"]
        assert g["last_seen"] == meta[p]["last_seen"] and g["level"] == meta[p]["level"]


def _prices(seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for i in range(6):
        for day in rng.sample(range(1, 28), rng.randint(1, 5)):
            out.append({"_time": f"2026-11-{day:02d}T08:00:00Z", "item_key": f"k{i}", "item_name": f"Item {i}",
                        "item_id": 100 + i, "price_copper": rng.randint(50, 5000),
                        "price_high_copper": rng.choice([None, rng.randint(5000, 9000)])})
    return out


@pytest.mark.parametrize("seed", range(5))
def test_prices_equal_the_hand_reads(seed):
    rows = _prices(seed)
    got = storemodels.prices(rows)
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r["item_id"]), []).append(r)
    assert set(got) == set(by)
    for k, rs in by.items():
        rs = sorted(rs, key=lambda r: r["_time"])
        g = got[k]
        assert g["price_scan_days"] == len(rs)
        assert g["price_latest_copper"] == rs[-1]["price_copper"]
        assert g["price_latest_high_copper"] == rs[-1]["price_high_copper"]
        assert g["price_min_copper"] == min(r["price_copper"] for r in rs)
        assert g["price_max_copper"] == max(r["price_copper"] for r in rs)
        # min_evidence: no change from one scan day.
        want = rs[-1]["price_copper"] - rs[0]["price_copper"] if len(rs) >= 2 else None
        assert g["price_change_copper"] == want


def test_talent_changes_round_trip_newest_first():
    changes = [{"t": f"2026-11-0{i + 1}T20:00:00Z", "date": f"2026-11-0{i + 1}", "before": [i, 0, 0], "after": [i + 1, 0, 0],
                "gained": [{"name": f"T{i}", "from": 0, "to": 1}], "dropped": []} for i in range(4)]
    got = storemodels.talent_changes(changes)
    assert got == [{k: c[k] for k in ("date", "t", "before", "after", "gained", "dropped")} for c in changes[::-1]]


def test_run_groups_counts_per_run():
    runs = [{"_time": "2026-11-01T10:00:00Z", "name": "Deadmines", "level": 18, "seconds": 3600, "xp": 9000},
            {"_time": "2026-11-02T10:00:00Z", "name": "Wailing Caverns", "level": 20, "seconds": 1800, "xp": 7000}]
    groups = [{"_time": "2026-11-01T09:00:00Z", "kind": "group", "size": "3"},
              {"_time": "2026-11-01T10:10:00Z", "kind": "group", "size": "5"},
              {"_time": "2026-11-01T10:20:00Z", "kind": "encounter", "success": "true"},
              {"_time": "2026-11-01T10:25:00Z", "kind": "encounter", "success": "false"},
              {"_time": "2026-11-01T10:30:00Z", "kind": "death"},
              {"_time": "2026-11-01T10:40:00Z", "kind": "encounter", "success": "1"},
              {"_time": "2026-11-02T10:05:00Z", "kind": "death"}]
    got = storemodels.run_groups(runs, groups)
    assert [r["name"] for r in got] == ["Wailing Caverns", "Deadmines"]
    wc, dm = got
    assert (dm["party_size"], dm["bosses"], dm["wipes"], dm["deaths"]) == (5, 2, 1, 1)
    # The group in force from the day before still counts for the second run.
    assert (wc["party_size"], wc["bosses"], wc["wipes"], wc["deaths"]) == (5, 0, 0, 1)


@pytest.mark.parametrize("seed", range(3))
def test_no_fan_out_by_the_key(seed):
    """By player or item: one row per key, and the sums add up to the total
    asked alone."""
    days, info = _players(seed)
    rel = [{"player": r["player"], "context": r["context"], "day": r["_time"][:10], "sightings": r["n"],
            "last_seen": None, "level": None, "classes": None, "races": None, "zones": None} for r in days]
    total = semantic.read("players", rel, {"metrics": ["player_sightings"]})["rows"][0]["player_sightings"]
    for by in (["player"], ["player", "context"], ["player", "day"]):
        rows = semantic.read("players", rel, {"metrics": ["player_sightings", "player_days"], "group_by": by})["rows"]
        keys = [tuple(r[k] for k in by) for r in rows]
        assert len(keys) == len(set(keys))
        assert sum(r["player_sightings"] for r in rows) == total == sum(r["n"] for r in days)
    prices = _prices(seed)
    got = storemodels.prices(prices)
    assert sum(g["price_scan_days"] for g in got.values()) == len(prices)


def test_store_metrics_carry_the_contract():
    cat = semantic.store_catalogue()
    for m in cat["metrics"]:
        assert m["aggregation"] in semantic.AGGREGATIONS and m["time_meaning"] and m["evidence_basis"], m["name"]
        assert m["source"] == "store"
    prep = {m["name"] for m in semantic.catalogue()["metrics"]}
    assert not prep & {m["name"] for m in cat["metrics"]}


def test_run_party_names_from_the_grouped_stretch():
    """Who was in each run (owner, 2026-11-24): the groupmates ForeverDB_Helper
    named from the moment the character joined the party to the run's end.
    It names each once a day: a second run with the same party finds them
    from the first, and the Recorder's size says how many are missing."""
    runs = [{"_time": "2026-11-01T10:00:00Z", "name": "Deadmines", "level": 18, "seconds": 1800, "xp": 9000},
            {"_time": "2026-11-01T11:00:00Z", "name": "Deadmines", "level": 19, "seconds": 1800, "xp": 9000},
            {"_time": "2026-11-01T15:00:00Z", "name": "Wailing Caverns", "level": 19, "seconds": 1800, "xp": 7000}]
    groups = [{"_time": "2026-11-01T09:40:00Z", "kind": "group", "size": "1"},
              {"_time": "2026-11-01T09:50:00Z", "kind": "group", "size": "4"},
              {"_time": "2026-11-01T12:00:00Z", "kind": "group", "size": "0"},
              {"_time": "2026-11-01T14:55:00Z", "kind": "group", "size": "2"}]
    mate = {"context": "dungeon", "level": 18, "class": "Priest", "race": "Dwarf", "role": "healer"}
    players = [{**mate, "_time": "2026-11-01T09:40:02Z", "player": "Strangerbar-Madeup"},
               {**mate, "_time": "2026-11-01T09:50:01Z", "player": "Strangerfoo-Madeup", "context": "grouped"},
               {**mate, "_time": "2026-11-01T09:00:00Z", "player": "Earlier-Group"},
               {**mate, "_time": "2026-11-01T10:05:00Z", "player": "Seennearby-Madeup", "context": "seen"},
               {**mate, "_time": "2026-11-01T14:55:03Z", "player": "Strangerbar-Madeup", "level": 19}]
    wc, dm2, dm1 = storemodels.run_groups(runs, groups, players)
    names = lambda r: [p["player"] for p in r["party"]]  # noqa: E731
    assert names(dm1) == names(dm2) == ["Strangerbar-Madeup", "Strangerfoo-Madeup"]
    assert dm1["party"][0] == {"player": "Strangerbar-Madeup", "level": 18, "class": "Priest", "race": "Dwarf",
                               "role": "healer"}
    assert dm1["names_known"] == "2 of 4 names known"
    # The party broke up at 12:00: the afternoon run has its own.
    assert names(wc) == ["Strangerbar-Madeup"] and wc["party"][0]["level"] == 19 and wc["names_known"] == "1 of 2 names known"
    # No names, no "0 of N".
    assert storemodels.run_groups(runs, groups)[0]["names_known"] is None
