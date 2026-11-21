"""The server's own analysis (wowstats.analysis.auto): the server plays the
MCP client through a provider adapter, on a fake client for each provider,
no network. One scripted model drives a whole run through either adapter;
the findings it leaves are the same. The per-character played-time timer, the
money and token caps, tenant isolation, "not configured" and a model
without tool calling."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tests.analysis.test_analysis import FINDINGS, QUESTIONS, SYNTH
from wowstats import analysis
from wowstats.analysis import auto, drive, load_state, prep, providers
from wowstats.mcp import tools

DATA = Path(__file__).resolve().parent.parent / "data" / "analysis-events.jsonl.gz"
CHAR = "Testpriest-Fixture"
H = 3600.0
USAGE = {"input": 2000, "output": 300, "cache_read": 8000, "cache_write": 500}


class FakeStore:
    vl_url, vl_archive_url, logs_filter, hot_days, stream = "http://unused", "", "feed:wow-*", 89, "feed:wow-*"

    def __init__(self, account_id: str = "0"):
        self.account_id = account_id


@pytest.fixture(scope="module")
def rows():
    with gzip.open(DATA) as f:
        return [json.loads(x) for x in f]


@pytest.fixture()
def lab(rows, tmp_path, monkeypatch):
    """prep reads the fixture's events whatever the store, and notes which
    store and character it was asked for (tenant isolation)."""
    asked: list[tuple[str, str]] = []

    def query(store, stream, char):
        asked.append((getattr(store, "account_id", "?"), char))
        return [dict(r) for r in rows]

    monkeypatch.setattr(prep, "query", query)
    monkeypatch.setattr(analysis, "other_players", lambda store, stream, char: set())
    monkeypatch.setattr(auto, "sdk_installed", lambda module: True)
    monkeypatch.setattr(auto, "RUN_GUESS_USD", {"claude-opus-5-5": 0.5})
    drive._WS.clear()
    key = tmp_path / "key"
    key.write_text("not-a-real-key\n")
    return SimpleLab(tmp_path, str(key), asked)


class SimpleLab:
    def __init__(self, root: Path, key: str, asked: list):
        self.root, self.key, self.asked = root, key, asked

    @staticmethod
    def acct(who: str) -> str:
        return str(100 + ord(who))  # VictoriaLogs tenants are numbers

    def env(self, who: str = "a", **extra) -> dict:
        return {"ANALYSIS_STATE_DIR": str(self.root / who), "WOW_STATE_DIR": str(self.root),
                "KB_GAME_TABLES": "off", "VL_URL": "http://unused", "VL_ACCOUNT_ID": self.acct(who),
                "ANALYSIS_API_KEY_FILE": self.key, "ANALYSIS_MONTHLY_BUDGET": "50", **extra}

    def ctx(self, env: dict) -> tools.Ctx:
        return tools.Ctx(store=FakeStore(env["VL_ACCOUNT_ID"]), default_char=CHAR, env=env)


# ---------------------------------------------------------------- the scripted model


def brain(char_arg: str = CHAR):
    """The whole run as a model drives it (test_analysis.walk, over tool
    calls): each step gets the last tool result, gives the next call."""
    r = yield "analysis_brief", {"char": char_arg, "section": "start"}
    r = yield "analysis_submit", {"char": char_arg, "pass": "survey", "answer": {"questions": QUESTIONS}}
    first = True
    while r["next"]["pass"] in ("lane", "pick"):
        nx = r["next"]
        if nx["pass"] == "pick":
            r = yield "analysis_submit", {"char": char_arg, "pass": "pick", "answer": {"picks": [0, 0, 99]}}
            continue
        if nx["questions"][0]["tables"]:
            yield "play_sql", {"char": char_arg, "query": f"SELECT COUNT(*) AS n FROM {nx['questions'][0]['tables'][0]}"}
        if first:
            for f in FINDINGS:
                yield "propose_finding", {"char": char_arg, "finding": f}
        ans = {"lane": nx["lane"], "answers": [{"question_id": q["id"], "answered": first, "note": "n"}
                                               for q in nx["questions"]],
               "ask_next": [{"question": "Which levels took longest?", "why": "pace", "tables": ["levels"]}]
               if first else []}
        first = False
        r = yield "analysis_submit", {"char": char_arg, "pass": "lane", "answer": ans}
    assert r["next"]["pass"] == "synthesis"
    yield "analysis_submit", {"char": char_arg, "pass": "synthesis", "answer": SYNTH}


class Script:
    """The model behind both fakes: one tool call a turn, from `brain`; a
    step that is a string is a turn that ends in those words, no tool call."""

    def __init__(self, gen=None, usage=USAGE):
        self.gen, self.usage, self.calls, self.started = gen or brain(), usage, 0, False
        self.seen_tools: list[str] = []
        self.done = False

    def next(self, last_result: str | None):
        self.calls += 1
        try:
            if not self.started:
                self.started = True
                return next(self.gen)
            try:
                got = json.loads(last_result) if last_result else {}
            except ValueError:
                got = {"error": last_result}  # a tool error comes back as text
            return self.gen.send(got)
        except StopIteration:
            self.done = True
            return None


class FakeAnthropic:
    """client.beta.messages.stream(**kw) as a context manager whose
    get_final_message() is the next scripted turn."""

    def __init__(self, script: Script):
        self.script, self.requests = script, []
        self.beta = NS(messages=NS(stream=self.stream))

    def stream(self, **kw):
        self.requests.append(kw)
        self.script.seen_tools = [t["name"] for t in kw["tools"]]
        last = kw["messages"][-1]["content"]
        result = last[0]["content"] if isinstance(last, list) else None
        step = self.script.next(result)
        u = self.script.usage
        usage = NS(input_tokens=u["input"], output_tokens=u["output"], cache_read_input_tokens=u["cache_read"],
                   cache_creation_input_tokens=u["cache_write"])
        if step is None or isinstance(step, str):  # a turn that ends in words
            msg = NS(content=[NS(type="thinking", thinking="...", signature="s"), NS(type="text", text=step or "Done.")],
                     stop_reason="end_turn", usage=usage, model=kw["model"])
        else:
            name, args = step
            msg = NS(content=[NS(type="thinking", thinking="...", signature="s"),
                              NS(type="tool_use", id=f"tu_{self.script.calls}", name=name, input=args)],
                     stop_reason="tool_use", usage=usage, model=kw["model"])

        class Ctx:
            def __enter__(self_):
                return NS(get_final_message=lambda: msg)

            def __exit__(self_, *a):
                return False

        return Ctx()


OLLAMA = "http://ollama:11434/v1"


class FakeOpenAI:
    """client.chat.completions.create(...) (any OpenAI-compatible endpoint)
    and client.responses.create(...) (OpenAI itself) answering the next
    scripted turn. `error` is raised by either, as the SDK raises an API error."""

    def __init__(self, script: Script, refuse_tools: bool = False, error: Exception | None = None):
        self.script, self.requests, self.refuse_tools, self.error = script, [], refuse_tools, error
        self.chat = NS(completions=NS(create=self.create))
        self.responses = NS(create=self.respond)

    def respond(self, model, instructions, input, tools, **kw):
        self.requests.append({"model": model, "instructions": instructions, "input": list(input), "tools": tools,
                              **kw})
        if self.error:
            raise self.error
        self.script.seen_tools = [t["name"] for t in tools]
        last = input[-1]
        step = self.script.next(last["output"] if last.get("type") == "function_call_output" else None)
        u = self.script.usage
        usage = NS(input_tokens=u["input"] + u["cache_read"], output_tokens=u["output"],
                   input_tokens_details=NS(cached_tokens=u["cache_read"]))
        reasoning = NS(type="reasoning", model_dump=lambda **k: {"type": "reasoning", "encrypted_content": "x"})
        if step is None or isinstance(step, str):
            out = [reasoning, NS(type="message", content=[NS(type="output_text", text=step or "Done.")],
                                 model_dump=lambda **k: {"type": "message", "role": "assistant"})]
        else:
            name, args = step
            cid = f"call_{self.script.calls}"
            out = [reasoning, NS(type="function_call", call_id=cid, name=name, arguments=json.dumps(args),
                                 model_dump=lambda **k: {"type": "function_call", "call_id": cid})]
        return NS(output=out, usage=usage, model=model, incomplete_details=None)

    def create(self, model, messages, tools):
        self.requests.append({"model": model, "messages": list(messages), "tools": tools})
        if self.error:
            raise self.error
        if self.refuse_tools:
            err = Exception("registry.ollama.ai/library/tiny does not support tools")
            err.status_code = 400
            raise err
        self.script.seen_tools = [t["function"]["name"] for t in tools]
        last = messages[-1]
        step = self.script.next(last["content"] if last["role"] == "tool" else None)
        u = self.script.usage
        usage = NS(prompt_tokens=u["input"] + u["cache_read"], completion_tokens=u["output"],
                   prompt_tokens_details=NS(cached_tokens=u["cache_read"]))
        if step is None or isinstance(step, str):
            msg, reason = NS(content=step or "Done.", tool_calls=None, refusal=None), "stop"
        else:
            name, args = step
            tc = NS(id=f"call_{self.script.calls}", type="function",
                    function=NS(name=name, arguments=json.dumps(args)))
            msg, reason = NS(content=None, tool_calls=[tc], refusal=None), "tool_calls"
        return NS(choices=[NS(message=msg, finish_reason=reason)], usage=usage, model=model)


def service(env: dict, client, played=0.0, chars=(CHAR,)) -> auto.Service:
    """Every character has `played` seconds on its timer, whatever its last refresh."""
    return auto.Service(env, client=client, played=lambda e, char, since, store=None: played,
                        chars=lambda e, store=None: list(chars))


def refreshed(env: dict, char: str = CHAR) -> dict | None:
    return analysis.last_refresh(load_state(env, char))


def kept(env: dict) -> dict:
    s = load_state(env, CHAR)
    return {k: (f["text"], f.get("sql")) for k, f in (s.get("findings") or {}).items()}


# ---------------------------------------------------------------- one run, both adapters


def test_the_same_script_records_the_same_findings_through_both_adapters(lab):
    ea = lab.env("a")
    eo = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="some-local-model",
                 ANALYSIS_BASE_URL=OLLAMA)
    sa, so = Script(), Script()
    ant, oai = FakeAnthropic(sa), FakeOpenAI(so)
    ra = service(ea, ant).run_job("player", ea, lab.ctx(ea))
    drive._WS.clear()
    ro = service(eo, oai).run_job("player", eo, lab.ctx(eo))
    assert [r["status"] for r in ra["runs"]] == ["done"] == [r["status"] for r in ro["runs"]], (ra, ro)
    assert kept(ea) == kept(eo) and sorted(kept(ea)) == ["sessions-online", "xp-by-level"]
    run_a, run_o = load_state(ea, CHAR)["runs"][-1], load_state(eo, CHAR)["runs"][-1]
    # Shaped like a client's run, marked as the server's, naming the model.
    assert run_a["kind"] == "server" == run_o["kind"]
    assert run_a["driver"] == "anthropic:claude-opus-5-5" and run_o["driver"] == "openai:some-local-model"
    assert {k: v for k, v in run_a.items() if k not in ("at", "driver", "seconds_total", "run")} == \
        {k: v for k, v in run_o.items() if k not in ("at", "driver", "seconds_total", "run")}
    # The page and the MCP read a server run like a client's.
    assert analysis.answer(ea, CHAR, "full")["last_analysis"]["kind"] == "server"
    assert drive.progress(ea, CHAR)["last_analysis"]["kind"] == "server"
    # Only the analysis tools were offered, in each provider's shape.
    assert set(sa.seen_tools) == set(auto.ALLOWED) == set(so.seen_tools)
    req = ant.requests[0]
    assert req["thinking"] == {"type": "adaptive"} and req["cache_control"] == {"type": "ephemeral"}
    assert req["output_config"]["task_budget"]["type"] == "tokens" and "fallbacks" in req
    assert oai.requests[0]["messages"][0]["role"] == "system"
    # Thinking blocks go back to Anthropic unchanged; OpenAI's tool results as `tool` messages.
    assert any(getattr(b, "type", "") == "thinking" for m in ant.requests[-1]["messages"]
               if m["role"] == "assistant" for b in m["content"])
    assert any(m["role"] == "tool" for m in oai.requests[-1]["messages"])


def test_money_is_counted_from_usage(lab):
    e = lab.env("a")
    s = service(e, FakeAnthropic(Script()))
    r = s.run_job("player", e, lab.ctx(e))["runs"][0]
    per_call = auto.anthropic_cost("claude-opus-5-5", USAGE)
    assert per_call == pytest.approx((2000 * 4 + 300 * 20 + 500 * 5 + 8000 * 0.2) / 1e6)
    assert r["usd"] == pytest.approx(per_call * r["calls"], rel=1e-3)
    b = auto.budget(e, s.spend)
    assert b["spent"] == pytest.approx(r["usd"], abs=0.01) and b["cap"] == 50 and b["runs"] == 1
    assert b["tokens"] == sum(USAGE.values()) * r["calls"] and b["cost_known"]


def test_openai_without_prices_counts_tokens_only(lab):
    e = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="m", ANALYSIS_MONTHLY_BUDGET="0")
    s = service(e, FakeOpenAI(Script()))
    r = s.run_job("player", e, lab.ctx(e))["runs"][0]
    # No prices: no money cap (a 0 budget would refuse everything), the cost unknown.
    assert r["status"] == "done" and r["usd"] is None
    b = auto.budget(e, s.spend)
    assert b["spent"] is None and b["left"] is None and not b["cost_known"] and b["tokens"] > 0
    priced = lab.env("p", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="m", ANALYSIS_PRICE_INPUT_PER_MTOK="1",
                     ANALYSIS_PRICE_OUTPUT_PER_MTOK="2")
    assert auto.Config.from_env(priced).cost("m", USAGE) == pytest.approx(((2000 + 8000 + 500) * 1 + 300 * 2) / 1e6)
    # Cached prompt tokens at their own price when it's set (OpenAI: a tenth of the input's).
    cached = lab.env("c", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="m", ANALYSIS_PRICE_INPUT_PER_MTOK="1",
                     ANALYSIS_PRICE_OUTPUT_PER_MTOK="2", ANALYSIS_PRICE_CACHED_INPUT_PER_MTOK="0.1")
    assert auto.Config.from_env(cached).cost("m", USAGE) == pytest.approx(
        ((2000 + 500) * 1 + 8000 * 0.1 + 300 * 2) / 1e6)


def test_a_token_cap_stops_a_run(lab, monkeypatch):
    monkeypatch.setattr(auto, "RUN_GUESS_TOKENS", 1000)
    e = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="m", ANALYSIS_MONTHLY_TOKENS="60000")
    r = service(e, FakeOpenAI(Script())).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "stopped" and "token cap" in r["why"] and r["calls"] < 10


# ---------------------------------------------------------------- the timer


def test_nine_hours_59_of_play_is_not_due_ten_is(lab):
    e = lab.env("a")
    script = Script()
    s = service(e, FakeAnthropic(script), played=9 * H + 59 * 60)
    out = s.run_job("auto", e, lab.ctx(e))
    assert not out["started"] and script.calls == 0
    assert out["skipped"] == f"not due: {CHAR}: 9 h 59 of play on record, never refreshed, due at 10 h 00"
    s = service(e, FakeAnthropic(script), played=10 * H)
    out = s.run_job("auto", e, lab.ctx(e))
    assert out["started"] and out["runs"][0]["status"] == "done"
    m = refreshed(e)
    assert m["by"] == "server" and m["via"] == "auto"


def test_the_rebuild_hook_never_blocks_and_never_doubles(lab, monkeypatch):
    e = lab.env("a")
    s = service(e, FakeAnthropic(Script()), played=10 * H)
    queued = []
    monkeypatch.setattr(s, "_enqueue", queued.append)
    assert s.on_rebuild(e) is None and queued == [s.key(e)] and s._queued[s.key(e)][0] == "auto"
    assert s.on_rebuild(e) == "busy"  # its check hasn't run yet
    s._queued.clear()
    assert s.on_rebuild(e) == "checked recently"
    assert service(lab.env("a", ANALYSIS_AUTO="off"), None).on_rebuild(e) == "off"


def test_a_players_call_always_runs_and_resets_the_timer(lab):
    e = lab.env("a")
    s = service(e, FakeAnthropic(Script()), played=2 * H)  # far from due
    out = s.refresh(lab.ctx(e), wait=True)
    assert out["started"] and out["runs"][0]["status"] == "done" and not out["timer"]["due"]
    assert out["budget"]["currency"] == "USD" and out["budget"]["left"] <= 50
    m = refreshed(e)
    assert m["by"] == "player" and m["via"] == "dashboard_refresh"
    # The timer starts over: the next rebuild check finds nothing due.
    s2 = service(e, FakeAnthropic(Script()), played=0.0)
    assert "not due" in s2.run_job("auto", e, lab.ctx(e))["skipped"]


def test_a_full_analysis_from_the_players_own_client_resets_the_timer(lab):
    from tests.analysis.test_analysis import walk

    e = lab.env("a")
    walk(e, drive)
    m = refreshed(e)
    assert m["by"] == "player" and m["via"] == "client" and m["at"] == load_state(e, CHAR)["runs"][-1]["at"]
    assert load_state(e, CHAR)["runs"][-1]["kind"] == "client"


def test_the_last_try_waits_before_the_timer_tries_again(lab):
    e = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="tiny", ANALYSIS_BASE_URL=OLLAMA)
    s = service(e, FakeOpenAI(Script(), refuse_tools=True), played=12 * H)
    out = s.run_job("auto", e, lab.ctx(e))
    assert out["runs"][0]["status"] == "stopped"
    again = s.run_job("auto", e, lab.ctx(e))
    assert not again["started"] and "stopped recently" in again["skipped"]


# ---------------------------------------------------------------- each character's own timer

ALT = "Namekeeper-Fixture"


class PlayStore(FakeStore):
    """The Recorder as the timer's two queries read it (wowstats.analysis.auto:
    played_chars, played_since), from synthetic sessions: {char: [(start,
    seconds)]}, one minute bucket per minute played."""

    def __init__(self, account_id: str, play: dict[str, list[tuple[float, float]]]):
        super().__init__(account_id)
        self.play = play

    @staticmethod
    def iso(t: float) -> str:
        from datetime import UTC, datetime

        return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")

    def logs(self, query: str, start=None, end=None, limit=None) -> list[dict]:
        assert 'feed:="wow-recorder"' in query
        if "stats by (char)" in query:
            return [{"char": c, "last": self.iso(max(a + n for a, n in ss))} for c, ss in self.play.items()]
        (char,) = [c for c in self.play if json.dumps(c) in query]
        lo = start.timestamp() if start else 0.0
        return [{"n": "1", "t0": self.iso(a + m), "t1": self.iso(a + min(m + 60, n))}
                for a, n in self.play[char] for m in range(0, int(n), 60) if a + min(m + 60, n) > lo]


def played(lab, who: str, play: dict) -> tuple[dict, tools.Ctx]:
    e = lab.env(who)
    return e, tools.Ctx(store=PlayStore(e["VL_ACCOUNT_ID"], play), default_char=CHAR, env=e)


T0 = 1_790_000_000.0  # 2026-09: long before any refresh in these tests


def test_a_never_refreshed_alt_with_10_minutes_played_isnt_due(lab):
    """The lab's first server run (2026-11-21) analysed every character
    with no refresh on record, an alt logged into once to reserve a name
    included. A character with none counts all its play on record."""
    e, ctx = played(lab, "a", {ALT: [(T0, 10 * 60)]})
    script = Script()
    s = auto.Service(e, client=FakeAnthropic(script))
    out = s.run_job("auto", e, ctx)
    assert not out["started"] and script.calls == 0
    assert out["skipped"] == f"not due: {ALT}: 0 h 10 of play on record, never refreshed, due at 10 h 00"
    (alt,) = s.timer(e, ctx.store)["chars"]
    assert alt["last_refresh"] is None and alt["played_since"] == "0 h 10" and not alt["due"]


def test_a_never_refreshed_main_with_40_hours_played_is_due_and_runs_alone(lab):
    main = [(T0 + d * 86400, 10 * H) for d in range(4)]
    e, ctx = played(lab, "a", {CHAR: main, ALT: [(T0 + 5 * 86400, 10 * 60)]})
    s = auto.Service(e, client=FakeAnthropic(Script()))
    tm = s.timer(e, ctx.store)
    assert tm["due"] and [(c["char"], c["played_since"], c["due"]) for c in tm["chars"]] == [
        (ALT, "0 h 10", False), (CHAR, "40 h 00", True)]  # the latest played first
    assert tm["chars"][1]["said"] == f"{CHAR}: 40 h 00 of play on record, never refreshed, due at 10 h 00"
    out = s.run_job("auto", e, ctx)
    assert [(r["char"], r["status"]) for r in out["runs"]] == [(CHAR, "done")]
    assert refreshed(e)["by"] == "server" and refreshed(e, ALT) is None


def test_after_a_refresh_the_timer_counts_from_that_refresh(lab):
    play = {CHAR: [(T0 + d * 86400, 10 * H) for d in range(4)]}
    e, ctx = played(lab, "a", play)
    s = auto.Service(e, client=FakeAnthropic(Script()))
    assert s.run_job("auto", e, ctx)["runs"][0]["status"] == "done"
    at = auto._epoch(refreshed(e)["at"])
    # 40 h before the refresh, none since: not due, however much came before.
    (c,) = s.timer(e, ctx.store)["chars"]
    assert c["played_since"] == "0 h 00" and c["last_refresh"]["at"] == refreshed(e)["at"] and not c["due"]
    assert "since its last refresh" in c["said"]
    play[CHAR].append((at + 3600, 9 * H + 59 * 60))
    assert "not due: " + f"{CHAR}: 9 h 59 of play since its last refresh" in s.run_job("auto", e, ctx)["skipped"]
    play[CHAR].append((at + 86400, 60))
    assert s.timer(e, ctx.store)["chars"][0]["due"]


def test_a_players_call_runs_a_never_refreshed_alt_whatever_its_play(lab):
    e, ctx = played(lab, "a", {ALT: [(T0, 10 * 60)]})
    s = auto.Service(e, client=FakeAnthropic(Script()))
    out = s.refresh(ctx, wait=True)
    assert [(r["char"], r["status"]) for r in out["runs"]] == [(ALT, "done")]
    (before,) = out["timer"]["chars"]  # what the player is told: its play against the threshold
    assert before["said"] == f"{ALT}: 0 h 10 of play on record, never refreshed, due at 10 h 00"
    assert out["timer"]["refresh_after"] == "10 h 00" and not out["timer"]["due"]
    assert refreshed(e, ALT)["via"] == "dashboard_refresh"


# ---------------------------------------------------------------- money


def test_no_run_starts_past_the_budget(lab):
    e = lab.env("a", ANALYSIS_MONTHLY_BUDGET="1")
    script = Script()
    s = service(e, FakeAnthropic(script), played=20 * H)
    s.spend.add(0.9, {"input": 1})
    out = s.refresh(lab.ctx(e), wait=True)
    assert not out["started"] and script.calls == 0
    assert out["skipped"] == "a run would likely cross the budget: about $0.50 a run, $0.10 of $1.00 left this month"
    assert "would likely cross" in s.run_job("auto", e, lab.ctx(e))["runs"][0]["why"]
    s.spend.add(0.2, {"input": 1})  # over the cap: said as spent
    assert s.refresh(lab.ctx(e), wait=True)["skipped"].startswith("budget spent: $1.10 of $1.00")


def test_a_first_run_on_an_openai_compatible_model_is_guessed_at_its_own_prices(lab):
    """The guess for a model with no runs yet came from the priciest Claude
    run ($3.50): a $2 budget never started glm-5.3, whose runs cost cents
    to a dollar, and said "budget spent: $0.00" (2026-11-21)."""
    prices = {"ANALYSIS_PROVIDER": "openai", "ANALYSIS_MODEL": "glm-5.3", "ANALYSIS_BASE_URL": OLLAMA,
              "ANALYSIS_PRICE_INPUT_PER_MTOK": "1.4", "ANALYSIS_PRICE_CACHED_INPUT_PER_MTOK": "0.26",
              "ANALYSIS_PRICE_OUTPUT_PER_MTOK": "4.4"}
    low = lab.env("o", ANALYSIS_MONTHLY_BUDGET="2", **prices)
    guess = auto.Config.from_env(low).run_guess()
    assert guess == pytest.approx(6 * (0.03 * 1.4 + 0.96 * 0.26 + 0.01 * 4.4), rel=1e-3)  # about $2.01
    # Under the guess: refused before any call, said as an estimate.
    script = Script()
    r = service(low, FakeOpenAI(script)).run_job("player", low, lab.ctx(low))["runs"][0]
    assert r["why"] == "a run would likely cross the budget: about $2.01 a run, $2.00 of $2.00 left this month"
    assert script.calls == 0
    # Over it, the run goes; the next guess is this install's own average.
    e = {**low, "ANALYSIS_MONTHLY_BUDGET": "3"}
    r = service(e, FakeOpenAI(Script())).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "done", r
    assert auto.Spend(auto.spend_path(e)).run_estimate("glm-5.3", guess)[0] == pytest.approx(r["usd"], abs=1e-3)
    # Unpriced: no money guess, so no money cap.
    assert auto.Config.from_env(lab.env("q", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="m")).run_guess() == 0.0


def test_a_run_stops_before_the_call_that_would_cross_the_budget(lab, monkeypatch):
    monkeypatch.setattr(auto, "RUN_GUESS_USD", {"claude-opus-5-5": 0.01})
    monkeypatch.setattr(auto, "FIRST_CALL_USD", 0.01)
    e = lab.env("a", ANALYSIS_MONTHLY_BUDGET="0.1")
    big = {"input": 10000, "output": 1000, "cache_read": 0, "cache_write": 0}  # $0.06 a call on Opus 5.5
    s = service(e, FakeAnthropic(Script(usage=big)))
    r = s.run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "stopped" and r["why"].startswith("budget spent") and r["calls"] == 1
    assert s.spend.spent() <= 0.1


# ---------------------------------------------------------------- isolation and safety


def test_a_run_reads_and_writes_only_its_own_tenant(lab):
    ea, eb = lab.env("a"), lab.env("b")
    # A model that asks for someone else's character: the server forces the run's own.
    s = service(ea, FakeAnthropic(Script(brain("Someoneelse-Realm"))))
    r = s.run_job("player", ea, lab.ctx(ea))["runs"][0]
    assert r["status"] == "done"
    assert {str(a) for a, _ in lab.asked} == {lab.acct("a")} and {c for _, c in lab.asked} == {CHAR}
    assert (lab.root / "a").exists() and not (lab.root / "b").exists()
    assert not load_state(eb, CHAR).get("findings")
    assert refreshed(eb) is None


def test_only_the_analysis_tools_run(lab):
    e = lab.env("a")
    answers = []

    def sneaky():
        for name, args in (("plan_session", {"hours": 2}), ("market", {}), ("dashboard_refresh", {})):
            answers.append((yield name, args))

    fake = FakeAnthropic(Script(sneaky()))
    service(e, fake).run_job("player", e, lab.ctx(e))
    results = {b["tool_use_id"]: b for r in fake.requests for m in r["messages"]
               if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"]}.values()
    assert len(results) == 3 and all(r.get("is_error") and "isn't available" in r["content"] for r in results)
    assert not (lab.root / "a" / "plans").exists()


def test_not_configured_means_off(lab, monkeypatch):
    e = {k: v for k, v in lab.env("a").items() if k != "ANALYSIS_API_KEY_FILE"}
    s = service(e, None)
    out = s.refresh(lab.ctx(e))
    assert not out["started"] and out["skipped"].startswith("analysis not configured") and not out["configured"]
    assert s.on_rebuild(e) == "off"
    assert not auto.configured(lab.env("o", ANALYSIS_PROVIDER="openai"))  # openai needs a model
    assert auto.configured(lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="m", ANALYSIS_API_KEY_FILE=""))
    # The SDKs' own variables configure nothing: only ours do.
    assert not auto.configured({**e, "ANTHROPIC_FEDERATION_RULE_ID": "fdrl_x", "ANTHROPIC_ORGANIZATION_ID": "org",
                                "ANTHROPIC_IDENTITY_TOKEN_FILE": "/t", "ANTHROPIC_API_KEY": "k"})
    fed = {**e, "ANALYSIS_WIF_RULE_ID": "fdrl_x", "ANALYSIS_WIF_ORGANIZATION_ID": "org",
           "ANALYSIS_WIF_TOKEN_FILE": "/var/run/secrets/ai-token/token"}
    assert auto.configured(fed) and auto.Config.from_env(fed).auth == "wif"
    half = {**e, "ANALYSIS_WIF_TOKEN_FILE": "/t", "ANALYSIS_WIF_RULE_ID": "fdrl_x"}
    assert "needs ANALYSIS_WIF_ORGANIZATION_ID" in auto.Config.from_env(half).why_off()
    monkeypatch.setattr(auto, "sdk_installed", lambda module: False)
    assert "`analysis` extra" in auto.Config.from_env(fed).why_off()


def test_a_model_without_tool_calling_fails_the_run_plainly(lab):
    e = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="tiny", ANALYSIS_BASE_URL=OLLAMA)
    s = service(e, FakeOpenAI(Script(), refuse_tools=True))
    r = s.run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "stopped" and r["why"].startswith("tool calling is required")
    assert s.last_try(e)["why"].startswith("tool calling is required")
    # A model that answers in words instead of calling a tool: the same.
    words = Script(iter(()))
    r = service(e, FakeOpenAI(words)).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "stopped" and "without calling a tool" in r["why"]


def api_error(status: int, message: str) -> Exception:
    """Shaped like the openai SDK's APIStatusError: status, body, message."""
    e = Exception(f"Error code: {status} - {message}")
    e.status_code, e.body, e.message = status, {"error": {"message": message}}, f"Error code: {status} - {message}"
    return e


