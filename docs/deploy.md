# Deploying without compose

**For:** self-hosters on Kubernetes or any setup other than the compose kit. **At the end:** you know each command of the image, its port, its health check and its settings.

For Kubernetes or any setup other than `deploy/compose`. One image,
`ghcr.io/<owner>/wow-mcp:<version>` (the names are set in
`.github/workflows/release.yml`; or build it: `docker login dhi.io` once
with a free Docker Hub account, then `docker build .`), and these
commands. While the package is private, the cluster pulls it with a token
that has `read:packages` (an `imagePullSecrets` entry; docs/development.md,
"Using a released image").

| Command | Port | Health | Serves |
|---|---|---|---|
| `wow-stats ui` (the default `CMD`) | 8080 (`PORT`) | `GET /healthz`: 200 `{"status": "ok", "semantic": ...}`, no login, both views; alert on `"semantic": "failing"`. Also counts to graph: `analysis_workspaces` (open, at most `analysis_workspaces_max` unless requests hold more), `analysis_workspaces_held`, `uploads_running` and `uploads_waiting` (`uploads_max` at once) | the page, `/api/*`, `POST /ingest` when set up |
| `wow-stats exporter` | 9464 (`LISTEN_PORT`) | `GET /healthz` | `GET /metrics`: `wow_api_up`, `wow_api_paused`, `wow_exporter_*` (its own health, never Blizzard's data) |
| `wow-stats publish [--loop] [--force]` | none | its log line per build | writes the static sites to `PUBLISH_DIR` (or pushes them, below) |
| `wow-stats static` | 8080, upload on `UPLOAD_PORT` | `GET /healthz` | the files `publish` wrote, nothing else |
| `wow-stats zogzog-key rotate` | none | | a new key for the sealed link, and a rebuild |
| `wow-stats render` (image `wow-mcp-render`, `docker build --target renderer .`) | 8083 (`PORT`) | `GET /healthz`: 200 once Chromium runs | `POST /render`: the paperdoll's picture and each past level's, for `publish` only (below) |
| `wow-stats zogzog-key link` | none | | prints the sealed link (never logged) |

## The container

One image for every command, with no third-party binary and no model: the
play analysis and the plans are written by the user's own assistant over
the MCP (docs/mcp.md), or, for the analysis only, by the AI provider the
host configured, through its API (docs/self-host/server-analysis.md).

- Runs as uid/gid 10001, `ENTRYPOINT ["wow-stats"]`.
- Works with a read-only root filesystem. Only `/tmp` needs to be writable
  (an `emptyDir`); `HOME=/tmp`. Nothing is written at runtime: bytecode is
  compiled into the image.
- Built on Docker's hardened Python image (Debian 13, glibc): no shell and
  no system package manager in it, so `docker exec ... sh` finds nothing;
  use `docker exec ... python -c ...` or `wow-stats ...`. Drop all
  capabilities, `allowPrivilegeEscalation: false`.
- Logs go to stdout: one line per request (method, path, status) for the UI,
  status codes and counts for the exporter. Never prices or secrets.

## `wow-stats ui`

One process serves one view, so a routing mistake can't hand the full view to
an outsider. Run two Deployments for two views.

Every view reads the character's whole history: game events are kept for
good (give VictoriaLogs a retention of years: the compose kit says `100y`).
There is no time window to set. A query whose answer would pass the row
limit (20,000) is read again in two halves of its time range, as often as
needed, so a long history comes back whole rather than cut.

| Variable | Default | |
|---|---|---|
| `VIEW` | full on a loopback `BIND`, else required | `full` (you, behind a login) or `public` (anyone) |
| `AUTH` | `none` on loopback, else required | see "Logins" below |
| `BIND` | `0.0.0.0` in the image (`127.0.0.1` outside it) | |
| `PUBLISHED_ON` | empty | with `AUTH=open` off loopback: the address the container port is published on (the compose kit passes `UI_PUBLISH`). Not a 127.0.0.1 address: the server refuses to start |
| `ALLOW_OPEN` | off | `1`: `AUTH=open` on any bind, on purpose (a public view anyone may read) |
| `ALLOWED_HOSTS` | empty | host names the page is reached by, comma separated, besides `localhost`, IP addresses and the hosts of `PUBLIC_URL` and `MCP_PUBLIC_URL`. Any other `Host` gets a 421 (below, "Host names") |
| `PROXY_TRUSTED_CIDRS` | empty | with `AUTH=proxy`: the proxy's addresses as the server sees them (`127.0.0.1/32`, the Docker network's subnet (`docker network inspect <network>`), a pod network). A request from anywhere else gets a 403 |
| `PROXY_SECRET_FILE`, `PROXY_SECRET_HEADER` | empty, `X-Proxy-Auth` | with `AUTH=proxy`: a secret of 32+ characters the proxy sends in that header on every request. With `PROXY_TRUSTED_CIDRS` too, both must match. `AUTH=proxy` with neither refuses to start |
| `PORT` | `8080` | |
| `VL_URL` | `http://victorialogs:9428` | VictoriaLogs holding the events |
| `VL_ARCHIVE_URL`, `VL_ARCHIVE_AFTER_DAYS` | empty, `89` | optional second VictoriaLogs for days older than that; empty: one store holds everything |
| `LOGS_FILTER` | `feed:wow-*` | LogsQL filter that selects the events (add your own labels here) |
| `VM_URL` | empty | VictoriaMetrics the exporter pushes to. With `VIEW=public` only the character profile is read from it (the look, Battle.net's item level, the talents: the card shows them); no market or prices, which are never public |
| `DEFAULT_CHAR` | empty (latest seen) | `Name-Surname` |
| `WEIGHTS` | empty | the spec each character's gear is scored for, over what its add-ons say: `Testchar-Demo:shadow,Name-Surname:holy` (a spec key or name of the class). The page's own selector still wins. The card shows it next to the talents, which stay as the game has them |
| `PRICE_DAYS` | `90` | days of Auctionator scans in the prices panel |
| `CACHE_TTL` | `45` | seconds an answer worked out when asked is kept in memory. The answers a plain open asks for are built in the background once per new data instead (docs/architecture.md, "Caching") |
| `VL_CONCURRENCY` | `2` | VictoriaLogs requests this process runs at once, all callers together (docs/architecture.md, "Caching") |
| `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD` or `BASIC_AUTH_PASSWORD_FILE` | | for `AUTH=basic`, 12+ characters |
| `INGEST_PASSWORD` or `INGEST_PASSWORD_FILE`, `INGEST_USER` (`wow`) | off | turns on `POST /ingest` with its own basic auth, 12+ characters |
| `ANALYSIS_STATE_DIR` | off | the play analysis' state (full view only): per character, the kept findings and the open run a client drives over the MCP, plus the plans. With `ANALYSIS_HOUR` (`4`, local time: the nightly recheck of kept findings, no model), `WOW_TIMEZONE` (the player's IANA timezone for hours and weekdays, `Europe/Brussels` when unset), `ANALYSIS_SHARE_PORT` (the /zogzog/ lines for the publisher, which reads `ANALYSIS_SHARE_URL`), `KB_GAME_TABLES`. How it works: docs/mcp.md, "The play analysis" |
| `ANALYSIS_SHARE_TOKEN_FILE` | `<WOW_STATE_DIR or ANALYSIS_STATE_DIR>/share-token` | the share port's bearer token, 32+ characters, asked on every request. Made (0600) on the first start when the file is missing. The publisher and the exporter read the same file (a shared volume, or a secret mounted in all three) through the same variable |
| `ANALYSIS_SHARE_BIND` | `BIND` | where the share port listens. Never route it from outside |
| `ANALYSIS_PROVIDER`, `ANALYSIS_MODEL`, `ANALYSIS_API_KEY_FILE` or `ANALYSIS_WIF_*`, `ANALYSIS_MONTHLY_BUDGET`, `ANALYSIS_STALE_PLAYED_HOURS`, `ANALYSIS_AUTO`... | off until credentials are set | the server's own analysis (full view only): its provider, credentials, budget and timer. Every one: docs/self-host/server-analysis.md and docs/reference/environment.md |
| `MCP_PUBLIC_URL` | off | the MCP endpoint (full view only, needs the image's `mcp` extra, which the image has): the https address clients use, ending in `/mcp`. Off until set |
| `MCP_PORT` | `8090` | the MCP's own port, on `BIND`: FastMCP serves `/mcp` and the OAuth paths there, never on `PORT` ("Routes" below) |
| `MCP_AUTH` | `oauth` | `none` only on a loopback `BIND` (a laptop) |
| `MCP_LOGIN` | `oidc` | the only value; `password` refuses to start |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET_FILE` | required with `MCP_PUBLIC_URL` | the identity provider and its app (code flow, client secret, refresh tokens, callback `https://<host>/oauth/callback`) |
| `OIDC_ALLOWED_SUBS` | required unless `WOW_STATE_DIR` | account ids allowed in, comma separated |
| `OIDC_REQUIRED_ROLE`, `OIDC_ALLOWED_ORGS` | empty | one person per install: a project role and the org ids allowed (from the claims below). Ignored with `WOW_STATE_DIR`: the friends list decides |
| `OIDC_ORG_CLAIM`, `OIDC_ROLES_CLAIM` | ZITADEL's (`urn:zitadel:iam:...`) | the claim names holding the org id and the roles: set your provider's |
| `OIDC_SCOPES` | `openid offline_access` (+ the org scope with `OIDC_ALLOWED_ORGS`, one person per install) | what the app asks the provider for. Every authorize request also sends `prompt=select_account`: the provider shows its account picker |
| `OIDC_BATTLENET_ORG_ID`, `OIDC_BATTLENET_IDP_ID` | empty | ZITADEL only, both or neither: a "Sign in with Battle.net" button on the MCP consent page, straight to that provider in that org |
| `MCP_SIGNING_KEY_FILE` | random per start | 32+ random bytes: signs the tokens and encrypts the stored logins. Without it every restart logs every client out |
| `MCP_STATE_DIR` | memory only | client registrations and logins, encrypted, in `<dir>/oauth/` |
| `MCP_REDIRECT_URIS`, `MCP_CIMD_HOSTS` | the built-in client lists (docs/mcp.md) | replace the lists of allowed redirects and of hosts whose client documents (CIMD) are fetched |
| `MCP_DCR` | `open` | dynamic client registration (`/register`): `open` to any client, `loopback` only for local clients (every redirect on 127.0.0.1, localhost or [::1]), `off` for none (CIMD only, narrowed by `MCP_CIMD_HOSTS`; `/register` answers 404 and the metadata has no `registration_endpoint`) |
| `MCP_ALLOWED_ORIGINS` | empty | extra browser origins allowed on `/mcp` |
| `MODEL3D` | `on` | the "View in 3D" toggle on the paperdoll (full view, and the static sites `publish` builds); drawn from the game files the person's shipper sends, nothing downloaded; `off` removes it and its routes, and the shipper stops sending them |
| `WOW_ITEMS` | `on` | item names, icons and tooltip cards from Forever's tables on wago.tools (`ui` and `publish`); `off`: none of it is fetched, the pages show what the events carry. `MODEL3D=off` with `WOW_ITEMS=off` is the small preset: no upstream downloads at all, a cold cache and no internet still publish |
| `RENDER_FROM` | empty | where this pod gets a character's picture (`/render/<char>.webp`): the publisher's still of the 3D model, read from the static site at that address; empty, no picture (the 3D toggle). With several people the request names the person's own site (their host, as the static server maps it) and reads their public bundle, else their sealed `/zogzog/` copy, opened with their key, so a page that isn't public still shows its picture |
| `MODEL3D_ASSETS_DIR`, `MODEL3D_ASSETS_MB` | `<MODEL3D_CACHE_DIR>/assets`, `512` | where the game files each person's shipper sends for the 3D view are kept (with several people: `users/<id>/assets`, removed with the person), and how much one person's may take |
| `MODEL3D_CACHE_DIR` | `<tmp>/wow-stats-model3d` | where the downloaded game tables and icons are kept |
| `MODEL3D_WAGO_URL`, `MODEL3D_PRODUCT` | `https://wago.tools`, `wowf` | Forever's game tables for items, talents and the KB (tests point it at a local server) |
| `ROUTE_REFERENCE_FILE` | the bundled Polestar route | another route times file, or `off` |
| `STATIC_DIR` | the packaged `web/` | serve the page from another folder (development) |

### Logins

| `AUTH` | Checks | Use when |
|---|---|---|
| `none` | nothing | `BIND=127.0.0.1` only; refused on any other bind |
| `open` | nothing | a port published on 127.0.0.1 only (`PUBLISHED_ON`), or on purpose with `ALLOW_OPEN=1` (a public view). Refused on any other bind |
| `proxy` | that the request came from the proxy (`PROXY_TRUSTED_CIDRS`, `PROXY_SECRET_FILE`; one of them required) | a reverse proxy in front does the login (OIDC, forward auth): "Behind a reverse proxy" below |
| `basic` | HTTP basic auth on every route but `/healthz` and `/ingest` (and, with several people, `/install/<code>` and `/privacy`) | no proxy in front |

Every mode checks the `Host` header too (below), and the share port its
own token. None of it relies on a network policy or on what the proxy
strips: the server refuses what it can't trust, and refuses to start on a
setting that would leave it open, with what to set.

### Host names

A web page can make its visitor's browser read a no-login server on their
own machine (DNS rebinding: `evil.example` first resolves to the attacker,
then to `127.0.0.1`). The browser still sends `Host: evil.example`, so the
server answers only for `localhost`, IP addresses (a rebinding page can't
make the browser send one), the hosts of `PUBLIC_URL` and `MCP_PUBLIC_URL`,
and `ALLOWED_HOSTS`. Anything else gets a 421 that says what to set.
`/healthz` and `/ingest` (it has its own login) answer any host.

### Behind a reverse proxy

`AUTH=proxy`: the proxy logs people in and tells the app who they are in
`PROXY_USER_HEADER` (`X-Forwarded-User`; with several people, the login's
`sub`). The app believes that header only on a request from the proxy:
give both a secret (`PROXY_SECRET_FILE` on the app, the same value sent in
`X-Proxy-Auth` by the proxy). `PROXY_TRUSTED_CIDRS` (the proxy's address as
the app sees it) is the weaker option: every container on that network can
send the header too, so use it alone only when the proxy has the network to
itself, or next to the secret. Without either the server doesn't start. The proxy must **replace** the identity header on every route,
including the ones without a login (`/ingest`, `/install/*`, `/privacy`):
a proxy that passes on a header the client sent lets anyone claim to be
anyone. The app refuses the header when it comes twice or holds a list
(a proxy that appends), but it can't see a header the proxy left alone.

The examples below use oauth2-proxy for the login (any forward-auth
service works: Authelia, authentik, Pomerium) with `--set-xauthrequest`
and `--user-id-claim=sub`, so its `X-Auth-Request-User` carries the
provider's `sub`: set `PROXY_USER_HEADER=X-Forwarded-User` and map it as
shown. `/mcp` and the OAuth paths go to port 8090 without the login
(docs/mcp.md); they're left out here.

The app, on a Docker network shared with the proxy:

```
VIEW=full AUTH=proxy PUBLIC_URL=https://wow.example.org
PROXY_SECRET_FILE=/run/secrets/proxy   # 32+ characters, the same value sent by the proxy
PROXY_TRUSTED_CIDRS=<subnet>          # optional on top: docker network inspect <network>
```

On a flat network where other containers share the proxy's subnet, the
CIDR alone trusts them too: use the secret, or give the proxy and the app
a network of their own.

**Caddy**

```
wow.example.org {
	# Whatever the client sent goes first, on every route.
	request_header -X-Forwarded-User
	request_header -X-Auth-Request-User

	@open path /ingest /install/* /privacy /privacy/* /healthz
	handle @open {
		reverse_proxy wow-ui:8080 {
			header_up X-Proxy-Auth {env.WOW_PROXY_SECRET}
		}
	}
	handle /oauth2/* {
		reverse_proxy oauth2-proxy:4180
	}
	handle {
		forward_auth oauth2-proxy:4180 {
			uri /oauth2/auth
			copy_headers X-Auth-Request-User>X-Forwarded-User
			@login status 401
			handle_response @login {
				redir * /oauth2/sign_in?rd={scheme}://{host}{uri}
			}
		}
		reverse_proxy wow-ui:8080 {
			header_up X-Proxy-Auth {env.WOW_PROXY_SECRET}
		}
	}
}
```

**nginx**

```
server {
    server_name wow.example.org;
    # listen 443 ssl; certificates...

    # proxy_set_header replaces the client's header; an empty value drops it.
    proxy_set_header Host $host;
    proxy_set_header X-Proxy-Auth "<the secret>";   # or leave out and trust the address

    location /oauth2/ {
        proxy_pass http://oauth2-proxy:4180;
    }
    location = /oauth2/auth {
        internal;
        proxy_pass http://oauth2-proxy:4180;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
    }
    location ~ ^/(ingest|install/|privacy|healthz) {
        proxy_set_header X-Forwarded-User "";
        proxy_set_header Host $host;
        proxy_set_header X-Proxy-Auth "<the secret>";
        proxy_pass http://wow-ui:8080;
    }
    location / {
        auth_request /oauth2/auth;
        auth_request_set $wow_user $upstream_http_x_auth_request_user;
        error_page 401 = /oauth2/sign_in;
        proxy_set_header X-Forwarded-User $wow_user;
        proxy_set_header Host $host;
        proxy_set_header X-Proxy-Auth "<the secret>";
        proxy_pass http://wow-ui:8080;
    }
}
```

A `proxy_set_header` inside a `location` replaces every one set above it:
that's why they repeat.

**Traefik** (Docker labels on the app's container)

```
traefik.http.routers.wow.rule=Host(`wow.example.org`)
traefik.http.routers.wow.middlewares=wow-strip,wow-login
traefik.http.routers.wow-open.rule=Host(`wow.example.org`) && (Path(`/ingest`) || PathPrefix(`/install/`) || PathPrefix(`/privacy`))
traefik.http.routers.wow-open.middlewares=wow-strip
# An empty value removes the header the client sent.
traefik.http.middlewares.wow-strip.headers.customrequestheaders.X-Forwarded-User=
traefik.http.middlewares.wow-strip.headers.customrequestheaders.X-Auth-Request-User=
# The sign-in redirect for a 401 needs one more middleware (oauth2-proxy's
# Traefik page has it, as does any forward-auth service's).
traefik.http.middlewares.wow-login.forwardauth.address=http://oauth2-proxy:4180/oauth2/auth
traefik.http.middlewares.wow-login.forwardauth.authResponseHeaders=X-Auth-Request-User
traefik.http.services.wow.loadbalancer.server.port=8080
```

Traefik copies the login's `X-Auth-Request-User` as is: set
`PROXY_USER_HEADER=X-Auth-Request-User` on the app with this one. A secret
in Traefik sits in a label or a middleware (`customrequestheaders.X-Proxy-Auth`),
readable by whoever reads your container config; trusting Traefik's address
(`PROXY_TRUSTED_CIDRS`) works too, on a network that holds only Traefik and
the app: any other container on it could claim to be anyone.

`/ingest` always has its own login and only exists when `INGEST_PASSWORD` is
set. `/healthz` never asks for one, so probes need no credentials.

The MCP endpoint is not on this port: FastMCP serves `/mcp` and its OAuth
paths on `MCP_PORT` (8090) with its own tokens, since claude.ai and Claude
Code can't carry the page login (docs/mcp.md lists the paths). Route them
to that port without the proxy's login.

### Routes

| Route | Public | Full |
|---|---|---|
| `/`, `/app.js`, `/app.css`, `/base.css`, `/theme.js` | yes (full-only sections stripped server side) | yes |
| `/api/config`, `/api/chars`, `/api/char`, `/api/journal` | allow-listed fields | everything |
| `/api/market`, `/api/prices`, `/api/logsql` | 404 | yes |
| `/mcp`, `/authorize`, `/token`, `/register`, `/revoke`, `/consent`, `/oauth/callback`, `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource` (and `/mcp` under it) | none | on `MCP_PORT` (8090) when `MCP_PUBLIC_URL` is set, never on this port: route them there without the page login |
| `/model3d/*`, `/api/model3d` | 404 | the 3D view, while `MODEL3D` is on: its page (`/model3d/view`, own CSP), scripts, and the person's own game files by id (`/model3d/file/<id>`, `private` caching) |
| `FULL_DOOR_PATH` (`/full/`, `/full`) | 404 | 302 to `/` (the login door: the proxy in front sends people through its login there; empty turns it off, for a full view on its own host) |
| `/render/<char>.webp` | with `RENDER_FROM` | same | the paperdoll's picture: the publisher's still of the 3D model, read from the static site; 404 until it's drawn (the page keeps the silhouette and the 3D toggle) |
| `/api/items`, `/icons/<size>/<name>.jpg` | items only | the tooltips' item cards; the icons in the full view (fetched from `wow.zamimg.com` once, cached by name) |
| `POST /ingest` | when `INGEST_PASSWORD` is set | same |

The full view makes outbound requests from the server only:
`wow.zamimg.com` (the item icons, cached on disk), `wago.tools` (Forever's
item tables: display ids, the tooltips' cards, enchant names; checked once
a day), the static site (`RENDER_FROM`) for the picture, and, with the
server's own analysis on, the AI provider's API. The browser
only talks to this server. A public server
makes none.

A typical pair behind a login proxy:

```
public pod: VIEW=public AUTH=open  BIND=0.0.0.0 ALLOW_OPEN=1
full pod:   VIEW=full   AUTH=proxy BIND=0.0.0.0 PUBLIC_URL=https://wow-full.example.org PROXY_TRUSTED_CIDRS=<the proxy's network>
```

## `wow-stats exporter`

Needs **your own** Battle.net API client ([Battle.net data](self-host/battlenet.md)). Keeps
Blizzard's data in memory only and pushes it to a VictoriaMetrics with a
retention under 30 days (§2.18 of the API terms). Never let a long-term
scraper (Prometheus, vmagent) scrape its `/metrics` for game data: it only
serves its own health there.

| Variable | Default | |
|---|---|---|
| `BNET_CLIENT_ID_FILE`, `BNET_CLIENT_SECRET_FILE` | required | files holding your client's id and secret |
| `BNET_REGION`, `BNET_LOCALE` | `eu`, `en_GB` | |
| `BNET_FOREVER_NAMESPACE`, `BNET_TOKEN_NAMESPACES` | derived from the region | |
| `BNET_API_URL`, `BNET_OAUTH_URL` | Blizzard's | |
| `PROFILE_CHARACTERS` | empty | comma list of `Name-Surname` (the WTF folder names): profile, appearance and equipment every 2 hours |
| `BNET_PROFILE_NAMESPACE` | `profile-forever-<region>` | |
| `TOKEN_INTERVAL`, `REALM_INTERVAL`, `PROFILE_INTERVAL` | `1200`, `300`, `7200` | seconds between API polls; the profile pass runs on the clock (every 2 hours: 00:00, 02:00... UTC), and a character listed for the first time is read within about 5 minutes |
| `HTTP_TIMEOUT` | `60` | |
| `VM_PUSH_URL` | empty (nothing stored) | `.../api/v1/import/prometheus`. It may already carry `extra_label=...` query args: the exporter appends its own with `&`, never replaces them |
| `VM_PUSH_EXTRA_LABELS` | empty | comma list of `name=value` added as `extra_label` |
| `VM_PUSH_INTERVAL` | `60` | |
| `VM_MERGE_KEY_FILE` | empty | the VictoriaMetrics `-forceMergeAuthKey`, as a file. With it the exporter force-merges once a day so expired data is really deleted |
| `VM_MERGE_URL` | `VM_PUSH_URL`'s host + `/internal/force_merge` | |
| `VM_MERGE_INTERVAL` | `86400` | |
| `LISTEN_PORT` | `9464` | |

The VictoriaMetrics behind it: `-retentionPeriod=23d` and
`-forceMergeAuthKey` (the comment in `deploy/compose/compose.yaml` explains
the 23 days), no backups, no copies.

### Appearance and the 30-day rule

Forever's profiles are on the Profile API without a realm:
`/profile/wow/character/testchar-demo` in `profile-forever-eu`, with a
client-credentials token (a character the API hasn't seen yet answers
404). Everything it returns is Blizzard API data, kept 30
days at most (§2.18), so the look travels like the market data and never
through VictoriaLogs:

1. The exporter reads each character of `PROFILE_CHARACTERS` every 2 hours and
   keeps the answer in memory: `wow_character_customization{character,
   option_id}` = choice id, `wow_character_profile{character, race_id,
   gender, class_id, faction}` = level, `wow_character_equipped{character, slot}` = item id,
   `wow_character_equipped_stat{character, slot, stat}` = that item's stat
   (a random suffix's are in no item table),
   `wow_character_profile_refreshed_timestamp_seconds`,
   `wow_character_talent_points{character, tree, order}` = points spent and
   `wow_character_talent_rank{character, spell_id, talent}` = rank (the
   active group of `/specializations`), and
   `wow_character_profile_found` = 0 for a 404 (its other series are
   dropped). Each call (profile, appearance, equipment, talents,
   achievements) replaces its own series only when it answers, and records
   when in `wow_character_part_refreshed_timestamp_seconds{part}`; the
   character's refreshed time is the oldest part's, so a 503 on one call
   never makes the rest look fresh. Every profile series carries a
   `snapshot` label (a hash of which series the character has), and
   `wow_character_snapshot{character, snapshot}` marks the newest one. It
   stops pushing a value 3 polling intervals after its last read and forgets
   anything not refreshed for 30 days.
2. The exporter's VictoriaMetrics stores them 23 days, force-merged daily.
3. The full view reads the last value (`last_over_time(...[2d])`) of the
   newest complete snapshot only (a talent dropped in a respec is gone at
   once; after an exporter restart whose first read failed half way, the
   last complete one, flagged with its age) when it
   draws the 3D model, and every view reads the talents the same way for the
   character card (never stored anywhere else, so there is no talent
   history). A character Battle.net doesn't know gets its race's default
   look and no talents: no add-on stands in.
4. The public site's static bundle (`wow-stats publish`) gets the look the
   same way when it is built. The bundle is rebuilt at least once a day and
   the old one replaced, so no copy outlives the 30 days. The bundle holds the
   option and choice ids.
5. The armory's item level: the profile's `equipped_item_level`,
   `wow_character_item_level{character, kind}`. No `/character-media`:
   Blizzard serves its renders on paths shared by character id across the
   Classic games, and one showed a stranger in a character's place.
   Nothing downloads from Blizzard's render host. The paperdoll's picture is our renderer's still of the 3D
   model instead (`RENDER_URL`): the publisher draws it from the same answer
   as the 3D view, keeps it per tenant, character and look (a new item draws
   it again, 25 days at most), and puts it in the bundles
   (`data/render/<char>.webp`, sealed in `/zogzog/`).

### Battle.net data and models

No Battle.net data reaches the server's own analysis (its tables have no
Battle.net source). The MCP's `market` tool (the WoW
Token price and ruleset status from Blizzard's API) is neither listed nor
callable, whatever the env, except for a person who turned on "Battle.net
data to my AI" in their settings on a multi-user install (their own data to
their own assistant;
`tests/route/test_market_gating.py`). A chat keeps a tool's answer longer
than Blizzard's 30 days (§2.18), so that switch is the person's call. The
character's current talents follow the same switch on
a multi-user install; on a one-person install the MCP reads them when
`VM_URL` is set. Either way they're read live from vm-wow, never copied. The analysis' tables (`wowstats.analysis.TABLES`) are
all built from the player's own game files in VictoriaLogs; `gear`,
`talents`, `gear_sheets` there come from the shipper, not the profile API.
A test fails when a prep table isn't in that list, so a new source can't
reach a model without being classified.

## The static sites: `wow-stats publish` and `wow-stats static`

Three ways in:

| Where | What | Served by | Login |
|---|---|---|---|
| `/` | the public view, static: the character card, the paperdoll, the climb, dungeons cleared, level by level | `wow-stats static` | none |
| `ZOGZOG_PATH` (`/zogzog/`) | for friends with the link, static, data sealed: the public view plus loot, the Overview's next moves, the journal, deaths as counts; no other players | `wow-stats static` | none: the key is the link's `#fragment` |
| the full view's own host, or `FULL_DOOR_PATH` (`/full/`) on the same one | the live full view (MCP, Ask links, LogsQL box) | `wow-stats ui` with `VIEW=full` | your proxy's |

Two hosts is the better split (`wow.example.org` static only,
`wow-full.example.org` the full view at `/`). The public
host then serves files and nothing else: no login, no cookie, no session to
steal, and a link to it can go anywhere. Set `FULL_DOOR_PATH=` (empty) and
`MCP_PUBLIC_URL=https://<full host>/mcp` on the full view, route its whole
host through the login, and redirect the old paths (`/full/`, `/sesame/`,
`/mcp`, `/oauth2/`) on the public host to the new one, permanently. One host
with `/full/` still works for a standalone install (docs/install.md).

`publish` is the only part that talks to VictoriaLogs, the exporter's VictoriaMetrics, Wowhead's icon
host (`wow.zamimg.com`), wago.tools and the renderer (`RENDER_URL`).
Every 10 minutes (`PUBLISH_INTERVAL`)
it rebuilds when the newest event's time moved (after each play session: the
shipper sends at logout and `/reload`), when the exporter's Battle.net data changed
(the profile, the look, the equipment: a hash of their last readings), or when
the build is a day old. Each build holds every answer its page asks for, as
files under `data/`, each cut down to its tier's allow-list in
`wowstats/redact.py` (`PUBLIC`, `ZOGZOG`), plus, under `model3d/file/<id>`,
the game files each character's 3D recipes need, from that person's own
files (`MODEL3D_ASSETS_DIR`; with several people, the full view's share
port, `/assets/<slug>/`). The log gives each character's count and size.

