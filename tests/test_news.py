"""This week on Forever (wowstats.news) and what is coming: the weekly pass's
digest from a synthetic week's feed, the relevance order for different
characters, the events (dated, undated, past) and the prep gaps they bring;
plus the tank and healer role builds at 60 (wowstats.classmeta).

Synthetic weeks here (so the days are known); the shipped digest is checked
in test_the_shipped_digest_*."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from wowstats import classguide as cg
from wowstats import classmeta as cm
from wowstats import news
from wowstats.analysis import kbschema

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "kb"))
import meta  # noqa: E402

ALLOW = kbschema.load_allow(ROOT / "data" / "kb" / "sources.json")
TODAY = date(2026, 11, 20)
SRC = {"source_url": "https://worldofwarcraft.blizzard.com/en-us/news/1", "source_type": "official",
       "confidence": "high", "scope": "forever", "last_verified": "2026-11-20"}


def ev_item(n, name, starts=None, window=None, levels=(60, 60), prep=(), date_="2026-11-18", **kw):
    return {**SRC, "id": f"d:{n}", "category": "opening", "title": name, "text": f"{name} is coming.",
            "date": date_, "levels": list(levels),
            "event": {"key": f"k{n}", "name": name, "starts": starts, "window": window, "levels": list(levels),
                      "changes": f"{name} changes things.", "prep": list(prep)}, **kw}


ONYXIA_PREP = [{"kind": "attunement", "what": "the attunement", "faction": "Alliance", "quest_id": 4182,
                "statements": ["dungeons:KB-04:3"]},
               {"kind": "attunement", "what": "the attunement", "faction": "Horde", "quest_id": 4903,
                "statements": ["dungeons:KB-04:4"]}]
WEEK = {"digest": {"week": "2026-11-19", "from": "2026-11-12", "items": [
    {**SRC, "id": "d:1", "category": "class", "title": "Shield Slam hits harder", "text": "Shield Slam +10%.",
     "date": "2026-11-17", "classes": ["WARRIOR"], "roles": ["tank"], "levels": [40, 60]},
    {**SRC, "id": "d:2", "category": "hotfix", "title": "Early bags", "text": "Six-slot bags in starting areas.",
     "date": "2026-11-18", "levels": [1, 25]},
    {**SRC, "id": "d:3", "category": "economy", "title": "AH deposits", "text": "Deposits by duration.",
     "date": "2026-11-16"},
    {**SRC, "id": "d:4", "category": "class", "title": "Frost Nova", "text": "Frost Nova breaks on Blizzard.",
     "date": "2026-11-19", "classes": ["MAGE"]},
    # 19 days from TODAY: near, dated.
    ev_item(5, "Raid opening", starts="2026-12-09", prep=ONYXIA_PREP),
    ev_item(6, "Hardcore", window="winter 2026", levels=(1, 60)),
    ev_item(7, "Old event", starts="2026-11-15"),
    ev_item(8, "Far raid", starts="2027-03-01"),
]}}
OLDER = {"digest": {"week": "2026-11-12", "from": "2026-11-05", "items": [
    {**SRC, "id": "o:1", "category": "patch", "title": "Launch patch", "text": "Launch fixes.", "date": "2026-11-08"},
    # The same event, an older mention with another day: the newer week wins.
    ev_item(5, "Raid opening", starts="2026-12-16", date_="2026-11-08")]}}
BUILDS = [{"class": "WARRIOR", "spec": "Protection", "role": "tank", "level": 60, "split": "0/5/46",
           "ranks": {}, **SRC},
          {"class": "PRIEST", "spec": "Holy", "role": "healer", "level": 60, "split": "x", "ranks": {}, **SRC}]


def full_build(cls, tree):
    """A legal 51-point build inside one tree and its neighbours, from
    Forever's own talents (row by row, each to its max)."""
    tal = cg.reference()["classes"][cls]["talents"]
    out, n = {}, 0
    for name, t in sorted(tal.items(), key=lambda x: (x[1]["tree"] != tree, x[1]["tree"], x[1]["row"], x[1]["col"])):
        if n >= 51:
            break
        need = t.get("needs")
        spent = sum(r for k, r in out.items() if tal[k]["tree"] == t["tree"])
        if (need and out.get(need, 0) < tal[need]["max"]) or spent < 5 * t["row"]:
            continue
        r = min(t["max"], 51 - n)
        out[name] = r
        n += r
    assert n == 51
    return out