def test_openai_itself_runs_through_responses_and_keeps_its_reasoning(lab):
    """OpenAI's reasoning models take function tools on Responses only (gpt-6
    on Chat Completions: 400 "use /v1/responses", 2026-11-21)."""
    e = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="gpt-6-luna", ANALYSIS_PRICE_INPUT_PER_MTOK="0.1",
                ANALYSIS_PRICE_OUTPUT_PER_MTOK="0.5", ANALYSIS_PRICE_CACHED_INPUT_PER_MTOK="0.01")
    script = Script()
    oai = FakeOpenAI(script)
    r = service(e, oai).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "done", r
    assert sorted(kept(e)) == ["sessions-online", "xp-by-level"] and set(script.seen_tools) == set(auto.ALLOWED)
    first, last = oai.requests[0], oai.requests[-1]
    assert first["store"] is False and first["include"] == ["reasoning.encrypted_content"] and first["instructions"]
    # Stateless: each turn's reasoning and calls go back with the tool results.
    kinds = [i.get("type") for i in last["input"]]
    assert "reasoning" in kinds and "function_call" in kinds and "function_call_output" in kinds
    # Responses has no cache writes: the uncached prompt, the cached one at its price, the output.
    per_call = (USAGE["input"] * 0.1 + USAGE["cache_read"] * 0.01 + USAGE["output"] * 0.5) / 1e6
    assert r["usd"] == pytest.approx(r["calls"] * per_call, abs=1e-4)  # usd is rounded to 4 places
    # The same host named outright is OpenAI too; any other host is Chat Completions.
    assert auto.Config.from_env({**e, "ANALYSIS_BASE_URL": "https://api.openai.com/v1"}).openai_itself
    assert not auto.Config.from_env({**e, "ANALYSIS_BASE_URL": OLLAMA}).openai_itself


