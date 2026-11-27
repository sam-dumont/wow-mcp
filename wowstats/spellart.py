"""Assemble the 3D viewer's spells from the shipper's Forever client tables.

The class reference supplies the rank chains. The installed build supplies the
level, casting time, visual, animation, model, attachment and missile for each
rank. Shoot uses the equipped item's ranged display. The event and effect type
numbers come from WoWDBDefs' meta/enums (see data/dbd/NOTICE.md).
"""

from __future__ import annotations

from collections import defaultdict
from functools import cache

from . import classguide, gear

# Viewer menu choices. Helpful spells are previewed on the caster.
ABILITIES = {
    1: ("WARRIOR", (("battle-shout", "Battle Shout", "self", "melee"),)),
    2: ("PALADIN", (("holy-light", "Holy Light", "self", "empty"),)),
    3: (
        "HUNTER",
        (("hunters-mark", "Hunter's Mark", "other", "empty"), ("arcane-shot", "Arcane Shot", "other", "ranged")),
    ),
    4: ("ROGUE", (("kick", "Kick", "other", "melee"),)),
    5: ("PRIEST", (("power-word-shield", "Power Word: Shield", "self", "empty"),)),
    7: ("SHAMAN", (("lightning-bolt", "Lightning Bolt", "other", "empty"),)),
    8: ("MAGE", (("frostbolt", "Frostbolt", "other", "empty"),)),
    9: ("WARLOCK", (("drain-life", "Drain Life", "other", "empty"),)),
    11: ("DRUID", (("rejuvenation", "Rejuvenation", "self", "empty"),)),
}
RANGED = {
    2: (2480, "Shoot bow"),
    3: (7918, "Fire gun"),
    16: (2764, "Throw weapon"),
    18: (7919, "Fire crossbow"),
    19: (5019, "Shoot wand"),
}
PHASE = {1: "precast", 3: "cast", 4: "travel-start", 5: "travel-end", 6: "impact", 7: "aura", 11: "channel"}
TARGET = {1: "caster", 2: "target", 3: "area", 4: "target-not-caster", 5: "missile-destination"}

TABLES = {
    "SpellXSpellVisual": (
        "SpellID",
        "DifficultyID",
        "SpellVisualID",
        "Probability",
        "Priority",
        "ViewerUnitConditionID",
        "ViewerPlayerConditionID",
        "CasterUnitConditionID",
        "CasterPlayerConditionID",
    ),
    "SpellLevels": ("SpellID", "SpellLevel", "DifficultyID"),
    "SpellMisc": ("SpellID", "DifficultyID", "Speed", "CastingTimeIndex", "DurationIndex", "LaunchDelay"),
    "SpellCastTimes": ("Base",),
    "SpellDuration": ("Duration",),
    "ItemSparse": ("ItemDelay",),
    "ItemRangedDisplayInfo": ("CastSpellVisualID", "AutoAttackSpellVisualID", "MissileSpellVisualEffectNameID"),
    "SpellVisual": (
        "SpellVisualMissileSetID",
        "Flags",
        "MissileAttachment",
        "MissileDestinationAttachment",
        "MissileCastOffset",
        "MissileImpactOffset",
    ),
    "SpellVisualEvent": (
        "SpellVisualID",
        "StartEvent",
        "EndEvent",
        "TargetType",
        "StartMinOffsetMs",
        "StartMaxOffsetMs",
        "EndMinOffsetMs",
        "EndMaxOffsetMs",
        "SpellVisualKitID",
    ),
    "SpellVisualKit": ("DelayMin", "DelayMax", "Flags"),
    "SpellVisualKitEffect": ("ParentSpellVisualKitID", "EffectType", "Effect"),
    "SpellVisualKitModelAttach": (
        "SpellVisualEffectNameID",
        "AttachmentID",
        "Offset",
        "Yaw",
        "Pitch",
        "Roll",
        "Scale",
        "StartAnimID",
        "AnimID",
        "EndAnimID",
        "Flags",
        "StartDelay",
    ),
    "SpellVisualAnim": ("InitialAnimID", "LoopAnimID", "AnimKitID"),
    "AnimKitSegment": (
        "ParentAnimKitID",
        "OrderIndex",
        "AnimID",
        "Speed",
        "AnimStartTime",
        "StartCondition",
        "StartConditionDelay",
    ),
    "SpellProceduralEffect": ("Type", "Value"),
    "SpellChainEffects": (
        "TextureFileDataID",
        "StartWidth",
        "EndWidth",
        "TexCoordScale",
        "SegDuration",
        "SegDelay",
        "Flags",
        "Red",
        "Green",
        "Blue",
        "Alpha",
        "BlendMode",
        "AvgSegLen",
        "NoiseScale",
    ),
    "SpellVisualMissile": (
        "SpellVisualMissileSetID",
        "SpellVisualEffectNameID",
        "Attachment",
        "DestinationAttachment",
        "CastOffset",
        "ImpactOffset",
        "Flags",
        "SpellMissileMotionID",
        "DecayTimeAfterImpact",
    ),
    "SpellVisualEffectName": ("ModelFileDataID", "Scale", "Alpha", "TextureFileDataID"),
}


