# Add friends

**For:** self-hosters who want several players on one server, each with their own page, start
page, sealed link and AI connection. **At the end:** you know what a shared install needs, how
the friends list decides who gets in, and how to add, switch off and remove someone.

A one-person install is the compose kit as it comes. A shared install adds a login proxy, a
second store and a few keys. It's the setup the start page (`/start`) and the per-person
install command exist for. Set it up once you have [HTTPS](reverse-proxy.md) and
[the AI login](oidc.md) working for yourself.

## What each friend gets

- An account at your identity provider (you make it there), and a name for their page's address
  (you pick it, in the friends list).
- Their own start page with a personal install command, their own upload password, page,
  sealed `/zogzog/` link, public page (off until they switch it on) and AI connection.
- Their own data, in their own tenant (a separate partition) of a VictoriaLogs for friends.
  They never see each other's data, and you see theirs only by looking in the store.

Send them [Join a friend's server](../players/getting-started.md) and the link to their start
page, `https://wow-full.example.org/start`.

## What a shared install needs

| Piece | Setting | Why |
|---|---|---|
| A login proxy in front of the full view | `AUTH=proxy`; the proxy **replaces** `X-Forwarded-User` with the provider's `sub` on every request, login or not | The page reads who is asking from that header, nothing else. oauth2-proxy, Authelia or authentik all do it. A header sent twice, or a list, gets 400 |
| The app knows its proxy | `PROXY_TRUSTED_CIDRS` (the proxy's network as the app sees it) and/or `PROXY_SECRET_FILE` (a 32+ character secret the proxy sends in `X-Proxy-Auth`) | Without one the full view doesn't start; a request from anywhere else gets 403. On a Docker network other containers share, use the secret, or a network for the proxy and the app alone |
| The full view's name | `PUBLIC_URL`, https | The page answers for its host (and `MCP_PUBLIC_URL`'s, `ALLOWED_HOSTS`), refuses plain http unless it's localhost |
| The AI login | `MCP_PUBLIC_URL` and the `OIDC_*` settings, same provider | Each friend's AI logs in as them ([Set up the login for your AI](oidc.md)) |
| A state folder on a volume | `WOW_STATE_DIR` | Who is who, each person's settings and upload password |
| You, the owner | `WOW_OWNER_SUB` (your `sub`), `WOW_OWNER_SLUG` (your site name) | You stay on the original store, with everything you had |
| The friends list | `WOW_FRIENDS_FILE` | Who gets in besides you (below) |
| A VictoriaLogs for friends | `FRIENDS_VL_URL`, and its `-deleteAuthKey` in `FRIENDS_VL_DELETE_KEY_FILE` | One tenant per friend; the key lets `users delete` erase a friend's events |
| A master key | `ZOGZOG_MASTER_KEY_FILE` (32 random bytes, base64url) | Each person's sealed-link key comes from it |
| Friends' sites | `SITE_DOMAIN` (sites at `<name>-wow.<domain>`) | Install commands and links |
| The publisher and static server, per person | `USERS_URL` and `ANALYSIS_SHARE_URL` on `publish`, `ANALYSIS_SHARE_PORT` on `ui`, `SITE_DOMAIN` on `static` | One site per person |
| The share port's token | `ANALYSIS_SHARE_TOKEN_FILE`: `ui` makes it (0600) on first start; give the same file to `publish`, the exporter and `ui-public` if you run it, and nothing else | Every request to the share port needs it; it listens on `127.0.0.1` unless `ANALYSIS_SHARE_BIND` says otherwise |
| The live public view, if you run it | `USERS_URL` and `ANALYSIS_SHARE_TOKEN_FILE` on `ui-public` too | It then follows your Settings like your static site does: public page off is a 404, hidden characters aren't there. Without them it shows every character in your store |
| An upload ceiling | `INGEST_DAILY_MB` (512 per person and day) | A friend's broken shipper can't fill your disk |
| Battle.net for friends (optional) | `PROFILE_CHARACTERS_URL` on the exporter (`http://ui:8091/profile-characters`) and the share port's token | Every friend's characters get Battle.net's copy every 2 hours, under their own account ([Battle.net data](battlenet.md)) |

The full view refuses to start with `WOW_STATE_DIR` unless `VIEW=full`, `AUTH=proxy`, the
proxy's trust and the MCP login are set. Caddy, nginx and Traefik recipes for the login proxy,
each replacing the identity header: [Deploy without compose, behind a reverse proxy](../deploy.md#behind-a-reverse-proxy). Every setting: [Environment variables, several people](../reference/environment.md#several-people-on-one-install).
The extra paths to route: [HTTP endpoints](../reference/http-endpoints.md#extra-full-view-paths-with-several-people-wow_state_dir).

## The friends list

A JSON file mapping each friend's `sub` at your identity provider to the name you give them (2
to 20 of a-z and 0-9), which becomes their page's address:

```json
{"3f2c9a10-5b7e-4c1d-9a8f-2e6b4d0c7a11": "frostbolt", "8d1e4f27-0c3a-4b9e-b5d2-7f6a1c9e3b40": "moonfire"}
```

The full view reads it at start and watches it while it runs: a change applies within about
10 seconds of the file changing, **without a restart**. Don't restart the server for a new
friend; nobody gets logged out. The same pass runs every 2 minutes anyway
(`FRIENDS_RECONCILE_S`), to retry anything that failed.

- **A new entry** gets a tenant with that name before their first login.
- **A removed entry** is switched off and offboarded on the spot: their events, settings,
  upload password and pages go. Their next login is refused.
- **`{}`** means nobody: every friend is offboarded.
- **A missing or broken file** changes nothing (a typo never offboards anyone). The log says
  why it was skipped.

Each change is one log line with `"event": "friends"`:

```sh
docker compose logs ui | grep '"event": "friends"'
```

Keep the file wherever your accounts live: render it from a group at your identity provider,
or edit it by hand.

**Mount the folder, not the file.** Some ways of mounting a single file never show the server a
new version: a Kubernetes ConfigMap mounted with `subPath`, or a Docker bind mount of one file
that your editor replaces instead of rewriting in place. Mount the directory that holds it
(`/run/wow/` with `WOW_FRIENDS_FILE=/run/wow/friends.json`). Kubernetes then updates a
ConfigMap's file in place, about a minute after the ConfigMap changes, so a change to the list
reaches the server within ~1 minute. Don't roll the pod on a list change either (a `checksum/`
annotation of the ConfigMap): the full view runs one copy, so a roll is a minute of downtime for
everyone.

### You pick each friend's name

The name in the list is where a friend's page address comes from. Friends don't choose it, and
the app reads no username, email or claim from the login to make one. Pick a handle
(`frostbolt`, not a real name) and tell them what it is when you send the invite. It can't
change once it's set: their links would break. Changing it in the list later only logs
`slug_kept`.

| Your accounts live in | The `sub` (the key) | The name (the value) |
|---|---|---|
| ZITADEL | the user's ID, on the user's page in the console | their ZITADEL username: render the list from the users holding your project's role, with the username as the value. Make each friend's username 2 to 20 of a-z and 0-9 |
| Keycloak | the user's ID (`kcadm.sh get users -r <realm> -q username=<name> --fields id`) | whatever you write, or the username if a script renders the list from the admin API |
| authentik, Authelia or another OIDC provider | what the provider sends as `sub` for that user: it depends on the provider's subject setting, so read it off one login's ID token | whatever you write, or copied by a script from the provider's API |

Two friends can't share a name, and the app keeps a few for itself: `full`, your own
(`WOW_OWNER_SLUG` and your aliases), and words like `www`, `api`, `admin`, `start` or `help`.
A clash is your mistake to fix, not theirs: the app logs one `name_conflict` line with the
name and the reason, `/admin` and `wow-stats users list` show it as a name conflict, and that friend's
start page says their address needs a fix from you, with nothing for them to do. Change the
name in the list and the next pass gives it to them; their page carries on by itself.

## Manage people

Run these where the full view runs, with its settings (in the compose kit,
`docker compose exec ui wow-stats users ...`):

| Command | Does |
|---|---|
| `wow-stats users list` | Everyone, with their status, tenant and when their data started |
| `wow-stats users reconcile` | Applies the friends list now (the server also picks up a change within ~10 seconds) |
| `wow-stats users disable <name>` | Refuses their page and their AI at the next request; their site goes at the next publish. Their data stays |
| `wow-stats users enable <name>` | Undoes `disable` |
| `wow-stats users delete <name>` | Says what would be deleted. Add `--yes` to delete their events, state and registry entry |
| `wow-stats users hosts` | The site hosts that should resolve in DNS |

Removing someone from the friends list does what `delete --yes` does. An open browser session
lasts until your login proxy's session ends: `disable` too when it must stop now.

Tenant numbers are never reused, so a deleted friend's data can't show up under someone new.

Who uses the install, when they last did and whether their sites build:
[Admin overview](admin.md).
