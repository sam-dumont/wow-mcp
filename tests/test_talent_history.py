"""The talent history (owner, 2026-11-20): the last 3 weeks of Battle.net's
daily talent reads, straight from vm-wow (23 days there), never copied into
a log store, a file, a bundle or a cache. What changed each day: the points
per tree before and after, the talents gained or dropped.

vm-wow is faked at the query_range level, shaped as VictoriaMetrics answers
(the exporter's series: wow_character_talent_points per tree,
wow_character_talent_rank per talent spell)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

from wowstats import redact, talents, views, wowsims
from wowstats.stores import Store

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
DAY, HOUR = 86400, 3600
T0 = int(NOW.timestamp()) - 20 * DAY  # three weeks back, give or take


def pts(tree, order, values):
    return {"metric": {"__name__": "wow_character_talent_points", "tree": tree, "order": str(order)},
            "values": [[t, str(v)] for t, v in values]}


def rank(spell, name, values):
    return {"metric": {"__name__": "wow_character_talent_rank", "spell_id": str(spell), "talent": name},
            "values": [[t, str(v)] for t, v in values]}


class FakeVM(Store):
    def __init__(self, series):
        super().__init__("http://vl.invalid", vm_url="http://vm.invalid", account_id=3)
        self.series, self.queries = series, []

    def profile_range(self, q, start, end, step):
        self.queries.append((q, step, end - start))
        name = q.split("(", 1)[1].split("{", 1)[0]
        return [s for s in self.series if s["metric"]["__name__"] == name]


# A priest levelling: day 0 Shadow 5 (Spirit Tap 5), day 1 the same, day 3
# a level (Improved SW:P 1), day 5 a respec into Discipline. Day 5's first
# sample lands while the read does: the new trees with the old talents too.
d0, d1, d3, d5 = T0, T0 + DAY, T0 + 3 * DAY, T0 + 5 * DAY
SERIES = [
    pts("Discipline", 0, [(d0, 0), (d1, 0), (d3, 0), (d5, 6), (d5 + HOUR, 6)]),
    pts("Shadow Magic", 1, [(d0, 5), (d1, 5), (d3, 6), (d5, 0), (d5 + HOUR, 0)]),
    pts("Holy", 2, [(d0, 0), (d1, 0), (d3, 0), (d5, 0), (d5 + HOUR, 0)]),
    rank(15270, "Spirit Tap", [(d0, 5), (d1, 5), (d3, 5), (d5, 5)]),  # gone with the respec
    rank(15275, "Improved Shadow Word: Pain", [(d3, 1), (d5, 1)]),
    # Classic ranks are spells of their own: rank 2 of Wand Specialization
    # is another series, the same talent.
    rank(14524, "Wand Specialization", [(d5, 1)]),
    rank(14525, "Wand Specialization", [(d5 + HOUR, 2)]),
    rank(14522, "Unbreakable Will", [(d5 + HOUR, 4)]),
]


def test_readings_one_whole_build_a_day_for_the_callers_account():
    vm = FakeVM(SERIES)
    got = vm.talent_readings("Testpriest-Fixture", now=NOW)
    assert [r["t"][:10] for r in got] == sorted({datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d")
                                                 for t in (d0, d1, d3, d5)})
    day5 = got[-1]
    # The mixed sample (new trees, old talents) is passed over for the day's whole one.
    assert {k: v["rank"] for k, v in day5["ranks"].items()} == {"wand specialization": 2, "unbreakable will": 4}
    assert [t["tree"] for t in day5["trees"]] == ["Discipline", "Shadow Magic", "Holy"]
    # Every query names the account, hourly samples, the 3 weeks.
    assert all('account_id="3"' in q and 'character="Testpriest-Fixture"' in q for q, _, _ in vm.queries)
    assert {(step, span.days) for _, step, span in vm.queries} == {("1h", 21)}


def test_history_is_what_moved_newest_first():
    readings = FakeVM(SERIES).talent_readings("Testpriest-Fixture", now=NOW)
    ch = talents.history(None, "PRIEST", readings)
    assert [c["date"] for c in ch] == [readings[-1]["t"][:10], readings[2]["t"][:10]]  # day 1: no change
    respec, level = ch
    assert [(t["tree"], t["points"]) for t in respec["before"]] == [("Discipline", 0), ("Shadow Magic", 6),
                                                                     ("Holy", 0)]
    assert [(t["tree"], t["points"]) for t in respec["after"]] == [("Discipline", 6), ("Shadow Magic", 0),
                                                                   ("Holy", 0)]
    assert {(g["name"], g["from"], g["to"]) for g in respec["gained"]} == {("Unbreakable Will", 0, 4),
                                                                          ("Wand Specialization", 0, 2)}
    assert {(g["name"], g["from"], g["to"]) for g in respec["dropped"]} == {("Spirit Tap", 5, 0),
                                                                           ("Improved Shadow Word: Pain", 1, 0)}
    assert [(g["name"], g["from"], g["to"]) for g in level["gained"]] == [("Improved Shadow Word: Pain", 0, 1)]
    assert level["dropped"] == []


def test_a_rank_up_is_gained_and_a_rank_down_dropped():
    r = [{"t": "2026-11-01T23:00:00Z", "trees": [{"tree": "Fire", "points": 2}],
          "ranks": {"x": {"spell": 1, "name": "Ignite", "rank": 2}}},
         {"t": "2026-11-02T23:00:00Z", "trees": [{"tree": "Fire", "points": 3}],
          "ranks": {"x": {"spell": 2, "name": "Ignite", "rank": 3}}},
         {"t": "2026-11-03T23:00:00Z", "trees": [{"tree": "Fire", "points": 1}],
          "ranks": {"x": {"spell": 3, "name": "Ignite", "rank": 1}}}]
    down, up = talents.history(None, "MAGE", r)
    assert (up["gained"][0]["from"], up["gained"][0]["to"]) == (2, 3) and not up["dropped"]
    assert (down["dropped"][0]["from"], down["dropped"][0]["to"]) == (3, 1) and not down["gained"]


class Plain:
    """A Views store stand-in for the degraded cases."""

    TALENT_HISTORY_DAYS = 21

    def __init__(self, url="http://vm.invalid", readings=None, boom=False):
        self.profile_vm_url, self._r, self._boom = url, readings or [], boom

    def talent_readings(self, name, days):
        if self._boom:
            raise OSError("vm-wow down")
        return self._r


def hist(store):
    v = views.Views.__new__(views.Views)
    v.store = store
    return v.talent_history("Testpriest-Fixture", "PRIEST")


def test_every_missing_source_says_one_honest_thing():
    """Degraded mode: never a blank. No Battle.net client on the site, no
    reading of the character, vm-wow down, or no change: each its own why."""
    assert hist(Plain(url=""))["why"] == "no_client"
    assert hist(Plain())["why"] == "no_battlenet" and not hist(Plain())["available"]
    assert hist(Plain(boom=True))["why"] == "unreachable"
    one = [{"t": "2026-11-18T23:00:00Z", "trees": [{"tree": "Holy", "points": 3}], "ranks": {}}]
    h = hist(Plain(readings=one * 2))
    assert h["available"] and h["changes"] == [] and h["since"] == "2026-11-18" and h["why"] is None


def test_no_bundle_carries_it():
    """Blizzard's 30 days: the history is the live full view's own answer
    (/api/talents), never in a tier's shape, so no static bundle has it."""
    blob = json.dumps({**redact.PUBLIC, **redact.ZOGZOG}, default=str)
    assert "changes" not in blob and "talent_history" not in blob
    d = {"char": "X", "header": {}, "talent_history": {"changes": [{"date": "2026-11-01"}]}}
    for tier in (redact.public, redact.zogzog):
        assert "talent_history" not in json.dumps(tier("char", d))


