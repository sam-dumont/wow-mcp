# Forever reference (quest and item names, dungeons)

**For:** contributors. **At the end:** you know where quest and item names come from and how to remake the file.

`data/forever-reference.json` ships in the image as `wowstats/data/` and
gives the server a name for every quest and item it shows, plus which zones
are dungeons and their level range. `wowstats/forever.py` reads it.

## Why it exists

The shipper names a quest from the data addons on the player's PC and from
the Recorder's own `text` records of the same run. A record from a run that
never saw the quest's text, or sent by an older shipper, carries only
`quest_id`, and it is never sent again (records dedupe on `source_id`).
Without this file the journal would print "quest 631".

The server names a quest in this order:

1. the name the record carries,
2. forever-db's title, or the gear Codex title when forever-db's record has none,
3. the title of a Recorder `text` record in the store (`Views.quest_titles`),
4. nothing: the page says "Unknown quest (631)" and the publisher logs the id.

The quest's level and zone come from forever-db too and show as a muted
second line. Quests in a chain that share one title ("The Missing
Diplomat", 17 parts) get their part, "· 6 of 17", from forever-db's
prerequisites; a repeated title with no known order shows its id instead,
muted, so two lines never read the same. Items work the same way: Forever's own ItemSparse only holds the
items Forever changed, so a vanilla item from the Recorder's loot (say
Bloodspattered Surcoat) takes its name from forever-db.

Drops say when they are a dungeon's, with its level range: "Drop: Arcane
Anomaly, City of Dalaran (dungeon, levels 28-35, 33.5%)". City of Dalaran is
one of Forever's new dungeons, a valid source like any other.

## The publish check

`publish.check_names` fails the publish when an answer shows a quest by id
that forever-db names: a view skipped the lookup. A quest no source names is
logged, not fatal. `publish.put_items` fails when an item's icon can't be
fetched, with the list; an item with no icon in the client tables gets the
game's question mark (`inv_misc_questionmark`), never an empty frame.

## Sources

