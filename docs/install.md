# Install it for real

**For:** players who run their own server, on one computer or a small box at home. **At the
end:** the server runs, the addons and the shipper are on the computer you play on, and your
climb fills in by itself a few minutes after each logout.

From zero to your climb in a web page, on Windows or a Mac. Count an hour the first time,
most of it waiting for downloads. Tried [the demo](self-host/demo.md) already? Same server,
without the made-up characters.

> Every step below was run on a Mac (Apple silicon) from a clean home folder. On Windows the
> shipper's install is tested on every change; the Docker steps follow Docker's and
> Microsoft's own instructions but weren't run on a fresh Windows machine.

Three parts, in this order:

1. [Addons](#1-addons) in the game
2. [The server](#2-the-server): Docker, one `docker compose up -d`
3. [The shipper](#3-the-shipper): a small program on the computer you play on

Then [troubleshooting](#4-troubleshooting), and [what's next](#next).

Someone already runs wow-stats for you and sent you a `/start` link? Skip this guide:
[Join a friend's server](players/getting-started.md).

You need the kit itself on the computer that runs the server: `git clone` this repository, or
download the source zip of a release and unpack it anywhere. The commands below say which
folder to run them from: "the kit's folder" is the top of it, the one with `README.md` in it.

## 1. Addons

The kit reads what your addons already save. It never talks to the game.

**Polestar** is required; the shipper's installer adds **Polestar Recorder** (its pinned
release, checksum checked, never over a newer copy). The others are optional: each fills more
of the page, and a missing one just leaves its part empty. The list, with links and what each
one adds: [Addons](reference/addons.md). The easiest way to install them is the
[CurseForge app](reference/addons.md#with-the-curseforge-app-recommended).

**Turn the Recorder on.** Without Polestar Client (below), the Recorder
stays off until you type `/psrec on` once in game. It then records on this
computer only, into its SavedVariables, which is all the shipper needs.

Polestar Client, the uploader ([polestar.guide/client](https://polestar.guide/client)),
is optional. With it, the Recorder records while the client's sharing is on
(its default), the client uploads the chunks to Polestar's own service and
the Recorder then deletes them from the game file. The client keeps the last
8 days of them in `sent.jsonl`, and the shipper reads that file too, so
nothing is lost either way.

### Turn them on, per character

At the character select screen, click **AddOns** (bottom left). The list
has a drop-down at the top: pick the character you're about to play, or
"All characters". Tick the addons above, then **Okay**. Do it for each
character: a character whose addons are off writes nothing the kit can
read.

The game writes addon data (SavedVariables) only when you log out, exit or
type `/reload`. Nothing reaches the page while you're still logged in, so
after installing everything: log in once with each character, then log out
(or `/reload`).

## 2. The server

### Docker

- **Mac**: [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/).
  Open it once and wait for "Engine running".
- **Windows**: [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/),
  with the WSL 2 backend (the installer's default). The install needs an
  admin account and a reboot; after that a normal account is fine. Open it
  once and wait for "Engine running".

### Start it

Mac (Terminal), from the kit's folder:

```sh
cd deploy/compose
cp .env.example .env
chmod 600 .env
open -e .env
```

Windows (PowerShell), from the kit's folder:

```powershell
cd deploy\compose
Copy-Item .env.example .env
notepad .env
```

In `.env`, set `INGEST_PASSWORD` to a password of 12 characters or more:
the shipper uses it to send your events. Two optional lines while you're
there:

- `MODEL3D=off` and `WOW_ITEMS=off`, the **small preset**: no 3D view, and
  no item names, icons or tooltip cards fetched from wago.tools and
  Wowhead's file host. For a server with no internet access, or a small
  one. Both default to `on`.
- `VL_MAX_DISK=20GiB` (any size): a ceiling for your game events, see
  [Disk space](#disk-space). Empty (the default): no ceiling.
- `UI_MEM_LIMIT=2g`: more memory for `ui` than the default 1 GiB, for a
  long history, see [Memory](#memory).

Save, then (both systems, still in `deploy/compose`):

```sh
docker compose up -d --build
```

The first time it builds the wow-stats image from the kit (a minute or
two) and pulls VictoriaLogs. Then open <http://127.0.0.1:8080>: an empty
page until the shipper sends something.

Want to see a filled page first? Load the demo's five made-up characters
(they carry a DEMO DATA banner; `docker compose down -v` before your real
install wipes them):

```sh
docker compose exec -T ui python - --vl http://victorialogs:9428 < ../../dev/seed.py
```

```powershell
Get-Content ..\..\dev\seed.py -Raw | docker compose exec -T ui python - --vl http://victorialogs:9428
```

`Bind for 127.0.0.1:8080 failed: port is already allocated` means
something else on your computer already uses 8080. Pick another port in
`.env` (`UI_PUBLISH=127.0.0.1:18080`), run `docker compose up -d` again,
and use that port everywhere below, the shipper's `url` too. Same for 8081
with `SITE_PUBLISH`.

### The three ways in

| Address | What | Who |
|---|---|---|
| <http://127.0.0.1:8081/> | the public view: the character card (level, race, class, gear, professions, talents, the 3D character), time per level, dungeons cleared, and each past level's gear. No zones, journal, deaths, gold or other players | anyone you give the address to |
| `http://127.0.0.1:8081/zogzog/#<key>` | the public view plus zones, guide steps, the journal, loot, gear advice, prices and deaths as counts, as a sealed static copy; no other players, no killers, nothing about where the character is now | friends you give the whole link to |
| <http://127.0.0.1:8080/> | the live full view: the same plus the MCP endpoint, the Ask Claude and Ask ChatGPT links and the query box | you |

The two sites on 8081 are files rebuilt after each play session (every 10
minutes the `publish` service checks for new events, and at least once a
day). `/zogzog/` is sealed: every data file is encrypted, and the key is
the part of the link after `#`. Browsers never send that part to a server,
so the key never reaches the server, a log or a proxy. No key or a wrong
one shows "Nothing here."

Print the friends' link with `docker compose run --rm publish zogzog-key link`. Making a new
link, switching the sealed site off, the help page and pictures of past levels:
[Share your page](self-host/sharing.md).

Everything here listens on `127.0.0.1` only, so nobody else on your network reaches it.
Reaching the page by a name rather than an address (`nas.local`)? Add it to
`UI_ALLOWED_HOSTS` in `.env`, or the page answers 421: it won't answer for names it doesn't
know, so a web page can't read yours through your browser. A wider `UI_PUBLISH` needs a login
(`UI_AUTH=basic` and its user and password), or `ui` refuses to start. To reach it from your
phone or the internet, with HTTPS and a login:
[Put it behind a reverse proxy](self-host/reverse-proxy.md).

### Disk space

Your game events are kept 100 years (`-retentionPeriod=100y` in
`compose.yaml`), so nothing ages out by date. They compress well: 55 copies
of the demo's week, 100,705 events, took 6.5 MiB (VictoriaLogs v1.54.0,
the volume's size right after a flush; measured 2026-11-19). They live in
the Docker volume `wow-stats_vl-data`; `docker system df -v` shows its size.

To cap it, set `VL_MAX_DISK` in `.env` (`20GiB`, `500MiB`...) and run
`docker compose up -d`. It becomes VictoriaLogs'
`-retention.maxDiskSpaceUsageBytes`: once the volume passes it, VictoriaLogs
deletes whole days, **the oldest first**, until it fits again. Those events
are gone for good: back up first ([Back up and restore](self-host/backup.md)).
Empty or `0`, the default, means no ceiling.

The other volumes stay small: `wow-stats_site-data` (the friends' sites
and their key, rebuilt after each play session) and `wow-stats_ui-state`
(the plans, the play analysis' findings, the MCP logins, and the game
tables `ui` downloads for item names and the 3D view, in
`MODEL3D_CACHE_DIR=/data/model3d-cache`).

### Memory

`ui` is the one that grows with your history: it answers the page and
prepares the AI's analysis, which holds a character's whole history in
memory. Its limit is 1 GiB (`UI_MEM_LIMIT` in `.env`). On 55 copies of the
demo's week (100,705 events, items and 3D off) it peaked at 472 MiB while
the publisher built the sites, from 187 MiB on one week: 1 GiB leaves about
twice the measured peak. A much longer history than that: raise it
(`UI_MEM_LIMIT=2g`) and run `docker compose up -d`. In the same run the
publisher peaked at 342 MiB, VictoriaLogs at 217 MiB and `site` at 22 MiB;
the three have no limit.

### Battle.net data (optional)

The WoW Token price and your look from the armory, with
your own Battle.net API client: [Battle.net data](self-host/battlenet.md).

## 3. The shipper

On the computer you play on. It runs every 5 minutes (on a Mac also each
time the game saves), copies `WTF` into a history, turns what's new into
events and sends them to the server. It needs uv (Python comes with it)
and, for the history, git.

The history is a git repository of your whole `WTF` folder in
`~/WoWArchive/wtf`: every version of every SavedVariables file, which is
what `--backfill` replays. It grows with everything in `WTF`, other addons'
files included. Don't want it? Skip git, or set `wtf_archive = false` in
the config (below): the shipper then reads the game files directly and
keeps the stats only. [docs/shipper.md](shipper.md#stats-only-no-wtf-archive)
has what stays either way.

### Install

You're still in `deploy/compose` from part 2: go back to the kit's folder
first.

**Mac.** [uv](https://docs.astral.sh/uv/getting-started/installation/)
and git (`xcode-select --install` if `git --version` asks for it), then:

```sh
cd ../..
curl -LsSf https://astral.sh/uv/install.sh | sh
shipper/install-mac.sh
```

Then open a new Terminal window, so `uv` is on your `PATH` for the commands
below (the installer finds it either way).

It copies the script to `~/.local/share/wow-stats/`, makes
`~/.config/wow-stats/config.toml` from the example, and loads a launchd
agent (`local.wow-stats`). No admin password.

**Windows.** In PowerShell, uv, then [Git for Windows](https://git-scm.com/downloads/win)
(any defaults), then:

```powershell
cd ..\..
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
powershell -ExecutionPolicy Bypass -File .\shipper\install-windows.ps1
```

Open a new PowerShell window after installing uv, so it's on the `PATH`. The
installer copies the script to `%LOCALAPPDATA%\wow-stats\`, makes
`%APPDATA%\wow-stats\config.toml` from the example, and registers a
scheduled task `wow-stats` (at logon and every 5 minutes, no console
window). No admin rights.

### Fill in the config

Mac: `open -e ~/.config/wow-stats/config.toml`. Windows:
`notepad $env:APPDATA\wow-stats\config.toml`. Set:

```toml
[ship]
url = "http://127.0.0.1:8080/ingest"
user = "wow"
password = "the INGEST_PASSWORD from .env"
```

Your game in an unusual place? Uncomment `wow_dir` at the top and point it
at the `_forever_` folder (the one with `WTF` and `Logs` in it). The
installer looks in `/Applications/World of Warcraft/_forever_` and
`C:\Program Files (x86)\World of Warcraft\_forever_` (then without `(x86)`).
On a Mac, run `shipper/install-mac.sh` again after changing `wow_dir`, so
launchd watches the right folders.

Stats only, no history of your `WTF` folder: add `wtf_archive = false` at
the top of the same file, next to `wow_dir`.

### The first run sends your whole history

There's no import step. Within 5 minutes the first run reads everything
already on disk: every character's SavedVariables, the Recorder chunks
still there, the last 8 days of chunks in Polestar Client's `sent.jsonl`,
and the combat logs in `Logs/`. To start it now and watch it:

Mac:

```sh
uv run --script ~/.local/share/wow-stats/wow_stats.py
```

Windows:

```powershell
uv run --script "$env:LOCALAPPDATA\wow-stats\wow_stats.py"
```

It prints what each step did. A first run looks like this (one account,
two characters, levels 22 to 32 over a week, no combat log; lines
shortened, character names changed):

```
polestar client: kept 310 new chunks in ~/WoWArchive/wtf/_polestar-client/sent.jsonl
archive: committed 171 changed files
polestar client: 310 chunks (310 in ~/Library/Application Support/gg.polestar.client/sent.jsonl, 310 kept)
extract: 20261118T141342946827Z.ndjson: 23267 events; per feed {"wow-character": 3, "wow-dungeon": 2, "wow-gear": 1, "wow-level": 10, "wow-player": 92, "wow-prices": 1347, "wow-recorder": 21812}; per character {"Testalt-Demo": 1, "Testchar-Demo": 23266}
ship: 20261118T141342946827Z.ndjson: 14258/23267 events sent
ship: 20261118T141342946827Z.ndjson sent (23267 events)
```

That took about a second. Combat logs make it longer: a long history can
be 100,000 events and a minute or so. "per character" is your check: a
character with 1 or 2 events has only its WTF folder, no addon data yet
(see [A character is missing](#4-troubleshooting)). Reload
<http://127.0.0.1:8080> and your characters are there. The next runs only
send what's new.

### Scheduling

The installers did it. To check:

- **Mac**: `launchctl print gui/$(id -u)/local.wow-stats | grep -E 'state|last exit'`.
  It runs every 5 minutes and each time the game writes its SavedVariables
  (logout, `/reload`). A new character's folder joins the watch list when
  you run `shipper/install-mac.sh` again after its first logout.
- **Windows**: `Get-ScheduledTask -TaskName wow-stats | Get-ScheduledTaskInfo`
  shows the last and next run. Task Scheduler (the app) lists it under
  "Task Scheduler Library" as `wow-stats`.

Both installers are safe to run again (after updating the kit, for example).
Your config and archive stay.

## 4. Troubleshooting

**A character is missing.** Its addons are off, or it hasn't saved yet. At
character select, **AddOns**, pick that character, tick Polestar and
friends; log in, then `/reload` or log out. On a Mac run
`shipper/install-mac.sh` again so launchd watches its new folder.

**Battle.net says 404 for a character.** The profile API doesn't know new
or low-level characters yet. It shows up later by itself; until then the
3D view uses the Narcissus look or the race's default.

**Nothing arrives.** Read the shipper's log, the last lines say what each
step did:

- Mac: `tail -n 30 ~/Library/Logs/wow-stats.log`
- Windows: `Get-Content "$env:LOCALAPPDATA\wow-stats\wow-stats.log" -Tail 30`

`unreachable` is the `url` or a server that's down (`docker compose ps`);
`HTTP 401` is a password that doesn't match `INGEST_PASSWORD`; `no WTF
folder` is `wow_dir`. Unsent events wait in `WoWArchive/spool/` and go with
the next run that works.

**Events marked too old.** The log says `events older than the receiver's
retention (N days) kept in .../too-old, not sent`: `retention_days` is set
in the config's `[ship]`. It's for a store that drops old events; the kit's
server keeps 100 years, so delete that line, move the files back into the
spool and run the shipper (Mac: `mv ~/WoWArchive/too-old/* ~/WoWArchive/spool/`;
Windows: `Move-Item $env:USERPROFILE\WoWArchive\too-old\* $env:USERPROFILE\WoWArchive\spool\`).

**The look is wrong.** Battle.net's look comes first (the exporter,
refreshed once a day; the full view says which source it used). No
Battle.net look: visit a barber with Narcissus on and save the look there,
with CharacterExport-Forever on so it lands on the right character, then
log out.

**`/played` before Polestar is unknown.** The page counts played time from
Polestar's level splits, which start the day Polestar did: "played since
tracking began (level N)". Install Altoholic and log in once: its `/played`
turns that into "played since level 1".

## Next

- Your AI on your character: [Connect your AI](ai/connect.md). On this same computer, Claude
  Code needs one command and no login.
- Reach it from your phone, share it with friends, add HTTPS: [Run it on a server](self-host/production.md).
- Keep it: [Back up and restore](self-host/backup.md), [Upgrade](self-host/upgrade.md).
- Remove it: [Uninstall](self-host/uninstall.md).
