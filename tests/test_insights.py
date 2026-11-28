"""wowstats.insights: the rules behind the Overview, next_moves and
sanity_check, on rows shaped like the real Polestar_Recorder records."""

from datetime import UTC, datetime

from wowstats import insights
from wowstats.analysis import pages
from wowstats.views import Views

CHAR = "Testchar-Insights"


def row(t, kind, **kw):
    return {"_time": t, "kind": kind, "source_id": f"rec:x:{t}:{kind}", **kw}


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")


def layer(ds=(), levels=()):
    """The semantic layer (pages.asker) on events shaped like the store's:
    each death of `ds` a death record with its likely killer, placed at its
    level by a session record just before it; each of `levels` a Polestar
    split (wow-level)."""
    rows = []
    for i, d in enumerate(ds):
        t, lv, k = 1794000000 + i * 3600, d.get("level") or 20, d.get("killer") or {}
        npc_level = lv + d["level_gap"] if d.get("level_gap") is not None else k.get("level")
        rows += [{"_time": _iso(t), "feed": "wow-recorder", "char": CHAR, "kind": "session", "level": str(lv),
                  "source_id": f"rec:s:{i}"},
                 {"_time": _iso(t + 60), "feed": "wow-recorder", "char": CHAR, "kind": "death", "zone": d.get("zone"),
                  "npc_id": str(k.get("npc_id") or ""), "npc_level": str(npc_level or ""), "source_id": f"rec:d:{i}"}]
    for i, lv in enumerate(levels):
        rows.append({"_time": lv.get("at") or _iso(1793000000 + i * 86400), "feed": "wow-level", "char": CHAR,
                     "level": str(lv["level"]), "seconds": str(lv["seconds"]), "xp": str(lv.get("xp") or 1000),
                     "partial": "true" if lv.get("partial") else "false", "source_id": f"lvl:{lv['level']}"})
    from wowstats.analysis import copies

    return pages.asker(CHAR, copies.build(rows, CHAR))


DEATH = row("2026-11-16T19:20:22.3Z", "death", zone="Wetlands", npc_id="271351", npc_level="30",
            npc_name="Dragonmaw Reclaimer")
FIGHT = [
    row("2026-11-16T19:17:24.2Z", "combat", on="false"),
    row("2026-11-16T19:18:49.7Z", "combat", on="true"),
    row("2026-11-16T19:20:05Z", "unit", npc_id="271351", level="30", reaction="h"),
    row("2026-11-16T19:20:12Z", "unit", npc_id="1051", level="28", reaction="h"),
    row("2026-11-16T19:20:15Z", "kill", npc_id="1051"),
]


def test_likely_killer_from_the_recorder_alone():
    d = insights.fight_before(DEATH, FIGHT + [DEATH], 30)
    assert d["killer"] == {"npc_id": 271351, "name": "Dragonmaw Reclaimer", "named": True, "level": 30, "likely": True,
                           "how": "the recorder's own pick: the last hostile creature that targeted you within 10 s"}
    assert d["fight_s"] == 92 and d["attackers"] == 2 and d["killed_in_fight"] == 1 and d["level_gap"] == 0


def test_killer_falls_back_to_the_last_hostile_unit():
    d = insights.fight_before({**DEATH, "npc_id": "", "npc_level": "", "npc_name": None}, FIGHT, 25)
    assert d["killer"]["npc_id"] == 1051 and d["killer"]["likely"] and d["level_gap"] == 3
    assert "last hostile creature seen" in d["killer"]["how"]


