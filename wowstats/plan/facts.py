"""The planner's inputs: one character's state and history as plain JSON,
read from the stores the analysis reads, never from vm-wow.

`gather(env)` builds the analysis' prep database (wowstats.analysis.prep)
from the player's own game events, the knowledge base, the gear advice and
the player's own Auctionator scans, then `extract()` reads what the
estimator needs out of them. Both are deterministic: the same events give
the same inputs, which is what the tests replay (tests/plan/).

Nothing here comes from Battle.net: the Store is built without vm-wow, the
prices are the player's own scans (feed wow-prices). So a stored plan holds
no Battle.net data and needs no 30-day expiry.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import statistics
import time
from datetime import UTC, datetime

from wowstats import quests, trainers
from wowstats.analysis.prep import TRAVEL  # the route position's rule, one copy
from wowstats.gear import BADGE
from wowstats.stores import parse_t

LEVELING = re.compile(r"^forever/(alliance|horde)/")
TRAVEL_DEST = re.compile(r"^(?:fly|boat|tram|zeppelin|portal) to (.+)$", re.I)
CITIES = ("Stormwind", "Ironforge", "Darnassus", "Orgrimmar", "Thunder Bluff", "Undercity")
CITY_STEP_MAX_S = 180
# A step for one class only: "Druid training", "Warlock: Summon Felsteed".
CLASS_STEP = re.compile(r"^(Warrior|Paladin|Hunter|Rogue|Priest|Shaman|Mage|Warlock|Druid)"
                        r"(?: (?:training|trainer)\b|:)")


def other_class(title: str, cls: str | None) -> bool:
    m = CLASS_STEP.match(title or "")
    return bool(m and cls and m.group(1).upper() != cls.upper())
TRAINING = re.compile(r"(trainer|training)\b", re.I)
# Recent history the pace and the rates read: enough levels to be stable,
# recent enough to be the player as he plays now.
PACE_LEVELS = 5
RECENT_SESSIONS = 12


def _f(v, d=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return d
    return x if math.isfinite(x) else d


def _i(v, d=None):
    x = _f(v)
    return int(x) if x is not None else d


# ---------------------------------------------------------------- gather


def store_for(env: dict, like=None):
    """The stores, with vm-wow left out on purpose (no Battle.net data; the
    callers pass Battle.net's worn item ids, read live, to gather):
    from the settings, or the logs of an existing Store (`like`)."""
    from wowstats.eventstore import events_location
    from wowstats.stores import Store, env_account

    if like is not None:  # wowstats.stores.Store or the MCP's wowstats.mcp.store.Store
        return Store(like.vl_url, like.vl_archive_url, "", getattr(like, "stream", None) or like.logs_filter,
                     like.hot_days, profile_vm_url="", account_id=getattr(like, "account_id", 0))
    return Store(events_location(env), env.get("VL_ARCHIVE_URL", ""), "",
                 env.get("LOGS_FILTER", "feed:wow-*"), int(env.get("VL_ARCHIVE_AFTER_DAYS", "89")), profile_vm_url="",
                 account_id=env_account(env))


def last_seen(store) -> str:
    rows = store.vl(f"{store.stream} char:* | stats by (char) max(_time) as last_seen")
    rows = [r for r in rows if r.get("char")]
    if not rows:
        raise LookupError("no character has any events yet")
    return max(rows, key=lambda r: r.get("last_seen") or "")["char"].replace(" ", "-")


def prices(store, days: int = 30) -> dict[int, dict]:
    """The player's own Auctionator scans: per item, the newest day's lowest
    buyout (copper), and the day."""
    from wowstats import storemodels
    from wowstats.stores import dedupe

    rows = store.vl(f'{store.stream} feed:="wow-prices" _time:{days}d | fields _time, item_id, item_name, '
                    f"price_copper, rev, source_id")
    got = storemodels.prices([r for r in dedupe(rows) if _i(r.get("item_id")) is not None])
    return {_i(d["item_id"]): {"t": d["price_latest_t"], "copper": _i(d["price_latest_copper"]),
                               "name": d["price_item"]} for d in got.values()}


def gather(env: dict, char: str | None = None, now: float | None = None, store=None, battlenet=None,
           gear_inputs=None) -> dict:
    """Read the stores and the KB; return extract()'s inputs. `battlenet`:
    the item ids Battle.net says the character wears now, read live by the
    caller (this store never reads vm-wow): they add the quests their
    rewards prove (wowstats.quests.with_battlenet) to the advice, the
    quests done and the open quests, never to the analysis' database.
    `gear_inputs`: the doll's (wowstats.views.Views.gear_inputs), when the
    caller has them: the advice and the dungeons' loot are scored from
    exactly what the doll is, so each slot has one BiS share everywhere
    (owner, 2026-11-21). Without them, the stored events alone."""
    from wowstats import analysis
    from wowstats.analysis import kb, prep
    from wowstats.mcp import route

    from . import store as plan_store

    store = store_for(env, store)
    char = prep.norm(char or env.get("DEFAULT_CHAR", "") or last_seen(store))
    rows = prep.query(store, store.stream, char)
    if not rows:
        raise LookupError(f"no events for {char}")
    knowledge = kb.shared(env)
    advice = None
    from wowstats import gear

    loot: dict = {}
    gi = gear_inputs if gear_inputs and gear_inputs.get("available") else None
    with contextlib.suppress(gear.GearError):
        if gi is not None:
            from wowstats.views import gear_args

            advice = gear.advise(gear.reference(), gi["sheet"], every=True, **gear_args(gi))
            loot = gear.instance_loot(gear.reference(), gi["sheet"], level=gi["level"], weights=gi["weights"],
                                      bis=advice.get("bis_by_slot"))
        else:
            advice = gear.advise_rows(rows, analysis.weights_for(env, char), battlenet=battlenet)
            loot = gear.instance_loot_rows(rows, analysis.weights_for(env, char),
                                           (advice or {}).get("bis_by_slot"))
    db = prep.build(rows, char, route.reference(), map_sizes=knowledge.meta.get("ui_map"),
                    advice=advice)
    try:
        scans = prices(store)
    except Exception:  # noqa: BLE001 - the scans are a bonus, never a reason to fail
        scans = {}
    inp = extract(db, knowledge, route.reference(), advice=advice, scans=scans,
                  state=analysis.load_state(env, char), now=now, loot=loot, bags=gear.newest_bags(rows),
                  done=quests.completed(rows, battlenet=battlenet) if battlenet else None)
    inp["who"]["char"] = inp["who"].get("char") or char
    # Polestar's own record of the last class trainer visit (classTrainVisit,
    # the level it was at), from the shipper's sheet: exact where the
    # Recorder's trainer windows can't tell a class trainer from a weapon master.
    visit = class_train_level(rows)
    if visit is not None:
        inp["trainer"] = {**inp["trainer"], "last_level": visit, "from": "Polestar's classTrainVisit"}
    inp["recorder"] = recorder_state(store, rows)
    inp["calibration"] = plan_store.calibration(env, char, inp["progress"])
    inp["plan_reviews"] = plan_store.reviews(env, char, inp["progress"])[-3:]
    return inp


def recorder_state(store, rows: list[dict]) -> str | None:
    """'on' when the Recorder's events arrive; else what the shipper's setup
    report says ('off': installed, switch not on; 'missing': not installed);
    None when there is no report (an older shipper)."""
    from wowstats import missing

    if any(r.get("feed") == "wow-recorder" for r in rows):
        return "on"
    try:
        rep = missing.setup(lambda q: store.vl(f"{store.stream} {q}"))
    except Exception:  # noqa: BLE001 - the report only words the advice; the plan stands without it
        return None
    if rep is None:
        return None
    if "recorder" not in (rep.get("addons") or {}):
        return "missing"
    # Present means its events arrived: switched on but silent is "waiting".
    return "waiting" if rep.get("recorder_on") else "off"


# ---------------------------------------------------------------- extract


def header(db) -> dict:
    """The prep's meta table: the build's own header (character, class,
    race, faction, the data's range), the rows the semantic layer's
    `character` and `class` columns read too. Not a measurement: plumbing
    (tests/test_semantic_guard.py)."""
    return dict(db.execute("SELECT key, value FROM meta").fetchall())


# ---------------------------------------------------------------- the semantic layer

# The pace the estimator reads (each level's played time, XP and route time,
# the recent sessions' online, AFK and XP) is the semantic layer's metrics,
# on a DuckDB copy of the prep (owner, 2026-11-22: "use the semantic model
# all the time"). The levels are kept for the last prep asked: played_between
# reads them once per snapshot.
_LEVELS: list = [None, None]


def rows_of(layer, ro, **q) -> list[dict]:
    """One query_metric question's rows as dicts, never cut unless it asks for a limit."""
    from wowstats.analysis import semantic

    got = semantic.run(layer, ro, {"limit": semantic.MAX_LIMIT, **q})
    if got["more"] and "limit" not in q:  # a limit asked for cuts on purpose
        raise RuntimeError(f"more than {semantic.MAX_LIMIT} rows for {q.get('group_by')}")
    return [dict(zip(got["columns"], r, strict=True)) for r in got["rows"]]


def level_rows(db, layer=None, ro=None) -> list[dict]:
    """Every level of the prep, oldest first, with or without a split: played_s,
    level_xp (as xp), class_planned_s (as planned_s: the class's route time
    only, the estimator falls back to the route itself) and partial."""
    if _LEVELS[0] is db:
        return _LEVELS[1]
    if layer is None:
        layer, ro = semantic_of(db)
    # planned_s is the Recorder's route time here (estimate's level_factor
    # says which clock it used): a character with no Recorder session gets
    # the route reference's, by level, side and class, from the estimate.
    rec_ran = rows_of(layer, ro, metrics=["session_records"])[0]["session_records"]
    out = [{"level": r["level"], "played_s": r["played_s"], "xp": r["level_xp"],
            "planned_s": r["class_planned_s"] if rec_ran else None, "partial": r["partial"]}
           for r in rows_of(layer, ro, metrics=["played_s", "level_xp", "class_planned_s"],
                            group_by=["level", "partial"], where=[{"dimension": "level", "op": "is not null"}],
                            order_by=["level"])]
    _LEVELS[:] = [db, out]
    return out


def recent_sessions(layer, ro, n: int = RECENT_SESSIONS) -> list[dict]:
    """The `n` newest Recorder sessions, newest first: their online and AFK
    time (timeline) and XP (sessions), the levels they began and ended at."""
    rows = rows_of(layer, ro, metrics=["session_xp"], group_by=["session_id", "start_t", "level_start", "level_end"],
               where=[{"dimension": "session_id", "op": "is not null"}], order_by=["-start_t"], limit=n)
    ids = [r["session_id"] for r in rows]
    tl = {r["session_id"]: r for r in rows_of(layer, ro, metrics=["online_s", "afk_s"], group_by=["session_id"], where=[
        {"dimension": "session_id", "op": "in", "values": ids}])} if ids else {}
    return [{"session_id": r["session_id"], "start_t": r["start_t"],
             "online_s": (tl.get(r["session_id"]) or {}).get("online_s") or 0,
             "afk_s": (tl.get(r["session_id"]) or {}).get("afk_s") or 0, "xp_gained": r["session_xp"],
             "level_start": r["level_start"], "level_end": r["level_end"]} for r in rows]


def step_kind(guide: str, title: str | None, prev_dest: str | None, planned_s: float | None) -> str:
    """questing, travel, town or dungeon, from the step's place in the guide
    and its title (the route has no other label): the lookup guides are
    dungeon trips, a 'Fly to'/'Boat to' title is travel, a title that names
    a hub some guide travels to (or a trainer) is town, and so are the short
    steps after arriving in a capital city."""
    title = title or ""
    if guide.startswith("lookup/"):
        return "dungeon"
    if TRAVEL.match(title):
        return "travel"
    if TRAINING.search(title) or title in hubs():
        return "town"
    if prev_dest and any(c in prev_dest for c in CITIES) and (planned_s or 0) <= CITY_STEP_MAX_S:
        return "town"
    return "questing"


_HUBS: set[str] | None = None


def hubs() -> set[str]:
    """Every place some guide's 'Fly to X', 'Boat to X' step goes to, plus the
    capitals: the towns, from the route's own titles."""
    global _HUBS
    if _HUBS is None:
        from wowstats.mcp import route

        ref = route.reference()
        out = set(CITIES)
        for g in (ref.guides or {}).values():
            for st in g.get("steps") or []:
                m = TRAVEL_DEST.match(str(st[2] if len(st) > 2 else ""))
                if m:
                    out.add(m.group(1).strip())
        _HUBS = out
    return _HUBS


def guide_steps(ref, guide: str) -> list[tuple[int, float, str]]:
    g = (ref.guides or {}).get(guide) or {}
    return [(int(s[0]), float(s[1] or 0), str(s[2] if len(s) > 2 else "")) for s in g.get("steps") or []]


def kinds_for(guide: str, steps: list[tuple[int, float, str]]) -> dict[int, str]:
    out, dest = {}, None
    for n, planned, title in steps:
        k = step_kind(guide, title, dest, planned)
        out[n] = k
        m = TRAVEL_DEST.match(title)
        if m:
            dest = m.group(1)
        elif k == "travel" or k == "questing" and planned > CITY_STEP_MAX_S:
            dest = None
    return out


def guide_sequence(ref, side: str, level: int) -> list[str]:
    """The side's levelling guides from the one the route is in at `level`,
    in the route's order (the guide each level ends in, then any guide of
    the same bracket the level map skips, by its start level)."""
    levels = (ref.levels or {}).get(side) or {}
    seq: list[str] = []
    for lv in sorted(int(k) for k in levels):
        g = (levels.get(str(lv)) or {}).get("guide")
        if g and g not in seq:
            seq.append(g)
    return [g for g in seq if (_i(((ref.guides or {}).get(g) or {}).get("level_end"), 0) or 0) >= level - 1]


# The position model's metrics (semantic.yaml `position`) under the names
# the planner reads them by.
POSITION = {"route_guide": "guide", "route_guide_title": "guide_title", "route_shown_step": "shown_step",
            "route_shown_title": "shown_title", "route_reason": "reason", "route_furthest_step": "furthest_step",
            "route_next_step": "anchor_step", "route_next_title": "anchor_title", "route_next_why": "anchor_why",
            "route_subzone": "subzone", "route_zone": "zone"}


def position(db, sem=None) -> dict:
    """Where the player is in Polestar's route: the prep's route_position
    (wowstats.analysis.prep._route_position) through the layer's `position`
    model, the row play_sql reads too. {} before the first step record."""
    row = ask(semantic_of(db, sem), metrics=list(POSITION))[0]
    if row["route_guide"] is None:
        return {}
    return {mine: row[theirs] for theirs, mine in POSITION.items()}


def last_position(db, sem=None) -> dict | None:
    """Where the character was last seen on a map (the newest record with a
    position, the layer's map_now/x_now/y_now): a dungeon trip starts there
    (wowstats.plan.tripcost). None before any positioned record."""
    row = ask(semantic_of(db, sem), metrics=["map_now", "x_now", "y_now"])[0]
    if row.get("map_now") is None or row.get("x_now") is None:
        return None
    return {"map_id": int(row["map_now"]), "x": round(float(row["x_now"]), 1), "y": round(float(row["y_now"]), 1)}


def semantic_of(db, sem=None) -> tuple:
    """(layer, read-only runner) of the semantic layer over this prep build:
    `sem` when the caller has one, else a copy of `db` of its own
    (wowstats.analysis.semantic.local). The plan reads its history through
    the layer, never its own SQL (owner, 2026-11-22): extract makes the copy
    once and hands it to each reader."""
    if sem is not None:
        return sem
    from wowstats.analysis import semantic

    return semantic.local(db)


def ask(sem, **qy) -> list[dict]:
    """Every row of one semantic-layer question (wowstats.analysis.semantic.rows)."""
    from wowstats.analysis import semantic

    return semantic.rows(*sem, qy)


def step_history(db, ref, sem=None) -> list[dict]:
    """Every levelling-guide step he spent time on: online time without AFK
    (active_s) and the XP the bar gained (xp_gained) per guide and step,
    against the route's planned seconds, with the step's kind; in the order
    the steps first came up (first_online_t)."""
    rows = ask(semantic_of(db, sem), metrics=["active_s", "first_online_t", "xp_gained"], group_by=["guide", "step"],
               where=[{"dimension": "guide", "op": "is not null"}, {"dimension": "step", "op": "is not null"}])
    kinds: dict[str, dict[int, str]] = {}
    planned: dict[str, dict[int, tuple]] = {}
    out = []
    for r in sorted(rows, key=lambda r: r["first_online_t"]):
        g, st = r["guide"], int(r["step"])
        if not (LEVELING.match(g) or g.startswith("lookup/")):
            continue
        if g not in kinds:
            steps = guide_steps(ref, g)
            kinds[g] = kinds_for(g, steps)
            planned[g] = {n: (p, t) for n, p, t in steps}
        p, title = planned[g].get(st, (None, None))
        out.append({"guide": g, "step": st, "title": title, "kind": kinds[g].get(st, "dungeon" if
                    g.startswith("lookup/") else "questing"), "planned_s": p,
                    "actual_s": round(r["active_s"] or 0, 1), "xp": int(r["xp_gained"] or 0),
                    "t": r["first_online_t"]})
    return out


def open_quests(db, level: int, sem=None) -> list[dict]:
    """The quests still in his log: the quests model's `open` rows (accepted,
    or its objectives seen done, and neither handed in, abandoned, failed
    nor closed as done (inferred) by the prep: wowstats.analysis.prep._quests,
    handed in with the Recorder off, its reward worn or Questie's list
    saying so), so nothing tells him to go back for a quest he finished. In
    the order of their last news: objectives done, else the accept."""
    from wowstats import forever

    sem = semantic_of(db, sem)
    rows = ask(sem, metrics=["quest_accepted_t", "quest_complete_t"],
               group_by=["quest_id", "quest", "zone", "level_accepted"],
               where=[{"dimension": "status", "op": "=", "value": "open"}])
    rows.sort(key=lambda r: (r["quest_complete_t"] or r["quest_accepted_t"] or 0, r["quest_id"]))
    out = []
    for r in rows:
        fq = forever.quest(r["quest_id"]) or {}
        timed = r["quest_accepted_t"] is not None
        out.append({"quest_id": int(r["quest_id"]), "name": fq.get("name") or r["quest"],
                    "level": fq.get("level"), "min_level": fq.get("min_level"),
                    "zone": fq.get("zone") or (r["zone"] if timed else None),
                    "accepted_zone": r["zone"] if timed else None, "accepted_level": r["level_accepted"],
                    "accepted_t": r["quest_accepted_t"], "complete": r["quest_complete_t"] is not None})
    zones = sorted({o["zone"] for o in out if o["zone"]})
    seen = {z["zone"]: z["last_record_t"] for z in ask(
        sem, metrics=["last_record_t"], group_by=["zone"],
        where=[{"dimension": "zone", "op": "in", "values": zones}])} if zones else {}
    for o in out:
        o["last_in_zone_t"] = seen.get(o["zone"])
    return out


def quest_facts(knowledge, qid: int) -> dict:
    """XP and the 'group' hints of a quest from the KB's forever-db document."""
    d = (knowledge.get([f"quest:{qid}"]) or [{}])[0] if knowledge is not None else {}
    body = d.get("body") or ""
    xp = re.search(r"XP (\d+)", body)
    return {"xp": int(xp.group(1)) if xp else None,
            "elite": bool(re.search(r"\belite\b|a group is needed|group quest", body, re.I)),
            "dungeon": bool(re.search(r"dungeon quest|in a dungeon", body, re.I)),
            "starts": (re.search(r"Starts: NPC \d+ ([^.;]+)", body) or [None, None])[1],
            "ends": (re.search(r"Ends: (?:NPC|object) \d+ ([^.;]+)", body) or [None, None])[1]}


def xp_share(level: int, quest_level: int | None, rule: dict | None) -> float | None:
    """The share of a quest's XP a hand-in pays at this level, by the KB's
    rule quest_xp.share_by_levels_over; None when the KB has no such rule."""
    if rule is None:
        return None
    if quest_level is None:
        return 1.0
    v = rule["value"]
    over = level - quest_level
    if over <= v["full_up_to"]:
        return 1.0
    return float(v.get(str(over), v["8_and_over"]))


def mount_facts(knowledge) -> dict:
    """The riding tiers from the KB's static rules (riding.apprentice,
    riding.journeyman, riding.vendor_mounts, riding.city_discount), each
    with its source. A tier without a rule is unknown: left out."""
    rule = knowledge.rule if knowledge is not None else (lambda _n: None)
    tiers = []
    for name in ("riding.apprentice", "riding.journeyman"):
        r = rule(name)
        if not r:
            continue
        v = r["value"]
        vendor = (rule("riding.vendor_mounts") or {}).get("value") or {}
        tiers.append({"name": name, "level": v["level"], "skill": v.get("skill"),
                      "training_copper": v["training_copper"], "comes_with_mount": bool(v.get("comes_with_mount")),
                      "vendor_mount_copper": vendor.get(str(v["level"])), "source": cite(r)})
    disc = rule("riding.city_discount")
    return {"tiers": tiers, "discount": {"pct": disc["value"]["pct"], "reputation": disc["value"].get("reputation"),
                                         "source": cite(disc)} if disc else None,
            "unknown": [] if len(tiers) == 2 else ["a riding tier: the KB has no sourced rule for it"]}


def cite(r: dict) -> dict:
    return {k: r.get(k) for k in ("name", "source_url", "source_type", "date", "confidence", "scope")}


def metric_row(sem: tuple, q: dict) -> dict:
    """The one row of a query_metric question without group_by (or {})."""
    from wowstats.analysis import semantic

    got = semantic.run(*sem, q)
    return dict(zip(got["columns"], got["rows"][0], strict=True)) if got["rows"] else {}


def since_local(t: float) -> str:
    """A Unix time as query_metric's `since`: the local time to the second,
    in the zone the prep's local times are in."""
    from zoneinfo import ZoneInfo

    from wowstats.analysis import prep

    return datetime.fromtimestamp(t, ZoneInfo(prep.TZ_NAME)).strftime("%Y-%m-%dT%H:%M:%S")


def riding_known(db, sem: tuple | None = None) -> int:
    """The riding skill the records saw (0: none): the client's Riding skill
    line (kb.RIDING), as the shipper reports skill ranks (skill_rank)."""
    from wowstats.analysis.kb import RIDING

    row = metric_row(sem or semantic_of(db), {"metrics": ["skill_rank"],
                                              "where": [{"dimension": "skill_id", "op": "=", "value": int(RIDING)}]})
    return _i(row.get("skill_rank"), 0) or 0


def rested_burn(db, sem: tuple | None = None) -> dict:
    """How much rested XP his kills use, measured: over the 5-minute XP
    records where the rested pool went down, rested XP used against the kill
    XP recorded in the same intervals (rested_per_kill_xp: intervals with a
    kill inside a dungeon trip left out of both sides). No rule assumed."""
    row = metric_row(sem or semantic_of(db), {"metrics": ["rested_intervals", "rested_used_xp", "rested_kill_xp",
                                                          "rested_per_kill_xp"]})
    burn = row.get("rested_per_kill_xp")
    return {"intervals": row.get("rested_intervals") or 0, "rested_used": row.get("rested_used_xp") or 0,
            "kill_xp": row.get("rested_kill_xp") or 0, "per_kill_xp": round(burn, 2) if burn is not None else None}


def upgrade_key(u: dict) -> tuple:
    """An upgrade's row key in the `upgrades` model: the item, the inventory
    slot its gain is measured in (one ring or trinket of two) and, for a
    one-hander, the other hand it is scored with."""
    return u.get("item_id"), u.get("slot"), _i((u.get("with") or {}).get("item_id"))


def upgrade_facts(sem: tuple) -> dict[tuple, dict]:
    """Per upgrade_key, the `upgrades` model's numbers: gain, share of the
    gear and the two BiS percents (delta_pct, their difference)."""
    from wowstats.analysis import semantic

    got = semantic.run(*sem, {"metrics": ["upgrade_gain", "upgrade_gain_share_pct", "upgrade_bis_pct",
                                          "upgrade_bis_pct_worn"], "group_by": ["item_id", "slot_id", "with_item_id"]},
                       semantic.EVERY_ROW)
    out = {}
    for iid, slot, with_id, gain, share, pct, worn in got["rows"]:
        pct, worn = (None if v is None else int(v) for v in (pct, worn))
        out[(iid, slot, with_id)] = {"gain": gain, "gain_share_pct": share, "bis_pct": pct, "bis_pct_worn": worn,
                                     "delta_pct": None if pct is None else pct - (worn or 0)}
    return out


def upgrades(sem: tuple, advice_ups: list[dict]) -> list[dict]:
    """The gear advice's upgrades (the build's upgrade_candidates came from
    the same advice) with their numbers from the `upgrades` model
    (upgrade_facts). An upgrade with no way to get it (no source, so no row)
    is left out: nothing to plan."""
    facts = upgrade_facts(sem)
    return [{**u, **facts[k]} for u in advice_ups if (k := upgrade_key(u)) in facts]


def route_levels(ref, meta: dict, level: int) -> dict:
    """The route's seconds per level for this side and class, from here to
    the route's last level."""
    side = meta.get("faction")
    cls = str(meta.get("class") or "").upper()
    out = {}
    if not side:
        return out
    levels = (ref.levels or {}).get(side) or {}
    for lv in sorted(int(k) for k in levels if int(k) >= max(1, level)):
        row = levels.get(str(lv)) or {}
        s = (row.get("by_class") or {}).get(cls) or row.get("seconds")
        if s:
            out[str(lv)] = s
    return out


def series(sem, source: str) -> list[dict]:
    """The `progress` model's series (semantic.yaml), one row per reading in
    time order: `xp` (the Recorder's XP bar) or `snapshot` (the sheets)."""
    return ask(sem, metrics=["progress_t", "progress_level", "progress_xp", "progress_online_s"],
               group_by=["reading", "level"], where=[{"dimension": "source", "op": "=", "value": source}],
               order_by=["reading"])


def progress(db, sem=None) -> list[dict]:
    """Every XP record as (t, level with the fraction, online seconds since
    the first record): what a plan is compared with. The semantic layer's
    `progress` model at the reading grain."""
    sem = semantic_of(db, sem)
    out = [{"t": r["progress_t"], "level": round(r["progress_level"], 3), "online_s": round(r["progress_online_s"])}
           for r in series(sem, "xp")]
    if out:
        return out
    # No Recorder: the logout snapshots, the played time between them from
    # the level splits (played_between), so a plan by level is reviewed too.
    snaps = series(sem, "snapshot")
    for r in snaps:
        on = played_between(db, (snaps[0]["level"], snaps[0]["progress_xp"]), (r["level"], r["progress_xp"])) or 0.0
        if out and on < out[-1]["online_s"]:
            continue  # a snapshot that went back (another source's older sheet): skip it
        out.append({"t": r["progress_t"], "level": round(r["progress_level"], 3), "online_s": round(on),
                    "estimated": True})
    return out


def gold_rate(db, days: float = 14, now: float | None = None, sem: tuple | None = None) -> dict:
    """Net gold per online hour between logout snapshots in the window: the
    moves between consecutive snapshots (gold_moves) summed, over the online
    time between them (everything earned less everything spent). The rate is
    gold_per_online_hour: the moves the Recorder saw time for, on both sides."""
    sem = sem or semantic_of(db)
    since = since_local((now or time.time()) - days * 86400)
    snap = metric_row(sem, {"metrics": ["snapshot_count", "snapshot_first_level", "snapshot_first_xp",
                                        "snapshot_last_level", "snapshot_last_xp"], "since": since})
    n = snap.get("snapshot_count") or 0
    if n < 2:
        return {"n": n, "copper_per_hour": None}
    g = metric_row(sem, {"metrics": ["gold_moved_copper", "gold_move_online_s", "gold_changes", "gold_per_online_hour",
                                     "gold_first_t", "gold_last_t"], "since": since})
    online = g.get("gold_move_online_s") or 0
    net = g.get("gold_moved_copper") or 0
    rate = g.get("gold_per_online_hour")
    estimated = False
    if online <= 0:
        # No Recorder timeline: the played time between the two snapshots
        # from Polestar's level splits (seconds per XP of each level crossed).
        online = played_between(db, (snap["snapshot_first_level"], snap["snapshot_first_xp"]),
                                (snap["snapshot_last_level"], snap["snapshot_last_xp"])) or 0
        estimated = online > 0
        rate = net / online * 3600 if online > 0 else None
    return {"n": n, "changes": g.get("gold_changes") or 0, "net_copper": net, "online_s": round(online),
            "copper_per_hour": round(rate) if rate is not None else None,
            "from_t": g.get("gold_first_t"), "to_t": g.get("gold_last_t"), **({"estimated": True} if estimated else {})}


def played_between(db, a: tuple, b: tuple) -> float | None:
    """wowstats.journal.played_between over the prep's levels (the layer's
    played_s and level_xp: level_rows)."""
    from wowstats import journal

    rows = [{"level": lv["level"], "seconds": lv["played_s"], "xp": lv["xp"], "partial": bool(lv["partial"])}
            for lv in level_rows(db)]
    return journal.played_between(rows, a, b)


def ask_on(db, sem=None):
    """query_metric's compiler and runner on a DuckDB copy of the prep build
    `db` (wowstats.analysis.semantic.local), or the copy `sem` already made:
    `ask(**query) -> rows as dicts`. extract() makes one copy and hands it on."""
    import warnings

    from wowstats.analysis import semantic

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # BSL's own deprecation notes
        layer, ro = semantic_of(db, sem)

    def ask(**q) -> list[dict]:
        got = semantic.run(layer, ro, {"limit": semantic.MAX_LIMIT, **q})
        if got["more"]:  # never a cut list: a part would read as the whole
            raise RuntimeError(f"more than {semantic.MAX_LIMIT} rows for {q.get('group_by')}")
        return [dict(zip(got["columns"], r, strict=True)) for r in got["rows"]]

    return ask


# The trip's numbers from the semantic layer (per_trip's columns), under the
# names the planner reads them by.
TRIP_METRICS = {"trip_s": "online_s", "trip_pickup_s": "pickup_s", "trip_travel_to_s": "travel_to_s",
                "trip_inside_s": "inside_s", "trip_travel_back_s": "travel_back_s", "trip_handin_s": "handin_s",
                "trip_xp_inside": "xp_inside", "trip_xp_quests": "xp_quests",
                "trip_quests_handed_in": "quests_handed_in", "trip_quest_money_copper": "quest_money_copper"}
TRIP_DIMS = {"trip": "trip_id", "dungeon": "dungeon", "level": "level_start", "trip_start": "start_t",
             "trip_end": "end_t", "trip_loot": "loot_inside"}
RUNS_PER_ASK = 10  # runs per kills question: their rows (one per creature) stay under query_metric's cap


def trips(ask) -> list[dict]:
    """Each dungeon trip with its run's kills (the semantic layer: the
    dungeon_trips metrics by trip, the trip's first run by dungeon_runs, the
    kills inside that run by creature level and elite), oldest first."""
    names = list(TRIP_METRICS)
    rows = {r["trip"]: r for r in ask(metrics=names[:8], group_by=list(TRIP_DIMS))}
    for r in ask(metrics=names[8:], group_by=["trip"]):
        rows.get(r["trip"], {}).update(r)
    # The trip's run: the first of its dungeon starting inside it.
    first: dict = {}
    for r in ask(metrics=["run_count"], group_by=["trip", "run"],
                 where=[{"dimension": "trip", "op": "is not null"}]):
        first[r["trip"]] = min(first.get(r["trip"], r["run"]), r["run"])
    kills: dict = {run: [] for run in first.values()}
    runs = sorted(kills)
    for i in range(0, len(runs), RUNS_PER_ASK):
        for r in ask(metrics=["kills"], group_by=["run", "npc_id", "npc_level", "elite"],
                     where=[{"dimension": "kind", "op": "=", "value": "kill"},
                            {"dimension": "run", "op": "in", "values": runs[i:i + RUNS_PER_ASK]}]):
            kills[r["run"]].append(r)
    out = []
    for r in sorted(rows.values(), key=lambda r: (r["trip_start"], r["trip"])):
        d = {mine: r.get(theirs) for theirs, mine in TRIP_DIMS.items()}
        d.update({mine: r.get(theirs) for theirs, mine in TRIP_METRICS.items()})
        with contextlib.suppress(TypeError, ValueError):
            d["loot_inside"] = json.loads(d["loot_inside"] or "[]")
        run = first.get(r["trip"])
        if run is not None:
            ks = kills[run]
            d["kills_inside"] = sum(int(k["kills"]) for k in ks)
            # Each kill's creature level, elites flagged (classification e,
            # R or b): what carries the run's kill XP to another character
            # level (wowstats.plan.dungeongain).
            d["kill_levels"] = [[int(k["npc_level"]), int(k["elite"])] for k in ks if k["npc_level"] is not None
                                for _ in range(int(k["kills"]))]
            # Each creature killed, by id: which wing of a dungeon run in
            # wings the run stayed in (wowstats.plan.wings.run_wing).
            npcs: dict[str, int] = {}
            for k in ks:
                if k.get("npc_id") is not None:
                    npcs[str(int(k["npc_id"]))] = npcs.get(str(int(k["npc_id"])), 0) + int(k["kills"])
            d["kill_npcs"] = dict(sorted(npcs.items(), key=lambda kv: int(kv[0])))
        out.append({k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()})
    return out


# Done at a trip's start: a hand-in the Recorder saw before it. A quest only
# deduced done (the addons' lists, a reward, Battle.net, a later quest) was
# handed in while the Recorder wasn't looking, so before any trip it saw.
SEEN_DONE = ("turnin", "flagged")


def trip_pace(sem, trips: list[dict], who: dict) -> None:
    """Each trip's `route_s`: the pickups, travel and hand-ins Polestar's
    rules plan for that same trip (dungeonview.trip_at), replayed on the
    character as it stood at the trip's start: its level, the quests done
    and in the log then, where it was last seen before. None when the rules
    have no data for the dungeon. Measured over planned is his pace
    (dungeonview.pace), which the next trips' legs are scaled by."""
    from . import wings as W
    from .dungeonview import trip_at

    if not trips:
        return
    seen = {}
    for r in ask(sem, metrics=["done_seen_t"], group_by=["quest_id", "done_source"]):
        if r["quest_id"] is None:
            continue
        t = r["done_seen_t"] if r["done_source"] in SEEN_DONE else float("-inf")
        seen[int(r["quest_id"])] = min(seen.get(int(r["quest_id"]), math.inf), t if t is not None else math.inf)
    held = [(int(r["quest_id"]), r["quest_accepted_t"], r["quest_closed_t"])
            for r in ask(sem, metrics=["quest_accepted_t", "quest_closed_t"], group_by=["quest_id", "status"])
            if r["quest_id"] is not None]
    for t in trips:
        t0 = t.get("start_t")
        if t0 is None:
            t["route_s"] = None
            continue
        done = {q for q, at in seen.items() if at < t0}
        log = {q for q, a, c in held if (a is None or a < t0) and (c is None or c >= t0) and q not in done}
        where = ask(sem, metrics=["map_now", "x_now", "y_now"], until=since_local(t0))[0]
        at = {"map_id": int(where["map_now"]), "x": float(where["x_now"]), "y": float(where["y_now"])} \
            if where.get("map_now") is not None and where.get("x_now") is not None else None
        plan = trip_at(W.trip_name(t), who, t.get("level_start") or 1, done, log, at)
        t["route_s"] = round(plan["pickup_s"] + plan["travel_to_s"] + plan["travel_back_s"] + plan["handin_s"], 1) \
            if plan and at else None


def loot_value(db, cards, scans: dict, days: float = 14, now: float | None = None, sem: tuple | None = None) -> dict:
    """What the questing loot was worth per online hour over the last days,
    outside dungeons: at a vendor (the client's sell price) and, for what he
    scanned, at his own Auctionator prices. The items (looted_items outside
    a dungeon trip) and the time (online_s outside a dungeon) are metrics."""
    from wowstats.analysis import semantic

    sem = sem or semantic_of(db)
    since = since_local((now or time.time()) - days * 86400)
    online = metric_row(sem, {"metrics": ["online_s"], "since": since,
                              "where": [{"dimension": "dungeon", "op": "is null"}]}).get("online_s") or 0
    got = semantic.run(*sem, {"metrics": ["looted_items"], "group_by": ["item_id"], "since": since,
                              "where": [{"dimension": "trip_id", "op": "is null"},
                                        {"dimension": "item_id", "op": "is not null"}]}, semantic.EVERY_ROW)
    rows = [(r[0], r[1]) for r in got["rows"]]
    vendor = ah = 0
    items = []
    for iid, n in rows:
        c = (cards(iid) or {}) if cards else {}
        sell = c.get("sell") or 0
        price = (scans.get(iid) or {}).get("copper")
        vendor += sell * n
        if price and c.get("b") != "Binds when picked up":
            ah += max(price, sell) * n
            items.append({"item_id": iid, "name": c.get("n") or (scans.get(iid) or {}).get("name"), "count": n,
                          "ah_copper": price, "vendor_copper": sell})
    items.sort(key=lambda x: -(x["ah_copper"] or 0) * x["count"])
    hours = online / 3600
    return {"online_s": round(online), "vendor_per_hour": round(vendor / hours) if hours else None,
            "ah_items": items[:8], "ah_per_hour": round(ah / hours) if hours and ah else None}


def run_loot(trip: dict, cards, scans: dict) -> dict:
    """One dungeon run's loot inside, valued: vendor price of everything,
    and the binds-on-equip greens and blues and the tradeables at his own
    scan prices where he has them."""
    vendor = ah = 0
    boe = []
    n = 0
    for iid, cnt in trip.get("loot_inside") or []:
        c = (cards(iid) or {}) if cards else {}
        n += cnt
        vendor += (c.get("sell") or 0) * cnt
        price = (scans.get(iid) or {}).get("copper")
        if c.get("q", 0) >= 2 and c.get("b") == "Binds when equipped":
            boe.append({"item_id": iid, "name": c.get("n"), "quality": c.get("q"), "ah_copper": price,
                        "vendor_copper": c.get("sell")})
        if price and c.get("b") != "Binds when picked up":
            ah += price * cnt
    return {"items": n, "vendor_copper": vendor, "boe": boe, "ah_copper": ah or None}


def card_lookup():
    """The item cards (Forever's client tables), when they can be had."""
    try:
        from wowstats import itemcard

        cat = itemcard.shared()
        return cat.card
    except Exception:  # noqa: BLE001
        return None


def owned(db, worn_ids: list[int], bags: dict | None, sem=None) -> dict:
    """What the character has, by item ID: worn (the newest sheet), in the
    bags or the bank (the newest wow-bags), looted or got as a quest reward
    (the Recorder's records, with the first time). `inventory_known` says
    whether bags and bank were reported at all: without them, a looted item
    may be worn, held or long sold."""
    sem = semantic_of(db, sem)
    known = [{"dimension": "item_id", "op": "is not null"}]
    where: dict[int, dict] = {}
    for r in ask(sem, metrics=["first_looted_t"], group_by=["item_id"], where=known, order_by=["item_id"]):
        where[int(r["item_id"])] = {"where": "looted", "t": r["first_looted_t"]}
    for r in ask(sem, metrics=["first_record_t"], group_by=["item_id"], order_by=["item_id"],
                 where=[*known, {"dimension": "kind", "op": "=", "value": "turnin"}]):
        where.setdefault(int(r["item_id"]), {"where": "reward", "t": r["first_record_t"]})
    for k in ("bank", "bags"):
        for iid in (bags or {}).get(k) or []:
            where[int(iid)] = {"where": k, "t": (where.get(int(iid)) or {}).get("t")}
    for iid in worn_ids:
        where[int(iid)] = {"where": "worn", "t": (where.get(int(iid)) or {}).get("t")}
    return {"items": {str(k): v for k, v in where.items()}, "inventory_known": bags is not None,
            "inventory_t": (bags or {}).get("t")}


QUEST_SIDE = re.compile(r"\bside (alliance|horde|both)\b", re.I)
QUEST_WHO = re.compile(r"(NPC|object|item) (\d+) ([^;.]+)")


def _quest_who(body: str, label: str) -> list[dict]:
    """"Starts: NPC 234 Gryan Stoutmantle; item 2874 An Unsent Letter." ->
    [{kind, id, name}], as tools/kb/export.py writes forever-db's quests."""
    m = re.search(label + r": (.*?)\.(?: [A-Z][a-z]+:|$)", body)
    return [{"kind": k.lower(), "id": int(i), "name": n.strip()} for k, i, n in QUEST_WHO.findall(m.group(1))] \
        if m else []


def dungeon_quests(knowledge, side: str | None, runs: dict) -> dict[str, dict]:
    """Per dungeon (its area ID), the quests that belong to it: the ones whose
    zone is the dungeon in forever-db, and the ones Polestar's run of it
    picks up, works on or hands in (an item that starts a quest inside, a
    hand-in in a city). The other side's are left out. Each with its level,
    XP (forever-db, Forever's own numbers), who gives it and who takes it
    back, and Polestar's note on it: picked up on the route, or left out
    and why. {area ID: {quests, other_side}}: `other_side` counts the other
    side's quests, so a dungeon with only those (Ragefire Chasm for the
    Alliance) reads as the other side's."""
    from wowstats import forever

    from .estimate import dkey

    areas = {aid: a for aid, a in ((forever.data() or {}).get("areas") or {}).items()
             if a.get("instance") == "dungeon"}
    by_key = {dkey(a["name"]): aid for aid, a in areas.items()}
    want: dict[str, dict[int, str | None]] = {aid: {} for aid in areas}
    for qid, row in ((forever.data() or {}).get("quests") or {}).items():
        aid = forever.dungeon_area(row[3])
        if aid in want:
            want[aid][int(qid)] = None
    for name, r in runs.items():
        aid = by_key.get(dkey(name))
        if aid is None:
            continue
        for kind in ("pickups", "objectives", "item_starts", "handins"):
            for qid in r.get(kind) or []:
                if isinstance(qid, int):
                    want[aid][qid] = "route"
        for qid, why in (r.get("left_out") or {}).items():
            if str(qid).isdigit() and int(qid) in want[aid]:
                want[aid][int(qid)] = f"left out of Polestar's route: {why}"
    pre = (forever.data() or {}).get("pre") or {}

    def prereqs(qid: int) -> list[int]:
        return sorted({p for part in pre.get(str(qid)) or [[], []] for p in part})

    ids = sorted({q for qs in want.values() for q in qs} | {p for qs in want.values() for q in qs
                                                             for p in prereqs(q)})
    docs: dict[int, str] = {}
    item_docs: dict[int, str] = {}

    def fetch(prefix: str, wanted: list[int], into: dict[int, str]) -> None:
        for k in range(0, len(wanted), 500):
            part = [f"{prefix}:{q}" for q in wanted[k:k + 500]]
            for did, body in knowledge.db.execute(
                    "SELECT id, body FROM docs WHERE id IN (" + ",".join("?" * len(part)) + ")", part):
                into[int(did.split(":")[1])] = body or ""

    if knowledge is not None and ids:
        lock = getattr(knowledge, "lock", None) or contextlib.nullcontext()
        with lock:
            fetch("quest", ids, docs)
            starters = sorted({w["id"] for b in docs.values() for w in _quest_who(b, "Starts") if w["kind"] == "item"})
            fetch("item", starters, item_docs)

    def how(qid: int) -> dict:
        """Where a prerequisite quest starts: its NPC or object, or the item
        that starts it, with the creatures that drop that item ("drops") and
        the objects in the world that hold it ("found": a journal on the
        ground, often named like the item, so it can't read as dropping
        from itself)."""
        starts = _quest_who(docs.get(qid, ""), "Starts")
        drops, found = [], []
        for w in starts:
            if w["kind"] == "item":
                m = re.search(r"Sources: (.*?)(?:\. [A-Z][a-z]+:|$)", item_docs.get(w["id"], ""))
                for k, i, n in re.findall(r"(NPC|object) (\d+) ([^;]+?) \d+(?:\.\d+)?%", m.group(1)) if m else []:
                    (drops if k == "NPC" else found).append({"kind": k.lower(), "id": int(i), "name": n.strip()})
        return {"starts": starts, "drops": drops, "found": found}
    out: dict[str, dict] = {}
    for aid, qs in want.items():
        rows, other = [], 0
        for qid, note in sorted(qs.items()):
            body = docs.get(qid, "")
            sd = QUEST_SIDE.search(body)
            sd = sd.group(1).title() if sd else None
            if side and sd and sd not in (side, "Both"):
                other += 1
                continue
            fq = forever.quest(qid) or {}
            xp = re.search(r"XP (\d+)", body)
            mn = re.search(r"Money (\d+) copper", body)
            rows.append({"quest_id": qid, "name": fq.get("name"), "level": fq.get("level"),
                         "min_level": fq.get("min_level"), "zone": fq.get("zone"),
                         "side": sd, "xp": int(xp.group(1)) if xp else None,
                         "money_copper": int(mn.group(1)) if mn else None,
                         "starts": _quest_who(body, "Starts"), "ends": _quest_who(body, "Ends"),
                         "route": note, "pre": {str(p): how(p) for p in prereqs(qid)}})
        if rows or other:
            out[aid] = {"quests": _one_per_title(rows), "other_side": other}
    return out


def _one_per_title(rows: list[dict]) -> list[dict]:
    """One row per quest title and side: forever-db has a quest per item a
    repeatable hand-in takes (Gnomeregan's "The Sparklematic 5200!" six
    times), which would count as six quests and six times the XP. Chains
    that share a title (forever.data()["parts"]) stay apart. The row kept
    is the first with XP; `variants` lists every ID, so any of them done or
    in the log counts."""
    from wowstats import forever

    parts = (forever.data() or {}).get("parts") or {}
    out: list[dict] = []
    by: dict[tuple, dict] = {}
    for r in rows:
        k = (r["name"], r["side"]) if r["name"] and str(r["quest_id"]) not in parts else ("#", r["quest_id"])
        kept = by.get(k)
        if kept is None:
            by[k] = r
            out.append(r)
        else:
            kept.setdefault("variants", [kept["quest_id"]]).append(r["quest_id"])
            if kept["xp"] is None and r["xp"] is not None:
                ids = kept["variants"]
                out[out.index(kept)] = by[k] = {**r, "variants": ids}
    return out


def extract(db, knowledge, ref, *, advice: dict | None = None, scans: dict | None = None, state: dict | None = None,
            reviews: dict | None = None, now: float | None = None, cards=None, loot: dict | None = None,
            bags: dict | None = None, done: dict[int, str] | None = None, sem=None) -> dict:
    """The estimator's inputs, JSON only. `done`: wowstats.quests.completed
    of the character's events; without it, the prep's quests_done table
    (the same answer, from the events `db` was built from). `sem`: the
    semantic layer over `db` (semantic_of), when the caller has one."""
    from wowstats import forever

    now = now or time.time()
    scans = scans or {}
    cards = cards if cards is not None else card_lookup()
    sem = semantic_of(db, sem)  # one copy for every reader below
    quests_done = done
    if quests_done is None:
        quests_done = {r["quest_id"]: r["done_source"] for r in ask(
            sem, metrics=["quests_known_done"], group_by=["quest_id", "done_source"], order_by=["quest_id"])}
    meta = header(db)
    layer, ro = sem
    _LEVELS[0] = None  # a prep asked again (the tests' replays) reads its levels again
    levels = level_rows(db, layer, ro)
    # The character now: the newest XP bar reading, the newest snapshot and
    # the records' highest level and newest time (the layer's latest and max).
    x = rows_of(layer, ro, metrics=["bar_t", "bar_level", "bar_xp", "bar_xp_max", "bar_rested_xp"])[0]
    x = (x["bar_t"], x["bar_level"], x["bar_xp"], x["bar_xp_max"], x["bar_rested_xp"]) if x["bar_t"] else None
    keys = ("snapshot_last_level", "snapshot_last_xp", "snapshot_last_xp_max", "money_copper", "snapshot_last_zone",
            "bind", "resting")
    snap = rows_of(layer, ro, metrics=["snapshot_count", *keys])[0]
    snap = (None, *(snap[k] for k in keys)) if snap["snapshot_count"] else None
    recs = rows_of(layer, ro, metrics=["level_max", "last_record_t"])[0]
    level = max(_i(x[1], 0) if x else 0, _i(snap[1], 0) if snap else 0, recs["level_max"] or 0)
    if not level:
        # Polestar's level splits alone (no sheet, no Recorder): the level
        # after the last one finished, or the one still in progress.
        level = max((lv["level"] + (0 if lv["partial"] else 1) for lv in levels if lv["level"]), default=0)
    in_split = next((lv["xp"] for lv in levels if lv["level"] == level and lv["partial"]), 0)
    xp_now = x[2] if x and x[1] == level else (snap[2] if snap and snap[1] == level else in_split or 0)
    xp_max = x[3] if x and x[1] == level else (snap[3] if snap and snap[1] == level else None)
    last_t = recs["last_record_t"] or now
    # Rested XP left at the last record, plus what logging out added since
    # (the KB's Classic rates, labelled as such).
    rested_then = (x[4] if x else 0) or 0
    resting = bool(snap[7]) if snap and snap[7] is not None else None
    away = max(0.0, now - last_t)
    acc = knowledge.rule("rested.accrual") if knowledge is not None else None
    if acc:
        v = acc["value"]
        gain = (xp_max or 0) * v["per_8h_rested_area"] * (away / 28800) * (1 if resting else v["away_factor"])
        rested_now = min(rested_then + gain, (xp_max or 0) * v["cap_levels"])
    else:
        rested_now = rested_then  # no sourced accrual rule: only what the game last said
    xp_table = {lv["level"]: lv["xp"] for lv in levels if lv["xp"]}
    if xp_max:
        xp_table[level] = xp_max
    hist = step_history(db, ref, sem)
    pos = position(db, sem)
    side = meta.get("faction")
    seq = guide_sequence(ref, side, level) if side else []
    ahead: list[dict] = []
    if pos:
        guides = [pos["guide"]] + [g for g in seq if g != pos["guide"]][:3] if pos["guide"] in seq or not seq else \
            [pos["guide"]] + seq[:3]
        if pos["guide"] in seq:
            guides = [pos["guide"]] + seq[seq.index(pos["guide"]) + 1:][:3]
        for g in guides:
            steps = guide_steps(ref, g)
            kinds = kinds_for(g, steps)
            start = pos["anchor_step"] if g == pos["guide"] else 1
            ahead += [{"guide": g, "guide_title": ((ref.guides or {}).get(g) or {}).get("title"), "step": n,
                       "planned_s": p, "title": t, "kind": kinds[n]} for n, p, t in steps
                      if n >= start and not other_class(t, meta.get("class"))]
        steps = guide_steps(ref, pos["guide"])
        kinds = kinds_for(pos["guide"], steps)
        pos["passed_over"] = [{"step": n, "planned_s": p, "title": t, "kind": kinds[n]} for n, p, t in steps
                              if pos["furthest_step"] <= n < pos["anchor_step"]]
        pos["shown_behind"] = [{"step": n, "planned_s": p, "title": t, "kind": kinds[n]} for n, p, t in steps
                               if pos["shown_step"] <= n <= pos["shown_step"] + 1 and pos["shown_step"] <
                               pos["furthest_step"]]
    sessions = recent_sessions(layer, ro)
    done = [lv for lv in levels if lv["played_s"] and not lv["partial"] and lv["level"] < level][-PACE_LEVELS:]
    lo = done[0]["level"] if done else level
    # Kill XP outside dungeons over every XP the bar gained, from level lo
    # on (a record's level is the one it was written at, so this is the
    # play since the first record at lo).
    kill_xp = sum(r["kill_xp"] or 0 for r in ask(sem, metrics=["kill_xp"], where=[
        {"dimension": "level", "op": ">=", "value": lo}, {"dimension": "dungeon_trip", "op": "is null"}]))
    all_xp = sum(r["xp_gained"] or 0 for r in ask(sem, metrics=["xp_gained"], where=[
        {"dimension": "level", "op": ">=", "value": lo}]))
    share_rule = knowledge.rule("quest_xp.share_by_levels_over") if knowledge is not None else None
    # A quest still in the log that only Battle.net's worn items prove done
    # (the history closes the others in the prep, wowstats.quests.settles_open).
    oq = [o for o in open_quests(db, level, sem) if quests_done.get(o["quest_id"]) != "battlenet"]
    for o in oq:
        o.update({k: v for k, v in quest_facts(knowledge, o["quest_id"]).items() if k != "xp"},
                 xp_full=quest_facts(knowledge, o["quest_id"])["xp"])
    # The last class trainer: the Recorder logs every trainer window, so the
    # profession and riding trainers the data knows are left out (weapon
    # masters it can't tell); Polestar's own classTrainVisit wins (gather).
    others = trainers.not_class_trainers()
    visits = [r for r in ask(sem, metrics=["last_record_t"], group_by=["npc_id", "level"],
                             where=[{"dimension": "kind", "op": "=", "value": "trainer"}]) if r["npc_id"] not in others]
    trainer = max(((r["last_record_t"], r["level"]) for r in visits), default=None, key=lambda v: v[0])
    ding_even = max((r["last_record_t"] for r in ask(sem, metrics=["last_record_t"], group_by=["level"], where=[
        {"dimension": "kind", "op": "=", "value": "level"}]) if r["level"] is not None and r["level"] % 2 == 0),
        default=None)
    profs = [{"name": r["profession"], "rank": r["profession_rank"], "max_rank": r["profession_max_rank"]}
             for r in ask(sem, metrics=["profession_rank", "profession_max_rank"], group_by=["skill_id", "profession"],
                          order_by=["skill_id"])]
    # Time on each profession's Polestar guide and the skill-ups made while it was up.
    lines = {p["name"]: f"forever/professions/{(p['name'] or '').lower()}" for p in profs}
    by_guide = {r["guide"]: r for r in ask(sem, metrics=["active_s", "skill_ups"], group_by=["guide"], where=[
        {"dimension": "guide", "op": "in", "values": sorted(set(lines.values()))}])} if lines else {}
    prof_rate = {name: {"seconds": round((by_guide.get(sl) or {}).get("active_s") or 0),
                        "skill_ups": (by_guide.get(sl) or {}).get("skill_ups") or 0} for name, sl in lines.items()}
    tr = trips(ask_on(db, sem))
    trip_pace(sem, tr, {k: meta.get(k) for k in ("class", "race", "faction")})
    for t in tr:
        t["loot"] = run_loot(t, cards, scans)
    ups = upgrades(sem, (advice or {}).get("upgrades_all") or [])
    for u in ups:
        u["ah_copper"] = (scans.get(u.get("item_id")) or {}).get("copper")
    from wowstats.analysis import standing

    st = state or {}
    findings = sorted((st.get("findings") or {}).values(), key=lambda f: -float(f.get("score") or 0))
    dungeons = []
    for aid, a in ((forever.data() or {}).get("areas") or {}).items():
        lv = a.get("levels") or []
        if a.get("instance") == "dungeon" and len(lv) == 2 and lv[1] >= level - 2 and lv[0] <= level + 5:
            dungeons.append({"area_id": int(aid), "name": a["name"], "min": lv[0], "max": lv[1]})
    from .dungeonview import kb_areas

    for a in kb_areas().values():  # Forever's new dungeons the client's tables don't list (the KB's)
        lv = a["levels"]
        if lv[1] >= level - 2 and lv[0] <= level + 5:
            dungeons.append({"area_id": None, "name": a["name"], "min": lv[0], "max": lv[1], "rule": a["rule"]})
    runs = dungeon_runs_kb(knowledge, side)
    worn_ids = sorted({_i(i.get("item_id")) for i in ((advice or {}).get("slots") or [])
                       for i in [i.get("current") or {}] if i.get("item_id")})
    return {
        "schema": 1, "now_t": now, "data_until_t": last_t,
        "who": {k: meta.get(k) for k in ("char", "class", "race", "faction")},
        "level": level, "xp": xp_now or 0, "xp_max": xp_max, "rested_xp_then": rested_then,
        "rested_xp": round(rested_now), "resting": resting, "offline_s": round(away),
        "zone": (pos or {}).get("zone") or (snap[5] if snap else None), "subzone": (pos or {}).get("subzone"),
        "bind": snap[6] if snap else None, "gold_copper": snap[4] if snap else None,
        "professions": profs, "profession_rate": prof_rate,
        "open_quests": oq, "position": pos, "last_position": last_position(db, sem), "route_ahead": ahead,
        "step_history": hist,
        "levels": levels, "xp_table": {str(k): v for k, v in xp_table.items()},
        "sessions": sessions, "kill_xp_share": round(kill_xp / all_xp, 3) if all_xp else None,
        "kill_xp_levels": [lo, level],
        "trainer": {"last_t": trainer[0] if trainer else None, "last_level": trainer[1] if trainer else None,
                    "even_ding_t": ding_even},
        "trips": tr, "dungeons": sorted(dungeons, key=lambda d: (d["min"], d["name"])), "dungeon_runs_kb": runs,
        "upgrades": [{k: u.get(k) for k in ("item_id", "name", "quality", "slot_name", "gain", "gain_share_pct",
                                            "level", "now", "fills_empty", "sources", "ah_copper", *BADGE)} | (
            {"with_item_id": (u.get("with") or {}).get("item_id"), "with": _partner(u["with"])} if u.get("with")
            else {}) for u in ups],
        "gear_spec": ((advice or {}).get("spec") or {}).get("label"),
        "worn_ids": worn_ids,
        # The dungeon panel's (wowstats.plan.dungeonview): every item each
        # dungeon gives him, what he has, the quests he handed in.
        "dungeon_loot": loot or {}, "owned": owned(db, worn_ids, bags, sem),
        "dungeon_quests": dungeon_quests(knowledge, side, runs),
        "gear_score": (advice or {}).get("gear_score"),
        "quests_done": sorted(quests_done), "quests_done_from": quests.counts(quests_done),
        "mount": {**mount_facts(knowledge), "riding_skill": riding_known(db, sem)},
        "rules": {"quest_xp_share": cite(share_rule) | {"value": share_rule["value"]} if share_rule else None,
                  "rested_accrual": cite(acc) | {"value": acc["value"]} if acc else None},
        "rested_burn": rested_burn(db, sem),
        "route_levels": route_levels(ref, meta, level),
        "gold_rate": gold_rate(db, now=now, sem=sem), "loot_value": loot_value(db, cards, scans, now=now, sem=sem),
        # What the plan acts on: findings judged under today's definitions
        # and the nudges standing on them (analysis.standing, review 6).
        "findings": [{"key": f.get("key"), "title": f.get("title"), "text": f.get("text")}
                     for f in [f for f in findings if not f.get("doubtful") and not f.get("revalidate")][:10]],
        "nudges": [{"text": n.get("text"), "verdict": n.get("verdict"), "at_level": n.get("at_level")}
                   for n in standing(st)["nudges"]],
        "analysis_updated": st.get("updated"),
        "calibration": reviews or {"factor": 1.0, "n": 0},
        "progress": progress(db, sem),
        "instance_limit": instance_limit(),
    }


def dungeon_runs_kb(knowledge, side: str | None) -> dict[str, dict]:
    """Polestar's dungeon run documents in the knowledge base (its own store,
    not play data), for the character's side: each run's objectives, the
    quests to pick up and hand in, the guide, item-started quests, notes."""
    runs: dict[str, dict] = {}
    if knowledge is None or not side:
        return runs
    for d in knowledge.db.execute("SELECT id, body FROM docs WHERE kind='polestar-dungeon'").fetchall():
        with contextlib.suppress(ValueError):
            j = json.loads(d[1])
            if isinstance(j, dict) and j.get("faction") in (side, "Both"):
                runs[j["dungeon"]] = {"objectives": (j.get("run") or {}).get("objectives") or [],
                                      "pickups": [p.get("quest") for p in j.get("pickups") or []],
                                      "guide": (j.get("run") or {}).get("guide"),
                                      "handins": [h.get("quest") for h in j.get("handins") or []],
                                      "item_starts": [x.get("quest") for x in
                                                      (j.get("run") or {}).get("itemStarts") or []],
                                      "notes": {str(p.get("quest")): p.get("note") for p in j.get("pickups") or []
                                                if p.get("note")},
                                      "left_out": {str(k): v for k, v in (j.get("leftOut") or {}).items()}}
    return runs


def class_train_level(rows: list[dict]) -> int | None:
    """The level of the last class trainer visit Polestar saw, from the newest
    wow-gear event that carries it."""
    g = [r for r in rows if r.get("feed") == "wow-gear" and _i(r.get("class_train_level"), None)]
    return _i(max(g, key=lambda r: parse_t(r.get("_time"))).get("class_train_level"), None) if g else None


def _partner(w: dict) -> dict:
    """The other hand of a weapon setup (wowstats.gear WeaponSetups): what
    it is and where it comes from, so a plan never says "equip" one half."""
    return {k: w.get(k) for k in ("kind", "item_id", "name", "slot_name", "level", "sources") if w.get(k) is not None}


def instance_limit() -> dict | None:
    """The instances a character may enter per hour, from the KB's
    instance.entries_per_hour, with its source; None: unknown, never assumed."""
    from wowstats.analysis.kb import static_rule

    r = static_rule("instance.entries_per_hour")
    n = ((r or {}).get("value") or {}).get("per_hour")
    return {"per_hour": int(n), "source": cite(r)} if n else None


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def now_utc() -> datetime:
    return datetime.now(UTC)
