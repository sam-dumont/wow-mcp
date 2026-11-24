# The shipper's events

**For:** contributors and anyone writing their own sender. **At the end:** you know every event and field the shipper sends.

What `shipper/wow_stats.py` sends and what `wow-stats ui` expects to read. Any
receiver that stores these lines in VictoriaLogs (with `feed` as the stream
field) works with the UI; the compose kit's is `POST /ingest` on the UI
itself.

```
 the PC you play on                           server
 shipper (launchd / Task Scheduler)  --POST-->  ui /ingest --> VictoriaLogs /insert/jsonline
   1 archive WTF/ to a git repo                 (validates, all or nothing)
   2 extract new events to a spool
   3 ship the spool, then move it to sent/
```

## Transport

- `POST <url>` with basic auth (user `wow` by default), body newline-delimited
  JSON, `Content-Type: application/x-ndjson`. The shipper splits files into
  POSTs of at most 4 MB; the UI's ingest refuses more than 8 MB (413).
- 2xx means stored. Anything else: the shipper keeps the spool file and tries
  again on its next run, so a server outage loses nothing.
- The UI's ingest accepts a POST only when every line is a JSON object with a
  string `_time` and a `feed` starting with `wow-` (otherwise 400, nothing
  stored). It sets `component` to `wow` on every line (never the sender's
  value), and forwards the lines to
  VictoriaLogs' `/insert/jsonline` with `_stream_fields=component,feed`,
  `_time_field=_time`, `_msg_field=_msg` and
  `Content-Type: application/stream+json` (with any other type VictoriaLogs
  answers 200 and stores nothing).
- VictoriaLogs drops events older than its retention on arrival: the compose
  kit sets 100 years, its default is 7 days.

## Fields on every event

- `_time`: RFC 3339 UTC with milliseconds, `2026-11-17T15:14:03.120Z`: the
  event's own time, not when it was sent ("Times" below says which one).
- `at_basis`: only on an event whose `_time` is approximate. `commit` means
  it is the archive's capture time, an upper bound (below); `run` means the
  snapshot had no time at all and carries the shipper's clock. Readers pick
  a snapshot with an exact time before an approximate one.
- `feed`: `wow-recorder`, `wow-level`, `wow-dungeon`, `wow-character`,
  `wow-gear`, `wow-bags`, `wow-quests`, `wow-player`, `wow-prices`, `wow-talents` or `wow-combatlog`.
- `char`: `Name-Surname` as in the WTF folder, `Testchar-Demo`. Polestar's
  account data writes `Testchar Demo` (a space): the UI treats both as one
  character.
- `_msg`: a short human line (Recorder events: the record line, cut to the
  fields the shipper knows for its kind). Never a raw combat log line.
- `source_id`: unique per event, so a resend (`--replay`, `--reextract`,
  `--backfill`) is harmless:
  `rec:<charId>:<chunkSeq>:<line>`, `level:<char>:<level>`,
  `dungeon:<char>:<entered>`, `character:<char>:<seen>`,
  `gear:<char>:<hash>:<epoch>` (the sheet and when it was read: an outfit
  worn again later is a new event; shippers up to 0.7.0-rc.5 sent
  `gear:<char>:<hash>`, which the UI tells apart by `_time`),
  `player:<source>:<player>:<epoch>`,
  `price:<realm>:<item key>:<day>`,
  `cl:<file>:<byteOffset>`. The UI keeps the newest `_time` per `source_id`
  (then the highest `rev`).
- Numbers are JSON numbers, booleans JSON booleans, names in snake_case.
- `demo: true` only on `dev/seed.py`'s made-up events: the UI shows a DEMO DATA
  banner for those characters.

Never sent: the Battle.net account id or the account folder name.

## Times

Four different clocks, and an event's `_time` is always the earliest one the
shipper knows exactly:

