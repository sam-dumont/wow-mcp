"""Item cards (the in-game tooltip) and item icons, and enchant names.

Every item the page shows gets the game's own icon and a tooltip as close to
the game's as the data allows. Sources, all inside the egress the app already
has (wago.tools and wow.zamimg.com):

- Forever's DB2 tables on wago.tools (product `wowf`, the latest build):
  ItemSparse (name, quality, item level, required level, slot, binding,
  unique, set, flavour text, weapon speed), Item (class,
  subclass, IconFileDataID), ItemModifiedAppearance and ItemAppearance (the
  appearance's DefaultIconFileDataID: the icon the game shows when Item's
  own is 0, as it is for 1,617 of Forever's items, its new ones among them),
  ManifestInterfaceData (FileDataID -> the icon's
  file name under Interface/ICONS), ItemSet (the set's name), ItemSubClass
  (Cloth, Sword...) and SpellItemEnchantment (an enchant id's text,
  "Stamina +3", its $k1/$k2 filled from EffectPointsMin).
- The stats (armor, block, primary stats, the green "Equip:" lines, weapon
  dps): Polestar's gear data (data/gear-reference.json, its item_stats, the
  Classic Era client's values as Polestar_ItemDB holds them). Forever's own
  ItemSparse keeps stat allocations, not values. Items Polestar doesn't know
  show no stat lines.
- Names Forever's own tables don't have: the Classic Era client's
  ItemSparse (product `wow_classic_era` on wago.tools, its latest build),
  the name and nothing else. Forever's ItemSparse holds rows only for the
  items Forever changed (19k of its 32k), forever-db names the ones with a
  quest or a source; the rest (12k in 1.60.1, Season of Discovery's 2xxxxx
  among them: 211500 is "Resilient Cloth Headband") are base items the
  client ships unchanged, so Era's name is the game's own. Fetched with the
  Forever tables; Era unreachable: those items stay unnamed and the page
  calls one by its slot, with its Wowhead link.
- Icons: wow.zamimg.com/images/wow/icons/{large,medium}/<name>.jpg, Wowhead's
  renders of the game's BLP icons (56 and 36 px), cached on disk by name.
  An item with no icon in either table gets the game's own stand-in,
  INV_Misc_QuestionMark, as the client draws it.

Kept on disk (MODEL3D_CACHE_DIR/item-cards.json, icons under icons/) and
checked for a newer build once a day at most, like wowstats.items. Off with
WOW_ITEMS=off (tests): no card, no icon, the page draws its slot glyphs.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import re
import threading
import time
from pathlib import Path

from wowstats import upstream

DAY = 86400
ICON_URL = "https://wow.zamimg.com/images/wow/icons/"
SIZES = ("large", "medium")
ICON_NAME = upstream.ICON_NAME
# The client's icon for an item without one (and an item it doesn't know).
QUESTION = "inv_misc_questionmark"
# The client whose names fill Forever's gaps (its base items, unchanged).
ERA_PRODUCT = "wow_classic_era"
TABLES = ("ItemSparse", "Item", "ItemModifiedAppearance", "ItemAppearance", "ManifestInterfaceData", "ItemSet",
          "ItemSubClass", "SpellItemEnchantment")

BONDING = {1: "Binds when picked up", 2: "Binds when equipped", 3: "Binds when used", 4: "Quest Item"}
# InventoryType -> the slot line's left text, as the classic tooltip writes it.
SLOT_TEXT = {1: "Head", 2: "Neck", 3: "Shoulder", 4: "Shirt", 5: "Chest", 6: "Waist", 7: "Legs", 8: "Feet",
             9: "Wrist", 10: "Hands", 11: "Finger", 12: "Trinket", 13: "One-Hand", 14: "Off Hand", 15: "Ranged",
             16: "Back", 17: "Two-Hand", 18: "Bag", 19: "Tabard", 20: "Chest", 21: "Main Hand", 22: "Off Hand",
             23: "Held In Off-hand", 24: "Projectile", 25: "Thrown", 26: "Ranged", 28: "Relic"}
# Polestar's stat keys -> the tooltip's lines. White lines first (base stats),
# then the green "Equip:" lines, in the game's order.
WHITE = (("str", "+{} Strength"), ("agi", "+{} Agility"), ("sta", "+{} Stamina"), ("int", "+{} Intellect"),
         ("spi", "+{} Spirit"))
EQUIP = (("defense", "Increased Defense +{}."),
         ("dodge", "Increases your chance to dodge an attack by {}%."),
         ("parry", "Increases your chance to parry an attack by {}%."),
         ("block", "Increases your chance to block attacks with a shield by {}%."),
         ("blockValue", "Increases the block value of your shield by {}."),
         ("hit", "Improves your chance to hit by {}%."),
         ("crit", "Improves your chance to get a critical strike by {}%."),
         ("spellHit", "Improves your chance to hit with spells by {}%."),
         ("spellCrit", "Improves your chance to get a critical strike with spells by {}%."),
         ("ap", "+{} Attack Power."),
         ("rap", "+{} ranged Attack Power."),
         ("sp", "Increases damage and healing done by magical spells and effects by up to {}."),
         ("heal", "Increases healing done by spells and effects by up to {}."),
         ("spArcane", "Increases damage done by Arcane spells and effects by up to {}."),
         ("spFire", "Increases damage done by Fire spells and effects by up to {}."),
         ("spFrost", "Increases damage done by Frost spells and effects by up to {}."),
         ("spHoly", "Increases damage done by Holy spells and effects by up to {}."),
         ("spNature", "Increases damage done by Nature spells and effects by up to {}."),
         ("spShadow", "Increases damage done by Shadow spells and effects by up to {}."),
         ("mp5", "Restores {} mana per 5 sec."),
         ("hp5", "Restores {} health per 5 sec."))


def _i(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _num(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def icon_name(file_name: str) -> str | None:
    """INV_Sword_39.blp -> inv_sword_39, as Wowhead names its renders."""
    n = file_name.rsplit(".", 1)[0].strip().lower()
    return n if ICON_NAME.fullmatch(n) else None


def enchant_text(row: dict) -> str | None:
    """SpellItemEnchantment's Name_lang, its $k1..$k3 filled in."""
    text = (row.get("Name_lang") or "").strip()
    if not text:
        return None
    for k in (1, 2, 3):
        text = text.replace(f"$k{k}", str(_i(row.get(f"EffectPointsMin_{k - 1}"))))
    text = re.sub(r"\$\w+", "", text).strip()  # a token we can't fill: dropped, the rest stays readable
    return text or None