`/zogzog/`'s data files are AES-256-GCM, one random 96-bit nonce each, the
file's path as associated data, under the 256-bit key in `ZOGZOG_KEY_FILE`.
The page takes the key from `location.hash` and opens them with WebCrypto.
Browsers never send a fragment, so the key isn't in a request, a log or a
`Referer`; it stays in the address bar so the link can be bookmarked. No key
or a wrong one shows an empty "Nothing here." page. The page, scripts, fonts
and 3D files are in clear (the same for everyone). `zogzog-key rotate` makes
a new key and rebuilds: every old link stops working. The publisher holds
the key; the static server never does.

The 30-day rule: both bundles carry Battle.net data (the look from the
profile API; in `/zogzog/` also the token and ruleset status), so a build is never older
than a day and each build replaces the last; at most two older builds stay
on disk for requests in flight.

### How the build reaches the static server

- **Compose**: a shared volume, `publish` writes and `site` reads it
  read-only (`deploy/compose/compose.yaml`). The key is made on the first run
  in that volume and never logged: print the link with
  `docker compose run --rm publish zogzog-key link`.
- **Kubernetes**: two pods, because the static pod must have no route out
  at all and NetworkPolicies are per pod. The static pod starts empty (its
  files live in an `emptyDir`) and listens on `UPLOAD_PORT` too; the
  publisher pushes each build there as a tar.gz with a bearer token
  (`PUBLISH_PUSH_URL`, `PUBLISH_PUSH_TOKEN_FILE` / `UPLOAD_TOKEN_FILE`, 32
  characters or more), and on every pass asks which build is live and pushes
  again when the pod restarted. A shared RWX volume would work as well; a
  push needs no storage class and no node pinning.

