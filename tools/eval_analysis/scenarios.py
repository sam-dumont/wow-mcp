"""The seeded histories: the docs site's demo character
(tools/fixtures/generate.py --demo, Nibbet-Gearholt: twenty evenings, levels
22 to 34) with one known effect injected, and the truth to grade against.

Each scenario is a JSON file in scenarios/ (data, not code): what to inject,
the server settings (WEIGHTS...), the goals handed to the analysis, and the
truth: what a right analysis says (`must_find`) and what it must not claim
(`must_not_claim`). The injections are small edits of the same event list, so
two scenarios differ only by their effect:

  death_spike    extra deaths in one zone, until its deaths per online hour
                 are at least `factor` times the rest of the history's; each death
                 takes its corpse run, so the rest of that evening moves later
  recorder_off   the Recorder's records dropped between two local dates; the
                 level-ups, character snapshots and gear sheets stay (Polestar
                 and the shipper still send them)
  offline_gap    every event from a local date on moves `days` later: a break
                 in the middle of a level, the play itself unchanged
  wear           the newest gear sheet again, a minute later, with one slot
                 changed: an item the player put on

`facts()` measures what each effect left in the prep database, with the
product's own prep: the tests check the injection did what its file says
(the spike is significant, the control has none, the gap level is on pace
in played time), and the report shows the numbers next to the verdict.

Synthetic data only: the demo character, its made-up realm, nothing real.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import random
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCENARIOS = HERE / "scenarios"
CHAR = "Nibbet-Gearholt"
TZ = ZoneInfo("Europe/Brussels")
# What prep.query leaves out (the store query's own filter).
SKIP_FEEDS = ("wow-player", "wow-prices", "wow-combatlog")
SESSION_GAP_S = 330  # the shipper's session split (tools/fixtures/generate.py shipper_shapes)
INJECTIONS = ("death_spike", "recorder_off", "offline_gap", "wear")
RULE_PLACES = ("any", "actions")


class ScenarioError(ValueError):
    pass


# ---------------------------------------------------------------- loading


def names() -> list[str]:
    return sorted(p.stem for p in SCENARIOS.glob("*.json"))


def load(name: str) -> dict:
    """One scenario file, checked: a mistake in the data fails here, not
    halfway through a run."""
    p = SCENARIOS / f"{name}.json"
    if not p.is_file():
        raise ScenarioError(f"no scenario {name!r}: {', '.join(names())}")
    sc = json.loads(p.read_text(encoding="utf-8"))
    check(sc, name)
    return sc


def check(sc: dict, name: str) -> None:
    if sc.get("name") != name:
        raise ScenarioError(f"{name}: `name` must be the file's name")
    for k in ("about", "goals", "truth"):
        if not sc.get(k):
            raise ScenarioError(f"{name}: `{k}` is missing")
    for inj in sc.get("inject") or []:
        if inj.get("kind") not in INJECTIONS:
            raise ScenarioError(f"{name}: unknown injection {inj.get('kind')!r} ({', '.join(INJECTIONS)})")
    truth = sc["truth"]
    if not truth.get("expected"):
        raise ScenarioError(f"{name}: truth.expected (the right answer, in words) is missing")
    for part in ("must_find", "must_not_claim"):
        for rule in truth.get(part) or []:
            if not rule.get("id") or not rule.get("all"):
                raise ScenarioError(f"{name}: every {part} rule needs an id and `all` (groups of patterns)")
            if any(not isinstance(g, list) or not g for g in rule["all"]):
                raise ScenarioError(f"{name}: {rule['id']}: each group in `all` is a non-empty list")
            if rule.get("in", "any") not in RULE_PLACES:
                raise ScenarioError(f"{name}: {rule['id']}: `in` is one of {', '.join(RULE_PLACES)}")
    if not (truth.get("must_find") or truth.get("must_not_claim")):
        raise ScenarioError(f"{name}: the truth checks nothing")
    for k, v in (sc.get("env") or {}).items():
        if not k.isupper() or not isinstance(v, str):
            raise ScenarioError(f"{name}: env {k!r} must be an UPPER_CASE name with a text value")
        if k.startswith("ANALYSIS_") or k in ("VL_URL", "VL_ARCHIVE_URL", "VM_URL"):
            raise ScenarioError(f"{name}: env {k} is the runner's, not a scenario's")


# ---------------------------------------------------------------- the events


@functools.lru_cache(maxsize=1)
def _base() -> tuple:
    if str(ROOT / "tools" / "fixtures") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools" / "fixtures"))
    sys.path.insert(0, str(ROOT))
    import generate

    return tuple(json.dumps(r, sort_keys=True) for r in generate.build_demo())


def base_events() -> list[dict]:
    """The demo character's events as generated: the same every time."""
    return [json.loads(x) for x in _base()]


