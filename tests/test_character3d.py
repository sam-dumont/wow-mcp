"""wowstats.character3d on a tiny made-up world: one race's two models (the
updated one and the original), three customization options, and a handful
of items. Every id here is invented; no game table is copied."""

import copy

import pytest

from wowstats import character3d as c3

GNOME, FEMALE, PRIEST, HUNTER = 7, 1, 5, 3
HD, SD = 14, 270
LAYOUT, SD_LAYOUT = 116, 203


def world() -> dict:
    sections = {i + 1: {"CharComponentTextureLayoutID": LAYOUT, "SectionType": i, "X": (i % 2) * 512,
                        "Y": (i // 2) * 256, "Width": 512, "Height": 256} for i in range(8)}
    sections[20] = {"CharComponentTextureLayoutID": SD_LAYOUT, "SectionType": 3, "X": 0, "Y": 0, "Width": 256,
                    "Height": 128}
    return {
        "ChrRaceXChrModel": {1: {"ChrRacesID": GNOME, "ChrModelID": HD, "Sex": FEMALE}},
        "ChrModel": {HD: {"Sex": 1, "DisplayID": 500, "CharComponentTextureLayoutID": LAYOUT},
                     SD: {"Sex": 1, "DisplayID": 1564, "CharComponentTextureLayoutID": SD_LAYOUT}},
        "CreatureDisplayInfo": {500: {"ModelID": 50}, 1564: {"ModelID": 51}},
        "CreatureModelData": {50: {"FileDataID": 9001}, 51: {"FileDataID": 9002}},
        "ChrRaces": {GNOME: {}},
        "ChrCustomizationOption": {
            85: {"ChrModelID": HD, "Flags": 0, "OrderIndex": 0, "ChrCustomizationID": 1},  # skin
            87: {"ChrModelID": HD, "Flags": 0, "OrderIndex": 1, "ChrCustomizationID": 3},  # hair
            89: {"ChrModelID": HD, "Flags": 0x20, "OrderIndex": 2, "ChrCustomizationID": 18},  # no default
            91: {"ChrModelID": HD, "Flags": 0x20, "OrderIndex": 3, "ChrCustomizationID": 40},  # an extra limb
            95: {"ChrModelID": SD, "Flags": 0, "OrderIndex": 0, "ChrCustomizationID": 3},  # the original's hair
        },
        "ChrCustomizationChoice": {
            1235: {"ChrCustomizationOptionID": 85, "OrderIndex": 0},
            1236: {"ChrCustomizationOptionID": 85, "OrderIndex": 1},
            1260: {"ChrCustomizationOptionID": 87, "OrderIndex": 0},
            1261: {"ChrCustomizationOptionID": 87, "OrderIndex": 1},
            1270: {"ChrCustomizationOptionID": 89, "OrderIndex": 0},
            1280: {"ChrCustomizationOptionID": 91, "OrderIndex": 0},
            1950: {"ChrCustomizationOptionID": 95, "OrderIndex": 0},
            1951: {"ChrCustomizationOptionID": 95, "OrderIndex": 1},
        },
        "ChrCustomizationElement": {
            1: {"ChrCustomizationChoiceID": 1235, "ChrCustomizationMaterialID": 3001},
            2: {"ChrCustomizationChoiceID": 1236, "ChrCustomizationMaterialID": 3002},
            3: {"ChrCustomizationChoiceID": 1236, "ChrCustomizationMaterialID": 3005,
                "RelatedChrCustomizationChoiceID": 1261},  # a face paint only with the second hair
            4: {"ChrCustomizationChoiceID": 1260, "ChrCustomizationGeosetID": 41},
            5: {"ChrCustomizationChoiceID": 1261, "ChrCustomizationGeosetID": 42},
            6: {"ChrCustomizationChoiceID": 1261, "ChrCustomizationMaterialID": 3003},
            7: {"ChrCustomizationChoiceID": 1280, "ChrCustomizationSkinnedModelID": 7},
            8: {"ChrCustomizationChoiceID": 1950, "ChrCustomizationGeosetID": 41},
            9: {"ChrCustomizationChoiceID": 1951, "ChrCustomizationGeosetID": 43},
        },
        "ChrCustomizationGeoset": {41: {"GeosetType": 0, "GeosetID": 2}, 42: {"GeosetType": 0, "GeosetID": 3},
                                   43: {"GeosetType": 0, "GeosetID": 5}},
        "ChrCustomizationMaterial": {3001: {"ChrModelTextureTargetID": 1, "MaterialResourcesID": 4001},
                                     3002: {"ChrModelTextureTargetID": 1, "MaterialResourcesID": 4002},
                                     3003: {"ChrModelTextureTargetID": 10, "MaterialResourcesID": 4003},
                                     3005: {"ChrModelTextureTargetID": 5, "MaterialResourcesID": 4005}},
        "ChrCustomizationSkinnedModel": {7: {"CollectionsFileDataID": 9100, "GeosetType": 29, "GeosetID": 2}},
        "ChrCustomizationCondModel": {},
        "ChrModelTextureLayer": {
            1: {"CharComponentTextureLayoutsID": LAYOUT, "ChrModelTextureTargetID": [1, 0], "TextureType": 1,
                "BlendMode": 1, "TextureSectionTypeBitMask": -1},
            2: {"CharComponentTextureLayoutsID": LAYOUT, "ChrModelTextureTargetID": [5, 0], "TextureType": 1,
                "BlendMode": 15, "TextureSectionTypeBitMask": 1 << 3},
            3: {"CharComponentTextureLayoutsID": LAYOUT, "ChrModelTextureTargetID": [10, 0], "TextureType": 6,
                "BlendMode": 1, "TextureSectionTypeBitMask": -1},
            4: {"CharComponentTextureLayoutsID": LAYOUT, "ChrModelTextureTargetID": [16, 0], "TextureType": 1,
                "BlendMode": 9, "TextureSectionTypeBitMask": 1 << 5},
            5: {"CharComponentTextureLayoutsID": SD_LAYOUT, "ChrModelTextureTargetID": [1, 0], "TextureType": 1,
                "BlendMode": 1, "TextureSectionTypeBitMask": -1},
        },
        "ChrModelMaterial": {
            1: {"CharComponentTextureLayoutsID": LAYOUT, "TextureType": 1, "Width": 2048, "Height": 1024},
            2: {"CharComponentTextureLayoutsID": LAYOUT, "TextureType": 6, "Width": 512, "Height": 512},
            3: {"CharComponentTextureLayoutsID": SD_LAYOUT, "TextureType": 1, "Width": 1024, "Height": 1024},
        },
        "CharComponentTextureSections": sections,
        "TextureFileData": {f: {"FileDataID": f, "UsageType": 0, "MaterialResourcesID": m} for f, m in (
            (8001, 4001), (8002, 4002), (8003, 4003), (8005, 4005), (8100, 4100), (8101, 4101), (8102, 4101),
            (8200, 4200), (8300, 4300), (8400, 4400), (8600, 4600), (8700, 4700), (8800, 4800))},
        "ComponentTextureFileData": {8101: {"GenderIndex": 0, "ClassID": 0, "RaceID": 0},
                                     8102: {"GenderIndex": 1, "ClassID": 0, "RaceID": 0}},
        "ModelFileData": {f: {"FileDataID": f, "ModelResourcesID": r} for f, r in (
            (9200, 600), (9201, 600), (9300, 610), (9400, 620), (9700, 630), (9701, 630), (9800, 640),
            (9900, 650), (9910, 660))},
        "ComponentModelFileData": {9200: {"RaceID": GNOME, "GenderIndex": 1, "ClassID": 0, "PositionIndex": -1},
                                   9201: {"RaceID": 1, "GenderIndex": 0, "ClassID": 0, "PositionIndex": -1},
                                   9700: {"RaceID": 0, "GenderIndex": 2, "ClassID": 0, "PositionIndex": 0},
                                   9701: {"RaceID": 0, "GenderIndex": 2, "ClassID": 0, "PositionIndex": 1}},
        # item: (class, subclass, inventory type)
        "Item": {i: {"ClassID": c, "SubclassID": s, "InventoryType": t} for i, (c, s, t) in {
            100: (4, 1, 20), 101: (4, 1, 1), 102: (4, 1, 8), 103: (2, 7, 21), 104: (2, 3, 26), 105: (4, 6, 14),
            106: (4, 1, 3), 107: (4, 1, 16), 108: (4, 0, 2), 109: (2, 19, 26), 110: (4, 1, 7),
            111: (2, 7, 13), 112: (2, 2, 15), 113: (2, 1, 17)}.items()},
        "ItemModifiedAppearance": {
            1: {"ItemID": 100, "ItemAppearanceModifierID": 0, "ItemAppearanceID": 200, "OrderIndex": 0},
            2: {"ItemID": 100, "ItemAppearanceModifierID": 1, "ItemAppearanceID": 299, "OrderIndex": 0},
            **{k: {"ItemID": i, "ItemAppearanceModifierID": 0, "ItemAppearanceID": i + 100, "OrderIndex": 0}
               for k, i in ((3, 101), (4, 102), (5, 103), (6, 104), (7, 105), (8, 106), (9, 107), (10, 108),
                            (11, 109), (12, 111), (13, 112), (14, 113))},
        },
        "ItemAppearance": {**{a: {"ItemDisplayInfoID": a + 100} for a in range(200, 210)},
                           211: {"ItemDisplayInfoID": 303}, 212: {"ItemDisplayInfoID": 304},
                           213: {"ItemDisplayInfoID": 303},
                           299: {"ItemDisplayInfoID": 399}},
        "ItemDisplayInfo": {
            300: idi(geosets=[1, 0, 1]),  # a robe: sleeves 802, chest 1002, trousers 1302
            301: idi(models=[600], mats=[4200], geosets=[2, 1], helmet=[0, 77]),
            302: idi(models=[610], mats=[4300], geosets=[1, 0], attach_geosets=[3, 1]),
            303: idi(models=[620], mats=[4400], visual=55),
            304: idi(models=[640], mats=[4700]),
            305: idi(models=[650], mats=[4800]),
            306: idi(models=[630, 630], mats=[4600, 4600]),
            307: idi(mats=[4600], geosets=[5]),  # a cape: the body's cape geosets, textured
            308: idi(),
            309: idi(models=[660]),
            399: idi(geosets=[9, 9, 9]),
        },
        "ItemDisplayInfoMaterialRes": {1: {"ItemDisplayInfoID": 300, "ComponentSection": 3, "MaterialResourcesID": 4100},
                                       2: {"ItemDisplayInfoID": 300, "ComponentSection": 5, "MaterialResourcesID": 4101},
                                       3: {"ItemDisplayInfoID": 300, "ComponentSection": 1, "MaterialResourcesID": 4100}},
        "ItemDisplayInfoModelMatRes": {1: {"ItemDisplayInfoID": 303, "ModelIndex": 0, "TextureType": 2,
                                           "MaterialResourcesID": 4400},
                                       2: {"ItemDisplayInfoID": 303, "ModelIndex": 0, "TextureType": 3,
                                           "MaterialResourcesID": 4300}},
        "HelmetGeosetData": {1: {"HelmetGeosetVisDataID": 77, "RaceID": GNOME, "HideGeosetGroup": 0},
                             2: {"HelmetGeosetVisDataID": 77, "RaceID": 1, "HideGeosetGroup": 7}},
        "ItemVisuals": {55: {"ModelFileID": [0, 0, 0, 0, 0]}, 56: {"ModelFileID": [0, 9600, 0, 0, 0]}},
        "ItemVisualsXEffect": {1: {"ItemVisualsID": 55, "AttachmentID": 1, "DisplayWeaponSubclassID": -1,
                                   "AttachmentModelFileID": 9500},
                               2: {"ItemVisualsID": 55, "AttachmentID": 2, "DisplayWeaponSubclassID": 4,
                                   "AttachmentModelFileID": 9501}},
        "SpellItemEnchantment": {900: {"ItemVisual": 56}},
    }


def idi(models=(), mats=(), geosets=(), attach_geosets=(), helmet=(0, 0), visual=0) -> dict:
    def six(v):
        return list(v) + [0] * (6 - len(v))

    return {"ModelResourcesID": (list(models) + [0, 0])[:2], "ModelMaterialResourcesID": (list(mats) + [0, 0])[:2],
            "GeosetGroup": six(geosets), "AttachmentGeosetGroup": six(attach_geosets),
            "HelmetGeosetVis": list(helmet), "ItemVisual": visual}


def build(items=(), choices=(), cls=PRIEST, models="hd", tables=None):
    """items: (slot, item id) or (slot, item id, {extra keys})."""
    worn = [{"slot": it[0], "item_id": it[1], **(it[2] if len(it) > 2 else {})} for it in items]
    return c3.assemble(tables or world(), GNOME, FEMALE, cls, list(choices), worn, models)


def layers(recipe, ttype=1):
    m = next(m for m in recipe["materials"] if m["type"] == ttype)
    return {ly["target"]: ly for ly in m["layers"]}


def test_the_bare_character():
    out = build()
    r = out["recipe"]
    assert out["classic"] is False and out["missing"] == []
    assert r["v"] == 1 and r["model"] == 9001 and r["animation"] == 0
    # Defaults: each option's first choice, none for the hidden ones.
    assert r["geosets"] == {"set": {"2": True, "3": False}, "groups": {"7": 2}}  # the ears
    assert r["materials"] == [{"type": 1, "width": 2048, "height": 1024, "layers": [
        {"file": 8001, "target": 1, "x": 0, "y": 0, "w": 2048, "h": 1024, "blend": 1}]}]
    assert r["textures"] == {} and r["skinned"] == r["attachments"] == r["collections"] == []
    assert out["needs"] == [8001, 9001]
    assert set(r) == {"v", "model", "geosets", "materials", "textures", "skinned", "attachments", "collections",
                      "animation"}


def test_saved_choices_and_the_choice_they_depend_on():
    r = build(choices=[{"optionId": 85, "choiceId": 1236}, {"optionId": 87, "choiceId": 1261},
                       {"optionId": 85, "choiceId": 9999}, {"optionId": "x"}])["recipe"]
    assert r["geosets"]["set"] == {"2": False, "3": True}
    got = layers(r)
    assert got[1]["file"] == 8002
    # The face paint (target 5) needs the second hair, which is chosen: its section is the layer's (bit 3).
    assert got[5] == {"file": 8005, "target": 5, "x": 512, "y": 256, "w": 512, "h": 256, "blend": 15}
    # The hair is one whole picture: bound as it is, not composited.
    assert r["textures"] == {"6": 8003} and [m["type"] for m in r["materials"]] == [1]
    r = build(choices=[{"optionId": 85, "choiceId": 1236}])["recipe"]
    assert 5 not in layers(r)


def test_items_dress_the_body_in_slot_layers():
    out = build(items=[(5, 100)])
    got = layers(out["recipe"])
    # chest = slot layer 13: target 13 * 100 + section. Section 3 has its own layer (blend 15); section 1
    # falls back to the base skin layer, whose plain copy (1) becomes alpha (15); section 5's layer keeps 9.
    assert got[1303] == {"file": 8100, "target": 1303, "x": 512, "y": 256, "w": 512, "h": 256, "blend": 15}
    assert got[1301]["blend"] == 15 and got[1301]["x"] == 512 and got[1301]["y"] == 0
    assert got[1305]["file"] == 8102 and got[1305]["blend"] == 9  # the female texture of the two
    assert list(got) == sorted(got)
    # Its geosets: sleeves 8 -> 1 + 1, chest 10 -> 1 + 0, trousers 13 -> 1 + 1, torso, upper arms 1.
    assert out["recipe"]["geosets"]["groups"] == {"7": 2, "8": 2, "10": 1, "13": 2, "22": 1, "28": 1}


def test_helmet_attaches_and_hides_the_hair():
    out = build(items=[(1, 101)])
    r = out["recipe"]
    assert r["attachments"] == [{"slot": 1, "attach": 11, "model": 9200, "textures": {"2": 8200}, "visuals": [],
                                 "scale": 1.0}]
    # Helm 27 and skull 21 from its geoset groups; the gnome's HelmetGeosetData hides group 0 (hair),
    # the other race's row (group 7) doesn't apply.
    assert r["geosets"]["groups"] == {"7": 2, "27": 3, "21": 2, "0": 0}
    assert 9200 in out["needs"] and 9201 not in out["needs"]


def test_boots_are_a_collection_model():
    r = build(items=[(8, 102)])["recipe"]
    # A collection's groups hold what the viewer shows (group * 100 + value): wow.export's 1 + the
    # AttachmentGeosetGroup number (3 -> 4, 1 -> 2), never the raw number.
    assert r["collections"] == [{"slot": 8, "model": 9300, "textures": {"2": 8300},
                                 "geosets": {"set": {}, "groups": {"5": 4, "20": 2}}}]
    assert r["geosets"]["groups"] == {"7": 2, "5": 2, "20": 2}  # feet: 0 means 2


def test_weapon_glows_and_enchant():
    r = build(items=[(16, 103, {"enchant": 900})])["recipe"]
    (att,) = r["attachments"]
    assert att["attach"] == 1 and att["model"] == 9400
    assert att["textures"] == {"2": 8400, "3": 8300}
    # Its own visual: the effect for any subclass (not the mace-only one); the enchant's visual has no
    # effect rows, so its models array places one per point.
    assert att["visuals"] == [{"model": 9500, "attach": 1}, {"model": 9600, "attach": 2}]


def test_shoulders_shield_and_cape():
    r = build(items=[(3, 106), (17, 105), (15, 107)])["recipe"]
    assert [(a["slot"], a["attach"], a["model"]) for a in r["attachments"]] == [(3, 6, 9700), (3, 5, 9701),
                                                                                 (17, 0, 9900)]
    assert r["textures"] == {"2": 8600} and r["geosets"]["groups"]["15"] == 6


def test_what_isnt_drawn():
    out = build(items=[(2, 108), (11, 108), (16, 103), (18, 104, {"name": "Gun"}), (5, 555), (7, 110)])
    # A neck is never drawn; a priest holds the sword, the gun stays out quietly; an unknown item and one
    # without an appearance are said.
    assert [a["slot"] for a in out["recipe"]["attachments"]] == [16]
    assert out["missing"] == [{"slot": 5, "item_id": 555, "why": "not in Forever's item tables"},
                              {"slot": 7, "item_id": 110, "why": "no appearance in Forever's item tables"}]


def held(out):
    return [(a["slot"], a["attach"]) for a in out["recipe"]["attachments"]]


def test_two_one_handers_and_a_ranged_weapon():
    """tools/3dcases c20 (a hunter, two one-handers and a crossbow) and c04
    (a rogue, two one-handers and a bow): Blizzard's renders show a weapon in
    each hand, the ranged one nowhere."""
    for cls in (HUNTER, PRIEST):
        out = build(items=[(16, 111), (17, 111), (18, 104)], cls=cls)
        assert held(out) == [(16, 1), (17, 2)]
        assert out["missing"] == []
        assert held(build(items=[(16, 111), (17, 111), (18, 112)], cls=cls)) == [(16, 1), (17, 2)]


def test_a_ranged_weapon_takes_a_free_hand():
    # A gun in the right hand when the main hand is empty; a bow in the left beside a one-hander.
    assert held(build(items=[(18, 104)], cls=HUNTER)) == [(18, 1)]
    assert held(build(items=[(16, 111), (18, 112)], cls=HUNTER)) == [(16, 1), (18, 2)]
    # A sword in the main hand keeps it; the gun stays out quietly.
    out = build(items=[(16, 103, {"name": "Sword"}), (18, 104, {"name": "Gun"})], cls=HUNTER)
    assert held(out) == [(16, 1)] and out["missing"] == []
    # A two-hander takes both hands: no bow beside it (tools/3dcases c16).
    assert held(build(items=[(16, 113), (18, 112)], cls=HUNTER)) == [(16, 1)]
    # A wand in the ranged slot is never drawn.
    assert build(items=[(18, 109)], cls=HUNTER)["recipe"]["attachments"] == []


def test_a_shield_on_the_shield_point():
    assert held(build(items=[(16, 111), (17, 105)])) == [(16, 1), (17, 0)]


def test_skinned_model_replaces_the_base_limb():
    r = build(choices=[{"optionId": 91, "choiceId": 1280}])["recipe"]
    assert r["skinned"] == [{"model": 9100, "textures": {"1": "material:1", "8": "material:1"},
                             "geosets": {"set": {"2902": True}, "groups": {}}}]
    assert r["geosets"]["groups"] == {"7": 2, "29": 0}


def test_the_original_model():
    out = build(choices=[{"optionId": 87, "choiceId": 1261}], models="sd")
    r = out["recipe"]
    assert out["classic"] is True and r["model"] == 9002
    # The updated look's second hair is the original's second hair (same option kind, same place).
    assert r["geosets"]["set"] == {"2": False, "5": True}
    assert r["materials"] == []  # no skin material on its own options here
    # No original model for this race: the updated one, said.
    t = world()
    del t["ChrModel"][SD]
    out = build(models="sd", tables=t)
    assert out["classic"] is False and out["recipe"]["model"] == 9001


def test_no_model():
    out = c3.assemble(world(), 99, 0, 1, [], [], "hd")
    assert out["recipe"] is None and out["needs"] == [] and "race 99" in out["why"]


def test_tables_are_not_changed():
    t = world()
    before = copy.deepcopy(t)
    build(items=[(1, 101), (5, 100), (8, 102), (16, 103)], tables=t)
    assert t == before


def test_needs_lists_every_file_the_recipe_names():
    out = build(items=[(1, 101), (5, 100), (8, 102), (16, 103, {"enchant": 900}), (15, 107)],
                choices=[{"optionId": 87, "choiceId": 1261}, {"optionId": 91, "choiceId": 1280}])
    assert out["needs"] == [8001, 8003, 8100, 8102, 8200, 8300, 8400, 8600, 9001, 9100, 9200, 9300, 9400, 9500,
                            9600]


def test_load_tables_from_a_folder(tmp_path):
    t = c3.load_tables(tmp_path, "1.60.1.70959")
    assert t.build == "1.60.1.70959" and set(t.problems) == set(c3.TABLES)
    assert c3.load_tables(tmp_path, "1.60.1.70959") is t
    assert c3.assemble(t, GNOME, FEMALE, PRIEST, [], [], "hd")["recipe"] is None


@pytest.mark.parametrize("bad", [b"nope", b"WDC5" + b"\0" * 10])
def test_a_broken_table_is_a_problem_not_a_crash(tmp_path, bad):
    (tmp_path / "ChrModel.db2").write_bytes(bad)
    t = c3.load_tables(tmp_path)
    assert "ChrModel" in t.problems and t["ChrModel"] == {}


def test_a_choice_from_another_race_is_dropped():
    t = world()
    t["ChrModel"][77] = {"Sex": 0, "DisplayID": 0, "CharComponentTextureLayoutID": LAYOUT}
    t["ChrCustomizationOption"][700] = {"ChrModelID": 77, "Flags": 0, "OrderIndex": 0, "ChrCustomizationID": 3}
    t["ChrCustomizationChoice"][7001] = {"ChrCustomizationOptionID": 700, "OrderIndex": 0}
    t["ChrCustomizationChoice"][7002] = {"ChrCustomizationOptionID": 700, "OrderIndex": 1}
    saved = [{"optionId": 700, "choiceId": 7002}]
    assert build(choices=saved, tables=t)["recipe"]["geosets"]["set"] == {"2": True, "3": False}
    assert build(choices=saved, models="sd", tables=t)["recipe"]["geosets"]["set"] == {"2": True, "5": False}


# --- Battle.net's look in options Forever's tables don't have -------------------------

# Forever's gnome male "Facial Hair" (9486): each choice a beard (geoset type 1)
# and a mustache (type 3). The ids and geosets are the game's (facts, as the
# 3D tables ship them); the world around them is made up.
FOREVER_FACIAL_HAIR = {78867: (0, 0), 78868: (1, 0), 78869: (2, 1), 78870: (3, 2), 78871: (4, 3),
                       78872: (5, 4), 78873: (6, 5), 78874: (0, 6)}


def bearded_world() -> dict:
    t = world()
    t["ChrCustomizationOption"][9486] = {"ChrModelID": HD, "Flags": 0, "OrderIndex": 4, "ChrCustomizationID": 5,
                                         "Name_lang": "Facial Hair"}
    t["ChrCustomizationOption"][784] = {"ChrModelID": HD, "Flags": 0, "OrderIndex": 5, "ChrCustomizationID": 5,
                                        "Name_lang": "Eyebrows"}
    t["ChrCustomizationOption"][9443] = {"ChrModelID": SD, "Flags": 0, "OrderIndex": 1, "ChrCustomizationID": 5,
                                         "Name_lang": "Facial Hair"}
    n = 100
    for i, (choice, (beard, mustache)) in enumerate(FOREVER_FACIAL_HAIR.items()):
        t["ChrCustomizationChoice"][choice] = {"ChrCustomizationOptionID": 9486, "OrderIndex": i}
        for gtype, gid in ((1, beard), (3, mustache)):
            n += 1
            t["ChrCustomizationGeoset"][n] = {"GeosetType": gtype, "GeosetID": gid}
            t["ChrCustomizationElement"][n] = {"ChrCustomizationChoiceID": choice, "ChrCustomizationGeosetID": n}
    for i, choice in enumerate((8765, 8766, 8767)):
        t["ChrCustomizationChoice"][choice] = {"ChrCustomizationOptionID": 784, "OrderIndex": i}
    for i in range(8):  # the original's facial hair: one more than the updated one's
        t["ChrCustomizationChoice"][95000 + i] = {"ChrCustomizationOptionID": 9443, "OrderIndex": i}
    return t


def facial_hair(out) -> set[int]:
    """The facial hair geosets shown (type * 100 + id)."""
    return {int(g) for g, on in out["recipe"]["geosets"]["set"].items() if on and int(g) // 100 in (1, 3)}


def active(t, choices, model=HD, look=None):
    return dict(c3._active_choices(c3.index(t), model, choices, HD, look))


def test_the_committed_table_has_the_owners_beard_and_mustache():
    look = c3.battlenet_geosets()
    # Battle.net's Beard "Curtain" and Mustache "Styled" (a gnome male's look).
    assert look[1233] == {(1, 6)} and look[8762] == {(3, 5)}
    assert 45134 not in look  # Eyesight shows no geoset: nothing to match


def test_battlenet_beard_and_mustache_are_forevers_facial_hair():
    saved = [{"optionId": 784, "choiceId": 8767}, {"optionId": 84, "choiceId": 1233},
             {"optionId": 783, "choiceId": 8762}, {"optionId": 6350, "choiceId": 45134}]
    got = active(bearded_world(), saved)  # the committed table
    assert got[9486] == 78873 and got[784] == 8767
    out = c3.assemble(bearded_world(), GNOME, FEMALE, PRIEST, saved, [], "hd")
    assert facial_hair(out) == {106, 305}


def test_a_look_choice_matches_the_closest_choice():
    look = {1: frozenset({(1, 4)}), 2: frozenset({(3, 2)})}  # a beard and mustache no Forever choice pairs
    got = active(bearded_world(), [{"optionId": 84, "choiceId": 1}, {"optionId": 783, "choiceId": 2}], look=look)
    assert got[9486] == 78870  # (1,3)+(3,2): one of two right, the first such
    got = active(bearded_world(), [{"optionId": 783, "choiceId": 2}], look=look)
    assert got[9486] == 78870


def test_nothing_matches_draws_the_default_and_says_so(caplog):
    look = {1: frozenset({(1, 99)})}
    with caplog.at_level("DEBUG", logger="wowstats.character3d"):
        got = active(bearded_world(), [{"optionId": 84, "choiceId": 1}], look=look)
    assert got[9486] == 78867
    assert len(caplog.records) == 1 and "[1]" in caplog.records[0].getMessage()
    # An empty table (the file missing) changes nothing either.
    assert active(bearded_world(), [{"optionId": 84, "choiceId": 1}], look={})[9486] == 78867


def test_the_original_model_takes_facial_hair_not_eyebrows():
    """Eyebrows share Facial Hair's kind (5): the original's Facial Hair
    follows the updated Facial Hair, by name."""
    saved = [{"optionId": 784, "choiceId": 8767}, {"optionId": 84, "choiceId": 1233},
             {"optionId": 783, "choiceId": 8762}]
    got = active(bearded_world(), saved, model=SD)
    assert got[9443] == 95006  # Styled is the updated list's 7th, so the original's 7th
    assert c3.classic_gaps(bearded_world(), GNOME, FEMALE, saved) == []


def test_ears_show_unless_a_helmet_or_the_look_says_otherwise():
    t = world()
    t["HelmetGeosetData"][3] = {"HelmetGeosetVisDataID": 77, "RaceID": GNOME, "HideGeosetGroup": 7}
    assert build(items=[(1, 101)], tables=t)["recipe"]["geosets"]["groups"]["7"] == 0
    # A look with an ears option of its own (a geoset of group 7) keeps its choice.
    t = world()
    t["ChrCustomizationGeoset"][44] = {"GeosetType": 7, "GeosetID": 3}
    t["ChrCustomizationElement"][10] = {"ChrCustomizationChoiceID": 1235, "ChrCustomizationGeosetID": 44}
    r = build(tables=t)["recipe"]["geosets"]
    assert r["set"]["703"] is True and "7" not in r["groups"]
