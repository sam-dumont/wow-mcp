"""`wow-stats static`: serves what `wow-stats publish` wrote, and nothing else.

GET and HEAD only, files only: no API, no query handling, no upstream, no
store. The public bundle at /, the zogzog bundle (sealed data) at
ZOGZOG_PATH. Every page keeps the strict CSP ('self' only; the 3D frame adds
its viewer's own), Referrer-Policy no-referrer and noindex.

It needs no network at all besides its listening port: give its container
no egress, and its root filesystem is read-only.

Environment: SITE_DIR (the publisher's PUBLISH_DIR; serves <SITE_DIR>/current),
ZOGZOG_PATH (/zogzog/, empty: off), BIND (0.0.0.0), PORT (8080).

Several people (wowstats.tenancy): SITE_DOMAIN set, each person's site is
<SITE_DIR>/<slug>/current, served for the host <slug>-wow.<SITE_DOMAIN> and
for SITE_ALIASES (host=slug, comma separated: wow.example.org=alex). Any
other host, or a site whose public page is off, answers 404. Pushes carry
?slug=; DELETE /bundle?slug= drops a site; GET /bundles lists them.

Where the publisher can't share a volume with it (Kubernetes: two pods, and
the static one must have no route out), UPLOAD_PORT and UPLOAD_TOKEN_FILE open a
second listener the publisher pushes each build to (`PUT /bundle`, a tar.gz,
bearer token): it lands in SITE_DIR, an emptyDir, and `current` swaps to it.
`GET /bundle` says which build is live, so a restarted pod gets the build
again at the publisher's next pass. Never route that port from outside.
"""

from __future__ import annotations

import hmac
import io
import json
import os
import posixpath
import re
import shutil
import sys
import tarfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from wowstats import httpserve, ui
from wowstats import model3d as m3d


class Server(httpserve.Server):
    """A page asks for dozens of files at once (icons, sealed data, the 3D
    files): the stock listen backlog of 5 made the kernel reset some of
    those connections (ERR_CONNECTION_RESET, a slot without its icon). The
    backlog, the timeouts and the connection cap: wowstats.httpserve."""

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".woff2": "font/woff2",
         ".webp": "image/webp", ".png": "image/png", ".txt": "text/plain; charset=utf-8",
         ".enc": "application/octet-stream"}
NOT_FOUND = b"Not found\n"
BUILD = re.compile(r"^b\d{1,12}$")
SLUG = re.compile(r"^[a-z0-9]{2,20}$")
MAX_UPLOAD = 512 * 1024 * 1024
KEEP = 2


