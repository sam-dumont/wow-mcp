"""The play analysis (full view only): prep, recheck, judge, keep.

1. prep: the character's events from VictoriaLogs into an in-memory SQLite
   database, facts and neutral sums only (wowstats.analysis.prep);
2. explore: the user's own MCP client asks its own questions with
   read-only SQL and the knowledge base, in passes (survey, lanes,
   follow-ups, coverage, synthesis: wowstats.analysis.passes), through the
   MCP tools (wowstats.mcp.analysistools). No model runs on the server
   (owner, 2026-11-18): the server holds the run's state and judges
   (wowstats.analysis.drive);
3. judge: each finding's SQL runs again and must give the numbers the
   model saw; the text is rendered from the re-run (wowstats.analysis.verify);
   the synthesis' clashes, nudges' feasibility and shared lines are checked
   the same way, in code;
4. recheck, nightly: every kept finding runs its SQL again on today's data;
   one that still holds gains weight, one that doesn't fades and is
   dropped once its weight is under MIN_WEIGHT. No model.
5. keep: findings, nudges and the shared page's pattern lines go to
   ANALYSIS_STATE_DIR/chars/<char>/analysis.json, ranked, one file per
   character.

The knowledge base (kb.py, SQLite FTS5, every document with its source):
the Forever client's spell, zone, talent and class tables from wago.tools
(daily, cached); Polestar's forever-db quests, NPCs and items, its guides'
notes, dungeon runs, route model and rested constants, quoted
(data/kb-reference.json.gz, rebuilt with tools/kb/export.py from a Polestar
checkout); short levelling notes in data/kb/, written by an LLM from the
sources each names and labelled so. Forever first, Classic Era labelled
where it is the only source; no Wowhead, Icy Veins, Reddit or armory pages.

Where it shows: public, nothing. /zogzog/: the nudges, whole (what, where,
what blocks it, the next step), that pass verify.shareable_action (no clock
time, date, he or she, player or creature from the records, or where the
character is now) and time per level; pattern lines describe and stay in
the full view,
served by the full view on ANALYSIS_SHARE_PORT (that and nothing else) to
the publisher (ANALYSIS_SHARE_URL), which screens them again and seals
them. Full view: everything, /api/analysis, the "How you play" section and
the MCP tool play_analysis.

Nothing from Battle.net enters the database, so the kept file holds only
the player's own game data (kept forever, like the events themselves). The recheck runs
nightly inside the full view's process group (start_schedule), or
`wow-stats analyse`, and logs one JSON line per character (event
analysis_recheck).

Environment: VL_URL, VL_ARCHIVE_URL, LOGS_FILTER, DEFAULT_CHAR (as the UI),
ANALYSIS_STATE_DIR (unset: off), ANALYSIS_HOUR (4, local: WOW_TIMEZONE, Europe/Brussels),
ANALYSIS_SHARE_PORT, and the KB's MODEL3D_CACHE_DIR / KB_GAME_TABLES.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

STATE_FILE = "analysis.json"
MIN_WEIGHT = 0.25
MAX_WEIGHT = 3.0
MAX_KEPT = 40
SCHEMA = 1

# What a driver's SQL may read, each with where its data comes from: the
# player's own game files (VictoriaLogs) or what the prep derives from them,
# and the prep's own documentation. Nothing from Battle.net (vm-wow) is in
# the database at all.
TABLES = {
    "records": "game", "positions": "game", "loot_items": "game", "xp": "game", "timeline": "game",
    "spans": "game", "sessions": "game", "levels": "game", "route_steps": "game", "dungeon_runs": "game",
    "dungeon_trips": "game", "quests": "game", "gold_moves": "game",
    "gear": "game", "gear_sheets": "game", "professions": "game", "snapshots": "game",
    "upgrade_candidates": "game", "agg_session": "game",
    "agg_level": "game", "agg_zone": "game", "agg_hour": "game", "dictionary": "doc", "record_kinds": "doc",
    "meta": "doc",
}
# The goals the owner gave the first analysis, in his words (2026-11-18):
# carried over when the single-character state of 0.3.x moves to its
# character's own file. Anyone else's come from the `goals` argument.
LEGACY_GOALS = ("It's not to speed run or minimax, it's to adapt to my playstyle but nudge me a bit when I'm "
                "wandering and losing time on nothing. And guide me for gear / XP.")


def log(*a) -> None:
    print(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "analysis:", *a, file=sys.stderr, flush=True)


def state_dir(env: dict) -> Path | None:
    d = (env.get("ANALYSIS_STATE_DIR") or "").strip()
    return Path(d) if d else None


def _norm(char: str) -> str:
    """The name as the analysis compares it: spaces as hyphens, lower case,
    one Unicode form (an É typed two ways is the same É)."""
    return unicodedata.normalize("NFC", char.strip().replace(" ", "-")).lower()


def _legacy_key(char: str) -> str:
    """The folder name before 0.7: every other letter became `_`, so Ærin and
    Érin of one realm shared a folder."""
    return re.sub(r"[^a-z0-9-]", "_", char.strip().replace(" ", "-").lower())


def char_key(char: str) -> str:
    """A character's folder name: readable, plus a short hash of the full
    name so two names that fold to the same letters never share one."""
    n = _norm(char)
    slug = re.sub(r"_+", "_", re.sub(r"[^a-z0-9-]", "_", n))[:48]
    return f"{slug}.{hashlib.sha256(n.encode()).hexdigest()[:10]}"


_MOVE_LOCK = threading.Lock()


def char_dir(env: dict, char: str) -> Path | None:
    """One directory per character under the state dir. A folder from before
    0.7 moves to the new name the first time the character comes up, when
    it is this character's: its analysis names it, or the name had nothing
    to fold (2026-11-20 server review, info 5)."""
    d = state_dir(env)
    if d is None or not char:
        return None
    new = d / "chars" / char_key(char)
    with _MOVE_LOCK:
        old = d / "chars" / _legacy_key(char)
        if not new.exists() and old.is_dir():
            named = str(_read(old / STATE_FILE).get("char") or "")
            folded = _legacy_key(char) != _norm(char)
            if (_norm(named) == _norm(char)) if named else not folded:
                with contextlib.suppress(OSError):
                    old.rename(new)
                    log(f"moved {old.name}/ to {new.name}/")
    return new


def _read(p: Path) -> dict:
    with contextlib.suppress(OSError, ValueError):
        s = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(s, dict) and s.get("schema") == SCHEMA:
            return s
    return {}


def load_state(env: dict, char: str) -> dict:
    """The character's kept analysis ({} when none). The single file of
    0.3.x (ANALYSIS_STATE_DIR/analysis.json, one character) is read as its
    character's until the next save moves it."""
    d = char_dir(env, char)
    if d is None:
        return {}
    s = _read(d / STATE_FILE)
    if s:
        return s
    legacy = _read(state_dir(env) / STATE_FILE)
    if legacy and str(legacy.get("char") or "").lower() == char.replace(" ", "-").lower():
        legacy.setdefault("goals", LEGACY_GOALS)
        return legacy
    return {}


