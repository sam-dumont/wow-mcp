"""Other players' data leaves the PC only where a feature uses it (security
review H1): the shipper's combat log, Recorder and Polestar probe events,
and /ingest's screen for 0.6.x shippers that still send raw lines.

Made-up names only: "Testchar Demo" is ours, the others are strangers.
"""

import contextlib
import inspect
import io
import json

from tests.test_server import PW, FakeVL, fake_vl  # noqa: F401  (fake_vl: the fixture)
from wowstats import ingest, otherplayers, ui

OURS = "Testchar Demo-Homerealm"
GROUPED = "Othername Alpha-Fakerealm"
STRANGER = "Strangerfoo Beta-Farrealm"
# Nothing of these may be stored outside a wow-player event's `player` (and
# its `_msg`, which names the same player).
NAMES = ("Othername", "Strangerfoo")
# Never anywhere: their realms and GUIDs.
NEVER = ("Fakerealm", "Farrealm", "0AAAAAAA", "0CCCCCCC")
WOLF = "Creature-0-1-2-3-299-0000000001"

LOG = "\n".join([
    '11/17/2026 15:14:00.000-5  COMBAT_LOG_VERSION,9,ADVANCED_LOG_ENABLED,0,BUILD_VERSION,1.15.8,PROJECT_ID,2',
    '11/17/2026 15:14:00.100-5  ZONE_CHANGE,1429,"Elwynn Forest",0',
    f'11/17/2026 15:14:01.000-5  SPELL_DAMAGE,Player-9999-0BBBBBBB,"{OURS}",0x511,0x0,{WOLF},"Young Wolf",'
    '0x10a48,0x0,133,"Fireball",0x4,42,0,4,0,0,0,nil,nil,nil',
    f'11/17/2026 15:14:02.000-5  SPELL_DAMAGE,Player-9999-0AAAAAAA,"{GROUPED}",0x512,0x0,{WOLF},"Young Wolf",'
    '0x10a48,0x0,585,"Smite",0x2,30,0,2,0,0,0,nil,nil,nil',
    f'11/17/2026 15:14:03.000-5  SPELL_HEAL,Player-9999-0CCCCCCC,"{STRANGER}",0x548,0x0,'
    f'Player-9999-0CCCCCCC,"{STRANGER}",0x548,0x0,2050,"Lesser Heal",0x2,60,0,0,nil',
    f'11/17/2026 15:14:04.000-5  SWING_DAMAGE,{WOLF},"Young Wolf",0x10a48,0x0,Player-9999-0AAAAAAA,'
    f'"{GROUPED}",0x512,0x0,8,0,1,0,0,0,nil,nil,nil',
    f'11/17/2026 15:14:05.000-5  UNIT_DIED,0000000000000000,nil,0x80000000,0x80000000,Player-9999-0CCCCCCC,'
    f'"{STRANGER}",0x548,0x0',
    '11/17/2026 15:14:06.000-5  COMBATANT_INFO,Player-9999-0AAAAAAA,0,30,40,50,60,70',
    '11/17/2026 15:14:07.000-5  ENCOUNTER_START,1234,"Hogger",1,5,0',
]) + "\n"


