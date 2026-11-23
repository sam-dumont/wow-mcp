# wow-stats

A levelling log for World of Warcraft: Forever, with an MCP server so your own AI assistant
can read it.

**[Quick start](docs/quickstart.md)**: the demo character on your computer and a first answer
about him, in about 10 minutes.

A small program on the computer you play on reads what the game and your addons already save.
A server you run keeps it and works out the answers: time per level against Polestar's route,
where the hours went, how far each slot is from its best in slot and where the next upgrade
drops, the dungeons worth running, deaths, one journal entry per play session, a plan for your
next two hours. Your page shows them, and the MCP tools hand them to any assistant.

It runs on your computer or a small box at home. Nothing goes anywhere else unless you set it
up that way. (Yes, it's overkill for levelling stats: a hardened image, signed releases, OIDC,
and a 3D viewer that reads the game's own files.)

It's a hobby project, for hobbyists who host a few friends: GPL-3.0, no warranty, no support
promise, and security fixes land when I can make them. If you use a friend's server, you trust
that friend: they hold your data, and the install command comes from their server.

![The Overview of the demo character Nibbet: his pace against Polestar's route, the hours to his next level, the guide steps done in order, the next moves, and time per level against the route](docs/images/overview.webp)

**[Live demo, the full page](https://sam-dumont.github.io/wow-mcp/demo/friends/)**: Nibbet-Gearholt,
an invented Gnome priest, as his friends see him. Demo data, no install. His
**[public profile](https://sam-dumont.github.io/wow-mcp/demo/)** is the cut-down page anyone
with the address sees.

## The tools compute, the AI is optional

The MCP tools work out their answers in code, from your data: no model in the loop, and the
same data gives the same answer. The ones you'll call most:

| You want | Tool |
|---|---|
| Where the character stands: level, XP, gold, pace, time to the next level | `character_summary` |
| Time, XP, quests and kills per level | `levels` |
| Each play session: zones, XP, deaths, dungeons | `sessions` |
| Your dungeon runs, or with `options` the ones worth a run now | `dungeons` |
| Every slot, its next upgrade and where it drops | `gear` |
| The next 45 minutes | `next_step` |
| A plan for the next hours, from your own pace | `plan_session` |
| Any named number (time played by level, deaths by zone...) with its definition | `list_metrics`, `query_metric` |

Every tool, its arguments and the prompts: [Tools and prompts](docs/reference/mcp-tools.md).
What they return on the demo, call by call: [A session, recorded](site/pages/mcp-session.md).

On top, two optional layers:

- **Your assistant** (Claude, ChatGPT, any MCP client) picks the tools and writes the reply, in
  your own account. The `coach` prompt plans your next session from them; the `analyse` prompt
  runs the play analysis, where the assistant asks its own questions of your data. The server
  keeps a finding only once its query re-runs to the same numbers, and writes those numbers
  into its text itself. That checks the numbers, not the reading.
  [Ask about your character](docs/ai/asking.md).
- **The server's own analysis**, off until the host configures an AI provider (Anthropic,
  OpenAI or any OpenAI-compatible endpoint). Then the server runs the play analysis itself for
  each character with 10 hours of play since its last refresh (the host can change that). What it reads about that
  character goes to that provider, never Battle.net data.
  [Server-side analysis](docs/self-host/server-analysis.md).

## Three ways in

1. **[Join a friend's server](docs/players/getting-started.md)**: someone runs it for you and
   sent you a link. Nothing from this repository needed.
2. **[Try the demo](docs/quickstart.md)**: Docker and a made-up character on your computer, no
   game needed.
3. **[Track my character](docs/install.md)**: run the server, add the addons and the small
   program on the computer you play on.

Then, for your AI: **[Connect your AI](docs/ai/connect.md)**. Everything else is in
**[the manual](docs/README.md)**, also on the [docs site](https://sam-dumont.github.io/wow-mcp/).

## How it fits together

```
 the PC you play on                      your server (docker compose)
 the shipper  ---POST over HTTPS--->  ui :8080  ---> VictoriaLogs (your events, kept)
   every 5 min, reads WTF/ and Logs/     |  your page (the full view, behind your login)
                                         |  /mcp for your AI assistant
                                         +-- publish + site :8081: static copies for friends
                                         +-- optional: exporter ---> VictoriaMetrics
                                             (Battle.net data, 23 days)
```

- **Your data stays yours.** Your page shows everything, behind your login. Friends get a
  sealed copy with a key in the link; anyone gets a public copy cut to an allow list. Neither
  ever shows other players, what killed you, or where you are now.
  [Privacy tiers](docs/explanation/privacy.md), [what's shared](docs/players/privacy.md).
- **Known addons only.** It reads [Polestar](https://github.com/fitzforge/polestar) and other
  well-known addons; it never asks you to install one of its own. [Addons](docs/reference/addons.md).
- **Small.** One player fits in 2 vCPU and 2 GiB of RAM; a year of events takes a few MiB.
  [Sizing](docs/self-host/sizing.md).

## The manual

| You are | Start here |
|---|---|
| A player a friend invited | [Join a friend's server](docs/players/getting-started.md), then [your page at a glance](docs/players/your-page.md) and [something's wrong](docs/players/troubleshooting.md) |
| Running your own server | [Quick start](docs/quickstart.md), [install it](docs/install.md), then [run it on a server](docs/self-host/production.md) |
| Using an AI assistant | [Connect your AI](docs/ai/connect.md), [every tool](docs/reference/mcp-tools.md), [ask about your character](docs/ai/asking.md) |
| Contributing | [Development](docs/development.md), [architecture](docs/architecture.md), [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |

What changed in each version: [CHANGELOG.md](CHANGELOG.md).

## Similar projects

- [Polestar](https://github.com/fitzforge/polestar) (GPL-3.0): the guide addon this kit stands
  on. Its Recorder logs your play; the route clock and gear scoring are its work.
- [WoW Character Intelligence](https://github.com/bkhouser/WoW4EMCP) (MIT): addon and Windows
  companion, quests, gear and bags in SQLite on your PC with provenance, read-only MCP.
- [WoW Eye](https://github.com/TravinDSO/woweye) (MIT): addon and MCP; the agent reads your
  character and puts markers and a compass in the game.
- [wow-coach](https://github.com/RayanBayat/wow-coach) (MIT): addon, desktop coach app and
  MCP, with a journal and goals per character.
- [LevelTrail](https://github.com/Bencepo/LevelTrail) (all rights reserved, source on GitHub):
  addon for level splits, deaths, gold and zones, shown in a browser viewer.
- [wcl-mcp](https://github.com/webmakersteve/wcl-mcp) (no license file): Warcraft Logs reports
  and combat logs in DuckDB, read-only SQL over MCP.
- [WowCoach Forever](https://forever.wowcoach.gg/) (hosted service): uploaded combat log
  reviews, with an AI connection on paid plans.
- [WoWthing](https://github.com/ThingEngineering/wowthing-again) (GPL-3.0): web app tracking
  characters and collections through the Battle.net API and an addon.

This one focuses on a self-hosted levelling history, metric definitions shared by the page and
the AI, and findings whose numbers are recomputed from the data.

## License

GPL-3.0-only ([LICENSE](LICENSE)). The kit ships route times and gear data
made from [Polestar](https://github.com/fitzforge/polestar) (GPL-3.0-only) and
its quest database forever-db (GPL-3.0), so it is GPL too. Thanks to
fitzforge and Polestar's contributors: the route clock and the gear scoring
are theirs. The gear data carries parts under MIT, from ForeverLevelingBiS
v0.12.0-beta by Thunder and from wowsims: their notices are in
[THIRD_PARTY.md](THIRD_PARTY.md).

The images (`/app/LICENSE`) and the installed shipper (`LICENSE` next to it)
carry the license and the source they were built from.

The page's fonts (Cinzel, Alegreya, Alegreya Sans) are under the SIL Open
Font License, their license files in `web/fonts/`.

World of Warcraft is a trademark of Blizzard Entertainment. Item names,
icons and models the server fetches stay Blizzard's: this kit doesn't
bundle them.
