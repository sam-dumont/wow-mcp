"""The images' HEALTHCHECK (wowstats/healthcheck.py): which mode, which port."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from wowstats import healthcheck as hc


@pytest.mark.parametrize(("cmdline", "mode"), [
    (b"/app/.venv/bin/python\0/app/.venv/bin/wow-stats\0ui\0", "ui"),
    (b"/app/.venv/bin/python\0/app/.venv/bin/wow-stats\0publish\0--loop\0", "publish"),
    (b"wow-stats\0render\0", "render"),
    (b"/app/.venv/bin/wow-stats\0--version\0", None),
    (b"/bin/sh\0-c\0sleep 1\0", None),
])
def test_mode(cmdline, mode):
    assert hc.mode(cmdline) == mode


def test_url_per_mode():
    assert hc.url("ui", {"BIND": "0.0.0.0", "PORT": "8080"}) == "http://127.0.0.1:8080/healthz"
    assert hc.url("render", {}) == "http://127.0.0.1:8083/healthz"
    assert hc.url("exporter", {"PORT": "8080"}) == "http://127.0.0.1:9464/healthz"
    assert hc.url("exporter", {"LISTEN_PORT": "9999"}) == "http://127.0.0.1:9999/healthz"
    assert hc.url("static", {"BIND": "::", "PORT": "8081"}) == "http://127.0.0.1:8081/healthz"
    assert hc.url("ui", {"BIND": "192.0.2.5", "PORT": "8080"}) == "http://192.0.2.5:8080/healthz"
    for m in ("publish", "analyse", "zogzog-key", "mcp-stdio"):
        assert hc.url(m, {}) is None


def test_check_against_a_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/healthz" else 500)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        assert hc.check(f"http://127.0.0.1:{port}/healthz") == 0
        assert hc.check(f"http://127.0.0.1:{port}/other") == 1
    finally:
        srv.shutdown()
    assert hc.check(f"http://127.0.0.1:{port}/healthz") == 1  # nothing listening
