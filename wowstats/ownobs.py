"""What the player's own ForeverDB addon recorded: kills, drops and where a
quest's objectives are (shipper `extract_foreverdb_own`, feed wow-foreverdb,
kinds `obs` and `quest_pos`; docs/contract.md).

- `obs` events are deltas: the addon's outbox batches since the last run, so
  the totals are the sum of every event, each counted once by `source_id`
  (a replayed spool, a re-ingested file, an archive read twice).
- A drop rate of (creature, item) is the item's drops over the creature's
  kills, both the player's own. Kills are the larger of K (kills that gave
  the XP line, solo) and k (corpses looted): a group kill gives no XP line
  and a corpse with nothing to loot isn't looted, so neither alone is the
  count, and the larger is never above the truth. It counts corpses the
  player looted: a boss another player looted isn't in it.
- `quest_pos`: where the game's own quest map put a quest (`g`) and where
  an objective advanced (`o`), one event per change; the newest per quest.

The server reads them from the player's own stream; nothing here leaves it.
"""

from __future__ import annotations

import json
import logging

from wowstats import forever

log = logging.getLogger(__name__)

LABEL = "your kills"
Q_OBS = 'feed:="wow-foreverdb" kind:="obs" | fields _time, source_id, obs_kills, obs_looted, obs_drops'
Q_POS = 'feed:="wow-foreverdb" kind:="quest_pos" | fields _time, source_id, quest_id, spots'


def _list(v) -> list:
    """A field as the store hands it: a JSON string (ingest nests lists as
    one) or the list itself."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return v if isinstance(v, list) else []


def _int(v) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def totals(rows: list[dict]) -> dict:
    """{"kills": {npc: n}, "looted": {npc: n}, "drops": {(npc, item): n}}
    summed over the `obs` events, one per source_id."""
    out: dict = {"kills": {}, "looted": {}, "drops": {}}
    seen: set[str] = set()
    for r in rows:
        sid = r.get("source_id")
        if not isinstance(sid, str) or sid in seen:
            continue
        seen.add(sid)
        for key, field, width in (("kills", "obs_kills", 2), ("looted", "obs_looted", 2), ("drops", "obs_drops", 3)):
            for e in _list(r.get(field)):
                nums = [_int(x) for x in e] if isinstance(e, list) and len(e) == width else []
                if len(nums) != width or None in nums or nums[-1] <= 0:
                    continue
                k = nums[0] if width == 2 else (nums[0], nums[1])
                out[key][k] = out[key].get(k, 0) + nums[-1]
    return out


def rates(own: dict) -> dict[tuple[int, int], dict]:
    """{(npc, item): {pct, kills, drops}}: drops over kills, in percent, never
    above 100 (an item count above the corpse count is a stack). A creature
    with no kill counted has no rate. Thin ones (kills under MIN_DROP_KILLS)
    are in: wowstats.plan.dungeongain.thin_rate keeps them out of a number."""
    out = {}
    for (npc, item), n in (own.get("drops") or {}).items():
        kills = max((own.get("kills") or {}).get(npc, 0), (own.get("looted") or {}).get(npc, 0))
        if kills > 0:
            out[npc, item] = {"pct": round(min(100.0, 100.0 * n / kills), 1), "kills": kills, "drops": n}
    return out


def positions(rows: list[dict]) -> dict[int, list[list]]:
    """{quest: [[source, objective, map, x, y], ...]} from the newest
    `quest_pos` event of each quest."""
    newest: dict[int, tuple] = {}
    for r in rows:
        qid = _int(r.get("quest_id"))
        spots = _list(r.get("spots"))
        if qid is None or not spots:
            continue
        key = (str(r.get("_time") or ""), str(r.get("source_id") or ""))
        if qid not in newest or key > newest[qid][0]:
            newest[qid] = (key, spots)
    out = {}
    for qid, (_, spots) in newest.items():
        ok = [s for s in spots if isinstance(s, list) and len(s) == 5 and s[0] in ("g", "o")
              and all(isinstance(v, int | float) and not isinstance(v, bool) for v in s[1:])]
        if ok:
            out[qid] = ok
    return out


def read(store) -> dict:
    """{"rates": {(npc, item): ...}, "quest_pos": {quest: spots}} from the
    player's stream. Empty on any trouble: this is a bonus, never a reason
    to fail a page."""
    try:
        stream = store.stream
        obs = store.vl(f"{stream} {Q_OBS}")
        pos = store.vl(f"{stream} {Q_POS}")
    except Exception:  # noqa: BLE001 - a store that can't answer has no own data
        log.warning("ForeverDB own records unreadable", exc_info=True)
        return {"rates": {}, "quest_pos": {}}
    return {"rates": rates(totals(obs)), "quest_pos": positions(pos)}


def merge_loot(loot: dict, own: dict) -> dict:
    """The dungeons' loot with the player's own rate beside ForeverDB.net's
    (a new dict; `loot` may be shared), for each drop the loot already names
    a creature for: a copy of that drop (boss, wing) with `chance_pct` his
    own, `kills` and `source`. A creature ForeverDB.net doesn't list for the
    item gets nothing: the dungeon of a creature isn't known from a kill
    count."""
    rate = own.get("rates") or {}
    if not rate:
        return loot
    out = {}
    for aid, items in loot.items():
        rows = []
        for it in items:
            added = []
            for d in it.get("drops") or []:
                r = rate.get((d.get("npc_id"), it.get("item_id")))
                if r:
                    added.append({**{k: d[k] for k in ("boss", "npc_id", "wing") if k in d},
                                  "chance_pct": r["pct"], "kills": r["kills"], "source": LABEL})
            rows.append({**it, "drops": sorted([*it["drops"], *added], key=lambda x: -(x["chance_pct"] or 0))}
                        if added else it)
        out[aid] = rows
    return out


def objective_spot(quest_pos: dict | None, qid) -> dict | None:
    """Where to go for a quest's objective, from the player's own records:
    the game's quest map spot of the first objective (the lowest objective
    number from 1; 0 is the hand-in), else where an objective advanced. {zone,
    x, y, objective, source} or None (no spot, or a map the reference has no
    name for)."""
    spots = (quest_pos or {}).get(_int(qid)) or []
    pick = sorted((s for s in spots if _int(s[1]) and s[1] >= 1), key=lambda s: (s[0] != "g", s[1]))
    for src, obj, m, x, y in pick:
        zone = forever.ui_map(int(m))
        if zone:
            return {"zone": zone, "x": x, "y": y, "objective": int(obj),
                    "source": "the quest map" if src == "g" else "where you advanced it"}
    return None
