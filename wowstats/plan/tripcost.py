"""A dungeon trip from where the player stands: the quests Polestar's
pick-up guide takes before the run and its hand-in guide after it, the
travel between them, and the seconds of each part.

The rules are Polestar's (addon/Polestar/Dungeons/Pickup.lua, Handin.lua),
ported once to tools/route_reference/trips.mjs, which writes the data they
read into data/route-reference.json `trips`, and once here, to replay them on
the player's own state: his level, race and class, the quests he handed in
and holds, his last known position. tests/plan/test_tripcost.py checks
this port against the exporter's tours, stop for stop and second for second.

`trip(...)` answers None when the reference has no tour data for the
dungeon (a Forever dungeon Polestar has no entrance for): the caller then
falls back on the measured means of his other trips, labelled so.
"""

from __future__ import annotations

import math
from functools import cache

SIDE_BIT = {"Alliance": 1, "Horde": 2}
LETTER = {"Alliance": "A", "Horde": "H"}
# forever-db race masks: the client's bits, Forever's Skyborne on each side's own.
RACE_BIT = {"HUMAN": 1, "ORC": 2, "DWARF": 4, "NIGHTELF": 8, "SCOURGE": 16, "UNDEAD": 16, "TAUREN": 32,
            "GNOME": 64, "TROLL": 128}
SKYBORNE = {"Alliance": 1 << 32, "Horde": 1 << 33}
CLASS_BIT = {"WARRIOR": 1, "PALADIN": 2, "HUNTER": 4, "ROGUE": 8, "PRIEST": 16, "SHAMAN": 64, "MAGE": 128,
             "WARLOCK": 256, "DRUID": 1024}


def race_bit(race: str | None, side: str | None) -> int:
    r = (race or "").replace(" ", "").upper()
    if r == "SKYBORNE":
        return SKYBORNE.get(side or "", 0)
    return RACE_BIT.get(r, 0)


# ---------------------------------------------------------------- travel


def eligible(side: int, races: int, classes: int, side_bit: int, rb: int, cb: int) -> bool:
    """Apply the trip's side, race and class masks to one quest."""
    return not ((side_bit and side and not side & side_bit) or (rb and races and not races & rb)
                or (classes and not classes & cb))


def _dist(a, b) -> float:
    dx = b[1] - a[1]
    dy = b[2] - a[2]
    return math.sqrt(dx * dx + dy * dy)


class Travel:
    """ns.TravelCost as the exporter ports it (trips.mjs network/travelCost):
    a straight walk, or walk + the side's network (flights, boats, walks
    between its nodes) + walk. Points are (c, wx, wy) tuples."""

    def __init__(self, trips: dict, side: str):
        r = trips["rules"]
        self.r = r
        self.run = r["run_speed"]
        self.detour = {int(k): v for k, v in r["detour"].items()}
        bit = SIDE_BIT[side]
        g = trips["graph"]
        taxi = [n for n in g["taxi"] if n[4] & bit]
        at = {n[0]: i for i, n in enumerate(taxi)}
        nodes = [(n[1], n[2], n[3]) for n in taxi]
        rides = []
        for _tid, fac, a, b, wa, ab, wb, ba in g["transports"]:
            if not fac & bit:
                continue
            nodes.append(tuple(a))
            ia = len(nodes) - 1
            nodes.append(tuple(b))
            ib = len(nodes) - 1
            w = r["transport_wait"]
            rides.append((ia, ib, wa * w + ab))
            rides.append((ib, ia, wb * w + ba))
        inf = math.inf
        nt = len(taxi)
        fly = [[0.0 if i == j else inf for j in range(nt)] for i in range(nt)]
        for f, t, yards in g["legs"]:
            a, b = at.get(f), at.get(t)
            if a is None or b is None:
                continue
            s = (yards or 0) / r["flight_speed"] + r["flight_stop"]
            if s < fly[a][b]:
                fly[a][b] = s
        _floyd(fly)
        n = len(nodes)
        d = [[0.0 if i == j else inf for j in range(n)] for i in range(n)]
        for i in range(nt):
            for j in range(nt):
                if i != j and fly[i][j] < inf:
                    d[i][j] = fly[i][j] + r["flight_overhead"]
        for a, b, s in rides:
            if s < d[a][b]:
                d[a][b] = s
        for i in range(n):
            for j in range(n):
                if i != j and nodes[i][0] == nodes[j][0]:
                    w = self.walk(nodes[i], nodes[j])
                    if w < d[i][j]:
                        d[i][j] = w
        _floyd(d)
        self.nodes, self.d = nodes, d
        self._near: dict = {}

    def walk(self, a, b) -> float:
        detour = self.detour[a[0]] if a[0] in self.detour else self.r["walk_detour"]
        return (_dist(a, b) * detour) / self.run

    def near(self, p) -> list[int]:
        k = (p[0], p[1], p[2])
        if k not in self._near:
            xs = sorted(((_dist(p, n), i) for i, n in enumerate(self.nodes) if n[0] == p[0]))
            self._near[k] = [i for _, i in xs[:self.r["near_nodes"]]]
        return self._near[k]

    def cost(self, a, b) -> float | None:
        if a is None or b is None:
            return None
        best = self.walk(a, b) if a[0] == b[0] else math.inf
        to = self.near(b)
        for i in self.near(a):
            wa = self.walk(a, self.nodes[i])
            row = self.d[i]
            for j in to:
                v = wa + row[j] + self.walk(self.nodes[j], b)
                if v < best:
                    best = v
        return self.r["cross_continent_s"] if best == math.inf else best


