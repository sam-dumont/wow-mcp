"""WCAG AA contrast of the panels a click opens, in a real browser, in both
themes: the gear comparison (from the doll and from the list), the item
tooltip, the dungeon cards and the journal entries on /zogzog/ (the full
view's page and styles), and the tooltip on the public page.

The comparison under the doll once took the day page's --panel under the
plate's night ink: cream on parchment, unreadable (owner, 2026-11-19). The
check (tests/contrast.js) reads every visible text node's colour against the
background it really sits on, walking up the tree, so a box that mixes one
palette's surface with another's text fails here whatever its colours.

Same setup as tests/test_advice_browser.py (needs the `browser` group)."""

from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_advice_browser import site  # noqa: E402,F401  (the fixture)
from tests.test_browser import wait_count  # noqa: E402

CHECK = (Path(__file__).parent / "contrast.js").read_text()


@pytest.fixture(scope="module")
def pw(site):  # noqa: F811
    with sync_api.sync_playwright() as p:
        yield p


def page(pw, engine, url, scheme, width=1920):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # browsers not installed
        pytest.skip(f"no {engine}: {e}")
    pg = b.new_page(viewport={"width": width, "height": 1000}, color_scheme=scheme)
    pg.goto(url)
    return b, pg


def legible(pg, selector, what, least=1):
    """Every visible text node under `selector` meets AA: 4.5:1, 3:1 for
    large text (24 px, or 18.66 px bold)."""
    got = pg.evaluate(CHECK, selector)
    assert got["checked"] >= least, f"{what}: {got['checked']} text nodes checked, wanted {least}"
    bad = [f"{f['ratio']}:1 (needs {f['need']}) {f['text']!r} at {f['where']}, colour {f['color']}" for f in got["fails"]]
    assert not bad, f"{what}: {len(bad)} below AA:\n" + "\n".join(bad[:20])


def character(pg):
    pg.locator(".tabs button", has_text="Character").first.click()
    wait_count(pg, "#advice .cand")


def tooltip(pg, item):
    item.hover()
    wait_count(pg, "#wowtip:visible", 1)
    wait_count(pg, "#wowtip .tt-name", 1)


@pytest.mark.parametrize("scheme", ["dark", "light"])
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_the_comparison_is_legible(site, pw, engine, scheme):  # noqa: F811
    url, gear = site
    b, pg = page(pw, engine, url, scheme)
    try:
        character(pg)
        # From the doll, every slot that has a replacement.
        for sl in [s for s in gear["slots"] if s["best"]]:
            pg.locator(f'#gear .slot[data-slot="{sl["slot"]}"]').first.click()
            wait_count(pg, ".cmp", 1)
            legible(pg, ".cmp", f"comparison of slot {sl['slot']} from the doll", least=12)
        # From a row of the list, and from the by-slot table.
        pg.locator("#advice .cand:visible .candmain .itm").first.click()
        wait_count(pg, ".cmp.inrow", 1)
        legible(pg, ".cmp", "comparison from the list", least=12)
        pg.locator("#advice .order button", has_text="By slot").click()
        pg.locator("#advice .best .itm:visible").first.click()
        wait_count(pg, ".cmprow .cmp", 1)
        legible(pg, ".cmp", "comparison from the by-slot table", least=12)
    finally:
        b.close()


@pytest.mark.parametrize("scheme", ["dark", "light"])
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_the_other_panels_are_legible(site, pw, engine, scheme):  # noqa: F811
    url, _ = site
    public = url.split("/zogzog/")[0] + "/?" + url.split("?")[1].split("#")[0]
    b, pg = page(pw, engine, url, scheme)
    try:
        character(pg)
        tooltip(pg, pg.locator("#gear .slotitm:visible").first)
        legible(pg, "#wowtip", "tooltip on the doll", least=3)
        pg.mouse.move(0, 0)
        pg.evaluate("() => document.querySelectorAll('.dg details').forEach((d) => { d.open = true; })")
        legible(pg, ".dg", "dungeon cards", least=20)
        legible(pg, "#gear", "the character plate (doll and advice)", least=40)
        # The badge colours' legend: every tier's colour with its ink.
        legible(pg, "#gear .bislegend", "the BiS badges' legend", least=4)
        pg.locator(".tabs button", has_text="Journal").first.click()
        wait_count(pg, ".jentry")
        # Click first (it toggles), then open them all: a single session stays open.
        pg.locator(".jentry summary").first.click()
        pg.evaluate("() => document.querySelectorAll('.jentry details, details.jentry').forEach((d) => { d.open = true; })")
        legible(pg, ".jentry", "journal entries", least=20)
        pg.goto(public)
        tooltip(pg, pg.locator(".slotitm:visible").first)
        legible(pg, "#wowtip", "tooltip on the public page", least=3)
    finally:
        b.close()

