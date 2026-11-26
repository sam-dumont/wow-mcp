"""All gear consumers use the same quest and equip restrictions."""

from wowstats import forever, gear, insights


def test_all_sourced_quests_preserve_known_requirements():
    ref = gear.reference()
    for qid, quest in ref.data["quests"].items():
        source = forever.quest(qid)
        if not source:
            continue
        side, race, cls = forever.quest_gates(qid)
        if side in (1, 2):
            assert quest["side"] == side, qid
        if race:
            assert quest["race"] == race, qid
        if cls:
            assert quest["class"] == cls, qid
        if source["min_level"]:
            assert quest["req"] == source["min_level"], qid


def test_shared_class_and_side_specific_quests():
    ref = gear.reference()
    for side, own, other in (("Horde", 1048, 6), ("Alliance", 6, 1048)):
        sheet = {"class": "PRIEST", "race": "Skyborne", "faction": side}
        who = gear._context(ref, sheet, 60, None, None, "shadow", None).who

        def source(qid):
            return gear._source(ref, {"kind": "quest", "quest": qid}, who, {})

        assert source(own) and not source(other)
        assert bool(source(95063)) == (side == "Horde")
        assert not source(1792)  # Whirlwind Weapon is Warrior-only
        assert source(338)  # The Green Hills of Stranglethorn
        if side == "Alliance":
            assert gear._source_level(source(971)) == 18


def test_item_side_applies_to_held_and_full_advice():
    iid = 272603  # Premier High Warlord's War Staff, an unsided vendor
    ref = gear.reference()
    for side in ("Alliance", "Horde"):
        sheet = {"class": "PRIEST", "race": "Skyborne", "faction": side}
        for held in (None, {"bags": [iid]}):
            a = gear.advise(ref, sheet, level=60, spec="shadow", pvp=True, every=True, held=held)
            all_ids = {u["item_id"] for u in a["upgrades_all"]}
            assert (iid in all_ids) == (side == "Horde")
            if side == "Alliance":
                assert all(u["item_id"] != iid for u, s in insights.in_reach(a, 60))
