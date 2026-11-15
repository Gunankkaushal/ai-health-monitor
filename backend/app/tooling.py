"""Patient-scoped, agent-restricted access to MCP tools.

This is the isolation boundary between agents/LLM and the MCP servers:

* Each agent gets a ``ScopedToolbox`` listing only the tools it is allowed to use.
* ``patient_id`` is removed from the schemas the model sees and is injected by the
  backend from the active request. If the model tries to supply ``patient_id`` (or any
  other undeclared argument) the call is rejected.
* Arguments are validated against the discovered MCP schema (with additionalProperties=false).
* Results that name a different patient are rejected (defence in depth).

It is also the "MCP proxy" interception point of the resilience lab: armed faults are applied here,
and so are the two resilience guardrails (bounded retries for idempotent tools, output contracts).
"""

from __future__ import annotations

import asyncio
import copy
import random
import time
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError

from . import activity
from .mcp_client import (
    MCPClientError,
    MCPManager,
    MCPTimeoutError,
    MCPToolError,
    MCPUnavailableError,
    ToolArgumentError,
    UnknownToolError,
)
from .resilience.injector import CONTROLS, INJECTOR, RateLimitedError

SCOPED_PARAM = "patient_id"


class ToolNotAllowedError(MCPClientError):
    pass


class PatientIsolationError(MCPClientError):
    pass


class MalformedToolOutputError(MCPToolError):
    """The tool answered, but not with its documented result shape."""


# Output contracts: keys every successful result must contain (list-typed where marked).
OUTPUT_CONTRACTS: dict[str, dict[str, type]] = {
    "get_patient_profile": {"id": int, "name": str},
    "get_recent_readings": {"readings": list},
    "get_reading_history": {"readings": list},
    "calculate_trends": {"trends": list},
    "get_monitoring_rules": {"rules": list, "trend_rules": list},
    "evaluate_readings": {"alerts": list, "readings_evaluated": int},
    "get_patient_alerts": {"alerts": list},
    "generate_health_summary": {"markdown": str, "alert_counts": dict},
    "export_health_report": {"content": str, "filename": str, "mime_type": str},
}

TRANSIENT_ERRORS = (MCPTimeoutError, MCPUnavailableError, RateLimitedError)


def check_output_contract(tool: str, data: Any) -> None:
    contract = OUTPUT_CONTRACTS.get(tool)
    if contract is None:
        return
    if not isinstance(data, dict):
        raise MalformedToolOutputError(f"{tool} returned {type(data).__name__}, expected a JSON object")
    for key, typ in contract.items():
        if not isinstance(data.get(key), typ):
            raise MalformedToolOutputError(f"{tool} result is missing '{key}' ({typ.__name__})")


def _mentions_other_patient(data: Any, patient_id: int) -> bool:
    if isinstance(data, dict):
        pid = data.get(SCOPED_PARAM)
        if pid is not None and pid != patient_id:
            return True
        return any(_mentions_other_patient(v, patient_id) for v in data.values())
    if isinstance(data, list):
        return any(_mentions_other_patient(v, patient_id) for v in data)
    return False


def model_facing_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
    """Copy of the MCP input schema with patient_id removed and extra properties forbidden."""
    schema = copy.deepcopy(input_schema) or {"type": "object", "properties": {}}
    props = schema.setdefault("properties", {})
    props.pop(SCOPED_PARAM, None)
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r != SCOPED_PARAM]
        if not schema["required"]:
            schema.pop("required")
    schema["additionalProperties"] = False
    schema.pop("title", None)
    return schema


@dataclass
class CallRecord:
    tool: str
    server: str | None
    status: str
    duration_ms: int | None
    detail: str


