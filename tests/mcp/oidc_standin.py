"""A local OpenID Connect provider for the MCP's OAuth tests: discovery,
JWKS, an authorization endpoint that logs in `user` without asking, a token
endpoint (code with PKCE, refresh), and userinfo with ZITADEL's role and
org claims. The tests change `users` to remove a role, move an account to
another org or switch it off, the way the owner would in ZITADEL.

    with Standin(user="398") as idp:
        idp.issuer  # http://127.0.0.1:<port>
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROLES = "urn:zitadel:iam:org:project:roles"
ORG = "urn:zitadel:iam:user:resourceowner:id"
CLIENT_ID = "wow-app"
CLIENT_SECRET = "standin-secret-0123456789abcdef"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Standin:
    def __init__(self, user: str = "owner-sub", org: str = "org-owner", roles=("user",), port: int = 0):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "k1"
        self.user = user
        # sub -> {"org", "roles", "active"}
        self.users = {user: {"org": org, "roles": list(roles), "active": True}}
        self.codes: dict[str, dict] = {}
        self.access: dict[str, str] = {}
        self.refresh: dict[str, str] = {}
        self.seen: list[dict] = []  # authorize requests
        self.refreshes = 0
        standin = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if u.path == "/.well-known/openid-configuration":
                    return self._json(200, standin.discovery())
                if u.path == "/jwks":
                    return self._json(200, {"keys": [standin.jwk()]})
                if u.path == "/authorize":
                    standin.seen.append(q)
                    code = secrets.token_urlsafe(16)
                    standin.codes[code] = {"sub": standin.user, "nonce": q.get("nonce"),
                                           "challenge": q.get("code_challenge"), "redirect": q.get("redirect_uri"),
                                           "scope": q.get("scope", "")}
                    loc = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q.get("state", "")})
                    self.send_response(302)
                    self.send_header("Location", loc)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                if u.path == "/userinfo":
                    sub = standin.access.get((self.headers.get("Authorization") or "")[7:])
                    if sub is None:
                        return self._json(401, {"error": "invalid_token"})
                    return self._json(200, standin.userinfo(sub))
                return self._json(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
                if urllib.parse.urlsplit(self.path).path != "/token":
                    return self._json(404, {})
                auth = self.headers.get("Authorization") or ""
                cid, _, sec = base64.b64decode(auth[6:]).decode().partition(":") if auth.startswith("Basic ") else (
                    form.get("client_id"), "", form.get("client_secret"))
                if urllib.parse.unquote(cid or "") != CLIENT_ID or urllib.parse.unquote(sec or "") != CLIENT_SECRET:
                    return self._json(401, {"error": "invalid_client"})
                if form.get("grant_type") == "authorization_code":
                    c = standin.codes.pop(form.get("code", ""), None)
                    if c is None or c["redirect"] != form.get("redirect_uri"):
                        return self._json(400, {"error": "invalid_grant"})
                    if c["challenge"]:
                        v = form.get("code_verifier", "")
                        if b64u(hashlib.sha256(v.encode()).digest()) != c["challenge"]:
                            return self._json(400, {"error": "invalid_grant", "error_description": "pkce"})
                    return self._json(200, standin.tokens(c["sub"], c["nonce"], "offline_access" in c["scope"]))
                if form.get("grant_type") == "refresh_token":
                    sub = standin.refresh.pop(form.get("refresh_token", ""), None)
                    if sub is None or not standin.users.get(sub, {}).get("active"):
                        return self._json(400, {"error": "invalid_grant"})
                    standin.refreshes += 1
                    return self._json(200, standin.tokens(sub, None, True))
                return self._json(400, {"error": "unsupported_grant_type"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.issuer = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def discovery(self) -> dict:
        i = self.issuer
        return {"issuer": i, "authorization_endpoint": f"{i}/authorize", "token_endpoint": f"{i}/token",
                "userinfo_endpoint": f"{i}/userinfo", "jwks_uri": f"{i}/jwks", "response_types_supported": ["code"],
                "subject_types_supported": ["public"], "id_token_signing_alg_values_supported": ["RS256"],
                "scopes_supported": ["openid", "offline_access"], "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"]}

    def jwk(self) -> dict:
        n = self.key.public_key().public_numbers()
        return {"kty": "RSA", "kid": self.kid, "use": "sig", "alg": "RS256",
                "n": b64u(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")), "e": b64u(n.e.to_bytes(3, "big"))}

    def sign(self, claims: dict) -> str:
        head = b64u(json.dumps({"alg": "RS256", "typ": "JWT", "kid": self.kid}).encode())
        body = b64u(json.dumps(claims).encode())
        sig = self.key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{b64u(sig)}"

    def userinfo(self, sub: str) -> dict:
        u = self.users.get(sub) or {}
        out = {"sub": sub, ORG: u.get("org", "")}
        if u.get("roles"):
            out[ROLES] = {r: {u.get("org", ""): "example.org"} for r in u["roles"]}
        return out

    def tokens(self, sub: str, nonce: str | None, offline: bool, ttl: int = 3600) -> dict:
        now = int(time.time())
        claims = {"iss": self.issuer, "sub": sub, "aud": CLIENT_ID, "iat": now, "exp": now + ttl}
        if nonce:
            claims["nonce"] = nonce
        at = secrets.token_urlsafe(24)
        self.access[at] = sub
        out = {"access_token": at, "token_type": "Bearer", "expires_in": ttl, "id_token": self.sign(claims),
               "scope": "openid offline_access" if offline else "openid"}
        if offline:
            rt = secrets.token_urlsafe(24)
            self.refresh[rt] = sub
            out["refresh_token"] = rt
        return out

    def public_pem(self) -> bytes:
        return self.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo)
