"""`wow-stats render`: a past level's picture, drawn by the 3D view's own
viewer (web/model3d/m3d) in a headless Chromium (docs/architecture.md, "A
past level's picture").

A process, and on Kubernetes a pod, of its own: Chromium runs without its
own sandbox in an unprivileged container (no user namespaces for it), so
this side holds nothing: no key, no store, no character name, no account.
It gets a recipe (wowstats.character3d, the same one the 3D view draws) and
the files it names, and answers an image:

  POST /render      {"recipe": {...}, "absent": [file id, ...], "scope": "<hex>"}
                    -> 200 image/webp (transparent around the character)
                       428 {"files": [file id, ...]}: the files it lacks;
                           send them (POST /file/<id>), then ask again
                       400 not a render request, 503 busy (one render at a
                       time), 504 the model didn't finish
  POST /file/<id>   one file the last 428 named, with X-Content-SHA256 (and
                    X-Render-Scope: the request's scope)
                    -> 204 kept, 400 wrong hash, 413 too large,
                       415 not a model, skin, skeleton, animation or texture
  GET  /healthz     -> 200 once Chromium runs

The files are what the recipe names plus what those models name (their
skin, skeleton, animations, textures: wowstats.assets.closure). `absent`
lists the ones the publisher doesn't have either: drawn without them, as the
3D view does. Files are kept by id under RENDER_CACHE_DIR/files/<scope>, at
most RENDER_CACHE_MB in all (the oldest used go first), so the next picture
of the same race costs no upload. `scope` is the person's, opaque here
(wowstats.pictures.scope): each person's files come from their own upload
(wowstats.assets checks a file's kind, not that it is the game's), so one
person's file never draws another's picture. No scope: one shared folder.

Chromium only ever talks to this process (a page server on 127.0.0.1 with
the 3D view's page, its files from the cache and the request being drawn);
every other request it would make is refused before it leaves
(`page.route`). This side needs no network but its own port.

POST needs `Authorization: Bearer <token>`: RENDER_TOKEN_FILE, or when unset
a token made on first start in RENDER_CACHE_DIR/render.token (its path
logged). The publisher sends it from its own RENDER_TOKEN_FILE.

Environment: BIND (0.0.0.0), PORT (8083), RENDER_TOKEN_FILE, RENDER_CACHE_DIR
(<tmp>/wow-stats-render), RENDER_CACHE_MB (256), RENDER_TIMEOUT_S (90).
Needs the `render` extra (Playwright) and its Chromium: the image's renderer target.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wowstats import assets, bearer, character3d, httpserve
from wowstats import model3d as m3d

# The picture: a 3:4 portrait, drawn at twice its CSS size (a phone's stage
# is ~350 px wide at 2x or 3x; the desktop's ~360). Changing any of these,
# or the viewer, changes every picture: bump REV (wowstats.pictures keys its
# cache on it). 2: our own viewer and recipes (2026-11-20).
WIDTH, HEIGHT, SCALE = 360, 480, 2
REV = 2
MAX_BODY = 64 * 1024
MAX_ASK = 400  # file ids one 428 names at most
FILE_PATH = re.compile(r"^/file/(\d{1,10})$")
SCOPE = re.compile(r"^[0-9a-f]{1,64}$")


class BadRequest(ValueError):
    pass


class Busy(Exception):
    pass


class Lacking(Exception):
    def __init__(self, files: list[int]):
        super().__init__(f"{len(files)} files missing")
        self.files = files


# ------------------------------------------------------------ the request

def _int(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise BadRequest(f"{what}: an integer from {lo} to {hi}")
    return v


def _fid(v, what: str) -> int:
    return _int(v, 1, 2**32 - 1, what)


def _list(v, most: int, what: str) -> list:
    if not isinstance(v, list) or len(v) > most:
        raise BadRequest(f"{what}: a list of at most {most}")
    return v


def _obj(v, what: str) -> dict:
    if not isinstance(v, dict):
        raise BadRequest(f"{what}: an object")
    return v


def _key(k, what: str) -> str:
    if not isinstance(k, str) or not k.isdigit() or len(k) > 6:
        raise BadRequest(f"{what}: keys are numbers")
    return k


def _textures(v, what: str) -> dict:
    out = {}
    for k, x in _obj(v, what).items():
        if isinstance(x, str):
            if not re.fullmatch(r"material:\d{1,3}", x):
                raise BadRequest(f"{what}: a file id or material:<type>")
            out[_key(k, what)] = x
        else:
            out[_key(k, what)] = _fid(x, what)
    if len(out) > 32:
        raise BadRequest(f"{what}: at most 32")
    return out


def _geosets(v, what: str) -> dict:
    g = _obj(v, what)
    s, gr = _obj(g.get("set", {}), what + ".set"), _obj(g.get("groups", {}), what + ".groups")
    if len(s) > 400 or len(gr) > 100:
        raise BadRequest(f"{what}: too many")
    for x in s.values():
        if not isinstance(x, bool):
            raise BadRequest(f"{what}.set: true or false")
    return {"set": {_key(k, what): x for k, x in s.items()},
            "groups": {_key(k, what): _int(x, 0, 99, what + ".groups") for k, x in gr.items()}}


def parse_recipe(r) -> dict:
    """A recipe (wowstats.character3d, v1), checked field by field and
    rebuilt: anything not in the format is refused or dropped."""
    r = _obj(r, "recipe")
    if r.get("v") != character3d.RECIPE_VERSION:
        raise BadRequest(f"recipe: version {character3d.RECIPE_VERSION}")
    mats = []
    for m in _list(r.get("materials", []), 16, "materials"):
        m = _obj(m, "material")
        layers = []
        for ly in _list(m.get("layers", []), 64, "layers"):
            ly = _obj(ly, "layer")
            layers.append({"file": _fid(ly.get("file"), "layer.file"),
                           **{k: _int(ly.get(k, 0), 0, 10**5, f"layer.{k}") for k in ("target", "x", "y", "w", "h")},
                           "blend": _int(ly.get("blend", 0), 0, 64, "layer.blend")})
        mats.append({"type": _int(m.get("type"), 0, 64, "material.type"),
                     "width": _int(m.get("width"), 1, 4096, "width"),
                     "height": _int(m.get("height"), 1, 4096, "height"),
                     "layers": layers})
    skinned = [{"model": _fid(_obj(s, "skinned").get("model"), "skinned.model"),
                "textures": _textures(s.get("textures", {}), "skinned.textures"),
                "geosets": _geosets(s.get("geosets", {}), "skinned.geosets")}
               for s in _list(r.get("skinned", []), 16, "skinned")]
    attachments = []
    for a in _list(r.get("attachments", []), 32, "attachments"):
        a = _obj(a, "attachment")
        scale = a.get("scale", 1.0)
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0 < scale <= 10:
            raise BadRequest("attachment.scale: a number from 0 to 10")
        attachments.append({
            "slot": _int(a.get("slot", 0), 0, 30, "attachment.slot"), "attach": _int(a.get("attach"), 0, 100, "attach"),
            "model": _fid(a.get("model"), "attachment.model"), "textures": _textures(a.get("textures", {}), "textures"),
            "visuals": [{"model": _fid(_obj(v, "visual").get("model"), "visual.model"),
                         "attach": _int(v.get("attach", 0), 0, 100, "visual.attach")}
                        for v in _list(a.get("visuals", []), 8, "visuals")],
            "scale": float(scale)})
    collections = [{"slot": _int(_obj(c, "collection").get("slot", 0), 0, 30, "collection.slot"),
                    "model": _fid(c.get("model"), "collection.model"),
                    "textures": _textures(c.get("textures", {}), "collection.textures"),
                    "geosets": _geosets(c.get("geosets", {}), "collection.geosets")}
                   for c in _list(r.get("collections", []), 32, "collections")]
    return character3d.standing_recipe({"v": character3d.RECIPE_VERSION, "model": _fid(r.get("model"), "model"),
            "geosets": _geosets(r.get("geosets", {}), "geosets"), "materials": mats,
            "textures": _textures(r.get("textures", {}), "textures"), "skinned": skinned,
            "attachments": attachments, "collections": collections,
            "animation": _int(r.get("animation", 0), 0, 2000, "animation"), "ranged": r.get("ranged")})


def parse_scope(v) -> str:
    """Whose files a request reads: "" (the shared folder) or the
    publisher's opaque hex for one person."""
    if v in (None, ""):
        return ""
    if not isinstance(v, str) or not SCOPE.match(v):
        raise BadRequest("scope: up to 64 lowercase hex digits")
    return v


