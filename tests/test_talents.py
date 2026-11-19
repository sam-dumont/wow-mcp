"""Talents and Legacy points (wowstats.talents), on a slice of Forever's own
tables (tests/data: the Mage trait tree and one Legacy tree of build
1.60.1.70959, as wago.tools serves them)."""

import json
import re
import time
from pathlib import Path

import pytest

from tests.test_public_view import CHAR, SECRET_PLAYER, SECRETS, PoisonStore
from tests.test_publish import TALENT_KEYS
from wowstats import redact, talents, ui

TABLES = json.loads((Path(__file__).parent / "data" / "wowf-talents-1.60.1.70959.json").read_text())
# A Mage's /simx export (VulcanSimBench `ts`): Improved Frostbolt 5/5,
# Arctic Reach 2/2, Cold Snap 1/1, Arcane Subtlety 2/2.
RANKS = {"11070": 5, "16757": 2, "12472": 1, "11210": 2}


@pytest.fixture(scope="module")
def cat():
    return talents.catalog_from(lambda name: iter(TABLES.get(name, [])))


def node(frame, name):
    return next((s["name"], n) for s in frame["sections"] for n in s["nodes"] if n["name"] == name)


def test_mage_frame_is_three_columns_on_the_games_grid(cat):
    f = talents.frame(cat, "Mage", RANKS)
    # Left to right in TalentTab.OrderIndex order (Polestar's Builds.lua numbers them the same).
    assert [s["name"] for s in f["sections"]] == ["Arcane", "Fire", "Frost"]
    assert [s["points"] for s in f["sections"]] == [2, 0, 8]
    # Forever's positions (TraitNode PosX/PosY on a 600 grid), not the classic ones.
    sec, n = node(f, "Improved Frostbolt")
    assert (sec, n["col"], n["row"], n["rank"], n["max"]) == ("Frost", 1, 0, 5, 5)
    assert n["icon"] == "spell_frost_frostbolt02"
    # The description's numbers for the rank learned: 0.1 s a rank.
    assert n["desc"] == "Reduces the casting time of your Frostbolt spell by 0.5 sec."
    _, n = node(f, "Arctic Reach")
    assert n["desc"].endswith("by 20%.")
    _, n = node(f, "Arcane Blast")  # a Forever talent with no classic one: still placed
    assert n["rank"] == 0 and n["icon"]
    assert all(0 <= n["col"] <= 3 and 0 <= n["row"] <= 7 for s in f["sections"] for n in s["nodes"])
    assert f["unmatched"] == 0


def test_answer_names_the_spec_by_most_points(cat):
    a = talents.answer(cat, "MAGE", RANKS, "VulcanSimBench /simx", "2026-11-18", 32)
    assert a["available"] and a["spec"] == "Frost" and a["level"] == 32
    assert a["points"] == [{"tree": "Arcane", "points": 2}, {"tree": "Fire", "points": 0},
                           {"tree": "Frost", "points": 8}]


def test_no_export_says_which_addon_fills_it(cat):
    a = talents.answer(cat, "HUNTER", {}, None, None)
    assert not a["available"] and "/simx" in a["why"] and a["sections"] == []


def test_history_is_one_stop_per_distinct_export(cat):
    rows = [
        {"_time": "2026-11-10T10:00:00Z", "talent_ranks": json.dumps([{"spell": 11070, "rank": 2}]),
         "talent_nodes": "52", "talents_level": "12", "talents_date": "2026-11-10"},
        # The same export on the next sheet: not a new stop.
        {"_time": "2026-11-11T10:00:00Z", "talent_ranks": json.dumps([{"spell": 11070, "rank": 2}]),
         "talent_nodes": "52", "talents_level": "12", "talents_date": "2026-11-10"},
        {"_time": "2026-11-18T10:00:00Z", "talent_ranks": json.dumps([{"spell": k, "rank": v} for k, v in RANKS.items()]),
         "talent_nodes": "52", "talents_level": "19", "talents_date": "2026-11-18"},
        {"_time": "2026-11-09T10:00:00Z"},  # a sheet with no export
    ]
    a, spec = talents.from_rows(cat, rows, "MAGE")
    assert [(h["level"], h["spec"]) for h in a["history"]] == [(12, "Frost"), (19, "Frost")]
    assert a["level"] == 19 and a["at"] == "2026-11-18" and a["source"] == "VulcanSimBench /simx"
    assert (spec["name"], spec["source"], spec["split"]) == ("Frost", "VulcanSimBench /simx", "2/0/8")
    assert "30 days" in a["history_why"]
    # No talents anywhere: no spec on the card (a gear pick is not a talent).
    a, spec = talents.from_rows(cat, [], "MAGE")
    assert not a["available"] and a["history"] == []
    assert spec["name"] is None and spec["split"] is None