| Name | What it is | Example |
|---|---|---|
| event time | when it happened in the game | a Recorder `turnin` at 19:02:11 |
| observation time | when a source wrote down what it saw: a snapshot file's modification time, a logout stamp in its content | Questie's file saved at logout, 2026-11-17T07:20 |
| capture time | when the shipper archived that file (the commit in `~/WoWArchive/wtf`) | a few minutes after the logout |
| processing time | when the shipper extracted or sent it | the run, or a re-extraction weeks later |

Recorder records, levels and dungeons carry their event time. Snapshots
(`wow-quests`, `wow-bags`, `wow-gear` without a Polestar logout, `wow-character`)
carry their observation time: the quest was done by then, maybe long before.
Processing time is never an event's `_time`, except as a last resort when a
snapshot has no time at all (then `at_basis: "run"`).

Git keeps no file times, so a re-extraction from the archive can't read them
from the rebuilt files. Each archive commit carries a capture manifest,
`_capture.json` next to the WTF mirror: every file a re-extraction reads, with
the modification time (ns) the live run saw, and each character folder with
its newest file's time (what dates a character Polestar doesn't track). A
re-extraction puts them back, so the same archive gives the same `_time` (and
the same `gear:` id) every time, and the same events the live runs sent.

An archive commit made before the manifest existed (shippers up to
0.7.0-rc.7) has nothing to put back: its files are dated by the commit's
time, the capture time, and those events carry `at_basis: "commit"`. Read
that `_time` as "seen by then", never as the moment. wowstats.quests words
that evidence "by <day>" instead of "on <day>" and marks it `t_by`.

## Per feed