def _floyd(d: list[list[float]]) -> None:
    n = len(d)
    for k in range(n):
        dk = d[k]
        for i in range(n):
            dik = d[i][k]
            if dik == math.inf:
                continue
            di = d[i]
            for j in range(n):
                v = dik + dk[j]
                if v < di[j]:
                    di[j] = v


# ---------------------------------------------------------------- stop order


def plan_stops(stops: list[dict], start, finish, t, rules: dict) -> tuple[list[int], float]:
    """Dungeons.PlanStops (Pickup.lua) without the log's room: the stops at
    hand first, exact up to `exact` stops, else nearest-next and cheapest
    insertion, each improved by moving runs of one to three stops. Stops
    are {inP, outP, deps: [index]}; t(a, b) -> seconds. -> (order, cost)"""
    n = len(stops)
    far = rules["far"]

    def T(a, b):
        if a is None or b is None:
            return far
        if a is b or a == b:
            return 0
        v = t(a, b)
        return far if v is None else v

    def out_of(i):
        return start if i == 0 else stops[i - 1]["outP"]

    def in_of(i):
        return stops[i - 1]["inP"]

    def tail(i):
        return T(out_of(i), finish) if finish is not None else 0

    deps = [{d + 1 for d in s["deps"]} for s in stops]

    def ready(mask, i):
        return not mask >> (i - 1) & 1 and all(mask >> (d - 1) & 1 for d in deps[i - 1])

    def cost(o):
        c, prev = 0, 0
        for i in o:
            c += T(out_of(prev), in_of(i))
            prev = i
        return c + tail(prev)

    def done(o):
        return [i - 1 for i in o], cost(o)

    if n == 0:
        return [], tail(0)
    order: list[int] = []
    mask0 = last0 = 0
    while True:
        here, bc, bi = out_of(last0), math.inf, 0
        for i in range(1, n + 1):
            if ready(mask0, i) and T(start, in_of(i)) <= rules["at_hand_s"]:
                v = T(here, in_of(i))
                if v < bc:
                    bc, bi = v, i
        if not bi:
            break
        order.append(bi)
        mask0 += 1 << (bi - 1)
        last0 = bi
    fixed = len(order)
    if fixed == n:
        return done(order)
    if n <= rules["exact"]:
        full = (1 << n) - 1
        nxt: dict = {}

        @cache
        def best(mask, last):
            if mask == full:
                return tail(last)
            here = out_of(last)
            bc, bi = math.inf, 0
            for i in range(1, n + 1):
                if ready(mask, i):
                    v = T(here, in_of(i)) + best(mask + (1 << (i - 1)), i)
                    if v < bc:
                        bc, bi = v, i
            nxt[(mask, last)] = bi
            return bc

        best(mask0, last0)
        o, mask, last = order[:fixed], mask0, last0
        while mask != full:
            i = nxt.get((mask, last))
            if not i:
                break
            o.append(i)
            mask += 1 << (i - 1)
            last = i
        best.cache_clear()
        if len(o) == n:
            return done(o)

    def valid(o):
        if len(o) != n:
            return False
        pos = {i: p for p, i in enumerate(o)}
        return all(d in pos and pos[d] < p for p, i in enumerate(o) for d in deps[i - 1])

    near = order[:fixed]
    mask, last = mask0, last0
    for _ in range(fixed, n):
        bc, bi = math.inf, 0
        for i in range(1, n + 1):
            if ready(mask, i):
                v = T(out_of(last), in_of(i))
                if v < bc:
                    bc, bi = v, i
        if not bi:
            break
        near.append(bi)
        mask += 1 << (bi - 1)
        last = bi
    ins = order[:fixed]
    placed = set(ins)
    for _ in range(fixed, n):
        bi = next((i for i in range(1, n + 1) if i not in placed and deps[i - 1] <= placed), 0)
        if not bi:
            break
        lo = fixed
        for p in range(fixed + 1, len(ins) + 1):
            if ins[p - 1] in deps[bi - 1]:
                lo = p
        bc, bp = math.inf, -1
        for p in range(lo, len(ins) + 1):
            a = 0 if p == 0 else ins[p - 1]
            nx = ins[p] if p < len(ins) else None
            v = T(out_of(a), in_of(bi)) + (T(stops[bi - 1]["outP"], in_of(nx)) - T(out_of(a), in_of(nx))
                                           if nx else tail(bi) - tail(a))
            if v < bc:
                bc, bp = v, p
        if bp < 0:
            break
        ins.insert(bp, bi)
        placed.add(bi)

    def improve(o):
        m = len(o)

        def out_at(k):
            return start if k <= 0 else stops[o[k - 1] - 1]["outP"]

        def in_at(k):
            return stops[o[k - 1] - 1]["inP"]

        def tail_at(k):
            return tail(0 if k <= 0 else o[k - 1])

        def edge(k):
            return T(out_at(k), in_at(k + 1)) if k < m else tail_at(k)

        def movable(a, L, b):
            if b < a:
                return not any(o[q - 1] in deps[o[s - 1] - 1] for q in range(b + 1, a) for s in range(a, a + L))
            return not any(o[s - 1] in deps[o[q - 1] - 1] for q in range(a + L, b + 1) for s in range(a, a + L))

        def moved(a, L, b):
            run = o[a - 1:a - 1 + L]
            x = []
            for k in range(m + 1):
                if k > 0 and (k < a or k >= a + L):
                    x.append(o[k - 1])
                if k == b:
                    x.extend(run)
            return x

        rounds, improved = 0, True
        while improved and rounds < 50:
            improved, rounds = False, rounds + 1
            for L in range(1, 4):
                for a in range(fixed + 1, m - L + 2):
                    z = a + L - 1
                    out = (T(out_at(a - 1), in_at(z + 1)) if z < m else tail_at(a - 1)) - T(out_at(a - 1), in_at(a)) \
                        - edge(z)
                    for b in range(fixed, m + 1):
                        if b < a - 1 or b > z:
                            into = T(out_at(b), in_at(a)) + (T(out_at(z), in_at(b + 1)) if b < m
                                                             else tail(o[z - 1])) - edge(b)
                            if out + into < -0.5 and movable(a, L, b):
                                o = moved(a, L, b)
                                improved = True
                                break
                    if improved:
                        break
                if improved:
                    break
        return o

    best_o, best_c = None, math.inf
    for c in (near, ins):
        if not valid(c):
            continue
        o = improve(c)
        v = cost(o)
        if v < best_c:
            best_o, best_c = o, v
    return done(best_o or near)


