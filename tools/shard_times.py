"""Write tests/shard_times.json, the seconds each test file takes, which
tests/conftest.py splits CI's pytest parts by (--shard K/N).

From the output of a run of the whole suite with every duration:

    uv run pytest -q -n 4 -m "not browser" --durations=0 -vv | tee run.log
    uv run python tools/shard_times.py run.log

or from CI's logs (`gh run view --job <id> --log`), each part's together
(ci.yml prints the slowest 20 only, so a local run is the one to use).
A file's time is the sum of its tests' setup, call and teardown: what it
costs a part, whichever worker runs it. Run it again when a part grows
past the others (ci.yml's pytest job prints each part's time)."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "shard_times.json"
LINE = re.compile(r"(\d+(?:\.\d+)?)s (?:setup|call|teardown)\s+(tests/[^:\s]+\.py)::")


def main(logs: list[str]) -> None:
    if not logs:
        sys.exit(__doc__)
    secs: dict[str, float] = defaultdict(float)
    for log in logs:
        for m in LINE.finditer(Path(log).read_text(errors="replace")):
            secs[m.group(2)] += float(m.group(1))
    if not secs:
        sys.exit("no durations in the logs: run pytest with --durations=0 -vv")
    # Whole seconds, at least one: the table is for balance, not for timing.
    table = {k: max(1, round(v)) for k, v in sorted(secs.items())}
    OUT.write_text(json.dumps(table, indent=1) + "\n")
    print(f"{OUT.relative_to(ROOT)}: {len(table)} files, {sum(table.values())} s")


if __name__ == "__main__":
    main(sys.argv[1:])