**`wow-recorder`** (Polestar Recorder chunks): `kind` (`pos`, `unit`,
`combat`, `step`, `kill`, `rep`, `loot`, `offer`, `progress`, `zone`, `xp`,
`skill`, `craft`, `accept`, `turnin`, `complete`, `abandon`, `death`, `level`,
`taxi`, `land`, `ride`, `vendor`, `trainer`, `rest`, `afk`, `encounter`,
`enter`, `session`, `text`...), `map_id`, `x`, `y` when present, `zone` (the
map name when the shipper can resolve it), and per kind what the record holds:
`quest_id`, `level`, `xp`, `xp_max`, `xp_gained` (the change since the
character's previous `xp` record), `money_copper`, `xp_reward`, `npc_id` (with `npc_name` from LibForeverData's NPC table on death, unit, kill, rare and loot records), `item_names` on loot records (a JSON object, item id to name, from LibForeverData's item table; the loot history),
`step`, `guide`. Only known kinds and their known fields go
(`shipper/wow_stats.py` `KINDS` and `REC_DERIVED`): a field past a kind's
spec is dropped, and a kind the shipper doesn't know goes with its time and
`kind` alone, so a Recorder version that records more (other players, say)
sends nothing new until the shipper learns it. Shippers before 0.7.0 sent
the rest in `args`; `/ingest` drops it.

- `duration_s`, computed by the shipper: on `step` (since the previous
  step), and on `zone` and `taxi` for servers before 0.7.0 only. A zone's
  and a flight's time depend on records the shipper may not have read yet,
  so the server works them out from the stored records instead: a zone to
  the next zone record of the session, else the session's last record
  (`wowstats/stores.py` `zone_time`); a flight to the first `land` before
  the next `taxi`, the land's own `seconds` when it has them
  (`taxi_time`).
- `quest_name` on `accept`, `turnin`, `complete`, `abandon` and `fail`: from
  LibForeverData's quest table (`Interface/AddOns/LibForeverData/Data/Quests.lua`),
  else Polestar_QuestDB's, else the quest title the Recorder saw in a `text`
  record. Missing when none knows the quest.
- `login: true` on the first record after more than 2 minutes without any.
  The Recorder starts every 5-minute chunk with a `session` record, so those
  can't tell a login from the next chunk; this flag can.

**`wow-level`** (`Polestar.lua`, `PolestarCharDB.qol.xp.splits[level]`):
`level`, `seconds` (played time in that level), `xp`, `quests`, `kills`,
`partial`; `_time` is the ding that ended the level. Polestar writes a split
when a level ends, so the level being played has none yet. `partial: true`
marks a split where tracking started partway through the level: the ding is
real, the played time is short.

**`wow-dungeon`** (`PolestarCharDB.instanceRuns`): `dungeon_id`, `name`,
`level`, `seconds`, `xp`; `_time` is when the run started.

**`wow-character`** (account `PolestarDB.alts.chars`): a snapshot saved at
logout: `level`, `xp`, `xp_max`, `money_copper`, `zone`, `race`, `class`,
`faction`, `realm`, `bind`, `resting`; `_time` is the snapshot's time.
Shippers before 0.7.0 could add `looks` (Narcissus): still stored,
read by nothing (below).

Characters Polestar doesn't track get one too, so every character of the
account shows up: one event each time what is known about it changes
(`source_id` carries a hash of the fields). They come from the character's
WTF folder (`<realm>/<char>/`, written at each login, even with every addon
off) and Syndicator (`SYNDICATOR_DATA.Characters[...].details` and `money`):
`realm` (the folder's name), `race`, `class`, `faction`, `money_copper`, and `source`,
the files that know it (`folder`, `syndicator`, comma-separated). `_time` is
the newest file in the character's folder. A backfill of archived versions
leaves these to the current tree: a rebuilt version holds only the addon
files the extractors read, not the folder's other files that date it.

**`wow-character` with `kind: played`** (optional, Altoholic): `/played` as
DataStore_Characters saved it. Fields: `played_total_s` and
`played_level_s` (DataStore's `played` and `playedThisLevel`, the game's
answer to the `/played` request it sends at every login and `/reload`),
`level` (bits 0-6 of `BaseInfo`, updated at each ding), `logged_out`
(`lastLogoutTimestamp` below 5000000000), `source: altoholic`; `_time` is
`lastUpdate`, the save (the logout). The values are valid for an earlier
moment, the last login or `/reload` before it, which DataStore doesn't
save: `lastUpdate` is rewritten at logout to the same time as
`lastLogoutTimestamp`, so `played + (lastLogoutTimestamp - lastUpdate)`
adds nothing. The UI never needs that moment (below: it anchors to a level
start). `source_id` is `played:<char>:<played_total_s>`. The character
comes from the entry's `name` ("First Last") else its key in
`DataStore_CharacterIDs.Set` (`Default.<realm>.<name>`, any realm; never
`List`, whose indexes shift when a character is deleted). Files: account `SavedVariables/DataStore.lua`
and `DataStore_Characters.lua`; format read from Thaoky's source
(github.com/Thaoky/DataStore and DataStore_Characters, the releases
Altoholic_Forever depends on). Every other reader of
`wow-character` leaves these out (`-kind:=played`): they carry no gold,
zone or race.

**`wow-character` with `source: altoholic`, no `kind`** (optional, Altoholic):
the character sheet as DataStore_Characters saved it, one event each time it
changes, for every character of the roster Altoholic saw logged in. Fields:
`money_copper` (`money`), `xp`, `xp_max`, `rested_xp` (`XP`, `maxXP`,
`restXP`), `zone`, `subzone`, `bind` (`zone`, `subZone`, `bindLocation`),
`level` (bits 0-6 of `BaseInfo`), `logged_out`. Never `guildRankIndex` or
`BaseInfo`'s other bits (class, race, gender). `_time` is `lastUpdate`.
`source_id` is `altoholic:sheet:<char>:<hash>`. The readers of the sheet
take it with Polestar's; one within 2 minutes of a Polestar sheet with the
same money and level is that sheet (the analysis' `snapshots`).

**`wow-altoholic`** (optional, Altoholic): one event per character and
module each time what that module saved for it changes, `source: altoholic`,
`_time` the module's `lastUpdate` for the character (lockouts: the newest
scan). Only the account's own roster; the fields below only (the shipper's
`ALTO_KEYS`, which `/ingest` applies too). `source_id` is
`altoholic:<kind>:<char>:<hash>`. Format read from Thaoky's source (the
2026-10-14 DataStore modules Altoholic_Forever 1.60.005 loads; Forever runs
their mainline branch).

