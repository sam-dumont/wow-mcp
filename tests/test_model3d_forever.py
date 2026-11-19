"""Forever's own armor in 3D (data/model3d-reference.json.gz) and the
Updated / Classic models. Made-up displays and a fake Wowhead
(tests.test_model3d.Upstream); one test reads the shipped reference for
the three displays that started it. Nothing here talks to the internet."""

import gzip
import json

import pytest

from tests.test_model3d import Upstream, disp, items
from tests.test_public_view import (
    CHAR,
    fake_extras,  # noqa: F401 - a fixture (stand-ins for the MCP and coach)
    make,
)
from wowstats import account, model3d, publish, redact, renderer, tenancy, ui

# A made-up reference, in the export's shape (tools/model3d_reference).
REF = {
    "schema": 1,
    "source": {"via": "test"},
    "displays": {
        # Gloves Wowhead lacks (item 7777 in the fake item tables): two sections.
        str(disp(7777)): {"class": [4, 1], "geosets": [1, 0, 0, 0, 0, 0], "attach": [0] * 6, "override": 0,
                          "models": [0, 0], "model_materials": [0, 0], "helmet_vis": [0, 0],
                          "sections": {"1": 501, "2": 502}},
        # A cloak: no model, its material is the cape's.
        "800001": {"class": [4, 1], "geosets": [2, 0, 0, 0, 0, 0], "attach": [0] * 6, "override": 0,
                   "models": [0, 0], "model_materials": [503, 0], "helmet_vis": [0, 0], "sections": {}},
        # A helmet: a model per race, its material, what it hides.
        "800002": {"class": [4, 2], "geosets": [0] * 6, "attach": [0] * 6, "override": 0,
                   "models": [700, 0], "model_materials": [504, 0], "helmet_vis": [90, 91], "sections": {}},
        # Shoulders: two models (left, right), two materials.
        "800003": {"class": [4, 3], "geosets": [0] * 6, "attach": [0] * 6, "override": 0,
                   "models": [701, 701], "model_materials": [505, 505], "helmet_vis": [0, 0], "sections": {}},
        # A texture Wowhead doesn't serve.
        "800004": {"class": [4, 1], "geosets": [0] * 6, "attach": [0] * 6, "override": 0,
                   "models": [0, 0], "model_materials": [0, 0], "helmet_vis": [0, 0], "sections": {"5": 506}},
    },
    "textures": {"501": [[9001, 3, 0, 0]], "502": [[9002, 0, 0, 0], [9003, 1, 0, 0]], "503": [[9004, 3, 0, 0]],
                 "504": [[9005, 3, 0, 0]], "505": [[9006, 3, 0, 0]], "506": [[9999, 3, 0, 0]]},
    "models": {"700": [[9101, 0, 0, 1, -1], [9102, 1, 0, 1, -1]], "701": [[9103, 2, 0, 0, 0], [9104, 2, 0, 0, 1]]},
    "helmet": {"90": [[7, 1, 0], [1, 0, 0]], "91": [[4, 0, 0]]},
}


@pytest.fixture
def ref_file(tmp_path):
    p = tmp_path / "ref.json.gz"
    p.write_bytes(gzip.compress(json.dumps(REF).encode()))
    return p


@pytest.fixture
def upstream():
    u = Upstream()
    # The textures Wowhead serves (not 9999).
    for t in (9001, 9002, 9003, 9004, 9005, 9006):
        u.files[f"/modelviewer/classic/textures/{t}.webp"] = b"RIFF"
    # The original models: Wowhead's classic data has its own customization
    # for them (other ids, fewer choices); its live data the updated ones.
    sd = json.dumps({"Options": [{"Id": 1, "Name": "Skin", "Choices": [{"Id": 111}]},
                                 {"Id": 3, "Name": "face", "Choices": [{"Id": 131}, {"Id": 132}]}]}).encode()
    for m in range(1, 17):
        u.files[f"/modelviewer/classic/meta/charactercustomization/{m}.json"] = sd
        u.files[f"/modelviewer/classic/meta/character/{m}.json"] = b'{"Model":119159}'
        u.files[f"/modelviewer/live/meta/character/{m}.json"] = b'{"Model":900914}'
        u.files[f"/modelviewer/live/meta/charactercustomization/{m}.json"] = json.dumps(
            {"Options": [{"Id": 1, "Name": "Skin", "Choices": [{"Id": 11}, {"Id": 12}]},
                         {"Id": 2, "Name": "Face", "Choices": [{"Id": 21}, {"Id": 22}, {"Id": 23}]}]}).encode()
    yield u
    u.srv.shutdown()
    u.srv.server_close()


