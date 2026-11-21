"""The Character tab's gear advice and comparison in a real browser, on
Testpriest's answer (tests/data/gear-priest.json.gz, the full view's
answer on a synthetic sheet, tools/fixtures/generate.py): the display rule
(at most 3 candidates in view per slot), the comparison on a slot click, the
real gate of a crafted item, and Wowhead as the only outside link, never
loaded.

Same setup as tests/test_browser.py (needs the `browser` group)."""

import gzip
import io
import json
import os
import re
import threading
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser import Catalog, wait_count  # noqa: E402
from tests.test_public_view import CHAR, PoisonStore  # noqa: E402
from wowstats import itemcard, links, publish, static, ui  # noqa: E402
from wowstats.plan import facts  # noqa: E402

DATA = Path(__file__).parent / "data"
WOWHEAD = re.compile("^" + re.escape(links.WOWHEAD_BASE) + r"/(item|quest|npc|spell|zone|object)=\d+$")


def answer(name: str) -> dict:
    return {**json.loads(gzip.decompress((DATA / name).read_bytes())), "char": CHAR}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("advice")
    mp = pytest.MonkeyPatch()
    mp.setattr(ui, "Store", lambda *a, **k: PoisonStore())
    # The Dungeons panel reads the planner's inputs from a store of its own
    # (facts.store_for), which would ask vl.invalid. The full run passed only
    # because tests/mcp/test_helppage.py had made this patch first and its
    # answer was still cached.
    mp.setattr(facts, "store_for", lambda env, like=None: like)
    mp.setattr(itemcard, "shared", lambda: Catalog())
    mp.setenv("WOW_ITEMS", "off")
    gear = answer("gear-priest.json.gz")
    mp.setattr(ui.App, "gear", lambda self, name, spec: gear)
    env = {"VL_URL": "http://vl.invalid", "MODEL3D": "off", "PUBLISH_DIR": str(tmp / "site"),
           "ZOGZOG_KEY_FILE": str(tmp / "zogzog.key"), "MODEL3D_CACHE_DIR": str(tmp / "cache")}
    out = io.StringIO()
    with redirect_stdout(out):
        publish.publish(env)
    key = Path(env["ZOGZOG_KEY_FILE"]).read_text().strip()  # never printed
    s = static.Site({"SITE_DIR": str(tmp / "site"), "ZOGZOG_PATH": "/zogzog/", "BIND": "127.0.0.1", "PORT": "0"})
    srv = static.Server(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://wow.localhost:{srv.server_address[1]}/zogzog/?char={CHAR}#{key}", gear
    srv.shutdown()
    mp.undo()


@pytest.fixture(scope="module")
def pw(site):
    with sync_api.sync_playwright() as p:
        yield p


def open_page(pw, engine, url, width):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # browsers not installed
        pytest.skip(f"no {engine}: {e}")
    pg = b.new_page(viewport={"width": width, "height": 1000})
    seen = []
    pg.on("request", lambda r: seen.append(r.url))
    pg.goto(url)
    pg.locator(".tabs button", has_text="Character").first.click()
    wait_count(pg, "#advice .cand")
    return b, pg, seen


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1920, 390])
def test_at_most_three_candidates_in_view_per_slot(site, pw, engine, width):
    url, gear = site
    b, pg, _ = open_page(pw, engine, url, width)
    try:
        for view in ("By where you get it", "By slot"):
            pg.locator("#advice .order button", has_text=view).click()
            wait_count(pg, "#advice .cand")
            slots = pg.locator("#advice .cand:visible").evaluate_all("els => els.map(e => e.dataset.slot)")
            counts = {s: slots.count(s) for s in set(slots)}
            assert counts and max(counts.values()) <= 3, (view, counts)
            per_list = pg.locator("#advice ul.cands").evaluate_all(
                "els => els.filter(e => e.checkVisibility()).map(e => e.querySelectorAll(':scope > .cand').length)")
            assert max(per_list, default=0) <= 3, (view, per_list)
            names = pg.locator("#advice .cand:visible .itm .iname").all_inner_texts()
            assert len(names) == len(set(names)), f"{view}: an item shown twice: {names}"
            cut = pg.locator("#advice .cand:visible .itm .iname").evaluate_all(
                "els => els.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent)")
            assert not cut, f"{view} at {width}: names cut short: {cut}"
            if view == "By slot" and width > 640:
                # A table, not a grid a row (.cand's list style once made it
                # one: every cell its own line, the columns adrift).
                shown = pg.locator("#advice .byslot tr.cand").evaluate_all(
                    "els => els.map(e => getComputedStyle(e).display)")
                assert shown and set(shown) == {"table-row"}, shown
        # Nothing dropped: the rest is behind "N more" and "N small upgrades".
        hidden = pg.locator("#advice details.upmore .cand").count()
        assert hidden + len(slots) >= len(gear["upgrades"]) - 3
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1920, 390])
def test_slot_click_opens_the_comparison(site, pw, engine, width):
    url, gear = site
    b, pg, _ = open_page(pw, engine, url, width)
    try:
        worn = set(pg.locator("#gear .slot:has(button.itm)").evaluate_all("els => els.map(e => +e.dataset.slot)"))
        slot = next(s for s in gear["slots"] if s["best"] and s["slot"] in worn)
        pg.locator(f'#gear .slot[data-slot="{slot["slot"]}"] button.itm').first.click()
        wait_count(pg, ".cmp", 1)
        cmp = pg.locator(".cmp")
        text = cmp.inner_text()
        assert "You wear" in text and "Best replacement" in text and "Where to get it" in text
        assert not re.search(r"Polestar|new − [\d.]+ worn", text), "the score's arithmetic is not shown"
        assert re.search(r"\+[\d.]+", cmp.locator(".cmphead .gainbig").inner_text())
        assert cmp.locator(".cmpstats tbody tr").count() >= 1
        assert pg.locator("#wowtip:visible").count() == 0, "the comparison, not the tooltip"
        best = cmp.locator(".cmpside").nth(1).locator(".iname").first.inner_text()
        cmp.locator(".cmpclose").click()
        # Every slot: the best is never its own alternative (the doll's copy
        # and the list's copy are different objects).
        for sl in [s for s in gear["slots"] if s["best"]]:
            pg.locator(f'#gear .slot[data-slot="{sl["slot"]}"]').first.click()
            wait_count(pg, ".cmp", 1)
            best = pg.locator(".cmp .cmpside").nth(1).locator(".iname").first.inner_text()
            alts = pg.locator(".cmp .cmpalts button.linkish").all_inner_texts()
            assert best not in alts, f"{best} offered as its own alternative: {alts}"
        pg.locator(".cmp .cmpclose").click()
        wait_count(pg, ".cmp", 0)
        pg.locator(f'#gear .slot[data-slot="{slot["slot"]}"] button.itm').first.click()
        wait_count(pg, ".cmp", 1)
        cmp.locator(".cmpclose").click()
        wait_count(pg, ".cmp", 0)
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_icy_cloak_shows_its_real_gate(site, pw, engine):
    url, _ = site
    b, pg, _ = open_page(pw, engine, url, 1920)
    try:
        pg.locator("#advice .order button", has_text="By where you get it").click()
        pg.evaluate("() => document.querySelectorAll('#advice details').forEach((d) => { d.open = true; })")
        row = pg.locator("#advice .cand", has_text="Icy Cloak")
        assert row.count() == 1
        pill = row.locator(".pill")
        assert pill.inner_text() == "Level 35 + Tailoring 200"
        assert "Tailoring reaches 200 (you have 105, 95 to go)" in pill.get_attribute("title")
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_character_tab_links_wowhead_only(site, pw, engine):
    url, gear = site
    b, pg, seen = open_page(pw, engine, url, 1920)
    try:
        pg.locator("#advice details.upmore summary").first.click()
        slot = next(s for s in gear["slots"] if s["best"])
        pg.locator(f'#gear .slot[data-slot="{slot["slot"]}"]').first.click()
        wait_count(pg, ".cmp", 1)
        links = pg.locator("#gear a[href], #advice a[href], .cmp a[href]").evaluate_all(
            "els => els.map(a => [a.href, a.target, a.rel, a.getAttribute('referrerpolicy')])")
        assert links, "no outside link at all"
        for href, target, rel, ref in links:
            assert WOWHEAD.match(href), href
            assert target == "_blank" and set(rel.split()) == {"noopener", "noreferrer"} and ref == "no-referrer"
        kinds = {WOWHEAD.match(h).group(1) for h, *_ in links}
        assert {"item", "quest", "npc"} <= kinds, kinds
        assert not [u for u in seen if "wowhead" in u], "Wowhead is linked, never loaded"
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_each_upgrade_says_who_it_is_built_for(site, pw, engine, scheme):
    """Every upgrade row carries its fit line (wowstats/gearfit.py): Testpriest
    is Shadow at 32, so Embalmed Shroud, a healer's head piece, reads as not
    made for his spec but fine while levelling (he still heals himself
    before Shadowform), and its comparison names the head piece built for
    him and where it comes from. WOW_SHOTS=<dir> saves the list and the
    comparison."""
    url, gear = site
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # browsers not installed
        pytest.skip(f"no {engine}: {e}")
    try:
        pg = b.new_page(viewport={"width": 1280, "height": 1000}, color_scheme=scheme)
        pg.goto(url)
        pg.locator(".tabs button", has_text="Character").first.click()
        wait_count(pg, "#advice .cand")
        pg.locator("#advice details.upmore").evaluate_all("els => els.forEach(e => e.open = true)")
        rows = pg.locator("#advice li.cand")
        assert rows.count() and rows.count() == pg.locator("#advice li.cand .fit").count()
        shroud = pg.locator("#advice li.cand", has_text="Embalmed Shroud").first
        line = shroud.locator(".fit").inner_text()
        assert line.startswith("Not made for your spec. Fine while levelling") and "don't roll Need" in line, line
        assert pg.locator("#advice li.cand .fit.yes", has_text="Built for your spec").count() >= 10
        # Readable in both schemes: the label isn't the page's own colour.
        col = shroud.locator(".fitlabel").evaluate("e => getComputedStyle(e).color")
        bg = pg.evaluate("() => getComputedStyle(document.body).backgroundColor")
        assert col != bg
        shots = os.environ.get("WOW_SHOTS")
        if shots:
            shroud.scroll_into_view_if_needed()
            pg.screenshot(path=f"{shots}/fit-list-{engine}-{scheme}.png")
        shroud.locator(".candmain .itm").click()
        wait_count(pg, ".cmp", 1)
        alt = pg.locator(".cmp .fitalt").inner_text()
        assert alt.startswith("Built for your spec instead: ") and "Electromagnetic Gigaflux Reactivator" in alt, alt
        if shots:
            pg.locator(".cmp").screenshot(path=f"{shots}/fit-compare-{engine}-{scheme}.png")
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1920, 390])
def test_the_doll_says_where_each_item_stands(site, pw, engine, width):
    """Each worn item with a BiS to measure against says BiS, 2nd or its
    percent of BiS on its icon, at 390 px too; the upgrades say theirs in BiS
    terms, and By slot puts the slot furthest from BiS first (owner,
    2026-11-21)."""
    url, gear = site
    b, pg, _ = open_page(pw, engine, url, width)
    try:
        want = {}
        for s in gear["slots"]:
            st = s.get("standing")
            if st and s.get("current"):
                want[str(s["slot"])] = "BiS" if st["rank"] == 1 else "2nd" if st["rank"] == 2 else f"{st['pct']}%"
        # The doll is the page's own sheet, the advice a separate answer: the
        # slots both fill.
        filled = set(pg.locator("#gear .slot:not(.empty)").evaluate_all("els => els.map(e => e.dataset.slot)"))
        want = {k: v for k, v in want.items() if k in filled}
        assert want, "the fixture has no standing to show"
        got = pg.locator("#gear .slot .ifr .ibis").evaluate_all(
            "els => els.filter(e => e.checkVisibility()).map(e => [e.closest('.slot').dataset.slot, e.textContent])")
        assert dict(got) == want
        assert pg.locator("#advice .cand .bisgain:visible").count() > 0
        assert all("% of BiS" in t for t in pg.locator("#advice .cand .bisgain").all_text_contents())
        pg.locator("#advice .order button", has_text="By slot").click()
        rows = pg.locator("#advice .byslot tbody tr.cand").evaluate_all(
            "els => els.map(e => { const t = (e.querySelector('.bisgain') || {}).textContent || '';"
            " const m = t.match(/^(\\d+)% → /); return m ? +m[1] : null; })")
        ranked = [r for r in rows if r is not None]
        assert ranked and ranked == sorted(ranked)
        assert rows[: len(ranked)] == ranked  # the ones with no BiS after
    finally:
        b.close()
