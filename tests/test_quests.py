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
    # quests.history: the same reads plus every gear and bags sheet, the
    # older ones too (the Views' and the MCP's one answer).
    sheets = [{**worn("2026-11-10T20:00:00Z", VIOLET_SASH), "char": "Testpriest-Fixture"},
              {**worn("2026-11-12T20:00:00Z"), "char": "Testpriest-Fixture"}]
    cf = 'char:="Testpriest-Fixture"'
    got = quests.history(lambda q: logsql.parse(q).run([*rows, *sheets]), f'feed:="wow-recorder" {cf}', cf)
    assert got[POWER_OVERWHELMING] == "reward" and got[BAZIL_THREDD] == "turnin" and got[5] == "altoholic"


def long_history(n: int, ids_each: int = 0, proof: int = 5) -> list[dict]:
    """`n` DataStore snapshots, oldest first, an hour apart: only the oldest
    has `proof` (a bit the later saves rounded away), each has `ids_each`
    other quests. Shaped as the shipper sends them (one per changed set)."""
    out = []
    for i in range(n):
        ids = set(range(100_000, 100_000 + ids_each + i))
        if i == 0:
            ids.add(proof)
        t = datetime(2026, 1, 1, tzinfo=UTC).timestamp() + i * 3600
        out.append(snap("altoholic", ids, datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")))
    return out


def test_the_live_readers_see_every_snapshot_like_the_planner():
    """Review 2 of 0.7.0: Q_SNAPSHOTS kept the newest 20 snapshots while the
    planner (wowstats.plan.facts.gather) reads every event. A quest only the
    oldest of 25 says is done was done for the planner and not for Gear, the
    Overview or the MCP. Every reader now gets the same answer."""
    cf = 'char:="Testpriest-Fixture"'
    rows = long_history(25)
    planner = quests.completed(rows)
    live = quests.history(lambda q: logsql.parse(q).run(rows), f'feed:="wow-recorder" {cf}', cf)
    assert planner[5] == "altoholic"
    assert live == planner


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


# ---------------------------------------------------------------- sold or replaced since


def _sheet(t: str, waist: int | None) -> dict:
    """Testpriest's sheet (tests/test_gear.py) with this belt, or none."""
    from tests.test_gear import WORN

    slots = {k: v for k, v in WORN.items() if k != 6} | ({6: waist} if waist else {})
    return {"_time": t, "feed": "wow-gear", "char": CHAR, "class": "PRIEST", "race": "Gnome", "faction": "Alliance",
            "spec": "Shadow", "items": json.dumps([{"slot": k, "item_id": v} for k, v in slots.items()]),
            "professions": json.dumps([{"name": "Tailoring", "rank": 160}])}


def _bags(t: str) -> dict:
    return {"_time": t, "feed": "wow-bags", "char": CHAR, "bags": "[]", "bank": "[]"}


@pytest.fixture
def sash_sold(tmp_path, monkeypatch):
    """Power Overwhelming handed in with the Recorder off (#33): its accept
    recorded, Violet Sash worn on the 10th, gone from the sheet and the bags
    on the 12th (sold or replaced). Served by the VictoriaLogs stand-in."""
    from tests import degraded as D
    from wowstats import plan as planmod
    from wowstats import ui

    monkeypatch.setenv("WOW_ITEMS", "off")
    rows = [rec("2026-11-10T18:00:00Z", "accept", quest_id=POWER_OVERWHELMING, level=32, zone="Stormwind City"),
            _sheet("2026-11-10T21:00:00Z", VIOLET_SASH), _bags("2026-11-10T21:00:00Z"),
            _sheet("2026-11-12T19:00:00Z", None), _bags("2026-11-12T19:00:00Z"),
            {"_time": "2026-11-12T19:00:00Z", "feed": "wow-character", "char": CHAR, "level": 32,
             "zone": "Stormwind City", "class": "PRIEST", "race": "Gnome", "faction": "Alliance",
             "source": "polestar"}]
    vl = D.VL(rows)
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": CHAR, "HOME": str(tmp_path), "ANALYSIS_STATE_DIR": str(tmp_path),
           "WOW_ITEMS": "off"}
    planmod._CACHE.clear()
    yield env, ui.App(env)
    vl.close()
    planmod._CACHE.clear()


def test_a_completed_quest_stays_completed_after_its_reward_is_gone(sash_sold):
    """The sash proved the hand-in once; selling or replacing it doesn't
    undo it. Gear (page and MCP), the Overview's advice (next steps,
    next_moves), the planner and the dungeon picks never offer it again."""
    from wowstats import plan as planmod
    from wowstats.mcp import tools as T
    from wowstats.mcp.store import Store as MStore

    env, app = sash_sold

    def from_po(ups):
        return [u for u in ups or [] if any(s.get("quest_id") == POWER_OVERWHELMING for s in u.get("sources") or [])]

    assert app.views.quests_done(CHAR)[POWER_OVERWHELMING] == "reward"
    assert from_po(app.gear(CHAR, None)["upgrades"]) == []
    assert (app.views.insights(CHAR)["advice"] or {}).get("upgrades") is not None
    assert from_po(app.views.insights(CHAR)["advice"]["upgrades"]) == []
    ctx = T.Ctx(store=MStore(vl_url=env["VL_URL"], vm_url=env["VM_URL"]), default_char=CHAR, env=env)
    m = T.call(ctx, "gear", {"char": CHAR, "limit": 40})
    assert not [u for u in m["next_upgrades"] + m.get("buy_now", []) if u["item_id"] == VIOLET_SASH]
    inp = planmod.inputs(env, CHAR)
    assert POWER_OVERWHELMING in inp["quests_done"]
    assert VIOLET_SASH not in {u["item_id"] for u in inp["upgrades"]}
    # The dungeon picks read the planner's quests_done: Testpriest's inputs
    # at 30 (tests/plan/test_dungeonview.py), City of Dalaran's quests open,
    # then this character's answer added.
    from tests.plan.test_dungeonview import _caster_at_30, load

    picks = _caster_at_30(load("plan-inputs.json.gz"))
    picks["quests_done"] = sorted({*picks["quests_done"], *inp["quests_done"]})
    dal = next(b for b in dungeonview.build(picks)["dungeons"] if b["name"] == "City of Dalaran")
    assert next(q for q in dal["quests"] if q["quest_id"] == POWER_OVERWHELMING)["state"] == "done"
    assert "Violet Sash" not in {r["name"] for r in dal["quest_rewards"]}


# ---------------------------------------------------------------- Battle.net's worn items


def test_battlenet_only_adds():
    """Battle.net's worn items prove a quest like a sheet does, last in
    trust: the history keeps every quest and source it has."""
    assert quests.completed([], battlenet=[VIOLET_SASH])[POWER_OVERWHELMING] == "battlenet"
    assert quests.with_battlenet({}, [VIOLET_SASH])[POWER_OVERWHELMING] == "battlenet"
    hist = quests.completed([worn("2026-11-10T21:00:00Z", VIOLET_SASH), {"kind": "turnin", "quest_id": 1}])
    both = quests.with_battlenet(hist, [VIOLET_SASH])
    assert both[POWER_OVERWHELMING] == "reward" and both[1] == "turnin" and hist.items() <= both.items()
    assert quests.with_battlenet(hist, []) == hist
    assert quests.worn_ids({6: {"item_id": VIOLET_SASH, "stats": {}}, 1: {"item_id": None}}) == [VIOLET_SASH]


@pytest.fixture
def sash_on_battlenet(tmp_path, monkeypatch):
    """No Syndicator (no gear or bags sheet at all), Power Overwhelming
    accepted with the Recorder on and handed in with it off; Battle.net says
    Violet Sash is worn now. VictoriaLogs and vm-wow stand-ins."""
    import time as _time

    from tests import degraded as D
    from wowstats import plan as planmod
    from wowstats import ui

    monkeypatch.setenv("WOW_ITEMS", "off")
    who = {"character": CHAR, "account_id": "0"}
    rows = [rec("2026-11-10T18:00:00Z", "accept", quest_id=POWER_OVERWHELMING, level=32, zone="Stormwind City"),
            {"_time": "2026-11-12T19:00:00Z", "feed": "wow-character", "char": CHAR, "level": 32,
             "zone": "Stormwind City", "class": "PRIEST", "race": "Gnome", "faction": "Alliance",
             "source": "polestar"}]
    vl = D.VL(rows, metrics={
        "wow_character_profile_refreshed_timestamp_seconds": [{"metric": who, "value": [0, str(_time.time())]}],
        "wow_character_equipped_stat": [],
        "wow_character_equipped": [{"metric": {**who, "slot": "waist"}, "value": [0, str(VIOLET_SASH)]}]})
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": CHAR, "HOME": str(tmp_path), "ANALYSIS_STATE_DIR": str(tmp_path),
           "WOW_ITEMS": "off"}
    planmod._CACHE.clear()
    yield env, ui.App(env)
    vl.close()
    planmod._CACHE.clear()


