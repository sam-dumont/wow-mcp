import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def cbor(v) -> bytes:
    """Just enough of an encoder for the shapes Auctionator writes."""

    def head(mt: int, n: int) -> bytes:
        if n < 24:
            return bytes([mt << 5 | n])
        for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([mt << 5 | ai]) + n.to_bytes(size, "big")
        raise ValueError(n)

    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, float):
        return b"\xfb" + struct.pack(">d", v)
    if isinstance(v, str):
        b = v.encode()
        return head(2, len(b)) + b  # WoW writes Lua strings as byte strings
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(cbor(x) for x in v)
    raise TypeError(v)


def lua_bytes(b: bytes) -> bytes:
    out = bytearray(b'"')
    for c in b:
        if c in (34, 92) or c < 32 or c > 126:
            out += b"\\%d" % c
        else:
            out.append(c)
    return bytes(out + b'"')


def test_cbor_roundtrip(shipper):
    v = {"l": {"2508": 135}, "h": {"2508": 200, "2509": 70000}, "a": {"2508": 3}, "m": 1.5, "x": [1, -2, True]}
    assert shipper.cbor_decode(cbor(v)) == v
    # Indefinite-length map, as some encoders write.
    assert shipper.cbor_decode(b"\xbf" + cbor("a") + cbor(1) + b"\xff") == {"a": 1}
    with pytest.raises(shipper.CborError):
        shipper.cbor_decode(b"\xa2" + cbor("a"))


def test_lua_reader_raw_keeps_bytes(shipper):
    blob = bytes(range(256))
    doc = shipper.LuaReader(b"X = {\n[\"k\"] = " + lua_bytes(blob) + b",\n}\n", raw=True).document()
    assert doc["X"][b"k"] == blob


@pytest.fixture
def wow(tmp_path, shipper, monkeypatch):
    """A fake _forever_ folder with the data addons and an Auctionator file."""
    root = tmp_path / "_forever_"
    addons = root / "Interface" / "AddOns"
    (addons / "LibForeverData" / "Data").mkdir(parents=True)
    (addons / "Polestar_QuestDB").mkdir(parents=True)
    (addons / "LibForeverData" / "Data" / "Items.lua").write_text(
        'R("item", 1, 2, [[\n\n2592\tWool Cloth;;\n4306\tSilk%3A Cloth;;\n]])\n')
    (addons / "LibForeverData" / "Data" / "Quests.lua").write_text("R(\"quest\", 1, 2, [[\n\n7\tKobold Camp Cleanup;2;1\n]])\n")
    (addons / "Polestar_QuestDB" / "Quests.lua").write_text("R(\"quests\", 1, 2, [[\n\n7\t'Wrong Name;2\n8\t'A Rogue%2Cs Deal;5\n]])\n")
    monkeypatch.setattr(shipper, "WOW", root)
    monkeypatch.setattr(shipper, "NAMES", {})
    return root


def test_names_from_data_addons(shipper, wow):
    assert shipper.names("Items") == {2592: "Wool Cloth", 4306: "Silk: Cloth"}
    # LibForeverData first; Polestar_QuestDB fills what it lacks.
    assert shipper.names("Quests") == {7: "Kobold Camp Cleanup", 8: "A Rogue,s Deal"}


def write_auctionator(acct, realm_blob: bytes, bak: bytes | None = None):
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True, exist_ok=True)
    head = b'AUCTIONATOR_CONFIG = {\n["debug"] = false,\n}\nAUCTIONATOR_PRICE_DATABASE = {\n["__dbversion"] = 8,\n'
    (sv / "Auctionator.lua").write_bytes(head + b'["PvE"] = ' + lua_bytes(realm_blob) + b",\n}\n")
    if bak is not None:
        (sv / "Auctionator.lua.bak").write_bytes(head + b'["PvE"] = ' + lua_bytes(bak) + b",\n}\n")


def test_auctionator_prices(shipper, wow, tmp_path):
    acct = tmp_path / "acct"
    db = {"2592": {"h": {"2508": 40}, "l": {"2508": 35}, "a": {"2508": 120}, "m": 35},
          "g:4306:0": {"h": {"2512": 99}, "a": {"2512": 7}, "m": 99},
          "version": 2}
    write_auctionator(acct, cbor(db), bak=cbor({"2592": {"h": {"2508": 38}, "a": {"2508": 200}, "m": 38}}))
    out = shipper.Out(dry=True)
    got = []
    out.emit = got.append
    state: dict = {}
    shipper.extract_auctionator(acct, "Hero-Test", state, out)
    by = {e["source_id"]: e for e in got}
    wool = by["price:PvE:2592:2508"]
    # Merged over the file and its .bak: lowest low, highest high, most listed.
    assert (wool["price_copper"], wool["price_high_copper"], wool["available"]) == (35, 40, 200)
    assert wool["item_name"] == "Wool Cloth" and wool["item_id"] == 2592 and wool["realm"] == "PvE"
    assert wool["rev"] == 1 and wool["feed"] == "wow-prices" and wool["char"] == "Hero-Test"
    day0 = time.mktime((2020, 1, 1, 0, 0, 0, 0, 0, -1))
    assert wool["_time"] == shipper.iso(day0 + 2508 * 86400)
    silk = by["price:PvE:g:4306:0:2512"]
    assert silk["item_id"] == 4306 and silk["price_copper"] == 99 and silk["item_name"] == "Silk: Cloth"
    assert len(got) == 2

    # Nothing new: nothing sent. A cheaper listing the same day: sent again, rev 2.
    got.clear()
    shipper.extract_auctionator(acct, "Hero-Test", state, out)
    assert got == []
    db["2592"]["l"]["2508"] = 30
    write_auctionator(acct, cbor(db))
    shipper.extract_auctionator(acct, "Hero-Test", state, out)
    assert [(e["source_id"], e["price_copper"], e["rev"]) for e in got] == [("price:PvE:2592:2508", 30, 2)]
    json.dumps(got)  # every value is JSON


