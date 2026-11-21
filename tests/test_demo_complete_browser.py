"""The docs site's demo shows every section with data: no empty state, no
fallback, no "not yet" anywhere, on a phone (390 px) and a desktop, in
Chromium and WebKit (the owner reads it in Safari on an iPhone).

The demo is built the way the pages workflow builds it
(site/scripts/build-demo.sh: VictoriaLogs, VictoriaMetrics, the renderer,
the golden game files), unless WOW_DEMO_DIR names one already built
(the pages workflow, after its build). WOW_DEMO_ENGINES narrows the
engines (chromium,webkit).

Every tab of the public page and of the friends' page is opened; on each:
- no visible `.empty` line (the sections' "nothing yet" states), but the
  two that only summarise (`And 3 more...`, `None: every step...`);
- none of the fallback phrases (FALLBACK);
- the character's picture in place of the silhouette wherever a `.still`
  shows (the Equipment panel, Back in time at every level);
- every icon drawn, collapsed lists opened and lazy images loaded first: no
  item frame still on its "?" or its slot's glyph, no talent showing its letter,
  no broken `<img>` (naturalWidth 0), and no item on the game's question
  mark (an item the bundle has no card or icon for);
- "View in 3D" opens a model the viewer drew (data-state drawn).

The demo has no degraded section on purpose: a section that falls back is
a gap in the demo's data (tools/fixtures/generate.py, golden3d.py) or in
the publisher, never something to allow here.

Needs the `browser` group and its browsers (`uv run --group browser
playwright install chromium webkit`), and Docker or VL_BIN and VM_BIN for
the build; skipped without them, like the other browser tests."""

from __future__ import annotations

import contextlib
import functools
import http.server
import os
import re
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (390, 1280)
ENGINES = [e for e in os.environ.get("WOW_DEMO_ENGINES", "chromium,webkit").split(",") if e]
# What a section says when it has nothing (web/app.js): none of these may show.
FALLBACK = ("not known yet", "No talents yet", "unknown until", "No 3D model", "nothing banked", "not recorded",
            "Picture on its way", "haven't come", "haven’t come", "No equipment saved yet", "No gear advice yet",
            "needs Polestar", "No dungeon in reach yet", "No analysis yet", "No talent change",
            "Talent changes need Battle.net", "can’t be read right now", "Couldn’t draw", "no landing seen",
            "travel 0s", "quests 0s")
# `.empty` lines that summarise rather than say a section is empty.
EMPTY_OK = ("And ", "None: every step was reached in order")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory) -> Path:
    given = os.environ.get("WOW_DEMO_DIR", "").strip()
    if given:
        return Path(given).resolve()
    if not (shutil.which("docker") or (os.environ.get("VL_BIN") and os.environ.get("VM_BIN"))):
        pytest.skip("the demo build needs Docker, or VL_BIN and VM_BIN")
    out = tmp_path_factory.mktemp("demo") / "demo"
    env = {**os.environ, "DEMO_OUT": str(out), "DEMO_VL_PORT": str(free_port()), "DEMO_VM_PORT": str(free_port()),
           "DEMO_RENDER_PORT": str(free_port())}
    r = subprocess.run([str(ROOT / "site" / "scripts" / "build-demo.sh")], env=env, capture_output=True, text=True,
                       timeout=1200)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return out


class Pages(http.server.SimpleHTTPRequestHandler):
    """Plain files as GitHub Pages serves them: /x answers x.html when there
    is no x (the 3D view's frame is model3d/view)."""

    def translate_path(self, path):
        p = super().translate_path(path)
        return p + ".html" if not os.path.exists(p) and os.path.isfile(p + ".html") else p

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def base(demo_dir):
    """The demo under /demo/ on 127.0.0.1: a secure context, as the sealed
    page's WebCrypto needs."""
    handler = functools.partial(Pages, directory=str(demo_dir.parent))
    # The page fetches a few hundred icons at once: the default backlog (5)
    # resets connections that GitHub Pages would answer.
    srv = type("Server", (http.server.ThreadingHTTPServer,), {"request_queue_size": 256})(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/{demo_dir.name}/"
    srv.shutdown()


@pytest.fixture(scope="module", params=ENGINES)
def browser(request):
    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, request.param).launch()
        except Exception as e:  # noqa: BLE001 - Playwright's own error: the browser isn't installed
            if os.environ.get("WOW_DEMO_DIR"):
                raise  # the pages workflow: a guard that can't run fails
            pytest.skip(f"{request.param}: {e}")
        yield b
        b.close()