def save_state(env: dict, char: str, state: dict) -> None:
    d = char_dir(env, char)
    if d is None:
        return
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".analysis-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, d / STATE_FILE)
    old = state_dir(env) / STATE_FILE
    if old.exists() and str(_read(old).get("char") or "").lower() == char.replace(" ", "-").lower():
        old.unlink(missing_ok=True)


def characters(env: dict) -> list[str]:
    """Every character with a kept analysis (their own names, from the files)."""
    d = state_dir(env)
    if d is None:
        return []
    out = {str(_read(p).get("char") or "") for p in (d / "chars").glob(f"*/{STATE_FILE}")}
    out.add(str(_read(d / STATE_FILE).get("char") or ""))
    return sorted(c for c in out if c)


def score(f: dict) -> float:
    """Effect (played minutes concerned), sample size and relevance, times
    the weight earned over runs."""
    eff = math.log1p(float(f.get("effect") or 0) / 60.0) + 1.0
    smp = min(1.0, math.sqrt(float(f.get("sample") or 1) / 5.0))
    return round(float(f.get("weight", 1.0)) * int(f.get("relevance") or 1) * eff * smp, 4)


def other_players(store, stream: str, char: str) -> set[str]:
    """Names of other players this character met: kept out of the database,
    used only to screen the shared page's lines."""
    names = sorted({char, char.replace("-", " ")})
    cf = "char:in(" + ", ".join(json.dumps(n) for n in names) + ")"
    out = set()
    with contextlib.suppress(Exception):
        for r in store.vl(f"{stream} {cf} feed:wow-player | stats by (player) count() n"):
            p = str(r.get("player") or "")
            if p:
                out.add(p)
                out.add(p.split("-")[0])
    return out