# ---------------------------------------------------------------- the rules


class Rules:
    """makeRules (trips.mjs): what the pick-up rules ask of a quest for this
    character."""

    def __init__(self, quests: dict, t, side: str, player: dict):
        self.q, self.t = quests, t
        self.bit = SIDE_BIT[side]
        self.p = player

    def Q(self, qid):  # noqa: N802 - Polestar's name
        return self.q.get(qid)

    def done(self, qid) -> bool:
        return qid in self.p["done"]

    def in_log(self, qid) -> bool:
        return qid in self.p["log"]

    def applies(self, q) -> bool:
        return eligible(q.get("side", 0), q.get("races", 0), q.get("classes", 0), self.bit,
                        self.p.get("race_bit") or 0, self.p.get("class_bit") or 0)

    def _nearest(self, pts, start):
        best, bt = None, math.inf
        for p in pts:
            if not p[3] & self.bit:
                continue
            pt = (p[0], p[1], p[2])
            v = self.t(start, pt) if start is not None else 0
            if v < bt:
                bt, best = v, (pt, p[4])
        return best

    def giver(self, q, start):
        return None if q.get("item") else self._nearest(q.get("g") or [], start)

    def ender(self, q, start):
        return self._nearest(q.get("e") or [], start)

    @staticmethod
    def starts_at(r, ends_of) -> bool:
        keys = {e[4] for e in ends_of.get("e") or []}
        return any(p[4] in keys for p in r.get("g") or [])

    def errand(self, r, start) -> bool:
        if r.get("obj") or r.get("item"):
            return False
        g = self.giver(r, start)
        return bool(g and self.ender(r, g[0]))

    def startable(self, r, start) -> bool:
        g = self.giver(r, start)
        return bool(g and self.ender(r, g[0]))

    def pre_done(self, r) -> bool:
        any_ = r.get("any") or []
        return all(self.done(p) for p in r.get("pre") or []) and (not any_ or any(self.done(p) for p in any_))

    def chain(self, qid) -> list[int]:
        out, seen = [], set()

        def walk(x):
            if x in seen:
                return
            seen.add(x)
            r = self.Q(x)
            if not r:
                return
            for p in r.get("pre") or []:
                if not self.done(p):
                    walk(p)
            any_ = r.get("any") or []
            if any_ and not any(self.done(p) for p in any_):
                pick = next((p for p in any_ if self.Q(p) and self.applies(self.Q(p))), None)
                if pick is not None:
                    walk(pick)
            out.append(x)

        walk(qid)
        return out[:-1]


