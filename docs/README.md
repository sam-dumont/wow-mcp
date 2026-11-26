# The wow-stats manual

**For:** everyone: players, AI users, people who run a server, and contributors. **At the
end:** you've found the page for what you want to do.

New here? [How it works](explanation/how-it-works.md) says what you get and what runs where,
[the live demo](../site/pages/demo.md) shows it on a made-up priest, the
[questions and answers](faq.md) and the [glossary](glossary.md) cover the rest. How it came to
be: [How this was built](how-this-was-built.md).

## Play

A friend runs wow-stats and sent you a link. You're trusting that friend: they hold your
data, and the install command comes from their server.

1. [Join a friend's server](players/getting-started.md): the addons, one command, about
   5 minutes.
2. [Your page at a glance](players/your-page.md), then each part in detail:
   [Overview and next moves](players/overview.md), [Gear and upgrades](players/gear.md), [Your class's meta](players/meta.md),
   [Dungeons](players/dungeons.md), [Plan your next hours](players/plan.md),
   [Climb, timeline, journal](players/history.md).
3. [Something's wrong](players/troubleshooting.md): fixes by symptom.
4. [What's shared](players/privacy.md): what your friends and the public see.

## Your AI

The tools work out each answer from your data in code; your assistant picks them and writes
the reply.

1. [Connect your AI](ai/connect.md): Claude, ChatGPT, Claude Code and a dozen more clients.
2. [Tools and prompts](reference/mcp-tools.md): every tool your assistant can call.
3. [Semantic layer](reference/semantic-layer.md): the named metrics, what each one counts, and
   how to check an answer.
4. [Ask about your character](ai/asking.md): what to ask, the play analysis, and how the
   server checks the numbers.

## Host it

A hobby project: no warranty, no support promise (GPL-3.0), security fixes when I can make
them. Your friends trust you with their data and with the installer your server hands them.

Start here:

1. [The host guide](self-host/production.md): what you need, the three choices to make before
   your first event, and every step in order.
2. [Quick start](quickstart.md): the demo character on your computer and a first answer from
   your AI, 10 minutes. [Try it at home](self-host/demo.md) is the long version, with the sites
   for friends.
3. [Install it for real](install.md): the server, the addons and the shipper, for yourself.

Set it up:

- [Reverse proxy and HTTPS](self-host/reverse-proxy.md): Caddy, Traefik or nginx.
- [Share your page](self-host/sharing.md): the public site, the sealed friends' site, the
  character's pictures.
- [The login for your AI](self-host/oidc.md): any OIDC provider, Keycloak as the example.
- [Add friends](self-host/friends.md): several players on one server.
- [Battle.net data](self-host/battlenet.md): your own API client and the 30-day rule.
- [Server-side analysis](self-host/server-analysis.md): the server writes "How you play" itself,
  with Anthropic, OpenAI or any OpenAI-compatible endpoint, on a timer and a monthly budget.
- [How I run it](self-host/how-i-run-it.md): my own install as a worked example, with friends
  signing in through Battle.net.
- [Without Docker Compose](deploy.md): Kubernetes or plain containers.

Keep it running:

- [When it stops](self-host/troubleshooting.md): error messages and their fixes.
- [Admin overview](self-host/admin.md): who uses the install, when, and whether its parts answer.
- [Back up and restore](self-host/backup.md).
- [Upgrade](self-host/upgrade.md): between versions, and switching event stores.
- [Monitor it](self-host/monitoring.md).
- [Harden it](self-host/hardening.md).
- [Uninstall](self-host/uninstall.md).

Look it up:

- [Environment variables](reference/environment.md): every setting, which program reads it,
  its default.
- [HTTP endpoints](reference/http-endpoints.md): every path, its port and its login.
- [Sizing](self-host/sizing.md): measured time, memory and disk.
- [The shipper](shipper.md): its files, flags and schedule.
- [The shipper's events](contract.md): every event and field it sends.

## Game data

- [Addons it reads](reference/addons.md): every addon and what it adds to the page.
- [Gear upgrades](gear-reference.md), [Route times](route-reference.md),
  [Forever reference](forever-reference.md): the game data the kit ships.
- [Gear source review](gear-source-review.md) and [recipe acquisition audit](recipe-acquisition-audit.md) give the evidence behind individual drops, trainer ranks and patterns.
- [Dungeon kill XP](dungeon-xp.md): how a dungeon you haven't run gets its kill XP estimate.
- [The knowledge base](kb.md): where the game rules your assistant quotes come from.

## Under the hood

- [Architecture](architecture.md): the parts and how an event travels.
- [MCP and the play analysis](mcp.md): the tools, plans, analysis and login, inside.
- [Privacy tiers](explanation/privacy.md): what's collected, and who sees what.
- [Where the data comes from](explanation/data-sources.md): Blizzard's API first, an addon only when there's no other way.
- [What the pipeline guarantees](guarantees.md): the promises from game file to answer, each with the test that proves it.
- [Security notes](security.md): what an install has to get right, and the findings left open
  on purpose.

## Project and contributing

- [How this was built](how-this-was-built.md): the six days from a levelling question to this.
- [Changelog](../CHANGELOG.md) and the [security policy](../SECURITY.md).
- [How to contribute](../CONTRIBUTING.md) and [Development and releases](development.md).
- [Evaluating the analysis](evaluation.md): a real AI on synthetic histories with known effects, graded.
