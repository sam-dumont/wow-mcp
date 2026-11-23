"""The static sites in a real browser (Chromium, Playwright): what Plausible
gets, the item icons and the tooltip on a phone.

A bundle published from the fixture data, served by `wowstats.static` (the
pod's own server) as http://wow.localhost: a secure context like the real
HTTPS site (/zogzog/ needs WebCrypto), and not the bare `localhost`
Plausible's script refuses to count. Plausible's script is the server's
own `script.manual.js` (tests/fixtures, fetched from Plausible CE v3.2.1);
its event endpoint is intercepted, never reached.

Needs the `browser` group: `uv run --group browser playwright install
chromium` once, then `uv run --group browser pytest tests/test_browser.py`.
Skipped without it. WOW_SHOTS=<dir> also saves the screenshots."""

import io
import json
import os
import re
import threading
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_public_view import PoisonStore  # noqa: E402
from wowstats import armory, itemcard, publish, static, stores, ui  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
JPEG = (FIXTURES / "icon.jpg").read_bytes() if (FIXTURES / "icon.jpg").is_file() else None


def png(w=6, h=12) -> bytes:
    """A real, decodable PNG (the browser must draw it: naturalWidth > 0)."""
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# The paperdoll's still (wowstats.armory: our renderer's picture of the 3D
# model) is framed on its own opaque box: a 1600x1200 transparent canvas
# with the character a 308x456 box in its lower middle, as Battle.net's
# renders were and as a still can be.
BODY = (659, 519, 967, 975)


def still_webp(w=1600, hgt=1200, box=BODY) -> bytes:
    """That still as a WebP with alpha, drawn by Chromium (the project has no
    image library): synthetic, no game file."""
    import base64

    x0, y0, x1, y1 = box
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001 - browsers not installed
            pytest.skip(f"no Chromium: {e}")
        try:
            url = b.new_page().evaluate(
                """([w, h, x0, y0, x1, y1]) => { const c = document.createElement('canvas');
                   c.width = w; c.height = h; const g = c.getContext('2d');
                   g.fillStyle = 'rgb(128,64,32)'; g.fillRect(x0, y0, x1 - x0, y1 - y0);
                   return c.toDataURL('image/webp', 1); }""", [w, hgt, x0, y0, x1, y1])
        finally:
            b.close()
    body = base64.b64decode(url.split(",", 1)[1])
    assert armory.kind(body) == "webp", url[:40]
    return body


NO_ICON = 5820  # the client tables have no icon for it: the game's question mark


class Catalog:
    """Every item gets a full card and a real (tiny) JPEG icon, but one."""

    def many(self, ids):
        out = {str(i): {"n": f"Item {i}", "q": 3, "i": f"inv_test_{i % 7}", "b": "Binds when equipped",
                        "s": "Chest", "t": "Cloth", "ar": 41, "w": ["+5 Stamina", "+4 Intellect"], "rl": 20,
                        "il": 25, "g": ["Equip: Restores 2 mana per 5 sec."], "set": "Test Set"} for i in ids}
        if str(NO_ICON) in out:
            del out[str(NO_ICON)]["i"]
        return out

    def icon(self, name, size):
        return JPEG

    def enchant(self, eid):
        return {66: "Stamina +3"}.get(eid)


