"""What the web server checks before it trusts a request, whatever sits in
front of it (pre-publication review M1, M3, M4, L6, 2026-11-19).

The app can't count on a network policy or a proxy that cleans headers:
a self-hoster may run it on a flat Docker network, publish the port by
mistake, or use a proxy that passes a client's X-Forwarded-* through. So
each rule is enforced here, and a setting that can't be made safe stops the
server at start with what to set.

- **Host** (M1, DNS rebinding): a page on `evil.example` whose name later
  resolves to 127.0.0.1 reads a no-login local install with the browser's
  own requests. Those requests carry `Host: evil.example`, so the server
  answers only for loopback names, IP literals (a rebinding page can't make
  the browser send one), the hosts of PUBLIC_URL and MCP_PUBLIC_URL, and
  ALLOWED_HOSTS.
- **AUTH=open** (L6): no login at all, so only on a loopback BIND, or when
  PUBLISHED_ON says the container port is published on a loopback address
  (the compose kit passes UI_PUBLISH), or with ALLOW_OPEN=1 on purpose.
- **AUTH=proxy** (M4): the login proxy's identity header is believed only
  from PROXY_TRUSTED_CIDRS or with the PROXY_SECRET_FILE secret in
  PROXY_SECRET_HEADER. Neither set: the server doesn't start. A header sent
  twice, or holding a comma (a proxy that appends instead of replacing),
  is refused.
- **The share port** (M3): a bearer token on every request, from
  ANALYSIS_SHARE_TOKEN_FILE, made on first start when the file is missing.
"""

from __future__ import annotations

import contextlib
import hmac
import ipaddress
import os
import secrets
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TRUE = ("1", "true", "yes", "on")
TOKEN_MIN = 32
LOOPBACK_NAMES = frozenset({"localhost"})


class GuardError(SystemExit):
    """A setting that would leave the server open: stops it at start."""


def is_loopback(host: str) -> bool:
    host = (host or "").strip().strip("[]")
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_of(addr: str) -> str:
    """The host part of `host`, `host:port`, `[v6]:port` or a URL ("" for a
    bare port, which publishes on every interface)."""
    addr = (addr or "").strip()
    if "://" in addr:
        return (urllib.parse.urlsplit(addr).hostname or "").lower()
    if addr.startswith("["):
        return addr[1:addr.find("]")].lower() if "]" in addr else ""
    if addr.count(":") > 1:  # a bare IPv6 address
        return addr.lower()
    h = addr.rsplit(":", 1)[0] if ":" in addr else addr
    return "" if h.isdigit() else h.lower().rstrip(".")


def _ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- Host (M1)

class Hosts:
    def __init__(self, env: dict):
        names = {host_of(env.get(k, "")) for k in ("PUBLIC_URL", "MCP_PUBLIC_URL")}
        names |= {host_of(h) or h.strip().lower() for h in (env.get("ALLOWED_HOSTS") or "").split(",")}
        self.names = frozenset(n for n in names if n)

    def ok(self, header: str | None) -> bool:
        if header is None:  # HTTP/1.0 without Host: not a browser
            return True
        h = host_of(header)
        if not h:
            return False
        return h in self.names or is_loopback(h) or _ip(h)

    def refusal(self, header: str | None) -> bytes:
        allowed = ", ".join(sorted(self.names)) or "none set"
        return (f"This server doesn't answer for the host {host_of(header or '')[:80]!r}. It answers for "
                f"localhost, IP addresses and the hosts of PUBLIC_URL, MCP_PUBLIC_URL and ALLOWED_HOSTS "
                f"({allowed}). Reaching it by another name? Add that name to ALLOWED_HOSTS "
                f"(comma separated) and set a login (AUTH=basic or AUTH=proxy): a name here means "
                f"a proxy or another computer in front of this server.\n").encode()


# ---------------------------------------------------------------- AUTH=open (L6)

