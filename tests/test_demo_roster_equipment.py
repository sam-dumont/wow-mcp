"""Each demo character can acquire the gear shown on its saved armory sheets."""
import json
import sys
from pathlib import Path

import pytest

from wowstats import crafting, forever, gear
from wowstats.plan import tripcost

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "fixtures"))
import demo3d  # noqa: E402
import generate  # noqa: E402
import roster  # noqa: E402
import roster_history  # noqa: E402


@pytest.mark.parametrize("c", roster.CHARACTERS, ids=lambda c: c.cls.lower())
def test_every_saved_outfit_is_wearable_and_its_sources_are_open(c):
    ref = gear.reference()
    index = roster.CHARACTERS.index(c) + 1
    start = roster_history.ZONES[c.cls][0][0]
    slots = {row["slot"] for row in roster.starter_gear(c)}
    assert {5, 7, 8, 16} <= slots
    if start >= 20:
        assert len(slots) >= 8
    side = tripcost.SIDE_BIT[c.faction]
    bits = side, tripcost.race_bit(c.race, c.faction), tripcost.CLASS_BIT[c.cls]
    rep, first, level = {}, True, start
    for session in roster_history.story(c, index):
        for event in session:
            if event.get("kind") == "level":
                level = int(event["level"])
            if event.get("kind") == "rep":
                rep[int(event["faction_id"])] = int(event["reaction"])
            if event["feed"] != "wow-gear":
                continue
            prof = {p["name"]: p["rank"] for p in json.loads(event["professions"])}
            who = gear.Who(c.cls, level, side, c.race_id, {}, set(), c.zone)
            items = json.loads(event["items"])
            if first:
                assert {5, 7, 8, 16} <= {it["slot"] for it in items}
                if start >= 20:
                    assert len(items) >= 8
                first = False
            for it in items:
                iid = it["item_id"]
                info = ref.item(iid)
                # For a level-free quest reward the planner approximates an advice level
                # from item level. The game enforces the quest and its actual equip gate.
                usable, why, _ = gear.usability(ref, info, c.cls, level, who)
                assert usable and (info["req"] or 1) <= level, (c.name, level, it, why)
                sources = ref.data["sources"].get(str(iid), [])
                if (c.race, c.cls) == ("Troll", "MAGE") and iid in roster.TROLL_MAGE_LOADOUT:
                    continue  # the four visible items in the client's Troll Mage starting loadout
                assert sources, (c.name, it)
                assert any(s.get("side") in (None, c.faction) for s in sources), (c.name, it)
                quests = [s["quest"] for s in sources if s["kind"] == "quest"]
                if len(quests) == len(sources):
                    assert any(tripcost.eligible(*forever.quest_gates(qid), *bits)
                               and ((forever.quest(qid) or {}).get("min_level") or 0) <= level
                               for qid in quests), (c.name, level, it)
                for s in sources:
                    if s["kind"] == "craft" and iid in crafting.reference()["_bop"]:
                        assert prof.get(s["profession"], 0) >= s["skill"], (c.name, level, it, s)
                    if s["kind"] == "reputation":
                        assert rep.get(s["faction"], 0) >= gear.STANDING_IDS[s["standing"]], (c.name, level, it)
    assert not first


def test_only_the_troll_mage_creation_loadout_is_a_source_for_starting_gear():
    from_client = [6140, 1395, 55, 35]  # CharacterLoadoutItem where CharacterLoadoutID = 2494
    assert list(roster.TROLL_MAGE_LOADOUT) == from_client
    row = {"feed": "wow-gear", "class": "MAGE", "race": "Troll", "items": [
        {"slot": slot, "item_id": iid} for slot, iid in zip([5, 7, 8, 16], from_client, strict=True)]}
    generate.in_forever([row])
    for cls, race in (("MAGE", "Human"), ("WARLOCK", "Troll")):
        with pytest.raises(ValueError, match="nothing live"):
            generate.in_forever([{**row, "class": cls, "race": race}])


def test_outfit_archive_covers_saved_and_reconstructed_gear():
    outfits = demo3d.roster_outfits()
    for cls, old_item in (("PRIEST", 6743), ("MAGE", 4944), ("ROGUE", 281322)):
        # The recorded acquisitions put these items into the replay between exact sheets.
        sheets = next(sheets for profile, sheets in outfits.items() if profile.cls == cls)
        assert any(len(s) > 1 and any(it["item_id"] == old_item for it in s) for s in sheets)
        assert any(len(s) == 1 and s[0]["item_id"] == old_item for s in sheets)
    # Slot toggles can also isolate the very same weapon with another enchant.
    staff = {"slot": 16, "item_id": 35}
    ring = {"slot": 11, "item_id": 5001}
    enchanted = {**staff, "enchant_id": 256}
    sheets = demo3d.outfit_phases([[ring, staff], [ring, enchanted], [staff, ring]])
    assert len(sheets) == 5
    assert [staff] in sheets and [enchanted] in sheets
