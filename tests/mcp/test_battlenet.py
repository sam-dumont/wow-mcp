"""The Battle.net button on the MCP's consent page (wowstats.mcp.consent):
absent without its two settings, and with them the same OAuth flow with
ZITADEL's org and IdP scopes added to that one authorize request, from the
config only. Against the local OpenID Connect provider (oidc_standin)."""

from __future__ import annotations

import urllib.parse

import pytest

from tests.mcp import fakes, oauth_client, oidc_standin
from wowstats import mcp as mcpmod
from wowstats.mcp import consent, server

ORG, IDP = "1000000000000001", "2000000000000002"
BNET = {f"urn:zitadel:iam:org:id:{ORG}", f"urn:zitadel:iam:org:idp:id:{IDP}"}


@pytest.fixture(scope="module")
def stores():
    s = fakes.FakeStores()
    yield s
    s.close()


def _lab(stores, tmp_path, extra: dict):
    idp = oidc_standin.Standin(user="owner-sub").__enter__()
    (tmp_path / "secret").write_text(oidc_standin.CLIENT_SECRET)
    (tmp_path / "key").write_text("k" * 40)
    port = oauth_client.free_port()
    env = {"VL_URL": stores.url, "MCP_PUBLIC_URL": f"http://127.0.0.1:{port}/mcp", "MCP_PORT": str(port),
           "OIDC_ISSUER": idp.issuer, "OIDC_CLIENT_ID": oidc_standin.CLIENT_ID,
           "OIDC_CLIENT_SECRET_FILE": str(tmp_path / "secret"), "OIDC_ALLOWED_SUBS": "owner-sub",
           "OIDC_SCOPES": "openid offline_access",
           "MCP_SIGNING_KEY_FILE": str(tmp_path / "key"), "MCP_STATE_DIR": str(tmp_path / "state"), **extra}
    run = mcpmod.endpoint(env, "127.0.0.1").start()
    return idp, run, oauth_client.Login(f"http://127.0.0.1:{port}", run.url)


@pytest.fixture
def plain(stores, tmp_path):
    idp, run, lg = _lab(stores, tmp_path, {})
    yield idp, run, lg
    lg.close()
    run.stop()
    idp.__exit__(None, None, None)


@pytest.fixture
def bnet(stores, tmp_path):
    idp, run, lg = _lab(stores, tmp_path, {server.BATTLENET_ORG_ENV: f" {ORG} ", server.BATTLENET_IDP_ENV: IDP})
    yield idp, run, lg
    lg.close()
    run.stop()
    idp.__exit__(None, None, None)


def test_the_two_settings_go_together():
    assert server.battlenet_scopes({}) == ()
    assert server.battlenet_scopes({server.BATTLENET_ORG_ENV: " ", server.BATTLENET_IDP_ENV: ""}) == ()
    assert set(server.battlenet_scopes({server.BATTLENET_ORG_ENV: f" {ORG}", server.BATTLENET_IDP_ENV: IDP})) == BNET
    for bad in ({server.BATTLENET_ORG_ENV: ORG}, {server.BATTLENET_IDP_ENV: IDP},
                {server.BATTLENET_ORG_ENV: ORG, server.BATTLENET_IDP_ENV: "x y"},
                {server.BATTLENET_ORG_ENV: "1 urn:evil", server.BATTLENET_IDP_ENV: IDP}):
        with pytest.raises(SystemExit):
            server.battlenet_scopes(bad)


def test_no_button_without_the_settings_and_the_query_changes_nothing(plain):
    idp, run, lg = plain
    lg.register()
    r = lg.authorize(consent_query="?login=battlenet")
    assert lg.code_from(r)
    assert "formaction" not in lg.consent_page and "Sign in with Battle.net" not in lg.consent_page
    assert "--gold" in lg.consent_page  # wow-stats' look either way
    assert set(idp.seen[-1]["scope"].split()) == {"openid", "offline_access"}


