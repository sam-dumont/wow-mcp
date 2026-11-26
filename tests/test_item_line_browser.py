"""The one item line in a real browser (Chromium and WebKit, 390 and 1280
px) on the demo character (tests/degraded.py's VictoriaLogs stand-in),
after the 0.7.0 tour showed three forms (owner, 2026-11-21):

- the doll: "17% → 82% with the best at hand" under a slot, from that
  slot's badge (a phone hides the line, the badge on the icon stays);
  a ring or trinket pair as #69's rule has it: the weaker slot gets the
  best item, the stronger the next;
- the Upgrades list, both views, and its comparison: "17% → 100% on your
  head", no slot chip in capitals;
- Back in time: "Best upgrades then: X (0% → 100% on your trinket)".

Needs the `browser` group."""

from __future__ import annotations

import re
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests import degraded as D  # noqa: E402
from tests.test_bis_one_number import _talents  # noqa: E402
from wowstats import plan as planmod  # noqa: E402
from wowstats import ui  # noqa: E402

pytestmark = pytest.mark.usefixtures("demo_clock")

GEAR = r"(empty|\d+%) → (\d+)% on your ([a-z ]+)"
DOLL = re.compile(r"^(empty|\d+%) → (\d+)% with the best at hand$")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("full"), metrics=_talents())
    tmp = tmp_path_factory.mktemp("itemline")
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp / "a"),
           "WOW_ITEMS": "off", "MODEL3D_CACHE_DIR": str(tmp / "c")}
    planmod._CACHE.clear()
    app = ui.App(env)
    app.port = 0
    srv = ui.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/?char={D.CHAR}", app.gear(D.CHAR, None)
    srv.shutdown()
    srv.server_close()
    vl.close()
    planmod._CACHE.clear()
    mp.undo()


@pytest.fixture(scope="module")
def pw():
    with sync_api.sync_playwright() as p:
        yield p


