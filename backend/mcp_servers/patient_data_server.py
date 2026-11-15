"""Patient Data MCP Server (stdio).

Tools: get_patient_profile, get_recent_readings, get_reading_history, calculate_trends
Run standalone for debugging:  python -m mcp_servers.patient_data_server
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from health_core import repository as repo
from mcp_servers._common import Days, Limit, MetricName, PatientId, bootstrap, guarded
from mcp_servers._common import READ_ONLY

log = bootstrap("patient-data")

mcp = MCPServer(
    "patient-data",
    version="1.0.0",
    instructions="Read-only access to one synthetic patient's profile and vital-sign readings.",
)


@mcp.tool(annotations=READ_ONLY)
def get_patient_profile(patient_id: PatientId) -> dict[str, Any]:
    """Return the basic profile (name, age, sex, height, notes) of the patient."""
    return guarded(repo.get_profile, patient_id)


@mcp.tool(annotations=READ_ONLY)
def get_recent_readings(patient_id: PatientId, limit: Limit = 20, metric: MetricName | None = None) -> dict[str, Any]:
    """Return the most recent readings for the patient, newest first. Optionally filter to one metric."""
    rows = guarded(repo.get_recent_readings, patient_id, limit, metric)
    return {"patient_id": patient_id, "count": len(rows), "readings": rows}


@mcp.tool(annotations=READ_ONLY)
def get_reading_history(patient_id: PatientId, days: Days = 30, metric: MetricName | None = None) -> dict[str, Any]:
    """Return all readings in the last N days, oldest first. Optionally filter to one metric."""
    rows = guarded(repo.get_reading_history, patient_id, days, metric)
    return {"patient_id": patient_id, "days": days, "count": len(rows), "readings": rows}


@mcp.tool(annotations=READ_ONLY)
def calculate_trends(patient_id: PatientId, days: Days = 30, metric: MetricName | None = None) -> dict[str, Any]:
    """Compute deterministic per-metric statistics (latest, mean, min, max, slope/day, direction) over N days."""
    return guarded(repo.calculate_trends, patient_id, days, metric)


if __name__ == "__main__":
    mcp.run("stdio")
