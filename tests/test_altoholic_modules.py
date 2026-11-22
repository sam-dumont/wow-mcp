"""Altoholic's spellbook, reputations, purse and XP, stats and lockouts
(owner, 2026-11-22): what the shipper reads, what it never reads, and what
/ingest keeps.

tests/data/altoholic-modules: made-up DataStore saves in the shapes the
Forever client writes (DataStore 2026-10-14). Every name, guild, friend and
calendar title is invented; the guild, friends list and calendar are filled
on purpose, and nothing of them may leave the shipper.
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

from wowstats import ingest, otherplayers

DATA = Path(__file__).parent / "data" / "altoholic-modules"
ROSTER = {"Testpriest-Fixture": {}, "Testhunter-Fixture": {}}
# Never in any event: the guild, its ranks, the friends list, the calendar.
NEVER = ("Made Up Guild", "Made Up Rank", "Strangerfoo", "secret raid", "guildRank", "guild")


def account(tmp_path, files=None) -> Path:
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True)
    for f in DATA.iterdir():
        if files is None or f.name in files:
            shutil.copy(f, sv / f.name)
    return acct


def run(shipper, acct, state=None, roster=ROSTER) -> list[dict]:
    out = shipper.Out(dry=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        shipper.extract_altoholic(acct, roster, {} if state is None else state, out)
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def by(events, kind, char="Testpriest-Fixture"):
    return next(e for e in events if e.get("kind", "sheet") == kind and e["char"] == char)


def test_spellbook_known_and_future(shipper, tmp_path):
    got = shipper.altoholic_spells(account(tmp_path))
    p = got["Testpriest-Fixture"]
    assert p["class"] == "PRIEST"
    spells = dict(map(tuple, p["spells"]))
    # bits 0-7: 0 known, else the level it becomes available at; 8+: the spell id.
    assert spells[585] == 0 and spells[2050] == 0 and spells[6074] == 14 and spells[2055] == 20
    assert spells[8092] == 10 and 0 not in spells
    assert got["Testhunter-Fixture"]["class"] == "HUNTER" and dict(map(tuple, got["Testhunter-Fixture"]["spells"]))[1978] == 4


def test_reputations_decoded_guild_left_out(shipper, tmp_path):
    got = shipper.altoholic_reps(account(tmp_path))
    # bit 3 negative, bits 4-7 the standing, 8+ the points from neutral;
    # a friendship faction (type 1, retail only) is left out.
    assert got["Testpriest-Fixture"]["factions"] == [[69, 6, 10500], [72, 5, 4200], [87, 2, -5000]]
    assert got["Testhunter-Fixture"]["factions"] == [[72, 4, 250]]


def test_sheet_fields_and_nothing_else(shipper, tmp_path):
    got = shipper.altoholic_sheet(account(tmp_path))["Testpriest-Fixture"]
    assert {k: got[k] for k in ("money_copper", "xp", "xp_max", "rested_xp", "zone", "subzone", "bind", "level")} == {
        "money_copper": 123456, "xp": 9100, "xp_max": 21300, "rested_xp": 4200, "zone": "Duskwood",
        "subzone": "Darkshire", "bind": "Darkshire", "level": 32}
    assert got["logged_out"] is True and "guildRankIndex" not in got and "sex" not in got


def test_stats_strings(shipper, tmp_path):
    s = shipper.altoholic_stats(account(tmp_path))["Testpriest-Fixture"]
    assert (s["strength"], s["agility"], s["stamina"], s["intellect"], s["armor"]) == (45, 30, 50, 80, 420)
    assert (s["melee_dmg_min"], s["melee_dmg_max"], s["melee_ap"], s["melee_crit_pct"]) == (21, 35, 40, 5.2)
    assert (s["spell_bonus_dmg"], s["spell_bonus_heal"], s["spell_crit_pct"], s["mp5"]) == (60, 58, 6.5, 18)
    assert (s["dodge_pct"], s["honorable_kills"], s["dishonorable_kills"]) == (3.1, 12, 1)
    assert (s["health_max"], s["power_type"], s["power_max"]) == (812, 0, 1250)
    assert not any("resist" in k for k in s)  # DataStore_Stats saves none


def test_lockouts_own_only(shipper, tmp_path):
    got = shipper.altoholic_lockouts(account(tmp_path))
    assert set(got) == {"Testpriest-Fixture"}
    lo = got["Testpriest-Fixture"]
    assert (lo["weekly_reset_day"], lo["weekly_reset_hour"]) == (2, 6)
    ony = next(x for x in lo["lockouts"] if x["instance_id"] == 249)
    assert ony == {"instance": "Onyxia's Lair", "instance_id": 249, "expires": 1795200000 + 345600, "extended": 0,
                   "raid": 1, "bosses": [["Onyxia", 1]]}
    scholo = next(x for x in lo["lockouts"] if x["instance_id"] == 289)
    assert scholo["bosses"] == [["Rattlegore", 1], ["Ras Frostwhisper", 0], ["Darkmaster Gandling", 0]]


def test_events_allowlisted_change_detected_and_private(shipper, tmp_path):
    acct = account(tmp_path)
    state: dict = {}
    evs = run(shipper, acct, state)
    kinds = sorted({(e["feed"], e.get("kind")) for e in evs}, key=str)
    assert kinds == sorted({("wow-altoholic", "spells"), ("wow-altoholic", "reps"), ("wow-altoholic", "stats"),
                            ("wow-altoholic", "lockouts"), ("wow-character", None)}, key=str)
    for e in evs:
        line = json.dumps(e)
        for s in NEVER:
            assert s not in line, (s, e)
        keys = shipper.ALTO_KEYS[e.get("kind", "sheet")]
        assert set(e) <= shipper.EVENT_BASE | keys, e
        assert e["source"] == "altoholic"
    sheet = by(evs, "sheet")
    assert sheet["feed"] == "wow-character" and "kind" not in sheet and sheet["money_copper"] == 123456
    assert by(evs, "spells")["known"] == 9 and by(evs, "spells")["future"] == 3
    # lastUpdate is the time: in the file, exact.
    assert by(evs, "reps")["_time"] == "2026-11-20T18:40:00.000Z" and "at_basis" not in by(evs, "reps")
    # Nothing changed: nothing sent again.
    assert run(shipper, acct, state) == []
    # One module changed: only its event.
    rp = acct / "SavedVariables" / "DataStore_Reputations.lua"
    rp.write_text(rp.read_text().replace(str((5 << 4) + (4200 << 8)), str((5 << 4) + (4300 << 8))))
    again = run(shipper, acct, state)
    assert [(e["kind"], e["char"]) for e in again] == [("reps", "Testpriest-Fixture")]
    assert [72, 5, 4300] in again[0]["factions"]


def test_only_the_roster(shipper, tmp_path):
    evs = run(shipper, account(tmp_path), roster={"Testhunter-Fixture": {}})
    assert evs and {e["char"] for e in evs} == {"Testhunter-Fixture"}


def test_absent_or_empty_modules_send_nothing(shipper, tmp_path):
    assert run(shipper, account(tmp_path, files=())) == []  # no Altoholic
    # DataStore.lua alone (character ids) and nothing else: nothing, no error.
    assert run(shipper, account(tmp_path / "a", files=("DataStore.lua",))) == []
    # The owner's Agenda today: empty lockouts, options only.
    acct = account(tmp_path / "b", files=("DataStore.lua", "DataStore_Agenda.lua"))
    (acct / "SavedVariables" / "DataStore_Agenda.lua").write_text(
        "DataStore_Agenda_Characters = {\n{\n[\"lastUpdate\"] = 1795200000,\n},\n}\n"
        "DataStore_Agenda_SavedInstances = {\n}\nDataStore_Agenda_BossKills = {\n}\n"
        "DataStore_Agenda_Options = {\n[\"WeeklyResetHour\"] = 6,\n[\"WeeklyResetDay\"] = 2,\n}\n")
    assert run(shipper, acct) == []


def test_reputations_are_archived(shipper):
    assert shipper.ARCHIVED_SOURCES.match("Account/ACCT/SavedVariables/DataStore_Reputations.lua")
    assert shipper.ARCHIVED_SOURCES.match("Account/ACCT/SavedVariables/DataStore_Reputations.lua.bak")
    assert not shipper.ARCHIVED_SOURCES.match("Account/ACCT/SavedVariables/DataStore_Mails.lua")


def test_ingest_keeps_what_the_shipper_sends_and_screens_the_rest(shipper, tmp_path):
    sent = run(shipper, account(tmp_path))
    now = ingest.datetime(2026, 11, 22, tzinfo=ingest.UTC)
    stored, bad = ingest.parse("\n".join(json.dumps(e) for e in sent).encode(), now)
    assert bad == 0
    back = [json.loads(x) for x in stored]
    assert [{k: v for k, v in e.items() if k != "component"} for e in back] == [
        {k: json.dumps(v, separators=(",", ":")) if isinstance(v, list | dict) else v for k, v in e.items()}
        for e in sent]
    # A shipper that sent more (the guild, the friends list) has it dropped.
    extra = [{"_time": "2026-11-20T18:40:00.000Z", "feed": "wow-altoholic", "kind": "reps", "char": "Testpriest-Fixture",
              "source": "altoholic", "factions": [[72, 5, 4200]], "guild_name": "Made Up Guild"},
             {"_time": "2026-11-20T18:40:00.000Z", "feed": "wow-altoholic", "kind": "contacts",
              "char": "Testpriest-Fixture", "friends": ["Strangerfoo"]},
             {"_time": "2026-11-20T18:40:00.000Z", "feed": "wow-character", "char": "Testpriest-Fixture",
              "source": "altoholic", "money_copper": 5, "guild_rank": "Made Up Rank"}]
    stored, bad = ingest.parse("\n".join(json.dumps(e) for e in extra).encode(), now)
    got = [json.loads(x) for x in stored]
    assert bad == 0 and not any(s in json.dumps(got) for s in NEVER)
    assert got[0]["factions"] == "[[72,5,4200]]" and got[2]["money_copper"] == 5
    assert set(got[1]) == {"_time", "feed", "kind", "char", "_msg", "component"}
    assert otherplayers.ALTO_KEYS["sheet"] >= {"money_copper", "rested_xp", "bind"}
