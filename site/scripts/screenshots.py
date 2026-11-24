"""The manual's screenshots, taken from the demo character (Nibbet, made up
by tools/fixtures/generate.py --demo), never from a real player's data.

    site/scripts/build-demo.sh && npm --prefix site run build
    uv run --extra render python site/scripts/screenshots.py http://127.0.0.1:4399/wow-mcp/

The address is the built site (site/dist) served the way GitHub Pages does
(`model3d/view` answers `model3d/view.html`): `astro preview` doesn't, and
View in 3D stays blank there. The dungeons shot comes from the full view
instead (full_view): the friends' copy leaves out who was in each run. Writes
docs/images/<name>.webp (light theme)
and <name>-dark.webp (dark) for every shot, and docs/images/social.png (the
link preview). The site shows the one for the reader's theme
(site/scripts/prepare.mjs); GitHub shows the light one.
Commit the images; the dates on them are the demo's and age, which is fine.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "images"
WIDTH = 1280


def settle(page) -> None:
    """Every icon on screen loaded: a half-loaded page shows "?" tiles."""
    with contextlib.suppress(PlaywrightTimeout):  # the 3D viewer keeps fetching: idle is a hint
        page.wait_for_load_state("networkidle", timeout=5000)
    page.evaluate("""async () => {
      for (const img of document.images) img.loading = "eager";
      const loaded = (i) => new Promise((r) => { i.onload = i.onerror = r; });
      await Promise.all([...document.images].map((i) => (i.complete ? 0 : loaded(i))));
    }""")
    page.wait_for_timeout(400)


def section(page, where, path: Path, height: int | None = None) -> None:
    el = (page.locator(where) if isinstance(where, str) else where).first
    el.scroll_into_view_if_needed()
    settle(page)
    box = el.bounding_box()
    y = box["y"] + page.evaluate("scrollY")  # the clip is in page coordinates with full_page
    clip = {"x": box["x"], "y": y, "width": box["width"], "height": min(box["height"], height or box["height"])}
    full_page_clip(page, path, clip)


def full_page_clip(page, path: Path, clip: dict) -> None:
    """A clip of the whole page taken from the top: scrolled, a sticky bar
    (the climb's Levels/Zones/... tabs) is drawn where the window was,
    over the chart."""
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    page.screenshot(path=path, clip=clip, full_page=True)


def tab(page, name: str) -> None:
    page.get_by_role("tab", name=name).click()
    page.wait_for_timeout(600)
    settle(page)


@contextlib.contextmanager
def full_view():
    """The demo character's full view, as its player sees it: the same
    history as the docs site's demo (dev/seed.py demo_rows, last evening
    yesterday), served by `wow-stats ui` over tests/degraded.py's
    VictoriaLogs stand-in, as the browser tests do. Yields its address."""
    import threading

    for p in (ROOT, ROOT / "dev"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import seed

    from tests import degraded as D
    from wowstats import ui

    tmp = Path(tempfile.mkdtemp())
    # demo=true as /ingest stores it (a string): the DEMO DATA banner.
    vl = D.VL([{**r, "demo": "true"} for r in seed.demo_rows()])
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp / "a"),
           "MODEL3D_CACHE_DIR": str(tmp / "c")}
    app = ui.App(env)
    app.port = 0
    srv = ui.serve(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/?char={D.CHAR}"
    finally:
        srv.shutdown()
        srv.server_close()
        vl.close()


def dungeons_full(page, url: str, path: Path) -> None:
    """The Dungeons panel down to the first card's runs, opened: what a
    trip is worth with the quests turned in outside, and who was in each
    run's party."""
    page.goto(url)
    page.click("#tab-overview")
    page.wait_for_selector("#dungeons .dghist", timeout=60000)
    runs = page.locator("#dungeons .dghist").first
    runs.locator("summary").click()
    page.wait_for_timeout(300)
    top = page.locator("#dungeons").first.bounding_box()["y"]
    end = runs.bounding_box()
    section(page, "#dungeons", path, round(end["y"] + end["height"] - top + 10))


