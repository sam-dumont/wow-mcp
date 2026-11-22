"""Evidence properties of the snapshot sources (properties 13 to 17).

A snapshot says what was true when a source looked: the gear sheet
(Syndicator through the shipper's wow-gear), the bags and bank (wow-bags),
Questie's completed quests (wow-quests), Polestar's logout sheet
(wow-character) and Battle.net's read (the exporter's parts in vm-wow).
Snapshots come again (a resend, `--replay`, a run after a lost
state.json), arrive in any order, can be approximate (`at_basis`, #75) or
partial. For each source, through the code the page, the MCP and the
planner read it with:

- (a) a snapshot sent again keeps its observation time: the copy that
  comes later doesn't make it look newly observed;
- (b) an approximate snapshot (`at_basis`: the archive's commit time, or
  the run's own clock when the file had none) received later doesn't
  replace one with an exact observation time;
- (c) two conflicting snapshots on the same time give the same answer in
  either arrival order;
- (d) a snapshot that lacks a part (the bank, the equipped items, the
  money) doesn't erase that part from an earlier complete one;
- (e) what no snapshot says stays unknown (None), never 0 or empty.

Made-up characters and items only. Borrowed from WoW4EMCP's tested
behaviours; builds on tests/test_quests.py and tests/test_bnet_freshness.py.
"""

from __future__ import annotations

import contextlib
import io
import json
from datetime import UTC, datetime

import pytest

from wowstats import gear, logsql, quests, stores
from wowstats.stores import Store

CHAR = "Hero-Alpha"
CF = f'char:="{CHAR}"'
T1, T2, T3 = 1794900000, 1794990000, 1795080000  # 2026-11-17..19
REWARD, REWARD_QUEST = 2041, 166  # an item only that quest gives (wowstats.quests.reward_quest)


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def lq(q: str, rows: list[dict]) -> list[dict]:
    """A LogsQL query over the stored rows, as VictoriaLogs answers it."""
    return logsql.parse(q).run([dict(r) for r in rows])


def gear_ev(t: float, items: list[int] | None, sid: str | None = None, **kw) -> dict:
    ev = {"_time": iso(t), "feed": "wow-gear", "char": CHAR, "class": "PRIEST",
          "source_id": sid or f"gear:{CHAR}:{items}:{int(t)}", **kw}
    if items is not None:
        ev["items"] = json.dumps([{"slot": 4, "slot_name": "Chest", "item_id": i} for i in items])
    return ev


def bags_ev(t: float, bags: list[int] | None, bank: list[int] | None, sid: str | None = None, **kw) -> dict:
    ev = {"_time": iso(t), "feed": "wow-bags", "char": CHAR, "source_id": sid or f"bags:{CHAR}:{bags}{bank}", **kw}
    if bags is not None:
        ev["bags"] = json.dumps(bags)
    if bank is not None:
        ev["bank"] = json.dumps(bank)
    return ev


def quest_ev(t: float, ids: list[int], **kw) -> dict:
    return {"_time": iso(t), "feed": "wow-quests", "kind": "completed", "char": CHAR, "source": "questie",
            "source_id": f"quests:{CHAR}:questie:{ids}", "quest_ids": json.dumps(ids), "n": len(ids), **kw}


def sheet_ev(t: float, level: int, money: int | None, sid: str | None = None, **kw) -> dict:
    ev = {"_time": iso(t), "feed": "wow-character", "char": CHAR, "level": level,
          "source_id": sid or f"character:{CHAR}:{int(t)}", **kw}
    if money is not None:
        ev["money_copper"] = money
    return ev


# ---------------------------------------------------------------- the readers
# What the page and the MCP read (their LogsQL, then the pick), and the
# planner (every event of the character, picked in Python).


def gear_now(rows: list[dict]) -> tuple:
    """(the sheet's time, the item ids it wears), both read paths agreeing."""
    page = gear.pick_sheet(gear.sheet_rows(lambda q: lq(q, rows), CF))
    plan = gear.pick_sheet([r for r in stores.dedupe(rows) if r.get("feed") == "wow-gear"])
    got = []
    for row in (page, plan):
        got.append((row.get("_time"), sorted(i["item_id"] for i in json.loads(row.get("items") or "[]")))
                   if row else None)
    assert got[0] == got[1], f"the page and the planner read different sheets: {got}"
    return got[0]


