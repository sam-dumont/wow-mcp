"""The professions tool: what levelling a profession costs, from our data.

Owner, 2026-11-25: an assistant asked "how much to level Tailoring from 121
to 190" found no answer in the MCP and fell back to a web guide. This tool
answers it alone: Polestar's profession guide as the route (the source of
truth), the recipes the character knows and the patterns it holds, the
materials against its bags and bank (counts), priced at Polestar's vendor
prices and the latest auction house scan of its house, the gold against its
purse and the riding goal, and the crafted gear the skill opens, craft or buy.
All of it from wowstats.crafting and the gear advice's own inputs.
"""

from __future__ import annotations

from wowstats import ahsource, crafting, forever
from wowstats import gear as gearmod

from . import tools as T

MAX_GEAR = 8


def _profession_arg(v) -> int | None:
    if v in (None, ""):
        return None
    line = crafting.line_of(v)
    if line is None:
        raise T.ToolError("profession is one of " + ", ".join(sorted(crafting.NAMES.values())))
    return line


def _guide_step(ctx: T.Ctx, char: str, gid: str | None) -> dict | None:
    """The character's newest step on Polestar's profession guide."""
    if not gid:
        return None
    try:
        rows = ctx.store.logs(f'{T.rec(char)} kind:=step guide:={T.lq(gid)} | fields _time, step '
                              f'| sort by (_time desc) | limit 1', limit=1)
    except T.SourceError:
        return None
    r = (rows or [None])[0]
    if not r or r.get("step") in (None, ""):
        return None
    n = int(float(r["step"]))
    g = crafting.guide(crafting.LINES.get(gid.rsplit("/", 1)[-1].replace("-", " ")) or 0) or {}
    st = next((x for x in g.get("steps") or [] if x["n"] == n), None)
    return {"step": n, "title": (st or {}).get("title"), "at": r.get("_time")}


def _patterns_held(line: int, who: crafting.Who) -> list[dict]:
    out = []
    for item in sorted(who.ids):
        t = (crafting.reference().get("patterns") or {}).get(str(item))
        if not t or (crafting.recipe(t[0]) or {}).get("p") != line:
            continue
        h = who.holds(item)
        out.append({"item_id": item, "name": crafting.name(item), "teaches": (crafting.recipe(t[0]) or {}).get("name"),
                    "spell_id": t[0], "needs_skill": t[1] or None, "learned": who.knows(line, t[0]),
                    "where": None if h is None else ("bags" if h[0] else "bank")})
    return out


