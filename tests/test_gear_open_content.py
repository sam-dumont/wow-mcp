"""What the gear advice may propose (wowstats.gear): never content Forever
hasn't opened (owner, 2026-11-25: "it proposes arena rewards for me even
though it's not opened yet"), PvP rewards only when the player counts them
(default off), and PvP gear easy in the PvP weights.

Run on the shipped data (gear-reference, ForeverDB's sources, the KB's
rules) with a level-36 Alliance shadow priest dressed like the owner's."""

from __future__ import annotations

import pytest

from wowstats import gear, gearmode

ARENA = {18706: "Arena Master", 18709: "Arena Wristguards", 18710: "Arena Bracers", 18711: "Arena Bands",
         18712: "Arena Vambraces"}
TALISMAN = 21119  # Talisman of Arathor: The League of Arathor's quartermaster, Neutral
LOREKEEPER = 19525  # Lorekeeper's Ring: Silverwing Sentinels, Friendly
WORN = {2: "Giantstone Medallion", 3: "Palm Frond Mantle", 5: "Robes of the Disgraced Thane", 6: "Violet Sash",
        7: "Magistrate's Pantaloons", 8: "Red Woolen Boots", 9: "Graveweave Bindings", 10: "Ephemeral Grips",
        11: "The Queen's Jewel", 12: "Ring of Pure Silver", 13: "Lookie's Spyglass", 15: "Vine Pruner's Cloak",
        16: "Spine of the Devourer", 18: "Gravestone Scepter"}


@pytest.fixture(scope="module")
def ref():
    r = gear.reference()
    assert r.available, r.why
    return r


def sheet(ref) -> dict:
    ids = {v: int(k) for k, v in ref.data["names"].items()}
    return {"class": "PRIEST", "race": "Gnome", "faction": "Alliance", "spec": "shadow",
            "items": [{"item_id": ids[n], "name": n, "slot": sl} for sl, n in WORN.items()],
            "professions": [{"name": "Tailoring", "rank": 120}]}


def ids(a: dict) -> set[int]:
    return {u["item_id"] for u in a["upgrades_all"]}


def advice(ref, level=36, **kw) -> dict:
    return gear.advise(ref, sheet(ref), level=level, zone="Desolace", spec="shadow", limit=40, every=True, **kw)


def test_arena_rewards_are_never_proposed(ref):
    """The Gurubashi Arena's chest is in ForeverDB's sources from Classic's
    data only: a 100% chest read "some effort", and Arena Master was the
    owner's trinket pick at 36. The KB's content.events_not_open closes it."""
    for level in (36, 50, 60):
        for kw in ({}, {"pvp": True}, {"mode": "pvp"}, {"mode": "pve"}):
            a = advice(ref, level, **kw)
            assert not ids(a) & set(ARENA), (level, kw)
            unknown = {x.get("item_id") for sl in a["slots"] for x in (sl.get("bis_unknown"),) if x}
            assert not unknown & set(ARENA)  # not a "where: unknown" either: we know where, and it's shut


def test_closed_content_by_rule_and_date(ref):
    chest = next(s for s in ref.data["sources"]["18706"] if s["kind"] == "container")
    assert gear.closed_content(chest, "2026-11-25") == "the Gurubashi Arena event"
    # Seen in Forever wins over any rule: the event reopens by itself.
    assert gear.closed_content({**chest, "label": "s"}, "2026-11-25") is None
    ony = {"kind": "drop", "instance_map": 249, "label": "k"}
    assert gear.closed_content(ony, "2026-12-08") == "Onyxia's Lair (raid)"
    assert gear.closed_content(ony, "2026-12-09") is None  # the KB's unlock day
    # A dungeon drop only Classic's data knows (labelled k) stays: not seen
    # yet isn't closed (Overlord Ramtusk in Razorfen Kraul).
    assert gear.closed_content({"kind": "drop", "instance_map": 47, "label": "k"}, "2026-11-25") is None


