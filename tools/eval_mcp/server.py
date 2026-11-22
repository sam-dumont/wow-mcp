"""The MCP check's server: `wow-stats mcp-stdio` on the demo's store, with
every clock at the demo's "now" (tests/degraded.py now(), ticking from
there), so the answers don't slide with the wall clock.

    uv run --extra mcp python tools/eval_mcp/server.py SETTINGS.json

run.py writes SETTINGS.json: the server's whole environment (answers.py
server_env, plus EVAL_NOW). Whatever the client passes on is dropped.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

KEEP = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    settings = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    keep = {k: os.environ[k] for k in KEEP if k in os.environ}
    os.environ.clear()
    os.environ.update(keep)
    os.environ.update({str(k): str(v) for k, v in settings.items()})
    import time_machine

    from wowstats.mcp import stdio

    with time_machine.travel(datetime.fromisoformat(os.environ.pop("EVAL_NOW")), tick=True):
        return stdio(os.environ)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
