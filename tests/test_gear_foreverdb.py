"""The gear score against ForeverDB.net's own (wowstats.gear, owner
2026-11-23: their weights and rules, credited).

tests/data/foreverdb-ladders.json.gz holds five specs' best-in-slot ladders
in all three modes as ForeverDB stores them (tools/foreverdb_weights/export.py
--fixtures): each item in their encoding and the score their server gave it.
Every item is scored here with our weights file and our score_with, and the
picks of the verified port of their page (foreverdb-picks.json, five levels a
spec and mode) are made again with our order, our percent rule and our weapon
setups."""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

import pytest

from wowstats import gear

DATA = Path(__file__).parent / "data"
SPECS = {"priest-shadow": ("PRIEST", "shadow"), "priest-holy": ("PRIEST", "holy"),
         "warrior-fury": ("WARRIOR", "fury"), "warrior-protection": ("WARRIOR", "protection"),
         "hunter-beast-mastery": ("HUNTER", "beastmastery")}
# ForeverDB's stat ids, as tools/foreverdb_weights/export.py names them.
NAMES = {3: "agi", 4: "str", 5: "int", 6: "spi", 7: "sta", 12: "defense", 13: "dodgeRating", 14: "parryRating",
         15: "blockRating", 31: "hitRating", 32: "critRating", 36: "haste", 37: "expertise", 38: "ap", 39: "rap",
         41: "heal", 42: "sp", 43: "mp5", 46: "hp5", 47: "spellPen", 48: "blockValue", 84: "spHoly",
         85: "spFire", 86: "spNature", 87: "spFrost", 88: "spShadow", 89: "spArcane"}
SLOTS = ("head", "neck", "shoulder", "back", "chest", "wrist", "hands", "waist", "legs", "feet", "ranged")


@pytest.fixture(scope="module")
def ladders() -> dict:
    return json.loads(gzip.decompress((DATA / "foreverdb-ladders.json.gz").read_bytes()))


@pytest.fixture(scope="module")
def ref():
    r = gear.reference()
    assert r.available, r.why
    return r


def nm(k) -> str:
    if k in ("dps", "wand", "armor"):
        return k
    return "dmgheal" if int(k) == 45 else NAMES.get(int(k), f"stat{k}")


def ours(it: dict) -> dict:
    """A ForeverDB item in the item table's shape: "+X damage and healing"
    (45) is spell power equal to healing there (a separate `dmgheal` when the
    item also has a healing or a damage line), a weapon its DPS."""
    st: dict = {}
    lines = list(it.get("st") or []) + list(it.get("pct") or [])
    alone = not any(int(k) in (41, 42) for k, _ in lines)
    for k, v in lines:
        k = int(k)
        if k == 45 and alone:
            st["sp"] = st.get("sp", 0) + v
            st["heal"] = st.get("heal", 0) + v
        else:
            st[nm(k)] = st.get(nm(k), 0) + v
    if it.get("dmg") and it.get("spd"):
        st["dps"] = (it["dmg"][0] + it["dmg"][1]) / 2 / (it["spd"] / 1000)
    if it.get("ar"):
        st["armor"] = it["ar"]
    return {"inv": it["inv"], "class": it["c"], "subclass": it.get("sc"), "ilvl": it.get("il"), "stats": st,
            "set": {nm(k): v for k, v in it.get("sb") or []}}


def py_round(x: float) -> float:
    return round(x, 1)


def test_weights_are_foreverdbs(ref, ladders):
    """Our file's three tables equal the ones ForeverDB scored its ladders
    with (Leveling computed from PvE by the rule, in tools/foreverdb_weights)."""
    for slug, (cls, key) in SPECS.items():
        spec = ref.spec_weights(cls, key)
        for mode in ("pve", "lvl", "pvp"):
            theirs = {nm(k): v for k, v in ladders[slug]["weights"][mode].items()}
            assert spec[mode] == pytest.approx(theirs), (slug, mode)


