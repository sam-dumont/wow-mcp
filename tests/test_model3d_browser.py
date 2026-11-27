"""The 3D view end to end in Chromium and WebKit: a static bundle published
from a person's store of made-up game files (tests/model3d_world.py),
served by `wow-stats static`, the page's "View in 3D" toggle, the sandboxed
frame drawing with our viewer, the Updated/Classic switch, and the
fallback to the picture when there is nothing to draw. No game file, no
network: the browser may only reach the local server.

Heavy: run through ~/.local/bin/wow-heavy, one browser suite at a time.
"""

from __future__ import annotations

import io
import threading
import time
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer

import pytest

from tests import model3d_world as w
from tests.test_browser import Catalog
from tests.test_publish import LookStore
from wowstats import itemcard, model3d, publish, static, ui

# The frame's canvas, copied to a 2D canvas (the viewer keeps its drawing
# buffer): how many of a grid of points the model covers, and in how many
# colours. data-state "drawn" means the scene is loaded, not painted: the
# first frame comes on the next animation frame, so wait two first, like
# renderer.py does before its screenshot (read too early under load, the
# canvas was empty: 0 hits, 2026-11-21).
COVERED = """async () => {
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const c = document.getElementById('stage');
  const k = document.createElement('canvas');
  k.width = c.width; k.height = c.height;
  const g = k.getContext('2d');
  g.drawImage(c, 0, 0);
  const px = g.getImageData(0, 0, k.width, k.height).data;
  let hit = 0; const colours = new Set();
  for (let y = 0; y < 40; y++) for (let x = 0; x < 40; x++) {
    const i = ((Math.floor((y + 0.5) * k.height / 40) * k.width) + Math.floor((x + 0.5) * k.width / 40)) * 4;
    if (px[i + 3] > 0) { hit++; colours.add((px[i] >> 4) + ',' + (px[i + 1] >> 4) + ',' + (px[i + 2] >> 4)); }
  }
  return [hit, colours.size];
}"""


def bundle(tmp_path, monkeypatch, with_files=True, friends=False):
    def store(vl, archive="", vm="", *a, **k):
        s = LookStore()
        s.vm_url = vm
        return s

    from wowstats.plan import facts

    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    monkeypatch.setattr(ui, "Store", store)
    monkeypatch.setattr(itemcard, "shared", lambda: Catalog())
    w.patch_tables(monkeypatch)
    if with_files:
        w.fill(tmp_path / "assets")
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "on",
           "MODEL3D_CACHE_DIR": str(tmp_path / "cache"), "MODEL3D_ASSETS_DIR": str(tmp_path / "assets"),
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_PATH": "/zogzog/" if friends else "",
           "ZOGZOG_KEY_FILE": str(tmp_path / "key")}
    with redirect_stdout(io.StringIO()):
        res = publish.publish(env)
    assert res["built"], res
    s = static.Site({"SITE_DIR": str(tmp_path / "site"), "ZOGZOG_PATH": env["ZOGZOG_PATH"]})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture(scope="module", params=["chromium", "webkit"])
def browser(request):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        try:
            b = getattr(p, request.param).launch()
        except Exception as e:  # noqa: BLE001 - a browser not installed here
            pytest.skip(f"{request.param}: {e}")
        yield b
        b.close()


def open_page(browser, base, width=1280, height=900, answers=None, path="/"):
    """`answers`: a status the bundle's 3D answers (data/model3d/...) get
    instead of their file, to stand for a missing file or a failing server."""
    ctx = browser.new_context(viewport={"width": width, "height": height})
    asked, outside, errors = [], [], []
    if answers:
        ctx.route(lambda url: url.startswith(base + "/data/model3d/"),
                  lambda r: r.fulfill(status=answers, body="nope", content_type="text/plain"))

    def away(r):
        outside.append(r.request.url)
        return r.abort()

    # Only what leaves the local server is routed (and refused): a route on
    # every request holds back the sandboxed frame's navigation in Chromium.
    ctx.route(lambda url: not url.startswith(base + "/"), away)
    ctx.on("request", lambda r: asked.append(r.url[len(base):]) if r.url.startswith(base + "/") else None)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + path, wait_until="domcontentloaded")
    return ctx, page, asked, outside, errors


def frame_of(page):
    for _ in range(100):
        for f in page.frames:
            if "/model3d/view" in f.url:
                # Its address changes when the navigation commits, before the
                # page is parsed: read then, it had no <html> yet (a loaded
                # suite, 2026-11-23).
                f.wait_for_load_state("domcontentloaded")
                return f
        page.wait_for_timeout(100)  # not time.sleep: Playwright handles its events while it waits
    raise AssertionError("no 3D frame")


