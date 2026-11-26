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


def test_shared_quest_lookup_keeps_the_library_guides_faction_requirements():
    assert forever.quest_gates(78150) == (0, 0, 0)
    for quest_id in (79536, 82208):
        assert forever.quest_gates(quest_id) == (1, 0, 0)


def test_spyglass_has_a_recorded_dungeon_source_without_a_measured_percentage():
    ref = gear.reference()
    who = gear.Who(cls="PRIEST", level=24, side=1, race_id=7, professions={}, done=set(), zone=None)
    sources = [gear._source(ref, s, who, ref.item(273298)) for s in ref.data["sources"]["273298"]]
    cookie = next(s for s in sources if s and s["npc_id"] == 645)
    assert cookie["dungeon"] and cookie["zone"] == "The Deadmines"
    assert cookie["basis"] == "seen in Polestar recordings" and cookie["voters"] >= 3
    assert cookie["chance_pct"] is None


def test_designed_dungeon_loot_preserves_its_source_and_zone():
    ref = gear.reference()
    source = ref.data["sources"]["16668"][0]
    assert source["via"] == "forever-db" and source["npc"] == 10440
    who = gear.Who(cls="SHAMAN", level=60, side=2, race_id=2, professions={}, done=set(), zone=None)
    drop = gear._source(ref, source, who, ref.item(16668))
    assert drop["basis"] == "forever-db loot table" and drop["chance_pct"] == 11.1
    assert drop["dungeon"] and "Stratholme" in drop["zone"]


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
    dal = forever.area(16544)
    assert {k: dal[k] for k in ("name", "instance", "map", "levels")} == {
        "name": "City of Dalaran", "instance": "dungeon", "map": 2959, "levels": [28, 35]}
    # The floor is Polestar's, from players' recordings: it moves with every
    # data build (31.6 at v0.4.1.1, 31.7 at v0.4.2.1), the range doesn't.
    assert 28 <= dal["floor"] <= 35
    assert forever.area(209)["instance"] == "dungeon"
    assert "instance" not in forever.area(11), "Wetlands is no instance"


def test_polestar_floors_by_its_codex_rule():
    """Polestar's floor (tools/forever_reference/export.py floor_of, its
    Codex rule): the range's bottom or players' 25th percentile entry level,
    whichever is higher. Scarlet Monastery opens at 30 but players start it
    later (35.1 at v0.4.1.1, 35.3 at v0.4.2.1: the percentile moves with
    every data build, so the test holds the rule, not the number)."""
    assert forever.area(796)["levels"] == [30, 38] and 33 <= forever.area(796)["floor"] <= 38
    assert forever.area(491)["floor"] >= forever.area(491)["levels"][0]
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


def test_factions_carry_their_side_from_the_client_table():
    """Each reputation's side from Faction's parents (Alliance Forces and Horde
    Forces for the battleground ones); a neutral town has none."""
    assert forever.faction(2799) == {"name": "Theramore Expeditionary Force", "side": "Alliance"}
    assert forever.faction(2798) == {"name": "Darkspear Raiders", "side": "Horde"}
    assert forever.faction(509)["side"] == "Alliance" and forever.faction(510)["side"] == "Horde"
    assert forever.faction(72)["side"] == "Alliance"  # Stormwind, under Alliance
    assert forever.faction(21) == {"name": "Booty Bay"} and "side" not in forever.faction(470)
    assert forever.faction(None) is None and forever.faction(999999) is None


def test_forevers_new_zones_and_dungeons_have_names():
    """Forever adds zones and dungeons faster than forever-db is published:
    the areas come from the newest client build we know, every zone and
    every Group Finder instance, named by a quest yet or not."""
    assert forever.area(16591) == {"name": "Riverglades"}
    drowned = forever.area(16550)
    assert {k: drowned[k] for k in ("name", "instance", "map", "levels")} == {
        "name": "The Drowned City", "instance": "dungeon", "map": 2970, "levels": [35, 42]}
    assert forever.area_of_map(2970) == 16550 and forever.area_id("The Drowned City") == 16550
    assert forever.area(16842)["name"] == "Krol'dok Stronghold" and forever.area(16620)["levels"] == [48, 55]
    # An event's instanced copy of a zone never takes the zone's name.
    assert forever.area_id("Deadwind Pass") == 41 and forever.area_id("Stranglethorn Vale") == 33
    assert forever.area_of_map(109) == 1417, "Sunken Temple keeps the area its quests name"
    assert forever.ui_map(2548) == "Riverglades" and forever.ui_map(1434) == "Stranglethorn Vale"
    assert forever.ui_map(None) is None and forever.ui_map(999999) is None


