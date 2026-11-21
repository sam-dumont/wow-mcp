"""The live full view's "How you play" in a real browser (Chromium and
WebKit): it says when the analysis ran and who ran it, the player's model
or the server's (owner, 2026-11-21). The demo character on
tests/degraded.py's VictoriaLogs stand-in; the analysis answer is a fixed
one (wowstats.analysis.answer's shape).

Needs the `browser` group."""

from __future__ import annotations

import re
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests import degraded as D  # noqa: E402
from wowstats import plan as planmod  # noqa: E402
from wowstats import ui  # noqa: E402

RUNS = {"server": "2026-11-19T21:05:00+00:00", "client": "2026-11-20T08:30:00+00:00"}


def _analysis(kind: str) -> dict:
    """The full view's /api/analysis answer, one finding and one nudge."""
    return {"available": True, "char": D.CHAR, "updated": RUNS[kind], "findings": [
        {"key": "f1", "title": "Deaths cluster on escorts", "text": "Three of five deaths were on escort quests.",
         "action": {"do": "Skip the next escort", "where": "Thousand Needles", "next_step": "Drop it"}}],
        "nudges": [{"text": "Train before the next zone.", "where": "Ironforge", "next_step": "Fly there"}],
        "patterns": [], "time_budget": [], "last_run": {"at": RUNS[kind], "kind": kind},
        "last_analysis": {"at": RUNS[kind], "kind": kind}, "runs": []}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    from wowstats import analysis

    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("full"))
    tmp = tmp_path_factory.mktemp("asof")
    kind = {"v": "server"}
    mp.setattr(analysis, "answer", lambda env, name, tier: _analysis(kind["v"]))
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp / "a"),
           "WOW_ITEMS": "off", "MODEL3D_CACHE_DIR": str(tmp / "c")}
    planmod._CACHE.clear()
    app = ui.App(env)
    app.port = 0
    srv = ui.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/?char={D.CHAR}", kind
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
@pytest.mark.parametrize("kind,who", [("server", "the server"), ("client", "you")])
def test_how_you_play_says_when_and_who(site, pw, engine, kind, who):
    url, state = site
    state["v"] = kind
    b, pg, errors = open_page(pw, engine, url, 1280)
    try:
        pg.click("#tab-overview")
        pg.wait_for_selector("#analysis:not([hidden]) .asof", timeout=30000)
        text = pg.locator("#analysis .asof").inner_text()
        day = {"server": "19 Nov", "client": "20 Nov"}[kind]
        assert re.fullmatch(rf"As of \w{{3}} {day}, by {who}\.", text), text
        assert not errors, errors
    finally:
        b.close()