def test_only_closed_content_goes(ref):
    """The rules remove 255 items outright on 2026-11-25, all raid gear
    and raid quest rewards (Ahn'Qiraj, Naxxramas, Zul'Gurub, Onyxia's Lair,
    Blackwing Lair) plus the arena's 5; every other item keeps a way."""
    from wowstats import forever

    def zone(s):
        return (forever.quest(s["quest"]) or {}).get("zone") if s["kind"] == "quest" else None

    gone = [sid for sid, ss in ref.data["sources"].items()
            if all(gear.closed_content(s, "2026-11-25", zone(s)) for s in ss)]
    # Conqueror's Crown (Ahn'Qiraj) and Cryptstalker Headpiece (Naxxramas): quests handed in outside.
    assert {int(x) for x in gone} >= set(ARENA) | {21329, 22438}
    assert len(gone) <= 300, len(gone)


def test_pvp_toggle_changes_the_candidates(ref):
    off, on = advice(ref), advice(ref, pvp=True)
    assert off["pvp_rewards"] == {"on": False, "forced": False}
    assert on["pvp_rewards"] == {"on": True, "forced": False}
    # Battleground reputation gear: gone with the vendor that sells it, back with the toggle.
    for iid in (TALISMAN, LOREKEEPER):
        assert iid not in ids(off) and iid in ids(on)
    assert not any(s.get("pvp") for u in off["upgrades_all"] for s in u["sources"])
    # Nothing else changes: PvE gear is the same either way.
    pvp_items = {u["item_id"] for u in on["upgrades_all"] if any(s.get("pvp") for s in u["sources"])}
    assert ids(on) - pvp_items == ids(off)


def test_pvp_mode_counts_pvp_rewards_and_they_are_easy(ref):
    a = advice(ref, 60, mode="pvp")
    assert a["pvp_rewards"] == {"on": True, "forced": True}
    pvp = [u for u in a["upgrades_all"] if any(s.get("pvp") for s in u["sources"])]
    assert any(any(s["kind"] == "pvp" for s in u["sources"]) for u in pvp)  # rank gear
    assert TALISMAN in {u["item_id"] for u in pvp}  # battleground reputation gear
    assert all(u["effort"] == "easy" for u in pvp), {u["name"]: u["effort"] for u in pvp if u["effort"] != "easy"}
    # In the Leveling weights with the toggle on, a rank not held stays a long shot.
    lvl = advice(ref, 60, pvp=True)
    ranks = [u for u in lvl["upgrades_all"] if any(s["kind"] == "pvp" for s in u["sources"])]
    assert ranks and all(u["effort"] == "long shot" for u in ranks)


def test_toggle_kept_per_character(tmp_path):
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    assert gearmode.pvp(env, "Grimble-Rockbow") is False
    gearmode.save(env, "Grimble-Rockbow", "pve")
    assert gearmode.save_pvp(env, "Grimble Rockbow", True) is True
    assert gearmode.pvp(env, "Grimble-Rockbow") is True
    assert gearmode.saved(env, "Grimble-Rockbow") == "pve"  # the mode stays
    assert gearmode.pvp(env, "Someone-Else") is False
    gearmode.save_pvp(env, "Grimble-Rockbow", False)
    assert gearmode.pvp(env, "Grimble-Rockbow") is False
    with pytest.raises(gearmode.ModeError, match="true or false"):
        gearmode.save_pvp(env, "Grimble-Rockbow", "yes")
    with pytest.raises(gearmode.ModeError, match="keeps no settings"):
        gearmode.save_pvp({}, "Grimble-Rockbow", True)


def test_a_pvp_vendor_is_a_pvp_reward(ref):
    """ForeverDB lists the Premier rank weapons at Sergeant Thunderhorn with
    no rank: the KB's pvp_rewards.sources names the PvP vendors, so the axe
    is no free "easy" pick for a character who never queued."""
    axe = ref.data["sources"]["272593"]  # Premier High Warlord's Battle Axe
    assert all(gear.is_pvp(s) for s in axe)
    who = gear.Who(cls="WARRIOR", level=60, side=2, race_id=2, professions={}, done=set(), zone=None)
    assert [gear._source(ref, s, who, ref.item(272593), 272593) for s in axe] == [None]
    who.pvp = True
    src = gear._source(ref, axe[0], who, ref.item(272593), 272593)
    assert src["pvp"] and gear.source_effort(src) == gear.LONG_SHOT  # honor and rank unknown
    who.pvp_mode = True
    assert gear.source_effort(gear._source(ref, axe[0], who, ref.item(272593), 272593)) == gear.EASY
