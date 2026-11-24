"""The Dungeons panel: one block per dungeon that matters to the character,
with everything it holds for him, from the planner's inputs
(wowstats.plan.facts) and nothing else.

A dungeon matters when he ran it, when it fits his level, or when it opens
within OPENS_WITHIN levels; a character below every dungeon gets the first
NEXT_UP that open for his side. Each block has:

- the drops worth knowing about, scored with his own weights (the gear
  advice's, wowstats.gear.instance_loot): the best for him first, each
  obtained (worn, in the bags or the bank, or looted) or missing, with the
  boss and the chance;
- the dungeon's quests: handed in, in his log, or not picked up, with the
  upgrades they give and who gives and takes them;
- a trip's whole value: inside time plus travel and hand-ins, XP from kills
  and quests, the expected gear gain per clear, the loot's gold. Measured
  numbers say how many of his runs they come from;
- one more run at his level now (`gain`, wowstats.plan.dungeongain): time,
  XP, the share of the level, the pace against his questing, the quests to
  take along, the trip's parts from Polestar's pick-up and hand-in rules
  on his own quests (wowstats.plan.tripcost). The Overview's dungeon move
  and the MCP's dungeon_options show this same estimate. The kills' XP in
  it is his own measure, else the kill estimate (wowstats.plan.dungeonxp),
  labelled, and counted in the pace and the verdict;
- one verdict line with its reason, and a value (high, some, none) the page
  colours the block by: XP, gear and gold together, never XP alone;
- his past runs, newest first.

Everything is something to do (owner, 2026-11-18): each block says where
the dungeon opens (Polestar's curated entrance, with its source, and the
nearest flight path of his side on that map), what blocks it now (level,
a quest to do first, a key), and the next step; each quest where it is
picked up and handed in (forever-db's spawns, on Polestar's zone maps),
what it needs first, and its level. Places come from those sources only:
an NPC with no known spawn has no place. Nothing is ranked by distance
from the character.

Other players never appear: the runs carry no party.
"""

from __future__ import annotations

import re
import urllib.parse

from wowstats import forever
from wowstats.gear import BADGE, badge_of, gain_words
from wowstats.links import WOWHEAD_BASE, dungeon_guides, dungeon_url, wowhead_url

from . import dungeongain as G
from . import estimate as E
from . import kbloot
from . import vsguide as V
from . import wings as W

OPENS_WITHIN = 5   # levels: a dungeon that opens this close is shown, "opens at N"
NEXT_UP = 3        # below every dungeon: the first ones that open for his side
SHOWN = 3          # drops a block shows before "N more" (the page's rule)
HIGH_GAIN_SHARE = 2.0  # percent of everything worn: a clear expected to bring this much is worth a loop


def key(name: str) -> str:
    """One name per dungeon whatever wrote it: Polestar's "Stormwind
    Stockade", the game's "The Stockade". A wing of a dungeon run in wings
    (wowstats.plan.wings) keeps its wing: "Scarlet Monastery: Library" is
    its own line; another name after a colon (Excavation Site: Wetlands) is
    the dungeon's."""
    n = re.sub(r"^(the |stormwind )", "", (name or "").lower()).split(":")[0]
    base = re.sub(r"[^a-z]", "", n).rstrip("s")
    w = W.key(name)
    return f"{base}:{w}" if w else base


def known_areas() -> dict[str, dict]:
    """Every dungeon the panel can show: the game's, Forever's new ones from
    the knowledge base, and a dungeon run in wings as one area per wing
    ("796:Library", named "Scarlet Monastery: Library")."""
    known = areas()
    known.update(kb_areas(known))
    return {wid: w for aid, a in known.items() for wid, w in W.split(aid, a)}


def _dq(inp: dict, aid: str, area: dict) -> dict:
    """The dungeon's quests (facts.dungeon_quests), a wing's own only."""
    d = (inp.get("dungeon_quests") or {}).get(area.get("dungeon_area") or aid) or {}
    if not area.get("wing"):
        return d
    return {**d, "quests": [q for q in d.get("quests") or []
                            if W.in_wing(area["name"], q["quest_id"], q.get("variants") or ())]}


def areas() -> dict[str, dict]:
    """The game's dungeons (data/forever-reference.json): area ID -> name, levels."""
    return {aid: a for aid, a in ((forever.data() or {}).get("areas") or {}).items()
            if a.get("instance") == "dungeon" and len(a.get("levels") or []) == 2}


def kb_areas(have: dict[str, dict] | None = None) -> dict[str, dict]:
    """Forever's new dungeons the client's tables don't list, from the
    knowledge base (data/kb/rules.json: dungeon.forever_new for the range and
    zone, dungeon.forever_new_entrances for the way in): "kb:<key>" -> the
    same shape as areas(), plus `entrance` and `rule` (the citation, for the
    MCP; the page never shows it). The client's own table wins for a
    dungeon it lists."""
    from wowstats.analysis import kb

    rule = kb.static_rule("dungeon.forever_new")
    if not rule:
        return {}
    ways = (kb.static_rule("dungeon.forever_new_entrances") or {}).get("value") or {}
    seen = {key(a["name"]) for a in (areas() if have is None else have).values()}
    cite = {k: rule.get(k) for k in ("name", "source_url", "source_type", "date", "confidence", "last_verified")}
    out = {}
    for name, d in (rule.get("value") or {}).items():
        if key(name) in seen or len(d.get("levels") or []) != 2:
            continue
        out[f"kb:{key(name)}"] = {"name": name, "instance": "dungeon", "levels": list(d["levels"]), "map": None,
                                  "zone": d.get("zone"), "entrance": ways.get(name), "rule": cite}
    return out


