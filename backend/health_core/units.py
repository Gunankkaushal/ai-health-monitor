"""Metric catalogue, canonical units, conversions, and plausibility ranges.

Plausibility ranges only reject obvious data-entry errors (e.g. a heart rate of 2000).
They are NOT clinical thresholds — those live in rules_config.json.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str
    accepted_units: tuple[str, ...]
    min_value: float
    max_value: float
    min_value2: float | None = None
    max_value2: float | None = None


METRICS: dict[str, MetricSpec] = {
    "heart_rate": MetricSpec("heart_rate", "Heart rate", "bpm", ("bpm",), 20, 250),
    "blood_pressure": MetricSpec(
        "blood_pressure", "Blood pressure", "mmHg", ("mmHg",), 50, 260, min_value2=30, max_value2=160
    ),
    "temperature": MetricSpec("temperature", "Temperature", "°C", ("°C", "C", "°F", "F"), 30, 45),
    "spo2": MetricSpec("spo2", "Oxygen saturation", "%", ("%",), 50, 100),
    "weight": MetricSpec("weight", "Weight", "kg", ("kg", "lb"), 2, 400),
    "sleep": MetricSpec("sleep", "Sleep duration", "hours", ("hours", "h", "min"), 0, 24),
}

METRIC_KEYS = tuple(METRICS.keys())


class ReadingValidationError(ValueError):
    pass


def normalize(metric: str, value: float, unit: str | None, value2: float | None = None) -> tuple[float, float | None, str]:
    """Convert to canonical units and validate plausibility. Returns (value, value2, canonical_unit)."""
    spec = METRICS.get(metric)
    if spec is None:
        raise ReadingValidationError(f"Unknown metric '{metric}'. Expected one of: {', '.join(METRIC_KEYS)}")
    unit = (unit or spec.unit).strip()
    if unit not in spec.accepted_units:
        raise ReadingValidationError(
            f"Unit '{unit}' is not accepted for {spec.label}. Use one of: {', '.join(spec.accepted_units)}"
        )

    if metric == "temperature" and unit in ("°F", "F"):
        value = (value - 32) * 5 / 9
    elif metric == "weight" and unit == "lb":
        value = value * 0.45359237
    elif metric == "sleep" and unit == "min":
        value = value / 60

    value = round(float(value), 2 if metric == "sleep" else 1)
    if not (spec.min_value <= value <= spec.max_value):
        raise ReadingValidationError(
            f"{spec.label} {value} {spec.unit} is outside the plausible range "
            f"{spec.min_value}–{spec.max_value} {spec.unit}. Please check the entry."
        )

    if metric == "blood_pressure":
        if value2 is None:
            raise ReadingValidationError("Blood pressure needs both systolic and diastolic values.")
        value2 = round(float(value2), 1)
        assert spec.min_value2 is not None and spec.max_value2 is not None
        if not (spec.min_value2 <= value2 <= spec.max_value2):
            raise ReadingValidationError(
                f"Diastolic {value2} mmHg is outside the plausible range {spec.min_value2}–{spec.max_value2} mmHg."
            )
        if value2 >= value:
            raise ReadingValidationError("Diastolic pressure must be lower than systolic pressure.")
    else:
        value2 = None

    return value, value2, spec.unit


def format_value(metric: str, value: float, value2: float | None) -> str:
    spec = METRICS[metric]
    if metric == "blood_pressure" and value2 is not None:
        return f"{value:g}/{value2:g} {spec.unit}"
    return f"{value:g} {spec.unit}"