def bags_now(rows: list[dict]) -> tuple:
    page = gear.newest_bags(gear.held_rows(lambda q: lq(q, rows), CF))
    plan = gear.newest_bags(stores.dedupe(rows))
    assert page == plan, f"the page and the planner read different bags: {page} {plan}"
    return None if page is None else (page["t"], page["bags"], page["bank"])


def quests_now(rows: list[dict]) -> dict:
    """quest -> (first seen, by then?) as the Gear page and the planner say."""
    return {q: (e["t"], e["t_by"]) for q, e in quests.explain(stores.dedupe(rows)).items()
            if e["from"] != "inferred"}  # a prerequisite of one, with no time of its own


def sheet_now(rows: list[dict]) -> tuple:
    """The hero line's level and money (views.build_header's pick), and the
    MCP's character_summary's: the same rows, the same pick."""
    snap = stores.pick_sheet(stores.sheet_rows(lambda q: lq(q, rows), CF))
    return snap.get("_time"), snap.get("level"), snap.get("money_copper")


# ---------------------------------------------------------------- (a) a replay keeps its time


def test_a_gear_sent_again_keeps_its_time():
    a, b = gear_ev(T1, [101]), gear_ev(T2, [202])
    assert gear_now([a, b, dict(a)]) == gear_now([a, b]) == (iso(T2), [202])


def test_a_bags_seen_again_keep_their_first_time():
    """The same bags read again after a lost state.json (the game rewrote
    Syndicator.lua at logout, so the file is newer, the content the same):
    the planner's quest evidence keeps when the reward was first seen."""
    first, again = bags_ev(T1, [REWARD], []), bags_ev(T3, [REWARD], [])
    assert quests_now([first, again]) == quests_now([first]) == {REWARD_QUEST: (T1, False)}
    assert bags_now([first, again]) == (T3, [REWARD], [])


def test_a_quests_seen_again_keep_their_first_time():
    first, again = quest_ev(T1, [7, 8]), quest_ev(T3, [7, 8])
    assert quests_now([first, again]) == quests_now([first]) == {7: (T1, False), 8: (T1, False)}


def test_a_sheet_sent_again_keeps_its_time():
    a, b = sheet_ev(T1, 20, 500), sheet_ev(T2, 21, 900)
    assert sheet_now([a, b, dict(a)]) == (iso(T2), 21, 900)


# ---------------------------------------------------------------- (b) approximate doesn't beat exact


def test_b_an_approximate_gear_sheet_doesnt_replace_an_exact_one():
    exact, approx = gear_ev(T2, [202]), gear_ev(T3, [101], at_basis="commit")
    assert gear_now([exact, approx]) == (iso(T2), [202])
    assert gear_now([approx]) == (iso(T3), [101])  # the only one: shown, by its upper bound


def test_b_approximate_bags_dont_replace_exact_ones():
    exact, approx = bags_ev(T2, [1], [2]), bags_ev(T3, [9], [9], at_basis="run")
    assert bags_now([exact, approx]) == (T2, [1], [2])


def test_b_an_approximate_quest_snapshot_keeps_the_exact_time():
    """The Questie set read exactly at T1; an archive's copy dated by its
    commit (an upper bound) at T3: first seen stays T1, exact."""
    exact, approx = quest_ev(T1, [7]), quest_ev(T3, [7], at_basis="commit")
    approx["source_id"] += ":old"
    assert quests_now([approx, exact]) == quests_now([exact, approx]) == {7: (T1, False)}


def test_b_an_approximate_sheet_doesnt_replace_an_exact_one():
    exact, approx = sheet_ev(T2, 21, 900), sheet_ev(T3, 19, 100, at_basis="run")
    assert sheet_now([exact, approx]) == (iso(T2), 21, 900)


# ---------------------------------------------------------------- (c) arrival order


@pytest.mark.parametrize("source", ["gear", "bags", "sheet"])
def test_c_two_conflicting_snapshots_on_one_time_either_order(source):
    a, b = {
        "gear": (gear_ev(T2, [101]), gear_ev(T2, [202])),
        "bags": (bags_ev(T2, [1], [2]), bags_ev(T2, [3], [4])),
        "sheet": (sheet_ev(T2, 21, 900, sid=f"character:{CHAR}:a"), sheet_ev(T2, 21, 950, sid=f"character:{CHAR}:b")),
    }[source]
    now = {"gear": gear_now, "bags": bags_now, "sheet": sheet_now}[source]
    assert now([a, b]) == now([b, a])


