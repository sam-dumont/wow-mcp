"""Talents from WoWSims Exporter (wowsims/exporter, MIT; in game "WowSims").

The addon saves each character's build whenever a talent changes, at every
level on Forever (Flavors/Forever/Flavor.lua: saveBelowMaxLevel, events
TRAIT_CONFIG_UPDATED and ACTIVE_COMBAT_CONFIG_CHANGED), as the wowsims sim's
talent string: one digit per talent, "-" between the three trees, trailing
zeros dropped ("02500420103--00502"). The file keeps only the latest save
per character; the shipper sends a `wow-talents` event each time it
changes, so the history is the player's own events in VictoriaLogs, kept
like every other one (the player's client data, not Battle.net's: no
30-day limit, never in vm-wow).

Digit i of tree t is the i-th TraitDefinition ID of that tree in the
addon's table (data/wowsims-talents.json, tools/wowsims_talents.py). A
talent missing from the sim's trees is never in the string: unrecorded()
names them for a class, so the page can say which it can't show instead
of dropping them silently.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

from wowstats import talents

_HERE = Path(__file__).resolve().parent
# The wheel's copy (pyproject force-include), else the checkout's.
TABLE_FILES = (_HERE / "data" / "wowsims-talents.json", _HERE.parent / "data" / "wowsims-talents.json")
STRING = re.compile(r"^[0-9]{0,40}(-[0-9]{0,40}){0,2}$")


@functools.cache
def table() -> dict[str, list[list[int]]]:
    for p in TABLE_FILES:
        try:
            return json.loads(p.read_text(encoding="utf-8"))["classes"]
        except (OSError, ValueError, KeyError):
            continue
    return {}


def _nodes(cat: dict | None, cls) -> dict[int, tuple[str, dict]]:
    """TraitDefinition ID -> (frame section name, node) for the class's frame."""
    out: dict[int, tuple[str, dict]] = {}
    token = talents.class_token(cls)
    for tr in (cat or {}).get("trees") or []:
        if tr.get("kind") != "class" or tr.get("class") != token:
            continue
        for sec in tr["sections"]:
            for nd in sec["nodes"]:
                for d in nd.get("defs") or []:
                    out.setdefault(int(d), (sec["name"], nd))
    return out


def decode(cat: dict | None, cls, text) -> dict | None:
    """One saved build -> a reading as talents.history() takes it ({trees:
    [{tree, points}], ranks: {key: {spell, name, rank}}}), trees in the
    frame's tab order. None when the string can't be read with this table
    (another class's, a digit past a tree's end, a rank above the talent's
    maximum): unreadable, never guessed."""
    trees = table().get(talents.class_token(cls) or "")
    if not isinstance(text, str) or not STRING.match(text) or not trees:
        return None
    nodes = _nodes(cat, cls)
    tabs = [s["name"] for s in (talents.frame(cat, cls, {}) or {}).get("sections") or []]
    points: dict[str, int] = {t: 0 for t in tabs}
    ranks: dict[str, dict] = {}
    spells = (cat or {}).get("spells") or {}
    for t, digits in enumerate(text.split("-")):
        if t >= len(trees) or len(digits) > len(trees[t]):
            return None
        for i, ch in enumerate(digits):
            rank = int(ch)
            if not rank:
                continue
            did = trees[t][i]
            sec, nd = nodes.get(did, (tabs[t] if t < len(tabs) else f"Tree {t + 1}", None))
            if nd is not None and rank > nd["max"]:
                return None
            first = nd["spells"][0] if nd and nd["spells"] else None
            name = (spells.get(first) or {}).get("name") if first else None
            key = (name or "").lower() or f"def:{did}"
            ranks[key] = {"spell": int(first) if first else None, "name": name, "rank": rank}
            points[sec] = points.get(sec, 0) + rank
    return {"trees": [{"tree": k, "points": v} for k, v in points.items()], "ranks": ranks}


def unrecorded(cat: dict | None, cls) -> list[dict]:
    """The class's talents the addon's string has no digit for (Forever
    talents the sim doesn't model): it can't record them. [{name, spells}]
    (every rank's spell id), by name."""
    known = {d for tree in table().get(talents.class_token(cls) or "", []) for d in tree}
    if not known:
        return []
    spells = (cat or {}).get("spells") or {}
    token = talents.class_token(cls)
    out: dict[str, set[int]] = {}
    for tr in (cat or {}).get("trees") or []:
        if tr.get("kind") != "class" or tr.get("class") != token:
            continue
        for sec in tr["sections"]:
            for nd in sec["nodes"]:
                if nd["spells"] and not {int(d) for d in nd.get("defs") or []} & known:
                    name = (spells.get(nd["spells"][0]) or {}).get("name")
                    if name:
                        out.setdefault(name, set()).update(int(x) for x in nd["spells"])
    return [{"name": n, "spells": sorted(v)} for n, v in sorted(out.items())]


def missing_from(cat: dict | None, cls, bnet: dict | None) -> list[str]:
    """The talents Battle.net shows learned that the addon can't save, by
    spell id or name: the page names them under the history."""
    ranks = [r for r in (bnet or {}).get("ranks") or [] if r.get("rank")]
    ids = {int(r["spell"]) for r in ranks if r.get("spell")}
    names = {str(r.get("name") or "").lower() for r in ranks}
    return [u["name"] for u in unrecorded(cat, cls) if ids & set(u["spells"]) or u["name"].lower() in names]
