"""The eight demo histories, their saved builds, and their own models."""

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from wowstats import assets, character3d, classguide, gear, ingest, mounts, riding, talents, wowsims

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "fixtures"))

import generate  # noqa: E402
import roster  # noqa: E402
import roster3d  # noqa: E402

NOW = 1_795_000_000


def test_eight_played_histories_complement_the_priest():
    rows = roster.sessions(NOW)
    lines, bad = ingest.parse(generate.jsonl(rows))
    assert not bad and len(lines) == len(rows)
    assert len(set(r["source_id"] for r in rows)) == len(rows)
    assert len({c.race for c in roster.CHARACTERS} | {"Gnome"}) == 9
    assert {c.cls for c in roster.CHARACTERS} | {"PRIEST"} == set(talents.CLASS_BITS.values())
    assert sorted([46, *(c.level for c in roster.CHARACTERS)]) == [10, 16, 23, 29, 34, 39, 46, 53, 60]
    assert {c.faction for c in roster.CHARACTERS} == {"Alliance", "Horde"}
    generate.in_forever(rows)
    ref = gear.reference()
    catalog = roster.bundled_talent_catalog()
    looks = {c.name: [{"optionId": i, "choiceId": 10 + i}] for i, c in enumerate(roster.CHARACTERS, 1)}
    metrics = roster.series(rows, NOW, looks)
    for c in roster.CHARACTERS:
        mine = [r for r in rows if r["char"] == c.name]
        sheet = next(r for r in reversed(mine) if r["feed"] == "wow-gear")
        character = next(r for r in reversed(mine) if r["feed"] == "wow-character" and r.get("kind") != "played")
        assert character["level"] == str(c.level) and character["zone"] == c.zone
        assert sheet["spec"] == c.spec and any(r.get("kind") == "kill" for r in mine)
        who = gear.Who(c.cls, c.level, 1 if c.faction == "Alliance" else 2, c.race_id, {}, set(), c.zone)
        worn = {it["slot"]: it for it in json.loads(sheet["items"])}
        splits = [r for r in mine if r["feed"] == "wow-level"]
        levels = [int(r["level"]) for r in splits]
        assert len(levels) >= 5 and levels == list(range(levels[0], c.level))
        logins = [r for r in mine if r.get("login") == "true"]
        played = [r for r in mine if r.get("kind") == "played"]
        assert all(r["source"] == "altoholic" for r in played)
        logouts = [r for r in played if r["logged_out"] == "true"]
        assert len(logins) == len(logouts) >= 3
        for before, login, after in zip(logouts[:-1], logins[1:], logouts[1:], strict=True):
            session_s = generate._epoch(after["_time"]) - generate._epoch(login["_time"])
            assert abs(int(after["played_total_s"]) - int(before["played_total_s"]) - session_s) < 2
        tracked = sum(int(r["seconds"]) for r in splits) + int(played[-1]["played_level_s"])
        lifetime = int(played[-1]["played_total_s"])
        assert lifetime >= tracked - len(splits) and (lifetime - tracked < 5) == (levels[0] == 1)
        done = {int(r["quest_id"]) for r in mine if r.get("kind") == "turnin"}
        assert len(done) >= 3 and done <= set(json.loads(next(r["quest_ids"] for r in mine if r["feed"] == "wow-quests")))
        assert bool([r for r in mine if r["feed"] == "wow-dungeon"]) == (c.level >= 16)
        assert len(json.loads(sheet["professions"])) == 2
        bags = json.loads(next(r["bags"] for r in reversed(mine) if r["feed"] == "wow-bags"))
        assert bags and set(bags).isdisjoint(it["item_id"] for it in worn.values())
        if c.level == 60:
            assert set(worn) == set(range(1, 19)) - {4, 17}
            assert sum(it["quality"] == 4 for it in worn.values()) >= 4
            capped = [r for r in mine if r["_time"] > splits[-1]["_time"]]
            assert any(r.get("kind") == "kill" for r in capped)
            assert all("xp_reward" not in r for r in capped if r.get("kind") == "kill")
            assert all(int(r["xp"]) == 0 for r in capped if "xp" in r)
            assert any(r["feed"] == "wow-dungeon" and int(r["level"]) == 60 for r in capped)
            assert any(int(r.get("xp_reward") or 0) > 0 for r in mine if r.get("kind") == "kill")
        else:
            assert 0 < int(character["xp"]) < int(character["xp_max"])
            assert (1 in worn) == (c.level >= 20) and (3 in worn) == (c.level >= 20)
        for slot, item in worn.items():
            info = ref.item(item["item_id"])
            usable, why, minimum = gear.usability(ref, info, c.cls, c.level, who)
            assert usable and minimum <= c.level, (c.name, item, why)
            assert slot in gear.SLOTS_OF[info["inv"]]
        if 17 in worn:
            assert gear.can_dual_wield(ref.gear["PROFICIENCY"][c.cls], c.level)
            assert ref.item(worn[16]["item_id"])["inv"] != 17
        book_event = next(r for r in mine if r["feed"] == "wow-altoholic" and r["kind"] == "spells")
        book = dict(json.loads(book_event["spells"]))
        # The rank this character trained. Recorder cast events only cover item
        # and quest spells (Polestar_Recorder/World.lua), not class combat spells.
        trained = {"WARRIOR": 21553, "PALADIN": 10321, "HUNTER": 14283, "ROGUE": 1758,
                   "SHAMAN": 943, "MAGE": 205, "WARLOCK": 699, "DRUID": 5201}
        assert book[trained[c.cls]] == 0
        assert not any(r.get("kind") == "cast" and int(r["spell_id"]) in book for r in mine)
        talent_buttons = {"WARRIOR": (1310222, 12292, 12294), "PALADIN": (20375, 20066),
                          "HUNTER": (1293241, 19577), "SHAMAN": (17364,), "DRUID": (1238122, 1328617)}
        assert all(book.get(spell) == 0 for spell in talent_buttons.get(c.cls, ()))
        if c.cls == "HUNTER":
            assert book[883] == 0  # Call Pet
        if c.cls == "WARLOCK":
            assert book[697] == 0  # Summon Voidwalker
        assert all(lv == 0 or lv > c.level for lv in book.values())
        mount_spells = {spell for spell in (13819, 23251) if book.get(spell) == 0}
        assert mount_spells == ({"PALADIN": {13819}, "WARRIOR": {23251}}.get(c.cls, set()))
        assert riding.book_rank(book_event["spells"]) == {"PALADIN": 75, "WARRIOR": 150}.get(c.cls, 0)
        pts = {name: rank for name, _, rank, _ in roster.learned(c)}
        path = classguide.path(c.cls)
        assert pts == Counter(p["talent"] for p in path["picks"] if p["level"] <= c.level)
        assert sum(pts.values()) == c.level - 9 and not classguide.check_path(c.cls)
        observed = next(r for r in reversed(mine) if r["feed"] == "wow-talents")
        for event in [r for r in mine if r["feed"] == "wow-talents"]:
            past = wowsims.decode(catalog, c.cls, event["talents"])
            assert sum(rank["rank"] for rank in past["ranks"].values()) == int(event["level"]) - 9
        decoded = wowsims.decode(catalog, c.cls, observed["talents"])
        assert {r["name"]: r["rank"] for r in decoded["ranks"].values()} == pts
        series = [s for s in metrics if s["metric"]["character"] == c.name]
        (choice,) = [s for s in series if s["metric"]["__name__"] == "wow_character_customization"]
        (option,) = looks[c.name]
        assert choice["metric"]["option_id"] == str(option["optionId"]) and set(choice["values"]) == {option["choiceId"]}
        ranks = {s["metric"]["spell_id"]: s["values"][-1] for s in series
                 if s["metric"]["__name__"] == "wow_character_talent_rank"}
        frame = talents.frame(catalog, c.cls, ranks)
        assert not frame["unmatched"] and sum(tree["points"] for tree in frame["sections"]) == c.level - 9
        assert not any("pet" in s["metric"]["__name__"] for s in series)
        companions = [r for r in mine if r["feed"] == "wow-companion"]
        assert [r["kind"] for r in companions] == ([c.cls.lower()] if c.cls in ("HUNTER", "WARLOCK") else [])


