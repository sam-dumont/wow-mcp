"""The renderer's files protocol (wowstats.renderer, wowstats.pictures):
a render asks for the files it lacks (428), the publisher sends them from
the person's store (POST /file/<id>, its token, the body's SHA-256, a size
cap, game files only), the renderer keeps them by id within RENDER_CACHE_MB
and the render goes through. No Chromium here: the drawing is a stand-in;
tests/test_model3d_browser.py draws for real. Made-up files only."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request

from tests import model3d_world as w
from wowstats import assets, pictures, renderer

TOKEN = "t" * 43
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00/\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00"


class Drawer:
    """The Renderer without Chromium: the same files and the same 428."""

    def __init__(self, root, budget=64 * 1024 * 1024):
        self.files = renderer.Files(root, budget)
        self.drawn: list[dict] = []

    def alive(self):
        return True

    def render(self, req):
        lacking = self.files.lacking(req["recipe"], req["absent"])
        if lacking:
            raise renderer.Lacking(lacking)
        self.drawn.append(req)
        return WEBP


def start(tmp_path, drawer):
    tf = tmp_path / "render.token"
    tf.write_text(TOKEN)
    s = renderer.serve(drawer, {"BIND": "127.0.0.1", "PORT": "0", "RENDER_TOKEN_FILE": str(tf)})
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}", tf


def post(url, path, body, headers=None):
    req = urllib.request.Request(url + path, data=body, method="POST",
                                 headers={"Authorization": "Bearer " + TOKEN, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError:  # refused before the body was read: the connection closes under it
        return 0, b""


def test_428_names_the_files_then_the_render_goes_through(tmp_path):
    d = Drawer(tmp_path / "cache")
    s, url, tf = start(tmp_path, d)
    try:
        code, body = post(url, "/render", json.dumps({"recipe": w.SCENE["recipe"]}).encode())
        first = json.loads(body)["files"]
        assert code == 428 and set(first) == set(w.character3d.needs(w.SCENE["recipe"])), first
        # The publisher's side: the files from the person's store, until it draws.
        store = w.fill(tmp_path / "assets")
        p = pictures.Pictures({"RENDER_URL": url, "RENDER_TOKEN_FILE": str(tf)}, tmp_path / "out",
                              log=lambda *a: None)
        img = p.get({"recipe": w.SCENE["recipe"]}, store.read)
        assert img == WEBP and len(d.drawn) == 1
        # Everything the recipe draws with that the store has went over, by id;
        # what the store doesn't have is drawn without (absent).
        have, missing = assets.closure(w.character3d.needs(w.SCENE["recipe"]), store.read)
        assert {int(f.name) for f in d.files.root.iterdir()} == set(have)
        assert d.drawn[0]["absent"] == missing and {w.FX.MISSING_ITEM, w.FX.MISSING_LAYER} <= missing
        # The next render needs nothing sent.
        assert post(url, "/render", json.dumps({"recipe": w.SCENE["recipe"],
                                                "absent": sorted(d.drawn[0]["absent"])}).encode())[0] == 200
    finally:
        s.shutdown()
        s.server_close()


def test_file_upload_rules(tmp_path):
    d = Drawer(tmp_path / "cache")
    s, url, _ = start(tmp_path, d)
    model = w.files()[w.FX.CHAR_M2]
    sha = {"X-Content-SHA256": hashlib.sha256(model).hexdigest()}
    try:
        assert post(url, "/file/1", model, {"Authorization": "Bearer " + "x" * 43, **sha})[0] == 401
        assert post(url, "/file/1", model, {"X-Content-SHA256": "0" * 64})[0] == 400
        assert post(url, "/file/1", model)[0] == 400  # no hash
        junk = b"<script>" * 10
        assert post(url, "/file/1", junk, {"X-Content-SHA256": hashlib.sha256(junk).hexdigest()})[0] == 415
        big = b"MD21" + bytes(assets.MAX_FILE)
        assert post(url, "/file/1", big, {"X-Content-SHA256": hashlib.sha256(big).hexdigest()})[0] in (413, 0)
        assert post(url, "/file/abc", model, sha)[0] == 404
        assert not list(d.files.root.iterdir()), "nothing refused was kept"
        assert post(url, "/file/1", model, sha)[0] == 204
        assert d.files.get(1) == model
    finally:
        s.shutdown()
        s.server_close()


def test_the_cache_keeps_to_its_budget(tmp_path):
    f = renderer.Files(tmp_path, budget=3000)
    for i in range(1, 6):
        f.put(i, b"BLP2" + bytes(996))
    kept = sorted(int(p.name) for p in f.root.iterdir())
    assert kept == [3, 4, 5], "the oldest go first, the newest always stays"


def test_the_renderer_holds_no_name_or_key(tmp_path):
    """Its page server answers the drawing and the files by id, and nothing
    else; the request it keeps has no character, level or item name."""
    req = renderer.parse(json.dumps({"recipe": w.SCENE["recipe"], "name": "Hero-Test", "level": 12,
                                     "items": [{"name": "Test Boots"}]}).encode())
    assert set(req) == {"recipe", "absent"}
    assert b"Hero" not in json.dumps(req["recipe"]).encode() and b"Boots" not in json.dumps(req["recipe"]).encode()
