"""Fetching from the few outside hosts the server reads (wago.tools' copies
of Forever's client tables, Wowhead's icon host), and writing what came to
disk in one step. Shared by wowstats.items, itemcard, talents and the KB."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from wowstats import __version__

WAGO_URL = "https://wago.tools"
PRODUCT = "wowf"  # WoW Forever on wago.tools
USER_AGENT = f"wow-stats/{__version__}"
MAX_FILE = 48 * 1024 * 1024

# The origins fetch() may reach: the fixed upstreams. Config (MODEL3D_WAGO_URL,
# ICON_URL) and tests add theirs with allow(); nothing a request or an upstream
# answer carries ever does (CodeQL #42, py/partial-ssrf).
ALLOWED: set[tuple[str, str, int | None]] = {("https", "wago.tools", 443), ("https", "wow.zamimg.com", 443)}
_PORTS = {"https": 443, "http": 80}

# The values that go into those URLs, each a strict shape: an icon name from a
# request path, a build from wago.tools' own JSON, a table or product from the
# code. Real icon names carry hyphens (ui-hud-actionbar-keyring); none is over
# 76 characters (Forever 1.60.1, 2026-11-22).
ICON_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
ICON_SIZE = re.compile(r"small|medium|large")
BUILD = re.compile(r"[0-9]{1,4}(?:\.[0-9]{1,7}){3}")
PRODUCT_NAME = re.compile(r"[a-z0-9_]{1,32}")
# Every client table the server and its tools read from wago.tools.
WAGO_TABLES = frozenset({
    "AreaTable", "ChrClasses", "CurvePoint", "GroupFinderActivity", "Item", "ItemAppearance",
    "ItemModifiedAppearance", "ItemSet", "ItemSparse", "ItemSubClass", "ManifestInterfaceData", "SkillLine",
    "SkillLineAbility", "Spell", "SpellAuraOptions", "SpellDuration", "SpellEffect", "SpellItemEnchantment",
    "SpellLevels", "SpellMisc", "SpellName", "SpellRadius", "Talent", "TalentTab", "TraitCurrencySource",
    "TraitDefinition", "TraitDefinitionEffectPoints", "TraitEdge", "TraitNode", "TraitNodeEntry",
    "TraitNodeXTraitNodeEntry", "TraitTree", "TraitTreeXTraitCurrency",
})


class Upstream(Exception):
    pass


def _origin(url: str) -> tuple[str, str, int | None]:
    u = urllib.parse.urlsplit(url)
    return u.scheme, (u.hostname or "").lower(), u.port or _PORTS.get(u.scheme)


def allow(url: str) -> str:
    """Let fetch() reach `url`'s origin (scheme, host, port): a configured
    upstream, or a test's local server. Never called with request input."""
    ALLOWED.add(_origin(url))
    return url


def allowed(url: str) -> bool:
    try:
        u = urllib.parse.urlsplit(url)
        return u.username is None and u.password is None and _origin(url) in ALLOWED
    except ValueError:  # a bad port
        return False


# Each check below is a direct fullmatch on a module-level pattern, one value
# per `if`: CodeQL's request-forgery query only takes that shape as a guard (a
# helper handed the pattern, or `in` a frozenset, left alert #42 open).
def _str(value, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"not a {what}")  # the value is never echoed: it may be anything
    return value


def builds_url(base: str, product: str) -> str:
    """wago.tools' latest build of `product`."""
    product = _str(product, "product")
    if not PRODUCT_NAME.fullmatch(product):
        raise ValueError("not a product")
    return f"{base.rstrip('/')}/api/builds/{urllib.parse.quote(product, safe='')}/latest"


def build_of(body: bytes | None) -> str:
    """The build in builds_url()'s answer; ValueError when there is none or
    it isn't one (wago.tools' answer is data, like a request)."""
    d = json.loads(body or b"{}")
    version = str(d.get("version") if isinstance(d, dict) else None)
    if not BUILD.fullmatch(version):
        raise ValueError("not a build")
    return version


def table_url(base: str, table: str, build: str) -> str:
    """One client table of one build, as CSV."""
    if table not in WAGO_TABLES:
        raise ValueError("not a listed table")
    build = _str(build, "build")
    if not BUILD.fullmatch(build):
        raise ValueError("not a build")
    return f"{base.rstrip('/')}/db2/{urllib.parse.quote(table, safe='')}/csv?build={urllib.parse.quote(build, safe='')}"


def icon_url(base: str, size: str, name: str) -> str:
    """One icon on Wowhead's icon host (`base` ends in /icons/)."""
    size, name = _str(size, "icon size"), _str(name, "icon name")
    if not ICON_SIZE.fullmatch(size):
        raise ValueError("not an icon size")
    if not ICON_NAME.fullmatch(name):
        raise ValueError("not an icon name")
    return f"{base.rstrip('/')}/{urllib.parse.quote(size, safe='')}/{urllib.parse.quote(name, safe='')}.jpg"


def same_origin(url: str):
    """The redirect rule for a fixed host: only to the same scheme, host and
    port as `url`."""
    want = urllib.parse.urlsplit(url)

    def ok(new: str) -> bool:
        u = urllib.parse.urlsplit(new)
        return (u.scheme, u.hostname, u.port) == (want.scheme, want.hostname, want.port) and not u.username

    return ok


class _Redirects(urllib.request.HTTPRedirectHandler):
    """urllib follows a redirect anywhere; this one only where `allow` says
    (2026-11-20 server review, info 4). Off it, the redirect itself comes back
    as an HTTPError with its code."""

    def __init__(self, allow):
        self.allow = allow

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allow(urllib.parse.urljoin(req.full_url, newurl)):
            raise urllib.error.HTTPError(req.full_url, code, "redirect off the allowed hosts", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(req: urllib.request.Request, timeout: float, allow=None):
    """urllib.request.urlopen, with redirects followed only where `allow`
    (a function of the new URL) says; by default to the same origin."""
    return urllib.request.build_opener(_Redirects(allow or same_origin(req.full_url))).open(req, timeout=timeout)


def fetch(url: str, timeout: int = 30) -> bytes | None:
    """GET a URL: the body, None on a 404, Upstream on anything else, before
    any request when it isn't on an allowed origin."""
    if not allowed(url):
        raise Upstream("not an allowed upstream")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as r:  # allowed origins only; redirects stay on them
            body = r.read(MAX_FILE + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise Upstream(f"{urllib.parse.urlsplit(url).hostname} answered {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Upstream(f"{urllib.parse.urlsplit(url).hostname} unreachable: {e}") from None
    if len(body) > MAX_FILE:
        raise Upstream(f"{urllib.parse.urlsplit(url).hostname}: file too large")
    return body


def write_file(path: Path, data: bytes) -> None:
    """Write a file whole or not at all (a temporary file, then a rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".part-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
