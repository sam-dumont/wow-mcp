"""The demo's client icons: names to FileDataIDs, BLP to JPEG, and the store that carries them.
The BLP is drawn here (tools/model3d_fixtures.py); no game art is committed."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from wowstats import publish, upstream

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "fixtures"))

import demo3d  # noqa: E402
import demo_icons  # noqa: E402
import golden3d  # noqa: E402

MANIFEST = {
    10: {"FilePath": "Interface\\ICONS\\", "FileName": "INV_Sword_39.blp"},
    11: {"FilePath": "Interface\\ICONS\\", "FileName": "Spell_Shadow_DevouringPlague..blp"},
    12: {"FilePath": "Interface\\Buttons\\", "FileName": "UI-Panel-Button-Up.blp"},
    13: {"FilePath": "interface/icons/", "FileName": "INV_Sword_39.blp"},  # a second row, same name
}


def test_names_resolve_to_the_lowest_icon_file_id_and_skip_other_folders():
    got = demo_icons.icon_files(MANIFEST)
    assert got == {"inv_sword_39": 10, "spell_shadow_devouringplague": 11}


def test_an_item_takes_its_own_icon_else_its_appearances():
    by_fdid = {10: "inv_sword_39", 11: "inv_helmet_01", 12: "inv_misc_questionmark"}
    items = {100: {"IconFileDataID": 10}, 101: {"IconFileDataID": 0}, 102: {"IconFileDataID": 0}}
    modified = {1: {"ID": 1, "ItemID": 101, "ItemAppearanceModifierID": 0, "ItemAppearanceID": 5, "OrderIndex": 0}}
    appearances = {5: {"ID": 5, "DefaultIconFileDataID": 11}}
    got = demo_icons.item_icon_names({100, 101, 102}, items, modified, appearances, by_fdid)
    assert got == {100: "inv_sword_39", 101: "inv_helmet_01"}  # 102 has none: the caller reports it


def test_item_ids_come_from_columns_json_and_links_but_not_objective_markers():
    rows = [{"item_id": 7048}, {"items": json.dumps([{"slot": 1, "item_id": 6685}])},
            {"text": "|cff|Hitem:2816::::|h[Sword]|h"}, {"objectives": "item:1:"}]
    sheets = {"p": [[{"slot": 3, "item_id": 273044}]]}
    assert demo_icons.item_ids(rows, sheets) == {7048, 6685, 2816, 273044}


def test_every_demo_class_has_talent_icons_from_the_reference():
    names = demo_icons.talent_icon_names({"PRIEST", "WARRIOR"})
    assert len(names) > 60 and all(upstream.ICON_NAME.fullmatch(n) for n in names)


def _red_blps():
    fx = golden3d.FX
    red = (200, 30, 30)
    return {
        "bgra": fx.blp_bgra(64, 64, [(*red, 255)] * 4096),
        "dxt1": fx.dxt1_solid(64, 64, red),
        "palette": fx.blp_palette(64, 64, [red], [0] * 4096, [], 0),
    }


@pytest.mark.parametrize("kind", ["bgra", "dxt1", "palette"])
def test_a_blp_becomes_a_jpeg_in_each_size(kind):
    pytest.importorskip("PIL")
    from PIL import Image

    out = demo_icons.jpegs(_red_blps()[kind])
    assert set(out) == {"large", "medium"}
    for size, body in out.items():
        assert upstream._jpeg(body)
        img = Image.open(io.BytesIO(body))
        assert img.size == (demo_icons.SIZES[size],) * 2
        r, g, b = img.convert("RGB").getpixel((5, 5))
        assert r > 150 and g < 90 and b < 90


def test_written_icons_sit_where_cached_icon_reads_them(tmp_path):
    pytest.importorskip("PIL")
    blp = golden3d.FX.blp_bgra(8, 8, [(10, 200, 10, 255)] * 64)
    files, total = demo_icons.write(tmp_path, "1.60.1.71139", {"inv_sword_39": 10}, {10: blp})
    assert files == 2 and total > 0
    for size in ("large", "medium"):
        assert upstream.cached_icon(None, tmp_path, size, "inv_sword_39").startswith(b"\xff\xd8")
    assert json.loads((tmp_path / "icons.json").read_text()) == {"build": "1.60.1.71139", "names": ["inv_sword_39"]}


def test_cached_icon_without_a_base_never_fetches(tmp_path, monkeypatch):
    monkeypatch.setattr(upstream, "fetch", lambda *a, **k: pytest.fail("fetched"))
    assert upstream.cached_icon(None, tmp_path, "medium", "inv_sword_39") is None


def test_the_store_check_names_a_missing_icon_file_and_a_build_mismatch(tmp_path):
    (tmp_path / "icons.json").write_text(json.dumps({"build": "1.60.1.71139", "names": ["inv_sword_39"]}))
    with pytest.raises(ValueError, match=r"large/inv_sword_39"):
        demo3d.read_icons(tmp_path, "1.60.1.71139")
    with pytest.raises(ValueError, match="1.60.1.99999"):
        demo3d.read_icons(tmp_path, "1.60.1.99999")
    with pytest.raises(ValueError, match="no client icons"):
        demo3d.read_icons(tmp_path / "other", "1.60.1.71139")


def test_a_talent_icon_the_store_lacks_fails_the_publish_with_its_name():
    b = publish.Bundle.__new__(publish.Bundle)
    b.put = lambda *a: None
    with pytest.raises(publish.PublishError, match="spell_not_in_client"):
        publish.copy_icons(b, {"spell_holy_a", "spell_not_in_client"},
                           icons=lambda name, size: None if name == "spell_not_in_client" else b"x")