def test_a_reward_battlenet_says_is_worn_proves_its_quest_everywhere(sash_on_battlenet):
    """Degraded mode: without Syndicator, Battle.net's equipment is the only
    proof. Gear (page and MCP), the Overview's advice, the planner (quests
    done, open quests) and the dungeon picks all count the quest done; the
    MCP only when the person lets Battle.net data reach the model."""
    from wowstats import plan as planmod
    from wowstats.mcp import tools as T
    from wowstats.mcp.store import Store as MStore

    env, app = sash_on_battlenet

    def from_po(ups):
        return [u for u in ups or [] if any(s.get("quest_id") == POWER_OVERWHELMING for s in u.get("sources") or [])]

    assert app.views.quests_done(CHAR)[POWER_OVERWHELMING] == "battlenet"
    assert POWER_OVERWHELMING not in app.views.quests_history(CHAR)  # never cached with the history
    g = app.gear(CHAR, None)
    assert g["worn_from"] == "battle.net" and from_po(g["upgrades"]) == []
    assert from_po(app.views.insights(CHAR)["advice"]["upgrades"]) == []

    ctx = T.Ctx(store=MStore(vl_url=env["VL_URL"], vm_url=env["VM_URL"]), default_char=CHAR, env=env, bnet=True)
    assert T._views(ctx).quests_done(CHAR)[POWER_OVERWHELMING] == "battlenet"
    m = T.call(ctx, "gear", {"char": CHAR, "limit": 40})
    assert not [u for u in m["next_upgrades"] + m.get("buy_now", []) if "Power Overwhelming" in str(u["where"])]
    off = T.Ctx(store=MStore(vl_url=env["VL_URL"], vm_url=env["VM_URL"]), default_char=CHAR, env=env, bnet=False)
    assert T.bnet_worn(off, CHAR) == [] and POWER_OVERWHELMING not in T._views(off).quests_done(CHAR)

    inp = planmod.inputs(env, CHAR, battlenet=app.bnet_worn(CHAR))
    assert POWER_OVERWHELMING in inp["quests_done"] and inp["quests_done_from"]["battlenet"] >= 1
    assert POWER_OVERWHELMING not in {o["quest_id"] for o in inp["open_quests"]}
    without = planmod.inputs(env, CHAR)  # its own cache entry: Battle.net off
    assert POWER_OVERWHELMING not in without["quests_done"]
    assert POWER_OVERWHELMING in {o["quest_id"] for o in without["open_quests"]}

    from tests.plan.test_dungeonview import _caster_at_30, load

    picks = _caster_at_30(load("plan-inputs.json.gz"))
    picks["quests_done"] = sorted({*picks["quests_done"], *inp["quests_done"]})
    dal = next(b for b in dungeonview.build(picks)["dungeons"] if b["name"] == "City of Dalaran")
    assert next(q for q in dal["quests"] if q["quest_id"] == POWER_OVERWHELMING)["state"] == "done"


