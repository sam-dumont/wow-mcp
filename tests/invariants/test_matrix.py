"""The consumer x guarantee matrix's own cells (docs/guarantees.md, "Every
consumer, every guarantee"): the ones no other test proved, each written
failing first where the consumer broke it.

- unknown != zero: without the Recorder, the deaths count is unknown on the
  full view and the /zogzog/ bundle, as it is over MCP (the public view
  shows no deaths at all);
- freshness: the public and /zogzog/ answers carry the time they were built;
- completeness: the nightly recheck answers for every character it can,
  and says which it couldn't, when one character's store read fails;
- the same config as the UI: the analysis scores gear for the WEIGHTS the
  page reads, through views.settings_from_env.
"""

from __future__ import annotations

import pytest

from tests import degraded as D
from wowstats import analysis, redact, ui
from wowstats.stores import IncompleteRead
from wowstats.views import settings_from_env

pytestmark = pytest.mark.usefixtures("demo_clock")


def _app(rows, tmp_path):
    """The full view; the public and /zogzog/ answers are its answers through
    their tier's shape (wowstats.redact), as the publisher builds them."""
    vl = D.VL(rows)
    return vl, ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "MODEL3D": "off",
                       "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp_path), "WOW_ITEMS": "off"})


@pytest.mark.parametrize("scenario, total", [("no-recorder", None), ("full", 7)])
def test_deaths_without_the_recorder_are_unknown_in_every_view(tmp_path, scenario, total):
    vl, app = _app(D.rows_for(scenario), tmp_path)
    try:
        full = app.api_char(D.CHAR)
        views = {"full": full["deaths"]["total"], "zogzog": redact.zogzog("char", full)["deaths"]["total"]}
        assert "deaths" not in redact.char(full)
    finally:
        vl.close()
    if total is None:
        assert views == {"full": None, "zogzog": None}
    else:
        assert set(views.values()) == {views["full"]} and isinstance(views["full"], int) and views["full"] > 0


def test_the_static_answers_say_when_they_were_built(tmp_path):
    vl, app = _app(D.rows_for("full"), tmp_path)
    try:
        full = app.api_char(D.CHAR)
        for tier in ("public", "zogzog"):
            got = redact.TIERS[tier]("char", full)
            assert got["generated_at"] and got["generated_at"] == full["generated_at"], tier
            for what in {"public": ("timeline", "snapshot"), "zogzog": ("journal", "timeline", "snapshot")}[tier]:
                assert redact.TIERS[tier](what, {"generated_at": "x"})["generated_at"] == "x", (tier, what)
    finally:
        vl.close()


def test_one_characters_store_failure_leaves_the_others_rechecked(monkeypatch, tmp_path, capsys):
    chars = ["Aaa-Realm", "Bbb-Realm", "Ccc-Realm"]
    seen = []

    def run(env, char):
        seen.append(char)
        if char == "Bbb-Realm":
            raise IncompleteRead({"2026-11-18T09:00:00Z": 600}, 500)

    monkeypatch.setattr(analysis, "characters", lambda env: list(chars))
    monkeypatch.setattr(analysis, "run", run)
    failed: list[str] = []
    done = analysis.run_all({"ANALYSIS_STATE_DIR": str(tmp_path)}, failed=failed)
    assert seen == chars
    assert done == ["Aaa-Realm", "Ccc-Realm"] and failed == ["Bbb-Realm"]
    assert "Bbb-Realm: not rechecked" in capsys.readouterr().err
    # The command says so too: the schedule records a failed night.
    monkeypatch.setattr(analysis, "run_all", lambda env=None, failed=None: failed.append("Bbb-Realm") or [])
    assert analysis.main([]) == 1


def test_the_analysis_scores_gear_for_the_pages_weights():
    env = {"WEIGHTS": "Nibbet-Gearholt:holy, Other-Realm:shadow"}
    page = settings_from_env(env).weights
    assert page and all(analysis.weights_for(env, c) == page.get(c) for c in [*page, "Nobody-Here"])
