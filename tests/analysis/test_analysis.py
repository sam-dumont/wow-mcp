"""The play analysis on recorder events (tests/data/analysis-events.jsonl.gz:
four sessions of a synthetic priest, tools/fixtures/generate.py): the prep's sums add up, the SQL tool reads and nothing
else, findings are kept only when their SQL gives the same numbers again,
and the shared page gets no number, time, date or name."""

import gzip
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wowstats import analysis
from wowstats.analysis import kb, prep, verify
from wowstats.analysis.sql import QueryError, ReadOnly
from wowstats.mcp import route

DATA = Path(__file__).resolve().parent.parent / "data" / "analysis-events.jsonl.gz"
CHAR = "Testpriest-Fixture"


@pytest.fixture(scope="module")
def rows():
    return [json.loads(line) for line in gzip.open(DATA)]


@pytest.fixture(scope="module")
def db(rows):
    return prep.build([dict(r) for r in rows], CHAR, route.reference())


@pytest.fixture(scope="module")
def knowledge():
    return kb.build({"KB_GAME_TABLES": "off"})


# ------------------------------------------------------------------ prep


def one(db, q):
    return db.execute(q).fetchone()


def test_prep_sums_add_up(db):
    sessions, online = one(db, "SELECT COUNT(*), SUM(online_s) FROM agg_session")
    assert sessions == 4 == one(db, "SELECT COUNT(*) FROM sessions")[0]
    # The timeline covers each online second once; every aggregate splits the same total.
    assert online == pytest.approx(one(db, "SELECT SUM(dur_s) FROM timeline")[0])
    assert online == pytest.approx(one(db, "SELECT SUM(online_s) FROM agg_level")[0])
    assert online == pytest.approx(one(db, "SELECT SUM(online_s) FROM agg_zone")[0])
    assert online == pytest.approx(one(db, "SELECT SUM(online_s) FROM agg_hour")[0])
    # XP: the xp records' gains, wherever they are summed.
    xp = one(db, "SELECT SUM(xp_gained) FROM xp")[0]
    assert xp > 0 and xp == one(db, "SELECT SUM(xp_gained) FROM agg_level")[0]
    assert one(db, "SELECT MIN(level), MAX(level) FROM agg_level") == (22, 26)


def test_prep_keeps_spell_ids_and_raw_kinds(db):
    assert one(db, "SELECT COUNT(*) FROM records WHERE kind = 'cast' AND spell_id IS NOT NULL")[0] > 0
    kinds = dict(db.execute("SELECT kind, n FROM record_kinds").fetchall())
    assert kinds["pos"] > 3000 and kinds["kill"] > 0 and kinds["gossip"] > 0


def test_prep_has_no_judgement(db):
    """Facts and neutral sums: no column, table or description that grades
    what the player did (owner, 2026-11-18)."""
    words = re.compile(r"wast|idle|unexplain|explained|productiv|wander|detour|standing|loss|lost|bad|good|"
                       r"efficien|optimal|useless|unproductive", re.I)
    text = prep.schema_text(db)
    assert not words.search(text), words.findall(text)


def test_prep_never_holds_other_players(rows):
    poisoned = [dict(r) for r in rows] + [{**rows[0], "feed": "wow-player", "player": "Someoneelse-Fixture"}]
    d = prep.build(poisoned, CHAR, route.reference())
    dump = "\n".join(d.iterdump())
    assert "Someoneelse" not in dump


# ------------------------------------------------------------------ the SQL tool


@pytest.mark.parametrize("q", [
    "INSERT INTO meta VALUES ('a', 'b')", "DELETE FROM records", "DROP TABLE records", "PRAGMA query_only = OFF",
    "ATTACH DATABASE '/tmp/x.db' AS x", "SELECT load_extension('x')", "CREATE TEMP TABLE t AS SELECT 1",
    "SELECT 1; DELETE FROM records", "UPDATE records SET zone = 'x'",
])
def test_sql_reads_and_nothing_else(db, q):
    ro = ReadOnly(db)
    with pytest.raises(QueryError):
        ro.run(q)
    assert one(db, "SELECT COUNT(*) FROM records")[0] > 0


def test_sql_stops_a_runaway_query(db):
    ro = ReadOnly(db, seconds=0.3)
    with pytest.raises(QueryError, match="stopped"):
        ro.run("WITH RECURSIVE c(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM c) SELECT MAX(i) FROM c")
    cols, got, _ = ro.run("SELECT COUNT(*) AS n FROM sessions")
    assert cols == ["n"] and got == [(4,)]


# ------------------------------------------------------------------ the knowledge base


def test_kb_finds_game_and_guide_documents(knowledge):
    kinds = knowledge.kinds()
    assert kinds.get("quest", 0) > 1000 and kinds.get("npc", 0) > 1000 and kinds.get("note", 0) >= 2
    hit = knowledge.search("Bashal'Aran", "quest")
    assert hit and all(h["kind"] == "quest" and h["source"] for h in hit)
    doc = knowledge.get([hit[0]["id"], "quest:0"])
    assert doc[0]["body"] and doc[1] == {"id": "quest:0", "missing": True}
    # Every document says where it comes from; LLM-written notes say so.
    assert not knowledge.db.execute("SELECT COUNT(*) FROM docs WHERE source = ''").fetchone()[0]
    for n in knowledge.db.execute("SELECT label FROM docs WHERE kind = 'note'"):
        assert "LLM" in n[0]


def test_kb_game_tables_make_spell_documents():
    class Tables:
        def docs(self):
            return kb.game_docs({"SpellName": [{"ID": "133", "Name_lang": "Fireball"}],
                                 "Spell": [{"ID": "133", "Description_lang": "Hurls a fiery ball."}]},
                                "wago.tools test"), "1.60.1.70959"

    k = kb.build({}, game=Tables())
    d = k.get(["spell:133"])[0]
    assert d["title"] == "Fireball" and "fiery" in d["body"] and d["source"] == "wago.tools test"
    assert k.search("fireball", "spell")[0]["id"] == "spell:133"


def test_kb_saved_index_opens_read_only(knowledge, tmp_path):
    knowledge.save(str(tmp_path / "kb.db"))
    k = kb.KB.open(str(tmp_path / "kb.db"))
    assert len(k) == len(knowledge) and k.meta == json.loads(json.dumps(knowledge.meta, default=list))
    assert k.search("Bashal'Aran", "quest")
    toc = k.contents()
    assert "quest:" in toc and "documents (source:" in toc


# ------------------------------------------------------------------ findings


