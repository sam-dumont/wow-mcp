"""The live full view never redraws under the reader (owner, 2026-11-19:
"the refresh every minute sucks because it just messes up with what i'm
doing"). In a real browser (Chromium and WebKit), on the live server with a
faked store and a faked clock:

- new data only brings a "New data · Show" pill: the open fold, the
  scroll, "Scored for" and the item search stay as the reader left them;
- "Show" draws the new answer and puts the reader back where they were;
- a hidden tab asks nothing, and asks once when it comes back.

Same setup as tests/test_advice_browser.py (needs the `browser` group)."""

import gzip
import json
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser import Catalog, wait_count  # noqa: E402
from tests.test_public_view import CHAR, PoisonStore  # noqa: E402
from wowstats import itemcard, ui  # noqa: E402
from wowstats.plan import facts  # noqa: E402

DATA = Path(__file__).parent / "data"
CHECK_MS = 3 * 60 * 1000  # app.js NEWS_EVERY_MS


@pytest.fixture(scope="module")
def live():
    mp = pytest.MonkeyPatch()
    mp.setattr(facts, "store_for", lambda env, like=None: like)
    mp.setattr(itemcard, "shared", lambda: Catalog())
    mp.setenv("WOW_ITEMS", "off")
    gear = {**json.loads(gzip.decompress((DATA / "gear-priest.json.gz").read_bytes())), "char": CHAR}

    def scored(self, name, spec):
        # The real answer: a spec the page picked comes back as the page's.
        if not spec:
            return gear
        label = next(c["label"] for c in gear["spec"]["choices"] if c["key"] == spec)
        return {**gear, "spec": {**gear["spec"], "key": spec, "label": label, "source": "page"}}

    mp.setattr(ui.App, "gear", scored)
    app = ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": "http://vl.invalid",
                  "MODEL3D": "off", "MCP_PUBLIC_URL": ""}, store=PoisonStore())
    app.port = 0
    srv = ui.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
    mp.undo()


@pytest.fixture(scope="module")
def pw(live):
    with sync_api.sync_playwright() as p:
        yield p


# A tab the test can hide: Playwright has no switch for it, so the page
# reads visibilityState from a flag the test sets.
HIDEABLE = """
  window.__hidden = false;
  Object.defineProperty(Document.prototype, 'visibilityState',
    { configurable: true, get() { return window.__hidden ? 'hidden' : 'visible'; } });
  Object.defineProperty(Document.prototype, 'hidden', { configurable: true, get() { return window.__hidden; } });
"""


def open_live(pw, engine, base, width=1280):
    """The Character tab, a fake clock, /api/char answering one level more
    once `bump` is set; every API request recorded."""
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    c = b.new_context(viewport={"width": width, "height": 900})
    c.add_init_script(HIDEABLE)
    pg = c.new_page()
    pg.clock.install()
    bump = {"on": False}
    api = []
    pg.on("request", lambda r: api.append(r.url) if "/api/" in r.url else None)

    def char(route):
        resp = route.fetch()
        d = resp.json()
        if bump["on"]:
            d["header"]["level"] = (d["header"].get("level") or 0) + 1
        route.fulfill(response=resp, body=json.dumps(d))

    pg.route("**/api/char?*", char)
    pg.goto(f"{base}/?char={CHAR}&tab=overview")
    wait_count(pg, "#advice .cand")
    wait_count(pg, "#advice details.upmore")
    # The first load done, the dungeon cards' item icons (/api/items) too: a
    # late batch once landed after the test's api.clear() and read as a poll.
    pg.wait_for_load_state("networkidle")
    return b, pg, bump, api


def reader_settles(pg):
    """What a reader does: picks a spec, opens a fold, scrolls to it."""
    pg.select_option("#gearspec", "holy")
    pg.locator("#gearspec").evaluate("(s) => s.blur()")
    wait_count(pg, "#gearspec option[value='holy'][selected]", 1)  # the redraw for the pick is in
    fold = pg.locator("#advice details.upmore").first
    fold.locator("summary").click()
    assert fold.evaluate("(d) => d.open")
    pg.evaluate("() => window.scrollTo(0, 600)")
    return pg.evaluate("() => window.scrollY")


def until(ok, timeout=10.0):
    """Polls from Python: the page's CSP has no 'unsafe-eval' for
    wait_for_function."""
    import time

    end = time.monotonic() + timeout
    while not ok():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.1)


def level(pg):
    return pg.locator("#hero").inner_text()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_new_data_waits_for_the_reader(live, pw, engine):
    b, pg, bump, api = open_live(pw, engine, live)
    y = reader_settles(pg)
    before = level(pg)
    # Minutes pass with nothing new: no pill, nothing redrawn.
    api.clear()
    pg.clock.run_for(CHECK_MS * 2 + 1000)
    pg.wait_for_timeout(300)
    assert pg.locator(".newspill:not([hidden])").count() == 0
    assert [u for u in api if "/api/char" not in u] == [], api  # only the cheap check
    assert len([u for u in api if "/api/char" in u]) == 2, api
    # New data: the pill, and still nothing moved.
    bump["on"] = True
    api.clear()
    pg.clock.run_for(CHECK_MS + 1000)
    pill = pg.locator(".newspill:not([hidden]) button")
    pill.wait_for(state="visible")
    assert pill.inner_text().replace("\n", " ").split() == ["New", "data", "·", "Show"]
    assert pg.locator("#advice details.upmore").first.evaluate("(d) => d.open")
    assert pg.locator("#gearspec").input_value() == "holy"
    assert abs(pg.evaluate("() => window.scrollY") - y) <= 1
    assert level(pg) == before
    assert [u for u in api if "/api/char" not in u] == [], api
    # Once the pill is up, no more checks until the reader takes it.
    api.clear()
    pg.clock.run_for(CHECK_MS * 3)
    pg.wait_for_timeout(200)
    assert api == [], api

    # "Show": the new answer, the reader's place kept.
    pill.click()
    pg.locator(".newspill").wait_for(state="hidden")
    until(lambda: level(pg) != before)
    until(lambda: pg.locator("#advice details.upmore").first.evaluate("(d) => d.open"))
    pg.wait_for_timeout(500)  # every section is in: nothing closes it again
    assert pg.locator("#advice details.upmore").first.evaluate("(d) => d.open")
    assert pg.locator("#gearspec").input_value() == "holy"
    assert abs(pg.evaluate("() => window.scrollY") - y) <= 40
    assert "Refreshes every minute" not in pg.locator("#foot").inner_text()
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_a_hidden_tab_asks_nothing(live, pw, engine):
    b, pg, bump, api = open_live(pw, engine, live)
    pg.evaluate("() => { window.__hidden = true; document.dispatchEvent(new Event('visibilitychange')); }")
    api.clear()
    pg.clock.run_for(CHECK_MS * 5)
    pg.wait_for_timeout(300)
    assert api == [], api
    # Back in view after more than the interval: one check, right away.
    pg.evaluate("() => { window.__hidden = false; document.dispatchEvent(new Event('visibilitychange')); }")
    pg.clock.run_for(50)
    pg.wait_for_timeout(300)
    assert len(api) == 1 and "/api/char" in api[0], api
    b.close()
