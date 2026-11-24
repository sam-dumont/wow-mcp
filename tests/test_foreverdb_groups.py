"""Who you grouped with, from ForeverDB_Helper's journal (owner, 2026-11-24:
"seeing who I grouped with is kinda important").

tests/data/foreverdb/ForeverDB_Helper.lua: a made-up ForeverDBHelperDB in the
shape ForeverDB_Helper 1.7.4 writes (Journal.lua captureGroup: one `player`
entry per groupmate per day, the journal keyed by first name and realm). Every
name is invented; the guild, realm and the private note are there on purpose
and must never leave the shipper.
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

from wowstats import ingest, otherplayers

DATA = Path(__file__).parent / "data" / "foreverdb"
ROSTER = {"Testpriest-Fixture": {}, "Testhunter-Fixture": {}}


def account(tmp_path, bak=False) -> Path:
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    (acct / "SavedVariables").mkdir(parents=True)
    shutil.copy(DATA / "ForeverDB_Helper.lua", acct / "SavedVariables" / "ForeverDB_Helper.lua")
    if bak:
        shutil.copy(DATA / "ForeverDB_Helper.lua", acct / "SavedVariables" / "ForeverDB_Helper.lua.bak")
    return acct


def run(shipper, acct, state, roster=ROSTER) -> list[dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        shipper.extract_foreverdb_groups(acct, roster, state, shipper.Out(dry=True))
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def test_groupmates_as_wow_player(shipper, tmp_path):
    sent = run(shipper, account(tmp_path, bak=True), {})
    assert [(e["player"], e["context"]) for e in sent] == [("Strangerfoo-Madeup", "grouped"),
                                                          ("Strangerbar-Madeup", "dungeon")]
    foo, bar = sent
    assert foo == {"_time": "2026-11-24T14:07:43.000Z", "feed": "wow-player", "char": "Testpriest-Fixture",
                   "source_id": "player:foreverdb-helper:Strangerfoo-Madeup:1795529263",
                   "_msg": "grouped Strangerfoo-Madeup in Stranglethorn Vale", "player": "Strangerfoo-Madeup",
                   "context": "grouped", "source": "foreverdb-helper", "zone": "Stranglethorn Vale", "level": 32,
                   "class": "Rogue", "race": "Human", "role": "damage"}
    assert bar["dungeon"] == "The Deadmines" and bar["role"] == "healer" and bar["level"] == 19
    for e in sent:
        assert set(e) <= shipper.EVENT_BASE | shipper.PLAYER_KEYS
    text = json.dumps(sent)
    # Never the realm, guild, map, group size, the journal's other entries or an own alt.
    for never in ("Made Up Guild", "Otherrealm", "1434", "groupSize", "Strangerqux", "Strangerzed", "Testhunter"):
        assert never not in text


def test_sent_once(shipper, tmp_path):
    acct, state = account(tmp_path), {}
    assert len(run(shipper, acct, state)) == 2
    assert run(shipper, acct, state) == []


def test_journal_of_an_ambiguous_first_name_is_skipped(shipper, tmp_path):
    roster = {"Testpriest-Fixture": {}, "Testpriest-Other": {}}
    assert run(shipper, account(tmp_path), {}, roster) == []


def test_ingest_keeps_what_was_sent(shipper, tmp_path):
    bar = run(shipper, account(tmp_path), {})[1]
    assert otherplayers.allowed({**bar, "guild": "Made Up Guild", "player_realm": "Otherrealm"}) == bar
    stored = ingest.screen(bar)
    assert stored["role"] == "healer" and stored["context"] == "dungeon"


def test_archived_and_optional(shipper, tmp_path):
    assert shipper.ARCHIVED_SOURCES.match("Account/ACCT/SavedVariables/ForeverDB_Helper.lua")
    assert shipper.ARCHIVED_SOURCES.match("Account/ACCT/SavedVariables/ForeverDB_Helper.lua.bak")
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    (acct / "SavedVariables").mkdir(parents=True)
    assert run(shipper, acct, {}) == []