def test_an_api_error_is_reported_as_itself_not_as_a_tool_refusal(lab):
    """A 400 that names function tools without saying the model has none is
    the endpoint's own words, not "refused tool calling" (gpt-6-luna on Chat
    Completions, 2026-11-21); a quota 429 isn't a rate limit to wait out."""
    e = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="gpt-6-luna", ANALYSIS_BASE_URL=OLLAMA)
    route = ("Function tools with reasoning_effort are not supported for gpt-6-luna in /v1/chat/completions. "
             "To use function tools, use /v1/responses or set reasoning_effort to 'none'.")
    r = service(e, FakeOpenAI(Script(), error=api_error(400, route))).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "stopped" and not r["why"].startswith("tool calling is required")
    assert r["why"].startswith("the AI provider answered 400: Function tools with reasoning_effort") and \
        "use /v1/responses" in r["why"]
    quota = api_error(429, "Insufficient balance or no resource package. Please recharge.")
    r = service(e, FakeOpenAI(Script(), error=quota)).run_job("player", e, lab.ctx(e))["runs"][0]
    assert "quota" in r["why"] and "Insufficient balance" in r["why"]
    # A real refusal still is one, with what the endpoint said.
    no = api_error(400, "registry.ollama.ai/library/tiny does not support tools")
    r = service(e, FakeOpenAI(Script(), error=no)).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["why"].startswith("tool calling is required") and "does not support tools" in r["why"]
    # Nothing key-shaped from an error message reaches the run's record.
    # Built at run time from a filler, so no key-shaped literal sits in the source.
    fake = "sk" + "-" + "Z" * 40
    leak = api_error(400, f"Incorrect API key provided: {fake}.")
    r = service(e, FakeOpenAI(Script(), error=leak)).run_job("player", e, lab.ctx(e))["runs"][0]
    assert "ZZZZZZZZ" not in r["why"] and "[masked]" in r["why"]


