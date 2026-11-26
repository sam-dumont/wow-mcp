"""The knowledge base's files against their schema (wowstats.analysis.kbschema):
every rule, statement and contradiction sourced, scoped, dated and checked,
citing only allow-listed domains, never surer than its sources; the code's
own rules don't share a name with a research lane's (the lane's would win
with another shape)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wowstats.analysis import kb, kbschema

DATA = Path(__file__).resolve().parent.parent / "data" / "kb"
RESEARCH = sorted((DATA / "research").glob("*.json"))


@pytest.fixture(scope="module")
def allow():
    return kbschema.load_allow(DATA / "sources.json")


def test_allow_list_is_well_formed(allow):
    types = set(json.loads((DATA / "sources.json").read_text())["types"])
    assert allow and all(v["type"] in types and v["max_confidence"] in kbschema.CONFIDENCE for v in allow.values())
    assert all(d == d.lower() and "/" not in d for d in allow)


def test_rules_json_is_fully_sourced(allow):
    doc = json.loads((DATA / "rules.json").read_text())
    assert kbschema.check_file(doc, allow, strict_type=False) == []


@pytest.mark.parametrize("path", RESEARCH, ids=lambda p: p.stem)
def test_research_lane_is_fully_sourced(path, allow):
    doc = json.loads(path.read_text())
    assert doc["schema"] == 2 and doc["lane"] == path.stem and doc["last_verified"]
    assert doc["statements"], "a lane with no statement was imported wrong"
    assert kbschema.check_file(doc, allow) == []
    ids = [r["id"] for k in ("statements", "contradictions") for r in doc[k]]
    assert len(ids) == len(set(ids))


def test_the_six_lanes_are_there():
    assert {p.stem for p in RESEARCH} >= {"dungeons", "economy-professions", "game-rules", "levelling", "pvp",
                                          "raids-endgame"}


def test_code_rules_never_share_a_name_with_a_lane():
    mine = set(json.loads((DATA / "rules.json").read_text())["rules"])
    for p in RESEARCH:
        assert not mine & set(json.loads(p.read_text())["rules"]), p.stem


def test_the_loader_refuses_nothing_shipped():
    k = kb.build({"KB_GAME_TABLES": "off"})
    assert k.meta["rules_refused"] == []
    # A superseded or expired statement is left out by design until the weekly
    # job removes it (2026-11-20: a refresh's 6 `supersedes` failed this count
    # and the run died before its PR, #105).
    every = [s for p in RESEARCH for s in json.loads(p.read_text())["statements"]]
    gone = kbschema.retired_ids(every, datetime.now(UTC).date())
    assert k.kinds()["kb-statement"] == len([s for s in every if s["id"] not in gone])


def test_lists_and_builds_have_two_sources_or_say_single_source(allow):
    bad = []
    for p in RESEARCH:
        doc = json.loads(p.read_text())
        paths = doc.get("paths") or {}
        for key, recs in (("statements", doc["statements"]), ("builds", doc.get("builds") or []),
                          ("paths", list(paths.values()) if isinstance(paths, dict) else paths)):
            bad += [f"{p.stem}: {r.get('id') or r.get('spec')}: {why}" for r in recs
                    if kbschema.is_list_or_build(r, key) and (why := kbschema.multi_source(r, allow))]
    assert bad == []


@pytest.mark.parametrize("rec, why", [
    ({"sources": ["https://www.icy-veins.com/a", "https://www.method.gg/b"]}, ""),
    ({"single_source": True, "confidence": "low"}, ""),
    ({"single_source": True}, "confidence isn't low"),
    ({}, "one source"),
    ({"source_url": "https://worldofwarcraft.blizzard.com/en-us/news/1"}, ""),
])
def test_the_multi_source_rule(allow, rec, why):
    base = {"source_url": "https://www.icy-veins.com/a", "confidence": "medium", "aspect": "enchants"}
    got = kbschema.multi_source({**base, **rec}, allow)
    assert (why in got) if why else got == ""


@pytest.mark.parametrize("rec, why", [
    ({}, "no source_url"),
    ({"source_url": "http://news.blizzard.com/x"}, "not an https URL"),
    ({"source_url": "https://example.org/x"}, "not on the source allow-list"),
    ({"sources": ["https://example.org/y"]}, "not on the source allow-list"),
    ({"source_url": 'https://news.blizzard.com/x"'}, "invalid characters"),
    ({"sources": ["https://news.blizzard.com/a b"]}, "invalid characters"),
    ({"scope": "retail"}, "scope"),
    ({"confidence": "certain"}, "confidence"),
    ({"last_verified": "yesterday"}, "last_verified"),
    ({"source_type": "blog"}, "source_type"),
    ({"source_url": "https://conquestcapped.com/x", "confidence": "high"}, "above what its sources allow"),
])
def test_a_bad_record_is_refused(allow, rec, why):
    good = {"text": "x", "source_url": "https://news.blizzard.com/en-us/article/1", "source_type": "official",
            "date": "2026-11-10", "confidence": "high", "scope": "forever", "last_verified": "2026-11-18"}
    assert kbschema.check(good, allow) == []
    bad = {**good, **rec}
    if not rec:
        bad.pop("source_url")
    assert any(why in e for e in kbschema.check(bad, allow))


def test_encoded_search_parameters_are_valid(allow):
    rec = rec_(source_url="https://www.wowhead.com/search?q=two%20words&quote=%22", source_type="guide")
    assert kbschema.check(rec, allow) == []


def test_forum_authorship_matches_the_post_number(allow):
    player = "https://us.forums.blizzard.com/en/wow/t/wow-forever-exp-eliminator/2406791"
    mixed = "https://eu.forums.blizzard.com/en/wow/t/643840"
    assert kbschema.source_type(player, allow) == kbschema.source_type(mixed, allow) == "community"
    assert kbschema.source_type(mixed + "/233", allow) == "official"
    assert kbschema.source_type(mixed + "/234", allow) == "community"
    assert kbschema.best_cap({"source_url": player}, allow) == "medium"
    assert kbschema.multi_source({"source_url": player}, allow)
    assert kbschema.multi_source({"source_url": mixed + "/233"}, allow) == ""


def test_a_booster_backed_by_an_official_source_may_be_high(allow):
    rec = {"source_url": "https://conquestcapped.com/x", "sources": ["https://us.forums.blizzard.com/en/wow/t/2403040/14"],
           "source_type": "commercial", "date": "2026-11", "confidence": "high", "scope": "forever",
           "last_verified": "2026-11-18"}
    assert kbschema.check(rec, allow) == []



# JudgeHype (owner, 2026-11-20): it mostly relays others in French, so it
# only corroborates.
JH = "https://wowforever.judgehype.com/article/tier-list-dps-tank-et-soigneurs-raids-wow-forever/"
WH = "https://www.wowhead.com/forever/guide/classes/tier-lists/dps-pve-rankings"
KAMI = "https://kami-labs.fr/en/tier-list-wow-forever/"


def rec_(**kw) -> dict:
    return {"text": "x", "source_url": WH, "source_type": "guide", "date": "2026-11-19", "confidence": "low",
            "scope": "forever", "last_verified": "2026-11-20"} | kw


def test_judgehype_only_corroborates(allow):
    assert allow["wowforever.judgehype.com"]["corroboration_only"] is True
    # Next to another source: fine.
    assert kbschema.check(rec_(sources=[WH, JH]), allow) == []
    # Never the source_url, never alone.
    assert any("only corroborates" in e for e in kbschema.check(rec_(source_url=JH, source_type="press"), allow))
    # It never counts toward the two-source rule...
    assert kbschema.counted(rec_(sources=[WH, JH]), allow) == [WH]
    assert "one source" in kbschema.multi_source(rec_(sources=[WH, JH], aspect="stats", confidence="medium"),
                                                         allow)
    # ...nor toward the confidence cap: a booster plus JudgeHype stays at the booster's low.
    booster = rec_(source_url="https://conquestcapped.com/x", sources=[JH], source_type="commercial",
                   confidence="medium")
    assert any("above what its sources allow (low)" in e for e in kbschema.check(booster, allow))


def test_a_relay_and_what_it_relays_are_one_origin(allow):
    relays = {"wowforever.judgehype.com": "www.wowhead.com"}
    r = rec_(sources=[WH, JH], relays=relays, confidence="medium")
    assert kbschema.origins(r, allow) == {"www.wowhead.com"}
    assert any("one origin" in e for e in kbschema.check(r, allow))
    assert kbschema.check({**r, "single_source": True, "confidence": "low"}, allow) == []
    # Another site backing it makes two origins again.
    third = {**r, "sources": [WH, JH, KAMI]}
    assert len(kbschema.origins(third, allow)) == 2 and kbschema.check(third, allow) == []
    assert any("relays" in e for e in kbschema.check({**r, "relays": {"example.org": "www.wowhead.com"}}, allow))


def test_the_raid_tier_statements_rest_on_wowhead_alone():
    # The JudgeHype seeding pass (2026-11-20): one standing per class from
    # Wowhead's lists, JudgeHype's French copy cited beside them. No other
    # site's list was read for them: one origin, capped at low.
    allow = kbschema.load_allow(DATA / "sources.json")
    doc = json.loads((DATA / "research" / "class-meta.json").read_text())
    tiers = [s for s in doc["statements"] if s["id"].endswith(":raid-tier")]
    assert len(tiers) == 9 and len({s["class"] for s in tiers}) == 9
    for s in tiers:
        assert kbschema.domain(s["source_url"]) == "www.wowhead.com" and JH in s["sources"]
        assert kbschema.origins(s, allow) == {"www.wowhead.com"}
        assert (s["single_source"], s["confidence"], s["levels"], s["aspect"]) == (True, "low", [60, 60], "standing")


def test_a_class_guide_record_keeps_its_class_topic_and_levels(allow):
    """2026-11-22 (#99): the model pass superseded two shaman class-guide
    records with flat ones (no class, topic "shaman", no levels); the schema
    passed them and only tests/test_classguide.py caught it, after the PR was
    open. The classes lane's shape is now the schema's, so the job's check
    before the PR (tools/kb/vet.py) and its gates refuse it too."""
    rec = {"id": "classes:shaman:3", "doc": "CG-SHA", "class": "SHAMAN", "topic": "talents", "levels": [10, 19],
           "title": "10-19", "text": "Thundering Strikes first.",
           "source_url": "https://worldofwarcraft.blizzard.com/en-us/news/24315281", "source_type": "official",
           "date": "2026-11-10", "confidence": "high", "scope": "forever", "last_verified": "2026-11-22"}
    doc = {"lane": "classes", "statements": [rec], "contradictions": []}
    assert kbschema.check_file(doc, allow) == []
    flat = {k: v for k, v in rec.items() if k not in ("class", "doc", "levels", "title")} | {"topic": "shaman"}
    errs = kbschema.check_file({**doc, "statements": [flat]}, allow)
    assert any("class" in e for e in errs) and any("topic" in e for e in errs) and any("levels" in e for e in errs)
    # Another lane keeps its free topics.
    assert kbschema.check_file({**doc, "lane": "game-rules", "statements": [flat]}, allow) == []


# A statement's source_type comes from its source_url (owner, 2026-11-23):
# the model wrote it, and Wowhead's guide pages came out "datamined" like
# its item pages. sources.json types the domain, and `paths` a part of it.
@pytest.mark.parametrize("url,want", [
    ("https://www.wowhead.com/forever/guide/classes/warrior/fury/overview-pve-dps", "guide"),
    ("https://www.wowhead.com/forever/profession-leveling-guides/first-aid", "guide"),
    ("https://www.wowhead.com/classic/guide/classic-auction-house-economy", "guide"),
    ("https://www.wowhead.com/forever/item=249427", "datamined"),
    ("https://www.wowhead.com/forever/spell=8690/hearthstone", "datamined"),
    ("https://www.wowhead.com/forever/quest=93680", "datamined"),
    ("https://www.wowhead.com/forever/zones/battlegrounds", "datamined"),
    ("https://www.wowhead.com/news=383858/enhancement-shaman-class-changes-now-live", "press"),
    ("https://www.wowhead.com/forever/news/auction-house-minimum-deposit-reduced-to-1-copper-383917", "press"),
    ("https://www.wowhead.com/forever/blue-tracker/news/eu/24303313", "press"),
    ("https://kami-labs.fr/en/tier-list-wow-forever/", "guide"),
    ("https://foreverdb.net/dungeon/2959-city-of-dalaran", "datamined"),
    ("https://foreverdb.net/class/Paladin/talents", "datamined"),
    ("https://foreverdb.net/class/Mage/pve", "guide"),
    ("https://foreverdb.net/guide/leveling-hunter", "guide"),
    ("https://foreverdb.net/professions/best", "guide"),
    ("https://news.blizzard.com/en-us/article/1", "official"),
    ("https://example.org/x", None),
])
def test_source_type_comes_from_the_url(allow, url, want):
    assert kbschema.source_type(url, allow) == want


def test_a_source_type_its_url_doesnt_give_is_refused(allow):
    assert kbschema.check(rec_(), allow) == []
    errs = kbschema.check(rec_(source_type="datamined"), allow)
    assert any("source_type 'datamined'" in e and "guide" in e for e in errs)


def test_every_path_rule_is_well_formed(allow):
    import re

    types = set(json.loads((DATA / "sources.json").read_text())["types"])
    for d, v in allow.items():
        for p in v.get("paths") or []:
            assert p["type"] in types and re.compile(p["match"]), d