def test_a_killer_is_named_from_game_data_never_by_its_id():
    """The recorder logged no name for the creature: forever-db's name (the
    knowledge base's game data); with none there either, what the recorder
    saw of it (its classification), never "creature 1871"."""
    nameless = {**DEATH, "npc_id": "1871", "npc_level": "30", "npc_name": None}
    d = insights.fight_before(nameless, FIGHT, 30)
    assert d["killer"]["name"] == "Eliza's Guard" and d["killer"]["named"]
    unknown = {**DEATH, "npc_id": "999999", "npc_level": "34", "npc_name": None}
    fight = FIGHT + [row("2026-11-16T19:20:18Z", "unit", npc_id="999999", level="34", reaction="h",
                         classification="elite")]
    d = insights.fight_before(unknown, fight, 30)
    assert d["killer"] == {**d["killer"], "npc_id": 999999, "name": "an elite creature", "named": False}
    assert insights.fight_before(unknown, FIGHT, 30)["killer"]["name"] == "an unnamed creature"
    for k in (d["killer"], insights.fight_before(unknown, FIGHT, 30)["killer"]):
        assert "999999" not in k["name"] and "creature 9" not in k["name"]


def test_death_clusters():
    k = {"npc_id": 271351, "name": "Dragonmaw Reclaimer", "level": 30}
    ds = [{"zone": "Wetlands", "killer": k, "level_gap": 0}] * 2 + [
        {"zone": "Wetlands", "killer": {**k, "npc_id": 1, "name": "Ogre"}, "level_gap": 4}]
    by = {c["by"]: c for c in insights.death_clusters(layer(ds), ds)}
    assert by["zone"]["n"] == 3 and by["creature"]["n"] == 2 and by["level gap"]["gap"] == 4


def test_profession_capped_at_its_tier():
    """Where to train, for the character's side, what it needs and costs,
    from Polestar's guides: never invented. The cap is the game's skill list
    (DataStore_Crafts on the sheet), so the move says it flat out."""
    hd = {"faction": "Alliance", "race": "Gnome"}
    skills = {333: {"rank": 150, "max_rank": 150, "t": "2026-01-02T10:00:00.000Z"},
              197: {"rank": 119, "max_rank": 150, "t": "2026-01-02T10:00:00.000Z"}}
    c = insights.profession_claims(32, [{"skill_id": 333, "name": "Enchanting", "rank": 150, "cap": 150,
                                         "cap_at": 1767348000},
                                        {"skill_id": 197, "name": "Tailoring", "rank": 119}], skills, hd)
    assert len(c) == 1 and c[0]["link"] == "#gear" and c[0]["title"] == "Train Expert Enchanting"
    assert c[0]["action"] is None and not c[0]["skill"]["may_be_stale"]
    assert c[0]["text"] == ("Capped at 150: skill-ups are wasted until you train Expert Enchanting. Kitta Firewind, "
                            "Elwynn Forest (64.8, 70.6); needs level 20 (met); 50 silver (before a reputation discount).")
    sk = c[0]["skill"]
    assert (sk["need_level"], sk["level_ok"], sk["need_skill"], sk["skill_ok"], sk["cost_copper"]) == (20, True, 125,
                                                                                                       True, 5000)
    assert [t["name"] for t in sk["trainers"]] == ["Kitta Firewind"]  # Hgarth is the Horde's
    assert sk["trainers"][0]["url"] == "https://www.wowhead.com/forever/npc=11072"
    assert sk["trainers"][0]["zone"]["url"] == "https://www.wowhead.com/forever/zone=12"
    assert c[0]["detail"] is None
    horde = insights.profession_claims(32, [{"skill_id": 333, "rank": 150}], skills, {"faction": "Horde"})
    assert [t["name"] for t in horde[0]["skill"]["trainers"]] == ["Hgarth"]


def test_profession_cap_from_the_tiers_hedges():
    """No Recorder record of the skill: the cap is the trainer tier over the
    rank, a floor (at 150 the character may have trained Expert already), so
    the move says "if" and how the real cap shows up. An undated cap
    cannot tell us which learning of the profession it belongs to."""
    p = {"skill_id": 164, "name": "Blacksmithing", "rank": 148, "max_rank": 300,
         "max_rank_at": None}
    c = insights.profession_claims(30, [p], {}, {"faction": "Horde"})
    assert c[0]["title"] == "Train Expert Blacksmithing, if not done yet" and c[0]["skill"]["may_be_stale"]
    assert c[0]["skill"]["max_rank"] == 150
    assert c[0]["text"].startswith("Blacksmithing 148/150 if you haven't trained Expert yet: skill-ups stop at 150 "
                                   "until you do. ")
    assert c[0]["detail"] == ("Blacksmithing 148/150 if you haven't trained Expert yet: skill-ups stop at 150 "
                              "until you do.")
    assert c[0]["action"] == "Trained Expert already? With Altoholic installed the cap is read at each login; " \
                             "else it shows at your next Blacksmithing skill-up."
    assert c[0]["text"].endswith(c[0]["action"])
    assert "CharacterExport" not in c[0]["cite"]


