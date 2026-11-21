"""The MCP endpoint on FastMCP, with its OIDC proxy.

FastMCP serves the MCP transport (Streamable HTTP, both protocol eras) and
the OAuth side: its OIDC proxy registers the MCP clients (DCR, CIMD), sends
the person to the identity provider (one fixed OIDC app)
and hands the client its own tokens. Nothing of it is hand-written here; this
module wires the tools and prompts in and adds the checks that are ours:

- Who may log in (`Gate`): at the code exchange and at every refresh (the
  client's, and FastMCP's own transparent one), the provider's userinfo is
  read with the fresh upstream access token. With several people, the sub
  alone and the web view's rule: the owner or the friends list
  (wowstats.tenancy.resolve_tenant, which provisions a listed person at
  their first login). With one person per install: the project role
  (OIDC_REQUIRED_ROLE, from userinfo: some providers, ZITADEL among them, put no role in the ID token),
  the org (OIDC_ALLOWED_ORGS, the `resourceowner` scope's claim) and
  OIDC_ALLOWED_SUBS; a grant removed in the provider ends the login at the
  next refresh.
- Every call (`AuthMiddleware`, the allowed-subs check): the token's
  `sub` must still be a live person of the registry, or in
  OIDC_ALLOWED_SUBS. A person switched off loses access on their next call.
- The tools run on the caller's own data: the tenant's context
  (wowstats.tenancy.ctx_for), or the install's one.

The server runs on its own port (MCP_PORT, 8090) beside the UI's, in the same
process: a reverse proxy routes /mcp and the OAuth paths there (ROUTES).
docs/mcp.md has the settings.
"""

from __future__ import annotations

import os

# FastMCP asks PyPI for a newer release at start; versions move through
# Dependabot, and a locked-down egress would drop the call anyway.
os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")

import asyncio  # noqa: E402
import base64  # noqa: E402
import contextvars  # noqa: E402
import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit  # noqa: E402

import anyio  # noqa: E402
from fastmcp import FastMCP  # noqa: E402
from fastmcp.prompts import Message, Prompt, PromptResult  # noqa: E402
from fastmcp.prompts.base import PromptArgument  # noqa: E402
from fastmcp.server.dependencies import get_access_token  # noqa: E402
from fastmcp.server.middleware import AuthMiddleware, Middleware  # noqa: E402
from fastmcp.tools import Tool, ToolResult  # noqa: E402
from fastmcp.utilities.authorization import AuthContext  # noqa: E402
from mcp.types import TextContent, ToolAnnotations  # noqa: E402
from pydantic import PrivateAttr  # noqa: E402

from . import consent, prompts, tools, wowhead  # noqa: E402

log = logging.getLogger("wowstats.mcp")

NAME = "wow-stats"
INSTRUCTIONS = ("Leveling stats for a WoW Forever (Classic-era) character recorded from the game client and the "
                "Polestar addon. Start with character_summary; use the coach prompt to plan the next session. "
                "Everything you show the player says what to do: what, where, what blocks it, the next step; "
                "a fact with nothing to do about it is an observation, said apart. Refer to the character by name, "
                "as \"the character\" or \"you\", never he or she. "
                "Rows with a game id (item, quest, npc, spell, zone) carry `wowhead`, its Wowhead Forever page: "
                "link names with it rather than building URLs.")

DEFAULT_PORT = 8090
# The paths FastMCP answers besides /mcp: a reverse proxy in front routes
# these to MCP_PORT, without the page's own login (docs/mcp.md).
ROUTES = ("/mcp", "/authorize", "/token", "/register", "/revoke", "/consent", "/oauth/callback",
          "/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource")
# The provider's callback: the path the old server used, so the OIDC app's
# redirect list doesn't change.
CALLBACK_PATH = "/oauth/callback"

# Where a client may send the browser back after the login: the known
# clients' web callbacks (read 2026-11-18), loopback last.
# MCP_REDIRECT_URIS replaces it.
DEFAULT_REDIRECT_URIS = (
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://insiders.vscode.dev/redirect",
    "https://vscode.dev/redirect",
    "https://www.cursor.com/agents/mcp/oauth/callback",
    "cursor://anysphere.cursor-mcp/oauth/callback",
    "http://127.0.0.1",
    "http://localhost",
    "http://[::1]",
)
# Hosts whose client metadata documents (CIMD) the server fetches, from the
# same list: Claude, ChatGPT/Codex, opencode, VS Code, Copilot CLI, Zed,
# Goose. A URL client id on another host is refused before any fetch.
# MCP_CIMD_HOSTS replaces it.
DEFAULT_CIMD_HOSTS = ("claude.ai", "claude.com", "chatgpt.com", "opencode.ai", "vscode.dev", "insiders.vscode.dev",
                      "github.com", "zed.dev", "goose-docs.ai")