def stat_lines(stats: dict) -> tuple[list[str], list[str]]:
    """(white lines, green Equip: lines) from Polestar's stats."""
    white = [fmt.format(_num(stats[k])) for k, fmt in WHITE if stats.get(k)]
    green = []
    sp, heal = stats.get("sp"), stats.get("heal")
    # A healing item that also carries some damage reads as one line in
    # Forever ("healing done by up to 20 and damage done by up to 7"), the
    # game's own wording; equal values are the "damage and healing" line.
    split = bool(sp and heal and heal != sp)
    for k, fmt in EQUIP:
        v = stats.get(k)
        if not v or (k == "heal" and sp and heal == sp):  # spell power already says "and healing"
            continue
        if split and k == "sp":
            green.append(f"Equip: Increases healing done by up to {_num(heal)} and damage done by up to {_num(sp)} "
                         "for all magical spells and effects.")
            continue
        if split and k == "heal":
            continue
        green.append("Equip: " + fmt.format(_num(v)))
    return white, green


def appearance_icons(modified: list[dict], appearances: list[dict]) -> dict[int, int]:
    """item id -> its appearance's DefaultIconFileDataID: the item's base
    appearance (modifier 0, lowest OrderIndex) first, as the client picks it."""
    default = {_i(r.get("ID")): _i(r.get("DefaultIconFileDataID")) for r in appearances}
    best: dict[int, tuple] = {}
    for r in modified:
        iid, fdid = _i(r.get("ItemID")), default.get(_i(r.get("ItemAppearanceID")))
        if not iid or not fdid:
            continue
        rank = (_i(r.get("ItemAppearanceModifierID")) != 0, _i(r.get("OrderIndex")), _i(r.get("ID")))
        if iid not in best or rank < best[iid][0]:
            best[iid] = (rank, fdid)
    return {iid: fdid for iid, (_, fdid) in best.items()}


