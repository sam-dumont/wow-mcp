"""Which quests a character has done (wowstats.quests): every source the
kit reads, the chain inference, and the shipper's readers of Questie and
Altoholic's DataStore_Quests on files shaped like the real ones.

The case behind it (owner, 2026-11-19): The Stockade's Bazil Thredd (389)
showed as blocked by The Unsent Letter (373), handed in before Polestar was
installed, so the Recorder never saw it."""

import gzip
import json
import random
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wowstats import ingest, logsql, quests
from wowstats.plan import dungeonview

UNSENT_LETTER, BAZIL_THREDD, STOCKADE_RIOTS = 373, 389, 391
# tests/data/quests-forever: DataStore_Quests.lua and Questie.lua in the
# layout the Forever client writes (keys, nesting, 16-digit numbers, the
# 98xxx slot as a [n] key), for a made-up set of quests (truth.txt). The
# DataStore numbers were built as the addon builds them: 2^bit added to a
# double per quest, in no particular order, printed with 16 digits.
DATA = Path(__file__).parent / "data" / "quests-forever"
ALTOHOLIC = Path(__file__).parent / "data" / "altoholic-forever"
TRUTH = {int(x) for x in (DATA / "truth.txt").read_text().split()}


def snap(source: str, ids, t="2026-11-19T10:00:00Z") -> dict:
    """A wow-quests event as VictoriaLogs holds it (lists as JSON strings)."""
    return ingest.screen({"_time": t, "feed": "wow-quests", "kind": "completed", "char": "Testpriest-Fixture",
                          "source": source, "quest_ids": sorted(ids), "n": len(ids)})


def test_the_unsent_letter_from_a_full_set():
    """(a) a source with the whole set lists it: done, blocker gone."""
    done = quests.completed([snap("questie", [UNSENT_LETTER, 1, 2])])
    assert done[UNSENT_LETTER] == "questie"
    assert dungeonview.needs(BAZIL_THREDD, set(done), {}) == []
    assert [n["quest_id"] for n in dungeonview.needs(BAZIL_THREDD, set(), {})] == [UNSENT_LETTER]


def test_the_unsent_letter_from_a_follow_up_alone():
    """(b) only the follow-up is recorded: its prerequisite is done too,
    with no addon that saves the whole set."""
    done = quests.completed([{"kind": "turnin", "quest_id": str(BAZIL_THREDD)}])
    assert done == {BAZIL_THREDD: "turnin", UNSENT_LETTER: "inferred"}
    # Down the chain: The Stockade Riots needs Bazil Thredd, which needs the letter.
    assert set(quests.completed([{"kind": "turnin", "quest_id": STOCKADE_RIOTS}])) == {
        STOCKADE_RIOTS, BAZIL_THREDD, UNSENT_LETTER}


def test_inference_reads_forever_db_prerequisites_only_where_certain():
    assert quests.needs_first(BAZIL_THREDD) == ([], [UNSENT_LETTER])
    # An "any" list of several: one of them was done, nobody knows which.
    pre = quests.forever.data()["pre"]
    multi = next(int(q) for q, (all_, any_) in pre.items() if len(any_) > 1 and not all_)
    assert not quests.close([multi])
    # An "all" list: every one of them.
    q, (all_, _) = next((int(q), v) for q, v in pre.items() if len(v[0]) > 1)
    assert set(all_) <= quests.close([q])


def test_the_game_flag_the_recorder_saw():
    """An offer record's quests_done: what the client flagged completed."""
    done = quests.completed([{"feed": "wow-recorder", "kind": "offer", "quests_done": f"{UNSENT_LETTER};97914"}])
    assert done[UNSENT_LETTER] == "flagged" and done[97914] == "flagged"


def test_holding_a_reward_only_one_quest_gives():
    rewards = quests.forever.data()["rewards"]
    item, quest = next((int(i), q) for i, q in rewards.items())
    assert quests.reward_quest(item) == quest
    worn = {"_time": "2026-11-19T10:00:00Z", "feed": "wow-gear", "items": json.dumps([{"slot": 1, "item_id": item}])}
    assert quests.completed([worn])[quest] == "reward"
    bags = ingest.screen({"_time": "2026-11-19T10:00:00Z", "feed": "wow-bags", "char": "Testhunter-Fixture",
                          "bags": [item], "bank": []})
    assert quests.completed([bags])[quest] == "reward"
    assert quests.completed([], held=[item])[quest] == "reward"
    # A white reward (vendors sell such things), an item two quests give:
    # holding them proves nothing.
    assert quests.reward_quest(60) is None  # Layered Tunic, Bounty on Garrick Padfoot
    assert quests.reward_quest(16608) is None  # two quests reward it
    assert quests.completed([], held=[60, 16608]) == {}


