"""tools/fixtures/golden3d.py: the made-up game files the demo's 3D view and
pictures are drawn from, read by the product's own readers (wowstats.db2,
wowstats.assets, wowstats.character3d), never by a test double.

They are the golden files twice: the demo draws them (site/scripts/demo.py)
and tests/test_golden3d_browser.py checks the pixels Chromium and WebKit
draw from them. The files aren't committed: the generator is (about 200 kB
of output, rebuilt in a second)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wowstats import assets, character3d

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "fixtures"))

import golden3d  # noqa: E402

# The demo character's outfit at level 34 (tools/fixtures/generate.py build_demo).
OUTFIT = [{"slot": 1, "item_id": 253951}, {"slot": 3, "item_id": 6685}, {"slot": 4, "item_id": 2576},
          {"slot": 5, "item_id": 273044}, {"slot": 6, "item_id": 7000}, {"slot": 7, "item_id": 270036},
          {"slot": 8, "item_id": 6998}, {"slot": 9, "item_id": 3647}, {"slot": 10, "item_id": 270029},
          {"slot": 15, "item_id": 273045}, {"slot": 16, "item_id": 2816}, {"slot": 17, "item_id": 279838},
          {"slot": 18, "item_id": 7001}, {"slot": 19, "item_id": 5976}]
PRIEST = 5


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    root = tmp_path_factory.mktemp("golden")
    golden3d.write_store(root)
    return root


@pytest.fixture(scope="module")
def tables(store):
    return character3d.load_tables(assets.Store(store))


def assemble(tables, items, look=None):
    return character3d.assemble(tables, golden3d.GNOME, golden3d.MALE, PRIEST,
                                golden3d.build()["look"] if look is None else look, items)


def test_deterministic():
    a, b = golden3d.build(), golden3d.build()
    assert a["files"] == b["files"] and a["look"] == b["look"]
    assert {n: golden3d.wdc5(n, r) for n, r in a["tables"].items()} == \
        {n: golden3d.wdc5(n, r) for n, r in b["tables"].items()}


def test_ids_clear_of_the_game():
    # Made-up rows and files sit far above anything the client has, and
    # every made-up file is ours (no id of a real game file).
    g = golden3d.build()
    assert all(f >= golden3d.GOLDEN_FILE for f in g["files"])
    for name, rows in g["tables"].items():
        if name in ("Item", "CreatureModelData", "ChrRaces"):
            continue  # keyed by the demo's item ids, a u16 model id, the game's race id
        assert all(k >= golden3d.GOLDEN_ROW for k in rows), name


def test_tables_read_back(tables):
    """Every column the assembly looks up comes back as written, through
    the WDC5 reader and the build's layouts (data/dbd); the tables it only
    folds into its maps are checked by the assembly below."""
    g = golden3d.build()
    for name, rows in g["tables"].items():
        if name not in character3d.LOOKED_UP:
            continue
        got = tables.get(name) or {}
        for k, row in rows.items():
            for col, v in row.items():
                if col in character3d.TABLES.get(name, ()):
                    assert got.get(k, {}).get(col) == v, (name, k, col)
    assert character3d.race_id(tables, "Gnome") == golden3d.GNOME


def test_outfit_assembles_with_every_file(store, tables):
    a = assemble(tables, OUTFIT)
    assert a["recipe"] and a["missing"] == []
    have = assets.Store(store)
    assert [n for n in a["needs"] if have.path(n) is None] == []


@pytest.mark.parametrize("item", [7001, 274425])
def test_standalone_gnome_can_draw_both_wands(store, tables, item):
    answer = assemble(tables, [{"slot": 18, "item_id": item}], look=[])
    recipe = answer["recipe"]
    assert not answer["missing"] and recipe["ranged"]["kind"] == 19
    wand, = recipe["attachments"]
    assert wand["slot"] == 18 and wand["model"] and wand["textures"]
    assert not assets.closure(answer["needs"], assets.Store(store).read)[1]


def test_hands_shoulders_and_shield(tables):
    """The 3D branch's contract: slot 16 in the right hand (attachment 1),
    slot 17 in the left (2), a shield on the left arm (0), and
    ModelResourcesID[0] the left shoulder (6), [1] the right (5)."""
    a = assemble(tables, [{"slot": 3, "item_id": 6685}, {"slot": 16, "item_id": 2816},
                          {"slot": 17, "item_id": 279838}])["recipe"]
    at = {(x["slot"], x["attach"]): x["model"] for x in a["attachments"]}
    assert (16, 1) in at and (17, 2) in at
    left, right = at[(3, 6)], at[(3, 5)]
    assert left != right
    shield = assemble(tables, [{"slot": 17, "item_id": golden3d.TEST_SHIELD}])["recipe"]
    assert [(x["slot"], x["attach"]) for x in shield["attachments"]] == [(17, 0)]
    # The rod's glow rides on it.
    rod = next(x for x in a["attachments"] if x["slot"] == 17)
    assert rod["visuals"] and rod["visuals"][0]["attach"] == 1


def test_look_and_helmet(tables):
    bare = assemble(tables, [])["recipe"]["geosets"]
    # The demo's look: the tuft (hair style 2), beard and moustache, long ears.
    assert bare["set"].get("3") is True and bare["set"].get("103") is True and bare["set"].get("702") is True
    assert bare["groups"].get("0") != 0
    hooded = assemble(tables, [{"slot": 1, "item_id": 253951}])["recipe"]["geosets"]
    assert hooded["groups"].get("0") == 0  # the hood hides the hair