def _t(r: dict) -> float:
    return datetime.fromisoformat(r["_time"].replace("Z", "+00:00")).timestamp()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _local_day(t: float) -> date:
    return datetime.fromtimestamp(t, TZ).date()


def _midnight(day: str) -> float:
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp()


def sessions(rows: list[dict]) -> list[tuple[float, float]]:
    """(start, end) of each evening, from the Recorder's records."""
    ts = sorted(_t(r) for r in rows if r["feed"] == "wow-recorder")
    out: list[list[float]] = []
    for t in ts:
        if out and t - out[-1][1] <= SESSION_GAP_S:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b) for a, b in out]


def build(sc: dict) -> list[dict]:
    """The scenario's events: the demo's, then each injection in order."""
    rows = base_events()
    for inj in sc.get("inject") or []:
        rows = INJECT[inj["kind"]](rows, inj)
    return sorted(rows, key=lambda r: (r["_time"], r.get("source_id") or ""))


def death_spike(rows: list[dict], inj: dict) -> list[dict]:
    zone, factor = inj["zone"], float(inj.get("factor", 2.0))
    rnd = random.Random(inj.get("seed", 1))
    rate = zone_rates(rows)
    if zone not in rate["zones"]:
        raise ScenarioError(f"death_spike: no online time in {zone}")
    z = rate["zones"][zone]
    target = factor * rate["rest_rate"](zone) * z["hours"]
    # At least `factor` times: rounding down would leave the spike short of
    # its own definition (7 deaths where 2x asks for 7.04).
    extra = max(1, math.ceil(target - 1e-9) - z["deaths"])
    kills = [r for r in rows if r["feed"] == "wow-recorder" and r.get("kind") == "kill" and r.get("zone") == zone]
    if len(kills) < extra:
        raise ScenarioError(f"death_spike: {zone} has {len(kills)} kills, fewer than {extra} deaths to add")
    # Evenly over the zone's kills, so the extra deaths spread over its evenings.
    step = len(kills) / extra
    picks = [kills[int(i * step + step / 2)] for i in range(extra)]
    rows = [dict(r) for r in rows]
    for n, k in enumerate(sorted(picks, key=_t)):
        t = _t(k) + 2
        corpse = round(rnd.uniform(60, 140), 1)
        rest = rnd.randint(12, 24)
        delta = corpse + rest + 1
        start, end = next(s for s in sessions(rows) if s[0] <= t <= s[1] + 1)
        # The corpse run takes time: the rest of the evening moves later.
        for r in rows:
            rt = _t(r)
            if t <= rt <= end + 600:
                r["_time"] = _iso(rt + delta)
        # The level it happened at took that much longer too.
        ding = next((r for r in sorted(rows, key=_t) if r["feed"] == "wow-level" and _t(r) > t), None)
        if ding is not None and ding.get("seconds") not in (None, ""):
            ding["seconds"] = str(int(float(ding["seconds"]) + delta))
        here = {k2: k[k2] for k2 in ("map_id", "x", "y", "zone") if k2 in k}
        base = {"char": k["char"], "component": "wow", "feed": "wow-recorder", "layer": "app"}
        rows += [
            {**base, "_time": _iso(t), "kind": "death", "npc_id": k.get("npc_id"), "npc_level": k.get("level"),
             **here, "source_id": f"eval:death:{n}"},
            {**base, "_time": _iso(t + corpse), "kind": "revive", "how": "c", **here,
             "source_id": f"eval:revive:{n}"},
            {**base, "_time": _iso(t + corpse + rest), "kind": "rest", "rest_kind": "f", "seconds": str(rest),
             "zone": zone, "source_id": f"eval:rest:{n}"},
        ]
    return rows


