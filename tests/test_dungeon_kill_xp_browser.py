"""The Dungeons panel's kill XP in a real browser (Chromium and WebKit, 390
and 1280) on the demo character (owner, 2026-11-23): one "Kill XP a run"
fact per dungeon block, after the quests and the gear, said as measured, an
estimate or not enough data yet, with no source named; the panel's one note
that Forever's dungeons are for their quests and gear; and the Overview's
dungeon move saying the panel's own line, time included.

Needs the `browser` group."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dungeon_lines_browser import open_moves, open_page, pw, site  # noqa: E402,F401

SOURCES = re.compile(r"cMaNGOS|EG Link|Efficient Games|advocaite|TrinityCore|Everlake|spawn table", re.I)


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_kill_xp_row_after_quests_and_gear(site, pw, engine, width):  # noqa: F811
    b, pg, errors = open_page(pw, engine, site, width)
    try:
        open_moves(pg)
        pg.wait_for_selector("#home .move.k-dungeon", timeout=30000)
        move_gain = pg.locator("#home .move.k-dungeon .mgain").first.text_content()
        pg.click("#tab-overview")
        pg.wait_for_selector(".dg .dgfacts", timeout=30000)
        note = pg.locator("#dungeons .dgnote").text_content()
        assert note == "Forever's dungeons are worth it for their quests and gear, not for running the same one " \
                       "over and over."
        rows = pg.eval_on_selector_all(
            ".dg", """els => els.map(e => {
                const dl = [...e.querySelectorAll('.dgfacts > div')].find(d => d.querySelector('dt').textContent === 'Kill XP a run');
                const sec = [...e.querySelectorAll('.dgsec, .dgfacts, .dgdrop, .dgqs')];
                const at = dl ? sec.indexOf(dl.closest('.dgsec')) : -1;
                const before = dl ? [...e.querySelectorAll('.dgqs, .dgdrop')].every(x =>
                    x.compareDocumentPosition(dl) & Node.DOCUMENT_POSITION_FOLLOWING) : null;
                return {name: e.querySelector('.dghead h3').textContent, kill: dl ? dl.innerText : null,
                        after: before, text: e.innerText, gain: (e.querySelector('.dggain') || {}).textContent};
            })""")
        killed = [r for r in rows if r["kill"]]
        assert killed, rows
        for r in killed:
            assert r["after"], f"{r['name']}: kill XP before its quests or gear"
            assert re.search(r"^Kill XP a run\n(~[\d,.]+k?\n(estimate|measured on your runs)"
                             r"|not enough data yet\nyour first run here measures it)", r["kill"]), r
            assert not SOURCES.search(r["text"]), r["name"]
        kinds = {("estimate" if "estimate" in r["kill"] else "measured" if "measured" in r["kill"] else "none")
                 for r in killed}
        assert {"estimate", "measured", "none"} <= kinds, kinds
        # The Overview's move says the panel's line, word for word, time included.
        assert move_gain in [r["gain"] for r in rows], (move_gain, [r["gain"] for r in rows])
        over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 1, f"{over}px sideways at {width}"
        assert not errors, errors
    finally:
        b.close()
