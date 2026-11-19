"""Several people on one install (wowstats.tenancy): person B never gets one
byte of person A's data, through any page, API route, MCP tool, plan,
analysis call or bundle.

The store is a fake VictoriaLogs that answers by tenant (the AccountID
header, as the real one does): tenant 0 holds the owner's canned events
(tests/mcp/fakes: Testpriest-Fixture, Thrall, Jaina, Twig), tenant 1 the same
events renamed (Testfriend-Otherhall, Varian, Tyrande, Pip). Any request to it
without the tenant headers fails the test. B's answers must hold none of
A's names, whatever B asks for, including A's character by name.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.mcp import fakes
from wowstats import account, ingest, publish, tenancy, ui
from wowstats import mcp as mcpmod
from wowstats.mcp import tools

CHAR_FILTER = re.compile(r'char:=?"?([A-Z][A-Za-z]+-[A-Z][A-Za-z]+)')
RENAME = {"Testpriest": "Testfriend", "Fixture": "Otherhall", "Thrall": "Varian", "Jaina": "Tyrande", "Twig": "Pip"}
A_MARKERS = [k.encode() for k in RENAME] + [k.lower().encode() for k in RENAME]
A_CHAR, B_CHAR = "Testpriest-Fixture", "Testfriend-Otherhall"
OWNER, FRIEND, FRIENDS_ORG = "owner-sub-1", "friend-sub-2", "org-friends"


def renamed(v):
    if isinstance(v, str):
        for a, b in RENAME.items():
            v = v.replace(a, b)
        return v
    if isinstance(v, list | tuple):
        return type(v)(renamed(x) for x in v)
    if isinstance(v, dict):
        return {renamed(k): renamed(x) for k, x in v.items()}
    return v


class TenantVL:
    """VictoriaLogs and VictoriaMetrics on one port, logs per tenant."""

    def __init__(self):
        # The web view's character list asks with counts (the MCP's fakes don't).
        ui_rules = [("stats by (char) count() as events", [{**c, "events": "120"} for c in fakes.CHARS])]
        self.rules = {"0": ui_rules + fakes.LOGS, "1": renamed(ui_rules + fakes.LOGS)}
        assert "Testpriest" not in json.dumps(self.rules["1"])
        self.chars = {"0": {c["char"] for c in fakes.CHARS}, "1": {renamed(c["char"]) for c in fakes.CHARS}}
        self.untagged: list[str] = []
        self.inserted: dict[str, list[dict]] = {}
        self.deleted: list[tuple[str, str]] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _tenant(self) -> str | None:
                acct, proj = self.headers.get("AccountID"), self.headers.get("ProjectID")
                if acct is None or proj != "0":
                    fake.untagged.append(self.path)
                    return None
                return acct

            def _send(self, code, body, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if u.path == "/select/logsql/query":
                    t = self._tenant()
                    if t is None:
                        return self._send(400, b"no tenant")
                    query = q.get("query", "")
                    rows = next((r for s, r in fake.rules.get(t, []) if s in query), [])
                    # A character this tenant doesn't have: no rows, as the real store answers.
                    mine = fake.chars.get(t, set())
                    if any(n not in mine for n in CHAR_FILTER.findall(query)):
                        rows = []
                    return self._send(200, "".join(json.dumps(r) + "\n" for r in rows).encode(),
                                      "application/stream+json")
                if u.path == "/api/v1/query":
                    res = next((r for s, r in fakes.METRICS if s in q.get("query", "")), [])
                    return self._send(200, json.dumps({"status": "success", "data": {"result": res}}).encode())
                if u.path == "/api/v1/query_range":
                    return self._send(200, json.dumps({"status": "success", "data": {"result": []}}).encode())
                self._send(404, b"")

            def do_POST(self):
                u = urllib.parse.urlsplit(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                t = self._tenant()
                if t is None:
                    return self._send(400, b"no tenant")
                if u.path == "/insert/jsonline":
                    fake.inserted.setdefault(t, []).extend(json.loads(x) for x in body.splitlines() if x.strip())
                    return self._send(200, b"")
                if u.path == "/delete/run_task":
                    fake.deleted.append((t, body.decode()))
                    return self._send(200, b'{"task_id":"1"}')
                self._send(404, b"")

        srv = type("S", (ThreadingHTTPServer,), {"request_queue_size": 64, "daemon_threads": True})
        self.httpd = srv(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def vl():
    v = TenantVL()
    yield v
    v.close()
    # The one rule every test checks: no VictoriaLogs request without its tenant.
    assert v.untagged == [], f"VictoriaLogs requests without AccountID/ProjectID: {v.untagged[:5]}"


@pytest.fixture
def env(vl, tmp_path):
    master = tmp_path / "master.key"
    master.write_text(tenancy.b64u(b"m" * 32))
    friends = tmp_path / "friends.json"  # who gets in besides the owner: FRIEND as mira, friend-3 as pip
    friends.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    return {"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "proxy", "PROXY_TRUSTED_CIDRS": "127.0.0.1/32", "VL_URL": vl.url, "VM_URL": vl.url,
            "FRIENDS_VL_URL": vl.url, "WOW_STATE_DIR": str(tmp_path / "state"), "WOW_OWNER_SUB": OWNER,
            "WOW_OWNER_SLUG": "sam", "WOW_OWNER_ALIASES": "wow.m.example", "WOW_FRIENDS_FILE": str(friends),
            "SITE_DOMAIN": "m.example",
            "PUBLIC_URL": "https://wow-full.m.example", "ZOGZOG_MASTER_KEY_FILE": str(master),
            "ZOGZOG_KEY_FILE": str(tmp_path / "owner-zogzog.key"), "MODEL3D": "off",
            "MODEL3D_CACHE_DIR": str(tmp_path / "cache"), "ANALYSIS_STATE_DIR": str(tmp_path / "owner-analysis")}


@pytest.fixture
def server(env, run_app):
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    r.t, r.app = t, app
    yield r


def as_user(sub):
    """What the login proxy sends: the sub, and nothing the app relies on
    besides (2026-11-19: X-Forwarded-Org arrived empty)."""
    return {"X-Forwarded-User": sub}


def friend_ready(server, sub=FRIEND, slug="mira"):
    """A listed friend's first visit: in, with the list's slug."""
    h = as_user(sub)
    code, _, body = server.get("/api/me", headers=h)
    assert code == 200 and json.loads(body)["slug"] == slug, body
    return h


def leaks(body: bytes, path: str = "") -> list[bytes]:
    """A's names in an answer, the request's own words aside (a 404 that
    names the character asked for tells nothing)."""
    for v in urllib.parse.parse_qs(urllib.parse.urlsplit(path).query).values():
        body = body.replace(v[0].encode(), b"")
    body = body.replace(urllib.parse.unquote(path).encode(), b"")
    return [m for m in A_MARKERS if m in body]


ROUTES = ["/", "/start", "/start/print", "/settings", "/api/config", "/api/chars", "/api/me", "/api/me/status",
          "/api/analysis", "/api/market", "/api/prices", "/api/logsql?q=*", "/api/logsql?q=" +
          urllib.parse.quote("char:Testpriest* or *"), "/api/admin/hosts"]
PER_CHAR = ["/api/char?name={c}", "/api/journal?name={c}", "/api/overview?name={c}", "/api/timeline?name={c}",
            "/api/snapshot?name={c}&level=12", "/api/people?name={c}", "/api/loot?name={c}", "/api/gear?name={c}",
            "/api/plan?char={c}", "/render/{c}.png"]


def test_friend_sees_only_their_own_data(server, vl):
    h = friend_ready(server)
    seen = 0
    for path in ROUTES + [p.format(c=c) for p in PER_CHAR for c in (B_CHAR, A_CHAR)]:
        code, _, body = server.get(path, headers=h)
        assert not leaks(body, path), (path, leaks(body, path), body[:300])
        seen += code == 200
    # B's own data is there (the test isn't passing on empty answers)
    code, _, body = server.get(f"/api/char?name={B_CHAR}", headers=h)
    assert code == 200 and b"Testfriend" in body
    code, _, body = server.get(f"/api/char?name={A_CHAR}", headers=h)
    assert code == 404
    code, _, body = server.get("/api/admin/hosts", headers=h)
    assert code == 404  # the owner's only
    assert seen > 15
    # No server-side model for a friend (multi-user design section 8), nor
    # for anyone since 0.4.0: the Coach's route is gone.
    assert server.get("/api/coach", method="POST", data=b"{}", headers=h)[0] == 404


def test_owner_sees_his_own_and_never_the_friends(server):
    friend_ready(server)
    h = as_user(OWNER)
    code, _, body = server.get("/api/chars", headers=h)
    assert code == 200 and b"Testpriest" in body and b"Testfriend" not in body
    code, _, body = server.get(f"/api/char?name={B_CHAR}", headers=h)
    assert code == 404
    code, _, body = server.get("/api/admin/hosts", headers=h)
    hosts = {x["host"] for x in json.loads(body)["hosts"]}
    assert hosts == {"sam-wow.m.example", "wow.m.example", "mira-wow.m.example"}


def test_the_owner_gets_in_by_his_sub_alone(server):
    code, _, body = server.get("/api/me", headers=as_user(OWNER))
    assert code == 200 and json.loads(body)["owner"] is True


def test_a_listed_friend_gets_in_without_any_org(server, vl):
    """Listed, never reconciled, no org header: provisioned at the first
    request, with the list's slug, in their own tenant."""
    assert server.t.registry.get(FRIEND) is None
    friend_ready(server)
    u = server.t.registry.get(FRIEND)
    assert u.slug == "mira" and u.account_id >= 1 and not u.owner
    code, _, body = server.get(f"/api/char?name={B_CHAR}", headers=as_user(FRIEND))
    assert code == 200 and b"Testfriend" in body and not leaks(body)