def pickup_plan(R: Rules, page: list[int], min_level: int, start, entrance) -> dict:
    """PickupPlan + dropDetours + crumbsFirst (trips.mjs pickupPlan)."""
    reach = max(R.p.get("level") or 1, min_level or 1)
    on_page = set(page)
    plan = {"take": [], "carried": [], "closed_by": {}, "crumbs": []}
    for qid in page:
        q = R.Q(qid)
        if not q or not R.applies(q) or R.done(qid):
            continue
        if R.in_log(qid):
            plan["carried"].append(qid)
            continue
        if q.get("money_only") or (q.get("min") or 1) > reach:
            continue
        g = R.giver(q, start)
        if not g:
            continue
        ok, pre = True, []
        for pid in R.chain(qid):
            r = R.Q(pid)
            if not r:
                ok = False
                break
            if (pid in on_page and not R.errand(r, start)) or not R.applies(r):
                ok = False
            if (r.get("min") or 1) > reach:
                ok = False
            if not R.in_log(pid) and not R.startable(r, start):
                ok = False
            if not R.in_log(pid) and r.get("inside_only"):
                ok = False
            pre.append(pid)
        if not ok:
            continue
        plan["take"].append({"id": qid, "giver": g, "pre": _with_breadcrumbs(R, q, qid, pre, on_page, start,
                                                                             plan["closed_by"])})
    on_way = {p for t in plan["take"] for p in t["pre"]}
    plan["take"] = [t for t in plan["take"] if t["id"] not in on_way]
    plan["carried"] = [x for x in plan["carried"] if x not in on_way]
    return _crumbs_first(R, _drop_detours(R, plan, start, entrance))