def weights_for(env: dict, char: str) -> str | None:
    """The spec the player chose to score this character's gear for
    (WEIGHTS, as the UI reads it: Name-Realm:spec,...)."""
    for pair in (env.get("WEIGHTS") or "").split(","):
        c, _, spec = pair.partition(":")
        if c.strip().replace(" ", "-") == char and spec.strip():
            return spec.strip().lower()
    return None


def upgrades_for(rows: list[dict], char: str, env: dict) -> list[dict]:
    """Every upgrade the gear advice finds, for prep's upgrade_candidates
    ([] without a sheet)."""
    from wowstats import gear

    try:
        a = gear.advise_rows(rows, weights_for(env, char))
    except gear.GearError as e:
        log(f"no gear advice for the prep: {e}")
        return []
    return (a or {}).get("upgrades_all") or []


def tables_of(db) -> tuple[list[str], int]:
    """The tables a run should look at (those with rows), and their column
    count: the run's coverage is measured against these."""
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")
             if r[0] not in ("dictionary", "meta", "record_kinds")]
    names = [t for t in names if db.execute(f'SELECT EXISTS (SELECT 1 FROM "{t}")').fetchone()[0]]
    cols = sum(len(db.execute(f'PRAGMA table_info("{t}")').fetchall()) for t in names)
    return names, cols


def keep(kept: dict, got: dict, now: str) -> bool:
    """Store a verified finding with its bookkeeping; True when new."""
    old = kept.get(got["key"])
    got["first_seen"] = (old or {}).get("first_seen", now)
    got["confirmations"] = int((old or {}).get("confirmations", 0)) + (1 if old else 0)
    got["weight"] = round(min(MAX_WEIGHT, float((old or {}).get("weight", 0.5)) + 0.5), 4)
    got["last_confirmed"] = now
    got["misses"] = 0
    for mark in ("doubtful", "reconciles"):  # a clash's verdict stands until a new synthesis
        if old and mark in old and mark not in got:
            got[mark] = old[mark]
    kept[got["key"]] = got
    return old is None


def recheck(kept: dict, ro, now: str, info: dict, duck=None) -> None:
    """Every kept finding on today's data, in place: one that holds gains
    weight, one that doesn't fades and goes under MIN_WEIGHT. A finding
    judged on DuckDB (play_sql and query_metric since 0.7) re-runs there
    (`duck()` gives that runner, None when DuckDB isn't installed: the
    finding waits, unchanged); one kept before runs on SQLite, its dialect."""
    from . import verify

    for key, f in list(kept.items()):
        runner = ro
        if f.get("engine") == "duckdb":
            runner = duck() if duck is not None else None
            if runner is None:
                continue
        upd, why = verify.recheck(f, runner)
        if upd is None:
            f["misses"] = int(f.get("misses", 0)) + 1
            f["weight"] = round(float(f.get("weight", 1.0)) * 0.5, 4)
            f["last_miss"], f["miss_why"] = now, why
            info["faded"] += 1
            if f["weight"] < MIN_WEIGHT:
                kept.pop(key)
                info["dropped"].append(f"{key}: faded ({why})")
            continue
        upd["confirmations"] = int(f.get("confirmations", 0)) + 1
        upd["weight"] = round(min(MAX_WEIGHT, float(f.get("weight", 1.0)) + 0.5), 4)
        upd["last_confirmed"] = now
        upd.pop("miss_why", None)
        kept[key] = upd
        info["confirmed"] += 1


