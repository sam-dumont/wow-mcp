import importlib.util
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_item_tables(monkeypatch):
    """The loot history's item table comes from wago.tools: never in tests."""
    from wowstats import items, talents

    monkeypatch.setenv("WOW_ITEMS", "off")
    monkeypatch.setattr(items, "_shared", None)
    monkeypatch.setattr(talents, "_shared", None)
    monkeypatch.setattr(talents, "_icons", {})


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
