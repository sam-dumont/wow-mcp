"""What the numbers say: the Overview, the two questions the coach answers
first (`next_moves`, `sanity_check`), deaths with their likely killer,
dungeon runs, the timeline and the level snapshots.

Pure functions over rows from VictoriaLogs: `Q` is any callable that takes a
LogsQL query (after the stream filter) and returns rows, so the web server
(Views) and the MCP server (its own Store) share every rule here.

Every claim carries `cite` (what it was computed from) and `link` (the
section of the page that holds that data), so the page links each line and
the coach can quote its source.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from . import forever, profcaps, trainers
from . import gear as gearmod
from . import timebounds as tb
from .analysis import kb
from .links import dungeon_url, wowhead_url
from .stores import (
    SESSION_BUCKETS,
    ZONE_RECORDS,
    iso,
    lq,
    num,
    opt_num,
    parse_t,
    split_sessions,
    zone_time,
    zone_totals,
)

Q = Callable[[str], list[dict]]
# query_metric's arguments -> rows as dicts: the semantic layer on the
# character's narrow prep (wowstats.analysis.pages.asker).
Ask = Callable[..., list[dict]]

# Seconds before a death that count as its fight. Polestar_Recorder's own
# killer guess is the last hostile creature that targeted the player within
# 10 s (World.lua); the fight around it is read over a wider window.
FIGHT_S = 30
# Two deaths in a zone, to the same creature, or to creatures this many
# levels above the player, make a cluster worth a line.
CLUSTER_MIN = 2
LEVEL_GAP = 3
COMBAT_S = 300
# A guide step that took this many times the route's plan.
SLOW_STEP = 3.0
# The rank a skill cap leads to, its level and skill gate and its price come
# from the knowledge base (data/kb/rules.json: profession.tiers,
# profession.training_cost); the trainers, and a level or price their guide
# gives, from Polestar's guides (wowstats.trainers).
PRICE_NOTE = "before a reputation discount"
# Riding: from this many levels before the tier's level, a riding move says
# where and what it costs, so the gold can be put by.
RIDING_AHEAD = 8
RIDING_SKILL = 762
# A source is in reach when its quest or its dungeon is at most this many
# levels above the character: an item it could wear now from a level-42
# quest is no move for a level 32.
REACH_LEVELS = 3
PRIMARY = {164: "Blacksmithing", 165: "Leatherworking", 171: "Alchemy", 182: "Herbalism", 186: "Mining",
           197: "Tailoring", 202: "Engineering", 333: "Enchanting", 393: "Skinning", 755: "Jewelcrafting"}
SECONDARY = {129: "First Aid", 185: "Cooking", 356: "Fishing"}


def rec(cf: str) -> str:
    return f'feed:="wow-recorder" {cf}'


def claim(text: str, link: str, cite: str, **kw) -> dict:
    return {"text": text, "link": link, "cite": cite, **kw}


def _n(x) -> str:
    """A count with thousands separators (17,398), as the page writes it."""
    return "?" if x is None else f"{round(x):,}"


def ratio_text(r: float) -> str:
    """A pace ratio, the one way the page and the claims write it: 2.0×."""
    return f"{r:.1f}×"


def zone_ref(name: str | None, levels=None) -> dict | None:
    """A zone or dungeon the page can name and link (Wowhead Forever): a
    dungeon its guide, when Wowhead has one."""
    if not name:
        return None
    zid = forever.area_id(name)
    out = {"name": name, "id": zid, "url": dungeon_url(name, zid)}
    if levels and len(levels) == 2:
        out["levels"] = [int(levels[0]), int(levels[1])]
    return out


def item_ref(u: dict) -> dict:
    """An upgrade as the Overview shows it: the item, its slot, its gain,
    its badge (gear.bis_badge)."""
    w = u.get("with") or {}
    return {**{k: u.get(k) for k in ("item_id", "name", "quality", "slot", "slot_name", "gain", "gain_share_pct",
                                     *gearmod.BADGE)},
            "url": wowhead_url("item", u["item_id"]) if u.get("item_id") else None,
            # A weapon setup's gain is both hands': the other one goes with it
            # (an off hand alone against a two-hander is no upgrade).
            **({"with": {"item_id": w["item_id"], "name": w.get("name"), "slot_name": w.get("slot_name"),
                         "kind": _partner_have(w), "url": wowhead_url("item", w["item_id"])}}
               if w.get("item_id") else {})}


def _partner_have(w: dict) -> str | None:
    """worn, bags, bank, or get: where the other hand of a setup is."""
    kinds = {x.get("kind") for x in w.get("sources") or []}
    if w.get("kind") == "get" and kinds and kinds <= set(gearmod.HELD):
        return "bags" if "bags" in kinds else "bank"
    return w.get("kind")


def source_ref(s: dict, level: int | None = None) -> dict:
    """Where an upgrade comes from, with its quest and zone linkable, and
    what stands in the way at `level`, if anything."""
    qid = s.get("quest_id")
    out = {"kind": s.get("kind"), "boss": s.get("boss"), "profession": s.get("profession"),
           "quest": {"id": qid, "name": s.get("name"), "url": wowhead_url("quest", qid)} if qid else None,
           "zone": zone_ref(s.get("zone"), s.get("levels")), "blocker": blocker(s, level)}
    if s.get("via"):  # ForeverDB's data: how sure, and the words for the kinds the page has none for
        out.update(via=s["via"], basis=s.get("basis"))
        if s.get("kind") not in ("quest", "drop", "craft"):
            out["text"] = gearmod.source_text(s)
    return out


def blocker(s: dict, level: int | None) -> str | None:
    """What keeps a source from being done right now at `level`: a quest
    that needs a higher level, or one that is above it; a dungeon whose
    range starts higher. None when nothing does."""
    if level is None:
        return None
    if s.get("kind") == "quest":
        need = s.get("req_level")
        if need and need > level:
            return f"the quest needs level {need}"
        if (s.get("level") or 0) > level:
            return f"a level {s['level']} quest: {s['level'] - level} above you"
    if s.get("kind") == "drop":
        lv = s.get("levels") or []
        if lv and lv[0] > level:
            return f"the dungeon starts at level {lv[0]}"
    return None


# ForeverDB's kinds with a gate: in reach for a move only when the
# character's data says it is met (wowstats.gear rep_check: the recorded
# standing; a PvP rank held).
UNCHECKED = frozenset({"reputation", "pvp"})


def reachable(s: dict, level: int | None) -> bool:
    """Whether a source can be done at `level`: its quest or dungeon at most
    REACH_LEVELS above, a recipe the character can make."""
    if level is None:
        return True
    if s.get("kind") == "quest":
        return (s.get("level") or 0) <= level + REACH_LEVELS
    if s.get("kind") == "drop":
        lv = s.get("levels") or []
        return not lv or lv[0] <= level + REACH_LEVELS
    if s.get("kind") == "craft":
        return bool(s.get("can_make"))
    if s.get("kind") in UNCHECKED:
        return s.get("met") is True
    return True


def in_reach(advice: dict | None, level: int | None) -> list[tuple[dict, dict]]:
    """Upgrades the character could wear now, with the first source it can
    reach at its level, best first (the gear advice's order)."""
    out = []
    for u in (advice or {}).get("upgrades") or []:
        if not u.get("now") or u.get("vs_unknown"):
            continue
        s = next((s for s in u.get("sources") or [] if reachable(s, level)), None)
        # A weapon setup is in reach when its other hand is too.
        w = u.get("with") or {}
        if w.get("kind") == "get" and not any(reachable(x, level) for x in w.get("sources") or []):
            continue
        if s is not None:
            out.append((u, s))
    return out


def dur(s) -> str | None:
    if s is None:
        return None
    s = int(s)
    if s < 60:
        return f"{s}s"
    h, m = divmod(s // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"  # as the page writes it (app.js dur)


def _dedupe(rows: list[dict]) -> list[dict]:
    """One row per record; a record sent twice (a backfill after a shipper
    update) keeps the fields of both copies."""
    seen: dict = {}
    for r in rows:
        k = r.get("source_id") or (r.get("_time"), r.get("kind"), r.get("_msg"))
        if k in seen:
            seen[k].update({f: v for f, v in r.items() if v not in (None, "") and not seen[k].get(f)})
        else:
            seen[k] = dict(r)
    return list(seen.values())


def _sorted(rows: list[dict]) -> list[dict]:
    return sorted(_dedupe(rows), key=lambda r: parse_t(r.get("_time")))


# ------------------------------------------------------------------- deaths


def level_at(dings: list[tuple[datetime, int]], t: datetime, default: int | None = None) -> int | None:
    """The character's level at `t`, from the dings before it (levels)."""
    i = tb.at_or_before([at for at, _ in dings], t)  # a ding at t already holds at t
    if i >= 0:
        return dings[i][1]
    return dings[0][1] - 1 if dings else default


def dings_from(levels: list[dict], recorder_levels: list[dict]) -> list[tuple[datetime, int]]:
    """(time, level reached) from wow-level splits (a split for level L ends
    with the ding to L+1) and the recorder's own level records."""
    pts: dict[int, datetime] = {}
    for lv in levels:
        if lv.get("at") and lv.get("level") is not None:
            pts[int(lv["level"]) + 1] = parse_t(lv["at"])
    for r in recorder_levels:
        lv = opt_num(r.get("level"))
        if lv is not None and lv not in pts:
            pts[int(lv)] = parse_t(r.get("_time"))
    return sorted(((t, lv) for lv, t in pts.items()), key=lambda x: x[0])


RANKS = {"elite": "an elite", "rareelite": "a rare elite", "rare": "a rare", "worldboss": "a world boss"}


def unnamed_creature(classification: str | None) -> str:
    """A creature no source names (not in the records, not in forever-db):
    what the recorder saw of it, never its id. The records carry no creature
    type or family, only the unit's classification."""
    rank = RANKS.get((classification or "").lower())
    if rank == "a world boss":
        return "a world boss"
    return f"{rank} creature" if rank else "an unnamed creature"


def fight_before(death: dict, rows: list[dict], level: int | None) -> dict:
    """The likely killer of one death, from Polestar_Recorder alone (no
    combat log): the death record's own npc_id/npc_level (the last hostile
    creature that targeted the player within 10 s), else the hostile unit
    seen last before it; plus the fight around it from the combat, unit and
    kill records of the FIGHT_S seconds before."""
    t = parse_t(death.get("_time"))
    before = [r for r in rows if t - timedelta(seconds=FIGHT_S) <= parse_t(r.get("_time")) <= t]
    hostile = [r for r in before if r.get("kind") == "unit" and r.get("reaction") in ("h", "n")
               and r.get("npc_id")]
    # Combat can start well before the last 30 s: its start is read over
    # COMBAT_S, the last "on" with no "off" after it.
    combat = [r for r in rows if r.get("kind") == "combat" and parse_t(r.get("_time")) <= t]
    combat_on = [r for r in combat if r.get("on") == "true"]
    if combat and combat[-1].get("on") != "true" and parse_t(combat[-1]["_time"]) < t - timedelta(seconds=2):
        combat_on = []  # out of combat before the death: a fall, drowning, or a dot after the fight
    # The fight started at the last combat start before the death (or the
    # first record in the window, when combat began earlier).
    start = parse_t(combat_on[-1]["_time"]) if combat_on else None
    npc = opt_num(death.get("npc_id"))
    npc_level = opt_num(death.get("npc_level"))
    name = death.get("npc_name")
    how = "the recorder's own pick: the last hostile creature that targeted you within 10 s"
    if not npc and hostile:
        last = hostile[-1]
        npc, npc_level, name = opt_num(last.get("npc_id")), opt_num(last.get("level")), last.get("npc_name")
        how = "the last hostile creature seen before the death"
    if npc and not name:
        name = next((r.get("npc_name") for r in rows if opt_num(r.get("npc_id")) == npc and r.get("npc_name")),
                    None) or forever.npc_name(npc)
    rank = next((r.get("classification") for r in reversed(hostile) if opt_num(r.get("npc_id")) == npc
                 and r.get("classification")), None)
    attackers = {opt_num(r.get("npc_id")) for r in hostile if parse_t(r.get("_time")) >= t - timedelta(seconds=15)}
    if npc:
        attackers.add(npc)
    killed = [r for r in before if r.get("kind") == "kill"]
    out = {
        "t": death.get("_time"), "zone": death.get("zone"), "level": level,
        "killer": ({"npc_id": npc, "name": name or unnamed_creature(rank), "named": bool(name), "level": npc_level,
                    "likely": True, "how": how} if npc else None),
        "attackers": len(attackers) or None,
        "fight_s": int((t - start).total_seconds()) if start else None,
        "killed_in_fight": len(killed),
        "level_gap": (npc_level - level) if (npc_level and level) else None,
        "in_dungeon": bool(death.get("instance")),
    }
    return out


def deaths(q: Q, cf: str, levels: list[dict]) -> list[dict]:
    """Every death with its likely killer (marked likely: no combat log)."""
    rows = _sorted(q(f"{rec(cf)} kind:in(death, level) | fields _time, kind, zone, level, npc_id, npc_level, "
                     f"npc_name, source_id"))
    ds = [r for r in rows if r.get("kind") == "death"]
    dings = dings_from(levels, [r for r in rows if r.get("kind") == "level"])
    out = []
    for d in ds:
        t = parse_t(d.get("_time"))
        lo, hi = iso(t - timedelta(seconds=COMBAT_S)), iso(t + timedelta(seconds=1))
        fight = _sorted(q(f"{rec(cf)} kind:in(combat, unit, kill) _time:[{lo}, {hi}] | fields _time, kind, on, "
                          f"npc_id, npc_name, level, reaction, classification, source_id"))
        out.append(fight_before(d, fight, level_at(dings, t)))
    return out


DEATH = {"dimension": "kind", "op": "=", "value": "death"}


def death_counts(ask: Ask, *where: dict) -> dict:
    """The deaths metric and its killer-level companions for the deaths
    `where` picks (the semantic layer): how many, how many to a creature
    LEVEL_GAP+ levels above (deaths_above), the largest gap, and the level
    to come back at (the highest of those creatures)."""
    r = (ask(metrics=["deaths", "deaths_above", "level_gap_max", "come_back_level"], where=[DEATH, *where])
         or [{}])[0]
    return {"n": int(r.get("deaths") or 0), "above": int(r.get("deaths_above") or 0),
            "gap": r.get("level_gap_max"), "back": r.get("come_back_level")}


def death_clusters(ask: Ask, ds: list[dict]) -> list[dict]:
    """Deaths that repeat: the same zone, the same creature, or creatures
    well above the player's level. Counted by the semantic layer (deaths by
    zone, by likely killer and zone, deaths_above); `ds` (deaths()) names
    the creatures and orders a tie by the earliest death."""
    first: dict = {}
    for i, d in enumerate(ds):
        first.setdefault(("zone", d.get("zone")), i)
        if d.get("killer"):
            first.setdefault(("npc", d["killer"]["npc_id"], d.get("zone")), i)
    names = {d["killer"]["npc_id"]: d["killer"]["name"] for d in reversed(ds) if d.get("killer")}
    out = []
    zones = [r for r in ask(metrics=["deaths"], group_by=["zone"], where=[DEATH]) if r["zone"]]
    for r in sorted(zones, key=lambda r: (-r["deaths"], first.get(("zone", r["zone"]), len(ds)))):
        if r["deaths"] >= CLUSTER_MIN:
            out.append({"by": "zone", "zone": r["zone"], "n": int(r["deaths"])})
    mobs = [r for r in ask(metrics=["deaths"], group_by=["npc_id", "zone"], where=[DEATH]) if r["npc_id"]]
    for r in sorted(mobs, key=lambda r: (-r["deaths"], first.get(("npc", r["npc_id"], r["zone"]), len(ds)))):
        if r["deaths"] >= CLUSTER_MIN:
            npc = int(r["npc_id"])
            out.append({"by": "creature", "npc_id": npc, "name": names.get(npc) or forever.npc_name(npc)
                        or unnamed_creature(None), "zone": r["zone"], "n": int(r["deaths"])})
    high = death_counts(ask)
    if high["above"]:
        out.append({"by": "level gap", "n": high["above"], "gap": high["gap"]})
    return out


# ----------------------------------------------------------------- dungeons


def dungeon_runs(q: Q, cf: str) -> list[dict]:
    """Each dungeon run (wow-dungeon) joined to the recorder records inside
    it: party size (group records), boss fights and wipes (encounter), and
    deaths; and who was in the party, from the groupmates ForeverDB_Helper's
    journal names (wow-player, storemodels.run_party)."""
    runs = _sorted(q(f'feed:="wow-dungeon" {cf} | fields _time, name, dungeon_id, level, seconds, xp, '
                     f"source_id"))
    if not runs:
        return []
    lo = min(parse_t(r.get("_time")) for r in runs) - timedelta(minutes=1)
    hi = max(parse_t(r.get("_time")) + timedelta(seconds=num(r.get("seconds"))) for r in runs) + timedelta(minutes=1)
    # Group records are written on change: the party in a run is the last
    # one before it plus any change inside.
    rows = _sorted(q(f"{rec(cf)} kind:in(group, encounter, death) _time:[{iso(lo - timedelta(hours=6))}, {iso(hi)}] "
                     f"| fields _time, kind, size, raid, encounter_id, success, npc_id, npc_name, source_id"))
    players = _sorted(q(f'feed:="wow-player" {cf} player:* context:in(grouped, dungeon) '
                        f"_time:[{iso(lo - timedelta(hours=6))}, {iso(hi)}] "
                        f"| fields _time, player, context, level, class, race, role, source_id"))
    from wowstats import storemodels

    return storemodels.run_groups(runs, rows, players)


ROSTER_NOTE = ("Who was in the group comes from ForeverDB_Helper (optional, from ForeverDB.net), whose journal "
               "names each groupmate once a day; without it, no addon the kit reads saves names: "
               "Polestar_Recorder keeps the party size, Polestar's instance runs the dungeon and the time. "
               "\"N of M names known\": the Recorder counted more players than the journal named (it was "
               "installed later, or the party changed).")
# What each addon keeps about other players and groups (`players` with groups).
GROUP_SOURCES = {
    "ForeverDB_Helper journal (wow-player, optional)": "each groupmate's name, level, class, race and role, "
                                                       "once a day; the zone or dungeon",
    "Polestar update.probed (wow-player)": "names, last seen, zone; no class or level, never group members",
    "Polestar_Recorder group records": "party size and raid flag, no names",
    "Polestar_Recorder encounter records": "boss fights and wipes, no names",
    "Polestar instanceRuns (wow-dungeon)": "dungeon, level, time, XP; no party",
    "DungeonMaster": "the last run's kills only",
    "Details": "no saved segments",
}


# ----------------------------------------------------------------- overview


def route_levels(ask: Ask) -> list[dict]:
    """Each level's split and the route planner's time for it, from the
    semantic layer (played_s, planned_s by level and partial): the level in
    play has no split (partial NULL), a whole split partial 0."""
    return [{"level": int(r["level"]), "partial": r["partial"], "seconds": None if r["played_s"] is None else int(
        r["played_s"]), "route_s": None if not r["planned_s"] else round(r["planned_s"])}
        for r in ask(metrics=["played_s", "planned_s"], group_by=["level", "partial"])
        if r["level"] is not None]


def on_track(ask: Ask, header: dict) -> dict:
    """Pace against Polestar's route clock and against the player's own
    previous levels, plus the time to the next level. The split times, the
    route planner's and their ratio are the semantic layer's (played_s,
    planned_s, vs_route: whole splits with a planned time on both sides)."""
    levels = route_levels(ask)
    done = [lv for lv in levels if lv["partial"] == 0]
    # The last 10 finished levels, played next to the route (the Overview's
    # chart); the pace is the last 5 of them.
    recent = [lv for lv in done[-10:] if lv["route_s"]]
    vs_route = [r for r in recent if r["level"] in {lv["level"] for lv in done[-5:]}]
    out: dict = {"claims": [], "per_level": [{k: r[k] for k in ("level", "seconds", "route_s")} for r in recent]}
    if vs_route:
        pace = ask(metrics=["vs_route", "played_s", "planned_s"],
                   where=[{"dimension": "level", "op": "in", "values": [r["level"] for r in vs_route]}])[0]
        played, planned, ratio = int(pace["played_s"]), round(pace["planned_s"]), pace["vs_route"]
        out.update(route_ratio=round(ratio, 2), route_levels=[r["level"] for r in vs_route],
                   played_s=played, route_s=planned)
        last = (f"The last {len(vs_route)} levels (levels {vs_route[0]['level']}-{vs_route[-1]['level']})"
                if len(vs_route) > 1 else f"Level {vs_route[0]['level']}")
        out["claims"].append(claim(
            f"{last} took {dur(played)}; Polestar's route plans "
            f"{dur(planned)} for {'them' if len(vs_route) > 1 else 'it'} ({ratio_text(ratio)}).", "#levels",
            "wow-level splits vs data/route-reference.json"))
    own = [lv["seconds"] for lv in done[:-1][-5:]]
    if done and own:
        med = sorted(own)[len(own) // 2]
        last = done[-1]
        out.update(last_level=last["level"], last_s=last["seconds"], own_median_s=med,
                   own_ratio=round(last["seconds"] / med, 2) if med else None)
        word = "faster than" if last["seconds"] < med * 0.9 else "slower than" if last["seconds"] > med * 1.1 \
            else "about the same as"
        out["claims"].append(claim(
            f"Level {last['level']} took {dur(last['seconds'])}, {word} your median of the 5 before "
            f"({dur(med)}).", "#levels", "wow-level splits", kind="slow_level",
            detail=f"{dur(last['seconds'])}, against a median of {dur(med)} over the 5 levels before.",
            title=f"Level {last['level']} went {word.split()[0]} than your own pace" if word != "about the same as"
            else f"Level {last['level']} went at your usual pace"))
    cur = next((lv for lv in levels if lv["partial"] == 1 and lv["level"] == header.get("level")), None)
    in_play = next((lv for lv in levels if lv["level"] == header.get("level")), None)
    if header.get("eta_s"):
        out["eta_s"] = header["eta_s"]
        out["claims"].append(claim(
            f"Level {(header.get('level') or 0) + 1} in about {dur(header['eta_s'])} of play at your recent "
            f"pace ({_n(header.get('xp_per_hour'))} XP an hour).", "#levels", "XP left / XP an hour, last 3 levels",
            kind="eta"))
    t_in = header.get("time_in_level_s")
    # No level anywhere (no snapshot, no ding, no split): nothing to say
    # about the level in play.
    if t_in and header.get("level") and header.get("xp_max") and header.get("xp") is not None:
        plan = (in_play or {}).get("route_s")
        out["level_played_s"] = t_in
        share = header["xp"] / header["xp_max"]
        way = f"{share:.0%} of the way" if share >= 0.01 else "under 1% of the way"
        tail = f"; the route plans {dur(plan)} for the whole level." if plan else "."
        out["claims"].append(claim(
            f"{dur(t_in)} played at level {header.get('level')} so far, {way}{tail}",
            "#levels", "Polestar_Recorder logged-in time since the ding"))
    if cur and cur["route_s"] and header.get("xp_pct") is not None:
        out["current_route_s"] = cur["route_s"]
    ratio = out.get("route_ratio")
    out["verdict"] = (None if ratio is None else "on track" if ratio <= 1.5 else "behind the route"
                      if ratio <= 3 else "well behind the route")
    return out


def guide_following(steps: dict, ref, rows: list[dict]) -> dict:
    """Steps done in order, skipped or jumped, and the time spent on steps
    that took far longer than the route plans for them."""
    total = steps.get("total") or 0
    skipped = steps.get("skipped") or []
    jumped = sum(len(s["steps"]) for s in skipped if s["how"] == "jumped")
    clicked = sum(len(s["steps"]) for s in skipped if s["how"] == "skipped")
    over, over_s = [], 0
    if ref and ref.available:
        for a in steps.get("slowest") or []:
            plan = ref.step_seconds(a["guide"], a["step"])
            if plan and a["secs"] > plan * SLOW_STEP and a["secs"] - plan > 300:
                over.append({**a, "route_s": plan})
                over_s += a["secs"] - plan
    claims = [] if not total else [claim(
        f"{total} guide steps recorded; {clicked} skipped by hand, {jumped} passed over (the guide jumped ahead).",
        "#guide", "Polestar_Recorder step and skip records", kind="guide_steps",
        title=f"{clicked + jumped} of {total} guide steps skipped or passed over")]
    if over:
        worst = over[0]
        what = f"step {worst['step']}" + (f" ({worst['title']})" if worst.get("title") else "")
        claims.append(claim(
            f"{len(over)} steps took over {SLOW_STEP:.0f}× their planned time, {dur(over_s)} more than planned. "
            f"The worst: {what} of {guide_title(worst['guide'], ref)}, {dur(worst['secs'])} against "
            f"{dur(worst['route_s'])}.", "#guide", "step durations vs the route's per-step plan", kind="slow_steps",
            title=f"{len(over)} guide steps took over {SLOW_STEP:.0f}× their plan",
            detail=f"{dur(over_s)} more than planned in all. The worst: {what} of {guide_title(worst['guide'], ref)}, "
                   f"{dur(worst['secs'])} against {dur(worst['route_s'])}.",
            action=f"Once a step runs past {SLOW_STEP:.0f}× its plan, look at what holds it up: "
                   "skip it only if no later step needs it.",
            zone=zone_ref(worst.get("zone"))))
    last = rows[-1] if rows else None
    cur = None
    if last:
        g, st = last.get("guide"), opt_num(last.get("step"))
        picked = last.get("reason") == "pick" or not isinstance(st, int)
        cur = {"guide": g, "step": None if picked else st, "t": last.get("_time"), "picked": picked,
               "guide_title": guide_title(g, ref),
               "title": None if picked or not (ref and ref.available) else ref.step_title(g, st)}
    return {"total": total, "skipped": clicked, "jumped": jumped, "in_order": max(0, total - clicked - jumped),
            "over_plan": over[:5], "over_plan_s": over_s,
            "current": cur, "claims": claims}


def short_guide(g: str | None) -> str:
    return (g or "the guide").rsplit("/", 1)[-1].split("-", 1)[-1].replace("-", " ").title()


def guide_title(guide: str | None, ref=None) -> str:
    """The guide's title from the route reference, else its id made
    readable (forever/lookup/pickup/stormwind-stockade: Pickup: Stormwind
    Stockade)."""
    t = ref.title(guide) if ref is not None and getattr(ref, "available", False) else None
    if t:
        return t
    parts = [x for x in (guide or "guide").split("/") if x not in ("forever", "alliance", "horde", "lookup")]
    parts = [re.sub(r"^\d+-", "", x) for x in parts if not re.fullmatch(r"\d+-\d+", x)]
    words = [x.replace("-", " ").title() for x in parts if x]
    return ": ".join(words[-2:]) if len(words) > 1 else (words[0] if words else "the guide")


def overview(data: dict, advice: dict | None, ds: list[dict], ref, who: dict, steps_rows: list[dict],
             sessions: list[dict], ask: Ask) -> dict:
    """The Overview's lines. `ask`: the semantic layer on the character's
    data (wowstats.analysis.pages.asker), for the pace and the death counts."""
    hd = data.get("header") or {}
    track = on_track(ask, hd)
    follow = guide_following(data.get("steps") or {}, ref, steps_rows)
    now_claims = []
    if hd.get("zone"):
        at = f" at level {hd['level']}" if hd.get("level") else ""
        now_claims.append(claim(f"In {hd['zone']}{at}.", "#zones", "the newest snapshot",
                                zone=zone_ref(hd["zone"])))
    cur = follow.get("current")
    if cur and cur["picked"]:
        now_claims.append(claim(f"Picked guide {cur['guide_title']}.", "#guide", "the last step record"))
    elif cur:
        now_claims.append(claim(f"On step {cur['step']} of {cur['guide_title']}"
                                + (f": {cur['title']}." if cur.get("title") else "."), "#guide",
                                "the last step record"))
    if sessions:
        s = sessions[0]
        now_claims.append(claim(f"Last session: {dur(s['seconds'])}, {s['when']}.", "#timeline",
                                "recorder session and activity records"))
    up_claims = [claim(f"{u['name']}: {gearmod.gain_words(u)}, {_where(s)}.", f"#up-{u.get('slot')}",
                       "gear advice (ForeverDB.net's weights) on your worn gear")
                 for u, s in in_reach(advice, hd.get("level"))[:3]]
    death_claims = []
    if ds:
        last = ds[-1]
        k = last.get("killer")
        n = death_counts(ask)["n"]
        death_claims.append(claim(
            (f"{n} deaths recorded. " if n else "") + f"The last, in {last.get('zone') or 'an unknown zone'}"
            + (f": likely {k['name']} (level {k.get('level') or '?'})" if k else "") + ".",
            "#deaths", "Polestar_Recorder death record and the fight before it"))
    return {"on_track": track, "now": now_claims, "guide": follow, "upgrades": up_claims, "deaths": death_claims,
            "levels_seen": len(data.get("levels") or [])}


HELD_WHERE = {"bags": "in your bags: equip it", "bank": "in your bank: take it out at a banker and equip it"}


def _pick_text(u: dict, s: dict) -> str:
    gain = gearmod.gain_words(u)
    w = u.get("with") or {}
    if w.get("kind") == "get":  # a weapon setup: both hands, the gain is the pair's
        other = next(iter(w.get("sources") or []), {})
        gain += f", worn with {w.get('name')} ({(w.get('slot_name') or '').lower()}: {_where(other)})"
    if s.get("kind") == "bags":
        return f"Equip {u['name']} from your bags ({gain})."
    if s.get("kind") == "bank":
        return f"Take {u['name']} out of your bank at a banker and equip it ({gain})."
    return f"Pick up {u['name']} ({gain}): {_where(s)}."


def _where(s: dict) -> str:
    if s.get("kind") in HELD_WHERE:
        return HELD_WHERE[s["kind"]]
    if s.get("kind") == "quest":
        return f"quest {s.get('name')}" + (f" ({s['zone']})" if s.get("zone") else "")
    basis = f" ({s['basis']})" if s.get("basis") else ""
    if s.get("kind") == "drop":
        chance = f", {gearmod.chance_words(s)}" if s.get("chance_pct") else ""
        zone = f" in {s['zone']}" if s.get("zone") else ""
        return f"drops from {s.get('boss') or s.get('name') or 'creatures'}{zone}{chance}{basis}"
    if s.get("kind") == "craft":
        return f"crafted ({s.get('profession') or 'a profession'}; {gearmod.craft_status(s)})"
    if s.get("kind") in ("vendor", "reputation", "pvp", "container"):
        return gearmod.source_text(s)
    return s.get("kind") or "source unknown"


# ------------------------------------------------------------------ sessions


def sessions(q: Q, cf: str) -> list[dict]:
    """Play sessions, newest first: the journal's and the semantic layer's
    rule (wowstats.stores.split_sessions)."""
    spans = split_sessions(q(f"{rec(cf)} {SESSION_BUCKETS}"))
    out = [(datetime.fromtimestamp(a, tz=UTC), datetime.fromtimestamp(b, tz=UTC)) for a, b in spans]
    return [{"start": iso(a), "end": iso(b), "seconds": int((b - a).total_seconds()),
             "when": a.strftime("%Y-%m-%d %H:%M UTC")} for a, b in reversed(out)]


# ------------------------------------------------------------- the 2 questions


IMPLEMENTED_SANITY = [
    "death clusters: the same zone, the same creature, or creatures 3+ levels above you (likely killer from "
    "Polestar_Recorder)",
    "guide steps far over the route's plan (3x and 5+ minutes)",
    "guide steps skipped or passed over",
    "upgrades you could wear now and haven't picked up",
    "profession capped at its trainer tier while the next tier is open",
    "unspent talent points (only once Battle.net's API knows the character)",
    "enchantable slots with no enchant (from the item links, shipper 0.2.2 on)",
    "levels much slower than your own usual pace",
]
UNSUPPORTED_SANITY = [
    "bags full and vendor trash not sold: the shipper sends which items the bags hold (Syndicator), not how "
    "full they are or what each sells for",
    "what killed you in a dungeon fight blow by blow: no combat log, only the recorder's likely killer",
]
IMPLEMENTED_NEXT = [
    "upgrades in reach now, best first, with where to get them",
    "dungeons at your level that drop upgrades for you",
    "profession trainer visits due",
    "rested XP banked",
    "time to the next level and to the end of the current guide",
]
# Nothing today: the auction house's line went once the gear advice priced
# what's on sale (the MCP's gear `buy_now`, the market). The slices check
# that no line here names what a registered tool computes
# (tests/slices/test_slices.py).
UNSUPPORTED_NEXT: list[str] = []


def _skills(q: Q, cf: str) -> dict[int, dict]:
    rows = _sorted(q(f"{rec(cf)} kind:=skill | fields _time, skill_id, rank, max_rank, source_id"))
    out: dict[int, dict] = {}
    for r in rows:
        sid = opt_num(r.get("skill_id"))
        if sid is not None:
            out[int(sid)] = {"rank": int(num(r.get("rank"))), "max_rank": int(num(r.get("max_rank"))),
                             "t": r.get("_time")}
    return out


def _trainer_text(ts: list[dict]) -> str:
    one = ts[0]
    at = f" ({one['x']:.1f}, {one['y']:.1f})" if one.get("x") is not None else ""
    more = f", or {len(ts) - 1} other trainer{'s' if len(ts) > 2 else ''}" if len(ts) > 1 else ""
    return f"{one['name']}, {one['zone']['name']}{at}{more}"


def next_tiers() -> dict[int, tuple[int, str, int | None, int]]:
    """{cap: (rank, name, level, skill)} of the rank each cap leads to, from
    the KB's profession.tiers; {} when the KB has no such rule (no move)."""
    r = kb.static_rule("profession.tiers")
    ts = sorted((r or {}).get("value") or [], key=lambda t: t["cap"])
    return {a["cap"]: (b["rank"], b["name"], b.get("need_level"), b.get("need_skill") or 0)
            for a, b in zip(ts, ts[1:], strict=False)}


def training_price(ref: dict | None, rank_no: int) -> tuple[int | None, str | None]:
    """(copper, how to read it) for a rank: the guide's own Forever price
    first, then the KB's profession.training_cost, then the guide's Classic
    Era fallback."""
    if ref and ref.get("price_copper") and "Classic" not in str(ref.get("price_scope") or ""):
        return ref["price_copper"], PRICE_NOTE
    cost = ((kb.static_rule("profession.training_cost") or {}).get("value") or {}).get(str(rank_no))
    if cost:
        return int(cost), PRICE_NOTE
    if ref and ref.get("price_copper"):
        return ref["price_copper"], PRICE_NOTE
    return None, None


def profession_claims(level: int | None, gear_profs: list[dict], skills: dict[int, dict],
                      header: dict | None = None) -> list[dict]:
    """A profession at its cap: the next rank, who trains it for the
    character's side and where, what it needs (each requirement met or not)
    and what it costs, with whose price it is. The cap is the game's skill
    list (DataStore_Crafts) or the Recorder's record, else the trainer tier
    the rank sits under (wowstats.profcaps). Only the game's list says what
    the character has now: the Recorder misses a training it didn't see and
    a tier cap is a floor, so those moves say "if" and what they last saw."""
    out = []
    faction = (header or {}).get("faction")
    profs = {int(p["skill_id"]): p for p in gear_profs or [] if p.get("skill_id")}
    tiers = next_tiers()
    for sid, p in profs.items():
        rec = (skills or {}).get(sid)
        rank, cap, src = profcaps.cap_of(opt_num(p.get("rank")), rec, p)
        stale = src != "game"
        tier = tiers.get(cap)
        if not tier or level is None or rank < cap - 5:
            continue
        rank_no, name, need_level, need_rank = tier
        ref = trainers.rank(sid, rank_no)
        if ref and ref.get("level") is not None:
            need_level = ref["level"]
        prof = p.get("name") or PRIMARY.get(sid) or SECONDARY.get(sid) or f"skill {sid}"
        ts = trainers.for_side((ref or {}).get("trainers") or [], faction)
        cost, scope = training_price(ref, rank_no)
        level_ok, skill_ok = level >= (need_level or 0), rank >= need_rank
        needs = (f"needs level {need_level} ({'met' if level_ok else f'you are {level}'})" if need_level
                 else "needs no character level")
        where = _trainer_text(ts) if ts else "trainer location not in our data"
        seen = parse_t((rec or {}).get("t")) if src == "recorder" else None
        seen = seen if seen and seen.year > 2000 else None
        head = (f"Capped at {cap}: skill-ups are wasted until you train {name} {prof}." if not stale
                else f"{prof} {rank}/{cap} when Polestar Recorder last saw it ({seen:%-d %b}): if you haven't "
                     f"trained {name} since, skill-ups stop at {cap} until you do." if seen
                else f"{prof} {rank}/{cap} if you haven't trained {name} yet: skill-ups stop at {cap} until you do.")
        text = (f"{head} {where}; {needs}"
                + (f"; {_coins(cost)} ({scope})" if cost else "; price not in our data") + ".")
        refresh = (f"Trained {name} already? With Altoholic installed the cap is read at each login; else it "
                   f"shows at your next {prof} skill-up.") if stale else None
        out.append(claim(text + (f" {refresh}" if refresh else ""), "#gear",
                         "the rank in wow-gear (Polestar), the cap from the game's skill list (DataStore_Crafts), "
                         "else the Recorder's skill records, else the trainer tier; ranks, gates and price: the "
                         "knowledge base's profession.tiers and profession.training_cost; trainers: "
                         "Polestar's profession guides (data/trainers-reference.json)",
                         kind="profession", title=f"Train {name} {prof}" + (", if not done yet" if stale else ""),
                         weight=3 if level_ok else 1.5, skill_id=sid, action=refresh, detail=head if stale else None,
                         skill={"skill_id": sid, "name": prof, "rank": rank, "max_rank": cap, "next": name,
                                "need_level": need_level, "level_ok": level_ok, "need_skill": need_rank,
                                "skill_ok": skill_ok, "cost_copper": cost, "cost_scope": scope, "trainers": ts,
                                "may_be_stale": stale,
                                "search_url": None if ts else trainers.search_url(f"{name} {prof} trainer")}))
    return out


def riding_claims(level: int | None, header: dict | None, skills: dict[int, dict]) -> list[dict]:
    """Riding, from RIDING_AHEAD levels before its tier: the race's trainer,
    the level, and the price against the gold the character holds. Riding's
    entry in `skills` is wowstats.riding.merge's (the spellbook, the
    Recorder's learn and skill records): no read saw a rank. That proves
    nothing past the tier's level (the Recorder misses a training it didn't
    see), so the move then says "if not trained yet" and what was read when."""
    hd = header or {}
    rd = (skills or {}).get(RIDING_SKILL) or {}
    if level is None or rd.get("rank"):
        return []
    r = trainers.riding(hd.get("race"), hd.get("faction"))
    rule = kb.static_rule("riding.apprentice")
    if not r or not rule:
        return []
    tier = {"level": rule["value"]["level"], "price_copper": rule["value"]["training_copper"]}
    if level < tier["level"] - RIDING_AHEAD:
        return []
    cost, money = tier["price_copper"], opt_num(hd.get("money_copper"))
    t = r["trainer"]
    lv_ok = level >= tier["level"]
    # Counted as a player reads the bag: silver down, and what's left to put
    # by is the price less that, so the two always add up to the price.
    have = None if money is None else int(money) // 100 * 100
    gold = ("" if have is None else f"; you have {_coins(have)}"
            + ("" if have >= cost else f"; {_coins(cost - have)} to go"))
    # Under the tier's level it can't have been trained: no hedge needed.
    stale = lv_ok
    book = parse_t(rd["book_t"]) if rd.get("book_t") else None
    book = book if book and book.year > 2000 else None
    seen = (f"your spellbook as Altoholic last read it ({book:%-d %b}) nor in Polestar Recorder's records" if book
            else "Polestar Recorder's records")
    head = (f"No riding in {seen}: if you haven't trained Apprentice Riding yet, it's trained from level "
            f"{tier['level']}.")
    where = f"{t['name']}, {t['zone']['name']} ({t['x']:.1f}, {t['y']:.1f}); {_coins(cost)} ({PRICE_NOTE}){gold}."
    text = f"{head} {where[0].upper()}{where[1:]}" if stale else f"Riding at level {tier['level']}" + (
        "" if lv_ok else f" (you are {level})") + f": {where}"
    refresh = ("Trained it already? With Altoholic installed your spellbook is read at each login; else Polestar "
               "Recorder sees it at your next riding skill-up.") if stale else None
    return [claim(text + (f" {refresh}" if refresh else ""), "#gear",
                  "Polestar's riding reminder (QoL/Riding.lua): trainer per race; level and cost: the "
                  f"knowledge base's riding.apprentice ({rule['source_url']}, as of {rule['last_verified']}); "
                  "riding known: Altoholic's spellbook (DataStore_Spells), else Polestar Recorder's learn and "
                  "skill records",
                  kind="riding", title=f"Riding at level {tier['level']}" + (", if not trained yet" if stale else ""),
                  weight=2.5 if lv_ok else 1.8, action=refresh, detail=head if stale else None,
                  skill={"name": "Riding", "next": "Apprentice Riding", "need_level": tier["level"],
                         "level_ok": lv_ok, "cost_copper": cost, "cost_scope": PRICE_NOTE,
                         "money_copper": have, "trainers": [t], "may_be_stale": stale})]


def _coins(copper: int) -> str:
    """Every coin, with its unit: 3 gold 40 silver. The caller rounds."""
    g, rest = divmod(int(copper), 10000)
    s, c = divmod(rest, 100)
    return " ".join(x for x in (f"{g} gold" if g else "", f"{s} silver" if s else "",
                                f"{c} copper" if c else "") if x) or "0 copper"


# The slots Enchanting reaches in Forever: the equipped-item rules of its
# ENCHANT_ITEM spells (SkillLine 333 -> SpellEffect 53 -> SpellEquippedItems,
# wago.tools wowf 1.60.1.70959): boots, bracers, chest and robes, cloaks,
# gloves, necklaces (new in Forever), shields and held off-hands, weapons.
# Ranged weapons, rings, trinkets, head, legs, shoulders and waist: no
# Enchanting recipe. Character slot ids (the shipper's `slot`).
ENCHANTABLE = {2: "Neck", 5: "Chest", 8: "Feet", 9: "Wrist", 10: "Hands", 15: "Back", 16: "Main hand",
               17: "Off hand"}


def enchant_claims(items: list[dict] | None, profs: list[dict] | None) -> list[dict]:
    """Enchantable slots holding an item without an enchant. Only from a
    sheet whose links said so (enchant_id 0); an older shipper's (no
    enchant_id at all) proves nothing. Which recipes the character knows
    isn't recorded anywhere we read, and Forever's SkillLineAbility gives
    every Enchanting recipe rank 1, so no recipe is named."""
    items = [i for i in items or [] if i.get("slot") in ENCHANTABLE and i.get("item_id")]
    if not items or any(i.get("enchant_id") is None for i in items):
        return []
    bare = [i for i in items if not i.get("enchant_id")]
    if not bare:
        return []
    ench = next((p for p in profs or [] if p.get("skill_id") == 333 and p.get("rank")), None)
    names = ", ".join(f"{ENCHANTABLE[i['slot']]} ({i.get('name') or 'item ' + str(i['item_id'])})" for i in bare)
    text = f"No enchant on {len(bare)} slot{'s' if len(bare) > 1 else ''} that can take one: {names}."
    if ench:
        text += f" You're an Enchanter ({ench['rank']}): these are yours to do."
    return [claim(text, "#gear", "the item links Syndicator saves (enchant id), Enchanting's slots from Forever's "
                  "DB2", kind="enchant", weight=2, item_id=bare[0]["item_id"],
                  title=f"No enchant on {len(bare)} slot{'s' if len(bare) > 1 else ''} that can take one",
                  items=[{**{k: i.get(k) for k in ("item_id", "name", "quality", "slot")},
                          "slot_name": ENCHANTABLE[i["slot"]], "url": wowhead_url("item", i["item_id"])}
                         for i in bare])]


def _upgrade_claim(u: dict, s: dict, text: str, cite: str, level: int | None = None) -> dict:
    share = u.get("gain_share_pct") or 0
    src = source_ref(s, level)
    if src["blocker"]:
        text = text.rstrip(".") + f" ({src['blocker']})."
    # One already in the bags or the bank costs nothing to take: it outweighs a trip.
    return claim(text, f"#up-{u.get('slot')}", cite, kind="upgrade", title=u["name"], item_id=u["item_id"],
                 weight=(3 if share >= 10 else 2) - (0.5 if src["blocker"] else 0)
                 + (1 if s.get("kind") in HELD_WHERE else 0), item=item_ref(u), source=src)


def _per_clear(pairs: list[tuple[dict, float | None]]) -> float:
    """The gear points one clear is expected to bring: per slot, the
    chance-weighted best of the items that can drop for it (a better one
    dropping makes a worse one moot), as wowstats.plan.estimate.expected_best."""
    by_slot: dict = defaultdict(list)
    for u, pct in pairs:
        if pct:
            # The ranking's value (wowstats.gearfit): another role's piece counts less.
            by_slot[u.get("slot")].append((u.get("value", u.get("gain")) or 0, min(100.0, pct) / 100))
    total = 0.0
    for its in by_slot.values():
        miss = 1.0
        for gain, p in sorted(its, key=lambda x: -x[0]):
            total += gain * p * miss
            miss *= 1 - p
    return total


def dungeon_claims(level: int | None, reach: list[tuple[dict, dict]]) -> list[dict]:
    """Dungeons at the character's level that drop upgrades it could wear
    now: the two with the most to gain per clear (drop chances counted).
    Polestar's list comes first (wowstats.plan.vsguide: from a dungeon's
    floor, where players start it, to its top); one Polestar lists only from
    a later level, up to REACH_LEVELS later, comes after, saying from which
    level. Gear alone never puts a dungeon ahead of Polestar's (owner,
    2026-11-19: Scarlet Monastery's Library at 32, Polestar's from 36)."""
    by: dict[str, list] = defaultdict(list)
    lv_of: dict[str, list] = {}
    start_of: dict[str, int] = {}
    for u, _ in reach:
        for s in u.get("sources") or []:
            lv = s.get("levels") or []
            if s.get("kind") != "drop" or not s.get("dungeon") or not s.get("zone") or len(lv) != 2:
                continue
            start = max(lv[0], math.ceil((s.get("floor") or lv[0]) - 1e-9))
            if level is not None and not start - REACH_LEVELS <= level <= lv[1]:
                continue
            by[s["zone"]].append((u, s.get("chance_pct")))
            lv_of[s["zone"]] = lv
            start_of[s["zone"]] = start

    def later(z: str) -> bool:
        return level is not None and start_of[z] > level

    def rank(kv):
        z, pairs = kv
        return (later(z), -_per_clear(pairs), -sum(u.get("value", u["gain"]) or 0 for u, _ in pairs))

    out = []
    for z, pairs in sorted(by.items(), key=rank)[:2]:
        lo, hi = lv_of[z]
        us = list({id(u): u for u, _ in pairs}.values())
        n = len(us)
        block = None
        if later(z):
            block = f"it starts at level {lo}" if lo > level else f"Polestar lists it from level {start_of[z]}"
        out.append(claim(f"{z} (levels {lo}-{hi}) drops {n} upgrade{'s' if n > 1 else ''} for you ("
                         + ", ".join(f"{u['name']} ({gearmod.gain_words(u)})" for u in us[:3]) + ")"
                         + (f"; {block}" if block else "") + ".",
                         "#advice", "gear advice drop sources", kind="dungeon", title=z,
                         weight=1.5 if block else 2.5,
                         zone=zone_ref(z, lv_of[z]), items=[item_ref(u) for u in us], blocker=block))
    return out


def next_moves(level: int | None, advice: dict | None, header: dict, track: dict, follow: dict,
               gear_profs: list[dict], skills: dict[int, dict]) -> dict:
    """Besides following Polestar: what pays now."""
    reach = in_reach(advice, level)
    # The best for each slot: two pairs of trousers for the same legs are one
    # move, not two.
    # Held ones first: equipping one is a move that costs nothing.
    best: dict = {}
    for u, s in sorted(reach, key=lambda us: us[1].get("kind") not in HELD_WHERE):
        best.setdefault(u.get("slot"), (u, s))
    moves = [_upgrade_claim(u, s, _pick_text(u, s), "gear advice (ForeverDB.net's weights)", level)
             for u, s in list(best.values())[:3]]
    moves += dungeon_claims(level, reach)
    moves += profession_claims(level, gear_profs, skills, header)
    moves += riding_claims(level, header, skills)
    rested = opt_num(header.get("rested_xp"))
    if rested:
        of = f" ({rested / header['xp_max']:.2f} of level {level})" if header.get("xp_max") else ""
        moves.append(claim(f"{_n(rested)} rested XP banked{of}: kills give double XP until it's spent.", "#levels",
                           "the recorder's XP record", kind="rested"))
    if track.get("eta_s"):
        moves.append(claim(f"Level {(level or 0) + 1} in about {dur(track['eta_s'])} at your pace.", "#levels",
                           "XP left / XP an hour", kind="eta"))
    return {"moves": moves, "checks": IMPLEMENTED_NEXT, "not_possible_yet": UNSUPPORTED_NEXT}


def death_steps(c: dict, crowd: int, level: int | None) -> tuple[list[str], list[str], int | None]:
    """What a group of deaths had in common and what to change, from counts
    only (no creature is named: /zogzog/ shows the zone's): creatures
    LEVEL_GAP+ levels above (come back at the highest one's level, while the
    character is under it), more than one attacker in most fights (pull one
    at a time). `c`: the group's death_counts; `crowd`: how many of its
    fights had several attackers (the fight before each death, deaths()).
    ([why], [next steps], come-back level)."""
    back = c["back"] if c["above"] else None
    why, todo = [], []
    if c["above"]:
        why.append(f"{c['above']} to creatures {LEVEL_GAP}+ levels above you (up to {c['gap']} above)")
        if back and (level is None or level < back):
            todo.append(f"come back at level {back}" + (f" (you are {level})" if level else ""))
    if crowd and crowd * 2 >= c["n"]:
        why.append(f"{crowd} with more than one attacker")
        todo.append("pull one at a time")
    return why, todo, back


def crowded(ds: list[dict]) -> int:
    """Deaths whose fight had more than one attacker (the unit records
    before each death: no metric holds them)."""
    return sum(1 for d in ds if (d.get("attackers") or 0) >= 2)


def deaths_zone_claim(zone: str, ds: list[dict], level: int | None, ask: Ask) -> dict | None:
    """Deaths that pile up in one zone, with what they had in common and
    what to change (death_steps). None when there is nothing to change any
    more (the character outgrew the zone and no fight was against several):
    a death count with nothing to do is not a move. The counts are the
    layer's; `ds` are the zone's deaths with their fights."""
    here = {"dimension": "zone", "op": "=", "value": zone}
    c = death_counts(ask, here)
    n, crowd = c["n"], crowded(ds)
    lvls = sorted(int(r["level"]) for r in ask(metrics=["deaths"], group_by=["level"], where=[DEATH, here])
                  if r["level"])
    why, todo, back = death_steps(c, crowd, level)
    if not todo:
        return None
    at = (f" at level {min(lvls)}" + (f"-{max(lvls)}" if max(lvls) != min(lvls) else "")) if lvls else ""
    text = f"{n} deaths in {zone}{at}: {'; '.join(why)}. Next time: {' and '.join(todo)}."
    return claim(text, "#deaths", "death records by zone, the likely killer's level and the attackers in the "
                                  "fight (counts only)", kind="deaths_zone", title=f"{n} deaths in {zone}",
                 detail=f"{'; '.join(why).capitalize()}.", action=" and ".join(todo).capitalize() + ".",
                 weight=1 + n / 3, zone=zone_ref(zone),
                 deaths={"n": n, "levels": [min(lvls), max(lvls)] if lvls else None,
                         "above": c["above"], "crowd": crowd, "come_back_at": back})


def sanity_check(level: int | None, ds: list[dict], follow: dict, track: dict, advice: dict | None,
                 gear_profs: list[dict], skills: dict[int, dict], talents: list[dict] | None,
                 gear_items: list[dict] | None = None, header: dict | None = None, *, ask: Ask) -> dict:
    """What looks like a mistake in the recorded play, worst first. The
    death counts are the semantic layer's (`ask`, death_clusters)."""
    found = []
    for c in death_clusters(ask, ds):
        if c["by"] == "zone":
            z = deaths_zone_claim(c["zone"], [d for d in ds if d.get("zone") == c["zone"]], level, ask)
            if z:
                found.append(z)
        elif c["by"] == "creature":
            mine = [d for d in ds if (d.get("killer") or {}).get("npc_id") == c["npc_id"]]
            why, todo, _ = death_steps(death_counts(ask, {"dimension": "npc_id", "op": "=", "value": c["npc_id"]}),
                                       crowded(mine), level)
            found.append(claim(f"{c['n']} deaths to {c['name']} in {c.get('zone') or '?'} (likely killer)"
                               + (f": next time, {' and '.join(todo)}." if todo else "."),
                               "#deaths", "death records + the fight before each", kind="deaths_creature",
                               title=f"{c['n']} deaths to {c['name']}", weight=1.5 + c["n"] / 3,
                               detail=f"{'; '.join(why).capitalize()}." if why else None,
                               action=" and ".join(todo).capitalize() + "." if todo else None))
        else:
            deaths = f"{c['n']} death{'s' if c['n'] > 1 else ''}"
            high = [d for d in ds if (d.get("level_gap") or 0) >= LEVEL_GAP]
            _, todo, _ = death_steps(death_counts(ask), crowded(high), level)
            todo = [x for x in todo if x.startswith("come back")]
            found.append(claim(f"{deaths} to creatures {LEVEL_GAP}+ levels above you (up to {c['gap']})"
                               + (f": {todo[0]} for that zone." if todo else "."),
                               "#deaths", "likely killer's level vs yours", kind="deaths_level_gap",
                               title=f"{deaths} to creatures {LEVEL_GAP}+ levels above you",
                               weight=1.5 + c["n"] / 3,
                               action=f"Check a creature's level before you pull; {todo[0]}." if todo else None))
    for c in (follow.get("claims") or [])[1:]:
        found.append({**c, "weight": 2})
    if follow.get("skipped", 0) + follow.get("jumped", 0) >= 10 and follow.get("claims"):
        found.append({**follow["claims"][0], "weight": 1})
    big = [(u, s) for u, s in in_reach(advice, level) if (u.get("gain_share_pct") or 0) >= 10]
    if big:
        u, s = big[0]
        found.append(_upgrade_claim(u, s, f"You could wear {u['name']} now: {gearmod.gain_words(u)} "
                                          f"({_where(s)}).",
                                    "gear advice", level))
    found += profession_claims(level, gear_profs, skills, header)
    if talents and level and level >= 10:
        spent = sum(int(t.get("points") or 0) for t in talents)
        if spent < level - 9:
            n = level - 9 - spent
            found.append(claim(f"{n} talent point{'s' if n > 1 else ''} unspent: spend {'them' if n > 1 else 'it'} "
                               f"in the talent pane (N), anywhere.",
                               "#talents", "Battle.net's talents (its profile read, every 2 hours)",
                               kind="talents", title=f"{n} talent point{'s' if n > 1 else ''} unspent", weight=4))
    found += enchant_claims(gear_items, gear_profs)
    if (track.get("own_ratio") or 0) >= 1.5:
        found.append({**track["claims"][1 if track.get("route_ratio") is not None else 0], "weight": 1})
    found.sort(key=lambda c: -c.get("weight", 0))
    return {"findings": found, "checks": IMPLEMENTED_SANITY, "not_possible_yet": UNSUPPORTED_SANITY}


# What the Overview leaves to its tiles (the ETA, rested XP, guide steps in
# order) rather than repeating as a move, and what it leaves out for having
# nothing to do about it (a slow level against the character's own pace:
# the MCP's sanity check keeps it).
TILE_KINDS = frozenset({"eta", "rested", "guide_steps"})
NO_ACTION = frozenset({"slow_level"})
DEATH_KINDS = frozenset({"deaths_zone", "deaths_creature", "deaths_level_gap"})


def overview_moves(nm: dict, sc: dict) -> list[dict]:
    """The Overview's one list: the next moves and the sanity check's
    findings together, each thing once, heaviest first, and the first of
    each kind ahead of a second of any kind, so the few lines on show
    differ (an upgrade, a dungeon, a profession) rather than repeat.

    One item, one move: an upgrade that a listed dungeon's move already
    names (its items carry the slot and the gain) goes into that move, which
    takes the upgrade's weight when it is heavier."""
    allc = [*nm.get("moves", []), *sc.get("findings", [])]
    in_dungeon: dict = {}
    for c in allc:
        if c.get("kind") == "dungeon":
            for i in c.get("items") or []:
                in_dungeon.setdefault(i.get("item_id"), (c.get("zone") or {}).get("name"))
    lift: dict = defaultdict(float)
    kept = []
    for c in allc:
        if c.get("kind") == "upgrade" and c.get("item_id") in in_dungeon:
            z = in_dungeon[c["item_id"]]
            lift[z] = max(lift[z], c.get("weight", 1))
            continue
        kept.append(c)
    kept = [{**c, "weight": max(c.get("weight", 1), lift[(c.get("zone") or {}).get("name")])}
            if c.get("kind") == "dungeon" and lift.get((c.get("zone") or {}).get("name")) else c for c in kept]
    seen, ranked = set(), []
    for c in sorted(kept, key=lambda c: -c.get("weight", 1)):
        k = c.get("kind")
        key = (k, c.get("item_id"), c.get("skill_id"), (c.get("zone") or {}).get("name"))
        # A death line with nothing left to change (outgrown) stays in the
        # sanity check (the MCP) and out of the moves.
        if not k or k in TILE_KINDS or k in NO_ACTION or key in seen or (k in DEATH_KINDS and not c.get("action")):
            continue
        seen.add(key)
        ranked.append(c)
    kinds: set = set()
    first, rest = [], []
    for c in ranked:
        (rest if c["kind"] in kinds else first).append(c)
        kinds.add(c["kind"])
    return first + rest


# ----------------------------------------------------------------- timeline

LEVELING = re.compile(r"/\d+-\d+/")
TIMELINE_KINDS = ("level", "session", "dungeon", "death", "gear", "profession", "guide")


def timeline(data: dict, ds: list[dict], runs: list[dict], sess: list[dict], gear_rows: list[dict],
             skill_rows: list[dict], steps_rows: list[dict], ref=None) -> list[dict]:
    ev = []
    for p in data.get("climb") or []:
        ev.append({"t": p["t"], "type": "level", "text": f"Level {p['level']}", "link": "#levels"})
    for s in sess:
        ev.append({"t": s["start"], "type": "session", "text": f"Session, {dur(s['seconds'])}",
                   "seconds": s["seconds"], "link": "#timeline"})
    first = min((s["start"] for s in sess), default=None)
    if first is not None and any(p["t"] < first for p in data.get("climb") or []):
        # The Recorder switched on after some play: one marker where its
        # detail starts; the levels and dungeons before it stay.
        ev.append({"t": first, "type": "session", "text": "Recorder on: zones, quests and deaths from here",
                   "marker": "tracking", "link": "#timeline"})
    for r in runs:
        ev.append({"t": r["t"], "type": "dungeon", "text": f"{r['name']} at level {r['level']}, "
                   f"{dur(r['seconds'])}" + (f", {r['bosses']} bosses" if r.get("bosses") else ""),
                   "link": "#dungeons"})
    for d in ds:
        k = d.get("killer")
        ev.append({"t": d["t"], "type": "death", "text": f"Died in {d.get('zone') or '?'}"
                   + (f", likely to {k['name']}" if k else ""), "link": "#deaths"})
    prev = None
    prev_ench: dict = {}
    for g in sorted(gear_rows, key=lambda r: parse_t(r.get("_time"))):
        items = {str(i.get("slot")): i.get("item_id") for i in g.get("items") or []}
        if prev is not None:
            changed = [i for i in g.get("items") or [] if prev.get(str(i.get("slot"))) != i.get("item_id")]
            if changed:
                ev.append({"t": g["_time"], "type": "gear", "text": "Put on " + ", ".join(
                    i.get("name") or str(i.get("item_id")) for i in changed[:4]), "link": "#advice"})
        # An enchant added or changed on the same item: only between two
        # sheets that both carry enchant ids (shipper 0.2.2 on).
        for i in g.get("items") or []:
            key, e = (i.get("slot"), i.get("item_id")), i.get("enchant_id")
            before = prev_ench.get(key, "unseen")
            if e and before not in ("unseen", None) and before != e:
                ev.append({"t": g["_time"], "type": "gear", "link": "#gear",
                           "text": f"Enchanted {i.get('name') or i.get('item_id')}"
                                   + (f": {i['enchant']}" if i.get("enchant") else "")})
            prev_ench[key] = e
        prev = items
    last: dict = {}
    for r in skill_rows:
        sid, rank = opt_num(r.get("skill_id")), int(num(r.get("rank")))
        if sid not in PRIMARY and sid not in SECONDARY:
            continue
        name = PRIMARY.get(sid) or SECONDARY.get(sid)
        if sid in last and rank // 25 > last[sid] // 25:
            ev.append({"t": r["_time"], "type": "profession", "text": f"{name} {rank}", "link": "#advice"})
        last[sid] = rank
    started = set()
    for r in steps_rows:
        # Leveling guides only (their id holds the level band); the
        # profession and lookup guides come and go within a minute.
        g = r.get("guide") or ""
        if LEVELING.search(g) and g not in started:
            started.add(g)
            ev.append({"t": r["_time"], "type": "guide", "text": f"Started {guide_title(g, ref)}", "link": "#guide"})
    ev.sort(key=lambda e: parse_t(e["t"]), reverse=True)
    return ev


# ---------------------------------------------------------- level snapshots


def windows(data: dict) -> dict[int, tuple[datetime | None, datetime | None]]:
    """Each level's span of calendar time, from one ding to the next."""
    climb = [(p["level"], parse_t(p["t"])) for p in data.get("climb") or []]
    out = {}
    for i, (lv, t) in enumerate(climb):
        out[lv] = (t, climb[i + 1][1] if i + 1 < len(climb) else None)
    for lv in data.get("levels") or []:
        if lv["level"] not in out and lv.get("at"):
            end = parse_t(lv["at"])
            out[lv["level"]] = (end - timedelta(seconds=lv["seconds"]), end)
    return out


ITEM_RE = re.compile(r"(\d+):(\d+)")


def snapshot(level: int, data: dict, span: tuple, gear_rows: list[dict], q: Q, cf: str, ref, who: dict,
             ds: list[dict]) -> dict:
    """The character at `level`: its gear then (the wow-gear event in force
    at the level's end, else partial: what the recorder saw it loot), time,
    zones, quests, deaths and the route's plan for that level."""
    t0, t1 = span
    lv = next((x for x in data.get("levels") or [] if x["level"] == level), None)
    out: dict = {"level": level, "from": iso(t0) if t0 else None, "to": iso(t1) if t1 else None,
                 "seconds": lv["seconds"] if lv else None, "partial_time": bool(lv and lv["partial"])}
    hd = data.get("header") or {}
    if lv is None and t1 is None and hd.get("level") == level and hd.get("time_in_level_s"):
        out.update(seconds=hd["time_in_level_s"], so_far=True)  # the level in play, from the Recorder
    end = t1 or datetime.max.replace(tzinfo=t0.tzinfo if t0 else None)
    # The gear in force at the level's end: the latest read at or before it.
    worn = gear_rows[:tb.at_or_before([parse_t(g.get("_time")) for g in gear_rows], end) + 1]
    if worn and t0 and parse_t(worn[-1].get("_time")) >= t0 - timedelta(days=2):
        g = worn[-1]
        out.update(gear=g, gear_partial=False, gear_at=g.get("_time"))
    else:
        out.update(gear=None, gear_partial=True)
    if t0:
        rng = f"_time:[{iso(t0)}, {iso(t1) if t1 else iso(datetime.now(t0.tzinfo))}]"
        stats = q(f"{rec(cf)} kind:in(turnin, kill, loot) {rng} | stats by (kind) count_uniq(source_id) as n")
        out["counts"] = {r.get("kind"): int(num(r.get("n"))) for r in stats}
        # Time per zone inside the level (stores.zone_time: the records' own times).
        visits = zone_time(q(f"{rec(cf)} {rng} {ZONE_RECORDS}"),
                           split_sessions(q(f"{rec(cf)} {rng} {SESSION_BUCKETS}")))
        out["zones"] = [{"zone": z["zone"], "secs": z["secs"]} for z in zone_totals(visits)][:6]
        if out["gear_partial"]:
            loot = q(f"{rec(cf)} kind:in(loot, turnin) {rng} | fields _time, items, item")
            got = set()
            for r in loot:
                got |= {int(m.group(1)) for m in ITEM_RE.finditer(r.get("items") or "")}
                if r.get("item"):
                    got.add(int(num(r["item"])))
            out["looted_item_ids"] = sorted(got)
        # The Recorder counts deaths: with nothing of it in the level, the
        # count is unknown (None), never 0.
        # The level is [its ding, the next ding): a death on the ding's own
        # second is the new level's, as level_at and the page's by_level say.
        died = [d for d in ds if tb.within(parse_t(d["t"]), t0, t1)]
        out["deaths"] = died if died or stats or out["zones"] else None
    early = bool(t0 and out.get("counts") and not q(f"{rec(cf)} _time:[2000-01-01T00:00:00Z, {iso(t0)}] | limit 1")
                 and lv and not lv["partial"])
    if (not out.get("counts") or early) and lv and (lv.get("quests") is not None or lv.get("kills") is not None):
        # No Recorder, or switched on during this level (it saw only part of
        # it): Polestar's own count of the level's quests and kills.
        out["counts"] = {k: int(lv[f]) for k, f in (("turnin", "quests"), ("kill", "kills")) if lv.get(f) is not None}
    if ref and ref.available:
        r = ref.level(level, who.get("faction"), who.get("class"))
        out["route_s"] = r.get("expected_s") if r else None
    return out


def lq_char(c: str) -> str:
    return f"char:={lq(c)}"
