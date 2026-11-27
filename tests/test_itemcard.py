"""Item cards, icons and enchant names (wowstats.itemcard), the routes that
serve them, and the enchant advice and timeline events."""

import json

import pytest

from wowstats import insights, itemcard

SPARSE = [
    {"ID": "19019", "Display_lang": "Thunderfury, Blessed Blade of the Windseeker", "OverallQualityID": "5",
     "ItemLevel": "80", "RequiredLevel": "60", "InventoryType": "13", "Bonding": "1", "MaxCount": "1",
     "ItemDelay": "1900", "ItemSet": "0", "Description_lang": ""},
    {"ID": "4504", "Display_lang": "Dwarven Guard Cloak", "OverallQualityID": "2", "ItemLevel": "31",
     "RequiredLevel": "0", "InventoryType": "16", "Bonding": "1", "MaxCount": "0", "ItemDelay": "0",
     "ItemSet": "7", "Description_lang": "Warm."},
    {"ID": "2589", "Display_lang": "Linen Cloth", "OverallQualityID": "1", "ItemLevel": "5", "RequiredLevel": "0",
     "InventoryType": "0", "Bonding": "0", "MaxCount": "0", "ItemDelay": "0", "ItemSet": "0"},
]
ITEMS = [{"ID": "19019", "ClassID": "2", "SubclassID": "7", "IconFileDataID": "135349"},
         {"ID": "4504", "ClassID": "4", "SubclassID": "1", "IconFileDataID": "133757"},
         {"ID": "2589", "ClassID": "7", "SubclassID": "5", "IconFileDataID": "132889"}]
ICONS = {135349: "inv_sword_39", 133757: "inv_misc_cape_05", 132889: "inv_fabric_linen_01"}
# Ephemeral Grips (273034), a City of Dalaran drop new in Forever: Item's
# IconFileDataID is 0, the icon is only on its appearance (1.60.1.70959).
EPHEMERAL = {"ID": "273034", "ClassID": "4", "SubclassID": "1", "InventoryType": "10", "IconFileDataID": "0"}
MODIFIED = [{"ID": "308395", "ItemID": "273034", "ItemAppearanceModifierID": "0", "ItemAppearanceID": "130417",
             "OrderIndex": "0"},
            {"ID": "308396", "ItemID": "273034", "ItemAppearanceModifierID": "3", "ItemAppearanceID": "9",
             "OrderIndex": "0"}]
APPEARANCES = [{"ID": "130417", "DefaultIconFileDataID": "132958"}, {"ID": "9", "DefaultIconFileDataID": "135349"}]
ICONS_ALL = {**ICONS, 132958: "inv_gauntlets_24"}
# Resilient Cloth Headband (211500), a base item Forever ships unchanged: in
# its Item table, not in its ItemSparse or forever-db; only the Classic Era
# client's ItemSparse names it (1.60.1.70959 against 1.15.9.70576).
RESILIENT = {"ID": "211500", "ClassID": "4", "SubclassID": "1", "InventoryType": "1", "IconFileDataID": "133694"}
ERA_SPARSE = [{"ID": "211500", "Display_lang": "Resilient Cloth Headband", "OverallQualityID": "1"},
              # Era's name never overrides Forever's own.
              {"ID": "4504", "Display_lang": "Era's Old Cloak Name", "OverallQualityID": "2"}]
POLESTAR = {"19019": {"stats": {"agi": 5, "sta": 8, "dps": 41.8}},
            "4504": {"stats": {"agi": 1, "armor": 22, "sta": 6, "sp": 5, "heal": 5, "mp5": 2}}}