| Part | From | Licence |
| --- | --- | --- |
| `quests` (title, level, minimum level, zone), `parts` (chain order), `pre` (the quests one needs done first, breadcrumbs left out), `items` (names) | forever-db, [Polestar](https://github.com/fitzforge/polestar)'s quest database, at its own release in `tools/forever-db-tag` (`data-v*`: the zip, checked against the release's `SHA256SUMS`; `forever-db/data/quest.jsonl`, `quest_prereq.jsonl`, `item.jsonl`), not the copy Polestar's addon release bundles | GPL-3.0, with CC BY 4.0 and MIT parts (`THIRD_PARTY.md`) |
| `rewards`: gear exactly one quest gives (an item or choice reward of one quest in forever-db or the gear data, no creature or object that drops it, no craft or drop in the gear data, green or better; the gear data covers the new quests forever-db has no reward rows for, City of Dalaran's among them), so holding it means that quest was handed in (`wowstats/quests.py`) | forever-db's `quest_reward.jsonl` and `item_source.jsonl`, Polestar's gear data (`data/gear-reference.json`) | GPL-3.0, with the MIT parts of the gear data (`THIRD_PARTY.md`) |
| `gear_quests`: every quest with a gear reward, as `[side, race mask, class mask, [rewards], [choices]]`: where gear comes from when Polestar's sources don't say (`wowstats/gear.py` `with_game_items`) | forever-db's `quest.jsonl` and `quest_reward.jsonl` | GPL-3.0, with CC BY 4.0 and MIT parts (`THIRD_PARTY.md`) |
| `areas`: every area's name; for a dungeon's or a raid's (its map an instance the Group Finder lists) its instance Map id, bosses and level range. `maps`: every map's name and type, and an instance's own area | Forever's client tables AreaTable, Map, DungeonEncounter and GroupFinderActivity on wago.tools, read whole, at the client build in `data/kb/watch.json` (`client_build.known`); level caps from Polestar's `data/codex/levels.json` (`max`), the same rule its Codex uses | Game data © Blizzard Entertainment, used as facts |
| `ui_maps`: each world map's name by its UiMap id, where ForeverDB.net places a quest's giver | Forever's client table UiMap, same build | Game data © Blizzard Entertainment, used as facts |
| `quest_work`: for every quest that gives gear, `[type, foe, bring, objectives]`: forever-db's quest type (1 group, 41 PvP, 62 raid, 81 dungeon, else 0), the strongest creature its objectives send you to (a kill target, or the one creature that drops a collected item; by rank, then level), the items to bring that no creature drops and no object gives, and how many objectives it lists. `npcs`: those creatures and every creature ForeverDB.net names as a drop source, as `[name, level min, level max, rank, zone]` (rank 0 normal, 1 elite, 2 rare elite, 3 boss, 4 rare; the zone for a drop source when one zone map alone holds all its spawns). The gear advice's quest work (`wowstats/questwork.py`) | forever-db's `quest.jsonl`, `quest_objective.jsonl`, `item_source.jsonl`, `npc.jsonl`, `spawn.jsonl` and `ui_map.jsonl`; the gear data's and ForeverDB.net's quests and creatures (`data/gear-reference.json`, `data/foreverdb/item-sources.json`); zone names from Forever's client table UiMap on wago.tools | GPL-3.0, with CC BY 4.0 and MIT parts (`THIRD_PARTY.md`); ForeverDB.net's data GPL-3.0 with its attribution term |
| `factions`: every reputation's name and side (Alliance, Horde, or none for a neutral one like Ratchet): a reputation's quartermaster sells to its side (`wowstats/gear.py` `side_of_gate`) | Forever's client table Faction on wago.tools, same build: the side of the faction it sits under (Alliance or Alliance Forces, Horde or Horde Forces) | Game data © Blizzard Entertainment, used as facts |

`unavailable_quests` lists gear quests the released forever-db corrections
withdraw or the gear Codex marks `<NYI>`. Gear advice skips those routes and
holding their items gives no credit for those quests. The historical Codex
entry keeps its title and restrictions.

The `guide_quests` rows identify the Library Books reward choices when the quest reward table omits them. The exporter matches each item ID in the guide header to its item name in the milestone step and that step’s hand-in. The guide documents Alliance for the two Greater Friend milestones. Horde availability for those rewards is unknown. [Polestar Library Books guide](https://github.com/fitzforge/polestar/blob/v0.4.3/guides/forever/miscellaneous/library-books.guide)

The `dungeon_drops` fallback covers otherwise unsourced gear. Each NPC must
appear in a single dungeon in the run or spawn data. Polestar's trusted boss
recordings supply the observer count and a measured rate when available.
The separate forever-db release supplies designed loot rates. Each drop
identifies which source supplied it.
[Polestar recording format](https://github.com/fitzforge/polestar/blob/v0.4.3/rust/crates/forge-data/src/codex/recorded.rs)

## New zones and dungeons

Forever adds zones and dungeons faster than Polestar's addon releases
can carry them. The addon previously bundled forever-db 2026.11.1, made from
client 1.60.1.70532, which has no Drowned City at all (its Map 2970 came
later), and its quests stop before Saving Sunnyglade or The Saltseer
Captain. forever-db's own release 2026.11.2 has both. So forever-db comes
from its own releases, and the client tables are read whole at the newest
build the knowledge base knows: every area and every map, whether a quest
names it yet or not.

A quest's zone, everywhere the server shows one (`forever.quest_zone`):
forever-db's zone; else its one dungeon from `data/dungeons/quests.json`
(`forever.quest_dungeon`: forever-db's zone, a boss it kills, Polestar's
journal or its dungeon runs); else the map ForeverDB.net puts its giver on
(`ui_maps`). The export stops when forever-db zones a quest, or the gear
data a drop, in an area the client doesn't name, and
`tests/test_forever.py` fails when a quest, giver or dungeon in the bundled
data has no zone name.

An instance's own area (`maps` `area`, `forever.area_of_map`) is the one
carrying the map's name: `Map.AreaTableID` (Blackrock Spire's sits on its
continent's map), else a top area of the map (Sunken Temple 1417, the zone
forever-db's quests use). The dungeon list (`wowstats.plan.dungeonview`)
takes one dungeon per instance map, its own area. The level range is the
Group Finder's normal-difficulty row for the dungeon's map; an instanced map
the Group Finder doesn't list (an event's copy of Deadwind Pass) is in
`maps` with its type, its areas plain names.

## Remaking it

    uv run python tools/forever_reference/export.py --polestar <polestar checkout>            # at the pins
    uv run python tools/forever_reference/export.py --polestar <polestar checkout> --latest   # forever-db's newest release first

Both versions are pins, recorded in the file's `source`: forever-db's
release in `tools/forever-db-tag` (`--latest` moves it to the newest
`data-v*` release, then exports) and the client build in
`data/kb/watch.json` (`client_build.known`, moved daily by the refresh job's
client-build kind, which reruns this export, the dungeon quests and the
route reference). The export reuses the checked release in the daily build's
overlay or in `--forever-db-cache`. The addon checkout can have unpublished
quest corrections even when its index carries the same version.
`.github/workflows/route-reference.yml` reruns it on each change, on a new
`tools/polestar-tag`, `tools/forever-db-tag` or client build, and fails when
the committed file differs.