def finding(**kw):
    f = {"key": "sessions-online", "title": "Online time over {n} sessions",
         "claim": "You played {n} sessions, {online:dur} in all.",
         "sql": "SELECT COUNT(*) AS n, SUM(online_s) AS online FROM agg_session",
         "expect": {"n": 4, "online": 31880}, "relevance": 2, "effect": "online", "sample": "n",
         "holds": "n > 0",
         "action": {"do": "Keep sessions near {online:dur} over {n} evenings", "where": "anywhere",
                    "blocked_by": "", "next_step": "Plan the next session at the same length"}}
    f.update(kw)
    return f


def test_a_finding_carries_an_action_or_is_an_observation(db):
    ro = ReadOnly(db)
    vague, why = verify.check(finding(action={"do": "Play better", "where": "", "blocked_by": "", "next_step": ""}), ro)
    assert vague is None and "isn't concrete" in why
    nonext, why = verify.check(finding(action={**finding()["action"], "next_step": ""}), ro)
    assert nonext is None and "next_step" in why
    typed, why = verify.check(finding(action={**finding()["action"], "do": "Keep sessions near 9000 seconds"}), ro)
    assert typed is None and "action.do" in why
    he, why = verify.check(finding(claim="He played {n} sessions."), ro)
    assert he is None and "he or she" in why
    she, why = verify.check(finding(action={**finding()["action"], "next_step": "Ask her guild for a group"}), ro)
    assert she is None and "action.next_step" in why
    obs, why = verify.check(finding(action=None), ro)
    assert obs["observation"] and obs["action"] is None and not why
    got, _ = verify.check(finding(), ro)
    assert got["action"]["next_step"] == "Plan the next session at the same length" and not got["observation"]
    # The nightly recheck renders the action from today's numbers.
    again, _ = verify.recheck(got, ro)
    assert again["action"]["do"] == got["action"]["do"]


def test_a_finding_that_reproduces_is_kept_with_rerun_text(db):
    got, why = verify.check(finding(), ReadOnly(db))
    assert why == "" and got["text"] == "You played 4 sessions, 8h51 in all."
    assert got["row"]["n"] == 4 and got["effect"] == pytest.approx(31880.0)


@pytest.mark.parametrize("change,reason", [
    ({"expect": {"n": 5, "online": 31880}}, "on re-run"),
    ({"claim": "You played {n} sessions, 578 minutes in all."}, "number written"),
    ({"claim": "You played {n} sessions in {zones} zones."}, "doesn't return"),
    ({"holds": "n > 10"}, "holds"),
    ({"sql": "SELECT n FROM nothing"}, "failed"),
    ({"expect": {}}, "no expect"),
    ({"holds": "__import__('os').system('true')"}, "unreadable"),
])
def test_a_finding_that_doesnt_reproduce_is_dropped(db, change, reason):
    got, why = verify.check(finding(**change), ReadOnly(db))
    assert got is None and reason in why


def test_formats():
    assert verify.render("{a:dur} {b:pct} {c:int} {d:1f} {e}", {"a": 5400, "b": 0.256, "c": 12345.6, "d": 2.345,
                                                              "e": "Duskwood"}) == "1h30 26% 12 346 2.3 Duskwood"
    assert verify.fmt(None, None) == "n/a" and verify.fmt(90, "dur") == "1 min"


@pytest.mark.parametrize("line,ok", [
    ("Dungeon runs paid more per hour than questing.", True),
    ("Sessions often start late at night, after 23:00.", False),
    ("On Tuesday you played the longest.", False),
    ("You spent 40% of the time in town.", False),
    ("Most of the levelling happened in Duskwood.", False),
    ("You died to Defias Pillager twice.", False),
    ("You grouped with Someoneelse a lot.", False),
    # Nothing in the records says who plays (owner, 2026-11-18).
    ("Uses city visits for crafting, and the gear he makes himself ends up being worn.", False),
    ("Her sessions are long and steady.", False),
    ("The gear the character makes ends up being worn; there is a theme here.", True),
])
def test_shareable_lines(line, ok):
    banned = {"Duskwood", "Defias Pillager", "Someoneelse"}
    assert verify.shareable(line, banned)[0] is ok


NUDGE = {"text": "Run The Deadmines for the Smelting Pants", "where": "Westfall, the Deadmines entrance",
         "blocked_by": "level 18 (you are 16)", "next_step": "Pick up the quests in Sentinel Hill first",
         "verdict": "at_level", "at_level": 18}


@pytest.mark.parametrize("change, ok", [
    ({}, True),  # zones, dungeons, items and levels are fine on /zogzog/
    ({"next_step": ""}, False),
    ({"where": "Westfall, with Someoneelse"}, False),  # another player
    ({"text": "Kill Defias Pillager in Westfall"}, False),  # a creature from the records
    ({"where": "You are in Westfall already"}, False),  # where the character is now
    ({"next_step": "Log in at 21:30 and go to Westfall"}, False),
    ({"next_step": "Go on Friday to Sentinel Hill"}, False),
    ({"text": "Let him run The Deadmines"}, False),
    ({"blocked_by": "1250 gold"}, False),  # a number above 100 typed by hand
])
def test_shareable_actions(change, ok):
    assert verify.shareable_action({**NUDGE, **change}, {"Defias Pillager", "Someoneelse"})[0] is ok


def test_banned_names_come_from_the_records(db, knowledge):
    names = verify.banned_names(db, knowledge, {"Someoneelse"})
    assert {"Duskwood", "Ashenvale", "Someoneelse"} <= names
    assert len(names) > 50  # the creatures and NPCs met, by name from the KB


# ------------------------------------------------------------------ a client-driven run, scripted


def test_profile_counts_every_table(db):
    p = prep.profile(db)
    tables, _ = analysis.tables_of(db)
    for t in tables:
        assert f"## {t}:" in p
    assert "distinct" in p and not re.search(r"standing|unexplain|detour|wander|idle|wast", p, re.I)