def test_legacy_points_are_a_floor_from_levels_and_skills(cat):
    roster = talents.roster_rows(
        [{"char": "Testpriest-Fixture", "class": "PRIEST", "level": "47"},
         {"char": "Testhunter-Fixture", "class": "Hunter", "level": "12"}],
        [{"char": "Testpriest-Fixture", "_time": "2026-11-18T10:00:00Z",
          "professions": json.dumps([{"name": "Tailoring", "rank": 160}, {"name": "Cooking", "rank": 90}])},
         {"char": "Testpriest-Fixture", "_time": "2026-11-01T10:00:00Z",
          "professions": json.dumps([{"name": "Tailoring", "rank": 300}])}])  # older: not used
    lg = talents.legacy(cat, roster)
    assert lg["earned"] == ["Reach Level 25 for the first time on a Priest",
                            "Reach Level 45 for the first time on a Priest",
                            "Reach 150 skill in Tailoring"]
    assert lg["derived_min"] == 3 and lg["total"] == 65 and lg["spent"] is None
    assert "DataStore_Achievements" in lg["why"]
    assert talents.legacy(None, roster)["available"] is False


def test_describe_drops_what_only_the_client_knows():
    assert talents.describe("Heals $s1% over $1261483d.", [5.0]) == "Heals 5% over ?."
    assert talents.describe("Costs $/10;12536s1% less on your next 2 $lspell:spells;.", []) == (
        "Costs ?% less on your next 2 spells.")
    assert talents.describe("By $/1000;S1 sec.", [-100.0], 3) == "By 0.3 sec."


def test_shipper_reads_forever_talents(shipper, tmp_path):
    import zlib

    body = "FSX1:v=1.3.0;b=1.60.1.70959;d=2026-11-18;cls=MAGE;lvl=19;ts=11070:5,12472:1,99:0;tsn=52;g=1:2:3"
    sim = f"{body}#{zlib.adler32(body.encode()):08x}"
    (tmp_path / "VulcanSimBench.lua").write_text(f'VulcanSimBenchCharDB = {{\n["last"] = "{sim}",\n}}\n')
    got = shipper.simbench_ranks(tmp_path)
    assert got == {"ranks": [{"spell": 11070, "rank": 5}, {"spell": 12472, "rank": 1}], "nodes": 52,
                   "level": 19, "date": "2026-11-18"}
    assert shipper.simbench_talents(tmp_path) is None  # Forever has no `tal`
    (tmp_path / "VulcanSimBench.lua").write_text("VulcanSimBenchCharDB = {\n}\n")  # Testhunter: never ran /simx
    assert shipper.simbench_ranks(tmp_path) is None


# ------------------------------------------------------------ the public card


class TalentStore(PoisonStore):
    """The poison store, its wow-gear rows carrying a /simx export with a
    secret tucked into a rank."""

    def vl(self, query, keys=(), ops=None, limit=0):
        rows = super().vl(query, keys, ops, limit)
        ranks = [{"spell": int(k), "rank": v, "player": SECRET_PLAYER} for k, v in RANKS.items()]
        return [{**r, "class": "MAGE", "talent_ranks": json.dumps(ranks), "talent_nodes": "52",
                 "talents_level": "19", "talents_date": "2026-11-18"} for r in rows]


@pytest.fixture
def loaded(cat, monkeypatch):
    c = talents.Catalog("http://wago.invalid", "wowf", None)
    c.data, c.checked = cat, time.time()
    monkeypatch.setattr(talents, "_shared", c)
    monkeypatch.setattr(talents, "icon_bytes", lambda name, size="medium": b"\xff\xd8jpeg " + name.encode())
    return c


def paths(v, prefix=""):
    if isinstance(v, dict):
        for k, x in v.items():
            yield prefix + k
            yield from paths(x, prefix + k + ".")
    elif isinstance(v, list):
        for x in v:
            yield from paths(x, prefix[:-1] + "[]." if prefix else "[].")


