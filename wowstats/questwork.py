"""What a quest reward really takes: the gear advice's quest sources, said
with the work behind them (owner, 2026-11-25: "there are a few recos here
that are easier said than done so we need to check that").

A quest was "easy" whatever it asked: a level-43 elite at 37, a chain of
twelve quests, a dungeon, twenty library books. Now each quest source
carries its facts, every one from data:

- the route (data/route-reference.json `turnins`, Polestar's guides): a
  quest a levelling guide hands in counts by where the guide does it, the
  step and the level its runs reach it at (owner, 2026-11-25: "the Big Game
  Hunter wand comes out of the guide and I'm deep into the chain"). Its
  elite and its level only matter off the route, or when the guide does it
  later than SOON_LEVELS from now. A guide off the levelling route (Library
  Books) counts the quests it hands in before;
- the chain (forever-db's prerequisites, data/forever-reference.json `pre`):
  how many quests come before it, how many are done or in the log, and the
  next one to do;
- the quest log: a quest already accepted needs no chain;
- the work (forever-reference `quest_work`): a group or dungeon quest by
  forever-db's type, the strongest creature its objectives name with its
  level and rank, the items to bring that no creature or object gives.

effort() turns the facts into the advice's effort (wowstats.gear EFFORT)
and says() into the line: what it takes, what blocks it, the next step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wowstats import forever

# forever-db's quest types (Classic's QuestInfo).
GROUP, PVP, RAID, DUNGEON = 1, 41, 62, 81
RANKS = {0: "normal", 1: "elite", 2: "rare elite", 3: "boss", 4: "rare"}
ELITE_RANKS = {1, 2, 3}
# A quest on the route counts by the guide while the guide gets there within
# this many levels (wowstats.gear SOON_LEVELS: the advice's "coming soon").
SOON_LEVELS = 3
# A creature this many levels over the character makes a quest some effort,
# then hard; an elite over the character's level is a group's work.
OVER_SOME, OVER_HARD = 3, 5
# Quests still to hand in before this one: from one it is some effort, from
# CHAIN_HARD on hard.
CHAIN_HARD = 5

EASY, SOME, HARD, LONG_SHOT = range(4)


@dataclass
class Position:
    """Where the character is: its level, side, the guide and step the
    Recorder saw last (None: unknown), the quests done and in the log."""
    level: int
    side: str | None = None  # "Alliance" / "Horde"
    guide: str | None = None
    step: int | None = None
    done: set = field(default_factory=set)
    log: set = field(default_factory=set)


def _route():
    try:
        from wowstats.mcp import route
    except ImportError:  # pragma: no cover - the MCP extra isn't installed
        return None
    ref = route.reference()
    return ref if ref.available else None


_INDEX: dict[int, dict] = {}


def turnin_index(ref) -> dict:
    """{quest id: [(guide, step, level, group)]} from the route file's
    `turnins`, built once per reference."""
    key = id(ref)
    if key not in _INDEX:
        out: dict[int, list] = {}
        for g, rows in (getattr(ref, "turnins", None) or {}).items():
            for q, step, level, group, *_ in rows:
                out.setdefault(int(q), []).append((g, int(step), level, bool(group)))
        _INDEX.clear()
        _INDEX[key] = out
    return _INDEX[key]


def on_route(qid: int, pos: Position, ref=None) -> dict | None:
    """The levelling guide of the character's side that hands the quest in
    ahead of it: {guide, title, step, level, group}. Ahead is a later step of
    the guide the character is in, or a guide that doesn't end below its
    level. The nearest such hand-in; None when no levelling guide has it."""
    ref = ref if ref is not None else _route()
    if ref is None:
        return None
    best = None
    for g, step, level, group in turnin_index(ref).get(int(qid), ()):
        row = (ref.guides or {}).get(g)
        if not row or (pos.side and row.get("faction") not in (None, pos.side)):
            continue
        if pos.guide == g:
            if pos.step is not None and step < pos.step:
                continue
        elif (row.get("level_end") or 0) < pos.level - 1:
            continue
        at = level if level is not None else row.get("level_start")
        cand = {"guide": g, "title": row.get("title"), "step": step, "level": at, "group": group}
        if best is None or (at or 99) < (best["level"] or 99):
            best = cand
    return best


def side_guide(qid: int, pos: Position, ref=None) -> dict | None:
    """A guide off the levelling route that hands the quest in (Library
    Books): {guide, title, step, before: [quests it hands in first, not done]}."""
    ref = ref if ref is not None else _route()
    if ref is None:
        return None
    titles = getattr(ref, "side_guides", None) or {}
    for g, step, _level, _group in turnin_index(ref).get(int(qid), ()):
        if g not in titles:
            continue
        rows = (getattr(ref, "turnins", None) or {}).get(g) or []
        before = []
        # Once the quest is in the log, what the guide does first is done
        # with (Library Books' milestones come with the books handed in).
        for q, s, *_ in [] if int(qid) in pos.log else rows:
            if s >= step:
                break
            if q not in pos.done and q not in before:
                before.append(q)
        return {"guide": g, "title": titles[g], "step": step, "before": before}
    return None


def hidden_gate(qid: int, _seen: frozenset = frozenset()) -> bool:
    """Whether the quest data says nothing of what a quest takes: no
    objectives and no zone in forever-db, and the same for every quest
    before it (Library Books' milestones: Greater Friend of the Library
    after Friend of the Library, each offered once enough books are in), or
    a quest forever-db doesn't have yet. Only then does a guide off the
    route say what comes first: a reputation guide's or a class guide's
    earlier quests are no gate."""
    q = forever.quest(qid)
    if q is None:
        return True
    row = (forever.data().get("quest_work") or {}).get(str(int(qid)))
    if not (row and row[3] == 0 and not q.get("zone")):
        return False
    pre = (forever.data().get("pre") or {}).get(str(int(qid))) or [[], []]
    before = [p for p in (*pre[0], *pre[1]) if p not in _seen]
    return all((forever.data().get("quest_work") or {}).get(str(p)) and hidden_gate(p, _seen | {int(qid)})
               for p in before)


def chain(qid: int, pos: Position) -> dict:
    """The quests forever-db says come before this one (all of an "all"
    list; of an "any" list the one done, else the one in the log, else the
    first), down the whole chain, done ones included (so "8 of 12"):
    {before, done, in_log, next: {quest_id, name, in_log} or None}. `next`
    is the first quest to work on: one in the log, else one whose own
    prerequisites are done."""
    seen: list[int] = []

    def walk(q: int) -> None:
        all_, any_ = (forever.data().get("pre") or {}).get(str(q)) or [[], []]
        picks = list(all_)
        if any_:
            done_any = [p for p in any_ if p in pos.done]
            picks.append(done_any[0] if done_any else ([p for p in any_ if p in pos.log] or any_)[0])
        for p in picks:
            if p not in seen and p != qid:
                seen.append(p)
                walk(p)

    walk(int(qid))
    left = [q for q in seen if q not in pos.done]
    logged = [q for q in left if q in pos.log]

    def ready(q: int) -> bool:
        all_, any_ = (forever.data().get("pre") or {}).get(str(q)) or [[], []]
        return all(p in pos.done for p in all_) and (not any_ or any(p in pos.done for p in any_))

    nxt = logged[0] if logged else next((q for q in reversed(left) if ready(q)), left[-1] if left else None)
    fq = forever.quest(nxt) or {} if nxt else {}
    return {"before": len(seen), "done": len(seen) - len(left), "in_log": len(logged),
            "next": {"quest_id": nxt, "name": fq.get("name"), "part": fq.get("part"), "in_log": nxt in pos.log}
            if nxt else None}


def work(qid: int) -> dict:
    """forever-reference `quest_work`: {type, foe: {npc_id, name, levels,
    rank}, bring: [item names], objectives}; empty for a plain quest."""
    row = (forever.data().get("quest_work") or {}).get(str(int(qid)))
    if not row:
        return {}
    kind, foe, bring, objectives = row
    out: dict = {"type": kind, "objectives": objectives}
    if foe:
        out["foe"] = creature(foe)
    if bring:
        out["bring"] = [forever.item_name(i) or f"item {i}" for i in bring]
    return out


def creature(npc_id) -> dict:
    """forever-reference `npcs`: {npc_id, name, level (the top), levels, rank,
    elite, zone}; only the id when no data names it."""
    row = (forever.data().get("npcs") or {}).get(str(npc_id)) if npc_id else None
    if not row:
        return {"npc_id": npc_id}
    name, lo, hi, rank, *rest = row
    return {"npc_id": npc_id, "name": name, "level": hi or lo or None, "levels": [lo, hi],
            "rank": RANKS.get(rank, "normal"), "elite": rank in ELITE_RANKS, "zone": rest[0] if rest else None}


def facts(qid: int, pos: Position, *, level=None, dungeon: str | None = None, ref=None) -> dict:
    """Everything the line and the effort read for one quest source."""
    qid = int(qid)
    out: dict = {"in_log": qid in pos.log, **work(qid)}
    if level:
        out["quest_level"] = level
    if dungeon:
        out["dungeon"] = dungeon
    r = on_route(qid, pos, ref)
    if r:
        out["route"] = r
    elif hidden_gate(qid):
        sg = side_guide(qid, pos, ref)
        if sg:
            out["side_guide"] = sg
    if qid not in pos.log:
        c = chain(qid, pos)
        if c["before"]:
            out["chain"] = c
    out["effort"] = effort(out, pos.level)
    return out


def _foe_effort(f: dict, level: int) -> int:
    e = EASY
    foe = f.get("foe") or {}
    over = (foe.get("level") or f.get("quest_level") or 0) - level
    if f.get("type") == RAID:
        return HARD
    if foe.get("elite") or f.get("type") == GROUP:
        e = HARD if over > 0 else SOME
    if over >= OVER_HARD:
        e = max(e, HARD)
    elif over >= OVER_SOME:
        e = max(e, SOME)
    if f.get("dungeon") or f.get("type") == DUNGEON:
        e = max(e, SOME)
    return e


def effort(f: dict, level: int) -> int:
    """The quest's effort (wowstats.gear EFFORT index). On the route and
    soon: the guide's work, easy, some effort for a step the guide keeps
    for a group. Otherwise the work itself: an elite over the character's
    level, a raid or a group quest with no lower foe is hard; a foe
    OVER_SOME levels up or a dungeon is some effort, OVER_HARD up hard;
    every quest still to hand in first is some effort, CHAIN_HARD of them
    hard; an item to bring no creature or object gives is some effort. A
    route that gets there later than SOON_LEVELS makes doing it now at
    least some effort (a trip off the route)."""
    r = f.get("route")
    if r and (r.get("level") or 0) <= level + SOON_LEVELS:
        return SOME if r.get("group") else EASY
    e = _foe_effort(f, level)
    left = 0
    if f.get("chain"):
        left += f["chain"]["before"] - f["chain"]["done"]
    if f.get("side_guide"):
        left += len(f["side_guide"]["before"])
    if left >= CHAIN_HARD:
        e = max(e, HARD)
    elif left:
        e = max(e, SOME)
    if f.get("bring"):
        e = max(e, SOME)
    if r:
        e = max(e, SOME)
    return e


def _lv(x) -> str:
    return str(int(round(x))) if isinstance(x, (int, float)) else "?"


def foe_words(foe: dict, level: int) -> str | None:
    """'King Bangalash (level 43 elite, 6 over you)'."""
    if not foe.get("name"):
        return None
    lv = foe.get("level")
    over = (lv or 0) - level
    tag = f"level {lv}" + (f" {foe['rank']}" if foe.get("rank") not in (None, "normal") else "")
    return f"{foe['name']} ({tag}{f', {over} over you' if over > 0 else ''})" if lv else foe["name"]


def says(f: dict, level: int) -> str:
    """The quest's work in one line: where the route does it, the chain and
    its next step, the foe, the dungeon, what to bring."""
    parts = []
    r = f.get("route")
    if r:
        at = f"at about level {_lv(r['level'])}" if r.get("level") else "later"
        part = f"on your route: hand-in {at} ({r.get('title') or r['guide']}, step {r['step']})"
        if r.get("group"):
            part += ", a step the guide keeps for a group"
        if (r.get("level") or 0) > level + SOON_LEVELS:
            part += "; doing it now is a trip off the route"
        parts.append(part)
    sg = f.get("side_guide")
    if sg:
        n = len(sg["before"])
        parts.append(f"Polestar's {sg['title']} guide, step {sg['step']}"
                     + (f": {n} of its quests to hand in first" if n else ""))
    if f.get("in_log"):
        parts.append("in your quest log")
    c = f.get("chain")
    if c:
        left = c["before"] - c["done"]
        noun = "quest" if c["before"] == 1 else "quests"
        part = (f"chain: {c['done']} of {c['before']} {noun} before it done" if left
                else f"chain: the {c['before']} {noun} before it done")
        if c.get("in_log"):
            part += f", {c['in_log']} in your log"
        nxt = c.get("next")
        if nxt and left:
            part += f"; next: {nxt.get('name') or 'quest ' + str(nxt['quest_id'])}"
            if nxt.get("part"):
                part += f", part {nxt['part'][0]} of {nxt['part'][1]}"
            if nxt.get("in_log"):
                part += " (in your log)"
        parts.append(part)
    if f.get("dungeon"):
        parts.append(f"a dungeon quest: {f['dungeon']}")
    elif f.get("type") == GROUP and not (r and r.get("group")):
        parts.append("a group quest")
    elif f.get("type") == RAID:
        parts.append("a raid quest")
    foe = foe_words(f.get("foe") or {}, level)
    tough = (f.get("foe") or {}).get("elite") or f.get("type") == GROUP or (
        ((f.get("foe") or {}).get("level") or 0) - level >= OVER_SOME)
    if foe and tough and not (r and (r.get("level") or 0) <= level + SOON_LEVELS and not r.get("group")):
        parts.append(f"kill {foe}" if (f.get("foe") or {}).get("elite") or f.get("type") == GROUP else f"foe: {foe}")
    over = (f.get("quest_level") or 0) - level
    soon = r and (r.get("level") or 0) <= level + SOON_LEVELS
    if not f.get("foe", {}).get("level") and over >= OVER_SOME and not soon:
        parts.append(f"quest level {f['quest_level']}, {over} over you")
    if f.get("bring"):
        parts.append("bring " + ", ".join(f["bring"]))
    return "; ".join(parts)