FINDINGS = [
    finding(),
    {"key": "xp-by-level", "title": "XP gained at level {level}", "claim": "At level {level} you gained "
     "{xp:int} XP in {online:dur} online.", "sql": "SELECT level, xp_gained AS xp, online_s AS online FROM "
     "agg_level ORDER BY xp_gained DESC LIMIT 1", "expect": {"level": 25, "xp": 32860}, "relevance": 3,
     "kb": ["note:xp-sources", "quest:0"], "action": None},
    # The model misread: refused.
    {**finding(), "key": "misread", "expect": {"n": 7}},
]
SYNTH = {
    "keep": ["xp-by-level", "sessions-online", "misread"], "findings": [], "clashes": [], "driver": "test",
    "nudges": [{"text": "Keep the long sessions.", "gist": "Keep the long sessions.", "findings": ["sessions-online"],
                "where": "anywhere", "blocked_by": "", "next_step": "Plan the next session's length now",
                "verdict": "now", "at_level": None, "constraints": []},
               {"text": "Based on a refused finding.", "gist": "x", "findings": ["misread"], "verdict": "now",
                "where": "", "blocked_by": "", "next_step": "Do the refused thing first",
                "at_level": None, "constraints": []},
               {"text": "Play more.", "gist": "x", "findings": ["sessions-online"], "verdict": "now",
                "at_level": None, "constraints": []},
               {"text": "Let him rest.", "gist": "Rest.", "findings": ["sessions-online"], "verdict": "now",
                "where": "an inn", "blocked_by": "", "next_step": "Log out in an inn", "at_level": None,
                "constraints": []}],
    "patterns": [{"text": "Sessions are long and steady.", "findings": ["sessions-online"]},
                 {"text": "Levelling ran through Duskwood.", "findings": ["xp-by-level"]},
                 {"text": "Pattern on a refused finding.", "findings": ["misread"]}],
}
QUESTIONS = [{"question": f"Question {i} about sessions and levels?", "tables": ["agg_session", "agg_level"]}
             for i in range(12)]


def env_for(tmp_path):
    return {"ANALYSIS_STATE_DIR": str(tmp_path / "state"), "DEFAULT_CHAR": CHAR, "VL_URL": "http://vl.invalid",
            "KB_GAME_TABLES": "off"}


@pytest.fixture()
def driven(rows, tmp_path, monkeypatch):
    """The drive module on the test events, no store: (env, drive)."""
    from wowstats.analysis import drive

    monkeypatch.setattr(prep, "query", lambda store, stream, char: [dict(r) for r in rows])
    monkeypatch.setattr(analysis, "other_players", lambda store, stream, char: {"Someoneelse"})
    drive._WS.clear()
    return env_for(tmp_path), drive


def walk(env, drive, findings=FINDINGS, synth=SYNTH, extra_lane_sql=()):
    """A whole client-driven run, the way a model drives it: survey, every
    lane (one query each, findings in the first), the picks, the coverage
    lanes, the synthesis. Returns the synthesis' answer."""
    b = drive.brief(env, CHAR)
    assert b["next"]["pass"] == "survey"
    out = drive.submit(env, CHAR, "survey", {"questions": QUESTIONS})
    assert out["accepted"], out
    first = True
    while out["next"]["pass"] in ("lane", "pick"):
        nx = out["next"]
        if nx["pass"] == "pick":
            out = drive.submit(env, CHAR, "pick", {"picks": [0, 0, 99]})
            continue
        assert drive.sql(env, CHAR, f"SELECT COUNT(*) AS n FROM {nx['questions'][0]['tables'][0]}")["rows"] == 1 \
            if nx["questions"][0]["tables"] else True
        if first:
            for q in extra_lane_sql:
                drive.sql(env, CHAR, q)
            for f in findings:
                drive.propose(env, CHAR, f)
        cited = [f["key"] for f in findings] if first else []
        ans = {"lane": nx["lane"], "answers": [{"question_id": q["id"], "answered": first, "note": "n",
                                                "findings": cited} for q in nx["questions"]],
               "ask_next": [{"question": "Which levels took longest?", "why": "pace", "tables": ["levels"]}]
               if first else []}
        first = False
        out = drive.submit(env, CHAR, "lane", ans)
        assert out["accepted"], out
    assert out["next"]["pass"] == "synthesis"
    return drive.submit(env, CHAR, "synthesis", synth)


def test_an_answer_with_no_finding_behind_it_goes_back_once(driven):
    """gpt-6.1-sol, death_spike r1 and r2 (2026-11-22): a lane answered "where
    are deaths concentrated?" with "Wetlands has eight deaths" in its note and
    proposed nothing; the note never reaches the synthesis, so the kept
    analysis said nothing about deaths. An answered question has to rest on a
    finding of this run (an observation when there is nothing to act on): the
    lane goes back once, naming the question and its note; the next hand-in
    is taken as it is."""
    env, drive = driven
    drive.brief(env, CHAR)
    out = drive.submit(env, CHAR, "survey", {"questions": QUESTIONS})
    nx = out["next"]
    q1, *rest = nx["questions"]
    note = "Wetlands has eight deaths; no cause established"
    ans = {"lane": nx["lane"], "ask_next": [],
           "answers": [{"question_id": q1["id"], "answered": True, "note": note, "findings": []},
                       *({"question_id": q["id"], "answered": False, "note": "no data", "findings": []}
                         for q in rest)]}
    back = drive.submit(env, CHAR, "lane", ans)
    assert not back["accepted"] and back["next"]["lane"] == nx["lane"], back
    assert q1["id"] in back["why"] and note in back["why"] and "action null" in back["why"]
    assert back["unbacked"] == [q1["id"]]
    # A key the judge never stored backs nothing.
    ans["answers"][0]["findings"] = ["made-up"]
    assert drive.submit(env, CHAR, "lane", ans)["accepted"]  # once only: then taken as it is
    nx = drive.next_for(env, CHAR)
    # The next lane cites a stored finding: taken the first time.
    assert drive.propose(env, CHAR, finding())["stored"]
    ok = {"lane": nx["lane"], "ask_next": [],
          "answers": [{"question_id": x["id"], "answered": True, "note": "n", "findings": ["sessions-online"]}
                      for x in nx["questions"]]}
    assert drive.submit(env, CHAR, "lane", ok)["accepted"]


def test_a_finding_leaves_only_when_the_synthesis_says_why(driven):
    """claude-sonnet-5-5, death_spike (2026-11-22): the lane's verified
    "Almost half the deaths are in Wetlands" was left out of the synthesis'
    `keep`, with no reason anywhere, and the kept analysis never named the
    Wetlands. Leaving a finding out is a decision: one in `drop` (with why)
    goes, one the synthesis names nowhere stays, and the answer says so."""
    env, drive = driven
    other = {**finding(), "key": "sessions-count", "title": "{n} sessions", "claim": "You played {n} sessions.",
             "expect": {"n": 4, "online": 31880}}
    synth = {**SYNTH, "keep": ["sessions-online"], "nudges": [], "patterns": [],
             "drop": [{"key": "sessions-count", "why": "a duplicate of sessions-online"}]}
    res = walk(env, drive, findings=[*FINDINGS, other], synth=synth)
    assert res["accepted"], res
    assert res["kept"] == ["sessions-online", "xp-by-level"]  # xp-by-level: in neither list
    assert res["kept_unlisted"] == ["xp-by-level"]
    assert "sessions-count: dropped (a duplicate of sessions-online)" in res["dropped"]
    assert set(analysis.load_state(env, CHAR)["findings"]) == {"sessions-online", "xp-by-level"}


