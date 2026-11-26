# wow-stats

## Pick your path

<div class="paths">
<a class="path" href="../../docs/players/getting-started.md">
<span class="path-kicker">Play</span>
<strong>A friend runs a server</strong>
<span>They sent you a link. Add Polestar, run one command, play. About 5 minutes, no admin rights.</span>
<span class="path-go">Join a friend's server</span>
</a>
<a class="path" href="../../docs/ai/connect.md">
<span class="path-kicker">Connect your AI</span>
<strong>Ask about your character</strong>
<span>Claude, ChatGPT or any MCP client reads your data and plans your next session. Nothing to install.</span>
<span class="path-go">Connect your AI</span>
</a>
<a class="path" href="../../docs/self-host/production.md">
<span class="path-kicker">Host it</span>
<strong>Run it yourself</strong>
<span>One Docker Compose file on a small box at home, for you and your friends. Try it with a made-up character in 10 minutes first.</span>
<span class="path-go">The host guide</span>
</a>
</div>

## What's on your page

<div class="feature">

![Nibbet’s next moves. Equip Deathmage Sash for a waist upgrade from 42% to 50%, run Scarlet Monastery Cathedral, then check Apprentice Riding.](../../docs/images/next-moves.webp)

<div>
<p class="eyebrow">Before you log in</p>
<h3>What to do with the next hour</h3>
<p>Nibbet already has a better belt in the bags. Equipping it is the first move. The next is a trip to Scarlet Monastery Cathedral. The Overview also shows how long the next level should take at your pace.</p>
<p><a href="../../docs/players/overview.md">Overview and next moves →</a></p>
</div>
</div>

<div class="feature flip">

![The auction house list starts with Shadoweave Shoulders, Shadoweave Mask and Underworld Band. Dungeon and world drops appear in their own groups below.](../../docs/images/upgrades.webp)

<div>
<p class="eyebrow">Gear</p>
<h3>Gear you can get at your level</h3>
<p>Find a drop by dungeon, check a craft's recipe and materials, or compare auction listings by price and scan date. Each upgrade shows what you'd gain in that slot. Gear scores use <a href="https://foreverdb.net">ForeverDB.net</a>'s levelling, PvP or PvE weights.</p>
<p><a href="../../docs/players/gear.md">Gear and upgrades →</a></p>
</div>
</div>

<div class="feature">

![Zul’Farrak lists gear drops, a quest handed in outside and the party from Nibbet’s last run. Uldaman lists Explorer’s League Lodestar as a quest reward.](../../docs/images/dungeons.webp)

<div>
<p class="eyebrow">Dungeons</p>
<h3>The dungeon worth running</h3>
<p>Each dungeon around your level gets a card: the quests to pick up before you go in, the drops you'd actually wear, and the XP of a run against your own questing, with the time for the pickups and hand-ins counted in (<a href="../../docs/players/dungeons.md">how a trip is counted</a>).</p>
<p><a href="../../docs/players/dungeons.md">Dungeons →</a></p>
</div>
</div>

Also on your page: **a plan for tonight**, 1 to 6 hours of guide steps with checkpoints and
where to log out ([Plan your next hours](../../docs/players/plan.md)), and **your history**:
every level, one journal entry per session, your look at each level
([Climb, timeline, journal](../../docs/players/history.md)).

<div class="need">
<div>
<p class="eyebrow">What you need</p>

### To play on a friend's server

- WoW Forever on Windows 10/11 or a Mac, logged in once.
- [Polestar](https://github.com/fitzforge/polestar) and Syndicator, from the CurseForge app.
- One command from your start page. No admin rights.

### To run your own

- Docker, and a box that stays on: 2 vCPU and 2 GiB of RAM for one player.
- About 10 minutes to [try it with made-up characters](../../docs/self-host/demo.md) first.

</div>
<div>
<p class="eyebrow">What it doesn't do</p>

### Your data stays yours

- **No touching the game.** It reads the files WoW and your addons already save, after they
  save them. No addon of its own, no memory reading.
- **No sending your data elsewhere.** It goes from your computer to the server you (or a
  friend) run, and nowhere else unless that server's host turns on the analysis below. Friends
  see what you choose to share.
- **No model behind the numbers.** The page and the MCP tools work them out in code from your
  data; your own AI reads them and writes the reply, in your own account. A host can also have
  the server analyse your play with an AI provider they pick, once a character has {{analysis_hours}} hours of play since
  its last refresh (their setting). Off until they set it up; the app's Privacy page says
  whether it's on.

[What's shared →](../../docs/players/privacy.md)

</div>
</div>

How the pieces fit: [How it works](../../docs/explanation/how-it-works.md). Questions:
[Questions and answers](../../docs/faq.md). The words these pages use: [Glossary](../../docs/glossary.md).
Why a levelling log grew an MCP server and signed releases in six days:
[How this was built](../../docs/how-this-was-built.md).

<p class="fineprint">The software is licensed under GPL-3.0.
<a href="https://github.com/fitzforge/polestar">Polestar</a> supplies the route times.
Gear weights and additional item sources: Data from <a href="https://foreverdb.net">ForeverDB.net</a>.
Blizzard Entertainment owns the artwork in the separate 3D demo. World of Warcraft is its trademark.
This is an independent project. <a href="../../THIRD_PARTY.md">Third-party credits and licences</a>.</p>
