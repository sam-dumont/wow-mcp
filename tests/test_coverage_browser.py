"""The coverage lines on the full view, in a real browser (wowstats.coverage):
one short line where a missing stretch of a source changes what a number
means, nowhere else.

The docs demo (tests/degraded.py) with the Recorder off for an hour of one
evening and no Syndicator sheet, so the worn items come from Battle.net,
read 5 hours ago from the last complete read:
- Deaths: "The Recorder missed about 1h 10m of play (level 28) ...";
- the journal, on the newest session of level 28 only;
- Equipment: "Equipment last read 5 h ago ...".
The full demo (no gap, a fresh read) shows none of them.

The static sites the publisher builds from the same store (owner,
2026-11-22): /zogzog/ says the same lines, word for word; the public site
says "Equipment may be out of date." under the equipment and nothing with a
time, a length or a level (it has no Deaths or journal).

COVERAGE_SHOTS=<dir> also saves each line's section at 390 and 1280 px, light
and dark, per engine."""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests import degraded as D  # noqa: E402
from tests.conftest import Running  # noqa: E402
from tests.test_browser import wait_count  # noqa: E402
from tests.test_coverage_static import Built, gap_rows, stale_read  # noqa: E402
from wowstats import coverage, static, ui  # noqa: E402
from wowstats import gear as gearmod  # noqa: E402
from wowstats.mcp import tools as T  # noqa: E402

SHOTS = os.environ.get("COVERAGE_SHOTS")
WORN = {"head": {"item_id": 10504, "stats": {}}, "chest": {"item_id": 14152, "stats": {}}}


def _serve(tmp, rows, bnet):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    mp.setattr(gearmod, "armory_worn", lambda store, char: gearmod.worn_stats(WORN))
    mp.setattr(coverage, "bnet_of", lambda store, char: bnet())
    vl = D.VL(rows)
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "MODEL3D": "off",
           "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp), "WOW_ITEMS": "off"}
    T._MISSING.clear()
    r = Running(ui.App(env))
    return r, vl, mp


@pytest.fixture(scope="module")
def gap(tmp_path_factory):
    rows = [r for r in D.rows_for("recorder-gap") if r["feed"] not in ("wow-gear", "wow-bags")]
    r, vl, mp = _serve(tmp_path_factory.mktemp("cov"), rows,
                       lambda: {"as_of": time.time() - 5 * 3600, "fallback": True})
    yield r
    vl.close()
    mp.undo()


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    rows = [r for r in D.base_rows() if r["feed"] not in ("wow-gear", "wow-bags")]
    r, vl, mp = _serve(tmp_path_factory.mktemp("full"), rows, lambda: {"as_of": time.time() - 1800, "fallback": False})
    yield r
    vl.close()
    mp.undo()


def _page(engine, width, scheme="light"):
    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, engine).launch()
        except Exception as e:  # browsers not installed
            pytest.skip(f"no {engine}: {e}")
        try:
            yield b.new_page(viewport={"width": width, "height": 1000}, color_scheme=scheme)
        finally:
            b.close()


def _shot(pg, sel: str, name: str, until: str | None = None) -> None:
    """`sel` (down to the bottom of `until`, inside it, when given)."""
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        el = pg.locator(sel).first
        el.scroll_into_view_if_needed()
        path = str(Path(SHOTS) / f"{name}.png")
        if until is None:
            el.screenshot(path=path)
            return
        a, b = el.bounding_box(), pg.locator(until).first.bounding_box()
        y = pg.evaluate("() => scrollY")  # the boxes are the viewport's, the clip the page's
        pg.screenshot(path=path, full_page=True, clip={"x": a["x"], "y": a["y"] + y, "width": a["width"],
                                                       "height": b["y"] + b["height"] - a["y"]})


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("scheme", ["light", "dark"] if SHOTS else ["light"])
def test_the_lines_where_they_change_the_meaning(gap, engine, width, scheme):
    for pg in _page(engine, width, scheme):
        pg.goto(f"{gap.base}/?char={D.CHAR}&tab=overview")
        wait_count(pg, "#deaths p.covnote", 1)
        assert pg.locator("#deaths p.covnote").inner_text().startswith("The Recorder missed about 1h 1")
        assert "(level 28)" in pg.locator("#deaths p.covnote").inner_text()
        # Per level over the levels the Recorder saw whole: 28 out of both sides (review 6).
        per = pg.locator("#deaths dl.sum div:has(dt:text-is('Per level')) dd").inner_text()
        assert per == "0.9 over 10 levels recorded whole", per
        wait_count(pg, "#gear p.covnote", 1)
        assert pg.locator("#gear p.covnote").inner_text() == "Equipment last read 5 h ago: what you wear now may differ."
        over = pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert over <= 0, over
        tag = f"{engine}-{width}-{scheme}"
        _shot(pg, "#deaths", f"deaths-{tag}")
        _shot(pg, "#gear", f"equipment-{tag}", until="#gear .dollfoot")
        pg.goto(f"{gap.base}/?char={D.CHAR}&tab=journal")
        wait_count(pg, "#journal .jentry", None)
        # The journal is drawn twice on a load (the tab, then the character):
        # open a session only once both are in, or the second closes it.
        pg.wait_for_load_state("networkidle")
        # Once, on the newest session of level 28: open it to read it.
        pg.locator("#journal .jentry details:has(p.covnote) summary").first.click()
        note = pg.locator("#journal p.covnote:visible")
        wait_count(pg, "#journal p.covnote:visible", 1)
        assert pg.locator("#journal p.covnote").count() == 1
        assert note.inner_text().startswith("The Recorder missed about 1h 1") and "level 28" in note.inner_text()
        _shot(pg, "#journal .jentry:has(p.covnote)", f"journal-{tag}")


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_no_line_with_nothing_missed(full, engine):
    for pg in _page(engine, 1280):
        pg.goto(f"{full.base}/?char={D.CHAR}&tab=overview")
        wait_count(pg, "#deaths .body > *")
        wait_count(pg, "#gear .dollfoot")  # and settled: nothing more to draw
        assert pg.locator("p.covnote").count() == 0
        pg.goto(f"{full.base}/?char={D.CHAR}&tab=journal")
        wait_count(pg, "#journal .jentry")
        assert pg.locator("#journal p.covnote").count() == 0