def test_profession_recorder_cap_wins_unless_the_rank_passed_it():
    """The Recorder's cap stands while it covers the rank, but it only saw
    what changed while it recorded, so the move hedges with the day it last
    saw it; a rank above it (trained while the Recorder was off) falls back
    to the tiers."""
    p = {"skill_id": 197, "name": "Tailoring", "rank": 150}
    trained = {197: {"rank": 151, "max_rank": 225, "t": "2026-01-02T11:00:00.000Z"}}
    assert insights.profession_claims(30, [p], trained, {"faction": "Alliance"}) == []
    behind = {197: {"rank": 140, "max_rank": 150, "t": "2026-01-01T09:00:00.000Z"}}
    c = insights.profession_claims(30, [p], behind, {"faction": "Alliance"})
    assert c[0]["title"] == "Train Expert Tailoring, if not done yet" and c[0]["skill"]["may_be_stale"]
    assert c[0]["detail"] == ("Tailoring 150/150 when Polestar Recorder last saw it (1 Jan): if you haven't "
                              "trained Expert since, skill-ups stop at 150 until you do.")
    passed = {197: {"rank": 140, "max_rank": 150, "t": "2026-01-01T09:00:00.000Z"}}
    assert insights.profession_claims(30, [{**p, "rank": 160}], passed, {"faction": "Alliance"}) == []


def test_an_expert_the_recorder_missed_gets_no_train_expert():
    """2026-11-24: an Expert enchanter (150/225 in game) was told to train
    Expert. The Recorder writes a skill record only when the skill list
    changes while it records (its login baseline is silent), so its last
    record stayed 150/150 from before the trainer visit. The game's own
    list on the sheet (DataStore_Crafts, read at each login) says 225: no
    move. Shapes as stored: the Recorder's record and the shipper's sheet."""
    rec = {333: {"rank": 150, "max_rank": 150, "t": "2026-01-01T14:28:40.400Z"}}
    sheet = [{"skill_id": 333, "name": "Enchanting", "rank": 150, "cap": 225, "cap_at": 1767650000}]
    hd = {"faction": "Alliance", "race": "Gnome"}
    assert insights.profession_claims(34, sheet, rec, hd) == []
    from wowstats import profcaps

    assert profcaps.fill(sheet, rec)[0] | {"cap": None, "cap_at": None} == {
        "skill_id": 333, "name": "Enchanting", "rank": 150, "max_rank": 225, "max_rank_from": "game",
        "cap": None, "cap_at": None}
    # The Recorder newer and higher (Expert trained after the login scan):
    # caps only go up, the higher wins.
    assert profcaps.cap_of(150, {"rank": 150, "max_rank": 225}, {"cap": 150, "cap_at": 1}) == (150, 225, "recorder")


def test_profcaps_fill_says_where_each_cap_comes_from():
    from wowstats import profcaps

    profs = [{"skill_id": 185, "name": "Cooking", "rank": 130, "max_rank": 225, "max_rank_at": "x"},
             {"skill_id": 197, "name": "Tailoring", "rank": 119}]
    got = profcaps.fill(profs, profcaps.newest_skills([
        {"_time": "2026-01-01T10:00:00Z", "skill_id": "197", "rank": "100", "max_rank": "150"},
        {"_time": "2026-01-02T10:00:00Z", "skill_id": "197", "rank": "119", "max_rank": "225"}]))
    assert got == [{"skill_id": 185, "name": "Cooking", "rank": 130, "max_rank": 150, "max_rank_from": "tiers"},
                   {"skill_id": 197, "name": "Tailoring", "rank": 119, "max_rank": 225, "max_rank_from": "recorder"}]


