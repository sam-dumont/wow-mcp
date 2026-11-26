"""MCP Tasks (io.modelcontextprotocol/tasks, SEP-2663; wowstats/mcp/tasks.py):
a cold analysis call runs as a task for a client that opts in, polled to its
answer, cancelled, expired; only its maker reads it; a warm call, a busy
caller and a client without the extension get the answer inline, as before."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import timedelta

import pytest
from fastmcp import Client
from fastmcp.client import extension_hooks
from fastmcp.exceptions import ToolError
from fastmcp_tasks import call_tool_task
from fastmcp_tasks.client import _send_cancel, _send_get
from mcp.shared.exceptions import MCPError

from tests.mcp import fakes, inmem, oauth_client
from tests.mcp.test_server import ctx, lab, stores  # noqa: F401 - the fixtures
from wowstats.analysis import drive
from wowstats.mcp import analysistools, server, tasks, tools

C = fakes.CHAR
SQL = {"char": C, "query": "SELECT level FROM per_level ORDER BY level"}


@pytest.fixture(autouse=True)
def analysis_on(monkeypatch, tmp_path):
    monkeypatch.setenv("ANALYSIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KB_GAME_TABLES", "off")


@pytest.fixture
def cold(monkeypatch):
    """Every analysis call finds its workspace cold, so it may be a task."""
    monkeypatch.setattr(analysistools, "warm", lambda c, args: False)


@pytest.fixture
def slow(monkeypatch):
    """play_sql takes 3 s on the server (a cold build on a big character)."""
    real = drive.sql

    def sql(*a, **kw):
        time.sleep(3)
        return real(*a, **kw)

    monkeypatch.setattr(drive, "sql", sql)


def text(res) -> str:
    return res.content[0].text


def test_only_the_workspace_tools_are_tasks():
    """The four analysis calls that build the workspace. analyse and
    dashboard_refresh start nothing on the server (the client's model runs
    the analysis) and nothing that spends the server's AI budget is one."""
    assert {t.name for t in tools.TOOLS + (tools.MARKET,) if t.task} == {
        "analysis_brief", "play_sql", "query_metric", "propose_finding"}

    async def go():
        mcp = inmem.build(tools.Ctx(store=None))
        return {t.name: t.task_config.mode for t in await mcp.list_tools()}

    modes = asyncio.run(go())
    assert {n for n, m in modes.items() if m != "forbidden"} == {
        "analysis_brief", "play_sql", "query_metric", "propose_finding"}


def test_task_created_polled_completed_read(ctx, cold):  # noqa: F811
    inline = inmem.call(ctx, "play_sql", SQL)
    assert not inline.is_error

    async def go():
        async with Client(inmem.build(ctx)) as c:
            task = await call_tool_task(c, "play_sql", SQL)
            first = task.create_result
            done = await task.wait(timeout=60)
            res = await task.result()
            again = await _send_get(c.session, task.task_id)
            return first, done, res, again

    first, done, res, again = asyncio.run(go())
    assert first.status == "working" and first.ttl_ms == tasks.TTL.total_seconds() * 1000
    assert first.poll_interval_ms == tasks.POLL.total_seconds() * 1000
    assert done.status == "completed"
    assert not res.is_error
    # The same answer the inline call gives (`as_of` and friends included).
    assert "level" in res.structured_content["result"]
    assert res.structured_content["result"] == inline.structured_content["result"]
    assert again.status == "completed"  # read again until the TTL ends


def test_transparent_for_a_client_that_waits(ctx, cold):  # noqa: F811
    """A FastMCP client opted in polls under the hood: call_tool reads the same."""
    res = inmem.call(ctx, "query_metric", {"char": C, "metrics": ["played_s"], "group_by": ["level"]})
    assert not res.is_error and res.structured_content["rows"], res.structured_content


def test_cancelled(ctx, cold, monkeypatch):  # noqa: F811
    started = threading.Event()
    release_query = threading.Event()
    finished = threading.Event()

    def blocked_sql(*args, **kwargs):
        started.set()
        release_query.wait(timeout=30)  # Bound cleanup if cancellation hangs.
        finished.set()
        return {}

    monkeypatch.setattr(drive, "sql", blocked_sql)

    async def go():
        async with Client(inmem.build(ctx)) as c:
            task = await call_tool_task(c, "play_sql", SQL)
            assert await asyncio.to_thread(started.wait, 30)
            await task.cancel()
            got = await task.wait(timeout=30)
            assert got.status == "cancelled"
            with pytest.raises(ToolError):
                await task.result()

    try:
        asyncio.run(go())
        assert not finished.is_set(), "cancelling the task waited for its SQL query"
    finally:
        release_query.set()


def test_expired(ctx, cold, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tasks, "TTL", timedelta(seconds=1))

    async def go():
        async with Client(inmem.build(ctx)) as c:
            task = await call_tool_task(c, "play_sql", SQL)
            assert (await task.wait(timeout=60)).status == "completed"
            await asyncio.sleep(2.5)
            with pytest.raises(MCPError, match="not found"):
                await _send_get(c.session, task.task_id)

    asyncio.run(go())


