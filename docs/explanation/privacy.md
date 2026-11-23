# Privacy tiers

**For:** self-hosters, and anyone who wants the exact rules for who sees what. **At the
end:** you know what wow-stats collects, the three tiers of the same data, and what never
leaves the full view.

## What it collects

Everything comes from files the game writes in the `_forever_` folder, read by the shipper on
the computer you play on:

- **Polestar** and **Polestar Recorder**: level times, XP, zones, quests accepted and handed
  in, kills, deaths, flights, guide steps, dungeon runs, gold at logout, and the players you
  crossed paths with.
- **Auctionator**: your own auction house scans, one price per item and day.
- **The combat log** (`Logs/WoWCombatLog*.txt`), when you turn it on in game: its events,
  parsed. Only your own character and creatures are named; any other player is "another
  player". The players it saw go to the People list by name only.
- **Other players**, everywhere: only what the People tab shows (name, when, zone, how you
  met). No realm, no game id, and nothing a Recorder version records beyond what the shipper
  knows ([The event contract](../contract.md), `wow-player`).
- **Syndicator** and the `WTF` character folders: every character of the account with its
  race, class, gold and the gear it wears. Sex and guild come from Battle.net, when you set it
  up.
- **Altoholic**, **Questie**: your `/played` from level 1, the quests finished before Polestar.
- **Altoholic** also: the recipes and spells each character knows (and the spells still to come),
  its reputations, its character sheet stats and lifetime honorable kills, its raid and dungeon
  lockouts, and its money, XP, rested XP and hearthstone at each logout. Never its friends list,
  calendar, guild tables (name, rank, bank, members' gear), mail or auction bidders: the shipper
  doesn't read them, and the server drops any of it an older or modified shipper sends.
- **WoWSims Exporter**: your own characters' talent builds, each time one changes. Never the
  other players it keeps in its file (the people you inspected or grouped with).
- **Polestar Client's `sent.jsonl`**, read only: the Recorder chunks it uploaded in the last 8
  days.
- **The game's own files** for the 3D view, read only from the install's `Data/` storage: a
  few client tables and the models and textures your characters wear. Game data, nothing
  about you; they go to your own server only and stay in your folder there
  ([The 3D view's files](../shipper.md#the-3d-views-files)). Your character's picture is drawn
  from the same files by the server's own renderer, never downloaded from Blizzard.
- **The game's hotfix cache**: for an item the server's reference data doesn't have yet (a
  dungeon added in a patch), its facts: name, slot, item level, stats. Game data again, kept
  in your folder on the server ([Item facts](../shipper.md#item-facts)).
- **Battle.net**, when the host set up an API client: every 2 hours, each character's look,
  talents, sex, guild and worn gear, in a store that keeps 23 days (Blizzard's terms allow 30).
  What it says the character wears also proves quests done (by their rewards): that reading is
  live only, never stored and never in the analysis' database.

Never collected: your Battle.net account id or account folder name, or anything else outside
the `_forever_` folder. Every addon: [Addons](../reference/addons.md). Every event and field:
[The event contract](../contract.md).

## Three tiers

- **You**: the live full view, behind your login.
- **Friends**: `/zogzog/#<key>`, a static, sealed copy. The key is the part after `#`, which
  browsers never send to a server.
- **Anyone**: `/`, the public static site.

Each tier is a strict allow list in [`wowstats/redact.py`](../../wowstats/redact.py) (`PUBLIC`
and `ZOGZOG`): a field the code adds to an answer stays out of both static sites until someone
adds it to that tier's list. This table follows those lists; a test fails when they drift
apart.

<!-- privacy-table -->
| What | Answer | You | Friends | Anyone |
|---|---|---|---|---|
| Which sections the page turns on | `config` | yes | yes | yes |
| The character list | `chars` | yes | yes | yes: names and levels |
| The character card and the climb: level, race, class, item level, equipment, professions, talents, your class at this level, played time per level against Polestar's route, dungeons cleared | `char` | yes | yes, plus gold, guild, zones, guide steps, flights and deaths as counts per zone and level, and a short state of the meta for your class and spec (plus one line when an event for your class and level is under two weeks away) | yes: the card, the climb, and which dungeon, when, at what level. No zones, gold or guild |
| BiS standing per worn slot: the paperdoll's badge (BiS, 2nd or its share of BiS) and the badge legend, without the advice behind it | `char.gear.standing` | yes | yes, with the gear advice | yes |
| The level slider and timeline | `timeline` | yes | yes, deaths without their killer | the level stops only |
| A past level: its gear and picture | `snapshot` | yes | yes, plus time, zones and where each item came from | its gear and picture |
| Item tooltips | `items` | yes | yes | yes |
| The 3D character | `model3d` | yes | yes | yes |
| The journal, one entry per play session | `journal` | yes | yes, no other players | no |
| Gear advice and upgrades | `gear` | yes | yes | no |
| The Overview's next moves | `overview` | yes | items, dungeons, professions and zones; no creatures | no |
| The Dungeons panel | `dungeons` | yes | yes, without the party | no |
| Loot history | `loot` | yes | yes | no |
| Talent changes, the last 3 weeks | `talents` | yes | only from your WoWSims Exporter saves; Battle.net's readings never leave the server (its 30-day rule) | no |
| The play analysis' nudges | `analysis` | yes | screened nudges and time per level | no |
| WoW Token, realm status (Battle.net, with the exporter) | `market` | yes | yes | no |
| Your own Auctionator prices | `prices` | yes | yes | no |
| Other players: the People view, groups, who you crossed | `people` | yes | no | no |
| Your class's meta in full and how close you are to it | `meta` | yes | no | no |
| This week on Forever: the news digest, what concerns your characters first, and what players are saying (forum and r/wowforever threads: titles, links, counts, our own line; no names) | `news` | yes | no | no |
<!-- /privacy-table -->

Never on either static site: where the character is right now (zone, last seen, the guide step
in progress) and what killed it.

Other players show up in your full view because the game showed them to you. Don't publish
the full view.

## Checked by tests

- Every file of both built sites (the sealed one decrypted) is walked, and any key outside its
  tier's list fails the build's test.
- A public server runs against a store where every row carries other players' names, gold,
  positions and prices; a single byte of them in any public answer fails the test.

## What the owner sees

On a shared install the owner has an overview page, `/admin`
([Admin overview](../self-host/admin.md)). Per person it shows times, counts and versions: the
last visit to the full view, the last AI use, the last upload (how many events, the shipper's
version, macOS or Windows), how many characters (a number), the public page on or off and its
last build, and whether the required addons are installed. Never a character's name, a zone,
gold or anything else from the game. Friends read the same on their privacy page.

The privacy page in the app (`/privacy`) also tells your friends what you can see beyond their
game data: the free text they or their AI store (analysis goals up to 600 characters, plan
constraints up to 300, findings), your reverse proxy's access log (time, IP, user agent, path,
login), the app's own log (MCP logins with the login id, failed tool names, installs, updates
and offboarding with the account number), your login provider, and what the MCP server
receives (tool names and arguments, never the conversation). It can't know how long you keep
things, so you tell it:

- `PRIVACY_LOG_DAYS`: what your log store really keeps, for the access log and the app's log
  alike. Unset, the page tells friends to ask you.
- `PRIVACY_BACKUP_DAYS`: what your backups really keep, counted from the deletion.
- `FRIENDS_PURGE_DAYS` (7): how long a friend's data waits after you take them off the
  list. The page quotes it and the deletion reads it, so the two can't disagree.
- Keep query strings out of your access log: the page's API calls carry character names there
  (`/api/loot?name=...`). The app's own log already drops them.

## Your AI

Your AI assistant reads what the MCP tools return: your own game data, the players your game
showed you included. Battle.net data (the token price, the current talents, the quests your
worn gear proves done) reaches it only when a person turns on "Battle.net data to my AI" on a
shared install, for their own data. On a one-person install it goes to the owner's AI.
Your AI never starts a run of the server's own analysis (below).
[Ask about your character](../ai/asking.md#what-the-assistant-can-see).

## The server's own analysis

Off unless the host configures an AI provider (`ANALYSIS_PROVIDER`: Anthropic, OpenAI or any
OpenAI-compatible endpoint) and leaves `ANALYSIS_AUTO` on. Then the server runs the play
analysis itself for a character with `ANALYSIS_STALE_PLAYED_HOURS` of play (10 by default; the
host picks) since its analysis was last refreshed, by the person's own AI or the server. That
provider receives what the analysis tools answer about that character: its events and the
names of other players its game showed, never Battle.net data. The host pays for it. Nothing
else starts a server run: no MCP call (`dashboard_refresh` included: it hands the work to your
own AI), no button. The app's Privacy page gives the host's hours when it's on, and says so
when a provider is configured but `ANALYSIS_AUTO=off`: then nothing goes to it.
[Server-side analysis](../self-host/server-analysis.md).

## Visitor counts

Optional (`PLAUSIBLE_DOMAIN`): one pageview per page on the static sites and, with it set on
the ui too, the full view, through your own address. Plausible gets the path alone (`/`,
`/zogzog/`, `/settings`), never the `/zogzog/` key or the query; the site that sent the
visitor when their browser says; their browser, device type and country. No cookies. The
privacy page tells friends the same. [Share your page, visitor counts](../self-host/sharing.md#visitor-counts-optional).

This documentation site counts page views with the maintainer's self-hosted Plausible: no
cookies, no personal data.
