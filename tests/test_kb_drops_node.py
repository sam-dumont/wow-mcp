"""A dungeon whose loot only the research notes list (wowstats/plan/kbloot.py)
on the page: web/app.js kbDropRow() and kbDrops(), lifted and run under node
with an h() that records the tree. The drops made for him come first, three
in sight and the rest under "N more"; an item known by its ID gets its chip,
one known only by name its name and boss, never an empty icon frame (the
demo's icon guard, tests/test_demo_complete_browser.py). Needs node."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

HARNESS = """
      const h = (tag, attrs, ...kids) => ({ tag, cls: (attrs && attrs.class) || '', text: (attrs && attrs.text) || '',
        kids: kids.flat(Infinity).filter((x) => x !== null && x !== undefined && x !== false) });
      const itemChip = (x) => ({ tag: 'chip', cls: '', text: x.name, item_id: x.item_id, kids: [] });
      const whMark = (url, name) => ({ tag: 'a', cls: 'whx', text: '', kids: [] });
      const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
      LIFTED
      process.stdout.write(JSON.stringify(kbDrops(BLOCK)));
"""


def lifted() -> str:
    src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    out = []
    for name in ("kbDropRow", "kbDrops"):
        m = re.search(rf"^function {name}\(.*?^}}\n", src, re.S | re.M)
        assert m, f"app.js lost function {name}"
        out.append(m.group(0))
    return "\n".join(out)


def render(block: dict) -> dict:
    script = HARNESS.replace("LIFTED", lifted()).replace("BLOCK", json.dumps(block))
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(r.stdout)


def walk(n):
    yield n
    for k in n.get("kids") or []:
        yield from walk(k)


def drop(name, fit, item_id=None, boss="Zul'Alai", armour=None):
    return {"name": name, "boss": boss, "armour": armour, "item_id": item_id, "url": "u" if item_id else None,
            "fit": fit, "confidence": "low", "roles": []}


def rows(tree, within):
    return [[x["text"] for x in walk(li) if x["text"]] for n in walk(tree) if n["tag"] == within
            for li in walk(n) if li["tag"] == "li"]


def test_his_drops_first_three_in_sight_the_rest_folded():
    b = {"drops_shown": 3, "kb_drops": [
        drop("Briny Sash", True, 274605, "Captain Dreadrise", "cloth"), drop("Robe of the Drowned", True),
        drop("Priestess Cloak", True), drop("Bloodspine Staff", True), drop("Dreadfrost Saber", None),
        drop("Viscera Spaulders", False, armour="mail")]}
    t = render(b)
    assert [n["text"] for n in walk(t) if n["tag"] == "h4"] == ["Drops for you"]
    top = next(n for n in t["kids"] if n["tag"] == "ul")
    assert [r[0] for r in rows(top, "ul")] == ["Briny Sash", "Robe of the Drowned", "Priestess Cloak"]
    more = next(n for n in t["kids"] if n["tag"] == "details")
    assert next(n["text"] for n in walk(more) if n["tag"] == "summary") == "3 more"
    assert [r[0] for r in rows(more, "ul")] == ["Bloodspine Staff", "Dreadfrost Saber", "Viscera Spaulders"]
    assert ["Viscera Spaulders", "mail", "Zul'Alai"] in rows(more, "ul")


def test_a_chip_only_for_an_item_known_by_its_id():
    t = render({"kb_drops": [drop("Briny Sash", True, 274605), drop("Serpentbane Crown", True)]})
    chips = [n for n in walk(t) if n["tag"] == "chip"]
    assert [c["item_id"] for c in chips] == [274605]
    assert any(n["cls"] == "iname" and n["text"] == "Serpentbane Crown" for n in walk(t))


def test_nothing_made_for_him_says_so_and_still_lists_them():
    t = render({"kb_drops": [drop("Harpoon of Command", False), drop("Dreadfrost Saber", None)]})
    texts = [n["text"] for n in walk(t)]
    assert "Drops" in texts and "None made for your class and spec." in texts and "2 drops" in texts
