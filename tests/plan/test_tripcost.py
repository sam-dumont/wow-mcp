"""A dungeon trip's pickups, travel and hand-ins (wowstats.plan.tripcost),
Polestar's pick-up and hand-in rules ported: the Python replays the
exporter's reference tours exactly, drops a breadcrumb that costs more than
it gives, orders the givers the way Polestar's own guide does, and follows
the player's quests (done, in the log) and where the player stands."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from wowstats.plan import tripcost as tc

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def trips():
    return json.loads((ROOT / "data" / "route-reference.json").read_text(encoding="utf-8"))["trips"]


def test_python_replays_every_reference_tour(trips):
    # tools/route_reference/trips.mjs wrote each dungeon's tour per side,
    # standing at the entrance at the dungeon's floor with nothing done: the
    # port must land on the same stops, quests and seconds.
    # A dungeon run in wings has a tour per wing too, on that wing's quests.
    seen = wings = 0
    for inst, dg in trips["dungeons"].items():
        for wing, tours in [(None, dg["tours"]), *((w, v["tours"]) for w, v in (dg.get("wings") or {}).items())]:
            for side, letter in (("Alliance", "A"), ("Horde", "H")):
                ref = tours.get(letter)
                got = tc.trip(trips, inst, side=side, level=dg["min"], wing=wing)
                at = (dg["name"], wing, side)
                if not ref:
                    assert not (got and got["quests"]), at
                    continue
                seen += 1
                wings += wing is not None
                assert got["pickup"] == ref["pickup"] and got["handin"] == ref["handin"], at
                assert sorted(got["quests"]) == ref["quests"], at
                for k in ("pickup_s", "travel_to_s", "travel_back_s", "handin_s"):
                    assert round(got[k]) == ref[k], (*at, k)
                assert [{k: c[k] for k in ("id", "detour_s", "far", "taken")} for c in got["breadcrumbs"]] == \
                    ref["breadcrumbs"], at
    assert seen > 30 and wings > 20


def test_a_wing_tour_takes_only_its_quests(trips):
    # Scarlet Monastery's Library for the Alliance: Mythology of the Titans
    # and Past Due are both Library quests; In the Name of the Light (Library,
    # Armory and Cathedral) is on all three of those pages, and none of the
    # Library's books is on the Graveyard's.
    sm = trips["dungeons"]["189"]
    lib = tc.trip(trips, 189, side="Alliance", level=38, wing="Library")
    assert {1050, 96800} <= set(lib["quests"])
    for w in ("Library", "Armory", "Cathedral"):
        assert 1053 in sm["wings"][w]["page"]
    assert not {1050, 96800, 1053} & set(sm["wings"]["Graveyard"]["page"])
    assert tc.trip(trips, 189, side="Alliance", level=38, wing="Nowhere") is None


def test_a_breadcrumb_that_costs_more_than_it_pays_is_dropped(trips):
    # Gnomeregan, Alliance: Castpipe's Task (2925) is a long detour (over
    # Polestar's DETOUR, 300 s), so the trip skips it, as Polestar does.
    dg = tc.dungeon_of(trips, 90)
    assert dg["name"] == "Gnomeregan"
    t = tc.trip(trips, 90, side="Alliance", level=dg["min"])
    crumb = next(c for c in t["breadcrumbs"] if c["id"] == 2925)
    assert crumb["detour_s"] > trips["rules"]["detour_s"] == 300
    assert not crumb["taken"] and 2925 not in t["quests"] and 2925 not in t["prereqs"]


def test_what_the_player_did_or_carries_changes_the_trip(trips):
    dg = tc.dungeon_of(trips, 34)  # the Stockade
    base = tc.trip(trips, 34, side="Alliance", level=dg["min"])
    first = base["quests"][0]
    # Done: off the trip.
    done = tc.trip(trips, 34, side="Alliance", level=dg["min"], done={first})
    assert first not in done["quests"] and done["pickup_s"] <= base["pickup_s"]
    # In the log: no pickup for it, still handed in after the run.
    log = tc.trip(trips, 34, side="Alliance", level=dg["min"], log={first})
    assert first in log["quests"] and log["pickup_s"] <= base["pickup_s"]
    # Everything done: nothing to pick up or hand in.
    gone = tc.trip(trips, 34, side="Alliance", level=dg["min"], done=set(dg["page"]))
    assert gone["quests"] == [] and gone["pickup_s"] == 0 and gone["handin_s"] == 0
    # With every quest done, the only outward leg is from the player's
    # position to the entrance. Pickups can make a city start the shorter tour.
    entrance = tuple(dg["entrance"])
    city = tc.to_world(trips, 1453, 50.0, 50.0)
    direct = tc.trip(trips, 34, side="Alliance", level=dg["min"], done=set(dg["page"]), at=city)
    assert direct["from"] == "you" and gone["from"] == "entrance"
    assert gone["travel_to_s"] == 0 < direct["travel_to_s"]
    assert direct["travel_to_s"] == pytest.approx(tc.travel_for(trips, "Alliance").cost(city, entrance))


# ------------------------------------------------- Polestar's own SFK fixture
# Polestar's tests/fixtures/codex/dungeon-pickup.json (synthetic, MIT): the
# Shadowfang Keep page for a Horde Warlock and the orders its pick-up guide
# takes (rust/crates/forge-addontest/tests/addontest/dungeons-pickup.rs),
# with no boat between the continents, as its stub world. The port plans one
# quest at a time (Polestar's `one_at_a_time`: Bethor's errand, the Dalaran
# Inquiry, Dalar, then Doan Karhan in the Barrens); Polestar 0.4.0's carrying
# (`from_undercity`, Doan Karhan between Bethor and Renferrel) is not ported
# (tools/route_reference/trips.mjs, header).

FRAMES = {"1413": [1, 2622.917, -7510.417, 1612.5, -5143.75], "1421": [0, 3450, -750, 1666.667, -1133.333],
          "1458": [0, 873.193, -86.182, 1877.945, 1237.841]}
NPC = {1498: (1458, 84.5, 17.5), 1937: (1421, 42.8, 40.9), 1938: (1421, 44.2, 39.8),
       1952: (1421, 43.4, 40.9), 2934: (1458, 53.7, 54.5), 6247: (1413, 49.3, 57.2)}
H = 2


def _w(m, x, y):
    c, left, right, top, bottom = FRAMES[str(m)]
    return [c, round(top + (bottom - top) * y / 100, 1), round(left + (right - left) * x / 100, 1)]


def _p(npc):
    return [*_w(*NPC[npc]), 3, f"n{npc}"]


def sfk(real):
    q = {
        1010: {"level": 18, "min": 14, "xp": 300, "side": H, "g": [_p(1498)], "e": [_p(1937)]},
        1011: {"level": 20, "min": 16, "xp": 300, "side": H, "any": [1009, 1010], "g": [_p(1937)], "e": [_p(1938)]},
        1009: {"level": 18, "min": 14, "xp": 300, "side": 1},
        1013: {"level": 26, "min": 16, "xp": 1500, "side": H, "g": [_p(2934)], "e": [_p(2934)]},
        1014: {"level": 27, "min": 18, "xp": 2000, "side": H, "pre": [1011], "g": [_p(1938)], "e": [_p(1938)],
               "obj": 1, "obj_s": 0},
        1098: {"level": 25, "min": 18, "xp": 1500, "side": H, "g": [_p(1952)], "e": [_p(1952)]},
        1740: {"level": 25, "min": 20, "xp": 1500, "classes": 256, "g": [_p(6247)], "e": [_p(6247)]},
        1999: {"level": 24, "min": 18, "xp": 1500, "side": H, "races": 32, "g": [_p(1952)], "e": [_p(1952)]},
    }
    return {"rules": copy.deepcopy(real["rules"]), "frames": FRAMES,
            "graph": {"taxi": [], "legs": [], "transports": []},
            "dungeons": {"33": {"key": "shadowfang-keep", "name": "Shadowfang Keep", "min": 18,
                                "entrance": _w(1421, 44.8, 67.8), "page": [1098, 1740, 1013, 1014, 1999],
                                "tours": {}}},
            "quests": {str(k): v for k, v in q.items()}}


def stops(t):
    """The pick-up stops as Polestar's guide titles them: an NPC, or a
    quest's errand (accept at its giver, hand in at its ender)."""
    names = {"gn1498": "Bethor Iceshard", "gn6247": "Doan Karhan", "gn1938": "Dalar Dawnweaver",
             "q1010": "Orders from the Undercity", "q1011": "The Dalaran Inquiry"}
    return [names[k.split("<")[0]] for k in t["pickup"]]