def kb_entrance(area: dict, side: str | None) -> dict | None:
    """The way into a KB dungeon: its zone and map point when a source gives
    one, the nearest flight path of his side (a name only), what to bring."""
    w = area.get("entrance") or {}
    if not area.get("zone"):
        return None
    fl = (w.get("flight") or {}).get(side or "")
    return {"zone": area["zone"], "x": w.get("x"), "y": w.get("y"), "source": None,
            "flight": {"name": fl, "x": None, "y": None} if fl else None, "prep": w.get("prep")}


def _places() -> dict:
    """route-reference.json `places` (tools/route_reference/export.mjs)."""
    from wowstats.mcp import route

    ref = route.reference()
    return ref.places if ref.available else {}


def _instances() -> dict[int, str]:
    """Instance Map id -> the dungeon's name (forever-reference `map`)."""
    return {int(a["map"]): a["name"] for a in ((forever.data() or {}).get("areas") or {}).values()
            if a.get("instance") and a.get("map")}


def place(places: dict, kind: str, id_) -> dict | None:
    """Where an NPC or object stands: {zone, x, y[, spawns]} on a zone map,
    {inside: dungeon} for one inside an instance, or None (no spawn known)."""
    p = (places.get(kind) or {}).get(str(id_))
    if isinstance(p, list) and len(p) >= 3:
        z = (places.get("zones") or {}).get(str(p[0]))
        if z:
            return {"zone": z, "x": p[1], "y": p[2], **({"spawns": p[3]} if len(p) > 3 else {})}
    if isinstance(p, dict) and p.get("instance"):
        return {"inside": _instances().get(int(p["instance"])) or "an instance"}
    return None


def entrance(places: dict, area: dict, side: str | None) -> dict | None:
    """The way in for his side: zone, map point, the nearest flight path of
    his side on that map, the source the point comes from."""
    e = (places.get("entrances") or {}).get(str(area.get("map")))
    if not e:
        return None
    letter = {"Alliance": "A", "Horde": "H"}.get(side or "")
    way = e["A"] if letter == "A" and e.get("A") else e
    z = (places.get("zones") or {}).get(str(way.get("zone")))
    if not z:
        return None
    fl = (way.get("flights") or {}).get(letter) if letter else None
    return {"zone": z, "x": way.get("x"), "y": way.get("y"), "source": way.get("source") or e.get("source"),
            "flight": {"name": fl["name"], "x": fl["x"], "y": fl["y"]} if fl else None}


def _qstate(qid: int, done: set, open_ids: dict) -> str:
    return "done" if qid in done else "open" if qid in open_ids else "new"


def _qref(qid: int, done: set, open_ids: dict) -> dict:
    fq = forever.quest(qid) or {}
    return {"quest_id": qid, "name": fq.get("name"), "state": _qstate(qid, done, open_ids),
            "url": wowhead_url("quest", qid)}


def needs(qid: int, done: set, open_ids: dict) -> list[dict]:
    """The quests still to hand in before this one opens (forever-db's
    prerequisites): every one of the "all" list, or the "any" list when
    none of it is done."""
    all_, any_ = ((forever.data() or {}).get("pre") or {}).get(str(qid)) or [[], []]
    out = [_qref(q, done, open_ids) for q in all_ if q not in done]
    if any_ and not any(q in done for q in any_):
        out += [{**_qref(q, done, open_ids), "any": True} for q in any_]
    return out


def keys(places: dict, area: dict, side: str | None, done: set, open_ids: dict, owned: dict) -> list[dict]:
    """Keys and attunements (Polestar's data/codex/attunements.json) for his
    side: the item, whether he has it, the quests that earn it."""
    e = (places.get("entrances") or {}).get(str(area.get("map"))) or {}
    letter = {"Alliance": "A", "Horde": "H"}.get(side or "")
    # A wing: only the key its own way in needs (the knowledge base's rule
    # for the wing: the Scarlet Key for the Armory and the Cathedral, none
    # for the Graveyard).
    need = W.key_rule(area)
    out = []
    for k in e.get("keys") or []:
        if k.get("faction") and letter and k["faction"] != letter:
            continue
        if need is not None and not (need and (k.get("name") or "").lower() in need.lower()):
            continue
        item = k.get("item")
        out.append({"name": k.get("name"), "kind": k.get("kind"), "need": k.get("need"), "note": k.get("note"),
                    "item_id": item, "url": wowhead_url("item", item) if item else None,
                    "have": bool(item and _where(owned, item)),
                    "quests": [_qref(q, done, open_ids) for q in k.get("goal") or []]})
    return out


def next_step(level: int, state: str, lo: int, verdict_value: str, quests: list[dict], ent: dict | None,
              wait: int | None = None) -> dict:
    """The one thing to do next for this dungeon, as references the page
    links: hand in, pick up, level up, go. `wait`: the level Polestar lists
    it from, when he is under it with no reason of his own to go now."""
    ready = [q for q in quests if q["state"] == "open" and q.get("complete")]
    if ready:
        return {"do": "handin", "quest_id": ready[0]["quest_id"], "name": ready[0]["name"]}
    pickable = [q for q in quests if q["state"] == "new" and not q["blocked"]]
    if wait:
        lo, state = wait, "soon"
    if state == "soon":
        return {"do": "level", "level": lo, "to_go": lo - level,
                "meanwhile": pickable[0]["quest_id"] if pickable else None}
    if verdict_value == "none":
        return {"do": "none"}
    if pickable and verdict_value == "high":
        return {"do": "pickup", "quest_id": pickable[0]["quest_id"], "name": pickable[0]["name"]}
    return {"do": "go", "where": ent["zone"] if ent else None}