def test_cards_are_the_tooltip():
    cards = itemcard.build_cards(SPARSE, ITEMS, ICONS, {7: "Guard's Set"}, {(2, 7): "Sword", (4, 1): "Cloth"},
                                 POLESTAR)
    tf = cards[19019]
    assert tf == {"n": "Thunderfury, Blessed Blade of the Windseeker", "q": 5, "i": "inv_sword_39",
                  "b": "Binds when picked up", "u": 1, "s": "One-Hand", "t": "Sword", "sp": 1.9, "dps": 41.8,
                  "w": ["+5 Agility", "+8 Stamina"], "rl": 60, "il": 80}
    cloak = cards[4504]
    assert cloak["ar"] == 22 and cloak["set"] == "Guard's Set" and cloak["de"] == "Warm."
    # Spell power already says "and healing": one line, not two.
    assert cloak["g"] == ["Equip: Increases damage and healing done by magical spells and effects by up to 5.",
                          "Equip: Restores 2 mana per 5 sec."]
    assert "d" not in tf, "no damage range made up from DmgVariance"
    linen = cards[2589]
    assert linen == {"n": "Linen Cloth", "q": 1, "i": "inv_fabric_linen_01"}


@pytest.mark.parametrize("row, want", [
    ({"Name_lang": "Stamina +3"}, "Stamina +3"),
    ({"Name_lang": "Armor +$k1", "EffectPointsMin_0": "30"}, "Armor +30"),
    ({"Name_lang": "Healing Spells +$k1, Damage Spells +$k2", "EffectPointsMin_0": "24", "EffectPointsMin_1": "8"},
     "Healing Spells +24, Damage Spells +8"),
    ({"Name_lang": "+$1220596M1% Critical Strike Chance"}, "+% Critical Strike Chance"),
    ({"Name_lang": ""}, None),
])
def test_enchant_text(row, want):
    assert itemcard.enchant_text(row) == want


@pytest.mark.parametrize("name, want", [("INV_Sword_39.blp", "inv_sword_39"), ("Spell_Holy_Heal.BLP", "spell_holy_heal"),
                                        ("../etc/passwd", None), ("a b.blp", None)])
def test_icon_name(name, want):
    assert itemcard.icon_name(name) == want


class Wago:
    """wago.tools as the catalog reads it: the build, then one CSV per table."""

    def __init__(self, era=True):
        self.asked = []
        self.era = era

    def __call__(self, url, timeout=30):
        self.asked.append(url)
        if url.endswith("/api/builds/wowf/latest"):
            return json.dumps({"version": "1.60.1.70959"}).encode()
        if url.endswith("/api/builds/wow_classic_era/latest"):
            return json.dumps({"version": "1.15.9.70576"}).encode() if self.era else None
        if "/images/wow/icons/" in url:
            return None if "missing" in url else b"\xff\xd8\xff\xe0" + url.encode() + b"\xff\xd9"
        table = url.split("/db2/")[1].split("/")[0]
        if url.endswith("build=1.15.9.70576"):
            assert table == "ItemSparse", "only Era's names are read"
            rows = ERA_SPARSE
            keys = list(dict.fromkeys(k for r in rows for k in r))
            return ("\n".join([",".join(keys)] + [",".join(r.get(k, "") for k in keys) for r in rows]) + "\n").encode()
        rows = {"ItemSparse": SPARSE, "Item": ITEMS + [EPHEMERAL, RESILIENT],
                "ItemModifiedAppearance": MODIFIED, "ItemAppearance": APPEARANCES,
                "ManifestInterfaceData": [{"ID": str(k), "FilePath": "Interface\\ICONS\\", "FileName": f"{v}.blp"}
                                          for k, v in ICONS_ALL.items()]
                + [{"ID": "1", "FilePath": "Interface\\Glues\\", "FileName": "x.blp"}],
                "ItemSet": [{"ID": "7", "Name_lang": "Guard's Set"}],
                "ItemSubClass": [{"ClassID": "2", "SubClassID": "7", "DisplayName_lang": "Sword"}],
                "SpellItemEnchantment": [{"ID": "66", "Name_lang": "Stamina +3"},
                                         {"ID": "744", "Name_lang": "Armor +$k1", "EffectPointsMin_0": "20"}]}[table]
        keys = list(dict.fromkeys(k for r in rows for k in r))
        lines = [",".join(keys)] + [",".join(json.dumps(r.get(k, "")) if "," in str(r.get(k, "")) else str(r.get(k, ""))
                                             for k in keys) for r in rows]
        return ("\n".join(lines) + "\n").encode()