def test_every_source_joins_in_trust_order():
    rows = [{"kind": "turnin", "quest_id": 1}, snap("questie", [1, 2]), snap("altoholic", [2, 3]),
            snap("altoholic", [4], t="2026-11-18T10:00:00Z")]
    done = quests.completed(rows)
    assert {q: done[q] for q in (1, 2, 3, 4)} == {1: "turnin", 2: "questie", 3: "altoholic", 4: "altoholic"}
    assert quests.counts(done)["turnin"] == 1


def test_the_logsql_reads_answer_from_the_cache():
    """Q_RECORDS and Q_SNAPSHOTS run on wowstats.logsql (the event cache),
    as views.gear and the MCP's gear tool ask them."""
    rows = [{"_time": "2026-11-19T09:00:00Z", "feed": "wow-recorder", "char": "Testpriest-Fixture", "kind": "turnin",
             "quest_id": str(BAZIL_THREDD)},
            {"_time": "2026-11-19T09:01:00Z", "feed": "wow-recorder", "char": "Testpriest-Fixture", "kind": "offer",
             "quests_done": "97914"},
            snap("altoholic", [5, 6])]
    rec = logsql.parse(f'feed:="wow-recorder" char:="Testpriest-Fixture" {quests.Q_RECORDS}').run(rows)
    sn = logsql.parse(quests.Q_SNAPSHOTS.format(cf='char:="Testpriest-Fixture"')).run(rows)
    done = quests.completed([*rec, *sn])
    assert {q: s for q, s in done.items() if s != "inferred"} == {BAZIL_THREDD: "turnin", 97914: "flagged",
                                                                  5: "altoholic", 6: "altoholic"}
    assert done[UNSENT_LETTER] == "inferred"


# ---------------------------------------------------------------- the shipper


@pytest.fixture
def acct(tmp_path):
    a = tmp_path / "WTF" / "Account" / "ACCT"
    shutil.copytree(ALTOHOLIC, a / "SavedVariables")
    for f in ("DataStore_Quests.lua", "Questie.lua"):
        shutil.copy(DATA / f, a / "SavedVariables" / f)
    return a


def test_questie_complete_table(shipper, acct):
    got = shipper.questie_completed(acct)
    assert set(got) == {"Testpriest-Fixture"}  # Testhunter's Questie never filled it
    assert UNSENT_LETTER in got["Testpriest-Fixture"] and set(got["Testpriest-Fixture"]) <= TRUTH


def test_datastore_history_reads_only_trusted_bits(shipper, acct):
    got = shipper.datastore_completed(acct)
    assert set(got) == {"Testpriest-Fixture"}  # Testhunter's history is empty
    g = got["Testpriest-Fixture"]
    ids = set(g["quest_ids"])
    # Never a quest that isn't done, whatever the rounding did.
    assert ids <= TRUTH
    assert {UNSENT_LETTER, BAZIL_THREDD, 98463} <= ids
    # The dense slots lost low bits: fewer than the game counted, and the
    # rest is reported unknown rather than guessed.
    assert g["size"] == len(TRUTH) and len(ids) < len(TRUTH) and g["unknown"] > 0
    assert g["at"] == 1792439015


def test_datastore_naive_decoding_would_lie():
    """Why _ds_bits exists: reading every set bit of the saved numbers
    reports quests that were never done."""
    text = (DATA / "DataStore_Quests.lua").read_text()
    body = text[text.index("DataStore_Quests_History"):]
    body = body[body.index('["Quests"] = {') + 14:body.index("},")]
    naive = set()
    for i, tok in enumerate(x.strip().rstrip(",") for x in body.strip().splitlines() if "[" not in x):
        v = int(float(tok))
        naive |= {64 * i + b if b else 64 * (i + 1) for b in range(64) if v >> b & 1}
    assert naive - TRUTH


