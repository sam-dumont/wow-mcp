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
# A Mage's talents as spell ID and rank: Improved Frostbolt 5/5, Arctic
# Reach 2/2, Cold Snap 1/1, Arcane Subtlety 2/2.
RANKS = {"11070": 5, "16757": 2, "12472": 1, "11210": 2}
# The same as stores.character_talents gives Battle.net's /specializations.
MAGE_BNET = {"trees": [{"tree": "Arcane", "points": 2}, {"tree": "Fire", "points": 0},
                       {"tree": "Frost", "points": 8}],
             "ranks": [{"spell": int(k), "rank": v, "name": None} for k, v in RANKS.items()],
             "refreshed": "2026-11-18T06:00:00Z"}


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
    a = talents.answer(cat, "MAGE", RANKS, "Battle.net", "2026-11-18", 32)
    assert a["available"] and a["spec"] == "Frost" and a["level"] == 32
    assert a["points"] == [{"tree": "Arcane", "points": 2}, {"tree": "Fire", "points": 0},
                           {"tree": "Frost", "points": 8}]


def test_no_talents_says_they_come_from_battlenet(cat):
    a = talents.answer(cat, "HUNTER", {}, None, None)
    assert not a["available"] and "Battle.net" in a["why"] and a["sections"] == []
    assert "simx" not in a["why"].lower()


def test_talents_come_from_battlenet_only(cat):
    """Since 2026-11-20 (Vulcan SimBench dropped): Battle.net's reading or
    nothing, and no history (its readings can't be kept)."""
    a, spec = talents.from_battlenet(cat, "MAGE", MAGE_BNET)
    assert a["available"] and a["source"] == "Battle.net" and a["at"] == "2026-11-18T06:00:00Z"
    assert (spec["name"], spec["source"], spec["split"]) == ("Frost", "Battle.net", "2/0/8")
    assert "history" not in a and "cross_check" not in a
    # No talents anywhere: no spec on the card (a gear pick is not a talent).
    a, spec = talents.from_battlenet(cat, "MAGE", None)
    assert not a["available"]
    assert spec["name"] is None and spec["split"] is None


def test_legacy_points_come_from_battlenets_achievements(cat):
    """Each Legacy challenge is an achievement, listed twice in the client
    under two ids: either one counts (owner, 2026-11-20: the API, not a
    guess from levels and skills)."""
    lg = talents.legacy(cat, {61499: "2026-11-01T10:00:00Z", 63970: "2026-11-10T10:00:00Z", 1: "x"})
    assert lg["available"] and lg["earned"] == ["Reach Level 25 for the first time on a Warrior",
                                                "Reach Level 45 for the first time on a Warrior"]
    assert lg["earned_points"] == 2 and lg["total"] == 65 and lg["spent"] is None
    assert "Battle.net" in lg["why"]
    # Battle.net read the account, nothing earned yet: 0, not unknown.
    assert talents.legacy(cat, {})["earned_points"] == 0
    # No Battle.net: unknown, never a floor.
    none = talents.legacy(cat, None)
    assert not none["available"] and none["earned_points"] is None and "Unknown" in none["why"]
    assert talents.legacy(None, {61499: "x"})["available"] is False


def test_describe_drops_what_only_the_client_knows():
    """A number the tables can't give (a spell outside the catalog, the
    caster's spell power) is left out with the words that only carry it,
    never shown as "?" (owner, 2026-11-21)."""
    assert talents.describe("Heals $s1% over $1261483d.", [5.0]) == "Heals 5%."
    assert talents.describe("Costs $/10;12536s1% less on your next 2 $lspell:spells;.", []) == (
        "Costs less on your next 2 spells.")
    assert talents.describe("By $/1000;S1 sec.", [-100.0], 3) == "By 0.3 sec."
    assert talents.describe("Increases your damage by $99999s1%.", []) == "Increases your damage."
    assert talents.describe("A $h% chance to stun the target for $99999d.", []) == "A chance to stun the target."
    assert talents.describe("Shields up to ${$s2*$pl} damage and lasts $99999d.", [1.0, 2.0]) == (
        "Shields damage.")
    assert talents.describe("Your next $m1 $lspell:spells; cast.", [1.0]) == "Your next 1 spell cast."
    assert talents.describe("Your next $m1 $lspell:spells; cast.", [2.0]) == "Your next 2 spells cast."


