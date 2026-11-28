"""The player's own ForeverDB records (owner, 2026-11-28): kills and drops
from the addon's outbox (ForeverDB_DB.obs, Observations.lua) and the spots of
a quest's objectives (ForeverDB_DB.quests[..].gs and .obs).

Everything here is made up: game ids only, no names, realm, guild or GUID.
The outbox id is a made-up 12 character token.
"""

import contextlib
import io
import json

import pytest

from wowstats import forever, ingest, ownobs
from wowstats.plan import dungeongain as G
from wowstats.plan import dungeonview as V

NPC, ITEM, OTHER = 3000, 4000, 4001


def lua(outbox: dict[int, str], quests: dict | None = None, oid: str = "AbCdEf012345") -> str:
    out = "".join(f'[{s}] = "{b}",\n' for s, b in outbox.items())
    qs = ""
    for qid, e in (quests or {}).items():
        spots = ""
        for key in ("gs", "obs"):
            spots += f'["{key}"] = {{' + "".join(
                f'{{["i"] = {i}, ["m"] = {m}, ["x"] = {x}, ["y"] = {y}}},' for i, m, x, y in e.get(key, [])) + "},\n"
        qs += f'[{qid}] = {{["id"] = {qid}, {spots}}},\n'
    return (f'ForeverDB_DB = {{\n["obs"] = {{["cap"] = 400, ["id"] = "{oid}", ["seq"] = {max(outbox, default=0)}, '
            f'["v"] = 1, ["out"] = {{\n{out}}}}},\n["quests"] = {{\n{qs}}},\n}}\n')


def account(tmp_path, outbox, quests=None):
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    (acct / "SavedVariables").mkdir(parents=True, exist_ok=True)
    (acct / "SavedVariables" / "ForeverDB.lua").write_text(lua(outbox, quests), encoding="utf-8")
    return acct


