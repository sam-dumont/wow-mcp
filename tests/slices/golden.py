"""The golden characters' plan, moves, prep and effects as stable keys, and
the committed snapshot they are checked against (tests/slices/golden.json).

A key that is in the snapshot and no longer comes out is a behaviour that
went away: a move, a raid prep or a class effect a player had and lost. That
fails the slices (tests/slices/test_slices.py) and the KB refresh's
behaviour gate (tools/kb/gates.py behaviour_gate), unless the same change
updates the snapshot. New keys don't fail: they are listed, and `--write`
takes them in.

    uv run python -m tests.slices.golden --check     # what went away (exit 1), what is new
    uv run python -m tests.slices.golden --write     # take the current behaviour as the snapshot

Keys hold names, ids and counts only, never a number the data moves (an XP
figure, a time) nor the knowledge base's wording, so a pace change or a
reworded note doesn't read as a lost move; a prep, an effect or a spec's
standing that disappears does.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

PATH = Path(__file__).with_name("golden.json")


def _plain(s) -> str:
    """A title without its numbers and dates (9 guide steps -> # guide steps)."""
    return re.sub(r"\d[\d,.:%×]*", "#", str(s or "")).strip()


def move_key(m: dict) -> str:
    what = m.get("item_id") or (m.get("zone") or {}).get("name") or (m.get("item") or {}).get("item_id")
    return f"{m.get('kind')}|{what if what else _plain(m.get('title') or m.get('text'))}"


def keys(s) -> dict[str, list[str]]:
    """The stable keys of one served golden character (harness.Served)."""
    ov = s.page("overview")
    meta = s.page("meta")
    ad = meta.get("adherence") or {}
    plan = s.mcp("plan_session", hours=2)
    preps, n = [], {}
    for p in ad.get("prep") or []:
        ev = p.get("event")
        if (p.get("quest") or {}).get("name"):
            preps.append(f"{ev}|quest {p['quest']['name']}")
        else:  # the knowledge base words these: counted per event, never by their wording
            n[ev] = n.get(ev, 0) + 1
            preps.append(f"{ev}|prep {n[ev]}")
    out = {
        "moves": [move_key(m) for m in ov.get("moves") or []],
        "next_moves": [str(p.get("page_section") or "") for p in s.mcp("next_moves").get("answer") or []],
        "prep": preps,
        "effects": [f"{e.get('key')}|{c}" for e in (meta.get("meta") or {}).get("coming_up") or []
                    for c in range(1, len(e.get("for_you") or []) + 1)],
        "coming_up": [str(e.get("key")) for e in plan.get("coming_up") or []],
        "class_meta": [f"spec {x.get('spec') or 'class'}" for x in ((meta.get("meta") or {}).get("standing") or [])]
                      + (["build"] if (meta.get("meta") or {}).get("build") else []),
    }
    return {k: sorted(set(v)) for k, v in out.items()}


def current() -> dict[str, dict[str, list[str]]]:
    """Every golden character's keys, served the real way on the demo's clock."""
    from tests import degraded as D

    from . import characters as C
    from . import harness as H

    got = {}
    with D.clock(), tempfile.TemporaryDirectory() as tmp:
        for name, (char, rows) in C.GOLDEN.items():
            s = H.serve(char, rows(), Path(tmp) / name)
            try:
                got[name] = keys(s)
            finally:
                s.close()
    return got


def stored() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}


def diff(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """(gone, added): `character: part: key` lines."""
    gone, added = [], []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name) or {}, new.get(name) or {}
        for part in sorted(set(a) | set(b)):
            x, y = set(a.get(part) or ()), set(b.get(part) or ())
            gone += [f"{name}: {part}: {k}" for k in sorted(x - y)]
            added += [f"{name}: {part}: {k}" for k in sorted(y - x)]
    return gone, added


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="list what went away (exit 1) and what is new")
    g.add_argument("--write", action="store_true", help="store the current behaviour as the snapshot")
    ap.add_argument("--json", help="with --check: write {gone, added} here")
    a = ap.parse_args(argv)
    now = current()
    if a.write:
        PATH.write_text(json.dumps(now, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{PATH}: {sum(len(v) for c in now.values() for v in c.values())} keys")
        return 0
    gone, added = diff(stored(), now)
    if a.json:
        Path(a.json).write_text(json.dumps({"gone": gone, "added": added}, indent=1) + "\n", encoding="utf-8")
    for line in gone:
        print(f"gone: {line}")
    for line in added:
        print(f"new: {line}")
    return 1 if gone else 0


if __name__ == "__main__":
    sys.exit(main())