@pytest.mark.parametrize("tier", ["public", "zogzog"])
def test_card_tiers_carry_the_talents_and_nothing_more(loaded, tier):
    app = ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MCP_PUBLIC_URL": ""},
                 store=TalentStore())
    full = app.char(CHAR)
    assert full["talents"]["available"] and full["talents"]["spec"] == "Frost"
    out = redact.TIERS[tier]("char", full)
    got = {p for p in paths(out) if p.split(".")[0] in ("spec", "talents", "legacy")}
    assert got == TALENT_KEYS
    assert SECRET_PLAYER not in json.dumps(out)
    frost = next(s for s in out["talents"]["sections"] if s["name"] == "Frost")
    assert frost["points"] == 8


def test_public_bundle_has_talents_and_their_icons(loaded, tmp_path, monkeypatch):
    from tests.test_publish import assert_public_allowlist, files, test_zogzog_bundle_holds_only_its_allowlist
    from wowstats import publish
    from wowstats.plan import facts

    monkeypatch.setattr(ui, "Store", lambda *a, **k: TalentStore())
    # The Dungeons panel reads through its own Store (facts.store_for): the
    # fake one, as in test_publish, or this passed only when an earlier test
    # had left plan.inputs' two-minute cache warm.
    monkeypatch.setattr(facts, "store_for", lambda env, like=None: like)
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(tmp_path / "zogzog.key")}
    publish.publish(env)
    pub = files(tmp_path / "site" / "current" / "public")
    assert_public_allowlist(pub)
    d = json.loads(pub[publish.data_path("char", CHAR)])
    assert d["talents"]["spec"] == "Frost" and (d["spec"]["name"], d["spec"]["split"]) == ("Frost", "2/0/8")
    icons = {n["icon"] for s in d["talents"]["sections"] for n in s["nodes"] if n["icon"]}
    assert icons and all(pub[f"data/icon/medium/{i}.jpg"].endswith(i.encode()) for i in icons)
    for body in pub.values():
        for s in SECRETS:
            assert s.encode() not in body
    # The sealed bundle holds them too, as ciphertext.
    zz = files(tmp_path / "site" / "current" / "zogzog")
    assert any(re.match(r"data/icon/medium/[\w-]+\.jpg\.enc$", p) for p in zz)
    site = (env, None, (tmp_path / "zogzog.key").read_text().strip(), tmp_path / "site" / "current")
    test_zogzog_bundle_holds_only_its_allowlist(site)


def test_hunter_without_an_export_gets_the_empty_card(monkeypatch, loaded):
    """A character that never ran /simx: the card says so, no frame."""

    class NoTalents(PoisonStore):
        def vl(self, query, keys=(), ops=None, limit=0):
            return [] if "talent_ranks" in query else super().vl(query, keys, ops, limit)

    app = ui.App({"VIEW": "public", "BIND": "127.0.0.1",
                  "MCP_PUBLIC_URL": ""}, store=NoTalents())
    d = app.char(CHAR)
    assert d["talents"]["available"] is False and d["talents"]["sections"] == []
    assert "/simx" in d["talents"]["why"]
    assert d["legacy"]["available"] is True and "DataStore_Achievements" in d["legacy"]["why"]


# ------------------------------------------------------------ Battle.net, the spec line and the gear weights

# Testpriest on 2026-11-18, as stores.character_talents gives Battle.net's
# /specializations: Discipline 16, Shadow Magic 7, Holy 0 (level 32). Spirit
# Tap comes as its classic rank-5 spell and Twin Disciplines under an id the
# catalog doesn't have: matched by rank spell and by name.
PRIEST_BNET = {
    "trees": [{"tree": "Discipline", "points": 16}, {"tree": "Shadow Magic", "points": 7},
              {"tree": "Holy", "points": 0}],
    "ranks": [{"spell": 14524, "rank": 2, "name": "Wand Specialization"},
              {"spell": 999001, "rank": 5, "name": "Twin Disciplines"},
              {"spell": 18551, "rank": 3, "name": "Mental Strength"}, {"spell": 14531, "rank": 2, "name": "Martyrdom"},
              {"spell": 14751, "rank": 1, "name": "Inner Focus"}, {"spell": 14521, "rank": 3, "name": "Meditation"},
              {"spell": 15338, "rank": 5, "name": "Spirit Tap"},
              {"spell": 15275, "rank": 2, "name": "Improved Shadow Word: Pain"}],
    "refreshed": "2026-11-18T06:00:00Z"}


