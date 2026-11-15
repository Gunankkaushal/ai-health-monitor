"""Request validation models for the REST API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from health_core.units import ReadingValidationError, normalize

MetricKey = Literal["heart_rate", "blood_pressure", "temperature", "spo2", "weight", "sleep"]


class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    age: int = Field(ge=0, le=120)
    sex: Literal["female", "male", "other", "unspecified"] = "unspecified"
    height_cm: float | None = Field(default=None, ge=40, le=250)
    notes: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name must not be blank")
        return v


class ReadingEntry(BaseModel):
    metric: MetricKey
    value: float
    value2: float | None = None
    unit: str | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def _normalize(self) -> ReadingEntry:
        try:
            v, v2, unit = normalize(self.metric, self.value, self.unit, self.value2)
        except ReadingValidationError as exc:
            raise ValueError(str(exc)) from None
        self.value, self.value2, self.unit = v, v2, unit
        return self


class ReadingsIn(BaseModel):
    recorded_at: datetime | None = None
    entries: list[ReadingEntry] = Field(min_length=1, max_length=6)

    @field_validator("recorded_at")
    @classmethod
    def _to_utc_naive(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if v > now + timedelta(minutes=5):
            raise ValueError("Timestamp cannot be in the future")
        if v < now - timedelta(days=365 * 5):
            raise ValueError("Timestamp is more than 5 years in the past")
        return v

    @model_validator(mode="after")
    def _unique_metrics(self) -> ReadingsIn:
        metrics = [e.metric for e in self.entries]
        if len(metrics) != len(set(metrics)):
            raise ValueError("Each metric can only appear once per submission")
        return self


class SimulateIn(BaseModel):
    scenario: Literal["normal", "fever", "low_oxygen", "high_blood_pressure", "poor_sleep", "tachycardia"] = "normal"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class SummaryIn(BaseModel):
    days: int = Field(default=30, ge=1, le=365)
