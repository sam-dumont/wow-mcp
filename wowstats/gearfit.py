"""Who an item is built for, and how well it fits this character's spec.

The model, for every class and spec (owner, 2026-11-20: "if it increases,
it increases, especially levelling. But gear recos must say 'this is for
your class/role' or 'this is maybe not the best for you'"):

- armour: the types the class can wear and the best one at its level
  (gear PROFICIENCY: mail at 40 for hunters and shamans, plate at 40 for
  warriors and paladins);
- weapons: the types it can train (gear.usability already says);
- stats, at two levels (owner, 2026-11-20):
  1. against the class: what the class gets nothing from counts zero
     (CLASS_ZERO: healing power for a class with no heals, Intellect and
     Spirit for warriors and rogues, attack power for mages, priests and
     warlocks, ranged attack power for anyone but a hunter);
  2. not the spec's, still the class's: healing power for a priest, druid,
     shaman or paladin who doesn't heal, attack power for a druid, shaman
     or paladin who casts or heals (SPEC_PARTIAL). It counts partially,
     by level and talents: until the spec's 31-point talent (Shadowform at
     40 at the earliest, KB gear-fit:capstone-at-40) a spec plays closer to
     a generalist of its class (a shadow priest heals himself between
     pulls), so it gets GENERALIST of the class's best weight for it; after,
     Polestar's own weight for the spec.

From it, per item:

- built_for(): the specs that get the most out of it, among those wearing
  it as their best armour type: who usually has first pick in a group;
- fit(): for this character, "yes" (built for your spec), "any" (only
  Stamina, armour or weapon damage: fits every spec), "spec" (built for
  another spec of your class: fine while levelling, but don't roll Need
  against who it's made for), "class" (made for another class: little of
  it works for yours), or "unknown" (no spec known and the class's specs
  don't share a role); the share of its stats that do little
  or nothing for this spec; a lower armour type than the best it can wear;
  the group-loot tip; and the factor the ranking applies.

Nothing is hidden: an off-spec upgrade keeps its gain and stays listed. The
ranking (gear.advise) multiplies the gain by `factor`, so between two close
options the one built for the spec comes first, and an item that a group
usually hands to someone else (a dungeon drop) comes after an equal one from
a quest, a craft or the auction house. The KB's gear-fit lane
(data/kb/research/gear-fit.json) holds the rules with their sources; each
fit names the statements it rests on (`kb`).
"""

from __future__ import annotations

from wowstats import gear as gm

ROLES = {
    ("WARRIOR", "arms"): "melee", ("WARRIOR", "fury"): "melee", ("WARRIOR", "protection"): "tank",
    ("PALADIN", "holy"): "healer", ("PALADIN", "protection"): "tank", ("PALADIN", "retribution"): "melee",
    ("HUNTER", "beastmastery"): "ranged", ("HUNTER", "marksmanship"): "ranged", ("HUNTER", "survival"): "ranged",
    ("ROGUE", "assassination"): "melee", ("ROGUE", "combat"): "melee", ("ROGUE", "subtlety"): "melee",
    ("PRIEST", "discipline"): "healer", ("PRIEST", "holy"): "healer", ("PRIEST", "shadow"): "caster",
    ("SHAMAN", "elemental"): "caster", ("SHAMAN", "enhancement"): "melee", ("SHAMAN", "restoration"): "healer",
    ("MAGE", "arcane"): "caster", ("MAGE", "fire"): "caster", ("MAGE", "frost"): "caster",
    ("WARLOCK", "affliction"): "caster", ("WARLOCK", "demonology"): "caster", ("WARLOCK", "destruction"): "caster",
    ("DRUID", "balance"): "caster", ("DRUID", "feral"): "melee", ("DRUID", "restoration"): "healer",
}
ROLE_WORDS = {"tank": "tanks", "healer": "healers", "melee": "melee fighters", "ranged": "hunters",
              "caster": "damage casters"}
CLASS_WORDS = {"WARRIOR": "warrior", "PALADIN": "paladin", "HUNTER": "hunter", "ROGUE": "rogue", "PRIEST": "priest",
               "SHAMAN": "shaman", "MAGE": "mage", "WARLOCK": "warlock", "DRUID": "druid"}