- `kind: spells` (`DataStore_Spells.lua`, `Spells[tab]`): `spells`, a list
  of `[spell id, level]`, level 0 for a known spell, else the level a spell
  still to come opens at (bits 0-7; bits 8+ the spell id); `class`
  (`englishClass`), `known`, `future` (the counts). Tab names aren't sent.
- `kind: reps` (`DataStore_Reputations.lua`, `Factions`): `factions`, a list
  of `[faction id, reaction 1-8, points from the start of neutral]` (bits 3
  negative, 4-7 standing, 8+ points), normal factions only; `n`. Never
  `guildName` or `guildRep`.
- `kind: stats` (`DataStore_Stats.lua`): the "|"-joined strings as numbers:
  `health_max`, `power_type`, `power_max`; `strength`, `agility`, `stamina`,
  `intellect`, `armor` (Base: four stats, no spirit); `melee_dmg_min`,
  `melee_dmg_max`, `melee_speed`, `melee_ap`, `melee_hit_rating`,
  `melee_crit_pct`, `expertise`; the same for `ranged_*` without expertise;
  `spell_bonus_dmg`, `spell_bonus_heal`, `spell_hit_rating`,
  `spell_crit_pct`, `spell_haste_rating`, `mp5`; `dodge_pct`, `parry_pct`,
  `block_pct`, `resilience` (Defense); `honorable_kills`,
  `dishonorable_kills` (PVP, lifetime). No resistances: DataStore saves none.
- `kind: lockouts` (`DataStore_Agenda.lua`, `SavedInstances` and
  `BossKills`): `lockouts`, a list of `{instance, instance_id, expires
  (scan time + reset seconds), extended, raid, bosses: [[boss, 0/1]]}`;
  `weekly_reset_day` (0 Sunday) and `weekly_reset_hour` from the account's
  Agenda options; `n`. Never `Contacts` or `Calendar`.

A module that isn't there, or holds nothing for a character, sends nothing.
Never read: Agenda's Contacts and Calendar, Containers' and Inventory's
guild tables, Mails, Auctions, `Altoholic_Sharing_Options`,
`DataStore_GuildIDs`, `DataStore_CharacterGuilds`.

**`wow-foreverdb`** (the PvP rank; the feed keeps the name of its first
source): `kind: pvp`, one event per character each time its PvP rank
changes, from the newest of three saves (a tie goes to the exact time:
ForeverDB, Altoholic, Syndicator); the same rank from another addon sends
nothing. `source` says which:

- `syndicator`: `SYNDICATOR_DATA.Characters[<char>].currencies[3473]` in
  `Syndicator.lua` (the currency list's discovered entries, rescanned at
  login and on each currency change). `_time` is the character folder's
  newest file (its last login or logout), else the file's own time.
- `altoholic`: `DataStore_Currencies_Characters[<id>].Totals[3473]` in
  `DataStore_Currencies.lua`, bits 0-19 (bits 20+ are this week's
  earnings, not read). `_time` is the row's `lastUpdate`.
- `foreverdb` (optional, ForeverDB 0.38.0+):
  `ForeverDB_DB.armory[<GUID>].pvp` in `ForeverDB.lua` (taken at login,
  level up and logout), `_time` the snapshot's own `time`.

Currency 3473 is the only currency read, by id; nothing else of either
currency table leaves the shipper. The client lists only currencies the
character has met: without 3473 there is no event (unknown, not rank 0).
Only the account's own roster; the fields below only (the shipper's
`FDB_KEYS`). `source_id` is `<source>:pvp:<char>:<hash>`.

