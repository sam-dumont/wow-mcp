"""Dungeons run in wings in a real browser (Chromium and WebKit, 390 and
1280) on the demo character (owner, 2026-11-23: "it's well known that
people do different parts"): Scarlet Monastery shows as its wings, each
its own block named "Scarlet Monastery: Graveyard" with the same layout as
any dungeon (verdict, the trip's line, Kill XP a run once open), never the whole
dungeon; the line counts quest upgrades before drop upgrades; and the
Overview's dungeon move says a panel line word for word.

WINGS_SHOTS=<dir> saves the first wing block and the move.

Needs the `browser` group."""

from __future__ import annotations

import os
import re

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dungeon_lines_browser import open_page, pw, site  # noqa: E402, F401
from tests.test_dungeon_trip_line_browser import LINE  # noqa: E402

WING = re.compile(r"^Scarlet Monastery: (Graveyard|Library|Armory|Cathedral)$")


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_a_wing_is_its_own_dungeon(site, pw, engine, width):  # noqa: F811
    b, pg, errors = open_page(pw, engine, site, width)
    shot = os.environ.get("WINGS_SHOTS")
    try:
        pg.wait_for_selector("#home .move.k-dungeon .mgain", timeout=30000)
        move = pg.locator("#home .move.k-dungeon .mgain").first.text_content()
        if shot:
            pg.locator("#home .move.k-dungeon").first.screenshot(path=f"{shot}/wings-after-move-{engine}-{width}.png")
        pg.click("#tab-overview")
        pg.wait_for_selector(".dg .dggain", timeout=30000)
        rows = pg.eval_on_selector_all(
            ".dg", """els => els.map(e => ({
                name: e.querySelector('.dghead h3').textContent,
                verdict: (e.querySelector('.dgverdict') || {}).textContent,
                gain: (e.querySelector('.dggain') || {}).textContent,
                kill: [...e.querySelectorAll('.dgfacts dt')].some(d => d.textContent === 'Kill XP a run')}))""")
        names = [r["name"] for r in rows]
        wings = [r for r in rows if WING.match(r["name"])]
        assert wings, names
        assert "Scarlet Monastery" not in names, names
        for r in wings:
            assert r["verdict"], r
            assert not r["gain"] or LINE.match(r["gain"]), r
        # Kill XP a run, as any dungeon open at the level has it.
        assert any(r["kill"] for r in wings if not r["verdict"].startswith("Opens at")), wings
        # Quest upgrades come before drop upgrades wherever both are said.
        for r in rows:
            g = r["gain"] or ""
            if "quest upgrade" in g and "drop upgrade" in g:
                assert g.index("quest upgrade") < g.index("drop upgrade"), g
        assert any("drop upgrade" in (r["gain"] or "") for r in wings), wings
        # The Overview's move says a panel line word for word.
        assert move in [r["gain"] for r in rows], (move, [r["gain"] for r in rows])
        if shot:
            i = names.index(wings[0]["name"])
            pg.locator(".dg").nth(i).screenshot(path=f"{shot}/wings-after-panel-{engine}-{width}.png")
        over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 1, f"{over}px sideways at {width}"
        assert not errors, errors
    finally:
        b.close()
