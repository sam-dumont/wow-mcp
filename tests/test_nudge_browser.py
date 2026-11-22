"""The refresh nudge on the live full view, in a real browser (Chromium and
WebKit, 390 and 1280 px): a card above the Ask links once the character's
play since its last analysis nears the server's own refresh, with the
prompt to paste; hidden for the browser session once dismissed; absent
below the threshold. The demo character on tests/degraded.py's
VictoriaLogs stand-in; /api/nudge's answer is a fixed one
(wowstats.analysis.auto, Service.nudge's shape). The public view has no
card (tests/analysis/test_nudge.py: no route there either).

Needs the `browser` group. WOW_SHOTS=<dir> keeps a screenshot per engine,
width and theme."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests import degraded as D  # noqa: E402
from wowstats import plan as planmod  # noqa: E402
from wowstats import ui  # noqa: E402

H = 3600


def nudge(**over) -> dict:
    return {"char": D.CHAR, "played_since_s": 10 * H + 12 * 60, "played_since": "10 h 12",
            "server_in_s": 4 * H + 48 * 60, "server_in": "4 h 48", "never_refreshed": False, "warn_at": "10 h 00",
            "refresh_after": "15 h 00", "you_pay": False, "operator": "Sam", "others": 0,
            "ask": f"Using the wow connector, run the analyse prompt for {D.CHAR}.", **over}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("full"))
    tmp = tmp_path_factory.mktemp("nudge")
    state = {"n": nudge()}
    mp.setattr(ui.App, "nudge", lambda self, name: state["n"])
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp / "a"),
           "WOW_ITEMS": "off", "MODEL3D_CACHE_DIR": str(tmp / "c")}
    planmod._CACHE.clear()
    app = ui.App(env)
    app.port = 0
    srv = ui.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/?char={D.CHAR}", state
    srv.shutdown()
    srv.server_close()
    vl.close()
    planmod._CACHE.clear()
    mp.undo()


@pytest.fixture(scope="module")
def pw():
    with sync_api.sync_playwright() as p:
        yield p


def open_page(pw, engine, url, width, scheme="light"):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    pg = b.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    errors = []
    pg.on("pageerror", lambda e: "ResizeObserver loop" in str(e) or errors.append(str(e)))
    pg.goto(url)
    pg.click("#tab-overview")
    return b, pg, errors


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_card_says_how_long_and_the_one_thing_to_do(site, pw, engine, width, scheme):
    url, state = site
    state["n"] = nudge()
    b, pg, errors = open_page(pw, engine, url, width, scheme)
    try:
        box = pg.wait_for_selector("#nudgebox:not([hidden]) .nudgehead", timeout=30000)
        assert box.inner_text() == "Your analysis is 10h 12m of play old."
        text = pg.locator("#nudgebox").inner_text()
        assert ("In about 4h 48m of play the server refreshes it on Sam’s credit. Be nice to Sam: ask your own "
                "assistant to run it.") in text
        assert pg.locator("#nudgebox code").inner_text() == f"Using the wow connector, run the analyse prompt for {D.CHAR}."
        # Nothing runs off the side at phone width.
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        card = pg.locator("#nudgebox").bounding_box()
        assert card["x"] >= 0 and card["x"] + card["width"] <= width
        shots = os.environ.get("WOW_SHOTS")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            pg.locator("#nudgebox").scroll_into_view_if_needed()
            box = pg.locator("#nudgebox").bounding_box()
            top = max(0, box["y"] - 60)
            pg.screenshot(path=str(Path(shots) / f"nudge-{engine}-{width}-{scheme}.png"),
                          clip={"x": 0, "y": top, "width": width, "height": box["y"] - top + box["height"] + 60})
        assert not errors, errors
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_dismissed_for_the_session(site, pw, engine):
    url, state = site
    state["n"] = nudge()
    b, pg, errors = open_page(pw, engine, url, 1280)
    try:
        pg.wait_for_selector("#nudgebox:not([hidden])", timeout=30000)
        pg.click("#nudgebox .nudgeclose")
        assert pg.locator("#nudgebox").is_hidden()
        pg.reload()
        pg.click("#tab-overview")
        pg.wait_for_selector("#askbox:not([hidden]), #planbox:not([hidden])", timeout=30000)
        pg.wait_for_timeout(500)
        assert pg.locator("#nudgebox").is_hidden()
        assert not errors, errors
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_no_card_below_the_threshold_and_the_first_one_said_apart(site, pw, engine):
    url, state = site
    state["n"] = None
    b, pg, errors = open_page(pw, engine, url, 1280)
    try:
        pg.wait_for_selector("#planbox:not([hidden])", timeout=30000)
        pg.wait_for_timeout(500)
        assert pg.locator("#nudgebox").is_hidden()
        state["n"] = nudge(never_refreshed=True, played_since_s=15 * H, server_in_s=0, you_pay=True)
        pg.reload()
        pg.click("#tab-overview")
        pg.wait_for_selector("#nudgebox:not([hidden])", timeout=30000)
        text = pg.locator("#nudgebox").inner_text()
        assert "15h of play and no analysis yet." in text
        assert "The server runs the first one any time now, on your own credit. Cheaper: ask your own assistant to run it." in text
        assert not errors, errors
    finally:
        b.close()
