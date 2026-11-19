# The shipper

**For:** players who run their own server, and anyone debugging the shipper. **At the end:** you know when it runs, what it keeps on your computer, its files and flags.

`shipper/wow_stats.py` runs on the computer you play on. One file, Python 3.11+
standard library only, run by uv (`uv run --script`) or a plain `python3`
when uv isn't there.

## When it runs

- macOS: a launchd agent (`local.wow-stats`) every 5 minutes, and each time
  the game saves its SavedVariables (logout, `/reload`).
- Windows: a scheduled task (`wow-stats`) at logon and every 5 minutes,
  without a console window.

Each run waits until no file in `WTF` changed for 5 seconds (the game writes
its files one after another; it gives up after 2 minutes and tries next
run), then:

1. **archive**: copies `_forever_/WTF/` into a git repo (`~/WoWArchive/wtf`)
   and commits. Polestar Recorder deletes the chunks its uploader acked at the
   next login, so the git history is the only place every version survives.
   Skipped when git isn't installed or `wtf_archive = false` (below).
2. **extract**: turns what is new into NDJSON in `~/WoWArchive/spool/`:
   the SavedVariables, the combat logs, and Polestar Client's `sent.jsonl`
   when the client is installed (below). `state.json` remembers what was
   already sent, so nothing goes twice.
3. **ship**: POSTs each spool file to `[ship] url`, in posts of at most 4 MB,
   then moves it to `~/WoWArchive/sent/` gzipped. Server down or no network:
   it logs that and the spool waits for the next run. The spool file shrinks
   after each post, so a run cut short resends nothing.

## Stats only (no WTF archive)

By default, when git is installed, every run commits your whole `WTF`
folder to `~/WoWArchive/wtf`: every version of every SavedVariables file,
other addons' included. That history is what `--backfill` replays after an
update that reads something new, and the only copy of the Recorder chunks
an uploader already deleted. It grows with `WTF`: 8 MiB of unrelated
files in `WTF` once made about 16 MiB of archive.

To keep the stats only, put this at the top of the config, next to
`wow_dir`:

```toml
wtf_archive = false
```

The shipper then reads the game files where they are and commits nothing.
What stays either way, all under `archive_dir`:

| | What | Size |
|---|---|---|
| `spool/` | events not sent yet | empty once the server has them |
| `sent/` | every event sent, gzipped: your own copy, for a new server | grows with your play |
| `state.json` | what was already sent, so nothing goes twice | small |
| `wtf/_polestar-client/sent.jsonl` | the Polestar Client chunks kept beyond its 8 days (only with Polestar Client) | grows with your play |

Without the archive, `--backfill` has nothing to replay: events a past
run missed can only come back while the game files still hold them. An
archive already made stays where it is; delete `~/WoWArchive/wtf` by hand
if you don't want it (keep `_polestar-client/` inside it if you use
Polestar Client).

## The first run sends your whole history

There's no import step. The first run after installing finds nothing in
`state.json`, so it extracts everything already on disk: every character's
SavedVariables, the Recorder chunks still there, the last 8 days of chunks
Polestar Client keeps, and the combat logs in `Logs/`. That can be 100,000
events or more. The log then has one line per feed and per character
(`extract: ...: 23514 events; per feed {...}; per character {...}`) and, for
a big spool file, a progress line after each post
(`ship: ...: 40000/112000 events sent`). Until `[ship] url` is set, all of
it waits in the spool.

**Retention.** VictoriaLogs drops events older than its retention when they
arrive, and still answers 200. The compose kit keeps 100 years. Sending to a
store with a shorter one (the default is 7 days)? Set `retention_days` in
`[ship]`: older events are kept in `~/WoWArchive/too-old/` instead of being
posted into a store that drops them, and the log says how many.

## Polestar Client's sent.jsonl

