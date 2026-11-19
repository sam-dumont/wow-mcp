# Connect your AI

**For:** players and self-hosters who use Claude, ChatGPT or another AI assistant. **At the
end:** your assistant answers questions about your character from your own data.

wow-stats speaks MCP (Model Context Protocol: the standard way an AI assistant calls outside
tools). You add the server's MCP address to your assistant once, log in, and from then on it
can read your characters' history and plans when you ask about them. No model runs on the
server: your assistant does the thinking, in your own account, at no extra cost beyond your
plan.

## What you need

- **The server's MCP address.** It's the server's address plus `/mcp`, for example
  `https://wow.example.org/mcp`. On a friend's server, the start page and the help page show
  it.
- **An assistant that takes custom MCP servers.** Every client below does. Some plans limit
  custom connectors: check yours.
- **Your login for the server.** The assistant opens your browser once to log in, the same
  account as your page.

Running the server yourself? It needs a public HTTPS address and a login provider first:
[Set up the login for your AI](../self-host/oidc.md). On the computer that runs the server
there's a shortcut with no login: [On the server's own computer](#on-the-servers-own-computer).

## Pick your client

The examples use `https://wow.example.org/mcp`: put your server's address instead.

<!-- generated:ai-clients -->
<!-- From wowstats/mcp/helppage.py (CLIENTS): edit it there, then run tools/manual/generate.py. -->

### Claude (claude.ai, desktop, phone)

1. Settings → Connectors → Add custom connector.
2. Paste the URL, leave the client id and secret empty.
3. Connect, then log in when your browser asks. The phone app uses connectors added on the web.

```text
https://wow.example.org/mcp
```

**It worked when:** The connector shows Connected, and a chat's tools menu lists wow.

### Claude Code

1. Run this in a terminal.
2. In Claude Code, /mcp → wow → Authenticate, and log in in your browser.

```sh
claude mcp add --transport http wow https://wow.example.org/mcp
```

**It worked when:** /mcp shows wow as connected with its tools.

### ChatGPT

1. Settings → Connectors, turn on developer mode, then Create.
2. Paste the URL, pick OAuth, and log in when asked.

```text
https://wow.example.org/mcp
```

**It worked when:** The connector shows as connected. Pick it for a chat.

### Codex (CLI and IDE)

1. Add the server, then log in: Codex opens your browser.

```sh
codex mcp add wow --url https://wow.example.org/mcp && codex mcp login wow
```

**It worked when:** codex mcp list shows wow.

### opencode

1. Add this to opencode.json (next to anything already under "mcp").
2. opencode opens your browser to log in the first time it uses the server.

```json
{"mcp": {"wow": {"type": "remote", "url": "https://wow.example.org/mcp"}}}
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

### VS Code (Copilot Chat)

1. Add this to .vscode/mcp.json, or to your user mcp.json for every workspace.
2. Start the server from the file; VS Code opens your browser to log in.

```json
{"servers": {"wow": {"type": "http", "url": "https://wow.example.org/mcp"}}}
```

**It worked when:** The server shows Running in mcp.json. Use agent mode.

### Copilot CLI

1. In the CLI, /mcp add, pick HTTP and paste the URL.
2. Log in when your browser opens.

```text
https://wow.example.org/mcp
```

> Not checked end to end yet: the exact path Copilot CLI's login comes back on. If it's refused, say so to whoever runs this site.

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

### Cursor

1. Add this to `~/.cursor/mcp.json` (or .cursor/mcp.json in a project).
2. Cursor shows a Login button next to the server: click it and log in.

```json
{"mcpServers": {"wow": {"url": "https://wow.example.org/mcp"}}}
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

### Gemini CLI

1. Add this to settings.json (`~/.gemini/settings.json`).
2. Keep scopes set to openid: without it Gemini asks for every scope the server advertises.
3. Log in when your browser opens.

```json
{"mcpServers": {"wow": {"httpUrl": "https://wow.example.org/mcp", "oauth": {"enabled": true, "scopes": ["openid"]}}}}
```

**It worked when:** /mcp lists wow with its tools.

### Zed

1. Add this to your Zed settings, under context_servers.
2. Log in when Zed opens your browser.

```json
{"context_servers": {"wow": {"url": "https://wow.example.org/mcp"}}}
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

### Goose

1. Extensions → Add → Remote (Streamable HTTP).
2. Paste the URL, then log in when asked.

```text
https://wow.example.org/mcp
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

### pi

1. pi has no MCP of its own: install the pi-mcp-adapter extension (7.0.1 was checked).
2. Add the URL as a server in the adapter, then log in when your browser opens.

```text
https://wow.example.org/mcp
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

### LM Studio

1. Add this to LM Studio's mcp.json (Program → Install → Edit mcp.json).
2. Log in when your browser opens.

```json
{"mcpServers": {"wow": {"url": "https://wow.example.org/mcp"}}}
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

### Windsurf (Devin)

1. Add this to mcp_config.json.
2. Log in when your browser opens.

```json
{"mcpServers": {"wow": {"serverUrl": "https://wow.example.org/mcp"}}}
```

> Not checked yet: where Windsurf sends your browser back after the login. If it's refused, say so to whoever runs this site.

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

<!-- /generated:ai-clients -->

## On the server's own computer

Running the compose kit and an MCP client on the same computer: the client can start the
tools itself over stdin and stdout. No port, no login, nothing anyone else can reach. It reads
the same data and keeps plans in the same place as the HTTPS endpoint.

Claude Code, from the kit's `deploy/compose` folder:

```sh
claude mcp add wow-stats -- docker compose -f "$PWD/compose.yaml" exec -T ui wow-stats mcp-stdio
```

Any other client that starts a local command: the command is `docker`, the arguments
`compose -f /path/to/deploy/compose/compose.yaml exec -T ui wow-stats mcp-stdio`.

This doesn't work on a shared install (several people): there the server must know who is
asking, so use the HTTPS address.

## When it doesn't work

<!-- generated:ai-trouble -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

### "Client Not Registered" in Claude

Claude kept a login from before the server changed.

**Fix:** Remove the wow-stats connector in Claude, add it again with the same address, and connect once more.

### The AI login keeps coming back

A login request is good for a few minutes and once. A login tab left open, or a second login started in another tab, sends you round again. A sign-in page that keeps returning means you're signed in there with an account that has no wow-stats access.

**Fix:** Close every login tab, start the connection again from the client and sign in straight away. Sign-in page loop: sign out there (or use a private window) and sign in with the account you were sent.

### My AI doesn't see the tools

Added is not enabled: most clients connect a server but leave it off for a chat, or wait for you to log in.

**Fix:** claude.ai: turn the connector on for the chat (the tools menu under the message box). Claude Code: `/mcp` must show wow as connected, not "needs authentication". ChatGPT: developer mode on and the connector picked for the chat. VS Code: start the server from mcp.json and use agent mode. Then name it: "use wow-stats to plan my next 2 hours".

### Logged out after a server update

An update can change the server's signing key or its tool list, and a login unused for 30 days expires. Calls then fail with 401 or "invalid token", and new tools stay hidden: clients keep the tool list for the session.

**Fix:** Log in again. claude.ai: Settings, Connectors, wow, Disconnect, then Connect. Claude Code: `/mcp`, wow, Re-authenticate. Then start a new chat to get the new tools.

<!-- /generated:ai-trouble -->

## Next

[Ask about your character](asking.md): what to ask, what the assistant can see, and how the
server checks its numbers.