def test_the_mcp_tool_runs_for_the_caller_only(lab, monkeypatch):
    ea = lab.env("a")
    s = service(ea, FakeAnthropic(Script()))
    seen = []
    monkeypatch.setattr(s, "_enqueue", seen.append)
    monkeypatch.setattr(auto, "_SERVICE", s)
    out = tools.call(lab.ctx(ea), "dashboard_refresh", {})
    job = s._queued[seen[0]]
    assert out["started"] and job[1]["ANALYSIS_STATE_DIR"] == ea["ANALYSIS_STATE_DIR"]
    assert str(job[2].store.account_id) == lab.acct("a")
    with pytest.raises(tools.ToolError):
        tools.call(lab.ctx(ea), "dashboard_refresh", {"char": "Someoneelse-Realm"})


def jwt(aud) -> str:
    import base64

    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b({'alg': 'RS256', 'kid': 'k'})}.{b({'aud': aud, 'sub': 'system:serviceaccount:wow:wow-ui'})}.sig"


def test_auth_order_and_explicit_credentials(lab, monkeypatch):
    """Federation first, then the key file, then (OpenAI-compatible only)
    none; whichever wins is passed to the SDK, so a stray ANTHROPIC_API_KEY
    or OPENAI_API_KEY in the environment changes nothing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stray-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "stray-openai")
    tok = lab.root / "token"
    oai = {"ANALYSIS_PROVIDER": "openai", "ANALYSIS_MODEL": "m"}
    wif_o = {"ANALYSIS_WIF_TOKEN_FILE": str(tok), "ANALYSIS_WIF_PROVIDER_ID": "idp",
             "ANALYSIS_WIF_SERVICE_ACCOUNT_ID": "svc"}
    wif_a = {"ANALYSIS_WIF_TOKEN_FILE": str(tok), "ANALYSIS_WIF_RULE_ID": "fdrl_x",
             "ANALYSIS_WIF_ORGANIZATION_ID": "org"}
    assert auto.Config.from_env(lab.env("o", **oai, **wif_o)).auth == "wif"
    assert auto.Config.from_env(lab.env("o", **oai)).auth == "key"
    assert auto.Config.from_env(lab.env("o", **oai, ANALYSIS_API_KEY_FILE="")).auth == "none"
    assert auto.Config.from_env(lab.env("a", **wif_a)).auth == "wif"
    assert auto.Config.from_env(lab.env("a")).auth == "key"

    assert auto.Config.from_env(lab.env("a")).make_client().api_key == "not-a-real-key"
    local = auto.Config.from_env(lab.env("o", **oai, ANALYSIS_API_KEY_FILE="",
                                         ANALYSIS_BASE_URL=OLLAMA)).make_client()
    assert local.api_key == "none" and str(local.base_url).startswith("http://ollama:11434/v1")

    tok.write_text(jwt(["https://api.anthropic.com"]))
    a = auto.Config.from_env(lab.env("a", **wif_a)).make_client()
    assert a.api_key is None and a.credentials is not None
    with pytest.raises(auto.AuthError, match="audience is https://api.anthropic.com, not https://api.openai.com/v1"):
        auto.Config.from_env(lab.env("o", **oai, **wif_o)).make_client()
    tok.write_text(jwt("https://api.openai.com/v1"))
    o = auto.Config.from_env(lab.env("o", **oai, **wif_o)).make_client()
    assert o.workload_identity["identity_provider_id"] == "idp"
    assert o.workload_identity["provider"]["get_token"]() == jwt("https://api.openai.com/v1")
    # A provider configured with another audience: ANALYSIS_WIF_AUDIENCE says so.
    tok.write_text(jwt("wow-stats"))
    auto.Config.from_env(lab.env("o", **oai, **wif_o, ANALYSIS_WIF_AUDIENCE="wow-stats")).make_client()


def test_a_wrong_token_fails_the_run_plainly(lab):
    tok = lab.root / "token"
    tok.write_text(jwt("https://cluster.internal"))
    e = lab.env("a", ANALYSIS_WIF_TOKEN_FILE=str(tok), ANALYSIS_WIF_RULE_ID="fdrl_x",
                ANALYSIS_WIF_ORGANIZATION_ID="org")
    s = auto.Service(e, played=lambda e, char, since, store=None: 0.0, chars=lambda e, store=None: [CHAR])
    r = s.run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "stopped" and "audience is https://cluster.internal" in r["why"]
    assert "audience" in s.last_try(e)["why"]


def test_the_api_key_never_leaves_the_key_file(lab, capsys):
    e = lab.env("a")
    cfg = auto.Config.from_env(e)
    client = cfg.make_client()
    assert client.api_key == "not-a-real-key"
    s = service(e, FakeAnthropic(Script()))
    s.run_job("player", e, lab.ctx(e))
    out = capsys.readouterr()
    assert "not-a-real-key" not in out.out + out.err
    assert "not-a-real-key" not in json.dumps(auto.budget(e, s.spend))


def test_adapters_map_tool_results_back(lab):
    a = providers.Anthropic(None, "claude-opus-5-5", "sys", [])
    a.start("hi")
    a.results([("tu_1", "ok", False), ("tu_2", "bad", True)])
    assert a.messages[-1]["content"][1] == {"type": "tool_result", "tool_use_id": "tu_2", "content": "bad",
                                            "is_error": True}
    o = providers.OpenAI(None, "m", "sys", [{"name": "x", "description": "d", "input_schema": {"type": "object"}}])
    assert o.tools[0]["function"]["parameters"] == {"type": "object"}
    o.start("hi")
    o.results([("call_1", "ok", False), ("call_2", "bad", True)])
    assert o.messages[-2:] == [{"role": "tool", "tool_call_id": "call_1", "content": "ok"},
                               {"role": "tool", "tool_call_id": "call_2", "content": "Error: bad"}]
    # A nudge after a turn that ended in words: a user message, appended.
    for ad in (a, o):
        ad.say("go on")
        assert ad.messages[-1] == {"role": "user", "content": "go on"}
    r = providers.OpenAIResponses(None, "m", "sys", [])
    r.start("hi")
    r.say("go on")
    assert r.input[-1] == {"role": "user", "content": "go on"}


def test_admin_shows_the_month_and_each_players_timer():
    from wowstats import admin

    now = 1_800_000_000.0
    person = {"slug": "friend", "owner": False, "status": "active", "admitted": True, "full": None, "mcp": None,
              "upload": {}, "os": None, "version": None, "chars": 1, "public_on": False, "build": None,
              "addons": None, "stale": ["server analysis stopped: budget spent"], "error": "",
              "name_conflict": None,
              "analysis": {"chars": [{"char": "Alt-Realm", "played_since": "0 h 10", "due": False,
                                     "refreshed": False},
                                    {"char": "Main-Realm", "played_since": "10 h 05", "due": True,
                                     "refreshed": True}],
                           "refresh_after": "10 h 00", "due": True, "at": now - 7200, "by": "server",
                           "last_try": None}}
    o = {"checked_at": now, "version": "0.7.0", "answers": {"built_at": None, "error": ""}, "publisher": None,
         "exporter": None, "disk": {"bytes": 0, "capped": False}, "checks": [], "people": [person],
         "analysis": {"cost_known": True, "spent": 50.0, "cap": 50.0, "left": 0.0, "tokens": 1, "tokens_cap": None,
                      "runs": 16, "month": "2026-11"}}
    page = admin.page_body(o)
    assert "AI analysis" in page and "$50.00 of $50, $0.00 left" in page and "16 runs" in page
    assert "Main-Realm: 10 h 05 of 10 h 00 since its refresh" in page and "by the server" in page
    assert page.index("Main-Realm") < page.index("Alt-Realm")  # the due ones first
    assert 'Alt-Realm: 0 h 10 of 10 h 00 <span class="dim">never refreshed</span>' in page
    assert "server analysis stopped" in page
    o["analysis"] = {**o["analysis"], "cost_known": False, "spent": None, "cap": None, "left": None,
                     "tokens": 1234}
    assert "cost unknown, 1,234 tokens" in admin.page_body(o)
    o["analysis"] = None
    assert "AI analysis" not in admin.page_body(o)


def test_the_privacy_page_says_whether_a_provider_gets_the_data(lab):
    from wowstats import account

    on = account.privacy_page(False, lab.env("a")).decode()
    assert "with the AI provider the host configured" in on and "never Battle.net data" in on
    off = account.privacy_page(False, {}).decode()
    assert "hasn't configured an AI provider" in off


# ---------------------------------------------------------------- unattended: done is the protocol's

# gpt-6.1-terra on 2026-11-21 ended its turn twice asking for "continue" in a
# new chat (the MCP prompt's own step 4). Nobody answers a server run.
ASKS = 'The analysis is in progress and saved on the server. Say "continue" in a new chat to pick up from the next lane.'

ADAPTER_ENVS = {
    "anthropic": {},
    "openai-chat": {"ANALYSIS_PROVIDER": "openai", "ANALYSIS_MODEL": "some-local-model", "ANALYSIS_BASE_URL": OLLAMA},
    "openai-responses": {"ANALYSIS_PROVIDER": "openai", "ANALYSIS_MODEL": "gpt-6.1-terra"},
}


def fake(kind: str, script: Script):
    return FakeAnthropic(script) if kind == "anthropic" else FakeOpenAI(script)


def said_to_model(client) -> list[str]:
    """The user messages in the last request after the prompt: the nudges."""
    req = client.requests[-1]
    msgs = req.get("messages") or req.get("input")
    return [m["content"] for m in msgs if m.get("role") == "user" and isinstance(m.get("content"), str)][1:]


def asks_to_continue(after_lanes: dict[int, int]):
    """`brain`, but after the n-th handed-in lane it ends `after_lanes[n]`
    turns in a row in words, asking the player to say continue."""
    inner = brain()
    step = next(inner)
    lanes = 0
    while True:
        got = yield step
        if step[0] == "analysis_submit" and step[1]["pass"] == "lane":
            lanes += 1
            for _ in range(after_lanes.get(lanes, 0)):
                yield ASKS
        try:
            step = inner.send(got)
        except StopIteration:
            return


def quits_after_a_lane():
    """`brain` until its first lane is handed in, then only asks to continue."""
    inner = brain()
    step = next(inner)
    while True:
        got = yield step
        if step[0] == "analysis_submit" and step[1]["pass"] == "lane":
            while True:
                yield ASKS
        step = inner.send(got)


def files(env: dict) -> dict:
    """The character's analysis, its run state and the 0.3.x file: their bytes, None when absent."""
    ps = drive.run_files(env, CHAR)
    assert [p.name for p in ps] == ["analysis.json", "drive.json", "analysis.json"]
    return {k: (p.read_bytes() if p.exists() else None) for k, p in zip(("kept", "run", "legacy"), ps, strict=True)}