def test_catalog_reads_wago_once_and_caches_icons(tmp_path, monkeypatch):
    from wowstats import upstream

    wago = Wago()
    monkeypatch.setattr(upstream, "fetch", wago)
    cat = itemcard.Catalog("https://wago.test", "wowf", tmp_path, icon_url="https://zam.test/images/wow/icons/")
    assert cat.card(19019)["i"] == "inv_sword_39"
    assert cat.enchant(66) == "Stamina +3" and cat.enchant(744) == "Armor +20" and cat.enchant(0) is None
    assert set(cat.many([19019, 4504, 1])) == {"19019", "4504"}
    n = len(wago.asked)
    # A second catalog on the same cache reads the file, not wago.tools.
    again = itemcard.Catalog("https://wago.test", "wowf", tmp_path)
    assert again.card(4504)["n"] == "Dwarven Guard Cloak" and len(wago.asked) == n
    body = cat.icon("inv_sword_39", "large")
    assert body.startswith(b"\xff\xd8\xff") and (tmp_path / "icons" / "large" / "inv_sword_39.jpg").is_file()
    assert cat.icon("inv_sword_39", "large") == body and len(wago.asked) == n + 1, "cached by name"
    assert cat.icon("missing_one", "medium") is None
    for bad in ("../x", "INV_UPPER", ""):
        assert cat.icon(bad, "large") is None
    assert cat.icon("inv_sword_39", "huge") is None


def test_icon_cache_recovers_from_throttling_and_a_truncated_jpeg(tmp_path, monkeypatch):
    import io
    import urllib.error

    from wowstats import upstream

    name = "inv_jewelry_talisman_13"
    cat = itemcard.Catalog("https://wago.test", "wowf", tmp_path)
    medium = tmp_path / "icons" / "medium" / f"{name}.jpg"
    medium.parent.mkdir(parents=True)
    medium.write_bytes(b"old HTML error page")
    calls, waits = [], []

    def read(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "throttled", {"Retry-After": "4"}, None)
        body = b"\xff\xd8\xff" + req.full_url.encode()
        return io.BytesIO(body if len(calls) == 2 else body + b"\xff\xd9")

    monkeypatch.setattr(upstream, "urlopen", read)
    monkeypatch.setattr(upstream.time, "sleep", waits.append)
    assert cat.icon(name, "medium") == medium.read_bytes() == b"\xff\xd8\xff" + calls[0].encode() + b"\xff\xd9"
    assert len(set(calls)) == 1 and waits == [4, 2]
    assert cat.icon(name, "medium") == medium.read_bytes() and len(calls) == 3
    large = cat.icon(name, "large")
    assert large != medium.read_bytes() and large == (tmp_path / "icons" / "large" / medium.name).read_bytes()
    assert calls[-1].endswith(f"/large/{name}.jpg")


@pytest.mark.parametrize(("code", "retry_after", "calls"), [(404, None, 1), (403, None, 1), (503, None, 3),
                                                           (429, "120", 1)])
def test_an_unavailable_icon_stops_and_writes_nothing(tmp_path, monkeypatch, code, retry_after, calls):
    import urllib.error

    from wowstats import upstream

    requests, waits = [], []

    def down(req, timeout):
        requests.append(req.full_url)
        headers = {"Retry-After": retry_after} if retry_after else {}
        raise urllib.error.HTTPError(req.full_url, code, "unavailable", headers, None)

    monkeypatch.setattr(upstream, "urlopen", down)
    monkeypatch.setattr(upstream.time, "sleep", waits.append)
    cat = itemcard.Catalog("https://wago.test", "wowf", tmp_path)
    assert cat.icon("inv_jewelry_ring_118", "large") is None
    assert len(requests) == calls and len(waits) == calls - 1
    assert not list(tmp_path.rglob("*.jpg"))


