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

import math
import re
import urllib.parse

from wowstats import classguide, forever, gear
from wowstats.gear import BADGE, badge_of, gain_words
from wowstats.links import WOWHEAD_BASE, dungeon_guides, dungeon_url, wowhead_url

from . import dungeongain as G
from . import estimate as E
from . import kbloot, tripcost
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
    """The game's dungeons (data/forever-reference.json): area ID -> name,
    levels. One per instance map, its own area (the export's `maps`): the
    areas list every room of a dungeon too (City of Dalaran has two areas
    of that name)."""
    return {aid: a for aid, a in forever.instance_areas("dungeon").items() if len(a.get("levels") or []) == 2}


def kb_areas(have: dict[str, dict] | None = None) -> dict[str, dict]:
    """Forever's new dungeons the client's tables don't list, from the
    knowledge base (data/kb/rules.json: dungeon.forever_new for the range and
    zone, dungeon.forever_new_entrances for the way in): "kb:<key>" -> the
    same shape as areas(), plus `entrance` and `rule` (the citation, for the
    MCP; the page never shows it). The client's own table wins for a
    dungeon it lists."""
    seen = {key(a["name"]) for a in (areas() if have is None else have).values()}
    return {f"kb:{k}": a for k, a in kb_rows().items() if k not in seen}


def kb_rows() -> dict[str, dict]:
    """Every dungeon of the knowledge base's dungeon.forever_new, by key(),
    in kb_areas' shape, listed by the client or not: a dungeon the client
    lists (The Drowned City since build 1.60.1.70888) still takes the KB's
    way in when Polestar has none (Blackmaw Hold), and what to bring."""
    from wowstats.analysis import kb

    rule = kb.static_rule("dungeon.forever_new")
    if not rule:
        return {}
    ways = (kb.static_rule("dungeon.forever_new_entrances") or {}).get("value") or {}
    cite = {k: rule.get(k) for k in ("name", "source_url", "source_type", "date", "confidence", "last_verified")}
    return {key(name): {"name": name, "instance": "dungeon", "levels": list(d["levels"]), "map": None,
                        "zone": d.get("zone"), "entrance": ways.get(name), "rule": cite}
            for name, d in (rule.get("value") or {}).items() if len(d.get("levels") or []) == 2}


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
              wait: int | None = None, *, group_only: bool = False) -> dict:
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
    partial = next((q for q in quests if waits_on_other_wings(q)), None)
    if verdict_value == "some" and not group_only and partial:
        if partial["state"] == "new" and (partial.get("min_level") or 0) > level:
            return {"do": "level", "level": partial["min_level"], "to_go": partial["min_level"] - level}
        if partial["needs"]:
            first = partial["needs"][0]
            return {"do": "prerequisite", "quest_id": first["quest_id"], "name": first["name"]}
        if partial["state"] == "new":
            return {"do": "pickup", "quest_id": partial["quest_id"], "name": partial["name"]}
        if partial["wings"].get("objectives_here") == []:
            return {"do": "other_wings", "where": " and ".join(partial["wings"]["other_wings"])}
    if group_only:
        return {"do": "join", "where": ent["zone"] if ent else None}
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
    quest_xph = E.rate(p, "questing") if level < classguide.MAX_LEVEL else 0
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
        v = b["vs_guide"]
        b["fits_now"] = b["min"] <= level <= b["max"] and (v["agrees"] or bool(v["reason"]))
    out.sort(key=trip_order)
    owned = inp.get("owned") or {}
    return {"char": who.get("char"), "level": level, "class": who.get("class"), "faction": who.get("faction"),
            "spec": inp.get("gear_spec"), "questing_xp_per_hour": round(quest_xph) if quest_xph else None,
            "inventory_known": bool(owned.get("inventory_known")), "dungeons": out}


