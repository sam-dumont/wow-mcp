"""A finding's prose says only numbers its query gives (property 25).

External review, 2026-11-22 (pinned to a856e2d): with 5 deaths on record,
"You died 3 times." proposed with `SELECT COUNT(*) AS n FROM records WHERE
kind='death'` (n = 5) was kept, and every reader showed the wrong prose next
to the right row: bare numbers up to 100 were allowed in the text, so the
judge never tied the prose's quantities to the result.

Now every measured number is a placeholder the server renders from the
result, at accept time and at every recheck; any other number is declared in
`constants` with its kind. A finding kept before is migrated, never
deleted: one whose text types a number nothing binds or declares is marked
to revalidate, on the page and in the MCP. And the nightly recheck that keeps
the rendered numbers current runs for every person on the server, one after
another, with no model; it ran for the owner only before.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime

from tests.analysis import test_auto as AU
from tests.analysis import test_drive as TD
from tests.mcp import inmem
from wowstats import analysis
from wowstats.analysis import auto, drive, load_state, prep, providers, verify
from wowstats.mcp import tools

CHAR = TD.CHAR
env = TD.env  # the fixture: the test events, a state folder, no store
lab, rows = AU.lab, AU.rows
DEATHS = "SELECT COUNT(*) AS n FROM records WHERE kind='death'"


def _events() -> list[dict]:
    with gzip.open(TD.DATA) as f:
        return [json.loads(x) for x in f]


def _ctx():
    return tools.Ctx(store=TD.FakeStore(), default_char=CHAR)


def _propose(finding: dict) -> dict:
    """propose_finding through the real FastMCP client (middleware included)."""
    r = inmem.call(_ctx(), "propose_finding", {"char": CHAR, "finding": finding})
    assert not r.is_error, r
    return r.structured_content


def _deaths(**kw) -> dict:
    f = {"key": "deaths-count", "title": "Deaths", "claim": "You died {n} times.", "sql": DEATHS,
         "expect": {"n": 5}, "relevance": 1, "action": None}
    f.update(kw)
    return f


def test_the_reviewers_reproduction_is_refused(env):
    assert drive.workspace(env, CHAR).judge.first_row(DEATHS)["n"] == 5
    got = _propose(_deaths(claim="You died 3 times."))
    assert not got["stored"] and "3 in the claim is neither a placeholder nor a declared constant" in got["why"]
    assert "deaths-count" not in (load_state(env, CHAR).get("findings") or {})
    read = inmem.call(_ctx(), "play_analysis", {"char": CHAR}).structured_content
    assert not any(f["key"] == "deaths-count" for f in read.get("findings") or [])
    # Declaring the count as a constant doesn't get it in either: a level or
    # a top-N is a parameter of the query, and a count is no kind at all.
    for c, why in (({"value": 3, "label": "level"}, "isn't in the query"),
                   ({"value": 3, "label": "top-n"}, "isn't in the query"),
                   ({"value": 3, "label": "game-rule"}, "cite in `kb`"),
                   ({"value": 3, "label": "deaths"}, "isn't one of")):
        bad = _propose(_deaths(claim="You died 3 times.", constants=[c]))
        assert not bad["stored"] and why in bad["why"], (c, bad)
    # The same claim bound to the query stands, and says the query's number.
    ok = _propose(_deaths())
    assert ok["stored"] and ok["finding"]["text"] == "You died 5 times." and ok["finding"]["row"] == {"n": 5}
    read = inmem.call(_ctx(), "play_analysis", {"char": CHAR}).structured_content
    f = next(f for f in read["findings"] if f["key"] == "deaths-count")
    assert f["text"] == "You died 5 times." and f["last_checked"] and read["refresh"]["recheck"] == "nightly"


def test_placeholders_render_now_and_again_after_a_recheck(env):
    """Cells too: {rows[1].col} names a later row. A recheck on more play
    renders the new value; the template is what's kept."""
    knowledge = __import__("wowstats.analysis.kb", fromlist=["build"]).build({"KB_GAME_TABLES": "off"})
    by_zone = ("SELECT zone, COUNT(*) AS n FROM records WHERE kind='death' GROUP BY zone ORDER BY n DESC, zone")
    zones = drive.workspace(env, CHAR).judge.run(by_zone, 5)[1]
    assert len(zones) >= 2, zones
    f = {"key": "deaths-by-zone", "title": "Deaths in {zone}",
         "claim": "You died {n} times in {zone}, then {rows[1].n} in {rows[1].zone}.", "sql": by_zone,
         "expect": {"n": zones[0][1]}, "relevance": 1, "action": None}
    got = _propose(f)
    assert got["stored"], got
    want = f"You died {zones[0][1]} times in {zones[0][0]}, then {zones[1][1]} in {zones[1][0]}."
    assert got["finding"]["text"] == want
    assert _propose(_deaths())["stored"]
    kept = load_state(env, CHAR)["findings"]
    assert kept["deaths-by-zone"]["claim_template"] == f["claim"] and kept["deaths-by-zone"]["rows"][1]["n"] == \
        zones[1][1]
    # One more death in the top zone, a second later than the last one: the
    # nightly recheck renders the new counts, no model.
    events = [dict(r) for r in _events()]
    last = max((r for r in events if r.get("kind") == "death" and r.get("zone") == zones[0][0]),
               key=lambda r: r["_time"])
    more = events + [{**last, "_time": last["_time"][:-5] + "9.900Z"}]
    out = analysis.run(env, CHAR, rows=more, players=set(), knowledge=knowledge)
    assert out["findings"]["deaths-count"]["text"] == "You died 6 times."
    assert out["findings"]["deaths-by-zone"]["text"].startswith(f"You died {zones[0][1] + 1} times in {zones[0][0]}")
    read = analysis.answer(env, CHAR)
    assert {f["key"]: f["text"] for f in read["findings"]}["deaths-count"] == "You died 6 times."


