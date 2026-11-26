"""Riding known from every read that sees it (wowstats.riding), the same
blind spot as the profession caps (#215): Polestar Recorder writes a skill
record only when the skill list changes while it records (its login
baseline is silent), so a riding training it didn't see read "not
learned", and riding_claims asserted it. DataStore_Crafts has no riding;
Altoholic's spellbook (DataStore_Spells) does: the training is a spell,
which DataStore itself picks out (`ridingSkill`). Shapes as the shipper
reads and sends them; names are made up."""

from wowstats import ahsource, insights, riding

GNOME = {"faction": "Alliance", "race": "Gnome", "money_copper": 1_200_000}
# The shipper's wow-altoholic kind=spells event as the store hands it back:
# [[spell id, level]] (level 0 known) as the JSON string of a nested value.
BOOK_RIDING = [{"_time": "2026-01-05T18:00:00Z", "spells": "[[6603, 0], [81, 0], [33388, 0], [585, 0]]"}]
BOOK_NONE = [{"_time": "2026-01-05T18:00:00Z", "spells": "[[6603, 0], [81, 0], [585, 0], [2050, 0]]"}]


def test_the_shipper_sends_the_riding_spell_datastore_saw(shipper, tmp_path):
    """DataStore_Spells keeps the spellbook per tab as spell id << 8 (+ the
    level a future spell opens at); a known riding training sits in General
    and DataStore also notes it as `ridingSkill`. The shipper's spellbook
    event carries it as [33388, 0]."""
    sv = tmp_path / "acct" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "DataStore.lua").write_text(
        'DataStore_CharacterIDs = {\n["Set"] = {\n["Default.Test Realm.Rider Fixture"] = 1,\n},\n}\n')
    general = ", ".join(str(s << 8) for s in (6603, 81, 33388))
    (sv / "DataStore_Spells.lua").write_text(
        'DataStore_Spells_Characters = {\n{\n["lastUpdate"] = 1767636000,\n["englishClass"] = "PRIEST",\n'
        f'["ridingSkill"] = 33388,\n["Spells"] = {{\n["General"] = {{\n{general},\n}},\n'
        f'["Holy"] = {{\n{585 << 8},\n{(2055 << 8) + 44},\n}},\n}},\n}},\n}}\n')
    got = shipper.altoholic_spells(tmp_path / "acct")["Rider-Fixture"]
    assert [33388, 0] in got["spells"] and [2055, 44] in got["spells"]
    assert riding.book_rank(got["spells"]) == 75


def test_the_rank_is_the_highest_any_read_saw():
    # The spellbook alone: the game's.
    assert riding.state(None, BOOK_RIDING) | {"t": None} == {
        "rank": 75, "max_rank": 75, "t": None, "from": "game", "book_t": "2026-01-05T18:00:00Z"}
    # A spellbook without riding, nothing else: no rank, the read's day kept.
    st = riding.state(None, BOOK_NONE)
    assert (st["rank"], st["from"], st["book_t"]) == (0, None, "2026-01-05T18:00:00Z")
    # The Recorder saw Journeyman after the spellbook's Apprentice: ranks only go up.
    rec = {"rank": 150, "max_rank": 150, "t": "2026-01-06T10:00:00Z"}
    assert (riding.state(rec, BOOK_RIDING)["rank"], riding.state(rec, BOOK_RIDING)["from"]) == (150, "recorder")
    # A learn record of a riding spell counts too.
    learn = [{"_time": "2026-01-04T12:00:00Z", "spell_id": "33388"}]
    assert (riding.state(None, None, learn)["rank"], riding.state(None, None, learn)["from"]) == (75, "recorder")
    # Nothing at all: nothing to merge.
    assert riding.merge({197: {"rank": 1}}, None, None) == {197: {"rank": 1}}


def test_riding_the_recorder_missed_gets_no_train_riding():
    """Apprentice Riding trained while the Recorder wasn't watching: no
    skill record, but the spellbook lists 33388 known. No move."""
    skills = riding.merge({}, BOOK_RIDING, [])
    assert insights.riding_claims(41, GNOME, skills) == []
    # The mount's share in the gear advice moves on to Journeyman as well.
    assert ahsource.mount_left(0, [], BOOK_RIDING, [])["name"] == "riding.journeyman"
    assert ahsource.mount_left(0, [])["name"] == "riding.apprentice"