def trip_order(b: dict) -> tuple:
    """At this level, rank worthwhile trips before tag-alongs. The whole trip's
    XP/h already includes its quests. Gear breaks ties, including at the level cap."""
    gain = b["gain"]
    return (not b["fits_now"], b["state"] == "soon", {"high": 0, "some": 1, "none": 2}[b["value"]],
            -(gain.get("vs_questing") or 0), -(b.get("known_gain_per_clear") or b.get("expected_gain_per_clear") or 0),
            b["name"])


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
        value = adv.get("value")
        if value is None:
            value = gain * (fit or {}).get("factor", 1.0) if gain is not None else None
        out.append({**{k: it.get(k) for k in ("item_id", "name", "quality", "item_level", "slot_name", "score",
                                               "gain_pct", "fills_empty")},
                    # The advice's badge when it lists the item (weapons as setups), else the loot's.
                    **badge_of(adv if adv else it),
                    "gain": gain, "level": it.get("level"),
                    "value": value, "fit": fit,
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
    player = inp.get("who") or {}
    side = player.get("faction")
    bits = (tripcost.SIDE_BIT.get(side, 0), tripcost.race_bit(player.get("race"), side),
            tripcost.CLASS_BIT.get((player.get("class") or "").upper(), 0))
    rewards: dict[int, list[dict]] = {}
    for d in drops:
        for qid in d["quests"]:
            rewards.setdefault(qid, []).append(d)
    out = []
    for q in _dq(inp, aid, area or {}).get("quests") or []:
        # One of a repeatable hand-in's variants (facts._one_per_title): the
        # one in his log, else one he did, stands for the row.
        ids = [v for v in q.get("variants") or [q["quest_id"]]
               if tripcost.eligible(*forever.quest_gates(v), *bits)]
        if not ids:
            continue
        canonical = q["quest_id"] if q["quest_id"] in ids else ids[0]
        qid = next((v for v in ids if v in open_ids), None) or next((v for v in ids if v in done), canonical)
        state = _qstate(qid, done, open_ids)
        rw = sorted(rewards.get(qid) or [], key=lambda d: (d["state"] != "missing", not d["now"], -(d["value"] or 0)))
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

        out.append({**{k: q.get(k) for k in ("name", "level", "min_level", "xp", "money_copper",
                                            "route")},
                    "quest_id": qid, **({"variants": ids} if len(ids) > 1 else {}),
                    "state": state, "complete": bool((open_ids.get(qid) or {}).get("complete")),
                    "xp_now": round(q["xp"] * (share if share is not None else 1.0))
                    if q.get("xp") is not None and state != "done" else None,
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
    finishes it (W.progress); a quest with some is not this wing's to
    pay (waits_on_other_wings)."""
    if not (area or {}).get("wing"):
        return {}
    w = W.quest(area["name"], q["quest_id"]) or next(
        (W.quest(area["name"], v) for v in q.get("variants") or [] if W.quest(area["name"], v)), None) or {}
    out = {"in": w.get("wings") or [area["wing"]], "how": w.get("how") or "one"}
    if out["how"] == "all" and state != "done":
        out.update(W.progress(area["wing"], w, open_quest, trips))
    return {"wings": out}


def waits_on_other_wings(q: dict) -> bool:
    """A wing's "all" quest its trip doesn't finish: its XP, reward and
    hand-in belong to no single wing's trip (_wings_of)."""
    return bool((q.get("wings") or {}).get("other_wings"))


def wing_progress_text(q: dict) -> str:
    wing = q["wings"]
    here = " and ".join(wing.get("objectives_here") or [])
    quest = q["name"] or f"quest {q['quest_id']}"
    what = f"{here} for {quest}" if here else f"continue {quest}"
    all_of = [n["name"] or f"quest {n['quest_id']}" for n in q["needs"] if not n.get("any")]
    any_of = [n["name"] or f"quest {n['quest_id']}" for n in q["needs"] if n.get("any")]
    if any_of:
        all_of.append(" or ".join(any_of))
    first = f"finish {' and '.join(all_of)} first, then " if all_of else ""
    return f"{first}{what}. The hand-in also needs {' and '.join(wing['other_wings'])}"


def _how(qid: int, pre: dict, who) -> dict:
    """How to get a prerequisite quest: who or what starts it, and for an item
    the creatures that drop it and the objects that hold it
    ("starts"/"drops"/"found": same shape as a quest's starts). Empty when
    the data doesn't say; the page then links the quest."""
    h = pre.get(str(qid)) or {}
    return {k: [who(w) for w in h.get(k) or []] for k in ("starts", "drops", "found") if h.get(k)}


def quest_rewards(drops: list[dict], quests: list[dict]) -> list[dict]:
    """The best missing reward choice per unfinished quest, including its gates."""
    out = []
    by_item = {d["item_id"]: d for d in drops}
    for q in quests:
        reward = next((r for r in q["rewards"] if r["state"] == "missing"), None)
        if q["state"] == "done" or not reward:
            continue
        best = by_item[reward["item_id"]]
        source = {k: q[k] for k in ("quest_id", "name", "url", "state", "complete", "min_level", "needs")}
        out.append({**{k: best[k] for k in ("item_id", "name", "quality", "slot_name", "gain", "gain_pct",
                                            "fills_empty", "level", "now", "url", *BADGE)},
                    "line": gain_words(best), "of": q["reward_choices"],
                    "quest": {**source, "starts": q["starts"][:1],
                              "other_wings": (q.get("wings") or {}).get("other_wings", [])}})
    out.sort(key=lambda r: (not r["now"], -(r["gain"] or 0), r["item_id"]))
    return out


def _who_url(w: dict) -> str | None:
    return wowhead_url(w["kind"], w["id"]) if w.get("kind") in ("npc", "object", "item") else None


def trip_value(mine: list[dict], every: dict, quests: list[dict], gain: dict | None = None,
               pace_now: dict | None = None) -> dict:
    """A trip's numbers: his own runs of this dungeon when he has some (n
    says how many), else his trips elsewhere for the time only. XP per run
    is measured or absent; the quests' XP is the game's own (forever-db).
    `gain`: the shared estimate of one more run (wowstats.plan.dungeongain),
    which `again_*` say; `pace_now`: his pace (pace), which a leg a past
    trip didn't see is filled at, from the rules' replay of that trip."""
    left = [q for q in quests if q["state"] != "done" and not waits_on_other_wings(q)]
    quest_xp_left = gain["xp_quests"] if gain else sum(q["xp_now"] or 0 for q in left)
    if mine:
        n = len(mine)
        mean = lambda k: sum(t.get(k) or 0 for t in mine) / n  # noqa: E731
        seen = lambda *ks: sum(x for x in (E.seen_mean(mine, k) for k in ks) if x is not None)  # noqa: E731
        loot = [t.get("loot") or {} for t in mine]
        ah = [x.get("ah_copper") for x in loot if x.get("ah_copper")]
        vendor = sum(x.get("vendor_copper") or 0 for x in loot) / n
        online = mean("online_s")
        xp_run = mean("xp_inside") + mean("xp_quests")
        # The XP an hour of his trips there: a leg a trip didn't see (logged
        # at the door; a hand-in made hours later, its XP in xp_quests) takes
        # the rules' leg for that trip at his pace, so its quests' XP comes
        # with time to earn it. A trip the rules couldn't replay: unknown.
        f = (pace_now or {}).get("factor") or 1.0
        filled, from_rules = 0.0, 0
        for t in mine:
            plan = t.get("route_legs") or {}
            for k in LEGS:
                if t.get(k) is not None:
                    filled += t[k]
                elif plan.get(k) is not None:
                    filled, from_rules = filled + plan[k] * f, from_rules + 1
                else:
                    filled = math.nan
            filled += t.get("inside_s") or 0
        hours = filled / n / 3600 if filled == filled else None
        g = gain or {}
        return {"measured": True, "n": n, "inside_s": round(mean("inside_s")),
                "travel_s": round(seen("travel_to_s", "travel_back_s")),
                "quests_s": round(seen("pickup_s", "handin_s")), "total_s": round(online),
                "xp_kills": round(mean("xp_inside")), "xp_quests": round(mean("xp_quests")),
                "xp_per_run": round(xp_run), "xp_per_hour": round(xp_run / hours) if hours else None,
                "legs_from_rules": from_rules,
                # One more run: the shared estimate's (the same number as the line).
                "again_s": g.get("run_s"), "again_xp": g.get("xp"), "again_xp_per_hour": g.get("xp_per_hour"),
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

    level = inp["level"]
    t = trip_at(name, inp.get("who") or {}, level, inp.get("quests_done") or [],
                {o["quest_id"] for o in inp.get("open_quests") or []}, inp.get("last_position"), skip)
    if not t:
        return None
    trips = route.reference().trips
    rows = {i: q for q in quests for i in (q["quest_id"], *(q.get("variants") or []))}
    # Some route quests have no panel row (for example, a skipped pickup).
    # Keep the variant groups from the dungeon catalogue for those as well.
    catalogue = (inp.get("dungeon_quests") or {}).get(str(forever.area_id(name))) or {}
    catalogue_rows = {i: q for q in catalogue.get("quests") or [] for i in q.get("variants") or [q["quest_id"]]}
    done = set(inp.get("quests_done") or [])
    data = tripcost.quests_of(trips)
    handins = []
    paid = set()
    for prerequisite, ids in ((True, t["prereqs"]), (False, t["quests"])):
        for qid in ids:
            q = rows.get(qid)
            # The selected variant represents this hand-in, including when two
            # IDs appear on the route or a different variant is already done.
            chosen = q["quest_id"] if q else qid
            group = catalogue_rows.get(qid) or {}
            variant_ids = (q or group).get("variants") or [chosen]
            finished = q["state"] == "done" if q else not done.isdisjoint(variant_ids)
            if finished or not paid.isdisjoint(variant_ids):
                continue
            name = (q or group or forever.quest(qid) or {}).get("name")
            xp = q.get("xp_now") if q else None
            if xp is None:
                raw = q or group
                if raw.get("xp") is None:
                    raw = data.get(qid) or {}
                    if raw.get("xp") == 0 and trips.get("schema", 1) < 2:
                        raw = {}  # Older trip exports also wrote zero for a missing reward.
                if raw.get("xp") is not None:
                    share = E.share(inp, level, raw.get("level"))
                    xp = round(raw["xp"] * (share if share is not None else 1))
            handins.append({"quest_id": chosen, "name": name, "url": wowhead_url("quest", chosen),
                            "xp": xp, "prerequisite": prerequisite})
            paid.update(variant_ids)
    return {**t, "handins": handins, "xp_quests": sum(q["xp"] or 0 for q in handins)}


# The player's pace on a trip's legs: his measured pickups, travel and
# hand-ins over what Polestar's rules plan for the same trip from where it
# started (facts.trip_pace's route_s). Owner, 2026-11-23: his trips take
# about twice the straight run ("I pause, do other stuff, go back and forth,
# forget to hand in a quest and have to come back"). Kept in PACE_RANGE: a
# trip quicker than the rules' straight run is the rules' miss, not a pace
# to plan on, and past 4x one trip's long break would set every estimate.
PACE_RANGE = (1.0, 4.0)
LEGS = ("pickup_s", "travel_to_s", "travel_back_s", "handin_s")


def pace(trips: list[dict]) -> dict:
    """The factor the route's pickups, travel and hand-ins scale by: his
    trips' measured legs over what the rules planned for them (`route_s`),
    summed over the trips (time-weighted: a 1-minute rerun planned at 58 s
    and walked in 11 can't set it the way a median of ratios would), in
    PACE_RANGE. Leg by leg: a leg the trip saw against the same leg
    planned; a leg it didn't see (None: logged at the door, a hand-in made
    hours later) is in neither sum. Without the planned legs one by one
    (`route_legs`), `route_s` stands for all four, so only a trip that saw
    all four counts. A trip counts when the rules could replay it and it
    walked some time outside. No such trip: 1, the straight run, labelled so."""
    measured = planned = 0.0
    n = 0
    for t in trips or []:
        seen = [k for k in LEGS if t.get(k) is not None]
        per = t.get("route_legs")
        if per:
            plan = sum(per.get(k) or 0 for k in seen)
        else:
            plan = t.get("route_s") if len(seen) == len(LEGS) else None
        legs = sum(t[k] for k in seen)
        if plan and legs > 0:
            measured, planned, n = measured + legs, planned + plan, n + 1
    if not n:
        return {"factor": 1.0, "trips": 0, "label": "straight run"}
    raw = measured / planned
    lo, hi = PACE_RANGE
    return {"factor": round(min(hi, max(lo, raw)), 2), "trips": n, "label": "your pace", "raw": round(raw, 2)}


def drop_items(drops: list[dict]) -> list[dict]:
    """One row per missing mob drop, paired with its best known source."""
    items = []
    for d in drops:
        if d["state"] != "missing" or not d["drops"]:
            continue
        known = [s for s in d["drops"] if s.get("chance_pct") is not None]
        best = max(known, key=lambda s: s["chance_pct"], default=d["drops"][0])
        chance = best.get("chance_pct")
        items.append({"item_id": d["item_id"], "item": d["name"], "slot": d["slot_name"],
                      "gain": d["value"], "req_level": d["level"], "now": d["now"],
                      **badge_of(d), "line": gain_words(d), "boss": best.get("boss") or "trash",
                      "chance": chance, "unknown_rate": len(known) != len(d["drops"]) and chance != 100})
    return items


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
    g = G.estimate(level=level, xp_max=inp.get("xp_max") or E.xp_need(inp, level)[0],
                   current_xp=inp.get("xp") or 0, name=name, runs=mine,
                   others=inp.get("trips") or [], quests=here, questing_xph=quest_xph, rule=G.kill_rule(),
                   route=route_trip(inp, name, here, skip), drops=drop_items(drops or []),
                   pace=pace(inp.get("trips") or []))
    if level == classguide.MAX_LEVEL:
        g["forecast_note"] = "At the level cap, this trip gives no levelling XP."
    elif g["kills_unknown"]:
        g["forecast_note"] = "Kill XP is unknown. Only the quests have an XP forecast."
    elif g["basis"] == "runs":
        g["forecast_note"] = f"Kill XP at level {level} is based on {_n(g['own_runs'], 'recorded run')} here."
    else:
        party = (g.get("kill_xp") or {}).get("party")
        g["forecast_note"] = (f"The kill XP model assumes {party} level-{level} players without XP buffs."
                              if party else "Kill XP comes from the dungeon model.")
    if g["run_s"] is None:
        g["forecast_note"] += " The whole-trip time is unknown."
    if g["unknown_handin_xp"]:
        g["forecast_note"] += (f" XP is unknown for {_n(g['unknown_handin_xp'], 'hand-in')}. "
                               "The forecast counts only known XP.")
    if wait:
        g["quests_needing_other_wings"] = [
            {"quest_id": q["quest_id"], "name": q["name"], "url": q.get("url"), "state": q["state"],
             "xp_now": q.get("xp_now"), "other_wings": q["wings"]["other_wings"],
             "objectives_here": q["wings"].get("objectives_here")} for q in wait]
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
        if g:
            summary = ". ".join(x.rstrip(".") for x in (m["text"], g.get("summary"), g.get("forecast_note")) if x)
            m = {**m, "gain": g, "text": summary + "."}
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
    if str(aid).startswith("kb:"):
        ent = kb_entrance(area, side)
    else:
        # Polestar's recorded way in first; the KB's when Polestar has none,
        # and the KB's "what to bring" either way (water breathing).
        kb_row = kb_rows().get(key(area["name"])) if area.get("map") else None
        kb_way = kb_entrance(kb_row, side) if kb_row else None
        ent = entrance(places, area, side) or kb_way
        if ent and kb_way and kb_way.get("prep") and not ent.get("prep"):
            ent = {**ent, "prep": kb_way["prep"]}
    gain = gain_of(inp, area["name"], mine, quests, quest_xph, drops)
    rewards = quest_rewards(drops, quests)
    handins = {q["quest_id"] for q in gain["quests"]}
    exp_gain = gain["known_gain_per_clear"] or 0
    trip = trip_value(mine, every, quests, gain, pace(inp.get("trips") or []))
    state = "run" if mine else "now" if lo <= level else "soon"
    gs = inp.get("gear_score") or 0
    share = exp_gain / gs * 100 if gs > 0 else None
    verdict, why, value, goals = judge(inp, state, lo, drops, quests, gain, quest_xph, mount,
                                trip, kb_fit)
    # Under Polestar's floor with no reason of his own (his quests there,
    # his runs there beating his questing): it waits for the floor, as
    # Polestar's list has it (wowstats.plan.vsguide). Gear alone isn't one.
    wait = None
    if state != "soon" and level < V.floor(area) and value != "none" and V.measured(quests, gain)[0] is None:
        goals = []
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
            "value": value, "verdict": verdict, "why": why, "goals": goals,
            "drops": drops, "kb_drops": kb_drops, "kb_fit": kb_fit, "drops_shown": SHOWN,
            "missing_now": gain["drop_upgrades"],
            "obtained": sum(1 for d in drops if d["state"] == "obtained"),
            "known_gain_per_clear": gain["known_gain_per_clear"],
            "expected_gain_per_clear": gain["expected_gain_per_clear"],
            "expected_gain_share_pct": round(share, 1)
            if share is not None and gain["expected_gain_per_clear"] is not None else None,
            "chance_one_per_clear_pct": gain["drop_chance_pct"],
            "quests": quests, "quest_rewards": [r for r in rewards if r["quest"]["quest_id"] in handins],
            "deferred_quest_rewards": [r for r in rewards if r["quest"]["quest_id"] not in handins],
            "trip": trip, "gain": gain,
            "history": history,
            "entrance": ent, "keys": keys(places, area, side, done, open_ids, inp.get("owned") or {}),
            "next": next_step(level, state, lo, value, quests, ent, wait, group_only="group_only" in goals),
            **({"rule": area.get("rule")} if kb_only else {})}


def E_iso(t) -> str | None:  # noqa: N802 - the epoch seconds the trips carry, as the page's other times
    from datetime import UTC, datetime

    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if t else None


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def judge(inp, state, lo, drops, quests, gain, quest_xph, mount, trip, kb_fit=0):
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
    n_miss = gain["drop_upgrades"]
    todo = {q["quest_id"] for q in gain["quests"]}
    left = [q for q in quests if q["quest_id"] in todo]
    quest_xp = gain["xp_quests"]
    q_up = [q for q in left if any(r["state"] == "missing" for r in q["rewards"])]
    later = [d for d in drops if d["state"] == "missing" and not d["now"] and d["drops"]]
    odds = G.odds_words(gain, "a clear")
    ups = _n(n_miss, "missing drop upgrade")
    handins = gain["handins"]
    prereqs = [q for q in handins if q["prerequisite"]]
    amount = f"{quest_xp:,} XP"
    if gain["unknown_handin_xp"]:
        amount = "XP unknown" if gain["unknown_handin_xp"] == len(handins) else f"{quest_xp:,} known XP"
    quest_bit = f"{_n(len(handins), 'hand-in')} ({amount})"
    if prereqs:
        quest_bit += f", including {_n(len(prereqs), 'prerequisite')}"
        if all(q["xp"] is not None for q in prereqs) and sum(q["xp"] or 0 for q in handins) == quest_xp:
            quest_bit += f" worth {sum(q['xp'] for q in prereqs):,} XP"
    if state == "soon":
        waiting = [_n(n_miss + len(later), "drop upgrade") if n_miss or later else None,
                   _n(kb_fit, "drop") + " made for you" if kb_fit else None,
                   _n(len(quests), "quest") if quests else None]
        waiting = [w for w in waiting if w]
        return (f"opens at {lo}", ("waiting there: " + " and ".join(waiting)) if waiting
                else "nothing in it for you yet", "some" if waiting else "none", [])
    xph = gain["xp_per_hour"] if not gain["kills_unknown"] else None
    good_xp = bool(xph and quest_xph and xph >= 0.9 * quest_xph)
    loop_gold = None
    if trip.get("measured") and trip.get("inside_s"):
        loop_gold = max(trip.get("vendor_copper") or 0, trip.get("ah_copper") or 0) / trip["inside_s"] * 3600
    rate = ((mount.get("gold_rate") or {}).get("copper_per_hour")) or 0
    gold = bool(mount.get("behind") and loop_gold and loop_gold > max(rate, 0) * 2)
    # A rare drop stays in the catalogue. Sending the player there for gear
    # requires an easy or moderate source they can use on this run.
    paid = {r["item_id"] for q in left for r in q["rewards"]}
    attainable = []
    for d in drops:
        if d["item_id"] in paid or not d["now"]:
            continue
        sources = [s for s in d["drops"] if gear.action_source([{"kind": "drop", "instance": "dungeon", **s}],
                                                              inp["level"])]
        if sources:
            attainable.append({**d, "drops": sources})
    points = G.drop_value(drop_items(attainable))["known_gain_per_clear"] or 0
    good_gear = points > 0 and points >= (inp.get("gear_score") or 0) * HIGH_GAIN_SHARE / 100
    worth_quests = bool(q_up or (quest_xp and (not quest_xph or quest_xp >= quest_xph / 4)))
    goals = [name for name, met in (("xp", good_xp), ("gear", good_gear or bool(q_up)),
                                    ("quests", worth_quests), ("gold", gold)) if met]
    if good_gear and worth_quests:
        return ("worth a run", f"{quest_bit}, and {ups} at {odds}", "high", goals)
    if good_gear:
        return ("worth a loop for gear", f"{ups} at {odds}", "high", goals)
    if worth_quests:
        return ("worth a run for its quests", quest_bit + (", one pays an upgrade" if q_up else ""), "high", goals)
    if good_xp:
        return ("worth a run for XP", "another run's XP per hour matches your questing", "high", goals)
    if gold:
        return ("worth a loop for gold", "looping it earns twice your gold per hour, and the mount is behind",
                "high", goals)
    if kb_fit:
        why = _n(kb_fit, "drop") + " made for you" + (f", and {quest_bit}" if handins else "")
        return ("worth a look for gear", why, "some", [])
    small = [quest_bit if handins else None, f"{ups} at {odds}" if n_miss else None]
    small = [x for x in small if x]
    if small:
        return ("only if a group is going", " and ".join(small) + ": not worth a trip of its own",
                "some", ["group_only"])
    for q in quests:
        if waits_on_other_wings(q):
            return ("part of a multi-wing quest", wing_progress_text(q), "some", [])
    if later:
        return ("not yet", f"{_n(len(later), 'upgrade')} once you're level {min(d['level'] for d in later)}",
                "some", [])
    return ("done", "nothing left for you", "none", [])
