"""The knowledge base's schema: what every sourced record must carry, and
the domains it may cite (data/kb/sources.json).

A record is a rule (data/kb/rules.json, data/kb/research/*.json `rules`), a
statement or a contradiction (research files' `statements` and
`contradictions`). Each carries `source_url`, `source_type`, `date`,
`confidence` (high | medium | low), `scope` (forever | classic_fallback) and
`last_verified` (YYYY-MM-DD). Every URL it cites (`source_url` and the
`sources` list) must be on a listed domain, and its confidence can't be above
the best of its sources' `max_confidence`: a booster site alone never makes a
statement more than low.

Two optional fields give a statement a lifetime (tools/kb/lifecycle.py, the
weekly job): `expires` (YYYY-MM-DD, the last day it holds; it leaves the
tree a week later) and `superseded_by` (the id of the statement that
replaces it; it leaves the tree once that one is there). A statement naming
another in `supersedes` does the same from the other side.

Lists and builds (a class-meta statement whose `aspect` is enchants,
consumables or stats; a level-60 build; a class's levelling path) are an
aggregate: two sources or more, or `single_source` set with low confidence
(owner, 2026-11-20). An official Blizzard page counts alone. multi_source()
says why one isn't; tests/test_kb_schema.py checks the shipped files and the
refresh job's gates (tools/kb/gates.py) every change.

Two ways a citation counts for less than a source:

- A domain flagged `corroboration_only` in sources.json (JudgeHype: it
  mostly relays others in French; owner, 2026-11-20) may be cited next to
  another source, never as a record's `source_url`, and it never counts
  toward the two-source rule or the confidence cap.
- `relays` ({cited domain: domain it relays}) says one cited site only
  passes on another's work (JudgeHype's raid tier list is Wowhead's,
  translated): the two are one origin, and a record resting on one origin
  this way is `single_source` at low confidence unless another site backs it.

The loader (wowstats.analysis.kb) leaves out a record that fails, and says
so; tests/test_kb_schema.py fails the build on any; the refresh job
(tools/kb/refresh.py) refuses a model's proposal that fails.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

FIELDS = ("source_url", "source_type", "date", "confidence", "scope", "last_verified")
SCOPES = ("forever", "classic_fallback")
CONFIDENCE = ("low", "medium", "high")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Days a statement stays past its `expires` before the weekly job drops it.
GRACE_DAYS = 7
# A class-meta statement with one of these aspects is a list item (docs/kb.md, "Sources and licensing").
LIST_ASPECTS = ("enchants", "consumables", "stats")


def load_allow(path: Path | None = None) -> dict:
    """{domain: {type, max_confidence}} from data/kb/sources.json."""
    if path is None:
        from .kb import data_file

        path = data_file("kb/sources.json")
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("domains") or {}


def domain(url: str) -> str:
    return (urllib.parse.urlsplit(url or "").hostname or "").lower()


def urls(rec: dict) -> list[str]:
    out = [rec.get("source_url")] + list(rec.get("sources") or [])
    seen, keep = set(), []
    for u in out:
        if isinstance(u, str) and u and u not in seen:
            seen.add(u)
            keep.append(u)
    return keep


def corroboration_only(d: str, allow: dict) -> bool:
    return bool((allow.get(d) or {}).get("corroboration_only"))


def counted(rec: dict, allow: dict) -> list[str]:
    """The URLs that count as sources: every cited one but a corroboration-only domain's."""
    return [u for u in urls(rec) if not corroboration_only(domain(u), allow)]


def origins(rec: dict, allow: dict | None = None) -> set[str]:
    """The distinct sites a record rests on: its counted domains, a domain it
    `relays` counted as the site it relays."""
    relays = rec.get("relays") if isinstance(rec.get("relays"), dict) else {}
    us = counted(rec, allow) if allow is not None else urls(rec)
    return {relays.get(domain(u), domain(u)) for u in us}


def cap(conf: str, allowed: str) -> str:
    """The lower of two confidences."""
    return CONFIDENCE[min(CONFIDENCE.index(conf), CONFIDENCE.index(allowed))]


def best_cap(rec: dict, allow: dict) -> str | None:
    caps = [allow[domain(u)]["max_confidence"] for u in counted(rec, allow) if domain(u) in allow]
    return max(caps, key=CONFIDENCE.index) if caps else None


