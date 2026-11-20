"""The 3D viewer on a phone (WebKit as Playwright's "iPhone 15"), and opened
behind another tab.

v0.6.0 on the owner's iPhone (2026-11-20): "Couldn't draw the model:
undefined.", twice, on the full view only. 3D was remembered from an
earlier visit (localStorage), and the full view opens on Home: the frame
was put in the hidden Character tab, where it has no size. Wowhead's viewer
took its aspect from 0/0 and threw a bare string ("Bad aspect ratio
given"), which has no .message. The public page has no Home tab, so it
drew. Chromium fails the same way; the phone only made it likely.

Opt-in (WOW_STATS_BROWSER=1), like tests/test_model3d_sandbox_browser.py,
whose site it reuses.
"""

import os
import time

import pytest

from tests.test_model3d_sandbox_browser import site  # noqa: F401 - the fixture

pytestmark = pytest.mark.skipif(os.environ.get("WOW_STATS_BROWSER") != "1",
                                reason="opt-in: WOW_STATS_BROWSER=1 (network and a browser)")


@pytest.mark.parametrize("view", ["full", "zogzog"])
def test_the_viewer_draws_on_an_iphone(site, view):  # noqa: F811
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    logs = []
    with sync_playwright() as p:
        b = p.webkit.launch()
        ctx = b.new_context(**p.devices["iPhone 15"])
        page = ctx.new_page()
        page.on("console", lambda m: logs.append(m.text[:300]))
        page.on("pageerror", lambda e: logs.append("pageerror: " + str(e)[:300]))
        page.on("response", lambda r: logs.append(f"{r.status} {r.url[-80:]}") if "/model3d/" in r.url else None)
        page.goto(site[view], wait_until="networkidle")
        btn = page.locator("#gear button.m3dtoggle").first
        btn.wait_for(timeout=30000)
        btn.scroll_into_view_if_needed()
        btn.click()
        drawn = ""
        deadline = time.time() + 90
        while time.time() < deadline:
            el = page.query_selector("iframe.m3dframe")
            frame = el.content_frame() if el else None
            if frame is not None:
                drawn = frame.evaluate("() => { const s = document.getElementById('status');"
                                       " if (s && s.classList.contains('err')) return 'error: ' + s.textContent;"
                                       " return s && s.hidden && document.querySelector('#stage canvas') ? 'drawn' : '' }")
                if drawn:
                    break
            time.sleep(0.5)
        under = page.evaluate("() => (document.querySelector('.m3dstatus') || {}).textContent || ''")
        b.close()
    assert drawn == "drawn", (drawn, under, logs[-25:])
    assert "Couldn" not in under, under


@pytest.mark.parametrize("engine", ["webkit", "chromium"])
def test_remembered_3d_opens_behind_another_tab(site, engine):  # noqa: F811
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    logs = []
    with sync_playwright() as p:
        b = getattr(p, engine).launch()
        ctx = b.new_context(**p.devices["iPhone 15"]) if engine == "webkit" else b.new_context()
        ctx.add_init_script("try { localStorage.setItem('wow-view-mode:full', '3d') } catch (e) {}")
        page = ctx.new_page()
        page.on("console", lambda m: logs.append(m.text[:300]))
        page.goto(site["full"].replace("?tab=overview", ""), wait_until="networkidle")
        time.sleep(3)
        page.click("#tab-overview")
        drawn = ""
        deadline = time.time() + 90
        while time.time() < deadline:
            el = page.query_selector("iframe.m3dframe")
            frame = el.content_frame() if el else None
            if frame is not None:
                drawn = frame.evaluate("() => { const s = document.getElementById('status');"
                                       " if (s && s.classList.contains('err')) return 'error: ' + s.textContent;"
                                       " return s && s.hidden && document.querySelector('#stage canvas') ? 'drawn' : '' }")
                if drawn:
                    break
            time.sleep(0.5)
        under = page.evaluate("() => (document.querySelector('.m3dstatus') || {}).textContent || ''")
        b.close()
    assert drawn == "drawn", (drawn, under, logs[-15:])
