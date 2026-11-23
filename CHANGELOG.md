# Changelog

## Unreleased

What comes after 0.10.0, in short. Every change follows.

**For players**

- The gear page's weights copy into the Forever Leveling BiS addon (its own string or a Pawn
  scale), so it ranks items in the game the way the page does.
- A level a crash cut short gets its whole time from the game's /played, and flights no longer
  count as time in the zones they cross.
- A dungeon's quests count toward the run they came from, wherever you hand them in. City of
  Dalaran's two hand-ins at the Silverpine grate were Silverpine's quest XP; now they're
  Dalaran's, and its trips read about a third faster.
- With ForeverDB_Helper installed, each dungeon run says who was in the party: names, level,
  class and role. Money in the analysis tools reads as 16g 72s 10c, not raw copper.
- The 3D character moves now and then (a swing, a cast, a channel) instead of standing still.
- The demo's Nibbet is a level 46 Shadow priest with Shadowform and a staff that glows as in the game.

### Added

- The gear page's weights go into the game. **Copy for Forever Leveling BiS** and **Copy as Pawn
  scale**, under the upgrades, copy ForeverDB.net's weights for the spec and mode shown in the two
  formats the addon imports (its own `FLB1;...` string and a Pawn scale tag), named "ForeverDB
  Leveling" (or PvP, PvE). Paste them in `/bis`, Stat weights, Import, and the addon ranks items
  the way the page does. MCP `gear` takes `export: flb | pawn` and returns the string. A test scores
  every item of the game data both ways for nine specs and gets the same numbers; the Pawn scale
  loses ranged-only hit for hunters and says so. Weights only: the addon keeps its own items.
- Dungeon quests handed in outside count toward their run. A dungeon quest's hand-in goes to
  the nearest run of that dungeon before it, within a day, the quest taken before the run ended;
  otherwise it stays its zone's quest XP. Which dungeon a quest belongs to is a new bundled file,
  `data/dungeons/quests.json` (`tools/dungeon_quests/export.py`, rerun at each Polestar tag), from
  forever-db's zones, Polestar's cited dungeon journal and its dungeon runs: every dungeon of the
  panel but The Shaper's Terrace (no XP quests) has quests, City of Dalaran its 8. The Dungeons
  panel shows "XP a trip" with "includes N quests turned in outside"; MCP `dungeons` gives each run
  its `trip` (quest XP, quests turned in outside, XP an hour); `dungeons` with `options` and
  `next_moves` read the trips with them. The analysis gets `dungeon_quest` and `trip_quest` on
  records and quests, `dungeon_quest_xp`, `zone_quest_xp` and `zone_quest_xp_per_online_hour` in
  `per_zone`, and `trip_quests_outside`, `trip_xp_quests_outside` in `per_trip` and `per_dungeon`.
- Who you grouped with. The shipper reads ForeverDB_Helper's journal (optional, from ForeverDB.net;
  not in the install list), which names each groupmate once a day when they join your party:
  `wow-player` events with `source: foreverdb-helper`, context `dungeon` inside an instance, else
  `grouped`, with their level, class, race and role (new field `role`: tank, healer, damage). Never
  their realm or guild, nor the rest of the journal. Each dungeon run on the People view and each
  past run on the Dungeons panel lists its party, matched from the moment you joined the group
  (the Recorder's group records) to the run's end (the combat log's groupmates count too), so a second run with the same party still finds
  the names. When the Recorder counted more players than the journal named, the run says "2 of 4
  names known". MCP `players` with `groups` returns the same `party` per run; `sessions`'
  `grouped_with` picks the names up too. Friends' and public views never show a party.
- A `<metric>_gold` column (at the end of the row) for every metric counted in copper in
  `query_metric`, and every `*_copper` column in `play_sql`: 167210 reads `16g 72s 10c`. An analysis had divided by 1000 and
  reported 167.2g for 16g 72s. Findings write money with `{col:gold}`.

- The 3D view comes alive: the character stands, and every 8 to 20 seconds swings its weapon
  (the swing its weapon type uses: a staff's two-handed one), casts, channels or roars, then stands
  again. Only animations the model has play; none while you turn it, for 3 seconds after, under
  reduced motion, in a hidden tab, or in the still pictures. The emotes (talk, exclaim, question,
  bow, cheer, laugh) play too: the server asks the shipper for their animation files.
- With a two-hander the paperdoll's off hand shows that weapon greyed, as the game's character
  pane does, instead of "Empty".

### Changed

- The demo's Nibbet is a level 46 Shadow priest. Thirteen more evenings take him from 34 to 46
  through Arathi, Badlands, Stranglethorn, Dustwallow, Feralas, Tanaris and the Hinterlands, with
  Scarlet Monastery's four wings, Razorfen Downs, Uldaman and Zul'Farrak, and end at the Stormwind
  trainers. Every worn item is a drop, quest reward or his own craft that a level 46 could have, at
  63% of the best for his level on average (ForeverDB's Shadow Leveling weights), four slots at
  their best. One is made for another class: the Fiery Cord he tailored (Fire spell power) where
  the Black Cord would score more than twice as high. He holds the Dreamstaff he enchanted himself
  and a White Obsidian Wand from Uldaman, and a Talisman of Arathor from Refuge Pointe's
  quartermaster. His talents are 37 points of Shadow, Shadowform at 40.
  The auction house lists two upgrades at his level next to the Shadoweave Shoulders. The
  generator refuses gear from content Forever hasn't opened: every worn item needs a source
  ForeverDB saw in Forever or the Forever client has, or one in Polestar's drop tables (the
  Gurubashi Arena's Arena Master, Classic data only, can't be worn).
- The demo's real 3D model moves to the `demo-3d-2` release (`site/demo3d.sha256` pins the new
  archive): `demo-3d-1` has none of his new gear. Until that release exists the demo draws
  golden3d's figure.

### Fixed

- Your own character plays the 3D view's emotes, not only the demo. The server asked the shipper
  for the animation files of Stand and the closed hands only, and Forever's HD gnome keeps its six
  emotes in headerless `.anim` files, which the server refused (415) even when asked. Now one list,
  `M3D.ANIMATIONS` in the viewer, is what the server asks for (`assets.ANIMATIONS`, a test keeps
  them equal), and a headerless file is taken as an animation when a stored model names it as one.
  On the owner's gnome: 48 files, 6 MB more over 8 character models (158 MB to 164 MB, under the
  512 MB per person). Shippers already installed send them as they are, no update needed; the
  still pictures never ask for them.
- MCP: `sessions` and `dungeons` now say other players chose the names in them (`untrusted`), like
  `players`.
- The Upgrades list no longer fails to load ("Couldn't load gear advice") when an item's only
  auction house listing is older than yesterday: the Auction House group's total read the fresh
  listing, which such an item doesn't have.
- The MCP `gear` tool reads the character's reputations and PvP rank, as the gear page does.
  Without them every reputation gate was unknown, a Friendly quartermaster's cape a hard pick, and
  the tool's best item for a slot could differ from the page's.
- A weapon's own glow (its ItemVisual from the game data) is drawn only on the attachment points
  its model has. The Dreamstaff's visual names five points, the staff has four: the fifth glow sat
  in the hand.
- The MCP check gives the model the tools' clock. The tools run on the demo's frozen day, the
  model on today's date, so "where is the character now?" read six-day-old data as stale and
  answered NONE (`zone_now`). The docs count 71 questions (4 in market).
- A level whose Polestar split a crash lost, or left partial, gets its whole time. The game's
  /played at the level's start and at the next one (Altoholic's saves, the Recorder's dings) gives
  it: one history read level 33 as 1,901 s at 27,158 XP/h where it took 12,260 s, and had no time
  at all for level 32 (9,982 s). The pages, `levels` (`from: altoholic`) and the analysis use it;
  its XP is the bar's size at the level. A save the game answered before a ding but wrote after it
  names the new level with the old one's start, and is dropped. A finished level whose ding nobody
  saw no longer takes the next levels' longest silence.
- A death written at login no longer takes the previous session's zone: the walk forgets the zone
  at a logout (a death in Thousand Needles read Stormwind).
- Kill XP sums the kills whose XP line the Recorder matched, instead of going unknown for a whole
  level or zone as soon as one kill had none. `kills_without_xp` stays beside it, and its note
  gives the actual reasons: kills another player tapped or grey ones (no XP), some instance kills,
  and XP lines the Recorder couldn't match. It used to blame an old Recorder.
- Known flight paths keep every continent. The flight map lists its own continent's nodes only, so
  opening one in Kalimdor forgot the Eastern Kingdoms (9 known paths read where there were 18).
- Zone time leaves flights out: a taxi to its land is flight time (`taxi_s`), not time in the
  zones it crosses, and a zone only flown over is no visit. The game's zone text flickering to
  another zone and back within 3 s (a loading screen, a cave mouth) is no visit either. Zone
  time, visits, XP per zone hour, the zones tool, the sessions' main zones and the page's zones
  all read it.
- A slot's best pick no longer goes to a craft the character can't make over an easier, better
  drop. The pick took the group-loot factor (x0.85) off a dungeon drop's whole score, so in PvE
  Green Silken Shoulders (+2.0, Tailoring 165 with 141) beat Bloodmage Mantle (+2.4, a 43% drop).
  The pick now ranks on the same value as the upgrade list: the gain, less the effort's share,
  times the fit factor. `gear options` says `available: "once you can make it"` and `now: false`
  for a craft still to skill up or learn, never "now".
- `gear` with `options: true` keeps `buy_now` and `auction_house`, so "which upgrade is on sale"
  works either way.
- The gear notes and docs credit ForeverDB.net's weights for the scores. Some still said
  "Polestar's gear points" (the `gear options` and `next_moves` notes, `class_meta`, the FAQ and
  glossary). Polestar still gives the route, the guide and where gear comes from.
- The share token no longer crash-loops the ui after a full disk. A failed write left
  `share-token.1.tmp` behind, and in a container (pid 1) every restart failed on it. The
  temporary file now gets a fresh name each time and is removed whatever fails.

### Upgrading

- The analysis database's version moves to 15 (levels, zone time and visits, flight paths): kept
  findings show as "to revalidate" once.

## 0.10.0 (2026-11-24)

What 0.10.0 brings over 0.9.0, in short. Every change follows.

**For players**

- Gear is scored with ForeverDB.net's stat weights, for Leveling, PvP or PvE (your pick, kept
  per character). Polestar's weights are gone from the scores.
- Every upgrade says where to get it and how hard that is: easy, some effort, hard or long
  shot. A rare drop never becomes the pick; an auction house listing makes anything easy.
- Reputation and PvP rank gear checks what you hold ("16,500 rep to go", "rank 3 needed").
- Next moves stop telling you to train what you already know (professions and riding).

**For your AI**

- `gear` with `options` scores in the mode you ask, and `prices` and `gear` read a full
  auction scan again (over 5,000 items on one second broke them).
- The full analysis reads auction prices: "which upgrades are cheapest on the AH" is one query
  (`ah_prices`, `ah_upgrades`). Kept findings show as "to revalidate" once.

**For hosts**

- Auction scans are shared between friends on one install, per ruleset and faction, prices
  only, kept 90 days. Run `wow-stats users ah-share` once after the upgrade.
- The MCP check grows from 10 questions to 70, and its tool-routing half runs in CI.

### Fixed

- The knowledge-base refresh no longer fails a model step on an expired identity token. GitHub's ID
  token lives about five minutes and the SDK re-reads it at each exchange, so the weekly review's
  community step got a 401 six minutes after the mint. Every model step now runs under
  `tools/kb/with-oidc.sh`, which mints first and again every three minutes while it runs; a test
  fails any step that sets `ANTHROPIC_IDENTITY_TOKEN_FILE` without it.
- /zogzog/ no longer prints "levels undefined-undefined" on a drop with no level range (a world drop,
  an open-world rare). Its redactor sends an absent range as an empty list where the full view sends
  none, and the page only checked that one was there. Every level range on the page now goes through
  one reader that shows nothing unless both ends are known.
- The release's browser tests caught up with 0.10.0's gear: the fake gear answers take the scoring
  mode the server passes since the three modes (the live page's gear tab got a 500 and drew no
  upgrade), the gate and fit checks take their items from the regenerated fixture instead of two
  items ForeverDB's weights no longer pick, and ForeverDB.net's credit link is allowed next to
  Wowhead (its licence asks for it wherever its data shows) while every other outside link still
  fails.