@pytest.mark.parametrize("kind", ADAPTER_ENVS)
def test_a_model_that_asks_to_continue_is_nudged_and_finishes(lab, kind):
    e = lab.env("a", **ADAPTER_ENVS[kind])
    # Twice in a row after the first lane (the most it gets), once after the third.
    script = Script(asks_to_continue({1: 2, 3: 1}))
    client = fake(kind, script)
    r = service(e, client).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "done" and r["why"] == "" and r["nudges"] == 3, r
    assert r["conversations"] == 1  # nudged in the same conversation, not restarted
    assert sorted(kept(e)) == ["sessions-online", "xp-by-level"]
    assert load_state(e, CHAR)["runs"][-1]["kind"] == "server"
    nudges = said_to_model(client)
    assert len(nudges) == 3 and all(n.startswith("No one will reply") for n in nudges)
    assert 'analysis_submit(pass="synthesis")' in nudges[0] and "Next: Investigate lane L2" in nudges[0]
    assert service(e, client).last_try(e)["status"] == "done"


@pytest.mark.parametrize("kind", ADAPTER_ENVS)
def test_a_model_that_never_finishes_is_incomplete_and_the_old_text_stays(lab, kind):
    e = lab.env("a", **ADAPTER_ENVS[kind])
    # No analysis yet: an incomplete run leaves none, not its partial findings.
    q = Script(quits_after_a_lane())
    r = service(e, fake(kind, q)).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "incomplete" and r["nudges"] == auto.NUDGES, r
    assert r["why"] == ('the model ended its turn without analysis_submit(pass="synthesis") after 2 nudges '
                        "(at the lane pass L2)")
    assert kept(e) == {} and set(files(e).values()) == {None}
    # A finished run publishes; the next run starts clean, not on gone findings.
    drive._WS.clear()
    assert service(e, fake(kind, Script())).run_job("player", e, lab.ctx(e))["runs"][0]["status"] == "done"
    before, shown = files(e), analysis.answer(e, CHAR, "full")
    assert before["kept"] and before["run"] and before["legacy"] is None
    # Then one that never finishes: what the player sees stays byte for byte.
    q = Script(quits_after_a_lane())
    s = service(e, fake(kind, q))
    r = s.run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "incomplete" and q.calls > 2 and r["nudges"] == 2
    assert files(e) == before and analysis.answer(e, CHAR, "full") == shown
    last = s.last_try(e)
    assert last["status"] == "incomplete" and "after 2 nudges" in last["why"]
    from wowstats import admin

    assert admin.last_try_line(last).startswith("server analysis incomplete: the model ended its turn without")
    # The timer waits before trying again, as after any run that didn't finish.
    again = service(e, fake(kind, Script()), played=12 * H).run_job("auto", e, lab.ctx(e))
    assert not again["started"] and "stopped recently" in again["skipped"]