def test_a_client_run_keeps_what_reproduces(driven, rows, knowledge):
    env, drive = driven
    res = walk(env, drive)
    assert res["accepted"] and res["kept"] == ["sessions-online", "xp-by-level"]
    out = analysis.load_state(env, CHAR)
    assert out["findings"]["xp-by-level"]["text"] == "At level 25 you gained 32 860 XP in 1h24 online."
    assert out["findings"]["xp-by-level"]["kb"] == ["note:xp-sources"]  # quest:0 isn't in the KB
    run = out["runs"][-1]
    assert run["kind"] == "client" and run["driver"] == "test" and run["new"] == 2
    assert run["findings"] == {"proposed": 3, "verified": 2, "kept": 2}
    assert run["feasibility"] == {"now": 3, "at_level": 0, "not_relevant": 0, "unchecked": 0}
    w = drive.workspace(env, CHAR)
    tables, ncols = w.tables, w.ncols
    q = run["questions"]
    # Every table no survey question named got its own question; one follow-up was picked (0 twice, 99 ignored).
    assert q["added_for_coverage"] == len(tables) - 2 and q["followups"] == 1
    cov = run["coverage"]
    assert cov["tables_total"] == len(tables) and cov["columns_total"] == ncols and cov["untouched"] == []
    assert [n["text"] for n in out["nudges"]] == ["Keep the long sessions."]
    assert out["nudges"][0]["next_step"] == "Plan the next session's length now"
    assert any("Play more." in d and "no next_step" in d for d in res["dropped"])
    assert any("Let him rest." in d and "he or she" in d for d in res["dropped"])
    # Actions and observations: the one without an action is shown apart.
    act = out["findings"]["sessions-online"]["action"]
    assert act["do"] == "Keep sessions near 8h51 over 4 evenings" and not out["findings"]["sessions-online"]["observation"]
    assert out["findings"]["xp-by-level"]["observation"] and out["findings"]["xp-by-level"]["action"] is None
    # Pattern lines describe: kept for the full view, never shared (owner,
    # 2026-11-18: everything shown is actionable).
    pats = {p["text"]: p["shareable"] for p in out["patterns"]}
    assert pats == {"Sessions are long and steady.": False, "Levelling ran through Duskwood.": False}
    assert out["nudges"][0]["shareable"] is True and out["nudges"][0]["screen"] == "action"
    assert any("misread" in r for r in drive.progress(env, CHAR)["run"]["refused"])
    assert any("rests on misread" in d for d in res["dropped"])
    assert drive.progress(env, CHAR)["next"]["pass"] == "done"

    # The full view gets it all; the shared page the screened lines only.
    full = analysis.answer(env, CHAR, "full")
    assert full["available"] and full["findings"][0]["sql"] and full["last_analysis"]["coverage"]
    zz = analysis.answer(env, CHAR, "zogzog")
    # The shared page gets the nudges whole (an action: where, what blocks
    # it, the next step) and no pattern line.
    assert "patterns" not in zz
    assert zz["nudges"] == [{"text": "Keep the long sessions.", "where": "anywhere", "blocked_by": "",
                             "next_step": "Plan the next session's length now", "verdict": "now", "at_level": None}]
    assert "findings" not in zz and all("online_s" in r for r in zz["time_budget"])
    assert analysis.shared(env)["chars"][CHAR] == zz
    assert analysis.answer(env, "Someone-Else", "full")["available"] is False
    # A line an older run kept, from before the brief said "the character":
    # screened again on the way out, never shared.
    from wowstats.analysis import STATE_FILE

    st_file = analysis.char_dir(env, CHAR) / STATE_FILE
    st = json.loads(st_file.read_text())
    st["patterns"].append({"text": "The gear he makes himself ends up being worn.", "findings": [], "shareable": True})
    st["nudges"].append({**st["nudges"][0], "text": "Let him craft his gear."})
    # A nudge an older run kept with only its gist screened: never shared.
    st["nudges"].append({**st["nudges"][0], "text": "Kill Hogger again.", "screen": None})
    st_file.write_text(json.dumps(st))
    assert [n["text"] for n in analysis.answer(env, CHAR, "zogzog")["nudges"]] == ["Keep the long sessions."]
    assert [p["shareable"] for p in analysis.answer(env, CHAR, "full")["patterns"]][-1] is False
    st["patterns"].pop()
    del st["nudges"][-2:]
    st_file.write_text(json.dumps(st))

    # The next night: kept findings run again. No model.
    again = analysis.run(env, CHAR, rows=[dict(r) for r in rows], players=set(), knowledge=knowledge)
    assert again["runs"][-1]["kind"] == "recheck" and again["nudges"] == out["nudges"]


def test_a_recheck_on_the_same_data_adds_no_support(driven, rows, knowledge):
    """External review of 0.7.0-rc.5: every nightly recheck raised a
    finding's weight even when its data hadn't changed. Still applicable is
    not better supported: only new play that still satisfies it counts."""
    env, drive = driven
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding())["stored"]
    w0 = analysis.load_state(env, CHAR)["findings"]["sessions-online"]["weight"]
    nights = [analysis.run(env, CHAR, rows=[dict(r) for r in rows], players=set(), knowledge=knowledge)
              for _ in range(3)]
    f = nights[-1]["findings"]["sessions-online"]
    assert f["weight"] == w0 and f["confirmations"] == 0 and f["last_checked"] >= f["last_confirmed"]
    assert [n["runs"][-1]["still"] for n in nights] == [1, 1, 1]
    assert [n["runs"][-1]["confirmed"] for n in nights] == [0, 0, 0]
    # Proposing it again on the same data doesn't add support either.
    assert drive.propose(env, CHAR, finding())["stored"]
    assert analysis.load_state(env, CHAR)["findings"]["sessions-online"]["weight"] == w0
    # New play that still satisfies it does, once.
    last = max(rows, key=lambda r: r["_time"])
    later = [dict(r) for r in rows] + [{**last, "_time": last["_time"][:4] + "-12-31T23:00:00Z"}]
    grown = analysis.run(env, CHAR, rows=later, players=set(), knowledge=knowledge)
    assert grown["findings"]["sessions-online"]["weight"] > w0 and grown["runs"][-1]["confirmed"] == 1
    assert grown["findings"]["sessions-online"]["confirmations"] == 1
    same = analysis.run(env, CHAR, rows=[dict(r) for r in later], players=set(), knowledge=knowledge)
    assert same["findings"]["sessions-online"]["weight"] == grown["findings"]["sessions-online"]["weight"]


