"""The gear at past levels, reconstructed where no character sheet exists.

The exact gear comes from wow-gear events (the shipper's character sheet),
which start with the first one it sent. Before that, Polestar_Recorder's
records say what the character got and when:

  turnin   the reward the player picked (`item`: Polestar_Recorder/Quests.lua,
           the GetQuestReward hook stores GetQuestItemInfo("choice", n)'s item
           id and QUEST_TURNED_IN writes it as the record's last field), plus
           the quest's fixed rewards from Polestar's quest table (data/
           gear-reference.json `quests[id].items`)
  loot     `items`, "id:count;id:count" (Finds.lua itemList)
  craft    `item_id`: what a recipe made. For an enchanting spell the
           Recorder writes the item it enchanted instead (Progress.lua
           finishCraft); a spell that isn't the item's own recipe in
           Polestar's craft sources is that: proof the item was owned, and
           almost surely worn, at that moment.

`vendor` records are not purchases: they list what the merchant offered
(World.lua vendorItems), so they never count.

The replay walks those in time order with the character's class, level at
the time and Polestar's own weights (wowstats.gear): an item it could wear
that scores above what the slot holds is "probably equipped" from then.
Then it is anchored on the first real sheet: per slot the replay's last item
is compared with the sheet's, and where they disagree the sheet wins and is
walked back to when that item was got (or to the start of the records when
it was got before them).

Never a fact: every reconstructed slot is `certainty: "likely"` with its
source; slots with nothing to go on are left out (unknown), and so is
everything before the first record.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from wowstats import forever
from wowstats import gear as gm
from wowstats.stores import parse_t

ITEM_RE = re.compile(r"(\d+):(\d+)")
PAIRS = {11: 12, 12: 11, 13: 14, 14: 13}
TWO_HAND = 17
UNWORN_INV = {0, 4, 18, 19, 24, 27}  # nothing, shirt, bag, tabard, ammo, quiver: no score, not tracked


@dataclass(order=True)
class Acq:
    t: datetime
    item_id: int = field(compare=False)
    kind: str = field(compare=False)  # quest | loot | craft | enchant
    quest_id: int | None = field(default=None, compare=False)
    quest: str | None = field(default=None, compare=False)
    zone: str | None = field(default=None, compare=False)
    npc: str | None = field(default=None, compare=False)

    def source(self) -> dict:
        return {k: v for k, v in (("kind", self.kind), ("quest", self.quest), ("zone", self.zone),
                                  ("npc", self.npc), ("t", iso(self.t))) if v is not None}


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return None


def acquisitions(ref: gm.Reference, turnins: list[dict], loots: list[dict], crafts: list[dict]) -> list[Acq]:
    """Every item got, oldest first, from the Recorder's rows."""
    quests = ref.data.get("quests") or {}
    craft_spells: dict[int, set[int]] = {}
    for sid, srcs in (ref.data.get("sources") or {}).items():
        for s in srcs:
            if s.get("kind") == "craft" and s.get("spell"):
                craft_spells.setdefault(int(sid), set()).add(int(s["spell"]))
    out: list[Acq] = []
    for r in turnins:
        t, q = parse_t(r.get("_time")), _int(r.get("quest_id"))
        name = (r.get("quest_name") or (quests.get(str(q)) or {}).get("name")
                or (forever.quest(q) or {}).get("name"))
        got = [_int(r.get("item"))] + [_int(i) for i in (quests.get(str(q)) or {}).get("items") or []]
        for iid in dict.fromkeys(i for i in got if i):
            out.append(Acq(t, iid, "quest", q, name, r.get("zone") or None))
    for r in loots:
        t = parse_t(r.get("_time"))
        for m in ITEM_RE.finditer(r.get("items") or ""):
            out.append(Acq(t, int(m.group(1)), "loot", zone=r.get("zone") or None, npc=r.get("npc_name") or None))
    for r in crafts:
        iid, spell = _int(r.get("item_id")), _int(r.get("spell_id"))
        if not iid:
            continue
        own = spell in craft_spells.get(iid, set())
        out.append(Acq(parse_t(r.get("_time")), iid, "craft" if own else "enchant"))
    return sorted(out)


@dataclass
class Span:
    """One item in one slot from `t` on."""

    t: datetime
    item_id: int
    source: dict


@dataclass
class Replay:
    start: datetime  # the first record: nothing is known before it
    slots: dict[int, list[Span]] = field(default_factory=dict)

    def at(self, t: datetime) -> dict[int, Span]:
        out = {}
        for slot, spans in self.slots.items():
            cur = None
            for s in spans:
                if s.t <= t:
                    cur = s
                else:
                    break
            if cur is not None and cur.item_id:
                out[slot] = cur
        return out

    def put(self, slot: int, span: Span) -> None:
        self.slots.setdefault(slot, []).append(span)
        self.slots[slot].sort(key=lambda s: s.t)


