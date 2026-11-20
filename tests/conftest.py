import contextlib
import importlib.util
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Fixtures only: the shipper (and every subprocess a test starts) refuses a
# WoW folder outside a temp directory, never the real install of the machine
# running the tests (2026-11-20).
os.environ["NO_REAL_WOW"] = "1"


@pytest.fixture(scope="session", autouse=True)
def sealed_names():
    """Every sealed /zogzog/ file's keyed name, kept with its path
    (tests/sealed.py), so a test can open a bundle by path."""
    from tests import sealed

    sealed.record()


@pytest.fixture(autouse=True)
def wow_folder_is_a_fixture():
    yield
    mod = sys.modules.get("wow_stats_shipper")
    if mod is not None:
        assert mod.SANDBOX and mod.in_temp(mod.WOW), f"a test pointed the shipper at {mod.WOW}"


@pytest.fixture(autouse=True)
def no_item_tables(monkeypatch):
    """The loot history's item table comes from wago.tools: never in tests."""
    from wowstats import items, talents

    monkeypatch.setenv("WOW_ITEMS", "off")
    monkeypatch.setattr(items, "_shared", None)
    monkeypatch.setattr(talents, "_shared", None)
    monkeypatch.setattr(talents, "_icons", {})


@pytest.fixture(autouse=True)
def close_browsers_a_test_opened():
    """A browser a test launches is closed when the test ends, failed or not.
    The browser tests launch one per test and close it on their last line,
    which an assertion before it skips: a run with many failures left 71
    Chromium and dozens of WebKit processes open on the owner's Mac
    (2026-11-20). Module-scoped browsers are launched before this and are
    left to their own fixture."""
    try:
        from playwright.sync_api import BrowserType
    except ImportError:
        yield
        return
    opened = []
    launch = BrowserType.launch

    def tracked(self, *a, **k):
        b = launch(self, *a, **k)
        opened.append(b)
        return b

    BrowserType.launch = tracked
    try:
        yield
    finally:
        BrowserType.launch = launch
        for b in opened:
            with contextlib.suppress(Exception):  # already closed, or its driver is gone
                b.close()


@pytest.fixture(scope="session")
def shipper():
    """shipper/wow_stats.py is a standalone script, not part of the package."""
    spec = importlib.util.spec_from_file_location("wow_stats_shipper", ROOT / "shipper" / "wow_stats.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wow_stats_shipper"] = mod
    spec.loader.exec_module(mod)
    return mod


class Running:
    def __init__(self, app):
        from wowstats import ui

        app.port = 0
        self.srv = ui.serve(app)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def get(self, path, headers=None, method="GET", data=None):
        """(status, headers, body) without following redirects."""

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(self.base + path, headers=headers or {}, method=method, data=data)
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def run_app():
    started = []

    def start(app):
        r = Running(app)
        started.append(r)
        return r

    yield start
    for r in started:
        r.close()