def build_cards(sparse: list[dict], items: list[dict], icons: dict[int, str], sets: dict[int, str],
                subclasses: dict[tuple[int, int], str], polestar: dict[str, dict],
                app_icons: dict[int, int] | None = None, names: dict[str, str] | None = None) -> dict[int, dict]:
    """One card per item: only the fields the tooltip draws, short keys.

    Every item of the Item table (32k in Forever 1.60.1): Forever's
    ItemSparse holds rows only for the items it changed (19k), so the rest
    take their quality, levels, slot and speed from Polestar's reference
    (the client's own values) and their name from forever-db (`names`),
    else none: the page names an item from its link."""
    by_sparse = {_i(r.get("ID")): r for r in sparse}
    out: dict[int, dict] = {}
    for it in items or [{"ID": k} for k in by_sparse]:
        iid = _i(it.get("ID"))
        if not iid:
            continue
        r = by_sparse.get(iid) or {}
        ps = polestar.get(str(iid)) or {}
        if not r and not ps and not it.get("IconFileDataID"):
            continue
        cls, sub = _i(it.get("ClassID")), _i(it.get("SubclassID"))
        inv = _i(r.get("InventoryType")) or _i(it.get("InventoryType")) or _i(ps.get("inv"))
        stats = ps.get("stats") or {}
        q = _i(r.get("OverallQualityID")) if r else _i(ps.get("quality"))
        c: dict = {"n": r.get("Display_lang") or (names or {}).get(str(iid)) or None, "q": q}
        if r.get("ItemLevel") is None and ps.get("ilvl"):
            r = {**r, "ItemLevel": ps["ilvl"], "RequiredLevel": ps.get("req"), "ItemDelay": ps.get("speed")}
        if not c["n"]:
            del c["n"]
        if not r.get("OverallQualityID") and not ps.get("quality") and q == 0 and not by_sparse.get(iid):
            del c["q"]
        # Item's own icon, else its appearance's (Forever's new items have
        # only that), else the question mark the game draws.
        c["i"] = (icons.get(_i(it.get("IconFileDataID"))) or icons.get((app_icons or {}).get(iid, 0))
                  or QUESTION)
        bond = BONDING.get(_i(r.get("Bonding")))
        if bond:
            c["b"] = bond
        if _i(r.get("MaxCount")) == 1:
            c["u"] = 1
        if _i(r.get("SellPrice")):
            # What a vendor pays, copper (Forever's ItemSparse): the planner
            # values a dungeon's loot with it (wowstats.plan), not the page.
            c["sell"] = _i(r.get("SellPrice"))
        if inv in SLOT_TEXT:
            c["s"] = SLOT_TEXT[inv]
        if cls == 2 or (cls == 4 and sub in (1, 2, 3, 4, 6)):
            t = subclasses.get((cls, sub))
            if t and t != "Miscellaneous":
                c["t"] = t
        if stats.get("armor"):
            c["ar"] = _num(stats["armor"])
        if stats.get("dps") and _i(r.get("ItemDelay")):
            # Damage per second and speed only: the min-max line would have
            # to come from DmgVariance, which gives Thunderfury 55-104 where
            # the game shows 44-115. No made-up numbers in a tooltip.
            c["sp"] = round(_i(r.get("ItemDelay")) / 1000, 2)
            c["dps"] = round(float(stats["dps"]), 1)
        white, green = stat_lines(stats)
        if white:
            c["w"] = white
        if green:
            c["g"] = green
        if _i(r.get("RequiredLevel")) > 1:
            c["rl"] = _i(r.get("RequiredLevel"))
        if _i(r.get("ItemLevel")) and (cls in (2, 4) or inv):
            c["il"] = _i(r.get("ItemLevel"))
        st = sets.get(_i(r.get("ItemSet")))
        if st:
            c["set"] = st
        desc = (r.get("Description_lang") or "").strip()
        if desc:
            c["de"] = desc
        out[iid] = c
    return out


def _rows(body: bytes):
    return csv.DictReader(io.StringIO(body.decode("utf-8-sig")))


