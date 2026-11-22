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

With friends on the install ([Add friends](friends.md)), add three more: the friends'
VictoriaLogs (their events, same steps as yours), the state folder (`WOW_STATE_DIR`: who is
who, settings, upload passwords) and the master key (`ZOGZOG_MASTER_KEY_FILE`: without it every
sealed link changes). Signing your own shipper builds? Keep that `.pem` too: a new key means
every player runs the update line once ([Upgrade](upgrade.md#signing-your-own-build)).

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

No server backup, but the player's archive survived? Refill the empty server from the computer
you play on. Run it once per player, each on their own computer.

```sh
uv run --script ~/.local/share/wow-stats/wow_stats.py --replay
```

On Windows: `uv run --script "$env:LOCALAPPDATA\wow-stats\wow_stats.py" --replay`.

`--replay` sends every event in `sent/` again, byte for byte: same ids, same fields, same
times. It reads nothing else, so the game doesn't need to be installed yet. It changes nothing
on the PC (not `sent/`, not `state.json`), so you can run it again when it stops halfway: the
log says how far it got. What the server already has is stored a second time but counted
once: every answer keeps one copy per event id.

The log's last line says how much went: `replay: 12000 events from 9 files in ... sent
again`. The page lists your characters within a minute (it keeps the character list for 45
seconds).

`sent/` gone too, but `wtf/` is there? Use `--reextract` instead:

```sh
uv run --script ~/.local/share/wow-stats/wow_stats.py --reextract
```

It reads every archived version of the game files again from scratch and sends what it finds,
with a record of its own. It never reads or writes `state.json`, so the next background run
carries on where it stopped (and sends the 3D files and item facts, which `--reextract` leaves
to it). Never delete `state.json` for a restore.

It writes the events as today's shipper does: quest, NPC and item names come from the data
addons installed now, and a snapshot an older version sent under another id comes back under
today's.
Two things it can't bring back: combat logs (`Logs/` isn't archived; `sent/` has them) and the
exact time of a snapshot archived before 0.7.0 (it gets the archive's time instead, marked as
approximate: [the contract's "Times"](../contract.md#times)).

Don't use `--backfill` for this. It looks for what the PC hasn't sent yet, and after a server
loss the PC has sent everything already, so it finds next to nothing.