def other_sides(inp: dict, known: dict, aid: str) -> bool:
    """Only the other side's quests: their city's dungeon (Ragefire Chasm
    for an Alliance character), never listed for him. The panel's rule, and
    the MCP's dungeon options' (estimate.dungeons): one list on both."""
    d = (inp.get("dungeon_quests") or {}).get(known[aid].get("dungeon_area") or aid) or {}
    return bool(d.get("other_side")) and not d.get("quests")


def build(inp: dict) -> dict:
    level = inp["level"]
    who = inp.get("who") or {}
    p = E.pace(inp)
    m = E.mounts(inp, p)
    quest_xph = E.rate(p, "questing") or (p["xp_per_hour"]["value"] or 0)
    known = known_areas()
    by_key = {key(a["name"]): aid for aid, a in known.items()}
    trips_by: dict[str, list[dict]] = {}
    for t in inp.get("trips") or []:
        aid = by_key.get(key(W.trip_name(t)))
        if aid:
            trips_by.setdefault(aid, []).append(t)

    def theirs(aid: str) -> bool:
        return other_sides(inp, known, aid)

    pick = set(trips_by)
    for aid, a in known.items():
        lo, hi = a["levels"]
        if hi >= level - 2 and lo <= level + OPENS_WITHIN and not theirs(aid):
            pick.add(aid)
    if not pick - set(trips_by):
        ahead = sorted((a["levels"][0], a["name"], aid) for aid, a in known.items()
                       if a["levels"][0] > level and not theirs(aid))
        pick |= {aid for *_, aid in ahead[:NEXT_UP]}
    every_trip = E.trip_stats(inp.get("trips") or [])
    out = [block(inp, aid, known[aid], trips_by.get(aid) or [], every_trip, quest_xph, m) for aid in pick]
    # Polestar's dungeons for his level (vsguide): the other side's left
    # out, and the ones with nothing left for him named last of all.
    done = {aid for aid, b in zip(pick, out, strict=True) if b["value"] == "none"}
    guide = V.for_level(level, {aid: a for aid, a in known.items() if not theirs(aid)}, done)
    for aid, b in zip(pick, out, strict=True):
        b["vs_guide"] = V.compare(level, b["name"], known[aid], guide, recommended=b["value"] == "high",
                                  why=b["why"], quests=b["quests"], gain=b["gain"])
    rank = {"high": 0, "some": 1, "none": 2}
    out.sort(key=lambda b: (b["state"] == "soon", rank[b["value"]], -b["min"] if b["state"] == "run" else b["min"],
                            b["name"]))
    owned = inp.get("owned") or {}
    return {"char": who.get("char"), "level": level, "class": who.get("class"), "faction": who.get("faction"),
            "spec": inp.get("gear_spec"), "questing_xp_per_hour": round(quest_xph) if quest_xph else None,
            "inventory_known": bool(owned.get("inventory_known")), "dungeons": out}


def _where(owned: dict, item_id) -> str | None:
    return ((owned.get("items") or {}).get(str(item_id)) or {}).get("where")


def drops_for(inp: dict, aid: str, top: int | None = None, area: dict | None = None) -> list[dict]:
    """The dungeon's items that matter to him: every one he has (worn, held,
    looted) and every one better than what he wears, scored with his
    weights; the gain from the gear advice when it lists the item (it weighs
    weapons as a pair). A wing's (`area` a wing): the items its creatures
    drop and its quests give, each drop's bosses narrowed to the wing's."""
    level = inp["level"]
    owned = inp.get("owned") or {}
    adv_gain = {u["item_id"]: u for u in inp.get("upgrades") or []}
    out = []
    wing = (area or {}).get("name") if (area or {}).get("wing") else None
    loot = (inp.get("dungeon_loot") or {}).get((area or {}).get("dungeon_area") or aid) or []
    if wing:
        mine = []
        for it in loot:
            drops = [d for d in it.get("drops") or [] if W.drop_in(wing, [d])]
            qs = [q for q in it.get("quests") or [] if W.in_wing(wing, q["quest_id"])]
            if drops or qs:
                mine.append({**it, "drops": drops, "quests": qs})
        loot = mine
    for it in loot:
        where = "worn" if it.get("equipped") else _where(owned, it["item_id"])
        adv = adv_gain.get(it["item_id"]) or {}
        gain = adv.get("gain", it.get("gain"))
        # Missing means a gain the page shows: one under 0.05 reads "+0.0".
        if not where and not (gain and round(gain, 1) > 0):
            continue
        # Who it's built for (wowstats.gearfit), and the gain the ranking
        # uses: less for an item a group usually hands to someone else.
        fit = adv.get("fit") or it.get("fit")
        value = gain * (fit or {}).get("factor", 1.0) if gain is not None else None
        out.append({**{k: it.get(k) for k in ("item_id", "name", "quality", "item_level", "slot_name", "score",
                                               "gain_pct", "fills_empty")},
                    # The advice's badge when it lists the item (weapons as setups), else the loot's.
                    **badge_of(adv if adv else it),
                    "gain": round(gain, 1) if gain is not None else None, "level": it.get("level"),
                    "value": round(value, 1) if value is not None else None, "fit": fit,
                    "now": (it.get("level") or 0) <= level, "state": "obtained" if where else "missing",
                    "where": where, "url": wowhead_url("item", it["item_id"]),
                    "drops": [{**d, "url": wowhead_url("npc", d["npc_id"]) if d.get("npc_id") else None}
                              for d in it.get("drops") or []],
                    "quests": [q["quest_id"] for q in it.get("quests") or []]})
    # The missing ones first: those he can wear now, then those he can wear
    # by the dungeon's top level (while he'd still run it), then the rest,
    # each by gain; then what he has, the best for him first.
    def order(x):
        when = 0 if x["now"] else 1 if top is None or (x["level"] or 0) <= top else 2
        return (x["state"] != "missing", when, -((x["value"] if x["state"] == "missing" else x["score"]) or 0),
                x["item_id"])

    out.sort(key=order)
    return out


