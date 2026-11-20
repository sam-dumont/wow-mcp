"""Records site/pages/mcp-session.md: the MCP server's real answers about
the demo character, for the docs site (the MCP can't run on GitHub Pages).

    site/scripts/build-demo.sh --mcp-session [YYYY-MM-DD]

build-demo.sh fills its throwaway VictoriaLogs first, then runs this with
its address. The tools are called the way an assistant calls them: through
`wow-stats mcp-stdio`, the same server and tools as the HTTP endpoint, with
no login (stdio is for a client on the same machine). The questions are
written here; the answers are whatever the server says, with long lists cut
to their first items so the page stays readable. No model is involved: the
page shows what an assistant gets, not what it would write back.

Rerun it after a change to a tool's answer, read the diff, commit both.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "fixtures"))

from generate import DEMO  # noqa: E402

OUT = ROOT / "site" / "pages" / "mcp-session.md"
LIST_MAX = 3
KEYS_MAX = 15
TEXT_MAX = 400

# (what the player asks, the tool an assistant picks, its arguments)
ASKS = [
    ("Where does Nibbet stand?", "character_summary", {}),
    ("Besides following Polestar, what should I do now?", "next_moves", {}),
    ("Is a dungeon worth it at my level?", "dungeon_options", {}),
    ("Plan my next two hours.", "plan_session", {"hours": 2}),
    ("Am I doing anything stupid?", "sanity_check", {}),
]


def trim(v, depth: int = 0):
    """Long lists to their first items (and a count of the rest), long
    strings to their start: the shape stays, the page stays short."""
    if isinstance(v, dict):
        items = list(v.items())
        out = {k: trim(x, depth + 1) for k, x in (items[:KEYS_MAX] if depth else items)}
        if depth and len(items) > KEYS_MAX:
            out["..."] = f"{len(items) - KEYS_MAX} more keys"
        return out
    if isinstance(v, list):
        head = [trim(x, depth + 1) for x in v[:LIST_MAX]]
        return head + ([f"... {len(v) - LIST_MAX} more"] if len(v) > LIST_MAX else [])
    if isinstance(v, str) and len(v) > TEXT_MAX:
        return v[:TEXT_MAX] + " ..."
    return v


FOLD = 20  # answers longer than this many lines start folded: a phone scrolls past them


def reply(lead: str, body: str) -> str:
    """The server's answer: a long one folded in <details>, its size in the summary."""
    block = f"```json\n{body}\n```\n"
    n = body.count("\n") + 1
    if n <= FOLD:
        return f"{lead}:\n\n{block}"
    return f"<details>\n<summary>{lead} ({n} lines of JSON)</summary>\n\n{block}\n</details>\n"


def answer(res) -> object:
    if res.structured_content is not None:
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    text = "".join(getattr(c, "text", "") for c in res.content)
    try:
        return json.loads(text)
    except ValueError:
        return text


async def record(vl: str) -> str:
    with tempfile.TemporaryDirectory() as state:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MCP_", "OIDC_", "WOW_STATE"))}
        # VM_URL: the demo's Battle.net series (build-demo.sh), so the
        # answers carry his talents and look as the pages do.
        env |= {"VL_URL": vl, "VM_URL": os.environ.get("VM_URL", ""), "ANALYSIS_STATE_DIR": state,
                "FASTMCP_CHECK_FOR_UPDATES": "off",
                "DEFAULT_CHAR": DEMO}
        t = StdioTransport(command=sys.executable, args=["-m", "wowstats", "mcp-stdio"], env=env, cwd=str(ROOT))
        async with Client(t) as c:
            tools = sorted(x.name for x in await c.list_tools())
            parts = []
            for ask, tool, args in ASKS:
                args = {"char": DEMO, **args}
                res = await c.call_tool(tool, args, raise_on_error=False)
                got = answer(res)
                # A plan comes with its own Markdown draft: shown as the page
                # would, not as one long JSON string.
                md = got.pop("markdown", None) if isinstance(got, dict) else None
                body = json.dumps(trim(got), indent=1, ensure_ascii=False)
                parts.append(f"## {ask}\n\nThe assistant calls `{tool}`:\n\n```json\n{json.dumps(args)}\n```\n\n"
                             + reply('The server refuses' if res.is_error else 'The server answers', body))
                if md:
                    # Its headings as bold lines: they'd fill the page's own contents.
                    lines = [re.sub(r"^#+ +(.*)$", r"**\1**", ln) for ln in md.strip().splitlines()]
                    quoted = "\n".join("> " + ln if ln else ">" for ln in lines)
                    parts.append(f"Its `markdown` field, the draft the assistant starts from:\n\n{quoted}\n")
    return f"""# An MCP session, recorded

The MCP server can't run on GitHub Pages, so this page shows it instead: the
questions a player might ask about Nibbet, [the demo character](demo.md), the
tool their assistant picks for each, and what the server answers, from the
same data the demo pages show. Long lists are cut to their first {LIST_MAX}
items. The assistant's own reply isn't here: that part depends on your
model, the answers don't.

[Ask your assistant](../../docs/mcp.md) explains how to connect yours. The
server has {len(tools)} tools: {", ".join(f"`{t}`" for t in tools)}.

<!-- Written by site/scripts/mcp_session.py: rerun it, don't edit by hand. -->

{chr(10).join(parts)}"""


def main(argv: list[str]) -> int:
    OUT.write_text(asyncio.run(record(argv[0])), encoding="utf-8")
    print(f"mcp-session: wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
