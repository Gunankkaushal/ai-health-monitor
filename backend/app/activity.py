"""Agent activity log (shown in the dashboard's Agent Activity panel)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from health_core.db import session_scope
from health_core.models import AgentActivity

log = logging.getLogger("app.activity")


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def record(
    run_id: str,
    agent: str,
    status: str,
    *,
    patient_id: int | None = None,
    tool: str | None = None,
    server: str | None = None,
    duration_ms: int | None = None,
    detail: str = "",
) -> None:
    try:
        with session_scope() as s:
            s.add(AgentActivity(run_id=run_id, patient_id=patient_id, agent=agent, tool=tool, server=server,
                                status=status, duration_ms=duration_ms, detail=detail[:1000]))
    except Exception:  # activity logging must never break a request
        log.exception("failed to record activity")


def recent(patient_id: int | None = None, limit: int = 50) -> list[dict]:
    with session_scope() as s:
        q = select(AgentActivity)
        if patient_id is not None:
            q = q.where(AgentActivity.patient_id == patient_id)
        q = q.order_by(AgentActivity.id.desc()).limit(limit)
        return [
            {
                "id": a.id,
                "run_id": a.run_id,
                "patient_id": a.patient_id,
                "agent": a.agent,
                "tool": a.tool,
                "server": a.server,
                "status": a.status,
                "duration_ms": a.duration_ms,
                "detail": a.detail,
                "created_at": a.created_at.isoformat(),
            }
            for a in s.scalars(q)
        ]
