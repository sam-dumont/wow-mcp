"""Definition changes (property 10): a saved finding is rechecked as before
only while the definitions it was judged under hold. Any change to the prep
version, the semantic model or a reference version, alone or together,
marks it to revalidate, says which, and keeps it so every night until it is
judged again (#78). A finding kept before manifests is carried over once,
never twice, whatever comes after (#80).

The changes are every one alone plus pairs drawn from seeds, over several
nights. Builds on tests/analysis/test_analysis.py's run-manifest tests (one
change each).
"""

from __future__ import annotations

import itertools
import random

import pytest

from tests.analysis import test_analysis as A
from wowstats import analysis
from wowstats.analysis import manifest, prep, semantic

# The run-manifest tests' fixtures: the analysis events, the KB, the drive module.
driven, rows, knowledge = A.driven, A.rows, A.knowledge
CHAR, finding, legacy, recheck_night = A.CHAR, A.finding, A.legacy, A.recheck_night

REFERENCE = ("kb_polestar", "forever_db", "route", "gear")
CHANGES = ("prep", "semantic", *(f"reference:{k}" for k in REFERENCE))


def _pairs(n: int, seed: int = 10) -> list[tuple[str, ...]]:
    return random.Random(seed).sample(list(itertools.combinations(CHANGES, 2)), n)


@pytest.fixture
def change(tmp_path, monkeypatch):
    """Apply a set of definition changes; what each is expected to say."""

    def clear():
        for f in (semantic._file, semantic.spec, manifest._semantic_hash):
            f.cache_clear()

    def apply(which: tuple[str, ...]) -> list[str]:
        why = []
        ref = {}
        for c in which:
            if c == "prep":
                monkeypatch.setattr(prep, "PREP_VERSION", prep.PREP_VERSION + 1)
                why.append(f"prep version {prep.PREP_VERSION - 1} -> {prep.PREP_VERSION}")
            elif c == "settings":
                # Another timezone: a finding's hours and days mean other hours.
                why.append(f"timezone {prep.TZ_NAME} -> Pacific/Auckland")
                monkeypatch.setattr(prep, "TZ_NAME", "Pacific/Auckland")
            elif c == "semantic":
                edited = tmp_path / "semantic.yaml"
                edited.write_text(semantic.MODEL_FILE.read_text().replace('description: "', 'description: "(x) ', 1))
                monkeypatch.setattr(semantic, "MODEL_FILE", edited)
                clear()
                why.append("the semantic model changed")
            else:
                ref[c.split(":")[1]] = "v9.9.9"
        if ref:
            real = manifest.reference_versions
            monkeypatch.setattr(manifest, "reference_versions", lambda *a: {**real(*a), **ref})
            # The reference part of `why` is checked by prefix (the old value is the build's own).
            why += [f"{k} " for k in sorted(ref)]
        return why

    yield apply
    monkeypatch.undo()
    clear()


def _why_matches(got: list[str], want: list[str]) -> bool:
    exact = [w for w in want if not w.endswith(" ")]
    prefixes = [w for w in want if w.endswith(" ")]
    rest = [g for g in got if g not in exact]
    return (all(w in got for w in exact) and len(rest) == len(prefixes)
            and all(any(g.startswith(p) and g.endswith("-> v9.9.9") for g in rest) for p in prefixes))


@pytest.mark.parametrize("which", [(), *((c,) for c in CHANGES), ("settings",), ("settings", "prep"), *_pairs(4)],
                         ids=lambda w: "+".join(w) or "none")
def test_a_finding_rechecks_as_before_only_while_its_definitions_hold(driven, rows, knowledge, change, which):
    env, drive = driven
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding())["stored"]
    w0 = analysis.load_state(env, CHAR)["findings"]["sessions-online"]["weight"]
    want = change(which)
    for _night in range(2):
        out = recheck_night(env, rows, knowledge)
        f, info = out["findings"]["sessions-online"], out["runs"][-1]
        if not which:
            assert "revalidate" not in f and info["still"] == 1 and info["revalidate"] == 0
            continue
        assert info["revalidate"] == 1 and info["still"] == info["confirmed"] == info["faded"] == 0
        assert _why_matches(f["revalidate"]["why"], want), (f["revalidate"]["why"], want)
        assert f["weight"] == w0  # neither gained nor lost on a definition it wasn't judged under
    a = analysis.answer(env, CHAR)
    assert (a["to_revalidate"], a["checked"]) == ((1, 0) if which else (0, 1))


