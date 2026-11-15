"""Groq LLM integration and the tool-calling loop.

Mode selection (read at request time, so a restart after editing .env is all that's needed):
  * GROQ_API_KEY set AND DEMO_MODE=false  -> live Groq responses
  * otherwise                             -> deterministic demo responses (clearly labeled)

The API key is only read on the backend, never logged, never sent to the frontend,
and never passed to the MCP server processes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from health_core import settings

from .mcp_client import MCPClientError
from .tooling import ScopedToolbox

log = logging.getLogger("app.llm")

MAX_TOOL_ITERATIONS = 5
MAX_TOOL_RESULT_CHARS = 6000


def mode_info() -> dict[str, Any]:
    has_key = bool(settings.groq_api_key())
    demo = settings.demo_mode()
    live = has_key and not demo
    if live:
        reason = "Groq API key configured and DEMO_MODE=false."
    elif not has_key and not demo:
        reason = "DEMO_MODE=false but GROQ_API_KEY is empty — using deterministic demo responses."
    elif has_key:
        reason = "GROQ_API_KEY is set but DEMO_MODE=true — set DEMO_MODE=false to use Groq."
    else:
        reason = "No GROQ_API_KEY configured — using deterministic demo responses."
    return {
        "mode": "groq" if live else "demo",
        "demo_mode_setting": demo,
        "groq_key_configured": has_key,  # boolean only — the key itself is never exposed
        "model": settings.groq_model(),
        "reason": reason,
        "max_tool_iterations": MAX_TOOL_ITERATIONS,
    }


class ChatCompleter(Protocol):
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
                       tool_choice: str) -> Any: ...


class GroqLLM:
    def __init__(self, api_key: str, model: str, timeout: float = 45.0) -> None:
        from groq import AsyncGroq  # imported lazily so demo mode never needs network setup

        self.model = model
        self._client = AsyncGroq(api_key=api_key, timeout=timeout, max_retries=2)

    async def complete(self, messages, tools, tool_choice):
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.2,
                                  "max_completion_tokens": 1200}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        resp = await self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message


def get_llm() -> GroqLLM | None:
    info = mode_info()
    if info["mode"] != "groq":
        return None
    return GroqLLM(settings.groq_api_key(), info["model"])


@dataclass
class LoopResult:
    text: str
    iterations: int
    stopped_reason: str  # "final_answer" | "max_iterations"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def _assistant_message(msg: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
            for tc in msg.tool_calls
        ]
    return out


async def run_tool_loop(
    llm: ChatCompleter,
    system_prompt: str,
    conversation: list[dict[str, Any]],
    toolbox: ScopedToolbox,
    max_iterations: int = MAX_TOOL_ITERATIONS,
) -> LoopResult:
    """Send tools → validate requested calls → execute via MCP → return results → final answer."""
    tools = toolbox.groq_tool_specs()
    allowed = {t["function"]["name"] for t in tools}
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *conversation]
    trace: list[dict[str, Any]] = []

    for iteration in range(1, max_iterations + 1):
        msg = await llm.complete(messages, tools or None, "auto")
        if not getattr(msg, "tool_calls", None):
            return LoopResult(msg.content or "", iteration, "final_answer", trace)

        messages.append(_assistant_message(msg))
        for tc in msg.tool_calls:
            name = tc.function.name
            entry: dict[str, Any] = {"tool": name, "iteration": iteration}
            try:
                if name not in allowed:
                    raise MCPClientError(f"Tool '{name}' is not available to this agent")
                raw = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise MCPClientError(f"Arguments were not valid JSON: {exc.msg}") from None
                if not isinstance(args, dict):
                    raise MCPClientError("Arguments must be a JSON object")
                data = await toolbox.call(name, args)
                content = json.dumps(data, default=str)
                if len(content) > MAX_TOOL_RESULT_CHARS:
                    content = content[:MAX_TOOL_RESULT_CHARS] + '... [truncated]"'
                entry["status"] = "ok"
            except MCPClientError as exc:
                content = json.dumps({"error": str(exc)})
                entry["status"] = "error"
                entry["error"] = str(exc)
            trace.append(entry)
            messages.append({"role": "tool", "tool_call_id": tc.id, "name": name, "content": content})

    # Iteration budget exhausted: ask for a final answer with tools disabled.
    messages.append({"role": "system", "content": "Tool budget reached. Answer now using only the tool results above."})
    msg = await llm.complete(messages, None, "none")
    return LoopResult(msg.content or "", max_iterations, "max_iterations", trace)