@pytest.mark.parametrize("seed", range(40))
def test_ds_bits_never_reports_a_bit_that_was_not_set(shipper, seed):
    """The addon's arithmetic (LibBit64.SetBit on a double, any order) and
    the client's 16-digit print, on random slots: every bit _ds_bits calls
    set was set, and every bit it calls clear was clear."""
    rnd = random.Random(seed)
    bits = set(rnd.sample(range(64), rnd.randint(1, 64)))
    v = 0.0
    for b in rnd.sample(sorted(bits), len(bits)):
        m = 2.0 ** b
        if not v % (m + m) >= m:
            v += m
    tok = f"{v:.16g}"  # what the client writes; the reader gives an int for plain digits
    on, unknown = shipper._ds_bits(int(tok) if tok.isdigit() else float(tok))
    assert on <= bits
    assert not (set(range(64)) - on - unknown) & bits
    # Below 2^53 nothing rounds: every bit is read.
    if max(bits) < 53:
        assert (on, unknown) == (bits, set())


def test_datastore_skipped_when_history_isnt_rebuilt(shipper, acct):
    f = acct / "SavedVariables" / "DataStore_Quests.lua"
    f.write_text(f.read_text().replace('["AutoUpdateHistory"] = true', '["AutoUpdateHistory"] = false'))
    assert shipper.datastore_completed(acct) == {}


def test_extract_quests_events(shipper, acct):
    got: list[dict] = []
    out = shipper.Out(dry=True)
    out.emit = got.append
    state: dict = {}
    shipper.extract_quests(acct, {"Testpriest-Fixture": {}, "Testhunter-Fixture": {}}, state, out)
    assert sorted((e["char"], e["source"]) for e in got) == [("Testpriest-Fixture", "altoholic"),
                                                             ("Testpriest-Fixture", "questie")]
    stored = [ingest.screen(e) for e in got]
    assert all(s and s["feed"] == "wow-quests" for s in stored)
    # Through ingest and back: the union the server reads.
    done = quests.completed(stored)
    assert done[UNSENT_LETTER] == "questie" and set(k for k, v in done.items() if v != "inferred") <= TRUTH
    # Unchanged files: nothing new.
    got.clear()
    shipper.extract_quests(acct, {"Testpriest-Fixture": {}}, state, out)
    assert got == []
    # Only the roster's characters.
    shipper.extract_quests(acct, {}, {}, out)
    assert got == []


def test_the_stockade_panel_on_the_priests_inputs():
    """The planner's real-shaped inputs (tests/data/plan-inputs.json.gz)
    recorded The Stockade Riots handed in, yet showed Bazil Thredd, the
    quest before it, as not picked up and blocked by The Unsent Letter.
    Through quests.completed both are done."""
    inp = json.loads(gzip.decompress((Path(__file__).parent / "data" / "plan-inputs.json.gz").read_bytes()))
    before = {q["quest_id"]: q for b in dungeonview.build(inp)["dungeons"] if b["name"] == "The Stockade"
              for q in b["quests"]}
    assert before[BAZIL_THREDD]["blocked"] and before[STOCKADE_RIOTS]["state"] == "done"
    inp["quests_done"] = sorted(quests.completed({"kind": "turnin", "quest_id": q} for q in inp["quests_done"]))
    after = {q["quest_id"]: q for b in dungeonview.build(inp)["dungeons"] if b["name"] == "The Stockade"
             for q in b["quests"]}
    assert after[BAZIL_THREDD]["state"] == "done" and not after[BAZIL_THREDD]["needs"]


# ---------------------------------------------------------------- handed in with the Recorder off

# City of Dalaran (Forever's new dungeon): forever-db has no reward rows for
# its quests, the gear data names Violet Sash as Power Overwhelming's alone.
POWER_OVERWHELMING, VIOLET_SASH = 92489, 251962
# Quest 16's only reward in forever-db, but creatures drop it too (its
# item_source): a generic item, holding it proves nothing.
GIVE_GERARD_A_DRINK, SHINY_RED_APPLE = 16, 4536
CHAR = "Testpriest-Fixture"


def rec(t: str, kind: str, **kw) -> dict:
    return {"_time": t, "feed": "wow-recorder", "char": CHAR, "kind": kind, "source_id": f"{kind}-{t}",
            "level": 33, "zone": "Dalaran", **kw}


