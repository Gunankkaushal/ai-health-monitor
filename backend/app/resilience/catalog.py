"""Discovery: how the lab sees the agent graph, classifies each edge, and decides which faults apply.

Discovery sources, ranked by trust:
  1. Agent allow-lists (code)            — which agent may call which tool        authoritative
  2. MCP tool manifest (tools/list)      — server, schema, read-only/idempotent   authoritative
  3. Response shape sampling             — collection vs object, timestamps       medium (read-only tools only)
  4. Declared journeys (journeys.py)     — stages, SLOs, critical path            as good as its author

Applicability is resolved in a fixed order. Steps 1–5 are deterministic and explainable; the only
heuristic (risk ranking) changes ORDER, never eligibility.
  1. fault predicates vs edge attributes  -> not_applicable
  2. interception point active?           -> unavailable
  3. fault enabled for the tenant?        -> disabled
  4. policy filter for this environment   -> blocked
  5. human overrides (exclude, w/ reason) -> excluded
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from health_core.db import session_scope
from health_core.models import AgentActivity, Patient, utcnow
from health_core.resilience_models import EdgeOverride

from ..agents import AGENTS
from ..llm import mode_info
from ..mcp_client import MCPClientError, MCPManager
from . import policy as policy_mod
from .injector import CONTROLS, FAULTS
from .journeys import JOURNEYS, journey_edges, journeys_for_edge

SERVER_OWNERS = {"patient_data": "Patient data team", "health_rules": "Clinical rules team",
                 "reports": "Reporting team", "groq": "AI platform team"}

# Response-shape facts learned by sampling read-only tools: tool -> {collection, timestamped, sampled_at}
_SHAPES: dict[str, dict[str, Any]] = {}
_SHAPES_AT: float = 0.0


async def sample_shapes(manager: MCPManager, force: bool = False) -> dict[str, dict[str, Any]]:
    """Call each READ-ONLY tool once for a sample patient and record its response shape.
    Write tools are never sampled — sampling must not change data."""
    global _SHAPES_AT
    if _SHAPES and not force and time.time() - _SHAPES_AT < 600:
        return _SHAPES
    with session_scope() as s:
        pid = s.scalar(select(Patient.id).where(Patient.is_synthetic.is_(True)).order_by(Patient.id).limit(1))
    tools = manager.all_tools()
    for name, info in tools.items():
        if info.read_only is not True:
            continue
        args: dict[str, Any] = {}
        if "patient_id" in (info.input_schema.get("properties") or {}):
            if pid is None:
                continue
            args["patient_id"] = pid
        try:
            data = (await manager.call_tool(name, args)).data
        except MCPClientError:
            continue
        lists = _lists(data)
        _SHAPES[name] = {
            "collection": bool(lists),
            "timestamped": any(isinstance(i, dict) and ("recorded_at" in i or "created_at" in i) for lst in lists for i in lst),
            "sampled_at": utcnow().isoformat(),
        }
    _SHAPES_AT = time.time()
    return _SHAPES


def _lists(data: Any) -> list[list]:
    out: list[list] = []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                out.append(v)
            elif isinstance(v, dict):
                out.extend(x for x in v.values() if isinstance(x, list))
    return out


def model_gateway_active() -> bool:
    return mode_info()["mode"] == "groq"


def edges(manager: MCPManager) -> list[dict[str, Any]]:
    tools = manager.all_tools()
    out: list[dict[str, Any]] = []
    for agent_key, agent in AGENTS.items():
        for tool in agent.allowed_tools:
            info = tools.get(tool)
            ro = info.read_only if info else None
            shape = _SHAPES.get(tool)
            out.append({
                "key": f"{agent_key}:{tool}", "agent_key": agent_key, "agent": agent.name, "tool": tool,
                "server": info.server if info else None, "kind": "mcp.tool",
                "direction": "read" if ro is True else "write",  # undeclared is treated as a write: safer
                "idempotent": bool(info and info.idempotent),
                "connected": info is not None,
                "interception": "mcp_proxy",
                "response": ("collection" if shape["collection"] else "object") if shape else "unknown",
                "timestamped": bool(shape and shape["timestamped"]),
                "provenance": {
                    "kind": "Agent allow-list (authoritative)",
                    "direction": "MCP manifest read_only_hint (authoritative)" if ro is not None
                    else "Not declared in manifest — treated as write",
                    "idempotent": "MCP manifest idempotent_hint (authoritative)" if info else "Tool not discovered",
                    "response": "Response shape sampling (medium)" if shape
                    else "Not sampled (write tools are never sampled)" if ro is False else "Not sampled yet",
                },
            })
        out.append({
            "key": f"{agent_key}:llm", "agent_key": agent_key, "agent": agent.name, "tool": "llm",
            "server": "groq", "kind": "model.inference", "direction": "read", "idempotent": False,
            "connected": model_gateway_active(), "interception": "model_gateway", "response": "completion",
            "timestamped": False,
            "provenance": {"kind": "Agent implementation (authoritative)", "direction": "Inferred from kind",
                           "idempotent": "Inferred from kind", "response": "Inferred from kind"},
        })
    for i, e in enumerate(out, start=1):
        e["id"] = f"E{i:02d}"
        e["journeys"] = journeys_for_edge(e["key"])
        e["critical"] = any(e["key"] in j["critical_edges"] for j in JOURNEYS)
        e["owner"] = SERVER_OWNERS.get(e["server"] or "", "Agent team")
    return out


def edge_by_key(manager: MCPManager, key: str) -> dict[str, Any] | None:
    return next((e for e in edges(manager) if e["key"] == key), None)


def overrides() -> list[dict[str, Any]]:
    with session_scope() as s:
        return [{"id": o.id, "edge_key": o.edge_key, "fault_id": o.fault_id, "reason": o.reason,
                 "review_by": o.review_by, "created_by": o.created_by, "created_at": o.created_at.isoformat()}
                for o in s.scalars(select(EdgeOverride).order_by(EdgeOverride.id))]


def applicability(edge: dict[str, Any], fault: dict[str, Any], policy_body: dict[str, Any], env: str,
                  ovr: list[dict[str, Any]] | None = None) -> dict[str, str]:
    # 1. predicates
    if edge["kind"] not in fault["can_target"]:
        return {"status": "not_applicable", "reason": f"targets {', '.join(fault['can_target'])}, edge is {edge['kind']}"}
    if edge["direction"] not in fault["paths"]:
        return {"status": "not_applicable", "reason": f"{edge['direction']} path; fault applies to {'/'.join(fault['paths'])} paths"}
    if "collection_response" in fault["requires"] and edge["response"] != "collection":
        return {"status": "not_applicable", "reason": f"response is {edge['response']}, not a collection"}
    if "timestamped_collection" in fault["requires"] and not edge["timestamped"]:
        return {"status": "not_applicable", "reason": "response records carry no timestamps"}
    # 2. interception point
    if fault["interception"] == "model_gateway" and not model_gateway_active():
        return {"status": "unavailable", "reason": "model gateway inactive: rule-based mode makes no model calls"}
    if not edge["connected"]:
        return {"status": "unavailable", "reason": "tool not currently discovered on any MCP server"}
    # 3. tenant enablement
    if fault["id"] in CONTROLS.disabled_faults:
        return {"status": "disabled", "reason": f"disabled for the tenant: {CONTROLS.disabled_faults[fault['id']]}"}
    # 4. policy
    c = policy_body["ceilings"]
    if edge["direction"] == "write" and c["write_path"][env] == "never":
        return {"status": "blocked", "reason": f"policy: write-path injection never allowed in {env}"}
    if fault["risk"] == "high" and c["high_risk_faults"][env] == "never":
        return {"status": "blocked", "reason": f"policy: high-risk faults never allowed in {env}"}
    # 5. overrides
    for o in ovr if ovr is not None else overrides():
        if o["edge_key"] == edge["key"] and o["fault_id"] in ("*", fault["id"]):
            return {"status": "excluded", "reason": f"excluded by {o['created_by']}: {o['reason']}"}
    return {"status": "eligible", "reason": "all predicates, interception, policy and override checks pass"}


def edge_faults(edge: dict[str, Any], policy_body: dict[str, Any], env: str) -> list[dict[str, Any]]:
    ovr = overrides()
    return [{"fault_id": f["id"], "name": f["name"], "risk": f["risk"], **applicability(edge, f, policy_body, env, ovr)}
            for f in FAULTS.values()]


def edge_stats(days: int = 7) -> dict[tuple[str, str], dict[str, Any]]:
    """Observed calls per (agent name, tool) from the activity log."""
    since = utcnow() - timedelta(days=days)
    acc: dict[tuple[str, str], list[AgentActivity]] = {}
    with session_scope() as s:
        for a in s.scalars(select(AgentActivity).where(AgentActivity.created_at >= since, AgentActivity.tool.is_not(None))):
            acc.setdefault((a.agent, a.tool), []).append(a)
    out = {}
    for k, rows in acc.items():
        durs = sorted(r.duration_ms for r in rows if r.duration_ms is not None)
        out[k] = {"calls": len(rows), "errors": sum(r.status != "ok" for r in rows),
                  "p95_ms": durs[min(len(durs) - 1, int(len(durs) * 0.95))] if durs else None}
    return out


def interception(manager: MCPManager) -> dict[str, Any]:
    es = edges(manager)
    tool_edges = [e for e in es if e["kind"] == "mcp.tool"]
    model_edges = [e for e in es if e["kind"] == "model.inference"]
    with session_scope() as s:
        recent = list(s.scalars(select(AgentActivity).where(AgentActivity.tool.is_not(None))
                                .order_by(AgentActivity.id.desc()).limit(500)))
    complete = sum(1 for a in recent if a.server and (a.status != "ok" or a.duration_ms is not None))
    return {
        "points": [
            {"key": "mcp_proxy", "mode": "MCP proxy", "deployed_as": "In-process, ScopedToolbox.call (every agent tool call)",
             "can_inject": "Latency, timeout, server error, rate limit, unavailable, malformed, empty, partial, stale, write timeout",
             "coverage": f"{len(tool_edges)} edges", "status": "connected"},
            {"key": "model_gateway", "mode": "Model gateway", "deployed_as": "In-process wrapper around the Groq client",
             "can_inject": "Model provider error, model latency, model degradation",
             "coverage": f"{len(model_edges)} edges",
             "status": "connected" if model_gateway_active() else "inactive (rule-based mode, no model calls)"},
            {"key": "server_process", "mode": "Server process", "deployed_as": "Not deployed",
             "can_inject": "Real process kill / restart (use Tool servers → Reconnect manually)",
             "coverage": "0 edges", "status": "not deployed"},
        ],
        "sources": [
            {"source": "Agent allow-lists", "establishes": "Which agent may call which tool", "trust": "Authoritative",
             "resolved": len(tool_edges)},
            {"source": "MCP tool manifest", "establishes": "Server, input schema, read-only and idempotency hints",
             "trust": "Authoritative", "resolved": sum(e["connected"] for e in tool_edges)},
            {"source": "Response shape sampling", "establishes": "Collection vs object, timestamped records",
             "trust": "Medium", "resolved": sum(e["response"] != "unknown" for e in tool_edges)},
            {"source": "Declared journeys", "establishes": "Stages, SLOs and critical path", "trust": "As good as its author",
             "resolved": len({k for j in JOURNEYS for k in journey_edges(j)})},
        ],
        "resolution_order": [
            "Fault predicates versus edge attributes — eligible set",
            "Interception point active? — otherwise unavailable",
            "Fault enabled for the tenant? — otherwise disabled",
            "Policy filter — write path, protected patients, risk class",
            "Human overrides — exclude, with a reason",
            "Risk ranking of what remains (heuristic) — changes order, never eligibility",
        ],
        "telemetry_completeness_pct": round(100 * complete / len(recent)) if recent else None,
        "telemetry_sample": len(recent),
        "shapes_sampled_at": max((v["sampled_at"] for v in _SHAPES.values()), default=None),
    }


def risk_rank(edge: dict[str, Any], tested: bool, stats: dict[str, Any] | None) -> float:
    """Heuristic ordering for suggestions: untested critical edges and write paths first."""
    score = 0.0
    score += 3 if edge["critical"] else 0
    score += 2 if not tested else 0
    score += 1 if edge["direction"] == "write" else 0
    if stats and stats["calls"]:
        score += min(2.0, 10 * stats["errors"] / stats["calls"])
    return score


def policy_env() -> tuple[dict[str, Any], str]:
    return policy_mod.current()["body"], policy_mod.environment()
