"""The images' HEALTHCHECK: `python -m wowstats.healthcheck`.

One image runs several modes, so the check reads which one from the
container's main process (/proc/1/cmdline) and asks that mode's /healthz.
Modes without a port (publish, analyse, zogzog-key...) are healthy while
they run. Kubernetes ignores HEALTHCHECK and uses its own probes
(docs/deploy.md); this is for `docker ps` and compose.

Standard library only, exec form, no shell: the image has none to spare.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

# Mode -> (port variable, its default). The defaults are the servers' own.
PORTS = {
    "ui": ("PORT", "8080"),
    "static": ("PORT", "8080"),
    "render": ("PORT", "8083"),
    "exporter": ("LISTEN_PORT", "9464"),
}


def mode(cmdline: bytes) -> str | None:
    """The wow-stats mode in a /proc/<pid>/cmdline, None when it isn't wow-stats."""
    args = [a.decode(errors="replace") for a in cmdline.split(b"\0") if a]
    for i, a in enumerate(args):
        if Path(a).name == "wow-stats":
            rest = [x for x in args[i + 1:] if not x.startswith("-")]
            return rest[0] if rest else None
    return None


def url(m: str, env: dict[str, str]) -> str | None:
    if m not in PORTS:
        return None
    var, default = PORTS[m]
    host = env.get("BIND", "") or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{env.get(var) or default}/healthz"


def check(target: str) -> int:
    """0 when the server answers 200. No proxy: an HTTP_PROXY in the
    container's environment must not catch a loopback call."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(target, timeout=4) as r:
            return 0 if r.status == 200 else 1
    except OSError as e:
        print(f"healthcheck: {target}: {e}", file=sys.stderr)
        return 1


def main() -> int:
    try:
        m = mode(Path("/proc/1/cmdline").read_bytes())
    except OSError:
        m = None
    target = url(m, dict(os.environ)) if m else None
    if target is None:
        return 0  # no server in this mode: running is healthy
    return check(target)


if __name__ == "__main__":
    sys.exit(main())