| Variable | Default | |
|---|---|---|
| `PUBLISH_DIR` | `/data/site` | where builds go; `current` points at the live one |
| `PUBLISH_INTERVAL` | `600` | seconds between checks with `--loop` |
| `ZOGZOG_PATH` | `/zogzog/` | the sealed site's path; empty: not built, not served |
| `ZOGZOG_KEY_FILE` | `<PUBLISH_DIR>/../zogzog.key` | 43 base64url characters; made (0600) when missing |
| `HELP_KEY_FILE` | unset: no help page | The MCP help page's own key (`/help/#<key>`, docs/mcp.md), 43 base64url characters; never made: no file, no page |
| `HELP_MCP_URL` | `MCP_PUBLIC_URL` | The MCP address the help page shows |
| `ZOGZOG_LINK_BASE` | empty | the site's address, for `zogzog-key link` |
| `PUBLISH_PUSH_URL`, `PUBLISH_PUSH_TOKEN_FILE` | empty | push builds to a static server instead of sharing a volume |
| `SITE_DIR` (static) | `/data/site` | the publisher's `PUBLISH_DIR`, or the `emptyDir` it pushes to |
| `UPLOAD_PORT`, `UPLOAD_TOKEN_FILE` (static) | off | the push listener; never route it from outside |
| `FULL_DOOR_PATH` (ui) | `/full/` | the login door of the live full view; empty: off |
| `PLAUSIBLE_DOMAIN` | empty | your Plausible site's domain: pageviews on the static sites and, set on the ui too, the full view (below); empty: no analytics at all |
| `PLAUSIBLE_SCRIPT`, `PLAUSIBLE_API` | `/js/script.js`, `/api/event` | the script and event paths, on the site's own address |
| `RENDER_URL` | empty: no pictures | the renderer (`wow-stats render`, below): the paperdoll's picture and each past level's |
| `RENDER_TOKEN_FILE` | required with `RENDER_URL` | the renderer's token, the same file the renderer reads (below). Without it the publisher draws no pictures and says why in its log |
| `RENDER_BUDGET_S`, `RENDER_TIMEOUT_S` | `60`, `120` | seconds a build spends drawing (the rest wait for the next pass), and per picture |

