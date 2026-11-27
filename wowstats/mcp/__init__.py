"""The MCP endpoint of the full view, on FastMCP (wowstats.mcp.server).

Seam with the UI server (docs/architecture.md): the UI checks the settings
when it starts, and main() starts the endpoint on its own port.

    from wowstats import mcp
    ep = mcp.endpoint(env, bind)     # None: off; SystemExit: a bad setting
    ep.attach_tenancy(t)             # several people (wowstats.tenancy)
    ep.start()                       # FastMCP on MCP_PORT, in a thread

FastMCP answers /mcp and the OAuth paths (server.ROUTES) on MCP_PORT (8090),
never on the UI's port: a reverse proxy routes them there, without the page's
own login in front (the tokens protect them). In VIEW=public there is no
endpoint at all.

Settings (env), docs/mcp.md has the long version:
  MCP_PUBLIC_URL        https URL clients use, e.g. https://wow.example.com/mcp
  MCP_PORT              the endpoint's port (8090), on BIND
  MCP_AUTH              oauth (default) | none (only when BIND is loopback)
  MCP_LOGIN             oidc (OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET_FILE,
                        OIDC_ALLOWED_SUBS; OIDC_SCOPES, OIDC_ALLOWED_ORGS,
                        OIDC_REQUIRED_ROLE)
  MCP_SIGNING_KEY_FILE  32+ random bytes; without it logins die with the process
  MCP_STATE_DIR         registrations and logins across restarts (encrypted)
  MCP_REDIRECT_URIS, MCP_CIMD_HOSTS, MCP_ALLOWED_ORIGINS   the client allowlists

`wow-stats mcp-stdio` (stdio()) serves the same tools over stdin/stdout to a
client on the same machine, which starts it: no port, no login; whoever can
run the command could read the store anyway (with the compose kit:
`docker compose exec -T ui wow-stats mcp-stdio`). One person per install.

FastMCP is the `mcp` extra: without it the full view runs without /mcp and
says so. The `market` tool reaches only a person whose Battle.net setting is
on (tools.mcp_tools).
"""

from __future__ import annotations

import ipaddress
import os
import sys
from urllib.parse import urlsplit

from . import tools
from .store import Store


def is_full(view) -> bool:
    """`view` is "full"/"public", or the UI server's Views object (its
    settings.view)."""
    if isinstance(view, str):
        return view == "full"
    return getattr(getattr(view, "settings", None), "view", "full") == "full"


def context(env=os.environ) -> tools.Ctx:
    """The tools' context. `env` is kept on it when it isn't the
    process's own (a person's settings, wowstats.tenancy)."""
    return tools.Ctx(store=Store.from_env(env), default_char=env.get("DEFAULT_CHAR", ""),
                     env=None if env is os.environ else dict(env), bnet=False if env.get("WOW_STATE_DIR") else None)


def is_loopback_bind(bind: str) -> bool:
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return bind == "localhost"


class Endpoint:
    """The endpoint's settings, checked at the UI's start; start() builds the
    FastMCP server (the OIDC discovery happens there) and serves it."""

    def __init__(self, env: dict, bind: str):
        self.env, self.bind = dict(env), bind
        self.ctx = context(env)
        self.tenancy = None
        self.running = None
        self.port = int(env.get("MCP_PORT") or "8090")
        mode = env.get("MCP_AUTH", "oauth").strip().lower()
        public = env.get("MCP_PUBLIC_URL", "").strip()
        self.path = urlsplit(public).path.rstrip("/") or "/mcp"
        if mode == "none":
            if not is_loopback_bind(bind):
                raise SystemExit("MCP_AUTH=none with a non-loopback BIND: anyone reaching the port could read the "
                                 "stats")
            self.settings = None
            self.origins = {"http://127.0.0.1", "http://localhost"}
        elif mode == "oauth":
            from .server import Settings

            self.settings = Settings.from_env(env)
            p = urlsplit(public)
            self.origins = {f"{p.scheme}://{p.netloc}", "https://claude.ai", "https://claude.com"}
        else:
            raise SystemExit(f"MCP_AUTH={mode}: use oauth or none")
        self.origins |= {o.strip().rstrip("/") for o in env.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()}

    def attach_tenancy(self, t) -> None:
        """Several people (wowstats.tenancy): each token's `sub` must be a live
        person of the registry on every call, its tools run on that person's
        data, and the login provisions a new person (an allowed org, the
        project role)."""
        self.tenancy = t

    def who(self):
        from .server import Who

        allowed = self.settings.allowed_subs if self.settings is not None else frozenset()
        return Who(base=self.ctx, tenancy=self.tenancy, allowed_subs=allowed)

    def build(self):
        from .server import auth_from_env, build_server

        who = self.who()
        auth = auth_from_env(self.settings, who) if self.settings is not None else None
        return build_server(who, auth=auth)

    def start(self):
        from .server import serve

        self.running = serve(self.build(), bind=self.bind, port=self.port, mcp_path=self.path,
                             origins=frozenset(self.origins))
        return self.running


def endpoint(env: dict, bind: str) -> Endpoint | None:
    """The endpoint when MCP_PUBLIC_URL (or MCP_AUTH=none) asks for one, else
    None. A bad setting raises SystemExit; FastMCP missing (no `mcp` extra)
    is said once and leaves it off."""
    if not (env.get("MCP_PUBLIC_URL", "").strip() or env.get("MCP_AUTH", "").strip().lower() == "none"):
        return None
    try:
        import fastmcp  # noqa: F401
    except ModuleNotFoundError:
        print("MCP: MCP_PUBLIC_URL is set but FastMCP isn't installed (the `mcp` extra): no /mcp", file=sys.stderr,
              flush=True)
        return None
    return Endpoint(env, bind)


def stdio(env=os.environ) -> int:
    """The tools over stdio, for a local client that starts this process
    (Claude Code's `claude mcp add ... -- <command>`). stdout carries the
    protocol only: anything said goes to stderr."""
    if (env.get("VIEW") or "full").strip().lower() != "full":
        print("mcp-stdio: VIEW=public has no MCP", file=sys.stderr)
        return 2
    if (env.get("WOW_STATE_DIR") or "").strip():
        print("mcp-stdio: several people (WOW_STATE_DIR) need the logged-in endpoint (MCP_PUBLIC_URL), "
              "which knows who is asking", file=sys.stderr)
        return 2
    try:
        import fastmcp  # noqa: F401
    except ModuleNotFoundError:
        print("mcp-stdio: FastMCP isn't installed (the `mcp` extra)", file=sys.stderr)
        return 2
    os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
    from .server import Who, build_server

    build_server(Who(base=context(env)), auth=None).run(transport="stdio", show_banner=False)
    return 0
