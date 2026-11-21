"""The character's picture in the middle of the paperdoll: a still of the
same 3D model the "View in 3D" toggle shows (its race, look and gear),
drawn by our own renderer (`wow-stats render`, wowstats.pictures) when the
publisher builds the static bundles.

Never Blizzard's armory render (owner, 2026-11-21). Battle.net's
/character-media serves its renders on paths shared by character id across
the Classic games: on 2026-11-21 the owner's Gnome showed a stranger's Night
Elf, byte for byte what Blizzard served for that character. So the
exporter no longer reads /character-media, nothing downloads from
Blizzard's render host, and the old cache of those downloads is removed
(forget_blizzard_cache).

The publisher puts the still at data/render/<char>.webp (public as is,
/zogzog/ sealed) and says so in the char answer's gear.render. The live
view has no renderer: RENDER_FROM points it at the static site, and it
serves the person's copy at /render/<char>.webp. With several people the
static site answers by Host (wowstats.static): the request names the
person's own site and reads their public bundle, else their /zogzog/ copy
unsealed with their key (a page that isn't public, a character hidden from
it). Only a WebP is served: whatever else a bundle could hold is not ours.
No still (no renderer, not drawn yet): the page keeps the silhouette and
its "View in 3D" toggle."""

from __future__ import annotations

import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

MAX_BYTES = 5 << 20
EXT = "webp"
# encodeURIComponent's safe characters, as the publisher writes paths.
URL_SAFE = "-_.!~*'()"


def kind(body: bytes | None) -> str | None:
    """"webp" when the body is a WebP image (what our renderer draws), else None."""
    return EXT if body and body[:4] == b"RIFF" and body[8:12] == b"WEBP" else None


def forget_blizzard_cache(env=None) -> bool:
    """Remove the downloads of Blizzard's renders that 0.7.0-rc.4 and
    older kept under MODEL3D_CACHE_DIR/render (30 days, keyed by Blizzard's
    address only, so one could hold another character's render). True when
    there was one."""
    env = os.environ if env is None else env
    old = Path(env.get("MODEL3D_CACHE_DIR") or Path(tempfile.gettempdir()) / "wow-stats-model3d") / "render"
    if not old.is_dir():
        return False
    shutil.rmtree(old, ignore_errors=True)
    return True


def rel(char: str) -> str:
    """The still's path in a bundle, as the publisher writes it (its
    url_part spelling: the seal's associated data too)."""
    return f"data/render/{urllib.parse.quote(char, safe=URL_SAFE)}.{EXT}"


def _read(url: str, timeout: int = 10, host: str = "") -> bytes | None:
    """The body at `url` on the static site; a redirect is followed only on
    that same site (2026-11-20 server review, info 4)."""
    from wowstats.upstream import same_origin, urlopen

    headers = {"User-Agent": "wow-stats"}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(url, headers=headers)
    try:
        with urlopen(req, timeout, same_origin(url)) as r:
            body = r.read(MAX_BYTES + 1)
    except (OSError, ValueError):
        return None
    return body if len(body) <= MAX_BYTES else None


def from_site(base: str, char: str, host: str = "", key: bytes | None = None,
              zogzog: str = "/zogzog/") -> bytes | None:
    """The still as the static site holds it (RENDER_FROM): the public
    bundle's copy, else the sealed /zogzog/ one when `key` opens it. `host`
    names the person's site on a static server shared by several people."""
    path = rel(char)
    base = base.rstrip("/")
    body = _read(f"{base}/{path}", host=host)
    if kind(body) or not key or not zogzog.strip("/"):
        return body if kind(body) else None
    from wowstats.publish import sealed_name

    blob = _read(f"{base}/{zogzog.strip('/')}/" + sealed_name(key, path), host=host)
    if not blob:
        return None
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:  # an install without the publish extra: public copies only
        return None
    try:
        # As wowstats.publish.seal wrote it: the nonce, then the ciphertext.
        body = AESGCM(key).decrypt(blob[:12], blob[12:], path.encode())
    except (InvalidTag, ValueError):
        return None
    return body if kind(body) else None
