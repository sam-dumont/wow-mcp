"""Item counts for the bags and the bank, end to end: Syndicator's slots
summed by the shipper (`bags_n`, `bank_n`), stored through /ingest, read
per part by gear.newest_bags (each part's counts from the event its list
came from), answered by the MCP's inventory and kept in the prep's
inventory table. Made-up characters and stacks."""

import json
from datetime import UTC, datetime

import pytest

from tests.mcp import fakes
from wowstats import gear, ingest
from wowstats.analysis import prep
from wowstats.mcp import tools
from wowstats.mcp.store import Store

HEARTH, LINEN, SILK = 6948, 2589, 4306
CHAR = "Hunter-Fixture"


def _slot(item_id: int, count: int | None) -> str:
    n = f'["itemCount"] = {count},\n' if count is not None else ""
    return f'{{\n{n}["itemID"] = {item_id},\n["quality"] = 1,\n}}'


def _containers(cs: list[list[tuple[int, int | None] | None]]) -> str:
    return "{\n" + ",\n".join("{\n" + ",\n".join(_slot(*s) if s else "{\n}" for s in c) + ",\n}" for c in cs) + ",\n}"


EMPTY = "{\n}"  # an unopened bank: an empty table


def _syndicator(tmp_path, bags, bank) -> object:
    sv = tmp_path / "acct" / "SavedVariables"
    sv.mkdir(parents=True, exist_ok=True)
    (sv / "Syndicator.lua").write_text(
        'SYNDICATOR_DATA = {\n["Characters"] = {\n["Hunter Fixture-PvE"] = {\n'
        '["details"] = {\n["character"] = "Hunter Fixture",\n["realm"] = "PvE",\n},\n'
        f'["bags"] = {_containers(bags)},\n["bank"] = {_containers(bank) if bank else EMPTY},\n'
        '},\n},\n}\n')
    return tmp_path / "acct"


def _ship(shipper, acct, state=None) -> list[dict]:
    got: list[dict] = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    shipper.extract_inventory(acct, {CHAR: {}}, state if state is not None else {}, out)
    return got


# ---------------------------------------------------------------- the shipper


def test_the_shipper_sums_stacks_and_sends_ids_and_counts(shipper, tmp_path):
    # Linen in two stacks across two bags, a slot with no count (one), the bank's silk in two stacks.
    acct = _syndicator(tmp_path, [[(LINEN, 20), (HEARTH, 1), None], [(LINEN, 7), (SILK, None)]],
                       [[(SILK, 10), (SILK, 4)]])
    (ev,) = _ship(shipper, acct)
    assert ev["bags"] == [LINEN, SILK, HEARTH] and ev["bank"] == [SILK]
    assert ev["bags_n"] == [[LINEN, 27], [SILK, 1], [HEARTH, 1]]
    assert ev["bank_n"] == [[SILK, 14]]


def test_a_bank_never_opened_sends_no_bank_and_no_bank_counts(shipper, tmp_path):
    (ev,) = _ship(shipper, _syndicator(tmp_path, [[(LINEN, 3)]], []))
    assert ev["bags_n"] == [[LINEN, 3]]
    # None: Out.emit leaves the field out of the line it writes.
    assert ev["bank"] is None and ev["bank_n"] is None


def test_a_change_of_count_alone_sends_a_new_event(shipper, tmp_path):
    state: dict = {}
    assert len(_ship(shipper, _syndicator(tmp_path, [[(LINEN, 3)]], []), state)) == 1
    assert _ship(shipper, _syndicator(tmp_path, [[(LINEN, 3)]], []), state) == []  # nothing moved
    (ev,) = _ship(shipper, _syndicator(tmp_path, [[(LINEN, 9)]], []), state)  # same ids, new count
    assert ev["bags"] == [LINEN] and ev["bags_n"] == [[LINEN, 9]]


def test_counts_survive_ingest(shipper, tmp_path):
    evs = _ship(shipper, _syndicator(tmp_path, [[(LINEN, 20)]], [[(SILK, 5)]]))
    lines, refused = ingest.parse("\n".join(json.dumps(e) for e in evs).encode())
    assert refused == 0
    (row,) = [json.loads(x) for x in lines]
    assert isinstance(row["bags_n"], str)  # stored as JSON, like the ID lists
    held = gear.newest_bags([row])
    assert held["counts"] == {"bags": {LINEN: 20}, "bank": {SILK: 5}}


# ---------------------------------------------------------------- the read


def _ev(t: str, **parts) -> dict:
    return {"_time": t, "feed": "wow-bags", "char": CHAR, "source_id": f"bags:{CHAR}:{t}",
            **{k: json.dumps(v) for k, v in parts.items()}}


OLD = "2026-11-10T20:00:00Z"
NEW = "2026-11-20T20:00:00Z"


def test_each_part_counts_come_from_the_event_its_list_came_from():
    rows = [_ev(OLD, bags=[LINEN], bags_n=[[LINEN, 2]], bank=[SILK], bank_n=[[SILK, 8]]),
            _ev(NEW, bags=[LINEN, HEARTH], bags_n=[[LINEN, 40], [HEARTH, 1]])]  # no bank this time
    held = gear.newest_bags(rows)
    assert held["bags"] == [LINEN, HEARTH] and held["bank"] == [SILK]
    assert held["counts"] == {"bags": {LINEN: 40, HEARTH: 1}, "bank": {SILK: 8}}
    assert held["at"] == {"bags": NEW, "bank": OLD}