def test_the_button_is_inside_the_consent_form(bnet):
    idp, run, lg = bnet
    lg.register()
    lg.authorize()
    page = lg.consent_page
    form = page[page.index('<form id="consentForm"'):page.index("</form>")]
    assert 'formaction="?login=battlenet"' in form and "Sign in with Battle.net" in form
    assert 'name="csrf_token"' in form and 'value="approve" class="btn-approve"' in form
    # The page's CSP is FastMCP's, untouched: no script, no font source.
    csp = page[page.index('http-equiv="Content-Security-Policy"'):].split('"')[3]
    assert csp.startswith("default-src &#x27;none&#x27;") or csp.startswith("default-src 'none'")
    assert "script-src" not in csp and "font-src" not in csp


def test_allow_keeps_the_normal_login(bnet):
    idp, run, lg = bnet
    lg.login()
    assert set(idp.seen[-1]["scope"].split()) == {"openid", "offline_access"}


def test_allow_asks_which_account_battlenet_doesnt(bnet):
    # Allow after a Battle.net sign-in in the same browser: the provider must
    # ask which account, not reuse its newest session (the Battle.net one).
    idp, run, lg = bnet
    lg.login()
    assert idp.seen[-1]["prompt"] == server.SELECT_ACCOUNT
    lg.authorize(consent_query="?login=battlenet")
    assert set(idp.seen[-1]["scope"].split()) >= BNET and "prompt" not in idp.seen[-1]


def test_without_the_button_allow_asks_which_account_too(plain):
    idp, run, lg = plain
    lg.login()
    assert idp.seen[-1]["prompt"] == server.SELECT_ACCOUNT


def test_battlenet_adds_exactly_the_two_scopes_and_round_trips(bnet):
    idp, run, lg = bnet
    assert lg.register().status_code == 201
    r = lg.authorize(consent_query="?login=battlenet")
    seen = idp.seen[-1]
    assert set(seen["scope"].split()) == {"openid", "offline_access"} | BNET
    assert seen["scope"].split()[:2] == ["openid", "offline_access"]
    # FastMCP's own state and PKCE towards the provider, untouched.
    assert seen["state"] and seen["code_challenge"] and seen["code_challenge_method"] == "S256"
    assert seen["redirect_uri"].endswith("/oauth/callback")
    loc = urllib.parse.urlsplit(r.headers["location"])
    assert loc.geturl().startswith(oauth_client.REDIRECT)
    assert dict(urllib.parse.parse_qsl(loc.query))["state"] == "st4te"  # the client's own state comes back
    assert lg.exchange(lg.code_from(r)).status_code == 200
    out = oauth_client.result(lg.rpc("tools/call", {"name": "character_summary", "arguments": {}}))["result"]
    assert not out["isError"]


def test_nothing_from_the_request_reaches_the_scopes(bnet):
    idp, run, lg = bnet
    lg.register()
    lg.authorize(consent_query="?login=battlenet&scope=urn:zitadel:iam:org:id:666")
    assert set(idp.seen[-1]["scope"].split()) == {"openid", "offline_access"} | BNET
    for other in ("?login=Battlenet", "?login=battlenet&login=other", "?login=urn:zitadel:iam:org:id:666"):
        lg.authorize(consent_query=other)
        assert set(idp.seen[-1]["scope"].split()) == {"openid", "offline_access"}, other


def test_the_battlenet_post_still_needs_the_forms_csrf(bnet):
    idp, run, lg = bnet
    lg.register()
    r = lg.http.get(f"{lg.base}/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": lg.client_id, "redirect_uri": lg.redirect,
        "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s"}))
    page = lg.http.get(urllib.parse.urljoin(str(r.url), r.headers["location"])).text
    txn = page.split('name="txn_id" value="')[1].split('"')[0]
    r = lg.http.post(f"{lg.base}/consent?login=battlenet",
                     data={"txn_id": txn, "csrf_token": "forged", "submit": "true", "action": "approve"})
    assert r.status_code == 400 and not any(set(s.get("scope", "").split()) & BNET for s in idp.seen)


def test_an_unknown_page_is_left_as_fastmcp_built_it():
    assert consent.restyle("<html><head></head><body>other</body></html>", battlenet=True) == (
        "<html><head></head><body>other</body></html>", False)
    page, ok = consent.restyle("<html><head></head><body>other</body></html>", battlenet=False)
    assert ok and "--gold" in page
