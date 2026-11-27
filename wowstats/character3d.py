"""How a character is put together for the 3D view, worked out from
Forever's own client tables: which model, which of its geosets show, the
body textures composited layer by layer, the items' models and where they
go. The answer is one JSON "recipe" (docs: the 3D view's recipe, v1) that
the viewer draws by loading files by id; `needs` lists those files.

A port of wow.export's character assembly (github.com/Kruithne/wow.export,
MIT, THIRD_PARTY.md): modules/tab_characters.js (update_geosets,
hide_skinned_model_base_geosets, update_textures, update_equipment_models,
update_skinned_models, load_character_model, check_cond_model_swap,
SLOT_TO_GEOSET_GROUPS), ui/character-appearance.js and the db/caches/
DB*.js that feed them, with these differences (each says why):

- a choice's elements are all applied (wow.export keeps one geoset per
  choice: Forever's "Styled" facial hair is a beard and a mustache), each
  one gated by its RelatedChrCustomizationChoiceID like the materials;
- an item's appearance is its base one (modifier 0, then the lowest order),
  as wowstats.model3d reads it;
- each item model gets its own textures (ItemDisplayInfoModelMatRes by
  ModelIndex, else ModelMaterialResourcesID), and a cape without a model
  textures the body's cape (M2 texture type 2);
- shoulders whose two model lists differ put the first on the left
  shoulder and the second on the right; shields go on the shield point (0);
- ears (geoset group 7) show by default (702), unless the look has its
  own ears option or a helmet hides them (wow.export leaves 701, no ears);
- glows: ItemDisplayInfo.ItemVisual and an enchant's
  SpellItemEnchantment.ItemVisual -> ItemVisualsXEffect (wow.export has none);
- the original ("Classic") models: Forever has them as ChrModel rows of
  their own (CharComponentTextureLayout 203, their own options) that no
  table ties to a race: CLASSIC_DISPLAYS does, by their display ids.

`tables` is a mapping table name -> rows ({id: {column: value}}), so tests
pass plain dicts; load_tables builds it from the client tables one person's
shipper sent (wowstats.assets), parsed once per build.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import struct
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path

from wowstats import db2, spellart
from wowstats.mounts import catalog as mount_catalog

log = logging.getLogger(__name__)

RECIPE_VERSION = 1

# Battle.net reports a look in the current game's ids; Forever's tables lack
# some of its options (a gnome's Beard 84 and Mustache 783 are one "Facial
# Hair" 9486 there, each choice a beard and a mustache). This table, made
# once from the current game's tables (tools/battlenet_look/export.py),
# says which geosets such a choice shows; _active_choices draws the Forever
# choice that shows the same ones.
_HERE = Path(__file__).resolve().parent
LOOK_FILES = (_HERE / "data" / "battlenet-look.json", _HERE.parent / "data" / "battlenet-look.json")


@functools.cache
def battlenet_geosets() -> dict[int, frozenset[tuple[int, int]]]:
    """Battle.net choice id -> the (geoset type, geoset id) it shows, for the
    options Forever's tables don't have; empty when the file is missing."""
    for f in LOOK_FILES:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return {int(k): frozenset((int(t), int(g)) for t, g in v) for k, v in (doc.get("choices") or {}).items()}
    return {}

# The tables the assembly reads, with the columns it reads (db2.Table keeps
# only those: ModelFileData alone is 120,000 rows).
TABLES: dict[str, tuple[str, ...]] = {
    "CharComponentTextureSections": ("CharComponentTextureLayoutID", "SectionType", "X", "Y", "Width", "Height"),
    "ChrCustomizationChoice": ("ChrCustomizationOptionID", "OrderIndex"),
    "ChrCustomizationCondModel": ("CreatureModelDataID",),
    "ChrCustomizationElement": ("ChrCustomizationChoiceID", "RelatedChrCustomizationChoiceID",
                                "ChrCustomizationGeosetID", "ChrCustomizationSkinnedModelID",
                                "ChrCustomizationMaterialID", "ChrCustomizationCondModelID"),
    "ChrCustomizationGeoset": ("GeosetType", "GeosetID"),
    "ChrCustomizationMaterial": ("ChrModelTextureTargetID", "MaterialResourcesID"),
    "ChrCustomizationOption": ("ChrModelID", "Flags", "OrderIndex", "ChrCustomizationID", "Name_lang"),
    "ChrCustomizationSkinnedModel": ("CollectionsFileDataID", "GeosetType", "GeosetID"),
    "ChrModel": ("Sex", "DisplayID", "CharComponentTextureLayoutID"),
    "ChrModelMaterial": ("CharComponentTextureLayoutsID", "TextureType", "Width", "Height"),
    "ChrModelTextureLayer": ("CharComponentTextureLayoutsID", "ChrModelTextureTargetID", "TextureType", "BlendMode",
                             "TextureSectionTypeBitMask"),
    "ChrRaces": ("ClientFileString", "Name_lang", "MaleModelFallbackRaceID", "FemaleModelFallbackRaceID",
                 "MaleTextureFallbackRaceID", "FemaleTextureFallbackRaceID"),
    "ChrRaceXChrModel": ("ChrRacesID", "ChrModelID", "Sex"),
    "ComponentModelFileData": ("GenderIndex", "ClassID", "RaceID", "PositionIndex"),
    "ComponentTextureFileData": ("GenderIndex", "ClassID", "RaceID"),
    "CreatureDisplayInfo": ("ModelID", "TextureVariationFileDataID", "CreatureModelScale"),
    "CreatureModelData": ("FileDataID", "ModelScale"),
    "HelmetGeosetData": ("RaceID", "HideGeosetGroup", "HelmetGeosetVisDataID"),
    "Item": ("ClassID", "SubclassID", "InventoryType", "AmmunitionType"),
    "ItemAppearance": ("ItemDisplayInfoID",),
    "ItemDisplayInfo": ("ItemRangedDisplayInfoID", "ItemVisual", "ModelResourcesID", "ModelMaterialResourcesID",
                        "GeosetGroup", "AttachmentGeosetGroup", "HelmetGeosetVis"),
    "ItemDisplayInfoMaterialRes": ("ComponentSection", "MaterialResourcesID", "ItemDisplayInfoID"),
    "ItemDisplayInfoModelMatRes": ("MaterialResourcesID", "TextureType", "ModelIndex", "ItemDisplayInfoID"),
    "ItemModifiedAppearance": ("ItemID", "ItemAppearanceModifierID", "ItemAppearanceID", "OrderIndex"),
    "ItemVisuals": ("ModelFileID",),
    "ItemVisualsXEffect": ("AttachmentID", "DisplayWeaponSubclassID", "AttachmentModelFileID", "ItemVisualsID"),
    "ModelFileData": ("FileDataID", "ModelResourcesID"),
    "Mount": ("Name_lang", "SourceSpellID"),
    "MountXDisplay": ("MountID", "CreatureDisplayInfoID", "PlayerConditionID"),
    **spellart.TABLES,
    "SpellItemEnchantment": ("ItemVisual",),
    "TextureFileData": ("FileDataID", "UsageType", "MaterialResourcesID"),
}

# --- the game's constants (wow.export's tables) ---------------------------------

SKIN, SKIN_EXTRA, CAPE = 1, 8, 2  # M2 texture types: body skin, its extra (Mechagnome metal), cape/object
HIDDEN_OPTION = 0x20  # ChrCustomizationOption.Flags: not given a default choice

# Character geoset groups (group * 100 + value is a geoset id).
HELM, SKULL, SLEEVES, CHEST, TROUSERS, TORSO, ARM_UPPER = 27, 21, 8, 10, 13, 22, 28
BELT, PANTS, KNEEPADS, BOOTS, FEET, GLOVES, HAND_ATTACHMENT = 18, 11, 9, 5, 20, 4, 23
CLOAK, TABARD, SHOULDERS = 15, 12, 26

# Inventory slot -> (index in ItemDisplayInfo.GeosetGroup, character geoset group): DBItemGeosets'
# SLOT_GEOSET_MAPPING. Feet's second entry is special (FEET below).
# Ears (geoset group 7): 701 is the bare head a helmet leaves, 702 the
# ears. The viewer's default (geosets ending in 01) is the bare head, so a
# race whose look has no ears option of its own gets 702, as in game; a
# helmet that hides them sets the group to 0 (equipment).
EARS, EARS_SHOWN = 7, 2

SLOT_GEOSETS: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((0, HELM), (1, SKULL)),
    3: ((0, SHOULDERS),),
    4: ((0, SLEEVES), (1, CHEST)),
    5: ((0, SLEEVES), (1, CHEST), (2, TROUSERS), (3, TORSO), (4, ARM_UPPER)),
    6: ((0, BELT),),
    7: ((0, PANTS), (1, KNEEPADS), (2, TROUSERS)),
    8: ((0, BOOTS), (1, FEET)),
    9: (),
    10: ((0, GLOVES), (1, HAND_ATTACHMENT)),
    15: ((0, CLOAK),),
    19: ((0, TABARD),),
}
# Which slot wins a group two slots set (first wins): DBItemGeosets' GEOSET_PRIORITY.
GEOSET_PRIORITY = {SLEEVES: (10, 5, 4), CHEST: (5, 4), TROUSERS: (5, 7)}
# A collection model's geosets (AttachmentGeosetGroup index -> group): tab_characters'
# SLOT_TO_GEOSET_GROUPS.
COLLECTION_GEOSETS: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((0, HELM), (1, SKULL)),
    5: ((0, SLEEVES), (1, CHEST), (2, TROUSERS), (3, TORSO), (4, ARM_UPPER)),
    6: ((0, BELT),),
    7: ((0, PANTS), (1, KNEEPADS), (2, TROUSERS)),
    8: ((0, BOOTS), (1, FEET)),
    10: ((0, GLOVES), (1, HAND_ATTACHMENT)),
    15: ((0, CLOAK),),
}
# Texture layer per slot: an item's layers are slot_layer * 100 + section (EquipmentSlots' SLOT_LAYER).
SLOT_LAYER = {4: 10, 7: 10, 1: 11, 8: 11, 3: 13, 5: 13, 19: 17, 6: 18, 9: 19, 10: 20, 16: 21, 17: 22, 15: 23}
SECTIONS = range(9)  # CharComponentTextureSections.SectionType an item can dress: arms to feet, accessory

# M2 attachment points.
SHIELD, HAND_RIGHT, HAND_LEFT, SHOULDER_RIGHT, SHOULDER_LEFT, HELMET, BACK = 0, 1, 2, 5, 6, 11, 12
SLOT_ATTACH = {1: (HELMET,), 15: (BACK,)}

# What wowstats.model3d already shows, kept: inventory types drawn (armor and held), the slots worn but
# never drawn (neck, fingers, trinkets), the hand each held type takes, the ranged slot's wands.
ARMOR = {1, 3, 4, 5, 6, 7, 8, 9, 10, 16, 19, 20}
HELD = {13, 14, 15, 17, 21, 22, 23, 25, 26}
UNSEEN_SLOTS = {2, 11, 12, 13, 14}
RIGHT, LEFT = 21, 22
# Item.SubclassID for bows, guns, thrown weapons, crossbows and wands.
RANGED_HAND = {2: LEFT, 3: RIGHT, 16: RIGHT, 18: RIGHT, 19: RIGHT}
# The main hand's item is in the right hand, the off hand's in the left (a one-hander there too);
# Blizzard's renders, 2026-11-20 (tools/3dcases: four dual-wielders).
SLOT_HAND = {16: RIGHT, 17: LEFT}
INV_SHIELD, INV_2H = 14, 17
WEAPON, WAND = 2, 19
RANGED_SLOT = 18
# The swing the character's right hand makes (AnimationData ids), as the game picks it by weapon:
# AttackUnarmed with nothing there, Attack1H, Attack2H, and Attack2HL ("loose") for staves,
# polearms and fishing poles (ItemSubClass 10, 6, 20). The 3D view plays it now and then (web/model3d).
ATTACK_UNARMED, ATTACK_1H, ATTACK_2H, ATTACK_2HL = 16, 17, 18, 19
LOOSE = {6, 10, 20}

# The original models: ChrModel rows with these display ids (Forever's tables give them their own
# options and the texture layout 203, but tie none of them to a race; the game picks them in code).
# Race -> (male, female) CreatureDisplayInfo ids. 1-8: the 2004 models (ChrModel 257-272). The
# Skyborne (95/96) have none: Blizzard's Classic render of them is their Updated model
# (tools/3dcases c22, c23); ChrModel 277/278 on layout 203 draw a goblin.
CLASSIC_DISPLAYS = {1: (49, 50), 2: (51, 52), 3: (53, 54), 4: (55, 56), 5: (57, 58), 6: (59, 60),
                    7: (1563, 1564), 8: (1478, 1479)}

GENDER_ANY_MODEL, GENDER_ANY_TEXTURE = 2, 3  # ComponentModelFileData / ComponentTextureFileData

# The tables the assembly looks rows up in; the others are only folded into the Index's maps.
LOOKED_UP = ("ChrModel", "CreatureDisplayInfo", "CreatureModelData", "ChrRaces", "ComponentTextureFileData",
             "ComponentModelFileData", "ChrCustomizationOption", "ChrCustomizationGeoset", "ChrCustomizationMaterial",
             "ChrCustomizationCondModel", "ChrCustomizationSkinnedModel", "Item", "ItemDisplayInfo", "ItemVisuals",
             "SpellItemEnchantment")


class Index:
    """The lookups the assembly needs, built once per set of tables."""

    def __init__(self, tables: Mapping[str, Mapping[int, dict]]):
        def t(name: str) -> Mapping[int, dict]:
            return tables.get(name) or {}

        self.spell_art = spellart.Catalog(tables)
        self.mounts = mount_catalog(tables)
        self.race_models: dict[tuple[int, int], int] = {}
        for r in t("ChrRaceXChrModel").values():
            self.race_models.setdefault((r.get("ChrRacesID", 0), r.get("Sex", 0)), r.get("ChrModelID", 0))
        self.model_by_display = {r.get("DisplayID"): mid for mid, r in sorted(t("ChrModel").items())}
        self.options: dict[int, list[tuple[int, dict]]] = {}
        for oid, r in sorted(t("ChrCustomizationOption").items(), key=lambda x: (x[1].get("OrderIndex", 0), x[0])):
            self.options.setdefault(r.get("ChrModelID", 0), []).append((oid, r))
        self.choices: dict[int, list[int]] = {}
        self.choice_option: dict[int, int] = {}
        for cid, r in sorted(t("ChrCustomizationChoice").items(), key=lambda x: (x[1].get("OrderIndex", 0), x[0])):
            self.choices.setdefault(r.get("ChrCustomizationOptionID", 0), []).append(cid)
            self.choice_option[cid] = r.get("ChrCustomizationOptionID", 0)
        self.elements: dict[int, list[dict]] = {}
        for _eid, r in sorted(t("ChrCustomizationElement").items()):
            self.elements.setdefault(r.get("ChrCustomizationChoiceID", 0), []).append(r)
        geoset_rows = t("ChrCustomizationGeoset")
        self.choice_geosets: dict[int, frozenset[tuple[int, int]]] = {}  # the ones no other choice gates
        for cid, els in self.elements.items():
            gs = {(g.get("GeosetType", 0), g.get("GeosetID", 0)) for e in els
                  if not e.get("RelatedChrCustomizationChoiceID")
                  for g in [geoset_rows.get(e.get("ChrCustomizationGeosetID") or 0)] if g}
            if gs:
                self.choice_geosets[cid] = frozenset(gs)
        self.textures: dict[int, list[int]] = {}  # MaterialResourcesID -> texture files (UsageType 0)
        for fid, r in sorted(t("TextureFileData").items()):
            if r.get("UsageType", 0) == 0:
                self.textures.setdefault(r.get("MaterialResourcesID", 0), []).append(r.get("FileDataID") or fid)
        self.models: dict[int, list[int]] = {}  # ModelResourcesID -> model files
        for fid, r in sorted(t("ModelFileData").items()):
            self.models.setdefault(r.get("ModelResourcesID", 0), []).append(r.get("FileDataID") or fid)
        self.layers: dict[tuple[int, int], dict] = {}
        self.layout_layers: dict[int, list[dict]] = {}
        for _lid, r in sorted(t("ChrModelTextureLayer").items()):
            target = r.get("ChrModelTextureTargetID")
            target = target[0] if isinstance(target, list) else target
            key = (r.get("CharComponentTextureLayoutsID", 0), target)
            self.layers.setdefault(key, r)
            self.layout_layers.setdefault(key[0], []).append(r)
        self.materials = {(r.get("CharComponentTextureLayoutsID", 0), r.get("TextureType", 0)): r
                          for _i, r in sorted(t("ChrModelMaterial").items())}
        self.sections: dict[int, list[dict]] = {}
        for _sid, r in sorted(t("CharComponentTextureSections").items()):
            self.sections.setdefault(r.get("CharComponentTextureLayoutID", 0), []).append(r)
        self.appearance: dict[int, int] = {}  # item -> ItemDisplayInfo id
        apps = t("ItemAppearance")
        for _i, r in sorted(t("ItemModifiedAppearance").items(),
                            key=lambda x: (x[1].get("ItemAppearanceModifierID", 0), x[1].get("OrderIndex", 0), x[0])):
            display = (apps.get(r.get("ItemAppearanceID", 0)) or {}).get("ItemDisplayInfoID", 0)
            if display and r.get("ItemID", 0) not in self.appearance:
                self.appearance[r.get("ItemID", 0)] = display
        self.body_textures: dict[int, list[dict]] = {}
        for _i, r in sorted(t("ItemDisplayInfoMaterialRes").items()):
            self.body_textures.setdefault(r.get("ItemDisplayInfoID", 0), []).append(r)
        self.model_textures: dict[int, list[dict]] = {}
        for _i, r in sorted(t("ItemDisplayInfoModelMatRes").items()):
            self.model_textures.setdefault(r.get("ItemDisplayInfoID", 0), []).append(r)
        self.helmet_hides: dict[tuple[int, int], list[int]] = {}
        for _i, r in sorted(t("HelmetGeosetData").items()):
            self.helmet_hides.setdefault((r.get("HelmetGeosetVisDataID", 0), r.get("RaceID", 0)), []).append(
                r.get("HideGeosetGroup", 0))
        self.effects: dict[int, list[dict]] = {}
        for _i, r in sorted(t("ItemVisualsXEffect").items()):
            self.effects.setdefault(r.get("ItemVisualsID", 0), []).append(r)
        # Only the tables looked up row by row stay referenced: the rest are folded in above.
        self._rows = {name: t(name) for name in LOOKED_UP}

    def t(self, name: str) -> Mapping[int, dict]:
        return self._rows.get(name) or {}

    # -- the model

    def model_file(self, chr_model: int) -> int | None:
        row = self.t("ChrModel").get(chr_model)
        display = self.t("CreatureDisplayInfo").get((row or {}).get("DisplayID", 0))
        return self.creature_model_file((display or {}).get("ModelID", 0))

    def creature_model_file(self, model_data: int) -> int | None:
        return (self.t("CreatureModelData").get(model_data) or {}).get("FileDataID") or None

    def fallback_race(self, race: int, sex: int, kind: str) -> int:
        row = self.t("ChrRaces").get(race) or {}
        return row.get(f"{'Female' if sex else 'Male'}{kind}FallbackRaceID", 0) or 0

    # -- picking a file among a resource's variants

    def pick_texture(self, files: list[int], race: int, sex: int, cls: int, fallback: int = 0) -> int | None:
        """DBComponentTextureFileData.getTextureForRaceGender: drop the other
        gender and other classes, then prefer this gender, this class, this
        race (then the fallback race)."""
        if not files:
            return None
        if len(files) == 1:
            return files[0]
        info = self.t("ComponentTextureFileData")
        tagged = [(f, info[f]) for f in files if f in info]
        if not tagged:
            return files[0]
        ok = [(f, i) for f, i in tagged
              if i.get("GenderIndex") in (sex, GENDER_ANY_TEXTURE) and i.get("ClassID", 0) in (0, cls)]
        if not ok:
            return next((f for f in files if f not in info), None)

        def rank(x):
            i = x[1]
            race_rank = 0 if i.get("RaceID") == race else 1 if fallback and i.get("RaceID") == fallback else 2
            return (i.get("GenderIndex") != sex, not (cls and i.get("ClassID") == cls), race_rank)

        return min(ok, key=rank)[0]

    def pick_model(self, files: list[int], race: int, sex: int, fallback: int = 0, position: int | None = None
                   ) -> int | None:
        """DBComponentModelFileData.getModelForRaceGender (and, with
        `position`, the left or right shoulder's ...ByPosition)."""
        info = self.t("ComponentModelFileData")
        if position is not None:
            files = [f for f in files if (info.get(f) or {}).get("PositionIndex") == position]
        if not files:
            return None
        if len(files) == 1 and position is None:
            return files[0]

        def find(test):
            return next((f for f in files if f in info and test(info[f])), None)

        return (find(lambda i: i.get("RaceID") == race and i.get("GenderIndex") == sex)
                or find(lambda i: i.get("RaceID") == race and i.get("GenderIndex") == GENDER_ANY_MODEL)
                or (fallback and find(lambda i: i.get("RaceID") == fallback
                                      and i.get("GenderIndex") in (sex, GENDER_ANY_MODEL)))
                or find(lambda i: i.get("RaceID") == 0)
                or files[0])


_INDEX_LOCK = threading.Lock()


def index(tables: Mapping[str, Mapping[int, dict]]) -> Index:
    """The tables' Index: kept on a Tables (load_tables), made for a plain dict."""
    if isinstance(tables, Tables):
        with _INDEX_LOCK:
            if tables.index is None:
                tables.index = Index(tables)
            return tables.index
    return Index(tables)


# --- the character's race, class and original look ---------------------------------

# ChrClasses ids: the game's own constants (wow.export's too).
CLASS_IDS = {"WARRIOR": 1, "PALADIN": 2, "HUNTER": 3, "ROGUE": 4, "PRIEST": 5, "DEATHKNIGHT": 6, "SHAMAN": 7,
             "MAGE": 8, "WARLOCK": 9, "MONK": 10, "DRUID": 11}


def class_id(name) -> int:
    """A class as the game names it ("Priest", "PRIEST", "Death Knight") ->
    its ChrClasses id, 0 when unknown (items then pick no class variant)."""
    return CLASS_IDS.get("".join(str(name or "").split()).replace("_", "").upper(), 0)


def _key(s) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def race_id(tables: Mapping[str, Mapping[int, dict]], race) -> int | None:
    """A race as the game saves it ("Night Elf", "Undead", "Skyborne") -> its
    ChrRaces id, read from the person's own table: its Name_lang or its
    ClientFileString (NightElf, Scourge), spaces and case aside. Only races
    with a character model count (the table also lists creatures' races).
    Forever's two Skyborne races share the name's end ("High Order
    Skyborne"): a plain "Skyborne" takes the first."""
    want = _key(race)
    if not want:
        return None
    ix = index(tables)
    playable = {r for r, _sex in ix.race_models}
    rows = sorted((rid, row) for rid, row in ix.t("ChrRaces").items() if rid in playable)
    for rid, row in rows:
        if want in (_key(row.get("Name_lang")), _key(row.get("ClientFileString"))):
            return rid
    for rid, row in rows:
        if _key(row.get("Name_lang")).endswith(want):
            return rid
    return None


def classic_gaps(tables: Mapping[str, Mapping[int, dict]], race_id: int, sex: int,
                 choices: Iterable[dict] | None, look_geosets: Mapping[int, frozenset] | None = None) -> list[str]:
    """The options (their names, "Hair Style") whose choice on the updated
    model is past the end of the original model's list for the same option:
    the Classic recipe draws its last choice there instead (_active_choices),
    so the page says the look isn't this character's on that model (a gnome
    male's "Genius" hair). Empty when the race has no original model."""
    ix = index(tables)
    sex = 1 if sex == 1 else 0
    updated = ix.race_models.get((race_id, sex))
    display = CLASSIC_DISPLAYS.get(race_id, (None, None))[sex]
    original = ix.model_by_display.get(display) if display else None
    if updated is None or original is None:
        return []
    hd = _active_choices(ix, updated, choices or [], None, look_geosets)
    out: list[str] = []
    for _mine, choices_there, src, at in _onto_original(ix, original, hd):
        if at >= len(choices_there):
            name = str((ix.t("ChrCustomizationOption").get(src) or {}).get("Name_lang") or "").strip()
            if name and name not in out:
                out.append(name)
    return out


# --- the assembly ---------------------------------------------------------------

def assemble(tables: Mapping[str, Mapping[int, dict]], race_id: int, sex: int, class_id: int,
             choices: Iterable[dict] | None, items: Iterable[dict] | None, models: str = "hd",
             look_geosets: Mapping[int, frozenset] | None = None, *, level: int = 1) -> dict:
    """The recipe for one character.

    race_id: ChrRaces id; sex: 0 male, 1 female; class_id: ChrClasses id;
    choices: Battle.net's customization, [{optionId, choiceId}] (another
    model's or unknown ones are dropped, the rest default); items: what is
    worn, [{slot, item_id}] with slot the inventory slot 1-19 (optional:
    `name`, said when a weapon loses its hand; `enchant`, a
    SpellItemEnchantment id for its glow); models: "hd" or "sd" (the
    original model where Forever has one, `classic` says which was used);
    look_geosets: Battle.net choice -> geosets for the options Forever lacks
    (battlenet_geosets, the default; tests pass their own).

    Returns {"recipe", "needs": [file ids the recipe names], "missing":
    [{slot, item_id, why}], "classic": bool}; recipe None when the tables
    have no model for this race and sex."""
    ix = index(tables)
    sex = 1 if sex == 1 else 0
    chr_model, classic = None, False
    if models == "sd":
        display = CLASSIC_DISPLAYS.get(race_id, (None, None))[sex]
        chr_model = ix.model_by_display.get(display) if display else None
        classic = chr_model is not None
    if chr_model is None:
        chr_model = ix.race_models.get((race_id, sex))
    model_file = ix.model_file(chr_model) if chr_model else None
    if not model_file:
        return {"recipe": None, "needs": [], "missing": [], "classic": False,
                "why": f"no character model for race {race_id} in the client tables"}
    layout = (ix.t("ChrModel").get(chr_model) or {}).get("CharComponentTextureLayoutID", 0)
    active = _active_choices(ix, chr_model, choices or [], ix.race_models.get((race_id, sex)), look_geosets)
    a = _Assembly(ix, race_id, sex, class_id, layout, active)
    model_file = a.cond_model() or model_file
    a.customization()
    if not any(g // 100 == EARS for g in a.set):
        a.groups[EARS] = EARS_SHOWN
    a.skinned_models()
    items = list(items or ())
    worn, missing = _worn(ix, items, class_id)
    a.equipment(worn)
    ranged = next((w for w in worn if w["ranged"]), None)
    recipe = {
        "v": RECIPE_VERSION,
        "model": model_file,
        "geosets": {"set": {str(k): v for k, v in a.set.items()},
                    "groups": {str(k): v for k, v in a.groups.items()}},
        "materials": a.material_list(),
        "textures": {str(k): v for k, v in sorted(a.direct.items())},
        "skinned": a.skinned,
        "attachments": a.attachments,
        "collections": a.collections,
        "animation": 0,
        "attack": attack_of(worn),
        "class_id": class_id,
        "ranged": {"kind": ranged["subclass"], "at_rest": ranged["at_rest"]} if ranged else None,
    }
    ranged_display = ix.t("ItemDisplayInfo")[ranged["display"]].get("ItemRangedDisplayInfoID") if ranged else None
    if actions := ix.spell_art.actions(class_id, level, ranged, ranged_display, lambda: a.ammunition(ranged, items)):
        recipe["actions"] = actions
    return {"recipe": recipe, "needs": sorted(_needs(recipe)), "missing": missing, "classic": classic}


def attack_of(worn: list[dict]) -> int:
    """The attack animation for what the right hand holds (worn: _worn's rows)."""
    r = next((w for w in worn if w.get("hand") == RIGHT and not w.get("ranged")), None)
    if r is None:
        return ATTACK_UNARMED
    if r["inv"] == INV_2H:
        return ATTACK_2HL if r.get("class") == WEAPON and r.get("subclass") in LOOSE else ATTACK_2H
    return ATTACK_1H


def standing_recipe(recipe: dict) -> dict:
    """The single-character portrait uses the weapons visible on Stand."""
    still = dict(recipe)
    ranged = still.pop("ranged", None)
    still.pop("class_id", None)
    still.pop("level", None)
    still.pop("actions", None)
    still.pop("companion", None)
    still.pop("mounts", None)
    if isinstance(ranged, dict) and ranged.get("at_rest") is False:
        still["attachments"] = [a for a in still.get("attachments", []) if a["slot"] != RANGED_SLOT]
    return still


def _active_choices(ix: Index, chr_model: int, saved: Iterable[dict], updated: int | None,
                    look_geosets: Mapping[int, frozenset] | None = None) -> list[tuple[int, int]]:
    """(option, choice) for each of the model's options: the saved choice
    when the model has it; else, for a choice Forever's tables lack
    (look_geosets says which geosets it shows), the model's choice showing
    the most of those geosets and the fewest others (Battle.net's Beard
    "Curtain" and Mustache "Styled" are Forever's Facial Hair "Styled");
    else the first choice (options flagged hidden get none, as in
    wow.export). Any other model's choice is dropped (a race change).

    On the original model (`updated` given and not this one): the updated
    model's look (Battle.net only knows that one), option by option by name
    (Facial Hair is Facial Hair; Eyebrows share its kind), else by kind
    (ChrCustomizationID) when only one option has it, the choice at the
    same place in the list, the last one when the original has fewer."""
    options = ix.options.get(chr_model, [])
    if updated is not None and updated != chr_model:
        hd = _active_choices(ix, updated, saved, None, look_geosets)
        picked = {mine: there[min(at, len(there) - 1)] for mine, there, _src, at in _onto_original(ix, chr_model, hd)}
        return _with_defaults(ix, options, picked)
    look = battlenet_geosets() if look_geosets is None else look_geosets
    own = {oid for oid, _r in options}
    picked: dict[int, int] = {}
    wanted: set[tuple[int, int]] = set()
    left: list[int] = []
    for s in saved:
        try:
            opt, choice = int(s["optionId"]), int(s["choiceId"])
        except (KeyError, TypeError, ValueError):
            continue
        if opt in own and choice in ix.choices.get(opt, ()):
            picked[opt] = choice
        elif look.get(choice):
            wanted |= look[choice]
            left.append(choice)
    shown: set[tuple[int, int]] = set()
    for oid, _r in options:
        if oid in picked or not wanted:
            continue
        best = None
        for ch in ix.choices.get(oid, []):
            gs = ix.choice_geosets.get(ch, frozenset())
            hit = len(gs & wanted)
            if hit and (best is None or hit - len(gs - wanted) > best[0]):
                best = (hit - len(gs - wanted), ch, gs & wanted)
        if best:
            picked[oid] = best[1]
            shown |= best[2]
    if wanted - shown:
        log.debug("look: choices %s show geosets %s no option of model %s has; its defaults drawn",
                  sorted(left), sorted(wanted - shown), chr_model)
    return _with_defaults(ix, options, picked)


def _with_defaults(ix: Index, options: list[tuple[int, dict]], picked: Mapping[int, int]) -> list[tuple[int, int]]:
    out = []
    for oid, r in options:
        if oid in picked:
            out.append((oid, picked[oid]))
        elif not r.get("Flags", 0) & HIDDEN_OPTION and ix.choices.get(oid):
            out.append((oid, ix.choices[oid][0]))
    return out


def _onto_original(ix: Index, original: int, hd: list[tuple[int, int]]):
    """For each of the original model's options that one of the updated
    look's matches: (it, its choices, the updated option, the place of the
    updated choice in its list). Shared by _active_choices and classic_gaps
    so the drawing and the note never disagree."""
    rows = ix.t("ChrCustomizationOption")

    def name(oid):
        return str((rows.get(oid) or {}).get("Name_lang") or "").strip().lower()

    by_name = {name(o): (o, c) for o, c in hd if name(o)}
    kinds: dict[int, list[tuple[int, int]]] = {}
    for o, c in hd:
        kinds.setdefault((rows.get(o) or {}).get("ChrCustomizationID"), []).append((o, c))
    for oid, r in ix.options.get(original, []):
        there = ix.choices.get(oid)
        same_kind = kinds.get(r.get("ChrCustomizationID")) if r.get("ChrCustomizationID") else None
        src = by_name.get(name(oid)) or (same_kind[0] if same_kind and len(same_kind) == 1 else None)
        if there and src:
            yield oid, there, src[0], ix.choices[src[0]].index(src[1])


def worn(tables: Mapping[str, Mapping[int, dict]], items: Iterable[dict] | None, class_id: int
         ) -> tuple[list[dict], list[dict]]:
    """The items assemble draws ({slot, item_id, ...}) and the ones it can't
    ({slot, item_id, why}), without assembling anything."""
    return _worn(index(tables), items or [], class_id)


def _worn(ix: Index, items: Iterable[dict], class_id: int) -> tuple[list[dict], list[dict]]:
    """The items drawn (with their display and hand) and the ones that
    can't be. Keep the ranged weapon for firing even when the standing
    pose uses both hands for melee (tools/3dcases)."""
    rows, missing = [], []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            slot, item_id = int(it.get("slot") or 0), int(it.get("item_id") or 0)
        except (TypeError, ValueError):
            continue
        if slot in UNSEEN_SLOTS or not item_id:
            continue
        row = ix.t("Item").get(item_id)
        if row is None:
            missing.append({"slot": slot, "item_id": item_id, "why": "not in Forever's item tables"})
            continue
        inv, item_class, sub = row.get("InventoryType", 0), row.get("ClassID", 0), row.get("SubclassID", 0)
        if inv not in ARMOR and inv not in HELD:
            continue
        if slot == RANGED_SLOT and (item_class != WEAPON or sub not in RANGED_HAND or inv not in {15, 25, 26}):
            continue
        display = ix.appearance.get(item_id)
        if not display or display not in ix.t("ItemDisplayInfo"):
            missing.append({"slot": slot, "item_id": item_id, "why": "no appearance in Forever's item tables"})
            continue
        rows.append({"slot": slot, "item_id": item_id, "display": display, "inv": inv, "subclass": sub,
                     "class": item_class, "name": it.get("name"), "enchant": it.get("enchant"),
                     "ranged": slot == RANGED_SLOT})
    rows.sort(key=lambda r: (r["ranged"], r["slot"]))
    held: dict[int, dict] = {}
    out = []
    for r in rows:
        hand = SLOT_HAND.get(r["slot"]) if r["inv"] in HELD else None
        if r["ranged"]:
            hand = RANGED_HAND[r["subclass"]]
            r["hand"] = hand
            r["at_rest"] = hand not in held and r["subclass"] != WAND
        elif hand:
            if hand in held:
                other = held[hand].get("name") or "another weapon"
                missing.append({"slot": r["slot"], "item_id": r["item_id"], "why": f"{other} is in that hand"})
                continue
            held[hand] = r
            r["hand"] = hand
            if r["inv"] == INV_2H:
                held.setdefault(LEFT if hand == RIGHT else RIGHT, r)
        out.append(r)
    out.sort(key=lambda r: r["slot"])
    missing.sort(key=lambda m: m["slot"])
    return out, missing


class _Assembly:
    def __init__(self, ix: Index, race: int, sex: int, cls: int, layout: int, active: list[tuple[int, int]]):
        self.ix, self.race, self.sex, self.cls, self.layout = ix, race, sex, cls, layout
        self.active = active
        self.chosen = {c for _o, c in active}
        self.set: dict[int, bool] = {}
        self.groups: dict[int, int] = {}
        self.layers: dict[int, list[dict]] = {}  # texture type -> layers
        self.direct: dict[int, int] = {}
        self.raw: dict[int, int] = {}  # non-skin texture types' source files (skinned models bind them)
        self.skinned: list[dict] = []
        self.attachments: list[dict] = []
        self.collections: list[dict] = []
        self.model_fallback = ix.fallback_race(race, sex, "Model")
        self.texture_fallback = ix.fallback_race(race, sex, "Texture")

    def elements(self, choice: int) -> list[dict]:
        """A choice's elements that apply: those tied to another choice only
        when that one is chosen too."""
        return [e for e in self.ix.elements.get(choice, [])
                if not e.get("RelatedChrCustomizationChoiceID") or e["RelatedChrCustomizationChoiceID"] in self.chosen]

    def geoset(self, geoset_row_id: int) -> int | None:
        row = self.ix.t("ChrCustomizationGeoset").get(geoset_row_id)
        return row.get("GeosetType", 0) * 100 + row.get("GeosetID", 0) if row else None

    def texture(self, material_resources: int) -> int | None:
        return self.ix.pick_texture(self.ix.textures.get(material_resources, []), self.race, self.sex, self.cls,
                                    self.texture_fallback)

    # -- check_cond_model_swap

    def cond_model(self) -> int | None:
        for _o, choice in self.active:
            for e in self.elements(choice):
                cond = self.ix.t("ChrCustomizationCondModel").get(e.get("ChrCustomizationCondModelID") or 0)
                f = self.ix.creature_model_file((cond or {}).get("CreatureModelDataID", 0))
                if f:
                    return f
        return None

    # -- apply_customization_geosets + apply_customization_textures

    def customization(self) -> None:
        for option, choice in self.active:
            for other in self.ix.choices.get(option, []):
                for e in self.ix.elements.get(other, []):
                    g = self.geoset(e.get("ChrCustomizationGeosetID") or 0)
                    if g:
                        self.set.setdefault(g, False)
            for e in self.elements(choice):
                g = self.geoset(e.get("ChrCustomizationGeosetID") or 0)
                if g:
                    self.set[g] = True
        for _o, choice in self.active:
            for e in self.elements(choice):
                mat = self.ix.t("ChrCustomizationMaterial").get(e.get("ChrCustomizationMaterialID") or 0)
                if not mat:
                    continue
                target = mat.get("ChrModelTextureTargetID", 0)
                layer = self.ix.layers.get((self.layout, target))
                if layer is None:
                    continue
                f = self.texture(mat.get("MaterialResourcesID", 0))
                if not f:
                    continue
                ttype = layer.get("TextureType", 0)
                if ttype not in (SKIN, SKIN_EXTRA):
                    self.raw[ttype] = f
                if layer.get("TextureSectionTypeBitMask") == -1:
                    m = self.ix.materials.get((self.layout, ttype))
                    section = {"X": 0, "Y": 0, "Width": m.get("Width"), "Height": m.get("Height")} if m else None
                else:
                    section = next((s for s in self.ix.sections.get(self.layout, [])
                                    if (1 << s.get("SectionType", 0)) & layer.get("TextureSectionTypeBitMask", 0)),
                                   None)
                self.add_layer(ttype, f, target, section, layer.get("BlendMode", 0))

    def add_layer(self, ttype: int, f: int, target: int, section: dict | None, blend: int) -> None:
        if section is None or (self.layout, ttype) not in self.ix.materials:
            return
        self.layers.setdefault(ttype, []).append(
            {"file": f, "target": target, "x": section.get("X", 0), "y": section.get("Y", 0),
             "w": section.get("Width", 0), "h": section.get("Height", 0), "blend": blend})

    # -- update_skinned_models + hide_skinned_model_base_geosets

    def skinned_models(self) -> None:
        wanted: dict[int, list[int]] = {}
        for _o, choice in self.active:
            for e in self.elements(choice):
                row = self.ix.t("ChrCustomizationSkinnedModel").get(e.get("ChrCustomizationSkinnedModelID") or 0)
                if not row or not row.get("CollectionsFileDataID"):
                    continue
                g = row.get("GeosetType", 0) * 100 + row.get("GeosetID", 0)
                wanted.setdefault(row["CollectionsFileDataID"], []).append(g)
                self.groups[row.get("GeosetType", 0)] = 0  # the base model's own limb goes
        for f, geosets in wanted.items():
            self.skinned.append({"model": f, "textures": {}, "geosets": {
                "set": {str(g): True for g in sorted(set(geosets))}, "groups": {}}})

    # -- update_geosets (equipment) + update_textures (equipment) + update_equipment_models

    def equipment(self, worn: list[dict]) -> None:
        values: dict[int, list[tuple[int, int]]] = {}
        ix = self.ix
        for it in worn:
            d = ix.t("ItemDisplayInfo")[it["display"]]
            gg = d.get("GeosetGroup") or [0] * 6
            for idx, group in SLOT_GEOSETS.get(it["slot"], ()):
                v = gg[idx] if idx < len(gg) else 0
                values.setdefault(group, []).append((it["slot"], (v or 2) if group == FEET else 1 + v))
        for group, slot_values in values.items():
            order = GEOSET_PRIORITY.get(group)
            pick = slot_values[0]
            if order:
                pick = next((sv for s in order for sv in slot_values if sv[0] == s), pick)
            self.groups[group] = pick[1]
        head = next((it for it in worn if it["slot"] == 1), None)
        if head:
            vis = (ix.t("ItemDisplayInfo")[head["display"]].get("HelmetGeosetVis") or [0, 0])[self.sex]
            for group in ix.helmet_hides.get((vis, self.race), []) if vis else []:
                self.groups[group] = 0
        self.item_textures(worn)
        for it in worn:
            self.item_models(it)

    def item_textures(self, worn: list[dict]) -> None:
        layers = self.ix.layout_layers.get(self.layout, [])
        base = next((ly for ly in layers if ly.get("TextureSectionTypeBitMask") == -1
                     and ly.get("TextureType") == SKIN), None)
        by_section: dict[int, dict] = {}
        for ly in layers:
            mask = ly.get("TextureSectionTypeBitMask", 0)
            if mask == -1:
                continue
            for s in SECTIONS:
                if (1 << s) & mask:
                    by_section.setdefault(s, ly)
        sections = {s.get("SectionType"): s for s in self.ix.sections.get(self.layout, [])}
        for it in worn:
            for r in self.ix.body_textures.get(it["display"], []):
                s = r.get("ComponentSection", 0)
                layer = by_section.get(s, base)
                f = self.texture(r.get("MaterialResourcesID", 0))
                if layer is None or s not in sections or not f:
                    continue
                # Items overlay the skin: a plain copy would erase the body where they're see-through.
                blend = layer.get("BlendMode", 0)
                self.add_layer(layer.get("TextureType", 0), f, SLOT_LAYER.get(it["slot"], 10) * 100 + s,
                               sections[s], 15 if blend in (0, 1) else blend)

    def item_models(self, it: dict) -> None:
        ix = self.ix
        d = ix.t("ItemDisplayInfo")[it["display"]]
        res = list(d.get("ModelResourcesID") or [])
        mats = list(d.get("ModelMaterialResourcesID") or [])
        options = [ix.models.get(r, []) for r in res if r]
        textures = [self.model_textures(it["display"], i, mats[i] if i < len(mats) else 0) for i in range(len(res))]
        slot = it["slot"]
        placed: list[tuple[int, int | None, dict]] = []  # (model, attachment or None, textures)
        if slot == 3 and len(options) == 2 and options[0] and options[0] == options[1]:
            for pos, attach in ((0, SHOULDER_LEFT), (1, SHOULDER_RIGHT)):
                m = ix.pick_model(options[0], self.race, self.sex, self.model_fallback, position=pos)
                if m:
                    placed.append((m, attach, textures[pos]))
        else:
            if slot == 3:
                attach_ids: tuple[int, ...] = (SHOULDER_LEFT, SHOULDER_RIGHT)
            elif it.get("hand") == RIGHT:
                attach_ids = (HAND_RIGHT,)
            elif it.get("hand") == LEFT:
                attach_ids = (SHIELD,) if it["inv"] == INV_SHIELD else (HAND_LEFT,)
            else:
                attach_ids = SLOT_ATTACH.get(slot, ())
            k = 0
            for i, r in enumerate(res):
                if not r:
                    continue
                m = ix.pick_model(ix.models.get(r, []), self.race, self.sex, self.model_fallback)
                # A shoulder's list is by position (left, right): a one-sided pauldron at [1] goes on
                # the right shoulder (tools/3dcases c16), the rest fill the attachments in order.
                at = i if slot == 3 else k
                if m:
                    placed.append((m, attach_ids[at] if at < len(attach_ids) else None, textures[i]))
                k += 1
        if slot == 15 and not placed:
            # A cape without a model of its own is the body's cape geosets, wearing its texture.
            f = textures[0].get(CAPE) if textures else None
            if f:
                self.direct[CAPE] = f
        visuals = self.visuals(it, d) if it.get("hand") else []
        agg = d.get("AttachmentGeosetGroup") or [0] * 6
        for m, attach, tex in placed:
            tx = {str(k): v for k, v in sorted(tex.items())}
            if attach is not None:
                self.attachments.append({"slot": slot, "attach": attach, "model": m, "textures": tx,
                                         "visuals": visuals, "scale": 1.0})
            else:
                groups = {str(g): 1 + (agg[i] if i < len(agg) else 0) for i, g in COLLECTION_GEOSETS.get(slot, ())}
                self.collections.append({"slot": slot, "model": m, "textures": tx,
                                         "geosets": {"set": {}, "groups": groups}})

    def ammunition(self, ranged: dict, items: list[dict]) -> dict | None:
        """A missile supplied by equipment. Slot zero is the equipped ammo.
        When the sheet omits it, preview the common arrow/bullet of the weapon's
        actual ammunition type and identify that choice in the recipe."""
        ix = self.ix
        if ranged["subclass"] == 16:  # a thrown weapon is its own projectile
            item_id, source = ranged["item_id"], "weapon"
        else:
            kind = ix.t("Item").get(ranged["item_id"], {}).get("AmmunitionType")
            selected = next((it.get("item_id") for it in items if isinstance(it, dict)
                             and it.get("slot") in (0, "0")), None)
            source = "equipped" if selected else "preview"
            try:
                item_id = int(selected) if selected else {2: 2512, 3: 2516}.get(kind)
            except (TypeError, ValueError):
                return None
            ammo = ix.t("Item").get(item_id, {})
            if ammo.get("ClassID") != 6 or ammo.get("SubclassID") != kind:
                return None
        display = ix.appearance.get(item_id)
        info = ix.t("ItemDisplayInfo").get(display, {})
        materials = info.get("ModelMaterialResourcesID") or []
        for i, res in enumerate(info.get("ModelResourcesID") or []):
            if res and (model := ix.pick_model(ix.models.get(res, []), self.race, self.sex, self.model_fallback)):
                textures = self.model_textures(display, i, materials[i] if i < len(materials) else 0)
                return {"model": model, "scale": 1, "alpha": 1, "textures": textures,
                        "ammo_item_id": item_id, "ammo_source": source}
        return None

    def model_textures(self, display: int, index: int, material: int) -> dict[int, int]:
        """One item model's textures by M2 texture type: its rows in
        ItemDisplayInfoModelMatRes, else its ModelMaterialResourcesID as the
        object skin (type 2)."""
        out: dict[int, int] = {}
        for r in self.ix.model_textures.get(display, []):
            if r.get("ModelIndex", 0) == index:
                f = self.texture(r.get("MaterialResourcesID", 0))
                if f:
                    out[r.get("TextureType", CAPE)] = f
        if not out and material:
            f = self.texture(material)
            if f:
                out[CAPE] = f
        return out

    def visuals(self, it: dict, d: dict) -> list[dict]:
        """Glow models at the item model's own attachment points: its own
        ItemVisual, then its enchant's."""
        ids = [d.get("ItemVisual") or 0]
        if it.get("enchant"):
            with contextlib.suppress(TypeError, ValueError):
                ids.append((self.ix.t("SpellItemEnchantment").get(int(it["enchant"])) or {}).get("ItemVisual") or 0)
        out: list[dict] = []
        for vid in ids:
            if not vid:
                continue
            effects = [e for e in self.ix.effects.get(vid, []) if e.get("AttachmentModelFileID")
                       and e.get("DisplayWeaponSubclassID", -1) in (-1, it["subclass"])]
            if effects:
                out += [{"model": e["AttachmentModelFileID"], "attach": e.get("AttachmentID", 0)} for e in effects]
            else:  # no effect rows: ItemVisuals' five models, one per point 1-5
                models = (self.ix.t("ItemVisuals").get(vid) or {}).get("ModelFileID") or []
                out += [{"model": m, "attach": i + 1} for i, m in enumerate(models) if m]
        return out

    # -- the materials

    def material_list(self) -> list[dict]:
        out = []
        for ttype in sorted(self.layers):
            m = self.ix.materials[(self.layout, ttype)]
            layers = sorted(self.layers[ttype], key=lambda ly: ly["target"])
            w, h = m.get("Width", 0), m.get("Height", 0)
            whole = len(layers) == 1 and layers[0]["blend"] in (0, 1) and (
                layers[0]["x"], layers[0]["y"], layers[0]["w"], layers[0]["h"]) == (0, 0, w, h)
            if ttype not in (SKIN, SKIN_EXTRA) and whole and ttype not in self.direct:
                self.direct[ttype] = layers[0]["file"]  # one whole picture: bound as it is (hair)
                continue
            out.append({"type": ttype, "width": w, "height": h, "layers": layers})
        types = {m["type"] for m in out}
        for s in self.skinned:
            tx: dict[str, int | str] = {}
            for ttype in (SKIN, SKIN_EXTRA):
                src = ttype if ttype in types else SKIN if SKIN in types else None
                if src is not None:
                    tx[str(ttype)] = f"material:{src}"
            for ttype, f in sorted({**self.raw, **self.direct}.items()):
                tx.setdefault(str(ttype), f"material:{ttype}" if ttype in types else f)
            s["textures"] = tx
        return out


def _creature(tables, display: int) -> dict | None:
    ix = index(tables)
    row = ix.t("CreatureDisplayInfo").get(display) or {}
    model = ix.t("CreatureModelData").get(row.get("ModelID", 0)) or {}
    if not model.get("FileDataID"):
        return None
    skins = row.get("TextureVariationFileDataID") or []
    scale = (row.get("CreatureModelScale") or 1) * (model.get("ModelScale") or 1)
    return {"model": model["FileDataID"], "scale": scale,
            "textures": {str(11 + i): f for i, f in enumerate(skins[:3]) if isinstance(f, int) and f > 0}}


def companion(tables, observed: tuple[int, int] | None) -> dict | None:
    """Resolve an observed creature display through this client's model and skins."""
    if observed and (creature := _creature(tables, observed[1])):
        return {"kind": observed[0], "model": creature["model"], "textures": creature["textures"]}
    return None


def mounts(tables, observed: list[dict] | None) -> list[dict]:
    """Resolve the character's owned displays against the same client as the rider."""
    return [{"name": m["name"], **creature} for m in observed or []
            if (creature := _creature(tables, m["display_id"]))]


def creature_files(creature: dict) -> set[int]:
    return {creature["model"], *creature["textures"].values()}


def needs(recipe: dict | None) -> set[int]:
    """The file ids a recipe names (empty for none)."""
    return _needs(recipe) if recipe else set()


def _needs(recipe: dict) -> set[int]:
    out = {recipe["model"]} | spellart.files(recipe.get("actions", []))
    if pet := recipe.get("companion"):
        out.update(creature_files(pet))
    for mount in recipe.get("mounts", []):
        out.update(creature_files(mount))
    for m in recipe["materials"]:
        out.update(ly["file"] for ly in m["layers"])
    out.update(recipe["textures"].values())
    for s in recipe["skinned"]:
        out.add(s["model"])
        out.update(v for v in s["textures"].values() if isinstance(v, int))
    for a in recipe["attachments"]:
        out.add(a["model"])
        out.update(a["textures"].values())
        out.update(v["model"] for v in a["visuals"])
    for c in recipe["collections"]:
        out.add(c["model"])
        out.update(c["textures"].values())
    return {f for f in out if f}


# --- the tables -----------------------------------------------------------------

class Tables(dict):
    """Table name -> rows, as assemble takes them, with the build they came
    from, the tables that were missing or unreadable, and the Index. From
    load_tables it holds only the LOOKED_UP tables: the others live on in
    the Index's maps (ModelFileData alone is 30 MB of rows)."""

    def __init__(self, rows: dict, build: str = "", problems: dict | None = None):
        super().__init__(rows)
        self.build = build
        self.problems = problems or {}
        self.index: Index | None = None


_CACHE: dict[tuple, Tables] = {}
_CACHE_MAX = 2  # builds kept parsed at once (one per client version in use)
_LOCK = threading.Lock()


def load_tables(source, build: str | None = None) -> Tables:
    """The client tables of one person (a wowstats.assets Store: .table(name)
    -> bytes or None, .build() -> its client version; or a folder of
    <Name>.db2 files with an optional build.json), parsed. People on the
    same build with the same files share one parse."""
    if isinstance(source, (str, Path)):
        folder = Path(source)

        def raw(name: str) -> bytes | None:
            p = folder / f"{name}.db2"
            return p.read_bytes() if p.is_file() else None

        if build is None:
            try:
                build = str(json.loads((folder / "build.json").read_text())["build"])
            except (OSError, ValueError, KeyError, TypeError):
                build = ""
    else:
        raw = source.table
        if build is None:
            build = source.build() if callable(getattr(source, "build", None)) else ""
    blobs = {name: raw(name) for name in TABLES}
    key = (build, tuple((n, hashlib.blake2b(b, digest_size=16).digest() if b else None) for n, b in blobs.items()))
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
        rows: dict[str, dict] = {}
        problems: dict[str, str] = {}
        for name, data in blobs.items():
            if data is None:
                problems[name] = "not sent yet"
                rows[name] = {}
                continue
            try:
                rows[name] = db2.Table(data, name, build or "", columns=TABLES[name]).rows
            except (db2.DB2Error, struct.error, ValueError, IndexError) as e:
                problems[name] = str(e)
                rows[name] = {}
        ix = Index(rows)
        out = Tables({n: rows[n] for n in LOOKED_UP}, build or "", problems)
        out.index = ix
        del rows, blobs
        while len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = out
        return out
