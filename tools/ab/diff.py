"""The A/B check's comparison: two driver outputs (tools/ab/driver.py) in,
one readable report out, a section per feature.

What counts as a change is what the player would see change: numbers,
names, reasons, a recommendation, a missing or new answer. What doesn't:
the run's own leftovers (temporary paths, local ports, the source address
an export rewrites), normalised away before comparing (normalise()).

What counts as a regression, listed first: an answer that fails in B and
didn't in A (HTTP 4xx/5xx, an MCP error, a driver step that broke), an
answer that is empty in B and wasn't in A, a NaN, Infinity or "undefined"
that A didn't have, an answer A gave that B doesn't, and every request B
made that the recording couldn't answer (a recording miss: B asked the
stores something A never did, so B's answer there is untested).

Standard library only."""

from __future__ import annotations

import html as htmlmod
import json
import math
import re
from pathlib import Path

# Leftovers of a run, never of the version: (pattern, replacement).
VOLATILE = [
    (re.compile(r"/(?:private/)?(?:tmp|var/folders/[^\s\"']+?)/ab-work-[A-Za-z0-9_]+"), "<work>"),
    (re.compile(r"ab-work-[A-Za-z0-9_]+"), "<work>"),
    (re.compile(r"(127\.0\.0\.1|localhost|host\.docker\.internal):\d{2,5}"), r"\1:<port>"),
    # wowstats.SOURCE_URL: the public export rewrites it, the code is the same.
    (re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/(?:wow-stats|wow-mcp)\b"), "<source>"),
    (re.compile(r"\bb\d{10}\b"), "b<build>"),
]
SKIP_FILES = {"_run.json", "_proxy.json", "_target.json"}
ID_KEYS = ("slot", "key", "id", "item_id", "quest_id", "name", "char", "tool", "level", "zone", "day", "date",
           "week", "title", "tier", "spec", "lane", "kind", "label")
BAD_TEXT = re.compile(r"(?<![A-Za-z])(NaN|nan|Infinity|-Infinity|undefined)(?![A-Za-z])")
MAX_LINES_PER_FILE = 60

# (section, file patterns relative to the run: fnmatch-style prefixes). The
# first that matches a file takes it.
FEATURES: list[tuple[str, tuple[str, ...]]] = [
    ("Gear and upgrades", ("api/gear", "mcp/gear", "mcp/gear_options")),
    ("Plan", ("plan/", "api/plan.", "mcp/plan_session", "mcp/next_step", "mcp/check_plan", "mcp/how_did_my_plan_go",
              "mcp/dungeon_options")),
    ("Gold, mount and market", ("mcp/mount_goal", "api/prices", "api/market", "mcp/prices", "mcp/market")),
    ("Talents, class and meta", ("api/talents", "api/meta", "mcp/talent_changes", "mcp/class_guide", "mcp/class_meta")),
    ("Advice: next moves and sanity check", ("mcp/next_moves", "mcp/sanity_check")),
    ("Pace, levels and sessions", ("api/char.", "api/overview", "mcp/character_summary", "mcp/levels", "mcp/sessions",
                                   "mcp/zones", "mcp/guide_steps", "mcp/route_reference", "mcp/deaths")),
    ("Journal and timeline", ("api/journal", "api/timeline", "api/snapshot-")),
    ("Dungeons and loot", ("api/dungeons", "api/loot", "mcp/dungeons")),
    ("People", ("api/people", "mcp/players", "mcp/people")),
    ("Play analysis", ("api/analysis", "mcp/play_analysis", "mcp/analys", "mcp/play_sql", "mcp/list_metrics",
                       "mcp/query_metric", "mcp/propose_finding", "mcp/whats_new")),
    ("News", ("api/news", "mcp/news")),
    ("Knowledge base", ("mcp/kb_",)),
    ("MCP tool list", ("mcp/_tools",)),
    ("Page and config", ("api/page", "api/config", "api/chars")),
    ("Public build", ("public/",)),
    ("Zogzog (sealed full content)", ("zogzog/",)),
]
# Cross-cutting: every change whose path names one of these, whatever the file.
TOPICS = {"Professions": re.compile(r"profession|skill_?cap|trainer|craft", re.I)}


def feature_of(rel: str) -> str:
    for title, pats in FEATURES:
        if any(rel.startswith(p) for p in pats):
            return title
    return "Other"


# ------------------------------------------------------------------ normalise


def _norm_str(s: str) -> str:
    for rx, rep in VOLATILE:
        s = rx.sub(rep, s)
    return s


def normalise(obj):
    if isinstance(obj, str):
        return _norm_str(obj)
    if isinstance(obj, list):
        return [normalise(x) for x in obj]
    if isinstance(obj, dict):
        return {_norm_str(k): normalise(v) for k, v in obj.items()}
    return obj


def html_text(s: str) -> list[str]:
    """What a reader sees of a page: the text, a line per block, no markup,
    scripts or styles (their code changes are not what the page says)."""
    s = re.sub(r"(?is)<(script|style|template)\b.*?</\1>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?i)<(br|/p|/div|/li|/h\d|/tr|/section|/header|/footer|/table|/ul|/ol|/dt|/dd)\b[^>]*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = htmlmod.unescape(s)
    return [ln for ln in (re.sub(r"\s+", " ", x).strip() for x in s.split("\n")) if ln]


def load(path: Path):
    """(kind, value): json, html (text lines) or text."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        try:
            v = json.loads(raw)
        except ValueError:
            return "text", _norm_str(raw).splitlines()
        if isinstance(v, dict) and set(v) >= {"status"} and "text" in v and str(v.get("ctype", "")).endswith("html"):
            return "json", normalise({**v, "text": html_text(v["text"])})
        return "json", normalise(v)
    if path.suffix == ".html":
        return "html", html_text(_norm_str(raw))
    return "text", _norm_str(raw).splitlines()


# ------------------------------------------------------------------ compare


def _ident(items: list) -> str | None:
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    for k in ID_KEYS:
        vals = [x.get(k) for x in items]
        scalar = all(v is not None and not isinstance(v, (dict, list)) for v in vals)
        if scalar and len(set(map(str, vals))) == len(vals):
            return k
    return None


def _short(v, n: int = 160) -> str:
    s = json.dumps(v, ensure_ascii=False, sort_keys=True) if not isinstance(v, str) else json.dumps(v,
                                                                                                    ensure_ascii=False)
    return s if len(s) <= n else s[: n - 1] + "…"


def compare(a, b, path: str = "") -> list[dict]:
    """Changes from a to b: [{path, kind: changed|added|removed, a, b}]."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}.{k}" if path else str(k)
            if k not in a:
                out.append({"path": p, "kind": "added", "b": b[k]})
            elif k not in b:
                out.append({"path": p, "kind": "removed", "a": a[k]})
            else:
                out += compare(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list):
        ka, kb = _ident(a), _ident(b)
        if ka and ka == kb:
            ma = {str(x[ka]): x for x in a}
            mb = {str(x[ka]): x for x in b}
            out = []
            for k in list(ma) + [k for k in mb if k not in ma]:
                p = f"{path}[{ka}={k}]"
                if k not in mb:
                    out.append({"path": p, "kind": "removed", "a": ma[k]})
                elif k not in ma:
                    out.append({"path": p, "kind": "added", "b": mb[k]})
                else:
                    out += compare(ma[k], mb[k], p)
            if [str(x[ka]) for x in a if str(x[ka]) in mb] != [str(x[kb]) for x in b if str(x[kb]) in ma]:
                out.append({"path": f"{path}[order by {ka}]", "kind": "changed",
                            "a": [x[ka] for x in a], "b": [x[kb] for x in b]})
            return out
        return _compare_seq(a, b, path)
    if a == b or (isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b)):
        return []
    return [{"path": path or "(value)", "kind": "changed", "a": a, "b": b}]


def _compare_seq(a: list, b: list, path: str) -> list[dict]:
    """A list without an id: aligned as a sequence, so one item inserted
    reads as one item added, not every later item changed. Items that took
    each other's place one for one are compared field by field."""
    import difflib

    ja = [json.dumps(x, sort_keys=True, ensure_ascii=False) for x in a]
    jb = [json.dumps(x, sort_keys=True, ensure_ascii=False) for x in b]
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=ja, b=jb, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                out += compare(a[i1 + k], b[j1 + k], f"{path}[{j1 + k}]")
            continue
        out += [{"path": f"{path}[{i}]", "kind": "removed", "a": a[i]} for i in range(i1, i2)]
        out += [{"path": f"{path}[{j}]", "kind": "added", "b": b[j]} for j in range(j1, j2)]
    return out


def compare_lines(a: list[str], b: list[str]) -> list[dict]:
    import difflib

    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        out.append({"path": f"line {i1 + 1}", "kind": {"replace": "changed", "delete": "removed",
                                                         "insert": "added"}[tag],
                    "a": "\n".join(a[i1:i2]) or None, "b": "\n".join(b[j1:j2]) or None})
    return out


def _str_change(a: str, b: str, ctx: int = 50) -> str:
    """Two long strings: only where they differ, with a little context."""
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[len(a) - 1 - j] == b[len(b) - 1 - j]:
        j += 1
    pre = ("…" if i > ctx else "") + a[max(0, i - ctx):i]
    post = a[len(a) - j:len(a) - j + ctx] + ("…" if j > ctx else "")
    cut = lambda s: s if len(s) <= 300 else s[:299] + "…"  # noqa: E731
    return f'"{pre}[-{cut(a[i:len(a) - j])}-]{{+{cut(b[i:len(b) - j])}+}}{post}"'


def describe(c: dict) -> str:
    if c["kind"] == "added":
        return f"`{c['path']}` new: {_short(c['b'])}"
    if c["kind"] == "removed":
        return f"`{c['path']}` gone (was {_short(c['a'])})"
    a, b = c["a"], c["b"]
    if isinstance(a, str) and isinstance(b, str) and max(len(a), len(b)) > 120:
        return f"`{c['path']}`: {_str_change(a, b)}"
    delta = ""
    numbers = all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (a, b))
    if numbers and c["path"] != "status":
        d = b - a
        delta = f" ({'+' if d > 0 else ''}{round(d, 4)})"
    return f"`{c['path']}`: {_short(a)} → {_short(b)}{delta}"