def test_the_givers_in_polestars_order(trips):
    d = sfk(trips)
    kw = dict(side="Horde", level=21, race="Scourge", done={1098}, log={1013})
    uc = tc.trip(d, 33, cls="WARLOCK", at=tc.to_world(d, 1458, 66, 44), **kw)
    # Not done 1098, Tauren-only 1999, the Book of Ur carried: 1740 and 1014,
    # 1014's chain (1010, then 1011) first.
    assert sorted(uc["quests"]) == [1013, 1014, 1740] and set(uc["prereqs"]) == {1010, 1011}
    assert stops(uc) == ["Orders from the Undercity", "The Dalaran Inquiry", "Dalar Dawnweaver", "Doan Karhan"]
    # From the Barrens, Doan Karhan first (Polestar's barrens_first).
    barrens = tc.trip(d, 33, cls="WARLOCK", at=tc.to_world(d, 1413, 50, 57), **kw)
    assert stops(barrens) == ["Doan Karhan", "Orders from the Undercity", "The Dalaran Inquiry", "Dalar Dawnweaver"]
    # A Mage gets no Warlock quest.
    mage = tc.trip(d, 33, cls="MAGE", at=tc.to_world(d, 1458, 66, 44), **kw)
    assert 1740 not in mage["quests"]
    assert stops(mage) == ["Orders from the Undercity", "The Dalaran Inquiry", "Dalar Dawnweaver"]