@pytest.mark.parametrize("then", [(), ("semantic",), ("prep",)], ids=lambda w: "+".join(w) or "nothing")
def test_an_old_finding_is_carried_over_once(driven, rows, knowledge, change, then):
    from wowstats import __version__

    env, drive = driven
    legacy(env, drive)
    carried = 0
    for night in range(3):
        if night == 1 and then:
            change(then)
        out = recheck_night(env, rows, knowledge)
        f, info = out["findings"]["sessions-online"], out["runs"][-1]
        carried += info["carried_over"]
        assert f["carried_over"]["from"] == "0.6" and f["carried_over"]["to"] == __version__
        if night and then:
            assert "revalidate" in f and info["revalidate"] == 1
    assert carried == 1


# ---------------------------------------------------------------- review 6: definitions never add weight
#
# The run manifest held the prep version, the semantic model, the reference
# data and the time settings, not the gear scoring: with WEIGHTS changed
# (Holy -> Shadow) the upgrade candidates moved, so did data_mark (it counted
# the prepared tables' rows), and the nightly recheck read that as new play:
# confirmed, weight 1.0 -> 1.5. Support now comes from the source events only.

HOLY, SHADOW = {"WEIGHTS": f"{CHAR}:holy"}, {"WEIGHTS": f"{CHAR}:shadow"}


def _night(env, rows, knowledge, extra=None):
    return analysis.run({**env, **(extra or {})}, CHAR, rows=[dict(r) for r in rows], players=set(),
                        knowledge=knowledge)


def _upgrades(rows, env) -> int:
    from wowstats.mcp import route

    db = prep.build([dict(r) for r in rows], CHAR, route.reference(), upgrades=analysis.upgrades_for(rows, CHAR, env))
    return db.execute("SELECT COUNT(*) FROM upgrade_candidates").fetchone()[0]


def upgrades_finding(n: int) -> dict:
    """A finding on what the gear scoring fills: the upgrade candidates."""
    return finding(key="upgrades", title="{n} upgrades within reach", claim="The gear advice lists {n} upgrades.",
                   sql="SELECT COUNT(*) AS n FROM upgrade_candidates", expect={"n": n}, effect="n", sample="n",
                   holds="n > 0", action={"do": "Go through the {n} upgrades on the Gear panel", "where": "Gear panel",
                                          "blocked_by": "", "next_step": "Pick the first one on the way"})


def test_changing_weights_marks_findings_to_revalidate_never_more_confident(driven, rows, knowledge):
    env, drive = driven
    judged = {**env, **HOLY}
    drive.brief(judged, CHAR)
    n_holy, n_shadow = (_upgrades(rows, w) for w in (HOLY, SHADOW))
    assert n_holy != n_shadow  # the fixture's candidates do move with the spec
    assert drive.propose(judged, CHAR, finding())["stored"]
    assert drive.propose(judged, CHAR, upgrades_finding(n_holy))["stored"]
    w0 = {k: f["weight"] for k, f in analysis.load_state(env, CHAR)["findings"].items()}
    assert _night(env, rows, knowledge, HOLY)["runs"][-1]["still"] == 2  # same play, same scoring: as before
    for _night_n in range(2):
        out = _night(env, rows, knowledge, SHADOW)
        f, info = out["findings"], out["runs"][-1]
        # The finding on the upgrade candidates: to revalidate. The one on
        # online time reads nothing the scoring fills: still, no gain.
        assert info["revalidate"] == 1 and info["still"] == 1 and info["confirmed"] == 0, info
        assert f["upgrades"]["revalidate"]["why"] == ["scoring spec holy -> shadow"], f["upgrades"]["revalidate"]
        assert "revalidate" not in f["sessions-online"]
        assert {k: x["weight"] for k, x in f.items()} == w0 and all(x["confirmations"] == 0 for x in f.values())
    # Proposed again under Shadow on the same play: judged, still no gain.
    drive._WS.clear()
    drive.brief({**env, **SHADOW}, CHAR, restart=True)
    assert drive.propose({**env, **SHADOW}, CHAR, upgrades_finding(n_shadow))["stored"]
    f = analysis.load_state(env, CHAR)["findings"]["upgrades"]
    assert "revalidate" not in f and f["weight"] == w0["upgrades"] and f["confirmations"] == 0
    out = _night(env, rows, knowledge, SHADOW)
    assert out["runs"][-1]["still"] == 2 and out["findings"]["upgrades"]["weight"] == w0["upgrades"]


