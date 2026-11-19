"""The docs site's demo character (tools/fixtures/generate.py --demo,
site/scripts/demo.py): invented, accepted by /ingest's screen as is, and
rich enough that every section of its pages has something to show."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

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
    runs = {r["name"]: int(r["level"]) for r in rows if r["feed"] == "wow-dungeon"}
    assert runs == {"Blackfathom Deeps": 23, "Stormwind Stockade": 31, "City of Dalaran": 33, "Razorfen Kraul": 33}
    snaps = [r for r in rows if r["feed"] == "wow-character"]
    assert snaps[-1]["level"] == "34" and snaps[0]["level"] == "23"  # the first logout, after a level
    # Shifted so the last evening is END, the scans with it: the upgrade is
    # listed on the newest scan day only (the auction house's "on sale now").
    last = max(r["_time"] for r in rows if r["feed"] == "wow-recorder")
    assert datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone(generate.TZ).date().isoformat() == END
    prices = [r for r in rows if r["feed"] == "wow-prices"]
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
