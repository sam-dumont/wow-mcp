"""Writes data/craft-reference.json: what a craft takes, so the server can
say what levelling a profession costs and what stands between a character
and a crafted item (wowstats.crafting).

Two sources, both read as they ship:

- Polestar's profession guides (a release checkout, GPL-3.0, the same tag as
  the other Polestar exports: tools/polestar-tag), the route of record
  (owner, 2026-11-25: Polestar is the source of truth). The compiled guide
  pack (addon/Polestar_Guides/Guides.lua) is read by
  tools/route_reference/dump.lua, which runs it in an empty Lua environment
  as data. Kept per profession: each step's title and note (the guide's own
  text, its {item:N}-style tokens written out with the guide's names table)
  and its goals: what to craft (spell, made item, count or `until` skill,
  yellow and grey, reagents), learn, train, buy, take from the auction house
  or collect. From the notes, the numbers the goals don't carry: the skill a
  recipe is orange from ("is orange at 104"), what a trainer recipe needs and
  costs ("{spell:3866} (from 85, 2s 50c)"), a pattern's vendor price
  ("teaches ... for 15s") and the vendor goods' prices ("44 {item:2321}
  (44s)": a unit price).
- Forever's client tables on wago.tools (`--build`, by default the build
  data/game-items.json was made from): every recipe of a crafting profession
  (SkillLineAbility: its skill line, yellow and grey; SpellReagents; the item
  it makes, SpellEffect 24), every recipe item and the recipe it teaches
  (ItemEffect trigger 6, through ItemXItemEffect) with the skill it asks
  (ItemSparse RequiredSkillRank), and which made items bind when picked up
  (ItemSparse Bonding 1: nobody can sell one on the auction house).

Stdlib only, the same file byte for byte on every run:

    git clone --depth 1 --branch "$(cat tools/polestar-tag)" https://github.com/fitzforge/polestar /tmp/polestar
    uv run python tools/craft_reference/export.py --polestar /tmp/polestar
    uv run python tools/craft_reference/export.py --check     # offline: the committed file
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "craft-reference.json"
DUMP = ROOT / "tools" / "route_reference" / "dump.lua"
GAME_ITEMS = ROOT / "data" / "game-items.json"
# The crafting skill lines (and the gathering ones whose guides craft:
# Mining smelts). Fishing, Herbalism and Skinning make nothing.
CRAFTING = {129: "First Aid", 164: "Blacksmithing", 165: "Leatherworking", 171: "Alchemy", 185: "Cooking",
            186: "Mining", 197: "Tailoring", 202: "Engineering", 333: "Enchanting"}
TABLES = ("SkillLineAbility", "SpellReagents", "SpellEffect", "ItemEffect", "ItemXItemEffect", "ItemSparse",
          "SpellName")
CREATE_ITEM = "24"  # SpellEffect.Effect: create an item
LEARN_TRIGGER = "6"  # ItemEffect.TriggerType: on learn (a recipe item)
BIND_ON_PICKUP = 1
COIN = {"g": 10000, "s": 100, "c": 1}
MONEY = r"\d+[gsc](?: \d+[gsc])*"
TOKEN = re.compile(r"\{(\w+):(\d+)\}")
# Numbers Polestar writes in its notes and not in its goals.
ORANGE = re.compile(r"\{(?:spell|item):(\d+)\} is orange at (\d+)")
LEARN_FROM = re.compile(r"\{spell:(\d+)\} \(from (\d+)(?:, (" + MONEY + r"))?\)")
TEACHES = re.compile(r"\{item:(\d+)\} teaches \{item:\d+\} \(\{skill:\d+\} (\d+)\) for (" + MONEY + ")")
VENDOR = re.compile(r"(\d+) \{item:(\d+)\} \((" + MONEY + r")\)")


def copper(text: str | None) -> int | None:
    if not text:
        return None
    return sum(int(n) * COIN[u] for n, u in re.findall(r"(\d+)([gsc])", text))


def i(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def wago(table: str, build: str) -> list[dict]:
    sys.path.insert(0, str(ROOT))
    from wowstats.upstream import WAGO_URL, fetch, table_url

    body = fetch(table_url(WAGO_URL, table, build), timeout=180)
    if not body:
        raise SystemExit(f"wago.tools has no {table} for {build}")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        raise SystemExit(f"wago.tools has no {table} rows for {build}")
    return rows


def guides(checkout: Path, lua: str) -> list[dict]:
    """The profession guides the release ships, as dump.lua reads them."""
    r = subprocess.run([lua, str(DUMP), str(checkout / "addon"), "guides"], capture_output=True, text=True,
                       check=False)
    if r.returncode != 0:
        raise SystemExit(f"{lua} dump.lua: {r.stderr}")
    return [g for g in json.loads(r.stdout)["guides"] if str(g.get("id", "")).startswith("forever/professions/")]


def render(text: str, names: dict) -> str:
    """A guide's text with its tokens written out from its own names table."""
    return TOKEN.sub(lambda m: names.get(f"{m.group(1)}:{m.group(2)}") or f"{m.group(1)} {m.group(2)}", text or "")


