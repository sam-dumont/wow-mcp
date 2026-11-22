"""The MCP check's grader: the model's final line against the expected
answer, by exact or normalized match, never a model's opinion. Also which
tools a run used, and whether it got there through play_sql alone.
"""

from __future__ import annotations

import json
import re
import unicodedata

# The final line every answer must end with (run.py's prompt asks for it).
PREFIX = "ANSWER:"

# Tools that orient a model but can't hold any of the ten answers by
# themselves: a run that used these and play_sql found its answer in SQL.
ORIENTING = frozenset({"character_summary", "analysis_brief", "analysis_progress", "list_metrics",
                       "kb_lookup", "kb_get", "kb_search"})

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def final_answer(text: str) -> str | None:
    """The value of the last `ANSWER:` line, None when there is none."""
    got = None
    for line in (text or "").splitlines():
        s = line.strip().strip("*_`").strip()
        if s.upper().startswith(PREFIX):
            got = s[len(PREFIX):].strip().strip("*_`").strip()
    return got


def norm(s: str) -> str:
    """Case, accents, quotes, apostrophes, arrows and spacing out of the way."""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    s = s.replace("->", "→").replace("=>", "→")
    s = re.sub(r"[\"“”*_]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" .,;:!'")
    s = re.sub(r"^the ", "", s)
    return s


def number(s: str) -> float | None:
    """The first number in s: "1,234" is a thousand and more, "25,3" a
    decimal comma."""
    s = re.sub(r"(\d),(\d{3})\b", r"\1\2", str(s or ""))
    m = _NUM.search(s)
    return float(m.group(0).replace(",", ".")) if m else None


def pct_pair(s: str) -> tuple[int, int] | None:
    m = re.search(r"(\d+)\s*%?\s*(?:→|->|to)\s*(\d+)\s*%?", norm(s))
    return (int(m.group(1)), int(m.group(2))) if m else None


def yesno(s: str) -> str | None:
    w = norm(s).split(" ")[0].strip(",.")
    return {"yes": "yes", "no": "no", "true": "yes", "false": "no"}.get(w)


def grade(got: str | None, expected, match: str, tolerance: float = 0) -> bool:
    if got is None or str(got).strip() == "":
        return False
    if match == "number":
        g = number(got)
        return g is not None and abs(g - float(expected)) <= float(tolerance) + 1e-9
    if match == "yesno":
        return yesno(got) == yesno(str(expected))
    if match == "pct_line":
        return pct_pair(got) is not None and pct_pair(got) == pct_pair(str(expected))
    if match == "text":
        return norm(got) == norm(str(expected))
    raise ValueError(f"unknown match {match!r}")


def tools_used(stream: str, server: str) -> tuple[list[str], str, dict]:
    """From a `claude -p --output-format stream-json` transcript: the tools
    called in order (server prefix dropped), the final result text, and the
    result message's usage and cost."""
    calls: list[str] = []
    result, meta = "", {}
    for line in (stream or "").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("type") == "assistant":
            for c in (m.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    calls.append(str(c.get("name") or "?").removeprefix(f"mcp__{server}__"))
        elif m.get("type") == "result":
            result = str(m.get("result") or "")
            meta.update(is_error=bool(m.get("is_error")), cost_usd=m.get("total_cost_usd"),
                        turns=m.get("num_turns"))
        elif m.get("type") == "system" and m.get("subtype") == "init":
            meta["connected"] = server in {s.get("name") for s in m.get("mcp_servers") or []
                                           if s.get("status") == "connected"}
    return calls, result, meta


def sql_only(calls: list[str]) -> bool:
    """play_sql was the only tool that could have held the answer."""
    return "play_sql" in calls and not (set(calls) - ORIENTING - {"play_sql"})