def worn(t: str, *items: int) -> dict:
    return {"_time": t, "feed": "wow-gear", "char": CHAR, "level": 33, "class": "PRIEST",
            "items": json.dumps([{"slot": 6, "item_id": i} for i in items])}


def built(rows):
    from wowstats.analysis import prep
    return prep.build(rows, CHAR)


def test_dalaran_rewards_prove_their_quest():
    assert quests.reward_quest(VIOLET_SASH) == POWER_OVERWHELMING
    e = quests.explain([worn("2026-11-10T20:00:00Z", VIOLET_SASH)])[POWER_OVERWHELMING]
    assert e["from"] == "reward" and "Violet Sash" in e["why"] and "worn" in e["why"]


def test_a_quest_handed_in_with_the_recorder_off_is_done_inferred():
    """Accepted while the Recorder ran, no hand-in, its only-this-quest
    reward worn later: done (inferred), with the reason, closed when the
    sheet first showed it; the planner's open quests leave it out."""
    from wowstats.plan import facts

    rows = [rec("2026-11-10T18:00:00Z", "accept", quest_id=POWER_OVERWHELMING),
            rec("2026-11-10T18:05:00Z", "kill"), worn("2026-11-10T21:00:00Z", VIOLET_SASH),
            rec("2026-11-12T18:00:00Z", "kill")]
    db = built(rows)
    status, why, closed = db.execute("SELECT status, done_why, closed_t FROM quests WHERE quest_id = ?",
                                     (POWER_OVERWHELMING,)).fetchone()
    assert status == quests.INFERRED == "done (inferred)"
    assert "Violet Sash" in why and closed == datetime(2026, 11, 10, 21, tzinfo=UTC).timestamp()
    assert db.execute("SELECT source FROM quests_done WHERE quest_id = ?", (POWER_OVERWHELMING,)).fetchone() == (
        "reward",)
    assert facts.open_quests(db, 33) == []
    # Sold since: the earlier sheet still proves it.
    db = built([*rows, worn("2026-11-12T19:00:00Z")])
    assert db.execute("SELECT status FROM quests").fetchone()[0] == quests.INFERRED


def test_a_generic_reward_leaves_the_quest_open():
    from wowstats.plan import facts

    item, quest = SHINY_RED_APPLE, GIVE_GERARD_A_DRINK
    assert quests.reward_quest(item) is None
    bags = ingest.screen({"_time": "2026-11-10T21:00:00Z", "feed": "wow-bags", "char": CHAR, "bags": [item],
                          "bank": []})
    db = built([rec("2026-11-10T18:00:00Z", "accept", quest_id=quest), bags])
    assert db.execute("SELECT status, done_why FROM quests").fetchall() == [("open", None)]
    assert [o["quest_id"] for o in facts.open_quests(db, 33)] == [quest]


def test_a_repeat_picked_up_again_stays_open():
    """A hand-in the Recorder saw before the accept is the last time round,
    not this one."""
    db = built([rec("2026-11-10T18:00:00Z", "turnin", quest_id=POWER_OVERWHELMING),
                rec("2026-11-11T18:00:00Z", "accept", quest_id=POWER_OVERWHELMING)])
    assert db.execute("SELECT status FROM quests ORDER BY quest_row").fetchall() == [("untimed",), ("open",)]


def test_the_analysis_sees_it_done():
    """play_sql's DuckDB mirror (what the analysis queries) and the
    semantic layer's quests view carry the status and the reason."""
    from wowstats.analysis import duck

    db = built([rec("2026-11-10T18:00:00Z", "accept", quest_id=POWER_OVERWHELMING),
                worn("2026-11-10T21:00:00Z", VIOLET_SASH)])
    run = duck.ReadOnlyDuck(duck.mirror(db))
    _, got, _ = run.run("SELECT q.status, q.done_why, d.source FROM quests q JOIN quests_done d USING (quest_id)")
    assert got[0][0] == "done (inferred)" and "Violet Sash" in got[0][1] and got[0][2] == "reward"
    _, open_, _ = run.run("SELECT COUNT(*) FROM quests WHERE status = 'open'")
    assert open_ == [(0,)]
