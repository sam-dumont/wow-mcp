"""Exercise the client's joins with small tables that can travel in Git."""

from __future__ import annotations

from wowstats import character3d, spellart


def put(tables, table, rid, **fields):
    # WDC records have every inline column, even when its value is zero.
    row = dict.fromkeys(spellart.TABLES[table], 0)
    tables.setdefault(table, {})[rid] = {**row, **fields, "ID": rid}


def test_ranged_uses_the_equipped_wand_and_its_own_timing():
    tables = {"Item": {11287: {"ClassID": 2, "SubclassID": 19}, 5210: {"ClassID": 2, "SubclassID": 19}}}
    put(tables, "SpellMisc", 318284, SpellID=5019, Speed=20, CastingTimeIndex=1)
    put(tables, "SpellCastTimes", 1, Base=0)
    # Lesser Magic Wand and Burning Wand use different missiles.
    for iid, delay, disp, vid, missile_set, missile, effect, model in [
        (11287, 1500, 378, 2799, 42, 1, 321, 165570),
        (5210, 1400, 370, 225, 43, 2, 188, 166135),
    ]:
        put(tables, "ItemSparse", iid, ItemDelay=delay)
        put(tables, "ItemRangedDisplayInfo", disp, CastSpellVisualID=vid)
        put(tables, "SpellVisual", vid, SpellVisualMissileSetID=missile_set)
        put(tables, "SpellVisualMissile", missile, SpellVisualMissileSetID=missile_set,
            SpellVisualEffectNameID=effect, Attachment=-1, DestinationAttachment=34)
        put(tables, "SpellVisualEffectName", effect, ModelFileDataID=model, Scale=1, Alpha=1)
    catalog = spellart.Catalog(tables)
    for iid, disp, model, delay in [(11287, 378, 165570, 1500), (5210, 370, 166135, 1400)]:
        [action] = catalog.actions(8, 16, {"subclass": 19, "item_id": iid}, disp)
        assert action["spell_id"] == 5019 and action["item_delay_ms"] == delay
        assert spellart.files([action]) == {model}
        assert action["missiles"][0]["destination_attachment"] == 34
    assert not catalog.actions(8, 16, None, None)
    assert not catalog.actions(8, 16, {"subclass": 19, "item_id": 11287}, 999)


def test_rank_cast_time_and_both_hand_effects_follow_the_client_rows():
    tables = {}
    for spell, level, casting, base in [(116, 4, 16, 1500), (205, 8, 19, 1800)]:
        put(tables, "SpellLevels", spell, SpellID=spell, SpellLevel=level)
        put(tables, "SpellMisc", spell, SpellID=spell, Speed=28, CastingTimeIndex=casting)
        put(tables, "SpellCastTimes", casting, Base=base)
        put(tables, "SpellXSpellVisual", spell, SpellID=spell, SpellVisualID=13, Probability=1, Priority=1)
    put(tables, "SpellVisual", 13)
    put(tables, "SpellVisualEvent", 184067, StartEvent=1, EndEvent=2, TargetType=1, SpellVisualID=13,
        SpellVisualKitID=194)
    put(tables, "SpellVisualKit", 194)
    put(tables, "SpellVisualKitEffect", 230822, ParentSpellVisualKitID=194, EffectType=6, Effect=359915)
    put(tables, "SpellVisualAnim", 359915, InitialAnimID=-1, LoopAnimID=51)
    for i, point in enumerate([21, 22]):
        put(tables, "SpellVisualKitEffect", 230823+i, ParentSpellVisualKitID=194, EffectType=2, Effect=367277+i)
        put(tables, "SpellVisualKitModelAttach", 367277+i, SpellVisualEffectNameID=117, AttachmentID=point,
            Offset=[0, 0, 0], Scale=1)
    put(tables, "SpellVisualEffectName", 117, ModelFileDataID=166381, Scale=1, Alpha=1)
    catalog = spellart.Catalog(tables)
    assert catalog.actions(8, 3, None, None) == []
    for level, spell, rank, cast_ms in [(4, 116, 1, 1500), (8, 205, 2, 1800)]:
        [action] = catalog.actions(8, level, None, None)
        assert (action["spell_id"], action["rank"], action["cast_ms"]) == (spell, rank, cast_ms)
        [kit] = action["kits"]
        assert (kit["phase"], kit["animation"]) == ("precast", 51)
        assert [a["attachment"] for a in kit["effects"]] == [21, 22]
        assert spellart.files([action]) == {166381}


def test_still_portrait_does_not_request_an_action_or_missile():
    recipe = {"model": 1, "materials": [], "textures": {}, "attachments": [], "collections": [], "skinned": [],
              "actions": [{"kits": [{"effects": [{"model": 2}], "chains": [{"texture": 3}]}],
                           "missiles": [{"model": 4}]}]}
    assert character3d.needs(recipe) == {1, 2, 3, 4}
    assert character3d.needs(character3d.standing_recipe(recipe)) == {1}


def test_bow_missile_uses_the_recorded_arrow_skin_or_identifies_the_basic_arrow_preview():
    tables = {
        "Item": {11304: {"ClassID": 2, "SubclassID": 2, "AmmunitionType": 2},
                 2512: {"ClassID": 6, "SubclassID": 2}, 18042: {"ClassID": 6, "SubclassID": 2}},
        "ItemModifiedAppearance": {1: {"ItemID": 2512, "ItemAppearanceID": 1},
                                   2: {"ItemID": 18042, "ItemAppearanceID": 2}},
        "ItemAppearance": {1: {"ItemDisplayInfoID": 11}, 2: {"ItemDisplayInfoID": 22}},
        "ItemDisplayInfo": {11: {"ModelResourcesID": [17490]}, 22: {"ModelResourcesID": [17490],
                                                                    "ModelMaterialResourcesID": [50]}},
        "ModelFileData": {137240: {"FileDataID": 137240, "ModelResourcesID": 17490}},
        "TextureFileData": {999: {"FileDataID": 999, "MaterialResourcesID": 50, "UsageType": 0}},
    }
    put(tables, "SpellMisc", 315869, SpellID=2480, Speed=40, CastingTimeIndex=4)
    put(tables, "SpellCastTimes", 4, Base=1000)
    put(tables, "ItemRangedDisplayInfo", 367, CastSpellVisualID=5)
    put(tables, "SpellVisual", 5, SpellVisualMissileSetID=10633)
    put(tables, "SpellVisualMissile", 12572, SpellVisualMissileSetID=10633, Attachment=-1,
        DestinationAttachment=34, Flags=2, SpellVisualEffectNameID=0)
    ix = character3d.Index(tables)
    assembly = character3d._Assembly(ix, 1, 0, 3, 0, [])
    bow = {"subclass": 2, "item_id": 11304}
    for items, ammo_id, source, files in [
        ([], 2512, "preview", {137240}),
        ([{"slot": 0, "item_id": 18042}], 18042, "equipped", {137240, 999}),
    ]:
        actions = ix.spell_art.actions(3, 1, bow, 367, lambda rows=items: assembly.ammunition(bow, rows))
        [missile] = actions[0]["missiles"]
        assert (missile["model"], missile["ammo_item_id"], missile["ammo_source"]) == (137240, ammo_id, source)
        assert spellart.files(actions) == files
