"""The class guide: how to level each class on Forever, for any character
of that class at its level.

Two inputs, both in data/:

- `kb/research/classes.json`, the knowledge base's `classes` lane: sourced
  statements per class and topic (spec, talents, stats, spells, solo,
  dungeon, quests, mistakes, respec, habit), each with its `levels`, its
  source, type, date, confidence and scope (wowstats.analysis.kbschema), the
  contradictions between sources, and one talent path per class (`paths`:
  a pick per level from 10 to 60, checked against Forever's current trees by
  tests/test_classguide.py);
- `class-reference.json` (tools/class_reference/export.py): the class
  trainers, what they teach at each level and Forever's talent names.

`guide()` is the MCP's answer (everything, with citations); `block()` is the
character page's short "your class at level N" (actions only: what to
train and where, the next talent points, a habit or two). Neither says
where the character is: trainers come race capital first, then the other
capitals of its side, in the data's order.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .links import wowhead_url

_HERE = Path(__file__).resolve().parent
DATA_DIRS = (_HERE / "data", _HERE.parent / "data")
CLASSES = ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID")
TOPICS = ("spec", "talents", "stats", "spells", "solo", "dungeon", "quests", "mistakes", "respec", "habit")
BRACKETS = ((1, 9), (10, 19), (20, 29), (30, 39), (40, 49), (50, 60))
MAX_LEVEL = 60
# The capitals (uiMap ids) and the side each belongs to; Zephras Isle is the
# Skyborne's home and trains both sides.
CAPITALS = {1453: "A", 1455: "A", 1457: "A", 1454: "H", 1456: "H", 1458: "H"}
SKYBORNE_HOME = 2521
RACE_HOME = {"human": 1453, "dwarf": 1455, "gnome": 1455, "nightelf": 1457, "orc": 1454, "troll": 1454,
             "tauren": 1456, "undead": 1458, "scourge": 1458, "forsaken": 1458, "skyborne": SKYBORNE_HOME}
# The race masks of the training lists: bit 2^(ChrRaces id - 1).
RACE_BIT = {"human": 1, "orc": 2, "dwarf": 4, "nightelf": 8, "undead": 16, "scourge": 16, "forsaken": 16,
            "tauren": 32, "gnome": 64, "troll": 128}
SKYBORNE_BITS = {"alliance": 2 ** 32, "horde": 2 ** 33}  # PlayableRaceBit 32 and 33 (ChrRaces 95, 96)
SIDES = {"alliance": "A", "horde": "H"}

_lock = threading.Lock()
_cache: dict = {}


def _data_file(name: str) -> Path | None:
    for d in DATA_DIRS:
        p = d / name
        if p.exists():
            return p
    return None


def _load(key: str, name: str) -> dict:
    with _lock:
        if key not in _cache:
            p = _data_file(name)
            try:
                _cache[key] = json.loads(p.read_text(encoding="utf-8")) if p else {}
            except (OSError, ValueError):
                _cache[key] = {}
        return _cache[key]


def reference() -> dict:
    return _load("ref", "class-reference.json")


def lane() -> dict:
    return _load("lane", "kb/research/classes.json")


def _letters(v) -> str:
    return "".join(c for c in str(v or "").lower() if c.isalpha())


def token(cls) -> str | None:
    t = _letters(cls).upper()
    return t if t in CLASSES else None


def bracket(level: int) -> tuple[int, int]:
    lv = max(1, min(MAX_LEVEL, int(level or 1)))
    return next(b for b in BRACKETS if b[0] <= lv <= b[1])


def _covers(rec: dict, lo: int, hi: int) -> bool:
    lv = rec.get("levels") or [1, MAX_LEVEL]
    return lv[0] <= hi and lv[1] >= lo


def statements(cls: str, topic: str | None = None, level: int | None = None) -> list[dict]:
    """The lane's statements of a class (and topic), those covering `level`
    when given; Forever's first, then by confidence."""
    order = {"high": 0, "medium": 1, "low": 2}
    out = [s for s in lane().get("statements") or [] if s.get("class") == cls
           and (topic is None or s.get("topic") == topic) and (level is None or _covers(s, level, level))]
    return sorted(out, key=lambda s: (s.get("scope") != "forever", order.get(s.get("confidence"), 3)))


def path(cls: str) -> dict:
    return (lane().get("paths") or {}).get(cls) or {}


# ------------------------------------------------------------------ trainers


