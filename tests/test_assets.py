"""The 3D view's game files sent by the shipper (wowstats.assets, the
/install/assets routes, shipper ship_assets): the upload password, the
limits, one file id stored once, each person's files in their own folder
and gone with them. Every file here is made-up bytes (tests/casc_fixture.py)."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
from pathlib import Path

import pytest

from tests import casc_fixture as cf
from tests.test_shipper_casc import BODY, CHAR, ITEM_DB2, PARENT, PSTAND, SKEL, SKIN0, STAND, TEX
from tests.test_shipper_casc import install as casc_install
from tests.test_tenancy import FRIEND, OWNER, TenantVL, as_user
from wowstats import assets, tenancy, ui

PW = "assets-password-long-enough"
BUILD = "1.60.1.70959"
AUTH = {"Authorization": ui._basic("wow", PW).decode()}


def table(name: str) -> bytes:
    return b"WDC5" + name.encode() * 8


def model() -> bytes:
    return cf.m2(skins=(SKIN0,), textures=(TEX,), skel=SKEL, anims=((0, 0, STAND),))


def post(srv, path, body, headers=None, sha=True):
    h = {**AUTH, **(headers or {})}
    if sha:
        h["X-Content-SHA256"] = hashlib.sha256(body).hexdigest()
    return srv.get(path, headers=h, method="POST", data=body)


@pytest.fixture
def one(run_app, tmp_path):
    app = ui.App({"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "none", "VL_URL": "http://vl.invalid",
                  "INGEST_PASSWORD": PW, "MCP_PUBLIC_URL": "", "MODEL3D_CACHE_DIR": str(tmp_path / "cache"),
                  "MODEL3D_ASSETS_DIR": str(tmp_path / "assets")})
    srv = run_app(app)
    srv.app = app
    return srv


def test_store_tables_follow_the_build(tmp_path):
    s = assets.Store(tmp_path)
    assert s.tables_needed(BUILD) == assets.TABLES
    s.put_table("ChrModel", BUILD, table("ChrModel"))
    assert "ChrModel" not in s.tables_needed(BUILD)
    assert s.tables_needed("1.60.1.99999") == assets.TABLES  # another build wants them all again
    s.put_table("Item", "1.60.1.99999", table("Item"))
    assert s.table("ChrModel") is None and s.table("Item") == table("Item")  # the old build's went
    for name, build, data, status in (("NotATable", BUILD, table("x"), 404), ("Item", "latest", table("x"), 400),
                                      ("Item", BUILD, b"BLP2" + bytes(8), 415)):
        with pytest.raises(assets.Refused) as e:
            s.put_table(name, build, data)
        assert e.value.status == status


def test_store_room_and_dedupe(tmp_path):
    s = assets.Store(tmp_path, max_mb=1)
    assert s.put_file(CHAR, model()) == "m2"
    used = s.used()
    s.put_file(CHAR, model() + b"different bytes, same id")  # a file id's content never changes: kept once
    assert s.used() == used
    with pytest.raises(assets.Refused) as e:
        s.put_file(BODY, b"BLP2" + bytes(1024 * 1024))
    assert e.value.status == 507 and s.path(BODY) is None
    assert s.refs() == {SKIN0, TEX, SKEL, STAND}


def test_model_refs_match_the_shippers(shipper):
    for data in (model(), cf.m2(skins=(1, 2), textures=(3, 4), skel=0, anims=((0, 0, 5), (4, 0, 6), (0, 2, 7))),
                 cf.skel(parent=PARENT, anims=((0, 0, 8), (1, 0, 9))), cf.skel(), b"MD21", b""):
        assert assets.model_refs(data) == shipper.model_refs(data)
    # Both follow the closed hands (15, the viewer's HandsClosed), never another animation.
    hands = cf.skel(parent=PARENT, anims=((0, 0, 8), (15, 0, 10), (15, 3, 11), (4, 0, 12)))
    assert assets.model_refs(hands) == shipper.model_refs(hands) == {PARENT, 8, 10, 11}


def test_routes_on_a_one_person_server(one):
    app = one.app
    assert one.get(f"/install/assets/need?build={BUILD}")[0] == 401
    assert one.get("/install/assets/need?build=latest", headers=AUTH)[0] == 400
    code, _, body = one.get(f"/install/assets/need?build={BUILD}", headers={**AUTH, "Host": "any-name.example"})
    need = json.loads(body)
    assert code == 200 and need["tables"] == assets.TABLES and need["files"] == []
    assert post(one, f"/install/assets/table/ChrModel?build={BUILD}", table("ChrModel"))[0] == 200
    assert post(one, f"/install/assets/table/ChrModel?build={BUILD}", b"nope")[0] == 415

    app.assets.wanted = lambda: {CHAR}  # the character assembly's answer
    need = json.loads(one.get(f"/install/assets/need?build={BUILD}", headers=AUTH)[2])
    assert need["files"] == [CHAR] and "ChrModel" not in need["tables"]
    assert post(one, f"/install/assets/file/{BODY}", b"BLP2" + bytes(64))[0] == 409  # never asked for
    assert post(one, f"/install/assets/file/{CHAR}", model(), {"X-Content-SHA256": "0" * 64}, sha=False)[0] == 400
    assert post(one, f"/install/assets/file/{CHAR}", b"PK\x03\x04 not a model")[0] == 415
    app.assets.wanted = lambda: {CHAR, BODY}
    code, _, body = post(one, f"/install/assets/file/{CHAR}", model())
    assert code == 200 and json.loads(body) == {"stored": CHAR, "kind": "m2"}
    # What the model names comes next, without the character assembly knowing it.
    need = json.loads(one.get(f"/install/assets/need?build={BUILD}", headers=AUTH)[2])
    assert need["files"] == sorted({BODY, SKIN0, TEX, SKEL, STAND})
    assert post(one, f"/install/assets/file/{SKEL}", cf.skel(parent=PARENT))[0] == 200
    # Over the size limit: refused on the Content-Length, before the body.
    c = http.client.HTTPConnection(one.base.split("//", 1)[1], timeout=10)
    c.request("POST", f"/install/assets/file/{TEX}", headers={**AUTH, "Content-Length": str(assets.MAX_FILE + 1)})
    assert c.getresponse().status == 413
    c.close()
    assert one.get(f"/install/assets/file/{TEX}", headers=AUTH)[0] == 404  # nothing to read back
    assert (Path(app.assets.store.root) / "files" / f"{CHAR}.m2").read_bytes() == model()


def test_routes_off_without_the_upload_password_or_the_3d_view(run_app, tmp_path):
    base = {"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "none", "VL_URL": "http://vl.invalid", "MCP_PUBLIC_URL": "",
            "MODEL3D_CACHE_DIR": str(tmp_path / "cache")}
    for env in (base, {**base, "INGEST_PASSWORD": PW, "MODEL3D": "off"}):
        srv = run_app(ui.App(env))
        assert srv.get(f"/install/assets/need?build={BUILD}", headers=AUTH)[0] == 404


@pytest.fixture
def several(run_app, tmp_path):
    vl = TenantVL()
    master = tmp_path / "master.key"
    master.write_text(tenancy.b64u(b"m" * 32))
    friends = tmp_path / "friends.json"
    friends.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    env = {"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "proxy", "PROXY_TRUSTED_CIDRS": "127.0.0.1/32",
           "VL_URL": vl.url, "VM_URL": vl.url, "FRIENDS_VL_URL": vl.url, "WOW_STATE_DIR": str(tmp_path / "state"),
           "WOW_OWNER_SUB": OWNER, "WOW_OWNER_SLUG": "sam", "WOW_FRIENDS_FILE": str(friends),
           "SITE_DOMAIN": "m.example", "PUBLIC_URL": "https://wow-full.m.example",
           "ZOGZOG_MASTER_KEY_FILE": str(master), "ZOGZOG_KEY_FILE": str(tmp_path / "owner-zogzog.key"),
           "MODEL3D_CACHE_DIR": str(tmp_path / "cache"), "ANALYSIS_STATE_DIR": str(tmp_path / "owner-analysis"),
           "INGEST_DAILY_MB": str(3000 / 1024 / 1024)}
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    srv = run_app(app)
    srv.srv.RequestHandlerClass = ui.make_handler(app, t)
    srv.t = t
    for sub in (FRIEND, "friend-3"):
        srv.get("/api/me", headers=as_user(sub))
    yield srv
    vl.close()
    assert vl.untagged == []


def auth_of(srv, sub, slug):
    _, token = srv.t.registry.redeem(srv.t.registry.new_code(sub))
    return {"Authorization": "Basic " + base64.b64encode(f"{slug}:{token}".encode()).decode()}


def test_each_person_their_own_files(several):
    t = several.t
    mira, pip = t.registry.get(FRIEND), t.registry.get("friend-3")
    a_mira, a_pip = auth_of(several, FRIEND, "mira"), auth_of(several, "friend-3", "pip")
    t.app_for(mira).assets.wanted = lambda: {CHAR}
    t.app_for(pip).assets.wanted = lambda: set()
    data = model()
    sha = {"X-Content-SHA256": hashlib.sha256(data).hexdigest()}
    assert several.get(f"/install/assets/file/{CHAR}", headers={**a_mira, **sha}, method="POST", data=data)[0] == 200
    assert (t.user_dir(mira) / "assets" / "files" / f"{CHAR}.m2").is_file()
    # Pip's server never asked for it and never sees Mira's.
    assert several.get(f"/install/assets/file/{CHAR}", headers={**a_pip, **sha}, method="POST", data=data)[0] == 409
    assert t.app_for(pip).assets.store.have() == set()
    need = json.loads(several.get(f"/install/assets/need?build={BUILD}", headers=a_pip)[2])
    assert need["files"] == []
    # The day's budget is /ingest's (3000 bytes here): spent by what was stored.
    big = b"BLP2" + bytes(2990)
    t.app_for(mira).assets.wanted = lambda: {CHAR, BODY, TEX}
    h = {**a_mira, "X-Content-SHA256": hashlib.sha256(big).hexdigest()}
    assert several.get(f"/install/assets/file/{BODY}", headers=h, method="POST", data=big)[0] == 429
    assert several.get(f"/install/assets/file/{BODY}", headers={**a_pip, "X-Content-SHA256": h["X-Content-SHA256"]},
                       method="POST", data=big)[0] == 409  # pip's own budget, pip's own rules
    # Removing Mira takes her files.
    tenancy.delete_tenant(t, mira)
    assert not (t.user_dir(mira) / "assets").exists()


def test_the_shipper_sends_what_the_server_asks(one, shipper, tmp_path, monkeypatch):
    root = casc_install(tmp_path, shipper)
    monkeypatch.setattr(shipper, "WOW", root / "_forever_")
    monkeypatch.setattr(shipper, "LOG", tmp_path / "wow-stats.log")
    monkeypatch.setattr(shipper, "DRY", False)
    monkeypatch.setattr(shipper, "CONFIG", {"ship": {"url": one.base + "/ingest", "user": "wow", "password": PW}})
    store = one.app.assets.store
    one.app.assets.wanted = lambda: {CHAR, BODY}
    # Tables go by file id (Forever's root has no names): the made-up
    # install has Item at ITEM_DB2, and no ChrModel.
    monkeypatch.setattr(assets, "TABLES", {"Item": ITEM_DB2, "ChrModel": 999999})
    state: dict = {}
    assert shipper.ship_assets(state, now=1000) == "sent"
    # Rounds: the character and the texture asked for, then the skin,
    # texture, skeleton and animation the model names, the parent skeleton,
    # its animation. Never the second skin or the walk.
    assert store.have() == {CHAR, BODY, SKIN0, TEX, SKEL, PARENT, STAND, PSTAND}
    assert store.table("Item") == b"WDC5" + b"rows" * 50 and store.build() == "1.60.1.70959"
    assert store.table("ChrModel") is None and ITEM_DB2 not in store.have()
    assert shipper.ship_assets(state, now=1001) is None  # 15 minutes between runs
    assert shipper.ship_assets(state, now=1000 + shipper.ASSETS_EVERY_S) == "nothing new"
    monkeypatch.setattr(shipper, "CONFIG", {**shipper.CONFIG, "model3d_files": False})
    assert shipper.ship_assets({}, now=0) is None


def test_a_forever_hd_character_model_fits():
    """Forever's HD character models are 17-28 MB with their animations
    (rc.2 on the lab: four of them over a 16 MB cap, never sent)."""
    assert assets.MAX_FILE >= 32 * 1024 * 1024
