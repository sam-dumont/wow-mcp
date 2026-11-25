"""What the gear advice may propose (wowstats.gear): never content Forever
hasn't opened (owner, 2026-11-25: "it proposes arena rewards for me even
though it's not opened yet"; endgame is shut unless a sourced entry opens it), PvP rewards only when the player counts them
(default off), and PvP gear easy in the PvP weights.

Run on the shipped data (gear-reference, ForeverDB's sources, the meta's
endgame section data/kb/endgame.json) with an Alliance shadow priest dressed
like the owner's level-36 one."""

from __future__ import annotations

import pytest

from wowstats import gear, gearmode

TODAY, OPENING = "2026-11-25", "2026-12-09"  # Onyxia, Barrow Deeps and Hyjal Summit open on the 9th
ARENA_MASTER = 18706
ARENA = {18706: "Arena Master", 18709: "Arena Wristguards", 18710: "Arena Bracers", 18711: "Arena Bands",
         18712: "Arena Vambraces"}
TALISMAN = 21119  # Talisman of Arathor: The League of Arathor's quartermaster, Neutral
LOREKEEPER = 19525  # Lorekeeper's Ring: Silverwing Sentinels, Friendly
# Theramore Expeditionary Force (Darkspear Islands, the Alliance's side), sold
# by Bryanna Embreeze, whom ForeverDB gives the Horde's side.
SEALS = {271904: "Volunteer's Lucky Seal", 271905: "Enriched Seal"}
DARKSPEAR_SIGNET = 272071  # Darkspear Raiders (the Horde's), reputation only
RUNE_OF_DUTY = 21568  # Classic's data names Warsong Outriders; seen sold by Silverwing's quartermaster
SEA_SHELL = 274749  # Souvenir Sea Shell: Booty Bay, a neutral town ForeverDB gives the Horde's side
WORN = {2: "Giantstone Medallion", 3: "Palm Frond Mantle", 5: "Robes of the Disgraced Thane", 6: "Violet Sash",
        7: "Magistrate's Pantaloons", 8: "Red Woolen Boots", 9: "Graveweave Bindings", 10: "Ephemeral Grips",
        11: "The Queen's Jewel", 12: "Ring of Pure Silver", 13: "Lookie's Spyglass", 15: "Vine Pruner's Cloak",
        16: "Spine of the Devourer", 18: "Gravestone Scepter"}


@pytest.fixture(scope="module")
def ref():
    r = gear.reference()
    assert r.available, r.why
    return r


def sheet(ref, faction: str = "Alliance") -> dict:
    ids = {v: int(k) for k, v in ref.data["names"].items()}
    return {"class": "PRIEST", "race": "Gnome" if faction == "Alliance" else "Troll", "faction": faction,
            "spec": "shadow",
            "items": [{"item_id": ids[n], "name": n, "slot": sl} for sl, n in WORN.items()],
            "professions": [{"name": "Tailoring", "rank": 120}]}


def ids(a: dict) -> set[int]:
    return {u["item_id"] for u in a["upgrades_all"]}


def advice(ref, level=36, faction="Alliance", **kw) -> dict:
    return gear.advise(ref, sheet(ref, faction), level=level, zone="Desolace", spec="shadow", limit=40, every=True,
                       **kw)


def test_arena_rewards_are_never_proposed(ref):
    """The Gurubashi Arena's chest is in ForeverDB's sources from Classic's
    data only: a 100% chest read "some effort", and Arena Master was the
    owner's trinket pick at 36. The endgame section keeps the event shut."""
    for level in (36, 50, 60):
        for kw in ({}, {"pvp": True}, {"mode": "pvp"}, {"mode": "pve"}):
            a = advice(ref, level, **kw)
            assert not ids(a) & set(ARENA), (level, kw)
            unknown = {x.get("item_id") for sl in a["slots"] for x in (sl.get("bis_unknown"),) if x}
            assert not unknown & set(ARENA)  # not a "where: unknown" either: we know where, and it's shut


def zone(s):
    from wowstats import forever

    return (forever.quest(s["quest"]) or {}).get("zone") if s["kind"] == "quest" else None


def shut(ref, iid: int, today: str = TODAY) -> list:
    return [gear.closed_content(s, today, zone(s)) for s in ref.data["sources"][str(iid)]]