def gaps(page) -> list[str]:
    """What the page shows that it shouldn't: visible empty lines, fallback
    phrases, a silhouette where the picture goes."""
    out = []
    for txt in page.eval_on_selector_all(
            ".empty", "els => els.filter(e => e.offsetParent !== null).map(e => e.innerText.trim())"):
        if not txt.startswith(EMPTY_OK):
            out.append(f"empty: {txt[:120]}")
    body = page.inner_text("body")
    out += [f"text: {f}" for f in FALLBACK if f.lower() in body.lower()]
    stills = page.eval_on_selector_all(
        ".still", "els => els.filter(e => e.offsetParent !== null).map(e => e.className)")
    out += [f"silhouette: .{c}" for c in stills if "has-render" not in c.split()]
    return out


# Every image loaded now (they're lazy: a phone shows them as you scroll)
# and every collapsed list open, then what still isn't an icon.
ICONS_JS = """async () => {
  const vis = (e) => e.offsetParent !== null;
  for (const d of document.querySelectorAll('details:not([open])')) d.open = true;
  const QM = window.wowIcon ? await window.wowIcon('inv_misc_questionmark', 'medium').catch(() => null) : null;
  const settle = async () => {
    for (const i of document.images) i.loading = 'eager';
    await Promise.all([...document.images].map((i) => i.complete ? 0
      : new Promise((r) => { i.onload = i.onerror = r; setTimeout(r, 5000); })));
  };
  for (let n = 0; n < 20; n++) {
    await settle();
    await new Promise((r) => setTimeout(r, 250));
    const busy = [...document.querySelectorAll('button.itm[aria-label]:not([aria-label=""]) .ifr:not(.has-icon)')].filter(vis).length
      + [...document.images].filter((i) => !i.complete).length;
    if (!busy) break;
  }
  const name = (e) => ((e.closest('[aria-label]') || {}).ariaLabel || e.closest('li, div')?.innerText || '')
    .trim().split('\\n')[0].slice(0, 60);
  const out = [];
  for (const f of document.querySelectorAll('.ifr.noicon')) if (vis(f)) out.push('icon "?": ' + name(f));
  // A slot draws its glyph until the icon comes: an item's frame without it.
  for (const f of document.querySelectorAll('button.itm[aria-label]:not([aria-label=""]) .ifr:not(.has-icon):not(.noicon)'))
    if (vis(f)) out.push('icon missing: ' + name(f));
  for (const f of document.querySelectorAll('.tface')) if (vis(f) && !f.querySelector('img')) out.push('talent letter: ' + name(f));
  for (const i of document.images) {
    if (!vis(i)) continue;
    if (i.complete && i.naturalWidth === 0) out.push('broken image: ' + (i.currentSrc || i.src).slice(0, 80) + ' ' + name(i));
    else if (QM && i.src === QM) out.push('question mark icon: ' + name(i));
  }
  return out;
}"""


def icon_gaps(page) -> list[str]:
    return page.evaluate(ICONS_JS)


def wait_stills(page) -> None:
    # A picture is drawn once decrypted and decoded: give it the time.
    page.wait_for_function("() => [...document.querySelectorAll('.still')].filter(e => e.offsetParent !== null)"
                           ".every(e => e.classList.contains('has-render'))", timeout=15000)


