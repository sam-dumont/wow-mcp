"""What one server-side analysis run costs, without spending anything.

Measures the run's fixed part on the synthetic priest
(tests/data/analysis-events.jsonl.gz): the first request (system, the
allowed tools, the `analyse` prompt) and the brief's pages a run reads
before its first lane. With Anthropic credentials configured
(wowstats.analysis.auto: ANALYSIS_WIF_* or ANALYSIS_API_KEY_FILE) it counts
the first request with `messages.count_tokens`, which is free; else it
estimates from characters (CHARS_PER_TOKEN) and says so. The rest of the
run is a model of its turns (TURNS below, from the procedure: the survey's
12 questions, 4 to 6 lanes, follow-ups, coverage, the synthesis), priced
with auto.PRICES and prompt caching.

    uv run --extra analysis python tools/analysis_cost.py
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHAR = "Testpriest-Fixture"
CHARS_PER_TOKEN = 3.3  # Claude 4.7 and later count about 30% more tokens than the old ~4.2 chars a token
# A run's turns past the brief, by pass: (calls, tool result characters per call, output tokens per call).
TURNS = {"survey": (2, 2000, 1500), "lanes": (45, 2500, 500), "followups": (12, 2500, 500),
         "coverage": (12, 2500, 500), "findings": (8, 1500, 900), "synthesis": (3, 3000, 4000)}
SEGMENT = 120_000  # auto.SEGMENT_TOKENS: a new conversation after a handed-in pass past it
USD_EUR = 0.86  # approximate, for the report only


def fixed_part() -> tuple[dict, list[int]]:
    from wowstats import analysis
    from wowstats.analysis import auto, prep
    from wowstats.mcp import prompts
    from wowstats.mcp import tools as T

    with gzip.open(ROOT / "tests" / "data" / "analysis-events.jsonl.gz") as f:
        rows = [json.loads(x) for x in f]
    prep.query = lambda store, stream, char: [dict(r) for r in rows]
    analysis.other_players = lambda *a: set()
    env = {"ANALYSIS_STATE_DIR": tempfile.mkdtemp(), "KB_GAME_TABLES": "off", "VL_URL": "http://unused"}

    class Store:
        vl_url, vl_archive_url, logs_filter, stream, hot_days, account_id = "", "", "feed:wow-*", "feed:wow-*", 89, 0

    ctx = T.Ctx(store=Store(), default_char=CHAR, env=env)
    reg = tuple(T.BY_NAME[n] for n in auto.ALLOWED)
    first = {"system": auto.SYSTEM, "tools": [{"name": t.name, "description": t.description,
                                               "input_schema": t.input_schema} for t in reg],
             "messages": [{"role": "user", "content": prompts.analyse_text(CHAR, "")}]}
    pages = []
    for sec in ("start", "schema", "profile", "kb", "survey"):
        out = T.call(ctx, "analysis_brief", {"char": CHAR, "section": sec}, reg)
        pages.append(len(json.dumps(out, ensure_ascii=False)))
        for page in range(2, int(out.get("pages") or 1) + 1):
            more = T.call(ctx, "analysis_brief", {"char": CHAR, "section": sec, "page": page}, reg)
            pages.append(len(json.dumps(more, ensure_ascii=False)))
    return first, pages


def count_first(first: dict) -> tuple[int, str]:
    from wowstats.analysis import auto

    cfg = auto.Config.from_env(dict(os.environ))
    if cfg.provider == "anthropic" and cfg.on:
        client = cfg.make_client()
        n = client.messages.count_tokens(model=cfg.model, **first).input_tokens
        return n, f"messages.count_tokens on {cfg.model}"
    chars = len(json.dumps(first, ensure_ascii=False))
    return round(chars / CHARS_PER_TOKEN), f"estimated: {chars:,} characters / {CHARS_PER_TOKEN} (no count_tokens)"


def model_run(first_tokens: int, pages: list[int]) -> dict:
    """Token totals for one run: each call reads the conversation so far
    from the cache, writes what's new to it, and writes its own output."""
    calls = [(c, 300) for c in pages]  # the brief: one call a page
    for n, chars, out in TURNS.values():
        calls += [(chars, out)] * n
    ctx = first_tokens
    t = {"input": 0, "cache_read": 0, "cache_write": first_tokens, "output": 0, "calls": 0, "conversations": 1,
         "long_calls": 0}
    for chars, out in calls:
        new = round(chars / CHARS_PER_TOKEN) + out  # the tool result in, the model's turn out
        t["calls"] += 1
        t["cache_read"] += ctx
        t["long_calls"] += ctx > 100_000
        t["output"] += out
        t["cache_write"] += new
        ctx += new
        if ctx > SEGMENT:  # a new conversation: the prompt and analysis_progress again
            t["conversations"] += 1
            ctx = first_tokens + 1500
            t["cache_write"] += ctx
    return t


def price(model: str, t: dict) -> float:
    from wowstats.analysis import auto

    i, o, w, r = auto.PRICES[model]
    base = (t["input"] * i + t["output"] * o + t["cache_write"] * w + t["cache_read"] * r) / 1e6
    if model in auto.LONG:  # the calls over 100k tokens of prompt pay the long prices: approximated by their share
        li, lo, lw, lr = auto.LONG[model][1]
        share = t["long_calls"] / t["calls"]
        base = base * (1 - share) + share * (t["output"] * lo + t["cache_write"] * lw + t["cache_read"] * lr) / 1e6
    return base


def main() -> int:
    first, pages = fixed_part()
    n, how = count_first(first)
    t = model_run(n, pages)
    print(f"first request: {n:,} tokens ({how})")
    print(f"brief pages read before the lanes: {len(pages)}, {sum(pages):,} characters")
    print(f"modelled run: {t['calls']} calls, {t['conversations']} conversations, "
          f"{t['cache_read']:,} cache reads, {t['cache_write']:,} cache writes, {t['output']:,} output tokens")
    for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
        usd = price(model, t)
        print(f"{model}: ${usd:.2f} a run (about EUR {usd * USD_EUR:.2f}); "
              f"$50 a month buys about {int(50 // usd)} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
