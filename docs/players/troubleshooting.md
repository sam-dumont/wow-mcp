# Something's wrong

**For:** players whose page is empty, stale or missing a part. **At the end:** you've found
your symptom and its fix, or you know what to send the owner of your server.

Find your symptom. Most problems are an addon switched off for one character, or the game
not having saved yet: WoW writes the addons' files only on `/reload` or logout, and the program
sends them within 5 minutes. "Nothing yet" right after playing is normal.

<!-- generated:player-trouble -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

### My page is still empty after 10 minutes

<details>
<summary>Why, and the fix</summary>

WoW saves the addons' files only on `/reload` or logout, and the program that sends your data runs every 5 minutes.

**Fix:** Type `/psrec on` in WoW (the Recorder starts off unless the Polestar Client installed it), then `/reload` and wait 5 minutes. Still nothing: read the last lines of the log (Mac `~/Library/Logs/wow-stats.log`, Windows `%LOCALAPPDATA%\wow-stats\wow-stats.log`). "HTTP 401": the password is stale, run a new install command from your start page. "unreachable": check your connection; nothing is lost, it all goes once you are back online.

</details>

### My last session is missing

<details>
<summary>Why, and the fix</summary>

WoW addons write their files only at logout, `/reload` or a clean quit. A crash, or Alt+F4, loses everything every addon gathered since the last save. No addon can save on a timer.

**Fix:** Type `/reload` after a dungeon, after an Auction House scan and before a break, or put `/reload` on a macro key. What the game never wrote can't be recovered.

</details>

### "WoW folder not found" (or the wrong folder)

<details>
<summary>Why, and the fix</summary>

The installer looks for a `_forever_` folder with `WTF/` in it where Battle.net puts WoW: `C:\Program Files (x86)\World of Warcraft\_forever_` on Windows (or the World of Warcraft folder Battle.net registered, on any drive), `/Applications/World of Warcraft/_forever_` on a Mac. `WTF/` only exists after one login.

**Fix:** Log in to WoW Forever once, quit, then run a new install command. Not sure where WoW is: in the Battle.net app, pick World of Warcraft, click the cog next to Play, then Show in Explorer (Show in Finder on a Mac); the `_forever_` folder is in the World of Warcraft folder. WoW installed somewhere else: on a Mac, put `WOW_STATS_WOW_DIR="/path/to/_forever_"` before `bash` at the end of your command (`... | WOW_STATS_WOW_DIR="/path/to/_forever_" bash`); on Windows, first run `$env:WOW_STATS_WOW_DIR = 'D:\Games\World of Warcraft\_forever_'` in the same PowerShell window, then the command.

</details>

### An antivirus blocked the installer

<details>
<summary>Why, and the fix</summary>

Some antivirus tools block `irm | iex` and unknown scheduled tasks. The installer writes only under your user folder and to WoW's AddOns folder, and adds one task that runs every 5 minutes.

**Fix:** Allow the script once from the antivirus's quarantine or history, then run a new install command. Can't allow it: send the owner a screenshot of the warning.

</details>

### Nothing arrives since a reboot

<details>
<summary>Why, and the fix</summary>

On Windows the task starts when you log in to Windows, not at boot. On a Mac the agent loads when you log in to macOS.

**Fix:** Log in and wait 5 minutes. Still nothing: Windows, Task Scheduler, task wow-stats shows its last run and result; Mac, `launchctl print gui/$(id -u)/local.wow-stats` says whether it's loaded. Either way, a new install command from your start page replaces the job and keeps your archive.

</details>

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

Gear and look but no bags: that's Battle.net's copy, read every 2 hours. Syndicator gives
the page your own gear and bags.

## Check that the program sends your data

<details>
<summary>Read its log, or run it now</summary>

<!-- generated:player-status -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

[Watch: reading the log and running it by hand (Mac)](../videos/media/status-mac-light.mp4)

Read the end of its log: each run writes what it did. A good run ends in a line like "ship: ... sent (120 events)"; a run with nothing new to send writes almost nothing.

To skip the 5-minute wait, run it now with the second line: it prints nothing and writes the log.

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

</details>

## Problems with your AI

Connecting Claude, ChatGPT or another assistant: [Connect your AI](../ai/connect.md#when-it-doesnt-work).

## Still stuck

Send the owner the last 20 lines of the log (the commands above), and
what you expected to see. The log never holds your password.