def wait_state(frame, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        state = frame.evaluate("document.documentElement.dataset.state || ''")
        if state:
            return state
        frame.wait_for_timeout(200)
    return "timeout: " + frame.evaluate("document.getElementById('status').textContent")


def test_the_toggle_draws_the_model_and_the_switch_changes_it(browser, tmp_path, monkeypatch):
    httpd, base = bundle(tmp_path, monkeypatch)
    ctx, page, asked, outside, errors = open_page(browser, base)
    try:
        btn = page.locator("#gear button.m3dtoggle").first
        btn.wait_for(timeout=20000)
        btn.click()
        frame = frame_of(page)
        assert wait_state(frame) == "drawn"
        hit, colours = frame.evaluate(COVERED)
        assert hit > 20 and colours >= 3, (hit, colours)
        assert page.locator("img.still, .still").first.is_hidden()
        files = {u for u in asked if u.startswith("/model3d/file/")}
        assert f"/model3d/file/{w.FX.CHAR_M2}" in files and f"/model3d/file/{w.SD_M2}" not in files
        # Classic: the original model's recipe, its own file, a new frame.
        page.locator(".m3dmodels button", has_text="Classic").click()
        page.wait_for_timeout(500)
        frame = frame_of(page)
        assert "models=sd" in frame.url and wait_state(frame) == "drawn"
        assert f"/model3d/file/{w.SD_M2}" in asked
        assert frame.evaluate(COVERED)[0] > 20
        # Back to 2D: the picture again, the frame gone.
        page.locator("#gear button.m3dtoggle").first.click()
        assert page.locator("iframe.m3dframe").count() == 0
        assert not outside, outside
        assert not errors, errors
    finally:
        ctx.close()
        httpd.shutdown()


def test_public_view_shows_a_known_companion_without_action_controls(browser, tmp_path, monkeypatch):
    assemble = model3d.Model3D.character

    def with_pet(self, *args, **kwargs):
        answer = assemble(self, *args, **kwargs)
        for key in ("recipe", "recipe_sd"):
            if recipe := answer.get(key):
                recipe["companion"] = {"kind": 2, "model": w.SD_M2, "textures": {}}
        return answer

    monkeypatch.setattr(model3d.Model3D, "character", with_pet)
    httpd, base = bundle(tmp_path, monkeypatch)
    ctx, page, asked, outside, errors = open_page(browser, base)
    try:
        page.emulate_media(reduced_motion="reduce")
        page.locator("#gear button.m3dtoggle").first.click()
        frame = frame_of(page)
        assert wait_state(frame) == "drawn"
        assert f"/model3d/file/{w.SD_M2}" in asked
        assert not frame.locator("button, input[type=checkbox], #effects").count()
        assert not outside and not errors
    finally:
        ctx.close()
        httpd.shutdown()


@pytest.mark.parametrize("friends", [False, True], ids=["public", "friends"])
def test_model_at_a_past_level(browser, tmp_path, monkeypatch, friends):
    httpd, base = bundle(tmp_path, monkeypatch, friends=friends)
    path = "/zogzog/?tab=overview#" + (tmp_path / "key").read_text().strip() if friends else "/"
    ctx, page, _asked, outside, errors = open_page(browser, base, path=path)
    try:
        page.locator("#gear button.m3dtoggle").click()
        assert wait_state(frame_of(page)) == "drawn"
        page.locator("#gear button.m3dtoggle").click()
        page.locator("#tab-timeline").click()
        frame = frame_of(page)
        assert "level=" in frame.url and wait_state(frame) == "drawn"
        assert frame.evaluate(COVERED)[0] > 20
        assert not outside and not errors
    finally:
        ctx.close()
        httpd.shutdown()


def test_phone_framing(browser, tmp_path, monkeypatch):
    httpd, base = bundle(tmp_path, monkeypatch)
    ctx, page, _asked, outside, errors = open_page(browser, base, 390, 844)
    try:
        page.locator("#gear button.m3dtoggle").first.click()
        frame = frame_of(page)
        assert wait_state(frame) == "drawn"
        hit, _ = frame.evaluate(COVERED)
        assert hit > 10
        box = page.locator("iframe.m3dframe").first.bounding_box()
        assert box and box["width"] <= 390 and box["height"] > 100
        assert not outside and not errors, (outside, errors)
    finally:
        ctx.close()
        httpd.shutdown()


@pytest.mark.parametrize("size", [None, (390, 844), (320, 568)])
def test_no_recipe_keeps_the_picture_and_says_why(browser, tmp_path, monkeypatch, size):
    """Nothing to draw (no game files yet): the line says why in one
    sentence, the toggle is off with that line as its reason, and on a phone
    the line keeps between the two icon columns (it ran under them, 2026-11-20)."""
    httpd, base = bundle(tmp_path, monkeypatch, with_files=False)
    ctx, page, asked, outside, errors = open_page(browser, base, *(size or ()))
    try:
        btn = page.locator("#gear button.m3dtoggle").first
        btn.wait_for(timeout=20000)
        why = page.locator("#gear .m3dwhy")
        why.wait_for(timeout=10000)
        text = why.text_content()
        assert text == "No 3D model yet: the shipper sends the game files from your computer on its next run."
        assert btn.is_disabled()
        assert btn.get_attribute("aria-describedby") == why.get_attribute("id")
        assert page.locator("iframe.m3dframe").count() == 0
        assert btn.get_attribute("aria-pressed") in (None, "false")
        if size:
            line = why.bounding_box()
            left = page.locator("#gear .doll > .slots.left").bounding_box()
            right = page.locator("#gear .doll > .slots.right").bounding_box()
            stage = page.locator("#gear .doll > .stage").bounding_box()
            assert line["x"] >= left["x"] + left["width"] - 1, (line, left)
            assert line["x"] + line["width"] <= right["x"] + 1, (line, right)
            assert stage["x"] <= line["x"] and line["y"] + line["height"] <= stage["y"] + stage["height"]
            # the text itself wraps inside its box: nothing scrolls sideways
            assert why.evaluate("(e) => e.scrollWidth <= e.clientWidth + 1")
        assert not [u for u in asked if u.startswith("/model3d/file/")]
        assert not outside and not errors, (outside, errors)
    finally:
        ctx.close()
        httpd.shutdown()


@pytest.mark.parametrize("size", [None, (390, 844)])
def test_no_answer_file_offers_no_toggle_and_no_status(browser, tmp_path, monkeypatch, size):
    """The 3D answer's file isn't there (404: a bundle published without the
    3D): no "View in 3D" (it could only fail), and no "404 Not Found" on the
    page ("No 3D model yet: 404 Not Found." under a dead button, 2026-11-22)."""
    httpd, base = bundle(tmp_path, monkeypatch)
    ctx, page, asked, outside, errors = open_page(browser, base, *(size or ()), answers=404)
    try:
        page.locator("#gear .doll > .stage").first.wait_for(timeout=20000)
        btn = page.locator("#gear button.m3dtoggle")
        # askFirst has asked once the 404 came back: wait for the toggle to go.
        page.wait_for_function("() => [...document.querySelectorAll('#gear button.m3dtoggle')].every((b) => b.hidden)",
                               timeout=10000)
        assert any(u.startswith("/data/model3d/") for u in asked), asked
        assert btn.count() == 0 or not btn.first.is_visible()
        assert page.locator("#gear .m3dwhy").count() == 0
        text = page.locator("body").inner_text()
        assert "404" not in text and "Not Found" not in text and "View in 3D" not in text
        assert page.locator("iframe.m3dframe").count() == 0
        assert not outside and not errors, (outside, errors)
    finally:
        ctx.close()
        httpd.shutdown()


def test_a_failing_answer_says_so_plainly(browser, tmp_path, monkeypatch):
    """Any other failure (a 5xx, the network): one plain line, no status code."""
    httpd, base = bundle(tmp_path, monkeypatch)
    ctx, page, _asked, outside, errors = open_page(browser, base, 390, 844, answers=503)
    try:
        why = page.locator("#gear .m3dwhy")
        why.wait_for(timeout=20000)
        assert why.text_content() == "The 3D model didn’t load: reload the page to try again."
        text = page.locator("body").inner_text()
        assert "503" not in text and "Service Unavailable" not in text
        assert page.locator("#gear button.m3dtoggle").first.is_disabled()
        assert not outside and not errors, (outside, errors)
    finally:
        ctx.close()
        httpd.shutdown()


def test_the_renderer_draws_a_recipe_with_the_files_it_was_sent(tmp_path):
    """wowstats.renderer for real (Chromium, SwiftShader): a recipe, a 428,
    the publisher's uploads from a person's store, a WebP. Nothing it loads
    leaves its own page server (Renderer.blocked)."""
    pytest.importorskip("playwright.sync_api")
    from wowstats import pictures, renderer

    try:
        r = renderer.Renderer({"RENDER_CACHE_DIR": str(tmp_path / "render")})
    except Exception as e:  # noqa: BLE001 - Chromium not installed here
        pytest.skip(f"chromium: {e}")
    tf = tmp_path / "render.token"
    tf.write_text("t" * 43)
    srv = renderer.serve(r, {"BIND": "127.0.0.1", "PORT": "0", "RENDER_TOKEN_FILE": str(tf)})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        store = w.fill(tmp_path / "assets")
        logs = []
        p = pictures.Pictures({"RENDER_URL": f"http://127.0.0.1:{srv.server_address[1]}",
                               "RENDER_TOKEN_FILE": str(tf)}, tmp_path / "out", log=logs.append)
        img = p.get({"recipe": w.SCENE["recipe"]}, store.read)
        assert isinstance(img, bytes) and img[:4] == b"RIFF" and img[8:12] == b"WEBP", (img, logs)
        assert len(img) > 500, "a picture, not an empty canvas"
        assert not r.blocked, r.blocked
    finally:
        srv.shutdown()
        srv.server_close()
        r.worker.submit(lambda: r.browser.close()).result()
        r.worker.submit(lambda: r.pw.stop()).result()
