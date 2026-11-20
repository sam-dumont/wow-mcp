# Security notes

**For:** self-hosters checking what an install has to get right, and anyone reviewing wow-stats' security. **At the end:** you know the settings that keep an install closed and which findings were left open on purpose.

What an install has to get right, and what the pre-publication review
(2026-11-19, 4 medium, 12 low, 12 info findings) left open on purpose. How to
report a vulnerability: [SECURITY.md](../SECURITY.md).

The app is meant to be safe on its own: a port published by mistake, a flat
Docker network or a proxy that passes client headers through must not open
anything. Network policies and a reverse proxy are a second layer, never the
only one.

## What the medium findings changed

Each one is enforced by the app, whatever sits in front of it.
`tests/test_deploy_shapes.py` starts it in the shapes people really run
(compose behind Caddy, nginx or Traefik; a port published by mistake; a
proxy that passes the client's headers through; a flat Docker network) and
checks each attack is refused. Setup recipes: [deploy.md](deploy.md),
"Host names" and "Behind a reverse proxy".

| Finding | Now |
|---|---|
| M1 DNS rebinding reads a local install | the web server answers only for localhost, IP addresses and the hosts of `PUBLIC_URL`, `MCP_PUBLIC_URL` and `ALLOWED_HOSTS`; any other `Host` gets a 421 that says what to set |
| L6 the full view with no login on any address | `AUTH=open` starts only on a loopback `BIND`, when `PUBLISHED_ON` says the port is published on 127.0.0.1 (the compose kit passes it), or with `ALLOW_OPEN=1` |
| M4 `AUTH=proxy` believed any client's header | it doesn't start without `PROXY_TRUSTED_CIDRS` or `PROXY_SECRET_FILE`; a request from anywhere else gets 403, and an identity header sent twice or appended to (a comma) gets 400 |
| M3 the share port was open and served sealed data | a bearer token on every request (`ANALYSIS_SHARE_TOKEN_FILE`, made 0600 on first start), 127.0.0.1 unless `ANALYSIS_SHARE_BIND` says otherwise; the publisher and the exporter send it, and the compose kit shares the file through the `ui-state` volume |
| M2 Wowhead's viewer ran in the app's origin | gone since the 3D view draws with its own viewer (`web/model3d/m3d/`, no eval, no WebAssembly). The frame stays sandboxed (`allow-scripts` only: an opaque origin) with no network; the page around it fetches only `model3d/file/<id>` (the person's own files, behind the login) and keeps the /zogzog/ key |
| Info 2 the relay's maps grew with every path asked | the relay is gone |
| L1, L2 `play_sql` | the authorizer is in place before anything compiles, PRAGMA and every non-SELECT are refused, and a value is capped at 64 KB while a query runs (#65, 64 KB since 0.6.1: at 1 MB a sort held about 0.5 GB) |

## What the low findings changed

| Finding | Now |
|---|---|
| L3 install codes burned by link previews | `/install/<code>` is redeemed only by a GET from curl or PowerShell, the two clients the commands use. A HEAD (link checkers) or any other client (Slack, Discord, iMessage previews, a browser) gets a note and the code stays valid. The command lines didn't change |
| L4 plain-http `PUBLIC_URL` | refused at start unless the host is 127.0.0.1 or localhost |
| L5 `POST /api/plan` with any Content-Type | `application/json` only, like `/api/me/*`: a cross-site form can't send it without a preflight, and none is ever granted |
| L7 no per-person ingest quota | `INGEST_DAILY_MB` (512) per person and UTC day; past it a 429, and the shipper keeps its spool until the next day |
| L8 no socket timeouts or connection cap | every listener (`ui`, `static`, `render`) closes a connection silent for 30 s and serves at most 256 at once; one more is closed right away (`wowstats/httpserve.py`) |
| L9 the 100-client registration cap could be filled by anyone | a full list drops the oldest registration that never logged in. Clients that did log in are kept on disk, so a restart doesn't make them look unused |
| L10 kb-refresh resolved anthropic at run time next to a write token | `anthropic` is in the locked `kb` group (hashes in `uv.lock`); `GH_TOKEN` is only in the steps that call GitHub, never the model passes; the checkout keeps no credentials; `inputs.mode` reaches shell through `env` |
| L11 Renovate automerged patches with no soak time | Renovate is gone (#64): Dependabot, 7-day cooldown, nothing automerged |
| L12 the privacy page stated one install's operations | the visit-count line needs `PLAUSIBLE_DOMAIN` on the full view, the backup line `PRIVACY_BACKUP_DAYS`, the log line `PRIVACY_LOG_DAYS`; without those two the page tells friends to ask the owner |
| Info 1 the renderer had no login | `POST /render` needs a bearer token (`RENDER_TOKEN_FILE`, or one made on first start in the cache folder); the publisher sends it from its own `RENDER_TOKEN_FILE` |
| Info 4 activity times on the public page | the Settings toggle says the page shows when you cleared each dungeon and your time per level |
| Info 6 "scoped" LogsQL | the docstring now says what it is: a filter, not a boundary. The tenant header is the boundary |
| Info 7 no release attestation | done in #64: provenance, SBOMs, and attestations plus cosign in a public repository |
| Info 9 other players' names reach the AI provider | said on the privacy page |
| Info 10 docs drift and lab names in comments | `WOW_OWNER_SLUG` documented as empty by default; the lab's store name, its identity provider and the owner's phone are out of the comments (and the compose file's "prints the link" comment) |
| Info 11 the owner's first zogzog key wasn't length-checked | refused unless it is 256 bits, as the publisher does |
| Info 12 friends trust the operator with code execution | said on the privacy page |

## What the 0.6.0 review changed

The pre-launch review of 0.6.0 (2026-11-20) found nothing a stranger can use
against the image or the compose kit. These are friend-level and
operator-trust items.

| Finding | Now |
|---|---|
| D1 the exporter read Blizzard's Auction House API | gone: no auction loop, no `wow_auction_*` series. Auction prices come only from the player's own Auctionator scans |
| S1 Battle.net data keyed by character name only | every profile series carries the `account_id` of the person whose character it is, and every reader (pages, `/render/`, the 3D view, the MCP, the publisher) asks for its own account. A name two people send goes to the owner if it's theirs, else to nobody, so naming someone else's character reads nothing. The exporter reads at most 12 characters a day per friend, newest first |
| S2 `play_sql` could hold about 0.5 GB through the sorter | a value is capped at 64 KB, so the sorter spills to disk |
| S4 a host proxy in front of `AUTH=open` exposed the full view | with `AUTH=open` the full view answers 403 to any request carrying proxy headers (`X-Forwarded-*`, `Forwarded`, `Via`, `X-Real-IP`), refuses to start with a non-empty `ALLOWED_HOSTS`, and the 421 page says to set a login. `ALLOW_OPEN=1` and the public view are exempt |
| S5 a friend could grow the shared process's memory | `/api/model3d` takes only a known `sex`, `/api/plan` only a known character; the per-key lock maps are refcounted or striped |
| S6 repository rulesets while private | not available on a private free repository: merge commits are off until the switch, then the rulesets |
| S7 privacy page statements | the page says the /zogzog/ link exists for everyone (it opens for whoever has it), that the app's log names a character in a few lines, and that the program updates itself without asking |

**Left open: a friend can still make the exporter read names nobody here
owns.** Forever has no proof that a character is yours short of a Battle.net
login. Such a name is read for that friend only, at most 12 a day, and it's
Blizzard's public armory data.

## Accepted findings

**L7, the LogsQL box.** `/api/logsql` stays for friends: it's the full view's
way to look at your own raw events. A query only reads the person's own
tenant, and the process runs at most `VL_CONCURRENCY` (2) store requests at
once, all callers together. The cost cap belongs to VictoriaLogs: run it with
`-search.maxQueryDuration`, `-search.maxConcurrentRequests` and
`-search.maxQueueDuration` (checklist below).

**L10, egress `audit` rather than `block`.** The monthly sweep fetches every
URL the knowledge base cites, on hosts nobody lists in advance. A block list
would fail the sweep each time a guide moves. The job holds no token while it
reads outside text, and its output is a PR a person reviews.

**L8, a slow client can still hold a slot.** The timeout is per read, so a
client that sends one byte every 29 s keeps its connection. 256 of those
fill the server. For anything open to the internet, put a proxy with its own
header and body timeouts in front.

**L3, a client that pretends to be curl.** It can still redeem a code. That
takes someone who has the link and means to break the install: the code is a
bearer for 15 minutes anyway, and the next one is a click away.

**Info 2, icons keep no record of a miss.** `/icons/*` on the full view
fetches an unknown icon name from Wowhead's CDN each time it's asked. The
name must match the icon pattern, the route is behind the full view's login,
and nothing piles up in memory: the cost is one small outbound request per
bad name, to a CDN that answers 404 fast.

**Info 3, sealed paths name characters.** `/zogzog/data/char/<name>.json.enc`
answers 200 or 404, and file sizes hint at how much history there is. The
names are the ones the public page shows when it's on. Hashing every path
would cost the bundles their readable layout for very little.

**Info 4, the public tier shows when you played.** Dungeon clear times and
time per level are part of the public page on purpose: they are the climb.
The Settings toggle now says so; turn the public page off to hide them.

**Info 8, two downloads without a pinned hash.** The installers fetch uv from
astral.sh over https (its official installer), and the renderer image gets
Playwright's Chromium from Playwright's CDN. Both move with their projects'
releases; pinning them by hash would break every install on their next
release. The Polestar Recorder download is pinned.

**The owner's overview.** `/admin` answers the owner only (`WOW_OWNER_SUB`); a
friend gets the same 404 as any unknown address, and a request without a login
never reaches it. It shows activity times, counts and versions per person, never
game content, keys, hashes or install codes, and its answers are not logged.
The last full-view visit and MCP use are one timestamp each per person
(`users/<account>/activity.json`, written at most every 5 minutes), deleted
with the person. The publisher reports its pass over the share port
(`POST /publisher.json`, same token); the full view keeps only the known
fields, capped.

**S3, the shipper updates itself from the server.** At most every 2 hours
the program on each player's computer fetches the server's manifest and, on
a newer version, swaps itself in and reruns its installer, without asking.
Since 0.7.0 each release signs the shipper files (their sha256 and the
version) with an Ed25519 key held in the `shipper-signing` environment, and
the shipper pins the public half: it takes an update only when the signature
checks out, and only the files it names, the installer it reruns included.
A hostile server can now hold players back on their version, not run its
own code on their computers. What stays: shippers from 0.5.1 to 0.6.x take
their update to 0.7.0 on the server's hashes alone, and the first install
and the update line on `/start` run what the server sends (you paste them
yourself). Players can set `auto_update = false` ([shipper.md](shipper.md)).
One key, no rotation: a lost or leaked key means a new key in a release and
the update line once on every PC.

## Operator checklist

- `PUBLIC_URL` is https.
- With `AUTH=proxy`: `PROXY_TRUSTED_CIDRS` is the proxy's network only, the
  proxy replaces the identity header (never appends), and the app's port
  isn't reachable from anywhere else.
- The share port's token file (`ANALYSIS_SHARE_TOKEN_FILE`) is given to the
  publisher and the exporter, and to nothing else.
- VictoriaLogs runs with `-search.maxQueryDuration` (30s is plenty for every
  page), `-search.maxConcurrentRequests` and a memory limit.
- The renderer and the publisher share one `RENDER_TOKEN_FILE` (32+
  characters: `head -c 32 /dev/urandom | base64`).
- On the full view: `PRIVACY_BACKUP_DAYS` set to what your backups really
  keep, `PRIVACY_LOG_DAYS` to what your log store really keeps, and
  `PLAUSIBLE_DOMAIN` when the sites count visits: the full view then counts
  its own pages too, and the privacy page says so.
- The reverse proxy's access log leaves out query strings: the page's API
  calls carry character names there (`/api/loot?name=...`).
- A reverse proxy with timeouts sits in front of anything reachable from the
  internet.
