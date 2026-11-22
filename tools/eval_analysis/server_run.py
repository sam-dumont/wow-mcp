"""The evaluation's `server` client: the server's own analysis runner
(wowstats.analysis.auto.Runner, what a self-hosted server runs on its
timer) over one scenario's events, in a process of its own.

    uv run --extra mcp --extra analysis-openai python tools/eval_analysis/server_run.py \\
        SETTINGS.json EVENTS.jsonl CHAR TRANSCRIPT.jsonl SPEND.json

clients.ServerRunner writes SETTINGS.json: the scenario server's settings
(server.py) plus the provider's (ANALYSIS_PROVIDER, ANALYSIS_MODEL, the key
file's path, the prices, ANALYSIS_MONTHLY_BUDGET as the batch's cap). The
process' environment is replaced by it, as in server.py. The runner is the
production class unchanged; two things are the evaluation's own:

- the spend file is the batch's (SPEND.json), shared by every run going at
  once, so the cap covers the batch. Spend's lock is a thread lock, enough
  in one server; here it also takes a file lock (SharedLock), so two
  processes never lose each other's additions. The cap is checked before
  each call, so the batch can pass it by at most one call per run going
  at once;
- each tool call and each model turn goes on a line of TRANSCRIPT.jsonl as
  it happens, so a run cut by the time cap still says what it was doing;
- with EVAL_SERVER_KEY_CMD, the key comes in this process' environment
  (clients.KEY_ENV), is taken out before anything else runs, and goes to
  the SDK client directly, as the server's make_client passes a key file's.

The last line on stdout is the runner's result as JSON. Exit 0 when the run
finished, 1 when it didn't.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import threading
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import scenarios  # noqa: E402
import server  # noqa: E402


class SharedLock:
    """A thread lock plus a file lock: Spend's read-add-write across processes."""

    def __init__(self, path: Path):
        self.path, self._t, self._f = path, threading.Lock(), None

    def __enter__(self):
        self._t.acquire()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc) -> bool:
        fcntl.flock(self._f, fcntl.LOCK_UN)
        self._f.close()
        self._t.release()
        return False


def shared_spend(path: Path):
    from wowstats.analysis import auto

    spend = auto.Spend(path)
    spend._lock = SharedLock(path.with_name(path.name + ".lock"))
    return spend


def sdk_client(cfg, key: str):
    """The provider's SDK client with this key, as auto.Config.make_client
    builds it from a key file."""
    from wowstats.analysis import auto

    base = cfg.base_url or auto.BASE_URLS[cfg.provider]
    if cfg.provider == "anthropic":
        import anthropic

        return anthropic.Anthropic(api_key=key, base_url=base, max_retries=3)
    import openai

    return openai.OpenAI(api_key=key, base_url=base, max_retries=3)


def run(env: dict, rows: list[dict], char: str, transcript: Path, spend_file: Path, client=None) -> dict:
    """One run of the production runner on `rows`; `client` replaces the
    provider's SDK client (the tests' stub)."""
    from wowstats.analysis import auto
    from wowstats.mcp import context

    server.serve_events(rows)
    cfg = auto.Config.from_env(env)
    if not cfg.on:
        return {"char": char, "status": "stopped", "why": cfg.why_off(), "usd": None, "tokens": 0, "calls": 0}
    if not cfg.priced:
        return {"char": char, "status": "stopped", "why": "no prices: the spend cap can't apply", "usd": None,
                "tokens": 0, "calls": 0}

    log = transcript.open("a", encoding="utf-8")

    class Logged(auto.Runner):
        def send(self):
            turn = super().send()
            log.write(json.dumps({"usage": turn.usage, "usd": self.usd, "model": turn.model}) + "\n")
            log.flush()
            return turn

        def tool(self, call):
            out = super().tool(call)
            log.write(json.dumps({"tool": call.name, "error": out[0][2]}) + "\n")
            log.flush()
            return out

    try:
        ctx = replace(context(env), bnet=False)
        return Logged(cfg, client if client is not None else cfg.make_client(), shared_spend(spend_file),
                      ctx, char).run()
    finally:
        log.close()


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    settings, events, char, transcript, spend = argv
    key = os.environ.pop("EVAL_SERVER_API_KEY", "")
    server.clean_env(json.loads(Path(settings).read_text(encoding="utf-8")))
    client = None
    if key:
        from wowstats.analysis import auto

        client = sdk_client(auto.Config.from_env(os.environ), key)
    del key
    r = run(os.environ, scenarios.read_events(Path(events)), char, Path(transcript), Path(spend), client)
    print(json.dumps(r, default=str), flush=True)
    return 0 if r.get("status") == "done" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