ORG_CLAIM = "urn:zitadel:iam:user:resourceowner:id"
ROLES_CLAIM = "urn:zitadel:iam:org:project:roles"
ORG_SCOPE = "urn:zitadel:iam:user:resourceowner"
# The Battle.net button on the consent page (wowstats.mcp.consent): ZITADEL's
# reserved scopes for one org and one identity provider. With both, its login
# skips its own pages and starts that provider at once, and puts a new
# account in that org.
BATTLENET_ORG_ENV, BATTLENET_IDP_ENV = "OIDC_BATTLENET_ORG_ID", "OIDC_BATTLENET_IDP_ID"
_ZITADEL_ID = re.compile(r"^[0-9]{1,32}$")
# OpenID Connect's prompt on the normal login: always the account picker
# (auth_from_env, extra_authorize_params).
SELECT_ACCOUNT = "select_account"
# Set while the consent form is submitted through the Battle.net button, for
# the authorize URL built in that same call.
_BATTLENET_LOGIN: contextvars.ContextVar[bool] = contextvars.ContextVar("wow_battlenet_login", default=False)

# Request bodies: an MCP call is a few KB, the OAuth
# endpoints take a form or a small JSON.
MAX_MCP_BODY = 1024 * 1024
MAX_OTHER_BODY = 16 * 1024

# The OAuth proxy's state on disk: swept
# hourly, registrations capped, a registration nobody used in a day dropped.
CULL_EVERY = 3600
MAX_CLIENTS = 100
CLIENTS_COLLECTION = "mcp-oauth-proxy-clients"
UNUSED_CLIENT_TTL = 24 * 3600


def emit(event: str, level: int = logging.INFO, **fields: object) -> None:
    """One audit line, JSON (without OpenTelemetry:
    the line is the record)."""
    body = {k: v for k, v in fields.items() if v is not None}
    line = {"ts": round(time.time(), 3), "level": logging.getLevelName(level).lower(), "event": f"mcp_{event}",
            "msg": f"mcp: {event}" + "".join(f" {k}={v}" for k, v in body.items() if k in ("result", "tool")),
            **body}
    print(json.dumps(line, separators=(",", ":"), default=str), flush=True)


def _csv(v: str | None) -> list[str]:
    return [s.strip() for s in (v or "").split(",") if s.strip()]


def battlenet_scopes(env: dict) -> tuple[str, ...]:
    """The two scopes the Battle.net button adds, from the config only;
    none (no button) when both settings are empty."""
    org = (env.get("OIDC_BATTLENET_ORG_ID") or "").strip()
    idp = (env.get("OIDC_BATTLENET_IDP_ID") or "").strip()
    if not org and not idp:
        return ()
    if not (_ZITADEL_ID.match(org) and _ZITADEL_ID.match(idp)):
        raise SystemExit(f"{BATTLENET_ORG_ENV} and {BATTLENET_IDP_ENV} go together: both ZITADEL ids (digits), "
                         "or both empty for no Battle.net button")
    return (f"urn:zitadel:iam:org:id:{org}", f"urn:zitadel:iam:org:idp:id:{idp}")


def _read_secret(path: str, what: str) -> bytes:
    try:
        return Path(path).read_bytes().strip()
    except OSError:
        raise SystemExit(f"{what}: can't read the file named by its _FILE variable") from None


# ---------------------------------------------------------------- the tenant behind a call


@dataclass
class Who:
    """How a token's `sub` becomes the tools' context, and whether it may call.

    tenancy: wowstats.tenancy.Tenancy (several people) or None.
    allowed_subs: one person per install (OIDC_ALLOWED_SUBS); ignored with tenancy.
    """
    base: tools.Ctx
    tenancy: Any = None
    allowed_subs: frozenset[str] = frozenset()

    def may_call(self, sub: str) -> bool:
        if self.tenancy is not None:
            return self.tenancy.mcp_user(sub) is not None
        return sub in self.allowed_subs

    def ctx(self, sub: str | None) -> tools.Ctx:
        if self.tenancy is None or sub is None:
            return self.base
        u = self.tenancy.mcp_user(sub)
        if u is None:  # AuthMiddleware already refused; a race with a switch-off
            raise PermissionError("this account was removed or switched off")
        self.tenancy.app_for(u)
        return self.tenancy.ctx_for(u)


