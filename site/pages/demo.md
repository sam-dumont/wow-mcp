# The live demo

Nibbet is a Gnome priest on WoW Forever who doesn't exist. A script plays the character from level 22 to 46 over thirty-three sessions: Polestar's guides through Darkshore, the Wetlands, Hillsbrad, Arathi, Stranglethorn, Dustwallow, Feralas, Tanaris and the Hinterlands, eight dungeons (Blackfathom Deeps, the Stockade, City of Dalaran, Razorfen Kraul, Scarlet Monastery's four wings, Razorfen Downs, Uldaman, Zul'Farrak), a Shadow build with Shadowform, Tailoring, Enchanting and Cooking, a few deaths and some auction house scans. The product's own publisher then builds Nibbet's pages, the same way it builds yours.

<div class="demo-links">
<a href="@BASE@demo/">Open the public page</a>
<a href="@BASE@demo/friends/">Open the friends' link</a>
</div>

## The nine characters

The character selector also opens eight shorter sessions, one for each other Forever class. All nine characters have different races. Flintwick, the Dwarf Beast Mastery hunter, carries a Glass Shooter and travels with a bear. Duskwick, the Undead Affliction warlock, carries a Firebelcher wand and summons a Voidwalker. Each character has their own equipment and spent talent points. The demo draws its own 2D icons. [Blizzard’s Forever race and class table](https://worldofwarcraft.blizzard.com/en-us/news/24304075/)

The eight new characters and both companions use original block models. The repository generates their meshes, textures and game tables. Your browser draws them through the same viewer that draws a player's own model.

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

The demo is rebuilt from a seeded script on every change: [Development](../../docs/development.md#the-docs-site-and-its-demo).