def test_base_items_take_the_era_clients_name(tmp_path, monkeypatch):
    """A base item Forever's tables don't name gets the Classic Era
    client's name; Forever's own name always wins; Era unreachable, the
    item stays unnamed and nothing fails (the page then calls it by slot)."""
    from wowstats import forever, upstream

    # forever-db names it since 2026.11.2: left out, so it stands for an
    # item no Forever source names.
    ref = forever.data()
    monkeypatch.setattr(forever, "data", lambda: {**ref, "items": {k: v for k, v in (ref.get("items") or {}).items()
                                                                  if k != "211500"}})
    monkeypatch.setattr(upstream, "fetch", Wago())
    cat = itemcard.Catalog("https://wago.test", "wowf", tmp_path / "a")
    assert cat.card(211500)["n"] == "Resilient Cloth Headband" and cat.card(211500)["s"] == "Head"
    assert cat.card(4504)["n"] == "Dwarven Guard Cloak"
    monkeypatch.setattr(upstream, "fetch", Wago(era=False))
    cat = itemcard.Catalog("https://wago.test", "wowf", tmp_path / "b")
    assert "n" not in cat.card(211500) and cat.card(211500)["s"] == "Head"
    assert cat.card(19019)["n"].startswith("Thunderfury")


def test_items_and_icons_routes(tmp_path, monkeypatch, run_app):
    from wowstats import ui

    class Cat:
        def many(self, ids):
            return {str(i): {"n": "X", "q": 2, "i": "inv_x"} for i in ids if i == 19019}

        def icon(self, name, size):
            return b"\xff\xd8\xffjpeg" if name == "inv_x" else None

    monkeypatch.setattr(itemcard, "shared", lambda: Cat())
    from tests.test_public_view import PoisonStore

    for view, icons in (("full", True), ("public", False)):
        app = ui.App({"VIEW": view, "AUTH": "open", "BIND": "127.0.0.1", "MODEL3D": "off"}, store=PoisonStore())
        r = run_app(app)
        code, _, body = r.get("/api/items?ids=19019,7,abc,../1")
        assert code == 200 and json.loads(body) == {"items": [{"id": 19019, "n": "X", "q": 2, "i": "inv_x"}]}
        code, headers, body = r.get("/icons/large/inv_x.jpg")
        assert (code == 200) is icons, view
        if icons:
            assert headers["Content-Type"] == "image/jpeg" and body.startswith(b"\xff\xd8\xff")
            assert r.get("/icons/large/nope.jpg")[0] == 404
            assert r.get("/icons/large/..%2Fx.jpg")[0] == 404
        code, headers, body = r.get("/items.js")
        assert code == 200 and "javascript" in headers["Content-Type"]


GEAR = [{"slot": 2, "item_id": 1, "name": "Medallion", "enchant_id": 0},
        {"slot": 5, "item_id": 2, "name": "Robes", "enchant_id": 254},
        {"slot": 8, "item_id": 3, "name": "Boots", "enchant_id": 0},
        {"slot": 11, "item_id": 4, "name": "Ring", "enchant_id": 0},
        {"slot": 18, "item_id": 5, "name": "Wand", "enchant_id": 0}]


def test_enchant_advice_names_bare_enchantable_slots():
    cs = insights.enchant_claims(GEAR, [{"skill_id": 333, "rank": 150, "name": "Enchanting"}])
    assert len(cs) == 1
    t = cs[0]["text"]
    assert "Neck (Medallion)" in t and "Feet (Boots)" in t and "You're an Enchanter (150)" in t
    assert "Ring" not in t and "Wand" not in t and "Robes" not in t  # no recipe for them / already done
    assert cs[0]["kind"] == "enchant" and cs[0]["title"] == "No enchant on 2 slots that can take one"
    assert [i["slot_name"] for i in cs[0]["items"]] == ["Neck", "Feet"]
    assert "Enchanter" not in insights.enchant_claims(GEAR, [])[0]["text"]
    # An older shipper's sheet (no enchant ids) proves nothing.
    assert insights.enchant_claims([{k: v for k, v in g.items() if k != "enchant_id"} for g in GEAR], []) == []
    assert insights.enchant_claims([g for g in GEAR if g["enchant_id"]], []) == []


