"""Deterministic trend statistics (no LLM)."""

from __future__ import annotations

from datetime import datetime
from statistics import mean

from .units import METRICS

# Relative slope (per day, as a fraction of the mean) below which a series counts as "stable".
STABLE_FRACTION_PER_DAY = 0.005


def _slope_per_day(points: list[tuple[datetime, float]]) -> float:
    if len(points) < 2:
        return 0.0
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in points]
    ys = [v for _, v in points]
    mx, my = mean(xs), mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def metric_trend(metric: str, readings: list[dict]) -> dict:
    """readings: dicts with recorded_at (datetime), value, value2 — already filtered to one metric."""
    spec = METRICS[metric]
    pts = sorted(((r["recorded_at"], r["value"]) for r in readings), key=lambda p: p[0])
    if not pts:
        return {"metric": metric, "label": spec.label, "unit": spec.unit, "count": 0, "direction": "no data"}

    values = [v for _, v in pts]
    slope = _slope_per_day(pts)
    avg = mean(values)
    rel = abs(slope) / avg if avg else 0
    direction = "stable" if len(pts) < 3 or rel < STABLE_FRACTION_PER_DAY else ("rising" if slope > 0 else "falling")

    half = len(values) // 2
    first_half_mean = mean(values[:half]) if half else values[0]
    second_half_mean = mean(values[half:])

    out = {
        "metric": metric,
        "label": spec.label,
        "unit": spec.unit,
        "count": len(values),
        "latest": values[-1],
        "latest_at": pts[-1][0].isoformat(),
        "mean": round(avg, 1),
        "min": min(values),
        "max": max(values),
        "slope_per_day": round(slope, 2),
        "first_half_mean": round(first_half_mean, 1),
        "second_half_mean": round(second_half_mean, 1),
        "change_between_halves": round(second_half_mean - first_half_mean, 1),
        "direction": direction,
    }
    if metric == "blood_pressure":
        dia = [r["value2"] for r in sorted(readings, key=lambda r: r["recorded_at"]) if r.get("value2") is not None]
        if dia:
            out["diastolic_latest"] = dia[-1]
            out["diastolic_mean"] = round(mean(dia), 1)
    return out
