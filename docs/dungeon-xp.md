# Dungeon kill XP

**For:** players who want to know what a dungeon's kills are worth before
they run it, and anyone checking where the number comes from. **At the
end:** you know how the estimate is built, how close it got on real runs,
and which dungeons don't have one yet.

Forever's dungeons are worth running for their quests and their gear. Their
kill XP is small on purpose, and the rules stop you from farming the same
one: so wow-stats gives the kill XP apart from the quests and the drops,
always labelled for what it is.

## The rules it follows

- **Kill XP is 60% of Classic's in a dungeon.** It was 40% at launch;
  Blizzard raised it by half in the 8-9 November 2026 hotfix
  ([EU hotfix notes](https://eu.forums.blizzard.com/en/wow/t/wow-forever-hotfixes-november-11-2026/639885/1),
  and the community manager's post of 10 November in the
  [dungeon XP thread](https://us.forums.blizzard.com/en/wow/t/the-majority-wants-increased-dungeon-xp/2406555/254)).
- **No XP from a mob more than 12 levels under the party's highest
  player** (14 November change,
  [Blizzard's post](https://us.forums.blizzard.com/en/wow/t/player-level-dungeon-xp-adjustment-november-14/2416343)).
  A high-level friend dragging you through kills your XP.
- **About 5 instance entries an hour per character.** Only a guide gives the
  number ([Warcraft Tavern](https://www.warcrafttavern.com/forever/guides/dungeons/));
  Blizzard hasn't published one.

## How a run's kill XP is estimated

One kill pays each of five players of your level:

    Classic's kill XP at your level       5 × level + 45 for a mob of your level, less for lower ones,
                                          nothing for a grey one (the knowledge base's xp.kill_formula)
    × 2.5 for an elite                    bosses too: no extra boss multiplier
    × 1.4 / 5                             the group's share
    × 0.6                                 Forever's dungeon factor

Buffs (sleeping bag, Well Fed, rested XP) are not in it.

Who a run kills depends on the dungeon:

| Dungeons | Who a run kills | Label |
|---|---|---|
| The original ones | 85% of the creatures cMaNGOS's spawn table places (a run skips some packs). Scarlet Monastery, Dire Maul, Blackrock Spire and Stratholme get one number per wing; Upper Blackrock Spire counts as a 10-player raid | estimate |
| Ruins of Lordaeron, The Drowned City | the kills per run in players' combat logs (EG Link), skips included | estimate |
| City of Dalaran | two reference runs at level 32, carried to your level by the formula | estimate |
| The Hall of Thanes, Excavation Site, Krol'dok Stronghold, Alcaz Prison, Blackmaw Hold, Shaper's Terrace | too few logged runs to say | not enough data yet |

Time inside is kills × the seconds a kill took in the reference runs:
13.5 s in the original dungeons (the Stockade's full clear), 23 s in the new
ones (Dalaran's two runs).

**Once you've run a dungeon, your own runs replace all of this** (label
`measured`): your kill XP inside, minus the quests you handed in there,
carried to your current level, and your own time.

The estimate never ranks a dungeon and never says it beats your questing.

## How close it gets

Checked against one character's recorded runs (tests in
`tests/plan/test_dungeonxp.py`):

| Check | Measured | Model | Off by |
|---|---|---|---|
| The Stockade, full clear at level 31, five players | 2,738-2,850 XP | 2,867 | +3% on the middle |
| City of Dalaran at level 32: every placed creature once against the two runs together, the bosses counted once | 11,597 XP | 10,465 | −10% |

The per-kill fit over 16 kills gave a factor of 0.56 ± 0.035, so a single
run can land 10-15% either side. One lead to check: the Stockade's kills
fit a zero-difference value of 11 at level 31, where Classic's table says
12. The model keeps 12 until a second dungeon agrees.

## Where the data comes from

- Original dungeons: [cMaNGOS classic-db](https://github.com/cmangos/classic-db),
  commit `28ef6259c782`, GPL-3.0.
- New dungeons' levels and kills per logged run: [EG Link](https://github.com/JIVESCORP/eg-link-output-wowf),
  commit `5d76a9e5bbba`. Data from Efficient Games (https://efficient.games),
  licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).
- New dungeons' ranks and placed counts: [advocaite/TrinityCore](https://github.com/advocaite/TrinityCore)'s
  `forever` branch, commit `25be61f58f48`, GPL-2.0-only, shipped as its own
  file (`data/dungeons/forever-spawns/`, with its licence).

`data/dungeons/` is rebuilt by `tools/dungeon_xp/export.py` (its header has
the commands). The MCP answer carries each estimate's sources and commit
(`gain.kill_xp.provenance`) and the data's date (`as_of`).
