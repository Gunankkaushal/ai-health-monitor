"""Fault injection at two interception points:

* MCP proxy      — every agent tool call passes through ``ScopedToolbox.call``, which asks
                   ``INJECTOR.match`` whether an armed fault targets this (agent, tool, patient).
* Model gateway  — ``BaseAgent.run`` wraps the LLM client with ``INJECTOR.wrap_llm``.

A fault only fires for calls on the targeted edge AND for a patient inside the run's blast radius,
on the share of calls the experiment asked for. The kill switch makes every injector fail open
(traffic passes through untouched) and disarms all faults.

This module also holds the in-memory mirror of the tenant-wide switches (kill switch, guardrails,
fault enablement) because they are consulted on every call; the database copy is the source of truth.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from health_core.db import session_scope
from health_core.models import utcnow
from health_core.resilience_models import ControlState

from ..mcp_client import MCPTimeoutError, MCPToolError, MCPUnavailableError, ToolCallResult

log = logging.getLogger("app.resilience")

LIBRARY_PATH = Path(__file__).with_name("fault_library.json")


def load_library() -> dict[str, Any]:
    return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))


LIBRARY = load_library()
FAULTS: dict[str, dict[str, Any]] = {f["id"]: f for f in LIBRARY["faults"]}


class RateLimitedError(MCPToolError):
    """Injected throttling refusal. Carries the retry-after hint the guardrail honours."""

    def __init__(self, message: str, retry_after_ms: int) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class InjectedModelError(RuntimeError):
    pass


# ---------------------------------------------------------------------------- tenant switches
DEFAULT_GUARDRAILS = {
    "retries": True,  # bounded retry with backoff + jitter for idempotent tools on transient errors
    "max_attempts": 3,
    "output_validation": True,  # tool results are checked against their output contract
}


@dataclass
class Controls:
    kill_switch: dict[str, Any] = field(default_factory=lambda: {"engaged": False})
    guardrails: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_GUARDRAILS))
    disabled_faults: dict[str, str] = field(default_factory=dict)  # fault_id -> reason

    def load(self) -> None:
        with session_scope() as s:
            rows = {r.key: r.value for r in s.scalars(select(ControlState))}
        self.kill_switch = rows.get("kill_switch", {"engaged": False})
        self.guardrails = {**DEFAULT_GUARDRAILS, **rows.get("guardrails", {})}
        self.disabled_faults = rows.get("disabled_faults", {})

    def save(self, key: str, value: dict[str, Any], actor: str) -> None:
        with session_scope() as s:
            row = s.get(ControlState, key)
            if row is None:
                s.add(ControlState(key=key, value=value, updated_by=actor, updated_at=utcnow()))
            else:
                row.value, row.updated_by, row.updated_at = value, actor, utcnow()
        setattr(self, key, value)


CONTROLS = Controls()


# ---------------------------------------------------------------------------- armed faults
@dataclass
class Observation:
    t: float  # monotonic seconds
    agent_key: str
    tool: str
    patient_id: int | None
    injected: bool
    outcome: str  # ok | error
    error: str | None
    duration_ms: int
    attempt: int


@dataclass
class ArmedFault:
    run_id: int
    agent_key: str
    tool: str  # MCP tool name, or "llm"
    fault: dict[str, Any]
    params: dict[str, Any]
    share_pct: int
    selection: str
    patient_scope: frozenset[int]
    rng: random.Random
    matching_calls: int = 0
    injected: int = 0


class Injector:
    def __init__(self) -> None:
        self.armed: dict[int, ArmedFault] = {}
        # run_id -> (patient scope, observations). Observations cover every call made for an in-scope
        # patient while the run is being observed, so the cascade beyond the target edge is visible.
        self.observing: dict[int, tuple[frozenset[int], list[Observation]]] = {}

    # ---- lifecycle
    def arm(self, af: ArmedFault) -> None:
        if CONTROLS.kill_switch.get("engaged"):
            raise RuntimeError("Kill switch is engaged; faults cannot be armed")
        self.armed[af.run_id] = af

    def disarm(self, run_id: int) -> ArmedFault | None:
        return self.armed.pop(run_id, None)

    def disarm_all(self) -> list[int]:
        ids = list(self.armed)
        self.armed.clear()
        return ids

    def observe(self, run_id: int, scope: frozenset[int]) -> list[Observation]:
        obs: list[Observation] = []
        self.observing[run_id] = (scope, obs)
        return obs

    def stop_observing(self, run_id: int) -> None:
        self.observing.pop(run_id, None)

    # ---- matching
    def match(self, agent_key: str, tool: str, patient_id: int | None) -> ArmedFault | None:
        if CONTROLS.kill_switch.get("engaged") or not self.armed:
            return None  # fail open
        for af in self.armed.values():
            if af.agent_key == agent_key and af.tool == tool and patient_id in af.patient_scope:
                return af
        return None

    def should_fire(self, af: ArmedFault) -> bool:
        af.matching_calls += 1
        n, share = af.matching_calls, max(0, min(100, af.share_pct))
        if af.selection == "every_nth":
            every = max(1, round(100 / share)) if share else 0
            fire = bool(every) and n % every == 0
        elif af.selection == "burst":
            fire = ((n - 1) % 10) < round(share / 10)
        else:
            fire = af.rng.random() * 100 < share
        if fire:
            af.injected += 1
        return fire

    def record(self, agent_key: str, tool: str, patient_id: int | None, injected: bool, outcome: str,
               error: str | None, duration_ms: int, attempt: int) -> None:
        if not self.observing:
            return
        o = Observation(time.monotonic(), agent_key, tool, patient_id, injected, outcome, error, duration_ms, attempt)
        for scope, obs in self.observing.values():
            if patient_id in scope:
                obs.append(o)

    # ---- MCP proxy
    async def apply_tool_fault(self, af: ArmedFault, tool: str,
                               forward: Callable[[], Awaitable[ToolCallResult]]) -> ToolCallResult:
        fid, p = af.fault["id"], af.params
        tag = f"[injected {fid} · run {af.run_id}]"
        if fid == "fault.latency":
            delay = int(p.get("delay_ms", 800)) + af.rng.randint(0, int(p.get("jitter_ms", 0)))
            await asyncio.sleep(min(delay, 5000) / 1000)
            return await forward()
        if fid == "fault.timeout":
            await asyncio.sleep(min(int(p.get("after_ms", 400)), 5000) / 1000)
            raise MCPTimeoutError(f"{tool} timed out {tag}")
        if fid == "fault.server_error":
            raise MCPToolError(f"{tool} failed with an internal server error {tag}")
        if fid == "fault.rate_limit":
            ra = min(int(p.get("retry_after_ms", 300)), 10000)
            raise RateLimitedError(f"{tool} rate limited, retry after {ra} ms {tag}", ra)
        if fid == "fault.unavailable":
            raise MCPUnavailableError(f"MCP server for {tool} is not connected {tag}")
        if fid == "fault.write_timeout":
            await forward()  # the write really commits…
            raise MCPTimeoutError(f"{tool} acknowledgement lost after commit {tag}")  # …but the caller can't tell

        result = await forward()
        data = copy.deepcopy(result.data)
        if fid == "fault.malformed":
            data = ("<html><body>502 Bad Gateway</body></html>" if p.get("mode") == "garbage_text"
                    else {"result": "ok", "items": None, "note": "unexpected payload shape"})
        elif fid == "fault.empty":
            data = _map_lists(data, lambda items: [])
        elif fid == "fault.partial":
            keep = int(p.get("keep_pct", 50))
            data = _map_lists(data, lambda items: items[: (len(items) * keep) // 100])
        elif fid == "fault.stale":
            cutoff = utcnow().timestamp() - int(p.get("lag_minutes", 60)) * 60
            data = _map_lists(data, lambda items: [i for i in items if not _newer_than(i, cutoff)])
        return ToolCallResult(server=result.server, tool=result.tool, data=data, duration_ms=result.duration_ms)

    # ---- model gateway
    def wrap_llm(self, llm: Any, agent_key: str, patient_id: int | None) -> Any:
        if llm is None:
            return None
        af = self.match(agent_key, "llm", patient_id)
        return _FaultyLLM(llm, af, self, agent_key, patient_id) if af else llm


def _map_lists(data: Any, fn: Callable[[list], list]) -> Any:
    """Apply ``fn`` to every list in the payload (top level and one level down)."""
    if isinstance(data, list):
        return fn(data)
    if isinstance(data, dict):
        return {k: (fn(v) if isinstance(v, list) else _map_lists(v, fn) if isinstance(v, dict) else v)
                for k, v in data.items()}
    return data


def _newer_than(item: Any, cutoff_ts: float) -> bool:
    if not isinstance(item, dict):
        return False
    raw = item.get("recorded_at") or item.get("created_at")
    if not isinstance(raw, str):
        return False
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() > cutoff_ts
    except ValueError:
        return False


class _EmptyCompletion:
    content = ""
    tool_calls = None


class _FaultyLLM:
    def __init__(self, inner: Any, af: ArmedFault, injector: Injector, agent_key: str, patient_id: int | None) -> None:
        self.inner, self.af, self.injector = inner, af, injector
        self.agent_key, self.patient_id = agent_key, patient_id
        self.model = getattr(inner, "model", "model")

    async def complete(self, messages, tools, tool_choice):
        if CONTROLS.kill_switch.get("engaged") or self.af.run_id not in self.injector.armed \
                or not self.injector.should_fire(self.af):
            return await self.inner.complete(messages, tools, tool_choice)
        fid, started = self.af.fault["id"], time.perf_counter()
        try:
            if fid == "fault.model_latency":
                await asyncio.sleep(min(int(self.af.params.get("delay_ms", 1500)), 5000) / 1000)
                return await self.inner.complete(messages, tools, tool_choice)
            if fid == "fault.model_degradation":
                return _EmptyCompletion()
            raise InjectedModelError(f"model provider error 503 [injected {fid} · run {self.af.run_id}]")
        finally:
            self.injector.record(self.agent_key, "llm", self.patient_id, True,
                                 "error" if fid == "fault.model_error" else "ok",
                                 fid if fid == "fault.model_error" else None,
                                 int((time.perf_counter() - started) * 1000), 1)


INJECTOR = Injector()
