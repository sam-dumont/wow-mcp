# Privacy tiers

**For:** self-hosters, and anyone who wants the exact rules for who sees what. **At the
end:** you know what wow-stats collects, the three tiers of the same data, and what never
leaves the full view.

## What it collects

Everything comes from files the game writes in the `_forever_` folder, read by the shipper on
the computer you play on:

- **Polestar** and **Polestar Recorder**: level splits, XP, zones, quests accepted and handed
  in, kills, deaths, flights, guide steps, dungeon runs, gold at logout, and the players you
  crossed paths with.
- **Auctionator**: your own auction house scans, one price per item and day.
- **The combat log** (`Logs/WoWCombatLog*.txt`), when you turn it on in game.
- **Syndicator** and the `WTF` character folders: every character of the account with its
  race, class, sex, guild, gold and the gear it wears.
- **Altoholic**, **Questie**: your `/played` from level 1, the quests finished before Polestar.
- **Polestar Client's `sent.jsonl`**, read only: the Recorder chunks it uploaded in the last 8
  days.

Never collected: your Battle.net account id or account folder name, or anything outside the
`_forever_` folder. Every addon: [Addons](../reference/addons.md). Every event and field:
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
| The character card and the climb: level, race, class, item level, equipment, professions, talents, your class at this level, played time per level against Polestar's route, dungeons cleared | `char` | yes | yes, plus gold, guild, zones, guide steps, flights and deaths as counts per zone and level | yes: the card, the climb, and which dungeon, when, at what level. No zones, gold or guild |
| The level slider and timeline | `timeline` | yes | yes, deaths without their killer | the level stops only |
| A past level: its gear and picture | `snapshot` | yes | yes, plus time, zones and where each item came from | its gear and picture |
| Item tooltips | `items` | yes | yes | yes |
| The 3D character | `model3d` | yes | yes | yes |
| The journal, one entry per play session | `journal` | yes | yes, no other players | no |
| Gear advice and upgrades | `gear` | yes | yes | no |
| The Overview's next moves | `overview` | yes | items, dungeons, professions and zones; no creatures | no |
| The Dungeons panel | `dungeons` | yes | yes, without the party | no |
| Loot history | `loot` | yes | yes | no |
| The play analysis' nudges | `analysis` | yes | screened nudges and time per level | no |
| Auction house, WoW Token, realm status (Battle.net, with the exporter) | `market` | yes | yes | no |
| Your own Auctionator prices | `prices` | yes | yes | no |
| Other players: the People view, groups, who you crossed | `people` | yes | no | no |
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

## Your AI

Your AI assistant reads what the MCP tools return: your own game data, the players your game
showed you included. Battle.net data (auction house, token, the armory look) reaches it only
when a person turns on "Battle.net data to my AI" on a shared install, for their own data.
[Ask about your character](../ai/asking.md#what-the-assistant-can-see).

## Visitor counts

Optional (`PLAUSIBLE_DOMAIN`): one pageview per visit on the static sites, through your own
address, with the path alone (`/` or `/zogzog/`): never the `/zogzog/` key, the query or a
referrer. [Share your page, visitor counts](../self-host/sharing.md#visitor-counts-optional).
