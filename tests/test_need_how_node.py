"""The Blocked line's "how to get the prerequisite" (web/app.js needHow),
run under node: the page's own at(), npcs() and needHow(), lifted from
app.js, with h() and whLink() writing plain text. An item a creature drops
says so; an item lying in the world says where; an object named like the
item never reads "X drops from X" (Henrig Lonebrow's Journal, 0.6.1).
Needs node, like tests/test_m3d_node.py."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

JOURNAL = {"kind": "item", "id": 5791, "name": "Henrig Lonebrow's Journal", "url": "https://x/item=5791"}


def lifted() -> str:
    src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    out = []
    for name in ("at", "npcs", "needHow"):
        m = re.search(rf"^function {name}\(.*?^}}\n", src, re.S | re.M)
        assert m, f"app.js lost function {name}"
        out.append(m.group(0))
    return "\n".join(out)


def say(needs: list[dict]) -> str | None:
    script = HARNESS.replace("LIFTED", lifted()).replace("NEEDS", json.dumps(needs))
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(r.stdout)


HARNESS = """
      const flat = (k) => [k].flat(Infinity).filter((x) => x !== null && x !== undefined && x !== false)
        .map((x) => (typeof x === 'string' ? x : x.text)).join('');
      const h = (tag, attrs, ...kids) => ({ text: (attrs && attrs.text) || flat(kids) });
      const whLink = (url, text) => ({ text });
      LIFTED
      const got = needHow(NEEDS);
      process.stdout.write(JSON.stringify(got ? got.text : null));
"""


def need(**k) -> list[dict]:
    return [{"quest_id": 1100, "name": "Lonebrow's Journal", "state": "new", "starts": [JOURNAL], **k}]


def test_an_object_named_like_the_item_never_drops_it():
    obj = {"kind": "object", "id": 19861, "name": "Henrig Lonebrow's Journal"}
    assert say(need(drops=[], found=[obj])) == ": from the item Henrig Lonebrow's Journal"
    placed = {**obj, "zone": "Thousand Needles", "x": 31.2, "y": 42.5}
    assert say(need(found=[placed])) == ": the item Henrig Lonebrow's Journal, found in Thousand Needles 31.2, 42.5"


def test_an_object_with_its_own_name_is_named():
    chest = {"kind": "object", "id": 1, "name": "Battered Chest", "inside": "Razorfen Kraul"}
    assert say(need(found=[chest])) == ": the item Henrig Lonebrow's Journal, found on Battered Chest inside Razorfen Kraul"


def test_a_creature_still_drops_it():
    npc = {"kind": "npc", "id": 639, "name": "Edwin VanCleef", "zone": "Westfall", "x": 42.6, "y": 71.6}
    got = say(need(drops=[npc], found=[{"kind": "object", "id": 2, "name": "Crate"}]))
    assert got == ": Henrig Lonebrow's Journal drops from Edwin VanCleef in Westfall 42.6, 71.6"


def test_a_creature_inside_a_dungeon_reads_inside():
    npc = {"kind": "npc", "id": 4421, "name": "Charlga Razorflank", "inside": "Razorfen Kraul"}
    assert say(need(drops=[npc])) == ": Henrig Lonebrow's Journal drops from Charlga Razorflank inside Razorfen Kraul"


def test_a_prerequisite_with_no_giver_says_where_the_objective_is():
    # Murdaloc (quest 739): the data names no giver; his own ForeverDB records put its first objective in the Badlands.
    spot = {"zone": "Badlands", "x": 49.6, "y": 66.2, "objective": 1, "source": "the quest map"}
    got = say([{"quest_id": 739, "name": "Murdaloc", "state": "new", "objective_at": spot}])
    assert got == ": the objective is near Badlands 49.6, 66.2 (your ForeverDB records)"
    # With a giver, it follows the giver.
    npc = {"kind": "npc", "id": 7, "name": "Someone", "zone": "Loch Modan", "x": 1.0, "y": 2.0}
    assert say([{"quest_id": 739, "name": "Murdaloc", "state": "new", "starts": [npc], "objective_at": spot}]) == (
        ": from Someone in Loch Modan 1, 2, the objective is near Badlands 49.6, 66.2 (your ForeverDB records)")