def test_an_event_without_counts_leaves_them_unknown_never_zero():
    # The bank from an older shipper (IDs only), the bags from a newer one.
    held = gear.newest_bags([_ev(OLD, bags=[LINEN], bank=[SILK]),
                             _ev(NEW, bags=[LINEN], bags_n=[[LINEN, 5]])])
    assert held["counts"] == {"bags": {LINEN: 5}, "bank": None}
    # And the other way: counts sent once, then an older shipper's IDs win the bags.
    held = gear.newest_bags([_ev(OLD, bags=[LINEN], bags_n=[[LINEN, 5]]), _ev(NEW, bags=[SILK])])
    assert held["bags"] == [SILK] and held["counts"] == {"bags": None, "bank": None}


def test_held_items_rows():
    held = gear.newest_bags([_ev(OLD, bags=[LINEN], bags_n=[[LINEN, 2]], bank=[SILK, LINEN],
                                 bank_n=[[SILK, 8], [LINEN, 3]])])
    rows = {r["item_id"]: r for r in gear.held_items(held)}
    assert rows[LINEN] == {"item_id": LINEN, "name": "Linen Cloth", "bags": 2, "bank": 3, "total": 5}
    assert rows[SILK]["bags"] == 0 and rows[SILK]["total"] == 8
    no_counts = gear.held_items(gear.newest_bags([_ev(OLD, bags=[LINEN])]))
    assert no_counts == [{"item_id": LINEN, "name": "Linen Cloth", "bags": None, "bank": None, "total": None}]


# ---------------------------------------------------------------- the MCP tool


def test_the_tool_answers_counts_and_says_when_the_bank_is_old():
    held = gear.newest_bags([_ev(OLD, bank=[SILK], bank_n=[[SILK, 8]]),
                             _ev(NEW, bags=[LINEN, HEARTH], bags_n=[[LINEN, 40], [HEARTH, 1]])])
    out = tools.inventory_answer(held, now=datetime(2026, 11, 21, tzinfo=UTC))
    assert out["counts_known"] is True and "unlock" not in out
    assert [(r["name"], r["bags"], r["bank"], r["total"]) for r in out["items"]] == [
        ("Hearthstone", 1, 0, 1), ("Linen Cloth", 40, 0, 40), ("Silk Cloth", 0, 8, 8)]
    assert out["bags_at"].startswith("2026-11-20") and out["bank_at"].startswith("2026-11-10")
    assert out["note"] == "the bank as of 2026-11-10: open it in game to refresh"
    # A fresh bank: no note. A filter by name or by id.
    assert tools.inventory_answer(held, now=datetime(2026, 11, 12, tzinfo=UTC))["note"] is None
    assert [r["item_id"] for r in tools.inventory_answer(held, "cloth")["items"]] == [LINEN, SILK]
    assert [r["item_id"] for r in tools.inventory_answer(held, str(SILK))["items"]] == [SILK]


def test_the_tool_without_counts_says_how_to_get_them():
    out = tools.inventory_answer(gear.newest_bags([_ev(NEW, bags=[LINEN])]), now=datetime(2026, 11, 21, tzinfo=UTC))
    assert out["counts_known"] is False and "newer shipper" in out["unlock"]
    assert out["items"][0]["bags"] is None and "bank not seen" in out["note"]
    assert tools.inventory_answer(None)["unlock"] == gear.SHEET_UNLOCK


@pytest.mark.parametrize("bags_rows, known", [
    (fakes.BAGS, False),  # the fakes' event: IDs only, an older shipper
    ([{**fakes.BAGS[0], "bags_n": json.dumps([[4768, 1], [6948, 1]]), "bank_n": json.dumps([[3645, 1]])}], True),
])
def test_the_tool_through_the_store(bags_rows, known):
    # The tools read the character's events from the event cache (fakes.RAW): the bags swapped there.
    raw = [r for r in fakes.RAW if r.get("feed") != "wow-bags"] + [{**r, "char": fakes.CHAR} for r in bags_rows]
    fake = fakes.FakeStores(logs=[('feed:="wow-bags"', bags_rows)] + fakes.LOGS, raw=raw)
    try:
        ctx = tools.Ctx(store=Store(vl_url=fake.url, vm_url=fake.url))
        out = tools.call(ctx, "inventory", {"char": fakes.CHAR})
    finally:
        fake.close()
    assert out["counts_known"] is known
    assert {r["item_id"] for r in out["items"]} == {4768, 6948, 3645}
    assert all((r["total"] == 1) is known for r in out["items"])


# ---------------------------------------------------------------- the prep table


def test_the_prep_inventory_table():
    rows = [_ev(OLD, bags=[LINEN], bags_n=[[LINEN, 2]], bank=[SILK], bank_n=[[SILK, 8]]),
            _ev(NEW, bags=[LINEN, HEARTH], bags_n=[[LINEN, 40], [HEARTH, 1]])]
    db = prep.build(rows, CHAR, None)
    got = db.execute("SELECT item_id, name, bags, bank, total, bags_at, bank_at FROM inventory ORDER BY item_id")
    t_old, t_new = (datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() for t in (OLD, NEW))
    assert got.fetchall() == [(LINEN, "Linen Cloth", 40, 0, 40, t_new, t_old), (SILK, "Silk Cloth", 0, 8, 8, t_new, t_old),
                              (HEARTH, "Hearthstone", 1, 0, 1, t_new, t_old)]
    assert prep.build([], CHAR, None).execute("SELECT COUNT(*) FROM inventory").fetchone() == (0,)