def finish(env: dict, char: str, state: dict, kept: dict, nudges: list, patterns: list, db, knowledge,
           players: set[str], info: dict, now: str) -> dict:
    """Screen, rank and save: shared by the nightly recheck and a driver's
    synthesis. A nudge or pattern stands only on findings still kept and not
    doubtful; a line with a number nobody re-ran goes; the shared page's
    lines are screened for numbers, times, dates and names."""
    from . import verify

    dropped = info.setdefault("dropped", [])

    def unsound(keys) -> str:
        if not keys:
            return "names no finding"
        bad = [k for k in keys if k not in kept or kept[k].get("doubtful")]
        return ("rests on " + ", ".join(f"{k} ({'doubtful' if k in kept else 'not kept'})" for k in bad)) if bad else ""

    def screen(lines, what):
        out = []
        for line in lines:
            why = unsound(line.get("findings"))
            if why:
                dropped.append(f"{what} \"{str(line.get('text') or '')[:60]}\": {why}")
            else:
                out.append(line)
        return out

    nudges = screen(nudges, "nudge")
    patterns = screen(patterns, "pattern")
    for what, lines in (("nudge", nudges), ("pattern", patterns)):
        said = [x for x in lines if any(verify.gendered(str(x.get(p) or "")) for p in ("text", "gist", "next_step"))]
        for line in said:
            dropped.append(f"{what} \"{str(line.get('text') or '')[:60]}\": says he or she")
            lines.remove(line)
    for n in nudges:
        for part in ("text", "gist"):
            if verify.literal_numbers(n.get(part, "")):
                if part == "text":
                    dropped.append(f"nudge \"{n['text'][:60]}\": a number above 100 typed into its text")
                n[part] = ""
    nudges = [n for n in nudges if n["text"]]
    # Owner, 2026-11-18: everything shown is actionable. A pattern line
    # describes; it stays in the full view and the MCP. /zogzog/ gets the
    # nudges, whole (what, where, what blocks it, the next step), screened
    # for the tier: places are allowed there, players, creatures, clock
    # times, dates and where the character is now are not.
    people = verify.banned_people(db, knowledge, players)
    for line in patterns:
        line["shareable"], line["why_not"] = False, "a description, not an action: full view only"
    for n in nudges:
        n["shareable"], n["why_not"] = verify.shareable_action(n, people)
        # Screened against this character's players and creatures, which
        # the share port can't do again: a state from before (a gist
        # screened as a line) is never shared until a run screens it so.
        n["screen"] = "action"
    for f in kept.values():
        f["score"] = score(f)
    ranked = sorted(kept.values(), key=lambda f: -f["score"])[:MAX_KEPT]
    kept = {f["key"]: f for f in ranked}
    newest = db.execute("SELECT value FROM meta WHERE key='last_record'").fetchone()[0]
    runs = (state.get("runs") or [])[-29:] + [info]
    out = {**state, "schema": SCHEMA, "char": char, "updated": now, "newest_event": newest, "findings": kept,
           "nudges": nudges, "patterns": patterns, "runs": runs,
           "kb": {"docs": len(knowledge), "kinds": knowledge.kinds(), "game_build": knowledge.meta.get("game_build"),
                  "polestar": (knowledge.meta.get("polestar") or {}).get("tag")},
           "time_budget": time_budget(db)}
    save_state(env, char, out)
    return out


def store_of(env: dict):
    from wowstats.stores import Store, env_account

    return Store(env.get("VL_URL", "http://victorialogs:9428"), env.get("VL_ARCHIVE_URL", ""), "",
                 env.get("LOGS_FILTER", "feed:wow-*"), int(env.get("VL_ARCHIVE_AFTER_DAYS", "89")),
                 profile_vm_url="",  # no vm-wow: Battle.net data never enters the analysis
                 account_id=env_account(env))  # the person's tenant (wowstats.tenancy)


