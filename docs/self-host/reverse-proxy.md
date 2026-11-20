# Put it behind a reverse proxy

**For:** self-hosters exposing the compose kit to the internet. **At the end:** your full view
answers on `https://wow-full.example.org` behind a password, the sites for friends on
`https://wow.example.org`, both over HTTPS, and the shipper sends over HTTPS.

Out of the box everything listens on `127.0.0.1` only. To reach it from elsewhere, put a
reverse proxy in front: a server that takes HTTPS on ports 80 and 443 and forwards each
request to the right container. The kit ships three, each tested: [Caddy](#caddy) (the
shortest: it gets certificates by itself), [Traefik](#traefik) and [nginx](#nginx). Pick one.

## Two names, on purpose

| Name | Serves | Login |
|---|---|---|
| `wow.example.org` | The static sites for friends: the public one at `/`, the sealed one at `/zogzog/` | None. Files only, no cookie, so a link to it can go anywhere |
| `wow-full.example.org` | Your full view, `/ingest` for the shipper, the MCP endpoint for your AI | Yours, on the page; the shipper and the MCP have their own |

Two names keep the public one free of any login, cookie or session to steal. Use your own
domain; any two names work.

## 1. Names and a password

1. Point both names at your server's public address (DNS `A` or `AAAA` records). From home,
   forward ports 80 and 443 on your router to the server.
2. In `deploy/compose/.env`:

   ```sh
   WOW_SITE_HOST=wow.example.org
   WOW_FULL_HOST=wow-full.example.org
   BASIC_AUTH_USER=me
   BASIC_AUTH_PASSWORD=a long random password, 12 characters or more
   SITE_URL=https://wow.example.org
   ```

The proxy files turn the page's login on by themselves (`AUTH=basic`, whatever `UI_AUTH`
says) and refuse to start without the user and password. The proxy reaches the containers over
Docker's network, so the `127.0.0.1` that keeps a plain install private no longer keeps
anyone out: the login does. It covers every page of the full view except `/healthz` and
`/ingest` (the shipper has its own password).

The page answers only for the names it knows: localhost, IP addresses, the host of
`MCP_PUBLIC_URL` and `ALLOWED_HOSTS`, which the proxy files set to `WOW_FULL_HOST`. Any other
name gets a 421, so a web page can't read yours through your browser
([Security notes](../security.md)).

`SITE_URL` makes the printed `/zogzog/` link use the public name. Keep `UI_PUBLISH`,
`SITE_PUBLISH` and `MCP_PUBLISH` on `127.0.0.1`.

## Caddy

Caddy gets and renews the certificates from Let's Encrypt by itself. From `deploy/compose`:

```sh
docker compose -f compose.yaml -f compose.caddy.yaml up -d --build
```

The routes are in [`deploy/compose/caddy/Caddyfile`](../../deploy/compose/caddy/Caddyfile):
the MCP paths to port 8090, the rest of the full name to 8080, the public name to the static
server.

Server only reachable on your home network, so Let's Encrypt can't check it? Add
`tls internal` inside each site block of the Caddyfile: Caddy then signs with its own
authority, and your devices warn until you trust it.

## Traefik

1. Add the address Let's Encrypt writes to before a certificate expires, in `.env`:

   ```sh
   ACME_EMAIL=you@example.org
   ```

2. From `deploy/compose`:

   ```sh
   docker compose -f compose.yaml -f compose.traefik.yaml up -d --build
   ```

The routes are at the end of [`deploy/compose/compose.traefik.yaml`](../../deploy/compose/compose.traefik.yaml),
where compose fills in the two names from `.env`. Traefik never gets the Docker socket.

## nginx

nginx doesn't fetch certificates: bring one valid for both names (certbot, your own CA).

1. Copy it into `deploy/compose/nginx/certs/` as `fullchain.pem` and `privkey.pem` (certbot's
   names). They're never committed.
2. From `deploy/compose`:

   ```sh
   docker compose -f compose.yaml -f compose.nginx.yaml up -d --build
   ```

The routes are in [`deploy/compose/nginx/templates/wow.conf.template`](../../deploy/compose/nginx/templates/wow.conf.template);
nginx fills in the two names at start. It answers nothing for a name it doesn't serve, and lets
the shipper post up to 8 MiB at once, as the server does.

## 2. Check it

From any computer:

```sh
curl -s https://wow.example.org/healthz
curl -s -o /dev/null -w '%{http_code}\n' https://wow-full.example.org/
curl -s -u me:'your password' -o /dev/null -w '%{http_code}\n' https://wow-full.example.org/
```

The first prints `ok`; the second `401` (the login works); the third `200`.

## 3. Point the shipper at it

On the computer you play on, in the shipper's config (`[ship]`):

```toml
url = "https://wow-full.example.org/ingest"
```

The password stays `INGEST_PASSWORD`. Never send it over plain HTTP across the internet.

## What goes where

Every path, its port and its login: [HTTP endpoints](../reference/http-endpoints.md). Using
another proxy (HAProxy, your router's): follow that table and the files above. What matters
most:

- **A login on the full view.** With a proxy in front, `UI_AUTH=open` would let anyone the
  proxy lets in see everything. The kit's proxy files force `AUTH=basic`; do the same. The
  server enforces it: with `AUTH=open` on the full view, a request that carries
  `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto`, `Forwarded`, `Via` or `X-Real-IP`
  gets a 403, and `ALLOWED_HOSTS` with `AUTH=open` stops the server at start.
- **Pass the `Host` header through**, and add your full view's name to `ALLOWED_HOSTS`. A
  proxy that rewrites `Host` to `ui:8080` gets a 421.
- **`/ingest`, `/install/update/shipper*`, `/install/assets/*`, `/healthz` and the MCP paths skip your page
  login.** They have their own. A login in front of `/ingest` blocks the shipper; one in
  front of `/install/update/shipper` stops its auto-update; one in front of the MCP paths
  blocks every AI client.
- **Let cookies through on `/authorize`, `/consent` and `/oauth/callback`.** The AI login ties
  itself to your browser with a cookie; a proxy that drops it stops every login at the Approve
  button with "Authorization session mismatch".
- **Never route the share port** (8082 in the kit) or VictoriaLogs (9428).

A login proxy instead of the page's password (oauth2-proxy, Authelia, authentik), which
several people on one install need: [Deploy without compose, behind a reverse proxy](../deploy.md#behind-a-reverse-proxy).

## Next

[Set up the login for your AI](oidc.md) for claude.ai and ChatGPT, and
[Share your page](sharing.md) for the sites on the public name.
