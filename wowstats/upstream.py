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


def fetch(url: str, timeout: int = 30) -> bytes | None:
    """GET a URL: the body, None on a 404, Upstream on anything else."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed hosts, or a test's
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
