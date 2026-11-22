# When it stops

**For:** people who run a wow-stats server and see an error, a refusal or an empty page.
**At the end:** you've matched the message to its fix, or you know what to put in an issue.

A player on your server with an empty page or a missing part: send them
[Something's wrong](../players/troubleshooting.md) first. Most of those are an addon switched
off, or the game not having saved yet.

**Help, in four steps:** find your message in the tables below; read the
[changelog](../../CHANGELOG.md)'s "Upgrading" notes for the version you moved to;
[search the issues](https://github.com/sam-dumont/wow-mcp/issues?q=is%3Aissue); then open one
with the bug form. A security problem goes through [the security policy](../../SECURITY.md),
never an issue.

## First look

From the kit's folder (`deploy/compose`):

```sh
docker compose ps
docker compose logs --tail 50 ui
curl -s http://127.0.0.1:8080/healthz
```

`ps` says what runs and what restarts in a loop. A service that won't start prints why as the
last line of its log, word for word as in the table below. On the full view, `/healthz`
answers `{"status": "ok", "semantic": "ok"}` once the page serves (`"idle"` before its first
build). `"semantic": "failing"` means the page's numbers couldn't be computed: the page keeps
its last good answers, marked out of date, and the `ui` log has a `"event": "semantic_failed"`
line saying why.

On the computer someone plays on, the little program checks itself: every line a tick, or a
cross with the one thing that fixes it.

<div class="os" data-os="windows">

**Windows (PowerShell).**

```powershell
uv run --script "$env:LOCALAPPDATA\wow-stats\wow_stats.py" --preflight
```

</div>

<div class="os" data-os="mac">

**Mac (Terminal).**

```sh
uv run --script ~/.local/share/wow-stats/wow_stats.py --preflight
```

</div>

## The server won't start

| What the log says | What to do |
|---|---|
| `AUTH=open on BIND=... anyone who reaches this port gets in without a login` | The page has no login and its port is open to your network. Keep `UI_PUBLISH` on `127.0.0.1`, or set `UI_AUTH=basic` with `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD` in `.env` |
| `AUTH=open with ALLOWED_HOSTS set` | A name to answer for means another computer or a proxy in front. Set a login (`UI_AUTH=basic` or `proxy`), or empty `UI_ALLOWED_HOSTS` |
| `AUTH=basic needs BASIC_AUTH_USER and a BASIC_AUTH_PASSWORD (or _FILE) of 12+ characters.` | Both in `.env`, the password 12 characters or more |
| `INGEST_PASSWORD (or _FILE) needs 12+ characters.` | The upload password the shipper sends: make it longer, and put the same one in the shipper's config |
| `AUTH=proxy trusts the login proxy's identity header, so the app must know which requests come from that proxy` | Set `PROXY_TRUSTED_CIDRS`, `PROXY_SECRET_FILE`, or both. [Behind a reverse proxy](../deploy.md#behind-a-reverse-proxy) |
| `PUBLIC_URL=http://...: use https` | The install commands run what this address sends, so it has to be https. Plain http works on `127.0.0.1` or `localhost` only |
| `MCP_PUBLIC_URL must be the https URL clients use for /mcp` | The address your AI reaches, ending in `/mcp`. [The login for your AI](oidc.md) |
| `MCP_LOGIN=oidc needs OIDC_ISSUER (https), OIDC_CLIENT_ID and OIDC_CLIENT_SECRET_FILE` | The AI login isn't filled in. [The login for your AI](oidc.md#2-give-them-to-wow-stats) |
| `MCP_LOGIN=oidc needs OIDC_ALLOWED_SUBS` or `OIDC_ALLOWED_SUBS is empty` | Put your own `sub` there, or every account your provider knows could read your data |
| `WOW_STATE_DIR (several people) needs VIEW=full and AUTH=proxy` | Friends need a login proxy in front of the full view. [What a shared install needs](friends.md#what-a-shared-install-needs) |
| `WOW_STATE_DIR needs WOW_OWNER_SUB` | Your own `sub` at the identity provider, so your data stays yours |
| `set BNET_CLIENT_ID_FILE and BNET_CLIENT_SECRET_FILE` (the exporter) | The exporter needs your own Battle.net API client. [Battle.net data](battlenet.md), or leave its profile off |
| `Bind for 127.0.0.1:8080 failed: port is already allocated` | Another program uses 8080. Set `UI_PUBLISH=127.0.0.1:18080` in `.env` and start again |

## The page refuses a request

| What the browser shows | What to do |
|---|---|
| `This server takes requests through its login proxy only.` (403) | The request didn't come from the proxy the app trusts. Check `PROXY_TRUSTED_CIDRS` against the proxy's address as the container sees it |
| `This server has no login (AUTH=open) and this request came through a proxy.` (403) | A reverse proxy in front of a page with no login. Set `UI_AUTH=basic` or `proxy` first |
| 400 on every page behind the proxy | The proxy sends the identity header twice, or as a list. It has to replace it on every request. [Add friends](friends.md#what-a-shared-install-needs) |
| A friend sees "No access yet" | They signed in, but they aren't on the friends list yet. [The friends list](friends.md#the-friends-list) |
| `the data store didn't answer; try again in a minute (request 1a2b3c4d in the server's log)` (502, on the page, in the shipper's log or from the AI) | VictoriaLogs didn't answer. The page never shows its address or its error; the server's log does, on the line with the same request id: `docker compose logs ui \| grep 1a2b3c4d`. An upload refused this way counts nowhere (not against the daily budget either), and the shipper sends it again at its next run |
| `the query needs more memory than a query may use` (from `play_sql`) | That one query built a value past 512 MB, so its process stopped. The server carries on; ask your AI to aggregate or narrow the query |

## Nothing arrives

The shipper writes one line per run in its log: Mac `~/Library/Logs/wow-stats.log`, Windows
`%LOCALAPPDATA%\wow-stats\wow-stats.log`.

| What the shipper's log says | What to do |
|---|---|
| `ship: <file> sent (N events)` | It works. The page has it within 5 minutes |
| `ship: <host>:<port> unreachable (...); N spool files wait` | The `url` in the config is wrong, or the server is down (`docker compose ps`). Nothing is lost: the files go with the next run that works |
| `ship: <file>: HTTP 401, kept for the next run` | The upload password doesn't match the server's `INGEST_PASSWORD`. On a shared server, the player gets a new install command from their start page |
| `ship: no [ship] url in <config>` | The config has no server address. [Fill in the config](../install.md#fill-in-the-config) |
| `events older than the receiver's retention (N days) kept in .../too-old, not sent` | `retention_days` is set in the config's `[ship]`. [Install it for real, troubleshooting](../install.md#4-troubleshooting) |

A new dungeon's loot without stats, or a 3D view still missing files: run the shipper once with `--prefill` (same command as `--preflight` above). It sends the [item facts and 3D files](../shipper.md#flags) the server still lacks now, instead of at the next daily check, and prints how many went.

## Server-side analysis

At start, the `ui` log has one line when it's configured: `analysis: the server's own runs on
...`, with the provider, the threshold and the budget. No such line: it's off, and
`dashboard_refresh` from your own AI says why (`server_automatic_refresh`, `off`). A run that
stopped shows its reason as a red flag on /admin ([Admin overview](admin.md)).

| What it says | What to do |
|---|---|
| `analysis not configured: this server has no Anthropic credentials` | Set `ANALYSIS_API_KEY_FILE`, or the `ANALYSIS_WIF_*` settings. [Server-side analysis](server-analysis.md#pick-a-provider-and-a-way-in) |
| `analysis not configured: workload identity federation needs ...` | One of the provider's federation IDs is missing: the message names it |
| `analysis not configured: ANALYSIS_MODEL is required with ANALYSIS_PROVIDER=openai` | Name a model that supports tool calling |
| `the identity token's audience is ..., not ...` | The projected token's `audience` doesn't match the provider: fix the volume, or set `ANALYSIS_WIF_AUDIENCE` |
| `a run would likely cross the budget` or `budget spent` | The month's `ANALYSIS_MONTHLY_BUDGET` is used up, or one more run would cross it. It resets on the 1st (UTC) |
| `off (ANALYSIS_AUTO)` at the end of the start line | On purpose: the server never starts a run. Players refresh from their own AI |

## Friends

Each change to the friends list is one log line with `"event"` in it:

```sh
docker compose logs ui | grep '"event"'
```

| Event | What to do |
|---|---|
| `list_unusable` | The friends file is missing, unreadable or not `{sub: name}`. Nothing changed, nobody was removed. Fix the file; the server rereads it within about 10 seconds |
| `name_conflict` | Two friends have the same name, or a name the app keeps (`full`, yours, `admin`...). Change it in the list. [You pick each friend's name](friends.md#you-pick-each-friends-name) |

## What to put in an issue

The bug form asks for these. Leave out real share links (`/zogzog/#...`), keys, tokens,
passwords and account names.

- The version: `docker compose exec ui wow-stats --version`.
- How it runs: the compose kit, Kubernetes, or `uv run`.
- The last lines of the log that broke: `docker compose logs --tail 50 <service>`, or the
  shipper's log.
- For the shipper, the `--preflight` output above.