class Scorer:
    """Polestar's score for one class and spec at a given level."""

    def __init__(self, ref: gm.Reference, cls: str, key: str, racial: dict | None):
        self.ref, self.cls, self.key, self.racial = ref, cls, key, racial
        self._w: dict[int, dict | None] = {}

    def weights(self, level: int) -> dict | None:
        if level not in self._w:
            from wowstats import gearfit

            self._w[level] = gearfit.model_weights(self.ref, gm.weights_for(self.ref, self.cls, self.key, level)[0],
                                                   self.cls, self.key, level)
        return self._w[level]

    def score(self, item_id: int | None, level: int, slot: int) -> float:
        info = self.ref.item(item_id) if item_id else None
        w = self.weights(level)
        return gm.score_with(info, w, slot == gm.OFF, self.racial) if info and w else 0.0


def replay(ref: gm.Reference, scorer: Scorer, acqs: list[Acq], dings: list[tuple[datetime, int]],
           start: datetime) -> Replay:
    """The gear the records point to, slot by slot."""
    from wowstats import insights

    rp = Replay(start)
    worn: dict[int, int] = {}
    waiting: list[Acq] = []  # got before the level that lets it be worn

    def level_at(t: datetime) -> int:
        return insights.level_at(dings, t) or 1

    def place(a: Acq, t: datetime, force: bool) -> None:
        info = ref.item(a.item_id)
        lvl = level_at(t)
        slots = gm.SLOTS_OF.get(info["inv"]) or ()
        if info["inv"] == TWO_HAND:
            new = scorer.score(a.item_id, lvl, gm.MAIN)
            old = scorer.score(worn.get(gm.MAIN), lvl, gm.MAIN) + scorer.score(worn.get(gm.OFF), lvl, gm.OFF)
            if force or new > old:
                worn[gm.MAIN] = a.item_id
                rp.put(gm.MAIN, Span(t, a.item_id, a.source()))
                if worn.pop(gm.OFF, None):
                    rp.put(gm.OFF, Span(t, 0, {}))
            return
        if slots == (gm.OFF,) and ref.item(worn.get(gm.MAIN) or 0) and ref.item(worn[gm.MAIN])["inv"] == TWO_HAND:
            return  # an off-hand while a two-hander is worn: not a swap the replay can judge
        if len(slots) == 2:  # rings, trinkets: the empty or the weaker one
            slot = min(slots, key=lambda s: (s in worn, scorer.score(worn.get(s), lvl, s)))
        else:
            slot = slots[0]
        if a.item_id in worn.values() and not force:
            return
        if force or scorer.score(a.item_id, lvl, slot) > scorer.score(worn.get(slot), lvl, slot):
            if force and a.item_id in worn.values():
                return
            worn[slot] = a.item_id
            # Enchanted before any other record of it: owned before the
            # records began, so worn since then unless the slot had another.
            t0 = start if force and slot not in rp.slots else t
            rp.put(slot, Span(t0, a.item_id, a.source()))

    def ding_for(w: Acq) -> datetime | None:
        req = ref.item(w.item_id).get("req") or 0
        return next((t for t, lv in dings if lv >= req and t >= w.t), None)

    def flush(until: datetime | None) -> None:
        """Waiting items whose level came by `until` (None: ever), at their ding, in order."""
        due = sorted(((d, w) for w in waiting if (d := ding_for(w)) and (until is None or d <= until)),
                     key=lambda x: x[0])
        for d, w in due:
            waiting.remove(w)
            place(w, d, False)

    for a in acqs:
        flush(a.t)
        info = ref.item(a.item_id)
        if not info or info.get("inv") in UNWORN_INV or not gm.SLOTS_OF.get(info.get("inv")):
            continue
        usable, _, min_level = gm.usability(ref, info, scorer.cls, level_at(a.t))
        if not usable:
            continue
        if min_level > level_at(a.t) and a.kind != "enchant":
            waiting.append(a)
            continue
        place(a, a.t, a.kind == "enchant")
    flush(None)
    return rp


