"""Health Rules MCP Server (stdio).

Tools: get_monitoring_rules, evaluate_readings, get_patient_alerts
Rules are deterministic and loaded from health_core/rules_config.json (or RULES_CONFIG_PATH).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from health_core import repository as repo
from health_core.rules import load_rules
from mcp_servers._common import Days, Limit, PatientId, bootstrap, guarded
from mcp_servers._common import IDEMPOTENT_WRITE, READ_ONLY

log = bootstrap("health-rules")

mcp = MCPServer(
    "health-rules",
    version="1.0.0",
    instructions="Deterministic, documented monitoring rules. Thresholds come from a config file, never from a model.",
)


@mcp.tool(annotations=READ_ONLY)
def get_monitoring_rules() -> dict[str, Any]:
    """Return every configured monitoring rule with its threshold, severity, source and limitations."""
    try:
        cfg = load_rules()
    except Exception as exc:  # config file broken → clear error instead of a crash
        raise ToolError(f"Rules configuration could not be loaded: {exc}") from exc
    return cfg.model_dump()


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def evaluate_readings(patient_id: PatientId, days: Days = 7) -> dict[str, Any]:
    """Evaluate the patient's readings from the last N days against the configured rules.

    New alerts are stored (idempotently) and every matching alert is returned.
    """
    result = guarded(repo.evaluate_and_store_alerts, patient_id, days)
    log.info("evaluated patient=%s readings=%s new_alerts=%s", patient_id, result["readings_evaluated"], result["new_alert_count"])
    return result


@mcp.tool(annotations=READ_ONLY)
def get_patient_alerts(patient_id: PatientId, limit: Limit = 50, include_acknowledged: bool = True) -> dict[str, Any]:
    """Return stored rule-generated alerts for the patient, newest first."""
    rows = guarded(repo.get_patient_alerts, patient_id, limit, include_acknowledged)
    return {"patient_id": patient_id, "count": len(rows), "alerts": rows}


if __name__ == "__main__":
    mcp.run("stdio")