# ------------------------------------------------------------------ flags


def _empty(v) -> bool:
    if v in (None, "", [], {}):
        return True
    if isinstance(v, dict):
        if "body" in v and "status" in v:
            return _empty(v["body"]) if "text" not in v else not v.get("text")
        if "structured" in v or "content" in v:
            return _empty(v.get("structured")) and _empty(v.get("content"))
    return False


def _bad_values(v, path: str = "") -> list[str]:
    out = []
    if isinstance(v, float) and not math.isfinite(v):
        out.append(f"{path or '(value)'} = {v}")
    elif isinstance(v, str):
        m = BAD_TEXT.search(v)
        if m:
            out.append(f"{path or '(value)'} has {m.group(1)!r}: {_short(v, 100)}")
    elif isinstance(v, dict):
        for k, x in v.items():
            if k in ("args",):
                continue
            out += _bad_values(x, f"{path}.{k}" if path else str(k))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            out += _bad_values(x, f"{path}[{i}]")
    return out


def _failed(v) -> str | None:
    if not isinstance(v, dict):
        return None
    st = v.get("status")
    if isinstance(st, int) and st >= 400:
        body = v.get("body")
        why = body.get("error") if isinstance(body, dict) else (v.get("text") or "")[:120]
        return f"HTTP {st}: {why}"
    if v.get("is_error"):
        c = v.get("content") or v.get("exception") or ""
        return f"MCP error: {_short(c, 200)}"
    if "error" in v and len(v) <= 2 and isinstance(v["error"], str):
        return f"error: {v['error'][:200]}"
    return None


