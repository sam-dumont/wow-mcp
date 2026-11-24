"""wow-bags end to end: Syndicator's saved bags and bank, through the
shipper, the ingest screen (lists stored as JSON strings) and every reader
that lists items the character could get. The SavedVariables below are
shaped like the real ones (details, containerInfo, slots with itemLink and
itemCount, an unopened bank as an empty table); the items are made up for
two characters: a level 32 Gnome Priest in full gear (Testpriest's shape,
tests/test_gear.py) and a level 8 Dwarf Hunter with almost every slot
empty (Testhunter's shape)."""

import json

import pytest

from tests.test_gear import sheet as priest_sheet
from wowstats import gear, ingest, insights, redact
from wowstats.plan import estimate

VEST = 821  # Riverpaw Leather Vest: a vendor/world item, no source in Polestar's data
HEARTH = 6948
HUNTER = {"items": [{"slot": 4, "item_id": 148, "name": "Rugged Trapper's Shirt"},
                    {"slot": 7, "item_id": 147, "name": "Rugged Trapper's Pants"},
                    {"slot": 8, "item_id": 129, "name": "Rugged Trapper's Boots"},
                    {"slot": 16, "item_id": 37, "name": "Worn Axe"},
                    {"slot": 18, "item_id": 2508, "name": "Old Blunderbuss"}],
          "class": "HUNTER", "race": "Dwarf", "faction": "Alliance", "professions": []}


def _slot(item_id: int, count: int = 1) -> str:
    return (f'{{\n["itemCount"] = {count},\n["itemID"] = {item_id},\n["isBound"] = false,\n["hasLoot"] = false,\n'
            f'["iconTexture"] = 134414,\n["itemLink"] = "|cnIQ1:|Hitem:{item_id}::::::::8:1485:::::::::|h[x]|h|r",\n'
            '["quality"] = 1,\n}')


def _char(name: str, cls: str, bags: list[list[int]], bank: list[int]) -> str:
    def containers(cs):
        return "{\n" + ",\n".join("{\n" + ",\n".join(_slot(i) if i else "{\n}" for i in c) + ",\n}" for c in cs) \
            + ",\n}" if cs else "{\n}"
    return (f'["{name}-PvE"] = {{\n["containerInfo"] = {{\n["bags"] = {{\n{_slot(2102)},\n}},\n}},\n'
            f'["details"] = {{\n["class"] = 3,\n["realmNormalized"] = "PvE",\n["race"] = "Dwarf",\n'
            f'["faction"] = "Alliance",\n["sex"] = 2,\n["character"] = "{name}",\n["className"] = "{cls}",\n'
            f'["realm"] = "PvE",\n}},\n["bags"] = {containers(bags)},\n["bank"] = {containers([bank] if bank else [])},\n'
            '}')


@pytest.fixture
def stored(shipper, tmp_path):
    """The rows VictoriaLogs holds after the shipper's wow-bags went through
    /ingest, for both characters."""
    priest_up = _priest_upgrade()
    sv = tmp_path / "acct" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "Syndicator.lua").write_text(
        'SYNDICATOR_CONFIG = {\n["debug"] = false,\n}\nSYNDICATOR_DATA = {\n["Characters"] = {\n'
        + _char("Hunter Fixture", "HUNTER", [[HEARTH, 0, VEST], [0, 0]], [])
        + ",\n" + _char("Priest Fixture", "PRIEST", [[HEARTH, 0]], [priest_up["item_id"], 0])
        + ',\n},\n["Guilds"] = {\n},\n}\n')
    got: list[dict] = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    shipper.extract_inventory(tmp_path / "acct", {"Hunter-Fixture": {}, "Priest-Fixture": {}}, {}, out)
    lines, refused = ingest.parse("\n".join(json.dumps(e) for e in got).encode())
    assert refused == 0
    rows = [json.loads(x) for x in lines]
    assert {r["feed"] for r in rows} == {"wow-bags"} and all(isinstance(r["bags"], str) for r in rows)
    return {r["char"]: [r] for r in rows}, priest_up


def _priest_upgrade() -> dict:
    """The priest's best non-weapon upgrade that comes from a quest: held in
    the bank, that quest is no errand any more."""
    a = gear.advise(gear.reference(), priest_sheet(), level=32, every=True)
    return next(u for u in a["upgrades_all"] if u["slot"] not in (16, 17, 18)
                and any(s["kind"] == "quest" for s in u["sources"]))


def test_newest_bags_reads_the_stored_lists(stored):
    rows, up = stored
    assert gear.newest_bags(rows["Hunter-Fixture"])["bags"] == sorted([HEARTH, VEST])
    p = gear.newest_bags(rows["Priest-Fixture"])
    assert p["bags"] == [HEARTH] and p["bank"] == [up["item_id"]]
    assert gear.newest_bags([]) is None


def test_a_held_item_is_an_equip_away(stored):
    """Testhunter's shape: a vest no source lists, in the bags, fills his empty
    chest; the Hearthstone isn't gear and stays out."""
    rows, _ = stored
    a = gear.advise(gear.reference(), HUNTER, level=8, every=True, held=gear.newest_bags(rows["Hunter-Fixture"]))
    vest = next(u for u in a["upgrades"] if u["item_id"] == VEST)
    assert (vest["name"], vest["slot_name"], vest["fills_empty"], vest["now"]) == (
        "Riverpaw Leather Vest", "Chest", True, True)
    assert vest["sources"] == [{"kind": "bags"}] and vest["gates"] == [] and gear.is_held(vest)
    assert HEARTH not in {u["item_id"] for u in a["upgrades_all"]}
    # Without the bags it is only the drop ForeverDB saw in Forever (Riverpaw
    # Mongrels, no rate measured: hard), never "equip it".
    bare = next(u for u in gear.advise(gear.reference(), HUNTER, level=8, every=True)["upgrades_all"]
                if u["item_id"] == VEST)
    assert [s["kind"] for s in bare["sources"]] == ["drop"] and bare["effort"] == "hard" and not gear.is_held(bare)


