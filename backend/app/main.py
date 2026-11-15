"""FastAPI application.

Run:  uvicorn app.main:app --port 8000   (from the backend/ directory)
On startup the backend launches the three MCP servers over stdio and discovers their tools.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from health_core.db import get_sessionmaker, init_db
from health_core.models import Alert, Patient, Reading, utcnow
from health_core.repository import alert_dict, profile_dict, reading_dict
from health_core.units import METRICS

from . import activity
from .agents import describe_agents
from .llm import mode_info
from .mcp_client import (
    LOG_DIR,
    MCPClientError,
    MCPManager,
    MCPTimeoutError,
    MCPToolError,
    MCPUnavailableError,
    ToolArgumentError,
)
from .orchestrator import Orchestrator
from .resilience import catalog as rl_catalog
from .resilience.api import router as resilience_router
from .resilience.injector import CONTROLS
from .resilience.runner import RunEngine
from .schemas import ChatIn, PatientIn, ReadingsIn, SimulateIn, SummaryIn
from .seed import seed
from .simulator import simulate_vitals


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    if not any(getattr(h, "_health_app", False) for h in root.handlers):
        for h in (logging.StreamHandler(sys.stderr), logging.FileHandler(LOG_DIR / "backend.log", encoding="utf-8")):
            h.setFormatter(fmt)
            h._health_app = True  # type: ignore[attr-defined]
            root.addHandler(h)
    root.setLevel(logging.INFO)
    # httpx would log request URLs; keep it quiet (and never log headers/keys).
    for noisy in ("httpx", "httpcore", "groq"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_logging()
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seeded = seed()
    if seeded:
        log.info("Seeded %d synthetic demo patients", seeded)
    manager = MCPManager()
    app.state.mcp = manager
    app.state.orchestrator = Orchestrator(manager)
    app.state.resilience = RunEngine(manager, app.state.orchestrator)
    CONTROLS.load()
    orphans = app.state.resilience.recover_orphans()
    if orphans:
        log.warning("Marked %d interrupted resilience run(s) as failed and cleaned up their data", orphans)
    log.info("Mode: %s", mode_info()["reason"])
    await manager.start()
    try:
        await rl_catalog.sample_shapes(manager, force=True)
    except Exception:  # discovery must never block startup
        log.exception("response-shape sampling failed")
    try:
        yield
    finally:
        await app.state.resilience.shutdown()
        await manager.stop()


app = FastAPI(title="AI Health Monitoring System (educational demo)", version="1.0.0", lifespan=lifespan)
app.include_router(resilience_router)


# ------------------------------------------------------------------ helpers
def db() -> Session:
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.close()


def orch(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_patient(s: Session, patient_id: int) -> Patient:
    p = s.get(Patient, patient_id)
    if p is None:
        raise HTTPException(404, f"Patient {patient_id} not found")
    return p


@app.exception_handler(MCPClientError)
async def _mcp_error(_: Request, exc: MCPClientError):
    status = 504 if isinstance(exc, MCPTimeoutError) else 503 if isinstance(exc, MCPUnavailableError) else \
        422 if isinstance(exc, ToolArgumentError) else 502 if isinstance(exc, MCPToolError) else 500
    return JSONResponse({"detail": f"MCP error: {exc}", "error_type": type(exc).__name__}, status_code=status)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e.get("loc", []) if p != "body")
        msg = str(e.get("msg", "")).removeprefix("Value error, ")
        msgs.append(f"{loc}: {msg}" if loc else msg)
    return JSONResponse({"detail": "; ".join(msgs)}, status_code=422)


# ------------------------------------------------------------------ system
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    return {
        **mode_info(),
        "metrics": [{"key": m.key, "label": m.label, "unit": m.unit, "accepted_units": list(m.accepted_units),
                     "min": m.min_value, "max": m.max_value} for m in METRICS.values()],
        "agents": describe_agents(),
        "disclaimer": "Educational monitoring demo with synthetic data. Not a medical device; does not diagnose or "
                      "recommend treatment.",
    }


@app.get("/api/mcp/status")
def mcp_status(request: Request):
    servers = request.app.state.mcp.status()
    return {"servers": servers, "connected": sum(s["status"] == "connected" for s in servers),
            "total_tools": sum(len(s["tools"]) for s in servers)}


@app.post("/api/mcp/reconnect")
async def mcp_reconnect(request: Request, server: str | None = None):
    await request.app.state.mcp.reconnect(server)
    return mcp_status(request)


@app.get("/api/rules")
async def rules(o: Orchestrator = Depends(orch)):
    return await o.rules()


@app.get("/api/activity")
def get_activity(patient_id: int | None = None, limit: int = Query(60, ge=1, le=300)):
    return activity.recent(patient_id, limit)


# ------------------------------------------------------------------ patients
@app.get("/api/patients")
def list_patients(s: Session = Depends(db)):
    out = []
    for p in s.scalars(select(Patient).order_by(Patient.id)):
        open_alerts = s.scalars(select(Alert.severity).where(Alert.patient_id == p.id, Alert.acknowledged.is_(False))).all()
        out.append(profile_dict(p) | {"open_alerts": len(open_alerts),
                                      "critical_alerts": sum(1 for x in open_alerts if x == "critical")})
    return out


@app.post("/api/patients", status_code=201)
def create_patient(body: PatientIn, s: Session = Depends(db)):
    p = Patient(**body.model_dump(), is_synthetic=False)
    s.add(p)
    s.commit()
    return profile_dict(p)


@app.get("/api/patients/{patient_id}")
def read_patient(patient_id: int, s: Session = Depends(db)):
    return profile_dict(get_patient(s, patient_id))


@app.put("/api/patients/{patient_id}")
def update_patient(patient_id: int, body: PatientIn, s: Session = Depends(db)):
    p = get_patient(s, patient_id)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    s.commit()
    return profile_dict(p)


@app.delete("/api/patients/{patient_id}", status_code=204)
def delete_patient(patient_id: int, s: Session = Depends(db)):
    s.delete(get_patient(s, patient_id))
    s.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ readings
@app.get("/api/patients/{patient_id}/readings")
def list_readings(patient_id: int, days: int = Query(30, ge=1, le=365), metric: str | None = None,
                  s: Session = Depends(db)):
    get_patient(s, patient_id)
    if metric is not None and metric not in METRICS:
        raise HTTPException(422, f"Unknown metric '{metric}'")
    q = select(Reading).where(Reading.patient_id == patient_id, Reading.recorded_at >= utcnow() - timedelta(days=days))
    if metric:
        q = q.where(Reading.metric == metric)
    return [reading_dict(r) for r in s.scalars(q.order_by(Reading.recorded_at.desc(), Reading.id.desc()))]


async def _store_and_process(patient_id: int, entries: list[dict], recorded_at, source: str, s: Session, o: Orchestrator):
    get_patient(s, patient_id)
    ts = recorded_at or utcnow()
    rows = [Reading(patient_id=patient_id, metric=e["metric"], value=e["value"], value2=e.get("value2"),
                    unit=e["unit"], recorded_at=ts, source=source) for e in entries]
    s.add_all(rows)
    s.commit()
    ids = [r.id for r in rows]
    try:
        pipeline = await o.process_new_readings(patient_id, ids)
        pipeline_error = None
    except MCPClientError as exc:  # readings are saved even if MCP is down; say so clearly
        pipeline, pipeline_error = None, f"Readings saved, but MCP processing failed: {exc}"
    return {"readings": [reading_dict(r) for r in rows], "pipeline": pipeline, "pipeline_error": pipeline_error}


@app.post("/api/patients/{patient_id}/readings", status_code=201)
async def add_readings(patient_id: int, body: ReadingsIn, s: Session = Depends(db), o: Orchestrator = Depends(orch)):
    entries = [e.model_dump() for e in body.entries]
    return await _store_and_process(patient_id, entries, body.recorded_at, "manual", s, o)


@app.post("/api/patients/{patient_id}/simulate", status_code=201)
async def simulate(patient_id: int, body: SimulateIn, s: Session = Depends(db), o: Orchestrator = Depends(orch)):
    """SYNTHETIC simulator — no real device is involved. Readings are stored with source='simulator'."""
    get_patient(s, patient_id)
    last_weight = s.scalar(select(Reading.value).where(Reading.patient_id == patient_id, Reading.metric == "weight")
                           .order_by(Reading.recorded_at.desc()).limit(1))
    entries = simulate_vitals(body.scenario, base_weight=last_weight or 72.0)
    result = await _store_and_process(patient_id, entries, None, "simulator", s, o)
    return result | {"scenario": body.scenario, "synthetic": True}


@app.delete("/api/patients/{patient_id}/readings/{reading_id}", status_code=204)
def delete_reading(patient_id: int, reading_id: int, s: Session = Depends(db)):
    r = s.get(Reading, reading_id)
    if r is None or r.patient_id != patient_id:  # never touch another patient's data
        raise HTTPException(404, "Reading not found for this patient")
    s.delete(r)
    s.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ alerts
@app.get("/api/patients/{patient_id}/alerts")
def list_alerts(patient_id: int, include_acknowledged: bool = True, s: Session = Depends(db)):
    get_patient(s, patient_id)
    q = select(Alert).where(Alert.patient_id == patient_id)
    if not include_acknowledged:
        q = q.where(Alert.acknowledged.is_(False))
    return [alert_dict(a) for a in s.scalars(q.order_by(Alert.recorded_at.desc(), Alert.id.desc()).limit(200))]


def _alert_for(s: Session, patient_id: int, alert_id: int) -> Alert:
    a = s.get(Alert, alert_id)
    if a is None or a.patient_id != patient_id:
        raise HTTPException(404, "Alert not found for this patient")
    return a


@app.post("/api/patients/{patient_id}/alerts/{alert_id}/acknowledge")
def acknowledge(patient_id: int, alert_id: int, s: Session = Depends(db)):
    a = _alert_for(s, patient_id, alert_id)
    a.acknowledged = True
    s.commit()
    return alert_dict(a)


@app.post("/api/patients/{patient_id}/alerts/{alert_id}/explain")
async def explain(patient_id: int, alert_id: int, s: Session = Depends(db), o: Orchestrator = Depends(orch)):
    a = _alert_for(s, patient_id, alert_id)
    result = await o.explain_alert(patient_id, alert_dict(a))
    if result.pop("store", False):
        a.ai_explanation = result["text"]
        s.commit()
    return result


# ------------------------------------------------------------------ agents
@app.post("/api/patients/{patient_id}/chat")
async def chat(patient_id: int, body: ChatIn, s: Session = Depends(db), o: Orchestrator = Depends(orch)):
    get_patient(s, patient_id)
    return await o.chat(patient_id, body.message, [m.model_dump() for m in body.history])


@app.post("/api/patients/{patient_id}/summary")
async def summary(patient_id: int, body: SummaryIn, s: Session = Depends(db), o: Orchestrator = Depends(orch)):
    get_patient(s, patient_id)
    return await o.summary(patient_id, body.days)


@app.get("/api/patients/{patient_id}/report")
async def report(patient_id: int, format: Literal["markdown", "json", "csv"] = "markdown",
                 days: int = Query(30, ge=1, le=365), s: Session = Depends(db), o: Orchestrator = Depends(orch)):
    get_patient(s, patient_id)
    r = await o.export(patient_id, format, days)
    return Response(content=r["content"], media_type=f"{r['mime_type']}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{r["filename"]}"'})
