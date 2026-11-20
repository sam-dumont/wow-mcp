"""Draw a recipe with our viewer (web/model3d/m3d) in headless Chromium,
on the standing animation's first frame, turned to a given angle, and
hand back the picture scaled so the body is as tall as the reference's.

One local page per recipe: the m3d scripts inline, the files at /f/<id>
from a dict (read from the player's own install by run.py), the
reference at /ref.png. Nothing leaves the machine.
"""

from __future__ import annotations

import base64
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from imgcmp import Img

ROOT = Path(__file__).resolve().parents[2]
M3D = ROOT / "web" / "model3d" / "m3d"
FACING = -math.pi / 2  # draw.js: the model facing the camera
CANVAS = (600, 800)

PAGE = r"""
'use strict';
const canvas = document.getElementById('c');
canvas.style.width = canvas.dataset.w + 'px';
canvas.style.height = canvas.dataset.h + 'px';
const recipe = JSON.parse(document.getElementById('recipe').textContent);
const get = (id) => fetch('/f/' + id).then((r) => (r.ok ? r.arrayBuffer() : null));
// the clock stays at 0: the Stand animation's first frame
window.__ready = M3D.draw(canvas, recipe, get, { clock: () => 0, maxPixelRatio: 1 }).then(
	(h) => { window.__h = h; return 'ok'; },
	(e) => { window.__err = String((e && e.stack) || e); return window.__err; });
const frames = (n) => new Promise((res) => {
	const f = () => (--n <= 0 ? res() : requestAnimationFrame(f));
	requestAnimationFrame(f);
});
// RGBA of a canvas region as base64
function grab(src, sx, sy, sw, sh, dw, dh) {
	const k = document.createElement('canvas');
	k.width = dw; k.height = dh;
	const g = k.getContext('2d');
	g.imageSmoothingEnabled = true;
	g.imageSmoothingQuality = 'high';
	g.drawImage(src, sx, sy, sw, sh, 0, 0, dw, dh);
	const d = g.getImageData(0, 0, dw, dh).data;
	let s = '';
	for (let i = 0; i < d.length; i += 0x8000)
		s += String.fromCharCode.apply(null, d.subarray(i, i + 0x8000));
	return btoa(s);
}
// the opaque box of a drawable, and the body's top and bottom rows (3 px or more)
function box(src, w, h) {
	const k = document.createElement('canvas');
	k.width = w; k.height = h;
	const g = k.getContext('2d');
	g.drawImage(src, 0, 0);
	const d = g.getImageData(0, 0, w, h).data;
	let x0 = w, y0 = h, x1 = -1, y1 = -1, top = -1, bottom = -1;
	for (let y = 0; y < h; y++) {
		let n = 0;
		for (let x = 0; x < w; x++) {
			if (d[(y * w + x) * 4 + 3] >= 40) {
				n++;
				if (x < x0) x0 = x;
				if (x > x1) x1 = x;
			}
		}
		if (n) { if (y < y0) y0 = y; y1 = y; }
		if (n >= 3) { if (top < 0) top = y; bottom = y; }
	}
	return { x0, y0, x1, y1, top, bottom };
}
// `grid`: the scale and crop of an earlier shot, to draw on the same pixels
window.__shot = async (rad, target, grid) => {
	window.__h.debug.turn(rad);
	await frames(4);
	let g = grid;
	if (!g) {
		const b = box(canvas, canvas.width, canvas.height);
		if (b.x1 < 0 || b.bottom <= b.top) return null;
		const s = target / (b.bottom - b.top);
		const pad = 2;
		const sx = Math.max(0, b.x0 - pad), sy = Math.max(0, b.y0 - pad);
		const sw = Math.min(canvas.width, b.x1 + pad + 1) - sx, sh = Math.min(canvas.height, b.y1 + pad + 1) - sy;
		g = { sx, sy, sw, sh, dw: Math.max(1, Math.round(sw * s)), dh: Math.max(1, Math.round(sh * s)) };
	}
	return { w: g.dw, h: g.dh, grid: g, rgba: grab(canvas, g.sx, g.sy, g.sw, g.sh, g.dw, g.dh) };
};
window.__ref = async () => {
	const img = new Image();
	img.src = '/ref.png';
	await img.decode();
	const b = box(img, img.naturalWidth, img.naturalHeight);
	if (b.x1 < 0) return null;
	const pad = 60;
	const sx = Math.max(0, b.x0 - pad), sy = Math.max(0, b.y0 - pad);
	const sw = Math.min(img.naturalWidth, b.x1 + pad + 1) - sx, sh = Math.min(img.naturalHeight, b.y1 + pad + 1) - sy;
	return { w: sw, h: sh, body: b.bottom - b.top, rgba: grab(img, sx, sy, sw, sh, sw, sh) };
};
"""


