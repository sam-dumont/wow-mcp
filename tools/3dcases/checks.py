"""What a case's grade reads: checks on the recipe (did each thing the
character wears or chose make it into the model?) and on the picture
(imgcmp.compare's numbers), turned into pass / warn / fail with reasons.

The thresholds were set on the first real run (2026-11-20, README): our
renderer and Blizzard's light differently and frame the camera a little
differently, so a perfect assembly still reads IoU ~0.85-0.9 and colour
distances of 20-40 per region.
"""

from __future__ import annotations

IOU_WARN, IOU_FAIL = 0.80, 0.65
REGION_IOU_WARN, REGION_IOU_FAIL = 0.55, 0.35
COLOUR_WARN, COLOUR_FAIL = 55.0, 85.0
HIST_WARN = 0.45
# A region smaller than this share of the body's pixels is too small to judge.
MIN_SHARE = 0.01
SLOT_NAMES = {1: "helm", 3: "shoulders", 4: "shirt", 5: "chest", 6: "belt", 7: "legs", 8: "boots", 9: "bracers",
              10: "gloves", 15: "cloak", 16: "main hand", 17: "off hand", 18: "ranged", 19: "tabard"}
# Region -> what usually shows there, for the reasons.
REGION_WORDS = {"head": "head (hair, beard, helm or skin tone)", "torso": "torso (chest, shoulders, tabard)",
                "legs": "legs (trousers, robe, cloak)", "feet": "feet (boots)", "hands": "hands (gloves, skin)",
                "weapon": "weapons"}
WORST = {"pass": 0, "warn": 1, "fail": 2}


def recipe_checks(spec: dict, recipe: dict, missing: list[dict], option_effects: dict[int, bool],
                  option_names: dict[int, str], hidden: set[int] = frozenset()) -> list[tuple[str, str]]:
    """(level, reason) for what the character has and the recipe lacks.
    option_effects: option id -> whether its choice changed the recipe from
    the option's first choice (only for non-default choices). hidden: slots
    the run drew without because the reference hides them (helm, cloak)."""
    out: list[tuple[str, str]] = []
    for m in missing:
        what = SLOT_NAMES.get(m.get("slot")) or f"slot {m.get('slot')}"
        out.append(("fail", f"{what} not drawn: {m.get('why')}"))
    slots = {it["slot"] for it in spec.get("items") or []} - set(hidden)
    placed = {a["slot"] for a in (recipe.get("attachments") or []) + (recipe.get("collections") or [])}
    gone = {m.get("slot") for m in missing}
    groups = (recipe.get("geosets") or {}).get("groups") or {}
    if 1 in slots and 1 not in placed and 1 not in gone:
        out.append(("fail", "helm absent"))
    if 15 in slots and 15 not in placed and 15 not in gone and int(groups.get("15", 0)) <= 1 \
            and "2" not in (recipe.get("textures") or {}):
        out.append(("fail", "cloak absent"))
    for slot in (16, 17):
        if slot in slots and slot not in placed and slot not in gone:
            out.append(("fail", f"{SLOT_NAMES[slot]} weapon absent"))
    for opt, changed in sorted(option_effects.items()):
        if not changed:
            name = option_names.get(opt) or f"option {opt}"
            word = "beard missing" if "beard" in name.lower() or "facial" in name.lower() else f"{name}: no effect"
            out.append(("warn", f"{word} (choice changes nothing on the model)"))
    return out


def picture_checks(cmp: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    iou = cmp.get("iou") or 0.0
    if iou < IOU_FAIL:
        out.append(("fail", f"silhouette differs (IoU {iou:.2f})"))
    elif iou < IOU_WARN:
        out.append(("warn", f"silhouette differs a little (IoU {iou:.2f})"))
    total = sum((r.get("ref_px") or 0) for r in cmp.get("regions", {}).values()) or 1
    for name, r in (cmp.get("regions") or {}).items():
        big = max(r.get("ref_px") or 0, r.get("our_px") or 0) / total >= MIN_SHARE
        if not big:
            continue
        what = REGION_WORDS.get(name, name)
        ri = r.get("iou")
        if name == "weapon":
            if (r.get("ref_px") or 0) > 3 * (r.get("our_px") or 0) + 50:
                out.append(("fail", "weapon missing or smaller than the reference's"))
            elif (r.get("our_px") or 0) > 3 * (r.get("ref_px") or 0) + 50:
                out.append(("warn", "weapon shown where the reference has none or a smaller one"))
        elif ri is not None and ri < REGION_IOU_FAIL:
            out.append(("fail", f"{what}: shape differs (IoU {ri:.2f})"))
        elif ri is not None and ri < REGION_IOU_WARN:
            out.append(("warn", f"{what}: shape differs a little (IoU {ri:.2f})"))
        c = r.get("colour")
        if c and c.get("pixels", 0) / total >= MIN_SHARE:
            d, h = c["distance"], c["histogram"]
            if d >= COLOUR_FAIL:
                out.append(("fail", f"{what}: wrong colour (ours {tuple(c['ours'])}, reference {tuple(c['ref'])})"))
            elif d >= COLOUR_WARN or h < HIST_WARN:
                out.append(("warn", f"{what}: colour off (distance {d:.0f}, histogram {h:.2f})"))
    side = cmp.get("weapon_side") or {}
    ref, ours = side.get("ref") or (0, 0), side.get("ours") or (0, 0)
    if sum(ref) > 200 and sum(ours) > 200:
        ref_left, our_left = ref[0] > ref[1] * 2, ours[0] > ours[1] * 2
        ref_right, our_right = ref[1] > ref[0] * 2, ours[1] > ours[0] * 2
        if (ref_left and our_right) or (ref_right and our_left):
            out.append(("fail", "weapon in the wrong hand"))
    return out


def grade(reasons: list[tuple[str, str]]) -> str:
    return max((lvl for lvl, _ in reasons), key=WORST.__getitem__, default="pass")