def open_3d(page, i: int) -> str:
    """Click the i-th visible "View in 3D", return the viewer's state."""
    page.locator("button.m3dtoggle:visible").nth(i).click()
    frame = page.wait_for_selector("iframe.m3dframe >> visible=true", timeout=15000).content_frame()
    # The frame is still on its way from about:blank to the viewer: no root
    # element for a moment, and a throw there ends the wait instead of polling.
    frame.wait_for_function("() => { const r = document.documentElement;"
                            " return !!r && ['drawn', 'error'].includes(r.dataset.state); }", timeout=60000)
    state = frame.evaluate("() => document.documentElement.dataset.state + ' '"
                           " + ((document.getElementById('status') || {}).textContent || '')")
    page.locator("button.m3dtoggle[aria-pressed=true]:visible").first.click()
    return state


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("which", ["public", "friends"])
def test_demo_has_no_gap(browser, base, which, width):
    ctx = browser.new_context(viewport={"width": width, "height": 844 if width < 600 else 900})
    page = ctx.new_page()
    errors: list[str] = []
    # WebKit reports a ResizeObserver that resized again within a frame as
    # an error event: the spec's notice, not a failure of the page.
    page.on("pageerror", lambda e: None if "ResizeObserver loop" in str(e) else errors.append(str(e)))
    page.goto(base + ("" if which == "public" else "friends/"))
    page.wait_for_selector(".tabs [role=tab]")
    found: dict[str, list[str]] = {}
    tabs = page.eval_on_selector_all(".tabs [role=tab]", "els => els.map(e => e.id)")
    assert tabs
    for tab in tabs:
        page.click(f"#{tab}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        with_still = page.eval_on_selector_all(".still", "els => els.filter(e => e.offsetParent !== null).length")
        if with_still:
            with contextlib.suppress(sync_api.TimeoutError):  # gaps() names it
                wait_stills(page)
        got = gaps(page) + icon_gaps(page)
        # Back in time: every level, its picture and its numbers.
        slider = page.locator("input[type=range][aria-label=Level]")
        if slider.count() and slider.first.is_visible():
            hi = int(slider.first.get_attribute("max"))
            for i in range(hi + 1):
                slider.first.fill(str(i))
                page.wait_for_timeout(300)
                with contextlib.suppress(sync_api.TimeoutError):
                    wait_stills(page)
                got += [f"level {i}: {g}" for g in gaps(page) + icon_gaps(page)]
        for i in range(page.locator("button.m3dtoggle:visible").count()):
            state = open_3d(page, i)
            if not state.startswith("drawn"):
                got.append(f"3D: {state[:160]}")
        if got:
            found[tab] = sorted(set(got))
    ctx.close()
    assert not errors, errors
    assert not found, found


BADGE = r"^(BiS|[1-9]?[0-9]%)$"
TIERS = {"t-bis": (100, 100), "t-epic": (85, 99), "t-rare": (50, 84)}


def badges(page, scope: str) -> list[dict]:
    """The BiS badges on the item icons under `scope`: text, tier class and
    colours (app.js itemChip)."""
    return page.eval_on_selector_all(f"{scope} .itm .ifr .ibis", """els => els.map(e => {
      const cs = getComputedStyle(e);
      return { text: e.textContent, cls: e.className, bg: cs.backgroundColor, fg: cs.color,
               label: e.closest('.itm').getAttribute('aria-label') };
    })""")


# The doll's slots (app.js paperdoll, data-slot) as an item line names them.
LINE_SLOT = {1: "head", 2: "neck", 3: "shoulder", 5: "chest", 6: "waist", 7: "legs", 8: "feet", 9: "wrist",
             10: "hands", 11: "finger", 12: "finger", 13: "trinket", 14: "trinket", 15: "back", 16: "weapons",
             17: "weapons", 18: "ranged"}


def doll_badges(page) -> dict[str, set]:
    """The doll's badge per slot as an item line says it ("59%", "100%",
    "empty"): {slot word: {...}}, two of each for rings and trinkets."""
    got = page.eval_on_selector_all("#gear .slot[data-slot]", """els => els.map(e => [
      e.dataset.slot, e.classList.contains('empty'), (e.querySelector('.ifr .ibis') || {}).textContent || null])""")
    out: dict[str, set] = {}
    for slot, empty, badge in got:
        word = LINE_SLOT.get(int(slot))
        if word:
            out.setdefault(word, set()).add("empty" if empty else "100%" if badge == "BiS" else badge)
    return out


def check_badge(b: dict) -> None:
    assert re.match(BADGE, b["text"]), b
    pct = 100 if b["text"] == "BiS" else int(b["text"].rstrip("%"))
    tier = next((t for t in TIERS if t in b["cls"].split()), None)
    want = next((t for t, (lo, hi) in TIERS.items() if lo <= pct <= hi), None)
    assert tier == want, b  # the doll's colours: orange BiS, purple 85%+, blue 50%+, plain below
    assert b["label"].endswith("BiS"), b  # said, not only shown


@pytest.mark.parametrize("width", WIDTHS)
def test_items_carry_the_bis_badge(browser, base, width):
    """Owner, 2026-11-21: every item shown carries the paperdoll's BiS badge
    (the same number and colours) and its change as the slot's BiS share
    from what's worn to the item, "70% → 91% on your chest" (owner,
    2026-11-21: "+21%" read as 21% more stats), never "points (n% of your
    gear)": the Overview's upgrade and dungeon cards, and the Dungeons
    panel's drops."""
    ctx = browser.new_context(viewport={"width": width, "height": 844 if width < 600 else 900})
    page = ctx.new_page()
    page.goto(base + "friends/")
    page.wait_for_selector("#home .move")
    page.wait_for_timeout(800)
    for kind in ("upgrade", "dungeon"):
        card = f"#home .move.k-{kind}"
        got = badges(page, card)
        assert got, f"no badge on a {kind} card"
        for b in got:
            check_badge(b)
        text = page.locator(card).first.inner_text()
        assert re.search(r"(empty|\d+%) → \d+% on your ", text) and "of your gear" not in text, text
        assert not re.search(r"[+−]\d+% on your ", text), text
    page.click("#tab-overview")
    page.wait_for_selector(".dg .dgdrop")
    page.wait_for_timeout(800)
    drops = badges(page, ".dg .dgdrop")
    assert drops
    for b in drops:
        check_badge(b)
    # A missing drop's or quest reward's line is the Overview's (app.js
    # upLine): from the worn share to the item's, the badge's number, the
    # slot in words; never the slot as a chip (owner, 2026-11-21).
    gains = page.eval_on_selector_all(".dg .dgdrop.missing:has(.ifr .ibis)", """els => els.map(e => [
      e.querySelector('.dgmeta .gain').textContent, e.querySelector('.ifr .ibis').textContent])""")
    assert gains
    assert not page.locator(".dg .dgdrop:has(.dgmeta .gain) .pill").count()
    doll = doll_badges(page)
    for text, badge in gains:
        m = re.match(r"^(empty|\d+%) → (\d+)% on your ([a-z ]+?)( at level \d+)?$", text)
        assert m and int(m.group(2)) == (100 if badge == "BiS" else int(badge.rstrip("%"))), (text, badge)
        # One BiS share per slot: the line starts from the doll's badge.
        assert m.group(1) in doll.get(m.group(3), {"empty"}), (text, doll.get(m.group(3)))
    # What a trip is worth: the chance a clear drops an upgrade, no points.
    trip = page.locator(".dg .dgfacts").first.inner_text()
    assert "Gear a clear" in trip and not re.search(r"[+−]\d+\.\d", trip), trip
    # The same colour as the doll's badge of the same tier.
    doll = {t: page.eval_on_selector_all(f"#gear .slot .ifr .ibis.{t}", "els => els.map(e => getComputedStyle(e)"
                                         ".backgroundColor)") for t in TIERS}
    for b in drops:
        tier = next((t for t in TIERS if t in b["cls"].split()), None)
        if tier and doll[tier]:
            assert b["bg"] == doll[tier][0], (b, doll[tier])
    ctx.close()