def emitted(shipper, run) -> list[dict]:
    """What the shipper would send: Out's own lines, after its filtering."""
    out = shipper.Out(dry=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run(out)
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def assert_no_other_players(events: list[dict]) -> None:
    for ev in events:
        line = json.dumps(ev, ensure_ascii=False)
        for s in NEVER:
            assert s not in line, (s, ev)
        if ev["feed"] == "wow-player":
            assert set(ev) <= otherplayers.EVENT_BASE | otherplayers.PLAYER_KEYS | {"component"}, ev
            continue
        for s in NAMES:
            assert s not in line, (s, ev)


def combatlog_events(shipper, tmp_path, monkeypatch) -> list[dict]:
    (tmp_path / "Logs").mkdir()
    (tmp_path / "Logs" / "WoWCombatLog-111726_151400.txt").write_text(LOG)
    monkeypatch.setattr(shipper, "WOW", tmp_path)
    return emitted(shipper, lambda out: shipper.extract_combatlogs({"Testchar-Demo"}, None, {}, out))


def test_combat_log_sends_parsed_fields_and_no_other_player(shipper, tmp_path, monkeypatch):
    got = combatlog_events(shipper, tmp_path, monkeypatch)
    assert_no_other_players(got)
    cl = [e for e in got if e["feed"] == "wow-combatlog"]
    assert len(cl) == 9
    for e in cl:
        assert set(e) <= otherplayers.EVENT_BASE | otherplayers.CL_KEYS, e
        assert e["char"] == "Testchar-Demo" and e.get("zone") == ("Elwynn Forest" if e["event"] != "COMBAT_LOG_VERSION" else None)
        assert not e["_msg"][:1].isdigit()  # never the raw line
    who = [(e["event"], e.get("source_name"), e.get("dest_name")) for e in cl if "source_name" in e or "dest_name" in e]
    assert who == [("SPELL_DAMAGE", OURS, "Young Wolf"),
                   ("SPELL_DAMAGE", "another player", "Young Wolf"),
                   ("SPELL_HEAL", "another player", "another player"),
                   ("SWING_DAMAGE", "Young Wolf", "another player"),
                   ("UNIT_DIED", None, "another player")]
    assert cl[1]["_msg"] == "ZONE_CHANGE Elwynn Forest" and cl[2]["_msg"] == f"SPELL_DAMAGE {OURS} > Young Wolf"
    # The People tab keeps its players: the name, how and where; no realm.
    people = sorted((e["player"], e["context"], e.get("zone")) for e in got if e["feed"] == "wow-player")
    assert people == [("Othername-Alpha", "grouped", "Elwynn Forest"), ("Strangerfoo-Beta", "combat", "Elwynn Forest")]


def recorder_events(shipper) -> list[dict]:
    t0 = 1794900000
    lines = ["chunk\t2\t1\t1794900000\tCID", "session\t0\tCID",
             "group\t5\t3\t0\tOthername Alpha",  # a field past the spec, as a later Recorder could add
             "who\t6\tOthername Alpha\tFakerealm",  # a kind this shipper doesn't know
             "unit\t7\t299\t5\tn\thostile\t1429\t4000\t5000\tmouseover\tStrangerfoo Beta",
             "death\t8\t1429\t4000\t5000\t299\t5"]
    sessions = shipper.sessions_of({("CID", 1): (t0, lines)})
    return emitted(shipper, lambda out: shipper.extract_recorder(sessions, {}, {"CID": "Testchar-Demo"}, {}, out))


def test_recorder_sends_known_kinds_and_fields_only(shipper):
    got = recorder_events(shipper)
    assert_no_other_players(got)
    by = {e["kind"]: e for e in got}
    assert by["group"]["size"] == 3 and by["group"]["raid"] is False and "args" not in by["group"]
    assert by["group"]["_msg"] == "group\t5\t3\t0"
    assert set(by["who"]) == {"_time", "feed", "char", "kind", "source_id", "_msg"} and by["who"]["_msg"] == "who\t6"
    assert by["unit"]["npc_id"] == 299 and by["unit"]["seen_by"] == "mouseover" and by["unit"]["map_id"] == 1429
    # The deaths tool shows the record line: still the whole known record.
    assert by["death"]["_msg"] == "death\t8\t1429\t4000\t5000\t299\t5" and by["death"]["npc_id"] == 299


def probed_events(shipper, tmp_path) -> list[dict]:
    acct = tmp_path / "Account" / "123#1"
    (acct / "SavedVariables").mkdir(parents=True)
    (acct / "SavedVariables" / "Polestar.lua").write_text(
        'PolestarDB = {\n["alts"] = {\n["chars"] = {\n["Testchar Demo-Homerealm"] = {\n["name"] = "Testchar Demo",\n'
        '["seen"] = 1794900000,\n["level"] = 20,\n},\n},\n},\n["update"] = {\n["probed"] = {\n'
        f'["{GROUPED}"] = 1794900100,\n["{STRANGER}"] = 1794900200,\n}},\n}},\n}}\n')
    return emitted(shipper, lambda out: shipper.extract_polestar(acct, [], [], {}, {}, out))


def test_probed_players_send_what_the_people_tab_reads(shipper, tmp_path):
    got = probed_events(shipper, tmp_path)
    assert_no_other_players(got)
    seen = sorted((e["player"], e["context"], e["_time"]) for e in got if e["feed"] == "wow-player")
    assert seen == [("Othername-Alpha", "seen", "2026-11-17T07:21:40.000Z"),
                    ("Strangerfoo-Beta", "seen", "2026-11-17T07:23:20.000Z")]


def test_ingest_keeps_what_this_shipper_sends(shipper, tmp_path, monkeypatch):
    """The screen changes nothing the current shipper sends."""
    sent = (combatlog_events(shipper, tmp_path, monkeypatch) + recorder_events(shipper)
            + probed_events(shipper, tmp_path))
    now = ingest.datetime(2026, 11, 20, tzinfo=ingest.UTC)
    stored, bad = ingest.parse("\n".join(json.dumps(e) for e in sent).encode(), now)
    assert bad == 0
    assert [json.loads(x) for x in stored] == [{**e, "component": "wow"} for e in sent]


# What a 0.6.x shipper sent for the same data: raw lines, `args`, realms.
OLD = [
    {"_time": "2026-11-17T20:14:02.000Z", "feed": "wow-combatlog", "char": "Testchar-Demo", "event": "SPELL_DAMAGE",
     "_msg": LOG.splitlines()[3], "source_id": "cl:WoWCombatLog.txt:300", "zone": "Elwynn Forest",
     "source_name": GROUPED, "dest_name": "Young Wolf"},
    {"_time": "2026-11-17T20:14:03.000Z", "feed": "wow-combatlog", "char": "Testchar-Demo", "event": "SPELL_HEAL",
     "_msg": LOG.splitlines()[4], "source_id": "cl:WoWCombatLog.txt:500", "zone": "Elwynn Forest",
     "source_name": STRANGER, "dest_name": STRANGER},
    {"_time": "2026-11-17T20:14:01.000Z", "feed": "wow-combatlog", "char": "Testchar-Demo", "event": "SPELL_DAMAGE",
     "_msg": LOG.splitlines()[2], "source_id": "cl:WoWCombatLog.txt:100", "zone": "Elwynn Forest",
     "source_name": OURS, "dest_name": "Young Wolf"},
    {"_time": "2026-11-17T20:14:06.000Z", "feed": "wow-combatlog", "char": "Testchar-Demo", "event": "COMBATANT_INFO",
     "_msg": LOG.splitlines()[7], "source_id": "cl:WoWCombatLog.txt:900", "zone": "Elwynn Forest"},
    {"_time": "2026-11-17T20:14:00.000Z", "feed": "wow-recorder", "char": "Testchar-Demo", "kind": "who",
     "_msg": "who\t6\tOthername Alpha\tFakerealm", "source_id": "rec:CID:1:3", "args": "Othername Alpha\tFakerealm"},
    {"_time": "2026-11-17T20:14:00.000Z", "feed": "wow-recorder", "char": "Testchar-Demo", "kind": "group",
     "_msg": "group\t5\t3\t0\tStrangerfoo Beta", "source_id": "rec:CID:1:2", "size": 3, "raid": False,
     "args": "Strangerfoo Beta"},
    {"_time": "2026-11-17T20:14:00.000Z", "feed": "wow-player", "char": "Testchar-Demo", "_msg": "seen Othername-Alpha",
     "source_id": "player:probed:Othername-Alpha:1", "player": "Othername-Alpha", "player_realm": "Fakerealm",
     "context": "seen", "source": "polestar-probe", "zone": "Elwynn Forest", "map_id": 1429},
    {"_time": "2026-11-17T20:14:00.000Z", "feed": "wow-level", "char": "Testchar-Demo", "_msg": "level 3", "level": 3},
]


def test_ingest_screens_a_06_shippers_raw_lines():
    now = ingest.datetime(2026, 11, 20, tzinfo=ingest.UTC)
    stored, bad = ingest.parse("\n".join(json.dumps(e) for e in OLD).encode(), now)
    assert bad == 0
    got = [json.loads(x) for x in stored]
    assert_no_other_players(got)
    cl = {e["source_id"]: e for e in got if e["feed"] == "wow-combatlog"}
    assert cl["cl:WoWCombatLog.txt:300"]["source_name"] == "another player"
    assert cl["cl:WoWCombatLog.txt:300"]["_msg"] == "SPELL_DAMAGE another player > Young Wolf"
    assert cl["cl:WoWCombatLog.txt:300"]["zone"] == "Elwynn Forest"
    assert cl["cl:WoWCombatLog.txt:500"]["dest_name"] == "another player"
    assert cl["cl:WoWCombatLog.txt:100"]["source_name"] == OURS  # the sender's own character stays
    rec = {e["kind"]: e for e in got if e["feed"] == "wow-recorder"}
    assert rec["who"]["_msg"] == "who\t6" and "args" not in rec["who"]
    assert rec["group"]["_msg"] == "group\t5\t3\t0" and rec["group"]["size"] == 3
    player = next(e for e in got if e["feed"] == "wow-player")
    assert player["player"] == "Othername-Alpha" and "map_id" not in player and player["zone"] == "Elwynn Forest"
    assert got[-1] == {**OLD[-1], "component": "wow"}  # other feeds pass as they came


def test_ingest_endpoint_stores_the_screened_events(run_app, fake_vl):  # noqa: F811
    srv = run_app(ui.App({"BIND": "127.0.0.1", "VL_URL": fake_vl, "INGEST_PASSWORD": PW}))
    tok = {"Authorization": ui._basic("wow", PW).decode(), "Content-Type": "application/x-ndjson"}
    body = "\n".join(json.dumps(e) for e in OLD).encode()
    code, _, resp = srv.get("/ingest", headers=tok, method="POST", data=body)
    assert code == 200 and json.loads(resp) == {"accepted": len(OLD)}
    assert_no_other_players([json.loads(x) for _, _, sent in FakeVL.got for x in sent.splitlines()])


def test_the_two_copies_are_equal(shipper):
    """wowstats/otherplayers.py copies the shipper's allow lists and the
    code that applies them: a change to one is a change to both."""
    for name in ("KINDS", "REC_DERIVED", "EVENT_BASE", "ANOTHER_PLAYER", "CL_KEYS", "PLAYER_KEYS", "AFFIL_MINE"):
        assert getattr(otherplayers, name) == getattr(shipper, name), name
    for name in ("CL_LINE", "CL_GUID"):
        assert getattr(otherplayers, name).pattern == getattr(shipper, name).pattern, name
    for name in ("rec_keys", "rec_line", "cl_standard", "cl_fields"):
        assert inspect.getsource(getattr(otherplayers, name)) == inspect.getsource(getattr(shipper, name)), name
