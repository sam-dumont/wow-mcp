"""`wow-stats mcp-stdio`: the same tools over stdin/stdout, for a client on
the same machine (docs/mcp.md, the compose kit's local connection)."""

import asyncio
import os
import subprocess
import sys

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StdioTransport  # noqa: E402

from wowstats.mcp import server  # noqa: E402


def _env(tmp_path, **more) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MCP_", "OIDC_", "WOW_STATE"))}
    return env | {"VL_URL": "http://127.0.0.1:9", "ANALYSIS_STATE_DIR": str(tmp_path / "state"),
                  "FASTMCP_CHECK_FOR_UPDATES": "off"} | more


def test_lists_the_same_tools_as_the_http_endpoint(tmp_path):
    async def go():
        t = StdioTransport(command=sys.executable, args=["-m", "wowstats", "mcp-stdio"], env=_env(tmp_path))
        async with Client(t) as c:
            return sorted(x.name for x in await c.list_tools())

    want = sorted(t.name for t in server.listing()[0])
    assert asyncio.run(go()) == want and "plan_session" in want


@pytest.mark.parametrize("more, why", [({"WOW_STATE_DIR": "/nonexistent"}, "several people"),
                                       ({"VIEW": "public"}, "VIEW=public")])
def test_refuses_what_it_cant_serve(tmp_path, more, why):
    r = subprocess.run([sys.executable, "-m", "wowstats", "mcp-stdio"], env=_env(tmp_path, **more),
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 2 and why in r.stderr and r.stdout == ""
