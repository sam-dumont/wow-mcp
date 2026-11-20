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
            "Talent changes need Battle.net", "can’t be read right now", "Couldn’t draw")
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
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
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


def wait_stills(page) -> None:
    # A picture is drawn once decrypted and decoded: give it the time.
    page.wait_for_function("() => [...document.querySelectorAll('.still')].filter(e => e.offsetParent !== null)"
                           ".every(e => e.classList.contains('has-render'))", timeout=15000)


def open_3d(page, i: int) -> str:
    """Click the i-th visible "View in 3D", return the viewer's state."""
    page.locator("button.m3dtoggle:visible").nth(i).click()
    frame = page.wait_for_selector("iframe.m3dframe >> visible=true", timeout=15000).content_frame()
    frame.wait_for_function("() => ['drawn', 'error'].includes(document.documentElement.dataset.state)",
                            timeout=60000)
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
        got = gaps(page)
        # Back in time: every level, its picture and its numbers.
        slider = page.locator("input[type=range][aria-label=Level]")
        if slider.count() and slider.first.is_visible():
            hi = int(slider.first.get_attribute("max"))
            for i in range(hi + 1):
                slider.first.fill(str(i))
                page.wait_for_timeout(300)
                with contextlib.suppress(sync_api.TimeoutError):
                    wait_stills(page)
                got += [f"level {i}: {g}" for g in gaps(page)]
        for i in range(page.locator("button.m3dtoggle:visible").count()):
            state = open_3d(page, i)
            if not state.startswith("drawn"):
                got.append(f"3D: {state[:160]}")
        if got:
            found[tab] = sorted(set(got))
    ctx.close()
    assert not errors, errors
    assert not found, found