def test_battlenet_talents_come_first_and_name_the_spec_by_points(cat):
    simx = [{"_time": "2026-11-12T10:00:00Z", "talent_ranks": json.dumps([{"spell": 15270, "rank": 5}]),
             "talent_nodes": "60", "talents_level": "28", "talents_date": "2026-11-12"}]
    a, spec = talents.from_rows(cat, simx, "PRIEST", PRIEST_BNET)
    assert a["source"] == "Battle.net" and a["at"] == "2026-11-18T06:00:00Z" and a["unmatched"] == 0
    assert [(p["tree"], p["points"]) for p in a["points"]] == [("Discipline", 16), ("Holy", 0), ("Shadow", 7)]
    assert (spec["name"], spec["source"], spec["split"]) == ("Discipline", "Battle.net", "16/0/7")
    assert a["cross_check"] == "The last /simx (2026-11-12) differs on 7 talents"
    # The history is the /simx exports alone: Battle.net's readings aren't kept.
    assert [h["level"] for h in a["history"]] == [28]
    # Battle.net down or not knowing him yet: the last /simx.
    a, spec = talents.from_rows(cat, simx, "PRIEST", None)
    assert a["source"] == "VulcanSimBench /simx" and (spec["name"], spec["split"]) == ("Shadow", "0/0/5")
    assert talents.capstone(cat, "Priest")["Shadow"] == "Shadowform"


class PriestStore(PoisonStore):
    """A level-32 Priest whose sheet says Shadow (GearQuestForever's pick)
    and whose talents only Battle.net knows."""

    def vl(self, query, keys=(), ops=None, limit=0):
        return [{**r, "level": "32"} if "level" in r else r for r in super().vl(query, keys, ops, limit)]

    def character_talents(self, name):
        return PRIEST_BNET


def priest_app(**env):
    return ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MCP_PUBLIC_URL": "",
                   **env}, store=PriestStore())


def test_card_shows_the_talents_and_what_the_gear_is_scored_for(loaded):
    """Two notions (owner, 2026-11-18): the talents as they are, "Discipline
    16/0/7", and the gear weights, the player's choice: Shadow, from the
    add-on pick, untouched by the point count."""
    d = priest_app().char(CHAR)
    assert d["header"]["level"] == 32
    assert (d["spec"]["name"], d["spec"]["split"]) == ("Discipline", "16/0/7")
    assert d["header"]["spec"] == "Discipline"
    w = d["spec"]["scored_for"]
    assert (w["key"], w["label"], w["source"]) == ("shadow", "Shadow", "your pick")
    # Full view: 7 in Shadow, 8 more points by 40, 31 needed for Shadowform.
    assert w["sanity_check"].startswith("Shadowform at 40 = full respec into Shadow: 7 points there now")
    # The advice keeps Shadow weights too.
    assert priest_app().gear(CHAR, None)["spec"]["key"] == "shadow"
    # Public and /zogzog/: the line, not the planning note.
    for tier in ("public", "zogzog"):
        out = redact.TIERS[tier]("char", d)
        assert out["spec"]["scored_for"] == {"key": "shadow", "label": "Shadow", "source": "your pick"}
        assert out["spec"]["split"] == "16/0/7"


def test_weights_setting_overrides_the_pick(loaded):
    app = priest_app(WEIGHTS="Someone-Else:discipline, Hero-Test:holy")
    w = app.char(CHAR)["spec"]["scored_for"]
    assert (w["key"], w["source"]) == ("holy", "site setting")
    a = app.gear(CHAR, None)
    assert a["spec"]["key"] == "holy" and a["spec"]["source"] == "server" and "WEIGHTS" in a["spec"]["why"]
    # The page's own choice still wins over the server's.
    assert app.gear(CHAR, "discipline")["spec"]["key"] == "discipline"
    assert ui.parse_weights("") == {}
    assert ui.parse_weights("Testpriest Fixture:Shadow") == {"Testpriest-Fixture": "shadow"}
    with pytest.raises(ui.ConfigError):
        ui.parse_weights("Testpriest-Fixture")


def test_no_sanity_line_when_the_plan_holds(loaded):
    from wowstats import gear

    ref = gear.reference()
    pts = {"Discipline": 16, "Holy": 0, "Shadow": 7}
    assert gear.capstone_check(ref, "PRIEST", "shadow", 32, pts, "Shadowform")  # 7 + 8 < 31
    # 31 points by 40 is every point there is: any off-tree point means a
    # respec, whichever tree the weights name.
    assert "16 points there now" in gear.capstone_check(ref, "PRIEST", "discipline", 32, pts)
    assert gear.capstone_check(ref, "PRIEST", "shadow", 25, pts) is None  # too early to say
    assert gear.capstone_check(ref, "PRIEST", "shadow", 40, pts) is None  # past it
    assert gear.capstone_check(ref, "PRIEST", "shadow", 32, {"Discipline": 0, "Holy": 0, "Shadow": 23}) is None


