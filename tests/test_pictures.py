"""Past levels' pictures: the renderer's request carries a drawing and
nothing else, the publisher draws each look once, keeps the pictures beside
its builds, puts them in both bundles (sealed in /zogzog/), and a renderer
that is slow or down leaves "pending" and a site that still builds."""

import io
import json
import os
import threading
import time
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_public_view import PoisonStore
from wowstats import pictures, publish, renderer, ui

pytest.importorskip("cryptography")

# The smallest file that passes for WebP here: RIFF, a size, WEBP, a chunk.
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00/\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00"

GOOD = {"model": {"chr_model": 13, "gender": 0, "options": [{"optionId": 81, "choiceId": 1186}]},
        "items": [[5, 9871], [7, 222]]}


def test_renderer_takes_a_drawing_and_nothing_else():
    got = renderer.parse(json.dumps({**GOOD, "name": "Hero-Test", "level": 12,
                                     "model": {**GOOD["model"], "char": "Hero"}}).encode())
    assert got == GOOD, "unlisted fields are dropped, not passed on"
    for bad in ({"model": {**GOOD["model"], "chr_model": 999}, "items": []},  # not a playable model
                {"model": {**GOOD["model"], "chr_model": "13"}, "items": []},
                {"model": {**GOOD["model"], "gender": 2}, "items": []},
                {**GOOD, "items": [[2, 5]]},  # a neck: never drawn
                {**GOOD, "items": [[5, 1]] * 21},
                {**GOOD, "items": [[5, True]]},
                {"model": GOOD["model"]}):
        with pytest.raises(renderer.BadRequest):
            renderer.parse(json.dumps(bad).encode())
    with pytest.raises(renderer.BadRequest):
        renderer.parse(b"not json")
    # A Skyborne arrives as its stand-in, the Blood Elf (model3d.STAND_INS).
    assert renderer.parse(json.dumps({**GOOD, "model": {**GOOD["model"], "chr_model": 19}}).encode())
    with pytest.raises(renderer.BadRequest):
        renderer.parse(json.dumps({**GOOD, "model": {**GOOD["model"], "chr_model": 218}}).encode())


def test_request_is_cut_from_the_3d_answer():
    ans = {"race": "Gnome", "sex": "male", "class": "PRIEST", "level": 12,
           "model": {"chr_model": 13, "gender": 0, "options": [{"optionId": 81, "choiceId": 1186}]},
           "appearance": {"source": "battle.net", "refreshed": "2026-11-18"},
           "items": [{"slot": 5, "slot_name": "Chest", "item_id": 6098, "name": "Neophyte's Robe",
                      "inventory_type": 20, "display_id": 12647}],
           "missing": [{"slot": 1, "item_id": 7, "name": "x", "why": "y"}]}
    req = pictures.request(ans)
    assert req == {"model": {"chr_model": 13, "gender": 0, "options": [{"optionId": 81, "choiceId": 1186}]},
                   "items": [[20, 12647]]}
    assert renderer.parse(json.dumps(req).encode()) == req
    assert pictures.request({"model": None, "why": "no race"}) is None


def test_a_weapon_wowhead_lacks_travels_with_its_meta():
    """A meta Wowhead doesn't have (a weapon or armor Forever added) goes
    with the request: the renderer doesn't read Forever's tables to make it.
    Only for an item the drawing has; a stranger is refused."""
    ans = {"model": GOOD["model"], "items": [{"slot": 16, "inventory_type": 17, "display_id": 752138},
                                             {"slot": 5, "inventory_type": 20, "display_id": 12647}]}
    meta = {"Textures": {"2": 147522}, "ModelFiles": {"16577": [{"FileDataId": 147517, "Gender": 2}]}}
    req = pictures.request(ans, {752138: meta})
    assert req["metas"] == {"752138": meta}
    assert pictures.key(req) != pictures.key(pictures.request(ans)), "the weapon is a new picture"
    assert pictures.request(ans, {}) == pictures.request(ans), "no stand-in: the same key as before"
    got = renderer.parse(json.dumps(req).encode())
    assert json.loads(got["metas"]["752138"]) == meta
    # Armor Forever added travels the same way (model3d.armor_meta).
    assert set(renderer.parse(json.dumps({**req, "metas": {"12647": meta}}).encode())["metas"]) == {"12647"}
    for bad in ({"1": meta}, {"752138": [1]}, ["752138"]):
        with pytest.raises(renderer.BadRequest):
            renderer.parse(json.dumps({**req, "metas": bad}).encode())
    # Same look and gear, same key; a different item, another picture.
    assert pictures.key(req) == pictures.key(json.loads(json.dumps(req)))
    assert pictures.key(req) != pictures.key({**req, "items": [[20, 12648]]})