def test_the_exporter_reads_the_build_daily_and_pushes_it_every_minute():
    """The history is vm-wow's own: it needs one reading a day per character
    (the profile pass) and every minute's push (an hourly sample over 3
    minutes finds one), as the exporter does by default."""
    from wowstats import exporter

    exporter.configure({"BNET_CLIENT_ID_FILE": "/x", "BNET_CLIENT_SECRET_FILE": "/y"})
    assert exporter.INTERVALS["profile"] <= DAY and exporter.PUSH_INTERVAL <= 60
    assert {"wow_character_talent_points", "wow_character_talent_rank"} <= set(exporter.PROFILE_SERIES)


# ------------------------------------------------------------ WoWSims Exporter


@pytest.fixture
def cat(monkeypatch):
    from tests.test_talents import TABLES

    c = talents.Catalog("http://wago.invalid", "wowf", None)
    c.data, c.checked = talents.catalog_from(lambda name: iter(TABLES.get(name, []))), time.time()
    monkeypatch.setattr(talents, "shared", lambda: c)
    return c.data


def test_the_addons_string_reads_back_to_talents(cat):
    got = wowsims.decode(cat, "PRIEST", "02500420103--00502")
    assert [(t["tree"], t["points"]) for t in got["trees"]] == [("Discipline", 17), ("Holy", 0), ("Shadow", 7)]
    assert got["ranks"]["spirit tap"]["rank"] == 5 and got["ranks"]["wand specialization"]["rank"] == 2
    # Never guessed: a digit past a tree's end, a rank above the talent's
    # maximum, a fourth tree, another shape.
    assert wowsims.decode(cat, "PRIEST", "0" * 18 + "1") is None
    assert wowsims.decode(cat, "PRIEST", "9") is None
    assert wowsims.decode(cat, "PRIEST", "1-1-1-1") is None
    assert wowsims.decode(cat, "PRIEST", "abc") is None
    assert wowsims.unrecorded(cat, "PRIEST") == []


