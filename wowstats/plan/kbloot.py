"""A dungeon's drops from the research knowledge base, for a dungeon whose
structured loot is empty: Forever's new dungeons that neither Polestar's
gear data nor forever-db's client tables cover yet (The Drowned City in
November 2026), while data/kb/research/dungeons.json lists every boss and
what it drops, as text ("Bosses and drops. • Zul'Alai (37): Berserker
Shackles (mail), ..." or "Drops. • Cloth: Glinteye Slippers (Saltspine),
...").

Each item: its name, the boss when the text gives one, the armour type and
role words the text puts next to it, and the item ID when one of our item
sources knows the name (forever-db's item names, Polestar's gear data,
then the player's own item facts: wowstats.itemfacts, read from their game
client's hotfix cache).

With the item's facts (Polestar's gear data first, else the player's), the
row carries its item level, slot, armour type and stats, and whether it is
made for him is the gear advice's own check on those stats
(wowstats.gearfit: his spec, his best armour type, what his class can
use). Without them, the words next to it decide (his armour type at his
level, his spec's role), and None when the text says neither. Everything
here is marked `confidence: low` (the list is one research note); the page
shows only the items, their bosses and their facts, never where they come
from."""

from __future__ import annotations

import functools
import json
import logging
import re

from wowstats import gearfit, itemfacts
from wowstats.analysis import kb

log = logging.getLogger(__name__)
# Found through kb.data_file: the checkout's data/ or the installed package's
# wowstats/data/ (the image has no checkout; a parents[2] path here once gave
# an empty drops list there, silently).
LANE = "kb/research/dungeons.json"
LEADS = ("Drops", "Bosses and drops", "Notable drops")
ARMOUR = {"cloth": 1, "leather": 2, "mail": 3, "plate": 4}
# Words next to an item that say whom it's for: the roles of gearfit.ROLES.
ROLE_WORDS = {
    "caster": {"caster", "healer"}, "intellect": {"caster", "healer"}, "spell": {"caster", "healer"},
    "healer": {"healer"}, "healing": {"healer"},
    "strength": {"melee", "tank"}, "agility": {"melee", "ranged"}, "attack power": {"melee", "ranged"},
    "melee": {"melee"}, "tank": {"tank"}, "ranged": {"ranged"}, "spirit": {"caster", "healer"},
    "physical": {"melee", "ranged", "tank"},
}
# Bullet heads that group items rather than name a boss ("Cloth", "Caster
# and healer", "Healer-leaning"); recipe heads are left out: the panel is
# gear.
GROUPS = set(ARMOUR) | set(ROLE_WORDS) | {"weapons", "trinkets", "other", "jewellery", "jewelry", "accessories"}
FILLER = {"and", "leaning"}
RECIPES = {"recipe", "recipes"}
# The short stat lines of a row, in the tooltip's order (the card has the full ones).
SHORT = (("str", "Strength"), ("agi", "Agility"), ("sta", "Stamina"), ("int", "Intellect"), ("spi", "Spirit"),
         ("ap", "Attack Power"), ("rap", "Ranged Attack Power"), ("sp", "Spell Power"), ("heal", "Healing"),
         ("spArcane", "Arcane Spell Damage"), ("spFire", "Fire Spell Damage"), ("spFrost", "Frost Spell Damage"),
         ("spHoly", "Holy Spell Damage"), ("spNature", "Nature Spell Damage"), ("spShadow", "Shadow Spell Damage"),
         ("mp5", "Mana per 5 sec."), ("hp5", "Health per 5 sec."), ("defense", "Defense"),
         ("blockValue", "Block Value"))
ARMOUR_NAME = {v: k for k, v in ARMOUR.items()}
# A capitalised word in brackets is the boss unless it names a kind of item.
NOT_A_BOSS = re.compile(r"/|\b(?:neck|ring|trinket|cloak|shield|off-hand|wand|staff|mace|sword|axe|dagger|bow|gun|"
                        r"polearm|handed|set|bind|trash|chest|helm|boots|gloves|belt|bracers|quest)\b", re.I)


@functools.cache
def _statements() -> tuple[dict, ...]:
    path = kb.data_file(LANE)
    if path is None:
        log.warning("kbloot: %s is not in any data directory, dungeon drops will be empty", LANE)
        return ()
    try:
        return tuple(json.loads(path.read_text(encoding="utf-8")).get("statements") or ())
    except (OSError, ValueError):
        log.warning("kbloot: %s is unreadable, dungeon drops will be empty", path, exc_info=True)
        return ()