def test_seed_has_an_icon_and_a_trait_definition_for_every_talent():
    catalog = roster.bundled_talent_catalog()
    trees = {tr["class"]: tr for tr in catalog["trees"] if tr["kind"] == "class"}
    assert set(trees) == set(talents.CLASS_BITS.values())
    for cls, tree in trees.items():
        nodes = [n for section in tree["sections"] for n in section["nodes"]]
        assert len(nodes) == len(classguide.reference()["classes"][cls]["talents"])
        for n in nodes:
            assert n["defs"]
            assert talents.ICON.fullmatch(catalog["spells"][n["spells"][0]].get("icon") or ""), (cls, n)


def test_all_demo_models_resolve_offline(tmp_path):
    roster3d.write_store(tmp_path)
    store = assets.Store(tmp_path)
    tables = character3d.load_tables(store)
    models = set()
    for c in roster.CHARACTERS:
        assert character3d.race_id(tables, c.race) == c.race_id
        ans = character3d.assemble(tables, c.race_id, c.sex - 2, character3d.class_id(c.cls), [], roster.worn(c))
        assert not ans["missing"]
        recipe = ans["recipe"]
        models.add(recipe["model"])
        need = character3d.needs(recipe)
        assert need and not assets.closure(need, store.read)[1]
        expected = {"WARRIOR": 2, "ROGUE": 2, "HUNTER": 3, "MAGE": 19, "WARLOCK": 19}.get(c.cls)
        assert (recipe.get("ranged") or {}).get("kind") == expected
    assert len(models) == 8
    for kind, display in ((1, roster.BEAR_DISPLAY), (2, 1132)):
        pet = character3d.companion(tables, (kind, display))
        assert pet["kind"] == kind and pet["model"] not in models
        assert not assets.closure({pet["model"], *pet["textures"].values()}, store.read)[1]
        models.add(pet["model"])
    for c in roster.CHARACTERS:
        book = roster.spellbook(c)
        observations = mounts.resolve(character3d.index(tables).mounts, [spell for spell, level in book if level == 0])
        if c.mount_spell is None:
            assert observations == []
            continue
        name = {"PALADIN": "Summon Warhorse", "WARRIOR": "Swift Timber Wolf"}[c.cls]
        (mount,) = character3d.mounts(tables, observations)
        assert mount["name"] == name and mount["model"] not in models
        assert not assets.closure(character3d.creature_files(mount), store.read)[1]
        models.add(mount["model"])
    assert len(models) == 12


def test_generated_geometry_cannot_supply_the_client_roster(tmp_path):
    import demo3d

    roster3d.write_store(tmp_path)
    with pytest.raises(ValueError, match="needs roster.json from demo3d.py build-roster"):
        demo3d.read_looks(tmp_path)