HELP: dict = {}


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    if JPEG is None:
        pytest.skip("tests/fixtures/icon.jpg missing")
    tmp = tmp_path_factory.mktemp("browser")
    mp = pytest.MonkeyPatch()

    def store(vl, archive="", vm="", *a, profile_vm_url=None, **k):  # the real Store's arguments
        s = PoisonStore()
        s.vm_url = vm
        s.profile_vm_url = (vm if profile_vm_url is None else profile_vm_url).rstrip("/")
        s._http_profile = True  # the real reader, over the faked HTTP
        return s

    # vm-wow still holds an older exporter's Blizzard render address: never
    # read, never downloaded (owner, 2026-11-21: the picture is ours only).
    def http_get(url, params=None, *a, **k):
        q = (params or {}).get("query", "")
        assert "wow_character_render" not in q, q
        return json.dumps({"data": {"result": []}}).encode()

    mp.setattr(stores, "http_get", http_get)
    # The still as the publisher puts it (tests/test_pictures.py runs the
    # real renderer path): a synthetic WebP in both bundles.
    still = still_webp()

    def put_still(b, full_app, pics, name):
        b.put(armory.rel(name), still)
        return {"ext": "webp"}

    mp.setattr(publish, "put_still", put_still)
    mp.setattr(ui, "Store", store)
    mp.setattr(itemcard, "shared", lambda: Catalog())
    mp.setenv("WOW_ITEMS", "off")
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "off",
           "PUBLISH_DIR": str(tmp / "site"), "ZOGZOG_KEY_FILE": str(tmp / "zogzog.key"),
           "PLAUSIBLE_DOMAIN": "wow.test", "MODEL3D_CACHE_DIR": str(tmp / "cache")}
    mp.setenv("MODEL3D_CACHE_DIR", str(tmp / "cache"))
    HELP["key"] = publish.new_key(str(tmp / "help.key"))  # the MCP help page's own key
    env |= {"HELP_KEY_FILE": str(tmp / "help.key"), "HELP_MCP_URL": "https://wow.example/mcp"}
    out = io.StringIO()
    with redirect_stdout(out):
        publish.publish(env)
    key = Path(env["ZOGZOG_KEY_FILE"]).read_text().strip()  # never printed
    site = static.Site({"SITE_DIR": str(tmp / "site"), "ZOGZOG_PATH": "/zogzog/", "BIND": "127.0.0.1", "PORT": "0"})
    srv = static.Server(("127.0.0.1", 0), static.make_handler(site))  # the pod's own server
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://wow.localhost:{srv.server_address[1]}", key
    srv.shutdown()
    mp.undo()


@pytest.fixture(scope="module")
def pw(served):
    with sync_api.sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(pw):
    try:
        b = pw.chromium.launch()
    except Exception as e:  # browsers not installed
        pytest.skip(f"no Chromium: {e}")
    yield b
    b.close()


errors: list[str] = []


def page_with_plausible(browser, **ctx):
    """A page whose /js/script.js is Plausible's manual script and whose
    /api/event answers 202; every request and every event recorded."""
    c = browser.new_context(**ctx)
    # Playwright sets navigator.webdriver, which the script respects.
    c.add_init_script("window.__plausible = true")
    seen, events = [], []
    script = (FIXTURES / "plausible-script.manual.js").read_text()
    c.route(re.compile(r".*/js/script\.js$"), lambda r: r.fulfill(status=200, body=script,
                                                                  content_type="application/javascript"))

    def event(route):
        req = route.request
        events.append({"body": json.loads(req.post_data), "url": req.url, "headers": req.headers})
        route.fulfill(status=202, body="ok")

    c.route(re.compile(r".*/api/event$"), event)
    pg = c.new_page()
    pg.on("request", lambda r: seen.append(r.url))
    # What the page said, for a failure message.
    errors.clear()
    pg.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    return c, pg, seen, events


BUSY = "html[data-busy]"


def settled(pg, timeout=30.0):
    """The page is done (web/app.js data-busy): every answer it asked for
    is in and drawn, its fonts are in, no section jump is on its way. Read
    a page only then: before, its late sections, its fonts and its jumps
    are still moving it (the 0.7.0 release gate, three runs in a row)."""
    pg.locator("html:not([data-busy])").wait_for(state="attached", timeout=timeout * 1000)


def wait_count(pg, selector, n=None, timeout=15.0):
    """Waits until the page shows `n` of `selector` (any, without `n`) and
    is settled, both at once: a count read while the page still draws can
    be gone by the next line. Without evaluating script: the pages' CSP has
    no 'unsafe-eval', which Playwright's wait_for_function needs."""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        k = pg.locator(selector).count()
        if ((n is None and k > 0) or (n is not None and k == n)) and not pg.locator(BUSY).count():
            return k
        pg.wait_for_timeout(100)
    body = pg.locator("body").inner_text()[:300].replace("\n", " | ")
    busy = " (the page still busy)" if pg.locator(BUSY).count() else ""
    raise AssertionError(f"{selector}: {pg.locator(selector).count()}, wanted {n if n is not None else '> 0'}{busy}; "
                         f"page: {body}; errors: {errors[-8:]}")


