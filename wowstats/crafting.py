"""What a craft takes, priced: the recipe, the skill and the gold.

Owner, 2026-11-25: the gear advice said "find and learn its recipe" and
"materials are never checked", and an assistant asked "how much to level
Tailoring" fell back to web guides. This module answers both from our own
data (data/craft-reference.json, tools/craft_reference/export.py):

- The route is Polestar's profession guide (the source of truth): from the
  character's skill to the one it needs, the guide's crafts in its order,
  each with the expected crafts over that stretch at the recipe's colours,
  the bolts and other in-between crafts its reagents need, the recipes to
  learn on the way (trainer, with Polestar's price, or a pattern) and the
  trainer rank when the route passes the cap.
- The recipe of a crafted item: known (Altoholic's recipe list), a pattern
  in the bags or bank that teaches it (the client's recipe items), or where
  to get one: Polestar's pattern vendors, a trainer recipe, the auction house
  (the player's latest price), else "not in our data" and the one action.
- The materials: what the route and the item take, less what the bags and
  bank hold (counts, from the shipper's wow-bags), the rest at Polestar's
  vendor price for vendor goods, else the latest auction house price of the
  character's house (any age, said), else unknown and said.
- The other way: a crafted item that doesn't bind when picked up can be
  bought; its latest auction house price, with its age.

Every number comes from the data or is left out with what would bring it.
Chance of a skill point per craft (Classic's rule, the one Polestar's guides
count with): 1 below yellow, (grey - skill) / (grey - yellow) from yellow to
grey, 0 at grey.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path

from wowstats import ahsource, forever, profcaps

_HERE = Path(__file__).resolve().parent
BUNDLED = (_HERE / "data" / "craft-reference.json", _HERE.parent / "data" / "craft-reference.json")
NAMES = {129: "First Aid", 164: "Blacksmithing", 165: "Leatherworking", 171: "Alchemy", 185: "Cooking",
         186: "Mining", 197: "Tailoring", 202: "Engineering", 333: "Enchanting"}
LINES = {v.lower(): k for k, v in NAMES.items()}
EASY, SOME, HARD = 0, 1, 2  # wowstats.gear EFFORT indexes
BANK_OLD_DAYS = 3

_lock = threading.Lock()
_ref: dict | None = None


def reference() -> dict:
    """The craft data, or {} when the file isn't there (every answer then
    says it can't price a craft)."""
    global _ref
    with _lock:
        if _ref is None:
            p = next((p for p in BUNDLED if p.exists()), None)
            _ref = json.loads(p.read_text(encoding="utf-8")) if p else {}
            pats: dict[int, list[int]] = {}
            for item, (spell, _req) in (_ref.get("patterns") or {}).items():
                pats.setdefault(int(spell), []).append(int(item))
            obs = p.parent / "kb" / "professions" / "learning.json" if p else None
            _ref["_observations"] = json.loads(obs.read_text()) if obs and obs.exists() else {}
            _ref["_teaches"] = pats
            _ref["_bop"] = set(_ref.get("bop") or [])
        return _ref


def line_of(profession) -> int | None:
    if isinstance(profession, int):
        return profession if profession in NAMES else None
    return LINES.get(str(profession or "").strip().lower())


def recipe(spell) -> dict | None:
    return (reference().get("recipes") or {}).get(str(spell))


def name(item_id) -> str:
    return forever.item_name(item_id) or f"item {item_id}"


def chance(skill: int, yellow: int, grey: int) -> float:
    if skill < yellow:
        return 1.0
    if skill >= grey or grey <= yellow:
        return 0.0
    return (grey - skill) / (grey - yellow)


def crafts_between(a: int, b: int, yellow: int, grey: int) -> float | None:
    """Expected crafts from skill a to b on one recipe; None when it goes grey first."""
    total = 0.0
    for s in range(a, b):
        p = chance(s, yellow, grey)
        if p <= 0:
            return None
        total += 1 / p
    return total


