"""The 3D character view: full view only, its own CSP, display ids from a
(fake) wago.tools, the model files through the relay from a (fake)
wow.zamimg.com. Nothing here talks to the internet."""

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_public_view import (
    CHAR,
    fake_extras,  # noqa: F401 - a fixture (stand-ins for the MCP and coach)
    make,
)
from wowstats import links, model3d, ui

ITEM = ("ID,InventoryType,ClassID,SubclassID\n"
        "5820,3,4,1\n53,4,4,0\n1489,5,4,2\n911,17,2,10\n935,13,2,7\n3021,15,2,2\n4369,26,2,3\n"
        "5069,26,2,19\n2512,24,6,2\n2101,18,11,2\n1116,11,4,0\n2585,20,4,1\n7777,10,4,1\n")
IMA = ("ID,ItemID,ItemAppearanceModifierID,ItemAppearanceID,OrderIndex\n"
       "1,5820,0,105820,0\n2,53,0,100053,0\n3,1489,0,101489,0\n4,911,0,100911,0\n5,935,0,100935,0\n"
       "6,3021,0,103021,0\n7,4369,0,104369,0\n8,5069,0,105069,0\n9,2512,0,102512,0\n10,2101,0,102101,0\n"
       "11,1116,0,101116,0\n12,2585,0,102585,0\n13,7777,0,107777,0\n"
       # A heroic look (modifier 1) never wins over the base one.
       "14,5820,1,199999,0\n")
IA = "ID,ItemDisplayInfoID\n" + "".join(f"{a},{a - 100000 + 900000}\n" for a in (
    105820, 100053, 101489, 100911, 100935, 103021, 104369, 105069, 102512, 102101, 101116, 102585, 107777)) + \
    "199999,1\n"


def disp(item: int) -> int:
    return item + 900000


class Upstream:
    """wago.tools and wow.zamimg.com in one local server."""

    def __init__(self):
        self.files: dict[str, bytes] = {"/api/builds/wowf/latest": json.dumps({"version": "1.60.1.1"}).encode()}
        for name, body in (("Item", ITEM), ("ItemModifiedAppearance", IMA), ("ItemAppearance", IA)):
            self.files[f"/db2/{name}/csv?build=1.60.1.1"] = body.encode()
        custom = json.dumps({"Options": [{"Id": 1, "Name": "Skin", "Choices": [{"Id": 11}, {"Id": 12}]},
                                         {"Id": 2, "Name": "Face", "Choices": [{"Id": 21}]}]}).encode()
        for m in range(1, 17):
            self.files[f"/modelviewer/classic/meta/character/{m}.json"] = b"{}"
            self.files[f"/modelviewer/classic/meta/charactercustomization/{m}.json"] = custom
        for m in (19, 20):  # the Blood Elf: Wowhead's live data only
            self.files[f"/modelviewer/live/meta/character/{m}.json"] = b"{}"
            self.files[f"/modelviewer/live/meta/charactercustomization/{m}.json"] = custom
        for item in (5820, 53, 1489, 911, 935, 3021, 4369, 2585):
            inv = {5820: 3, 53: 4, 1489: 5, 2585: 20}.get(item)
            where = f"armor/{inv}" if inv else "item"
            self.files[f"/modelviewer/classic/meta/{where}/{disp(item)}.json"] = b"{}"
        self.files["/modelviewer/live/viewer/viewer.min.js"] = b"window.ZamModelViewer=function(){};"
        self.seen: list[str] = []
        self.down = False
        up = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                up.seen.append(self.path)
                body = None if up.down else up.files.get(self.path)
                self.send_response(503 if up.down else 200 if body is not None else 404)
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def env(self, tmp_path) -> dict:
        # The fake viewer is pinned by its hash, as an operator pins a new
        # build of the real one (wowstats.model3d.VIEWER_SHA256).
        viewer = hashlib.sha256(self.files["/modelviewer/live/viewer/viewer.min.js"]).hexdigest()
        return {"MODEL3D_WAGO_URL": self.base, "MODEL3D_ZAM_URL": self.base + "/modelviewer/",
                "MODEL3D_CACHE_DIR": str(tmp_path / "cache"), "MODEL3D_VIEWER_SHA256": viewer}


