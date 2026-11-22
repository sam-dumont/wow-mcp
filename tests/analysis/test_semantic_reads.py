"""A kept finding is tied to the semantic definitions it reads, not the whole
semantic.yaml (owner, 2026-11-22): adding an unrelated metric leaves it
valid; a change to a metric, dimension, view or model view it reads sends it
to revalidate; a read set that can't be told falls back to the whole-file
hash; a finding kept before this adopts its read set only when that is exact
(wowstats/analysis/definitions.py).

Synthetic data: the run-manifest tests' events and drive fixture.
"""

from __future__ import annotations

import json

import pytest
import yaml

from tests.analysis import test_analysis as A
from wowstats import analysis
from wowstats.analysis import definitions, manifest, semantic

driven, rows, knowledge = A.driven, A.rows, A.knowledge
CHAR, recheck_night = A.CHAR, A.recheck_night
KEY = "sessions-online"


def sql_finding(**kw):
    """Reads online_s and the other per_session metrics through the view per grain."""
    return A.finding(**{"sql": A.PER_SESSION_SQL, **kw})


def metric_finding(**kw):
    """Reads online_s (timeline) through query_metric, no SQL of its own."""
    f = A.finding(title="Online time in all", claim="You were online {online_s:dur} in all.",
                  expect={"online_s": 31880}, effect="online_s", sample="online_s", holds="online_s > 0",
                  action={"do": "Keep sessions near {online_s:dur} in all", "where": "anywhere", "blocked_by": "",
                          "next_step": "Plan the next session at the same length"},
                  metric_query={"metrics": ["online_s"]}, **kw)
    f.pop("sql")
    return f


def raw_finding(**kw):
    """Reads a prep table only (agg_session): no semantic definition."""
    return A.finding(**kw)


FINDINGS = {"sql": sql_finding, "metric_query": metric_finding}


def _clear():
    for mod in (semantic, manifest, definitions):
        for v in vars(mod).values():
            if callable(getattr(v, "cache_clear", None)):
                v.cache_clear()


@pytest.fixture
def model_edit(tmp_path, monkeypatch):
    """Call it with a function that edits semantic.yaml as loaded (a dict):
    that copy is the model from then on; the real one is back after."""

    def edit(fn):
        model = yaml.safe_load(semantic.MODEL_FILE.read_text())
        fn(model)
        out = tmp_path / f"semantic-{len(list(tmp_path.glob('semantic-*')))}.yaml"
        out.write_text(yaml.safe_dump(model, sort_keys=False))
        monkeypatch.setattr(semantic, "MODEL_FILE", out)
        _clear()

    yield edit
    monkeypatch.undo()
    _clear()


def add_unrelated_metric(m):
    """A new measure on levels, a model none of the findings here reads."""
    m["levels"]["measures"]["unrelated_levels"] = {**m["levels"]["measures"]["split_levels"],
                                                    "description": "A metric nothing here reads."}


def online_s(m) -> dict:
    return m["timeline"]["measures"]["online_s"]


def _propose(env, drive, f):
    drive.brief(env, CHAR)
    got = drive.propose(env, CHAR, f)
    assert got["stored"], got
    return analysis.load_state(env, CHAR)["findings"][KEY]


def _whole_hash_moved(out, judged_manifest) -> bool:
    return out["manifests"][out["runs"][-1]["manifest"]]["semantic_hash"] != judged_manifest["semantic_hash"]


# ---------------------------------------------------------------- what a finding reads


@pytest.mark.parametrize("kind", FINDINGS)
def test_adding_an_unrelated_metric_keeps_a_finding_valid(driven, rows, knowledge, model_edit, kind):
    env, drive = driven
    f = _propose(env, drive, FINDINGS[kind]())
    assert f["semantic_reads"]["names"] and f["semantic_reads"]["hash"]
    judged = analysis.load_state(env, CHAR)["manifests"][f["manifest"]]
    model_edit(add_unrelated_metric)
    for _night in range(2):
        out = recheck_night(env, rows, knowledge)
        info = out["runs"][-1]
        assert _whole_hash_moved(out, judged)  # the old scheme would have sent it to revalidate
        assert info["still"] == 1 and info["revalidate"] == 0, info
        assert "revalidate" not in out["findings"][KEY]
    assert analysis.answer(env, CHAR)["checked"] == 1