def _absent(v) -> bool:
    """A route or tool this version doesn't have (404 on a route)."""
    return isinstance(v, dict) and v.get("status") == 404 and "no events" not in json.dumps(v.get("body") or "")


def flags(rel: str, a, b) -> list[str]:
    out = []
    fa, fb = _failed(a), _failed(b)
    if fb and not fa and a is not None:
        out.append(f"{'gone now' if _absent(b) else 'fails now'}: {fb}")
    if a is not None and b is not None and not fb and _empty(b) and not _empty(a):
        out.append("empty now (A had an answer)")
    bad_a = {x.split(" ")[0] for x in _bad_values(a)} if a is not None else set()
    out += [f"bad value: {x}" for x in _bad_values(b) if x.split(" ")[0] not in bad_a]
    return out


# ------------------------------------------------------------------ report


def files(run: Path) -> dict[str, Path]:
    return {p.relative_to(run).as_posix(): p for p in sorted(run.rglob("*"))
            if p.is_file() and p.relative_to(run).as_posix() not in SKIP_FILES}


def run_meta(run: Path) -> dict:
    out = {}
    for name in ("_run.json", "_proxy.json"):
        p = run / name
        if p.exists():
            try:
                out[name] = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                out[name] = {}
    return out


def diff_runs(a_dir: Path, b_dir: Path) -> dict:
    fa, fb = files(a_dir), files(b_dir)
    result: dict = {"files": {}, "regressions": [], "only_a": [], "only_b": []}
    for rel in sorted(set(fa) | set(fb)):
        va = load(fa[rel])[1] if rel in fa else None
        vb = load(fb[rel])[1] if rel in fb else None
        if rel not in fb:
            result["only_a"].append(rel)
            if not _absent(va) and not _failed(va):
                result["regressions"].append({"file": rel, "what": "answer gone (A had it, B has no such output)"})
            continue
        if rel not in fa:
            result["only_b"].append(rel)
        kind = "json" if isinstance(vb, (dict, list)) and (va is None or isinstance(va, (dict, list))) else "lines"
        if va is None:
            changes = []
        elif kind == "json":
            changes = compare(va, vb)
        else:
            changes = compare_lines(va if isinstance(va, list) else [str(va)],
                                    vb if isinstance(vb, list) else [str(vb)])
        for c in changes:  # an answer's own wrapper says nothing: body.x and structured.x read as x
            c["path"] = re.sub(r"^(body|structured)(\.|(?=\[)|$)", "", c["path"]) or "(answer)"
        for f in flags(rel, va, vb):
            result["regressions"].append({"file": rel, "what": f})
        if changes or rel not in fa:
            result["files"][rel] = changes
    ma, mb = run_meta(a_dir), run_meta(b_dir)
    for step, st in ((mb.get("_run.json") or {}).get("steps") or {}).items():
        if st != "ok" and ((ma.get("_run.json") or {}).get("steps") or {}).get(step) == "ok":
            result["regressions"].append({"file": "_run.json", "what": f"driver step {step} {st}"})
    proxy_b = mb.get("_proxy.json") or {}
    for m in proxy_b.get("misses") or []:
        result["regressions"].append({"file": "(recording)", "what": "recording miss: "
                                      + _short({"store": m.get("store"), "path": m.get("path"),
                                                "query": dict(m.get("query") or [])}, 400)})
    for r in proxy_b.get("refusals") or []:
        result["regressions"].append({"file": "(recording)", "what": f"store request refused: {r.get('why')}"})
    result["meta"] = {"a": ma, "b": mb}
    return result