def _side(faction) -> str | None:
    return SIDES.get(str(faction or "").lower())


def place(t: dict) -> dict:
    from . import forever

    z = t.get("zone")
    zid = forever.area_id(z) if z else None
    return {"npc": t["npc"], "name": t["name"], "url": wowhead_url("npc", t["npc"]), "x": t.get("x"),
            "y": t.get("y"), "zone": {"name": z, "id": zid, "url": wowhead_url("zone", zid) if zid else None}}


def trainers(cls: str, race=None, faction=None) -> list[dict]:
    """The class's trainers in the capitals of the character's side (and on
    Zephras Isle for a Skyborne), its race's capital first, then the data's
    order. Never by distance: the static pages must not say where the
    character is."""
    ts = ((reference().get("classes") or {}).get(cls) or {}).get("trainers") or []
    side = _side(faction)
    home = RACE_HOME.get(_letters(race))
    maps = dict(CAPITALS)
    if home == SKYBORNE_HOME:
        maps[SKYBORNE_HOME] = "AH"
    keep = [t for t in ts if t.get("map") in maps and (not side or side in t.get("side", "AH"))]
    keep.sort(key=lambda t: t.get("map") != home)
    return [place(t) for t in keep]


# ------------------------------------------------------------------ spells


def race_bit(race, faction) -> int:
    r = _letters(race)
    if r == "skyborne":
        return SKYBORNE_BITS.get(str(faction or "").lower(), 0)
    return RACE_BIT.get(r, 0)


def _race_ok(mask: int, race, faction) -> bool:
    bit = race_bit(race, faction)
    return not mask or not bit or bool(mask & bit)


def _talent_spells(cls: str) -> dict[int, str]:
    tal = ((reference().get("classes") or {}).get(cls) or {}).get("talents") or {}
    return {v["spell"]: k for k, v in tal.items()}


def _needs(cls: str) -> dict[int, str]:
    """Spell id -> the talent its first rank comes from (Mind Flay's higher
    ranks need the Mind Flay talent): a rank chain whose start is a talent."""
    spells = (reference().get("classes") or {}).get(cls, {}).get("spells") or {}
    prev = {r["id"]: r.get("prev") for rows in spells.values() for r in rows}
    tal = _talent_spells(cls)
    out = {}
    for sid in prev:
        cur, seen = sid, set()
        while prev.get(cur) and cur not in seen:
            seen.add(cur)
            cur = prev[cur]
        if cur in tal:
            out[sid] = tal[cur]
    return out


def path_talents(cls: str, level: int) -> set[str]:
    """The talents the class path has taken by `level`."""
    return {p["talent"] for p in path(cls).get("picks") or [] if p.get("level", 99) <= level}


def taught(cls: str, level: int, race=None, faction=None, have: set[str] | None = None) -> list[dict]:
    """What the class trainer teaches at exactly `level` for this race; a
    rank that needs a talent only when the character has it (`have`, else
    the class path's talents by then)."""
    spells = ((reference().get("classes") or {}).get(cls) or {}).get("spells") or {}
    needs = _needs(cls)
    got = have if have is not None else path_talents(cls, level)
    out = []
    for r in spells.get(str(level)) or []:
        if not _race_ok(r.get("races") or 0, race, faction):
            continue
        need = needs.get(r["id"])
        if need and need not in got:
            continue
        out.append({"id": r["id"], "name": r["name"], "rank": r.get("rank") or None, "level": level,
                    "cost_copper": r.get("cost_copper"), "url": wowhead_url("spell", r["id"]),
                    "needs_talent": need})
    return out


def train(cls: str, level: int, race=None, faction=None, have: set[str] | None = None) -> dict:
    """{now, next}: the newest training level at or below `level` and the
    next one above it, each {level, spells}; None where there is none."""
    spells = ((reference().get("classes") or {}).get(cls) or {}).get("spells") or {}
    levels = sorted(int(k) for k in spells)
    now = nxt = None
    for lv in reversed([x for x in levels if x <= level]):
        rows = taught(cls, lv, race, faction, have)
        if rows:
            now = {"level": lv, "spells": rows}
            break
    for lv in [x for x in levels if x > level]:
        rows = taught(cls, lv, race, faction, have)
        if rows:
            nxt = {"level": lv, "spells": rows}
            break
    return {"now": now, "next": nxt}


# ------------------------------------------------------------------ talents


def _talent(cls: str, name: str) -> dict:
    return ((reference().get("classes") or {}).get(cls) or {}).get("talents", {}).get(name) or {}


