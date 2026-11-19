# Upgrade

**For:** self-hosters moving to a newer version. **At the end:** you're on the new version with
your data, and you know how to go back.

Your data lives in Docker volumes, which an upgrade doesn't touch. The third-party images
(VictoriaLogs, VictoriaMetrics, the proxies) are pinned by digest in the compose files: they
change only when you take a new version of this repository.

## Before you start

1. Read the [changelog](../../CHANGELOG.md) for every version between yours and the new one.
   Each entry ends with what an operator must change, under "Upgrading" when there's
   something: a renamed variable, a new volume, a route to add to your proxy. Your version:
   `docker compose exec ui wow-stats --version`.
2. [Back up](backup.md) the events volume and `ui-state`.

## From a copy of the repository (the default)

From the top of your copy:

```sh
git fetch --tags
git checkout v0.5.0
cd deploy/compose
docker compose up -d --build
```

Use the version you want instead of `v0.5.0`. With a release's source zip instead of `git`:
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

## The shipper

On the computer you play on, your own install: take the new version of the repository there
too, then run the installer again (`shipper/install-mac.sh`, or
`powershell -ExecutionPolicy Bypass -File .\shipper\install-windows.ps1`). It keeps your
config and archive. Players on a friend's server run the update line from their start page
([Update](../players/getting-started.md#update)).

## Going back

Check out the old version and run `docker compose up -d --build` again (or put the old image
back in `WOW_STATS_IMAGE`). The events stay readable: their format and VictoriaLogs' version
only change with a note in the changelog. Plans and findings written by the newer version may
not read in the older one: restore your `ui-state` backup if they don't.
