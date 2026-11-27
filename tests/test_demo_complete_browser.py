"""The docs site's demo shows every section with data: no empty state, no
fallback, no "not yet" anywhere, on a phone (390 px) and a desktop, in
Chromium and WebKit (the owner reads it in Safari on an iPhone).

The demo is built the way the pages workflow builds it
(site/scripts/build-demo.sh: VictoriaLogs, VictoriaMetrics, the renderer,
the pinned client files), unless WOW_DEMO_DIR names one already built
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
- each of the nine characters opens its own dressed client model through
  the selector and plays its class and equipped weapon's animations.

The demo has no degraded section on purpose: a section that falls back is
a gap in the demo's data (tools/fixtures/generate.py, demo3d.py) or in
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

from tests.demo_models import LEVEL, MOUNT, PET, RANGED, ROSTER, check_answer, check_m2  # noqa: E402
from tests.test_browser import settled  # noqa: E402
from tests.test_model3d_browser import COVERED  # noqa: E402

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
    state = viewer_state(frame)
    page.locator("button.m3dtoggle[aria-pressed=true]:visible").first.click()
    return state


def viewer_state(frame) -> str:
    # The frame can still be on about:blank, before the viewer's <html>.
    frame.wait_for_function("() => ['drawn', 'error'].includes(document.documentElement?.dataset.state)",
                            timeout=60000)
    return frame.evaluate("() => document.documentElement.dataset.state")


CLASS_ACTION = {"PRIEST": "Power Word: Shield", "WARRIOR": "Battle Shout", "PALADIN": "Holy Light",
                "HUNTER": "Hunter's Mark", "ROGUE": "Kick", "SHAMAN": "Lightning Bolt", "MAGE": "Frostbolt",
                "WARLOCK": "Drain Life", "DRUID": "Rejuvenation"}
# The human female M2 aliases its omni precast (52) to its directed precast (51).
CLASS_CLIPS = {"PRIEST": [54], "WARRIOR": [55], "PALADIN": [51, 54], "HUNTER": [53], "ROGUE": [95],
               "SHAMAN": [51, 53], "MAGE": [51, 53], "WARLOCK": [124], "DRUID": [52, 54]}
RANGED_ACTION = {2: ("Shoot bow", [46]), 3: ("Fire gun", [49]), 19: ("Shoot wand", [53])}


def play_clips(frame, label, clips):
    """Record the M2 animation the renderer entered after the button click.
    A bow or gun can also have an optional ready pose before its release."""
    frame.evaluate("window.demoClips = []")
    frame.get_by_role("button", name=label, exact=True).click(timeout=30000)
    frame.wait_for_function("clips => clips.every(id => window.demoClips.includes(id))", arg=clips, timeout=30000)


def check_mounted_rider(frame, recipe):
    """Sample both real draws in the same frame. The rider's origin should stay at
    attachment 0 through the mount's gait, including when that bone moves."""
    mount, = recipe["mounts"]
    frame.evaluate("""mountName => {
      const proto = M3D.M2RendererGL.prototype, render = proto.render;
      let saddle, gait;
      window.saddles = new Set();
      proto.render = function(...args) {
        if (this.m2.name === mountName) {
          saddle = this.getAttachmentTransform(0);
          gait = this._current_anim()?.id;
        } else if (saddle && this._current_anim()?.id === 91) {
          const origin = this.model_matrix.slice(12, 15);
          if (origin.every((x, i) => Math.abs(x - saddle[12+i]) < 0.001))
            window.saddles.add(gait + '/' + Array.from(origin, x => x.toFixed(4)).join(','));
        }
        return render.apply(this, args);
      };
    }""", {123410: "PvPRidingDireWolf", 126407: "WarHorse"}[mount["model"]])
    select = frame.get_by_role("combobox", name="Mount")
    assert select.input_value() == "-1"
    select.select_option(label=mount["name"])
    for label, clip in (("Idle", 0), ("Walk", 4), ("Run", 5)):
        frame.get_by_role("button", name=label, exact=True).click()
        frame.wait_for_function("""([clip, samples]) =>
          [...window.saddles].filter(pose => pose.startsWith(clip + '/')).length >= samples""",
                                arg=[clip, 1 if clip == 0 else 2], timeout=15000)
    hit, colours = frame.evaluate(COVERED)
    assert hit > 20 and colours >= 3
    select.select_option(label="On foot")
    frame.get_by_role("button", name="Run", exact=True).wait_for(state="hidden")