def _goal(g: dict, orange: dict) -> dict | None:
    t = g.get("t")
    if t == "craft":
        out = {"t": "craft", "spell": g["spell"], "item": g.get("item"),
               "reagents": [[r["item"], r["count"]] for r in g.get("reagents") or []]}
        for k, key in (("count", "count"), ("untilSkill", "until"), ("yellow", "yellow"), ("grey", "grey")):
            if g.get(k) is not None:
                out[key] = g[k]
        if g["spell"] in orange:
            out["orange"] = orange[g["spell"]]
        return out
    if t == "learn":
        return {"t": "learn", "spell": g["spell"], **({"item": g["item"]} if g.get("item") else {}),
                **({"only": g["only"]} if g.get("only") else {})}
    if t == "train":
        # A gathering guide's train goal names the cap only (Fishing, Mining).
        return {"t": "train", **({"rank": g["rank"]} if g.get("rank") else {}),
                **({"max": g["rankMax"]} if g.get("rankMax") else {})}
    if t in ("buy", "auction", "collect"):
        return {"t": t, "item": g["item"], **({"count": g["count"]} if g.get("count") else {})}
    if t in ("level", "skill"):
        return {"t": t, "level": g.get("level")}
    return None  # goto, accept, turnin, complete: the guide's own errands, shown by its text


def guide_out(g: dict) -> tuple[int, dict, dict, dict, dict]:
    """(skill line, the guide as kept, trainer recipes, pattern prices, vendor unit prices)."""
    names = g.get("names") or {}
    line = next((s["skillLine"] for s in g.get("sections") or [] if s.get("skillLine")), None)
    learn: dict[int, dict] = {}
    patterns: dict[int, dict] = {}
    vendor: dict[int, int] = {}
    steps = []
    for n, s in enumerate(g.get("steps") or [], 1):
        note = s.get("note") or ""
        orange: dict[int, int] = {}
        for ident, at in ORANGE.findall(note):
            craft = next((x for x in s.get("goals") or [] if x.get("t") == "craft"
                          and int(ident) in (x.get("spell"), x.get("item")) and x.get("untilSkill")), None)
            if craft:
                orange[craft["spell"]] = int(at)
        for spell, frm, price in LEARN_FROM.findall(note):
            learn[int(spell)] = {"from": int(frm), "copper": copper(price)}
        for item, skill, price in TEACHES.findall(note):
            patterns[int(item)] = {"skill": int(skill), "copper": copper(price)}
        for count, item, price in VENDOR.findall(note):
            if int(count) > 0:
                vendor[int(item)] = round(copper(price) / int(count))
        goals = [x for x in (_goal(x, orange) for x in s.get("goals") or []) if x]
        steps.append({"n": n, "title": render(s.get("title"), names), "note": render(note, names),
                      "section": s.get("section"), "goals": goals})
    out = {"id": g["id"], "title": render(g.get("title"), names),
           "sections": [[x["from"], x["to"]] for x in g.get("sections") or []], "steps": steps}
    return line, out, learn, patterns, vendor


def client(build: str) -> dict:
    t = {name: wago(name, build) for name in TABLES}
    reagents = {}
    for r in t["SpellReagents"]:
        rs = [[i(r[f"Reagent_{k}"]), i(r[f"ReagentCount_{k}"])] for k in range(8) if i(r.get(f"Reagent_{k}"))]
        if rs:
            reagents[r["SpellID"]] = rs
    makes: dict[str, list[int]] = {}
    for e in t["SpellEffect"]:
        if e["Effect"] == CREATE_ITEM and i(e["EffectItemType"]) and e["SpellID"] not in makes:
            makes[e["SpellID"]] = [i(e["EffectItemType"]), max(1, i(e["EffectBasePointsF"]))]
    names = {r["ID"]: r["Name_lang"] for r in t["SpellName"]}
    recipes = {}
    for r in t["SkillLineAbility"]:
        line = i(r["SkillLine"])
        if line not in CRAFTING or r["Spell"] not in reagents:
            continue
        e = {"p": line, "name": names.get(r["Spell"]) or None, "y": i(r["TrivialSkillLineRankLow"]),
             "g": i(r["TrivialSkillLineRankHigh"]), "r": reagents[r["Spell"]]}
        if r["Spell"] in makes:
            e["i"], e["n"] = makes[r["Spell"]]
        if r["AcquireMethod"] == "1":
            e["auto"] = True  # learned with the skill itself
        recipes[r["Spell"]] = e
    sparse = {r["ID"]: r for r in t["ItemSparse"]}
    effect = {r["ID"]: r for r in t["ItemEffect"]}
    teaches = {}
    for x in t["ItemXItemEffect"]:
        e = effect.get(x["ItemEffectID"])
        if e and e["TriggerType"] == LEARN_TRIGGER and e["SpellID"] in recipes:
            s = sparse.get(x["ItemID"]) or {}
            teaches[x["ItemID"]] = [i(e["SpellID"]), i(s.get("RequiredSkillRank"))]
    made = {str(e["i"]) for e in recipes.values() if e.get("i")}
    bop = sorted(int(k) for k in made | teaches.keys() if i((sparse.get(k) or {}).get("Bonding")) == BIND_ON_PICKUP)
    return {"recipes": dict(sorted(recipes.items(), key=lambda kv: int(kv[0]))),
            "patterns": dict(sorted(teaches.items(), key=lambda kv: int(kv[0]))), "bop": bop}


