# forever-spawns: the new dungeons' ranks and spawn counts

`forever-spawns.json` holds two facts per creature of three new Forever
dungeons (Ruins of Lordaeron, The Drowned City, City of Dalaran): its rank
(elite or normal) and how many are placed in the dungeon. wow-stats reads
it at run time for the kill-XP estimate (`wowstats/plan/dungeonxp.py`).

**Licence: GPL-2.0-only** (`LICENSE` next to this file), the licence of
the source below. It is a separate file under its own licence: none of it
is copied into wow-stats' code or its GPL-3.0 data files.

## Source

[advocaite/TrinityCore](https://github.com/advocaite/TrinityCore), branch
`forever`, commit `25be61f58f48b6e7bc8eda1146af8e1c73c82470`, files under
`sql/custom/world/`. Each row's `from` column says which of them:

- `ADV-SNIFF`: the spawn and template files from a sniff of the official
  beta (build 70124): the Ruins of Lordaeron;
- `ADV-CACHE`: `creature_template.Classification` read from players'
  `creaturecache.wdb` (The Drowned City);
- `ANOCORE`: the `2026_11_05_99` rows (City of Dalaran);
- `health`: a rank read from EG Link's observed health against that
  branch's base health curve (`2026_10_27_74`), where none of the above
  has the creature.

Rows that branch copied from EG Link or from Everlake are not here: they
count as those sources.

`placed` is filled only where the branch has a full spawn table for the
dungeon (sniff or AnoCore); it caps the kills counted per run, so a
creature that rises again or comes in waves counts once per spawn.

Rebuilt by `tools/dungeon_xp/export.py`; don't edit by hand.
