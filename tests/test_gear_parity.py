"""wowstats.gear against Polestar's own Lua, item by item.

tools/gear_reference/parity.lua runs the release's Gear.lua (with ItemDB.lua,
Weights.lua, SpecData.lua and Adjust.lua) under a stub client and answers
Gear.CompareToEquipped for every item in data/gear-reference.json; the port
must give the same score, gain, percent, slot and usability for each.

Needs Lua 5.1+ and a Polestar checkout at the tag the data was exported
from: POLESTAR_CHECKOUT=<clone> (LUA=lua5.4 to pick the interpreter).
Skipped without them; the route-reference workflow runs it.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from wowstats import gear

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = os.environ.get("POLESTAR_CHECKOUT", "")
LUA = os.environ.get("LUA", "lua")

# Skipped only when no checkout is named: with one, a missing Lua fails, so
# the workflow can't pass by skipping.
pytestmark = pytest.mark.skipif(not CHECKOUT, reason="POLESTAR_CHECKOUT not set: no Polestar to compare with")


def test_lua_is_there():
    assert shutil.which(LUA), f"{LUA} not found (LUA=lua5.4 to pick the interpreter)"

# A level 32 Gnome Priest wearing a two-hander and one trinket (the real
# character this was built with), then other classes, races, levels and specs:
# racial weapon crit, dual wield, armor at 40, Feral's weapon DPS, edits.
WORN = {2: 281321, 3: 5820, 5: 270261, 6: 3344, 7: 270036, 8: 4313, 9: 273808, 10: 270029, 11: 1116,
        12: 281304, 13: 273298, 15: 7356, 16: 1317, 18: 7001}
DAGGER_WORN = {**WORN, 16: 2632, 17: 2632}
CASES = [
    ("PRIEST", 5, "Gnome", 7, "Alliance", 32, "shadow", WORN, {}),
    ("PRIEST", 5, "Gnome", 7, "Alliance", 32, "holy", WORN, {"holy": {"heal": {"x": 1.5}, "spellHit": {"v": 2}}}),
    ("WARRIOR", 1, "Human", 1, "Alliance", 25, "arms", WORN, {}),
    ("WARRIOR", 1, "Orc", 2, "Horde", 20, "fury", DAGGER_WORN, {}),
    ("ROGUE", 4, "Human", 1, "Alliance", 58, "combat", DAGGER_WORN, {"combat": {"agi": {"x": 1.2}}}),
    ("HUNTER", 3, "Orc", 2, "Horde", 45, "beastmastery", WORN, {}),
    ("PALADIN", 2, "Dwarf", 3, "Alliance", 41, "retribution", WORN, {}),
    ("DRUID", 11, "Tauren", 6, "Horde", 30, "feral", WORN, {}),
    ("SHAMAN", 7, "Troll", 8, "Horde", 12, "enhancement", DAGGER_WORN, {}),
    ("MAGE", 8, "Gnome", 7, "Alliance", 60, "fire", WORN, {}),
    ("WARLOCK", 9, "Skyborne", 33, "Horde", 50, "destruction", WORN, {}),
]


def polestar(case, items) -> list[dict]:
    cls, cid, race, rid, side, level, spec, worn, adjust = case
    lines = [f"class {cls} {cid}", f"race {race} {rid} {side}", f"level {level}", f"spec {spec}"]
    lines += [f"equip {s} {i}" for s, i in worn.items()]
    for sp, edits in adjust.items():
        lines += [f"adjust {sp} {st} {k} {v}" for st, e in edits.items() for k, v in e.items()]
    lines += [f"item {i}" for i in items]
    out = subprocess.run([LUA, str(ROOT / "tools" / "gear_reference" / "parity.lua"), str(Path(CHECKOUT) / "addon")],
                         input="\n".join(lines) + "\n", capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines()]


def test_same_release():
    ref = gear.reference()
    toc = (Path(CHECKOUT) / "addon" / "Polestar" / "Polestar.toc").read_text(encoding="utf-8")
    assert f"## Version: {ref.data['polestar']['version']}" in toc


@pytest.mark.parametrize("case", CASES, ids=[f"{c[0]}-{c[6]}-{c[5]}" for c in CASES])
def test_every_item_scores_as_in_polestar(case):
    ref = gear.reference()
    cls, _, _, rid, _, level, spec, worn_ids, adjust = case
    items = sorted(int(k) for k in ref.data["items"])
    rows = polestar(case, items)
    lw = rows.pop()
    assert lw["spec"] == spec
    w, _ = gear.weights_for(ref, cls, spec, level)
    w, _ = gear.adjust_apply(w, adjust.get(spec))
    assert set(w) == set(lw["weights"]) and all(w[k] == pytest.approx(lw["weights"][k], abs=1e-12) for k in w)
    racial = ref.gear["RACIALS"].get(str(rid))
    worn = gear.worn_from(ref, [{"slot": s, "item_id": i} for s, i in worn_ids.items()], w, racial)
    dual = gear.can_dual_wield(ref.gear["PROFICIENCY"][cls], level)
    bad = []
    for r in rows:
        info = ref.item(r["item"])
        usable, reason, min_level = gear.usability(ref, info, cls, level)
        if (usable, reason, min_level) != (r["usable"], r.get("reason"), r["min_level"]):
            bad.append(("usability", r, (usable, reason, min_level)))
            continue
        if not usable:
            continue
        p = gear.compare(info, r["item"], worn, w, racial, dual)
        same = (p["slot"] == r["slot"] and p["score"] == pytest.approx(r["score"], abs=1e-9)
                and p["delta"] == pytest.approx(r["delta"], abs=1e-9)
                and (p["pct"] is None) == (r.get("pct") is None)
                and (p["pct"] is None or p["pct"] == pytest.approx(r["pct"], abs=1e-9))
                and p.get("equipped", False) == r.get("equipped", False))
        if not same:
            bad.append(("compare", r, p))
    assert not bad, f"{len(bad)} of {len(rows)} items differ, first: {bad[:3]}"
