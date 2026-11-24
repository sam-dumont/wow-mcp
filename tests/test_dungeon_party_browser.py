"""Who was in each run, in a real browser (Chromium and WebKit, phone and
desktop width) on the demo character: the Dungeons panel's "Your runs" list
each run's party under it (owner, 2026-11-24: "seeing who I grouped with is
kinda important"). The demo's groupmates come from its combat log.

Needs the `browser` group."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dungeon_lines_browser import open_page, pw, site  # noqa: E402, F401


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_the_party_under_each_run(site, pw, engine, width):  # noqa: F811
    b, pg, errors = open_page(pw, engine, site, width)
    try:
        pg.click("#tab-overview")
        pg.wait_for_selector(".dghist .party", state="attached", timeout=30000)
        hist = pg.locator(".dghist:has(.party)").first
        hist.locator("summary").click()
        party = hist.locator(".party").first
        text = party.text_content()
        assert text.startswith("With: ") and len(party.locator("li").all()) >= 1, text
        shot = os.environ.get("PARTY_SHOTS")
        if shot:
            hist.screenshot(path=f"{shot}/party-{engine}-{width}.png")
        over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 1, f"{over}px sideways at {width}"
        assert not errors, errors
    finally:
        b.close()
