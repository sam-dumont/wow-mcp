# Where the 31 missing gear sources went

The November 26 review recovered 27 of the 30 dropped items in [issue 333](https://github.com/sam-dumont/wow-mcp/issues/333#issuecomment-7188711928). Two come from summons. Three still lack a verified route. The 31st item is a cloak the player must hand back.

The exporter uses these rows only when the normal sources have no route. A loot window establishes that the item dropped. It supplies no reliable chance per kill or dungeon clear here. Every recovered drop stays hard, and the planner skips it as a next action. A real auction listing goes through the usual price, item and character checks.

| Item | Route carried into gear advice | Evidence checked |
| --- | --- | --- |
| 5387 Enchanted Moonstalker Cloak | Quest supply. Keep it for Volcor. | The [live quest cache][quest-cache] has 5387 as both the start item and the turn-in objective of quest 993. It lists no item reward. Holding the cloak cannot establish completion. |
| 6593 Battleforge Cloak | Highland Tortoise, Wetlands | EG Link recorded 260809 in a group loot window on [build 70862][eg-70862]. |
| 9428 Unearthed Bands | Shadowforge Surveyor, Badlands | Questie's NPC 4844 also has open-world spawns in forever-db. The [Forever observations](https://foreverchanges.pro/item/9428/unearthed-bands) and [independent item record](https://db.icy-veins.com/wow-forever/items/9428) agree on the creature. |
| 9455 Emissary Cuffs | Dark Iron Ambassador, Gnomeregan | Two Polestar voters and the [Ambassador's Forever drop table](https://db.icy-veins.com/wow-forever/npcs/6228) agree on 6228. |
| 10131 Revenant Boots | Bloodhound, Blackrock Depths | Polestar recorded 8921. The [item record](https://db.icy-veins.com/wow-forever/items/10131) names the same NPC and [players saw the boots in the dungeon](https://foreverchanges.pro/item/10131/revenant-boots). |
| 10238 Heavy Lamellar Boots | Random drop seen in Sunken Temple | One Polestar voter saw them on an Atal'ai Deathwalker. The [separate loot log](https://foreverchanges.pro/item/10238/heavy-lamellar-boots) confirms the instance. The fallback names only the location. |
| 10243 Heavy Lamellar Girdle | World drop | Polestar recorded multiple creatures, including Black Dragonspawn. [Live world drops](https://foreverchanges.pro/item/10243/heavy-lamellar-girdle) confirm the route. No preferred mob. |
| 10387 Hyperion Girdle | Overlord Wyrmthalak, Lower Blackrock Spire | Earlier ForeverDB.net and Polestar both recorded 9568. The [current NPC record](https://db.icy-veins.com/wow-forever/npcs/9568) still lists the girdle. |
| 12582 Keris of Zul'Serak | War Master Voone, Lower Blackrock Spire | Earlier ForeverDB.net and two Polestar voters agree with the [current item record](https://db.icy-veins.com/wow-forever/items/12582). |
| 13051 Witchfury | Barbed Lasher, Maraudon | One Polestar voter recorded 12219. The [current item record](https://db.icy-veins.com/wow-forever/items/13051) confirms that pair. |
| 13164 Heart of the Scale | No verified current route | The old upper-wing row is wrong. Wyrmthalak is in the lower wing. The [ForeverChanges source](https://foreverchanges.pro/item/13164/heart-of-the-scale) labels the drop Classic. [Wowhead](https://www.wowhead.com/forever/item=13164) lists the boss. The [current NPC loot record](https://db.icy-veins.com/wow-forever/npcs/9568) includes a different trinket, Heart of Wyrmthalak (22321). No reviewed live window identifies 13164. |
| 13206 Wolfshear Leggings | Gizrul the Slavener, Lower Blackrock Spire | Earlier ForeverDB.net, Polestar and the [current item record](https://db.icy-veins.com/wow-forever/items/13206) agree on 10268. |
| 13258 Slaghide Gauntlets | Urok Doomhowl, when carrying the Roughshod Pike and Omokk's Head | Earlier ForeverDB.net, Polestar and the [current item record](https://db.icy-veins.com/wow-forever/items/13258) agree on 10584. Both items are used at [Urok's Tribute Pile](https://questing.wiki/wow-forever/quests/4867/). |
| 13953 Silent Fang | Darkmaster Gandling, Scholomance | Earlier ForeverDB.net and the [current item record](https://db.icy-veins.com/wow-forever/items/13953) agree on 1853. [Loot windows](https://foreverchanges.pro/item/13953/silent-fang) place the sword in Scholomance. |
| 14179 Watcher's Cape | Blackfathom Sea Witch, Blackfathom Deeps | EG Link recorded 4805 in group loot on [build 70862][eg-70862]. Two Polestar voters recorded the same pair. |
| 14425 Silksand Wraps | Scarab, Zul'Farrak | EG Link recorded 7269 in group loot on [build 70938][eg-70938]. |
| 14434 Windchaser Robes | Alcaz Jailer, Alcaz Prison | Earlier ForeverDB.net recorded the NPC ID. The released forever-db names it, the [current NPC table](https://db.icy-veins.com/wow-forever/npcs/255377) lists the robes, and the [Forever observations](https://foreverchanges.pro/item/14434/windchaser-robes) include the prison. |
| 14444 Venomshroud Leggings | No verified current route | Princess Theradras was a Classic lead. The [current item page](https://foreverchanges.pro/item/14444/venomshroud-leggings) gives no observed route. |
| 14487 Bonechill Hammer | Ras Frostwhisper, Scholomance | [Current named NPC record](https://db.icy-veins.com/wow-forever/items/14487) and [observed instance](https://foreverchanges.pro/item/14487/bonechill-hammer). The old Classic percentage stays out. |
| 14756 Slayer's Slippers | Highperch Wyvern, Thousand Needles | Polestar's 4107 also appears in the [item record](https://db.icy-veins.com/wow-forever/items/14756) and the [live loot windows](https://foreverchanges.pro/item/14756/slayers-slippers). |
| 14833 Tyrant's Gauntlets | Sandfury Blood Drinker, Zul'Farrak | Earlier ForeverDB.net and Polestar recorded 5649, also in the [current item record](https://db.icy-veins.com/wow-forever/items/14833). |
| 14919 Jade Circlet | Tar Creeper, Un'Goro Crater | EG Link recorded 6527 on [build 71037][eg-71037]. The loot window has quest context. The circlet itself has no quest-only flag. |
| 14922 Lofty Sabatons | Frenzied Pterrordax, Un'Goro Crater | Earlier ForeverDB.net and the [current item record](https://db.icy-veins.com/wow-forever/items/14922) agree on 9167. |
| 14952 Bloodforged Helmet | Alcaz Jailer, Alcaz Prison | Polestar's one voter saw 255377. The [current NPC record](https://db.icy-veins.com/wow-forever/npcs/255377) lists this helmet. |
| 14957 High Chief's Sabatons | World drop | Polestar saw a Gorishi Wasp drop. [Separate live observations](https://foreverchanges.pro/item/14957/high-chiefs-sabatons) confirm an outdoor drop on a different creature. No preferred mob. |
| 14960 High Chief's Belt | Pterrordax, Un'Goro Crater | Polestar's 9166 also appears in the [current item record](https://db.icy-veins.com/wow-forever/items/14960). |
| 15255 Gallant Flamberge | Firebrand Invoker, Blackrock Spire | Earlier ForeverDB.net and the [current item record](https://db.icy-veins.com/wow-forever/items/15255) agree on 9262. |
| 18374 Flamescarred Shoulders | Magister Kalendris, Dire Maul West | Earlier ForeverDB.net and the [current item record](https://db.icy-veins.com/wow-forever/items/18374) agree on 11487. |
| 18462 Jagged Bone Fist | Guard Mol'dar, Dire Maul North | Earlier ForeverDB.net distinguished this sighting from the Classic odds on Guard Fengus. The [current NPC record](https://db.icy-veins.com/wow-forever/npcs/14326) includes the fist. |
| 18760 Necromantic Band | Death Knight Darkreaver, when carrying the Divination Scryer | The [current item record](https://db.icy-veins.com/wow-forever/items/18760) names 14516, and [players saw the band in Scholomance](https://foreverchanges.pro/item/18760/necromantic-band). The [summon](https://questing.wiki/wow-forever/quests/7647/) requires the scryer in the Great Ossuary. Another group member can summon, but the character sheet cannot verify their bags. |
| 22206 Bouquet of Red Roses | Seasonal lead only | ForeverTDB's [holiday-condition rows][tdb-holiday] come from VMaNGOS 1.12. No reviewed Forever loot window establishes a live date or boss. |

## How the sources were checked

- The [issue's original 31 IDs](https://github.com/sam-dumont/wow-mcp/issues/333#issuecomment-7188711928) were matched against all eight post-launch `wowf` loot files in the [EG Link revision][eg]. They cover builds 70790 through 71091. Four items had a positive creature loot window. The [DropTables aggregation][drop-tables] imports EG Link and Everloot, so an EG Link row there counts once. Group loot exists in the original EG Link files even when the aggregation omits it.
- [Polestar v0.4.3][polestar] had below-threshold NPC sightings for 20 of the 31. A single voter did not establish a named farming target. [ForeverChanges](https://foreverchanges.pro/about) distinguishes live loot from its Classic AtlasLoot entries. Its location-only sightings confirm a location. [Icy Veins](https://db.icy-veins.com/wow-forever/) was checked for the item and NPC together. Its rates have no role in the fallback. Archived [ForeverDB.net addon rows][old-fdb] were used only when corroborated. Data from ForeverDB.net (https://foreverdb.net).
- All 31 raw [QuestieDB Forever rows][questie] matched the corresponding Classic rows byte for byte. None appeared in its [November 25 item-trace corrections][traces]. Two generated delta rows changed loot candidates, but these do not document a new player sighting. The [WoWForever.ninja world sitemap][ninja] published no item page for these IDs. The site identifies its [original loot tables](https://wowforever.ninja/about/data/) as Classic.
- [ForeverTDB][tdb] and its [Forever fork][fork] are reconstructions with Classic imports, live captures and authored fixes. The targeted ForeverTDB loot patches did not supply a new live item/NPC pair for these IDs. Numeric matches on NPC IDs and coordinates were disregarded. `creature_loot_template` in the VMaNGOS import is Classic evidence.

[quest-cache]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.70862/quests.enUS.json
[eg]: https://github.com/JIVESCORP/eg-link-output-wowf/tree/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf
[eg-70862]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.70862/loot.json
[eg-70938]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.70938/loot.json
[eg-71037]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.71037/loot.json
[drop-tables]: https://github.com/valcheffnet/DropTables/tree/e2c6bf3b418f064fdcecbd23e1c20b70095f07e2/data
[polestar]: https://github.com/fitzforge/polestar/blob/v0.4.3/data/codex/recorded.json
[old-fdb]: https://github.com/sam-dumont/wow-mcp/blob/6a1f768b5aaa6fc709a287a528211442e42210c3/data/foreverdb/item-sources.json
[questie]: https://github.com/Questie/QuestieDB/blob/6c9c71280e1295e96985825bcc49c79d470f01ea/data/Forever/foreverItemDB.lua
[traces]: https://github.com/Questie/QuestieDB/blob/6c9c71280e1295e96985825bcc49c79d470f01ea/src/corrections/Forever/traces/foreverItemTraces.lua
[ninja]: https://wowforever.ninja/sitemap-world-0.xml
[tdb]: https://github.com/advocaite/TrinityCore/tree/25be61f58f48b6e7bc8eda1146af8e1c73c82470/sql/custom/world
[fork]: https://github.com/Sakhwoow/WoWForever/tree/b54d56666fdcc9a804ab286d2c1b5de14cddf520/sql/custom/world
[tdb-holiday]: https://github.com/advocaite/TrinityCore/blob/25be61f58f48b6e7bc8eda1146af8e1c73c82470/sql/custom/world/2026_10_11_17_world_classic_loot_conditions.sql
