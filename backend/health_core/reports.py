"""Deterministic summary/report rendering (no LLM)."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone

DISCLAIMER = (
    "Educational demo only. This summary is generated from synthetic or self-entered data using "
    "simple, transparent rules. It is not a diagnosis, does not recommend treatment, and is not a "
    "substitute for advice from a qualified health professional."
)


def _fmt(trend: dict) -> str:
    if trend["count"] == 0:
        return f"- **{trend['label']}**: no readings in this period."
    latest = trend["latest"]
    if trend["metric"] == "blood_pressure" and "diastolic_latest" in trend:
        latest_s = f"{latest:g}/{trend['diastolic_latest']:g}"
        mean_s = f"{trend['mean']:g}/{trend['diastolic_mean']:g}"
    else:
        latest_s, mean_s = f"{latest:g}", f"{trend['mean']:g}"
    return (
        f"- **{trend['label']}**: latest {latest_s} {trend['unit']}, average {mean_s} {trend['unit']} "
        f"({'systolic ' if trend['metric'] == 'blood_pressure' else ''}range {trend['min']:g}–{trend['max']:g}, "
        f"{trend['count']} readings, trend: {trend['direction']})."
    )


def build_summary(profile: dict, trends: list[dict], alerts: list[dict], days: int) -> dict:
    sev = Counter(a["severity"] for a in alerts)
    open_alerts = [a for a in alerts if not a["acknowledged"]]
    with_data = [t for t in trends if t["count"] > 0]
    changing = [t for t in with_data if t["direction"] in ("rising", "falling")]

    lines = [
        f"# Health summary — {profile['name']}",
        "",
        f"_Period: last {days} days · Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        f"> {DISCLAIMER}",
        "",
        "## Profile",
        f"- Age: {profile['age']} · Sex: {profile['sex']}"
        + (f" · Height: {profile['height_cm']:g} cm" if profile.get("height_cm") else ""),
        f"- Data type: {'synthetic demo patient' if profile.get('is_synthetic') else 'user-entered'}",
        "",
        "## Readings and trends",
        *[_fmt(t) for t in trends],
        "",
        "## Rule-generated alerts",
    ]
    if alerts:
        lines.append(
            f"{len(alerts)} alert(s) in this period — critical: {sev.get('critical', 0)}, "
            f"warning: {sev.get('warning', 0)}, info: {sev.get('info', 0)}; {len(open_alerts)} not yet acknowledged."
        )
        for a in alerts[:10]:
            lines.append(f"- [{a['severity'].upper()}] {a['message']} — observed {a['observed']} on {a['recorded_at'][:16].replace('T', ' ')} (rule `{a['rule_id']}`)")
        if len(alerts) > 10:
            lines.append(f"- …and {len(alerts) - 10} more.")
    else:
        lines.append("No rule-generated alerts in this period.")
    lines += ["", "## Notable changes"]
    if changing:
        for t in changing:
            lines.append(f"- {t['label']} is {t['direction']} (about {t['slope_per_day']:+g} {t['unit']}/day).")
    else:
        lines.append("- No metric shows a clear rising or falling trend in this period.")

    return {
        "patient_id": profile["id"],
        "patient_name": profile["name"],
        "days": days,
        "metrics_with_data": len(with_data),
        "alert_counts": {"critical": sev.get("critical", 0), "warning": sev.get("warning", 0), "info": sev.get("info", 0)},
        "open_alerts": len(open_alerts),
        "trends": trends,
        "alerts": alerts,
        "markdown": "\n".join(lines),
        "disclaimer": DISCLAIMER,
        "generated_by": "deterministic report generator (Reports MCP server)",
    }


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "patient"


def render_report(summary: dict, history: list[dict], fmt: str) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    base = f"health-report-{_slug(summary['patient_name'])}-{stamp}"
    if fmt == "markdown":
        md = summary["markdown"] + "\n\n## Reading log\n\n| Time (UTC) | Metric | Value | Source |\n|---|---|---|---|\n"
        md += "\n".join(f"| {r['recorded_at'][:16].replace('T', ' ')} | {r['label']} | {r['display']} | {r['source']} |" for r in history)
        return {"filename": f"{base}.md", "mime_type": "text/markdown", "content": md + "\n"}
    if fmt == "json":
        payload = {k: v for k, v in summary.items() if k != "markdown"} | {"readings": history}
        return {"filename": f"{base}.json", "mime_type": "application/json", "content": json.dumps(payload, indent=2)}
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["recorded_at", "metric", "value", "value2", "unit", "source"])
        for r in history:
            w.writerow([r["recorded_at"], r["metric"], r["value"], r["value2"] if r["value2"] is not None else "", r["unit"], r["source"]])
        return {"filename": f"{base}.csv", "mime_type": "text/csv", "content": buf.getvalue()}
    raise ValueError(f"Unsupported format '{fmt}'")