def to_gear(pg):
    """/zogzog/ opens on the Overview: the gear is under Character."""
    tab = pg.locator(".tabs button", has_text="Character")
    if tab.count():
        tab.first.click()
    pg.locator(".slotitm").first.wait_for(state="visible")


def shot(pg, name):
    d = os.environ.get("WOW_SHOTS")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(d) / f"{name}.png"))


@pytest.mark.parametrize("bundle", ["public", "zogzog"])
def test_plausible_sees_the_path_and_never_the_key(served, browser, bundle):
    base, key = served
    path = "/" if bundle == "public" else "/zogzog/"
    c, pg, seen, events = page_with_plausible(browser)
    # A query and a fragment like a shared link's: the key, a tab, a name.
    url = f"{base}{path}?char=Hero-Test&tab=gear" + (f"#{key}" if bundle == "zogzog" else "#gear")
    pg.goto(url)
    wait_count(pg, ".slotitm")  # the data opened (on /zogzog/, decrypted)
    pg.wait_for_timeout(500)
    views = [e for e in events if e["body"].get("n") == "pageview"]
    assert len(views) == 1, events
    body = views[0]["body"]
    assert body["u"] == base + path, body
    assert body["d"] == "wow.test"
    assert not body.get("r"), "no referrer"
    assert not body.get("h"), "no hash tracking"
    blob = json.dumps(events)
    assert key not in blob and "Hero-Test" not in blob and "tab=" not in blob
    for e in events:
        assert "referer" not in {k.lower() for k in e["headers"]}, e["headers"]
        assert e["url"] == base + "/api/event"
    # Every request stays on the site: no third party.
    assert all(u.startswith(base + "/") or u.startswith("data:") for u in seen), [
        u for u in seen if not u.startswith(base)]
    c.close()


@pytest.mark.parametrize("bundle", ["public", "zogzog"])
def test_every_slot_shows_its_icon(served, browser, bundle):
    base, key = served
    c, pg, _, _ = page_with_plausible(browser, viewport={"width": 1280, "height": 900})
    pg.goto(f"{base}/{'' if bundle == 'public' else 'zogzog/'}#{key if bundle == 'zogzog' else ''}")
    wait_count(pg, ".slotitm")
    to_gear(pg)
    n = pg.locator(".slotitm").count()
    wait_count(pg, ".slotitm .ifr.has-icon", n)
    # Square, 56 px, never stretched.
    box = pg.locator(".slotitm .ifr img").first.bounding_box()
    assert round(box["width"]) == round(box["height"]) == 56
    shot(pg, f"{bundle}-desktop")
    c.close()


def test_phone_tap_opens_the_tooltip_as_a_bottom_panel(served, browser):
    base, key = served
    c, pg, _, _ = page_with_plausible(browser, viewport={"width": 390, "height": 844}, has_touch=True,
                                      is_mobile=True, device_scale_factor=3)
    pg.goto(f"{base}/zogzog/#{key}")
    wait_count(pg, ".slotitm .ifr.has-icon")
    to_gear(pg)
    first = pg.locator(".slotitm").first
    first.scroll_into_view_if_needed()
    first.tap()
    tip = pg.locator("#wowtip")
    sync_api.expect(tip).to_be_visible()
    sync_api.expect(tip).to_have_class(re.compile(r"\bsheet\b"))
    sync_api.expect(tip.locator(".tt-name")).to_have_text(re.compile(r"\S"))
    for line in ("Binds when equipped", "41 Armor", "+5 Stamina", "Requires Level 20", "Item Level 25",
                 "Equip: Restores 2 mana per 5 sec.", "Test Set"):
        sync_api.expect(tip).to_contain_text(line)
    b = tip.bounding_box()
    assert b["x"] == 0 and round(b["width"]) == 390 and round(b["y"] + b["height"]) == 844, b
    shot(pg, "zogzog-phone-tooltip")
    # The same item again closes it; another item swaps it; outside closes.
    first.tap()
    sync_api.expect(tip).to_be_hidden()
    first.tap()
    # Another item (a list's 36 px chip) swaps it: one tooltip at a time.
    # The Character tab's: the Overview, before it, has chips of its own.
    second = pg.locator("#main .itm:not(.slotitm)").first
    name = second.get_attribute("aria-label")
    second.scroll_into_view_if_needed()
    second.tap()
    sync_api.expect(tip).to_be_visible()
    sync_api.expect(tip.locator(".tt-name")).to_have_text(name.split(", enchanted")[0])
    assert pg.locator("#wowtip").count() == 1
    shot(pg, "zogzog-phone-tooltip-list")
    pg.locator("h1, header").first.tap()
    sync_api.expect(tip).to_be_hidden()
    c.close()


