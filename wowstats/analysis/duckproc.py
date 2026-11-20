"""play_sql's, the judge's and the recheck's DuckDB queries in a child process
with a memory cap, so a query that blows up kills only that child.

DuckDB's memory_limit counts its buffer pool, not a single value a scalar
function builds: `WITH a AS (SELECT string_agg(...) AS x FROM range(1000000))
SELECT x || x || ... ` grew past 8 GB with memory_limit 256MB,
operator_memory_limit 64MB, max_memory 256MB and one thread (DuckDB 2.0.0,
tried 2026-11-20), and took the whole server down with it. A blocklist of
functions can't close that (stacked CTEs multiply without any of them), and
every function stays available to the analysis.

So the parent keeps its own locked-down copy for the checks that don't run
data (plan_check, DESCRIBE, the semantic layer's compile) and sends each
query to a child holding another copy:
- Linux (the image): RLIMIT_AS at the child's size once loaded plus
  QUERY_MEMORY. A query past it gets DuckDB's out-of-memory error, or kills
  the child;
- Linux and macOS: a watchdog thread in the child ends it when its resident
  size grows QUERY_MEMORY past the loaded size (macOS ignores RLIMIT_AS);
- the parent waits QUERY_SECONDS plus a grace, then kills the child;
- on Linux the child asks to be the kernel's first pick when the
  container runs out of memory (oom_score_adj 1000), so `ui` lives on.
A dead child answers the caller with a QueryError and is started again on
the next query. Its environment is empty but for what Python needs.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path

from .sql import MAX_ROWS, QueryError

try:
    import resource
except ImportError:  # Windows: no memory cap there, the parent's timeout only
    resource = None

# Bytes a query may add to the loaded copy: twice DuckDB's own memory_limit
# (256 MB), and well under the compose kit's 1 GiB for `ui`.
QUERY_MEMORY = 512 << 20
GRACE_SECONDS = 5.0  # on top of the query's own timeout, before the parent kills the child
START_SECONDS = 120.0  # loading a long history into the child
REPLY_MAX = 32 << 20  # bytes of JSON an answer may take
WATCH_EVERY = 0.01
_HEAD = struct.Struct(">I")
TOO_BIG = "the query needs more memory than a query may use: aggregate or narrow it"


# ---------------------------------------------------------------- framing


def _send(fh, payload: bytes) -> None:
    fh.write(_HEAD.pack(len(payload)) + payload)
    fh.flush()


def _recv_blocking(fh) -> bytes | None:
    head = fh.read(_HEAD.size)
    if len(head) < _HEAD.size:
        return None
    (n,) = _HEAD.unpack(head)
    body = fh.read(n)
    return body if len(body) == n else None


def _recv(fh, deadline: float, limit: int) -> bytes:
    """One frame from the child, or TimeoutError / EOFError / ValueError.
    The read runs in a thread (select can't wait on a pipe on Windows); on a
    timeout the caller kills the child, which ends the read."""
    box: dict = {}

    def read() -> None:
        try:
            head = fh.read(_HEAD.size)
            if len(head) < _HEAD.size:
                raise EOFError
            (n,) = _HEAD.unpack(head)
            if n > limit:
                raise ValueError(n)
            body = fh.read(n)
            if len(body) < n:
                raise EOFError
            box["body"] = body
        except (EOFError, ValueError) as e:
            box["error"] = e
        except OSError:  # the pipe closed under the read: the child is gone
            box["error"] = EOFError()

    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(max(0.0, deadline - time.monotonic()))
    if t.is_alive():
        raise TimeoutError
    if "error" in box:
        raise box["error"]
    return box["body"]


# ---------------------------------------------------------------- the child


def _rss() -> int:
    """Resident bytes now (Linux) or at the peak (elsewhere: ru_maxrss);
    0 where neither exists (Windows)."""
    try:
        with open("/proc/self/statm", "rb") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except OSError:
        if resource is None:
            return 0
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak if sys.platform == "darwin" else peak * 1024


def _cap_address_space(memory: int) -> None:
    try:
        with open("/proc/self/statm", "rb") as f:
            size = int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
    except OSError:
        return  # not Linux: the watchdog alone
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    want = size + memory
    if hard != resource.RLIM_INFINITY:
        want = min(want, hard)
    resource.setrlimit(resource.RLIMIT_AS, (want, hard))


def _watch(base: int, memory: int) -> None:
    while True:
        if _rss() - base > memory:
            print(f"duckproc: query passed {memory >> 20} MB, stopping the query process", file=sys.stderr,
                  flush=True)
            os._exit(3)
        time.sleep(WATCH_EVERY)


def child() -> None:
    from . import duck, semantic

    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # nothing but frames on the parent's pipe
    inp = sys.stdin.buffer
    head = json.loads(_recv_blocking(inp) or b"{}")
    data = _recv_blocking(inp)
    if data is None:
        return
    db = sqlite3.connect(":memory:")
    db.deserialize(data)
    del data
    con = duck.mirror(db, head.get("tables"))
    db.close()
    if head.get("semantic"):
        semantic.prepare(con, head.get("tz"))
    ro = {}
    seconds = float(head.get("seconds") or duck.QUERY_SECONDS)
    memory = int(head.get("memory") or QUERY_MEMORY)
    duck.ReadOnlyDuck(con, seconds).run("SELECT 1")  # DuckDB's threads up before the measure
    base = _rss()
    _cap_address_space(memory)
    with contextlib.suppress(OSError), open("/proc/self/oom_score_adj", "w") as f:
        f.write("1000")  # the container's OOM killer takes this child before the server
    if base:
        threading.Thread(target=_watch, args=(base, memory), daemon=True).start()
    _send(out, b'{"ready":true}')
    while (req := _recv_blocking(inp)) is not None:
        q = json.loads(req)
        key = None if q.get("tables") is None else tuple(sorted(q["tables"]))
        if key not in ro:
            ro[key] = duck.ReadOnlyDuck(con, seconds, tables=None if key is None else set(key))
        try:
            cols, rows, cut = ro[key].run(q["sql"], int(q.get("max_rows") or MAX_ROWS), q.get("params"))
            reply = json.dumps({"cols": cols, "rows": rows, "cut": cut}, separators=(",", ":")).encode()
            if len(reply) > REPLY_MAX:
                reply = json.dumps({"error": f"the answer is larger than {REPLY_MAX >> 20} MB: "
                                             "aggregate or narrow it"}).encode()
        except QueryError as e:
            reply = json.dumps({"error": str(e)}).encode()
        except MemoryError:
            reply = json.dumps({"error": TOO_BIG}).encode()
        except Exception as e:  # noqa: BLE001 - the query's own failure, as the in-process runner says it
            reply = json.dumps({"error": duck._short(e)}).encode()
        _send(out, reply)


# ---------------------------------------------------------------- the parent


def _stop(cell: list) -> None:
    proc = cell[0]
    cell[0] = None
    if proc is None:
        return
    # Kill first: a reader thread blocked on stdout holds its lock, and
    # close() would wait for it; the child's death ends that read.
    if proc.poll() is None:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
    for f in (proc.stdin, proc.stdout):
        with contextlib.suppress(OSError, ValueError):
            f.close()


class Worker:
    """One child process with a copy of a prep database (and the semantic
    layer's views unless `semantic` is off); `run` is ReadOnlyDuck.run's
    execution part (the parent has checked the query)."""

    def __init__(self, db: sqlite3.Connection, tables=None, seconds: float | None = None,
                 memory: int | None = None, semantic: bool = True):
        from . import duck, prep

        self.db = db  # serialized again at each start: no second copy held between them
        self.tables = None if tables is None else sorted(tables)
        self.seconds = duck.QUERY_SECONDS if seconds is None else seconds
        self.memory, self.semantic = QUERY_MEMORY if memory is None else memory, semantic
        self.tz = prep.TZ_NAME
        self.lock = threading.Lock()
        self.starts = 0
        self._cell: list = [None]
        weakref.finalize(self, _stop, self._cell)

    def _env(self) -> dict:
        root = str(Path(__file__).resolve().parents[2])
        env = {"PATH": os.defpath, "PYTHONPATH": root, "LANG": "C.UTF-8", "WOW_TIMEZONE": self.tz}
        if "SYSTEMROOT" in os.environ:  # Windows can't start Python without it
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        return env

    def _proc(self):
        proc = self._cell[0]
        if proc is not None and proc.poll() is None:
            return proc
        _stop(self._cell)
        proc = subprocess.Popen([sys.executable, "-m", "wowstats.analysis.duckproc"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, env=self._env(), cwd="/")
        self._cell[0] = proc
        self.starts += 1
        try:
            _send(proc.stdin, json.dumps({"tables": self.tables, "tz": self.tz, "seconds": self.seconds,
                                          "memory": self.memory, "semantic": self.semantic}).encode())
            _send(proc.stdin, self.db.serialize())
            if json.loads(_recv(proc.stdout, time.monotonic() + START_SECONDS, 1024)) != {"ready": True}:
                raise EOFError
        except (OSError, EOFError, TimeoutError, ValueError):
            _stop(self._cell)
            raise QueryError("the query engine didn't start: try again in a minute") from None
        return proc

    def run(self, sql: str, max_rows: int, params: dict | None, tables) -> tuple[list[str], list[tuple], bool]:
        req = json.dumps({"sql": sql, "max_rows": max_rows, "params": params,
                          "tables": None if tables is None else sorted(tables)}).encode()
        with self.lock:
            proc = self._proc()
            try:
                _send(proc.stdin, req)
                got = json.loads(_recv(proc.stdout, time.monotonic() + self.seconds + GRACE_SECONDS,
                                       REPLY_MAX + 1024))
            except TimeoutError:
                _stop(self._cell)
                raise QueryError(f"stopped after {self.seconds:.0f} s: narrow the query") from None
            except (OSError, EOFError, ValueError):
                _stop(self._cell)  # the cap ended it (or it died some other way): the next query starts a new one
                raise QueryError(TOO_BIG) from None
        if "error" in got:
            raise QueryError(got["error"])
        return got["cols"], [tuple(r) for r in got["rows"]], bool(got["cut"])

    def close(self) -> None:
        with self.lock:
            _stop(self._cell)


if __name__ == "__main__":
    child()
