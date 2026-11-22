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
   one that doesn't hold fades and is dropped once its weight is under
   MIN_WEIGHT. One that holds is still applicable (`last_checked`); it is
   better supported (weight, `confirmations`) only when there is play the
   last support didn't see (`data_mark`). No model. Only under the
   definitions it was judged with: a finding whose build (its `manifest`,
   wowstats.analysis.manifest) had another prep version, semantic model or
   reference data is "to revalidate" instead, until an analysis proposes
   it again.
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
import contextvars
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
import uuid
from datetime import UTC, datetime
from pathlib import Path

from wowstats import __version__

STATE_FILE = "analysis.json"
MIN_WEIGHT = 0.25
MAX_WEIGHT = 3.0
MAX_KEPT = 40
SCHEMA = 1
# Findings kept without a manifest were judged by 0.6 (or a 0.7.0 release
# candidate before the manifest): recheck() carries them over once.
CARRIED_FROM = "0.6"


def carried_note(f: dict) -> str | None:
    """The one line the page and play_analysis show on a carried-over finding."""
    c = f.get("carried_over") or {}
    if not c.get("held"):
        return None
    return f"Checked against {c.get('to')} data, first judged under {c.get('from')}."

# What a driver's SQL may read, each with where its data comes from: the
# player's own game files (VictoriaLogs) or what the prep derives from them,
# and the prep's own documentation. Nothing from Battle.net (vm-wow) is in
# the database at all.
TABLES = {
    "records": "game", "positions": "game", "loot_items": "game", "xp": "game", "timeline": "game",
    "spans": "game", "sessions": "game", "levels": "game", "route_steps": "game", "dungeon_runs": "game",
    "dungeon_trips": "game", "quests": "game", "quests_done": "game", "gold_moves": "game",
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


def _inside(state: Path, name: str) -> Path:
    """state/chars/<name>, refused if it would land anywhere else. The keys
    hold only a-z, 0-9, `-`, `_` and `.` before a hash, so it never does;
    the check says so where the path is made."""
    root = os.path.realpath(os.path.join(state, "chars"))
    p = os.path.normpath(os.path.join(root, name))
    if not p.startswith(root + os.sep):
        raise ValueError(f"not a character folder: {name!r}")
    return Path(p)


def char_dir(env: dict, char: str) -> Path | None:
    """One directory per character under the state dir. A folder from before
    0.7 moves to the new name the first time the character comes up, when
    it is this character's: its analysis names it, or the name had nothing
    to fold (2026-11-20 server review, info 5)."""
    d = state_dir(env)
    if d is None or not char:
        return None
    new, old = _inside(d, char_key(char)), _inside(d, _legacy_key(char))
    with _MOVE_LOCK:
        if not new.exists() and old.is_dir():
            named = str(_read(old / STATE_FILE).get("char") or "")
            folded = _legacy_key(char) != _norm(char)
            if (_norm(named) == _norm(char)) if named else not folded:
                with contextlib.suppress(OSError):
                    old.rename(new)
                    log(f"moved {old.name}/ to {new.name}/")
    return new


# Who writes a character's analysis files (analysis.json, drive.json): a
# server run's token while it runs (wowstats.analysis.auto), None for anyone
# else (the player's own MCP session, the nightly recheck). A server run that
# doesn't finish puts the files back only when nobody else wrote them since it
# started: the external review of 0.7.0-rc.5 found the rollback overwrote the
# player's own session on the same character. Review 2 of 0.7.0: the nightly
# recheck is a child process (start_schedule), and `wow-stats analyse` can be
# run by hand, so an in-memory watcher alone never saw them. Every write now
# goes through `writing`: an flock on the folder's LOCK_FILE, shared by every
# process, and a revision in REVISION_FILE bumped under it. A run rolls back
# only when the revision is still the one its own last write left.
WRITER: contextvars.ContextVar[str | None] = contextvars.ContextVar("analysis_writer", default=None)
LOCK_FILE = ".lock"
REVISION_FILE = "revision"
_WATCH: dict[str, dict[str, Guard]] = {}  # character folder -> {run token: its guard}
_WATCH_LOCK = threading.Lock()
_FOLDER_LOCKS: dict[str, list] = {}  # character folder -> [RLock, depth, fd]


@contextlib.contextmanager
def folder_lock(folder: Path):
    """The character folder, locked against every thread and process that
    writes it. Re-entrant in one process: the flock is taken once."""
    from wowstats.tenancy import _lock_fd

    with _WATCH_LOCK:
        held = _FOLDER_LOCKS.setdefault(str(folder), [threading.RLock(), 0, None])
    with held[0]:
        if held[1] == 0:
            folder.mkdir(parents=True, exist_ok=True)
            held[2] = os.open(folder / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
            _lock_fd(held[2], True)
        held[1] += 1
        try:
            yield
        finally:
            held[1] -= 1
            if held[1] == 0:
                _lock_fd(held[2], False)
                os.close(held[2])
                held[2] = None


def revision(folder: Path) -> int:
    """How many writes the folder has had (0: none since this file came)."""
    with contextlib.suppress(OSError, ValueError):
        return int((folder / REVISION_FILE).read_text(encoding="ascii").strip() or 0)
    return 0


class Guard:
    """A server run's watch on one character folder: who else wrote it
    in this process (`others`), and the revision its own last write left
    (`seen`), which any other process's write moves."""

    def __init__(self, folder: Path | None):
        self.folder, self.others, self.seen = folder, [], 0

    def other_writer(self) -> str | None:
        """Who else wrote the folder since the run started, None when nobody
        did and a rollback is safe. Call it under folder_lock."""
        if self.others:
            return self.others[0]
        if self.folder is not None and revision(self.folder) != self.seen:
            return "another process"
        return None


@contextlib.contextmanager
def writing(folder: Path):
    """Around every write to a character's analysis files: under the
    folder's lock, then one revision more, noted against every server run
    watching the folder other than the writer's own."""
    with folder_lock(folder):
        yield
        before = revision(folder)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".revision-")
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(str(before + 1))
        os.replace(tmp, folder / REVISION_FILE)
        me = WRITER.get()
        with _WATCH_LOCK:
            for token, g in (_WATCH.get(str(folder)) or {}).items():
                if token != me:
                    g.others.append(me or "another session")
                    continue
                if before != g.seen:  # another process wrote between this run's own writes
                    g.others.append("another process")
                g.seen = before + 1


@contextlib.contextmanager
def guard(env: dict, char: str):
    """For a server run on one character: its writes carry its own token,
    and the Guard it yields notes every other writer of the character's
    files while it runs, in this process or another."""
    d = char_dir(env, char)
    token = uuid.uuid4().hex
    g = Guard(d)
    key = str(d) if d is not None else None
    if d is not None:
        with folder_lock(d):
            g.seen = revision(d)
            with _WATCH_LOCK:
                _WATCH.setdefault(key, {})[token] = g
    mark = WRITER.set(token)
    try:
        yield g
    finally:
        WRITER.reset(mark)
        if key is not None:
            with _WATCH_LOCK:
                _WATCH[key].pop(token, None)
                if not _WATCH[key]:
                    del _WATCH[key]


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
    with writing(d):
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
    (WEIGHTS: Name-Realm:spec,...), read by views.settings_from_env, the
    one reader the page and the MCP use."""
    from wowstats.views import settings_from_env

    return settings_from_env(env).weights.get(char)


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


def scoring_for(rows: list[dict], char: str, env: dict) -> dict:
    """The gear scoring the build's upgrade candidates come from, normalized
    for the run manifest's definitions: the spec the gear is scored for, as
    resolved from WEIGHTS (settings_from_env), the add-ons and the class
    default (gear.resolve_spec), and the player's own weight edits for it.
    Two settings that resolve to the same spec score the same. Without a
    sheet or the gear data nothing is scored: None. Review 6: without it a
    WEIGHTS change wasn't a definition change at all."""
    from wowstats import gear

    got = gear.newest_sheet(rows)
    ref = gear.reference()
    cls = gear.class_token((got or ({},))[0].get("class"))
    if got is None or not ref.available or not cls:
        return {"spec": None, "adjust": None}
    sheet = got[0]
    key, _how = gear.resolve_spec(ref, cls, sheet, None, weights_for(env, char))
    edits = (sheet.get("gear_adjust") or {}).get(key) if isinstance(sheet.get("gear_adjust"), dict) else None
    return {"spec": key, "adjust": json.loads(json.dumps(edits, sort_keys=True)) if edits else None}


def tables_of(db) -> tuple[list[str], int]:
    """The tables a run should look at (those with rows), and their column
    count: the run's coverage is measured against these."""
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")
             if r[0] not in ("dictionary", "meta", "record_kinds")]
    names = [t for t in names if db.execute(f'SELECT EXISTS (SELECT 1 FROM "{t}")').fetchone()[0]]
    cols = sum(len(db.execute(f'PRAGMA table_info("{t}")').fetchall()) for t in names)
    return names, cols


