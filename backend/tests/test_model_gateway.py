"""Model-gateway fault injection, exercised with a stand-in LLM client (tests never call Groq)."""

import random

from app.agents import AGENTS, AgentContext
from app.resilience.injector import FAULTS, INJECTOR, ArmedFault


class FakeLLM:
    model = "fake-model"

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools, tool_choice):
        self.calls += 1

        class Msg:
            content = "Readings look steady."
            tool_calls = None

        return Msg()


def arm(run_id, fault_id, scope=frozenset({1}), params=None):
    INJECTOR.arm(ArmedFault(run_id, "monitoring", "llm", FAULTS[fault_id], params or {}, 100, "random", scope,
                            random.Random(0)))


async def test_model_faults_fall_back_to_labelled_deterministic_answer(manager):
    agent, llm = AGENTS["monitoring"], FakeLLM()
    ctx = AgentContext(manager, 1, "t-model", llm)

    ok = await agent.run(ctx, "How are things?")
    assert ok.mode == "groq" and ok.text == "Readings look steady."

    arm(9001, "fault.model_error")
    try:
        res = await agent.run(ctx, "How are things?")
    finally:
        INJECTOR.disarm(9001)
    assert res.mode == "demo-fallback" and "injected fault.model_error" in res.error
    assert "Readings for" in res.text  # deterministic answer from real MCP tools

    arm(9002, "fault.model_degradation")
    try:
        res = await agent.run(ctx, "How are things?")
    finally:
        INJECTOR.disarm(9002)
    assert res.mode == "demo-fallback" and "empty answer" in res.error  # never shows "(no answer)"


async def test_model_fault_respects_blast_radius(manager):
    llm = FakeLLM()
    arm(9003, "fault.model_error", scope=frozenset({2}))  # patient 2 only
    try:
        res = await AGENTS["monitoring"].run(AgentContext(manager, 1, "t-scope", llm), "How are things?")
    finally:
        INJECTOR.disarm(9003)
    assert res.mode == "groq" and llm.calls == 1
