"""The pages' numbers from the semantic layer: one definition for the pages
and the MCP's query_metric.

The character page's time per level, XP an hour (per level, the pace, per
dungeon run), the climb's kills and quests (completed levels, and the level
in play apart: climb), the average level and the deaths (how many, by zone,
by level) are metrics of wowstats/analysis/semantic.yaml. The views
(wowstats.views.Views.char) hand this module the rows those metrics read,
and take the numbers back; the facts around them (when a split ended, a
run's dungeon name, the newest deaths) stay the rows' own.

One computation path (owner, 2026-11-22: "use the semantic model all the
time"): DuckDB and the layer are core dependencies, and a failure raises
SemanticError, logged as one JSON line (`"event": "semantic_failed"`) and
counted in health() for /healthz. The page then shows its standard failure
state (the last good answer, marked stale); nothing counts the numbers
another way. A missing data source still gets partial answers, from the
layer: its metrics are NULL where nothing was recorded.

It runs when an answer is built (the full view's background builder, the
publisher's static builds), never per page view: a page view gets the built
answer. It reads the character's one analytical copy
(wowstats.analysis.copies), the same the MCP's tools and the planner read:
one prep of every event, one source of truth. A narrow prep of its own
(the feeds these models read) built a few times faster but was a second
copy of each character in memory, and its sessions had to be patched to end
where the full prep's do (in_level_s, 2026-11-23).

The answer is kept per copy (its character and the events it was built
from): the same data asked again (the snapshot of each level, the meta, an
MCP tool calling the views) reuses it.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime

from wowstats.stores import SemanticError, iso, opt_num, parse_t

KEEP = 16
_memo: OrderedDict = OrderedDict()
_lock = threading.RLock()
# The operator's view of the layer (health(), the full view's /healthz):
# the characters whose last computation failed, with when. Names never leave
# this module: /healthz answers without a login.
_failing: dict[str, float] = {}
_last_ok: float | None = None


def _failed(char: str, e: BaseException) -> SemanticError:
    """The structured log line and the health record of a failed
    computation; the error to raise. One computation path (owner,
    2026-11-22): the caller shows its failure state, never other numbers."""
    with _lock:
        _failing[char] = time.time()
    print(json.dumps({"event": "semantic_failed", "component": "wow", "level": "error", "char": char,
                      "error": repr(e)[:500]}, ensure_ascii=False), flush=True)
    return SemanticError(f"the semantic layer failed for {char}: {e!r}")


def _ok(char: str) -> None:
    global _last_ok
    with _lock:
        _failing.pop(char, None)
        _last_ok = time.time()


def health() -> dict:
    """The layer's state for /healthz: "failing" while a character's last
    computation failed (the page serves its last good answers, marked
    stale), "ok" once one succeeded, "idle" before the first. Counts and
    times only, no names."""
    with _lock:
        failing = dict(_failing)
        last_ok = _last_ok
    if failing:
        return {"semantic": "failing", "semantic_failing_chars": len(failing),
                "semantic_failed_at": iso(datetime.fromtimestamp(max(failing.values()), UTC))}
    return {"semantic": "ok" if last_ok is not None else "idle"}


def numbers(char: str, cp) -> dict:
    """The page's numbers for `char` from its copy `cp`
    (wowstats.analysis.copies.of; None: no events). Raises SemanticError
    when the layer fails: there is no other way to count them."""
    from . import copies

    key = ("none", char) if cp is None else (cp.key, cp.probe)
    with _lock:
        if key in _memo:
            _memo.move_to_end(key)
            return _memo[key]
    try:
        with copies.holding():
            got = _compute(asker(char, cp))
    except SemanticError:
        raise
    except Exception as e:  # noqa: BLE001 - any layer failure is the page's failure state, said once here
        raise _failed(char, e) from e
    _ok(char)
    with _lock:
        _memo[key] = got
        while len(_memo) > KEEP:
            _memo.popitem(last=False)
    return got


def asker(char: str, cp):
    """`ask(**query_metric's arguments) -> rows as dicts` on the character's
    copy `cp` (None: no events, every metric NULL): the layer's own compiler
    and runner, for the tools and the Overview's rules that read a metric
    (wowstats.mcp.tools deaths and dungeons, wowstats.insights). A failure
    raises SemanticError (logged, and in health())."""
    from . import semantic

    def ask(**q) -> list[dict]:
        try:
            got = (cp or _empty()).run({"limit": semantic.MAX_LIMIT, **q})
            if got["more"]:  # never a cut list: the caller would count a part as the whole
                raise RuntimeError(f"more than {semantic.MAX_LIMIT} rows for {q.get('group_by')}")
        except SemanticError:
            raise
        except Exception as e:  # noqa: BLE001 - see numbers()
            raise _failed(char, e) from e
        return [dict(zip(got["columns"], r, strict=True)) for r in got["rows"]]

    ask.char = char
    return ask


def _empty():
    """A copy of an empty prep, for a character with no events: every metric
    NULL (unknown), never 0 (copies.empty)."""
    from . import copies

    return copies.empty()


def pace_xp_per_hour(ask, levels: dict | None = None) -> float | None:
    """The XP an hour over the last three finished levels: the "Next level in"
    tile's pace. `levels`: the splits' rows when the caller has them."""
    split = {"dimension": "partial", "op": "is not null"}
    if levels is None:
        levels = {r["level"]: r for r in ask(metrics=["xp_per_hour"], group_by=["level", "partial"], where=[split])}
    done = sorted(lv for lv, r in levels.items() if r["partial"] == 0)[-3:]
    if not done:
        return None
    got = ask(metrics=["xp_per_hour"], where=[{"dimension": "level", "op": "in", "values": done}])
    return got[0]["xp_per_hour"] if got else None


def _compute(ask) -> dict:
    def one(metric: str, levels: list[int]):
        if not levels:
            return None
        got = ask(metrics=[metric], where=[{"dimension": "level", "op": "in", "values": levels}])
        return got[0][metric] if got else None

    split = {"dimension": "partial", "op": "is not null"}
    levels = {r["level"]: r for r in ask(metrics=["played_s", "planned_s", "level_xp", "split_quests", "split_kills",
                                                  "xp_per_hour"], group_by=["level", "partial", "ding_t"],
                                         where=[split])}
    done = sorted(lv for lv, r in levels.items() if r["partial"] == 0)
    totals = (ask(metrics=["played_s"], where=[split]) or [{}])[0]
    runs = {r["run"]: r for r in ask(metrics=["run_s", "run_xp", "run_xp_per_hour"], group_by=["run"])}
    return {"levels": levels, "totals": totals, "runs": runs, "deaths": deaths(ask), "counts": climb(ask),
            "in_level_s": in_level(ask),
            "avg_level_s": one("avg_level_s", done[-5:]), "avg_levels": len(done[-5:]),
            "pace_xp_per_hour": pace_xp_per_hour(ask, levels)}


def in_level(ask) -> int | None:
    """The level in play's time so far (in_level_s): the page's hero line
    and the MCP's character_summary. None when it can't be measured."""
    got = ask(metrics=["in_level_s"], where=[{"dimension": "in_play", "op": "=", "value": 1}])
    v = got[0]["in_level_s"] if got else None
    return None if v is None else int(v)


def climb(ask) -> dict:
    """The climb's kills and quests in their two populations, one
    definition for the page's hero line and the MCP's character_summary
    (climb_kills, climb_quests by in_play): the completed levels (Polestar's
    split counts, the Recorder's records for a level without one), the
    current level so far (None when no row says what it is), and both
    together. A count nobody kept is None, never 0."""
    def n(v):
        return None if v is None else int(v)

    by = {r["in_play"]: r for r in ask(metrics=["climb_kills", "climb_quests"], group_by=["in_play"])}
    lv = ask(metrics=["climb_kills"], group_by=["level", "in_play"])
    done = sorted(int(r["level"]) for r in lv if r["in_play"] == 0)
    cur = next((int(r["level"]) for r in lv if r["in_play"] == 1), None)
    both = (ask(metrics=["climb_kills", "climb_quests"]) or [{}])[0]
    c, p = by.get(0), by.get(1)
    return {"completed_levels": {"from_level": done[0], "to_level": done[-1], "kills": n(c["climb_kills"]),
                                 "quests": n(c["climb_quests"])} if c and done else None,
            "current_level": {"level": cur, "kills": n(p["climb_kills"]), "quests": n(p["climb_quests"])}
            if p and cur is not None else None,
            "including_current_level": {"kills": n(both.get("climb_kills")), "quests": n(both.get("climb_quests"))}}


def deaths(ask, since: str | None = None, until: str | None = None) -> dict:
    """The deaths metric: in all, by zone and by level (the layer places a
    death at the level it happened, a record without a level of its own
    included). since/until: query_metric's, local time. The total is None
    when no Recorder record falls in the window: nobody counted, never 0."""
    when = {k: v for k, v in (("since", since), ("until", until)) if v}
    death = [{"dimension": "kind", "op": "=", "value": "death"}]
    total = (ask(metrics=["deaths"], **when) or [{}])[0].get("deaths")
    return {"total": None if total is None else int(total),
            "by_zone": {r["zone"]: r["deaths"] for r in ask(metrics=["deaths"], group_by=["zone"], where=death,
                                                            **when)},
            "by_level": {r["level"]: r["deaths"] for r in ask(metrics=["deaths"], group_by=["level"], where=death,
                                                              **when)}}


def at_utc(t) -> str | None:
    """Unix seconds as the store writes a time (UTC, milliseconds): a split's `at`."""
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def whole(v) -> int | None:
    """A layer number as the page shows it: whole, rounded like wowstats.rates."""
    return None if v is None else round(v)


def apply_rows(levels: list[dict], dungeons: list[dict], n: dict) -> None:
    """The layer's numbers into the level splits and the dungeon runs (in
    place). A level the layer times without a split (the game's /played at
    its two starts: a split lost to a crash) joins the list, in level order;
    one whose split was partial is whole with it."""
    have = {lv["level"] for lv in levels}
    for lvl, r in n["levels"].items():
        if lvl not in have and r["played_s"] is not None:
            levels.append({"level": lvl, "seconds": None, "xp": None, "quests": None, "kills": None,
                           "partial": False, "at": at_utc(r.get("ding_t")), "xp_per_hour": None})
    levels.sort(key=lambda lv: lv["level"])
    for lv in levels:
        r = n["levels"].get(lv["level"])
        if r is not None:
            lv.update(seconds=int(r["played_s"] or 0), route_s=whole(r.get("planned_s")), xp=int(r["level_xp"] or 0),
                      quests=opt_num(r["split_quests"]), kills=opt_num(r["split_kills"]),
                      xp_per_hour=whole(r["xp_per_hour"]), partial=bool(r["partial"]))
    for d in dungeons:
        r = n["runs"].get(parse_t(d["t"]).timestamp())
        if r is not None:
            d.update(seconds=int(r["run_s"] or 0), xp=int(r["run_xp"] or 0), xp_per_hour=whole(r["run_xp_per_hour"]))


def header(n: dict) -> dict:
    """The hero line's numbers (views.build_header takes them as they are)."""
    t = n["totals"]
    return {"splits_played_s": None if t.get("played_s") is None else int(t["played_s"]),
            "avg_level_s": None if n["avg_level_s"] is None else int(n["avg_level_s"]),
            "avg_levels": n["avg_levels"], "xp_per_hour": whole(n["pace_xp_per_hour"]),
            "in_level_s": n["in_level_s"],
            # The kills and quests with their population named (climb): never one bare total.
            "counts": n["counts"]}


def apply_deaths(de: dict, n: dict, rows: list[dict]) -> None:
    """The layer's death counts into views.build_deaths' answer (in place);
    `rows` are the death records newest first, for the order of the zones."""
    de["total"] = n["deaths"]["total"]
    # As the page lists them: most deaths first, then the zone of the newest death.
    newest: dict[str, int] = {}
    for i, r in enumerate(rows):
        newest.setdefault(r.get("zone") or "Unknown", i)
    by_zone = {(z or "Unknown"): int(c) for z, c in n["deaths"]["by_zone"].items()}
    de["by_zone"] = [{"zone": z, "n": c} for z, c in sorted(
        by_zone.items(), key=lambda x: (-x[1], newest.get(x[0], len(newest))))]
    de["by_level"] = {str(lv): int(c) for lv, c in n["deaths"]["by_level"].items() if lv is not None}