def check(rec: dict, allow: dict, strict_type: bool = True) -> list[str]:
    """Why a record is refused ([] when it is fine). strict_type: the
    source_type must be one of sources.json's types (the research files);
    rules.json's older rules describe theirs in words."""
    errs = [f"no {k}" for k in FIELDS if not rec.get(k)]
    if rec.get("scope") and rec["scope"] not in SCOPES:
        errs.append(f"scope {rec['scope']!r} is not {' or '.join(SCOPES)}")
    if rec.get("confidence") and rec["confidence"] not in CONFIDENCE:
        errs.append(f"confidence {rec['confidence']!r} is not one of {', '.join(CONFIDENCE)}")
    if rec.get("last_verified") and not DAY.match(str(rec["last_verified"])):
        errs.append(f"last_verified {rec['last_verified']!r} is not YYYY-MM-DD")
    for u in urls(rec):
        if not u.startswith("https://"):
            errs.append(f"{u} is not an https URL")
        elif domain(u) not in allow:
            errs.append(f"{domain(u)} is not on the source allow-list (data/kb/sources.json)")
    if strict_type and rec.get("source_type"):
        types = {v["type"] for v in allow.values()}
        if rec["source_type"] not in types:
            errs.append(f"source_type {rec['source_type']!r} is not one of {', '.join(sorted(types))}")
    if rec.get("expires") and not DAY.match(str(rec["expires"])):
        errs.append(f"expires {rec['expires']!r} is not YYYY-MM-DD")
    if rec.get("source_url") and corroboration_only(domain(rec["source_url"]), allow):
        errs.append(f"{domain(rec['source_url'])} only corroborates: cite it next to a source, not as source_url")
    if "relays" in rec:
        rel = rec["relays"]
        cited = {domain(u) for u in urls(rec)}
        if not (isinstance(rel, dict) and rel and all(k in cited and v in allow for k, v in rel.items())):
            errs.append(f"relays {rel!r} is not {{cited domain: allow-listed domain}}")
        elif (len(origins(rec, allow)) < 2 and not (rec.get("single_source") and rec.get("confidence") == "low")
              and not any((allow.get(domain(u)) or {}).get("type") == "official" for u in urls(rec))):
            errs.append("its sources relay one another, so it rests on one origin: single_source at low confidence")
    for k in ("superseded_by", "supersedes"):
        if k in rec and not (isinstance(rec[k], str) and rec[k].strip()):
            errs.append(f"{k} is not a statement id")
    top = best_cap(rec, allow)
    if top and rec.get("confidence") in CONFIDENCE and CONFIDENCE.index(rec["confidence"]) > CONFIDENCE.index(top):
        errs.append(f"confidence {rec['confidence']} is above what its sources allow ({top})")
    return errs


def check_file(doc: dict, allow: dict, strict_type: bool = True) -> list[str]:
    """Every refusal in a rules or research file, as "<where>: <why>"."""
    out = []
    for name, r in ((doc.get("rules") or {}) if isinstance(doc, dict) else {}).items():
        errs = check(r, allow, strict_type) if isinstance(r, dict) else ["not an object"]
        out += [f"rule {name}: {e}" for e in errs]
    shape = class_guide_shape if isinstance(doc, dict) and doc.get("lane") == "classes" else None
    for key in ("statements", "contradictions"):
        for i, r in enumerate(doc.get(key) or []):
            where = f"{key[:-1]} {r.get('id') or i}" if isinstance(r, dict) else f"{key[:-1]} {i}"
            out += [f"{where}: {e}" for e in (check(r, allow) if isinstance(r, dict) else ["not an object"])]
            if isinstance(r, dict) and not (r.get("text") or "").strip():
                out.append(f"{where}: no text")
            if shape and isinstance(r, dict):
                out += [f"{where}: {e}" for e in shape(r, key)]
    # The weekly digests (wowstats.news; `digest` in a pass's own file,
    # `digests` in data/kb/news.json): each line sourced like a statement;
    # an event's day, when it has one, a real day.
    weeks = ([doc.get("digest")] + list(doc.get("digests") or [])) if isinstance(doc, dict) else []
    items = [it for w in weeks if isinstance(w, dict) for it in w.get("items") or []]
    for i, it in enumerate(items):
        where = f"digest {it.get('id') or i}" if isinstance(it, dict) else f"digest {i}"
        if not isinstance(it, dict):
            out.append(f"{where}: not an object")
            continue
        out += [f"{where}: {e}" for e in check(it, allow)]
        if not (it.get("text") or "").strip():
            out.append(f"{where}: no text")
        ev = it.get("event") or {}
        if ev and (not ev.get("key") or not ev.get("name")):
            out.append(f"{where}: an event needs a key and a name")
        if ev.get("starts") and not DAY.match(str(ev["starts"])):
            out.append(f"{where}: starts {ev['starts']!r} is not YYYY-MM-DD")
    # A sourced role build (class-meta: a tank or healer spec's level-60 talents).
    for i, b in enumerate((doc.get("builds") or []) if isinstance(doc, dict) else []):
        where = f"build {i}"
        out += [f"{where}: {e}" for e in (check(b, allow) if isinstance(b, dict) else ["not an object"])]
    for i, q in enumerate(doc.get("open_questions") or []):
        if not isinstance(q, dict) or not (q.get("text") or "").strip():
            out.append(f"open question {i}: no text")
    return out


