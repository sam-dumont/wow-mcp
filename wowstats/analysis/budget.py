"""How the `ui` process shares its memory: one number, the container's
limit, split between the process, the characters' analytical copies
(wowstats.analysis.copies) and the play analysis' query processes
(wowstats.analysis.drive, duckproc).

The limit is the cgroup's (`/sys/fs/cgroup/memory.max`, cgroup v1's
`memory.limit_in_bytes`): the compose kit's `UI_MEM_LIMIT`, a Kubernetes
pod's limit. Without one (a laptop, `wow-stats ui` outside a container)
the defaults are the ones the code had before it read a limit: 4 copies,
6 workspaces, 512 MiB a query.

The split, for a limit L (docs/install.md, "Memory"):

- PROCESS: the interpreter, the KB, the item tables, the event cache, the
  page answers and what the heap keeps after the builds. Measured in the
  image on the demo (25,655 events, three characters, two copies open):
  `ui` at 550-600 MiB once every page and tool ran, the copies' data about
  20 MiB each of that. Under 1 GiB the split can't fit a query process
  beside it: 1 GiB is the floor.
- The copies: one DuckDB copy per character the page or the MCP asked for,
  each under `memory_limit` = COPY_SHARE of what's left, at most
  COPY_LIMIT_MAX. A copy holds the prep's tables (about 20 MiB on the demo:
  `duckdb_memory()`), and building one takes a transient few times that,
  which `copies` serialises (one build at a time) and gives back
  (malloc_trim) before the next.
- The workspaces: what's left, one query process each (DuckDB's interpreter
  and the copy, about 120 MiB, plus QUERY_MEMORY a query may add, a quarter
  of what's left, at least 128 and at most 512 MiB; measured 190-210 MiB
  each after the 11 tools). 1 GiB: 2 copies, 1 workspace; 2 GiB: 5 copies,
  2 workspaces.

Everything here is a default: ANALYSIS_WORKSPACES_MAX still sets the cap
of workspaces, ANALYSIS_COPIES_MAX the cap of copies.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
from pathlib import Path

MiB = 1 << 20
PROCESS = 448 * MiB
COPY_SHARE = 0.25
COPY_LIMIT_MIN = 128 * MiB
COPY_LIMIT_MAX = 256 * MiB
# What one open copy costs past its DuckDB data: the semantic layer's
# compiled models and the read-only runner (measured: under 10 MiB).
COPY_OVERHEAD = 16 * MiB
COPIES_MIN, COPIES_MAX = 2, 8
WORKSPACE_BASE = 120 * MiB  # a query process before its query: the interpreter, DuckDB and the copy
QUERY_MIN, QUERY_MAX = 128 * MiB, 512 * MiB
# Without a limit: what the code did before it read one.
UNLIMITED = {"copies": 4, "copy_limit": COPY_LIMIT_MAX, "threads": 2, "workspaces": 6, "query": QUERY_MAX}

CGROUP_FILES = ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")


def cgroup_limit(files=CGROUP_FILES) -> int | None:
    """The container's memory limit in bytes, None when there is none
    ("max", cgroup v1's huge sentinel, no cgroup file)."""
    for f in files:
        try:
            raw = Path(f).read_text().strip()
        except OSError:
            continue
        if not raw.isdigit():
            return None  # "max": no limit
        n = int(raw)
        return n if n < 1 << 60 else None
    return None


def split(limit: int | None) -> dict:
    """The budget for a memory limit in bytes (None: none): how many
    copies, each copy's DuckDB memory_limit and threads, how many
    workspaces and how much one query may add."""
    if limit is None:
        return dict(UNLIMITED, limit=None)
    rest = max(limit - PROCESS, 0)
    copy_limit = int(min(max(rest * COPY_SHARE, COPY_LIMIT_MIN), COPY_LIMIT_MAX))
    copies_room = rest * COPY_SHARE
    # A copy's data stays far under its memory_limit on a real history (20
    # MiB on the demo): count it at a quarter of the limit, plus overhead.
    copies = int(min(max(copies_room // (copy_limit // 4 + COPY_OVERHEAD), COPIES_MIN), COPIES_MAX))
    left = rest - copies_room
    query = int(min(max(left / 4, QUERY_MIN), QUERY_MAX))
    workspaces = max(1, int(left // (WORKSPACE_BASE + query)))
    return {"limit": limit, "copies": copies, "copy_limit": copy_limit, "threads": 1 if limit < 2048 * MiB else 2,
            "workspaces": workspaces, "query": query}


@functools.cache
def current() -> dict:
    """This process' budget, from its container's limit (read once)."""
    return split(cgroup_limit())


def duckdb_size(n: int) -> str:
    """Bytes as DuckDB's memory_limit setting reads them."""
    return f"{max(n // MiB, 1)}MB"


@functools.cache
def _libc():
    name = ctypes.util.find_library("c")
    if not name:
        return None
    try:
        lib = ctypes.CDLL(name)
        return lib if hasattr(lib, "malloc_trim") else None
    except OSError:
        return None


def trim() -> None:
    """Give the heap's free pages back to the system (glibc's malloc_trim):
    a copy's build parses its rows as JSON and leaves megabytes free in the
    heap, which glibc otherwise keeps for the process. A no-op elsewhere."""
    lib = _libc()
    if lib is not None:
        lib.malloc_trim(0)
