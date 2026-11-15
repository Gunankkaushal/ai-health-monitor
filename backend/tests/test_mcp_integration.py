"""Real MCP: subprocess servers over stdio, discovery, tool execution, validation, timeouts, shutdown."""

import subprocess
import sys

import pytest

from app.mcp_client import (
    MCPToolError,
    MCPTimeoutError,
    MCPUnavailableError,
    ToolArgumentError,
    UnknownToolError,
)
from health_core.settings import BACKEND_DIR

EXPECTED = {
    "patient_data": {"get_patient_profile", "get_recent_readings", "get_reading_history", "calculate_trends"},
    "health_rules": {"get_monitoring_rules", "evaluate_readings", "get_patient_alerts"},
    "reports": {"generate_health_summary", "export_health_report"},
}


async def test_all_servers_connect_and_tools_are_discovered(manager):
    status = {s["key"]: s for s in manager.status()}
    assert set(status) == set(EXPECTED)
    for key, tools in EXPECTED.items():
        assert status[key]["status"] == "connected", status[key]["error"]
        assert status[key]["transport"] == "stdio"
        assert status[key]["protocol_version"]
        assert {t["name"] for t in status[key]["tools"]} == tools
        for t in status[key]["tools"]:
            assert t["input_schema"]["type"] == "object"


async def test_every_tool_executes_over_mcp(manager):
    pid = 1
    calls = {
        "get_patient_profile": {"patient_id": pid},
        "get_recent_readings": {"patient_id": pid, "limit": 3},
        "get_reading_history": {"patient_id": pid, "days": 7, "metric": "heart_rate"},
        "calculate_trends": {"patient_id": pid, "days": 30},
        "get_monitoring_rules": {},
        "evaluate_readings": {"patient_id": pid, "days": 7},
        "get_patient_alerts": {"patient_id": pid, "limit": 5},
        "generate_health_summary": {"patient_id": pid, "days": 30},
        "export_health_report": {"patient_id": pid, "format": "markdown", "days": 30},
    }
    for tool, args in calls.items():
        res = await manager.call_tool(tool, args)
        assert res.data is not None, tool
        assert res.server in EXPECTED and tool in EXPECTED[res.server]

    recent = (await manager.call_tool("get_recent_readings", {"patient_id": pid, "limit": 3})).data
    assert recent["count"] == 3
    hist = (await manager.call_tool("get_reading_history", {"patient_id": pid, "days": 7, "metric": "heart_rate"})).data
    assert all(r["metric"] == "heart_rate" for r in hist["readings"])
    report = (await manager.call_tool("export_health_report", {"patient_id": pid, "format": "markdown", "days": 30})).data
    assert report["filename"].endswith(".md") and "# Health summary" in report["content"]


async def test_client_side_argument_validation(manager):
    with pytest.raises(ToolArgumentError):
        await manager.call_tool("get_reading_history", {"patient_id": 1, "days": 0})
    with pytest.raises(ToolArgumentError):
        await manager.call_tool("get_recent_readings", {"patient_id": 1, "metric": "blood_sugar"})
    with pytest.raises(ToolArgumentError):
        await manager.call_tool("export_health_report", {"patient_id": 1, "format": "exe"})


async def test_server_side_validation_and_errors(manager):
    # Bypass the client-side validator to prove the server validates independently.
    client = manager._clients["patient_data"]
    res = await client.call_tool("get_recent_readings", {"patient_id": 1, "limit": 99999})
    assert res.is_error

    with pytest.raises(MCPToolError, match="does not exist"):
        await manager.call_tool("get_patient_profile", {"patient_id": 999999})
    with pytest.raises(UnknownToolError):
        await manager.call_tool("run_sql", {"query": "DROP TABLE patients"})


async def test_timeout_is_enforced_and_connection_survives(manager):
    with pytest.raises(MCPTimeoutError):
        await manager.call_tool("generate_health_summary", {"patient_id": 1, "days": 365}, timeout=0.0005)
    res = await manager.call_tool("get_patient_profile", {"patient_id": 1})
    assert res.data["id"] == 1


async def test_reconnect_restarts_server(manager):
    await manager.reconnect("reports")
    st = {s["key"]: s for s in manager.status()}["reports"]
    assert st["status"] == "connected"
    assert (await manager.call_tool("generate_health_summary", {"patient_id": 1, "days": 7})).data["patient_id"] == 1


async def test_heartbeat_keeps_connections_alive():
    """Regression: protocol 2026-07-28 removed `ping`; the heartbeat must not drop healthy servers."""
    import asyncio

    from tests.conftest import running_manager

    async with running_manager(heartbeat_interval=0.5) as m:
        first = {s["key"]: s["last_heartbeat"] for s in m.status()}
        await asyncio.sleep(3)
        for s in m.status():
            assert s["status"] == "connected", s["error"]
            assert s["restarts"] == 0
            assert s["last_heartbeat"] != first[s["key"]]  # at least one successful heartbeat happened
        assert (await m.call_tool("get_patient_profile", {"patient_id": 1})).data["id"] == 1


async def test_shutdown_stops_servers(manager):
    await manager.stop()
    assert all(s["status"] == "stopped" for s in manager.status())
    with pytest.raises(MCPUnavailableError):
        await manager.call_tool("get_patient_profile", {"patient_id": 1})


def test_server_keeps_logs_off_stdout():
    """On stdio, stdout is reserved for JSON-RPC. A server started and closed with no input
    must write nothing to stdout, while its startup log goes to stderr."""
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_servers.health_rules_server"],
        cwd=BACKEND_DIR, input=b"", capture_output=True, timeout=60,
    )
    assert proc.stdout == b""
    assert b"server process starting" in proc.stderr
