"""Patient-scoped data access used by the MCP servers.

Every function takes an explicit ``patient_id`` and every query filters on it, so a
tool call can only ever see one patient's records. No raw SQL or file paths are
accepted from callers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import reports
from .db import session_scope
from .models import Alert, Patient, Reading, utcnow
from .rules import ReadingInput, RulesConfig, evaluate_all, load_rules
from .trends import metric_trend
from .units import METRIC_KEYS, METRICS, format_value


class PatientNotFound(LookupError):
    pass


def _patient_or_raise(session, patient_id: int) -> Patient:
    patient = session.get(Patient, patient_id)
    if patient is None:
        raise PatientNotFound(f"Patient {patient_id} does not exist")
    return patient


def profile_dict(p: Patient) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "age": p.age,
        "sex": p.sex,
        "height_cm": p.height_cm,
        "notes": p.notes,
        "is_synthetic": p.is_synthetic,
        "created_at": p.created_at.isoformat(),
    }


def reading_dict(r: Reading) -> dict:
    return {
        "id": r.id,
        "patient_id": r.patient_id,
        "metric": r.metric,
        "label": METRICS[r.metric].label,
        "value": r.value,
        "value2": r.value2,
        "unit": r.unit,
        "display": format_value(r.metric, r.value, r.value2),
        "recorded_at": r.recorded_at.isoformat(),
        "source": r.source,
    }


def alert_dict(a: Alert) -> dict:
    return {
        "id": a.id,
        "patient_id": a.patient_id,
        "reading_id": a.reading_id,
        "rule_id": a.rule_id,
        "metric": a.metric,
        "severity": a.severity,
        "message": a.message,
        "explanation": a.explanation,
        "observed": a.observed,
        "threshold": a.threshold,
        "source": a.source,
        "ai_explanation": a.ai_explanation,
        "acknowledged": a.acknowledged,
        "recorded_at": a.recorded_at.isoformat(),
        "created_at": a.created_at.isoformat(),
        "origin": "rule",
    }


def _check_metric(metric: str | None) -> None:
    if metric is not None and metric not in METRIC_KEYS:
        raise ValueError(f"Unknown metric '{metric}'. Expected one of: {', '.join(METRIC_KEYS)}")


def get_profile(patient_id: int) -> dict:
    with session_scope() as s:
        return profile_dict(_patient_or_raise(s, patient_id))


def get_recent_readings(patient_id: int, limit: int = 20, metric: str | None = None) -> list[dict]:
    _check_metric(metric)
    with session_scope() as s:
        _patient_or_raise(s, patient_id)
        q = select(Reading).where(Reading.patient_id == patient_id)
        if metric:
            q = q.where(Reading.metric == metric)
        q = q.order_by(Reading.recorded_at.desc(), Reading.id.desc()).limit(limit)
        return [reading_dict(r) for r in s.scalars(q)]


def _history(s, patient_id: int, days: int, metric: str | None, now: datetime | None = None) -> list[Reading]:
    since = (now or utcnow()) - timedelta(days=days)
    q = select(Reading).where(Reading.patient_id == patient_id, Reading.recorded_at >= since)
    if metric:
        q = q.where(Reading.metric == metric)
    return list(s.scalars(q.order_by(Reading.recorded_at.asc(), Reading.id.asc())))


def get_reading_history(patient_id: int, days: int = 30, metric: str | None = None) -> list[dict]:
    _check_metric(metric)
    with session_scope() as s:
        _patient_or_raise(s, patient_id)
        return [reading_dict(r) for r in _history(s, patient_id, days, metric)]


def calculate_trends(patient_id: int, days: int = 30, metric: str | None = None) -> dict:
    _check_metric(metric)
    with session_scope() as s:
        _patient_or_raise(s, patient_id)
        rows = _history(s, patient_id, days, metric)
    metrics = [metric] if metric else list(METRIC_KEYS)
    trends = []
    for m in metrics:
        series = [{"recorded_at": r.recorded_at, "value": r.value, "value2": r.value2} for r in rows if r.metric == m]
        trends.append(metric_trend(m, series))
    return {"patient_id": patient_id, "days": days, "trends": trends}


def evaluate_and_store_alerts(patient_id: int, days: int = 7, config: RulesConfig | None = None) -> dict:
    """Run deterministic rules over the window and persist any new alerts (idempotent)."""
    config = config or load_rules()
    with session_scope() as s:
        _patient_or_raise(s, patient_id)
        rows = _history(s, patient_id, days, None)
        inputs = [ReadingInput(r.id, r.metric, r.value, r.value2, r.recorded_at) for r in rows]
        matches = evaluate_all(inputs, config)

        existing = {
            (a.rule_id, a.reading_id): a
            for a in s.scalars(select(Alert).where(Alert.patient_id == patient_id))
        }
        new_ids: list[int] = []
        results: list[Alert] = []
        for m in matches:
            key = (m.rule_id, m.reading_id)
            if key in existing:
                results.append(existing[key])
                continue
            alert = Alert(
                patient_id=patient_id,
                reading_id=m.reading_id,
                rule_id=m.rule_id,
                metric=m.metric,
                severity=m.severity,
                message=m.message,
                explanation=m.explanation,
                observed=m.observed,
                threshold=m.threshold,
                source=m.source,
                recorded_at=m.recorded_at,
            )
            s.add(alert)
            try:
                s.flush()
            except IntegrityError:  # another process inserted it concurrently
                s.rollback()
                continue
            new_ids.append(alert.id)
            existing[key] = alert
            results.append(alert)

        out = [alert_dict(a) | {"is_new": a.id in new_ids} for a in results]
    out.sort(key=lambda a: a["recorded_at"], reverse=True)
    return {
        "patient_id": patient_id,
        "days": days,
        "readings_evaluated": len(inputs),
        "rules_version": config.version,
        "new_alert_count": len(new_ids),
        "alerts": out,
    }


def get_patient_alerts(patient_id: int, limit: int = 50, include_acknowledged: bool = True) -> list[dict]:
    with session_scope() as s:
        _patient_or_raise(s, patient_id)
        q = select(Alert).where(Alert.patient_id == patient_id)
        if not include_acknowledged:
            q = q.where(Alert.acknowledged.is_(False))
        q = q.order_by(Alert.recorded_at.desc(), Alert.id.desc()).limit(limit)
        return [alert_dict(a) for a in s.scalars(q)]


def generate_health_summary(patient_id: int, days: int = 30) -> dict:
    profile = get_profile(patient_id)
    trends = calculate_trends(patient_id, days)["trends"]
    alerts = [a for a in get_patient_alerts(patient_id, limit=200) if _within(a["recorded_at"], days)]
    return reports.build_summary(profile, trends, alerts, days)


def export_health_report(patient_id: int, fmt: str = "markdown", days: int = 30) -> dict:
    summary = generate_health_summary(patient_id, days)
    history = get_reading_history(patient_id, days)
    return reports.render_report(summary, history, fmt)


def _within(iso: str, days: int) -> bool:
    return datetime.fromisoformat(iso) >= utcnow() - timedelta(days=days)
