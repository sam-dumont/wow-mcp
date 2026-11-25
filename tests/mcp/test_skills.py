"""The skills the MCP serves (wowstats/mcp/skills, the MCP Skills extension
io.modelcontextprotocol/skills, SEP-2640): listed and fetched the way a
client that opts in gets them, readable as skill:// resources by any client,
through the login too, and written against what the server really has: every
tool, view, metric and argument a skill names exists."""

from __future__ import annotations

import asyncio
import hashlib
import re

import pytest
import yaml
from fastmcp import Client
from fastmcp.skills import SkillsClientExtension

from tests.mcp import inmem
from tests.mcp.test_server import ctx, lab, lab_env, stores  # noqa: F401 - the fixtures
from wowstats.analysis import semantic
from wowstats.mcp import server, tools

SKILLS = sorted(p.parent for p in server.SKILLS_DIR.glob("*/SKILL.md"))
# A skill is loaded whole into the model's context: short and procedural.
MAX_BYTES = 3_000
# What a host shows the model to pick a skill from (the Agent Skills spec
# allows 1024).
MAX_DESCRIPTION = 400
# Answer fields the skills name, besides tools, arguments, views and metrics.
FIELDS = {"line", "source", "wowhead", "untrusted", "untrusted_reference_data", "in_current_zone", "trip",
          "party", "next", "note", "constants", "pve", "pvp", "flb", "pawn", "level",
          # professions
          "gold", "unpriced", "route", "why_incomplete", "mats", "trainer_ranks", "crafted_gear", "unlock"}


def frontmatter(path) -> dict:
    text = (path / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n", 2)[1])


def body(path) -> str:
    return (path / "SKILL.md").read_text(encoding="utf-8").split("---\n", 2)[2]


def known_names() -> set[str]:
    specs = tools.TOOLS + (tools.MARKET,)
    names = {t.name for t in specs}
    names |= {a for t in specs for a in (t.input_schema.get("properties") or {})}
    names |= set(semantic.views())
    names |= {m["name"] for m in semantic.catalogue()["metrics"]}
    return names | FIELDS


def test_there_are_skills_and_each_is_valid():
    assert len(SKILLS) >= 6
    for p in SKILLS:
        fm = frontmatter(p)
        assert fm["name"] == p.name and re.fullmatch(r"wow-[a-z0-9-]+", p.name)
        assert 40 <= len(fm["description"]) <= MAX_DESCRIPTION, p.name
        assert (p / "SKILL.md").stat().st_size <= MAX_BYTES, p.name
        assert "—" not in (p / "SKILL.md").read_text(encoding="utf-8")


def test_skills_name_only_what_the_server_has():
    known = known_names()
    tool_names = {t.name for t in tools.TOOLS + (tools.MARKET,)}
    skill_names = {p.name for p in SKILLS}
    for p in SKILLS:
        text = body(p)
        ticked = set(re.findall(r"`([a-z][a-z0-9_]*)`", text))
        assert ticked <= known, f"{p.name} names {sorted(ticked - known)}"
        assert ticked & tool_names, p.name
        views = set(re.findall(r"\bper_[a-z_]+\b", text))
        assert views <= set(semantic.views()), f"{p.name}: {sorted(views - set(semantic.views()))}"
        others = set(re.findall(r"\b(wow-[a-z0-9-]+) skill\b", text))
        assert others <= skill_names, p.name


def test_skills_keep_the_servers_rules():
    for p in SKILLS:
        text = body(p)
        assert "never he or she" in text and "`untrusted`" in text, p.name
        assert "from a tool result" in text, p.name
        bare = re.sub(r"never he or she", "", text)
        assert not re.search(r"\b(he|she|him|his|her|hers)\b", bare, re.I), p.name
        # The web is never the first stop.
        for m in re.finditer(r"[^.\n]*\bweb\b[^.\n]*", text):
            assert re.search(r"\b(never|don't|no)\b", m.group(0), re.I), f"{p.name}: {m.group(0)}"


async def _listed(mcp):
    async with Client(mcp, extensions=[SkillsClientExtension()]) as c:
        listed = await c.list_skills()
        got = {}
        for s in listed:
            entry = (await c.get_skill_mcp(s.uri)).skill
            read = await c.read_resource(s.uri)
            got[s.uri] = (s, entry, read[0].text)
        try:
            await c.get_skill_mcp("skill://wow-nothing/SKILL.md")
            unknown = None
        except Exception as e:  # noqa: BLE001 - the protocol error, whatever FastMCP wraps it in
            unknown = e
        return got, unknown


def test_skills_list_get_and_read(ctx):  # noqa: F811
    got, unknown = asyncio.run(_listed(inmem.build(ctx)))
    assert sorted(got) == [f"skill://{p.name}/SKILL.md" for p in SKILLS]
    for uri, (listed, entry, text) in got.items():
        name = uri.split("/")[2]
        assert entry.model_dump() == listed.model_dump()
        assert listed.frontmatter.root == frontmatter(server.SKILLS_DIR / name)
        raw = text.encode("utf-8")
        assert raw == (server.SKILLS_DIR / name / "SKILL.md").read_bytes()
        (res,) = listed.resources
        assert res.size == len(raw) and res.digest == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert unknown is not None


def test_a_client_without_skills_still_works(ctx):  # noqa: F811
    async def go():
        async with Client(inmem.build(ctx)) as c:
            tools_ = await c.list_tools()
            resources = [str(r.uri) for r in await c.list_resources()]
            with pytest.raises(RuntimeError):
                await c.list_skills()
            return tools_, resources

    tools_, resources = asyncio.run(go())
    assert len(tools_) == len(tools.TOOLS)
    assert {f"skill://{p.name}/SKILL.md" for p in SKILLS} <= set(resources)


def test_skills_through_the_login(lab):  # noqa: F811
    _, run, lg = lab
    lg.login()

    async def go():
        async with Client(run.url, auth=lg.tokens["access_token"], extensions=[SkillsClientExtension()]) as c:
            return [s.uri for s in await c.list_skills()]

    assert sorted(asyncio.run(go())) == [f"skill://{p.name}/SKILL.md" for p in SKILLS]
