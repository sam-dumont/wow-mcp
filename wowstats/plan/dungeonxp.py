"""Kill XP of a dungeon the player hasn't run: an estimate from who a run
kills (data/dungeons/kill-xp.json, tools/dungeon_xp/export.py), Classic's
kill formula and Forever's dungeon rules.

One kill pays, to each of five players of the same level:

    Classic's kill XP at that level (the KB's xp.kill_formula: base 5L+45,
    the zero-difference table below, nothing for a grey mob)
    x 2.5 for an elite (bosses included: no extra boss multiplier)
    x 1.4 / 5, the group's share (a raid: no group bonus, its own size)
    x 0.6, Forever's dungeon factor (60% of Classic since the 8-9 November
      2026 hotfix; 0.4 before)
    and nothing for a mob more than 12 levels under the party's highest.

Player buffs (sleeping bag, Well Fed, rested) are not in it. The player's
own runs of a dungeon replace all of this (wowstats.plan.dungeongain).

The zero difference stays Classic's (12 for levels 30-39). The calibration
on one character's runs (2026-11-23) leans to 11 at level 31: the
Stockade's slope over three mob levels fits 11, and its full clear comes
out 2% off with 11 against +8% with 12. One dungeon and three levels: a
lead, not a change.

A run kills, per kind of data:

- spawns (the original dungeons, cMaNGOS's spawn table): SHARE of the
  placed creatures, a usual run skipping some packs;
- logged_runs (Ruins of Lordaeron, The Drowned City): the kills per run
  that players' combat logs counted (EG Link), skips included;
- reference_runs (City of Dalaran): the reference runs' own kill XP,
  carried to the level by the formula over the placed creatures.

Time inside: kills x the seconds a kill took in the reference runs (an
original dungeon: the Stockade's; a new one: Dalaran's).

Labels: "measured" (his own runs, set by dungeongain), "estimate", or
"not_enough_data" (with what would unlock one).
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

from . import dungeongain as G

FACTOR = 0.6          # Forever's dungeon kill XP: 60% of Classic since 2026-11-08/09 (0.4 before)
ELITE = 2.5           # an elite in a 5-player dungeon (Classic's dungeon rule; bosses the same)
GROUP_RATE = {1: 1.0, 2: 1.0, 3: 1.166, 4: 1.3, 5: 1.4}
RAID_RATE = 1.0       # a raid group: no bonus, the XP split by its size
GAP = 12              # no XP for a mob more than this many levels under the party's highest
SHARE = 0.85          # of a spawn table's creatures, what a usual run kills
PARTY = 5

_HERE = Path(__file__).resolve().parents[1]
FILES = (_HERE / "data" / "dungeons", _HERE.parent / "data" / "dungeons")


@functools.cache
def _load() -> tuple[dict, dict]:
    for d in FILES:
        main, fs = d / "kill-xp.json", d / "forever-spawns" / "forever-spawns.json"
        if main.exists():
            return json.loads(main.read_text()), (json.loads(fs.read_text()) if fs.exists() else {})
    return {}, {}


def _key(name: str) -> str:
    """dungeonview.key's rule: one name whatever wrote it."""
    n = re.sub(r"^(the |stormwind )", "", (name or "").lower()).split(":")[0]
    return re.sub(r"[^a-z]", "", n).rstrip("s")


def entry(name: str) -> tuple[str, dict] | None:
    data = _load()[0].get("dungeons") or {}
    k = _key(name)
    return next(((n, e) for n, e in data.items() if _key(n) == k), None)


def _wing(name: str, e: dict) -> str | None:
    """The kill model's wing a wing's name means ("Scarlet Monastery: Library"
    -> "Library"), None for the dungeon's own name."""
    from . import wings

    hit = wings.wing_of(name)
    return hit[1] if hit and hit[1] in (e.get("wings") or {}) else None