HEROISM = 21999  # Helm of Heroism: Saving the Best for Last (9006/9013), the end of Lord Valthalak's chain
SPAULDERS = 22001  # Spaulders of Heroism: Anthion's Parting Words, the same chain's middle
MC = 21520  # Ravencrest's Legacy: Treasure of the Timeless One, after a Molten Core quest
KAZZAK = 17111  # Blazefury Medallion: Lord Kazzak, a world boss
ONY_HEAD = 7496  # Celebrating Good Times: after Victory for the Alliance (Onyxia's head)
RAMTUSK = {"kind": "drop", "instance_map": 47, "zone": 491, "zone_name": "Razorfen Kraul", "label": "k"}
STRAT = 8283  # Arcane Armor: a level-56 drop in Stratholme only Classic's data knows (k)


def test_endgame_is_shut_unless_the_section_opens_it(ref):
    """Owner, 2026-11-25: "it should be explicit for end game, not 'we don't
    know so we count as opened'"."""
    assert shut(ref, ARENA_MASTER) == ["Gurubashi Arena chest event (event, not open)"]
    for iid in (HEROISM, SPAULDERS):  # every class's version, both sides
        assert all(r and r.startswith("Dungeon Set 2") for r in shut(ref, iid)), iid
    assert all(r and r.startswith("Molten Core") for r in shut(ref, MC))
    assert shut(ref, KAZZAK) == ["World bosses (world-boss, not open)"]
    # A source players saw in Forever counts whatever the section says.
    chest = ref.data["sources"][str(ARENA_MASTER)][0]
    assert gear.closed_content({**chest, "label": "s"}, TODAY) is None


def test_an_announced_day_reopens_by_itself(ref):
    ony = {"kind": "drop", "instance_map": 249, "zone": 2159, "zone_name": "Onyxia's Lair", "npc": 10184,
           "label": "k"}
    assert gear.closed_content(ony, "2026-12-08") == "Onyxia's Lair (raid, opens 2026-12-09)"
    assert gear.closed_content(ony, OPENING) is None
    q = {"kind": "quest", "quest": ONY_HEAD, "label": "k"}
    assert gear.closed_content(q, TODAY, zone(q)) and gear.closed_content(q, OPENING, zone(q)) is None
    # Forever's new raids: no loot in our data yet, so a drop shaped like one.
    for raid in ("Barrow Deeps", "Hyjal Summit"):
        drop = {"kind": "drop", "zone_name": raid, "npc": 1, "label": "k"}
        assert gear.closed_content(drop, TODAY) == f"{raid} (raid, opens 2026-12-09)"
        assert gear.closed_content(drop, OPENING) is None
    # A raid nobody announced stays shut after the 9th.
    assert all(shut(ref, MC, OPENING)) and all(shut(ref, HEROISM, OPENING))


def test_a_raid_no_entry_names_is_shut_by_structure(ref, monkeypatch):
    """Endgame comes from the data, not the list: drop the section and every
    raid drop, raid quest and world boss is still shut; dungeons aren't."""
    from wowstats import endgame

    monkeypatch.setattr(endgame, "section", lambda: {})
    ony = {"kind": "drop", "instance_map": 249, "zone": 2159, "zone_name": "Onyxia's Lair", "label": "k"}
    assert "not in the endgame section" in gear.closed_content(ony, OPENING)
    assert all(shut(ref, MC)) and all(shut(ref, KAZZAK))
    assert gear.closed_content(RAMTUSK, TODAY) is None


def test_levelling_and_level_60_dungeons_still_count(ref):
    # A level-30 dungeon drop only Classic's data knows (Overlord Ramtusk, k).
    assert gear.closed_content(RAMTUSK, TODAY) is None
    # Level-60 five-mans are open (Forever launched with every Classic
    # dungeon): their Classic-only drops count, Lord Valthalak's summons don't.
    assert not any(shut(ref, STRAT))
    valthalak = {"kind": "drop", "instance_map": 229, "zone": 1583, "zone_name": "Blackrock Spire", "npc": 16042,
                 "label": "k"}
    assert gear.closed_content(valthalak, TODAY).startswith("Dungeon Set 2")
    drakkisath = {**valthalak, "npc": 10363}
    assert gear.closed_content(drakkisath, TODAY) is None
    # Ivus is a level-63 boss summoned in Alterac Valley, a live battleground: no world boss.
    ivus = next(s for s in ref.data["sources"]["19109"] if s.get("npc") == 13419)
    assert gear.closed_content(ivus, TODAY) is None


