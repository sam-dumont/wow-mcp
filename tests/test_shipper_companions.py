"""Local combat companion capture. All logs and cache records are synthetic."""

import json
import struct
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


def player(name, flags="0x511"):
    return f'Player-9-ABCDE,"{name}",{flags},0x0'


def unit(npc, flags="0x1111", prefix="Pet"):
    return f'{prefix}-0-9-1-2-{npc}-ABCDE,"Private nickname",{flags},0x0'


TARGET = unit(300, "0xa48", "Creature")


def line(sec, source="", dest="", event="SWING_DAMAGE"):
    return f"11/17/2026 15:14:{sec:02d}.000+0  {event},{source},{dest}\n"


def record(npc, *models):
    name = b"Fixture\0"
    payload = bytes(4) + bytes([len(name)]) + bytes(10) + name + bytes(26)
    payload += struct.pack("<If", len(models), float(len(models)))
    payload += b"".join(struct.pack("<Iff", display, 1., 1.) for display in models)
    return struct.pack("<II", npc, len(payload)) + payload


HEADER = struct.pack("<4sI4sIII", b"BOMW", 71139, b"SUne", 688, 4, 0)


@pytest.fixture
def capture(shipper, tmp_path, monkeypatch):
    monkeypatch.setattr(shipper, "WOW", tmp_path)
    log = tmp_path / "Logs" / "WoWCombatLog.txt"
    log.parent.mkdir()
    cache = tmp_path / "Cache" / "WDB" / "enUS" / "creaturecache.wdb"
    cache.parent.mkdir(parents=True)
    state = {"cl_classes": {"Arrow": "hunter", "Ember": "warlock", "Faith": "priest"}}
    events = []

    def poll(rows="", only=False):
        if rows:
            with log.open("a") as f:
                f.write(rows)
        events.clear()
        shipper.extract_combatlogs({"Arrow"} if only else set(state["cl_classes"]), "Arrow", state,
                                   SimpleNamespace(emit=events.append))
        return [e for e in events if e["feed"] == "wow-companion"]

    return poll, log, cache, state, events


def test_companion_requires_a_verified_owner_and_own_pet_flags(capture):
    poll, _, cache, _, _ = capture
    cache.write_bytes(HEADER + record(10, 910) + bytes(8))
    assert poll(line(1, unit(10), TARGET), only=True) == []
    assert poll(line(2, player("Arrow"), TARGET)
                + line(3, unit(10, "0x1112"), TARGET)  # another player's pet
                + line(4, TARGET, unit(10, "0x2111"))  # guardian
                + line(5, player("Someone", "0x512"), unit(1860, "0x1112"), "SPELL_SUMMON")) == []
    pet, = poll(line(6, TARGET, unit(10)))
    assert (pet["char"], pet["kind"], pet["display_id"]) == ("Arrow", "hunter", 910)
    assert set(pet) == {"feed", "char", "kind", "display_id", "_time", "source_id", "_msg"}
    assert all(s not in json.dumps(pet) for s in ("Private nickname", "ABCDE", "Pet-"))
    assert poll(line(7, player("Faith"), unit(11)) + line(8, unit(11), TARGET)) == []
    assert poll(line(9, event="COMBAT_LOG_VERSION") + line(10, unit(12), TARGET), only=True) == []


def test_own_warlock_summon_identifies_creature_with_unknown_class(capture):
    poll, _, cache, state, _ = capture
    del state["cl_classes"]["Ember"]
    cache.write_bytes(HEADER + record(1860, 1132) + bytes(8))
    other = player("Someone", "0x512")
    dest = unit(1860, "0xa28", "Creature")
    assert poll(line(1, player("Arrow"), TARGET)
                + line(2, other, dest, "SPELL_SUMMON")
                + line(3, player("Ember"), unit(500, "0xa28", "Creature"), "SPELL_SUMMON")) == []
    pet, = poll(line(4, player("Ember"), dest, "SPELL_SUMMON"))
    assert (pet["char"], pet["kind"], pet["display_id"]) == ("Ember", "warlock", 1132)


def test_hunter_class_comes_from_local_roster(capture, shipper):
    poll, _, cache, state, events = capture
    state.clear()
    wtf = shipper.WOW / "WTF"
    sv = wtf / "Account" / "Fixture" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "Syndicator.lua").write_text(
        'SYNDICATOR_DATA = { Characters = { [1] = { details = { character = "Arrow", className = "HUNTER" } } } }')
    shipper.extract_tree(wtf, state, SimpleNamespace(emit=events.append))
    cache.write_bytes(HEADER + record(10, 910) + bytes(8))
    pet, = poll(line(1, player("Arrow"), unit(10)))
    assert (pet["char"], pet["kind"], pet["display_id"]) == ("Arrow", "hunter", 910)