def _mats_out(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        x = {"item_id": r["item_id"], "item": r["name"], "need": r["need"], "in_bags": r["bags"],
             "in_bank": r["bank"], "to_buy": r["buy"], "unit_copper": r["unit_copper"],
             "cost_copper": r["cost_copper"], "price_from": r["price_from"]}
        if r.get("price_from") == "ah":
            x["price_seen"] = crafting.days_words(r.get("days_ago"))
        out.append(x)
    return out


def _route_out(rt: dict) -> dict:
    return {"guide": rt.get("guide"), "from": rt["from"], "to": rt["to"], "complete": rt["complete"],
            **({"why_incomplete": rt["why"]} if rt.get("why") else {}),
            "crafts": [{"polestar_step": s["step"], "craft": s["name"], "spell_id": s["spell"], "count": s["crafts"],
                        "skill_from": s["from"], "skill_to": s["to"], "yellow": s["yellow"], "grey": s["grey"],
                        "known": s["known"],
                        "first_make": [{"craft": a["name"], "item_id": a["item"], "count": a["count"]}
                                       for a in s["also"]]} for s in rt["steps"]],
            "learn": [{"recipe": e.get("name"), "spell_id": e["spell"], "polestar_step": e["step"],
                       "from": ("pattern " + e["pattern"]) if e.get("pattern") else "a trainer",
                       "pattern_held": e.get("held"), "needs_skill": e.get("skill"), "copper": e.get("copper")}
                      for e in rt["learn"]],
            "train": [{"rank": t["rank"], "cap": t["max"], "from_skill": t["from"], "polestar_step": t["step"],
                       "copper": t["copper"]} for t in rt["train"]]}


def _trainers(line: int, side: str | None) -> list[dict]:
    from wowstats import trainers

    out = []
    for n in range(1, 5):
        r = trainers.rank(line, n)
        if r:
            out.append({"rank": n, "name": r.get("name"), "cap": r.get("max"), "copper": r.get("price_copper"),
                        "trainers": [{"name": t["name"], "npc_id": t["npc"], "wowhead": t["url"]}
                                     for t in trainers.for_side(r.get("trainers") or [], side)][:4]})
    return out


def _target(line: int, rank: int, cap: int | None, asked) -> int:
    if asked not in (None, ""):
        try:
            t = int(asked)
        except (TypeError, ValueError):
            raise T.ToolError("target is a skill number, e.g. 190") from None
        if not 1 <= t <= 300:
            raise T.ToolError("target is a skill between 1 and 300")
        return t
    if cap and cap > rank:
        return cap
    g = crafting.guide(line) or {}
    return next((b for _a, b in g.get("sections") or [] if b > rank), 300)


def _crafted_gear(ctx: T.Ctx, char: str, g: dict, line: int, rank: int, target: int) -> list[dict]:
    """The gear the advice would have the character craft in this profession
    up to the target skill, best gain first, each with its plan."""
    try:
        a = gearmod.advise(gearmod.reference(), g["sheet"], level=g["level"], done=set(g["done"]),
                           every=True, weights=T.views_weights(ctx, char),
                           held=gearmod.newest_bags(g["held"]), market=g["market"], log=g["log"],
                           route=g["route_at"])
    except gearmod.GearError:
        return []
    out, seen = [], set()
    for u in sorted(a.get("upgrades_all") or a.get("upgrades") or [], key=lambda u: -(u.get("gain") or 0)):
        src = next((s for s in u.get("sources") or [] if s["kind"] == "craft"
                    and crafting.line_of(s.get("profession")) == line and (s.get("skill") or 0) <= target), None)
        if not src or u["item_id"] in seen:
            continue
        seen.add(u["item_id"])
        p = src.get("plan") or {}
        out.append({"item": u.get("name"), "item_id": u["item_id"], "slot": u.get("slot_name"),
                    "level": u.get("level"), "gain_points": u.get("gain"), "needs_skill": src.get("skill"),
                    "line": p.get("line") or gearmod.craft_status(src), "next_step": p.get("action"),
                    "effort": gearmod.EFFORT[p["effort"]] if p.get("effort") is not None else None,
                    "craft_copper": p.get("craft_copper"), "buy_copper": p.get("buy_copper"),
                    "bind_on_pickup": p.get("bind_on_pickup")})
        if len(out) >= MAX_GEAR:
            break
    return out


def _inventory(who: crafting.Who) -> dict:
    out = {"counts_known": who.held is not None, "bags_at": who.held_at.get("bags"),
           "bank_at": who.held_at.get("bank")}
    if who.held is None:
        out["unlock"] = ("Your bag and bank counts haven't reached us: update the shipper and log out once "
                         "(Syndicator saves the counts).")
    elif who.held.get("bank") is None:
        out["note"] = "The bank hasn't been read: open it once in game with Syndicator on."
    elif (who.bank_days or 0) > crafting.BANK_OLD_DAYS:
        out["note"] = (f"The bank as of {str(who.held_at.get('bank'))[:10]} ({who.bank_days} days ago): open it in "
                       "game to refresh.")
    return out


def t_professions(ctx: T.Ctx, args: dict) -> dict:
    char = T.resolve_char(ctx, args)
    want = _profession_arg(args.get("profession"))
    if not crafting.reference():
        raise T.ToolError("the craft data (data/craft-reference.json) isn't installed")
    g = T._gear_inputs(ctx, char)
    if g["early"]:
        return g["early"]
    who = crafting.who_from(g["sheet"], gearmod.newest_bags(g["held"]), g["market"])
    lines = [want] if want else [ln for ln in who.ranks if ln in crafting.NAMES and crafting.guide(ln)]
    if not lines:
        return {"char": char, "professions": [], "why": "no crafting profession on the character sheet",
                "unlock": "Log in once with Polestar on, then log out: the sheet carries the professions."}
    item = args.get("item")
    out = []
    for line in lines:
        rank, cap = who.ranks.get(line), who.caps.get(line)
        if rank is None:
            out.append({"profession": crafting.NAMES[line], "skill": None,
                        "why": "the character doesn't have it on its sheet (or the sheet is older than the training)"})
            continue
        target = _target(line, rank, cap, args.get("target") if want else None)
        if target <= rank:
            out.append({"profession": crafting.NAMES[line], "skill": rank, "cap": cap, "target": target,
                        "why": f"{crafting.NAMES[line]} is already at {rank}: nothing to level up to {target}",
                        "patterns_held": _patterns_held(line, who),
                        "crafted_gear": _crafted_gear(ctx, char, g, line, rank, rank)})
            continue
        led = crafting.Ledger(who)
        rt = crafting.route(line, rank, target, who, led)
        rows = led.rows()
        mats = crafting.mats_summary(rows, who)
        learn_c, learn_unknown = crafting._learn_cost(rt["learn"])
        train_c = sum(t.get("copper") or 0 for t in rt["train"])
        unpriced = mats["unpriced"] + learn_unknown + [f"rank {t['rank']}" for t in rt["train"]
                                                       if t.get("copper") is None]
        total = mats["cost_copper"] + learn_c + train_c
        known = who.known.get(line)
        p = {"profession": crafting.NAMES[line], "skill": rank, "cap": cap, "target": target,
             "recipes_known": len(known) if known is not None else None,
             **({} if known is not None else {
                 "recipes_unlock": "Open the profession window once with Altoholic on, then log out."}),
             "patterns_held": _patterns_held(line, who),
             "polestar_guide_step": _guide_step(ctx, char, rt.get("guide")),
             "route": _route_out(rt), "mats": _mats_out(rows),
             "gold": {"mats_copper": mats["cost_copper"], "recipes_copper": learn_c, "training_copper": train_c,
                      "total_copper": total, "unpriced": unpriced or None,
                      "line": f"{crafting.NAMES[line]} {rank} → {target}: {crafting.gold_words(total, who)}"
                              + (f" (without {', '.join(unpriced[:5])}: no price in our data)" if unpriced else ""),
                      "oldest_price_days": mats["oldest_price_days"]},
             "trainer_ranks": _trainers(line, who.side)}
        st = rt["steps"][0] if rt["steps"] else None
        if rt["train"] and (not st or st["from"] >= rt["train"][0]["from"]):
            p["next_step"] = f"Train {crafting.NAMES[line]} rank {rt['train'][0]['rank']} at a trainer."
        elif st:
            first = st["also"][0] if st["also"] else None
            p["next_step"] = (f"Make {first['count']} {first['name']} first, then " if first else "") + \
                f"craft ~{st['crafts']} {st['name']} ({st['from']} → {st['to']})."
        if item not in (None, ""):
            p["item"] = _item_plan(line, str(item), who)
        p["crafted_gear"] = _crafted_gear(ctx, char, g, line, rank, target)
        out.append(p)
    mk = g["market"] or {}
    scan = mk.get("scan") or {}
    return {"char": char, "money_copper": who.money, "riding_goal": who.goal,
            "auction_house": {"prices_known": bool(who.prices), "newest_scan_day": scan.get("day"),
                              **({} if who.prices else {"unlock": ahsource.notice_none()})},
            "inventory": _inventory(who), "professions": out,
            "say": "Quote each `gold.line` and `next_step` as is; list `route.crafts` in order with their counts "
                   "and `mats` with what is held and what to buy. No web guide: this is Polestar's route."}


def _item_plan(line: int, item: str, who: crafting.Who) -> dict | None:
    """One crafted item, craft or buy: by name or id among this profession's recipes."""
    ref = crafting.reference().get("recipes") or {}
    hits = [(sp, r) for sp, r in ref.items() if r.get("p") == line and r.get("i") and (
        str(r["i"]) == item.strip() or item.strip().lower() in str(r.get("name") or "").lower())]
    if not hits:
        return {"why": f"no {crafting.NAMES[line]} recipe makes {item!r} in our data"}
    sp, r = hits[0]
    p = crafting.plan({"profession": crafting.NAMES[line], "spell_id": int(sp),
                       "skill": _req_skill(int(sp), r)}, r["i"], who)
    if not p:
        return None
    return {"item": forever.item_name(r["i"]) or r.get("name"), "item_id": r["i"], "spell_id": int(sp),
            "needs_skill": p["skill"]["need"], "line": p["line"], "next_step": p["action"],
            "effort": gearmod.EFFORT[p["effort"]], "recipe": p["recipe"], "craft_copper": p["craft_copper"],
            "buy": p["buy"], "bind_on_pickup": p["bind_on_pickup"], "item_mats": _mats_out(p["item_mats"])}


def _req_skill(spell: int, r: dict) -> int:
    """The skill a recipe needs: its pattern's, else the trainer's, else
    where it turns orange (the client's yellow is the first point it can
    give a skill-up, the earliest a trainer would teach it)."""
    for _item, (sp, req) in (crafting.reference().get("patterns") or {}).items():
        if sp == spell and req:
            return req
    tr = (crafting.reference().get("trainer") or {}).get(str(spell))
    if tr and tr.get("from"):
        return tr["from"]
    return r.get("y") or 1


PROF_TOOLS: tuple[T.Tool, ...] = (
    T.Tool("professions", "Professions: skill-up cost",
           "Use this first for any profession question (cost to level Tailoring, what to craft, recipes known, "
           "make or buy an item). Polestar's profession route to `target` (default the cap): crafts with counts, "
           "recipes and trainer ranks; mats against the bags and bank with vendor and auction prices; the gold "
           "against the character's gold and riding goal; the crafted gear it opens, craft or buy. No web guide.",
           T.schema({"char": T.CHAR,
                     "profession": {"type": "string", "description": "E.g. Tailoring. Default: every crafting "
                                                                     "profession the character has."},
                     "target": {"type": "integer", "minimum": 1, "maximum": 300,
                                "description": "The skill to reach (with `profession`). Default: the cap."},
                     "item": {"type": "string", "description": "A crafted item, by part of its name or its id "
                                                               "(with `profession`)."}}),
           t_professions),
)