def _with_breadcrumbs(R, q, qid, pre, on_page, start, closed_by):
    if not pre:
        return pre
    named = {qid, *pre}
    for x in (qid, *pre):
        for k in ("pre", "any"):
            named.update((R.Q(x) or {}).get(k) or [])
    before: dict[int, list[int]] = {}
    for x in (qid, *pre):
        for b in (R.Q(x) or {}).get("crumbs") or []:
            r = R.Q(b)
            if not r or b in named or b in on_page or R.done(b) or R.in_log(b):
                continue
            if not R.applies(r) or not R.pre_done(r) or not R.errand(r, start):
                continue
            for pid in pre:
                if not R.in_log(pid) and R.starts_at(R.Q(pid), r):
                    before.setdefault(pid, []).append(b)
                    closed_by[b] = [pid]
                    named.add(b)
                    break
    if not before:
        return pre
    return [y for pid in pre for y in (*before.get(pid, []), pid)]


def _drop_detours(R, plan, start, entrance):
    at: dict[int, tuple | None] = {}
    ids: list[int] = []

    def add(x, pt):
        if x in at:
            return
        at[x] = pt
        ids.append(x)

    for t in plan["take"]:
        add(t["id"], t["giver"][0])
        for x in t["pre"]:
            g = R.giver(R.Q(x), start)
            add(x, g[0] if g else None)
    ids.sort()
    named = {p for x in ids for k in ("pre", "any") for p in (R.Q(x) or {}).get(k) or []}
    crumbs: dict[int, list[int]] = {}
    for x in ids:
        r = R.Q(x)
        if x in named or R.in_log(x) or r.get("obj") or not at.get(x) or R.starts_at(r, r):
            continue
        for tid in ids:
            if tid != x and at.get(tid) and R.starts_at(R.Q(tid), r):
                crumbs.setdefault(x, []).append(tid)
    if not crumbs:
        return plan
    stops = [p for p in (start, entrance) if p]
    for x in ids:
        if x in crumbs:
            continue
        if at.get(x):
            stops.append(at[x])
        e = R.ender(R.Q(x), at.get(x) or start)
        if e:
            stops.append(e[0])

    def after_own(r):
        out = []
        for k in ("pre", "any"):
            for pid in r.get(k) or []:
                if pid not in at:
                    continue
                e = R.ender(R.Q(pid), at.get(pid) or start)
                if e:
                    out.append(e[0])
        if out and entrance:
            out.append(entrance)
        return out or None

    drop = set()
    for x in ids:
        targets = crumbs.get(x)
        if not targets:
            continue
        g = at[x]
        c = g[0] if g else None
        sea = c is not None
        for tid in targets:
            tc = at[tid][0] if at.get(tid) else None
            if tc is None or tc == c:
                sea = False
        near = any(a[0] == c for a in stops)
        best = None
        for a in after_own(R.Q(x)) or stops:
            for tid in targets:
                xa, y, z = R.t(a, g), R.t(g, at[tid]), R.t(a, at[tid])
                if xa is not None and y is not None and z is not None and (best is None or xa + y - z < best):
                    best = xa + y - z
        far = c is not None and not near
        rules = R.p["rules"]
        dropped = far or (best is not None and (best > rules["detour_s"] or (sea and best > rules["near_s"])))
        if dropped:
            drop.add(x)
        plan["crumbs"].append({"id": x, "targets": targets, "detour_s": None if best is None else _jsround(best),
                               "far": far, "sea": sea, "taken": not dropped})
    if drop:
        plan["take"] = [{**t, "pre": [p for p in t["pre"] if p not in drop]} for t in plan["take"]
                        if t["id"] not in drop]
    return plan