def quests_for(inp: dict, aid: str, drops: list[dict], places: dict | None = None,
               area: dict | None = None) -> list[dict]:
    """The dungeon's quests for him, with their state: handed in, in his log
    (open), or not picked up; the upgrades among their rewards (the best of
    a pick-one reward). A wing's (`area` a wing): its own quests, each
    saying the other wings it needs or can be done in (`wings`)."""
    level = inp["level"]
    done = set(inp.get("quests_done") or [])
    open_ids = {o["quest_id"]: o for o in inp.get("open_quests") or []}
    rewards: dict[int, list[dict]] = {}
    for d in drops:
        for qid in d["quests"]:
            rewards.setdefault(qid, []).append(d)
    out = []
    for q in _dq(inp, aid, area or {}).get("quests") or []:
        # One of a repeatable hand-in's variants (facts._one_per_title): the
        # one in his log, else one he did, stands for the row.
        vs = q.get("variants") or []
        qid = next((v for v in vs if v in open_ids), None) or next((v for v in vs if v in done), q["quest_id"])
        state = _qstate(qid, done, open_ids)
        rw = sorted(rewards.get(qid) or [], key=lambda d: (d["state"] != "missing", -(d["gain"] or 0)))
        # A quest Polestar leaves out, for a class or a slow drop, stays out
        # unless he has it, did it, or it pays an upgrade.
        if q.get("route", "") and q["route"] != "route" and state == "new" and not any(
                d["state"] == "missing" for d in rw):
            continue
        share = E.share(inp, level, q.get("level"))
        need = needs(qid, done, open_ids) if state == "new" else []
        too_low = (q.get("min_level") or 0) > level and state == "new"
        pl = places or {}

        def who(w: dict) -> dict:
            at = place(pl, w["kind"], w["id"]) if w.get("kind") in ("npc", "object") else None
            return {**w, "url": _who_url(w), **(at or {})}

        out.append({**{k: q.get(k) for k in ("quest_id", "name", "level", "min_level", "xp", "money_copper",
                                            "route")},
                    "state": state, "complete": bool((open_ids.get(qid) or {}).get("complete")),
                    "xp_now": round((q.get("xp") or 0) * (share if share is not None else 1.0))
                    if q.get("xp") and state != "done" else None,
                    "url": wowhead_url("quest", qid),
                    "starts": [who(w) for w in q.get("starts") or []],
                    "ends": [who(w) for w in q.get("ends") or []],
                    "needs": [{**n, **_how(n["quest_id"], q.get("pre") or {}, who)} for n in need],
                    "blocked": bool(need or too_low),
                    "rewards": [{k: d[k] for k in ("item_id", "name", "quality", "gain", "state", "where", "url",
                                                   *BADGE)}
                                for d in rw[:1]], "reward_choices": len(rw),
                    **_wings_of(area, q, state, open_ids.get(qid), inp.get("trips") or ())})
    order = {"open": 0, "new": 1, "done": 2}
    out.sort(key=lambda q: (order[q["state"]], q.get("min_level") or 0, q["quest_id"]))
    return out


def _wings_of(area: dict | None, q: dict, state: str = "new", open_quest: dict | None = None,
              trips=()) -> dict:
    """For a wing's quest, the wings it is in (wowstats.plan.wings): "all"
    needs each of them, "any" is done in whichever. `other_wings`: for an
    "all" quest not done, the wings it still needs before this wing's trip
    finishes it (W.other_wings); a quest with some is not this wing's to
    pay (waits_on_other_wings)."""
    if not (area or {}).get("wing"):
        return {}
    w = W.quest(area["name"], q["quest_id"]) or next(
        (W.quest(area["name"], v) for v in q.get("variants") or [] if W.quest(area["name"], v)), None) or {}
    out = {"in": w.get("wings") or [area["wing"]], "how": w.get("how") or "one"}
    if out["how"] == "all" and state != "done":
        out["other_wings"] = W.other_wings(area["name"], w, open_quest, trips)
    return {"wings": out}


def waits_on_other_wings(q: dict) -> bool:
    """A wing's "all" quest its trip doesn't finish: its XP, reward and
    hand-in belong to no single wing's trip (_wings_of)."""
    return bool((q.get("wings") or {}).get("other_wings"))


def _how(qid: int, pre: dict, who) -> dict:
    """How to get a prerequisite quest: who or what starts it, and for an item
    the creatures that drop it and the objects that hold it
    ("starts"/"drops"/"found": same shape as a quest's starts). Empty when
    the data doesn't say; the page then links the quest."""
    h = pre.get(str(qid)) or {}
    return {k: [who(w) for w in h.get(k) or []] for k in ("starts", "drops", "found") if h.get(k)}


