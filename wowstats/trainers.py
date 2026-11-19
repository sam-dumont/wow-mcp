"""Profession and riding trainers (data/trainers-reference.json, built by
tools/trainers/export.py from Polestar's shipped guides): who teaches a rank
on Forever, where they stand, the level it needs and the price the guide
gives, with whose price it is. A rank the data has no trainer for gives
None: the page says so and links Wowhead's search, it never guesses."""

from __future__ import annotations

import json
import threading
import urllib.parse
from pathlib import Path

from . import forever
from .links import WOWHEAD_BASE, wowhead_url

_HERE = Path(__file__).resolve().parent
BUNDLED = (_HERE / "data" / "trainers-reference.json", _HERE.parent / "data" / "trainers-reference.json")
SIDES = {"alliance": "A", "horde": "H"}
# The riding reminder's keys: the race's token without spaces.
RACE_KEYS = {"Night Elf": "NightElf", "Undead": "Scourge", "Forsaken": "Scourge"}

_lock = threading.Lock()
_data: dict | None = None


def data() -> dict:
    global _data
    with _lock:
        if _data is None:
            _data = {"professions": {}, "riding": {}}
            for p in BUNDLED:
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("schema") == 1:
                    _data = d
                    break
        return _data


def side(faction: str | None) -> str | None:
    return SIDES.get(str(faction or "").lower())


def place(t: dict) -> dict:
    """A trainer as the page shows it: name, Wowhead link, zone with its
    own link, map coordinates."""
    z = t.get("zone")
    zid = forever.area_id(z)
    return {"npc": t["npc"], "name": t["name"], "url": wowhead_url("npc", t["npc"]), "x": t.get("x"),
            "y": t.get("y"), "zone": {"name": z, "id": zid, "url": wowhead_url("zone", zid) if zid else None}}


def search_url(text: str) -> str:
    return f"{WOWHEAD_BASE}/search?q={urllib.parse.quote(text)}"


def rank(skill_id: int, rank_no: int) -> dict | None:
    """{name, max, level, price_copper, price_scope, trainers} of a rank, or
    None when the guides have no trainer for it."""
    return ((data().get("professions") or {}).get(str(skill_id)) or {}).get("ranks", {}).get(str(rank_no))


def for_side(trainers: list[dict], faction: str | None) -> list[dict]:
    """The trainers the character can use: its side's and the neutral ones
    (all of them when the side isn't known). In the data's order, by zone:
    never by distance from the character (the /zogzog/ page must not say
    where it is)."""
    s = side(faction)
    return [place(t) for t in trainers if not s or s in t.get("side", "AH")]


def riding(race: str | None, faction: str | None) -> dict | None:
    """{trainer, tiers, price_scope} for the race's riding trainer, or None."""
    r = data().get("riding") or {}
    if not race:
        return None
    key = f"Skyborne:{faction}" if race == "Skyborne" else RACE_KEYS.get(race, race.replace(" ", ""))
    t = (r.get("trainers") or {}).get(key)
    if not t:
        return None
    return {"trainer": place(t), "tiers": r.get("tiers") or {}, "price_scope": r.get("price_scope")}


def not_class_trainers() -> frozenset[int]:
    """The NPC IDs the data knows as profession or riding trainers: a
    trainer window from one of them is no class training."""
    d = data()
    ids = {t["npc"] for p in (d.get("professions") or {}).values() for r in (p.get("ranks") or {}).values()
           for t in r.get("trainers") or [] if isinstance(t.get("npc"), int)}
    ids |= {t["npc"] for t in ((d.get("riding") or {}).get("trainers") or {}).values() if isinstance(t.get("npc"), int)}
    return frozenset(ids)