@pytest.fixture
def env(upstream, tmp_path, ref_file):
    return {**upstream.env(tmp_path), "MODEL3D_REFERENCE": str(ref_file)}


@pytest.fixture
def m3d(env):
    return model3d.Model3D(env, ui.static_dir())


# --- the meta, from Forever's tables ------------------------------------------

def test_armor_meta_in_wowheads_shape():
    gloves = model3d.armor_meta(REF, 10, disp(7777))
    assert gloves["ComponentTextures"] == {"1": 501, "2": 502}
    assert gloves["TextureFiles"] == {
        "501": [{"FileDataId": 9001, "Gender": 3, "Class": 0, "Race": 0, "ExtraData": 0}],
        "502": [{"FileDataId": 9002, "Gender": 0, "Class": 0, "Race": 0, "ExtraData": 0},
                {"FileDataId": 9003, "Gender": 1, "Class": 0, "Race": 0, "ExtraData": 0}]}
    assert gloves["Item"]["InventoryType"] == 10 and gloves["Item"]["GeosetGroup"] == [1, 0, 0, 0, 0, 0]
    assert gloves["ModelFiles"] is None and gloves["Textures"] is None and gloves["Scale"] == 1
    cloak = model3d.armor_meta(REF, 16, 800001)
    assert cloak["ComponentTextures"] == {"12": 503} and "503" in cloak["TextureFiles"]
    helm = model3d.armor_meta(REF, 1, 800002)
    assert helm["ComponentModels"] == {"0": 700} and helm["Textures"] == {"2": 9005}
    assert [e["ExtraData"] for e in helm["ModelFiles"]["700"]] == [-1, -1]
    # What a helmet hides, by geoset group then race, as Wowhead lists it.
    assert helm["Item"]["HideGeosetMale"] == [{"RaceId": 1, "GeosetGroup": 0, "RaceBitSelection": 0},
                                              {"RaceId": 7, "GeosetGroup": 1, "RaceBitSelection": 0}]
    assert helm["Item"]["HideGeosetFemale"] == [{"RaceId": 4, "GeosetGroup": 0, "RaceBitSelection": 0}]
    shoulders = model3d.armor_meta(REF, 3, 800003)
    assert shoulders["ComponentModels"] == {"0": 701, "1": 701}
    assert shoulders["Textures"] == shoulders["Textures2"] == {"2": 9006}
    assert [e["ExtraData"] for e in shoulders["ModelFiles"]["701"]] == [0, 1]  # left, right
    assert model3d.armor_meta(REF, 10, 123) is None


def test_the_three_forever_items_resolve():
    """Violet Sash, Graveweave Bindings and Ephemeral Grips: Wowhead answers
    404 for their displays (2026-11-19), the shipped reference dresses them
    with textures Wowhead serves."""
    ref = model3d.ArmorReference()
    sash, bindings, grips = ref.meta(6, 714142), ref.meta(9, 715413), ref.meta(10, 737496)
    assert sash["ComponentTextures"] == {"5": 34469} and sash["TextureFiles"]["34469"][0]["FileDataId"] == 159391
    assert bindings["ComponentTextures"] == {"1": 27336}
    assert bindings["TextureFiles"]["27336"][0]["FileDataId"] == 148880
    assert grips["ComponentTextures"] == {"1": 26407, "2": 33984}
    assert {e["FileDataId"] for m in grips["TextureFiles"].values() for e in m} == {148848, 153802}
    assert grips["Item"]["GeosetGroup"][0] == 1
    assert ref.data()["source"]["build"] == "1.60.1.70959"


