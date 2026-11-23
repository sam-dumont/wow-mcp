# Route times (Polestar)

**For:** players asking "was that level slow?", and contributors. **At the end:** you know where the route times come from and how far to trust them.

The `coach` prompt (your assistant, over the MCP) says "level 28 took you 4h10m, the route plans 1h39m". The second
number comes from `data/route-reference.json`, which ships in the image: how
long each level, each levelling guide and each guide step takes on Polestar's
route, for every starting race and class of both sides.

## Credit and licence

The numbers are Polestar's. [Polestar](https://github.com/fitzforge/polestar)
is the levelling guide addon for WoW Forever, by fitzforge and its
contributors, licensed GPL-3.0-only. The file is made by Polestar's own route
planner from Polestar's own guides and its open quest database (forever-db,
GPL-3.0), so it is GPL-3.0 too, and so is wow-stats (`LICENSE`: the GNU GPL
version 3, and only that version).

The file records the Polestar release it came from (`polestar.tag`,
`polestar.commit`) and the forever-db version.

## How the file is made

`tools/route_reference/export.mjs` runs Polestar's planner without building
anything:

1. A shallow clone of Polestar at a release tag (about 450 MB on disk).
2. `tools/route_reference/dump.lua` reads the compiled guides the release
   ships (`addon/Polestar_Guides*/Guides.lua`), the guide pack's map frames
   and the addon's flights, boats and inns (`Nav/TravelData.lua`). The Lua
   runs in an empty environment: no `io`, `os` or `load`.
3. Quests, NPCs, objects, items and spawn points come from `forever-db/data`.
4. The planner's command registry (`tools/planner/commands.js`) answers
   `guides_list` and `timeline_get` for each race's first guide and each
   class: 10 starts (Skyborne on both sides) times 9 classes, 90 runs to 60.
   The export keeps the medians per side and level, per guide and per step,
   and each run's ding times.

It takes under 3 seconds and gives the same file byte for byte on every run. To redo it after a Polestar release:

```sh
git clone --depth 1 --branch v0.4.2.2 https://github.com/fitzforge/polestar /tmp/polestar
node tools/route_reference/export.mjs --polestar /tmp/polestar
```

Node 24 and Lua 5.1 or newer (`--lua lua5.4` to pick one). CI
(`.github/workflows/route-reference.yml`) reruns it on every change to the
export or the file, with the tag set in that workflow, and fails when the
result differs from the committed file. It never commits: the tag lives in
`tools/polestar-tag`, and the refresh job (docs/kb.md, "The refresh job")
moves it, reruns every Polestar export and merges the PR when a release
passes its checks. By hand: change the file, rerun the exports, commit.

## What the file holds