@pytest.fixture
def weeks(monkeypatch):
    builds = [{**BUILDS[0], "ranks": full_build("WARRIOR", "Protection")},
              {**BUILDS[1], "ranks": full_build("PRIEST", "Holy")}]
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": {"statements": [], "builds": builds},
                                              "weekly": [OLDER, WEEK]})
    return builds


TANK60 = {"class": "Warrior", "level": 60, "spec": "Protection", "faction": "Alliance"}
LOW20 = {"class": "Warrior", "level": 20, "spec": "Arms", "faction": "Alliance"}


# ---------------------------------------------------------------- the digest from a week's feed


def item(n, text, **kw):
    return {"source": "wowhead-news", "id": n, "url": f"https://www.wowhead.com/news={n}/x", "title": f"News {n}",
            "date": "2026-11-18", "text": text, "source_type": "press", "max_confidence": "medium", **kw}


class Client:
    def __init__(self, answer):
        self.answer, self.requests = answer, []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.requests.append(kw)
        return SimpleNamespace(stop_reason="end_turn", usage={"input_tokens": 1000, "output_tokens": 100},
                               content=[SimpleNamespace(type="text", text=json.dumps(self.answer))])


def entry(n, cat, title, text, event=None, **kw):
    base = {"item": n, "category": cat, "title": title, "text": text, "classes": [], "level_min": 0,
            "level_max": 0, "roles": [],
            "event": event or {"name": "", "key": "", "starts": "", "window": "", "changes": "", "level_min": 0,
                               "level_max": 0, "prep": []}}
    return base | kw


def test_a_weeks_feed_makes_a_dated_cited_digest(monkeypatch):
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": {}, "weekly": []})
    filler = " The rest of the article talks about other things players asked this week." * 3
    feed = [item(201, "Blizzard announced that Onyxia's Lair opens on December 9 for every region." + filler),
            item(202, "Warrior Shield Slam now deals 10% more damage after Tuesday's hotfix." + filler),
            item(203, "The Hardcore ruleset is planned for the winter season." + filler)]
    raid = {"name": "Onyxia's Lair opens", "key": "", "starts": "2026-12-09", "window": "", "changes": "A 40-player raid.",
            "level_min": 60, "level_max": 60,
            "prep": [{"kind": "attunement", "what": "the attunement", "statement": "dungeons:KB-04:3",
                      "faction": "Alliance", "quest_id": 4182},
                     {"kind": "gear", "what": "made up", "statement": "nope:1", "faction": "", "quest_id": 0}]}
    hc = {"name": "Hardcore ruleset", "key": "", "starts": "2026-12-21", "window": "winter 2026", "changes": "",
          "level_min": 0, "level_max": 0, "prep": []}
    answer = {"statements": [], "digest": [
        entry(1, "opening", "Onyxia opens", "Onyxia's Lair opens on 9 Dec.", raid, level_min=60, level_max=60),
        entry(2, "class", "Shield Slam up", "Shield Slam deals 10% more damage.", classes=["WARRIOR"],
              roles=["tank"]),
        # The item gives a season, not a day: the guessed day is dropped.
        entry(3, "announcement", "Hardcore this winter", "Hardcore comes this winter.", hc),
        entry(9, "patch", "Nowhere", "From no item.")]}
    client = Client(answer)
    out = meta.run(feed, client, cap=3.0, day="2026-11-23")
    doc = out["doc"]
    d = doc["digest"]
    assert d["week"] == "2026-11-23" and d["from"] == "2026-11-18" and len(d["items"]) == 3
    assert kbschema.check_file(doc, ALLOW) == []
    by = {i["title"]: i for i in d["items"]}
    ony = by["Onyxia opens"]
    assert (ony["source_url"], ony["date"], ony["confidence"]) == ("https://www.wowhead.com/news=201/x",
                                                                  "2026-11-18", "medium")
    assert ony["event"]["starts"] == "2026-12-09" and ony["event"]["key"] == "onyxia-s-lair-opens"
    assert [p["statements"] for p in ony["event"]["prep"]] == [["dungeons:KB-04:3"]]  # the made-up step is refused
    assert ony["event"]["prep"][0]["quest_id"] == 4182  # Dragonkin Menace: named by the statement
    assert by["Hardcore this winter"]["event"]["starts"] is None
    assert by["Hardcore this winter"]["event"]["window"] == "winter 2026"
    assert by["Shield Slam up"]["classes"] == ["WARRIOR"] and by["Shield Slam up"]["roles"] == ["tank"]
    assert out["refused"] == 3  # item 9, the made-up prep, the guessed day
    assert "<upcoming_events>" in client.requests[0]["messages"][0]["content"]
    # Read back as a week: the events derive from it, the undated one "announced, no date".
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": {}, "weekly": [doc]})
    evs = {e["name"]: e for e in news.events(TODAY)}
    assert evs["Onyxia's Lair opens"]["when"] == "9 Dec" and evs["Onyxia's Lair opens"]["in_days"] == 19
    assert evs["Hardcore ruleset"]["starts"] is None
    assert evs["Hardcore ruleset"]["when"] == "announced for winter 2026, no date"


