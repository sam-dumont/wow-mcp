"""The evaluation's MCP server: wow-stats' own stdio server (`wow-stats
mcp-stdio`, every tool and prompt as a player's client sees them) over one
scenario's events instead of a VictoriaLogs.

    uv run --extra mcp python tools/eval_analysis/server.py SETTINGS.json EVENTS.jsonl

The runner (run.py) writes SETTINGS.json: the server's whole environment.
Whatever the client that starts this process passes on is dropped, so the
server sees a throwaway ANALYSIS_STATE_DIR, VL_URL pointing at a closed
local port (a read this file doesn't answer fails at once instead of
reaching a real store), the scenario's own settings (WEIGHTS...), and no
provider settings at all. Two reads are swapped, the same seam the analysis tests use
(tests/analysis/test_drive.py): the character's events (prep.query), from
the file with the store query's own filter, and the other players met
(none: the demo meets nobody the shared page must hide).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import scenarios  # noqa: E402


def serve_events(rows: list[dict]) -> None:
    """Point the analysis at `rows` (this process only)."""
    from wowstats import analysis
    from wowstats.analysis import prep

    def query(store, stream: str, char: str) -> list[dict]:
        return scenarios.analysis_rows(rows, char)

    prep.query = query
    analysis.other_players = lambda store, stream, char: set()


KEEP = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")


def clean_env(settings: dict) -> None:
    keep = {k: os.environ[k] for k in KEEP if k in os.environ}
    os.environ.clear()
    os.environ.update(keep)
    os.environ.update({str(k): str(v) for k, v in settings.items()})


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    clean_env(json.loads(Path(argv[0]).read_text(encoding="utf-8")))
    serve_events(scenarios.read_events(Path(argv[1])))
    from wowstats.mcp import stdio

    return stdio(os.environ)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
