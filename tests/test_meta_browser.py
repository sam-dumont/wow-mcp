"""The state of the meta in a real browser (Chromium and WebKit, phone and
desktop): /zogzog/ shows the class's paragraph and nothing of the score; the
live full view shows the paragraph, the score with its parts, the gaps as
moves with somewhere to go, and the meta in full behind one toggle.

A Gnome priest at level 32 with the fixtures' gear advice
(tests/data/gear-priest.json.gz) over the poison store; the meta itself is
the shipped knowledge base lane. Same setup as tests/test_advice_browser.py
(needs the `browser` group)."""

import gzip
import io
import json
import threading
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from datetime import date  # noqa: E402

from tests.test_browser import Catalog, wait_count  # noqa: E402
from tests.test_public_view import CHAR, PoisonStore  # noqa: E402
from wowstats import classmeta, itemcard, news, publish, static, ui, views  # noqa: E402
from wowstats.plan import facts  # noqa: E402

DATA = Path(__file__).parent / "data"
# The priest's level: 32, or 60 for the raid prep (a fresh App, so a fresh cache).
LEVEL = {"v": 32}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("meta")
    mp = pytest.MonkeyPatch()
    mp.setattr(ui, "Store", lambda *a, **k: PoisonStore())
    mp.setattr(facts, "store_for", lambda env, like=None: like)
    mp.setattr(itemcard, "shared", lambda: Catalog())
    mp.setenv("WOW_ITEMS", "off")
    # What is coming is counted from a fixed day, so the shipped digest's
    # events stay ahead of it.
    mp.setattr(news, "_today", lambda today=None: today or date(2026, 11, 20))
    # What players are saying: one synthetic week (the shipped file may have none yet).
    mp.setattr(news, "community_weeks", lambda: [{"week": "2026-11-19", "items": [
        {"id": "forums-us:1", "source": "forums-us", "title": "Dungeon XP change went too far",
         "url": "https://us.forums.blizzard.com/en/wow/t/dungeon-xp/1", "date": "2026-11-17", "replies": 299,
         "likes": 900, "text": "Players say the group levelling change hurts people who play with friends.",
         "classes": []},
        {"id": "reddit:1abcde", "source": "reddit", "title": "Shadow priest levelling spots",
         "url": "https://www.reddit.com/r/wowforever/comments/1abcde/shadow/", "date": "2026-11-18",
         "text": "Priests swap levelling routes.", "classes": ["PRIEST"]}]}])
    gear = {**json.loads(gzip.decompress((DATA / "gear-priest.json.gz").read_bytes())), "char": CHAR}
    mp.setattr(ui.App, "gear", lambda self, name, spec: gear)
    char = views.Views.char

    def priest(self, name):
        d = char(self, name)
        hd = {**d["header"], "class": "PRIEST", "race": "Gnome", "faction": "Alliance", "level": LEVEL["v"]}
        return {**d, "header": hd, "meta": classmeta.paragraph("PRIEST", "Shadow", LEVEL["v"])}

    mp.setattr(views.Views, "char", priest)
    env = {"VL_URL": "http://vl.invalid", "MODEL3D": "off", "PUBLISH_DIR": str(tmp / "site"),
           "ZOGZOG_KEY_FILE": str(tmp / "zogzog.key"), "MODEL3D_CACHE_DIR": str(tmp / "cache")}
    with redirect_stdout(io.StringIO()):
        publish.publish(env)
    key = Path(env["ZOGZOG_KEY_FILE"]).read_text().strip()  # never printed
    s = static.Site({"SITE_DIR": str(tmp / "site"), "ZOGZOG_PATH": "/zogzog/", "BIND": "127.0.0.1", "PORT": "0"})
    srv = static.Server(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    app = ui.App({"VIEW": "full", "BIND": "127.0.0.1"}, store=PoisonStore())
    app.port = 0
    live = ui.serve(app)
    threading.Thread(target=live.serve_forever, daemon=True).start()
    yield {"zogzog": f"http://wow.localhost:{srv.server_address[1]}/zogzog/?char={CHAR}#{key}",
           "full": f"http://127.0.0.1:{live.server_address[1]}/?char={CHAR}", "char": CHAR}
    srv.shutdown()
    live.shutdown()
    live.server_close()
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
    errors = []
    # WebKit reports a benign ResizeObserver loop as a page error on any page.
    pg.on("pageerror", lambda e: "ResizeObserver loop" in str(e) or errors.append(str(e)))
    pg.goto(url)
    pg.locator(".tabs button", has_text="Character").first.click()
    wait_count(pg, "#meta .metatext")
    return b, pg, errors


def no_side_scroll(pg):
    return pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_zogzog_shows_the_paragraph_only(site, pw, engine, width):
    b, pg, errors = open_page(pw, engine, site["zogzog"], width)
    try:
        box = pg.locator("#meta")
        assert "State of the meta: Priest" in box.locator("h2").inner_text()
        assert "as of" in box.locator("h2").inner_text()
        assert len(box.locator(".metatext").inner_text()) > 60
        assert box.locator(".metascore, .metagaps, details").count() == 0
        assert no_side_scroll(pg) and not errors
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_full_view_shows_the_score_gaps_and_the_meta_in_full(site, pw, engine, width):
    b, pg, errors = open_page(pw, engine, site["full"], width)
    try:
        wait_count(pg, "#meta .metascore")
        box = pg.locator("#meta")
        n = int(box.locator(".msnum b").inner_text())
        assert 0 <= n <= 100
        assert box.locator(".metapart").count() >= 1
        assert "Gear" in box.locator(".metaparts").inner_text()
        # Every gap is a move with somewhere to go on the page.
        moves = box.locator(".metagaps .move")
        assert moves.count() >= 1
        for i in range(min(moves.count(), 3)):
            assert moves.nth(i).locator("a.mgo").get_attribute("href").startswith("#")
        # The bars' widths come through the CSSOM (no inline style under the CSP).
        assert box.locator(".mpbar span").first.evaluate("e => e.style.width").endswith("%")
        assert not box.locator(".metamore").get_attribute("open")
        # What is coming that concerns a level-32 priest: the undated ruleset, said so.
        coming = box.locator(".metacoming li")
        assert coming.count() >= 1 and "no date" in coming.first.inner_text()
        box.locator(".metamore summary").click()
        assert box.locator(".metamore .metacol").count() >= 2
        assert no_side_scroll(pg) and not errors
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_home_shows_this_week_on_forever(site, pw, engine, width):
    b, pg, errors = open_page(pw, engine, site["full"], width)
    try:
        pg.locator(".tabs button", has_text="Overview").first.click()
        wait_count(pg, "#news .nwitem")
        panel = pg.locator("#news")
        assert "This week on Forever" in panel.locator("h2").inner_text()
        items = panel.locator(".nwitem")
        assert items.count() >= 3
        # What concerns this priest first: the "for you" lines lead.
        flags = [items.nth(i).locator(".pill").count() > 0 for i in range(items.count())]
        assert flags[0] and flags == sorted(flags, reverse=True)
        assert no_side_scroll(pg) and not errors
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_home_shows_what_players_are_saying_apart(site, pw, engine, width):
    b, pg, errors = open_page(pw, engine, site["full"], width)
    try:
        pg.locator(".tabs button", has_text="Overview").first.click()
        wait_count(pg, "#news .nwsaid")
        block = pg.locator("#news .nwcommunity")
        assert "What players are saying" in block.locator("h3").inner_text()
        assert "opinions, not facts" in block.inner_text()
        rows = block.locator(".nwsaid")
        assert rows.count() == 2
        # The priest's own class first, each title a link to its thread.
        assert "Shadow priest" in rows.nth(0).inner_text() and rows.nth(0).locator(".pill").count() == 1
        hrefs = [rows.nth(i).locator("a.mtitle").get_attribute("href") for i in range(2)]
        assert hrefs[0].startswith("https://www.reddit.com/r/wowforever/") and hrefs[1].startswith("https://us.forums.blizzard.com/en/wow/t/")
        assert "299 replies" in rows.nth(1).inner_text()
        assert no_side_scroll(pg) and not errors
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
def test_a_level_60_gets_the_raid_prep_first(site, pw, engine, width):
    LEVEL["v"] = 60
    app = ui.App({"VIEW": "full", "BIND": "127.0.0.1"}, store=PoisonStore())
    app.port = 0
    live = ui.serve(app)
    threading.Thread(target=live.serve_forever, daemon=True).start()
    b = None
    try:
        b, pg, errors = open_page(pw, engine, f"http://127.0.0.1:{live.server_address[1]}/?char={site['char']}", width)
        wait_count(pg, "#meta .metagaps .move")
        first = pg.locator("#meta .metagaps .move").first
        assert "k-prep" in first.get_attribute("class")
        text = first.inner_text()
        # What (the attunement), where (its first quest, linked), the next step.
        assert "Before Barrow Deeps" in text and "Onyxia attunement" in text
        assert "Starts with Dragonkin Menace in Burning Steppes" in text
        assert first.locator("a[href*='quest=4182']").count() == 1
        assert "9 Dec" in pg.locator("#meta .metacoming").inner_text()
        assert no_side_scroll(pg) and not errors
    finally:
        LEVEL["v"] = 32
        if b:
            b.close()
        live.shutdown()
        live.server_close()
