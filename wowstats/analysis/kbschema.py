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


def cap(conf: str, allowed: str) -> str:
    """The lower of two confidences."""
    return CONFIDENCE[min(CONFIDENCE.index(conf), CONFIDENCE.index(allowed))]


def best_cap(rec: dict, allow: dict) -> str | None:
    caps = [allow[domain(u)]["max_confidence"] for u in urls(rec) if domain(u) in allow]
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
    for key in ("statements", "contradictions"):
        for i, r in enumerate(doc.get(key) or []):
            where = f"{key[:-1]} {r.get('id') or i}" if isinstance(r, dict) else f"{key[:-1]} {i}"
            out += [f"{where}: {e}" for e in (check(r, allow) if isinstance(r, dict) else ["not an object"])]
            if isinstance(r, dict) and not (r.get("text") or "").strip():
                out.append(f"{where}: no text")
    for i, q in enumerate(doc.get("open_questions") or []):
        if not isinstance(q, dict) or not (q.get("text") or "").strip():
            out.append(f"open question {i}: no text")
    return out
