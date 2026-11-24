# Gear and upgrades

**For:** players with a wow-stats page. **At the end:** you know which item to chase next,
where it comes from, and whether to buy it instead.

Everything here is in the Character tab: your gear at the top, the upgrades under it.

## Your gear

![The Equipment panel of the demo priest: the picture in the middle with View in 3D under it, every slot with its item, item level and BiS badge, a green "▲ 17% → 82% with the best at hand" under the slots with an upgrade, the badge legend, item level 29.1 and professions below](../images/gear.webp)

The badge on each item's icon says how close it is to the best you can wear at your level for
that slot: **BiS** (orange) is the best, then **2nd** or a share like **84%** (purple from 85%,
blue from 50%, dark below). Rings and trinkets count the best two: a new one replaces the
weaker, so its line starts from the weaker one's %. Both weapons count as one setup. No badge:
nothing to measure against.

"▲ 62% → 85% with the best at hand", in green under a slot, means the best upgrade you can get
now takes that slot from 62% to 85%. Click the item to compare. "empty → 85%": nothing is worn
there yet. For rings and trinkets the weaker one gets the best item, the stronger one the next:
both read 100% only when two different items that good are in reach.

The picture in the middle is your character in that gear. "Picture on its way": it isn't drawn
yet, and **View in 3D** shows the model meanwhile.

## The upgrades list

![The Upgrades list grouped by where you get them: Scarlet Monastery Library with three drops, each with its badge and its slot's share before and after ("17% → 100% on your head"), and the expected points per clear, the Tailoring crafts within reach of skill 140, then quests in Stranglethorn Vale and Stormwind City](../images/upgrades.webp)

Every item has the same badge and says what it does to its slot: "70% → 91% on your chest"
("empty → 91%" for an empty slot). The slot furthest from its best comes first.

- **By where you get it** (the default) groups them by trip: a dungeon, the quests of a zone, a
  profession, the auction house. Each group says what the trip holds: "31% chance one of your 3
  upgrades drops" for a dungeon, "2 upgrades you can take now" for quests or crafts, "2
  upgrades for 4g 10s in all" at the auction house.
- **By slot** shows the best item per slot: **Now** (you can wear it), **Level 35** (not yet),
  **cheap** (on the auction house for at most what you earn in a level).

**Scored for**, at the top, picks the spec ("Automatic" follows your talents): pick another to
see the upgrades for a respec.

**Weights** picks what the scores value: **Leveling** (the default: Stamina, armour and a
caster's wand count for more), **PvP** or **PvE** (raids and dungeons at 60). The weights are
[ForeverDB.net](https://foreverdb.net)'s, and every score links there so you can check it.
Your pick is kept on the server for that character, so your AI's `gear` answers use it too.
Next moves and the plan always score on Leveling.

**BiS with no known source** is the slot's best item when nothing says where to get it. It's
never an upgrade to go and get, but the % lines measure against it, so you see how far the
best known item is from the real top.
<!-- after #214 -->

Each source says how hard it is: **easy** (your bags, the auction house, a quest, a vendor, a
craft you know, a drop of 25% or more), **some effort**, **hard** or **long shot** (a drop
under 1%, or a reputation or PvP rank you don't have). The list prefers an easy item to a
slightly better hard one: a hard item has to score about 18% more to win. A long shot is never
an upgrade; it shows apart with its odds. Drops say their rate ("4% over 1,200 kills") and
about how many runs, reputation gear says where you stand ("you're Friendly: 16,500 rep to
go").
<!-- /after #214 -->

## The same weights in the game

At the bottom of the Upgrades list, **Copy for Forever Leveling BiS** and **Copy as Pawn
scale** copy the weights shown (the spec and mode picked above) as text, for the Forever
Leveling BiS addon. The text also shows in a box under the buttons, in case the browser
refuses the clipboard. To use it in the game: type `/bis`, click **Stat weights** at the top
right of the window, then **Import**, paste, check the spec and weapon setup, and click
**Import**. The addon picks the new profile ("ForeverDB Leveling", "ForeverDB PvP" or
"ForeverDB PvE") for that spec and setup.

It's weights only: the addon keeps its own item list, and adds what it knows on top (item
effects, random suffixes, racial weapon bonuses, set bonuses at 60), so a few items can rank
differently there. Take the Forever Leveling BiS string when you can: it is exact. The Pawn
scale has no ranged-only hit (hunters' "+% hit with ranged weapons" items score lower), and the
addon rescales a Pawn scale on import (same order, other numbers). Your AI gets the same text
from `gear` with `export: flb` or `export: pawn`. Data from
[ForeverDB.net](https://foreverdb.net).

## What to do with it

1. Take the top group in "By where you get it".
2. Going to a dungeon: read its card in [Dungeons](dungeons.md) first.
3. "In your bags: equip it" means you already carry it (seeing your bags needs Syndicator).
   PvP rank gear checks your rank, which Syndicator and Altoholic record once you hold PvP
   renown (ForeverDB does too, if you run it). Until then it says "your PvP rank isn't
   recorded yet".
4. To see what's for sale: scan the auction house with Auctionator, `/reload`, and an
   **Auction House** group shows within 5 minutes, with prices. On a server you share with
   friends, a friend's scan counts too: anyone's newest scan of your ruleset and your faction's
   house (or the neutral one) fills the group, and nobody sees who scanned it.

List missing: [A part of my page is empty](troubleshooting.md#a-part-of-my-page-is-empty).
How the scoring works: [Gear reference](../gear-reference.md).
