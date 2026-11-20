"""docs/images/admin.webp: the admin overview of the demo lab (demo/lab), as
its owner sees it. The lab's people are invented: the owner `owner` and the
friends `frostbolt` and `moonfire` (demo/stack/up.sh); never a real install.

    demo/lab admin-shot     # with the lab up, its friends sent and published

<owner proxy address> is an identity proxy logged in as the lab's owner
(demo/lab starts one for the shot)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "images" / "admin.webp"


def main(base: str) -> int:
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        png = Path(tmp) / "admin.png"
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
        page.goto(base.rstrip("/") + "/admin")
        page.wait_for_selector("main table")
        page.wait_for_load_state("networkidle")
        page.locator("main").first.screenshot(path=str(png))
        browser.close()
        # sharp: the site's dependency, as site/scripts/screenshots.py uses it.
        script = """
          import sharp from "sharp";
          const [src, dst] = process.argv.slice(1);
          await sharp(src).resize({ width: 1100, withoutEnlargement: true }).webp({ quality: 78 }).toFile(dst);
        """
        subprocess.run(["node", "--input-type=module", "-e", script, str(png), str(OUT)], cwd=ROOT / "site", check=True)
    print(f"{OUT.relative_to(ROOT)}  {OUT.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