def _sub() -> str | None:
    tok = get_access_token()
    return str(tok.claims.get("sub", "")) if tok is not None else None


def allowed_check(who: Who):
    """The allowed-subs check, with the registry when there is one."""

    def check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        sub = str(ctx.token.claims.get("sub", ""))
        if sub and who.may_call(sub):
            return True
        emit("auth", logging.WARNING, result="denied", sub=sub or None)
        return False

    return check


# ---------------------------------------------------------------- tools and prompts


class WowTool(Tool):
    """One tools.Tool, run on the caller's context in a worker thread (the
    stores are blocking HTTP)."""

    _who: Who = PrivateAttr()

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        sub = _sub()
        try:
            ctx = self._who.ctx(sub)
        except PermissionError as e:
            return ToolResult(content=[TextContent(type="text", text=str(e))], is_error=True)
        reg = tools.mcp_tools(ctx)
        if self.name not in {t.name for t in reg}:
            return ToolResult(content=[TextContent(type="text", text=f"unknown tool: {self.name}")], is_error=True)
        registry = tools.TOOLS if reg == tools.TOOLS else reg
        try:
            out = await anyio.to_thread.run_sync(tools.call, ctx, self.name, dict(arguments or {}), registry)
        except tools.ToolError as e:
            return ToolResult(content=[TextContent(type="text", text=str(e))], is_error=True)
        except Exception:  # a bug in a tool: say so to the model, keep the details in the log
            log.exception("tool %s failed", self.name)
            emit("call", logging.ERROR, tool=self.name, result="error")
            return ToolResult(content=[TextContent(type="text", text=f"{self.name} failed inside the server; see "
                                                                     "its log")], is_error=True)
        out = wowhead.annotate(out)
        text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        return ToolResult(content=[TextContent(type="text", text=text)], structured_content=out)


def annotations(spec: tools.Tool) -> ToolAnnotations:
    """What the tool does, as a client reads it before calling: read-only
    unless it writes the person's state (tools.WRITES); a writer is
    destructive when it can overwrite or drop what is there. Every tool used
    to say read-only, the analysis and planner writers too (review,
    2026-11-19)."""
    if not spec.writes:
        return ToolAnnotations(readOnlyHint=True, openWorldHint=spec.open_world)
    return ToolAnnotations(readOnlyHint=False, destructiveHint=spec.writes == "replaces", idempotentHint=False,
                           openWorldHint=spec.open_world)


def _tool(spec: tools.Tool, who: Who) -> WowTool:
    t = WowTool(name=spec.name, title=spec.title, description=spec.description, parameters=spec.input_schema,
                annotations=annotations(spec))
    t._who = who
    return t


class WowPrompt(Prompt):
    """The coach prompt, the play analysis' `analyse` and the short commands
    (prompts.py)."""

    async def render(self, arguments: dict[str, Any] | None = None) -> PromptResult:
        args = arguments or {}
        if self.name == "coach":
            msgs = prompts.coach_messages(str(args.get("char") or ""), str(args.get("focus") or ""))
        elif self.name == "analyse":
            msgs = prompts.analyse_messages(str(args.get("char") or ""), str(args.get("goals") or ""))
        else:
            arg = prompts.COMMANDS[self.name][2]
            msgs = prompts.command_messages(self.name, str(args.get(arg["name"]) or "") if arg else "")
        return PromptResult(messages=[Message(m["content"]["text"], role=m["role"]) for m in msgs],
                            description=self.description)


def _prompt(p: dict) -> WowPrompt:
    return WowPrompt(name=p["name"], title=p.get("title"), description=p.get("description"),
                     arguments=[PromptArgument(name=a["name"], description=a.get("description"),
                                               required=bool(a.get("required"))) for a in p.get("arguments") or []])


class PersonTools(Middleware):
    """tools/list shows what this caller may run: `market` only for a person
    whose Battle.net setting is on (tools.mcp_tools)."""

    def __init__(self, who: Who):
        self.who = who

    async def on_list_tools(self, context, call_next):
        listed = await call_next(context)
        try:
            ctx = self.who.ctx(_sub())
        except PermissionError:
            return []
        names = {t.name for t in tools.mcp_tools(ctx)}
        return [t for t in listed if t.name in names]


