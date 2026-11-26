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

from tests import model3d_world as w
from tests.test_public_view import PoisonStore
from wowstats import pictures, publish, renderer, ui

pytest.importorskip("cryptography")

# The smallest file that passes for WebP here: RIFF, a size, WEBP, a chunk.
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00/\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00"

GOOD = {"recipe": w.SCENE["recipe"]}


def test_renderer_takes_a_recipe_and_nothing_else():
    got = renderer.parse(json.dumps({**GOOD, "name": "Hero-Test", "level": 12, "absent": [5, 7]}).encode())
    assert got == {"recipe": w.SCENE["recipe"], "absent": {5, 7}, "scope": ""}, \
        "unlisted fields are dropped, not passed on"
    r = w.SCENE["recipe"]
    for bad in ({"recipe": {**r, "model": "100001"}}, {"recipe": {**r, "model": -1}},
                {"recipe": {**r, "materials": [{"type": 1, "width": 99999, "height": 1, "layers": []}]}},
                {"recipe": {**r, "attachments": [{"slot": 16}] * 40}},
                {"recipe": {**r, "textures": {"x": 1}}},
                {"recipe": {**r, "model": True}},
                {**GOOD, "absent": ["a"]}, {}):
        with pytest.raises(renderer.BadRequest):
            renderer.parse(json.dumps(bad).encode())
    with pytest.raises(renderer.BadRequest):
        renderer.parse(b"not json")
    # Extra keys inside the recipe are dropped too: the page gets a recipe it built itself.
    assert "name" not in renderer.parse(json.dumps({"recipe": {**r, "name": "Hero"}}).encode())["recipe"]


def test_request_is_the_answers_recipe():
    ans = {"race": "Gnome", "sex": "male", "class": "PRIEST", "level": 12, "recipe": w.SCENE["recipe"],
           "recipe_sd": None, "appearance": {"source": "battle.net", "refreshed": "2026-11-18"},
           "items": [{"slot": 5, "slot_name": "Chest", "item_id": 6098, "name": "Neophyte's Robe"}],
           "missing": [{"slot": 1, "item_id": 7, "name": "x", "why": "y"}]}
    req = pictures.request(ans)
    assert req == {"recipe": w.SCENE["recipe"]}
    assert renderer.parse(json.dumps(req).encode())["recipe"] == req["recipe"]
    assert pictures.request({"recipe": None, "why": "no race"}) is None
    # Same recipe, same key; another one, another picture.
    assert pictures.key(req) == pictures.key(json.loads(json.dumps(req)))
    assert pictures.key(req) != pictures.key({"recipe": {**req["recipe"], "animation": 4}})


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
    """The made-up 3D world (tests/model3d_world.py) in the person's store
    and the poisoned event store, with a look from Battle.net: every level
    with gear has a recipe to draw."""
    class LookStore(PoisonStore):
        def vm_query(self, q):
            if "wow_character_customization" in q:
                return [{"metric": {"option_id": "85"}, "value": [1, "1236"]}]
            if "wow_character_profile_refreshed" in q:
                return [{"metric": {}, "value": [1, "1795413600"]}]
            if "wow_character_profile{" in q:
                return [{"metric": {"gender": "female", "race_id": "7"}, "value": [1, "32"]}]
            return super().vm_query(q)

    def store(vl, archive="", vm="", *a, **k):
        s = LookStore()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    from wowstats.plan import facts

    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    w.patch_tables(monkeypatch)
    w.fill(tmp_path / "assets")
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "on",
           "MODEL3D_CACHE_DIR": str(tmp_path / "cache"), "MODEL3D_ASSETS_DIR": str(tmp_path / "assets"),
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key"),
           "RENDER_TOKEN_FILE": str(tmp_path / "render.token")}
    (tmp_path / "render.token").write_text(TOKEN)
    yield env, tmp_path


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
        key = publish.read_key(env["ZOGZOG_KEY_FILE"])
        sealed = cur / "zogzog" / publish.sealed_name(key, f"data/picture/{char}/{level}.webp")
        assert publish.unseal(key, f"data/picture/{char}/{level}.webp", sealed.read_bytes()) == WEBP
        assert WEBP not in sealed.read_bytes()
    # What the renderer was told: a drawing. No character, no item id or
    # name, no level, nothing of the store's poison.
    from tests.test_public_view import SECRETS

    for body in r.asked:
        req = json.loads(body)
        # The scope keeps each person's files apart: a hash, never the account.
        assert set(req) == {"recipe", "absent", "scope"} and req["scope"] == pictures.scope(env)
        assert renderer.SCOPE.match(req["scope"]) and req["scope"] != env.get("VL_ACCOUNT_ID", "0")
        for s in ("Hero", "Test", *SECRETS):
            assert s.encode() not in body, (s, body)
    # One picture per look and gear: drawn once for both bundles.
    assert len(r.asked) == len({json.dumps(json.loads(b), sort_keys=True) for b in r.asked}) == res["pictures"]["drawn"]
    kept = {f.stem for f in (tmp / "site" / "pictures").glob("*.webp")}
    # Each drawing once, plus the still's own name when it looks like a level.
    assert res["pictures"]["drawn"] <= len(kept) <= res["pictures"]["drawn"] + 1
    # The next build asks nothing: every picture is kept.
    r2 = FakeRenderer()
    try:
        res2, _ = run({**env, "RENDER_URL": r2.url}, force=True)
    finally:
        r2.close()
    assert res2["built"] and not r2.asked and res2["pictures"]["drawn"] == 0


