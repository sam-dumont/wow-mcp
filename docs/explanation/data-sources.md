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

Profession caps, the look, the current talents, sex, guild and Legacy points come from the
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
has no talents, no talent-based spec hint for the gear advice, no armory render, no sex, no
guild and no Legacy points (the page says "unknown"), and every 3D model wears its race's
default look (the 3D view lets you pick male or female). Everything else works from the
addons.

## Field by field

"API" means the exporter's read of Blizzard's profile API, every 2 hours (a new character within
about 10 minutes), kept 23 days. "Live" means
the exporter calls it on Forever today; "listed" means the endpoint exists for Classic but
nobody here has called it on Forever yet.

| What the page uses | Comes from today | Blizzard API has it? | Addon still needed because |
|---|---|---|---|
| Talents, spec hint by points | API (`/specializations`) | yes, live | - |
| The 3D look | API (`/appearance`) | yes, live | - |
| Armory render, armory item level | API (`/character-media`, profile) | yes, live | - |
| Worn items | Syndicator; without its items (not installed, or a gear event with the professions only), the API's whole set; API for random-suffix stats | yes, live (`/equipment`) | history: gear per level, after 30 days |
| Item level (our average) | Syndicator + Polestar_ItemDB | yes, live (profile) | history |
| Level, class, race, faction | Polestar, Syndicator; each one they didn't send, the API's | yes, live (profile) | history: the level curve |
| Sex | API (profile `gender`) | yes, live | - |
| Guild | API (profile `guild`) | yes, listed | - |
| Level splits, time per level, XP | Polestar | no | no API |
| Sessions, zones, deaths, loot, flights, trainer visits | Polestar Recorder | no | no API |
| Dungeon runs | Polestar | no (statistics has counts at best) | no API |
| Profession ranks | Polestar | no | no API |
| Profession caps | Polestar Recorder skill records, else trainer tiers | no | no API |
| Recipes known | Altoholic | no | no API |
| Quests done before Polestar | Questie or Altoholic | no | no API |
| `/played` since level 1 | Altoholic | no | no API |
| Gold, bags, bank | Syndicator, Polestar | no | no API |
| Spec pick, stat weights, tracked upgrades (the gear advice's spec: these first, the talents only as a hint) | Polestar, ForeverLevelingBiS, GearQuestForever | no (they're your settings) | no API |
| Auction prices | Auctionator scans | no (the project reads no Auction House API) | history: price trends |
| Legacy points earned | API (`/achievements`: each Legacy challenge is one) | yes, listed | - |
| Legacy points spent | not shown | no | no addon saves them either |
| Talent changes, every one | WoWSims Exporter, optional | no (your own file) | history: every change since you installed it |
| Talent changes, last 3 weeks (without WoWSims Exporter) | API (`/specializations`, each read in vm-wow) | yes, live | - |
| Reputations | not used | yes, listed | - |

The rule per kind (owner, 2026-11-20): when an addon sent it, the addon's value is used and
the API's isn't, even when the exporter read one. When no addon did, the API's fills it. An
API value read 30 days ago or more is never shown, whatever a store still holds (Blizzard's
terms), and it never goes into VictoriaLogs, the analysis folders or a backup. Worn items come
as a whole set, never slot by slot: a Syndicator sheet with items lists every slot worn, so an
empty slot there is really empty. Professions have no Classic profile endpoint, so they stay
the addon's.

## The MCP

Your AI assistant gets the same current talents and talent changes (`talent_changes`) as the page: WoWSims Exporter's
saves when you have it, else the API's, which the MCP reads live from the same VictoriaMetrics (23 days) and keeps nothing. On a multi-user install the "Battle.net data
to my AI" switch in Settings decides, like for the `market` tool. A one-person install has no
such switch: whoever set `VM_URL` is the person.

## Not checked live yet

Guild and achievements are "listed": the endpoints exist for Classic, the exporter reads them
since 2026-11-20, and nobody has seen Forever's answer yet. If Forever's `/achievements` lists
nothing, the Legacy points read 0 of 65 until that's sorted out.