def recorder_off(rows: list[dict], inj: dict) -> list[dict]:
    lo, hi = date.fromisoformat(inj["from"]), date.fromisoformat(inj["to"])
    kept = [r for r in rows if not (r["feed"] == "wow-recorder" and lo <= _local_day(_t(r)) <= hi)]
    if len(kept) == len(rows):
        raise ScenarioError(f"recorder_off: no Recorder records from {lo} to {hi}")
    return kept


def offline_gap(rows: list[dict], inj: dict) -> list[dict]:
    t0, days = _midnight(inj["before"]), int(inj["days"])
    out = []
    for r in rows:
        r = dict(r)
        if _t(r) >= t0:
            r["_time"] = _iso(_t(r) + days * 86400)
        out.append(r)
    return out


def wear(rows: list[dict], inj: dict) -> list[dict]:
    from wowstats import gear

    ref = gear.reference()
    slot, item = int(inj["slot"]), int(inj["item"])
    info = ref.item(item)
    if not info:
        raise ScenarioError(f"wear: item {item} isn't in the gear data")
    sheets = sorted((r for r in rows if r["feed"] == "wow-gear"), key=_t)
    if not sheets:
        raise ScenarioError("wear: no gear sheet to change")
    new = copy.deepcopy(sheets[-1])
    items = json.loads(new["items"])
    hit = [i for i in items if i["slot"] == slot]
    if not hit:
        raise ScenarioError(f"wear: slot {slot} is empty in the newest sheet")
    name = (ref.data.get("names") or {}).get(str(item))
    hit[0].update({"item_id": item, "item_level": info.get("ilvl"), "name": name, "quality": info.get("quality"),
                   "req_level": info.get("req") or None})
    new["items"] = json.dumps(items, separators=(",", ":"))
    lv = [i["item_level"] for i in items if i.get("item_level")]
    new["item_level"] = str(round(sum(lv) / len(lv), 1))
    new["_time"] = _iso(_t(new) + 60)
    digest = hashlib.sha1(json.dumps([new.get("items"), new.get("professions")]).encode()).hexdigest()[:12]
    new["source_id"] = f"gear:{new['char']}:{digest}"
    return [*rows, new]


INJECT = {"death_spike": death_spike, "recorder_off": recorder_off, "offline_gap": offline_gap, "wear": wear}


# ---------------------------------------------------------------- what the analysis reads


def analysis_rows(rows: list[dict], char: str = CHAR) -> list[dict]:
    """What wowstats.analysis.prep.query returns for these events: the
    character's own, without other players, prices and combat log lines."""
    from wowstats.analysis import prep

    want = prep.norm(char)
    return [dict(r) for r in rows if prep.norm(str(r.get("char") or "")) == want and r.get("feed") not in SKIP_FEEDS]


def prep_db(rows: list[dict], env: dict | None = None, char: str = CHAR):
    """The prep database the server would build (SQLite), for facts()."""
    from wowstats.analysis import kb, prep, upgrades_for
    from wowstats.mcp import route

    env = {"KB_GAME_TABLES": "off", **(env or {})}
    got = analysis_rows(rows, char)
    k = kb.shared(env)
    return prep.build([dict(r) for r in got], char, route.reference(), map_sizes=k.meta.get("ui_map"),
                      upgrades=upgrades_for(got, char, env))


