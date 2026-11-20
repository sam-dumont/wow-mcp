"""Info 1 of the review: POST /render had no login, so anything that reached
the port (a published container port, a flat Docker network, a pod without
a network policy) could keep Chromium busy at ~4 s and 800 MB a drawing.
It now takes a bearer token from a file, made on first start when none is
given; the publisher sends it."""

import json
import os
import stat
import threading
import urllib.error
import urllib.request

import pytest

from wowstats import pictures, renderer

BODY = json.dumps({"recipe": {"v": 1}}).encode()  # past the door, a recipe it refuses


class NoChromium:
    def alive(self) -> bool:
        return True


def start(env):
    s = renderer.serve(NoChromium(), {"BIND": "127.0.0.1", "PORT": "0", **env})
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}"


def post(url, headers=None):
    req = urllib.request.Request(url + "/render", data=BODY, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_a_given_token_is_required(tmp_path):
    tf = tmp_path / "render.token"
    tf.write_text("t" * 43 + "\n")
    s, url = start({"RENDER_TOKEN_FILE": str(tf)})
    try:
        assert post(url) == 401
        assert post(url, {"Authorization": "Bearer " + "x" * 43}) == 401
        assert post(url, {"Authorization": "Bearer " + "t" * 43}) == 400  # past the door: a bad drawing
        assert urllib.request.urlopen(url + "/healthz", timeout=5).status == 200  # probes need none
    finally:
        s.shutdown()
        s.server_close()


def test_no_token_given_one_is_made(tmp_path):
    s, url = start({"RENDER_CACHE_DIR": str(tmp_path)})
    try:
        made = tmp_path / "render.token"
        assert made.is_file() and stat.S_IMODE(os.stat(made).st_mode) == 0o600
        token = made.read_text().strip()
        assert len(token) >= 32
        assert post(url) == 401
        assert post(url, {"Authorization": "Bearer " + token}) == 400
    finally:
        s.shutdown()
        s.server_close()


def test_a_short_token_refuses_to_start(tmp_path):
    tf = tmp_path / "render.token"
    tf.write_text("short")
    with pytest.raises(SystemExit, match="RENDER_TOKEN_FILE"):
        renderer.serve(NoChromium(), {"BIND": "127.0.0.1", "PORT": "0", "RENDER_TOKEN_FILE": str(tf)})


def test_the_publisher_sends_the_token(tmp_path, monkeypatch):
    tf = tmp_path / "render.token"
    tf.write_text("t" * 43)
    seen = []

    def fake(req, timeout):
        seen.append(req.get_header("Authorization"))
        raise urllib.error.URLError("stop")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    p = pictures.Pictures({"RENDER_URL": "http://render.invalid", "RENDER_TOKEN_FILE": str(tf)}, tmp_path / "out",
                          log=lambda *a: None)
    p._ask({"recipe": {}}, lambda fdid: None)
    assert seen == ["Bearer " + "t" * 43]


def test_the_publisher_without_a_token_draws_nothing(tmp_path):
    logs = []
    p = pictures.Pictures({"RENDER_URL": "http://render.invalid"}, tmp_path / "out", log=logs.append)
    assert not p.on and any("RENDER_TOKEN_FILE" in m for m in logs)