def test_a_commit_dated_snapshot_reads_as_by_then():
    """Review 4, B: a snapshot re-extracted from an archive without capture
    times carries the commit's time with `at_basis: "commit"`: done by then,
    never "on" that day as if the game said so at that moment."""
    approx = {**snap("questie", [11], t="2026-11-20T08:00:00Z"), "at_basis": "commit"}
    rows = [approx, snap("altoholic", [12], t="2026-11-17T07:20:00Z")]
    cf = 'char:="Testpriest-Fixture"'
    got = quests.explain(logsql.parse(quests.Q_SNAPSHOTS.format(cf=cf)).run(rows))
    assert got[11]["why"] == "Questie lists it completed by 2026-11-20" and got[11]["t_by"] is True
    assert got[12]["why"] == "Altoholic (DataStore_Quests) lists it completed on 2026-11-17"
    assert got[12]["t_by"] is False
    # An exact sighting earlier than the bound wins, and is exact.
    got = quests.explain([approx, snap("questie", [11], t="2026-11-18T08:00:00Z")])
    assert got[11]["t_by"] is False and got[11]["why"].endswith("on 2026-11-18")
    # Seen by a time before the accept: not this time round. After it: as
    # unknown as no time, so it settles like an untimed source does.
    accepted = datetime(2026, 11, 21, tzinfo=UTC).timestamp()
    assert not quests.settles_open(quests.explain([approx])[11], accepted)
    assert quests.settles_open(quests.explain([approx])[11], accepted - 7 * 86400)
