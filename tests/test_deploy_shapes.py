"""The web server on the ways people self-host it, each with the attack the
pre-publication review described (M1, M3, M4, L6), and the refusal.

The owner's lab has a network policy on every port and a Traefik that
strips client identity headers; most self-hosters have neither (owner,
2026-11-19: "make sure it does fix this also for people who self host
differently"). So every case here runs the app with no help from outside:

- exposed directly, by mistake (no proxy, a 0.0.0.0 bind, a published port);
- compose behind Caddy, nginx or Traefik on a Docker network, where any
  other container reaches the port too;
- a proxy that appends to a client's X-Forwarded-User instead of replacing it;
- a proxy proving itself with a shared secret instead of its address;
- a no-login local install, read by a DNS-rebinding page;
- the share port on a flat network.

Each is real sockets against a real server: no handler is called directly.
"""

from __future__ import annotations

import http.client
import json
import os
import stat
import urllib.error
import urllib.request

import pytest

from tests.test_tenancy import FRIEND, OWNER, TenantVL
from wowstats import analysis, guard, publish, tenancy, ui
from wowstats.ui import ConfigError

SECRET = "s" * 40


@pytest.fixture
def vl():
    v = TenantVL()
    yield v
    v.close()


def multi_env(vl, tmp_path, **extra) -> dict:
    """A multi-user full view as compose or Kubernetes would run it: 0.0.0.0
    in its container, the login proxy in front."""
    master = tmp_path / "master.key"
    master.write_text(tenancy.b64u(b"m" * 32))
    friends = tmp_path / "friends.json"
    friends.write_text(json.dumps({FRIEND: "mira"}))
    env = {"BIND": "0.0.0.0", "VIEW": "full", "AUTH": "proxy", "VL_URL": vl.url, "VM_URL": vl.url,
           "FRIENDS_VL_URL": vl.url, "WOW_STATE_DIR": str(tmp_path / "state"), "WOW_OWNER_SUB": OWNER,
           "WOW_OWNER_SLUG": "sam", "WOW_FRIENDS_FILE": str(friends), "PUBLIC_URL": "https://wow.example.org",
           "ZOGZOG_MASTER_KEY_FILE": str(master), "ZOGZOG_KEY_FILE": str(tmp_path / "zz.key"),
           "MODEL3D": "off", "MODEL3D_CACHE_DIR": str(tmp_path / "cache"),
           "ANALYSIS_STATE_DIR": str(tmp_path / "owner-analysis")}
    return {**env, **extra}


def start(run_app, env):
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    r = run_app(app)  # on 127.0.0.1: the test is the "other container"
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    return r


# ---------------------------------------------------------------- exposed directly (L6, M4)

@pytest.mark.parametrize("view", ["full", "public"])
@pytest.mark.parametrize("published", [None, "0.0.0.0:8080", "8080", "192.0.2.20:8080", "nas.local:8080"])
def test_no_login_off_loopback_refuses_to_start(view, published):
    env = {"BIND": "0.0.0.0", "VIEW": view, "AUTH": "open"}
    if published is not None:
        env["PUBLISHED_ON"] = published
    with pytest.raises(ConfigError, match="PUBLISHED_ON"):
        ui.App(env)


@pytest.mark.parametrize("extra", [{"PUBLISHED_ON": "127.0.0.1:8080"}, {"PUBLISHED_ON": "[::1]:8080"},
                                   {"PUBLISHED_ON": "localhost:8080"}, {"ALLOW_OPEN": "1"}])
def test_no_login_only_when_the_port_stays_local_or_on_purpose(extra):
    assert ui.App({"BIND": "0.0.0.0", "VIEW": "full", "AUTH": "open", **extra}).auth_mode == "open"


def test_proxy_mode_without_saying_who_the_proxy_is_refuses_to_start(vl, tmp_path):
    for bind in ("0.0.0.0", "127.0.0.1"):
        with pytest.raises(ConfigError, match="PROXY_TRUSTED_CIDRS"):
            ui.App(multi_env(vl, tmp_path, BIND=bind))
    with pytest.raises(ConfigError, match="CIDR"):
        ui.App(multi_env(vl, tmp_path, PROXY_TRUSTED_CIDRS="not-a-network"))
    short = tmp_path / "short"
    short.write_text("x" * 10)
    with pytest.raises(ConfigError, match="32"):
        ui.App(multi_env(vl, tmp_path, PROXY_SECRET_FILE=str(short)))


# ---------------------------------------------------------------- behind a proxy on a Docker network (M4)