def quest_rewards(drops: list[dict], quests: list[dict]) -> list[dict]:
    """The upgrades its quests pay, one per quest still to do (the best of a
    pick-one reward for him): the item, its gain, the quest and who gives
    it. Those he can wear now first, by gain."""
    out = []
    for q in quests:
        rw = [d for d in drops if q["quest_id"] in d["quests"] and d["state"] == "missing"]
        if q["state"] == "done" or not rw or waits_on_other_wings(q):
            continue
        best = max(rw, key=lambda d: d.get("value", d["gain"]) or 0)
        out.append({**{k: best[k] for k in ("item_id", "name", "quality", "slot_name", "gain", "gain_pct",
                                            "fills_empty", "level", "now", "url", *BADGE)},
                    "of": len(rw), "quest": {**{k: q[k] for k in ("quest_id", "name", "url", "state", "complete")},
                                             "starts": q["starts"][:1]}})
    out.sort(key=lambda r: (not r["now"], -(r["gain"] or 0), r["item_id"]))
    return out


def _who_url(w: dict) -> str | None:
    return wowhead_url(w["kind"], w["id"]) if w.get("kind") in ("npc", "object", "item") else None


def trip_value(mine: list[dict], every: dict, quests: list[dict]) -> dict:
    """A trip's numbers: his own runs of this dungeon when he has some (n
    says how many), else his trips elsewhere for the time only. XP per run
    is measured or absent; the quests' XP is the game's own (forever-db)."""
    left = [q for q in quests if q["state"] != "done" and not waits_on_other_wings(q)]
    quest_xp_left = sum(q["xp_now"] or 0 for q in left)
    if mine:
        n = len(mine)
        mean = lambda k: sum(t.get(k) or 0 for t in mine) / n  # noqa: E731
        loot = [t.get("loot") or {} for t in mine]
        ah = [x.get("ah_copper") for x in loot if x.get("ah_copper")]
        vendor = sum(x.get("vendor_copper") or 0 for x in loot) / n
        online = mean("online_s")
        xp_run = mean("xp_inside") + mean("xp_quests")
        # Another run: the kills again and only the quests still to do, the
        # walk there and back, the pickups and hand-ins only for those.
        again_s = mean("travel_to_s") + mean("inside_s") + mean("travel_back_s") + (
            mean("pickup_s") + mean("handin_s") if left else 0)
        again_xp = mean("xp_inside") + quest_xp_left
        return {"measured": True, "n": n, "inside_s": round(mean("inside_s")),
                "travel_s": round(mean("travel_to_s") + mean("travel_back_s")),
                "quests_s": round(mean("pickup_s") + mean("handin_s")), "total_s": round(online),
                "xp_kills": round(mean("xp_inside")), "xp_quests": round(mean("xp_quests")),
                "xp_per_run": round(xp_run), "xp_per_hour": round(xp_run / online * 3600) if online else None,
                "again_s": round(again_s), "again_xp": round(again_xp),
                "again_xp_per_hour": round(again_xp / again_s * 3600) if again_s else None,
                "vendor_copper": round(vendor), "ah_copper": round(sum(ah) / len(ah)) if ah else None,
                "quest_money_copper": round(mean("quest_money_copper")),
                "boe": [{"item_id": b.get("item_id"), "name": b.get("name"), "quality": b.get("quality"),
                         "ah_copper": b.get("ah_copper")} for x in loot for b in x.get("boe") or []][:4],
                "quest_xp_left": quest_xp_left,
                # The dungeon's quests handed in outside after his runs (City of
                # Dalaran's at the Silverpine grate): in xp_quests, said apart.
                "quests_outside": sum(t.get("quests_outside") or 0 for t in mine),
                "xp_quests_outside": round(mean("xp_quests_outside"))}
    if every.get("n"):
        return {"measured": False, "n": every["n"], "total_s": round(every["online_s"]),
                "inside_s": round(every["inside_s"]), "quest_xp_left": quest_xp_left}
    return {"measured": False, "n": 0, "quest_xp_left": quest_xp_left}


def trip_at(name: str, who: dict, level: int, done, log, seen: dict | None, skip=()) -> dict | None:
    """The trip Polestar's rules make for a character in this state
    (wowstats.plan.tripcost.trip): `who` his faction, race and class, `seen`
    where he stands (map_id, x, y; None: the entrance), `skip` quests the
    trip leaves out (a wing's "all" quest it doesn't finish). None without
    route data for the dungeon or the wing."""
    from wowstats.mcp import route

    from . import tripcost

    ref = route.reference()
    trips = ref.trips if ref.available else {}
    area = next((a for a in areas().values() if key(a["name"]) == key(name.split(":")[0])), None)
    if not trips or not area or not area.get("map"):
        return None
    wing = W.wing_of(name)
    seen = seen or {}
    at = tripcost.to_world(trips, seen.get("map_id"), seen.get("x"), seen.get("y"))
    return tripcost.trip(trips, area["map"], side=who.get("faction"), level=level, race=who.get("race"),
                         cls=who.get("class"), done=set(done or ()), log=set(log or ()), at=at,
                         wing=wing[1] if wing else None, skip=set(skip))