@dataclass
class ScopedToolbox:
    manager: MCPManager
    patient_id: int | None
    agent: str
    allowed: tuple[str, ...]
    run_id: str
    agent_key: str = ""
    calls: list[CallRecord] = field(default_factory=list)

    def available(self) -> list[str]:
        tools = self.manager.all_tools()
        return [t for t in self.allowed if t in tools]

    def groq_tool_specs(self) -> list[dict[str, Any]]:
        """OpenAI/Groq-style function specs for the tools this agent may call."""
        tools = self.manager.all_tools()
        specs = []
        for name in self.available():
            info = tools[name]
            specs.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": info.description[:1000],
                    "parameters": model_facing_schema(info.input_schema),
                },
            })
        return specs

    def _log(self, tool: str, server: str | None, status: str, duration: int | None, detail: str) -> None:
        self.calls.append(CallRecord(tool, server, status, duration, detail))
        activity.record(self.run_id, self.agent, status, patient_id=self.patient_id, tool=tool, server=server,
                        duration_ms=duration, detail=detail)

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        args = dict(arguments or {})
        if tool not in self.allowed:
            self._log(tool, None, "rejected", None, f"{self.agent} is not permitted to call {tool}")
            raise ToolNotAllowedError(f"{self.agent} is not permitted to call '{tool}'")
        try:
            info = self.manager.tool_info(tool)
        except UnknownToolError as exc:
            self._log(tool, None, "error", None, str(exc))
            raise
        if SCOPED_PARAM in args:
            self._log(tool, info.server, "rejected", None, "patient_id supplied by caller; it is set by the system")
            raise ToolArgumentError("patient_id is set by the system for the active patient and must not be supplied")

        try:
            Draft202012Validator(model_facing_schema(info.input_schema)).validate(args)
        except SchemaValidationError as exc:
            where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            msg = f"Invalid arguments for {tool} at {where}: {exc.message}"
            self._log(tool, info.server, "rejected", None, msg)
            raise ToolArgumentError(msg) from None

        if SCOPED_PARAM in (info.input_schema.get("properties") or {}):
            if self.patient_id is None:
                raise PatientIsolationError(f"{tool} requires an active patient")
            args[SCOPED_PARAM] = self.patient_id

        result = await self._call_with_guardrails(tool, args, info)
        data = result.data
        foreign = self.patient_id is not None and (
            _mentions_other_patient(data, self.patient_id)
            or (tool == "get_patient_profile" and isinstance(data, dict) and data.get("id") != self.patient_id)
        )
        if foreign:
            self._log(tool, result.server, "error", result.duration_ms, "result referenced another patient; discarded")
            raise PatientIsolationError("Tool result referenced a different patient and was discarded")

        shown = {k: v for k, v in args.items() if k != SCOPED_PARAM}
        self._log(tool, result.server, "ok", result.duration_ms, f"args={shown}" if shown else "")
        return data

    async def _call_with_guardrails(self, tool: str, args: dict[str, Any], info: Any) -> Any:
        """Forward to MCP through the fault proxy, retrying idempotent tools on transient errors."""
        guard = CONTROLS.guardrails
        retry_ok = bool(guard.get("retries")) and info.idempotent is True
        attempts = max(1, int(guard.get("max_attempts", 3))) if retry_ok else 1
        for attempt in range(1, attempts + 1):
            fault = INJECTOR.match(self.agent_key, tool, self.patient_id)
            fired = fault is not None and INJECTOR.should_fire(fault)
            started = time.perf_counter()
            try:
                if fired:
                    result = await INJECTOR.apply_tool_fault(fault, tool, lambda: self.manager.call_tool(tool, args))
                else:
                    result = await self.manager.call_tool(tool, args)
                if guard.get("output_validation"):
                    check_output_contract(tool, result.data)
            except MCPClientError as exc:
                ms = int((time.perf_counter() - started) * 1000)
                INJECTOR.record(self.agent_key, tool, self.patient_id, fired, "error", type(exc).__name__, ms, attempt)
                if attempt < attempts and isinstance(exc, TRANSIENT_ERRORS):
                    delay = getattr(exc, "retry_after_ms", None)
                    delay = min(delay, 2000) / 1000 if delay is not None else 0.1 * 2 ** (attempt - 1)
                    delay += random.uniform(0, 0.05)  # jitter so concurrent callers do not retry in lockstep
                    self._log(tool, info.server, "error", ms,
                              f"{type(exc).__name__}: {exc} — retry {attempt}/{attempts - 1} in {delay * 1000:.0f} ms")
                    await asyncio.sleep(delay)
                    continue
                self._log(tool, info.server, "error", ms, f"{type(exc).__name__}: {exc}")
                raise
            INJECTOR.record(self.agent_key, tool, self.patient_id, fired, "ok", None, result.duration_ms, attempt)
            return result
        raise AssertionError("unreachable")
