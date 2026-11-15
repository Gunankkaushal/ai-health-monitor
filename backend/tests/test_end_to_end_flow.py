"""Complete flow through the real API + real MCP servers:
add reading → retrieve via MCP → evaluate rules via MCP → alert visible → summary → report download."""

from fastapi.testclient import TestClient

from app.main import app


def test_add_reading_to_summary_flow():
    with TestClient(app) as c:
        status = c.get("/api/mcp/status").json()
        assert status["connected"] == 3 and status["total_tools"] == 9

        # 1. Add a reading (SpO2 88 % → should hit spo2_low_critical)
        r = c.post("/api/patients/3/readings", json={"entries": [{"metric": "spo2", "value": 88, "unit": "%"}]})
        assert r.status_code == 201, r.text
        body = r.json()
        reading_id = body["readings"][0]["id"]
        pipeline = body["pipeline"]
        assert body["pipeline_error"] is None

        # 2. The Monitoring Agent retrieved it through the Patient Data MCP server
        assert [x["id"] for x in pipeline["retrieved_via_mcp"]] == [reading_id]

        # 3. The Alert Agent evaluated the rules through the Health Rules MCP server
        new = [a for a in pipeline["new_alerts"] if a["reading_id"] == reading_id]
        assert [a["rule_id"] for a in new] == ["spo2_low_critical"]
        assert new[0]["severity"] == "critical" and new[0]["origin"] == "rule"

        # 4. The alert is displayed by the alerts endpoint
        alerts = c.get("/api/patients/3/alerts").json()
        assert any(a["reading_id"] == reading_id and a["rule_id"] == "spo2_low_critical" for a in alerts)

        # 5. Summary (Reports MCP server) includes it
        s = c.post("/api/patients/3/summary", json={"days": 7}).json()
        assert s["mode"] == "demo"
        assert "Oxygen saturation at or below 92%" in s["summary"]["markdown"]
        assert s["summary"]["alert_counts"]["critical"] >= 1

        # 6. Downloadable report
        rep = c.get("/api/patients/3/report?format=markdown&days=7")
        assert rep.status_code == 200
        assert "attachment" in rep.headers["content-disposition"]
        assert "88 %" in rep.text

        # Activity log shows which agents ran which tools, tied to one run id
        acts = [a for a in c.get("/api/activity?patient_id=3&limit=100").json() if a["run_id"] == pipeline["run_id"]]
        pairs = {(a["agent"], a["tool"], a["status"]) for a in acts}
        assert ("Monitoring Agent", "get_recent_readings", "ok") in pairs
        assert ("Alert Agent", "evaluate_readings", "ok") in pairs

        # Re-evaluation is idempotent (no duplicate alert)
        again = c.post("/api/patients/3/simulate", json={"scenario": "normal"}).json()
        assert not any(a["reading_id"] == reading_id for a in again["pipeline"]["new_alerts"])
        assert again["synthetic"] is True and all(x["source"] == "simulator" for x in again["readings"])


def test_validation_errors_are_readable():
    with TestClient(app) as c:
        r = c.post("/api/patients/1/readings", json={"entries": [{"metric": "heart_rate", "value": 900}]})
        assert r.status_code == 422 and "plausible range" in r.json()["detail"]
        r = c.post("/api/patients/1/readings", json={"recorded_at": "2999-01-01T00:00:00Z",
                                                      "entries": [{"metric": "heart_rate", "value": 70}]})
        assert r.status_code == 422 and "future" in r.json()["detail"]
        r = c.post("/api/patients", json={"name": "  ", "age": 30})
        assert r.status_code == 422


def test_chat_demo_mode_is_labeled_and_routed():
    with TestClient(app) as c:
        cfg = c.get("/api/config").json()
        assert cfg["mode"] == "demo" and cfg["groq_key_configured"] is False

        r = c.post("/api/patients/2/chat", json={"message": "Summarize the alerts for this week"}).json()
        assert r["mode"] == "demo" and "deterministic" in r["mode_label"].lower()
        assert r["routed_to"] == ["Alert Agent", "Summary Agent"]

        r = c.post("/api/patients/2/chat", json={"message": "Do I have hypertension? What medication should I take?"}).json()
        assert r["safety_note"] is True
        assert "can't diagnose" in r["answer"]