Polestar Recorder deletes its chunks from the SavedVariables once Polestar
Client has uploaded them. The client keeps a copy of every chunk its server
accepted in the last 8 days in `sent.jsonl`, so the shipper reads that file
on every run, as a second source of the same chunks: it fills in what the
WTF archive missed (the play before you installed the shipper, a run that
didn't happen). A chunk gives the same events whichever file it came from
(same `source_id`, same durations and XP), and one already sent is never
sent again.
Each run also appends the chunks it hasn't kept yet to its own copy,
`~/WoWArchive/wtf/_polestar-client/sent.jsonl`, which the archive step commits
and never prunes: as long as the shipper runs once every 8 days, no chunk is lost.

| | Folder (Tauri's app data folder for `gg.polestar.client`) |
|---|---|
| macOS | `~/Library/Application Support/gg.polestar.client/` |
| Windows | `%APPDATA%\gg.polestar.client\` |
| Linux | `~/.local/share/gg.polestar.client/` |

`polestar_client_dir` in the config points it elsewhere. The shipper only
reads `sent.jsonl` there, never the client's tokens or settings, and never
writes to that folder. No Polestar Client, no file: nothing to read, not an
error.

Lines that aren't valid events never leave the PC: they go to
`~/WoWArchive/rejected/` and the log says how many.

## Files

| What | macOS | Windows |
|---|---|---|
| Script | `~/.local/share/wow-stats/wow_stats.py` | `%LOCALAPPDATA%\wow-stats\wow_stats.py` |
| Config | `~/.config/wow-stats/config.toml` | `%APPDATA%\wow-stats\config.toml` |
| Log | `~/Library/Logs/wow-stats.log` | `%LOCALAPPDATA%\wow-stats\wow-stats.log` |
| Archive | `~/WoWArchive` | `%USERPROFILE%\WoWArchive` |
| Polestar Client (read only) | `~/Library/Application Support/gg.polestar.client/sent.jsonl` | `%APPDATA%\gg.polestar.client\sent.jsonl` |

`WOW_STATS_CONFIG` points it at another config file. `config.example.toml`
lists every setting: `wow_dir` (the `_forever_` folder, found by itself in the
usual install places), `archive_dir`, `wtf_archive` (default `true`),
`log_file`, `polestar_client_dir`, and
`[ship]` with `url`, `user`, `password` or `password_file`, and
`retention_days`.

## Flags

- `--dry-run`: extract to stdout, write nothing, send nothing. The quickest
  way to see what it would send.
- `--no-ship`: archive and extract, keep the spool.
- `--backfill`: re-extract every archived version of WTF. Not needed after
  installing (the first run sends what is there); use it after an update
  that reads something new (quest names, Auctionator). Duplicates are
  harmless: every event has a `source_id`.
- `--preflight`: the installers' last step, a checklist with one line per
  check (the WoW folder, Polestar, Polestar Recorder and its switch, Questie
  or Altoholic, each other optional addon, saved game data, the server with
  your password, the background program): a check mark, or a cross with the
  one thing that fixes it. Exits 2 only when the install can't work as it is
  (no WoW folder, the password refused, no background program); a missing
  addon is a cross, never a failure. The addon list is
  `docs/videos/steps.json`'s, copied into `ADDONS` (a test keeps them equal).

## Addons it reads

Polestar and Polestar Recorder for nearly everything, Auctionator for prices,
Syndicator (equipped items), CharacterExport-Forever (profession caps),
Altoholic (its DataStore_Characters: `/played` since level 1; its
DataStore_Quests: the quests done before Polestar), Questie (the same
quests, exact),
GearQuestForever and ForeverLevelingBiS (tracked upgrades, spec) for the
gear panel, and LibForeverData, Polestar_QuestDB or Polestar_ItemDB only to
look up quest and item names and item levels.
Missing addons just mean fewer events.

## Something looks wrong

- **Nothing on the page.** Read the log: the last lines say what each step
  did (`archive: committed 165 changed files`, `extract: ...`,
  `ship: ... sent (5299 events)`). `unreachable` means the url or the server;
  `401` means the password doesn't match `INGEST_PASSWORD`.
- **Old days missing.** The server's VictoriaLogs drops events older than
  its retention when they arrive. The compose kit keeps 100 years; a
  VictoriaLogs you run yourself defaults to 7 days: set `retention_days` and
  look in `~/WoWArchive/too-old/`. Recorder chunks from before the shipper
  was installed exist only while Polestar Client keeps them, 8 days.
- **A new character doesn't show up.** It needs one logout first, so the game
  writes its SavedVariables. On macOS rerun the installer after that, so
  launchd watches the new folder too.
- **Quest names missing.** Install LibForeverData (or Polestar_QuestDB), then
  run with `--backfill`.

## Update and uninstall

Update: `git pull` and rerun the installer. Config, state and archive stay.

Backup, uninstall and deleting your data are three separate steps:
[Back up and restore](self-host/backup.md) and [Uninstall](self-host/uninstall.md). In
short: uninstalling (macOS `launchctl bootout gui/$(id -u)/local.wow-stats`
and the plist; Windows `schtasks /Delete /TN wow-stats /F`, not
`Unregister-ScheduledTask`: under a Restricted execution policy PowerShell 7
can't load its module) leaves `~/WoWArchive` in place until you delete it.