@pytest.mark.parametrize("kind", FINDINGS)
@pytest.mark.parametrize("part", ["description", "expr"])
def test_changing_a_read_metric_sends_it_to_revalidate(driven, rows, knowledge, model_edit, kind, part):
    env, drive = driven
    w0 = _propose(env, drive, FINDINGS[kind]())["weight"]
    edits = {"description": lambda m: online_s(m).update(description="Online seconds, counted another way."),
             "expr": lambda m: online_s(m).update(expr="_.dur_s.max()")}
    model_edit(edits[part])
    for _night in range(2):  # every night until judged again
        out = recheck_night(env, rows, knowledge)
        f, info = out["findings"][KEY], out["runs"][-1]
        assert f["revalidate"]["why"] == ["the semantic model changed"]
        assert info["revalidate"] == 1 and info["still"] == info["confirmed"] == info["faded"] == 0
        assert f["weight"] == w0


def test_changing_a_dimension_it_uses_sends_it_to_revalidate(driven, rows, knowledge, model_edit):
    """per_session is keyed by session_id in its models (timeline among them):
    that dimension changed, the finding goes to revalidate; a dimension of a
    model it doesn't read (levels.partial) changed, it stays valid."""
    env, drive = driven
    _propose(env, drive, sql_finding())

    def unrelated(m):
        m["levels"]["dimensions"]["partial"]["description"] = "Edited: a dimension per_session doesn't read."

    model_edit(unrelated)
    out = recheck_night(env, rows, knowledge)
    assert out["runs"][-1]["still"] == 1 and "revalidate" not in out["findings"][KEY]

    def used(m):
        unrelated(m)
        m["timeline"]["dimensions"]["session_id"]["description"] = "Edited: the key per_session joins on."

    model_edit(used)
    out = recheck_night(env, rows, knowledge)
    assert out["findings"][KEY]["revalidate"]["why"] == ["the semantic model changed"]


def test_a_metric_query_reads_its_dimensions_and_time_only_when_it_uses_them():
    g = definitions.current()
    bare = definitions.names_of({"metric_query": {"metrics": ["online_s"]}}, g)
    assert bare == ["metric:online_s"]
    cut = definitions.names_of({"metric_query": {"metrics": ["online_s"], "group_by": ["session_id"],
                                                 "where": [{"dimension": "zone", "op": "is not null"}],
                                                 "since": "2026-01-01T00:00:00"}}, g)
    assert cut == ["dim:timeline.session_id", "dim:timeline.time", "dim:timeline.zone", "metric:online_s"]
    # A cross-model metric reads both sides: its own definition and the metrics it divides.
    cross = definitions.names_of({"metric_query": {"metrics": ["kills_per_combat_hour"], "group_by": ["level"]}}, g)
    assert "dim:records.level" in cross and "dim:timeline.level" in cross
    reach = definitions.hash_of(["metric:kills_per_combat_hour"], g)
    g2 = {**g, "metric:kills": {**g["metric:kills"], "d": "changed"}}
    assert definitions.hash_of(["metric:kills_per_combat_hour"], g2) != reach
    # A definition gone since reads as changed, never as unchanged.
    gone = {k: v for k, v in g.items() if k != "metric:online_s"}
    assert definitions.hash_of(bare, gone) != definitions.hash_of(bare, g)


# ---------------------------------------------------------------- can't tell: the whole file


