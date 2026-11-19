# Set up the login for your AI

**For:** self-hosters who want claude.ai, ChatGPT or any MCP client to reach their server from
anywhere. **At the end:** the MCP endpoint answers on `https://wow-full.example.org/mcp`, only
your account gets in, and a connector added in claude.ai logs in and lists the tools.

claude.ai and ChatGPT call your server from their own servers, not from your browser, so they
can't carry your page's password. They speak OAuth (the "log in with..." standard): the first
time, your browser opens a consent page from your server, then your identity provider's
login, and the client gets a token it renews by itself. wow-stats doesn't keep passwords: you bring an OIDC identity provider (a
login service that speaks OpenID Connect: Keycloak, Authentik, ZITADEL, and most hosted ones).

On the computer that runs the server, Claude Code needs none of this:
[On the server's own computer](../ai/connect.md#on-the-servers-own-computer).

## Before you start

- The kit behind HTTPS on a public name: [Put it behind a reverse proxy](reverse-proxy.md).
  The examples use `wow-full.example.org`.
- An identity provider you can make an app in, reachable from the server over HTTPS.

## 1. Make an app at your identity provider

Any provider works if it offers OIDC discovery (`<issuer>/.well-known/openid-configuration`),
the authorization code flow with a client secret, and refresh tokens. Make an app (some call
it a client) with:

| Setting | Value |
|---|---|
| Type | Confidential, or "web application": it has a client secret |
| Flow | Authorization code |
| Redirect URI | `https://wow-full.example.org/oauth/callback` |
| Refresh tokens | On (the `offline_access` scope) |

Note three things: the **issuer** URL, the **client id** and the **client secret**. Then find
your own account's **subject id** (`sub`): the provider's stable id for you, often a UUID or a
long number, shown on the user's page in its admin console.

## 2. Give them to wow-stats

From `deploy/compose`:

```sh
printf '%s' 'the client secret' > secrets/oidc_client_secret
head -c 48 /dev/urandom > secrets/mcp_signing_key
chmod 644 secrets/oidc_client_secret secrets/mcp_signing_key
```

The signing key signs the tokens wow-stats hands to clients. Keep it: a new one logs every
client out. The containers run as their own user, hence `chmod 644`.

Then in `.env`:

```sh
MCP_PUBLIC_URL=https://wow-full.example.org/mcp
OIDC_ISSUER=https://id.example.org/realms/wow
OIDC_CLIENT_ID=wow-mcp
OIDC_ALLOWED_SUBS=your sub
MCP_SIGNING_KEY_FILE=/run/wow-secrets/mcp_signing_key
```

The host of `MCP_PUBLIC_URL` is one the page answers for by itself. `OIDC_ALLOWED_SUBS` takes
several ids, comma separated. Anyone else who logs in at your
provider is refused. Restart with the same files as before (Caddy here):

```sh
docker compose -f compose.yaml -f compose.caddy.yaml up -d
```

## 3. Check it

```sh
curl -s https://wow-full.example.org/.well-known/oauth-protected-resource/mcp
curl -s -o /dev/null -w '%{http_code}\n' https://wow-full.example.org/mcp
```

The first prints a JSON document naming `https://wow-full.example.org/mcp` as the resource; the
second prints `401`: the endpoint is there and wants a token. A `502` or `404` instead is the
proxy: the MCP paths must go to port 8090 ([What goes where](reverse-proxy.md#what-goes-where)).

Then add the connector in claude.ai, or any client: [Connect your AI](../ai/connect.md). Your
browser opens a consent page from your server (**Approve**), then your provider's login.

## Worked example: Keycloak

[Keycloak](https://www.keycloak.org/) is a free, self-hosted identity provider. This assumes
it runs at `https://id.example.org` and you can run its admin CLI, `kcadm.sh`, which ships in
its container. If yours runs in Docker, prefix each command with
`docker exec -it <container> /opt/keycloak/bin/` (`-it` lets the first one ask for the
password).

Log the CLI in as Keycloak's admin (it asks for the password):

```sh
kcadm.sh config credentials --server http://localhost:8080 --realm master --user admin
```

A realm (Keycloak's word for a separate set of users and apps) for wow-stats, and the app:

```sh
kcadm.sh create realms -s realm=wow -s enabled=true
kcadm.sh create clients -r wow -s clientId=wow-mcp -s publicClient=false \
  -s standardFlowEnabled=true -s directAccessGrantsEnabled=false \
  -s 'redirectUris=["https://wow-full.example.org/oauth/callback"]'
```

The client secret (Keycloak made one):

```sh
id=$(kcadm.sh get clients -r wow -q clientId=wow-mcp --fields id --format csv --noquotes)
kcadm.sh get clients/$id/client-secret -r wow --fields value --format csv --noquotes
```

Your account in the realm (Keycloak wants a username of 3 characters or more), and its `sub`
(Keycloak's user id):

```sh
kcadm.sh create users -r wow -s username=player -s enabled=true -s email=player@example.org \
  -s emailVerified=true -s firstName=Test -s lastName=Player
kcadm.sh set-password -r wow --username player --new-password 'a long password'
kcadm.sh get users -r wow -q username=player --fields id --format csv --noquotes
```

So in `.env`: `OIDC_ISSUER=https://id.example.org/realms/wow`, `OIDC_CLIENT_ID=wow-mcp`, and
`OIDC_ALLOWED_SUBS=` the id the last command printed. The secret goes in
`secrets/oidc_client_secret` as in step 2.

Keycloak lets in any user of the realm; `OIDC_ALLOWED_SUBS` is what keeps wow-stats to you.

## Other providers

- **ZITADEL**: the defaults read ZITADEL's own claims. With one install per person you can
  also require a project role (`OIDC_REQUIRED_ROLE`) or an organisation
  (`OIDC_ALLOWED_ORGS`).
- **Others with roles or organisations**: set `OIDC_ROLES_CLAIM` and `OIDC_ORG_CLAIM` to the
  claim names your provider uses, then the two settings above.

Every login setting: [Environment variables, the MCP endpoint](../reference/environment.md#the-mcp-endpoint-and-its-login).

## When it doesn't work

- **`ui` keeps restarting** (`docker compose ps`). It reads your provider's discovery document
  when it starts, so the issuer must answer from the server, over HTTPS with a certificate the
  server trusts. `docker compose logs ui | tail` names the address it couldn't reach.
- **"Client Not Registered", or a client that never gets to your login page.** The client
  isn't on the server's list of known clients: Claude, ChatGPT, VS Code, Cursor, Codex,
  opencode, Copilot CLI, Zed and Goose are by default. Others: add their redirect address to
  `MCP_REDIRECT_URIS` or their host to `MCP_CIMD_HOSTS` (this replaces the list, so keep the
  ones you use).
- **"Authorization session mismatch" at Approve.** The proxy drops cookies on the login pages.
- **Refused after logging in.** Your `sub` isn't in `OIDC_ALLOWED_SUBS`. The `ui` log says
  which one was refused: `docker compose logs ui | grep -i refus`.
- **Everyone logged out after a restart.** `MCP_SIGNING_KEY_FILE` isn't set, or the file
  changed.

How the login works inside: [MCP, how login works](../mcp.md#how-login-works-and-why-it-isnt-the-page-login).
