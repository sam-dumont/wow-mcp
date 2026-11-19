# Back up and restore

**For:** self-hosters who don't want to lose their history. **At the end:** you have a backup
of the server's data and the player's archive, and you've seen a restore work.

## What to keep

| Where | What | Back it up? |
|---|---|---|
| Volume `wow-stats_vl-data` | Every game event. The one that matters | Yes |
| Volume `wow-stats_ui-state` | Session plans, the AI analysis' findings, the AI logins, downloaded game tables | Yes, if you use the AI |
| Volume `wow-stats_site-data` | The static sites and the sealed site's key | Optional: the sites rebuild by themselves; keep it to keep the key (and the links you sent) |
| `deploy/compose/.env` and `deploy/compose/secrets/` | Your settings and secrets | Yes, somewhere safe: they're not in any volume |
| Volume `wow-stats_vm-data` | Battle.net data | **No.** Blizzard's terms keep it 30 days at most |

The volume names start with the compose project's name, `wow-stats`. `docker volume ls`
lists them.

On the computer you play on, the shipper's archive is a second copy of everything:
`~/WoWArchive` (Mac) or `%USERPROFILE%\WoWArchive` (Windows). Its `sent/` folder holds every
event already sent, and `wtf/` every version of your game files (unless you turned that off).
Copy it with the shipper's config, which holds your upload password: `~/.config/wow-stats/` or
`%APPDATA%\wow-stats\`.

## Back up the events

VictoriaLogs should be stopped while its files are copied, about a minute. From
`deploy/compose`:

Mac and Linux:

```sh
docker compose stop victorialogs
docker run --rm -v wow-stats_vl-data:/data:ro -v "$PWD":/backup alpine:3.22 \
  tar czf /backup/wow-stats-$(date +%F).tgz -C /data .
docker compose start victorialogs
```

Windows (PowerShell):

```powershell
docker compose stop victorialogs
docker run --rm -v wow-stats_vl-data:/data:ro -v "${PWD}:/backup" alpine:3.22 tar czf "/backup/wow-stats-$(Get-Date -Format yyyy-MM-dd).tgz" -C /data .
docker compose start victorialogs
```

The `.tgz` lands in `deploy/compose`. Move it somewhere else: a backup on the same disk
doesn't survive the disk.

The same three lines with `ui` instead of `victorialogs` and `wow-stats_ui-state` instead of
`wow-stats_vl-data` keep the plans and AI logins.

## Restore

Into an empty stack, from `deploy/compose`, with your backup file in that folder:

```sh
docker compose down
docker volume rm wow-stats_vl-data
docker compose up -d victorialogs
docker compose stop victorialogs
docker run --rm -v wow-stats_vl-data:/data -v "$PWD":/backup alpine:3.22 \
  tar xzf /backup/wow-stats-2026-11-19.tgz -C /data
docker compose up -d
```

Use your file's name in the `tar` line. Open the page: your characters are back.

No server backup, but the player's archive survived? The shipper can read every archived
version of the game files again (the `wtf/` folder: on by default when git is installed) and
send what it finds to an empty server, from the computer you play on:

```sh
uv run --script ~/.local/share/wow-stats/wow_stats.py --backfill
```

On Windows: `uv run --script "$env:LOCALAPPDATA\wow-stats\wow_stats.py" --backfill`. Every
event carries an id, so anything already on the server isn't stored twice.