def test_declared_constants_stand_and_undeclared_numbers_dont(env):
    top = ("SELECT zone, COUNT(*) AS n FROM records WHERE kind='death' GROUP BY zone ORDER BY n DESC, zone "
           "LIMIT 3")
    n0 = drive.workspace(env, CHAR).judge.first_row(top)["n"]
    f = {"key": "top-death-zones", "title": "Where you die",
         "claim": "Of your top 3 death zones, {zone} comes first with {n}.", "sql": top, "expect": {"n": n0},
         "relevance": 1, "action": None}
    bare = _propose(f)
    assert not bare["stored"] and "3 in the claim is neither" in bare["why"]
    declared = _propose({**f, "constants": [{"value": 3, "label": "top-N"}]})
    assert declared["stored"], declared
    assert declared["finding"]["constants"] == [{"value": 3, "label": "top-n"}]
    # A number in the action is held to the same rule.
    act = {"do": "Go back to {zone} with a 5-player group", "where": "{zone}", "blocked_by": "",
           "next_step": "Ask in the trade channel before going back"}
    assert "5 in the action.do is neither" in _propose({**f, "key": "act-bare", "action": act,
                                                        "constants": [{"value": 3, "label": "top-n"}]})["why"]
    assert _propose({**f, "key": "act-ok", "action": act, "constants": [
        {"value": 3, "label": "top-n"}, {"value": 5, "label": "group-size"}]})["stored"]
    # An unknown kind, or a constant equal to a placeholder's value, is refused.
    assert "isn't one of" in _propose({**f, "key": "kind-bad", "constants": [{"value": 3, "label": "deaths"}]})["why"]
    same = _propose({**f, "key": "same", "claim": f"{{zone}} has {{n}} of your deaths, {n0} in all.",
                     "constants": [{"value": n0, "label": "threshold"}]})
    assert not same["stored"] and "equals {n}" in same["why"]
    # One labelled like the column a placeholder reads, with another value, contradicts it.
    lvl = "SELECT level, COUNT(*) AS n FROM records WHERE kind='death' GROUP BY level ORDER BY n DESC LIMIT 1"
    row = drive.workspace(env, CHAR).judge.first_row(lvl)
    other = row["level"] + 7
    clash = _propose({"key": "clash", "title": "Deaths by level",
                      "claim": f"Most deaths came at level {{level}} ({{n}}), none past level {other}.", "sql": lvl,
                      "expect": {"n": row["n"]}, "relevance": 1, "action": None,
                      "constants": [{"value": other, "label": "level"}]})
    assert not clash["stored"] and "contradicts {level}" in clash["why"]