def write_auctionator_realms(acct, realms: dict[str, bytes]):
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True, exist_ok=True)
    body = b"".join(b'["' + r.encode() + b'"] = ' + lua_bytes(blob) + b",\n" for r, blob in realms.items())
    (sv / "Auctionator.lua").write_bytes(b'AUCTIONATOR_PRICE_DATABASE = {\n["__dbversion"] = 8,\n' + body + b"}\n")


def test_auctionator_moving_its_realm_key_sends_nothing_again(shipper, wow, tmp_path):
    """Auctionator keys a realm "PvE Alliance" on a faction auction house and
    "PvE" on a shared one, and moved an account's prices from one key to the
    other with the same numbers: the 0.4.3 shipper sent them all again
    (2026-11-18/19, 1,347 prices). A state written by 0.4.3 (prices under the
    old key) now sends nothing for the same days, in either direction."""
    acct = tmp_path / "acct"
    db = {"2592": {"h": {"2508": 40}, "l": {"2508": 35}, "a": {"2508": 120}},
          "4306": {"h": {"2512": 99}, "a": {"2512": 7}}}
    out = shipper.Out(dry=True)
    got = []
    out.emit = got.append
    # The state as 0.4.3 left it: the prices it sent under "PvE Alliance".
    state = {"prices": {"price:PvE Alliance:2592:2508": [35, 40, 120, 1],
                        "price:PvE Alliance:4306:2512": [99, 99, 7, 1]}}
    write_auctionator_realms(acct, {"PvE": cbor(db)})
    shipper.extract_auctionator(acct, "Hero-Test", state, out)
    assert got == []
    assert state["prices"]["price:PvE:2592:2508"] == [35, 40, 120, 1]
    # Both keys in one file on a fresh state: one copy goes.
    state = {}
    write_auctionator_realms(acct, {"PvE Alliance": cbor(db), "PvE": cbor(db)})
    shipper.extract_auctionator(acct, "Hero-Test", state, out)
    assert sorted(e["source_id"] for e in got) == ["price:PvE Alliance:2592:2508", "price:PvE Alliance:4306:2512"]
    # The other way round (a shared key first, a faction key later): the same.
    got.clear()
    state = {"prices": {"price:PvE:2592:2508": [35, 40, 120, 1]}}
    write_auctionator_realms(acct, {"PvE Horde": cbor({"2592": db["2592"]})})
    shipper.extract_auctionator(acct, "Hero-Test", state, out)
    assert got == []
    # A day that moved since (a cheaper listing) is news: sent, under the key it's under now.
    db["2592"]["l"]["2508"] = 30
    write_auctionator_realms(acct, {"PvE": cbor(db)})
    state = {"prices": {"price:PvE Alliance:2592:2508": [35, 40, 120, 1],
                        "price:PvE Alliance:4306:2512": [99, 99, 7, 1]}}
    shipper.extract_auctionator(acct, "Hero-Test", state, out)
    assert [(e["source_id"], e["price_copper"]) for e in got] == [("price:PvE:2592:2508", 30)]
    # Two factions' own houses are two houses: never twins of each other.
    got.clear()
    state = {"prices": {"price:PvE Alliance:2592:2508": [30, 40, 120, 1]}}
    write_auctionator_realms(acct, {"PvE Horde": cbor({"2592": db["2592"]})})
    shipper.extract_auctionator(acct, "Hero-Test", state, out)
    assert [e["source_id"] for e in got] == ["price:PvE Horde:2592:2508"]


def recorder_chunk(seq: int, epoch: int, lines: list[str]) -> str:
    return "\n".join([f"chunk\t2\t{seq}\t{epoch}\tCID"] + lines)


def test_recorder_quest_names_and_login(shipper, wow):
    t0 = 1794900000
    chunks = {
        ("CID", 1): (t0, recorder_chunk(1, t0, ["session\t0\tCID", "accept\t10\t7", "turnin\t50\t8"]).split("\n")),
        # 4 minutes later: the next chunk of the same session.
        ("CID", 2): (t0 + 240, recorder_chunk(2, t0 + 240, ["session\t0\tCID", "text\t5\t9\tSeen Title",
                                                            "turnin\t10\t9"]).split("\n")),
        # An hour later: a new login.
        ("CID", 3): (t0 + 3600, recorder_chunk(3, t0 + 3600, ["session\t0\tCID", "xp\t5\t10\t100"]).split("\n")),
    }
    sessions = shipper.sessions_of(chunks)
    assert len(sessions) == 2
    got = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    state: dict = {}
    shipper.extract_recorder(sessions, {}, {"CID": "Hero-Test"}, state, out)
    names = {e["source_id"]: e.get("quest_name") for e in got if e["kind"] in ("accept", "turnin")}
    assert names == {"rec:CID:1:2": "Kobold Camp Cleanup", "rec:CID:1:3": "A Rogue,s Deal",
                     "rec:CID:2:3": "Seen Title"}
    logins = [e["source_id"] for e in got if e.get("login")]
    assert logins == ["rec:CID:1:1", "rec:CID:3:1"]
    # A rerun sends nothing again, and marks no new login.
    got.clear()
    shipper.extract_recorder(sessions, {}, {"CID": "Hero-Test"}, state, out)
    assert got == []


