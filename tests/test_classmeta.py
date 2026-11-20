"""The state of the meta (wowstats.classmeta): the paragraph, the full
description and the adherence score, for any class, spec and level, from the
knowledge base's words and the character's own data only.

Synthetic lanes here (so the numbers are known); the shipped lane is checked
in test_the_shipped_lane_*."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from wowstats import classguide as cg
from wowstats import classmeta as cm
from wowstats import redact
from wowstats.analysis import kbschema

DATA = Path(__file__).resolve().parent.parent / "data"
ALLOW = kbschema.load_allow(DATA / "kb" / "sources.json")
TODAY = date(2026, 11, 20)
SRC = {"source_url": "https://www.method.gg/wow-forever/x", "source_type": "guide", "confidence": "medium",
       "scope": "forever", "last_verified": "2026-11-20"}


def st(cls, spec, aspect, text, date_="2026-11-14", levels=(1, 60), **kw):
    return {"id": f"t:{cls}:{spec}:{aspect}:{len(text)}", "class": cls, "spec": spec, "aspect": aspect,
            "levels": list(levels), "text": text, "date": date_, **SRC, **kw}


LANE = {"statements": [
    st("MAGE", "Frost", "summary", "Frost is the safe way to level. Take the Frost build. Spell power first."),
    st("MAGE", "Fire", "summary", "Fire shines in dungeons once crit gear arrives. Spell power and crit."),
    st("MAGE", "Frost", "standing", "Most mages level Frost."),
    st("MAGE", "Fire", "standing", "Fire is a dungeon pick."),
    st("MAGE", "", "stats", "Spell power, then Intellect."),
    st("MAGE", "", "enchants", "Cheap boots enchant.", slot="Feet", enchant="Minor Speed", spell_id=13890,
       levels=(20, 60)),
    st("MAGE", "", "enchants", "Intellect on the chest.", slot="Chest", enchant="Minor Mana", levels=(20, 60)),
    st("MAGE", "", "consumables", "Conjured water.", item_id=8079),
    st("MAGE", "", "shift", "Frost Nova breaks on Blizzard damage now.", date_="2026-11-17"),
    st("WARRIOR", "Arms", "summary", "Arms with a slow two-hander. Strength first. Mortal Strike at 40."),
    st("WARRIOR", "", "enchants", "Agility on the gloves.", slot="Hands", enchant="Agility", levels=(30, 60)),
]}
WEEKLY = [{"statements": [{**SRC, "id": "w:1", "class": "Mage", "topic": "Mage mechanics", "date": "2026-11-19",
                           "text": "Ice Lance now shatters. More words here."},
                          {**SRC, "id": "w:2", "class": "Shaman", "topic": "balance", "date": "2026-11-12",
                           "text": "Stormstrike changed."}]}]


@pytest.fixture
def lane(monkeypatch):
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": LANE, "weekly": WEEKLY})


def tal_of(cls, ranks: dict[str, int]) -> dict:
    """A talents answer as wowstats.talents gives it: one section, the ranks."""
    return {"available": True, "spec": None,
            "sections": [{"name": "x", "nodes": [{"name": n, "rank": r} for n, r in ranks.items()]}]}


def build_ranks(cls, level):
    return cm._want(cls, level)


def slot(n, name, cur, gain=None, item=1000, **best):
    out = {"slot": n, "slot_name": name, "current": {"item_id": item, "name": f"Old {name}", "score": cur},
           "best": None}
    if gain:
        out["best"] = {"item_id": item + 1, "name": f"New {name}", "slot": n, "slot_name": name, "gain": gain,
                       "gain_share_pct": 12, "score": cur + gain,
                       "sources": [{"kind": "drop", "boss": "A Boss", "zone": "Shadowfang Keep", "levels": [22, 30]}],
                       **best}
    return out


# ---------------------------------------------------------------- the paragraph


def test_paragraph_is_the_specs_summary_plus_the_newest_change(lane):
    p = cm.paragraph("Mage", "Frost", 30, TODAY)
    assert p["class"] == "Mage" and p["spec"] == "Frost"
    assert p["text"].startswith("Frost is the safe way to level.")
    # The weekly pass's Nov 19 change is newer than the summary: one sentence of it.
    assert p["text"].endswith("Since Nov 19: Ice Lance now shatters.")
    assert p["as_of"] == "2026-11-20" and p["stale"] is False


def test_paragraph_follows_the_characters_spec_and_falls_back_to_the_levelling_one(lane):
    assert cm.paragraph("MAGE", "fire", 40, TODAY)["spec"] == "Fire"
    # Arcane has nothing in the lane: the levelling build's spec (Frost) is told.
    assert cm.paragraph("Mage", "Arcane", 40, TODAY)["spec"] == cg.path("MAGE")["spec"]
    assert cm.paragraph("Mage", None, 12, TODAY)["spec"] == cg.path("MAGE")["spec"]


def test_no_summary_no_paragraph(lane):
    assert cm.paragraph("Druid", "Balance", 30, TODAY) is None
    assert cm.paragraph("Necromancer", None, 30, TODAY) is None


def test_old_knowledge_says_so(monkeypatch):
    old = {"statements": [{**st("ROGUE", "Combat", "summary", "Combat swords."), "date": "2026-10-01",
                           "last_verified": "2026-10-02"}]}
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": old, "weekly": []})
    p = cm.paragraph("Rogue", "Combat", 30, TODAY)
    assert p["as_of"] == "2026-10-02" and p["stale"] is True


def test_the_paragraph_reaches_zogzog_but_not_the_public_page(lane):
    p = cm.paragraph("Mage", "Frost", 30, TODAY)
    d = {"char": "X", "header": {"class": "Mage"}, "meta": {**p, "secret": 1}}
    z = redact.zogzog("char", d)
    assert z["meta"] == p  # the paragraph as it is, nothing added to it
    assert "meta" not in redact.public("char", d)


# ---------------------------------------------------------------- the description


def test_description_has_every_part_for_the_spec(lane):
    d = cm.describe("Mage", "Frost", 30, today=TODAY)
    assert d["spec"] == "Frost" and d["summary"].startswith("Frost")
    assert [x["spec"] for x in d["standing"]] == ["Frost", "Fire"]  # the character's spec first
    assert d["build"]["spec"] == cg.path("MAGE")["spec"] and d["build"]["notes"]
    assert d["enchants"][0]["slot"] in ("Feet", "Chest")
    feet = next(e for e in d["enchants"] if e["slot"] == "Feet")
    assert feet["url"].endswith("spell=13890") and feet["enchant"] == "Minor Speed"
    assert d["consumables"][0]["url"].endswith("item=8079")
    assert [s["date"] for s in d["shifts"]] == ["2026-11-19", "2026-11-17"]
    # No sources on the page's answer; the MCP's has them.
    assert "source_url" not in d["stats"][0]
    assert cm.describe("Mage", "Frost", 30, cite=True, today=TODAY)["stats"][0]["source_url"].startswith("https://")


def test_level_filters_what_applies(lane):
    assert cm.describe("Mage", "Frost", 12, today=TODAY)["enchants"] == []


# ---------------------------------------------------------------- adherence


def char_of(cls, level, ranks=None, items=None, spec=None, race="Human", faction="Alliance"):
    return {"char": "Test-Realm",
            "header": {"class": cls, "level": level, "race": race, "faction": faction},
            "spec": {"name": spec}, "talents": tal_of(cls, ranks) if ranks is not None else {"available": False},
            "gear": {"items": items or [], "professions": []}}


GEAR = {"available": True, "spec": {"label": "Frost"},
        "slots": [slot(1, "Head", 30, 10), slot(5, "Chest", 40, None), slot(8, "Feet", 20, 30, item=2000),
                  slot(16, "Main hand", 50, 5)]}


def test_on_the_build_with_best_gear_and_enchants_is_100(lane):
    full = {"available": True, "spec": {"label": "Frost"}, "slots": [slot(1, "Head", 30), slot(5, "Chest", 40)]}
    items = [{"slot": 8, "item_id": 5, "enchant_id": 911}, {"slot": 5, "item_id": 6, "enchant_id": 912}]
    a = cm.adherence(char_of("Mage", 40, build_ranks("MAGE", 40), items), full)
    assert a["score"] == 100 and a["label"] == "On the meta" and a["gaps"] == []


def test_the_score_is_the_weighted_parts(lane):
    want = build_ranks("MAGE", 30)
    # Half the build's points kept; the other half sit outside it.
    keep = dict(list(want.items())[: len(want) // 2])
    total, kept = sum(want.values()), sum(keep.values())
    ranks = {**keep, "Some Arcane Talent": total - kept}
    items = [{"slot": 8, "item_id": 5, "enchant_id": 0, "name": "Boots"},
             {"slot": 5, "item_id": 6, "enchant_id": 912, "name": "Robe"}]
    a = cm.adherence(char_of("Mage", 30, ranks, items), GEAR)
    parts = {p["key"]: p for p in a["parts"]}
    assert parts["talents"]["score"] == round(kept / total, 3) and parts["talents"]["off_build"] == total - kept
    assert parts["gear"]["score"] == round(140 / 185, 3)
    assert parts["enchants"]["score"] == 0.5 and parts["enchants"]["of"] == 2
    expect = 0.4 * kept / total + 0.4 * 140 / 185 + 0.2 * 0.5
    assert a["score"] == round(expect * 100)
    kinds = [g["kind"] for g in a["gaps"]]
    assert {"talents", "upgrade", "enchant"} <= set(kinds)
    # Every gap is actionable: a title, where to go or what to do.
    for g in a["gaps"]:
        assert g["title"] and g["link"].startswith("#")
        assert g.get("action") or g.get("source") or g.get("trainers"), g
    up = next(g for g in a["gaps"] if g["kind"] == "upgrade")
    assert up["item"]["name"] == "New Feet" and up["source"]["boss"] == "A Boss"  # the biggest gain first
    respec = next(g for g in a["gaps"] if g["kind"] == "talents")
    assert respec["trainers"] and all(t["url"].startswith("https://") for t in respec["trainers"])
    assert "trainer" in respec["action"]
    ench = next(g for g in a["gaps"] if g["kind"] == "enchant")
    assert ench["enchants"][0]["name"] == "Minor Speed" and ench["items"][0]["slot_name"] == "Feet"


def test_free_points_are_a_gap_with_the_next_talents(lane):
    want = build_ranks("WARRIOR", 25)
    first = cg.picks("WARRIOR")[0]["talent"]
    ranks = {first: 1}
    a = cm.adherence(char_of("Warrior", 25, ranks), None)
    tp = next(p for p in a["parts"] if p["key"] == "talents")
    assert tp["free"] == 16 - 1 and tp["score"] == round(1 / sum(want.values()), 3)
    g = next(g for g in a["gaps"] if g["kind"] == "talents")
    assert "free talent point" in g["title"] and g["talents"][0]["name"]


def test_what_isnt_known_is_left_out_not_zero(lane):
    # No talents export, no gear sheet, an older shipper without enchant ids.
    a = cm.adherence(char_of("Mage", 30, None, [{"slot": 8, "item_id": 5}]), None)
    assert a["available"] is False
    assert a["gaps"][0]["title"] == "Your talents aren't on your page yet"
    b = cm.adherence(char_of("Mage", 30, None), GEAR)
    assert b["available"] and b["score"] == round(140 / 185 * 100)
    assert next(p for p in b["parts"] if p["key"] == "talents")["score"] is None


def test_below_level_10_there_are_no_talents_to_measure(lane):
    a = cm.adherence(char_of("Warlock", 6, {}), GEAR)
    assert [p["key"] for p in a["parts"]] == ["gear"]


@pytest.mark.parametrize("cls,level,race,faction", [
    ("Priest", 32, "Gnome", "Alliance"), ("Hunter", 14, "Dwarf", "Alliance"), ("Warrior", 20, "Orc", "Horde"),
    ("Paladin", 41, "Human", "Alliance"), ("Rogue", 60, "Night Elf", "Alliance"), ("Shaman", 47, "Tauren", "Horde"),
    ("Mage", 58, "Troll", "Horde"), ("Warlock", 9, "Undead", "Horde"), ("Druid", 60, "Skyborne", "Horde")])
def test_any_class_and_level_on_the_build_scores_full_talents(lane, cls, level, race, faction):
    t = cg.token(cls)
    a = cm.adherence(char_of(cls, level, build_ranks(t, level), race=race, faction=faction), GEAR)
    tp = next((p for p in a["parts"] if p["key"] == "talents"), None)
    if level < 10:
        assert tp is None
    else:
        assert tp["score"] == 1.0 and tp["free"] == 0 and tp["off_build"] == 0
    # Trainers named in a gap are of the character's side.
    for g in a["gaps"]:
        for tr in g.get("trainers") or []:
            assert tr["zone"]["name"]


def test_answer_without_a_class_in_the_kb(monkeypatch):
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": {}, "weekly": []})
    monkeypatch.setattr(cg, "path", lambda c: {})
    a = cm.answer(char_of("Mage", 30, {}), None)
    assert a["available"] is False and a["adherence"] is None


# ---------------------------------------------------------------- the shipped lane

LANE_FILE = DATA / "kb" / "research" / "class-meta.json"


def shipped() -> dict:
    return json.loads(LANE_FILE.read_text(encoding="utf-8"))


def test_the_shipped_lane_is_sourced():
    d = shipped()
    assert kbschema.check_file(d, ALLOW) == []


@pytest.mark.parametrize("cls", cg.CLASSES)
def test_the_shipped_lane_covers_every_class(cls):
    rows = [s for s in shipped()["statements"] if cg.token(s.get("class")) == cls]
    assert any(s["aspect"] == "summary" for s in rows), f"{cls}: no summary"
    trees = set(cm.trees(cls))
    for s in rows:
        assert s["aspect"] in cm.ASPECTS, s["id"]
        assert s.get("spec") in ("", None) or s["spec"] in trees, s["id"]
        if s["aspect"] == "enchants":
            assert s.get("slot") in cm.ENCHANT_SLOTS.values(), s["id"]
    assert cm.paragraph(cls, None, 30, TODAY)["text"]


def test_a_newer_reviewed_weekly_summary_takes_over(monkeypatch):
    newer = {**SRC, "id": "w:9", "class": "Mage", "aspect": "summary", "spec": "Frost", "date": "2026-11-26",
             "last_verified": "2026-11-27", "text": "Frost is still first. Ice Lance carries it."}
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": LANE, "weekly": [*WEEKLY, {"statements": [newer]}]})
    p = cm.paragraph("Mage", "Frost", 30, date(2026, 11, 28))
    assert p["text"] == "Frost is still first. Ice Lance carries it." and p["as_of"] == "2026-11-27"
    # An aspect statement is the meta's own part, not a "what changed" line.
    assert all(s["id"] != "w:9" for s in cm.shifts("MAGE", "Frost"))


def test_one_patch_note_from_two_sources_is_one_shift(monkeypatch):
    a = {**SRC, "id": "w:a", "class": "Priest", "date": "2026-11-16",
         "text": "Vampiric Embrace now heals the party for 10% of shadow damage (was 20%)."}
    b = {**SRC, "id": "w:b", "class": "Priest", "date": "2026-11-17",
         "text": "Vampiric Embrace now heals the party for 10% of the priest's Shadow damage (was 20%), "
                 "and the priest gets double."}
    c = {**SRC, "id": "w:c", "class": "Priest", "date": "2026-11-14", "text": "Holy Fire costs less mana."}
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": {}, "weekly": [{"statements": [a, b, c]}]})
    assert [s["id"] for s in cm.shifts("PRIEST")] == ["w:b", "w:c"]
