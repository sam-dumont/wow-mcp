"""The doll badge's colour by closeness to BiS (web/app.js bisTier), lifted
and run under node: legendary for BiS, epic from 85%, rare from 50%, none
below, as the Forever Leveling BiS addon's legend (owner, 2026-11-21).
Needs node."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def tiers(standings: list) -> list:
    src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"^function bisTier\(.*?^}\n", src, re.S | re.M)
    assert m, "app.js lost function bisTier"
    script = m.group(0) + f"process.stdout.write(JSON.stringify({json.dumps(standings)}.map(bisTier)));"
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(r.stdout)


def test_the_thresholds():
    pcts = [100, 99, 85, 84, 50, 49, 0]
    got = tiers([{"rank": 1 if p == 100 else None, "pct": p} for p in pcts])
    assert got == ["bis", "epic", "epic", "rare", "rare", None, None]


def test_bis_is_the_rank_and_2nd_goes_by_its_percent():
    # gear.standing_out: rank 1 is BiS (pct 100); a runner-up shows "2nd"
    # but its colour follows its share of BiS.
    assert tiers([{"rank": 1, "pct": 100}, {"rank": 2, "pct": 97}, {"rank": 2, "pct": 60}, {"rank": 2, "pct": 30}]) \
        == ["bis", "epic", "rare", None]


def test_no_standing_no_colour():
    assert tiers([None]) == [None]
