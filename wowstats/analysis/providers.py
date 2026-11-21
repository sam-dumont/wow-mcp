"""The model behind the server's own analysis, one thin adapter per provider.

The analysis loop (wowstats.analysis.auto) is provider-neutral: tool
definitions as plain JSON Schema, the read-only analysis tools, the same
prompts. An adapter does two things only: send the conversation and the
tools and hand back the reply as a Turn (text, tool calls, usage, why it
stopped); and put tool results back into the conversation in its
provider's shape. No shim library: each adapter speaks its provider's
official SDK, imported only when that provider is the configured one.

  anthropic  Anthropic's SDK (`analysis` extra): streaming, adaptive
             thinking, prompt caching, a task budget, server-side refusal
             fallbacks (the claude-api skill's defaults).
  openai     OpenAI's SDK (`analysis-openai` extra) on any OpenAI-compatible
             Chat Completions endpoint: OpenAI, OpenRouter, Ollama, vLLM,
             LM Studio. Tool calling is required: an endpoint or model that
             refuses tools fails the run with ToolsRefused, never a quieter
             run without them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Anthropic: the run's request shape (claude-api skill, Opus 5.5 defaults).
EFFORT = "medium"  # Opus 5.5's own default, said out loud
MAX_TOKENS = 64000  # streamed: no HTTP timeout; one turn rarely writes a tenth of it
TASK_BETA = "task-budgets-2026-03-13"
# The model paces itself to this many tokens (what it writes plus the tool
# results it reads) per conversation; the loop's call and money caps bound the run.
TASK_BUDGET = 200_000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# A refusal is re-run on the API's default fallback model, inside the same call.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})


@dataclass
class Call:
    id: str
    name: str
    args: dict | None  # None: the arguments weren't a JSON object (`bad`)
    bad: str = ""


@dataclass
class Turn:
    text: str
    calls: list[Call]
    # input (uncached), output, cache_read, cache_write
    usage: dict[str, int]
    # "tools" (calls to run), "end", "pause" (send again), "refusal", "length"
    stop: str
    model: str
    raw: object = field(default=None, repr=False)


class ToolsRefused(Exception):
    """The endpoint or the model refused tool calling: the analysis can't run."""


# ---------------------------------------------------------------- Anthropic


class Anthropic:
    name = "anthropic"

    def __init__(self, client, model: str, system: str, tools: list[dict]):
        self.client, self.model, self.system, self.tools = client, model, system, tools
        self.messages: list = []

    def start(self, text: str) -> None:
        self.messages = [{"role": "user", "content": text}]

    def request(self) -> dict:
        kw = {"model": self.model, "max_tokens": MAX_TOKENS, "system": self.system, "tools": self.tools,
              "messages": self.messages, "thinking": {"type": "adaptive"},
              "output_config": {"effort": EFFORT, "task_budget": {"type": "tokens", "total": TASK_BUDGET}},
              "cache_control": {"type": "ephemeral"}, "betas": [TASK_BETA]}
        if self.model in FALLBACK_MODELS:
            kw["betas"] = [TASK_BETA, FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    def send(self) -> Turn:
        with self.client.beta.messages.stream(**self.request()) as s:
            msg = s.get_final_message()
        blocks = list(getattr(msg, "content", None) or [])
        # Back unchanged: thinking blocks must be passed back as they came.
        self.messages.append({"role": "assistant", "content": blocks})
        calls = [Call(b.id, b.name, b.input if isinstance(b.input, dict) else None,
                      "" if isinstance(b.input, dict) else "the arguments weren't a JSON object")
                 for b in blocks if getattr(b, "type", "") == "tool_use"]
        text = "".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text")
        reason = getattr(msg, "stop_reason", None)
        stop = {"tool_use": "tools", "pause_turn": "pause", "refusal": "refusal", "max_tokens": "length"}.get(
            reason, "end")
        if stop == "end" and calls:
            stop = "tools"
        u = msg.usage

        def g(k):
            return int((u.get(k) if isinstance(u, dict) else getattr(u, k, 0)) or 0)

        usage = {"input": g("input_tokens"), "output": g("output_tokens"), "cache_read": g("cache_read_input_tokens"),
                 "cache_write": g("cache_creation_input_tokens")}
        return Turn(text, calls, usage, stop, str(getattr(msg, "model", "") or self.model), msg)

    def results(self, results: list[tuple[str, str, bool]]) -> None:
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": i, "content": text, **({"is_error": True} if err else {})}
            for i, text, err in results]})


# ---------------------------------------------------------------- OpenAI-compatible


TOOL_WORDS = ("tool", "function")


class OpenAI:
    name = "openai"

    def __init__(self, client, model: str, system: str, tools: list[dict]):
        self.client, self.model = client, model
        self.system = system
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": t["input_schema"]}} for t in tools]
        self.messages: list = []

    def start(self, text: str) -> None:
        self.messages = [{"role": "system", "content": self.system}, {"role": "user", "content": text}]

    def send(self) -> Turn:
        try:
            r = self.client.chat.completions.create(model=self.model, messages=self.messages, tools=self.tools)
        except Exception as e:
            # A 400 that names tools or functions: the model or endpoint has
            # no tool calling (Ollama: "does not support tools").
            if getattr(e, "status_code", None) in (400, 404, 422) and any(
                    w in str(getattr(e, "message", "") or e).lower() for w in TOOL_WORDS):
                raise ToolsRefused(f"the endpoint refused tool calling for {self.model}") from None
            raise
        ch = r.choices[0]
        m = ch.message
        calls, raw_calls = [], []
        for tc in m.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            raw_calls.append({"id": tc.id, "type": "function",
                              "function": {"name": fn.name, "arguments": fn.arguments or "{}"}})
            try:
                args = json.loads(fn.arguments or "{}")
            except ValueError:
                args = None
            calls.append(Call(tc.id, fn.name, args if isinstance(args, dict) else None,
                              "" if isinstance(args, dict) else "the arguments weren't a JSON object"))
        msg = {"role": "assistant", "content": m.content or ""}
        if raw_calls:
            msg["tool_calls"] = raw_calls
        self.messages.append(msg)
        reason = ch.finish_reason
        stop = ("tools" if calls else {"length": "length", "content_filter": "refusal"}.get(reason, "end"))
        if getattr(m, "refusal", None) and not calls:
            stop = "refusal"
        u = r.usage
        prompt = int(getattr(u, "prompt_tokens", 0) or 0) if u else 0
        details = getattr(u, "prompt_tokens_details", None) if u else None
        cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        usage = {"input": max(0, prompt - cached), "output": int(getattr(u, "completion_tokens", 0) or 0) if u else 0,
                 "cache_read": cached, "cache_write": 0}
        return Turn(m.content or "", calls, usage, stop, str(getattr(r, "model", "") or self.model), r)

    def results(self, results: list[tuple[str, str, bool]]) -> None:
        for i, text, err in results:
            self.messages.append({"role": "tool", "tool_call_id": i, "content": ("Error: " + text) if err else text})


ADAPTERS = {"anthropic": Anthropic, "openai": OpenAI}
