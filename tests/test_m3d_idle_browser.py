"""The 3D viewer's idle animations (web/model3d/m3d/draw.js Idle) in Chromium
and WebKit, on a made-up model: no game file, no network.

The model has Stand (0), ReadySpellDirected (51) and SpellCastDirected (53)
in the file, EmoteTalk (60) in an .anim that comes, and Ready2H (27) and
Attack2H (18) in .anim files that don't (a server whose shipper hasn't sent
them yet). With the recipe's attack 18, only "cast" and
"talk" can be played; the clock and the random draws are the test's.

Heavy: run through ~/.local/bin/wow-heavy, one browser suite at a time.
"""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
M3D = ROOT / "web" / "model3d" / "m3d"


def fixtures():
    spec = importlib.util.spec_from_file_location("model3d_fixtures", ROOT / "tools" / "model3d_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FX = fixtures()
MODEL, SKIN, TEX, TALK_ANIM, ABSENT_ANIM = 100081, 100082, 200021, 100083, 100084
INLINE = 0x20


COMBAT_MODEL = 100085


def files(combat=False) -> dict[int, bytes]:
    verts, idx = FX.box((-0.3, -0.3, 0.0), (0.3, 0.3, 2.0), 0)
    skin_file, all_verts = FX.skin([(0, verts, idx)], [(0, 0)])
    data, _ts, _vs = FX.anim_payload([0], [b"\0" * 8])
    model = FX.m2(verts=all_verts, textures=[(0, TEX)], bounds=((-0.3, -0.3, 0.0), (0.3, 0.3, 2.0)), skin_id=SKIN,
                  bones=[lambda d: FX.bone(d, FX.PIVOT_ROOT, -1, 0)],
                  attachments=[(1, 0, (0, -.3, 1)), (2, 0, (0, .3, 1))],
                  sequences=[FX.sequence(0, 1000, INLINE), FX.sequence(51, 400, INLINE),
                             FX.sequence(53, 600, INLINE), FX.sequence(60, 2000, 0),
                             FX.sequence(27, 500, 0), FX.sequence(18, 700, 0)]
                            + ([FX.sequence(i, 400, INLINE) for i in (17, 26, 29, 46, 48, 49, 52, 54, 55, 95, 124)]
                               if combat else []),
                  afid=[(60, 0, TALK_ANIM), (27, 0, ABSENT_ANIM), (18, 0, ABSENT_ANIM)])
    return {MODEL: model, SKIN: skin_file, TEX: FX.blp_bgra(4, 4, [(200, 200, 200, 255)] * 16), TALK_ANIM: data}


FILES = {**files(), COMBAT_MODEL: files(combat=True)[MODEL]}
RECIPE = {**FX.emitter_recipe(MODEL), "attack": 18}
COMBAT = {**RECIPE, "model": COMBAT_MODEL, "attack": 17, "class_id": 3, "level": 30, "ranged": {"kind": 2, "at_rest": False},
          "attachments": [{"slot": slot, "attach": hand, "model": MODEL} for slot, hand in [(16, 1), (17, 2), (18, 2)]]}

PAGE = r"""
'use strict';
window.__t = 0;
const opts = JSON.parse(document.getElementById('opts').textContent);
const canvas = document.getElementById('c');
const get = (id) => fetch('/f/' + id).then((r) => (r.ok ? r.arrayBuffer() : null));
window.__asked = [];
const asked = (id) => { window.__asked.push(id); return get(id); };
const o = { clock: () => window.__t, random: () => opts.random, effects: document.getElementById('effects') };
if (opts.idle !== undefined) o.idle = opts.idle;
if (opts.reduced) o.reducedMotion = () => true;
window.__ready = M3D.draw(canvas, JSON.parse(document.getElementById('recipe').textContent), asked, o).then(
	(h) => { window.__h = h; return 'ok'; }, (e) => String((e && e.stack) || e));
window.__frames = (n) => new Promise((resolve) => {
	const f = () => (--n <= 0 ? resolve() : requestAnimationFrame(f));
	requestAnimationFrame(f);
});
// the clock in 100 ms frames, each drawn; Idle's state after
window.__walk = async (to) => {
	while (window.__t < to) { window.__t = Math.min(to, window.__t + 100); await window.__frames(1); }
	await window.__frames(2);
	return window.__h.debug.idle();
};
"""


@pytest.fixture(scope="module")
def server():
    pages = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in pages:
                body, ctype = pages[path].encode(), "text/html; charset=utf-8"
            elif path.startswith("/f/") and path[3:].isdigit() and int(path[3:]) in FILES:
                body, ctype = FILES[int(path[3:])], "application/octet-stream"
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = type("Server", (ThreadingHTTPServer,), {"request_queue_size": 128})(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    names = [n.strip() for n in (M3D / "ORDER.txt").read_text().splitlines() if n.strip()]
    scripts = "".join(f"<script>{(M3D / n).read_text()}</script>" for n in names)

    def add(name, opts, recipe):
        pages["/" + name] = (
            "<!doctype html><html><head><meta charset='utf-8'><style>canvas{width:300px;height:400px}"
            "</style></head><body><canvas id='c'></canvas><canvas id='effects'></canvas>"
            f"<script type='application/json' id='recipe'>{json.dumps(recipe)}</script>"
            f"<script type='application/json' id='opts'>{json.dumps(opts)}</script>"
            f"{scripts}<script>{PAGE}</script></body></html>")
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


@pytest.fixture
def view(browser, server):
    pages = []

    def go(recipe=RECIPE, **opts):
        page = browser.new_page(viewport={"width": 340, "height": 440})
        pages.append(page)
        page.goto(server(f"p{len(pages)}-{browser.browser_type.name}", {"idle": True, "random": 0.5, **opts}, recipe))
        assert page.evaluate("() => window.__ready") == "ok"
        return page

    yield go
    for p in pages:
        p.close()


def walk(page, t):
    return page.evaluate(f"window.__walk({t})")


def test_plays_only_what_the_model_has_then_stands_again(view):
    page = view()
    # random 0.5: the first move at 8 s + 0.5 * 12 s
    st = walk(page, 13900)
    assert st["playing"] == 0 and st["next_at"] == 14000 and st["available"] is None
    # nothing but Stand asked for so far: the other .anim files wait until a move is due
    assert TALK_ANIM not in page.evaluate("() => window.__asked")
    st = walk(page, 14200)
    assert st["available"] == ["cast", "talk"], st
    assert st["move"] == "talk" and st["playing"] == 60, st  # floor(0.5 * 2)
    # the absent .anim is a move not played, not a missing file of the drawing
    assert ABSENT_ANIM in page.evaluate("() => window.__asked")
    assert page.evaluate("() => window.__h.missing") == []
    # EmoteTalk once (2 s), then Stand, the next move 14 s later
    st = walk(page, 16500)
    assert st["playing"] == 0 and st["move"] is None and st["played"] == ["talk"], st
    assert 16000 <= st["next_at"] - 14000 <= 16500 + 100
    # the next move isn't the same one: cast, its two steps, then Stand
    st = walk(page, st["next_at"] + 100)
    assert st["move"] == "cast" and st["playing"] == 51, st
    st = walk(page, st["next_at"] + 900)
    assert st["playing"] == 53, st
    st = walk(page, st["next_at"] + 1600)
    assert st["playing"] == 0 and st["played"] == ["talk", "cast"], st


def test_nothing_starts_while_the_model_is_turned(view):
    page = view()
    box = page.locator("#c").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 30, y)
    st = walk(page, 20000)
    assert st["playing"] == 0 and st["played"] == [], st
    page.mouse.up()
    # three seconds after letting go at the soonest
    st = walk(page, 22000)
    assert st["played"] == [], st
    st = walk(page, 45000)
    assert st["played"], st


def test_reduced_motion_plays_nothing(view):
    page = view(reduced=True)
    st = walk(page, 60000)
    assert st["playing"] == 0 and st["played"] == [] and st["available"] is None, st


def test_off_by_default_for_the_renderers_pictures(view):
    page = view(idle=None)
    assert page.evaluate("() => window.__h.debug.idle()") is None


def test_weapons_switch_for_a_real_clip_then_both_melee_hands_return(view):
    page = view(recipe=COMBAT, reduced=True)
    actions = lambda: [m["name"] for m in page.evaluate("__h.actions()")]  # noqa: E731
    slots = lambda: page.evaluate("__h.debug.equippedSlots()")  # noqa: E731
    assert actions() == ["attack", "ranged", "hunters-mark", "arcane-shot"]
    assert slots() == [16, 17]
    assert page.evaluate("__h.play('arcane-shot')")
    assert slots() == [18] and walk(page, 100)["playing"] == 29
    assert walk(page, 600)["playing"] == 46
    assert page.evaluate("""() => {
      const c = document.querySelector('#effects');
      return c.getContext('2d').getImageData(0, 0, c.width, c.height).data.some(x => x);
    }""")
    assert walk(page, 1100)["playing"] == 0 and slots() == [16, 17]
    assert page.evaluate("__h.play('attack')") and slots() == [16, 17]
    # Missing thrown release, then missing bow model, then missing hand on this model.
    for patch in ({"ranged": {"kind": 16}}, {"attachments": []},
                  {"attachments": [{"slot": 18, "attach": 98, "model": MODEL}]}):
        page.evaluate("r => __h.redraw(r).then(() => true)", {**COMBAT, **patch})
        assert "ranged" not in actions() and "arcane-shot" not in actions()
        assert not page.evaluate("__h.play('ranged')")


def test_class_buttons_require_their_own_animation_and_follow_redraw(view):
    page = view(recipe=COMBAT)
    for cls, ability in [(1, "battle-shout"), (2, "holy-light"), (3, "hunters-mark"), (4, "kick"),
                         (5, "power-word-shield"), (7, "lightning-bolt"), (8, "frostbolt"),
                         (9, "drain-life"), (11, "rejuvenation")]:
        page.evaluate("r => __h.redraw(r).then(() => true)", {**COMBAT, "class_id": cls})
        names = [m["name"] for m in page.evaluate("__h.actions()")]
        assert ability in names
        assert page.evaluate("name => __h.play(name)", ability)
        assert page.evaluate("__h.debug.idle().move") == ability
    page.evaluate("r => __h.redraw(r).then(() => true)", {**COMBAT, "model": MODEL, "class_id": 4})
    assert not page.evaluate("__h.play('kick')")
