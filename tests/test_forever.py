"""Forever's quest and item names and its instances (wowstats.forever,
data/forever-reference.json), and where the page uses them."""

from wowstats import forever, gear


def test_quests_forever_only_ids_included():
    # 98245 and 78146 are Forever's own quests, 631 the original game's.
    assert forever.quest(631) == {"name": "The Thandol Span", "level": 31, "min_level": 26, "zone": "Wetlands",
                                  "part": [1, 3]}
    assert forever.quest(98245)["name"] == "Razormaw Needling"
    assert forever.quest(78146)["name"] == "Goaz Scrolls" and forever.quest(78146)["zone"] is None
    assert forever.quest(0) is None and forever.quest("x") is None and forever.quest(None) is None


def test_name_quests_keeps_the_shipped_name_then_forever_db_then_the_recorder(capsys):
    rows = [{"quest_id": 631, "name": "Shipped"}, {"quest_id": 98246, "name": None},
            {"quest_id": 9999999, "name": None}, {"quest_id": 9999998, "name": None}]
    forever.name_quests(rows, {9999999: "From a text record"})
    assert [r["name"] for r in rows] == ["Shipped", "Trying Times", "From a text record", None]
    assert rows[1]["level"] == 29 and rows[1]["zone"] == "Wetlands"
    assert "9999998" in capsys.readouterr().err, "a quest no source names is logged"


def test_item_names_for_items_forevers_itemsparse_leaves_out():
    assert forever.item_name(15488) == "Bloodspattered Surcoat"
    assert forever.item_name(273034) == "Ephemeral Grips"


def test_city_of_dalaran_is_a_dungeon_with_its_group_finder_range():
    """Forever's new dungeon (owner, 2026-11-18): its drops are valid
    leveling gear, labelled as a dungeon's, levels from GroupFinderActivity."""
    assert forever.area(16544) == {"name": "City of Dalaran", "instance": "dungeon", "map": 2959, "levels": [28, 35],
                                   "floor": 31.6}
    assert forever.area(209)["instance"] == "dungeon"
    assert "instance" not in forever.area(11), "Wetlands is no instance"


def test_polestar_floors_by_its_codex_rule():
    """Polestar's floor (tools/forever_reference/export.py floor_of, its
    Codex rule): the range's bottom or players' 25th percentile entry level,
    whichever is higher. Scarlet Monastery opens at 30 but players start it
    at 35.1; Razorfen Kraul at 31.1."""
    assert forever.area(796)["levels"] == [30, 38] and forever.area(796)["floor"] == 35.1
    assert forever.area(491)["floor"] == 31.1
    for a in (forever.data().get("areas") or {}).values():
        if a.get("instance") == "dungeon" and a.get("levels"):
            assert a["levels"][0] <= a["floor"] <= a["levels"][1] + 10, a


def test_floor_of_follows_polestars_rule():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tools" / "forever_reference" / "export.py"
    spec = importlib.util.spec_from_file_location("forever_reference_export", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    floor_of = mod.floor_of
    assert floor_of([30, 38], {"people": 76, "p25": 35.14}, None) == 35.1
    assert floor_of([29, 38], {"people": 102, "p25": 31.05}, None) == 31.1  # half away from zero, as Rust
    assert floor_of([30, 38], {"people": 4, "p25": 35.14}, None) == 30.0  # too few recorded
    assert floor_of([30, 38], {"people": 76, "p25": 22.0}, None) == 30.0  # never under the bottom
    assert floor_of([40, 47], {"people": 9, "p25": 43.5}, {"floor": 43}) == 43.0  # the named exception


def test_drop_sources_say_dungeon_and_levels():
    ref = gear.reference()
    who = gear.Who(cls="PRIEST", level=32, side=1, race_id=7, professions={}, done=set(), zone=None)
    src = next(s for s in ref.data["sources"]["273034"] if s["kind"] == "drop")
    out = gear._source(ref, src, who, ref.item(273034))
    assert out["boss"] == "Arcane Anomaly" and out["zone"] == "City of Dalaran"
    assert out["instance"] == "dungeon" and out["levels"] == [28, 35] and out["dungeon"] is True


def test_chain_parts_tell_quests_with_one_title_apart():
    """The journal listed "The Missing Diplomat" four times in a row."""
    assert forever.quest(631)["part"] == [1, 3] and forever.quest(633)["part"] == [3, 3]
    assert forever.quest(1274)["part"] == [1, 17] and forever.quest(1242)["part"] == [3, 17]
    assert forever.quest(98245)["part"] is None
    rows = forever.name_quests([{"quest_id": 632, "name": None}, {"quest_id": 632, "name": "Renamed by hand"}])
    assert rows[0]["part"] == [2, 3] and "part" not in rows[1], "a part only for forever-db's own title"


def test_a_dungeons_quests_found_under_either_of_its_ids():
    """Razorfen Kraul is 491 as an instance and 1717 as its quests' zone
    (Gnomeregan 721/133, Uldaman 1337/1517, Zul'Farrak 1176/978): matching
    the instance ID alone found none of their quests (2026-11-19)."""
    assert forever.dungeon_area(1717) == "491" and forever.dungeon_area(491) == "491"
    assert forever.dungeon_area(133) == "721" and forever.dungeon_area(1517) == "1337"
    assert forever.dungeon_area(11) is None  # Wetlands, no dungeon
    assert forever.quest_dungeon(1101) == 491  # The Crone of the Kraul
    assert forever.quest_dungeon(2841) == 721  # Rig Wars