def data_mark(db) -> str:
    """Which play the build read: the source events (prep.input_mark), never
    what prep, the gear scoring or a setting derived from them. A finding
    gains support only when this moved since its last support: the same
    data checked again is still applicable, not more proven (external review
    of 0.7.0-rc.5: every nightly recheck raised the weight of findings whose
    data hadn't changed). Review 6: it counted the prepared tables' rows, so
    a WEIGHTS change (other upgrade candidates) or a prep change read as new
    play; a definition change is the manifest's, "to revalidate"."""
    return MARK_PREFIX + str((db.execute("SELECT value FROM meta WHERE key='input_mark'").fetchone() or [""])[0])


# A support mark read from the source events. One without it was made from
# the prepared tables (before review 6): comparing the two would read every
# finding's next recheck as new play.
MARK_PREFIX = "src:"


def _support(f: dict, old: dict | None, mark: str | None, now: str) -> bool:
    """A finding that holds: still applicable now; better supported (weight,
    confirmations) only on data its last support didn't see. True when it
    gained support."""
    f["last_checked"] = now
    was = (old or {}).get("support_mark")
    if old is not None and mark is not None and (was == mark or (was and not str(was).startswith(MARK_PREFIX))):
        # The same play, or a mark of the old kind: what it saw can't be
        # compared, so today's play counts as seen, with no weight for it.
        for k in ("weight", "confirmations", "last_confirmed"):
            if k in old:
                f[k] = old[k]
        f["support_mark"] = mark
        return False
    f["confirmations"] = int((old or {}).get("confirmations", 0)) + (1 if old else 0)
    f["weight"] = round(min(MAX_WEIGHT, float((old or {}).get("weight", 0.5)) + 0.5), 4)
    f["last_confirmed"] = now
    if mark is not None:
        f["support_mark"] = mark
    return True


