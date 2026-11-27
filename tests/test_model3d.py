"""The 3D view's server side (wowstats.model3d): the answer is a recipe
assembled from the person's own client tables (character3d), the files come
from the person's own store (/model3d/file/<id>), the frame runs our viewer
without eval, and nobody reads another person's answer or files. Made-up
tables and files only (tests/model3d_world.py)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests import model3d_world as w
from tests.test_assets import several  # noqa: F401 - a fixture
from tests.test_public_view import (
    CHAR,
    fake_extras,  # noqa: F401 - a fixture
    make,
)
from tests.test_tenancy import FRIEND, as_user
from wowstats import assets, character3d, model3d, redact, ui

# A full view loads the MCP module when it is installed: a stand-in here.
pytestmark = pytest.mark.usefixtures("fake_extras")
SHEET = {"race": "Gnome", "sex": "female", "class": "PRIEST", "items": w.GEAR}


@pytest.fixture(autouse=True)
def tables(monkeypatch):
    w.patch_tables(monkeypatch)


@pytest.fixture
def m3d(tmp_path):
    store = w.fill(tmp_path / "assets")
    m = model3d.Model3D({"MODEL3D_CACHE_DIR": str(tmp_path / "cache")}, ui.static_dir())
    m.assets = type("A", (), {"store": store})()
    return m


def full(tmp_path, **env):
    return make("full", MODEL3D_CACHE_DIR=str(tmp_path / "cache"), MODEL3D_ASSETS_DIR=str(tmp_path / "assets"), **env)


# --- the answer -------------------------------------------------------------

def test_the_answer_is_a_recipe_from_the_persons_tables(m3d):
    d = m3d.character(SHEET)
    assert (d["race"], d["sex"], d["sex_known"], d["class"]) == ("Gnome", "female", True, "PRIEST")
    assert d["recipe"]["model"] == w.FX.CHAR_M2 and d["recipe"]["v"] == 1
    assert d["recipe_sd"]["model"] == w.SD_M2, "the original model is in the store too"
    # The boots are a collection model, the sword an attachment; a neck is
    # never drawn (quietly); an item the tables don't know is said.
    assert {i["item_id"] for i in d["items"]} == {w.BOOTS, w.SWORD}
    assert d["missing"] == [] and "why" not in d
    d = m3d.character({**SHEET, "items": [*w.GEAR, {"slot": 5, "slot_name": "Chest", "item_id": 555,
                                                    "name": "Test Robe"}]})
    assert d["missing"] == [{"slot": 5, "slot_name": "Chest", "item_id": 555, "name": "Test Robe",
                             "why": "not in Forever's item tables"}]
    # Every field the page reads, and nothing of Wowhead's.
    assert {"race", "sex", "sex_known", "class", "recipe", "recipe_sd", "no_classic", "items", "missing",
            "appearance"} <= set(d)
    assert not {"model", "model_sd", "stand_in", "source"} & set(d)


def test_observed_companion_uses_only_the_current_shipper_sheet(m3d, monkeypatch):
    tables = w.world()
    tables["CreatureDisplayInfo"][70001] = {"ModelID": 60411, "TextureVariationFileDataID": [801, 0, 803, 804]}
    tables["CreatureModelData"][60411] = {"FileDataID": w.FX.CHAR_M2}
    monkeypatch.setattr(m3d, "tables", lambda: tables)
    rows = [{"last": "2026-11-01T12:00:00Z", "display_id": "70002"},
            {"last": "2026-11-01T12:00:00.5Z", "display_id": "70001"}]
    queries = []

    def read(q, *args):
        queries.append(q)
        return rows

    hunter = {**SHEET, "class": "HUNTER"}
    app = SimpleNamespace(store=SimpleNamespace(stream="feed:wow-*", vl=read),
                          char=lambda name: {"gear": hunter},
                          views=SimpleNamespace(char_filter=lambda name: f'char:="{name}"',
                                                snapshot=lambda name, level: {"gear": hunter}))
    assert "companion" not in m3d.for_char(app, "Example")["recipe"]
    texture = next(data for data in w.files().values() if data.startswith(b"BLP2"))
    for file_id in (801, 803):
        m3d.store.put_file(file_id, texture)
    answer = m3d.for_char(app, "Example")
    pet = {"kind": 1, "model": w.FX.CHAR_M2, "textures": {"11": 801, "13": 803}}
    assert answer["recipe"]["companion"] == answer["recipe_sd"]["companion"] == pet
    assert 'feed:="wow-companion" char:="Example" kind:=hunter' in queries[0]
    assert {801, 803} <= character3d.needs(answer["recipe"])
    gear, header, _ = m3d.gear_at(app, "Example", None)
    assert {801, 803} <= m3d.needs(gear, header, None)

    queries.clear()
    assert "companion" not in m3d.for_char(app, "Example", level=20)["recipe"]
    assert queries == []
    rows.append({"last": "2026-11-01T13:00:00Z", "display_id": "0"})
    assert "companion" not in m3d.for_char(app, "Example")["recipe"]
    rows.append({"last": "2026-11-01T13:00:00Z", "display_id": "70001"})
    assert model3d.companion_display(app, "Example", "WARLOCK") == (2, 70001)
    assert "kind:=warlock" in queries[-1]


def test_current_and_snapshot_actions_use_the_level_being_drawn(m3d, monkeypatch):
    monkeypatch.setattr(model3d, "find_look", lambda app, name: None)
    app = SimpleNamespace(char=lambda name: {"gear": SHEET, "header": {"level": 30}},
                          views=SimpleNamespace(snapshot=lambda name, level: {"gear": SHEET}))
    for level, expected in [(None, 30), (5, 5)]:
        answer = m3d.for_char(app, "Example", level=level)
        for recipe in (answer["recipe"], answer["recipe_sd"]):
            assert recipe["level"] == expected
            assert "level" not in character3d.standing_recipe(recipe)


def test_owned_mount_requests_its_skin_and_stays_out_of_old_portraits(m3d, monkeypatch):
    tables = w.world()
    tables["CreatureDisplayInfo"][70002] = {"ModelID": 23, "CreatureModelScale": 1.2, "TextureVariationFileDataID": [801]}
    tables["CreatureModelData"][23] = {"FileDataID": w.FX.BELT_M2, "ModelScale": 1}
    monkeypatch.setattr(m3d, "tables", lambda: tables)
    monkeypatch.setattr(model3d.mounts, "owned", lambda *_: [{"name": "Swift Wolf", "display_id": 70002},
                                                           {"name": "Unknown", "display_id": 70003}])
    app = SimpleNamespace(char=lambda name: {"gear": SHEET},
                          views=SimpleNamespace(snapshot=lambda name, level: {"gear": SHEET}))
    gear, header, _ = m3d.gear_at(app, "Example", None)
    assert {w.FX.BELT_M2, 801} <= m3d.needs(gear, header, None)
    assert "mounts" not in m3d.for_char(app, "Example")["recipe"]
    m3d.store.put_file(801, next(data for data in w.files().values() if data.startswith(b"BLP2")))
    mount = {"name": "Swift Wolf", "model": w.FX.BELT_M2, "scale": 1.2, "textures": {"11": 801}}
    answer = m3d.for_char(app, "Example")
    for recipe in (answer["recipe"], answer["recipe_sd"]):
        assert recipe["mounts"] == [mount]
        assert 801 in character3d.needs(recipe)
        assert 801 not in character3d.needs(character3d.standing_recipe(recipe))
    assert "mounts" not in m3d.for_char(app, "Example", level=12)["recipe"]

    # Neither public nor friends needs to know how or where a mount was learned.
    answer["recipe"]["mounts"] = [{**mount, "character": "PRIVATE", "learned_at": "PRIVATE",
                                   "textures": {"11": 801, "source": "PRIVATE"}}]
    for share in (redact.public, redact.zogzog):
        safe = share("model3d", answer)
        assert "PRIVATE" not in json.dumps(safe)
        assert safe["recipe"]["mounts"] == [{**mount, "textures": {"11": 801, "12": None, "13": None}}]


def test_historical_equipment_uses_the_dated_latest_face_without_todays_pet_or_mount(m3d, monkeypatch):
    look = {"options": [{"optionId": 87, "choiceId": 1262}], "source": "battle.net", "refreshed": "2026-11-27"}
    monkeypatch.setattr(model3d, "find_look", lambda app, name: look)
    current = {**SHEET, "mounts": [{"display_id": 70002}], "companion_display": (1, 70001)}
    app = SimpleNamespace(char=lambda name: {"gear": current},
                          views=SimpleNamespace(snapshot=lambda name, level: {"gear": {"items": w.GEAR}}))
    sheet, _, _ = m3d.gear_at(app, "Example", 12)
    assert "mounts" not in sheet and "companion_display" not in sheet
    answer = m3d.for_char(app, "Example", 12)
    assert answer["recipe"]["level"] == 12
    for shared in (redact.public, redact.zogzog):
        assert shared("model3d", answer)["appearance"] == {"source": "battle.net", "refreshed": "2026-11-27",
                                                            "scope": "latest_profile"}


def test_the_enchant_reaches_the_weapon(m3d):
    plain = m3d.character(SHEET)["recipe"]
    glowing = m3d.character({**SHEET, "items": [{**w.GEAR[1], "enchant_id": 1}]})["recipe"]
    sword = [a for a in glowing["attachments"] if a["slot"] == 16]
    assert sword and plain != glowing, "the enchant id changes the weapon's visuals"


def test_classic_says_which_choices_it_lacks(m3d):
    d = m3d.character(SHEET, look={"options": [{"optionId": 87, "choiceId": 1262}], "source": "battle.net",
                                   "refreshed": "2026-11-18"})
    assert d["no_classic"] == ["Hair Style"] and d["appearance"]["source"] == "battle.net"
    assert m3d.character(SHEET)["no_classic"] == []


def test_male_has_no_original_model_here(m3d):
    d = m3d.character({**SHEET, "sex": "male"})
    assert d["recipe"] and d["recipe_sd"] is None and d["no_classic"] == []


def test_sex_unknown_takes_the_pick(m3d):
    d = m3d.character({**SHEET, "sex": None}, sex_override="female")
    assert (d["sex"], d["sex_known"]) == ("female", False) and d["recipe_sd"]
    d = m3d.character({**SHEET, "sex": None})
    assert (d["sex"], d["sex_known"]) == ("male", False)
    # Battle.net's gender, when no sheet has it.
    assert m3d.character({**SHEET, "sex": None}, look={"gender": "female"})["sex_known"] is True


@pytest.mark.parametrize("sheet,why", [
    ({**SHEET, "race": "Orc"}, "no model for the race Orc"),
    ({**SHEET, "race": None}, "the race isn't known yet"),
])
def test_no_recipe_says_why(m3d, sheet, why):
    d = m3d.character(sheet)
    assert d["recipe"] is None and d["recipe_sd"] is None and why in d["why"]


def test_no_tables_or_no_model_file_says_why(tmp_path):
    m = model3d.Model3D({"MODEL3D_CACHE_DIR": str(tmp_path / "cache")}, ui.static_dir())
    m.assets = type("A", (), {"store": w.fill(tmp_path / "none", with_files=False)})()
    d = m.character(SHEET)
    assert d["recipe"] is None and d["why"] == "the shipper sends this model's files from your computer on its next run"
    empty = model3d.Model3D({"MODEL3D_CACHE_DIR": str(tmp_path / "c2")}, ui.static_dir())
    empty.assets = type("A", (), {"store": assets.Store(tmp_path / "blank")})()
    d = empty.character(SHEET)
    # One sentence on the stage: "No 3D model yet: <why>." (model3d.js whyLine), one colon.
    assert d["recipe"] is None and d["why"] == "the shipper sends the game files from your computer on its next run"
    assert ":" not in d["why"]


def test_an_item_whose_files_are_late_is_listed(tmp_path):
    store = w.fill(tmp_path / "assets")
    for f in (tmp_path / "assets" / "files").glob(f"{w.FX.BELT_M2}.*"):
        f.unlink()
    m = model3d.Model3D({"MODEL3D_CACHE_DIR": str(tmp_path / "cache")}, ui.static_dir())
    m.assets = type("A", (), {"store": store})()
    d = m.character(SHEET)
    assert d["recipe"] and w.BOOTS not in {i["item_id"] for i in d["items"]}
    assert any(r["item_id"] == w.BOOTS and "haven't come" in r["why"] for r in d["missing"])


def test_needs_cover_both_models_and_both_sexes_when_unknown(m3d):
    known = m3d.needs(SHEET, None, None)
    unknown = m3d.needs({**SHEET, "sex": None}, None, None)
    assert {w.FX.CHAR_M2, w.SD_M2} <= known and known <= unknown
    assert m3d.needs({**SHEET, "race": "Orc"}, None, None) == set()


# --- routes -----------------------------------------------------------------

def test_api_and_files_on_a_full_view(run_app, tmp_path):
    w.fill(tmp_path / "assets")
    srv = run_app(full(tmp_path))
    code, _, body = srv.get(f"/api/model3d?name={CHAR}&sex=female")
    d = json.loads(body)
    assert code == 200 and d["recipe"]["model"] == w.FX.CHAR_M2 and d["models"] == "hd"
    code, headers, body = srv.get(f"/model3d/file/{w.FX.CHAR_M2}")
    assert code == 200 and body == w.files()[w.FX.CHAR_M2]
    assert headers["Content-Type"] == "application/octet-stream" and headers["Cache-Control"].startswith("private")
    for path in ("/model3d/file/999999", "/model3d/file/abc", "/model3d/file/12345678901", "/model3d/file/",
                 "/model3d/zam/r2/viewer/viewer.min.js", "/model3d/jquery-3.7.1.min.js"):
        assert srv.get(path)[0] == 404, path
    assert srv.get(f"/model3d/file/{w.FX.CHAR_M2}", method="POST", data=b"x")[0] == 404


def test_the_public_view_has_none_of_it(run_app, tmp_path):
    w.fill(tmp_path / "assets")
    srv = run_app(make("public", MODEL3D_ASSETS_DIR=str(tmp_path / "assets")))
    for path in ("/model3d/view", "/model3d/view.js", "/model3d/model3d.js", f"/model3d/file/{w.FX.CHAR_M2}",
                 f"/api/model3d?name={CHAR}"):
        assert srv.get(path)[0] == 404, path


def test_the_frame_runs_without_eval(run_app, tmp_path):
    srv = run_app(full(tmp_path))
    _, headers, page = srv.get("/")
    assert "unsafe-eval" not in headers["Content-Security-Policy"] and b"model3d/model3d.js" in page
    code, headers, body = srv.get("/model3d/view")
    csp = headers["Content-Security-Policy"]
    assert code == 200 and "sandbox allow-scripts" in csp and "frame-ancestors 'self'" in csp
    for word in ("unsafe-eval", "wasm-unsafe-eval", "zamimg", "http", "unsafe-inline"):
        assert word not in csp, word
    assert "connect-src 'none'" in csp
    html = body.decode()
    assert "M3D.draw" in html and "ZamModelViewer" not in html and "jquery" not in html.lower()
    # The renderer's copy reads its own files: connect-src 'self', still no eval.
    render = model3d.view_csp(model3d.view_page(ui.static_dir() / "model3d", render=True), render=True)
    assert "connect-src 'self'" in render and "eval" not in render


def test_each_person_their_own_3d(several):  # noqa: F811
    t = several.t
    mira, pip = t.registry.get(FRIEND), t.registry.get("friend-3")
    w.fill(t.user_dir(mira) / "assets")
    a_mira, a_pip = t.app_for(mira).model3d, t.app_for(pip).model3d
    assert a_mira.character(SHEET)["recipe"], "Mira's own files draw her"
    d = a_pip.character(SHEET)
    assert d["recipe"] is None and d["why"], "Pip's server has no tables of its own: Mira's never answer for Pip"
    path = f"/model3d/file/{w.FX.CHAR_M2}"
    code, _, body = several.get(path, headers=as_user(FRIEND))
    assert code == 200 and body == w.files()[w.FX.CHAR_M2]
    assert several.get(path, headers=as_user("friend-3"))[0] == 404
    assert several.get(path)[0] in (302, 401, 403), "no login, no file"


def test_wanted_is_every_characters_needs(run_app, tmp_path):
    w.fill(tmp_path / "assets")
    app = full(tmp_path)
    got = app.model3d.wanted(app)
    assert w.FX.CHAR_M2 in got and w.SD_M2 in got
    assert app.assets.wanted() == got


def test_the_publisher_reads_each_persons_files_through_the_share_port(several, tmp_path):  # noqa: F811
    """The publisher's pod has no disk of the full view's: it reads one
    person's tables and files from the share port, with its token, under
    that person's slug only (publish.user_env sets the address)."""
    import urllib.error
    import urllib.request

    from wowstats import analysis, publish

    t = several.t
    mira = t.registry.get(FRIEND)
    w.fill(t.user_dir(mira) / "assets")
    e = {"ANALYSIS_SHARE_PORT": "0", "ANALYSIS_SHARE_TOKEN_FILE": str(tmp_path / "share-token")}
    srv = analysis.share_server(e, t)
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        pe = publish.user_env({**e, "USERS_URL": base + "/users.json", "FRIENDS_VL_URL": "http://vl.invalid"},
                              {"slug": "mira", "account_id": 1})
        assert pe["MODEL3D_ASSETS_URL"] == base + "/assets/mira/"
        store = assets.RemoteStore(pe["MODEL3D_ASSETS_URL"], pe, tmp_path / "pub-cache")
        assert store.build() == w.BUILD and store.table("ChrModel") == b"WDC5 made-up"
        assert store.read(w.FX.CHAR_M2) == w.files()[w.FX.CHAR_M2] and store.read(424242) is None
        # Pip's slug reads Pip's folder: none of Mira's.
        pip = assets.RemoteStore(base + "/assets/pip/", pe, tmp_path / "pub-cache")
        assert pip.read(w.FX.CHAR_M2) is None and pip.build() == ""
        for path in (f"/assets/mira/file/{w.FX.CHAR_M2}", "/assets/nobody/build", "/assets/../x/build"):
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(base + path, timeout=10)  # no token
            assert err.value.code in (401, 404), path
    finally:
        srv.shutdown()
        srv.server_close()