def _split(text: str) -> list[str]:
    """Top-level parts of a list: commas and semicolons outside brackets."""
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch in ",;" and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [p.strip() for p in out + [cur] if p.strip()]


def _item(part: str, boss: str | None, group: list[str], prev: str | None) -> list[dict]:
    m = re.fullmatch(r"(.+?)\s*(?:\((.*)\))?", part.strip().rstrip("."))
    name, note = (m.group(1).strip(), (m.group(2) or "").strip()) if m else (part.strip(), "")
    if not name or not name[0].isupper() or name.lower() in ("none", "trash") or name.endswith(" set"):
        return []
    words = [w.strip() for w in note.split(",") if w.strip()]
    armour = next((g for g in group if g in ARMOUR), None)
    roles: set[str] = {r for g in group for r in ROLE_WORDS.get(g, ())}
    who = boss
    for w in words:
        low = w.lower()
        # "Glinteye Slippers (Saltspine)", "(Unbound Spirit)": a name in
        # title case is the boss, under an armour heading; "(Strength)",
        # "(Spirit neck)" and "(attack power trinket)" are not.
        tokens = w.split()
        if (all(t[:1].isupper() for t in tokens) and not (len(tokens) == 1 and low in ROLE_WORDS)
                and not re.search(r"\d", w) and not NOT_A_BOSS.search(w)):
            who = who or w
            continue
        for k in ARMOUR:
            if re.search(rf"\b{k}\b", low):
                armour = armour or k
        for k, r in ROLE_WORDS.items():
            if re.search(rf"\b{k}\b", low):
                roles |= r
    # "Mugg's Jabber / Mugg's Crosser": two items. "Krol'dok Warplate Helmet
    # and Chest": two of one name. "Warplate Gauntlets, Legplates and
    # Pauldrons": the last two take the first one's name.
    names = [n.strip() for n in name.split(" / ") if n.strip()]
    a, sep, b = name.rpartition(" and ")
    if sep and " " not in b:
        if " " in a:
            names = [a, a.rsplit(" ", 1)[0] + " " + b]
        elif prev and " " in prev:
            stem = prev.rsplit(" ", 1)[0]
            names = [f"{stem} {a}", f"{stem} {b}"]
    return [{"name": n, "boss": who, "armour": armour, "roles": sorted(roles)} for n in names]


@functools.cache
def drops(dungeon: str) -> tuple[dict, ...]:
    """Every item the KB says the dungeon drops, once each, in its order."""
    out: list[dict] = []
    seen: set[str] = set()
    for s in _statements():
        if not str(s.get("title") or "").startswith(dungeon + " (") or not str(s.get("text") or "").startswith(LEADS):
            continue
        for bullet in s["text"].split("•")[1:]:
            head, colon, rest = bullet.partition(":")
            if not colon:
                continue
            head = head.strip()
            words = [w for w in re.findall(r"[a-z]+", head.lower()) if w not in FILLER]
            if words and set(words) <= RECIPES:
                continue
            group = words if words and set(words) <= GROUPS else []
            boss = None if group else re.sub(r"\s*\(.*\)$", "", re.sub(r"^Rare\s+", "", head)).strip() or None
            # "...Waterlogged Burial Shroud. The wiki adds Linken's Boots of Iron (mail)."
            rest = re.sub(r"\.\s+(?:The wiki|Wowhead|Another source) adds\s+", ", ", rest)
            rest = rest.split(". ")[0]
            prev = None
            for part in _split(rest):
                for it in _item(part, boss, group, prev):
                    prev = it["name"]
                    if it["name"].lower() not in seen:
                        seen.add(it["name"].lower())
                        out.append(it)
    return tuple(out)


@functools.cache
def _ids() -> dict[str, int]:
    """Item name -> ID from the item names we have on disk."""
    from wowstats import forever, gear

    out: dict[str, int] = {}
    for k, v in ((forever.data() or {}).get("items") or {}).items():
        out.setdefault(str(v).lower(), int(k))
    ref = gear.reference()
    for k, v in ((ref.data if ref.available else {}).get("names") or {}).items():
        out.setdefault(str(v).lower(), int(k))
    return out


