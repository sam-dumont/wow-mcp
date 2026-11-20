"""tools/3dcases offline: synthetic pictures and specs, no game file, no
Battle.net, no browser. The real run is tools/3dcases/README.md."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "3dcases"


def load(name):
    """The tool's modules import each other by plain name (it runs as a
    script): load them the same way, from its folder only."""
    if str(TOOL) not in sys.path:
        sys.path.insert(0, str(TOOL))
    mod = sys.modules.get(name)
    if mod is not None and Path(getattr(mod, "__file__", "") or "").parent != TOOL:
        del sys.modules[name]
    return importlib.import_module(name)


store = load("store")
imgcmp = load("imgcmp")
checks = load("checks")
run = load("run")
Img = imgcmp.Img

SKIN, CLOTH, STEEL = (200, 150, 120, 255), (60, 60, 160, 255), (180, 180, 190, 255)


def figure(w=120, h=200, weapon="right", head=SKIN, torso=CLOTH, scale=1.0, dx=0, dy=0):
    """A stick character: head, torso with arms, legs, feet, and a weapon
    beside one hand (the character's right is the picture's left)."""
    img = Img.blank(w, h)

    def rect(x0, y0, x1, y1, c):
        for y in range(int(y0 * scale) + dy, int(y1 * scale) + dy):
            for x in range(int(x0 * scale) + dx, int(x1 * scale) + dx):
                if 0 <= x < w and 0 <= y < h:
                    img.put(x, y, c)

    rect(48, 20, 72, 50, head)       # head
    rect(40, 50, 80, 110, torso)     # torso
    rect(32, 52, 40, 112, SKIN)      # arms
    rect(80, 52, 88, 112, SKIN)
    rect(44, 110, 58, 170, CLOTH)    # legs
    rect(62, 110, 76, 170, CLOTH)
    rect(42, 170, 60, 180, (40, 30, 20, 255))  # feet
    rect(60, 170, 78, 180, (40, 30, 20, 255))
    if weapon == "right":
        rect(8, 60, 14, 140, STEEL)
    elif weapon == "left":
        rect(106, 60, 112, 140, STEEL)
    return img


# ------------------------------------------------------------------ pictures

def test_png_round_trip():
    img = figure()
    back = imgcmp.png_decode(imgcmp.png_encode(img))
    assert (back.w, back.h, bytes(back.px)) == (img.w, img.h, bytes(img.px))


def test_identical_pictures_match():
    a = figure()
    cmp = imgcmp.compare(a, figure())
    assert cmp["iou"] == 1.0
    for name in ("head", "torso", "legs", "feet", "weapon"):
        r = cmp["regions"][name]
        assert r["iou"] == 1.0, name
        assert r["colour"]["distance"] == 0
    assert checks.picture_checks(cmp) == []


def test_align_finds_a_shift():
    ref = figure()
    ours = figure(dx=-5, dy=-7)
    cmp = imgcmp.compare(ref, ours)
    assert (cmp["dx"], cmp["dy"]) == (5, 7)
    assert cmp["iou"] == 1.0


def test_weapon_region_and_hand():
    ref = figure(weapon="right")
    cmp = imgcmp.compare(ref, figure(weapon="left"))
    assert cmp["weapon_side"]["ref"][0] > 0 and cmp["weapon_side"]["ref"][1] == 0
    assert cmp["weapon_side"]["ours"][1] > 0 and cmp["weapon_side"]["ours"][0] == 0
    reasons = checks.picture_checks(cmp)
    assert ("fail", "weapon in the wrong hand") in reasons
    assert checks.grade(reasons) == "fail"


def test_missing_weapon_and_wrong_head_colour():
    ref = figure()
    cmp = imgcmp.compare(ref, figure(weapon=None, head=(20, 160, 40, 255)))
    words = " ".join(w for _l, w in checks.picture_checks(cmp))
    assert "weapon missing" in words
    assert "head" in words and "wrong colour" in words


def test_brightness_is_matched_before_colours():
    ref = figure()
    dark = figure()
    dark.px = bytearray(v if i % 4 == 3 else v * 7 // 10 for i, v in enumerate(dark.px))
    cmp = imgcmp.compare(ref, dark)
    assert cmp["gain"] > 1.3
    assert all(r["colour"]["distance"] < 12 for r in cmp["regions"].values() if r["colour"])


def test_contact_sheet_is_three_panels():
    sheet = imgcmp.png_decode(imgcmp.contact_sheet(figure(), figure(weapon="left")))
    box = imgcmp.crop_box(figure(), figure(weapon="left"))
    assert sheet.w == 3 * (box[2] - box[0]) + 2 * 8
    assert sheet.at(0, 0) == (255, 255, 255, 255)


# ------------------------------------------------------------------ the grade

RECIPE = {"attachments": [{"slot": 16, "model": 1}], "collections": [],
          "geosets": {"groups": {"15": 1}, "set": {}}, "textures": {}}
SPEC = {"items": [{"slot": 1, "item_id": 10}, {"slot": 15, "item_id": 11}, {"slot": 16, "item_id": 12}]}


def test_recipe_checks_name_what_is_absent():
    reasons = checks.recipe_checks(SPEC, RECIPE, [], {9486: False, 80: True}, {9486: "Facial Hair"})
    assert ("fail", "helm absent") in reasons
    assert ("fail", "cloak absent") in reasons
    assert any(lvl == "warn" and w.startswith("beard missing") for lvl, w in reasons)
    assert checks.grade(reasons) == "fail"


def test_hidden_slots_are_not_absent():
    reasons = checks.recipe_checks(SPEC, RECIPE, [], {}, {}, hidden={1, 15})
    assert reasons == []


def test_missing_items_carry_their_why():
    reasons = checks.recipe_checks({"items": []}, RECIPE, [{"slot": 17, "item_id": 5, "why": "no display"}], {}, {})
    assert reasons == [("fail", "off hand not drawn: no display")]


def test_grade_is_the_worst():
    assert checks.grade([]) == "pass"
    assert checks.grade([("pass", "x"), ("warn", "y")]) == "warn"
    assert checks.grade([("warn", "y"), ("fail", "z")]) == "fail"


def test_hidden_items_are_hidden_slots():
    """Battle.net names a hidden helm as the game's cosmetic "Hidden Helm"
    item, which has no appearance."""

    class Ix:
        appearance = {12: 900}

    class Inst:
        ix = Ix()

        @staticmethod
        def item_facts(item_id):
            return {11: (4, 5, 1), 12: (4, 5, 16), 13: (4, 4, 5)}.get(item_id)

    items = [{"slot": 1, "item_id": 11}, {"slot": 15, "item_id": 12}, {"slot": 5, "item_id": 13}]
    assert run.hidden_slots(Inst(), items) == {1}


def test_bare_drops_only_what_the_hands_hold():
    recipe = {"attachments": [{"slot": 1}, {"slot": 3}, {"slot": 16}, {"slot": 17}, {"slot": 18}], "model": 5}
    assert [a["slot"] for a in run.bare(recipe)["attachments"]] == [1, 3]


# ------------------------------------------------------------------ the store

def write_fetch(root, case, day, race=7, gender="MALE"):
    d = root / "fetch" / case / day
    d.mkdir(parents=True)
    (d / "profile.json").write_text(json.dumps({"race": {"id": race}, "character_class": {"id": 5},
                                                "gender": {"type": gender}, "level": 40, "name": "Someone"}))
    (d / "appearance.json").write_text(json.dumps({
        "customizations": [{"option": {"id": 80}, "choice": {"id": 1174}}],
        "items": [{"id": 53, "slot": {"type": "SHIRT"}, "enchant": 0},
                  {"id": 7, "slot": {"type": "TABARD"}, "enchant": 0},
                  {"id": 9, "slot": {"type": "MAIN_HAND"}, "enchant": 254}]}))
    return d


def test_data_older_than_30_days_is_refused(tmp_path):
    st = store.Store(tmp_path)
    write_fetch(tmp_path, "c01", "2026-10-20")
    got, why = st.latest("c01", now="2026-11-20")
    assert got is None and "31 days ago" in why and "refresh" in why
    got, why = st.latest("c01", now="2026-11-19")
    assert got is not None and why == ""
    assert st.latest("c02", now="2026-11-20")[1].startswith("never fetched")


def test_prune_drops_only_old_data(tmp_path):
    st = store.Store(tmp_path)
    write_fetch(tmp_path, "c01", "2026-10-01")
    write_fetch(tmp_path, "c01", "2026-11-19")
    (tmp_path / "runs" / "2026-10-02T1000").mkdir(parents=True)
    gone = st.prune(now="2026-11-20")
    assert {p.name for p in gone} == {"2026-10-01", "2026-10-02T1000"}
    assert st.days("c01") == ["2026-11-19"]


def test_spec_is_anonymous_and_maps_every_shown_slot(tmp_path):
    d = write_fetch(tmp_path, "c01", "2026-11-20", gender="FEMALE")
    spec = store.spec_from(d)
    assert spec["sex"] == 1 and spec["race"] == 7 and spec["class"] == 5
    assert [it["slot"] for it in spec["items"]] == [4, 16, 19]
    assert spec["items"][1]["enchant"] == 254
    anon = store.anonymous({**spec, "name": "Someone", "guild": "X"}, "c01", ["race:gnome/female"], "2026-11-20")
    assert set(anon) <= store.ANON_KEYS
    assert "Someone" not in json.dumps(anon)


def test_cells():
    spec = {"race": 2, "sex": 1, "items": [{"slot": 1, "item_id": 1, "enchant": 0},
                                           {"slot": 15, "item_id": 2, "enchant": 0},
                                           {"slot": 16, "item_id": 3, "enchant": 5},
                                           {"slot": 17, "item_id": 4, "enchant": 0}],
            "gear": {"5": [4, 4, "CHEST"], "16": [2, 7, "17"], "17": [4, 6, "SHIELD"]},
            "choices": [{"optionId": 1, "choiceId": 11}, {"optionId": 2, "choiceId": 20}]}
    got = store.cells(spec, {1: "Piercings", 2: "Tattoo"}, {1: 10, 2: 20})
    assert got == ["armour:plate", "gear:cloak", "gear:helm", "gear:shield", "gear:weapon-glow",
                   "look:piercings", "race:orc/female"]


def test_the_repo_specs_name_nobody():
    """cases.json keeps what the assembly reads and nothing else."""
    cases = store.load_specs()
    for c in cases:
        assert set(c) <= store.ANON_KEYS, c["id"]
        assert c["id"].startswith("c") and c["id"][1:].isdigit()
        for k in ("race", "sex", "class"):
            assert isinstance(c[k], int)
        for it in c["items"]:
            assert set(it) == {"slot", "item_id", "enchant"}
        for ch in c["choices"]:
            assert set(ch) == {"optionId", "choiceId"}
        assert all(isinstance(x, str) and ":" in x for x in c["cells"])


@pytest.mark.parametrize("bad", ["http://render.worldofwarcraft.com/x.png", "https://example.com/x.png"])
def test_renders_come_only_from_blizzard(bad):
    assert store.Api().image(bad) is None


def test_a_render_written_long_after_the_login_is_someone_elses(tmp_path):
    (tmp_path / "profile.json").write_text(json.dumps({"last_login_timestamp": 1795064400000}))  # 2026-11-19 05:00 UTC
    (tmp_path / "renders.json").write_text(json.dumps({"hd": "Thu, 19 Nov 2026 05:00:02 GMT",
                                                       "sd": "Thu, 19 Nov 2026 09:30:00 GMT"}))
    assert store.foreign_render(tmp_path, "hd") == ""
    assert "4.5 h after" in store.foreign_render(tmp_path, "sd")
    (tmp_path / "renders.json").write_text(json.dumps({"hd": "Mon, 16 Nov 2026 10:00:00 GMT"}))
    assert store.foreign_render(tmp_path, "hd") == ""  # older than the login: not redrawn, still its own
