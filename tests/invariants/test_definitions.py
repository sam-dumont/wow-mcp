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