def route_trip(inp: dict, name: str, quests: list[dict], skip=()) -> dict | None:
    """The trip Polestar's pick-up and hand-in rules make for him now
    (trip_at): from where he was last seen, on his level, race, class and
    quests, with the XP of the quests it hands in (the panel's own figure
    for a quest it lists, forever-db's at his level's share for a
    prerequisite it doesn't). None without route data for the dungeon (a
    Forever dungeon Polestar has no entrance for)."""
    from wowstats.mcp import route

    from . import tripcost

    level = inp["level"]
    t = trip_at(name, inp.get("who") or {}, level, inp.get("quests_done") or [],
                {o["quest_id"] for o in inp.get("open_quests") or []}, inp.get("last_position"), skip)
    if not t:
        return None
    trips = route.reference().trips
    rows = {i: q for q in quests for i in (q["quest_id"], *(q.get("variants") or []))}
    data = tripcost.quests_of(trips)
    xp = 0.0
    for qid in dict.fromkeys([*t["quests"], *t["prereqs"]]):
        q = rows.get(qid)
        if q is not None:
            xp += q.get("xp_now") or 0
        elif (data.get(qid) or {}).get("xp"):
            share = E.share(inp, level, data[qid].get("level"))
            xp += data[qid]["xp"] * (share if share is not None else 1.0)
    return {**t, "xp_quests": round(xp)}


# The player's pace on a trip's legs: his measured pickups, travel and
# hand-ins over what Polestar's rules plan for the same trip from where it
# started (facts.trip_pace's route_s). Owner, 2026-11-23: his trips take
# about twice the straight run ("I pause, do other stuff, go back and forth,
# forget to hand in a quest and have to come back"). Kept in PACE_RANGE: a
# trip quicker than the rules' straight run is the rules' miss, not a pace
# to plan on, and past 4x one trip's long break would set every estimate.
PACE_RANGE = (1.0, 4.0)


def pace(trips: list[dict]) -> dict:
    """The factor the route's pickups, travel and hand-ins scale by: his
    trips' measured legs over what the rules planned for them (`route_s`),
    summed over the trips (time-weighted: a 1-minute rerun planned at 58 s
    and walked in 11 can't set it the way a median of ratios would), in
    PACE_RANGE. A trip counts when the rules could replay it and it had legs
    outside (a trip logged at the door alone measured none). No such trip:
    1, the straight run, labelled so."""
    measured = planned = 0.0
    n = 0
    for t in trips or []:
        plan = t.get("route_s")
        legs = sum(t.get(k) or 0 for k in ("pickup_s", "travel_to_s", "travel_back_s", "handin_s"))
        if plan and legs > 0:
            measured, planned, n = measured + legs, planned + plan, n + 1
    if not n:
        return {"factor": 1.0, "trips": 0, "label": "straight run"}
    raw = measured / planned
    lo, hi = PACE_RANGE
    return {"factor": round(min(hi, max(lo, raw)), 2), "trips": n, "label": "your pace", "raw": round(raw, 2)}


def drop_odds(drops: list[dict]) -> list[dict]:
    """His missing upgrades that drop there and can be worn now, each with
    its best drop chance and the quests that also give it (the estimate
    counts those as quest upgrades when the trip takes the quest)."""
    return [{"item_id": d["item_id"], "quests": d["quests"],
             "chance_pct": max((x.get("chance_pct") or 0 for x in d["drops"]), default=0) or None}
            for d in drops if d["state"] == "missing" and d["now"] and d["drops"]]


def gain_of(inp: dict, name: str, mine: list[dict], quests: list[dict], quest_xph: float | None,
            drops: list[dict] | None = None) -> dict:
    """One more run's XP, time and pace (wowstats.plan.dungeongain), from
    the same inputs wherever it is asked: the panel, the Overview's move,
    the MCP's dungeon_options. `drops`: the panel's (drops_for), for the
    drop upgrades the line counts."""
    level = inp["level"]
    # A wing's "all" quest its trip doesn't finish: out of the trip (no
    # XP, no reward, no hand-in), listed apart for the MCP.
    wait = [q for q in quests if waits_on_other_wings(q)]
    here = [q for q in quests if not waits_on_other_wings(q)]
    skip = {i for q in wait for i in (q["quest_id"], *(q.get("variants") or []))}
    g = G.estimate(level=level, xp_max=inp.get("xp_max") or E.xp_need(inp, level)[0], name=name, runs=mine,
                   others=inp.get("trips") or [], quests=here, questing_xph=quest_xph, rule=G.kill_rule(),
                   route=route_trip(inp, name, here, skip), drops=drop_odds(drops or []),
                   pace=pace(inp.get("trips") or []))
    if wait:
        g["quests_needing_other_wings"] = [
            {"quest_id": q["quest_id"], "name": q["name"], "url": q.get("url"), "state": q["state"],
             "xp_now": q.get("xp_now"), "other_wings": q["wings"]["other_wings"]} for q in wait]
    return g


def gain_named(inp: dict, name: str, quest_xph: float | None) -> dict | None:
    """gain_of for a dungeon by any of its names (the planner's list, a
    move's zone): None for a name no dungeon has."""
    known = known_areas()
    aid = next((a for a, x in known.items() if key(x["name"]) == key(name)), None)
    if aid is None:
        return None
    area = known[aid]
    mine = [t for t in inp.get("trips") or [] if key(W.trip_name(t)) == key(area["name"])]
    # The panel's quests exactly: the drops decide which of the quests
    # Polestar leaves out still count (one that pays an upgrade does).
    drops = drops_for(inp, aid, area["levels"][1], area)
    return gain_of(inp, area["name"], mine, quests_for(inp, aid, drops, _places(), area), quest_xph, drops)


