"""What a kept finding reads in the semantic model, and a hash over only that.

The run manifest's `semantic_hash` covers the whole of semantic.yaml (and
the SQL views the model reads): one metric added and every kept finding of
every person went "to revalidate", whatever it read (owner, 2026-11-22: tie
each finding to the definitions it actually reads). So a finding keeps, at
judging time, `semantic_reads`: the names its evidence reads and a hash over
their definitions, and the recheck compares that hash with today's.

The definitions are a graph, one node per piece, each with its digest and
what it depends on:

- `metric:<name>`: the measure, calculated measure or cross-model metric as
  written (expression, description, metadata: eligibility, min_evidence...),
  on its model, the measures a calculated one reads, the metrics a
  cross-model one reads;
- `dim:<model>.<name>`: one model's dimension, on its model;
- `model:<model>`: the model's own keys (table, description, anything but
  its dimensions and measures), on the relation it reads;
- `view:<per_x>`: a view per grain as written, on its metrics and the
  dimensions it reads (entity, columns, where) in their models;
- `relation:<sem_x>`: a model view's SQL (semantic.VIEWS, plus the
  character columns when it uses them), on the relations that SQL names;
- `relation:<table>`: a prep table, its name only: what the tables mean is
  PREP_VERSION, compared for every finding by the manifest (it is one
  number, not one per table).

A finding's names are the relations its SQL names (prep tables, model views,
views per grain: a metric query's compiled SQL names its model views) and,
for a metric query, its metrics and the dimensions it groups by, filters on,
or cuts time with (`time` when it has a grain, since or until), in the
models of its metrics. Its hash covers every node those names reach; a name
today's graph no longer has counts as changed. Adding an unrelated metric,
dimension, view or table moves no node it reaches, so it stays valid.

Conservative where it can't tell: a finding with no SQL and no metric
query, a metric query whose names don't resolve, or SQL that names no known
relation keeps `{"names": None}` and is compared on the whole-file hash, as
before. A finding kept before this (no `semantic_reads`) is compared on the
whole-file hash too, and adopts its names (`adopt`) only when the build it
was judged under has today's whole-file hash: then every definition it reads
is the one it was judged under, so the adopted hash is the one it would have
been given. Otherwise it goes to revalidate once and takes `semantic_reads`
when an analysis proposes it again.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LITERAL = re.compile(r"'(?:[^']|'')*'")
_MODEL_PARTS = ("dimensions", "measures", "calculated_measures")


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _words(text: str) -> set[str]:
    """The identifiers a query or expression names, string literals left out,
    lower-cased (SQL names are case-insensitive)."""
    return {w.lower() for w in _WORD.findall(_LITERAL.sub("''", str(text or "")))}


def graph(model_file: dict, sql_views: dict[str, str], who: str, tables) -> dict[str, dict]:
    """The definitions as nodes {key: {"d": digest, "deps": [keys]}}; a
    metric's node also lists its `models`. Pure: built from semantic.yaml as
    loaded, the model views' SQL (semantic.VIEWS), the character columns
    (semantic._WHO) and the prep's table names."""
    from .semantic import CROSS_KEY, OURS, VIEWS_KEY

    g: dict[str, dict] = {}
    for t in tables:
        g[f"relation:{t}"] = {"d": _digest("table"), "deps": []}
    for name, body in sql_views.items():
        names = (_words(body) & ({*tables, *sql_views})) - {name}
        g[f"relation:{name}"] = {"d": _digest({"sql": body, "who": who if "{who}" in body else None}),
                                 "deps": sorted(f"relation:{n}" for n in names)}
    models = {k: v for k, v in model_file.items() if k not in OURS and isinstance(v, dict)}
    owner: dict[str, str] = {}
    for m, cfg in models.items():
        g[f"model:{m}"] = {"d": _digest({k: v for k, v in cfg.items() if k not in _MODEL_PARTS}),
                           "deps": [f"relation:{cfg.get('table')}"]}
        for d, spec in (cfg.get("dimensions") or {}).items():
            g[f"dim:{m}.{d}"] = {"d": _digest(spec), "deps": [f"model:{m}"]}
        for section in ("measures", "calculated_measures"):
            for n in cfg.get(section) or {}:
                owner[n] = m
    for m, cfg in models.items():
        own = {n for s in ("measures", "calculated_measures") for n in cfg.get(s) or {}}
        for section in ("measures", "calculated_measures"):
            for n, spec in (cfg.get(section) or {}).items():
                # A calculated measure's expression names measures; a measure's, columns only.
                reads = set() if section == "measures" else (_words((spec or {}).get("expr")) & own) - {n}
                g[f"metric:{n}"] = {"d": _digest({"section": section, "def": spec}), "models": [m],
                                    "deps": sorted([f"model:{m}", *(f"metric:{r}" for r in reads)])}
    for n, spec in (model_file.get(CROSS_KEY) or {}).items():
        used = sorted(_words(spec.get("expr")) & set(owner))
        where = (((spec.get("metadata") or {}).get("eligibility") or {}).get("where") or {})
        on = sorted({*(owner[u] for u in used), *(where if isinstance(where, dict) else {})})
        g[f"metric:{n}"] = {"d": _digest({"section": CROSS_KEY, "def": spec}), "models": on,
                            "deps": sorted([*(f"metric:{u}" for u in used), *(f"model:{mo}" for mo in on)])}
    for n, spec in (model_file.get(VIEWS_KEY) or {}).items():
        metrics = [x for x in spec.get("metrics") or [] if isinstance(x, str)]
        on = {mo for x in metrics for mo in (g.get(f"metric:{x}") or {}).get("models") or []}
        dims = {spec.get("entity"), *(spec.get("dimensions") or []),
                *((w or {}).get("dimension") for w in spec.get("where") or [] if isinstance(w, dict))}
        g[f"view:{n}"] = {"d": _digest(spec), "deps": sorted(
            [*(f"metric:{x}" for x in metrics), *_dims(g, on, dims)])}
    return g


