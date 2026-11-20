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
    from wowstats import itemcard, items, talents

    monkeypatch.setenv("WOW_ITEMS", "off")
    # The item catalog too: one made by a module-scoped fixture, before this
    # one turns WOW_ITEMS off, stayed on for the rest of the worker.
    monkeypatch.setattr(itemcard, "_shared", None)
    monkeypatch.setattr(items, "_shared", None)
    monkeypatch.setattr(talents, "_shared", None)
    monkeypatch.setattr(talents, "_icons", {})


class _SharedBrowser:
    """What a test's `launch()` returns: the module's one real browser, whose
    close() does nothing (the module's teardown closes it). Launching a
    Chromium per test was most of the browser suites' time (2026-11-20)."""

    def __init__(self, real):
        self._real = real

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture(autouse=True, scope="module")
def one_browser_per_module():
    """Tests launch browsers as they like (one per test, closed on their last
    line, which an assertion before it skips: 71 Chromium and dozens of WebKit
    left open on the owner's Mac, 2026-11-20). Behind it, each Playwright
    instance (one per module) launches each engine once, and every module
    closes what it opened, failed tests included."""
    try:
        from playwright.sync_api import BrowserType
    except ImportError:
        yield
        return
    opened: dict = {}
    launch = BrowserType.launch

    def shared(self, *a, **k):
        key = (id(self), repr(a), repr(sorted(k.items())))
        if key not in opened:
            opened[key] = launch(self, *a, **k)
        return _SharedBrowser(opened[key])

    BrowserType.launch = shared
    try:
        yield
    finally:
        BrowserType.launch = launch
        for b in opened.values():
            with contextlib.suppress(Exception):  # its driver may already be stopped
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


# Need docker compose, a Polestar checkout or Windows: CI's jobs and the release run them.
CI_ONLY = {"test_compose.py", "test_windows_e2e.py", "test_gear_parity.py"}


def pytest_collection_modifyitems(items):
    """Every test in a *browser* file carries the `browser` marker, so the
    default run (pyproject's `-m "not browser and not ci_only"`) is the fast
    suite and `-m browser` the heavy one (tools/test_changed.py picks between
    them from the diff)."""
    for item in items:
        name = Path(str(item.fspath)).name
        if "browser" in name:
            item.add_marker(pytest.mark.browser)
        if name in CI_ONLY:
            item.add_marker(pytest.mark.ci_only)
