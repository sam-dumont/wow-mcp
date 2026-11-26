# Third-party material

wow-stats is GPL-3.0-only (`LICENSE`). It ships these parts made by others.

| Component | Used for | Licence | Where |
|---|---|---|---|
| [Polestar](https://github.com/fitzforge/polestar) by fitzforge and contributors | Route times (its route planner on its guides); gear advice (where gear comes from, and the class rules: who wears what, from `Gear/Weights.lua`, plus the class masks the client leaves open; none of its weights, and its item stats aren't read since 2026-11-24); class and profession trainers, what each trains and its cost (`Core/ClassTrainers.lua`, `QoL/ClassSpells.lua`, `QoL/Riding.lua`, `Polestar_Guides/Guides.lua`: Forever's trainer lists, from ForeverTDB, used as facts); the profession guides' routes, the recipes they teach and buy with their prices, and the vendor goods' prices, for the skill-up costs (`Polestar_Guides/Guides.lua`); each quest's usual entry level (`data/codex/levels.json`, and `recorded.json`: the 25th percentile of players' recorded entry levels); which dungeon a quest belongs to (its cited dungeon journal `data/codex/journal-quests.json`, its dungeon runs `data/dungeon-runs.json`, the dungeons' map ids in `Dungeons/Art.lua`) | GPL-3.0-only | `data/route-reference.json`, `data/dungeons/quests.json`, `data/gear-reference.json`, `data/class-reference.json`, `data/trainers-reference.json`, `data/craft-reference.json`, `data/forever-reference.json` (level floors), `wowstats/gear.py`; `docs/route-reference.md`, `docs/gear-reference.md` |
| forever-db, Polestar's quest database (Polestar v0.4.2.2, built from its own sources: notice below) | Quests, NPCs and items behind the route times; every quest's and item's name, quest level and zone, so the page never shows a bare id; the gear rewards of every quest (`gear_quests`), where gear comes from when Polestar's sources don't say. Polestar publishes it on its own releases (`data-v*`, ahead of the addon's); we read it at the release in `tools/forever-db-tag` (2026.11.2, client build 1.60.1.70888), its zip checked against the release's `SHA256SUMS`. The route times still read the copy Polestar's addon tag carries | GPL-3.0, with CC BY 4.0 and MIT parts, notice below | `data/route-reference.json`, `data/forever-reference.json` (`quests`, `items`), `data/dungeons/wings.json` (quest objectives and item sources behind each quest's wing), `data/dungeons/quests.json` (each quest's zone: the dungeon it belongs to); `docs/forever-reference.md` |
| Forever client tables AreaTable, Map, GroupFinderActivity via wago.tools | Zone names, which areas are dungeons, a dungeon's level range | Game data © Blizzard Entertainment, used as facts | `data/forever-reference.json` (`areas`) |
| Forever client tables Item, ItemModifiedAppearance, ItemAppearance via wago.tools, read at run time | Which icon an item has, including Forever's new items whose icon is only on their appearance | Game data © Blizzard Entertainment, used as facts | `wowstats/itemcard.py` (cache only, nothing committed) |
| Item, spell and talent icons from wow.zamimg.com (Wowhead's image host), fetched at run time | The icons on the pages | Blizzard's art, served by Wowhead; not shipped, no licence of ours covers them | `wowstats/itemcard.py`, `wowstats/talents.py` (cache only) |
| [ForeverLevelingBiS](https://www.curseforge.com/wow/addons/forever-leveling-bis) **v0.12.0-beta** by Thunder (data 2026-10-13), as Polestar generated it into `Polestar_ItemDB/Forever.lua` | Where leveling gear comes from (quests, drops, crafts) | MIT (Copyright (c) 2026 Thunder), notice below | `data/gear-reference.json` (`items`, `sources`, `quests`) |
| [ForeverDB.net](https://foreverdb.net), the ForeverDB addon's data files (`Data/GearSources.lua`, `Loot.lua`, `GearFinder.lua`, `Meta.lua`), from its manifest | Where each piece of gear comes from (quests, vendors, drops with players' measured chances, rares, crafts, reputation, PvP, containers), each labelled seen in Forever, Forever client, Classic data or clue; the stat weights per class and spec with its Leveling and PvP tables, scoring formula and set bonus shares. Credited with a link wherever a score ("Weights: ForeverDB.net") or a source ("Source: ForeverDB.net") shows. **Data from ForeverDB.net (https://foreverdb.net)** | GPL-3.0 with an attribution term (section 7(b)): keep the files' notice line and show the line above; its `LICENSE.txt`, `NOTICE.txt` and `COPYING.txt` ship byte for byte in `data/foreverdb/`. Its Classic-labelled entries come from CMaNGOS classic-db (GPL-3.0), next row | `data/foreverdb/item-sources.json` (`tools/foreverdb_sources/export.py`), `data/foreverdb-weights.json` (`tools/foreverdb_weights/export.py`), `tests/data/foreverdb-ladders.json.gz` and `foreverdb-picks.json` (test fixtures), `wowstats/gear.py`; `docs/gear-reference.md` |
| [CMaNGOS classic-db](https://github.com/cmangos/classic-db), Copyright (C) the CMaNGOS project and its contributors, through ForeverDB's data | Item sources labelled Classic data (`k`, `c`) in `item-sources.json`: drop chances and sources from Classic 1.12, said so wherever they show | GPL-3.0 | `data/foreverdb/item-sources.json` |
| [WoWSims Exporter](https://github.com/wowsims/exporter) by General Wrex and the wowsims team (commit ce873f7d5aed) | How to read its saved talent string: each class's three trees of TraitDefinition IDs in the sim's order (`Flavors/Forever/Talents.lua`, `tools/wowsims_talents.py`); the shape of its SavedVariables, which the shipper reads | MIT (Copyright (c) 2022 General Wrex), notice below | `data/wowsims-talents.json`, `wowstats/wowsims.py`, `shipper/wow_stats.py` (`wowsims_saves`) |
| Forever client tables ItemSparse, Item, RandPropPoints, ItemDamageOneHand, ItemDamageOneHandCaster, ItemDamageTwoHand, ItemDamageTwoHandCaster, ItemDamageRanged, ItemDamageThrown, ItemDamageWand, ItemArmorTotal, ArmorLocation, ItemArmorQuality, ItemArmorShield, ItemEffect, ItemXItemEffect, SpellEffect, SpellEquippedItems via wago.tools (CSV exports, build 1.60.1.71037: `tools/game_items/export.py`) | Every weapon's and armour piece's stats, item level, required level, slot and class mask, for the gear advice | Game data © Blizzard Entertainment, used as facts | `data/game-items.json` |
| Forever client tables SkillLineAbility, SpellReagents, SpellEffect, SpellName, ItemEffect, ItemXItemEffect, ItemSparse via wago.tools (CSV exports, the build of `data/game-items.json`: `tools/craft_reference/export.py`) | Every crafting recipe's skill colours, reagents and the item it makes, which recipe item teaches which recipe, which crafted items bind when picked up | Game data © Blizzard Entertainment, used as facts | `data/craft-reference.json` |
| Blizzard client data from the maintainer's own Forever client: the ItemSparse and Item rows of its hotfix cache (`Cache/ADB/enUS/DBCache.bin`, same build) | The same, for the 6,411 items Forever sends as hotfixes (its own and re-itemised items) | Game data © Blizzard Entertainment, used as facts | `data/game-items.json` (`from: "hotfix"`) |
| Classic Era client tables ItemSparse, Item, ItemEffect, ItemXItemEffect, SpellEffect via wago.tools (build 1.15.9.70576) | The same, for 4,867 items Forever has nowhere (old PvP and raid gear) | Game data © Blizzard Entertainment, used as facts | `data/game-items.json` (`from: "era"`) |
| Blizzard client data from each player's own Forever client: the ItemSparse and Item rows of its hotfix cache (`Cache/ADB/<locale>/DBCache.bin`) and its RandPropPoints table, sent by that player's shipper, read at run time | Name, slot, item level, stats and icon of the weapons and armour no data above has yet (a new dungeon's drops) | Game data © Blizzard Entertainment, used as facts | not shipped: kept per person on their server (`wowstats/itemfacts.py`) |
| Polestar's forever-db, guide notes, dungeon runs, route model notes and rested constants | The play analysis' knowledge base (quests, NPCs, items, levelling notes, quoted) | GPL-3.0 | `data/kb-reference.json.gz` (`tools/kb/export.py`) |
| Guides, wikis, news and Blizzard's own pages and forums (about 800 pages, each statement linking its own) | The knowledge base's rules and statements: one fact each, in our own words or as an aggregate, never the page's text (`docs/kb.md`, "Sources and licensing") | Our text, GPL-3.0-only; the facts and the pages stay their authors' | `data/kb/` |
| Thread titles and links from the Blizzard forums' Forever discussion (US and EU, Discourse JSON) and r/wowforever (found through Anthropic's web search, never read from Reddit), read by the weekly KB pass | What players are saying: the title as a citation with its link, reply and like counts, and one line in our own words | The titles stay their authors'; our lines GPL-3.0-only | `data/kb/community.json` (`tools/kb/community.py`) |
| The current game's ChrCustomizationOption, Choice, Element and Geoset tables, and Forever's ChrModel and ChrCustomizationOption, via wago.tools (CSV exports, once: `tools/battlenet_look/export.py`) | Which geosets a Battle.net look choice shows when Forever's tables don't have it, so the 3D view draws the matching Forever choice | Game data © Blizzard Entertainment, used as facts (numbers only) | `data/battlenet-look.json` |
| The Forever client's spell, zone, talent and class tables via wago.tools, fetched at run time | The knowledge base's spell, zone and talent documents | Game data © Blizzard Entertainment, used as facts | not shipped: cached next to the 3D tables |
| [wow.export](https://github.com/Kruithne/wow.export) by Kruithne, Marlamin and contributors | How the shipper reads the game's own storage (CASC: build info, local indexes, BLTE, encoding and root), followed from its `casc/casc-source-local.js`, `casc-source.js` and `blte-reader.js` | MIT (Copyright (c) Kruithne, Copyright (c) Marlamin), notice below | `shipper/wow_stats.py` (`Casc`) |
| [wow.export](https://github.com/Kruithne/wow.export) by Kruithne, Marlamin and contributors | How the 3D view reads the client tables (`db/WDCReader.js`) and puts a character together from them: model, geosets, composited body textures, item models and their places (`modules/tab_characters.js`, `ui/character-appearance.js`, `db/caches/DB*.js`), ported to Python | MIT (Copyright (c) Kruithne, Copyright (c) Marlamin), notice below | `wowstats/db2.py`, `wowstats/character3d.py` |
| [wow.export](https://github.com/Kruithne/wow.export) by Kruithne, Marlamin and contributors | How the 3D view draws a character in the browser: the M2, skin, skeleton, animation and BLP readers, the M2 renderer, its shaders and the body texture compositing (`3D/loaders/`, `3D/renderers/`, `src/shaders/`, `casc/blp.js`, `ui/model-viewer-gl.js`), ported to plain WebGL with no build step | MIT (Copyright (c) Kruithne, Copyright (c) Marlamin), notice below and in `web/model3d/m3d/LICENSE-wow.export.txt` | `web/model3d/m3d/` |
| [WoWDBDefs](https://github.com/wowdev/WoWDBDefs) by the WoWDBDefs contributors | The client tables' column names and types (`definitions/*.dbd`), trimmed: only the tables the 3D view and the item facts read, and in each only its column list and the definition blocks for Forever's 1.60 builds (`tools/model3d_dbd.py`) | CC BY-SA 4.0 (the trimmed files stay under it), notice below | `data/dbd/` |
| The Ed25519 reference code of [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) section 6 (Josefsson, Liusvaara) | Checking the release signature before the shipper updates itself (the verify half), and signing a release (the sign half, on the same arithmetic) | Simplified BSD, as IETF Trust code components (Copyright (c) 2017 IETF Trust and the persons identified as the document authors) | `shipper/wow_stats.py` (`ed25519_verify`), `tools/release/shipper_sign.py` |
| [cMaNGOS classic-db](https://github.com/cmangos/classic-db), commit 28ef6259c782 (`Full_DB/ClassicDB_1_12_1_z2815.sql.gz`) by the cMaNGOS contributors | Who each original dungeon places: creature levels, rank, spawns and pools, summed per creature (`tools/dungeon_xp/export.py`) for the kill-XP estimate of a dungeon not run yet | GPL-3.0. The project's COPYRIGHT.md says parts of its data came from other projects and may be under other terms; we use the levels, ranks and counts as facts | `data/dungeons/kill-xp.json` (`spawns` dungeons); `data/dungeons/wings.json` (creature and object spawns placed in a wing, `tools/dungeon_wings/export.py`) |
| [EG Link](https://github.com/JIVESCORP/eg-link-output-wowf) output by Efficient Games, commit 5d76a9e5bbba | The new Forever dungeons' creature levels and kills per logged run, for the same estimate. Data from Efficient Games (https://efficient.games), licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/) | CC BY 4.0 | `data/dungeons/kill-xp.json` (`logged_runs` and `reference_runs` dungeons) |
| [advocaite/TrinityCore](https://github.com/advocaite/TrinityCore), branch `forever`, commit 25be61f58f48 | The new dungeons' creature ranks (elite or normal) and placed counts, read at run time | GPL-2.0-only: kept as a separate data file under its own licence, never merged into our code or our GPL-3.0 files | `data/dungeons/forever-spawns/` (its `LICENSE` and `README.md`) |
| Cinzel, Alegreya, Alegreya Sans | The page's fonts | SIL Open Font License 1.1 | `web/fonts/`, licence files next to them |
| [Plausible](https://github.com/plausible/analytics) tracker v33 (Plausible CE v3.2.1, built at commit e4f5a87a5570, unmodified) | Page counts on the docs site only, not the product | MIT (Copyright 2020 Plausible Insights OÜ), notice in the file's header | `site/public/js/app.js` |

No version of ForeverLevelingBiS after v0.12.0-beta is used: its 1.x
releases are All Rights Reserved.

The public demo's 3D model is Blizzard's art: Nibbet's character model,
his gear's models and textures, and the client table rows that dress him,
read from a Forever client by `tools/fixtures/demo3d.py`. They are © Blizzard
Entertainment and no licence of ours covers them. They are never in git: they
ship only as the `demo-3d` release's asset (tag `demo-3d-2`), which the docs
site's build downloads into the demo (`site/scripts/demo3d-asset.sh`). Deleting
that release removes them; the demo then draws its made-up figure
(`tools/fixtures/golden3d.py`) again.

Test fixtures, in the source only (no image, wheel or installer carries
them): `tests/fixtures/plausible-script.manual.js` is
[Plausible Analytics](https://github.com/plausible/analytics)' manual tracker,
unmodified, MIT (Copyright 2020 Plausible Insights OÜ), notice at the top of
the file. `tests/fixtures/icon.jpg` was drawn for these tests (a few shapes);
it is part of wow-stats.

## ForeverLevelingBiS v0.12.0-beta

MIT License

Copyright (c) 2026 Thunder

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## WoWSims Exporter

MIT License

Copyright (c) 2022 General Wrex

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## forever-db

forever-db's `NOTICE.md`, as its release ships it (`tools/kb/bump.py forever-db` refuses a release whose notice says more than this copy):

The Polestar quest data for World of Warcraft: Forever. Every value in its tables is held by at
least one of the independent sources below.

### Credits

- **EG Link.** EG Link output: World of Warcraft: Forever (https://github.com/JIVESCORP/eg-link-output-wowf),
  data from Efficient Games (https://efficient.games), licensed under CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/); reduced to quest facts.
- **CMaNGOS classic-db** (https://github.com/cmangos/classic-db), GPL-3.0.
- **VMaNGOS** values (through pfQuest) and **Factoruide** data (https://github.com/Factoruide), GPL-2.0-or-later.
- **ForeverTDB**, the Forever TrinityCore world database (https://github.com/advocaite/TrinityCore/tree/forever), GPL-2.0.
- **QuestieDB** (Questie team), credited per its maintainer; Questie is moving to GPL-3.0. The quest
  objective lines in `corrections/quests.json` are the game's words as QuestieDB, EG Link's client
  data and players' recordings hold them.
- **World of Warcraft: Forever client tables**, read from the game client.
- **pfQuest** (https://github.com/shagu/pfQuest), MIT licence:

    MIT License

    Copyright (c) 2017-2021 Eric Mauser (Shagu)

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

### Disclaimer

World of Warcraft and Blizzard Entertainment are trademarks or registered trademarks of Blizzard
Entertainment, Inc. in the U.S. and/or other countries. This data set is not made, endorsed or
supported by Blizzard Entertainment. Names, ids and numbers describe the game for players. No game
assets are included. The game's own text is limited to names, titles and the quest objective lines
the corrections carry; that text is Blizzard's and neither of this data set's licences covers it.

## Profession observations

`data/kb/professions/learning.json` records two facts from the project's issue tracker.
The owner reported the Black Slippers trainer requirement in [#283](https://github.com/sam-dumont/wow-mcp/issues/283).
[#281](https://github.com/sam-dumont/wow-mcp/issues/281) identifies one reported source for the Twisted Nether Wand
formula. Its availability on this client build is unverified. Each row links the report and its independent reference.
Other trainer requirements come from the GPL-licensed Polestar guide listed above. The offline recipe audit reads
an installed ForeverLevelingBiS table locally. Its table and wowforever.ninja's observations are not distributed.

## QuestieDB

Polestar's own notice for its guide data (`addon/Polestar_Guides/LICENSE-DATA.txt`, v0.4.1) adds
that QuestieDB has no licence file and its maintainers ask that it be treated as all rights
reserved ([Questie#4447](https://github.com/Questie/Questie/issues/4447)). forever-db keeps a value
only when an independent open source holds it too, and leaves out what only a closed or web source
knows (its README).

The notice above says Questie is moving to GPL-3.0. That hasn't happened: Questie#4447 was closed
in October 2026 without a licence, and its last comments (February 2026) say every contributor
would have to agree to one. wow-stats ships forever-db as Polestar distributes it, QuestieDB
lineage included (the maintainer's call): Polestar puts the same data in front of a much
larger audience, and can build without QuestieDB (`--no-questie`) should that be needed.

## wow.export

MIT License

Copyright (c) Kruithne <kruithne@gmail.com>
Copyright (c) Marlamin <marlamin@marlamin.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## WoWDBDefs

The files in `data/dbd/` are adapted from WoWDBDefs' `definitions/` folder
(https://github.com/wowdev/WoWDBDefs), Copyright WoWDBDefs Contributors,
licensed under the Creative Commons Attribution-ShareAlike 4.0 International
licence (CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/).
Changes: only the tables the 3D view and the item facts read are kept, and in each file only
its COLUMNS section and the definition blocks whose builds are Forever's
(1.60.x); nothing else is altered. `data/dbd/` as a whole is distributed
under CC BY-SA 4.0. No warranties are given.