def test_switching_a_published_character_reads_its_file_once(browser, base):
    with browser.new_context(viewport={"width": 390, "height": 844}) as ctx:
        page = ctx.new_page()
        page.clock.install()
        page.goto(base)
        page.locator("#hero").get_by_role("heading", name="Nibbet", exact=True).wait_for()
        name = "Flintwick-Bearhide"
        path = page.evaluate("name => new URL(window.wowApi('char', {name}), document.baseURI).href", name)
        requests = []
        page.on("request", lambda request: requests.append(request.url) if request.url == path else None)
        page.get_by_role("combobox", name="Character", exact=True).select_option(name)
        page.locator("#hero").get_by_role("heading", name="Flintwick", exact=True).wait_for()
        page.clock.run_for(3 * 60 * 1000 + 1000)
        assert requests == [path]
        assert not page.locator(".newspill:visible").count()


@pytest.mark.parametrize("which", ["public", "friends"])
@pytest.mark.parametrize(("name", "width"), [(name, 1280) for name in ROSTER]
                         + [(name, 390) for name, (cls, _, _) in ROSTER.items() if cls in {*MOUNT, "HUNTER"}])
def test_each_demo_character_draws(browser, base, demo_dir, name, width, which):
    with browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce") as ctx:
        page = ctx.new_page()
        served = set()
        page.on("response", lambda r: served.add(int(r.url.rsplit("/", 1)[1]))
                if r.ok and "/model3d/file/" in r.url else None)
        page.goto(base + ("friends/" if which == "friends" else ""))
        select = page.get_by_role("combobox", name="Character", exact=True)
        select.locator("option").nth(8).wait_for(state="attached")
        assert set(select.locator("option").evaluate_all("opts => opts.map(o => o.value)")) == set(ROSTER)
        assert (page.locator("html").get_attribute("data-sealed") == "1") == (which == "friends")
        page.get_by_role("tab", name="Character", exact=True).click()
        select.select_option(name)
        page.locator("#hero").get_by_role("heading", name=name.split("-")[0], exact=True).wait_for()
        page.locator("#hero").get_by_label(f"Level {LEVEL[name]}", exact=True).wait_for()
        if LEVEL[name] == 60:
            page.locator("#hero").get_by_text("Level cap reached", exact=True).wait_for()
            assert not page.locator("#hero").get_by_role("progressbar").count()
        answer = page.evaluate("name => window.wowGetJSON(window.wowApi('model3d', {name}))", name)
        recipe = check_answer(name, answer)
        files = (demo_dir / "zogzog" if which == "friends" else demo_dir) / "model3d" / "file"
        for part in (recipe, recipe.get("companion"), *recipe.get("mounts", [])):
            if part:
                with (files / str(part["model"])).open("rb") as stream:
                    check_m2(part["model"], stream)
        page.locator("#gear").get_by_role("button", name="View in 3D").click(timeout=30000)
        iframe = page.wait_for_selector(f"#gear iframe.m3dframe[data-name='{name}']", timeout=30000)
        frame = iframe.content_frame()
        assert viewer_state(frame) == "drawn", frame.locator("#status").inner_text()
        if width == 390:
            size = iframe.bounding_box()
            card = page.locator("#gear .doll").bounding_box()
            slots = page.locator("#gear .doll > .slots.left").bounding_box()
            assert size["width"] >= card["width"] - 2
            assert slots["y"] >= size["y"] + size["height"]
        hit, colours = frame.evaluate(COVERED)
        assert hit > 20 and colours >= 3, (name, hit, colours)
        outfit = {layer["file"] for mat in recipe["materials"] for layer in mat["layers"]}
        outfit.update(a["model"] for a in recipe["attachments"] + recipe["collections"])
        assert {recipe["model"], *outfit} <= served, (name, outfit - served)

        cls = ROSTER[name][0]
        if cls in PET:
            toggle = frame.get_by_role("checkbox", name=PET[cls][1])
            assert toggle.is_checked()
            toggle.uncheck()
            assert frame.evaluate(COVERED) != [hit, colours], name
            toggle.check()
            pet = recipe["companion"]
            assert {pet["model"], *pet["textures"].values()} <= served, name
        else:
            assert not frame.locator("#companion-control").is_visible()

        if cls in MOUNT:
            check_mounted_rider(frame, recipe)
            mount = recipe["mounts"][0]
            assert {mount["model"], *(f for f in mount["textures"].values() if f)} <= served
        else:
            assert not frame.get_by_role("combobox", name="Mount").count()

        frame.evaluate("""() => {
          window.demoClips = [];
          const proto = M3D.M2RendererGL.prototype, render = proto.render;
          proto.render = function(...args) {
            const id = this._current_anim()?.id;
            if (id !== undefined && !window.demoClips.includes(id)) window.demoClips.push(id);
            return render.apply(this, args);
          };
        }""")
        play_clips(frame, CLASS_ACTION[cls], CLASS_CLIPS[cls])
        if cls in RANGED:
            play_clips(frame, *RANGED_ACTION[RANGED[cls][0]])
        else:
            assert not frame.locator('button[data-action="ranged"]').count()
        if cls == "HUNTER":
            play_clips(frame, "Arcane Shot", [49])


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
    page.wait_for_selector("#char option:nth-child(9)", state="attached")
    assert page.locator("#char").input_value() == "Nibbet-Gearholt"
    assert page.locator("#char option").count() == 9
    found: dict[str, list[str]] = {}
    tabs = page.eval_on_selector_all(".tabs [role=tab]", "els => els.map(e => e.id)")
    assert tabs
    for tab in tabs:
        page.click(f"#{tab}")
        settled(page)
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
                level = int(page.locator(".snapctl output").inner_text().split()[-1])
                page.locator(f".snapbody[data-level='{level}']").wait_for(timeout=30000)
                with contextlib.suppress(sync_api.TimeoutError):
                    wait_stills(page)
                got += [f"level {level}: {g}" for g in gaps(page) + icon_gaps(page)]
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
    settled(page)
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
    settled(page)
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


