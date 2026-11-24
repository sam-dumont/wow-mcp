"""The MCP check's deterministic tier: each question's probes, the tool
calls a model should make, run against the fixture server without a model.

A probe is {tool, args, path, then, equals, contains, error, untrusted,
holds} (questions.yaml says what each key means). run() calls the tools in
order on one server, the one the model gets (answers.Tools: server.py over
stdio), so check_plan sees the plan_session before it, and returns
what each call gave; check() says what a probe got wrong, or nothing.
tests/test_eval_mcp.py runs every question's probes on every PR, so a tool
that stops holding a question's answer, or a renamed argument, fails CI
before anyone spends a model run on it.
"""

from __future__ import annotations

import json
import math
import re

_SEG = re.compile(r"^([^\[]*)((?:\[[^\]]*\])*)$")
_SEL = re.compile(r"\[([^\]]*)\]")

# The markers a tool puts on text other people wrote (players' names, the
# knowledge base's model-written text): server.py INSTRUCTIONS names them.
UNTRUSTED = ("untrusted", "untrusted_reference_data")


class PathError(LookupError):
    pass


def resolve(obj, path: str):
    """`a.b[0].c`, `dungeons[rank=1].name`, `levels[*].seconds` (a list),
    `sessions.#` (a length). Keys may hold colons (`names.item:23720`) and
    dots (`facts.end.level`)."""
    vals, many = [obj], False
    segs, i = path.split("."), 0
    while i < len(segs):
        seg = segs[i]
        i += 1
        # A key with dots in it (plan facts: `facts.b1.steps`): the longest
        # run of plain segments that is a key of every value here.
        for j in range(len(segs), i, -1):
            key = ".".join(segs[i - 1:j])
            if "[" not in key and vals and all(isinstance(v, dict) and key in v for v in vals):
                seg, i = key, j
                break
        if seg == "#":
            vals = [len(v) for v in vals]
            continue
        m = _SEG.match(seg)
        if not m:
            raise PathError(f"bad path segment {seg!r}")
        name, sels = m.group(1), _SEL.findall(m.group(2))
        nxt = []
        for v in vals:
            if name:
                if not isinstance(v, dict) or name not in v:
                    raise PathError(f"{path}: no {name!r}")
                v = v[name]
            cur = [v]
            for s in sels:
                out = []
                for c in cur:
                    if not isinstance(c, list):
                        raise PathError(f"{path}: [{s}] on a {type(c).__name__}")
                    if s == "*":
                        out.extend(c)
                        many = True
                    elif re.fullmatch(r"-?\d+", s):
                        if not -len(c) <= int(s) < len(c):
                            raise PathError(f"{path}: no item {s} in {len(c)}")
                        out.append(c[int(s)])
                    elif "=" in s:
                        k, want = s.split("=", 1)
                        hit = [x for x in c if isinstance(x, dict) and str(x.get(k)) == want]
                        if not hit:
                            raise PathError(f"{path}: nothing with {k}={want}")
                        out.append(hit[0])
                    else:
                        raise PathError(f"{path}: bad selector [{s}]")
                cur = out
            nxt.extend(cur)
        vals = nxt
    return vals if many else vals[0]


def apply(value, ops: list[str] | None):
    """`then` steps: sum, max, min, len, div:N, floor, round:N, minutes
    (a "2h27m" duration in minutes)."""
    for op in ops or []:
        name, _, arg = str(op).partition(":")
        if name in ("sum", "max", "min"):
            value = {"sum": sum, "max": max, "min": min}[name](float(x) for x in value)
        elif name == "len":
            value = len(value)
        elif name == "div":
            value = float(value) / float(arg)
        elif name == "floor":
            value = math.floor(float(value))
        elif name == "round":
            value = round(float(value), int(arg or 0))
        elif name == "minutes":
            value = minutes(value)
        else:
            raise ValueError(f"unknown then step {op!r}")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value


def minutes(dur: str) -> int:
    """"2h27m" -> 147, "25m" -> 25, "1d12h" -> 2160."""
    m = re.fullmatch(r"\s*(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*", str(dur or ""))
    if not m or not any(m.groups()):
        raise ValueError(f"not a duration: {dur!r}")
    d, h, mi = (int(x or 0) for x in m.groups())
    return (d * 24 + h) * 60 + mi


def run(call, probes: list[dict]) -> list[dict]:
    """Every probe's call, in order, through `call(tool, args)` ->
    (is_error, text) (answers.Tools.raw: the model's own server):
    [{probe, error, text, data}]."""
    out = []
    for p in probes:
        error, text = call(p["tool"], dict(p.get("args") or {}))
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        out.append({"probe": p, "error": error, "text": text, "data": data})
    return out


def value(result: dict):
    """The probe's value: its path through the answer, then its steps."""
    p = result["probe"]
    return apply(resolve(result["data"], p["path"]), p.get("then"))


def check(result: dict, expected=None, match: str = "text", tolerance: float = 0) -> list[str]:
    """What this probe got wrong ([] when nothing). `expected` is the
    question's answer, graded against the probe's value when the probe has
    a path and no `equals`."""
    import grading

    p, problems = result["probe"], []
    want_error = bool(p.get("error"))
    if result["error"] != want_error:
        return [f"{p['tool']}: isError {result['error']}, want {want_error}: {result['text'][:200]}"]
    if want_error:
        return []
    if not isinstance(result["data"], dict):
        return [f"{p['tool']}: not a JSON object: {result['text'][:200]}"]
    if p.get("untrusted") and not any(k in result["data"] for k in UNTRUSTED):
        problems.append(f"{p['tool']}: no untrusted marker on text other people wrote")
    if "contains" in p and grading.norm(p["contains"]) not in grading.norm(result["text"]):
        problems.append(f"{p['tool']}: {p['contains']!r} not in the answer")
    if "path" in p:
        try:
            got = value(result)
        except (PathError, ValueError, TypeError) as e:
            return problems + [f"{p['tool']} {p['path']}: {e}"]
        holds = p.get("holds", "answer")
        if "equals" in p:
            if got != p["equals"]:
                problems.append(f"{p['tool']} {p['path']}: {got!r}, want {p['equals']!r}")
        elif got in (None, "", [], {}):
            problems.append(f"{p['tool']} {p['path']}: empty")
        elif holds == "some" or expected is None or match == "none":
            pass
        elif holds == "any":
            if grading.norm(str(expected)) not in grading.norm(json.dumps(got, ensure_ascii=False)):
                problems.append(f"{p['tool']} {p['path']}: {expected!r} isn't in {str(got)[:200]!r}")
        elif holds != "answer":
            problems.append(f"{p['tool']}: unknown holds {holds!r}")
        elif not grading.grade(str(got), expected, match, tolerance):
            problems.append(f"{p['tool']} {p['path']}: {got!r}, the question's answer is {expected!r}")
    return problems