| Key | What |
| --- | --- |
| `levels.<side>.<level>` | Seconds spent in the level (its ding to the next), median over races and classes, with `min_s`, `max_s`, `reached_s`, the guide the level ends in and `by_class` |
| `guides.<id>` | Seconds for the guide, its levels, and `steps`: `[step, seconds, title]`, steps counted from 1 like the addon's |
| `routes[]` | One per race and class: total seconds and `dings` as `[level, seconds since level 1]` |
| `bias` | The walk measurement below |
| `trips` | A dungeon trip's quests and travel, for the dungeon's line: each dungeon's page of quests and entrance, what Polestar's pick-up and hand-in rules read per quest, the flight and boat network, and the reference tours. See [Dungeon trips](#dungeon-trips) |
| `places` | Where things are, for the Dungeons panel: `npc` and `object` (quest givers and enders) as `[uiMap, x, y]` plus the spawn count when there are several, `zones` (uiMap id to the guides' zone name), `entrances` (per instance Map id) and their `keys`. See below |

Guide ids are Polestar's (`forever/alliance/05-duskwood`), the `guide` the
recorder writes on each step. `ROUTE_REFERENCE_FILE` points at another file,
`off` turns the comparison off.

## Places

A quest giver's place is where Polestar's guides send the player to it (an
accept, turn-in or vendor goal's live-frame map point, the one the guides use
most). An NPC no guide visits gets its forever-db spawn only when exactly one
zone map holds every spawn: map rectangles overlap their neighbours (Auberdine,
in Darkshore, sits inside Felwood's rectangle too), so the smallest rectangle
is not a zone, and a spot two zones could hold gets no place at all. An NPC
whose spawns are all in one instance map gets `{"instance": <Map id>}`.

Dungeon entrances, keys and attunements come from Polestar's own curated
`data/codex/entrances.json` and `attunements.json`, each entrance with its
source (QuestieDB's Forever table, players' recordings, a guide site); the
Alliance's own way in sits under `A` where it differs. The flight path next to
an entrance is TravelData.lua's nearest taxi node of each side on that map, a
straight line on the map. Meeting stones aren't there: no source we read ties
a stone to its dungeon.

## Dungeon trips

`trips` holds what Polestar's "Pick up the quests" and "Hand in the quests" guides
(`addon/Polestar/Dungeons/Pickup.lua`, `Handin.lua`) need, and
`tools/route_reference/trips.mjs` ports their rules: which quests to take (the class,
race and side ones that apply, their open prerequisites first, a breadcrumb only when its
detour is under 300 s), the order of the stops (nearest next, cheapest insertion, then
moving runs of one to three stops), and the travel between them (walk, or walk + flights
and boats + walk, Polestar's TravelCost). `wowstats/plan/tripcost.py` replays the same
rules at runtime on the player's own quests (done, in the log) from where they were last
seen (the semantic layer's `map_now`, `x_now`, `y_now`).

A dungeon's page is forever-db's quests zoned in it plus Polestar's journal list, as the
Codex's dungeon pages are not in the addon checkout. The per-side `tours` (standing at the
entrance, at the dungeon's floor, nothing done) are what the Python must replay:
`tests/plan/test_tripcost.py` checks every one.

A dungeon run in wings (Scarlet Monastery, Dire Maul, Blackrock Spire, Stratholme,
Maraudon) also has a page and tours per wing (`wings`): the page's quests whose objectives
are in that wing, from `data/dungeons/wings.json`. The wings share the dungeon's entrance
(Polestar lists one per dungeon; the wings' doors are a few hundred yards apart).

Not ported: carrying a quest across stops to fit the log (Polestar's split), the
25-quest log cap, follow-ups taken at the hand-in, exclusive quest groups and profession
quests. A quest a dungeon page lists whose giver forever-db places nowhere is counted as
given inside, like those whose giver only spawns in the instance.

Against one character's recorded trips (2026-11-23), the model's pickups ran 11-25 min
where the measured ones ran 18-53 min (the measure counts everything from the start to the
last pickup, other quests and fights included); the measured hand-ins include quests from outside the dungeon (65 min against the model's 22
for the Stockade).

## How far to trust it

The clock models a quick player who knows the route. Real players are
slower. One player's levels 23 to 31 (Gnome Priest) took 1.3 to 3.3 times
the route's time, 1.9 times on the median level. Read it for the shape (which
levels are slow against your own usual ratio), not as a target.

What the export can't reproduce from Polestar's maintainer setup:

- **Straight-line walks.** No navigation grid (`nav build` is 750 MB), so
  every walk is distance times a detour factor over run speed. The release's
  baked walks (`Legs.lua`, 2761 of them) measure the real detour: 1.52 in the
  Eastern Kingdoms against the model's 1.35, 1.32 in Kalimdor against 1.30
  (path length over straight line, summed). Eastern Kingdoms walks run about
  13% short. Travel (walks, flights, boats) is 48% of a run's clock, so the
  whole clock runs short by 3 to 4% at most: an estimate, not measured end to
  end.
- **Uncalibrated.** The simulator's calibration lives in Polestar's
  `data/sim`, which isn't in the repository: the planner runs on its priors.
- **forever-db, not a full data build.** Mid route the runs sit 1 to 2
  levels below Polestar's own calibrated level model (`data/route-levels.json`:
  Redridge left at 25 here against 27.4 there), and they catch up by 60. A
  level's time can come from a slightly different guide than in the game.
- **190 to 250 planner warnings per run**, nearly all delivery quests whose
  carried item forever-db lists as an objective. They don't change the time.

On Polestar {{polestar_version}}, the full runs take {{route_hours_min}} to {{route_hours_max}} hours to 60 depending on class:
{{route_fastest_class}} fastest ({{route_fastest_hours}} h on average), {{route_slowest_class}} slowest ({{route_slowest_hours}} h). Polestar's
50-60 rebuild (grind steps replaced by quests, Mount Hyjal included) took 16 to 25 hours off
every run, nearly all of it in levels 50 to 60.
