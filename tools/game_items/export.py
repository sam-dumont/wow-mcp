"""Writes data/game-items.json: every weapon and armour piece as World of
Warcraft: Forever's own client tables describe it, for the gear advice
(wowstats.gear: the score, who can wear it, from what level).

The client keeps an item in two places, and this reads both:

- Its files: Forever's ItemSparse and Item tables as wago.tools exports them
  (its CSV downloads, which its robots.txt allows), at the build named in
  the file (`--build`, Forever's latest by default: tools/kb/watch.py moves
  the pin).
- Its hotfix cache: the items Forever changed or added since the files were
  built reach the client as hotfixes (encrypted in the files, so wago.tools
  can't read them) and land in `_forever_/Cache/ADB/<locale>/DBCache.bin`.
  `--dbcache` reads one, read-only, with the shipper's own parser
  (shipper/wow_stats.py): only its ItemSparse and Item rows, never anything
  else in it. A hotfix row wins over the file's. The cache's build must be
  the files' build. Without it, about half of Forever's changed items keep
  their old numbers, so the file records how many rows came from it.
- An item neither has (680 of Polestar's 10,621, none with a known source)
  takes the Classic Era client's numbers (`--era`), as Polestar's item table
  did, marked `"from": "era"`.

The numbers are the tooltip's, computed the client's way, and checked
against ForeverDB.net's (tests/test_gear_foreverdb.py: 6,441 of 6,444 of its
ladder items equal):

- a stat = its allocation (StatPercentEditor) x the item level's budget
  (RandPropPoints: Epic for epics, Superior for rares, Good below; the slot's
  column) / 10000, rounded half away from zero; stat 50 is bonus armour;
- weapon damage from the ItemDamage table of its kind (one-hand, two-hand,
  caster versions, ranged, thrown, wand) at its item level and quality, the
  spread from DmgVariance; DPS = (min + max) / 2 / speed;
- armour = ItemArmorTotal (item level, material) x ArmorLocation (slot) x
  ItemArmorQuality, or ItemArmorShield for a shield, plus bonus armour;
- "% hit", "% crit" and attack power on an equip spell (ItemEffect,
  SpellEffect) as ForeverDB scores them: hit 10 and crit 14 rating a
  percent, the first spell's attack power (a creature-type one too); a hit
  that only works with a bow, gun or crossbow (SpellEquippedItems) is
  `rangedHit`, which only a hunter's score counts.

Set bonuses stay ForeverDB's (data/foreverdb-weights.json `sets`): its
shares equal the client's ItemSet and ItemSetSpell bonuses on all 416 pieces
it lists, but it leaves some sets out (Tier 0, Stormcloth) and some bonus
lines (a healing-only one), and the score is theirs.

Game data (c) Blizzard Entertainment, used as facts. Stdlib only, the same
bytes for the same inputs:

    uv run python tools/game_items/export.py \\
        --dbcache "/Applications/World of Warcraft/_forever_/Cache/ADB/enUS/DBCache.bin"
    uv run python tools/game_items/export.py --check    # offline: the committed file
"""

from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "game-items.json"
ERA_BUILD = "1.15.9.70576"  # Classic Era's build the fallback rows come from
FILE_TABLES = ("ItemSparse", "Item", "RandPropPoints", "ItemDamageOneHand", "ItemDamageOneHandCaster",
               "ItemDamageTwoHand", "ItemDamageTwoHandCaster", "ItemDamageRanged", "ItemDamageThrown",
               "ItemDamageWand", "ItemArmorTotal", "ArmorLocation", "ItemArmorQuality", "ItemArmorShield",
               "ItemEffect", "ItemXItemEffect", "SpellEffect", "SpellEquippedItems")
ERA_TABLES = ("ItemSparse", "Item", "ItemEffect", "SpellEffect")
# Weapons and armour the gear advice can score: wearable slots, no shirts,
# tabards, bags, ammo or quivers (wowstats.gear SLOTS_OF).
GEAR_INV = {1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21, 22, 23, 25, 26, 28}
# ForeverDB's stat ids -> the gear advice's stat keys (tests/test_gear_foreverdb.py NAMES).
STAT_KEYS = {3: "agi", 4: "str", 5: "int", 6: "spi", 7: "sta", 12: "defense", 13: "dodgeRating",
             14: "parryRating", 15: "blockRating", 31: "hitRating", 32: "critRating", 36: "haste",
             37: "expertise", 38: "ap", 39: "rap", 41: "heal", 42: "sp", 43: "mp5", 46: "hp5", 47: "spellPen",
             48: "blockValue", 84: "spHoly", 85: "spFire", 86: "spNature", 87: "spFrost", 88: "spShadow",
             89: "spArcane"}
