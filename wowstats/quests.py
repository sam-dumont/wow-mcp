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
- `reward`: the character holds or held (worn, bags, bank, on any gear or
  bags snapshot) gear that only that quest gives
  (data/forever-reference.json `rewards`);
- `battlenet`: the same, from what Battle.net says it wears now (the
  friend without Syndicator has no sheet at all). Read live at query time
  and never kept: Blizzard's API terms allow 30 days, so it never enters
  the history, the analysis' database, an export or a cache longer than
  the readers' minute (wowstats.views.Views.quests_done, wowstats.plan's
  two-minute inputs, keyed by the items). Last in trust: it only adds
  quests the history doesn't have, never removes or relabels one;
- `inferred`: a quest it had to hand in first to get one of the above
  (forever-db's prerequisites, every one of an "all" list, the one quest
  of a one-quest "any" list), and so on down the chain. This holds even
  with no addon that saves the whole set.

A quest the Recorder saw accepted but never handed in stays in its log
for good when the hand-in happened with the Recorder off. Any other source
that says it is done closes it as INFERRED, "done (inferred)", with the
reason (explain, settles_open): the analysis' quests table and the
planner's open quests both read that one answer (wowstats.analysis.prep).
No addon the kit reads saves the quest log itself, so "gone from the log"
is not a source.

Every reader gets the same answer: Gear, the Overview, next steps and the
MCP through Views.quests_done; the planner and the Dungeons panel through
wowstats.plan.facts.gather, from the same history plus the same live
Battle.net equipment. The analysis' database (wowstats.analysis.prep)
keeps the history alone.

Battle.net's profile API has the whole set too (/quests/completed in
profile-forever-*, checked 2026-11-19); the exporter doesn't read it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from wowstats import forever

# In order of trust: a quest some source names keeps the first one.
SOURCES = ("turnin", "flagged", "questie", "altoholic", "reward", "battlenet", "inferred")
SNAPSHOT_SOURCES = ("questie", "altoholic")

# LogsQL pipes after a character's Recorder filter, and after a stream and
# character filter: what views and the MCP read for completed().
Q_RECORDS = "kind:in(turnin, offer) | stats by (kind, quest_id, quests_done) count() as n"
# Every snapshot, like the planner, which reads every event: DataStore's
# saves lose different low bits from one to the next, and a Questie reset
# starts a smaller set, so no newest-N holds them all. Review 2 of 0.7.0:
# the newest 20 here missed a quest only the 25th-newest listed. The shipper
# sends one per changed set (a few a play day); 1,000 of 800 ids each take
# about 0.25 s in completed(), cached a minute by the Views.
Q_SNAPSHOTS = 'feed:="wow-quests" {cf} kind:=completed | fields _time, at_basis, feed, kind, source, quest_ids'
# Every gear and bags sheet, not just the newest: a reward sold or replaced
# since still proves the hand-in. The Gear page once read the newest alone
# and offered Violet Sash from Power Overwhelming again once it was gone
# (#33's case), while the planner, reading the whole history, had it done.
# What the character owns now is wowstats.gear's Q_SHEET and Q_HELD.
Q_SHEETS = 'feed:in("wow-gear", "wow-bags") {cf} | fields _time, at_basis, feed, items, bags, bank'


def history(run, rec: str, cf: str) -> dict[int, str]:
    """completed() from a character's history, for the live readers (the
    web Views, and through them the MCP): `run(query)` returns the rows of
    a LogsQL query after the stream filter, `rec` is the character's
    Recorder filter, `cf` its character filter. The planner reads every
    event (wowstats.analysis.prep.query): the same evidence."""
    return completed([r for q in (f"{rec} {Q_RECORDS}", Q_SNAPSHOTS.format(cf=cf), Q_SHEETS.format(cf=cf))
                      for r in run(q)])


def worn_ids(worn: dict | None) -> list[int]:
    """The item ids of Battle.net's equipment as wowstats.gear.armory_worn
    gives it ({slot: {"item_id", ...}}), for the `battlenet` source."""
    return sorted({n for r in (worn or {}).values() if isinstance(r, dict) and (n := _int(r.get("item_id")))})


def with_battlenet(done: dict[int, str], worn: Iterable[int] | None) -> dict[int, str]:
    """`done` (completed() of the history) plus the quests Battle.net's
    worn items prove (and what they need first): the history keeps every
    quest and source it has, Battle.net only adds."""
    return {**completed([], battlenet=worn), **done} if worn else dict(done)


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


# The status a quest log row gets when the log still holds its accept but
# another source says the quest was handed in (wowstats.analysis.prep's
# quests table, and through it the planner's open quests).
INFERRED = "done (inferred)"
_WHERE = {"gear": "worn", "bags": "in the bags", "bank": "in the bank"}