class Site:
    def __init__(self, env: dict | None = None):
        env = dict(os.environ if env is None else env)
        self.dir = Path(env.get("SITE_DIR", "/data/site"))
        zz = env.get("ZOGZOG_PATH", "/zogzog/").strip()
        self.zogzog = "/" + zz.strip("/") + "/" if zz.strip("/") else ""
        self.bind = env.get("BIND", "0.0.0.0")
        self.port = int(env.get("PORT", "8080"))
        self.upload_port = int(env.get("UPLOAD_PORT", "0") or 0)
        tf = env.get("UPLOAD_TOKEN_FILE", "")
        self.token = Path(tf).read_text().strip().encode() if tf else b""
        if self.upload_port and len(self.token) < 32:
            raise SystemExit("UPLOAD_PORT needs UPLOAD_TOKEN_FILE with a token of 32 characters or more")
        self.lock = threading.Lock()
        self.domain = env.get("SITE_DOMAIN", "").strip().lower().strip(".")
        self.aliases: dict[str, str] = {}
        for pair in (x.strip() for x in env.get("SITE_ALIASES", "").split(",")):
            host, sep, slug = pair.partition("=")
            if not pair:
                continue
            if not sep or not SLUG.match(slug.strip()):
                raise SystemExit(f"SITE_ALIASES: {pair!r} is not host=slug")
            self.aliases[host.strip().lower()] = slug.strip()
        self.multi = bool(self.domain)
        self.host_re = re.compile(rf"^([a-z0-9]{{2,20}})-wow\.{re.escape(self.domain)}$") if self.multi else None

    def slug_of(self, host: str) -> str | None:
        """The site a Host header names (its port dropped); None: not ours."""
        h = (host or "").strip().lower().rsplit(":", 1)[0] if host and not host.startswith("[") else ""
        if h in self.aliases:
            return self.aliases[h]
        m = self.host_re.match(h) if self.host_re else None
        return m.group(1) if m else None

    def root_of(self, slug: str = "") -> Path:
        if not self.multi:
            return self.dir
        if not SLUG.match(slug or ""):
            raise ValueError("bad slug")
        return self.dir / slug

    def live(self, slug: str = "") -> str:
        cur = self.root_of(slug) / "current"
        return os.readlink(cur) if cur.is_symlink() else ""

    def remove(self, slug: str) -> None:
        with self.lock:
            shutil.rmtree(self.root_of(slug), ignore_errors=True)

    def install(self, build: str, tar_gz: bytes, slug: str = "") -> None:
        """Unpack a pushed build next to the others and swap `current` to it."""
        if not BUILD.match(build):
            raise ValueError("bad build name")
        base = self.root_of(slug)
        base.mkdir(parents=True, exist_ok=True)
        with self.lock:
            tmp = base / f".in-{build}"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
                # The "data" filter refuses absolute paths, links out of the
                # tree, devices and setuid bits.
                tf.extractall(tmp, filter="data")
            final = base / build
            shutil.rmtree(final, ignore_errors=True)
            os.replace(tmp, final)
            link = base / ".current.tmp"
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(build)
            os.replace(link, base / "current")
            old = sorted(p for p in base.glob("b*") if p.is_dir() and p.name != build)
            for p in old[:-KEEP]:
                shutil.rmtree(p, ignore_errors=True)

    def resolve(self, url_path: str, host: str = "") -> tuple[Path, str] | None:
        """The file for a request path (and Host, several people), and which
        bundle; None: 404."""
        site = self.dir
        if self.multi:
            slug = self.slug_of(host)
            if slug is None:
                return None
            site = self.dir / slug
        path = urllib.parse.unquote(url_path.split("?", 1)[0].split("#", 1)[0])
        if "\x00" in path or "\\" in path:
            return None
        bundle = "public"
        if self.zogzog and (path + "/").startswith(self.zogzog):
            bundle, path = "zogzog", "/" + path[len(self.zogzog):]
        clean = posixpath.normpath("/" + path.lstrip("/"))
        if clean.startswith("/.") or "/." in clean:
            return None
        root = (site / "current" / bundle).resolve()
        rel = clean.lstrip("/")
        f = root / rel if rel else root / "index.html"
        if path.endswith("/") and rel:
            f = f / "index.html"
        if not f.suffix and not f.is_file():
            f = f.with_name(f.name + ".html")  # /model3d/view -> view.html
        try:
            f = f.resolve()
            f.relative_to(root)
        except (OSError, ValueError):
            return None
        return (f, bundle) if f.is_file() else None