def test_profcaps_as_they_were_at_a_level():
    """A level's snapshot reads the caps the Recorder had seen by then, not
    today's (the sheet no longer carries them since CharacterExport went)."""
    from wowstats import profcaps
    from wowstats.stores import parse_t

    rows = [{"_time": "2026-01-01T10:00:00Z", "skill_id": "197", "rank": "100", "max_rank": "150"},
            {"_time": "2026-01-03T10:00:00Z", "skill_id": "197", "rank": "151", "max_rank": "225"}]
    then = profcaps.newest_skills(rows, until=parse_t("2026-01-02T00:00:00Z").timestamp())
    assert then[197]["max_rank"] == 150 and profcaps.newest_skills(rows)[197]["max_rank"] == 225
    got = profcaps.fill([{"skill_id": 197, "name": "Tailoring", "rank": 119}], then)
    assert got[0]["max_rank"] == 150 and got[0]["max_rank_from"] == "recorder"
    assert profcaps.newest_skills(rows, until=0) == {}


def test_profession_without_a_trainer_in_the_data_says_so():
    """Artisan Enchanting's trainer stands inside Uldaman with no spawn in
    the data: no location is made up, Wowhead's search stands in."""
    c = insights.profession_claims(40, [{"skill_id": 333, "rank": 225, "max_rank": 225}], {}, {"faction": "Alliance"})
    assert "trainer location not in our data" in c[0]["text"]
    # The guide has no price for it: the KB's profession.training_cost gives one.
    assert "5 gold (before a reputation discount)" in c[0]["text"] and c[0]["skill"]["cost_copper"] == 50000
    assert c[0]["skill"]["trainers"] == []
    assert c[0]["skill"]["search_url"].startswith("https://www.wowhead.com/forever/search?q=Artisan%20Enchanting")


def test_profession_level_not_met_is_said_and_weighs_less():
    c = insights.profession_claims(15, [{"skill_id": 164, "rank": 150, "max_rank": 150}], {}, {"faction": "Horde"})
    assert "needs level 20 (you are 15)" in c[0]["text"] and not c[0]["skill"]["level_ok"]
    assert c[0]["weight"] < 3


def test_riding_names_the_race_trainer_and_the_gold_to_go():
    hd = {"faction": "Alliance", "race": "Gnome", "money_copper": 123456}
    r = insights.riding_claims(32, hd, {})
    # The bag counted to the silver; what's left is the price less that: the two add up.
    assert r[0]["text"] == ("Riding at level 40 (you are 32): Binjy Featherwhistle, Dun Morogh (49.2, 48.1); 100 gold "
                            "(before a reputation discount); you have 12 gold 34 silver; 87 gold 66 silver to go.")
    assert r[0]["skill"]["money_copper"] == 123400
    assert insights.riding_claims(31, hd, {}) == []  # more than RIDING_AHEAD levels off
    assert insights.riding_claims(40, hd, {762: {"rank": 75}}) == []  # already rides
    assert insights.riding_claims(40, {"race": "Night Elf"}, {})[0]["skill"]["trainers"][0]["name"] == "Jartsam"


def _rules(monkeypatch, **over):
    """kb.static_rule with some rules replaced (None: the KB doesn't know it)."""
    from wowstats.analysis import kb

    real = kb.static_rule
    monkeypatch.setattr(kb, "static_rule", lambda n: over[n.replace(".", "_")] if n.replace(".", "_") in over
                        else real(n))


