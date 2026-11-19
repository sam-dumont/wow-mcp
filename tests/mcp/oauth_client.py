"""An MCP client's side of the OAuth login, step by step, for the tests:
register (DCR), authorize with PKCE, approve FastMCP's consent page, follow
the provider (oidc_standin logs in without asking), exchange the code,
refresh. Every step returns the raw response so a test can check refusals."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import urllib.parse

import httpx2

REDIRECT = "http://127.0.0.1:53999/callback"


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Login:
    def __init__(self, base: str, mcp_url: str, redirect: str = REDIRECT):
        self.base, self.mcp_url, self.redirect = base.rstrip("/"), mcp_url, redirect
        self.http = httpx2.Client(follow_redirects=False, timeout=15)
        self.client_id = ""
        self.verifier = ""
        self.tokens: dict = {}

    def close(self):
        self.http.close()

    def register(self, redirect_uris=None, name="wow-stats test") -> httpx2.Response:
        r = self.http.post(f"{self.base}/register", json={
            "redirect_uris": redirect_uris or [self.redirect], "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "client_name": name})
        if r.status_code in (200, 201):
            self.client_id = r.json()["client_id"]
        return r

    def authorize(self, redirect: str | None = None) -> httpx2.Response:
        """Up to the client's redirect: the final 302 (code or error) or the
        response where the flow stopped."""
        self.verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        q = {"response_type": "code", "client_id": self.client_id, "redirect_uri": redirect or self.redirect,
             "code_challenge": challenge, "code_challenge_method": "S256", "state": "st4te",
             "resource": self.mcp_url, "scope": "openid"}
        r = self.http.get(f"{self.base}/authorize?" + urllib.parse.urlencode(q))
        for _ in range(10):
            if r.status_code == 200 and "csrf_token" in r.text:  # FastMCP's consent page
                fields = dict(re.findall(r'name="(txn_id|csrf_token)" value="([^"]*)"', r.text))
                r = self.http.post(f"{self.base}/consent", data={**fields, "submit": "true", "action": "approve"})
                continue
            if r.status_code in (302, 303, 307):
                loc = r.headers["location"]
                if loc.startswith(redirect or self.redirect):
                    return r
                r = self.http.get(urllib.parse.urljoin(str(r.url), loc))
                continue
            return r
        return r

    def code_from(self, r: httpx2.Response) -> str | None:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.headers.get("location", "")).query))
        return q.get("code")

    def exchange(self, code: str) -> httpx2.Response:
        r = self.http.post(f"{self.base}/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect,
            "client_id": self.client_id, "code_verifier": self.verifier, "resource": self.mcp_url})
        if r.status_code == 200:
            self.tokens = r.json()
        return r

    def login(self) -> dict:
        assert self.register().status_code in (200, 201)
        r = self.authorize()
        code = self.code_from(r)
        assert code, (r.status_code, r.headers, r.text[:500])
        r = self.exchange(code)
        assert r.status_code == 200, r.text
        return self.tokens

    def refresh(self) -> httpx2.Response:
        r = self.http.post(f"{self.base}/token", data={
            "grant_type": "refresh_token", "refresh_token": self.tokens.get("refresh_token", ""),
            "client_id": self.client_id, "resource": self.mcp_url})
        if r.status_code == 200:
            self.tokens = r.json()
        return r

    def rpc(self, method: str, params: dict | None = None, token: str | None = None) -> httpx2.Response:
        """One JSON-RPC call on /mcp (legacy handshake first, stateless)."""
        h = {"Authorization": f"Bearer {token or self.tokens.get('access_token', '')}",
             "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        init = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
        r = self.http.post(self.mcp_url, json=init, headers=h)
        if r.status_code != 200:
            return r
        sid = r.headers.get("mcp-session-id")
        if sid:
            h["mcp-session-id"] = sid
        h["mcp-protocol-version"] = "2025-06-18"
        self.http.post(self.mcp_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h)
        return self.http.post(self.mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                                  "params": params or {}}, headers=h)


def result(r: httpx2.Response) -> dict:
    """The JSON-RPC message of a response, JSON or one SSE event."""
    import json

    text = r.text
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        datas = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
        text = datas[-1] if datas else "{}"
    return json.loads(text)
