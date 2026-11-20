"""Item facts from the player's own game client (shipper ship_item_facts,
wowstats.itemfacts, the /install/items routes): the hotfix cache reader on
made-up bytes (truncated, damaged, garbage), only the items the server lacks
leave the computer, everything the shipper sends is checked, the reference
always wins, and the dungeon loot path shows the facts. Every DBCache.bin and
client table here is written by the test: made-up ids, names, stats and
budgets, nothing from a real client."""

from __future__ import annotations

import copy
import gzip
import hashlib
import http.client
import json
import random
import struct
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from tests.test_assets import auth_of, several  # noqa: F401 - several: the friends' server fixture
from tests.test_shipper_casc import install as casc_install
from tests.test_tenancy import FRIEND
from tools.fixtures import golden3d
from wowstats import itemfacts, tenancy, ui
from wowstats.plan import dungeonview as V
from wowstats.plan import kbloot

PW = "itemfacts-password-long-enough"
BUILD = "1.60.1.70959"
AUTH = {"Authorization": ui._basic("wow", PW).decode()}
DATA = Path(__file__).resolve().parent / "data"

# Made-up items under the names the research notes give The Drowned City's
# drops (data/kb/research/dungeons.json): ids no client uses.
CROWN, ROBE, AXE, CLOAK, OTHER = 900609, 900611, 900598, 900613, 900700
REF_ITEM = 25  # in Polestar's gear data (Worn Shortsword): the reference has it
# stat id -> allocation (StatPercentEditor): 4 Strength, 5 Intellect, 7 Stamina, 45 spell power, 55 a resistance
ITEMS = {
    CROWN: dict(name="Serpentbane Crown", inv=1, cls=4, sub=3, q=3, ilvl=42, req=37, stats=[(55, 3000), (7, 6000)]),
    ROBE: dict(name="Robe of the Drowned", inv=20, cls=4, sub=1, q=3, ilvl=40, req=35,
               stats=[(5, 5000), (7, 6000), (45, 4000)]),
    AXE: dict(name="Bloodslash Axe", inv=13, cls=2, sub=0, q=3, ilvl=39, req=34, stats=[(4, 5000), (7, 5000)],
              delay=2500, icon=777001),
    CLOAK: dict(name="Priestess Cloak", inv=16, cls=4, sub=1, q=3, ilvl=40, req=35, stats=[(5, 7000), (45, 3000)]),
    REF_ITEM: dict(name="Worn Shortsword", inv=21, cls=2, sub=7, q=1, ilvl=2, req=1, stats=[]),
    OTHER: dict(name="A Made-up Potion", inv=0, cls=0, sub=1, q=1, ilvl=10, req=5, stats=[]),  # not gear: never sent
}
# RandPropPoints: item level -> Good, Superior, Epic, five slot groups each.
BUDGET = {lv: {"Good": [lv // 2 + 2, lv // 3 + 1, lv // 4 + 1, lv // 5 + 1, lv // 6 + 1],
               "Superior": [lv // 2 + 6, lv // 3 + 4, lv // 4 + 3, lv // 5 + 2, lv // 6 + 2],
               "Epic": [lv, lv // 2, lv // 3, lv // 4, lv // 5]} for lv in range(1, 81)}
QUERIED = 0x1000000


# ---------------------------------------------------------------- made-up DBCache.bin

def sparse_row(sh, name, it, **over) -> bytes:
    vals = {"ItemLevel": it["ilvl"], "RequiredLevel": it["req"], "InventoryType": it["inv"],
            "OverallQualityID": it["q"], "ItemDelay": it.get("delay", 0), "Bonding": 1, "SellPrice": 1234,
            "AllowableClass": -1, "MaxCount": 0,
            "StatModifier_bonusStat": [s for s, _ in it["stats"]] + [-1] * (10 - len(it["stats"])),
            "StatPercentEditor": [a for _, a in it["stats"]] + [0] * (10 - len(it["stats"])), **over}
    flat = []
    for fname, _code, n in sh._SPARSE_FIELDS:
        v = vals.get(fname, [0] * n if n > 1 else 0)
        flat.extend(v if n > 1 else [v])
    # Description, Display3, Display2, Display1, then the name (Display_lang).
    return b"\0\0\0\0" + name.encode() + b"\0" + sh._SPARSE_TAIL.pack(*flat)


def item_row(sh, it, **over) -> bytes:
    vals = {"ClassID": it["cls"], "SubclassID": it["sub"], "InventoryType": it["inv"],
            "IconFileDataID": it.get("icon", 0), **over}
    return sh._ITEM_ROW.pack(*(vals.get(f, 0) for f, _c, _n in sh._ITEM_FIELDS))


def entry(table: int, rid: int, data: bytes, push: int = 112720, status: int = 1) -> bytes:
    return b"XFTH" + struct.pack("<iiIIIiB3x", 93, push, push, table, rid, len(data), status) + data


def dbcache(sh, items=None, extra: list[bytes] = ()) -> bytes:
    out = [b"XFTH" + struct.pack("<II", 9, 70959) + bytes(32)]
    for rid, it in (items or ITEMS).items():
        out.append(entry(sh.HOTFIX_SPARSE, rid, sparse_row(sh, it["name"], it), push=QUERIED + rid))
        out.append(entry(sh.HOTFIX_ITEM, rid, item_row(sh, it), push=QUERIED + rid))
        out.append(entry(0x12345678, rid, b"another table's row"))
    return b"".join(out + list(extra))


# ---------------------------------------------------------------- the reader

def test_the_layouts_are_forevers(shipper):
    # The 1.60 rows' fixed part: 302 bytes after ItemSparse's five strings, 43 for Item.
    assert shipper._SPARSE_TAIL.size == 302 and shipper._ITEM_ROW.size == 43


def test_reads_weapons_and_armour_only(shipper):
    got, why = shipper.hotfix_items(dbcache(shipper))
    assert why == "" and set(got) == {CROWN, ROBE, AXE, CLOAK, REF_ITEM}  # the potion is not gear
    c = got[CROWN]
    assert {k: c[k] for k in ("name", "inv", "class", "sub", "q", "ilvl", "req", "stats", "bond", "sell")} == {
        "name": "Serpentbane Crown", "inv": 1, "class": 4, "sub": 3, "q": 3, "ilvl": 42, "req": 37,
        "stats": [[55, 3000], [7, 6000]], "bond": 1, "sell": 1234}
    assert got[AXE]["speed"] == 2500 and got[AXE]["icon"] == 777001 and got[AXE]["allow"] == -1
    assert c["h"] == shipper.facts_hash({k: v for k, v in c.items() if k != "h"})


def test_a_pushed_hotfix_beats_a_queried_row_and_the_newest_wins(shipper):
    it = ITEMS[ROBE]
    newer = {**it, "ilvl": 45}
    extra = [entry(shipper.HOTFIX_SPARSE, ROBE, sparse_row(shipper, it["name"], newer), push=100),
             entry(shipper.HOTFIX_SPARSE, ROBE, sparse_row(shipper, it["name"], {**it, "ilvl": 41}), push=90)]
    got, _ = shipper.hotfix_items(dbcache(shipper, extra=extra))
    assert got[ROBE]["ilvl"] == 45
    # The newest push removes the row: the item is gone.
    gone = [entry(shipper.HOTFIX_SPARSE, CROWN, b"", push=200, status=2)]
    got, _ = shipper.hotfix_items(dbcache(shipper, extra=gone))
    assert CROWN not in got and ROBE in got


def test_a_changed_layout_sends_nothing(shipper):
    rows = [entry(shipper.HOTFIX_SPARSE, 910000 + i, b"\0\0\0\0Made-up\0" + bytes(300)) for i in range(40)]
    got, why = shipper.hotfix_items(dbcache(shipper, extra=rows))
    assert got == {} and "layout" in why
    assert shipper.hotfix_items(b"XFTH" + struct.pack("<I", 8) + bytes(40)) == ({}, "hotfix cache version 8 isn't "
                                                                                      "one this shipper reads")


def test_fuzz_truncated_damaged_and_garbage_never_crash_or_crawl(shipper):
    good = dbcache(shipper)
    rnd = random.Random(1604)
    cases = [good[:n] for n in rnd.sample(range(len(good)), 150)] + [b"", b"XFTH", good[:44]]
    for _ in range(150):
        b = bytearray(good)
        for _ in range(rnd.randint(1, 20)):
            b[rnd.randrange(len(b))] = rnd.randrange(256)
        cases.append(bytes(b))
    cases += [rnd.randbytes(rnd.randint(1, 4096)) for _ in range(100)]
    cases += [b"XFTH" + struct.pack("<II", 9, 1) + bytes(32) + b"XFTH" + struct.pack("<iiIIIiB3x", 0, 1, 1,
                                                                                         shipper.HOTFIX_SPARSE, 1,
                                                                                         -5, 1)]
    t = time.perf_counter()
    for c in cases:
        got, why = shipper.hotfix_items(c)
        assert isinstance(got, dict) and isinstance(why, str)
        for f in got.values():
            assert f["class"] in (2, 4) and isinstance(f["name"], str) and len(f["stats"]) <= 10
    # A real cache is ~8 MB and reads in about 0.1 s; a big garbage file stops at its first bad entry.
    big = b"XFTH" + struct.pack("<II", 9, 1) + bytes(32) + rnd.randbytes(8 * 1024 * 1024)
    assert shipper.hotfix_items(big)[0] == {}
    assert time.perf_counter() - t < 5


def test_the_cache_path_on_windows_and_mac(shipper):
    win = shipper.dbcache_path(PureWindowsPath(r"C:\Program Files (x86)\World of Warcraft\_forever_"), "frFR")
    assert str(win) == r"C:\Program Files (x86)\World of Warcraft\_forever_\Cache\ADB\frFR\DBCache.bin"
    mac = shipper.dbcache_path(PurePosixPath("/Applications/World of Warcraft/_forever_"), "enUS")
    assert str(mac) == "/Applications/World of Warcraft/_forever_/Cache/ADB/enUS/DBCache.bin"


def test_a_cache_over_the_cap_is_not_read(shipper, tmp_path, monkeypatch):
    p = tmp_path / "Wörld of Warcraft" / "DBCache.bin"
    p.parent.mkdir()
    p.write_bytes(b"XFTH" + bytes(100))
    assert shipper.read_dbcache(p) == p.read_bytes()
    monkeypatch.setattr(shipper, "DBCACHE_MAX", 50)
    assert shipper.read_dbcache(p) is None
    assert shipper.read_dbcache(tmp_path / "missing.bin") is None


# ---------------------------------------------------------------- the server's checks

def facts_of(shipper, ids=(CROWN, ROBE, AXE, CLOAK)) -> dict:
    got, _ = shipper.hotfix_items(dbcache(shipper))
    return {i: got[i] for i in ids}


@pytest.mark.parametrize("change", [
    {"id": True}, {"id": "900609"}, {"id": 0}, {"q": 99}, {"class": 15}, {"class": 3}, {"ilvl": -1},
    {"name": ""}, {"name": "x" * 121}, {"name": "bad\nname"}, {"name": 5}, {"stats": [[7, 1]] * 11},
    {"stats": [[300, 1]]}, {"stats": [[7, "1"]]}, {"stats": [[7]]}, {"stats": "7:1"}, {"extra": 1}, {"h": -1},
])
def test_junk_from_a_shipper_is_refused(shipper, change):
    good = facts_of(shipper)[CROWN]
    assert itemfacts.valid(good)["name"] == "Serpentbane Crown"
    with pytest.raises(itemfacts.Refused):
        itemfacts.valid({**good, **change})
    bad = dict(good)
    del bad["ilvl"]
    with pytest.raises(itemfacts.Refused):
        itemfacts.valid(bad)


def budget_table() -> bytes:
    return golden3d.wdc5("RandPropPoints", BUDGET)


def test_only_the_missing_items_are_asked_for(shipper, tmp_path):
    st = itemfacts.Store(tmp_path)
    got, _ = shipper.hotfix_items(dbcache(shipper))
    pairs = [[i, f["h"]] for i, f in got.items()]
    need = st.need(BUILD, pairs, itemfacts.has_reference)
    # The reference has REF_ITEM: never asked for. The table: until stored for this build.
    assert need["want"] == sorted([CROWN, ROBE, AXE, CLOAK]) and need["tables"] == itemfacts.TABLES
    assert st.put_items([got[i] for i in need["want"]], itemfacts.has_reference) == (4, 0)
    st.put_table("RandPropPoints", BUILD, budget_table())
    assert st.need(BUILD, pairs, itemfacts.has_reference) == {"build": BUILD, "want": [], "tables": {},
                                                              "max_items": itemfacts.MAX_POST}
    # A hotfix changed one item: only that one; a new build: the table again.
    pairs = [[i, h + 1 if i == ROBE else h] for i, h in pairs]
    need = st.need("1.60.1.71037", pairs, itemfacts.has_reference)
    assert need["want"] == [ROBE] and need["tables"] == itemfacts.TABLES
    with pytest.raises(itemfacts.Refused):
        st.need(BUILD, [[1, 2, 3]], itemfacts.has_reference)
    with pytest.raises(itemfacts.Refused):
        st.need(BUILD, [[1, 1]] * (itemfacts.MAX_NEED + 1), itemfacts.has_reference)


def test_the_reference_wins(shipper, tmp_path):
    st = itemfacts.Store(tmp_path)
    got, _ = shipper.hotfix_items(dbcache(shipper))
    # Sent anyway (an older server's list): never kept.
    assert st.put_items([got[REF_ITEM], got[CROWN]], itemfacts.has_reference) == (1, 0)
    assert set(st.facts().items) == {str(CROWN)}
    # Read: Polestar's entry, even when the person's facts name the item too.
    facts = itemfacts.Facts({str(REF_ITEM): itemfacts.valid({**got[CROWN], "id": REF_ITEM})})
    with itemfacts.using(facts):
        assert kbloot.info_of(REF_ITEM)["subclass"] == 7  # the sword, not the made-up crown
        assert kbloot.info_of(CROWN) is None


def test_a_bad_budget_table_is_refused(tmp_path):
    st = itemfacts.Store(tmp_path)
    for name, data, status in (("ItemSparse", budget_table(), 404), ("RandPropPoints", b"nope", 415),
                               ("RandPropPoints", b"WDC5" + bytes(64), 400),
                               ("RandPropPoints", golden3d.wdc5("RandPropPoints", {5: {**BUDGET[5],
                                                                                    "Epic": [10**6] * 5}}), 400)):
        with pytest.raises(itemfacts.Refused) as e:
            st.put_table(name, BUILD, data)
        assert e.value.status == status
    assert st.facts().budget == {}


def test_allocations_become_the_tooltips_numbers(shipper, tmp_path):
    st = itemfacts.Store(tmp_path)
    st.put_items(list(facts_of(shipper).values()), itemfacts.has_reference)
    assert st.facts().info(CROWN)["stats"] == {}  # no budget yet: no numbers made up
    st.put_table("RandPropPoints", BUILD, budget_table())
    f = st.facts()
    # Head, rare: Superior[0] at 42 = 27; Stamina 6000 -> 16.2 -> 16; the resistance stays out.
    assert f.info(CROWN) == {"inv": 1, "ilvl": 42, "req": 37, "quality": 3, "class": 4, "subclass": 3,
                             "stats": {"sta": 16}}
    # Spell power counts for damage and healing, as Polestar's data has it.
    assert f.info(ROBE)["stats"] == {"int": 13, "sta": 16, "sp": 10, "heal": 10}
    assert f.info(AXE)["speed"] == 2500
    card = f.card(AXE, {777001: "inv_axe_made_up"}, {(2, 0): "Axe"})
    assert card["n"] == "Bloodslash Axe" and card["i"] == "inv_axe_made_up" and card["t"] == "Axe"
    assert card["w"] == ["+5 Strength", "+5 Stamina"] and card["il"] == 39 and card["s"] == "One-Hand"
    assert f.card(CROWN)["i"] == "inv_misc_questionmark"  # no icon of its own: the game's question mark


# ---------------------------------------------------------------- the routes and the shipper

@pytest.fixture
def one(run_app, tmp_path):
    app = ui.App({"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "none", "VL_URL": "http://vl.invalid",
                  "INGEST_PASSWORD": PW, "MCP_PUBLIC_URL": "", "MODEL3D_CACHE_DIR": str(tmp_path / "cache"),
                  "ITEM_FACTS_DIR": str(tmp_path / "items"), "WOW_ITEMS": "off"})
    srv = run_app(app)
    srv.app = app
    return srv


def post(srv, path, obj=None, raw=None, headers=None):
    body = raw if raw is not None else json.dumps(obj).encode()
    return srv.get(path, headers={**AUTH, **(headers or {})}, method="POST", data=body)


def test_routes(one, shipper):
    got = facts_of(shipper)
    pairs = [[i, f["h"]] for i, f in got.items()]
    assert one.get(f"/install/items/need?build={BUILD}", method="POST", data=b"{}")[0] == 401
    assert post(one, "/install/items/need?build=latest", {"items": pairs})[0] == 400
    assert one.get(f"/install/items/need?build={BUILD}", headers=AUTH)[0] == 404  # POST only
    code, _, body = post(one, f"/install/items/need?build={BUILD}", {"items": pairs},
                         headers={"Host": "any-name.example"})
    assert code == 200 and json.loads(body)["want"] == sorted(got)
    assert post(one, f"/install/items/need?build={BUILD}", raw=b"not json")[0] == 400
    # One junk item among good ones: left out, the rest kept.
    junk = {**got[CROWN], "id": 900999, "q": "epic"}
    code, _, body = post(one, f"/install/items/facts?build={BUILD}", {"items": [*got.values(), junk]})
    assert code == 200 and json.loads(body) == {"stored": 4, "refused": 1}
    assert post(one, f"/install/items/facts?build={BUILD}", {"items": [got[CROWN]] * 501})[0] == 400
    t = budget_table()
    assert post(one, f"/install/items/table/RandPropPoints?build={BUILD}", raw=t,
                headers={"X-Content-SHA256": "0" * 64})[0] == 400
    assert post(one, f"/install/items/table/RandPropPoints?build={BUILD}", raw=t,
                headers={"X-Content-SHA256": hashlib.sha256(t).hexdigest()})[0] == 200
    c = http.client.HTTPConnection(one.base.split("//", 1)[1], timeout=10)
    c.request("POST", f"/install/items/facts?build={BUILD}",
              headers={**AUTH, "Content-Length": str(itemfacts.MAX_BODY + 1)})
    assert c.getresponse().status == 413
    c.close()
    f = one.app.item_facts.facts()
    assert f.info(CROWN)["stats"] == {"sta": 16} and f.by_name("serpentbane crown") == CROWN
    # The page's tooltips: the facts fill what the catalog doesn't have.
    code, _, body = one.get(f"/api/items?ids={CROWN},{AXE}")
    cards = {c["id"]: c for c in json.loads(body)["items"]}
    assert cards[CROWN]["n"] == "Serpentbane Crown" and cards[CROWN]["w"] == ["+16 Stamina"]


def test_the_catalog_card_wins(one, shipper, monkeypatch):
    from wowstats import itemcard

    one.app.item_facts.store.put_items(list(facts_of(shipper).values()), itemfacts.has_reference)

    class Cat:
        icon_files, subclasses = {}, {}

        def many(self, ids):
            return {str(AXE): {"n": "Catalog Axe", "w": ["+1 Strength"]}, str(CROWN): {"n": "Catalog Crown"}}

    monkeypatch.setattr(itemcard, "shared", lambda: Cat())
    cards = one.app.item_cards([AXE, CROWN, ROBE])
    assert cards[str(AXE)] == {"n": "Catalog Axe", "w": ["+1 Strength"]}  # has stats: untouched
    assert cards[str(CROWN)]["n"] == "Catalog Crown" and cards[str(CROWN)]["il"] == 42  # gaps filled, name kept
    assert cards[str(ROBE)]["n"] == "Robe of the Drowned"


def write_cache(shipper, root: Path, items=None) -> Path:
    p = shipper.dbcache_path(root / "_forever_", "enUS")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(dbcache(shipper, items))
    return p


def test_the_shipper_sends_only_what_the_server_lacks(one, shipper, tmp_path, monkeypatch):
    root = casc_install(tmp_path, shipper,
                        extra=lambda inst: inst.file(itemfacts.TABLES["RandPropPoints"], budget_table()))
    cache = write_cache(shipper, root)
    monkeypatch.setattr(shipper, "WOW", root / "_forever_")
    monkeypatch.setattr(shipper, "LOG", tmp_path / "wow-stats.log")
    monkeypatch.setattr(shipper, "DRY", False)
    monkeypatch.setattr(shipper, "CONFIG", {"ship": {"url": one.base + "/ingest", "user": "wow", "password": PW}})
    sent: list[str] = []
    real = shipper._assets_call
    monkeypatch.setattr(shipper, "_assets_call", lambda url, h, body=None: (sent.append(url), real(url, h, body))[1])
    state: dict = {}
    assert shipper.ship_item_facts(state, now=1000) == "sent"
    f = one.app.item_facts.facts()
    assert set(f.items) == {str(i) for i in (CROWN, ROBE, AXE, CLOAK)}  # not the reference's, not the potion
    assert f.budget["42"] == BUDGET[42]["Good"] + BUDGET[42]["Superior"] + BUDGET[42]["Epic"]
    assert [u.split("?")[0].rsplit("/", 1)[-1] for u in sent] == ["need", "RandPropPoints", "facts"]
    # Unchanged file: nothing until the daily recheck, which sends nothing new.
    assert shipper.ship_item_facts(state, now=2000) is None
    assert shipper.ship_item_facts(state, now=1000 + shipper.ITEMS_RECHECK_S) == "nothing new"
    # A hotfix changes one item: that one goes, alone.
    changed = {**ITEMS, ROBE: {**ITEMS[ROBE], "ilvl": 44}}
    cache.write_bytes(dbcache(shipper, changed))
    sent.clear()
    assert shipper.ship_item_facts(state, now=1000 + shipper.ITEMS_RECHECK_S + shipper.ITEMS_RETRY_S) == "sent"
    assert one.app.item_facts.facts().get(ROBE)["ilvl"] == 44
    assert [u.split("?")[0].rsplit("/", 1)[-1] for u in sent] == ["need", "facts"]
    # Off in the config, or no cache yet: nothing read, nothing sent.
    monkeypatch.setattr(shipper, "CONFIG", {**shipper.CONFIG, "item_facts": False})
    assert shipper.ship_item_facts({}, now=0) is None
    monkeypatch.setattr(shipper, "CONFIG", {**shipper.CONFIG, "item_facts": True})
    cache.unlink()
    assert shipper.ship_item_facts({}, now=0) is None


def test_a_server_without_item_facts(run_app, shipper, tmp_path, monkeypatch):
    srv = run_app(ui.App({"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "none", "VL_URL": "http://vl.invalid",
                          "MCP_PUBLIC_URL": "", "MODEL3D_CACHE_DIR": str(tmp_path / "cache")}))  # no upload password
    root = casc_install(tmp_path, shipper)
    write_cache(shipper, root)
    monkeypatch.setattr(shipper, "WOW", root / "_forever_")
    monkeypatch.setattr(shipper, "LOG", tmp_path / "wow-stats.log")
    monkeypatch.setattr(shipper, "CONFIG", {"ship": {"url": srv.base + "/ingest", "user": "wow", "password": PW}})
    state: dict = {}
    assert shipper.ship_item_facts(state, now=1000) == "no server"
    assert shipper.ship_item_facts(state, now=1000 + shipper.ITEMS_RETRY_S) is None  # not again before a day


def test_each_person_their_own_facts(several, shipper):  # noqa: F811
    t = several.t
    mira, pip = t.registry.get(FRIEND), t.registry.get("friend-3")
    a_mira, a_pip = auth_of(several, FRIEND, "mira"), auth_of(several, "friend-3", "pip")
    body = json.dumps({"items": list(facts_of(shipper).values())}).encode()
    h = {"Content-Type": "application/json"}
    assert several.get(f"/install/items/facts?build={BUILD}", headers={**a_mira, **h}, method="POST",
                       data=body)[0] == 200
    assert (t.user_dir(mira) / "items" / "facts.json").is_file()
    assert t.app_for(mira).item_facts.facts().by_name("Serpentbane Crown") == CROWN
    assert t.app_for(pip).item_facts.facts().by_name("Serpentbane Crown") is None
    # Pip's need list still asks for everything: Mira's facts are hers.
    pairs = json.dumps({"items": [[CROWN, 1]]}).encode()
    code, _, out = several.get(f"/install/items/need?build={BUILD}", headers=a_pip, method="POST", data=pairs)
    assert code == 200 and json.loads(out)["want"] == [CROWN]
    # The day's budget is /ingest's (3000 bytes in this server): the fourth post of these waits for tomorrow.
    codes = [several.get(f"/install/items/facts?build={BUILD}", headers=a_pip, method="POST", data=body)[0]
             for _ in range(4)]
    assert len(body) * 3 < 3000 < len(body) * 4 and codes == [200, 200, 200, 429]
    tenancy.delete_tenant(t, mira)
    assert not (t.user_dir(mira) / "items").exists()


# ---------------------------------------------------------------- the dungeon loot path

@pytest.fixture(scope="module")
def priest():
    return json.loads(gzip.open(DATA / "plan-inputs.json.gz").read())


def priest_facts(shipper, tmp_path) -> itemfacts.Facts:
    st = itemfacts.Store(tmp_path / "priest")
    st.put_items(list(facts_of(shipper).values()), itemfacts.has_reference)
    st.put_table("RandPropPoints", BUILD, budget_table())
    return st.facts()


def test_the_loot_view_shows_the_facts(priest, shipper, tmp_path):
    with itemfacts.using(priest_facts(shipper, tmp_path)):
        b = {x["name"]: x for x in V.build(copy.deepcopy(priest))["dungeons"]}["The Drowned City"]
    kbd = {d["name"]: d for d in b["kb_drops"]}
    crown, robe, axe = kbd["Serpentbane Crown"], kbd["Robe of the Drowned"], kbd["Bloodslash Axe"]
    assert crown["item_id"] == CROWN and crown["item_level"] == 42 and crown["slot_name"] == "Head"
    assert crown["stats"] == ["+16 Stamina"] and crown["armour"] == "mail"
    assert robe["stats"] == ["+16 Stamina", "+13 Intellect", "+10 Spell Power"]  # the tooltip's order
    # The spec check on the stats: a shadow priest at 32 wears cloth and casts.
    assert robe["fit"] is True and kbd["Priestess Cloak"]["fit"] is True
    assert crown["fit"] is False  # mail
    assert axe["fit"] is False  # a priest can't use an axe, whatever the note says
    # Items no facts cover keep the note's words, and its armour.
    assert kbd["Viscera Spaulders"]["item_id"] is None and "stats" not in kbd["Viscera Spaulders"]
    # The sealed page keeps the facts (wowstats.redact).
    from wowstats import redact

    z = redact.zogzog("dungeons", {"dungeons": [b]})["dungeons"][0]
    zc = next(d for d in z["kb_drops"] if d["name"] == "Serpentbane Crown")
    assert zc["stats"] == ["+16 Stamina"] and zc["item_level"] == 42


def test_without_facts_the_notes_alone(priest):
    b = {x["name"]: x for x in V.build(copy.deepcopy(priest))["dungeons"]}["The Drowned City"]
    kbd = {d["name"]: d for d in b["kb_drops"]}
    assert kbd["Serpentbane Crown"]["item_id"] is None and "stats" not in kbd["Serpentbane Crown"]


def test_each_answer_reads_its_own_persons_facts(shipper, tmp_path):
    mine = priest_facts(shipper, tmp_path)
    assert itemfacts.current() is None
    with itemfacts.using(mine):
        assert kbloot.item_id("Serpentbane Crown") == CROWN
        with itemfacts.using(itemfacts.Facts()):
            assert kbloot.item_id("Serpentbane Crown") is None
    assert itemfacts.current() is None