def per_kill(level: int, lmin: int, lmax: int, elite: bool, rule: dict, party: int = PARTY,
             top: int | None = None) -> float:
    """One kill's XP to each party member of `level`, the creature's level
    taken evenly over its range; `top` the party's highest level."""
    top = level if top is None else top
    rate = GROUP_RATE.get(party, RAID_RATE) if party <= 5 else RAID_RATE
    lv = range(lmin, lmax + 1)
    r = {**rule, "elite_factor": ELITE}
    xs = [0.0 if top - m > GAP else G.kill_xp(level, m, elite, r) for m in lv]
    return sum(xs) / len(xs) * rate / party * FACTOR


def _ranks(name: str) -> dict[int, str]:
    d = ((_load()[1].get("dungeons") or {}).get(name) or {}).get("ranks") or []
    return {r[0]: r[1] for r in d}


def _placed(name: str) -> dict[int, float]:
    d = ((_load()[1].get("dungeons") or {}).get(name) or {}).get("ranks") or []
    return {r[0]: r[3] for r in d if r[3]}


def roster(name: str, e: dict, mobs: list, level: int, rule: dict, party: int = PARTY,
           counts: dict[int, float] | None = None) -> tuple[float, float]:
    """(XP, kills) of one run's roster at `level`. `counts` replaces each
    creature's own count (the placed census)."""
    xp = kills = 0.0
    if e.get("basis") == "spawns":
        for _id, _n, lo, hi, elite, _boss, n in mobs:
            xp += n * per_kill(level, lo, hi, bool(elite), rule, party)
            kills += n
        return xp, kills
    ranks = _ranks(name)
    for i, _n, lo, hi, boss, n in mobs:
        if counts is not None:
            n = counts.get(i) or 0
        # No rank in the data: a dungeon creature is taken as elite, as nearly all are.
        elite = bool(boss) or ranks.get(i, "elite") == "elite"
        xp += n * per_kill(level, lo, hi, elite, rule, party)
        kills += n
    return xp, kills


def seconds_per_kill(kind: str) -> float | None:
    """A kill's time in the reference runs: the Stockade's full clear for an
    original dungeon, Dalaran's two runs for a new one."""
    d = _load()[0].get("dungeons") or {}
    if kind == "original":
        r = ((d.get("The Stockade") or {}).get("reference") or {}).get("runs") or []
        return r[0]["inside_s"] / r[0]["kills"] if r else None
    e = d.get("City of Dalaran") or {}
    runs = (e.get("reference") or {}).get("runs") or []
    kills = dalaran_kills(e)
    return sum(r["inside_s"] for r in runs) / kills if runs and kills else None


def dalaran_kills(e: dict) -> float | None:
    """Every kill of the reference runs: the placed trash once, the bosses in
    each run."""
    placed = _placed("City of Dalaran")
    if not placed:
        return None
    mobs = e.get("mobs") or []
    runs = len((e.get("reference") or {}).get("runs") or [])
    trash = sum(placed.get(m[0], 0) for m in mobs if not m[4])
    bosses = sum(1 for m in mobs if m[4])
    return trash + bosses * runs


def _provenance(e: dict) -> dict:
    main = _load()[0]
    src = (main.get("sources") or {}).get(e.get("source") or "") or {}
    out = {k: src.get(k) for k in ("repo", "commit", "file", "licence", "credit") if src.get(k)}
    if e.get("source") == "eg_link":
        fs = (main.get("sources") or {}).get("forever_spawns") or {}
        out["ranks"] = {k: fs.get(k) for k in ("repo", "commit", "licence", "file")}
    if e.get("reference"):
        out["reference_runs"] = e["reference"]
    return out