def zone_rates(rows: list[dict], db=None) -> dict:
    """Deaths per online hour by zone (zones with an hour or more), from the
    prep's own agg_zone, and a one-sided Poisson test of each zone against
    the rest of the history's rate."""
    db = db or prep_db(rows)
    zs = {z: {"hours": s / 3600.0, "deaths": int(d or 0)}
          for z, s, d in db.execute("SELECT zone, online_s, deaths FROM agg_zone WHERE zone IS NOT NULL")}
    hours = sum(v["hours"] for v in zs.values())
    deaths = sum(v["deaths"] for v in zs.values())

    def rest_rate(z: str) -> float:
        h = hours - zs[z]["hours"]
        return (deaths - zs[z]["deaths"]) / h if h > 0 else 0.0

    for z, v in zs.items():
        v["rate"] = v["deaths"] / v["hours"] if v["hours"] else 0.0
        v["rest_rate"] = rest_rate(z)
        v["p"] = poisson_tail(v["deaths"], v["rest_rate"] * v["hours"])
    return {"zones": zs, "hours": hours, "deaths": deaths, "rest_rate": rest_rate}


def poisson_tail(k: int, mu: float) -> float:
    """P(X >= k) for X ~ Poisson(mu)."""
    if k <= 0:
        return 1.0
    if mu <= 0:
        return 0.0
    below = sum(math.exp(-mu + i * math.log(mu) - math.lgamma(i + 1)) for i in range(k))
    return max(0.0, 1.0 - below)


def facts(sc: dict, rows: list[dict] | None = None) -> dict:
    """What the injection left in the prep database, measured."""
    rows = rows if rows is not None else build(sc)
    db = prep_db(rows, sc.get("env"))
    rates = zone_rates(rows, db)
    big = {z: v for z, v in rates["zones"].items() if v["hours"] >= 1.0}
    out: dict = {
        "events": len(rows), "deaths": rates["deaths"], "online_hours": round(rates["hours"], 2),
        "death_zones": {z: {"hours": round(v["hours"], 2), "deaths": v["deaths"], "per_hour": round(v["rate"], 3),
                            "rest_per_hour": round(v["rest_rate"], 3), "p": round(v["p"], 4)}
                        for z, v in sorted(big.items(), key=lambda kv: -kv[1]["hours"])},
        "significant_death_zones": sorted(z for z, v in big.items() if v["p"] < 0.05),
    }
    levels = {lv: {"played_s": p, "planned_s": pl} for lv, p, pl in
              db.execute("SELECT level, played_s, planned_s FROM levels")}
    dings = dict(db.execute("SELECT level, ding_t FROM levels WHERE ding_t IS NOT NULL"))
    for lv, v in levels.items():
        prev = dings.get(lv - 1)
        if prev and dings.get(lv):
            v["calendar_days"] = round((dings[lv] - prev) / 86400, 1)
    out["levels"] = levels
    out["recorded_levels"] = sorted(r[0] for r in db.execute(
        "SELECT DISTINCT level FROM records WHERE level IS NOT NULL"))
    out["sessions"] = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    out["longest_gap_days"] = round((db.execute("SELECT MAX(gap_before_s) FROM sessions").fetchone()[0] or 0) / 86400,
                                    1)
    worn = {s: (i, n) for s, i, n in db.execute("SELECT slot_name, item_id, name FROM gear")}
    out["worn_head"] = worn.get("Head")
    out["top_upgrades"] = {}
    for slot in ("Head",):
        out["top_upgrades"][slot] = [{"item_id": i, "name": n, "gain": g} for i, n, g in db.execute(
            "SELECT item_id, name, MAX(gain) g FROM upgrade_candidates WHERE slot_name = ? AND now = 1 "
            "GROUP BY item_id, name ORDER BY g DESC LIMIT 3", (slot,))]
    return out


def write_events(rows: list[dict], path: Path) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def read_events(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

