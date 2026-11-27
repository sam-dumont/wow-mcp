"""The live full view's Dungeons panel in a real browser (Chromium and
WebKit) on the demo character (tests/degraded.py's VictoriaLogs stand-in,
Battle.net's talents in vm-wow), after the rc.6 live check (owner,
2026-11-21): its drops and quest rewards say the Overview's item line,
"59% → 100% on your wrist", starting from the doll's badge, with no slot
chip and no points.

Needs the `browser` group."""

from __future__ import annotations

import re
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests import degraded as D  # noqa: E402
from tests.test_bis_one_number import _talents  # noqa: E402
from tests.test_demo_complete_browser import doll_badges  # noqa: E402
from wowstats import plan as planmod  # noqa: E402
from wowstats import ui  # noqa: E402


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("full"), metrics=_talents())
    tmp = tmp_path_factory.mktemp("livefix")
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp / "a"),
           "WOW_ITEMS": "off", "MODEL3D_CACHE_DIR": str(tmp / "c")}
    planmod._CACHE.clear()
    app = ui.App(env)
    app.port = 0
    srv = ui.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/?char={D.CHAR}"
    srv.shutdown()
    srv.server_close()
    vl.close()
    planmod._CACHE.clear()
    mp.undo()


@pytest.fixture(scope="module")
def pw():
    with sync_api.sync_playwright() as p:
        yield p


def open_page(pw, engine, url, width):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    pg = b.new_page(viewport={"width": width, "height": 900})
    errors = []
    # WebKit reports a ResizeObserver that resized again within a frame as
    # an error (tests/test_browser.py): not one of the page's.
    pg.on("pageerror", lambda e: "ResizeObserver loop" in str(e) or errors.append(str(e)))
    pg.goto(url)
    return b, pg, errors


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_dungeon_item_lines_are_the_overviews(site, pw, engine, width):
    url = site
    b, pg, errors = open_page(pw, engine, url, width)
    try:
        pg.wait_for_selector("#home .move.k-dungeon", timeout=30000)
        # The Overview's dungeon card says the line the panel says.
        card = pg.locator("#home .move.k-dungeon").first.text_content()
        assert re.search(r"(empty|\d+%) → \d+% on your ", card), card
        pg.click("#tab-overview")
        pg.wait_for_selector("#gear .slot .ifr .ibis", timeout=30000)
        pg.wait_for_selector(".dg .dgdrop.missing .dgmeta .gain", timeout=30000)
        doll = doll_badges(pg)
        lines = pg.eval_on_selector_all(".dg .dgdrop.missing:has(.ifr .ibis) .dgmeta .gain",
                                        "els => els.map(e => e.textContent)")
        assert len(lines) >= 5, lines
        for text in lines:
            m = re.match(r"^(empty|\d+%) → (\d+)% on your ([a-z ]+?)( at level \d+)?$", text)
            assert m, text
            assert m.group(1) in doll.get(m.group(3), {"empty"}), (text, doll.get(m.group(3)))
        assert not pg.locator(".dg .dgdrop:has(.dgmeta .gain) .pill").count(), "the slot is in the line, not a chip"
        trips = pg.eval_on_selector_all(".dg .dgfacts", "els => els.map(e => e.innerText)")
        assert trips and not any(re.search(r"[+−]\d+\.\d", t) for t in trips), trips
        over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 1, f"{over}px sideways at {width}"
        assert not errors, errors
    finally:
        b.close()


def test_phone_shows_the_handins_wing_progress_and_both_mask_bosses(site, pw, demo_clock):
    browser, page, errors = open_page(pw, "chromium", site, 390)
    try:
        def inspect_handins(parent):
            summary = parent.get_by_text("12 hand-ins this trip (91,031 XP)", exact=True)
            summary.click(timeout=30000)
            details = summary.locator("..")
            assert details.locator("li").count() == 12
            assert "Prerequisites (5), 11,200 XP" in details.inner_text()
            assert "Dungeon quests (7), 79,831 XP" in details.inner_text()
            assert details.locator("li a").count() == 12
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

        inspect_handins(page.locator("#home"))
        page.get_by_role("tab", name="Character", exact=True).click()
        inspect_handins(page.locator(".dg").filter(has=page.get_by_role("heading", name="Zul'Farrak", exact=True)))
        armory = page.locator(".dg").filter(has=page.get_by_role("heading", name="Scarlet Monastery: Armory"))
        assert "kill Herod" in armory.locator(".dgverdict").inner_text()
        assert "Down the Scarlet Path" in armory.locator(".dgnext").inner_text()
        page.get_by_role("button", name="By where you get it").click()
        mask = page.locator("#advice .srcgroup .cand[data-up='1-9470']").first
        mask.locator("xpath=ancestor::details").evaluate_all("els => els.forEach(e => e.open = true)")
        line = mask.locator(".srcline").inner_text()
        assert "Nekrum Gutchewer" in line and "25%" in line
        assert "Shadowpriest Sezz'ziz" in line and "drop rate unknown" in line
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert not errors
    finally:
        browser.close()