def test_c_quests_either_order():
    a, b = quest_ev(T1, [7, 8]), quest_ev(T2, [7, 9])
    assert quests_now([a, b]) == quests_now([b, a]) == {7: (T1, False), 8: (T1, False), 9: (T2, False)}


# ---------------------------------------------------------------- (d) partial doesn't erase


def test_d_bags_without_the_bank_keep_the_earlier_bank():
    """Syndicator lost its bank cache (a reinstall): the newer event has
    the bags only. The bank from the earlier complete read stays."""
    full, part = bags_ev(T1, [1], [2, 3]), bags_ev(T2, [4], None)
    assert bags_now([full, part]) == (T2, [4], [2, 3])


def test_d_a_sheet_without_items_keeps_the_earlier_items():
    """A sheet with the professions only (Syndicator didn't know the
    character on that run) doesn't erase what it wore before."""
    full, part = gear_ev(T1, [101]), gear_ev(T2, None, professions=json.dumps([{"skill_id": 182, "rank": 40}]))
    assert gear_now([full, part]) == (iso(T2), [101])


def test_d_a_smaller_quest_set_doesnt_erase_the_earlier_one():
    full, part = quest_ev(T1, [7, 8, 9]), quest_ev(T2, [9])
    assert set(quests_now([full, part])) == {7, 8, 9}


def test_d_a_sheet_without_money_keeps_the_earlier_money():
    full, part = sheet_ev(T1, 20, 500), sheet_ev(T2, 21, None)
    assert sheet_now([full, part]) == (iso(T2), 21, 500)


# ---------------------------------------------------------------- (e) unknown stays unknown


def test_e_bags_never_read_stay_unknown():
    assert bags_now([]) is None
    assert bags_now([bags_ev(T1, [1], None)]) == (T1, [1], None), "a bank never seen is unknown, not empty"


def test_e_gear_never_read_stays_unknown():
    assert gear_now([]) is None
    assert gear_now([gear_ev(T1, None)]) == (iso(T1), []) and not gear.sheet_has_items(gear_ev(T1, None))


def test_e_quests_no_snapshot_no_answer():
    assert quests_now([]) == {}


def test_e_money_never_read_stays_unknown():
    assert sheet_now([sheet_ev(T1, 20, None)]) == (iso(T1), 20, None)


# ---------------------------------------------------------------- Battle.net


ACCT = "0"


def _bnet(snaps: list[tuple[str, float, float | None]]):
    """A vm-wow answering the exporter's marker and refreshed series:
    (snapshot, marker value, refreshed time or None: that read was cut)."""
    def query(q: str) -> list[dict]:
        out = []
        for sid, since, refreshed in snaps:
            m = {"account_id": ACCT, "character": CHAR, "snapshot": sid}
            if "wow_character_snapshot" in q:
                out.append({"metric": m, "value": [0, str(since)]})
            elif "refreshed" in q and refreshed is not None:
                out.append({"metric": m, "value": [0, str(refreshed)]})
        return out
    st = Store("http://vl.invalid", profile_vm_url="http://vm.invalid")
    st.profile_query = query
    return st


def test_bnet_a_the_same_read_pushed_again_keeps_its_time(monkeypatch):
    """An exporter restart pushes the same set again with a new marker value:
    the read shown and its time are the ones Blizzard answered."""
    monkeypatch.setattr(stores.time, "time", lambda: T3)
    assert _bnet([("s1", T1, T1)]).bnet_state(CHAR) == _bnet([("s1", T3, T1)]).bnet_state(CHAR) == \
        {"as_of": T1, "fallback": False}


def test_bnet_b_d_a_cut_newer_read_doesnt_replace_the_complete_one(monkeypatch):
    """(b) and (d) at once: a newer read with some parts and no refreshed
    time (untimed, partial) shows the last complete one, said so."""
    monkeypatch.setattr(stores.time, "time", lambda: T3)
    assert _bnet([("s1", T1, T1), ("s2", T2, None)]).bnet_state(CHAR) == {"as_of": T1, "fallback": True}