@pytest.fixture
def upstream():
    u = Upstream()
    yield u
    u.srv.shutdown()
    u.srv.server_close()


@pytest.fixture
def m3d(upstream, tmp_path):
    return model3d.Model3D(upstream.env(tmp_path), ui.static_dir())


def items(*rows):
    return [{"slot": s, "slot_name": n, "item_id": i, "name": nm} for s, n, i, nm in rows]


# --- what the public view never gets -------------------------------------

PATHS_3D = ["/model3d/view", "/model3d/view.js", "/model3d/model3d.js", "/model3d/model3d.css",
            "/model3d/jquery-3.7.1.min.js", "/model3d/zam/r2/viewer/viewer.min.js",
            "/model3d/zam/r2/meta/character/13.json", f"/api/model3d?name={CHAR}"]


def test_public_view_never_has_3d(run_app, upstream, tmp_path):
    srv = run_app(make("public", **upstream.env(tmp_path)))
    for path in PATHS_3D:
        for method in ("GET", "POST", "HEAD"):
            assert srv.get(path, method=method, data=b"{}" if method == "POST" else None)[0] == 404, path
    code, headers, body = srv.get("/")
    for text in (body.decode(), srv.get("/app.js")[2].decode(), srv.get("/app.css")[2].decode()):
        # Links to Wowhead's pages are fine (the Overview names items and
        # zones with one, from addresses the server writes, and the public
        # view has no answer that carries one); loading anything from it isn't.
        for word in ("model3d", "zamimg", "wowhead.com", "Show in 3D"):
            assert word not in text.lower() if word.islower() else word not in text
        # Wowhead only as a plain link, from web/items.js's one constant: never a script, a frame or an image.
        assert "wowhead.com" not in text
    items = srv.get("/items.js")[2].decode()
    assert re.findall(r"https://[\w.]*wowhead\.com[^'\"]*", items) == [links.WOWHEAD_BASE]
    csp = headers["Content-Security-Policy"]
    assert "frame-src" not in csp and "unsafe-eval" not in csp and "zamimg" not in csp
    assert upstream.seen == [], "a public server never calls Wowhead or wago.tools"


@pytest.mark.usefixtures("fake_extras")
def test_csp_per_view(run_app, upstream, tmp_path):
    srv = run_app(make("full", **upstream.env(tmp_path)))
    _, headers, body = srv.get("/")
    page = headers["Content-Security-Policy"]
    assert "frame-src 'self'" in page and "unsafe-eval" not in page and "http" not in page
    assert b"model3d/model3d.js" in body and b'id="model3d"' not in body  # the toggle is on the paperdoll
    assert upstream.seen == [], "nothing loads before the click"
    code, headers, body = srv.get("/model3d/view?name=" + CHAR)
    csp = headers["Content-Security-Policy"]
    assert code == 200 and "frame-ancestors 'self'" in csp and "sandbox allow-scripts" in csp and "http" not in csp
    # Only the page itself and its own frame: every other answer keeps the strict CSP.
    assert "frame-src" not in srv.get("/api/chars")[1]["Content-Security-Policy"]
    assert "unsafe-eval" not in srv.get("/model3d/model3d.js")[1]["Content-Security-Policy"]
    off = run_app(make("full", MODEL3D="off", **upstream.env(tmp_path)))
    assert off.get("/model3d/view")[0] == 404 and b"model3d" not in off.get("/")[2]


@pytest.mark.usefixtures("fake_extras")
def test_relay_only_what_the_viewer_needs(run_app, upstream, tmp_path):
    srv = run_app(make("full", **upstream.env(tmp_path)))
    zam = f"/model3d/zam/{model3d.ZAM_REV}/"
    code, headers, body = srv.get(zam + "meta/character/13.json")
    assert code == 200 and body == b"{}" and headers["Content-Type"] == "application/json"
    assert srv.get(zam + "meta/character/19.json")[0] == 200  # from live/ when classic/ has none
    assert srv.get(zam + "viewer/viewer.min.js")[0] == 200
    n = len(upstream.seen)
    assert srv.get(zam + "meta/character/13.json")[0] == 200
    assert len(upstream.seen) == n, "served from the disk cache"
    for bad in ("../../etc/passwd", "meta/character/..%2f..%2fx.json",
                "m2/1.skin", "textures/1.js", "https://evil.example/x.js", "viewer/other.js"):
        assert srv.get(zam + bad)[0] == 404, bad
    # The unversioned paths are gone: no browser cache from v0.2.x matches.
    assert srv.get("/model3d/zam/meta/character/13.json")[0] == 404
    assert not any("evil" in p or "passwd" in p for p in upstream.seen)