def _duck_runner(db):
    """The DuckDB runner for the recheck, built on first use (None without DuckDB)."""
    made: list = []

    def get():
        from . import duck, duckproc, semantic

        if not duck.available():
            return None
        if not made:
            con = duck.mirror(db, TABLES, rows=False)
            semantic.prepare(con)
            made.append(duck.ReadOnlyDuck(con, worker=duckproc.Worker(db, TABLES)))
        return made[0]

    return get


def run(env: dict | None = None, char: str | None = None, rows: list[dict] | None = None,
        players: set[str] | None = None, knowledge=None) -> dict:
    """The nightly recheck of one character: prep, re-run every kept
    finding, screen and rank, save. No model. `rows`, `players` and
    `knowledge` are for tests and local runs; the defaults read the stores
    and the KB."""
    from wowstats.mcp import route

    from . import kb, prep
    from .sql import ReadOnly

    env = dict(os.environ if env is None else env)
    t0 = time.time()
    char = prep.norm(char or env.get("DEFAULT_CHAR", ""))
    if not char:
        raise SystemExit("analysis: no character (DEFAULT_CHAR or --char)")
    store = None
    if rows is None or players is None:
        store = store_of(env)
    if rows is None:
        rows = prep.query(store, store.stream, char)
    if players is None:
        players = other_players(store, store.stream, char)
    knowledge = knowledge if knowledge is not None else kb.shared(env)
    db = prep.build(rows, char, route.reference(), map_sizes=knowledge.meta.get("ui_map"),
                    upgrades=upgrades_for(rows, char, env))
    now = datetime.now(UTC).isoformat(timespec="seconds")
    state = load_state(env, char)
    kept: dict[str, dict] = dict(state.get("findings") or {})
    info = {"at": now, "kind": "recheck", "char": char, "confirmed": 0, "faded": 0, "dropped": []}
    recheck(kept, ReadOnly(db), now, info, duck=_duck_runner(db))
    info["seconds_total"] = round(time.time() - t0, 1)
    out = finish(env, char, state, kept, state.get("nudges") or [], state.get("patterns") or [], db, knowledge,
                 players, info, now)
    print(json.dumps({"event": "analysis_recheck", "component": "wow", "findings": len(out["findings"]),
                      **{k: info[k] for k in ("confirmed", "faded", "seconds_total")},
                      "dropped": len(info["dropped"])}, separators=(",", ":")), file=sys.stderr, flush=True)
    return out


def run_all(env: dict | None = None) -> list[str]:
    """The recheck of every character with a kept analysis (and
    DEFAULT_CHAR, so its time per level is there from the first night)."""
    env = dict(os.environ if env is None else env)
    chars = characters(env)
    dc = (env.get("DEFAULT_CHAR") or "").strip().replace(" ", "-")
    if dc and dc.lower() not in {c.lower() for c in chars}:
        chars.append(dc)
    done = []
    for c in chars:
        try:
            run(env, c)
            done.append(c)
        except LookupError as e:
            log(f"{c}: {e}")
    return done


def current_level(db) -> int | None:
    r = db.execute("SELECT level FROM records WHERE level IS NOT NULL ORDER BY t DESC LIMIT 1").fetchone()
    return r[0] if r else None


