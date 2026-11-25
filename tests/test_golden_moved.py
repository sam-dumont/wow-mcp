"""The golden snapshot's moved recommendations (tests/slices/golden.py
`moved`) and the refresh's `bump.py golden`, which takes only those: a
dungeon pick that swapped to another dungeon still in the character's list
is taken like a moved upgrade, a dungeon gone from the list still refuses
(owner, 2026-11-25: ForeverDB.net's data of that day moved the bare golden
character's pick from Maraudon: Orange to Uldaman and was refused)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from tests.slices import golden

ROOT = Path(__file__).resolve().parent.parent


def load_bump():
    spec = importlib.util.spec_from_file_location("kbtool_bump", ROOT / "tools" / "kb" / "bump.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def char(moves: list[str], dungeons: list[str]) -> dict:
    return {"moves": moves, "dungeons": dungeons, "prep": [], "next_moves": ["#advice"]}


OLD = {"bare": char(["dungeon|Maraudon: Orange", "dungeon|Razorfen Downs", "upgrade|10771"],
                    ["Maraudon: Orange", "Razorfen Downs", "Uldaman"])}


def check(old: dict, new: dict) -> dict:
    """What `golden --check --json` writes for the snapshot `old` and the keys `new`."""
    gone, added = golden.diff(old, new)
    return {"gone": gone, "added": added, "moved": golden.moved(gone, new)}


def test_a_swapped_dungeon_pick_moved_with_the_data():
    new = {"bare": char(["dungeon|Uldaman", "dungeon|Razorfen Downs", "upgrade|1713"],
                        ["Maraudon: Orange", "Razorfen Downs", "Uldaman"])}
    got = check(OLD, new)
    assert got["gone"] == ["bare: moves: dungeon|Maraudon: Orange", "bare: moves: upgrade|10771"]
    assert got["moved"] == got["gone"]


def test_a_dungeon_gone_from_the_list_is_lost_behaviour():
    new = {"bare": char(["dungeon|Uldaman", "dungeon|Razorfen Downs", "upgrade|10771"],
                        ["Razorfen Downs", "Uldaman"])}
    got = check(OLD, new)
    assert set(got["gone"]) == {"bare: moves: dungeon|Maraudon: Orange", "bare: dungeons: Maraudon: Orange"}
    assert got["moved"] == []


def test_a_pick_for_another_character_list_doesnt_count():
    # The pick left a dungeon that only another character's list still has.
    old = {**OLD, "other": char([], ["Maraudon: Orange"])}
    new = {"bare": char(["dungeon|Uldaman"], ["Razorfen Downs", "Uldaman"]), "other": char([], ["Maraudon: Orange"])}
    assert "bare: moves: dungeon|Maraudon: Orange" not in check(old, new)["moved"]


def run_bump(tmp_path, monkeypatch, new: dict) -> tuple[int, list, dict]:
    bump = load_bump()
    writes = []

    def fake_run(cmd, **kw):
        if "--check" in cmd:
            Path(cmd[cmd.index("--json") + 1]).write_text(json.dumps(check(OLD, new)), encoding="utf-8")
        elif "--write" in cmd:
            writes.append(cmd)
        return None

    monkeypatch.setattr(bump.subprocess, "run", fake_run)
    why = tmp_path / "why.json"
    rc = bump.main(["golden", "--as", "foreverdb", "--why", str(why)])
    return rc, writes, json.loads(why.read_text(encoding="utf-8")) if why.exists() else {}


def test_the_refresh_takes_a_swapped_dungeon_pick(tmp_path, monkeypatch):
    new = {"bare": char(["dungeon|Uldaman", "dungeon|Razorfen Downs", "upgrade|10771"],
                        ["Maraudon: Orange", "Razorfen Downs", "Uldaman"])}
    rc, writes, why = run_bump(tmp_path, monkeypatch, new)
    assert (rc, len(writes), why) == (0, 1, {})


def test_the_refresh_refuses_a_dungeon_that_left_the_data(tmp_path, monkeypatch):
    new = {"bare": char(["dungeon|Uldaman", "dungeon|Razorfen Downs", "upgrade|10771"], ["Razorfen Downs", "Uldaman"])}
    rc, writes, why = run_bump(tmp_path, monkeypatch, new)
    assert (rc, writes) == (3, [])
    assert "dungeons: Maraudon: Orange" in why["foreverdb"]
