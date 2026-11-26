"""What a craft takes, priced (wowstats.crafting), on the committed craft
data (data/craft-reference.json): Polestar's Tailoring guide and Forever's
client tables. Real ids: Filigreed Shadow Circlet (253981, recipe 1257416,
Pattern 253982), its cousin the Filigreed Silky Circlet's pattern (253978),
Shadoweave Pants (10002, recipe 12052), Stylish Red Shirt (3866)."""

import pytest

from wowstats import ahsource, crafting
from wowstats import gear as gearmod

TAILORING = 197
CIRCLET = {"profession": "Tailoring", "skill": 125, "spell_id": 1257416}
PANTS = {"profession": "Tailoring", "skill": 190, "spell_id": 12052}
KNOWN = {2963, 2387, 8776, 2964, 2402, 3866, 3839, 3852}


def who(**kw) -> crafting.Who:
    base = {"ranks": {TAILORING: 121}, "caps": {TAILORING: 150}, "known": {TAILORING: set(KNOWN)},
            "side": "Alliance", "held": {"bags": {2997: 6, 2321: 3}, "bank": {}}, "ids": {2997, 2321},
            "prices": {4305: {"price_copper": 875, "days_ago": 2}, 2604: {"price_copper": 50, "days_ago": 2},
                       2592: {"price_copper": 60, "days_ago": 2}, 1210: {"price_copper": 3000, "days_ago": 2},
                       249409: {"price_copper": 200, "days_ago": 2}},
            "money": 296965, "goal": {"level": 40, "left_copper": 703035}}
    return crafting.Who(**{**base, **kw})


def test_the_data_says_what_the_client_says():
    assert crafting.reference()["patterns"]["253982"] == [1257416, 125]
    r = crafting.recipe(1257416)
    assert (r["y"], r["g"], r["i"]) == (130, 130, 253981)
    assert [4305, 8] in r["r"]  # eight Bolts of Silk Cloth
    assert 253981 in crafting.reference()["bop"] and 10002 not in crafting.reference()["bop"]
    assert crafting.guide(TAILORING)["id"] == "forever/professions/tailoring"


def test_chance_follows_the_colours():
    assert crafting.chance(104, 110, 145) == 1.0
    assert crafting.chance(145, 110, 145) == 0.0
    assert crafting.chance(127, 110, 145) == pytest.approx(18 / 35)
    assert crafting.crafts_between(121, 125, 110, 145) == pytest.approx(sum(35 / (145 - s) for s in range(121, 125)))
    assert crafting.crafts_between(140, 150, 110, 145) is None  # grey before 150


def test_four_points_on_polestars_route_with_the_bolts_first():
    w = who()
    led = crafting.Ledger(w)
    rt = crafting.route(TAILORING, 121, 125, w, led)
    (step,) = rt["steps"]
    assert (step["spell"], step["from"], step["to"], step["step"]) == (3866, 121, 125, 15)
    assert step["crafts"] == 7
    # 7 shirts take 21 Bolts of Woolen Cloth: 6 in the bags, 15 made from Wool Cloth.
    assert step["also"] == [{"spell": 2964, "item": 2997, "count": 15, "name": "Bolt of Woolen Cloth"}]
    rows = {r["item_id"]: r for r in led.rows()}
    assert rows[2997]["need"] == 6 and rows[2997]["buy"] == 0
    assert rows[2592]["need"] == 45 and rows[2592]["cost_copper"] == 45 * 60
    assert rows[2321]["need"] == 7 and rows[2321]["bags"] == 3 and rows[2321]["price_from"] == "vendor"
    assert not rt["train"] and not rt["learn"]


def test_the_circlet_says_which_pattern_is_held_and_that_it_cant_be_bought():
    w = who(ids={2997, 2321, 253978},
            listings={253982: {"price_copper": 7500, "seen_day": "2026-11-26", "seen_days_ago": 0}})
    p = crafting.plan(CIRCLET, 253981, w)
    assert p["recipe"]["state"] == "missing"
    assert p["recipe"]["sibling"]["pattern_item_id"] == 253978
    assert p["pattern_copper"] == 7500
    assert p["bind_on_pickup"] is True and p["buy"] is None
    line = p["line"]
    assert "You don't have Pattern: Filigreed Shadow Circlet." in line
    assert "75s" in line
    assert "4 Tailoring points to go: ~7 Stylish Red Shirt" in line
    assert "binds when picked up" in line
    assert f"of your {ahsource.price_text(296965)}" in line
    assert p["effort"] == crafting.SOME
    assert p["action"] == "Craft ~7 Stylish Red Shirt (Tailoring 121 → 125)."


def test_a_pattern_in_the_bank_is_learnt_and_a_known_recipe_is_made():
    p = crafting.plan(CIRCLET, 253981, who(ranks={TAILORING: 125}, ids={253982},
                                           held={"bags": {}, "bank": {253982: 1}}))
    assert p["recipe"]["state"] == "in_bags" and p["recipe"]["pattern_item_id"] == 253982
    assert p["action"] == "Take Pattern: Filigreed Shadow Circlet out of the bank and learn it."
    known = crafting.plan(CIRCLET, 253981, who(ranks={TAILORING: 130}, known={TAILORING: KNOWN | {1257416}}))
    assert known["can_make"] and known["complete_cost"] and known["effort"] == crafting.EASY
    assert known["action"].startswith("Make it")


