"""Blast-radius policy: tenant-wide limits that hold whatever an experiment asks for.

An experiment may be stricter than the policy, never looser. A request that exceeds a ceiling is
refused with the rule named — never silently clamped, because clamping makes a run look stronger
than it was. Policy is versioned; publishing a change needs two different tenant admins.

Environment mapping for this product:
  * "protected patients" = user-created (non-sample) patients — the equivalent of validated systems.
  * "sandbox"            = the synthetic sample patients.
"""

from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from health_core.db import session_scope
from health_core.models import utcnow
from health_core.resilience_models import PolicyVersion

ENVIRONMENTS = ("dev", "staging", "production")

DEFAULT_POLICY: dict[str, Any] = {
    "ceilings": {
        "max_share_pct": {"dev": 100, "staging": 50, "production": 5},
        "max_duration_s": {"dev": 300, "staging": 120, "production": 30},
        "max_concurrent_runs": {"dev": 2, "staging": 1, "production": 1},
        "max_patients_in_scope": {"dev": 10, "staging": 5, "production": 1},
        "write_path": {"dev": "allowed", "staging": "sandbox_only", "production": "never"},
        "protected_patients": {"dev": "allowed", "staging": "never", "production": "never"},
        "high_risk_faults": {"dev": "allowed", "staging": "approval", "production": "never"},
    },
    "blackout_windows": [
        {"name": "Weekly maintenance", "weekdays": [6], "start": "02:00", "end": "02:30", "scope": "all"},
    ],
    "auto_rollback": {"abort_metric_breach": True, "telemetry_loss_s": 30, "hard_stop_at_duration": True},
}

CEILING_LABELS = {
    "max_share_pct": ("Share of calls faulted", "Injector, at arm time"),
    "max_duration_s": ("Maximum fault duration", "Run watchdog"),
    "max_concurrent_runs": ("Concurrent runs", "Run queue"),
    "max_patients_in_scope": ("Patients in blast radius", "Target resolver"),
    "write_path": ("Write-path injection", "Target resolver"),
    "protected_patients": ("User-created (protected) patients", "Target resolver"),
    "high_risk_faults": ("High-risk fault classes", "Approval service"),
}


def environment() -> str:
    env = (os.environ.get("RESILIENCE_ENV") or "dev").strip().lower()
    return env if env in ENVIRONMENTS else "dev"


def current() -> dict[str, Any]:
    """The published policy in force: {version, body, published_at, ...}. Version 1 is the built-in default."""
    with session_scope() as s:
        row = s.scalars(select(PolicyVersion).where(PolicyVersion.status == "published")
                        .order_by(PolicyVersion.version.desc()).limit(1)).first()
        if row is None:
            row = PolicyVersion(version=1, status="published", body=copy.deepcopy(DEFAULT_POLICY),
                                proposed_by="system", approved_by="system", reason="Built-in default policy",
                                published_at=utcnow())
            s.add(row)
            s.flush()
        return version_dict(row)


def version_dict(r: PolicyVersion) -> dict[str, Any]:
    return {"id": r.id, "version": r.version, "status": r.status, "body": r.body, "proposed_by": r.proposed_by,
            "approved_by": r.approved_by, "reason": r.reason, "created_at": r.created_at.isoformat(),
            "published_at": r.published_at.isoformat() if r.published_at else None}


def validate_body(body: dict[str, Any]) -> list[str]:
    """Structural validation for a proposed policy body."""
    errors: list[str] = []
    ceilings = body.get("ceilings")
    if not isinstance(ceilings, dict):
        return ["'ceilings' must be an object"]
    for key in DEFAULT_POLICY["ceilings"]:
        per_env = ceilings.get(key)
        if not isinstance(per_env, dict) or set(per_env) != set(ENVIRONMENTS):
            errors.append(f"ceilings.{key} must define dev, staging and production")
            continue
        for env, v in per_env.items():
            if key in ("write_path", "protected_patients", "high_risk_faults"):
                allowed = {"write_path": {"allowed", "sandbox_only", "never"},
                           "protected_patients": {"allowed", "never"},
                           "high_risk_faults": {"allowed", "approval", "never"}}[key]
                if v not in allowed:
                    errors.append(f"ceilings.{key}.{env} must be one of {sorted(allowed)}")
            elif not isinstance(v, int) or v < 0:
                errors.append(f"ceilings.{key}.{env} must be a non-negative integer")
    for i, w in enumerate(body.get("blackout_windows") or []):
        try:
            _hhmm(w["start"]), _hhmm(w["end"])
            if not all(isinstance(d, int) and 0 <= d <= 6 for d in w["weekdays"]):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            errors.append(f"blackout_windows[{i}] needs name, weekdays (0=Mon…6=Sun), start and end as HH:MM")
    if not isinstance(body.get("auto_rollback"), dict):
        errors.append("'auto_rollback' must be an object")
    return errors


def _hhmm(v: str) -> int:
    h, m = v.split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError
    return h * 60 + m


def active_blackout(policy_body: dict[str, Any], servers: set[str], now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    minute = now.hour * 60 + now.minute
    for w in policy_body.get("blackout_windows") or []:
        if now.weekday() not in w.get("weekdays", []):
            continue
        start, end = _hhmm(w["start"]), _hhmm(w["end"])
        inside = start <= minute < end if start <= end else (minute >= start or minute < end)
        scope = w.get("scope", "all")
        if inside and (scope == "all" or scope in servers):
            return w
    return None


def check_experiment(spec: dict[str, Any], edge: dict[str, Any], fault: dict[str, Any], patients: dict[int, bool],
                     policy_body: dict[str, Any], env: str) -> list[dict[str, str]]:
    """Return policy violations as [{rule, message}]. ``patients`` maps id -> is_synthetic."""
    c = policy_body["ceilings"]
    v: list[dict[str, str]] = []

    def add(rule: str, message: str) -> None:
        v.append({"rule": rule, "message": message})

    if spec["share_pct"] > c["max_share_pct"][env]:
        add("max_share_pct", f"{spec['share_pct']}% of calls exceeds the {env} ceiling of {c['max_share_pct'][env]}%.")
    if spec["duration_s"] > c["max_duration_s"][env]:
        add("max_duration_s", f"{spec['duration_s']} s exceeds the {env} ceiling of {c['max_duration_s'][env]} s.")
    scope = spec["patient_scope"]
    if len(scope) > c["max_patients_in_scope"][env]:
        add("max_patients_in_scope",
            f"{len(scope)} patients in scope exceeds the {env} ceiling of {c['max_patients_in_scope'][env]}.")
    missing = [p for p in scope if p not in patients]
    if missing:
        add("patient_scope", f"Patients {missing} do not exist.")
    protected = [p for p in scope if p in patients and not patients[p]]
    if protected and c["protected_patients"][env] == "never":
        add("protected_patients", f"Patients {protected} are user-created (protected); {env} only allows sample patients.")
    write_path = edge["direction"] == "write"
    if write_path:
        rule = c["write_path"][env]
        if rule == "never":
            add("write_path", f"Write-path injection is never allowed in {env} ({edge['tool']} writes alerts).")
        elif rule == "sandbox_only" and protected:
            add("write_path", f"Write-path injection in {env} is limited to sample (sandbox) patients.")
    if fault["risk"] == "high" and c["high_risk_faults"][env] == "never":
        add("high_risk_faults", f"High-risk fault '{fault['name']}' is never allowed in {env}.")
    return v
