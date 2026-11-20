"""A dungeon's drops from the research knowledge base, for a dungeon whose
structured loot is empty: Forever's new dungeons that neither Polestar's
gear data nor forever-db's client tables cover yet (The Drowned City in
November 2026), while data/kb/research/dungeons.json lists every boss and
what it drops, as text ("Bosses and drops. • Zul'Alai (37): Berserker
Shackles (mail), ..." or "Drops. • Cloth: Glinteye Slippers (Saltspine),
...").

Each item: its name, the boss when the text gives one, the armour type and
role words the text puts next to it, and the item ID when one of our item
sources knows the name (forever-db's item names, Polestar's gear data, the
item catalog already loaded). No stats: the text has none, so whether an
item is made for him comes from those words (his armour type at his level,
his spec's role), and is None when the text says neither. Everything here
is marked `confidence: low` (one research note, not the client's tables);
the page shows only the items and their bosses, never where they come
from."""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

from wowstats import gearfit

LANE = Path(__file__).resolve().parents[2] / "data" / "kb" / "research" / "dungeons.json"
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
# A capitalised word in brackets is the boss unless it names a kind of item.
NOT_A_BOSS = re.compile(r"/|\b(?:neck|ring|trinket|cloak|shield|off-hand|wand|staff|mace|sword|axe|dagger|bow|gun|"
                        r"polearm|handed|set|bind|trash|chest|helm|boots|gloves|belt|bracers|quest)\b", re.I)


@functools.cache
def _statements() -> tuple[dict, ...]:
    try:
        return tuple(json.loads(LANE.read_text(encoding="utf-8")).get("statements") or ())
    except (OSError, ValueError):
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
    got = _ids().get(name.lower())
    if got is None:
        from wowstats import itemcard

        cat = itemcard.shared()
        for k, v in (getattr(cat, "cards", None) or {}).items():  # only what's loaded: no fetch here
            if str(v.get("n") or "").lower() == name.lower():
                return int(k)
    return got


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
    from wowstats.links import wowhead_url

    out = []
    for it in drops(dungeon):
        iid = item_id(it["name"])
        out.append({"name": it["name"], "boss": it["boss"], "armour": it["armour"], "roles": it["roles"],
                    "item_id": iid, "url": wowhead_url("item", iid) if iid else None,
                    "fit": fit(it, cls, spec, level), "confidence": "low"})
    rank = {True: 0, None: 1, False: 2}
    out.sort(key=lambda x: rank[x["fit"]])
    return out