@dataclass
class Who:
    """What the character brings: its rank and cap in each profession,
    the recipes it knows (None: no recipe list), what it holds (counts,
    None: unknown), the prices of its auction house, its gold and goal."""

    ranks: dict = field(default_factory=dict)  # line -> rank
    caps: dict = field(default_factory=dict)  # line -> cap (None: unknown)
    known: dict = field(default_factory=dict)  # line -> set of spells; a line missing: unknown
    side: str | None = None  # "Alliance" / "Horde"
    held: dict | None = None  # {"bags": {id: n} | None, "bank": {id: n} | None}
    held_at: dict = field(default_factory=dict)  # {"bags": iso, "bank": iso}
    bank_days: int | None = None  # how old the bank read is
    ids: set = field(default_factory=set)  # item ids in the bags and bank (no counts needed)
    ids_by_place: dict = field(default_factory=dict)  # observed ID lists, independent of stack counts
    prices: dict | None = None  # item id -> {price_copper, seen_day, days_ago}; None: no scan
    listings: dict = field(default_factory=dict)  # current items from ahsource.on_sale
    money: int | None = None
    goal: dict | None = None  # ahsource.mount_left

    def knows(self, line: int, spell) -> bool | None:
        got = self.known.get(line)
        if got is None or not spell:
            return None
        return int(spell) in got

    def quantity(self, where: str, item_id: int) -> int | None:
        counts = (self.held or {}).get(where)
        ids = self.ids_by_place.get(where)
        if counts is not None:
            return counts.get(item_id, 1 if ids is not None and item_id in ids else 0)
        if ids is not None and item_id not in ids:
            return 0
        return None

    def minimum(self, where: str, item_id: int) -> int:
        n = self.quantity(where, item_id)
        return n if n is not None else int(item_id in self.ids_by_place.get(where, ()))

    def has(self, item_id: int) -> bool:
        return item_id in self.ids or any(self.minimum(k, item_id) for k in ("bags", "bank"))


def who_from(sheet: dict, held: dict | None, market: dict | None) -> Who:
    """A Who from the gear advice's inputs: the sheet (professions with
    their caps, recipes), wowstats.gear.newest_bags() and ahsource.market()."""
    ranks, caps, known = {}, {}, {}
    for p in sheet.get("professions") or []:
        ln = line_of(p.get("skill_id") if isinstance(p.get("skill_id"), int) else p.get("name"))
        if ln and isinstance(p.get("rank"), (int, float)):
            if p.get("max_rank_from"):
                ranks[ln] = int(p["rank"])
                caps[ln] = p.get("max_rank") if p["max_rank_from"] != "tiers" else None
            else:
                ranks[ln], cap, src = profcaps.cap_of(int(p["rank"]), None, p)
                caps[ln] = cap if src != "tiers" else None
    for r in sheet.get("recipes") or []:
        ln = line_of(r.get("profession")) if isinstance(r, dict) else None
        if ln and isinstance(r.get("known"), list):
            known[ln] = {int(x) for x in r["known"] if isinstance(x, (int, float)) or str(x).isdigit()}
    held_obj, held, held_at, ids, bank_days = held, None, {}, set(), None
    ids_by_place = {}
    if held_obj:
        counts = held_obj.get("counts") or {}
        ids_by_place = {k: set(held_obj[k]) for k in ("bags", "bank") if held_obj.get(k) is not None}
        held = {k: {int(i): int(n) for i, n in c.items()} for k, c in counts.items() if c is not None} or None
        held_at = dict(held_obj.get("at") or {})
        ids = {int(i) for k in ("bags", "bank") for i in held_obj.get(k) or []}
        bank_days = _days_ago(held_at.get("bank"))
    mk = market or {}
    return Who(ranks=ranks, caps=caps, known=known, side=sheet.get("faction"), held=held, held_at=held_at,
               bank_days=bank_days, ids=ids, ids_by_place=ids_by_place, prices=mk.get("latest"),
               listings=mk.get("items") or {},
               money=mk.get("money_copper"),
               goal=mk.get("mount"))


