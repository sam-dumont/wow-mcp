"""The eight short demo histories, their saved builds, and their own models."""

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from wowstats import assets, character3d, classguide, gear, ingest, talents, wowsims

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "fixtures"))

import generate  # noqa: E402
import roster  # noqa: E402
import roster3d  # noqa: E402

NOW = 1_795_000_000


def test_eight_short_histories_complement_the_priest():
    rows = roster.sessions(NOW)
    lines, bad = ingest.parse(generate.jsonl(rows))
    assert not bad and len(lines) == len(rows) < 1000
    assert len(set(r["source_id"] for r in rows)) == len(rows)
    assert len({c.race for c in roster.CHARACTERS} | {"Gnome"}) == 9
    assert {c.cls for c in roster.CHARACTERS} | {"PRIEST"} == set(talents.CLASS_BITS.values())
    assert {c.faction for c in roster.CHARACTERS} == {"Alliance", "Horde"}
    generate.in_forever(rows)
    ref = gear.reference()
    catalog = roster.bundled_talent_catalog()
    looks = {c.name: [{"optionId": i, "choiceId": 10 + i}] for i, c in enumerate(roster.CHARACTERS, 1)}
    metrics = roster.series(rows, NOW, looks)
    for c in roster.CHARACTERS:
        mine = [r for r in rows if r["char"] == c.name]
        sheet = next(r for r in mine if r["feed"] == "wow-gear")
        assert next(r for r in mine if r["feed"] == "wow-character")["level"] == str(c.level)
        assert sheet["spec"] == c.spec and any(r.get("kind") == "kill" for r in mine)
        who = gear.Who(c.cls, c.level, 1 if c.faction == "Alliance" else 2, c.race_id, {}, set(), c.zone)
        worn = {it["slot"]: it for it in json.loads(sheet["items"])}
        assert len(worn) >= 12
        for slot, item in worn.items():
            info = ref.item(item["item_id"])
            usable, why, minimum = gear.usability(ref, info, c.cls, c.level, who)
            assert usable and minimum <= c.level, (c.name, item, why)
            assert slot in gear.SLOTS_OF[info["inv"]]
        if 17 in worn:
            assert gear.can_dual_wield(ref.gear["PROFICIENCY"][c.cls], c.level)
            assert ref.item(worn[16]["item_id"])["inv"] != 17
        pts = {name: rank for name, _, rank, _ in roster.learned(c)}
        path = classguide.path(c.cls)
        assert pts == Counter(p["talent"] for p in path["picks"] if p["level"] <= c.level)
        assert sum(pts.values()) == c.level - 9 and not classguide.check_path(c.cls)
        observed = next(r for r in mine if r["feed"] == "wow-talents")
        decoded = wowsims.decode(catalog, c.cls, observed["talents"])
        assert {r["name"]: r["rank"] for r in decoded["ranks"].values()} == pts
        series = [s for s in metrics if s["metric"]["character"] == c.name]
        (choice,) = [s for s in series if s["metric"]["__name__"] == "wow_character_customization"]
        (option,) = looks[c.name]
        assert choice["metric"]["option_id"] == str(option["optionId"]) and choice["values"] == [option["choiceId"]] * 2
        ranks = {s["metric"]["spell_id"]: s["values"][-1] for s in series
                 if s["metric"]["__name__"] == "wow_character_talent_rank"}
        frame = talents.frame(catalog, c.cls, ranks)
        assert not frame["unmatched"] and sum(tree["points"] for tree in frame["sections"]) == c.level - 9
        assert not any("pet" in s["metric"]["__name__"] for s in series)
        companions = [r for r in mine if r["feed"] == "wow-companion"]
        assert [r["kind"] for r in companions] == ([c.cls.lower()] if c.cls in ("HUNTER", "WARLOCK") else [])


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
    assert len(models) == 10


def test_generated_geometry_cannot_supply_the_client_roster(tmp_path):
    import demo3d

    roster3d.write_store(tmp_path)
    with pytest.raises(ValueError, match="needs roster.json from demo3d.py build-roster"):
        demo3d.read_looks(tmp_path)
