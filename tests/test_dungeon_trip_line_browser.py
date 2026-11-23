"""The dungeon trip's line in a real browser (Chromium and WebKit, phone and
desktop width) on the demo character: the Overview's dungeon move and the
Dungeons panel say the same one line, the trip's time bold, then its XP,
its pace against questing and its parts (owner, 2026-11-23: "~2h10 trip for
~54k XP + 3 upgrades, 1.6× your questing (25 min pickups · 1h15 inside ·
20 min hand-ins)").

Needs the `browser` group."""

from __future__ import annotations

import os
import re

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dungeon_lines_browser import open_page, pw, site  # noqa: E402, F401

LINE = re.compile(r"^~(\d+ min|\d+h\d\d) trip for ~[\d.,]+k? XP( \+ \d+ upgrades?)?, ([\d.]+×|under 0\.1×) your "
                  r"questing \(((\d+ min|\d+h\d\d) (pickups|inside|hand-ins)( · )?)+\)\.$")


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_the_trip_line(site, pw, engine, width):  # noqa: F811
    b, pg, errors = open_page(pw, engine, site, width)
    try:
        pg.wait_for_selector("#home .move.k-dungeon .mgain", timeout=30000)
        move = pg.locator("#home .move.k-dungeon .mgain").first
        text = move.text_content()
        assert LINE.match(text), text
        assert re.match(r"^~(\d+ min|\d+h\d\d) trip$", move.locator("b").text_content())
        shot = os.environ.get("TRIP_SHOTS")
        if shot:
            pg.locator("#home .move.k-dungeon").first.screenshot(path=f"{shot}/trip-after-move-{engine}-{width}.png")
        pg.click("#tab-overview")
        pg.wait_for_selector(".dg .dggain", timeout=30000)
        panel = pg.eval_on_selector_all(".dg .dggain", "els => els.map(e => e.textContent)")
        # Word for word: the move's line is the panel's.
        assert text in panel, (text, panel)
        trips = [t for t in panel if " trip for " in t]
        assert len(trips) >= 3 and all(LINE.match(t) for t in trips), panel
        if shot:
            pg.locator(".dg:has(.dggain)").first.screenshot(path=f"{shot}/trip-after-panel-{engine}-{width}.png")
        over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 1, f"{over}px sideways at {width}"
        assert not errors, errors
    finally:
        b.close()
