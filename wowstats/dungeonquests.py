"""Dungeon quests and the run they belong to (owner, 2026-11-24: "mark a
quest as a dungeon quest if it's related, so we can group the quest to the
previous dungeon run").

City of Dalaran's quests are handed in at the Silverpine grate outside, so
their XP (19.7k on one character) read as Silverpine's quest XP and the
dungeon's XP an hour looked poor. The same goes for every dungeon whose
quests are given or handed in outside: the Deadmines' in Westfall, the
Stockade's in Stormwind, Wailing Caverns' in the Barrens.

Which dungeon a quest belongs to is data/dungeons/quests.json
(tools/dungeon_quests/export.py: forever-db's zone, Polestar's cited
journal and its dungeon runs, all Forever's own data). Which run a hand-in
belongs to is `attribute`:

- the nearest run of that dungeon that started before the hand-in, the
  same character (a prep build is one character), inside the run or after
  it: a quest handed in inside counts toward that run too;
- within WINDOW_S of leaving it. A day covers logging out after the run
  and handing in the next evening; past it the hand-in is left where it
  was made, as that zone's quest XP;
- the quest was in the log before the run ended (its last accept before
  the hand-in came before the exit). Picked up after the run, it was done
  without it and stays quest XP of its zone, as does a dungeon quest handed
  in with no run of its dungeon before it.

Two runs of the same dungeon: the nearest one before the hand-in wins.
"""

from __future__ import annotations

import bisect
import functools
import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
FILES = (_HERE / "data" / "dungeons" / "quests.json", _HERE.parent / "data" / "dungeons" / "quests.json")
# How long after leaving a run a hand-in still counts toward it (see above).
WINDOW_S = 24 * 3600


@functools.cache
def data() -> dict:
    for p in FILES:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return {"dungeons": {}, "quests": {}}


def dungeons_of(qid) -> list[int]:
    """The instance maps a quest belongs to (one, nearly always), [] for a
    quest of no dungeon."""
    try:
        return [m for m, _ in data()["quests"].get(str(int(qid))) or []]
    except (TypeError, ValueError):
        return []


def sources_of(qid) -> list[str]:
    """Why the quest is a dungeon quest: zone, journal, route, flag."""
    try:
        return sorted({s for _, ss in data()["quests"].get(str(int(qid))) or [] for s in ss})
    except (TypeError, ValueError):
        return []


def attribute(runs: list[tuple], turnins: list[tuple], accepts: dict[int, list[float]]) -> dict:
    """{turn-in key: run index} for the hand-ins of dungeon quests that
    count toward a run. runs: [(index, map, enter_t, leave_t)]; turnins:
    [(key, t, quest_id)]; accepts: {quest_id: sorted accept times}. A
    hand-in left out stays quest XP of the zone it was made in."""
    by_map: dict[int, list[tuple]] = {}
    for i, m, r0, r1 in runs:
        if m is not None:
            by_map.setdefault(int(m), []).append((r0, r1, i))
    for v in by_map.values():
        v.sort()
    out = {}
    for key, t, qid in turnins:
        best = None
        for m in dungeons_of(qid):
            for r0, r1, i in by_map.get(m, ()):
                if r0 <= t and (best is None or r0 > best[0]):
                    best = (r0, r1, i)
        if best is None:
            continue  # no run of its dungeon before: done without one
        r0, r1, i = best
        if t > r1 + WINDOW_S:
            continue  # handed in more than a day after the run
        acc = accepts.get(int(qid)) or []
        k = bisect.bisect_right(acc, t)
        if k and acc[k - 1] >= r1:
            continue  # picked up after the run: not that run's quest
        out[key] = i
    return out
