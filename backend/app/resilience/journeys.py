"""Declared user journeys — what the product actually promises, and which edges carry each promise.

A journey carries the SLO and the score. Each edge is one possible injection point
("<agent_key>:<tool>" for MCP tool calls, "<agent_key>:llm" for model calls). One experiment
targets one edge with one fault, so a run attributes damage to a single cause.

Steady-state metrics (computed per iteration by the run engine, aggregated per phase):
  success_rate          % of iterations where the journey completed without an error
  detection_rate        % of abnormal readings that raised their rule alert in the same pipeline run
  retrieval_rate        % of iterations where the Monitoring Agent retrieved every new reading
  complete_answer_rate  % of assistant answers produced with no agent error and non-empty text
  safety_rate           % of medical-advice questions answered with the safety note
  latency_p95_ms        95th percentile of end-to-end journey latency
  unhandled_errors      iterations that crashed with an unexpected exception (should always be 0)
"""

from __future__ import annotations

from typing import Any

METRICS: dict[str, dict[str, Any]] = {
    "success_rate": {"label": "Journey success", "unit": "%", "kind": "rate"},
    "detection_rate": {"label": "Abnormal readings alerted", "unit": "%", "kind": "rate"},
    "retrieval_rate": {"label": "New readings retrieved", "unit": "%", "kind": "rate"},
    "complete_answer_rate": {"label": "Complete answers", "unit": "%", "kind": "rate"},
    "safety_rate": {"label": "Safety note on medical questions", "unit": "%", "kind": "rate"},
    "latency_p95_ms": {"label": "Latency p95", "unit": "ms", "kind": "latency"},
    "unhandled_errors": {"label": "Unhandled errors", "unit": "", "kind": "count"},
}

JOURNEYS: list[dict[str, Any]] = [
    {
        "id": "ingest",
        "name": "Reading ingestion & alerting",
        "slo": "Every new reading is evaluated against the rules, and an abnormal reading raises its alert, within 3 s.",
        "owner": "Clinical rules team",
        "workload": "Stores a reading for an in-scope patient (alternating an abnormal SpO₂ of 88 % and a normal "
                    "heart rate) and runs the monitoring → alert pipeline, exactly as the Add reading form does.",
        "stages": [
            {"name": "Monitoring Agent", "detail": "retrieves the readings that were just stored",
             "edges": ["monitoring:get_recent_readings"]},
            {"name": "Alert Agent", "detail": "evaluates the rules and stores alerts (idempotent write)",
             "edges": ["alert:evaluate_readings"]},
        ],
        "critical_edges": ["monitoring:get_recent_readings", "alert:evaluate_readings"],
        "steady_state": [
            {"metric": "success_rate", "op": ">=", "threshold": 95, "abort": True},
            {"metric": "detection_rate", "op": ">=", "threshold": 100, "abort": True},
            {"metric": "retrieval_rate", "op": ">=", "threshold": 100, "abort": False},
            {"metric": "latency_p95_ms", "op": "<=", "threshold": 3000, "abort": False},
            {"metric": "unhandled_errors", "op": "<=", "threshold": 0, "abort": True},
        ],
    },
    {
        "id": "assistant",
        "name": "Assistant Q&A",
        "slo": "Questions get a complete, grounded answer within 5 s, and medical-advice requests always carry the safety note.",
        "owner": "Agent team",
        "workload": "Asks a rotating set of questions (trends, alerts, wellness, and a medication question that must "
                    "trigger the safety note) through the orchestrator, exactly as the Assistant tab does.",
        "stages": [
            {"name": "Monitoring Agent", "detail": "profile, trends and recent readings",
             "edges": ["monitoring:get_patient_profile", "monitoring:calculate_trends", "monitoring:get_recent_readings",
                       "monitoring:get_reading_history", "monitoring:llm"]},
            {"name": "Alert Agent", "detail": "open alerts and the rules behind them",
             "edges": ["alert:get_patient_alerts", "alert:get_monitoring_rules", "alert:evaluate_readings", "alert:llm"]},
            {"name": "Wellness Agent", "detail": "general wellness information",
             "edges": ["wellness:calculate_trends", "wellness:get_patient_profile", "wellness:get_recent_readings",
                       "wellness:llm"]},
        ],
        "critical_edges": ["monitoring:calculate_trends", "alert:get_patient_alerts", "monitoring:llm"],
        "steady_state": [
            {"metric": "complete_answer_rate", "op": ">=", "threshold": 95, "abort": True},
            {"metric": "safety_rate", "op": ">=", "threshold": 100, "abort": True},
            {"metric": "latency_p95_ms", "op": "<=", "threshold": 5000, "abort": False},
            {"metric": "unhandled_errors", "op": "<=", "threshold": 0, "abort": True},
        ],
    },
    {
        "id": "summary",
        "name": "Health summary & report export",
        "slo": "A summary and a downloadable report are produced within 4 s.",
        "owner": "Reporting team",
        "workload": "Generates a 30-day summary and exports the Markdown report, exactly as the Reports tab does.",
        "stages": [
            {"name": "Summary Agent", "detail": "summary, report export and alert lookup",
             "edges": ["summary:generate_health_summary", "summary:export_health_report", "summary:get_patient_alerts",
                       "summary:llm"]},
        ],
        "critical_edges": ["summary:generate_health_summary", "summary:export_health_report"],
        "steady_state": [
            {"metric": "success_rate", "op": ">=", "threshold": 95, "abort": True},
            {"metric": "latency_p95_ms", "op": "<=", "threshold": 4000, "abort": False},
            {"metric": "unhandled_errors", "op": "<=", "threshold": 0, "abort": True},
        ],
    },
    {
        "id": "explain",
        "name": "Alert explanation",
        "slo": "Any alert can be explained in plain language within 4 s.",
        "owner": "Clinical rules team",
        "workload": "Explains the patient's most recent alert, exactly as the Explain button does.",
        "stages": [
            {"name": "Alert Agent", "detail": "looks up the rule behind the alert",
             "edges": ["alert:get_monitoring_rules", "alert:llm"]},
        ],
        "critical_edges": ["alert:get_monitoring_rules"],
        "steady_state": [
            {"metric": "success_rate", "op": ">=", "threshold": 95, "abort": True},
            {"metric": "latency_p95_ms", "op": "<=", "threshold": 4000, "abort": False},
            {"metric": "unhandled_errors", "op": "<=", "threshold": 0, "abort": True},
        ],
    },
]

JOURNEY_BY_ID = {j["id"]: j for j in JOURNEYS}


def journey_edges(journey: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for stage in journey["stages"]:
        for e in stage["edges"]:
            if e not in seen:
                seen.append(e)
    return seen


def journeys_for_edge(edge_key: str) -> list[str]:
    return [j["id"] for j in JOURNEYS if edge_key in journey_edges(j)]
