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


# Only the test owns the clock and random source. The embedded viewer still
# chooses its action on the same idle timer a player gets. Observe draws
# from the original actor, the shot and the spell models on that frame.
OBSERVE_IDLE = """(() => {
  const probe = window.motion = {now:0, want:null, clips:[], seen:new Set(), blended:new Set(), mountRandom:0};
  let library;
  Object.defineProperty(window, 'M3D', {get:() => library, set:value => {
    if (library === value) return;
    library = value;
    let draw;
    Object.defineProperty(value, 'draw', {get:() => draw, set:original => {
      draw = async (canvas, recipe, get, opts) => {
        const proto = value.M2RendererGL.prototype, render = proto.render;
        let actor, drawing;
        const gl = WebGL2RenderingContext.prototype;
        for (const [method, count] of [['drawElements',1], ['drawArrays',2]]) {
          const originalDraw = gl[method];
          gl[method] = function(...args) {
            if (drawing && args[count] > 0) probe.seen.add(drawing);
            return originalDraw.apply(this, args);
          };
        }
        proto.render = function(...args) {
          if (!actor && this.fileDataID === recipe.model) actor = this;
          drawing = this.fileDataID;
          if (this === actor) {
            const id = this._current_anim()?.id;
            if (probe.clips.at(-1) !== id) probe.clips.push(id);
            if (this.transition) probe.blended.add(id);
            const request = this._sequence(this.current_animation)?.requested;
            if (probe.requested && probe.requested.at(-1) !== request) probe.requested.push(request);
          }
          try { return render.apply(this, args); } finally { drawing = null; }
        };
        const particles = proto.renderParticles;
        proto.renderParticles = function(...args) {
          drawing = this.fileDataID;
          try { return particles.apply(this, args); } finally { drawing = null; }
        };
        const chainDraw = value.SpellChain.prototype.render;
        value.SpellChain.prototype.render = function(...args) {
          drawing = this.def.texture;
          try { return chainDraw.apply(this, args); } finally { drawing = null; }
        };
        const random = () => {
          if (probe.handle?.mounted >= 0) return probe.mountRandom;
          const idle = probe.handle?.debug.idle();
          const pool = (idle?.available || []).filter(m => m !== idle?.played.at(-1));
          const i = pool.indexOf(probe.want);
          return i >= 0 ? (i + 0.5) / pool.length : 0;
        };
        probe.handle = await original(canvas, recipe, get, {...opts, clock:() => probe.now, random});
        return probe.handle;
      };
    }});
  }});
  probe.frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
})();"""

# Visual IDs below are from the installed Forever 1.60.1.71139 client.
# Each M2 must be drawn in its turn. Drain Life's chain uses a BLP directly.
CLASS_DRAW = {
    "PRIEST": ("power-word-shield", {166336, 166342}),
    "WARRIOR": ("battle-shout", {165658}), "PALADIN": ("holy-light", {166336, 166349}),
    "HUNTER": ("hunters-mark", {166363, 166362}), "ROGUE": ("kick", {166437}),
    "SHAMAN": ("lightning-bolt", {166492, 166488, 166497, 166496}),
    "MAGE": ("frostbolt", {166381, 166214, 166370, 166385}),
    "WARLOCK": ("drain-life", {166471, 186214}), "DRUID": ("rejuvenation", {166602, 166694}),
}
PROJECTILE = {"PRIEST": 165891, "MAGE": 165570, "WARLOCK": 166135, "WARRIOR": 137240,
              "ROGUE": 137240, "HUNTER": 137245}


def spontaneous(frame, move, effects, *, ranged=None):
    frame.evaluate("""wanted => {
      const p = motion;
      p.want = wanted;
      p.seen.clear(); p.clips.length = 0; p.blended.clear(); p.requested = [];
      p.now = p.handle.debug.idle().next_at + 1;
    }""", move)
    frame.wait_for_function("move => motion.handle.debug.idle().move === move", arg=move, timeout=15000)
    result = frame.evaluate("""async expected => {
      const p = motion;
      // Step through release in small increments: a real arrow only spends a
      // few frames between these actors. Long auras can coast after impact.
      for (let i = 0; i < 240 && p.handle.debug.idle().move; i++) {
        p.now += expected.every(id => p.seen.has(id)) ? 80 : 30;
        await p.frame();
        if (expected.every(id => p.seen.has(id)) && p.clips.some(id => id > 0) && p.clips.at(-1) === 0) break;
      }
      const slots = p.handle.debug.equippedSlots();
      if (p.handle.debug.idle().move) { p.now += 65000; await p.frame(); }
      await p.frame();
      return {seen:[...p.seen], clips:p.clips, requested:p.requested, blended:[...p.blended], slots,
        idle:p.handle.debug.idle().move, errors:p.handle.errors};
    }""", sorted(effects))
    assert not result["errors"], result
    assert effects <= set(result["seen"]), (move, effects - set(result["seen"]), result)
    assert result["idle"] is None and result["clips"][-1] == 0, (move, result)
    if ranged:
        ready, release = {2: ({105, 109}, 46), 3: ({106, 110}, 49), 19: ({111, 112}, 107)}[ranged]
        # The Skyborne and some Classic bodies alias clips. Check what the
        # kit requested as well as the model's actual animated return to Stand.
        sequence = result["requested"]
        assert release in sequence and ready.intersection(sequence[:sequence.index(release)]), result
        assert sequence[-1] == 0 and 0 in result["blended"], (move, result)



