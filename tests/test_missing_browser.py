"""The "Missing:" lines on the full view, in a real browser: one line at the
top of each section a missing source empties or thins, with the fix and the
addon's link; the section's own empty note gives way to it; it comes back
after a section redraws; none when the data is there.

The live full view on the poisoned store (tests/test_public_view.py): its
rows are all Polestar Recorder records and its setup report names no addon,
so wowstats.missing finds Polestar, Syndicator, Questie/Altoholic and
Auctionator missing, end to end. Same browsers as tests/test_browser.py
(the `browser` group)."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.conftest import Running  # noqa: E402
from tests.test_browser import Catalog, wait_count  # noqa: E402
from tests.test_public_view import CHAR, PoisonStore  # noqa: E402
from wowstats import itemcard, ui  # noqa: E402
from wowstats.plan import facts  # noqa: E402


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("missing")
    mp = pytest.MonkeyPatch()
    mp.setattr(ui, "Store", lambda *a, **k: PoisonStore())
    mp.setattr(facts, "store_for", lambda env, like=None: like)
    mp.setattr(itemcard, "shared", lambda: Catalog())
    mp.setenv("WOW_ITEMS", "off")
    app = ui.App({"VL_URL": "http://vl.invalid", "VIEW": "full", "BIND": "127.0.0.1", "MODEL3D": "off",
                  "MODEL3D_CACHE_DIR": str(tmp / "cache")})
    r = Running(app)
    yield r, app
    r.close()
    mp.undo()


@pytest.fixture(scope="module")
def pw(live):
    with sync_api.sync_playwright() as p:
        yield p


def open_char(pw, engine, url, width):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # browsers not installed
        pytest.skip(f"no {engine}: {e}")
    pg = b.new_page(viewport={"width": width, "height": 1000})
    pg.goto(f"{url}/?char={CHAR}&tab=overview")
    wait_count(pg, "#levels p.missing", 1)
    return b, pg


def lines(pg) -> dict[str, list[str]]:
    return pg.evaluate("""() => {
      const out = {};
      for (const p of document.querySelectorAll('p.missing')) {
        const host = p.closest('[data-missing-host]');
        (out[host.id || host.className] ||= []).push(p.textContent);
      }
      return out;
    }""")


def test_the_server_says_what_is_missing(live):
    r, _ = live
    import json

    st, _, body = r.get(f"/api/char?name={CHAR}")
    assert st == 200
    got = {m["id"]: m["line"] for m in json.loads(body)["missing"]}
    assert got == {
        "levels": "Missing: level times, dungeon runs and gear advice. Add Polestar.",
        "bags": "Missing: your gear and bags. Add Syndicator.",
        "quests": "Missing: quests you did before installing. Add Questie or Altoholic.",
        "prices": "Missing: auction prices. Add Auctionator.",
    }


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1920, 390])
def test_one_line_per_section_with_the_fix(live, pw, engine, width):
    r, _ = live
    b, pg = open_char(pw, engine, r.base, width)
    try:
        wait_count(pg, "#prices p.missing", 1)
        wait_count(pg, "#advice p.missing", 1)  # inside the gear panel, drawn after it
        # The advice redraws #advice when its answer lands, whenever that is:
        # the line is back before anything else runs (see below), so any
        # moment from here reads the same lines.
        got = lines(pg)
        assert got["levels"] == ["Missing: level times, dungeon runs and gear advice. Add Polestar."]
        assert got["dungeons"] == got["levels"]  # the first kind that names the section, once
        assert got["gear"] == ["Missing: your gear and bags. Add Syndicator."]
        assert got["advice"] == got["levels"]  # the advice, inside the gear panel, is its own section
        assert got["prices"] == ["Missing: auction prices. Add Auctionator."]
        assert all(len(v) == 1 for v in got.values()), got
        assert "zones" not in got and "deaths" not in got  # the Recorder's data is there
        # The addon's name is its link, opened like every outside link.
        a = pg.locator('#gear p.missing[data-kind="bags"] a')
        assert a.inner_text() == "Syndicator"
        assert a.get_attribute("href") == "https://www.curseforge.com/wow/addons/syndicator"
        assert a.get_attribute("rel") == "noopener noreferrer" and a.get_attribute("target") == "_blank"
        # An emptied section: the line stands in for its own empty note.
        assert pg.locator("#gear.has-missing p.empty:visible").count() == 0
        # A section that redraws gets its line back, before the next task
        # runs: a frame later, a read in between found none (0.7.0 release
        # gate, #advice redrawn by its late answer).
        back = pg.evaluate("""() => {
          document.querySelector('#advice').replaceChildren(document.createElement('h3'));
          return new Promise((ok) => setTimeout(() => ok(document.querySelectorAll('#advice p.missing').length)));
        }""")
        assert back == 1
        pg.evaluate("() => document.querySelector('#gear').replaceChildren()")
        wait_count(pg, '#gear p.missing[data-kind="bags"]', 1)
        # Nothing cut or overflowing on a phone.
        over = pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert over <= 0, over
        pg.locator("#levels p.missing").scroll_into_view_if_needed()
    finally:
        b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_no_line_when_the_data_is_there(live, pw, engine, monkeypatch):
    r, app = live
    monkeypatch.setattr(ui.App, "missing", lambda self, name: [])
    app._cache.clear()
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:
        pytest.skip(f"no {engine}: {e}")
    try:
        pg = b.new_page(viewport={"width": 1920, "height": 1000})
        pg.goto(f"{r.base}/?char={CHAR}&tab=overview")
        wait_count(pg, "#levels .body > *")  # and settled: nothing more to draw
        assert pg.locator("p.missing").count() == 0
        assert pg.locator(".has-missing").count() == 0
    finally:
        b.close()