def test_a_stopped_run_keeps_the_old_text_too(lab, monkeypatch):
    e = lab.env("a")
    assert service(e, FakeAnthropic(Script())).run_job("player", e, lab.ctx(e))["runs"][0]["status"] == "done"
    before = files(e)
    monkeypatch.setattr(auto, "MAX_CALLS", 8)  # past the first lane's findings
    r = service(e, FakeAnthropic(Script())).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "stopped" and r["why"] == "stopped after 8 calls"
    assert files(e) == before


def test_the_unattended_wording_is_in_the_server_prompt_only(lab):
    from wowstats.mcp import prompts

    e = lab.env("a")
    ant = FakeAnthropic(Script())
    service(e, ant).run_job("player", e, lab.ctx(e))
    req = ant.requests[0]
    assert "unattended" in req["system"] and "no one will reply" in req["system"]
    assert "never ask to continue" in req["system"].lower()
    assert req["messages"][0]["content"].endswith(auto.UNATTENDED)
    for kind in ("openai-chat", "openai-responses"):
        eo = lab.env("o", **ADAPTER_ENVS[kind])
        oai = FakeOpenAI(Script())
        drive._WS.clear()
        service(eo, oai).run_job("player", eo, lab.ctx(eo))
        first = oai.requests[0]
        system = first.get("instructions") or first["messages"][0]["content"]
        user = (first.get("input") or first.get("messages"))[-1]["content"]
        assert system == auto.SYSTEM and user.endswith(auto.UNATTENDED)
    # The MCP client's prompt is unchanged: a person is there to say "continue".
    mcp = prompts.analyse_messages(CHAR)[0]["content"]["text"]
    assert mcp == prompts.analyse_text(CHAR, "")
    assert "unattended" not in mcp and "no one will reply" not in mcp.lower()
    assert 'say "continue" works in a new chat' in mcp


