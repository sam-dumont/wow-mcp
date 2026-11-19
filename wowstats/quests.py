"""Which quests a character has done: the one answer every page and tool
uses (the Dungeons panel's quests and blockers, the plan, the gear advice,
the MCP).

The Recorder only sees the quests handed in while it runs, and every
friend installs it mid-levelling, so a quest done before that read as not
done (owner, 2026-11-19: The Stockade's Bazil Thredd "blocked" by The
Unsent Letter, handed in before Polestar was installed). A quest counts as
done when any of these says so:

- `turnin`: the Recorder saw it handed in;
- `flagged`: the game said so to the Recorder (an `offer` record's
  quests_done: the quests of that NPC the client flags completed);
- `questie`, `altoholic`: the whole set the game keeps, as Questie or
  Altoholic's DataStore_Quests last saved it (the shipper's wow-quests
  feed, docs/contract.md). Questie's is exact but only as fresh as its
  last login; DataStore's numbers lose their low bits to rounding, so the
  shipper sends only the bits it can trust;
- `reward`: the character holds (worn, bags, bank) gear that only that
  quest gives (data/forever-reference.json `rewards`);
- `inferred`: a quest it had to hand in first to get one of the above
  (forever-db's prerequisites, every one of an "all" list, the one quest
  of a one-quest "any" list), and so on down the chain. This holds even
  with no addon that saves the whole set.

Battle.net's profile API has the whole set too (/quests/completed in
profile-forever-*, checked 2026-11-19), but the planner never reads
Battle.net data (its plans are kept past the API terms' 30 days), so it
isn't a source here.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from wowstats import forever

# In order of trust: a quest some source names keeps the first one.
SOURCES = ("turnin", "flagged", "questie", "altoholic", "reward", "inferred")
SNAPSHOT_SOURCES = ("questie", "altoholic")

# LogsQL pipes after a character's Recorder filter, and after a stream and
# character filter: what views and the MCP read for completed().
Q_RECORDS = "kind:in(turnin, offer) | stats by (kind, quest_id, quests_done) count() as n"
Q_SNAPSHOTS = ('feed:="wow-quests" {cf} kind:=completed | fields _time, feed, kind, source, quest_ids '
               "| sort by (_time desc) | limit 20")


def _int(v) -> int | None:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _ids(v) -> list[int]:
    """Quest ids as a record carries them: a list, a JSON list (nested
    values are stored as JSON strings), or the Recorder's "1;2;3"."""
    if isinstance(v, str):
        v = v.strip()
        if v.startswith("["):
            try:
                v = json.loads(v)
            except ValueError:
                return []
        else:
            v = v.replace(",", ";").split(";")
    if not isinstance(v, list):
        return []
    return [n for x in v if (n := _int(x)) is not None]


def needs_first(qid: int) -> tuple[list[int], list[int]]:
    """forever-db's prerequisites of a quest: ([every one of these], [any one
    of these]), breadcrumbs left out."""
    all_, any_ = ((forever.data() or {}).get("pre") or {}).get(str(qid)) or [[], []]
    return list(all_), list(any_)


def reward_quest(item_id) -> int | None:
    """The one quest that gives this item, when nothing else does
    (forever-reference `rewards`), else None."""
    n = _int(item_id)
    return ((forever.data() or {}).get("rewards") or {}).get(str(n)) if n else None


def close(done: Iterable[int]) -> set[int]:
    """The quests a character must have handed in to have done `done`: an
    "all" prerequisite always, an "any" one when it is the only one, down
    the chain. `done` itself is not in the result."""
    out: set[int] = set()
    todo = [q for q in done if q]
    seen = set(todo)
    while todo:
        all_, any_ = needs_first(todo.pop())
        for p in [*all_, *(any_ if len(any_) == 1 else [])]:
            if p not in seen:
                seen.add(p)
                out.add(p)
                todo.append(p)
    return out


def evidence(rows: Iterable[dict], held: Iterable[int] | None = None) -> dict[str, set[int]]:
    """Per source (SOURCES but `inferred`), the quests it says are done,
    from a character's stored events: Recorder records (or Q_RECORDS'
    stats rows), wow-quests snapshots (all of them: a completed quest stays
    completed, and DataStore's unreadable bits differ from one save to the
    next), and, when `held` is None, the newest wow-gear and wow-bags for what it
    holds."""
    from wowstats.stores import parse_t

    ev: dict[str, set[int]] = {s: set() for s in SOURCES if s != "inferred"}
    gear_row = bags_row = None
    for r in rows:
        kind, feed = r.get("kind"), r.get("feed")
        if kind == "turnin":
            if (q := _int(r.get("quest_id"))) is not None:
                ev["turnin"].add(q)
        elif kind == "offer":
            ev["flagged"].update(_ids(r.get("quests_done")))
        elif feed == "wow-quests" and kind == "completed" and r.get("source") in SNAPSHOT_SOURCES:
            ev[r["source"]].update(_ids(r.get("quest_ids")))
        elif held is None and feed == "wow-gear" and (
                gear_row is None or parse_t(r.get("_time")) > parse_t(gear_row.get("_time"))):
            gear_row = r
        elif held is None and feed == "wow-bags" and (
                bags_row is None or parse_t(r.get("_time")) > parse_t(bags_row.get("_time"))):
            bags_row = r
    if held is None:
        held = set()
        if gear_row is not None:
            items = gear_row.get("items")
            if isinstance(items, str):
                try:
                    items = json.loads(items)
                except ValueError:
                    items = []
            held |= {n for i in items or [] if isinstance(i, dict) and (n := _int(i.get("item_id")))}
        if bags_row is not None:
            for k in ("bags", "bank"):
                held |= set(_ids(bags_row.get(k)))
    ev["reward"] = {q for i in held if (q := reward_quest(i)) is not None}
    return ev


def completed(rows: Iterable[dict], held: Iterable[int] | None = None) -> dict[int, str]:
    """Every quest the character has done -> the first source (SOURCES
    order) that says so. The keys are the answer to "is this quest done?"."""
    out: dict[int, str] = {}
    for src, ids in evidence(rows, held).items():
        for q in sorted(ids):
            out.setdefault(q, src)
    for q in sorted(close(out)):
        out.setdefault(q, "inferred")
    return out


def counts(done: dict[int, str]) -> dict[str, int]:
    """How many quests each source gave (for the MCP: where "done" comes from)."""
    out = dict.fromkeys(SOURCES, 0)
    for src in done.values():
        out[src] += 1
    return {k: v for k, v in out.items() if v}
