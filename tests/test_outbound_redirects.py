"""Outbound fetches follow a redirect only to where they were allowed to go
(2026-11-20 server review, info 4: urllib followed one anywhere)."""

from __future__ import annotations

import http.server
import threading

import pytest

from wowstats import armory, upstream


def serve(routes: dict[str, tuple[int, dict, bytes]], hits: list):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            code, headers, body = routes.get(self.path, (404, {}, b""))
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def two_hosts():
    """`home` redirects to itself (/same) and to `other` (/off)."""
    other_hits: list = []
    other, other_url = serve({"/secret": (200, {}, b"\x89PNG\r\n\x1a\nfrom elsewhere")}, other_hits)
    home_hits: list = []
    home, home_url = serve({"/same": (302, {"Location": "/ok"}, b""),
                            "/off": (302, {"Location": f"{other_url}/secret"}, b""),
                            "/ok": (200, {}, b"\x89PNG\r\n\x1a\nhere")}, home_hits)
    yield home_url, other_hits
    for s in (home, other):
        s.shutdown()
        s.server_close()


def test_upstream_follows_redirects_on_its_own_host_only(two_hosts, monkeypatch):
    home, other_hits = two_hosts
    monkeypatch.setattr(upstream, "ALLOWED", set(upstream.ALLOWED))
    upstream.allow(home)  # the test's server, as config would; `other` stays off the list
    assert upstream.fetch(f"{home}/same") == b"\x89PNG\r\n\x1a\nhere"
    with pytest.raises(upstream.Upstream, match="answered 302"):
        upstream.fetch(f"{home}/off")
    assert other_hits == []


def test_render_from_follows_redirects_on_its_own_site_only(two_hosts):
    """RENDER_FROM's static site: the same site only."""
    home, other_hits = two_hosts
    assert armory._read(f"{home}/same") == b"\x89PNG\r\n\x1a\nhere"
    assert armory._read(f"{home}/off") is None
    assert other_hits == []
