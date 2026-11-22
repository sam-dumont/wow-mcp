# Join a friend's server

**For:** players a friend invited to their wow-stats server. **At the end:** your page
updates by itself after you play.

A friend runs wow-stats and sent you a link. You need a few addons and one command on the
computer you play on: about 5 minutes. The link opens your start
page, which has these same steps with your own command in them: use it if you can.

## What you need

<!-- generated:player-needs -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

- WoW Forever on Windows 10/11 or macOS, logged in once.
- The link the owner sent you.
- No admin rights: it all installs for your user.

<!-- /generated:player-needs -->

## The steps

<!-- generated:player-steps -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

[Watch: the whole setup, from the start page](../videos/media/onboarding-light.mp4)

### 1. Log in

Open https://wow.example.org/start, the owner's link, and sign in the way they told you.

<details>
<summary>Check it worked</summary>

**It worked when:** Your start page opens with your install command ready.

**If not:** "You're signed in, but the person who runs this site hasn't let you in yet": the owner hasn't added you yet. Tell them you signed in, then reload the page. A login loop: same message to the owner.

</details>

### 2. Install the addons

Install Polestar and Syndicator with the CurseForge app (https://www.curseforge.com/download/app), under WoW Forever.

<details>
<summary>Check it worked</summary>

**It worked when:** Character select, AddOns: Polestar, Polestar Recorder and Syndicator are ticked, and you typed `/psrec on` once in game (not needed with the Polestar Client). Syndicator sends your own gear and bags (without it, Battle.net's copy, no bags); the others are optional: your start page says what each adds.

**If not:** Missing from the AddOns list: the folder is one level too deep (`Interface/AddOns/Polestar/Polestar/...`). Move it up so `Interface/AddOns/Polestar/Polestar.toc` exists, then restart WoW.

</details>

### 3. Run the install command

[Watch: run the install command (Mac)](../videos/media/install-mac-light.mp4)

It installs a small program that sends your addons' saves to your page every 5 minutes, and Polestar Recorder.

<div class="os" data-os="windows">

**Windows (PowerShell).** Windows key, type PowerShell, Enter. Paste with a right click, press Enter.

Your own command is on your start page:

```powershell
irm https://wow.example.org/install/<your code> | iex
```

<details>
<summary>What it installs</summary>

Only under your user folder and WoW's AddOns folder: `%LOCALAPPDATA%\wow-stats` and a scheduled task "wow-stats" (every 5 minutes, as you, no admin). Log: `%LOCALAPPDATA%\wow-stats\wow-stats.log`. Polestar Recorder (the official release, checked; a newer one you have stays) in `_forever_\Interface\AddOns`.

</details>

</div>

<div class="os" data-os="mac">

**Mac (Terminal).** Cmd+Space, type Terminal, Enter. Paste with Cmd+V, press Enter.

Your own command is on your start page:

```sh
curl --proto '=https' --proto-redir '=https' -fsSL https://wow.example.org/install/<your code> | bash
```

<details>
<summary>What it installs</summary>

Only under your user folder and WoW's AddOns folder: `~/.local/share/wow-stats`, `~/.config/wow-stats/config.toml` and a launchd agent local.wow-stats (every 5 minutes). Log: `~/Library/Logs/wow-stats.log`. Polestar Recorder (the official release, checked; a newer one you have stays) in `_forever_/Interface/AddOns`.

</details>

</div>

<details>
<summary>Check it worked</summary>

**It worked when:** The output says "wow-stats is installed. WoW folder: ..." and that folder is your `_forever_` folder.

**If not:** "WoW folder not found" or the wrong folder: see that troubleshooting entry. "This install code expired or was used already": get a new command and run it.

It works once, within 15 minutes. Used or expired: click "Get my install command" again.

</details>

### 4. Play 5 minutes

Play, then `/reload` or log out: WoW saves the addons' files only then. Your page gets them within 5 minutes.

<details>
<summary>Check it worked</summary>

**It worked when:** "Data arrived" ticks on your start page by itself, with everything your addons saved so far. A crash or Alt+F4 loses what wasn't saved: `/reload` before a break.

**If not:** Nothing after 10 minutes: first type `/psrec on` in WoW (the Recorder starts off without the Polestar Client), then `/reload`. Still nothing: see "My page is still empty after 10 minutes".

</details>

### 5. See your character

Open https://wow.example.org: your characters are in the list at the top.

<details>
<summary>Check it worked</summary>

**It worked when:** Your character is in the list at the top of your page. Battle.net's copy (their look, and your gear without Syndicator) comes within minutes, then every 2 hours, when the owner set it up. Your public page stays off until you switch it on in Settings.

**If not:** Data arrived but no character: Polestar Recorder is off for that character. Tick it in AddOns at character select, `/reload`, wait 5 minutes.

</details>

### 6. Connect your AI

Add wow-stats to your own Claude, ChatGPT or other assistant: it plans your next session from your stats. wow-stats never sees the conversation.

<details>
<summary>Check it worked</summary>

**It worked when:** Ask "With wow-stats, what's my character doing?": the answer names your character.

**If not:** "Client Not Registered" or a disconnected connector: see that troubleshooting entry.

</details>

<!-- /generated:player-steps -->

What the program sends: [What's shared, and with whom](privacy.md).

## Optional: turn on combat logging

WoW can write every fight to a file, and the program sends it with the rest. Nothing on your
page reads it yet: it's for death recaps and dungeon and raid performance, later. Skip this
step if you don't care about those.

**Turn it on:** type `/combatlog` in chat. The game writes to the `Logs` folder next to `WTF`,
and logging stays on until you type `/combatlog` again or exit the game
([Warcraft Logs' guide](https://www.warcraftlogs.com/help/start)). So it's once per game
session, not once for good.

**Make it one click:** the same command turns logging off when it's already on, so a second
press stops it. A macro that only ever turns it on is safer:

1. Type `/macro`, click **New**, name it `Log`, pick any icon.
2. Paste this line, then drag the icon to your action bar:
   ```
   /run LoggingCombat(true)
   ```
3. Press it once after you log in. Pressing it again changes nothing.

`LoggingCombat` is the game's own command ([Warcraft Wiki](https://warcraft.wiki.gg/wiki/API:LoggingCombat)),
not an addon.

**What it costs:** the file grows for as long as logging is on, so an evening of play is a
bigger upload than your addons' files. Other players in it reach the server as "another
player", never by name ([What's shared](privacy.md)).

## Updates

<!-- generated:player-update -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

- Addons: Update all in CurseForge.
- The program and Polestar Recorder update themselves.
- New computer or leaked password: Reset my upload password, then run the new install command.

<details>
<summary>If your start page asks for an update</summary>

[Watch: an update (Mac)](../videos/media/update-mac-light.mp4)

No code needed:

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

</details>

<!-- /generated:player-update -->

## Uninstall

<details>
<summary>Remove the program from your computer</summary>

<!-- generated:player-uninstall -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

[Watch: an uninstall (Mac)](../videos/media/uninstall-mac-light.mp4)

Run the line for your OS. It stops the job and removes the program and your config. Your addons stay, Polestar Recorder included: remove them in CurseForge, or delete their folders in `_forever_/Interface/AddOns`.

Your archive (WoWArchive in your home folder) stays: delete it yourself if you don't want the history. To delete your data on the site, ask the owner to take you off the friends list: it's deleted 7 days later, or right away if you ask.

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

</details>

## Next

- [Your page at a glance](your-page.md): the tabs and Settings.
- [Something's wrong](troubleshooting.md): fixes by symptom.
- [Connect your AI](../ai/connect.md): each assistant, step by step.
