"""Resilience-lab tables (chaos experiments, runs, findings, suites, policy, audit).

Everything here describes what the fault-injection layer did or was allowed to do; none of it is
patient data. Tables are created by ``init_db`` alongside the core tables.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import utcnow


class Experiment(Base):
    __tablename__ = "rl_experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    journey_id: Mapped[str] = mapped_column(String(40))
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    edge_key: Mapped[str] = mapped_column(String(80))  # "<agent_key>:<tool>" or "<agent_key>:llm"
    fault_id: Mapped[str] = mapped_column(String(60))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    share_pct: Mapped[int] = mapped_column(Integer, default=30)
    selection: Mapped[str] = mapped_column(String(16), default="random")  # random | every_nth | burst
    duration_s: Mapped[int] = mapped_column(Integer, default=30)
    patient_scope: Mapped[list] = mapped_column(JSON, default=list)
    environment: Mapped[str] = mapped_column(String(16), default="dev")
    steady_state: Mapped[list] = mapped_column(JSON, default=list)
    baseline_iterations: Mapped[int] = mapped_column(Integer, default=3)
    fault_iterations: Mapped[int] = mapped_column(Integer, default=8)
    recovery_iterations: Mapped[int] = mapped_column(Integer, default=3)
    change_ref: Mapped[str] = mapped_column(String(40), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class Approval(Base):
    __tablename__ = "rl_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("rl_experiments.id", ondelete="CASCADE"), index=True)
    experiment_version: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ExperimentRun(Base):
    __tablename__ = "rl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    suite_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)  # frozen copy of the experiment at request time
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    phase: Mapped[str] = mapped_column(String(20), default="queued")
    verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(60))
    break_glass: Mapped[str] = mapped_column(Text, default="")
    events: Mapped[list] = mapped_column(JSON, default=list)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Finding(Base):
    __tablename__ = "rl_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    journey_id: Mapped[str] = mapped_column(String(40))
    edge_key: Mapped[str] = mapped_column(String(80))
    fault_id: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # high | medium | low
    title: Mapped[str] = mapped_column(Text)
    what_happened: Mapped[str] = mapped_column(Text)
    guardrail: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | filed | resolved
    reference: Mapped[str] = mapped_column(String(40), default="")
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Suite(Base):
    __tablename__ = "rl_suites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    journey_id: Mapped[str] = mapped_column(String(40))
    experiment_ids: Mapped[list] = mapped_column(JSON, default=list)
    gate_mode: Mapped[str] = mapped_column(String(10), default="block")  # block | warn | report
    gate_threshold: Mapped[int] = mapped_column(Integer, default=70)
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SuiteRun(Base):
    __tablename__ = "rl_suite_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suite_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|waiting_window|running|completed|failed
    gate: Mapped[str | None] = mapped_column(String(10), nullable=True)  # passed | warned | blocked
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_ids: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PolicyVersion(Base):
    __tablename__ = "rl_policy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # set when published
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | published | superseded | rejected
    body: Mapped[dict] = mapped_column(JSON)
    proposed_by: Mapped[str] = mapped_column(String(60))
    approved_by: Mapped[str] = mapped_column(String(60), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EdgeOverride(Base):
    __tablename__ = "rl_edge_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    edge_key: Mapped[str] = mapped_column(String(80), index=True)
    fault_id: Mapped[str] = mapped_column(String(60))  # "*" = every fault
    reason: Mapped[str] = mapped_column(Text)
    review_by: Mapped[str] = mapped_column(String(20), default="")
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ControlState(Base):
    """Small key/value store for tenant-wide switches (kill switch, guardrails, fault enablement)."""

    __tablename__ = "rl_control"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(60), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base):
    """Append-only, hash-chained audit record. The API exposes no update or delete path."""

    __tablename__ = "rl_audit"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    actor: Mapped[str] = mapped_column(String(60))
    on_behalf_of: Mapped[str] = mapped_column(String(60), default="")
    event: Mapped[str] = mapped_column(String(40), index=True)
    subject: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    change_ref: Mapped[str] = mapped_column(String(40), default="")
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