def test_a_run_survives_a_new_chat(driven):
    """Progress says where it stands; the brief comes in pages that fit a
    small context; play_sql pages and truncates."""
    env, drive = driven
    b = drive.brief(env, CHAR, goals="Level fast, gear up.")
    assert b["goals"] == "Level fast, gear up." and len(json.dumps(b)) < 16000
    for sec in ("schema", "profile", "kb"):
        n = b["sections"][sec]["pages"]
        pages = [drive.brief(env, CHAR, sec, p) for p in range(1, n + 1)]
        assert all(len(p["text"]) <= drive.PAGE_CHARS for p in pages)
        assert (n == 1) or "next_page" in pages[0]
    drive.submit(env, CHAR, "survey", {"questions": QUESTIONS})
    pr = drive.progress(env, CHAR)
    assert pr["run"]["pass"] == "lanes" and pr["run"]["goals"] == "Level fast, gear up."
    assert pr["next"]["lane"] == pr["run"]["lanes"]["open"][0] and pr["next"]["questions"]
    # Out of order: refused with the open lanes.
    bad = drive.submit(env, CHAR, "pick", {"picks": []})
    assert not bad["accepted"] and "lanes" in bad["why"]
    page1 = drive.sql(env, CHAR, "SELECT t FROM records ORDER BY t", page_size=10)
    page2 = drive.sql(env, CHAR, "SELECT t FROM records ORDER BY t", page=2, page_size=10)
    assert page1["more"] and page1["rows"] == 10 and page1["result"] != page2["result"]
    assert "error" in drive.sql(env, CHAR, "SELECT t FROM records", page=200, page_size=10)
    assert drive.sql(env, CHAR, "DELETE FROM records")["error"]
    # The goals stay with the character for the next run.
    drive.brief(env, CHAR, restart=True)
    assert drive.progress(env, CHAR)["run"]["goals"] == "Level fast, gear up."


def test_a_finding_that_stops_holding_fades_then_goes(driven, rows, knowledge):
    env, drive = driven
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding(holds="n >= 4"))["stored"]
    # Data where it no longer holds (one session): it fades, then goes.
    first = [dict(r) for r in rows if r["_time"] < "2026-02-05"]
    weights = []
    for _ in range(3):
        out = analysis.run(env, CHAR, rows=[dict(r) for r in first], players=set(), knowledge=knowledge)
        weights.append(out["findings"].get("sessions-online", {}).get("weight"))
    assert weights == [0.5, 0.25, None]
    assert any("sessions-online: faded" in d for d in out["runs"][-1]["dropped"])


def test_state_is_per_character_and_the_old_file_moves(driven):
    env, drive = driven
    old = {"schema": analysis.SCHEMA, "char": CHAR, "findings": {}, "runs": []}
    d = analysis.state_dir(env)
    d.mkdir(parents=True)
    (d / analysis.STATE_FILE).write_text(json.dumps(old))
    assert analysis.load_state(env, CHAR)["goals"] == analysis.LEGACY_GOALS
    assert analysis.load_state(env, "Other-Char") == {}
    assert analysis.characters(env) == [CHAR]
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding())["stored"]
    assert not (d / analysis.STATE_FILE).exists()
    assert (analysis.char_dir(env, CHAR) / analysis.STATE_FILE).exists()
    assert analysis.load_state(env, CHAR)["goals"] == analysis.LEGACY_GOALS


def test_nightly_schedule():
    tz = prep.TZ
    last = datetime(2026, 11, 17, 3, 30, tzinfo=UTC).isoformat()
    assert analysis.due({}, None)
    assert not analysis.due({}, last, datetime(2026, 11, 18, 1, 0, tzinfo=tz))
    assert analysis.due({}, last, datetime(2026, 11, 18, 4, 5, tzinfo=tz))
    assert not analysis.due({}, datetime(2026, 11, 18, 3, 10, tzinfo=UTC).isoformat(),
                            datetime(2026, 11, 18, 6, 0, tzinfo=tz))


def test_the_allowlist_names_every_source(db):
    from wowstats.mcp import tools

    # Every prep table is classified, and nothing else may be read.
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == set(analysis.TABLES)
    ro = ReadOnly(db, tables={"sessions"})
    assert ro.run("SELECT COUNT(*) FROM sessions")[1] == [(4,)]
    with pytest.raises(QueryError):
        ro.run("SELECT COUNT(*) FROM records")
    with pytest.raises(QueryError):
        ro.run("WITH x AS (SELECT * FROM records) SELECT COUNT(*) FROM x")
    # Battle.net data reaches no model: the MCP never lists `market`.
    assert "market" not in tools.BY_NAME


def test_the_brief_holds_no_hint(driven):
    """Neutral prep: every section is the schema, the profile, the tools,
    the player's words and the pass' task. No hypothesis, no category."""
    env, drive = driven
    texts = []
    for sec in drive.SECTIONS:
        b = drive.brief(env, CHAR, sec)
        texts.append(json.dumps(b, ensure_ascii=False))
        for p in range(2, (b.get("pages") or 1) + 1):
            texts.append(json.dumps(drive.brief(env, CHAR, sec, p), ensure_ascii=False))
    rest = "\n".join(texts)
    # Whole words: game data in the profile (an item called "Treaty of Understanding") isn't a lead.
    m = re.search(r"\b(standing|unexplain\w*|detour|wander(ing)?|idle|wast(e|ed|ing))\b", rest, re.I)
    assert not m, rest[max(0, m.start() - 80):m.end() + 40] if m else ""


def test_clashing_findings_are_merged_or_doubtful(driven):
    env, drive = driven
    online = {"key": "sessions-online2", "title": "Sessions", "claim": "Your {n:int} sessions.",
              "sql": "SELECT COUNT(*) AS n FROM sessions", "expect": {"n": 4}, "relevance": 2}
    merged = {**online, "key": "sessions-defined", "claim": "Counting every login span: {n:int} sessions."}
    clash = {"subject": "sessions", "findings": ["sessions-online", "sessions-online2"],
             "query": "SELECT COUNT(*) FROM sessions"}

    res = walk(env, drive, [*FINDINGS, online], {**SYNTH, "clashes": [{**clash, "outcome": "doubtful"}]})
    out = analysis.load_state(env, CHAR)
    assert out["findings"]["sessions-online"]["doubtful"] and out["findings"]["sessions-online2"]["doubtful"]
    assert out["nudges"] == []  # the only nudge stood on a doubtful finding
    assert [p["text"] for p in out["patterns"]] == ["Levelling ran through Duskwood."]
    assert res["clashes"] == {"found": 1, "merged": 0, "doubtful": 1}

    drive.brief(env, CHAR, restart=True)
    env2 = {**env, "ANALYSIS_STATE_DIR": env["ANALYSIS_STATE_DIR"] + "-b"}
    res = walk(env2, drive, [*FINDINGS, online], {**SYNTH, "findings": [merged],
                                                  "clashes": [{**clash, "outcome": "merged",
                                                               "merged_key": "sessions-defined"}]})
    out = analysis.load_state(env2, CHAR)
    assert "sessions-defined" in out["findings"] and not {"sessions-online", "sessions-online2"} & set(out["findings"])
    assert out["findings"]["sessions-defined"]["reconciles"] == ["sessions-online", "sessions-online2"]
    assert res["clashes"] == {"found": 1, "merged": 1, "doubtful": 0}
    assert {"sessions-online2: merged after a clash", "sessions-online: merged after a clash",
            'nudge "Based on a refused finding.": rests on misread (not kept)',
            'pattern "Pattern on a refused finding.": rests on misread (not kept)'} <= set(res["dropped"])
    # The lines that cited a merged finding now stand on the merged one.
    assert [(n["text"], n["findings"]) for n in out["nudges"]] == [("Keep the long sessions.", ["sessions-defined"])]
    assert "Sessions are long and steady." in [p["text"] for p in out["patterns"]]