def test_the_manifest_holds_the_resolved_scoring(rows, knowledge):
    """Normalized: the spec the gear is scored for, whichever way it was
    chosen. WEIGHTS naming the spec the add-on already picked scores the
    same, so it isn't a definition change."""
    from wowstats.mcp import route

    def scoring(env):
        ref = route.reference()
        db = prep.build([dict(r) for r in rows], CHAR, ref, upgrades=analysis.upgrades_for(rows, CHAR, env))
        return manifest.build(db, rows, knowledge, ref, scoring=analysis.scoring_for(rows, CHAR, env))["settings"]

    picked = scoring({})  # the fixture's add-on picked Shadow
    assert picked["scoring"]["spec"] == "shadow"
    assert scoring({"WEIGHTS": f"{CHAR}:Shadow"}) == picked
    assert scoring(HOLY)["scoring"]["spec"] == "holy"


@pytest.mark.parametrize("what", ["prep", "semantic", "settings"])
def test_a_definition_that_moves_prepared_rows_adds_no_weight(driven, rows, knowledge, change, monkeypatch, what):
    """The support marker reads the source events, never the prepared
    tables a definition change can move: a prep version whose upgrade
    candidates differ, proposed again on the same play, is judged, not
    better supported. New source events still count, once."""
    env, drive = driven
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding())["stored"]
    w0 = analysis.load_state(env, CHAR)["findings"]["sessions-online"]["weight"]
    change((what,))
    real = analysis.upgrades_for
    monkeypatch.setattr(analysis, "upgrades_for", lambda *a: real(*a)[:-3])  # the new prep's tables differ
    out = _night(env, rows, knowledge)
    assert out["runs"][-1]["revalidate"] == 1 and out["findings"]["sessions-online"]["weight"] == w0
    drive._WS.clear()
    drive.brief(env, CHAR, restart=True)
    assert drive.propose(env, CHAR, finding())["stored"]
    f = analysis.load_state(env, CHAR)["findings"]["sessions-online"]
    assert "revalidate" not in f and f["weight"] == w0 and f["confirmations"] == 0
    assert _night(env, rows, knowledge)["findings"]["sessions-online"]["weight"] == w0
    # New play does count, once.
    last = max(rows, key=lambda r: r["_time"])
    later = [dict(r) for r in rows] + [{**last, "_time": last["_time"][:4] + "-12-31T23:00:00Z"}]
    grown = _night(env, later, knowledge)
    assert grown["runs"][-1]["confirmed"] == 1 and grown["findings"]["sessions-online"]["weight"] > w0


def test_the_support_mark_reads_the_source_events_only(rows):
    from wowstats.mcp import route

    ref = route.reference()
    a = prep.build([dict(r) for r in rows], CHAR, ref, upgrades=analysis.upgrades_for(rows, CHAR, HOLY))
    b = prep.build([dict(r) for r in reversed(rows)] + [dict(rows[0])], CHAR, ref, upgrades=[])
    assert analysis.data_mark(a) == analysis.data_mark(b)  # same events, other order, a duplicate, other tables
    last = max(rows, key=lambda r: r["_time"])
    c = prep.build([dict(r) for r in rows] + [{**last, "_time": last["_time"][:4] + "-12-31T23:00:00Z"}], CHAR, ref)
    assert analysis.data_mark(c) != analysis.data_mark(a)


def test_a_support_mark_from_before_counts_todays_play_as_seen(driven, rows, knowledge):
    """A finding supported under the old mark (the prepared tables' rows):
    the first recheck after the upgrade can't compare the two, so it counts
    today's play as seen, with no weight for it; new play after that counts."""
    import json

    env, drive = driven
    drive.brief(env, CHAR)
    assert drive.propose(env, CHAR, finding())["stored"]
    st_file = analysis.char_dir(env, CHAR) / analysis.STATE_FILE
    st = json.loads(st_file.read_text())
    st["findings"]["sessions-online"]["support_mark"] = "0123456789abcdef"  # the old kind
    st_file.write_text(json.dumps(st))
    w0 = st["findings"]["sessions-online"]["weight"]
    out = _night(env, rows, knowledge)
    f = out["findings"]["sessions-online"]
    assert out["runs"][-1]["still"] == 1 and f["weight"] == w0 and f["support_mark"].startswith(analysis.MARK_PREFIX)
    last = max(rows, key=lambda r: r["_time"])
    grown = _night(env, [dict(r) for r in rows] + [{**last, "_time": last["_time"][:4] + "-12-31T23:00:00Z"}],
                   knowledge)
    assert grown["runs"][-1]["confirmed"] == 1 and grown["findings"]["sessions-online"]["weight"] > w0