- `pvp_rank` (0-14): the game's rank (`rank`, UnitPVPRank) when the client
  has it, else `renown`; `rank_from` says which (`rank` or `renown`).
- `renown`: currency 3473 "Renown - PvP Rank" (0-14), the Forever client's
  rank track. The client of 2026-11 has no UnitPVPRank, so this is the
  rank there.
- `highest_rank`, `honorable_kills`, `honor_points` when ForeverDB saved
  them, whichever source gave the rank.

Never read: the rest of the snapshot (guild roster, gear, talents),
ForeverDB's battleground history, `dk` (-64 with no kill in the Forever
client: not a count). The gear advice takes the newest event as the rank
held (`gear.standings_of`).

**`wow-gear`** (the character sheet): one event each time it changes, for
every character of the account (its folder, Polestar's alts, Syndicator). `_time` is the character's logout snapshot in
`PolestarDB.alts.chars` (the moment the game wrote these files), else the
newest of the files' modification times (an observation time, "Times"). `source_id` carries a hash of the
content, so a sheet that goes back to an earlier state is sent again with
the newer time.

- `items`: the equipped items (left out when Syndicator doesn't know the
  character: unknown, readers keep the last sheet's), from the account's `Syndicator.lua`
  (`SYNDICATOR_DATA.Characters[...].equipped`, a Lua array whose index is the
  inventory slot + 1): `slot` (1 head ... 19 tabard), `slot_name`, `item_id`,
  `name` (from the item link), `quality` (0 poor, 1 common, 2 uncommon,
  3 rare, 4 epic, 5 legendary), `item_level` and `req_level` from
  Polestar_ItemDB (`Forever.lua` over `Items.lua`; null when the item isn't
  there).
- `item_level`: the average over the equipped items, shirt and tabard left out.
- `professions`: `skill_id`, `name`, `rank`, from
  `PolestarDB.alts.chars[...].profs` (saved at logout). With Altoholic
  installed (shippers after 0.9.0), also `cap` and `cap_at`: the cap from
  `DataStore_Crafts.lua`'s `Ranks` (rank + cap << 16, the game's skill list
  rescanned at each login and skill-up) and that row's `lastUpdate` (unix
  seconds); a profession only DataStore knows is added. The server takes the
  highest cap that covers the rank, this one or the Recorder's `skill`
  records (`max_rank`), else the trainer tiers (wowstats/profcaps.py); only
  `cap` says what the character has now, so a move built on the others
  hedges. Shippers before 0.7.0 also sent
  `max_rank` and `max_rank_at` from CharacterExport-Forever: still stored,
  read by nothing.
- `wanted`: the upgrades the player tracks in GearQuestForever (the
  character's `GearQuestForeverCharDB.hunts` with status `tracked`):
  `item_id`, `name`, `item_level`, `req_level`, `slot_name`, `level`.
  ForeverLevelingBiS keeps no suggestions in its SavedVariables (it computes
  them in game), only the spec.
- No Polestar gear settings. Shippers up to 0.9.0 sent
  `polestar_spec` (Polestar's Stat weights setting), `gear_adjust` (the
  edits in its Adjust weights window) and `min_upgrade` (its smallest
  upgrade worth showing): still stored, read by nothing. The gear scores are
  ForeverDB.net's weights only (docs/gear-reference.md).
- No talents. They come from Battle.net's API only (the exporter's
  `/specializations`, below). Shippers before 0.7.0 sent `talents`,
  `talent_ranks`, `talent_nodes`, `talents_level` and `talents_date` from
  Vulcan SimBench's `/simx` export: still stored, read by nothing.