def test_gear_snapshot_on_change(shipper, wow, tmp_path, monkeypatch):
    """wow-gear: equipped items from Syndicator (index = slot + 1), item
    levels from Polestar_ItemDB, skill ranks from Polestar, tracked upgrades
    from GearQuestForever; sent again only when it changes. The files of the
    add-ons dropped on 2026-11-20 (CharacterExport-Forever, Vulcan SimBench)
    are left on disk and read by nobody: talents come from Battle.net, caps
    from the Recorder."""
    monkeypatch.setattr(shipper, "ITEMDB", {})
    db = wow / "Interface" / "AddOns" / "Polestar_ItemDB"
    db.mkdir(parents=True)
    (db / "Forever.lua").write_text(
        'DB.Register(nil, {\n[5820]="3 32 0 2 4 1 int6",\n})\n'
        'DB.RegisterLeveling({}, {\n[53]="44 40 1 40 0 0 0||744|A Quest",\n}, {\n[12998]="Magician\'s Mantle",\n'
        '}, {\n[12998]="q1031",\n})\n')
    (db / "Items.lua").write_text('DB.Register({}, {\n[1317]="17 27 0 2 2 10 spi9",\n[53]="4 1 1 1 4 0",\n})\n')
    acct = tmp_path / "acct"
    sv = acct / "SavedVariables"
    cdir = acct / "PvE" / "Hero-Test" / "SavedVariables"
    cdir.mkdir(parents=True)
    sv.mkdir(parents=True, exist_ok=True)

    def syndicator(staff: int) -> None:
        (sv / "Syndicator.lua").write_text(
            'SYNDICATOR_DATA = {\n["Characters"] = {\n["Hero Test-PvE"] = {\n'
            '["details"] = {\n["character"] = "Hero Test",\n["guild"] = "Moss Order-PvE",\n'
            '["className"] = "PRIEST",\n["race"] = "Gnome",\n["faction"] = "Alliance",\n},\n'
            '["money"] = 987654,\n["equipped"] = {\n{\n},\n{\n},\n{\n},\n'
            '{\n["itemID"] = 5820,\n["quality"] = 2,\n["itemLink"] = "|cnIQ2:|Hitem:5820:66::::::::32:::::::|h[Faerie Mantle]|h|r",\n},\n'
            '{\n["itemID"] = 53,\n["quality"] = 1,\n["itemLink"] = "|cnIQ1:|Hitem:53::|h[Neophyte\'s Shirt]|h|r",\n},\n'
            + "{\n},\n" * 11
            + f'{{\n["itemID"] = {staff},\n["quality"] = 2,\n["itemLink"] = "|h[Some Staff]|h",\n}},\n'
            + '{\n},\n{\n},\n{\n},\n{\n["itemID"] = 9999,\n["quality"] = 1,\n},\n},\n},\n},\n}\n')

    syndicator(1317)
    (sv / "Polestar.lua").write_text(
        'PolestarDB = {\n["alts"] = {\n["chars"] = {\n["Hero Test-PvE"] = {\n["name"] = "Hero Test",\n'
        '["seen"] = 1794928683,\n["profs"] = {\n[185] = 130,\n[197] = 119,\n},\n},\n},\n},\n'
        '["settings"] = {\n["gear"] = {\n["minUpgrade"] = 3,\n["spec"] = {\n["PRIEST"] = "holy",\n'
        '["MAGE"] = "fire",\n},\n},\n},\n'
        '["gearAdjust"] = {\n["PRIEST"] = {\n["shadow"] = {\n["sta"] = {\n["x"] = 1.5,\n},\n'
        '["spellHit"] = {\n["v"] = 2,\n},\n},\n},\n["MAGE"] = {\n["fire"] = {\n["int"] = {\n["x"] = 2,\n},\n},\n},\n},\n}\n')
    (cdir / "CharacterExport-Forever.lua").write_text(
        'CharacterExportForeverDBChar = {\n["professionCache"] = {\n["PvE"] = {\n["Hero"] = {\n'
        '["id:185"] = {\n["professionID"] = 185,\n["name"] = "Cooking",\n["rank"] = 120,\n["maxRank"] = 225,\n'
        '["lastUpdated"] = 1794858386,\n},\n'
        '},\n},\n},\n}\n')
    (cdir / "GearQuestForever.lua").write_text(
        'GearQuestForeverCharDB = {\n["hunts"] = {\n'
        '["gen:priest:12998:Shoulder:25:shadow:Alliance"] = {\n["status"] = "tracked",\n},\n'
        '["gen:priest:1317:MainHand:13:shadow:Alliance"] = {\n["status"] = "completed",\n},\n},\n'
        '["specPick"] = {\n["PRIEST"] = "shadow",\n},\n}\n')
    (cdir / "VulcanSimBench.lua").write_text('VulcanSimBenchCharDB = {\n["last"] = "FSX1:tal=5-0-18#00000000",\n}\n')
    got = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    state: dict = {}
    shipper.extract_gear(acct, shipper.roster(acct), state, out)
    assert len(got) == 1
    g = got[0]
    for gone in ("talents", "talent_ranks", "talent_nodes", "talents_level", "talents_date"):
        assert gone not in g
    assert g["feed"] == "wow-gear" and g["char"] == "Hero-Test" and g["_time"] == shipper.iso(1794928683)
    assert [(i["slot"], i["slot_name"], i["name"], i["item_level"], i["quality"]) for i in g["items"]] == [
        (3, "Shoulder", "Faerie Mantle", 32, 2), (4, "Shirt", "Neophyte's Shirt", 1, 1),
        (16, "Main hand", "Some Staff", 27, 2)]
    # The enchant from the link's second field: 66 on the mantle, an empty
    # field (the shirt) is none, a link without one (the staff) unknown.
    assert [i["enchant_id"] for i in g["items"]] == [66, 0, None]
    assert g["item_level"] == 29.5  # the shirt doesn't count
    # Polestar's ranks only: CharacterExport's 120/225 is not read.
    assert g["professions"] == [
        {"skill_id": 185, "name": "Cooking", "rank": 130},
        {"skill_id": 197, "name": "Tailoring", "rank": 119}]
    assert g["wanted"] == [{"item_id": 12998, "name": "Magician's Mantle", "item_level": None, "req_level": None,
                            "slot_name": "Shoulder", "level": 25}]
    assert (g["spec"], g["class"]) == ("Shadow", "PRIEST")
    # Sex and guild are Battle.net's (owner, 2026-11-20): never sent.
    assert "guild" not in g and "sex" not in g
    # Polestar's gear settings for this class only, the edits as a list
    # (VictoriaLogs would flatten a nested object).
    assert (g["polestar_spec"], g["min_upgrade"]) == ("holy", 3)
    assert g["gear_adjust"] == [{"spec": "shadow", "stat": "spellHit", "v": 2},
                                {"spec": "shadow", "stat": "sta", "x": 1.5}]
    assert "987654" not in json.dumps(got)  # never the gold


    got.clear()
    shipper.extract_gear(acct, shipper.roster(acct), state, out)
    assert got == []
    syndicator(7001)
    shipper.extract_gear(acct, shipper.roster(acct), state, out)
    assert len(got) == 1 and got[0]["source_id"] != g["source_id"]