def test_a_boe_craft_can_be_bought_instead():
    w = who(listings={10002: {"price_copper": 45000, "seen_day": "2026-11-25", "seen_days_ago": 1}})
    p = crafting.plan(PANTS, 10002, w)
    assert p["bind_on_pickup"] is False and p["buy"]["copper"] == 45000
    assert "Or buy it: 4g 50s on the auction house (yesterday)." in p["line"]
    assert p["effort"] == crafting.EASY  # listed and affordable
    # Past 150 the route needs the Expert rank: its price isn't in our data, and it says so.
    assert [t["max"] for t in p["route"]["train"]] == [225]
    assert "train rank 3" in p["line"] and "incomplete estimate" in p["line"]


def test_nothing_known_degrades_to_an_estimate_and_the_action():
    w = crafting.Who(ranks={TAILORING: 121}, caps={TAILORING: None}, side="Alliance")
    p = crafting.plan(CIRCLET, 253981, w)
    assert p["recipe"]["state"] == "unknown"
    assert "no recipe list" in p["line"]
    assert "Bag counts unknown: update the shipper." in p["line"]
    assert "scan the auction house with Auctionator" in p["line"]


def test_the_gear_advice_carries_the_plan_and_its_effort():
    s = {"kind": "craft", "profession": "Tailoring", "skill": 125, "spell_id": 1257416,
         "plan": crafting.brief(crafting.plan(CIRCLET, 253981, who()))}
    assert gearmod.craft_status(s) == s["plan"]["line"]
    # No price nor vendor for the pattern in this data: where it comes from is
    # unknown, so hard (with the auction house price above, some effort).
    assert gearmod.source_effort(s) == gearmod.HARD == crafting.HARD


def test_dated_export_and_recorder_cap_survive_a_zero_and_an_unlearn():
    from wowstats import profcaps

    before = {"skill_id": TAILORING, "name": "Tailoring", "rank": 143,
              "max_rank": 225, "max_rank_at": "2026-11-25T12:00:00Z", "cap": 0}
    empty = {TAILORING: {"t": "2026-11-26T12:00:00Z", "rank": 0, "max_rank": 0}}
    sheet = {"professions": profcaps.fill([before], empty)}
    who = crafting.who_from(sheet, None, None)
    assert who.ranks[TAILORING] == 143 and who.caps[TAILORING] == 225
    assert not crafting.route(TAILORING, 143, 190, who)["train"]
    reset = {TAILORING: {"t": "2026-11-26T12:00:00Z", "rank": 1, "max_rank": 75}}
    assert profcaps.cap_of(143, reset[TAILORING], before) == (1, 75, "recorder")
    # A later return past the old rank must not restore the old Expert cap.
    readings = profcaps.newest_skills([
        {"_time": "2026-11-26T12:00:00Z", "skill_id": TAILORING, "rank": 1, "max_rank": 75},
        {"_time": "2026-11-27T12:00:00Z", "skill_id": TAILORING, "rank": 146, "max_rank": 150},
    ])
    assert profcaps.cap_of(143, readings[TAILORING], before) == (146, 150, "recorder")
    assert profcaps.cap_of(143, readings[TAILORING], sheet["professions"][0]) == (146, 150, "recorder")


@pytest.mark.parametrize("source", ["game", "recorder"])
@pytest.mark.parametrize("later_cap", [0, 150, 225])
def test_a_later_rank_two_points_behind_does_not_repeat_expert_training(source, later_cap):
    from wowstats import profcaps

    p = {"skill_id": TAILORING, "name": "Tailoring"}
    rows = [{"skill_id": TAILORING, "_time": 1, "rank": 143, "max_rank": 225}]
    if source == "game":
        p.update(rank=141, cap=later_cap, cap_at=2)
    else:
        rows.append({"skill_id": TAILORING, "_time": 2, "rank": 141, "max_rank": later_cap})
    skills = profcaps.newest_skills(rows)
    sheet = {"professions": profcaps.fill([p], skills)}
    w = crafting.who_from(sheet, None, None)
    assert (w.ranks[TAILORING], w.caps[TAILORING]) == (143, 225)
    assert not crafting.route(TAILORING, w.ranks[TAILORING], 190, w)["train"]


def test_a_new_apprentice_grows_towards_expert_again():
    from wowstats import insights, profcaps

    p = {"skill_id": TAILORING, "rank": 143, "cap": 225, "cap_at": 1}
    rows = [{"_time": 2, "skill_id": TAILORING, "rank": 5, "max_rank": 75},
            {"_time": 3, "skill_id": TAILORING, "rank": 148, "max_rank": 150}]
    skills = profcaps.newest_skills(rows)
    apprentice = profcaps.fill([p], profcaps.newest_skills(rows, until=2))[0]
    assert (apprentice["rank"], apprentice["max_rank"]) == (5, 75)
    profs = profcaps.fill([p], skills)
    w = crafting.who_from({"professions": profs}, None, None)
    assert [t["max"] for t in crafting.route(TAILORING, w.ranks[TAILORING], 190, w)["train"]] == [225]
    (move,) = insights.profession_claims(32, profs, skills)
    assert (move["skill"]["rank"], move["skill"]["max_rank"]) == (148, 150)


