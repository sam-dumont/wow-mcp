"""Two facts the brief hands every client (seeded evaluation, review 5): a
level's calendar beside its played time, and the worn gear against the spec
it's scored for. Deterministic, no model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wowstats.analysis import drive, passes, prep

CHAR = "Calendar-Test"
T0 = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
DAY = 86400


def _t(s: float) -> str:
    return (T0 + timedelta(seconds=s)).isoformat().replace("+00:00", "Z")


def _rec(s: float, kind: str, level: int, i: int) -> dict:
    return {"_time": _t(s), "char": CHAR, "feed": "wow-recorder", "kind": kind, "level": level,
            "source_id": f"r{i}", "zone": "Wetlands"}


def _evening(start: float, level: int, hours: float, i: int) -> list[dict]:
    """One login: a session record, then one every 5 minutes."""
    n = int(hours * 12)
    return [_rec(start + k * 300, "session" if k == 0 else "xp", level, i * 1000 + k) for k in range(n + 1)]


def _split(s: float, level: int, played: float, partial: bool = False) -> dict:
    return {"_time": _t(s), "char": CHAR, "feed": "wow-level", "level": level, "seconds": played,
            "partial": partial, "source_id": f"split{level}"}


def history() -> list[dict]:
    """Level 10 on evening 0 (ding at 2 h), level 11 over two evenings with a
    ten-day break between them (ding on day 11), level 12 in play after a
    four-day silence. Level 11's split says 3 h played: the Recorder saw 2 h
    of it, so one hour was play it missed."""
    rows = _evening(0, 10, 2, 1)
    ding10 = 2 * 3600
    rows.append(_rec(ding10, "level", 11, 9001))
    rows += _evening(ding10 + 300, 11, 1, 2)
    rows += _evening(10 * DAY, 11, 1, 3)
    ding11 = 10 * DAY + 3600
    rows.append(_rec(ding11, "level", 12, 9002))
    rows += _evening(14 * DAY, 12, 1, 4)
    rows += [_split(ding10, 10, 2 * 3600, partial=True), _split(ding11, 11, 3 * 3600)]
    return rows


def build(rows=None, advice=None):
    return prep.build(rows if rows is not None else history(), CHAR, advice=advice)


def test_each_level_carries_its_calendar_offline_time_and_longest_silence():
    db = build()
    got = {r[0]: r[1:] for r in db.execute(
        "SELECT level, calendar_s, offline_s, longest_gap_s, longest_gap_from_local, longest_gap_to_local, "
        "recorder_missed_s FROM levels")}
    # The first tracked level has no previous ding: no calendar, no window.
    assert got[10] == (None, None, None, None, None, None)
    cal, off, gap, a, b, missed = got[11]
    assert cal == 10 * DAY + 3600 - 2 * 3600
    assert off == cal - 3 * 3600  # the game's played time, not the Recorder's
    # From the end of the first evening of level 11 to the start of the next.
    assert gap == 10 * DAY - (2 * 3600 + 300 + 3600)
    assert a.startswith("2026-10-01T") and b.startswith("2026-10-11T")
    # The split says 3 h, the Recorder saw about 1 h of level 11 on each side
    # of the break: the hour it missed is the coverage's gap, not time away.
    assert 3000 < missed < 4000
    # The level in play: no calendar yet, its silence measured to the last record.
    cal12, off12, gap12, *_ , missed12 = got[12]
    assert cal12 is None and off12 is None and missed12 is None
    assert gap12 == 14 * DAY - (10 * DAY + 3600)  # from the ding to the next login


def test_the_semantic_layer_reads_the_prep_s_calendar():
    from wowstats.analysis import TABLES, duck, semantic

    con = duck.mirror(build(), TABLES)
    semantic.prepare(con)
    got = semantic.run(semantic.Layer(con), duck.ReadOnlyDuck(con), {
        "metrics": ["calendar_s", "offline_s", "offline_share"], "group_by": ["level"]})
    rows = {r[0]: dict(zip(got["columns"], r, strict=True)) for r in got["rows"]}
    assert rows[11]["calendar_s"] == 10 * DAY - 3600
    assert rows[11]["offline_s"] == 10 * DAY - 3600 - 3 * 3600
    assert 0.98 < rows[11]["offline_share"] < 1
    assert rows[10]["offline_share"] is None  # partial split: not measured


def test_the_brief_lists_long_silences_and_nothing_else():
    f = drive.facts(build())["level_calendar"]
    assert [x["level"] for x in f["levels"]] == [11, 12]  # the longest silence first
    lv11 = f["levels"][0]
    assert lv11["played_h"] == 3.0 and lv11["calendar_days"] == 10.0 and lv11["longest_gap_days"] == 9.9
    assert lv11["recorder_missed_h"] == 0.9
    # Evenings a day apart are no break.
    short = [r for r in history() if r["_time"] < _t(10 * DAY)]
    short += _evening(DAY, 11, 1, 5)
    assert drive.facts(build(short))["level_calendar"]["levels"] == "none"


ADVICE = {
    "spec": {"key": "holy", "label": "Holy (healer)"},
    "upgrades_all": [],
    "slots": [
        {"slot": 1, "slot_name": "Head", "standing": {"rank": None, "pct": 17, "label": "17% of BiS"},
         "current": {"item_id": 253981, "name": "Filigreed Shadow Circlet", "score": 5.59, "known": True,
                     "fit": {"for_you": "spec", "built_for": "damage casters"}}},
        {"slot": 5, "slot_name": "Chest", "standing": {"rank": 1, "pct": 100, "label": "BiS"},
         "current": {"item_id": 2000, "name": "Robe", "score": 25.0, "known": True,
                     "fit": {"for_you": "yes", "built_for": "healers"}}},
        {"slot": 2, "slot_name": "Neck", "standing": None, "current": None},
    ],
}


def _with_gear(rows: list[dict]) -> list[dict]:
    items = [{"slot": 1, "slot_name": "Head", "item_id": 253981, "name": "Filigreed Shadow Circlet"},
             {"slot": 5, "slot_name": "Chest", "item_id": 2000, "name": "Robe"}]
    return [*rows, {"_time": _t(3 * 3600), "char": CHAR, "feed": "wow-gear", "items": items,
                    "source_id": "gear:1"}]


def test_the_worn_gear_carries_its_score_and_fit_for_the_scored_spec():
    db = build(_with_gear(history()), ADVICE)
    got = {r[0]: r[1:] for r in db.execute("SELECT slot_name, scored_for, score, bis_pct, fit, built_for FROM gear")}
    assert got["Head"] == ("Holy (healer)", 5.59, 17.0, "spec", "damage casters")
    assert got["Chest"] == ("Holy (healer)", 25.0, 100.0, "yes", "healers")
    g = drive.facts(db)["gear_fit"]
    assert g["scored_for"] == "Holy (healer)"
    assert [(x["slot"], x["item"], x["bis_pct"], x["fit"]) for x in g["worn"]] == [
        ("Head", "Filigreed Shadow Circlet", 17.0, "spec")]
    # Without the advice (no sheet the advice can read) nothing is invented.
    bare = drive.facts(build(_with_gear(history())))["gear_fit"]
    assert bare["worn"] == "none" and bare["scored_for"].startswith("no gear sheet")


def test_the_rules_tell_every_client_to_weigh_both():
    r = passes.FINDING_RULES
    assert "offline_s" in r and "longest_gap_s" in r and "played_s" in r
    assert "gear.scored_for" in r and "gear.fit" in r and "names that spec" in r


def test_a_finding_on_the_worn_gear_s_fit_follows_the_scoring():
    """gear now holds scores for the scored spec: a WEIGHTS change marks a
    finding that reads it, never one on gear_sheets alone."""
    from wowstats.analysis import manifest

    assert manifest.reads_scoring({"sql": "SELECT bis_pct FROM gear WHERE slot = 1"})
    assert manifest.reads_scoring({"sql": "SELECT COUNT(*) FROM upgrade_candidates"})
    assert not manifest.reads_scoring({"sql": "SELECT COUNT(*) FROM gear_sheets"})
