"""/help/ and the 404 page on the full view, in a real browser (Chromium and
WebKit, Playwright), at 1920 and 390 px, as a logged-in friend opens them
from the nav or from a link to one question.

Needs the `browser` group (`uv run --group browser playwright install
chromium webkit` once). WOW_SHOTS=<dir> saves screenshots, light and dark."""

import os
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests import test_start_browser  # noqa: E402
from tests.test_start_browser import STATES  # noqa: E402

site, pw = test_start_browser.site, test_start_browser.pw  # the friends' full view, and Playwright

OVER = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
WIDE = """() => [...document.querySelectorAll('#help p, #help li, #help dd, #help pre, #help h3')]
  .filter((e) => e.offsetParent && e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent.slice(0, 60))"""
IN_VIEW = """(s) => { const r = document.querySelector(s).getBoundingClientRect();
  return r.top >= -2 && r.top < innerHeight; }"""


def browser(pw, engine):
    try:
        return getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")


def context(b, width, scheme="light"):
    return b.new_context(viewport={"width": width, "height": 844 if width < 600 else 1080}, color_scheme=scheme,
                         extra_http_headers={"X-Forwarded-User": STATES["ok"]},
                         device_scale_factor=2 if width < 600 else 1)


def shoot(pg, name):
    out = os.environ.get("WOW_SHOTS")
    if not out:
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    pg.screenshot(path=f"{out}/{name}-full.png", full_page=True)
    pg.screenshot(path=f"{out}/{name}-first.png")


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_help_on_the_full_view(site, pw, engine):
    b = browser(pw, engine)
    schemes = ("light", "dark") if os.environ.get("WOW_SHOTS") else ("light",)
    for width in (390, 1920):
        for scheme in schemes:
            c = context(b, width, scheme)
            pg = c.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            r = pg.goto(f"{site}/help")  # the nav's link, or a typed address
            assert r.status == 200 and pg.url.endswith("/help/")
            pg.wait_for_selector(".ask")
            assert pg.locator(".bar-nav a[aria-current=page]").inner_text() == "Help"
            assert pg.locator("#hnav").is_visible()
            assert pg.evaluate(OVER) <= 0, (engine, width)
            assert pg.evaluate(WIDE) == [], (engine, width)
            # The clips are the start page's own, in the page's theme.
            pg.locator("#start figure.media").scroll_into_view_if_needed()
            pg.wait_for_selector("#start figure.media video")
            src = pg.locator("#start figure.media video").get_attribute("src")
            assert src.startswith(f"/start/media/onboarding-{scheme}.mp4"), src
            pg.evaluate("() => scrollTo(0, 0)")
            shoot(pg, f"help-{engine}-{width}-{scheme}")
            # Your AI: pick an app, its snippet carries the full view's own address.
            pg.locator("select[data-sel=client]").select_option(label="Claude Code")
            assert "claude mcp add --transport http wow https://wow-full.m.example/mcp" in pg.locator(
                "body").inner_text()
            # The header's jumps: the fragment is the target here, and stays shareable.
            pg.locator("#hnav a[data-jump=trouble]").click()
            pg.wait_for_function("() => location.hash === '#trouble'")
            assert not errors, errors
            c.close()
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("anchor", ["tr-no-data", "x-update", "ai"])
def test_an_anchor_from_another_page_lands_open(site, pw, engine, anchor):
    """/help/#<id> lands on that part, the folds around it opened."""
    b = browser(pw, engine)
    c = context(b, 390)
    pg = c.new_page()
    pg.goto(f"{site}/help/#{anchor}")
    pg.wait_for_selector(".ask")
    pg.wait_for_function(f"() => {{ const r = document.getElementById('{anchor}').getBoundingClientRect();"
                         " return r.top >= -2 && r.top < innerHeight; }")
    if pg.locator(f"#{anchor}").evaluate("(e) => e.tagName") == "DETAILS":
        assert pg.locator(f"#{anchor}").evaluate("(e) => e.open")
        assert pg.locator(f"#{anchor} > :not(summary)").first.is_visible()
    # A later link on the same page moves there too.
    pg.evaluate("() => { location.hash = '#start'; }")
    pg.wait_for_function(IN_VIEW, arg="#start")
    c.close()
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_not_found_is_a_page_with_the_way_back(site, pw, engine):
    b = browser(pw, engine)
    schemes = ("light", "dark") if os.environ.get("WOW_SHOTS") else ("light",)
    for width in (390, 1920):
        for scheme in schemes:
            c = context(b, width, scheme)
            pg = c.new_page()
            r = pg.goto(f"{site}/no-such-page")
            assert r.status == 404
            assert pg.locator("h1").inner_text() == "Nothing here"
            assert pg.locator("main a.btn[href='/']").inner_text() == "My page"
            for name, href in (("My page", "/"), ("Start", "/start"), ("Help", "/help/")):
                assert pg.locator(f".bar-nav a[href='{href}']").inner_text() == name
            assert pg.locator(".bar-nav a[href='/help/']").is_visible()
            assert pg.evaluate(OVER) <= 0
            # Styled: the page's own fonts and colours loaded.
            assert "Cinzel" in pg.locator("h1").evaluate("(e) => getComputedStyle(e).fontFamily")
            shoot(pg, f"notfound-{engine}-{width}-{scheme}")
            pg.locator(".bar-nav a[href='/help/']").click()
            pg.wait_for_selector(".ask")
            c.close()
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_the_addon_list_on_help(site, pw, engine):
    """The optional addons, one step each, WoWSims Exporter among them: the
    same list as /start and the docs (tests/test_addon_lists.py)."""
    from wowstats import account

    b = browser(pw, engine)
    for width, scheme in ((390, "light"), (390, "dark"), (1920, "light"), (1920, "dark")):
        c = context(b, width, scheme)
        pg = c.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"{site}/help/")
        pg.wait_for_selector("#q-addons")
        pg.locator("#q-addons details.hopt summary").click()
        opt = [a for a in account.addons() if not a["required"]]
        items = pg.locator("#q-addons details.hopt li")
        assert items.count() == len(opt)
        for a, li in zip(opt, items.all(), strict=True):
            assert li.inner_text() == f"{a['name']} {a['what']}. {a['step']}"
        assert pg.evaluate(OVER) <= 0 and not errors
        out = os.environ.get("WOW_SHOTS")
        if out:
            Path(out).mkdir(parents=True, exist_ok=True)
            pg.locator("#q-addons").scroll_into_view_if_needed()
            pg.locator("#q-addons").screenshot(path=f"{out}/help-addons-{engine}-{width}-{scheme}.png")
        c.close()
    b.close()
