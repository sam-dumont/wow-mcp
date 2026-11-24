"""Writes data/foreverdb-weights.json: the stat weights the gear advice
scores with (wowstats.gear), per class, spec and mode, Leveling, PvP and PvE,
and the set-bonus share of each set piece.

The weights are ForeverDB.net's (https://foreverdb.net, credited wherever a
score shows), read from its addon's data files (GPL-3.0 with an attribution
term, data/foreverdb/LICENSE.txt; owner, 2026-11-24), the same download as
data/foreverdb/item-sources.json (tools/foreverdb_sources/export.py):

- Data/GearFinder.lua: per class, its specs (name, role, weapon style) and
  each spec's weights in the three profiles: `raid` is PvE (the class-guide
  weights; a hunter's attack power already counts its ranged attack power),
  `lvl` Leveling, `pvp` PvP.
- Data/GearSources.lua `gfExtra`: a set piece's share of its set's stat
  bonuses (the bonus total over the set's size).

The addon stores weights to two decimals. Leveling stays computed here from
PvE by ForeverDB's published rule (leveling(), its numbers to four
decimals, as the site's files had them); the addon's own Leveling table must
equal it to the addon's rounding (`checks.lvl`). PvP is the addon's table as
it is (`pvp_from: guide`); a spec without one would get pvp_rule(). Against
the site's files (2026-11-23) one number moved, by that rounding: Marksmanship
PvP mana per 5 sec, 0.028 there, 0.03 in the addon.

    uv run python tools/foreverdb_weights/export.py              # fetch (the manifest) and write
    uv run python tools/foreverdb_weights/export.py --files DIR  # from a verified download (tools/kb/bump.py)
    uv run python tools/foreverdb_weights/export.py --check      # offline

`--check` reads nothing from the network: it recomputes Leveling from the
stored PvE tables and fails when the file isn't what this script writes.
The parity fixture (tests/data/foreverdb-ladders.json.gz) was recorded from
the site's best-in-slot ladders on 2026-11-23 and stays as recorded.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "foreverdb_sources"))

import export as fdb  # noqa: E402 (tools/foreverdb_sources/export.py)

OUT = ROOT / "data" / "foreverdb-weights.json"
SITE = "https://foreverdb.net"
ROUNDING = 0.0051  # the addon keeps two decimals
QUEST_AHEAD = 5  # GearFinder.lua: "a quest reward from 5 levels below its quest's level"

# ForeverDB's stat ids (the game's ItemModType) -> the names wowstats.gear
# scores with. Ratings stay ratings: an item's "1% hit" line is 10 hit
# rating there (wowstats.gear.PCT_RATING).
STATS = {3: "agi", 4: "str", 5: "int", 6: "spi", 7: "sta", 12: "defense", 13: "dodgeRating", 14: "parryRating",
         15: "blockRating", 31: "hitRating", 32: "critRating", 36: "haste", 37: "expertise", 38: "ap", 39: "rap",
         41: "heal", 42: "sp", 43: "mp5", 45: "dmgheal", 46: "hp5", 47: "spellPen", 48: "blockValue",
         84: "spHoly", 85: "spFire", 86: "spNature", 87: "spFrost", 88: "spShadow", 89: "spArcane"}
ORDER = ("WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID")
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


def same(a: dict, b: dict) -> bool:
    keys = set(a) | set(b)
    return all(abs((a.get(k) or 0) - (b.get(k) or 0)) < 1e-6 for k in keys)


def close(a: dict, b: dict, tol: float = ROUNDING) -> bool:
    """Equal to the addon's two decimals."""
    return all(abs((a.get(k) or 0) - (b.get(k) or 0)) <= tol for k in set(a) | set(b))