def check(path: Path) -> list[str]:
    """What's wrong with the committed file (offline)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{path}: {e}"]
    bad = []
    if doc.get("schema") != 1 or not isinstance(doc.get("recipes"), dict) or not isinstance(doc.get("guides"), dict):
        return ["not a craft-reference file (schema 1, recipes, guides)"]
    for line, g in doc["guides"].items():
        if not line.isdigit() or not g.get("steps"):
            bad.append(f"guide {line}: no steps")
        for s in g.get("steps") or []:
            for x in s.get("goals") or []:
                if x.get("t") == "craft" and not (x.get("count") or x.get("until")):
                    bad.append(f"guide {line} step {s.get('n')}: a craft with neither a count nor a skill")
    for spell, r in doc["recipes"].items():
        if not spell.isdigit() or not r.get("r") or r.get("p") not in CRAFTING:
            bad.append(f"recipe {spell}: malformed")
    for item, (spell, _) in doc.get("patterns", {}).items():
        if str(spell) not in doc["recipes"]:
            bad.append(f"pattern {item} teaches {spell}, no such recipe")
    # Known facts (Forever client, 1.60.1): Gloves of Meditation's colours, the
    # Filigreed Shadow Circlet's pattern and its binding.
    gm = doc["recipes"].get("3852") or {}
    if (gm.get("y"), gm.get("g"), gm.get("i")) != (125, 155, 4318):
        bad.append("Gloves of Meditation (3852): yellow 125, grey 155, makes 4318")
    if doc.get("patterns", {}).get("253982") != [1257416, 125]:
        bad.append("Pattern: Filigreed Shadow Circlet (253982) teaches 1257416 at Tailoring 125")
    if 253981 not in doc.get("bop", []):
        bad.append("Filigreed Shadow Circlet (253981) binds when picked up")
    return bad[:20]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--polestar", type=Path, help="a Polestar release checkout (tools/polestar-tag)")
    ap.add_argument("--lua", default="lua", help="a Lua 5.1-5.4 interpreter")
    ap.add_argument("--build", help="Forever's client build (default: data/game-items.json's)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true", help="check the committed file, offline")
    a = ap.parse_args(argv)
    if a.check:
        bad = check(a.out)
        for b in bad:
            print(b, file=sys.stderr)
        print(f"{a.out}: {'ok' if not bad else f'{len(bad)} problems'}")
        return 1 if bad else 0
    if not a.polestar:
        ap.error("--polestar is required")
    build = a.build or json.loads(GAME_ITEMS.read_text(encoding="utf-8"))["source"]["build"]
    tag = (ROOT / "tools" / "polestar-tag").read_text(encoding="utf-8").strip()
    commit = subprocess.run(["git", "-C", str(a.polestar), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=False).stdout.strip() or None
    by_line, learn, patterns, vendor = {}, {}, {}, {}
    for g in guides(a.polestar, a.lua):
        line, out, ln, pt, vd = guide_out(g)
        if line:
            by_line[str(line)] = out
        learn.update(ln)
        patterns.update(pt)
        vendor.update(vd)
    doc = {
        "schema": 1,
        "source": {
            "polestar": {"tag": tag, "commit": commit, "licence": "GPL-3.0",
                         "files": ["addon/Polestar_Guides/Guides.lua"],
                         "note": "profession guides: the route, trainer recipes and prices, vendor goods"},
            "client_tables": {"via": "wago.tools", "product": "wowf", "build": build, "tables": list(TABLES),
                              "note": "Game data (c) Blizzard Entertainment, used as facts"},
        },
        "guides": dict(sorted(by_line.items(), key=lambda kv: int(kv[0]))),
        "trainer": {str(k): v for k, v in sorted(learn.items())},
        "pattern_vendor": {str(k): v for k, v in sorted(patterns.items())},
        "vendor": {str(k): v for k, v in sorted(vendor.items())},
        **client(build),
    }
    # indent 1 (one key per line, like data/dungeons/wings.json): git merges disjoint changes, where the
    # one-line file conflicted on every PR that touched it (2026-11-25).
    a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{a.out}: {len(doc['guides'])} guides, {len(doc['recipes'])} recipes, {len(doc['patterns'])} patterns, "
          f"build {build}")
    bad = check(a.out)
    for b in bad:
        print(b, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