def test_inventory_ids_on_change(shipper, tmp_path):
    """wow-bags: the item IDs in Syndicator's bags and bank (arrays of
    containers of slots, {} for an empty slot), IDs only, sent again only
    when the set changes; the gear sheet never carries them."""
    acct = tmp_path / "acct"
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True)

    def syndicator(bank_item: int) -> None:
        (sv / "Syndicator.lua").write_text(
            'SYNDICATOR_DATA = {\n["Characters"] = {\n["Hero Test-PvE"] = {\n'
            '["details"] = {\n["character"] = "Hero Test",\n["className"] = "PRIEST",\n},\n'
            '["bags"] = {\n{\n{\n["itemID"] = 6948,\n["itemCount"] = 1,\n["itemLink"] = "|h[Hearthstone]|h",\n},\n'
            '{\n},\n{\n["itemID"] = 2589,\n["itemCount"] = 12,\n},\n},\n{\n{\n["itemID"] = 2589,\n'
            '["itemCount"] = 3,\n},\n},\n},\n'
            f'["bank"] = {{\n{{\n{{\n["itemID"] = {bank_item},\n["itemCount"] = 1,\n}},\n{{\n}},\n}},\n}},\n'
            '},\n["Other One-PvE"] = {\n["details"] = {\n["character"] = "Other One",\n},\n},\n'
            # Scanned, nothing in it (a fresh character: Syndicator writes the
            # empty containers, and a never-opened bank as an empty table).
            '["Empty Bags-PvE"] = {\n["details"] = {\n["character"] = "Empty Bags",\n},\n'
            '["bags"] = {\n{\n},\n{\n},\n},\n["bank"] = {\n},\n},\n},\n}\n')

    syndicator(10413)
    assert shipper.inventory_of(acct, "Hero-Test") == {"bags": [2589, 6948], "bank": [10413]}
    got = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    state: dict = {}
    chars = {"Hero-Test": {}, "Other-One": {}, "Nobody-Here": {}, "Empty-Bags": {}}
    shipper.extract_inventory(acct, chars, state, out)
    # Other-One's bags were never scanned and Nobody-Here is unknown: no
    # event. Empty-Bags was scanned and holds nothing: an empty event, so an
    # older list doesn't stay the newest.
    assert [(e["feed"], e["char"], e["bags"], e["bank"]) for e in got] == [
        ("wow-bags", "Empty-Bags", [], []), ("wow-bags", "Hero-Test", [2589, 6948], [10413])]
    assert "itemCount" not in json.dumps(got) and "Hearthstone" not in json.dumps(got)
    got.clear()
    shipper.extract_inventory(acct, chars, state, out)
    assert got == []
    syndicator(1121)
    shipper.extract_inventory(acct, chars, state, out)
    assert len(got) == 1 and got[0]["bank"] == [1121]