def test_a_friend_named_like_the_owner_is_a_friend(server):
    # Same display name, listed with the owner's slug: still another person
    # (identity is the sub), in without a slug, and both of his names refused.
    Path(server.t.friends_file).write_text(json.dumps({FRIEND: "mira", "Sam": "sam"}))
    h = as_user("Sam")
    code, _, body = server.get("/api/me", headers=h)
    me = json.loads(body)
    assert code == 200 and me["owner"] is False and me["slug"] == ""
    for slug in ("sam", "wow"):
        code, _, body = server.get("/api/me/slug", headers={**h, "Content-Type": "application/json"},
                                   method="POST", data=json.dumps({"slug": slug}).encode())
        assert code == 400 and b"taken" in body, body
    code, _, body = server.get("/api/chars", headers=h)
    assert not leaks(body)


def test_unlisted_or_no_login_is_refused(server):
    for h in (as_user("someone"), {"X-Forwarded-User": "someone", "X-Forwarded-Org": FRIENDS_ORG}, {}):
        code, _, body = server.get("/api/chars", headers=h)
        assert code in (401, 403) and not leaks(body)
    assert server.t.registry.get("someone") is None
    # The page a refused friend sees: the app's style (from the login-free
    # /privacy/ assets, the only ones they can load), what to do, a way out.
    code, headers, body = server.get("/", headers=as_user("someone"))
    assert code == 403 and headers["Content-Type"].startswith("text/html")
    assert b"This account doesn&#x27;t have access yet. Ask Sam to add you." in body
    assert b'href="/oauth2/logout"' in body and b'href="/privacy/account.css"' in body
    assert b"organisation" not in body and b"<script" not in body and not leaks(body)
    assert server.get("/privacy/account.css", headers=as_user("someone"))[0] == 200


def test_no_list_means_the_owner_only(env, run_app):
    """Without WOW_FRIENDS_FILE nobody provisions themselves: nothing else
    says who may (the org gate is gone)."""
    e = {k: v for k, v in env.items() if k != "WOW_FRIENDS_FILE"}
    t = tenancy.Tenancy(e)
    assert t.identify(OWNER).user.owner
    assert t.identify(FRIEND).status == 403 and t.registry.get(FRIEND) is None