def keep(kept: dict, got: dict, now: str, mark: str | None = None) -> bool:
    """Store a verified finding with its bookkeeping; True when new. `mark`:
    data_mark of the data it was judged on."""
    old = kept.get(got["key"])
    got["first_seen"] = (old or {}).get("first_seen", now)
    _support(got, old, mark, now)
    got["misses"] = 0
    for mark in ("doubtful", "reconciles"):  # a clash's verdict stands until a new synthesis
        if old and mark in old and mark not in got:
            got[mark] = old[mark]
    kept[got["key"]] = got
    return old is None


def recheck(kept: dict, ro, now: str, info: dict, duck=None, mark: str | None = None,
            build: dict | None = None, builds: dict | None = None) -> None:
    """Every kept finding on today's data, in place: one that holds is still
    applicable, and gains weight only when `mark` (data_mark) moved since
    its last support; one that doesn't fades and goes under MIN_WEIGHT. A finding
    judged on DuckDB (play_sql and query_metric since 0.7) re-runs there
    (`duck()` gives that runner, None when DuckDB isn't installed: the
    finding waits, unchanged); one kept before runs on SQLite, its dialect.

    `build`: today's manifest, `builds` the kept ones by id. A finding judged
    under other definitions (manifest.changed) isn't run: it is marked
    `revalidate` (since when, why), counted apart, and keeps its weight.

    A finding kept before manifests existed (0.6) is carried over once
    instead: its SQL runs on today's build, and if it holds it takes today's
    manifest and `carried_over`, with no weight gained for it (support only
    grows on new data). If it doesn't, it fades like any other. Either way
    it isn't tried again: a later definition change, or a failed carry-over
    still without a manifest, is "to revalidate" like any other finding.
    Without it, a friend who rarely plays would see every old finding "to
    revalidate" for weeks, until the next analysis."""
    from . import manifest, verify

    for key, f in list(kept.items()):
        carry = build is not None and not f.get("manifest") and "carried_over" not in f
        if build is not None and not carry:
            why_not = manifest.changed((builds or {}).get(f.get("manifest") or ""), build, f)
            if why_not:
                f["revalidate"] = {"since": (f.get("revalidate") or {}).get("since") or now, "why": why_not}
                info["revalidate"] = info.get("revalidate", 0) + 1
                continue
        runner = ro
        if f.get("engine") == "duckdb":
            runner = duck() if duck is not None else None
            if runner is None:
                continue
        upd, why = verify.recheck(f, runner)
        if carry:
            f["carried_over"] = {"from": CARRIED_FROM, "to": __version__, "at": now, "held": upd is not None}
            if upd is not None:
                upd["carried_over"], upd["manifest"], upd["last_checked"] = f["carried_over"], build["id"], now
                upd.setdefault("support_mark", mark)  # what the old support saw is unknown: count today's as seen
                upd.pop("revalidate", None)
                upd.pop("miss_why", None)
                kept[key] = upd
                info["carried_over"] = info.get("carried_over", 0) + 1
                continue
        if upd is None:
            f["misses"] = int(f.get("misses", 0)) + 1
            f["weight"] = round(float(f.get("weight", 1.0)) * 0.5, 4)
            f["last_miss"], f["miss_why"] = now, why
            info["faded"] += 1
            if f["weight"] < MIN_WEIGHT:
                kept.pop(key)
                info["dropped"].append(f"{key}: faded ({why})")
            continue
        gained = _support(upd, f, mark, now)
        upd.pop("miss_why", None)
        kept[key] = upd
        k = "confirmed" if gained else "still"
        info[k] = info.get(k, 0) + 1


