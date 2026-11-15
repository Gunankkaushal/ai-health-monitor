"""Patient isolation: repository scoping, scoped toolbox, agent tool restrictions, API ownership checks."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents import AGENTS
from app.mcp_client import ToolArgumentError, ToolCallResult, ToolInfo
from app.tooling import PatientIsolationError, ScopedToolbox, ToolNotAllowedError, model_facing_schema
from health_core import repository as repo
from health_core.db import session_scope
from health_core.models import Alert, Reading


def test_repository_queries_are_scoped():
    for pid in (1, 2, 3):
        assert {r["patient_id"] for r in repo.get_reading_history(pid, days=60)} == {pid}
        assert {a["patient_id"] for a in repo.get_patient_alerts(pid, limit=500)} <= {pid}


def test_model_facing_schema_hides_patient_id():
    schema = {"type": "object", "properties": {"patient_id": {"type": "integer"}, "days": {"type": "integer"}},
              "required": ["patient_id"]}
    out = model_facing_schema(schema)
    assert "patient_id" not in out["properties"] and "required" not in out
    assert out["additionalProperties"] is False
    assert "patient_id" in schema["properties"]  # original untouched


async def test_scoped_toolbox_over_real_mcp(manager):
    box = ScopedToolbox(manager, 2, "Monitoring Agent", AGENTS["monitoring"].allowed_tools, "test")
    data = await box.call("get_recent_readings", {"limit": 50})
    assert data["patient_id"] == 2
    assert {r["patient_id"] for r in data["readings"]} == {2}

    # The model may not pick the patient.
    with pytest.raises(ToolArgumentError, match="set by the system"):
        await box.call("get_recent_readings", {"patient_id": 1})
    # Undeclared arguments (e.g. SQL or paths) are rejected.
    with pytest.raises(ToolArgumentError):
        await box.call("get_recent_readings", {"sql": "SELECT * FROM readings"})

    for spec in box.groq_tool_specs():
        assert "patient_id" not in spec["function"]["parameters"]["properties"]


async def test_agents_have_restricted_tools(manager):
    wellness = ScopedToolbox(manager, 1, "Wellness Agent", AGENTS["wellness"].allowed_tools, "test")
    with pytest.raises(ToolNotAllowedError):
        await wellness.call("evaluate_readings", {"days": 7})
    summary = ScopedToolbox(manager, 1, "Summary Agent", AGENTS["summary"].allowed_tools, "test")
    with pytest.raises(ToolNotAllowedError):
        await summary.call("get_reading_history", {})
    assert {t["function"]["name"] for t in wellness.groq_tool_specs()} == set(AGENTS["wellness"].allowed_tools)

    # Every agent's tool set is distinct and a subset of the discovered tools.
    discovered = set(manager.all_tools())
    sets = [frozenset(a.allowed_tools) for a in AGENTS.values()]
    assert len(set(sets)) == len(sets)
    assert all(s <= discovered for s in sets)


class _ForeignManager:
    """Fake manager whose server 'leaks' another patient's record."""

    def all_tools(self):
        return {"get_recent_readings": self.tool_info("get_recent_readings")}

    def tool_info(self, name):
        return ToolInfo(name, "", {"type": "object", "properties": {"patient_id": {"type": "integer"},
                                                                    "limit": {"type": "integer"}}}, "patient_data")

    async def call_tool(self, name, args, timeout=None):
        return ToolCallResult("patient_data", name, {"patient_id": args["patient_id"],
                                                     "readings": [{"patient_id": 999, "value": 1}]}, 1)


async def test_foreign_patient_data_in_result_is_discarded():
    box = ScopedToolbox(_ForeignManager(), 1, "Monitoring Agent", ("get_recent_readings",), "test")
    with pytest.raises(PatientIsolationError):
        await box.call("get_recent_readings", {"limit": 1})


def test_api_enforces_ownership():
    from app.main import app

    with TestClient(app) as c:
        with session_scope() as s:
            other_reading = s.scalar(select(Reading.id).where(Reading.patient_id == 2).limit(1))
            other_alert = s.scalar(select(Alert.id).where(Alert.patient_id == 2).limit(1))
        assert c.delete(f"/api/patients/1/readings/{other_reading}").status_code == 404
        assert c.post(f"/api/patients/1/alerts/{other_alert}/acknowledge").status_code == 404
        assert c.get("/api/patients/424242/readings").status_code == 404
        assert all(r["patient_id"] == 1 for r in c.get("/api/patients/1/readings?days=60").json())
        assert all(a["patient_id"] == 1 for a in c.get("/api/patients/1/alerts").json())
