"""Trim, pack and fetch the demo store. All test models use invented game files."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from wowstats import character3d, db2, redact

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "fixtures"))

import demo3d  # noqa: E402
import golden3d  # noqa: E402

OUTFIT = [{"slot": 1, "item_id": 7048}, {"slot": 3, "item_id": 6685}, {"slot": 5, "item_id": 273044},
          {"slot": 6, "item_id": 7000}, {"slot": 15, "item_id": 273045}, {"slot": 16, "item_id": 2816},
          {"slot": 17, "item_id": 279838}, {"slot": 18, "item_id": 7001}]


def _dump(x) -> str:
    return json.dumps(x, sort_keys=True, default=list)


def test_trim_keeps_the_recipes(tmp_path):
    g = golden3d.build()
    rows = {n: g["tables"].get(n, {}) for n in character3d.TABLES}
    sheets = {demo3d.NIBBET: [OUTFIT] + [[i] for i in OUTFIT]}
    looks = {demo3d.NIBBET.name: g["look"]}
    full = demo3d.answers(character3d.Tables(rows, golden3d.BUILD), looks, sheets)
    small = demo3d.trim(rows, looks, sheets)
    # Fewer rows (the test helm and shield, the items he doesn't wear here)...
    assert sum(map(len, small.values())) < sum(map(len, rows.values()))
    assert golden3d.TEST_HELM not in small["Item"]
    # ... and the same answers, through the WDC5 files the store keeps.
    (tmp_path / "build.json").write_text(json.dumps({"build": golden3d.BUILD}))
    for name, r in small.items():
        (tmp_path / f"{name}.db2").write_bytes(golden3d.wdc5(name, r))
    again = demo3d.answers(character3d.load_tables(tmp_path), looks, sheets)
    assert _dump(again) == _dump(full)


def test_trim_keeps_the_spell_and_missile_of_each_recorded_rank(tmp_path):
    g = golden3d.build()
    rows = {n: g["tables"].get(n, {}) for n in character3d.TABLES}
    # A different missile for each rank. The level 8 gnome has previously
    # been level 4. The third missile belongs to a rank she's never reached.
    spells = (116, 205, 837)
    for name, subset in {
        "SpellLevels": {i: {"SpellID": s, "SpellLevel": i * 4} for i, s in enumerate(spells, 1)},
        "SpellMisc": {i: {"SpellID": s} for i, s in enumerate(spells, 1)},
        "SpellXSpellVisual": {i: {"SpellID": s, "SpellVisualID": 20 + i, "Probability": 1}
                              for i, s in enumerate(spells, 1)},
        "SpellVisual": {20 + i: {"SpellVisualMissileSetID": 30 + i} for i in (1, 2, 3)},
        "SpellVisualMissile": {i: {"SpellVisualMissileSetID": 30 + i, "SpellVisualEffectNameID": 40 + i}
                               for i in (1, 2, 3)},
        "SpellVisualEffectName": {40 + i: {"ModelFileDataID": 50 + i, "Scale": 1, "Alpha": 1} for i in (1, 2, 3)},
    }.items():
        # The reader supplies the other fields' zero values and the row ID.
        rows[name] = db2.Table(golden3d.wdc5(name, subset), name, golden3d.BUILD,
                               columns=character3d.TABLES[name]).rows
    mage = demo3d.Profile("Test mage", "Gnome", 0, "MAGE", levels=(4, 8))
    looks, sheets = {mage.name: g["look"]}, {mage: [[]]}
    trimmed = demo3d.trim(rows, looks, sheets)
    assert set(trimmed["SpellVisualEffectName"]) == {41, 42}
    for name, subset in trimmed.items():
        (tmp_path / f"{name}.db2").write_bytes(golden3d.wdc5(name, subset))
    answers = demo3d.answers(character3d.load_tables(tmp_path, golden3d.BUILD), looks, sheets)
    shots = [(a["recipe"]["actions"][0], set(a["needs"])) for a in answers if "recipe" in a]
    assert sorted(action["rank"] for action, _ in shots) == [1, 1, 2, 2]  # both model styles
    for action, needs in shots:
        assert action["missiles"][0]["model"] == 50 + action["rank"]
        assert action["missiles"][0]["model"] in needs
        # Both published tiers must preserve the spell while dropping unrelated
        # fields even in nested client records. A missing rotation/animation
        # is distinct from an explicitly requested zero-valued animation.
        missile = action["missiles"][0]
        source = {**action, "private": "account", "missiles": [{**missile, "private": "account"}]}
        projectile = {**missile, "textures": {2: 500, "note": "account", "4": "account"}}
        with_ammo = {**action, "missiles": [projectile]}
        for tier in (redact.public, redact.zogzog):
            saved, saved_ammo = tier("model3d", {"recipe": {"actions": [source, with_ammo]}})["recipe"]["actions"]
            assert saved == action
            assert saved_ammo["missiles"][0]["textures"] == {"2": 500}


def test_trim_keeps_only_the_riders_mount_and_its_skin():
    golden = golden3d.build()
    rows = {n: golden["tables"].get(n, {}) for n in character3d.TABLES}
    rider = demo3d.Profile("Test rider", "Gnome", 0, "PRIEST", mount_spell=13819)
    for mount_id, spell, display in [(41, 13819, 8469), (105, 23251, 14575)]:
        rows["Mount"][mount_id] = {"SourceSpellID": spell, "Name_lang": f"Mount {mount_id}"}
        rows["MountXDisplay"][display] = {"MountID": mount_id, "CreatureDisplayInfoID": display}
        rows["CreatureDisplayInfo"][display] = {"ModelID": 1201, "TextureVariationFileDataID": [1302, 0, 0, 0]}
    rows["CreatureModelData"][1201] = {"FileDataID": 1301}
    looks, sheets = {rider.name: golden["look"]}, {rider: [[]]}
    kept = demo3d.trim(rows, looks, sheets)
    assert set(kept["Mount"]) == {41}
    assert set(kept["MountXDisplay"]) == {8469}
    assert 14575 not in kept["CreatureDisplayInfo"]
    for result in demo3d.answers(character3d.Tables(kept), looks, sheets):
        if "recipe" in result:
            assert result["recipe"]["mounts"] == [
                {"name": "Mount 41", "model": 1301, "scale": 1, "textures": {"11": 1302}}]
            assert {1301, 1302} <= set(result["needs"])


def test_trim_keeps_second_character_and_companion(tmp_path, monkeypatch):
    g = golden3d.build()
    rows = {n: g["tables"].get(n, {}) for n in character3d.TABLES}
    hunter = demo3d.Profile("Test hunter", "Dwarf", 1, "HUNTER", (1, 822))
    # The two races share only the atlas layout. The pet has its own model
    # and skin, and the hunter has a different option from the gnome.
    rows["ChrRaces"][3] = {"ClientFileString": "Dwarf", "Name_lang": "Dwarf"}
    rows["ChrRaceXChrModel"][1000] = {"ChrRacesID": 3, "ChrModelID": 1001, "Sex": 1}
    rows["ChrModel"][1001] = {"Sex": 1, "DisplayID": 1100, "CharComponentTextureLayoutID": golden3d.LAYOUT}
    rows["CreatureDisplayInfo"][1100] = {"ModelID": 1200}
    rows["CreatureModelData"][1200] = {"FileDataID": 1300}
    rows["CreatureDisplayInfo"][822] = {"ModelID": 1201, "TextureVariationFileDataID": [1302, 0, 0, 0]}
    rows["CreatureModelData"][1201] = {"FileDataID": 1301}
    rows["ChrCustomizationOption"][1400] = {"ChrModelID": 1001, "Name_lang": "Skin Color"}
    rows["ChrCustomizationChoice"][1401] = {"ChrCustomizationOptionID": 1400}
    full = character3d.Tables(rows, golden3d.BUILD)
    sheets = {demo3d.NIBBET: [[i for i in OUTFIT if i["item_id"] != 7048]], hunter: [[]]}
    looks = {p.name: demo3d.pick_look(full, p) for p in sheets}
    assert looks[hunter.name] == [{"optionId": 1400, "choiceId": 1401}]
    trimmed = demo3d.trim(rows, looks, sheets)
    g["tables"] = trimmed
    golden3d.write_store(tmp_path, g)
    # Reuse generated bytes at distinct IDs, as two client model rows would.
    files = tmp_path / "files"
    (files / "1300.m2").write_bytes(g["files"][golden3d.CHAR_M2])
    (files / "1301.m2").write_bytes(g["files"][golden3d.CHAR_M2])
    skin = files / "1302.blp"
    skin.write_bytes(golden3d.skin_texture((140, 105, 80)))
    monkeypatch.setattr(demo3d, "roster_outfits", lambda: sheets)
    meta = {"version": 1, "build": golden3d.BUILD, "looks": looks}
    (tmp_path / "roster.json").write_text(json.dumps(meta))
    assert demo3d.read_looks(tmp_path) == looks
    again = demo3d.answers(character3d.load_tables(tmp_path / "tables"), looks, sheets)
    assert again == demo3d.answers(full, looks, sheets)
    assert any(a.get("recipe", {}).get("companion") and {1300, 1301, 1302} <= set(a["needs"]) for a in again)
    skin.unlink()
    with pytest.raises(ValueError, match=r"missing client files \[1302\]"):
        demo3d.read_looks(tmp_path)
    looks[hunter.name] = looks[demo3d.NIBBET.name]
    (tmp_path / "roster.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="Test hunter needs choices for its own model"):
        demo3d.read_looks(tmp_path)


def test_pack_is_the_same_bytes(tmp_path):
    store = tmp_path / "store"
    (store / "files").mkdir(parents=True)
    (store / "files" / "1.blp").write_bytes(b"BLP2" + bytes(12))
    (store / "look.json").write_text("[]\n")
    a = demo3d.pack(store, tmp_path / "a.tar.gz")
    (store / "files" / "1.blp").touch()  # a newer time changes nothing
    b = demo3d.pack(store, tmp_path / "b.tar.gz")
    assert a == b
    assert (tmp_path / "a.tar.gz.sha256").read_text().split()[0] == a


def _fetch(tmp_path, url: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "site" / "scripts" / "demo3d-asset.sh"), str(tmp_path / "dest")],
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "DEMO_3D_URL": url},
                          capture_output=True, text=True, timeout=60)


def test_missing_archive_stops_the_build(tmp_path):
    r = _fetch(tmp_path, f"file://{tmp_path}/none.tar.gz")
    assert r.returncode != 0 and r.stdout == ""
    assert "DEMO_3D_STORE" in r.stderr


def test_another_archive_is_refused(tmp_path):
    store = tmp_path / "store"
    (store / "tables").mkdir(parents=True)
    (store / "tables" / "build.json").write_text("{}")
    (store / "look.json").write_text("[]")
    demo3d.pack(store, tmp_path / "other.tar.gz")
    r = _fetch(tmp_path, f"file://{tmp_path}/other.tar.gz")
    assert r.returncode != 0 and r.stdout == ""
    assert "SHA-256" in r.stderr
    assert not list((tmp_path / "dest").iterdir())


def test_the_pin_is_a_sha256():
    import roster_asset

    digest, name = roster_asset.PIN.read_text().split()
    assert len(digest) == 64 and int(digest, 16) >= 0 and name == demo3d.ROSTER_ARCHIVE


def test_verified_roster_fetch_is_reusable_offline(tmp_path, monkeypatch):
    import roster_asset

    store = tmp_path / "store"
    (store / "tables").mkdir(parents=True)
    (store / "tables" / "build.json").write_text('{"build":"test"}')
    (store / "roster.json").write_text('{"version":1,"build":"test","looks":{}}')
    archive = tmp_path / demo3d.ROSTER_ARCHIVE
    digest = demo3d.pack(store, archive)
    monkeypatch.setenv("DEMO_3D_URL", archive.as_uri())
    cache = tmp_path / "cache"
    pin = tmp_path / (archive.name + ".sha256")
    got = roster_asset.fetch(cache, pin)
    assert got == cache / digest
    assert (got / "roster.json").read_bytes() == (store / "roster.json").read_bytes()
    archive.unlink()
    assert roster_asset.fetch(cache, pin) == got
    (got / "roster.json").unlink()
    with pytest.raises(SystemExit, match="incomplete cached roster"):
        roster_asset.fetch(cache, pin)


def test_oversized_roster_is_refused_before_extraction(tmp_path, monkeypatch):
    import roster_asset

    archive = tmp_path / "too-large.tar.gz"
    archive.write_bytes(b"x" * 1025)
    monkeypatch.setenv("DEMO_3D_URL", archive.as_uri())
    monkeypatch.setattr(roster_asset, "MAX_BYTES", 1024)
    cache = tmp_path / "cache"
    with pytest.raises(SystemExit, match="exceeds the 1024-byte limit"):
        roster_asset.fetch(cache)
    assert not list(cache.iterdir())