def anchor(ref: gm.Reference, rp: Replay, acqs: list[Acq], sheet_items: list[dict], sheet_t: datetime) -> dict:
    """Compare the replay at the first sheet with the sheet, then make the
    replay agree with it. Per slot the sheet has an item in: "agree" (the
    replay ended on it), "differs" (it ended on another one) or "no_record"
    (it had nothing: the item was got before the records began).
    -> {"slots": {slot name: verdict}, "agree": n, "differs": n, "no_record": n, "total": n}."""
    have = {int(i["slot"]): int(i["item_id"]) for i in sheet_items if _int(i.get("slot")) and _int(i.get("item_id"))}
    have = {s: i for s, i in have.items() if s in gm.EQUIPPED_SLOTS}
    end = {s: sp.item_id for s, sp in rp.at(sheet_t).items()}
    result: dict[int, str] = {}
    for s in sorted(have):
        other = end.get(PAIRS[s]) if s in PAIRS else None  # either finger, either trinket
        if have[s] in (end.get(s), other):
            result[s] = "agree"
        elif end.get(s) is None and (s not in PAIRS or other is None or other in have.values()):
            result[s] = "no_record"
        else:
            result[s] = "differs"
    first = {}
    for a in acqs:
        if a.t <= sheet_t:
            first.setdefault(a.item_id, a)
    for s, iid in have.items():
        ok = iid in (end.get(s), end.get(PAIRS[s]) if s in PAIRS else None)
        if ok:
            # Same item, maybe the other ring slot: put it where the sheet has it.
            if end.get(s) != iid:
                _swap(rp, s, PAIRS[s])
            continue
        a = first.get(iid)
        since, src = (a.t, a.source()) if a else (rp.start, {"kind": "sheet", "t": iso(sheet_t)})
        spans = [sp for sp in rp.slots.get(s, []) if sp.t < since]
        rp.slots[s] = spans + [Span(since, iid, src)]
    n = {k: sum(1 for v in result.values() if v == k) for k in ("agree", "differs", "no_record")}
    # Two fingers, two trinkets: named by slot number so neither hides the other.
    return {"slots": {f"{gm.SLOT_NAMES.get(s, s)}" + (f" {1 + (s in (12, 14))}" if s in PAIRS else ""): v
                      for s, v in result.items()}, **n, "total": len(result)}


def _swap(rp: Replay, a: int, b: int) -> None:
    rp.slots[a], rp.slots[b] = rp.slots.get(b, []), rp.slots.get(a, [])


def items_at(ref: gm.Reference, rp: Replay, t: datetime, names: dict | None = None) -> list[dict]:
    """The reconstructed slots at `t`, as snapshot items marked likely."""
    names = names or {}
    out = []
    for slot, sp in sorted(rp.at(t).items()):
        info = ref.item(sp.item_id) or {}
        out.append({"slot": slot, "slot_name": gm.SLOT_NAMES.get(slot), "item_id": sp.item_id,
                    "name": names.get(str(sp.item_id)) or (ref.data.get("names") or {}).get(str(sp.item_id)),
                    "quality": info.get("quality"), "item_level": info.get("ilvl"),
                    "req_level": info.get("req") or None, "certainty": "likely", "source": sp.source})
    return out


# The rows the replay reads, as LogsQL after the recorder's character filter.
Q_TURNIN = "kind:=turnin | fields _time, quest_id, quest_name, item, zone"
Q_LOOT = "kind:=loot items:* | fields _time, items, item_names, zone, npc_name"
Q_CRAFT = "kind:=craft item_id:* | fields _time, spell_id, item_id"


def build(ref: gm.Reference, sheet: dict, turnins: list[dict], loots: list[dict], crafts: list[dict],
          dings: list[tuple[datetime, int]], first_sheet: dict | None) -> tuple[Replay, dict | None, dict] | None:
    """The anchored replay, the agreement with the first sheet, the shipped
    item names. None without records or a class Polestar scores."""
    cls = gm.class_token(sheet.get("class"))
    if not ref.available or not cls or cls not in (ref.gear.get("WEIGHTS") or {}):
        return None
    rows = [r for r in turnins + loots + crafts if r.get("_time")]
    if not rows:
        return None
    start = min(parse_t(r["_time"]) for r in rows)
    key, _ = gm.resolve_spec(ref, cls, sheet)
    race_id = gm.RACE_IDS.get(str(sheet.get("race") or "").strip().lower())
    racial = (ref.gear.get("RACIALS") or {}).get(str(race_id)) if race_id else None
    acqs = acquisitions(ref, turnins, loots, crafts)
    rp = replay(ref, Scorer(ref, cls, key, racial), acqs, dings, start)
    check = None
    if first_sheet and first_sheet.get("_time"):
        t = parse_t(first_sheet["_time"])
        check = anchor(ref, rp, acqs, first_sheet.get("items") or [], t)
    names: dict = {}
    for r in loots:
        with contextlib.suppress(ValueError):
            names.update(json.loads(r.get("item_names") or "{}"))
    return rp, check, names