def make_handler(site: Site):
    class Handler(BaseHTTPRequestHandler):
        server_version = "wow-stats"
        sys_version = ""

        def log_message(self, fmt, *args):  # the path only (the zogzog key is a fragment: never sent)
            code = args[1] if len(args) > 1 else ""
            path = urllib.parse.urlsplit(self.path).path
            sys.stdout.write(f"{self.command} {path} {code}\n")
            if str(code) == "404" and "/model3d/" in path:
                # The bundle lacks a file the 3D viewer asked for (or the
                # browser holds an old revision's file): the model draws
                # wrong or not at all. One line to grep for.
                sys.stdout.write(f"missing 3D file: {path}\n")

        def _send(self, code: int, body: bytes, ctype: str, csp: str, bundle: str, cache: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Security-Policy", csp)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            if self.path == "/healthz":
                return self._send(200, b"ok\n", "text/plain", ui.CSP, "", "no-store")
            hit = site.resolve(self.path, self.headers.get("Host", ""))
            if hit is None:
                return self._send(404, NOT_FOUND, "text/plain; charset=utf-8", ui.CSP, "", "no-store")
            f, bundle = hit
            body = f.read_bytes()
            # The 3D frame: its own sandboxed policy, read from the page.
            csp = m3d.view_csp(body) if f.name == "view.html" else ui.CSP_3D
            # The 3D files named by a FileDataID never change; the metas and
            # the viewer script do (wowstats.model3d.cache_control).
            posix = f.as_posix()
            zam = f"/model3d/zam/{m3d.ZAM_REV}/"
            cache = (m3d.cache_control(posix.split(zam, 1)[1]) if zam in posix
                     else "public, max-age=86400" if f.suffix == ".woff2" else "no-cache")
            self._send(200, body, TYPES.get(f.suffix, "application/octet-stream"), csp, bundle, cache)

        do_HEAD = do_GET

        def _refuse(self):
            self._send(405, b"", "text/plain", ui.CSP, "", "no-store")

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

    return Handler


def make_upload_handler(site: Site):
    class Upload(BaseHTTPRequestHandler):
        server_version = "wow-stats"
        sys_version = ""

        def log_message(self, fmt, *args):
            code = args[1] if len(args) > 1 else ""
            sys.stdout.write(f"upload {self.command} {urllib.parse.urlsplit(self.path).path} {code}\n")

        def _reply(self, code: int, obj) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            got = self.headers.get("Authorization", "").removeprefix("Bearer ").encode()
            return hmac.compare_digest(got, site.token)

        def _slug(self) -> str | None:
            slug = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("slug", [""])[0]
            if site.multi and not SLUG.match(slug):
                self._reply(400, {"error": "slug"})
                return None
            return slug if site.multi else ""

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path not in ("/bundle", "/bundles") or (path == "/bundles" and not site.multi):
                return self._reply(404, {"error": "not found"})
            if not self._authorized():
                return self._reply(401, {"error": "token"})
            if path == "/bundles":  # every site this pod holds: the publisher drops the ones that left
                return self._reply(200, {"slugs": sorted(d.name for d in site.dir.iterdir()
                                                         if d.is_dir() and SLUG.match(d.name))})
            slug = self._slug()
            if slug is None:
                return None
            self._reply(200, {"build": site.live(slug)})

        def do_DELETE(self):
            if urllib.parse.urlsplit(self.path).path != "/bundle":
                return self._reply(404, {"error": "not found"})
            if not self._authorized():
                return self._reply(401, {"error": "token"})
            slug = self._slug()
            if not slug:
                return self._reply(400, {"error": "slug"})
            site.remove(slug)
            self._reply(200, {"removed": slug})

        def do_PUT(self):
            u = urllib.parse.urlsplit(self.path)
            if u.path != "/bundle":
                return self._reply(404, {"error": "not found"})
            if not self._authorized():
                return self._reply(401, {"error": "token"})
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_UPLOAD:
                return self._reply(413, {"error": "size"})
            build = urllib.parse.parse_qs(u.query).get("build", [""])[0]
            slug = self._slug()
            if slug is None:
                return None
            try:
                site.install(build, self.rfile.read(n), slug)
            except (ValueError, tarfile.TarError, OSError) as e:
                return self._reply(400, {"error": type(e).__name__})
            self._reply(200, {"build": build})

    return Upload


def main() -> int:
    site = Site()
    site.dir.mkdir(parents=True, exist_ok=True)
    if site.upload_port:
        up = Server((site.bind, site.upload_port), make_upload_handler(site))
        threading.Thread(target=up.serve_forever, daemon=True).start()
        print(f"wow-stats static: builds pushed to :{site.upload_port}", flush=True)
    httpd = Server((site.bind, site.port), make_handler(site))
    print(f"wow-stats static: {site.dir}/current on {site.bind}:{site.port}"
          + (f", full content at {site.zogzog}" if site.zogzog else ""), flush=True)
    httpd.serve_forever()
    return 0