def test_riding_not_seen_past_its_level_hedges():
    """Past level 40 with no read seeing riding: "if not trained yet", with
    the day the spellbook was read, never "you haven't"."""
    c = insights.riding_claims(41, GNOME, riding.merge({}, BOOK_NONE, []))
    assert c[0]["title"] == "Riding at level 40, if not trained yet" and c[0]["skill"]["may_be_stale"]
    assert c[0]["detail"] == ("No riding in your spellbook as Altoholic last read it (5 Jan) nor in Polestar "
                              "Recorder's records: if you haven't trained Apprentice Riding yet, it's trained from "
                              "level 40.")
    assert c[0]["text"].startswith(c[0]["detail"] + " Binjy Featherwhistle, Dun Morogh (49.2, 48.1); 100 gold ")
    assert c[0]["text"].endswith(c[0]["action"])
    # No spellbook at all: the Recorder's silence only.
    c = insights.riding_claims(41, GNOME, {})
    assert c[0]["detail"].startswith("No riding in Polestar Recorder's records: if you haven't trained")
    # Under the tier's level it can't be trained yet: said flat out.
    c = insights.riding_claims(35, GNOME, riding.merge({}, BOOK_NONE, []))
    assert c[0]["title"] == "Riding at level 40" and c[0]["detail"] is None and c[0]["action"] is None
    assert c[0]["text"].startswith("Riding at level 40 (you are 35): ")


def test_the_mount_goal_hedges_a_tier_no_game_read_saw():
    from wowstats.plan import estimate as E

    tiers = [{"name": "riding.apprentice", "level": 40, "skill": 75, "training_copper": 1_000_000,
              "comes_with_mount": True},
             {"name": "riding.journeyman", "level": 60, "skill": 150, "training_copper": 10_000_000,
              "comes_with_mount": True}]
    inp = {"mount": {"tiers": tiers, "riding_skill": 0, "riding_from": None}, "gold_copper": 0, "level": 41,
           "xp": 0, "route_levels": {}, "gold_rate": None}
    p = {"level_factor": {"factor": 1.0}, "overall": {"factor": 1.0}}
    m = E.mounts(inp, p)
    assert [t["may_be_trained"] for t in m["tiers"]] == [True, False]
    assert any(u.startswith("whether riding.apprentice is trained") for u in m["unknown"])
    # The spellbook saw Apprentice: Journeyman's absence is the game's word, and 41 < 60 anyway.
    seen = E.mounts({**inp, "mount": {**inp["mount"], "riding_skill": 75, "riding_from": "game"}}, p)
    assert [t["bought"] for t in seen["tiers"]] == [True, False]
    assert not any(t["may_be_trained"] for t in seen["tiers"])
    assert not any(u.startswith("whether") for u in seen["unknown"])


def test_class_mounts_share_the_quest_and_its_cost_across_advice():
    from wowstats.analysis import kb
    from wowstats.plan import estimate as E
    from wowstats.plan import facts

    who = {"class": "PALADIN", "faction": "Horde", "race": "Undead"}
    inp = {"level": 39, "xp": 0, "who": who, "open_quests": [{"quest_id": 94971}],
           "gold_copper": 0, "gold_rate": {"copper_per_hour": 10000}, "mount": facts.mount_facts(kb.build({}))}
    tiers = E.mounts(inp, E.pace(inp))["tiers"]
    q = tiers[0]["quest"]
    assert q["quest_id"] == 94971 and q["state"] == "open" and "optional" in q["action"]
    assert tiers[0]["trainer_alternative_copper"] == 1_000_000
    assert tiers[0]["left_copper"] == tiers[0]["eta_played_s"] == tiers[1]["prior_cost_copper"] == 0
    assert tiers[1]["need_copper"] is None and tiers[1]["eta_played_s"] is None
    overview = insights.riding_claims(39, who, {})[0]
    assert "Danitha" in overview["text"] and overview["skill"]["cost_copper"] == 0
    assert ahsource.mount_left(0, [], who=who)["need_copper"] == 0
    assert ahsource.mount_left(0, [], who=who, done={94971})["name"] == "riding.journeyman"
    assert riding.class_route(75, {**who, "race": "Tauren"}) is None
    for side, npc in (("Horde", "Zevrost"), ("Alliance", "Ursula")):
        lock = {"class": "WARLOCK", "faction": side}
        assert "Felsteed" in riding.class_route(75, lock, done={4490})["action"]
        epic = ahsource.mount_left(0, [], BOOK_RIDING, who=lock, done={7631})
        assert epic["need_copper"] == 0 and npc in epic["quest"]["action"]
    done = E.mounts({**inp, "quests_done": [94971, 97185]}, E.pace(inp))
    assert done["current"]["quest"]["state"] == "done" and "Danitha" in done["current"]["quest"]["action"]
