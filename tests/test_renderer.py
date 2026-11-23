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

import pytest

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
        lacking = self.files.lacking(req["recipe"], req["absent"], req.get("scope", ""))
        if lacking:
            raise renderer.Lacking(lacking)
        self.drawn.append(req)
        # What Chromium would read: the request's own scope's files.
        self.read = {f: self.files.get(f, req.get("scope", ""))
                     for f in assets.closure(w.character3d.needs(req["recipe"]),
                                             lambda f: self.files.get(f, req.get("scope", "")))[0]}
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
        assert {int(f.name) for f in (d.files.root / p.scope).iterdir()} == set(have)
        assert d.drawn[0]["absent"] == missing and {w.FX.MISSING_ITEM, w.FX.MISSING_LAYER} <= missing
        # The next render of that person needs nothing sent; another scope's does.
        again = {"recipe": w.SCENE["recipe"], "absent": sorted(d.drawn[0]["absent"])}
        assert post(url, "/render", json.dumps({**again, "scope": p.scope}).encode())[0] == 200
        assert post(url, "/render", json.dumps(again).encode())[0] == 428
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


def test_one_persons_files_never_draw_anothers_picture(tmp_path):
    """Review of ce8239e (bug 6): the renderer kept files by id alone, for
    every person. Each person's files come from their own upload, checked for
    their kind only, so a file one person sent stood in for another's."""
    d = Drawer(tmp_path / "cache")
    s, url, tf = start(tmp_path, d)
    try:
        store = w.fill(tmp_path / "assets")
        have, _ = assets.closure(w.character3d.needs(w.SCENE["recipe"]), store.read)
        tex = next(f for f in sorted(have) if store.read(f)[:4] == b"BLP2")
        odd = store.read(tex) + b"someone else's"

        def theirs(f):
            return odd if f == tex else store.read(f)

        def pics(account):
            return pictures.Pictures({"RENDER_URL": url, "RENDER_TOKEN_FILE": str(tf), "VL_ACCOUNT_ID": account},
                                     tmp_path / f"out-{account}", log=lambda *a: None)

        a, b = pics("1"), pics("2")
        assert a.scope != b.scope
        assert a.get({"recipe": w.SCENE["recipe"]}, theirs) == WEBP and d.read[tex] == odd
        sent = []

        def mine(f):
            sent.append(f)
            return store.read(f)

        assert b.get({"recipe": w.SCENE["recipe"]}, mine) == WEBP
        assert tex in sent and d.read[tex] == store.read(tex), "drawn from the other person's file"
        assert d.files.get(tex, a.scope) == odd and d.files.get(tex, b.scope) == store.read(tex)
    finally:
        s.shutdown()
        s.server_close()


def test_a_scope_is_hex_or_refused(tmp_path):
    with pytest.raises(renderer.BadRequest):
        renderer.parse(json.dumps({"recipe": w.SCENE["recipe"], "scope": "../x"}).encode())
    assert renderer.parse(json.dumps({"recipe": w.SCENE["recipe"]}).encode())["scope"] == ""
    d = Drawer(tmp_path / "cache")
    s, url, _ = start(tmp_path, d)
    model = w.files()[w.FX.CHAR_M2]
    sha = {"X-Content-SHA256": hashlib.sha256(model).hexdigest()}
    try:
        assert post(url, "/file/1", model, {**sha, "X-Render-Scope": "../../etc"})[0] == 400
        assert post(url, "/file/1", model, {**sha, "X-Render-Scope": "ab12"})[0] == 204
        assert d.files.get(1, "ab12") == model and d.files.get(1) is None
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
    assert set(req) == {"recipe", "absent", "scope"}
    assert b"Hero" not in json.dumps(req["recipe"]).encode() and b"Boots" not in json.dumps(req["recipe"]).encode()
