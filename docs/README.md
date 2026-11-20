# The wow-stats manual

**For:** everyone: players, AI users, people who run a server, and contributors. **At the
end:** you've found the page for what you want to do.

New here? [How it works](explanation/how-it-works.md) in five minutes, the
[questions and answers](faq.md), or the [glossary](glossary.md) for a word you don't know.

## Play

A friend runs wow-stats and sent you a link.

1. [Join a friend's server](players/getting-started.md): the addons, one command, about
   15 minutes.
2. [Your page at a glance](players/your-page.md), then each part in detail:
   [Overview and next moves](players/overview.md), [Gear and upgrades](players/gear.md), [Your class's meta](players/meta.md),
   [Dungeons](players/dungeons.md), [Plan your next hours](players/plan.md),
   [Climb, timeline, journal](players/history.md).
3. [Something's wrong](players/troubleshooting.md): fixes by symptom.
4. [What's shared](players/privacy.md): what your friends and the public see.

## Connect your AI

1. [Connect your AI](ai/connect.md): Claude, ChatGPT, Claude Code and a dozen more clients.
2. [Ask about your character](ai/asking.md): what to ask, and how the server checks the
   numbers.
3. [Tools and prompts](reference/mcp-tools.md): every tool your assistant can call.
4. [Semantic layer](reference/semantic-layer.md): the named metrics, what each one counts, and
   how to check an answer.

## Self-host

Start here:

1. [Try it at home](self-host/demo.md): five made-up characters on your computer, 10 minutes.
2. [Install it for real](install.md): the server, the addons and the shipper, for yourself.

Set it up:

- [Run it on a server](self-host/production.md): the order to set things up.
- [Reverse proxy and HTTPS](self-host/reverse-proxy.md): Caddy, Traefik or nginx.
- [The login for your AI](self-host/oidc.md): any OIDC provider, Keycloak as the example.
- [Share your page](self-host/sharing.md): the public site, the sealed friends' site, the
  pictures of past levels.
- [Add friends](self-host/friends.md): several players on one server.
- [How I run it](self-host/how-i-run-it.md): my own install as a worked example, with friends
  signing in through Battle.net.
- [Admin overview](self-host/admin.md): who uses the install, when, and whether its parts answer.
- [Battle.net data](self-host/battlenet.md): your own API client and the 30-day rule.
- [Without Docker Compose](deploy.md): Kubernetes or plain containers.

Keep it running:

- [When it stops](self-host/troubleshooting.md): error messages and their fixes.
- [Back up and restore](self-host/backup.md).
- [Upgrade](self-host/upgrade.md).
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
- [The knowledge base](kb.md): where the game rules your assistant quotes come from.

## Under the hood

- [Architecture](architecture.md): the parts and how an event travels.
- [MCP and the play analysis](mcp.md): the tools, plans, analysis and login, inside.
- [Privacy tiers](explanation/privacy.md): what's collected, and who sees what.
- [Where the data comes from](explanation/data-sources.md): Blizzard's API first, an addon only when there's no other way.
- [Security notes](security.md): what an install has to get right, and the findings left open
  on purpose.

## Project and contributing

- [Changelog](../CHANGELOG.md) and the [security policy](../SECURITY.md).
- [How to contribute](../CONTRIBUTING.md) and [Development and releases](development.md).