def _legacy_state(env) -> None:
    """A state saved before the contract: one finding whose text types a
    number nothing binds, one whose numbers are all placeholders, and a
    nudge on the first."""
    from wowstats.analysis import SCHEMA

    base = {"sql": DEATHS, "sql_params": None, "engine": "sqlite", "expect": {"n": 5}, "holds": None,
            "row": {"n": 5}, "kb": [], "action": None, "action_template": None, "observation": True,
            "relevance": 1, "weight": 1.0, "score": 1.0, "first_seen": "2026-11-01T00:00:00+00:00",
            "last_checked": "2026-11-02T04:00:00+00:00"}
    findings = {
        "deaths-typed": {**base, "key": "deaths-typed", "title": "Deaths", "title_template": "Deaths",
                         "text": "You died 3 times.", "claim_template": "You died 3 times."},
        "deaths-bound": {**base, "key": "deaths-bound", "title": "Deaths", "title_template": "Deaths",
                         "text": "You died 5 times.", "claim_template": "You died {n} times."}}
    nudge = {"text": "Rest before the next pull.", "gist": "Rest.", "where": "anywhere", "blocked_by": "",
             "next_step": "Sit down after each fight", "findings": ["deaths-typed"], "verdict": "now",
             "shareable": True, "screen": "action"}
    analysis.save_state(env, CHAR, {"schema": SCHEMA, "char": CHAR, "findings": findings, "nudges": [nudge],
                                    "patterns": [], "runs": [{"at": "2026-11-01T00:00:00+00:00",
                                                              "kind": "client", "new": 2}]})


def test_a_legacy_finding_with_unbound_numbers_is_flagged_never_dropped(env):
    from wowstats.analysis import STATE_FILE

    _legacy_state(env)
    before = json.loads((analysis.char_dir(env, CHAR) / STATE_FILE).read_text())
    s = load_state(env, CHAR)
    assert analysis.migrate(json.loads(json.dumps(before))) == 1
    typed, bound = s["findings"]["deaths-typed"], s["findings"]["deaths-bound"]
    assert typed["unbound_numbers"] == ["3"] and typed["revalidate"]["why"][0].startswith(verify.UNBOUND_WHY)
    assert "unbound_numbers" not in bound and "revalidate" not in bound
    # Nothing lost: every field kept is still there, with its value.
    for k, f in before["findings"].items():
        assert all(s["findings"][k][x] == v for x, v in f.items()), k
    # The page's answer and the MCP say so; the nudge on it is held.
    for read in (analysis.answer(env, CHAR),
                 inmem.call(_ctx(), "play_analysis", {"char": CHAR}).structured_content):
        f = {x["key"]: x for x in read["findings"]}
        assert f["deaths-typed"]["revalidate"]["why"][0] == f"{verify.UNBOUND_WHY} (3)"
        assert f["deaths-typed"]["text"] == "You died 3 times." and not f["deaths-bound"].get("revalidate")
        assert read["to_revalidate"] == 1 and read["on_hold"][0]["waits_on"] == ["deaths-typed"]
    # The nightly recheck keeps it, flagged, without re-rendering it, and saves the migration.
    knowledge = __import__("wowstats.analysis.kb", fromlist=["build"]).build({"KB_GAME_TABLES": "off"})
    out = analysis.run(env, CHAR, rows=[dict(r) for r in _events()], players=set(), knowledge=knowledge)
    assert out["findings"]["deaths-typed"]["text"] == "You died 3 times."
    assert out["findings"]["deaths-typed"]["revalidate"]["why"][0] == f"{verify.UNBOUND_WHY} (3)"
    saved = json.loads((analysis.char_dir(env, CHAR) / STATE_FILE).read_text())["findings"]
    assert saved["deaths-typed"]["unbound_numbers"] == ["3"] and out["runs"][-1]["revalidate"] >= 1
    # An analysis that proposes it again, bound, clears the flag.
    assert _propose({**_deaths(key="deaths-typed")})["stored"]
    again = load_state(env, CHAR)["findings"]["deaths-typed"]
    assert again["text"] == "You died 5 times." and "unbound_numbers" not in again and "revalidate" not in again


def _kept(claim: str, sql: str, row: dict) -> dict:
    """A finding kept before the contract: no `constants`, its prose as typed."""
    return {"key": "legacy", "title_template": "Deaths", "claim_template": claim, "sql": sql, "sql_params": None,
            "row": row, "kb": [], "action_template": None}