def gearfinder(text: str) -> dict:
    """{class: {spec slug: {"name", "role", "style", "raid", "lvl", "pvp"}}} from
    GearFinder.lua's per-class long strings (S and W lines, tab-separated)."""
    out: dict = {}
    for cls, body in sorted(text.items()):
        if cls not in CLASSES.values():
            raise SystemExit(f"GearFinder.lua: an unknown class {cls!r}")
        specs: dict = {}
        for ln in str(body).splitlines():
            f = ln.split("\t")
            if f[0] == "S" and len(f) >= 6:
                specs[f[1]] = {"name": f[2], "role": f[3], "style": f[4] or "any", "slug": f[5]}
            elif f[0] == "W" and len(f) >= 4 and f[1] in specs:
                specs[f[1]][f[2]] = r4({name(k): float(v) for k, v in (x.split("=", 1) for x in f[3].split(",") if x)})
        out[cls] = {s["slug"]: s for s in specs.values()}
    return out


def extra_sets(gf_extra: dict) -> dict:
    """{item: set share} from gfExtra's `s` tokens ("s38:2,5:2")."""
    out = {}
    for iid, raw in gf_extra.items():
        for tok in str(raw).split():
            if tok.startswith("s"):
                out[str(iid)] = r4({name(k): float(v) for k, v in (x.split(":", 1) for x in tok[1:].split(",") if x)})
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def build(src: Path) -> dict:
    """The weights file from a verified download (tools/foreverdb_sources)."""
    man, parsed, _ = fdb.read_files(src)
    _, build_id, _ = fdb.notice_and_contract(parsed)
    gf = gearfinder(parsed["Data/GearFinder.lua"][0]["gfClass"])
    sets = extra_sets(parsed["Data/GearSources.lua"][0].get("gfExtra") or {})
    specs: dict = {}
    checks = {"lvl": [0, 0], "pvp_rule": [0, 0]}  # pvp_rule: [specs given the rule, 0]
    for cls, ss in gf.items():
        for slug, s in ss.items():
            p = s["raid"]
            lv = leveling(p, cls, s["role"])
            checks["lvl"][0 if close(lv, s["lvl"]) else 1] += 1
            # A spec without a PvP table gets ForeverDB's PvP rule (none today).
            by_rule = not s.get("pvp")
            checks["pvp_rule"][0] += by_rule
            key = SPEC_KEYS.get(s["name"], slug.replace("-", ""))
            specs.setdefault(cls, {})[key] = {
                "foreverdb": f"{[c for c, t in CLASSES.items() if t == cls][0].lower()}/{slug}", "role": s["role"],
                "style": s["style"], "pvp_from": "rule" if by_rule else "guide", "pve": p, "lvl": lv,
                "pvp": pvp_rule(p, cls) if by_rule else s["pvp"]}
    entries = fdb.manifest_entries(man)
    version = (man.get("data") or {}).get("version") or ""
    return {
        "schema": 1,
        "source": {"name": "ForeverDB.net", "url": SITE, "credit": "Weights: ForeverDB.net", "build": build_id,
                   "fetched": version[:10], "data_version": version,
                   "urls": [f"{SITE}/{entries[p]['url']}" for p in ("Data/GearFinder.lua", "Data/GearSources.lua")],
                   "licence": "GPL-3.0 with an attribution term (data/foreverdb/LICENSE.txt)",
                   "note": "ForeverDB's addon weights (GearFinder.lua) and set shares (GearSources.lua gfExtra); "
                           "Leveling is computed by tools/foreverdb_weights/export.py from PvE by their rule."},
        "rules": {"quest_ahead": QUEST_AHEAD, "no_req_level": "item level - 5"},
        "checks": {k: {"same": v[0], "differ": v[1]} for k, v in checks.items()},
        "specs": {c: v for c, v in sorted(specs.items(), key=lambda kv: ORDER.index(kv[0]))},
        "sets": sets,
    }


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=Path, help="read a download made by tools/foreverdb_sources (bump.py) here")
    ap.add_argument("--check", action="store_true", help="offline: check the committed file")
    a = ap.parse_args(argv)
    if a.check:
        return check()
    src = a.files
    if src is None:
        src = Path(tempfile.mkdtemp(prefix="foreverdb-"))
        fdb.download(src)
    out = build(src)
    OUT.write_text(dump(out), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {sum(len(v) for v in out['specs'].values())} specs, "
          f"{len(out['sets'])} set pieces; checks {out['checks']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
