"""Append-only, hash-chained audit log of every action that changed what the resilience lab did
or was allowed to do. There is deliberately no update or delete function in this module or the API.

Each record's hash covers its own fields plus the previous record's hash, so editing or deleting
any record breaks the chain from that point on, and ``verify()`` reports where.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from health_core.db import session_scope
from health_core.models import utcnow
from health_core.resilience_models import AuditEvent

GENESIS = "0" * 64

# Closed vocabulary: an unknown event name is a programming error, not a new event type.
EVENTS = frozenset({
    "experiment.created", "experiment.updated", "experiment.archived",
    "run.requested", "run.approved", "run.refused", "run.started", "run.break_glass",
    "fault.armed", "fault.disarmed", "run.rolled_back", "run.aborted", "run.completed", "run.failed",
    "killswitch.engaged", "killswitch.released",
    "policy.proposed", "policy.published", "policy.rejected",
    "fault.enabled", "fault.disabled", "guardrails.changed",
    "edge.override", "edge.override_removed",
    "finding.filed", "finding.resolved",
    "suite.created", "suite.run", "gate.evaluated",
})

_lock = threading.Lock()


def _digest(fields: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _fields(e: AuditEvent) -> dict[str, Any]:
    return {
        "seq": e.seq, "occurred_at": e.occurred_at.isoformat(), "actor": e.actor, "on_behalf_of": e.on_behalf_of,
        "event": e.event, "subject": e.subject, "detail": e.detail, "before": e.before, "after": e.after,
        "reason": e.reason, "change_ref": e.change_ref, "prev_hash": e.prev_hash,
    }


def append(actor: str, event: str, subject: str, detail: str = "", *, before: Any = None, after: Any = None,
           reason: str = "", change_ref: str = "", on_behalf_of: str = "") -> int:
    if event not in EVENTS:
        raise ValueError(f"Unknown audit event '{event}'")
    with _lock, session_scope() as s:
        last = s.scalars(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)).first()
        e = AuditEvent(
            seq=(last.seq + 1) if last else 1,  # monotonic and gapless
            occurred_at=utcnow(),  # server UTC, never client time
            actor=actor, on_behalf_of=on_behalf_of, event=event, subject=subject, detail=detail[:2000],
            before=before, after=after, reason=reason[:2000], change_ref=change_ref,
            prev_hash=last.hash if last else GENESIS, hash="",
        )
        e.hash = _digest(_fields(e))
        s.add(e)
        return e.seq


def as_dict(e: AuditEvent) -> dict[str, Any]:
    return _fields(e) | {"hash": e.hash}


def query(event: str | None = None, actor: str | None = None, days: int | None = None, limit: int = 500) -> list[dict]:
    with session_scope() as s:
        q = select(AuditEvent)
        if event:
            q = q.where(AuditEvent.event.like(f"{event}%"))
        if actor:
            q = q.where(AuditEvent.actor.like(f"%{actor}%"))
        if days:
            q = q.where(AuditEvent.occurred_at >= utcnow() - timedelta(days=days))
        return [as_dict(e) for e in s.scalars(q.order_by(AuditEvent.seq.desc()).limit(limit))]


def verify() -> dict[str, Any]:
    """Recompute the whole chain. Detects edits, deletions and re-ordering."""
    with session_scope() as s:
        prev, expected_seq, checked = GENESIS, 1, 0
        for e in s.scalars(select(AuditEvent).order_by(AuditEvent.seq)):
            problem = None
            if e.seq != expected_seq:
                problem = f"sequence gap: expected {expected_seq}, found {e.seq}"
            elif e.prev_hash != prev:
                problem = "previous-hash link broken"
            elif _digest(_fields(e)) != e.hash:
                problem = "record contents do not match its hash"
            if problem:
                return {"intact": False, "checked": checked, "first_bad_seq": e.seq, "problem": problem,
                        "verified_at": utcnow().isoformat()}
            prev, expected_seq, checked = e.hash, e.seq + 1, checked + 1
        total = s.scalar(select(func.count(AuditEvent.seq))) or 0
    return {"intact": True, "checked": checked, "total": total, "first_bad_seq": None, "problem": None,
            "verified_at": utcnow().isoformat()}


def export(fmt: str, **filters: Any) -> tuple[str, str, str]:
    rows = list(reversed(query(limit=100000, **filters)))
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "json":
        return json.dumps({"exported_at": utcnow().isoformat(), "chain": verify(), "events": rows}, indent=2,
                          default=str), "application/json", f"audit-log-{stamp}.json"
    buf = io.StringIO()
    cols = ["seq", "occurred_at", "actor", "on_behalf_of", "event", "subject", "detail", "reason", "change_ref",
            "before", "after", "prev_hash", "hash"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({c: json.dumps(r[c], default=str) if c in ("before", "after") and r[c] is not None else r[c]
                    for c in cols})
    return buf.getvalue(), "text/csv", f"audit-log-{stamp}.csv"