LINE_PARTS = ("text", "gist", "where", "blocked_by", "next_step")


def bind(lines: list[dict], kept: dict, level: int | None, dropped: list, what: str) -> list[dict]:
    """Nudges and patterns say things about numbers (a level, a count): a
    stored line must not go stale between runs. The client writes them with
    `{column}` placeholders, filled from the re-run row of the findings the
    line cites; the template is kept (`tpl`) and rendered again at every run.
    A line without placeholders that names the level it was written at
    ("well behind level 32") goes once the character is past it. Lines that
    quote nothing stay as they are."""
    from . import verify

    out = []
    for line in lines:
        tpl = line.get("tpl") or {p: str(line[p]) for p in LINE_PARTS if line.get(p)}
        row: dict = {}
        for k in line.get("findings") or []:
            for col, v in ((kept.get(k) or {}).get("row") or {}).items():
                row.setdefault(col, v)
        gone = ""
        if any(verify.PLACEHOLDER.search(t) for t in tpl.values()):
            if any(m.group(1) not in row for t in tpl.values() for m in verify.PLACEHOLDER.finditer(t)):
                gone = "a placeholder has no value in the findings it cites"
            else:
                line["tpl"] = tpl
                line.update({p: verify.render(t, row) for p, t in tpl.items()})
        else:
            written = line.get("written_level")
            if written is None:
                line["written_level"] = level
            elif level is not None and level > written and any(
                    re.search(rf"(?<![\w.]){written}(?![\w.])", t) for t in tpl.values()):
                gone = f"it was written at level {written}"
        if line.get("verdict") == "at_level" and level is not None and (line.get("at_level") or 0) <= level:
            line["verdict"], line["at_level"] = "now", None
        if gone:
            dropped.append(f"{what} \"{str(line.get('text') or '')[:60]}\": {gone}")
        else:
            out.append(line)
    return out


def held_on(line: dict, findings: dict) -> list[str]:
    """The findings a nudge or pattern line cites that can't back it today:
    awaiting revalidation (judged under other definitions), doubtful, or no
    longer kept. [] when the line stands. A line that names no finding
    stands on nothing: held on "(none)". Review 6: a definition change
    marked the finding and left the nudge on it in "What to do"."""
    keys = [k for k in line.get("findings") or [] if isinstance(k, str)]
    if not keys:
        return ["(none)"]
    return [k for k in keys if k not in findings or findings[k].get("revalidate") or findings[k].get("doubtful")]


