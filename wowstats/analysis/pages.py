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
answer. The prep here is narrow on purpose: the feeds the asked models read
(ROWS_QUERY), not the whole history the analysis copies (positions, every
record). Each model's inputs are all in it: levels (the splits, the dings,
the session records' level and class, the sheets' class when the Recorder
never ran, and the kill and turn-in records that count a level without a
split), dungeon_runs (the runs), and records for deaths (the death records
with their likely killer, and the level and zone marks that place them).

The answer is kept per character and per rows (a digest): the same data asked
again (the snapshot of each level, the meta, an MCP tool calling the views)
reuses it, and one DuckDB copy is built at a time.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime

from wowstats.stores import SemanticError, iso, opt_num, parse_t

# What the pages' models read. LogsQL after the stream and character filters;
# the feeds also at the top level, so the event cache answers it (eventcache.scope).
# The character sheets (wow-character, wow-gear) for the class, race and side
# of a character the Recorder never saw (the route planner's time per class);
# a death's npc_id and npc_level for its likely killer (deaths_above...);
# the kill and turn-in records for the climb's counts at a level without a
# split (the level in play: climb).
ROWS_QUERY = ("feed:in(wow-level, wow-dungeon, wow-recorder, wow-character, wow-gear) "
              "(feed:in(wow-level, wow-dungeon) or kind:in(level, session, zone, death, kill, turnin) "
              "or (feed:in(wow-character, wow-gear) class:*)) "
              "| fields _time, feed, char, kind, level, zone, subzone, map_id, class, race, faction, seconds, xp, "
              "quests, kills, partial, name, dungeon_id, left, npc_id, npc_level, source_id, rev")
KEEP = 16
# DuckDB copies kept open for the next question on the same rows (asker):
# a tool and the Overview ask a few each.
COPIES = 4
_memo: OrderedDict = OrderedDict()
_copies: OrderedDict = OrderedDict()
_lock = threading.RLock()
# The operator's view of the layer (health(), the full view's /healthz):
# the characters whose last computation failed, with when. Names never leave
# this module: /healthz answers without a login.
_failing: dict[str, float] = {}
_last_ok: float | None = None


def _digest(char: str, rows: list[dict]) -> str:
    h = hashlib.sha256(char.encode())
    for r in sorted(json.dumps(r, sort_keys=True, default=str) for r in rows):
        h.update(r.encode())
    return h.hexdigest()


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


def numbers(char: str, rows: list[dict]) -> dict:
    """The page's numbers for `char` from `rows` (ROWS_QUERY's answer).
    Raises SemanticError when the layer fails: there is no other way to
    count them."""
    key = _digest(char, rows)
    with _lock:
        if key in _memo:
            _memo.move_to_end(key)
            return _memo[key]
    try:
        got = _compute(asker(char, rows, key))
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


def asker(char: str, rows: list[dict], key: str | None = None):
    """`ask(**query_metric's arguments) -> rows as dicts` on the narrow prep
    of `rows` (ROWS_QUERY's answer for `char`): the layer's own compiler and
    runner, for the tools and the Overview's rules that read a metric
    (wowstats.mcp.tools deaths and dungeons, wowstats.insights). The DuckDB
    copy is built once per rows (a digest) and kept for the next questions.
    A failure raises SemanticError (logged, and in health())."""
    from . import semantic

    key = key or _digest(char, rows)

    def ask(**q) -> list[dict]:
        try:
            layer, ro = _built(char, rows, key)
            with _lock:
                got = semantic.run(layer, ro, {"limit": semantic.MAX_LIMIT, **q})
            if got["more"]:  # never a cut list: the caller would count a part as the whole
                raise RuntimeError(f"more than {semantic.MAX_LIMIT} rows for {q.get('group_by')}")
        except SemanticError:
            raise
        except Exception as e:  # noqa: BLE001 - see numbers()
            raise _failed(char, e) from e
        return [dict(zip(got["columns"], r, strict=True)) for r in got["rows"]]

    ask.char = char
    return ask


