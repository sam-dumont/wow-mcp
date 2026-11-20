# Join a friend's server

**For:** WoW Forever players a friend invited to their wow-stats server. **At the end:** your
characters are on your own page, and it updates by itself a few minutes after you log out.

Someone runs wow-stats for you and your friends and sent you a link. You need no code from
this repository and no server: a few addons, one command on the computer you play on, and
you play. About 5 minutes the first time.

The same steps live on your start page, `https://<their server>/start`, with your personal
install command in them and a tick when each step works. Use that page if you can. This page
is the same guide, for reading before you start or when the start page isn't at hand.

## What you need

<!-- generated:player-needs -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

- WoW Forever on Windows 10 or 11, or macOS, logged in once (so its WTF folder exists).
- Your wow-stats account: the owner sends you the link.
- No admin rights: everything installs for your user only.

<!-- /generated:player-needs -->

## The steps

The examples use `https://wow.example.org` for your friend's server: use the address they
sent you.

<!-- generated:player-steps -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

[Watch: the whole setup, from the start page](../videos/media/onboarding-light.mp4)

### 1. Log in

*Log in with the account you were sent.*

Your start page, https://wow.example.org/start, is the personal version of this page: your install command is there, and each step ticks when it works. Log in with the username and password you were sent. Your page's address is already set: the owner chose it when they made your account.

**It worked when:** Your start page opens with your install command ready.

**If not:** "Access denied" or a login loop: the account has no wow-stats access yet. Send the owner the email you log in with.

### 2. Install the addons

*Polestar is required. The install command (step 3) adds Polestar Recorder.*

