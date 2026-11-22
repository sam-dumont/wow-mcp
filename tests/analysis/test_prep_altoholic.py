"""Altoholic's spellbook, reputations, purse and XP, stats and lockouts in
the prep tables (owner, 2026-11-22): the shipper's own events on the
made-up saves in tests/data/altoholic-modules, stored the way /ingest
stores them (nested values as JSON strings), with and without the Recorder.
Each table reads nothing new without Altoholic."""

import contextlib
import io
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wowstats import ingest
from wowstats.analysis import prep

DATA = Path(__file__).resolve().parent.parent / "data" / "altoholic-modules"
CHAR = "Testpriest-Fixture"
NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


def shipped(shipper, tmp_path, edit=None) -> list[dict]:
    """What the shipper sends for the made-up saves, as the store keeps it."""
    acct = tmp_path / "ACCT"
    shutil.copytree(DATA, acct / "SavedVariables")
    if edit:
        edit(acct / "SavedVariables")
    out = shipper.Out(dry=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        shipper.extract_altoholic(acct, {CHAR: {}, "Testhunter-Fixture": {}}, {}, out)
    sent = [json.loads(x) for x in buf.getvalue().splitlines()]
    stored, bad = ingest.parse("\n".join(json.dumps(e) for e in sent).encode(), NOW)
    assert bad == 0
    return [json.loads(x) for x in stored]


def rec(t: str, kind: str, **f) -> dict:
    return {"_time": t, "char": CHAR, "feed": "wow-recorder", "kind": kind, "source_id": f"rec:x:1:{t}{kind}",
            **{k: str(v) for k, v in f.items()}}


def polestar(t: str, money: int, level: int = 32) -> dict:
    return {"_time": t, "char": CHAR, "feed": "wow-character", "source_id": f"character:{CHAR}:{t}",
            "level": level, "xp": 9100, "xp_max": 21300, "money_copper": money, "zone": "Duskwood",
            "bind": "Darkshire", "class": "PRIEST", "race": "Human", "faction": "Alliance"}


@pytest.fixture
def alto(shipper, tmp_path):
    return shipped(shipper, tmp_path)


def test_spellbook_known_and_future(alto):
    db = prep.build(alto, CHAR, None, now=NOW)
    rows = {r[0]: r[1:] for r in db.execute("SELECT spell_id, source, known, available_level FROM spells_known")}
    assert rows[585] == ("altoholic", 1, None) and rows[6074] == ("altoholic", 0, 14)
    assert rows[2055] == ("altoholic", 0, 20) and len(rows) == 12


def test_the_recorder_learn_record_wins(alto):
    db = prep.build([*alto, rec("2026-11-20T10:00:00Z", "learn", spell_id=585, level=31)], CHAR, None, now=NOW)
    assert db.execute("SELECT source, known FROM spells_known WHERE spell_id = 585").fetchone() == ("learn", 1)
    assert db.execute("SELECT COUNT(*) FROM spells_known").fetchone()[0] == 12


def test_reputation_snapshots_cover_a_character_the_recorder_never_saw(alto):
    db = prep.build(alto, CHAR, None, now=NOW)
    got = db.execute("SELECT faction_id, reaction, rep_points, rep_gained, source, rec_id FROM reps "
                     "ORDER BY faction_id").fetchall()
    assert got == [(69, 6, 10500, None, "altoholic", None), (72, 5, 4200, None, "altoholic", None),
                   (87, 2, -5000, None, "altoholic", None)]


def test_reputation_history_moves_only(shipper, tmp_path):
    first = shipped(shipper, tmp_path / "a")
    later = shipped(shipper, tmp_path / "b", lambda sv: (sv / "DataStore_Reputations.lua").write_text(
        (sv / "DataStore_Reputations.lua").read_text()
        .replace(str((5 << 4) + (4200 << 8)), str((5 << 4) + (4700 << 8)))
        .replace("1795200000", "1795290000")))
    later = [r for r in later if r.get("kind") == "reps"]
    db = prep.build(first + later, CHAR, None, now=NOW)
    sw = db.execute("SELECT rep_points, rep_gained FROM reps WHERE faction_id = 72 ORDER BY t").fetchall()
    assert sw == [(4200, None), (4700, 500)]
    # An unchanged faction gets no second row.
    assert db.execute("SELECT COUNT(*) FROM reps WHERE faction_id = 69").fetchone()[0] == 1
    # The Recorder saw a change in between: its rows count those points, the snapshot's move doesn't.
    db = prep.build([*first, *later, rec("2026-11-21T12:00:00Z", "rep", faction_id=72, reaction=5, standing=4500,
                                         rep_gained=300)], CHAR, None, now=NOW)
    assert db.execute("SELECT rep_gained FROM reps WHERE faction_id = 72 AND source = 'altoholic' "
                      "ORDER BY t").fetchall() == [(None,), (None,)]


def test_snapshot_alone_and_merged_with_polestar(alto):
    db = prep.build(alto, CHAR, None, now=NOW)
    assert db.execute("SELECT money_copper, xp, rested_xp, subzone, bind, source FROM snapshots").fetchall() == [
        (123456, 9100, 4200, "Darkshire", "Darkshire", "altoholic")]
    # Polestar saved the same logout 30 s earlier: one snapshot, Altoholic's rested XP added.
    db = prep.build([*alto, polestar("2026-11-20T18:39:30Z", 123456)], CHAR, None, now=NOW)
    assert db.execute("SELECT money_copper, rested_xp, source FROM snapshots").fetchall() == [
        (123456, 4200, "polestar,altoholic")]
    assert db.execute("SELECT COUNT(*) FROM gold_moves").fetchone()[0] == 0
    # A different purse: two snapshots, a gold move between them.
    db = prep.build([*alto, polestar("2026-11-19T18:00:00Z", 100000)], CHAR, None, now=NOW)
    assert db.execute("SELECT delta_copper FROM gold_moves").fetchall() == [(23456,)]


def test_char_stats_and_lockouts(alto):
    db = prep.build(alto, CHAR, None, now=NOW)
    st = db.execute("SELECT strength, intellect, spell_crit_pct, melee_crit_pct, honorable_kills, health_max, "
                    "source FROM char_stats").fetchall()
    assert st == [(45, 80, 6.5, 5.2, 12, 812, "altoholic")]
    lo = db.execute("SELECT instance, instance_id, raid, active, bosses_killed, bosses_total, weekly_reset_day "
                    "FROM lockouts ORDER BY expires_t").fetchall()
    # Scholomance reset on 2026-11-21 (before NOW), Onyxia on 2026-11-24.
    assert lo == [("Scholomance", 289, 0, 0, 1, 3, 2), ("Onyxia's Lair", 249, 1, 1, 1, 1, 2)]


def test_without_altoholic_nothing_new(alto):
    plain = [r for r in alto if r.get("source") != "altoholic"]
    assert plain == []
    db = prep.build([polestar("2026-11-20T18:39:30Z", 5)], CHAR, None, now=NOW)
    for t in ("char_stats", "lockouts", "spells_known", "reps"):
        assert db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
    assert db.execute("SELECT source, rested_xp FROM snapshots").fetchall() == [("polestar", None)]
