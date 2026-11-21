# Semantic layer

**For:** anyone asking their AI about a character, and anyone curious how the numbers are
defined. **At the end:** you know which metrics exist, what each one counts, and how to check
an answer.

The MCP server has two ways to ask the play data a question. `play_sql` runs whatever SELECT
the assistant writes. `query_metric` asks for **named metrics**: `played_s` by `level`,
`dungeon_s` by `zone` per week. The server writes the SQL from one model file,
[`wowstats/analysis/semantic.yaml`](../../wowstats/analysis/semantic.yaml), and sends back the
rows, the SQL it ran and each metric's definition and caveats. 66 metrics (3 of them across
two models), 25 dimensions, three time grains (day, week, month).

The character page reads the same file: its time per level, XP an hour, kill and quest totals,
dungeon runs and deaths come out of the layer when the page's answers are built (see
[The pages read it too](#the-pages-read-it-too)). One definition for the page and your AI.

## Why a semantic layer

"How long did level 30 take?" has four honest answers in this data, and they don't agree:

| Metric | Counts | Level 30 on the demo |
|---|---|---|
| `played_s` | Polestar's split: the game's /played inside the level | 11,240 s |
| `online_s` | the Recorder's timeline: every interval between two records of a login | 11,221 s |
| `active_s` | the same timeline, AFK flag off | 11,221 s |
| `calendar_s` | wall clock from the previous ding to this one, nights included | 101,240 s |

With raw SQL the assistant picks one of them without telling you, and the next chat picks
another. With the layer each one has a name, a one-line definition and its caveats, declared
once. The assistant names the one it means, and the answer says which one it got.

Two more things come free with it. Every answer carries the SQL that made it, so you (or the
judge) can re-run it. And the values you filter on are bound as parameters (`$p0`, `$p1`): a
zone called `Westfall'; DROP TABLE levels; --` is just a zone that matches nothing.

The tables underneath moved from SQLite to DuckDB for this: a columnar engine made for
exactly these GROUP BYs, and the dialect the layer compiles to.
The layer itself is [Boring Semantic Layer](https://github.com/boringdata/boring-semantic-layer)
(MIT): measures, dimensions, time grains and ratios in a YAML file, compiled to SQL through ibis
and sqlglot. Only the compiler runs here (no ibis backend, so no pandas or pyarrow in the image).
The others I looked at, on 2026-11-20:

- **dbt MetricFlow** (Apache-2.0): the reference design, but 0.214 caps Python at
  3.14 and the image runs 3.15. It also wants a dbt manifest and a time spine table.
- **Cube**: a Node server next to the app, heavier than the whole image.
- **Malloy**: its own query language to learn, and the Python package was last released in
  September 2024.
- **Wren Engine**: pulls pandas, pyarrow and boto3.

## How a question runs

```mermaid
%%{init: {"flowchart": {"defaultRenderer": "elk"}} }%%
flowchart LR
  rec[Recorder events<br/>one character, one tenant] --> prep[prep<br/>SQLite build]
  prep --> duck[(DuckDB copy<br/>in memory, read only)]
  yaml[semantic.yaml] --> bsl[Boring Semantic Layer<br/>ibis + sqlglot]
  q[query_metric<br/>metrics, group_by, where, grain] --> bsl
  bsl --> sql[SQL with $p0, $p1...]
  sql --> duck
  duck --> ans[rows + SQL + definitions]
  ans --> judge[propose_finding<br/>re-runs the same call]
  ps[play_sql<br/>the assistant's own SELECT] --> duck
```

- **One character at a time.** The database behind both tools holds the calling person's
  character and nothing else (the same tenant scoping as every other tool). A friend naming the
  owner's character gets "no events", from `play_sql` and `query_metric` alike.
- **Several models, no fan-out.** Metrics from different tables (`online_s` from the timeline,
  `played_s` from the level splits, `kills` from the records) are each aggregated on their own
  table, then joined on the `group_by` columns. A level's 11,240 s is never counted once per
  timeline row.
- **Locked down.** DuckDB runs with no file or network access, no extensions, a 256 MB memory
  limit and a 5 s timeout. A query that reads `read_csv`, `read_parquet`, a URL or `ATTACH`
  is refused, and so is one calling `repeat`, `lpad` or a scalar `range` (DuckDB's memory limit
  doesn't count one huge value: `lpad('x', 500000000)` made 1.6 GB in a test).

## Examples on the demo

All on `Testchar-Demo` from [the demo](../self-host/demo.md) (made-up data). Rows trimmed.

**Dungeon time per zone, per week:**

```json
{"metrics": ["dungeon_s"], "group_by": ["zone"], "grain": "week",
 "where": [{"dimension": "dungeon", "op": "is not null"}], "order_by": ["-dungeon_s"]}
```

| zone | week | dungeon_s |
|---|---|---|
| Desolace | 2026-11-09 | 2663 |
| Stranglethorn Vale | 2026-11-16 | 2598 |
| Stonetalon Mountains | 2026-11-09 | 1995 |

(The demo's dungeon runs keep the outdoor zone name; in the game the zone usually becomes the
dungeon. Group by `dungeon` for the dungeon itself.)

**XP per played hour, by level** (a ratio: `level_xp` over `played_s`):

```json
{"metrics": ["xp_per_hour", "level_xp", "played_s"], "group_by": ["level"],
 "where": [{"dimension": "level", "op": "=", "value": 30}]}
```

| level | xp_per_hour | level_xp | played_s |
|---|---|---|---|
| 30 | 17615.66 | 55000 | 11240 |

**Where the deaths happen:**

```json
{"metrics": ["deaths", "kills", "deaths_per_100_kills"], "group_by": ["zone"],
 "order_by": ["-deaths"], "limit": 3}
```

| zone | deaths | kills | deaths_per_100_kills |
|---|---|---|---|
| Stranglethorn Vale | 3 | 281 | 1.068 |
| Wetlands | 1 | 151 | 0.662 |
| Ashenvale | 1 | 93 | 1.075 |

**Same metric, two grains.** `"grain": "day"` gives one row a day; `"grain": "week"` adds them
up (the test suite checks that the weeks sum to the days):

| week | online_s |
|---|---|
| 2026-11-09 | 75162 |
| 2026-11-16 | 16591 |

**Against the route planner** (a derived metric, `played_s / planned_s`):

```json
{"metrics": ["played_s", "planned_s", "vs_route"], "group_by": ["level"], "limit": 3}
```

| level | played_s | planned_s | vs_route |
|---|---|---|---|
| 22 | 6420 | 3933 | 1.632 |
| 23 | 7310 | 4632 | 1.578 |
| 24 | 8520 | 5300 | 1.608 |

**Which quest paid the most XP per minute** (`quest_xp_per_min`, done quests only):

```json
{"metrics": ["quest_xp_per_min", "done_quest_xp", "done_quest_s", "quest_wall_s"],
 "group_by": ["quest"], "order_by": ["-quest_xp_per_min"], "limit": 3}
```

| quest | quest_xp_per_min | done_quest_xp | done_quest_s | quest_wall_s |
|---|---|---|---|---|
| Demo quest 951 | 1219.3 | 3221 | 158.5 | 317 |
| Demo quest 409 | 1211.1 | 3724 | 184.5 | 369 |
| Demo quest 505 | 1042.7 | 3128 | 180 | 540 |

**Time per guide step**, with the step's name from the route reference:

```json
{"metrics": ["online_s"], "group_by": ["guide_title", "step", "step_title"],
 "where": [{"dimension": "step_title", "op": "is not null"}], "order_by": ["-online_s"], "limit": 3}
```

| guide_title | step | step_title | online_s |
|---|---|---|---|
| Wetlands, Ashenvale, Ironforge & Stormwind City | 64 | Shael'dryn | 1892 |
| Wetlands, Ashenvale, Ironforge & Stormwind City | 100 | Jorgen and Elling Trias | 1476 |
| Wetlands & Hillsbrad Foothills | 30 | Prospector Whelgar | 1226 |

**Kills per hour of combat** (`kills` from the records over `combat_s` from the timeline):

```json
{"metrics": ["kills_per_combat_hour", "kills", "combat_s"], "group_by": ["level"], "limit": 3}
```

| level | kills_per_combat_hour | kills | combat_s |
|---|---|---|---|
| 22 | 64.8 | 96 | 5330 |
| 23 | 69.7 | 121 | 6253 |
| 24 | 84.2 | 175 | 7486 |

## Time on a quest

A quest's time is `quest_s`: every online second while the quest sat in the log, **shared
equally among the quests open in that second**. Two quests open for an hour get 30 minutes
each.

The obvious answers are wrong. Wall time from accept to hand-in (`quest_wall_s`) counts the
nights in between: Demo quest 1018 was picked up at 23:22 and handed in the next evening,
73,560 s of wall time for 3,186 s of shared play. Online time while open (`quest_open_s`)
counts the same second once per open quest: on the demo the quests hold about 2 at a time, so
it adds up to 172,284 s, nearly twice the 91,753 s played that week.

Shared, the times add up: on the demo `quest_s` sums to 86,283 s, the online time with at least
one quest in the log, nothing counted twice (a test checks it). What it can't know is which
quest you were working on: a quest that sits in the log while you finish three others takes
its share of their time, so one carried for days looks slow. `quests_open_avg` says how crowded
the log was. The pairing itself: each accept meets the next hand-in or abandon of the same
quest id; a hand-in with no accept before it (picked up before tracking started) is `untimed`,
with its XP and no time.

## Money spent

Nothing the kit reads saves spending. The Recorder has the money a quest pays (and offers), and
a vendor or trainer window opening, never a price paid. Polestar's alts list and Syndicator
keep the purse at logout. Auctionator keeps prices, not sales.

So the layer has `unexplained_gold_delta`: between two logout snapshots, how much the purse
moved minus the quest money handed in between them. That's loot coin, vendor and auction sales
and mail in, less repairs, purchases, training, flights and mail out, all together. It is never
"repairs". A move counts in the level of its first snapshot; filter `dings = 0` for the moves
inside one level. On the demo the rest is the flights: -86 copper at 22, -116 at 24.

[Accountant Classic](https://www.curseforge.com/wow/addons/accountant-classic) (GPL-2.0, lists
Forever, updated 2026-11-12) saves money in and out per source (training, taxi, trade, auction
house, merchants, repairs, mail, quests, loot) per session, day, week, month and total, in
`Accountant_ClassicSaveData`. It isn't read: whether to add it is the owner's call. It counts by
calendar window, not by level, so even with it a level's repairs would come from the totals at
each logout.

## Across two models

`kills_per_combat_hour`, `deaths_per_online_hour` and `quest_xp_per_played_hour` read metrics
from two models. They sit under `cross_model_metrics` in the model file, as an expression over
metric names (numbers, `+ - * /` and parentheses only). Boring Semantic Layer's own joins go
from one model's rows to another's by a key; the layer already answers every multi-model
question by aggregating each model on its own and joining the results on the `group_by`
columns, so a ratio across two models is computed on that joined row. No fan-out, any grain.
They group and filter by the dimensions both models have (level, character, class, time), and
refuse the others.

## The pages read it too

The character page's numbers used to be counted by the page's own code, next to the layer's.
Now the page takes them from the layer:

| On the page | Metric |
|---|---|
| Time, XP, quests, kills and XP/hour of each level | `played_s`, `level_xp`, `split_quests`, `split_kills`, `xp_per_hour` |
| "A level, over the last 5" | `avg_level_s` on the last 5 whole levels |
| XP an hour (the pace), and the time to the next level from it | `xp_per_hour` on the last 3 whole levels |
| Played since tracking began, kills, quests | `played_s`, `split_kills`, `split_quests` (plus the level in play, live) |
| Each dungeon run's time, XP and XP/hour | `run_s`, `run_xp`, `run_xp_per_hour` by `run` |
| Deaths: how many, by zone, by level | `deaths` |

DuckDB runs when the answers are built (the full view's background builder after new data,
the publisher's static builds), once per character and data, never for a page view. It reads
only the feeds those metrics need (level splits, dungeon runs, and the Recorder's level,
session, zone and death records), not the whole history the analysis copies. On the demo every
answer says exactly what it said before the move (`tests/test_layer_pages.py`, against a
snapshot taken before).

Two definitions the page used to have its own way of are now the layer's, in shared code
rather than through DuckDB:

- **Sessions**: more than 330 s without a Recorder record ends one, for the journal, the
  Overview, the timeline, the MCP's `sessions` tool and the layer's `sessions`
  (`stores.split_sessions`). It used to be 20 minutes or a login on the page, 20 minutes on 5
  minute buckets in the MCP.
- **The route's time for a level**: the class's median over the side's starting races, else
  the median over classes, for the Overview's pace, the MCP and `planned_s` (`Route.level`).
  The page used to take your race's own run when the route export had one.

The hero line's gold is the purse at the last logout, a fact rather than a metric.

Without DuckDB installed (a `pip install` without the `mcp` extra; the image has it), the page
counts those numbers itself, the same way.

## Still out of reach

- **Two characters in one query.** Each character is its own database. Ask twice, once per
  `char`.
- **Repairs, by name.** See [Money spent](#money-spent).

## Evidence for a finding

`propose_finding` takes a `query_metric` call instead of SQL: put the call's arguments in
`finding.metric_query`, and the finding's `{placeholders}` name its output columns. The judge
compiles it, re-runs it and compares every number in `expect`. The kept finding stores the
compiled SQL and its parameters, and the nightly recheck re-runs exactly that.

## play_sql speaks DuckDB now

Same tables, same columns, same row cap and timeout. The dialect differs from SQLite in a few
places that matter:

- `/` divides as decimals: `7/2` is 3.5. Use `//` for whole numbers.
- A comparison doesn't sum: `COUNT(*) FILTER (WHERE kind = 'kill')` or
  `SUM(CAST(kind = 'kill' AS INT))`, not `SUM(kind = 'kill')`.
- `strftime` takes the time first: `strftime(to_timestamp(t), '%H')`.

Findings kept before the switch were written for SQLite: the nightly recheck keeps running
those on SQLite, so none of them breaks.

## The catalogue

<!-- generated:semantic-catalogue -->
<!-- From wowstats/analysis/semantic.yaml: edit it there, then run tools/manual/generate.py. -->

### levels

One row per level: Polestar's split when the level is done, the ding time and the route planner's time.

| Metric | Kind | Unit | Definition | Caveats |
|---|---|---|---|---|
| `split_levels` | simple | levels | Levels with a Polestar split (a time played). | The level in play has no split yet; a partial split counts. |
| `played_s` | simple | seconds | Time at a level, way 1: Polestar's own split, the game's /played seconds spent in the level. | Counts from the moment tracking started: a level marked partial began before it. Offline time is not in it. |
| `calendar_s` | simple | seconds | Time at a level, way 4: wall-clock seconds from the previous ding to this one, offline time included. | NULL for the first tracked level and the level in play. A week off between two dings counts in full. |
| `level_xp` | simple | xp | XP the level took (Polestar split). | From the split: missing for a level the split never closed. |
| `split_quests` | simple | quests | Quests handed in during the level, as Polestar's split counts them. | The split's own count; records.turnin can differ for a partial level. |
| `split_kills` | simple | kills | Kills during the level, as Polestar's split counts them. | The split's own count; records.kill can differ for a partial level. |
| `planned_s` | simple | seconds | The route planner's seconds for the level: for this class, or the median over classes when the class isn't known. | A model of a quick player who knows the route, uncalibrated. NULL when the route reference has no time for the level. |
| `xp_per_hour` | ratio | xp/hour | XP per played hour in the level: level_xp over played_s. | Played time from the split, so idle time in the game counts against it. |
| `vs_route` | ratio | ratio | Played time over the route planner's time: 1.0 is on plan, 2.0 twice as long. | As uncalibrated as planned_s; NULL without a planned time. |
| `avg_level_s` | ratio | seconds | Average played seconds a level: played_s over split_levels (the page's "a level, over the last 5"). | Filter partial = 0 for whole levels: a partial split is short. |
| `over_route_s` | derived | seconds | Seconds played beyond the route planner's time (negative: faster). | As uncalibrated as planned_s; NULL without a planned time. |

Group or filter by: `character`, `class`, `level`, `partial`, `time`.

### timeline

The online time cut at every record: each row an interval inside a login span, with the state at its start.

| Metric | Kind | Unit | Definition | Caveats |
|---|---|---|---|---|
| `online_s` | simple | seconds | Time at a level, way 2: online seconds the Recorder saw, every interval between two records of a login span. | A silence over 330 s ends a span, so a crash or a long loading screen can drop a few minutes. |
| `active_s` | simple | seconds | Time at a level, way 3: online seconds with the AFK flag off. | The game sets AFK after 5 idle minutes: those 5 minutes still count as active. |
| `afk_s` | simple | seconds | Online seconds with the AFK flag on. | Starts 5 minutes into the idle time (the game's own flag). |
| `dungeon_s` | simple | seconds | Online seconds inside a dungeon run. | Inside the instance only: the walk there and the hand-ins are outside (see trip_s). |
| `combat_s` | simple | seconds | Online seconds in combat. | From the combat records' on/off pairs. |
| `dead_s` | simple | seconds | Online seconds from a death to the revive (ghost run, spirit healer). | Death to revive only; the walk back after a spirit healer is alive time. |
| `taxi_s` | simple | seconds | Online seconds on a flight path. | Flight paths only; boats and zeppelins are ride time, not in it. |
| `dungeon_share` | ratio | share | Share of online time spent inside dungeons: dungeon_s over online_s. | 0 to 1. NULL when online_s is 0. |
| `afk_share` | ratio | share | Share of online time with the AFK flag on: afk_s over online_s. | 0 to 1. |
| `combat_share` | ratio | share | Share of online time in combat: combat_s over online_s. | 0 to 1. |

Group or filter by: `character`, `class`, `dungeon`, `guide`, `guide_title`, `hour`, `level`, `session_id`, `step`, `step_title`, `subzone`, `time`, `zone`.

### records

Every Recorder record except positions: kills, deaths, quests, loot and the rest.

| Metric | Kind | Unit | Definition | Caveats |
|---|---|---|---|---|
| `kills` | simple | kills | Creatures the player or pet killed (kill records). | Includes kills tapped by another player (records.extra has tapped). |
| `deaths` | simple | deaths | Deaths (death records). | The zone is where the player died, not where the corpse run ended. |
| `quests_done` | simple | quests | Quests handed in (turnin records). | A quest handed in twice (repeatable) counts twice. |
| `quests_accepted` | simple | quests | Quests accepted (accept records). | Re-accepting after an abandon counts again. |
| `quests_abandoned` | simple | quests | Quests abandoned (abandon records). | Only what the player abandoned; a failed quest is a fail record. |
| `kill_xp` | simple | xp | XP from kills (kill records' xp_reward). | Rested bonus included, as the game reports it. |
| `quest_xp` | simple | xp | XP from quest hand-ins (turnin records' xp_reward). | Discovery XP is apart (explore records). |
| `quest_money_copper` | simple | copper | Money quest hand-ins paid, copper. | Quest rewards only; loot, sales and costs are not in it (see gold_delta_copper). |
| `quest_xp_share` | derived | share | Share of kill and quest XP that came from quests: quest_xp over quest_xp + kill_xp. | Leaves out discovery XP and dungeon bonus XP. |
| `deaths_per_100_kills` | ratio | deaths/100 kills | Deaths per 100 kills. | NULL without kills; a death to a fall or drowning counts too. |

Group or filter by: `character`, `class`, `hour`, `kind`, `level`, `session_id`, `time`, `zone`.

### sessions

Login spans: a silence of more than 330 s between Recorder records ends one.

| Metric | Kind | Unit | Definition | Caveats |
|---|---|---|---|---|
| `session_count` | simple | sessions | Login spans. | A /reload doesn't split a span; a crash longer than 330 s does. |
| `session_s` | simple | seconds | Seconds from a span's first record to its last. | Close to online_s for the same spans, without the timeline's cut. |
| `session_xp` | simple | xp | XP gained during the spans (xp records). | From the XP bar every 5 minutes: a span shorter than that can show 0. |
| `avg_session_s` | ratio | seconds | Average span length: session_s over session_count. | One long evening moves it a lot on few sessions. |
| `xp_per_session_hour` | ratio | xp/hour | XP per hour of login span: session_xp over session_s. | AFK time inside a span counts against it. |

Group or filter by: `character`, `class`, `level_start`, `session_id`, `start_hour`, `time`, `weekday`.

### dungeon_runs

Polestar's instance runs: one row per run, enter to leave.

| Metric | Kind | Unit | Definition | Caveats |
|---|---|---|---|---|
| `run_count` | simple | runs | Dungeon runs. | A wipe and re-entry can show as two runs. |
| `run_s` | simple | seconds | Seconds inside the instance, as Polestar times the run (the `dungeons` tool's number). | Inside only. The trip around it: trip_s. |
| `run_xp` | simple | xp | XP gained inside the instance. | Kills inside; the quests handed in after are trip XP (trip_xp). |
| `run_xp_per_hour` | ratio | xp/hour | XP per hour inside: run_xp over run_s (the `dungeons` tool's xp_per_hour). | Leaves out the quest XP the run unlocks. |

Group or filter by: `character`, `class`, `dungeon`, `level`, `run`, `time`.

### dungeon_trips

A dungeon trip: from the first pickup guide step to the last hand-in around one run, online time.

| Metric | Kind | Unit | Definition | Caveats |
|---|---|---|---|---|
| `trip_count` | simple | trips | Dungeon trips. | One per run; bracket says whether guide steps widened it. |
| `trip_s` | simple | seconds | Online seconds from the trip's start to its end: pickup, travel, inside, travel back, hand-in. | Online time only: logging out mid-trip doesn't count. |
| `trip_inside_s` | simple | seconds | Online seconds inside the instance during the trip. | Online time, so it can differ from run_s by a few seconds. |
| `trip_travel_s` | simple | seconds | Online seconds getting to the instance and back to the first hand-in. | Needs guide steps for the edges: an `instance` bracket trip has 0. |
| `trip_xp` | simple | xp | XP inside the instance plus XP from the trip's quests handed in during the trip. | A trip quest handed in a day later is not in it. |
| `trip_xp_per_hour` | ratio | xp/hour | XP per online hour of the whole trip: trip_xp over trip_s. | The number to compare with questing XP/hour; run_xp_per_hour flatters dungeons. |
| `trip_inside_share` | ratio | share | Share of the trip spent inside the instance. | 0 to 1. |

Group or filter by: `bracket`, `character`, `class`, `dungeon`, `level`, `time`.

### snapshots

The character snapshots the shipper sends at logout and /reload: money, XP, bind.

| Metric | Kind | Unit | Definition | Caveats |
|---|---|---|---|---|
| `snapshot_count` | simple | snapshots | Snapshots (logouts and reloads). | Only what the shipper read: a crash leaves none. |
| `gold_delta_copper` | derived | copper | Money on the group's last snapshot minus money on its first, copper. | Not additive: a week's delta is not the sum of its days (the change between days is in neither). Needs two snapshots in the group. |

Group or filter by: `character`, `class`, `level`, `time`, `zone`.

### quests

Each quest the log held: its accept paired with its hand-in or abandon, and the online time it took.

| Metric | Kind | Unit | Definition | Caveats |
|---|---|---|---|---|
| `quest_count` | simple | quests | Quests (one per accept, or per hand-in with no accept seen). | A repeatable quest counts once per accept. |
| `quest_s` | simple | seconds | Time on quests: every online second while a quest was in the log, shared equally among the quests open in that second. | Additive: the quests' times sum to the online time with a quest open, nothing counted twice. It can't tell which quest the player worked on, so a quest that sits in the log takes its share of the others' time. NULL for untimed quests. |
| `quest_open_s` | simple | seconds | Online seconds while the quest was in the log, in full for each open quest. | Not additive across quests: ten open quests each count the same evening. Use quest_s for time spent. |
| `quest_wall_s` | simple | seconds | Calendar seconds from accept to hand-in or abandon. | Counts the nights and days off in between: never a time spent. |
| `quest_reward_xp` | simple | xp | XP the hand-ins paid (untimed quests included). | Same hand-ins as quest_xp in records, grouped by quest. |
| `quest_reward_copper` | simple | copper | Money the hand-ins paid, copper. | Quest rewards only. |
| `done_quest_xp` | simple | xp | XP of the done quests (accept and hand-in both seen). | Leaves out untimed quests, whose time is unknown. NULL without a done quest. |
| `done_quest_s` | simple | seconds | quest_s of the done quests. | Abandoned and open quests' time is in quest_s, not here. NULL without a done quest. |
| `quest_xp_per_min` | ratio | xp/minute | XP per minute on quests: done_quest_xp over done_quest_s. | Done quests only. Time is the shared quest_s, so a quick quest done while ten sat in the log looks quicker than it was, and one carried for days looks slower. Kill XP on the way is not in it. |
| `quests_open_avg` | ratio | quests | How many quests were in the log on average while these were: quest_open_s over quest_s. | 1 means one quest at a time; read it next to quest_s. |

Group or filter by: `character`, `class`, `done_why`, `level`, `level_accepted`, `quest`, `quest_id`, `status`, `time`, `zone`.

### gold_moves

The purse between two consecutive snapshots (logout or /reload): what it moved and the part the records name (quest money).

| Metric | Kind | Unit | Definition | Caveats |
|---|---|---|---|---|
| `gold_moves` | simple | moves | Moves between two snapshots. | A crash leaves no snapshot: the next move spans both sessions. |
| `gold_moved_copper` | simple | copper | How much the purse moved, copper: the sum of each move's change. | Additive, unlike gold_delta_copper: a week is the sum of its moves. A move counts in the level and day of its first snapshot. |
| `gold_quest_copper` | simple | copper | Money hand-ins paid inside the moves, copper. | Quest rewards the Recorder saw; one handed in with the Recorder off is unexplained. |
| `unexplained_gold_delta` | simple | copper | The purse's change no record explains, copper: gold_moved_copper minus gold_quest_copper. | Loot coin, vendor and auction sales and mail in, less repairs, vendor purchases, training, flights, auction deposits and mail out, all together. No addon the kit reads saves spending (the Recorder has quest money only; Auctionator saves prices, not sales), so this is never repairs. A move counts in the level of its first snapshot: filter dings = 0 for moves inside one level. |

Group or filter by: `character`, `class`, `dings`, `level`, `time`.

### Across two models

Each model aggregates on its own to the question's group_by, the results join on those columns, then the expression runs on the joined row: no fan-out. They group and filter by the dimensions their models share.

| Metric | Kind | Unit | Expression | Definition | Caveats | Group or filter by |
|---|---|---|---|---|---|---|
| `kills_per_combat_hour` | ratio | kills/hour | `kills / (combat_s / 3600)` | Kills per hour in combat: kills (records) over combat_s (timeline). | Combat time includes fights that ended in no kill (fleeing, dying, a tapped mob). NULL without combat time. | `character`, `class`, `hour`, `level`, `session_id`, `time`, `zone` |
| `deaths_per_online_hour` | ratio | deaths/hour | `deaths / (online_s / 3600)` | Deaths per online hour: deaths (records) over online_s (timeline). | Online time includes AFK; filter or compare with active_s. | `character`, `class`, `hour`, `level`, `session_id`, `time`, `zone` |
| `quest_xp_per_played_hour` | ratio | xp/hour | `quest_xp / (played_s / 3600)` | XP from quest hand-ins per played hour in the level: quest_xp (records) over played_s (levels). | Levels only share level, character, class and time with records: group by one of those. A partial level's split is short. | `character`, `class`, `level` |

### Dimensions

| Dimension | Models | Definition |
|---|---|---|
| `bracket` | dungeon_trips | guides (lookup guide steps bracket it) or instance (enter and leave only). |
| `character` | levels, timeline, records, sessions, dungeon_runs, dungeon_trips, snapshots, quests, gold_moves | The character (one per query: the char argument). |
| `class` | levels, timeline, records, sessions, dungeon_runs, dungeon_trips, snapshots, quests, gold_moves | The character's class, from its latest session record. |
| `dings` | gold_moves | Level-ups between the two snapshots: 0 for a move inside one level. |
| `done_why` | quests | For 'done (inferred)': the evidence, e.g. a reward only that quest gives worn or in the bags, or Questie's completed list. NULL otherwise. |
| `dungeon` | timeline, dungeon_runs, dungeon_trips | Dungeon name while inside a dungeon run, else NULL. |
| `guide` | timeline | The Polestar guide active then (the last step record), its id. NULL before the first step record. |
| `guide_title` | timeline | That guide's title, from the route reference (NULL when the reference doesn't have the guide). |
| `hour` | timeline, records | Hour of day 0-23, local time. |
| `kind` | records | Record kind (record_kinds: kill, death, turnin, accept...). |
| `level` | levels, timeline, records, dungeon_runs, dungeon_trips, snapshots, quests, gold_moves | Character level. |
| `level_accepted` | quests | Level at the accept (NULL for untimed). |
| `level_start` | sessions | Level at the start of the span. |
| `partial` | levels | 1 when Polestar's split began partway through the level (tracking started then), 0 for a whole level, NULL with no split (the level in play). |
| `quest` | quests | Quest title (the records' own; "Quest <id>" when none names it). Two quests with one title group together: add quest_id. |
| `quest_id` | quests | Quest id. |
| `run` | dungeon_runs | One run, named by when it started (unix seconds): group by it to list the runs one by one. |
| `session_id` | timeline, records, sessions | Login span (sessions). |
| `start_hour` | sessions | Hour of the start, local time. |
| `status` | quests | done (accept and hand-in seen), abandoned, open (still in the log), untimed (handed in, accepted before tracking began) or 'done (inferred)' (still in the log, but handed in with the Recorder off: done_why says what shows it). |
| `step` | timeline | The guide's step number then (from 1). Group by guide too: step numbers restart in each guide. |
| `step_title` | timeline | That step's title, from the route reference (NULL when the reference doesn't have it). |
| `subzone` | timeline | Subzone text. |
| `time` | levels, timeline, records, sessions, dungeon_runs, dungeon_trips, snapshots, quests, gold_moves | When the level ended (the ding), local time. NULL while the level is in play. Grains: day, week, month. |
| `weekday` | sessions | Mon..Sun of the start, local time. |
| `zone` | timeline, records, snapshots, quests | Zone name, as the last zone record said (inside a dungeon, the game usually names the dungeon). |

<!-- /generated:semantic-catalogue -->