- A full Auctionator scan writes every item at the scan day's midnight. Once that was more than
  5,000 items at one instant, the MCP `prices` tool refused to answer ("one second holds more
  records than a read can return") and `gear` lost the auction house without a word
  (`auction_house: null`, no "buy it" upgrades). The MCP now reads the scans the way the gear
  page does: one stats row per item, auction house and scan day. `gear` takes the page's own
  price read. The page and `next_moves` already did.
- The MCP check's stand-in store answered `_time:3d` on the wall clock while its stdio server ran
  on the demo's clock, so the gear advice showed no scan there. The store now runs on the
  server's clock, and the auction upgrade question is back.
- The daily KB watch no longer moves an addon or Polestar back to an older release while the
  newer one still exists; when the newer release is really gone, the commit says so. The KB
  watch tests no longer break when a watch PR moves `data/kb/watch.json` (#211).
- Next moves told an Expert enchanter to "Train Expert Enchanting". Polestar Recorder writes a
  skill record only when the skill list changes while it records, so a trainer visit it didn't
  see never reached us and its last record (150/150) stood. The shipper now also reads each
  profession's cap from Altoholic's `DataStore_Crafts` (the game's own list, rescanned at every
  login) and the higher cap wins. Without Altoholic, the move says what it last saw and when
  ("Enchanting 150/150 when Polestar Recorder last saw it (17 Nov): if you haven't trained
  Expert since...") and its title ends "if not done yet", on the page too.
- Riding had the same blind spot: with no Polestar Recorder record of the Riding skill, next
  moves and the mount goal took riding as not trained, so a training the Recorder didn't see
  read "not learned". Riding now also comes from Altoholic's spellbook (`DataStore_Spells`,
  read at each login: Apprentice Riding is spell 33388) and the Recorder's learn records; the
  highest rank wins. Past level 40 with nothing seeing riding, the move says so and when it
  last looked ("No riding in your spellbook as Altoholic last read it (23 Nov) nor in Polestar
  Recorder's records: if you haven't trained Apprentice Riding yet..."), its title ends "if not
  trained yet", and `mount_goal` marks the tier `may_be_trained`. The gear page's riding share
  and the semantic layer's `riding` model (new `riding_rank`) read the same.

### Changed

- The play analysis sees Auction House prices. The lab's 0.10.0 run left "which upgrades are
  cheapest on the AH" unanswered with a full Auctionator scan sitting right there. Prep 14 adds
  `ah_prices` (the price list's own read of the character's houses, one row per item, house and scan
  day, `PRICE_DAYS` back) and `ah_upgrades` (each upgrade with its newest price, on sale now or not
  by the gear advice's rule, score per gold, the easiest other source). The semantic layer reads them
  as `auction` and `ah_upgrades`, so the cheapest upgrade on sale is one `query_metric` call. The
  brief adds one line (scan age, upgrades priced and on sale) and stays under its size cap. Shared
  scans carry no scanner and neither do these tables. Findings on them are judged and rechecked like
  any other; the bumped prep version marks older findings to revalidate.
- PvP rank gear reads your rank from Syndicator or Altoholic too: both save Forever's "Renown - PvP
  Rank" currency (3473), and the shipper now sends that one currency, nothing else from either
  currency table. ForeverDB stays optional; the newest of the three saves wins. With none saved,
  the gear says "your PvP rank isn't recorded yet" instead of asking you to install ForeverDB.
- Auction scans are shared on an install with friends (owner: "a scan of the AH per ruleset"). A
  scan is one ruleset and one faction's house, so everyone's newest scan of their own rulesets and
  factions feeds their gear advice ("on the AH, easy"), price list and `prices`. Only the price
  fields go (item, prices, quantity, scan day, ruleset, faction), never the character or the
  account; Horde never reads Alliance, rulesets never mix. The shared space is one tenant of the
  friends' store; a deleted friend's past prices stay in it, since nothing there says who scanned
  them ([Shared auction scans](docs/self-host/friends.md#shared-auction-scans)).

- The shared auction scans keep 90 days (`PRICE_DAYS`), then older rows are pruned once a day
  (a delete task on the friends' VictoriaLogs, a `DELETE` on the SQLite file). A deleted friend's
  anonymous prices are still not deleted.

- Where gear comes from is ForeverDB.net's addon data now (GPL-3.0 with an attribution term, bundled in
  `data/foreverdb/`): 7,577 items with quests, vendors and their price, boss drops with the chance players
  measured and over how many kills, rares, crafts, reputation, PvP and containers. Each source says how
  sure it is (seen in Forever, Forever client, Classic data, a clue) and links ForeverDB.net. 856 of
  ForeverDB's 935 picked items have a source, 462 before. Polestar's sources fill in only where ForeverDB
  has none.
- The gear advice weighs how hard an item is to get (owner: "convenience and easiness before a quarter
  of a point"). Each item is easy, some effort, hard or a long shot from its easiest source; a harder
  item wins a slot only when it scores 5.3% (some effort) or 17.6% (hard) more. A long shot (a drop
  under 1%, Classic's data alone, a PvP rank not held) is never an upgrade: it shows as the slot's best
  possible with its odds ("0.01%, about 10,000 runs"). An auction house listing makes anything easy.
  Drops say their expected runs or kills.
- `gear` with `options` scores in the asked `mode` (PvE, PvP) and says it (`mode`, `mode_from`), like
  `gear` without options.
- Reputation gear is checked against the recorded standings (the Recorder and Altoholic): an upgrade
  once the standing is held, else "you're Friendly: 16,500 rep to go". PvP rank gear is checked against
  the rank ForeverDB's armory snapshot saves (the shipper reads `ForeverDB.lua`, new `wow-foreverdb`
  events): a rank held makes its gear some effort (the quartermaster), else "rank 3 needed, you're
  rank 1". Without ForeverDB, 0 honorable kills in Altoholic still means no rank.
- The stat weights come from the same addon data (`Data/GearFinder.lua`) instead of the website's files:
  one number moved (Marksmanship PvP mp5, 0.028 to 0.03), 138 more set pieces.
- The KB watch refreshes ForeverDB's data at most once a week (`tools/kb/bump.py foreverdb`,
  `foreverdb` gate, `watch.json` `foreverdb.taken`); releases download nothing.
- Gear items come from the game's own tables now, not Polestar's item table: Forever's client
  tables and its hotfixes (`data/game-items.json`, `tools/game_items/export.py`), the way the
  3D view reads them. 17,908 weapons and armour pieces, 1,186 more than before, with Forever's
  numbers (Staff of the Shade: 37 Shadow damage, not Classic's 21). Scored from them, 36,674 of
  ForeverDB.net's 36,746 ladder scores match.
- Where gear comes from adds forever-db's quest rewards (the quest database Polestar bundles):
  172 more items with a quest to get them from.
- Gear nobody says where to get shows as the slot's best possible when it beats what can be got:
  the gear page lists it apart, the answers as `bis_unknown` with `"where": "unknown"`. It is
  never an upgrade to go and get; standings and "N% → M%" lines measure against what can be got.
- The analysis brief's upgrades carry their easiest source and its `effort` (the others through
  `upgrade_sources`), and list five weak worn items, built-for-someone-else first.
- Required level 1 counts as none (ForeverDB's rule): Forever's own items count from their item
  level - 5.
- Hit that only works with a bow, gun or crossbow counts for hunters only.
- Every MCP answer that shows a gear score, standing, BiS or upgrade carries `score_source`, the
  ForeverDB.net link, and every place on the page that shows one links it (next moves, the
  comparison, dungeons, level snapshots).

- Gear scoring is now ForeverDB.net's: the widely used community stat weights per spec, its
  formula, and three modes: Leveling (the default: Stamina, armour and a caster's wand count
  much more), PvP and PvE. Pick the mode on the gear page; the MCP `gear` tool takes `mode`.
  Every score carries the credit "Weights: ForeverDB.net" with the link (page legend, `source`
  in MCP answers). Checked against ForeverDB's own ladders: 36,690 of 36,746 scores equal,
  the rest a tenth apart.
- An item with no required level is wearable from its item level - 5, a quest reward from the
  quest's level - 5 (ForeverDB's rule).
- The gear page's Weights pick (Leveling, PvP, PvE) is kept on the server per character, not
  in the browser. Your AI's `gear` answers use the same pick; its `mode` argument still
  overrides it for one call (`mode_from` says which). The plan, next moves, the analysis and
  the public page's badges stay on Leveling.
- "Who an item is built for" (the fit label and the group-loot tip) reads ForeverDB.net's
  weights in the mode you score in, not always PvE. Leveling halves a healer's healing, so a
  Shadow priest now reads "Built for your spec" on caster pieces that carry some healing.
- An upgrade has to gain at least 1% over what you wear, for everyone.

### Removed

- Every Polestar weight from the gear scores: its level-band weights, racial multipliers,
  school damage cap and formula tables are out of `data/gear-reference.json`, which now keeps
  only the items, their sources, the quests and the class rules (who wears what).
- Polestar's gear settings: your Adjust weights edits, its Stat weights spec and its smallest
  upgrade no longer change anything. The shipper stops sending them (`polestar_spec`,
  `gear_adjust`, `min_upgrade`); what an older shipper sends is stored and never read.
- `weights_adjusted_by_player` in the MCP `gear` answer and `spec.adjusted` in the gear API.

### Upgrading

- Shared installs: run `wow-stats users ah-share` once after the upgrade, so the auction scans
  stored before it reach the shared space. Until then each person's gear and prices see only
  the scans sent since the upgrade. One-person installs: nothing to do.
- Profession caps from Altoholic come with the new shipper (it updates itself) and its next
  character sheet, sent after a logout or `/reload`. Until then a trained rank the Recorder
  missed still shows, hedged. The sheet's professions gain `cap` and `cap_at`
  (docs/contract.md).
- Every gear score, gain and "% of BiS" changes, and so do the upgrades and the dungeons
  suggested from them. A caster levelling sees Stamina pieces and wands rise, Spirit pieces
  drop. Nothing to do on the host: `data/foreverdb-weights.json` ships in the image.
- A player who had set a spec in Polestar's Stat weights and nothing else gets the spec from
  the gear page, the site's `WEIGHTS`, ForeverLevelingBiS or GearQuestForever, else the
  talents. Pick it on the gear page if it changed.
- Kept findings show as "to revalidate" once: the run manifest's gear scoring no longer
  carries the Polestar edits.
- Gear page users who picked PvP or PvE on main since the ForeverDB change pick it once more:
  the browser's copy isn't read.

### Docs

- The docs lead with what you get, then three ways in: players, people using an AI assistant, hosts. The README and the home page say it in their first screen; the player and AI pages open with a three-step start.
- The host guide (`docs/self-host/production.md`) is the hosts' front page: what you need, the three choices before your first event (where your events live, who logs in, what you share), every step in order, what's locked down for you.
- How this was built: the six days from a levelling question to this, and why it ended up like this.
- Fixed where the docs disagreed with the code: the start page says "It works" (not "Data arrived"), `char` is `Name-Realm`, the Overview has no "ahead of the route", People is a section of the Character tab, the Battle.net setting's wording, the SQLite store in backup, upgrade, monitoring, friends, troubleshooting and uninstall, `RENDER_TOKEN_FILE` in the environment reference, and a 0.7 to 0.8 upgrade section. A `-f` on the command line replaces `COMPOSE_FILE`: the proxy pages say so.
- The server analysis' default timer and the friends' purge delay are filled from the code (`analysis_hours` and `purge_days` in `tools/docs_values.py`).

### Tests

- `test_push_reaches_every_pod` no longer fails now and then under parallel runs: its "down pod"
  is a port held by a bound, non-listening socket, so no other test worker can take it and the
  push always gets a refused connection.
- The MCP check grows from 10 easy lookups to 70 questions in 15 groups: multi-tool questions
  ("2 hours tonight", "am I slower than usual"), every gear mode and another spec, hedges on
  riding and trainers, plans checked with `check_plan`, prices, people, the knowledge base,
  missing addons, unknown names, an injected player name and French. A run passes a question
  only when the answer is right, the expected tools were called and nothing forbidden was said
  (a long shot as the gear pick, an invented count). `--jobs`, `--max-usd`, `--group`,
  `--coverage`.
- Its deterministic half runs in CI: `tests/test_eval_mcp.py` runs every question's probes on
  the fixture server without a model, and fails when an MCP tool has no probe and no reason in
  `tools/eval_mcp/coverage.yaml`, or a listed mode (`gear:mode=pvp`, `deaths:since`,
  `scenario:no-recorder`...) has none.
- `tests/test_workflow_guard.py` checks the pull-request workflows against main's required
  checks (`.github/required-checks.txt`, kept equal to the ruleset by hand): no reusable-workflow
  call job named like a required check, and each required check made by exactly one job (#202's
  skipped `test` passed the gate).
- `tests/test_riding.py`: riding from a DataStore_Spells save through the shipper to the moves
  and the mount goal, hedged when nothing saw it.
- `tests/test_gear_foreverdb.py` replaces the Polestar Lua parity test: weights, ladder
  scores, picks and weapon setups against ForeverDB fixtures, in the normal suite. A slice
  checks the page and the MCP agree in each mode.
- `tests/test_gearmode.py` and a slice check the mode kept on the server: the page's pick
  reaches the MCP, `mode` overrides it, Leveling clears it. `tools/gearfit_check.py` judges
  the levelling tables in Leveling and the level-60 ones in PvE.

## 0.9.0 (2026-11-23)

What 0.9.0 brings over 0.8.0, in short. Every change follows.

**For players**

- The time in your current level is one number everywhere: the page and your AI both count
  the Recorder's sessions since your last ding, and time played includes it.
- Gear coverage stops asking for Syndicator when your gear sheet is already there.

**For your AI**

- `character_summary` gives the time in your current level, and time played counts it.
- `dungeons` with `options` no longer lists the other side's city dungeon, and `next_moves`
  prices auction-house upgrades instead of calling them out of reach.

**For hosts**

- The install guide runs the released image (`WOW_STATS_IMAGE`, tag@digest): no Docker Hub
  login, no build.
- Events can live in SQLite files instead of VictoriaLogs (`EVENTS_STORE`,
  `compose.sqlite.yaml`): one container fewer. VictoriaLogs stays the default.
- `ui` sizes its memory from its container's limit and keeps one DuckDB copy per character:
  650-870 MiB on the demo instead of 950-1,020.
- `/admin`'s AI analysis cell works again (`store: AttributeError` since 0.7.0).

### Added

- Your events can live in SQLite files instead of VictoriaLogs: `EVENTS_STORE=sqlite:<folder>`, one file per person, read by the app itself. In the compose kit, `compose.sqlite.yaml` on top of `compose.yaml` (or `COMPOSE_FILE=compose.yaml:compose.sqlite.yaml` in `.env`): one container fewer, no database memory to size. Friends get a file each in the same folder (no `FRIENDS_VL_URL`), and removing one deletes their file after the usual `FRIENDS_PURGE_DAYS`. What you give up: the archive of older days (`VL_ARCHIVE_URL` is refused), VictoriaLogs' own UI and Grafana; the page's query box answers the LogsQL the app itself uses and says so for the rest. Back it up by copying the `events` volume with `ui` and `publish` stopped. VictoriaLogs stays the default and fully supported. The two don't convert: pick before your first events. [Where your events live](docs/install.md#where-your-events-live).
- `LOGS_FILTER` and the query box take a stream filter (`{component="wow"}`) without a trip to VictoriaLogs.

### Changed

- The install guide runs the released image: `WOW_STATS_IMAGE` (tag@digest, from the release notes) in `.env`, then `docker compose pull` and `docker compose up -d`. No Docker Hub login any more; building from source (`docker login dhi.io`, `--build`) moved to [Development](docs/development.md#build-the-image-yourself). The quick start, the demo, upgrading and the reverse proxy pages follow.
- Every read, write, deletion and health check of the events goes through one module (`wowstats/eventstore.py`); the admin page's store check names the store in use.
- One DuckDB copy per character, shared by the page, the MCP's tools, the planner and the gear advice (`wowstats/analysis/copies.py`). Before, each built its own (the tools kept 8, the page 4, the planner one per plan, the gear answer one per call), and each build left the heap about 350 MB bigger. A copy is rebuilt when the character's events change, swapped in before the old one closes when there is room, closed when it is the least recently used past `ANALYSIS_COPIES_MAX`, never under a request still reading it; one build at a time. Gear advice is scored inside that copy and taken back out.
- The copy is filled column by column instead of parsing the rows as JSON: the records table's build went from +230 MB to +45 MB, as fast.
- `ui` sizes itself from its container's memory limit (`/sys/fs/cgroup/memory.max`, cgroup v1 too): DuckDB's `memory_limit` and threads per copy, how many copies (`ANALYSIS_COPIES_MAX`), how many analysis workspaces (`ANALYSIS_WORKSPACES_MAX`) and what one query may add. At 1 GiB: 2 copies, 1 workspace. Without a limit the old defaults stay (4 copies, 6 workspaces). [Memory](docs/install.md#memory).
- On the compose kit's demo (three characters, the 11 main tools, the page, 4 uploads) `ui`'s container settles at 650-870 MiB instead of 950-1,020, and the `ui` process' peak went from 804 to 711 MiB.

### Fixed

- `/admin`: the AI analysis cell showed `store: AttributeError` for every person since 0.7.0.
  It handed the timer the page's store, which can't run the timer's query; it now reads
  through the MCP's store, like the server's own runs. The server's runs and the nightly
  recheck were not affected. A broken cell now shows the error's message, not only its type.
- The time in your current level is one number: the page said 2h26m, the MCP's `character_summary` said nothing and its time played left the level out. Both now read `in_level_s`, the Recorder's sessions since your last ding (a new semantic-layer metric), and time played is your level splits plus that. One session rule everywhere: a silence over 330 s ends a session (the page counted 315).
- Gear coverage no longer says "unavailable, add Syndicator" when your gear sheet is there. The gear sheet and the bags are two kinds now (`Missing: your bags` alone, with the wait, when only the bags are late; one line for both when Syndicator is missing), a source with any part on record is at least `partial` (a character sheet before your first ding too), and no line tells you to add an addon whose data already arrived.
- `dungeons` with `options` no longer lists the other side's city dungeon (Ragefire Chasm for an Alliance character): same list as the Dungeons panel.
- `next_moves` no longer says auction-house wins are out of reach: the gear advice prices what's on sale.
- The self-host page said a friend's data goes the moment they leave the list: it goes 7 days later (`FRIENDS_PURGE_DAYS`), or at once with `users delete --yes`.
- The demo sends Syndicator's bags and lists Syndicator in its setup, as a player with it would.

### Upgrading

- `ANALYSIS_WORKSPACES_MAX` now defaults from `ui`'s memory limit: 1 at 1 GiB, 6 without a limit. Set it to keep the old 6 under a limit. New: `ANALYSIS_COPIES_MAX` (2 at 1 GiB, 4 without a limit).
- Nothing to change for an install on VictoriaLogs. The SQLite event store is opt-in (`EVENTS_STORE=sqlite:<folder>`, or `compose.sqlite.yaml` on top of `compose.yaml`) and doesn't convert: an install that switches starts with no events, so pick it before your first ones.
- To move to a released image, put `WOW_STATS_IMAGE=<the release's tag@digest>` in `deploy/compose/.env` and run `docker compose pull && docker compose up -d`. Built from source, keep `WOW_STATS_IMAGE` empty and `docker compose up -d --build` as before.

### Tests

- Integration slices (`tests/slices`, marker `slice`, in the normal suite, about 30 s): five golden characters sent through `/ingest` into a VictoriaLogs stand-in and into the SQLite event store, then read by the page API and the MCP tools. They check that one number means one thing on every surface (level, time in level, played, pace, ETA, dungeon trip lines, best upgrade per slot), that coverage and `missing` agree with what arrived, that every number in a move has a source, and that no move, raid prep or class effect of the committed snapshot (`tests/slices/golden.json`) goes away. The KB refresh runs the same snapshot as a gate (`behaviour` in `tools/kb/gates.py`): the #189 digest that took the Onyxia attunement from every level 60 would have been refused.
- The degraded suite and `tests/test_logsql.py` run on both event stores; the `logsql` CI job compares the SQLite store's answers with a real VictoriaLogs', and any test fails when the SQLite store meets a query outside its subset.

## 0.8.0 (2026-11-23)

What 0.8.0 brings over 0.7.0, in short. Every change follows.

**For players**

- Dungeons are planned as whole trips: pickups, travel, time inside and hand-ins, from where
  you were last seen, at your own pace. "~1h55 trip for ~58k XP + 4 quest upgrades, 9 drop
  upgrades (88% a run), 2.1× your questing".
- Dungeons run in parts get one line per wing: Scarlet Monastery, Dire Maul, Blackrock Spire,
  Stratholme and Maraudon.
- A dungeon you haven't run gets a kill XP estimate and a time from its kills, labelled
  `estimate`. Your own runs replace both.
- The public page shows the BiS badge on each worn item, as the full view does.
- One demo, the docs site's Nibbet, loaded with `docker compose run --rm demo`.

**For your AI**

- 38 tools instead of 41, one question each: `gear_options`, `dungeon_options` and `people`
  became `gear`, `dungeons` and `players` with `options`/`groups`. See "Upgrading".
- `list_metrics` without arguments is a 29,000-character index instead of 200,000.
- Every answer about a character carries `as_of`, the newest event it read, and the MCP reads
  through the page's cache: same `as_of`, same data.
- A character or metric name that doesn't exist is an error, not an empty answer.

**For hosts**

- The compose kit's VictoriaLogs runs with query limits and a 1 GiB memory cap.
- `/healthz` says when VictoriaLogs stops answering.
- The renderer keeps each person's game files apart.

### Added

- Kill XP for a dungeon you haven't run: `dungeons` with `options` gives `gain.kill_xp`, a run's kill XP, kills and time inside at your level, labelled `estimate`, or `not_enough_data` with what's missing. Your own runs replace it (`measured`). The original dungeons come from cMaNGOS's spawn tables (85% of the packs, per wing for Scarlet Monastery, Dire Maul, Blackrock Spire and Stratholme), Ruins of Lordaeron and The Drowned City from players' logged runs, City of Dalaran from two reference runs; the six other new dungeons say not enough data yet. Classic's kill formula, elites ×2.5, five players of your level, Forever's 60% dungeon factor; no buffs. The estimate counts in the run's XP, its "× your questing" and the dungeon ranking, labelled. Checked against a recorded Stockade full clear (+3%) and Dalaran's two runs (−10%): [Dungeon kill XP](docs/dungeon-xp.md).
- The Dungeons panel shows a run's kill XP under "What a trip is worth", after the quests and the gear: measured on your runs, an estimate, or not enough data yet. One line at the top says what Forever's dungeons are for: their quests and gear, not running the same one over and over.
- Dungeons run in parts get one line per wing, in the Dungeons panel, the Overview's move and `dungeons` with `options`: Scarlet Monastery (Graveyard, Library, Armory, Cathedral), Dire Maul (East, West, North), Blackrock Spire (Lower, Upper), Stratholme (Live, Undead) and Maraudon (Orange, Purple, Inner). Each wing has its own level range, quests (the ones whose objectives are in it; a quest that needs several wings counts in each and `gain.quests[].wings` says so), pickups and hand-ins, kill XP, drops and keys. Your runs count for a wing when your kills stayed there; otherwise the wing is an estimate. A handful of quests done outside every wing (deliveries, outdoor steps) are on no wing's line.
- A dungeon's pickups, travel and hand-ins run at your pace: each of your past trips is replayed on Polestar's rules as you stood at its start, and your measured legs over the planned ones (summed over your trips, kept between 1× and 4×) stretch the next trip's. The line's split says the stretched minutes; `gain.trip.pace` gives `{factor, trips, label}` and those parts are labelled `your pace`. No trip yet: 1×, the straight run. Time inside is unchanged. One player's four trips: 2.13×. Semantic layer: `quest_closed_t` (quests) and `done_seen_t` (done_quests).
- `/healthz` on the full view says when VictoriaLogs doesn't answer: `"store": "failing"` with `store_failed_at`, still `200`, like `semantic` ([Monitor it](docs/self-host/monitoring.md)). It said `ok` while uploads and fresh reads failed.

### Changed

- A dungeon's line says the whole trip, from where you were last seen, and counts both kinds of upgrades: "~1h55 trip for ~58k XP + 4 quest upgrades, 9 drop upgrades (88% a run), 2.1× your questing (20 min pickups · 1h15 inside · 15 min hand-ins)". The pickups, travel and hand-ins follow Polestar's pick-up and hand-in rules on your own quests (done, in the log) instead of the mean of your other trips; your runs there still replace each part. Quest upgrades are the trip's quest rewards you'd wear; drop upgrades are your missing upgrades that drop there, with the chance one drops in a run. A part with nothing in it is left out. `dungeons` with `options` gives each part as `gain.trip` with its label (`measured`, `route`, `your pace` or `estimate`): [Route reference](docs/route-reference.md#dungeon-trips).
- A dungeon you haven't run takes its time from its kills (kills × the seconds a kill took in the reference runs) instead of a guide's clear time, in the panel's line, the Overview's move and `dungeons` with `options` (basis `kill_model`). Gnomeregan at 34 goes from ~40 min to ~1h15 inside; a real run takes about an hour. Your own runs still replace it.
- A dungeon with neither a run of yours nor a kill estimate no longer says "slower than questing: go for the drops only" (or faster, or about the same): that compared the dungeon's quests alone with your questing, and the kills could flip it. The line keeps the time and what the quests are worth, and `vs_questing` is `null` there.
- The public page shows the BiS badge on each worn item of the paperdoll (BiS, 2nd, or its share like 62%) and the badge legend under it, as the full view and `/zogzog/` do. Privacy: what's newly public is, per worn slot, the badge's rank, percent and label (`char.gear.standing`, [privacy table](docs/explanation/privacy.md)). Nothing else of the gear advice: no upgrades, no best item, no where to get it, no comparison on click.
- Every answer about a character says which data it was computed from: a top-level `as_of`, the newest event time it read, on the MCP's answers and the full view's `/api/char`. The MCP now reads a character's events through the same cache as the page instead of asking VictoriaLogs on its own, so the same `as_of` on both sides means the same data. Cached answers (the planner's, the analysis workspace, kept findings and plans) keep the `as_of` they were built from.
- `query_metric`, `play_sql` and the finding judge see new data on the next request after it lands, like the page, instead of up to 30 minutes later. A request already running finishes on the data it started with.
- Your AI has 38 tools instead of 41, each answering one question: `gear` with `options` gives what `gear_options` gave (every way to an upgrade per slot, with its cost), `dungeons` with `options` what `dungeon_options` gave (the dungeons worth it now, ranked), and `players` with `groups` the dungeon runs with group size, bosses, wipes and deaths that `people` added. The `gear` and `dungeon` prompts call the new forms. The tool list a client reads at connect is 34,500 characters instead of 35,600.
- `list_metrics` without arguments answers with an index, each model's metrics with one line each, about 29,000 characters. It was the whole catalogue, 200,000 characters: over Claude Code's limit for one tool answer, and the server's own analysis saw only the first fifth. `names`, `model` or `search` give the full definitions, 40 metrics a call.
- `kb_get`, `kb_lookup`, `kb_search`, `play_sql` and `query_metric` are marked read-only, so a client no longer asks you to approve each lookup. They only count the call in an open analysis run.
- The words say what the code does, no more. A kept finding reads "Numbers recomputed from your data" (its SQL runs again each night; that doesn't prove the sentence true). A guide step past 3× its plan says to look at what holds it up and skip it only if no later step needs it: the code can't tell a prerequisite or travel from a detour. Tool descriptions and prompts call the judge's findings "kept", not "verified". A source's `complete` coverage is documented as "no hole found": only the Recorder and Battle.net are looked at for one.
- The session plan's "Skip or drop" section is now "Look at, maybe skip", and the coach prompt's "What to skip" likewise: both say to look at what holds a step up and skip it only if no later step needs it.
- One demo. The local demo (`dev/seed.py`) loads Nibbet, the docs site's made-up priest, from the same generator (`tools/fixtures/generate.py`), instead of five hand-written characters (Testchar, Testalt and three dressed for the 3D view) that had no class, race, gear or talents. In the compose kit it is one line on every system, `docker compose run --rm demo`; the piped `python - < dev/seed.py` is gone. What it lacks next to the site's demo (Battle.net, the 3D view, the pictures) is listed in [Try the demo](docs/self-host/demo.md).
- The demo's characters show up on the page seconds after the seed, not 80 seconds later: the seed sends through `/ingest` like a shipper (`--ingest`), which wakes the page.
- With `MCP_DCR=off` the authorization server metadata no longer advertises a `registration_endpoint`, and `/register` answers 404: a client sees up front that it needs a client metadata URL instead of trying to register and getting refused. An unknown client id at `/authorize` is no longer pointed at `/register` either. `open` and `loopback` are unchanged.

### Fixed

- A quest that needs a boss in several wings (Scarlet Monastery's In the Name of the Light: the Library, the Armory and the Cathedral) no longer counts its full XP, reward and hand-in on each wing's trip. A wing's trip pays it only when it finishes it: complete in your log, or the other wings' bosses killed on your trips since you took it. Until then the wing's data lists it under `quests_needing_other_wings`. The Armory at level 38 went from "~35 min trip for ~9.7k XP" with 4,700 of it from that quest to "~25 min trip for ~5k XP".
- A level-60 character gets the "Before Barrow Deeps, Hyjal Summit and Onyxia's Lair open" moves again (the Onyxia attunement, starting with Dragonkin Menace) and the raid's class effects. A news item from 23 Nov mentioned the raid opening again with no preparation, and the newest mention replaced the whole event. Mentions of an event now merge: a newer one can move its day, window, levels or status, but only one that brings preparation or effects restates its name and description, and an empty field never erases. The weekly news pass writes a bare re-mention the same way, with the event's name and no description.
- A dungeon run's kill XP (`dungeon_trips.xp_inside`, `trip_xp_inside`, the dungeons page's kill XP) no longer holds the quests handed in inside the instance. Polestar's run XP is the bar's gain inside, so a quest turned in to an NPC in there (Blackfathom Deeps' In Search of Thaelrid) counted as kill XP and again as quest XP.
- The analysis judge refuses a `{n:pct}` or `{n:dur}` placeholder on a value whose unit it doesn't fit (a count shown as "2%", kills shown as a duration). The refusal says which format fits.
- The judge takes a raw query's units from what each column is made of, not from its name: `SELECT played_s AS n FROM levels` with `{n:pct}` was kept as "Level 34 took 7100%". An alias, a cast, a sum or an average, a CTE or a subquery keep the unit of the columns they read (a view per grain's metric, a prep table's `*_s` seconds or `*_pct` percent), one count or time over another is a ratio, and `{n:dur}` on `played_s AS n` stands. A value whose unit can't be told (a plain `xp`, a literal) gets neither format: write `{n}` there. Found by a pre-release check.
- Knowledge base statements take their `source_type` from their URL (`data/kb/sources.json`), not from the model: 353 records changed, mostly Wowhead and ForeverDB guide pages and kami-labs.fr that said `datamined` and are now `guide`. The KB jobs write it the same way, and a record whose type differs from its URL's is refused.
- A character name that isn't one of yours is now an error naming your characters, instead of "Nothing arrived yet" as if it existed and hadn't played. One of yours with nothing arrived yet still answers. An unknown metric in `query_metric` or `list_metrics` is an error too: both used to come back as a normal answer.
- A finished level with no Polestar split keeps its row: `levels` lists it with its time, XP and counts unknown (null, never 0), the analysis keeps it instead of dropping it when no Recorder ding came out of it either, and the `polestar` coverage reads `partial` with that level in `gaps`, on the page's data and every MCP answer, instead of `complete`. `character_summary`'s `avg_last_5_levels` and `xp_per_hour_last_3_levels` are unknown while their window holds such a level, instead of averaging the others.
- XP gained is worked out from the bar's readings in order, by the analysis and the Journal alike, not taken from the shipper: a Recorder chunk recovered after the ones that follow it no longer loses the XP of its step (100 → 200 → 300 counted 100 instead of 200), and the Journal shows the same XP as your AI.
- `deaths` over a range with no Recorder record (before it was installed, or while it was off) says `total: null`, not 0, and "The Recorder wasn't on for this period."
- The renderer keeps each person's game files in their own folder. It kept them by file id for everyone, so a file one friend's shipper sent could stand in for another's in their past-level pictures.
- The compose kit's VictoriaLogs now runs with the limits [security.md](docs/security.md) asks for: 30 s per query, 4 queries at once, 10 s in the queue, and 1 GiB of memory (`VL_MEM_LIMIT`). Until now it ran on VictoriaLogs' defaults with no memory limit.
- The Python answers to the views' LogsQL (`wowstats/logsql.py`) now match VictoriaLogs v1.54.0 where they didn't, checked against a real VictoriaLogs in CI (`tests/test_logsql.py`): an unnamed `count()` is `count(*)`, `sum()` of no number is `NaN`, `min()` over rows without the field is empty, text sorts in natural order (item 9 before item 10), `inf`, `nan` and padded numbers aren't numbers, a row left with no field isn't answered, `<` and `'` are escaped in JSON values, and single-quoted strings go to VictoriaLogs instead of matching nothing.
- The LogsQL conformance tests (`tests/test_logsql.py`) write every time bound in UTC (`Z`): one had no zone, so the suite failed against a VictoriaLogs running in another time zone (`TZ=Europe/Brussels`).
- The README's demo writes `INGEST_PASSWORD` to `.env` instead of exporting it, so every later `docker compose` command works in a new terminal too (the Claude Code line in [Try the demo](docs/self-host/demo.md) failed there).

### Upgrading

- `class_meta`, `/api/meta` and the card's meta paragraph (`char.meta`) call the day their statements were last checked `checked` instead of `as_of`: `as_of` now means the data an answer read. A script that reads the old field needs the new name.
- MCP: `gear_options`, `dungeon_options` and `people` are gone. Call `gear` with `options: true`, `dungeons` with `options: true` and `players` with `groups: true`. A saved prompt or script that names the old tools needs the new call.
- Compose: VictoriaLogs gets a 1 GiB memory limit. It peaked at 217 MiB on a year of the demo; with a much longer history, set `VL_MEM_LIMIT=2g` in `.env` before `docker compose up -d`.
- The demo: `docker compose run --rm demo` replaces `docker compose exec -T ui python - --ingest ... < ../../dev/seed.py` and its PowerShell form. `docker compose down -v` removes the old demo characters with the rest.
- The renderer: the first picture after the update re-sends that person's files to the renderer once.

## 0.7.0 (2026-11-23)

What 0.7.0 brings over 0.6.1, in short. Every change follows, then each release candidate's.

**For players**

- Every slot says how far it is from its best in slot (BiS): a badge on each item icon
  (orange for BiS, purple from 85%, blue from 50%), and "70% → 91% on your chest" on every
  upgrade, dungeon drop and quest reward.
- Route times follow Polestar 0.4.2.2: every route to 60 is 16 to 25 hours shorter, nearly
  all of it from level 50 (the grind steps became quests). "Ahead" and "behind the route"
  read differently from 50 on.
- Numbers say what they count. The climb reads "1,225 kills and 88 quests handed in over the
  completed levels (levels 22–33)", then "Level 34 so far: 93 kills and 6 quests". Your page
  and your AI give the same numbers.
- A number that's missing something says so: "The Recorder missed about 2h of play (level
  31): deaths from then aren't counted", "Equipment last read 5 hours ago". Without the
  Recorder, deaths are unknown, never 0.
- Your picture and "View in 3D" are drawn from your own game files. Blizzard's armory renders
  are gone (they could show another player's character).
- A quest you finished counts as done everywhere, even with the Recorder off. Battle.net
  fills what your addons don't send, every 2 hours.
- Before the server runs your play analysis on its host's AI credit, your page offers the
  prompt to run it with your own assistant instead.
- When a host takes you off their friends list, your access stops at once and your data goes
  7 days later. Back on the list within those 7 days, you get everything back.

**For your AI**

- One computation path: the tools, `query_metric`, five ready-made tables (`per_level`,
  `per_session`, `per_zone`, `per_dungeon`, `per_slot`) and your page all read the same
  named metrics.
- A finding can't state a number its query didn't give, and every kept finding is rechecked
  each night, for every person.
- `character_summary` swaps `kills` and `quests_turned_in` for `counts` (completed levels,
  current level, both). `gear_options` names your current zone and marks what's there.
- Other players' names and outside text arrive without invisible characters, marked
  `untrusted`.
- `dashboard_refresh` refreshes "How you play" with your own AI, at no cost to the host.

**For hosts**

- The server can run the play analysis itself (Anthropic, OpenAI or any compatible
  endpoint) with a monthly cap. It stays off until you configure a provider.
- `MCP_DCR` (`open`, `loopback`, `off`) narrows who may register an AI client.
  `FRIENDS_PURGE_DAYS` (7) sets how long a removed friend's data waits, and a friends list
  that would remove everyone at once is refused.
- DuckDB and the semantic layer are core now: a failure keeps the last good answer, marked
  out of date, and `/healthz` says `"semantic": "failing"`.
- One-button releases, signed shipper updates, the image on Docker's hardened Python.
  Upgrading from 0.6.x: see "Upgrading from 0.6.x" under 0.7.0-rc.8.

### Added

- The docs site's demo draws Nibbet as a real 3D model, like Wowhead's dressing room, instead of the block figure: his gnome and his gear, read from a Forever client by `tools/fixtures/demo3d.py` and published only as the `demo-3d` release's asset, never in git. The Pages build checks it against `site/demo3d.sha256`; without it the demo draws the block figure again. Every item the demo character wears is now one his level allows and Forever can draw: his first staff was a mace with no look in the game, and his hood, bracers and second trinket asked for levels above his own.
- With Altoholic installed, the shipper also sends each of your characters' spellbook (known spells and the ones still to come, with their level), reputations, character sheet stats and lifetime honorable kills, raid and dungeon lockouts, and money, XP, rested XP and hearthstone at logout (feeds `wow-altoholic`, and `wow-character` with `source: altoholic`). Your AI gets them as `spells_known` (exact, no more "unknown" ranks), `reps` (characters the Recorder never saw), two new tables, `char_stats` and `lockouts`, and metrics over them (`spell_crit_pct`, `melee_hit_rating`, `honorable_kills_lifetime`, `lockouts_active`, `weekly_reset_day`...). Without Altoholic nothing breaks: stats read unknown, lockouts are estimated from the Recorder's raid boss kills of the last 7 days, and each caveat names Altoholic as what gives more. It is your own data, kept like the rest of your history (no 30-day limit). Never read: Altoholic's friends list, calendar, guild tables, mail and auctions; `/ingest` drops them from an older shipper too. With the spellbook, `class_spells` is exact (a spell still to come is never "learned") and `reputation` has a `rep_source` dimension; findings over those two models revalidate, every other kept finding stays valid.
- Text other people wrote leaves your AI's tools without invisible characters: the Unicode Tags block (which can spell hidden ASCII instructions), bidi overrides and isolates, zero-width spaces, joiners and BOMs. Emoji sequences keep their joins (a family emoji stays one emoji). It runs once, where every tool's answer leaves the server, so player names, KB statements, news and guides are all covered; the KB refresh strips the same characters from fetched pages before its model reads them, and both log how many they removed. `players` and `people` now say other players chose those names (`untrusted`), like the KB tools already did. New `MCP_DCR` (`open` by default, `loopback`, `off`) narrows who may register a client on a hosted server.
- Players are asked to refresh their own analysis before the server pays for it. From 5 hours of play before the server's own refresh (`ANALYSIS_NUDGE_LEAD`, so from 10 h with 15), the full view shows a card above the Ask links: "Your analysis is 10h 12m of play old. In about 4h 48m of play the server refreshes it on Sam's credit. Be nice to Sam: ask your own assistant to run it.", with the prompt to copy; dismissed, it stays hidden for that browser session. Your AI's tool answers carry the same as a `notice` once in a while: a client's first call, then at most every 20 calls or every hour, never on the analysis' own tools. The name is `OPERATOR_NAME` ("the host" when unset), and the host reads "your own credit" on their own page. No nudge while the server's refresh is off, and never on the public page or `/zogzog/`.
- Your AI can ask more of the data the server already gets, with no shipper or addon change. XP by source (`explore_xp` joins `kill_xp` and `quest_xp`) and each one per online hour by level and zone, `xp_per_active_hour`, `dungeon_vs_quest_xp_ratio` (a level's dungeon trips against its questing), `runs_per_online_hour` (a dungeon farm's pace), `rides`, `distance_yd` and `gold_earned_copper` over the whole history, `slots_at_bis` out of `slots_scored`, and a `sheets` model for the gear over time (`gear_changes`, `first_worn_t`). Two new views: `per_death` (one row per death: where, at what level, the likely killer) and `per_week`; `per_level`, `per_session`, `per_zone` and `per_dungeon` get the new columns. Kills whose record doesn't say what they paid (an older Recorder) now make `kill_xp` unknown instead of 0, and `kills_without_xp` counts them. `sanity_check` no longer says kill XP can't be split from quest XP. Kept findings show as "to revalidate" until the next analysis, since the metric file changed.
- Your AI's `play_sql` can read more of what the Recorder already sends, with no shipper update: reputation changes (`reps`), what each quest giver offered (`offers`), quest reward choices and the one you picked (`quest_rewards`), spells learned (`spells_known`), flight paths known (`flight_nodes`), the Onyxia attunement chain with the steps done (`attunement_steps`), achievements earned (`achievements_seen`), vendor prices of what you looted (`item_values`, never auction prices) and the quest texts you read (`quest_text`, for searching only: findings never quote it). The timeline gets your group size, raid, swimming, indoors and ghost time, and a looted item the quest it counted for. Findings judged before show as "to revalidate" (`PREP_VERSION` 11).
- Your AI can ask `query_metric` about those new tables too, with each number's rules attached. Solo against grouped time (`solo_s`, `grouped_s`, `group_share`), how you travel (`mounted_move_s`, `unmounted_move_s`, `swim_s`, `ghost_s`), each death to its revive (`corpse_runs`, `spirit_res`, `corpse_run_s`, `avg_corpse_run_s`), zone time by level (`zone_level_s`), your /played at each ding when the level record carries it, quests a giver offered that you never took (`quests_skipped`) or that it held back, reward picks, loot a quest wanted, loot at vendor price per online hour (`loot_value_per_online_hour`: vendor prices only, your own auction scans stay out), flight paths known, reputation pace and the online time to the next tier (`rep_to_next_s`, an estimate on the KB's low-confidence tier widths), the Onyxia attunement's next step, achievements earned, class trainer ranks you can learn now and their cost (`spells_trainable_now`: exact with Altoholic's spellbook; without it, ranks from before the Recorder ran read unknown, not missing), and the gold still needed for riding (`riding_gold_gap_copper`). Three new views for `play_sql`: `per_faction`, `per_flight_node`, `per_quest_giver`. `sanity_check` and `next_moves` no longer list flights not taken and class trainer visits as impossible. Findings that don't read these metrics keep their check.

### Changed

- A kept finding goes "to revalidate" only when a semantic definition it reads changes: its metrics, the dimensions it groups or filters by, the views and model SQL under them. Before, any edit to `semantic.yaml` (one metric added) sent every kept finding of every person to revalidate. Each finding now keeps `semantic_reads`, the names it reads and a hash over only those; when they can't be told, the whole file still decides. Findings kept before this go to revalidate once if `semantic.yaml` changed since they were judged (it did since 0.7.0-rc.8, and so did `PREP_VERSION`), and follow the new rule from their next analysis.
- Kept findings that typed a number their own result holds now show it as that value: "You died 5 times." next to a query that counted 5 becomes `You died {n} times.`, reads the same today and moves with the next recheck. A number that matches no value, or two columns at once, stays "to revalidate". Dates, clock times, guide keys (`forever/alliance/30-40/01-hillsbrad`), file names and item or quest ids no longer count as numbers in any finding, new or kept; `` `3` ``, `#3` and `3/5` still do. On the 113 synthetic eval states, 627 of 2,202 kept findings are flagged instead of 772.
- A finding kept before the prose rule no longer goes to "to revalidate" for a level, top-N or threshold its own SQL writes: "At level 32 ..." next to `WHERE level = 32` stands, checked exactly like a new finding's declared constant (a number equal to the count it sits next to still doesn't). On the 113 synthetic eval states, 772 of 2,202 kept findings are flagged instead of 883; the rest type numbers their query never wrote. A level written only inside a string or a comment of the SQL no longer counts for new findings either, and `IN (30,31)` reads as 30 and 31.
- Taking a friend off the friends list stops their access at once (login, AI, uploads, install codes, and their public page and `/zogzog/` link within a minute) and deletes their data 7 days later instead of on the spot. Listed again within those 7 days, they're back with everything. `FRIENDS_PURGE_DAYS` sets the wait (`0`: at once), `wow-stats users delete <slug> --yes` skips it for a friend who asks, and `users list`, the admin overview and the start log show who waits and until when. A list that would remove every friend at once (`{}`, a wrongly empty render) is now refused and logged as `empty_list_refused`; `wow-stats users reconcile --allow-empty` or `FRIENDS_ALLOW_EMPTY=1` applies it. The privacy page says all of this with the install's own number.
- The page's numbers have one computation path, the semantic layer, and kills and quests say what they count. Before, when the layer failed, the page quietly counted its numbers again with its own formulas; now DuckDB and the layer are core dependencies (the shipper never installs the package, and the renderer image leaves them out), a failure keeps the last good answer marked out of date, logs one `"event": "semantic_failed"` line, and the full view's `/healthz` says `"semantic": "failing"` to alert on. The demo's page said 1,188 kills and 94 quests where `character_summary` said 1,262 and 99: both right, one without the level in play. The page now says "over the completed levels (levels 22-31)" and adds "Level 32 so far: 74 kills and 5 quests"; `character_summary` drops `kills` and `quests_turned_in` for `counts` with `completed_levels`, `current_level` and `including_current_level`, from the same metrics (`climb_kills`, `climb_quests` by `in_play`). A level without a Polestar split now counts the Recorder's records on both sides. Findings judged before show as "to revalidate" (`PREP_VERSION` 10).
- The knowledge base's jobs split into what needs a model and what doesn't. The daily watch now only checks upstream versions (the client build, Polestar, Questie) and lands them like Dependabot, with no model call. The model runs once a week (mode `review`, `meta` still works) and only on what a free check saw change: an official page whose text hash moved, a forum thread's new posts (not the whole thread), new news items, new community threads, and stale statements whose page changed since they last held. A quiet week makes no model call. The known statements go first in each page call, sorted and cached for an hour when three pages or more are read, and every call's `cache_creation_input_tokens` and `cache_read_input_tokens` land in the job summary. Before, nine daily runs re-sent the same ~50K tokens uncached on every page: about 1.4M tokens and 7 euros in one week.
- Route times come from Polestar 0.4.2.2, and every route to 60 is 16 to 25 hours shorter than in 0.6.1 (21 on the median run). The cut is Polestar's own: its 50-60 rebuild redid both factions' routes and swapped the 50-60 grind steps for quests, Mount Hyjal included; its own notes put the Horde at 106 hours instead of 121 and the Alliance at 115 instead of 130. A Gnome Priest's route goes from 109 h to 85 h, and levels 50 to 60 alone from 45 h to 23 h; levels 1 to 50 move by about an hour. So "ahead of the route" and "behind the route" read differently from 50 on. Findings judged on the old route show as "to revalidate" until the next analysis.
- The knowledge base's watch no longer refuses a version bump because main moved under it. The bump is deterministic, so when another PR changed the same files during the run (the test data, say), the job makes it again on the current main, gates it and lands that, up to twice; before, Polestar 0.4.2.2 sat refused on a fixture conflict. The versions lane is one script now, `tools/kb/versions.sh`, run by the watch and again on a moved main.
- The knowledge base's watch now takes a Polestar release whose route times, levels or gear numbers moved: Polestar is the source of truth. It still stops on a changed licence term, a renamed saved table, or an export or test-data generator that fails. It had refused the release with that 50-60 rebuild for its shorter routes.
- Your AI's `levels`, `zones`, `sessions` and the pace and counts in `character_summary` now read the same metrics as `query_metric` and the views, and so do the plan's inputs (each level's time and XP, the recent sessions, where the character stands now) and the analysis' time budget. Same answers on the demo data, with three exceptions: XP read without a zone of its own lands in the zone you stood in instead of "Unknown"; a zone's XP an hour divides by its exact seconds (one or two XP off before); zones with the same time are listed by name. New metrics: `zone_s`, `zone_visits`, `xp_per_zone_hour`, `ride_s`, `class_planned_s`, `guide_steps`, `flights`, the newest XP bar reading and snapshot (`bar_*`, `snapshot_last_xp_max`, `snapshot_last_zone`, `money_copper`, `bind`, `resting`), `level_now`, `level_max` and `session_records`. Without the Recorder, `levels` now sets each level beside the route for the class the character sheet names, where it used the median over every class.
- Your AI's SQL now starts from five ready-made tables instead of raw ones: `per_level` (played, wall clock, the longest silence, how much of the level the Recorder saw, deaths and deaths an hour, the route plan against your play, quests, gold), `per_session`, `per_zone`, `per_dungeon` and `per_slot` (what you wear in each slot, how it fits your spec, the best upgrade you can wear now and the "17% → 100%" numbers). Nobody wrote their SQL: each is the named metrics compiled at that grain, so a view says exactly what `query_metric` says. `list_metrics` lists them, `play_sql` and the analysis' schema put them first, the raw tables stay as the fallback.
- Deaths and dungeons read through the semantic layer, like the pages' numbers: the `deaths` and `dungeons` tools, the Overview's "on track" verdict and its death clusters, `sanity_check`, `next_moves` and the Dungeons page's trips (`dungeon_options`) count with `query_metric`'s metrics, no SQL of their own. Same answers on the demo and every test fixture, with one change: `deaths` now gives `by_level` for every death the layer can place at a level, not only for death records that carried one (the demo's carry none, so it was empty). New metrics for your AI: `deaths_above`, `level_gap_max` and `come_back_level` (deaths to creatures 3+ levels above you, and the level to come back at), each trip's phases (`trip_pickup_s`, `trip_travel_to_s`, `trip_travel_back_s`, `trip_handin_s`) with its XP, quests and quest money, a `run` on every record (the kills of one run) and a `per_trip` table for `play_sql`.
- Every answer now says what it stood on. The page, your AI's tools on one character, `query_metric` and `play_sql` carry one `coverage`: per source (Recorder, Polestar, gear and bags, quests, prices, Battle.net) complete, partial, unavailable or unknown, when it was last read, and for a missing one the one thing that brings it. The Recorder's gaps are exact: a finished level whose Polestar time (the game's own played timer) is longer than what the Recorder saw. The pages show it only where it changes a number: "The Recorder missed about 1h 10m of play (level 28): deaths from then aren't counted." under Deaths and on that level's last session in the journal, and "Equipment last read 5 h ago" when Battle.net's gear is older than one read.
- Gold, loot, rested XP, riding, gear, other players, prices, talent changes and dungeon groups now read through the semantic layer like everything else, with no hand-written SQL or tallies left in those tools: new metrics on the prep (`gold_per_online_hour`, `rested_per_kill_xp`, `looted_items`, `skill_rank`...) and four small store models (`players`, `prices`, `talent_changes`, `run_groups`) that only read what the store already holds for one answer and keep nothing. Two numbers move because the old code counted rows the metric excludes. The rested burn skips stretches with a dungeon kill in them, so the demo's 0.55 becomes 0.5, the rule it was built on. The gold rate only counts purse changes the Recorder has play time for, so a character whose Recorder started late gets an honest rate (about 4,200 copper an hour on the demo instead of 12,100) and mount ETAs move with it. Upgrade candidates now carry the inventory slot and paired weapon their gain was measured against, so the same trinket in slot 13 and 14 stays two rows; findings judged before show as "to revalidate" (`PREP_VERSION` 8).
- The `/zogzog/` link shows the same coverage lines as your own page (the Recorder's gap under Deaths and in the journal, the equipment's last read). The public page says "Equipment may be out of date." where your page says how old the read is, and never when or how long; its data holds that one state, with no time.
- Guide steps, the plan's step history and open quests, its kill XP share, last trainer visit and profession pace, and the brief's long breaks now read the semantic layer, like `query_metric`. Two more views: `per_guide` (time on each Polestar guide, its XP, steps done, skipped and jumped, Polestar's own step timer) and `per_quest`. Same numbers as before, except: `guide_steps` sums a guide's step times before rounding (a few seconds more, so 234% can read 235%) and names the zone of a step the record left blank; a quest handed in the same second its objectives completed no longer shows as still in your log (the demo's Willix the Importer); a quest the game failed is `failed`, and a quest you picked up before tracking started is in your open quests once its objectives are done. Findings judged before show as "to revalidate" (`PREP_VERSION` 9).
- The last hand-written SQL left the tools and the plan. The plan's review series (your level and online time at each XP reading, the sheets without the Recorder), where you stand in the route, your professions, the quests known done and what you already own now read the semantic layer, with the same answers on the demo and every fixture. New for your AI: a `progress` model you can ask by day, week or month (`progress_level`, `progress_online_s`), `professions`, `done_quests`, `position` (`route_next_step` and friends) and `first_looted_t`. A test now fails on any new SQL in the package outside the compiler, the prep build, `play_sql`'s executor and a short list of plumbing, each with its reason.

### Fixed

- Parts of the page still moved after it looked ready, on a busy computer: a section link could stop where you clicked it, a screen short of its section (Safari's engine stops a smooth scroll when content lands above it), and the Overview could draw twice and show its news empty in between. The page now marks itself busy until every answer is drawn and its fonts are in, a section link lands once the page is done, and only the last Overview asked for draws. The help page waits for its fonts the same way.
- A tab clicked while the page was still loading was lost: the tabs only listened once the page's config had come back, so the page stayed on Overview. They listen from the start now.
- A section's "Missing:" line was gone from the page until the next frame each time the section redrew (the gear advice landing in its panel, the minute's refresh), and anything that read the page in between found the section without it. The line is now put back before anything else runs.
- On `/zogzog/`, a section link clicked before the gear advice came in ended a screen or more above its section: the advice lands above every section after the page shows and pushed it down, and WebKit stopped its smooth scroll where it was. The page now follows the section until everything is drawn or you scroll yourself, as a browser does for a link it follows itself. The same goes for the Overview's and the timeline's links to a section, on every page.
- A browser that left before its answer was written (a closed tab) showed in the server's log as a bug: "error on /api/...", then a 500 written to the same closed connection and its traceback. Nothing went wrong on the server's side; it now just drops that connection.
- Pages, your AI's tools and the nightly recheck spend about 40% less CPU. The semantic layer compiled every question again (about 50 ms each, and a page asks dozens), and each analysis query process compiled the views per grain again at start (a second each). A question's SQL is now compiled once per shape and column types, the values bound as before, and the query process takes its parent's views. Same SQL, same answers. The CI test job, which had started hitting its 20-minute limit, takes about a third less.
- The `ui` process no longer grows with the number of characters and shippers. Each character's analysis workspace (the prep your AI's SQL reads, with its DuckDB query process) stayed open until a restart: about 20 characters held 4-5 GB. Now one closes after 15 minutes unused (`ANALYSIS_WORKSPACE_IDLE_S`) and at most 6 stay open (`ANALYSIS_WORKSPACES_MAX`), the least recently used going first, never one a call is using; the next call rebuilds it with the same answers, a few seconds slower. Uploads are read whole, so at most 4 run at once (`INGEST_CONCURRENCY`); one more waits up to 10 seconds (`INGEST_WAIT_S`), then gets a 503, and the shipper keeps that file in its spool for its next run, 5 minutes later. `/healthz` counts both: `analysis_workspaces`, `analysis_workspaces_held`, `uploads_running`, `uploads_waiting`.
- A finding's text can no longer say a number its query didn't give. With 5 deaths on record, "You died 3 times." next to `SELECT COUNT(*) AS n FROM records WHERE kind='death'` was kept and shown beside the right row, because numbers up to 100 were allowed in the text (an outside review). Now everything the query measures is a placeholder the server fills from the result (`{n}`, `{played_s:dur}`, `{rows[1].zone}` for a later row), when the finding is kept and again every night; any other number goes in the finding's `constants` with its kind (a level or top-N written in the query, a game rule with a cited KB document, a patch...). Findings kept before keep their text; one that types a number nothing binds shows "to revalidate" until the next analysis. Each finding now says when it was last checked, and the page and `play_analysis` say how the nightly recheck works. That recheck also ran for the owner only: your friends' findings were never checked again. It now runs for everyone on the server, one person at a time, with no model.
- The knowledge base's daily watch could record an addon release or a client build it never checked. After the bump checked Questie or exported the class reference, the accept step asked GitHub or wago.tools again and recorded whatever was newest then, so a release out in between skipped the contract check; an addon whose release couldn't be read was passed over without a word and then accepted on a later read. The bump and the export now hand over what they checked, and the accept records exactly that: a release out meanwhile waits for the next run, an unreadable one stays pending with the reason on its issue. Polestar already worked this way (`--release`), and the accept now never reads upstream for any kind.
- The knowledge base's weekly review could mark a forum post read that it never got. A thread known at 20 posts got a 21st; the topic loaded, post 21 answered 503, the pass found no new text, called the thread read, and the accept step fetched the thread again and recorded 21 posts. Post 21 was never read and the next watch saw nothing to do. A failed fetch now leaves the page pending (a deleted post, 404, still counts as nothing to read), and the accept records the version the model read, handed over from the pass, never a fresh fetch, so a post that lands while the model reads waits for the next run. The same holds for the news pass (a blue post that failed to load holds the feed there, and a batch the model stopped on isn't read), the guides and re-verified pages (a failed fetch moves no hash), and the community corner (Reddit searches the model never ran aren't recorded as run). Found by an outside review.
- Your AI couldn't save a finding on the ready-made tables it's told to read first. `SELECT COUNT(*) AS n FROM per_level` returned its rows and the judge still answered "the query reads none of the character's tables", on all eight `per_*` tables: it knew the raw tables and the `sem_*` views by name, not these. And a `per_slot` finding judged under Holy stayed "checked" under Shadow while the scoring alone moved its upgrade gain, because the definition check had its own list of gear tables. One resolver (`semantic.reads`) now says which tables any table or view stands on, and the coverage, the judge and the definition check all ask it, so a new view is covered the day it's declared.
- Your AI's `gear_options` now says the zone the character is in now (the same as `character_summary` and `gear`) and marks each option there with `in_current_zone`, like `gear` already did. Without it, asked for the quest upgrade in the current zone, Claude Haiku read the zone off `sessions`, found three in Arathi Highlands where `gear` lists one, and picked the wrong one about one run in ten. `make eval-mcp`'s question now asks for the biggest of the three and its expected answer is computed over both tools.
- Your AI's SQL now has the next guide step: `route_position` (one row) holds the step to do next by the rule the plan and `next_step` use, and `play_sql` points to it. Polestar falls back to an earlier step at login, so the newest step record can be behind: Claude Haiku read it and answered step 45 where `next_step` says 66, about one run in ten of `make eval-mcp`. The rule moved from the plan into the analysis database, so both read one row.
- A player who has just started no longer breaks your AI's tools. With one XP record and no `xp_gained` the store answers its sums as "NaN", and `zones` failed on it. Every typed tool now has a test on a nearly empty store (one session, one XP record, no level-up, no death) and must answer without an error or a NaN; the start page's event counts and the setup report's saved-character count read "NaN" and "Infinity" as no number too.
- A character the Recorder never saw (Polestar or Syndicator only) got the median route time over every class for each level, not its own class's: the analysis knew the class from the character sheet but the level table only looked at Recorder sessions. It reads the sheet too now. Findings judged before show as "to revalidate" (`PREP_VERSION` 7).
- What to equip next no longer needs SQL. The analysis brief named a worn item that fits your spec badly (a Shadow circlet on a Holy priest) but not what to wear instead: the best head pieces for Holy sat in a table only `play_sql` reached. Each item the brief lists now carries its slot's best three upgrades you can wear now, said as the page says them ("17% → 100% on your head"), where to get each and the Wowhead pages. Your AI can ask for any slot through `query_metric` (new `upgrades` and `worn` models). The `gear` tool also scored the gear for your add-on's spec while the page and every other tool used the spec you set (`WEIGHTS`); it uses yours now, and says how each worn item fits it. Findings judged before show as "to revalidate" (`PREP_VERSION` 6).
- The knowledge base's daily watch never took the Polestar release with the 50-60 rebuild. Polestar's data notices list each source's revision and how many names it took, both move with every data build, and the watch read that as a licence change. It now skips those lines and still refuses any changed term. The watch also drops a model-written record that would fail the KB checks before it reaches the PR (listed under "Dropped"), runs the test suite the way CI does, and stays with its PR until it merges, rebasing it when main moves; a PR that doesn't merge ends the run red.
- The analysis sometimes found something and never said it: a lane answered "Wetlands has eight deaths" in its note and proposed no finding, and a note never reaches what you see. An answered question now has to rest on a finding (an observation when there is nothing to act on): the lane goes back to your AI once to propose it. The server's own runner also stopped a run after a model asked "continue?" three times while it was still working; any tool call now restarts the count, with 8 nudges a run at most. And the synthesis could drop a verified finding just by not listing it (Claude Sonnet left out "Almost half the deaths are in Wetlands" without a word): a finding now leaves only when the synthesis says why.
- A light check of the MCP tools (contributors only): `make eval-mcp` asks Claude Haiku ten questions about the demo character with one exact answer each, computed from the demo data, through the MCP tools alone, and grades the answers without a model. About a minute on a Claude Code login; it flags any answer found only through `play_sql`.
- The analysis evaluation (contributors only): `--client kind:model` runs several models side by side (`claude:claude-haiku-5-5`, `server:gpt-6-luna`...), each server model with its own cap, now $12 by default, and priced as itself (a model named on the command line got gpt-6.1-sol's prices). A scenario most of them miss is flagged. The evaluation is an optional tool, not a release gate.
- The analysis evaluation (contributors only): every run of a batch goes at once, each with its own server, client and folder; each scenario gets 20 minutes, not 10, and reports its minutes and tool calls per tool even when cut; `--repeats` runs each scenario N times and a scenario passes at two runs of three; `--client server` runs the server's own analysis runner under a per-batch spend cap (`--max-usd`). The `recorder_off` check now reads what a finding means (the Recorder missed that stretch) instead of one wording. Two more grader false negatives found by the first release eval are fixed: a finding made from a metric is re-run as the server rechecks it, and a finding that faults the off-spec circlet no longer counts as praising it.
- Shipper tests pass on a Mac with the Polestar client installed: they read that machine's real client data (a leak between tests) and one asserted on `os.fsync` where macOS uses `F_FULLFSYNC`. A test now runs the real `F_FULLFSYNC` on a file and a directory on macOS; the shipper code needed no change.
- On macOS the shipper's flushes now use `F_FULLFSYNC`, which empties the drive's write cache; plain fsync there doesn't survive a power cut. A filesystem that refuses it gets plain fsync.
- A published page with no 3D model for a character (the publisher ran without the 3D) showed "No 3D model yet: 404 Not Found." on the Equipment panel, over a "View in 3D" button that could only fail. It now shows the picture alone, no button. Any other failure to load the model reads "The 3D model didn’t load: reload the page to try again.", with no status code.
- A source that sent nothing read as 0. `deaths` and `character_summary` said 0 deaths for a character without the Recorder, the journal's estimated logouts carried 0 deaths, and Back in time said "Deaths 0" at a level the Recorder never saw. They now say unknown: `null` for your AI, "not recorded" on the page.
- The shipper gave a Recorder record its zone, a guide step its time and a session its login from what that one run had read: a run that started mid-session gave its first records no zone and called them a login. It now carries the zone and step over from the chunk before, so the events are the same however the runs split the chunks.
- `play_sql` and `query_metric` said `more: false` on an answer cut at its size limit. The MCP's `sessions` cut its list without saying so and still described a 20-minute session rule (it is 330 seconds).
- When the data store failed, `route_reference` answered for every side without saying the character's couldn't be read, `talent_changes` asked a player who has WoWSims Exporter to install it, and `dashboard_refresh` showed no timer. Each now says what it couldn't read.
- A flight that took off in one shipper run and landed in the next had no time in the analysis: the shipper only timed a flight whose landing it read in the same run. The server now times every flight from its stored landing (the game's own seconds in the air when the Recorder has them), for the page and the analysis alike. Findings judged before show as "to revalidate" (`PREP_VERSION` 3).
- When Battle.net's store (vm-wow) didn't answer, the page and your AI said the character hadn't been read yet: "No talents known yet", Legacy points unknown because "Battle.net hasn't read this account's characters", coverage `unknown`. They now say the store didn't answer, and coverage says `unavailable` as of the last read the server saw.
- `/ingest` stored the part of an upload that arrived when the shipper was killed or the connection dropped mid-upload, if it ended on a whole line, and answered 200 to nobody. It now refuses an upload shorter than its `Content-Length` and stores nothing of it; the shipper sends the whole file again.
- The shipper flushes its spool and its `state.json` to the disk before renaming them in place, the spool first, and keeps the previous state as `state.json.prev`. A power cut used to be able to leave a short `state.json`; the shipper then started over from nothing and sent snapshots it had already sent (the roster, Questie) again with a later time, so the server dated them days late. It now goes on from the previous state.
- Snapshots (gear sheets, bags, quest sets, character sheets) are picked by one rule everywhere, the page, your AI's tools and the analysis: a snapshot with an exact time before one dated only by an archive commit or the shipper's clock, then the newest, then the same tie-break whatever order they arrived in. Before, an archive copy with an approximate later time could replace the sheet you really wore, and two sheets on the same second gave a different answer depending on which arrived first.
- The same bags or quest set seen again later (the game rewrites the file at every logout, a run after a lost `state.json`) erased when it was first seen: a quest reward in your bags since Tuesday could read as first seen on Friday. Each observation is now kept.
- A partial snapshot no longer erases what an earlier one saw. The shipper leaves the bank out when Syndicator never saw it (it sent an empty bank), and the equipped items out when Syndicator doesn't know the character (it sent none); the readers then take them from the last snapshot that had them, and the money from the last character sheet that had it. A bank never seen reads unknown, not empty. A snapshot the shipper had to date with its own clock says so (`at_basis: "run"`).
- After an exporter restart, a first Battle.net read that failed half way (a 503 on the equipment) left the character with no Battle.net data at all until the next full read, 2 hours later: no gear, no talents, no look. The pages now keep the last complete read (still inside vm-wow's 2-day window) and say how old it is. Nothing new is stored.
- Back in time counted a death on the second you dinged in both levels. It counts in the new level only, as the deaths by level already did.
- Your AI's `sessions` left out a dungeon you entered, or a player you grouped with, on the session's very last record. The page had them.
- Without the Recorder, the page and the `/zogzog/` bundle said 0 deaths. They now say unknown (`null`), as your AI's `deaths` already did; the page still reads "No deaths recorded" with the Recorder's switch.
- One character whose store read failed stopped the nightly recheck for every character after it, and the night counted as done. The others are rechecked now; the failed one keeps its last answer, and the night ends in an error.
- A finding judged under another `WOW_TIMEZONE` (or session gap) is marked "to revalidate", like one judged under another semantic model: its hours and days meant other hours.
- Changing `WEIGHTS` (Holy to Shadow) made the nightly recheck count the same play as new play: findings got more weight and a "seen" more without a single new event. A finding on your upgrade candidates is now "to revalidate" when the spec your gear is scored for changes, the others stay as they were, and only new events from the game add weight. Proposing a finding again after a prep change no longer adds weight either. Some findings on upgrades show as "to revalidate" once after the upgrade.
- After ten or more partial snapshots in a row (a week of runs with Syndicator's bank cache lost, say), the page and your AI lost the equipment, the bank and the money, while the analysis still had them: they only looked at the newest 10 snapshots. They now find, for each part, the newest snapshot that has it, however old, and say when that was: the hero line's money shows the time of the sheet it came from, and `coverage` lists each part's time under `gear` and `polestar`.
- With an archive for older days (`VL_ARCHIVE_URL`), an archive that didn't answer gave a shorter history without saying so: the page and the analysis counted only the recent days (1 death of 2) and still called the Recorder complete, and the server dropped the older days it had already read, for up to 10 minutes after the archive came back. A read that needs the older days now fails with an error that says the archive didn't answer, nothing already read is dropped, and everything is back as soon as the archive answers again.
- A nudge resting on a finding "to revalidate" stayed in "What to do" on the page, in your AI's answers, in session plans and on the shared page. It now waits with its finding ("1 step is on hold until the next analysis judges its finding again.") and comes back once the finding is judged again. The same goes for a finding marked doubtful or dropped.
- Deaths "Per level" divided every death the Recorder saw by every level Polestar knows, so a Recorder switched on late made it far too low (0.25 for a character dying once a level). It now counts only the levels the Recorder saw whole, on both sides, and says how many: "1.0 over 1 level recorded whole".
- `--replay` stopped at "no WTF folder" and sent nothing on a computer without the game (not installed yet, or moved), although it only reads `sent/`. It now runs without the WoW folder.
- Your AI's `players` gave a different top 20 on each call over the same events when players tied (met as often, on as many days): the store's rows come in no set order. Ties now go to the one seen last, then by name.
- The play analysis read a three-week break as a slow level, and a Shadow head piece on a Holy priest as fine: both facts sat in columns your AI had to think of joining. The analysis tables now give each level its calendar time, offline time and longest silence beside its played time (new metrics `offline_s` and `offline_share`), and each worn item its score, share of the slot's best and fit for the spec the gear is scored for. `analysis_brief` lists the levels with a silence of three days or more and the worn items built for another spec or under 25% of their slot's best, and its finding rules say to weigh both before judging pace or gear. Findings judged before show as "to revalidate" (`PREP_VERSION` 5).
- Your AI's `zones` failed ("cannot convert float NaN to integer") when the XP records of a zone carried no XP amount: the store answers a sum over nothing as `NaN`. Every number read from the store now takes `NaN` as no number.
- `--replay` sent nothing from a `sent/` file whose last part a crash had cut short, not even the whole parts before it, and still exited 0. It now sends every part that reads whole, names the damaged file and how many bytes it couldn't read, and exits 1.
- After a power cut, the shipper could lose events: it flushed the spool and `state.json` before renaming them, but not the renames themselves, and never flushed the copy in `sent/`, the spool file shortened after each upload, or a downloaded update. Each now reaches the disk before the next step counts on it (on Windows, the renames rely on NTFS: Python can't flush a folder there).
- `--reextract` ended with a normal run that moved `state.json` on, although the restore keeps a record of its own. The next background run then skipped play that only the restore had sent. It now leaves `state.json` exactly as it was; the background run after it carries on from there.
- The server's outbound URLs (wago.tools' tables, Wowhead's icons) now take only values of a strict shape: an icon name from a request, a build from wago.tools' answer, a table from a fixed list, each checked whole and quoted before it goes in. `fetch` reaches only the fixed hosts, plus `MODEL3D_WAGO_URL` and `ICON_URL` when you set them. An icon name ending in a newline used to pass the old check (CodeQL #42).

## 0.7.0-rc.8 (2026-11-21)

What 0.7.0 brings over 0.6.1, in short. Every change is below, one
release candidate at a time.

**For players**

- Gear says how far each slot is from its best in slot (BiS): a badge on
  every item icon, orange for BiS, purple from 85%, blue from 50%, and
  "70% → 91% on your chest" on every upgrade, dungeon drop and quest
  reward. The upgrades come slot furthest from BiS first.
- The paperdoll's picture is a still of your own 3D model, drawn by the
  server's renderer. Blizzard's armory renders are gone: they could show
  another player's character.
- "View in 3D" draws your character from your own game files: a weapon in
  each hand, pauldrons on the right shoulder, eyes with their colour.
- A quest you finished counts as done everywhere, even with the Recorder
  off: the whole gear and bags history, every Questie or Altoholic save,
  the rewards you hold, and what Battle.net says you wear.
- New dungeon loot (The Drowned City's) gets its stats from your own
  game's cache.
- Talent tooltips show the game's numbers: the one for your rank, and
  the ones another spell holds (Spirit Tap's Spirit and its 15 sec).
- Battle.net fills what your addons don't send, every 2 hours.
- Other players' names, realms and game ids stay on your PC unless a page
  uses them.

**For your AI**

- `dashboard_refresh` refreshes "How you play" with your own AI, for the
  character whose analysis is the oldest. It never starts a run on the
  server and costs the host nothing.
- The connector's login asks which account to use and, when the host set
  it up, can send you straight to Battle.net.
- Named metrics (`list_metrics`, `query_metric`) and `play_sql` on DuckDB.
  A finding's query must read your play history: a constant is refused.

**For hosts**

- The server can run the play analysis itself, for friends who never ask
  their own AI: Anthropic, OpenAI or any OpenAI-compatible endpoint,
  through workload identity federation or a key file, with a monthly cap.
  It runs for a character once it has 10 hours of play since its last
  refresh (`ANALYSIS_STALE_PLAYED_HOURS`), and on nothing else.
- `WOW_OWNER_SUB` takes a list of accounts. A friend listed without a name
  gets one from their BattleTag.
- One-button releases, signed shipper updates, the image on Docker's
  hardened Python. After 0.7.0, a knowledge base refresh goes out as a
  patch release by itself.

### Upgrading from 0.6.x

- 0.6 shippers update themselves within 6 hours of the server moving to
  0.7.0. Nothing to reinstall.
- Pictures need the renderer now: `RENDER_URL` on the publisher and
  `RENDER_FROM` on the full view (docs/reference/environment.md). Without
  them the paperdoll shows the silhouette and the 3D toggle.
- `PROFILE_INTERVAL` defaults to 7200 (it was 86400) and picks clock slots.
- `ui-public` with several people takes `USERS_URL` and
  `ANALYSIS_SHARE_TOKEN_FILE` to follow Settings.
- Lines stored before 0.7.0 keep what 0.6 sent about other players:
  docs/contract.md, "Stored before 0.7.0", lists the filters that find
  them.
- Building from source needs `docker login dhi.io` once (a free Docker Hub
  account). The released images pull as before.
- Findings kept before this change have no manifest. The first nightly
  recheck runs each one once on the new build: one that still holds is
  carried over (it gets the manifest and a note, "Checked against 0.7.0
  data, first judged under 0.6."), one that doesn't fades as usual and
  shows "to revalidate" until the next analysis proposes it again.
- The server's own analysis stays off until you configure a provider;
  `ANALYSIS_AUTO=off` keeps it off with one.
- The exporter's profile series carry a new `snapshot` label, plus
  `wow_character_snapshot` and `wow_character_part_refreshed_timestamp_seconds`.
  Your own queries on vm-wow see each series twice for 2 days after the
  upgrade (with and without the label); the pages pick the newest.

### Docs

- Every page checked against 0.7.0's behaviour: the server's own analysis
  and what it sends where, `dashboard_refresh` on your own AI, the BiS
  badges, the picture, owner aliases, BattleTag names, quest completion,
  the connector's account picker, the knowledge base's patch releases.
  Screenshots retaken on the demo character.

### Added

- A seeded evaluation of the play analysis, for contributors: `make eval-analysis` runs the whole analysis with a real MCP client (Claude Code by default, any OpenAI-compatible endpoint by config) on five synthetic histories with a known effect injected (a death spike in one zone, four unrecorded evenings, a three-week break, a wrong-spec item, and a healthy control), then re-runs every kept query and grades the kept lines against the truth. Not in CI: hosted CI has no client. [docs/evaluation.md](docs/evaluation.md).

### Changed

- The nightly recheck runs a finding only under the definitions it was judged with. Each analysis build now keeps a run manifest (the play it saw, `PREP_VERSION`, a hash of the semantic model, the reference data's versions, timezone and gaps, build time, freshness) and each finding the id of its own. When the prep version, the semantic model or the reference data changed since, the finding shows as "to revalidate" on the page and in `play_analysis`, stays out of the checked count, and `analysis_brief` lists it for the next analysis to judge again. Before, the same SQL could pass on data that meant something else.
- A finding kept by 0.6 is carried over once instead of waiting "to revalidate" until the next analysis, which for a character who rarely plays could take weeks: the first recheck runs its SQL on the new build, and if it holds it gets the new manifest and a one-line note on the page and in `play_analysis` ("Checked against 0.7.0 data, first judged under 0.6."). No weight is gained for it. One that fails fades as before.

### Fixed

- An item said its gain three ways: "17% → 82% on your head" on the Overview, "▲ 17% → 82% at hand" on the paperdoll, "HEAD 17% → 100%" in the Upgrades list and "(0% → 100%)" in Back in time. Every place now says "17% → 82% on your head" (Upgrades in both views, its comparison, Back in time, the moves, the Dungeons panel), and the paperdoll says "17% → 82% with the best at hand" under the slot. Your AI gets the same words as `line` on each item of `gear`, `gear_options` and `dungeon_options`, and in the plan's gear lines. An empty ring or trinket slot no longer offers the one the other slot already wears.
- A Battle.net refresh that failed half way (a 503 on the equipment) marked the whole character fresh while its gear was hours old. Each call now keeps its own time and the character reads "as of" the oldest. A talent dropped in a respec or a slot emptied also lingered on the pages for up to 2 days; they now go with the next refresh.
- A minute with more events than one read holds (5,000) was read once, cut at the page, and stayed cut: the cache never went back for the rest. It's now read by the second. More events in one second than a page gives an error that says so ("no answer rather than a partial one"), never a partial history.
- The backup guide's restore didn't restore: `--backfill` reads the archive against `state.json`, which already lists every record sent, so an emptied server got no Recorder record back (21 of 21 missing in the test). Two new shipper flags do the restore. `--replay` sends `sent/` again as it went (same ids, same fields) and writes nothing on the PC. `--reextract` reads the whole WTF archive again with a record of its own, never `state.json`. `--backfill` keeps its meaning: what this PC hasn't sent yet. The archive's `_capture.json` also dates each character folder, so a re-extraction brings back the characters Polestar doesn't track as they were sent.
- A re-extraction from the WTF archive (`--backfill`) dated Questie, DataStore and Syndicator snapshots at the time of the re-extraction, because git keeps no file times: a Questie save from 17 Nov came back as seen the day you ran it. Each archive commit now carries `_capture.json`, the files' times as the live run read them, and a re-extraction puts them back. Commits from before it are dated by the commit and say so (`at_basis: "commit"`); quest evidence from those reads "by <day>" (docs/contract.md, "Times").
- A ring or trinket line could start from the other worn ring's share: the paperdoll read 40% and 34% for the two rings, and every ring upgrade said "40% → …" though it replaces the 34% one. The doll measured the stronger worn ring against the best item left and the weaker against the next, so the weaker could read the higher share. The best item left now goes to the slot it replaces (an empty one, else the weaker worn), the stronger against the next, so the weaker always reads the lower share and every line starts from it. The stronger ring or trinket can read a higher share than before, and only the best ring left reads 100% going into the slot it replaces (the second of the best two reads its share of the best).
- Your AI scored the gear for the spec your add-ons pick even when the host set `WEIGHTS` for that character: `gear_options`, `dungeon_options` and `next_step` could say Shadow while the page said Discipline, and whichever asked first decided what the other got for two minutes. The MCP now reads the same settings as the page, and the planner keeps one answer per spec.
- `query_metric` compared the route plan of levels you never timed: `vs_route` and `over_route_s` added the level in play's plan (no split yet) to the planned side only, so one finished level on plan read 0.5 and 100 s ahead. Both now read whole splits with a planned time on both sides; `planned_s` on its own is still the whole target.
- One logout snapshot gave a money change of 0. `gold_delta_copper` is now empty below two snapshots with money at different times, and a `gold_delta_copper_evidence` column says how many there were (0: none, 1: not enough).
- `query_metric` with a `limit` said `more: false` when there were more rows. The query now asks for one row past the limit.
- Each metric in `list_metrics` says how it adds up (`aggregation`), what its time is (`time_meaning`), where the number comes from (`evidence_basis`), and, when they apply, the rows both sides read (`eligibility`) and the observations it needs (`min_evidence`). The last two are enforced, not just printed.
- Time per zone no longer depends on how the shipper happened to read a session. It set a zone's time from the chunks it had seen so far and never sent the record again, so a zone read mid-session kept 5 s where the whole session gave 306 s. The server now works the time out from the stored records (to the next zone record, else the end of the session) for the zone table, the journal, the level snapshots, the MCP's `zones` and `sessions` and the analysis' `spans`. Nothing to rebuild: the old records read right as they are.
- Two Recorder records at the same second follow each other by chunk and record number, not as text: record 10 sorted before record 2, and the earlier of two zone records took the later one's time in the analysis and the zone tables.
- Findings judged before these two fixes show as "to revalidate" (`PREP_VERSION` 2) until the next analysis judges them again: the analysis' zone spans and the order of same-time records changed under them. Findings kept by 0.6 (no manifest) are carried over once as above, on the new prep.
- After a query ran out of memory, the next one could fail with the same "more memory than a query may use" error on a two-row answer: the query process that hit the cap answered, then crashed on a later query (4 runs in 20 on x86 Linux). A process that ran out of memory now answers that query and is replaced before the next one.

## 0.7.0-rc.7 (2026-11-21)

### Added

- "How you play" says when the analysis ran and who ran it, under its heading: "As of Thu 19 Nov, by the server." or "by you." when your own AI did.

### Changed

- The Dungeons panel's drops and quest rewards say the Overview's item line, "59% → 100% on your wrist" (plus "at level 36" when it can't be worn yet), instead of a bold share, an "empty slot" note and a slot chip. A dungeon's "Gear a clear" says the chance a clear drops one of your upgrades ("17%: chance one of your 3 upgrades drops") instead of "+19.8", a points scale no item line uses any more. The Overview's place groups count upgrades the same way ("2 upgrades you can take now", "31% chance one of your 3 upgrades drops", "34% chance your upgrade drops", "2 upgrades for 4g 10s in all"), and the meta's gear bar says "of what the best gear for your level gives" instead of a count of points.

### Fixed

- The Dungeons panel and the Overview's quest rewards read about a third of the paperdoll's BiS share ("12% → 18% on your chest" next to a doll at 39%). The planner took the character's level from the newest Recorder event with a `level`, and a kill's level is the creature's: a level-33 character who last killed a level-55 guard got BiS from 20 levels up. Only a ding, a session record or Polestar's sheet set the level now. The planner also scores from the paperdoll's own inputs (Battle.net's talents and worn stats, the auction house) instead of its own copy, so each worn slot has one BiS share and every item line on the Overview and the Dungeons panel starts from the badge the doll shows (the MCP's `next_moves`, `next_step`, `gear_options` and `dungeon_options` too).
- A talent whose number grows with its rank showed it as 0: Spirit Tap 5/5 read "Gives you a 0% chance to increase your Spirit". Forever keeps those numbers in a per-rank curve (wago.tools' `TraitDefinitionEffectPoints` and `CurvePoint`), and the spell itself says 0. The talent frame now reads the curve for every talent that has one, in every class and the Legacy trees: Spirit Tap says 20% at 1/5 and 100% at 5/5, and a talent not learned yet shows rank 1's numbers. The cached talent catalog is rebuilt once.
- Talent tooltips no longer show "?" for a number that lives in another spell, a duration, the stacks or a formula. Spirit Tap read "increase your Spirit by ?% for ?"; it now reads "by 100% for 15 sec", and Improved Scorch "by 3% and lasts 30 sec, stacking up to 5 times". The catalog reads the spells a description names (`$15271s1`) from the same wago.tools tables, plus SpellDuration, SpellAuraOptions and SpellRadius, and works out `${$s1/-1000}`-style formulas. A number only the game client knows (your spell power, your level) is left out with the words that carried it: "Shields up to ? damage" reads "Shields damage". "Your next 1 spells" says "spell". The cached catalog is rebuilt once more.
- The nginx kit refused the 3D view's HD character models (17 to 28 MB) with a 413: it capped the whole full-view host at 8 MiB. `/install/assets/` now takes up to 48 MiB, the server's own limit; `/ingest` and the rest keep 8 MiB.
- The compose kit's `exporter` passes `PROFILE_CHARACTERS_URL` from `.env` and reads the share token from `ui-state` (read-only), so friends' characters get Battle.net's copy without a `compose.override.yaml`.

## 0.7.0-rc.6 (2026-11-21)

### Changed

- `dashboard_refresh` runs on the client: the calling AI does the analysis itself, with the same flow as `analyse` (analysis_brief, the passes, analysis_submit), for the character whose analysis is most out of date. It starts nothing on the server and costs the host nothing; it used to queue a run of the server's own model, and the owner's Claude calling it spent the lab's OpenAI money. Its answer is `analyse`'s, plus `do` (the one line to follow), `then` (the other characters to refresh) and `server_automatic_refresh`: the timers, budget and last try of the server's automatic refreshes, which stay for players who don't refresh themselves. No MCP call starts a server run any more (and /admin never had a button for one); the accepted synthesis resets the character's timer like any refresh. The tool is now read-only and no longer open-world.
- Every item the page shows (the Overview's upgrade and dungeon cards, the Dungeons panel's drops, quest rewards and what you already have, the gear advice and its comparison, past levels' best upgrades) carries the paperdoll's BiS badge on its icon, in the same colours, and says what it changes as the slot's share of BiS with what you wear, then with the item: "70% → 91% on your head" ("empty → 91%" for an empty slot) instead of "+32.0 points (11% of your gear)". Rings and trinkets count against the weaker of the two worn, weapons as a whole setup. An item with nothing to measure against (a one-hander scored alone, a random-suffix item worn) has no badge and keeps its points. The MCP's `gear`, `gear_options`, `next_moves` and `dungeon_options` items carry the same `bis_pct`, `bis_pct_worn` (null for an empty slot) and `delta_pct` (the difference); `gear` drops `share_of_bis_pct` for them.

### Fixed

- The analysis' judge accepted a finding whose SQL read nothing: `SELECT 3 AS n` with "You died {n} times." was kept with 3 while the records held 5 deaths, because a constant re-runs to the same value every time. A finding's query (and a clash's reconciling query) must now read the character's play history; a constant, a CTE of constants, `range()` or a read of the documentation tables alone is refused with why, and the refusal points to `query_metric` for counts like deaths. A kept finding with such a query fades on the next recheck. This closes that hole only: a query that reads a table can still compute the wrong thing.
- `query_metric` with metrics from three or more models (say `run_s`, `played_s`, `online_s` by level) no longer splits a level into two rows when the first model has no row for it (dungeon runs cover a few levels only). Each model now joins on the keys of all the models before it, so every order of the metrics gives the same rows.
- An upload the store refused (VictoriaLogs down or answering an error) no longer counts as one: `/ingest` answers 502 as before, and now leaves the person's first-event mark, their last upload in /admin and the day's upload budget as they were. Those three are set only once the store has the lines.
- A gear sheet worn again no longer erases its earlier occurrence: outfit A, then B, then A again showed B, A in the gear history, and a level played in the first A had no sheet. The shipper now names each sheet by its content and the time it was read (`gear:<char>:<hash>:<epoch>`), and the server keeps every occurrence; a resend of the same line still counts once. Sheets from older shippers (`gear:<char>:<hash>`) are told apart by their time, so the history already stored comes back whole without a shipper update.
- The nightly recheck raised a kept finding's weight (and its "seen N×" tag) every night, even when no play had been added: three nights on the same data took a finding from 1.0 to 2.5. Still applicable and better supported are now apart: a finding that holds gets `last_checked`; its weight, `confirmations` and `last_confirmed` move only when the play history changed since its last support (`support_mark`). Proposing the same finding again on the same data adds nothing either. The recheck's log line and run record count `still` next to `confirmed`.
- `query_metric` didn't count in the analysis run's bookkeeping: no query counted, and a table read only through a metric stayed "untouched", so the coverage round asked about it again. `play_sql` and `query_metric` now share one bookkeeping: both count as queries, and a metric's semantic-layer view counts as the prep tables (and columns) under it.
- A server analysis run that didn't finish put back the character's files from its start, erasing anything the player's own AI had written to the same character meanwhile (a finding it proposed, its run's progress). The server run now watches who else writes that character's analysis while it runs, in its process or another one (the nightly recheck runs as a child process, and `wow-stats analyse` can be run by hand): every write takes a lock on the character's folder and moves a revision kept there (`revision`, `.lock`). It rolls back only when nobody else wrote, and otherwise leaves the files alone and ends its reason with "not rolled back".
- The README, the landing page, the FAQ, the architecture and MCP pages said no model runs on the server and your own assistant does all the thinking. Since 0.7.0 a host can also have the server run the play analysis itself, with an AI provider they configure, once a character has `ANALYSIS_STALE_PLAYED_HOURS` (10) of play since its last refresh. Those summaries now say who processes what and when, like the privacy pages; the in-app Privacy page gives the host's hours and says your AI never starts a server run. Kept findings are described as checked against their saved query (which you can read), not as proven. Also: the data sources table had "quests done" from Questie or Altoholic only, with no API (the Recorder, rewards held and quest chains count too, and Battle.net has the list but isn't read), and the security notes still had S6 open (the repository is public with a ruleset on main).

- Gear (the page and the MCP `gear` tool) no longer offers a quest's reward again once the reward that proved the quest done is sold or replaced: it read only the newest sheet and bags, the planner the whole history. Every gear and bags sheet now counts, as one answer per character for Gear, the Overview, `next_moves`, `sanity_check`, the planner and the Dungeons panel. What Battle.net says the character wears now counts too, everywhere (the only proof for a friend without Syndicator): read live, after the history and only adding quests, never kept past the readers' short caches nor put in the analysis' database; over MCP only when the person lets Battle.net data reach their AI.
- The in-app Privacy page said the server runs the play analysis itself whenever an AI provider was configured, even with `ANALYSIS_AUTO=off`, when no server run ever starts. It now says the host configured a provider but turned the server's own analysis off, and that nothing goes to it.

- Gear, the Overview, next steps and the MCP read only the newest 20 quest-addon snapshots, while the planner read them all: a quest only an older Questie or Altoholic save listed (DataStore's saves lose different low bits each time) was done for the planner and not done everywhere else. Every reader now reads every snapshot. Working the list out is also about 7 times faster (1,000 snapshots of 800 quests: 1.7 s before, 0.2 s now), for the planner too.

## 0.7.0-rc.5 (2026-11-21)

### Added

- `WOW_OWNER_SUB` takes a comma-separated list: an owner who signs in with two accounts (the usual one and one linked to Battle.net) is the owner with both, on the web, over MCP and on /admin, with one set of data and one analysis timer. A single sub works as before. A sub in both this list and the friends list is the owner's; the start log names it by its place in the list, and no data is made or deleted for it. A BattleTag header never gives the owner's other login a friend's name or tenant.

### Changed

- A knowledge base refresh merged on main now goes out by itself as a stable patch release (X.Y.Z+1), so Renovate and Dependabot pick up the KB baked into the image. Its notes say "Knowledge base refresh" and what moved (a Polestar tag, the lanes). Nothing goes out while a release candidate series is open: the KB rides the next candidate or the final (`.github/workflows/kb-release.yml`, docs/development.md "Knowledge base releases").
- Gear: the BiS badge on each worn item's icon is coloured by how close it is, like the Forever Leveling BiS addon: orange for BiS, purple from 85%, blue from 50%, the plain dark band below. A legend under the paperdoll says so. The badge keeps its text ("BiS", "84%"), and the item's border still shows its quality.
- The paperdoll's picture is our own still of the character's 3D model (the same race, look and gear as "View in 3D"), drawn by the renderer (`RENDER_URL`) when the publisher builds, in the public site and /zogzog/ (`data/render/<char>.webp`), and in the full view through `RENDER_FROM`. It is kept per person, character and look, so new gear draws it again. No still yet (the renderer down or busy): "Picture on its way. View it in 3D meanwhile."; no renderer: the silhouette and the 3D toggle. The 3D view's caption says "Look as of <date>." instead of "Look from Battle.net".

### Removed

- Blizzard's armory renders, everywhere: the exporter no longer reads `/character-media` (no `wow_character_render` series), the publisher no longer downloads from `render.worldofwarcraft.com`, and the old cache of those downloads (`MODEL3D_CACHE_DIR/render`) is deleted at start. Blizzard serves its renders on paths shared by character id across the Classic games: the owner's Gnome showed a stranger's Night Elf, byte for byte what Blizzard served for that character. `/render/<char>.webp` serves only a WebP; `RENDER_FROM` empty now means no picture rather than Blizzard's.

### Fixed

- The server's first analysis ran for every character with no refresh on record, including an alt logged into once to reserve its name ($0.32 for nothing in the lab). The timer is now each character's own: its play since its last refresh, or all its play on record when it has none, so that alt stays at 10 minutes of the 10 hours and a 40-hour main is due straight away. An automatic run takes only the due characters. `dashboard_refresh` still runs on demand whatever the play, and it and /admin list each character's play against `ANALYSIS_STALE_PLAYED_HOURS`, "never refreshed" included.
- The 3D view's Updated models had pale, washed-out eyes, the iris almost gone (a Gnome looked startled; Humans' eyes went white). The viewer read the model's second set of texture coordinates upside down, so the eye's shine layer added the bright half of the eye texture over the whole eye. It now reads them as stored, and the eyes have their colour back. Glowing eyes (Undead, Night Elves) are unchanged.

## 0.7.0-rc.4 (2026-11-21)

### Added

- `dashboard_refresh` and /admin give each person's own server runs this month apart from the install's: the tool's `budget` is now marked `scope: "install"` ("this month, whole install"), and `yours` has the caller's own runs, cost and tokens (`scope: "you"`). /admin's AI analysis tile says "whole install", and each person's AI analysis cell adds their runs and cost this month. The spend file now records them per tenant number; runs from before this release aren't in anyone's share, and `yours` says how many.

### Fixed

- `dashboard_refresh` told a player whose refresh was waiting behind someone else's that "a refresh is already running for you". The server runs one refresh at a time: the answer now says `state: "running"` only when the caller's own job runs, else `state: "queued"` with its `position` ("queued behind another refresh (position 1)"). Calling again while queued adds no second job; a queued timer check becomes the player's own refresh, which runs whatever the timer says.
- An account with no data said "no character has any events yet: is the shipper sending?" even when the session was signed in as the wrong account (a Battle.net sign-in that landed on another of the person's accounts). It now names who the session is signed in as: "No character has any events yet for <name>. If that isn't you, sign in again with the account that ships your data."
- The server's run log line names the character (`char`).
- The MCP login's normal "Allow" button signed in as whichever account the browser's identity provider had used last: after one Battle.net sign-in, every later connector login landed on that account without asking, so the owner couldn't get his own back. The normal login now sends `prompt=select_account`, and the provider asks which account; the Battle.net button still goes straight to Battle.net.

## 0.7.0-rc.3 (2026-11-21)

### Added

- Gear: each worn item says where it stands against the best you can wear at your level for that slot: BiS, 2nd, or its percent of BiS (on the icon, so a phone shows it too). Nothing when there's no item to compare it with. Each upgrade says it in the same terms, "62% → 100% of BiS", and the upgrades are ordered by it: the slot furthest from BiS first, then the item that closes most of the gap. The same order and numbers in the Overview's gear moves and the MCP `gear` tool (`worn_standing`, `share_of_bis_pct`).
- The server can run the play analysis itself ("How you play": findings, nudges, patterns), for friends who never ask their own AI. It plays the MCP client with the same tools and prompt, through Anthropic's API (the default) or any OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama, vLLM, LM Studio): `ANALYSIS_PROVIDER`. It refreshes a player once they have played `ANALYSIS_STALE_PLAYED_HOURS` (10) since their last analysis, in the background after a page rebuild; any refresh resets that timer, including one from the player's own AI. A monthly cap in USD (`ANALYSIS_MONTHLY_BUDGET`) or tokens (`ANALYSIS_MONTHLY_TOKENS`) stops it; /admin shows the month's spend and each player's play since their last refresh. Credentials: workload identity federation (`ANALYSIS_WIF_*`, both providers) or `ANALYSIS_API_KEY_FILE`; off without them. See [Server-side analysis](docs/self-host/server-analysis.md).
- MCP tool `dashboard_refresh`: the caller's own analysis, refreshed now by the server, with what it started or skipped and why, and the budget left.
- Friends' page names from their BattleTag: a friend listed without a name (`""`, `null`, or the friends file as a plain list of subs) gets one at their first web login, from the header `PROXY_BATTLETAG_HEADER` names: the BattleTag's name in a-z and 0-9, a hyphen and 4 hex of an HMAC of the whole tag (`petrel-3f9a`), 6 or 8 when that is taken or reserved. Never the `#1234`. The key is `slug-key` in `WOW_STATE_DIR`, made on the first start; the BattleTag is read at that login and never stored. A name in the list still wins, existing names never change, and without a BattleTag the friend is in without an address as before. Slugs may now hold one hyphen and 24 characters. See [Names from the BattleTag](docs/self-host/friends.md#names-from-the-battletag).
- `wow_stats.py --prefill`: sends the server the item facts and 3D files it still lacks right away (the background runs ask once a day for item facts), prints how many went and exits.

### Fixed

- Server-side analysis took a model ending its turn as the end of a conversation: gpt-6.1-terra stopped twice in one run to ask the player to say "continue" in a new chat, and the server opened a new one each time, re-reading the brief, with up to 12 such restarts before giving up. Done stays the protocol's (the synthesis accepted); now the server's prompt says it runs unattended and must finish in one go; a model that still stops is told nobody will reply, twice in a row at most, then the run ends "incomplete" (on /admin, with the pass it stopped at). A run that doesn't finish puts the character's analysis back as it was: the player keeps the last finished one, never a partial run's findings. The MCP client's `analyse` prompt is unchanged.
- Quests handed in while the Recorder was off stayed "open" for good: the plan, the Dungeons panel, the abandon advice and the analysis kept sending players back to quests finished long ago (two City of Dalaran quests, their rewards already worn). A quest still in the log now closes as "done (inferred)", with the reason, when a reward only that quest gives is worn, in the bags or bank, or on any later gear or bags snapshot, or Questie's or Altoholic's list has it. The reward list now also takes the gear data's quest-only items forever-db has no reward rows for (Forever's new quests, 189 items). The analysis gets the reason in `quests.done_why` and every done quest in a new `quests_done` table.
- Gear: every off hand was scored as if worn with the best main hand on the list, not the one you wear, so its gain carried that main hand's too (a level 33 priest's orbs read +47 when they were worth +15). An off hand is now scored with your main hand, and a main hand with your off hand; only against a two-hander does a one-hand piece come with a partner to get, and then one you can wear now. The page, the Overview and the MCP `gear` tool name the worn hand it goes with.
- Server-side analysis on an OpenAI-compatible model never started its first run under a $3.50 monthly budget, and said "budget spent: $0.00": with no run of its own yet, the estimate was the priciest Claude run's. It is now the endpoint's own prices over a typical run's tokens (about $2 for glm-5.3, $0.11 for gpt-6-luna), and a run refused on that estimate says so, with the amount and what's left.
- Server-side analysis on OpenAI's own API failed every run with "the endpoint refused tool calling": OpenAI's reasoning models (gpt-6 and later) take function tools on the Responses API only, and Chat Completions answered 400 "use /v1/responses". OpenAI itself (no `ANALYSIS_BASE_URL`, or `api.openai.com`) now goes through Responses, stateless (`store: false`, the reasoning passed back each turn); every other OpenAI-compatible endpoint stays on Chat Completions.
- A failed server-side run says what the AI provider said: any 4xx naming tools used to read as "refused tool calling", other errors as a bare status, and z.ai's "Insufficient balance" (a 429) as a rate limit to wait out. Only an endpoint saying the model has no tools counts as a refusal now; the run's reason carries the provider's message (key-shaped strings masked).
- Server-side analysis on OpenAI counted cached prompt tokens at the full input price: a run's prompts are mostly cached, so the meter read several times the bill and the monthly cap stopped runs early. `ANALYSIS_PRICE_CACHED_INPUT_PER_MTOK` sets their price (the input price when unset).
- "How you play" said "No analysis yet" above the kept findings when the only client run was saved before runs had a `kind`.
- The nightly recheck re-ran a nudge's or pattern's findings but kept its words, so a line written at level 32 still said "well behind level 32" at 33. The client now writes changing values as `{column}` placeholders from the cited findings, rendered again every night; a line that types the level it was written at goes once the character is past it, and an `at_level` nudge turns `now` when reached. Older lines keep working.
- The journal in the night theme: each session was a pale parchment card on the dark page. It now takes the page's colours like every other panel; by day it stays parchment.
- The 3D view asked the shipper for no model files on a new server: the answer made before the client tables arrived was kept 10 minutes, and the gear history's outfits were never counted (an unpack error, logged as `model3d: <name>'s gear history: ValueError`).
- The 3D view never had a character model: Forever's HD models are 17-28 MB, over the server's 16 MB limit per file (now 48 MB). The shipper says in its log when a file is over the limit.
- New dungeon loot kept its bare names until the next day: the page's answers were rebuilt when events arrived, and the shipper sends its item facts after its events. New item facts now rebuild them too.

## 0.7.0-rc.2 (2026-11-20)

### Fixed

- The dungeon page's research-note drops (The Drowned City's) were empty in the container image: the lane file was looked up from the checkout's layout, not the installed one. CI now checks the installed layout inside the image.

## 0.7.0-rc.1 (2026-11-20)

**New dungeon loot gets its stats from your own game.** Forever's new
items (The Drowned City's) aren't in the item reference yet. The shipper
now reads the game's hotfix cache, sends the server the facts of the items
it lacks (name, slot, item level, stats, never anything else), and the
dungeon page shows their item level and stats and checks them against your
spec, like any other drop. Their tooltips fill in too. The reference wins
for any item it has. `item_facts = false` in the shipper's config turns it
off (docs/shipper.md, "Item facts").

**MCP connector: sign in with Battle.net.** With `OIDC_BATTLENET_ORG_ID`
and `OIDC_BATTLENET_IDP_ID` set (ZITADEL), the consent page a connector opens
has a "Sign in with Battle.net" button: the same login, sent straight to
Battle.net, so a friend needs no account session in that browser first.
Unset, nothing changes. The consent page now wears wow-stats' colours, day
and night.

**KB: JudgeHype as corroboration only.** JudgeHype (French) may be cited
next to another source but never counts as one, and the refresh doesn't
read it: of its 17 newest Forever items, 10 relayed sources we already
read. Its raid tier list is Wowhead's, so the new raid-tier line per class
rests on Wowhead alone, at low confidence, with the Holy Paladin
disagreement recorded.

**KB: the main article, not the first.** Page text now comes from the
`<article>` with the most text, so a sidebar teaser no longer stands in
for the page.

**3D view: lit like Blizzard's renders.** The light comes from the front,
above and the right, where it came from the left almost level: a blade
turned that way got no light and showed half as bright. The shine some
materials add on top is drawn too.

**3D view: a weapon in each hand, pauldrons on the right shoulder.** An
off-hand weapon is drawn in the left hand (it was refused as "in that
hand"), and a hunter holds their melee weapons like everyone else, the bow
or gun only when a hand is free, as Blizzard's own renders show them. A
one-sided pauldron sits on the shoulder it was made for, and a Skyborne's
Classic look is their Updated model (it was a goblin).

**No 3D model yet, said once and in the middle.** With nothing to draw,
"View in 3D" is off from the start and the line above it says why in one
sentence; on a phone that line stays between the two icon columns.

**tools/3dcases: the 3D view checked against Blizzard's renders.** Real
characters from public Battle.net profiles, drawn from your own install and
graded pass, warn or fail against the official Updated and Classic renders,
with a contact sheet per case. The repository keeps anonymous specs only;
names and renders stay on your disk, 30 days at most (tools/3dcases/README.md).

**Other players' data stays on your PC unless a page uses it.** The
shipper sent every combat log line as it was, with the name, realm and game
id of every player in range, plus each Recorder record whole (fields it
didn't know included) and the realm of every player Polestar's probe noted.
Now the combat log goes out parsed: the event, the zone, and who did what to
whom, where anyone but you and the monsters is "another player". Recorder
records carry only the kinds and fields the shipper knows, so a future
Recorder version that records more sends nothing new until the shipper
learns it. Other players reach the server as a name, when and where you met
them, and how (grouped, nearby, targeted): what the People tab, the journal
and the MCP's `players` read. No realm, no position. `/ingest` applies the
same cut to what a 0.6 shipper still sends until it updates
(`wowstats/otherplayers.py`). Every page works as before.

Upgrading: lines stored before this keep what 0.6 sent. The event contract
lists the LogsQL filters that find them (docs/contract.md, "Stored before
0.7.0"); read what they select before deleting anything.

**Battle.net fills what the addons didn't send, every 2 hours.** The
exporter reads every character every 2 hours on the clock (00:00, 02:00...
UTC, `PROFILE_INTERVAL=7200`) instead of once a day from whenever the pod
started, and a character it has never read within about 5 minutes: a friend
who installs at 09:30 no longer waits until the next morning. That's 6 calls
a character a pass, one at a time: about 390 calls an hour for 130
characters, 1% of Blizzard's 36,000 an hour. On the page, per kind: what an
addon sent wins and the API isn't used for it; what no addon sent comes from
the API. A friend without Syndicator sends a gear event with the professions
and no item, and the paperdoll was empty: it now shows Battle.net's worn
items (the whole set, said so), and the gear advice scores them. Level
splits only? The class, race and faction come from Battle.net, so the class
tools and the advice work. An API value read 30 days ago or more is never
shown. With nothing from either yet, the Equipment panel says when
Battle.net's copy comes instead of staying blank.

Upgrading: `PROFILE_INTERVAL` now defaults to 7200 (it was 86400). If you
set it yourself, it now also picks the clock slots: 86400 runs at 00:00 UTC.

**Six fixes from the server's security review.** Nothing removed, every
SQL function still there.

- `play_sql`, the judge and the recheck run their DuckDB queries in a child
  process capped at 512 MB over its loaded data. DuckDB's own
  `memory_limit` doesn't count a single value: `x || x || ...` over a
  `string_agg` took 2.3 GB with the limit at 256 MB, and 64 of them killed
  the whole server. Now only that query fails, with "aggregate or narrow
  it", and the next one gets a fresh child.
- The live public view (`ui-public`) follows Settings when you give it
  `USERS_URL` and the share token: public page off answers 404 everywhere,
  and a character you hid from the public page isn't there. Before, it
  showed everyone.
- `/ingest` answers 400 to JSON nested a few thousand levels deep, instead
  of a 500.
- A store error no longer hands its VictoriaLogs address and error text to
  the page, the shipper or the AI: they get "the data store didn't answer"
  with a request id, and the server's log has the detail under that id.
- Outbound downloads (wago.tools, the icon host, Blizzard's render host,
  `RENDER_FROM`) follow a redirect only to the same host, or to another
  allowed render address. Anywhere else is an error.
- Each character's analysis folder is its name plus a short hash, so Ærin
  and Érin of one realm no longer share one. An existing folder moves to
  its new name the first time the character comes up.

Upgrading: nothing to do. `ui-public` with several people takes `USERS_URL`
and `ANALYSIS_SHARE_TOKEN_FILE` to follow Settings (docs/self-host/friends.md).

**Releases are one button, and this repository is the only one.**
Development moved here from the private repository it was exported from.
Actions > release > Run workflow picks the next version (`rc` for a
release candidate `X.Y.Z-rc.N`, a prerelease), merges the release commit
through a pull request, builds and signs both images, tags and publishes
the GitHub release with this file's section and each image's digest
(docs/development.md, "Releasing"). The knowledge base's refresh job runs
in a private CI mirror that holds its credentials, and its pull requests
land here behind the same checks. From 0.7.0 the shipper orders release
candidates: a candidate sorts below its final, and a 0.7 shipper takes one.
A 0.6 shipper can't read a candidate's version: it stays put and takes the
final.

**The installers download a pinned uv.** When a friend's machine has no uv,
the install command fetches uv 0.13.9 from its GitHub release and checks the
SHA-256 before unpacking it, instead of piping astral.sh's installer into a
shell. The proxy docs now lead with `PROXY_SECRET_FILE`: trusting a subnet
also trusts every other container on it.

**The shipper only takes signed updates.** Each release signs the shipper
files (version plus every file's sha256) with an Ed25519 key that lives in
a GitHub environment, and the shipper pins the public half. Before, the
hashes came from the same server as the files, so whoever took over a
server could run code on every friend's PC within 2 hours. Now the worst
they get is holding people on their version. An update without a valid
signature is refused: the shipper keeps its version, logs why, and the
owner's overview shows "auto-update refused" on that row. Shippers from
0.5.1 to 0.6.x don't check signatures yet, so they take 0.7.0 the old way
and check everything after it. Building your own image with a changed
shipper: sign it with your own key first (docs/self-host/upgrade.md,
"Signing your own build"), or your players stay on their version.

**/zogzog/ file names give nothing away.** A sealed file used to sit at
`data/char/<Name>.json.enc`, so a 404 or a 200 told anyone whether a
hidden character existed. Names are now an HMAC of the path under the
link's key. The publisher rewrites the files on the new version and the
links people already have keep working.

**The knowledge base's text is marked as data for AI clients.** The KB
refreshes and merges on its own, from web pages through a model, and the
MCP hands that text to friends' AI clients. A new gate refuses any KB text
shaped like an instruction to a model (tool-call markup, "ignore the
previous instructions", a chat role at the start of a line), and every
tool that answers with KB text says so in its description and in an
`untrusted_reference_data` field at the top of the answer.

**One odd addon file no longer stops the shipping.** A SavedVariables file
nested 5,000 deep, or a Syndicator file with a string where a table goes,
made the run fail before it shipped or checked for updates, so the fix
could never arrive. Each addon is now read on its own: a file that can't be
read costs its events for that run and one line in the log, and the run
still ships what's spooled and checks for an update. A corrupt `state.json`
is kept as `state.json.corrupt` and the run starts fresh.

**Smaller fixes from the same review.** The shipper never follows a
redirect when it ships or checks the server (a 302 used to carry the upload
password to the other host, and its 200 marked the events sent). The
installers write the WoW folder as a proper TOML string, so a folder like
`D:\Jeux d'été\...` works on Windows and is no longer left out on a Mac.
The 3D view's file reader holds every compressed block to its declared size
(80 MB used to come out of a block that declared 16 bytes). And the Mac
install line refuses anything but https, redirects included.

**0.6 shippers update themselves to 0.7.0, no reinstall.** They ask the
server they ship to, never GitHub, so the repository move changes nothing
for them: within 6 hours of the server moving to 0.7.0 they take it, and
from then on every update is signed. `tests/test_shipper_upgrade.py` runs
the real 0.6.1 file against this server to prove it. A shipper that still
reports an older version 7 hours after the server moved (`auto_update =
false`, or an update it refused) gets the update command on its start page.

**kb_topics names every document.** A lane's statements folded in from
another lane, or written by a model pass, carry no research document; the
lane's `documents` no longer lists them as a nameless `""` entry (the 0.7.0
release candidate's A/B check).

**Four things the A/B check caught before 0.7.0.** Without a Battle.net
read of the achievements, the Legacy count says "unknown" again instead of
0, and the card's guild tells "no guild" from "not read yet" (the exporter
now says which). A level's snapshot shows each profession's cap as the
Recorder had seen it by then. A session that starts mid-minute keeps that
minute's XP in the journal. And the class guide keeps its contradictions and
knowledge-base ids: the 24,000-character cap now cuts them last, and the
next bracket sends the statements it repeats by id.

**Every page with a source missing.** Without Syndicator, the gear page and
the `gear` tool give the best items for your level (or upgrades over what
Battle.net says you wear), marked as an estimate, with the one step that
turns them into upgrades over your own gear. Without the Recorder, the
journal has one entry per logout with the time played estimated from your
level times, the level page counts Polestar's quests and kills, and
`sessions` answers the same way. With only level splits, the summary takes
the level from them, and the class tools say the class isn't known yet
instead of failing. Switch the Recorder on later (or install Syndicator)
and the earlier history stays, with one marker where the detail starts;
the hint goes once its data arrives, and the plan turns step by step.

**Gear says who it's built for.** Every upgrade, dungeon drop and the MCP
`gear` tool now say "Built for your spec", "Not made for your spec. Fine
while levelling", "Made for healers, not for a mage", or "Role unknown: pick
your role in settings",
with what it costs you ("Leather: you can wear mail", "Most of its stats do
nothing for a Fury warrior") and who usually rolls first in a group.
Off-spec pieces name the on-spec one for the same slot. Stats your class
can't use (healing power for a warrior, Spirit for a rogue, attack power for
a mage) now count zero; stats your class uses but your spec doesn't (a
shadow priest's healing power) count partly, more before the spec's
31-point talent at 40. Close options rank the on-spec one first, so some
lists, dungeon suggestions and plans reorder.
Nothing is hidden. Checked against published gear tables for every spec
(docs/gear-reference.md, "Who an item is built for").

**An A/B check before each release.** `tools/ab/ab.py` records what your
install asks its stores once, then shows the same answers to the old and the
new version with the clock frozen, and lists every answer that changed, a
section per feature, regressions first (docs/development.md, "Before a
release: the A/B check"). Recordings stay out of git and go after 30 days.

**"View in 3D" draws your character from your own game files, with our own
viewer.** The 3D view no longer loads anything from Wowhead, so Forever's
own races and items (Skyborne on its own body, the armor Forever added)
show as in game, with the enchant's glow, and the Updated/Classic switch
works for every race the game has both models of. The files are the ones
your shipper sends (it updates itself); until they've come, the toggle keeps
Battle.net's picture and says why in one line. The public and friends'
sites carry each character's files, nothing more.

**/start's addon list reads true.** The summary counts the rows ("4 sending
· 1 installed, not sending yet · 2 optional to add"), an optional addon you
haven't installed says "optional, not installed" instead of a red cross,
and an installed one that isn't sending yet shows only the step left ("Log
in once with each character"), never "Install it".

**A plan without the Recorder.** "Plan my next 2 hours" used to refuse a
character whose Polestar Recorder was off. It now plans by level from
Polestar's level splits: the XP an hour of the last five levels, the level
reached in the time asked, Polestar's guide for each level, a checkpoint at
each level-up, the dungeon branch and the mount goal. The plan says it is
estimated and ends with the one line that turns it into the step-by-step
plan (`/psrec on`, then `/reload`). The gold rate and a plan's review use the
level splits too when the Recorder's online time is missing.

**The shipper sends the 3D view its files.** Every 15 minutes at most, it
asks your server which game files the 3D view still wants (client tables,
then your characters' models and textures) and sends them from your WoW
install, within the same daily budget as your events, never twice. Each
person's files are kept in their own folder and go when they are removed.
`model3d_files = false` turns it off (docs/shipper.md, "The 3D view's
files").

**The shipper no longer calls valid game files missing.** It checks each
file against its content key instead of header fields the game doesn't
keep reliably, and reads the files the game downloaded while playing
(stored with no header). It also tries every index entry and reads the index again
when the game rewrites it, and tries each copy the game lists for a file
(one per platform, say) before calling it missing.

**Every talent change, with WoWSims Exporter.** An optional addon now
fills the talent history: WoWSims Exporter saves your build each time a
talent changes, at every level, and the shipper sends one event per change
(never the other players the addon keeps in its file). With it, the talents
end with every change since you installed it; without it, Battle.net's last
3 weeks and the one step that fills in the rest. A talent the addon can't
save is named under the history, not dropped. The MCP's `talent_changes`
gives the same. Help, /start, the installers' check and the docs now show
one addon list, each with what it adds and its one step; the shipper's
`--talents-check` counts what it would read from the addon's file, counts
only.

**Talent changes are back, from Battle.net.** The character page's talents
end with "Talent changes over the last 3 weeks", where "Build by level" was:
each day the build changed, the points per tree before and after, and the
talents gained or dropped. It reads the exporter's daily talent reads
straight from vm-wow (23 days), on the live full view only: no static
bundle, file or cache keeps them. Without Battle.net, or with no change,
the block says so in one line. The MCP has the same answer in a new tool,
`talent_changes`, under the same "Battle.net data to my AI" switch.

**Some figures shift slightly: sessions and route time have one definition
everywhere**, the semantic layer's: a session ends after 330 s without a
Recorder record (was 20 minutes or a login), at its last record, and a
level's route time is the class's median over its side's races (was your
race's own run). `route_reference` drops `race`.

**The shipper can read the game's own files.** A first step towards a 3D
view drawn from each player's own WoW install instead of Wowhead's: the
shipper reads Forever's storage (CASC), read-only, and
`wow_stats.py --casc-extract` copies the files the 3D view needs to a folder
for checking by hand (docs/shipper.md, "Flags"). Nothing is sent yet.

**The shipper checks for updates every 2 hours** instead of 6, so fixes reach
players' PCs the same afternoon. Shippers already installed keep their 6 hours
until they take this version. `auto_update = false` still pins it.

**Blue posts from Wowhead's Forever blue tracker.** The weekly meta pass no
longer reads the forums' own staff-post list (`/groups/blizzard-tracker`,
which the forums' robots.txt disallows). It takes the list from Wowhead's
Forever blue tracker feed and each staff post's text from the forum topic's
JSON, with any player post it quotes cut out. The digest line says "Blizzard
staff", never a name (docs/kb.md, "The meta pass").

**Three addons fewer: Blizzard's API first.** wow-stats no longer reads
CharacterExport-Forever, Narcissus or Vulcan SimBench. The rule now: data
comes from Blizzard's API when it has it, from an addon only when it doesn't
or when you need history past Blizzard's 30 days (new page: [Where the data
comes from](docs/explanation/data-sources.md)).

- Talents come from Battle.net only. A character Battle.net doesn't know
  yet has no talents on the card. The "Build by level" talent
  history and the talents line in level snapshots are gone: they came from
  `/simx` exports, and Battle.net's readings can't be kept.
- The 3D look comes from Battle.net only; a character it doesn't know wears
  its race's default look.
- Profession caps come from Polestar Recorder's skill records, else from the
  trainer tiers (a floor: the move then says "if you haven't trained it
  yet"). Professions in the answers carry `max_rank_from` (`recorder` or
  `tiers`).
- A self-hosted site without a Battle.net client has no talents and no
  looks (every model wears the race's default).

**Your choice of spec first, the talents only as a hint.** The gear advice
scores for the spec you chose (the site's `WEIGHTS`, Polestar's Stat
weights, then ForeverLevelingBiS's or GearQuestForever's pick) and falls
back on the tree with the most talent points only when none of them says
anything. A Shadow priest levelling with most points in Discipline gets
Shadow weights. This is the 2026-11-18 rule again: the change above had put
the talents before the addon's pick.

**Sex, guild and Legacy points from Battle.net.** The exporter now reads
the guild from the profile and the completed achievements (each Legacy
challenge is one). The card's guild, the 3D model's sex and the Legacy
count come from there. Without Battle.net they read "unknown", and the
Legacy count is no longer a floor guessed from levels and skills. The
shipper stops sending `sex` and `guild`.

**The MCP has your current talents.** `character_summary` gives the spec,
the points per tree and when Battle.net read them; `class_guide`,
`class_meta` and `sanity_check` use them. Read live from the dashboard's
VictoriaMetrics (23 days), nothing kept. On a multi-user install the
"Battle.net data to my AI" setting decides.

Upgrading: nothing to change. Events already stored with `talents`,
`talent_ranks`, `looks` or a CharacterExport cap stay stored and nothing
reads them. The shipper updates itself and stops sending those fields; an
older server takes the newer shipper's events (the fields are just absent),
and this server takes an older shipper's (it ignores them). You can uninstall
the three addons.

**Level times and dungeon runs from Mac characters with an accent.** The
Mac client can hand back a character folder's name with its accents split
off ("E" plus a combining mark). The shipper used that name for level
splits, dungeon runs and the Recorder's sessions, the app refuses it, so
those events were skipped while the character itself (named by Polestar)
arrived. Names are now composed (NFC) everywhere; the next run sends the
whole history Polestar kept.

**"Installed" is not "on" for Polestar Recorder.** The setup report says
`recorder_on: "unknown"` when no account ever saved the Recorder's switch
(installed by the install command, the game not played since), instead of
leaving the field out. The start page, the admin badge and the missing-data
lines treat it like "off": type /psrec on in game, then /reload.

**State of the meta for your class.** The character card has a short
paragraph on what your class and spec run on Forever right now, with the
day it was last checked; friends see it on your zogzog link (it holds
nothing of yours). Your own page adds a 0-100 score of how close you are
(talents against the levelling build, gear against the best you can wear
now, enchants the meta uses), the gaps that cost the most with where to fix
each, and the meta in full. The MCP's new `class_meta` tool answers "how
close am I to the meta?". The knowledge comes from a new sourced KB lane
(`data/kb/research/class-meta.json`), and the weekly meta pass now tags each
class statement with its spec and the part of the meta it changes. New
route: `/api/meta` (full view only).

Tank and healer specs score their talents against the levelling build
before 60 and against their role's endgame build at 60 (eight sourced
builds in the lane; a spec without one shows "no target yet").

**This week on Forever.** The weekly KB pass now also writes a news digest:
one line per item in our own words, with its category, day, source and who
it concerns, and the events it announces (with the day only when the source
names it, else "announced, no date"). The full view's Overview has a "This week
on Forever" panel (your characters' news first, earlier weeks a click away),
the meta box a "Coming up" list, and "What to change" the prep for an event
less than four weeks away (attunement quests, resistance, consumables:
advice, not scored). The card (and the zogzog link) gets one line when an
event for the class and level is under two weeks away. New MCP tool `news`;
`class_meta` and `plan_session` know what's coming. New route: `/api/news`
(full view only).

**Named metrics for your AI.** Two new MCP tools, `list_metrics` and
`query_metric`, answer from a semantic layer: 47 metrics (time at a level
counted four ways, XP per hour, dungeon time, deaths, gold, sessions...) and
14 dimensions, defined once in `wowstats/analysis/semantic.yaml` and compiled
to SQL by [Boring Semantic Layer](https://github.com/boringdata/boring-semantic-layer).
An answer carries its rows, the SQL that made them and each metric's
definition; filter values are bound as parameters, never written into the SQL.
`propose_finding` takes a `query_metric` call as evidence (`metric_query`).
[Semantic layer](docs/reference/semantic-layer.md) has the catalogue.

**The layer answers the four questions it couldn't.** 66 metrics now, 25
dimensions:
- **Quests:** `quest` and `quest_id` dimensions over a new `quests` table
  (each accept paired with its hand-in or abandon), and `quest_xp_per_min`.
  A quest's time is the online time while it was in the log, shared equally
  among the quests open then: the times add up to the time played, nothing
  counted twice ([Time on a quest](docs/reference/semantic-layer.md#time-on-a-quest)).
- **Guide steps:** `guide`, `step`, and their titles from the route
  reference, on the timeline.
- **Money:** `unexplained_gold_delta`, what the purse did between two logouts
  less the quest money in between, by level or by week (`gold_moved_copper`
  adds up, unlike `gold_delta_copper`). No addon the kit reads saves
  spending, so it is never "repairs".
- **Across two models:** `kills_per_combat_hour`, `deaths_per_online_hour`,
  `quest_xp_per_played_hour`, declared as expressions over metric names
  and grouped by the dimensions both models share.

**The character page reads the layer.** Time, XP, quests and kills per
level, the pace and the average level, each dungeon run's time and XP/hour,
and the deaths by zone and level come from the same definitions as
`query_metric`, worked out when the answers are built (never per page
view). Same numbers on the demo, checked against a snapshot taken before.
A death record with no level or zone of its own now counts at the level of
the dings and session records around it and in the zone of the last zone
record, as `query_metric` always counted it (it showed under "Unknown" or no
level before). The session journal, the Overview's pace against the route
and the purse still count their own way: the layer defines those
differently, and changing them is a separate decision.

**`play_sql` runs on DuckDB.** Same tables, columns, row cap and timeout, one
character's data, read only, no file or network access. The dialect changes in
a few places: `7/2` is 3.5 (`//` for whole numbers), `SUM(kind = 'kill')`
becomes `COUNT(*) FILTER (WHERE kind = 'kill')`, and `repeat`, `lpad`, `rpad`,
`printf`, `format`, `replace` and `regexp_replace` are refused (they can build
a value bigger than the memory limit counts). Findings kept before keep
being rechecked on SQLite.

**The main image is on Docker's hardened Python, not Alpine.** DuckDB ships
no wheel for Alpine's musl, and Debian slim, the first glibc base we tried,
came with 53 HIGH and CRITICAL findings in Trivy (47 with no fix).
`dhi.io/python:3.15-debian13` is glibc, has no shell and no system package
manager, and its HIGH findings drop to 15, all marked not affected in the
image's own VEX statements. The image is 291 MB (amd64). Same user (10001),
ports, entrypoint and read-only root filesystem. `docker exec ... sh` no
longer works: use `docker exec ... python -c ...` or `wow-stats ...`.
**Building from source now needs a free Docker Hub account**: run
`docker login dhi.io` once. The released images pull as before. The
renderer stays on Debian slim. Sizes in [Sizing](docs/self-host/sizing.md).

**What players are saying.** "This week on Forever" now ends with up to
five of the week's busiest threads from the official forums' Forever
discussion and r/wowforever, each with one line in our own words and a link
to the thread, labelled as opinion: they never set a date or count as a
fact. The weekly KB pass writes them (`data/kb/community.json`); Reddit is
read through the model's web search, never Reddit's site or API. The MCP's
`news` returns them as `community`, or alone with `category=community`.

**The knowledge base stops growing week after week.** A lifecycle runs with
the weekly KB pass (`tools/kb/lifecycle.py`, same PR): the week's file folds
into the lanes (one current file each, git keeps the history), statements
leave a week after their `expires` or once `superseded_by` a newer one, the
same claim from two sources becomes one statement citing both, statements
not checked for 120 days are re-read against their source, and the news
keeps 8 weeks in `data/kb/news.json` (older weeks: one line each in
`digest-history.json`). First run: 2.10 MiB to 1.71 MiB, 6 duplicates
merged. Each KB file and the total have a size cap
(`data/kb/budget.json`); going over fails a test and opens one issue.
The MCP's KB answers (`kb_*`, `class_guide`, `class_meta`, `news`) stay
under 24,000 characters (the largest was 86,924): `kb_topics` lists 15
per lane by default, `class_guide` no longer sends the next bracket's
repeats twice, and anything past the cap says what it cut.

## 0.6.1 (2026-11-20)

The fixes from the review before the repository went public, Battle.net
data per person, no more Auction House API, and visitor counts on the full
view. On upgrade: drop the Auction House settings, and route Plausible ahead
of the full view's login if you count visits (both below). The shipper
picks up 0.6.1 by itself within 6 hours.

**Battle.net data is per person.** Every profile series the exporter writes
now carries the `account_id` of the person whose character it is, and every
reader (the pages, `/render/`, the 3D view, the MCP, the publisher) asks for
its own account. A character name two people send goes to the owner if it is
theirs, else to nobody: a friend who names someone else's character reads
nothing. The exporter reads at most 12 characters a day per friend. On
upgrade, the exporter's first profile pass (at start) relabels everything;
until it runs, pages show no Battle.net data. The privacy page also says
that the program updates itself without asking, that the /zogzog/ link
exists for everyone, and that the app's log names a character in a few lines.

**No Auction House API.** The exporter no longer reads Blizzard's Auction
House API: no `auctions` loop, no `wow_auction_*` series. Auction prices come
only from your own Auctionator scans, as the gear advice and the price list
already did. The full view's market strip keeps the WoW Token and the
ruleset status; the MCP's `market` tool (still behind "Battle.net data to my
AI") now returns those two and takes no arguments. `/api/market` takes no
`faction`. On upgrade, drop `AH_FACTION`, `AH_NONCOMMODITY`, `AH_INTERVAL`,
`WATCH_ITEMS` and `BNET_RULESET_ID` from your settings: nothing reads them.
Auction series already in your Battle.net store expire with its retention.

**The full view counts visits too.** With `PLAUSIBLE_DOMAIN` set on the ui,
every page of the full view sends one Plausible pageview, like the public and
`/zogzog/` pages: its path alone, never the query or the `#fragment`, an
install code replaced by `code`. Use the same `PLAUSIBLE_DOMAIN` as the
publisher and every host counts into one Plausible site, a new friend's page
included; Plausible's Hostname filter tells them apart. The privacy page now
says what Plausible gets. On upgrade: route `/js/script.js` and `/api/event`
on the full view's name to Plausible ahead of its login, and let
`GET /privacy/*` through without one (docs/deploy.md, "Visitor counts").

**Classic models say when they can't show the hairstyle.** The updated
models added hairstyles (and faces, beards) the classic ones never had: a
gnome male's "Genius" is the 12th style, classic gnome males have 7, so
Classic drew the 7th. Forever has no classic customization to map it
properly, so under Classic one line now says "This hairstyle doesn't exist
on classic models: switch to Updated to see it." The same goes for any
other choice past the end of the classic list. Nothing changes under
Updated.

**Three review fixes (memory and a proxy).** `play_sql` values are capped at
64 KB instead of 1 MB: the sorter kept 1,000 rows of 999 KB in memory (568
MB) and now spills to disk (57 MB). `/api/model3d` answers 400 to a `sex`
that isn't male or female, `/api/plan` checks the character like the other
routes, and the per-key lock tables no longer grow (a lock lives while its
answer is built; the plan's locks are 64 stripes and its cache 64 entries), so a friend can't grow the shared process one name
at a time. With `AUTH=open` on the full view, a request that carries a
proxy header (`X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto`,
`Forwarded`, `Via`, `X-Real-IP`) gets 403, and `ALLOWED_HOSTS` together
with `AUTH=open` stops the server at start: put a login on it first
(`AUTH=basic` or `AUTH=proxy`). The public view and `ALLOW_OPEN=1` are
unchanged. The 421 page now says to set a login too.

**The 3D model draws when the Character tab opens.** With 3D remembered
from an earlier visit, the full view (which opens on the Overview) started the
model in the hidden Character tab, where the frame had no size. Wowhead's
viewer refused a 0/0 aspect ratio and the page said "Couldn't draw the
model: undefined." twice. The frame now waits until the tab is shown, and
an error from the viewer says what it said instead of "undefined".

**A friend who isn't on the list yet knows they're waiting.** The refusal
page said "This account doesn't have access yet. Ask the person who runs
this site to add you." It now says they're signed in, the person who runs
the site hasn't let them in yet, and to reload once they have. With a login
provider that lets everyone through (the friends list being the lock),
that's the page a new friend sees after their first sign-in.

## 0.6.0 (2026-11-19)

Everything since 0.5.0 in one release. The full view opens on answers it
already built, the shipper updates itself, a new friend no longer restarts
anything, and the owner gets `/admin`. Run the shipper's update line once
on each computer (below); after that it keeps itself current.

**The privacy page says what the owner can see.** It used to say the
server's logs kept the time, the page and the login id. They keep more: the
web server's access log has the IP address, the browser and the page, and
the app's own log has login ids and account numbers. The page now lists
those, the login service, the goals, plan constraints and findings people
or their AI store, and what the MCP server receives (tools and their
arguments, never the conversation). The offboarding promise now covers
logs and backups: both expire on their own. Upgrading: set
`PRIVACY_LOG_DAYS` to what your log store keeps (unset, the page tells
friends to ask you), and keep query strings out of your access log.

**An overview for the owner, at `/admin`.** On a shared install, one page
with a row per person: on the friends list or not, the last visit, the last
AI use, the last upload (events, shipper version, Mac or Windows), how many
characters, the public page, the last site build and its error, and the
addons as one badge, with a red flag for no upload in 3 days or an old
shipper. Above it, the server's version, the publisher's last pass, the
Battle.net exporter's last read, the state folder's size, and green/red
checks of the stores and services it's set up with. Times, counts and
versions only: no character name, zone or gold. Friends get a 404 there, and
their privacy page says what the owner sees. Upgrading: the publisher now
POSTs its pass to the share port (`/publisher.json`, next to `USERS_URL`;
`PUBLISH_REPORT_URL` to point it elsewhere): a full view older than the
publisher refuses it, and the pass goes on.

**A new friend no longer needs a restart.** The full view watches the
friends list (`WOW_FRIENDS_FILE`) and applies a change within about 10
seconds: the new friend is provisioned and their answers are built at
once, a removed one is offboarded. A broken file still changes nothing.
Upgrading: mount the file's folder, not the file alone (a Kubernetes
`subPath` mount never sees a new version), and drop any pod checksum of
the list, so adding a friend stops restarting the server.

**The shipper updates itself.** Every 6 hours at most it asks the server
it ships to for its version and, when it's newer, replaces itself with the
server's copy: same server, same upload password, https only (or this
computer), the sha256 checked, a file that doesn't compile refused, never
a downgrade. The old copy stays as `wow_stats.py.prev`; a changed launchd
agent or scheduled task is set up again by the new installer. A shipper
from before this version can't do it yet: `/start` asks for the update line
once, from the version in the setup report. `auto_update = false` pins it
(docs/shipper.md, "Auto-update").

**Auction prices are no longer sent twice.** Auctionator keys a realm's
prices "Realm Faction" or "Realm" and moved one account's from one key to
the other with the same numbers: the shipper sent all 1,347 again. A price
already sent under the realm's other key now counts as sent.

**Upgrading:** route `/install/update/shipper*` like `/ingest`, without
your page login (docs/self-host/reverse-proxy.md). Run the shipper's update
line (or its installer) once on each computer.

**Forever's own armor in 3D, and Classic models.** Armor Forever added
(Violet Sash, Graveweave Bindings, Ephemeral Grips...) has no model data on
Wowhead, so the 3D view drew the character without it. It is now dressed
from Forever's own client tables, shipped with the image
(`data/model3d-reference.json.gz`, docs/model3d-reference.md); anything
still missing is listed under the model as its icon. A switch under the
model picks Updated or Classic models, like the game's option, remembered
in the browser; Settings sets the default for everyone who opens your
page. Classic face and hair are approximate. With one person,
`MODEL3D_MODELS=sd` sets that default.

**The full view opens without waiting on the logs.** One background thread
builds every answer a plain open of the page asks for, once per new data
(checked every 60 seconds, market prices every 5 minutes), and the page
serves that set. "Reading the logs" on every open is gone.

**Advice that matches the character.** A profession cap read while the
window was open no longer says "Train Expert" after you trained it: the
newer of the window's cap and the Recorder's wins, and an old reading says
to open the window and `/reload`. A weapon pair leads with the half you
still need, and "craft it now" needs the recipe known, read from
Altoholic's `DataStore_Crafts`, as well as the skill. The missing-data
lines name the character they're about and use the shipper's setup report
(which addons each character loads) instead of guessing "add X".

**Friends get their page name from the friends list.** The owner sets it
there (`{sub: slug}`); the name form and `POST /api/me/slug` are gone. A
name that's taken or reserved shows "Your page's address needs a fix from
the person who runs this site" on `/start`, one log line, and a "name
conflict" row in `/admin`. No owner name is written into the product
anymore.

**A new account gets a page, not a 404.** With no character yet, the
publisher writes a short "No character yet" page with the `/reload` line.
Unknown addresses get a 404 in the site's own look.

**Docs and the help video.** The docs site is now a manual by reader
(player, AI user, self-hoster), with screenshots from the demo. The `/help`
clips are re-recorded on the decoy character from today's `/start`, and
the page catches up by itself after the first upload.

**Releases build arm64 natively.** Each image and platform builds on a
runner of its own architecture, no QEMU, and the legs merge into one index
with its provenance and SBOM checked. The compose kit's Caddy, nginx and
Traefik images are bumped (2.11.8, 1.31.0, 3.7.18).

## 0.5.0 (2026-11-19)

The first public edition. The start page asks one question at a time, the
install says what is missing, and the advice says where it disagrees with
Polestar.

**The public edition.** This tree now builds the release anyone can run:
the license in every binary, a compose kit that builds from the tree, the
demo's five made-up characters, synthetic test fixtures, `mcp-stdio` for an
AI client on the same machine, docs for running it yourself, and images
for amd64 and arm64. Nothing in it names a private server. `ui` gets a
1 GiB memory limit (`UI_MEM_LIMIT`): it peaked at 472 MiB on 55 weeks of
the demo, 100,705 events, which took 6.5 MiB on disk.

**`/start` asks one thing per visit.** The seven-step wizard showed about
1,650 words every time. Now one state at a time: not installed (the install
command for your system), installed and waiting (play), nothing arrived
(the one fix), or data arriving (when it last did, and "Open my page").
50 to 90 words visible; the rest sits in folds, the full manual at
`/start/print`.

**The install ends with a checklist, the pages say what's missing.** The
installer checks the WoW folder, Polestar, the Recorder and its switch,
Questie or Altoholic, the optional addons, saved data, the server and the
background program: a tick, or a cross with one fix. Only a missing WoW
folder, a refused password or an unregistered background program fail it.
The shipper sends a small setup report, and the full view, `/start` and
the MCP show one "Missing: ... <fix>" line per kind of data you don't send
yet. `docs/videos/steps.json` is the one addon list. The report also lists,
per character, the addons it doesn't load (unticked at character select),
so a line says "installed but not ticked for this character", "play to your
next level" or "Add Questie" as the case is, and never guesses from the gap
alone. On `/start` each line names the character it is about: a fresh level
1 alt's gaps no longer read as your main's.

**Dungeon advice follows Polestar, and says why when it doesn't.** At level
32 it said Scarlet Monastery: Library while Polestar said Razorfen Kraul.
Below Polestar's entry level only a measured reason sends you in (its
quests ready, or your runs beating your questing XP per hour); gear alone
never does. Razorfen Kraul and Gnomeregan now show their quests: four
dungeons file them under a second area id.

**Upgrades from the Auction House, and random-suffix items scored on real
stats.** An upgrade on sale in your latest Auctionator scan (today or
yesterday) shows its price, the gain per gold and "cheap" when it costs
what you make in one level; a drop on sale says "or buy it now". A
random-suffix item is scored on its actual suffix when we have the link,
else on its likely one, not on armor alone.

**No more auto-reload.** The full view redrew every section each minute,
closing folds and moving the page. It now checks every 3 minutes while the
tab is visible and shows a "New data · Show" pill.

**The character picture, Forever's weapons in 3D, one item level.** The
picture works with several people on one server, weapons Forever added
show in the 3D view with their own texture, and the panel shows one item
level.

**The MCP login works through a reverse proxy.** `docs/mcp.md` says which
paths must keep their cookies (`/authorize`, `/consent`,
`/oauth/callback`), and a test runs the whole login through a proxy.

**Releases you can check.** Each image carries its build provenance and an
SBOM per platform, and from the public repository GitHub attestations and a
cosign signature: `docs/development.md`, "Verifying an image". CI fails on
a known vulnerability in any locked package, a secret in the history or a
broken workflow; Dependabot proposes updates weekly. SECURITY.md says how
to report a vulnerability privately.

### Upgrading

- The images now have a `HEALTHCHECK` (`python -m wowstats.healthcheck`,
  it picks the port from the mode): `docker ps` and compose show
  `healthy`. Kubernetes ignores it and keeps its own probes.
- New OCI labels on both images: `org.opencontainers.image.version`,
  `revision`, `title` and `description`.

## 0.4.3 (2026-11-19)

Friends get in by the list alone, every past level gets a picture, every
class gets a guide, and quests done before Polestar count.

**Quests done before Polestar count as done.** The Recorder only sees the
quests you hand in while it runs, so a quest finished before you installed
it read as not done: The Stockade's Bazil Thredd showed as blocked by The
Unsent Letter, handed in long before. One answer now serves the Dungeons
panel, its blockers, the plan, the gear advice and the MCP
(`wowstats/quests.py`). A quest is done when the Recorder saw it handed in,
when the game flagged it done to the Recorder, when Questie or Altoholic
saved it in the game's full list of completed quests (new `wow-quests`
feed from the shipper), when you hold gear only that quest gives, or when
a quest that needed it is done (forever-db's prerequisites, down the
chain: this one works with no extra addon). Altoholic saves that list in a
way that loses some low bits; the shipper only sends what it can trust.
The addon list now says Questie or Altoholic gives the quests you did
before Polestar. The MCP's plan facts carry `quests_done_from`, a count per
source.

**The help page starts with what to do.** `/help/` was a wall of text: the
required addons said three times, a raw address in a sentence, three
install routes mixed together, and a friend had to read it all to find the
first step. It is now one page in the order a friend needs it. The first
phone screen says what you get and gives 3 steps with one action each (the
CurseForge app and Polestar, your start page for your own install command
with Mac and Windows tabs, then play). The install and update commands now
put Polestar Recorder in the AddOns folder themselves: the official release
pinned at 0.4.1.1, its SHA-256 checked, never over a newer copy, the game's
saved data untouched; the zip by hand stays as a fold. Uninstall still
leaves every addon in place. Below: your page (the
tour, one line per tab), your AI (its address, an app picker, 3 things to
ask), 6 questions for when something is wrong, and everything else folded
(the addons by hand, what the program sends, the sending check, update,
uninstall). 337 words show before anything is opened; a browser test keeps
it under 350 and the 3 steps inside the first 390x844 screen.

**A guide for every class.** The character page says what the character's
class needs at its level: the trainer spells to buy now and at the next
training level, with the class trainers in the capitals of its side (its
race's capital first, Wowhead links and map coordinates), the next talent
points (from its own talents when an export holds them), and a habit or
two. It shows on the public page, `/zogzog/` and the full view: it is built
from the class, race, side, level and talents only. The MCP's new
`class_guide` tool gives the whole guide for any class and level: spec and
talent path by bracket, stat priority, solo play, dungeon role, class
quests, common mistakes and the respec moment, every statement sourced
(official, datamined, guide authors) with its date, confidence and scope,
and the contradictions between sources. The knowledge base gains the
`classes` lane; every talent path is checked against Forever's current
trees, and the trainer lists and talent names come from Forever's own data
(`tools/class_reference/export.py`).
**The dungeon estimate says the verdict first.** "Worth it: ~35 min for ~17k
XP (27% of a level), 1.4× faster than your questing." Under 0.8× of the
player's questing it reads "Slower than questing: go for the drops only", in
between "About the same as questing" (`FASTER`/`SLOWER` in
`plan/dungeongain.py`). The move and the Dungeons panel now go verdict, quests
to pick up, then the drops, and a quest blocked by another says where to get
that one first ("The Unsent Letter first: An Unsent Letter drops from Edwin
VanCleef"). Same line in the MCP's `dungeon_options`.

**The gear comparison is readable in both themes.** Opened under the doll
by day, it showed cream text on parchment: the character plate kept its
night text colours but took the day page's panel colour. The plate now
takes the whole night palette and the journal's parchment the whole day
palette, so anything opened on them gets its background and its text from
the same set. Item names darken by day through the palette too, not by a
page-wide rule, so they stay bright on the plate. A few day colours went a
shade darker to clear 4.5:1 (muted text, gold, the journal's muted and gold
at night). The comparison also drops the score's arithmetic line: the
header already gives the gain. `tests/test_contrast_browser.py` checks every
visible text in the comparison, the tooltip, the dungeon cards, the
character plate and the journal entries for WCAG AA, Chromium and WebKit,
dark and light.

**Dungeons link their guide.** A dungeon's name sent you to Wowhead's zone
page, a map and a list. It now opens Wowhead's Forever dungeon guide (how
to get in, quests, loot), with a "Dungeon guide" link next to the levels;
Scarlet Monastery, Dire Maul, Stratholme and Blackrock Spire list one guide
per wing. 32 guides, each fetched on 2026-11-19, kept as a static list in
the knowledge base (`dungeon.wowhead_guides`). Krol'dok Stronghold,
Blackmaw Hold and Shaper's Terrace have no guide yet and keep their old
link. The entrance still links the zone, and the MCP's `dungeon_options`
gives each dungeon its link too. `tests/test_links.py` holds every link
builder to the `/forever/` form and every fixed link (guides, addon pages,
the Ask links) to the checked list.

**Dungeon or quest? The dungeon move says.** The Overview's dungeon move,
the Dungeons panel and the MCP's `dungeon_options` now give one more run of
the dungeon at the character's level, from one estimate: the time, the XP,
the share of the level, and the XP per hour against the player's own
questing, then the dungeon's quests to pick up first, who gives them and
where. "One run: ~35 min, ~17k XP (27% of level 32), about 1.4× your
questing pace." The numbers come from the player's own runs there, the kill
XP carried to the level now with Classic's kill formula over the creatures
killed (`xp.kill_formula` in the KB). A dungeon never run gets the KB's
clear time plus the player's own travel time, and the quests' XP alone,
said so: its kill XP is never guessed. `dungeon_options` used the average
of every trip anywhere, so its XP per hour disagreed with the page; it
gives the same `gain` now.

**Friends get in.** A friend on the list got "Not set up yet ... its
organisation isn't allowed" on the full view: the login proxy sent the org
header empty, and the web view refused anyone without it. The friends list
alone decides now, on the web and the MCP alike: the owner's sub, or a sub
on `WOW_FRIENDS_FILE`, nothing else. A listed friend who logs in before the
next pass over the list is set up on the spot. Without the list only the
owner gets in. `WOW_OWNER_ORG`, `OIDC_ALLOWED_ORGS` and `PROXY_ORG_HEADER`
no longer do anything with `WOW_STATE_DIR`, and neither do the MCP's role
and org checks there. A refused login gets a page in the app's own style:
"This account doesn't have access yet. Ask <the owner> to add you.", with a log-out
link to switch accounts.

**A picture for every past level.** Level by level now shows the character
as it looked at that level, in that level's gear, with "View in 3D" next to
it, on the public page and `/zogzog/`. The pictures are drawn by a new
renderer, `wow-stats render`, in its own image
(the renderer image, the Dockerfile's `renderer` target) and its own pod that holds no key:
the publisher sends it the race, sex, look and item display ids and keeps
the picture. A picture not drawn yet says so, and the 3D view works
meanwhile. Set `RENDER_URL` on the publisher (docs/deploy.md).

**A character name can't write outside its bundle.** The publisher used
names from stored events as file names and wrote wherever the decoded path
led: `../../victim/b1/public/data/char/X` landed next to the build. A name
is now letters, space, hyphen, apostrophe and ASCII digits, starting with a
letter (`wowstats/names.py`). `/ingest` refuses a line with any other, the
shipper skips it, and the publisher skips a stored one and logs it. Every
bundle write also resolves its destination and refuses one outside the
bundle.

**`/zogzog/` names nobody in clear.** Its link preview carried the
character's name and title in the page's HTML, even with the public page
off. Sealed pages get a generic preview; the title appears once the data is
open.

**"Make a new link" works for the owner.** His key came from
`ZOGZOG_KEY_FILE` whatever the generation, so he got the same link back. The
file now seeds his first link only; after that his key is derived from the
master like a friend's. No key or link is printed any more, not even the
first one: `wow-stats zogzog-key link` prints it on demand. Install codes
are out of the request log.

**Removing the last friend removes them.** A valid `{}` friends list means
nobody, and offboards everyone listed before. A missing, unreadable or
broken file still changes nothing.

**MCP tools say what they write.** `plan_session`, `analysis_brief`,
`propose_finding` and `analysis_submit` are marked as writers that can
replace data; `play_sql` and the KB lookups as writers that only add to a
run's coverage. A test keeps every tool classified.

**MCP answers that were wrong.** `sessions` mixed creature levels into the
level range (a level-10 hunter showed 3 to 14). `character_summary` counted
deaths by `source_id` and said 0 without it; it now counts them like
`deaths`. XP per hour is rounded, not truncated, everywhere. `mount_goal`
and `dungeon_options` answered with an error when the time to a level was
unknown; they answer with what's known and say what's missing.
`check_plan` passed a dungeon plan after "no dungeons": it now checks the
plan against the constraints and the time budget and names the one broken.

**The friends' manual.** The help page (`/help/`) covers what a friend
needs without asking: what you get, the install, how to tell the shipper
runs, a section per part of your page (Overview, Character, dungeons, the
plan, Timeline, Journal, the zogzog and public pages, Settings), your AI,
updating, uninstalling and how to have your data deleted. Eight clips go
with it, a dark and a light take each, played in the reader's theme: the
onboarding (45 s), a tour of the page (60 s), the zogzog link, Settings,
and the Mac terminal for the install, the shipper's log, the update and the
uninstall. The start page plays the onboarding at the top and the install
clip at step 4, served with byte ranges (Safari needs them). `demo/`
records them on a throwaway lab with invented characters
(`demo/README.md`). Windows has no clip: VHS can't record PowerShell on a
Mac.

**Your AI, in one screen.** The help page's AI part is the server, the
client picker, five one-line asks and what your AI sees. The 34-tool
reference is gone from the page: the AI reads it from the server, and every
tool argument now describes itself in its schema (`market`'s `faction` had
borrowed a levelling route's text). The addon list is a name and a line
each; the Recorder's CurseForge caveat is said once, in the step that needs
it. The level ring's CSS class was `.badge`, so every badge on the help
page drew as an 82 px dark ring over its text: it is `.lvring` now, and a
browser test keeps badges one line tall.

## 0.4.2 (2026-11-19)

The friends list decides who's in, and Forever's new dungeons have the right
levels.

**Friends come and go with the list** (#25). With `WOW_FRIENDS_FILE` (rendered
from your identity provider: user id and slug), the full view checks the list
every 2 minutes. A listed friend gets their tenant and slug before their
first login; a login that isn't listed gets none. A friend who leaves the
list is offboarded on the spot: their events (their VictoriaLogs tenant),
state, registry entry and upload password go, their shipper gets a 401, the
MCP refuses them, and the publisher drops their site from every static pod
(`GET /bundles` on the upload port). A missing, empty or unreadable list
changes nothing, and the owner is never touched. `wow-stats users reconcile`
runs a pass now. The privacy page says how long backups keep events (30 days).

**Forever's dungeon ranges** (#23). Ruins of Lordaeron is 15-22, Excavation
Site: Wetlands 26-33 and City of Dalaran 28-35. They come from the game
client's own table, which Polestar agrees with and the owner checked in game.
Blizzard's announcement gave narrower ranges; the KB keeps that statement
and says why it lost.

## 0.4.1 (2026-11-19)

Bags and bank count, a sourced knowledge base, Windows tested end to end,
and pages that only say what to do.

**Bags and bank** (#17). What you carry now counts. An upgrade you already
hold says "In your bags: equip it" (or "in the bank: take it out first") on
the Character page, the Overview, the plans and the MCP's `gear`, instead of
where it drops. This also finds upgrades that drop nowhere in particular,
such as vendor items and random drops. The Dungeons panel says when a drop
you have is still an upgrade to equip. Emptied bags now send an empty list,
so an old one doesn't linger.

**Knowledge base** (#18). Six research lanes are in `data/kb/research/`:
game rules, dungeons, levelling, economy and professions, PvP and raids.
That's 1,585 statements, 158 contradictions kept side by side and 330 open
questions. Each statement has a source, a date and a confidence. The loader
and the tests refuse a source that isn't on the allow-list. Profession ranks,
training prices, riding, the talent capstone, the instance limit and
Forever's new dungeons come from the KB now, not from constants in the code.
New MCP tool `kb_topics`; `kb_rule` shows a rule's conflicts.

**The KB stays current** (#18, `docs/kb.md`). The `kb-refresh` workflow
watches the game build, the hotfix pages, Polestar and the addons every day
and opens one issue per kind of change. It proposes new statements as a PR,
never merged on its own and capped at $3 a run. Once a month it checks
every cited link.

**Windows, tested for real** (#16). A CI job on Windows runs a friend's
whole path: the `/start` install line in PowerShell 5.1 and 7 under a fresh
Windows's Restricted policy, the scheduled task, a ship to `/ingest`, the
update line, a password reset and the uninstall line. It runs for a plain
profile and for `C:\Users\Zoë Ü` without Git. Fixed along the way: uv's
installer failed under the Restricted policy; the task could pick a
virtualenv's Python; the uninstall line failed in PowerShell 7; an accented
WoW folder came back garbled; the app couldn't lock its user list on
Windows. A git failure in the shipper log now shows git's own message.

**Pages say what to do** (#19). The Overview, Character and Dungeons pages
dropped their explainers and footers. Actions, numbers and Wowhead links
stay. A caveat that changes the action stays in a few words, like "100g
(less with reputation)".

**Shared pages show actions only** (#19). How-you-play patterns stay in the
full view. `/zogzog/` gets the analysis nudges in full, minus any that name
another player, a creature from your records, a time, a date or where you
are right now.

**Empty states** (#19). No data yet, a level-1 character or nothing
equipped: the page names the next install step instead of a spinner or a
blank panel. `/settings` can reset the upload password and shows the new
command right away.

**Every item has a name** (#19). About 12,000 items showed as "Unknown item
(211500)". They have names now. An item with no name anywhere shows as
"Head item, see on Wowhead", never as a number.

**Fixes.** `/ingest` stores `component=wow` as a stream field, so a store
filtered on `{component="wow"}` (a shared `LOGS_FILTER`) sees events sent
through the app, friends' included (#17). A level-1 character with no play
session got the other faction's dungeons (#19).

**CI** runs on pushes to main and on PRs again, without the browser
tests: those run locally before every push and release (#20, #21).

### Upgrading from 0.4.0

- No new or changed variables, ports, volumes or routes (`docs/deploy.md`
  is unchanged for the deployment). Pin image 0.4.1 with its digest on
  every pod.
- Move your own shipper to `/ingest`. In `~/.config/wow-stats/config.toml`,
  set `[ship]` `url = "https://<full host>/ingest"`, `user` to your slug
  and the password to the upload password from `/settings` (reset
  it there to get a fresh one). Your events land in tenant 0, the store the
  pages read, with `component=wow`. Then stop sending to the old receiver,
  so each event arrives once (duplicates are harmless anyway: every event
  has a `source_id`).

## 0.4.0 (2026-11-19)

The big one: friends can have their own wow-stats on the same install, no
model runs on the server anymore, and every page tells you what to do next
instead of just what happened.

**Several people on one install.** Anyone in an allowed org who logs in
gets their own store tenant, state, upload password, zogzog key and site
(`<name>-wow.<domain>`) at first login. Nobody is added by hand. `/start`
walks a new player through the install with a single-use code (15
minutes); updates use one code-free command (`/install/update`). `/settings`,
`/privacy`, and `wow-stats users list|hosts|disable|enable|delete` for the
owner. Two-tenant tests check that one person never sees another's names,
over every page, MCP tool, plan, analysis and built site.

**No model on the server.** The Coach button, `/api/coach`, the nightly
model run and the `wow-stats-agent` image (340 MB of Claude Code CLI) are
gone. The play analysis is now driven by your own assistant over the MCP:
it asks its own questions in SQL (`analysis_brief`, `play_sql`), and the
server re-runs every finding before keeping it (`propose_finding`). Every
finding says what to do, where, what blocks it and the next step; one
without that is kept as an observation. **Ask Claude** and **Ask ChatGPT**
on the character page open a chat with the instruction typed in and no
data in the link.

**MCP on FastMCP**, on
its own port (8090). The login is your identity provider (OIDC) only: the
passphrase login is gone, and the server refuses to start with
`MCP_LOGIN=password`. Works with 14 clients (Claude, Claude Code, ChatGPT,
Codex, Cursor, VS Code, Gemini CLI...). Battle.net market data reaches an
assistant only for a person who turns on "Battle.net data to my AI".

**Overview**: one verdict, four numbers (pace against the route, next
level, guide steps in order, rested XP) and at most 3 moves on show, each
naming the item, quest, dungeon, trainer or zone, with Wowhead Forever
links. /zogzog/ keeps its moves but never one about a creature.

**Character tab**: click a slot to compare what you wear with the best
replacement stat by stat; upgrades by where you get them or by slot; real
gates ("Level 35 + Tailoring 200", not just the level).

**Dungeons**: one block per dungeon that matters to you, with a verdict on
the whole trip (gear, quests, XP, gold), the next step, the entrance from
Polestar's data, the drops scored for your spec, the quests and what a run
is worth. A character below every dungeon gets the first three that open.

**Help page** (`/help/#<key>`, sealed under its own key): an Install part
from the same steps as `/start`, and an MCP part for developers with a
client picker and a card per tool.

**Lighter on VictoriaLogs.** One cache per character serves every endpoint:
a full-view load went from 132 queries to 15 and the store's peak from 691
to 146 MiB on a local copy (vl-wow was OOM-killed three times on
2026-11-18). At most 2 VictoriaLogs requests at once per process
(`VL_CONCURRENCY`).

**Fixed**

- Deaths named "creature 1871" when the recorder had no name for the
  killer: now forever-db's name (13,226 creatures), else "an elite
  creature" or "an unnamed creature". Never an id.
- The Overview crashed for a character with no level anywhere (no
  snapshot, no ding). The level now comes from the newest ding when there's
  no snapshot.
- The help page scrolled sideways at 390 px.

**Upgrading**: docs/deploy.md, "Upgrading from 0.3.x", lists every env
var to remove, change and add.
