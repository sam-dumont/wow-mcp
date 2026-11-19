"""Wowhead's viewer, boxed in, in a real browser (pre-publication review M2).

A gnome priest holding a staff, drawn by the real viewer in the full view
and in the sealed /zogzog/ bundle, in Chromium and WebKit. Then, from inside
the 3D frame (where Wowhead's script runs), what a hostile viewer build
would try: read the page around it, the cookies and storage, the key in the
address, call /api/* or the sealed files, ask the page for a file off the
relay's list. Before the fix the frame was same-origin and each of these
worked.

Opt-in (WOW_STATS_BROWSER=1): the network (wago.tools, wow.zamimg.com) and
Playwright's Chromium and WebKit. WOW_STATS_BROWSER_CACHE keeps the upstream
files between runs.
"""

import io
import json
import os
import secrets
import threading
import time
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.conftest import Running
from tests.test_browser import Catalog
from tests.test_model3d_browser import LookStore
from tests.test_public_view import KINDS, poison_row
from wowstats import itemcard, model3d, publish, static, stores, ui

pytestmark = pytest.mark.skipif(os.environ.get("WOW_STATS_BROWSER") != "1",
                                reason="opt-in: WOW_STATS_BROWSER=1 (network and a browser)")

# The Bent Staff (item 35, two-handed) next to the shoulders: a weapon goes
# through the viewer's item path (meta/item/<display>.json), armour through
# meta/armor/<slot>/.
GEAR = [{"slot": 3, "slot_name": "Shoulder", "item_id": 5820, "name": "Faerie Mantle", "quality": 2, "item_level": 32},
        {"slot": 16, "slot_name": "Main Hand", "item_id": 35, "name": "Bent Staff", "quality": 1, "item_level": 2}]


class ArmedCatalog(Catalog):
    def card(self, item_id):
        return {**self.many([item_id])[str(item_id)], "s": "Two-Hand" if item_id == 35 else "Shoulder"}


class ArmedStore(LookStore):
    def vl(self, query, keys=(), ops=None, limit=0):
        return [{**poison_row(i, k), "items": json.dumps(GEAR)} for i, k in enumerate(KINDS)]


