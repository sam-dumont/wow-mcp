"""The MCP endpoint on FastMCP (wowstats.mcp.server): the registry, the
tools and prompts as a client gets them, and the OAuth login against a local
OpenID Connect provider (oidc_standin), with the checks that are ours: who may
log in, the re-check at each refresh, the CIMD hosts, the redirects, the
body cap and the Origin guard."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.server.auth.redirect_validation import matches_allowed_pattern

from tests.mcp import fakes, inmem, oauth_client, oidc_standin, proxy
from wowstats import __version__, dataversions, ui
from wowstats import mcp as mcpmod
from wowstats.mcp import prompts, server, tools
from wowstats.mcp.store import Store


@pytest.fixture(scope="module")
def stores():
    s = fakes.FakeStores()
    yield s
    s.close()


@pytest.fixture
def ctx(stores):
    return tools.Ctx(store=Store(vl_url=stores.url))


# ---------------------------------------------------------------- registry, tools, prompts


def test_registry_is_tools_and_prompts(ctx):
    async def go():
        async with Client(inmem.build(ctx)) as c:
            assert c.server_info.version == __version__
            status = await c.call_tool("data_status", {})
            assert status.structured_content == dataversions.status()
            return await c.list_tools(), await c.list_prompts()

    listed, ps = asyncio.run(go())
    assert [t.name for t in listed] == [t.name for t in tools.TOOLS]  # market hidden, order kept
    for t in listed:
        spec = tools.BY_NAME[t.name]
        assert t.description == spec.description and t.title == spec.title
        assert t.input_schema == spec.input_schema
        # Read-only only for the tools that write nothing (test_annotations.py).
        assert t.annotations.read_only_hint is spec.read_only and t.annotations.open_world_hint is spec.open_world
    assert [p.name for p in ps] == [p["name"] for p in prompts.PROMPTS]
    assert {a.name for a in next(p for p in ps if p.name == "coach").arguments} == {"char", "focus"}


def test_help_page_reads_the_same_registry():
    from wowstats.mcp import helppage

    served, ps = helppage.registry()
    assert [t.name for t in served] == [t.name for t in tools.TOOLS]
    assert served[0].input_schema == tools.TOOLS[0].input_schema
    assert [p["name"] for p in ps] == [p["name"] for p in prompts.PROMPTS]
    assert ps[1]["arguments"][0]["name"] == "hours"


def test_every_tool_answers_or_says_why(ctx):
    for t in tools.TOOLS:
        r = inmem.call(ctx, t.name, {})
        if r.is_error:
            assert r.content[0].text and "failed inside the server" not in r.content[0].text, (t.name, r.content)
        else:
            assert json.loads(r.content[0].text) == r.structured_content, t.name


def test_bad_arguments_are_the_callers(ctx):
    r = inmem.call(ctx, "levels", {"bogus": 1})
    assert r.is_error and "unknown argument(s): bogus" in r.content[0].text
    # A run's tools name their character every time (#11): without it, the caller's error.
    r = inmem.call(ctx, "play_sql", {"query": "select 1"})
    assert r.is_error and "char is required" in r.content[0].text


def test_prompts_render(ctx):
    async def go():
        async with Client(inmem.build(ctx)) as c:
            return await c.get_prompt("plan", {"hours": "12"}), await c.get_prompt("coach", {"char": "Testpriest"})

    plan, coach = asyncio.run(go())
    assert "plan_session with hours=8.0" in plan.messages[0].content.text  # clamped like before
    assert "Coach the character Testpriest." in coach.messages[0].content.text


def test_redirect_cases_against_fastmcps_matcher():
    """The clients' redirect table (redirect_cases.py): 19 real redirects pass,
    8 lookalikes don't, with FastMCP's own matcher and our default list."""
    from tests.mcp.redirect_cases import CASES

    allow = list(server.DEFAULT_REDIRECT_URIS)
    wrong = [(n, u, e) for n, u, e in CASES if any(matches_allowed_pattern(u, p) for p in allow) != e]
    assert sum(e for *_, e in CASES) == 19 and sum(not e for *_, e in CASES) == 8
    assert wrong == []


# ---------------------------------------------------------------- settings


def test_settings_refuse_what_cant_work(tmp_path):
    base = {"MCP_PUBLIC_URL": "https://wow.example/mcp", "OIDC_ISSUER": "https://idp.example",
            "OIDC_CLIENT_ID": "x", "OIDC_CLIENT_SECRET": "y"}
    with pytest.raises(SystemExit, match="MCP_PUBLIC_URL"):
        server.Settings.from_env({**base, "MCP_PUBLIC_URL": "http://wow.example/mcp"})
    with pytest.raises(SystemExit, match="OIDC_ISSUER"):
        server.Settings.from_env({**base, "OIDC_ISSUER": "http://idp.example"})
    with pytest.raises(SystemExit, match="passphrase"):
        server.Settings.from_env({**base, "MCP_LOGIN": "password"})
    (tmp_path / "k").write_text("short")
    with pytest.raises(SystemExit, match="32 random bytes"):
        server.Settings.from_env({**base, "MCP_SIGNING_KEY_FILE": str(tmp_path / "k")})
    with pytest.raises(SystemExit, match="non-loopback"):
        mcpmod.endpoint({"MCP_AUTH": "none"}, "0.0.0.0")
    assert mcpmod.endpoint({}, "127.0.0.1") is None
    with pytest.raises(SystemExit, match="OIDC_ALLOWED_SUBS"):
        server.auth_from_env(server.Settings.from_env(base), server.Who(base=None))


def test_the_ui_reports_the_endpoint_and_checks_it_at_start():
    app = ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MCP_AUTH": "none", "VL_URL": "http://vl.invalid"})
    assert app.mcp is not None and app.mcp.port == 8090 and app.mcp.path == "/mcp"
    assert ui.App({"VIEW": "public", "BIND": "127.0.0.1", "MCP_AUTH": "none"}).mcp is None


# ---------------------------------------------------------------- the OAuth login, end to end


@pytest.fixture
def lab_env():
    """Settings a test adds to the lab's (parametrize it by this name)."""
    return {}


