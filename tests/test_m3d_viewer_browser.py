"""The 3D renderer (web/model3d/m3d) in Chromium and WebKit, on the synthetic
files of tools/model3d_fixtures.py: no game file, no network.

A local server gives one page with the m3d scripts inline under a policy of
script hashes only (no 'unsafe-eval', no 'wasm-unsafe-eval': stricter than
the viewer frame's), and the files at /f/<id>. The page draws the scene's
recipe with a clock the test sets, and the test reads the canvas back at
points of the model projected to the screen (debug.project), so every check
is "this colour at this point of the model", not a screenshot diff.

Heavy: run through ~/.local/bin/wow-heavy, one browser suite at a time.
"""

import base64
import hashlib
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
M3D = ROOT / "web" / "model3d" / "m3d"
TOLERANCE = 10


def fixtures():
    spec = importlib.util.spec_from_file_location("model3d_fixtures", ROOT / "tools" / "model3d_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FX = fixtures()
SCENE = FX.scene()

PAGE_SCRIPT = r"""
'use strict';
window.__t = 0;
const canvas = document.getElementById('c');
canvas.style.width = canvas.dataset.w + 'px';
canvas.style.height = canvas.dataset.h + 'px';
const recipe = JSON.parse(document.getElementById('recipe').textContent);
const get = (id) => fetch('/f/' + id).then((r) => (r.ok ? r.arrayBuffer() : null));
window.__ready = M3D.draw(canvas, recipe, get, { clock: () => window.__t }).then(
	(h) => { window.__h = h; return 'ok'; },
	(e) => { window.__err = String((e && e.stack) || e); return window.__err; });
window.__frames = (n) => new Promise((resolve) => {
	const f = () => (--n <= 0 ? resolve() : requestAnimationFrame(f));
	requestAnimationFrame(f);
});
window.__at = async (t) => { window.__t = t; await window.__frames(3); };
// RGBA at points of the model (WoW coordinates), and where they landed
window.__pixel = (x, y) => {
	const k = document.createElement('canvas');
	k.width = canvas.width;
	k.height = canvas.height;
	const g = k.getContext('2d');
	g.drawImage(canvas, 0, 0);
	return Array.from(g.getImageData(x, y, 1, 1).data);
};
window.__sample = (points) => {
	const w = canvas.width, h = canvas.height;
	const k = document.createElement('canvas');
	k.width = w;
	k.height = h;
	const g = k.getContext('2d');
	g.drawImage(canvas, 0, 0);
	const d = g.getImageData(0, 0, w, h).data;
	const sx = w / canvas.clientWidth;
	return points.map((p) => {
		const q = window.__h.debug.project(p);
		if (!q) return null;
		const x = Math.round(q[0] * sx), y = Math.round(q[1] * sx);
		if (x < 0 || y < 0 || x >= w || y >= h) return [0, 0, 0, 0, x, y, 'off'];
		const i = (y * w + x) * 4;
		return [d[i], d[i + 1], d[i + 2], d[i + 3], x, y];
	});
};
"""


def sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def page_html(recipe: dict, width: int, height: int) -> tuple[str, str]:
    names = [n.strip() for n in (M3D / "ORDER.txt").read_text().splitlines() if n.strip()]
    scripts = [(M3D / n).read_text() for n in names] + [PAGE_SCRIPT]
    style = "html,body{margin:0;background:#fff}canvas{display:block}"
    policy = ("default-src 'none'; script-src " + " ".join(sha(s) for s in scripts)
              + f"; style-src {sha(style)}; img-src data: blob:; connect-src 'self'")
    body = "".join(f"<script>{s}</script>" for s in scripts)
    recipe_json = json.dumps(recipe).replace("<", "\\u003c")
    html = (f"<!doctype html><html><head><meta charset='utf-8'><style>{style}</style></head><body>"
            f"<canvas id='c' data-w='{width}' data-h='{height}'></canvas>"
            f"<script type='application/json' id='recipe'>{recipe_json}</script>{body}</body></html>")
    return html, policy


@pytest.fixture(scope="module")
def server():
    pages = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in pages:
                html, policy = pages[path]
                self._send(200, html.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": policy})
            elif path.startswith("/f/") and path[3:].isdigit() and int(path[3:]) in SCENE["files"]:
                self._send(200, SCENE["files"][int(path[3:])], "application/octet-stream")
            else:
                self._send(404, b"", "text/plain")

        def _send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def add(name, recipe, width=480, height=640):
        pages["/" + name] = page_html(recipe, width, height)
        return base + "/" + name

    yield add
    httpd.shutdown()


@pytest.fixture(scope="module", params=["chromium", "webkit"])
def browser(request):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        try:
            b = getattr(p, request.param).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"{request.param}: {e}")
        yield b
        b.close()


class Drawing:
    def __init__(self, page, logs):
        self.page = page
        self.logs = logs

    def at(self, t):
        self.page.evaluate(f"window.__at({int(t)})")

    def sample(self, points):
        return self.page.evaluate("(p) => window.__sample(p)", [list(p) for p in points])

    def handle(self, expr):
        return self.page.evaluate(f"() => window.__h.{expr}")


@pytest.fixture
def draw(browser, server):
    pages = []

    def go(recipe=None, width=480, height=640, name="scene"):
        url = server(f"{name}-{len(pages)}", recipe or SCENE["recipe"], width, height)
        page = browser.new_page(viewport={"width": max(width, 320) + 40, "height": height + 40})
        pages.append(page)
        logs = []
        page.on("console", lambda m: logs.append(m.text[:300]))
        page.on("pageerror", lambda e: logs.append("pageerror: " + str(e)[:300]))
        page.goto(url)
        state = page.evaluate("() => window.__ready")
        assert state == "ok", (state, logs)
        page.evaluate("window.__frames(3)")
        return Drawing(page, logs)

    yield go
    for p in pages:
        p.close()


def check(d, expected):
    """Each (point, colour or None, why): the colour within TOLERANCE, None
    the transparent background. Returns the failures."""
    got = d.sample([p for p, _, _ in expected])
    bad = []
    for (point, colour, why), px in zip(expected, got, strict=True):
        if colour is None:
            ok = px is not None and px[3] < 20
        else:
            ok = px is not None and px[3] > 235 and all(abs(a - b) <= TOLERANCE for a, b in zip(px[:3], colour,
                                                                                               strict=True))
        if not ok:
            bad.append((why, point, colour, px))
    return bad


def test_draws_textured_with_geosets_and_attachments(draw):
    d = draw()
    d.at(0)
    bad = check(d, SCENE["expect"]["t0"])
    assert not bad, (bad, d.logs)
    assert not [m for m in d.logs if "Refused" in m or "Content Security Policy" in m], d.logs


def test_material_layers_land_in_their_sections(draw):
    d = draw()
    m = SCENE["expect"]["material"]
    got = d.page.evaluate(f"""() => {{
        const m = window.__h.debug.material({m['type']});
        return [m.width, m.height, {json.dumps(m['points'])}.map(([[x, y]]) =>
            Array.from(m.data.slice((y * m.width + x) * 4, (y * m.width + x) * 4 + 3)))];
    }}""")
    assert got[:2] == [m["width"], m["height"]]
    for ((x, y), colour), px in zip(m["points"], got[2], strict=True):
        assert all(abs(a - b) <= 3 for a, b in zip(px, colour, strict=True)), ((x, y), colour, px)


def test_animation_moves_the_head_and_the_item(draw):
    d = draw()
    d.at(0)
    before = check(d, SCENE["expect"]["t0"])
    d.at(500)
    bad = check(d, SCENE["expect"]["t500"])
    assert not before and not bad, (before, bad, d.logs)
    # and back at rest a loop later (1000 ms Stand)
    d.at(1500)
    d.at(2000)
    assert not check(d, SCENE["expect"]["t0"][:8])


def test_missing_files_are_listed_not_fatal(draw):
    d = draw()
    assert sorted(d.handle("missing")) == SCENE["expect"]["missing"]
    assert d.handle("errors") == []
    # a recipe whose character itself is missing draws nothing, and says so
    r = dict(SCENE["recipe"], model=31337)
    e = draw(r, name="nochar")
    assert 31337 in e.handle("missing")
    assert e.page.evaluate("() => window.__pixel(240, 320)")[3] == 0


def test_redraw_swaps_the_recipe(draw):
    d = draw()
    r = json.loads(json.dumps(SCENE["recipe"]))
    r["geosets"]["set"]["401"] = True
    r["attachments"] = [a for a in r["attachments"] if a["model"] != FX.MISSING_ITEM]
    d.page.evaluate(f"() => window.__h.redraw({json.dumps(r)}).then(() => window.__frames(3))")
    d.at(0)
    assert not check(d, SCENE["expect"]["redraw"]), d.logs
    assert d.handle("missing") == [FX.MISSING_LAYER]


@pytest.mark.parametrize("size", [(360, 520), (900, 600)])
def test_framing_keeps_head_and_feet_in_view_and_drag_turns(draw, size):
    w, h = size
    d = draw(width=w, height=h, name=f"frame{w}")
    d.at(0)
    top, feet = d.page.evaluate("() => [window.__h.debug.project([0, 0, 2.6]), window.__h.debug.project([0, 0, 0])]")
    assert 0 < top[1] < h * 0.3 and h * 0.7 < feet[1] < h, (top, feet)
    for side in (-0.95, 0.95):
        x = d.page.evaluate(f"() => window.__h.debug.project([0, {side}, 1.3])")[0]
        assert 0 < x < w, (side, x)
    # a drag to the right turns the model's front to the right: its right
    # side (WoW +y, screen right) goes round the back, its left side faces us
    right_before = d.page.evaluate("() => window.__h.debug.project([0, 0.7, 1.4])")[0]
    box = d.page.locator("#c").bounding_box()
    y = box["y"] + box["height"] / 2
    d.page.mouse.move(box["x"] + box["width"] / 2, y)
    d.page.mouse.down()
    d.page.mouse.move(box["x"] + box["width"] / 2 + 157, y, steps=8)
    d.page.mouse.up()
    d.page.evaluate("window.__frames(2)")
    right_after = d.page.evaluate("() => window.__h.debug.project([0, 0.7, 1.4])")[0]
    assert right_after < right_before - 10, (right_before, right_after)
    assert not check(d, [((0.0, -0.9, 0.8), FX.YELLOW, "the skinned model's outer side, facing after the drag")]), \
        d.logs
    top, feet = d.page.evaluate("() => [window.__h.debug.project([0, 0, 2.6]), window.__h.debug.project([0, 0, 0])]")
    assert top[1] > 0 and feet[1] < h, (top, feet)


def test_stop_ends_the_loop(draw):
    d = draw()
    d.at(0)
    head = d.sample([(0.25, 0.22, 2.5)])[0]
    assert head[:3] == list(FX.RED), head
    d.page.evaluate("() => window.__h.stop()")
    d.at(500)
    # nothing redraws: the frame at rest stays on the canvas, the head untilted
    assert d.page.evaluate(f"() => window.__pixel({head[4]}, {head[5]})")[:3] == list(FX.RED)
    assert sorted(d.handle("missing")) == SCENE["expect"]["missing"]


def lit(px, floor=60):
    return px is not None and px[3] > floor


def walk(d, start, end, step=20):
    for t in range(start, end + 1, step):
        d.at(t)


def test_glow_particles_colour_over_their_life(draw):
    d = draw(FX.emitter_recipe(FX.GLOW_M2), name="glow")
    d.at(0)
    (px,) = d.sample([(0, 0, 0)])
    assert not lit(px, 5), ("nothing spawned yet", px)
    d.at(100)
    (early,) = d.sample([(0, 0, 0)])
    assert d.handle("debug.particles()") == {"particles": 3, "edges": 0, "emitters": 1}
    d.at(900)
    (late,) = d.sample([(0, 0, 0)])
    # red at birth, blue at the end of the 1 s life (additive, 3 particles)
    assert lit(early) and early[0] > 200 and early[2] < 100, early
    assert lit(late) and late[2] > 200 and late[0] < 140, late
    d.at(1200)
    assert d.handle("debug.particles()")["particles"] == 3, "the next loop's burst, the first one gone"


def test_emitter_off_draws_nothing(draw):
    d = draw(FX.emitter_recipe(FX.GLOW_OFF_M2), name="glowoff")
    for t in (100, 500, 900):
        d.at(t)
        (px,) = d.sample([(0, 0, 0)])
        assert not lit(px, 5), (t, px)
    assert d.handle("debug.particles()") == {"particles": 0, "edges": 0, "emitters": 1}


def test_an_items_glow_visual_emits_at_its_attachment(draw):
    d = draw()
    d.at(100)
    (px,) = d.sample([SCENE["expect"]["glow_at"]])
    assert lit(px) and px[0] > 200 and px[2] < 100, px
    assert d.handle("debug.particles()")["particles"] == 3
    # the meshes around it are as before
    assert not check(d, SCENE["expect"]["t0"][:5]), d.logs


def test_ribbon_trails_behind_its_bone(draw):
    d = draw(FX.emitter_recipe(FX.RIBBON_M2), name="ribbon")
    left, right = (0.0, -0.6, 1.0), (0.0, 0.6, 1.0)
    # the bone turns once a second about WoW x: top at 0, left (screen) at
    # 250 ms, bottom at 500, right at 750; the trail lives 0.5 s
    walk(d, 0, 500)
    a, b = d.sample([left, right])
    assert lit(a) and a[0] > 200 and 80 < a[1] < 180 and a[2] < 60, a
    assert not lit(b, 5), b
    walk(d, 520, 1000)
    a, b = d.sample([left, right])
    assert lit(b) and b[0] > 200, b
    assert not lit(a, 5), a