@pytest.mark.parametrize("day, text, ok", [
    ("2026-12-09", "Raids open on December 9th.", True),
    ("2026-12-09", "Raids open 9 Dec in Europe.", True),
    ("2026-12-09", "Raids open on 2026-12-09.", True),
    ("2026-12-09", "Raids open in December.", False),
    ("2026-12-09", "Raids open 12/9.", False),
    ("2026-12-09", "Raids open December 19.", False),
])
def test_a_day_counts_only_when_the_item_names_it(day, text, ok):
    assert meta.date_in_text(day, text) is ok


# ---------------------------------------------------------------- relevance


def test_a_level_60_tank_and_a_level_20_character_see_different_news_first(weeks):
    tank = news.digest(chars=[TANK60], today=TODAY)
    low = news.digest(chars=[LOW20], today=TODAY)
    assert tank["week"] == "2026-11-19" and [w["week"] for w in tank["weeks"]] == ["2026-11-19", "2026-11-12"]
    t = [i["title"] for i in tank["items"] if i["for_you"]]
    lw = [i["title"] for i in low["items"] if i["for_you"]]
    assert t[0] == "Shield Slam hits harder" and "Raid opening" in t and "Early bags" not in t
    # The low character: its level's news (and Hardcore, open to every level) first.
    assert lw[:2] == ["Hardcore", "Early bags"] and "Shield Slam hits harder" not in lw and "Raid opening" not in lw
    # Nothing is hidden: what doesn't concern you comes after, newest first.
    assert {i["title"] for i in tank["items"]} == {i["title"] for i in low["items"]}
    assert [i["for_you"] for i in tank["items"]] == sorted((i["for_you"] for i in tank["items"]), reverse=True)
    # The mage's change concerns neither.
    assert not next(i for i in tank["items"] if i["title"] == "Frost Nova")["for_you"]


def test_a_filter_keeps_what_concerns_it_and_what_concerns_everyone(weeks):
    got = {i["title"] for i in news.digest(cls="mage", level=30, today=TODAY)["items"]}
    assert "Frost Nova" in got and "AH deposits" in got and "Shield Slam hits harder" not in got
    since = news.digest(since="2026-11-01", today=TODAY)
    assert "Launch patch" in {i["title"] for i in since["items"]}
    assert news.digest(week="2026-11-12", today=TODAY)["items"][0]["title"] in ("Launch patch", "Raid opening")


