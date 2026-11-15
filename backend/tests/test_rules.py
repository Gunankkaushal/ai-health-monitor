"""Deterministic alert rules: thresholds, severity selection, trend rule, configurability, units."""

import json
from datetime import datetime, timedelta

import pytest

from health_core.rules import ReadingInput, evaluate_all, evaluate_reading, evaluate_trends, load_rules
from health_core.units import ReadingValidationError, normalize

CFG = load_rules()
T0 = datetime(2026, 1, 10, 8, 0)


def ids(metric, value, value2=None, cfg=CFG):
    return sorted(m.rule_id for m in evaluate_reading(ReadingInput(1, metric, value, value2, T0), cfg))


def test_config_is_documented():
    assert CFG.disclaimer and CFG.population
    for r in [*CFG.rules, *CFG.trend_rules]:
        assert r.source.strip(), r.id
        assert r.limitations.strip(), r.id
        assert r.explanation.strip(), r.id


@pytest.mark.parametrize("value,expected", [
    (100, []), (101, ["hr_high_warning"]), (131, ["hr_high_critical"]),
    (50, []), (49, ["hr_low_warning"]), (39, ["hr_low_critical"]), (72, []),
])
def test_heart_rate(value, expected):
    assert ids("heart_rate", value) == expected


@pytest.mark.parametrize("value,expected", [
    (95, []), (94, ["spo2_low_warning"]), (93, ["spo2_low_warning"]), (92, ["spo2_low_critical"]), (85, ["spo2_low_critical"]),
])
def test_spo2(value, expected):
    assert ids("spo2", value) == expected


@pytest.mark.parametrize("value,expected", [
    (37.9, []), (38.0, ["temp_high_warning"]), (39.4, ["temp_high_critical"]), (34.9, ["temp_low_warning"]), (36.6, []),
])
def test_temperature(value, expected):
    assert ids("temperature", value) == expected


@pytest.mark.parametrize("sys_,dia,expected", [
    (118, 76, []),
    (125, 79, []),
    (132, 78, ["bp_high_info"]),
    (120, 92, ["bp_high_warning"]),
    (185, 100, ["bp_high_critical"]),
    (150, 125, ["bp_high_critical"]),
    (85, 65, ["bp_low_warning"]),
    (100, 55, ["bp_low_warning"]),
])
def test_blood_pressure(sys_, dia, expected):
    assert ids("blood_pressure", sys_, dia) == expected


def test_only_most_severe_rule_per_group():
    matches = evaluate_reading(ReadingInput(1, "heart_rate", 150, None, T0), CFG)
    assert [m.severity for m in matches] == ["critical"]


@pytest.mark.parametrize("value,expected", [(7.5, []), (6.5, ["sleep_short_info"]), (4.5, ["sleep_short_warning"])])
def test_sleep(value, expected):
    assert ids("sleep", value) == expected


def test_weight_trend_rule():
    within = [ReadingInput(1, "weight", 70.0, None, T0), ReadingInput(2, "weight", 72.5, None, T0 + timedelta(days=5))]
    m = evaluate_trends(within, CFG)
    assert [x.rule_id for x in m] == ["weight_change_7d"]
    assert m[0].reading_id == 2

    apart = [ReadingInput(1, "weight", 70.0, None, T0), ReadingInput(2, "weight", 72.5, None, T0 + timedelta(days=10))]
    assert evaluate_trends(apart, CFG) == []


def test_match_carries_explanation_and_source():
    m = evaluate_reading(ReadingInput(9, "spo2", 90, None, T0), CFG)[0]
    d = m.as_dict()
    assert d["origin"] == "rule"
    assert d["reading_id"] == 9 and "92" in d["threshold"] and d["source"].startswith("NHS")


def test_rules_are_configurable(tmp_path):
    data = json.loads(json.dumps(CFG.model_dump()))
    for r in data["rules"]:
        if r["id"] == "hr_high_warning":
            r["conditions"][0]["threshold"] = 90
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    custom = load_rules(path)
    assert ids("heart_rate", 95, cfg=custom) == ["hr_high_warning"]
    assert ids("heart_rate", 95) == []  # default config unchanged


def test_invalid_config_rejected(tmp_path):
    data = CFG.model_dump()
    data["rules"][0]["metric"] = "blood_sugar"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules(path)


def test_evaluate_all_combines_reading_and_trend_rules():
    readings = [
        ReadingInput(1, "spo2", 91, None, T0),
        ReadingInput(2, "weight", 80, None, T0),
        ReadingInput(3, "weight", 77.5, None, T0 + timedelta(days=2)),
    ]
    assert sorted(m.rule_id for m in evaluate_all(readings, CFG)) == ["spo2_low_critical", "weight_change_7d"]


# ---- units / validation feeding the rules
def test_fahrenheit_converted_before_rules():
    v, _, unit = normalize("temperature", 100.4, "°F")
    assert unit == "°C" and v == 38.0
    assert ids("temperature", v) == ["temp_high_warning"]


def test_pounds_converted():
    v, _, unit = normalize("weight", 154, "lb")
    assert unit == "kg" and abs(v - 69.9) < 0.1


@pytest.mark.parametrize("metric,value,value2,unit", [
    ("heart_rate", 900, None, "bpm"),
    ("spo2", 101, None, "%"),
    ("blood_pressure", 120, None, "mmHg"),
    ("blood_pressure", 80, 90, "mmHg"),
    ("temperature", 37, None, "K"),
    ("glucose", 5, None, None),
])
def test_implausible_or_invalid_rejected(metric, value, value2, unit):
    with pytest.raises(ReadingValidationError):
        normalize(metric, value, unit, value2)
