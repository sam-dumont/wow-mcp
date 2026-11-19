# Third-party material

wow-stats is GPL-3.0-only (`LICENSE`). It ships these parts made by others.

| Component | Used for | Licence | Where |
|---|---|---|---|
| [Polestar](https://github.com/fitzforge/polestar) by fitzforge and contributors | Route times (its route planner on its guides); gear advice (its item data, sources, stat weights and scoring, ported to `wowstats/gear.py`) | GPL-3.0-only | `data/route-reference.json`, `data/gear-reference.json`, `wowstats/gear.py`; `docs/route-reference.md`, `docs/gear-reference.md` |
| forever-db, Polestar's quest database (Polestar v0.4.1, built from its own sources: notice below) | Quests, NPCs and items behind the route times; every quest's and item's name, quest level and zone, so the page never shows a bare id | GPL-3.0, with CC BY 4.0 and MIT parts, notice below | `data/route-reference.json`, `data/forever-reference.json` (`quests`, `items`); `docs/forever-reference.md` |
| Forever client tables AreaTable, Map, GroupFinderActivity via wago.tools | Zone names, which areas are dungeons, a dungeon's level range | Game data © Blizzard Entertainment, used as facts | `data/forever-reference.json` (`areas`) |
| Forever client tables Item, ItemModifiedAppearance, ItemAppearance, ItemDisplayInfo, ItemDisplayInfoMaterialRes, TextureFileData, ComponentTextureFileData, ModelFileData, ComponentModelFileData, HelmetGeosetData via wago.tools (build 1.60.1.70959) | How each armor display is dressed, for the 3D view's armor that Wowhead's model data lacks | Game data © Blizzard Entertainment, used as facts | `data/model3d-reference.json.gz`; `docs/model3d-reference.md` |
| Forever client tables Item, ItemModifiedAppearance, ItemAppearance via wago.tools, read at run time | Item icons, including Forever's new items whose icon is only on their appearance | Game data © Blizzard Entertainment, used as facts | `wowstats/itemcard.py` (cache only, nothing committed) |
| [ForeverLevelingBiS](https://www.curseforge.com/wow/addons/forever-leveling-bis) **v0.12.0-beta** by Thunder (data 2026-10-13), as Polestar generated it into `Polestar_ItemDB/Forever.lua` and `Gear/SpecData.lua` | Forever item stats, where leveling gear comes from (quests, drops, crafts), racial gear traits | MIT (Copyright (c) 2026 Thunder), notice below | `data/gear-reference.json` (`items`, `sources`, `quests`, `gear.RACIALS`) |
| [wowsims](https://github.com/wowsims) by the wowsims team, as Polestar blended it into `Gear/SpecData.lua` | Stat weights per level band | MIT (Copyright (c) 2024 wowsims team), notice below | `data/gear-reference.json` (`gear.WEIGHT_BANDS`) |
| Blizzard client data (Classic Era item tables via wago.tools, the Forever client's rating and health tables), as Polestar ships them | Item stats; rating and health per point | Game data © Blizzard Entertainment, used as facts | `data/gear-reference.json` |
| Polestar's forever-db, guide notes, dungeon runs, route model notes and rested constants | The play analysis' knowledge base (quests, NPCs, items, levelling notes, quoted) | GPL-3.0 | `data/kb-reference.json.gz` (`tools/kb/export.py`) |
| The Forever client's spell, zone, talent and class tables via wago.tools, fetched at run time | The knowledge base's spell, zone and talent documents | Game data © Blizzard Entertainment, used as facts | not shipped: cached next to the 3D tables |
| ForeverTDB creature armor by level, as Polestar ships it | The armor share behind the armor penetration weight | GPL-2.0, used as facts | `data/gear-reference.json` (`gear.ARMOR_SHARE`) |
| Cinzel, Alegreya, Alegreya Sans | The page's fonts | SIL Open Font License 1.1 | `web/fonts/`, licence files next to them |
| [jQuery](https://jquery.com) 3.7.1 by the OpenJS Foundation and contributors | The 3D viewer's page (full view only) | MIT | `web/model3d/jquery-3.7.1.min.js`, notice in `web/model3d/LICENSE-jquery.txt` |

No version of ForeverLevelingBiS after v0.12.0-beta is used: its 1.x
releases are All Rights Reserved.

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

## wowsims

MIT License

Copyright (c) 2024 wowsims team

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

forever-db's `NOTICE.md`, as Polestar v0.4.1 ships it:

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