def test_profession_ranks_gates_and_price_come_from_the_kb(monkeypatch):
    """profession.tiers and profession.training_cost drive the move: a
    changed rule changes the gate and the price, no rule means no move."""
    tiers = [{"rank": 1, "name": "Apprentice", "cap": 75, "need_level": None, "need_skill": 0},
             {"rank": 2, "name": "Journeyman", "cap": 150, "need_level": 12, "need_skill": 60}]
    _rules(monkeypatch, profession_tiers={"value": tiers}, profession_training_cost={"value": {"2": 777}})
    c = insights.profession_claims(11, [{"skill_id": 202, "rank": 75, "max_rank": 75}], {}, {"faction": "Horde"})
    sk = c[0]["skill"]
    assert c[0]["title"] == "Train Journeyman Engineering, if not done yet"
    # The guide's Journeyman Engineering price is Classic Era's: the KB's wins; its level gate (10) stays the guide's.
    assert (sk["need_level"], sk["need_skill"], sk["cost_copper"]) == (10, 60, 777)
    assert "needs level 10 (met)" in c[0]["text"] and "7 silver 77 copper" in c[0]["text"]
    assert insights.profession_claims(40, [{"skill_id": 202, "rank": 150, "max_rank": 150}], {}, {}) == []
    _rules(monkeypatch, profession_tiers=None)
    assert insights.profession_claims(11, [{"skill_id": 202, "rank": 75, "max_rank": 75}], {}, {}) == []


def test_riding_level_and_price_come_from_the_kb(monkeypatch):
    hd = {"faction": "Alliance", "race": "Gnome", "money_copper": 0}
    _rules(monkeypatch, riding_apprentice={"value": {"level": 30, "training_copper": 420000},
                                           "source_url": "https://example.invalid", "last_verified": "2026-11-18"})
    r = insights.riding_claims(25, hd, {})
    assert r[0]["skill"]["need_level"] == 30 and r[0]["skill"]["cost_copper"] == 420000
    assert r[0]["text"].startswith("Riding at level 30 (you are 25)") and "42 gold" in r[0]["text"]
    _rules(monkeypatch, riding_apprentice=None)
    assert insights.riding_claims(40, hd, {}) == []  # unknown: no move, never a guess


def test_deaths_in_a_zone_say_what_to_change_or_stay_out():
    """Counts only (no creature named): the level gap and the fights against
    several give the next step; a zone outgrown, fought one at a time, has
    none and isn't a move."""
    ds = [{"zone": "Wetlands", "level": 24, "level_gap": 4, "attackers": 1},
          {"zone": "Wetlands", "level": 25, "level_gap": 5, "attackers": 2},
          {"zone": "Wetlands", "level": 25, "level_gap": 0, "attackers": 2}]
    c = insights.deaths_zone_claim("Wetlands", ds, 26, layer(ds))
    assert c["text"] == ("3 deaths in Wetlands at level 24-25: 2 to creatures 3+ levels above you (up to 5 above); "
                         "2 with more than one attacker. Next time: come back at level 30 (you are 26) and pull one "
                         "at a time.")
    assert c["deaths"] == {"n": 3, "levels": [24, 25], "above": 2, "crowd": 2, "come_back_at": 30}
    alone = [{**d, "attackers": 1} for d in ds]
    assert insights.deaths_zone_claim("Wetlands", alone, 32, layer(alone)) is None


def test_every_claim_cites_its_data():
    levels = [{"level": lv, "seconds": s, "xp": 1, "partial": False, "at": f"2026-11-1{lv - 20}T10:00:00Z"}
              for lv, s in ((23, 8056), (24, 8542), (25, 7030), (26, 5978), (27, 10030))]
    tr = insights.on_track(layer(levels=levels), {"level": 28, "eta_s": 3600, "xp_per_hour": 1})
    assert tr["claims"]
    sc = insights.sanity_check(28, [], {"claims": []}, tr, None, [], {}, [{"points": 10}], ask=layer())
    assert sc["findings"][0]["text"] == "9 talent points unspent: spend them in the talent pane (N), anywhere."
    assert sc["not_possible_yet"]
    for c in tr["claims"] + sc["findings"]:
        assert c["link"].startswith("#") and c["cite"]


def test_an_empty_character_has_no_claims():
    ov = insights.overview({"header": {}, "levels": [], "steps": {}}, None, [], None, {}, [], [], layer())
    assert ov["now"] == [] and ov["guide"]["claims"] == [] and ov["deaths"] == [] and ov["upgrades"] == []