def _crumbs_first(R, plan):
    def crumb(r):
        return r and not r.get("obj") and not R.starts_at(r, r)

    def on_way(x, r):
        for t in plan["take"]:
            if t["id"] != x and not t.get("crumb_of") and R.starts_at(R.Q(t["id"]), r):
                if x not in t["pre"]:
                    t["pre"].append(x)
                plan["closed_by"][x] = [t["id"]]
                return True
        return False

    take = []
    for t in plan["take"]:
        if not t["pre"] and crumb(R.Q(t["id"])) and on_way(t["id"], R.Q(t["id"])):
            t["crumb_of"] = True
        else:
            take.append(t)
    plan["take"] = take
    plan["carried"] = [x for x in plan["carried"] if not (crumb(R.Q(x)) and on_way(x, R.Q(x)))]
    return plan


def pickup_stops(R, plan, start) -> list[dict]:
    """stopsOf (trips.mjs pickupStops)."""
    stops: list[dict] = []
    by: dict[str, int] = {}

    def add(s):
        stops.append(s)
        by[s["key"]] = len(stops) - 1
        return s

    def depend(s, i):
        if i is None or by.get(s["key"]) == i or i in s["deps"]:
            return
        s["deps"].append(i)

    for t in plan["take"]:
        prev, deps = None, []
        for x in t["pre"]:
            k = f"q{x}"
            r = R.Q(x)
            if k not in by:
                st = R.giver(r, start)
                fin = R.ender(r, st[0] if st else start)
                add({"key": k, "quest": x, "prereq": True, "inP": st[0] if st else None,
                     "outP": fin[0] if fin else (st[0] if st else None), "deps": [], "obj_s": r.get("obj_s") or 0})
            s = stops[by[k]]
            for p in [*(r.get("pre") or []), *(r.get("any") or [])]:
                depend(s, by.get(f"q{p}"))
            if prev is not None and plan["closed_by"].get(stops[prev]["quest"]):
                depend(s, prev)
            prev = by[k]
            deps.append(k)
        deps.sort(key=_js_key)
        gk = f"g{t['giver'][1]}"
        k = f"{gk}<{','.join(deps)}" if deps else gk
        s = stops[by[k]] if k in by else add({"key": k, "inP": t["giver"][0], "outP": t["giver"][0],
                                              "deps": [by[d] for d in deps], "accepts": []})
        if t["id"] not in s["accepts"]:
            s["accepts"].append(t["id"])
    return stops


def handin_stops(R, quests: list[int], entrance) -> list[dict]:
    stops: list[dict] = []
    at: dict[str, int] = {}
    for x in quests:
        e = R.ender(R.Q(x), entrance)
        if not e:
            continue
        pt, key = e
        k = f"{key}@{_jsnum(pt[0])},{_jsnum(pt[1])},{_jsnum(pt[2])}"
        if k not in at:
            stops.append({"key": f"t{key}", "inP": pt, "outP": pt, "deps": [], "hands": []})
            at[k] = len(stops) - 1
        stops[at[k]]["hands"].append(x)
    return stops


def run_quests(R, page, min_level) -> list[int]:
    reach = max(R.p.get("level") or 1, min_level or 1)
    out = []
    for x in page:
        q = R.Q(x)
        if q and R.applies(q) and not R.done(x) and not R.in_log(x) and (q.get("item") or q.get("gi")) \
                and (q.get("min") or 1) <= reach and not q.get("money_only"):
            out.append(x)
    return out


def trip_seconds(R, plan, start, entrance, page, min_level, rules) -> dict:
    """tripSeconds (trips.mjs): pickup, travel to and back, hand-ins."""
    t = R.t
    talk = rules["talk_s"]
    ps = pickup_stops(R, plan, start)
    order, _ = plan_stops(ps, start, entrance, t, rules)
    pickup, pos = 0.0, start
    for i in order:
        s = ps[i]
        pickup += (t(pos, s["inP"]) or 0) + ((t(s["inP"], s["outP"]) or 0) + rules["step_s"] + s["obj_s"]
                                             if s.get("prereq") else talk * len(s["accepts"]))
        pos = s["outP"]
    to = (t(pos, entrance) or 0) if order else (t(start, entrance) or 0)
    handing = [*(x["id"] for x in plan["take"]), *plan["carried"], *run_quests(R, page, min_level)]
    hs = handin_stops(R, handing, entrance)
    horder, _ = plan_stops(hs, entrance, None, t, rules)
    back = handin = 0.0
    pos = entrance
    for k, i in enumerate(horder):
        s = hs[i]
        v = t(pos, s["inP"]) or 0
        if k == 0:
            back = v
        else:
            handin += v
        handin += talk * len(s["hands"])
        pos = s["outP"]
    return {"pickup_s": pickup, "travel_to_s": to, "travel_back_s": back, "handin_s": handin,
            "pickup": [ps[i]["key"] for i in order], "handin": [hs[i]["key"] for i in horder],
            "quests": list(dict.fromkeys(handing)),
            "prereqs": list(dict.fromkeys(p for x in plan["take"] for p in x["pre"]))}


