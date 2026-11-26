# Dungeon kill XP

**For:** players who want to know what a dungeon's kills are worth before
they run it, and anyone checking where the number comes from. **At the
end:** you know how the estimate is built, how close it got on real runs,
and which dungeons don't have one yet.

Forever's dungeons are worth running for their quests and their gear. Their
kill XP is small on purpose, and the rules stop you from farming the same
one: so the Dungeons panel lists the quests and the drops first and the
kill XP after them, always labelled for what it is.

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
- **Five resets and re-entries per hour for each dungeon.** Blizzard reduced
  the limit from ten on November 20. The hotfix gives no separate hourly
  total for visits to different dungeons.
  [November 20 hotfix](https://us.forums.blizzard.com/en/wow/t/2403040/14)

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
| The original ones | 85% of the creatures cMaNGOS's spawn table places (a run skips some packs). Scarlet Monastery, Dire Maul, Blackrock Spire, Stratholme and Maraudon get one number per wing (each wing is its own line, [`data/dungeons/wings.json`](../data/dungeons/wings.json)); Upper Blackrock Spire counts as a 10-player raid | estimate |
| Ruins of Lordaeron, The Drowned City | the kills per run in players' combat logs (EG Link), skips included | estimate |
| City of Dalaran | two reference runs at level 32, carried to your level by the formula | estimate |
| The Hall of Thanes, Excavation Site, Krol'dok Stronghold, Alcaz Prison, Blackmaw Hold, Shaper's Terrace | too few logged runs to say | not enough data yet |

Time inside is kills × the seconds a kill took in the reference runs:
13.5 s in the original dungeons (the Stockade's full clear), 23 s in the new
ones (Dalaran's two runs). The run's line in the panel and the Overview uses
that time too, where a guide's clear time often reads short (Gnomeregan: the
guide's 40 min against ~1h15 here, and about an hour on a real run).

**Once you've run a dungeon, your own runs replace the time and the kills
inside** (label `measured`): your kill XP inside, minus the quests you handed
in there, carried to your current level, and your own time in there.

Outside the instance it goes leg by leg: the pickups, the walk there, the
walk back and the hand-ins. Each one comes from your runs there that saw it,
else from Polestar's rules at your pace, else from your other trips that saw
it. The rules run on your quests as they are now: one already in your log
needs no pickup (and no walk to its giver), a done one costs neither pickup
nor hand-in and pays no XP. A leg the route has no stop for costs nothing,
whatever an old trip measured: with every quest in your log, no pickups. A leg the records didn't see is unknown, never 0 s: log in at the door,
and that trip saw the 32 minutes inside and nothing of the walk there. Until
2026-11-25 that read as a walk in no time, so a City of Dalaran run seen
inside only came out at 32m02s whether the quests around it took 0, 35 or
120 minutes, with the quests' XP on top. Now it's 32m02s, 1h07 or 2h32. A
leg nothing can time (no route data, no trip that saw it) leaves the trip's
time unknown, and its "×" with it, rather than a quest's XP paid in no time.
The hand-ins count as seen only when all of a trip's quests were handed in
inside the trip: one handed in three hours later at the grate pays its XP
to the trip, but its walk was never on the clock.

The estimate counts in the run's XP, its "× your questing" and the ranking of
dungeons, labelled `estimate` in `gain.trip` (until 2026-11-23 it stayed out of them,
and a dungeon you hadn't run had no "×" at all). The pickups and
hand-ins around it come from Polestar's pick-up and hand-in rules on your
quests, at your own pace from your past trips, labelled `your pace` (`route`, the
straight run, before your first trip; [Route reference](route-reference.md#dungeon-trips)).

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

## Quests handed in outside

A dungeon's quests often pay out at the door or in a city: City of
Dalaran's at the Silverpine grate, the Deadmines' in Westfall, the
Stockade's in Stormwind. Counted where they were handed in, two Dalaran
quests (19.4k XP) read as Silverpine Forest's quest XP and Dalaran's trip
looked a quarter slower than it was. So a dungeon quest's hand-in counts
toward the run it came from:

- **the nearest run of that dungeon before the hand-in**, inside it or
  after it. Two runs of the same dungeon: the later one before the
  hand-in wins;
- **within a day of leaving it**. That covers logging out after the run and
  handing in the next evening; later than that, the hand-in stays its
  zone's quest XP;
- **the quest was in the log before the run ended**. Picked up after the run,
  or handed in with no run of its dungeon before it, it was done without
  one and stays its zone's quest XP.

The trip's end follows such a hand-in while each comes within 2 hours of
the last and before the next run, the same chain as the hand-in guide
steps; a later one adds its XP, not its time, and that trip's hand-ins
count as not seen, so the estimate takes them from the rules. The zone it was handed in
keeps none of it: `zone_quest_xp` in the analysis is the zone's own quest
XP, `dungeon_quest_xp` what went to the runs. The Dungeons panel's "XP a
trip" says how many quests were turned in outside, and the MCP's
`dungeons` gives each run's trip with them.

Which dungeon a quest belongs to is `data/dungeons/quests.json`, from
Forever's own data only: forever-db at its release in `tools/forever-db-tag`, the rest at Polestar's tag:

| Source | What it says |
|---|---|
| forever-db's zone | the quest's zone is the dungeon's area: where its work is done, wherever it's picked up |
| a boss it kills | a kill objective in forever-db whose creature is one of the dungeon's encounters (the client's DungeonEncounter, in `data/forever-reference.json`): The Saltseer Captain, handed in at Booty Bay with no zone, kills Captain Dreadrise of The Drowned City |
| Polestar's dungeon journal (`data/codex/journal-quests.json`) | cited rows for quests no rule places: giver, ender and zone outside, the objective inside. Its exclude rows take City of Dalaran's city quests off |
| Polestar's dungeon runs (`data/dungeon-runs.json`) | the quests a run picks up, works on, starts from an item inside and hands in |

forever-db's own dungeon flag (quest type 81) is noted beside a quest one
of these places; alone it doesn't say which dungeon. All 28 dungeons on the panel have quests. Shaper's Terrace has 6,
Gnomeregan 28, and Blackrock Depths 42. The coverage test checks each
dungeon for quests or an explicit reason it has none.

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
the commands); `data/dungeons/wings.json`, which wing each creature, object
and quest belongs to, by `tools/dungeon_wings/export.py` from the same
cMaNGOS dump and Polestar's forever-db; `data/dungeons/quests.json`, which
dungeon each quest belongs to, by `tools/dungeon_quests/export.py` from a
Polestar checkout and forever-db's pinned release (rerun by the route-reference workflow and the knowledge
base's refresh at each Polestar tag and client build). The MCP answer carries each estimate's sources and commit
(`gain.kill_xp.provenance`) and the data's date (`as_of`).