def test_describe_reads_the_spells_a_description_names():
    """$12345s1, $12345d, $u, $h: another spell's effect, a duration, the
    stacks and the proc chance, from the same tables as the spell's own
    numbers; ${...} formulas of those numbers are worked out."""
    spells = {"22959": {"values": [3.0, 0.0, 0.0], "dur": 30.0, "stacks": 5},
              "12536": {"values": [-1000.0, 0.0, 0.0]}}
    text = ("Increases Fire damage by $22959s1% and lasts $22959d, stacking up to $22959u times. "
            "Costs $/10;12536s1% less. Cooldown ${$s1/-1000} sec, ${$m2/10}.1 yards, $h% chance, $d.")
    me = {"chance": 4, "dur": 90.0}
    assert talents.describe(text, [-2000.0, 15.0], spell=me, spells=spells) == (
        "Increases Fire damage by 3% and lasts 30 sec, stacking up to 5 times. "
        "Costs 100% less. Cooldown 2 sec, 1.5 yards, 4% chance, 1.5 min.")


def test_no_talent_shows_a_question_mark(cat):
    """0.7.0-rc.6: talents whose numbers name another spell read "?"
    (Spirit Tap's Spirit, Improved Scorch's vulnerability). Every node of
    the fixture's frames reads its numbers or leaves them out."""
    f = talents.frame(cat, "Priest", {"15270": 5})
    tap = node(f, "Spirit Tap")[1]["desc"]
    assert "increase your Spirit by 100% for 15 sec" in tap, tap
    assert "50% of your Mana regeneration" in tap, tap
    m = talents.frame(cat, "Mage", RANKS)
    scorch = node(m, "Improved Scorch")[1]["desc"]
    assert "by 3% and lasts 30 sec, stacking up to 5 times" in scorch, scorch
    for cls in ("Priest", "Mage"):
        for kind in ("class", "legacy"):
            fr = talents.frame(cat, cls, {}, kind)
            for s in (fr or {}).get("sections") or []:
                for n in s["nodes"]:
                    assert "?" not in n["desc"] and "$" not in n["desc"], (n["name"], n["desc"])
                    assert not re.search(r"\s[,.]|\(\s*\)|\b(by|for|of|over)\s*[,.]", n["desc"]), (
                        n["name"], n["desc"])


def test_shipper_reads_no_simbench(shipper):
    """Vulcan SimBench was dropped on 2026-11-20: the shipper has no reader for it."""
    assert not any(hasattr(shipper, n) for n in ("simbench_export", "simbench_talents", "simbench_ranks"))


# ------------------------------------------------------------ the public card


class TalentStore(PoisonStore):
    """The poison store, a Mage whose talents Battle.net gives, a secret
    tucked into a rank's name; its old wow-gear rows still carry a /simx
    export (stored before 2026-11-20), which nothing reads any more."""

    def vl(self, query, keys=(), ops=None, limit=0):
        rows = super().vl(query, keys, ops, limit)
        old = [{"spell": 12345, "rank": 3}]
        return [{**r, "class": "MAGE", "talent_ranks": json.dumps(old), "talent_nodes": "52",
                 "talents_level": "19", "talents_date": "2026-11-18"} for r in rows]

    def character_talents(self, name):
        return {**MAGE_BNET, "ranks": [{**x, "name": SECRET_PLAYER} for x in MAGE_BNET["ranks"]]}


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


def test_character_battlenet_doesnt_know_gets_the_empty_card(monkeypatch, loaded):
    """No Battle.net reading (a site without the API client, or a character
    the API doesn't know): the card says so, no frame, even with an old
    /simx export stored."""

    class NoTalents(PoisonStore):
        def vl(self, query, keys=(), ops=None, limit=0):
            return [{**r, "talent_ranks": json.dumps([{"spell": 11070, "rank": 5}])}
                    for r in super().vl(query, keys, ops, limit)]

    app = ui.App({"VIEW": "public", "BIND": "127.0.0.1",
                  "MCP_PUBLIC_URL": ""}, store=NoTalents())
    d = app.char(CHAR)
    assert d["talents"]["available"] is False and d["talents"]["sections"] == []
    assert "Battle.net" in d["talents"]["why"]
    # Sex, guild and Legacy points are Battle.net's too (2026-11-20): unknown.
    assert d["legacy"]["available"] is False and d["legacy"]["earned_points"] is None
    assert "Unknown" in d["legacy"]["why"]
    full = ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MCP_PUBLIC_URL": ""}, store=NoTalents()).char(CHAR)
    assert (full["header"]["guild"], full["header"]["sex"]) == (None, None)


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


