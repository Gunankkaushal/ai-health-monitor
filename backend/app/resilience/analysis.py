"""Turning a run's raw iterations into verdicts, cascade, findings and scores. All deterministic.

Score formula (the wireframe left this open; this is the documented choice):
  run score      = round(100 × (0.7 × steady_fraction + 0.3 × recovered))
                   steady_fraction = share of fault-phase iterations where EVERY steady-state check held
                   recovered       = 1 if every recovery iteration held after the fault was removed
                   A rolled-back run is capped at 49. Inconclusive and dry runs get no score.
  edge score     = mean of the latest scored run per fault on that edge
  journey score  = sum(edge scores) / number of journey edges with an active interception point
                   (an untested edge counts as 0, so coverage is part of resilience)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from health_core.db import session_scope
from health_core.models import utcnow
from health_core.resilience_models import ExperimentRun, Finding

from .injector import FAULTS, Observation
from .journeys import JOURNEY_BY_ID, METRICS, journey_edges

SCORE_FORMULA = ("run = 100 × (0.7 × share of fault iterations where every steady-state check held + 0.3 × recovered), "
                 "capped at 49 when rolled back · journey = sum of edge scores ÷ edges with an active interception "
                 "point (untested = 0)")


def aggregate(iterations: list[dict[str, Any]], metric: str) -> float | None:
    kind = METRICS[metric]["kind"]
    vals = [it["metrics"].get(metric) for it in iterations]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    if kind == "rate":
        return round(sum(vals) / len(vals), 1)
    if kind == "latency":
        vals = sorted(vals)
        return float(vals[min(len(vals) - 1, int(len(vals) * 0.95))])
    return float(sum(vals))


def holds(value: float | None, op: str, threshold: float) -> bool | None:
    if value is None:
        return None
    return value >= threshold if op == ">=" else value <= threshold


def phase_metrics(iterations: list[dict[str, Any]], checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {c["metric"]: aggregate(iterations, c["metric"]) for c in checks}


def iteration_holds(it: dict[str, Any], checks: list[dict[str, Any]]) -> bool:
    for c in checks:
        v = it["metrics"].get(c["metric"])
        if v is None:
            continue
        if holds(v, c["op"], c["threshold"]) is False:
            return False
    return True


def breached(iterations: list[dict[str, Any]], checks: list[dict[str, Any]], abort_only: bool) -> list[dict]:
    out = []
    for c in checks:
        if abort_only and not c.get("abort"):
            continue
        v = aggregate(iterations, c["metric"])
        if holds(v, c["op"], c["threshold"]) is False:
            out.append({**c, "value": v})
    return out


def fmt(metric: str, v: float | None) -> str:
    if v is None:
        return "—"
    unit = METRICS[metric]["unit"]
    return f"{v:g}{unit if unit == '%' else (' ' + unit if unit else '')}"


# ---------------------------------------------------------------------------- fault-phase statistics
def fault_stats(obs: list[Observation], edge: dict[str, Any], window: tuple[float, float],
                baseline: tuple[float, float], n_fault: int, n_base: int) -> dict[str, Any]:
    def on_edge(o: Observation) -> bool:
        return o.agent_key == edge["agent_key"] and o.tool == edge["tool"]

    f_obs = [o for o in obs if window[0] <= o.t <= window[1]]
    b_obs = [o for o in obs if baseline[0] <= o.t <= baseline[1]]
    target = [o for o in f_obs if on_edge(o)]
    base_target = [o for o in b_obs if on_edge(o)]
    per_iter_f = len(target) / n_fault if n_fault else 0
    per_iter_b = len(base_target) / n_base if n_base else 0
    other_err: dict[str, int] = {}
    base_err = {f"{o.agent_key}:{o.tool}" for o in b_obs if o.outcome == "error"}
    for o in f_obs:
        k = f"{o.agent_key}:{o.tool}"
        if o.outcome == "error" and not on_edge(o) and k not in base_err:
            other_err[k] = other_err.get(k, 0) + 1
    first = next((o.t for o in target if o.injected), None)
    return {
        "target_calls": len(target),
        "injected": sum(o.injected for o in target),
        "retries": sum(o.attempt > 1 for o in target),
        "baseline_calls_per_iteration": round(per_iter_b, 2),
        "fault_calls_per_iteration": round(per_iter_f, 2),
        "amplification": round(per_iter_f / per_iter_b, 2) if per_iter_b else None,
        "errors_beyond_target": other_err,
        "contained": not other_err,
        "first_injection_s": round(first - window[0], 1) if first else None,
        "edges_observed": sorted({f"{o.agent_key}:{o.tool}" for o in b_obs + f_obs}),
    }


def cascade(edge: dict[str, Any], fault: dict[str, Any], stats: dict[str, Any], journey: dict[str, Any],
            checks: list[dict[str, Any]], base: dict[str, Any], during: dict[str, Any],
            fault_iters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [{
        "node": f"{edge['id']} · {edge['agent']} → {edge['tool']}",
        "effect": f"{stats['injected']} of {stats['target_calls']} calls faulted ({fault['name']})"
                  + (f"; {stats['retries']} retries" if stats["retries"] else ""),
        "first_seen_s": stats["first_injection_s"], "hop": "targeted",
    }]
    if stats["amplification"] and stats["amplification"] > 1.2:
        rows.append({"node": edge["server"] or edge["tool"], "hop": "1st", "first_seen_s": stats["first_injection_s"],
                     "effect": f"Call volume {stats['amplification']}× baseline (self-inflicted by retries)"})
    for k, n in stats["errors_beyond_target"].items():
        rows.append({"node": k.replace(":", " → "), "hop": "1st", "first_seen_s": None,
                     "effect": f"{n} errors on an edge that was not targeted"})
    failed = [it for it in fault_iters if it["ok"] is False]
    if failed:
        rows.append({"node": edge["agent"], "hop": "2nd", "first_seen_s": failed[0].get("t_s"),
                     "effect": f"{len(failed)} of {len(fault_iters)} journey iterations failed — e.g. {failed[0].get('error') or failed[0].get('note')}"})
    parts = [f"{METRICS[c['metric']]['label']} {fmt(c['metric'], during.get(c['metric']))} "
             f"(baseline {fmt(c['metric'], base.get(c['metric']))})" for c in checks]
    rows.append({"node": f"Journey: {journey['name']}", "hop": "user-visible", "first_seen_s": None,
                 "effect": " · ".join(parts)})
    return rows


# ---------------------------------------------------------------------------- findings
GUARDRAIL_BY_FAULT = {
    "fault.latency": "Set per-call time budgets below the journey SLO and fail fast with a labelled partial result.",
    "fault.timeout": "Retry idempotent calls with bounded backoff and jitter (max 3 attempts); surface a degraded state instead of failing silently.",
    "fault.server_error": "Degrade the affected section with a clear message; keep the rest of the answer.",
    "fault.rate_limit": "Honour retry-after, cap attempts at 3, add jitter.",
    "fault.unavailable": "Bounded retry, then degrade clearly; alert the operator when a server stays down.",
    "fault.malformed": "Validate every tool result against its output contract and degrade gracefully when it does not match.",
    "fault.empty": "Treat an unexpected empty result as a data gap: say so in the answer and do not report success.",
    "fault.partial": "Compare returned counts with what was requested or stored and flag incomplete results.",
    "fault.stale": "Check freshness (newest timestamp vs the reading just stored) and label stale answers.",
    "fault.write_timeout": "Make writes idempotent and reconcile after an uncertain commit instead of assuming failure.",
    "fault.model_error": "Fall back to the deterministic answer and label it.",
    "fault.model_latency": "Set a model time budget and fall back to the deterministic answer when it is exceeded.",
    "fault.model_degradation": "Reject empty model answers and fall back to the deterministic answer.",
}

METRIC_FINDING = {
    "detection_rate": ("high", "Abnormal readings can go without an alert"),
    "safety_rate": ("high", "Medical-advice questions lose the safety note"),
    "success_rate": ("high", "Journey fails"),
    "complete_answer_rate": ("medium", "Assistant answers become incomplete"),
    "retrieval_rate": ("medium", "New readings are not visible to the Monitoring Agent"),
    "latency_p95_ms": ("medium", "Journey exceeds its latency SLO"),
    "unhandled_errors": ("high", "Unhandled exception reaches the user as an HTTP 500"),
}


def derive_findings(run: dict[str, Any], edge: dict[str, Any], fault: dict[str, Any], journey: dict[str, Any],
                    results: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    verdict, stats = run["verdict"], results.get("fault_stats") or {}

    def add(kind: str, severity: str, title: str, what: str, guardrail: str) -> None:
        out.append({"kind": kind, "severity": severity, "title": title, "what_happened": what,
                    "guardrail": guardrail, "owner": edge["owner"]})

    if verdict == "inconclusive" and results.get("inconclusive_reason") == "not_exercised":
        add("not_exercised", "low", f"{edge['id']} is never called by the {journey['name']} workload in this mode",
            f"The fault was armed for the whole run but no call reached {edge['agent']} → {edge['tool']}. "
            "The edge is untested, and it is invisible to coverage until something exercises it.",
            "Exercise this edge (for model-mediated tools, enable the Groq model) or remove it from the journey.")
        return out
    if verdict not in ("held", "rejected"):
        return out
    for check in results.get("steady_state", []):
        if check["held_during_fault"] is not False:
            continue
        severity, title = METRIC_FINDING[check["metric"]]
        if check["metric"] == "success_rate" and edge["key"] not in journey["critical_edges"]:
            severity = "medium"
        add(check["metric"], severity, f"{title} when {edge['id']} ({edge['tool']}) suffers {fault['name'].lower()}",
            f"{METRICS[check['metric']]['label']} fell to {fmt(check['metric'], check['fault'])} against a threshold of "
            f"{check['op']} {fmt(check['metric'], check['threshold'])} (baseline {fmt(check['metric'], check['baseline'])}).",
            GUARDRAIL_BY_FAULT.get(fault["id"], "Add a guardrail for this failure mode."))
    if stats.get("amplification") and stats["amplification"] >= 2.5:
        add("amplification", "medium", f"Retries multiply load on {edge['tool']} by {stats['amplification']}×",
            f"{stats['retries']} retries during the fault; calls per iteration rose from "
            f"{stats['baseline_calls_per_iteration']} to {stats['fault_calls_per_iteration']}.",
            "Cap attempts, honour retry-after, add jitter, and consider a circuit breaker.")
    if stats and not stats.get("contained", True):
        add("containment", "medium", f"Fault on {edge['id']} spread beyond the targeted edge",
            "Errors appeared on: " + ", ".join(stats["errors_beyond_target"]), "Isolate the dependency so one failing tool does not fail others.")
    if results.get("recovered") is False:
        add("recovery", "high", f"{journey['name']} did not recover after the fault was removed",
            "Steady state was still broken in the recovery phase.", "Investigate state left behind by the fault (caches, partial writes).")
    return out


def store_findings(run_id: int, journey_id: str, edge: dict[str, Any], fault_id: str, items: list[dict[str, Any]],
                   verdict: str | None) -> list[int]:
    """Insert or update findings (deduplicated per edge+fault+kind). A held run verifies and resolves
    open findings for the same edge and fault."""
    ids: list[int] = []
    with session_scope() as s:
        existing = {(f.edge_key, f.fault_id, f.kind): f for f in s.scalars(
            select(Finding).where(Finding.edge_key == edge["key"], Finding.fault_id == fault_id,
                                  Finding.status.in_(("open", "filed"))))}
        new_kinds = {i["kind"] for i in items}
        if verdict == "held":
            for (_, _, kind), f in existing.items():
                if kind not in new_kinds:
                    f.status, f.reference, f.updated_at = "resolved", f"verified by run {run_id}", utcnow()
        for item in items:
            f = existing.get((edge["key"], fault_id, item["kind"]))
            if f:
                f.occurrences += 1
                f.run_id, f.updated_at = run_id, utcnow()
                f.what_happened, f.severity = item["what_happened"], item["severity"]
            else:
                f = Finding(run_id=run_id, journey_id=journey_id, edge_key=edge["key"], fault_id=fault_id, **item)
                s.add(f)
            s.flush()
            ids.append(f.id)
    return ids


def finding_dict(f: Finding) -> dict[str, Any]:
    return {"id": f.id, "run_id": f.run_id, "journey_id": f.journey_id, "edge_key": f.edge_key, "fault_id": f.fault_id,
            "fault_name": FAULTS.get(f.fault_id, {}).get("name", f.fault_id), "kind": f.kind, "severity": f.severity,
            "title": f.title, "what_happened": f.what_happened, "guardrail": f.guardrail, "owner": f.owner,
            "status": f.status, "reference": f.reference, "occurrences": f.occurrences,
            "created_at": f.created_at.isoformat(), "updated_at": f.updated_at.isoformat()}


# ---------------------------------------------------------------------------- scores
def run_score(steady_fraction: float, recovered: bool, rolled_back: bool) -> int:
    score = round(100 * (0.7 * steady_fraction + 0.3 * (1 if recovered else 0)))
    return min(score, 49) if rolled_back else score


def journey_scores(edges: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_key = {e["key"]: e for e in edges}
    with session_scope() as s:
        runs = list(s.scalars(select(ExperimentRun).where(ExperimentRun.score.is_not(None), ExperimentRun.dry_run.is_(False))
                              .order_by(ExperimentRun.id)))
    # A run only scores the journey it ran under: an edge shared by two journeys is judged against each
    # journey's own steady state, so evidence from one journey never counts for another.
    latest: dict[tuple[str, str, str], tuple[int, int, str]] = {}  # (journey, edge, fault) -> (score, run id, ended)
    trend: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        k = (r.spec.get("journey_id"), r.spec.get("edge_key"), r.spec.get("fault_id"))
        latest[k] = (r.score, r.id, r.ended_at.isoformat() if r.ended_at else "")
    out: dict[str, dict[str, Any]] = {}
    for jid, j in JOURNEY_BY_ID.items():
        keys = journey_edges(j)
        testable = [k for k in keys if k in by_key and (by_key[k]["kind"] == "mcp.tool" or by_key[k]["connected"])]
        edge_scores: dict[str, float] = {}
        last_tested: str | None = None
        for k in testable:
            vals = [(sc, rid, ended) for (jj, ek, _), (sc, rid, ended) in latest.items() if jj == jid and ek == k]
            if vals:
                edge_scores[k] = sum(v[0] for v in vals) / len(vals)
                newest = max(v[2] for v in vals)
                last_tested = max(last_tested or "", newest)
        score = round(sum(edge_scores.values()) / len(testable)) if testable and edge_scores else None
        # Trend: journey score after each scored run touching this journey.
        acc: dict[tuple[str, str], int] = {}
        points = []
        for r in runs:
            if r.spec.get("journey_id") != jid:
                continue
            acc[(r.spec.get("edge_key"), r.spec.get("fault_id"))] = r.score
            per_edge: dict[str, list[int]] = {}
            for (ek, _), sc in acc.items():
                per_edge.setdefault(ek, []).append(sc)
            total = sum(sum(v) / len(v) for k, v in per_edge.items() if k in testable)
            points.append({"run_id": r.id, "at": r.ended_at.isoformat() if r.ended_at else None,
                           "score": round(total / len(testable)) if testable else 0, "run_score": r.score})
        trend[jid] = points
        out[jid] = {"score": score, "tested_edges": len(edge_scores), "testable_edges": len(testable),
                    "total_edges": len(keys), "edge_scores": {k: round(v) for k, v in edge_scores.items()},
                    "last_tested": last_tested, "trend": points[-30:]}
    return out
