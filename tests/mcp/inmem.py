"""The FastMCP server in memory (fastmcp.Client(server)): the whole MCP
path, middleware included, without HTTP or a login."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from wowstats.mcp import server, tools


def build(ctx: tools.Ctx):
    return server.build_server(server.Who(base=ctx))


def listed(ctx: tools.Ctx) -> list[str]:
    async def go():
        async with Client(build(ctx)) as c:
            return [t.name for t in await c.list_tools()]

    return asyncio.run(go())


def call(ctx: tools.Ctx, name: str, args: dict | None = None):
    """The CallToolResult (is_error, content, structured_content)."""

    async def go():
        async with Client(build(ctx)) as c:
            return await c.call_tool(name, args or {}, raise_on_error=False)

    return asyncio.run(go())
