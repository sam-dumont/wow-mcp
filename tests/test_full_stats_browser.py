"""Plausible's own manual script on the live full view, in Chromium: one
pageview per page, its address the path alone (never the query or the
#fragment), the same data-domain as the static pages, and every request on
the full view's own address. Skips without Playwright, like
tests/test_browser.py."""

import json
import re
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_public_view import fake_extras, make  # noqa: E402, F401 - fake_extras is a fixture

FIXTURES = Path(__file__).parent / "fixtures"
DOMAIN = "wow.test"


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browsers not installed
            pytest.skip(f"no Chromium: {e}")
        yield b
        b.close()


@pytest.mark.usefixtures("fake_extras")
def test_the_full_view_sends_its_path_alone(run_app, browser):
    # *.localhost reaches 127.0.0.1, and Plausible's script ignores
    # 127.0.0.1 and localhost themselves.
    srv = run_app(make("full", PLAUSIBLE_DOMAIN=DOMAIN, MODEL3D="off", ALLOWED_HOSTS="wow-full.localhost"))
    base = srv.base.replace("127.0.0.1", "wow-full.localhost")
    c = browser.new_context()
    c.add_init_script("window.__plausible = true")  # Playwright sets navigator.webdriver
    script = (FIXTURES / "plausible-script.manual.js").read_text()
    c.route(re.compile(r".*/js/script\.js$"), lambda r: r.fulfill(status=200, body=script,
                                                                  content_type="application/javascript"))
    events, seen = [], []

    def event(route):
        req = route.request
        events.append({"body": json.loads(req.post_data), "url": req.url, "headers": req.headers})
        route.fulfill(status=202, body="ok")

    c.route(re.compile(r".*/api/event$"), event)
    pg = c.new_page()
    pg.on("request", lambda r: seen.append(r.url))
    pg.goto(f"{base}/?char=Hero-Test&tab=gear#gear")
    for _ in range(50):
        if events:
            break
        pg.wait_for_timeout(100)
    pg.wait_for_timeout(300)
    views = [e for e in events if e["body"].get("n") == "pageview"]
    assert len(views) == 1, events
    body = views[0]["body"]
    assert body["u"] == base + "/" and body["d"] == DOMAIN, body
    assert not body.get("h"), "no hash tracking"
    blob = json.dumps(events)
    assert "Hero-Test" not in blob and "tab=" not in blob and "#gear" not in blob
    assert all(e["url"] == base + "/api/event" for e in events)
    assert base + "/privacy/stats.js" in seen
    assert all(u.startswith(base + "/") or u.startswith("data:") for u in seen), [
        u for u in seen if not u.startswith(base)]
    c.close()
