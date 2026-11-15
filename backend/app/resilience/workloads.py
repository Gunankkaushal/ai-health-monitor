"""Journey workloads: one iteration drives the real product path once and reports per-iteration metrics.

Workloads call the same orchestrator methods the REST API uses, so a fault injected on an edge
reaches the product exactly as it would for a user. Ground truth (did the alert really get stored?)
is read straight from the database, outside the system under test.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from sqlalchemy import select

from health_core.db import session_scope
from health_core.models import Alert, Reading, utcnow
from health_core.repository import alert_dict

from ..mcp_client import MCPClientError
from ..orchestrator import Orchestrator

QUESTIONS = [
    ("How are the readings trending?", False),
    ("Are there any alerts I should know about?", False),
    ("Any general wellness tips based on the data?", False),
    ("Should I take medication for this?", True),
]


def _rate(ok: bool | None) -> float | None:
    return None if ok is None else (100.0 if ok else 0.0)


async def ingest(orch: Orchestrator, i: int, pid: int, created: list[int]) -> dict[str, Any]:
    abnormal = i % 2 == 0
    metric, value, unit = ("spo2", 88.0, "%") if abnormal else ("heart_rate", 72.0, "bpm")
    with session_scope() as s:
        r = Reading(patient_id=pid, metric=metric, value=value, value2=None, unit=unit, recorded_at=utcnow(),
                    source="experiment")
        s.add(r)
        s.flush()
        rid = r.id
    created.append(rid)
    t0 = time.perf_counter()
    error, res = None, None
    try:
        res = await orch.process_new_readings(pid, [rid])
    except MCPClientError as exc:  # the API saves the reading and reports the pipeline error
        error = f"{type(exc).__name__}: {exc}"
    latency = int((time.perf_counter() - t0) * 1000)
    ok = res is not None
    # Unknown (not 0%) when the pipeline never completed: success_rate already counts that failure, and
    # scoring it twice would raise a misleading "readings not visible" finding against the wrong edge.
    retrieved: bool | None = any(x["id"] == rid for x in res["retrieved_via_mcp"]) if ok else None
    detected: bool | None = None
    if abnormal:
        with session_scope() as s:
            stored = s.scalar(select(Alert.id).where(Alert.reading_id == rid, Alert.severity == "critical")) is not None
        reported = ok and any(a.get("reading_id") == rid for a in res["new_alerts"])
        detected = stored and reported
    return {
        "ok": ok, "error": error, "latency_ms": latency,
        "note": f"{'abnormal SpO₂ 88 %' if abnormal else 'normal HR 72'} → "
                + ("alert raised" if detected else "NO alert" if abnormal else "no alert expected")
                + (f"; warnings: {res['warnings']}" if ok and res.get("warnings") else ""),
        "metrics": {"success_rate": _rate(ok), "detection_rate": _rate(detected), "retrieval_rate": _rate(retrieved),
                    "latency_p95_ms": latency, "unhandled_errors": 0},
    }


async def assistant(orch: Orchestrator, i: int, pid: int, created: list[int]) -> dict[str, Any]:
    question, medical = QUESTIONS[i % len(QUESTIONS)]
    t0 = time.perf_counter()
    res = await orch.chat(pid, question)
    latency = int((time.perf_counter() - t0) * 1000)
    broken = [a for a in res["agents"]
              if "could not complete" in a["text"] or "(no answer)" in a["text"] or (a["error"] and a["mode"] == "demo")]
    degraded = [a for a in res["agents"] if a["mode"] == "demo-fallback"]
    complete = bool(res["answer"].strip()) and not broken
    return {
        "ok": complete, "latency_ms": latency,
        "error": "; ".join(f"{a['agent']}: {a['error'] or 'empty answer'}" for a in broken) or None,
        "note": f"“{question}” → {'complete' if complete else 'incomplete'}"
                + (f", {len(degraded)} agent(s) on labelled fallback" if degraded else ""),
        "metrics": {"complete_answer_rate": _rate(complete), "safety_rate": _rate(res["safety_note"]) if medical else None,
                    "latency_p95_ms": latency, "unhandled_errors": 0},
    }


async def summary(orch: Orchestrator, i: int, pid: int, created: list[int]) -> dict[str, Any]:
    t0 = time.perf_counter()
    error = None
    try:
        s = await orch.summary(pid, 30)
        e = await orch.export(pid, "markdown", 30)
        ok = bool(s["summary"].get("markdown")) and bool(e.get("content"))
    except MCPClientError as exc:
        ok, error = False, f"{type(exc).__name__}: {exc}"
    latency = int((time.perf_counter() - t0) * 1000)
    return {"ok": ok, "error": error, "latency_ms": latency, "note": "summary + report " + ("produced" if ok else "failed"),
            "metrics": {"success_rate": _rate(ok), "latency_p95_ms": latency, "unhandled_errors": 0}}


async def explain(orch: Orchestrator, i: int, pid: int, created: list[int]) -> dict[str, Any]:
    with session_scope() as s:
        a = s.scalars(select(Alert).where(Alert.patient_id == pid).order_by(Alert.recorded_at.desc()).limit(1)).first()
        alert = alert_dict(a) if a else None
    if alert is None:
        return {"ok": None, "error": None, "latency_ms": None, "note": "patient has no alerts to explain — skipped",
                "metrics": {"success_rate": None, "latency_p95_ms": None, "unhandled_errors": 0}}
    t0 = time.perf_counter()
    error = None
    try:
        res = await orch.explain_alert(pid, alert)
        ok = bool((res.get("text") or "").strip())
    except MCPClientError as exc:
        ok, error = False, f"{type(exc).__name__}: {exc}"
    latency = int((time.perf_counter() - t0) * 1000)
    return {"ok": ok, "error": error, "latency_ms": latency,
            "note": f"explain {alert['rule_id']} → " + ("explained" if ok else "failed"),
            "metrics": {"success_rate": _rate(ok), "latency_p95_ms": latency, "unhandled_errors": 0}}


WORKLOADS: dict[str, Callable[..., Any]] = {"ingest": ingest, "assistant": assistant, "summary": summary,
                                            "explain": explain}


def unhandled(metric_names: list[str], exc: BaseException) -> dict[str, Any]:
    """An exception escaped the product code path — exactly what a user would see as a 500."""
    metrics: dict[str, Any] = {m: 0.0 for m in metric_names if m.endswith("_rate")}
    metrics.update({"unhandled_errors": 1, "latency_p95_ms": None})
    return {"ok": False, "error": f"UNHANDLED {type(exc).__name__}: {exc}"[:300], "latency_ms": None,
            "note": "request would have failed with HTTP 500", "metrics": metrics}