def estimate(name: str, level: int, rule: dict | None = None) -> dict | None:
    """One run's kill XP and time inside at `level` for a party of five of
    that level; None for a name no dungeon data has."""
    rule = rule or G.kill_rule()
    hit = entry(name)
    if not hit or not rule:
        return None
    dname, e = hit
    main = _load()[0]
    out = {"label": e.get("label"), "basis": e.get("basis"), "party": PARTY, "factor": FACTOR,
           "as_of": main.get("as_of"), "xp": None, "kills": None, "inside_s": None, "xp_per_hour": None}
    if e.get("label") == "not_enough_data":
        return {**out, "unlock": e.get("unlock"), "provenance": _provenance(e)}
    spk = seconds_per_kill("original" if e.get("source") == "cmangos" else "new")
    if e.get("basis") == "reference_runs":
        runs = e["reference"]["runs"]
        at = runs[0]["level"]
        placed = _placed(dname)
        here, _ = roster(dname, e, e["mobs"], level, rule, counts=placed)
        there, _ = roster(dname, e, e["mobs"], at, rule, counts=placed)
        mean = sum(r["kill_xp"] for r in runs) / len(runs)
        xp = mean * here / there if there else None
        kills = (dalaran_kills(e) or 0) / len(runs)
        out.update(xp=round(xp) if xp is not None else None, kills=round(kills),
                   inside_s=round(sum(r["inside_s"] for r in runs) / len(runs)))
    elif e.get("wings") and _wing(name, e):
        # One wing's line (wowstats.plan.wings): that wing's creatures only,
        # at its own party size (Upper Blackrock Spire: ten).
        w = _wing(name, e)
        v = e["wings"][w]
        party = v.get("party") or PARTY
        xp, kills = roster(dname, e, v["mobs"], level, rule, party)
        out.update(xp=round(xp * SHARE), kills=round(kills * SHARE), party=party, wing=w, share=SHARE,
                   inside_s=round(kills * SHARE * spk) if spk else None)
    elif e.get("wings"):
        wings = []
        for w, v in e["wings"].items():
            party = v.get("party") or PARTY
            xp, kills = roster(dname, e, v["mobs"], level, rule, party)
            wings.append({"wing": w, "party": party, "xp": round(xp * SHARE), "kills": round(kills * SHARE),
                          "inside_s": round(kills * SHARE * spk) if spk else None})
        five = [x for x in wings if x["party"] == PARTY] or wings
        out.update(xp=round(sum(x["xp"] for x in five) / len(five)),
                   kills=round(sum(x["kills"] for x in five) / len(five)),
                   inside_s=round(sum(x["inside_s"] or 0 for x in five) / len(five)) if spk else None,
                   wings=wings, share=SHARE, per="wing")
    else:
        share = SHARE if e.get("basis") == "spawns" else 1.0
        xp, kills = roster(dname, e, e["mobs"], level, rule)
        out.update(xp=round(xp * share), kills=round(kills * share),
                   inside_s=round(kills * share * spk) if spk else None, **({"share": SHARE} if share < 1 else {}))
    if out["xp"] is not None and out["inside_s"]:
        out["xp_per_hour"] = round(out["xp"] / out["inside_s"] * 3600)
    out["provenance"] = _provenance(e)
    return out


def full_clear(name: str, level: int, party: int = PARTY, rule: dict | None = None) -> float | None:
    """Every placed creature once (no skipped pack), for the validation
    against a recorded full clear."""
    rule = rule or G.kill_rule()
    hit = entry(name)
    if not hit or not rule:
        return None
    dname, e = hit
    if e.get("basis") == "spawns":
        return roster(dname, e, e["mobs"], level, rule, party)[0]
    placed = _placed(dname)
    return roster(dname, e, e.get("mobs") or [], level, rule, party, counts=placed)[0] if placed else None


def bosses_xp(name: str, level: int, rule: dict | None = None) -> float:
    """The bosses of one run at `level`, by the formula."""
    rule = rule or G.kill_rule()
    dname, e = entry(name)
    return sum(per_kill(level, m[2], m[3], True, rule) for m in e.get("mobs") or [] if m[4])
