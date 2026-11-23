"""The golden characters: a few made-up players whose events the slices
send through the real path (tests/slices/harness.py). Seeded and built from
the fixture generator's own player (tools/fixtures/generate.py), so every run
sends the same events; their last day is the demo's (tests/degraded.py END),
so one fixed clock serves all of them.

- nibbet: the docs site's demo (build_demo), every source.
- nibbet-gear-no-bags: the demo before Syndicator scanned his bags: the gear
  sheet with its items, no wow-bags yet (Syndicator installed).
- gorvak: a level 60 warrior who dinged on the last evening (raid prep, the
  time in the level from the Recorder after the ding).
- tilly: a fresh level 10 mage, no ding yet (no Polestar split at all).
- nibbet-bare: the demo without the Recorder and Syndicator (Polestar's
  splits and sheets, quests and prices).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from tests import degraded as D

G = D.generate
# The generator's table stops at the levels its fixtures play.
for _lv, _xp in ((10, 7600), (11, 8800), (59, 209800), (60, 209800)):
    G.XP_MAX.setdefault(_lv, _xp)

SETUP = {"polestar": "0.4.1", "recorder": "0.4.1", "syndicator": "1.9.2", "questie": "11.18.0"}


def _day(n: int, h: int, mi: int = 0) -> float:
    """Local time `n` days before the demo's last day."""
    d = datetime.fromisoformat(D.END) - timedelta(days=n)
    return G.local(d.year, d.month, d.day, h, mi)


def _finish(p, addons: dict) -> list[dict]:
    p.other("wow-setup", addons=addons, recorder_on=True, saved_chars=1)
    p.out[-1].pop("char", None)
    return G.shipper_shapes(sorted(p.out, key=lambda r: r["_time"]))


def gorvak() -> list[dict]:
    p = G.Player(G.Game(), random.Random(60), "Gorvak-Gearholt", cls="WARRIOR", race="Human", faction="Alliance",
                 sex=2, level=59, xp=G.XP_MAX[59] - 6000, money=3_400_000, bind="Stormwind City")
    p.login(_day(3, 20), "Stormwind City", "Trade District")
    p.wait(1500)
    p.gain(2500)
    p.logout()
    p.login(_day(1, 19), "Stormwind City", "Trade District")
    p.wait(900)
    p.gain(4000)  # the ding to 60
    p.wait(2400)
    G.gear_sheet(p)
    p.logout()
    return _finish(p, SETUP)


def tilly() -> list[dict]:
    p = G.Player(G.Game(), random.Random(10), "Tilly-Gearholt", cls="MAGE", race="Gnome", faction="Alliance",
                 sex=3, level=10, xp=1200, money=4500, bind="Goldshire")
    p.login(_day(1, 18), "Elwynn Forest", "Goldshire")
    p.wait(1800)
    p.gain(900)
    G.gear_sheet(p)
    p.logout()
    return _finish(p, SETUP)


def nibbet_gear_no_bags() -> list[dict]:
    return [r for r in D.base_rows() if r["feed"] != "wow-bags"]


def nibbet_bare() -> list[dict]:
    return D._setup(D._drop(D.base_rows(), "wow-recorder", "wow-gear", "wow-bags"),
                    drop_addons=("recorder", "syndicator"))


GOLDEN = {
    "nibbet": (D.CHAR, D.base_rows),
    "nibbet-gear-no-bags": (D.CHAR, nibbet_gear_no_bags),
    "gorvak": ("Gorvak-Gearholt", gorvak),
    "tilly": ("Tilly-Gearholt", tilly),
    "nibbet-bare": (D.CHAR, nibbet_bare),
}
