"""What one analytical build was computed with: its run manifest.

A kept finding holds its SQL (or its metric query, compiled) and the
nightly recheck runs it on newly prepared data. If the prep's meaning, the
semantic model or the reference data moved in between, the same SQL can
pass while measuring something else. So every build (a driver's workspace,
the nightly recheck) gets a manifest, and every finding keeps the id of the
one it was judged under. The recheck compares the definitions:

- the same prep version, semantic model (the definitions the finding reads)
  and reference versions: recheck as before;
- any of them different (or no manifest at all, a finding kept before
  0.7.0): the finding is "to revalidate". It isn't run, gains or loses
  nothing, is shown so on the page and through MCP, and stays out of the
  checked counts until a client or server analysis proposes it again under
  today's definitions.

Fields: `input` (the newest event, the event count and data_mark: which
play the build saw), `prep_version` (prep.PREP_VERSION, bumped by hand when
the tables' meaning changes), `semantic_hash` (semantic.yaml as loaded,
plus the SQL views the model reads: what a finding kept before
`semantic_reads` is compared on; one kept since compares only the
definitions it reads, wowstats.analysis.definitions), `reference` (the bundled Polestar and
forever-db versions the KB, the route and the gear advice come from),
`settings` (timezone, session and trip gaps, and the gear `scoring`: the
spec the upgrade candidates are scored for, review 6), `built_at`, `freshness` (how
old the newest event was at build time). The id hashes everything but the
build time and freshness, so two builds of the same play under the same
definitions share one.
"""

from __future__ import annotations

import functools
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


@functools.cache
def _semantic_hash(path: str, mtime_ns: int) -> str:
    from . import semantic

    try:  # as loaded: comments and spacing don't count
        model = semantic._file()
    except ImportError:  # no Boring Semantic Layer: no metric queries either, the file's bytes stand in
        model = {"raw": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    return _digest({"model": model, "views": semantic.VIEWS})[:16]


def semantic_hash() -> str:
    from . import semantic

    p = semantic.MODEL_FILE
    return _semantic_hash(str(p), p.stat().st_mtime_ns)


def reference_versions(knowledge=None, route_ref=None) -> dict:
    """The reference data's versions in use: the KB's packaged Polestar and
    forever-db export, the route planner's and the gear data's Polestar.
    The game tables' build (wago.tools) is left out: it comes and goes with
    the network, and no prep table reads it."""
    out: dict = {}
    if knowledge is not None:
        meta = getattr(knowledge, "meta", {}) or {}
        out["kb_polestar"] = (meta.get("polestar") or {}).get("tag")
        out["forever_db"] = (meta.get("forever_db") or {}).get("version")
    if route_ref is not None:
        out["route"] = (getattr(route_ref, "polestar", {}) or {}).get("tag") if route_ref.available else None
    try:
        from wowstats import gear

        g = gear.reference()
        out["gear"] = g.meta().get("tag") if g.available else None
    except Exception:  # noqa: BLE001 - a broken gear file is the gear advice's error to report
        out["gear"] = None
    return out


def build(db, rows: list, knowledge=None, route_ref=None, now: datetime | None = None,
          scoring: dict | None = None) -> dict:
    from . import data_mark, prep

    now = now or datetime.now(UTC)
    newest = (db.execute("SELECT value FROM meta WHERE key='last_record'").fetchone() or [None])[0]
    lag = None
    if newest:
        lag = max(0, round((now - datetime.fromisoformat(newest.replace("Z", "+00:00"))).total_seconds()))
    m = {"input": {"newest_event": newest, "events": len(rows), "data_mark": data_mark(db)},
         "prep_version": prep.PREP_VERSION, "semantic_hash": semantic_hash(),
         "reference": reference_versions(knowledge, route_ref),
         "settings": {"timezone": prep.TZ_NAME, "session_gap_s": prep.OFFLINE_S, "trip_gap_s": prep.TRIP_GAP_S,
                      "scoring": scoring or {"spec": None}}}
    m["id"] = _digest(m)[:16]
    m["built_at"] = now.isoformat(timespec="seconds")
    m["freshness"] = {"newest_event": newest, "lag_s": lag}
    return m


# The prep tables the gear scoring fills (from analysis.advice_for): prep's
# upgrade_candidates, and gear's score, bis_pct and fit for the scored spec.
# A scoring change means something else only there. A query reads them
# directly or through any view over them (sem_upgrades, sem_gear, per_slot,
# a metric_query's compiled SQL): semantic.reads resolves which.
SCORED_TABLES = ("upgrade_candidates", "gear")


def reads_scoring(finding: dict | None) -> bool:
    """Whether a finding's query reads what the gear scoring fills, through
    whatever relation. Without the finding (or its SQL), assume it does."""
    from . import semantic

    sql = str((finding or {}).get("sql") or "")
    return not sql.strip() or bool(semantic.reads(semantic.relations_in(sql)) & set(SCORED_TABLES))


def changed(judged: dict | None, now: dict, finding: dict | None = None) -> list[str]:
    """What moved between the build a finding was judged under and today's,
    in words; [] when it rechecks as before. The definitions and the
    settings that change what a query means (the timezone its hours and days
    are in, the session and trip gaps, the spec the gear is scored for) count; the input and the times are
    kept for the record, since the recheck's whole job is to run on new input.
    `finding`: the gear scoring counts only for one that reads what it fills
    (reads_scoring): a WEIGHTS change says nothing new about online time; the
    semantic model only through the definitions it reads (its
    `semantic_reads`, wowstats.analysis.definitions), else the whole file."""
    from . import definitions

    if not judged:
        return ["judged before builds kept a manifest"]
    out = []
    if judged.get("prep_version") != now["prep_version"]:
        out.append(f"prep version {judged.get('prep_version')} -> {now['prep_version']}")
    if definitions.changed(finding, judged, now):  # only what the finding reads, when it says (definitions.py)
        out.append("the semantic model changed")
    old, new = judged.get("reference") or {}, now["reference"]
    out += [f"{k} {old.get(k)} -> {new.get(k)}" for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]
    old, new = judged.get("settings") or {}, now.get("settings") or {}
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a == b or (k == "scoring" and not reads_scoring(finding)):
            continue
        if isinstance(a, dict) or isinstance(b, dict):  # the scoring: say which part moved
            a, b = a if isinstance(a, dict) else {}, b if isinstance(b, dict) else {}
            out += [f"{k} {p} {a.get(p)} -> {b.get(p)}" for p in sorted(set(a) | set(b)) if a.get(p) != b.get(p)]
        else:
            out.append(f"{k} {a} -> {b}")
    return out


def kept(manifests: dict | None, findings: dict, current: dict | None = None) -> dict:
    """The manifests still worth keeping: those a kept finding names, plus
    the current build's."""
    keep = {f.get("manifest") for f in findings.values()}
    out = {k: v for k, v in (manifests or {}).items() if k in keep}
    if current is not None:
        out[current["id"]] = current
    return out