def doll_state(page) -> dict:
    """The doll's badges by slot, from the page's own drawing."""
    page.wait_for_selector("#gear .slot .ifr .ibis", state="attached", timeout=15000)
    return dict(page.eval_on_selector_all("#gear .slot[data-slot]", """els => els.map(e => [
      e.dataset.slot, (e.querySelector('.ifr .ibis') || {}).textContent || null])"""))


@pytest.mark.parametrize("width", WIDTHS)
def test_public_doll_shows_the_bis_badges(browser, base, width):
    """Owner, 2026-11-23: the public page's paperdoll shows each worn slot's
    BiS badge and the legend, the same as the friends' page, and none of
    the advice (no upgrade line under a slot, no Upgrades box)."""
    ctx = browser.new_context(viewport={"width": width, "height": 844 if width < 600 else 900})
    page = ctx.new_page()
    page.goto(base + "friends/")
    friends = doll_state(page)
    page.goto(base)
    page.wait_for_selector(".tabs [role=tab]")
    public = doll_state(page)
    assert any(public.values()) and public == friends, (public, friends)
    for tab in page.eval_on_selector_all(".tabs [role=tab]", "els => els.map(e => e.id)"):
        page.click(f"#{tab}")
        if page.locator("#gear .doll").is_visible():
            break
    settled(page)
    for b in badges(page, "#gear .slot"):
        check_badge(b)
    assert page.locator("#gear .bislegend").is_visible()
    assert page.locator("#gear .bislegend .bischip").all_text_contents() == ["BiS", "85%+", "50%+"]
    assert not page.locator("#gear .slot .iup").count()
    assert not page.locator("#advice").inner_text().strip()
    ctx.close()