# --- through the relay and the answer -------------------------------------------

def test_armor_wowhead_lacks_is_drawn(m3d, upstream):
    gear = {"race": "Gnome", "sex": "male", "class": "PRIEST", "items": items(
        (5, "Chest", 2585, "Robe"), (10, "Hands", 7777, "Gloves Wowhead lacks"))}
    d = m3d.character(gear)
    assert {i["item_id"] for i in d["items"]} == {2585, 7777} and d["missing"] == []
    path = f"meta/armor/10/{disp(7777)}.json"
    assert json.loads(m3d.relay.get(path)) == model3d.armor_meta(REF, 10, disp(7777))
    files = model3d.viewer_files(m3d.relay, d)
    assert {path, "textures/9001.webp", "textures/9002.webp"} <= files  # male: not 9003
    # A level picture gets it too: the renderer doesn't read the reference.
    assert m3d.stand_ins(d) == {disp(7777): model3d.armor_meta(REF, 10, disp(7777))}
    req = renderer.parse(json.dumps({"model": d["model"], "items": [[10, disp(7777)]],
                                     "metas": {str(disp(7777)): m3d.stand_ins(d)[disp(7777)]}}).encode())
    assert set(req["metas"]) == {str(disp(7777))}


def test_wowheads_own_meta_comes_first(m3d, upstream):
    upstream.files[f"/modelviewer/classic/meta/armor/10/{disp(7777)}.json"] = b'{"theirs":1}'
    assert m3d.relay.get(f"meta/armor/10/{disp(7777)}.json") == b'{"theirs":1}'


def test_a_missing_texture_lists_the_item(m3d, upstream):
    upstream.files["/db2/Item/csv?build=1.60.1.1"] += b"8004,5,4,1\n"
    upstream.files["/db2/ItemModifiedAppearance/csv?build=1.60.1.1"] += b"98,8004,0,180004,0\n"
    upstream.files["/db2/ItemAppearance/csv?build=1.60.1.1"] += b"180004,800004\n"
    d = m3d.character({"race": "Human", "sex": "female", "class": "MAGE",
                       "items": items((5, "Chest", 8004, "Robe of Missing Texture"))})
    assert d["items"] == []
    assert d["missing"] == [{"slot": 5, "slot_name": "Chest", "item_id": 8004, "name": "Robe of Missing Texture",
                             "inventory_type": 5, "display_id": 800004, "why": "Wowhead has no texture for it"}]


def test_a_display_nobody_has_stays_missing(upstream, tmp_path):
    m = model3d.Model3D({**upstream.env(tmp_path), "MODEL3D_REFERENCE": str(tmp_path / "none.json")},
                        ui.static_dir())
    d = m.character({"race": "Gnome", "sex": "male", "class": "PRIEST",
                     "items": items((10, "Hands", 7777, "Gloves"))})
    assert d["missing"][0]["why"] == "Wowhead's viewer has no model for it"


# --- Updated or Classic ---------------------------------------------------------

def test_classic_look_matches_option_by_option():
    hd = [{"Id": 1, "Name": "Skin", "Choices": [{"Id": 11}, {"Id": 12}, {"Id": 13}]},
          {"Id": 2, "Name": "Face", "Choices": [{"Id": 21}, {"Id": 22}]},
          {"Id": 5, "Name": "Hair Color", "Choices": [{"Id": 51}, {"Id": 52}]}]
    sd = [{"Id": 1, "Name": "Skin", "Choices": [{"Id": 111}, {"Id": 112}]},  # fewer: the last one
          {"Id": 9, "Name": "face", "Choices": [{"Id": 191}, {"Id": 192}]},  # another id: by name
          {"Id": 4, "Name": "Piercings", "Choices": [{"Id": 141}]},  # nothing chosen: the first
          {"Id": 6, "Name": "Empty", "Choices": []}]
    chosen = [{"optionId": 1, "choiceId": 13}, {"optionId": 2, "choiceId": 22}, {"optionId": 5, "choiceId": 52}]
    assert model3d.classic_look(hd, chosen, sd) == [{"optionId": 1, "choiceId": 112}, {"optionId": 9, "choiceId": 192},
                                                    {"optionId": 4, "choiceId": 141}]


