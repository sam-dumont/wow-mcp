# Changelog

## Unreleased

### Fixed

- The server's first analysis ran for every character with no refresh on record, including an alt logged into once to reserve its name ($0.32 for nothing in the lab). The timer is now each character's own: its play since its last refresh, or all its play on record when it has none, so that alt stays at 10 minutes of the 10 hours and a 40-hour main is due straight away. An automatic run takes only the due characters. `dashboard_refresh` still runs on demand whatever the play, and it and /admin list each character's play against `ANALYSIS_STALE_PLAYED_HOURS`, "never refreshed" included.

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
