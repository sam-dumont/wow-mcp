# Quick start

**For:** anyone trying wow-stats before hosting it. **At the end:** you've asked the demo how
long Nibbet spent in Blackfathom Deeps. Allow about 10 minutes. If a friend already hosts wow-stats, [join their server](players/getting-started.md).

## 1. Start the demo

Start [Docker](https://docs.docker.com/get-docker/) and extract the
[release v{{app_version}} kit](https://github.com/sam-dumont/wow-mcp/archive/refs/tags/v{{app_version}}.zip).
The kit supplies the demo characters. The commands pull the matching public image from GitHub Container Registry. No Docker Hub login needed.

In the extracted folder, run these commands on Mac, Linux or Git Bash on Windows.

```sh
cd deploy/compose
files=compose.yaml
if [ -f compose.demo.yaml ]; then files=compose.yaml,compose.demo.yaml; fi
printf '%s\n' COMPOSE_PROJECT_NAME=wow-stats-demo COMPOSE_PATH_SEPARATOR=, "COMPOSE_FILE=$files" \
  INGEST_PASSWORD=demo-password-only 'WOW_STATS_IMAGE={{release_image}}' > .env.demo
docker compose --env-file .env.demo pull ui
docker compose --env-file .env.demo up -d --no-build --wait ui
docker compose --env-file .env.demo run --rm demo
```

Open <http://127.0.0.1:8080/>. Nibbet, a made-up level 46 Gnome priest, has a DEMO DATA banner.
The v0.13.0 kit contains Nibbet alone. Kits with `compose.demo.yaml` also load the other eight
classes, their models and simulated armory profiles. The demo keeps its own Docker volumes.

For PowerShell, ports, or the nine-character demo from a development checkout, see [Try the demo](self-host/demo.md).

## 2. Get a first answer

The page and the MCP tools read the same numbers. With
[Claude Code](https://docs.anthropic.com/en/docs/claude-code), still in `deploy/compose`:

```sh
claude mcp add wow-stats -- docker compose --env-file "$PWD/.env.demo" -f "$PWD/compose.yaml" exec -T ui wow-stats mcp-stdio
```

Then ask Claude Code: "With wow-stats, which level took Nibbet-Gearholt longest, and how long
was Nibbet's Blackfathom Deeps run?" It calls `levels` and `dungeons` and comes back with level 32,
about 9 hours, and 83 minutes in Blackfathom Deeps. The tools computed those; the model only
wrote the sentence.

<details>
<summary>No AI client? Call a tool by hand</summary>

The server speaks plain JSON-RPC on `mcp-stdio`. This asks `levels` for levels 30 to 34 and
prints the answer, with no model anywhere (the `sleep` keeps the pipe open until it comes
back). Level 32 comes back as `9h00m`:

```sh
{ printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"sh","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"levels","arguments":{"char":"Nibbet-Gearholt","from_level":30,"to_level":34}}}'; sleep 10; } \
  | docker compose --env-file .env.demo exec -T ui wow-stats mcp-stdio | tail -1
```

</details>

Every tool: [Tools and prompts](reference/mcp-tools.md). What the main ones return on the
demo: [A session, recorded](../site/pages/mcp-session.md).

`docker compose --env-file .env.demo --profile demo down -v` removes the demo and its volumes.
`claude mcp remove wow-stats` removes the Claude Code entry.

## 3. Your own character

Three parts, an hour the first time, most of it downloads:
[Install it for real](install.md).

1. **The addons**: [Polestar](https://github.com/fitzforge/polestar) and its Recorder, plus
   any optional ones.
2. **The server**: the same `docker compose` kit, without the demo.
3. **The shipper**: a small program on the computer you play on, which sends what the addons
   saved every 5 minutes.

Then [Connect your AI](ai/connect.md) to your server's `/mcp` address. A friend already runs a
server for you? Skip all of this: [Join a friend's server](players/getting-started.md).
