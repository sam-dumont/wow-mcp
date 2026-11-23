"""The judge refuses a unit-bearing placeholder format ({n:pct}, {n:dur})
on a value whose unit it doesn't fit: a reviewer got "{n:pct}" kept on a
plain count, rendered "2%" (owner, 2026-11-23). The unit comes from the
semantic layer: the metric a metric_query names, or a column of a view per
grain a raw query reads. A column nothing declares keeps today's rule."""

from __future__ import annotations

from tests.analysis.test_drive import call, env  # noqa: F401  (env: the fixture)


def propose(f, **kw):
    base = {"key": "units-check", "title": "Units", "relevance": 1, "action": None}
    return call("propose_finding", {"finding": {**base, **f}, **kw})


def test_pct_on_a_count_metric_is_refused(env):  # noqa: F811
    got = propose({"claim": "You died {deaths:pct} of the time.", "sql": "", "expect": {"deaths": 5}},
                  metric_query={"metrics": ["deaths"]})
    assert not got["stored"]
    assert "{deaths:pct}" in got["why"] and "deaths" in got["why"] and "{deaths}" in got["why"]


def test_pct_on_a_share_metric_stands(env):  # noqa: F811
    got = propose({"claim": "You spent {combat_share:pct} of your time in combat.", "sql": "",
                   "expect": {"combat_share": 0.209}}, metric_query={"metrics": ["combat_share"]})
    assert got["stored"], got
    assert "21%" in got["finding"]["text"]


def test_dur_on_seconds_stands_and_on_a_count_is_refused(env):  # noqa: F811
    ok = propose({"claim": "You played {online_s:dur}.", "sql": "", "expect": {"online_s": 31880}},
                 metric_query={"metrics": ["online_s"]})
    assert ok["stored"], ok
    assert "8h51" in ok["finding"]["text"]
    bad = propose({"key": "units-kills", "claim": "You killed for {kills:dur}.", "sql": "",
                   "expect": {"kills": 272}}, metric_query={"metrics": ["kills"]})
    assert not bad["stored"] and "{kills:dur}" in bad["why"] and "seconds" in bad["why"]


def test_a_view_column_carries_its_metric_unit(env):  # noqa: F811
    sql = "SELECT SUM(kills) AS kills, SUM(online_s) AS online_s FROM per_session"
    bad = propose({"claim": "You killed {kills:pct}.", "sql": sql, "expect": {"kills": 272}})
    assert not bad["stored"] and "{kills:pct}" in bad["why"]
    ok = propose({"key": "units-online", "claim": "You played {online_s:dur}.", "sql": sql,
                  "expect": {"online_s": 31880}})
    assert ok["stored"], ok


def test_a_column_nothing_declares_keeps_todays_rule(env):  # noqa: F811
    sql = "SELECT COUNT(*) AS n FROM levels"
    got = propose({"claim": "You have {n:pct} levels on record.", "sql": sql,
                   "expect": {"n": int(call("play_sql", {"query": sql})["result"].split("\n")[1])}})
    assert got["stored"], got