def check_open(env: dict, bind: str) -> None:
    if is_loopback(bind):
        return
    if (env.get("ALLOW_OPEN") or "").strip().lower() in TRUE:
        return
    pub = (env.get("PUBLISHED_ON") or "").strip()
    if pub and is_loopback(host_of(pub)):
        return
    where = f"PUBLISHED_ON={pub}" if pub else "PUBLISHED_ON unset"
    raise GuardError(
        f"AUTH=open on BIND={bind} ({where}): anyone who reaches this port gets in without a login. "
        "Either publish the port on 127.0.0.1 only and set PUBLISHED_ON to that address (the compose kit "
        "does: UI_PUBLISH), or set AUTH=basic or AUTH=proxy, or set ALLOW_OPEN=1 if everyone who can reach "
        "it may see it (a public view).")


# Headers a reverse proxy adds. A full view with no login never sits behind one:
# a host-level nginx passes `Host: 127.0.0.1:8080`, which every check above
# accepts, and the page would be on the internet (review 0.6.0, S4).
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded", "Via", "X-Real-IP")
PROXIED = (b"This server has no login (AUTH=open) and this request came through a proxy. Set a login "
           b"(AUTH=basic or AUTH=proxy) before putting a proxy in front of it.\n")


def check_open_hosts(env: dict) -> None:
    """A full view with no login and names to answer for is a proxy setup
    without a login: stop at start."""
    if (env.get("ALLOWED_HOSTS") or "").strip():
        raise GuardError(
            "AUTH=open with ALLOWED_HOSTS set: a name to answer for means a proxy or another computer in "
            "front of a view with no login. Set AUTH=basic or AUTH=proxy, or empty ALLOWED_HOSTS.")


def proxied(headers) -> bool:
    return any(headers.get(h) is not None for h in PROXY_HEADERS)


# ---------------------------------------------------------------- AUTH=proxy (M4)

def _read_secret(path: str, what: str) -> str:
    try:
        s = Path(path).read_text(encoding="utf-8").strip()
    except OSError as e:
        raise GuardError(f"{what}={path}: can't read it ({e.strerror})") from None
    if len(s) < TOKEN_MIN:
        raise GuardError(f"{what}={path}: {TOKEN_MIN} characters or more")
    return s


class ProxyTrust:
    """Who may speak for the login proxy."""

    def __init__(self, env: dict):
        nets = []
        for c in (env.get("PROXY_TRUSTED_CIDRS") or "").split(","):
            if not c.strip():
                continue
            try:
                nets.append(ipaddress.ip_network(c.strip(), strict=False))
            except ValueError:
                raise GuardError(f"PROXY_TRUSTED_CIDRS: {c.strip()!r} is not an address or a CIDR") from None
        self.nets = tuple(nets)
        path = (env.get("PROXY_SECRET_FILE") or "").strip()
        self.secret = _read_secret(path, "PROXY_SECRET_FILE").encode() if path else None
        self.secret_header = (env.get("PROXY_SECRET_HEADER") or "X-Proxy-Auth").strip()
        if not self.nets and self.secret is None:
            raise GuardError(
                "AUTH=proxy trusts the login proxy's identity header, so the app must know which requests "
                "come from that proxy. Set PROXY_TRUSTED_CIDRS (the proxy's addresses as this server sees "
                "them, e.g. 127.0.0.1/32 or the Docker network's subnet from `docker network inspect`) "
                f"and/or PROXY_SECRET_FILE (a secret of {TOKEN_MIN}+ characters the proxy sends in "
                f"{self.secret_header}). "
                'docs/deploy.md, "Behind a reverse proxy".')

    def trusted(self, peer: str, headers) -> bool:
        """Both checks that are set must pass."""
        if self.nets:
            try:
                ip = ipaddress.ip_address(peer)
            except ValueError:
                return False
            if ip.version == 6 and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if not any(ip in n for n in self.nets):
                return False
        if self.secret is not None:
            got = (headers.get(self.secret_header) or "").strip().encode()
            if not hmac.compare_digest(got, self.secret):
                return False
        return True