def test_guide_picks_are_not_steps():
    from wowstats.views import build_steps

    g = "forever/alliance/03-darkshore-return"
    rows = [
        {"_time": "2026-11-17T12:00:00Z", "kind": "step", "guide": g, "step": "", "reason": "pick", "duration_s": "900"},
        {"_time": "2026-11-17T12:01:00Z", "kind": "step", "guide": g, "step": "1", "reason": "auto", "duration_s": "60"},
        {"_time": "2026-11-17T12:03:00Z", "kind": "step", "guide": g, "step": "2", "reason": "next", "duration_s": "120"},
        {"_time": "2026-11-17T12:04:00Z", "kind": "step", "guide": "forever/lookup/pickup/stormwind-stockade",
         "step": "4", "reason": "pick", "duration_s": "60"},
    ]
    st = build_steps(rows, Views.route_ref())
    assert st["total"] == 2 and st["picks_total"] == 2
    assert all(isinstance(a["step"], int) for a in st["slowest"])
    assert st["median_s"] == 120, "the 900 s before the pick isn't a step"
    assert st["picks"][1]["title"] == "Darkshore"
    assert st["picks"][0]["title"] == "Pickup: Stormwind Stockade"
    assert st["slowest"][0]["title"], "a numbered step carries the guide's own text"


# Upgrades as the gear advice answers them (wowstats.gear): wearable now,
# best first, each with its sources.
def up(item_id, slot, gain, *sources, share=12.0):
    return {"item_id": item_id, "name": f"Item {item_id}", "quality": 3, "slot": slot, "slot_name": f"Slot {slot}",
            "gain": gain, "gain_share_pct": share, "now": True, "vs_unknown": False, "sources": list(sources)}


RFK = {"kind": "drop", "boss": "Agathelos the Raging", "zone": "Razorfen Kraul", "dungeon": True,
       "instance": "dungeon", "levels": [29, 38], "chance_pct": 25}
STRAT = {"kind": "drop", "boss": "Baron Rivendare", "zone": "Stratholme", "dungeon": True, "instance": "dungeon",
         "levels": [58, 60], "chance_pct": 25}
RED_QUEST = {"kind": "quest", "quest_id": 1380, "name": "Khan Hratha", "zone": "Desolace", "level": 42}
QUEST = {"kind": "quest", "quest_id": 98297, "name": "Stopping the Cycle", "zone": "Wetlands", "level": 34}
ADVICE = {"upgrades": [up(1, 16, 35, RFK), up(2, 17, 33, RED_QUEST), up(3, 16, 30, QUEST), up(4, 5, 20, STRAT),
                       up(5, 7, 10, QUEST, share=4)]}


def test_moves_only_name_what_the_level_can_reach():
    """A level-42 quest or a level-58 dungeon is no move at 32, however good
    the item; the best upgrade per slot only."""
    nm = insights.next_moves(32, ADVICE, {}, {}, {}, [], {})
    ups = [m for m in nm["moves"] if m["kind"] == "upgrade"]
    assert [m["item_id"] for m in ups] == [1, 5]  # 2: red quest, 3: same slot as 1, 4: Stratholme
    rfk = ups[0]
    assert rfk["link"] == "#up-16" and rfk["item"]["url"] == "https://www.wowhead.com/forever/item=1"
    assert rfk["source"]["zone"] == {"name": "Razorfen Kraul", "id": 491, "levels": [29, 38],
                                     "url": "https://www.wowhead.com/forever/guide/"
                                            "razerfen-kraul-dungeon-overview-location-rewards"}
    assert ups[1]["source"]["quest"]["url"] == "https://www.wowhead.com/forever/quest=98297"
    dungeons = [m for m in nm["moves"] if m["kind"] == "dungeon"]
    assert [m["title"] for m in dungeons] == ["Razorfen Kraul"] and "(levels 29-38)" in dungeons[0]["text"]
    # Stratholme's drop is a move once the character is at its level.
    assert any(m["title"] == "Stratholme" for m in insights.next_moves(58, ADVICE, {}, {}, {}, [], {})["moves"])