def picks(cls: str) -> list[dict]:
    """The class path's picks, each with the rank it reaches."""
    seen: dict[str, int] = {}
    out = []
    for p in sorted(path(cls).get("picks") or [], key=lambda p: p["level"]):
        seen[p["talent"]] = seen.get(p["talent"], 0) + 1
        t = _talent(cls, p["talent"])
        out.append({"level": p["level"], "talent": p["talent"], "tree": p.get("tree") or t.get("tree"),
                    "rank": seen[p["talent"]], "max": t.get("max"),
                    "url": wowhead_url("spell", t["spell"]) if t.get("spell") else None})
    return out


def next_talents(cls: str, level: int, ranks: dict[str, int] | None = None, n: int = 3) -> list[dict]:
    """The next points to spend: with the character's ranks known, the path's
    picks it hasn't taken yet that its level allows ("now") and then the
    coming ones; without them, the picks from this level on."""
    all_picks = picks(cls)
    if ranks is not None:
        todo = [p for p in all_picks if ranks.get(p["talent"], 0) < p["rank"]]
        spent = sum(ranks.values())
        free = max(0, (level - 9) - spent) if level >= 10 else 0
        out = [{**p, "now": i < free} for i, p in enumerate(todo)]
    else:
        out = [{**p, "now": p["level"] <= level} for p in all_picks if p["level"] >= level]
    return out[:n]


def ranks_of(tal: dict | None) -> dict[str, int] | None:
    """{talent name: rank} from the character page's talents answer, or None
    when Battle.net gave none."""
    if not tal or not tal.get("available"):
        return None
    out: dict[str, int] = {}
    for sec in tal.get("sections") or []:
        for nd in sec.get("nodes") or []:
            if nd.get("name") and nd.get("rank"):
                out[nd["name"]] = int(nd["rank"])
    return out


# ------------------------------------------------------------------ answers


def _action(s: dict) -> str:
    return s.get("action") or s.get("text") or ""


def habits(cls: str, level: int, n: int = 2) -> list[str]:
    out = [_action(s) for s in statements(cls, "habit", level) if s.get("scope") == "forever"]
    return [x for x in out if x][:n]


def respec(cls: str, level: int) -> dict | None:
    """The class's respec moment when it is within 5 levels: {level, action}."""
    for s in statements(cls, "respec"):
        at = (s.get("at_level") or (s.get("levels") or [None])[0])
        if at and at - 5 <= level <= at:
            return {"level": at, "action": _action(s)}
    return None


def block(header: dict, tal: dict | None = None) -> dict | None:
    """The character page's "your class at level N": what to train now and
    next, where, the next talent points, one or two habits. None for a class
    or level the guide doesn't know."""
    cls = token(header.get("class"))
    level = header.get("level")
    if not cls or not isinstance(level, int | float) or not path(cls):
        return None
    level = int(level)
    ranks = ranks_of(tal)
    have = set(ranks) if ranks is not None else None
    p = path(cls)
    return {
        "class": cls, "level": level, "spec": p.get("spec"),
        "train": train(cls, level, header.get("race"), header.get("faction"), have),
        "trainers": trainers(cls, header.get("race"), header.get("faction")),
        "talents": next_talents(cls, level, ranks) if level < MAX_LEVEL or ranks is not None else [],
        "habits": habits(cls, level),
        "respec": respec(cls, level),
    }


CITE = ("id", "title", "text", "action", "levels", "source_url", "source_type", "date", "confidence", "scope",
        "last_verified")


def _cite(s: dict) -> dict:
    return {k: s[k] for k in CITE if s.get(k) not in (None, "")}


