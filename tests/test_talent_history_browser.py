"""The talent changes on the character page in a real browser (Chromium and
WebKit, phone and desktop, light and dark): the live full view shows
"Talent changes over the last 3 weeks" under the talent frame, each day
with the points per tree that moved and the talents gained or dropped;
each missing source says one line; /zogzog/'s bundle has no such block.

A Frost mage (the talent test tables, tests/test_talents.py) over the
poison store, its Battle.net readings faked at Store.talent_readings
(needs the `browser` group). TALENT_SHOTS=<dir> saves a screenshot per
engine and theme."""

import io
import os
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser import Catalog, wait_count  # noqa: E402
from tests.test_public_view import CHAR, PoisonStore  # noqa: E402
from tests.test_talents import MAGE_BNET, TABLES  # noqa: E402
from wowstats import itemcard, publish, static, talents, ui, views, wowsims  # noqa: E402
from wowstats.plan import facts  # noqa: E402

DAY = 86400
MODE = {"v": "history"}


def _t(days_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * DAY))


def _r(days_ago, trees, ranks):
    return {"t": _t(days_ago), "trees": [{"tree": t, "points": p, "order": i} for i, (t, p) in enumerate(trees)],
            "ranks": {str(s): {"spell": s, "name": n, "rank": k} for s, n, k in ranks}}


# Fire first, a respec into Frost, then two levels.
READINGS = [
    _r(18, [("Arcane", 2), ("Fire", 3), ("Frost", 0)], [(11210, "Arcane Subtlety", 2), (11119, "Ignite", 3)]),
    _r(17, [("Arcane", 2), ("Fire", 3), ("Frost", 0)], [(11210, "Arcane Subtlety", 2), (11119, "Ignite", 3)]),
    _r(12, [("Arcane", 2), ("Fire", 0), ("Frost", 5)], [(11210, "Arcane Subtlety", 2), (11070, None, 5)]),
    _r(9, [("Arcane", 2), ("Fire", 0), ("Frost", 6)], [(11210, "Arcane Subtlety", 2), (11070, None, 5),
                                                      (12472, None, 1)]),
    _r(4, [("Arcane", 2), ("Fire", 0), ("Frost", 8)], [(11210, "Arcane Subtlety", 2), (11070, None, 5),
                                                      (12472, None, 1), (16757, None, 2)]),
]


