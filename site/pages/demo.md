# The live demo

Nibbet is a Gnome priest on WoW Forever who doesn't exist. A script plays him from level 22 to 34 over twenty sessions: Polestar's guides through Darkshore, the Wetlands, Hillsbrad and Arathi, four dungeons (Blackfathom Deeps, the Stockade, City of Dalaran, Razorfen Kraul), Tailoring, Enchanting and Cooking, a few deaths and some auction house scans. The product's own publisher then builds his pages, the same way it builds yours.

<div class="demo-links">
<a href="@BASE@demo/">Open his public page</a>
<a href="@BASE@demo/friends/">Open the friends' link</a>
</div>

## The two pages

**The public page** is what anyone with the address sees: his card, his gear, the climb level by level, the dungeons he cleared and his gear at each past level. [Privacy tiers](../../docs/explanation/privacy.md) lists what stays out.

**The friends' link** is the one you hand to friends. It adds the Overview (what to do next, with the quests to pick up and the drops worth having), the gear advice with what's on the auction house today, the journal, the timeline and the guide steps. Its files are encrypted: the key sits in the link after the `#`, which a browser never sends to a server. This demo's key is in its link on purpose.

His Battle.net side is made up too: a profile with a guild, his look, his talents read once a day (a Discipline start, then a respec into Shadow at 30) and one Legacy point. His player has WoWSims Exporter, so the talent changes go back to level 22, not only 3 weeks.

His picture and "View in 3D" come from made-up game files: a blocky gnome and his gear, written in the formats the shipper sends (the client's tables, models, textures) by a script in the repository. Nothing in them comes from Blizzard's files. The product draws them the way it draws yours: the 3D view in your browser, the pictures in its renderer.

## What the demo can't do

The pages are plain files on GitHub Pages, so anything that needs the server is off:

- no login, no full view (other players, gold over time, the live timeline);
- no MCP: [a recorded session](mcp-session.md) shows what an assistant gets from the same data.

His portrait is a still of his 3D model, not Battle.net's render: there is no Nibbet on Battle.net to render.

The dates move: every build shifts his play so his last session is the day before. The auction house only lists what was on sale today or yesterday, so a demo frozen in October would have an empty auction house.

## Build it yourself

The demo is rebuilt from a seeded script on every change: [Development](../../docs/development.md#the-docs-site-and-its-demo).