# Paths the page may fetch for the frame, and some it must refuse
# (wowstats.model3d.RELAY is the server's copy of the list).
PATHS = ["viewer/viewer.min.js", "meta/character/13.json", "meta/armor/3/9.json", "meta/item/7.json",
         "textures/5.webp", "m2/1.m2", "skin/1-2.skin", "anim/1-0-0.anim", "m2/1.skin", "viewer/x.js",
         "../api/me", "meta/character/../../x.json", "textures/1.js",
         "sd/meta/character/13.json", "sd/meta/charactercustomization/13.json", "sd/m2/1.m2", "sd/meta/item/7.json"]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sandbox")
    mp = pytest.MonkeyPatch()

    def store(vl, archive="", vm="", *a, **k):
        s = ArmedStore()
        s.vm_url = vm
        return s

    mp.setattr(ui, "Store", store)
    mp.setattr(stores, "Store", store)  # the dungeon plan reads the logs with its own Store (plan.facts.store_for)
    mp.setattr(itemcard, "shared", lambda: ArmedCatalog())
    cache = Path(os.environ.get("WOW_STATS_BROWSER_CACHE") or tmp / "cache")
    key = tmp / "zogzog.key"
    key.write_text(publish.b64u(secrets.token_bytes(32)) + "\n")  # a throwaway key
    env = {"VL_URL": "http://vl.invalid", "VM_URL": "http://vm.invalid", "MODEL3D": "on",
           "MODEL3D_CACHE_DIR": str(cache), "PUBLISH_DIR": str(tmp / "site"), "ZOGZOG_PATH": "/zogzog/",
           "ZOGZOG_KEY_FILE": str(key)}
    with redirect_stdout(io.StringIO()):
        res = publish.publish(env)
    assert res["built"], res
    s = static.Site({"SITE_DIR": str(tmp / "site"), "ZOGZOG_PATH": "/zogzog/"})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    full = Running(ui.App({**env, "VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "MCP_PUBLIC_URL": ""}))
    # The paperdoll is on the overview tab (the full view opens on Home).
    yield {"full": full.base + "/?tab=overview",
           "zogzog": f"http://127.0.0.1:{httpd.server_address[1]}/zogzog/?tab=overview#{key.read_text().strip()}"}
    full.close()
    httpd.shutdown()
    mp.undo()


ATTACKS = """async () => {
  const out = {};
  try { out.parent = String(window.parent.document.title); } catch (e) { out.parent = 'blocked'; }
  try { out.cookie = document.cookie; out.cookie = 'readable'; } catch (e) { out.cookie = 'blocked'; }
  try { out.storage = String(localStorage.length); } catch (e) { out.storage = 'blocked'; }
  out.hash = location.hash;
  out.origin = self.origin;
  for (const u of ['/api/chars', '/api/me', 'data/chars.json.enc', '../data/chars.json.enc', '/']) {
    out['fetch ' + u] = await fetch(u).then((r) => 'reached ' + r.status, () => 'blocked');
  }
  const ask = (path) => new Promise((resolve) => {
    const id = 1e9 + Math.floor(Math.random() * 1e6);
    const on = (ev) => {
      if (!ev.data || ev.data.id !== id) return;
      removeEventListener('message', on);
      resolve(ev.data.status + ':' + (ev.data.body ? ev.data.body.byteLength : 0));
    };
    addEventListener('message', on);
    parent.postMessage({ wowM3d: 'get', id, path }, '*');
    setTimeout(() => resolve('no answer'), 5000);
  });
  for (const p of ['../api/chars', '../data/chars.json.enc', 'zam/r2/../../api/me', 'model3d.js']) {
    out['ask ' + p] = await ask(p);
  }
  return out;
}"""


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("view", ["full", "zogzog"])
def test_the_viewer_draws_and_cannot_get_out(site, engine, view):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    asked, bad, csp = [], [], []
    with sync_playwright() as p:
        b = getattr(p, engine).launch()
        page = b.new_page(viewport={"width": 1280, "height": 900})
        page.on("response", lambda r: (asked.append(r.url) if "/model3d/" in r.url else None,
                                       bad.append((r.status, r.url)) if r.status >= 400 and "/model3d/zam/" in r.url
                                       else None))
        logs = []
        page.on("console", lambda m: (logs.append(m.text[:300]), csp.append(m.text) if "Content Security Policy" in m.text
                                      or "Refused" in m.text else None))
        page.on("pageerror", lambda e: logs.append("pageerror: " + str(e)[:300]))
        page.goto(site[view], wait_until="networkidle")
        btn = page.locator("#gear button.m3dtoggle").first
        btn.wait_for(timeout=30000)
        btn.scroll_into_view_if_needed()
        btn.click()
        frame = None
        deadline = time.time() + 90
        drawn = ""
        while time.time() < deadline:
            # By the element: Playwright gives a sandboxed (opaque) frame an empty url.
            el = page.query_selector("iframe.m3dframe")
            frame = el.content_frame() if el else None
            if frame is not None:
                drawn = frame.evaluate("() => { const s = document.getElementById('status');"
                                       " if (s && s.classList.contains('err')) return 'error: ' + s.textContent;"
                                       " return s && s.hidden && document.querySelector('#stage canvas') ? 'drawn' : '' }")
                if drawn:
                    break
            time.sleep(0.5)
        if drawn != "drawn" and frame is not None:
            drawn += " | status: " + frame.evaluate("() => (document.getElementById('status') || {}).textContent")
            drawn += " | canvas: " + str(frame.evaluate("() => Boolean(document.querySelector('#stage canvas'))"))
        assert drawn == "drawn", (drawn, logs[-15:], asked[-8:], bad)
        page.wait_for_load_state("networkidle")
        time.sleep(2)
        sandbox = page.locator("iframe.m3dframe").first.get_attribute("sandbox")
        src = page.locator("iframe.m3dframe").first.get_attribute("src")
        drawing = list(csp)  # before the attacks: drawing needed nothing the policy refuses
        got = frame.evaluate(ATTACKS)
        relay = page.evaluate(f"() => {json.dumps(PATHS)}.map((p) => window.wowRelayPath('zam/{model3d.ZAM_REV}/' + p))")
        b.close()
    zam = f"/model3d/zam/{model3d.ZAM_REV}/"
    assert any(zam + "m2/" in u for u in asked), "the viewer drew no model"
    assert any(zam + "meta/item/" in u for u in asked), "the staff wasn't asked for"
    assert not bad, bad
    assert sandbox == "allow-scripts" and "#" not in src
    assert got["parent"] == "blocked" and got["cookie"] == "blocked" and got["storage"] == "blocked", got
    assert got["hash"] == "" and got["origin"] == "null", got
    for k, v in got.items():
        if k.startswith("fetch "):
            assert v == "blocked", (k, v)
        if k.startswith("ask "):
            assert v == "404:0", (k, v)
    assert relay == [bool(model3d.RELAY.match(p)) for p in PATHS], "web/model3d/model3d.js RELAY != wowstats.model3d"
    assert not drawing, drawing