class Catalog:
    def __init__(self, wago_url: str, product: str, cache: Path | None, on: bool = True,
                 icon_url: str = ICON_URL):
        self.wago = wago_url.rstrip("/")
        self.product = product
        self.cache = cache
        self.file = cache / "item-cards.json" if cache else None
        self.icon_url = icon_url.rstrip("/") + "/"
        self.on = on
        self.cards: dict[int, dict] = {}
        self.enchants: dict[int, str] = {}
        # Kept for the cards of items only a player's own client knows
        # (wowstats.itemfacts): an icon's file id -> its name, and the
        # subclass names (Cloth, Sword...).
        self.icon_files: dict[int, str] = {}
        self.subclasses: dict[tuple[int, int], str] = {}
        self.build = ""
        self.checked = 0.0
        self.lock = threading.Lock()

    # ------------------------------------------------------------ cards

    def card(self, item_id: int) -> dict | None:
        self._load()
        return self.cards.get(item_id)

    def many(self, ids) -> dict[str, dict]:
        self._load()
        return {str(i): self.cards[i] for i in ids if i in self.cards}

    def enchant(self, enchant_id: int | None) -> str | None:
        if not enchant_id:
            return None
        self._load()
        return self.enchants.get(enchant_id)

    # The cache file's shape: a new one rebuilds the cards on the same build
    # (2: every Item row, not only ItemSparse's; 3: appearance icons, the
    # question mark; 4: the vendor's sell price, for the planner's loot value;
    # 5: Era's names for the items Forever's tables don't name; 6: the icon
    # file names and subclass names, for wowstats.itemfacts).
    FORMAT = 6

    def _load(self) -> None:
        if not self.on or (self.cards and time.time() - self.checked < DAY):
            return
        with self.lock:
            if self.cards and time.time() - self.checked < DAY:
                return
            if not self.cards and self.file and self.file.is_file():
                with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                    d = json.loads(self.file.read_text(encoding="utf-8"))
                    if d.get("format") != self.FORMAT:
                        raise ValueError("an older cache")
                    self.cards = {int(k): v for k, v in d["cards"].items()}
                    self.enchants = {int(k): v for k, v in d["enchants"].items()}
                    self.icon_files = {int(k): v for k, v in d["icon_files"].items()}
                    self.subclasses = {tuple(int(x) for x in k.split(":")): v for k, v in d["subclasses"].items()}
                    self.build, self.checked = d["build"], float(d["checked"])
                if self.cards and time.time() - self.checked < DAY:
                    return
            from wowstats.upstream import Upstream, build_of, builds_url, fetch
            from wowstats.upstream import write_file as _write

            try:
                build = build_of(fetch(builds_url(self.wago, self.product)))
                if build != self.build or not self.cards:
                    self._refresh(build, fetch, Upstream)
            except (Upstream, ValueError, KeyError, TypeError):
                pass  # keep what we have; try again tomorrow
            self.checked = time.time()
            if self.file and self.cards:
                with contextlib.suppress(OSError):
                    _write(self.file, json.dumps({"format": self.FORMAT, "build": self.build, "checked": self.checked,
                                                  "cards": {str(k): v for k, v in self.cards.items()},
                                                  "enchants": {str(k): v for k, v in self.enchants.items()},
                                                  "icon_files": {str(k): v for k, v in self.icon_files.items()},
                                                  "subclasses": {f"{c}:{sc}": v
                                                                 for (c, sc), v in self.subclasses.items()}},
                                                 separators=(",", ":"), ensure_ascii=False).encode())

    def _refresh(self, build: str, fetch, Upstream) -> None:  # noqa: N803 - the exception class passed in
        from wowstats import gear
        from wowstats.upstream import table_url

        def table(name: str) -> bytes:
            body = fetch(table_url(self.wago, name, build), timeout=180)
            if not body:
                raise Upstream(f"wago.tools has no {name} table for {build}")
            return body

        # One table at a time, each dropped once read: the full UI pod is small.
        icons = {}
        for r in _rows(table("ManifestInterfaceData")):
            if "icons" in (r.get("FilePath") or "").lower():
                n = icon_name(r.get("FileName") or "")
                if n:
                    icons[_i(r.get("ID"))] = n
        sets = {_i(r.get("ID")): r.get("Name_lang") for r in _rows(table("ItemSet")) if r.get("Name_lang")}
        subs = {(_i(r.get("ClassID")), _i(r.get("SubClassID"))): r.get("DisplayName_lang")
                for r in _rows(table("ItemSubClass"))}
        enchants = {}
        for r in _rows(table("SpellItemEnchantment")):
            t = enchant_text(r)
            if t:
                enchants[_i(r.get("ID"))] = t
        item_rows = [{k: r.get(k) for k in ("ID", "ClassID", "SubclassID", "InventoryType", "IconFileDataID")}
                     for r in _rows(table("Item"))]
        app_icons = appearance_icons(
            [{k: r.get(k) for k in ("ID", "ItemID", "ItemAppearanceModifierID", "ItemAppearanceID", "OrderIndex")}
             for r in _rows(table("ItemModifiedAppearance"))],
            [{k: r.get(k) for k in ("ID", "DefaultIconFileDataID")} for r in _rows(table("ItemAppearance"))])
        try:
            polestar = gear.reference().data.get("items") or {}
        except Exception:  # noqa: BLE001 - no stats rather than no cards
            polestar = {}
        from wowstats import forever

        # Names, best first: Forever's ItemSparse (in build_cards), forever-db,
        # then the Era client's for the base items neither names.
        names = {**self._era_names({str(r["ID"]) for r in item_rows}, fetch, Upstream),
                 **(forever.data().get("items") or {})}
        cards = build_cards(_rows(table("ItemSparse")), item_rows, icons, sets, subs, polestar, app_icons, names)
        self.cards, self.enchants, self.build = cards, enchants, build
        self.icon_files, self.subclasses = icons, {k: v for k, v in subs.items() if v}

    def _era_names(self, ids: set[str], fetch, Upstream) -> dict[str, str]:  # noqa: N803
        """The Classic Era client's item names for `ids` ({} when wago.tools
        has no Era build or table: the items stay unnamed, nothing fails)."""
        from wowstats.upstream import build_of, builds_url, table_url

        try:
            era = build_of(fetch(builds_url(self.wago, ERA_PRODUCT)))
            body = fetch(table_url(self.wago, "ItemSparse", era), timeout=180)
        except (Upstream, ValueError, KeyError, TypeError):
            return {}
        if not body:
            return {}
        return {str(_i(r.get("ID"))): r["Display_lang"] for r in _rows(body)
                if r.get("Display_lang") and str(_i(r.get("ID"))) in ids}

    # ------------------------------------------------------------ icons

    def icon(self, name: str, size: str) -> bytes | None:
        """The icon's JPEG, from the cache or fetched once; None when unknown."""
        from wowstats.upstream import icon_url

        if not self.on or size not in SIZES:
            return None
        try:  # the name comes from a request path: checked before it is a file name or a URL
            url = icon_url(self.icon_url, size, name)
        except ValueError:
            return None
        f = self.cache / "icons" / size / f"{name}.jpg" if self.cache else None
        if f is not None and f.is_file():
            with contextlib.suppress(OSError):
                return f.read_bytes()
        from wowstats.upstream import Upstream, fetch
        from wowstats.upstream import write_file as _write

        try:
            body = fetch(url)
        except Upstream:
            return None
        if not body or body[:3] != b"\xff\xd8\xff":  # a JPEG, nothing else
            return None
        if f is not None:
            with contextlib.suppress(OSError):
                _write(f, body)
        return body


_shared: Catalog | None = None
_guard = threading.Lock()


def shared() -> Catalog:
    global _shared
    with _guard:
        if _shared is None:
            import os
            import tempfile

            from wowstats.upstream import PRODUCT, WAGO_URL, allow

            env = os.environ
            cache: Path | None = Path(env.get("MODEL3D_CACHE_DIR") or Path(tempfile.gettempdir()) / "wow-stats-model3d")
            try:
                cache.mkdir(parents=True, exist_ok=True)
            except OSError:
                cache = None
            # The configured upstreams (tests point them at a local server).
            _shared = Catalog(allow(env.get("MODEL3D_WAGO_URL", WAGO_URL)), env.get("MODEL3D_PRODUCT", PRODUCT), cache,
                              on=env.get("WOW_ITEMS", "on").strip().lower() not in ("off", "0", "false", "no"),
                              icon_url=allow(env.get("ICON_URL", ICON_URL)))
        return _shared


def item_ids(obj) -> set[int]:
    """Every item id in an answer (any "item_id" key, at any depth)."""
    out: set[int] = set()

    def walk(v):
        if isinstance(v, dict):
            iid = v.get("item_id")
            if isinstance(iid, int) and iid > 0:
                out.add(iid)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(obj)
    return out