Easiest: the CurseForge app (https://www.curseforge.com/download/app) lists WoW Forever as its own WoW install; restart the app if Forever doesn't show up.

**It worked when:** Character select, AddOns: Polestar and Polestar Recorder are ticked. In game, `/psrec on` was typed once (not needed with the Polestar Client). Once data arrives, each addon turns green on your start page.

**If not:** Missing from the AddOns list: the folder is one level too deep (`Interface/AddOns/Polestar/Polestar/...`). Move it up so `Interface/AddOns/Polestar/Polestar.toc` exists, then restart WoW.

### 3. Run the install command

*One line in a terminal. No admin rights.*

[Watch: run the install command (Mac)](../videos/media/install-mac-light.mp4)

The command installs a small program that runs every 5 minutes, reads what WoW and your addons saved in your `WTF/` folder and sends what's new to your page. It also puts Polestar Recorder in WoW's AddOns folder (the official release, checked before it's copied), unless you have a newer one. It finds the WoW folder by itself, writes only under your user folder and to that AddOns folder, and never prints your password.

<div class="os" data-os="windows">

**Windows (PowerShell).** Windows key, type PowerShell, Enter. Paste with a right click, press Enter.

Your command is on your start page. It looks like this (the code is yours, single use):

```powershell
irm https://wow.example.org/install/<your code> | iex
```

It installs: `%LOCALAPPDATA%\wow-stats` and a scheduled task "wow-stats" (every 5 minutes, as you, no admin). Log: `%LOCALAPPDATA%\wow-stats\wow-stats.log`. Polestar Recorder in `_forever_\Interface\AddOns`.

</div>

<div class="os" data-os="mac">

**Mac (Terminal).** Cmd+Space, type Terminal, Enter. Paste with Cmd+V, press Enter.

Your command is on your start page. It looks like this (the code is yours, single use):

```sh
curl --proto '=https' --proto-redir '=https' -fsSL https://wow.example.org/install/<your code> | bash
```

It installs: `~/.local/share/wow-stats`, `~/.config/wow-stats/config.toml` and a launchd agent local.wow-stats (every 5 minutes). Log: `~/Library/Logs/wow-stats.log`. Polestar Recorder in `_forever_/Interface/AddOns`.

</div>

The command works once, for 15 minutes, and is only for the first install. Used or expired before you ran it: click "Get my install command" again. Updates later need no code: once installed, the update line shows in this step.

**It worked when:** The output says "wow-stats is installed. WoW folder: ..." and that folder is your `_forever_` folder.

**If not:** "WoW folder not found" or the wrong folder: see that troubleshooting entry. "This install code expired or was used already": get a new command and run it.

### 4. Play 5 minutes

*Play, then `/reload` or log out.*

Play a few minutes with the addons on. WoW writes the addons' files only on `/reload`, logout or character switch: do one, then wait up to 5 minutes.

The first run sends everything your addons saved so far, every character: a few seconds, a minute with a long combat log. After that, only what's new.

A game or computer crash, or Alt+F4, loses what every addon gathered since the last save. Type `/reload` after a dungeon, after an Auction House scan and before a break.

**It worked when:** "Data arrived" ticks on your start page by itself, and the program's log ends in a line like "ship: ... sent (N events)".

**If not:** Nothing after 10 minutes: first type `/psrec on` in WoW (the Recorder starts off without the Polestar Client), then `/reload`. Still nothing: see "My page is still empty after 10 minutes".

### 5. See your character

*Your character, your climb and your gear.*

Open https://wow.example.org: each character you played with the addons on is in the list at the top, with level, gear and time per level. Settings decides what's public; the public page is off until you switch it on.

**It worked when:** Your character is in the list at the top of your page.

**If not:** Data arrived but no character: Polestar Recorder is off for that character. Tick it in AddOns at character select, `/reload`, wait 5 minutes.

### 6. Connect your AI

*Add wow-stats as a connector once.*

Your assistant reads your stats and plans the next session: what to do in 2 hours, which gear to chase, when a dungeon is worth it. It uses your own AI account; wow-stats never sees the conversation.

**It worked when:** Ask "With wow-stats, what's my character doing?": the answer names your character.

**If not:** "Client Not Registered" or a disconnected connector: see that troubleshooting entry.

<!-- /generated:player-steps -->

## What the program on your computer does

<!-- generated:player-shipper -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

A small Python program that runs in the background on the computer you play on, every 5 minutes. It never touches the game: it reads the files WoW writes on `/reload` or logout.

Each run turns what's new in your WTF folder into events and posts them to your page with your own upload password; a copy of what it sent stays in WoWArchive in your home folder. With Git installed (Macs often have it), it also keeps every version of the WTF folder there. Nothing is sent twice; offline, it waits for the next run.

The first run sends your whole history: everything your addons saved so far.

It sends:

- your addons' saved files (SavedVariables), every character
- WoW's combat logs, when there are any

It never sends:

- your WoW or Battle.net password
- anything outside the WoW folder

<!-- /generated:player-shipper -->

What your friend's server shows to whom: [What's shared, and with whom](privacy.md).

## Update

<!-- generated:player-update -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

- Addons: the CurseForge app's Update all; by hand, delete the old folders, then unzip the new ones.
- The program on your computer updates itself, within a few hours of a new version on the server. Installed before version 0.5.1? Run the update line for your OS once (your start page says so when it's needed); from then on it updates by itself. No code, no login: it reads your install's config and keeps it, upload password included. It updates Polestar Recorder too, unless yours is newer.
- Moving to another computer, or the password leaked: Reset my upload password on your start page. The old password stops at once; run the new install command it gives you.

[Watch: an update (Mac)](../videos/media/update-mac-light.mp4)

The update lines, no code needed:

<div class="os" data-os="windows">

**Windows (PowerShell).**

```powershell
irm https://wow.example.org/install/update | iex
```

</div>

<div class="os" data-os="mac">

**Mac (Terminal).**

```sh
curl --proto '=https' --proto-redir '=https' -fsSL https://wow.example.org/install/update | bash
```

</div>

<!-- /generated:player-update -->

## Uninstall

<!-- generated:player-uninstall -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

[Watch: an uninstall (Mac)](../videos/media/uninstall-mac-light.mp4)

Run the line for your OS. It stops the job and removes the program and your config. Your addons stay, Polestar Recorder included: remove them in CurseForge, or delete their folders in `_forever_/Interface/AddOns`.

Your archive (WoWArchive in your home folder) stays: delete it yourself if you don't want the history. To delete your data on the site, ask the owner to take you off the friends list: it goes within minutes.

<div class="os" data-os="windows">

**Windows (PowerShell).**

```powershell
schtasks /Delete /TN wow-stats /F; Remove-Item -Recurse -Force "$env:LOCALAPPDATA\wow-stats", "$env:APPDATA\wow-stats"
```

</div>

<div class="os" data-os="mac">

**Mac (Terminal).**

```sh
launchctl bootout gui/$(id -u)/local.wow-stats; rm -f ~/Library/LaunchAgents/local.wow-stats.plist; rm -rf ~/.local/share/wow-stats ~/.config/wow-stats
```

</div>

<!-- /generated:player-uninstall -->

## Next

- [Your page, tab by tab](your-page.md): what each part shows and what to do with it.
- [Something's wrong](troubleshooting.md): fixes by symptom.
- [Connect your AI](../ai/connect.md): ask Claude, ChatGPT or another assistant about your
  character.
- [Addons](../reference/addons.md): every addon wow-stats reads and what each one adds.