def report(result: dict, a_label: str, b_label: str, header: list[str] | None = None) -> str:
    ma = (result["meta"]["a"].get("_run.json") or {})
    mb = (result["meta"]["b"].get("_run.json") or {})
    lines = [f"# A/B: {a_label} vs {b_label}", ""]
    lines += header or []
    lines += [f"- A: version {ma.get('version', '?')}, clock {ma.get('frozen_at', '?')}",
              f"- B: version {mb.get('version', '?')}, clock {mb.get('frozen_at', '?')}", ""]
    for who, m in (("B", result["meta"]["b"].get("_proxy.json") or {}),):
        if m:
            lines.append(f"- {who}'s store requests: {m.get('exact', 0)} answered exactly, {m.get('fuzzy', 0)} by "
                         f"nearest time, {m.get('miss', 0)} not in the recording, {m.get('refused', 0)} refused")
    lines.append("")
    regs = result["regressions"]
    lines += [f"## Regressions ({len(regs)})", ""]
    lines += [f"- **{r['file']}**: {r['what']}" for r in regs[:200]] or ["None."]
    if len(regs) > 200:
        lines.append(f"- … and {len(regs) - 200} more")
    lines.append("")
    by_feature: dict[str, list[str]] = {}
    for rel in result["files"]:
        by_feature.setdefault(feature_of(rel), []).append(rel)
    order = [t for t, _ in FEATURES] + ["Other"]
    lines += ["## Summary", "", "| Feature | Outputs changed | Changes |", "|---|---:|---:|"]
    for t in order:
        rels = by_feature.get(t) or []
        if rels:
            lines.append(f"| {t} | {len(rels)} | {sum(len(result['files'][r]) or 1 for r in rels)} |")
    if not result["files"]:
        lines.append("| (nothing changed) | 0 | 0 |")
    lines.append("")
    for t in order:
        rels = by_feature.get(t) or []
        if not rels:
            continue
        lines += [f"## {t}", ""]
        for rel in rels:
            ch = result["files"][rel]
            if rel in result["only_b"]:
                lines.append(f"### {rel} (new in B)")
                lines.append("")
                continue
            lines += [f"### {rel} ({len(ch)} change{'s' if len(ch) != 1 else ''})", ""]
            for c in ch[:MAX_LINES_PER_FILE]:
                lines.append("- " + describe(c).replace("\n", " ⏎ "))
            if len(ch) > MAX_LINES_PER_FILE:
                lines.append(f"- … and {len(ch) - MAX_LINES_PER_FILE} more")
            lines.append("")
    for topic, rx in TOPICS.items():
        hits = [(rel, c) for rel, ch in result["files"].items() for c in ch if rx.search(c["path"])]
        lines += [f"## Topic: {topic} ({len(hits)})", ""]
        lines += [f"- {rel}: " + describe(c).replace("\n", " ⏎ ") for rel, c in hits[:80]] or ["No change."]
        lines.append("")
    if result["only_a"]:
        lines += ["## Outputs only A has", ""] + [f"- {r}" for r in result["only_a"]] + [""]
    return "\n".join(lines)