# ---------------------------------------------------------------- the server


def build_server(who: Who, *, auth=None) -> FastMCP:
    """The FastMCP server. `auth` is the OIDC proxy (auth_from_env) or None
    (MCP_AUTH=none on a loopback bind, the tests, the help page)."""
    middleware: list[Middleware] = []
    if auth is not None:
        if who.tenancy is None and not who.allowed_subs:
            raise SystemExit("OIDC_ALLOWED_SUBS is empty: refusing to serve every account the provider knows")
        middleware.append(AuthMiddleware(auth=allowed_check(who)))
    middleware.append(PersonTools(who))
    keeper = getattr(auth, "state_keeper", None)
    mcp = FastMCP(name=NAME, instructions=INSTRUCTIONS, auth=auth, middleware=middleware,
                  lifespan=keeper.lifespan() if keeper else None)
    for spec in tools.TOOLS + (tools.MARKET,):
        mcp.add_tool(_tool(spec, who))
    for p in prompts.PROMPTS:
        mcp.add_prompt(_prompt(p))
    return mcp


def registry() -> FastMCP:
    """The server's own registry, no auth, no store: what the help page lists."""
    return build_server(Who(base=tools.Ctx(store=None)))


def listing() -> tuple[list, list]:
    """The tools and prompts /mcp lists to a caller without the Battle.net
    setting, from FastMCP itself (the help page)."""
    mcp = registry()

    async def go():
        return list(await mcp.list_tools()), list(await mcp.list_prompts())

    return asyncio.run(go())


# ---------------------------------------------------------------- the OAuth proxy


