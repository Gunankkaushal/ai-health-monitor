"""Create tables and load SYNTHETIC demo patients with ~30 days of readings.

Usage:  python -m app.seed            (seed only if the database is empty)
        python -m app.seed --reset    (drop everything and reseed)
"""

from __future__ import annotations

import argparse
import random
from datetime import timedelta

from sqlalchemy import func, select

from health_core import resilience_models  # noqa: F401  (so --reset also drops the resilience tables)
from health_core.db import Base, get_engine, init_db, session_scope
from health_core.models import Patient, Reading, utcnow
from health_core.repository import evaluate_and_store_alerts

from .simulator import simulate_vitals

DEMO_PATIENTS = [
    # name, age, sex, height, notes, base weight, scenario schedule {days_ago: scenario}
    ("Alex Rivera", 34, "male", 178.0, "Routine monitoring. Readings generally steady.", 76.0, {}),
    ("Priya Shah", 58, "female", 162.0, "Blood pressure trending upward over the last week.", 68.0,
     {6: "high_blood_pressure", 4: "high_blood_pressure", 2: "high_blood_pressure", 1: "poor_sleep"}),
    ("Sam Okafor", 71, "male", 170.0, "Recent short fever with a low-oxygen episode.", 82.0,
     {9: "fever", 8: "fever", 3: "low_oxygen"}),
]


def seed(reset: bool = False, days: int = 30, rng_seed: int = 42) -> int:
    if reset:
        Base.metadata.drop_all(get_engine())
    init_db()
    with session_scope() as s:
        if s.scalar(select(func.count(Patient.id))):
            return 0

    rng = random.Random(rng_seed)
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    created_ids: list[int] = []
    with session_scope() as s:
        for name, age, sex, height, notes, weight, schedule in DEMO_PATIENTS:
            p = Patient(name=name, age=age, sex=sex, height_cm=height, notes=notes, is_synthetic=True)
            s.add(p)
            s.flush()
            created_ids.append(p.id)
            for d in range(days, 0, -1):
                scenario = schedule.get(d, "normal")
                ts = now - timedelta(days=d) + timedelta(hours=8, minutes=rng.randint(0, 50))
                drift = (days - d) * (0.6 if name == "Priya Shah" else 0)  # gradual BP drift
                for v in simulate_vitals(scenario, rng, base_weight=weight):
                    value = v["value"] + (drift if v["metric"] == "blood_pressure" and scenario == "normal" else 0)
                    s.add(Reading(patient_id=p.id, metric=v["metric"], value=value, value2=v["value2"],
                                  unit=v["unit"], recorded_at=ts, source="seed"))
    # Pre-compute rule alerts for the seeded history (same deterministic rules the MCP server uses).
    for pid in created_ids:
        evaluate_and_store_alerts(pid, days=days + 1)
    return len(created_ids)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop all tables and reseed")
    args = ap.parse_args()
    n = seed(reset=args.reset)
    print(f"Seeded {n} synthetic patients." if n else "Database already has patients; nothing to seed (use --reset).")