def test_capstone_level_comes_from_the_kb(loaded, monkeypatch):
    """talents.points sets the check: first point at 10 and a 31-point
    capstone mean level 40; another rule moves it; no rule, no check."""
    from wowstats import gear
    from wowstats.analysis import kb

    ref = gear.reference()
    pts = {"Discipline": 16, "Holy": 0, "Shadow": 7}
    assert gear.capstone_rule() == (40, 31)
    monkeypatch.setattr(kb, "static_rule", lambda n: {"value": {"first_level": 5, "capstone_points": 31}})
    assert gear.capstone_rule() == (35, 31)
    assert "at 35 = full respec" in gear.capstone_check(ref, "PRIEST", "shadow", 32, pts, "Shadowform")
    monkeypatch.setattr(kb, "static_rule", lambda n: None)
    assert gear.capstone_check(ref, "PRIEST", "shadow", 32, pts, "Shadowform") is None


# Forever's priest frame, TraitTree 1114 of build 1.60.1.70959
# (wago.tools/db2/TraitNode/csv?build=1.60.1.70959, PosX/PosY on a 600 grid,
# Discipline from X 1020, Holy 5020, Shadow 9080, rows from Y 2130):
# (tree, column, row, max rank) of each talent Testpriest has, from
# TraitNode and TraitNodeEntry.MaxRanks. Forever moved and re-ranked
# talents (Wand Specialization is 2 ranks, classic's 5; Twin Disciplines is
# Forever's own), so the classic Talent table's TierID/ColumnIndex don't
# describe this frame.
PRIEST_PLACES = {
    "Wand Specialization": ("Discipline", 1, 0, 2, 2),
    "Twin Disciplines": ("Discipline", 2, 0, 5, 5),
    "Mental Strength": ("Discipline", 2, 1, 3, 5),
    "Martyrdom": ("Discipline", 3, 1, 2, 2),
    "Inner Focus": ("Discipline", 1, 2, 1, 1),
    "Meditation": ("Discipline", 3, 2, 3, 3),
    "Spirit Tap": ("Shadow", 2, 0, 5, 5),
    "Improved Shadow Word: Pain": ("Shadow", 2, 1, 2, 2),
}


def test_the_priests_talents_sit_where_the_game_draws_them(cat):
    a, spec = talents.from_rows(cat, [], "PRIEST", PRIEST_BNET)
    got = {n["name"]: (s["name"], n["col"], n["row"], n["rank"], n["max"])
           for s in a["sections"] for n in s["nodes"] if n["rank"]}
    assert got == PRIEST_PLACES
    # The game's tab order, every tree: 16/0/7.
    assert [s["name"] for s in a["sections"]] == ["Discipline", "Holy", "Shadow"]
    assert spec["split"] == "16/0/7" and a["unmatched"] == 0
    # Every tree is the game's 4 x 7 grid, one talent a cell; the stray
    # duplicate of Holy Specialization (X 9280, Y 21300) isn't drawn, which
    # 0.2.2's smallest-gap step (400) turned into a shifted Shadow tree.
    for s in a["sections"]:
        cells = [(n["col"], n["row"]) for n in s["nodes"]]
        assert all(0 <= c <= 3 and 0 <= r <= 6 for c, r in cells), (s["name"], cells)
        assert len(cells) == len(set(cells)), s["name"]
    assert [n["name"] for s in a["sections"] for n in s["nodes"]].count("Holy Specialization") == 1
    shadow = next(s for s in a["sections"] if s["name"] == "Shadow")
    assert node(talents.frame(cat, "Priest", {}), "Shadowform")[1]["row"] == 6
    # The prerequisite arrows (TraitEdge), as cells: Vampiric Embrace above
    # Shadowform, Mind Flay beside Improved Mind Flay.
    assert {"fc": 1, "fr": 3, "tc": 1, "tr": 6} in shadow["edges"]
    assert {"fc": 2, "fr": 2, "tc": 3, "tr": 2} in shadow["edges"]
    disc = next(s for s in a["sections"] if s["name"] == "Discipline")
    assert {"fc": 1, "fr": 4, "tc": 1, "tr": 6} in disc["edges"]  # Penance -> Power Infusion
    # Every talent has its icon, Devouring Contagion's "Spell_Shadow_DevouringPlague..blp" too.
    assert [n["name"] for s in a["sections"] for n in s["nodes"] if not n["icon"]] == []
    assert node(a, "Devouring Contagion")[1]["icon"] == "spell_shadow_devouringplague"