@cache
def ranks():
    result = {}
    classes = classguide.reference().get("classes", {})
    for cls, (token, menu) in ABILITIES.items():
        book = classes.get(token, {}).get("spells", {})
        for name, label, _target, _equip in menu:
            chain = {s["rank"]: s["id"] for spells in book.values() for s in spells if s.get("name") == label}
            # Rank one of the starting spells is already known before a trainer
            # sells rank two. The trainer record carries that first spell's ID.
            for spells in book.values():
                for spell in spells:
                    if spell.get("name") == label and spell["rank"] == 2 and spell.get("prev"):
                        chain.setdefault(1, spell["prev"])
            result[(cls, name)] = sorted(chain.items(), reverse=True)
    return result


class Catalog:
    """Retain just the relation chains for the menu's spells and weapon visuals."""

    def __init__(self, tables):
        self.rows = {}
        self.groups = {}
        self.used: dict[str, set[int]] | None = None  # The demo packer records rows read by its profiles.

        def select(table, keys=None, column=None):
            rows = tables.get(table, {})
            if keys is not None:
                if column:
                    rows = {i: r for i, r in rows.items() if r.get(column) in keys}
                else:
                    rows = {i: rows[i] for i in keys if i in rows}
            self.rows[table] = rows
            if column:
                grouped = defaultdict(list)
                for i, r in sorted(rows.items()):
                    grouped[r[column]].append(i)
                self.groups[table] = grouped
            return rows.values()

        spell_ids = {s for chain in ranks().values() for _, s in chain} | {s for s, _ in RANGED.values()}
        select("SpellLevels", spell_ids, "SpellID")
        misc = select("SpellMisc", spell_ids, "SpellID")
        select("SpellCastTimes", {r["CastingTimeIndex"] for r in misc})
        select("SpellDuration", {r["DurationIndex"] for r in misc})
        select(
            "ItemSparse",
            {i for i, r in tables.get("Item", {}).items() if r.get("ClassID") == 2 and r.get("SubclassID") in RANGED},
        )
        ranged = select("ItemRangedDisplayInfo")
        links = select("SpellXSpellVisual", spell_ids, "SpellID")
        visual_ids = {r["SpellVisualID"] for r in links} | {r.get("CastSpellVisualID") for r in ranged}
        visuals = select("SpellVisual", visual_ids)
        events = select("SpellVisualEvent", visual_ids, "SpellVisualID")
        kit_ids = {r["SpellVisualKitID"] for r in events}
        select("SpellVisualKit", kit_ids)
        effects = select("SpellVisualKitEffect", kit_ids, "ParentSpellVisualKitID")
        ids = defaultdict(set)
        for r in effects:
            ids[r["EffectType"]].add(r["Effect"])
        animations = select("SpellVisualAnim", ids[6])
        select("AnimKitSegment", {r["AnimKitID"] for r in animations if r.get("AnimKitID")}, "ParentAnimKitID")
        attach = select("SpellVisualKitModelAttach", ids[2])
        procedures = select("SpellProceduralEffect", ids[1])
        select("SpellChainEffects", {int(r["Value"][0]) for r in procedures if r.get("Type") == 0 and r.get("Value")})
        missiles = select(
            "SpellVisualMissile",
            {r["SpellVisualMissileSetID"] for r in visuals if r.get("SpellVisualMissileSetID")},
            "SpellVisualMissileSetID",
        )
        select(
            "SpellVisualEffectName",
            {r.get("SpellVisualEffectNameID") for r in (*attach, *missiles)}
            | {r.get("MissileSpellVisualEffectNameID") for r in ranged},
        )

    def row(self, name, key):
        row = self.rows[name].get(key)
        if row is not None and self.used is not None:
            self.used.setdefault(name, set()).add(key)
        return row or {}

    def many(self, name, key):
        return [self.row(name, i) for i in self.groups[name].get(key, ())]

    def animation(self, effect, kit):
        anim = self.row("SpellVisualAnim", effect)
        if not anim:
            return
        kit.update(initial_animation=anim["InitialAnimID"], animation=anim["LoopAnimID"], animation_id=effect)
        if aid := anim["AnimKitID"]:
            kit["animation_kit_id"] = aid
            seg = self.many("AnimKitSegment", aid)
            # Kick uses a single unconditional segment. A composite or
            # conditional animation kit needs a different playback path.
            if len(seg) == 1 and not (seg[0]["StartCondition"] or seg[0]["StartConditionDelay"]):
                kit.update(animation=seg[0]["AnimID"], animation_speed=seg[0]["Speed"])

    def model(self, effect_id):
        effect = self.row("SpellVisualEffectName", effect_id)
        if effect and effect.get("ModelFileDataID", 0) > 0:
            return {
                "effect_id": effect_id,
                "model": effect["ModelFileDataID"],
                "scale": effect["Scale"],
                "alpha": effect["Alpha"],
            }

    def attachment(self, attach_id):
        row = self.row("SpellVisualKitModelAttach", attach_id)
        if row and (model := self.model(row["SpellVisualEffectNameID"])):
            return {
                **model,
                "attach_id": attach_id,
                "attachment": row["AttachmentID"],
                "offset": row["Offset"],
                "rotation": [row["Yaw"], row["Pitch"], row["Roll"]],
                "scale": model["scale"] * row["Scale"],
                "start_animation": row["StartAnimID"],
                "animation": row["AnimID"],
                "end_animation": row["EndAnimID"],
                "delay_ms": row["StartDelay"] * 1000,
                "flags": row["Flags"],
            }

    def procedure(self, effect, kit):
        p = self.row("SpellProceduralEffect", effect)
        if not p:
            return
        values = p["Value"]
        if p["Type"] == 0 and (r := self.row("SpellChainEffects", int(values[0]))):
            if texture := r["TextureFileDataID"][0]:
                kit.setdefault("chains", []).append(
                    {
                        "id": int(values[0]),
                        "procedure_id": effect,
                        "params": values,
                        "texture": texture,
                        "start_width": r["StartWidth"],
                        "end_width": r["EndWidth"],
                        "tex_coord_scale": r["TexCoordScale"],
                        "seg_duration": r["SegDuration"],
                        "seg_delay": r["SegDelay"],
                        "avg_seg_len": r["AvgSegLen"],
                        "noise_scale": r["NoiseScale"],
                        "color": [r["Red"], r["Green"], r["Blue"], r["Alpha"]],
                        "blend_mode": r["BlendMode"],
                        "flags": r["Flags"],
                    }
                )
        else:
            kit.setdefault("procedures", []).append({"id": effect, "type": p["Type"], "values": values})

    def kit(self, event):
        kid = event["SpellVisualKitID"]
        r = self.row("SpellVisualKit", kid)
        if not r or event["StartEvent"] not in PHASE or event["TargetType"] not in TARGET:
            return None
        kit = {
            "kit_id": kid,
            "event_id": event["ID"],
            "phase": PHASE[event["StartEvent"]],
            "target": TARGET[event["TargetType"]],
            "start_event": event["StartEvent"],
            "end_event": event["EndEvent"],
            "target_type": event["TargetType"],
            "start_offset_ms": [event["StartMinOffsetMs"], event["StartMaxOffsetMs"]],
            "end_offset_ms": [event["EndMinOffsetMs"], event["EndMaxOffsetMs"]],
            "delay_ms": r["DelayMin"],
            "max_delay_ms": r["DelayMax"],
            "flags": r["Flags"],
            "effects": [],
        }
        for e in self.many("SpellVisualKitEffect", kid):
            typ, effect = e["EffectType"], e["Effect"]
            if typ == 2 and (a := self.attachment(effect)):
                kit["effects"].append(a)
            elif typ == 6:
                self.animation(effect, kit)
            elif typ == 1:
                self.procedure(effect, kit)
        return kit

    def visual(self, vid, projectile=0, ammunition=None):
        if not (v := self.row("SpellVisual", vid)):
            return None
        kits = [k for e in self.many("SpellVisualEvent", vid) if (k := self.kit(e))]
        missiles = []
        for m in self.many("SpellVisualMissile", v["SpellVisualMissileSetID"]):
            effect_id = m["SpellVisualEffectNameID"] or projectile
            model = self.model(effect_id)
            if not effect_id and m["Flags"] & 2 and ammunition:
                model = ammunition()
            if model:
                missiles.append(
                    {
                        **model,
                        "missile_id": m["ID"],
                        "attachment": m["Attachment"],
                        "destination_attachment": m["DestinationAttachment"],
                        "offset": m["CastOffset"],
                        "destination_offset": m["ImpactOffset"],
                        "flags": m["Flags"],
                        "motion_id": m["SpellMissileMotionID"],
                        "decay_ms": m["DecayTimeAfterImpact"],
                    }
                )
        return {"visual_id": vid, "visual_flags": v["Flags"], "kits": kits, "missiles": missiles}

    def timing(self, spell):
        row = next((r for r in self.many("SpellMisc", spell) if not r["DifficultyID"]), None)
        if row:
            cast = self.row("SpellCastTimes", row["CastingTimeIndex"])
            dur = self.row("SpellDuration", row["DurationIndex"])
            return {
                "cast_ms": max(0, cast.get("Base", 0)),
                "duration_ms": dur.get("Duration", 0),
                "speed": row["Speed"],
                "launch_delay_ms": row["LaunchDelay"] * 1000,
            }

    def actions(self, cls, level, ranged, ranged_display, ammunition=None):
        actions = []
        kind = ranged["subclass"] if ranged else None
        weapon = None
        if kind in RANGED:
            spell, label = RANGED[kind]
            r = self.row("ItemRangedDisplayInfo", ranged_display)
            v = self.visual(r["CastSpellVisualID"], r["MissileSpellVisualEffectNameID"], ammunition) if r else None
            if v and (t := self.timing(spell)):
                item_id = ranged["item_id"]
                delay = self.row("ItemSparse", item_id).get("ItemDelay", 0)
                if not delay:
                    # Items added as hotfixes can be absent from the base CASC table.
                    item = ((gear.game_items() or {}).get("items") or {}).get(str(item_id), {})
                    delay = item.get("speed", 0)
                weapon = {
                    **v,
                    **t,
                    "name": "ranged",
                    "label": label,
                    "equipment": "ranged",
                    "target": "other",
                    "spell_id": spell,
                    "item_id": item_id,
                    "item_delay_ms": delay,
                }
                actions.append(weapon)
        for name, label, target, equipment in ABILITIES.get(cls, (None, []))[1]:
            if equipment == "ranged" and (not weapon or kind not in (2, 3, 18)):
                continue
            for rank, spell in ranks()[(cls, name)]:
                levels = self.many("SpellLevels", spell)
                if not any(not r["DifficultyID"] and 0 < r["SpellLevel"] <= level for r in levels):
                    continue
                # An unknown condition or random alternate does not identify the
                # character's appearance. Use the client's unconditional visual.
                links = [
                    r
                    for r in self.many("SpellXSpellVisual", spell)
                    if not r["DifficultyID"]
                    and r["Probability"] == 1
                    and not any(
                        r[k]
                        for k in (
                            "ViewerUnitConditionID",
                            "ViewerPlayerConditionID",
                            "CasterUnitConditionID",
                            "CasterPlayerConditionID",
                        )
                    )
                ]
                if (
                    links
                    and (v := self.visual(min(links, key=lambda r: r["Priority"])["SpellVisualID"]))
                    and (t := self.timing(spell))
                ):
                    action = {
                        **v,
                        **t,
                        "name": name,
                        "label": label,
                        "target": target,
                        "equipment": equipment,
                        "spell_id": spell,
                        "rank": rank,
                    }
                    # Arcane Shot supplies missile and impact. The equipped bow,
                    # gun or crossbow supplies its draw and release.
                    if equipment == "ranged":
                        action["item_id"] = weapon["item_id"]
                        action["item_delay_ms"] = weapon["item_delay_ms"]
                        action["weapon_visual_id"] = weapon["visual_id"]
                        action["kits"] = [k for k in weapon["kits"] if k["phase"] in ("precast", "cast")] + action[
                            "kits"
                        ]
                    actions.append(action)
                break
        return actions


def files(actions):
    out = {m["model"] for a in actions for m in a["missiles"]}
    for a in actions:
        for missile in a["missiles"]:
            out.update((missile.get("textures") or {}).values())
        for kit in a["kits"]:
            out.update(m["model"] for m in kit["effects"])
            out.update(c["texture"] for c in kit.get("chains") or ())
    return out