# ---------------------------------------------------------------- queued vs running, per person


def test_a_job_waiting_behind_another_tenant_is_queued_not_running(lab, monkeypatch):
    """One worker: a's refresh runs, b's waits. b hears "queued behind
    another refresh (position 1)", never "already running"; only a does."""
    ea, eb, ec = lab.env("a"), lab.env("b"), lab.env("c")
    s = service(ea, FakeAnthropic(Script()))
    put = []
    monkeypatch.setattr(s, "_enqueue", put.append)  # the worker is played by hand below
    a = s.refresh(lab.ctx(ea))
    assert a["started"] and a["state"] == "running" and a["running_for_you"]
    ka, kb = s.key(ea), s.key(eb)
    job_a = s._next(ka)  # the worker takes a's job
    assert job_a[0] == "player" and ka in s._running
    b = s.refresh(lab.ctx(eb))
    assert b["started"] and b["state"] == "queued" and not b["running_for_you"]
    assert b["position"] == 1 and "queued behind another refresh (position 1)" in b["note"]
    assert "already running" not in json.dumps(b)
    c = s.refresh(lab.ctx(ec))
    assert c["position"] == 2 and "queued behind 2 other refreshes (position 2)" in c["note"]
    again = s.refresh(lab.ctx(ea))
    assert not again["started"] and again["skipped"] == "no new refresh: a refresh is already running for you"
    # a finishes; b's job is next and runs as the player's own.
    assert s._run_job(ka, job_a)["runs"][0]["status"] == "done"
    assert s.refresh(lab.ctx(eb))["position"] == 1 and s.refresh(lab.ctx(eb))["ahead"] == 0
    assert s._next(kb)[0] == "player" and s.refresh(lab.ctx(eb))["state"] == "running"
    assert put == [ka, kb, s.key(ec)]