def test_roster_every_character(shipper, tmp_path):
    """Characters Polestar doesn't track still get a wow-character snapshot:
    from Syndicator (race, class, sex, guild, gold) or from their WTF folder
    alone. Polestar's own characters keep Polestar's snapshots."""
    acct = tmp_path / "acct"
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True)
    (acct / "93" / "Testhunter-Fixture").mkdir(parents=True)
    (acct / "93" / "Testhunter-Fixture" / "AddOns.txt").write_text("TomTom: enabled\n")
    os.utime(acct / "93" / "Testhunter-Fixture" / "AddOns.txt", (1794100000, 1794100000))
    (sv / "Syndicator.lua").write_text(
        'SYNDICATOR_DATA = {\n["Characters"] = {\n["Sylwen Moon-PvE"] = {\n'
        '["details"] = {\n["character"] = "Sylwen Moon",\n["guild"] = "Moss Order-PvE",\n'
        '["className"] = "HUNTER",\n["race"] = "Night Elf",\n["faction"] = "Alliance",\n["sex"] = 3,\n},\n'
        '["money"] = 4242,\n["equipped"] = {\n{\n},\n'
        '{\n["itemID"] = 5820,\n["quality"] = 2,\n["itemLink"] = "|h[Faerie Mantle]|h",\n},\n},\n},\n},\n}\n')
    (sv / "Polestar.lua").write_text(
        'PolestarDB = {\n["alts"] = {\n["chars"] = {\n["Hero Test-PvE"] = {\n["name"] = "Hero Test",\n'
        '["seen"] = 1794928683,\n["level"] = 12,\n},\n},\n},\n}\n')
    chars = shipper.roster(acct)
    assert set(chars) == {"Testhunter-Fixture", "Sylwen-Moon", "Hero-Test"}
    got = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    state: dict = {}
    shipper.extract_roster(chars, state, out)
    by = {e["char"]: e for e in got}
    assert set(by) == {"Testhunter-Fixture", "Sylwen-Moon"}  # Hero-Test: Polestar's snapshots
    s = by["Sylwen-Moon"]
    assert (s["feed"], s["race"], s["class"], s["money_copper"], s["source"]) == (
        "wow-character", "Night Elf", "HUNTER", 4242, "syndicator")
    assert "sex" not in s and "guild" not in s
    g = by["Testhunter-Fixture"]
    assert g["source"] == "folder" and g["realm"] == "93" and g["_time"] == shipper.iso(1794100000)
    assert g.get("race") is None and g.get("level") is None
    got.clear()
    shipper.extract_roster(chars, state, out)
    assert got == []  # sent again only when it changes
    shipper.extract_gear(acct, chars, state, out)
    assert [(e["char"], e["items"][0]["item_id"]) for e in got] == [("Sylwen-Moon", 5820)]
    assert "sex" not in got[0] and "guild" not in got[0]


def test_no_narcissus_look(shipper, tmp_path):
    """Narcissus was dropped on 2026-11-20: the look comes from Battle.net's
    API only. A barber shop save left on disk sends nothing."""
    acct = tmp_path / "acct"
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True)
    (acct / "93" / "Testpriest-Fixture" / "SavedVariables").mkdir(parents=True)
    (sv / "Narcissus_Barbershop.lua").write_text(
        'NarciBarberShopDB = {\n["PlayerData"] = {\n["0A1B2C3D"] = {\n["playerName"] = "Testpriest",\n'
        '["SavedLooks"] = {\n[7] = {\n["male"] = {\n{\n["timeCreated"] = 1795000000,\n'
        '["data"] = {\n{\n80,\n1174,\n},\n},\n},\n},\n},\n},\n},\n},\n}\n')
    got = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    shipper.extract_roster(shipper.roster(acct), {}, out)
    assert got and all("looks" not in e for e in got)
    assert not hasattr(shipper, "narcissus_looks")


# DataStore's two files as the client writes them, in the shape Thaoky's
# source gives (DataStore/DataStore.lua, API/Utility.lua StoreToSetAndList,
# DataStore_Characters/DataStore_Characters.lua): no real file exists yet.
DATASTORE_LUA = """
DataStore_CharacterIDs = {
\t["Set"] = {
\t\t["Default.Forever.Testpriest Fixture"] = 1,
\t\t["Default.Forever.Testhunter Fixture"] = 2,
\t},
\t["List"] = {
\t\t"Default.Forever.Testhunter Fixture", -- [1] (stale order: List indexes shift, Set is the truth)
\t\t"Default.Forever.Testpriest Fixture", -- [2]
\t},
\t["Count"] = 2,
}
"""
DATASTORE_CHARACTERS_LUA = """
DataStore_Characters_Info = {
\t{
\t\t["name"] = "Testpriest Fixture",
\t\t["played"] = 412345,
\t\t["playedThisLevel"] = 5400,
\t\t["BaseInfo"] = BASEINFO,
\t\t["lastUpdate"] = 1795400000,
\t\t["lastLogoutTimestamp"] = 1795400000,
\t\t["money"] = 1234567,
\t}, -- [1]
\t{
\t\t["BaseInfo"] = 12,
\t\t["lastUpdate"] = 1795300000,
\t\t["lastLogoutTimestamp"] = 5000000000,
\t\t["played"] = 7200,
\t\t["playedThisLevel"] = 600,
\t}, -- [2]
\t{
\t\t["name"] = "Never Played",
\t\t["lastUpdate"] = 1795300000,
\t}, -- [3]
}
"""