# --- races, sexes, models ------------------------------------------------

RACES = ["Human", "Orc", "Dwarf", "Night Elf", "Undead", "Tauren", "Gnome", "Troll", "Skyborne"]


@pytest.mark.parametrize("race", RACES)
@pytest.mark.parametrize("sex", ["male", "female"])
def test_every_race_and_sex_has_a_model(m3d, race, sex):
    d = m3d.character({"race": race, "sex": sex, "class": "WARRIOR", "items": []})
    assert d["model"] is not None, d.get("why")
    want = model3d.CHR_MODELS[race.lower()][sex == "female"]
    if race == "Skyborne":  # Forever's own race, on the Blood Elf body Wowhead has
        assert d["stand_in"] == "Blood Elf" and d["model"]["chr_model"] == (19, 20)[sex == "female"]
    else:
        assert d["stand_in"] is None and d["model"]["chr_model"] == want
    assert d["model"]["gender"] == (sex == "female")
    assert d["model"]["options"] == [{"optionId": 1, "choiceId": 11}, {"optionId": 2, "choiceId": 21}]
    assert d["appearance"] is None


def test_race_spellings():
    """Syndicator and Polestar write the client's race names; ChrRaces has
    two Skyborne factions."""
    pairs = {model3d.chr_model(r, s) for r in RACES for s in ("male", "female")}
    assert len(pairs) == 18
    for alias, race in (("NightElf", "Night Elf"), ("Scourge", "Undead"), ("High Order Skyborne", "Skyborne"),
                        ("Windshaper Skyborne", "Skyborne"), ("night elf", "Night Elf")):
        assert model3d.chr_model(alias, "female") == model3d.chr_model(race, "female")
    assert model3d.chr_model("Murloc", "male") is None
    assert model3d.chr_model("Gnome", None) == model3d.chr_model("Gnome", "male")


def test_sex_override_only_when_unknown(m3d):
    d = m3d.character({"race": "Gnome", "class": "PRIEST", "items": []}, sex_override="female")
    assert (d["sex"], d["sex_known"], d["model"]["chr_model"]) == ("female", False, 14)
    d = m3d.character({"race": "Gnome", "sex": "male", "items": []}, sex_override="female")
    assert (d["sex"], d["sex_known"], d["model"]["chr_model"]) == ("male", True, 13)
    d = m3d.character({}, {"race": "Dwarf", "sex": "female", "class": "Hunter"})  # header only: Syndicator
    assert d["model"]["chr_model"] == 6 and d["class"] == "HUNTER"


def test_no_race_says_why(m3d):
    d = m3d.character({}, {"race": None})
    assert d["model"] is None and "isn't saved" in d["why"]


# --- dressing ------------------------------------------------------------

def test_display_ids_for_any_item(m3d, upstream):
    d = m3d.character({"race": "Night Elf", "sex": "female", "class": "PRIEST", "items": items(
        (3, "Shoulder", 5820, "Faerie Mantle"), (4, "Shirt", 53, "Shirt"), (5, "Chest", 2585, "Gray Woolen Robe"),
        (11, "Finger", 1116, "Ring"), (10, "Hands", 7777, "Gloves Wowhead lacks"),
        (19, "Tabard", 424242, "Unknown Tabard"), (16, "Main hand", 911, "Staff"), (18, "Ranged", 5069, "Wand"))})
    shown = {(i["item_id"], i["inventory_type"], i["display_id"]) for i in d["items"]}
    assert shown == {(5820, 3, disp(5820)), (53, 4, disp(53)), (2585, 20, disp(2585)), (911, 17, disp(911))}
    why = {m["item_id"]: m["why"] for m in d["missing"]}
    assert why == {7777: "Wowhead's viewer has no model for it", 424242: "not in Forever's item tables"}
    assert d["source"] == "wago.tools wowf 1.60.1.1"
    # The ring and the wand are left out without a word, like the game does.
    assert 1116 not in why and 5069 not in why


