"""Run engine: preflight (policy, approvals, blackout, concurrency, kill switch), then
baseline → inject → recovery, with a watchdog that rolls back on an abort-flagged breach.

Runs execute as asyncio tasks inside the backend process. Experiment data (readings written by the
ingestion workload, and the alerts they raise) is removed in cleanup; the ids are persisted as the
run progresses, so a run interrupted by a restart is cleaned up on the next startup.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select

from health_core.db import session_scope
from health_core.models import Patient, Reading, utcnow
from health_core.resilience_models import Approval, Experiment, ExperimentRun, Suite, SuiteRun

from ..mcp_client import MCPManager
from ..orchestrator import Orchestrator
from . import analysis, audit, catalog
from . import policy as policy_mod
from .injector import CONTROLS, FAULTS, INJECTOR, ArmedFault
from .journeys import JOURNEY_BY_ID, journey_edges
from .roles import APPROVAL_TTL_HOURS, ROLES, Actor, required_approvers
from .workloads import WORKLOADS, unhandled

log = logging.getLogger("app.resilience.runner")

ACTIVE = ("queued", "running")


class _Stop(Exception):
    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind, self.reason = kind, reason


# ---------------------------------------------------------------------------- serialisation
def experiment_dict(e: Experiment) -> dict[str, Any]:
    return {"id": e.id, "name": e.name, "journey_id": e.journey_id, "hypothesis": e.hypothesis, "edge_key": e.edge_key,
            "fault_id": e.fault_id, "params": e.params, "share_pct": e.share_pct, "selection": e.selection,
            "duration_s": e.duration_s, "patient_scope": e.patient_scope, "environment": e.environment,
            "steady_state": e.steady_state, "baseline_iterations": e.baseline_iterations,
            "fault_iterations": e.fault_iterations, "recovery_iterations": e.recovery_iterations,
            "change_ref": e.change_ref, "version": e.version, "created_by": e.created_by,
            "created_at": e.created_at.isoformat(), "updated_at": e.updated_at.isoformat(), "archived": e.archived}


def run_dict(r: ExperimentRun, full: bool = True) -> dict[str, Any]:
    d = {"id": r.id, "label": f"run-{r.id:04d}", "experiment_id": r.experiment_id, "suite_run_id": r.suite_run_id,
         "name": r.spec.get("name"), "journey_id": r.spec.get("journey_id"), "edge_key": r.spec.get("edge_key"),
         "fault_id": r.spec.get("fault_id"), "dry_run": r.dry_run, "status": r.status, "phase": r.phase,
         "verdict": r.verdict, "reason": r.reason, "score": r.score, "requested_by": r.requested_by,
         "break_glass": r.break_glass, "created_at": r.created_at.isoformat(),
         "started_at": r.started_at.isoformat() if r.started_at else None,
         "ended_at": r.ended_at.isoformat() if r.ended_at else None}
    if full:
        d |= {"spec": r.spec, "events": r.events, "results": r.results}
    return d


def approvals_for(s, e: Experiment) -> list[dict[str, Any]]:
    cutoff = utcnow() - timedelta(hours=APPROVAL_TTL_HOURS)
    out = []
    for a in s.scalars(select(Approval).where(Approval.experiment_id == e.id).order_by(Approval.id)):
        out.append({"id": a.id, "actor": a.actor, "role": a.role, "comment": a.comment,
                    "created_at": a.created_at.isoformat(), "experiment_version": a.experiment_version,
                    "valid": a.experiment_version == e.version and a.created_at >= cutoff})
    return out


def approval_status(e: Experiment, approvals: list[dict[str, Any]], edge: dict[str, Any] | None) -> dict[str, Any]:
    fault = FAULTS.get(e.fault_id, {"risk": "low"})
    needed, change_required = required_approvers(e.environment, fault["risk"], bool(edge and edge["direction"] == "write"))
    pool = [a for a in approvals if a["valid"]]
    missing, used = [], set()
    for role in needed:
        match = next((a for a in pool if a["id"] not in used and a["role"] == role), None) \
            or next((a for a in pool if a["id"] not in used and a["role"] == "tenant_admin"), None)
        if match:
            used.add(match["id"])
        else:
            missing.append(role)
    return {"required": needed, "required_labels": [ROLES[r]["label"] for r in needed], "missing": missing,
            "missing_labels": [ROLES[r]["label"] for r in missing], "change_ref_required": change_required,
            "change_ref_ok": bool(e.change_ref) or not change_required,
            "satisfied": not missing and (bool(e.change_ref) or not change_required)}


# ---------------------------------------------------------------------------- engine
class RunEngine:
    def __init__(self, manager: MCPManager, orch: Orchestrator) -> None:
        self.manager, self.orch = manager, orch
        self.tasks: dict[int, asyncio.Task] = {}
        self.abort_requests: dict[int, str] = {}

    # ---- startup / shutdown
    def recover_orphans(self) -> int:
        with session_scope() as s:
            orphans = list(s.scalars(select(ExperimentRun).where(ExperimentRun.status.in_(ACTIVE))))
            for r in orphans:
                _cleanup(r.results.get("created_reading_ids", []))
                r.status, r.phase, r.verdict = "failed", "done", "inconclusive"
                r.reason, r.ended_at = "Interrupted by a backend restart; faults were disarmed and experiment data removed.", utcnow()
            for sr in s.scalars(select(SuiteRun).where(SuiteRun.status.in_(("queued", "running", "waiting_window")))):
                sr.status, sr.detail, sr.ended_at = "failed", "Interrupted by a backend restart.", utcnow()
        return len(orphans)

    async def shutdown(self) -> None:
        INJECTOR.disarm_all()
        for t in list(self.tasks.values()):
            t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    def active_runs(self) -> list[int]:
        return [rid for rid, t in self.tasks.items() if not t.done()]

    # ---- kill switch
    def engage_kill_switch(self, actor: str, reason: str) -> dict[str, Any]:
        disarmed = INJECTOR.disarm_all()
        for rid in self.active_runs():
            self.abort_requests[rid] = f"Kill switch engaged by {actor}: {reason}"
        state = {"engaged": True, "by": actor, "reason": reason, "at": utcnow().isoformat()}
        CONTROLS.save("kill_switch", state, actor)
        audit.append(actor, "killswitch.engaged", "tenant", f"disarmed runs {disarmed}; stopping {self.active_runs()}",
                     reason=reason, after=state)
        return state

    # ---- preflight
    def _patients(self) -> dict[int, bool]:
        with session_scope() as s:
            return {p.id: p.is_synthetic for p in s.scalars(select(Patient))}

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Everything the builder needs: policy violations, applicability, approvals required."""
        policy_body, env = catalog.policy_env()
        errors: list[dict[str, str]] = []
        journey = JOURNEY_BY_ID.get(spec.get("journey_id", ""))
        edge = catalog.edge_by_key(self.manager, spec.get("edge_key", ""))
        fault = FAULTS.get(spec.get("fault_id", ""))
        if journey is None:
            errors.append({"rule": "journey", "message": "Unknown journey."})
        if edge is None:
            errors.append({"rule": "edge", "message": "Unknown edge."})
        elif journey and edge["key"] not in journey_edges(journey):
            errors.append({"rule": "edge", "message": f"{edge['id']} is not part of {journey['name']}."})
        if fault is None:
            errors.append({"rule": "fault", "message": "Unknown fault."})
        if spec.get("environment") != env:
            errors.append({"rule": "environment",
                           "message": f"This deployment is '{env}'; the experiment targets '{spec.get('environment')}'."})
        if not spec.get("patient_scope"):
            errors.append({"rule": "patient_scope", "message": "Choose at least one patient for the blast radius."})
        for p in fault["params"] if fault else []:
            v = spec.get("params", {}).get(p["name"], p.get("default"))
            if "options" in p and v not in p["options"]:
                errors.append({"rule": "params", "message": f"{p['label']} must be one of {p['options']}."})
            elif "min" in p and (not isinstance(v, (int, float)) or not p["min"] <= v <= p["max"]):
                errors.append({"rule": "params", "message": f"{p['label']} must be {p['min']}–{p['max']} {p.get('unit', '')}."})
        applic = None
        if edge and fault:
            applic = catalog.applicability(edge, fault, policy_body, env)
            if applic["status"] != "eligible":
                errors.append({"rule": "applicability", "message": f"{fault['name']} on {edge['id']}: {applic['status']} — {applic['reason']}"})
            errors += policy_mod.check_experiment(spec, edge, fault, self._patients(), policy_body, env)
        needed, change_required = required_approvers(env, fault["risk"] if fault else "low",
                                                     bool(edge and edge["direction"] == "write"))
        return {"ok": not errors, "violations": errors, "applicability": applic, "environment": env,
                "approvals_required": [ROLES[r]["label"] for r in needed], "change_ref_required": change_required,
                "edge": edge, "fault": fault}

    def request_run(self, experiment_id: int, actor: Actor, dry_run: bool = False, break_glass_reason: str = "",
                    suite_run_id: int | None = None) -> dict[str, Any]:
        with session_scope() as s:
            e = s.get(Experiment, experiment_id)
            if e is None or e.archived:
                raise HTTPException(404, "Experiment not found")
            spec = experiment_dict(e)
            approvals = approvals_for(s, e)
        refusal: tuple[int, str] | None = None
        check = self.validate(spec)
        edge = check["edge"]
        policy_body, env = catalog.policy_env()
        servers = {edge["server"]} if edge and edge["server"] else set()
        status = None
        if CONTROLS.kill_switch.get("engaged"):
            refusal = (409, "Kill switch is engaged. A tenant admin must release it before new runs.")
        elif not dry_run and not check["ok"]:
            refusal = (422, "; ".join(f"[{v['rule']}] {v['message']}" for v in check["violations"]))
        elif dry_run and [v for v in check["violations"] if v["rule"] in ("journey", "edge", "patient_scope", "environment")]:
            refusal = (422, "; ".join(v["message"] for v in check["violations"]))
        elif (w := policy_mod.active_blackout(policy_body, servers)) is not None:
            refusal = (409, f"Blackout window '{w['name']}' ({w['start']}–{w['end']} UTC) is active.")
        elif len(self.active_runs()) >= policy_body["ceilings"]["max_concurrent_runs"][env]:
            refusal = (409, f"Concurrency ceiling reached ({policy_body['ceilings']['max_concurrent_runs'][env]} run(s) in {env}).")
        if refusal is None and not dry_run:
            with session_scope() as s:
                status = approval_status(s.get(Experiment, experiment_id), approvals, edge)
            if not status["satisfied"]:
                missing = ", ".join(status["missing_labels"]) or "change record"
                if break_glass_reason and actor.role == "tenant_admin":
                    pass  # allowed below, logged as break-glass
                else:
                    refusal = (409, f"Approval required from: {missing}." +
                               ("" if status["change_ref_ok"] else " A change record (CHG) is required."))

        with session_scope() as s:
            run = ExperimentRun(experiment_id=experiment_id, suite_run_id=suite_run_id, spec=spec, dry_run=dry_run,
                                requested_by=actor.name, status="refused" if refusal else "queued",
                                phase="done" if refusal else "queued", reason=refusal[1] if refusal else "",
                                break_glass=break_glass_reason if (status and not status["satisfied"]) else "",
                                events=[], results={})
            if refusal:
                run.ended_at = utcnow()
            s.add(run)
            s.flush()
            rid = run.id
        label = f"run-{rid:04d}"
        if refusal:
            audit.append(actor.name, "run.refused", label, refusal[1], reason=refusal[1])
            raise HTTPException(refusal[0], {"message": refusal[1], "run_id": rid})
        audit.append(actor.name, "run.requested", label, f"experiment {experiment_id} · {spec['fault_id']} on "
                     f"{spec['edge_key']}" + (" · dry run" if dry_run else ""), change_ref=spec["change_ref"])
        if break_glass_reason and status and not status["satisfied"]:
            audit.append(actor.name, "run.break_glass", label, "Run started without the required approvals",
                         reason=break_glass_reason)
        self.tasks[rid] = asyncio.create_task(self._execute(rid, actor.name), name=f"chaos-run-{rid}")
        with session_scope() as s:
            return run_dict(s.get(ExperimentRun, rid))

    def abort(self, run_id: int, actor: str, reason: str) -> None:
        if run_id not in self.active_runs():
            raise HTTPException(409, "Run is not active")
        self.abort_requests[run_id] = f"Aborted by {actor}" + (f": {reason}" if reason else "")
        INJECTOR.disarm(run_id)  # stop damage immediately, before the next iteration boundary

    # ---- execution
    def _update(self, run_id: int, **fields: Any) -> None:
        with session_scope() as s:
            r = s.get(ExperimentRun, run_id)
            for k, v in fields.items():
                setattr(r, k, v)

    async def _execute(self, run_id: int, actor: str) -> None:
        with session_scope() as s:
            r = s.get(ExperimentRun, run_id)
            spec, dry = dict(r.spec), r.dry_run
        label = f"run-{run_id:04d}"
        journey = JOURNEY_BY_ID[spec["journey_id"]]
        edge = catalog.edge_by_key(self.manager, spec["edge_key"])
        fault = FAULTS[spec["fault_id"]]
        policy_body, _ = catalog.policy_env()
        telemetry_s = policy_body["auto_rollback"].get("telemetry_loss_s", 30)
        checks = spec["steady_state"] or journey["steady_state"]
        workload = WORKLOADS[journey["id"]]
        scope = frozenset(spec["patient_scope"])
        events: list[dict[str, Any]] = []
        created: list[int] = []
        iterations: dict[str, list[dict[str, Any]]] = {"baseline": [], "fault": [], "recovery": []}
        results: dict[str, Any] = {"created_reading_ids": created, "checks": checks}
        t_start = time.monotonic()
        counter = 0
        obs = INJECTOR.observe(run_id, scope)
        windows: dict[str, tuple[float, float]] = {}

        def event(msg: str) -> None:
            events.append({"t_s": round(time.monotonic() - t_start, 1), "at": utcnow().isoformat(), "msg": msg})

        def flush(**extra: Any) -> None:
            results["iterations"] = iterations
            self._update(run_id, events=list(events), results=dict(results), **extra)

        async def phase(name: str, n: int, watchdog: bool = False, deadline: float | None = None) -> None:
            nonlocal counter
            t0 = time.monotonic()
            for k in range(n):
                if run_id in self.abort_requests:
                    raise _Stop("aborted", self.abort_requests[run_id])
                if CONTROLS.kill_switch.get("engaged"):
                    raise _Stop("aborted", "Kill switch engaged")
                if deadline and time.monotonic() >= deadline:
                    event(f"Hard stop: {spec['duration_s']} s fault duration reached after {k} iterations")
                    break
                pid = spec["patient_scope"][counter % len(spec["patient_scope"])]
                try:
                    it = await asyncio.wait_for(workload(self.orch, counter, pid, created), timeout=telemetry_s)
                except asyncio.TimeoutError:
                    if watchdog:
                        raise _Stop("rolled_back", f"Watchdog lost telemetry: an iteration ran longer than {telemetry_s} s")
                    it = unhandled([c["metric"] for c in checks], TimeoutError(f"iteration exceeded {telemetry_s} s"))
                except Exception as exc:  # noqa: BLE001 - an escaped exception is a result, not a crash of the run
                    it = unhandled([c["metric"] for c in checks], exc)
                counter += 1
                it |= {"i": counter, "patient_id": pid, "t_s": round(time.monotonic() - t_start, 1),
                       "holds": None}
                it["holds"] = analysis.iteration_holds(it, checks)
                iterations[name].append(it)
                results["created_reading_ids"] = created
                flush()
                if watchdog and policy_body["auto_rollback"].get("abort_metric_breach", True):
                    bad = analysis.breached(iterations[name], checks, abort_only=True)
                    if bad:
                        b = bad[0]
                        raise _Stop("rolled_back", f"Steady state breached: {analysis.METRICS[b['metric']]['label']} "
                                                    f"{analysis.fmt(b['metric'], b['value'])} vs {b['op']} "
                                                    f"{analysis.fmt(b['metric'], b['threshold'])}")
            windows[name] = (t0, time.monotonic())

        stop: _Stop | None = None
        t_disarm: float | None = None
        try:
            self._update(run_id, status="running", phase="baseline", started_at=utcnow())
            audit.append(actor, "run.started", label, f"{journey['name']} · {len(scope)} patient(s) in scope")
            event(f"Run started · journey {journey['name']} · patients {sorted(scope)}")
            await phase("baseline", spec["baseline_iterations"])
            base_metrics = analysis.phase_metrics(iterations["baseline"], checks)
            base_bad = analysis.breached(iterations["baseline"], checks, abort_only=False)
            results["baseline"] = base_metrics
            event("Baseline captured: " + ", ".join(f"{analysis.METRICS[m]['label']} {analysis.fmt(m, v)}"
                                                    for m, v in base_metrics.items()))
            if base_bad and not dry:
                raise _Stop("baseline", "Steady state did not hold before injection: " + ", ".join(
                    analysis.METRICS[b["metric"]]["label"] for b in base_bad))

            self._update(run_id, phase="fault" if not dry else "observe")
            deadline = time.monotonic() + spec["duration_s"]
            if not dry:
                INJECTOR.arm(ArmedFault(run_id, edge["agent_key"], edge["tool"], fault,
                                        {**{p["name"]: p.get("default") for p in fault["params"]}, **spec["params"]},
                                        spec["share_pct"], spec["selection"], scope, random.Random(run_id)))
                audit.append("scheduler (service)", "fault.armed", f"{label} · {edge['id']}",
                             f"{fault['name']} on {spec['share_pct']}% of calls ({spec['selection']}), up to "
                             f"{spec['duration_s']} s, patients {sorted(scope)}", on_behalf_of=actor)
                event(f"Fault armed: {fault['name']} on {edge['id']} {edge['agent']} → {edge['tool']} · {spec['share_pct']}% of calls")
            try:
                await phase("fault", spec["fault_iterations"], watchdog=not dry, deadline=deadline)
            finally:
                if not dry and INJECTOR.disarm(run_id):
                    audit.append("scheduler (service)", "fault.disarmed", label, "fault removed", on_behalf_of=actor)
                    event("Fault disarmed")
            self._update(run_id, phase="recovery")
            t_disarm = time.monotonic()
            await phase("recovery", spec["recovery_iterations"])
        except _Stop as st:
            stop = st
            if INJECTOR.disarm(run_id):
                event("Fault disarmed")
            if st.kind == "rolled_back":
                event(f"Watchdog rollback: {st.reason}")
                audit.append("watchdog (service)", "run.rolled_back", label, st.reason, on_behalf_of=actor)
                windows.setdefault("fault", (windows.get("baseline", (t_start, t_start))[1], time.monotonic()))
                self._update(run_id, phase="recovery")
                t_disarm = time.monotonic()
                try:
                    await phase("recovery", spec["recovery_iterations"])
                except _Stop as st2:  # an abort during recovery still ends the run
                    stop = _Stop("aborted", f"{st.reason}; then {st2.reason}") if st2.kind == "aborted" else stop
            else:
                event(st.reason)
        except Exception as exc:  # noqa: BLE001
            log.exception("run %s failed", run_id)
            INJECTOR.disarm(run_id)
            stop = _Stop("failed", f"Run engine error: {type(exc).__name__}: {exc}")
            event(stop.reason)
        finally:
            INJECTOR.disarm(run_id)
            INJECTOR.stop_observing(run_id)
            self.abort_requests.pop(run_id, None)

        # ---- analysis
        status, verdict, reason, score = "completed", None, "", None
        fault_it, rec_it = iterations["fault"], iterations["recovery"]
        during = analysis.phase_metrics(fault_it, checks)
        recov = analysis.phase_metrics(rec_it, checks)
        base = results.get("baseline", {})
        results["steady_state"] = [
            {**c, "baseline": base.get(c["metric"]), "fault": during.get(c["metric"]), "recovery": recov.get(c["metric"]),
             "held_during_fault": analysis.holds(during.get(c["metric"]), c["op"], c["threshold"])}
            for c in checks]
        recovered = bool(rec_it) and all(it["holds"] for it in rec_it)
        results["recovered"] = recovered if rec_it else None
        first_ok = next((it for it in rec_it if it["holds"]), None)
        results["recovery_time_s"] = round(first_ok["t_s"] - (t_disarm - t_start), 1) if first_ok and t_disarm is not None else None
        if not dry and edge and "fault" in windows:
            stats = analysis.fault_stats(obs, edge, windows["fault"], windows.get("baseline", (0, 0)),
                                         len(fault_it), len(iterations["baseline"]))
            results["fault_stats"] = stats
            results["cascade"] = analysis.cascade(edge, fault, stats, journey, checks, base, during, fault_it)
        else:
            stats = None

        if stop and stop.kind == "aborted":
            status, verdict, reason = "aborted", None, stop.reason
            audit.append(actor, "run.aborted", label, stop.reason, reason=stop.reason)
        elif stop and stop.kind == "failed":
            status, verdict, reason = "failed", "inconclusive", stop.reason
            audit.append("scheduler (service)", "run.failed", label, stop.reason)
        elif stop and stop.kind == "baseline":
            verdict, reason = "inconclusive", stop.reason
            results["inconclusive_reason"] = "baseline"
        elif dry:
            ok = all(it["holds"] for it in iterations["baseline"] + fault_it + rec_it)
            verdict = "dry_run_passed" if ok else "dry_run_failed"
            reason = "Steady state held without any fault." if ok else "Steady state did not hold even without a fault."
        elif stats and stats["injected"] == 0:
            verdict = "inconclusive"
            results["inconclusive_reason"] = "not_exercised"
            reason = (f"The fault was armed but no call reached {edge['id']} ({edge['agent']} → {edge['tool']}). "
                      "The workload does not exercise this edge in the current mode.")
        else:
            rolled = bool(stop and stop.kind == "rolled_back")
            steady = sum(1 for it in fault_it if it["holds"]) / len(fault_it) if fault_it else 0.0
            held = not rolled and all(c["held_during_fault"] is not False for c in results["steady_state"])
            verdict = "held" if held else "rejected"
            status = "rolled_back" if rolled else "completed"
            score = analysis.run_score(steady, recovered, rolled)
            results["score_breakdown"] = {"steady_fraction": round(steady, 2), "recovered": recovered,
                                          "rolled_back": rolled, "formula": analysis.SCORE_FORMULA}
            reason = (stop.reason if rolled else
                      "Hypothesis held: steady state was maintained while the fault was active." if held else
                      "Hypothesis rejected: " + "; ".join(
                          f"{analysis.METRICS[c['metric']]['label']} {analysis.fmt(c['metric'], c['fault'])} "
                          f"(needs {c['op']} {analysis.fmt(c['metric'], c['threshold'])})"
                          for c in results["steady_state"] if c["held_during_fault"] is False))
        # cleanup: experiment data never outlives the run
        removed = _cleanup(created)
        if created:
            event(f"Cleanup: removed {removed} experiment reading(s) and their alerts")
        event(f"Run {status}" + (f" · verdict {verdict}" if verdict else "") + (f" · score {score}" if score is not None else ""))
        run_view = {"verdict": verdict}
        findings = analysis.derive_findings(run_view, edge, fault, journey, results) if edge and not dry else []
        results["finding_ids"] = analysis.store_findings(run_id, journey["id"], edge, fault["id"], findings, verdict) \
            if edge and not dry and verdict in ("held", "rejected", "inconclusive") else []
        results["created_reading_ids"] = []
        results["cleaned_up_readings"] = removed
        results["iterations"] = iterations
        self._update(run_id, status=status, phase="done", verdict=verdict, reason=reason, score=score,
                     events=events, results=results, ended_at=utcnow())
        if status != "aborted":
            audit.append("scheduler (service)", "run.completed", label,
                         f"{status} · verdict {verdict} · score {score}", on_behalf_of=actor)
        self.tasks.pop(run_id, None)

    # ---- suites
    async def run_suite(self, suite_run_id: int, actor: Actor, wait_for_window: bool) -> None:
        with session_scope() as s:
            sr = s.get(SuiteRun, suite_run_id)
            suite = s.get(Suite, sr.suite_id)
            exp_ids, journey_id = list(suite.experiment_ids), suite.journey_id
            mode, threshold, name = suite.gate_mode, suite.gate_threshold, suite.name
        run_ids: list[int] = []
        notes: list[str] = []

        def upd(**kw: Any) -> None:
            with session_scope() as s:
                r = s.get(SuiteRun, suite_run_id)
                for k, v in kw.items():
                    setattr(r, k, v)

        try:
            for eid in exp_ids:
                while True:
                    try:
                        upd(status="running")
                        run = self.request_run(eid, actor, suite_run_id=suite_run_id)
                        break
                    except HTTPException as exc:
                        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                        if wait_for_window and ("Blackout" in detail["message"] or "Concurrency" in detail["message"]):
                            upd(status="waiting_window", detail=detail["message"])
                            await asyncio.sleep(5)
                            continue
                        if detail.get("run_id"):
                            run_ids.append(detail["run_id"])
                        notes.append(f"experiment {eid} refused: {detail['message']}")
                        run = None
                        break
                if run:
                    run_ids.append(run["id"])
                    upd(run_ids=list(run_ids))
                    task = self.tasks.get(run["id"])
                    if task:
                        await asyncio.wait({task})
            scores = analysis.journey_scores(catalog.edges(self.manager))
            score = scores[journey_id]["score"]
            gate = ("passed" if (score or 0) >= threshold else "blocked" if mode == "block" else "warned") \
                if mode != "report" else "passed"
            detail = (f"Journey score {score if score is not None else 'n/a'} vs gate {threshold} ({mode})"
                      + (" · " + " · ".join(notes) if notes else ""))
            upd(status="completed", gate=gate, score=score, run_ids=run_ids, detail=detail, ended_at=utcnow())
            audit.append(actor.name, "gate.evaluated", f"suite {name}", f"{gate}: {detail}")
        except asyncio.CancelledError:
            upd(status="failed", detail="cancelled", ended_at=utcnow())
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("suite run failed")
            upd(status="failed", detail=f"{type(exc).__name__}: {exc}", ended_at=utcnow())


def _cleanup(reading_ids: list[int]) -> int:
    if not reading_ids:
        return 0
    with session_scope() as s:  # alerts cascade with their readings (FK ON DELETE CASCADE)
        res = s.execute(delete(Reading).where(Reading.id.in_(reading_ids), Reading.source == "experiment"))
        return res.rowcount or 0