def test_a_banked_quest_reward_loses_its_quest(stored):
    """Testpriest's shape: an upgrade a quest would give is in the bank; its
    only way is the bank, and it stays listed past the per-slot cap."""
    rows, up = stored
    a = gear.advise(gear.reference(), priest_sheet(), level=32, every=True,
                    held=gear.newest_bags(rows["Priest-Fixture"]))
    got = next(u for u in a["upgrades"] if u["item_id"] == up["item_id"])
    assert got["sources"] == [{"kind": "bank"}] and got["gain"] == up["gain"]


def test_the_overview_says_equip(stored):
    rows, _ = stored
    a = gear.advise(gear.reference(), HUNTER, level=8, held=gear.newest_bags(rows["Hunter-Fixture"]))
    moves = insights.next_moves(8, a, {}, {}, {}, [], {})["moves"]
    m = next(m for m in moves if m.get("item_id") == VEST)
    assert m["text"].startswith("Equip Riverpaw Leather Vest from your bags (") and "% on your chest" in m["text"]
    assert m["source"]["kind"] == "bags" and m["source"]["blocker"] is None


def test_the_plan_puts_the_held_one_first(stored):
    rows, _ = stored
    a = gear.advise(gear.reference(), HUNTER, level=8, every=True, held=gear.newest_bags(rows["Hunter-Fixture"]))
    opts = estimate.gear_options({"upgrades": a["upgrades_all"], "level": 8, "route_ahead": []}, {}, [], {})
    chest = next(s for s in opts if s["slot"] == "Chest")["options"]
    assert (chest[0]["item_id"], chest[0]["kind"], chest[0]["cost"]) == (VEST, "bags", "equip it")


def test_zogzog_keeps_where_it_is_public_has_no_advice(stored):
    """zogzog allows gear advice (the source's kind is on its list); the
    public tier has no gear answer at all."""
    rows, _ = stored
    a = gear.advise(gear.reference(), HUNTER, level=8, held=gear.newest_bags(rows["Hunter-Fixture"]))
    z = redact.zogzog("gear", {"char": "Hunter-Fixture", "available": True, **a})
    vest = next(u for u in z["upgrades"] if u["item_id"] == VEST)
    assert vest["sources"][0]["kind"] == "bags"
    with pytest.raises(KeyError):
        redact.public("gear", a)


def test_the_full_view_reads_them_from_the_store(shipper, tmp_path, monkeypatch, run_app):
    """Through the running app: the cached store (wowstats.logsql over real
    recorder events, tests/test_bounded_reads.py's fake VictoriaLogs), the
    Character page's advice, the Overview and the Dungeons panel. A dungeon
    drop that is an upgrade, put in the bags: the advice says equip it, the
    dungeon marks it as his."""
    from tests import test_bounded_reads as br
    from wowstats import eventcache, plan, stores, ui

    def app_with(rows):
        monkeypatch.setattr(stores, "http_get", br.FakeVL(rows))
        monkeypatch.setattr(eventcache, "_SHARED", {})
        monkeypatch.setattr(plan, "_CACHE", {})  # the planner's inputs, kept two minutes
        return run_app(ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": "http://vl.invalid",
                               "MODEL3D": "off", "DEFAULT_CHAR": br.CHAR, "HOME": str(tmp_path)}))

    def get(srv, path):
        code, _, body = srv.get(path)
        assert code == 200, (path, body[:200])
        return json.loads(body)

    rows = br.events()
    srv = app_with(rows)
    dungeons = get(srv, f"/api/dungeons?name={br.CHAR}")
    assert not dungeons["inventory_known"]
    before = {u["item_id"]: u for u in get(srv, f"/api/gear?name={br.CHAR}")["upgrades"]}
    pick = next(before[d["item_id"]] for b in dungeons["dungeons"] for d in b["drops"]
                if d["state"] == "missing" and d["now"] and d["item_id"] in before
                and before[d["item_id"]]["slot"] not in (16, 17, 18))
    srv.close()

    sv = tmp_path / "acct" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "Syndicator.lua").write_text('SYNDICATOR_DATA = {\n["Characters"] = {\n'
                                       + _char("Testpriest Fixture", "PRIEST", [[HEARTH, pick["item_id"]]], [])
                                       + ",\n},\n}\n")
    got: list[dict] = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    shipper.extract_inventory(tmp_path / "acct", {br.CHAR: {}}, {}, out)
    got[0]["_time"] = max(r["_time"] for r in rows)
    lines, _ = ingest.parse(json.dumps(got[0]).encode())
    srv = app_with(rows + [json.loads(lines[0])])

    after = get(srv, f"/api/gear?name={br.CHAR}")
    u = next(x for x in after["upgrades"] if x["item_id"] == pick["item_id"])
    assert u["sources"] == [{"kind": "bags"}] and u["gain"] == pick["gain"]
    moves = get(srv, f"/api/overview?name={br.CHAR}")
    assert all(m.get("source", {}).get("kind") != "drop" for m in moves.get("moves") or []
               if m.get("item_id") == pick["item_id"])
    dungeons = get(srv, f"/api/dungeons?name={br.CHAR}")
    assert dungeons["inventory_known"]
    held = [d for b in dungeons["dungeons"] for d in b["drops"] if d["item_id"] == pick["item_id"]]
    assert held and all(d["state"] == "obtained" and d["where"] == "bags" for d in held)