def class_guide_shape(rec: dict, key: str) -> list[str]:
    """What the class guide (wowstats.classguide, data/kb/research/classes.json)
    needs of a record: its class; a statement also its topic and level range.
    A model statement that replaced one without them (2026-11-22, #99) dropped
    the class's 10-19 talent order from the guide."""
    from wowstats import classguide  # the guide reads the KB, not the other way round

    errs = []
    if rec.get("class") not in classguide.CLASSES:
        errs.append(f"class {rec.get('class')!r} is not one of the guide's classes")
    if key == "statements":
        if rec.get("topic") not in classguide.TOPICS:
            errs.append(f"topic {rec.get('topic')!r} is not one of the guide's topics")
        lv = rec.get("levels")
        if not (isinstance(lv, list) and len(lv) == 2 and all(isinstance(x, int) for x in lv)
                and 1 <= lv[0] <= lv[1] <= classguide.MAX_LEVEL):
            errs.append(f"levels {lv!r} is not [from, to] within 1-{classguide.MAX_LEVEL}")
    return errs


def expired(rec: dict, today) -> bool:
    """Past its `expires` by more than GRACE_DAYS: the loader leaves it out
    and the weekly job (tools/kb/lifecycle.py) drops it."""
    from datetime import date, timedelta

    v = str(rec.get("expires") or "")
    if not DAY.match(v):
        return False
    try:
        return date.fromisoformat(v) + timedelta(days=GRACE_DAYS) < today
    except ValueError:
        return False


def retired_ids(statements: list[dict], today) -> set:
    """The ids the loader leaves out until the weekly job removes them: named
    by another's `supersedes`, naming a present replacement in
    `superseded_by`, or expired."""
    ids = {r.get("id") for r in statements}
    gone = {r["supersedes"] for r in statements if r.get("supersedes")}
    return gone | {r.get("id") for r in statements if (r.get("superseded_by") in ids) or expired(r, today)}


def multi_source(rec: dict, allow: dict) -> str:
    """Why a list item or a build breaks the multi-source rule ("" when it
    holds): two distinct sources, or `single_source` with low confidence, or
    an official page."""
    us = counted(rec, allow)
    if any((allow.get(domain(u)) or {}).get("type") == "official" for u in us):
        return ""
    if rec.get("single_source"):
        return "" if rec.get("confidence") == "low" else "single_source but its confidence isn't low"
    if len(us) >= 2 and rec.get("relays") and len(origins(rec, allow)) < 2:
        return "its sources are one origin (one relays the other) and no single_source"
    return "" if len(us) >= 2 else "a list item or build with one source (corroboration excluded) and no single_source"


def is_list_or_build(rec: dict, key: str) -> bool:
    """Whether the multi-source rule applies: `key` is the record's list in
    its file (statements, builds, paths)."""
    return key in ("builds", "paths") or (key == "statements" and rec.get("aspect") in LIST_ASPECTS)
