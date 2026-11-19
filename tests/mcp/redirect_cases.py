"""Redirects the MCP clients really use (read from each client's own source
on 2026-11-18): (client, redirect, accepted).
The first 19 are what the clients really send or register; the last 8 must
stay refused."""

CASES = [
 ("claude.ai", "https://claude.ai/api/mcp/auth_callback", True),
 ("claude code", "http://localhost:54123/callback", True),
 ("chatgpt", "https://chatgpt.com/connector_platform_oauth_redirect", True),
 ("codex", "http://127.0.0.1:41234/callback", True),
 ("codex no-iss", "http://127.0.0.1:41234/callback/abcdef123456", True),
 ("opencode", "http://127.0.0.1:19876/mcp/oauth/callback", True),
 ("vscode", "http://127.0.0.1:33418/", True),
 ("vscode web", "https://vscode.dev/redirect", True),
 ("vscode dcr portless", "http://127.0.0.1/", True),
 ("insiders", "https://insiders.vscode.dev/redirect", True),
 ("copilot cli", "http://127.0.0.1:5555/callback", True),
 ("zed", "http://127.0.0.1:5555/callback", True),
 ("goose v6", "http://[::1]:5555/oauth_callback", True),
 ("cursor scheme", "cursor://anysphere.cursor-mcp/oauth/callback", True),
 ("cursor web", "https://www.cursor.com/agents/mcp/oauth/callback", True),
 ("cursor loop", "http://localhost:8787/callback", True),
 ("gemini", "http://localhost:7777/oauth/callback", True),
 ("lmstudio", "http://127.0.0.1:33389/mcp-oauth-callback", True),
 ("pi", "http://127.0.0.1:19876/callback", True),
 # must stay refused
 ("evil userinfo", "http://localhost@evil.com/cb", False),
 ("evil host", "https://evil.com/api/mcp/auth_callback", False),
 ("evil sub", "https://vscode.dev.evil.com/redirect", False),
 ("loopback lookalike", "http://localhost.evil.com/cb", False),
 ("https loopback", "https://127.0.0.1/cb", False),
 ("other path on vscode.dev", "https://vscode.dev/steal", False),
 ("other cursor scheme", "cursor://evil/oauth/callback", False),
 ("javascript", "javascript:alert(1)", False),
]
