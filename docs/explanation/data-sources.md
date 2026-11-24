# Where the data comes from

**For:** players who wonder why an addon is on the list, and self-hosters deciding whether to
set up a Battle.net client. **At the end:** you know which part of the page comes from
Blizzard's API, which from an addon, and why.

## The rule

Blizzard's API first. It's there for that, it needs nothing installed on your PC, and it's the
same data the armory shows. An addon only fills what the API can't give, which comes down to
two cases:

1. **The API doesn't have it.** Blizzard's Classic profile API (the one Forever answers) covers
   a character's profile, appearance, equipment, talents, media, statistics, achievements,
   reputations, PvP and hunter pets. No professions, no quests, no bags, no gold, no played
   time, no zone.
2. **You need history.** Blizzard's terms cap API data at 30 days, so wow-stats deletes every
   API reading after 23 to 30 days. Your gear at level 24, how long level 31 took, what Wool
   Cloth cost last month: none of that survives from the API. The addon copy is your own file
   and you keep it as long as you want.

The look, the current talents, sex, guild and Legacy points come from the
API, not from an addon: the shipper doesn't send sex or guild, and the server doesn't guess
Legacy points from levels and skills.

Talent history: [WoWSims Exporter](https://www.curseforge.com/wow/addons/wowsims-exporter)
(optional) saves your build by itself each time a talent changes, at every level, and the
shipper sends one event per change: the page then shows every change since you installed it.
It starts switched off: tick it in the AddOns list at character select. Without it, the page
shows the last 3 weeks' changes, read live from the exporter's own series in vm-wow (23 days);
nothing copies them. A talent the sim behind the addon doesn't know (a Forever-only one) isn't
in its saves: the page names it under the history instead of leaving it out.

## Without a Battle.net client

A self-hosted site without the exporter (see [Battle.net client](../self-host/battlenet.md))
has no talents, no talent-based spec hint for the gear advice, no sex, no
guild and no Legacy points (the page says "unknown"), and every 3D model wears its race's
default look (the 3D view lets you pick male or female). Everything else works from the
addons.

## Field by field

"API" means the exporter's read of Blizzard's profile API, every 2 hours (a new character within
about 5 minutes), kept 23 days. "Live" means
the exporter calls it on Forever today; "listed" means the endpoint exists for Classic but
nobody here has called it on Forever yet.

| What the page uses | Comes from today | Blizzard API has it? | Addon still needed because |
|---|---|---|---|
| Talents, spec hint by points | API (`/specializations`) | yes, live | - |
| The 3D look | API (`/appearance`) | yes, live | - |
| The paperdoll's picture | our renderer, from your game files, the API's look and your gear (never Blizzard's render) | yes (`/character-media`), not read | its renders sit on paths shared by character id across the Classic games: one showed another player's character |
| Armory item level | API (profile) | yes, live | - |
| Worn items | Syndicator; without its items (not installed, or a gear event with the professions only), the API's whole set; API for random-suffix stats | yes, live (`/equipment`) | history: gear per level, after 30 days |
| Item level (our average) | Syndicator + Polestar_ItemDB | yes, live (profile) | history |
| Stats of items newer than our reference (a dungeon added in a patch) | the game's hotfix cache, read by the shipper | no | no API |
| Level, class, race, faction | Polestar, Syndicator, Altoholic (level); each one they didn't send, the API's | yes, live (profile) | history: the level curve |
| Sex | API (profile `gender`) | yes, live | - |
| Guild | API (profile `guild`) | yes, listed | - |
| Level splits, time per level, XP | Polestar | no | no API |
| Sessions, zones, deaths, loot, flights, trainer visits | Polestar Recorder | no | no API |
| Dungeon runs | Polestar | no (statistics has counts at best) | no API |
| Profession ranks | Polestar | no | no API |
| Profession caps | Altoholic (`DataStore_Crafts`, the game's skill list, read at each login) and Polestar Recorder's skill records: the higher wins. Neither saw the trainer visit: the move says when each last looked, "if not done yet" | no | no API |
| Riding | Altoholic's spellbook (`DataStore_Spells`) and Polestar Recorder's skill and learn records: the highest rank wins. Past level 40 with nothing seeing riding: hedged, "if not trained yet" | no | no API |
| Recipes known | Altoholic | no | no API |
| Spells known, and the class's spells still to come with their level | Altoholic's spellbook; without it, an estimate from the Recorder's learn records (spells learned before it: unknown) | no | no API |
| Character sheet stats (base stats, attack power, crit, hit rating, spell power, mp5, dodge, parry, block), lifetime honorable kills | Altoholic | `/statistics`, `/pvp-summary`: not read | no resistances anywhere: Altoholic doesn't save them |
| Raid and dungeon lockouts, bosses killed, the weekly reset day | Altoholic | no | no API |
| Quests done | Polestar Recorder (hand-ins, the game's "done" flags at quest givers); Questie or Altoholic; a reward only one quest gives, worn or held on any gear or bags sheet ever sent; then what the API says you wear now (read live, never kept, only adds quests); and the quests a done one needed first | yes (`/quests/completed`), not read | the history is kept past the API terms' 30 days, so it holds only addon data |
| `/played` since level 1 | Altoholic | no | no API |
| Gold, bags, bank | Syndicator, Polestar; gold, XP and rested XP of every character at logout also from Altoholic | no | no API |
| The gear advice's spec | the gear page's pick, ForeverLevelingBiS, GearQuestForever; the talents only as a hint. Polestar's Stat weights, Adjust weights and smallest upgrade are no longer read | no (they're your settings) | no API |
| Gear scores | ForeverDB.net's stat weights per spec, in the mode picked on the gear page (Leveling, PvP, PvE), shipped in the image | no | - |
| Item stats | Forever's own client tables and hotfixes, shipped in the image | the API's item data is Classic's | - |
| Where an item comes from | ForeverDB.net's addon data first (drop rates, vendors, the reputation or PvP rank a source asks for; refreshed at most weekly), then Polestar's item table and forever-db's quest rewards, shipped in the image | no | - |
| Reputation standings (for reputation gear) | Polestar Recorder's reputation records and Altoholic's snapshot, newest per faction | yes, not read | - |
| PvP rank | not recorded: no addon the shipper reads saves it | `/pvp-summary`: not read | - |
| Auction prices | Auctionator scans | no (the project reads no Auction House API) | history: price trends |
| Legacy points earned | API (`/achievements`: each Legacy challenge is one) | yes, listed | - |
| Legacy points spent | not shown | no | no addon saves them either |
| Talent changes, every one | WoWSims Exporter, optional | no (your own file) | history: every change since you installed it |
| Talent changes, last 3 weeks (without WoWSims Exporter) | API (`/specializations`, each read in vm-wow) | yes, live | - |
| Reputations | Polestar Recorder (each change it saw); Altoholic (the standing of every faction, each character it saw logged in, the Recorder's or not) | yes (`/reputations`), not read | history: Altoholic's snapshots are your own data, kept like the rest |

The rule per kind: when an addon sent it, the addon's value is used and
the API's isn't, even when the exporter read one. When no addon did, the API's fills it. An
API value read 30 days ago or more is never shown, whatever a store still holds (Blizzard's
terms), and it never goes into VictoriaLogs, the analysis folders or a backup. Worn items come
as a whole set, never slot by slot: a Syndicator sheet with items lists every slot worn, so an
empty slot there is really empty. Professions have no Classic profile endpoint, so they stay
the addon's.

## The MCP

Your AI assistant gets the same current talents and talent changes (`talent_changes`) as the page: WoWSims Exporter's
saves when you have it, else the API's, which the MCP reads live from the same VictoriaMetrics (23 days) and keeps nothing.
The quests done follow the same rule: the worn items the API reports count only when the
switch below lets Battle.net data reach your AI. On a multi-user install the "Battle.net data
to my AI" switch in Settings decides, like for the `market` tool. A one-person install has no
such switch: whoever set `VM_URL` is the person.

## Not checked live yet

Guild and achievements are "listed": the endpoints exist for Classic, the exporter reads them,
and nobody has seen Forever's answer yet. If Forever's `/achievements` lists
nothing, the Legacy points read 0 of 65 until that's sorted out.