def test_nudges_pass_a_feasibility_check():
    def n(text, verdict, at=None, kb=("note:xp-sources",)):
        return {"text": text, "gist": text, "findings": ["k"], "verdict": verdict, "at_level": at,
                "constraints": [{"what": "needs a level", "kb": list(kb)}] if kb else []}

    raw = [n("a", "now", kb=()), n("b", "at_level", 40), n("c", "at_level", 30), n("d", "not_relevant"),
           n("e", "now", kb=("quest:0",)), n("f", "at_level", None)]
    out, counts = analysis.feasible(raw, 31, lambda i: i == "note:xp-sources")
    assert [(x["text"], x["verdict"], x["at_level"]) for x in out] == [
        ("a", "now", None), ("b", "at_level", 40), ("c", "now", None)]
    assert counts == {"now": 2, "at_level": 1, "not_relevant": 1, "unchecked": 2}
    assert out[1]["constraints"] == [{"what": "needs a level", "kb": ["note:xp-sources"]}]


def test_a_dungeon_trip_spans_the_lookup_guides():
    """One recorded trip (pickup guide, the instance, hand-in guide), trimmed
    to the records the trip needs: its boundaries and its quests."""
    import gzip
    from pathlib import Path

    raw = gzip.decompress((Path(__file__).parent / "stockade_trip.jsonl.gz").read_bytes()).decode()
    trip_rows = [json.loads(x) for x in raw.splitlines()]
    db = prep.build(trip_rows, "Testpriest-Fixture")
    cols = [d[1] for d in db.execute("PRAGMA table_info(dungeon_trips)")]
    trips = [dict(zip(cols, r, strict=True)) for r in db.execute("SELECT * FROM dungeon_trips")]
    assert len(trips) == 1
    t = trips[0]
    assert t["bracket"] == "guides" and t["level_start"] == 31
    assert t["start_local"] == "2026-10-14T19:02:00+02:00"  # the first pickup step
    assert t["end_local"] == "2026-10-14T19:45:59+02:00"  # the last hand-in step (after the walk to Darkshire's flight master)
    assert json.loads(t["quest_ids"]) == [377, 378, 386, 387, 388, 391]
    assert t["quests_handed_in"] == 6 and t["inside_s"] == 1376
    assert abs(t["pickup_s"] + t["travel_to_s"] + t["inside_s"] + t["travel_back_s"] + t["handin_s"]
               - t["online_s"]) < 1
    assert json.loads(t["rewards"]) == [1317, 270029, 270036]
    assert set(json.loads(t["still_worn"])) <= set(json.loads(t["rewards"])) | {i for i, _ in json.loads(
        t["loot_inside"])}
    assert t["gold_after_copper"] is not None
    assert any(s.startswith("lookup/pickup/") for s in json.loads(t["steps"]))
    inside = db.execute("SELECT COUNT(*) FROM records WHERE dungeon_trip_id = 1").fetchone()[0]
    outside = db.execute("SELECT COUNT(*) FROM records WHERE dungeon_trip_id IS NULL").fetchone()[0]
    assert inside > 0 and outside > 0
    assert db.execute("SELECT COUNT(*) FROM timeline WHERE dungeon_trip_id = 1").fetchone()[0] > 0


def _seed_rows(tmp_path):
    import subprocess
    import sys

    out = tmp_path / "seed.ndjson"
    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, str(root / "dev" / "seed.py"), "--out", str(out)], check=True,
                   capture_output=True)
    return [json.loads(x) for x in out.read_text().splitlines() if x.strip()]


@pytest.mark.parametrize("session_levels", [True, False])
def test_time_per_level_matches_the_splits(tmp_path, session_levels):
    """The blog question on the demo ("which level took longest, and how much
    of it in a dungeon?"): the timeline's time per level is Polestar's split.
    Level 22's evening was booked as 23 when the seed's session records had
    no level and the first mark was the ding to 23 (2026-11-19)."""
    rows = [r for r in _seed_rows(tmp_path) if r.get("char") == "Testchar-Demo"]
    if not session_levels:  # records that start mid-level, no session level: the ding alone says it
        rows = [{k: v for k, v in r.items() if not (r.get("kind") == "session" and k == "level")} for r in rows]
    db = prep.build(rows, "Testchar-Demo", route.reference())
    online = dict(db.execute("SELECT level, SUM(dur_s) FROM timeline GROUP BY level"))
    splits = {int(r["level"]): int(r["seconds"]) for r in rows if r["feed"] == "wow-level"}
    for level, secs in splits.items():
        assert abs(online.get(level, 0) - secs) <= 120, (level, online.get(level), secs)
    slowest = max(splits, key=splits.get)
    assert slowest == 30 and max(online, key=online.get) == 30
    inside = one(db, "SELECT SUM(dur_s) FROM timeline WHERE level = 30 AND instance IS NOT NULL")[0]
    assert abs(inside - 2650) <= 60  # Scarlet Monastery: Library, 44 min


def test_play_sql_keeps_every_digit():
    from wowstats.analysis.sql import as_text

    text = as_text(["s", "f", "tiny", "big"], [(13734.0, 2667.25, 0.00012, 1234567.0)], False)
    assert text.splitlines()[1] == "13734\t2667.25\t0.00012\t1234567"


# ---------------------------------------------------------------- the character's folder (review info 5)


