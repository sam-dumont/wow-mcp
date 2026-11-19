# The wow-stats manual

**For:** everyone: players, people who run a server, AI users and contributors. **At the
end:** you've found the page for what you want to do.

## Pick your path

| You are | Start here | Then |
|---|---|---|
| **A player** a friend invited | [Join a friend's server](players/getting-started.md) | [Your page, tab by tab](players/your-page.md), [Something's wrong](players/troubleshooting.md), [What's shared](players/privacy.md) |
| **Running your own server** | [Try the demo](self-host/demo.md), then [Install it for real](install.md) | [Run it on a server](self-host/production.md) |
| **Using an AI assistant** | [Connect your AI](ai/connect.md) | [Ask about your character](ai/asking.md), [MCP tools and prompts](reference/mcp-tools.md) |
| **Contributing** | [Development](development.md) | [Architecture](architecture.md), [CONTRIBUTING.md](../CONTRIBUTING.md), [SECURITY.md](../SECURITY.md) |

## Tutorials: learn by doing

- [Try the demo](self-host/demo.md): five made-up characters on your computer in 10 minutes.
- [Install it for real](install.md): the server, the addons and the shipper, for yourself.
- [Join a friend's server](players/getting-started.md): the player's install, about 15 minutes.

## How-to guides: get one thing done

For players:

- [Something's wrong](players/troubleshooting.md): fixes by symptom.
- [Connect your AI](ai/connect.md): Claude, ChatGPT, Claude Code and a dozen more clients.
- [Ask about your character](ai/asking.md): what to ask, and how numbers are checked.

For self-hosters:

- [Run it on a server](self-host/production.md): the order to set things up.
- [Put it behind a reverse proxy](self-host/reverse-proxy.md): HTTPS with Caddy, Traefik or nginx.
- [Set up the login for your AI](self-host/oidc.md): any OIDC provider, with Keycloak as the worked example.
- [Share your page](self-host/sharing.md): the public site, the sealed friends' site, the
  help page, pictures of past levels.
- [Add friends](self-host/friends.md): several players on one server.
- [Battle.net data](self-host/battlenet.md): your own API client and the 30-day rule.
- [Back up and restore](self-host/backup.md).
- [Upgrade](self-host/upgrade.md).
- [Monitor it](self-host/monitoring.md).
- [Harden it](self-host/hardening.md).
- [Uninstall](self-host/uninstall.md).
- [Deploy without compose](deploy.md): Kubernetes or plain containers.

## Reference: look it up

- [Environment variables](reference/environment.md): every setting, which program reads it,
  its default.
- [MCP tools and prompts](reference/mcp-tools.md): every tool your AI can call.
- [Addons](reference/addons.md): every addon wow-stats reads.
- [HTTP endpoints](reference/http-endpoints.md): every path, its port and its login.
- [Sizing](self-host/sizing.md): measured time, memory and disk.
- [The shipper](shipper.md): its files, flags and schedule.
- [The event contract](contract.md): every event and field the shipper sends.
- [Route times](route-reference.md), [Gear upgrades](gear-reference.md),
  [Forever reference](forever-reference.md): the bundled game data.

## Explanation: how and why

- [Architecture](architecture.md): the parts and how an event travels.
- [Privacy tiers](explanation/privacy.md): what's collected, and who sees what.
- [Security notes](security.md): what an install has to get right, and the findings left open on purpose.
- [What's shared, for players](players/privacy.md): the same, in a player's words.
- [Your page, tab by tab](players/your-page.md): what each part of the page means.
- [MCP and the play analysis](mcp.md): the tools, plans, analysis and login, inside.
- [The knowledge base](kb.md): where game rules come from.