def test_every_ladder_score(ref, ladders):
    """Every item of five specs' ladders, in all three modes, scores what
    ForeverDB's server gave it, to 0.1. The few off by 0.1 sit on an x.x5
    boundary (their float sum order rounds the other way)."""
    same = off = 0
    worst = 0.0
    for slug, (cls, key) in SPECS.items():
        d = ladders[slug]
        for mode in ("pve", "lvl", "pvp"):
            w, _ = gear.weights_for(ref, cls, key, mode)
            for entries in d["ladder"][mode].values():
                for iid, _lv, sc in entries:
                    got = gear.score_with(ours(d["items"][str(iid)]), w)
                    if abs(py_round(got) - sc) < 0.051:
                        same += 1
                    else:
                        off += 1
                        worst = max(worst, abs(got - sc))
    assert same >= 36_690, (same, off)  # 36,690 of 36,746 on 2026-11-23
    assert off <= 56 and worst <= 0.051, (off, worst)


def test_game_items_score_like_foreverdb(ref, ladders):
    """The items themselves, from the game's own tables (data/game-items.json,
    tools/game_items/export.py; owner 2026-11-24): every ladder item scored
    from our item data, not ForeverDB's, lands on ForeverDB's score (to its
    rounding: their float sum order splits an x.x5 the other way). Before,
    Polestar's item table lacked 1,186 of these items and held stale numbers
    for others (Staff of the Shade: Classic's +21 Shadow damage, Forever's 37).

    What's left (2026-11-24): 5 items no client data has (Forever hotfixes
    the maintainer's client never received: Expeditionary's Cape...) and 3
    items only Classic Era describes (Grand Marshal's Warhammer, Marshal's
    Satin Grips and Handwraps), which Forever re-itemised."""
    game = gear.game_items()["items"]
    same = off = missing = 0
    off_from = set()
    for slug, (cls, key) in SPECS.items():
        d = ladders[slug]
        for mode in ("pve", "lvl", "pvp"):
            w, _ = gear.weights_for(ref, cls, key, mode)
            for entries in d["ladder"][mode].values():
                for iid, _lv, sc in entries:
                    info = ref.item(iid)
                    if info is None:
                        missing += 1
                    elif abs(gear.score_with(info, w) - sc) <= 0.05 + 1e-6:
                        same += 1
                    else:
                        off += 1
                        off_from.add(game[str(iid)].get("from", "file"))
    assert same >= 36_674, (same, off, missing)  # of 36,746 on 2026-11-24
    assert missing <= 54 and off <= 18, (missing, off)
    assert off_from <= {"era"}, off_from  # Forever's own rows are all exact


def test_the_five_items_from_the_game(ref):
    """The owner's examples (2026-11-24), as ForeverDB.net shows them."""
    staff, rod, wraps, spine, scepter = (ref.item(i) for i in (2549, 9381, 286989, 273040, 7001))
    assert staff["stats"] == pytest.approx({"sta": 15, "spShadow": 37, "dps": (46 + 70) / 2 / 2.6})
    assert rod["stats"] == pytest.approx({"heal": 9, "sp": 3, "dps": (42 + 79) / 2 / 1.7})
    assert wraps["stats"] == {"armor": 31, "hitRating": 10, "int": 9} and wraps["set"] == {"spi": 2.0}
    assert spine["stats"] == pytest.approx({"sta": 14, "spellPen": 3, "dps": (80 + 120) / 2 / 3.6})
    assert scepter["stats"] == pytest.approx({"spi": 3, "spShadow": 6, "dps": (30 + 57) / 2 / 1.5})
    assert ref.data["names"]["286989"] == "Violet Sorcerer's Wraps"