def test_names_that_fold_alike_keep_their_own_analysis(tmp_path):
    """Ærin and Érin of one realm both folded to `_rin-…` before 0.7 and
    shared one analysis; now each has its own folder."""
    from wowstats.analysis import SCHEMA

    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    a, e = "Ærin-Firemaw", "Érin-Firemaw"
    assert analysis.char_dir(env, a) != analysis.char_dir(env, e)
    assert analysis.char_dir(env, a) == analysis.char_dir(env, "ærin firemaw")  # one name, one folder
    # An É typed as E + combining accent is the same character.
    assert analysis.char_dir(env, "Érin-Firemaw") == analysis.char_dir(env, e)
    analysis.save_state(env, a, {"schema": SCHEMA, "char": a, "findings": {"x": {}}})
    analysis.save_state(env, e, {"schema": SCHEMA, "char": e, "findings": {}})
    assert analysis.load_state(env, a)["findings"] == {"x": {}}
    assert analysis.load_state(env, e)["char"] == e
    assert analysis.characters(env) == sorted([a, e])


def test_a_folder_from_before_moves_to_its_new_name(tmp_path):
    """A pre-0.7 folder moves when its analysis names the character, or when
    the name had nothing to fold; a folded folder another character's
    analysis names stays put."""
    from wowstats.analysis import SCHEMA, STATE_FILE

    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    chars = tmp_path / "chars"

    def old(folder: str, char: str | None, run: bool = False):
        (chars / folder).mkdir(parents=True)
        if char:
            (chars / folder / STATE_FILE).write_text(json.dumps({"schema": SCHEMA, "char": char, "findings": {}}))
        if run:
            (chars / folder / "drive.json").write_text('{"id": "r1"}')

    old("testpriest-fixture", None, run=True)  # a run only, nothing to fold: it's this character's
    old("_rin-firemaw", "Ærin-Firemaw")
    assert analysis.load_state(env, "Érin-Firemaw") == {}  # not Érin's: stays
    assert (chars / "_rin-firemaw").is_dir()
    assert analysis.load_state(env, "Ærin-Firemaw")["char"] == "Ærin-Firemaw"
    assert not (chars / "_rin-firemaw").exists()
    d = analysis.char_dir(env, "Testpriest-Fixture")
    assert (d / "drive.json").is_file() and not (chars / "testpriest-fixture").exists()
    assert d.name.startswith("testpriest-fixture.")


def test_a_nudge_with_placeholders_is_rendered_again_at_the_new_level():
    kept = {"lvl-gap": {"row": {"level": 32, "behind": 4}}}
    nudge = {"text": "You are {behind} levels behind level {level}", "findings": ["lvl-gap"], "verdict": "now"}
    dropped: list = []
    out = analysis.bind([nudge], kept, 32, dropped, "nudge")
    assert out[0]["text"] == "You are 4 levels behind level 32"
    kept["lvl-gap"]["row"] = {"level": 33, "behind": 5}
    out = analysis.bind(out, kept, 33, dropped, "nudge")
    assert out[0]["text"] == "You are 5 levels behind level 33" and not dropped
    assert "written_level" not in out[0]


def test_a_typed_level_goes_once_the_character_is_past_it():
    dropped: list = []
    n = {"text": "Well behind level 32 for this zone", "findings": [], "verdict": "now"}
    out = analysis.bind([n], {}, 32, dropped, "nudge")
    assert out and out[0]["written_level"] == 32
    assert analysis.bind(out, {}, 32, dropped, "nudge")  # same level: stays
    assert analysis.bind(out, {}, 33, dropped, "nudge") == []
    assert "written at level 32" in dropped[0]
    # a line that quotes nothing is timeless
    t = {"text": "Turn in the Stockade quest", "findings": []}
    assert analysis.bind(analysis.bind([t], {}, 32, [], "nudge"), {}, 40, [], "nudge")


def test_a_nudge_whose_placeholder_lost_its_value_goes():
    dropped: list = []
    n = {"text": "Do it at {level}", "findings": ["gone"]}
    assert analysis.bind([n], {}, 33, dropped, "nudge") == [] and dropped


def test_an_at_level_nudge_becomes_now_when_reached():
    n = {"text": "Go", "findings": [], "verdict": "at_level", "at_level": 33}
    assert analysis.bind([n], {}, 33, [], "nudge")[0]["verdict"] == "now"


def test_a_legacy_run_without_kind_counts_as_the_client_run(tmp_path):
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    state = {"schema": analysis.SCHEMA, "char": "Testchar", "findings": {}, "runs": [
        {"at": "2026-11-18T10:00:00+00:00", "new": 3, "confirmed": 0, "faded": 0},
        {"at": "2026-11-19T04:00:00+00:00", "kind": "recheck", "confirmed": 3, "faded": 0}]}
    analysis.save_state(env, "Testchar", state)
    got = analysis.answer(env, "Testchar")
    assert got["last_analysis"] and got["last_analysis"]["new"] == 3


# ------------------------------------------------------------------ run manifest (review 4)


@pytest.fixture
def semantic_edit(tmp_path, monkeypatch):
    """Call it to load a copy of semantic.yaml with one description changed
    as the model from then on; the real one is back after the test."""
    from wowstats.analysis import manifest, semantic

    def clear():
        for f in (semantic._file, semantic.spec, manifest._semantic_hash):
            f.cache_clear()

    def edit():
        edited = tmp_path / "semantic.yaml"
        edited.write_text(semantic.MODEL_FILE.read_text().replace('description: "', 'description: "(edited) ', 1))
        monkeypatch.setattr(semantic, "MODEL_FILE", edited)
        clear()

    yield edit
    monkeypatch.undo()
    clear()


def test_a_finding_keeps_the_build_it_was_judged_under(driven, rows, knowledge):
    from wowstats.analysis import manifest

    env, drive = driven
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding())["stored"]
    st = analysis.load_state(env, CHAR)
    m = st["manifests"][st["findings"]["sessions-online"]["manifest"]]
    assert m["prep_version"] == prep.PREP_VERSION and len(m["semantic_hash"]) == 16
    assert m["input"]["events"] == len(rows) and m["input"]["newest_event"]
    assert m["reference"]["kb_polestar"] and m["reference"]["route"] and m["reference"]["gear"]
    assert m["settings"]["timezone"] == prep.TZ_NAME and m["settings"]["session_gap_s"] == prep.OFFLINE_S
    assert m["built_at"] and m["freshness"]["lag_s"] >= 0
    # Unchanged definitions: the recheck runs it as before.
    out = analysis.run(env, CHAR, rows=[dict(r) for r in rows], players=set(), knowledge=knowledge)
    f = out["findings"]["sessions-online"]
    assert out["runs"][-1]["still"] == 1 and out["runs"][-1]["revalidate"] == 0 and "revalidate" not in f
    assert manifest.changed(out["manifests"][out["runs"][-1]["manifest"]], m) == []
    a = analysis.answer(env, CHAR)
    assert a["checked"] == 1 and a["to_revalidate"] == 0