def test_missing_or_ambiguous_skin_clears_pet_then_retries_at_observation_time(capture, shipper, monkeypatch):
    poll, _, cache, _, _ = capture
    cache.write_bytes(HEADER + record(10, 910) + record(11, 911, 912) + bytes(8))
    first, = poll(line(1, player("Arrow"), unit(10)))
    unknown, = poll(line(1, unit(11), TARGET))
    assert [first["display_id"], unknown["display_id"]] == [910, 0]
    assert datetime.fromisoformat(unknown["_time"]) - datetime.fromisoformat(first["_time"]) == timedelta(microseconds=1)
    with monkeypatch.context() as patch:
        def unexpected(*args):
            pytest.fail("unchanged cache read again")
        patch.setattr(shipper, "creature_displays", unexpected)
        assert poll() == []
    missing, = poll(line(2, unit(12), TARGET))
    assert missing["display_id"] == 0
    cache.write_bytes(HEADER + record(12, 912) + bytes(8))
    resolved, = poll()
    assert resolved["display_id"] == 912 and resolved["_time"] == missing["_time"]
    assert missing["source_id"] != resolved["source_id"]
    cache.unlink()
    assert poll(line(3, unit(12), TARGET)) == []


def test_cache_rejects_malformed_records_and_conflicting_models(shipper):
    row = record(10, 910)
    assert shipper.creature_displays(HEADER + row + bytes(8), {10}) == {10: 910}
    later_build = HEADER[:4] + struct.pack("<I", 72000) + HEADER[8:]
    assert shipper.creature_displays(later_build + row + bytes(8), {10}) == {10: 910}
    weightless = bytearray(row)
    weightless[-16:-12] = weightless[-4:] = bytes(4)
    assert shipper.creature_displays(HEADER + weightless, {10}) == {10: 910}
    assert shipper.creature_displays(HEADER + record(10, 0), {10}) == {10: 0}
    for data in [b"X" + HEADER[1:] + row, HEADER[:4] + bytes(4) + HEADER[8:] + row,
                 HEADER[:12] + bytes(4) + HEADER[16:] + row, HEADER + row[:-1], HEADER + row + b"x",
                 HEADER + row + struct.pack("<II", 15, 99)]:
        assert shipper.creature_displays(data, {10}) == {}
    for payload in [b"", row[8:13] + b"X" + row[14:], row[8:].replace(b"Fixture\0", b"Fixtures"),
                    row[8:-12], row[8:-4] + struct.pack("<f", float("nan"))]:
        assert shipper.creature_display(payload) == 0
    assert shipper.creature_displays(HEADER + row + record(10, 900), {10}) == {10: 0}


def test_upgrade_reads_only_newest_tail_and_rolls_keep_latest(capture, shipper, monkeypatch):
    poll, log, cache, state, events = capture
    cache.write_bytes(HEADER + record(10, 910) + record(11, 911) + bytes(8))
    prefix = line(1, player("Arrow"), unit(10))
    log.write_text(prefix)
    fs = {}
    shipper.cl_append(log, fs)  # old shipper's cursor, plus a fallback 'char'
    state["cl"] = {log.name: {**fs, "char": "Ember"}}
    pet, = poll()
    assert (pet["char"], pet["display_id"]) == ("Arrow", 910)
    assert events == [pet]  # the existing feeds didn't replay
    newer, = poll(line(2, unit(11), TARGET))
    assert newer["display_id"] == 911
    log.rename(log.with_name("WoWCombatLog-previous.txt"))
    assert poll(prefix) == []  # rolled content, same inode replaced by other old lines

    # A fallback stored by an earlier shipper is still no proof of ownership
    # when the only player evidence starts outside the migration window.
    state.clear()
    state.update(cl_classes={"Arrow": "hunter"}, cl={log.name: {**fs, "char": "Arrow"}})
    log.write_text(prefix + line(3, unit(11), TARGET))
    fs = state["cl"][log.name]
    shipper.cl_append(log, fs)
    monkeypatch.setattr(shipper, "COMPANION_TAIL", len(line(3, unit(11), TARGET)) + 1)
    log.with_name("WoWCombatLog-previous.txt").unlink()
    assert poll() == []