def test_the_answer_has_both_models(m3d, upstream):
    d = m3d.character({"race": "Gnome", "sex": "female", "class": "MAGE", "items": []})
    assert d["model"]["chr_model"] == 14 and d["model_sd"]["chr_model"] == 14
    assert d["model_sd"]["gender"] == 1
    assert {o["optionId"] for o in d["model_sd"]["options"]} == {1, 3}
    # The original model from the classic data only, under sd/, never live/.
    assert m3d.relay.get("sd/meta/character/14.json") == b'{"Model":119159}'
    assert m3d.relay.get("meta/character/14.json") == b'{"Model":900914}'
    files = model3d.viewer_files(m3d.relay, d)
    assert {"sd/meta/character/14.json", "sd/meta/charactercustomization/14.json", "m2/119159.m2",
            "m2/900914.m2"} <= files
    assert "/modelviewer/classic/meta/charactercustomization/14.json" in upstream.seen
    assert not any("/sd/" in p for p in upstream.seen)


def test_no_classic_model_for_a_stand_in(m3d):
    """Skyborne is drawn on the Blood Elf body, which Wowhead's classic
    data doesn't have: Classic is off for it, with why on the page."""
    d = m3d.character({"race": "Skyborne", "sex": "male", "class": "PRIEST", "items": []})
    assert d["model"]["chr_model"] == 19 and d["model_sd"] is None


def test_default_models(m3d, env):
    class App:
        pass

    a = App()
    assert m3d.default_models(a) == "hd"
    a.models_default = lambda: "sd"
    assert m3d.default_models(a) == "sd"
    a.models_default = lambda: "bogus"
    assert m3d.default_models(a) == "hd"
    m = model3d.Model3D({**env, "MODEL3D_MODELS": "SD"}, ui.static_dir())
    assert m.default_models(App()) == "sd"


@pytest.mark.usefixtures("fake_extras")
def test_relay_paths_for_classic(run_app, env):
    srv = run_app(make("full", **env))
    zam = f"/model3d/zam/{model3d.ZAM_REV}/"
    assert srv.get(zam + "sd/meta/character/13.json")[2] == b'{"Model":119159}'
    for bad in ("sd/meta/item/1.json", "sd/m2/1.m2", "sd/sd/meta/character/13.json"):
        assert srv.get(zam + bad)[0] == 404, bad
    js = (ui.static_dir() / "model3d" / "model3d.js").read_text()
    assert "|sd/meta/(?:character|charactercustomization)/" in js


@pytest.mark.usefixtures("fake_extras")
def test_api_answer_carries_both(run_app, env):
    srv = run_app(make("full", **env, MODEL3D_MODELS="sd"))
    code, _, body = srv.get(f"/api/model3d?name={CHAR}")
    d = json.loads(body)
    assert code == 200 and d["models"] == "sd" and "model_sd" in d
    # The public shape keeps them (the static sites' toggle).
    assert redact.public("model3d", d)["models"] == "sd"


# --- the setting ----------------------------------------------------------------

def test_settings_models(tmp_path):
    reg = tenancy.Registry(tmp_path / "users.json")
    t = type("T", (), {"registry": reg})()
    u = reg.ensure("sub-1")
    assert u.models == "hd"
    u = account.set_settings(t, u, {"models": "sd"}, [])
    assert reg.get("sub-1").models == "sd" and u.public()["models"] == "sd"
    with pytest.raises(tenancy.TenancyError):
        account.set_settings(t, u, {"models": "4k"}, [])


def test_publish_takes_the_persons_models(tmp_path):
    env = {"PUBLISH_DIR": str(tmp_path)}
    u = {"slug": "abc", "account_id": 7, "store": "owner", "models": "sd"}
    assert publish.user_env(env, u)["MODEL3D_MODELS"] == "sd"
    assert "MODEL3D_MODELS" not in publish.user_env(env, {**u, "models": None})
