# Questions and answers

**For:** players, AI users and self-hosters with a quick question. **At the end:** you have
the short answer and the page with the long one.

## About wow-stats

### Is it allowed? Does it touch the game?

It never touches the game. It reads files the game already wrote to disk (the addons'
SavedVariables and, if there are any, the combat logs) after the game saved them. No memory
reading, no injected code, no automation in game. The addons it reads are ordinary addons from
CurseForge.

### Which addons do I need?

Polestar is the one that matters: levels, route steps and the gear scoring come from it.
Each optional one fills one more part of the page; a missing one leaves that part empty.

<!-- generated:addon-list -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

- **[Polestar](https://www.curseforge.com/wow/addons/polestar)** (required): level times, quests, deaths, dungeons, the gear advice. Install it, then tick it in AddOns at character select.
- **[Polestar Recorder](https://github.com/fitzforge/polestar/releases/latest)** (required): your play sessions, zone by zone. The install command adds it: type `/psrec on` once in game.
- **[Syndicator](https://www.curseforge.com/wow/addons/syndicator)** (optional): gear and gold on every character. Install it, then log in once with each character.
- **[Auctionator](https://www.curseforge.com/wow/addons/auctionator)** (optional): prices from your own auction house scans. Install it, then scan the auction house once.
- **[Altoholic](https://www.curseforge.com/wow/addons/altoholic)** (optional): your full /played from level 1, the quests you finished before Polestar, the recipes and spells you know, and the reputations, stats, lockouts, money and XP of every character. Install it, then log in once with each character.
- **[Questie](https://www.curseforge.com/wow/addons/questie)** (optional): the quests you finished before Polestar (Altoholic does it too: one of the two is enough). Install it, then log in once with each character.
- **[WoWSims Exporter](https://www.curseforge.com/wow/addons/wowsims-exporter)** (optional): every talent change, for your full talent history (without it: the last 3 weeks). Install it, then tick it in the AddOns list at character select: it starts switched off.

<!-- /generated:addon-list -->

More on each: [Addons it reads](reference/addons.md).

### Does it work on retail or Classic Era?

It's built for WoW Forever: the route times, the gear data and the dungeon list are
Forever's. Other versions aren't supported.

### What does it cost?

Nothing. It's open source (GPL-3.0). If you run the server yourself, it fits on a small box
you already have: 2 vCPU and 2 GiB of RAM. The AI part uses your own assistant's plan, unless
you turn on the server's own analysis: then you pay its AI provider, inside a monthly budget you
set ([Server-side analysis](self-host/server-analysis.md)).

### Who sees my data?

You see everything on your page. Friends see only what's on the zogzog link you send them,
anyone sees your public page if you switch it on. Neither ever shows other players, what
killed you, or where you are right now. [What's shared](players/privacy.md).

## My page

### My page is empty after I played

The game writes the addons' files only on `/reload`, logout or a character switch, and the
program on your computer runs every 5 minutes. Type `/reload`, wait 5 minutes, refresh. Still empty: type
`/psrec on` once (the Recorder starts off unless the Polestar Client installed it), then
`/reload` again. [My page is still empty after 10 minutes](players/troubleshooting.md#my-page-is-still-empty-after-10-minutes).

### My last session is missing

The game crashed, or you closed it with Alt+F4: what happened since the last save is gone,
for every addon. Put `/reload` on a macro key and press it after a dungeon, after an auction
house scan and before a break. [My last session is missing](players/troubleshooting.md#my-last-session-is-missing).

### One part of the page is empty

Each part comes from one addon: no bags without Syndicator, no prices without an Auctionator
scan. The page says which one is missing and how to fix it in a "Missing:" line.
[A part of my page is empty](players/troubleshooting.md#a-part-of-my-page-is-empty).

### One of my characters is missing

Addons are switched on per character. At character select, AddOns, tick Polestar and Polestar
Recorder for that one, log in, `/reload`, wait 5 minutes.

### Do I have to update or reinstall anything?

Only your addons (Update all in the CurseForge app). The program on your computer updates itself
within a few hours of a new version on the server, Polestar Recorder with it.
[Updates](players/getting-started.md#updates).

### What does "2.0×" mean on the Overview?

Your last 5 levels took twice as long as Polestar's route time for them. 1.0× is the route's
pace. [Overview and next moves](players/overview.md).

### What are "points" in the upgrades?

An item's score for your spec on [ForeverDB.net](https://foreverdb.net)'s stat weights, the
number that compares a helmet with a ring. The **Weights** pick on the gear page sets what they
value: Leveling (the default: Stamina, armour and a caster's wand count for more), PvP or PvE.
The page turns them into the slot's BiS: the badge on an item's icon is its share of the best
item you can get for that slot at your level, and "70% → 91% on your head" is that share for what you wear
now, then with the item ("empty → 91%" when nothing is there). An item with nothing to
measure against keeps its points ("+4.3 points"). [Gear and upgrades](players/gear.md).

### I changed my weights in Polestar and nothing moved

On purpose: the scores use ForeverDB.net's weights only. Polestar's Adjust weights, its Stat
weights spec and its smallest upgrade are not read. Pick the spec and the mode on the gear
page instead; your AI's `gear` answers follow the same pick.

### Why does a move say "if not done yet"?

Nothing your addons saved shows the training. Polestar Recorder only writes a skill when it
changes while it records, so a trainer visit it missed looks like no visit. With
[Altoholic](https://www.curseforge.com/wow/addons/altoholic), the game's own skill list and
spellbook come along at every login and the move goes away by itself. Without it, the move
says what it last saw and when ("Enchanting 150/150 when Polestar Recorder last saw it"):
already trained, ignore it.

## Your AI

### Which assistants work?

Any that takes a custom MCP server: Claude (web, desktop, phone and Claude Code), ChatGPT,
Codex, Cursor, VS Code, Gemini CLI, Zed and more. [Connect your AI](ai/connect.md).

### Does wow-stats see my conversations?

No. Your assistant calls the server for data and does the thinking in your own account. If
the host turned on the server's own analysis, the server talks to their AI provider in its own
conversation, not yours: [What's shared](players/privacy.md).

### Does the server run an AI on my data?

Only if the host turned it on. Then, once one of your characters has {{analysis_hours}} hours of play since its
analysis was last refreshed (the host can change the number), the server has the host's AI
provider rewrite that character's "How you play" section. Your own AI never starts one: asking
it to refresh your analysis does the work in your account and resets the timer. The Privacy page
in the app says whether it's on.

### "Client Not Registered" in Claude

Claude kept a login from before the server changed. Remove the wow-stats connector in Claude,
add it again with the same address, and connect once more. [When it doesn't work](ai/connect.md#when-it-doesnt-work).

## Running a server

### What do I need?

Docker, and a computer or small box that stays on: 2 vCPU and 2 GiB of RAM for one player.
For friends and for claude.ai or ChatGPT to reach it, a domain name and HTTPS.
[The host guide](self-host/production.md).

### Can I try it without the game?

Yes: [Try it at home](self-host/demo.md) runs the server with a made-up character in about 10
minutes, the same one as [the live demo](../site/pages/demo.md), which needs nothing installed.

### How do I update it?

Back up, put the new release's `tag@digest` in `WOW_STATS_IMAGE`, then `docker compose pull`
and `docker compose up -d`. Your data lives in Docker
volumes, which an upgrade doesn't touch; the changelog's "Upgrading" notes say when there's
more to do.
[Upgrade](self-host/upgrade.md) and the [changelog](../CHANGELOG.md).

### I found a security problem

Please don't open a public issue: [the security policy](../SECURITY.md) says how to report it
privately.