def test_keyboard_opens_and_escape_closes(served, browser):
    base, _ = served
    c, pg, _, _ = page_with_plausible(browser, viewport={"width": 1280, "height": 900})
    pg.goto(f"{base}/")
    wait_count(pg, ".slotitm")
    first = pg.locator(".slotitm").first
    first.focus()
    pg.keyboard.press("Enter")
    tip = pg.locator("#wowtip")
    sync_api.expect(tip).to_be_visible()
    sync_api.expect(tip).not_to_have_class(re.compile(r"\bsheet\b"))
    pg.keyboard.press("Escape")
    sync_api.expect(tip).to_be_hidden()
    sync_api.expect(first).to_be_focused()
    c.close()


@pytest.mark.parametrize("bundle", ["public", "zogzog"])
def test_the_paperdoll_shows_our_still(served, browser, bundle):
    """The publisher's real output, in Chromium: the middle of the paperdoll
    is our still of the 3D model, drawn (not the silhouette), in both
    bundles; on /zogzog/ decrypted from the sealed copy. Nothing is asked
    of Blizzard's render host."""
    base, key = served
    c, pg, seen, _ = page_with_plausible(browser, viewport={"width": 1280, "height": 900})
    pg.goto(f"{base}/{'' if bundle == 'public' else 'zogzog/'}#{key if bundle == 'zogzog' else ''}")
    wait_count(pg, ".slotitm")
    to_gear(pg)
    wait_count(pg, ".stage .still.has-render img.render", 1)
    img = pg.locator(".stage .still.has-render img.render")
    assert img.evaluate("i => i.naturalWidth") > 0
    assert img.get_attribute("alt").endswith("as the 3D view shows them")
    if bundle == "public":
        assert any(u.endswith("/data/render/Hero-Test.webp") for u in seen), seen
    assert not [u for u in seen if "worldofwarcraft" in u or u.endswith(".png") and "/render/" in u], seen
    # One item level, and the line under it says what it is (2026-11-19: the
    # armory's "23" stood over "27.6 average of the items worn").
    ilvl = pg.locator(".dollfoot .ilvl")
    assert re.fullmatch(r"\d+\.\d", ilvl.locator("b").inner_text()), ilvl.inner_text()
    assert ilvl.locator("span").inner_text() == "item level"
    assert ilvl.locator("small").inner_text() == "average of the items you wear"
    shot(pg, f"{bundle}-render")
    c.close()


FRAMES = """(els) => els.map((f) => {
  const img = f.querySelector('img');
  return {name: (f.closest('.itm') || f).getAttribute('aria-label'), img: !!img,
          w: img ? img.naturalWidth : 0, done: img ? img.complete : false, src: img ? img.src : ''};
})"""


