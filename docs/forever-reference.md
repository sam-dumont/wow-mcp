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
2. forever-db's title (every quest Forever has, its own 98xxx ones too),
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
| `quests` (title, level, minimum level, zone), `parts` (chain order), `pre` (the quests one needs done first, breadcrumbs left out), `items` (names) | forever-db in [Polestar](https://github.com/fitzforge/polestar) at `POLESTAR_TAG` (`forever-db/data/quest.jsonl`, `quest_prereq.jsonl`, `item.jsonl`) | GPL-3.0, with CC BY 4.0 and MIT parts (`THIRD_PARTY.md`) |
| `rewards`: gear exactly one quest gives (an item or choice reward of one quest in forever-db or the gear data, no creature or object that drops it, no craft or drop in the gear data, green or better; the gear data covers the new quests forever-db has no reward rows for, City of Dalaran's among them), so holding it means that quest was handed in (`wowstats/quests.py`) | forever-db's `quest_reward.jsonl` and `item_source.jsonl`, Polestar's gear data (`data/gear-reference.json`) | GPL-3.0, with the MIT parts of the gear data (`THIRD_PARTY.md`) |
| `gear_quests`: every quest with a gear reward, as `[side, race mask, class mask, [rewards], [choices]]`: where gear comes from when Polestar's sources don't say (`wowstats/gear.py` `with_game_items`) | forever-db's `quest.jsonl` and `quest_reward.jsonl` | GPL-3.0, with CC BY 4.0 and MIT parts (`THIRD_PARTY.md`) |
| `areas`: zone names, dungeon or raid (with its instance Map id), its bosses, level range | Forever's client tables AreaTable, Map, DungeonEncounter and GroupFinderActivity on wago.tools, at the client build in `data/kb/watch.json` (`client_build.known`); level caps from Polestar's `data/codex/levels.json` (`max`), the same rule its Codex uses | Game data © Blizzard Entertainment, used as facts |
| `ui_maps`: each world map's name by its UiMap id, where ForeverDB.net places a quest's giver | Forever's client table UiMap, same build | Game data © Blizzard Entertainment, used as facts |
| `factions`: every reputation's name and side (Alliance, Horde, or none for a neutral one like Ratchet): a reputation's quartermaster sells to its side (`wowstats/gear.py` `side_of_gate`) | Forever's client table Faction on wago.tools, at forever-db's `clientBuild`: the side of the faction it sits under (Alliance or Alliance Forces, Horde or Horde Forces) | Game data © Blizzard Entertainment, used as facts |

## New zones and dungeons

Forever adds zones and dungeons faster than forever-db is published:
forever-db 2026.11.1 was made from client 1.60.1.70532, which has no
Drowned City at all (its Map 2970 came later), and its quests stop before
Saving Sunnyglade or The Saltseer Captain. So the client tables are read at
the newest build the knowledge base knows (the refresh job's client-build
lane moves it and reruns this export), and the file keeps every zone the
world map shows and every instance the Group Finder lists, whether a quest
names it yet or not.

A quest's zone, everywhere the server shows one (`forever.quest_zone`):
forever-db's zone; else its one dungeon from `data/dungeons/quests.json`
(`forever.quest_dungeon`: forever-db's zone, a boss it kills, Polestar's
journal or its dungeon runs); else the map ForeverDB.net puts its giver on
(`ui_maps`). The export stops when forever-db zones a quest in an area the
client doesn't name, and `tests/test_forever.py` fails when a quest,
giver or dungeon in the bundled data has no zone name.

Beyond those, only areas a quest or a drop names are kept. The level range is the Group
Finder's normal-difficulty row for the dungeon's map; an instance whose area
sits on its continent's map (Blackrock Spire) is found through `Map.AreaTableID`.

## Remaking it

    uv run python tools/forever_reference/export.py --polestar <polestar checkout>

Stdlib only, the same bytes on every run. `.github/workflows/route-reference.yml`
reruns it on each change, on a new `tools/polestar-tag` and on a new client build in `data/kb/watch.json`, and fails when the
committed file differs.