class AddonViews(views.Views):
    """Views over canned wow-talents rows and Battle.net's current read."""

    def __init__(self, rows, store=None, bnet=None):
        self.store, self._rows, self._bnet, self._aliases = store or Plain(url=""), rows, bnet, {}

    def q(self, query):
        assert 'feed:="wow-talents"' in query and 'char:="Testpriest-Fixture"' in query
        return self._rows

    def bnet_talents(self, char):
        return self._bnet


ROWS = [{"_time": "2026-09-01T20:00:00Z", "talents": "0050", "level": 12},
        {"_time": "2026-09-01T21:00:00Z", "talents": "0050", "level": 12},  # a gear save: no change
        {"_time": "2026-09-03T20:00:00Z", "talents": "0250", "level": 14},
        {"_time": "2026-10-10T20:00:00Z", "talents": "zz", "level": 20},  # unreadable: left out
        {"_time": "2026-11-18T20:00:00Z", "talents": "02500420103--00502", "level": 33}]


def test_the_addon_gives_every_change_without_battlenet(cat):
    h = AddonViews(ROWS).talent_history("Testpriest-Fixture", "PRIEST")
    assert h["available"] and h["source"] == "addon" and h["days"] is None and h["since"] == "2026-09-01"
    assert [c["date"] for c in h["changes"]] == ["2026-11-18", "2026-09-03"]
    assert [(g["name"], g["from"], g["to"]) for g in h["changes"][1]["gained"]] == [("Wand Specialization", 0, 2)]
    assert h["not_recorded"] == []


def test_a_talent_the_addon_cant_save_is_named(cat, monkeypatch):
    """The sim's trees miss a talent: the addon's string never has it. When
    Battle.net shows it learned, the page names it instead of dropping it."""
    full = wowsims.table()
    cut = {k: [list(t) for t in v] for k, v in full.items()}
    spirit_tap = next(d for sec in next(tr for tr in cat["trees"] if tr.get("class") == "PRIEST")["sections"]
                      for nd in sec["nodes"] if nd["spells"] and cat["spells"][nd["spells"][0]]["name"] == "Spirit Tap"
                      for d in nd["defs"])
    cut["PRIEST"] = [[d if d != spirit_tap else 1 for d in t] for t in cut["PRIEST"]]
    monkeypatch.setattr(wowsims, "table", lambda: cut)
    for bnet in ({"ranks": [{"spell": 15270, "rank": 5, "name": "Spirit Tap"}]},
                 {"ranks": [{"spell": 15270, "rank": 5, "name": None}]}):  # by spell id when unnamed
        h = AddonViews(ROWS, bnet=bnet).talent_history("Testpriest-Fixture", "PRIEST")
        assert h["not_recorded"] == ["Spirit Tap"], bnet
    # Not learned: nothing to say.
    assert AddonViews(ROWS, bnet={"ranks": []}).talent_history("Testpriest-Fixture", "PRIEST")["not_recorded"] == []


def test_without_the_addon_battlenet_and_the_one_step(cat):
    one = [{"t": "2026-11-18T23:00:00Z", "trees": [{"tree": "Holy", "points": 3}], "ranks": {}}]
    h = AddonViews([], store=Plain(readings=one * 2)).talent_history("Testpriest-Fixture", "PRIEST")
    assert h["source"] == "battlenet" and h["days"] == 21
    assert h["addon"] == {"name": "WoWSims Exporter", "url": "https://www.curseforge.com/wow/addons/wowsims-exporter"}