def run(shipper, acct, state) -> list[dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        shipper.extract_foreverdb_own(acct, {}, state, shipper.Out(dry=True))
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def test_decode_reads_k_k_and_d_and_nothing_else(shipper):
    kills, looted, drops = shipper.decode_obs(
        f"K:{NPC}:2^k:{NPC}:3^d:{NPC}:{ITEM}:1^K:{NPC}:1^d:{NPC}:{ITEM}:2"
        "^x:739:900:12^M:3000:enUS:Name^I:4000:0:1:C3000.1^Q:739:C:1:1418:496:662:0:0:0:0:0:30^K:abc:1^d:1:2^k:5:0")
    assert kills == {NPC: 3} and looted == {NPC: 3} and drops == {(NPC, ITEM): 3}


def test_events_sum_the_new_batches_and_say_what_they_cover(shipper, tmp_path):
    acct = account(tmp_path, {1: f"K:{NPC}:2^d:{NPC}:{ITEM}:1", 2: f"k:{NPC}:2^K:{NPC}:1", 3: f"d:{NPC}:{OTHER}:1"})
    [ev] = run(shipper, acct, {})
    assert ev["feed"] == "wow-foreverdb" and ev["kind"] == "obs" and "char" not in ev
    assert ev["obs_from"] == 1 and ev["obs_to"] == 3
    assert ev["obs_kills"] == [[NPC, 3]] and ev["obs_looted"] == [[NPC, 2]]
    assert ev["obs_drops"] == [[NPC, ITEM, 1], [NPC, OTHER, 1]]
    assert set(ev) <= shipper.EVENT_BASE | shipper.FDB_KEYS
    assert "AbCdEf012345" not in json.dumps(ev)  # the outbox id never leaves, only a hash of it
    assert ingest.screen(ev)["kind"] == "obs"


def test_a_batch_goes_once_across_overlapping_syncs(shipper, tmp_path):
    state: dict = {}
    first = account(tmp_path, {1: f"K:{NPC}:2", 2: f"d:{NPC}:{ITEM}:1"})
    assert len(run(shipper, first, state)) == 1
    assert run(shipper, first, state) == []  # nothing new
    # The outbox as the next save holds it: batch 2 again (the addon keeps it
    # until the uploader acks), batch 3 new, batch 1 rolled out.
    second = account(tmp_path, {2: f"d:{NPC}:{ITEM}:1", 3: f"K:{NPC}:4"})
    [ev] = run(shipper, second, state)
    assert (ev["obs_from"], ev["obs_to"]) == (3, 3) and ev["obs_kills"] == [[NPC, 4]] and "obs_drops" not in ev
    assert state["foreverdb_obs"] == {"AbCdEf012345": 3}


def test_a_big_outbox_goes_in_bounded_events(shipper, tmp_path, monkeypatch):
    monkeypatch.setattr(shipper, "OBS_CHUNK", 3)
    acct = account(tmp_path, {1: "^".join(f"K:{n}:1" for n in range(1, 8))})
    sent = run(shipper, acct, {})
    assert len(sent) == 3 and len({e["source_id"] for e in sent}) == 3
    assert sum(len(e["obs_kills"]) for e in sent) == 7
    assert all(len(json.dumps(e)) < shipper.POST_MAX_BYTES for e in sent)


def test_quest_spots_go_when_they_change(shipper, tmp_path):
    state: dict = {}
    quests = {739: {"gs": [(1, 1418, 49.6, 66.2), (0, 1418, 40.0, 40.0)], "obs": [(2, 1418, 50.1, 60.0)]}}
    acct = account(tmp_path, {}, quests)
    [ev] = run(shipper, acct, state)
    assert ev["kind"] == "quest_pos" and ev["quest_id"] == 739
    assert ev["spots"] == [["g", 1, 1418, 49.6, 66.2], ["g", 0, 1418, 40.0, 40.0], ["o", 2, 1418, 50.1, 60.0]]
    assert set(ev) <= shipper.EVENT_BASE | shipper.FDB_KEYS
    assert run(shipper, acct, state) == []
    quests[739]["gs"].append((1, 1418, 51.0, 67.0))
    [again] = run(shipper, account(tmp_path, {}, quests), state)
    assert again["source_id"] != ev["source_id"] and len(again["spots"]) == 4


def test_a_quest_with_no_usable_spot_sends_nothing(shipper, tmp_path):
    quests = {5: {"gs": [(1, 1418, 149.0, 66.2)]}, 6: {}}
    assert run(shipper, account(tmp_path, {}, quests), {}) == []


def test_no_file_no_event(shipper, tmp_path):
    acct = tmp_path / "WTF" / "Account" / "ACCT"
    (acct / "SavedVariables").mkdir(parents=True)
    assert run(shipper, acct, {}) == []


# ---- the server's side

def obs_event(sid, kills=(), looted=(), drops=()):
    # As the store hands them back: nested values are one JSON string each.
    return {"_time": "2026-11-28T10:00:00Z", "source_id": sid, "obs_kills": json.dumps(list(kills)),
            "obs_looted": json.dumps(list(looted)), "obs_drops": json.dumps(list(drops))}


def test_totals_add_up_across_syncs_and_count_a_replay_once():
    rows = [obs_event("a", kills=[[NPC, 3]], drops=[[NPC, ITEM, 1]]),
            obs_event("a", kills=[[NPC, 3]], drops=[[NPC, ITEM, 1]]),  # the same event read twice
            obs_event("b", kills=[[NPC, 2]], looted=[[NPC, 1]], drops=[[NPC, ITEM, 1]]),
            {"source_id": "c", "obs_kills": "not json"}]
    got = ownobs.totals(rows)
    assert got == {"kills": {NPC: 5}, "looted": {NPC: 1}, "drops": {(NPC, ITEM): 2}}


def test_a_rate_is_drops_over_kills_and_thin_under_five():
    four = ownobs.rates(ownobs.totals([obs_event("a", kills=[[NPC, 4]], drops=[[NPC, ITEM, 1]])]))
    five = ownobs.rates(ownobs.totals([obs_event("a", kills=[[NPC, 5]], drops=[[NPC, ITEM, 1]])]))
    assert four[NPC, ITEM] == {"pct": 25.0, "kills": 4, "drops": 1}
    assert G.thin_rate(four[NPC, ITEM]) and not G.thin_rate(five[NPC, ITEM])
    assert five[NPC, ITEM]["pct"] == 20.0
    # Looted corpses stand in where no solo kill was counted (a dungeon boss),
    # and the larger of the two is the kill count.
    group = ownobs.rates(ownobs.totals([obs_event("a", kills=[[NPC, 1]], looted=[[NPC, 8]],
                                                  drops=[[NPC, ITEM, 2]])]))
    assert group[NPC, ITEM]["kills"] == 8 and group[NPC, ITEM]["pct"] == 25.0
    # No kill counted: no rate. A stack is never over 100.
    assert ownobs.rates(ownobs.totals([obs_event("a", drops=[[NPC, ITEM, 1]])])) == {}
    assert ownobs.rates(ownobs.totals([obs_event("a", kills=[[NPC, 1]], drops=[[NPC, ITEM, 3]])]))[NPC, ITEM]["pct"] == 100.0


def loot_with(drops):
    return {"1": [{"item_id": ITEM, "name": "Item", "drops": drops}]}


def test_the_rate_sits_beside_foreverdbnet_and_is_labelled():
    net = {"boss": "Boss", "npc_id": NPC, "chance_pct": 12.0, "wing": "Wing"}
    own = {"rates": {(NPC, ITEM): {"pct": 40.0, "kills": 5, "drops": 2}, (NPC, OTHER): {"pct": 1.0, "kills": 9, "drops": 1}}}
    merged = ownobs.merge_loot(loot_with([net]), own)["1"][0]["drops"]
    assert merged[0] == {"boss": "Boss", "npc_id": NPC, "wing": "Wing", "chance_pct": 40.0, "kills": 5,
                         "source": "your kills"}
    assert merged[1] == net
    # A creature the dungeon's loot doesn't list for the item gets nothing; the input is not touched.
    other = {"boss": "Other", "npc_id": 1, "chance_pct": 3.0}
    src = loot_with([other])
    assert ownobs.merge_loot(src, own) == src and src["1"][0]["drops"] == [other]


def missing(*sources):
    return {"item_id": ITEM, "name": "Item", "slot_name": "Chest", "value": 10, "level": 30, "now": True,
            "state": "missing", "drops": list(sources), "quests": [], "gain": 10}


def test_a_rate_under_five_kills_is_no_rate_over_five_it_is():
    net = {"boss": "Boss", "npc_id": NPC, "chance_pct": None}
    thin = {**net, "chance_pct": 25.0, "kills": 4, "source": ownobs.LABEL}
    enough = {**net, "chance_pct": 20.0, "kills": 5, "source": ownobs.LABEL}
    assert V.drop_items([missing(net, thin)])[0]["chance"] is None
    got = V.drop_items([missing(net, enough)])[0]
    assert got["chance"] == 20.0 and got["unknown_rate"] is True  # the net row has none: not every source counted


# ---- the quest spot (Murdaloc, quest 739: Badlands)

def test_positions_keep_the_newest_event_of_each_quest():
    rows = [{"_time": "2026-11-27T10:00:00Z", "source_id": "q:1", "quest_id": "739",
             "spots": json.dumps([["g", 1, 1418, 10.0, 10.0]])},
            {"_time": "2026-11-28T10:00:00Z", "source_id": "q:2", "quest_id": "739",
             "spots": json.dumps([["g", 0, 1418, 40.0, 40.0], ["g", 1, 1418, 49.6, 66.2]])}]
    assert ownobs.positions(rows) == {739: [["g", 0, 1418, 40.0, 40.0], ["g", 1, 1418, 49.6, 66.2]]}


def test_the_objective_spot_skips_the_hand_in_and_names_the_zone():
    assert forever.ui_map(1418) == "Badlands"
    pos = {739: [["g", 0, 1418, 40.0, 40.0], ["o", 1, 1418, 1.0, 2.0], ["g", 1, 1418, 49.6, 66.2]]}
    assert ownobs.objective_spot(pos, 739) == {"zone": "Badlands", "x": 49.6, "y": 66.2, "objective": 1,
                                               "source": "the quest map"}
    assert ownobs.objective_spot({739: [["g", 0, 1418, 40.0, 40.0]]}, 739) is None  # a hand-in is no objective
    assert ownobs.objective_spot(pos, 1) is None


def test_a_prerequisite_without_a_place_gets_one_from_his_own_records():
    # Quest 704 needs Murdaloc (739) first; forever-db names no place for it.
    need = {"quest_id": 739, "name": "Murdaloc", "state": "new"}
    inp = {"own": {"quest_pos": {739: [["g", 1, 1418, 49.6, 66.2]]}}}
    assert V._objective_at(inp, need)["objective_at"]["zone"] == "Badlands"
    assert V._objective_at({}, need) == {}
    assert V._objective_at(inp, {**need, "state": "done"}) == {}


@pytest.mark.parametrize("what", ["objective_at", "kills"])
def test_the_mcp_answer_keeps_what_the_page_shows(what):
    from wowstats import redact

    blob = json.dumps(redact.DUNGEON) + json.dumps(redact.QREF)
    assert what in blob



# ---- which rate stands, and the rate the data carries

def test_one_kill_in_the_data_is_no_rate_for_the_obsidian_wand():
    """ForeverDB.net saw the White Obsidian Wand drop once in one kill: 100%
    of one. The loot rows keep the kill count, so it is unknown, not a sure thing."""
    from wowstats import gear

    ref = gear.reference()
    sheet = {"items": [], "class": "PRIEST", "race": "Gnome", "faction": "Alliance", "spec": "Shadow",
             "professions": []}
    rows = [r for items in gear.instance_loot(ref, sheet, level=60).values() for r in items if r["item_id"] == 274425]
    assert rows and rows[0]["drops"][0]["kills"] == 1 and rows[0]["drops"][0]["chance_pct"] == 100
    got = V.drop_items([{**missing(*rows[0]["drops"]), "item_id": 274425}])[0]
    assert got["chance"] is None


def test_the_rate_over_more_kills_stands_and_says_whose_it_is():
    net = {"boss": "B", "npc_id": NPC, "chance_pct": 12.0, "kills": 80}
    mine = {"boss": "B", "npc_id": NPC, "chance_pct": 40.0, "kills": 6, "source": ownobs.LABEL}
    got = V.drop_items([missing(net, mine)])[0]
    assert got["chance"] == 12.0 and got["rate_source"] == "ForeverDB.net"
    got = V.drop_items([missing(net, {**mine, "kills": 200})])[0]
    assert got["chance"] == 40.0 and got["rate_source"] == "your kills"
    # No kill count loses to one with at least 5.
    nocount = {"boss": "B", "npc_id": NPC, "chance_pct": 90.0}
    assert V.drop_items([missing(nocount, mine)])[0]["chance"] == 40.0
    assert V.drop_items([missing(nocount)])[0]["chance"] == 90.0