def test_a_forged_owner_header_from_outside_the_proxy_gets_nothing(vl, tmp_path, run_app):
    """Caddy/nginx/Traefik on 198.51.100.0/24 (a stand-in for the Docker network); anything else that reaches the
    port (another container, the published port) sends the owner's sub."""
    srv = start(run_app, multi_env(vl, tmp_path, PROXY_TRUSTED_CIDRS="198.51.100.0/24"))
    for path in ("/", "/api/me", "/api/chars", "/api/admin/hosts", "/settings", "/api/logsql?q=*"):
        code, _, body = srv.get(path, headers={"X-Forwarded-User": OWNER})
        assert code == 403, (path, code, body[:200])
        assert b"proxy" in body
    # What needs no login keeps working: probes, and the shipper's own login.
    assert srv.get("/healthz")[0] == 200
    assert srv.get("/ingest", method="POST", data=b"")[0] == 401


def test_the_proxy_itself_is_believed(vl, tmp_path, run_app):
    srv = start(run_app, multi_env(vl, tmp_path, PROXY_TRUSTED_CIDRS="127.0.0.1/32"))
    code, _, body = srv.get("/api/me", headers={"X-Forwarded-User": OWNER})
    assert code == 200 and json.loads(body)["owner"], body


@pytest.mark.parametrize("headers", [
    [("X-Forwarded-User", "someone-else"), ("X-Forwarded-User", OWNER)],
    [("X-Forwarded-User", OWNER), ("X-Forwarded-User", "someone-else")],
    [("X-Forwarded-User", f"someone-else, {OWNER}")],
    [("X-Forwarded-User", f"{OWNER},{OWNER}")],
])
def test_a_proxy_that_appends_to_the_clients_header_logs_in_no_one(vl, tmp_path, run_app, headers):
    srv = start(run_app, multi_env(vl, tmp_path, PROXY_TRUSTED_CIDRS="127.0.0.1/32"))
    # urllib keeps one value per header name: write the raw header list.
    conn = http.client.HTTPConnection("127.0.0.1", srv.srv.server_address[1], timeout=10)
    conn.putrequest("GET", "/api/me")
    for k, v in headers:
        conn.putheader(k, v)
    conn.endheaders()
    r = conn.getresponse()
    body = r.read()
    conn.close()
    assert r.status == 400, (r.status, body[:200])
    assert b"more than once" in body


# ---------------------------------------------------------------- a proxy with a shared secret (M4)

def test_a_proxy_secret_is_required_and_checked(vl, tmp_path, run_app):
    f = tmp_path / "proxy-secret"
    f.write_text(SECRET + "\n")
    srv = start(run_app, multi_env(vl, tmp_path, PROXY_SECRET_FILE=str(f)))
    for secret in (None, "", "s" * 39, "x" * 40):
        h = {"X-Forwarded-User": OWNER} | ({"X-Proxy-Auth": secret} if secret is not None else {})
        assert srv.get("/api/me", headers=h)[0] == 403, secret
    code, _, body = srv.get("/api/me", headers={"X-Forwarded-User": OWNER, "X-Proxy-Auth": SECRET})
    assert code == 200 and json.loads(body)["owner"]


def test_secret_and_cidr_together_both_count(vl, tmp_path, run_app):
    f = tmp_path / "proxy-secret"
    f.write_text(SECRET)
    srv = start(run_app, multi_env(vl, tmp_path, PROXY_SECRET_FILE=str(f), PROXY_TRUSTED_CIDRS="198.51.100.0/24"))
    # The right secret from the wrong address: no.
    assert srv.get("/api/me", headers={"X-Forwarded-User": OWNER, "X-Proxy-Auth": SECRET})[0] == 403


# ---------------------------------------------------------------- DNS rebinding (M1)