class StateKeeper:
    """A sweep of the proxy's FileTreeStore (expired entries are only
    dropped when read again) and its cap on registrations (DCR is open to
    anyone).

    A full list makes room (make_room): the oldest registration that never
    finished a login goes. Refusing instead let anyone who scripted 100
    registrations lock every new client out for a day, and again the next
    (security review L9). The clients that did log in are kept on disk
    (USED_FILE), so a restart doesn't make them look unused."""

    USED_FILE = "clients-logged-in.json"

    def __init__(self, store: Any, state_dir: Path, every: float = CULL_EVERY):
        self.store, self.dir, self.every = store, state_dir, every
        try:
            self.used: set[str] = set(json.loads((state_dir / self.USED_FILE).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            self.used = set()

    def mark_used(self, client_id: str) -> None:
        if client_id in self.used:
            return
        self.used.add(client_id)
        try:
            tmp = self.dir / (self.USED_FILE + ".tmp")
            tmp.write_text(json.dumps(sorted(self.used)), encoding="utf-8")
            os.replace(tmp, self.dir / self.USED_FILE)
        except OSError:
            pass  # kept in memory: the next login writes it again

    def clients(self) -> int:
        try:
            return sum(1 for _ in (self.dir / CLIENTS_COLLECTION).glob("*.json"))
        except OSError:
            return 0

    def full(self) -> bool:
        return self.clients() >= MAX_CLIENTS

    def _keep(self) -> set[str]:
        strategy = getattr(self.store, "_key_sanitization_strategy", None)
        return {(strategy.sanitize(value=c) if strategy is not None else c) + ".json" for c in self.used}

    def make_room(self) -> bool:
        """Under the cap, or brought under it by dropping the oldest unused
        registrations; False when every one of them has logged in."""
        keep = self._keep()
        try:
            files = sorted((f for f in (self.dir / CLIENTS_COLLECTION).glob("*.json") if f.name not in keep),
                           key=lambda f: f.stat().st_mtime)
        except OSError:
            files = []
        while self.full() and files:
            files.pop(0).unlink(missing_ok=True)
        return not self.full()

    def drop_unused(self) -> int:
        keep = self._keep()
        dropped = 0
        try:
            for f in (self.dir / CLIENTS_COLLECTION).glob("*.json"):
                if f.name not in keep and time.time() - f.stat().st_mtime > UNUSED_CLIENT_TTL:
                    f.unlink(missing_ok=True)
                    dropped += 1
        except OSError:
            pass
        return dropped

    async def cull(self) -> None:
        try:
            await self.store.cull()
            emit("state_cull", result="ok", clients=self.clients(), unused_dropped=self.drop_unused() or None)
        except Exception as e:  # noqa: BLE001 - a failed sweep waits for the next one
            emit("state_cull", logging.WARNING, result="failed", error=type(e).__name__)

    def lifespan(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def run(_server):
            async def loop():
                while True:
                    await self.cull()
                    await asyncio.sleep(self.every)

            task = asyncio.create_task(loop())
            try:
                yield {}
            finally:
                task.cancel()

        return run


@dataclass
class Gate:
    """Who may log in, from the provider's userinfo (module docstring)."""
    who: Who
    required_role: str = ""
    allowed_orgs: frozenset[str] = frozenset()
    org_claim: str = ORG_CLAIM
    roles_claim: str = ROLES_CLAIM

    def decide(self, claims: dict, info: dict) -> str | None:
        """None when this person may use the server, else why not."""
        sub = str(claims.get("sub") or info.get("sub") or "")
        if not sub:
            return "no subject in the login"
        if info.get("sub") and str(info["sub"]) != sub:
            return "userinfo is someone else's"
        if self.who.tenancy is not None:
            if self.who.tenancy.resolve_tenant(sub) is None:
                return "this account has no access to wow-stats"
            return None
        merged = {**claims, **info}
        org = str(merged.get(self.org_claim) or "")
        roles = merged.get(self.roles_claim)
        if self.required_role:
            keys = roles.keys() if isinstance(roles, dict) else roles if isinstance(roles, list) else ()
            if self.required_role not in {str(k) for k in keys}:
                return "this account has no access to wow-stats"
        if self.allowed_orgs and org not in self.allowed_orgs:
            return "this account's organisation may not use this server"
        if sub not in self.who.allowed_subs:
            return "this account may not use this server"
        return None


def _key(master: bytes, purpose: bytes) -> bytes:
    return hmac.new(master, b"wow-stats mcp " + purpose, hashlib.sha256).digest()


@dataclass
class Settings:
    """The OAuth settings, read and checked when the UI starts (a bad one
    stops the server there, not in a request)."""
    public_url: str
    issuer: str
    client_id: str
    client_secret: str
    master: bytes
    state_dir: Path | None
    allowed_subs: frozenset[str]
    env: dict

    @classmethod
    def from_env(cls, env: dict) -> Settings:
        public = env.get("MCP_PUBLIC_URL", "").strip()
        p = urlsplit(public)
        if not (p.scheme == "https" or (p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost"))):
            raise SystemExit("MCP_PUBLIC_URL must be the https URL clients use for /mcp (e.g. "
                             "https://wow.example.com/mcp)")
        login = env.get("MCP_LOGIN", "oidc").strip().lower()
        if login != "oidc":
            raise SystemExit(f"MCP_LOGIN={login}: the MCP logs in through an OpenID Connect provider only (oidc); "
                             "the passphrase login went with the hand-written OAuth server (docs/mcp.md)")
        issuer = env.get("OIDC_ISSUER", "").strip().rstrip("/")
        ip = urlsplit(issuer)
        secret = (_read_secret(env["OIDC_CLIENT_SECRET_FILE"], "OIDC_CLIENT_SECRET_FILE").decode()
                  if env.get("OIDC_CLIENT_SECRET_FILE") else env.get("OIDC_CLIENT_SECRET", ""))
        client_id = env.get("OIDC_CLIENT_ID", "").strip()
        if not ((ip.scheme == "https" or (ip.scheme == "http" and ip.hostname in ("127.0.0.1", "localhost")))
                and client_id and secret):
            raise SystemExit("MCP_LOGIN=oidc needs OIDC_ISSUER (https), OIDC_CLIENT_ID and OIDC_CLIENT_SECRET_FILE")
        if env.get("MCP_SIGNING_KEY_FILE"):
            master = _read_secret(env["MCP_SIGNING_KEY_FILE"], "MCP_SIGNING_KEY_FILE")
            if len(master) < 32:
                raise SystemExit("MCP_SIGNING_KEY_FILE: use 32 random bytes or more (openssl rand -hex 32)")
        else:
            master = secrets.token_bytes(32)
            print("MCP: no MCP_SIGNING_KEY_FILE, logins end with this process (every connector logs in again "
                  "after a restart)", flush=True)
        state = (env.get("MCP_STATE_DIR") or "").strip()
        return cls(public_url=public, issuer=issuer, client_id=client_id, client_secret=secret, master=master,
                   state_dir=Path(state) / "oauth" if state else None,
                   allowed_subs=frozenset(_csv(env.get("OIDC_ALLOWED_SUBS"))), env=dict(env))


def auth_from_env(s: Settings, who: Who):
    """FastMCP's OIDC proxy, configured from the env names the old server
    used (docs/mcp.md)."""
    from cryptography.fernet import Fernet
    from fastmcp.server.auth.oidc_proxy import OIDCProxy
    from key_value.aio.stores.filetree import FileTreeStore
    from key_value.aio.stores.memory import MemoryStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
    from mcp.server.auth.provider import RegistrationError, TokenError

    env, master, issuer, client_id, secret = s.env, s.master, s.issuer, s.client_id, s.client_secret
    p = urlsplit(s.public_url)
    if who.tenancy is None and not who.allowed_subs:
        raise SystemExit("MCP_LOGIN=oidc needs OIDC_ALLOWED_SUBS (or several people: WOW_STATE_DIR)")
    if s.state_dir is not None:
        # Beside the analysis' state (ANALYSIS_STATE_DIR may live in the same
        # folder): the proxy's files in their own.
        s.state_dir.mkdir(parents=True, exist_ok=True)
        sdir, raw = s.state_dir, FileTreeStore(data_directory=s.state_dir)
    else:
        sdir, raw = None, MemoryStore()
        print("MCP: no MCP_STATE_DIR, registrations and logins are kept in memory only", flush=True)
    keeper = StateKeeper(raw, sdir) if sdir is not None else None

    # Several people: the friends list decides (Gate), no org or role is read.
    one = who.tenancy is None
    allowed_orgs = frozenset(_csv(env.get("OIDC_ALLOWED_ORGS"))) if one else frozenset()
    gate = Gate(who, required_role=env.get("OIDC_REQUIRED_ROLE", "").strip() if one else "",
                allowed_orgs=allowed_orgs,
                org_claim=env.get("OIDC_ORG_CLAIM", "").strip() or ORG_CLAIM,
                roles_claim=env.get("OIDC_ROLES_CLAIM", "").strip() or ROLES_CLAIM)
    scopes = env.get("OIDC_SCOPES", "").strip() or " ".join(
        ["openid", "offline_access"] + ([ORG_SCOPE] if allowed_orgs else []))
    redirects = _csv(env.get("MCP_REDIRECT_URIS")) or list(DEFAULT_REDIRECT_URIS)
    bnet = battlenet_scopes(env)
    cimd_hosts = frozenset(h.lower() for h in _csv(env.get("MCP_CIMD_HOSTS"))) or frozenset(DEFAULT_CIMD_HOSTS)

    class Denied(TokenError):
        def __init__(self, why: str):
            super().__init__("invalid_grant", why)

    class WithAudit(OIDCProxy):
        async def register_client(self, client_info):
            if keeper is not None and not keeper.make_room():
                emit("auth", logging.WARNING, result="client_cap", clients=keeper.clients())
                raise RegistrationError("invalid_client_metadata",
                                        f"this server already has {MAX_CLIENTS} registered clients; ask its owner")
            await super().register_client(client_info)
            hosts = sorted({urlsplit(str(u)).hostname or "?" for u in (client_info.redirect_uris or [])})
            emit("auth", logging.WARNING, result="client_registered", client=client_info.client_id,
                 name=(client_info.client_name or "")[:80] or None, redirect_hosts=hosts)

        async def get_client(self, client_id: str):
            # CIMD: only the known clients' hosts are fetched (an egress
            # allow-list can name the same ones), and every refusal says why in the log.
            if client_id.startswith(("https://", "http://")):
                host = (urlsplit(client_id).hostname or "").lower()
                if host not in cimd_hosts:
                    emit("cimd", logging.WARNING, result="host_not_allowed", host=host or "?")
                    return None
                client = await super().get_client(client_id)
                if client is None:
                    emit("cimd", logging.WARNING, result="fetch_failed", host=host)
                return client
            return await super().get_client(client_id)

        async def _userinfo(self, access_token: str) -> dict:
            url = getattr(self.oidc_config, "userinfo_endpoint", None)
            if not url or not access_token:
                return {}
            import httpx2

            async with httpx2.AsyncClient(timeout=10) as c:
                r = await c.get(str(url), headers={"Authorization": f"Bearer {access_token}"})
            if r.status_code != 200:
                raise Denied(f"the login provider's userinfo answered {r.status_code}")
            info = r.json()
            return info if isinstance(info, dict) else {}

        async def _check(self, idp_tokens: dict, when: str) -> dict:
            """Raises Denied unless the person behind these upstream tokens
            may use the server; returns the claims to embed (the org)."""
            id_token = idp_tokens.get("id_token")
            claims: dict = {}
            if id_token:
                verified = await self._token_validator.verify_token(id_token)
                if verified is None:
                    raise Denied("the login provider's ID token didn't check out")
                claims = dict(verified.claims or {})
            info = await self._userinfo(str(idp_tokens.get("access_token") or ""))
            why = await anyio.to_thread.run_sync(gate.decide, claims, info)
            sub = str(claims.get("sub") or info.get("sub") or "")
            if why:
                emit("auth", logging.WARNING, result="refused", when=when, sub=sub or None, why=why)
                raise Denied(why)
            emit("auth", result="ok", when=when, sub=sub)
            return {"org": str({**claims, **info}.get(gate.org_claim) or "")}

        # ------------------------------------------------ the consent page

        async def _show_consent_page(self, request):
            response = await super()._show_consent_page(request)
            if response.status_code != 200 or not response.body:
                return response
            page, ok = consent.restyle(response.body.decode("utf-8"), battlenet=bool(bnet))
            if not ok:
                emit("auth", logging.WARNING, result="consent_page_unchanged",
                     why="FastMCP's consent markup changed: served as is, without the Battle.net button")
                return response
            response.body = page.encode("utf-8")
            response.headers["content-length"] = str(len(response.body))
            return response

        async def _submit_consent(self, request):
            # The Battle.net button posts the same form (txn_id, csrf_token,
            # action=approve) with ?login=battlenet: FastMCP's checks run as
            # for Allow; only the authorize URL it builds gets the two scopes.
            if not (bnet and request.query_params.get(consent.LOGIN_PARAM) == consent.BATTLENET):
                return await super()._submit_consent(request)
            mark = _BATTLENET_LOGIN.set(True)
            try:
                response = await super()._submit_consent(request)
            finally:
                _BATTLENET_LOGIN.reset(mark)
            if response.status_code in (302, 303):
                emit("auth", result="battlenet_login")
            return response

        def _build_upstream_authorize_url(self, txn_id, transaction):
            url = super()._build_upstream_authorize_url(txn_id, transaction)
            if not (bnet and _BATTLENET_LOGIN.get()):
                return url
            # The Battle.net button: its two scopes, and no account picker
            # (the IdP scope already says which login to start).
            parts = urlsplit(url)
            query = [(k, " ".join([v, *bnet]) if k == "scope" else v)
                     for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "prompt"]
            return urlunsplit(parts._replace(query=urlencode(query)))

        # ------------------------------------------------ tokens

        async def exchange_authorization_code(self, client, authorization_code):
            code = await self._code_store.get(key=authorization_code.code)
            if code is not None:
                await self._check(code.idp_tokens, "login")
            if client.client_id:
                self_keeper_used(client.client_id)
            return await super().exchange_authorization_code(client, authorization_code)

        async def _extract_upstream_claims(self, idp_tokens):
            return await self._check(idp_tokens, "token")

        async def exchange_refresh_token(self, client, refresh_token, scopes):
            try:
                return await super().exchange_refresh_token(client, refresh_token, scopes)
            except Denied:
                # The upstream tokens were refreshed and stored before the
                # check: drop them and this refresh token, so nothing of the
                # login is left to replay.
                await self._forget(refresh_token.token)
                raise

        async def _try_transparent_refresh(self, upstream_token_set):
            refreshed = await super()._try_transparent_refresh(upstream_token_set)
            try:
                await self._check(refreshed.raw_token_data, "transparent_refresh")
            except Denied:
                await self._upstream_token_store.delete(key=refreshed.upstream_token_id)
                raise
            return refreshed

        async def _forget(self, refresh_jwt: str) -> None:
            """Private API of FastMCP's proxy (4.1.6): the refresh JWT's jti maps to the upstream
            token set."""
            from fastmcp.server.auth.oauth_proxy.proxy import _hash_token

            try:
                jti = self.jwt_issuer.verify_token(refresh_jwt, expected_token_use="refresh").get("jti")
                mapping = await self._jti_mapping_store.get(key=str(jti)) if jti else None
                if mapping is not None:
                    await self._upstream_token_store.delete(key=mapping.upstream_token_id)
                    await self._jti_mapping_store.delete(key=str(jti))
                await self._refresh_token_store.delete(key=_hash_token(refresh_jwt))
            except Exception as e:  # noqa: BLE001 - the denial stands either way
                emit("auth", logging.WARNING, result="forget_failed", error=type(e).__name__)

    def self_keeper_used(client_id: str) -> None:
        if keeper is not None:
            keeper.mark_used(client_id)

    provider = WithAudit(
        config_url=f"{issuer}/.well-known/openid-configuration",
        client_id=client_id,
        client_secret=secret,
        base_url=f"{p.scheme}://{p.netloc}",
        redirect_path=CALLBACK_PATH,
        allowed_client_redirect_uris=redirects,
        # The ID token is verified (aud = this app's client id), not the
        # access token: no API audience to set up in the provider.
        verify_id_token=True,
        # Always `openid` (the ID token is the login) whatever the client
        # asked; offline_access for the refresh token the re-checks need.
        # prompt=select_account: the provider asks which account instead of
        # reusing the browser's newest session. Without it, one Battle.net
        # sign-in in that browser made every later "Allow" log in as that
        # account too, silently (ZITADEL's login v2 picks the most recently
        # changed session when the request names nobody), and the owner
        # couldn't get his own account back (2026-11-21).
        extra_authorize_params={"scope": scopes, "prompt": SELECT_ACCOUNT},
        required_scopes=["openid"],
        jwt_signing_key=_key(master, b"jwt"),
        client_storage=FernetEncryptionWrapper(
            key_value=raw, fernet=Fernet(base64.urlsafe_b64encode(_key(master, b"storage")))),
    )
    provider.state_keeper = keeper
    provider.gate = gate
    return provider


# ---------------------------------------------------------------- ASGI guards


class BodyLimit:
    """Refuse request bodies past a cap before the app reads them."""

    def __init__(self, app, mcp_path: str = "/mcp", mcp_limit: int = MAX_MCP_BODY,
                 other_limit: int = MAX_OTHER_BODY):
        self.app, self.mcp_path, self.mcp_limit, self.other_limit = app, mcp_path, mcp_limit, other_limit

    async def _refuse(self, send) -> None:
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"text/plain"), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": b"request body too large\n"})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS", "DELETE"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        limit = self.mcp_limit if path == self.mcp_path or path.startswith(self.mcp_path + "/") else self.other_limit
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > limit:
                        return await self._refuse(send)
                except ValueError:
                    return await self._refuse(send)
        body, more = bytearray(), True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > limit:
                return await self._refuse(send)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


class OriginCheck:
    """A browser page may not call /mcp from another site (the spec's DNS
    rebinding guard; with MCP_AUTH=none it is the only one). Requests
    without an Origin (every native client, claude.ai's servers) pass."""

    def __init__(self, app, origins: frozenset[str], mcp_path: str = "/mcp"):
        self.app, self.origins, self.mcp_path = app, origins, mcp_path

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == self.mcp_path:
            for name, value in scope.get("headers", []):
                if name == b"origin" and value.decode("latin-1").rstrip("/") not in self.origins:
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body",
                                "body": b'{"jsonrpc":"2.0","error":{"code":-32600,"message":"origin not allowed"}}'})
                    return
        return await self.app(scope, receive, send)


def http_app(mcp: FastMCP, mcp_path: str, origins: frozenset[str]):
    from starlette.middleware import Middleware as AsgiMiddleware

    return mcp.http_app(path=mcp_path, middleware=[AsgiMiddleware(BodyLimit, mcp_path=mcp_path),
                                                   AsgiMiddleware(OriginCheck, origins=origins,
                                                                  mcp_path=mcp_path)])


# ---------------------------------------------------------------- running it


@dataclass
class Running:
    url: str
    port: int
    mcp: FastMCP
    thread: threading.Thread
    stop: Callable[[], None]


def serve(mcp: FastMCP, *, bind: str, port: int, mcp_path: str, origins: frozenset[str]) -> Running:
    """uvicorn in a daemon thread beside the UI's stdlib server."""
    import uvicorn

    app = http_app(mcp, mcp_path, origins)
    config = uvicorn.Config(app, host=bind, port=port, log_level="warning", lifespan="on", access_log=False,
                            proxy_headers=False)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, name="mcp", daemon=True)
    t.start()
    deadline = time.time() + 15
    while not server.started and t.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        sys.exit(f"MCP: the server didn't start on {bind}:{port}")
    real = port
    for s in getattr(server, "servers", []) or []:
        for sock in getattr(s, "sockets", []) or []:
            real = sock.getsockname()[1]

    def stop():
        server.should_exit = True
        t.join(timeout=5)

    return Running(url=f"http://{bind}:{real}{mcp_path}", port=real, mcp=mcp, thread=t, stop=stop)
