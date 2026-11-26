"""Check the full dashboard when dev/seed.py sends the nine demo histories to /ingest.

This test runs the UI against a VictoriaLogs stand-in. It leaves out VictoriaMetrics,
the client model store and the portrait renderer so it can check exactly what a player
sees with event data alone. KIT_LACKS lists the expected messages for those inputs.
The image CI smoke runs the Compose overlay with the real model store. The hosted
browser suite checks both published pages with models and portraits.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests import degraded as D  # noqa: E402
from tests.test_browser import settled  # noqa: E402
from tests.test_demo_complete_browser import ENGINES, FALLBACK, WIDTHS, gaps, icon_gaps  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "demo-password-only"
# The page's own lines for the parts the kit doesn't run, word for word.
# Battle.net: the exporter and its VictoriaMetrics need your own API client.
# The 3D view: drawn from the game files a player's computer sends.
KIT_LACKS = (
    "No talents yet. They show once Battle.net has read this character: it reads every 2 hours.",
    "Talents: not known yet",
    "Talents\n–\nnot known yet",  # the class meta card's row
    "Legacy points: unknown until Battle.net reads this account.",
    "No 3D model yet: the shipper sends the game files from your computer on its next run.",
)


def kit_gaps(page) -> list[str]:
    """gaps() without the kit's lines and the pictures' silhouettes (the
    renderer draws the pictures, from the same game files): the page's text
    once those lines are taken out still has to be free of every fallback
    phrase."""
    out = [g for g in gaps(page) if not (g.startswith("empty: ") and g[len("empty: "):] in KIT_LACKS)
           and not g.startswith(("text: ", "silhouette: "))]
    body = page.inner_text("body")
    for line in KIT_LACKS:
        body = body.replace(line, "")
    return out + [f"text: {f}" for f in FALLBACK if f.lower() in body.lower()]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - the test's own ui
        return r.read()


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """The ui in its own process, as the kit runs it: the suite turns the
    item tables off in this one (conftest.py, no_item_tables), the kit
    doesn't."""
    home = tmp_path_factory.mktemp("local-demo")
    store = D.VL([])
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "VIEW": "full", "AUTH": "open", "BIND": "127.0.0.1", "PORT": str(port),
           "VL_URL": store.url, "INGEST_PASSWORD": PASSWORD, "MODEL3D": "on", "WOW_ITEMS": "on",
           "HOME": str(home), "ANALYSIS_STATE_DIR": str(home / "analysis"), "MCP_STATE_DIR": ""}
    log = (home / "ui.log").open("wb")
    proc = subprocess.Popen([sys.executable, "-m", "wowstats", "ui"], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        end = time.monotonic() + 60
        while time.monotonic() < end:
            with contextlib.suppress(OSError):
                get(url + "/healthz")
                break
            time.sleep(0.5)
        r = subprocess.run([sys.executable, str(ROOT / "dev" / "seed.py"), "--ingest", url + "/ingest"],
                           capture_output=True, text=True, timeout=300, env={**os.environ, "INGEST_PASSWORD": PASSWORD})
        assert r.returncode == 0, r.stderr[-3000:]
        end = time.monotonic() + 120
        while time.monotonic() < end and b"Nibbet-Gearholt" not in get(url + "/api/chars"):
            time.sleep(0.5)
        yield url
    finally:
        proc.terminate()
        proc.wait(30)
        log.close()
        store.close()


@pytest.fixture(scope="module", params=ENGINES)
def browser(request):
    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, request.param).launch()
        except Exception as e:  # noqa: BLE001 - Playwright's own error: the browser isn't installed
            pytest.skip(f"{request.param}: {e}")
        yield b
        b.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_local_demo_has_no_gap_but_what_the_kit_lacks(browser, base, width):
    ctx = browser.new_context(viewport={"width": width, "height": 844 if width < 600 else 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: None if "ResizeObserver loop" in str(e) else errors.append(str(e)))
    page.goto(base + "/")
    page.wait_for_selector(".tabs [role=tab]")
    page.wait_for_selector("#char option:nth-child(9)", state="attached")
    assert page.locator("#char").input_value() == "Nibbet-Gearholt"
    assert page.locator("#char option").count() == 9
    settled(page, timeout=90)
    assert "Nibbet" in page.inner_text("body")
    found: dict[str, list[str]] = {}
    tabs = page.eval_on_selector_all(".tabs [role=tab]", "els => els.map(e => e.id)")
    assert len(tabs) >= 4, tabs
    for tab in tabs:
        page.click(f"#{tab}")
        settled(page, timeout=90)
        # The live UI loads its first item icons on demand.
        end = time.monotonic() + 90
        while (icons := icon_gaps(page)) and time.monotonic() < end:
            page.wait_for_timeout(2000)
        got = kit_gaps(page) + icons
        if got:
            found[tab] = sorted(set(got))
    ctx.close()
    assert not errors, errors
    assert not found, found