### The pictures: `wow-stats render`

Its own image, `ghcr.io/<owner>/wow-mcp-render:<version>` (the
Dockerfile's `renderer` target: Debian, Playwright's headless Chromium, no
fonts), because Chromium runs there without its own sandbox (no user
namespaces in an unprivileged container).
`POST /render` needs `Authorization: Bearer <token>`: the file in
`RENDER_TOKEN_FILE` (32+ characters), or, when that's unset, a token the
renderer makes on its first start in `RENDER_CACHE_DIR/render.token` (0600;
the log names the path, never the value). Hand the same file to the
publisher as its `RENDER_TOKEN_FILE`. Anything else that reaches the port
gets a 401, whatever the network around it allows.
Give it nothing else: no other Secret, `automountServiceAccountToken: false`, no route
to the stores or the other pods; in from the publisher only, no way out at
all (it needs no DNS either). It runs as uid 10001 with a read-only
root filesystem and `/tmp` writable (Chromium's profile and the game files
the publisher sent, one folder per person: `RENDER_CACHE_DIR`, `RENDER_CACHE_MB` 256 in all). One render at
a time, about 4 s at one core; measured peak 850 MB with its cache, so a
1.5 GiB limit. The publisher sends a recipe (file ids, never a name), and
the files it lacks when it answers 428 (`POST /file/<id>`, the same token,
`X-Content-SHA256`) (docs/architecture.md, "A past level's picture").