def _days_ago(t) -> int | None:
    import time

    from wowstats.stores import parse_t

    if not t:
        return None
    d = parse_t(t)
    if d.year < 2000:
        return None
    return max(0, int((time.time() - d.timestamp()) // 86400))


# --- prices -------------------------------------------------------------------


def unit_price(item_id: int, who: Who) -> dict | None:
    """What one more of an item costs: Polestar's vendor price for vendor
    goods, else the latest auction house price of the character's house."""
    v = (reference().get("vendor") or {}).get(str(item_id))
    if v:
        return {"copper": int(v), "from": "vendor"}
    p = (who.prices or {}).get(item_id) or (who.prices or {}).get(str(item_id))
    if p and p.get("price_copper"):
        return {"copper": int(p["price_copper"]), "from": "ah", "seen_day": p.get("seen_day"),
                "days_ago": p.get("days_ago")}
    return None


def ah_price(item_id: int, who: Who) -> dict | None:
    p = (who.prices or {}).get(item_id) or (who.prices or {}).get(str(item_id))
    return dict(p) if p and p.get("price_copper") else None


def days_words(d) -> str:
    return {None: "", 0: "today", 1: "yesterday"}.get(d, f"{d} days ago")


# --- the ledger: what a plan takes, against what is held ----------------------


class Ledger:
    """Materials a plan takes, drawn from the bags first, then the bank,
    then bought. One ledger per answer, so the route and the item share the
    same stacks."""

    def __init__(self, who: Who):
        self.who = who
        self.need: dict[int, int] = {}
        self.order: list[int] = []

    def add(self, item_id: int, n: int) -> None:
        if n <= 0:
            return
        if item_id not in self.need:
            self.order.append(item_id)
        self.need[item_id] = self.need.get(item_id, 0) + n

    def held_left(self, item_id: int) -> int:
        stock = sum(self.who.minimum(k, item_id) for k in ("bags", "bank"))
        return max(0, stock - self.need.get(item_id, 0))

    def row(self, i: int, n: int, reserved: int = 0) -> dict:
        bags, bank = (self.who.quantity(k, i) for k in ("bags", "bank"))
        minimum = sum(self.who.minimum(k, i) for k in ("bags", "bank"))
        shortage = max(0, n - max(0, minimum - reserved))
        buy = shortage if shortage == 0 or (bags is not None and bank is not None) else None
        price = unit_price(i, self.who) if shortage else None
        cost = price["copper"] * shortage if price else (0 if not shortage else None)
        row = {"item_id": i, "name": name(i), "need": n, "bags": bags, "bank": bank, "buy": buy,
               "buy_up_to": shortage, "unit_copper": price["copper"] if price else None,
               "price_from": price["from"] if price else None,
               "cost_copper": cost if buy is not None else None, "budget_copper": cost}
        if price and price["from"] == "ah":
            row.update(seen_day=price.get("seen_day"), days_ago=price.get("days_ago"))
        return row

    def rows(self) -> list[dict]:
        return [self.row(i, self.need[i]) for i in self.order]


def mats_summary(rows: list[dict], who: Who) -> dict:
    cost = sum(r["budget_copper"] or 0 for r in rows)
    unpriced = [r for r in rows if r["buy_up_to"] and r["budget_copper"] is None]
    covered = sum(r["buy"] == 0 for r in rows)
    ages = [r["days_ago"] for r in rows if r.get("price_from") == "ah" and r.get("days_ago") is not None]
    return {"cost_copper": cost, "items": len(rows), "covered": covered,
            "counts_known": all(r["buy"] is not None for r in rows),
            "unpriced": [r["name"] for r in unpriced], "oldest_price_days": max(ages) if ages else None}


def _book_recipe(spell: int, crafts: int, led: Ledger, made_here: dict[int, int] | None = None) -> list[dict]:
    """Books a recipe's reagents for `crafts` crafts; a reagent made by an
    in-between craft (bolts) is made from what the holdings don't cover.
    Returns the in-between crafts [{spell, item, count}]."""
    r = recipe(spell)
    if not r or crafts <= 0:
        return []
    extra = []
    for item, n in r["r"]:
        need = n * crafts
        via = (made_here or {}).get(item)
        if via:
            have = led.held_left(item) or 0
            take = min(have, need)
            led.add(item, take)
            make = need - take
            if make > 0:
                per = (recipe(via) or {}).get("n") or 1
                k = math.ceil(make / per)
                extra.append({"spell": via, "item": item, "count": k})
                extra += _book_recipe(via, k, led)
        else:
            led.add(item, need)
    return extra


# --- the route: Polestar's profession guide -------------------------------------


def guide(line: int) -> dict | None:
    return (reference().get("guides") or {}).get(str(line))


def trainer_rank_price(line: int, max_rank: int) -> int | None:
    from wowstats import trainers

    for n in range(1, 5):
        r = trainers.rank(line, n)
        if r and r.get("max") == max_rank:
            return r.get("price_copper")
    return None


def _bracket_from(g: dict, st: dict) -> int | None:
    sec = st.get("section")
    secs = g.get("sections") or []
    return secs[sec - 1][0] if isinstance(sec, int) and 0 < sec <= len(secs) else None


def route(line: int, rank: int, target: int, who: Who, led: Ledger | None = None) -> dict:
    """Polestar's guide from `rank` to `target`: its crafts in order with the
    expected count over that stretch, the in-between crafts, the recipes to
    learn and the trainer ranks on the way, the materials booked in `led`."""
    g = guide(line)
    led = led or Ledger(who)
    out: dict = {"profession": NAMES.get(line), "from": rank, "to": target, "guide": (g or {}).get("id"),
                 "steps": [], "learn": [], "train": [], "complete": True}
    if not g:
        out.update(complete=False, why=f"no Polestar guide for {NAMES.get(line, line)} in our data")
        return out
    if target <= rank:
        return out
    cap = who.caps.get(line)
    s = rank
    pending: list[tuple[dict, dict]] = []  # the guide's learn goals met on the way
    for st in g["steps"]:
        if s >= target:
            break
        goals = st.get("goals") or []
        mains = [x for x in goals if x["t"] == "craft" and x.get("until")]
        for tr in (x for x in goals if x["t"] == "train"):
            # A rank opens the bracket (from, max]: needed when the route goes
            # past `from` and the cap (or, unknown, the skill) is still below.
            mx, frm = tr.get("max"), _bracket_from(g, st)
            if not mx or frm is None or target <= frm or any(t["max"] == mx for t in out["train"]):
                continue
            if (cap is not None and cap < mx) or (cap is None and s <= frm):
                out["train"].append({"rank": tr.get("rank"), "max": mx, "from": frm, "step": st["n"],
                                     "copper": trainer_rank_price(line, mx), "known_cap": cap})
        for le in (x for x in goals if x["t"] == "learn"):
            only = (le.get("only") or {}).get("factions")
            if not (only and who.side and who.side not in only):
                pending.append((st, le))
        for m in mains:
            if s >= target or m["until"] <= s:
                continue
            stop = min(m["until"], target)
            y = m.get("yellow") or (recipe(m["spell"]) or {}).get("y") or 0
            gr = m.get("grey") or (recipe(m["spell"]) or {}).get("g") or 0
            exp = crafts_between(s, stop, y, gr)
            if exp is None:
                continue  # grey before the stretch ends: the guide's next craft takes over
            n = math.ceil(exp)
            made = {x["item"]: x["spell"] for x in goals if x["t"] == "craft" and not x.get("until") and x.get("item")}
            extra = _book_recipe(m["spell"], n, led, made)
            out["steps"].append({"step": st["n"], "title": st.get("title"), "spell": m["spell"],
                                 "item_id": m.get("item"), "name": (recipe(m["spell"]) or {}).get("name")
                                 or name(m.get("item")), "from": s, "to": stop, "crafts": n,
                                 "yellow": y, "grey": gr, "known": who.knows(line, m["spell"]),
                                 "also": [{**x, "name": name(x["item"])} for x in extra], "note": st.get("note")})
            s = stop
    # A recipe is learnt when the route crafts it (a main craft or a bolt in
    # between), once, and only when the character doesn't know it yet.
    used = {x["spell"] for x in out["steps"]} | {a["spell"] for x in out["steps"] for a in x["also"]}
    seen: set[int] = set()
    for st, le in pending:
        sp = le["spell"]
        if sp not in used or sp in seen or who.knows(line, sp):
            continue
        seen.add(sp)
        tr = (reference().get("trainer") or {}).get(str(sp)) or {}
        entry = {"spell": sp, "name": (recipe(sp) or {}).get("name"), "step": st["n"], "known": who.knows(line, sp)}
        if item := le.get("item"):
            pv = (reference().get("pattern_vendor") or {}).get(str(item)) or {}
            entry.update(pattern_item_id=item, pattern=name(item), held=who.has(item), copper=pv.get("copper"),
                         skill=pv.get("skill"))
        else:
            entry.update(trainer=True, copper=tr.get("copper"), skill=tr.get("from"))
        out["learn"].append(entry)
    if s < target:
        out.update(complete=False, reached=s, why=f"Polestar's guide stops at {s}")
    return out


def gold_words(copper: int, who: Who) -> str:
    """'~12g 40s of your 29g 69s; riding at 40 still needs 70g 30s'."""
    t = f"~{ahsource.price_text(copper)}"
    if who.money is not None:
        t += f" of your {ahsource.price_text(who.money)}"
        if copper > who.money:
            t += f" ({ahsource.price_text(copper - who.money)} short)"
    g = who.goal or {}
    if g.get("left_copper"):
        t += f"; riding at {g.get('level')} still needs {ahsource.price_text(g['left_copper'])}"
    return t


def _learn_cost(entries: list[dict]) -> tuple[int, list[str]]:
    cost, unknown = 0, []
    for e in entries:
        if e.get("held"):
            continue
        if e.get("copper") is not None:
            cost += e["copper"]
        else:
            unknown.append(e.get("pattern") or e.get("name") or str(e["spell"]))
    return cost, unknown


# --- one crafted item ------------------------------------------------------------


def recipe_state(line: int, spell: int, who: Who) -> dict:
    """Known, a pattern held, or where a pattern comes from."""
    known = who.knows(line, spell)
    pats = reference().get("_teaches", {}).get(int(spell), [])
    ref = reference()
    observed = ref.get("_observations", {})
    trainer = (ref.get("trainer") or {}).get(str(spell)) or {}
    trainer_obs = (observed.get("trainer") or {}).get(str(spell)) or {}
    trainer_skill = trainer_obs.get("skill") or trainer.get("from")
    paths = []
    for p in pats:
        skill = ref["patterns"][str(p)][1]
        bop = p in ref["_bop"]
        pattern = {"pattern_item_id": p, "pattern": name(p), "skill": skill, "bind_on_pickup": bop}
        if who.has(p):
            paths.append({**pattern, "kind": "held", "location": next(
                (k for k in ("bags", "bank") if who.minimum(k, p)), None), "copper": 0})
        vendor = (ref.get("pattern_vendor") or {}).get(str(p))
        if vendor:
            paths.append({**pattern, "kind": "vendor", "copper": vendor.get("copper")})
        listing = who.listings.get(p)
        if not bop and listing and listing.get("price_copper"):
            paths.append({**pattern, "kind": "ah", "copper": listing["price_copper"], "seen_day": listing["seen_day"]})
        drop = (observed.get("pattern_drop") or {}).get(str(p))
        if drop:
            paths.append({**pattern, "kind": "drop", **drop})
    if trainer or trainer_obs:
        paths.append({"kind": "trainer", "skill": trainer_skill, "copper": trainer.get("copper"),
                      "source": trainer_obs.get("source")})
    if not pats and (recipe(spell) or {}).get("auto"):
        paths.append({"kind": "skill", "copper": 0})
    rank = who.ranks.get(line) or 0
    # Prefer a confirmed way the character can use at this rank. A held
    # pattern costs nothing; a reported drop is a lead rather than stock.
    def priority(w):
        costs_too_much = who.money is not None and w.get("copper") is not None and w["copper"] > who.money
        return (w["kind"] == "drop", costs_too_much, max(0, (w.get("skill") or 0) - rank), w["kind"] != "held",
                w.get("copper") is None, w.get("copper") or 0)
    paths.sort(key=priority)
    selected = paths[0] if paths else {}
    state = "unknown" if known is None else "missing"
    if known or selected.get("kind") == "held":
        state = "known" if known else "in_bags"
    out = {"state": state, "known": known, "spell_id": spell, "where": paths,
           "trainer_skill": trainer_skill, "patterns": pats, "selected": selected}
    if pats:
        p = selected.get("pattern_item_id") or pats[0]
        out.update(pattern_item_id=p, pattern=name(p), pattern_skill=ref["patterns"][str(p)][1],
                   pattern_bop=p in ref["_bop"])
    sib = sibling_pattern(spell, who)
    if sib:
        out["sibling"] = sib
    return out


def sibling_pattern(spell: int, who: Who) -> dict | None:
    """A pattern held that teaches a close cousin of this recipe (another
    Filigreed circlet): what the player may take for the one asked."""
    r = recipe(spell) or {}
    want = set(str(r.get("name") or "").split())
    if not want or not who.ids:
        return None
    for item in sorted(who.ids):
        t = (reference().get("patterns") or {}).get(str(item))
        if not t or t[0] == spell:
            continue
        other = recipe(t[0]) or {}
        words = set(str(other.get("name") or "").split())
        if other.get("p") == r.get("p") and len(want ^ words) == 2 and len(want) == len(words):
            return {"pattern_item_id": item, "pattern": name(item), "makes": other.get("name")}
    return None


def plan(source: dict, item_id: int | None, who: Who) -> dict | None:
    """The whole truth of one craft source (wowstats.gear's shape: profession,
    skill, spell_id): the recipe, the skill-up, the materials, the gold,
    the auction house the other way, the effort and one line."""
    line = line_of(source.get("profession"))
    spell = source.get("spell_id") or source.get("spell")
    if not line or not spell or not reference():
        return None
    need = int(source.get("skill") or 0)
    rank = who.ranks.get(line)
    rs = recipe_state(line, int(spell), who)
    selected = rs.get("selected") or {}
    if rs["state"] != "known" and selected.get("skill"):
        need = selected["skill"]
    elif not need:
        need = rs.get("pattern_skill") or rs.get("trainer_skill") or 0
    led = Ledger(who)
    rt = route(line, rank, need, who, led) if rank is not None and rank < need else None
    learn_cost, learn_unknown = _learn_cost((rt or {}).get("learn") or [])
    train_cost = sum(t.get("copper") or 0 for t in (rt or {}).get("train") or [])
    route_rows = led.rows()
    route_mats = mats_summary(route_rows, who)
    r = recipe(spell) or {}
    item_rows = [led.row(i, n, reserved=led.need.get(i, 0)) for i, n in r.get("r") or []]
    item_mats = mats_summary(item_rows, who)
    pattern_cost = selected.get("copper") if rs["state"] != "known" else 0
    craft_total = (route_mats["cost_copper"] + item_mats["cost_copper"] + learn_cost + train_cost
                   + (pattern_cost or 0))
    bop = int(item_id) in reference().get("_bop", set()) if item_id else None
    buy, quote = None, None
    if item_id and bop is False:
        listing = who.listings.get(int(item_id))
        if listing and listing.get("price_copper"):
            buy = {"copper": listing["price_copper"], "seen_day": listing.get("seen_day"),
                   "days_ago": listing.get("seen_days_ago")}
        elif p := ah_price(int(item_id), who):
            quote = {"copper": p["price_copper"], "seen_day": p.get("seen_day"), "days_ago": p.get("days_ago")}
    can_make = bool(rank is not None and rank >= need and rs["state"] == "known")
    out = {"recipe": rs, "skill": {"profession": NAMES[line], "need": need, "have": rank,
                                   "cap": who.caps.get(line)},
           "route": rt, "route_mats": route_rows, "route_mats_summary": route_mats,
           "item_mats": item_rows, "item_mats_summary": item_mats,
           "learn_copper": learn_cost, "train_copper": train_cost, "pattern_copper": pattern_cost,
           "craft_copper": craft_total, "bind_on_pickup": bop, "buy": buy, "estimate": quote, "can_make": can_make,
           "money_copper": who.money, "goal": who.goal}
    unpriced = [*learn_unknown, *route_mats["unpriced"], *item_mats["unpriced"]]
    unpriced += [f"rank {t['rank']}" for t in (rt or {}).get("train", []) if t.get("copper") is None]
    if pattern_cost is None and rs["state"] not in ("known", "in_bags"):
        unpriced.append(rs.get("pattern") or "learning the recipe")
    if not r.get("r"):
        unpriced.append("recipe materials")
    out["unpriced"] = list(dict.fromkeys(unpriced))
    out["complete_cost"] = (not unpriced and item_mats["counts_known"] and route_mats["counts_known"]
                            and (not rt or rt["complete"]))
    out["craft_effort"] = effort(out, who)
    out["effort"] = EASY if buy and (who.money is None or buy["copper"] <= who.money) else out["craft_effort"]
    out["line"] = line_text(out, who)
    out["action"] = action_text(out, who)
    return out


def _unpriced(p: dict) -> list[str]:
    return sorted(set(p["route_mats_summary"]["unpriced"]) | set(p["item_mats_summary"]["unpriced"]))


def effort(p: dict, who: Who) -> int:
    """EASY: makeable now, or the pattern is held (or known) and the
    skill-up and mats fit the purse with no trainer rank to buy. SOME:
    the recipe can be had (trainer, vendor,
    auction house) but the gold or a trainer rank stands in the way; HARD:
    where the recipe comes from isn't in our data."""
    affordable = who.money is None or p["craft_copper"] <= who.money
    if p["can_make"] and affordable and p["complete_cost"]:
        return EASY
    st = p["recipe"]["state"]
    if st in ("known", "in_bags") and affordable and p["complete_cost"] and not (p.get("route") or {}).get("train"):
        return EASY
    available = any(w["kind"] in ("trainer", "vendor", "ah", "skill") for w in p["recipe"].get("where") or [])
    if st in ("known", "in_bags", "unknown") or available:
        return SOME
    return HARD


def _recipe_words(rs: dict) -> str:
    st = rs["state"]
    if st == "known":
        return "You know the recipe."
    if st == "in_bags":
        return f"You have {rs['pattern']}."
    out = []
    if st == "missing":
        out.append(f"You don't have {rs['pattern']}." if rs.get("pattern") else "You don't know the recipe.")
    else:
        out.append("Check you know the recipe (no recipe list: open the profession window with Altoholic on).")
    for w in rs.get("where") or []:
        k = w["kind"]
        price = f" for {ahsource.price_text(w['copper'])}" if w.get("copper") else ""
        if k == "vendor":
            out.append(f"A vendor sells {name(w['pattern_item_id'])}{price}.")
        elif k == "ah":
            out.append(f"{name(w['pattern_item_id'])} was listed{price} on {w['seen_day']}.")
        elif k == "trainer":
            out.append(f"A trainer teaches it{' from ' + str(w['skill']) if w.get('skill') else ''}{price}.")
        elif k == "drop":
            out.append(f"Recorded drop from {w['npc']}. Availability on this build is unverified.")
    if st == "missing" and rs.get("pattern") and not rs.get("where"):
        out.append("The pattern binds on pickup. Its source is unverified." if rs.get("pattern_bop") else
                   "The pattern source is unknown. Check the auction house with Auctionator.")
    if rs.get("sibling"):
        s = rs["sibling"]
        out.append(f"The one you hold, {s['pattern']}, makes {s['makes']}.")
    return " ".join(out)


def line_text(p: dict, who: Who) -> str:
    """One line a player can act on: the recipe, the points to go and their
    crafts on Polestar's route, the gold against the purse, the auction
    house the other way. What has no price is named, with the one action."""
    parts = [_recipe_words(p["recipe"])]
    sk, rt = p["skill"], p.get("route")
    if sk["have"] is not None and sk["need"] > sk["have"] and rt:
        txt = f"{sk['need'] - sk['have']} {sk['profession']} points to go"
        crafts = ", ".join(f"~{s['crafts']} {s['name']}" for s in rt["steps"][:3])
        if crafts:
            txt += f": {crafts}" + (" and more" if len(rt["steps"]) > 3 else "")
        if rt.get("train"):
            txt += "; train " + ", ".join(f"rank {t['rank']}" for t in rt["train"])
        learn = [e for e in rt.get("learn") or [] if not e.get("held")]
        if learn:
            txt += f"; learn {len(learn)} recipe{'s' if len(learn) > 1 else ''} on the way"
        parts.append(txt + ".")
    elif sk["have"] is None:
        parts.append(f"Your {sk['profession']} skill isn't on the sheet: it needs {sk['need']}.")
    rm, im = p["route_mats_summary"], p["item_mats_summary"]
    if p["craft_copper"] or rm["items"] or im["items"]:
        total = gold_words(p["craft_copper"], who) + ("." if p["complete_cost"] else " (incomplete estimate).")
        if rm["counts_known"] and im["counts_known"]:
            parts.append(f"Bags and bank cover {rm['covered'] + im['covered']} of {rm['items'] + im['items']} "
                         f"mats. Estimated cost {total}")
        else:
            parts.append(f"Estimated cost {total}")
    miss = p["unpriced"]
    if miss:
        more = f" and {len(miss) - 3} more" if len(miss) > 3 else ""
        parts.append(f"No price for {', '.join(miss[:3])}{more}"
                     + (": scan the auction house with Auctionator." if _unpriced(p) else "."))
    if not (rm["counts_known"] and im["counts_known"]) and (rm["items"] or im["items"]):
        parts.append("Bag counts unknown: update the shipper.")
    if p["bind_on_pickup"]:
        parts.append("It binds when picked up: nobody sells it.")
    elif p["buy"]:
        b = p["buy"]
        parts.append(f"Or buy it: {ahsource.price_text(b['copper'])} on the auction house "
                     f"({days_words(b.get('days_ago'))}).")
    elif p.get("estimate"):
        quote = p["estimate"]
        day = quote.get("seen_day") or days_words(quote.get("days_ago"))
        parts.append(f"Last seen for {ahsource.price_text(quote['copper'])} ({day}). No current listing.")
    elif p["bind_on_pickup"] is False:
        parts.append("Not seen on the auction house in your scans.")
    return " ".join(parts)


def action_text(p: dict, who: Who) -> str:
    """The one next step."""
    if p["can_make"]:
        return ("Check the missing material prices or counts before crafting." if not p["complete_cost"] else
                f"Make it: the materials cost ~{ahsource.price_text(p['item_mats_summary']['cost_copper'])}.")
    rs = p["recipe"]
    selected = rs.get("selected") or {}
    rt = p.get("route") or {}
    if rs["state"] == "missing" and selected.get("kind") == "drop":
        return f"Check {selected['npc']}, a recorded drop source unverified on this build."
    first = rt.get("steps") or []
    tr = rt.get("train") or []
    if tr and (not first or first[0]["from"] >= tr[0]["from"]):
        return f"Train {p['skill']['profession']} rank {tr[0]['rank']} at a trainer."
    if first:
        s = first[0]
        return f"Craft ~{s['crafts']} {s['name']} ({p['skill']['profession']} {s['from']} → {s['to']})."
    if rs["state"] == "in_bags":
        where = selected.get("location")
        return (f"Take {rs['pattern']} out of the bank and learn it." if where == "bank" else
                f"Learn {rs['pattern']} from your bags.")
    if rs["state"] == "missing":
        kind = selected.get("kind")
        if kind == "trainer":
            return f"Learn the recipe from a {p['skill']['profession']} trainer."
        if kind in ("ah", "vendor"):
            return f"Buy {rs['pattern']} from " + ("the auction house." if kind == "ah" else "its vendor.")
        if rs.get("pattern_bop"):
            return f"Look for {rs['pattern']} in game. Its drop source is unverified."
        if rs.get("pattern"):
            return f"Check the auction house for {rs['pattern']}."
    return "Check the recipe in your profession window."


def brief(p: dict | None) -> dict | None:
    """What a gear source carries of a plan (the professions tool has the rest)."""
    if not p:
        return None
    sk, rs = p["skill"], p["recipe"]
    blockers = []
    if sk["have"] is None or sk["have"] < sk["need"]:
        blockers.append(f"{sk['profession']} {sk['need']} required"
                        + (f" (you have {sk['have']})" if sk["have"] is not None else ""))
    if rs["state"] not in ("known", "in_bags"):
        blockers.append("Recipe knowledge unknown" if rs["state"] == "unknown" else
                        f"Missing {rs.get('pattern') or 'recipe'}")
    if rs.get("selected", {}).get("kind") == "drop":
        blockers.append(f"Reported drop from {rs['selected']['npc']}, unverified for this build")
    rm, im = p["route_mats_summary"], p["item_mats_summary"]
    return {"skill": sk, "blocker": ". ".join(blockers), "craft_effort": p["craft_effort"],
            "cost_complete": p["complete_cost"], "unpriced": p["unpriced"],
            "materials": {"covered": im["covered"] + rm["covered"], "items": im["items"] + rm["items"],
                          "counts_known": rm["counts_known"] and im["counts_known"]},
            "buy": p.get("buy"), "estimate": p.get("estimate"),
            "steps": [{k: s[k] for k in ("name", "crafts", "from", "to")} for s in (p["route"] or {}).get("steps", [])],
            "riding": {k: p["goal"].get(k) for k in ("level", "left_copper")} if p.get("goal") else None,
            "learn": {"trainer_skill": rs.get("trainer_skill"), "pattern_skill": rs.get("pattern_skill"),
                      "kind": (rs.get("selected") or {}).get("kind"), "pattern_bop": rs.get("pattern_bop"),
                      "pattern_id": rs.get("pattern_item_id")},
            "line": p["line"], "action": p["action"], "effort": p["effort"], "recipe": p["recipe"]["state"],
            "craft_copper": p["craft_copper"], "buy_copper": (p.get("buy") or {}).get("copper"),
            "buy_days_ago": (p.get("buy") or {}).get("days_ago"), "bind_on_pickup": p["bind_on_pickup"],
            "can_make": p["can_make"]}
