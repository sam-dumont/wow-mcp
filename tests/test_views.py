import pytest

from wowstats import views
from wowstats.views import build_climb, build_levels


def test_partial_split_is_a_finished_level():
    """Polestar's `partial` marks the split where tracking began mid-level.
    The level still ended on a real ding (Testpriest's 22 -> 23 on 2026-11-12)."""
    rows = [
        {"_time": "2026-11-12T10:46:00Z", "source_id": "level:A-B:22", "level": "22", "seconds": "5199",
         "xp": "11745", "partial": "true"},
        {"_time": "2026-11-12T13:00:17Z", "source_id": "level:A-B:23", "level": "23", "seconds": "8056",
         "xp": "29400", "partial": "false"},
    ]
    levels = build_levels(rows)
    assert [lv["level"] for lv in levels] == [22, 23]
    assert build_climb(levels, []) == [{"level": 23, "t": "2026-11-12T10:46:00Z"},
                                       {"level": 24, "t": "2026-11-12T13:00:17Z"}]


def test_header_totals_come_from_the_splits():
    """The climb's kills and quests are the splits' sums over the completed
    levels, not the Recorder's kill/turnin records, which only cover the
    time the Recorder was on; the level in play has no split, so no count."""
    from wowstats.analysis import pages
    from wowstats.views import build_header

    rows = [
        {"_time": "2026-11-12T10:46:00Z", "source_id": "level:A-B:22", "level": "22", "seconds": "5199",
         "xp": "11745", "quests": "4", "kills": "60", "partial": "true"},
        {"_time": "2026-11-12T13:00:17Z", "source_id": "level:A-B:23", "level": "23", "seconds": "8056",
         "xp": "29400", "quests": "11", "kills": "95", "partial": "false"},
    ]
    levels = build_levels(rows)
    n = pages.numbers("A-B", [{**r, "feed": "wow-level", "char": "A-B"} for r in rows])
    pages.apply_rows(levels, [], n)
    res = {"snap": [], "xp": [{"_time": "2026-11-12T14:00:00Z", "kind": "xp", "level": "24", "xp": "10",
                               "xp_max": "30000"}]}
    hd = build_header("A-B", levels, res, layer=pages.header(n))
    assert hd["counts"]["completed_levels"] == {"from_level": 22, "to_level": 23, "kills": 155, "quests": 15}
    assert hd["counts"]["current_level"] is None and hd["played_s"] == 5199 + 8056


def test_header_for_a_character_only_syndicator_knows():
    """No Polestar: race, class and gold from the roster snapshot; the page
    knows to say Polestar doesn't track it. Sex and guild are Battle.net's
    (2026-11-20, Views.bnet_profile): an old snapshot's are not read."""
    snap = {"_time": "2026-11-12T21:00:00Z", "race": "Night Elf", "class": "HUNTER", "sex": "female",
            "guild": "Moss Order", "money_copper": "31337", "faction": "Alliance", "source": "syndicator"}
    hd = views.build_header("Sylwen-Moon", [], {"snap": [snap], "xp": []}, None)
    assert (hd["race"], hd["class"], hd["sex"], hd["guild"], hd["money_copper"]) == (
        "Night Elf", "Hunter", None, None, 31337)
    assert hd["polestar"] is False and hd["sources"] == ["syndicator"] and hd["level"] is None
    folder = views.build_header("Testhunter-Fixture", [], {"snap": [{"_time": "2026-11-08T16:46:59Z",
                                                                "source": "folder"}], "xp": []}, None)
    assert folder["polestar"] is False and folder["race"] is None and folder["seen"]
    polestar = views.build_header("Hero-Test", [], {"snap": [{"_time": "2026-11-12T21:00:00Z", "level": "12"}],
                                                    "xp": []}, None)
    assert polestar["polestar"] is True and polestar["sex"] is None


def _lv(level, seconds, at, partial=False):
    return {"level": level, "seconds": seconds, "xp": 1000, "quests": 1, "kills": 1, "partial": partial, "at": at,
            "xp_per_hour": None}