def test_hunter_holds_the_ranged_weapon(m3d):
    gear = {"race": "Dwarf", "sex": "male", "class": "HUNTER", "items": items(
        (16, "Main hand", 935, "Night Watch Shortsword"), (18, "Ranged", 4369, "Deadly Blunderbuss"),
        (0, "Ammo", 2512, "Rough Arrow"), (19, "Quiver", 2101, "Light Quiver"))}
    d = m3d.character(gear)
    assert [(i["item_id"], i["inventory_type"]) for i in d["items"]] == [(4369, 26)]
    assert d["missing"] == [{"slot": 16, "slot_name": "Main hand", "item_id": 935, "name": "Night Watch Shortsword",
                             "inventory_type": 13, "display_id": disp(935),
                             "why": "Deadly Blunderbuss is in that hand"}]
    # A bow goes in the off hand: the sword stays.
    gear["items"] = items((16, "Main hand", 935, "Sword"), (18, "Ranged", 3021, "Ranger Bow"))
    assert {i["item_id"] for i in m3d.character(gear)["items"]} == {935, 3021}
    # Not a hunter: the melee weapon wins, the gun stays out quietly.
    gear["class"] = "WARRIOR"
    gear["items"] = items((16, "Main hand", 935, "Sword"), (18, "Ranged", 4369, "Gun"))
    d = m3d.character(gear)
    assert [i["item_id"] for i in d["items"]] == [935] and d["missing"] == []


# Spine of the Devourer: a staff Forever added (display 752138), whose model
# (resource 16577) Wowhead has under older displays, with Forever's own
# texture (material 23305 -> file 147522). Display 905120 stands for an old
# display of that model.
FOREVER_STAFF = 273040


def forever_staff(upstream):
    up = upstream.files
    up["/db2/Item/csv?build=1.60.1.1"] += b"273040,17,2,10\n"
    up["/db2/ItemModifiedAppearance/csv?build=1.60.1.1"] += b"99,273040,0,173040,0\n"
    up["/db2/ItemAppearance/csv?build=1.60.1.1"] += b"173040,752138\n"
    up["/db2/ItemDisplayInfo/csv?build=1.60.1.1"] = (
        b"ID,ModelResourcesID_0,ModelResourcesID_1,ModelMaterialResourcesID_0,ModelMaterialResourcesID_1\n"
        b"905070,16577,0,23304,0\n905120,16577,0,23302,0\n752138,16577,0,23305,0\n"
        b"777777,4242,0,1,0\n888888,16577,0,99999,0\n")
    up["/db2/TextureFileData/csv?build=1.60.1.1"] = (
        b"FileDataID,UsageType,MaterialResourcesID\n147518,0,23302\n147520,0,23304\n147522,0,23305\n")
    up["/modelviewer/classic/meta/item/905120.json"] = json.dumps({
        "Model": 0, "Textures": {"2": 147518},
        "ModelFiles": {"16577": [{"FileDataId": 147517, "Gender": 2, "Class": 0, "Race": 0}]},
        "Item": {"InventoryType": 17, "ItemClass": 2, "ItemSubClass": 10}}).encode()


def test_a_weapon_forever_added_is_drawn(m3d, upstream):
    """The main hand and the ranged weapon go to the viewer when worn, a
    weapon Forever added included (2026-11-19: the model had none)."""
    forever_staff(upstream)
    gear = {"race": "Gnome", "sex": "male", "class": "PRIEST", "items": items(
        (5, "Chest", 2585, "Robe"), (16, "Main hand", FOREVER_STAFF, "Spine of the Devourer"),
        (18, "Ranged", 5069, "Gravestone Scepter"))}
    d = m3d.character(gear)
    shown = {i["slot"]: (i["item_id"], i["inventory_type"], i["display_id"]) for i in d["items"]}
    assert shown[16] == (FOREVER_STAFF, 17, 752138)
    assert d["missing"] == []
    # The wand is left out without a word: the game never draws one.
    assert 18 not in shown
    # Its meta: the old display's model, with Forever's own texture.
    meta = json.loads(m3d.relay.get("meta/item/752138.json"))
    assert meta["Textures"] == {"2": 147522} and "16577" in meta["ModelFiles"]
    assert "textures/147522.webp" in model3d.viewer_files(m3d.relay, d)
    # A static bundle and a level picture get the same meta (the renderer
    # can't make it).
    assert m3d.stand_ins(d) == {752138: meta}
    # A ranged weapon the game draws (a bow) goes with the staff.
    gear["items"] = items((16, "Main hand", FOREVER_STAFF, "Staff"), (18, "Ranged", 3021, "Bow"))
    assert {i["slot"] for i in m3d.character(gear)["items"]} == {16, 18}