def test_altoholic_played(shipper, tmp_path, capsys):
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True)
    assert shipper.altoholic_played(acct) == []  # no Altoholic: nothing, no error
    (sv / "DataStore.lua").write_text(DATASTORE_LUA)
    # Level 34 with class/race/sex bits above bit 6, as ScanBaseInfo packs them.
    (sv / "DataStore_Characters.lua").write_text(
        DATASTORE_CHARACTERS_LUA.replace("BASEINFO", str(34 + 9 * 128 + 7 * 2048)))
    got = {p["char"]: p for p in shipper.altoholic_played(acct)}
    assert set(got) == {"Testpriest-Fixture", "Testhunter-Fixture"}
    g = got["Testpriest-Fixture"]
    assert (g["played_total_s"], g["played_level_s"], g["level"], g["logged_out"]) == (412345, 5400, 34, True)
    # No `name`: the key from DataStore_CharacterIDs.Set (id 2), never the
    # List's index; still logged in.
    assert got["Testhunter-Fixture"]["level"] == 12 and got["Testhunter-Fixture"]["logged_out"] is False

    out = shipper.Out(dry=True)
    state = {}
    capsys.readouterr()
    roster = {"Testpriest-Fixture": {}, "Testhunter-Fixture": {}}
    shipper.extract_played(acct, roster, state, out)
    shipper.extract_played(acct, roster, state, out)  # nothing new the second time
    evs = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert len(evs) == 2
    e = next(e for e in evs if e["char"] == "Testpriest-Fixture")
    assert e["feed"] == "wow-character" and e["source"] == "altoholic" and e["played_total_s"] == 412345
    assert e["source_id"] == "played:Testpriest-Fixture:412345" and e["_time"].startswith("2026-11-23")


@pytest.mark.parametrize("link, want", [
    ("|cff1eff00|Hitem:2041:41:0:0:0:0:0:0:20|h[Tunic of Westfall]|h|r", 41),
    ("|cnIQ2:|Hitem:5820:66::::::::32:::::::|h[Faerie Mantle]|h|r", 66),
    ("|cnIQ1:|Hitem:53::|h[Neophyte's Shirt]|h|r", 0),
    ("|cnIQ1:|Hitem:53:0:0:0|h[Neophyte's Shirt]|h|r", 0),
    ("item:6070:247", 247),
    ("|h[Some Staff]|h", None),
    ("", None),
    (None, None),
])
def test_link_enchant(shipper, link, want):
    assert shipper.link_enchant(link) == want

# Altoholic 1.60.005 (Altoholic_Forever) as the Forever client saved it on
# 2026-11-18, cut down to what the shipper reads: tests/data/altoholic-forever.
# The layout, keys, levels, times and these two characters' /played are the
# real ones; the guild's name, the GUIDs, money, zones and XP are replaced or
# gone.
ALTOHOLIC_FOREVER = Path(__file__).parent / "data" / "altoholic-forever"


def test_altoholic_real_forever_saves(shipper, tmp_path, capsys):
    import shutil

    acct = tmp_path / "WTF" / "Account" / "ACCT"
    shutil.copytree(ALTOHOLIC_FOREVER, acct / "SavedVariables")
    got = {p["char"]: p for p in shipper.altoholic_played(acct)}
    # Keys are "Default.<GetRealmName()>.<First Last>"; the guild key, in its
    # own table, is never a character.
    assert set(got) == {"Testpriest-Fixture", "Testhunter-Fixture"}
    g = got["Testpriest-Fixture"]
    assert (g["played_total_s"], g["played_level_s"], g["level"], g["logged_out"]) == (168240, 516, 32, True)
    # Testhunter: level 1, 2 s played, saved 37 s after Testpriest: a character made
    # that evening, not a broken save (the server still checks it against
    # what it already knows: views.played_total).
    assert (got["Testhunter-Fixture"]["played_total_s"], got["Testhunter-Fixture"]["level"]) == (2, 1)
    # Only the roster's characters are ever sent.
    assert [p["char"] for p in shipper.altoholic_played(acct, {"Testpriest-Fixture"})] == ["Testpriest-Fixture"]
    out = shipper.Out(dry=True)
    capsys.readouterr()
    shipper.extract_played(acct, {"Testpriest-Fixture": {}}, {}, out)
    evs = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [e["char"] for e in evs] == ["Testpriest-Fixture"]
    # DataStore_Talents on Forever: no trees or ranks (only empty Glyphs),
    # and the "spec" it keeps is the class name. The shipper doesn't read it.
    tal = shipper.read_sv_versions(acct / "SavedVariables" / "DataStore_Talents.lua")[0]
    chars = shipper._by_id(tal["DataStore_Talents_Characters"])
    assert len(chars) == 2 and all(set(c) == {"Glyphs"} for c in chars.values())
    # Specializations[id]: spec index (bits 0-2), role (3-4), name id (5+).
    names = shipper._by_id(tal["DataStore_Talents_SpecializationInfos"]["Names"]["List"])
    specs = shipper._by_id(tal["DataStore_Talents_Specializations"])
    assert [names[v >> 5] for _, v in sorted(specs.items())] == ["Priest", "Hunter"]
    assert "DataStore_Talents" not in json.dumps(shipper.gear_of(acct, acct / "x", "Testpriest-Fixture") or {})