def frames_drawn(pg, where):
    """Every item frame shows a decoded image: no empty box (0.2.3: Forever's
    new items in the upgrade list)."""
    pg.evaluate("() => document.querySelectorAll('.ifr img').forEach((i) => { i.loading = 'eager'; })")
    n = wait_count(pg, ".itm .ifr")
    wait_count(pg, ".itm .ifr.has-icon", n)
    got = pg.locator(".itm .ifr").evaluate_all(FRAMES)
    bad = [f for f in got if not f["img"] or not f["done"] or f["w"] == 0]
    assert not bad, f"{where}: {bad}"
    text = pg.locator("body").inner_text()
    assert not re.search(r"\b(quest|item) \d+\b", text), f"{where}: an id where a name belongs"
    assert "undefined" not in text and "null" not in text, f"{where}: a missing value printed"
    return got


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("bundle", ["public", "zogzog"])
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_no_item_shows_without_its_image(served, pw, engine, bundle, width):
    """Chromium and WebKit (Safari on an iPhone is a common reader), phone and
    desktop: the paperdoll, the upgrade list, the loot, the journal."""
    base, key = served
    if True:
        try:
            b = getattr(pw, engine).launch()
        except Exception as e:  # noqa: BLE001 - that browser isn't installed
            pytest.skip(f"no {engine}: {e}")
        phone = width < 600
        c = b.new_context(viewport={"width": width, "height": 844 if phone else 900}, is_mobile=phone,
                          has_touch=phone, device_scale_factor=3 if phone else 1)
        pg = c.new_page()
        errors.clear()
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        pg.goto(f"{base}/{'' if bundle == 'public' else 'zogzog/'}#{key if bundle == 'zogzog' else ''}")
        wait_count(pg, ".slotitm")
        to_gear(pg)
        pg.evaluate("() => document.querySelectorAll('details').forEach((d) => { d.open = true; })")
        got = frames_drawn(pg, f"{engine} {bundle} {width} character")
        if bundle == "public":
            # The doll's label may carry its BiS standing after the name (owner, 2026-11-23).
            q = [f for f in got if (f["name"] or "").split(", ")[0] == "Faerie Mantle"]
            assert q and all(f["src"].endswith("/inv_misc_questionmark.jpg") for f in q), q
        if bundle == "zogzog":
            wait_count(pg, "#advice .cand .itm")
            pg.locator(".tabs button", has_text="Journal").first.click()
            wait_count(pg, "#journal .jentry")
            pg.evaluate("() => document.querySelectorAll('#journal details').forEach((d) => { d.open = true; })")
            wait_count(pg, "#journal .qlist .qn")
            frames_drawn(pg, f"{engine} {bundle} {width} journal")
        assert pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
        c.close()
        b.close()


WIDE = """() => [...document.querySelectorAll('#help p, #help li, #help dd, #help pre, #help h3')]
  .filter((e) => e.offsetParent && e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent.slice(0, 60))"""