def test_only_endgame_goes(ref):
    """On 2026-11-25 the section shuts 438 items outright (every source
    shut): raid gear and raid quest rewards, the Dungeon Set 2 chain, world
    bosses, the arena, the Ahn'Qiraj war effort; 54 of them come back with
    Onyxia on 9 Dec. Nothing under level 45 goes but the arena's chest."""
    def gone(day):
        return {int(sid) for sid, ss in ref.data["sources"].items()
                if ss and all(gear.closed_content(s, day, zone(s)) for s in ss)}

    now, later = gone(TODAY), gone(OPENING)
    assert now >= set(ARENA) | {HEROISM, SPAULDERS, MC, KAZZAK, 21329, 22438}
    assert later < now and HEROISM in later
    assert 380 <= len(now) <= 480, len(now)
    low = {i for i in now if ((ref.item(i) or {}).get("req") or 60) < 45} - set(ARENA)
    assert not low, sorted(low)


def test_a_level_60_sees_no_closed_endgame(ref):
    a = advice(ref, 60, mode="pve")
    got = ids(a)
    assert not got & ({HEROISM, SPAULDERS, MC, KAZZAK} | set(ARENA))


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


def test_a_reputations_gear_is_its_sides(ref):
    """The client's Faction table says whose a reputation is; its
    quartermaster sells to that side only, whatever side ForeverDB gives the
    vendor (2026-11-25: Theramore's seals had no source for an Alliance
    character and were offered to the Horde)."""
    alliance, horde = advice(ref, 40, pvp=True), advice(ref, 40, faction="Horde", pvp=True)
    assert set(SEALS) <= ids(alliance) and not set(SEALS) & ids(horde)
    seal = next(u for u in alliance["upgrades_all"] if u["item_id"] == 271904)
    vendor = next(s for s in seal["sources"] if s["kind"] == "vendor")
    assert vendor["name"] == "Bryanna Embreeze" and vendor["reputation_id"] == 2799 and vendor["pvp"]
    # The rep's effort, never the long shot of a PvP vendor with no rank.
    assert seal["effort"] != "long shot"
    assert DARKSPEAR_SIGNET in ids(horde) and DARKSPEAR_SIGNET not in ids(alliance)
    # Gear both quartermasters sell keeps Classic's both-sides reading.
    assert RUNE_OF_DUTY in ids(alliance) and RUNE_OF_DUTY in ids(horde)
    # Battleground reputation gear is a PvP reward: gone with the toggle off.
    assert not set(SEALS) & ids(advice(ref, 40))


def test_a_neutral_towns_gear_is_everyones(ref):
    """Ratchet and Booty Bay are neutral (Steamwheedle Cartel): their
    quartermasters sell to both sides, PvP or not."""
    for faction in ("Alliance", "Horde"):
        a = advice(ref, 32, faction=faction)
        shell = next((u for u in a["upgrades_all"] if u["item_id"] == SEA_SHELL), None)
        assert shell, faction
        assert {s["kind"] for s in shell["sources"]} == {"vendor", "reputation"}
        assert not any(s.get("pvp") for s in shell["sources"])


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
    assert gearmode.pvp(env, "Nibbet-Gearholt") is False
    gearmode.save(env, "Nibbet-Gearholt", "pve")
    assert gearmode.save_pvp(env, "Nibbet Gearholt", True) is True
    assert gearmode.pvp(env, "Nibbet-Gearholt") is True
    assert gearmode.saved(env, "Nibbet-Gearholt") == "pve"  # the mode stays
    assert gearmode.pvp(env, "Someone-Else") is False
    gearmode.save_pvp(env, "Nibbet-Gearholt", False)
    assert gearmode.pvp(env, "Nibbet-Gearholt") is False
    with pytest.raises(gearmode.ModeError, match="true or false"):
        gearmode.save_pvp(env, "Nibbet-Gearholt", "yes")
    with pytest.raises(gearmode.ModeError, match="keeps no settings"):
        gearmode.save_pvp({}, "Nibbet-Gearholt", True)


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
