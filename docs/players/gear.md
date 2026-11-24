# Gear and upgrades

**For:** players with a wow-stats page. **At the end:** you know which item to chase next,
where it comes from, and whether to buy it instead.

Everything here is in the Character tab: your gear at the top, the upgrades under it.

## Your gear

![The Equipment panel of the demo priest: the picture in the middle with View in 3D under it, every slot with its item, item level and BiS badge, a green "▲ 36% → 100% with the best at hand" under the slots with an upgrade, the badge legend with "Leveling · Weights: ForeverDB.net", item level 28.2 and professions below](../images/gear.webp)

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

![The Upgrades list grouped by where you get them: Tailoring crafts within reach of skill 141, each with its badge and its slot's share before and after ("17% → 100% on your feet") and a ForeverDB.net source link, then a vendor in Arathi Highlands with prices, then quests in Stormwind City](../images/upgrades.webp)

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

The list weighs how hard each item is to get, from its easiest source (your AI sees the class
as `effort`):

| | From |
|---|---|
| **easy** | your bags or bank, an auction house listing, a quest, a vendor, a craft you can make, a reputation you hold, a drop of 25% or more |
| **some effort** | a drop of 8-25%, a craft still to learn or skill up for, a reputation up to Honored you don't hold yet, a PvP rank you hold |
| **hard** | a drop of 1-8%, a drop seen in Forever with no rate, a world drop, a raid drop, a reputation above Honored you don't hold |
| **long shot** | a drop under 1%, a rate known only from Classic, a PvP rank you don't hold (or that nothing recorded) |

A rate known only from Classic counts one step harder; an auction house listing makes any item
easy, with its price. A harder item wins a slot only when it scores 5.3% more (some effort) or
17.6% more (hard). The % lines measure against what you can get.

Drops say their chance and how many runs that means ("43.75% over 16 kills"; a 3% boss drop
adds "about 33 runs"). Reputation gear says where you stand ("you're Friendly: 16,500 rep to
go"), PvP rank gear too ("rank 3 needed, you're rank 1"); a rank nothing recorded yet counts as
a long shot. A 0.03% drop never becomes the pick, however good it is.

**Best possible, out of plain reach** lists, apart, the slots whose best item is a long shot
or has no known source: with its odds, never as an upgrade to chase.

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