def test_a_legacy_number_its_own_sql_writes_counts_as_declared():
    """Owner, 2026-11-22: 883 of 2,202 kept findings in the eval states were
    flagged, mostly typed levels. A number the finding's own SQL writes as a
    numeric literal is declared, under exactly the rule a new proposal meets."""
    at32 = _kept("At level 32 you died {n} times.", DEATHS + " AND level = 32", {"n": 2})
    assert verify.unbound(at32) == []
    state = {"findings": {"legacy": at32}}
    assert analysis.migrate(state) == 0 and "revalidate" not in state["findings"]["legacy"]
    # Compared as numbers: 32.0 in the SQL declares the 32 typed in the prose.
    assert verify.unbound({**at32, "sql": DEATHS + " AND level = 32.0"}) == []
    # A digit in a string literal or a comment isn't a numeric literal.
    assert verify.unbound({**at32, "sql": DEATHS + " AND zone <> 'Level 32'"}) == ["32"]
    assert verify.unbound({**at32, "sql": DEATHS + " -- level 32"}) == ["32"]
    # In neither the SQL nor the result: still flagged (the reviewer's prose, kept).
    typed = _kept("You died 3 times.", DEATHS, {"n": 5})
    assert verify.unbound(typed) == ["3"]
    state = {"findings": {"legacy": typed}}
    assert analysis.migrate(state) == 1 and state["findings"]["legacy"]["unbound_numbers"] == ["3"]
    # No row kept: nothing to compare a placeholder with, so nothing is declared.
    assert verify.unbound({**at32, "row": None}) == ["32"]


def test_a_measure_equal_to_a_sql_literal_is_refused_on_both_paths(env):
    """One rule (verify.refused_numbers) for a proposal and a kept finding:
    a typed number equal to a placeholder's value is a measure, whatever the
    SQL writes; one labelled like the column it contradicts is refused too."""
    capped = DEATHS + " LIMIT 5"
    assert drive.workspace(env, CHAR).judge.first_row(capped)["n"] == 5
    claim = "You died 5 times, {n} on record."
    new = _propose(_deaths(key="capped", claim=claim, sql=capped, constants=[{"value": 5, "label": "top-n"}]))
    assert not new["stored"] and "equals {n}" in new["why"]
    assert verify.unbound(_kept(claim, capped, {"n": 5})) == ["5"]
    # The same prose and SQL with another count on the kept row stands.
    assert verify.unbound(_kept(claim, capped, {"n": 4})) == []
    # Read as a level, 39 contradicts {level} on the kept row: refused (a new
    # proposal labelled level is too, test above). Every reading must pass.
    lvl = "SELECT level, COUNT(*) AS n FROM records WHERE kind='death' AND level < 39 GROUP BY level LIMIT 1"
    assert verify.unbound(_kept("Deaths at level {level}, none past level 39.", lvl, {"level": 20, "n": 1})) \
        == ["39"]
    # The reviewer's case, new: refused by the same function check() calls.
    why = verify.refused_numbers({"claim": "You died 3 times."}, [], DEATHS, None, [], {"n": 5})
    assert list(why) == ["3"] and "neither a placeholder nor a declared constant" in why["3"]
    got = _propose(_deaths(claim="You died 3 times."))
    assert not got["stored"] and "3 in the claim is neither" in got["why"]


class Raises:
    """A model: any use of it fails the test."""

    def __init__(self, *a, **k):
        raise AssertionError("the nightly recheck called a model")

    def __getattr__(self, name):
        raise AssertionError("the nightly recheck called a model")


def test_every_persons_recheck_runs_sequentially_without_a_model(lab, monkeypatch):
    owner = lab.env("o", DEFAULT_CHAR=CHAR)
    friend = lab.env("f", DEFAULT_CHAR="")
    nobody = lab.env("n", DEFAULT_CHAR="")  # no kept analysis: nothing to recheck
    for e in (owner, friend):
        drive._WS.clear()
        assert drive.propose(e, CHAR, _deaths(), AU.FakeStore(e["VL_ACCOUNT_ID"]))["stored"]
    drive._WS.clear()
    monkeypatch.setattr(providers, "ADAPTERS", {k: Raises for k in providers.ADAPTERS})
    for name in ("Anthropic", "OpenAI", "OpenAIResponses"):
        monkeypatch.setattr(providers, name, Raises)
    monkeypatch.setattr(auto, "Runner", Raises)
    monkeypatch.setattr(auto.Service, "run_job", lambda *a, **k: Raises())
    order: list[str] = []

    def one(e):
        order.append(e["VL_ACCOUNT_ID"])
        assert len(order) == len(set(order))  # each person once, one after the other
        return 0 if analysis.run_all(e) else 1

    newest = lambda: "2026-12-01T00:00:00+00:00"  # noqa: E731
    now = datetime(2026, 12, 2, 5, 0, tzinfo=prep.TZ)
    ran = analysis.recheck_pass([(owner, newest), (friend, newest), (nobody, newest)], one, now)
    assert order == [owner["VL_ACCOUNT_ID"], friend["VL_ACCOUNT_ID"]] and [r["exit"] for r in ran] == [0, 0]
    for e in (owner, friend):
        s = load_state(e, CHAR)
        assert s["runs"][-1]["kind"] == "recheck" and s["findings"]["deaths-count"]["text"] == "You died 5 times."
        assert analysis.refresh_policy(e)["last_recheck"]
    # Each person's tenant was read, nobody else's.
    asked = {str(a) for a, _ in lab.asked}
    assert asked >= {owner["VL_ACCOUNT_ID"], friend["VL_ACCOUNT_ID"]} and nobody["VL_ACCOUNT_ID"] not in asked
    # Done for the day: the same pass again runs nothing.
    assert analysis.recheck_pass([(owner, newest), (friend, newest)], one, now) == []


