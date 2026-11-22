"""The grader: what the analysis kept, against the scenario's truth.

Deterministic checks, in this order:

  finished      the run reached its end (the synthesis accepted). A run that
                didn't is an `error`: the client or the harness, not the
                analysis, and nothing else is graded;
  queries       every kept finding's saved query runs again on a fresh
                build of the scenario's database, outside the server, and
                gives the values the finding was judged on (`expect`);
  must_find     each expected line is there: one kept finding, nudge or
                pattern whose words match every group of the rule;
  must_not_claim  no kept line says what the truth rules out (for the
                control and the coverage case, the unsupported claims).

A rule is data (scenarios/*.json): `all` is a list of groups, each a list of
regular expressions (case-insensitive); a line matches when each group has
one match in it. `unless` lists expressions that excuse a line (it says the
records are missing, there was a break...). `in`: "any" reads every kept
line (a finding's title, text and action; a nudge's text, gist, where,
blocker and next step; a pattern), "actions" only what tells the player to
do something (the nudges and the findings' actions). `kinds` limits a rule
to some kinds of line (["finding"]: only a finding the judge verified, never
a nudge or a pattern line written without a query behind it).

Words are a blunt instrument on purpose: the same answer grades the same
way every time, and a miss shows the exact line. The optional judge
(judge.py) reads the wording; it never changes this verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Line:
    kind: str       # finding, nudge, pattern
    key: str        # a finding's key, or the line's position
    text: str       # everything it says
    action: str     # what it tells the player to do ("" for an observation or a pattern)

    def ref(self) -> str:
        return f"{self.kind} {self.key}: {self.text[:240]}"


def _join(*parts) -> str:
    return " ".join(str(p) for p in parts if p not in (None, "")).strip()


def lines(analysis: dict) -> list[Line]:
    """Every line the kept analysis shows the player."""
    out = []
    for k, f in sorted((analysis.get("findings") or {}).items()):
        a = f.get("action") or {}
        act = _join(*(a.get(p) for p in ("do", "where", "blocked_by", "next_step"))) if isinstance(a, dict) else ""
        out.append(Line("finding", k, _join(f.get("title"), f.get("text"), act), act))
    for i, n in enumerate(analysis.get("nudges") or []):
        t = _join(*(n.get(p) for p in ("text", "gist", "where", "blocked_by", "next_step")))
        out.append(Line("nudge", str(i + 1), t, t))
    for i, p in enumerate(analysis.get("patterns") or []):
        out.append(Line("pattern", str(i + 1), _join(p.get("text")), ""))
    return out


def _hit(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def matches(rule: dict, ls: list[Line]) -> list[Line]:
    out = []
    kinds = rule.get("kinds")
    for ln in ls:
        if kinds and ln.kind not in kinds:
            continue
        text = ln.action if rule.get("in", "any") == "actions" else ln.text
        if not text:
            continue
        if all(_hit(g, text) for g in rule["all"]) and not _hit(rule.get("unless") or [], text):
            out.append(ln)
    return out


def rerun(analysis: dict, judge, metrics=None) -> list[dict]:
    """Each kept finding's query on `judge` (a ReadOnly over a fresh build):
    it runs, its placeholders are still there, its values match `expect`. A
    finding made from a metric (`engine: duckdb`, query_metric's SQL over
    the semantic views) re-runs on `metrics`, the reader without the table
    list, as the server's own recheck does (analysis.recheck): the judge's
    refuses the views, and two good findings failed on it (2026-11-22)."""
    from wowstats.analysis import verify

    out = []
    for k, f in sorted((analysis.get("findings") or {}).items()):
        on = metrics if f.get("engine") == "duckdb" and metrics is not None else judge
        got, why = verify.recheck(f, on)
        if got is None:
            out.append({"key": k, "ok": False, "why": why})
            continue
        off = [f"{c}: {got['row'].get(c)!r} on re-run, judged {v!r}" for c, v in (f.get("expect") or {}).items()
               if c not in got["row"] or not verify.close(got["row"][c], v)]
        out.append({"key": k, "ok": not off, "why": "; ".join(off)})
    return out


def grade(sc: dict, analysis: dict | None, run: dict, reruns: list[dict] | None) -> dict:
    """The verdict: pass, fail (the analysis is wrong) or error (the run
    never finished: the client or the harness)."""
    checks = [{"check": "finished", "ok": bool(run.get("done")),
               "detail": "" if run.get("done") else (run.get("error") or "the run stopped before its synthesis")}]
    if not run.get("done") or analysis is None:
        return {"verdict": "error", "checks": checks}
    bad = [r for r in reruns or [] if not r["ok"]]
    checks.append({"check": "queries", "ok": not bad,
                   "detail": f"{len(reruns or []) - len(bad)} of {len(reruns or [])} re-ran and matched",
                   "evidence": [f"{r['key']}: {r['why']}" for r in bad]})
    ls = lines(analysis)
    truth = sc["truth"]
    for rule in truth.get("must_find") or []:
        hit = matches(rule, ls)
        checks.append({"check": f"must_find {rule['id']}", "ok": bool(hit), "detail": rule.get("about", ""),
                       "evidence": [h.ref() for h in hit[:5]]})
    for rule in truth.get("must_not_claim") or []:
        hit = matches(rule, ls)
        checks.append({"check": f"must_not_claim {rule['id']}", "ok": not hit, "detail": rule.get("about", ""),
                       "evidence": [h.ref() for h in hit[:5]]})
    return {"verdict": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks}
