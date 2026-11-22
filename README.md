# wow-stats

A levelling log for World of Warcraft: Forever, with an MCP server so your own AI assistant
can read it.

A small program on the computer you play on reads what the game and your addons already save.
A server you run turns it into your climb: time per level against Polestar's route, where the
hours went, how far each slot is from its best in slot and where the next upgrade drops, the
dungeons worth running, deaths, one journal entry per play session, a plan for your next two
hours. Claude, ChatGPT or any MCP client can ask about it. When your assistant keeps a finding,
the server re-runs its SQL and writes every measured number in its text from that result, and
again each night.

It runs on your computer or a small box at home. Nothing goes anywhere else unless you set it
up that way. (Yes, it's absolutely overkill for levelling stats: a hardened image, signed
releases, OIDC, and a 3D viewer that reads the game's own files. The character page still
answers in 31 ms on the demo.)

It's a hobby project, for hobbyists who host a few friends: GPL-3.0, no warranty, no support
promise, and security fixes land when I can make them. If you use a friend's server, you trust that friend: they hold your data,
and the install command comes from their server.

![The Overview of the demo character Nibbet, level 34: behind the route at 2.2 times Polestar's route time, the next level in 2h 24m of play, 91% of guide steps in order, the next moves, and time per level against the route](docs/images/overview.webp)

**[Live demo, the full page](https://sam-dumont.github.io/wow-mcp/demo/friends/)**: Nibbet-Gearholt,
an invented Gnome priest, as his friends see him: the Overview with what to do next, the gear
advice, the journal and the timeline. Demo data, no install. His
**[public profile](https://sam-dumont.github.io/wow-mcp/demo/)** is the cut-down page anyone
with the address sees: his card, gear, climb and dungeons.

## Three ways in

1. **[Join a friend's server](docs/players/getting-started.md)**: someone runs it for you and
   sent you a link. Nothing from this repository needed.
2. **[Try the demo](docs/self-host/demo.md)**: Docker and five made-up characters on your
   computer, about 10 minutes, no game needed.
3. **[Track my character](docs/install.md)**: run the server, add the addons and the small
   program on the computer you play on.

Then, for your AI: **[Connect your AI](docs/ai/connect.md)**. Everything else is in
**[the manual](docs/README.md)**, also on the [docs site](https://sam-dumont.github.io/wow-mcp/).

## The demo in two commands

With [Docker](https://docs.docker.com/get-docker/) running and logged in to Docker's hardened
images once (`docker login dhi.io`, a free Docker Hub account), from the top of a copy of
this repository (Mac, Linux, or Git Bash on Windows):

```sh
cd deploy/compose && export INGEST_PASSWORD=demo-password-only
docker compose up -d --build ui && docker compose exec -T ui python - --vl http://victorialogs:9428 < ../../dev/seed.py
```

The first run builds the image, a minute or two. It ends with
`<N> events into http://victorialogs:9428`, a few thousand. Open <http://127.0.0.1:8080/>: five
made-up characters, each with a DEMO DATA banner, and Testchar-Demo at level 32. `docker compose down -v` removes it all.
PowerShell, a port already taken, or the sites for friends: [Try the demo](docs/self-host/demo.md).

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
  [Privacy tiers](docs/explanation/privacy.md).
- **Your assistant does the thinking**, in your own account. The server hands it data and
  keeps a finding only once its query re-runs to the numbers the assistant saw. Every number
  the query measures is written into the finding's text by the server, from the result; any
  other number (a level, a top 3) has to be declared with its kind, and a level or a top N has
  to be in the query. You can inspect the query; matching it doesn't make the reading right.
  [Ask about your character](docs/ai/asking.md).
- **Unless the host turns on the server's own analysis.** Off until the host configures an AI
  provider (Anthropic, OpenAI or any OpenAI-compatible endpoint). Then the server runs the play
  analysis itself for each character with 10 hours of play since its last refresh (the host can
  pick another number). What the analysis reads about that character goes to that provider,
  never Battle.net data. Your own AI never starts a server run. [What's shared](docs/players/privacy.md),
  [Server-side analysis](docs/self-host/server-analysis.md).
- **Known addons only.** It reads [Polestar](https://github.com/fitzforge/polestar) and other
  well-known addons; it never asks you to install one of its own. [Addons](docs/reference/addons.md).
- **Small.** One player fits in 2 vCPU and 2 GiB of RAM; a year of events takes a few MiB.
  [Sizing](docs/self-host/sizing.md).

## The manual

| You are | Start here |
|---|---|
| A player a friend invited | [Join a friend's server](docs/players/getting-started.md), then [your page at a glance](docs/players/your-page.md) and [something's wrong](docs/players/troubleshooting.md) |
| Running your own server | [Try the demo](docs/self-host/demo.md), [install it](docs/install.md), then [run it on a server](docs/self-host/production.md) |
| Using an AI assistant | [Connect your AI](docs/ai/connect.md), [ask about your character](docs/ai/asking.md), [every tool](docs/reference/mcp-tools.md) |
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
the AI, and findings checked against the data.

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
