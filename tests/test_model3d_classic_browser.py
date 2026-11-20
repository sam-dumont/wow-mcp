"""Classic models and a hairstyle they don't have, in a real browser.

The test gnome (tests/test_model3d_browser.py LOOK) wears "Genius", the
updated models' 12th style: classic gnome males have 7, so Classic draws
the last of them. Under Classic, one line says so; under Updated, nothing.
The screenshots (light and dark) are left in the test's tmp_path.

Opt-in (WOW_STATS_BROWSER=1), like tests/test_model3d_sandbox_browser.py,
whose site it reuses.
"""

import os
import time

import pytest

from tests.test_model3d_sandbox_browser import site  # noqa: F401 - the fixture

pytestmark = pytest.mark.skipif(os.environ.get("WOW_STATS_BROWSER") != "1",
                                reason="opt-in: WOW_STATS_BROWSER=1 (network and a browser)")

NOTE = "This hairstyle doesn’t exist on classic models: switch to Updated to see it."
DRAWN = ("() => { const s = document.getElementById('status');"
         " if (s && s.classList.contains('err')) return 'error: ' + s.textContent;"
         " return s && s.hidden && document.querySelector('#stage canvas') ? 'drawn' : '' }")


def drawn(page):
    deadline = time.time() + 90
    while time.time() < deadline:
        el = page.query_selector("iframe.m3dframe")
        frame = el.content_frame() if el else None
        if frame is not None:
            got = frame.evaluate(DRAWN)
            if got:
                return got
        time.sleep(0.5)
    return ""


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_classic_says_the_hairstyle_is_missing(site, scheme, tmp_path):  # noqa: F811
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(color_scheme=scheme, viewport={"width": 1280, "height": 1000})
        ctx.add_init_script(f"try {{ localStorage.setItem('wow-theme', '{scheme}') }} catch (e) {{}}")
        page = ctx.new_page()
        page.goto(site["full"], wait_until="networkidle")
        page.locator("#gear button.m3dtoggle").first.click()
        assert drawn(page) == "drawn"
        note = page.locator(".m3dclassic")
        assert note.count() == 1 and not note.is_visible()  # Updated: nothing to say
        page.get_by_role("button", name="Classic").click()
        assert note.is_visible() and note.text_content() == NOTE
        time.sleep(1)  # the frame reloads on its new address
        assert drawn(page) == "drawn"
        time.sleep(1)  # the frame's first frames after the reload
        top, bottom = page.locator("#gear").bounding_box(), page.locator(".m3dinfo").bounding_box()
        y = page.evaluate("scrollY")  # the boxes are in the viewport, the clip in the page
        page.screenshot(path=str(tmp_path / f"classic-hair-note-{scheme}.png"), full_page=True,
                        clip={"x": top["x"], "y": top["y"] + y, "width": top["width"],
                              "height": bottom["y"] + bottom["height"] - top["y"] + 8})
        page.get_by_role("button", name="Updated").click()
        assert not note.is_visible()
        b.close()