def item_id(name: str) -> int | None:
    # The files on disk, never the item catalog: whether it happened to be
    # loaded made the answer change from run to run, and asking for it here
    # created the shared catalog early (a sealed bundle in the tests then got
    # icons it shouldn't have). Then the facts of the person whose answer
    # this is (set around it, wowstats.itemfacts.using): the files win.
    got = _ids().get(name.lower())
    if got is None:
        facts = itemfacts.current()
        if facts:
            return facts.by_name(name)
    return got


def info_of(iid: int | None) -> dict | None:
    """The item's facts: Polestar's gear data when it has them (it wins),
    else the player's own (wowstats.itemfacts)."""
    from wowstats import gear

    if not iid:
        return None
    ref = gear.reference()
    got = ref.item(iid) if ref.available else None
    if got is None:
        facts = itemfacts.current()
        got = facts.info(iid) if facts else None
    return got


def stat_lines(stats: dict) -> list[str]:
    same = stats.get("sp") and stats.get("sp") == stats.get("heal")  # spell power already heals
    return [f"+{round(stats[k])} {word}" for k, word in SHORT
            if stats.get(k) and not (same and k == "heal")]


def stat_fit(info: dict, cls: str | None, spec: str | None, level: int) -> bool | None:
    """Made for him by the item's own stats: the gear advice's check
    (wowstats.gearfit.fit) for his spec at his level. False for what his
    class can't use or a lower armour type than his best; None when his
    role is unknown and the armour says nothing."""
    from wowstats import gear

    ref = gear.reference()
    if not cls or not ref.available:
        return None
    if not gear.usability(ref, info, cls, level)[0]:
        return False
    key, how = gear.resolve_spec(ref, cls, {}, spec or None)
    w, _ = gear.weights_for(ref, cls, key) if key else (None, None)
    if not w:
        return None
    f = gearfit.fit(ref, info, cls, key, how, level, w)
    if f["armor"]:
        return False
    return {"yes": True, "any": True, "spec": False, "class": False}.get(f["for_you"])


def fit(it: dict, cls: str | None, spec: str | None, level: int) -> bool | None:
    """Made for him, by the words the KB puts next to it: his best armour
    type at his level, his spec's role (gearfit.ROLES). None when the text
    says neither."""
    from wowstats import gear

    if not cls:
        return None
    verdicts = []
    if it.get("armour"):
        ref = gear.reference()
        prof = ((ref.gear if ref.available else {}).get("PROFICIENCY") or {}).get(cls)
        best = gearfit.best_armor(prof, level)
        verdicts.append(ARMOUR[it["armour"]] == best if best else None)
    role = gearfit.role_of(cls, (spec or "").lower() or None)
    if it.get("roles") and role:
        verdicts.append(role in it["roles"])
    verdicts = [v for v in verdicts if v is not None]
    return all(verdicts) if verdicts else None


def for_block(dungeon: str, cls: str | None, spec: str | None, level: int) -> list[dict]:
    """The dungeon's KB drops for the panel: made for him first, then the
    ones the text doesn't say, then the rest."""
    from wowstats import itemcard
    from wowstats.links import wowhead_url

    out = []
    for it in drops(dungeon):
        iid = item_id(it["name"])
        info = info_of(iid)
        row = {"name": it["name"], "boss": it["boss"], "armour": it["armour"], "roles": it["roles"],
               "item_id": iid, "url": wowhead_url("item", iid) if iid else None,
               "fit": fit(it, cls, spec, level), "confidence": "low"}
        if info:
            armour = ARMOUR_NAME.get(gearfit.armor_of(info) or 0)
            if armour or info.get("class") == 2 or info.get("inv") in (2, 11, 12, 16):
                row["armour"] = armour  # the item's own type, over the note's word
            row.update({"item_level": info.get("ilvl"), "quality": info.get("quality"),
                        "slot_name": itemcard.SLOT_TEXT.get(info.get("inv")), "stats": stat_lines(info["stats"])})
            if info.get("stats"):
                row["fit"] = stat_fit(info, cls, spec, level)
            else:
                row["fit"] = fit({**it, "armour": row["armour"]}, cls, spec, level)
        out.append(row)
    rank = {True: 0, None: 1, False: 2}
    out.sort(key=lambda x: rank[x["fit"]])
    return out