def test_a_missing_texture_arriving_rebuilds_the_cached_portrait(world):
    from tests.test_renderer import Drawer, start
    from wowstats import assets

    env, tmp = world
    texture = w.FX.TEX_MULT
    path = assets.Store(tmp / "assets").path(texture)
    data = path.read_bytes()
    path.unlink()

    class LayerDrawer(Drawer):
        def render(self, req):
            body = super().render(req)
            return body + (b"layer" if texture in self.read else b"")

    d = LayerDrawer(tmp / "renderer")
    srv, url, _ = start(tmp, d)
    env = {**env, "RENDER_URL": url}
    try:
        assert run(env)[0]["built"]
        assert any(texture in req["absent"] for req in d.drawn)
        old = (tmp / "site" / "current" / "public" / "data" / "render" / "Hero-Test.webp").read_bytes()
        count = len(d.drawn)
        assert not run(env)[0]["built"] and len(d.drawn) == count
        path.write_bytes(data)
        assert run(env)[0]["why"] == "picture files arrived"
        new = (tmp / "site" / "current" / "public" / "data" / "render" / "Hero-Test.webp").read_bytes()
        assert new == old + b"layer" and len(d.drawn) > count
        assert not run(env)[0]["built"]
    finally:
        srv.shutdown()
        srv.server_close()


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
    # No still either: the paperdoll keeps the silhouette and its 3D toggle.
    assert char_answer(tmp / "site" / "current" / "public")["gear"]["render"] is None


def char_answer(root, key=None):
    """The bundle's char answer for the poisoned store's character."""
    from tests.test_public_view import CHAR

    rel = f"data/char/{CHAR}.json"
    if key is None:
        return json.loads((root / rel).read_text())
    return json.loads(publish.unseal(key, rel, (root / publish.sealed_name(key, rel)).read_bytes()))


def test_the_paperdoll_still_is_ours_in_both_bundles(world):
    """The paperdoll's picture is our renderer's still of the 3D model as
    the character is now (owner, 2026-11-21: never Blizzard's render, which
    once showed a stranger). Both bundles carry it, the char answer says so,
    and it is kept per tenant, character and look."""
    from tests.test_public_view import CHAR

    env, tmp = world
    r = FakeRenderer()
    try:
        res, _ = run({**env, "RENDER_URL": r.url})
    finally:
        r.close()
    cur = tmp / "site" / "current"
    rel = f"data/render/{CHAR}.webp"
    assert (cur / "public" / rel).read_bytes() == WEBP
    assert (char_answer(cur / "public")["gear"]["render"] or {}).get("ext") == "webp"
    key = publish.read_key(env["ZOGZOG_KEY_FILE"])
    assert publish.unseal(key, rel, (cur / "zogzog" / publish.sealed_name(key, rel)).read_bytes()) == WEBP
    assert (char_answer(cur / "zogzog", key)["gear"]["render"] or {}).get("ext") == "webp"
    assert not list(cur.rglob("*.png")), "no Blizzard render, nor any image we didn't draw"
    # Kept under its tenant and character: the scoped name is on disk.
    full = ui.App({**env, "VIEW": "full", "BIND": "127.0.0.1", "AUTH": "none"})
    req = pictures.request(publish.model_answer(full, CHAR, None))
    scope = f"{getattr(full.store, 'account_id', 0)}\t{CHAR}"
    assert (tmp / "site" / "pictures" / f"{pictures.key(req, scope)}.webp").read_bytes() == WEBP


def test_the_still_says_pending_while_the_renderer_is_down(world):
    env, tmp = world
    res, _ = run({**env, "RENDER_URL": "http://127.0.0.1:9"})
    cur = tmp / "site" / "current"
    assert (char_answer(cur / "public")["gear"]["render"] or {}).get("pending") is True
    assert not list((cur / "public" / "data").glob("render/*"))


def test_a_still_belongs_to_one_tenant_and_character():
    """Same look, another person or character: another name, so nobody's
    still is ever served for someone else's; the same drawing is copied,
    not drawn again (Pictures.get)."""
    req = {"recipe": w.SCENE["recipe"]}
    names = {pictures.key(req), pictures.key(req, "0\tHero-Test"), pictures.key(req, "1\tHero-Test"),
             pictures.key(req, "0\tOther-Test")}
    assert len(names) == 4
    assert pictures.key(req, "0\tHero-Test") != pictures.key({"recipe": {**req["recipe"], "animation": 4}},
                                                               "0\tHero-Test")
