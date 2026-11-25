"""MCP Tasks (io.modelcontextprotocol/tasks, SEP-2663) for the calls that
can take a while: the analysis tools whose first call for a character builds
its workspace (wowstats.analysis.drive.Workspace: the events read, the prep
database, the DuckDB child; about 3 s on a real character, 2026-11-25). A
client that opts in gets a task handle back and polls `tasks/get`; a
client that doesn't gets the answer inline, as before.

What runs as a task, and when, is the server's call (the spec lets it decide
per request):

- only the tools marked `task=True` in wowstats.mcp.tools (TASK_TOOLS);
- only when that character's workspace isn't open yet: a warm call answers
  in well under a second, and a task would only add polls to the hundreds
  of queries an analysis run makes;
- only while the caller has fewer than PER_PERSON tasks going: past that,
  the call runs inline (slower, never refused).

No task runs a model on the server. dashboard_refresh and analyse start
nothing there (the caller's own model does the analysis, 2026-11-21), so
they stay inline, and the server's automatic refreshes keep their own
budget check (wowstats.analysis.auto), which no MCP call reaches.

Where a task lives: FastMCP's task engine (fastmcp-tasks, Docket) on its
in-process memory backend. One replica serves /mcp, so that is the whole
queue. A restart drops every task: `tasks/get` then answers "not found" and
the client calls the tool again. What the task computed isn't lost with it:
an analysis run's state is on disk (drive.json), and the call only read it.

Who may read a task: its key carries the caller's `sub` (with the OAuth
proxy's own client id, the same for every MCP client:
fastmcp_tasks.context.get_task_scope), so another person gets "not found",
the same answer as for a task that doesn't exist. The same person signed in
from another client may read it: a client that reconnects picks its task up.
The task methods also check that the caller may still use the server, like
every tools/call.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any

from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension
from fastmcp_tasks.context import get_task_context, get_task_scope
from fastmcp_tasks.models import CreateTaskResult
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

# How long a finished task's answer is kept for `tasks/get` (ttlMs). The
# answers are seconds of work: a client that hasn't polled in 15 minutes
# calls again.
TTL = timedelta(minutes=15)
# pollIntervalMs: a cold build takes a few seconds.
POLL = timedelta(seconds=1)
# Tasks one person may have going at once; more run inline.
PER_PERSON = 3
# Tasks running at once across the install (Docket's worker): each holds a
# thread and a workspace build.
CONCURRENCY = 4


def task_config() -> TaskConfig:
    return TaskConfig(mode="optional", poll_interval=POLL)


class Live:
    """The tasks each caller has going, by task scope: created here, ended by
    the tool's run (WowTool.run calls `done`). An entry older than the TTL
    is dropped as well: a task cancelled before it started never runs, so
    nothing else would end it."""

    def __init__(self, ttl: timedelta):
        self.ttl = ttl.total_seconds()
        self._lock = threading.Lock()
        self._by: dict[str | None, dict[str, float]] = {}

    def _prune(self, now: float) -> None:
        for scope in list(self._by):
            ids = {t: at for t, at in self._by[scope].items() if now - at < self.ttl}
            if ids:
                self._by[scope] = ids
            else:
                del self._by[scope]

    def count(self, scope: str | None) -> int:
        with self._lock:
            self._prune(time.monotonic())
            return len(self._by.get(scope, ()))

    def add(self, scope: str | None, task_id: str) -> None:
        with self._lock:
            self._by.setdefault(scope, {})[task_id] = time.monotonic()

    def done(self) -> None:
        """Called at the end of a tool run; a no-op outside a task."""
        info = get_task_context()
        if info is None:
            return
        with self._lock:
            ids = self._by.get(info.task_scope)
            if ids is not None:
                ids.pop(info.task_id, None)
                if not ids:
                    del self._by[info.task_scope]


def in_task() -> bool:
    return get_task_context() is not None


class WowTasks(TasksExtension):
    """FastMCP's tasks extension with this server's rules (module docstring).

    `who` is wowstats.mcp.server.Who."""

    def __init__(self, who: Any):
        super().__init__(url="memory://", execution_ttl=TTL, concurrency=CONCURRENCY)
        self.who, self.per_person = who, PER_PERSON
        self.live = Live(TTL)

    def clone(self) -> WowTasks:
        twin = super().clone()
        twin.live = self.live
        return twin

    def _may(self, task_id: str) -> None:
        """The allowed-subs check on the task methods: a person switched off
        loses their tasks with the rest (AuthMiddleware covers tools/call;
        these methods don't pass through the middleware). A task they made
        answers "not found", the same words as for anyone else's (fastmcp_tasks
        says one message for missing, expired and not yours)."""
        from fastmcp.server.dependencies import get_access_token

        tok = get_access_token()
        if tok is None:  # no login (MCP_AUTH=none on a loopback bind, the tests)
            return
        sub = str(tok.claims.get("sub", ""))
        if not (sub and self.who.may_call(sub)):
            raise MCPError(code=INVALID_PARAMS, message=f"Task {task_id} not found")

    async def _handle_get(self, ctx, params):
        self._may(params.task_id)
        return await super()._handle_get(ctx, params)

    async def _handle_cancel(self, ctx, params):
        self._may(params.task_id)
        return await super()._handle_cancel(ctx, params)

    async def _handle_update(self, ctx, params):
        self._may(params.task_id)
        return await super()._handle_update(ctx, params)

    async def intercept_tool_call(self, params, context, call_next):
        from fastmcp.server.dependencies import _is_client_tool_call

        from . import analysistools, tools

        spec = tools.BY_NAME.get(params.name)
        if spec is None or not spec.task or not _is_client_tool_call():
            return await call_next()
        scope = get_task_scope()
        if self.live.count(scope) >= self.per_person:
            return await call_next()
        try:
            if analysistools.warm(self.who.ctx(_sub()), dict(params.arguments or {})):
                return await call_next()
        except PermissionError:
            return await call_next()  # the tool says so itself
        out = await super().intercept_tool_call(params, context, call_next)
        if isinstance(out, CreateTaskResult):
            self.live.add(scope, out.task_id)
        return out


def _sub() -> str | None:
    from fastmcp.server.dependencies import get_access_token

    tok = get_access_token()
    return str(tok.claims.get("sub", "")) if tok is not None else None
