"""The demo's made-up character (tools/fixtures/golden3d.py) through the
whole 3D path: WDC5 tables, the assembly, the M3D viewer, in Chromium and
WebKit. The same page as tests/test_m3d_viewer_browser.py (the viewer's
scripts under a strict policy, the files at /f/<id>), read back at points
of the model projected to the screen.

Colours are checked by family, not value: the viewer lights the model
(front, top and right, with a specular term), so a violet robe stays violet
but not one RGB. Heavy: run through ~/.local/bin/wow-heavy."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_golden3d import OUTFIT, golden3d
from tests.test_m3d_viewer_browser import page_html
from wowstats import assets, character3d

pytest.importorskip("playwright.sync_api")

W, H = 400, 600


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    root = tmp_path_factory.mktemp("golden")
    golden3d.write_store(root)
    store = assets.Store(root)
    tables = character3d.load_tables(store)
    a = character3d.assemble(tables, golden3d.GNOME, golden3d.MALE, 5, golden3d.build()["look"], OUTFIT)
    assert a["missing"] == []
    html, policy = page_html(a["recipe"], W, H)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/":
                self._send(200, html.encode(), "text/html; charset=utf-8", policy)
                return
            got = store.file(int(self.path[3:])) if self.path.startswith("/f/") and self.path[3:].isdigit() else None
            self._send(200, got[0], "application/octet-stream") if got else self._send(404, b"", "text/plain")

        def _send(self, code, body, ctype, csp=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if csp:
                self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture(scope="module", params=["chromium", "webkit"])
def browser(request):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            b = getattr(p, request.param).launch()
        except Exception as e:  # noqa: BLE001 - Playwright's own error: the browser isn't installed
            pytest.skip(f"{request.param}: {e}")
        yield b
        b.close()


def violet(px):
    return px[0] > px[1] + 25 and px[2] > px[1] + 25


def ginger(px):
    return px[0] > px[2] + 60 and px[0] > px[1]


def blue(px):
    return px[2] > px[0] + 40


def opaque(px):
    return px[3] > 235


# Points of the model (WoW axes: x forward, y the character's left, z up)
# and what must be drawn there.
POINTS = [
    ((0.12, 0.0, 0.3), violet, "the robe's skirt (Violet Sorcerer's Robes)"),
    ((0.17, -0.06, 0.7), ginger, "the beard, in the look's hair colour"),
    ((0.12, 0.18, 0.95), blue, "the Azure Silk Hood's left side, on the head attachment"),
    ((0.12, -0.18, 0.95), blue, "the hood's right side"),
    ((0.08, -0.22, 0.45), opaque, "the scepter in the right hand (slot 16, attachment 1)"),
    ((0.078, 0.22, 0.45), opaque, "the rod in the left hand (slot 17, attachment 2)"),
    ((0.13, 0.08, 0.04), opaque, "the left boot"),
]
CLEAR = [((0.0, 0.6, 0.7), "beside the left arm"), ((0.0, -0.6, 0.7), "beside the right arm"),
         ((0.0, 0.0, 1.8), "above the head")]


def test_golden_character_draws(browser, drawn):
    page = browser.new_page(viewport={"width": W + 40, "height": H + 40})
    logs = []
    page.on("pageerror", lambda e: logs.append(str(e)[:300]))
    page.goto(drawn)
    assert page.evaluate("() => window.__ready") == "ok", logs
    assert page.evaluate("() => window.__h.missing || []") == []
    page.evaluate("window.__at(0)")
    got = page.evaluate("(p) => window.__sample(p)", [list(p) for p, _f, _w in POINTS] + [list(p) for p, _w in CLEAR])
    bad = [f"{why}: {px}" for (_p, test, why), px in zip(POINTS, got, strict=False) if not (px and test(px))]
    bad += [f"{why}: {px}" for (_p, why), px in zip(CLEAR, got[len(POINTS):], strict=True) if not (px and px[3] < 20)]
    # The silhouette: the character stands in the canvas, taller than wide,
    # filling a fair part of it.
    box = page.evaluate("""() => {
      const c = document.getElementById('c'), k = document.createElement('canvas');
      k.width = c.width; k.height = c.height;
      const g = k.getContext('2d'); g.drawImage(c, 0, 0);
      const d = g.getImageData(0, 0, k.width, k.height).data;
      let x0 = k.width, y0 = k.height, x1 = -1, y1 = -1, n = 0;
      for (let y = 0; y < k.height; y++) for (let x = 0; x < k.width; x++) {
        if (d[(y * k.width + x) * 4 + 3] > 16) { n++; if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
      }
      return { x0, y0, x1, y1, n, w: k.width, h: k.height };
    }""")
    page.close()
    assert not bad, bad
    assert box["x1"] > box["x0"] and box["y1"] - box["y0"] > box["x1"] - box["x0"], box
    assert box["y1"] - box["y0"] > 0.5 * box["h"], box
    assert 0.08 < box["n"] / (box["w"] * box["h"]) < 0.6, box
