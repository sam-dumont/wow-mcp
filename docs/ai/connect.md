# Connect your AI

**For:** anyone with a wow-stats page and Claude, ChatGPT or another AI assistant. **At the
end:** your assistant answers questions about your character from your own data.

You add one address to your assistant, log in once, and from then on it reads your characters'
history and plans when you ask. Your assistant does the thinking, in your own account, at no
extra cost beyond your plan. (A host can also turn on the server's own analysis, with an AI
provider they pay for: [What's shared](../players/privacy.md).) (Under the hood that's MCP, the
standard way an assistant calls outside tools.)

## In three steps

<!-- TODO(media): a 30-second clip of these three steps in claude.ai on the demo (connect-light.mp4 / connect-dark.mp4), linked here like the other clips. -->

1. **Copy your address**: your start page shows it, the server's address plus `/mcp`.
2. **Add it to your assistant** as a custom connector (claude.ai: Settings → Connectors → Add
   custom connector; every other client is [below](#pick-your-client)), and log in once with
   the account your page uses.
3. **Ask**: "With wow-stats, plan my next 2 hours, no dungeons." The answer names your
   character, your next guide steps and where to log out. More to ask:
   [Ask about your character](asking.md).

What your assistant reads goes to your AI provider, like anything in your chat. wow-stats
never sees the conversation. [What the assistant can see](asking.md#what-the-assistant-can-see).

You need:

- **The address**: the server's address plus `/mcp`, like `https://wow.example.org/mcp`. Your
  start page shows it.
- **An assistant that takes custom connectors.** Every client below does; some plans limit
  them.
- **Your login**: the assistant opens your browser once, for the same account as your page.
  Press **Allow**, and the login page asks which account to use: pick the one your page uses.
  If your page signs you in with Battle.net, press **Sign in with Battle.net** instead: it goes
  straight to Battle.net.

## Pick your client

Running the server yourself: claude.ai and ChatGPT need a public HTTPS address and a login
provider first, [Set up the login for your AI](../self-host/oidc.md). On the server's own
computer, Claude Code needs neither: [below](#on-the-servers-own-computer).

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

### Other clients

<details>
<summary>Claude Code</summary>

1. Run this in a terminal.
2. In Claude Code, /mcp → wow → Authenticate, and log in in your browser.

```sh
claude mcp add --transport http wow https://wow.example.org/mcp
```

**It worked when:** /mcp shows wow as connected with its tools.

</details>

<details>
<summary>ChatGPT</summary>

1. Settings → Connectors, turn on developer mode, then Create.
2. Paste the URL, pick OAuth, and log in when asked.

```text
https://wow.example.org/mcp
```

**It worked when:** The connector shows as connected. Pick it for a chat.

</details>

<details>
<summary>Codex (CLI and IDE)</summary>

1. Add the server, then log in: Codex opens your browser.

```sh
codex mcp add wow --url https://wow.example.org/mcp && codex mcp login wow
```

**It worked when:** codex mcp list shows wow.

</details>

<details>
<summary>opencode</summary>

1. Add this to opencode.json (next to anything already under "mcp").
2. opencode opens your browser to log in the first time it uses the server.

```json
{"mcp": {"wow": {"type": "remote", "url": "https://wow.example.org/mcp"}}}
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

</details>

<details>
<summary>VS Code (Copilot Chat)</summary>

1. Add this to .vscode/mcp.json, or to your user mcp.json for every workspace.
2. Start the server from the file; VS Code opens your browser to log in.

```json
{"servers": {"wow": {"type": "http", "url": "https://wow.example.org/mcp"}}}
```

**It worked when:** The server shows Running in mcp.json. Use agent mode.

</details>

<details>
<summary>Copilot CLI</summary>

1. In the CLI, /mcp add, pick HTTP and paste the URL.
2. Log in when your browser opens.

```text
https://wow.example.org/mcp
```

> Not checked end to end yet: the exact path Copilot CLI's login comes back on. If it's refused, tell the owner.

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

</details>

<details>
<summary>Cursor</summary>

1. Add this to `~/.cursor/mcp.json` (or .cursor/mcp.json in a project).
2. Cursor shows a Login button next to the server: click it and log in.

```json
{"mcpServers": {"wow": {"url": "https://wow.example.org/mcp"}}}
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

</details>

<details>
<summary>Gemini CLI</summary>

1. Add this to settings.json (`~/.gemini/settings.json`).
2. Keep scopes set to openid: without it Gemini asks for every scope the server advertises.
3. Log in when your browser opens.

```json
{"mcpServers": {"wow": {"httpUrl": "https://wow.example.org/mcp", "oauth": {"enabled": true, "scopes": ["openid"]}}}}
```

**It worked when:** /mcp lists wow with its tools.

</details>

<details>
<summary>Zed</summary>

1. Add this to your Zed settings, under context_servers.
2. Log in when Zed opens your browser.

```json
{"context_servers": {"wow": {"url": "https://wow.example.org/mcp"}}}
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

</details>

<details>
<summary>Goose</summary>

1. Extensions → Add → Remote (Streamable HTTP).
2. Paste the URL, then log in when asked.

```text
https://wow.example.org/mcp
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

</details>

<details>
<summary>pi</summary>

1. pi has no MCP of its own: install the pi-mcp-adapter extension (7.0.1 was checked).
2. Add the URL as a server in the adapter, then log in when your browser opens.

```text
https://wow.example.org/mcp
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

</details>

<details>
<summary>LM Studio</summary>

1. Add this to LM Studio's mcp.json (Program → Install → Edit mcp.json).
2. Log in when your browser opens.

```json
{"mcpServers": {"wow": {"url": "https://wow.example.org/mcp"}}}
```

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

</details>

<details>
<summary>Windsurf (Devin)</summary>

1. Add this to mcp_config.json.
2. Log in when your browser opens.

```json
{"mcpServers": {"wow": {"serverUrl": "https://wow.example.org/mcp"}}}
```

> Not checked yet: where Windsurf sends your browser back after the login. If it's refused, tell the owner.

**It worked when:** Ask "use wow-stats: what's my character doing?": the answer names your character and its level.

</details>

<!-- /generated:ai-clients -->

## On the server's own computer

<details>
<summary>Claude Code next to the compose kit: no port, no login</summary>

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

</details>

## When it doesn't work

<!-- generated:ai-trouble -->
<!-- From docs/videos/steps.json: edit it there, then run tools/manual/generate.py. -->

### "Client Not Registered" in Claude

<details>
<summary>Why, and the fix</summary>

Claude kept a login from before the server changed.

**Fix:** Remove the wow-stats connector in Claude, add it again with the same address, and connect once more.

</details>

### The AI login keeps coming back

<details>
<summary>Why, and the fix</summary>

A login request is good for a few minutes and once. A login tab left open, or a second login started in another tab, sends you round again. A sign-in page that keeps returning means you're signed in there with an account that has no wow-stats access.

**Fix:** Close every login tab, start the connection again from the client and sign in straight away. Sign-in page loop: sign out there (or use a private window) and sign in with the account your page uses.

</details>

### My AI doesn't see the tools

<details>
<summary>Why, and the fix</summary>

Added is not enabled: most clients connect a server but leave it off for a chat, or wait for you to log in.

**Fix:** claude.ai: turn the connector on for the chat (the tools menu under the message box). Claude Code: `/mcp` must show wow as connected, not "needs authentication". ChatGPT: developer mode on and the connector picked for the chat. VS Code: start the server from mcp.json and use agent mode. Then name it: "use wow-stats to plan my next 2 hours".

</details>

### Logged out after a server update

<details>
<summary>Why, and the fix</summary>

An update can change the server's signing key or its tool list, and a login unused for 30 days expires. Calls then fail with 401 or "invalid token", and new tools stay hidden: clients keep the tool list for the session.

**Fix:** Log in again. claude.ai: Settings, Connectors, wow, Disconnect, then Connect. Claude Code: `/mcp`, wow, Re-authenticate. Then start a new chat to get the new tools.

</details>

<!-- /generated:ai-trouble -->

## Next

[Ask about your character](asking.md): what to ask, what the assistant can see, and how the
server checks its numbers.