def test_no_stand_in_without_the_same_model_and_texture(m3d, upstream):
    forever_staff(upstream)
    assert m3d.relay.get("meta/item/777777.json") is None  # a model no known display has
    assert m3d.relay.get("meta/item/888888.json") is None  # its texture is unknown: no wrong look
    assert m3d.relay.get("meta/item/123.json") is None  # not in Forever's tables


def test_display_ids_cached_and_kept_when_upstream_fails(upstream, tmp_path, monkeypatch):
    ids = model3d.DisplayIds(upstream.base, "wowf", tmp_path)
    assert ids.get(5820) == (3, disp(5820), 4, 1)  # the base look, not the heroic one
    assert ids.get(999) is None
    fetched = len(upstream.seen)
    again = model3d.DisplayIds(upstream.base, "wowf", tmp_path)  # a restart: from disk
    assert again.get(911) == (17, disp(911), 2, 10) and len(upstream.seen) == fetched
    upstream.down = True
    monkeypatch.setattr(model3d.time, "time", lambda: 10 ** 12)  # a day later: the check fails
    assert again.get(911) == (17, disp(911), 2, 10)
    empty = model3d.DisplayIds(upstream.base, "wowf", None)
    with pytest.raises(model3d.Upstream):
        empty.get(911)


@pytest.mark.usefixtures("fake_extras")
def test_api_for_a_character(run_app, upstream, tmp_path):
    srv = run_app(make("full", **upstream.env(tmp_path)))
    code, _, body = srv.get(f"/api/model3d?name={CHAR}")
    d = json.loads(body)
    assert code == 200 and d["race"] == "Gnome" and d["model"]["chr_model"] == 13
    assert [i["item_id"] for i in d["items"]] == [5820]
    assert srv.get("/api/model3d?name=%22x")[0] == 400
    assert srv.get("/api/model3d?name=Nobody-Here")[0] == 404
    upstream.down = True
    other = run_app(make("full", **upstream.env(tmp_path / "other")))
    code, _, body = other.get(f"/api/model3d?name={CHAR}")
    assert code == 502 and "error" in json.loads(body)


def test_saved_look_replaces_the_default_per_option(m3d):
    base = {"race": "Gnome", "sex": "male", "class": "PRIEST", "items": []}
    d = m3d.character(base)
    assert d["model"]["options"][0] == {"optionId": 1, "choiceId": 11} and d["appearance"] is None
    look = {"source": "battle.net", "refreshed": "2026-11-18T06:00:00Z", "gender": "male",
            "options": [{"optionId": 1, "choiceId": 12}, {"optionId": 999, "choiceId": 5}]}
    d = m3d.character(base, look=look)
    assert d["model"]["options"][0] == {"optionId": 1, "choiceId": 12}
    assert d["appearance"] == {"source": "battle.net", "refreshed": "2026-11-18T06:00:00Z"}
    # Another race's choice (not offered by this model) falls back to the default.
    d = m3d.character(base, look={"source": "narcissus", "options": [{"optionId": 1, "choiceId": 77}]})
    assert d["model"]["options"][0] == {"optionId": 1, "choiceId": 11} and d["appearance"]["source"] == "narcissus"
    # Battle.net's gender fills in when no addon saved the sex.
    d = m3d.character({"race": "Gnome", "items": []}, look={**look, "gender": "female"})
    assert d["sex"] == "female" and d["sex_known"]


def test_narcissus_rows():
    from wowstats.model3d import narcissus_look
    assert narcissus_look([]) is None
    rows = [{"_time": "2026-11-18T10:00:00Z", "looks": '[{"optionId": 80, "choiceId": 1174}]'}]
    assert narcissus_look(rows)["options"] == [{"optionId": 80, "choiceId": 1174}]