- `spec` (ForeverLevelingBiS, else GearQuestForever's pick) and `spec_from`, the addon it came from
  (`ForeverLevelingBiS` or `GearQuestForever`), `class`, `race`, `faction`.
  No `sex` or `guild` since 0.7.0: both come from Battle.net's profile
  (the exporter). Older events still carry them; nothing reads them.
- `recipes`: the recipes the character knows, per profession, from
  Altoholic's `DataStore_Crafts.lua` (account SavedVariables), as a list of
  `{profession, known: [recipe spell IDs], seen}` (`seen`: how many recipes
  that profession's window listed). Only professions whose window the
  character opened with Altoholic installed; missing otherwise. Gear advice
  says "learn the pattern first" or "find the recipe" from it, and never
  "make it now" without it.
- `class_train_level`: the level of the last class trainer visit, from
  Polestar's `PolestarCharDB.classTrainVisit` (the character's
  SavedVariables), for the planner's class trainer errand.

Never sent in `wow-gear`: the money Syndicator keeps next to the gear (it
goes in `wow-character`, for characters Polestar doesn't track), bag and
bank contents (`wow-bags`, below).

**`wow-talents`** (the build, optional): one event each time a character's
build or level changes in the account's `WowSimsExporter.lua` (WoWSims
Exporter: `WSEDB.profiles.*.savedCharacters`, which keeps each character's
latest save only; never `WSEDB.global`, whose `inspectHistory` and
`groupSync` hold other players). `talents` is the addon's string (one digit
per talent in the sim's order, `-` between the three trees, trailing zeros
dropped, `"0"` for none), with `level`, `class`, `spec` and
`addon_version`; `source` is `wowsims`, `_time` the save's own time. A save
the shipper can't match to one character of the account is skipped, never
guessed. The server reads the string with the addon's talent table
(`data/wowsims-talents.json`) into the character page's talent history.

**`wow-bags`** (what the character holds): one event each time the set of
item IDs in its bags or its bank changes, from the account's `Syndicator.lua`
(`SYNDICATOR_DATA.Characters[...].bags` and `.bank`, Lua arrays of
containers of slots; the bank as it was the last time the character opened
it). `bank` is left out when Syndicator holds no bank (never opened, or its
cache lost): unknown, and readers keep the last bank they saw. `bags` and `bank` are sorted lists of item
IDs: no counts, no links, nothing else from the slots. A character whose
bags Syndicator hasn't scanned yet sends nothing; one that holds nothing
sends empty lists, so an older list doesn't stay the newest. `_time` is
`Syndicator.lua`'s modification time. The newest one is what the character
has: the gear advice lists a held upgrade with its bag or bank as its only
source ("equip it"), and so do the Overview, the plans and the MCP; the
Dungeons panel marks a drop obtained (worn, in the bags or in the bank). No
view shows the lists themselves. Both tiers' allow lists stay as they were:
`/zogzog/` already has the advice's `sources[].kind` and the panel's
`where`; the public site has no advice.

**`wow-quests`** (the quests the game says are completed): `kind` is
`completed`, one event per character and `source` each time that source's
set changes. The Recorder only sees the quests handed in while it runs, so
these carry the ones done before Polestar. `quest_ids` is the sorted list,
`n` its length.

- `source: questie`: Questie's `QuestieConfig.char[<"First Surname">].complete`
  (account `Questie.lua`), the game's full set as of the last login with
  Questie on. `_time` is the file's modification time.
- `source: altoholic`: DataStore_Quests' `DataStore_Quests_History[<id>].Quests`
  (account `DataStore_Quests.lua`), the game's set rebuilt 5 s after each
  login, 64 quests per number. The client saves each number with 16
  significant digits and the addon builds it in a double, so in a slot whose
  high bits are set the low ones are lost: only the bits no rounding can have
  changed are sent (`unknown` counts the others, `size` is the game's own
  count). Never sent when the addon's `AutoUpdateHistory` option is off.
  `_time` is its `LastUpdate`.