def standing(state: dict) -> dict:
    """The kept lines every reader shows, by one rule (held_on): `nudges`
    and `patterns` that stand on findings judged under today's definitions,
    and `on_hold`, the others ({kind, text, waits_on}), which come back
    once an analysis judges their findings again. Read at output time, on
    the findings as they are now: a state saved before a finding was marked
    shows no held line either. The page, the MCP's play_analysis and
    whats_new, the plan's facts and the /zogzog/ bundle all go through it."""
    findings = state.get("findings") or {}
    out: dict = {"nudges": [], "patterns": [], "on_hold": []}
    for kind, lines in (("nudge", state.get("nudges")), ("pattern", state.get("patterns"))):
        for line in lines or []:
            if not isinstance(line, dict):
                continue
            waits = held_on(line, findings)
            if waits:
                out["on_hold"].append({"kind": kind, "text": line.get("text"), "waits_on": waits})
            else:
                out[kind + "s"].append({k: v for k, v in line.items() if k != "on_hold"})
    return out


def finish(env: dict, char: str, state: dict, kept: dict, nudges: list, patterns: list, db, knowledge,
           players: set[str], info: dict, now: str, build: dict | None = None) -> dict:
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
            if verify.literal_numbers((n.get("tpl") or {}).get(part) or n.get(part, "")):
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
    # A line on a finding to revalidate is kept (it comes back once the
    # finding is judged again) but held: out of every "do now" list.
    info["on_hold"] = 0
    for line in (*nudges, *patterns):
        waits = held_on(line, kept)
        if waits:
            line["on_hold"] = waits
            info["on_hold"] += 1
        else:
            line.pop("on_hold", None)
    newest = db.execute("SELECT value FROM meta WHERE key='last_record'").fetchone()[0]
    runs = (state.get("runs") or [])[-29:] + [info]
    from . import manifest

    out = {**state, "schema": SCHEMA, "char": char, "updated": now, "newest_event": newest, "findings": kept,
           "manifests": manifest.kept(state.get("manifests"), kept, build),
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

    from . import kb, manifest, prep
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
    ref = route.reference()
    db = prep.build(rows, char, ref, map_sizes=knowledge.meta.get("ui_map"),
                    upgrades=upgrades_for(rows, char, env))
    build = manifest.build(db, rows, knowledge, ref, scoring=scoring_for(rows, char, env))
    now = datetime.now(UTC).isoformat(timespec="seconds")
    state = load_state(env, char)
    kept: dict[str, dict] = dict(state.get("findings") or {})
    info = {"at": now, "kind": "recheck", "char": char, "manifest": build["id"], "confirmed": 0, "still": 0,
            "faded": 0, "revalidate": 0, "carried_over": 0, "dropped": []}
    recheck(kept, ReadOnly(db), now, info, duck=_duck_runner(db), mark=data_mark(db), build=build,
            builds=state.get("manifests"))
    info["seconds_total"] = round(time.time() - t0, 1)
    level = current_level(db)
    nudges = bind([n for n in state.get("nudges") or [] if isinstance(n, dict)], kept, level, info["dropped"], "nudge")
    patterns = bind([p for p in state.get("patterns") or [] if isinstance(p, dict)], kept, level, info["dropped"],
                    "pattern")
    out = finish(env, char, state, kept, nudges, patterns, db, knowledge,
                 players, info, now, build)
    print(json.dumps({"event": "analysis_recheck", "component": "wow", "findings": len(out["findings"]),
                      **{k: info[k] for k in ("confirmed", "still", "faded", "revalidate", "carried_over",
                                                 "seconds_total")},
                      "dropped": len(info["dropped"])}, separators=(",", ":")), file=sys.stderr, flush=True)
    return out


def run_all(env: dict | None = None, failed: list[str] | None = None) -> list[str]:
    """The recheck of every character with a kept analysis (and
    DEFAULT_CHAR, so its time per level is there from the first night).
    A character whose store read fails (stores.SourceError, IncompleteRead
    included) keeps its last answer, is named in `failed`, and the others
    are still rechecked: one person's store never holds up the rest."""
    from wowstats.stores import SourceError

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
        except SourceError as e:
            log(f"{c}: not rechecked, the store didn't answer whole: {e}")
            if failed is not None:
                failed.append(c)
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

RUN_KEYS = ("at", "kind", "by", "via", "driver", "passes", "coverage", "questions", "findings", "clashes",
            "feasibility", "new", "confirmed", "faded", "revalidate", "carried_over", "manifest", "dropped",
            "refused", "seconds_total")


def explored(runs: list[dict]) -> dict | None:
    """The last run that explored: a client's run or the server's own
    (wowstats.analysis.auto). Runs kept before `kind` existed carry none: one
    that found something new and isn't a recheck is a client run."""
    return next((r for r in reversed(runs or []) if r.get("kind") in ("client", "server")
                 or (r.get("new") is not None and r.get("kind") not in ("recheck",))), None)


def last_refresh(state: dict) -> dict | None:
    """When the character's analysis was last refreshed, and by whom: its
    last finished run that explored (a nightly recheck isn't one). None:
    no refresh on record. The server's timer counts from it."""
    r = explored(state.get("runs") or [])
    if r is None:
        return None
    by = r.get("by") or ("server" if r.get("kind") == "server" else "player")
    return {"at": r.get("at"), "by": by, "via": r.get("via") or ("auto" if by == "server" else "client")}


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
    # A line resting on a finding that awaits revalidation (or is doubtful,
    # or gone) is held on every tier, from the findings as they are now.
    lines = standing(s)
    patterns = [{**p, "shareable": False} for p in lines["patterns"]]
    nudges = lines["nudges"]
    if tier == "zogzog":
        return {"available": True, "char": s.get("char"),
                "nudges": [{k: n.get(k) for k in verify.SHARED_NUDGE} for n in nudges
                           if n.get("shareable") and n.get("screen") == "action"
                           and verify.shareable_action(n, set())[0]][:4],
                "time_budget": [{k: r.get(k) for k in ("level", "online_s", "instance_s", "taxi_s", "dead_s",
                                                       "afk_s", "planned_s")} for r in s.get("time_budget") or []]}
    explored_run = explored(runs)
    return {"available": True, "char": s.get("char"), "updated": s.get("updated"),
            "newest_event": s.get("newest_event"),
            "findings": [{k: f.get(k) for k in ("key", "title", "text", "action", "observation", "sql", "kb", "row",
                                                 "doubtful", "reconciles", "relevance", "effect",
                                                 "sample", "weight", "score", "confirmations", "first_seen",
                                                 "last_confirmed", "misses", "manifest", "revalidate",
                                                 "carried_over")}
                         | ({"carried_note": carried_note(f)} if carried_note(f) else {})
                         for f in findings],
            # Checked: rechecked under the definitions they were judged
            # with. The rest wait for an analysis to judge them again.
            "checked": sum(1 for f in findings if not f.get("revalidate")),
            "to_revalidate": sum(1 for f in findings if f.get("revalidate")),
            "nudges": [{k: n.get(k) for k in ("text", "gist", "where", "blocked_by", "next_step", "findings",
                                              "shareable", "verdict", "at_level", "constraints")}
                       for n in nudges],
            "patterns": patterns, "on_hold": lines["on_hold"], "time_budget": s.get("time_budget") or [],
            "last_run": {k: last.get(k) for k in RUN_KEYS},
            "last_analysis": {k: explored_run.get(k) for k in RUN_KEYS} if explored_run else None,
            "manifest": (s.get("manifests") or {}).get(last.get("manifest") or ""),
            "runs": [{k: r.get(k) for k in ("at", "kind", "driver", "new", "confirmed", "faded", "revalidate",
                                            "carried_over", "seconds_total")}
                     for r in runs[-14:]], "kb": s.get("kb")}


# ---------------------------------------------------------------- schedule

SCHEDULE_FILE = "recheck.json"


def due(env: dict, last_at: str | None, now: datetime | None = None) -> bool:
    """Nightly: once a local day, from ANALYSIS_HOUR on (local time, WOW_TIMEZONE);
    never run before: at once."""
    from wowstats import timebounds as tb

    from .prep import TZ

    now = tb.local(now or datetime.now(UTC), TZ)
    if not last_at:
        return True
    last_day = tb.local_day(datetime.fromisoformat(last_at), TZ)
    return last_day < now.date() and now.hour >= int(env.get("ANALYSIS_HOUR", "4"))


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
    and /items/<slug>/facts (that person's item facts, wowstats.itemfacts.RemoteFacts)
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
            m = re.fullmatch(r"/items/([a-z0-9]{2,20})/facts", u.path) if tenancy is not None else None
            if m:
                from wowstats import itemfacts

                person = tenancy.registry.by_slug(m.group(1))
                if person is None or not person.active:
                    return self.reply(404, b"")
                return itemfacts.share_route(self, itemfacts.default_dir(tenancy.env_for(person)))
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
        return 0
    failed: list[str] = []
    run_all(failed=failed)
    return 1 if failed else 0
