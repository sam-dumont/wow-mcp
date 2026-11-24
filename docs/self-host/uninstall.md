# Uninstall

**For:** self-hosters and players removing wow-stats. **At the end:** the programs are gone,
and you've decided separately what happens to the data.

Removing the programs deletes nothing you'd miss. Deleting the data is a separate step and
can't be undone: [back up](backup.md) first if you might want it.

## The server

From `deploy/compose`:

```sh
docker compose --profile exporter --profile public down
```

That stops and removes every container. The volumes, with your data, stay. Behind a proxy or
on the SQLite store, run it with the same files you started with (`COMPOSE_FILE` in `.env`
does that by itself; on the command line, `-f compose.yaml -f compose.caddy.yaml` and the
like): `down -v` only removes the volumes of the files it was given.

To delete the data too (every event, the sites and their key, the plans, the AI logins):

```sh
docker compose --profile exporter --profile public down -v
```

Then delete the copy of the repository, `.env` and `secrets/` included, and the images:
`docker image ls` lists them (`ghcr.io/.../wow-mcp` and its renderer, or `wow-stats:local` if
you built it), `docker image rm <name:tag>` removes one.

## The computer you play on

This stops the background job and removes the program and its config. Your archive stays.

Mac:

```sh
launchctl bootout gui/$(id -u)/local.wow-stats; rm -f ~/Library/LaunchAgents/local.wow-stats.plist; rm -rf ~/.local/share/wow-stats ~/.config/wow-stats
```

Windows (PowerShell):

```powershell
schtasks /Delete /TN wow-stats /F; Remove-Item -Recurse -Force "$env:LOCALAPPDATA\wow-stats", "$env:APPDATA\wow-stats"
```

To delete the archive too: the `WoWArchive` folder in your home folder
(`~/WoWArchive`, `%USERPROFILE%\WoWArchive`).

The addons stay in `_forever_/Interface/AddOns`: remove them in the CurseForge app, or delete
their folders.

## A friend's server

Players: [Delete your data](../players/privacy.md#delete-your-data).
