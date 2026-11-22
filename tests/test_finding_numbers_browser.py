"""The full view's "How you play" on a real kept state, in a real browser
(Chromium and WebKit): a finding kept before the placeholder rule whose text
types a number nothing binds shows "to revalidate" with why, every finding
says when it was last checked, and the section says how the nightly recheck
works (external review, 2026-11-22). The state goes through the real
wowstats.analysis.answer, migration included.

Needs the `browser` group."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests import degraded as D  # noqa: E402
from wowstats import analysis, ui  # noqa: E402
from wowstats import plan as planmod  # noqa: E402
from wowstats.analysis import verify  # noqa: E402


def _state() -> dict:
    checked = (datetime.now(UTC) - timedelta(hours=3)).isoformat(timespec="seconds")
    base = {"sql": "SELECT COUNT(*) AS n FROM records WHERE kind='death'", "sql_params": None, "engine": "sqlite",
            "expect": {"n": 5}, "row": {"n": 5}, "kb": [], "action": None, "action_template": None,
            "observation": False, "relevance": 1, "weight": 1.0, "score": 1.0, "last_checked": checked,
            "first_seen": checked, "title_template": "Deaths", "title": "Deaths"}
    act = {"do": "Rest before each pull in the Wetlands", "where": "Wetlands", "blocked_by": "",
           "next_step": "Sit down after every fight"}
    return {"schema": analysis.SCHEMA, "char": D.CHAR, "nudges": [], "patterns": [],
            "runs": [{"at": checked, "kind": "client", "new": 2}],
            "findings": {
                "typed": {**base, "key": "typed", "text": "You died 3 times.", "claim_template": "You died 3 times.",
                          "action": act, "action_template": act},
                "bound": {**base, "key": "bound", "text": "You died 5 times.", "score": 0.9,
                          "claim_template": "You died {n} times.", "action": act, "action_template": act}}}


@pytest.fixture(scope="module")
def url(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("full"))
    tmp = tmp_path_factory.mktemp("numbers")
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp / "a"),
           "WOW_ITEMS": "off", "MODEL3D_CACHE_DIR": str(tmp / "c")}
    analysis.save_state(env, D.CHAR, _state())
    planmod._CACHE.clear()
    app = ui.App(env)
    app.port = 0
    srv = ui.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/?char={D.CHAR}"
    srv.shutdown()
    srv.server_close()
    vl.close()
    planmod._CACHE.clear()
    mp.undo()


@pytest.fixture(scope="module")
def pw():
    with sync_api.sync_playwright() as p:
        yield p


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_unbound_numbers_say_to_revalidate_and_each_finding_says_when_checked(url, pw, engine):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: "ResizeObserver loop" in str(e) or errors.append(str(e)))
    try:
        pg.goto(url)
        pg.click("#tab-overview")
        pg.wait_for_selector("#analysis:not([hidden]) .findings", timeout=30000)
        items = pg.locator("#analysis .findings > li")
        typed, bound = items.nth(0).inner_text(), items.nth(1).inner_text()
        assert f"to revalidate: {verify.UNBOUND_WHY} (3)" in typed and "You died 3 times." in typed
        assert "to revalidate" not in bound and "You died 5 times." in bound
        checked = pg.locator("#analysis .findings .checked").all_inner_texts()
        assert checked == ["Checked against your data 3 h ago."] * 2, checked
        refresh = pg.locator("#analysis .refresh").inner_text()
        assert refresh.startswith("Every night from 04:00") and "No nightly recheck yet." in refresh
        assert not errors, errors
    finally:
        b.close()