def test_ranged_hit_counts_for_hunters_only(ref):
    """Infernal Trickster Leggings' 1% hit works with a bow, gun or crossbow
    only (SpellEquippedItems): ForeverDB counts it for hunters, not warriors."""
    legs = ref.item(17754)
    assert legs["stats"].get("rangedHit") == 10
    fury, _ = gear.weights_for(ref, "WARRIOR", "fury", "pve")
    bm, _ = gear.weights_for(ref, "HUNTER", "beastmastery", "pve")
    assert gear.stat_weight(fury, "rangedHit") == 0 and gear.stat_weight(bm, "rangedHit") == bm["hitRating"]


def test_game_items_file_check():
    """The committed item file is well formed and holds the five items
    (tools/game_items/export.py --check, offline)."""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, str(root / "tools" / "game_items" / "export.py"), "--check"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


def test_set_share_counts_on_each_piece(ref):
    """Violet Sorcerer's Vestments: 10 Spirit over 5 pieces, 2 on each."""
    share = ref.weights["sets"]["286989"]
    assert share == {"spi": 2.0}


def available(d: dict, mode: str, slot: str, level: int) -> list:
    """The ladder's entries a page shows at this level with the default
    filters: PvP rewards only in PvP mode, "no known source" always."""
    groups = {"b", "d", "r", "c", "w", "q", "v"} | ({"p"} if mode == "pvp" else set())
    return [e for e in d["ladder"][mode].get(slot, []) if e[1] <= level
            and any(g in groups for g in (d["items"][str(e[0])].get("g") or "b"))]


def ranked(ref, d: dict, cls: str, key: str, mode: str, slot: str, level: int) -> list[tuple[int, float]]:
    """Our order: our score, then the higher item level, then the lower ID."""
    w, _ = gear.weights_for(ref, cls, key, mode)
    rows = []
    for iid, _lv, _sc in available(d, mode, slot, level):
        info = ours(d["items"][str(iid)])
        rows.append((py_round(gear.score_with(info, w)), info["ilvl"] or 0, iid))
    rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
    return [(r[2], r[0]) for r in rows]


def test_picks_match_foreverdb(ref, ladders):
    """Per slot the best, the two alternatives and their "-N% vs best", as
    ForeverDB's page picks them (75 spec, mode and level cases)."""
    want = json.loads((DATA / "foreverdb-picks.json").read_text())
    bad = []
    for case, slots in want.items():
        slug, mode, level = case.split("/")
        cls, key = SPECS[slug]
        d = ladders[slug]
        for slot in (*SLOTS, "finger1", "trinket1"):
            got = ranked(ref, d, cls, key, mode, slot.rstrip("1"), int(level))
            exp = slots[slot]
            if exp is None:
                if got:
                    bad.append((case, slot, "expected none"))
                continue
            if not got or got[0][0] != exp["best"]:
                bad.append((case, slot, got[:1], exp["best"]))
                continue
            if slot in SLOTS:
                alts = [[i, gear.behind_pct(got[0][1], s)] for i, s in got[1:3]]
                # Our score and theirs round apart on an x.x5 boundary: the
                # percent then moves by one.
                if [a[0] for a in alts] != [a[0] for a in exp["alts"]] or any(
                        abs(a[1] - e[1]) > 1 for a, e in zip(alts, exp["alts"], strict=True)):
                    bad.append((case, slot, alts, exp["alts"]))
    assert not bad, bad[:10]


