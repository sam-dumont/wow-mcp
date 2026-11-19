"""The real 3D viewer on a real static bundle: every file it asks for is in
the bundle.

Opt-in (WOW_STATS_BROWSER=1): it publishes through the real upstreams
(wago.tools for Forever's item tables, wow.zamimg.com for Wowhead's viewer
and files), serves the build with `wow-stats static` and drives Chromium
headless through "Show in 3D". It fails on any 4xx under /model3d/ and on
the static server's "missing 3D file" line. Needs the network and
Playwright's Chromium (`uv run --group browser playwright install
chromium`). WOW_STATS_BROWSER_CACHE keeps the upstream files between runs.

Why: v0.2.1's live bundle answered 404 to m2/119159.m2 for browsers that
had cached v0.2.0's classic character meta under the same URL, and drew a
broken head (wowstats.model3d ZAM_REV); a local build looked fine.
"""

import io
import os
import threading
import time
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.test_browser import Catalog
from tests.test_public_view import PoisonStore
from wowstats import itemcard, model3d, publish, static, ui

pytestmark = pytest.mark.skipif(os.environ.get("WOW_STATS_BROWSER") != "1",
                                reason="opt-in: WOW_STATS_BROWSER=1 (network and a browser)")

# Testpriest's look as Battle.net gives it (Forever's modern customization ids):
# face, skin, hair style, hair colour, facial hair. A gnome, male.
LOOK = {"81": "1186", "80": "1174", "82": "1210", "83": "1220", "84": "1233"}


class LookStore(PoisonStore):
    def vm_query(self, q):
        if "wow_character_customization" in q:
            return [{"metric": {"option_id": k}, "value": [1, v]} for k, v in LOOK.items()]
        if "wow_character_profile_refreshed" in q:
            return [{"metric": {}, "value": [1, str(int(time.time()))]}]
        if "wow_character_profile{" in q:
            return [{"metric": {"gender": "male", "race_id": "7"}, "value": [1, "32"]}]
        return super().vm_query(q)


@pytest.fixture
def site(tmp_path, monkeypatch):
    def store(vl, archive="", vm="", *a, **k):
        s = LookStore()
        s.vm_url = vm
        return s

    monkeypatch.setattr(ui, "Store", store)
    # Item cards and icons as in tests/test_browser.py. The conftest turns the
    # catalog off, and then the bundle has no question-mark icon for the page
    # to ask for. The 3D viewer reads its own wago.tools tables (model3d).
    monkeypatch.setattr(itemcard, "shared", lambda: Catalog())
    cache = Path(os.environ.get("WOW_STATS_BROWSER_CACHE") or tmp_path / "cache")
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "on",
           "MODEL3D_CACHE_DIR": str(cache), "PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_PATH": ""}
    with redirect_stdout(io.StringIO()):
        res = publish.publish(env)
    assert res["built"] and res["files_3d"] > 10, res
    s = static.Site({"SITE_DIR": str(tmp_path / "site"), "ZOGZOG_PATH": ""})
    log = io.StringIO()
    handler = static.make_handler(s)

    class Logged(handler):
        def log_message(self, fmt, *args):
            with redirect_stdout(log):
                super().log_message(fmt, *args)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Logged)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", log
    httpd.shutdown()


@pytest.mark.parametrize("width,height,dpr", [(1280, 900, 1), (390, 844, 3)])
def test_viewer_finds_every_file_in_the_bundle(site, width, height, dpr):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    base, log = site
    bad, asked = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": width, "height": height}, device_scale_factor=dpr)
        ctx.on("response", lambda r: (asked.append(r.url) if "/model3d/" in r.url else None,
                                      bad.append((r.status, r.url)) if r.status >= 400 and "/model3d/" in r.url else None))
        page = ctx.new_page()
        page.goto(base + "/", wait_until="networkidle")
        btn = page.locator("#gear button.m3dtoggle").first
        btn.wait_for(timeout=20000)
        btn.scroll_into_view_if_needed()
        btn.click()
        frame = page.locator("iframe.m3dframe").first
        frame.wait_for(timeout=20000)
        page.wait_for_load_state("networkidle")
        time.sleep(3)
        b.close()
    zam = f"/model3d/zam/{model3d.ZAM_REV}/"
    assert any(zam + "m2/" in u for u in asked), "the viewer drew no model"
    assert not bad, bad
    assert "missing 3D file" not in log.getvalue(), log.getvalue()
