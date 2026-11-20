# Join a friend's server

**For:** WoW Forever players a friend invited to their wow-stats server. **At the end:** your
characters are on your own page, and it updates by itself a few minutes after you log out.

A friend runs wow-stats and sent you a link. You need a few addons and one command on the
computer you play on: about 5 minutes, nothing from this repository. The link opens your start
page, which has these same steps with your own command in them: use it if you can.

## What you need

<!-- generated:player-needs -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

- WoW Forever on Windows 10 or 11, or macOS, logged in once (so its WTF folder exists).
- The link the owner sent you.
- No admin rights: everything installs for your user only.

<!-- /generated:player-needs -->

## The steps

<!-- generated:player-steps -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

[Watch: the whole setup, from the start page](../videos/media/onboarding-light.mp4)

### 1. Log in

Open https://wow.example.org/start, the link the owner sent you, and sign in the way they told you: the account they made for you, or Battle.net when the button is there.

**It worked when:** Your start page opens with your install command ready.

<details>
<summary>Didn't work?</summary>

"You're signed in, but the person who runs this site hasn't let you in yet": the owner hasn't added you yet. Tell them you signed in, then reload the page. A login loop: same message to the owner.

</details>

### 2. Install the addons

Easiest with the CurseForge app (https://www.curseforge.com/download/app), which lists WoW Forever as its own install. Polestar is required. Add Syndicator: it gives your page your own gear and bags (without it, Battle.net's copy). The others are optional: your start page says what each one adds.

**It worked when:** Character select, AddOns: Polestar, Polestar Recorder and Syndicator are ticked, and you typed `/psrec on` once in game (not needed with the Polestar Client).

<details>
<summary>Didn't work?</summary>

Missing from the AddOns list: the folder is one level too deep (`Interface/AddOns/Polestar/Polestar/...`). Move it up so `Interface/AddOns/Polestar/Polestar.toc` exists, then restart WoW.

</details>

### 3. Run the install command

[Watch: run the install command (Mac)](../videos/media/install-mac-light.mp4)

It installs a small program that sends what WoW and your addons save to your page every 5 minutes, and adds Polestar Recorder (the official release, checked) unless you have a newer one. It writes only under your user folder and to WoW's AddOns folder.

<div class="os" data-os="windows">

**Windows (PowerShell).** Windows key, type PowerShell, Enter. Paste with a right click, press Enter.

Your own command is on your start page. It looks like this:

```powershell
irm https://wow.example.org/install/<your code> | iex
```

<details>
<summary>What it installs</summary>

`%LOCALAPPDATA%\wow-stats` and a scheduled task "wow-stats" (every 5 minutes, as you, no admin). Log: `%LOCALAPPDATA%\wow-stats\wow-stats.log`. Polestar Recorder in `_forever_\Interface\AddOns`.

</details>

</div>

<div class="os" data-os="mac">

**Mac (Terminal).** Cmd+Space, type Terminal, Enter. Paste with Cmd+V, press Enter.

Your own command is on your start page. It looks like this:

```sh
curl --proto '=https' --proto-redir '=https' -fsSL https://wow.example.org/install/<your code> | bash
```

<details>
<summary>What it installs</summary>

`~/.local/share/wow-stats`, `~/.config/wow-stats/config.toml` and a launchd agent local.wow-stats (every 5 minutes). Log: `~/Library/Logs/wow-stats.log`. Polestar Recorder in `_forever_/Interface/AddOns`.

</details>

</div>

It works once, within 15 minutes. Used or expired: click "Get my install command" again.

**It worked when:** The output says "wow-stats is installed. WoW folder: ..." and that folder is your `_forever_` folder.

<details>
<summary>Didn't work?</summary>

"WoW folder not found" or the wrong folder: see that troubleshooting entry. "This install code expired or was used already": get a new command and run it.

</details>

### 4. Play 5 minutes

Play a few minutes, then `/reload` or log out: WoW saves the addons' files only then, and your page gets them within 5 minutes. The first time, that's everything your addons saved so far.

A crash or Alt+F4 loses what wasn't saved: `/reload` after a dungeon and before a break.

**It worked when:** "Data arrived" ticks on your start page by itself.

<details>
<summary>Didn't work?</summary>

Nothing after 10 minutes: first type `/psrec on` in WoW (the Recorder starts off without the Polestar Client), then `/reload`. Still nothing: see "My page is still empty after 10 minutes".

</details>

### 5. See your character

Open https://wow.example.org: your characters are in the list at the top. Battle.net's copy (their look, and your gear without Syndicator) comes within minutes, then every 2 hours, when the owner set it up. Your public page stays off until you switch it on in Settings.

**It worked when:** Your character is in the list at the top of your page.

<details>
<summary>Didn't work?</summary>

Data arrived but no character: Polestar Recorder is off for that character. Tick it in AddOns at character select, `/reload`, wait 5 minutes.

</details>

### 6. Connect your AI

Claude, ChatGPT or another assistant reads your stats and plans your next session, on your own AI account: wow-stats never sees the conversation.

**It worked when:** Ask "With wow-stats, what's my character doing?": the answer names your character.

<details>
<summary>Didn't work?</summary>

"Client Not Registered" or a disconnected connector: see that troubleshooting entry.

</details>

<!-- /generated:player-steps -->

What the program sends, and what it never touches: [What's shared, and with whom](privacy.md).

## Updates

<!-- generated:player-update -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

- Addons: Update all in the CurseForge app.
- The program updates itself, Polestar Recorder too, within a few hours of a new version: nothing to reinstall. Only an install from before 0.5.1 needs the update line once, and your start page says so.
- New computer or leaked upload password: Reset my upload password on your start page, then run the new install command.

<details>
<summary>The update line, when your start page asks for it</summary>

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

</details>

## Next

- [Your page at a glance](your-page.md): the tabs, your links and Settings.
- [Something's wrong](troubleshooting.md): fixes by symptom.
- [Connect your AI](../ai/connect.md): Claude, ChatGPT or another assistant on your character.