# ---------------------------------------------------------------- events


def test_events_come_from_the_newest_mention_and_past_ones_drop_off(weeks):
    evs = news.events(TODAY)
    names = [e["name"] for e in evs]
    assert "Old event" not in names  # 15 Nov: past
    raid = evs[0]
    assert raid["name"] == "Raid opening" and raid["starts"] == "2026-12-09" and raid["in_days"] == 19
    # Dated first, soonest first; undated last.
    assert names == ["Raid opening", "Far raid", "Hardcore"]
    # A window that has ended drops an undated event; one past every window too.
    assert "Hardcore" not in [e["name"] for e in news.events(date(2027, 3, 21))]


RAID_FX = [{"classes": ["WARRIOR"], "text": "Battle Shout reaches the whole raid.",
            "statements": ["raids-endgame:KB-02:15"]}]


def test_a_sparse_later_mention_moves_the_day_but_never_erases_the_prep(monkeypatch):
    # 2026-11-23: a one-line re-mention ("First raid tier opens", no prep, no
    # effects) replaced the curated raid opening; every level 60 lost the
    # Onyxia attunement move.
    rich = ev_item(5, "Raid opening", starts="2026-12-16", prep=ONYXIA_PREP)
    rich["event"]["effects"] = RAID_FX
    sparse = ev_item(5, "First raid tier opens", starts="2026-12-09", date_="2026-11-22")
    weekly = [{"digest": {"week": "2026-11-19", "items": [rich]}},
              {"digest": {"week": "2026-11-23", "items": [sparse]}}]
    monkeypatch.setattr(cm, "lanes", lambda: {"meta": {}, "weekly": weekly})
    raid = next(e for e in news.events(TODAY) if e["key"] == "k5")
    assert raid["name"] == "Raid opening" and raid["changes"] == "Raid opening changes things."
    assert raid["prep"] == ONYXIA_PREP and raid["effects"] == RAID_FX
    assert raid["starts"] == "2026-12-09"  # the newer day still wins
    assert news.prep_gaps(TANK60, today=TODAY)[0]["title"].startswith("Before Raid opening (9 Dec)")
    # A newer mention that brings prep of its own restates the event; its empty effects erase nothing.
    renamed = ev_item(5, "Raids open", starts="2026-12-09", date_="2026-11-22", prep=ONYXIA_PREP[:1])
    weekly[1]["digest"]["items"] = [renamed]
    raid = next(e for e in news.events(TODAY) if e["key"] == "k5")
    assert raid["name"] == "Raids open" and raid["prep"] == ONYXIA_PREP[:1] and raid["effects"] == RAID_FX


def test_the_writer_keeps_a_sparse_re_mention_to_when(monkeypatch):
    known = [{"key": "k5", "name": "Raid opening", "prep": ONYXIA_PREP, "effects": []},
             {"key": "k6", "name": "Hardcore", "prep": [], "effects": []}]
    batch = [item(301, "The first raid tier opens on December 9, and crafted tier stats are out.")]
    ev = {"name": "First raid tier opens", "key": "k5", "starts": "2026-12-09", "window": "",
          "changes": "Three raids launch.", "level_min": 60, "level_max": 60, "prep": []}
    hc = {**ev, "name": "Hardcore rules", "key": "k6", "starts": "", "changes": "Permadeath."}
    kept, _ = meta.to_digest([entry(1, "announcement", "Tier stats", "Crafted tier stats are out.", ev),
                              entry(1, "announcement", "Hardcore", "Hardcore is coming.", hc)],
                             batch, ALLOW, "2026-11-23", {"dungeons:KB-04:3"}, known, {})
    assert kept[0]["event"] == {"key": "k5", "name": "Raid opening", "starts": "2026-12-09", "window": None,
                                "changes": None, "levels": [60, 60], "prep": []}
    # An event with nothing curated yet takes the newer wording as before.
    assert kept[1]["event"]["name"] == "Hardcore rules" and kept[1]["event"]["changes"] == "Permadeath."