def _dims(g: dict, models, names) -> list[str]:
    """The dimension nodes `names` stand for in `models`; one no model has is
    kept as a node no graph holds (dim:?.<name>), so it never resolves."""
    out = []
    for d in names:
        if not isinstance(d, str):
            continue
        got = [f"dim:{m}.{d}" for m in sorted(models) if f"dim:{m}.{d}" in g]
        out += got or [f"dim:?.{d}"]
    return out


@functools.cache
def _current(path: str, mtime_ns: int) -> dict[str, dict]:
    from . import TABLES, semantic

    return graph(semantic._file(), semantic.VIEWS, semantic._WHO, TABLES)


def current() -> dict[str, dict]:
    """Today's definitions (semantic.yaml as loaded, the model views)."""
    from . import semantic

    p = semantic.MODEL_FILE
    return _current(str(p), p.stat().st_mtime_ns)


def names_of(finding: dict, g: dict | None = None) -> list[str] | None:
    """The names a finding's evidence reads in `g` (today's definitions by
    default), or None when they can't be told: no SQL and no metric query,
    a metric query that doesn't resolve, SQL that names no known relation."""
    g = current() if g is None else g
    sql = str(finding.get("sql") or "")
    mq = finding.get("metric_query")
    if not sql.strip() and not mq:
        return None
    words = _words(sql)
    out = {k for w in words for k in (f"relation:{w}", f"view:{w}") if k in g}
    if mq:
        if not isinstance(mq, dict) or not isinstance(mq.get("metrics"), list) or not mq["metrics"]:
            return None
        metrics = [f"metric:{m}" for m in mq["metrics"]]
        if any(not isinstance(m, str) or f"metric:{m}" not in g for m in mq["metrics"]):
            return None
        models = {mo for m in metrics for mo in g[m].get("models") or []}
        dims = {*(mq.get("group_by") or []), *((w or {}).get("dimension") for w in mq.get("where") or []
                                               if isinstance(w, dict))}
        if mq.get("grain") or mq.get("since") or mq.get("until"):
            dims.add("time")
        resolved = _dims(g, models, dims)
        if any(k not in g for k in resolved):
            return None
        out |= {*metrics, *resolved}
    return sorted(out) or None


def hash_of(names, g: dict | None = None) -> str:
    """The hash over every node `names` reach in `g` (today's by default); a
    node `g` doesn't have counts as `missing`, so a definition removed since
    reads as changed."""
    g = current() if g is None else g
    seen: dict[str, str] = {}
    todo = list(names)
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        node = g.get(k)
        seen[k] = node["d"] if node else "missing"
        todo += (node or {}).get("deps") or []
    return _digest(seen)[:16]


def of(finding: dict) -> dict:
    """The `semantic_reads` a finding is kept with: its names and their hash
    under today's definitions, or {"names": None} (the whole-file hash
    decides) when its names can't be told."""
    names = names_of(finding)
    return {"names": names, "hash": hash_of(names)} if names else {"names": None}


def changed(finding: dict | None, judged: dict, now: dict) -> bool:
    """Whether the semantic definitions a finding reads moved since the build
    it was judged under (`judged`) and today's (`now`): its own hash when it
    has one, else the whole-file hash."""
    names = ((finding or {}).get("semantic_reads") or {}).get("names")
    if names:
        return finding["semantic_reads"].get("hash") != hash_of(names)
    return judged.get("semantic_hash") != now["semantic_hash"]


def adopt(finding: dict, judged: dict | None, now: dict) -> bool:
    """A finding kept before `semantic_reads`: give it its names and hash
    when that is exact, i.e. the build it was judged under has today's
    whole-file hash (every definition it reads is the one it was judged
    under, so today's hash is the one it would have been given). Otherwise
    leave it: the whole-file hash decides, it goes to revalidate, and the
    analysis that proposes it again gives it its names. True if adopted."""
    if "semantic_reads" in finding or not judged or judged.get("semantic_hash") != now.get("semantic_hash"):
        return False
    finding["semantic_reads"] = of(finding)
    return True
