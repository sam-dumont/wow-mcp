"""Writes data/foreverdb-weights.json: the stat weights the gear advice
scores with (wowstats.gear), per class, spec and mode, Leveling, PvP and PvE,
and the set-bonus share of each set piece.

The weights are ForeverDB.net's (https://foreverdb.net, credited wherever a
score shows): the hand-set class-guide weights every calculator for the game
uses some version of, plus two rules of theirs that make the Leveling and
PvP tables. What is copied and what is computed:

- PvE: each spec's class-guide weights (`/data/guides.json`), as published.
  Hunters' attack power also counts their ranged attack power weight, as
  ForeverDB's best-in-slot pages do (Beast Mastery 0.1 + 0.45 = 0.55).
- Leveling: computed here from PvE by ForeverDB's published rule
  (`/data/bis/index.json` lvlMethod), so the rule is code: see leveling().
- PvP: each spec's PvP-guide weights (`/data/bis/<class>-<spec>.json`
  weights.pvp), whose gaps ForeverDB fills with its rule; a spec without a
  PvP guide gets pvp_rule() here.
- Sets: the share of a set's stat bonuses on each of its pieces (the bonus
  total over the number of pieces), from the same per-spec files.

Every computed table is checked against ForeverDB's own copy; the counts go
in the file's `checks`. Run it with the network:

    uv run python tools/foreverdb_weights/export.py [--cache DIR]

`--check` reads nothing from the network: it recomputes Leveling from the
stored PvE tables and fails when the file isn't what this script writes.
`--fixtures DIR` (with `--cache`) writes the parity test's fixture
(tests/data/foreverdb-ladders.json.gz) from the cached spec files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "foreverdb-weights.json"
FIXTURE = ROOT / "tests" / "data" / "foreverdb-ladders.json.gz"
SITE = "https://foreverdb.net"
UA = "wow-stats weights export (+https://github.com/sam-dumont/wow-mcp)"

# ForeverDB's stat ids (the game's ItemModType) -> the names wowstats.gear
# scores with. Ratings stay ratings: an item's "1% hit" line is 10 hit
# rating there (wowstats.gear.PCT_RATING).
STATS = {3: "agi", 4: "str", 5: "int", 6: "spi", 7: "sta", 12: "defense", 13: "dodgeRating", 14: "parryRating",
         15: "blockRating", 31: "hitRating", 32: "critRating", 36: "haste", 37: "expertise", 38: "ap", 39: "rap",
         41: "heal", 42: "sp", 43: "mp5", 45: "dmgheal", 46: "hp5", 47: "spellPen", 48: "blockValue",
         84: "spHoly", 85: "spFire", 86: "spNature", 87: "spFrost", 88: "spShadow", 89: "spArcane"}
CLASSES = {"Warrior": "WARRIOR", "Paladin": "PALADIN", "Hunter": "HUNTER", "Rogue": "ROGUE", "Priest": "PRIEST",
           "Shaman": "SHAMAN", "Mage": "MAGE", "Warlock": "WARLOCK", "Druid": "DRUID"}
# The class guide's tree -> data/gear-reference.json's spec key.
SPEC_KEYS = {"Beast Mastery": "beastmastery", "Feral Combat": "feral"}

# The Leveling rule (lvlMethod), word for word in numbers.
MAIN = ("str", "agi", "int", "dmgheal", "sp", "heal")  # what "the spec's main stat" can be
MAIL_PLATE = ("WARRIOR", "PALADIN", "HUNTER", "SHAMAN")
WAND = ("PRIEST", "MAGE", "WARLOCK")  # the casters who shoot a wand
LVL_STA, LVL_HIT, LVL_MANA, LVL_HEAL, LVL_WAND = 0.8, 0.7, 0.6, 0.5, 1.5
LVL_ARMOR = {True: 0.03, False: 0.02}
# The PvP rule (pvpMethod), for a spec without a PvP guide.
PVP_STA, PVP_HIT, PVP_MP5, PVP_ARMOR = 0.9, 0.8, 0.7, 0.02


def name(k) -> str:
    return k if k in ("dps", "wand", "armor") else STATS.get(int(k), f"stat{k}")


def named(w: dict) -> dict:
    return {name(k): float(v) for k, v in w.items()}


def r4(w: dict) -> dict:
    return {k: round(v, 4) for k, v in sorted(w.items()) if v}


def main_stat(w: dict) -> float:
    return max((w.get(k) or 0) for k in MAIN)


def pve(guide: dict, cls: str) -> dict:
    """The class guide's weights; a hunter's attack power counts its ranged
    attack power too (ForeverDB's best-in-slot tables do)."""
    w = named(guide)
    if cls == "HUNTER" and w.get("rap"):
        w["ap"] = (w.get("ap") or 0) + w["rap"]
    return r4(w)


def leveling(w: dict, cls: str, role: str) -> dict:
    """ForeverDB's Leveling weights from the PvE ones (lvlMethod): Stamina
    at least 80% of the main stat (a tank keeps its own when higher), armour
    0.03 a point for mail and plate wearers and 0.02 for the rest (a tank
    keeps a higher one), Hit and Expertise 70%, Spirit and mana per 5 sec
    60%, healing 50% for healers, a wand's DPS 1.5 for those who shoot one."""
    out = dict(w)
    out["sta"] = max(w.get("sta") or 0, LVL_STA * main_stat(w))
    out["armor"] = max(w.get("armor") or 0, LVL_ARMOR[cls in MAIL_PLATE])
    for k in ("hitRating", "expertise"):
        if k in out:
            out[k] = w[k] * LVL_HIT
    for k in ("spi", "mp5"):
        if k in out:
            out[k] = w[k] * LVL_MANA
    if role == "Healer" and "heal" in out:
        out["heal"] = w["heal"] * LVL_HEAL
    if cls in WAND:
        out["wand"] = LVL_WAND
    return r4(out)


def pvp_rule(w: dict, cls: str) -> dict:
    """ForeverDB's PvP rule for a spec without a PvP guide (pvpMethod):
    Stamina 90% of the main stat, Hit and Expertise 80%, mana per 5 sec 70%,
    armour at least 0.02 a point, everything else as in PvE."""
    out = dict(w)
    out["sta"] = PVP_STA * main_stat(w)
    for k in ("hitRating", "expertise"):
        if k in out:
            out[k] = w[k] * PVP_HIT
    if "mp5" in out:
        out["mp5"] = w["mp5"] * PVP_MP5
    out["armor"] = max(w.get("armor") or 0, PVP_ARMOR)
    return r4(out)


def fetch(path: str, cache: Path | None):
    if cache:
        f = cache / path.strip("/").replace("/", "_")
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    req = urllib.request.Request(SITE + path, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / path.strip("/").replace("/", "_")).write_bytes(raw)
    return json.loads(raw)


def same(a: dict, b: dict) -> bool:
    keys = set(a) | set(b)
    return all(abs((a.get(k) or 0) - (b.get(k) or 0)) < 1e-6 for k in keys)


def build(cache: Path | None, fetched: str) -> tuple[dict, dict]:
    index = fetch("/data/bis/index.json", cache)
    guides = fetch("/data/guides.json", cache)
    specs: dict = {}
    sets: dict = {}
    checks = {"pve": [0, 0], "lvl": [0, 0], "pvp_rule": [0, 0]}
    files = {}
    urls = [SITE + "/data/bis/index.json", SITE + "/data/guides.json"]
    for c in index["classes"]:
        cls = CLASSES[c["n"]]
        tree = {s["tree"]: s for s in guides["classes"][c["n"]]["specs"]}
        for s in c["specs"]:
            path = f"/data/bis/{c['slug']}-{s['slug']}.json"
            urls.append(SITE + path)
            d = fetch(path, cache)
            files[f"{c['slug']}-{s['slug']}"] = d
            g = tree[s["n"]]
            p = pve(g["weights"], cls)
            lv = leveling(p, cls, s["role"])
            theirs = {m: r4(named(d["weights"][m])) for m in ("pve", "lvl", "pvp")}
            checks["pve"][0 if same(p, theirs["pve"]) else 1] += 1
            checks["lvl"][0 if same(lv, theirs["lvl"]) else 1] += 1
            if d.get("pvpFrom") == "guide":
                pv = theirs["pvp"]
            else:
                pv = pvp_rule(p, cls)
                checks["pvp_rule"][0 if same(pv, theirs["pvp"]) else 1] += 1
            key = SPEC_KEYS.get(s["n"], s["slug"].replace("-", ""))
            specs.setdefault(cls, {})[key] = {
                "foreverdb": f"{c['slug']}/{s['slug']}", "role": s["role"], "style": d.get("style") or "any",
                "pvp_from": d.get("pvpFrom") or "rule", "pve": p, "lvl": lv, "pvp": pv}
            for iid, it in d["items"].items():
                if it.get("sb"):
                    sets[iid] = r4({name(k): float(v) for k, v in it["sb"]})
    out = {
        "schema": 1,
        "source": {"name": "ForeverDB.net", "url": SITE, "credit": "Weights: ForeverDB.net",
                   "build": index.get("build"), "fetched": fetched, "urls": urls,
                   "note": "The class-guide stat weights and ForeverDB's Leveling and PvP rules; Leveling is "
                           "computed by tools/foreverdb_weights/export.py from PvE."},
        "rules": {"quest_ahead": index.get("questAhead", 5), "no_req_level": "item level - 5"},
        "checks": {k: {"same": v[0], "differ": v[1]} for k, v in checks.items()},
        "specs": specs,
        "sets": dict(sorted(sets.items(), key=lambda kv: int(kv[0]))),
    }
    return out, files


# The parity fixture: five specs' ladders in all three modes, each item
# trimmed to what the score reads (tests/test_gear_foreverdb.py).
FIXTURE_SPECS = ("priest-shadow", "priest-holy", "warrior-fury", "warrior-protection", "hunter-beast-mastery")
ITEM_FIELDS = ("n", "c", "sc", "inv", "il", "rl", "st", "pct", "sb", "dmg", "spd", "ar", "u", "lc", "g")


def fixtures(files: dict) -> dict:
    out = {}
    for slug in FIXTURE_SPECS:
        d = files[slug]
        ids = {str(e[0]) for lad in d["ladder"].values() for es in lad.values() for e in es}
        items = {i: {k: d["items"][i][k] for k in ITEM_FIELDS if k in d["items"][i]} for i in sorted(ids)}
        out[slug] = {"cls": d["cls"], "class": d["class"], "spec": d["spec"], "style": d.get("style"),
                     "weights": d["weights"], "ladder": d["ladder"], "items": items}
    return out


def dump(out: dict) -> str:
    return json.dumps(out, indent=1, ensure_ascii=False) + "\n"


def check() -> int:
    """Offline: Leveling recomputed from each stored PvE table, the schema,
    and the file byte for byte as dump() writes it."""
    d = json.loads(OUT.read_text(encoding="utf-8"))
    bad = []
    for cls, ss in d["specs"].items():
        for key, s in ss.items():
            if not same(leveling(s["pve"], cls, s["role"]), s["lvl"]):
                bad.append(f"{cls} {key}: lvl isn't the rule applied to pve")
            for m in ("pve", "lvl", "pvp"):
                if not s.get(m):
                    bad.append(f"{cls} {key}: no {m} weights")
    if d.get("schema") != 1 or not d.get("source", {}).get("url"):
        bad.append("schema or source missing")
    if OUT.read_text(encoding="utf-8") != dump(d):
        bad.append("not in the export's format: rerun the export")
    for b in bad:
        print(b, file=sys.stderr)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, help="keep (or reuse) the fetched files here")
    ap.add_argument("--check", action="store_true", help="offline: check the committed file")
    ap.add_argument("--fixtures", action="store_true", help="also write the parity test's fixture")
    a = ap.parse_args()
    if a.check:
        return check()
    out, files = build(a.cache, dt.date.today().isoformat())
    OUT.write_text(dump(out), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {sum(len(v) for v in out['specs'].values())} specs, "
          f"{len(out['sets'])} set pieces; checks {out['checks']}")
    if a.fixtures:
        FIXTURE.write_bytes(gzip.compress(json.dumps(fixtures(files), separators=(",", ":")).encode(), mtime=0))
        print(f"wrote {FIXTURE.relative_to(ROOT)} ({FIXTURE.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
