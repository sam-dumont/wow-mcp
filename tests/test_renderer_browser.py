"""The renderer for real: Wowhead's viewer in Playwright's Chromium draws a
gnome in seven items, through the relay from wow.zamimg.com, and answers a
WebP with the character on a clear background.

Opt-in (WOW_STATS_BROWSER=1): it needs the network and Playwright's Chromium
(`uv run --group browser playwright install chromium`).
"""

import json
import os
import struct
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wowstats import renderer

pytestmark = pytest.mark.skipif(os.environ.get("WOW_STATS_BROWSER") != "1",
                                reason="opt-in: WOW_STATS_BROWSER=1 (network and a browser)")

TOKEN = "r" * 43
# A gnome (ChrModel 13) in Battle.net's look ids, in a hat, shoulders, a
# robe, trousers, boots, a cloak and a staff: game data, nobody's character.
GNOME = {"model": {"chr_model": 13, "gender": 0, "options": [
    {"optionId": 81, "choiceId": 1185}, {"optionId": 80, "choiceId": 1174}, {"optionId": 82, "choiceId": 1199},
    {"optionId": 83, "choiceId": 1214}, {"optionId": 84, "choiceId": 1227}]},
    "items": [[1, 16098], [3, 8387], [20, 9943], [7, 5848], [8, 7081], [16, 15165], [17, 472]]}


def webp_size(b: bytes) -> tuple[int, int]:
    assert b[:4] == b"RIFF" and b[8:12] == b"WEBP"
    chunk = b[12:16]
    if chunk == b"VP8X":
        return 1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little")
    if chunk == b"VP8L":
        bits = struct.unpack("<I", b[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return struct.unpack("<HH", b[26:30])


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    env = {"RENDER_CACHE_DIR": os.environ.get("WOW_STATS_BROWSER_CACHE") or str(tmp_path_factory.mktemp("render")),
           "RENDER_TOKEN_FILE": str(tmp_path_factory.mktemp("token") / "render.token"), "BIND": "127.0.0.1",
           "PORT": "0"}
    Path(env["RENDER_TOKEN_FILE"]).write_text(TOKEN)
    r = renderer.Renderer(env)
    httpd = renderer.serve(r, env)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", r
    httpd.shutdown()


def post(url, body: bytes):
    try:
        req = urllib.request.Request(url + "/render", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(req,
                                    timeout=180) as resp:
            return resp.status, resp.headers["Content-Type"], resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def test_draws_a_character(server):
    url, r = server
    assert urllib.request.urlopen(url + "/healthz").status == 200
    code, ctype, img = post(url, json.dumps(GNOME).encode())
    assert code == 200 and ctype == "image/webp", img[:200]
    assert webp_size(img) == (renderer.WIDTH * renderer.SCALE, renderer.HEIGHT * renderer.SCALE)
    assert 5_000 < len(img) < 400_000, len(img)
    assert not r.blocked, f"Chromium tried to reach {r.blocked}"


def test_refuses_what_isnt_a_drawing(server):
    url, _ = server
    assert post(url, b'{"model": {"chr_model": 999, "gender": 0, "options": []}, "items": []}')[0] == 400
    assert post(url, b"x" * (renderer.MAX_BODY + 1))[0] == 400