def test_main_news_gives_a_level_60_the_onyxia_attunement_first():
    # The real data/kb/news.json, read through the plan layer (classmeta.adherence).
    char = {"char": "X", "header": {"class": "Warrior", "level": 60, "faction": "Alliance", "race": "Human"},
            "spec": {"name": "Protection"}, "talents": None}
    prep = cm.adherence(char, None, date(2026, 11, 23))["prep"]
    first = prep[0]
    assert first["title"].startswith("Before Barrow Deeps") and "Onyxia attunement" in first["title"]
    assert first["quest"]["name"] == "Dragonkin Menace" and first["quest"]["url"].endswith("quest=4182")
    raid = next(e for e in news.upcoming(date(2026, 11, 23), char={"class": "Warrior", "level": 60})
                if e["key"] == "raids-open-2026-12")
    assert raid["for_you"] == ["Battle Shout reaches the whole raid."]


def test_an_announced_event_with_no_date_says_so_and_brings_no_prep(weeks):
    hc = next(e for e in news.upcoming(TODAY, char=LOW20) if e["name"] == "Hardcore")
    assert hc["starts"] is None and hc["in_days"] is None and hc["when"] == "announced for winter 2026, no date"
    assert news.soon_line("Warrior", "Arms", 20, TODAY) is None
    # Without even a window: "announced, no date", and it drops UNDATED_DAYS after its last mention.
    assert news.window_end(None) is None


def test_an_event_three_weeks_away_preps_a_level_60_and_not_a_level_20(weeks):
    gaps = news.prep_gaps(TANK60, today=TODAY)
    assert len(gaps) == 1  # Alliance: the Alliance chain only
    g = gaps[0]
    assert g["kind"] == "prep" and g["advice"] is True and g["in_days"] == 19
    assert g["quest"]["name"] == "Dragonkin Menace" and g["quest"]["url"].endswith("quest=4182")
    assert g["where"] == "Dragonkin Menace in Burning Steppes" and g["action"].startswith("Pick up Dragonkin Menace")
    assert "Drakefire Amulet" in g["detail"] and g["blocker"] is None
    horde = news.prep_gaps({**TANK60, "faction": "Horde"}, today=TODAY)
    assert [x["quest"]["name"] for x in horde] == ["Warlord's Command"]
    assert news.prep_gaps(LOW20, today=TODAY) == []
    # A level-52 can start: the raid's level is what blocks it.
    mid = news.prep_gaps({**TANK60, "level": 52}, today=TODAY)
    assert mid and mid[0]["blocker"] == "level 60 (you are 52)"
    # Over NEAR_DAYS away: nothing yet.
    assert news.prep_gaps(TANK60, today=date(2026, 11, 1)) == []
    assert news.soon_line("Warrior", "Protection", 60, date(2026, 11, 28)).startswith("Coming 9 Dec: Raid opening")
    assert news.soon_line("Warrior", "Protection", 60, TODAY) is None  # 19 days: past SOON_DAYS


def test_prep_gaps_are_advice_outside_the_score(weeks):
    char = {"char": "X", "header": {"class": "Warrior", "level": 60, "faction": "Alliance", "race": "Human"},
            "spec": {"name": "Protection"}, "talents": None}
    a = cm.adherence(char, None, TODAY)
    assert a["prep"] and a["prep"][0]["kind"] == "prep"
    assert all(g.get("kind") != "prep" for g in a["gaps"])


# ---------------------------------------------------------------- role builds at 60


def tal_of(ranks):
    return {"available": True, "spec": None, "sections": [{"name": "x", "nodes": [{"name": n, "rank": r}
                                                                                  for n, r in ranks.items()]}]}


def char(cls, spec, level, ranks, faction="Alliance"):
    return {"char": "X", "header": {"class": cls, "level": level, "faction": faction, "race": "Human"},
            "spec": {"name": spec}, "talents": tal_of(ranks)}


