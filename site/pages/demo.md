# The live demo

Nibbet is a made-up Gnome priest. The journal records 34 sessions from level 22 to 46, including 11 runs across eight dungeons. The same publisher that builds a player's dashboard turns that history into two demo pages.

<div class="demo-links">
<a href="@BASE@demo/">Open the public page</a>
<a href="@BASE@demo/friends/">Open the friends' link</a>
</div>

## The two pages

**The public page** is what anyone with the address sees: the card, the gear, the climb level by level, the dungeons cleared and the gear at each past level. [Privacy tiers](../../docs/explanation/privacy.md) lists what stays out.

**The friends' link** is the one you hand to friends. It adds the Overview (what to do next, with the quests to pick up and the drops worth having), the gear advice with what's on the auction house today, the journal, the timeline and the guide steps. Its files are encrypted: the key sits in the link after the `#`, which a browser never sends to a server. This demo's key is in its link on purpose.

The Battle.net side is made up too: a profile with a guild, the look, the talents read once a day (a Discipline start, then a respec into Shadow at 30) and one Legacy point. Nibbet's player has WoWSims Exporter, so the talent changes go back to level 22, not only 3 weeks.

Open **View in 3D** to turn Nibbet around. The published demo uses Blizzard's character model and gear from a separate demo archive. The play history is invented. If the archive is unavailable, the build draws a blocky gnome from the test fixtures. [Artwork and credits](../../THIRD_PARTY.md).

## What the demo can't do

The pages are plain files on GitHub Pages, so anything that needs the server is off:

- no login, no full view (other players, gold over time, the live timeline);
- no MCP: [the 0.13.0 recording](mcp-session.md) shows six calls for Nibbet, using the single-character demo.

The dates move: every build shifts the play so the last session is the day before. The auction house only lists what was on sale today or yesterday, so a demo frozen in October would have an empty auction house.

## Get this for your character

- A friend runs wow-stats for you: [Join a friend's server](../../docs/players/getting-started.md), about 5 minutes.
- Your AI on the same data: [Connect your AI](../../docs/ai/connect.md).
- Run it yourself: [the host guide](../../docs/self-host/production.md).

## Build it yourself

Each release rebuilds the demo from the same script. [Build the demo locally](../../docs/development.md#the-docs-site-and-its-demo).
