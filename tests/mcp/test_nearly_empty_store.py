"""A player who has just started: one session, one XP record without
`xp_gained`, no level-up, no deaths. VictoriaLogs answers a sum() or max()
over rows without the field as "NaN"; every typed tool has to answer such a
store without raising and without a NaN in its JSON (the `zones` tool crashed
on one, 2026-11-22). Synthetic rows."""

from __future__ import annotations

import json
import re

import pytest

from tests.mcp import fakes
from wowstats.mcp import tools
from wowstats.mcp.store import Store

CHAR = "Newbie-Fixture"
T = "2026-11-20T18:00:00.000Z"
ONE_XP = {"_time": T, "kind": "xp", "level": 2, "xp": 40, "xp_max": 400, "zone": "Elwynn Forest", "char": CHAR}
SNAP = {"_time": T, "level": 2, "xp": 40, "xp_max": 400, "money_copper": 120, "zone": "Elwynn Forest"}
NAN_STATS = {"n": "NaN", "xp": "NaN", "secs": "NaN", "visits": "NaN", "seconds": "NaN", "duration_s": "NaN",
             "value": "NaN", "kind": "xp", "zone": "Elwynn Forest"}

# First needle contained in the query wins (tests/mcp/fakes.py).
LOGS = [
    ("count_uniq(source_id) as n", []),
    ("max(_time) as last_seen", [{"char": CHAR, "last_seen": T}]),
    (" | stats ", [NAN_STATS]),
    ("feed:wow-character", [SNAP]),
    ("", [ONE_XP]),
]


@pytest.fixture(scope="module")
def ctx():
    fake = fakes.FakeStores(logs=LOGS, metrics=[])
    yield tools.Ctx(store=Store(vl_url=fake.url, vm_url=fake.url), default_char=CHAR)
    fake.close()


def _args(tool) -> dict:
    sch = tool.input_schema
    out = {}
    for k in sch.get("required", []):
        t = sch["properties"][k].get("type")
        out[k] = {"integer": 1, "number": 1, "boolean": False, "array": [], "object": {}}.get(t, "a")
    if "char" in sch["properties"]:
        out["char"] = CHAR
    return out


def _nan_paths(v, path="$"):
    if isinstance(v, float) and v != v or isinstance(v, float) and v in (float("inf"), float("-inf")) or isinstance(v, str) and re.fullmatch(r"[+-]?(nan|inf(inity)?)", v.strip(), re.I):
        yield path
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _nan_paths(x, f"{path}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from _nan_paths(x, f"{path}[{i}]")


@pytest.mark.parametrize("tool", [t for t in tools.TOOLS if not t.writes], ids=lambda t: t.name)
def test_every_tool_survives_a_nearly_empty_store(ctx, tool):
    try:
        out = tools.call(ctx, tool.name, _args(tool))
    except tools.ToolError:
        return  # a said "nothing here" is an answer, not a crash
    json.dumps(out, allow_nan=False)
    assert list(_nan_paths(out)) == []


def test_setup_report_reads_a_nan_saved_count_as_unknown():
    from wowstats import missing

    for bad in ("NaN", "Infinity", "", None):
        assert missing.opt_num(bad) is None
    assert missing.opt_num("3") == 3
