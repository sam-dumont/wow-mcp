# Upgrade

**For:** self-hosters moving to a newer version. **At the end:** you're on the new version with
your data, and you know how to go back.

Your data lives in Docker volumes, which an upgrade doesn't touch. The third-party images
(VictoriaLogs, VictoriaMetrics, the proxies) are pinned by digest in the compose files: they
change only when you take a new version of this repository.

## Before you start

1. Read the [changelog](../../CHANGELOG.md) for every version between yours and the new one.
   Each entry ends with what you must change, under "Upgrading" when there's
   something: a renamed variable, a new volume, a route to add to your proxy. Your version:
   `docker compose exec ui wow-stats --version`.
2. [Back up](backup.md) the events volume and `ui-state`.

## From a copy of the repository (the default)

From the top of your copy:

```sh
git fetch --tags
git checkout v0.7.0
cd deploy/compose
docker compose up -d --build
```

Use the version you want instead of `v0.7.0`. With a release's source zip instead of `git`:
unpack the new zip next to the old one, copy `deploy/compose/.env` and `deploy/compose/secrets/`
across, and run the last two lines from the new copy. The volumes are found by name, so the
data follows.

Then check: `docker compose ps` shows every service `running` (or `healthy`), and the page
opens with your characters.

## From a released image

With `WOW_STATS_IMAGE` set in `.env` ([Using a released image](../development.md#using-a-released-image)),
put the new version's `tag@digest` from its release in that line, then:

```sh
docker compose pull ui publish site
docker compose up -d
```

## From 0.6.x to 0.7.0

Nothing to change for a one-person install: back up, check out `v0.7.0`, `docker compose up -d
--build`. Then, depending on what you run:

- **Your players' shippers** take 0.7.0 by themselves within 6 hours of your server moving
  to it, no reinstall, and check the signature of every update after it (below). A 0.6
  shipper can't read a release candidate's version: it waits for the final. One still on an
  older version 7 hours later gets the update command on its start page, and shows in the
  [admin overview](admin.md).
- **The character's picture** needs the renderer now: `RENDER_URL` on the publisher and
  `RENDER_FROM` on the full view ([Share your page, pictures](sharing.md#pictures-optional)).
  Blizzard's armory renders are gone: the exporter stops reading them, nothing downloads from
  Blizzard's render host, and the old cache of those downloads (`MODEL3D_CACHE_DIR/render`) is
  deleted at start. Without the renderer the paperdoll shows a silhouette and the 3D toggle.
- **Building from source** needs `docker login dhi.io` once (a free Docker Hub account). The
  released images pull as before.
- **The server's own analysis** is new and stays off until you configure an AI provider
  ([Server-side analysis](server-analysis.md)); `ANALYSIS_AUTO=off` keeps it off with one.
- **Your AI's login** now shows your identity provider's account picker at **Allow Access**:
  pick your own account ([Set up the login for your AI](oidc.md#3-check-it)).
- **Optional, with friends**: `WOW_OWNER_SUB` takes a comma-separated list when you sign in
  with more than one account, and a friend listed without a name can get one from their
  BattleTag (`PROXY_BATTLETAG_HEADER`, [Add friends](friends.md#names-from-the-battletag)).
- **Battle.net** (`PROFILE_INTERVAL`): the exporter now reads every 2 hours on the clock
  instead of once a day. If you set `PROFILE_INTERVAL` yourself, it now also picks the clock
  slots: 86400 runs at 00:00 UTC.
- **The live public view** (`ui-public`) with several people: give it `USERS_URL` and
  `ANALYSIS_SHARE_TOKEN_FILE`, or it keeps showing every character in your store
  ([Add friends](friends.md)).
- **Other players' data**: 0.7.0 shippers send other players as "another player", and
  `/ingest` cuts 0.6 shippers' events the same way. What was stored before keeps what 0.6
  sent. [The event contract](../contract.md), "Stored before 0.7.0", has the LogsQL filters
  that find it, if you want it gone: read what they select before deleting anything.

The full list: the [changelog](../../CHANGELOG.md).

## The shipper

It follows the server: at most every 2 hours it asks the server it ships to for its version,
and takes the new one when it's newer and signed ([Auto-update](../shipper.md#auto-update)).
A shipper from before 0.5.1 can't do that yet: run the installer again once
(`shipper/install-mac.sh`, or
`powershell -ExecutionPolicy Bypass -File .\shipper\install-windows.ps1` from the new
copy; players on a friend's server run the update line from their start page,
[Updates](../players/getting-started.md#updates)). It keeps your config and archive.
0.6.x updates itself within 6 hours of the server moving to 0.7.0: no reinstall.

To pin the shipper to the version it has, put `auto_update = false` at the top of its
config.

### Signing your own build

The shipper only takes an update signed with the key it pins (`UPDATE_KEY` in
`shipper/wow_stats.py`, the project's by default). The official images carry the project's
signature, and so does an image you build from a release tag. Change a shipper file and
build your own: sign it with your own key before `docker build`, or your players' shippers
keep their version.

```sh
uv run --no-project python tools/release/shipper_sign.py keygen --out ~/wow-update-key.pem
uv run --no-project python tools/release/shipper_sign.py sign --key ~/wow-update-key.pem --pin
docker build .
```

`--pin` writes your public key into `shipper/wow_stats.py`; `sign` writes
`shipper/update-signature.json`. Keep the `.pem` out of the repository. Shippers installed
from an image with the other key refuse your first update: run the update line once on each
PC, and from then on they follow your key.

## Going back

Check out the old version and run `docker compose up -d --build` again (or put the old image
back in `WOW_STATS_IMAGE`). The events stay readable: their format and VictoriaLogs' version
only change with a note in the changelog. Plans and findings written by the newer version may
not read in the older one: restore your `ui-state` backup if they don't.
