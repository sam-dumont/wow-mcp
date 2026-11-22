"""The pages' numbers from the semantic layer: one definition for the pages
and the MCP's query_metric.

The character page's time per level, XP an hour (per level, the pace, per
dungeon run), the kill and quest totals, the average level and the deaths
(how many, by zone, by level) are metrics of wowstats/analysis/semantic.yaml.
The views (wowstats.views.Views.char) hand this module the rows those
metrics read, and take the numbers back; the facts around them (when a split
ended, a run's dungeon name, the newest deaths) stay the rows' own.

It runs when an answer is built (the full view's background builder, the
publisher's static builds), never per page view: a page view gets the built
answer. The prep here is narrow on purpose: the feeds the asked models read
(ROWS_QUERY), not the whole history the analysis copies (positions, every
record), so the publisher holds a few thousand rows, not a year of records.
Each model's inputs are all in it: levels (the splits, the dings, the
session records' level and class, the sheets' class when the Recorder never
ran), dungeon_runs (the runs), and records for deaths (the death records
with their likely killer, and the level and zone marks that place them).

The answer is kept per character and per rows (a digest): the same data asked
again (the snapshot of each level, the meta, an MCP tool calling the views)
reuses it, and one DuckDB copy is built at a time.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict

from wowstats.stores import opt_num, parse_t

# What the pages' models read. LogsQL after the stream and character filters;
# the feeds also at the top level, so the event cache answers it (eventcache.scope).
# The character sheets (wow-character, wow-gear) for the class, race and side
# of a character the Recorder never saw (the route planner's time per class);
# a death's npc_id and npc_level for its likely killer (deaths_above...).
ROWS_QUERY = ("feed:in(wow-level, wow-dungeon, wow-recorder, wow-character, wow-gear) "
              "(feed:in(wow-level, wow-dungeon) or kind:in(level, session, zone, death) "
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
_missing_said = False


def available() -> bool:
    try:
        import boring_semantic_layer  # noqa: F401
        import duckdb  # noqa: F401
    except ImportError:
        return False
    return True


def _digest(char: str, rows: list[dict]) -> str:
    h = hashlib.sha256(char.encode())
    for r in sorted(json.dumps(r, sort_keys=True, default=str) for r in rows):
        h.update(r.encode())
    return h.hexdigest()


def numbers(char: str, rows: list[dict]) -> dict | None:
    """The page's numbers for `char` from `rows` (ROWS_QUERY's answer), or
    None when the layer isn't installed (the `mcp` extra: the image has it)."""
    if not _ready():
        return None
    key = _digest(char, rows)
    with _lock:
        if key in _memo:
            _memo.move_to_end(key)
            return _memo[key]
    try:
        got = _compute(asker(char, rows, key))
    except Exception as e:  # noqa: BLE001 - a layer bug must not take the page down: it counts as before
        print(f"pages: the semantic layer failed for {char}, the page counts its numbers itself: {e!r}",
              flush=True)
        return None
    with _lock:
        _memo[key] = got
        while len(_memo) > KEEP:
            _memo.popitem(last=False)
    return got


def _ready() -> bool:
    global _missing_said
    if available():
        return True
    if not _missing_said:
        _missing_said = True
        print("pages: DuckDB and the semantic layer are not installed (the mcp extra): "
              "the pages count their numbers themselves", flush=True)
    return False


def asker(char: str, rows: list[dict], key: str | None = None):
    """`ask(**query_metric's arguments) -> rows as dicts` on the narrow prep
    of `rows` (ROWS_QUERY's answer for `char`): the layer's own compiler and
    runner, for the tools and the Overview's rules that read a metric
    (wowstats.mcp.tools deaths and dungeons, wowstats.insights). The DuckDB
    copy is built once per rows (a digest) and kept for the next questions.
    None when the layer isn't installed."""
    if not _ready():
        return None
    from . import semantic

    key = key or _digest(char, rows)

    def ask(**q) -> list[dict]:
        layer, ro = _built(char, rows, key)
        with _lock:
            got = semantic.run(layer, ro, {"limit": semantic.MAX_LIMIT, **q})
        if got["more"]:  # never a cut list: the caller would count a part as the whole
            raise RuntimeError(f"more than {semantic.MAX_LIMIT} rows for {q.get('group_by')}")
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
    totals = (ask(metrics=["played_s", "split_kills", "split_quests"], where=[split]) or [{}])[0]
    runs = {r["run"]: r for r in ask(metrics=["run_s", "run_xp", "run_xp_per_hour"], group_by=["run"])}
    return {"levels": levels, "totals": totals, "runs": runs, "deaths": deaths(ask),
            "avg_level_s": one("avg_level_s", done[-5:]), "avg_levels": len(done[-5:]),
            "pace_xp_per_hour": one("xp_per_hour", done[-3:])}


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
            "kills": None if t.get("split_kills") is None else int(t["split_kills"]),
            "quests": None if t.get("split_quests") is None else int(t["split_quests"])}


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
