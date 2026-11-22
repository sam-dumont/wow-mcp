"""What a relation stands on, asked in one place (external review, 2026-11-22,
finding 1).

The views per grain (per_level ... per_slot) are what play_sql puts first,
but two checks didn't see through them. The judge refused a finding whose
query read only a view ("reads none of the character's tables": it knew the
prep's tables and the sem_* views by name, not the per_* ones), so no
finding could rest on the tables the analysis is told to read first. And
the definition check (manifest.reads_scoring) knew upgrade_candidates, gear,
sem_upgrades and sem_gear by name: a per_slot finding judged under Holy
stayed "checked" under Shadow, its numbers moved by the scoring alone.

Now semantic.reads resolves any relation to the prep tables under it
(a view per grain to its models' views, a model view to the tables its SQL
names), and the coverage, the judge and the definition check all ask it.
These tests run over the view registry, so a view added to semantic.yaml is
covered without touching them.
"""

from __future__ import annotations

import pytest

from tests.analysis import test_analysis as A
from tests.invariants import test_definitions as D
from wowstats import analysis
from wowstats.analysis import TABLES, manifest, semantic

driven, rows, knowledge = A.driven, A.rows, A.knowledge
CHAR, finding = A.CHAR, A.finding
HOLY, SHADOW, night = D.HOLY, D.SHADOW, D._night


def _count_finding(key: str, sql: str, row: dict) -> dict:
    return finding(key=key, title="{n} rows", claim="The history has {n} rows here.", sql=sql,
                   expect=dict(row), effect="n", sample="n", holds="n >= 0",
                   action={"do": "Look at the {n} rows", "where": "the analysis", "blocked_by": "",
                           "next_step": "Read the first one"})


def test_every_relation_resolves_to_the_play_history():
    """Every view per grain and every model view stands on prep game tables,
    and a prep table on itself."""
    for name in [*semantic.views(), *semantic.VIEWS]:
        under = semantic.reads({name})
        assert under and under <= set(TABLES), (name, under)
        assert any(TABLES[t] == "game" for t in under), (name, under)
    assert semantic.reads({"levels"}) == {"levels"}
    assert semantic.reads({"per_slot"}) == {"upgrade_candidates", "gear"}
    assert semantic.reads({"not_a_relation"}) == set()


@pytest.mark.parametrize("view", sorted(semantic.views()))
def test_a_finding_can_rest_on_every_view(driven, view):
    env, drive = driven
    drive.brief(env, CHAR)
    sql = f"SELECT COUNT(*) AS n FROM {view}"
    row = drive.workspace(env, CHAR).judge.first_row(sql)
    got = drive.propose(env, CHAR, _count_finding(f"{view.replace('_', '-')}-rows", sql, row))
    assert got["stored"], got
    # A constant still isn't evidence.
    bare = drive.propose(env, CHAR, _count_finding("constant", "SELECT 1 AS n", {"n": 1}))
    assert not bare["stored"] and "reads none" in bare["why"]


@pytest.mark.parametrize("view", sorted(semantic.views()))
def test_a_view_follows_the_scoring_exactly_when_its_tables_do(view):
    scored = bool(semantic.reads({view}) & set(manifest.SCORED_TABLES))
    assert manifest.reads_scoring({"sql": f"SELECT * FROM {view}"}) == scored
    assert scored == (view == "per_slot")  # the one view on the gear models today


# The reviewer's query: per_slot's numbers move with the scoring, the
# sessions count beside it doesn't. Then a view the scoring doesn't fill
# joined to a raw table it does, and a view the scoring doesn't touch.
SLOT = ("SELECT COUNT(*) AS n, SUM(best_upgrade_gain) AS gain, (SELECT COUNT(*) FROM sessions) AS sessions "
        "FROM per_slot")
MIXED = "SELECT COUNT(*) AS n, (SELECT COUNT(*) FROM per_level) AS levels FROM upgrade_candidates"
ONLINE = "SELECT COUNT(*) AS n, SUM(online_s) AS online FROM per_session"


def test_a_view_finding_is_revalidated_when_the_scoring_moves(driven, rows, knowledge):
    env, drive = driven
    holy, shadow = {**env, **HOLY}, {**env, **SHADOW}
    drive.brief(holy, CHAR)
    judge = drive.workspace(holy, CHAR).judge
    found = {k: judge.first_row(q) for k, q in (("slot", SLOT), ("mixed", MIXED), ("online", ONLINE))}
    for k, q in (("slot", SLOT), ("mixed", MIXED), ("online", ONLINE)):
        assert drive.propose(holy, CHAR, _count_finding(k, q, found[k]))["stored"], k
    w0 = {k: f["weight"] for k, f in analysis.load_state(env, CHAR)["findings"].items()}
    assert night(env, rows, knowledge, HOLY)["runs"][-1]["still"] == 3  # same play, same scoring
    for _ in range(2):
        out = night(env, rows, knowledge, SHADOW)
        f, info = out["findings"], out["runs"][-1]
        assert f["slot"]["revalidate"]["why"] == ["scoring spec holy -> shadow"], f["slot"]
        assert f["mixed"]["revalidate"]["why"] == ["scoring spec holy -> shadow"], f["mixed"]
        assert "revalidate" not in f["online"]  # online time: nothing the scoring fills
        assert info["revalidate"] == 2 and info["still"] == 1 and info["confirmed"] == 0, info
        # Same source events: no confidence gained or lost.
        assert {k: x["weight"] for k, x in f.items()} == w0 and all(x["confirmations"] == 0 for x in f.values())
    # Judged again under Shadow on the same play: back to checked, still no gain.
    drive._WS.clear()
    drive.brief(shadow, CHAR, restart=True)
    row = drive.workspace(shadow, CHAR).judge.first_row(SLOT)
    assert row["gain"] != found["slot"]["gain"] and row["sessions"] == found["slot"]["sessions"]
    assert drive.propose(shadow, CHAR, _count_finding("slot", SLOT, row))["stored"]
    f = analysis.load_state(env, CHAR)["findings"]["slot"]
    assert "revalidate" not in f and f["weight"] == w0["slot"] and f["confirmations"] == 0
