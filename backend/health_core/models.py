"""Database tables. All data in this project is synthetic demo data."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    age: Mapped[int] = mapped_column(Integer)
    sex: Mapped[str] = mapped_column(String(20), default="unspecified")
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    readings: Mapped[list[Reading]] = relationship(back_populates="patient", cascade="all, delete-orphan")
    alerts: Mapped[list[Alert]] = relationship(back_populates="patient", cascade="all, delete-orphan")


class Reading(Base):
    """One measurement of one metric. Values are stored in canonical units (see units.py)."""

    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(32), index=True)
    value: Mapped[float] = mapped_column(Float)
    value2: Mapped[float | None] = mapped_column(Float, nullable=True)  # diastolic for blood_pressure
    unit: Mapped[str] = mapped_column(String(16))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | simulator | seed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="readings")


class Alert(Base):
    """A rule-generated alert. ``explanation`` is deterministic rule text; ``ai_explanation`` is
    optional, model-written, and always displayed with an "AI-written" label."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("patient_id", "rule_id", "reading_id", name="uq_alert_rule_reading"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    reading_id: Mapped[int | None] = mapped_column(ForeignKey("readings.id", ondelete="CASCADE"), nullable=True)
    rule_id: Mapped[str] = mapped_column(String(64))
    metric: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))  # info | warning | critical
    message: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    observed: Mapped[str] = mapped_column(String(64))
    threshold: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(Text)
    ai_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="alerts")


class AgentActivity(Base):
    """Audit trail of agent runs and the MCP tool calls they made."""

    __tablename__ = "agent_activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    patient_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(40))
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    server: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # ok | error | rejected | info
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