def page_html(recipe: dict) -> str:
    names = [n.strip() for n in (M3D / "ORDER.txt").read_text().splitlines() if n.strip()]
    scripts = [(M3D / n).read_text() for n in names] + [PAGE]
    body = "".join(f"<script>{s}</script>" for s in scripts)
    w, h = CANVAS
    data = json.dumps(recipe).replace("<", "\\u003c")
    return ("<!doctype html><html><head><meta charset='utf-8'><style>html,body{margin:0;background:transparent}"
            "canvas{display:block}</style></head><body>"
            f"<canvas id='c' data-w='{w}' data-h='{h}'></canvas>"
            f"<script type='application/json' id='recipe'>{data}</script>"
            f"{body}</body></html>")


def _img(got: dict | None) -> Img | None:
    if not got:
        return None
    return Img(got["w"], got["h"], bytearray(base64.b64decode(got["rgba"])))


class Renderer:
    """One Chromium for a whole run; `session(recipe, files, ref_png)` per
    picture set."""

    def __init__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                                      "--enable-unsafe-swiftshader"])
        self.state = {"html": "", "files": {}, "ref": b""}
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/":
                    self._send(200, state["html"].encode(), "text/html; charset=utf-8")
                elif path == "/ref.png" and state["ref"]:
                    self._send(200, state["ref"], "image/png")
                elif path.startswith("/f/") and path[3:].isdigit() and int(path[3:]) in state["files"]:
                    self._send(200, state["files"][int(path[3:])], "application/octet-stream")
                else:
                    self._send(404, b"", "text/plain")

            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def close(self) -> None:
        self.httpd.shutdown()
        self.browser.close()
        self._pw.stop()

    def session(self, recipe: dict, files: dict[int, bytes], ref_png: bytes) -> Session:
        self.state.update(html=page_html(recipe), files=files, ref=ref_png)
        page = self.browser.new_page(viewport={"width": CANVAS[0], "height": CANVAS[1]}, device_scale_factor=1)
        page.goto(self.base)
        status = page.evaluate("window.__ready")
        if status != "ok":
            page.close()
            raise RuntimeError(f"the viewer failed: {status}")
        return Session(page)


class Session:
    def __init__(self, page):
        self.page = page

    def reference(self) -> tuple[Img | None, int]:
        got = self.page.evaluate("window.__ref()")
        return _img(got), (got or {}).get("body", 0)

    def shot(self, rad: float, target: int, grid: dict | None = None) -> tuple[Img | None, dict | None]:
        """The picture at this turn, and its grid (pass it back to draw
        another recipe on the same pixels)."""
        got = self.page.evaluate("([r, t, g]) => window.__shot(r, t, g)", [rad, target, grid])
        return _img(got), (got or {}).get("grid")

    def redraw(self, recipe: dict) -> None:
        self.page.evaluate("(r) => window.__h.redraw(r).then(() => 'ok')", recipe)

    def missing(self) -> list[int]:
        return self.page.evaluate("window.__h.missing")

    def errors(self) -> list:
        return self.page.evaluate("window.__h.errors.map((e) => ({file: e.file, error: String(e.error)}))")

    def close(self) -> None:
        self.page.close()
