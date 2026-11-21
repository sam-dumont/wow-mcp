# Harden it

**For:** self-hosters putting the server on the internet. **At the end:** you know what's
locked down already, and the few things that are on you.

## Already done for you

- **Containers.** Every wow-stats container runs as its own user (uid 10001), on a read-only
  root filesystem with only `/tmp` writable, with every Linux capability dropped and
  `no-new-privileges`. The image holds Python's standard library plus a short, pinned list:
  cryptography for the sealed site, FastMCP for the MCP endpoint, DuckDB and the Boring
  Semantic Layer for the analysis' queries, and Anthropic's and OpenAI's SDKs for the
  server-side analysis (imported only when you configure one). No shell at run time.
- **Listening.** Every published port binds `127.0.0.1`. VictoriaLogs has no published port
  at all: only `ui` and `publish` reach it.
- **One view per process.** The public view and the full view are separate processes, so a
  routing mistake can't hand your full view to a visitor. The public answers are cut to an
  allow list on the server: a field the code adds later stays private until someone adds it
  to the list ([Privacy tiers](../explanation/privacy.md)).
- **Static sites.** Files only: no login, no cookie, no backend call. The sealed site's data
  is encrypted, and its key never reaches the server, a log or a proxy.
- **Host names.** The page answers only for localhost, IP addresses and the names you set
  (`PUBLIC_URL`, `MCP_PUBLIC_URL`, `ALLOWED_HOSTS`), so a web page can't read yours through
  your browser. `AUTH=open` refuses to listen beyond loopback unless you say so (`ALLOW_OPEN`).
- **The page.** A strict Content Security Policy (scripts, styles and fonts from the server
  itself, no third-party code), `Referrer-Policy: no-referrer`. Outbound links to Wowhead
  carry no referrer.
- **The MCP.** OAuth with PKCE, a consent page per client, tokens bound to your `/mcp`
  address, an allow list of known clients, a 1 MiB request limit. Who gets in is checked at
  every token refresh, against your identity provider's answer.
- **Uploads.** `/ingest` takes events only with its password, and the server screens every
  event before storing it.
- **Logs.** Never a password, key, token or price.

## On you

1. **Keep the ports on `127.0.0.1`** and reach them through a reverse proxy with HTTPS
   ([Put it behind a reverse proxy](reverse-proxy.md)). Never publish VictoriaLogs (9428):
   it answers anyone, with every event.
2. **Put a login on the full view.** `UI_AUTH=basic` with a long password, or `AUTH=proxy`
   behind a login proxy that replaces `X-Forwarded-User` on every request. With `AUTH=proxy`
   the server also needs to know the proxy: `PROXY_TRUSTED_CIDRS` and/or `PROXY_SECRET_FILE`. `open` is only
   for a port no one else reaches.
3. **Use long random secrets.** `INGEST_PASSWORD` and `BASIC_AUTH_PASSWORD` need 12
   characters; use 24 or more. Keep `.env` at `chmod 600`. Secrets in files under `secrets/`
   (the `_FILE` variables) stay out of `docker inspect`.
4. **Keep `MCP_SIGNING_KEY_FILE` private and stable.** It signs every AI token.
5. **Never route the internal ports**: the static server's upload port, the full view's share
   port, the renderer, the exporter's `/metrics`
   ([HTTP endpoints, never route these](../reference/http-endpoints.md#never-route-these-from-outside)).
6. **Give the renderer nothing.** If you run it: no secrets, no route to your stores, no way
   out at all. It runs Chromium without its own sandbox.
7. **Server-side analysis, if you turn it on**: the provider's key (or its federation token)
   goes to the full view alone, with egress to that provider's API and nothing else, under a
   monthly budget ([Server-side analysis](server-analysis.md)).
8. **On Kubernetes**, add network policies: the static pod has no route out at all, the
   publisher reaches the stores and the static pod's upload port, the renderer nothing out
   ([Deploy without compose](../deploy.md)).
9. **Update.** Read the [changelog](../../CHANGELOG.md) and [upgrade](upgrade.md) when a
   release comes out.

What the security review left open on purpose: [Security notes](../security.md). A security
problem in wow-stats itself goes through [SECURITY.md](../../SECURITY.md).