def test_battlenet_talents_name_the_spec_by_points(cat):
    a, spec = talents.from_battlenet(cat, "PRIEST", PRIEST_BNET)
    assert a["source"] == "Battle.net" and a["at"] == "2026-11-18T06:00:00Z" and a["unmatched"] == 0
    assert [(p["tree"], p["points"]) for p in a["points"]] == [("Discipline", 16), ("Holy", 0), ("Shadow", 7)]
    assert (spec["name"], spec["source"], spec["split"]) == ("Discipline", "Battle.net", "16/0/7")
    # Battle.net down or not knowing him yet: no talents, no spec.
    a, spec = talents.from_battlenet(cat, "PRIEST", None)
    assert not a["available"] and spec["name"] is None
    assert talents.capstone(cat, "Priest")["Shadow"] == "Shadowform"


class PriestStore(PoisonStore):
    """A level-32 Priest whose sheet says Shadow (GearQuestForever's pick)
    and whose talents only Battle.net knows."""

    def vl(self, query, keys=(), ops=None, limit=0):
        return [{**r, "level": "32"} if "level" in r else r for r in super().vl(query, keys, ops, limit)]

    def character_talents(self, name):
        return PRIEST_BNET

    def character_profile(self, name):
        return {"sex": "female", "guild": "Test Guild", "refreshed": "2026-11-18T06:00:00Z"}

    def account_achievements(self):
        return {61499: "2026-11-01T10:00:00Z"}


def priest_app(**env):
    return ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MCP_PUBLIC_URL": "",
                   **env}, store=PriestStore())


def test_card_shows_the_talents_and_what_the_gear_is_scored_for(loaded):
    """Two notions (owner, 2026-11-18): the talents as they are, "Discipline
    16/0/7" from Battle.net, and what the gear is scored for. The player's
    choice comes first (restored 2026-11-20): this priest picked Shadow in
    a gear addon, so the gear is scored for Shadow whatever the points say."""
    d = priest_app().char(CHAR)
    assert d["header"]["level"] == 32
    assert (d["spec"]["name"], d["spec"]["split"]) == ("Discipline", "16/0/7")
    assert d["header"]["spec"] == "Discipline"
    # Sex, guild and Legacy points from Battle.net (2026-11-20), never an addon.
    assert (d["header"]["sex"], d["header"]["guild"]) == ("female", "Test Guild")
    assert (d["legacy"]["earned_points"], d["legacy"]["earned"]) == (
        1, ["Reach Level 25 for the first time on a Warrior"])
    w = d["spec"]["scored_for"]
    assert (w["key"], w["label"], w["source"]) == ("shadow", "Shadow", "your pick")
    # Full view: 7 in Shadow, 8 more points by 40, 31 needed for Shadowform.
    assert w["sanity_check"].startswith("Shadowform at 40 = full respec into Shadow: 7 points there now")
    a = priest_app().gear(CHAR, None)["spec"]
    assert (a["key"], a["source"]) == ("shadow", "picked")
    # WEIGHTS still wins over the pick, and the points stay a hint.
    holy = priest_app(WEIGHTS="Hero-Test:holy")
    assert holy.char(CHAR)["spec"]["scored_for"]["key"] == "holy"
    assert holy.gear(CHAR, None)["spec"]["key"] == "holy"
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
    a, spec = talents.from_battlenet(cat, "PRIEST", PRIEST_BNET)
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


# ------------------------------------------------------------ the MCP reads them too


def _bnet_metrics(bnet: dict) -> list:
    """vm-wow's answer for Battle.net's talents, as tests.mcp.fakes serves it."""
    from tests.mcp import fakes

    who = {"character": fakes.CHAR, "account_id": "0"}
    return [
        ("wow_character_talent_points", [fakes._vec({**who, "tree": t["tree"], "order": str(i)}, t["points"])
                                         for i, t in enumerate(bnet["trees"])]),
        ("wow_character_talent_rank", [fakes._vec({**who, "spell_id": str(r["spell"]), "talent": r["name"]},
                                                  r["rank"]) for r in bnet["ranks"]]),
        ("wow_character_profile_refreshed_timestamp_seconds", [fakes._vec(who, 1763445600)]),
        *fakes.METRICS]