@pytest.fixture
def lab(stores, tmp_path, lab_env):
    """The endpoint with the OIDC proxy, one person (OIDC_ALLOWED_SUBS)."""
    with oidc_standin.Standin(user="owner-sub") as idp:
        (tmp_path / "secret").write_text(oidc_standin.CLIENT_SECRET)
        (tmp_path / "key").write_text("k" * 40)
        port = oauth_client.free_port()
        env = {"VL_URL": stores.url, "MCP_PUBLIC_URL": f"http://127.0.0.1:{port}/mcp", "MCP_PORT": str(port),
               "OIDC_ISSUER": idp.issuer, "OIDC_CLIENT_ID": oidc_standin.CLIENT_ID,
               "OIDC_CLIENT_SECRET_FILE": str(tmp_path / "secret"), "OIDC_ALLOWED_SUBS": "owner-sub",
               "OIDC_REQUIRED_ROLE": "user", "OIDC_ALLOWED_ORGS": "org-owner",
               "MCP_SIGNING_KEY_FILE": str(tmp_path / "key"), "MCP_STATE_DIR": str(tmp_path / "state"),
               "MCP_CIMD_HOSTS": "claude.ai", **lab_env}
        ep = mcpmod.endpoint(env, "127.0.0.1")
        run = ep.start()
        lg = oauth_client.Login(f"http://127.0.0.1:{port}", run.url)
        try:
            yield idp, run, lg
        finally:
            lg.close()
            run.stop()


