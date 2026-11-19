"""`wow-stats render`: a past level's picture, drawn by Wowhead's model viewer
in a headless Chromium (docs/architecture.md, "A past level's picture").

A process, and on Kubernetes a pod, of its own, because the viewer is Wowhead's
script and Chromium runs it without its sandbox (an unprivileged container
has no user namespaces for it). So this side holds nothing: no key, no store,
no character name, no account. It gets what a drawing needs and answers an
image:

  POST /render  {"model": {"chr_model": 13, "gender": 0,
                           "options": [{"optionId": 81, "choiceId": 1186}, ...]},
                 "items": [[inventory_type, display_id], ...],
                 "metas": {"<display_id>": {...}}}   (optional)
                -> 200 image/webp (transparent around the character)
                   400 not a render request, 502 Wowhead's files didn't come,
                   503 busy (one render at a time), 504 the model didn't finish
  GET /healthz  -> 200 once Chromium runs

`metas` are the metas Wowhead lacks for the items drawn (a weapon or armor
Forever added: wowstats.model3d.Model3D.stand_in_meta needs Forever's
tables, which this side doesn't read), served in place of Wowhead's for that
one drawing.

The model id, the look and the display ids are the publisher's work
(wowstats.model3d, wowstats.pictures): the request names no item, no level
and no one. Chromium only ever talks to this process (a page server on
127.0.0.1 with the same view page and relay the 3D view uses); every other
request it would make is refused before it leaves (`page.route`), and the
relay fetches from wow.zamimg.com alone, through its allow-list of paths.

POST /render needs `Authorization: Bearer <token>`: RENDER_TOKEN_FILE, or
when unset a token made on first start in RENDER_CACHE_DIR/render.token
(its path logged). The publisher sends it from its own RENDER_TOKEN_FILE.

Environment: BIND (0.0.0.0), PORT (8083), RENDER_TOKEN_FILE, RENDER_CACHE_DIR (<tmp>/wow-stats-render:
Wowhead's files), RENDER_CACHE_MB (256), RENDER_TIMEOUT_S (90), MODEL3D_ZAM_URL (tests).
Needs the `render` extra (Playwright) and its Chromium: the image's renderer target.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wowstats import bearer, httpserve
from wowstats import model3d as m3d

# The picture: a 3:4 portrait, drawn at twice its CSS size (a phone's stage
# is ~350 px wide at 2x or 3x; the desktop's ~360). Changing any of these
# changes every picture: bump REV (wowstats.pictures keys its cache on it).
WIDTH, HEIGHT, SCALE = 360, 480, 2
REV = 1
MAX_BODY = 16 * 1024
# The viewer's model ids: every race and sex Forever has (a Skyborne comes
# as the Blood Elf stand-in, model3d.STAND_INS), nothing else.
MODELS = frozenset({m for pair in m3d.CHR_MODELS.values() for m in pair} - set(m3d.STAND_INS)
                   | {v[0] for v in m3d.STAND_INS.values()})
DRAWN = m3d.ARMOR | {21, 22}  # armour slots, then the two hands (model3d.HAND)
# After the last of Wowhead's files arrived, the viewer still builds the
# model and decodes its textures: this long with nothing asked means drawn.
QUIET_S = 1.5


class BadRequest(ValueError):
    pass


class Busy(Exception):
    pass


def _int(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise BadRequest(f"{what}: an integer from {lo} to {hi}")
    return v


def parse(body: bytes) -> dict:
    """A render request, checked field by field and rebuilt: anything not
    listed in the module's docstring is refused or dropped."""
    try:
        d = json.loads(body)
    except ValueError:
        raise BadRequest("not JSON") from None
    if not isinstance(d, dict) or not isinstance(d.get("model"), dict) or not isinstance(d.get("items"), list):
        raise BadRequest("model and items are required")
    m = d["model"]
    model = _int(m.get("chr_model"), 1, 10**4, "chr_model")
    if model not in MODELS:
        raise BadRequest("chr_model: not a playable model")
    options = m.get("options")
    if not isinstance(options, list) or len(options) > 40:
        raise BadRequest("options: a list of at most 40")
    opts = []
    for o in options:
        if not isinstance(o, dict):
            raise BadRequest("options: {optionId, choiceId}")
        opts.append({"optionId": _int(o.get("optionId"), 1, 10**7, "optionId"),
                     "choiceId": _int(o.get("choiceId"), 1, 10**7, "choiceId")})
    if len(d["items"]) > 20:
        raise BadRequest("items: at most 20")
    items = []
    for it in d["items"]:
        if not isinstance(it, list) or len(it) != 2:
            raise BadRequest("items: [inventory_type, display_id] pairs")
        inv = _int(it[0], 1, 30, "inventory_type")
        if inv not in m3d.ARMOR and inv not in m3d.HELD:
            raise BadRequest("inventory_type: not a drawn slot")
        items.append([inv, _int(it[1], 1, 10**7, "display_id")])
    out = {"model": {"chr_model": model, "gender": _int(m.get("gender"), 0, 1, "gender"), "options": opts},
           "items": items}
    metas = d.get("metas")
    if metas is not None:
        drawn = {str(disp) for inv, disp in items}
        if not isinstance(metas, dict) or not set(metas) <= drawn:
            raise BadRequest("metas: {display_id: meta} for the items drawn")
        if any(not isinstance(v, dict) for v in metas.values()):
            raise BadRequest("metas: each one a JSON object")
        out["metas"] = {k: json.dumps(v, separators=(",", ":")).encode() for k, v in metas.items()}
    return out