ARMOR = {1: "cloth", 2: "leather", 3: "mail", 4: "plate"}
STAT_WORDS = {"str": "Strength", "agi": "Agility", "sta": "Stamina", "int": "Intellect", "spi": "Spirit",
              "ap": "attack power", "rap": "ranged attack power", "dmgheal": "spell damage and healing",
              "healonly": "healing power", "dmgonly": "spell damage", "spArcane": "Arcane damage",
              "spFire": "Fire damage", "spFrost": "Frost damage", "spHoly": "Holy damage",
              "spNature": "Nature damage", "spShadow": "Shadow damage", "crit": "critical strike",
              "hit": "hit", "spellCrit": "spell critical strike", "spellHit": "spell hit", "mp5": "mana regen",
              "hp5": "health regen", "defense": "Defense", "dodge": "dodge", "parry": "parry", "block": "block",
              "blockValue": "block value", "haste": "haste", "expertise": "expertise", "armorPen": "armor penetration"}
MANA = ("int", "spi", "mp5", "mana")

# Stats that do nothing for a spec, whatever a weight table says: (who, the
# stats, the KB statement). The weights come from Polestar and wowsims; a
# few carry small values here that the class can't use (a shadow priest's
# healing power, a warrior's Spirit, an elemental shaman's attack power).
# Level 1, against the class: (classes, stats, KB statement). Zero, at
# every level and spec.
NO_HEALS = ("WARRIOR", "ROGUE", "MAGE", "WARLOCK", "HUNTER")
CLASS_ZERO = (
    (NO_HEALS, ("heal",), "gear-fit:class-no-heals"),
    (("WARRIOR", "ROGUE"), MANA, "gear-fit:no-mana"),
    (("MAGE", "PRIEST", "WARLOCK"), ("ap", "rap"), "gear-fit:casters-no-ap"),
    (("WARRIOR", "PALADIN", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID"), ("rap",), "gear-fit:ranged-ap"),
)
# Level 2, not the spec's but the class's: (stat, the roles it isn't
# theirs for, KB statement). Partial, by level and talents.
SPEC_PARTIAL = (
    ("heal", ("tank", "melee", "ranged", "caster"), "gear-fit:hybrids-heal-while-levelling"),
    ("ap", ("caster", "healer"), "gear-fit:casters-no-ap"),
)
# Before the spec's 31-point talent, the share of the class's best weight
# for such a stat (its healer's healing power, its fighter's attack power).
# A model choice, not a sourced number: half, a generalist who also does
# the other job now and then. After the talent, Polestar's own weight.
GENERALIST = 0.5
# The 31-point talent's level and points when the KB has no
# talents.points rule (gear.capstone_rule reads it).
CAPSTONE = (40, 31)

# Rough item budget per point of each stat (how much of an item's stat
# budget one point takes, Strength = 1): only to say what share of an item's
# stats a spec uses. Armour and weapon damage aren't counted: every wearer
# gets them. Spell damage is split three ways: "damage and healing" (the
# same number on both lines), healing beyond it, damage beyond it.
BUDGET = {"str": 1, "agi": 1, "sta": 1, "int": 1, "spi": 1, "ap": 0.5, "rap": 0.4, "dmgheal": 0.86,
          "healonly": 0.45, "dmgonly": 0.86, "spArcane": 0.7, "spFire": 0.7, "spFrost": 0.7, "spHoly": 0.7,
          "spNature": 0.7, "spShadow": 0.7, "crit": 14, "hit": 10, "spellCrit": 14, "spellHit": 8, "mp5": 2.5,
          "hp5": 2.5, "defense": 1.2, "dodge": 12, "parry": 15, "block": 5, "blockValue": 0.65, "haste": 10,
          "expertise": 10, "armorPen": 0.14}
# A stat "does little" for a spec when its weight per budget point is under
# this share of the spec's best primary stat weight.
LITTLE = 0.1
# Specs whose affinity is at least this share of the best one's count as
# built for it too.
NEAR = 0.85
# A spec in the same role as one it's built for counts as built for too
# down to this share (an Arms warrior and a rogue on an Agility piece).
ROLE_NEAR = 0.65
# The roles whose gear a role counts as its own at ROLE_NEAR: weapon
# fighters share Agility and Strength pieces (a hunter and a rogue), a tank
# levels in them too; a damage dealer doesn't count a tank's piece as his.
KIN = {"melee": {"melee", "ranged"}, "ranged": {"melee", "ranged"}, "tank": {"tank", "melee"},
       "caster": {"caster"}, "healer": {"healer"}}
# The ranking's factors (gear.advise): an item built for someone else, and
# one more when it only drops in a group (the others usually roll first).
OTHER = 0.9   # level 2: built for another spec of the class
CLASS = 0.75  # level 1: made for another class
GROUP = 0.85
WASTED_NOTE = 25  # percent of an item's stats doing little before the label says so


def role_of(cls: str, key: str | None) -> str | None:
    return ROLES.get((cls, key or ""))


def capstone(level: int, points: int | None = None) -> bool:
    """Has the spec its 31-point talent (Shadowform...)? The level it can
    come at the earliest (one point a level from 10: 40), and when the
    talents are known, 31 points in the tree."""
    lv, need = gm.capstone_rule() or CAPSTONE
    return level >= lv and (points is None or points >= need)


def model_weights(ref: gm.Reference, w: dict | None, cls: str, key: str | None, level: int,
                  points: int | None = None) -> dict | None:
    """The spec's weights in the model: what the class can't use at zero,
    what the class uses but the spec doesn't at a partial, level- and
    talent-aware weight (see the module's docstring). What the gear advice
    scores with, before the player's own edits."""
    if not w:
        return w
    role = role_of(cls, key)
    out = dict(w)
    for classes, stats, _ in CLASS_ZERO:
        if cls in classes:
            for s in stats:
                if out.get(s):
                    out[s] = 0
    if role and not capstone(level, points):
        for stat, roles, _ in SPEC_PARTIAL:
            if role not in roles or stat in zeroed(cls, key):
                continue
            best = max((((gm.weights_for(ref, cls, k, level)[0] or {}).get(stat) or 0)
                        for c, k in ROLES if c == cls), default=0)
            out[stat] = max(out.get(stat) or 0, round(best * GENERALIST, 3))
    return out


def zeroed(cls: str, key: str | None = None) -> dict[str, str]:
    """stat -> the KB statement that says the class gets nothing from it."""
    out: dict[str, str] = {}
    for classes, stats, kb in CLASS_ZERO:
        if cls in classes:
            for s in stats:
                out.setdefault(s, kb)
    return out


def partial(cls: str, key: str | None) -> dict[str, str]:
    """stat -> the KB statement: the class uses it, this spec less."""
    role = role_of(cls, key)
    z = zeroed(cls, key)
    return {stat: kb for stat, roles, kb in SPEC_PARTIAL if role in roles and stat not in z}


def spec_points(ref: gm.Reference, cls: str, key: str, sheet: dict | None) -> int | None:
    """Talent points in the spec's tree, as Battle.net last read them; None
    when unknown."""
    rows = [t for t in (sheet or {}).get("talents") or [] if isinstance(t, dict) and t.get("tree")]
    if not rows:
        return None
    return sum(int(t.get("points") or 0) for t in rows if gm.spec_key(ref, cls, t["tree"]) == key)


def parts(stats: dict) -> dict[str, float]:
    """An item's stats as budget parts (BUDGET's keys), positive ones only."""
    out = {k: float(v) for k, v in (stats or {}).items()
           if k in BUDGET and k not in ("sp", "heal") and isinstance(v, (int, float)) and v > 0}
    sp, heal = float((stats or {}).get("sp") or 0), float((stats or {}).get("heal") or 0)
    shared = min(sp, heal)
    if shared > 0:
        out["dmgheal"] = shared
    if heal > shared:
        out["healonly"] = heal - shared
    if sp > shared:
        out["dmgonly"] = sp - shared
    return out


def _part_weight(w: dict, part: str) -> float:
    if part == "dmgheal":
        return max(w.get("sp") or 0, w.get("heal") or 0)
    if part == "healonly":
        return w.get("heal") or 0
    if part == "dmgonly":
        return w.get("sp") or 0
    return w.get(part) or 0


def credit(w: dict, part: str) -> float:
    """How much a budget point of this part is worth to the spec, against
    its best primary stat (0..1)."""
    top = max((w.get(k) or 0) for k in ("str", "agi", "sta", "int", "spi")) or 1
    return min(1.0, _part_weight(w, part) / BUDGET[part] / top)


def use(w: dict, stats: dict) -> tuple[float, float, list[str], bool]:
    """(affinity: how much of the item's stat budget, Stamina aside, is worth
    to this spec, each part at its credit(); the share of the whole budget
    that does little or nothing for it; those parts; whether every one of
    them does nothing at all). Stamina counts for everyone, so it never
    decides who an item is built for. An affinity of 1.0 for an item with no
    other stat."""
    ps = parts(stats)
    total = sum(v * BUDGET[k] for k, v in ps.items())
    if total <= 0:
        return 1.0, 0.0, [], True
    core = sum(v * BUDGET[k] for k, v in ps.items() if k != "sta")
    got, lost, little, none = 0.0, 0.0, [], True
    for k, v in ps.items():
        cr = credit(w, k)
        if k != "sta":
            got += v * BUDGET[k] * cr
        if cr < LITTLE:
            lost += v * BUDGET[k]
            little.append(k)
            none = none and _part_weight(w, k) <= 0
    return (got / core if core > 0 else 1.0), lost / total, little, none


def best_armor(prof: dict | None, level: int) -> int:
    if not prof:
        return 0
    if prof.get("armorAt40") and level >= 40:
        return prof["armorAt40"]
    return prof.get("armor") or 0


def armor_of(info: dict) -> int | None:
    """The item's armour type (1 cloth .. 4 plate), None when every class
    wears it the same (cloaks, rings, necks, trinkets, off-hand pieces)."""
    if info.get("class") == 4 and info.get("inv") not in gm.ANY_ARMOR_INV and 1 <= (info.get("subclass") or 0) <= 4:
        return info["subclass"]
    return None


def _cache(ref: gm.Reference) -> dict:
    c = getattr(ref, "_fit", None)
    if c is None:
        c = {}
        object.__setattr__(ref, "_fit", c)
    return c


def spec_weights(ref: gm.Reference, cls: str, key: str, level: int) -> dict | None:
    """The model's weights for any spec at a level (cached on the reference)."""
    c = _cache(ref)
    k = ("w", cls, key, level)
    if k not in c:
        c[k] = model_weights(ref, gm.weights_for(ref, cls, key, level)[0], cls, key, level)
    return c[k]


def all_specs(ref: gm.Reference) -> list[tuple[str, str]]:
    return [(cls, s["key"]) for cls, meta in (ref.gear.get("CLASSES") or {}).items()
            for s in meta.get("specs") or []]


def built_for(ref: gm.Reference, info: dict, level: int, armour: bool = True) -> list[dict]:
    """The specs that get the most out of the item at this level:
    [{cls, key, role, share}], the best first. armour: only among those who
    wear its armour type as their best, when any of them can use it (who
    usually has first pick in a group); without, every spec that can wear it
    (who its stats are made for). [] when the item has no stat a spec could
    prefer (only Stamina, armour or damage: fits anyone)."""
    c = _cache(ref)
    k = ("b", id(info), info.get("ilvl"), tuple(sorted((info.get("stats") or {}).items())), level, armour)
    if k in c:
        return c[k]
    ps = parts(info.get("stats") or {})
    if not ps or set(ps) <= {"sta"}:
        c[k] = []
        return []
    armor = armor_of(info)
    rows = []
    for cls, key in all_specs(ref):
        usable, _, min_level = gm.usability(ref, info, cls, level)
        if not usable or min_level > max(level, info.get("req") or 0):
            continue
        w = spec_weights(ref, cls, key, level)
        if not w:
            continue
        share = use(w, info.get("stats") or {})[0]
        best = armor is None or best_armor((ref.gear.get("PROFICIENCY") or {}).get(cls), level) == armor
        rows.append({"cls": cls, "key": key, "role": role_of(cls, key), "share": round(share, 3), "best": best})
    pool = ([r for r in rows if r["best"]] or rows) if armour else rows
    # A healer item: its healing line at least twice its damage (Forever's
    # healing gear carries about a third as damage, KB gear-fit:
    # healer-items-carry-damage). Healers have first pick, whatever else
    # the item carries.
    st = info.get("stats") or {}
    if (st.get("heal") or 0) > 0 and (st.get("heal") or 0) >= 2 * (st.get("sp") or 0):
        pool = [r for r in pool if r["role"] == "healer"] or [r for r in rows if r["role"] == "healer"] or pool
    top = max((r["share"] for r in pool), default=0)
    out = [r for r in pool if top > 0 and r["share"] >= top * NEAR]
    out.sort(key=lambda r: (-r["share"], r["cls"], r["key"]))
    # Every role gets full use out of it: it's built for anyone.
    if out and {r["role"] for r in out} >= set(ROLE_WORDS):
        out = []
    c[k] = out
    return out


def _label(ref: gm.Reference, cls: str, key: str) -> str:
    lab = next((s["label"] for s in gm.specs_of(ref, cls) if s["key"] == key), key)
    return lab.split(" (")[0]


def who_words(ref: gm.Reference, rows: list[dict], by_role: bool = False) -> str:
    """'healers', 'hunters and Enhancement shamans', 'warriors and paladins'.
    by_role: one role across several classes is named by the role (what
    an item is built for); else by class, up to three (who rolls first)."""
    roles = {r["role"] for r in rows}
    classes: dict[str, list[str]] = {}
    for r in rows:
        classes.setdefault(r["cls"], []).append(r["key"])
    if len(roles) == 1 and len(classes) >= (2 if by_role else 4):
        return ROLE_WORDS[next(iter(roles))]
    words = []
    for cls, keys in classes.items():
        every = [s["key"] for s in gm.specs_of(ref, cls)]
        noun = CLASS_WORDS[cls] + "s"
        # A class whose specs share one role is named whole (rogues, mages).
        if set(keys) >= set(every) or len({role_of(cls, k) for k in every}) == 1:
            words.append(noun)
        else:
            words.append(" and ".join(_label(ref, cls, k) for k in keys) + " " + noun)
    if len(words) > 3:
        return " and ".join(ROLE_WORDS[r] for r in sorted(roles))
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def known_role(ref: gm.Reference, cls: str, key: str, how: str) -> str | None:
    """The character's role, None when it's unknown: no spec picked and no
    talents (the class's leveling default), for a class whose specs don't
    all share one role."""
    if how != "default":
        return role_of(cls, key)
    roles = {role_of(cls, s["key"]) for s in gm.specs_of(ref, cls)}
    return roles.pop() if len(roles) == 1 else None


def _share_words(pct: float) -> str:
    if pct >= 70:
        return "Most of its stats"
    if pct >= 40:
        return "About half its stats"
    if pct >= 25:
        return "About a third of its stats"
    return "Some of its stats"


def group_drop(sources: list[dict]) -> bool:
    """Every source is a drop in a dungeon or raid: the group rolls for it."""
    return bool(sources) and all(s.get("kind") == "drop" and (s.get("instance") or s.get("dungeon"))
                                 for s in sources)


def fit(ref: gm.Reference, info: dict, cls: str, key: str, how: str, level: int, w: dict,
        sources: list[dict] | None = None, points: int | None = None) -> dict:
    """How the item fits this character (see the module's docstring).
    points: talent points in the spec's tree, when known (capstone())."""
    role = known_role(ref, cls, key, how)
    rows = built_for(ref, info, level, armour=False)
    prio = built_for(ref, info, level)
    own, lost, little, none = use(w, info.get("stats") or {})
    top = max((r["share"] for r in rows), default=0)

    def among(rs: list[dict]) -> bool:
        # This spec, or another spec of the class in the same role (a
        # Subtlety rogue and a Combat one wear the same leather).
        return any(r["cls"] == cls and (r["key"] == key or (role and r["role"] == role)) for r in rs)

    # Built for this spec: it's among those the stats are made for, or in
    # the same role as one of them and nearly as well served.
    mine = among(rows) or bool(role and any(r["role"] in KIN[role] for r in rows) and own >= ROLE_NEAR * top)
    wasted = round(lost * 100)
    armor = armor_of(info)
    best = best_armor((ref.gear.get("PROFICIENCY") or {}).get(cls), level)
    lower = {"item": ARMOR[armor], "best": ARMOR[best]} if armor and best and armor < best else None
    who = who_words(ref, rows, by_role=True) if rows else None
    kb = []
    if role is None:
        for_you = "unknown"
    elif not rows:
        for_you = "any"
    elif mine:
        for_you = "yes"
    elif any(r["cls"] == cls for r in rows):
        # Level 2: built for another spec of the class.
        for_you = "spec"
    else:
        # Level 1: made for other classes.
        for_you = "class"
    early = not capstone(level, points)
    class_words = CLASS_WORDS.get(cls, cls.lower())
    spec_words = f"{_label(ref, cls, key)} {class_words}"
    if for_you == "unknown":
        label = "Role unknown: pick your role in settings"
    elif for_you == "yes":
        label = "Built for your spec"
    elif for_you == "any":
        label = "Fits any spec"
    elif for_you == "spec":
        label = ("Not made for your spec. Fine while levelling" if early
                 else "Not made for your spec: maybe not the best for you")
        kb.extend(["gear-fit:built-for"] + (["gear-fit:capstone-at-40"] if early else []))
    else:
        label = f"Made for {who}, not for {'an' if class_words[:1] in 'aeiou' else 'a'} {class_words}"
        kb.append("gear-fit:built-for")
    notes = []
    if lower:
        notes.append(f"{lower['item'].capitalize()}: you can wear {lower['best']}")
        kb.append("gear-fit:armor")
    if wasted >= WASTED_NOTE and little:
        verb = "do nothing" if none else "do little"
        article = "an" if spec_words[:1].lower() in "aeiou" else "a"
        notes.append(f"{_share_words(wasted)} {verb} for {article} {spec_words}")
        z = {**partial(cls, key), **zeroed(cls, key)}
        kb.extend(sorted({z[s] for s in (("heal",) if "healonly" in little else ()) + tuple(little) if s in z}))
    # The group-loot tip: who usually rolls first (the best-armour wearers
    # its stats are made for), when that isn't this character.
    tip = None
    first = bool(prio) and not among(prio) and not (for_you == "yes" and not lower and not
                                                     {r["role"] for r in prio} - ({role} if role else set()))
    if for_you == "spec":
        first = True
        tip = "In a group, don't roll Need if someone it's made for is there"
        kb.append("gear-fit:group-loot")
    elif first:
        lead = {r["role"] for r in prio} == {"healer"}
        tip = ("Healer item: in a group, let the healer roll first" if lead
               else f"In a group, {who_words(ref, prio)} usually get first pick")
        kb.append("gear-fit:group-loot")
    factor = {"spec": OTHER, "class": CLASS}.get(for_you, 1.0)
    if first and group_drop(sources or []):
        factor *= GROUP
    return {"for_you": for_you, "label": label, "notes": notes, "built_for": who,
            "built_for_specs": [[r["cls"], r["key"]] for r in rows], "wasted_pct": wasted,
            "wasted_stats": [STAT_WORDS.get(s, s) for s in little], "armor": lower, "tip": tip,
            "factor": round(factor, 3), "kb": list(dict.fromkeys(kb))}


def text(f: dict | None) -> str | None:
    """One line for a reader: the label, the notes and the tip."""
    if not f:
        return None
    bits = [f["label"], *(f.get("notes") or [])]
    if f.get("tip"):
        bits.append(f["tip"])
    return ". ".join(bits) + "."
