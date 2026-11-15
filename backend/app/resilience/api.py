"""REST API for the resilience lab: /api/resilience/*

Every state-changing call names its actor (X-Actor header), is permission-checked, and is written to
the hash-chained audit log.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from health_core.db import session_scope
from health_core.models import Patient, utcnow
from health_core.resilience_models import (
    Approval,
    EdgeOverride,
    Experiment,
    ExperimentRun,
    Finding,
    PolicyVersion,
    Suite,
    SuiteRun,
)

from ..agents import AGENTS
from ..llm import mode_info
from . import analysis, audit, catalog, roles
from . import policy as policy_mod
from .injector import CONTROLS, DEFAULT_GUARDRAILS, FAULTS, LIBRARY
from .journeys import JOURNEY_BY_ID, JOURNEYS, METRICS, journey_edges
from .roles import Actor, actor_from_request, require
from .runner import RunEngine, approval_status, approvals_for, experiment_dict, run_dict

router = APIRouter(prefix="/api/resilience", tags=["resilience"])


def engine(request: Request) -> RunEngine:
    return request.app.state.resilience


def manager(request: Request):
    return request.app.state.mcp


def actor(request: Request) -> Actor:
    return actor_from_request(request)


# ---------------------------------------------------------------------------- inputs
class SteadyCheck(BaseModel):
    metric: str
    op: Literal[">=", "<="]
    threshold: float
    abort: bool = False


class ExperimentIn(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    journey_id: str
    hypothesis: str = Field(default="", max_length=2000)
    edge_key: str
    fault_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    share_pct: int = Field(default=30, ge=1, le=100)
    selection: Literal["random", "every_nth", "burst"] = "random"
    duration_s: int = Field(default=60, ge=5, le=3600)
    patient_scope: list[int] = Field(default_factory=list, max_length=50)
    environment: str | None = None
    steady_state: list[SteadyCheck] = Field(default_factory=list)
    baseline_iterations: int = Field(default=3, ge=1, le=20)
    fault_iterations: int = Field(default=8, ge=1, le=50)
    recovery_iterations: int = Field(default=3, ge=1, le=20)
    change_ref: str = Field(default="", max_length=40)


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=1000)


class RunIn(BaseModel):
    dry_run: bool = False
    break_glass_reason: str = Field(default="", max_length=1000)


class ApprovalIn(BaseModel):
    comment: str = Field(default="", max_length=1000)


class ToggleIn(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=1000)


class OverrideIn(BaseModel):
    fault_id: str
    reason: str = Field(min_length=3, max_length=1000)
    review_by: str = Field(default="", max_length=20)


class PolicyDraftIn(BaseModel):
    body: dict[str, Any]
    reason: str = Field(min_length=3, max_length=1000)


class GuardrailsIn(BaseModel):
    retries: bool
    max_attempts: int = Field(ge=1, le=5)
    output_validation: bool
    reason: str = Field(min_length=3, max_length=1000)


class SuiteIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    journey_id: str
    experiment_ids: list[int] = Field(min_length=1, max_length=40)
    gate_mode: Literal["block", "warn", "report"] = "block"
    gate_threshold: int = Field(default=70, ge=0, le=100)


class SuiteRunIn(BaseModel):
    wait_for_window: bool = False


# ---------------------------------------------------------------------------- status & overview
@router.get("/status")
def status(request: Request, who: Actor = Depends(actor)):
    eng = engine(request)
    pol = policy_mod.current()
    return {"environment": policy_mod.environment(), "policy_version": pol["version"],
            "kill_switch": CONTROLS.kill_switch, "guardrails": CONTROLS.guardrails, "active_runs": eng.active_runs(),
            "model_mode": mode_info()["mode"], "library_version": LIBRARY["version"], "actor": who.as_dict()}


@router.get("/overview")
async def overview(request: Request):
    mgr = manager(request)
    await catalog.sample_shapes(mgr)
    es = catalog.edges(mgr)
    scores = analysis.journey_scores(es)
    by_key = {e["key"]: e for e in es}
    with session_scope() as s:
        findings = [analysis.finding_dict(f) for f in s.scalars(select(Finding).where(Finding.status != "resolved"))]
        runs = [run_dict(r, full=False) for r in s.scalars(select(ExperimentRun).order_by(ExperimentRun.id.desc()).limit(8))]
        suites = [{"id": x.id, "name": x.name, "journey_id": x.journey_id} for x in s.scalars(select(Suite))]
    tested = {k for j in scores.values() for k in j["edge_scores"]}
    # Critical edges are critical to a journey, so "untested" is judged per journey.
    uc: list[dict[str, Any]] = []
    for j in JOURNEYS:
        for k in j["critical_edges"]:
            if k in by_key and k not in scores[j["id"]]["edge_scores"]                     and (by_key[k]["kind"] == "mcp.tool" or by_key[k]["connected"]):
                uc.append({"edge": by_key[k], "journey_ids": [j["id"]]})
    journeys = []
    for j in JOURNEYS:
        jf = [f for f in findings if f["journey_id"] == j["id"]]
        journeys.append({"id": j["id"], "name": j["name"], "slo": j["slo"], **scores[j["id"]],
                         "open_findings": {sev: sum(f["severity"] == sev for f in jf) for sev in ("high", "medium", "low")}})
    testable = [e for e in es if e["kind"] == "mcp.tool" or e["connected"]]
    return {
        "journeys": journeys,
        "coverage": {"edges_tested": len(tested & {e["key"] for e in testable}), "edges_testable": len(testable),
                     "edges_total": len(es), "critical_untested": len(uc),
                     "agents_exercised": len({by_key[k]["agent_key"] for k in tested if k in by_key}), "agents_total": 4},
        "untested_critical": uc,
        "open_findings": {sev: sum(f["severity"] == sev for f in findings) for sev in ("high", "medium", "low")},
        "recent_runs": runs, "suites": suites, "score_formula": analysis.SCORE_FORMULA,
    }


# ---------------------------------------------------------------------------- topology & journeys
@router.get("/topology")
async def topology(request: Request, rescan: bool = False):
    mgr = manager(request)
    await catalog.sample_shapes(mgr, force=rescan)
    es = catalog.edges(mgr)
    stats = catalog.edge_stats()
    tested = {k for j in analysis.journey_scores(es).values() for k in j["edge_scores"]}
    for e in es:
        st = stats.get((e["agent"], e["tool"]))
        e["stats"] = st or {"calls": 0, "errors": 0, "p95_ms": None}
        e["tested"] = e["key"] in tested
        e["retry_policy"] = (f"bounded · {CONTROLS.guardrails['max_attempts']} attempts, backoff + jitter"
                             if CONTROLS.guardrails.get("retries") and e["idempotent"] else
                             "none (not idempotent)" if not e["idempotent"] else "disabled by guardrail setting")
        e["risk_rank"] = round(catalog.risk_rank(e, e["tested"], st), 2)
    servers = [{"key": s["key"], "name": s["name"], "status": s["status"], "tools": [t["name"] for t in s["tools"]]}
               for s in mgr.status()]
    suggestion = max((e for e in es if not e["tested"] and (e["kind"] == "mcp.tool" or e["connected"])),
                     key=lambda e: e["risk_rank"], default=None)
    return {"edges": es, "servers": servers,
            "agents": [{"key": k, "name": a.name} for k, a in AGENTS.items()],
            "model": {"active": catalog.model_gateway_active(), "model": mode_info()["model"]},
            "synced_at": utcnow().isoformat(), "suggestion": suggestion,
            "suggestion_basis": "Heuristic ranking: critical path, never tested, write path, observed error rate."}


@router.get("/journeys")
async def journeys(request: Request):
    mgr = manager(request)
    await catalog.sample_shapes(mgr)
    es = {e["key"]: e for e in catalog.edges(mgr)}
    scores = analysis.journey_scores(list(es.values()))
    pol, env = catalog.policy_env()
    with session_scope() as s:
        runs = list(s.scalars(select(ExperimentRun).where(ExperimentRun.dry_run.is_(False), ExperimentRun.verdict.is_not(None))
                              .order_by(ExperimentRun.id.desc())))
    latest: dict[tuple[str, str], ExperimentRun] = {}  # (journey, edge) -> newest run
    for r in runs:
        latest.setdefault((r.spec.get("journey_id"), r.spec.get("edge_key")), r)
    out = []
    for j in JOURNEYS:
        stages = []
        for st in j["stages"]:
            rows = []
            for k in st["edges"]:
                e = es.get(k)
                if not e:
                    continue
                r = latest.get((j["id"], k))
                faults = [f for f in catalog.edge_faults(e, pol, env) if f["status"] == "eligible"]
                rows.append({**e, "eligible_faults": [f["name"] for f in faults],
                             "coverage": (f"run-{r.id:04d} · {r.verdict}" if r else
                                          "not reachable — model inactive" if e["kind"] == "model.inference" and not e["connected"]
                                          else "never tested"),
                             "last_run_id": r.id if r else None, "last_verdict": r.verdict if r else None,
                             "edge_score": scores[j["id"]]["edge_scores"].get(k)})
            stages.append({**st, "edges": rows})
        sc = scores[j["id"]]
        never = [x for st in stages for x in st["edges"] if x["coverage"] == "never tested"]
        out.append({**{k: v for k, v in j.items() if k != "stages"}, "stages": stages, **sc,
                    "never_tested": len(never), "never_tested_critical": sum(1 for x in never if x["key"] in j["critical_edges"]),
                    "write_edges": sum(1 for st in stages for x in st["edges"] if x["direction"] == "write")})
    return {"journeys": out, "metrics": METRICS, "score_formula": analysis.SCORE_FORMULA}


# ---------------------------------------------------------------------------- faults, interception, overrides
@router.get("/faults")
async def faults(request: Request):
    mgr = manager(request)
    await catalog.sample_shapes(mgr)
    es = catalog.edges(mgr)
    pol, env = catalog.policy_env()
    with session_scope() as s:
        exps = list(s.scalars(select(Experiment).where(Experiment.archived.is_(False))))
        runs = list(s.scalars(select(ExperimentRun).where(ExperimentRun.dry_run.is_(False)).order_by(ExperimentRun.id.desc())))
    out = []
    for f in FAULTS.values():
        app = [catalog.applicability(e, f, pol, env) for e in es]
        last = next((r for r in runs if r.spec.get("fault_id") == f["id"]), None)
        used = [e for e in exps if e.fault_id == f["id"]]
        out.append({**f, "enabled": f["id"] not in CONTROLS.disabled_faults,
                    "disabled_reason": CONTROLS.disabled_faults.get(f["id"]),
                    "eligible_edges": sum(a["status"] == "eligible" for a in app),
                    "usage": {"experiments": len(used), "journeys": len({e.journey_id for e in used}),
                              "last_run": f"run-{last.id:04d}" if last else None}})
    return {"version": LIBRARY["version"], "faults": out}


@router.post("/faults/{fault_id}/toggle")
def toggle_fault(fault_id: str, body: ToggleIn, who: Actor = Depends(actor)):
    require(who, "admin")
    if fault_id not in FAULTS:
        raise HTTPException(404, "Unknown fault")
    disabled = dict(CONTROLS.disabled_faults)
    before = {"enabled": fault_id not in disabled}
    if body.enabled:
        disabled.pop(fault_id, None)
    else:
        disabled[fault_id] = body.reason
    CONTROLS.save("disabled_faults", disabled, who.name)
    with session_scope() as s:
        affected = [x.name for x in s.scalars(select(Suite))
                    if any(e.fault_id == fault_id for e in s.scalars(select(Experiment).where(Experiment.id.in_(x.experiment_ids))))]
    audit.append(who.name, "fault.enabled" if body.enabled else "fault.disabled", fault_id,
                 f"{len(affected)} suite(s) flagged for review: {', '.join(affected) or 'none'}",
                 before=before, after={"enabled": body.enabled}, reason=body.reason)
    return {"fault_id": fault_id, "enabled": body.enabled, "suites_flagged": affected}


@router.get("/edges/{edge_key}")
async def edge_detail(edge_key: str, request: Request):
    mgr = manager(request)
    await catalog.sample_shapes(mgr)
    e = catalog.edge_by_key(mgr, edge_key)
    if not e:
        raise HTTPException(404, "Unknown edge")
    pol, env = catalog.policy_env()
    fl = catalog.edge_faults(e, pol, env)
    counts = {st: sum(f["status"] == st for f in fl) for st in ("eligible", "not_applicable", "unavailable", "disabled", "blocked", "excluded")}
    return {"edge": e, "faults": fl, "counts": counts, "overrides": [o for o in catalog.overrides() if o["edge_key"] == edge_key]}


@router.get("/interception")
async def interception(request: Request):
    mgr = manager(request)
    await catalog.sample_shapes(mgr)
    return catalog.interception(mgr) | {"overrides": catalog.overrides()}


@router.post("/edges/{edge_key}/overrides", status_code=201)
def add_override(edge_key: str, body: OverrideIn, request: Request, who: Actor = Depends(actor)):
    require(who, "admin")
    if catalog.edge_by_key(manager(request), edge_key) is None:
        raise HTTPException(404, "Unknown edge")
    if body.fault_id != "*" and body.fault_id not in FAULTS:
        raise HTTPException(422, "Unknown fault")
    with session_scope() as s:
        o = EdgeOverride(edge_key=edge_key, fault_id=body.fault_id, reason=body.reason, review_by=body.review_by,
                         created_by=who.name)
        s.add(o)
        s.flush()
        oid = o.id
    audit.append(who.name, "edge.override", edge_key, f"Excluded {body.fault_id}" +
                 (f" · review {body.review_by}" if body.review_by else ""), reason=body.reason,
                 after={"fault_id": body.fault_id})
    return {"id": oid}


@router.delete("/overrides/{override_id}", status_code=204)
def remove_override(override_id: int, body: ReasonIn | None = None, who: Actor = Depends(actor)):
    require(who, "admin")
    with session_scope() as s:
        o = s.get(EdgeOverride, override_id)
        if o is None:
            raise HTTPException(404, "Override not found")
        before = {"edge_key": o.edge_key, "fault_id": o.fault_id, "reason": o.reason}
        s.delete(o)
    audit.append(who.name, "edge.override_removed", before["edge_key"], f"Re-included {before['fault_id']}",
                 before=before, reason=body.reason if body else "")
    return Response(status_code=204)


# ---------------------------------------------------------------------------- policy
@router.get("/policy")
def get_policy():
    cur = policy_mod.current()
    with session_scope() as s:
        history = [policy_mod.version_dict(v) for v in s.scalars(select(PolicyVersion).order_by(PolicyVersion.id.desc()).limit(20))]
    return {"current": cur, "environment": policy_mod.environment(), "environments": list(policy_mod.ENVIRONMENTS),
            "labels": {k: {"label": a, "enforced_by": b} for k, (a, b) in policy_mod.CEILING_LABELS.items()},
            "history": history, "default": policy_mod.DEFAULT_POLICY,
            "active_blackout": policy_mod.active_blackout(cur["body"], {"patient_data", "health_rules", "reports"})}


@router.post("/policy/drafts", status_code=201)
def propose_policy(body: PolicyDraftIn, who: Actor = Depends(actor)):
    require(who, "edit_policy")
    errors = policy_mod.validate_body(body.body)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    cur = policy_mod.current()
    with session_scope() as s:
        d = PolicyVersion(status="draft", body=body.body, proposed_by=who.name, reason=body.reason)
        s.add(d)
        s.flush()
        did = d.id
    audit.append(who.name, "policy.proposed", f"policy draft {did}", f"based on v{cur['version']}",
                 before=cur["body"], after=body.body, reason=body.reason)
    return {"id": did, "status": "draft", "needs": "approval by a second tenant admin"}


@router.post("/policy/drafts/{draft_id}/approve")
def approve_policy(draft_id: int, who: Actor = Depends(actor)):
    require(who, "edit_policy")
    cur = policy_mod.current()
    with session_scope() as s:
        d = s.get(PolicyVersion, draft_id)
        if d is None or d.status != "draft":
            raise HTTPException(404, "Draft not found")
        if d.proposed_by == who.name:
            raise HTTPException(403, "The proposer cannot approve their own policy change (two tenant admins required)")
        for old in s.scalars(select(PolicyVersion).where(PolicyVersion.status == "published")):
            old.status = "superseded"
        d.status, d.version, d.approved_by, d.published_at = "published", cur["version"] + 1, who.name, utcnow()
        version, proposer, body = d.version, d.proposed_by, d.body
    audit.append(f"{proposer} + {who.name}", "policy.published", f"policy v{version}", f"v{cur['version']} → v{version}",
                 before=cur["body"], after=body)
    return policy_mod.current()


@router.post("/policy/drafts/{draft_id}/reject")
def reject_policy(draft_id: int, body: ReasonIn, who: Actor = Depends(actor)):
    require(who, "edit_policy")
    with session_scope() as s:
        d = s.get(PolicyVersion, draft_id)
        if d is None or d.status != "draft":
            raise HTTPException(404, "Draft not found")
        d.status = "rejected"
    audit.append(who.name, "policy.rejected", f"policy draft {draft_id}", reason=body.reason)
    return {"id": draft_id, "status": "rejected"}


# ---------------------------------------------------------------------------- roles, guardrails, kill switch
@router.get("/roles")
def get_roles(who: Actor = Depends(actor)):
    return roles.describe() | {"you": who.as_dict()}


@router.get("/guardrails")
def get_guardrails():
    return {"current": CONTROLS.guardrails, "default": DEFAULT_GUARDRAILS}


@router.put("/guardrails")
def set_guardrails(body: GuardrailsIn, who: Actor = Depends(actor)):
    require(who, "admin")
    before = dict(CONTROLS.guardrails)
    after = {"retries": body.retries, "max_attempts": body.max_attempts, "output_validation": body.output_validation}
    CONTROLS.save("guardrails", after, who.name)
    audit.append(who.name, "guardrails.changed", "tenant", before=before, after=after, reason=body.reason)
    return after


@router.get("/killswitch")
def get_killswitch():
    return CONTROLS.kill_switch


@router.post("/killswitch/engage")
async def engage_killswitch(body: ReasonIn, request: Request, who: Actor = Depends(actor)):
    require(who, "abort")  # deliberately available to every role that can abort — stopping damage is never gated
    return engine(request).engage_kill_switch(who.name, body.reason or "no reason given")


@router.post("/killswitch/release")
def release_killswitch(body: ReasonIn, who: Actor = Depends(actor)):
    require(who, "admin")  # re-arming needs a tenant admin
    before = dict(CONTROLS.kill_switch)
    state = {"engaged": False, "by": who.name, "reason": body.reason, "at": utcnow().isoformat()}
    CONTROLS.save("kill_switch", state, who.name)
    audit.append(who.name, "killswitch.released", "tenant", before=before, after=state, reason=body.reason)
    return state


# ---------------------------------------------------------------------------- experiments
def _spec(body: ExperimentIn) -> dict[str, Any]:
    d = body.model_dump()
    d["environment"] = d["environment"] or policy_mod.environment()
    d["steady_state"] = d["steady_state"] or JOURNEY_BY_ID.get(body.journey_id, {}).get("steady_state", [])
    for c in d["steady_state"]:
        if c["metric"] not in METRICS:
            raise HTTPException(422, f"Unknown steady-state metric '{c['metric']}'")
    return d


@router.post("/experiments/validate")
async def validate_experiment(body: ExperimentIn, request: Request):
    await catalog.sample_shapes(manager(request))
    return engine(request).validate(_spec(body))


@router.get("/experiments")
def list_experiments(request: Request, include_archived: bool = False):
    mgr = manager(request)
    es = {e["key"]: e for e in catalog.edges(mgr)}
    with session_scope() as s:
        q = select(Experiment).order_by(Experiment.id.desc())
        if not include_archived:
            q = q.where(Experiment.archived.is_(False))
        out = []
        for e in s.scalars(q):
            last = s.scalars(select(ExperimentRun).where(ExperimentRun.experiment_id == e.id)
                             .order_by(ExperimentRun.id.desc()).limit(1)).first()
            out.append(experiment_dict(e) | {
                "approval": approval_status(e, approvals_for(s, e), es.get(e.edge_key)),
                "edge": es.get(e.edge_key), "fault_name": FAULTS.get(e.fault_id, {}).get("name"),
                "last_run": run_dict(last, full=False) if last else None})
    return out


@router.post("/experiments", status_code=201)
async def create_experiment(body: ExperimentIn, request: Request, who: Actor = Depends(actor)):
    require(who, "author")
    await catalog.sample_shapes(manager(request))
    spec = _spec(body)
    check = engine(request).validate(spec)
    if not check["ok"]:  # refused at save time with the rule named — never silently clamped
        raise HTTPException(422, {"message": "Experiment violates policy or applicability rules",
                                  "violations": check["violations"]})
    with session_scope() as s:
        e = Experiment(**spec, created_by=who.name)
        s.add(e)
        s.flush()
        d = experiment_dict(e)
    audit.append(who.name, "experiment.created", f"exp-{d['id']:04d}", f"{d['journey_id']} · {d['fault_id']} on {d['edge_key']}",
                 after=d, change_ref=d["change_ref"])
    return d


@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: int, request: Request):
    e_info = {e["key"]: e for e in catalog.edges(manager(request))}
    with session_scope() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise HTTPException(404, "Experiment not found")
        apps = approvals_for(s, e)
        runs = [run_dict(r, full=False) for r in s.scalars(select(ExperimentRun).where(ExperimentRun.experiment_id == exp_id)
                                                            .order_by(ExperimentRun.id.desc()))]
        return experiment_dict(e) | {"approvals": apps, "approval": approval_status(e, apps, e_info.get(e.edge_key)),
                                     "runs": runs, "edge": e_info.get(e.edge_key), "fault": FAULTS.get(e.fault_id)}


@router.put("/experiments/{exp_id}")
async def update_experiment(exp_id: int, body: ExperimentIn, request: Request, who: Actor = Depends(actor)):
    require(who, "author")
    await catalog.sample_shapes(manager(request))
    spec = _spec(body)
    check = engine(request).validate(spec)
    if not check["ok"]:
        raise HTTPException(422, {"message": "Experiment violates policy or applicability rules",
                                  "violations": check["violations"]})
    with session_scope() as s:
        e = s.get(Experiment, exp_id)
        if e is None or e.archived:
            raise HTTPException(404, "Experiment not found")
        before = experiment_dict(e)
        for k, v in spec.items():
            setattr(e, k, v)
        e.version += 1  # existing approvals no longer apply to the changed experiment
        e.updated_at = utcnow()
        s.flush()
        after = experiment_dict(e)
    audit.append(who.name, "experiment.updated", f"exp-{exp_id:04d}", f"version {before['version']} → {after['version']}; "
                 "approvals reset", before=before, after=after, change_ref=after["change_ref"])
    return after


@router.delete("/experiments/{exp_id}", status_code=204)
def archive_experiment(exp_id: int, who: Actor = Depends(actor)):
    require(who, "author")
    with session_scope() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise HTTPException(404, "Experiment not found")
        e.archived = True
    audit.append(who.name, "experiment.archived", f"exp-{exp_id:04d}")
    return Response(status_code=204)


@router.post("/experiments/{exp_id}/approvals", status_code=201)
def approve_experiment(exp_id: int, body: ApprovalIn, request: Request, who: Actor = Depends(actor)):
    require(who, "approve")
    edge_map = {e["key"]: e for e in catalog.edges(manager(request))}
    with session_scope() as s:
        e = s.get(Experiment, exp_id)
        if e is None or e.archived:
            raise HTTPException(404, "Experiment not found")
        if e.created_by == who.name:  # checked at request time, not display time
            raise HTTPException(403, "Segregation of duties: an author cannot approve their own experiment")
        current = [a for a in approvals_for(s, e) if a["valid"]]
        if any(a["actor"] == who.name for a in current):
            raise HTTPException(409, f"{who.name} has already approved version {e.version}")
        status_before = approval_status(e, current, edge_map.get(e.edge_key))
        if not status_before["required"]:
            raise HTTPException(409, "This experiment needs no approval in the current environment")
        s.add(Approval(experiment_id=e.id, experiment_version=e.version, actor=who.name, role=who.role, comment=body.comment))
        s.flush()
        apps = approvals_for(s, e)
        st = approval_status(e, apps, edge_map.get(e.edge_key))
        n = sum(a["valid"] for a in apps)
    audit.append(who.name, "run.approved", f"exp-{exp_id:04d}",
                 f"approval {n} of {len(st['required'])}" + (" · complete" if st["satisfied"] else ""), reason=body.comment)
    return st


@router.post("/experiments/{exp_id}/runs", status_code=201)
async def start_run(exp_id: int, body: RunIn, request: Request, who: Actor = Depends(actor)):
    require(who, "run")
    return engine(request).request_run(exp_id, who, body.dry_run, body.break_glass_reason)


# ---------------------------------------------------------------------------- runs
@router.get("/runs")
def list_runs(limit: int = Query(50, ge=1, le=500), experiment_id: int | None = None):
    with session_scope() as s:
        q = select(ExperimentRun).order_by(ExperimentRun.id.desc()).limit(limit)
        if experiment_id:
            q = q.where(ExperimentRun.experiment_id == experiment_id)
        return [run_dict(r, full=False) for r in s.scalars(q)]


@router.get("/runs/{run_id}")
def get_run(run_id: int, request: Request):
    with session_scope() as s:
        r = s.get(ExperimentRun, run_id)
        if r is None:
            raise HTTPException(404, "Run not found")
        d = run_dict(r)
        ids = r.results.get("finding_ids") or []
        d["findings"] = [analysis.finding_dict(f) for f in s.scalars(select(Finding).where(Finding.id.in_(ids)))] if ids else []
    d["edge"] = catalog.edge_by_key(manager(request), d["edge_key"]) if d["edge_key"] else None
    d["fault"] = FAULTS.get(d["fault_id"] or "")
    d["journey"] = {k: v for k, v in JOURNEY_BY_ID.get(d["journey_id"] or "", {}).items() if k in ("id", "name", "slo")}
    return d


@router.post("/runs/{run_id}/abort")
async def abort_run(run_id: int, body: ReasonIn, request: Request, who: Actor = Depends(actor)):
    require(who, "abort")
    engine(request).abort(run_id, who.name, body.reason)
    return {"run_id": run_id, "status": "aborting"}


# ---------------------------------------------------------------------------- findings
@router.get("/findings")
def list_findings(status: str | None = None, journey_id: str | None = None):
    with session_scope() as s:
        q = select(Finding).order_by(Finding.id.desc())
        if status:
            q = q.where(Finding.status == status)
        if journey_id:
            q = q.where(Finding.journey_id == journey_id)
        rows = [analysis.finding_dict(f) for f in s.scalars(q)]
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(rows, key=lambda f: (f["status"] == "resolved", order.get(f["severity"], 3), -f["id"]))


def _issue_markdown(f: dict[str, Any]) -> str:
    return (f"# [{f['severity'].upper()}] {f['title']}\n\n"
            f"- **Reference:** {f['reference'] or 'unfiled'}\n- **Journey:** {f['journey_id']}\n"
            f"- **Weak path:** {f['edge_key']}\n- **Fault:** {f['fault_name']}\n- **Owner:** {f['owner']}\n"
            f"- **Seen in:** run-{f['run_id']:04d} ({f['occurrences']} occurrence(s))\n\n"
            f"## What happened\n{f['what_happened']}\n\n## Recommended guardrail\n{f['guardrail']}\n")


@router.post("/findings/{finding_id}/file")
def file_finding(finding_id: int, who: Actor = Depends(actor)):
    require(who, "author")
    with session_scope() as s:
        f = s.get(Finding, finding_id)
        if f is None:
            raise HTTPException(404, "Finding not found")
        if f.status == "open":
            f.status, f.reference, f.updated_at = "filed", f"HM-{f.id:04d}", utcnow()
        d = analysis.finding_dict(f)
    audit.append(who.name, "finding.filed", d["reference"], d["title"])
    return d | {"issue_markdown": _issue_markdown(d)}


@router.post("/findings/{finding_id}/resolve")
def resolve_finding(finding_id: int, body: ReasonIn, who: Actor = Depends(actor)):
    require(who, "author")
    with session_scope() as s:
        f = s.get(Finding, finding_id)
        if f is None:
            raise HTTPException(404, "Finding not found")
        f.status, f.updated_at = "resolved", utcnow()
        d = analysis.finding_dict(f)
    audit.append(who.name, "finding.resolved", d["reference"] or f"finding {finding_id}", d["title"], reason=body.reason)
    return d


@router.get("/findings/export")
def export_findings(status: str | None = None):
    rows = list_findings(status)
    body = "\n---\n\n".join(_issue_markdown(f) for f in rows) or "No findings.\n"
    return Response(body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="resilience-findings.md"'})


# ---------------------------------------------------------------------------- suites
def _suite_dict(x: Suite, s) -> dict[str, Any]:
    last = s.scalars(select(SuiteRun).where(SuiteRun.suite_id == x.id).order_by(SuiteRun.id.desc()).limit(1)).first()
    return {"id": x.id, "name": x.name, "journey_id": x.journey_id, "experiment_ids": x.experiment_ids,
            "gate_mode": x.gate_mode, "gate_threshold": x.gate_threshold, "created_by": x.created_by,
            "last_run": _suite_run_dict(last) if last else None}


def _suite_run_dict(r: SuiteRun) -> dict[str, Any]:
    return {"id": r.id, "suite_id": r.suite_id, "status": r.status, "gate": r.gate, "score": r.score,
            "run_ids": r.run_ids, "detail": r.detail, "requested_by": r.requested_by,
            "created_at": r.created_at.isoformat(), "ended_at": r.ended_at.isoformat() if r.ended_at else None}


@router.get("/suites")
def list_suites():
    with session_scope() as s:
        return [_suite_dict(x, s) for x in s.scalars(select(Suite).order_by(Suite.id))]


@router.post("/suites", status_code=201)
def create_suite(body: SuiteIn, who: Actor = Depends(actor)):
    require(who, "author")
    if body.journey_id not in JOURNEY_BY_ID:
        raise HTTPException(422, "Unknown journey")
    with session_scope() as s:
        found = {e.id for e in s.scalars(select(Experiment).where(Experiment.id.in_(body.experiment_ids), Experiment.archived.is_(False)))}
        if missing := [i for i in body.experiment_ids if i not in found]:
            raise HTTPException(422, f"Experiments not found: {missing}")
        x = Suite(**body.model_dump(), created_by=who.name)
        s.add(x)
        s.flush()
        d = _suite_dict(x, s)
    audit.append(who.name, "suite.created", f"suite {d['name']}", f"{len(body.experiment_ids)} experiments · gate "
                 f"{body.gate_mode} below {body.gate_threshold}")
    return d


@router.post("/journeys/{journey_id}/starter-suite", status_code=201)
async def starter_suite(journey_id: str, request: Request, who: Actor = Depends(actor)):
    """Create one experiment per edge of the journey (the first eligible fault, read paths preferring
    timeout) plus a gating suite. Edges with no eligible fault in this mode are listed, not hidden."""
    require(who, "author")
    j = JOURNEY_BY_ID.get(journey_id)
    if j is None:
        raise HTTPException(404, "Unknown journey")
    mgr = manager(request)
    await catalog.sample_shapes(mgr)
    pol, env = catalog.policy_env()
    with session_scope() as s:
        pid = s.scalar(select(Patient.id).where(Patient.is_synthetic.is_(True)).order_by(Patient.id).limit(1))
    if pid is None:
        raise HTTPException(409, "No sample patient exists to use as the blast radius")
    prefer = ["fault.timeout", "fault.write_timeout", "fault.model_error", "fault.rate_limit", "fault.latency"]
    created, skipped = [], []
    for k in journey_edges(j):
        e = catalog.edge_by_key(mgr, k)
        if e is None:
            continue
        elig = [f["fault_id"] for f in catalog.edge_faults(e, pol, env) if f["status"] == "eligible"]
        choice = next((f for f in prefer if f in elig), elig[0] if elig else None)
        if choice is None:
            skipped.append({"edge_key": k, "reason": "no eligible fault in this mode/policy"})
            continue
        body = ExperimentIn(name=f"{FAULTS[choice]['name']} on {e['id']} {e['agent']} → {e['tool']}", journey_id=journey_id,
                            hypothesis=f"When {e['tool']} suffers {FAULTS[choice]['name'].lower()} on 30% of calls, "
                                       f"{j['name'].lower()} keeps its SLO.",
                            edge_key=k, fault_id=choice, share_pct=min(30, pol["ceilings"]["max_share_pct"][env]),
                            # Deterministic selection: a release gate must give the same answer for the same code.
                            selection="every_nth",
                            duration_s=min(60, pol["ceilings"]["max_duration_s"][env]), patient_scope=[pid])
        try:
            created.append(await create_experiment(body, request, who))
        except HTTPException as exc:
            skipped.append({"edge_key": k, "reason": str(exc.detail)})
    if not created:
        raise HTTPException(409, {"message": "No experiment could be created", "skipped": skipped})
    suite = create_suite(SuiteIn(name=f"{j['name']} — starter suite", journey_id=journey_id,
                                 experiment_ids=[c["id"] for c in created]), who)
    return {"suite": suite, "experiments": created, "skipped": skipped}


@router.post("/suites/{suite_id}/run", status_code=202)
async def run_suite(suite_id: int, body: SuiteRunIn, request: Request, who: Actor = Depends(actor)):
    require(who, "run")
    with session_scope() as s:
        x = s.get(Suite, suite_id)
        if x is None:
            raise HTTPException(404, "Suite not found")
        sr = SuiteRun(suite_id=suite_id, requested_by=who.name)
        s.add(sr)
        s.flush()
        d = _suite_run_dict(sr)
        name = x.name
    audit.append(who.name, "suite.run", f"suite {name}", f"suite run {d['id']}" + (" · wait for window" if body.wait_for_window else ""))
    asyncio.get_running_loop().create_task(engine(request).run_suite(d["id"], who, body.wait_for_window))
    return d


@router.get("/suite-runs/{suite_run_id}")
def get_suite_run(suite_run_id: int):
    with session_scope() as s:
        r = s.get(SuiteRun, suite_run_id)
        if r is None:
            raise HTTPException(404, "Suite run not found")
        d = _suite_run_dict(r)
        d["runs"] = [run_dict(x, full=False) for x in s.scalars(select(ExperimentRun).where(ExperimentRun.id.in_(r.run_ids or [])))]
        return d


# ---------------------------------------------------------------------------- audit
@router.get("/audit")
def get_audit(event: str | None = None, actor_q: str | None = Query(None, alias="actor"), days: int | None = Query(None, ge=1, le=3650)):
    return {"events": audit.query(event, actor_q, days), "chain": audit.verify(), "vocabulary": sorted(audit.EVENTS)}


@router.get("/audit/verify")
def verify_audit():
    return audit.verify()


@router.get("/audit/export")
def export_audit(format: Literal["csv", "json"] = "csv", event: str | None = None,
                 actor_q: str | None = Query(None, alias="actor"), days: int | None = None):
    body, mime, name = audit.export(format, event=event, actor=actor_q, days=days)
    return Response(body, media_type=f"{mime}; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})
