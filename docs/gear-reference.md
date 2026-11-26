# Gear upgrades

**For:** players curious about the gear advice, and contributors. **At the end:** you know how upgrades are scored, where the data and the weights come from, and where it differs from Polestar and ForeverDB.

The equipment panel says, per slot, what to wear instead and where to get it:
"Belt of Arugal, +24% on your waist, drops from Archmage Arugal in Shadowfang Keep".
Three sources, kept apart:

- **The items**: Forever's own client tables (`data/game-items.json`, below),
  the way the 3D view already reads them. Stats, item level, required level,
  slot and class mask, for 17,908 weapons and armour pieces.
- **Where they come from**: [Polestar](https://github.com/fitzforge/polestar)'s
  gear data (`data/gear-reference.json`: drops, crafts, quests), plus the
  quest rewards of forever-db, the quest database Polestar bundles
  (`data/forever-reference.json` `gear_quests`).
- **How much each item is worth**: the community stat weights per spec, in
  the set [ForeverDB.net](https://foreverdb.net) publishes, with its formula
  and its Leveling and PvP rules (`data/foreverdb-weights.json`). Every
  score on the page and in the MCP carries the credit "Weights: ForeverDB.net"
  with the link. Checked against ForeverDB's own BiS ladders, scoring our
  item data: 36,674 of 36,746 scores equal (below).

A drop names its boss and, for a dungeon, the level range
(`data/forever-reference.json`, `docs/forever-reference.md`).

Polestar is required: without its SavedVariables a character has no level
the server trusts, so the panel says Polestar is missing instead.

## Credit and licences

[Polestar](https://github.com/fitzforge/polestar) is the levelling guide addon
for WoW Forever, by fitzforge and its contributors, GPL-3.0-only. Its gear
advice (`addon/Polestar/Gear/`, `addon/Polestar_ItemDB/`) is built from:

| Part | From | Licence |
| --- | --- | --- |
| Quest rewards and item sources in `Polestar_ItemDB/Forever.lua` | Polestar's Codex. Its release uses Blizzard client facts, QuestieDB facts, pfQuest, web database facts, player recordings and Polestar's guides | Polestar GPL-3.0-only, pfQuest MIT, upstream source notices in `THIRD_PARTY.md` |
| Who wears what: armour and weapon types per class and the level each is learned, the class masks, the specs and each class's levelling default (`Weights.lua` `CLASSES`, `PROFICIENCY`) | Polestar | GPL-3.0-only |

None of Polestar's weights or gear settings reach the file or the scores
(owner, 2026-11-24): the items, their sources and the class rules are all
it gives the gear advice.

The export records Polestar's tag and commit and the source, version and generation
date registered by its item database. The client item build and hotfix cache
used for scoring are recorded in `data/game-items.json`. The shipper can read
the player's spec setting from a locally installed ForeverLevelingBiS.
[Source notices](../THIRD_PARTY.md)

## The items: the game's own tables

`tools/game_items/export.py` writes `data/game-items.json` from Forever's
client tables (owner, 2026-11-24: "can we get them from the game data
instead? We do that already for 3D"). Polestar's item table is no longer
read: it had Classic's numbers for items Forever re-itemised (Staff of the
Shade: +21 Shadow damage in Classic, 37 in Forever) and no row at all for
1,186 of the items ForeverDB ranks.

- **Tables** (wago.tools CSV exports at `source.build` in `data/game-items.json`):
  ItemSparse and Item (the rows), RandPropPoints (the stat budget per item
  level), the seven ItemDamage tables (weapon DPS per item level and
  quality), ItemArmorTotal, ArmorLocation, ItemArmorQuality and
  ItemArmorShield (armour), ItemEffect, ItemXItemEffect, SpellEffect and
  SpellEquippedItems ("+1% hit" equip lines, and whether that hit needs a
  bow, gun or crossbow). ItemLimitCategory supplies the shared equip limits
  for rings and trinkets. Don Julio's Band and Don Rodrigo's Band share a
  limit of one, even though their item IDs differ.
- **Forever's hotfixes.** Most of Forever's own and re-itemised items are
  server hotfixes, which wago.tools doesn't have. The exporter reads them
  from a Forever client's hotfix cache (`--dbcache`, the maintainer's
  `_forever_/Cache/ADB/enUS/DBCache.bin` at the same build, parsed by the
  shipper's own code). `source.hotfix_cache` records the build and item count.
- **Classic Era** (1.15.9.70576) fills 4,867 items Forever has nowhere in
  its files or the cache (old PvP and raid gear). Those carry `from: "era"`
  and stay out of the "where: unknown" list below: they may not exist in
  Forever at all.
- **Stats** are worked out the way the game does: each stat is
  `StatPercentEditor x RandPropPoints[item level][quality][slot group] / 10000`,
  rounded half up (half away from zero below zero). Weapon damage, armour
  and shield armour come from their tables, so do the equip lines' hit,
  crit and attack power. ForeverDB scores percent hit and crit as rating
  (10 and 14 a percent), and so does the file (`hitRating`, `critRating`,
  `rangedHit` for the bow-only kind, which counts for hunters only).
- **Set bonuses** stay ForeverDB's (`data/foreverdb-weights.json` `sets`):
  the client's ItemSet tables give the same shares on all 416 pieces it
  lists, but ForeverDB leaves some sets out (Tier 0, Stormcloth), and the
  score is theirs.
- **Class masks**: the client's, else Polestar's. The client leaves about
  50 class items open to every class (Tier 3, Atiesh, the Zul'Gurub
  bracers: the server enforces those).
- **Required level 1** counts as none, like ForeverDB: Forever's own items
  say 1 where Classic's say 0, and they count from their item level - 5.

What it can't hold: random suffixes ("of the Owl" stats are server data;
the likely-suffix estimate below stays) and equip spells that add spell
power (Briarwood Reed), which ForeverDB doesn't count either.

```sh
uv run python tools/game_items/export.py --build 1.60.1.71139 --dbcache <DBCache.bin>
uv run python tools/game_items/export.py --check   # offline: shape, build, five known items
```

The check pins five items as ForeverDB.net shows them: Staff of the Shade,
Earthen Rod, Violet Sorcerer's Wraps, Spine of the Devourer and Gravestone
Scepter.

### Where an item comes from

ForeverDB.net's addon data first (`data/foreverdb/item-sources.json`, GPL-3.0
with an attribution term: Data from ForeverDB.net, https://foreverdb.net):
7,645 items, 9,891 sources on 2026-11-26. Quest rewards, vendors with their
price, boss drops with the chance players measured and how many kills it
stands on, creature and rare drops, crafts, reputation, PvP rank, world drops
and containers. Each source says how ForeverDB knows it, and the advice says
it too: "seen in Forever" (players' addons recorded it), "Forever client",
"Classic data, may differ in Forever" (CMaNGOS classic-db), or "a clue,
unconfirmed". Every source shown links ForeverDB.net ("Source: ForeverDB.net"
on the page, `where_source` in MCP answers).

Polestar's sources and forever-db's quest rewards
(`data/forever-reference.json` `gear_quests`) fill in only for items
ForeverDB has no source for.

A reputation source is checked against the character's standings: the
Recorder's rep records and Altoholic's reputation snapshot, the newest per
faction (`gear.standings_of`). Met, it's a purchase: "reputation: Timbermaw
Hold Honored (you're Honored)". Not met, it says what is missing:
"(you're Friendly: 16,500 rep to go)", or "not met yet" for a faction the
character never met; with no reputation data at all, "your standing isn't
recorded". A PvP rank source is checked against the newest rank saved (the
newest `wow-foreverdb` event): the Forever client's "Renown - PvP Rank"
currency (3473; the client has no UnitPVPRank) from Syndicator or Altoholic,
or ForeverDB's armory snapshot. Held, it's a purchase from the quartermaster:
"(you hold rank 3)", some effort. Not held: "(rank 3 needed, you're rank 1)".
With no rank saved, 0 lifetime honorable kills in Altoholic still means no
rank; otherwise "your PvP rank isn't recorded yet". A rank not held or not known is a long shot
(below).

### What never counts

Two filters run before the effort (`gear._source`):

- **Endgame Forever hasn't opened** (`gear.closed_content`, `wowstats/endgame.py`). Owner,
  2026-11-25: "we're still very early: no raid yet, no high level content still, so it should
  be explicit for end game, not 'we don't know so we count as opened'". Classic's data lists
  every raid, world boss and event, so endgame is shut by default and counts only when the
  meta's endgame section (`data/kb/endgame.json`) opens it, or when ForeverDB.net saw the source
  in Forever (`s`).

  What is endgame comes from the data's structure, not an item list: a drop in a raid
  (forever-db's areas with `instance: raid`, ForeverDB's raid instances), a quest done in a raid
  or following one (forever-db's quest zones and prerequisites: Treasure of the Timeless One
  follows a Blackwing Lair quest, so its rewards are raid rewards), and a world boss (a level-63
  boss outside any instance: Azuregos, Kazzak, the Emerald Dragons, the Silithus lords). The
  section's entries name what the structure can't see: the Dungeon Set 2 chain from its first
  quest (An Earnest Proposition, 8905-8920) and the bosses its brazier summons, a raid drop's
  hand-in quest (Onyxia's and Nefarian's heads, the Ruins of Ahn'Qiraj armaments), a world
  event's chest (Gurubashi Arena, the Ahn'Qiraj war effort, the Abyssal Council's spoils).

  Each entry has a `status`: `open`, `announced` (with an `opens` day, or none) or `not-open`,
  and its source. A source counts when every entry naming it is open or its day has come; one
  shut entry wins (Lord Valthalak sits in Blackrock Spire, which is open: his chain isn't). An
  entry that names a source beats one that only covers it by a rule (Ivus and Lokholar are
  level-63 bosses, summoned in Alterac Valley, a live battleground). Endgame nothing names is
  shut: no source said it opened.

  On 2026-11-25 (the section as seeded from the KB):

  | Open | Announced | Shut |
  |---|---|---|
  | Level-60 five-mans: Blackrock Depths, Blackrock Spire (lower and upper), Stratholme, Scholomance, Dire Maul, Sunken Temple, Shaper's Terrace, Blackmaw Hold (Forever launched with every Classic dungeon and 9 new ones; Master Spelunker lists them) | Onyxia's Lair, Barrow Deeps, Hyjal Summit: 9 Dec 2026 (Blizzard, news 24303009). Scarlet Enclave: no day | Molten Core, Blackwing Lair, Zul'Gurub, both Ahn'Qiraj, Naxxramas; Dungeon Set 2; world bosses; Gurubashi Arena; Ahn'Qiraj war effort; Abyssal Council |

  That shuts 438 items outright (every source shut): Dungeon Set 2 160 sources, Onyxia 142,
  Naxxramas 94, Ahn'Qiraj 82, Zul'Gurub 72, world bosses 52, Ruins of Ahn'Qiraj 20, Blackwing
  Lair 11, Molten Core 6, the arena 5. On 9 Dec, Onyxia's 54 come back by themselves: no code
  change, the day is in the file. Barrow Deeps and Hyjal Summit have no loot in our data yet;
  when ForeverDB or forever-db add it, it is shut until the 9th like Onyxia's.

  Levelling dungeons and quests don't change: a dungeon drop only Classic's data knows (`k`)
  still counts. A few Classic world-drop epics ForeverDB only lists on Onyxia and the Colossus
  of Zora (Uther's Strength, Horizon Choker, Serenity Belt...) go with them: long shots anyway.

  The weekly meta pass keeps the section (`docs/kb.md`, "The endgame section"). A new opening
  in the news is a reviewed change to the file, never code.
- **PvP rewards, unless counted** (`gear.is_pvp`, KB rule `pvp_rewards.sources`): a PvP rank
  source, a reputation of the eight battleground factions and the quartermaster next to it,
  or a PvP vendor (Champions' Hall, Hall of Legends, Dalaran's Underbelly: ForeverDB lists
  the Premier rank weapons there with no rank, so a PvP vendor counts as a long shot outside
  the PvP mode). Counted when the player ticks **PvP rewards** (`wowstats/gearmode.py`, kept
  per character next to the mode; MCP `gear` `pvp`), and always in the PvP mode, where each
  such source carries `by_playing` and is easy. Every answer that ranks gear reads the same
  toggle: the gear page, `gear` and its options, next moves, the planner, the analysis.
- **The other side's reputation gear** (`gear.side_of_gate`): a reputation's side comes from
  the client's Faction table (`factions` in `data/forever-reference.json`), and its
  quartermaster sells to that side only, whatever side ForeverDB gives the vendor. ForeverDB
  had Theramore's Bryanna Embreeze and the Ratchet and Booty Bay quartermasters as the
  Horde's: 43 Theramore items never reached an Alliance character, 46 went to the Horde, and
  the 10 neutral towns' items went to the Horde only. A reputation only
  Classic's data names (label `k`) doesn't decide: Classic names one side's faction for gear
  both quartermasters sell (Rune of Duty, Warsong Outriders, seen at Silverwing's
  quartermaster).

### How hard it is to get

Owner, 2026-11-24: "Convenience and easiness before a quarter of a point."
Every item gets an effort class from its easiest source (`gear.source_effort`,
ForeverDB's own buckets as a start):

| Class | Sources |
|---|---|
| easy | in the bags or bank, on the auction house, a quest that is only a hand-in or that your route hands in soon, a vendor, a craft you can make, a reputation you hold, a drop of 25% or more |
| some effort | a quest with quests to do first, a dungeon quest, a foe 3-4 levels over you, an elite at or under your level, a step the guide keeps for a group, something to bring that no creature drops; a drop of 8-25%, a craft still to learn or skill up, a reputation up to Honored not held yet, a PvP rank held |
| hard | a quest with 5 or more quests to do first, an elite over your level, a foe 5 levels over you or more, a raid quest; a drop of 1-8%, a drop seen in Forever with no rate, a world drop, a raid drop, a reputation above Honored not held |
| long shot | a drop under 1%, a drop known only from Classic's data with no rate, a drop from a creature nobody names or places yet, a PvP rank not held or not recorded |

#### What a quest takes

A quest used to be easy whatever it asked. Owner, 2026-11-25: "there are a
few recos here that are easier said than done." A level-37 character got
Big Game Hunter (King Bangalash, a level-43 elite, twelve quests in) and
Greater Friend of the Library (twenty-odd library books) as easy errands.
Each quest source now carries its `work` (`wowstats/questwork.py`), every
fact from data, and says it in one line (`work_says`):

- **The route first.** A quest one of your side's Polestar levelling guides
  hands in ahead of you counts by where the guide does it: "on your route:
  hand-in at about level 39 (Stranglethorn Vale, ..., step 61)". The level
  is where the guide's planned runs reach that step
  (`data/route-reference.json` `turnins`). Within 3 levels it's easy, or
  some effort for a step the guide keeps for a group (`when group`, Big
  Game Hunter). The guide does the chain and the elite at its own pace, so
  neither makes it hard (owner, 2026-11-25: "the Big Game Hunter wand comes
  out of the guide and I'm deep into the chain"). A guide that gets there
  more than 3 levels from now: doing it now is a trip off the route, at
  least some effort, judged by the rules below.
- **The chain.** forever-db's prerequisites, down to the first quest, done
  ones included: "chain: 8 of 13 quests before it done, 2 in your log; next:
  Panther Mastery, part 3 of 4 (in your log)". Your quests done and in the
  log come from the Recorder (accepts, hand-ins, abandons) and the other
  quest sources (`wowstats.quests`). Any quest still to do first is some
  effort, five or more hard. A quest already in your log skips this.
- **The work** (`data/forever-reference.json` `quest_work` and `npcs`):
  forever-db's quest type (group, dungeon, raid), the strongest creature
  its objectives send you to, with its level and rank, and the items to
  bring that no creature drops and no object gives. An elite over your
  level is a group's work, hard; one at or under your level is some effort.
  A foe 3-4 levels over you is some effort, 5 or more hard. A dungeon quest
  names its dungeon and is at least some effort.
- **A gate the quest data doesn't say.** A quest with no objectives and no
  zone in forever-db, nor any quest before it (Library Books' milestones), takes
  its work from the guide off the route that hands it in: "Polestar's
  Library Books guide, step 27: 24 of its quests to hand in first".

A drop from a creature in the open world says the creature's levels and
rank, and "no drop rate known" when nobody measured one. A creature no data
names or places (no level, no zone: ForeverDB's "NPC 268423", Zalfan
Moonshadow) says "not placed yet" and is a long shot: there's nothing to go
and kill.

Half a weapon setup is as hard as its other half: an off hand that needs a
main hand you don't have yet takes the harder of the two efforts while you
level.

In the PvP mode a PvP reward (rank or battleground reputation) is easy whatever is held.

A drop known only from Classic's data (never seen in Forever) counts one
class harder than its rate says. Every drop says what its rate means at the
keyboard: "0.03%, about 3,333 runs". An auction house listing makes any item
easy: buy it.

Below level 60 the pick per slot and the upgrade order weigh the effort:
each item's score is taken at 95% for some effort and 85% for hard or a long
shot (`EFFORT_PENALTY`, through `effort_penalty`), so a harder item wins only
when it scores at least 5.3% (some effort) or 17.6% (hard) more than the
easier one. A long shot is then never a pick or an upgrade: it is the slot's
best possible, listed apart with its odds (`bis_unknown` with `"effort":
"long shot"`), and the standings and the BiS percents measure against what
can be got. At 60 (`ENDGAME_LEVEL`) marginal gains are the point: the score
alone ranks, and a long shot can be the pick or an upgrade, its effort and
odds on it (owner, 2026-11-24). Every upgrade carries its `effort`; the page
shows it next to the source.

Coverage on 2026-11-24, on ForeverDB's own picks
(`tests/data/foreverdb-picks.json`, 935 items): 856 have a source in
ForeverDB's data, against 462 from Polestar alone before.

    uv run python tools/foreverdb_sources/export.py           # fetch the manifest's files, check, write
    uv run python tools/foreverdb_sources/export.py --check   # offline: notice, schema, licence files

Only the four files read are downloaded, each checked against the manifest's
SHA-256, and the Lua is read as text by a strict parser
(`tools/foreverdb_sources/lua.py`) that refuses anything but literals: nothing
in it is run. Every file's first line, "-- Data: ForeverDB.net
(https://foreverdb.net), build ...", goes first in the JSON (`_notice`) with
the attribution. The KB watch refreshes it at most once a week
(`docs/kb.md`, kind `foreverdb`); releases ship what is committed.

### Gear with no known source

An item with stats and no source is the slot's best possible when it beats
everything that can be got, like a long shot: the page lists it apart
("Best possible, out of plain reach", `bis_unknown` per slot in the answers,
`"where": "unknown"`). It is never an upgrade to go and get, and the
standing measures against what can be got: nothing to do about it until
someone knows where it drops. Left out: commons, random-suffix
bases, Era-only rows and placeholder names (`OLD`, `Monster -`, `QATest`...).
For weapons it's a whole setup: the best two-hander, or a main hand with the
best off hand the character can get.

## The weights: ForeverDB.net

The stat weights are the widely used community weights per class and spec,
the ones many gear calculators use. We take the set ForeverDB.net publishes,
with its Leveling and PvP rules, and credit it wherever a score shows. We
take the weights, the rules and the set bonus shares; not its item database.

`tools/foreverdb_weights/export.py` reads the addon's `Data/GearFinder.lua`
(the same GPL download as the item sources, above: the PvE and PvP tables per
spec, the Leveling rule checked against the addon's own Leveling table) and
`Data/GearSources.lua` (the set bonus shares), and writes
`data/foreverdb-weights.json`: per class and spec three tables, with the
source URLs, the client build and the data version. Moving from the website's
static files to the addon's (2026-11-24) changed one number: Marksmanship
PvP mp5, 0.028 to 0.03 (the addon rounds to two decimals); 138 more set
pieces have a share.

    uv run python tools/foreverdb_weights/export.py --files <download>   # from export.py's download
    uv run python tools/foreverdb_weights/export.py --check    # offline: the file is well formed and the rules hold

**The three modes**, picked per character on the gear page and kept on the
server (`gearmode.py`: `gear-modes.json` in `ANALYSIS_STATE_DIR`, else
`WOW_STATE_DIR`; an install with neither keeps the pick until the page
reloads). The MCP `gear` tool reads the same pick; its `mode` argument
overrides it for one call. The plan, next moves, the analysis and the public
doll's badges stay on Leveling: they rank levelling moves.

| Mode | What it values | From |
| --- | --- | --- |
| **Leveling** (default) | Staying alive and killing fast while questing: Stamina at least 0.8 of the main stat (Strength, Agility, Intellect or spell power, whichever weighs most), armour at least 0.03 for mail and plate wearers and 0.02 for the rest, hit and expertise at 0.7 of the guide's, Spirit and mp5 at 0.6, a healer's healing at half, and a wand's DPS at 1.5 for priests, mages and warlocks | ForeverDB's rule on the class guide's weights, computed by our generator; the result equals theirs for all 27 specs |
| **PvP** | Stamina near the main stat, resilience to burst; per spec | ForeverDB's PvP table per spec (all 27 have one; the generator keeps their fallback rule for a spec without: Stamina 0.9 of the main stat, hit 0.8, mp5 0.7, armour at least 0.02) |
| **PvE** | Raids and dungeons at 60: the class guide's priorities, Stamina at 0.1 | The class guides' weights (a hunter's attack power also counts its ranged attack power) |

**The formula.** Score = the sum of each stat times its weight, with:

- percent lines read as ratings: 1% hit = 10, crit 14, dodge 12, parry 15,
  block 5;
- "+X damage and healing" one stat (`dmgheal`), healing-only and damage-only
  lines on their own weights; school damage on top of spell power,
  uncapped;
- weapon DPS ((min + max) / 2 / speed) times the `dps` weight: the ranged
  weapon's for hunters, the melee weapon's for the rest; a wand's DPS times
  the `wand` weight (Leveling casters only);
- armour times its weight; a set's bonus stats shared across its pieces
  (bonus / set size on each);
- procs and on-use effects count zero;
- an off-hand weapon's DPS part counts half when a two-hander is weighed
  against a pair.

**Availability.** An item is wearable from its required level; with none,
from its item level - 5; a quest reward from the quest's level - 5 (or its
required level, if higher).

**Where it differs from ForeverDB.net.**

- The pool is personal: your side, your quests not done, your bags, your
  professions (below). ForeverDB lists every item.
- Item stats are the Forever client's own tables (`data/game-items.json`),
  not Polestar's item table.
- The off hand next to a main hand: ForeverDB takes the best full score,
  then halves its weapon part; we take the one worth most once halved.

**Where it differs from Forever Leveling BiS.** That addon (1.x) ranks with
its own weights, so a list can disagree, mostly on Spirit and Intellect
pieces. Our scores use the player's chosen ForeverDB.net profile.

**Public origins.** A short search (2026-11-23) found no openly licensed
source with the same numbers: Icy Veins and Wowhead give priority orders,
not weights; RankedBoost's Forever calculator uses its own. ForeverDB's
corrections log says its Marksmanship table follows the WoWSims Forever
turret weights. When an openly licensed source with these numbers turns up,
it goes next to ForeverDB in the file and here.

## How the file is made

`tools/gear_reference/export.mjs` runs Polestar's own files, nothing built:

1. A shallow clone of Polestar at the release tag, the same tag as the route
   times (`tools/polestar-tag`, moved by kb-refresh with the exports when
   its watcher sees a new Polestar release: docs/kb.md).
2. `tools/gear_reference/dump.lua` loads `Gear/ItemDB.lua`, then the data
   addon's `Items.lua` and `Forever.lua` in the order of its `.toc`, then
   `Weights.lua` for the class rules, each in an empty environment (no `io`,
   `os` or `load`). Items are parsed by `ItemDB.Get`, sources by
   `ItemDB.Sources`, quests by `ItemDB.Quest`, so the file reads the data the
   way the addon does.
3. The export keeps the items gear advice scores (no shirts, tabards, bags or
   ammo), their sources, the quests behind them and the class rules. No
   weights: those are ForeverDB.net's.

Under 3 seconds, the same file byte for byte on every run:

```sh
git clone --depth 1 --branch v0.4.3 https://github.com/fitzforge/polestar /tmp/polestar
node tools/gear_reference/export.mjs --polestar /tmp/polestar
```

CI reruns both exports on every change to them, the data files or the
scoring, and fails when a result differs from the committed file. A Polestar
bump means: change `tools/polestar-tag`, rerun the exports, commit the
files; kb-refresh does it on its own when the release passes its checks.

## What the file holds

| Key | What |
| --- | --- |
| `items.<id>` | Polestar's own item rows. Not read for scoring since 2026-11-24 (`data/game-items.json` instead); its `allow` fills the class masks the client leaves open |
| `names.<id>` | Item names, for the items with a source |
| `sources.<id>[]` | `{kind: "quest", quest}`, `{kind: "drop", npc, npc_name, pct, zone, zone_name}` (npc 0 is trash) or `{kind: "craft", spell, profession, skill}` |
| `quests.<id>` | `name`, `level`, `req`, `side` (1 Alliance, 2 Horde, 3 both), `zone_name`, `race` and `class` masks, `dungeon`, `choices[]` and `items[]` |
| `gear` | Polestar's class rules: `CLASSES` (specs, class bit, levelling default) and `PROFICIENCY` (armour, weapons, shields, relics, dual wield) |

10,621 items, 3,529 of them with a source, 1,088 quests; 1.7 MB (about
220 KB gzipped on the wire). `GEAR_REFERENCE_FILE` points at another file,
`off` turns gear advice off.

## How a character is scored

`wowstats/gear.py`, per character, at the level Polestar last saw:

- **Weights.** ForeverDB.net's table for the class, spec and mode (above),
  as it is. Nothing the player set in Polestar changes it.
- **Score.** ForeverDB's formula (above).
- **Usable.** Armor and weapon types the class learns, and at what level
  (Shamans wear mail from 40), the class mask, the level from the
  availability rule above.
- **Where.** Quests of your side, race and class only, minus the ones you
  have done (`wowstats/quests.py`: the Recorder's hand-ins, Questie or
  Altoholic, a reward only that quest gives on any gear or bags sheet, and
  what Battle.net says you wear now, read live). A quest that offers a
  choice shows its best reward only (`Rewards.lua` `RankChoices`). Crafts
  say whether you make it at your rank; crafts with no required level
  (Engineering goggles, which need the skill to wear) only show to those
  who have it.

Per slot: the best item you can wear now and the next one within 3 levels,
when it beats both. An upgrade gains at least 1% over what you wear (Polestar's
"smallest upgrade" setting is not read).

**Gains are in score points** underneath, the number that compares
across slots. The page shows them as the slot's share of BiS (below); an
item with nothing to measure against keeps its points ("Chest, +4.3
points"). The percent over the replaced item is Polestar's tooltip number,
but against a weak item it reads +427% for a modest gain, so the page keeps
it for the tooltip.

**Weapons are whole setups.** A two-hander is weighed against main hand + off
hand, ForeverDB's way: the best main hand, then the best other off hand. A one-hander against a worn two-hander is never
weighed alone: in game Polestar pairs it with the best off-hand piece in your
bags (`refreshBags`, `bagBest`); the server pairs it with the best off hand
you can get by then (held ones included), or the one you wear, and says which
("with Raptor's Gaze, drop: Shadetooth"). A setup is listed once.

**Repeated sources** read once: the data has some quests twice (one copy
without a zone), and the copy that says the most stays. The page lists them by where you get them (a group
per dungeon, zone's quests, profession or the auction house; a dungeon ranked
by its expected gain per clear, the chance-weighted best per slot) or by
slot; either way a slot shows its best and at most 2 alternatives that add
something (another source, available now, another gate), a group at most 3,
and the rest folds under "N more". An upgrade under 2% of your gear is
"small". **BiS** (`standing` on each slot, `bis` on each upgrade): the best
score the slot can hold now, among what is worn and every item wearable at
this level; a worn item is BiS, 2nd (one item above it) or its percent of
BiS's score. On the paperdoll that badge takes ForeverLevelingBiS's colours:
orange for BiS, purple from 85%, blue from 50%, the plain dark band below,
with a legend under the paperdoll. Rings and trinkets count the best two different items, and the
hands count whole setups. None for a random-suffix item scored on a guess, a
slot with nothing to compare, or no sheet at all. Every item the page and the MCP show carries the same measure (`bis_pct`,
`bis_pct_worn` and `delta_pct`, `wowstats/gear.py` `bis_badge`): its own
share of the slot's BiS, as a badge on its icon, and the worn item's share
then its own ("70% → 94% on your waist", "empty → 94%" for an empty slot;
`gain_words` on the server, `upText` on the page). A bare "+24%" read as
24% more stats, so the page never says it; `delta_pct` is the difference. Rings and trinkets measure in the
slot the item would take, the weaker of the two worn, so the line starts from that
one's badge and only the best ring or trinket left reads 100% (the second of the
best two reads its share of the best); a two-hander or a
setup the advice pairs counts whole, a one-hander scored alone (a dungeon
drop the advice doesn't list) has none. Upgrades are ordered by it:
the slot furthest from BiS first; within a slot by points (the same start
and the same BiS, so the biggest gain closes most of the gap, an item built
for someone else a little less). **Gates** (`gates` on each upgrade): the level to wear it, and for
an item only a craft gives, the profession's skill when you have the
profession, or "buy" when you don't (a crafter or the auction house; the
data has no recipe price or reputation gate, so none is shown).

**The spec**, first match wins:

1. The selector on the page (kept in the browser).
2. The site's `WEIGHTS` setting for that character (docs/deploy.md): the
   player's choice, kept where the site runs.
3. The spec the player picked in a gear addon: ForeverLevelingBiS or
   GearQuestForever's SavedVariables (`spec` in `wow-gear`). Polestar's own
   Stat weights setting is not read.
4. Only when none of the above says anything: the talent tree with the
   most points, as Polestar's Automatic picks it (`Gear.lua` `DetectSpec`,
   `mostPoints`), from Battle.net's API (the exporter's
   `/specializations` read every 2 hours, under the talent frame's tree names). A hint,
   not a choice. A site without a Battle.net client, or a character the
   API doesn't know yet, skips it.
5. The class's levelling default in Polestar's class rules (Shadow for a Priest).

The page and the `gear` tool say which one won, in words: "Scored for Shadow
because you picked Shadow in ForeverLevelingBiS." The shipper sends which
addon the pick came from (`spec_from`).

After a respec the talents catch up at Battle.net's next read, within 2 hours.

The character card keeps two things apart: the talents
as the game has them, named by points ("Discipline 16/0/7", from Battle.net
every 2 hours), and what the gear is scored for, from the list above. A Shadow
priest levelling with most points in Discipline on the way to Shadowform
gets Shadow weights as soon as anything the player chose says Shadow: the
site's `WEIGHTS` or a gear addon's pick. From
level 30 the full view adds one line when the weights' tree can't reach
its 31-point talent by 40 without a respec ("Shadowform at 40 = full respec into Shadow").

## Who an item is built for

An upgrade is an upgrade, especially while levelling, but the page says when
it's made for someone else. `wowstats/gearfit.py` holds
the model, one entry per class and spec; the KB lane
`data/kb/research/gear-fit.json` holds the facts behind it, each with two
sources.

**Stats.** Who an item is built for reads each spec's ForeverDB.net weights
in the mode the scores use (Leveling unless the player picked PvP or PvE):
the same weights as the gain, so the label and the score never disagree. The rules below explain the zeros in them and hold the
KB citations:

1. **Against the class: zero**, at every level.

   | Stat | Counts zero for | KB |
   |---|---|---|
   | Healing power | warriors, rogues, mages, warlocks, hunters | `gear-fit:class-no-heals` |
   | Intellect, Spirit, mana regen | warriors, rogues | `gear-fit:no-mana` |
   | Attack power | mages, priests, warlocks | `gear-fit:casters-no-ap` |
   | Ranged attack power | everyone but hunters | `gear-fit:ranged-ap` |

2. **The class's, not the spec's.** Healing power for a priest, druid,
   shaman or paladin who doesn't heal; attack power for a druid, shaman or
   paladin who casts or heals. The guides weigh it zero too. Until the
   spec's 31-point talent (40 at the earliest: `gear-fit:capstone-at-40`)
   the label on such an item says "Fine while levelling"
   (`gear-fit:hybrids-heal-while-levelling`).

Forever's "damage and healing" line is one stat: a shadow priest gets all
of it. A healer item still carries about a third of its healing as damage
(Stoneweaver Leggings: healing 20, damage 7). Stamina never counts as
"doing little": the guides weigh it low for a raid, every other mode high.

**Built for.** For each item, every spec that can wear it is scored on how
much of the item's budget its weights use (Stamina left out: everyone takes
it). The ones within 85% of the best are who it's built for. A spec in a
close role counts too at 65% (weapon fighters share Agility and Strength
pieces; a tank levels in them). An item whose healing is at least twice its
damage is a healer item. Only Stamina, armour or weapon damage: fits anyone.

**The labels**, one line under the item:

- "Built for your spec"
- "Fits any spec"
- "Not made for your spec. Fine while levelling" (built for another spec of
  your class, before your 31-point talent; after it: "Not made for your
  spec: maybe not the best for you"), with "In a group, don't roll Need if
  someone it's made for is there"
- "Made for healers, not for a mage" (made for other classes)
- "Role unknown: pick your role in settings" (no spec chosen anywhere and
  the class has more than one role; a mage is always a caster)

then what it costs you: "Leather: you can wear mail", "Most of its stats do
nothing for a Fury warrior", and who rolls first in a group: the specs it's
built for among those who wear its armour type as their best ("In a group,
hunters usually get first pick" on a mail Agility piece; "Healer item: in a
group, let the healer roll first"). An off-spec upgrade names the best one
in the same slot that is built for you, and where it comes from.

**Ranking.** The gain shown is the plain gain. Lists sort on `value`: the
gain, 10% less for an item built for another spec of your class, 25% less
for one made for another class, and 15% less again when the only way to get
it is a group drop someone else usually takes. Close
options put the on-spec one first; a far bigger gain still wins. Dungeon
and quest suggestions, the plan's "next upgrade" and the MCP `gear` tool
(`rank_points`, `fit`) all use it. Testpriest at 32, Leveling: as Shadow,
the healer's Filigreed Pristine Circlet (+18.4) ranks at 90% and names the
Filigreed Shadow Circlet (+24.4) as the one built for him. On PvE weights
the dungeon suggested first is the City of Dalaran as Shadow, Razorfen
Kraul as Holy.

### Checked against published gear tables

`tools/gearfit_check.py` scores the model against levelling and pre-raid
tables for every class and spec (`tests/data/gearfit-refs.json`: item IDs,
ranks and source URLs only), and `tests/test_gearfit_refs.py` runs it
offline with floors. "Built" is the share of a table's picks the label calls
built for that spec (or any); in a levelling bracket, "Not made for your
spec. Fine while levelling" counts too: the tables pick what's best while
levelling, and the page shows it, labelled. "Top 3" is the share we rank in
the top 3 of all the tables' items for that slot and bracket the class can
wear.

    uv run python tools/gearfit_check.py --misses

The disagreements, decided one by one (no tuning to match a table):

- **The items from the game's tables (2026-11-24): top 3 went from 90% to
  86% (726 of 845).** 85 more table picks have data now, and Forever
  re-itemised some of the tables' Classic-era picks (Freezing Band: 21 Frost
  damage in Classic, 13 in Forever; Hypnotic Blade and Illusionary Rod lost
  their 44 spell power), so they rank lower in our list, as they should. The
  floor is 85%.

- **Fixed: warriors were told rogue gear wasn't for them.** Leather and
  Agility pieces on warrior lists (Timbermaw Tunic, Amulet of the Darkmoon)
  read "built for Feral druids and rogues". Weapon fighters now share their
  pieces; the tip still says the leather wearers roll first.
- **Retribution paladins (top 3: 64%, built: 77%).** The Forever tables favour spell
  damage and Intellect (Champion of the Light turns Intellect into spell
  damage); the class guide's weights put Strength first. The KB holds both
  (`class-meta:paladin:contradiction:6`). A known difference, kept until
  the guide changes its weights.
- **Hunters, balance druids, elemental shamans (built: 71-80%).** Levelling
  tables list the best blue that exists in a bracket, often a caster neck
  or a healer's head piece (Ephemeral Choker, Geomancer Headdress, the
  Wildheart set for balance at 60). A druid's or a shaman's healer piece
  while levelling now reads "fine while levelling"; a hunter's caster neck
  is still "made for priests". The label is right about whose stats
  they are; the ranking still has them in the top 3 (88-94%). A legitimate
  difference: the page shows them, with the label.
- **Protection paladins (built: 89%).** The tables pick damage pieces
  (melee chests, the Demon's Blood trinket) for levelling. Same as above.
- **The casters' tables are partly Classic Era** (no Forever table found
  for mages, warlocks and priests); they're flagged `era` and only used for
  items Forever didn't re-itemize.

## Where it differs from the addon

- **No vendor items.** Polestar's data has quests, drops and crafts only.
- **Bags as of the last logout.** Like the game, items in your bags (and,
  unlike it, your bank) count, from Syndicator's save: the shipper's
  `wow-bags`. Such an upgrade has one source, the bag or the bank ("equip
  it"), and stays listed past the three-a-slot cap. Something picked up
  since the last logout isn't there yet.
- **Random-suffix items** ("of the Owl") keep their stats in the item link,
  which Syndicator saves without them. A worn one scores on the stats
  Battle.net's armory shows for it (the exporter's
  `wow_character_equipped_stat`, when the character is on Battle.net and
  wears the same item as at its last read). Otherwise, and for a
  random-suffix drop or bag item, it scores on its *likely* suffix: the
  median, by your weights, of what the fixed-stat items of the same slot,
  quality and item level (±2) carry beyond armor and damage. Blizzard gives
  a suffix the same stat budget, so half the suffixes it could roll are
  worth less to you and half more. Against a guessed worn item the gain
  shows in points, not percent, and those rows sort after the others. The
  Auction House never offers a random-suffix item: its price is the
  cheapest suffix listed, whatever the stats.
- **Talent ranks.** Polestar reads ranks that change weights (Mental
  Dexterity, which turns an Enhancement shaman's Intellect into attack
  power); the server only uses points per tree, as of Battle.net's last
  read, so it doesn't credit that Intellect.
- **Weapon skills.** The addon checks what you've trained; the server allows
  every weapon type the class can learn.
- **Quests done before Polestar Recorder ran** count when something else
  says so: Questie, Altoholic, a reward held on any sheet, or Battle.net's
  worn gear. One with no trace anywhere still shows.
- With ForeverLevelingBiS 1.x installed, Polestar's Automatic weights may use
  its scores in game. The page never does.

## Checked against ForeverDB.net

`tests/test_gear_foreverdb.py` runs in the normal suite against two
committed fixtures made by `export.py --fixtures`: five specs' ladders
(Shadow and Holy priest, Fury and Protection warrior, Beast Mastery hunter)
in all three modes, ForeverDB's items and scores (`tests/data/foreverdb-ladders.json.gz`),
and 75 cases of their picks per slot and level (`tests/data/foreverdb-picks.json`).

- Weights: our three tables equal theirs, all 27 specs.
- Scores: 36,674 of 36,746 ladder entries equal to the tenth, scored from
  our own item data (2026-11-24; 36,690 on ForeverDB's item data). Off: 18
  entries of 3 items only Classic Era describes (Grand Marshal's Warhammer,
  Marshal's Satin Grips and Handwraps: Forever re-itemised them). Missing:
  54 entries of 5 Forever hotfixes no client data here has (Expeditionary's
  Cape...).
- Picks: the same best item per slot and level, and the same "-N%" for the
  next two, within one point on a rounding edge.
- Weapons: the same setup, but for the off hand rule above.