def feasible(raw: list, level: int | None, kb_has, dropped: list | None = None) -> tuple[list[dict], dict]:
    """The synthesis' feasibility verdicts, checked: a nudge whose verdict is
    not_relevant goes; at_level at or under the current level is now; a
    constraint's KB ids must exist (unknown ones are dropped, and a nudge
    whose constraints are left with none is unchecked and goes). Returns
    (nudges, counts per verdict); why each one went is added to `dropped`."""
    counts = {"now": 0, "at_level": 0, "not_relevant": 0, "unchecked": 0}
    out = []
    for n in raw:
        if not isinstance(n, dict):
            continue
        verdict = n.get("verdict") if n.get("verdict") in ("now", "at_level", "not_relevant") else "unchecked"
        at = n.get("at_level") if isinstance(n.get("at_level"), int) else None
        cons = []
        for c in n.get("constraints") or []:
            if isinstance(c, dict):
                ids = [str(i) for i in c.get("kb") or [] if kb_has is None or kb_has(str(i))]
                if ids:
                    cons.append({"what": str(c.get("what") or "")[:200], "kb": ids})
        if n.get("constraints") and not cons:
            verdict = "unchecked"
        if verdict == "at_level" and (at is None or (level is not None and at <= level)):
            verdict = "now" if at is not None else "unchecked"
        counts[verdict] += 1
        if verdict in ("not_relevant", "unchecked") or len(out) >= 5:
            if dropped is not None:
                why = {"not_relevant": "its verdict is not_relevant",
                       "unchecked": "no verdict, or none of its constraints' KB ids exist"}.get(
                    verdict, "five nudges at most")
                dropped.append(f"nudge \"{str(n.get('text') or '')[:60]}\": {why}")
            continue
        out.append({"text": str(n.get("text") or "")[:500], "gist": str(n.get("gist") or "")[:200],
                    **{p: str(n.get(p) or "")[:300] for p in ("where", "blocked_by", "next_step")},
                    "findings": [str(k) for k in n.get("findings") or []], "verdict": verdict,
                    "at_level": at if verdict == "at_level" else None, "constraints": cons})
    return out, counts


def time_budget(db) -> list[dict]:
    """Per level: online time and the neutral splits the prep keeps (no
    category of its own), next to the route planner's time."""
    rows = db.execute("SELECT a.level, a.online_s, a.instance_s, a.taxi_s, a.ride_s, a.dead_s, a.afk_s, "
                      "a.combat_s, a.xp_gained, l.played_s, l.planned_s FROM agg_level a LEFT JOIN levels l "
                      "ON l.level = a.level ORDER BY a.level").fetchall()
    keys = ("level", "online_s", "instance_s", "taxi_s", "ride_s", "dead_s", "afk_s", "combat_s", "xp_gained",
            "played_s", "planned_s")
    return [{k: (round(v) if isinstance(v, float) else v) for k, v in zip(keys, r, strict=True)} for r in rows]


# ---------------------------------------------------------------- answers

RUN_KEYS = ("at", "kind", "driver", "passes", "coverage", "questions", "findings", "clashes", "feasibility", "new",
            "confirmed", "faded", "dropped", "refused", "seconds_total")


def answer(env: dict, char: str, tier: str = "full") -> dict:
    """GET /api/analysis: one character's kept state for the full view, or
    the shareable lines only (tier "zogzog")."""
    from . import verify

    s = load_state(env, char) if char else {}
    if not s:
        return {"available": False, "why": "no analysis yet" if state_dir(env) else "analysis off"}
    findings = sorted((s.get("findings") or {}).values(), key=lambda f: -float(f.get("score") or 0))
    runs = s.get("runs") or []
    last = runs[-1] if runs else {}
    # Pattern lines describe: never shared (a state kept by an older run
    # may still mark some). Nudges are screened again with today's rules: one
    # kept by an older run (that called the character "he", or was shared as
    # a short gist) stops being shared without a rerun.
    patterns = [{**p, "shareable": False} for p in s.get("patterns") or [] if isinstance(p, dict)]
    nudges = [n for n in s.get("nudges") or [] if isinstance(n, dict)]
    if tier == "zogzog":
        return {"available": True, "char": s.get("char"),
                "nudges": [{k: n.get(k) for k in verify.SHARED_NUDGE} for n in nudges
                           if n.get("shareable") and n.get("screen") == "action"
                           and verify.shareable_action(n, set())[0]][:4],
                "time_budget": [{k: r.get(k) for k in ("level", "online_s", "instance_s", "taxi_s", "dead_s",
                                                       "afk_s", "planned_s")} for r in s.get("time_budget") or []]}
    explored = next((r for r in reversed(runs) if r.get("kind") == "client"), None)
    return {"available": True, "char": s.get("char"), "updated": s.get("updated"),
            "newest_event": s.get("newest_event"),
            "findings": [{k: f.get(k) for k in ("key", "title", "text", "action", "observation", "sql", "kb", "row",
                                                 "doubtful", "reconciles", "relevance", "effect",
                                                 "sample", "weight", "score", "confirmations", "first_seen",
                                                 "last_confirmed", "misses")} for f in findings],
            "nudges": [{k: n.get(k) for k in ("text", "gist", "where", "blocked_by", "next_step", "findings",
                                              "shareable", "verdict", "at_level", "constraints")}
                       for n in nudges],
            "patterns": patterns, "time_budget": s.get("time_budget") or [],
            "last_run": {k: last.get(k) for k in RUN_KEYS},
            "last_analysis": {k: explored.get(k) for k in RUN_KEYS} if explored else None,
            "runs": [{k: r.get(k) for k in ("at", "kind", "driver", "new", "confirmed", "faded", "seconds_total")}
                     for r in runs[-14:]], "kb": s.get("kb")}


