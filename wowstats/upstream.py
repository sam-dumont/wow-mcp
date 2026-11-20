"""Fetching from the few outside hosts the server reads (wago.tools' copies
of Forever's client tables, Wowhead's icon host), and writing what came to
disk in one step. Shared by wowstats.items, itemcard, talents and the KB."""

from __future__ import annotations

import os
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


class Upstream(Exception):
    pass


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
    """GET a URL: the body, None on a 404, Upstream on anything else."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as r:  # fixed hosts, or a test's; redirects stay on them
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