DMGHEAL, BONUS_ARMOR = 45, 50
# The budget column of an inventory type (wowstats.itemfacts.SLOT_GROUP).
SLOT_GROUP = {1: 0, 4: 0, 5: 0, 7: 0, 17: 0, 20: 0, 3: 1, 6: 1, 8: 1, 10: 1, 12: 1, 2: 2, 9: 2, 11: 2, 14: 2, 16: 2,
              23: 2, 13: 3, 21: 3, 22: 3, 15: 4, 25: 4, 26: 4, 28: 4}
CASTER_WEAPON = 0x200  # ItemSparse Flags_1
MATERIAL = {1: ("Cloth", "Clothmodifier"), 2: ("Leather", "Leathermodifier"), 3: ("Mail", "Chainmodifier"),
            4: ("Plate", "Platemodifier")}
ALL_CLASSES = 1 | 2 | 4 | 8 | 16 | 64 | 128 | 256 | 1024
SCHOOLS = {2: "spHoly", 4: "spFire", 8: "spNature", 16: "spFrost", 32: "spShadow", 64: "spArcane"}
PRIMARY = {0: "str", 1: "agi", 2: "sta", 3: "int", 4: "spi"}
# The five items the owner named (2026-11-24), as ForeverDB.net shows them:
# --check holds the file to them.
CHECKS = {
    "2549": {"name": "Staff of the Shade", "stats": {"sta": 15, "spShadow": 37, "dps": 22.3077}},
    "9381": {"name": "Earthen Rod", "stats": {"heal": 9, "sp": 3, "dps": 35.5882}},
    "286989": {"name": "Violet Sorcerer's Wraps", "stats": {"int": 9, "hitRating": 10, "armor": 31}},
    "273040": {"name": "Spine of the Devourer", "stats": {"sta": 14, "spellPen": 3, "dps": 27.7778}},
    "7001": {"name": "Gravestone Scepter", "stats": {"spi": 3, "spShadow": 6, "dps": 29.0}},
}