### Visitor counts (Plausible)

With `PLAUSIBLE_DOMAIN` set, the public and `/zogzog/` pages load
`/js/script.js` and send one pageview to `/api/event`, both on the site's
own address: route those two paths, and nothing else, to your Plausible in
the reverse proxy (`/js/script.js` to its `/js/script.manual.js`, with
`Host` set to Plausible's), so the CSP stays `'self'` and no visitor's
browser talks to a third party. The static pod itself still makes no
request.

Set the same `PLAUSIBLE_DOMAIN` on the ui and every page of the full view
counts too (the privacy note, the 404 and refusal pages included; never the
3D viewer's frame, which would count the visit twice). Route the same two
paths on the full view's name to Plausible, ahead of its login, with its
cookies stripped; the page loads `stats.js` from `/privacy/stats.js`, which
the app serves before any login, like the rest of `/privacy/*`.

One `PLAUSIBLE_DOMAIN` for every host (the public page, each friend's
`<slug>-wow` page, the full view) means one Plausible site: a new friend's
page counts with no setup, and Plausible's Hostname filter (or the Pages
report broken down by URL) tells the hosts apart. If you set a hostname
allow list in Plausible (Site settings, Shields, Hostnames), cover them all,
for example `*.example.org`.

The pageview's address is the page's path alone (`/`, `/zogzog/`,
`/settings`): the page sends it explicitly (`web/stats.js`) instead of
`location.href`, and the manual script has no hash tracking, so `/zogzog/`'s
key (the fragment) and the query never reach Plausible; an install code in a
path is replaced by `code`. The referrer is the site that sent the visitor,
when their browser says: never another wow page, since every page answers
with `Referrer-Policy: no-referrer`.
`tests/test_browser.py` runs Plausible's own script in Chromium on a
`/zogzog/#<key>` address and checks every field of the payload and every
header; `tests/test_full_stats_browser.py` does the same on the full view.

### Item icons, tooltips and the render

Every item on every page shows the game's icon (Wowhead's renders of the
game files, 56 px in a slot, 36 px in a list) and the in-game tooltip on
hover, tap or Enter. The cards come from Forever's tables on wago.tools
(`Item`, `ItemSparse`, `ManifestInterfaceData`, `ItemSet`, `ItemSubClass`,
`SpellItemEnchantment`) plus Polestar's item stats; the publisher writes
the cards and icons of the items each bundle names (`data/items.json`,
`data/icon/<size>/<name>.jpg`, sealed in `/zogzog/`). The middle of the
paperdoll is our renderer's still of the 3D model (above), or a
silhouette while there is none; "View in 3D" swaps it for the model in
place.