def _built(char: str, rows: list[dict], key: str):
    """The layer and its read-only runner on the narrow prep of `rows`."""
    import warnings

    from wowstats.mcp import route

    from . import TABLES, duck, prep, semantic

    with _lock:
        if key in _copies:
            _copies.move_to_end(key)
            return _copies[key][1:]
        name = prep.norm(char)
        # The views' character filter may hold another spelling of the name: the rows are this character's.
        db = prep.build([{**r, "char": name} for r in rows], name, route.reference())
        con = duck.mirror(db, TABLES)
        db.close()
        try:
            semantic.prepare(con)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                layer = semantic.Layer(con)
        except Exception:
            con.close()
            raise
        _copies[key] = (con, layer, duck.ReadOnlyDuck(con))
        while len(_copies) > COPIES:
            _, (old, *_) = _copies.popitem(last=False)
            old.close()
        return _copies[key][1:]


def _compute(ask) -> dict:
    def one(metric: str, levels: list[int]):
        if not levels:
            return None
        got = ask(metrics=[metric], where=[{"dimension": "level", "op": "in", "values": levels}])
        return got[0][metric] if got else None

    split = {"dimension": "partial", "op": "is not null"}
    levels = {r["level"]: r for r in ask(metrics=["played_s", "level_xp", "split_quests", "split_kills",
                                                  "xp_per_hour"], group_by=["level", "partial"], where=[split])}
    done = sorted(lv for lv, r in levels.items() if r["partial"] == 0)
    totals = (ask(metrics=["played_s"], where=[split]) or [{}])[0]
    runs = {r["run"]: r for r in ask(metrics=["run_s", "run_xp", "run_xp_per_hour"], group_by=["run"])}
    return {"levels": levels, "totals": totals, "runs": runs, "deaths": deaths(ask), "counts": climb(ask),
            "avg_level_s": one("avg_level_s", done[-5:]), "avg_levels": len(done[-5:]),
            "pace_xp_per_hour": one("xp_per_hour", done[-3:])}


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
    included). since/until: query_metric's, local time."""
    when = {k: v for k, v in (("since", since), ("until", until)) if v}
    death = [{"dimension": "kind", "op": "=", "value": "death"}]
    return {"total": (ask(metrics=["deaths"], **when) or [{}])[0].get("deaths") or 0,
            "by_zone": {r["zone"]: r["deaths"] for r in ask(metrics=["deaths"], group_by=["zone"], where=death,
                                                            **when)},
            "by_level": {r["level"]: r["deaths"] for r in ask(metrics=["deaths"], group_by=["level"], where=death,
                                                              **when)}}


def whole(v) -> int | None:
    """A layer number as the page shows it: whole, rounded like wowstats.rates."""
    return None if v is None else round(v)


def apply_rows(levels: list[dict], dungeons: list[dict], n: dict) -> None:
    """The layer's numbers into the level splits and the dungeon runs (in place)."""
    for lv in levels:
        r = n["levels"].get(lv["level"])
        if r is not None:
            lv.update(seconds=int(r["played_s"] or 0), xp=int(r["level_xp"] or 0),
                      quests=opt_num(r["split_quests"]), kills=opt_num(r["split_kills"]),
                      xp_per_hour=whole(r["xp_per_hour"]))
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
            # The kills and quests with their population named (climb): never one bare total.
            "counts": n["counts"]}


def apply_deaths(de: dict, n: dict, rows: list[dict]) -> None:
    """The layer's death counts into views.build_deaths' answer (in place);
    `rows` are the death records newest first, for the order of the zones."""
    de["total"] = int(n["deaths"]["total"])
    # As the page lists them: most deaths first, then the zone of the newest death.
    newest: dict[str, int] = {}
    for i, r in enumerate(rows):
        newest.setdefault(r.get("zone") or "Unknown", i)
    by_zone = {(z or "Unknown"): int(c) for z, c in n["deaths"]["by_zone"].items()}
    de["by_zone"] = [{"zone": z, "n": c} for z, c in sorted(
        by_zone.items(), key=lambda x: (-x[1], newest.get(x[0], len(newest))))]
    de["by_level"] = {str(lv): int(c) for lv, c in n["deaths"]["by_level"].items() if lv is not None}
