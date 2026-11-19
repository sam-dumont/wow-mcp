"""What the gear advice and the planner may claim, checked on made-up
characters shaped like the one whose plan went wrong (owner, 2026-11-19): a
level 33 caster wearing a two-handed staff, an off hand in the bags and no
one-hander, Tailoring 119 without the patterns, Enchanting 150.

1. An off hand is never "equip it now" against a worn two-hander: a setup is
   two hands, and the row leads with the part still to get.
2. A craft is "make it now" only with the recipe known and the skill; a
   pattern in the bags is "learn it first", an unknown one a find.
3. Each weapon setup's best comes on its own (as a levelling BiS list ranks
   them), with where the stat weights come from.
4. The logout follows the route, the mount price is the training's, and a
   profession trainer is no class training."""

from __future__ import annotations

import pytest

from tests.test_gear import sheet as priest_sheet
from wowstats import gear, insights, trainers
from wowstats.plan import estimate as E
from wowstats.plan import facts

STAFF = 1317  # Hardened Root Staff, the fixture's two-hander (tests/test_gear.py WORN)
ORB = 273052  # Ponderous Orb: a held-in-off-hand piece (inventory type 23)
SHADOW_CIRCLET = 253955  # Tailoring 100 (spell 1257403)
SHADOW_CIRCLET_SPELL = 1257403
SHADOW_CIRCLET_PATTERN = 253956  # "Pattern: Shadow Circlet"
MYSTIC_ORB = 249394  # Orb of Mystic Insight: Enchanting 140 (spell 1248745)
MYSTIC_ORB_SPELL = 1248745


def caster(**kw) -> dict:
    s = priest_sheet(professions=[{"name": "Tailoring", "rank": 119}, {"name": "Enchanting", "rank": 150}])
    return {**s, **kw}


def advise(sheet: dict, bags=(), level=33) -> dict:
    return gear.advise(gear.reference(), sheet, level=level, every=True,
                       held={"bags": list(bags), "bank": []})


def craft_src(a: dict, item_id: int) -> tuple[dict, dict]:
    u = next(u for u in a["upgrades_all"] if u["item_id"] == item_id)
    return u, next(s for s in u["sources"] if s["kind"] == "craft")


# ---------------------------------------------------------------- 1. weapon setups


def test_an_off_hand_in_the_bags_is_never_equipped_alone_against_a_two_hander():
    a = advise(caster(), bags=[ORB])
    assert ref_inv(next(s for s in a["slots"] if s["slot"] == 16)["current"]["item_id"]) == 17
    for u in a["upgrades_all"]:
        if u["item_id"] == ORB:
            # The orb alone against the staff is no upgrade; with a one-hander
            # the row is the one-hander's, the orb its partner.
            pytest.fail(f"the off hand leads a row: {u['name']} with {u.get('with')}")
    rows = [u for u in a["upgrades_all"] if (u.get("with") or {}).get("item_id") == ORB]
    assert rows, "the orb never shows as the partner of a one-hander"
    for u in rows:
        assert ref_inv(u["item_id"]) in (13, 21) and u["slot"] == 16
        assert u["with"]["kind"] == "get" and [s["kind"] for s in u["with"]["sources"]] == ["bags"]
        assert all(s["kind"] not in gear.HELD for s in u["sources"])  # the one-hander is the errand
        assert not gear.is_held(u)


def test_both_halves_held_is_an_equip_and_says_so():
    """With the one-hander in the bags too, the setup is held: an equip
    away, whichever part leads."""
    a = advise(caster(), bags=[ORB])
    shank = next(u for u in a["upgrades_all"] if (u.get("with") or {}).get("item_id") == ORB)["item_id"]
    b = advise(caster(), bags=[ORB, shank])
    pair = next(u for u in b["upgrades_all"]
                if {u["item_id"], (u.get("with") or {}).get("item_id")} == {ORB, shank})
    assert gear.is_held(pair)