def test_every_mcp_tool_as_the_friend(server):
    friend_ready(server)
    u = server.t.registry.get(FRIEND)
    server.t.app_for(u)
    ctx = server.t.ctx_for(u)
    assert ctx.store.account_id == u.account_id and ctx.bnet is True
    ran = 0
    for tool in tools.mcp_tools(ctx):
        props = tool.input_schema["properties"]
        for args in ({}, {"char": A_CHAR}, {"char": B_CHAR}):
            if any(k not in props for k in args):
                continue
            try:
                out = tools.call(ctx, tool.name, dict(args), tools.mcp_tools(ctx))
            except tools.ToolError as e:
                out = {"error": str(e)}
            except LookupError as e:
                out = {"error": str(e)}
            # The fake store ignores the char filter; the real one finds no
            # rows for A's name in B's tenant. Only the asked name may echo.
            body = json.dumps(out).encode().replace(A_CHAR.encode(), b"")
            assert not leaks(body), (tool.name, args, leaks(body), body[:300])
            ran += 1
    assert ran > 30


def test_mcp_over_http_scopes_and_revokes(server, env, tmp_path):
    """The FastMCP endpoint with the registry: the friend logs in through the
    provider (oidc_standin), is provisioned, sees only their tenant; an
    unlisted sub never gets a token, whatever its org or roles; a listed one
    needs neither; a switch-off ends the calls and the refreshes."""
    from tests.mcp import oauth_client, oidc_standin

    friend_ready(server)
    with oidc_standin.Standin(user=FRIEND, org=FRIENDS_ORG) as idp:
        (tmp_path / "oidc").write_text(oidc_standin.CLIENT_SECRET)
        (tmp_path / "sign").write_text("s" * 40)
        port = oauth_client.free_port()
        ep = mcpmod.endpoint({**env, "MCP_AUTH": "oauth", "MCP_PUBLIC_URL": f"http://127.0.0.1:{port}/mcp",
                              "MCP_PORT": str(port), "OIDC_ISSUER": idp.issuer,
                              "OIDC_CLIENT_ID": oidc_standin.CLIENT_ID,
                              "OIDC_CLIENT_SECRET_FILE": str(tmp_path / "oidc"),
                              "MCP_SIGNING_KEY_FILE": str(tmp_path / "sign"),
                              "MCP_STATE_DIR": str(tmp_path / "mcp-state")}, "127.0.0.1")
        ep.attach_tenancy(server.t)
        run = ep.start()
        try:
            lg = oauth_client.Login(f"http://127.0.0.1:{port}", run.url)
            lg.login()

            def call(name, args):
                r = lg.rpc("tools/call", {"name": name, "arguments": args})
                return r.status_code, json.dumps(oauth_client.result(r)).encode()

            code, body = call("character_summary", {"char": A_CHAR})
            assert code == 200 and not leaks(body.replace(A_CHAR.encode(), b"")), body[:300]
            code, body = call("character_summary", {})
            assert code == 200 and b"Testfriend" in body and not leaks(body), body[:300]
            # Unlisted, with or without the org and the role: each login
            # stops at the code exchange.
            for user, org, roles in (("friend-x", FRIENDS_ORG, ["user"]), ("friend-y", "", [])):
                idp.user = user
                idp.users[user] = {"org": org, "roles": roles, "active": True}
                other = oauth_client.Login(f"http://127.0.0.1:{port}", run.url)
                other.register()
                r = other.exchange(other.code_from(other.authorize()))
                assert r.status_code in (400, 401) and r.json()["error"] == "invalid_grant", (user, r.text)
                assert server.t.registry.get("friend-x") is None and server.t.registry.get("friend-y") is None
            # Listed, no org claim and no role: in, provisioned with the list's slug.
            idp.user = "friend-3"
            idp.users["friend-3"] = {"org": "", "roles": [], "active": True}
            pip = oauth_client.Login(f"http://127.0.0.1:{port}", run.url)
            pip.register()
            r = pip.exchange(pip.code_from(pip.authorize()))
            assert r.status_code == 200, r.text
            assert server.t.registry.get("friend-3").slug == "pip"
            # Switched off: the next call is refused (live revocation, no
            # restart), and so is the next refresh.
            server.t.registry.update(FRIEND, status="disabled")
            r = lg.rpc("tools/call", {"name": "character_summary", "arguments": {}})
            out = oauth_client.result(r)
            assert "error" in out or out["result"].get("isError"), out
            assert b"Testfriend" not in r.content
            assert lg.refresh().status_code in (400, 401)
        finally:
            run.stop()


def test_ingest_lands_in_the_senders_tenant(server, vl):
    friend_ready(server)
    u = server.t.registry.get(FRIEND)
    t = server.t
    code = t.registry.new_code(u.sub)
    got = t.registry.redeem(code)
    assert got is not None and t.registry.redeem(code) is None  # once
    _, token = got
    auth = "Basic " + base64.b64encode(f"mira:{token}".encode()).decode()
    lines = [{"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "char": B_CHAR, "level": 3,
              "password": "x", "nested": {"a": 1}, "_stream": "evil"},
             {"_time": "not a time", "feed": "wow-recorder", "char": B_CHAR}]
    data = "\n".join(json.dumps(x) for x in lines).encode()
    code, _, body = server.get("/ingest", headers={"Authorization": auth}, method="POST", data=data)
    assert code == 200, body
    assert set(vl.inserted) == {str(u.account_id)}
    a, b = vl.inserted[str(u.account_id)]
    assert "password" not in a and "_stream" not in a and a["nested"] == '{"a":1}'
    assert b["feed"] == "wow-invalid" and b["feed_sent"] == "wow-recorder"
    bad = "Basic " + base64.b64encode(f"sam:{token}".encode()).decode()  # the right token, someone else's name
    code, _, _ = server.get("/ingest", headers={"Authorization": bad}, method="POST", data=data)
    assert code == 401