def test_numbers_are_written_one_way():
    levels = [{"level": lv, "seconds": 7200, "partial": False} for lv in range(20, 26)]
    tr = insights.on_track(layer(levels=levels), {"level": 26, "eta_s": 3600, "xp_per_hour": 17398})
    assert any("17,398 XP an hour" in c["text"] for c in tr["claims"])
    nm = insights.next_moves(26, None, {"rested_xp": 8554, "xp_max": 54500}, tr, {}, [], {})
    assert nm["moves"][0]["text"].startswith("8,554 rested XP banked (0.16 of level 26)")
    assert insights.ratio_text(2.03) == "2.0×" and insights.dur(11240) == "3h 07m"


def test_overview_moves_differ_and_leave_the_tiles_alone():
    nm = insights.next_moves(32, ADVICE, {"rested_xp": 10, "xp_max": 100}, {"eta_s": 60}, {}, [
        {"skill_id": 333, "name": "Enchanting", "rank": 150, "max_rank": 150}], {})
    ds = [{"zone": "Wetlands", "killer": {"npc_id": 9, "name": "Raptor"}, "level": 30, "attackers": 2}] * 3
    sc = insights.sanity_check(32, ds, {}, {}, ADVICE, [], {}, None, ask=layer(ds))
    mv = insights.overview_moves(nm, sc)
    kinds = [m["kind"] for m in mv]
    assert "rested" not in kinds and "eta" not in kinds
    assert len(set(kinds[:3])) == 3, kinds  # the three on show are three kinds of move
    # Item 1 drops in a listed dungeon: named once, in that dungeon's move.
    assert sum(1 for m in mv for i in [m.get("item") or {}, *(m.get("items") or [])] if i.get("item_id") == 1) == 1
    dz = next(m for m in mv if m["kind"] == "deaths_zone")
    assert dz["zone"]["url"] == "https://www.wowhead.com/forever/zone=11" and dz["title"] == "3 deaths in Wetlands"
    assert dz["action"] == "Pull one at a time."


def test_zogzog_overview_has_no_moves_about_creatures():
    from wowstats import redact

    ds = [{"zone": "Wetlands", "killer": {"npc_id": 9, "name": "Raptor"}, "level_gap": 4, "level": 28}] * 3
    sc = insights.sanity_check(30, ds, {}, {}, ADVICE, [], {}, None, ask=layer(ds))
    mv = insights.overview_moves(insights.next_moves(30, ADVICE, {}, {}, {}, [], {}), sc)
    assert {"deaths_creature", "deaths_level_gap"} <= {m["kind"] for m in mv}
    assert next(m for m in mv if m["kind"] == "deaths_creature")["action"] == "Come back at level 32 (you are 30)."
    # Outgrown at 32: still in the sanity check, no longer a move.
    sc32 = insights.sanity_check(32, ds, {}, {}, ADVICE, [], {}, None, ask=layer(ds))
    assert {"deaths_creature", "deaths_level_gap"} <= {c["kind"] for c in sc32["findings"]}
    assert not {"deaths_creature", "deaths_level_gap"} & {m["kind"] for m in insights.overview_moves({}, sc32)}
    z = redact.zogzog("overview", {"moves": mv})
    assert z["moves"] and {m["kind"] for m in z["moves"]} <= redact.ZOGZOG_MOVE_KINDS
    assert "Raptor" not in str(z)


def test_zone_links_find_dungeons_and_their_wings():
    from wowstats import forever

    assert forever.area_id("Razorfen Kraul") == 491  # the instance, not its entrance
    assert forever.area_id("Scarlet Monastery: Library") == 796
    assert forever.area_id("Nowhere") is None and insights.zone_ref(None) is None