def open_page(pw, engine, url, width):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    pg = b.new_page(viewport={"width": width, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: "ResizeObserver loop" in str(e) or errors.append(str(e)))
    pg.goto(url)
    return b, pg, errors


def no_sideways(pg, width):
    over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 1, f"{over}px sideways at {width}"


ENGINES = ["chromium", "webkit"]
WIDTHS = [1280, 390]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", WIDTHS)
def test_doll_says_the_best_at_hand(site, pw, engine, width):
    url, gear = site
    b, pg, errors = open_page(pw, engine, url, width)
    try:
        pg.click("#tab-overview")
        pg.wait_for_selector("#gear .slot .iup", state="attached", timeout=30000)
        got = pg.eval_on_selector_all("#gear .slot:has(.iup)", """els => els.map(e => ({
          slot: +e.dataset.slot, line: e.querySelector('.iup').textContent,
          badge: (e.querySelector('.ifr .ibis') || {}).textContent || null,
          shown: getComputedStyle(e.querySelector('.iup')).display !== 'none',
          lines: e.querySelector('.iup').getClientRects().length ?
            Math.round(e.querySelector('.iup').getBoundingClientRect().height /
                       parseFloat(getComputedStyle(e.querySelector('.iup')).lineHeight)) : 0}))""")
        by = {s["slot"]: s for s in gear["slots"]}
        assert {g["slot"] for g in got} == {s for s, x in by.items() if x["best"]}
        for g in got:
            m = DOLL.match(g["line"])
            assert m, g
            best = by[g["slot"]]["best"]
            assert m.group(2) == str(best["bis_pct"]), (g, best["bis_pct"])
            # From the slot's own badge (BiS reads 100%), or "empty".
            want = "empty" if by[g["slot"]]["current"] is None else (
                "100%" if g["badge"] == "BiS" else g["badge"])
            assert m.group(1) == want, g
            if width >= 1000:
                assert g["shown"] and g["lines"] == 1, f"one short line on the doll: {g}"
            else:
                assert not g["shown"], "a phone shows the badge on the icon, not the line"
        # The pairs: the weaker worn slot holds the best item, the stronger
        # the next, never the same one.
        for x, y in ((11, 12), (13, 14)):
            bx, by_ = by[x]["best"], by[y]["best"]
            if not (bx and by_):
                continue
            cx, cy = by[x]["current"], by[y]["current"]
            weak, strong = (bx, by_) if cy and (cx is None or cx["score"] <= cy["score"]) else (by_, bx)
            assert weak["item_id"] != strong["item_id"] and weak["score"] >= strong["score"], (weak, strong)
        no_sideways(pg, width)
        assert not errors, errors
    finally:
        b.close()


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", WIDTHS)
def test_upgrades_list_says_the_line(site, pw, engine, width):
    url, _gear = site
    b, pg, errors = open_page(pw, engine, url, width)
    try:
        pg.click("#tab-overview")
        pg.wait_for_selector("#advice .cand .gain", timeout=30000)
        for view in ("By where you get it", "By slot"):
            pg.get_by_role("button", name=view).click()
            pg.wait_for_selector("#advice .cand .gain")
            lines = pg.eval_on_selector_all("#advice .cand .gain", "els => els.map(e => e.textContent)")
            assert len(lines) >= 5, (view, lines)
            for t in lines:
                assert re.match(rf"^{GEAR}$|^[A-Za-z ]+, [+−][\d.]+ points$", t), (view, t)
            assert not pg.locator("#advice .candmeta .slotname").count(), "the slot is in the line, not a chip"
        # The comparison says it too.
        pg.get_by_role("button", name="By where you get it").click()
        pg.locator("#advice .cand .candmain .itm").first.click()
        head = pg.locator(".cmp .cmphead .gainbig").inner_text().strip()
        assert re.match(rf"^{GEAR}$", head), head
        no_sideways(pg, width)
        assert not errors, errors
    finally:
        b.close()


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", WIDTHS)
def test_back_in_time_says_the_line(site, pw, engine, width):
    url, _gear = site
    b, pg, errors = open_page(pw, engine, url, width)
    try:
        pg.click("#tab-timeline")
        slider = pg.locator("input[type=range][aria-label=Level]")
        slider.wait_for(timeout=30000)
        seen = []
        for i in range(int(slider.get_attribute("max")), -1, -1):
            slider.fill(str(i))
            # The level drawn, not a fixed wait: a cold snapshot takes longer
            # than 400 ms on a CI runner.
            lv = pg.locator(".snapctl output").inner_text().split()[-1]
            pg.wait_for_selector(f".snapbody[data-level='{lv}']", timeout=30000)
            p = pg.locator(".snap p:has(b:text('Best upgrades then'))")
            if p.count():
                seen.append(p.first.inner_text())
            if len(seen) >= 2:
                break
        assert seen, "no level with its best upgrades on the demo"
        for text in seen:
            parts = re.findall(r"\(([^()]*)\)", text)
            assert parts, text
            for part in parts:
                assert re.match(rf"^{GEAR}$|^[A-Za-z ]+, [+−][\d.]+ points$", part), text
        no_sideways(pg, width)
        assert not errors, errors
    finally:
        b.close()


def test_timeline_redraw_keeps_pending_level(site, pw):
    b, pg, errors = open_page(pw, "chromium", site[0], 390)
    try:
        pg.click("#tab-timeline")
        pg.locator(".snapbody[data-level]").wait_for()
        slider = pg.get_by_role("slider", name="Level")
        # Clicking an event filter redraws the same timeline. Do both actions
        # in one turn so the redraw always lands before the snapshot debounce.
        index, label = slider.evaluate("""slider => {
          slider.stepDown();
          slider.dispatchEvent(new Event('input', {bubbles: true}));
          const chosen = [slider.value, document.querySelector('.snapctl output').textContent];
          document.querySelector('#timeline button.chip').click();
          return chosen;
        }""")
        assert slider.input_value() == index
        assert pg.locator(".snapctl output").inner_text() == label
        level = label.split()[-1]
        pg.locator(f".snapbody[data-level='{level}']").wait_for()
        assert not errors, errors
    finally:
        pg.close()
        b.close()