def test_warm_workspace_answers_inline(ctx):  # noqa: F811
    """Once the character's workspace is open the call is quick: no task,
    even for a client that asked for tasks."""
    assert not inmem.call(ctx, "play_sql", SQL).is_error  # opens it
    assert analysistools.warm(ctx, SQL)

    async def go():
        async with Client(inmem.build(ctx)) as c:
            with pytest.raises(ToolError, match="synchronously|task"):
                await call_tool_task(c, "play_sql", SQL)

    asyncio.run(go())


def test_busy_caller_runs_inline(ctx, cold, slow, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tasks, "PER_PERSON", 1)

    async def go():
        async with Client(inmem.build(ctx)) as c:
            first = await call_tool_task(c, "play_sql", SQL)
            with pytest.raises(ToolError):  # the second answers inline, not as a task
                await call_tool_task(c, "play_sql", SQL)
            assert (await first.wait(timeout=30)).status == "completed"
            # Its task ended: the next one may be a task again.
            nxt = await call_tool_task(c, "play_sql", SQL)
            await nxt.cancel()

    asyncio.run(go())


def test_client_without_the_extension(ctx, cold, monkeypatch):  # noqa: F811
    """A client that doesn't declare io.modelcontextprotocol/tasks gets
    today's answer inline: the server never hands it a task."""
    monkeypatch.setattr(extension_hooks, "_internal_client_extension_factories", [])

    async def go():
        async with Client(inmem.build(ctx)) as c:
            res = await c.call_tool("play_sql", SQL, raise_on_error=False)
            with pytest.raises(MCPError):  # and the task methods refuse it
                await _send_get(c.session, "x")
            return res

    res = asyncio.run(go())
    assert not res.is_error and "level" in text(res)


# ---------------------------------------------------------------- through the login


@pytest.fixture
def lab_env(tmp_path):
    """Two people may use the lab's server, the analysis on."""
    return {"OIDC_ALLOWED_SUBS": "owner-sub,friend-sub", "ANALYSIS_STATE_DIR": str(tmp_path / "lab-state"),
            "KB_GAME_TABLES": "off"}


def login_as(idp, run, sub: str) -> oauth_client.Login:
    idp.user = sub
    idp.users.setdefault(sub, {"org": "org-owner", "roles": ["user"], "active": True})
    lg = oauth_client.Login(run.url.rsplit("/mcp", 1)[0], run.url)
    lg.login()
    return lg


def test_only_its_maker_reads_a_task(lab, cold, slow):  # noqa: F811
    """Another person gets "not found", the same words as for a task that
    doesn't exist. The same person signed in from another client reads it:
    the task is theirs, and a client that reconnects picks it up."""
    idp, run, owner = lab
    owner.login()
    friend = login_as(idp, run, "friend-sub")
    owner_again = oauth_client.Login(owner.base, run.url)
    idp.user = "owner-sub"
    owner_again.login()

    async def go():
        async with Client(run.url, auth=owner.tokens["access_token"]) as c:
            task = await call_tool_task(c, "play_sql", SQL)
            async with Client(run.url, auth=friend.tokens["access_token"]) as other:
                for send in (_send_get, _send_cancel):
                    with pytest.raises(MCPError, match=f"Task {task.task_id} not found"):
                        await send(other.session, task.task_id)
            async with Client(run.url, auth=owner_again.tokens["access_token"]) as again:
                seen = await _send_get(again.session, task.task_id)
            got = await task.wait(timeout=60)
            res = await task.result()
            return seen, got, res

    try:
        seen, got, res = asyncio.run(go())
    finally:
        friend.close()
        owner_again.close()
    assert seen.status in ("working", "completed")
    assert got.status == "completed" and not res.is_error and "level" in res.structured_content["result"]


def test_switched_off_person_loses_their_tasks(lab, cold, slow, monkeypatch):  # noqa: F811
    idp, run, owner = lab
    owner.login()

    async def go():
        async with Client(run.url, auth=owner.tokens["access_token"]) as c:
            task = await call_tool_task(c, "play_sql", SQL)
            monkeypatch.setattr(server.Who, "may_call", lambda self, sub: False)
            with pytest.raises(MCPError, match="not found"):
                await _send_get(c.session, task.task_id)

    asyncio.run(go())


def test_legacy_client_over_http_gets_the_answer_inline(lab, cold):  # noqa: F811
    """A 2025-06-18 client (handshake era): the tools answer as before."""
    _idp, _run, lg = lab
    lg.login()
    out = oauth_client.result(lg.rpc("tools/call", {"name": "play_sql", "arguments": SQL}))
    assert "result" in out and not out["result"].get("isError")
    assert "level" in out["result"]["structuredContent"]["result"]
    assert out["result"].get("resultType") != "task"