class Pages:
    """The page server Chromium loads: the 3D view's own page, its scripts,
    the relay, and the request being drawn. 127.0.0.1 only."""

    def __init__(self, relay: m3d.Relay, static: Path):
        self.relay, self.static = relay, static
        self.jobs: dict[str, dict] = {}
        # The drawing's own metas (parse: "metas"), by display id: one
        # drawing at a time.
        self.metas: dict[str, bytes] = {}
        self.lock = threading.Lock()
        self.inflight = 0
        self.last = 0.0
        self.models = 0
        self.errors: list[str] = []
        pages = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                pages.serve(self)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="render-pages").start()

    def reset(self) -> None:
        with self.lock:
            self.inflight, self.last, self.models, self.errors = 0, time.time(), 0, []

    def serve(self, h: BaseHTTPRequestHandler) -> None:
        path = urllib.parse.urlsplit(h.path).path
        body, ctype, csp = None, "application/octet-stream", None
        if path == "/model3d/view":
            body, ctype = m3d.view_page(self.static, render=True), "text/html; charset=utf-8"
            csp = m3d.view_csp(body, render=True)
        elif path.startswith("/model3d/") and path[len("/model3d/"):] in m3d.OWN:
            name = path[len("/model3d/"):]
            body, ctype = (self.static / name).read_bytes(), m3d.OWN[name]
        elif path.startswith("/data/model3d/") and path.endswith(".json"):
            job = self.jobs.get(path[len("/data/model3d/"):-len(".json")])
            if job is not None:
                body, ctype = json.dumps(job).encode(), "application/json"
        elif path.startswith(f"/model3d/zam/{m3d.ZAM_REV}/"):
            rel = path[len(f"/model3d/zam/{m3d.ZAM_REV}/"):]
            with self.lock:
                self.inflight += 1
                self.models += rel.startswith("m2/")
            own = m3d.ITEM_META.match(rel) or m3d.ARMOR_META.match(rel)
            try:
                body = self.metas.get(own.groups()[-1]) if own else None
                if body is None:
                    body = self.relay.get(rel)
            except m3d.Upstream as e:
                with self.lock:
                    self.errors.append(str(e))
            finally:
                with self.lock:
                    self.inflight -= 1
                    self.last = time.time()
            ctype = m3d.TYPES.get(os.path.splitext(rel)[1], ctype)
        if body is None:
            h.send_response(404)
            h.send_header("Content-Length", "0")
            h.end_headers()
            return
        h.send_response(200)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        if csp:
            h.send_header("Content-Security-Policy", csp)
        h.end_headers()
        h.wfile.write(body)

    def quiet(self) -> bool:
        with self.lock:
            return self.models > 0 and self.inflight == 0 and time.time() - self.last >= QUIET_S