def test_mac_decomposed_folder_names_still_ship_levels_and_dungeons(shipper, tmp_path):
    """The Mac client's character folders can come back decomposed (NFD: "E"
    + a combining accent). The levels and dungeons took the raw folder name,
    which valid_char refuses: a friend's first upload had his character and
    nothing else (2026-11-20). Files as the client writes them: CRLF."""
    import unicodedata

    wtf = tmp_path / "WTF"
    acct = wtf / "Account" / "123456789#1"
    folder = unicodedata.normalize("NFD", "Éloïse-Dûrand")
    cdir = acct / "Pyrewood Village" / folder / "SavedVariables"
    cdir.mkdir(parents=True)
    (acct / "SavedVariables").mkdir()

    def crlf(t: str) -> bytes:
        return t.replace("\n", "\r\n").encode()

    (acct / "SavedVariables" / "Polestar.lua").write_bytes(crlf(
        'PolestarDB = {\n["alts"] = {\n["chars"] = {\n["Éloïse Dûrand-Pyrewood Village"] = {\n'
        '["name"] = "Éloïse Dûrand",\n["seen"] = 1795600000,\n["level"] = 33,\n},\n},\n},\n}\n'))
    splits = "".join(f'[{lv}] = {{\n["seconds"] = 1800,\n["quests"] = 5,\n["kills"] = 20,\n'
                     f'["at"] = {1794000000 + lv * 20000},\n}},\n' for lv in range(1, 33))
    (cdir / "Polestar.lua").write_bytes(crlf(
        'PolestarCharDB = {\n["qol"] = {\n["xp"] = {\n["splits"] = {\n' + splits + '},\n},\n},\n'
        '["instanceRuns"] = {\n{\n["id"] = 389,\n["name"] = "Ragefire Chasm",\n["kind"] = "party",\n'
        '["entered"] = 1794500000,\n["left"] = 1794502400,\n["time"] = 2400,\n["level"] = 15,\n}, -- [1]\n},\n}\n'))
    got = []
    out = shipper.Out(dry=True)
    out.emit = lambda ev, emit=out.emit: (got.append(ev), emit(ev))
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        shipper.extract_tree(wtf, {}, out)
    assert out.skipped == 0
    assert out.counts == {"wow-character": 1, "wow-level": 32, "wow-dungeon": 1}
    assert {e["char"] for e in got} == {"Éloïse-Dûrand"}  # one character, composed (NFC)
    assert shipper.our_characters(wtf)[0] == {"Éloïse-Dûrand"}