def main(base: str) -> None:
    base = base.rstrip("/") + "/"
    tmp = Path(tempfile.mkdtemp())
    shots: dict[str, Path] = {}

    def shot(name: str) -> Path:
        shots[name] = tmp / f"{name}.png"
        return shots[name]

    with full_view() as full, sync_playwright() as p:
        # PW_CHROMIUM: a Chromium already on disk, when Playwright wants another build.
        browser = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for scheme in ("light", "dark"):
            suffix = "" if scheme == "light" else "-dark"
            page = browser.new_page(viewport={"width": WIDTH, "height": 820}, color_scheme=scheme)
            page.goto(base + "demo/friends/")
            page.wait_for_url("**/zogzog/**")
            settle(page)
            page.screenshot(path=shot(f"overview{suffix}"))
            if scheme == "light":
                # The link preview: 1200x630, the Overview's top.
                page.set_viewport_size({"width": 1200, "height": 630})
                settle(page)
                page.screenshot(path=tmp / "social.png")
                page.set_viewport_size({"width": WIDTH, "height": 820})
            section(page, page.get_by_role("region", name="Next moves"), shot(f"next-moves{suffix}"), 760)
            tab(page, "Character")
            section(page, "#gear", shot(f"gear{suffix}"), 900)
            page.locator(".adv-h", has_text="Upgrades").first.scroll_into_view_if_needed()
            settle(page)
            top = page.locator(".adv-h", has_text="Upgrades").first.bounding_box()
            gear = page.locator("#gear").first.bounding_box()
            y = top["y"] - 16 + page.evaluate("scrollY")
            clip = {"x": gear["x"], "y": y, "width": gear["width"], "height": 900}
            full_page_clip(page, shot(f"upgrades{suffix}"), clip)
            section(page, "#levels", shot(f"climb{suffix}"), 760)
            page.locator("#gear").get_by_role("button", name="View in 3D").first.click()
            page.wait_for_timeout(5000)
            # The model whole, its head too: the gear panel's top at the window's.
            page.evaluate("document.querySelector('#gear').scrollIntoView({block: 'start'})")
            page.wait_for_timeout(1000)
            page.screenshot(path=shot(f"model3d{suffix}"))
            page.keyboard.press("Escape")
            tab(page, "Timeline")
            page.set_viewport_size({"width": WIDTH, "height": 1040})  # the model under the gear list
            page.wait_for_timeout(4000)
            page.screenshot(path=shot(f"timeline{suffix}"))
            page.set_viewport_size({"width": WIDTH, "height": 820})
            tab(page, "Journal")
            page.screenshot(path=shot(f"journal{suffix}"))
            dungeons_full(page, full, shot(f"dungeons{suffix}"))
            page.close()
        browser.close()

    OUT.mkdir(parents=True, exist_ok=True)
    # sharp is already the site's dependency (Astro's image service).
    script = """
      import sharp from "sharp";
      const [src, dst] = process.argv.slice(1);
      await sharp(src).resize({ width: 1100, withoutEnlargement: true }).webp({ quality: 78 }).toFile(dst);
    """
    social = """
      import sharp from "sharp";
      const [src, dst] = process.argv.slice(1);
      await sharp(src).png({ palette: true, quality: 85, compressionLevel: 9 }).toFile(dst);
    """
    subprocess.run(["node", "--input-type=module", "-e", social, str(tmp / "social.png"), str(OUT / "social.png")],
                   cwd=ROOT / "site", check=True)
    for name, png in shots.items():
        dst = OUT / f"{name}.webp"
        subprocess.run(["node", "--input-type=module", "-e", script, str(png), str(dst)], cwd=ROOT / "site", check=True)
        print(f"{dst.relative_to(ROOT)}  {dst.stat().st_size // 1024} KiB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4399/wow-mcp/")