TOKEN = "t" * 43


class FakeRenderer:
    def __init__(self, code=200, delay=0.0):
        self.asked: list[bytes] = []
        self.code, self.delay = code, delay
        me = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if self.headers.get("Authorization") != "Bearer " + TOKEN:  # the real one: wowstats.bearer
                    self.send_response(401)
                    self.end_headers()
                    return
                me.asked.append(body)
                time.sleep(me.delay)
                self.send_response(me.code)
                self.send_header("Content-Type", "image/webp")
                self.end_headers()
                self.wfile.write(WEBP if me.code == 200 else b"no\n")

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The 3D upstreams of tests/test_model3d and the poisoned store, with
    a look from Battle.net: every level with gear has a model to draw."""
    from tests.test_model3d import Upstream

    class LookStore(PoisonStore):
        def vm_query(self, q):
            if "wow_character_customization" in q:
                return [{"metric": {"option_id": "1"}, "value": [1, "12"]}]
            if "wow_character_profile_refreshed" in q:
                return [{"metric": {}, "value": [1, "1795413600"]}]
            if "wow_character_profile{" in q:
                return [{"metric": {"gender": "male", "race_id": "7"}, "value": [1, "32"]}]
            return super().vm_query(q)

    def store(vl, archive="", vm="", *a, **k):
        s = LookStore()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    from wowstats.plan import facts

    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    up = Upstream()
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "on", **up.env(tmp_path),
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key"),
           "RENDER_TOKEN_FILE": str(tmp_path / "render.token")}
    (tmp_path / "render.token").write_text(TOKEN)
    yield env, tmp_path
    up.srv.shutdown()
    up.srv.server_close()


def run(env, force=False):
    with redirect_stdout(io.StringIO()) as out:
        res = publish.publish(env, force)
    return res, out.getvalue()


def snapshots(root):
    return {p.parent.name + "/" + p.stem: json.loads(p.read_text())
            for p in (root / "data" / "snapshot").rglob("*.json")}


def test_both_bundles_carry_each_levels_picture(world):
    env, tmp = world
    r = FakeRenderer()
    try:
        res, _ = run({**env, "RENDER_URL": r.url})
    finally:
        r.close()
    assert res["built"] and res["pictures"]["pending"] == 0 and res["pictures"]["drawn"] >= 1, res
    cur = tmp / "site" / "current"
    snaps = snapshots(cur / "public")
    drawn = {k for k, v in snaps.items() if (v.get("picture") or {}).get("ext") == "webp"}
    assert drawn, snaps
    for k in drawn:
        char, level = k.split("/")
        assert (cur / "public" / "data" / "picture" / char / f"{level}.webp").read_bytes() == WEBP
        sealed = cur / "zogzog" / "data" / "picture" / char / f"{level}.webp.enc"
        key = publish.read_key(env["ZOGZOG_KEY_FILE"])
        assert publish.unseal(key, f"data/picture/{char}/{level}.webp", sealed.read_bytes()) == WEBP
        assert WEBP not in sealed.read_bytes()
    # What the renderer was told: a drawing. No character, no item id or
    # name, no level, nothing of the store's poison.
    from tests.test_public_view import SECRETS

    for body in r.asked:
        req = json.loads(body)
        assert set(req) == {"model", "items"} and set(req["model"]) == {"chr_model", "gender", "options"}
        for s in ("Hero", "Test", *SECRETS):
            assert s.encode() not in body, (s, body)
    # One picture per look and gear: drawn once for both bundles.
    assert len(r.asked) == len({json.dumps(json.loads(b), sort_keys=True) for b in r.asked}) == res["pictures"]["drawn"]
    kept = list((tmp / "site" / "pictures").glob("*.webp"))
    assert len(kept) == res["pictures"]["drawn"]
    # The next build asks nothing: every picture is kept.
    r2 = FakeRenderer()
    try:
        res2, _ = run({**env, "RENDER_URL": r2.url}, force=True)
    finally:
        r2.close()
    assert res2["built"] and not r2.asked and res2["pictures"]["drawn"] == 0


def test_a_slow_or_absent_renderer_leaves_pending_and_tries_again(world):
    env, tmp = world
    state_f = tmp / "site" / "state.json"
    # Down: the site builds, every level with gear says pending, and the
    # next pass waits RETRY_S instead of rebuilding in a loop.
    res, out = run({**env, "RENDER_URL": "http://127.0.0.1:9"})
    assert res["built"] and res["pictures"]["drawn"] == 0 and res["pictures"]["pending"] >= 1, res
    assert "renderer is unreachable" in out
    snaps = snapshots(tmp / "site" / "current" / "public")
    assert any((v.get("picture") or {}).get("pending") for v in snaps.values())
    assert not list((tmp / "site" / "current" / "public" / "data").glob("picture/**/*.webp"))
    st = json.loads(state_f.read_text())
    assert st["pictures_pending"] == res["pictures"]["pending"] and st["pictures_retry_at"] > time.time() + 600
    r = FakeRenderer()
    try:
        assert run({**env, "RENDER_URL": r.url})[0]["built"] is False and not r.asked
        # Its time has come: a pass rebuilds for the pictures alone.
        state_f.write_text(json.dumps({**st, "pictures_retry_at": 0}))
        res, _ = run({**env, "RENDER_URL": r.url})
    finally:
        r.close()
    assert res["built"] and res["why"] == "level pictures pending" and res["pictures"]["pending"] == 0
    assert json.loads(state_f.read_text())["pictures_pending"] == 0


def test_a_build_spends_at_most_its_budget(world):
    env, tmp = world
    r = FakeRenderer(delay=0.3)
    try:
        res, _ = run({**env, "RENDER_URL": r.url, "RENDER_BUDGET_S": "0.1"})
    finally:
        r.close()
    # The first render goes over the budget: the rest wait for the next pass,
    # which comes at once since this one drew something.
    assert res["pictures"]["drawn"] == 1, res
    if res["pictures"]["pending"]:
        st = json.loads((tmp / "site" / "state.json").read_text())
        assert st["pictures_retry_at"] <= time.time()


def test_old_and_unused_pictures_go(world):
    env, tmp = world
    pics = tmp / "site" / "pictures"
    pics.mkdir(parents=True)
    (pics / ("0" * 64 + ".webp")).write_bytes(WEBP)  # a look no level has any more
    r = FakeRenderer()
    try:
        res, _ = run({**env, "RENDER_URL": r.url})
        drawn = res["pictures"]["drawn"]
        assert not (pics / ("0" * 64 + ".webp")).exists()
        # The 30-day rule: a picture carries the Battle.net look, so it is
        # drawn again once it is MAX_AGE_S old.
        old = time.time() - pictures.MAX_AGE_S - 60
        for f in pics.glob("*.webp"):
            os.utime(f, (old, old))
        res, _ = run({**env, "RENDER_URL": r.url}, force=True)
    finally:
        r.close()
    assert res["pictures"]["drawn"] == drawn


def test_no_renderer_no_picture_field(world):
    env, tmp = world
    res, _ = run(env)
    assert "pictures" not in res
    assert all(v.get("picture") is None for v in snapshots(tmp / "site" / "current" / "public").values())
