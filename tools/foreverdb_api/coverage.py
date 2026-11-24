"""How many of ForeverDB.net's best-in-slot picks have a source we know:
every item its ladders pick first for a slot at some level from 1 to 60, for
the five specs of tests/data/foreverdb-ladders.json.gz, in all three modes
(1,339 items). Prints the count with and without data/foreverdb/api.json,
and by kind.

    uv run python tools/foreverdb_api/coverage.py [--api FILE]

The release workflow prints it in the run summary after the fetch.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LADDERS = ROOT / "tests" / "data" / "foreverdb-ladders.json.gz"


def picks() -> set[int]:
    """Every item a ladder ranks first in its slot at some level (ForeverDB's
    own scores and order: score, then item level, then the lower ID), with
    the page's default filters (PvP rewards in PvP mode only)."""
    lad = json.loads(gzip.decompress(LADDERS.read_bytes()))
    out: set[int] = set()
    for d in lad.values():
        for mode, slots in d["ladder"].items():
            groups = {"b", "d", "r", "c", "w", "q", "v"} | ({"p"} if mode == "pvp" else set())
            for entries in slots.values():
                ok = [e for e in entries if any(g in groups for g in (d["items"][str(e[0])].get("g") or "b"))]
                for level in range(1, 61):
                    here = [e for e in ok if e[1] <= level]
                    if here:
                        best = min(here, key=lambda e: (-e[2], -(d["items"][str(e[0])].get("il") or 0), e[0]))
                        out.add(best[0])
    return out


def count(api: str | None) -> tuple[int, collections.Counter]:
    """Picks with a source, and the kinds, reading the gear data fresh with
    the API file `api` ("off" for none)."""
    os.environ["FOREVERDB_DATA_FILE"] = api or "off"
    from wowstats import fdbapi, gear

    fdbapi.reset()
    gear._CACHE.clear()
    ref = gear.reference()
    sources = ref.data.get("sources") or {}
    kinds: collections.Counter = collections.Counter()
    got = 0
    for i in picks():
        ss = sources.get(str(i)) or []
        if ss:
            got += 1
            for k in {s["kind"] for s in ss}:
                kinds[k] += 1
    return got, kinds


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=str(ROOT / "data" / "foreverdb" / "api.json"))
    a = ap.parse_args(argv)
    total = len(picks())
    before, kb = count("off")
    after, ka = count(a.api)
    print(f"best-in-slot picks with a known source: {before} of {total} without the API data, "
          f"{after} with it (+{after - before})")
    print(f"  by kind without: {dict(kb)}")
    print(f"  by kind with:    {dict(ka)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