def test_an_unresolvable_read_set_falls_back_to_the_whole_file_hash(driven, rows, knowledge, model_edit):
    assert definitions.of({"sql": "SELECT 1 AS n"}) == {"names": None}  # names no relation
    assert definitions.of({"metric_query": {"metrics": ["no_such_metric"]}}) == {"names": None}
    assert definitions.of({"metric_query": {"metrics": ["online_s"], "group_by": ["no_such_dim"]}}) == {
        "names": None}
    assert definitions.of({}) == {"names": None}
    env, drive = driven
    # A finding on a prep table reads no semantic definition: any model edit leaves it valid...
    assert _propose(env, drive, raw_finding())["semantic_reads"]["names"] == ["relation:agg_session"]
    model_edit(add_unrelated_metric)
    out = recheck_night(env, rows, knowledge)
    assert out["runs"][-1]["still"] == 1 and "revalidate" not in out["findings"][KEY]
    # ... unless its read set couldn't be told: then the whole file decides.
    st_file = analysis.char_dir(env, CHAR) / analysis.STATE_FILE
    st = json.loads(st_file.read_text())
    st["findings"][KEY]["semantic_reads"] = {"names": None}
    st_file.write_text(json.dumps(st))
    out = recheck_night(env, rows, knowledge)
    assert out["findings"][KEY]["revalidate"]["why"] == ["the semantic model changed"]


# ---------------------------------------------------------------- findings kept before semantic_reads


def _before_semantic_reads(env, drive, f) -> dict:
    """A finding kept by a build before semantic_reads (0.7.0-rc.8): a
    manifest with the whole-file hash, no semantic_reads. What today's
    judge gives it is returned, for comparison."""
    stamped = _propose(env, drive, f)["semantic_reads"]
    st_file = analysis.char_dir(env, CHAR) / analysis.STATE_FILE
    st = json.loads(st_file.read_text())
    st["findings"][KEY].pop("semantic_reads")
    st_file.write_text(json.dumps(st))
    return stamped


@pytest.mark.parametrize("kind", FINDINGS)
def test_a_legacy_finding_under_todays_whole_file_adopts_its_read_set_exactly(driven, rows, knowledge, model_edit,
                                                                              kind):
    """The build it was judged under has today's whole-file hash: every
    definition it reads is the one it was judged under, so the adopted read
    set and hash are exactly what the judge gives it today. After that it
    follows the new scheme: an unrelated metric leaves it valid."""
    env, drive = driven
    stamped = _before_semantic_reads(env, drive, FINDINGS[kind]())
    out = recheck_night(env, rows, knowledge)
    f = out["findings"][KEY]
    assert f["semantic_reads"] == stamped and out["runs"][-1]["still"] == 1 and "revalidate" not in f
    model_edit(add_unrelated_metric)
    out = recheck_night(env, rows, knowledge)
    assert out["runs"][-1]["still"] == 1 and "revalidate" not in out["findings"][KEY]


def test_a_legacy_finding_under_another_whole_file_revalidates_once_then_follows_the_new_scheme(
        driven, rows, knowledge, model_edit):
    """Its build's whole-file hash isn't today's: which of its definitions
    moved can't be told, so it is never silently valid. It goes to
    revalidate (every night, weight kept) until an analysis proposes it
    again; from then on only what it reads counts."""
    env, drive = driven
    _before_semantic_reads(env, drive, sql_finding())
    w0 = analysis.load_state(env, CHAR)["findings"][KEY]["weight"]
    model_edit(add_unrelated_metric)  # unrelated, but the old hash can't say so
    for _night in range(2):
        out = recheck_night(env, rows, knowledge)
        f = out["findings"][KEY]
        assert f["revalidate"]["why"] == ["the semantic model changed"] and "semantic_reads" not in f
        assert f["weight"] == w0
    drive._WS.clear()
    drive.brief(env, CHAR, restart=True)
    assert drive.propose(env, CHAR, sql_finding())["stored"]
    f = analysis.load_state(env, CHAR)["findings"][KEY]
    assert f["semantic_reads"]["names"] == ["view:per_session"] and "revalidate" not in f

    def another(m):
        add_unrelated_metric(m)
        m["levels"]["measures"]["another_unrelated"] = dict(m["levels"]["measures"]["unrelated_levels"])

    model_edit(another)
    out = recheck_night(env, rows, knowledge)
    assert out["runs"][-1]["still"] == 1 and "revalidate" not in out["findings"][KEY]