# The words a reader sees before opening anything: closed folds show their
# summary only, hidden panels nothing, a <select> its picked option.
SEEN_WORDS = r"""() => {
  const sel = [...document.querySelectorAll('select')];
  const keep = sel.map((s) => s.options[s.selectedIndex]?.text || '');
  sel.forEach((s) => { s.style.display = 'none'; });
  const text = document.body.innerText;
  sel.forEach((s) => { s.style.display = ''; });
  return (text + ' ' + keep.join(' ')).split(/\s+/).filter((w) => /\w/.test(w)).length;
}"""


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_help_page_opens_with_its_own_key_only(served, pw, engine):
    """/help/#<key> on a phone, as a friend opens it from a chat: the
    sealed help opens with its key only; the first screen holds what you
    get and the 3 steps; the page fits 390 px and stays short; the AI app
    picker, the folds and the in-page links work without touching the key
    in the fragment; picks survive a reload."""
    base, zz_key = served
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    c = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=engine == "chromium", has_touch=True,
                      device_scale_factor=3)
    pg = c.new_page()
    errors.clear()
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    url = f"{base}/help/#{HELP['key']}"
    pg.goto(url)
    wait_count(pg, ".ask", None)
    shot(pg, f"help-{engine}-390-first")
    # The first screen answers "how do I use this": the promise, then 3 steps.
    first = pg.evaluate("""() => ['h1', '#q-addons h3', '#q-command h3', '#q-play h3', '#q-play p']
      .map((s) => { const r = document.querySelector(s).getBoundingClientRect(); return [s, r.top, r.bottom]; })""")
    assert all(top >= 0 and bottom <= 844 for _, top, bottom in first), first
    assert pg.locator("#q-addons .hbtn").is_visible() and pg.locator("#q-command .hbtn").is_visible()
    assert pg.locator("#q-addons .hchip:visible").count() == 1  # Polestar; the install command adds the Recorder
    words = pg.evaluate(SEEN_WORDS)
    assert words <= 350, words
    over = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    assert pg.evaluate(over) <= 0
    assert pg.evaluate(WIDE) == []  # no line wider than its box (a long URL or path)
    # Your AI: nothing until you pick your app, then its steps and its snippet.
    assert pg.locator(".hclient:visible").count() == 0
    pg.locator("select[data-sel=client]").select_option(label="Claude Code")
    text = pg.locator("body").inner_text()
    assert "claude mcp add --transport http wow https://wow.example/mcp" in text
    assert pg.locator("button[data-copy]:visible").count() >= 5  # the address, the snippet, 3 asks
    assert pg.evaluate(over) <= 0
    assert pg.evaluate(WIDE) == []
    # The header's links jump without touching the key in the fragment.
    pg.locator("#hnav a[data-jump='trouble']").click()
    pg.locator("#tr-no-data summary").click()
    assert pg.locator("#tr-no-data p").first.is_visible()
    assert pg.evaluate("() => location.hash.slice(1)") == HELP["key"]
    pg.locator("#q-command [data-sw=os][data-val=windows]").click()
    pg.reload()
    wait_count(pg, ".ask", None)
    assert pg.locator("select[data-sel=client]").evaluate("(s) => s.options[s.selectedIndex].text") == "Claude Code"
    assert pg.locator("#q-command [data-sw=os][aria-selected=true]").inner_text().startswith("Windows")
    assert not errors, errors
    for wrong in (zz_key, ""):
        pg.goto("about:blank")
        pg.goto(f"{base}/help/#{wrong}")
        wait_count(pg, "#help p", 1)
        assert pg.locator("#help").inner_text().strip() == "Nothing here."
        assert not pg.locator("#hnav").is_visible()
    c.close()
    b.close()


# A badge is a word in a line of text. app.css once styled `.badge` as the
# level ring (82 px, dark): every badge on the help page became a dark
# square over the name next to it. The help page's own tags and chips stay
# about a line tall.
BADGES = """() => [...document.querySelectorAll('.htag, .hchip, .badge')].filter((e) => e.getClientRects().length)
  .map((e) => { const r = e.getBoundingClientRect(), lh = parseFloat(getComputedStyle(e.parentElement).fontSize) * 2.2;
                return {text: e.textContent.trim(), h: Math.round(r.height), max: Math.round(lh)}; })"""


@pytest.mark.parametrize("width", [390, 1920])
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_help_badges_are_one_line_tall(served, pw, engine, width):
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    c = b.new_context(viewport={"width": width, "height": 900})
    pg = c.new_page()
    pg.goto(f"{served[0]}/help/#{HELP['key']}")
    wait_count(pg, ".ask", None)
    pg.evaluate("() => document.querySelectorAll('#help details').forEach((d) => { d.open = true; })")
    pg.locator("select[data-sel=client]").select_option(label="Copilot CLI (not checked)")
    boxes = pg.evaluate(BADGES)
    tall = [x for x in boxes if x["h"] > x["max"]]
    assert not tall, tall
    assert {"Polestar", "not checked end to end"} <= {x["text"] for x in boxes}
    assert pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    assert pg.evaluate(WIDE) == []
    c.close()
    b.close()


@pytest.mark.parametrize("case", ["pending", "missing"])
def test_without_our_still_the_paperdoll_shows_no_image(served, browser, case):
    """Our still not drawn yet (the renderer down or busy), or gone: the
    silhouette and the way to the 3D view, never another picture."""
    base, _ = served
    c, pg, seen, _ = page_with_plausible(browser, viewport={"width": 1280, "height": 900})
    if case == "pending":
        def pending(route):
            d = json.loads(route.fetch().text())
            d["gear"]["render"] = {"ext": None, "pending": True}
            route.fulfill(status=200, body=json.dumps(d), content_type="application/json")
        c.route(re.compile(r".*/data/char/[^/]+\.json$"), pending)
    else:
        c.route(re.compile(r".*/data/render/.*"), lambda r: r.fulfill(status=404, body="not found"))
    pg.goto(f"{base}/")
    wait_count(pg, ".slotitm")
    to_gear(pg)
    wait_count(pg, ".stage .still .figure", 1)
    if case == "pending":
        wait_count(pg, ".stage .still.pending .stillnote", 1)
        assert "View it in 3D" in pg.locator(".stage .stillnote").inner_text()
        assert not [u for u in seen if "/data/render/" in u], seen
    else:
        pg.wait_for_timeout(500)
    assert pg.locator(".stage img").count() == 0
    c.close()