def test_wanted_covers_every_outfit_of_the_history_once(tmp_path, monkeypatch):
    """Now, each past level, every gear sheet the shipper sent and every
    point of the Recorder's replay (an outfit worn for half a level too);
    the same outfit is worked out once."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from wowstats import history

    def outfit(*ids):
        return [{"slot": 5, "item_id": i} for i in ids]

    rp = history.Replay(start=datetime(2026, 11, 1, tzinfo=UTC))
    for day, iid in ((1, 400), (2, 500), (3, 200)):  # 500 replaced the next day, inside one level
        rp.put(5, history.Span(datetime(2026, 11, day, tzinfo=UTC), iid, {}))
    sheets = {None: outfit(100), 22: outfit(200), 23: outfit(100)}
    views = SimpleNamespace(
        snapshot=lambda name, lv: {"gear": {"items": sheets[lv]}},
        snapshot_inputs=lambda name: ({}, {}, [], [{"items": outfit(200)}, {"items": outfit(300)},
                                                   {"items": outfit(100)}, {"items": []}], []),
        reconstruction=lambda name, data, gh: (rp, {}, {}))
    app = SimpleNamespace(
        chars=lambda: {"chars": [{"char": "Testchar"}]}, valid_char=lambda n: True,
        char=lambda n: {"gear": {"race": "Gnome", "class": "PRIEST", "items": outfit(100)}, "header": {}},
        timeline=lambda n: {"levels": [22, 23]}, views=views)
    monkeypatch.setattr(model3d, "find_look", lambda app, name: None)
    m = model3d.Model3D({"MODEL3D_CACHE_DIR": str(tmp_path)}, tmp_path, None)
    seen = []
    monkeypatch.setattr(m, "needs", lambda gear, header, look: seen.append(gear["items"][0]["item_id"])
                        or {gear["items"][0]["item_id"]})
    assert m.wanted(app) == {100, 200, 300, 400, 500}
    assert sorted(seen) == [100, 200, 300, 400, 500]


def test_wanted_asks_again_once_the_tables_came(tmp_path, monkeypatch):
    """rc.2 on the lab: the shipper's first ask came before its tables, the
    empty answer was kept 10 minutes, and its ask right after sending them
    got no files."""
    from types import SimpleNamespace

    now = {"tables": None}
    app = SimpleNamespace(
        chars=lambda: {"chars": [{"char": "Testchar"}]}, valid_char=lambda n: True,
        char=lambda n: {"gear": {"race": "Gnome", "class": "PRIEST", "items": [{"slot": 5, "item_id": 100}]},
                        "header": {}},
        timeline=lambda n: {"levels": []}, views=None)
    monkeypatch.setattr(model3d, "find_look", lambda app, name: None)
    m = model3d.Model3D({"MODEL3D_CACHE_DIR": str(tmp_path)}, tmp_path, None)
    monkeypatch.setattr(m, "tables", lambda: now["tables"])
    monkeypatch.setattr(m, "needs", lambda gear, header, look: {7} if now["tables"] else set())
    assert m.wanted(app) == set()
    now["tables"] = {"ChrModel": object()}
    assert m.wanted(app) == {7}
    monkeypatch.setattr(m, "needs", lambda gear, header, look: {8})
    assert m.wanted(app) == {7}  # the same tables: kept
    now["tables"] = {"ChrModel": object()}
    assert m.wanted(app) == {8}  # new tables (a patch): asked again


def test_a_history_that_cant_be_read_still_counts_the_levels(tmp_path, monkeypatch):
    from types import SimpleNamespace

    def boom(name):
        raise OSError("store down")

    views = SimpleNamespace(snapshot=lambda name, lv: {"gear": {"items": [{"slot": 5, "item_id": 200}]}},
                            snapshot_inputs=boom)
    app = SimpleNamespace(
        chars=lambda: {"chars": [{"char": "Testchar"}]}, valid_char=lambda n: True,
        char=lambda n: {"gear": {"race": "Gnome", "class": "PRIEST", "items": [{"slot": 5, "item_id": 100}]},
                        "header": {}},
        timeline=lambda n: {"levels": [22]}, views=views)
    monkeypatch.setattr(model3d, "find_look", lambda app, name: None)
    m = model3d.Model3D({"MODEL3D_CACHE_DIR": str(tmp_path)}, tmp_path, None)
    monkeypatch.setattr(m, "needs", lambda gear, header, look: {gear["items"][0]["item_id"]})
    assert m.wanted(app) == {100, 200}