class MageStore(PoisonStore):
    def character_talents(self, name):
        return MAGE_BNET

    def talent_readings(self, name, days=21, now=None):
        if MODE["v"] == "down":
            raise OSError("vm-wow down")
        return {"history": READINGS, "same": READINGS[3:4] * 2, "none": []}[MODE["v"]]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("thist")
    mp = pytest.MonkeyPatch()
    mp.setattr(ui, "Store", lambda *a, **k: MageStore())
    mp.setattr(itemcard, "shared", lambda: Catalog())
    mp.setattr(facts, "store_for", lambda env, like=None: like)
    mp.setenv("WOW_ITEMS", "off")
    c = talents.Catalog("http://wago.invalid", "wowf", None)
    c.data, c.checked = talents.catalog_from(lambda name: iter(TABLES.get(name, []))), time.time()
    mp.setattr(talents, "shared", lambda: c)  # conftest resets _shared before every test
    char = views.Views.char

    def mage(self, name):
        d = char(self, name)
        tal, spec = talents.from_battlenet(talents.shared().get(), "MAGE", MAGE_BNET)
        return {**d, "talents": tal, "spec": spec,
                "header": {**d["header"], "class": "MAGE", "race": "Gnome", "faction": "Alliance", "level": 30}}

    mp.setattr(views.Views, "char", mage)
    # WoWSims Exporter's saves (mode "addon"): months of changes, and one talent it can't save.
    mp.setattr(views.Views, "addon_talents",
               lambda self, char, cls: [{**r, "t": _t(d)} for d, r in zip((80, 61, 40, 9, 4), READINGS, strict=True)]
               if MODE["v"] == "addon" else [])
    mp.setattr(wowsims, "missing_from", lambda cat, cls, bnet: ["Arctic Reach"] if MODE["v"] == "addon" else [])
    env = {"VL_URL": "http://vl.invalid", "MODEL3D": "off", "PUBLISH_DIR": str(tmp / "site"),
           "ZOGZOG_KEY_FILE": str(tmp / "zogzog.key"), "MODEL3D_CACHE_DIR": str(tmp / "cache")}
    with redirect_stdout(io.StringIO()):
        publish.publish(env)
    key = Path(env["ZOGZOG_KEY_FILE"]).read_text().strip()  # never printed
    s = static.Site({"SITE_DIR": str(tmp / "site"), "ZOGZOG_PATH": "/zogzog/", "BIND": "127.0.0.1", "PORT": "0"})
    srv = static.Server(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    app = ui.App({"VIEW": "full", "BIND": "127.0.0.1"}, store=MageStore())
    app.port = 0
    live = ui.serve(app)
    threading.Thread(target=live.serve_forever, daemon=True).start()
    yield {"zogzog": f"http://wow.localhost:{srv.server_address[1]}/zogzog/?char={CHAR}#{key}",
           "full": f"http://127.0.0.1:{live.server_address[1]}/?char={CHAR}", "bundle": tmp / "site"}
    srv.shutdown()
    live.shutdown()
    live.server_close()
    mp.undo()


@pytest.fixture(scope="module")
def pw(site):
    with sync_api.sync_playwright() as p:
        yield p


def open_page(pw, engine, url, width, scheme="dark"):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # browsers not installed
        pytest.skip(f"no {engine}: {e}")
    pg = b.new_page(viewport={"width": width, "height": 1000}, color_scheme=scheme)
    errors = []
    pg.on("pageerror", lambda e: "ResizeObserver loop" in str(e) or errors.append(str(e)))
    pg.goto(url)
    pg.locator(".tabs button", has_text="Character").first.click()
    wait_count(pg, "#talents .tframe")
    return b, pg, errors


def no_side_scroll(pg):
    return pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_the_full_view_shows_three_weeks_of_changes(site, pw, engine, width, scheme):
    MODE["v"] = "history"
    b, pg, errors = open_page(pw, engine, site["full"], width, scheme)
    try:
        wait_count(pg, "#talents .tchanges li")
        box = pg.locator("#talents .thistory")
        assert box.locator("h3").inner_text() == "Talent changes over the last 3 weeks"
        rows = box.locator(".tchanges li")
        assert rows.count() == 3  # two levels and the respec; the day without a change isn't one
        newest, _, respec = (rows.nth(i).inner_text() for i in range(3))
        assert "Frost 6 → 8" in newest and "Arctic Reach" in newest and "Gained" in newest
        assert "Fire 3 → 0" in respec and "Frost 0 → 5" in respec
        assert "Dropped" in respec and "Ignite" in respec and "Improved Frostbolt" in respec
        # Each talent links to its spell; the block sits between the frame and Legacy.
        assert box.locator("a").count() >= 4
        order = pg.evaluate("[...document.querySelectorAll('#talents > *')].map(e => e.className)")
        assert order.index("thistory") > order.index("tframe") and order.index("thistory") < order.index("legacy")
        assert no_side_scroll(pg) and not errors
        shots = os.environ.get("TALENT_SHOTS")
        if shots and width == 1280:
            box.scroll_into_view_if_needed()
            pg.locator("#talents").screenshot(path=str(Path(shots) / f"talent-history-{engine}-{scheme}.png"))
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_with_the_addon_every_change_and_what_it_cant_save(site, pw, engine, scheme):
    MODE["v"] = "addon"
    b, pg, errors = open_page(pw, engine, site["full"], 1280, scheme)
    try:
        wait_count(pg, "#talents .tchanges li")
        box = pg.locator("#talents .thistory")
        assert box.locator("h3").inner_text() == "Talent changes"
        assert box.locator(".tchanges li").count() == 3
        text = box.inner_text()
        assert "Not saved by WoWSims Exporter: Arctic Reach." in text and "Every change, not only" not in text
        assert no_side_scroll(pg) and not errors
        shots = os.environ.get("TALENT_SHOTS")
        if shots:
            box.scroll_into_view_if_needed()
            pg.locator("#talents").screenshot(path=str(Path(shots) / f"talent-addon-{engine}-{scheme}.png"))
    finally:
        MODE["v"] = "history"
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_without_the_addon_the_one_step_to_every_change(site, pw, engine):
    MODE["v"] = "history"
    b, pg, errors = open_page(pw, engine, site["full"], 390)
    try:
        wait_count(pg, "#talents .tchanges li")
        hint = pg.locator("#talents .thistory .thint")
        assert hint.count() == 1
        assert hint.inner_text() == ("Every change, not only 3 weeks: add WoWSims Exporter, "
                                     "then tick it in AddOns at character select.")
        assert hint.locator("a").get_attribute("href") == "https://www.curseforge.com/wow/addons/wowsims-exporter"
        assert not errors
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize(("mode", "line"), [
    ("same", "No talent change since"),
    ("none", "once Battle.net has read this character on two days"),
    ("down", "can’t be read right now"),
])
def test_every_missing_source_says_one_line(site, pw, engine, mode, line):
    MODE["v"] = mode
    b, pg, errors = open_page(pw, engine, site["full"], 390)
    try:
        wait_count(pg, "#talents .thistory p.empty")
        box = pg.locator("#talents .thistory")
        assert line in box.locator("p.empty").inner_text() and box.locator(".tchanges").count() == 0
        assert not errors
    finally:
        MODE["v"] = "history"
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_zogzog_has_no_talent_history(site, pw, engine):
    """Blizzard's 30 days: the static bundles never carry it."""
    b, pg, errors = open_page(pw, engine, site["zogzog"], 1280)
    try:
        assert pg.locator("#talents .thistory").count() == 0 and not errors
    finally:
        b.close()
    assert not list(site["bundle"].rglob("*talents/*")), "a bundle holds a talent answer"
