# The manual's videos

The friends' manual is the help page (`/help/`, built by `wow-stats publish` from
`docs/videos/steps.json`) and the start page (`/start`). Its clips live in `docs/videos/media`,
one per theme (`<name>-dark.mp4`, `<name>-light.mp4`), and ship inside the app. This folder makes
them, on a throwaway two-person lab on this machine: the real app, a real VictoriaLogs, a fake WoW
folder, invented characters.

```bash
VL_BIN=/path/to/victoria-logs-prod demo/record.sh   # footage -> demo/video/public
npm --prefix demo/video ci
npm --prefix demo/video run render -- --publish     # videos -> demo/video/out, then docs/videos/media
```

`MODES=dark` records one theme; `VIDEOS=tour` renders one video. Needs uv,
[VHS](https://github.com/charmbracelet/vhs), ffmpeg, the Inconsolata font, Node 22 and a
VictoriaLogs v1.54.0 binary (`victoria-logs-prod` from the release tarball). Run it on an idle
machine: VHS drops frames when the CPU is busy, and a render takes all of it.

Nothing leaves the machine but the item tables (wago.tools, for names and icons). The lab binds
127.0.0.1, its keys are made for the run and deleted with it. launchd is left alone: `launchctl` is a
no-op shim first on the takes' PATH, and the takes' shell exits if it resolves to anything else
(the uninstall line would otherwise stop this Mac's own shipper). The installers run as they do for
a friend, so their checklist shows the background program as a friend sees it. `demo/lab friend`
installs with `WOW_STATS_NO_LOAD=1` instead. The shipper only ever reads the fake WoW folder: record.sh
stops if the take's config names any other (without a config it reads the real game folder).

| Video | What it shows | Where |
|---|---|---|
| `onboarding` | start page, install, the first evening arrives, your page | `/start`, help "What you get" |
| `tour` | Overview, Character, upgrades, dungeons, a plan, Timeline, Journal | help "Overview" |
| `private` | the zogzog link, opened as a friend would | help "Your zogzog link" |
| `settings` | hide a character, public page on, reset the upload password | help "Settings" |
| `install-mac` | the one line, in Terminal | `/start` step 3, help "Install" |
| `status-mac` | the end of the shipper's log | help "Is the shipper running?" |
| `update-mac` | the update line | help "Updating" |
| `uninstall-mac` | the uninstall line; the archive stays | help "Uninstalling" |

Windows has no tape: VHS has no PowerShell host on a Mac, and the installer only runs on Windows.
The help page shows the Windows commands as text next to the Mac clip.

| File | What it does |
|---|---|
| `lab` | the lab by hand: `up`, `friend dark\|light`, `publish`, `down` |
| `record.sh` | the takes, in a friend's order: wizard + install, the shipper, tour, zogzog, update, settings, uninstall |
| `stack/up.sh`, `env.sh` | the lab (ports, keys, the app, VictoriaLogs, the login stand-in, the static sites) |
| `stack/friend.sh` | a friend installs in a throwaway HOME (their name comes from the friends list, as the owner sets it) |
| `stack/fake_wow.py` | a WoW Forever folder with a few minutes of play, for the shipper |
| `stack/seed_friend.py` | the decoy character's evenings (`tools/fixtures/generate.py --demo`, Nibbet-Gearholt), sent to `/ingest` |
| `stack/idproxy.py` | the login proxy's stand-in: one port per identity, sets `X-Forwarded-User`/`-Org` |
| `browser/*.py` | the browser takes (Chromium, 1280x720), each with its marks (the second each beat starts) |
| `tapes/*.tape` | the terminal takes; `settings.tape.in` is filled per theme |
| `video/` | the Remotion compositions: titles, cuts at the marks, the caption band |
