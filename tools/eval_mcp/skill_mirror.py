"""The MCP check's skills: the server's skills, fetched the way an MCP client
fetches them (skills/list, then resources/read of every file the entry
lists, each checked against its sha256 and size), written out as Claude Code
filesystem skills (.claude/skills/<name>/...).

Why a mirror: Claude Code 2.1.332 loads skills from MCP servers only in an
interactive session. `claude -p` and the Agent SDK never send skills/list
(its docs, code.claude.com/docs/en/mcp "Where MCP skills are supported", and
a one-skill test server on 2026-11-25: the interactive session asked for
skills/list, `-p` and claude-agent-sdk 0.2.189 did not, with or without
--disable-slash-commands). So `run.py --skills` measures the skills' text,
the same bytes a client gets, delivered through the filesystem. It does not
measure MCP delivery: the name (`wow-gear` here, `wow:wow-gear` over MCP) and
the listing differ.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path

MODE = "skills via filesystem mirror, not MCP delivery"


async def fetch(target) -> dict[str, dict[str, bytes]]:
    """{skill name: {relative path: bytes}} from a server: a FastMCP
    instance (in memory) or a client transport."""
    from fastmcp import Client
    from fastmcp.skills.client import SkillsClientExtension
    from mcp.types import BlobResourceContents, TextResourceContents

    out: dict[str, dict[str, bytes]] = {}
    async with Client(target, extensions=[SkillsClientExtension()]) as c:
        for s in await c.list_skills():
            if s.resources == "dynamic":
                raise RuntimeError(f"{s.uri}: dynamic resources, no file list to mirror")
            root = s.uri.removesuffix("SKILL.md")
            files: dict[str, bytes] = {}
            for r in s.resources:
                if not r.uri.startswith(root):
                    raise RuntimeError(f"{r.uri} is outside its skill's root {root}")
                body = b""
                for part in await c.read_resource(r.uri):
                    if isinstance(part, TextResourceContents):
                        body += part.text.encode("utf-8")
                    elif isinstance(part, BlobResourceContents):
                        import base64

                        body += base64.b64decode(part.blob)
                digest = "sha256:" + hashlib.sha256(body).hexdigest()
                if digest != r.digest or len(body) != r.size:
                    raise RuntimeError(f"{r.uri}: read {digest} ({len(body)} B), listed {r.digest} ({r.size} B)")
                files[r.uri[len(root):]] = body
            out[s.frontmatter.name] = files
    return out


def write(skills: dict[str, dict[str, bytes]], project: Path) -> list[str]:
    """The skills as project skills under project/.claude/skills."""
    base = project / ".claude" / "skills"
    if base.exists():
        shutil.rmtree(base)
    for name, files in skills.items():
        for rel, body in files.items():
            p = (base / name / rel).resolve()
            if not p.is_relative_to(base.resolve()):
                raise RuntimeError(f"{name}/{rel} leaves the skills folder")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
    return sorted(skills)


def mirror(target, project: Path) -> list[str]:
    return write(asyncio.run(fetch(target)), project)
