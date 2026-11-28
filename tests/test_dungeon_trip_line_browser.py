"""The dungeon trip's line in a real browser (Chromium and WebKit, phone and
desktop width) on the demo character: the Overview's dungeon move and the
Dungeons panel say the same one line, the trip's time bold, then its XP,
its pace against questing and its parts (owner, 2026-11-23: "~1h55 trip for
~58k XP + 4 quest upgrades, at least one of 9 drop upgrades drops: 88% a run, 2.1× your
questing (20 min pickups · 1h15 inside · 15 min hand-ins)").

Needs the `browser` group."""

from __future__ import annotations

import os
import re

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dungeon_lines_browser import open_page, pw, site  # noqa: E402, F401

# The upgrades (owner, 2026-11-23): the quests' first, then the drops with
# the chance one drops in a run, a part with none left out.
ODDS = r"(?:drop chance unknown|>?[\d.]+% a run(?: from known rates)?)"
DROP = (r"(?:\d+ drop upgrades? \(" + ODDS + r"\)|at least one of \d+ drop upgrades drops: " + ODDS
        + r"|\d+ drop upgrades, and at least one of the \d+ with a known rate drops: " + ODDS + ")")
UPS = r"(?: \+ (?:\d+ quest upgrades?(?:, " + DROP + r")?|" + DROP + r"))?"
LINE = re.compile(r"^~(\d+ min|\d+h\d\d) trip for ~[\d.,]+k? (?P<known>known )?XP" + UPS + r", ([\d.]+×|under 0\.1×) your "
                  r"questing \(((\d+ min|\d+h\d\d) (pickups|inside|hand-ins|to find a group \(assumed\))( · )?)+\)\.$")


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
        panel = pg.eval_on_selector_all(".dg .dggain", """els => els.map(e => ({
            line: e.textContent,
            missingXP: [...e.closest('.dg').querySelectorAll('.dgmore .gquests li')]
                .some(q => q.textContent.endsWith(' (XP unknown)'))
        }))""")
        # Word for word: the move's line is the panel's.
        assert any(card["line"] == text for card in panel), (text, panel)
        trips = [card for card in panel if " trip for " in card["line"]]
        assert len(trips) >= 3 and {card["missingXP"] for card in trips} == {False, True}, trips
        for card in trips:
            match = LINE.fullmatch(card["line"])
            assert match, card
            assert bool(match["known"]) == card["missingXP"], card
        if shot:
            pg.locator(".dg:has(.dggain)").first.screenshot(path=f"{shot}/trip-after-panel-{engine}-{width}.png")
        over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 1, f"{over}px sideways at {width}"
        assert not errors, errors
    finally:
        b.close()