class Renderer:
    """Chromium, started once and kept, driven from one thread (Playwright's
    sync API stays on the thread that started it): one render at a time."""

    def __init__(self, env: dict):
        cache = cache_dir(env)
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            cache = None
        relay = m3d.Relay(env.get("MODEL3D_ZAM_URL", m3d.ZAM_URL), cache, int(env.get("RENDER_CACHE_MB", "256")),
                          env.get("MODEL3D_VIEWER_SHA256", ""))
        from wowstats import ui

        self.pages = Pages(relay, ui.static_dir() / "model3d")
        self.timeout = float(env.get("RENDER_TIMEOUT_S", "90"))
        self.worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="chromium")
        self.busy = threading.Lock()
        self.pw = self.browser = None
        self.blocked: list[str] = []  # every request Chromium tried to send anywhere else
        self.worker.submit(self._start).result()

    def _start(self) -> None:
        from playwright.sync_api import sync_playwright

        if self.pw is None:
            self.pw = sync_playwright().start()
        # Software WebGL (SwiftShader through ANGLE): no GPU in the pod, and
        # the same pixels on any machine. Playwright passes --no-sandbox.
        self.browser = self.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                                                     "--disable-gpu-compositing", "--mute-audio"])

    def alive(self) -> bool:
        return self.browser is not None and self.browser.is_connected()

    def render(self, req: dict) -> bytes:
        if not self.busy.acquire(blocking=False):
            raise Busy
        try:
            return self.worker.submit(self._render, req).result(timeout=self.timeout + 30)
        finally:
            self.busy.release()

    def _render(self, req: dict) -> bytes:
        if not self.alive():
            self._start()
        token = secrets.token_hex(8)
        p = self.pages
        refused = len(self.blocked)
        p.jobs[token] = {"model": req["model"],
                         "items": [{"inventory_type": i, "display_id": d} for i, d in req["items"]]}
        p.metas = dict(req.get("metas") or {})
        p.reset()
        ctx = self.browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=SCALE)
        try:
            page = ctx.new_page()

            def guard(route):
                if route.request.url.startswith(p.base + "/"):
                    return route.continue_()
                self.blocked.append(urllib.parse.urlsplit(route.request.url).hostname or route.request.url[:40])
                return route.abort("blockedbyclient")

            page.route("**/*", guard)
            page.goto(f"{p.base}/model3d/view?static=1&render=1&name={token}", wait_until="load")
            deadline = time.time() + self.timeout
            while not p.quiet():
                if p.errors:
                    raise m3d.Upstream(p.errors[0])
                err = page.evaluate("() => { const s = document.getElementById('status');"
                                    " return s && s.classList.contains('err') ? s.textContent : '' }")
                if err:
                    raise m3d.Upstream(err)
                if time.time() > deadline:
                    raise TimeoutError("the model didn't finish")
                page.wait_for_timeout(200)
            # Two frames more: the last texture's upload reaches the canvas.
            page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            png = page.screenshot(type="png", omit_background=True)
            return self._webp(page, png)
        finally:
            p.jobs.pop(token, None)
            p.metas = {}
            ctx.close()
            if len(self.blocked) > refused:  # never seen: the viewer loads everything through the relay
                sys.stderr.write(f"render: Chromium was refused {sorted(set(self.blocked[refused:]))}\n")

    @staticmethod
    def _webp(page, png: bytes) -> bytes:
        """The screenshot as WebP with its alpha (Chromium's encoder; the
        image has no other library): a fifth of the PNG's size."""
        import base64

        b64 = page.evaluate("""async (src) => {
          const img = new Image(); img.src = src; await img.decode();
          const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
          c.getContext('2d').drawImage(img, 0, 0);
          const blob = await new Promise((r) => c.toBlob(r, 'image/webp', 0.9));
          const buf = new Uint8Array(await blob.arrayBuffer());
          let s = '';
          for (let i = 0; i < buf.length; i += 0x8000) {
            s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
          }
          return btoa(s);
        }""", "data:image/png;base64," + base64.b64encode(png).decode())
        out = base64.b64decode(b64)
        if out[:4] != b"RIFF" or out[8:12] != b"WEBP":
            raise RuntimeError("Chromium didn't encode WebP")
        return out


def make_handler(r: Renderer, token: str):
    """POST /render needs `Authorization: Bearer <token>` (RENDER_TOKEN_FILE,
    wowstats.bearer); /healthz needs nothing."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "wow-stats-render"
        sys_version = ""

        def log_message(self, fmt, *args):
            sys.stderr.write("render: " + (fmt % args) + "\n")

        def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                return self._send(200 if r.alive() else 503, b"ok\n" if r.alive() else b"no browser\n")
            return self._send(404, b"not found\n")

        def do_POST(self):  # noqa: N802
            if self.path != "/render":
                return self._send(404, b"not found\n")
            if not bearer.allowed(self.headers, token):
                return self._send(401, b"send the renderer's token (RENDER_TOKEN_FILE)\n")
            try:
                n = int(self.headers.get("Content-Length") or "-1")
            except ValueError:
                n = -1
            if not 0 < n <= MAX_BODY:
                return self._send(400, b"a JSON body of at most 16 KiB\n")
            try:
                req = parse(self.rfile.read(n))
            except BadRequest as e:
                return self._send(400, f"{e}\n".encode())
            t0 = time.time()
            try:
                img = r.render(req)
            except Busy:
                return self._send(503, b"busy: one render at a time\n")
            except m3d.Upstream as e:
                return self._send(502, f"{e}\n".encode())
            except (TimeoutError, concurrent.futures.TimeoutError):
                return self._send(504, b"the model didn't finish\n")
            sys.stderr.write(f"render: {len(req['items'])} items, {len(img) // 1024} KiB, "
                             f"{time.time() - t0:.1f} s\n")
            return self._send(200, img, "image/webp")

    return Handler


def cache_dir(env: dict) -> Path:
    return Path(env.get("RENDER_CACHE_DIR") or Path(tempfile.gettempdir()) / "wow-stats-render")


def serve(r, env: dict) -> httpserve.Server:
    """The /render listener: its token (RENDER_TOKEN_FILE, else one made in
    the cache folder), timeouts and a connection cap (wowstats.httpserve)."""
    token = bearer.server_token(env, "RENDER_TOKEN_FILE", cache_dir(env) / "render.token")
    return httpserve.Server((env.get("BIND", "0.0.0.0"), int(env.get("PORT", "8083"))), make_handler(r, token))


def main() -> int:
    env = dict(os.environ)
    r = Renderer(env)
    httpd = serve(r, env)
    print(f"render: listening on {httpd.server_address[0]}:{httpd.server_address[1]}", flush=True)
    httpd.serve_forever()
    return 0