def _jsround(v: float) -> int:
    """Math.round: halves up, as the exporter writes them."""
    return math.floor(v + 0.5)


def _jsnum(v) -> str:
    """A number as JavaScript prints it in a template string (12 not 12.0)."""
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def _js_key(s: str):
    """Array.prototype.sort's default order: UTF-16 code units, as Python
    compares these ASCII keys."""
    return s


# ---------------------------------------------------------------- the trip


def travel_for(trips: dict, side: str) -> Travel:
    """The side's network, built once per reference (kept on it, like
    quests_of's index)."""
    k = f"_travel_{side}"
    if k not in trips:
        trips[k] = Travel(trips, side)
    return trips[k]


def quests_of(trips: dict) -> dict[int, dict]:
    q = trips.get("_q")
    if q is None:
        q = trips["_q"] = {int(k): v for k, v in trips["quests"].items()}
    return q


def to_world(trips: dict, map_id, x, y) -> tuple | None:
    """A map point (uiMap, percent) in world yards, on the export's frames."""
    b = (trips.get("frames") or {}).get(str(map_id)) if map_id is not None else None
    if not b or x is None or y is None:
        return None
    c, left, right, top, bottom = b
    wx = round((top + (bottom - top) * (y / 100)) * 10) / 10
    wy = round((left + (right - left) * (x / 100)) * 10) / 10
    return (c, wx, wy)


def dungeon_of(trips: dict, instance_map) -> dict | None:
    return (trips.get("dungeons") or {}).get(str(instance_map)) if instance_map is not None else None


def trip(trips: dict, instance_map, *, side: str | None, level: int, race: str | None = None,
         cls: str | None = None, done: set | None = None, log: set | None = None, at=None,
         wing: str | None = None, skip: set | None = None) -> dict | None:
    """The trip for this character now: the quests and stops, and each
    part's seconds. `at`: his last known position (c, wx, wy); without it
    the trip starts at the entrance (`from`: "entrance"). `wing`: one wing's
    trip, its own quests only (the page's `wings`, trips.mjs). None when the
    reference has nothing for the dungeon, the wing or his side. `skip`:
    quests of the page the trip leaves out (a wing's "all" quest it doesn't
    finish, dungeonview.gain_of)."""
    d = dungeon_of(trips, instance_map)
    if not d or side not in SIDE_BIT:
        return None
    ent = tuple(d["entrance_A"] if side == "Alliance" and d.get("entrance_A") else d["entrance"])
    tr = travel_for(trips, side)
    cache: dict = {}

    def t(a, b):
        k = (a, b)
        if k not in cache:
            cache[k] = tr.cost(a, b)
        return cache[k]

    rules = trips["rules"]
    player = {"level": level, "done": set(done or ()), "log": set(log or ()), "rules": rules,
              "race_bit": race_bit(race, side), "class_bit": CLASS_BIT.get((cls or "").upper(), 0)}
    R = Rules(quests_of(trips), t, side, player)
    start = tuple(at) if at else ent
    page = d["page"]
    if wing is not None:
        w = (d.get("wings") or {}).get(wing)
        if w is None:
            return None
        page = w["page"]
    if skip:
        page = [q for q in page if q not in skip]
    plan = pickup_plan(R, page, d["min"], start, ent)
    s = trip_seconds(R, plan, start, ent, page, d["min"], rules)
    s["from"] = "you" if at else "entrance"
    s["breadcrumbs"] = plan["crumbs"]
    return s
