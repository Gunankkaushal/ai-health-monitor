import { useState } from "react";
import { api, errorMessage } from "../api";
import type { AddReadingsResponse, MetricKey, MetricMeta } from "../types";
import { Card, Icon, InlineError, METRIC_ICON, SeverityBadge } from "./ui";

type Field = { value: string; value2: string; unit: string };

function nowLocal() {
  const d = new Date();
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

const PLACEHOLDER: Record<MetricKey, string> = {
  heart_rate: "72",
  blood_pressure: "120",
  temperature: "36.8",
  spo2: "98",
  weight: "70.5",
  sleep: "7.5",
};

export function ReadingForm({ patientId, metrics, onAdded }: {
  patientId: number;
  metrics: MetricMeta[];
  onAdded: (r: AddReadingsResponse) => void;
}) {
  const blank = () =>
    Object.fromEntries(metrics.map((m) => [m.key, { value: "", value2: "", unit: m.unit }])) as Record<MetricKey, Field>;
  const [fields, setFields] = useState<Record<MetricKey, Field>>(blank);
  const [when, setWhen] = useState(nowLocal);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const set = (k: MetricKey, patch: Partial<Field>) => setFields((f) => ({ ...f, [k]: { ...f[k], ...patch } }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const entries = [];
    for (const m of metrics) {
      const f = fields[m.key];
      if (!f || (f.value === "" && f.value2 === "")) continue;
      const v = Number(f.value);
      if (f.value === "" || Number.isNaN(v)) return setErr(`${m.label}: enter a number.`);
      if (f.unit === m.unit && (v < m.min || v > m.max))
        return setErr(`${m.label}: ${v} ${m.unit} is outside the plausible range ${m.min}–${m.max}.`);
      if (m.key === "blood_pressure") {
        const v2 = Number(f.value2);
        if (f.value2 === "" || Number.isNaN(v2)) return setErr("Blood pressure: enter both systolic and diastolic.");
        if (v2 >= v) return setErr("Blood pressure: diastolic must be lower than systolic.");
        entries.push({ metric: m.key, value: v, value2: v2, unit: f.unit });
      } else entries.push({ metric: m.key, value: v, unit: f.unit });
    }
    if (!entries.length) return setErr("Enter at least one measurement.");
    const ts = new Date(when);
    if (Number.isNaN(ts.getTime())) return setErr("Enter a valid date and time.");
    if (ts.getTime() > Date.now() + 5 * 60000) return setErr("The timestamp cannot be in the future.");

    setBusy(true);
    try {
      const res = await api.post<AddReadingsResponse>(`/patients/${patientId}/readings`, {
        recorded_at: ts.toISOString(),
        entries,
      });
      setFields(blank());
      setWhen(nowLocal());
      onAdded(res);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card icon="plus" title="Record vitals" subtitle="Fill in any measurements taken — leave the rest blank">
      <form onSubmit={submit} className="space-y-5">
        <div className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
          {metrics.map((m) => {
            const units = m.accepted_units.filter((u) => u !== "C" && u !== "F" && u !== "h");
            return (
              <div key={m.key}>
                <label className="field-label flex items-center gap-1.5" htmlFor={`rf-${m.key}`}>
                  <Icon name={METRIC_ICON[m.key]} className="h-3.5 w-3.5 text-muted" />
                  {m.label}
                </label>
                <div className="flex gap-2">
                  <input id={`rf-${m.key}`} className="input tabular" inputMode="decimal"
                         placeholder={m.key === "blood_pressure" ? `Systolic ${PLACEHOLDER[m.key]}` : PLACEHOLDER[m.key]}
                         aria-label={m.key === "blood_pressure" ? "Systolic" : `${m.label} value`}
                         value={fields[m.key]?.value ?? ""} onChange={(e) => set(m.key, { value: e.target.value })} />
                  {m.key === "blood_pressure" && (
                    <input className="input tabular" inputMode="decimal" placeholder="Diastolic 80" aria-label="Diastolic"
                           value={fields[m.key]?.value2 ?? ""} onChange={(e) => set(m.key, { value2: e.target.value })} />
                  )}
                  {units.length > 1 ? (
                    <select className="input !w-24 shrink-0" aria-label={`${m.label} unit`} value={fields[m.key]?.unit ?? m.unit}
                            onChange={(e) => set(m.key, { unit: e.target.value })}>
                      {units.map((u) => <option key={u} value={u}>{u}</option>)}
                    </select>
                  ) : (
                    <span className="grid h-9 w-24 shrink-0 place-items-center rounded-lg bg-surface-2 text-xs text-muted">{m.unit}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div className="flex flex-wrap items-end justify-between gap-3 border-t border-line pt-4">
          <div>
            <label className="field-label" htmlFor="rf-when">Measured at</label>
            <input id="rf-when" type="datetime-local" className="input" value={when} max={nowLocal()}
                   onChange={(e) => setWhen(e.target.value)} />
          </div>
          <button className="btn btn-primary" disabled={busy}>
            <Icon name="check" /> {busy ? "Saving…" : "Save reading"}
          </button>
        </div>
        {err && <InlineError>{err}</InlineError>}
      </form>
    </Card>
  );
}

const SCENARIOS = [
  ["normal", "Normal day", "All vitals in typical ranges"],
  ["fever", "Fever episode", "Elevated temperature and heart rate"],
  ["low_oxygen", "Low oxygen", "Reduced SpO₂ saturation"],
  ["high_blood_pressure", "High blood pressure", "Elevated systolic and diastolic"],
  ["poor_sleep", "Poor sleep", "Short sleep duration"],
  ["tachycardia", "Fast heart rate", "Sustained high resting heart rate"],
] as const;

export function SimulatorCard({ patientId, onAdded }: { patientId: number; onAdded: (r: AddReadingsResponse) => void }) {
  const [scenario, setScenario] = useState<string>("normal");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      onAdded(await api.post<AddReadingsResponse>(`/patients/${patientId}/simulate`, { scenario }));
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card icon="flask" title="Scenario simulator" subtitle="Generate a simulated reading set to exercise the alert pipeline">
      <div className="grid gap-2" role="radiogroup" aria-label="Scenario">
        {SCENARIOS.map(([k, label, hint]) => (
          <button key={k} type="button" role="radio" aria-checked={scenario === k} onClick={() => setScenario(k)}
                  className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition ${
                    scenario === k ? "border-accent bg-accent/6" : "border-line hover:bg-surface-2"}`}>
            <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-full border-2 ${scenario === k ? "border-accent" : "border-line-strong"}`}>
              {scenario === k && <span className="h-1.5 w-1.5 rounded-full bg-accent" />}
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium">{label}</span>
              <span className="block text-xs text-muted">{hint}</span>
            </span>
          </button>
        ))}
      </div>
      <button className="btn btn-primary mt-4 w-full" onClick={run} disabled={busy}>
        <Icon name="activity" /> {busy ? "Generating…" : "Run simulation"}
      </button>
      {err && <div className="mt-3"><InlineError>{err}</InlineError></div>}
      <p className="mt-3 text-xs text-muted">Simulated readings are tagged “Simulated” wherever they appear.</p>
    </Card>
  );
}

/** Shows the deterministic pipeline that ran after readings were saved. */
export function PipelineCard({ result, onClose }: { result: AddReadingsResponse; onClose: () => void }) {
  const p = result.pipeline;
  const steps = p
    ? [
        { title: "Readings stored", detail: `${result.readings.length} reading(s) saved` },
        {
          title: "Monitoring Agent",
          detail: <>Retrieved {p.retrieved_via_mcp.length}/{result.readings.length} via <code className="font-mono">get_recent_readings</code></>,
        },
        {
          title: "Alert Agent",
          detail: <>{p.readings_evaluated} readings evaluated against rules v{p.rules_version}</>,
        },
        {
          title: "Result",
          detail: p.new_alerts.length === 0 ? "No new alerts" : `${p.new_alerts.length} new alert(s) raised`,
        },
      ]
    : [];

  return (
    <div className="animate-in rounded-xl border border-line bg-surface p-4 shadow-card" role="status">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className={`grid h-8 w-8 place-items-center rounded-full ${result.pipeline_error ? "bg-crit/10 text-crit" : "bg-good/12 text-good"}`}>
            <Icon name={result.pipeline_error ? "alertTriangle" : "check"} />
          </span>
          <div>
            <div className="text-sm font-semibold">
              {result.synthetic ? "Simulation complete" : "Reading saved"}
              {result.synthetic && result.scenario && <span className="font-normal text-muted"> · {result.scenario.replace(/_/g, " ")}</span>}
            </div>
            <div className="text-xs text-muted">Agent pipeline ran automatically</div>
          </div>
        </div>
        <button className="btn btn-sm btn-icon btn-ghost" onClick={onClose} aria-label="Dismiss"><Icon name="x" /></button>
      </div>

      {result.pipeline_error && <div className="mt-3"><InlineError>{result.pipeline_error}</InlineError></div>}
      {p?.warnings?.map((w) => (
        <p key={w} className="mt-3 flex items-center gap-1.5 text-sm text-warn"><Icon name="alertTriangle" className="h-3.5 w-3.5" />{w}</p>
      ))}

      {p && (
        <>
          <ol className="mt-4 grid gap-3 sm:grid-cols-4">
            {steps.map((s, i) => (
              <li key={s.title} className="flex gap-2.5">
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-accent/12 text-[11px] font-semibold text-accent">{i + 1}</span>
                <div className="min-w-0 text-xs">
                  <div className="font-medium text-ink">{s.title}</div>
                  <div className="text-muted">{s.detail}</div>
                </div>
              </li>
            ))}
          </ol>
          {p.new_alerts.length > 0 && (
            <ul className="mt-4 space-y-1.5 border-t border-line pt-3">
              {p.new_alerts.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <SeverityBadge severity={a.severity} />
                  <span>{a.message}</span>
                  <span className="text-xs text-muted tabular">observed {a.observed}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