def identity(headers, name: str) -> tuple[str, str]:
    """(value, problem): the identity header once, as the proxy set it.
    Twice, or a list, means a proxy appended to what the client sent."""
    vals = headers.get_all(name) if hasattr(headers, "get_all") else None
    if vals is None:
        v = headers.get(name)
        vals = [] if v is None else [v]
    if len(vals) > 1 or any("," in v for v in vals):
        return "", (f"{name} came more than once: the proxy in front must replace it, never append to "
                    "what the client sent")
    return (vals[0].strip() if vals else ""), ""


# ---------------------------------------------------------------- the share port (M3)

def share_token_path(env: dict) -> Path | None:
    p = (env.get("ANALYSIS_SHARE_TOKEN_FILE") or "").strip()
    if p:
        return Path(p)
    base = (env.get("WOW_STATE_DIR") or env.get("ANALYSIS_STATE_DIR") or "").strip()
    return Path(base) / "share-token" if base else None


def share_token(env: dict) -> str:
    """The share port's token: the file's, or a new one written there (0600)
    on first start, for the publisher and the exporter to read from the
    same volume or a secret."""
    path = share_token_path(env)
    if path is None:
        raise GuardError("ANALYSIS_SHARE_PORT needs ANALYSIS_SHARE_TOKEN_FILE (or a state dir to make it in)")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # A fresh temporary name each time (mkstemp: 0600, O_EXCL), removed
        # whatever fails, the write included. It was share-token.<pid>.tmp,
        # left behind when the write hit a full disk: in a container the pid
        # is always 1, so every restart then failed on O_EXCL and the ui
        # crash-looped after the space came back (0.10.0 checks, 2026-11-24).
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(secrets.token_urlsafe(TOKEN_MIN) + "\n")
            with contextlib.suppress(FileExistsError):
                os.link(tmp, path)  # atomic, and never replaces a file another process just made
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    return _read_secret(str(path), "ANALYSIS_SHARE_TOKEN_FILE")


def bearer_ok(headers, token: str) -> bool:
    got = (headers.get("Authorization") or "").strip().encode()
    return hmac.compare_digest(got, b"Bearer " + token.encode())


def share_open(url: str, env: dict | None = None, timeout: float = 15):
    """GET a share-port URL with the token from ANALYSIS_SHARE_TOKEN_FILE.
    Without the file the request goes bare and the port answers 401: the
    caller's log then says what to set."""
    env = os.environ if env is None else env
    headers = {}
    p = (env.get("ANALYSIS_SHARE_TOKEN_FILE") or "").strip()
    if p:
        try:
            headers["Authorization"] = "Bearer " + _read_secret(p, "ANALYSIS_SHARE_TOKEN_FILE")
        except GuardError as e:  # a client logs and tries again next pass
            raise OSError(str(e.code)) from None
    req = urllib.request.Request(url, headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - an address from our own env
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise OSError(f"{urllib.parse.urlsplit(url).netloc} refused the request (401): set "
                          "ANALYSIS_SHARE_TOKEN_FILE to the full view's share token file") from None
        raise


def share_post(url: str, body: bytes, env: dict | None = None, timeout: float = 15) -> None:
    """POST JSON to a share-port URL with the token (share_open's twin)."""
    env = os.environ if env is None else env
    headers = {"Content-Type": "application/json"}
    p = (env.get("ANALYSIS_SHARE_TOKEN_FILE") or "").strip()
    if p:
        try:
            headers["Authorization"] = "Bearer " + _read_secret(p, "ANALYSIS_SHARE_TOKEN_FILE")
        except GuardError as e:
            raise OSError(str(e.code)) from None
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - an address from our own env
            r.read(1024)
    except urllib.error.HTTPError as e:
        raise OSError(f"{urllib.parse.urlsplit(url).netloc} answered {e.code}") from None
