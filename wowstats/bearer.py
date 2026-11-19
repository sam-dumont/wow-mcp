"""Shared-secret tokens between wow-stats' own processes (the publisher and
the renderer): a file each side reads, sent as `Authorization: Bearer`.

The server side never runs without one: when its *_FILE isn't set it makes
a token on first start (0600, in its own state folder) and logs the path,
never the value. Whoever calls it is given that file. So a port reached by
mistake (published, on a flat Docker network, a pod with no network
policy) answers 401 instead of doing the work.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

MIN_LEN = 32


def read(path: str | Path, var: str) -> str:
    token = Path(path).read_text(encoding="utf-8").strip()
    if len(token) < MIN_LEN:
        raise SystemExit(f"{var}={path}: the token needs {MIN_LEN}+ characters "
                         "(head -c 32 /dev/urandom | base64 > <file>)")
    return token


def server_token(env: dict, var: str, default: Path, log=print) -> str:
    """The token in env[var]'s file; with var unset, `default`, made when
    missing. A var that names a missing file is a mistake, not a cue to
    make one: it stops the start."""
    given = (env.get(var) or "").strip()
    if given:
        if not Path(given).is_file():
            raise SystemExit(f"{var}={given}: no such file")
        return read(given, var)
    path = Path(default)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
        log(f"{var} unset: made a token in {path}; give that file to its callers as their {var}")
    return read(path, var)


def client_token(env: dict, var: str) -> str | None:
    given = (env.get(var) or "").strip()
    return read(given, var) if given else None


def allowed(headers, token: str) -> bool:
    got = (headers.get("Authorization") or "").encode()
    return hmac.compare_digest(got, f"Bearer {token}".encode())