def test_bnet_c_either_order(monkeypatch):
    monkeypatch.setattr(stores.time, "time", lambda: T3)
    a, b = ("s1", T2, T2), ("s2", T2, T2 - 5)
    assert _bnet([a, b]).snapshot_info(CHAR) == _bnet([b, a]).snapshot_info(CHAR)


def test_bnet_e_never_read_is_unknown(monkeypatch):
    from wowstats import coverage

    monkeypatch.setattr(stores.time, "time", lambda: T3)
    assert _bnet([]).bnet_state(CHAR) is None
    assert _bnet([("s1", T1, None)]).bnet_state(CHAR) is None, "no complete read: no time, not 0"
    assert coverage.battlenet({})["state"] == "unavailable"


# ---------------------------------------------------------------- the shipper's side


SYND = ('SYNDICATOR_DATA = {\n["Characters"] = {\n["Hero Alpha-PvE"] = {\n'
        '["details"] = {\n["character"] = "Hero Alpha",\n["className"] = "PRIEST",\n},\n'
        '["bags"] = {\n{\n{\n["itemID"] = 2589,\n["itemCount"] = 1,\n},\n},\n},\n'
        '["bank"] = {\n},\n},\n},\n}\n')
POLE = ('PolestarDB = {\n["alts"] = {\n["chars"] = {\n["Hero-Alpha"] = {\n["name"] = "Hero Alpha",\n'
        '["profs"] = {\n[182] = 40,\n},\n},\n},\n},\n}\n')


def _shipped(shipper, tmp_path, files: dict[str, str], extract) -> list[dict]:
    acct = tmp_path / "acct"
    (acct / "SavedVariables").mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (acct / "SavedVariables" / name).write_text(text)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):  # a dry run prints each event as it would be spooled
        extract(acct, {CHAR: {}}, {}, shipper.Out(dry=True))
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def test_d_e_shipper_a_bank_never_seen_is_left_out(shipper, tmp_path):
    """Syndicator writes a bank it never saw as an empty table: the event
    leaves `bank` out (unknown) rather than sending an empty bank that
    would erase the one an earlier read saw."""
    got = _shipped(shipper, tmp_path, {"Syndicator.lua": SYND}, shipper.extract_inventory)
    assert [(e["bags"], "bank" in e) for e in got] == [([2589], False)]


def test_d_e_shipper_a_sheet_syndicator_doesnt_know_has_no_items(shipper, tmp_path):
    """A sheet with Polestar's professions only (Syndicator doesn't know the
    character): `items` left out, not an empty list: nothing was seen."""
    got = _shipped(shipper, tmp_path, {"Polestar.lua": POLE}, shipper.extract_gear)
    assert len(got) == 1 and "items" not in got[0] and got[0]["professions"]


def test_b_shipper_marks_a_time_from_its_own_clock(shipper, tmp_path):
    """No logout stamp and no file time for that sheet: dated by the run,
    marked `at_basis: "run"`, so readers rank it below an exact one."""
    got = _shipped(shipper, tmp_path, {"Polestar.lua": POLE}, shipper.extract_gear)
    assert got[0].get("at_basis") == "run"


# ---------------------------------------------------------------- the analysis' tables


def test_c_d_the_prepared_gear_and_sheets_dont_depend_on_arrival():
    """The planner's prep (gear, gear_sheets, snapshots): two sheets on one
    second in either order give the same tables, and the current gear is
    the one in force (exact first, its items from the newest sheet that
    has them)."""
    from tests.invariants import histories as H

    a, b = gear_ev(T2, [101]), gear_ev(T2, [202])
    s1 = sheet_ev(T2, 21, 900, sid=f"character:{CHAR}:a")
    s2 = sheet_ev(T2, 21, 950, sid=f"character:{CHAR}:b")
    one, two = H.facts(H.build([a, b, s1, s2], CHAR)), H.facts(H.build([b, a, s2, s1], CHAR))
    for t in ("gear", "gear_sheets", "snapshots"):
        assert one[t] == two[t], t
    later = gear_ev(T3, None, professions="[]")
    approx = gear_ev(T3 + 60, [303], at_basis="commit")
    got = H.facts(H.build([a, later, approx], CHAR))["gear"]
    assert [r[2] for r in got] == [101]