def test_the_plan_and_the_coach_name_the_other_hand():
    a = advise(caster(), bags=[ORB])
    ups = [{k: u.get(k) for k in ("item_id", "name", "slot_name", "gain", "level", "now", "sources")}
           | ({"with": facts._partner(u["with"])} if u.get("with") else {}) for u in a["upgrades_all"]]
    inp = {"upgrades": ups, "level": 33}
    for g in E.gear_now(inp):
        if g["slot"] in ("Main hand", "Off hand"):
            assert g["worn_with"], g
    rows = [o for s in E.gear_options(inp, {"level_factor": {}}, [], {}) for o in s["options"]]
    assert not any(o["item_id"] == ORB for o in rows)  # never "in your bags: equip it"
    assert all(o.get("worn_with") for o in rows if o["kind"] != "setup" and gear.reference().item(
        o["item_id"])["inv"] in (13, 21, 14, 22, 23))
    # The coach's "what to do now" says the pair, never "equip the orb".
    reach = insights.in_reach({"upgrades": a["upgrades_all"]}, 33)
    texts = [insights._pick_text(u, s) for u, s in reach]
    assert not any(t.startswith("Equip Ponderous Orb") for t in texts)
    assert any("worn with Ponderous Orb" in t for t in texts)
    # The overview's move carries the other hand, and where it is.
    u = next(u for u, _ in reach if (u.get("with") or {}).get("item_id") == ORB)
    assert insights.item_ref(u)["with"] == {"item_id": ORB, "name": "Ponderous Orb", "slot_name": "Off hand",
                                            "kind": "bags", "url": insights.wowhead_url("item", ORB)}


def ref_inv(item_id: int) -> int:
    return gear.reference().item(item_id)["inv"]


# ---------------------------------------------------------------- 2. crafts


def test_no_recipe_list_never_says_make_it_now():
    u, s = craft_src(advise(caster()), SHADOW_CIRCLET)
    assert s["skill_ok"] and s["known"] is None and not s["can_make"]
    assert "check you know the recipe" in gear.craft_status(s)
    assert "make it once" not in gear.craft_status(s)


def test_a_pattern_in_the_bags_is_learnt_first():
    sh = caster(recipes=[{"profession": "Tailoring", "known": [], "seen": 40}])
    u, s = craft_src(advise(sh, bags=[SHADOW_CIRCLET_PATTERN]), SHADOW_CIRCLET)
    assert s["known"] is False and s["pattern_item_id"] == SHADOW_CIRCLET_PATTERN and not s["can_make"]
    assert {"kind": "learn", "profession": "Tailoring", "need": SHADOW_CIRCLET_PATTERN, "have": None} in u["gates"]
    assert gear.craft_status(s) == "first learn Pattern: Shadow Circlet, in your bags"


def test_an_unknown_recipe_is_a_find_and_the_skill_comes_first():
    sh = caster(recipes=[{"profession": "Enchanting", "known": [], "seen": 30}])
    u, s = craft_src(advise(sh), MYSTIC_ORB)
    assert s["known"] is False and s["pattern_item_id"] is None and not s["can_make"]
    assert any(g["kind"] == "recipe" for g in u["gates"])
    low = caster(professions=[{"name": "Tailoring", "rank": 90}],
                 recipes=[{"profession": "Tailoring", "known": [], "seen": 40}])
    _, s = craft_src(advise(low), SHADOW_CIRCLET)
    assert gear.craft_status(s).startswith("first reach Tailoring 100 (you have 90), then find and learn")


def test_known_recipe_and_skill_is_make_it_with_materials_unchecked():
    sh = caster(recipes=[{"profession": "Tailoring", "known": [SHADOW_CIRCLET_SPELL], "seen": 40}])
    u, s = craft_src(advise(sh), SHADOW_CIRCLET)
    assert s["can_make"] and not u["gates"]
    assert "materials (not checked)" in gear.craft_status(s)


def test_the_town_stop_crafts_only_what_can_be_made():
    def errands(sh):
        a = advise(sh)
        ups = [{k: u.get(k) for k in ("name", "gain", "now", "sources")} for u in a["upgrades_all"]]
        inp = {"upgrades": ups, "level": 33, "trainer": {}, "professions": []}
        ts = E.town_stop(inp, {}, [{"id": "b1", "level_to": 33, "town": True, "title": "town"}], {})
        return [e["what"] for e in (ts or {}).get("errands") or []]

    assert not any(w.startswith("craft") for w in errands(caster()))
    assert not any(w.startswith("craft") for w in errands(caster(recipes=[
        {"profession": "Tailoring", "known": [], "seen": 40}, {"profession": "Enchanting", "known": [], "seen": 30}])))
    known = errands(caster(recipes=[{"profession": "Tailoring", "known": [SHADOW_CIRCLET_SPELL], "seen": 40}]))
    assert "craft Shadow Circlet" in known


