# The live demo

Nibbet is a Gnome priest on WoW Forever who doesn't exist. A script plays him from level 22 to 34 over nineteen evenings: Polestar's guides through Darkshore, the Wetlands, Hillsbrad and Arathi, four dungeons (Blackfathom Deeps, the Stockade, City of Dalaran, Razorfen Kraul), Tailoring, Enchanting and Cooking, a few deaths and some auction house scans. The product's own publisher then builds his pages, the same way it builds yours.

<div class="demo-links">
<a href="@BASE@demo/">Open his public page</a>
<a href="@BASE@demo/friends/">Open the friends' link</a>
</div>

## The two pages

**The public page** is what anyone with the address sees: his card, his gear, the climb level by level, the dungeons he cleared and his gear at each past level. [Privacy tiers](../../docs/explanation/privacy.md) lists what stays out.

**The friends' link** is the one you hand to friends. It adds the Overview (what to do next, with the quests to pick up and the drops worth having), the gear advice with what's on the auction house today, the journal, the timeline and the guide steps. Its files are encrypted: the key sits in the link after the `#`, which a browser never sends to a server. This demo's key is in its link on purpose.

Nibbet's player never installed Syndicator, so his bags can't count as a source for upgrades. The full view shows that as one "Missing:" line with the fix, until the addon is there; the demo has no full view, but [the recorded MCP session](mcp-session.md) shows the same line in every answer it touches.

## What the demo can't do

The pages are plain files on GitHub Pages, so anything that needs the server is off:

- no login, no full view (other players, gold over time, the live timeline);
- no MCP: [a recorded session](mcp-session.md) shows what an assistant gets from the same data;
- no Battle.net data: Nibbet has no Battle.net profile, so his card shows the outline instead of his render. "View in 3D" says why it has nothing to draw: the model comes from a player's own game files, and the demo has none.

The dates move: every build shifts his play so his last evening is the day before. The auction house only lists what was on sale today or yesterday, so a demo frozen in October would have an empty auction house.

## Build it yourself

The demo is rebuilt from a seeded script on every change: [Development](../../docs/development.md#the-docs-site-and-its-demo).
