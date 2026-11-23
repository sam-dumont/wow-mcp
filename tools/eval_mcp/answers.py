"""The MCP check's expected answers, computed from the demo character's data
(tools/fixtures/generate.py build_demo, as tests/degraded.py serves it), so
they follow the seed when it changes. Nothing here is typed by hand.

    uv run --extra mcp python tools/eval_mcp/answers.py     # prints them as JSON

Counts come from the raw events where they are plain (deaths, level
splits); the rest from the server's own answers on the same data (gear
scoring, the route, the dungeon ranking, the analysis tables), read in
process on the demo's frozen clock. The check asks whether a model finds
these through MCP, not whether the server computes them right: its own
tests do that.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import degraded as D  # noqa: E402
from wowstats.stores import parse_t  # noqa: E402

ARMOR = ("Head", "Shoulder", "Chest", "Waist", "Legs", "Feet", "Wrist", "Hands")


def server_env(vl_url: str, state_dir: str) -> dict:
    """The MCP server's whole environment, here and in server.py: the demo's
    store, no item tables (no network), no Battle.net, a throwaway state."""
    return {"VIEW": "full", "VL_URL": vl_url, "VM_URL": vl_url, "DEFAULT_CHAR": D.CHAR,
            "ANALYSIS_STATE_DIR": state_dir, "WOW_ITEMS": "off", "KB_GAME_TABLES": "off",
            "WOW_TIMEZONE": "Europe/Brussels", "FASTMCP_CHECK_FOR_UPDATES": "off"}


# ---------------------------------------------------------------- from the events


def level_at(rows: list[dict], t) -> int | None:
    """The level at time t: a split (wow-level) is written at the ding that
    ends its level, so t is past every split before it."""
    splits = sorted((parse_t(r["_time"]), int(r["level"])) for r in rows if r["feed"] == "wow-level")
    if not splits:
        return None
    done = [lv for at, lv in splits if at <= t]
    return max(done) + 1 if done else splits[0][1]


def deaths(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["feed"] == "wow-recorder" and r.get("kind") == "death"]


def deaths_at_level(rows: list[dict], level: int) -> int:
    return sum(1 for r in deaths(rows) if level_at(rows, parse_t(r["_time"])) == level)


def level_minutes(rows: list[dict], level: int) -> int:
    split = [r for r in rows if r["feed"] == "wow-level" and int(r["level"]) == level]
    if len(split) != 1:
        raise ValueError(f"level {level}: {len(split)} splits")
    return int(split[0]["seconds"]) // 60


def top_killer_id(rows: list[dict]) -> int:
    n = Counter(int(r["npc_id"]) for r in deaths(rows) if r.get("npc_id"))
    (top, k), *rest = n.most_common() or [(None, 0)]
    if top is None or (rest and rest[0][1] == k):
        raise ValueError(f"no single top killer: {n.most_common(3)}")
    return top


# ---------------------------------------------------------------- from the server's answers


class Tools:
    """The MCP tools in process, on the store at `vl_url` (the clock is the
    caller's: run under D.clock())."""

    def __init__(self, vl_url: str, state_dir: str):
        from wowstats.mcp import context, server

        self.server = server.build_server(server.Who(base=context(server_env(vl_url, state_dir))))

    def __call__(self, name: str, **args) -> dict:
        from fastmcp import Client

        async def go():
            async with Client(self.server) as c:
                return await c.call_tool(name, args, raise_on_error=False)

        r = asyncio.run(go())
        text = "".join(getattr(x, "text", "") for x in r.content)
        if r.is_error:
            raise RuntimeError(f"{name}: {text[:300]}")
        return json.loads(text)

    def sql(self, query: str) -> list[dict]:
        got = self("play_sql", char=D.CHAR, query=query, page_size=200)
        head, *lines = got["result"].split("\n")
        cols = head.split("\t")
        return [dict(zip(cols, ln.split("\t"), strict=True)) for ln in lines if ln]


def _one(items: list, what: str):
    if len(items) != 1:
        raise ValueError(f"{what}: {len(items)} candidates, want exactly 1: {items!r}"[:400])
    return items[0]


def longest_break_h(t: Tools) -> float:
    gaps = [float(r["longest_gap_s"]) for r in t.sql("SELECT longest_gap_s FROM levels") if r["longest_gap_s"]]
    return round(max(gaps) / 3600, 1)


def recorder_missed(t: Tools, level: int) -> str:
    row = _one(t.sql(f"SELECT recorder_missed_s FROM levels WHERE level = {int(level)}"), f"level {level}")
    return "Yes" if float(row["recorder_missed_s"] or 0) > 0 else "No"


def off_class_armor(t: Tools) -> str:
    rows = t.sql("SELECT slot_name, name, fit FROM gear WHERE fit = 'class' AND name IS NOT NULL")
    return _one([r["name"] for r in rows if r["slot_name"] in ARMOR], "worn armor made for another class")


def best_now(t: Tools, slot: str) -> str:
    s = _one([x for x in t("gear")["slots"] if x["slot"] == slot], f"slot {slot}")
    return s["best_now"]["item"]


def zone_quest_upgrade_line(t: Tools) -> str:
    """The quest upgrade in the current zone with the biggest jump, over
    everything the gear advice lists: `gear`'s shortlist (3 a slot) and
    `gear` with options (4 a slot, more slots). The demo has three in Arathi
    Highlands; asking for "the one" let a model pick any (eval-mcp
    2026-11-22: 18% → 47%, the boots gear_options listed first)."""
    lines: dict[int, str] = {}
    for u in t("gear", limit=40)["next_upgrades"]:
        if u.get("in_current_zone") and any(w.startswith("quest ") for w in u.get("where") or []):
            lines[u["item_id"]] = u["line"]
    for s in t("gear", options=True, limit=20)["slots"]:
        for o in s["options"]:
            if (o.get("in_current_zone") and o.get("kind") == "quest"
                    and lines.setdefault(o["item_id"], o["line"]) != o["line"]):
                raise ValueError(f"item {o['item_id']}: {lines[o['item_id']]!r} vs {o['line']!r}")
    pairs = {}
    for item, line in lines.items():
        m = re.match(r"\s*(\d+)%\s*→\s*(\d+)%", line)
        if not m:
            raise ValueError(f"no 'NN% → NN%' in {line!r}")
        pairs[item] = (int(m.group(2)) - int(m.group(1)), f"{m.group(1)}% → {m.group(2)}%")
    top = max((j for j, _ in pairs.values()), default=None)
    return _one([p for j, p in pairs.values() if j == top], "biggest quest upgrade in the current zone")


def next_guide_step(t: Tools) -> int:
    """next_step's first block, which play_sql's route_position must say
    too: a model that read the newest step record instead answered 45
    where next_step said 66 (eval-mcp 2026-11-22)."""
    first = t("next_step")["blocks"][0]["what"]
    m = re.search(r"steps? (\d+)", first)
    if not m:
        raise ValueError(f"no step number in the first block: {first!r}")
    sql = int(_one(t.sql("SELECT next_step FROM route_position"), "route_position row")["next_step"])
    if sql != int(m.group(1)):
        raise ValueError(f"next_step says {m.group(1)}, route_position says {sql}")
    return int(m.group(1))


def top_dungeon(t: Tools) -> str:
    return _one([d for d in t("dungeons", options=True)["dungeons"] if d.get("rank") == 1], "rank 1 dungeon")["name"]


def killer_name(t: Tools, npc_id: int) -> str:
    names = {(d.get("killer") or {}).get("name") for d in t("sanity_check")["deaths"]
             if (d.get("killer") or {}).get("npc_id") == npc_id}
    return _one(sorted(n for n in names if n), f"name of npc {npc_id}")


# ---------------------------------------------------------------- all of them


def compute(rows: list[dict] | None = None) -> dict:
    """Every answer key of questions.yaml: its value. Raises when the data
    no longer has one unambiguous answer for a question."""
    rows = D.rows_for("full") if rows is None else rows
    vl = D.VL(rows)
    try:
        with D.clock(), tempfile.TemporaryDirectory(prefix="eval-mcp-answers-") as state:
            t = Tools(vl.url, state)
            makers: dict[str, Callable[[], object]] = {
                "deaths_at_level_32": lambda: deaths_at_level(rows, 32),
                "longest_break_h": lambda: longest_break_h(t),
                "off_class_armor": lambda: off_class_armor(t),
                "best_feet_now": lambda: best_now(t, "Feet"),
                "next_guide_step": lambda: next_guide_step(t),
                "top_dungeon": lambda: top_dungeon(t),
                "recorder_missed_32": lambda: recorder_missed(t, 32),
                "zone_quest_upgrade_line": lambda: zone_quest_upgrade_line(t),
                "level_27_minutes": lambda: level_minutes(rows, 27),
                "top_killer": lambda: killer_name(t, top_killer_id(rows)),
            }
            return {k: f() for k, f in makers.items()}
    finally:
        vl.close()


if __name__ == "__main__":
    print(json.dumps(compute(), ensure_ascii=False, indent=1))
