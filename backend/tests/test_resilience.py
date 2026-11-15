"""Resilience lab, end to end: real API + real MCP servers + real fault injection.

Covers every NaviStorm screen's behaviour: discovery and applicability, policy refusals (never
clamped), approvals and segregation of duties, live runs with watchdog rollback, findings and
scores, guardrails before/after, kill switch, abort, suites and the CI gate, hash-chained audit.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from health_core.db import get_engine

AUTHOR = {"X-Actor": "morgan.author"}
SRE = {"X-Actor": "devon.sre"}
OWNER = {"X-Actor": "kai.owner"}
ADMIN = {"X-Actor": "jordan.admin"}
ADMIN2 = {"X-Actor": "taylor.admin"}
VIEWER = {"X-Actor": "riley.viewer"}
AUDITOR = {"X-Actor": "casey.auditor"}

FAST = {"baseline_iterations": 2, "fault_iterations": 4, "recovery_iterations": 2, "duration_s": 60}


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client
        # leave tenant switches as we found them
        client.put("/api/resilience/guardrails", headers=ADMIN,
                   json={"retries": True, "max_attempts": 3, "output_validation": True, "reason": "test teardown"})
        client.post("/api/resilience/killswitch/release", headers=ADMIN, json={"reason": "test teardown"})


def exp(c, headers=AUTHOR, **kw):
    body = {"name": "test experiment", "journey_id": "ingest", "edge_key": "monitoring:get_recent_readings",
            "fault_id": "fault.timeout", "share_pct": 50, "selection": "every_nth", "patient_scope": [1], **FAST, **kw}
    return c.post("/api/resilience/experiments", json=body, headers=headers)


def wait(c, run_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/resilience/runs/{run_id}").json()
        if r["status"] not in ("queued", "running"):
            return r
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish: {r['status']} {r['phase']}")


def run(c, exp_id, headers=AUTHOR, **body):
    r = c.post(f"/api/resilience/experiments/{exp_id}/runs", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return wait(c, r.json()["id"])


def guardrails(c, retries=True, validation=True):
    r = c.put("/api/resilience/guardrails", headers=ADMIN,
              json={"retries": retries, "max_attempts": 3, "output_validation": validation, "reason": "test"})
    assert r.status_code == 200, r.text


@contextmanager
def env(monkeypatch, name):
    monkeypatch.setenv("RESILIENCE_ENV", name)
    yield


def no_experiment_data(c):
    for pid in (1, 2, 3):
        assert not [x for x in c.get(f"/api/patients/{pid}/readings?days=2").json() if x["source"] == "experiment"]


# ---------------------------------------------------------------------------- discovery
def test_topology_edges_come_from_allow_lists_and_manifest(c):
    t = c.get("/api/resilience/topology?rescan=true").json()
    edges = {e["key"]: e for e in t["edges"]}
    assert len(edges) == 17  # 13 agent→tool edges + 4 agent→model edges
    ev = edges["alert:evaluate_readings"]
    assert ev["direction"] == "write" and ev["idempotent"] is True and "manifest" in ev["provenance"]["direction"]
    assert edges["monitoring:get_recent_readings"]["response"] == "collection"
    assert edges["monitoring:get_recent_readings"]["timestamped"] is True
    assert edges["summary:export_health_report"]["response"] == "object"
    assert all(e["journeys"] for e in edges.values()), "every edge belongs to a journey"
    from app.resilience.journeys import JOURNEYS, journey_edges
    for j in JOURNEYS:  # declared journeys may only reference edges that discovery actually finds
        assert set(journey_edges(j)) <= set(edges), j["id"]
        assert set(j["critical_edges"]) <= set(journey_edges(j)), j["id"]
    assert edges["monitoring:llm"]["connected"] is False  # rule-based mode: model gateway inactive


def test_applicability_resolution_is_explained(c):
    d = c.get("/api/resilience/edges/summary:export_health_report").json()
    by = {f["fault_id"]: f for f in d["faults"]}
    assert by["fault.empty"]["status"] == "not_applicable" and "not a collection" in by["fault.empty"]["reason"]
    assert by["fault.write_timeout"]["status"] == "not_applicable"
    assert by["fault.timeout"]["status"] == "eligible"
    m = {f["fault_id"]: f for f in c.get("/api/resilience/edges/monitoring:llm").json()["faults"]}
    assert m["fault.model_error"]["status"] == "unavailable" and "rule-based" in m["fault.model_error"]["reason"]
    i = c.get("/api/resilience/interception").json()
    assert {p["key"]: p["status"] for p in i["points"]}["mcp_proxy"] == "connected"
    assert len(i["resolution_order"]) == 6


def test_override_excludes_fault_and_is_audited(c):
    r = c.post("/api/resilience/edges/summary:get_patient_alerts/overrides", headers=AUTHOR,
               json={"fault_id": "fault.latency", "reason": "not allowed"})
    assert r.status_code == 403
    r = c.post("/api/resilience/edges/summary:get_patient_alerts/overrides", headers=ADMIN,
               json={"fault_id": "fault.latency", "reason": "Known slow path, tracked elsewhere", "review_by": "2027-01-31"})
    assert r.status_code == 201
    f = {x["fault_id"]: x for x in c.get("/api/resilience/edges/summary:get_patient_alerts").json()["faults"]}
    assert f["fault.latency"]["status"] == "excluded"
    assert c.get("/api/resilience/audit?event=edge.override").json()["events"][0]["reason"].startswith("Known slow")
    c.request("DELETE", f"/api/resilience/overrides/{r.json()['id']}", headers=ADMIN, json={"reason": "done"})


# ---------------------------------------------------------------------------- policy
def test_policy_refuses_with_rule_named_never_clamps(c, monkeypatch):
    with env(monkeypatch, "staging"):
        r = exp(c, share_pct=80, environment="staging")
        assert r.status_code == 422
        rules = {v["rule"] for v in r.json()["detail"]["violations"]}
        assert "max_share_pct" in rules
        pid = c.post("/api/patients", json={"name": "Protected Person", "age": 40}).json()["id"]
        r = exp(c, patient_scope=[pid], environment="staging")
        assert "protected_patients" in {v["rule"] for v in r.json()["detail"]["violations"]}
        c.delete(f"/api/patients/{pid}")
    with env(monkeypatch, "production"):
        r = exp(c, edge_key="alert:evaluate_readings", fault_id="fault.write_timeout", share_pct=5, environment="production")
        rules = {v["rule"] for v in r.json()["detail"]["violations"]}
        assert {"write_path", "applicability"} <= rules
    # environment mismatch: a dev deployment refuses an experiment written for production
    monkeypatch.setenv("RESILIENCE_ENV", "dev")
    r = exp(c, environment="production")
    assert any(v["rule"] == "environment" for v in r.json()["detail"]["violations"])


def test_policy_change_needs_two_admins(c):
    cur = c.get("/api/resilience/policy").json()["current"]
    body = cur["body"] | {"blackout_windows": []}
    assert c.post("/api/resilience/policy/drafts", headers=SRE, json={"body": body, "reason": "x y"}).status_code == 403
    d = c.post("/api/resilience/policy/drafts", headers=ADMIN, json={"body": body, "reason": "Remove blackout for tests"}).json()
    assert c.post(f"/api/resilience/policy/drafts/{d['id']}/approve", headers=ADMIN).status_code == 403
    pub = c.post(f"/api/resilience/policy/drafts/{d['id']}/approve", headers=ADMIN2).json()
    assert pub["version"] == cur["version"] + 1 and pub["body"]["blackout_windows"] == []
    bad = c.post("/api/resilience/policy/drafts", headers=ADMIN, json={"body": {"ceilings": {}}, "reason": "broken"})
    assert bad.status_code == 422


# ---------------------------------------------------------------------------- approvals
def test_approvals_segregation_and_expiry_on_edit(c):
    e = exp(c, edge_key="alert:evaluate_readings", fault_id="fault.write_timeout").json()
    r = c.post(f"/api/resilience/experiments/{e['id']}/runs", json={}, headers=AUTHOR)
    assert r.status_code == 409 and "SRE approver" in r.json()["detail"]["message"]
    assert c.post(f"/api/resilience/experiments/{e['id']}/approvals", json={}, headers=AUTHOR).status_code == 403
    assert c.post(f"/api/resilience/experiments/{e['id']}/approvals", json={}, headers=VIEWER).status_code == 403
    st = c.post(f"/api/resilience/experiments/{e['id']}/approvals", json={"comment": "ok"}, headers=SRE).json()
    assert st["satisfied"] is True
    # editing the experiment invalidates approvals
    body = {k: e[k] for k in ("name", "journey_id", "edge_key", "fault_id", "share_pct", "selection", "patient_scope",
                              "duration_s", "baseline_iterations", "fault_iterations", "recovery_iterations")}
    upd = c.put(f"/api/resilience/experiments/{e['id']}", json=body | {"share_pct": 40}, headers=AUTHOR).json()
    assert upd["version"] == 2
    assert c.get(f"/api/resilience/experiments/{e['id']}").json()["approval"]["satisfied"] is False
    # break glass: only tenant admins, always audited
    r = c.post(f"/api/resilience/experiments/{e['id']}/runs", json={"break_glass_reason": "incident"}, headers=AUTHOR)
    assert r.status_code == 409
    r = c.post(f"/api/resilience/experiments/{e['id']}/runs", json={"break_glass_reason": "live incident INC-1"}, headers=ADMIN)
    assert r.status_code == 201
    wait(c, r.json()["id"])
    assert c.get("/api/resilience/audit?event=run.break_glass").json()["events"]


# ---------------------------------------------------------------------------- runs
def test_write_timeout_is_absorbed_by_idempotent_retry(c):
    e = exp(c, edge_key="alert:evaluate_readings", fault_id="fault.write_timeout").json()
    c.post(f"/api/resilience/experiments/{e['id']}/approvals", json={}, headers=SRE)
    r = run(c, e["id"])
    assert r["verdict"] == "held" and r["status"] == "completed", r["reason"]
    assert r["score"] == 100
    fs = r["results"]["fault_stats"]
    assert fs["injected"] >= 2 and fs["retries"] == fs["injected"] and fs["contained"]
    held = {x["metric"]: x for x in r["results"]["steady_state"]}
    assert held["detection_rate"]["fault"] == 100.0  # lost acknowledgement no longer hides the alert
    assert r["results"]["cleaned_up_readings"] > 0
    no_experiment_data(c)


def test_guardrails_off_rolls_back_and_raises_findings_then_fix_verifies(c):
    e = exp(c, share_pct=100).json()
    guardrails(c, retries=False)
    try:
        r = run(c, e["id"])
    finally:
        guardrails(c, retries=True)
    assert r["status"] == "rolled_back" and r["verdict"] == "rejected"
    assert r["score"] <= 49 and "Steady state breached" in r["reason"]
    assert any(ev["msg"].startswith("Watchdog rollback") for ev in r["events"])
    kinds = {f["kind"]: f for f in r["findings"]}
    assert kinds["success_rate"]["severity"] == "high" and "retry" in kinds["success_rate"]["guardrail"].lower()
    assert r["results"]["cascade"][0]["hop"] == "targeted"
    no_experiment_data(c)

    # Same edge, every other call failing: with bounded retries the journey holds and the finding is verified.
    e2 = exp(c, share_pct=50).json()
    r2 = run(c, e2["id"])
    assert r2["verdict"] == "held", r2["reason"]
    f = c.get(f"/api/resilience/findings?status=resolved").json()
    assert any(x["id"] == kinds["success_rate"]["id"] and "verified by run" in x["reference"] for x in f)


def test_malformed_output_crash_without_contracts_and_graceful_with_them(c):
    body = dict(journey_id="assistant", edge_key="monitoring:calculate_trends", fault_id="fault.malformed",
                params={"mode": "wrong_shape"}, share_pct=100)
    e = exp(c, **body).json()
    guardrails(c, validation=False)
    try:
        r = run(c, e["id"])
    finally:
        guardrails(c, validation=True)
    assert r["status"] == "rolled_back"
    assert any(x["kind"] == "unhandled_errors" for x in r["findings"])
    assert any(it["error"] and it["error"].startswith("UNHANDLED") for it in r["results"]["iterations"]["fault"])

    r = run(c, e["id"])
    ss = {x["metric"]: x for x in r["results"]["steady_state"]}
    assert ss["unhandled_errors"]["fault"] == 0  # degraded section, no 500
    assert r["verdict"] == "rejected" and ss["complete_answer_rate"]["held_during_fault"] is False


def test_stale_data_is_flagged_by_the_pipeline(c):
    e = exp(c, fault_id="fault.stale", params={"lag_minutes": 30}, share_pct=100).json()
    r = run(c, e["id"])
    ss = {x["metric"]: x for x in r["results"]["steady_state"]}
    assert ss["retrieval_rate"]["fault"] == 0 and ss["detection_rate"]["fault"] == 100
    assert r["verdict"] == "rejected" and r["status"] == "completed"  # retrieval is not an abort metric
    assert any("warnings" in (it["note"] or "") for it in r["results"]["iterations"]["fault"])


def test_unexercised_edge_is_inconclusive_not_passed(c):
    e = exp(c, journey_id="assistant", edge_key="monitoring:get_reading_history", share_pct=100).json()
    r = run(c, e["id"])
    assert r["verdict"] == "inconclusive" and r["score"] is None
    assert [f["kind"] for f in r["findings"]] == ["not_exercised"]


def test_dry_run_needs_no_fault_or_approval(c):
    e = exp(c, edge_key="alert:evaluate_readings", fault_id="fault.write_timeout").json()  # would need SRE approval
    r = run(c, e["id"], dry_run=True)
    assert r["verdict"] == "dry_run_passed" and r["score"] is None


def test_abort_by_viewer_and_kill_switch(c):
    e = exp(c, fault_id="fault.latency", params={"delay_ms": 1500, "jitter_ms": 0}, fault_iterations=20, share_pct=100).json()
    rid = c.post(f"/api/resilience/experiments/{e['id']}/runs", json={}, headers=AUTHOR).json()["id"]
    time.sleep(2)
    assert c.post(f"/api/resilience/runs/{rid}/abort", json={"reason": "x"}, headers=AUDITOR).status_code == 403
    assert c.post(f"/api/resilience/runs/{rid}/abort", json={"reason": "looks wrong"}, headers=VIEWER).status_code == 200
    r = wait(c, rid)
    assert r["status"] == "aborted" and "riley.viewer" in r["reason"]

    rid = c.post(f"/api/resilience/experiments/{e['id']}/runs", json={}, headers=AUTHOR).json()["id"]
    time.sleep(2)
    ks = c.post("/api/resilience/killswitch/engage", json={"reason": "pager"}, headers=VIEWER).json()
    assert ks["engaged"] is True
    assert wait(c, rid)["status"] == "aborted"
    assert c.post(f"/api/resilience/experiments/{e['id']}/runs", json={}, headers=AUTHOR).status_code == 409
    assert c.post("/api/resilience/killswitch/release", json={"reason": "x"}, headers=VIEWER).status_code == 403
    assert c.post("/api/resilience/killswitch/release", json={"reason": "resolved"}, headers=ADMIN).json()["engaged"] is False
    no_experiment_data(c)


def test_blackout_window_refuses_runs(c):
    cur = c.get("/api/resilience/policy").json()["current"]
    allday = {"name": "Test freeze", "weekdays": list(range(7)), "start": "00:00", "end": "23:59", "scope": "all"}
    d = c.post("/api/resilience/policy/drafts", headers=ADMIN, json={"body": cur["body"] | {"blackout_windows": [allday]}, "reason": "freeze"}).json()
    c.post(f"/api/resilience/policy/drafts/{d['id']}/approve", headers=ADMIN2)
    try:
        e = exp(c).json()
        r = c.post(f"/api/resilience/experiments/{e['id']}/runs", json={}, headers=AUTHOR)
        assert r.status_code == 409 and "Blackout window 'Test freeze'" in r.json()["detail"]["message"]
    finally:
        d = c.post("/api/resilience/policy/drafts", headers=ADMIN, json={"body": cur["body"], "reason": "unfreeze"}).json()
        c.post(f"/api/resilience/policy/drafts/{d['id']}/approve", headers=ADMIN2)


def test_fault_disable_removes_it_everywhere(c):
    assert c.post("/api/resilience/faults/fault.partial/toggle", json={"enabled": False, "reason": "x y z"}, headers=SRE).status_code == 403
    c.post("/api/resilience/faults/fault.partial/toggle", json={"enabled": False, "reason": "Under review"}, headers=ADMIN)
    try:
        r = exp(c, fault_id="fault.partial")
        assert r.status_code == 422 and "disabled" in r.text
    finally:
        c.post("/api/resilience/faults/fault.partial/toggle", json={"enabled": True, "reason": "Reviewed"}, headers=ADMIN)


# ---------------------------------------------------------------------------- suites, scores, CI gate
def test_starter_suite_runs_and_gates_on_journey_score(c):
    out = c.post("/api/resilience/journeys/ingest/starter-suite", headers=AUTHOR).json()
    suite = out["suite"]
    assert {x["edge_key"] for x in out["experiments"]} == {"monitoring:get_recent_readings", "alert:evaluate_readings"}
    # the write-path experiment needs an SRE approval before the suite can run it
    for x in out["experiments"]:
        if x["edge_key"] == "alert:evaluate_readings":
            c.post(f"/api/resilience/experiments/{x['id']}/approvals", json={}, headers=SRE)
    sr = c.post(f"/api/resilience/suites/{suite['id']}/run", json={}, headers={"X-Actor": "ci-pipeline"}).json()
    deadline = time.time() + 240
    while time.time() < deadline:
        sr = c.get(f"/api/resilience/suite-runs/{sr['id']}").json()
        if sr["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert sr["status"] == "completed", sr
    assert len(sr["runs"]) == 2 and all(r["verdict"] == "held" for r in sr["runs"])
    assert sr["gate"] == "passed" and sr["score"] >= 70
    ov = c.get("/api/resilience/overview").json()
    j = {x["id"]: x for x in ov["journeys"]}["ingest"]
    assert j["tested_edges"] == j["testable_edges"] == 2 and j["trend"]
    # Evidence is per journey: ingest runs on a shared edge never count towards the assistant journey.
    a = {x["id"]: x for x in ov["journeys"]}["assistant"]
    assert "monitoring:get_recent_readings" not in a["edge_scores"]
    assert "alert:evaluate_readings" not in a["edge_scores"]


def test_audit_log_is_append_only_and_tamper_evident(c):
    a = c.get("/api/resilience/audit").json()
    assert a["chain"]["intact"] is True and a["chain"]["checked"] >= 10
    seqs = [e["seq"] for e in a["events"]]
    assert seqs == sorted(seqs, reverse=True) and seqs[-1] == 1
    assert c.get("/api/resilience/audit/export?format=csv").text.startswith("seq,occurred_at")
    target = seqs[len(seqs) // 2]
    with get_engine().begin() as conn:
        orig = conn.execute(text("SELECT detail FROM rl_audit WHERE seq=:s"), {"s": target}).scalar()
        conn.execute(text("UPDATE rl_audit SET detail='tampered' WHERE seq=:s"), {"s": target})
    try:
        v = c.get("/api/resilience/audit/verify").json()
        assert v["intact"] is False and v["first_bad_seq"] == target
    finally:
        with get_engine().begin() as conn:
            conn.execute(text("UPDATE rl_audit SET detail=:d WHERE seq=:s"), {"d": orig, "s": target})
    assert c.get("/api/resilience/audit/verify").json()["intact"] is True
    # no write path exists
    assert c.delete("/api/resilience/audit").status_code == 405


def test_unknown_actor_is_rejected_and_anonymous_is_read_only(c):
    assert c.get("/api/resilience/status", headers={"X-Actor": "mallory"}).status_code == 401
    assert exp(c, headers={}).status_code == 403