@pytest.mark.parametrize("width", [390, 1440, 1920])
@pytest.mark.parametrize("bundle", ["public", "zogzog"])
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_render_fills_the_stage_like_the_3d_model(served, pw, engine, bundle, width):
    """0.3.0 drew a render's whole 1600x1200 canvas in the portrait
    stage: the character ~17% of its height, at the bottom. Framed
    on its own box it stands where the 3D model does (about half the stage,
    centred, feet above the toggle), never past one image pixel per CSS px."""
    base, key = served
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    phone = width < 600
    c = b.new_context(viewport={"width": width, "height": 844 if phone else 1000}, is_mobile=phone and engine == "chromium",
                      has_touch=phone)
    pg = c.new_page()
    pg.goto(f"{base}/{'' if bundle == 'public' else 'zogzog/'}#{key if bundle == 'zogzog' else ''}")
    wait_count(pg, ".slotitm")
    to_gear(pg)
    wait_count(pg, ".stage .still.framed img.render", 1)
    st = pg.locator(".doll > .stage").bounding_box()
    im = pg.locator(".stage .still.framed img.render").bounding_box()
    x0, y0, x1, y1 = BODY
    body_top, body_h = im["y"] + im["height"] * y0 / 1200, im["height"] * (y1 - y0) / 1200
    body_cx = im["x"] + im["width"] * (x0 + x1) / 2 / 1600
    where = f"{engine} {bundle} {width}: stage {st}, image {im}"
    assert body_h >= 0.45 * st["height"], where  # the 3D model is ~52% of it
    assert body_h <= y1 - y0 + 1, where  # never upscaled past its own pixels
    assert st["y"] < body_top and body_top + body_h < st["y"] + st["height"] - 30, where  # feet above the toggle
    assert abs(body_cx - (st["x"] + st["width"] / 2)) < 0.05 * st["width"], where
    shot(pg, f"render-{engine}-{bundle}-{width}")
    c.close()
    b.close()


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_overview_names_its_moves_and_leads_to_them(served, pw, engine, width):
    """/zogzog/'s Overview: the verdict, four numbers, the moves by name
    with Wowhead links that send no referrer, the time per level, on one
    phone-wide column without sideways scrolling; an upgrade's "See the
    upgrade" opens the Character tab on that slot's card."""
    base, key = served
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    phone = width < 600
    c = b.new_context(viewport={"width": width, "height": 844 if phone else 900}, is_mobile=phone, has_touch=phone)
    pg = c.new_page()
    errors.clear()
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.goto(f"{base}/zogzog/#{key}")
    wait_count(pg, "#home .ovtiles > div", 4)
    assert pg.locator("#home .verdict").inner_text().strip()
    wait_count(pg, "#home .move")
    assert pg.locator("#home .moves:not(.more) > .move").count() <= 3
    links = pg.locator("#home a.wh")
    assert links.count() > 0
    for i in range(links.count()):
        a = links.nth(i)
        assert a.get_attribute("href").startswith("https://www.wowhead.com/forever/")
        assert a.get_attribute("target") == "_blank" and a.get_attribute("referrerpolicy") == "no-referrer"
        assert set(a.get_attribute("rel").split()) == {"noopener", "noreferrer"}
    text = pg.locator("#home").inner_text()
    assert "undefined" not in text and "null" not in text and "NaN" not in text
    assert pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    up = pg.locator("#home .move.k-upgrade .mgo")
    if up.count():
        target = up.first.get_attribute("href")
        up.first.click()
        card = pg.locator(target)
        card.wait_for(state="visible")
        assert "lit" in card.get_attribute("class")
        assert pg.locator("#tab-overview").get_attribute("aria-selected") == "true"
    # WebKit reports the climb's ResizeObserver redrawing as the Character tab
    # opens: harmless, and not the Overview's.
    assert not [e for e in errors if e.startswith("pageerror") and "ResizeObserver loop" not in e], errors
    c.close()
    b.close()


