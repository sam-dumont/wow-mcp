# Something's wrong

**For:** players whose page is empty, stale or missing a part. **At the end:** you've found
your symptom and its fix, or you know what to send the person who runs your server.

Start with the symptom that matches. Each fix is one thing to try; most problems are an addon
switched off for one character, or the game not having saved yet. WoW writes the addons'
files only when you `/reload`, log out or switch character, and the program that sends them
runs every 5 minutes. So "nothing yet" right after playing is normal: give it 5 minutes after
a logout.

<!-- generated:player-trouble -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

### My page is still empty after 10 minutes

WoW saves the addons' files only on `/reload` or logout, and the program that sends your data runs every 5 minutes.

**Fix:** Type `/psrec on` in WoW (the Recorder starts off unless the Polestar Client installed it), then `/reload` and wait 5 minutes. Still nothing: read the last lines of the log (Mac `~/Library/Logs/wow-stats.log`, Windows `%LOCALAPPDATA%\wow-stats\wow-stats.log`). "HTTP 401": the password is stale, run a new install command from your start page. "unreachable": check your connection; nothing is lost, it all goes once you are back online.

### My last session is missing

WoW addons write their files only at logout, `/reload` or a clean quit. A crash of the game or the computer, or Alt+F4, loses everything since the last save, for every addon (Polestar, Auctionator...), not just this one. An addon can't save on a timer: a reload needs a key press.

**Fix:** Type `/reload` after a dungeon, after an Auction House scan and before a break, or put `/reload` on a macro key. What the game never wrote can't be recovered.

### "WoW folder not found" (or the wrong folder)

The installer looks for a `_forever_` folder with `WTF/` in it where Battle.net puts WoW: `C:\Program Files (x86)\World of Warcraft\_forever_` on Windows (or the World of Warcraft folder Battle.net registered, on any drive), `/Applications/World of Warcraft/_forever_` on a Mac. `WTF/` only exists after one login.

**Fix:** Log in to WoW Forever once, quit, then run a new install command. Not sure where WoW is: in the Battle.net app, pick World of Warcraft, click the cog next to Play, then Show in Explorer (Show in Finder on a Mac); the `_forever_` folder is in the World of Warcraft folder. WoW installed somewhere else: on a Mac, set the folder in the command, `curl -fsSL <your command's address> | WOW_STATS_WOW_DIR="/path/to/_forever_" bash`; on Windows, first run `$env:WOW_STATS_WOW_DIR = 'D:\Games\World of Warcraft\_forever_'` in the same PowerShell window, then the command.

### An antivirus blocked the installer

Some antivirus tools block `irm | iex` and unknown scheduled tasks. The installer writes only under your user folder and to WoW's AddOns folder, and adds one task that runs every 5 minutes.

**Fix:** Allow the script once from the antivirus's quarantine or history, then run a new install command. Can't allow it: send the owner a screenshot of the warning.

### Nothing arrives since a reboot

On Windows the task starts when you log in to Windows, not at boot. On a Mac the agent loads when you log in to macOS.

**Fix:** Log in and wait 5 minutes. Still nothing: Windows, Task Scheduler, task wow-stats shows its last run and result; Mac, `launchctl print gui/$(id -u)/local.wow-stats` says whether it's loaded. Either way, a new install command from your start page replaces the job and keeps your archive.

<!-- /generated:player-trouble -->

## A part of my page is empty

Each part of the page comes from an addon. The start page shows a "Missing:" line under an
empty part; this table is the same list.

<!-- generated:player-missing -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

| Missing from your page | Comes from | Fix |
|---|---|---|
| level times, dungeon runs and gear advice | Polestar | Tick Polestar in AddOns at character select, play to your next level, then log out. |
| play sessions, zones, deaths and loot | Polestar Recorder | Type `/psrec on` once in game, then `/reload`. |
| your gear and bags | Syndicator | Tick Syndicator in AddOns at character select, log in with this character, then log out. |
| quests you did before installing | Questie or Altoholic | Log in once with it ticked in AddOns, then log out. |
| auction prices | Auctionator | Scan the auction house with Auctionator, then log out. |

<!-- /generated:player-missing -->

The Recorder is off when your page has levels and gear but no sessions, zones or deaths. Type
`/psrec on` once in game, then `/reload`. The Polestar Client turns it on by itself if you use
it.

## Check that the program sends your data

<!-- generated:player-status -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

[Watch: reading the log and running it by hand (Mac)](../videos/media/status-mac-light.mp4)

Read the end of its log: each run writes what it did. A good run ends in a line like "ship: ... sent (120 events)"; a run with nothing new to send writes almost nothing.

Don't want to wait 5 minutes? Run it now with the second line: it prints nothing and writes the log.

<div class="os" data-os="windows">

**Windows (PowerShell).** The log, then a run by hand. The third line shows the task's last and next run.

```powershell
Get-Content "$env:LOCALAPPDATA\wow-stats\wow-stats.log" -Tail 20
uv run --script "$env:LOCALAPPDATA\wow-stats\wow_stats.py"
Get-ScheduledTask -TaskName wow-stats | Get-ScheduledTaskInfo
```

</div>

<div class="os" data-os="mac">

**Mac (Terminal).** The log, then a run by hand. The third line says whether the background job is loaded ("state = running" or "not running" between runs are both fine).

```sh
tail -n 20 ~/Library/Logs/wow-stats.log
uv run --script ~/.local/share/wow-stats/wow_stats.py
launchctl print gui/$(id -u)/local.wow-stats | grep -E 'state|last exit'
```

</div>

<!-- /generated:player-status -->

## Problems with your AI

Connecting Claude, ChatGPT or another assistant: [Connect your AI](../ai/connect.md#when-it-doesnt-work).

## Still stuck

Send the person who runs your server the last 20 lines of the log (the commands above), and
what you expected to see. The log never holds your password.
