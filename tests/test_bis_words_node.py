"""The page's wording for an item's gain (web/app.js upShort/upText), lifted
and run under node: the slot's BiS share from what's worn to this item,
"70% → 91% on your chest" (owner, 2026-11-21: "+21% on your chest" read as
21% more stats). The server says it the same way (wowstats/gear.py
gain_words, tests/test_gear.py). Needs node."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# What upText needs, in the order app.js defines it.
PARTS = (r"^const pts = .*?;\n", r"^const slotKey = .*?;\n", r"^function bisTier\(.*?^}\n",
         r"^function badgeFor\(.*?^}\n", r"^function upShort\(.*?^}\n", r"^function upText\(.*?^}\n")


def words(items: list) -> list:
    src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    code = ""
    for part in PARTS:
        m = re.search(part, src, re.S | re.M)
        assert m, f"app.js lost {part}"
        code += m.group(0)
    script = code + f"process.stdout.write(JSON.stringify({json.dumps(items)}.map((u) => [upShort(u), upText(u)])));"
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return [tuple(x) for x in json.loads(r.stdout)]


def test_from_worn_to_this_item():
    assert words([{"slot_name": "Chest", "bis_pct": 91, "bis_pct_worn": 70, "delta_pct": 21, "gain": 4.2}]) \
        == [("70% → 91%", "70% → 91% on your chest")]


def test_empty_slot():
    assert words([{"slot_name": "Chest", "bis_pct": 91, "bis_pct_worn": None, "delta_pct": 91, "gain": 9}]) \
        == [("empty → 91%", "empty → 91% on your chest")]


def test_ring_pair_says_the_slot_not_which_ring():
    # gear.bis_badge measured it against the weaker worn ring.
    assert words([{"slot_name": "Finger", "bis_pct": 67, "bis_pct_worn": 33, "delta_pct": 34, "gain": 4}]) \
        == [("33% → 67%", "33% → 67% on your finger")]


def test_weapon_setup():
    got = words([{"slot_name": "Main hand", "bis_pct": 100, "bis_pct_worn": 50, "delta_pct": 50, "gain": 10},
                 {"slot_name": "Off hand", "bis_pct": 75, "bis_pct_worn": 50, "delta_pct": 25, "gain": 5}])
    assert got == [("50% → 100%", "50% → 100% on your weapons"), ("50% → 75%", "50% → 75% on your weapons")]


def test_no_badge_keeps_the_points():
    assert words([{"slot_name": "Main hand", "bis_pct": None, "bis_pct_worn": None, "delta_pct": None, "gain": 4.3}]) \
        == [("+4.3", "Main hand, +4.3 points")]


# --- each place's text (owner, 2026-11-21) ------------------------------------
# One helper, two forms: the doll says "with the best at hand" (the slot is
# its place), every other place "on your <slot>" (upLine adds the level an
# item opens at).

def places(items: list) -> list:
    src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    code = ""
    for part in (*PARTS, r"^function upLine\(.*?^}\n"):
        m = re.search(part, src, re.S | re.M)
        assert m, f"app.js lost {part}"
        code += m.group(0)
    script = code + (f"process.stdout.write(JSON.stringify({json.dumps(items)}"
                     ".map((u) => ({doll: upText(u, 'doll'), gear: upText(u), line: upLine(u)}))));")
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(r.stdout)


def test_each_place_says_it_the_same_way():
    head = {"slot_name": "Head", "bis_pct": 82, "bis_pct_worn": 17, "delta_pct": 65, "gain": 9.4, "now": True}
    assert places([head]) == [{"doll": "17% → 82% with the best at hand", "gear": "17% → 82% on your head",
                               "line": "17% → 82% on your head"}]


def test_a_later_item_says_its_level_in_the_lists_only():
    u = {"slot_name": "Finger", "bis_pct": 100, "bis_pct_worn": 11, "delta_pct": 89, "gain": 16, "now": False,
         "level": 34}
    got = places([u])[0]
    assert got["line"] == "11% → 100% on your finger at level 34"
    assert got["gear"] == "11% → 100% on your finger"


def test_doll_empty_slot_and_no_badge():
    empty = {"slot_name": "Trinket", "bis_pct": 100, "bis_pct_worn": None, "delta_pct": 100, "gain": 5.4}
    bare = {"slot_name": "Main hand", "bis_pct": None, "bis_pct_worn": None, "delta_pct": None, "gain": 4.3}
    got = places([empty, bare])
    assert got[0]["doll"] == "empty → 100% with the best at hand"
    assert got[1] == {"doll": "+4.3 points with the best at hand", "gear": "Main hand, +4.3 points",
                      "line": "Main hand, +4.3 points"}
