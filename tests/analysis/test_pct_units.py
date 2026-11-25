"""`{col:pct}` renders by the unit the column declares, never by the
value's size. External review of 0.11.0: upgrade_gain_share_pct (unit
percent) was 1.5 for Lorekeeper's Ring, the judge accepted the finding, and
the renderer, which took anything up to 1.5 for a 0..1 share, wrote
"Lorekeeper's Ring adds 150% to the worn gear score." The unit checks of
#175 and #197 only refused pct on a column that isn't a percent or a share;
they never reached the renderer. The expected strings here are written by
hand, not computed with the code under test."""

from __future__ import annotations

import pytest

from tests.analysis.test_drive import CHAR, call, env  # noqa: F401  (env: the fixture)
from wowstats import analysis
from wowstats.analysis import _duck_runner, load_state, prep, recheck, verify
from wowstats.analysis.sql import ReadOnly
from wowstats.mcp import route


@pytest.mark.parametrize("value,unit,want", [
    (1.5, "percent", "1.5%"),
    (0.015, "share", "1.5%"),
    (150, "percent", "150%"),
    (0.5, "share", "50%"),
    (1.5, "share", "150%"),       # a share above 1 is still a share: one and a half times
    (0.5, "percent", "0.5%"),     # and a percent under 1 is still a percent
    (62.4, "percent", "62%"),
    (0.256, "ratio", "26%"),
    (0.0004, "share", "0.04%"),
    (0, "percent", "0%"),
])
def test_pct_follows_the_declared_unit(value, unit, want):
    assert verify.fmt(value, "pct", unit) == want


@pytest.mark.parametrize("unit", [None, "points", "seconds", "observations"])
def test_pct_without_a_percent_or_share_unit_is_refused(unit):
    with pytest.raises(verify.UnitError):
        verify.fmt(0.5, "pct", unit)


def test_render_reads_each_columns_unit():
    row = {"p": 1.5, "s": 0.015, "t": 5400}
    assert verify.render("{p:pct} {s:pct} {t:dur}", row, None, {"p": "percent", "s": "share"}) == "1.5% 1.5% 1h30"
    rows = [{"p": 1.5}, {"p": 150}]
    assert verify.render("{p:pct} then {rows[1].p:pct}", rows[0], rows, {"p": "percent"}) == "1.5% then 150%"
    with pytest.raises(verify.UnitError):
        verify.render("{p:pct}", row)  # no units given: refused, not guessed


@pytest.fixture
def lorekeeper(env, monkeypatch):  # noqa: F811
    """The fixture character with the reviewer's number: Lorekeeper's Ring
    adds 1.5% to the worn gear's score (prepared gear improvement 1.5)."""
    real = prep._upgrades

    def patched(db, ups):
        real(db, [{**u, "gain_share_pct": 1.5} if u.get("name") == "Lorekeeper's Ring" else u for u in ups])

    monkeypatch.setattr(prep, "_upgrades", patched)
    # The ring is Silverwing Sentinels' (Warsong Gulch): a PvP reward, an
    # upgrade only with the PvP rewards toggle on (wowstats.gearmode).
    from wowstats import gearmode

    monkeypatch.setattr(gearmode, "pvp", lambda env, char: True)
    return env


RING = {"metrics": ["upgrade_gain_share_pct"], "group_by": ["item"],
        "where": [{"dimension": "item", "op": "=", "value": "Lorekeeper's Ring"}]}


def test_the_lorekeepers_ring_finding_says_1_5_percent(lorekeeper):
    q = call("query_metric", RING)
    assert q["result"].split("\n")[1] == "Lorekeeper's Ring\t1.5", q["result"]
    assert q["definitions"]["upgrade_gain_share_pct"]["unit"] == "percent"
    got = call("propose_finding", {"finding": {
        "key": "lorekeeper-ring", "title": "{item}: {upgrade_gain_share_pct:pct}",
        "claim": "{item} adds {upgrade_gain_share_pct:pct} to the worn gear score.",
        "expect": {"upgrade_gain_share_pct": 1.5}, "relevance": 1, "action": None, "sql": ""},
        "metric_query": RING})
    assert got["stored"], got
    assert got["finding"]["text"] == "Lorekeeper's Ring adds 1.5% to the worn gear score."
    assert got["finding"]["title"] == "Lorekeeper's Ring: 1.5%"
    kept = load_state(lorekeeper, CHAR)["findings"]["lorekeeper-ring"]
    assert kept["units"] == {"upgrade_gain_share_pct": "percent"}
    # The nightly recheck renders it again with the same unit.
    after = _nightly(lorekeeper)
    assert "miss_why" not in after["lorekeeper-ring"], after["lorekeeper-ring"]
    assert after["lorekeeper-ring"]["text"] == "Lorekeeper's Ring adds 1.5% to the worn gear score."


def test_a_share_finding_is_multiplied_once(env):  # noqa: F811
    got = call("propose_finding", {"finding": {
        "key": "combat-share", "title": "Combat {combat_share:pct}",
        "claim": "You spent {combat_share:pct} of your time in combat.",
        "expect": {"combat_share": 0.209}, "relevance": 1, "action": None, "sql": ""},
        "metric_query": {"metrics": ["combat_share"]}})
    assert got["stored"], got
    assert got["finding"]["text"] == "You spent 21% of your time in combat."


def test_a_kept_finding_whose_pct_column_lost_its_unit_is_not_rendered(env):  # noqa: F811
    """Kept before the judge checked units: the recheck refuses to guess."""
    old = {"key": "old", "title_template": "{n:pct}", "claim_template": "Took {n:pct}.",
           "sql": "SELECT COUNT(*) AS n FROM levels", "holds": "n > 0"}
    db = prep.build(prep.query(None, None, CHAR), CHAR, route.reference())
    kept, info = {"old": old}, {"confirmed": 0, "faded": 0, "dropped": []}
    recheck(kept, ReadOnly(db), "2026-11-25T00:00:00+00:00", info, duck=_duck_runner(db))
    assert info["faded"] == 1 and "unit" in kept["old"]["miss_why"], (info, kept)
    assert "text" not in kept["old"]


def _nightly(e: dict) -> dict:
    """The kept findings after the nightly recheck, as the server runs it."""
    analysis.run(e, CHAR, rows=prep.query(None, None, CHAR), players=set())
    return load_state(e, CHAR)["findings"]