def test_played_total_from_altoholic():
    """Altoholic's /played is the game's answer at the last login: played -
    playedThisLevel pins when that level began, and the splits carry it to
    now. A snapshot with a ding in its session is skipped (the answer may be
    from the level before)."""
    levels = [_lv(30, 3000, "2026-11-10T10:00:00Z"), _lv(31, 4000, "2026-11-12T10:00:00Z"),
              _lv(32, 5000, "2026-11-15T10:00:00Z")]
    header = {"level": 33, "time_in_level_s": 600}
    assert views.played_total([], levels, header) == {}
    # Saved 2026-11-14 at level 32, 1000 s into it; total then 200000 s. Since
    # the start of 32: its split (5000) and 600 s of 33: 200000 - 1000 + 5600.
    snaps = [{"_time": "2026-11-14T22:00:00Z", "played_total_s": "200000", "played_level_s": "1000", "level": "32"}]
    got = views.played_total(snaps, levels, header)
    assert got == {"played_total_s": 204600, "played_total_from": "altoholic", "played_total_at": "2026-11-14T22:00:00Z"}
    # A newer snapshot whose session had a ding (31 -> 32 on the 12th, at 10:00)
    # is skipped for the one before; with no older one there is nothing.
    newer = {"_time": "2026-11-12T11:00:00Z", "played_total_s": "190000", "played_level_s": "100", "level": "32"}
    assert views.played_total([newer], levels, header) == {}
    # In the level being played: no split yet, the Recorder's time in it.
    now = [{"_time": "2026-11-17T22:00:00Z", "played_total_s": "210000", "played_level_s": "300", "level": "33"}]
    assert views.played_total(now, levels, header)["played_total_s"] == 210000 - 300 + 600
    # A level before tracking began can't be carried forward.
    old = [{"_time": "2026-11-01T22:00:00Z", "played_total_s": "150000", "played_level_s": "10", "level": "25"}]
    assert views.played_total(old, levels, header) == {}
    # No Polestar splits at all: the answer as saved.
    assert views.played_total(old, [], {"level": 25})["played_total_s"] == 150000


def test_played_total_never_below_what_is_proven():
    """A fresh or broken Altoholic save (Testhunter's 2 s on 2026-11-18) can't
    pull the total under the time the splits already prove, and with no
    splits it counts only at the level the character is at."""
    levels = [_lv(30, 3000, "2026-11-10T10:00:00Z"), _lv(31, 4000, "2026-11-12T10:00:00Z")]
    header = {"level": 32, "time_in_level_s": 600}
    tiny = [{"_time": "2026-11-18T16:27:01Z", "played_total_s": "2", "played_level_s": "2", "level": "32"}]
    assert views.played_total(tiny, levels, header) == {}
    # Behind it, an older sound one still counts.
    ok = {"_time": "2026-11-13T10:00:00Z", "played_total_s": "200000", "played_level_s": "1000", "level": "31"}
    assert views.played_total([*tiny, ok], levels, header)["played_total_s"] == 200000 - 1000 + 4000 + 600
    # No splits: a save from another level than the character's is not its total.
    assert views.played_total([{**tiny[0], "level": "1"}], [], {"level": 12}) == {}
    assert views.played_total([{**tiny[0], "level": "1"}], [], {"level": 1})["played_total_s"] == 2


def test_loot_groups_runs_and_levels_and_names_items(monkeypatch):
    """The /zogzog/ loot history: a group per dungeon run (by its time
    window) and per level for the rest, items summed, green+ counted as
    notable, names from the shipper when the item tables don't know them."""
    from wowstats import items, views

    monkeypatch.setattr(items.Items, "get", lambda self, i: {2589: ("Linen Cloth", 1), 6597: (None, 2)}.get(
        i, (None, None)))
    loot = [
        {"_time": "2026-11-12T10:05:00Z", "items": "2589:2;6597:1", "item_names": '{"6597":"Spellbinder Robe"}',
         "zone": "Blackfathom Deeps", "source_id": "rec:1:1:1"},
        {"_time": "2026-11-12T10:20:00Z", "items": "2589:3", "zone": "Blackfathom Deeps", "source_id": "rec:1:1:2"},
        {"_time": "2026-11-13T09:00:00Z", "items": "4709:1", "item_names": "not json", "zone": "Ashenvale",
         "source_id": "rec:1:2:1"},
    ]
    runs = [{"_time": "2026-11-12T10:00:00Z", "left": "2026-11-12T10:30:00Z", "name": "Blackfathom Deeps",
             "level": "24", "source_id": "run:1"}]
    v = views.Views.__new__(views.Views)
    v._aliases = {}
    monkeypatch.setattr(v, "q", lambda q: loot if "kind:=loot" in q else runs if "wow-dungeon" in q else [],
                        raising=False)
    monkeypatch.setattr(v, "char", lambda c: {"header": {"level": 25}, "levels": []}, raising=False)
    out = v.loot("Hero-Test")
    assert out["total"] == 4 and out["notable"] == 1  # entries looted, not quantities
    newest, run = out["groups"]
    assert newest["kind"] == "level" and newest["level"] == 25
    assert newest["items"] == [{"item_id": 4709, "name": None, "quality": None, "count": 1,
                                "t": "2026-11-13T09:00:00Z", "zone": "Ashenvale"}]
    assert run["kind"] == "dungeon" and run["name"] == "Blackfathom Deeps" and run["level"] == 24
    assert run["notable"] == 1
    assert [(i["name"], i["count"]) for i in run["items"]] == [("Spellbinder Robe", 1), ("Linen Cloth", 5)]


# ---------------------------------------------------------------- the title line