def test_weapon_setups_match_foreverdb(ref, ladders):
    """Two-hander against main hand + off hand (an off-hand weapon at half
    its damage), by each spec's style: Shadow pairs a scepter and an orb in
    PvE at 34 and takes the staff in Leveling, Fury always dual-wields,
    Protection keeps its shield."""
    want = json.loads((DATA / "foreverdb-picks.json").read_text())
    bad = []
    for case, slots in want.items():
        slug, mode, level = case.split("/")
        level = int(level)
        cls, key = SPECS[slug]
        d = ladders[slug]
        w, info = gear.weights_for(ref, cls, key, mode)
        cands, seen = [], set()
        for slot in ("2h", "mh", "oh"):
            for iid, lv, _sc in available(d, mode, slot, level):
                if iid not in seen:
                    seen.add(iid)
                    cands.append({"id": iid, "info": ours(d["items"][str(iid)]), "at": lv, "sources": [{"kind": "x"}]})
        dual = gear.can_dual_wield((ref.gear.get("PROFICIENCY") or {}).get(cls), level)
        setups = gear.WeaponSetups(cands, gear.Worn(), w, dual, level, style=info["style"])
        best = setups.best_setup(level)
        exp = slots["weapons"]
        if best is None:
            if exp["main"] is not None:
                bad.append((case, None, exp))
            continue
        got = {"kind": "2h" if best["main"]["role"] == "two" else "1h", "main": gear._pid(best["main"]),
               "off": gear._pid(best["off"]) if best["off"] else None}
        if got != exp and got["off"] and exp["off"] and {**got, "off": None} == {**exp, "off": None}:
            # One deliberate difference: ForeverDB takes the off hand with the
            # best full score, then halves its weapon part; we take the one
            # worth most once halved. Ours must never be worth less.
            def half(i):
                return gear.score_with(ours(d["items"][str(i)]), w, as_offhand=True)
            if half(got["off"]) >= half(exp["off"]) - 1e-9:
                continue
        if got != exp:
            bad.append((case, got, exp))
    assert not bad, bad


def test_shadow_34_reads_like_foreverdb(ref, ladders):
    """The research's worked examples (Shadow, level 34): PvE scores
    Filigreed Shadow Circlet 15.2 and Black Slippers 16.5; Leveling's wand
    weight makes Gravestone Scepter 49.9."""
    d = ladders["priest-shadow"]
    w = {m: gear.weights_for(ref, "PRIEST", "shadow", m)[0] for m in ("pve", "lvl")}
    by_name = {it["n"]: ours(it) for it in d["items"].values()}
    assert py_round(gear.score_with(by_name["Filigreed Shadow Circlet"], w["pve"])) == 15.2
    assert py_round(gear.score_with(by_name["Black Slippers"], w["pve"])) == 16.5
    assert py_round(gear.score_with(by_name["Gravestone Scepter"], w["lvl"])) == 49.9
    assert py_round(gear.score_with(by_name["Gravestone Scepter"], w["pve"])) == 6.6


# --- the formula's pieces -----------------------------------------------------


def test_percent_lines_count_as_ratings():
    w = {"hitRating": 1.2, "critRating": 0.5, "dodgeRating": 1.0}
    info = {"inv": 1, "class": 4, "stats": {"spellHit": 1, "crit": 1, "dodge": 1}}
    assert gear.score_with(info, w) == pytest.approx(12 + 7 + 12)


def test_damage_and_healing_is_one_stat():
    """Spell power equal to healing is "+X damage and healing" (one line);
    unequal ones are a healing line and a damage line."""
    w = {"dmgheal": 1.0, "heal": 0.5, "sp": 1.0}
    assert gear.score_with({"inv": 6, "class": 4, "stats": {"sp": 11, "heal": 11}}, w) == pytest.approx(11)
    assert gear.score_with({"inv": 6, "class": 4, "stats": {"sp": 11, "heal": 33}}, w) == pytest.approx(11 + 16.5)


def test_school_damage_adds_uncapped():
    w = {"dmgheal": 1.0, "spShadow": 1.0, "spFire": 1.0}
    st = {"sp": 10, "heal": 10, "spShadow": 20, "spFire": 5}
    assert gear.score_with({"inv": 1, "class": 4, "stats": st}, w) == pytest.approx(35)