def test_zogzog_moves_keep_the_trainers_and_the_death_counts():
    """/zogzog/ shows where to train and what the deaths had in common, in
    the data's order, with no creature in it."""
    from wowstats import redact

    hd = {"faction": "Alliance", "race": "Gnome", "money_copper": 10000}
    ds = [{"zone": "Wetlands", "level": 25, "level_gap": 4, "attackers": 2,
           "killer": {"npc_id": 9, "name": "Raptor", "level": 29}}] * 3
    sc = insights.sanity_check(32, ds, {}, {}, None, [{"skill_id": 186, "rank": 75, "max_rank": 75}], {}, None,
                               None, hd, ask=layer(ds))
    nm = insights.next_moves(32, None, hd, {}, {}, [], {})
    z = redact.zogzog("overview", {"moves": insights.overview_moves(nm, sc)})
    by = {m["kind"]: m for m in z["moves"]}
    assert set(by) == {"profession", "riding", "deaths_zone"}
    assert len(by["profession"]["skill"]["trainers"]) > 1 and "side" not in by["profession"]["skill"]["trainers"][0]
    zones = [t["zone"]["name"] for t in by["profession"]["skill"]["trainers"]]
    assert zones == sorted(zones)  # by zone, never by distance from the character
    assert by["deaths_zone"]["deaths"]["come_back_at"] == 29 and "Raptor" not in str(z)


def test_one_item_one_move():
    """An upgrade that a listed dungeon's move names goes into that move."""
    nm = insights.next_moves(32, ADVICE, {}, {}, {}, [], {})
    assert any(m["kind"] == "upgrade" and m["item_id"] == 1 for m in nm["moves"])  # the MCP's list keeps it
    mv = insights.overview_moves(nm, insights.sanity_check(32, [], {}, {}, ADVICE, [], {}, None, ask=layer()))
    rfk = next(m for m in mv if m["kind"] == "dungeon")
    assert [i["item_id"] for i in rfk["items"]] == [1]
    assert not any(m["kind"] == "upgrade" and m["item_id"] == 1 for m in mv)
    assert any(m["kind"] == "upgrade" and m["item_id"] == 5 for m in mv)  # a quest item: no dungeon names it


def test_a_dungeon_is_optional_and_comes_last():
    """Owner, 2026-11-28: a dungeon never leads. Its text says it is optional
    and it follows the upgrades, the professions and riding, even when it
    names the best upgrade of the list."""
    hd = {"faction": "Alliance", "race": "Gnome", "money_copper": 10000}
    skills = {}
    nm = insights.next_moves(32, ADVICE, hd, {}, {}, [{"skill_id": 333, "name": "Enchanting", "rank": 150,
                                                      "max_rank": 150}], skills)
    kinds = [m["kind"] for m in nm["moves"]]
    assert kinds.index("dungeon") > max(i for i, k in enumerate(kinds) if k in ("upgrade", "profession", "riding"))
    dz = next(m for m in nm["moves"] if m["kind"] == "dungeon")
    assert dz["text"].startswith("If you like dungeons: Razorfen Kraul")
    mv = insights.overview_moves(nm, insights.sanity_check(32, [], {}, {}, ADVICE, [], {}, None, ask=layer()))
    assert mv[-1]["kind"] == "dungeon"
    assert [m["kind"] for m in mv].count("upgrade") >= 1 and mv[0]["kind"] != "dungeon"


def test_moves_require_a_reachable_route_for_both_hands():
    hard = {**RFK, "chance_pct": 5}
    rare = {**RFK, "chance_pct": 0.3}
    auction = {"kind": "ah", "price_copper": 245500, "seen_day": "2026-11-26", "seen_days_ago": 0}
    items = [up(1, 16, 30, hard, rare), up(2, 11, 20, hard, auction),
             {**up(3, 17, 50, QUEST), "with": {"kind": "get", "sources": [rare]}},
             up(4, 12, 20, rare, {**auction, "seen_days_ago": 2})]
    moves = insights.next_moves(35, {"upgrades": items}, {}, {}, {}, [], {})["moves"]
    assert [m["item_id"] for m in moves] == [2]
    m = moves[0]
    assert "Buy Item 2" in m["text"] and "24g 55s" in m["text"] and "2026-11-26" in m["text"]
    assert m["source"]["price_copper"] == 245500 and m["source"]["seen_day"] == "2026-11-26"
    from wowstats import redact
    kept = redact.zogzog("overview", {"moves": moves})["moves"][0]
    assert kept["source"]["price_copper"] == 245500 and kept["source"]["seen_day"] == "2026-11-26"
