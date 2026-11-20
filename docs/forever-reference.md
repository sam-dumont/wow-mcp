# Forever reference (quest and item names, dungeons)

**For:** contributors. **At the end:** you know where quest and item names come from and how to remake the file.

`data/forever-reference.json` ships in the image as `wowstats/data/` and
gives the server a name for every quest and item it shows, plus which zones
are dungeons and their level range. `wowstats/forever.py` reads it.

## Why it exists

The shipper names a quest from the data addons on the player's PC and from
the Recorder's own `text` records of the same run. Records shipped before it
learned that (2026-11-18), or from a run that never saw the quest's text,
carry only `quest_id`. They are never sent again (records dedupe on
`source_id`), so the 0.2.3 journal printed "quest 631", "quest 98245" and so
on, and nothing logged it.

Now the server names them, in this order:

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
| `rewards`: gear exactly one quest gives (an item or choice reward of one quest, no creature or object that drops it, no craft or other source in the gear data, green or better), so holding it means that quest was handed in (`wowstats/quests.py`) | forever-db's `quest_reward.jsonl` and `item_source.jsonl`, Polestar's gear data (`data/gear-reference.json`) | GPL-3.0, with the MIT parts of the gear data (`THIRD_PARTY.md`) |
| `areas`: zone names, dungeon or raid (with its instance Map id), level range | Forever's client tables AreaTable, Map and GroupFinderActivity on wago.tools, at forever-db's `clientBuild`; level caps from Polestar's `data/codex/levels.json` (`max`), the same rule its Codex uses | Game data © Blizzard Entertainment, used as facts |

Only areas a quest or a drop names are kept. The level range is the Group
Finder's normal-difficulty row for the dungeon's map; an instance whose area
sits on its continent's map (Blackrock Spire) is found through `Map.AreaTableID`.

## Remaking it

    uv run python tools/forever_reference/export.py --polestar <polestar checkout>

Stdlib only, the same bytes on every run. `.github/workflows/route-reference.yml`
reruns it on each change and on a new `tools/polestar-tag`, and fails when the
committed file differs.