def test_dated_id_only_bank_preserves_counted_bags_and_marks_unknown_shortage():
    from wowstats.mcp.proftools import _inventory

    held = {"bags": [2324, 2321], "bank": [2592], "counts": {"bags": {2324: 34, 2321: 27}, "bank": None},
            "at": {"bank": "2026-11-22T12:00:00Z"}}
    who = crafting.who_from({}, held, None)
    inv = _inventory(who)
    assert inv["containers"] == {"bags": "counted", "bank": "ids_only"} and inv["bank_at"] == held["at"]["bank"]
    led = crafting.Ledger(who)
    for i, n in [(2324, 18), (2321, 9), (2592, 12)]:
        led.add(i, n)
    bleach, thread, wool = led.rows()
    assert bleach["buy"] == thread["buy"] == 0
    assert wool["bank"] is None and wool["bags"] == 0 and wool["buy"] is None and wool["buy_up_to"] == 11
    assert not crafting.mats_summary(led.rows(), who)["counts_known"]


def test_trainer_and_pattern_are_distinct_learning_paths():
    spell, item, pat = 1257429, 254007, 254008  # Black Slippers
    s = {"profession": "Tailoring", "spell_id": spell, "skill": 140}
    for rank, held, listings, kind, need in (
        (143, {}, {}, "trainer", 150),
        (143, {pat: 1}, {}, "held", 140),
        (143, {}, {pat: {"price_copper": 500, "seen_day": "2026-11-26"}}, "ah", 140),
        (143, {}, {pat: {"price_copper": 5000000, "seen_day": "2026-11-26"}}, "trainer", 150),
        (150, {}, {}, "trainer", 150),
    ):
        w = who(ranks={TAILORING: rank}, held={"bags": held, "bank": {}}, listings=listings)
        p = crafting.plan(s, item, w)
        assert p["skill"]["need"] == need and p["recipe"]["selected"]["kind"] == kind
        assert p["recipe"]["trainer_skill"] == 150 and p["recipe"]["pattern_skill"] == 140
        assert not p["can_make"]
        if rank >= need:
            assert p["route"] is None and p["action"].startswith(("Learn", "Buy"))
        else:
            assert p["route"]["to"] == 150


def test_only_a_current_finished_listing_can_lower_effort():
    pattern = 249487
    spell = crafting.reference()["patterns"][str(pattern)][0]
    item = crafting.recipe(spell)["i"]
    src = {"profession": "Enchanting", "spell_id": spell, "skill": 185}
    latest = {i: {"price_copper": 200, "seen_day": "2026-11-24", "days_ago": 2} for i in (pattern, item)}
    fake_pat_sale = {pattern: {"price_copper": 99, "seen_day": "2026-11-26", "seen_days_ago": 0}}
    who = crafting.Who(ranks={333: 185}, known={333: set()}, prices=latest, listings=fake_pat_sale, money=2000)
    p = crafting.plan(src, item, who)
    assert p["recipe"]["pattern_bop"] and p["bind_on_pickup"] is False
    assert p["effort"] == crafting.HARD and p["buy"] is None and p["estimate"]["copper"] == 200
    assert "Boulderfist Magus" in p["action"] and "auction" not in p["action"]
    assert "auction" not in crafting._recipe_words(p["recipe"])
    # Yesterday's historical price can also be absent from a newer full scan.
    latest[item].update(seen_day="2026-11-25", days_ago=1)
    assert crafting.plan(src, item, who)["buy"] is None
    who.listings[item] = {"price_copper": 500, "seen_day": "2026-11-26", "seen_days_ago": 0}
    p = crafting.plan(src, item, who)
    assert p["effort"] == crafting.EASY and p["craft_effort"] == crafting.HARD
    assert p["buy"]["copper"] == 500
    assert not crafting.brief(p)["cost_complete"]


def test_client_recipe_alone_cannot_make_a_gear_upgrade_available():
    # Admiral's Hat is made by 12081; the client names its pattern, not how to get it.
    item, spell, pattern = "10030", 12081, 10318
    assert item not in crafting.gear_sources()
    missing = crafting.Who(known={197: set()}, prices={pattern: {"price_copper": 100}})
    assert item not in crafting.gear_sources(missing)
    for evidence in (crafting.Who(known={197: {spell}}), crafting.Who(ids={pattern}),
                     crafting.Who(listings={pattern: {"price_copper": 100}})):
        assert crafting.gear_sources(evidence)[item][0]["spell"] == spell
    assert item not in crafting.gear_sources(crafting.Who(known={333: {spell}}))