def test_a_second_call_while_queued_adds_no_job(lab, monkeypatch):
    ea, eb = lab.env("a"), lab.env("b")
    s = service(ea, FakeAnthropic(Script()), played=10 * H)
    put = []
    monkeypatch.setattr(s, "_enqueue", put.append)
    s.refresh(lab.ctx(ea))
    s._next(s.key(ea))
    # b's rebuild queued a timer check; b then calls: still one job, now theirs (it always runs).
    assert s.on_rebuild(eb) is None and s._queued[s.key(eb)][0] == "auto"
    for _ in range(3):
        out = s.refresh(lab.ctx(eb))
        assert not out["started"] and out["state"] == "queued" and out["position"] == 1
        assert out["skipped"] == "no new refresh: your refresh is queued behind another refresh (position 1)"
    assert list(s._queued) == [s.key(eb)] and s._queued[s.key(eb)][0] == "player"
    assert put == [s.key(ea), s.key(eb)]
    assert s.on_rebuild(eb) == "busy"


def drain(s: auto.Service, timeout: float = 60.0) -> None:
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with s._lock:
            if not s._queued and not s._running:
                return
        time.sleep(0.05)
    raise AssertionError("the worker never finished")


# ---------------------------------------------------------------- install-wide vs per person


def test_the_budget_is_the_install_and_yours_is_your_own(lab, monkeypatch):
    ea, eb = lab.env("a"), lab.env("b")
    s = service(ea, FakeAnthropic(Script()))
    ra = s.run_job("player", ea, lab.ctx(ea))["runs"][0]
    drive._WS.clear()
    s2 = service(eb, FakeAnthropic(Script()))
    s2.run_job("player", eb, lab.ctx(eb))
    s2.run_job("player", eb, lab.ctx(eb))  # nothing new to say still counts as a run
    monkeypatch.setattr(s, "_enqueue", lambda k: None)
    out = s.refresh(lab.ctx(ea))
    b, mine = out["budget"], out["yours"]
    assert b["scope"] == "install" and "whole install" in b["said"] and b["runs"] == 3
    assert mine["scope"] == "you" and mine["said"] == "this month, your own runs only" and mine["runs"] == 1
    assert mine["spent"] == pytest.approx(ra["usd"], abs=0.01) and mine["tokens"] == ra["tokens"]
    assert b["spent"] > mine["spent"] and "note" not in mine
    assert auto.mine(eb, s.spend)["runs"] == 2
    # The split is per tenant number, never a name.
    tenants = s.spend.this_month()["tenants"]
    assert set(tenants) == {lab.acct("a"), lab.acct("b")}
    # A month recorded before the split says so instead of undercounting silently.
    d = s.spend.read()
    d["months"][auto.month()]["runs"] += 4
    auto._write_json(s.spend.path, d)
    assert "4 of the install's runs this month predate" in auto.mine(ea, s.spend)["note"]


def test_admin_labels_the_install_and_shows_each_persons_runs():
    from wowstats import admin

    now = 1_800_000_000.0
    person = {"slug": "friend", "owner": False, "status": "active", "admitted": True, "full": None, "mcp": None,
              "upload": {}, "os": None, "version": None, "chars": 1, "public_on": False, "build": None,
              "addons": None, "stale": [], "error": "", "name_conflict": None,
              "analysis": {"chars": [], "refresh_after": "10 h 00", "due": False, "at": now - 60, "by": "server",
                           "last_try": None,
                           "month": {"runs": 2, "spent": 3.1, "cost_known": True}}}
    o = {"checked_at": now, "version": "0.7.0", "answers": {"built_at": None, "error": ""}, "publisher": None,
         "exporter": None, "disk": {"bytes": 0, "capped": False}, "checks": [], "people": [person],
         "analysis": {"cost_known": True, "spent": 5.0, "cap": 50.0, "left": 45.0, "tokens": 1, "tokens_cap": None,
                      "runs": 3, "month": "2026-11"}}
    page = admin.page_body(o)
    assert "3 runs this month (2026-11), whole install" in page
    assert "2 server runs, $3.10 this month" in page


# ---------------------------------------------------------------- last refresh and last try, per person


def test_a_server_run_sets_last_refresh_and_last_try_for_its_player(lab, monkeypatch):
    """The page rebuild's timer runs the job on the worker; the player's
    next dashboard_refresh sees both, and another player sees neither."""
    ea, eb = lab.env("a"), lab.env("b")
    s = service(ea, FakeAnthropic(Script()), played=11 * H)
    assert s.on_rebuild(ea) is None
    drain(s)
    m, lt = refreshed(ea), s.last_try(ea)
    assert m["by"] == "server" and m["via"] == "auto" and lt["by"] == "server" and lt["status"] == "done"
    monkeypatch.setattr(s, "_enqueue", lambda k: None)
    seen = s.refresh(lab.ctx(ea))
    assert seen["timer"]["last_refresh"]["by"] == "server" and seen["timer"]["last_refresh"]["at"] == m["at"]
    assert seen["timer"]["chars"][0]["last_refresh"] == m
    assert seen["last_try"]["status"] == "done" and seen["last_try"]["by"] == "server"
    other = s.refresh(lab.ctx(eb))
    assert other["timer"]["last_refresh"] is None and other["last_try"] is None


def test_the_run_log_names_the_character(lab, capsys):
    e = lab.env("a")
    service(e, FakeAnthropic(Script())).run_job("player", e, lab.ctx(e))
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"what": "run"' in x]
    assert lines and lines[-1]["char"] == CHAR and lines[-1]["tenant"] == lab.acct("a")