`publish` needs the image's `publish` extra (pyca/cryptography, for AES-GCM:
the standard library has none). Everything else stays standard library.

## Several people on one install

`WOW_STATE_DIR` on `wow-stats ui` turns it on. Each person gets their own
VictoriaLogs tenant (`AccountID`), state, ingest token and zogzog key. The
owner stays tenant 0 on the original store.

**The friends list.** With `WOW_FRIENDS_FILE` (`{"<sub>": "<slug>"}`, the slug possibly `""`, rendered by
whatever manages your accounts, from a group in your identity provider for
instance), the list alone decides who is
a friend. The full view reads it at start and watches it while it runs
(its content every 10 seconds, plus a full pass every `FRIENDS_RECONCILE_S`,
120, that retries what failed), so a change applies without a restart: a
listed person gets a tenant with that slug before their first
login (or at it, if they're quicker than the pass); a person who left the list loses access
at once (status `removed`: the web view, the MCP, `/ingest` and install codes refuse them,
and the publisher takes their site off every static pod within a minute), and
`FRIENDS_PURGE_DAYS` (7) later their data goes (`wow-stats users delete`'s steps: their
tenant's events, state, registry entry, ingest token). Listed again before that, they're
back with everything. The clock is `removed_at` in the registry, so a restart neither
resets nor skips it; `wow-stats users delete <slug> --yes` skips it for one person. A login
that isn't listed gets no tenant. A valid list that would remove every friend at once
(`{}`, or only the owner's logins) is refused and logged as `empty_list_refused` unless
`FRIENDS_ALLOW_EMPTY=1` or `wow-stats users reconcile --allow-empty`. A
missing, unreadable or unparseable file (or one with a bad entry) changes
nothing, and the owner is never in play. The slug comes from the list
only: friends don't pick it, and once set it never changes (a new value in
the list logs `slug_kept`). Where to set it per identity provider:
[Add friends](self-host/friends.md#you-pick-each-friends-name). Each step is one JSON log line (`"event": "friends"`). Without the
file, only the owner gets in. Mount the file's folder, never the file
alone: a Kubernetes ConfigMap mounted with `subPath` (or a one-file
Docker bind mount an editor replaces) never shows the server a new
version. A ConfigMap mounted as a directory is updated in place within
about a minute; don't put a checksum of it on the pod, or every new friend
restarts the full view. Needs `VIEW=full`, `AUTH=proxy` and
`MCP_AUTH=oauth` (the full view refuses to start otherwise).

**Who is who.** The login proxy sets `X-Forwarded-User` (the provider's
`sub`) and strips any the client sent. The sub is all the app reads: the
owner is any sub in `WOW_OWNER_SUB`, a friend is a sub on the list, and nobody else
gets in, whatever their name, email, org or roles. The web view and the MCP
use the same rule (`tenancy.Tenancy.identify`; the MCP's auth calls
`resolve_tenant(sub)` at each login and refresh). Org and role belong to
whatever renders the list: no org header, and `OIDC_ALLOWED_ORGS` is
ignored in this mode. A refused login gets a short page: signed in but not let in
yet, ask the person who runs the site, then reload; and a log-out link
(`/oauth2/logout`) to switch accounts. Your login provider may then let
anyone through to that page (no role or group check of its own): the
friends list is the lock either way.

| `wow-stats ui` | |
|---|---|
| `WOW_STATE_DIR` | the registry (`users.json`, 0600) and each person's state, on a volume |
| `WOW_OWNER_SUB` | the owner's login id; comma-separated for an owner with several logins (one linked to Battle.net): each is the owner on the web, the MCP and /admin, with one tenant, one set of data and settings and one analysis timer per character. One also on the friends list stays the owner's: nothing is made or deleted for it, and the start log names it by its place in this list (`owner_also_listed`, `position`) |
| `WOW_OWNER_SLUG` (empty by default: set your own), `WOW_OWNER_ALIASES` | the owner's site name, and hosts that are theirs too (`wow.example.org`): all reserved for them. Pages never print it to friends: they say "the person who runs this site" |
| `WOW_FRIENDS_FILE`, `FRIENDS_RECONCILE_S` (120) | who gets in besides the owner, `{"<sub>": "<slug>"}` (a slug may be `""`, or a plain list of subs: named from `PROXY_BATTLETAG_HEADER` at the first login), watched for changes (mount its folder, not the file); how often a full pass runs anyway. Unset: the owner only |
| `FRIENDS_PURGE_DAYS` (7), `FRIENDS_ALLOW_EMPTY` | days between a friend leaving the list and the deletion of their data (`0`: at once; the privacy page quotes it); `1` applies a list that removes every friend at once, refused otherwise |
| `FRIENDS_VL_URL` | the friends' VictoriaLogs (one tenant each); unset: no friends |
| `FRIENDS_VL_DELETE_KEY_FILE` | its `-deleteAuthKey`, for `wow-stats users delete` |
| `ZOGZOG_MASTER_KEY_FILE` | 32+ random bytes, base64url: each person's zogzog key is derived from it. The owner's `ZOGZOG_KEY_FILE` only seeds their first link: after "Make a new link" their key is derived too, so the full view and the publisher agree without sharing a file |
| `PUBLIC_URL`, `SITE_DOMAIN` | the full view's address (install commands, MCP), and the domain friends' sites live under (`<slug>-wow.<domain>`). `PUBLIC_URL` must be https (plain http only on 127.0.0.1 or localhost): every install and update runs what that address sends |
| `INGEST_DAILY_MB` (512) | what `/ingest` accepts from one person a day; past it a 429, and the shipper keeps its spool for the next day. `0`: no limit |
| `PRIVACY_BACKUP_DAYS` (empty) | how long your backups keep a person's events, for the privacy page ("Backups of your events expire N days later"). Empty: the page tells friends to ask you |
| `PRIVACY_LOG_DAYS` (empty) | how long your logs keep a request: the web server's access log in front of the app and the app's own log, whichever keeps longer. For the privacy page ("These lines are kept N days"). Empty: the page tells friends to ask you |
| `PLAUSIBLE_DOMAIN` (on the full view too) | set it here as on the publisher: every full-view page counts, and the privacy page says what Plausible gets ("Visitor counts" above) |
| `PROXY_USER_HEADER` | default `X-Forwarded-User`; believed only from the proxy (`PROXY_TRUSTED_CIDRS`, `PROXY_SECRET_FILE`: "Behind a reverse proxy") |
| `PROXY_BATTLETAG_HEADER` | empty by default; the header the proxy puts the BattleTag in, to name a listed friend with no slug at their first login (`name-3f9a`, never the `#1234`: docs/self-host/friends.md, "Names from the BattleTag"). The proxy replaces any copy the client sent |
| `DOH_URL` | a DNS-over-HTTPS resolver to tell whether a friend's host resolves yet (egress to it) |

Routes added to the full view: `/start` (set up, then "is it working?"; the whole manual at `/start/print`),
`/settings`, `/help/` (the help page, unsealed: docs/mcp.md), `/api/me*`, `/api/admin/hosts` (owner only: the hosts that
should resolve), `/install/<code>` and `/privacy` without the login (the
code is the credential: single use, 15 minutes), `/ingest` with basic auth
`<slug>:<token>`.

| Other commands | |
|---|---|
| `wow-stats publish` | `USERS_URL` (the full view's share port, `/users.json`) builds one site per person into `PUBLISH_DIR/<slug>/`, pushes with `?slug=`, and drops sites of people who left from every static pod (`GET /bundles` on the upload port). Needs `ANALYSIS_SHARE_TOKEN_FILE`, the full view's share token |
| `wow-stats static` | `SITE_DOMAIN` serves `<slug>-wow.<domain>` from `SITE_DIR/<slug>/current`; `SITE_ALIASES=wow.example.org=alex`; any other host, or a site whose public page is off, is a 404 |
| `wow-stats exporter` | `PROFILE_CHARACTERS_URL` (share port, `/profile-characters`) adds every person's characters to `PROFILE_CHARACTERS`, each labelled with its person's account (at most 12 a day per friend; a name two people send goes to the owner or to nobody). Needs `ANALYSIS_SHARE_TOKEN_FILE` too |
| `wow-stats users list\|hosts\|reconcile [--allow-empty]\|disable\|enable\|delete <slug> [--yes]` | in the full view's pod; `list` shows each removed friend's `purge_at`; `reconcile` applies the friends list now; `delete` runs VictoriaLogs' delete task for the tenant, then removes the state and the registry entry (dry run without `--yes`), without waiting for `FRIENDS_PURGE_DAYS` |

Revoking access: `wow-stats users disable <slug>` refuses the web view and
the MCP at the next request, and so does removing the person from the friends list. Removing only
their access in your identity provider refuses their next login, and the MCP at its next upstream check
(within the hour); an open web session lasts until the proxy's session
expires, so take them off the list too when it must stop now. AccountIDs are never reused.