# ---------------------------------------------------------------- schedule

SCHEDULE_FILE = "recheck.json"


def due(env: dict, last_at: str | None, now: datetime | None = None) -> bool:
    """Nightly: once a local day, from ANALYSIS_HOUR on (local time, WOW_TIMEZONE);
    never run before: at once."""
    from .prep import TZ

    now = (now or datetime.now(UTC)).astimezone(TZ)
    if not last_at:
        return True
    last = datetime.fromisoformat(last_at).astimezone(TZ)
    return last.date() < now.date() and now.hour >= int(env.get("ANALYSIS_HOUR", "4"))


def start_schedule(env: dict, newest_event) -> threading.Thread | None:
    """A daemon thread in the full view: checks every 10 minutes, runs
    `wow-stats analyse` (the recheck of every character) as a child process
    when it is due and events arrived since the last one."""
    d = state_dir(env)
    if d is None:
        return None
    mark = d / SCHEDULE_FILE

    def loop():
        time.sleep(120)
        while True:
            try:
                last = {}
                with contextlib.suppress(OSError, ValueError):
                    last = json.loads(mark.read_text(encoding="utf-8"))
                newest = newest_event() if last else None
                if due(env, last.get("at")) and (not last or (newest and newest > last.get("at", ""))):
                    log("starting the nightly recheck")
                    p = subprocess.run([sys.executable, "-m", "wowstats", "analyse"], env={**os.environ, **env},
                                       timeout=3600, check=False)
                    log(f"recheck ended ({p.returncode})")
                    # Failed or not, the day is done: no retry loop.
                    d.mkdir(parents=True, exist_ok=True)
                    mark.write_text(json.dumps({"at": datetime.now(UTC).isoformat(timespec="seconds"),
                                                "exit": p.returncode}), encoding="utf-8")
            except Exception as e:  # noqa: BLE001 - the UI must stay up
                log(f"schedule: {e!r}")
            time.sleep(600)

    t = threading.Thread(target=loop, name="analysis", daemon=True)
    t.start()
    return t


def shared(env: dict) -> dict:
    """The /zogzog/ shape of every character with a kept analysis."""
    chars = {}
    for c in characters(env):
        a = answer(env, c, "zogzog")
        if a.get("available"):
            chars[a["char"]] = a
    return {"available": bool(chars), "chars": chars}