def parse(body: bytes) -> dict:
    """A render request: {"recipe", "absent", "scope"}."""
    try:
        d = json.loads(body)
    except ValueError:
        raise BadRequest("not JSON") from None
    d = _obj(d, "the request")
    absent = {_fid(x, "absent") for x in _list(d.get("absent", []), 4000, "absent")}
    return {"recipe": parse_recipe(d.get("recipe")), "absent": absent, "scope": parse_scope(d.get("scope"))}


# ------------------------------------------------------------ the files

class Files:
    """Files by id on disk, one folder per scope (a person's; "" the shared
    one), at most `budget` bytes in all (the oldest used go first)."""

    def __init__(self, root: Path | None, budget: int):
        self.root = root / "files" if root else Path(tempfile.mkdtemp(prefix="wow-stats-render-files-"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.lock = threading.Lock()

    def path(self, fdid: int, scope: str = "") -> Path:
        return (self.root / parse_scope(scope) if scope else self.root) / str(int(fdid))

    def get(self, fdid: int, scope: str = "") -> bytes | None:
        p = self.path(fdid, scope)
        try:
            body = p.read_bytes()
            os.utime(p)  # used: the last to go
            return body
        except OSError:
            return None

    def has(self, fdid: int, scope: str = "") -> bool:
        return self.path(fdid, scope).is_file()

    def put(self, fdid: int, body: bytes, scope: str = "") -> None:
        with self.lock:
            p = self.path(fdid, scope)
            p.parent.mkdir(exist_ok=True)
            tmp = p.with_name(f".{p.name}.{secrets.token_hex(4)}")
            tmp.write_bytes(body)
            os.replace(tmp, p)
            every = [*self.root.iterdir(), *(f for d in self.root.iterdir() if d.is_dir() for f in d.iterdir())]
            files = sorted((f for f in every if f.is_file() and not f.name.startswith(".")),
                           key=lambda f: f.stat().st_mtime)
            used = sum(f.stat().st_size for f in files)
            for f in files:
                if used <= self.budget:
                    break
                if f == p:
                    continue
                used -= f.stat().st_size
                f.unlink(missing_ok=True)

    def lacking(self, recipe: dict, absent: set[int], scope: str = "") -> list[int]:
        """The files the recipe draws with that aren't in the scope's folder,
        and aren't known to be absent (assets.closure over what is there)."""
        # A picture stays on Stand: never the .anim files of the moves.
        _have, missing = assets.closure(character3d.needs(recipe),
                                        lambda f: self.get(f, scope) if self.has(f, scope) else None,
                                        animations=assets.STILL)
        return sorted(missing - absent)


# ------------------------------------------------------------ the browser

class Pages:
    """The page server Chromium loads: the 3D view's own page, the files
    from the cache, and the recipe being drawn. 127.0.0.1 only."""

    def __init__(self, files: Files, static: Path):
        self.files, self.static = files, static
        self.jobs: dict[str, dict] = {}
        self.scope = ""  # the drawing's (one at a time): whose files Chromium reads
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

    def serve(self, h: BaseHTTPRequestHandler) -> None:
        path = urllib.parse.urlsplit(h.path).path
        body, ctype, csp = None, "application/octet-stream", None
        if path == "/model3d/view":
            body, ctype = m3d.view_page(self.static, render=True), "text/html; charset=utf-8"
            csp = m3d.view_csp(body, render=True)
        elif path.startswith("/data/model3d/") and path.endswith(".json"):
            job = self.jobs.get(path[len("/data/model3d/"):-len(".json")])
            if job is not None:
                body, ctype = json.dumps(job).encode(), "application/json"
        elif path.startswith("/model3d/file/") and path[len("/model3d/file/"):].isdigit():
            body = self.files.get(int(path[len("/model3d/file/"):]), self.scope)
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


class Renderer:
    """Chromium, started once and kept, driven from one thread (Playwright's
    sync API stays on the thread that started it): one render at a time."""

    def __init__(self, env: dict):
        cache = cache_dir(env)
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            cache = None
        self.files = Files(cache, int(env.get("RENDER_CACHE_MB", "256")) * 1024 * 1024)
        from wowstats import ui

        self.pages = Pages(self.files, ui.static_dir() / "model3d")
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
        lacking = self.files.lacking(req["recipe"], req["absent"], req.get("scope", ""))
        if lacking:
            raise Lacking(lacking[:MAX_ASK])
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
        p.jobs[token] = {"recipe": req["recipe"], "models": "hd"}
        p.scope = req.get("scope", "")
        ctx = self.browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=SCALE)
        try:
            page = ctx.new_page()

            def guard(route):
                if route.request.url.startswith(p.base + "/"):
                    return route.continue_()
                self.blocked.append(urllib.parse.urlsplit(route.request.url).hostname or route.request.url[:40])
                return route.abort("blockedbyclient")

            page.route("**/*", guard)
            page.goto(f"{p.base}/model3d/view?render=1&name={token}", wait_until="load")
            # view.js says when the scene is loaded (every file asked, the
            # model built): data-state drawn, or error with why.
            try:
                page.wait_for_function("() => ['drawn', 'error'].includes(document.documentElement.dataset.state)",
                                       timeout=self.timeout * 1000)
            except Exception as e:  # noqa: BLE001 - Playwright's own TimeoutError
                raise TimeoutError("the model didn't finish") from e
            state = page.evaluate("() => [document.documentElement.dataset.state,"
                                  " (document.getElementById('status') || {}).textContent || '']")
            if state[0] != "drawn":
                raise BadRequest(f"the viewer couldn't draw it: {state[1][:200]}")
            # Two frames more: the last texture's upload reaches the canvas.
            page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            png = page.screenshot(type="png", omit_background=True)
            # Encoded on a blank page: the view's policy allows no images.
            return self._webp(ctx.new_page(), png)
        finally:
            p.jobs.pop(token, None)
            ctx.close()
            if len(self.blocked) > refused:  # never seen: the viewer loads everything from this process
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


# ------------------------------------------------------------ the listener

def make_handler(r, token: str):
    """POST needs `Authorization: Bearer <token>` (RENDER_TOKEN_FILE,
    wowstats.bearer); /healthz needs nothing. `r` has .files, .render(req)
    and .alive()."""
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

        def _body(self, most: int) -> bytes | None:
            try:
                n = int(self.headers.get("Content-Length") or "-1")
            except ValueError:
                n = -1
            return self.rfile.read(n) if 0 < n <= most else None

        def do_POST(self):  # noqa: N802
            m = FILE_PATH.match(self.path)
            if self.path != "/render" and not m:
                return self._send(404, b"not found\n")
            if not bearer.allowed(self.headers, token):
                return self._send(401, b"send the renderer's token (RENDER_TOKEN_FILE)\n")
            if m:
                return self._file(int(m.group(1)))
            body = self._body(MAX_BODY)
            if body is None:
                return self._send(400, b"a JSON body of at most 64 KiB\n")
            try:
                req = parse(body)
            except BadRequest as e:
                return self._send(400, f"{e}\n".encode())
            t0 = time.time()
            try:
                img = r.render(req)
            except Lacking as e:
                return self._send(428, json.dumps({"files": e.files}).encode(), "application/json")
            except Busy:
                return self._send(503, b"busy: one render at a time\n")
            except BadRequest as e:
                return self._send(400, f"{e}\n".encode())
            except (TimeoutError, concurrent.futures.TimeoutError):
                return self._send(504, b"the model didn't finish\n")
            except Exception as e:  # noqa: BLE001 - Chromium's own failures: say so, keep serving
                sys.stderr.write(f"render: failed: {e!r}\n")
                return self._send(500, b"the render failed\n")
            sys.stderr.write(f"render: {len(img) // 1024} KiB, {time.time() - t0:.1f} s\n")
            return self._send(200, img, "image/webp")

        def _file(self, fdid: int) -> None:
            body = self._body(assets.MAX_FILE)
            if body is None:
                return self._send(413, f"a body of 1 to {assets.MAX_FILE} bytes\n".encode())
            want = (self.headers.get("X-Content-SHA256") or "").strip().lower()
            if not hmac.compare_digest(want, hashlib.sha256(body).hexdigest()):
                return self._send(400, b"X-Content-SHA256 doesn't match the body\n")
            if assets.kind_of(body) is None:
                return self._send(415, b"not a model, skin, skeleton, animation or texture\n")
            try:
                scope = parse_scope(self.headers.get("X-Render-Scope") or "")
            except BadRequest as e:
                return self._send(400, f"{e}\n".encode())
            r.files.put(fdid, body, scope)
            return self._send(204, b"")

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
