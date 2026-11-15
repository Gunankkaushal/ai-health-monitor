"""Reports MCP Server (stdio).

Tools: generate_health_summary, export_health_report
Reports are returned as content; the model never supplies file paths.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from health_core import repository as repo
from mcp_servers._common import Days, PatientId, bootstrap, guarded
from mcp_servers._common import READ_ONLY

log = bootstrap("reports")

mcp = MCPServer(
    "reports",
    version="1.0.0",
    instructions="Deterministic health summaries and exportable reports for one patient.",
)


@mcp.tool(annotations=READ_ONLY)
def generate_health_summary(patient_id: PatientId, days: Days = 30) -> dict[str, Any]:
    """Build a readable summary (profile, per-metric trends, rule-generated alerts) for the last N days."""
    return guarded(repo.generate_health_summary, patient_id, days)


@mcp.tool(annotations=READ_ONLY)
def export_health_report(
    patient_id: PatientId,
    format: Literal["markdown", "json", "csv"] = "markdown",
    days: Days = 30,
) -> dict[str, Any]:
    """Render a downloadable report. Returns {filename, mime_type, content}; nothing is written to disk."""
    return guarded(repo.export_health_report, patient_id, format, days)


if __name__ == "__main__":
    mcp.run("stdio")