def test_an_item_no_source_names_shows_its_slot(served, pw):
    """An item no table names (211500 before the Era names, 0.4.0) is "Head
    item, see on Wowhead", in its chip and its tooltip with the link: never
    "Unknown item (211500)"."""
    base, _ = served
    b = pw.chromium.launch()
    c = b.new_context(viewport={"width": 1280, "height": 900})
    pg = c.new_page()
    pg.goto(f"{base}/")
    wait_count(pg, ".slotitm")
    pg.evaluate("""() => {
      const chip = window.wowItem({ item_id: 211500, slot_name: 'Head' });
      chip.id = 'nameless';
      document.body.append(chip);
    }""")
    pg.wait_for_function("() => document.querySelector('#nameless .iname').textContent.length > 0")
    assert pg.locator("#nameless .iname").inner_text() == "Head item, see on Wowhead"
    pg.locator("#nameless").click()
    tip = pg.locator(".tt").last
    tip.wait_for()
    assert "Head item, see on Wowhead" in tip.inner_text() and "211500" not in tip.inner_text()
    assert tip.locator("a", has_text="Open on Wowhead").get_attribute("href").endswith("/item=211500")
    c.close()
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_zogzog_section_links_keep_the_key(served, pw, engine):
    """/zogzog/'s section nav (#levels...) used to put "levels" where the key
    was: the hashchange reload then showed "Nothing here." on a plain click.
    The link scrolls to its section; the page and its address keep the key."""
    base, key = served
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(f"{base}/zogzog/#{key}")
    to_gear(pg)
    pg.locator('.sections a[href="#levels"]').click()
    settled(pg)  # the jump has landed: the page drawn, the target in place
    assert pg.locator("main.locked").count() == 0
    assert pg.evaluate("() => location.hash.slice(1)") == key
    assert pg.locator(".slotitm").first.is_visible()
    top = pg.locator("#levels").evaluate("(e) => e.getBoundingClientRect().top")
    assert 0 <= top < 300, top
    b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_zogzog_section_link_lands_when_the_advice_comes_late(served, pw, engine):
    """The gear advice is drawn in #gear, above every section, after the page
    shows. A section link followed before it came used to end a screen lower
    (the 0.7.0 release gate: #levels at 522 px in WebKit on a loaded runner).
    Here the advice is held until after the click: the jump still lands."""
    base, key = served
    try:
        b = getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001 - that browser isn't installed
        pytest.skip(f"no {engine}: {e}")
    c = b.new_context(viewport={"width": 1280, "height": 900})
    p0 = c.new_page()
    p0.goto(f"{base}/zogzog/#{key}")
    to_gear(p0)
    name = p0.locator("#char").input_value()
    p0.close()
    gear = publish.sealed_name(publish.unb64u(key), f"data/gear/{urllib.parse.quote(name, safe='')}.json")
    held = []
    pg = c.new_page()
    pg.route(f"**/zogzog/{gear}", lambda r: held.append(r))
    pg.goto(f"{base}/zogzog/#{key}")
    to_gear(pg)
    assert len(held) == 1 and pg.locator("#advice .adv-h").count() == 0
    before = pg.locator("#gear").evaluate("(e) => e.getBoundingClientRect().height")
    pg.locator('.sections a[href="#levels"]').click()
    assert pg.locator(BUSY).count() == 1  # the jump waits for the advice
    held[0].continue_()
    settled(pg)
    assert pg.locator("#advice .adv-h").count() >= 1
    grew = pg.locator("#gear").evaluate("(e) => e.getBoundingClientRect().height") - before
    assert grew > 900, grew  # the advice did push the sections down a screen
    top = pg.locator("#levels").evaluate("(e) => e.getBoundingClientRect().top")
    assert 0 <= top < 300, top
    c.close()
    b.close()
