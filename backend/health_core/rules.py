"""Deterministic, configurable alert rules.

Thresholds come ONLY from rules_config.json (overridable via RULES_CONFIG_PATH).
No LLM is involved in deciding whether an alert fires.
"""

from __future__ import annotations

import json
import operator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .settings import rules_config_path
from .units import METRIC_KEYS, format_value

Severity = Literal["info", "warning", "critical"]
SEVERITY_RANK: dict[str, int] = {"info": 1, "warning": 2, "critical": 3}

_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


class Condition(BaseModel):
    field: Literal["value", "value2"]
    op: Literal[">", ">=", "<", "<="]
    threshold: float


class ThresholdRule(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{3,64}$")
    group: str
    metric: str
    match: Literal["any", "all"] = "any"
    conditions: list[Condition] = Field(min_length=1)
    severity: Severity
    message: str
    explanation: str
    source: str
    limitations: str

    @field_validator("metric")
    @classmethod
    def _known_metric(cls, v: str) -> str:
        if v not in METRIC_KEYS:
            raise ValueError(f"unknown metric {v}")
        return v


class TrendRule(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{3,64}$")
    group: str
    metric: str
    type: Literal["abs_change_within_days"]
    days: int = Field(ge=1, le=90)
    threshold: float = Field(gt=0)
    severity: Severity
    message: str
    explanation: str
    source: str
    limitations: str


class RulesConfig(BaseModel):
    version: str
    population: str
    disclaimer: str
    rules: list[ThresholdRule]
    trend_rules: list[TrendRule] = []


def load_rules(path: Path | None = None) -> RulesConfig:
    path = path or rules_config_path()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cfg = RulesConfig.model_validate(data)
    ids = [r.id for r in cfg.rules] + [r.id for r in cfg.trend_rules]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate rule ids in rules config")
    return cfg


@dataclass(frozen=True)
class ReadingInput:
    id: int | None
    metric: str
    value: float
    value2: float | None
    recorded_at: datetime


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    group: str
    metric: str
    severity: str
    message: str
    explanation: str
    source: str
    limitations: str
    observed: str
    threshold: str
    reading_id: int | None
    recorded_at: datetime

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["recorded_at"] = self.recorded_at.isoformat()
        d["origin"] = "rule"
        return d


def _describe_conditions(rule: ThresholdRule) -> str:
    names = {"value": "systolic" if rule.metric == "blood_pressure" else "value", "value2": "diastolic"}
    joiner = " or " if rule.match == "any" else " and "
    return joiner.join(f"{names[c.field]} {c.op} {c.threshold:g}" for c in rule.conditions)


def _rule_matches(rule: ThresholdRule, reading: ReadingInput) -> bool:
    results = []
    for c in rule.conditions:
        v = reading.value if c.field == "value" else reading.value2
        results.append(v is not None and _OPS[c.op](v, c.threshold))
    return any(results) if rule.match == "any" else all(results)


def evaluate_reading(reading: ReadingInput, config: RulesConfig) -> list[RuleMatch]:
    """Return at most one match per rule group — the most severe one."""
    best: dict[str, ThresholdRule] = {}
    for rule in config.rules:
        if rule.metric != reading.metric or not _rule_matches(rule, reading):
            continue
        current = best.get(rule.group)
        if current is None or SEVERITY_RANK[rule.severity] > SEVERITY_RANK[current.severity]:
            best[rule.group] = rule
    return [
        RuleMatch(
            rule_id=r.id,
            group=r.group,
            metric=r.metric,
            severity=r.severity,
            message=r.message,
            explanation=r.explanation,
            source=r.source,
            limitations=r.limitations,
            observed=format_value(reading.metric, reading.value, reading.value2),
            threshold=_describe_conditions(r),
            reading_id=reading.id,
            recorded_at=reading.recorded_at,
        )
        for r in best.values()
    ]


def evaluate_trends(readings: list[ReadingInput], config: RulesConfig) -> list[RuleMatch]:
    """Evaluate trend rules against the latest reading of each metric."""
    matches: list[RuleMatch] = []
    for rule in config.trend_rules:
        series = sorted((r for r in readings if r.metric == rule.metric), key=lambda r: r.recorded_at)
        if len(series) < 2:
            continue
        latest = series[-1]
        window = [r for r in series if r.recorded_at >= latest.recorded_at - timedelta(days=rule.days)]
        if len(window) < 2:
            continue
        lo = min(window, key=lambda r: r.value)
        hi = max(window, key=lambda r: r.value)
        change = hi.value - lo.value
        if change >= rule.threshold:
            matches.append(
                RuleMatch(
                    rule_id=rule.id,
                    group=rule.group,
                    metric=rule.metric,
                    severity=rule.severity,
                    message=rule.message,
                    explanation=rule.explanation,
                    source=rule.source,
                    limitations=rule.limitations,
                    observed=f"{change:.1f} kg range ({lo.value:g}–{hi.value:g} kg) over {rule.days} days",
                    threshold=f"change >= {rule.threshold:g} kg within {rule.days} days",
                    reading_id=latest.id,
                    recorded_at=latest.recorded_at,
                )
            )
    return matches


def evaluate_all(readings: list[ReadingInput], config: RulesConfig) -> list[RuleMatch]:
    out: list[RuleMatch] = []
    for r in readings:
        out.extend(evaluate_reading(r, config))
    out.extend(evaluate_trends(readings, config))
    return out