def wse_lua(entries: list[dict], inspect_name: str = "Otherplayer") -> str:
    """A WowSimsExporter.lua shaped as the client writes it (AceDB, v3.2.11 on
    Forever, 2026-11-20): global (other players' data the shipper must never
    send), profileKeys, profiles.<profile>.savedCharacters[i] = {timestamp,
    dateString, data = JSON string}; `name` only when the entry has one."""
    def lua_str(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

    items = []
    for i, e in enumerate(entries, 1):
        name = f'\t\t\t\t\t["name"] = {lua_str(e["name"])},\n' if e.get("name") else ""
        items.append(f'\t\t\t\t[{i}] = {{\n\t\t\t\t\t["timestamp"] = {e["timestamp"]},\n'
                     f'\t\t\t\t\t["dateString"] = "2026-11-20 11:42:07",\n{name}'
                     f'\t\t\t\t\t["data"] = {lua_str(json.dumps(e["data"]))},\n\t\t\t\t}},\n')
    return ("\nWSEDB = {\n"
            '\t["global"] = {\n\t\t["inspectHistory"] = {\n\t\t\t{\n'
            f'\t\t\t\t["name"] = "{inspect_name}",\n\t\t\t\t["talents"] = "5555",\n\t\t\t}},\n\t\t}},\n'
            f'\t\t["groupSync"] = {{\n\t\t\t["{inspect_name}"] = {{ ["level"] = 30 }},\n\t\t}},\n'
            '\t\t["tutorialSeen"] = true,\n\t},\n'
            '\t["profileKeys"] = {\n\t\t["Testpriest - Forever"] = "Default",\n\t},\n'
            '\t["profiles"] = {\n\t\t["Default"] = {\n\t\t\t["showAutoSaveMessages"] = true,\n'
            '\t\t\t["savedCharacters"] = {\n' + "".join(items) + "\t\t\t},\n\t\t},\n\t},\n}\n")


def wse_data(talents: str, level: int, cls: str = "priest", **kw) -> dict:
    """The `data` JSON as ExportStructures/Character.lua fills it (v3.2.11 keys)."""
    return {"class": cls, "flavor": "forever", "id": "Player-1-0A2B3C4D", "level": level, "race": "Gnome",
            "spec": "holy_disc", "talents": talents, "unit": "player", "version": "3.2.11",
            "professions": [{"name": "Tailoring", "level": 120}],
            "gear": {"items": [None, {"id": 6096}] + [None] * 15}, **kw}


def test_wowsims_talents_on_change(shipper, tmp_path, capsys):
    """WoWSims Exporter keeps only each character's latest save: one
    wow-talents event per change of build or level, nothing for an unchanged
    file, never the other players in its `global` table."""
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True)
    roster = {"Testpriest-Fixture": {"class": "PRIEST"}, "Testhunter-Fixture": {"class": "HUNTER"}}
    out, state = shipper.Out(dry=True), {}
    shipper.extract_talents(acct, roster, state, out)  # no addon: nothing, no error
    assert capsys.readouterr().out == ""

    def save(talents, level, ts):
        (sv / "WowSimsExporter.lua").write_text(wse_lua([
            # Newer addons write the character's own "Firstname Surname"...
            {"timestamp": ts, "name": "Testpriest-Forever", "data": wse_data(talents, level, name="Testpriest Fixture")},
            # ...older ones only the entry's UnitName-realm: one hunter called Testhunter.
            {"timestamp": 1795000000, "name": "Testhunter-Forever", "data": wse_data("05", 12, cls="hunter")},
            # A name no character of this account has: skipped, never guessed.
            {"timestamp": 1795000000, "name": "Nobody-Forever", "data": wse_data("5", 11)},
        ]))

    save("02500420103--00502", 33, 1795000000)
    shipper.extract_talents(acct, roster, state, out)
    shipper.extract_talents(acct, roster, state, out)  # unchanged file: nothing more
    save("02500420103--00502", 33, 1795003600)  # saved again, same build (a gear change): nothing
    shipper.extract_talents(acct, roster, state, out)
    save("02500420103--00503", 34, 1795090000)  # a level and a point
    shipper.extract_talents(acct, roster, state, out)
    printed = capsys.readouterr().out
    evs = [json.loads(x) for x in printed.splitlines() if x.startswith("{")]
    assert sorted((e["char"], e["talents"], e["level"]) for e in evs) == [
        ("Testhunter-Fixture", "05", 12), ("Testpriest-Fixture", "02500420103--00502", 33),
        ("Testpriest-Fixture", "02500420103--00503", 34)]
    e = evs[-1]
    assert e["feed"] == "wow-talents" and e["source"] == "wowsims" and e["addon_version"] == "3.2.11"
    assert len({x["source_id"] for x in evs}) == 3
    # The client's .bak (the save before) is read every run, never sent again.
    (sv / "WowSimsExporter.lua.bak").write_text(wse_lua([
        {"timestamp": 1795000000, "name": "Testpriest-Forever",
         "data": wse_data("02500420103--00502", 33, name="Testpriest Fixture")}]))
    shipper.extract_talents(acct, roster, state, out)
    shipper.extract_talents(acct, roster, state, out)
    assert not [x for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    # Only the saved characters: no other player, no inspect or group data, no gear.
    assert "Otherplayer" not in printed and "5555" not in printed and "6096" not in printed


def test_wowsims_ambiguous_first_name_is_skipped(shipper, tmp_path, capsys):
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    (acct / "SavedVariables").mkdir(parents=True)
    (acct / "SavedVariables" / "WowSimsExporter.lua").write_text(wse_lua([
        {"timestamp": 1795000000, "name": "Twin-Forever", "data": wse_data("5", 11)}]))
    roster = {"Twin-Fixture": {"class": "PRIEST"}, "Twin-Otherhall": {"class": "PRIEST"}}
    shipper.extract_talents(acct, roster, {}, shipper.Out(dry=True))
    assert not [x for x in capsys.readouterr().out.splitlines() if x.startswith("{")]


def test_wowsims_talents_check_counts_only(shipper, tmp_path):
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    (acct / "SavedVariables").mkdir(parents=True)
    (acct / "SavedVariables" / "WowSimsExporter.lua").write_text(wse_lua([
        {"timestamp": 1795000000, "name": "Testpriest-Forever", "data": wse_data("5", 11)},
        {"timestamp": 1795000000, "name": "Nobody-Forever", "data": wse_data("5", 11)}]))
    got = shipper.wowsims_counts(acct, {"Testpriest-Fixture": {"class": "PRIEST"}})
    assert got == {"file": 1, "profiles": 1, "entries": 2, "readable": 2, "with a full name": 0, "matched": 1,
                   "not matched": 1, "characters on this account": 1}
    assert all(isinstance(v, int) for v in got.values())  # counts only: no name, no build


def test_never_the_real_wow_folder(shipper, tmp_path):
    """2026-11-20: an agent's check read the owner's own WoW folder by default.
    Under the tests (NO_REAL_WOW=1) the default is a temp path, configure()
    refuses anything else, and --talents-check needs a person at a terminal."""
    assert shipper.SANDBOX and all(shipper.in_temp(c) for c in shipper.WOW_CANDIDATES)
    # Outside every temp directory, and nothing there: the guards refuse before any read.
    outside = Path.home() / "wow-stats-test-no-such-folder" / "_forever_"
    assert not outside.exists() and not shipper.in_temp(outside)
    real = tmp_path / "c.toml"
    real.write_text(f'wow_dir = "{outside.as_posix()}"\n')

    def run(env, *args):
        return subprocess.run([sys.executable, str(ROOT / "shipper" / "wow_stats.py"), *args], env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)

    # Under the tests: refused, whatever the command.
    r = run({**os.environ, "WOW_STATS_CONFIG": str(real)}, "--dry-run")
    assert r.returncode != 0 and "NO_REAL_WOW=1: refusing the WoW folder" in r.stderr and r.stdout == ""
    # Outside the tests, --talents-check still needs a person at a terminal.
    env = {k: v for k, v in os.environ.items() if k != "NO_REAL_WOW"}
    r = run({**env, "WOW_STATS_CONFIG": str(real)}, "--talents-check")
    assert r.returncode == 2 and "run it yourself, in a terminal" in r.stderr and r.stdout == ""
