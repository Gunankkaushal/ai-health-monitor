"""The four specialised agents.

Each agent has:
  * its own instructions (system prompt),
  * a restricted allow-list of MCP tools (enforced by ScopedToolbox),
  * a deterministic "demo" implementation (no LLM) and a Groq tool-calling implementation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .llm import GroqLLM, run_tool_loop
from .mcp_client import MCPClientError, MCPManager
from .resilience.injector import CONTROLS, INJECTOR
from .tooling import ScopedToolbox

log = logging.getLogger("app.agents")

SAFETY_PREAMBLE = """You are one agent inside an EDUCATIONAL health-monitoring demo that uses synthetic data.
Hard rules:
- Never diagnose or name likely diseases/conditions. Never recommend, start, stop or dose medication.
- Never invent medical thresholds. Only describe a value as high/low by citing a rule or alert returned
  by the rules tools. If you have no rule to cite, just report the number.
- Only use facts returned by your tools. If data is missing, say so.
- You can only access the currently selected patient; the system fills in the patient automatically.
- Suggest talking to a qualified health professional for concerns, and emergency services for severe symptoms.
- Be concise (under 180 words), plain language, use units and dates."""


@dataclass
class AgentContext:
    manager: MCPManager
    patient_id: int | None
    run_id: str
    llm: GroqLLM | None
    conversation: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentResult:
    agent: str
    text: str
    mode: str  # "demo" | "groq" | "demo-fallback"
    data: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"agent": self.agent, "text": self.text, "mode": self.mode, "data": self.data,
                "tool_calls": self.tool_calls, "error": self.error}


class EmptyModelAnswer(RuntimeError):
    """The model returned no usable text; treated like a provider failure so the user gets the labelled fallback."""


class BaseAgent:
    key: ClassVar[str]
    name: ClassVar[str]
    responsibility: ClassVar[str]
    instructions: ClassVar[str]
    allowed_tools: ClassVar[tuple[str, ...]]

    def toolbox(self, ctx: AgentContext) -> ScopedToolbox:
        return ScopedToolbox(ctx.manager, ctx.patient_id, self.name, self.allowed_tools, ctx.run_id, self.key)

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {"name": cls.name, "responsibility": cls.responsibility, "allowed_tools": list(cls.allowed_tools),
                "instructions": cls.instructions}

    async def run(self, ctx: AgentContext, task: str) -> AgentResult:
        llm = INJECTOR.wrap_llm(ctx.llm, self.key, ctx.patient_id)  # model-gateway interception point
        if llm is not None:
            box = self.toolbox(ctx)
            try:
                loop = await run_tool_loop(
                    llm,
                    f"{SAFETY_PREAMBLE}\n\nYour role — {self.name}:\n{self.instructions}",
                    [*ctx.conversation, {"role": "user", "content": task}],
                    box,
                )
                if not loop.text.strip() and CONTROLS.guardrails.get("output_validation"):
                    raise EmptyModelAnswer("model returned an empty answer")
                calls = [{"tool": c.tool, "server": c.server, "status": c.status} for c in box.calls]
                return AgentResult(self.name, loop.text.strip() or "(no answer)", "groq",
                                   {"iterations": loop.iterations, "stopped_reason": loop.stopped_reason}, calls)
            except Exception as exc:  # Groq/network failure → labeled deterministic fallback
                log.warning("%s: Groq call failed (%s); using deterministic fallback", self.name, type(exc).__name__)
                result = await self._safe_demo(ctx, task)
                result.mode = "demo-fallback"
                result.error = f"Groq request failed: {type(exc).__name__}: {str(exc)[:200]}"
                return result
        return await self._safe_demo(ctx, task)

    async def _safe_demo(self, ctx: AgentContext, task: str) -> AgentResult:
        box = self.toolbox(ctx)
        try:
            text, data = await self.demo(box, task)
            err = None
        except MCPClientError as exc:
            text, data, err = f"{self.name} could not complete: {exc}", {}, str(exc)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            # A tool answered with an unexpected shape. Degrade this agent's section instead of failing the request.
            if not CONTROLS.guardrails.get("output_validation"):
                raise
            log.warning("%s: unexpected tool data (%s: %s)", self.name, type(exc).__name__, exc)
            text, data = f"{self.name} could not complete: a tool returned data in an unexpected format.", {}
            err = f"Unexpected tool data: {type(exc).__name__}: {exc}"[:300]
        calls = [{"tool": c.tool, "server": c.server, "status": c.status} for c in box.calls]
        return AgentResult(self.name, text, "demo", data, calls, err)

    async def demo(self, box: ScopedToolbox, task: str) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError


# ---------------------------------------------------------------------------- Monitoring
class MonitoringAgent(BaseAgent):
    key = "monitoring"
    name = "Monitoring Agent"
    responsibility = "Retrieves readings and identifies changes over time."
    allowed_tools = ("get_patient_profile", "get_recent_readings", "get_reading_history", "calculate_trends")
    instructions = (
        "Retrieve the patient's readings and describe what changed over time: latest values, averages, "
        "direction of change (rising/falling/stable) and notable jumps, with dates. Use calculate_trends for "
        "statistics and get_recent_readings / get_reading_history for specifics. You do NOT judge whether values "
        "are abnormal — leave that to the Alert Agent."
    )

    async def demo(self, box, task):
        profile = await box.call("get_patient_profile")
        trends = await box.call("calculate_trends", {"days": 30})
        recent = await box.call("get_recent_readings", {"limit": 6})
        lines = [f"Readings for {profile['name']} over the last 30 days:"]
        for t in trends["trends"]:
            if t["count"] == 0:
                lines.append(f"• {t['label']}: no readings.")
                continue
            latest = f"{t['latest']:g}"
            if t["metric"] == "blood_pressure" and "diastolic_latest" in t:
                latest += f"/{t['diastolic_latest']:g}"
            lines.append(
                f"• {t['label']}: latest {latest} {t['unit']} (avg {t['mean']:g}, range {t['min']:g}–{t['max']:g}); "
                f"{t['direction']}" + (f", {t['slope_per_day']:+g} {t['unit']}/day" if t["direction"] in ("rising", "falling") else "")
                + "."
            )
        if recent["readings"]:
            r = recent["readings"][0]
            lines.append(f"Most recent entry: {r['label']} {r['display']} at {r['recorded_at'][:16].replace('T', ' ')} UTC ({r['source']}).")
        return "\n".join(lines), {"trends": trends["trends"]}


# ---------------------------------------------------------------------------- Alert
class AlertAgent(BaseAgent):
    key = "alert"
    name = "Alert Agent"
    responsibility = "Applies deterministic, configurable rules to flag readings that need attention."
    allowed_tools = ("get_monitoring_rules", "evaluate_readings", "get_patient_alerts")
    instructions = (
        "Alerts are produced ONLY by the deterministic Health Rules server. Use get_patient_alerts (or "
        "evaluate_readings for fresh results) and get_monitoring_rules. Explain each relevant alert in plain "
        "language: what was observed, which rule (id) fired, its threshold and source, and its limitations. "
        "Never create, upgrade or dismiss alerts yourself and never state thresholds that are not in the rules."
    )

    async def evaluate(self, box: ScopedToolbox, days: int = 7) -> dict[str, Any]:
        """Deterministic rule evaluation — always used for new readings, regardless of LLM mode."""
        return await box.call("evaluate_readings", {"days": days})

    async def demo(self, box, task):
        alerts = (await box.call("get_patient_alerts", {"limit": 20, "include_acknowledged": False}))["alerts"]
        if not alerts:
            return "No open rule-generated alerts for this patient.", {"alerts": []}
        lines = [f"{len(alerts)} open rule-generated alert(s):"]
        for a in alerts[:6]:
            lines.append(f"• [{a['severity'].upper()}] {a['message']} — observed {a['observed']} on "
                         f"{a['recorded_at'][:10]} (rule {a['rule_id']}: {a['threshold']}). {a['explanation']}")
        if len(alerts) > 6:
            lines.append(f"…and {len(alerts) - 6} more in the Alerts panel.")
        return "\n".join(lines), {"alerts": alerts}

    @staticmethod
    def demo_explanation(alert: dict[str, Any], rule: dict[str, Any] | None) -> str:
        parts = [
            f"This alert was raised by the deterministic rule '{alert['rule_id']}' because the reading "
            f"({alert['observed']}) met the condition {alert['threshold']}.",
            alert["explanation"],
            f"Source: {alert['source']}",
        ]
        if rule:
            parts.append(f"Limitations: {rule['limitations']}")
        parts.append("If readings like this repeat or you feel unwell, consider talking to a health professional.")
        return " ".join(parts)


# ---------------------------------------------------------------------------- Wellness
class WellnessAgent(BaseAgent):
    key = "wellness"
    name = "Wellness Agent"
    responsibility = "Provides general wellness information based on available data."
    allowed_tools = ("get_patient_profile", "get_recent_readings", "calculate_trends")
    instructions = (
        "Offer GENERAL, widely accepted wellness information (sleep routine, regular activity, hydration, "
        "stress management, consistent measurement technique) that relates to what the data shows. "
        "Not personalised medical advice: no diets for conditions, no supplements, no medication. "
        "Encourage discussing persistent concerns with a health professional."
    )

    async def demo(self, box, task):
        trends = {t["metric"]: t for t in (await box.call("calculate_trends", {"days": 14}))["trends"]}
        tips: list[str] = []
        sleep = trends.get("sleep", {})
        if sleep.get("count") and sleep["mean"] < 7:
            tips.append(f"Sleep has averaged {sleep['mean']:g} h over 14 days. Public guidance suggests 7+ hours for adults; "
                        "a consistent bedtime, less screen time before bed and limiting late caffeine are common general tips.")
        bp = trends.get("blood_pressure", {})
        if bp.get("count") and bp["direction"] == "rising":
            tips.append("Blood pressure readings are trending upward. For reliable home readings: rest 5 minutes, sit with "
                        "back supported, and measure at similar times. Regular activity and limiting salt and alcohol are "
                        "commonly discussed heart-health habits — repeated high readings are worth raising with a professional.")
        hr = trends.get("heart_rate", {})
        if hr.get("count") and hr["direction"] == "rising":
            tips.append("Resting heart rate is trending upward. Stress, caffeine, poor sleep, dehydration and illness can all play a role.")
        weight = trends.get("weight", {})
        if weight.get("count") and abs(weight.get("change_between_halves", 0)) >= 1:
            tips.append("Weight has shifted by about "
                        f"{weight['change_between_halves']:+g} kg; weighing at the same time of day on the same scale makes trends more reliable.")
        if not tips:
            tips.append("Readings look steady. General habits that support wellbeing include regular physical activity, "
                        "7+ hours of sleep, staying hydrated, and taking measurements consistently.")
        text = "General wellness information (not medical advice):\n" + "\n".join(f"• {t}" for t in tips)
        return text, {"tips": tips}


# ---------------------------------------------------------------------------- Summary
class SummaryAgent(BaseAgent):
    key = "summary"
    name = "Summary Agent"
    responsibility = "Produces readable summaries of readings, trends and alerts."
    allowed_tools = ("generate_health_summary", "export_health_report", "get_patient_alerts")
    instructions = (
        "Produce a clear, well-structured summary of the patient's readings, trends and rule-generated alerts. "
        "Use generate_health_summary as your factual basis; keep its numbers exactly. Keep alerts attributed to "
        "the rules. End with a one-line reminder that this is an educational summary."
    )

    async def summary(self, box: ScopedToolbox, days: int) -> dict[str, Any]:
        return await box.call("generate_health_summary", {"days": days})

    async def export(self, box: ScopedToolbox, fmt: str, days: int) -> dict[str, Any]:
        return await box.call("export_health_report", {"format": fmt, "days": days})

    async def demo(self, box, task):
        s = await self.summary(box, 30)
        c = s["alert_counts"]
        text = (f"Summary for {s['patient_name']} (last {s['days']} days): {s['metrics_with_data']} metrics recorded; "
                f"alerts — critical {c['critical']}, warning {c['warning']}, info {c['info']} ({s['open_alerts']} open). "
                "Use 'Generate summary' or 'Download report' for the full document.")
        return text, {"summary_markdown": s["markdown"]}


AGENTS: dict[str, BaseAgent] = {
    "monitoring": MonitoringAgent(),
    "alert": AlertAgent(),
    "wellness": WellnessAgent(),
    "summary": SummaryAgent(),
}


def describe_agents() -> list[dict[str, Any]]:
    return [{"key": k, **a.describe()} for k, a in AGENTS.items()]


def to_json(data: Any) -> str:
    return json.dumps(data, default=str)