def test_every_zone_our_data_names_has_a_name():
    """The check that fails when a quest, a giver or a dungeon in the bundled
    data points at a zone no table names (it used to read "Quests in an
    unknown zone" on the gear page)."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "data"
    ref = forever.data()
    bad = sorted({v[3] for v in ref["quests"].values() if v[3] and not forever.area(v[3])})
    assert not bad, f"forever-db zones with no name: {bad}"
    fdb = json.loads((root / "foreverdb" / "item-sources.json").read_text(encoding="utf-8"))
    no_zone = sorted({s["quest"] for e in fdb["items"].values() for s in e.get("sources") or []
                      if s.get("kind") == "quest"
                      and not ((forever.quest(s["quest"]) or {}).get("zone")
                               or forever.ui_map((s.get("giver") or {}).get("map")))
                      and (s.get("giver") or {}).get("map")})
    assert not no_zone, f"ForeverDB quests whose giver's map has no name: {no_zone}"
    no_inst = sorted(k for k in fdb["instances"] if not forever.area_of_map(k))
    assert not no_inst, f"ForeverDB instances with no area: {no_inst}"
    dq = json.loads((root / "dungeons" / "quests.json").read_text(encoding="utf-8"))["dungeons"]
    no_area = sorted(f"{m} {d['name']}" for m, d in dq.items() if not forever.area(d.get("area")))
    assert not no_area, f"dungeons with no area: {no_area}"


def test_the_reference_is_at_its_pins():
    """forever-db at its own release pin and the client tables at the build
    the watch knows, not Polestar's bundled copy: the file says which, and
    they are the pins (a rerun at the pins gives the same bytes:
    route-reference.yml). Every area and every instance map is there,
    named by a quest yet or not."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tools" / "forever_reference" / "upstream.py"
    spec = importlib.util.spec_from_file_location("forever_upstream", path)
    up = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(up)
    ref = forever.data()
    src = ref["source"]
    assert up.pinned() == {"forever_db": src["forever_db"]["version"], "client_build": src["client_tables"]["build"]}
    assert len(ref["areas"]) > 1000
    # Instance maps by their own area: Riverglades' neighbours and the
    # dungeons no quest names (Blackmaw Hold, Map 2974), Deadmines by its
    # own area, not the Westfall area the client also puts on its map.
    assert forever.area_of_map(2974) == 16554 and forever.area_of_map(36) == 1581
    assert ref["maps"]["2970"] == {"name": "The Drowned City", "type": "dungeon", "area": 16550}


def test_a_quest_forever_db_lacks_is_zoned_where_its_giver_stands():
    """Saving Sunnyglade (94877) and The Saltseer Captain (95791) came after
    forever-db 2026.11.1 (Polestar v0.4.2.2's copy), and the gear page
    grouped them under "an unknown zone". forever-db 2026.11.2 zones the
    first in Riverglades and gives the second no zone: its boss puts it in
    The Drowned City, not Booty Bay where ForeverDB.net places its giver."""
    r = gear.reference()
    assert r.available, r.why
    qs = r.data["quests"]
    assert qs["94877"]["zone_name"] == "Riverglades"
    assert forever.quest(95791)["zone"] is None
    assert qs["95791"]["zone_name"] == forever.quest_zone(95791, 1434) == "The Drowned City"


def test_a_quest_forever_db_zones_nowhere_takes_its_dungeon():
    """The Drowned City (95775) has no zone in forever-db; Polestar's journal
    puts it in the dungeon, so the gear page and the plan say where it is
    done, and it counts as a dungeon quest."""
    assert forever.quest(95775)["zone"] is None
    assert forever.quest_dungeon(95775) == 16550
    assert forever.quest_zone(95775) == "The Drowned City"
    assert forever.quest_zone(631) == "Wetlands", "forever-db's zone first"
    assert forever.quest_zone(94877, 2548) == "Riverglades", "else the giver's map"
    assert forever.quest_zone(9999999) is None


def test_a_quest_that_kills_a_dungeon_boss_is_the_dungeons():
    """tools/dungeon_quests/export.py `boss`: The Saltseer Captain (95791,
    handed in at Booty Bay, no zone) kills Captain Dreadrise (260042), and
    The Only Good Brinescale... (95776) kills Var'Taka (258924): encounters
    of The Drowned City in the client's DungeonEncounter, so both quests
    are the dungeon's."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert {"Captain Dreadrise", "Var'Taka"} <= set(forever.area(16550)["bosses"])
    got = json.loads((root / "data" / "dungeons" / "quests.json").read_text())["quests"]
    assert got["95791"] == [[2970, ["boss"]]] and got["95776"] == [[2970, ["boss"]]]