MODEL_EXTENT = """async () => {
  await motion.frame();
  const stage = document.getElementById('stage'), k = document.createElement('canvas');
  k.width = stage.width; k.height = stage.height;
  const g = k.getContext('2d'); g.drawImage(stage,0,0);
  const px = g.getImageData(0,0,k.width,k.height).data;
  let l=k.width, r=-1, t=k.height, b=-1;
  for (let y=0; y<k.height; y++) for (let x=0; x<k.width; x++) {
    if (px[(y*k.width+x)*4+3]<64) continue;
    l=Math.min(l,x); r=Math.max(r,x); t=Math.min(t,y); b=Math.max(b,y);
  }
  return [(r-l+1)/k.width,(b-t+1)/k.height,l,t,k.width-1-r,k.height-1-b];
}"""


def check_framing(frame, min_height):
    width, height, *margins = frame.evaluate(MODEL_EXTENT)
    assert height >= min_height and width >= 0.35 and min(margins) > 0, (width, height, margins)


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
    assert select.input_value() == "foot"
    assert select.locator("option").all_text_contents() == ["On foot", "On mount"]
    select.select_option(label="On mount")
    note = frame.locator("#mount-note")
    sync_api.expect(note).to_have_text(mount["name"], timeout=15000)
    assert frame.evaluate("() => motion.handle.mounted") >= 0
    check_framing(frame, 0.68)
    for clip, draw in ((4, 0), (5, 0.99)):
        poses = frame.evaluate("""async ([clip, draw]) => {
          motion.mountRandom = draw;
          motion.now = motion.handle.debug.idle().next_at + 1;
          await motion.frame();
          const poses = () => [...window.saddles].filter(s => s.startsWith(clip + '/'));
          for (let i = 0; i < 12 && poses().length < 2; ++i) {
            motion.now += 40;
            await motion.frame();
          }
          return poses();
        }""", [clip, draw])
        assert len(poses) >= 2, (mount["name"], clip, poses)
        frame.evaluate("async () => {motion.now += 10000; await motion.frame()}")
    select.select_option(label="On foot")
    sync_api.expect(note).to_have_text("")
    assert frame.evaluate("() => motion.handle.mounted") == -1


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
def test_roster_climbs_and_historical_gear_on_the_phone(browser, base, which):
    with browser.new_context(viewport={"width": 390, "height": 844}) as ctx:
        page = ctx.new_page()
        page.goto(base + ("friends/" if which == "friends" else ""))
        select = page.get_by_role("combobox", name="Character", exact=True)
        select.locator("option").nth(8).wait_for(state="attached")
        page.get_by_role("tab", name="Character", exact=True).click()
        for name, level in LEVEL.items():
            select.select_option(name)
            page.locator("#hero").get_by_role("heading", name=name.split("-")[0], exact=True).wait_for()
            climb = page.locator("#levels .lede")
            climb.filter(has_text=re.compile(r"played since tracking began at level \d+")).wait_for()
            d = page.evaluate("name => window.wowGetJSON(window.wowApi('char', {name}))", name)
            hd = d["header"]
            text = climb.inner_text()
            assert f"played since tracking began at level {hd['first_level']}" in text, name
            if name == "Brakka-Ironvale":
                assert "31h 30m played since tracking began at level 55, including 10h 22m at level 60" in text
                assert "Polestar’s route plans 19h 28m for 5 finished levels. You played 21h 09m on those levels" in text
                ticks = page.locator("#levels .climb svg text.axis").all_text_contents()
                assert max(int(t) for t in ticks if t.isdecimal()) == 60
            assert hd["level"] == level and hd["played_s"] > 3600
            splits = d["levels"]
            assert len(splits) >= 5 and all(s["seconds"] > 0 for s in splits)
            assert any(s.get("route_s") for s in splits), name
            if name != "Nibbet-Gearholt":
                assert hd["played_total_s"] >= hd["played_s"] - 1, name
            for lv in [s["level"] for s in splits] + [level]:
                snap = page.evaluate("([name, level]) => window.wowGetJSON(window.wowApi('snapshot', {name, level}))",
                                     [name, lv])
                items = (snap.get("gear") or {}).get("items", [])
                assert all((it.get("req_level") or 0) <= lv for it in items), (name, lv)