LOCAL = {"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "open", "MODEL3D": "off"}


@pytest.mark.parametrize("env", [LOCAL, {**LOCAL, "BIND": "0.0.0.0", "PUBLISHED_ON": "127.0.0.1:8080"},
                                 {**LOCAL, "PUBLIC_URL": "https://wow.example.org"}])
def test_a_rebinding_page_reads_nothing(env, run_app):
    from tests.test_public_view import PoisonStore

    srv = run_app(ui.App(env, store=PoisonStore()))
    port = srv.srv.server_address[1]
    for host in (f"evil.example:{port}", "evil.example", f"127.0.0.1.evil.example:{port}", f"localhost.evil:{port}"):
        for path, method, data in (("/api/config", "GET", None), ("/", "GET", None),
                                   ("/api/logsql?q=*", "GET", None), ("/api/plan", "POST", b"{}")):
            code, _, body = srv.get(path, headers={"Host": host, "Content-Type": "text/plain"}, method=method,
                                    data=data)
            assert code == 421, (host, path, code)
            assert b"ALLOWED_HOSTS" in body
    for host in (f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}", "192.0.2.20:8080"):
        assert srv.get("/api/config", headers={"Host": host})[0] == 200, host
    assert srv.get("/healthz", headers={"Host": "evil.example"})[0] == 200  # no data, and probes use pod IPs


def test_the_public_url_and_allowed_hosts_are_answered(run_app):
    from tests.test_public_view import PoisonStore

    env = {**LOCAL, "AUTH": "none", "PUBLIC_URL": "https://wow.example.org/", "ALLOWED_HOSTS": "nas.local, Other.Example:8443"}
    srv = run_app(ui.App(env, store=PoisonStore()))
    for host in ("wow.example.org", "WOW.example.org:443", "nas.local:8080", "other.example"):
        assert srv.get("/api/config", headers={"Host": host})[0] == 200, host
    assert srv.get("/api/config", headers={"Host": "example.org"})[0] == 421
    # The MCP's address is the same server's (it needs a whole OIDC setup to start here).
    assert guard.Hosts({"MCP_PUBLIC_URL": "https://mcp.example.org/mcp"}).ok("mcp.example.org")


# ---------------------------------------------------------------- the share port on a flat network (M3)

@pytest.fixture
def share(tmp_path):
    state = tmp_path / "analysis"
    env = {"ANALYSIS_SHARE_PORT": "0", "ANALYSIS_STATE_DIR": str(state), "BIND": "127.0.0.1"}
    srv = analysis.share_server(env)
    yield env, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_the_share_port_answers_nobody_without_the_token(share):
    env, base = share
    for path in ("/analysis.json", "/analysis.json?slug=mira", "/users.json", "/profile-characters", "/hosts.json"):
        for auth in (None, "Bearer ", "Bearer wrong", "Basic " + "x" * 40):
            req = urllib.request.Request(base + path, headers={"Authorization": auth} if auth else {})
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=10)
            assert e.value.code == 401, (path, auth)


def test_the_share_token_is_made_once_and_kept_private(share, tmp_path):
    env, base = share
    f = guard.share_token_path(env)
    assert f == tmp_path / "analysis" / "share-token"
    token = f.read_text().strip()
    assert len(token) >= 32
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert guard.share_token(env) == token  # a restart keeps it: the publisher's copy stays valid
    with guard.share_open(base + "/analysis.json", {"ANALYSIS_SHARE_TOKEN_FILE": str(f)}) as r:
        assert json.loads(r.read())["available"] is False


def test_a_full_disk_leaves_no_temporary_file_behind(tmp_path, monkeypatch):
    """A write that fails (ENOSPC) leaves nothing that blocks the next
    start: the old share-token.<pid>.tmp stayed, and in a container (pid 1)
    every restart failed on it until someone deleted it."""
    import errno
    import io

    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    real = os.fdopen

    class Full(io.TextIOWrapper):
        def write(self, s):
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(guard.os, "fdopen", lambda fd, *a, **k: Full(real(fd, "wb")))
    with pytest.raises(OSError, match="No space"):
        guard.share_token(env)
    assert sorted(p.name for p in tmp_path.iterdir()) == []
    monkeypatch.undo()
    # A stale temporary file from an older version (pid 1 in a container) doesn't block a start either.
    (tmp_path / "share-token.1.tmp").write_text("")
    token = guard.share_token(env)
    assert len(token) >= 32 and (tmp_path / "share-token").read_text().strip() == token
    assert stat.S_IMODE(os.stat(tmp_path / "share-token").st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["share-token", "share-token.1.tmp"]


def test_a_client_without_the_token_says_what_to_set(share):
    env, base = share
    with pytest.raises(OSError, match="ANALYSIS_SHARE_TOKEN_FILE"):
        guard.share_open(base + "/analysis.json", {})
    # The publisher keeps going without the analysis lines.
    assert publish.fetch_analysis({"ANALYSIS_SHARE_URL": base + "/analysis.json"}) == {"available": False}


def test_a_configured_token_file_is_used_and_made_if_missing(tmp_path):
    f = tmp_path / "secrets" / "share"
    env = {"ANALYSIS_SHARE_PORT": "0", "ANALYSIS_STATE_DIR": str(tmp_path / "a"), "BIND": "127.0.0.1",
           "ANALYSIS_SHARE_TOKEN_FILE": str(f)}
    srv = analysis.share_server(env)
    try:
        assert f.exists() and not (tmp_path / "a" / "share-token").exists()
        with guard.share_open(f"http://127.0.0.1:{srv.server_address[1]}/analysis.json", env) as r:
            assert r.status == 200
    finally:
        srv.shutdown()
        srv.server_close()
