"""Orchestrator: routes each request to the right agents and combines their results.

Routing is deterministic (keyword based) so it is transparent and testable; each selected
agent then runs with its own instructions and tool allow-list.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import activity
from .agents import AGENTS, AgentContext, AlertAgent, SummaryAgent
from .llm import get_llm, mode_info
from .mcp_client import MCPManager

ROUTES: list[tuple[str, tuple[str, ...]]] = [
    ("alert", ("alert", "warning", "flag", "danger", "concern", "worry", "rule", "threshold", "abnormal", "normal", "high", "low")),
    ("summary", ("summary", "summarize", "summarise", "report", "overview", "recap")),
    ("wellness", ("tip", "wellness", "improve", "advice", "lifestyle", "exercise", "habit", "better", "healthy", "stress")),
    ("monitoring", ("trend", "change", "history", "latest", "reading", "heart", "pressure", "temperature", "oxygen",
                    "spo2", "weight", "sleep", "pulse", "how is", "how are", "doing")),
]

MEDICAL_REQUEST = re.compile(
    r"\b(diagnos\w*|do i have|does (he|she|they) have|what disease|medication|medicine|dose|dosage|prescri\w*|"
    r"which (drug|pill)|should i take|treat(ment)?)\b",
    re.I,
)

SAFETY_NOTE = ("I can't diagnose conditions or advise on medication — this is an educational monitoring demo. "
               "Please ask a qualified health professional. Here is what the stored readings show:")


def route(message: str) -> list[str]:
    text = message.lower()
    selected = [key for key, words in ROUTES if any(re.search(rf"\b{re.escape(w)}", text) for w in words)]
    if not selected:
        selected = ["monitoring", "alert"]
    order = ["monitoring", "alert", "wellness", "summary"]
    return sorted(set(selected), key=order.index)[:3]


class Orchestrator:
    def __init__(self, manager: MCPManager) -> None:
        self.manager = manager

    def _ctx(self, patient_id: int | None, run_id: str, conversation: list[dict[str, Any]] | None = None,
             use_llm: bool = True) -> AgentContext:
        return AgentContext(self.manager, patient_id, run_id, get_llm() if use_llm else None, conversation or [])

    async def chat(self, patient_id: int, message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        run_id = activity.new_run_id()
        agents = route(message)
        medical = bool(MEDICAL_REQUEST.search(message))
        if medical and "monitoring" not in agents:
            agents = ["monitoring", *agents][:3]
        activity.record(run_id, "Orchestrator", "info", patient_id=patient_id,
                        detail=f"routed to: {', '.join(agents)}" + (" (medical-advice request: safety note added)" if medical else ""))

        convo = [{"role": m["role"], "content": m["content"][:2000]} for m in (history or [])[-6:]
                 if m.get("role") in ("user", "assistant")]
        ctx = self._ctx(patient_id, run_id, convo)
        results = []
        for key in agents:
            results.append(await AGENTS[key].run(ctx, message))

        modes = {r.mode for r in results}
        mode = "groq" if modes == {"groq"} else ("demo-fallback" if "demo-fallback" in modes else ("demo" if modes == {"demo"} else "mixed"))
        sections = [f"**{r.agent}**\n{r.text}" for r in results]
        answer = "\n\n".join(([SAFETY_NOTE] if medical else []) + sections)
        return {
            "run_id": run_id,
            "mode": mode,
            "mode_label": _mode_label(mode),
            "routed_to": [AGENTS[k].name for k in agents],
            "answer": answer,
            "agents": [r.as_dict() for r in results],
            "safety_note": medical,
        }

    async def process_new_readings(self, patient_id: int, reading_ids: list[int]) -> dict[str, Any]:
        """Deterministic pipeline after readings are added:
        Monitoring Agent retrieves them via MCP → Alert Agent evaluates rules via MCP."""
        run_id = activity.new_run_id()
        activity.record(run_id, "Orchestrator", "info", patient_id=patient_id,
                        detail=f"new readings {reading_ids}: monitoring → alert pipeline")
        ctx = self._ctx(patient_id, run_id, use_llm=False)
        mon_box = AGENTS["monitoring"].toolbox(ctx)
        recent = await mon_box.call("get_recent_readings", {"limit": max(len(reading_ids), 1) + 5})
        wanted = set(reading_ids)
        retrieved = [r for r in recent["readings"] if r["id"] in wanted]

        alert_agent: AlertAgent = AGENTS["alert"]  # type: ignore[assignment]
        evaluation = await alert_agent.evaluate(alert_agent.toolbox(ctx), days=7)
        # Report every alert raised for the readings just processed, not only those inserted by this exact call:
        # after a lost acknowledgement the retried (idempotent) evaluation finds the alert already stored.
        new_alerts = [a for a in evaluation["alerts"] if a.get("is_new") or a.get("reading_id") in wanted]
        warnings: list[str] = []
        if len(retrieved) < len(reading_ids):
            # Saved but not visible through MCP (stale replica, truncated page…). Say so rather than
            # reporting success on data the agents never saw.
            warnings.append(f"Monitoring Agent retrieved only {len(retrieved)} of {len(reading_ids)} new reading(s); "
                            "the data it saw may be stale or incomplete.")
            activity.record(run_id, "Orchestrator", "error", patient_id=patient_id, detail=warnings[-1])
        return {
            "run_id": run_id,
            "retrieved_via_mcp": retrieved,
            "readings_evaluated": evaluation["readings_evaluated"],
            "new_alerts": new_alerts,
            "rules_version": evaluation.get("rules_version", "unknown"),
            "warnings": warnings,
        }

    async def summary(self, patient_id: int, days: int) -> dict[str, Any]:
        run_id = activity.new_run_id()
        agent: SummaryAgent = AGENTS["summary"]  # type: ignore[assignment]
        ctx = self._ctx(patient_id, run_id)
        data = await agent.summary(agent.toolbox(ctx), days)
        result: dict[str, Any] = {"run_id": run_id, "summary": data, "ai_narrative": None, "mode": "demo"}
        if ctx.llm is not None:
            res = await agent.run(ctx, f"Write a short narrative overview of the last {days} days for this patient.")
            result["ai_narrative"] = res.text if res.mode == "groq" else None
            result["mode"] = res.mode
            result["error"] = res.error
        result["mode_label"] = _mode_label(result["mode"])
        return result

    async def export(self, patient_id: int, fmt: str, days: int) -> dict[str, Any]:
        run_id = activity.new_run_id()
        agent: SummaryAgent = AGENTS["summary"]  # type: ignore[assignment]
        ctx = self._ctx(patient_id, run_id, use_llm=False)
        return await agent.export(agent.toolbox(ctx), fmt, days)

    async def explain_alert(self, patient_id: int, alert: dict[str, Any]) -> dict[str, Any]:
        run_id = activity.new_run_id()
        agent: AlertAgent = AGENTS["alert"]  # type: ignore[assignment]
        ctx = self._ctx(patient_id, run_id)
        rules_cfg = await agent.toolbox(ctx).call("get_monitoring_rules")
        rule = next((r for r in rules_cfg["rules"] + rules_cfg["trend_rules"] if r["id"] == alert["rule_id"]), None)
        if ctx.llm is not None:
            task = ("Explain this rule-generated alert in plain language for a non-expert. Do not change its "
                    "severity. Alert: " + json.dumps({k: alert[k] for k in ("rule_id", "severity", "message", "observed",
                                                                             "threshold", "recorded_at", "source")}))
            res = await agent.run(ctx, task)
            if res.mode == "groq":
                return {"run_id": run_id, "mode": "groq", "mode_label": _mode_label("groq"), "text": res.text, "store": True}
            return {"run_id": run_id, "mode": res.mode, "mode_label": _mode_label(res.mode),
                    "text": AlertAgent.demo_explanation(alert, rule), "error": res.error, "store": False}
        return {"run_id": run_id, "mode": "demo", "mode_label": _mode_label("demo"),
                "text": AlertAgent.demo_explanation(alert, rule), "store": False}

    async def rules(self) -> dict[str, Any]:
        run_id = activity.new_run_id()
        agent = AGENTS["alert"]
        return await agent.toolbox(self._ctx(None, run_id, use_llm=False)).call("get_monitoring_rules")


def _mode_label(mode: str) -> str:
    return {
        "groq": f"AI-generated (Groq · {mode_info()['model']})",
        "demo": "Rule-based response (deterministic, no AI model)",
        "demo-fallback": "Rule-based fallback (AI unavailable, deterministic)",
        "mixed": "Partly AI-generated, partly deterministic",
    }.get(mode, mode)