@pytest.mark.parametrize("which", ["public", "friends"])
def test_all_equipped_items_and_talent_nodes_show_their_own_jpeg(browser, base, which):
    with browser.new_context(viewport={"width": 390, "height": 844}) as ctx:
        page = ctx.new_page()
        page.goto(base + ("friends/" if which == "friends" else ""))
        select = page.get_by_role("combobox", name="Character", exact=True)
        select.locator("option").nth(8).wait_for(state="attached")
        page.get_by_role("tab", name="Character", exact=True).click()
        for name in ROSTER:
            select.select_option(name)
            page.locator("#hero").get_by_role("heading", name=name.split("-")[0], exact=True).wait_for()
            # The heading is ready before the friends' page finishes decrypting its icons.
            page.wait_for_function("""() => {
                const faces = [...document.querySelectorAll('#talents .tface, #gear .slot:not(.empty) .ifr')];
                return faces.length && faces.every(f => f.querySelector('img'));
            }""")
            errors = page.evaluate("""async name => {
                const sheet = await window.wowGetJSON(window.wowApi('char', {name}));
                const picks = [];
                for (const item of sheet.gear.items.filter(i => i.item_id)) {
                    const card = await window.wowCard(item.item_id);
                    picks.push([item.name, document.querySelector('#gear .slot[data-slot="' + item.slot + '"] .ifr'),
                                card?.i, 'large']);
                }
                const faces = [...document.querySelectorAll('#talents .tface')];
                const nodes = sheet.talents.sections.flatMap(s => s.nodes);
                if (!nodes.length || faces.length !== nodes.length) return ['talent grid missing'];
                nodes.forEach((n, i) => picks.push([n.name, faces[i], n.icon, 'medium']));
                const sealed = document.documentElement.dataset.sealed === '1';
                return (await Promise.all(picks.map(async ([label, frame, icon, size]) => {
                    if (!icon || icon === 'inv_misc_questionmark') return label + ': no catalog icon';
                    const src = await window.wowIcon(icon, size);
                    if (!(sealed ? src?.startsWith('data:image/jpeg;base64,/9j/')
                                 : src === 'data/icon/' + size + '/' + icon + '.jpg')) return label + ': ' + src?.slice(0, 45);
                    const img = frame?.querySelector('img');
                    if (img?.getAttribute('src') !== src) return label + ': wrong image';
                    img.loading = 'eager';
                    try { await img.decode(); } catch { return label + ': broken image'; }
                    return img.naturalWidth ? null : label + ': empty image';
                }))).filter(Boolean);
            }""", name)
            assert not errors, (name, errors)


@pytest.mark.parametrize("which", ["public", "friends"])
@pytest.mark.parametrize(("name", "width"), [(name, 1280) for name in ROSTER]
                         + [(name, 390) for name, (cls, _, _) in ROSTER.items() if cls in {*MOUNT, "HUNTER", "SHAMAN"}])
def test_each_demo_character_draws(browser, base, demo_dir, name, width, which):
    with browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="no-preference") as ctx:
        ctx.add_init_script(OBSERVE_IDLE)
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
        assert frame.get_by_role("button").count() == 0
        assert frame.get_by_role("checkbox").count() == 0
        assert frame.get_by_role("combobox").count() == int(cls in MOUNT)
        if cls == "SHAMAN":
            check_framing(frame, 0.70)
        if cls in PET:
            pet = recipe["companion"]
            assert {pet["model"], *pet["textures"].values()} <= served, name
            assert frame.evaluate("id => motion.seen.has(id)", pet["model"])

        if cls in MOUNT:
            check_mounted_rider(frame, recipe)
            mount = recipe["mounts"][0]
            assert {mount["model"], *(f for f in mount["textures"].values() if f)} <= served
        else:
            assert not frame.get_by_role("combobox", name="Mount").count()

        # Both publication tiers must retain the class kit. Exercise every
        # shot on desktop; mobile also covers mounting and actual stage size.
        if width == 1280:
            move, effects = CLASS_DRAW[cls]
            spontaneous(frame, move, effects)
            if cls in RANGED:
                spontaneous(frame, "ranged", {PROJECTILE[cls]}, ranged=RANGED[cls][0])
            if cls == "HUNTER":
                spontaneous(frame, "arcane-shot", {1931959, 165592, 166525})



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