def i(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def half_up(x: float) -> int:
    """Rounded half away from zero (a -3.5 Spirit shows -4)."""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def wago(table: str, build: str) -> list[dict]:
    from wowstats.upstream import WAGO_URL, fetch, table_url

    body = fetch(table_url(WAGO_URL, table, build), timeout=180)
    if not body:
        raise SystemExit(f"wago.tools has no {table} for {build}")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        raise SystemExit(f"wago.tools has no {table} rows for {build}")
    return rows


def flat(row: dict) -> dict:
    """A hotfix row (lists for arrays) in the CSV's shape (Name_0, Name_1...)."""
    out = {}
    for k, v in row.items():
        if isinstance(v, list):
            out.update({f"{k}_{n}": x for n, x in enumerate(v)})
        else:
            out[k] = v
    return out


def hotfix_rows(path: Path) -> tuple[int, dict[str, dict], dict[str, dict]]:
    """(build, ItemSparse rows, Item rows) of a DBCache.bin, each item's
    newest valid row (the shipper's own rule: a pushed hotfix beats a row the
    client asked for)."""
    sys.path.insert(0, str(ROOT / "shipper"))
    import wow_stats as ws

    data = path.read_bytes()
    if data[:4] != b"XFTH":
        raise SystemExit(f"{path}: not a hotfix cache")
    version, build = struct.unpack_from("<II", data, 4)
    pos = ws.HOTFIX_HEADER.get(version)
    if pos is None:
        raise SystemExit(f"{path}: hotfix cache version {version} unknown")
    newest: dict = {}
    n = 0
    while pos + 32 <= len(data) and data[pos:pos + 4] == b"XFTH":
        _region, push, _uniq, table, rid, size, status = struct.unpack_from("<iiIIIiB", data, pos + 4)
        if size < 0 or pos + 32 + size > len(data):
            break
        n += 1
        if table in (ws.HOTFIX_SPARSE, ws.HOTFIX_ITEM):
            rank = (push < ws.HOTFIX_QUERIED, push, n)
            old = newest.get((table, rid))
            if old is None or rank > old[0]:
                newest[(table, rid)] = (rank, status, pos + 32, size)
        pos += 32 + size
    sparse, item = {}, {}
    for (table, rid), (_rank, status, start, size) in newest.items():
        if status != ws.HOTFIX_VALID:
            continue
        raw = data[start:start + size]
        row = ws._sparse_row(raw) if table == ws.HOTFIX_SPARSE else ws._item_row(raw)
        if row is not None:
            (sparse if table == ws.HOTFIX_SPARSE else item)[str(rid)] = flat(row)
    return build, sparse, item


class Client:
    """The budget, damage, armour and effect tables of one build."""

    def __init__(self, t: dict[str, list[dict]]):
        self.budget = {r["ID"]: r for r in t["RandPropPoints"]}
        self.damage = {name: {r["ItemLevel"]: r for r in t[name]}
                       for name in FILE_TABLES if name.startswith("ItemDamage")}
        self.armor_total = {r["ItemLevel"]: r for r in t["ItemArmorTotal"]}
        self.armor_loc = {r["ID"]: r for r in t["ArmorLocation"]}
        self.armor_quality = {r["ID"]: r for r in t["ItemArmorQuality"]}
        self.shield = {r["ItemLevel"]: r for r in t["ItemArmorShield"]}
        self.effects = spell_effects(t["SpellEffect"])
        ie = {r["ID"]: r for r in t["ItemEffect"]}
        self.equip: dict[str, list[dict]] = collections.defaultdict(list)
        for r in t["ItemXItemEffect"]:
            e = ie.get(r["ItemEffectID"])
            if e and e["TriggerType"] == "1":
                self.equip[r["ItemID"]].append(e)
        for v in self.equip.values():
            v.sort(key=lambda e: (i(e["LegacySlotIndex"]), i(e["ID"])))
        self.ranged_only = ranged_only(t["SpellEquippedItems"])

    def stats(self, r: dict) -> tuple[dict[int, int], int]:
        """Stat id -> amount, and bonus armour."""
        p = self.budget.get(str(i(r["ItemLevel"])))
        g = SLOT_GROUP.get(i(r["InventoryType"]))
        if not p or g is None:
            return {}, 0
        q = i(r["OverallQualityID"])
        col = "Epic" if q >= 4 else "Superior" if q == 3 else "Good"
        points = f(p.get(f"{col}F_{g}")) or f(p.get(f"{col}_{g}"))
        out: dict[int, int] = {}
        for n in range(10):
            sid, alloc = i(r.get(f"StatModifier_bonusStat_{n}")), f(r.get(f"StatPercentEditor_{n}"))
            if sid > 0 and alloc:
                out[sid] = out.get(sid, 0) + half_up(alloc * points / 10000)
        return out, out.pop(BONUS_ARMOR, 0)

    def weapon(self, r: dict, subclass: int) -> tuple[int, int] | None:
        inv, q = i(r["InventoryType"]), i(r["OverallQualityID"])
        caster = "Caster" if i(r.get("Flags_1")) & CASTER_WEAPON else ""
        name = ("ItemDamageWand" if subclass == 19 else "ItemDamageThrown" if inv == 25 else
                "ItemDamageRanged" if inv in (15, 26) else f"ItemDamageTwoHand{caster}" if inv == 17 else
                f"ItemDamageOneHand{caster}")
        row = self.damage[name].get(str(i(r["ItemLevel"])))
        speed = i(r["ItemDelay"])
        if not row or not speed:
            return None
        avg = f(row[f"Quality_{q}"]) * speed / 1000
        var = f(r["DmgVariance"])
        return math.floor(avg * (1 - var / 2)), math.floor(avg * (1 + var / 2) + 0.5)

    def armor(self, r: dict, subclass: int) -> float:
        inv, q, lvl = i(r["InventoryType"]), i(r["OverallQualityID"]), str(i(r["ItemLevel"]))
        if subclass == 6:
            row = self.shield.get(lvl)
            return f(row[f"Quality_{q}"]) if row else 0.0
        if inv == 16:  # a cloak is cloth
            subclass = 1
        if subclass not in MATERIAL:
            return 0.0
        tot, loc, aq = self.armor_total.get(lvl), self.armor_loc.get(str(5 if inv == 20 else inv)), \
            self.armor_quality.get(lvl)
        if not (tot and loc and aq):
            return 0.0
        mat, mod = MATERIAL[subclass]
        return f(tot[mat]) * f(loc[mod]) * f(aq[f"Qualitymod_{q}"])

    def equip_lines(self, item_id: str) -> dict[str, float]:
        """ForeverDB's percent lines from equip spells: hit and crit as
        rating, the first spell's attack power."""
        out: dict[str, float] = {}
        ap_done = False
        for e in self.equip.get(item_id, []):
            hit_key = "rangedHit" if e["SpellID"] in self.ranged_only else "hitRating"
            hit = crit = ap = 0.0
            for a, _m, v in self.effects.get(e["SpellID"], []):
                if a in (54, 55):
                    hit = max(hit, v)
                elif a in (52, 57, 290):
                    crit = max(crit, v)
                elif a in (99, 102):
                    ap = max(ap, v)
            if hit:
                out[hit_key] = out.get(hit_key, 0) + hit * 10
            if crit:
                out["critRating"] = out.get("critRating", 0) + crit * 14
            if ap and not ap_done:
                out["ap"] = out.get("ap", 0) + ap
                ap_done = True
        return out


def ranged_only(rows: list[dict]) -> set[str]:
    """Spells that only work with a bow, gun or crossbow equipped."""
    ranged = (1 << 2) | (1 << 3) | (1 << 18)
    return {r["SpellID"] for r in rows if r["EquippedItemClass"] == "2" and i(r["EquippedItemSubclass"])
            and not i(r["EquippedItemSubclass"]) & ~ranged}


def spell_effects(rows: list[dict]) -> dict[str, list[tuple[int, int, float]]]:
    """Spell id -> [(aura, misc value, amount)] of its aura effects."""
    out: dict[str, list] = collections.defaultdict(list)
    for e in rows:
        if e.get("Effect") != "6":
            continue
        # Forever's rows carry the amount as a float; Classic Era's as base
        # points plus one die side.
        amount = f(e.get("EffectBasePointsF")) or f(e.get("EffectBasePoints")) + f(e.get("EffectDieSides"))
        out[e["SpellID"]].append((i(e["EffectAura"]), i(e["EffectMiscValue_0"]), amount))
    return out


def bonus_stats(effects: list[tuple[int, int, float]]) -> dict[str, float]:
    """One set bonus spell's stats, ForeverDB's way (percents kept as percent)."""
    o: dict[str, float] = collections.Counter()
    hit = 0.0
    for a, m, v in effects:
        if a == 29 and m in PRIMARY:
            o[PRIMARY[m]] += v
        elif a == 13 and m in (126, 127):
            o["dmg"] += v
        elif a == 13 and m in SCHOOLS:
            o[SCHOOLS[m]] += v
        elif a == 135:
            o["heal"] += v
        elif a == 99:
            o["ap"] += v
        elif a == 124:
            o["rap"] += v
        elif a == 85 and m == 0:
            o["mp5"] += v
        elif a == 161:
            o["hp5"] += v
        elif (a == 22 or a == 674) and m == 1:
            o["armor"] += v
        elif a in (54, 55):
            hit = max(hit, v)
        elif a in (52, 57, 290):
            o["critRating"] += v
        elif a == 30 and m == 95:
            o["defense"] += v
        elif a == 49:
            o["dodgeRating"] += v
        elif a == 47:
            o["parryRating"] += v
        elif a == 51:
            o["blockRating"] += v
        elif a == 158:
            o["blockValue"] += v
    if hit:
        o["hitRating"] += hit
    if o.get("rap") and o.get("rap") == o.get("ap"):  # "+X Attack Power" sets both: one line
        o.pop("rap")
    return o



def stat_keys(raw: dict[int, int]) -> dict[str, float]:
    """ForeverDB's stat ids as the gear advice's keys: "+X damage and
    healing" (45) is spell power and healing alike, unless the item also has
    a healing or a damage line (then one `dmgheal`)."""
    out: dict[str, float] = {}
    alone = not any(k in (41, 42) for k in raw)
    for k, v in raw.items():
        if k == DMGHEAL:
            for key in (("sp", "heal") if alone else ("dmgheal",)):
                out[key] = out.get(key, 0) + v
        elif k in STAT_KEYS:
            out[STAT_KEYS[k]] = out.get(STAT_KEYS[k], 0) + v
    return out


def era_item(r: dict, effects: dict, equip: list[str], ranged: set[str]) -> dict[str, float]:
    """A Classic Era row's stats: amounts in the row, the rest on equip spells."""
    raw: dict[int, int] = {}
    for n in range(10):
        sid, amount = i(r.get(f"StatModifier_bonusStat_{n}")), i(r.get(f"StatModifier_bonusAmount_{n}"))
        if sid >= 0 and amount:
            raw[sid] = raw.get(sid, 0) + amount
    st = stat_keys(raw)
    for sid in equip:
        b = bonus_stats(effects.get(sid, []))
        if sid in ranged and b.get("hitRating"):
            b["rangedHit"] = b.pop("hitRating") * 10
        for k in ("hitRating", "critRating", "dodgeRating", "parryRating", "blockRating"):
            if b.get(k):
                b[k] *= {"hitRating": 10, "critRating": 14, "dodgeRating": 12, "parryRating": 15, "blockRating": 5}[k]
        dmg, heal = b.pop("dmg", 0), b.pop("heal", 0)
        if dmg and dmg == heal:
            b["sp"] = b.get("sp", 0) + dmg
            b["heal"] = b.get("heal", 0) + heal
        else:
            if dmg:
                b["sp"] = b.get("sp", 0) + dmg
            if heal:
                b["heal"] = b.get("heal", 0) + heal
        b.pop("armor", None)
        for k, v in b.items():
            if v:
                st[k] = st.get(k, 0) + v
    return st


def entry(r: dict, it: dict, stats: dict[str, float], armor: float, dmg, origin: str) -> dict:
    subclass = i(it["SubclassID"])
    e: dict = {"name": r["Display_lang"].strip(), "inv": i(r["InventoryType"]), "ilvl": i(r["ItemLevel"]),
               "req": max(0, i(r["RequiredLevel"])), "quality": i(r["OverallQualityID"]),
               "class": i(it["ClassID"]), "subclass": subclass}
    st = {k: (round(v, 4) if isinstance(v, float) and not v.is_integer() else int(v)) for k, v in stats.items() if v}
    if armor:
        st["armor"] = half_up(armor)
    if dmg and i(r["ItemDelay"]):
        e["speed"] = i(r["ItemDelay"])
        e["dmg"] = list(dmg)
        st["dps"] = (dmg[0] + dmg[1]) / 2 / (i(r["ItemDelay"]) / 1000)
    allow = i(r.get("AllowableClass"))
    if allow > 0 and allow & ALL_CLASSES != ALL_CLASSES:
        e["allow"] = allow
    if i(r.get("RequiredSkill")):
        e["required_skill"] = i(r["RequiredSkill"])
        e["required_skill_rank"] = i(r.get("RequiredSkillRank"))
    if i(r.get("MaxCount")) == 1:
        e["unique"] = True
    e["stats"] = dict(sorted(st.items()))
    if origin != "file":
        e["from"] = origin
    return e


def build_items(t: dict[str, list[dict]], hot_sparse: dict, hot_item: dict, era: dict[str, list[dict]] | None):
    cl = Client(t)
    sparse = {r["ID"]: r for r in t["ItemSparse"]}
    origin = dict.fromkeys(sparse, "file")
    for k, r in hot_sparse.items():
        sparse[k] = r
        origin[k] = "hotfix"
    item = {r["ID"]: r for r in t["Item"]}
    item.update(hot_item)
    out: dict[str, dict] = {}
    for k, r in sparse.items():
        it = item.get(k)
        if not it or i(it["ClassID"]) not in (2, 4) or i(r["InventoryType"]) not in GEAR_INV:
            continue
        if not r["Display_lang"].strip():
            continue
        raw, bonus = cl.stats(r)
        stats = stat_keys(raw)
        for key, v in cl.equip_lines(k).items():
            stats[key] = stats.get(key, 0) + v
        sub = i(it["SubclassID"])
        dmg = cl.weapon(r, sub) if i(it["ClassID"]) == 2 else None
        armor = (cl.armor(r, sub) if i(it["ClassID"]) == 4 else 0.0) + bonus
        e = entry(r, it, stats, armor, dmg, origin[k])
        out[k] = e
    era_n = 0
    if era:
        effects = spell_effects(era["SpellEffect"])
        equip: dict[str, list[str]] = collections.defaultdict(list)
        for e in era["ItemEffect"]:
            if e["TriggerType"] == "1":
                equip[e["ParentItemID"]].append(e["SpellID"])
        eitem = {r["ID"]: r for r in era["Item"]}
        for r in era["ItemSparse"]:
            k = r["ID"]
            it = item.get(k) or eitem.get(k)
            if k in out or k in sparse or not it or i(it["ClassID"]) not in (2, 4) \
                    or i(r["InventoryType"]) not in GEAR_INV or not r["Display_lang"].strip():
                continue
            dmg = (i(r["MinDamage_0"]), i(r["MaxDamage_0"])) if i(it["ClassID"]) == 2 and i(r["MaxDamage_0"]) else None
            e = entry(r, it, era_item(r, effects, equip.get(k, []), cl.ranged_only), f(r.get("Resistances_0")), dmg,
                      "era")
            out[k] = e
            era_n += 1
    return dict(sorted(out.items(), key=lambda kv: int(kv[0]))), era_n


def check(path: Path) -> list[str]:
    """What's wrong with the committed file (offline)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{path}: {e}"]
    bad = []
    src = doc.get("source") or {}
    if doc.get("schema") != 1 or not src.get("build") or not isinstance(doc.get("items"), dict):
        return ["not a game-items file (schema 1, source.build, items)"]
    hc = src.get("hotfix_cache") or {}
    if hc and str(hc.get("build")) != src["build"].rsplit(".", 1)[-1]:
        bad.append(f"hotfix cache build {hc.get('build')} isn't the files' {src['build']}")
    for k, e in doc["items"].items():
        if not k.isdigit() or not isinstance(e.get("stats"), dict) or not e.get("name") \
                or e.get("inv") not in GEAR_INV or e.get("class") not in (2, 4):
            bad.append(f"item {k}: malformed")
            if len(bad) > 20:
                break
    for k, want in CHECKS.items():
        got = doc["items"].get(k)
        if not got:
            bad.append(f"{want['name']} ({k}) missing")
            continue
        if got["name"] != want["name"]:
            bad.append(f"{k}: named {got['name']!r}, not {want['name']!r}")
        for s, v in want["stats"].items():
            if abs((got["stats"].get(s) or 0) - v) > 1e-3:
                bad.append(f"{want['name']}: {s} {got['stats'].get(s)}, ForeverDB.net shows {v}")
    return bad


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", help="Forever's client build (default: wago.tools' latest wowf)")
    ap.add_argument("--dbcache", type=Path, help="a Forever client's DBCache.bin, same build")
    ap.add_argument("--era", default=ERA_BUILD,
                    help="Classic Era build for the items Forever has nowhere ('' for none)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true", help="check the committed file, offline")
    a = ap.parse_args(argv)
    if a.check:
        bad = check(a.out)
        for b in bad:
            print(b, file=sys.stderr)
        print(f"{a.out}: {'ok' if not bad else f'{len(bad)} problems'}")
        return 1 if bad else 0
    from wowstats.upstream import PRODUCT, WAGO_URL, build_of, builds_url, fetch

    build = a.build or build_of(fetch(builds_url(WAGO_URL, PRODUCT)))
    hot_build, hot_sparse, hot_item = (None, {}, {})
    if a.dbcache:
        hot_build, hot_sparse, hot_item = hotfix_rows(a.dbcache)
        if str(hot_build) != build.rsplit(".", 1)[-1]:
            raise SystemExit(f"{a.dbcache} is build {hot_build}, the files are {build}: log in with that client first")
    t = {name: wago(name, build) for name in FILE_TABLES}
    era = {name: wago(name, a.era) for name in ERA_TABLES} if a.era else None
    items, era_n = build_items(t, hot_sparse, hot_item, era)
    n_hot = sum(1 for e in items.values() if e.get("from") == "hotfix")
    doc = {
        "schema": 1,
        "source": {
            "via": "wago.tools", "product": PRODUCT, "build": build, "tables": list(FILE_TABLES),
            "hotfix_cache": {"build": hot_build, "items": n_hot,
                             "note": "ItemSparse and Item rows of a Forever client's DBCache.bin, same build"}
            if a.dbcache else None,
            "era": {"product": "wow_classic_era", "build": a.era, "items": era_n, "tables": list(ERA_TABLES),
                    "note": "items Forever has in neither its files nor the hotfix cache"} if a.era else None,
            "note": "Game data (c) Blizzard Entertainment, used as facts",
        },
        "items": items,
    }
    # indent 1 (one key per line, like data/dungeons/wings.json): git merges disjoint changes, where the
    # one-line file conflicted on every PR that touched it (2026-11-25).
    a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{a.out}: {len(items)} items ({n_hot} from the hotfix cache, {era_n} from Classic Era), build {build}")
    bad = check(a.out)
    for b in bad:
        print(b, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
