"""A kept finding today's renderer refuses is that finding's problem only.

0.11.1 in the lab: one of a character's kept findings wrote `{share:pct}` on a
column with no declared unit and declared a constant. Reading the state
(load_state -> migrate -> verify.unbound -> constant_why) rendered the
placeholder without its unit, the strict renderer of #246 raised UnitError,
and the character's overview, dungeons and plan answers and the analysis
share endpoint all failed on every rebuild. The CHANGELOG of 0.11.1 says
such a finding is refused and fades at the nightly recheck: these tests hold
the code to that, on every path that reads or judges a kept finding."""

from __future__ import annotations

import copy

from tests.analysis.test_drive import CHAR, call, env  # noqa: F401  (env: the fixture)
from wowstats import analysis
from wowstats.analysis import load_state, prep, save_state, standing, verify

# Shaped like the lab's: kept before the prose contract (no `contract`), a
# constant declared, a percent on a column the judge can't give a unit to
# (a count over a literal), no `units` kept, its text rendered by 0.11.0's
# size-guessing renderer.
BAD = {
    "key": "deaths-in-groups", "title": "21% of your deaths in groups",
    "text": "21% of your deaths came in 5-player groups.",
    "title_template": "{share:pct} of your deaths in groups",
    "claim_template": "{share:pct} of your deaths came in 5-player groups.",
    "sql": "SELECT COUNT(*) * 1.0 / 7 AS share FROM levels", "sql_params": None, "metric_query": None,
    "engine": "sqlite", "expect": {"share": 0.21}, "holds": None, "kb": [], "effect_col": None,
    "sample_col": None, "relevance": 1, "row": {"share": 0.21}, "action": None, "action_template": None,
    "observation": True, "constants": [{"value": 5, "label": "group-size"}], "effect": 0.0, "sample": 1.0,
    "weight": 1.0, "confirmations": 1, "first_seen": "2026-11-20T10:00:00+00:00",
}


def _good(env) -> dict:  # noqa: F811
    """A finding proposed today with a percent on a declared share and a
    constant: the proposal path goes through the same number checks."""
    got = call("propose_finding", {"finding": {
        "key": "combat-share", "title": "Combat {combat_share:pct}",
        "claim": "You spent {combat_share:pct} of your time in combat, even in 5-player groups.",
        "constants": [{"value": 5, "label": "group-size"}],
        "expect": {"combat_share": 0.209}, "relevance": 1, "action": None, "sql": ""},
        "metric_query": {"metrics": ["combat_share"]}})
    assert got["stored"], got
    return got


def test_a_proposal_with_a_percent_and_a_constant_is_judged_not_raised(env):  # noqa: F811
    got = _good(env)
    assert got["finding"]["text"] == "You spent 21% of your time in combat, even in 5-player groups."


def test_a_kept_percent_with_no_unit_is_marked_and_the_rest_still_reads(env):  # noqa: F811
    _good(env)
    state = load_state(env, CHAR)
    state["findings"]["deaths-in-groups"] = copy.deepcopy(BAD)
    save_state(env, CHAR, state)

    state = load_state(env, CHAR)  # raised UnitError in 0.11.1
    bad, good = state["findings"]["deaths-in-groups"], state["findings"]["combat-share"]
    assert bad["unrendered"].startswith("a percent placeholder's column declares no unit"), bad
    assert bad["revalidate"]["why"][0].startswith(verify.UNRENDERED_WHY), bad["revalidate"]
    assert bad["text"] == BAD["text"]  # kept, never dropped on read
    assert "revalidate" not in good and "unrendered" not in good, good

    # The answers built on the state still build: the page's and the shared tier.
    full = analysis.answer(env, CHAR)
    assert full["available"] and {f["key"] for f in full["findings"]} == {"combat-share", "deaths-in-groups"}
    assert full["to_revalidate"] == 1
    assert analysis.answer(env, CHAR, "zogzog")["available"]
    assert standing(state) is not None

    # The nightly recheck renders it on today's data and fades it, as the docs say.
    analysis.run(env, CHAR, rows=prep.query(None, None, CHAR), players=set())
    after = load_state(env, CHAR)["findings"]
    assert "unit" in after["deaths-in-groups"]["miss_why"], after["deaths-in-groups"]
    assert after["deaths-in-groups"]["weight"] < 1.0
    assert "miss_why" not in after["combat-share"], after["combat-share"]


def test_migrate_reads_every_number_rule_without_rendering_a_unit():
    """The number checks compare values; whether a format fits its unit is
    units_why's call alone."""
    f = copy.deepcopy(BAD)
    assert verify.unbound(f) == []
    f["claim_template"] = "{share:pct} of your deaths came in 5-player groups, 0.21 in all."
    assert verify.unbound(f) == ["0.21"]  # a measure typed by hand is still refused


def test_any_render_error_of_a_kept_finding_marks_that_finding_only():
    broken = {**copy.deepcopy(BAD), "key": "broken", "claim_template": "You died {n} times.",
              "title_template": "Deaths", "constants": [], "row": {"n": [1, 2]}, "units": {}}
    fine = {"key": "fine", "title_template": "Deaths", "claim_template": "You died {n} times.",
            "sql": "SELECT COUNT(*) AS n FROM levels", "row": {"n": 4}, "contract": verify.CONTRACT}
    state = {"findings": {"broken": broken, "fine": fine}}
    assert analysis.migrate(state) == 1
    assert broken["unrendered"].startswith(verify.UNRENDERED_WHY), broken
    assert "revalidate" not in fine and "unrendered" not in fine

    # Fixed (a later analysis kept a readable row): the flag is lifted.
    broken["row"] = {"n": 4}
    assert analysis.migrate(state) == 0
    assert "unrendered" not in broken and "revalidate" not in broken, broken


def test_a_bug_in_one_findings_checks_stays_with_that_finding(monkeypatch):
    real = verify.unbound

    def flaky(f):
        if f.get("key") == "boom":
            raise RuntimeError("a rule's bug")
        return real(f)

    monkeypatch.setattr(verify, "unbound", flaky)
    boom = {"key": "boom", "title_template": "x", "claim_template": "y", "sql": "SELECT 1 AS n", "row": {"n": 1}}
    ok = {"key": "ok", "title_template": "Deaths", "claim_template": "You died {n} times.",
          "sql": "SELECT COUNT(*) AS n FROM levels", "row": {"n": 4}}
    state = {"findings": {"boom": boom, "ok": ok}}
    assert analysis.migrate(state) == 1
    assert boom["revalidate"]["why"][0].startswith(verify.UNRENDERED_WHY), boom
    assert "revalidate" not in ok