def test_a_semantic_change_means_to_revalidate_never_a_silent_pass(driven, rows, knowledge, semantic_edit):
    env, drive = driven
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding())["stored"]
    w0 = analysis.load_state(env, CHAR)["findings"]["sessions-online"]["weight"]
    semantic_edit()
    for _ in range(2):  # every night until re-judged, not just the first
        out = analysis.run(env, CHAR, rows=[dict(r) for r in rows], players=set(), knowledge=knowledge)
        f, info = out["findings"]["sessions-online"], out["runs"][-1]
        assert f["revalidate"]["why"] == ["the semantic model changed"]
        assert info["revalidate"] == 1 and info["still"] == 0 and info["confirmed"] == 0 and info["faded"] == 0
        assert f["weight"] == w0
    # The page and MCP's play_analysis (analysis.answer) say so and don't count it as checked.
    a = analysis.answer(env, CHAR)
    assert a["to_revalidate"] == 1 and a["checked"] == 0 and a["findings"][0]["revalidate"]["why"]
    assert drive.progress(env, CHAR)["to_revalidate"] == 1
    # The next analysis is told which ones, then re-judges under today's model.
    drive._WS.clear()
    b = drive.brief(env, CHAR, restart=True)
    assert [x["key"] for x in b["to_revalidate"]["findings"]] == ["sessions-online"]
    assert drive.propose(env, CHAR, finding())["stored"]
    assert "revalidate" not in analysis.load_state(env, CHAR)["findings"]["sessions-online"]
    out = analysis.run(env, CHAR, rows=[dict(r) for r in rows], players=set(), knowledge=knowledge)
    assert out["runs"][-1]["still"] == 1 and out["runs"][-1]["revalidate"] == 0
    assert analysis.answer(env, CHAR)["checked"] == 1


def test_a_prep_version_or_reference_change_means_to_revalidate(driven, rows, knowledge, monkeypatch):
    from wowstats.analysis import manifest

    env, drive = driven
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding())["stored"]
    monkeypatch.setattr(prep, "PREP_VERSION", prep.PREP_VERSION + 1)
    out = analysis.run(env, CHAR, rows=[dict(r) for r in rows], players=set(), knowledge=knowledge)
    assert out["findings"]["sessions-online"]["revalidate"]["why"] == [
        f"prep version {prep.PREP_VERSION - 1} -> {prep.PREP_VERSION}"]
    monkeypatch.setattr(prep, "PREP_VERSION", prep.PREP_VERSION - 1)
    real = manifest.reference_versions
    monkeypatch.setattr(manifest, "reference_versions", lambda *a: {**real(*a), "route": "v9.9.9"})
    out = analysis.run(env, CHAR, rows=[dict(r) for r in rows], players=set(), knowledge=knowledge)
    assert any(w.startswith("route ") for w in out["findings"]["sessions-online"]["revalidate"]["why"])


def legacy(env, drive, **kw):
    """A finding kept by 0.6: no manifest, no support mark (#49 came later)
    and, after #78's first recheck, maybe already marked to revalidate."""
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding())["stored"]
    st_file = analysis.char_dir(env, CHAR) / analysis.STATE_FILE
    st = json.loads(st_file.read_text())
    f = st["findings"]["sessions-online"]
    for k in ("manifest", "support_mark"):
        f.pop(k, None)
    f.update(kw)
    st.pop("manifests")
    st_file.write_text(json.dumps(st))
    return f


def recheck_night(env, rows, knowledge):
    return analysis.run(env, CHAR, rows=[dict(r) for r in rows], players=set(), knowledge=knowledge)


def test_a_finding_kept_before_manifests_is_carried_over_once_when_it_holds(driven, rows, knowledge):
    from wowstats import __version__

    env, drive = driven
    before = legacy(env, drive, revalidate={"since": "2026-11-20T04:00:00+00:00",
                                            "why": ["judged before builds kept a manifest"]})
    out = recheck_night(env, rows, knowledge)
    f, info = out["findings"]["sessions-online"], out["runs"][-1]
    assert f["manifest"] == info["manifest"] and f["manifest"] in out["manifests"]
    assert f["carried_over"]["from"] == "0.6" and f["carried_over"]["to"] == __version__ and f["carried_over"]["held"]
    assert "revalidate" not in f and f["last_checked"] == info["at"]
    # Checked, with no weight gained for the carry-over itself (#49).
    assert info["carried_over"] == 1 and info["confirmed"] == 0 and info["still"] == 0 and info["revalidate"] == 0
    assert f["weight"] == before["weight"] and f["confirmations"] == before["confirmations"]
    a = analysis.answer(env, CHAR)
    assert a["checked"] == 1 and a["to_revalidate"] == 0
    assert a["findings"][0]["carried_note"] == f"Checked against {__version__} data, first judged under 0.6."
    # Once: the next night is an ordinary recheck, same data, same weight.
    out = recheck_night(env, rows, knowledge)
    f2, info = out["findings"]["sessions-online"], out["runs"][-1]
    assert info["carried_over"] == 0 and info["still"] == 1 and info["confirmed"] == 0
    assert f2["carried_over"] == f["carried_over"] and f2["weight"] == before["weight"]


def test_a_legacy_finding_whose_sql_fails_fades_and_is_not_tried_again(driven, rows, knowledge):
    env, drive = driven
    before = legacy(env, drive, sql="SELECT COUNT(*) AS n, SUM(online_s) AS online FROM agg_session WHERE 0")
    out = recheck_night(env, rows, knowledge)
    f, info = out["findings"]["sessions-online"], out["runs"][-1]
    assert info["faded"] == 1 and info["carried_over"] == 0
    assert f["misses"] == 1 and f["weight"] == round(before["weight"] * 0.5, 4) and f["miss_why"]
    assert not f.get("manifest") and f["carried_over"]["held"] is False
    assert "carried_note" not in analysis.answer(env, CHAR)["findings"][0]
    # Tried once: the next night it is to revalidate, not run again.
    out = recheck_night(env, rows, knowledge)
    f, info = out["findings"]["sessions-online"], out["runs"][-1]
    assert info["revalidate"] == 1 and info["faded"] == 0 and f["misses"] == 1
    assert f["revalidate"]["why"] == ["judged before builds kept a manifest"]


def test_a_carried_over_finding_goes_to_revalidate_on_a_later_semantic_change(driven, rows, knowledge,
                                                                               semantic_edit):
    env, drive = driven
    legacy(env, drive)
    assert recheck_night(env, rows, knowledge)["runs"][-1]["carried_over"] == 1
    semantic_edit()
    out = recheck_night(env, rows, knowledge)
    f, info = out["findings"]["sessions-online"], out["runs"][-1]
    assert f["revalidate"]["why"] == ["the semantic model changed"]
    assert info["revalidate"] == 1 and info["carried_over"] == 0 and info["still"] == 0