def test_timeline_marks_an_enchant_added():
    t0, t1, t2 = "2026-11-18T10:00:00Z", "2026-11-18T11:00:00Z", "2026-11-18T12:00:00Z"
    rows = [{"_time": t0, "items": [{"slot": 8, "item_id": 3, "name": "Boots"}]},  # old shipper: unknown
            {"_time": t1, "items": [{"slot": 8, "item_id": 3, "name": "Boots", "enchant_id": 0}]},
            {"_time": t2, "items": [{"slot": 8, "item_id": 3, "name": "Boots", "enchant_id": 66,
                                     "enchant": "Stamina +3"}]}]
    ev = insights.timeline({"levels": [], "header": {}}, [], [], [], rows, [], [], None)
    texts = [e["text"] for e in ev if e["type"] == "gear"]
    assert texts == ["Enchanted Boots: Stamina +3"]


def test_items_without_a_sparse_row_still_get_a_card():
    """Forever's ItemSparse holds only the items it changed: Hardened Root
    Staff (1317) has an Item row and Polestar's values, no ItemSparse row."""
    items = [*ITEMS, {"ID": "1317", "ClassID": "2", "SubclassID": "10", "InventoryType": "17",
                      "IconFileDataID": "135153"}]
    pole = {**POLESTAR, "1317": {"inv": 17, "ilvl": 27, "req": 0, "quality": 2, "speed": 3300,
                                 "stats": {"dps": 13.6, "sp": 20, "heal": 20, "spi": 9, "str": 1}}}
    cards = itemcard.build_cards(SPARSE, items, {**ICONS, 135153: "inv_staff_08"}, {},
                                 {(2, 10): "Staff"}, pole)
    staff = cards[1317]
    assert "n" not in staff, "no name made up: the page takes it from the link"
    assert staff["i"] == "inv_staff_08" and staff["q"] == 2 and staff["s"] == "Two-Hand" and staff["t"] == "Staff"
    assert staff["il"] == 27 and staff["sp"] == 3.3 and staff["dps"] == 13.6 and "+9 Spirit" in staff["w"]
    assert "rl" not in staff
    assert len(cards) == 4


def test_an_item_without_its_own_icon_takes_its_appearances(tmp_path, monkeypatch):
    """0.2.3 showed an empty frame for Ephemeral Grips and Black Handwraps:
    Item.IconFileDataID is 0 for 1,617 of Forever's items, and the game
    takes the icon from the base appearance (modifier 0) instead."""
    from wowstats import upstream

    monkeypatch.setattr(upstream, "fetch", Wago())
    cat = itemcard.Catalog("https://wago.test", "wowf", tmp_path)
    assert cat.card(273034)["i"] == "inv_gauntlets_24"
    assert cat.card(19019)["i"] == "inv_sword_39", "Item's own icon first"


def test_an_item_with_no_icon_anywhere_gets_the_question_mark():
    items = [*ITEMS, {"ID": "5", "ClassID": "4", "SubclassID": "1", "InventoryType": "10", "IconFileDataID": "0"}]
    cards = itemcard.build_cards(SPARSE, items, ICONS, {}, {}, {"5": {"quality": 2, "inv": 10}}, {})
    assert cards[5]["i"] == itemcard.QUESTION == "inv_misc_questionmark"


def test_a_healing_item_with_some_damage_reads_as_forevers_one_line():
    """Stoneweaver Leggings in Forever: healing 20 and damage 7 on one line."""
    _, green = itemcard.stat_lines({"armor": 53, "heal": 20, "int": 8, "sp": 7, "spi": 12, "sta": 9})
    assert green == ["Equip: Increases healing done by up to 20 and damage done by up to 7 for all magical spells "
                     "and effects."]