def test_battle_net_look_from_vm_wow(monkeypatch):
    from wowstats.stores import Store
    s = Store("http://vl", vm_url="http://vm")
    assert Store("http://vl").character_look("Testpriest-Fixture") is None
    answers = {
        "wow_character_customization": [{"metric": {"option_id": "82"}, "value": [0, "1210"]},
                                        {"metric": {"option_id": "80"}, "value": [0, "1174"]}],
        "wow_character_profile{": [{"metric": {"gender": "male", "race_id": "7"}, "value": [0, "32"]}],
        "wow_character_profile_refreshed": [{"metric": {}, "value": [0, "1795413600"]}],
    }
    seen = []

    def vm_query(q):
        seen.append(q)
        return next((v for k, v in answers.items() if k in q), [])
    monkeypatch.setattr(s, "vm_query", vm_query)
    look = s.character_look("Testpriest-Fixture")
    assert look["options"] == [{"optionId": 80, "choiceId": 1174}, {"optionId": 82, "choiceId": 1210}]
    assert look["gender"] == "male" and look["refreshed"].startswith("2026-")
    assert 'character="Testpriest-Fixture"' in seen[0]
    answers["wow_character_customization"] = []  # Testhunter: not on Battle.net
    assert s.character_look("Testhunter-Fixture") is None


def test_a_battle_net_look_keeps_its_hair_and_beard():
    """Testpriest (Gnome male) came out bald and beardless: Battle.net's ids are
    those of Wowhead's current data, and Forever's one Facial Hair option
    (9486) is Beard plus Mustache there. Matched by id, else by geoset."""
    from wowstats.model3d import LIVE_FIRST, apply_look, translate_look

    def choice(cid, *geosets):
        return {"Id": cid, "Elements": [{"Geoset": {"GeosetType": t, "GeosetID": g}} for t, g in geosets]}

    meta = [
        {"Id": 80, "Choices": [choice(1173), choice(1174)]},
        {"Id": 82, "Choices": [choice(1199, (0, 0)), choice(1210, (0, 12))]},
        {"Id": 84, "Choices": [choice(1227, (1, 0)), choice(1233, (1, 6))]},
        {"Id": 783, "Choices": [choice(8757, (3, 0)), choice(8762, (3, 5))]},
    ]
    saved = [{"optionId": 80, "choiceId": 1174}, {"optionId": 82, "choiceId": 1210},
             {"optionId": 9486, "choiceId": 78873}]
    forever = {78873: [(1, 6), (3, 5)]}
    got = apply_look(meta, translate_look(meta, saved, lambda c: forever.get(c, [])))
    assert got == [{"optionId": 80, "choiceId": 1174}, {"optionId": 82, "choiceId": 1210},
                   {"optionId": 84, "choiceId": 1233}, {"optionId": 783, "choiceId": 8762}]
    # Without Forever's tables the facial hair is lost, not wrong.
    got = apply_look(meta, translate_look(meta, saved, lambda c: []))
    assert {"optionId": 84, "choiceId": 1227} in got and {"optionId": 82, "choiceId": 1210} in got
    assert LIVE_FIRST.match("meta/charactercustomization/13.json") and not LIVE_FIRST.match("meta/item/1.json")


def test_cache_rules_keep_only_what_never_changes():
    """v0.2.1 moved the character metas from classic to live data under the
    same URLs, cached a day: browsers mixed v0.2.0's classic meta with the
    new files and drew a broken head. Only files named by a FileDataID are
    cached; the metas and the viewer script revalidate."""
    for path in ("m2/900914.m2", "skin/490253.skin", "textures/916430.webp", "bone/1053747.bone",
                 "skel/2184855.skel", "anim/1-0.anim", "phys/1.phys"):
        assert model3d.cache_control(path) == "public, max-age=86400", path
        assert model3d.cache_control(path, "private") == "private, max-age=86400"
    for path in ("meta/character/13.json", "meta/charactercustomization/13.json", "meta/item/20377.json",
                 "meta/armor/3/9077.json", "viewer/viewer.min.js"):
        assert model3d.cache_control(path) == "no-cache", path


def test_view_js_reads_the_current_revision():
    js = (ui.static_dir() / "model3d" / "view.js").read_text()
    assert f"const ZAM = 'zam/{model3d.ZAM_REV}/';" in js