def test_mcp_reads_the_current_talents_from_battlenet(loaded):
    """Owner, 2026-11-20: the MCP's character and class tools read Battle.net's
    talents now, live from vm-wow (the dashboard's store and 23 days), never
    kept. On a multi-user install the person's "Battle.net data to my AI"
    switch decides, as for `market`."""
    from tests.mcp import fakes
    from wowstats.mcp import classtools, tools
    from wowstats.mcp.store import Store

    fake = fakes.FakeStores(metrics=_bnet_metrics(PRIEST_BNET))
    try:
        ctx = tools.Ctx(store=Store(vl_url=fake.url, vm_url=fake.url))
        out = tools.call(ctx, "character_summary", {"char": fakes.CHAR})
        assert out["talents"]["spec"] == "Discipline" and out["talents"]["source"] == "Battle.net"
        assert [t["points"] for t in out["talents"]["points"]] == [16, 0, 7]
        assert out["talents"]["as_of"].startswith("2025-11-18") and out["talents_why"] is None
        assert classtools._who(ctx, fakes.CHAR)["talents"]["spec"] == "Discipline"
        # The person turned the switch off (a multi-user install): no talents in the chat.
        off = tools.Ctx(store=Store(vl_url=fake.url, vm_url=fake.url), env={}, bnet=False)
        out = tools.call(off, "character_summary", {"char": fakes.CHAR})
        assert out["talents"] is None and "Battle.net data to my AI" in out["talents_why"]
        assert classtools._who(off, fakes.CHAR)["talents"] is None
        # No vm-wow at all: none, and the reason the page gives.
        bare = tools.Ctx(store=Store(vl_url=fake.url))
        assert tools.call(bare, "character_summary", {"char": fakes.CHAR})["talents_why"] == talents.NO_TALENTS
    finally:
        fake.close()


def test_a_talents_numbers_follow_its_rank_curve(cat):
    """Forever's trait talents keep the per-rank number in a curve
    (TraitDefinitionEffectPoints -> CurvePoint), not in the spell's effect:
    Spirit Tap's $m1 is 0 in SpellEffect and 20, 40 ... 100 by rank. 0.7.0-rc.6
    showed "0% chance" at 5/5 (owner, 2026-11-21)."""
    def desc(rank):
        return node(talents.frame(cat, "Priest", {"15270": rank}), "Spirit Tap")[1]["desc"]

    assert desc(5).startswith("Gives you a 100% chance"), desc(5)
    assert desc(3).startswith("Gives you a 60% chance"), desc(3)
    # Not learned: rank 1's number, as the game's tooltip shows.
    assert desc(0).startswith("Gives you a 20% chance"), desc(0)
    # No learned talent of the fixture's frames reads a curve's talent as 0.
    for cls, ranks in (("Priest", {b["spell"]: b["rank"] for b in PRIEST_BNET["ranks"]}), ("Mage", RANKS)):
        f = talents.frame(cat, cls, {str(k): v for k, v in ranks.items()})
        zero = [n["name"] for s in f["sections"] for n in s["nodes"] if n["rank"] and re.search(r"\b0%", n["desc"])]
        assert not zero, (cls, zero)