def test_a_level_60_tank_and_healer_score_against_their_role_builds(weeks):
    prot, holy = weeks[0]["ranks"], weeks[1]["ranks"]
    a = cm.adherence(char("Warrior", "Protection", 60, prot), None, TODAY)
    t = next(p for p in a["parts"] if p["key"] == "talents")
    assert (t["target"], t["role"], t["score"], t["of"]) == ("role", "tank", 1.0, 51)
    # Half the build moved elsewhere: half the score, and a respec gap naming the build.
    half = dict(list(prot.items())[: len(prot) // 2])
    b = cm.adherence(char("Warrior", "Protection", 60, half), None, TODAY)
    tb = next(p for p in b["parts"] if p["key"] == "talents")
    assert tb["target"] == "role" and tb["score"] < 1 and tb["free"] > 0
    assert any("free talent point" in g["title"] for g in b["gaps"])
    h = cm.adherence(char("Priest", "Holy", 60, holy), None, TODAY)
    th = next(p for p in h["parts"] if p["key"] == "talents")
    assert (th["target"], th["role"], th["score"]) == ("role", "healer", 1.0)
    d = cm.describe("Priest", "Holy", 60, today=TODAY)
    assert d["build"]["target"]["role"] == "healer" and d["build"]["target"]["split"] == "x"


def test_a_level_45_tank_scores_against_the_levelling_build(weeks):
    want = cm._want("WARRIOR", 45)
    a = cm.adherence(char("Warrior", "Protection", 45, want), None, TODAY)
    t = next(p for p in a["parts"] if p["key"] == "talents")
    assert t["target"] == "levelling" and t["score"] == 1.0 and t["of"] == 36
    assert cm.describe("Warrior", "Protection", 45, today=TODAY)["build"]["target"] is None


def test_a_role_spec_without_a_sourced_build_has_no_target_yet(weeks):
    a = cm.adherence(char("Shaman", "Restoration", 60, {"Totemic Focus": 5}), None, TODAY)
    t = next(p for p in a["parts"] if p["key"] == "talents")
    assert t["target"] == "none" and t["score"] is None and t["known"] is False
    assert cm.describe("Shaman", "Restoration", 60, today=TODAY)["build"]["target"]["none"] is True
    # A damage spec at 60 keeps the levelling build.
    d = cm.adherence(char("Warrior", "Arms", 60, cm._want("WARRIOR", 60)), None, TODAY)
    assert next(p for p in d["parts"] if p["key"] == "talents")["target"] == "levelling"


# ---------------------------------------------------------------- the shipped data


def test_the_shipped_role_builds_play_on_forevers_trees():
    cm.reset()
    builds = cm.lanes()["meta"].get("builds") or []
    assert {(b["class"], b["spec"]) for b in builds} <= set(cm.ROLE_SPECS)
    for b in builds:
        tal = cg.reference()["classes"][b["class"]]["talents"]
        assert sum(b["ranks"].values()) == 51, b["spec"]
        for name, r in b["ranks"].items():
            t = tal[name]
            assert r <= t["max"]
            above = sum(x for n, x in b["ranks"].items() if tal[n]["tree"] == t["tree"] and tal[n]["row"] < t["row"])
            assert above >= 5 * t["row"], (b["spec"], name)
            if t.get("needs"):
                assert b["ranks"].get(t["needs"], 0) == tal[t["needs"]]["max"], (b["spec"], name)
        assert kbschema.check(b, ALLOW) == []


def test_the_shipped_digest_is_sourced_and_its_events_resolve():
    cm.reset()
    ws = news.weeks()
    assert ws and all(kbschema.check(i, ALLOW) == [] for w in ws for i in w["items"])
    for w in ws:
        for i in w["items"]:
            for p in (i.get("event") or {}).get("prep") or []:
                assert all(news.statement(s) for s in p.get("statements") or []), p
    assert any(e["starts"] for e in news.events(date(2026, 11, 20)))
