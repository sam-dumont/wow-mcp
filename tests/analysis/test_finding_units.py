"""The judge refuses a unit-bearing placeholder format ({n:pct}, {n:dur})
on a value whose unit it doesn't fit: a reviewer got "{n:pct}" kept on a
plain count, rendered "2%" (owner, 2026-11-23). The unit comes from the
semantic layer: the metric a metric_query names, or, for a raw query, the
columns each output column's expression reads (wowstats.analysis.lineage):
an alias kept no unit and `played_s AS n` was kept as "Level 34 took 7100%"
(pre-release check, 2026-11-23). A value whose unit can't be told gets
neither format."""

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


def one(sql):
    """play_sql's first row as {column: text}."""
    head, first = call("play_sql", {"query": sql})["result"].split("\n")[:2]
    return dict(zip(head.split("\t"), first.split("\t"), strict=True))


LEVEL = "SELECT level, played_s AS n FROM levels WHERE played_s > 0 ORDER BY level LIMIT 1"


def test_an_alias_keeps_its_columns_unit(env):  # noqa: F811
    n = float(one(LEVEL)["n"])
    bad = propose({"claim": "Level {level} took {n:pct}.", "sql": LEVEL, "expect": {"n": n}})
    assert not bad["stored"], bad
    assert "{n:pct}" in bad["why"] and "seconds" in bad["why"] and "{n:dur}" in bad["why"]
    ok = propose({"key": "units-alias-dur", "claim": "Level {level} took {n:dur}.", "sql": LEVEL,
                  "expect": {"n": n}})
    assert ok["stored"], ok


def test_casts_sums_and_ctes_keep_the_unit(env):  # noqa: F811
    sql = ("WITH l AS (SELECT level, played_s AS p FROM levels WHERE played_s > 0) "
           "SELECT CAST(SUM(p) AS BIGINT) AS total FROM l")
    bad = propose({"claim": "You took {total:pct}.", "sql": sql, "expect": {"total": int(one(sql)["total"])}})
    assert not bad["stored"] and "seconds" in bad["why"], bad


def test_a_ratio_column_stands_as_pct(env):  # noqa: F811
    sql = "SELECT SUM(afk_s) * 1.0 / SUM(online_s) AS afk FROM per_session"
    ok = propose({"claim": "You were away {afk:pct} of the time.", "sql": sql,
                  "expect": {"afk": float(one(sql)["afk"])}})
    assert ok["stored"], ok


def test_a_count_or_an_unknown_unit_gets_no_pct(env):  # noqa: F811
    sql = "SELECT COUNT(*) AS n FROM levels"
    got = propose({"claim": "You have {n:pct} levels on record.", "sql": sql,
                   "expect": {"n": int(one(sql)["n"])}})
    assert not got["stored"] and "{n:pct}" in got["why"], got
    sql = "SELECT MAX(xp) AS n FROM levels"
    got = propose({"key": "units-unknown", "claim": "Your biggest level took {n:dur}.", "sql": sql,
                   "expect": {"n": int(one(sql)["n"])}})
    assert not got["stored"] and "can't tell" in got["why"], got