def with_gain(moves: list[dict], panel: dict | None) -> list[dict]:
    """The Overview's dungeon moves with the panel's estimate for the same
    dungeon (one estimate, so the move and the panel never disagree), its
    summary added to the move's text for the MCP, and the panel's quest
    rewards he could use (the move itself only knows the drops)."""
    by = {key(b["name"]): b.get("gain") for b in (panel or {}).get("dungeons") or [] if b.get("gain")}
    rewards = {key(b["name"]): b.get("quest_rewards") for b in (panel or {}).get("dungeons") or []}
    guide = {key(b["name"]): b.get("vs_guide") for b in (panel or {}).get("dungeons") or [] if b.get("vs_guide")}
    out = []
    for m in moves:
        k = key((m.get("zone") or {}).get("name") or m.get("title") or "") if m.get("kind") == "dungeon" else None
        g = by.get(k)
        if g and g.get("summary"):
            m = {**m, "gain": g, "text": m["text"].rstrip(".") + f". {g['summary']}."}
        v = guide.get(k)
        if v:
            # Polestar's list against this move (wowstats.plan.vsguide): the
            # move names a wing, the panel the dungeon; the line names the move's.
            v = V.named(v, (m.get("zone") or {}).get("name"))
            m = {**m, "vs_guide": v, "text": m["text"].rstrip(".") + (f". {v['text']}" if v.get("text") else "")}
        rw = rewards.get(k)
        if rw:
            m = {**m, "quest_rewards": rw, "text": m["text"].rstrip(".") + ". Its quests pay " + ", ".join(
                f"{r['name']} ({gain_words(r)}) from {r['quest']['name']}" for r in rw[:3]) + "."}
        out.append(m)
    return out


def block(inp: dict, aid: str, area: dict, mine: list[dict], every: dict, quest_xph: float, mount: dict) -> dict:
    level = inp["level"]
    lo, hi = area["levels"]
    dungeon_aid = area.get("dungeon_area") or aid  # a wing's dungeon: its loot, zone page and area id
    drops = drops_for(inp, aid, hi, area)
    # No loot in the client's tables or Polestar's data (Forever's newest
    # dungeons): the research notes' boss-by-boss list, unscored, each
    # item made for him or not by the words next to it (wowstats.plan.kbloot).
    kb_drops = [] if (inp.get("dungeon_loot") or {}).get(dungeon_aid) else kbloot.for_block(
        area["name"], (inp.get("who") or {}).get("class"), re.sub(r"\s+", "", inp.get("gear_spec") or ""), level)
    kb_fit = sum(1 for d in kb_drops if d["fit"])
    places = _places()
    quests = quests_for(inp, aid, drops, places, area)
    side = (inp.get("who") or {}).get("faction")
    done = set(inp.get("quests_done") or [])
    open_ids = {o["quest_id"]: o for o in inp.get("open_quests") or []}
    ent = kb_entrance(area, side) if str(aid).startswith("kb:") else entrance(places, area, side)
    missing_now = [d for d in drops if d["state"] == "missing" and d["now"]]
    # The value, not the raw gain: a dungeon whose drops are built for this
    # spec beats one whose only upgrade is another role's piece.
    chances = [{"slot": d["slot_name"], "gain": d.get("value", d["gain"]) or 0,
                "chance": max((x.get("chance_pct") or 0 for x in d["drops"]), default=0)} for d in missing_now]
    exp_gain = E.expected_best([c for c in chances if c["chance"]])
    p_one = 1.0
    for c in chances:
        p_one *= 1 - min(100.0, c["chance"]) / 100.0
    p_one = 1 - p_one if any(c["chance"] for c in chances) else 0.0
    trip = trip_value(mine, every, quests)
    gain = gain_of(inp, area["name"], mine, quests, quest_xph, drops)
    state = "run" if mine else "now" if lo <= level else "soon"
    gs = inp.get("gear_score") or 0
    share = exp_gain / gs * 100 if gs > 0 else None
    verdict, why, value = judge(inp, state, lo, drops, missing_now, quests, gain, quest_xph, mount, exp_gain, share,
                                p_one, trip, kb_fit)
    # Under Polestar's floor with no reason of his own (his quests there,
    # his runs there beating his questing): it waits for the floor, as
    # Polestar's list has it (wowstats.plan.vsguide). Gear alone isn't one.
    wait = None
    if state != "soon" and level < V.floor(area) and value != "none" and V.measured(quests, gain)[0] is None:
        wait = V.from_level(area)
        verdict, why, value = (f"from level {wait}", f"Polestar lists it from level {wait}; you're {level}", "some")
    history = [{"t": E_iso(t.get("start_t")), "level": t.get("level_start"), "inside_s": round(t.get("inside_s") or 0),
                "total_s": round(t.get("online_s") or 0), "xp_kills": t.get("xp_inside"),
                "xp_quests": t.get("xp_quests"), "quests_handed_in": t.get("quests_handed_in"),
                "quests_outside": t.get("quests_outside") or 0,
                "vendor_copper": (t.get("loot") or {}).get("vendor_copper"),
                "items": (t.get("loot") or {}).get("items")}
               for t in sorted(mine, key=lambda t: -(t.get("start_t") or 0))]
    kb_only = str(aid).startswith("kb:")
    # The name links the dungeon's guide; the entrance, a place, its zone
    # page (a Forever dungeon the client's tables don't list: a search).
    zone_url = (f"{WOWHEAD_BASE}/search?q={urllib.parse.quote(area['name'])}" if kb_only
                else wowhead_url("zone", dungeon_aid))
    guides = dungeon_guides(area["name"])
    return {"area_id": None if kb_only else int(dungeon_aid), "name": area["name"], "min": lo, "max": hi,
            "state": state, **({"dungeon": area["dungeon"], "wing": area["wing"]} if area.get("wing") else {}),
            "opens_at": lo if lo > level else None,
            "url": dungeon_url(area["name"]) or zone_url, "zone_url": zone_url, "guides": guides,
            "value": value, "verdict": verdict, "why": why,
            "drops": drops, "kb_drops": kb_drops, "kb_fit": kb_fit, "drops_shown": SHOWN,
            "missing_now": len(missing_now),
            "obtained": sum(1 for d in drops if d["state"] == "obtained"),
            "expected_gain_per_clear": round(exp_gain, 1), "expected_gain_share_pct": round(share, 1)
            if share is not None else None, "chance_one_per_clear_pct": round(p_one * 100),
            "quests": quests, "quest_rewards": quest_rewards(drops, quests), "trip": trip, "gain": gain,
            "history": history,
            "entrance": ent, "keys": keys(places, area, side, done, open_ids, inp.get("owned") or {}),
            "next": next_step(level, state, lo, value, quests, ent, wait),
            **({"rule": area.get("rule")} if kb_only else {})}


