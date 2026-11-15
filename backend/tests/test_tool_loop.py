"""Groq tool-calling loop, exercised with a SCRIPTED fake model (no network, no API key).

The fake replaces only the Groq HTTP call; every tool call still goes through the real
ScopedToolbox → MCPManager → stdio MCP server path.
"""

import json
from types import SimpleNamespace as NS

from app.agents import AGENTS, AgentContext
from app.llm import mode_info, run_tool_loop
from app.tooling import ScopedToolbox


def tool_msg(*calls):
    return NS(content=None, tool_calls=[
        NS(id=f"call_{i}", function=NS(name=name, arguments=args)) for i, (name, args) in enumerate(calls)
    ])


def final(text):
    return NS(content=text, tool_calls=None)


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def complete(self, messages, tools, tool_choice):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools, "tool_choice": tool_choice})
        return self.script.pop(0) if self.script else final("done")


def box_for(manager, agent="monitoring", pid=1):
    a = AGENTS[agent]
    return ScopedToolbox(manager, pid, a.name, a.allowed_tools, "loop-test")


async def test_full_loop_executes_tools_via_mcp(manager):
    llm = ScriptedLLM([tool_msg(("get_recent_readings", json.dumps({"limit": 2}))), final("Latest readings look steady.")])
    res = await run_tool_loop(llm, "sys", [{"role": "user", "content": "latest?"}], box_for(manager))
    assert res.text == "Latest readings look steady." and res.stopped_reason == "final_answer"
    assert res.tool_calls == [{"tool": "get_recent_readings", "iteration": 1, "status": "ok"}]

    # Tools were sent without patient_id, and the real MCP result was returned to the model.
    first = llm.requests[0]
    assert {t["function"]["name"] for t in first["tools"]} == set(AGENTS["monitoring"].allowed_tools)
    assert all("patient_id" not in t["function"]["parameters"]["properties"] for t in first["tools"])
    tool_result = [m for m in llm.requests[1]["messages"] if m["role"] == "tool"][0]
    payload = json.loads(tool_result["content"])
    assert payload["count"] == 2 and all(r["patient_id"] == 1 for r in payload["readings"])


async def test_invalid_tool_requests_are_rejected_and_reported(manager):
    llm = ScriptedLLM([
        tool_msg(
            ("get_recent_readings", json.dumps({"patient_id": 2})),       # tries to switch patient
            ("evaluate_readings", json.dumps({"days": 7})),              # not allowed for Monitoring
            ("get_reading_history", "{not json"),                         # malformed JSON
            ("calculate_trends", json.dumps({"days": 9999})),            # out of range
        ),
        final("ok"),
    ])
    res = await run_tool_loop(llm, "sys", [{"role": "user", "content": "x"}], box_for(manager))
    assert [c["status"] for c in res.tool_calls] == ["error"] * 4
    tool_msgs = [m for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 4 and all("error" in json.loads(m["content"]) for m in tool_msgs)
    assert "set by the system" in tool_msgs[0]["content"]


async def test_max_iterations_limit(manager):
    looping = [tool_msg(("get_patient_profile", "{}")) for _ in range(10)]
    llm = ScriptedLLM(looping)
    res = await run_tool_loop(llm, "sys", [{"role": "user", "content": "x"}], box_for(manager), max_iterations=3)
    assert res.stopped_reason == "max_iterations" and res.iterations == 3
    assert len(res.tool_calls) == 3
    assert llm.requests[-1]["tools"] is None  # final answer forced with tools disabled


async def test_agent_falls_back_to_labeled_demo_when_groq_fails(manager):
    class Broken:
        async def complete(self, *a, **k):
            raise ConnectionError("network down")

    ctx = AgentContext(manager, 1, "fallback-test", Broken())  # type: ignore[arg-type]
    res = await AGENTS["alert"].run(ctx, "any alerts?")
    assert res.mode == "demo-fallback" and "network down" in res.error
    assert res.text  # deterministic content still produced


def test_mode_selection(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_real")
    monkeypatch.setenv("DEMO_MODE", "false")
    info = mode_info()
    assert info["mode"] == "groq" and info["groq_key_configured"] is True
    assert "gsk_test_not_real" not in json.dumps(info)  # key never exposed
    monkeypatch.setenv("DEMO_MODE", "true")
    assert mode_info()["mode"] == "demo"
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("DEMO_MODE", "false")
    assert mode_info()["mode"] == "demo"