def test_the_shipper_reads_known_recipes_from_datastore(shipper, tmp_path):
    """DataStore_Crafts saves each recipe as one number: bit 2 learned,
    bits 7 and up the spell; DataStore.lua maps the character key."""
    acct = tmp_path / "acct"
    sv = acct / "SavedVariables"
    sv.mkdir(parents=True)
    learned = (SHADOW_CIRCLET_SPELL << 7) | 4 | 1
    unlearned = (1257402 << 7) | 2
    (sv / "DataStore.lua").write_text(
        'DataStore_CharacterIDs = {\n["Set"] = {\n["Default.Test Realm.Caster Fixture"] = 1,\n},\n}\n')
    (sv / "DataStore_Crafts.lua").write_text(
        "DataStore_Crafts_Characters = {\n{\n[\"Professions\"] = {\n"
        f'{{\n["Name"] = "Tailoring",\n["Crafts"] = {{\n[2581] = {{\n{learned},\n{unlearned},\n}},\n}},\n}},\n'
        '{\n["Name"] = "Cooking",\n},\n},\n},\n}\n')
    assert shipper.recipes_of(acct, "Caster-Fixture") == [
        {"profession": "Tailoring", "known": [SHADOW_CIRCLET_SPELL], "seen": 2}]
    assert shipper.recipes_of(acct, "Someone-Else") == []
    s = gear.sheet({"recipes": '[{"profession": "Tailoring", "known": [1257403], "seen": 2}]'})
    assert gear.recipe_lists(s["recipes"]) == {"tailoring": {SHADOW_CIRCLET_SPELL}}


# ---------------------------------------------------------------- 3. the BiS cross-check


def test_each_weapon_setup_comes_on_its_own():
    a = advise(caster(), bags=[ORB])
    kinds = {x["setup"]: x for x in a["weapon_setups"]}
    two, pair = kinds["two-hander"], kinds["main hand + off hand"]
    assert two["worn"] and not pair["worn"]
    assert len(pair["parts"]) == 2 and {p["have"] for p in pair["parts"]} <= {"get", "bags", "bank", "worn"}
    assert two["parts"][0]["score"] >= next(s for s in a["slots"] if s["slot"] == 16)["current"]["score"]
    # Whichever setup the upgrades put first, its score is the gain over both hands worn.
    best = max(two["score"], pair["score"])
    worn = sum((s["current"] or {}).get("score") or 0 for s in a["slots"] if s["slot"] in (16, 17))
    weapon_ups = [u for u in a["upgrades_all"] if u["slot"] in (16, 17) and u["now"]]
    assert max(u["gain"] for u in weapon_ups) == pytest.approx(best - worn, abs=0.05)
    assert a["band"]["note"]  # where the weights come from travels with the advice


# ---------------------------------------------------------------- 4. the rest of the plan


def _route(*titles_kinds):
    return [{"guide": "forever/alliance/30-40/01-hillsbrad-foothills", "step": n, "title": t, "kind": k,
             "planned_s": 60} for n, (t, k) in enumerate(titles_kinds, 1)]


def test_logout_follows_the_route_not_a_far_hearth():
    route = _route(("Kill boars", "questing"), ("Southshore: hand-ins", "town"), ("Kill bears", "questing"))
    block = [{"id": "b1", "kind": "questing", "title": "Hillsbrad: step 1", "guide": route[0]["guide"],
              "step_from": 1, "step_to": 1}]
    base = {"level": 33, "xp_table": {"33": 58600}, "route_ahead": route, "rules": {}}
    off = E.logout({**base, "bind": "Theramore Isle"}, block)
    assert not off["bind_on_route"] and "Southshore" in off["where"] and "Theramore Isle" in off["how"]
    on = E.logout({**base, "bind": "Southshore"}, block)
    assert on["bind_on_route"] and on["where"] == "the inn at Southshore"


def test_the_riding_price_is_the_trainings_with_the_mount_in_it():
    inp = {"level": 33, "xp": 0, "xp_table": {"33": 58600}, "gold_copper": 0, "route_levels": {},
           "mount": {"tiers": [{"name": "riding.apprentice", "level": 40, "skill": 75,
                                "training_copper": 1000000, "comes_with_mount": True,
                                "vendor_mount_copper": 200000}]}}
    t = E.mounts(inp, {"level_factor": {"factor": 1.0}, "overall": {"factor": 1.0}})["tiers"][0]
    assert t["need_copper"] == 1000000 and t["mount_copper"] is None and t["extra_mount_copper"] == 200000


def test_a_profession_trainer_is_no_class_training():
    others = trainers.not_class_trainers()
    assert 1300 in others and 11068 in others  # Stormwind's Tailoring and Enchanting trainers
    rows = [{"feed": "wow-gear", "_time": "2026-11-18T12:00:00Z", "class_train_level": "32"},
            {"feed": "wow-gear", "_time": "2026-11-17T12:00:00Z", "class_train_level": "30"}]
    assert facts.class_train_level(rows) == 32
    assert facts.class_train_level([{"feed": "wow-gear", "_time": "2026-11-18T12:00:00Z"}]) is None
