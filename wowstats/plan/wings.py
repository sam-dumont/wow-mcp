"""Dungeons run in wings: one line per wing (owner, 2026-11-23: "it's well
known that people do different parts").

Scarlet Monastery (Graveyard, Library, Armory, Cathedral), Dire Maul (East,
West, North), Blackrock Spire (Lower, Upper), Stratholme (Live, Undead) and
Maraudon (Orange, Purple, Inner) each become one dungeon per wing, named
"Scarlet Monastery: Library", in the Dungeons panel, the Overview's move and
the MCP alike. data/dungeons/wings.json (tools/dungeon_wings/export.py) says
which wing each creature, object and quest belongs to; the kill model
(data/dungeons/kill-xp.json) gives each wing its kills.

- Level range: the knowledge base's rule for the wing (data/kb/research,
  dungeon.sm_library...), else the dungeon's (Maraudon: no source splits it).
  Polestar's floor is the dungeon's, held inside the wing's range.
- Quests: those whose objectives are in the wing; one that needs several
  wings counts in each (`how`: "all"), one that can be done in any of them
  too (`how`: "any"); one done outside every wing ("outside") is on no line.
- Drops: from the wing's creatures. Quest rewards follow their quests.
- His runs: the wing whose creatures he killed (three kills in four or
  more there); a run across wings is no wing's measure.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]
FILES = (_HERE / "data" / "dungeons" / "wings.json", _HERE.parent / "data" / "dungeons" / "wings.json")
ONE_WING = 0.75  # share of a run's kills in one wing for the run to be that wing's


@functools.cache
def _data() -> dict:
    for p in FILES:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get("dungeons") or {}
    return {}


def _k(s: str) -> str:
    n = re.sub(r"^(the |stormwind )", "", (s or "").lower())
    return re.sub(r"[^a-z]", "", n).rstrip("s")


def dungeon(name: str) -> dict | None:
    """The split dungeon a name belongs to (the dungeon's or a wing's name),
    with its instance map as `map`; None for a dungeon run whole."""
    base = (name or "").split(":", 1)[0]
    for mid, d in _data().items():
        if _k(d["name"]) == _k(base):
            return {**d, "map": int(mid)}
    return None


def wing_of(name: str) -> tuple[dict, str] | None:
    """(the split dungeon, the wing) for a wing's name, "Scarlet Monastery:
    Library"; None for any other name."""
    if ":" not in (name or ""):
        return None
    d = dungeon(name)
    w = name.split(":", 1)[1].strip()
    hit = next((x for x in (d or {}).get("wings") or {} if _k(x) == _k(w)), None)
    return (d, hit) if d and hit else None


def key(name: str) -> str | None:
    """The wing part of a dungeon key ("library"), None when the name is no wing."""
    hit = wing_of(name)
    return _k(hit[1]) if hit else None


def split(aid: str, area: dict) -> list[tuple[str, dict]]:
    """The panel's areas for one dungeon: (id, area) per wing for a split
    dungeon ("796:Library"), else the dungeon itself."""
    from wowstats.analysis.kb import static_rule

    d = dungeon(area.get("name") or "")
    if not d:
        return [(aid, area)]
    lo, hi = area["levels"]
    out = []
    for w, v in d["wings"].items():
        rng = ((static_rule(v["rule"]) or {}).get("range") if v.get("rule") else None) or [lo, hi]
        wlo, whi = int(rng[0]), int(rng[1])
        floor = area.get("floor")
        wing = {**area, "name": v["name"], "levels": [wlo, whi], "wing": w, "dungeon": area["name"],
                "dungeon_area": aid, **({"party": v["party"]} if v.get("party") else {})}
        if isinstance(floor, int | float):
            wing["floor"] = max(float(wlo), min(float(floor), float(whi)))
        out.append((f"{aid}:{w}", wing))
    return out


def key_rule(area: dict) -> str | None:
    """What a wing's way in needs, from its knowledge base rule: the key's
    words ("The Scarlet Key"), "" for none, None when unknown (a dungeon run
    whole, a wing with no rule: every key of the dungeon stays)."""
    from wowstats.analysis.kb import static_rule

    hit = wing_of(area.get("name") or "") if area.get("wing") else None
    rule = hit[0]["wings"][hit[1]].get("rule") if hit else None
    r = static_rule(rule) if rule else None
    if r is None:
        return None
    return r.get("key") or ""


def quest(name: str, qid: int) -> dict | None:
    """How a quest of a split dungeon sits in its wings: {wings, how, by};
    None for a quest the file doesn't list."""
    d = dungeon(name)
    return ((d or {}).get("quests") or {}).get(str(qid)) if d else None


def in_wing(name: str, qid: int, variants=()) -> bool:
    """A quest of the wing `name` (any of its variants listed there)."""
    hit = wing_of(name)
    if not hit:
        return True
    d, w = hit
    for i in (qid, *variants):
        q = (d.get("quests") or {}).get(str(i))
        if q is not None:
            return w in q["wings"]
    # Not in the file: a quest the forever-db export didn't zone in the
    # dungeon nor Polestar's page lists; it stays with the whole dungeon.
    return False


def npc(name: str, npc_id) -> list[str]:
    d = dungeon(name)
    return ((d or {}).get("npcs") or {}).get(str(npc_id)) or [] if d else []


def drop_in(name: str, drops: list[dict]) -> bool:
    """An item that drops in the wing `name`: one of its sources is placed
    there (a creature with no npc id, the dungeon's trash, drops anywhere)."""
    hit = wing_of(name)
    if not hit:
        return True
    w = hit[1]
    return any(w in npc(name, s["npc_id"]) if s.get("npc_id") else True for s in drops)


def run_wing(name: str, kill_npcs: dict | None) -> str | None:
    """The wing a run of the dungeon `name` stayed in: ONE_WING of its kills
    or more on creatures placed only there. None: unknown, or across wings."""
    d = dungeon(name)
    if not d or not kill_npcs:
        return None
    by: dict[str, float] = {}
    total = 0.0
    for i, n in kill_npcs.items():
        ws = (d.get("npcs") or {}).get(str(i)) or []
        total += n
        for w in ws:
            by[w] = by.get(w, 0.0) + n / len(ws)
    if not by or not total:
        return None
    w, n = max(by.items(), key=lambda kv: kv[1])
    return w if n / total >= ONE_WING else None


def trip_name(t: dict) -> str:
    """The dungeon a trip counts for: its wing ("Scarlet Monastery: Library")
    when the run stayed in one, else the name it was recorded under."""
    name = t.get("dungeon") or ""
    d = dungeon(name)
    if not d or ":" in name:
        return name
    w = run_wing(name, t.get("kill_npcs"))
    return d["wings"][w]["name"] if w else name