def test_the_default_runner_is_a_child_with_the_persons_env_and_a_time_cap(lab, monkeypatch):
    friend = lab.env("f", DEFAULT_CHAR="")
    drive._WS.clear()
    assert drive.propose(friend, CHAR, _deaths(), AU.FakeStore(friend["VL_ACCOUNT_ID"]))["stored"]
    seen = []

    def fake_run(argv, env, timeout, check):
        seen.append((argv[-1], env["ANALYSIS_STATE_DIR"], env["VL_ACCOUNT_ID"], timeout))
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr(analysis.subprocess, "run", fake_run)
    analysis.recheck_pass([(friend, lambda: "2026-12-01T00:00:00+00:00")])
    assert seen == [("analyse", friend["ANALYSIS_STATE_DIR"], friend["VL_ACCOUNT_ID"], analysis.RECHECK_TIMEOUT_S)]


def test_the_schedule_covers_every_active_person(monkeypatch):
    from wowstats import ui

    class U:
        def __init__(self, sub, owner, active=True):
            self.sub, self.owner, self.active = sub, owner, active

    class App:
        def __init__(self, sub):
            self.env = {"ANALYSIS_STATE_DIR": f"/x/{sub}"}

    class T:
        registry = type("R", (), {"users": staticmethod(lambda: [U("f", False), U("o", True), U("g", False, False)])})

        @staticmethod
        def app_for(u):
            return App(u.sub)

    got = ui.recheck_targets(None, T())
    assert [e["ANALYSIS_STATE_DIR"] for e, _ in got] == ["/x/o", "/x/f"]


def test_the_server_analysis_keeps_rendered_placeholders_and_refuses_typed_numbers(lab, monkeypatch):
    """The server's own run (wowstats.analysis.auto) on a scripted model that
    writes placeholders, cells and a declared constant, and one typed
    number: the first are kept as the query renders them, the last refused."""
    findings = [
        AU.FINDINGS[0],
        {"key": "xp-by-level", "title": "XP at level {level}",
         "claim": "Your top 2 levels for XP: {level} with {xp:int} XP, then {rows[1].level} with "
                  "{rows[1].xp:int}.",
         "sql": "SELECT level, xp_gained AS xp FROM agg_level ORDER BY xp_gained DESC LIMIT 2",
         "expect": {"level": 25, "xp": 32860}, "constants": [{"value": 2, "label": "top-n"}], "relevance": 3,
         "kb": [], "action": None},
        _deaths(key="misread", claim="You died 3 times."),
    ]
    monkeypatch.setattr(AU, "FINDINGS", findings)
    refused = []
    judge = drive._judge

    def judged(w, f):
        got, why, unknown = judge(w, f)
        refused.extend([why] if got is None else [])
        return got, why, unknown

    monkeypatch.setattr(drive, "_judge", judged)
    e = lab.env("a")
    r = AU.service(e, AU.FakeAnthropic(AU.Script())).run_job(e, lab.ctx(e))
    assert [x["status"] for x in r["runs"]] == ["done"], r
    kept = load_state(e, CHAR)["findings"]
    assert sorted(kept) == ["sessions-online", "xp-by-level"]
    top = drive.workspace(e, CHAR).judge.run(findings[1]["sql"], 2)[1]
    assert kept["xp-by-level"]["text"] == (f"Your top 2 levels for XP: {top[0][0]} with {verify.fmt(top[0][1], 'int')}"
                                           f" XP, then {top[1][0]} with {verify.fmt(top[1][1], 'int')}.")
    assert kept["xp-by-level"]["constants"] == [{"value": 2, "label": "top-n"}]
    assert load_state(e, CHAR)["runs"][-1]["refused"] == 1
    assert any("misread: 3 in the claim is neither" in w for w in refused), refused