def E_iso(t) -> str | None:  # noqa: N802 - the epoch seconds the trips carry, as the page's other times
    from datetime import UTC, datetime

    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if t else None


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def judge(inp, state, lo, drops, missing_now, quests, gain, quest_xph, mount, exp_gain, share, p_one, trip,
          kb_fit=0):
    """One line and its reason, and the block's value (high, some, none):
    gear, quests, XP and gold together. The gear counts when a clear's
    expected gain is HIGH_GAIN_SHARE percent of what he wears or more; the
    quests when one pays an upgrade or their XP is a quarter of an hour of
    his questing; XP per hour only with the kills known (his own runs
    there, wowstats.plan.dungeongain); gold when the mount is behind and
    looping it beats his own gold per hour twice. The quests and the XP are
    the estimate's, so the verdict and the numbers under it agree. kb_fit:
    the drops made for him in a dungeon known only from the research notes
    (no gain to weigh: they count, but never make it "high")."""
    n_miss = len(missing_now)
    todo = {q["quest_id"] for q in gain["quests"]}
    left = [q for q in quests if q["quest_id"] in todo]
    quest_xp = gain["xp_quests"]
    q_up = [q for q in left if any(r["state"] == "missing" for r in q["rewards"])]
    later = [d for d in drops if d["state"] == "missing" and not d["now"]]
    odds = f"{round(p_one * 100)}% a clear" if p_one else "no drop chance known"
    ups = _n(n_miss, "missing upgrade")
    quest_bit = _n(len(left), "quest") + " left" + (f" ({quest_xp:,} XP)" if quest_xp else "")
    if state == "soon":
        waiting = [_n(len([d for d in drops if d["state"] == "missing"]), "upgrade") if drops else None,
                   _n(kb_fit, "drop") + " made for you" if kb_fit else None,
                   _n(len(quests), "quest") if quests else None]
        waiting = [w for w in waiting if w]
        return (f"opens at {lo}", ("waiting there: " + " and ".join(waiting)) if waiting
                else "nothing in it for you yet", "some" if waiting else "none")
    xph = gain["xp_per_hour"] if not gain["kills_unknown"] else None
    good_xp = bool(xph and quest_xph and xph >= 0.9 * quest_xph)
    loop_gold = None
    if trip.get("measured") and trip.get("inside_s"):
        loop_gold = max(trip.get("vendor_copper") or 0, trip.get("ah_copper") or 0) / trip["inside_s"] * 3600
    rate = ((mount.get("gold_rate") or {}).get("copper_per_hour")) or 0
    gold = bool(mount.get("behind") and loop_gold and loop_gold > max(rate, 0) * 2)
    gear = bool(n_miss and exp_gain > 0 and (share is None or share >= HIGH_GAIN_SHARE))
    worth_quests = bool(q_up or (quest_xp and (not quest_xph or quest_xp >= quest_xph / 4)))
    if gear and worth_quests:
        return ("worth a run", f"{quest_bit}, and {ups} at {odds}", "high")
    if gear:
        return ("worth a loop for gear", f"{ups} at {odds}", "high")
    if worth_quests:
        return ("worth a run for its quests", quest_bit + (", one pays an upgrade" if q_up else ""), "high")
    if good_xp:
        return ("worth a run for XP", "another run's XP per hour matches your questing", "high")
    if gold:
        return ("worth a loop for gold", "looping it earns twice your gold per hour, and the mount is behind", "high")
    if kb_fit:
        return ("worth a look for gear", _n(kb_fit, "drop") + " made for you" + (f", and {quest_bit}" if left else ""),
                "some")
    small = [quest_bit if left else None, f"{ups} at {odds}" if n_miss else None]
    small = [x for x in small if x]
    if small:
        return ("only if a group is going", " and ".join(small) + ": not worth a trip of its own", "some")
    if later:
        return ("not yet", f"{_n(len(later), 'upgrade')} once you're level {min(d['level'] for d in later)}", "some")
    return ("done", "nothing left for you", "none")