@pytest.mark.parametrize("cls, spec, want", [
    # Testpriest, 2026-11-18: Discipline by points, gear levelling Shadow (owner).
    ("PRIEST", {"name": "Discipline", "split": "16/0/7", "scored_for": {"key": "shadow", "label": "Shadow"}},
     "Priest · Discipline 16/0/7 (levelling Shadow)"),
    # Weights on the dominant tree: no parenthesis; Battle.net's longer tree
    # name still matches its spec.
    ("PRIEST", {"name": "Shadow Magic", "split": "5/0/26", "scored_for": {"key": "shadow", "label": "Shadow"}},
     "Priest · Shadow Magic 5/0/26"),
    ("Priest", {"name": "Discipline", "split": "16/0/7", "scored_for": {"key": "discipline", "label": "Discipline"}},
     "Priest · Discipline 16/0/7"),
    # A label's note stays out of the title.
    ("PRIEST", {"name": "Discipline", "split": "16/0/7", "scored_for": {"key": "holy", "label": "Holy (healer)"}},
     "Priest · Discipline 16/0/7 (levelling Holy)"),
    # No talent data (Testhunter): the class alone.
    ("HUNTER", {"name": None, "split": None, "scored_for": {"key": "bm", "label": "Beast Mastery"}}, "Hunter"),
    ("HUNTER", None, "Hunter"),
    # Talents but no gear setting known: no parenthesis.
    ("PRIEST", {"name": "Discipline", "split": "16/0/7", "scored_for": None}, "Priest · Discipline 16/0/7"),
    (None, {"name": "Discipline", "split": "16/0/7"}, None),
])
def test_card_title(cls, spec, want):
    assert views.card_title(cls, spec) == want


def test_overview_without_a_snapshot_or_any_level(monkeypatch, tmp_path):
    """A character with no snapshot: its level comes from the newest ding;
    with no ding and no split either (no level anywhere), the Overview still
    answers instead of crashing in the route clock (route.level(None))."""
    import json

    from tests.test_bounded_reads import CHAR, FakeVL, events
    from wowstats import eventcache, stores, ui

    rows = [r for r in events() if r.get("feed") != "wow-character"]  # events() adds a snapshot: not here
    assert not any(r.get("feed") == "wow-character" for r in rows)

    def overview(rs):
        monkeypatch.setattr(stores, "http_get", FakeVL(rs))
        monkeypatch.setattr(eventcache, "_SHARED", {})
        app = ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": "http://vl.invalid",
                      "MODEL3D": "off", "DEFAULT_CHAR": CHAR, "HOME": str(tmp_path)})
        return app.char(CHAR)["header"], app.overview(CHAR)

    header, ov = overview(rows)
    assert header["level"] == 26  # the newest ding
    assert ov
    bare = [r for r in rows if r.get("feed") != "wow-level" and r.get("kind") != "level"]
    header, ov = overview(bare)
    assert header["level"] is None and ov
    assert "None" not in json.dumps([c.get("text") for c in ov.get("now") or []])


def _sheets(ids):
    """A -> B -> A, then A again on retry (the same line sent twice), one
    sheet a day at noon; `ids(content, day)` names each one as a shipper would."""
    a = '[{"slot":16,"item_id":1317,"name":"Staff A"}]'
    b = '[{"slot":16,"item_id":7001,"name":"Staff B"}]'
    return [{"_time": f"2026-11-0{d}T12:00:00Z", "items": items, "item_level": il, "source_id": ids(tag, d)}
            for tag, items, il, d in (("a", a, 20, 1), ("b", b, 25, 2), ("a", a, 20, 3), ("a", a, 20, 3))]


@pytest.mark.parametrize("ids", [
    lambda c, d: f"gear:Hero-Test:{c * 12}",                    # shipper <= 0.7.0-rc.5: the content alone
    lambda c, d: f"gear:Hero-Test:{c * 12}:{1793880000 + d * 86400}",  # newer: content and capture time
], ids=["old-shipper", "new-shipper"])
def test_a_sheet_worn_again_keeps_its_first_occurrence(ids):
    """Review 2026-11-21, C: outfit A on day 1, B on day 2, A again on day
    3. The history has three sheets (the retry of day 3 collapses), and the
    level played on day 1 shows A, not "no sheet"."""
    from datetime import datetime, timedelta

    from wowstats import insights

    rows = _sheets(ids)
    v = views.Views.__new__(views.Views)
    v.q = lambda query: rows
    v.char_filter = lambda char: ""
    gh = v.gear_history("Hero-Test")
    assert [(g["_time"][:10], g["items"][0]["item_id"]) for g in gh] == [
        ("2026-11-01", 1317), ("2026-11-02", 7001), ("2026-11-03", 1317)]
    t0 = datetime.fromisoformat("2026-11-01T09:00:00+00:00")
    data = {"levels": [{"level": 12, "seconds": 3600, "partial": False}]}
    snap = insights.snapshot(12, data, (t0, t0 + timedelta(hours=4)), gh, lambda q: [], "", None, {}, [])
    assert snap["gear_partial"] is False and snap["gear"]["items"][0]["item_id"] == 1317
