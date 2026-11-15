"""SYNTHETIC reading simulator.

Generates plausible-looking fake vital signs for demos. It is not connected to any
wearable or medical device, and every reading it produces is stored with
source="simulator" so it is always labeled as synthetic in the UI.
"""

from __future__ import annotations

import random
from typing import Literal

Scenario = Literal["normal", "fever", "low_oxygen", "high_blood_pressure", "poor_sleep", "tachycardia"]
SCENARIOS: tuple[str, ...] = ("normal", "fever", "low_oxygen", "high_blood_pressure", "poor_sleep", "tachycardia")


def simulate_vitals(scenario: str = "normal", rng: random.Random | None = None, base_weight: float = 72.0) -> list[dict]:
    """Return one synthetic reading per metric as dicts: metric, value, value2, unit."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}'. Choose one of: {', '.join(SCENARIOS)}")
    r = rng or random.Random()

    hr = r.gauss(72, 6)
    sys_bp, dia_bp = r.gauss(118, 6), r.gauss(76, 4)
    temp = r.gauss(36.7, 0.2)
    spo2 = min(100, r.gauss(97.5, 0.8))
    sleep = r.gauss(7.6, 0.45)
    weight = base_weight + r.gauss(0, 0.3)

    if scenario == "fever":
        temp, hr = r.uniform(38.2, 39.0), r.uniform(92, 108)
    elif scenario == "low_oxygen":
        spo2, hr = r.uniform(89, 93), r.uniform(88, 104)
    elif scenario == "high_blood_pressure":
        sys_bp, dia_bp = r.uniform(142, 160), r.uniform(91, 99)
    elif scenario == "poor_sleep":
        sleep = r.uniform(3.8, 5.5)
    elif scenario == "tachycardia":
        hr = r.uniform(112, 135)

    dia_bp = min(dia_bp, sys_bp - 20)
    return [
        {"metric": "heart_rate", "value": round(hr), "value2": None, "unit": "bpm"},
        {"metric": "blood_pressure", "value": round(sys_bp), "value2": round(dia_bp), "unit": "mmHg"},
        {"metric": "temperature", "value": round(temp, 1), "value2": None, "unit": "°C"},
        {"metric": "spo2", "value": round(max(80, spo2)), "value2": None, "unit": "%"},
        {"metric": "weight", "value": round(weight, 1), "value2": None, "unit": "kg"},
        {"metric": "sleep", "value": round(max(0, sleep), 1), "value2": None, "unit": "hours"},
    ]
