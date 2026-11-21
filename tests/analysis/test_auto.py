"""The server's own analysis (wowstats.analysis.auto): the server plays the
MCP client through a provider adapter, on a fake client for each provider,
no network. One scripted model drives a whole run through either adapter;
the findings it leaves are the same. The per-player played-time timer, the
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
    # A client's run marks the timer too: never measure play over the network here.
    monkeypatch.setattr(auto, "played_since", lambda env, since, store=None: 0.0)
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
    """The model behind both fakes: one tool call a turn, from `brain`."""

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
        if step is None:
            msg = NS(content=[NS(type="text", text="Done.")], stop_reason="end_turn", usage=usage, model=kw["model"])
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


class FakeOpenAI:
    """client.chat.completions.create(...) answering the next scripted turn."""

    def __init__(self, script: Script, refuse_tools: bool = False):
        self.script, self.requests, self.refuse_tools = script, [], refuse_tools
        self.chat = NS(completions=NS(create=self.create))

    def create(self, model, messages, tools):
        self.requests.append({"model": model, "messages": list(messages), "tools": tools})
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
        if step is None:
            msg, reason = NS(content="Done.", tool_calls=None, refusal=None), "stop"
        else:
            name, args = step
            tc = NS(id=f"call_{self.script.calls}", type="function",
                    function=NS(name=name, arguments=json.dumps(args)))
            msg, reason = NS(content=None, tool_calls=[tc], refusal=None), "tool_calls"
        return NS(choices=[NS(message=msg, finish_reason=reason)], usage=usage, model=model)


def service(env: dict, client, played=0.0, chars=(CHAR,)) -> auto.Service:
    return auto.Service(env, client=client, played=lambda e, since, store=None: played,
                        chars=lambda e, since: list(chars))


def kept(env: dict) -> dict:
    s = load_state(env, CHAR)
    return {k: (f["text"], f.get("sql")) for k, f in (s.get("findings") or {}).items()}


# ---------------------------------------------------------------- one run, both adapters


def test_the_same_script_records_the_same_findings_through_both_adapters(lab):
    ea = lab.env("a")
    eo = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="some-local-model",
                 ANALYSIS_BASE_URL="http://ollama:11434/v1")
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
    assert not out["started"] and "not due: 9 h 59" in out["skipped"] and script.calls == 0
    s = service(e, FakeAnthropic(script), played=10 * H)
    out = s.run_job("auto", e, lab.ctx(e))
    assert out["started"] and out["runs"][0]["status"] == "done"
    m = auto.marker(e)
    assert m["by"] == "server" and m["via"] == "auto" and m["played_total_s"] == 10 * H


def test_the_rebuild_hook_never_blocks_and_never_doubles(lab, monkeypatch):
    e = lab.env("a")
    s = service(e, FakeAnthropic(Script()), played=10 * H)
    queued = []
    monkeypatch.setattr(s, "_enqueue", queued.append)
    assert s.on_rebuild(e) is None and len(queued) == 1 and queued[0][0] == "auto"
    assert s.on_rebuild(e) == "busy"  # its check hasn't run yet
    s._busy.clear()
    assert s.on_rebuild(e) == "checked recently"
    assert service(lab.env("a", ANALYSIS_AUTO="off"), None).on_rebuild(e) == "off"


def test_a_players_call_always_runs_and_resets_the_timer(lab):
    e = lab.env("a")
    s = service(e, FakeAnthropic(Script()), played=2 * H)  # far from due
    out = s.refresh(lab.ctx(e), wait=True)
    assert out["started"] and out["runs"][0]["status"] == "done" and not out["timer"]["due"]
    assert out["budget"]["currency"] == "USD" and out["budget"]["left"] <= 50
    m = auto.marker(e)
    assert m["by"] == "player" and m["via"] == "dashboard_refresh" and m["played_total_s"] == 2 * H
    # The timer starts over: the next rebuild check finds nothing due.
    s2 = service(e, FakeAnthropic(Script()), played=0.0)
    assert "not due" in s2.run_job("auto", e, lab.ctx(e))["skipped"]


def test_a_full_analysis_from_the_players_own_client_resets_the_timer(lab, monkeypatch):
    from tests.analysis.test_analysis import walk

    e = lab.env("a")
    monkeypatch.setattr(auto, "played_since", lambda env, since, store=None: 4 * H)
    walk(e, drive)
    m = auto.marker(e)
    assert m["by"] == "player" and m["via"] == "client" and m["played_total_s"] == 4 * H
    assert load_state(e, CHAR)["runs"][-1]["kind"] == "client"


def test_the_last_try_waits_before_the_timer_tries_again(lab):
    e = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="tiny")
    s = service(e, FakeOpenAI(Script(), refuse_tools=True), played=12 * H)
    out = s.run_job("auto", e, lab.ctx(e))
    assert out["runs"][0]["status"] == "stopped"
    again = s.run_job("auto", e, lab.ctx(e))
    assert not again["started"] and "stopped recently" in again["skipped"]


# ---------------------------------------------------------------- money


def test_no_run_starts_past_the_budget(lab):
    e = lab.env("a", ANALYSIS_MONTHLY_BUDGET="1")
    script = Script()
    s = service(e, FakeAnthropic(script), played=20 * H)
    s.spend.add(0.9, {"input": 1})
    out = s.refresh(lab.ctx(e), wait=True)
    assert not out["started"] and out["skipped"].startswith("budget spent") and script.calls == 0
    assert "budget spent" in s.run_job("auto", e, lab.ctx(e))["runs"][0]["why"]


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
    assert auto.marker(eb) == {}


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
    e = lab.env("o", ANALYSIS_PROVIDER="openai", ANALYSIS_MODEL="tiny")
    s = service(e, FakeOpenAI(Script(), refuse_tools=True))
    r = s.run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "stopped" and r["why"].startswith("tool calling is required")
    assert s.last_try(e)["why"].startswith("tool calling is required")
    # A model that answers in words instead of calling a tool: the same.
    words = Script(iter(()))
    r = service(e, FakeOpenAI(words)).run_job("player", e, lab.ctx(e))["runs"][0]
    assert r["status"] == "stopped" and "without calling a tool" in r["why"]


def test_the_mcp_tool_runs_for_the_caller_only(lab, monkeypatch):
    ea = lab.env("a")
    s = service(ea, FakeAnthropic(Script()))
    seen = []
    monkeypatch.setattr(s, "_enqueue", seen.append)
    monkeypatch.setattr(auto, "_SERVICE", s)
    out = tools.call(lab.ctx(ea), "dashboard_refresh", {})
    assert out["started"] and seen[0][1]["ANALYSIS_STATE_DIR"] == ea["ANALYSIS_STATE_DIR"]
    assert str(seen[0][2].store.account_id) == lab.acct("a")
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
                                         ANALYSIS_BASE_URL="http://ollama:11434/v1")).make_client()
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
    s = auto.Service(e, played=lambda e, since, store=None: 0.0, chars=lambda e, since: [CHAR])
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


def test_admin_shows_the_month_and_each_players_timer():
    from wowstats import admin

    now = 1_800_000_000.0
    person = {"slug": "friend", "owner": False, "status": "active", "admitted": True, "full": None, "mcp": None,
              "upload": {}, "os": None, "version": None, "chars": 1, "public_on": False, "build": None,
              "addons": None, "stale": ["server analysis stopped: budget spent"], "error": "",
              "name_conflict": None,
              "analysis": {"played_since": "10 h 05", "due": True, "at": now - 7200, "by": "server",
                           "last_try": None}}
    o = {"checked_at": now, "version": "0.7.0", "answers": {"built_at": None, "error": ""}, "publisher": None,
         "exporter": None, "disk": {"bytes": 0, "capped": False}, "checks": [], "people": [person],
         "analysis": {"cost_known": True, "spent": 50.0, "cap": 50.0, "left": 0.0, "tokens": 1, "tokens_cap": None,
                      "runs": 16, "month": "2026-11"}}
    page = admin.page_body(o)
    assert "AI analysis" in page and "$50.00 of $50, $0.00 left" in page and "16 runs" in page
    assert "10 h 05 played since" in page and "by the server" in page and "server analysis stopped" in page
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