def guide(cls, level: int, race=None, faction=None, tal: dict | None = None) -> dict:
    """The MCP's class guide: the class's plan at this level (bracket), the
    next bracket, what to train and where, the next talent points, and every
    statement of the class with its source; contradictions stay explicit."""
    c = token(cls)
    if not c:
        raise ValueError(f"unknown class {cls!r}: one of {', '.join(x.title() for x in CLASSES)}")
    level = max(1, min(MAX_LEVEL, int(level or 1)))
    lo, hi = bracket(level)
    nxt = bracket(hi + 1) if hi < MAX_LEVEL else None
    ranks = ranks_of(tal)
    have = set(ranks) if ranks is not None else None
    p = path(c)

    def topic(t: str, a: int, b: int) -> list[dict]:
        return [_cite(s) for s in statements(c, t) if _covers(s, a, b)]

    def bracket_of(a: int, b: int) -> dict:
        return {"levels": [a, b], "talent_picks": [x for x in picks(c) if a <= x["level"] <= b],
                **{t: topic(t, a, b) for t in TOPICS if t not in ("talents", "spells")}}

    return {
        "class": c.title(), "level": level, "race": race, "faction": faction,
        "spec": p.get("spec"), "talent_path_source": {k: p.get(k) for k in (
            "source_url", "source_type", "date", "confidence", "scope", "last_verified", "based_on")},
        "now": bracket_of(lo, hi),
        "next": bracket_of(*nxt) if nxt else None,
        "train": train(c, level, race, faction, have),
        "trainers": trainers(c, race, faction),
        "next_talents": next_talents(c, level, ranks, 6),
        "talents_from": ("Battle.net's talents now" if ranks is not None
                         else "the class path (no talents from Battle.net)"),
        "respec": respec(c, level),
        "talents": topic("talents", 1, MAX_LEVEL),
        "spells": topic("spells", 1, MAX_LEVEL),
        "stats": topic("stats", 1, MAX_LEVEL),
        "contradictions": [_cite(x) for x in lane().get("contradictions") or [] if x.get("class") == c],
        "open_questions": [q.get("text") for q in lane().get("open_questions") or [] if q.get("class") == c],
        "kb": {"lane": "classes", "search": f"kb_search with '{c.lower()}' and a topic; kb_get the ids above for "
                                            f"the whole statement",
               "levelling_ids": [s["id"] for s in _levelling(c)]},
        "note": "Statements carry their source, date, confidence and scope; a classic_fallback is Classic Era "
                "advice used where Forever has no source. Trainer lists and talents are Forever's own data.",
    }


def _levelling(c: str) -> list[dict]:
    """The levelling lane's per-class documents (KB-C-<CLS>), for kb_get."""
    p = _data_file("kb/research/levelling.json")
    if not p:
        return []
    try:
        st = json.loads(p.read_text(encoding="utf-8")).get("statements") or []
    except (OSError, ValueError):
        return []
    code = {"WARRIOR": "WAR", "PALADIN": "PAL", "HUNTER": "HUN", "ROGUE": "ROG", "PRIEST": "PRI", "SHAMAN": "SHA",
            "MAGE": "MAG", "WARLOCK": "WLK", "DRUID": "DRU"}[c]
    return [s for s in st if re.match(rf"KB-C-{code}(-CF)?$", s.get("doc") or "")]


def check_path(cls: str, p: dict | None = None) -> list[str]:
    """Why a class's talent path can't be played on Forever's current trees
    ([] when it can): one pick per level from 10 to 60, each talent in that
    class's tree, no rank past its max, a row only once 5 points per row are
    in its tree, an arrow's target only after its source is maxed."""
    p = path(cls) if p is None else p
    tal = ((reference().get("classes") or {}).get(cls) or {}).get("talents") or {}
    errs = []
    got = sorted(p.get("picks") or [], key=lambda x: x.get("level", 0))
    if [x.get("level") for x in got] != list(range(10, MAX_LEVEL + 1)):
        errs.append("not one pick per level from 10 to 60")
    ranks: dict[str, int] = {}
    spent: dict[str, int] = {}
    for x in got:
        name, t = x.get("talent"), tal.get(x.get("talent") or "")
        if not t:
            errs.append(f"level {x.get('level')}: {name} is not a {cls.title()} talent on Forever's trees")
            continue
        if x.get("tree") and x["tree"] != t["tree"]:
            errs.append(f"level {x['level']}: {name} is in {t['tree']}, not {x['tree']}")
        if spent.get(t["tree"], 0) < 5 * t["row"]:
            errs.append(f"level {x['level']}: {name} (row {t['row'] + 1}) needs {5 * t['row']} points in "
                        f"{t['tree']}, has {spent.get(t['tree'], 0)}")
        need = t.get("needs")
        if need and ranks.get(need, 0) < (tal.get(need) or {}).get("max", 1):
            errs.append(f"level {x['level']}: {name} needs {need} maxed first")
        ranks[name] = ranks.get(name, 0) + 1
        spent[t["tree"]] = spent.get(t["tree"], 0) + 1
        if ranks[name] > t["max"]:
            errs.append(f"level {x['level']}: {name} past its {t['max']} ranks")
    return errs
