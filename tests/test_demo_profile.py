"""The docs site's demo character (tools/fixtures/generate.py --demo,
site/scripts/demo.py): invented, accepted by /ingest's screen as is, and
rich enough that every section of its pages has something to show."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from wowstats import ingest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "fixtures"))

import generate  # noqa: E402

END = "2026-11-18"


def test_demo_events(tmp_path):
    rows = generate.build_demo(END)
    lines, bad = ingest.parse(generate.jsonl(rows))
    assert bad == 0 and len(lines) == len(rows)
    # One character, the invented one: none of the fixtures' names.
    assert {r["char"] for r in rows if "char" in r} == {generate.DEMO}
    assert not any(n in json.dumps(rows) for n in (generate.PRIEST, generate.HUNTER))
    feeds = Counter(r["feed"] for r in rows)
    assert {"wow-recorder", "wow-level", "wow-dungeon", "wow-gear", "wow-prices", "wow-quests", "wow-setup"} <= set(
        feeds)
    runs = [(r["name"], int(r["level"])) for r in rows if r["feed"] == "wow-dungeon"]
    assert runs == [("Blackfathom Deeps", 23), ("Stormwind Stockade", 31), ("City of Dalaran", 33),
                    ("Razorfen Kraul", 33), ("Scarlet Monastery", 35), ("Scarlet Monastery", 35),
                    ("Scarlet Monastery", 38), ("Scarlet Monastery", 38), ("Razorfen Downs", 40),
                    ("Uldaman", 43), ("Zul'Farrak", 45)]
    snaps = [r for r in rows if r["feed"] == "wow-character"]
    assert snaps[-1]["level"] == "46" and snaps[0]["level"] == "23"  # the first logout, after a level
    # Shifted so the last evening is END, the scans with it: the upgrade is
    # listed on the newest scan day only (the auction house's "on sale now").
    last = max(r["_time"] for r in rows if r["feed"] == "wow-recorder")
    assert datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone(generate.TZ).date().isoformat() == END
    prices = [r for r in rows if r["feed"] == "wow-prices"]
    assert {(r["realm"], r["faction"]) for r in snaps + prices} == {(generate.DEMO_REALM, "Alliance")}
    days = sorted({r["scan_day"] for r in prices})
    assert days[-1] == END and len(days) == 2
    upgrade = [r for r in prices if r["item_id"] == str(generate.AH_UPGRADE)]
    assert [r["scan_day"] for r in upgrade] == [END]
    assert all(r["source_id"].endswith(r["scan_day"]) for r in prices)
    # A full scan each day (ahsource.FULL_SCAN_MIN items at least).
    from wowstats.ahsource import FULL_SCAN_MIN

    assert min(Counter(r["scan_day"] for r in prices).values()) >= FULL_SCAN_MIN
    # Dungeon runs keep their length when shifted.
    for r in rows:
        if r["feed"] == "wow-dungeon":
            t0 = datetime.fromisoformat(r["_time"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(r["left"].replace("Z", "+00:00"))
            assert round((t1 - t0).total_seconds()) == int(r["seconds"])


def test_demo_is_reproducible():
    assert generate.jsonl(generate.build_demo(END)) == generate.jsonl(generate.build_demo(END))


def test_demo_gear_at_46():
    """Nibbet at 46 (sessions_demo_46): a Shadow priest whose every piece is
    something a level-46 character got from a dungeon, a quest or his own
    trade, scored by the product's own Shadow weights (Leveling) at 60-70%
    of the best his level can hold on average, a few pieces BiS."""
    from wowstats import gear

    rows = generate.build_demo(END)
    sheet = [r for r in rows if r["feed"] == "wow-gear"][-1]
    items = json.loads(sheet["items"])
    assert all((it.get("req_level") or 0) <= 46 for it in items)
    a = gear.advise_rows(rows)
    pct = {s["slot"]: s["standing"]["pct"] for s in a["slots"] if s.get("standing") and s["slot"] != 17}
    assert len(pct) == 16
    avg = sum(pct.values()) / len(pct)
    assert 60 <= avg <= 70, (avg, pct)
    assert sum(1 for v in pct.values() if v == 100) >= 3
    # His weapon is the Dreamstaff (his own enchanting, which glows in game);
    # the wand stays in the ranged slot.
    worn = {int(it["slot"]): int(it["item_id"]) for it in items}
    assert worn[16] == 249454 and 17 not in worn and worn[18] == 274425
    # Talents: all 37 points in Shadow, Shadowform among them.
    talents = [r for r in rows if r["feed"] == "wow-talents"][-1]
    assert talents["level"] == "46" and talents["spec"] == "Shadow"
    build = generate.talent_build(46, True)
    assert sum(build.values()) == 37 and build["Shadowform"] == 1
    assert generate.wowsims_string(build).startswith("--")


def test_every_worn_item_comes_from_something_live_in_forever():
    """build_demo refuses gear from content Forever hasn't opened (in_forever,
    by ForeverDB's labels and Polestar's drop tables): the Gurubashi Arena's
    Arena Master, ForeverDB's Classic data only, sat in his trinket slot."""
    generate.in_forever(generate.build_demo(END))
    arena = {"feed": "wow-gear", "items": json.dumps([{"item_id": 18706, "name": "Arena Master", "slot": 13}])}
    with pytest.raises(ValueError, match="Arena Master"):
        generate.in_forever([arena])
    # The shirt and the tabard are worn for the look: no source asked.
    generate.in_forever([{"feed": "wow-gear", "items": [{"item_id": 5976, "slot": 19}]}])