def share_server(env: dict, tenancy=None):
    """ANALYSIS_SHARE_PORT: a second listener for the publisher and the
    exporter next to the full view: GET /analysis.json with the /zogzog/
    shape (screened lines and time per level) for the publisher. Never
    routed from outside.

    Every request carries `Authorization: Bearer <token>` from
    ANALYSIS_SHARE_TOKEN_FILE (made on first start when missing: 0600, in
    the state dir by default), whatever the network: a network policy is
    one deployment's luck, and on a flat Docker network or a published port
    the sealed tier's lines and the users list were anyone's (pre-publication
    review M3). It binds to ANALYSIS_SHARE_BIND, BIND by default.

    The lines are per character ({available, chars: {...}}, shared()).
    With several people (wowstats.tenancy) it also answers
    /analysis.json?slug=<slug> (that person's lines), /users.json (who has a
    site and what is on: no hashes, no keys) for the publisher,
    /profile-characters (every person's characters, "<account_id>\t<name>"
    per line) for the exporter, /hosts.json (each site's host) for the host's DNS automation, and
    /assets/<slug>/{build,table/<Name>,file/<id>} (that person's 3D files, wowstats.assets.RemoteStore)
    for the publisher; the publisher POSTs its pass to /publisher.json (the owner's overview)."""
    port = (env.get("ANALYSIS_SHARE_PORT") or "").strip()
    if not port or (state_dir(env) is None and tenancy is None):
        return None
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    from wowstats import guard

    token = guard.share_token(env)

    def analysis_of(slug: str) -> dict:
        if tenancy is None or not slug:
            return shared(env) if state_dir(env) else {"available": False, "chars": {}}
        u = tenancy.registry.by_slug(slug)
        if u is None or not u.active:
            return {"available": False, "chars": {}}
        return shared(tenancy.env_for(u))

    class H(BaseHTTPRequestHandler):
        timeout = 30  # a slow client holds one thread, not one forever

        def do_GET(self):  # noqa: N802
            if not guard.bearer_ok(self.headers, token):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Bearer realm="wow-stats share"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            u = urlsplit(self.path)
            qs = parse_qs(u.query)
            m = re.fullmatch(r"/assets/([a-z0-9]{2,20})/(.{1,60})", u.path) if tenancy is not None else None
            if m:
                from wowstats import assets

                person = tenancy.registry.by_slug(m.group(1))
                if person is None or not person.active:
                    return self.reply(404, b"")
                return assets.share_route(self, Path(tenancy.env_for(person)["MODEL3D_ASSETS_DIR"]), m.group(2))
            if u.path == "/analysis.json":
                body, ctype = json.dumps(analysis_of(qs.get("slug", [""])[0])).encode(), "application/json"
            elif tenancy is not None and u.path == "/users.json":
                body, ctype = json.dumps(tenancy.users_json()).encode(), "application/json"
            elif tenancy is not None and u.path == "/hosts.json":
                body, ctype = json.dumps({"hosts": tenancy.host_list()}).encode(), "application/json"
            elif tenancy is not None and u.path == "/profile-characters":
                body = "".join(f"{a}\t{c}\n" for a, c in tenancy.profile_characters()).encode()
                ctype = "text/plain"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def reply(self, code: int, body: bytes, ctype: str = "application/octet-stream") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            """POST /publisher.json: the publisher's report of its last pass
            (wowstats.admin.take_report), for the owner's overview."""
            if not guard.bearer_ok(self.headers, token):
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if tenancy is None or urlsplit(self.path).path != "/publisher.json":
                self.send_error(404)
                return
            from wowstats import admin

            try:
                n = int(self.headers.get("Content-Length") or "-1")
            except ValueError:
                n = -1
            ok = 0 <= n <= admin.REPORT_MAX_BYTES and admin.take_report(tenancy, self.rfile.read(n))
            self.send_response(204 if ok else 400)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    bind = (env.get("ANALYSIS_SHARE_BIND") or env.get("BIND") or "127.0.0.1").strip()
    srv = ThreadingHTTPServer((bind, int(port)), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="analysis-share", daemon=True).start()
    return srv


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="wow-stats analyse",
                                 description="the nightly recheck: every kept finding's SQL re-run on today's data")
    ap.add_argument("--char", help="one character (default: every character with a kept analysis)")
    a = ap.parse_args(argv)
    if a.char:
        run(char=a.char)
    else:
        run_all()
    return 0