def test_weapon_dps_by_class_and_hand(ref):
    """Melee DPS for a warrior, never a bow's; ranged for a hunter, never a
    sword's; an off-hand weapon at half; a wand by the wand weight."""
    fury, _ = gear.weights_for(ref, "WARRIOR", "fury", "pve")
    bm, _ = gear.weights_for(ref, "HUNTER", "beastmastery", "pve")
    shadow, _ = gear.weights_for(ref, "PRIEST", "shadow", "lvl")
    sword = {"inv": 13, "class": 2, "subclass": 7, "stats": {"dps": 20}}
    bow = {"inv": 15, "class": 2, "subclass": 2, "stats": {"dps": 20}}
    wand = {"inv": 26, "class": 2, "subclass": 19, "stats": {"dps": 20}}
    assert gear.score_with(sword, fury) == pytest.approx(100)
    assert gear.score_with(sword, fury, as_offhand=True) == pytest.approx(50)
    assert gear.score_with(bow, fury) == 0
    assert gear.score_with(bow, bm) == pytest.approx(80)
    assert gear.score_with(sword, bm) == 0
    assert gear.score_with(wand, shadow) == pytest.approx(30)
    assert gear.score_with(wand, gear.weights_for(ref, "PRIEST", "shadow", "pve")[0]) == 0


def test_available_level():
    """The required level; else the item level minus 5; a quest reward
    once its quest is at most 5 levels above, never before its minimum."""
    assert gear.available_level({"req": 28, "ilvl": 33}) == 28
    assert gear.available_level({"req": 0, "ilvl": 63}) == 58
    assert gear.available_level({"ilvl": 3}) == 1
    assert gear._source_level({"kind": "quest", "level": 39, "req_level": 30}) == 34
    assert gear._source_level({"kind": "quest", "level": 60, "req_level": 58}) == 58
    assert gear._source_level({"kind": "drop"}) == 0


def test_behind_pct():
    """ForeverDB's "-N% vs best": rounded half up, at least 1, 0 when equal."""
    assert gear.behind_pct(28, 28) == 0
    assert gear.behind_pct(100, 99.9) == 1
    assert gear.behind_pct(100, 74.5) == 26
    assert gear.behind_pct(0, 0) == 0
    assert 100 - gear.behind_pct(100, 99.99) == 99  # never "100% of BiS" for a runner-up
    assert math.isclose(gear.behind_pct(42, 28), 33)


def test_modes():
    assert gear.mode_key(None) == "lvl" and gear.mode_key("Leveling") == "lvl"
    assert gear.mode_key("PvP") == "pvp" and gear.mode_key("raid") == "pve" and gear.mode_key("junk") == "lvl"


def test_polestar_settings_change_nothing(ref):
    """A sheet carrying an older shipper's Polestar gear settings (Adjust
    weights edits, Stat weights spec, smallest upgrade) scores exactly as one
    without: ForeverDB's weights only (owner, 2026-11-24)."""
    row = {"items": '[{"slot": 16, "item_id": 1317}]', "class": "PRIEST", "spec": "Shadow", "race": "Gnome",
           "faction": "Alliance"}
    old = {**row, "gear_adjust": '[{"spec": "shadow", "stat": "sta", "x": 3}]', "min_upgrade": "40",
           "polestar_spec": "holy"}
    a, b = (gear.advise(ref, gear.sheet(r), level=30) for r in (row, old))
    assert a["weights"] == b["weights"] and a["spec"] == b["spec"] and a["upgrades"] == b["upgrades"]
    assert a["spec"]["key"] == "shadow" and a["min_upgrade_pct"] == gear.MIN_UPGRADE
    assert "adjusted" not in a["spec"]
    # The class rules only: no Polestar weights in the gear data.
    assert set(ref.gear) == {"CLASSES", "PROFICIENCY"} and "weights" not in ref.data


def test_weights_file_check():
    """The committed weights file is what the export writes, and Leveling is
    its rule applied to PvE (offline)."""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, str(root / "tools" / "foreverdb_weights" / "export.py"), "--check"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
