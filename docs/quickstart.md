# Quick start

**For:** anyone thinking of hosting wow-stats who wants to see it answer first (a friend
already runs it for you? [Join a friend's server](players/getting-started.md) instead). **At the end:** the
nine demo characters run on your computer, a tool has answered a question about Nibbet, and you know
the next page for your own character. About 10 minutes.

## 1. Start the demo

You need [Docker](https://docs.docker.com/get-docker/) running (Docker Desktop on Mac and
Windows) and a copy of this repository: `git clone` it, or unpack a release's source zip.

From the top of your copy (Mac, Linux, or Git Bash on Windows):

```sh
cd deploy/compose
test -f .env || printf '%s\n' INGEST_PASSWORD=demo-password-only > .env
docker compose -f compose.yaml -f compose.demo.yaml up -d --build ui
docker compose -f compose.yaml -f compose.demo.yaml run --rm demo
```

The first run builds the demo image from your checkout. The seed prints the event count for
nine characters, then writes their simulated armory data to the demo’s own database.

Open <http://127.0.0.1:8080/>: Nibbet, a made-up Gnome priest at level 46, under a DEMO DATA
banner. Use the character selector to try the other eight classes. For PowerShell, ports and the sites for friends, see
[Try it at home](self-host/demo.md).

## 2. Get a first answer

The page and the MCP tools read the same numbers. With
[Claude Code](https://docs.anthropic.com/en/docs/claude-code), still in `deploy/compose`:

```sh
claude mcp add wow-stats -- docker compose -f "$PWD/compose.yaml" -f "$PWD/compose.demo.yaml" exec -T ui wow-stats mcp-stdio
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
  | docker compose -f compose.yaml -f compose.demo.yaml exec -T ui wow-stats mcp-stdio | tail -1
```

</details>

Every tool: [Tools and prompts](reference/mcp-tools.md). What the main ones return on the
demo: [A session, recorded](../site/pages/mcp-session.md).

`docker compose -f compose.yaml -f compose.demo.yaml down -v` removes the demo, `claude mcp remove wow-stats` the Claude Code entry.

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
