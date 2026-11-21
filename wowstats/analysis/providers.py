"""The model behind the server's own analysis, one thin adapter per provider.

The analysis loop (wowstats.analysis.auto) is provider-neutral: tool
definitions as plain JSON Schema, the read-only analysis tools, the same
prompts. An adapter does two things only: send the conversation and the
tools and hand back the reply as a Turn (text, tool calls, usage, why it
stopped); and put tool results, or a user message (`say`), back into the
conversation in its provider's shape. No shim library: each adapter speaks its provider's
official SDK, imported only when that provider is the configured one.

  anthropic  Anthropic's SDK (`analysis` extra): streaming, adaptive
             thinking, prompt caching, a task budget, server-side refusal
             fallbacks (the claude-api skill's defaults).
  openai     OpenAI's SDK (`analysis-openai` extra). OpenAI itself (no
             ANALYSIS_BASE_URL) through the Responses API: its reasoning
             models refuse function tools on Chat Completions ("Function tools
             with reasoning_effort are not supported ... use /v1/responses",
             gpt-6 and later, 2026-11-21). Any other OpenAI-compatible
             endpoint (OpenRouter, Ollama, vLLM, LM Studio) through Chat
             Completions, the API they all speak. Tool calling is required: an
             endpoint or model that refuses tools fails the run with
             ToolsRefused, never a quieter run without them.
"""

from __future__ import annotations

import json
import re
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

    def say(self, text: str) -> None:
        """A user message after a turn that ended in words (the runner's
        nudge). Appended after the assistant turn as it came: the history
        stays append-only, so its thinking blocks stay valid."""
        self.messages.append({"role": "user", "content": text})


# ---------------------------------------------------------------- OpenAI-compatible


# How an endpoint says the model has no tool calling at all (Ollama: "...
# does not support tools"). Not any 4xx that names tools: OpenAI's "Function
# tools with reasoning_effort are not supported for gpt-6-luna in
# /v1/chat/completions" names a wrong route, and reading every 400 with "tool"
# in it as a refusal hid that behind "refused tool calling" (2026-11-21).
NO_TOOLS = re.compile(r"(does not|doesn't|do not|don't|cannot|can't) (support|use) "
                      r"(function calling|tool calling|tool use|tools|functions)"
                      r"|(tools|tool calling|tool use|function calling) (is|are) not (supported|available)", re.I)
# Anything key-shaped in an error message is masked before it's shown or logged.
SECRETISH = re.compile(r"sk-[^\s'\"]+|[A-Za-z0-9_\-]{32,}")


def said(e: Exception) -> str:
    """What the endpoint said, one line: the error body's message (never
    headers or the request), key-shaped strings masked, 300 characters."""
    body = getattr(e, "body", None)
    msg = ""
    if isinstance(body, dict):
        err = body.get("error", body)
        msg = str(err.get("message") or "") if isinstance(err, dict) else str(err or "")
    msg = msg or str(getattr(e, "message", "") or e)
    msg = re.sub(r"^Error code: \d+ - ", "", msg)
    return SECRETISH.sub("[masked]", " ".join(msg.split()))[:300]


def refused_tools(e: Exception, model: str) -> None:
    """Raise ToolsRefused when the endpoint said the model has no tools; any
    other error goes on as itself, so the run reports what was really said."""
    if getattr(e, "status_code", None) in (400, 404, 422) and NO_TOOLS.search(said(e)):
        raise ToolsRefused(f"the endpoint refused tool calling for {model} ({said(e)})") from None


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
            refused_tools(e, self.model)
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

    def say(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})


class OpenAIResponses:
    """OpenAI's own API through Responses, stateless: store=False, and each
    turn's output items (reasoning included, encrypted) go back as input, so
    the model keeps its reasoning across tool calls without OpenAI keeping
    the conversation."""

    name = "openai"

    def __init__(self, client, model: str, system: str, tools: list[dict]):
        self.client, self.model, self.system = client, model, system
        self.tools = [{"type": "function", "name": t["name"], "description": t["description"],
                       "parameters": t["input_schema"], "strict": False} for t in tools]
        self.input: list = []

    def start(self, text: str) -> None:
        self.input = [{"role": "user", "content": text}]

    def send(self) -> Turn:
        try:
            r = self.client.responses.create(model=self.model, instructions=self.system, input=self.input,
                                             tools=self.tools, store=False,
                                             include=["reasoning.encrypted_content"])
        except Exception as e:
            refused_tools(e, self.model)
            raise
        items = list(getattr(r, "output", None) or [])
        calls, texts, refused = [], [], False
        for it in items:
            kind = getattr(it, "type", "")
            if kind == "function_call":
                try:
                    args = json.loads(it.arguments or "{}")
                except ValueError:
                    args = None
                calls.append(Call(it.call_id, it.name, args if isinstance(args, dict) else None,
                                  "" if isinstance(args, dict) else "the arguments weren't a JSON object"))
            elif kind == "message":
                for c in getattr(it, "content", None) or []:
                    if getattr(c, "type", "") == "output_text":
                        texts.append(c.text)
                    elif getattr(c, "type", "") == "refusal":
                        refused = True
            # Back as they came: the next request carries the reasoning and the calls.
            self.input.append(it.model_dump(mode="json", exclude_none=True) if hasattr(it, "model_dump") else it)
        why = getattr(getattr(r, "incomplete_details", None), "reason", None)
        if calls:
            stop = "tools"
        elif refused or why == "content_filter":
            stop = "refusal"
        elif why == "max_output_tokens":
            stop = "length"
        else:
            stop = "end"
        u = getattr(r, "usage", None)
        prompt = int(getattr(u, "input_tokens", 0) or 0) if u else 0
        details = getattr(u, "input_tokens_details", None) if u else None
        cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        usage = {"input": max(0, prompt - cached), "output": int(getattr(u, "output_tokens", 0) or 0) if u else 0,
                 "cache_read": cached, "cache_write": 0}
        return Turn("".join(texts), calls, usage, stop, str(getattr(r, "model", "") or self.model), r)

    def results(self, results: list[tuple[str, str, bool]]) -> None:
        for i, text, err in results:
            self.input.append({"type": "function_call_output", "call_id": i,
                               "output": ("Error: " + text) if err else text})

    def say(self, text: str) -> None:
        self.input.append({"role": "user", "content": text})


ADAPTERS = {"anthropic": Anthropic, "openai": OpenAI, "openai-responses": OpenAIResponses}