def test_balance_71139_replays_the_hotfix_for_existing_characters(tmp_path):
    """An existing client's pre-hotfix catalog can be read without another Wago download."""
    from wowstats import classguide, talent_hotfixes

    # Only the identities the old format saved. The purchase in Improved Wrath rank 3
    # uses its Classic rank-specific spell ID, whereas a new reading uses the trait spell.
    before = [(134384, "16814", "Improved Wrath", ["16814", "16815", "16816"], 5),
              (134392, "16821", "Improved Moonfire", ["16821", "16822"], 2),
              (134394, "16850", "Improved Starfire", ["16850", "16923", "16924"], 5),
              (134397, "16896", "Moonfury", ["16896", "16897"], 5)]
    nodes = [{"defs": [d], "spells": [s], "ranks": rs, "max": cap, "row": i, "col": 0}
             for i, (d, s, _, rs, cap) in enumerate(before)]
    section = {"name": "Balance", "order": 0, "nodes": nodes, "edges": []}
    cached = {"trees": [{"class": "DRUID", "kind": "class", "sections": [section]}],
              "spells": {s: {"name": name} for _, s, name, _, _ in before}}
    (tmp_path / "talent-catalog.json").write_text(json.dumps({"format": 6, "build": talent_hotfixes.BUILD,
                                                             "checked": time.time(), "catalog": cached}))
    cat = talents.Catalog("https://wago.tools", "wowf", tmp_path).get()
    readings = [{"spell": 16816, "rank": 3}, {"name": "Improved Moonfire", "rank": 2},
                {"spell": 16923, "rank": 2}, {"spell": 16896, "rank": 2}, {"spell": 408258, "rank": 4}]
    f = talents.frame(cat, "Druid", talents.match_ranks(cat, "Druid", readings))
    druid = classguide.reference()["classes"]["DRUID"]["talents"]
    for name, rank, count, number in (("Starlight Wrath", 3, 5, "0.3 seconds"), ("Moon and Stars", 2, 2, "100%"),
                                      ("Improved Starfire", 2, 2, "15%"), ("Dreamstate", 4, 5, "80%")):
        _, n = node(f, name)
        assert (n["rank"], n["max"]) == (rank, count)
        assert number in n["desc"] and druid[name]["max"] == count
    assert druid["Dreamstate"]["spell"] == 408258
    assert druid["Vengeance"]["needs"] == "Moon and Stars"
    assert f["unmatched"] == 1  # The historical Moonfury purchase cannot establish a Dreamstate rank.
    before_refund = {"trees": [{"tree": "Balance", "points": 9}],
                     "ranks": [{"spell": 16924, "rank": 3}, {"name": "Improved Wrath", "rank": 5}]}
    stale, spec = talents.from_battlenet(cat, "Druid", before_refund)
    assert not stale["available"] and "refund" in stale["why"] and not stale["points"]
    assert spec["name"] is None and spec["split"] is None
    old = {"trees": [], "ranks": {"improved wrath": {"spell": 16816, "rank": 3, "name": "Improved Wrath"}}}
    new = {"trees": [], "ranks": {"starlight wrath": {"spell": 16814, "rank": 3, "name": "Starlight Wrath"}}}
    assert talents.history(cat, "Druid", [old, new]) == []
    del old["ranks"]["improved wrath"]["spell"]
    assert talents.history(cat, "Druid", [old, new]) == []


def test_cached_kb_search_uses_the_current_balance_names_and_ranks(tmp_path):
    import gzip

    from wowstats import talent_hotfixes
    from wowstats.analysis.kb import GameTables

    old = [{"id": "talent:784", "kind": "talent", "title": "Improved Starfire", "body": "5 ranks"},
           {"id": "spell:16896", "kind": "spell", "title": "Moonfury", "body": "5 ranks"},
           {"id": "spell:16815", "kind": "spell", "title": "Improved Wrath", "body": "old rank 2"},
           {"id": "spell:436287", "kind": "spell", "title": "Improved Wrath", "body": "a different spell"}]
    (tmp_path / "kb-game.json.gz").write_bytes(gzip.compress(json.dumps(
        {"build": talent_hotfixes.BUILD, "checked": time.time(), "docs": old}).encode()))
    docs, build = GameTables("https://wago.tools", "wowf", tmp_path).docs()
    rows = {d["id"]: d for d in docs}
    assert len(rows) == len(docs) and build == "1.60.1.71139"
    assert rows["spell:16815"]["title"] == "Starlight Wrath" and "20%" in rows["spell:16815"]["body"]
    assert rows["spell:16924"]["title"] == "Improved Starfire (retired rank)"
    assert rows["spell:436287"] == old[-1]
    assert rows["spell:16896"]["title"] == "Moonfury (retired)"
    assert rows["talent:790"]["title"] == "Dreamstate" and "20/40/60/80/100%" in rows["talent:790"]["body"]
    assert "2 ranks" in rows["talent:784"]["body"] and "8/15%" in rows["talent:784"]["body"]
    assert rows["spell:408248"]["url"] == talent_hotfixes.SOURCE