def test_login_call_refresh(lab):
    idp, run, lg = lab
    meta = lg.http.get(f"{lg.base}/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"] and meta["client_id_metadata_document_supported"]
    prm = lg.http.get(f"{lg.base}/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == run.url
    assert lg.http.post(run.url, json={}).status_code == 401  # no token
    lg.login()
    scope = idp.seen[-1]["scope"].split()
    assert {"openid", "offline_access", "urn:zitadel:iam:user:resourceowner"} <= set(scope)
    assert idp.seen[-1]["redirect_uri"].endswith("/oauth/callback")  # the OIDC app's redirect, unchanged
    r = lg.rpc("tools/call", {"name": "character_summary", "arguments": {}})
    out = oauth_client.result(r)["result"]
    assert not out["isError"] and out["structuredContent"]["char"] == fakes.CHAR
    first = lg.tokens["refresh_token"]
    assert lg.refresh().status_code == 200 and idp.refreshes == 1  # upstream refreshed too
    old = lg.tokens
    lg.tokens = {"refresh_token": first}
    assert lg.refresh().status_code in (400, 401)  # an old refresh token is spent
    lg.tokens = old
    assert len(oauth_client.result(lg.rpc("tools/list"))["result"]["tools"]) == len(tools.TOOLS)


def test_a_role_removed_in_the_provider_ends_the_login_at_the_next_refresh(lab):
    idp, run, lg = lab
    lg.login()
    idp.users["owner-sub"]["roles"] = []
    r = lg.refresh()
    assert r.status_code in (400, 401) and r.json()["error"] == "invalid_grant"
    idp.users["owner-sub"]["roles"] = ["user"]
    assert lg.refresh().status_code in (400, 401)  # that refresh token is gone for good


def test_the_transparent_refresh_rechecks_too(lab):
    idp, run, lg = lab
    lg.login()
    auth = run.mcp.auth

    async def go():
        jti = auth.jwt_issuer.verify_token(lg.tokens["access_token"])["jti"]
        mapping = await auth._jti_mapping_store.get(key=jti)
        uts = await auth._upstream_token_store.get(key=mapping.upstream_token_id)
        idp.users["owner-sub"]["org"] = "org-elsewhere"
        with pytest.raises(Exception, match="organisation"):
            await auth._try_transparent_refresh(uts)
        return await auth._upstream_token_store.get(key=mapping.upstream_token_id)

    assert asyncio.run(go()) is None  # nothing left to validate the next call with


def test_someone_else_never_gets_a_token(lab):
    idp, run, lg = lab
    idp.user = "stranger"
    idp.users["stranger"] = {"org": "org-owner", "roles": ["user"], "active": True}
    lg.register()
    r = lg.exchange(lg.code_from(lg.authorize()))
    assert r.status_code in (400, 401) and "may not use" in r.json()["error_description"]


def test_cimd_hosts_and_redirects_are_checked(lab, capsys):
    idp, run, lg = lab
    lg.client_id = "https://evil.example/oauth/client.json"
    r = lg.authorize()
    assert r.status_code == 400
    assert '"result":"host_not_allowed","host":"evil.example"' in capsys.readouterr().out
    r = lg.register(redirect_uris=["https://evil.example/cb"])
    assert r.status_code == 400
    r = lg.register(redirect_uris=["http://127.0.0.1:5555/callback"])
    assert r.status_code == 201


@pytest.mark.parametrize("lab_env", [{"MCP_DCR": "loopback"}])
def test_dcr_loopback_registers_local_clients_only(lab, capsys):
    idp, run, lg = lab
    r = lg.register(redirect_uris=["https://claude.ai/api/mcp/auth_callback"])
    assert r.status_code == 400 and "local clients only" in r.json()["error_description"]
    assert '"result":"registration_refused","mode":"loopback"' in capsys.readouterr().out
    assert lg.register(redirect_uris=["http://127.0.0.1:5555/callback"]).status_code == 201


@pytest.mark.parametrize(("lab_env", "advertised"), [({}, True), ({"MCP_DCR": "loopback"}, True),
                                                     ({"MCP_DCR": "off"}, False)])
def test_registration_endpoint_only_when_dcr_takes_someone(lab, advertised):
    idp, run, lg = lab
    meta = lg.http.get(f"{lg.base}/.well-known/oauth-authorization-server").json()
    assert ("registration_endpoint" in meta) is advertised and meta["client_id_metadata_document_supported"]
    prm = lg.http.get(f"{lg.base}/.well-known/oauth-protected-resource/mcp").json()
    assert not {k for k in prm if "regist" in k}


@pytest.mark.parametrize("lab_env", [{"MCP_DCR": "off"}])
def test_dcr_off_leaves_cimd(lab):
    idp, run, lg = lab
    assert lg.register(redirect_uris=["http://127.0.0.1:5555/callback"]).status_code == 404
    # A client id from before (or a typo) isn't sent to /register either.
    lg.client_id = "stale-dcr-client"
    r = lg.authorize()
    assert r.status_code == 400 and "link" not in r.headers
    body = r.json()
    assert "registration_endpoint" not in body and "client metadata URL" in body["error_description"]
    assert "Client Not Registered" in lg.http.get(r.request.url, headers={"Accept": "text/html"}).text
    lg.client_id = "https://evil.example/oauth/client.json"
    assert lg.authorize().status_code == 400  # CIMD still checks its hosts


def test_body_cap_and_origin(lab):
    idp, run, lg = lab
    big = b"x" * (server.MAX_OTHER_BODY + 1)
    assert lg.http.post(f"{lg.base}/register", content=big,
                        headers={"Content-Type": "application/json"}).status_code == 413
    r = lg.http.post(run.url, json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["message"] == "origin not allowed"


# ---------------------------------------------------------------- the login through a reverse proxy

# The paths a client calls from its own server, and the pages the person's
# browser walks through (docs/mcp.md): a proxy may keep cookies off the
# first, never off the second.
API_PATHS = {"/register", "/token", "/revoke", "/mcp"}
BROWSER_PATHS = {"/authorize", "/consent", "/oauth/callback"}


@pytest.fixture
def proxied(stores, tmp_path):
    """The endpoint behind a reverse proxy (MCP_PUBLIC_URL is the proxy's),
    one person; yields a function that starts the proxy with the paths whose
    cookies it drops."""
    with oidc_standin.Standin(user="owner-sub") as idp:
        (tmp_path / "secret").write_text(oidc_standin.CLIENT_SECRET)
        (tmp_path / "key").write_text("k" * 40)
        port, front = oauth_client.free_port(), oauth_client.free_port()
        env = {"VL_URL": stores.url, "MCP_PUBLIC_URL": f"http://127.0.0.1:{front}/mcp", "MCP_PORT": str(port),
               "OIDC_ISSUER": idp.issuer, "OIDC_CLIENT_ID": oidc_standin.CLIENT_ID,
               "OIDC_CLIENT_SECRET_FILE": str(tmp_path / "secret"), "OIDC_ALLOWED_SUBS": "owner-sub",
               "OIDC_SCOPES": "openid offline_access",
               "MCP_SIGNING_KEY_FILE": str(tmp_path / "key"), "MCP_STATE_DIR": str(tmp_path / "state")}
        run = mcpmod.endpoint(env, "127.0.0.1").start()
        opened = []

        def behind(strip_cookies):
            px = proxy.Proxy(f"http://127.0.0.1:{port}", front, strip_cookies).__enter__()
            lg = oauth_client.Login(px.url, f"{px.url}/mcp")
            opened.append((px, lg))
            return px, lg

        try:
            yield behind
        finally:
            for px, lg in opened:
                lg.close()
                px.__exit__(None, None, None)
            run.stop()


def test_the_whole_login_through_a_proxy_that_keeps_cookies_off_the_api_calls(proxied):
    px, lg = proxied(API_PATHS)
    lg.login()
    assert len(oauth_client.result(lg.rpc("tools/list"))["result"]["tools"]) == len(tools.TOOLS)
    walked = [(m, p) for m, p, _ in px.seen]
    for step in [("POST", "/register"), ("GET", "/authorize"), ("GET", "/consent"), ("POST", "/consent"),
                 ("GET", "/oauth/callback"), ("POST", "/token"), ("POST", "/mcp")]:
        assert step in walked, (step, px.seen)


def test_a_proxy_that_strips_cookies_on_the_browser_pages_breaks_the_consent(proxied):
    # FastMCP binds the consent form to the browser with a cookie; without it
    # the Approve click is refused (claude.ai's connector, 2026-11-19).
    px, lg = proxied(API_PATHS | BROWSER_PATHS)
    assert lg.register().status_code == 201
    r = lg.authorize()
    assert r.status_code == 403 and "Authorization session mismatch" in r.text
    assert ("POST", "/consent", 403) in px.seen


def test_a_registration_flood_never_locks_clients_out(lab, monkeypatch, tmp_path):
    """Review L9: /register is open to anyone, and the cap refused every new
    client once a script had filled it, for a day, again the next day. Now a
    full list makes room by dropping the oldest registration that never
    logged in; one that did is kept, across a restart too."""
    idp, run, lg = lab
    monkeypatch.setattr(server, "MAX_CLIENTS", 5)
    lg.login()  # a real client: registered, then logged in
    mine = lg.client_id
    for i in range(12):  # the flood
        assert lg.http.post(f"{lg.base}/register", json={"redirect_uris": ["http://127.0.0.1:5555/callback"],
                                                       "client_name": f"flood {i}"}).status_code == 201
    (clients,) = (tmp_path / "state").rglob(server.CLIENTS_COLLECTION)
    names = {p.stem for p in clients.glob("*.json")}
    assert len(names) <= 5 and any(mine in n for n in names), names


def test_logged_in_clients_are_remembered_across_a_restart(tmp_path):
    k = server.StateKeeper(store=None, state_dir=tmp_path)
    k.mark_used("client-a")
    assert "client-a" in server.StateKeeper(store=None, state_dir=tmp_path).used
