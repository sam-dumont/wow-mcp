"""The class guide (wowstats.classguide): every class has a sourced guide and
a talent path that Forever's current trees allow, and the character page's
block works for any race, side, class and level, saying nothing about where
the character is."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wowstats import classguide as cg
from wowstats.analysis import kbschema

DATA = Path(__file__).resolve().parent.parent / "data"
ALLOW = kbschema.load_allow(DATA / "kb" / "sources.json")
# What every class's guide answers (the owner's list, 2026-11-19).
NEEDED = {"spec", "talents", "stats", "spells", "solo", "dungeon", "quests", "mistakes", "respec", "habit"}
ALLIANCE_MAPS = {1453, 1455, 1457}
HORDE_MAPS = {1454, 1456, 1458}
ZONE = {1453: "Stormwind City", 1455: "Ironforge", 1457: "Darnassus", 1454: "Orgrimmar", 1456: "Thunder Bluff",
        1458: "Undercity"}

# Characters of every class, both sides, several races and levels: Testpriest
# (Gnome priest 32) and Testhunter (Dwarf hunter, low level) among them.
PEOPLE = [
    ("Priest", "Gnome", "Alliance", 32), ("Hunter", "Dwarf", "Alliance", 5), ("Warrior", "Orc", "Horde", 20),
    ("Paladin", "Undead", "Horde", 24), ("Paladin", "Human", "Alliance", 41), ("Rogue", "Night Elf", "Alliance", 13),
    ("Shaman", "Dwarf", "Alliance", 30), ("Shaman", "Tauren", "Horde", 47), ("Mage", "Troll", "Horde", 58),
    ("Warlock", "Undead", "Horde", 9), ("Druid", "Skyborne", "Horde", 14), ("Druid", "Night Elf", "Alliance", 60),
]


def test_the_reference_has_every_class():
    ref = cg.reference()
    assert set(ref["classes"]) == set(cg.CLASSES)
    for c, v in ref["classes"].items():
        assert v["trainers"] and v["spells"] and len(v["talents"]) >= 45, c
        assert all(r["name"] for rows in v["spells"].values() for r in rows), c
        # Every even level from 4 to 60 trains something: a level the sniffed
        # lists miss (Druid 8 to 14) comes from the client, without a price.
        assert [n for n in range(4, 61, 2) if str(n) not in v["spells"]] == [], c
        for rows in v["spells"].values():
            assert all((r.get("from") == "client") == (r["cost_copper"] is None) for r in rows), c


@pytest.mark.parametrize("cls", cg.CLASSES)
def test_every_class_has_a_playable_sourced_path(cls):
    p = cg.path(cls)
    assert p, f"{cls}: no talent path"
    assert kbschema.check(p, ALLOW) == [], cls
    assert cg.check_path(cls) == [], cls
    assert p["spec"] and p["based_on"]


@pytest.mark.parametrize("cls", cg.CLASSES)
def test_every_class_covers_the_topics(cls):
    st = cg.statements(cls)
    assert {s["topic"] for s in st} >= NEEDED, NEEDED - {s["topic"] for s in st}
    for s in st:
        assert s["topic"] in cg.TOPICS and s["text"].strip(), s["id"]
        lo, hi = s["levels"]
        assert 1 <= lo <= hi <= 60, s["id"]
        assert "—" not in s["text"] + (s.get("action") or ""), s["id"]  # no em dashes
    habits = [s for s in st if s["topic"] == "habit"]
    assert len(habits) >= 2 and all(0 < len(s["action"]) <= 90 for s in habits), cls
    assert any(s["scope"] == "forever" for s in st if s["topic"] == "stats"), cls


def test_lane_records_name_their_class():
    lane = cg.lane()
    assert lane["lane"] == "classes"
    for k in ("statements", "contradictions"):
        assert all(r.get("class") in cg.CLASSES for r in lane[k]), k
    assert set(lane["paths"]) == set(cg.CLASSES)


@pytest.mark.parametrize("cls,race,side,level", PEOPLE, ids=lambda x: str(x))
def test_block_for_any_character(cls, race, side, level):
    b = cg.block({"class": cls, "race": race, "faction": side, "level": level})
    assert b and b["class"] == cls.upper() and b["level"] == level
    # Trainers: the capitals of the character's side (Zephras Isle for a
    # Skyborne), its race's capital first; never the other side's.
    ts = b["trainers"]
    assert ts, (cls, race)
    other = HORDE_MAPS if side == "Alliance" else ALLIANCE_MAPS
    assert not {t["zone"]["name"] for t in ts} & {ZONE[m] for m in other}
    home = cg.RACE_HOME[cg._letters(race)]
    has_home = any(t["map"] == home for t in cg.reference()["classes"][cls.upper()]["trainers"])
    if has_home:
        first = next(t for t in cg.reference()["classes"][cls.upper()]["trainers"] if t["map"] == home)
        assert ts[0]["npc"] == first["npc"]
    for t in ts:
        assert t["url"].startswith("https://www.wowhead.com/forever/npc=")
    tr = b["train"]
    if tr["now"]:
        assert tr["now"]["level"] <= level and tr["now"]["spells"]
    if tr["next"]:
        assert tr["next"]["level"] > level
    assert tr["now"] or tr["next"]
    for sp in (tr["now"] or {"spells": []})["spells"] + (tr["next"] or {"spells": []})["spells"]:
        assert sp["name"] and sp["url"].startswith("https://www.wowhead.com/forever/spell=")
    assert len(b["talents"]) <= 3 and len(b["habits"]) <= 2
    if level < 60:
        assert b["talents"] and all(p["level"] >= min(level, 10) for p in b["talents"])
    # Nothing about where the character is or what it did.
    assert set(b) == {"class", "level", "spec", "train", "trainers", "talents", "habits", "respec"}


def test_racial_spells_follow_the_race():
    def names(race):
        return {s["name"] for s in cg.taught("PRIEST", 40, race, "Alliance")}

    human, gnome = names("Human"), names("Gnome")
    assert "Feedback" in human and "Feedback" not in gnome


def test_a_talents_higher_ranks_need_the_talent():
    with_it = {s["name"] for s in cg.taught("PRIEST", 28, "Human", "Alliance", have={"Mind Flay"})}
    without = {s["name"] for s in cg.taught("PRIEST", 28, "Human", "Alliance", have=set())}
    assert "Mind Flay" in with_it and "Mind Flay" not in without


def test_known_talents_say_what_to_spend_now():
    picks = cg.picks("PRIEST")
    ranks: dict[str, int] = {}
    for p in picks[:2]:
        ranks[p["talent"]] = ranks.get(p["talent"], 0) + 1
    # Level 14 with the path's first 2 points spent: 3 points to spend now.
    nxt = cg.next_talents("PRIEST", 14, ranks, n=5)
    assert [p["now"] for p in nxt] == [True, True, True, False, False]
    assert nxt[0]["level"] == picks[2]["level"]


def test_ranks_from_the_page_talents():
    tal = {"available": True, "sections": [{"nodes": [{"name": "Spirit Tap", "rank": 3}, {"name": "X", "rank": 0}]}]}
    assert cg.ranks_of(tal) == {"Spirit Tap": 3}
    assert cg.ranks_of({"available": False}) is None


@pytest.mark.parametrize("cls", cg.CLASSES)
@pytest.mark.parametrize("level", [8, 27, 52])
def test_mcp_guide_for_every_class_at_three_levels(cls, level):
    g = cg.guide(cls.lower(), level, None, None)
    assert g["class"] == cls.title() and g["level"] == level and g["spec"]
    lo, hi = g["now"]["levels"]
    assert lo <= level <= hi
    if hi < 60:
        assert g["next"] and g["next"]["levels"][0] == hi + 1
    else:
        assert g["next"] is None
    cited = [s for t in cg.TOPICS if t not in ("talents", "spells") for s in g["now"][t]] + g["stats"]
    assert cited and all(s["source_url"].startswith("https://") and s["confidence"] and s["scope"] for s in cited)
    assert g["now"]["talent_picks"] or level < 10
    assert g["train"]["now"] or g["train"]["next"]
    assert g["talent_path_source"]["source_url"]
    json.dumps(g)


def test_unknown_class_is_refused():
    with pytest.raises(ValueError, match="unknown class"):
        cg.guide("death knight", 20)
    assert cg.block({"class": "Monk", "level": 20}) is None