wowstats.quests joins them with the Recorder's hand-ins, the game's
"already done" flags in `offer` records, the gear only one quest gives
(held: worn, bags or bank, on any gear or bags snapshot), the gear
Battle.net says the character wears now (read live, never stored, never in
the analysis' database) and the quest chains (a quest done means the quests
it needed were done), into the one
"done" set the Dungeons panel, the plan, the gear advice and the MCP use. A
quest the Recorder saw accepted and never handed in, that this set says is
done, closes as "done (inferred)" with the reason (the analysis' `quests`
table, the plan's open quests). No page shows the lists.

**Appearance.** From Battle.net's profile API only (the exporter, below),
for every character it knows; any other character gets its race's default
look. The shipper sends none. The ids are the game's
(ChrCustomizationOption and ChrCustomizationChoice), the ones the 3D view's
recipe reads. A look that doesn't fit the model (a race change) falls back
to the default per option. Shippers before 0.7.0 could send `looks`
on `wow-character` (Narcissus's barber shop): still stored, read by nothing.

**`wow-player`**: one event per sighting of another player, from Polestar's
probe list (`context: seen`: players you targeted or moused over) and the
combat log (`grouped`, or `combat` for a player your log saw nearby, once
per player per 10 minutes): `player` (`Name-Surname`, the character-folder
form), `context`, `source`, `zone` when known. The People tab, the journal's
"players met" and the MCP's `players` reads these; nothing else about another
player is sent (`PLAYER_KEYS`): no realm, no GUID, no position. Shippers
before 0.7.0 also sent `player_realm` and `map_id`; `/ingest` drops them.
Player names are fields, never stream fields. Full view only.

**`wow-prices`** (the account's `Auctionator.lua`,
`AUCTIONATOR_PRICE_DATABASE`): one event per item and scan day, from the
player's own auction house scans.

- Fields: `item_id`, `item_key` (Auctionator's key: `2592`, or `g:4306:0` for
  some items), `item_name` (LibForeverData's or Polestar_QuestDB's item table),
  `price_copper` (the day's lowest price), `price_high_copper`, `available`
  (items listed), `scan_day` (`YYYY-MM-DD`), `realm`, `faction` (only for
  houses Auctionator keys as "Realm Faction"). `_time` is the scan day's local
  midnight.
- Each realm's table is a CBOR string (Auctionator 11+); the shipper decodes
  it with the standard library. Days count from 2020-01-01 local time.
- Within a day the numbers only widen (the low only goes down, the high and
  the listed count only go up). The shipper merges the file, its `.bak` and
  what it sent before, and sends the day again with `rev` + 1 when it changed.
  The UI keeps the highest `rev`.
- The shipper only reads the file (in WTF, or the archived versions during a
  `--backfill`), never writes it. Full view only.

**`wow-combatlog`** (`Logs/WoWCombatLog*.txt`, when combat logging is on):
parsed fields only, never the raw line (`CL_KEYS`): `event`
(`SPELL_DAMAGE`...), `zone`, and `source_name`/`dest_name` when the line has
the standard prefix. A name is your own character's or a creature's;
any other player is `another player`, with no name, realm or GUID. `_msg`
is built from those (`SPELL_DAMAGE Testchar Demo-Realm > Young Wolf`),
`_time` from the line's own timestamp (the PC's local time, converted to
UTC). No page reads this feed yet; the players it saw go out as `wow-player`
events. Shippers before 0.7.0 sent the raw line: `/ingest` parses it again
the same way and stores only these fields (`wowstats/otherplayers.py`).

**Stored before 0.7.0.** Every shipper up to 0.6.1 sent other players' data
that 0.7.0 no longer sends, and `/ingest` stored it as it came. To find it
(VictoriaLogs deletes whole events, so read what a filter selects before
deleting anything):

| What | LogsQL |
|---|---|
| Raw combat log lines (every name, realm and GUID in range) | `feed:="wow-combatlog" _msg:~"^[0-9]{1,2}/[0-9]{1,2}"` |
| Recorder fields the shipper didn't know (`args`) | `feed:="wow-recorder" args:*` |
| Other players' realms and the sightings' map ids | `feed:="wow-player" (player_realm:* OR map_id:*)` |

The combat log events have no feature reading them, so the first set can
go whole. The other two are real records with one field too many:
deleting them loses the record.

**`wow-setup`** (the shipper's own check, no character): `addons` (the
addons of `docs/videos/steps.json` found in `Interface/AddOns`, id to its
.toc version), `recorder_on` (Polestar Recorder's switch,
`PolestarRecorderDB.enabled`; missing when no account saved it yet),
`saved_chars` (how many characters have Polestar's saved data), `shipper`
(the shipper's own version; missing from one older than its auto-update,
which `/start` then asks to update once by hand). Sent with
each send, when it changes, and once a day; no folder, name or character.
The start page (`/api/me/status` `setup`) and the "Missing:" lines
(`wowstats/missing.py`) read the newest one; it is not game data, so it
counts for nothing else.

## What the UI builds from them

- Header, climb, zones, guide steps, dungeons, deaths, flights: `wow-recorder`,
  `wow-level`, `wow-dungeon`, `wow-character`. The climb's played time is a
  sum over the `wow-level` splits; the Recorder's records only cover the
  time it was on. Kills and quests always name their population
  (`climb_kills`, `climb_quests` by `in_play` in the semantic layer): the
  **completed levels** (a level's split counts, the Recorder's records for
  a level without one) and the **current level so far** (the Recorder's
  records at the level in play, or its split while one grows). The page
  shows the completed levels' totals with the current level beside them;
  the MCP's `character_summary` returns `counts.completed_levels`,
  `counts.current_level` and `counts.including_current_level`.
- Played time, everywhere (the climb's x axis, pace, ETA): logged-in time,
  never calendar time. Finished levels use their split. The level being
  played has no split yet, so its time so far comes from the Recorder: it
  opens a chunk (a `session` record) every 300 s while the game runs, so a
  silence over 315 s is time offline, and the rest adds up to the same
  logged-in time Polestar's splits count (level 31 of the test character:
  9369 s both ways).
- Total played since level 1: only with Altoholic (`kind: played` above).
  Its `/played` is the game's answer at a login, so `played_total_s -
  played_level_s` is the moment that level began; the UI adds that level's
  split and every later one, plus the level in play, so the total is
  current. A snapshot whose session had a ding is skipped (the answer may
  be from the level before), and so is one from a level before tracking
  began. Without one, the climb says "played since tracking began".
  Polestar's own addons don't save `/played`.
- Guide steps: a `step` record with `reason: pick` is the player picking or
  switching guide (its `step` is often empty): listed as "Picked guide ...",
  never counted or timed as a step. Numbered steps show the step's own text
  from the route reference when the guide is in it.
- Gear and professions panel: the newest `wow-gear` event. The public view
  shows it too (no gold in it).
- Journal: one entry per play session. A session ends after more than
  330 s without a Recorder record (`stores.split_sessions`, also the Overview's, the MCP's
  `sessions` and the semantic layer's). Each entry has start,
  end, played time, levels gained, XP, zones (time clipped to the session),
  quests handed in with names, deaths per zone and dungeons. The full view
  adds the players met and the gold change (the logout snapshot after the
  session against the last one before it).
- Prices panel: `wow-prices`, full view only.
- `as_of` (the full view's `/api/char`, and every MCP answer about one
  character): the newest `_time` of the character's events the answer was
  computed from, UTC to the second. The page and the MCP read one snapshot
  of them (`wowstats/eventcache.py`), so the same `as_of` means the same
  data. It is an event time (the table above), not when the answer was
  built: that is `generated_at`. The public view and the static builds
  leave it out.
