# Recipe acquisition review, November 26, 2026

The original comparison for [#283](https://github.com/sam-dumont/wow-mcp/issues/283) flagged 119 craft spells. Trainer captures give a rank for 61. Polestar's guide gives a rank for two more. The client identifies 46 patterns and their reading ranks. Six slippers have both a trainer and a pattern. Across those 119 spells, 103 have a numeric rank for at least one learning method. Four others have a recorded trainer visit with no numeric rank. The remaining 12 have no verified learning method in these sources.

For Black Slippers (1257429), a tailor at 143 can read pattern 254008 at 140. Without the pattern or an accessible seller, the next recorded route is a trainer at 150. A tailor who already knows the recipe needs neither acquisition step. NPC 3363 offers all six slippers at 150 in the 71091 capture. The emulator's 145 has verified build zero. [Trainer capture][slippers] · [Emulator SQL][sql]

## Evidence

- The installed Forever client (1.60.1.71139) has all 119 spell IDs in SkillLineAbility. All have AcquireMethod 0. MinSkillLineRank is 1 on 111 and ranges from 40 to 135 on the other eight. Copper Chain Pants (2662) has 40 there and 15 in a recorded trainer service. Its TradeSkillCategory groups recipes. SpellLearnSpell has no matching teacher for these 119. TradeSkillItem describes items and character levels. None of those tables identifies the trainer's required profession rank. [WoWDBDefs table layouts](https://github.com/wowdev/WoWDBDefs/tree/master/definitions)
- A pattern must teach this exact spell through ItemXItemEffect and ItemEffect (trigger 6). ItemSparse.RequiredSkillRank is its reading rank. All 46 client joins agree with the shipped reference. Fourteen of those items have a valid ItemSparse record in the local 71139 hotfix cache. All fourteen agree.
- EG Link stores the trainer NPC, spell, profession and required skill in each recorded service. The eight 1.60.1 captures checked here are 70790, 70840, 70862, 70888, 70938, 70959, 71037 and 71091. The table links a matching NPC in the latest capture that contains that spell. The captures agree on all 61 numeric ranks. For 10548 they give 205, while Polestar's guide gives 210. For 9201 they give 160, while an earlier Polestar trainer check gave 170. The runtime uses the cited capture. [Data from Efficient Games (CC BY 4.0)](https://github.com/JIVESCORP/eg-link-output-wowf/tree/f25e0efde0c8490e4e6f356a68f7a81a703fa21c) · [Polestar trainer checks](https://github.com/fitzforge/polestar/blob/v0.4.3/rust/crates/forge-data/src/professions/extract.rs)
- ForeverDB's recipeInfo.tf records a trainer sighting. Its Classic fallback rank cannot establish the Forever rank. Nineteen of the 40 pattern-only spells have a recorded drop or faction vendor. The table gives one recorded source to check per pattern and one seller per faction. A sighting establishes where a player obtained an item. Its availability, a character's currency and their standing still need checking. The other 21 pattern-only spells have a verified reading rank and an unverified origin. The six slippers also lack a recorded origin for the pattern itself. [Data from ForeverDB.net](https://foreverdb.net) · [Item 14512, observed and Classic drop origins](https://foreverdb.net/item/14512)
- Each recipe name links the individual WoWForever.ninja page checked on November 26. The additional checks included the local trainer log formats, Polestar's guides, its profession observations and the pinned forever-db item sources and quest rewards. The local trainer log formats do not capture a required profession rank for each offer. Classic sources, unwitnessed emulator rows and community NPC lists without build-specific trainer evidence leave a rank unverified. [Polestar guide](https://github.com/fitzforge/polestar/tree/v0.4.3/guides/forever/professions) · [forever-db release](https://github.com/fitzforge/polestar/releases/tag/data-v2026.11.2) · [Jalane Ayrole's specialist list](https://azeroth.wiki/forever/map/stormwind-city/jalane-ayrole)

The shipped [learning evidence](../data/kb/professions/learning.json) records each observation's source. Its ForeverDB file records include full SHA-256 hashes. Recipes, Loot and Favor use snapshot 2026-11-26.a0032eb5. Items uses 2026-11-26.31fdc878. The [craft reference](../data/craft-reference.json) identifies the client build and the Polestar guide commit. [Source notices](../THIRD_PARTY.md)

## Spell dispositions

The trainer column gives the required skill, NPC ID and short build. The pattern column gives the item ID and the skill required to read it.

| Spell | Recipe | Trainer | Pattern · skill | Pattern source or unresolved learning method |
|---:|---|---|---|---|
| 2159 | [Fine Leather Cloak](https://wowforever.ninja/professions/leatherworking/2159-fine-leather-cloak/) | [50 · NPC 11098 · 71091][source1] | — | — |
| 2395 | [Barbaric Linen Vest](https://wowforever.ninja/professions/tailoring/2395-barbaric-linen-vest/) | [40 · NPC 3363 · 71091][source1] | — | — |
| 2662 | [Copper Chain Pants](https://wowforever.ninja/professions/blacksmithing/2662-copper-chain-pants/) | [15 · NPC 2836 · 71037][source2] | — | — |
| 2666 | [Runed Copper Belt](https://wowforever.ninja/professions/blacksmithing/2666-runed-copper-belt/) | [45 · NPC 2836 · 71037][source2] | — | — |
| 2668 | [Rough Bronze Leggings](https://wowforever.ninja/professions/blacksmithing/2668-rough-bronze-leggings/) | [80 · NPC 2836 · 71037][source2] | — | — |
| 2675 | [Shining Silver Breastplate](https://wowforever.ninja/professions/blacksmithing/2675-shining-silver-breastplate/) | [120 · NPC 2836 · 71037][source2] | — | — |
| 2739 | [Copper Shortsword](https://wowforever.ninja/professions/blacksmithing/2739-copper-shortsword/) | [25 · NPC 2836 · 71037][source2] | — | — |
| 2740 | [Bronze Mace](https://wowforever.ninja/professions/blacksmithing/2740-bronze-mace/) | [85 · NPC 2836 · 71037][source2] | — | — |
| 2741 | [Bronze Axe](https://wowforever.ninja/professions/blacksmithing/2741-bronze-axe/) | [90 · NPC 2836 · 71037][source2] | — | — |
| 2742 | [Bronze Shortsword](https://wowforever.ninja/professions/blacksmithing/2742-bronze-shortsword/) | [95 · NPC 2836 · 71037][source2] | — | — |
| 3292 | [Heavy Copper Broadsword](https://wowforever.ninja/professions/blacksmithing/3292-heavy-copper-broadsword/) | [70 · NPC 2836 · 71037][source2] | — | — |
| 3491 | [Big Bronze Knife](https://wowforever.ninja/professions/blacksmithing/3491-big-bronze-knife/) | [80 · NPC 2836 · 71037][source2] | — | — |
| 3515 | [Golden Scale Boots](https://wowforever.ninja/professions/blacksmithing/3515-golden-scale-boots/) | — | 3875 · 175 | Origin unverified |
| 3760 | [Hillman's Cloak](https://wowforever.ninja/professions/leatherworking/3760-hillmans-cloak/) | [125 · NPC 11098 · 71091][source1] | — | — |
| 3774 | [Green Leather Belt](https://wowforever.ninja/professions/leatherworking/3774-green-leather-belt/) | [135 · NPC 11098 · 71091][source1] | — | — |
| 3776 | [Green Leather Bracers](https://wowforever.ninja/professions/leatherworking/3776-green-leather-bracers/) | [155 · NPC 11098 · 71091][source1] | — | — |
| 3852 | [Gloves of Meditation](https://wowforever.ninja/professions/tailoring/3852-gloves-of-meditation/) | [105 · NPC 3363 · 71091][source1] | — | — |
| 3859 | [Azure Silk Vest](https://wowforever.ninja/professions/tailoring/3859-azure-silk-vest/) | [125 · NPC 3363 · 71091][source1] | — | — |
| 3861 | [Long Silken Cloak](https://wowforever.ninja/professions/tailoring/3861-long-silken-cloak/) | [150 · NPC 4576 · 70959][source3] | — | — |
| 6661 | [Barbaric Harness](https://wowforever.ninja/professions/leatherworking/6661-barbaric-harness/) | [165 · NPC 11098 · 71091][source1] | — | — |
| 6690 | [Lesser Wizard's Robe](https://wowforever.ninja/professions/tailoring/6690-lesser-wizards-robe/) | [110 · NPC 3363 · 71091][source1] | — | — |
| 7147 | [Guardian Pants](https://wowforever.ninja/professions/leatherworking/7147-guardian-pants/) | [135 · NPC 11098 · 71091][source1] | — | — |
| 7151 | [Barbaric Shoulders](https://wowforever.ninja/professions/leatherworking/7151-barbaric-shoulders/) | [150 · NPC 11098 · 71091][source1] | — | — |
| 7156 | [Guardian Gloves](https://wowforever.ninja/professions/leatherworking/7156-guardian-gloves/) | [165 · NPC 11098 · 71091][source1] | — | — |
| 8758 | [Azure Silk Pants](https://wowforever.ninja/professions/tailoring/8758-azure-silk-pants/) | [115 · NPC 3363 · 71091][source1] | — | — |
| 8760 | [Azure Silk Hood](https://wowforever.ninja/professions/tailoring/8760-azure-silk-hood/) | [120 · NPC 3363 · 71091][source1] | — | — |
| 8762 | [Silk Headband](https://wowforever.ninja/professions/tailoring/8762-silk-headband/) | [125 · NPC 4576 · 70959][source3] | — | — |
| 8766 | [Azure Silk Belt](https://wowforever.ninja/professions/tailoring/8766-azure-silk-belt/) | [140 · NPC 4576 · 70959][source3] | — | — |
| 8770 | [Robe of Power](https://wowforever.ninja/professions/tailoring/8770-robe-of-power/) | [155 · NPC 4576 · 70959][source3] | — | — |
| 8772 | [Crimson Silk Belt](https://wowforever.ninja/professions/tailoring/8772-crimson-silk-belt/) | [140 · NPC 4576 · 70959][source3] | — | — |
| 8774 | [Green Silken Shoulders](https://wowforever.ninja/professions/tailoring/8774-green-silken-shoulders/) | [145 · NPC 4576 · 70959][source3] | — | — |
| 8791 | [Crimson Silk Vest](https://wowforever.ninja/professions/tailoring/8791-crimson-silk-vest/) | [150 · NPC 4576 · 70959][source3] | — | — |
| 8799 | [Crimson Silk Pantaloons](https://wowforever.ninja/professions/tailoring/8799-crimson-silk-pantaloons/) | [160 · NPC 4576 · 70959][source3] | — | — |
| 8804 | [Crimson Silk Gloves](https://wowforever.ninja/professions/tailoring/8804-crimson-silk-gloves/) | [175 · NPC 4576 · 70959][source3] | — | — |
| 9196 | [Dusky Leather Armor](https://wowforever.ninja/professions/leatherworking/9196-dusky-leather-armor/) | [150 · NPC 11098 · 71091][source1] | — | — |
| 9198 | [Frost Leather Cloak](https://wowforever.ninja/professions/leatherworking/9198-frost-leather-cloak/) | [155 · NPC 11098 · 71091][source1] | — | — |
| 9201 | [Dusky Bracers](https://wowforever.ninja/professions/leatherworking/9201-dusky-bracers/) | [160 · NPC 11098 · 71091][source1] | — | — |
| 9206 | [Dusky Belt](https://wowforever.ninja/professions/leatherworking/9206-dusky-belt/) | [170 · NPC 11098 · 71091][source1] | — | — |
| 9926 | [Heavy Mithril Shoulder](https://wowforever.ninja/professions/blacksmithing/9926-heavy-mithril-shoulder/) | [180 · NPC 2836 · 71037][source2] | — | — |
| 9928 | [Heavy Mithril Gauntlet](https://wowforever.ninja/professions/blacksmithing/9928-heavy-mithril-gauntlet/) | [180 · NPC 2836 · 71037][source2] | — | — |
| 9935 | [Steel Plate Helm](https://wowforever.ninja/professions/blacksmithing/9935-steel-plate-helm/) | [190 · NPC 2836 · 71037][source2] | — | — |
| 9954 | [Truesilver Gauntlets](https://wowforever.ninja/professions/blacksmithing/9954-truesilver-gauntlets/) | — | — | Learning method and rank unverified |
| 9959 | [Heavy Mithril Breastplate](https://wowforever.ninja/professions/blacksmithing/9959-heavy-mithril-breastplate/) | [205 · NPC 2836 · 71037][source2] | — | — |
| 9974 | [Truesilver Breastplate](https://wowforever.ninja/professions/blacksmithing/9974-truesilver-breastplate/) | — | — | Learning method and rank unverified |
| 10499 | [Nightscape Tunic](https://wowforever.ninja/professions/leatherworking/10499-nightscape-tunic/) | [180 · NPC 11098 · 71091][source1] | — | — |
| 10507 | [Nightscape Headband](https://wowforever.ninja/professions/leatherworking/10507-nightscape-headband/) | [180 · NPC 11098 · 71091][source1] | — | — |
| 10511 | [Turtle Scale Breastplate](https://wowforever.ninja/professions/leatherworking/10511-turtle-scale-breastplate/) | [185 · NPC 11098 · 71091][source1] | — | — |
| 10518 | [Turtle Scale Bracers](https://wowforever.ninja/professions/leatherworking/10518-turtle-scale-bracers/) | [185 · NPC 11098 · 71091][source1] | — | — |
| 10548 | [Nightscape Pants](https://wowforever.ninja/professions/leatherworking/10548-nightscape-pants/) | [205 · NPC 11098 · 71091][source1] | — | — |
| 10552 | [Turtle Scale Helm](https://wowforever.ninja/professions/leatherworking/10552-turtle-scale-helm/) | [205 · NPC 11098 · 71091][source1] | — | — |
| 10556 | [Turtle Scale Leggings](https://wowforever.ninja/professions/leatherworking/10556-turtle-scale-leggings/) | [210 · NPC 11098 · 71091][source1] | — | — |
| 10558 | [Nightscape Boots](https://wowforever.ninja/professions/leatherworking/10558-nightscape-boots/) | [210 · NPC 11098 · 71091][source1] | — | — |
| 10619 | [Dragonscale Gauntlets](https://wowforever.ninja/professions/leatherworking/10619-dragonscale-gauntlets/) | — | — | Learning method and rank unverified |
| 10630 | [Gauntlets of the Sea](https://wowforever.ninja/professions/leatherworking/10630-gauntlets-of-the-sea/) | — | — | Learning method and rank unverified |
| 10632 | [Helm of Fire](https://wowforever.ninja/professions/leatherworking/10632-helm-of-fire/) | — | — | Learning method and rank unverified |
| 10647 | [Feathered Breastplate](https://wowforever.ninja/professions/leatherworking/10647-feathered-breastplate/) | — | — | Learning method and rank unverified |
| 12048 | [Black Mageweave Vest](https://wowforever.ninja/professions/tailoring/12048-black-mageweave-vest/) | [170 · NPC 4576 · 70959][source3] | — | — |
| 12049 | [Black Mageweave Leggings](https://wowforever.ninja/professions/tailoring/12049-black-mageweave-leggings/) | [170 · NPC 4576 · 70959][source3] | — | — |
| 12052 | [Shadoweave Pants](https://wowforever.ninja/professions/tailoring/12052-shadoweave-pants/) | — | — | Learning method and rank unverified |
| 12053 | [Black Mageweave Gloves](https://wowforever.ninja/professions/tailoring/12053-black-mageweave-gloves/) | [180 · NPC 4576 · 70959][source3] | — | — |
| 12055 | [Shadoweave Robe](https://wowforever.ninja/professions/tailoring/12055-shadoweave-robe/) | — | — | Learning method and rank unverified |
| 12067 | [Dreamweave Gloves](https://wowforever.ninja/professions/tailoring/12067-dreamweave-gloves/) | [190 · NPC 4576 · 70959][source3] | — | — |
| 12069 | [Cindercloth Robe](https://wowforever.ninja/professions/tailoring/12069-cindercloth-robe/) | [190 · NPC 4576 · 70959][source3] | — | — |
| 12070 | [Dreamweave Vest](https://wowforever.ninja/professions/tailoring/12070-dreamweave-vest/) | [190 · NPC 4576 · 70959][source3] | — | — |
| 12071 | [Shadoweave Gloves](https://wowforever.ninja/professions/tailoring/12071-shadoweave-gloves/) | — | — | Learning method and rank unverified |
| 12072 | [Black Mageweave Headband](https://wowforever.ninja/professions/tailoring/12072-black-mageweave-headband/) | [190 · guide][source4] | — | — |
| 12073 | [Black Mageweave Boots](https://wowforever.ninja/professions/tailoring/12073-black-mageweave-boots/) | [Seen · rank unknown][source5] | — | — |
| 12074 | [Black Mageweave Shoulders](https://wowforever.ninja/professions/tailoring/12074-black-mageweave-shoulders/) | [Seen · rank unknown][source5] | — | — |
| 12076 | [Shadoweave Shoulders](https://wowforever.ninja/professions/tailoring/12076-shadoweave-shoulders/) | — | — | Learning method and rank unverified |
| 12082 | [Shadoweave Boots](https://wowforever.ninja/professions/tailoring/12082-shadoweave-boots/) | — | — | Learning method and rank unverified |
| 12088 | [Cindercloth Boots](https://wowforever.ninja/professions/tailoring/12088-cindercloth-boots/) | [Seen · rank unknown][source5] | — | — |
| 12092 | [Dreamweave Circlet](https://wowforever.ninja/professions/tailoring/12092-dreamweave-circlet/) | [Seen · rank unknown][source5] | — | — |
| 15292 | [Dark Iron Pulverizer](https://wowforever.ninja/professions/blacksmithing/15292-dark-iron-pulverizer/) | — | 11610 · 240 | Origin unverified |
| 15294 | [Dark Iron Sunderer](https://wowforever.ninja/professions/blacksmithing/15294-dark-iron-sunderer/) | — | 11611 · 250 | Origin unverified |
| 15295 | [Dark Iron Shoulders](https://wowforever.ninja/professions/blacksmithing/15295-dark-iron-shoulders/) | — | 11615 · 255 | Origin unverified |
| 15296 | [Dark Iron Plate](https://wowforever.ninja/professions/blacksmithing/15296-dark-iron-plate/) | — | 11612 · 260 | [Ribbly Screwspigot][source6] |
| 16983 | [Serenity](https://wowforever.ninja/professions/blacksmithing/16983-serenity/) | — | 12827 · 260 | Origin unverified |
| 16985 | [Corruption](https://wowforever.ninja/professions/blacksmithing/16985-corruption/) | — | 12830 · 265 | Origin unverified |
| 18402 | [Runecloth Belt](https://wowforever.ninja/professions/tailoring/18402-runecloth-belt/) | [210 · guide][source4] | — | — |
| 18441 | [Ghostweave Pants](https://wowforever.ninja/professions/tailoring/18441-ghostweave-pants/) | — | 14495 · 245 | Origin unverified |
| 18456 | [Truefaith Vestments](https://wowforever.ninja/professions/tailoring/18456-truefaith-vestments/) | — | 14512 · 295 | [Grand Crusader Dathrohan][source7] |
| 18457 | [Robe of the Archmage](https://wowforever.ninja/professions/tailoring/18457-robe-of-the-archmage/) | — | 14513 · 295 | Origin unverified |
| 18458 | [Robe of the Void](https://wowforever.ninja/professions/tailoring/18458-robe-of-the-void/) | — | 14514 · 295 | Origin unverified |
| 19054 | [Red Dragonscale Breastplate](https://wowforever.ninja/professions/leatherworking/19054-red-dragonscale-breastplate/) | — | 15730 · 290 | Origin unverified |
| 19060 | [Green Dragonscale Leggings](https://wowforever.ninja/professions/leatherworking/19060-green-dragonscale-leggings/) | — | 15733 · 245 | Origin unverified |
| 19092 | [Wicked Leather Belt](https://wowforever.ninja/professions/leatherworking/19092-wicked-leather-belt/) | — | 15768 · 275 | [Stratholme creatures][source6] |
| 19094 | [Black Dragonscale Shoulders](https://wowforever.ninja/professions/leatherworking/19094-black-dragonscale-shoulders/) | — | 15770 · 270 | [Blackrock Depths creatures][source6] |
| 19098 | [Wicked Leather Armor](https://wowforever.ninja/professions/leatherworking/19098-wicked-leather-armor/) | — | 15773 · 280 | Origin unverified |
| 19107 | [Black Dragonscale Leggings](https://wowforever.ninja/professions/leatherworking/19107-black-dragonscale-leggings/) | — | 15781 · 290 | Origin unverified |
| 22866 | [Belt of the Archmage](https://wowforever.ninja/professions/tailoring/22866-belt-of-the-archmage/) | — | 18414 · 295 | Origin unverified |
| 22867 | [Felcloth Gloves](https://wowforever.ninja/professions/tailoring/22867-felcloth-gloves/) | — | 18415 · 280 | Origin unverified |
| 22868 | [Inferno Gloves](https://wowforever.ninja/professions/tailoring/22868-inferno-gloves/) | — | 18416 · 280 | Origin unverified |
| 22869 | [Mooncloth Gloves](https://wowforever.ninja/professions/tailoring/22869-mooncloth-gloves/) | — | 18417 · 280 | Origin unverified |
| 22870 | [Cloak of Warding](https://wowforever.ninja/professions/tailoring/22870-cloak-of-warding/) | — | 18418 · 280 | Origin unverified |
| 22921 | [Girdle of Insight](https://wowforever.ninja/professions/leatherworking/22921-girdle-of-insight/) | — | 18514 · 290 | Origin unverified |
| 22922 | [Mongoose Boots](https://wowforever.ninja/professions/leatherworking/22922-mongoose-boots/) | — | 18515 · 290 | Origin unverified |
| 22923 | [Swift Flight Bracers](https://wowforever.ninja/professions/leatherworking/22923-swift-flight-bracers/) | — | 18516 · 290 | Origin unverified |
| 24655 | [Green Dragonscale Gauntlets](https://wowforever.ninja/professions/leatherworking/24655-green-dragonscale-gauntlets/) | — | — | Learning method and rank unverified |
| 1252237 | [Veteran's Chain Shirt](https://wowforever.ninja/professions/blacksmithing/1252237-veterans-chain-shirt/) | — | 251340 · 80 | [Rhahk'Zor][source8] |
| 1252239 | [Protector's Chain Shirt](https://wowforever.ninja/professions/blacksmithing/1252239-protectors-chain-shirt/) | — | 251342 · 80 | [Oggleflint][source6] |
| 1252240 | [Acolyte's Chain Shirt](https://wowforever.ninja/professions/blacksmithing/1252240-acolytes-chain-shirt/) | — | 251343 · 80 | [Cookie][source6] |
| 1252241 | [Crusader's Chain Shirt](https://wowforever.ninja/professions/blacksmithing/1252241-crusaders-chain-shirt/) | — | 251344 · 80 | [Rethilgore][source9] |
| 1252242 | [Veteran's Chain Leggings](https://wowforever.ninja/professions/blacksmithing/1252242-veterans-chain-leggings/) | — | 251345 · 95 | [Lord Pythas][source6] |
| 1252243 | [Guard's Chain Leggings](https://wowforever.ninja/professions/blacksmithing/1252243-guards-chain-leggings/) | — | 251346 · 95 | [Lady Sarevess][source6] |
| 1252244 | [Protector's Chain Leggings](https://wowforever.ninja/professions/blacksmithing/1252244-protectors-chain-leggings/) | — | 251347 · 95 | [Razorclaw the Butcher][source6] |
| 1252245 | [Acolyte's Chain Leggings](https://wowforever.ninja/professions/blacksmithing/1252245-acolytes-chain-leggings/) | — | 251348 · 95 | [Lord Pythas][source6] |
| 1252246 | [Crusader's Chain Leggings](https://wowforever.ninja/professions/blacksmithing/1252246-crusaders-chain-leggings/) | — | 251349 · 95 | [Skum][source9] |
| 1252247 | [Veteran's Chain Helm](https://wowforever.ninja/professions/blacksmithing/1252247-veterans-chain-helm/) | — | 251350 · 125 | [Overlord Ramtusk][source10] |
| 1252248 | [Guard's Chain Helm](https://wowforever.ninja/professions/blacksmithing/1252248-guards-chain-helm/) | — | 251351 · 125 | [Old Serra'kis][source6] |
| 1252249 | [Protector's Chain Helm](https://wowforever.ninja/professions/blacksmithing/1252249-protectors-chain-helm/) | — | 251352 · 125 | [Twilight Lord Kelris][source6] |
| 1252250 | [Acolyte's Chain Helm](https://wowforever.ninja/professions/blacksmithing/1252250-acolytes-chain-helm/) | — | 251353 · 125 | [Charlga Razorflank][source11] |
| 1252251 | [Crusader's Chain Helm](https://wowforever.ninja/professions/blacksmithing/1252251-crusaders-chain-helm/) | — | 251354 · 125 | [Agathelos the Raging][source6] |
| 1252323 | [Prefect's Waistguard](https://wowforever.ninja/professions/blacksmithing/1252323-prefects-waistguard/) | — | 251426 · 250 | [Gor'mak][source12] / [Stondry Darkhammer][source13] · 90 Favor |
| 1257426 | [Gilded Slippers](https://wowforever.ninja/professions/tailoring/1257426-gilded-slippers/) | [150 · NPC 3363 · 71091][source1] | 254002 · 140 | Origin unverified |
| 1257427 | [Frothing Slippers](https://wowforever.ninja/professions/tailoring/1257427-frothing-slippers/) | [150 · NPC 3363 · 71091][source1] | 254004 · 140 | Origin unverified |
| 1257428 | [Fiery Slippers](https://wowforever.ninja/professions/tailoring/1257428-fiery-slippers/) | [150 · NPC 3363 · 71091][source1] | 254006 · 140 | Origin unverified |
| 1257429 | [Black Slippers](https://wowforever.ninja/professions/tailoring/1257429-black-slippers/) | [150 · NPC 3363 · 71091][source1] | 254008 · 140 | Origin unverified |
| 1257430 | [Golden Slippers](https://wowforever.ninja/professions/tailoring/1257430-golden-slippers/) | [150 · NPC 3363 · 71091][source1] | 254010 · 140 | Origin unverified |
| 1257431 | [Radiant Slippers](https://wowforever.ninja/professions/tailoring/1257431-radiant-slippers/) | [150 · NPC 3363 · 71091][source1] | 254012 · 140 | Origin unverified |

## Installed addon comparison

Rerunning `tools/craft_reference/audit.py` on November 26 with the installed addon and this checkout matched 450 spells and flagged 120 raw gear-record differences. This comparison adds two spells to the original cohort. Radiant Slippers (1257431), covered above, now agrees. Both additions have an offer recorded at NPC 2836 in build 1.60.1.71037. Mithril Plate Pants (9931) requires Blacksmithing 185. Heavy Mithril Boots (9968) requires 210, matching the Polestar guide. [Trainer capture](https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.71037/trainers.json) · [Mithril Plate Pants](https://wowforever.ninja/professions/blacksmithing/9931-mithril-scale-pants/) · [Heavy Mithril Boots](https://wowforever.ninja/professions/blacksmithing/9968-heavy-mithril-boots/)

[source1]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.71091/trainers.json
[source2]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.71037/trainers.json
[source3]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.70959/trainers.json
[source4]: https://github.com/fitzforge/polestar/blob/3cf9e32bb38b8d73164acea249039d010e03d2c5/addon/Polestar_Guides/Guides.lua
[source5]: https://foreverdb.net/downloads/addon-data/Recipes.lua?v=91d4902752
[source6]: https://foreverdb.net/downloads/addon-data/Loot.lua?v=d905c2bd95
[source7]: https://foreverdb.net/downloads/addon-data/Items.lua?v=b5b77c572c
[source8]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.70862/loot.json
[source9]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.70790/loot.json
[source10]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.70888/loot.json
[source11]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.70938/loot.json
[source12]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.71037/vendors.json
[source13]: https://foreverdb.net/downloads/addon-data/Favor.lua?v=824a6094ee
[slippers]: https://github.com/JIVESCORP/eg-link-output-wowf/blob/f25e0efde0c8490e4e6f356a68f7a81a703fa21c/data/wowf/1.60.1.71091/trainers.json
[sql]: https://github.com/advocaite/TrinityCore/blob/25be61f58f48b6e7bc8eda1146af8e1c73c82470/sql/custom/world/2026_11_05_06_world_forever_profession_trainer_recipes.sql
