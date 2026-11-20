"""The armory's full-body render of a character (Battle.net's
/character-media "main-raw", else "main"), for the middle of the paperdoll.

The exporter keeps its address in vm-wow with the rest of the profile, 30
days at most (Blizzard's API terms). The publisher downloads the image
into the static bundles (public as is, /zogzog/ sealed); the live view
serves it at /render/<char>.<ext>. Either way the visitor's browser never
asks Blizzard for it.

Downloads come only from the exporter's RENDER_HOSTS, over https, PNG or
JPEG, at most MAX_BYTES; cached by address on disk and dropped after 30
days. Where the live view has no route to Blizzard (a locked-down deployment: only the
publisher does), RENDER_FROM points it at the static site, which already
holds the image. With several people the static site answers by Host
(wowstats.static): the request names the person's own site and reads their
public bundle, else their /zogzog/ copy unsealed with their key (a page
that isn't public, a character hidden from it). Without the Host every
answer was a 404 and the full view showed the silhouette (2026-11-19)."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from wowstats.exporter import RENDER_HOSTS

MAX_BYTES = 5 << 20
KEEP_S = 30 * 86400
# encodeURIComponent's safe characters, as the publisher writes paths.
URL_SAFE = "-_.!~*'()"
MAGIC = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg"}
_lock = threading.Lock()


def kind(body: bytes | None) -> str | None:
    """png or jpg from the file's first bytes, None for anything else."""
    for m, k in MAGIC.items():
        if body and body.startswith(m):
            return k
    return None


def ext(url: str) -> str:
    return "png" if urllib.parse.urlsplit(url).path.lower().endswith(".png") else "jpg"


def allowed(url: str) -> bool:
    u = urllib.parse.urlsplit(url or "")
    return u.scheme == "https" and u.hostname in RENDER_HOSTS and not u.username and u.port is None


def cache_dir(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("MODEL3D_CACHE_DIR") or Path(tempfile.gettempdir()) / "wow-stats-model3d") / "render"


def _read(url: str, timeout: int = 30, host: str = "") -> bytes | None:
    """The body at `url`. A redirect is followed only to an allowed render
    address (Blizzard's) or, for RENDER_FROM's static site, to that same
    site (2026-11-20 server review, info 4: urllib followed it anywhere)."""
    from wowstats.upstream import same_origin, urlopen

    headers = {"User-Agent": "wow-stats"}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(url, headers=headers)
    try:
        with urlopen(req, timeout, allowed if allowed(url) else same_origin(url)) as r:
            body = r.read(MAX_BYTES + 1)
    except (OSError, ValueError):
        return None
    return body if len(body) <= MAX_BYTES else None


def _get(url: str, timeout: int = 30, host: str = "") -> bytes | None:
    body = _read(url, timeout, host)
    return body if kind(body) else None


def fetch(url: str, cache: Path | None = None, now: float | None = None) -> bytes | None:
    """The render at `url` (an allowed address), from the cache when it was
    downloaded within 30 days. Old cache files go on every call."""
    if not allowed(url):
        return None
    now = now or time.time()
    cache = cache or cache_dir()
    f = cache / (hashlib.sha256(url.encode()).hexdigest()[:32] + "." + ext(url))
    with _lock:
        try:
            cache.mkdir(parents=True, exist_ok=True)
            for old in cache.iterdir():
                if now - old.stat().st_mtime > KEEP_S:
                    old.unlink(missing_ok=True)
            if f.is_file():
                body = f.read_bytes()
                if kind(body):
                    return body
        except OSError:
            pass
    body = _get(url)
    if body:
        try:
            with _lock:
                f.write_bytes(body)
        except OSError:
            pass
    return body


def from_site(base: str, char: str, extension: str, host: str = "", key: bytes | None = None,
              zogzog: str = "/zogzog/") -> bytes | None:
    """The image as the static site holds it (RENDER_FROM): the public
    bundle's copy, else the sealed /zogzog/ one when `key` opens it. `host`
    names the person's site on a static server shared by several people."""
    # The publisher's own spelling of the path (publish.url_part): it is the
    # seal's associated data too.
    rel = f"data/render/{urllib.parse.quote(char, safe=URL_SAFE)}.{extension}"
    base = base.rstrip("/")
    body = _get(f"{base}/{rel}", timeout=10, host=host)
    if body or not key or not zogzog.strip("/"):
        return body
    from wowstats.publish import sealed_name

    zz = f"{base}/{zogzog.strip('/')}/"
    # The keyed name since 0.7.0; a bundle the publisher hasn't rebuilt yet
    # (it does at its first pass on a new version) still has the plain one.
    blob = _read(zz + sealed_name(key, rel), timeout=10, host=host) or _read(f"{zz}{rel}.enc", timeout=10, host=host)
    if not blob:
        return None
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:  # an install without the publish extra: public copies only
        return None
    try:
        # As wowstats.publish.seal wrote it: the nonce, then the ciphertext.
        body = AESGCM(key).decrypt(blob[:12], blob[12:], rel.encode())
    except (InvalidTag, ValueError):
        return None
    return body if kind(body) else None