# ---------------------------------------------------------------- the static sites


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    """The publisher's two bundles over the `gap` store, served by the pod's
    own static server; the zogzog key is a throwaway one (Built)."""
    b = Built(tmp_path_factory.mktemp("covsites"), gap_rows(), stale_read())
    site = static.Site({"SITE_DIR": b.env["PUBLISH_DIR"], "ZOGZOG_PATH": "/zogzog/", "BIND": "127.0.0.1", "PORT": "0"})
    srv = static.Server(("127.0.0.1", 0), static.make_handler(site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://wow.localhost:{srv.server_address[1]}"
    yield {"public": f"{base}/?char={D.CHAR}", "zogzog": f"{base}/zogzog/?char={D.CHAR}&tab=overview#{b.key}"}
    srv.shutdown()
    b.close()


def _lines(pg) -> dict[str, str]:
    wait_count(pg, "#deaths p.covnote", 1)
    wait_count(pg, "#gear p.covnote", 1)
    return {s: pg.locator(f"{s} p.covnote").inner_text() for s in ("#deaths", "#gear")}


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("scheme", ["light", "dark"] if SHOTS else ["light"])
def test_zogzog_says_the_full_views_lines(gap, sites, engine, width, scheme):
    for pg in _page(engine, width, scheme):
        pg.goto(f"{gap.base}/?char={D.CHAR}&tab=overview")
        full = _lines(pg)
        # One page per address: WebKit's driver crashes when a page swaps
        # its process (another origin, or another query before the same #key).
        pg = pg.context.browser.new_page(viewport={"width": width, "height": 1000}, color_scheme=scheme)
        pg.goto(sites["zogzog"])
        zz = _lines(pg)
        assert zz == full and "(level 28)" in zz["#deaths"] and "5 h ago" in zz["#gear"], zz
        assert pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0
        tag = f"{engine}-{width}-{scheme}"
        _shot(pg, "#deaths", f"zogzog-deaths-{tag}")
        _shot(pg, "#gear", f"zogzog-equipment-{tag}", until="#gear .dollfoot")
        # The journal's line, on the same session as the full view's.
        pg = pg.context.browser.new_page(viewport={"width": width, "height": 1000}, color_scheme=scheme)
        pg.goto(sites["zogzog"].replace("tab=overview", "tab=journal"))
        wait_count(pg, "#journal .jentry details:has(p.covnote)")  # and the page settled: no second draw to come
        pg.locator("#journal .jentry details:has(p.covnote) summary").first.click()
        wait_count(pg, "#journal p.covnote:visible", 1)
        note = pg.locator("#journal p.covnote").inner_text()
        assert note.startswith("The Recorder missed about 1h 1") and "level 28" in note


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("scheme", ["light", "dark"] if SHOTS else ["light"])
def test_public_says_it_without_a_time(sites, engine, width, scheme):
    for pg in _page(engine, width, scheme):
        pg.goto(sites["public"])
        wait_count(pg, "#gear p.covnote", 1)
        assert pg.locator("p.covnote").all_inner_texts() == ["Equipment may be out of date."]
        assert pg.locator("#deaths").count() == 0 and pg.locator("#journal").count() == 0
        text = pg.locator("body").inner_text()
        assert not re.search(r"\bago\b|last read|missed|Recorder", text), text
        assert pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0
        _shot(pg, "#gear", f"public-equipment-{engine}-{width}-{scheme}", until="#gear .dollfoot")