def test_ingest_has_a_daily_budget_per_person(env, run_app, vl):
    """Review L7: a friend's token (or a stolen one) could POST 8 MB batches
    without end into the shared store, kept for years. Each person gets
    INGEST_DAILY_MB a day; past it, 429, and the shipper keeps its spool
    for the next day. Another person's budget is their own."""
    line = json.dumps({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "char": B_CHAR, "level": 3,
                       "note": "x" * 300}).encode()
    e = {**env, "INGEST_DAILY_MB": str(3.5 * len(line) / 1024 / 1024)}  # three of those lines a day
    app = ui.App(e)
    t = ui.tenancy_of(e, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    r.get("/api/me", headers=as_user(FRIEND))
    tokens = {}
    for sub, slug in ((FRIEND, "mira"), (OWNER, "sam")):
        _, tokens[slug] = t.registry.redeem(t.registry.new_code(sub))

    def send(slug):
        auth = "Basic " + base64.b64encode(f"{slug}:{tokens[slug]}".encode()).decode()
        return r.get("/ingest", headers={"Authorization": auth}, method="POST", data=line)[0]

    assert [send("mira") for _ in range(3)] == [200, 200, 200]
    assert send("mira") == 429
    assert send("sam") == 200


def test_installers_carry_the_token_and_never_print_it(server):
    friend_ready(server)
    t = server.t
    u = t.registry.get(FRIEND)
    code = t.registry.new_code(u.sub)
    st, _, sh = server.get(f"/install/{code}", headers={"User-Agent": "curl/8.7.1"})
    assert st == 200 and sh.startswith(b"#!/bin/bash")
    token = re.search(rb'^password = "([A-Za-z0-9_-]{32,})"$', sh, re.M).group(1).decode()
    assert t.registry.check_ingest("mira", token) is not None
    assert b"echo" not in sh.split(token.encode())[0].rsplit(b"\n", 1)[-1]
    st, _, again = server.get(f"/install/{code}", headers={"User-Agent": "curl/8.7.1"})
    assert st == 404  # once
    code = t.registry.new_code(u.sub)
    st, _, ps = server.get(f"/install/{code}", headers={"User-Agent": "Mozilla/5.0 WindowsPowerShell/5.1"})
    assert st == 200 and b"Invoke-RestMethod https://astral.sh/uv/install.ps1" in ps
    assert t.registry.check_ingest("mira", token) is None  # a new code, a new token


@pytest.mark.parametrize("method,ua", [
    ("HEAD", "curl/8.7.1"),  # a link checker
    ("GET", "Slackbot-LinkExpanding 1.0 (+api.slack.com/robots)"),  # their real agents carry a URL
    ("GET", "Mozilla/5.0 (compatible; Discordbot/2.0; +discordapp.com)"),
    ("GET", "facebookexternalhit/1.1;line-poker/1.0"),  # iMessage's preview
    ("GET", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"),
])
def test_a_link_preview_never_burns_the_install_code(server, method, ua):
    """Review L3: a code pasted in a chat is fetched by the chat's unfurler
    (or a HEAD from a link checker). That must not redeem it: the friend's
    own curl or irm right after still gets the installer."""
    friend_ready(server)
    t = server.t
    code = t.registry.new_code(FRIEND)
    st, _, body = server.get(f"/install/{code}", headers={"User-Agent": ua}, method=method)
    assert st == 200 and b"password = " not in body
    st, _, sh = server.get(f"/install/{code}", headers={"User-Agent": "curl/8.7.1"})
    assert st == 200 and re.search(rb'^password = "[A-Za-z0-9_-]{32,}"$', sh, re.M), sh[:200]


def test_bundles_are_per_person(server, env, tmp_path):
    pytest.importorskip("cryptography")
    friend_ready(server)
    t = server.t
    t.registry.update(FRIEND, public_on=True)
    users = t.users_json()["users"]
    fu = next(u for u in users if u["slug"] == "mira")
    e = publish.user_env({**env, "PUBLISH_DIR": str(tmp_path / "site")}, fu)
    key = tenancy.zogzog_key(tenancy.read_master(env), fu["account_id"], 0)
    res = publish.publish(e, force=True, zz_key=key, public=True, slug="mira",
                          canonical="https://mira-wow.m.example")
    assert res["built"]
    root = tmp_path / "site" / "mira" / "current"
    n = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        body = p.read_bytes()
        if p.suffix == ".enc":
            rel = p.relative_to(root / "zogzog").as_posix()[:-4]
            body = publish.unseal(key, urllib.parse.quote(rel, safe="/-_.!~*'()"), body)
        assert not leaks(body), (p, leaks(body))
        n += 1
    assert n > 10
    assert b"Testfriend" in (root / "public" / "data" / "chars.json").read_bytes()
    # The wizard's link opens this bundle: same key.
    assert t.zogzog_link(t.registry.get(FRIEND)).endswith("#" + tenancy.b64u(key))


def test_static_server_maps_hosts_to_slugs(tmp_path):
    from wowstats import static

    for slug in ("sam", "mira"):
        d = tmp_path / slug / "b1" / "public"
        d.mkdir(parents=True)
        (d / "index.html").write_text(slug)
        (tmp_path / slug / "current").symlink_to("b1")
    s = static.Site({"SITE_DIR": str(tmp_path), "SITE_DOMAIN": "m.example", "SITE_ALIASES": "wow.m.example=sam"})
    assert s.resolve("/", "wow.m.example")[0].read_text() == "sam"
    assert s.resolve("/", "sam-wow.m.example:443")[0].read_text() == "sam"
    assert s.resolve("/", "mira-wow.m.example")[0].read_text() == "mira"
    assert s.resolve("/", "other-wow.m.example") is None
    assert s.resolve("/", "evil.example") is None
    assert s.resolve("/../sam/current/public/index.html", "mira-wow.m.example") is None


def test_delete_removes_the_tenant(server, vl):
    friend_ready(server)
    t = server.t
    u = t.registry.get(FRIEND)
    with pytest.raises(tenancy.TenancyError):
        tenancy.delete_tenant(t, t.registry.get(OWNER))
    out = tenancy.delete_tenant(t, u)
    assert vl.deleted == [(str(u.account_id), "filter=%2A")]
    assert t.registry.get(FRIEND) is None and out["slug"] == "mira"
    # A new friend never gets the deleted one's AccountID back.
    h = as_user("friend-3")
    server.get("/api/me", headers=h)
    assert t.registry.get("friend-3").account_id > u.account_id


def test_screen_matches_the_vrl():
    ev = ingest.screen({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "Bad Key": 1, "host": "x",
                        "battletag": "x", "email_x": "y", "ok_field": [1, 2], "_msg": "m" * 5000})
    assert ev == {"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "ok_field": "[1,2]", "component": "wow",
                  "_msg": "m" * 4096}
    # The payload never picks it (the VRL sets it after the filter).
    assert ingest.screen({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "component": "x"})["component"] == "wow"
    assert ingest.screen({"_time": "2026-11-17T15:14:03Z", "feed": "WOW-x"}) is None
    old = ingest.screen({"_time": "1999-01-01T00:00:00Z", "feed": "wow-level"})
    assert old["feed"] == "wow-invalid" and old["ingest_error"] == "_time out of range"


def test_slug_rules(tmp_path):
    r = tenancy.Registry(tmp_path / "u.json")
    r.ensure("a")
    for bad in ("a", "full", "x" * 21, "a-b", "a b", "été"):
        with pytest.raises(tenancy.TenancyError):
            r.set_slug("a", bad)
    assert r.set_slug("a", "frost42").slug == "frost42"
    r.ensure("b")
    with pytest.raises(tenancy.TenancyError, match="taken"):
        r.set_slug("b", "frost42")
    with pytest.raises(tenancy.TenancyError, match="can't change"):
        r.set_slug("a", "other")


def test_account_pages_render():
    live = account.start_page(base="https://wow-full.m.example")
    assert b'data-widget="install"' in live
    for state in (b"new", b"wait", b"none", b"ok"):  # one hero each; account.js shows one
        assert b'class="hero" data-in="' + state + b'"' in live
    assert b"/install/update | bash" in live and b'href="/start/print"' in live
    p = account.start_page(printable=True)
    assert b"data-widget" not in p and b"Polestar Recorder" in p and b"Pick your name" in p


def test_resolve_tenant_for_the_mcp_port(env):
    """The MCP admits by the web view's rule: the sub, the owner or listed."""
    t = tenancy.Tenancy(env)
    u = t.resolve_tenant(FRIEND)  # first login: provisioned
    assert u is not None and u.account_id >= 1 and not u.owner and u.slug == "mira"
    assert t.resolve_tenant(FRIEND).account_id == u.account_id  # same tenant again
    owner = t.resolve_tenant(OWNER)
    assert owner.owner and owner.account_id == 0
    assert t.resolve_tenant("new-one") is None
    assert t.registry.get("new-one") is None  # unlisted: nothing provisioned
    t.registry.update(FRIEND, status="disabled")
    assert t.resolve_tenant(FRIEND) is None
    assert t.resolve_tenant("") is None


def test_install_steps_are_complete_and_actionable():
    """docs/videos/steps.json feeds /start and /help/: stable ids, and every
    step says how to check it and what to do when it didn't work."""
    s = account.steps()
    assert [st["id"] for st in s["steps"]] == ["login", "name", "addons", "install", "play", "see", "ai"]
    for st in s["steps"]:
        assert st["check"] and st["if_not"] and st["audience"] in ("start", "both"), st["id"]
    assert {t["id"] for t in s["troubleshooting"]} >= {"no-data", "no-folder", "antivirus", "client-not-registered"}
    assert all(t["fix"] for t in s["troubleshooting"])
    ids = {a["id"] for a in s["addons"]}
    assert {"polestar", "recorder"} <= {a["id"] for a in s["addons"] if a["required"]}
    assert all(a["dir"] and isinstance(a["feeds"], list) for a in s["addons"])
    for k in s["data"]:  # the missing-data kinds name known addons and say what to do
        assert set(k["addons"]) <= ids and k["signals"] and k["what"] and k["fix"] and k["sections"], k
    for a in s["addons"]:
        assert a["url"] is None or a["url"].startswith("https://"), a
    assert "{code}" in s["steps"][3]["os"]["mac"]["command"] and "{code}" in s["steps"][3]["os"]["windows"]["command"]


def test_update_keeps_the_password_and_reset_revokes_it(server):
    h = friend_ready(server)
    t = server.t
    u = t.registry.get(FRIEND)
    _, token = t.registry.redeem(t.registry.new_code(u.sub))
    t.registry.update(u.sub, ingest_set_at="2026-11-18T00:00:00+00:00")
    curl = {"User-Agent": "curl/8.7.1"}
    st, _, boot = server.get("/install/update", headers=curl)
    assert st == 200 and b"--config -" in boot and token.encode() not in boot  # public, the same for everyone
    st, _, ps = server.get("/install/update", headers={"User-Agent": "WindowsPowerShell/5.1"})
    assert st == 200 and b"/install/update/payload" in ps
    good = "Basic " + base64.b64encode(f"mira:{token}".encode()).decode()
    st, _, sh = server.get("/install/update/payload", headers={**curl, "Authorization": good})
    assert st == 200 and b"wow-stats is updated" in sh
    assert token.encode() not in sh and not re.search(rb'^password = "[^"]+"', sh, re.M)
    assert b'> "$conf"' not in sh  # the config is never rewritten
    st, _, _ = server.get("/install/update/payload", headers={**curl, "Authorization": "Basic " +
                                                             base64.b64encode(b"mira:wrong").decode()})
    assert st == 401
    assert t.registry.check_ingest("mira", token) is not None  # an update changes nothing
    j = {**h, "Content-Type": "application/json"}
    st, _, body = server.get("/api/me/install-code", headers=j, method="POST", data=b"{}")
    assert st == 400 and b"update" in body  # installed: no code without a reset
    st, _, body = server.get("/api/me/install-code", headers=j, method="POST", data=b'{"reset": true}')
    assert st == 200 and b"/install/" in body
    assert t.registry.check_ingest("mira", token) is None  # the reset revoked it at once
    _, _, me = server.get("/api/me", headers=h)
    assert json.loads(me)["update"]["mac"].endswith("/install/update | bash")


LOGIN_FREE = ["/privacy", "/install/update", "/install/abcdefghij", "/healthz"]


def test_login_free_pages_load_nothing_behind_the_login(server):
    """The lab routes /privacy and /install/ around the login: whatever those
    pages load must be reachable there too (an anonymous reader gets 401/403
    from everything else)."""
    friend_ready(server)
    seen, todo = set(), list(LOGIN_FREE)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        for ua in ("Mozilla/5.0", "curl/8.7.1", "WindowsPowerShell/5.1"):
            st, hdr, body = server.get(path, headers={"User-Agent": ua})
            if path == "/install/abcdefghij":
                # a bad code: a script-friendly refusal for curl and irm, the
                # "run it in a terminal" note for anything else; nothing to load
                assert st == (200 if ua == "Mozilla/5.0" else 404), (ua, st)
                continue
            assert st == 200, (path, ua, st)
            text = body.decode("utf-8", "replace")
            refs = re.findall(r'(?:src|href)="(/[^"]*)"', text) + re.findall(r'url\("?(/[^")]+)"?\)', text)
            refs += [u.split("/install/", 1)[1] and "/install/" + u.split("/install/", 1)[1]
                     for u in re.findall(r"\$base(/install/[\w/]+)", text)]
            for ref in refs:
                ref = ref.split("#")[0].split("?")[0]
                if ref and ref.startswith("/") and ref not in ("/", "/start", "/settings", "/privacy"):
                    assert ref.startswith(("/privacy/", "/install/")), (path, "loads", ref)
                    if ref != "/install/update/payload":  # basic auth: the install's own password
                        todo.append(ref)
    assert "/privacy/account.css" in seen and any(s.startswith("/privacy/fonts/") for s in seen)
    # the payload answers to the install's own password, not to a login
    st, _, _ = server.get("/install/update/payload", headers={"User-Agent": "curl/8.7.1"})
    assert st == 401


def test_the_wizard_works_with_the_store_down(env, run_app):
    env = {**env, "FRIENDS_VL_URL": "http://127.0.0.1:9", "VL_URL": "http://127.0.0.1:9", "VM_URL": ""}
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    h = as_user(FRIEND)
    st, _, body = r.get("/api/me", headers=h)
    me = json.loads(body)
    assert st == 200 and me["chars"] == [] and me["update"]["mac"].endswith("/install/update | bash")
    st, _, _ = r.get("/start", headers=h)
    assert st == 200


# ---------------------------------------------------------------- the friends list (WOW_FRIENDS_FILE)


@pytest.fixture
def listed(env, run_app, tmp_path):
    """The full view with a friends list: FRIEND as mira, friend-3 as pip."""
    f = tmp_path / "friends.json"
    f.write_text(json.dumps({FRIEND: "mira", "friend-3": "pip"}))
    e = {**env, "WOW_FRIENDS_FILE": str(f)}
    app = ui.App(e)
    t = ui.tenancy_of(e, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    r.t, r.app, r.file = t, app, f
    yield r


def ingest_as(server, slug, token):
    auth = "Basic " + base64.b64encode(f"{slug}:{token}".encode()).decode()
    line = json.dumps({"_time": "2026-11-17T15:14:03Z", "feed": "wow-level", "char": B_CHAR, "level": 3})
    return server.get("/ingest", headers={"Authorization": auth}, method="POST", data=line.encode())[0]


def test_listed_friends_get_a_tenant_and_nobody_else(listed, vl):
    t = listed.t
    out = tenancy.reconcile(t)
    assert sorted(out["provisioned"]) == ["mira", "pip"] and not out["removed"]
    mira, pip = t.registry.get(FRIEND), t.registry.get("friend-3")
    assert (mira.slug, pip.slug) == ("mira", "pip") and 0 < mira.account_id != pip.account_id
    code, _, body = listed.get("/api/me", headers=as_user(FRIEND))
    assert code == 200 and json.loads(body)["slug"] == "mira"  # ready before the first login
    # Same org, valid login, not on the list: no tenant, nothing written.
    code, _, _ = listed.get("/api/me", headers=as_user("stranger"))
    assert code == 403 and t.registry.get("stranger") is None
    assert t.resolve_tenant("stranger") is None and t.registry.get("stranger") is None
    assert tenancy.reconcile(t)["provisioned"] == []  # a second pass changes nothing
    assert vl.deleted == []


def test_a_friend_who_leaves_the_list_is_offboarded(listed, vl):
    t = listed.t
    tenancy.reconcile(t)
    mira = t.registry.get(FRIEND)
    _, token = t.registry.redeem(t.registry.new_code(FRIEND))
    _, pip_token = t.registry.redeem(t.registry.new_code("friend-3"))
    assert ingest_as(listed, "mira", token) == 200
    (t.user_dir(mira) / "analysis").mkdir(parents=True, exist_ok=True)
    listed.file.write_text(json.dumps({"friend-3": "pip"}))
    out = tenancy.reconcile(t)
    assert out["removed"] == ["mira"] and not out["errors"]
    # Their tenant's events, and only theirs.
    assert vl.deleted == [(str(mira.account_id), "filter=%2A")]
    assert t.registry.get(FRIEND) is None and not t.user_dir(mira).exists()
    assert ingest_as(listed, "mira", token) == 401
    assert listed.get("/api/me", headers=as_user(FRIEND))[0] == 403
    assert t.mcp_user(FRIEND) is None and t.resolve_tenant(FRIEND) is None
    assert [u["slug"] for u in t.users_json()["users"]] == ["sam", "pip"]  # the publisher drops mira's site
    # The other friend and the owner are untouched.
    assert t.registry.get("friend-3").slug == "pip" and ingest_as(listed, "pip", pip_token) == 200
    assert t.registry.get(OWNER).owner and listed.get("/api/me", headers=as_user(OWNER))[0] == 200


@pytest.mark.parametrize("content", [None, "", "not json", "[]", "null", '{"x": "Bad Slug"}', '{"x": 1}'])
def test_an_unusable_list_changes_nothing(listed, vl, content):
    t = listed.t
    tenancy.reconcile(t)
    before = [u.sub for u in t.registry.users()]
    if content is None:
        listed.file.unlink()
    else:
        listed.file.write_text(content)
    assert tenancy.reconcile(t) == {"skipped": True}
    assert [u.sub for u in t.registry.users()] == before and vl.deleted == []
    assert all(u.active for u in t.registry.users())


@pytest.mark.parametrize("content", ["{}", "{}\n", json.dumps({OWNER: "sam"})])
def test_an_empty_list_removes_the_last_friends(listed, vl, content):
    """A valid, parseable {} (Terraform with nobody left) means no friends:
    everyone listed before is offboarded. The old rule read it as a failed
    render and left the last friend active (outside review, 2026-11-19). A
    list naming only the owner is the same: he is never in play."""
    t = listed.t
    tenancy.reconcile(t)
    mira, pip = t.registry.get(FRIEND), t.registry.get("friend-3")
    listed.file.write_text(content)
    out = tenancy.reconcile(t)
    assert not out["skipped"] and sorted(out["removed"]) == ["mira", "pip"] and not out["errors"]
    assert t.registry.get(FRIEND) is None and t.registry.get("friend-3") is None
    assert sorted(vl.deleted) == sorted([(str(mira.account_id), "filter=%2A"), (str(pip.account_id), "filter=%2A")])
    assert listed.get("/api/me", headers=as_user(FRIEND))[0] == 403
    owner = t.registry.get(OWNER)
    assert owner.owner and owner.active and ("0", "filter=%2A") not in vl.deleted


def test_the_owner_is_never_in_play(listed, vl):
    t = listed.t
    assert OWNER not in json.loads(listed.file.read_text())  # absent from the list
    tenancy.reconcile(t)
    owner = t.registry.get(OWNER)
    assert owner.owner and owner.account_id == 0 and owner.active
    listed.file.write_text(json.dumps({OWNER: "other", "friend-3": "pip"}))  # listed by mistake: ignored
    tenancy.reconcile(t)
    owner = t.registry.get(OWNER)
    assert owner.owner and owner.slug == "sam" and owner.active
    assert ("0", "filter=%2A") not in vl.deleted


def test_the_publisher_clears_every_static_pod(tmp_path):
    from wowstats import static

    for slug in ("sam", "mira"):
        (tmp_path / "site" / slug / "b1" / "public").mkdir(parents=True)
        (tmp_path / "site" / slug / "current").symlink_to("b1")
    (tmp_path / "token").write_text("t" * 32)
    site = static.Site({"SITE_DIR": str(tmp_path / "site"), "SITE_DOMAIN": "m.example", "UPLOAD_TOKEN_FILE":
                        str(tmp_path / "token")})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), static.make_upload_handler(site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env = {"PUBLISH_PUSH_URL": f"http://127.0.0.1:{srv.server_address[1]}",
               "PUBLISH_PUSH_TOKEN_FILE": str(tmp_path / "token")}
        publish.unpush_others(env, {"sam"})
    finally:
        srv.shutdown()
        srv.server_close()
    assert sorted(d.name for d in (tmp_path / "site").iterdir()) == ["sam"]


# ---------------------------------------------------------------- the owner's /zogzog/ link


def test_a_new_link_rotates_the_owners_key_too(server, env, tmp_path):
    """"Make a new link" gave the owner the same link back: his key came from
    ZOGZOG_KEY_FILE whatever the generation (outside review, 2026-11-19).
    The file only seeds generation 0; from the first rotation his key is
    derived like a friend's, so the full view and the publisher agree on it
    without sharing a file."""
    (tmp_path / "owner-zogzog.key").write_text(tenancy.b64u(b"o" * 32) + "\n")
    t = server.t
    h = {**as_user(OWNER), "Content-Type": "application/json"}
    assert server.get("/api/me", headers=as_user(OWNER))[0] == 200
    first = t.zogzog_link(t.registry.get(OWNER))
    assert first.endswith("#" + tenancy.b64u(b"o" * 32))
    code, _, body = server.get("/api/me/settings", headers=h, method="POST",
                               data=json.dumps({"new_zogzog_link": True}).encode())
    assert code == 200, body
    owner = t.registry.get(OWNER)
    second = t.zogzog_link(owner)
    assert second and second != first and owner.zogzog_gen == 1
    # The publisher seals the owner's bundle under the key the link carries.
    u = next(x for x in t.users_json()["users"] if x["owner"])
    key = publish.user_key(env, u)
    assert second.endswith("#" + tenancy.b64u(key))
    # And a friend's key is never the owner's.
    friend_ready(server)
    fu = next(x for x in t.users_json()["users"] if x["slug"] == "mira")
    assert publish.user_key(env, fu) != key


def test_no_link_or_key_reaches_a_log(tmp_path, capsys, monkeypatch):
    """The first key and a rotated one are written to their file; the
    publisher's output names the file and never the key or the link."""
    kf = tmp_path / "zogzog.key"
    monkeypatch.setattr(publish, "publish", lambda env, force=False, **k: {"built": True})
    publish.rotate({"PUBLISH_DIR": str(tmp_path / "site"), "ZOGZOG_KEY_FILE": str(kf),
                    "ZOGZOG_LINK_BASE": "https://wow.m.example"})
    key = kf.read_text().strip()
    out = capsys.readouterr()
    assert key not in out.out + out.err and "#" not in out.out + out.err
    assert str(kf) in out.out


@pytest.mark.parametrize(("rng", "code", "body", "crange"), [
    (None, 200, b"0123456789", None),
    ("bytes=0-1", 206, b"01", "bytes 0-1/10"),
    ("bytes=7-", 206, b"789", "bytes 7-9/10"),
    ("bytes=-2", 206, b"89", "bytes 8-9/10"),
    ("bytes=4-99", 206, b"456789", "bytes 4-9/10"),
    ("bytes=12-", 416, b"", "bytes */10"),
    ("bytes=0-1,4-5", 200, b"0123456789", None),  # several ranges: the whole file
])
def test_a_clip_answers_byte_ranges(rng, code, body, crange):
    """Safari plays a <video> only from a server that answers Range with 206."""
    sent = {}

    class H:
        headers = {"Range": rng} if rng else {}

        def send(self, c, b, ctype, cache, headers=None):
            sent.update(code=c, body=b, headers=headers or {})

    account.send_media(H(), b"0123456789", "video/mp4")
    assert (sent["code"], sent["body"]) == (code, body)
    assert sent["headers"].get("Content-Range") == crange
    assert sent["headers"]["Accept-Ranges"] == "bytes"


def test_full_view_reads_each_persons_render_from_their_own_site(env, run_app, tmp_path):
    """The full view has no route to Blizzard: /render/<char>.<ext> comes from
    the static server (RENDER_FROM), which answers by Host. The owner's comes
    from his public bundle (wow.m), a friend whose page isn't public from
    their sealed /zogzog/ copy. Before 2026-11-19 the request named no host:
    a 404 for everyone, and the paperdoll kept its silhouette."""
    pytest.importorskip("cryptography")
    from wowstats import static

    png = b"\x89PNG\r\n\x1a\n" + b"x" * 64
    site_dir = tmp_path / "site"
    own = site_dir / "sam" / "b1" / "public" / "data" / "render"
    own.mkdir(parents=True)
    (own / f"{A_CHAR}.png").write_bytes(png)
    (site_dir / "sam" / "current").symlink_to("b1")
    s = static.Site({"SITE_DIR": str(site_dir), "ZOGZOG_PATH": "/zogzog/", "SITE_DOMAIN": "m.example",
                     "SITE_ALIASES": "wow.m.example=sam"})
    srv = static.Server(("127.0.0.1", 0), static.make_handler(s))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        e = {**env, "RENDER_FROM": f"http://127.0.0.1:{srv.server_address[1]}"}
        app = ui.App(e)
        t = ui.tenancy_of(e, app)
        r = run_app(app)
        r.srv.RequestHandlerClass = ui.make_handler(app, t)
        r.t = t
        h = friend_ready(r)
        # The friend's bundle: /zogzog/ only (their page is off), sealed with their key.
        fu = t.registry.get(FRIEND)
        key = tenancy.person_key(e, fu.account_id, fu.zogzog_gen, False)
        rel = f"data/render/{B_CHAR}.png"
        zz = site_dir / "mira" / "b1" / "zogzog" / "data" / "render"
        zz.mkdir(parents=True)
        (zz / f"{B_CHAR}.png.enc").write_bytes(publish.seal(key, rel, png))
        (site_dir / "mira" / "current").symlink_to("b1")

        code, hdrs, body = r.get(f"/render/{A_CHAR}.png", headers=as_user(OWNER))
        assert (code, body) == (200, png)
        code, _, body = r.get(f"/render/{B_CHAR}.png", headers=h)
        assert (code, body) == (200, png)
        # Nobody reads another person's picture, by name or by site.
        assert r.get(f"/render/{A_CHAR}.png", headers=h)[0] == 404
        assert r.get(f"/render/{B_CHAR}.png", headers=as_user(OWNER))[0] == 404
        # A wrong key opens nothing.
        (zz / f"{B_CHAR}.png.enc").write_bytes(publish.seal(b"k" * 32, rel, png))
        assert r.get(f"/render/{B_CHAR}.png", headers=h)[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("url,ok", [
    ("https://wow-full.m.example", True), ("http://127.0.0.1:8080", True), ("http://localhost:8080", True),
    ("http://[::1]:8080", True), ("", True),
    ("http://wow-full.m.example", False), ("http://192.0.2.10:8080", False), ("ftp://wow.example", False),
])
def test_public_url_is_https_off_loopback(env, url, ok):
    """Review L4: every friend's install and update pipes this server's reply
    into bash or iex, and the shipper sends its password to it. Over plain
    http an on-path attacker swaps the script: refused unless it's this
    machine (a laptop install, the tests)."""
    e = {**env, "PUBLIC_URL": url}
    if ok:
        tenancy.Tenancy(e)
    else:
        with pytest.raises(SystemExit, match="PUBLIC_URL"):
            tenancy.Tenancy(e)


def test_the_privacy_page_says_what_this_install_does():
    """Review L12: the page stated this lab's own operations (Plausible
    counts, backups that expire 30 days later) on every install. Those
    sentences now come from the install's settings; without them the page
    says to ask the owner rather than state something untrue."""
    from wowstats import account

    bare = account.privacy_page(False, {}).decode()
    assert "Plausible" not in bare and "30\ndays later" not in bare and "expire 30" not in bare
    assert "ask the owner" in bare.lower()
    lab = account.privacy_page(False, {"PLAUSIBLE_DOMAIN": "wow.m.example", "PRIVACY_BACKUP_DAYS": "30"}).decode()
    assert "The owner sees visit counts per site (Plausible, no cookies)." in lab
    assert "Backups of your events expire 30 days later." in lab
    assert "ask the owner how long" not in lab.lower()


def test_the_owners_first_key_is_checked_like_the_publishers(tmp_path):
    """Review info 11: the owner's generation-0 key file was used whatever
    its length; publish.read_key refuses anything but 256 bits. The full
    view would show a link that opens nothing the publisher sealed."""
    kf = tmp_path / "zogzog.key"
    kf.write_text(tenancy.b64u(b"k" * 16))
    with pytest.raises(SystemExit, match="256-bit"):
        tenancy.person_key({"ZOGZOG_KEY_FILE": str(kf)}, 0, 0, owner=True)
    kf.write_text(tenancy.b64u(b"k" * 32))
    assert tenancy.person_key({"ZOGZOG_KEY_FILE": str(kf)}, 0, 0, owner=True) == b"k" * 32


def test_the_privacy_page_names_what_people_trust():
    """Review info 9 and 12: the AI assistant sees other players' names (so
    their names reach the person's AI provider), and installing or updating
    runs what this server sends. Both are said on the page."""
    from wowstats import account

    page = account.privacy_page(False, {}).decode()
    assert "names of other players" in page and "AI provider" in page
    assert "runs what this server sends" in page