def _found(rows: Iterable[dict], held: Iterable[int] | None,
           battlenet: Iterable[int] | None = None) -> dict[str, dict[int, dict]]:
    """Per source (SOURCES but `inferred`), quest -> {why, t, last_t, t_by}:
    what says it is done, and the first and last time it was seen saying so
    (unix s; None when the record has no time, like a `held` list). `t_by`:
    `t` is an upper bound, not the moment. A snapshot the shipper rebuilt
    from an archive without capture times carries `at_basis: "commit"`: its
    `_time` is when the archive captured it, so the game saw it by then, at
    the latest (docs/contract.md, "Times"). An upper bound still settles
    "seen after the accept" the safe way: a time before the accept proves
    it, one after leaves it as unknown as no time at all (settles_open)."""
    from wowstats.stores import parse_t

    ev: dict[str, dict[int, dict]] = {s: {} for s in SOURCES if s != "inferred"}

    def add(src: str, q: int, why: str, t: float | None, by: bool = False) -> None:
        e = ev[src].get(q)
        if e is None:
            ev[src][q] = {"why": why, "t": t, "last_t": t, "t_by": by and t is not None}
            return
        if t is not None:
            if e["t"] is None or t < e["t"]:
                e.update(why=why, t=t, t_by=by)
            if e["last_t"] is None or t > e["last_t"]:
                e["last_t"] = t

    def when(r: dict) -> float | None:
        d = parse_t(r.get("_time"))
        return d.timestamp() if d.year > 1 else None

    def day(t: float | None, by: bool = False) -> str:
        from datetime import UTC, datetime
        return f" {'by' if by else 'on'} {datetime.fromtimestamp(t, UTC):%Y-%m-%d}" if t is not None else ""

    def reward(item: int, where: str, t: float | None, src: str = "reward", by: bool = False) -> None:
        if (q := reward_quest(item)) is not None:
            name = ((forever.data() or {}).get("items") or {}).get(str(item)) or f"item {item}"
            add(src, q, f"{name} ({item}) {where}{day(t, by)}: a reward only this quest gives", t, by)

    for r in rows:
        kind, feed, t = r.get("kind"), r.get("feed"), when(r)
        by = r.get("at_basis") == "commit"
        if kind == "turnin":
            if (q := _int(r.get("quest_id"))) is not None:
                add("turnin", q, "handed in (the Recorder saw it)", t)
        elif kind == "offer":
            why = f"the game flagged it completed{day(t)}"
            for q in _ids(r.get("quests_done")):
                add("flagged", q, why, t)
        elif feed == "wow-quests" and kind == "completed" and r.get("source") in SNAPSHOT_SOURCES:
            # Every snapshot, each a full set of up to ~1,000 ids: the reason is
            # worded once per snapshot, not per quest (review 2 of 0.7.0).
            who = "Questie" if r["source"] == "questie" else "Altoholic (DataStore_Quests)"
            why = f"{who} lists it completed{day(t, by)}"
            for q in _ids(r.get("quest_ids")):
                add(r["source"], q, why, t, by)
        elif held is None and feed == "wow-gear":
            # Every sheet, not just the newest: a reward sold or banked
            # since still proves the hand-in.
            items = r.get("items")
            if isinstance(items, str):
                try:
                    items = json.loads(items)
                except ValueError:
                    items = []
            for i in items or []:
                if isinstance(i, dict) and (n := _int(i.get("item_id"))):
                    reward(n, _WHERE["gear"], t, by=by)
        elif held is None and feed == "wow-bags":
            for k in ("bags", "bank"):
                for n in _ids(r.get(k)):
                    reward(n, _WHERE[k], t, by=by)
    for n in held or []:
        if (n := _int(n)) is not None:
            reward(n, "held (worn, bags or bank)", None)
    for n in battlenet or []:
        if (n := _int(n)) is not None:
            reward(n, "worn, as Battle.net says now", None, "battlenet")
    return ev


def evidence(rows: Iterable[dict], held: Iterable[int] | None = None) -> dict[str, set[int]]:
    """Per source (SOURCES but `inferred`), the quests it says are done,
    from a character's stored events: Recorder records (or Q_RECORDS'
    stats rows), wow-quests snapshots (all of them: a completed quest stays
    completed, and DataStore's unreadable bits differ from one save to the
    next), and, when `held` is None, every wow-gear and wow-bags snapshot
    for what it holds or held."""
    return {src: set(qs) for src, qs in _found(rows, held).items()}


def explain(rows: Iterable[dict], held: Iterable[int] | None = None,
            battlenet: Iterable[int] | None = None) -> dict[int, dict]:
    """completed() with the reason: quest -> {from: the first source (SOURCES
    order) that says so, why: the evidence in words, t / last_t: the first
    and last time that source said so (unix s, None when unknown), t_by:
    True when `t` is only "by then" (an approximate capture time, _found)}.
    `battlenet`: the item ids Battle.net says it wears now (worn_ids)."""
    out: dict[int, dict] = {}
    for src, qs in _found(rows, held, battlenet).items():
        for q in sorted(qs):
            if q not in out:
                out[q] = {"from": src, **qs[q]}
    for q in sorted(close(out)):
        out.setdefault(q, {"from": "inferred", "why": "a quest that needs it first is done (forever-db prerequisites)",
                           "t": None, "last_t": None, "t_by": False})
    return out


def completed(rows: Iterable[dict], held: Iterable[int] | None = None,
              battlenet: Iterable[int] | None = None) -> dict[int, str]:
    """Every quest the character has done -> the first source (SOURCES
    order) that says so. The keys are the answer to "is this quest done?"."""
    return {q: e["from"] for q, e in explain(rows, held, battlenet).items()}


def settles_open(e: dict | None, accepted_t: float | None) -> bool:
    """Whether evidence (an explain() entry) closes a quest the log still
    holds since `accepted_t`: any source but the Recorder's own hand-in
    (that one closes it in time order, so a hand-in before the accept is a
    repeat picked up again), seen at or after the accept when it has a time."""
    if not e or e["from"] == "turnin":
        return False
    return e["last_t"] is None or accepted_t is None or e["last_t"] >= accepted_t


def counts(done: dict[int, str]) -> dict[str, int]:
    """How many quests each source gave (for the MCP: where "done" comes from)."""
    out = dict.fromkeys(SOURCES, 0)
    for src in done.values():
        out[src] += 1
    return {k: v for k, v in out.items() if v}
