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
4. **3D files**: at most every 15 minutes, sends the game files the 3D view
   asked for ([The 3D view's files](#the-3d-views-files)).
5. **item facts**: when the game's hotfix cache changed, sends the facts of
   the items the server lacks ([Item facts](#item-facts)).
6. **update**: at most every 2 hours, asks the same server for a newer
   shipper and takes it ([Auto-update](#auto-update)).

## Auto-update

The shipper updates itself. After a run's own work, at most once every 2
hours, it asks the server it ships to for the current version:
`GET <server>/install/update/shipper`, with the same upload password as
`/ingest`. The answer is the version of the `wow_stats.py` that server
carries (its `VERSION` line), the sha256 of each file, and the release's
signature over both:

```json
{"version": "0.7.0",
 "files": {"wow_stats.py": {"sha256": "9f2c...", "size": 121344},
           "install-mac.sh": {"sha256": "51ab...", "size": 4310}},
 "signature": {"statement": "{\"files\":{...},\"version\":\"0.7.0\"}",
               "signature": "base64 Ed25519 signature"}}
```

Each release signs the statement (the version and every shipper file's
sha256) with the project's Ed25519 key, and the shipper pins the public half
(`UPDATE_KEY` in `wow_stats.py`). The server only passes the statement on, so
whoever takes over a server can't push code to your computer: the shipper
checks the signature first and takes only the files the statement names,
with their hashes. No signature, or one that doesn't check out: it keeps its
version, logs why, and the owner's overview shows "auto-update refused" on
your row. The update line on your start page still works by hand.

When that version is higher than its own and the signature checks out, it
downloads `/install/update/shipper/wow_stats.py` from the same server, checks the
signed sha256 and that the file compiles and names the version announced, writes
it next to itself and renames it into place in one step. The old copy
stays as `wow_stats.py.prev`. The new version runs from the next run on.
When the server's installer for your system (`install-mac.sh` and
`wow-stats.plist`, or `install-windows.ps1`) differs from the copy the
installer left next to the script, the shipper also runs the new installer
once, in the background, to rewrite the launchd agent or the scheduled task.

What it never does:

- **Downgrade.** An equal or lower version is left alone.
- **Talk to anyone else.** Only the server in `[ship] url`, over https (plain
  http only to this computer, `127.0.0.1` or `localhost`), and no redirect.
- **Take an unsigned update.** A server built from a tree between releases
  (or your own fork's, see [Signing your own build](self-host/upgrade.md#signing-your-own-build))
  serves no signature the shipper accepts: it stays on its version.
- **Break the shipping.** No endpoint (a server older than 0.5.1), no network,
  no signature, a wrong hash, a file that doesn't compile: it keeps the running
  version and logs one line (`auto-update: ...; keeping 0.7.0`). An addon
  file it can't read costs that file's events for the run, never the
  shipping or the update check.
- **Touch your config or `state.json`.** They stay where they are; a version
  that changes their format reads the old one.
- **Rewrite a copy you run by hand.** Only the installed script
  (`~/.local/share/wow-stats/wow_stats.py`, `%LOCALAPPDATA%\wow-stats\wow_stats.py`)
  updates itself, not a git checkout.

To pin the version you have, put this at the top of the config, next to
`wow_dir` (your start page then gives the update command once the server
has had a newer version for 7 hours):

```toml
auto_update = false
```

A shipper from before 0.5.1 can't update itself: run the update line once
(below). Shippers from 0.5.1 to 0.6.x check no signature: they take the first
signed release (0.7.0) the old way, on the sha256 alone, and check every
update after it. `/start` says so while the server still sees one: its setup report
carries the shipper's version from 0.5.1 on.

## The 3D view's files

The 3D view draws your characters from the game's own files, with its own
viewer: nothing comes from anyone else. Your shipper reads them from your WoW install (read-only: it opens
the game's storage under `Data/` and never writes there) and sends them to
your own server, the one in `[ship]`, with the same upload password as
`/ingest`:

- it asks `GET <server>/install/assets/need` what the server still wants:
  about 30 client tables (how races are built, how items dress them, some
  11 MB once per game patch), then the models and textures your characters
  wear (weapon and enchant glows included), then what those name (skin,
  skeleton, and the animations the 3D view plays: standing, closed hands,
  the attack, cast, channel and roar, and six emotes, about 6 MB for a
  gnome);
- it sends them one by one, at most 96 MB a run, within the same daily
  budget as your events. A file the server already has is never sent again.
  One file can be 48 MB at most (the server's limit, given in its answer to
  `need`; Forever's HD character models are 17 to 28 MB): a bigger one is
  skipped and the log says how many were.

Only what your game has on disk can go: the launcher downloads some files
only when the game needs them, so a look you never saw in game (the Classic
models while you play with the updated ones) may lack pieces. The view draws
without them and lists what's missing. A table's encrypted rows (content not
out yet) go blank.

Nothing goes anywhere but your server. With several people, your files stay
in your own folder there and go with your account. `model3d_files = false`
in the config turns it off; the paperdoll then keeps its silhouette and
the 3D view says why.

## Item facts

Forever adds items the item reference doesn't have yet (a new dungeon's
loot). Your game already knows them: it keeps the rows the server sent it
in its hotfix cache, `_forever_/Cache/ADB/<locale>/DBCache.bin`. The
shipper reads that one file (read-only, never more than 64 MB) and:

- sends `POST <server>/install/items/need` the list of weapons and armour in
  it (id and a checksum of each, about 6,500 items, no names);
- sends the facts of the items the server says it lacks, 500 a post: name,
  slot, armour or weapon type, quality, item level, required level, stat
  allocations, speed, icon id, sell price;
- once per game patch, the game's item-level budget table (`RandPropPoints`,
  36 kB), which turns the allocations into the tooltip's numbers.

It reads the file again only when it changed, and asks once a day anyway.
An item the reference has is never sent. A cache it can't read (a new
format, a damaged file) sends nothing and says why in the log. Same upload
password and daily budget as `/ingest`; with several people, your facts
stay in your own folder there and go with your account.
`item_facts = false` in the config turns it off.

## Stats only (no WTF archive)

By default, when git is installed, every run commits your whole `WTF`
folder to `~/WoWArchive/wtf`: every version of every SavedVariables file,
other addons' included. That history is what `--backfill` reads again after
an update that reads something new, what `--reextract` rebuilds a server
from, and the only copy of the Recorder chunks an uploader already deleted.
Each commit also carries `_capture.json`, the files' times as the shipper
saw them, since git keeps none ([the contract's "Times"](contract.md#times)). It grows with `WTF`: 8 MiB of unrelated
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
| `state.json` | what was already sent, so nothing goes twice; `state.json.prev` is the one before, used when a power cut leaves the new one unreadable | small |
| `wtf/_polestar-client/sent.jsonl` | the Polestar Client chunks kept beyond its 8 days (only with Polestar Client) | grows with your play |

Without the archive, `--backfill` and `--reextract` have nothing to read:
events a past run missed can only come back while the game files still
hold them. `--replay` still works: it only needs `sent/`. An
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
`log_file`, `polestar_client_dir`, `auto_update` (default `true`), and
`[ship]` with `url`, `user`, `password` or `password_file`, and
`retention_days`.

## Flags

- `--dry-run`: extract to stdout, write nothing, send nothing. The quickest
  way to see what it would send.
- `--no-ship`: archive and extract, keep the spool.
- `--backfill`: re-extract every archived version of WTF, for what this PC
  hasn't sent yet. Not needed after installing (the first run sends what is
  there); use it after an update that reads something new (quest names,
  Auctionator). Duplicates are harmless: every event has a `source_id`. It
  is not a restore: on a PC that sent everything, it finds next to nothing.
- `--replay`: send every event in `sent/` again, as it went the first time,
  then stop. For a new or emptied server
  ([Back up and restore](self-host/backup.md#restore)). Writes nothing on the PC
  and doesn't need the WoW folder. A damaged file in `sent/` still sends what
  reads whole; the log names it and the run exits 1.
- `--reextract`: read every archived version again from scratch, with a
  record of its own, and send it all. For a server to refill when `sent/` is
  gone too. `state.json` stays byte for byte as it was.
- `--prefill`: sends the [3D view's files](#the-3d-views-files) and the [item facts](#item-facts)
  the server still lacks, now (the background runs wait up to a day), prints how many
  went and exits. Events stay with the background runs.
- `--preflight`: the installers' last step, a checklist with one line per
  check (the WoW folder, Polestar, Polestar Recorder and its switch, Questie
  or Altoholic, each other optional addon, saved game data, the server with
  your password, the background program): a check mark, or a cross with the
  one thing that fixes it. Exits 2 only when the install can't work as it is
  (no WoW folder, the password refused, no background program); a missing
  addon is a cross, never a failure. The addon list is
  `docs/videos/steps.json`'s, copied into `ADDONS` (a test keeps them equal).
- `--talents-check`: what the shipper reads from WoWSims Exporter's file,
  per account, as counts only (entries, readable saves, saves with the
  character's full name, saves matched to one character, characters on the
  account): no name, no build. Read-only, sends nothing.

- `--casc-extract FOLDER --fdids <ids> --tables <names>`: copies the
  game files the 3D view would ask for (file ids, client tables such as
  `ChrModel`) from your WoW install to FOLDER, one file per id plus
  `manifest.json` (what was missing and why). Read-only on the game folder;
  it also takes what each model names (its first skin, skeleton, parent
  skeleton and the animations the 3D view plays). `@file` reads either
  list from a file; `--wow-root` overrides the folder holding `.build.info` (default: the one
  above your `_forever_`). For checking by hand: nothing is sent.
- `--casc-diagnose`: prints how the WoW install's storage is laid out
  (each index's versions and header, the archives' sizes, where the
  encoding table is and whether it reads), never a file's contents. For a
  report when `--casc-extract` fails.

## Addons it reads

Polestar and Polestar Recorder for nearly everything, Auctionator for prices,
Syndicator (equipped items; currency 3473 "Renown - PvP Rank" only, by id: the PvP
rank), Altoholic (its DataStore_Characters: `/played` since level 1; its
DataStore_Quests: the quests done before Polestar; its DataStore_Currencies: currency
3473 only, the PvP rank), Questie (the same
quests, exact), WoWSims Exporter (`WowSimsExporter.lua`, its
`profiles.*.savedCharacters` only: each character's talents, one
`wow-talents` event per change; never its `global` table, which holds
other players),
GearQuestForever and ForeverLevelingBiS (tracked upgrades, spec) for the
gear panel, ForeverDB (optional: its armory snapshot's PvP part only, the
same rank with the honorable kills; the newest of the three saves wins),
ForeverDB_Helper (optional: its journal's groupmate entries only, who you
grouped with, in which zone or dungeon, their level, class, race and role;
never their realm or guild, nor the rest of the journal), and LibForeverData, Polestar_QuestDB or Polestar_ItemDB only to
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

Update: it updates itself (above). By hand, or once for a shipper from
before 0.5.1: `git pull` and rerun the installer, or the update line from
your start page. Config, state and archive stay.

Backup, uninstall and deleting your data are three separate steps:
[Back up and restore](self-host/backup.md) and [Uninstall](self-host/uninstall.md). In
short: uninstalling (macOS `launchctl bootout gui/$(id -u)/local.wow-stats`
and the plist; Windows `schtasks /Delete /TN wow-stats /F`, not
`Unregister-ScheduledTask`: under a Restricted execution policy PowerShell 7
can't load its module) leaves `~/WoWArchive` in place until you delete it.
