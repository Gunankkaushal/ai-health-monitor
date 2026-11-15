import { useState } from "react";
import { api, errorMessage } from "../api";
import type { Alert, RulesConfig, Severity } from "../types";
import {
  Card, Empty, ErrorBox, fmtRelative, fmtTime, Icon, InlineError, METRIC_ICON, Pill, Segmented, SEVERITY_ACCENT,
  SeverityBadge, Skeleton,
} from "./ui";

interface Explanation {
  mode: string;
  mode_label: string;
  text: string;
  error?: string | null;
}

const ORDER: Record<Severity, number> = { critical: 0, warning: 1, info: 2 };
const sortAlerts = (list: Alert[]) =>
  [...list].sort((a, b) => ORDER[a.severity] - ORDER[b.severity] || b.recorded_at.localeCompare(a.recorded_at));

export function AlertsPanel({ patientId, alerts, loading, error, rules, onChanged, onRetry }: {
  patientId: number;
  alerts: Alert[];
  loading: boolean;
  error: string | null;
  rules: RulesConfig | null;
  onChanged: () => void;
  onRetry: () => void;
}) {
  const [view, setView] = useState<"open" | "all">("open");
  const [showRules, setShowRules] = useState(false);
  const [explanations, setExplanations] = useState<Record<number, Explanation>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const sorted = sortAlerts(alerts.filter((a) => view === "all" || !a.acknowledged));
  const counts = alerts.filter((a) => !a.acknowledged).reduce(
    (acc, a) => ({ ...acc, [a.severity]: acc[a.severity] + 1 }),
    { critical: 0, warning: 0, info: 0 } as Record<Severity, number>,
  );

  const explain = async (a: Alert) => {
    setBusy(a.id);
    setErr(null);
    try {
      const r = await api.post<Explanation>(`/patients/${patientId}/alerts/${a.id}/explain`);
      setExplanations((e) => ({ ...e, [a.id]: r }));
      if (r.mode === "groq") onChanged();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const ack = async (a: Alert) => {
    setBusy(a.id);
    try {
      await api.post(`/patients/${patientId}/alerts/${a.id}/acknowledge`);
      onChanged();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        {(["critical", "warning", "info"] as const).map((s) => (
          <div key={s} className="flex items-center gap-3 rounded-xl border border-line bg-surface p-4 shadow-card">
            <span className={`h-9 w-1 rounded-full ${SEVERITY_ACCENT[s]}`} />
            <div>
              <div className="text-2xl font-semibold tabular">{counts[s]}</div>
              <div className="text-xs text-muted capitalize">Open {s}</div>
            </div>
          </div>
        ))}
      </div>

      <Card
        icon="bell"
        title="Alerts"
        subtitle="Raised by documented threshold rules, evaluated on every new reading"
        actions={
          <>
            <Segmented label="Alert filter" value={view} onChange={setView}
                       options={[{ value: "open", label: "Open" }, { value: "all", label: "All" }]} />
            <button className="btn btn-sm" onClick={() => setShowRules((v) => !v)} aria-expanded={showRules}>
              <Icon name="shield" /> {showRules ? "Hide rules" : "Rules"}
            </button>
          </>
        }
      >
        {showRules && <RulesTable rules={rules} />}
        {loading && <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24" />)}</div>}
        {error && <ErrorBox message={error} onRetry={onRetry} />}
        {err && <div className="mb-3"><InlineError>{err}</InlineError></div>}
        {!loading && !error && sorted.length === 0 && (
          <Empty icon="check" title={view === "open" ? "No open alerts" : "No alerts"}>
            All readings are within the configured rule thresholds.
          </Empty>
        )}
        <ul className="space-y-3">
          {sorted.slice(0, 40).map((a) => {
            const ex = explanations[a.id];
            const aiText = ex ? ex.text : a.ai_explanation;
            return (
              <li key={a.id}
                  className={`relative overflow-hidden rounded-xl border border-line bg-surface pl-4 ${a.acknowledged ? "opacity-65" : ""}`}>
                <span className={`absolute inset-y-0 left-0 w-1 ${SEVERITY_ACCENT[a.severity]}`} />
                <div className="p-4 pl-1">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <SeverityBadge severity={a.severity} />
                      <span className="text-sm font-semibold">{a.message}</span>
                    </div>
                    <span className="text-xs text-muted" title={fmtTime(a.recorded_at)}>{fmtRelative(a.recorded_at)}</span>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
                    <span className="inline-flex items-center gap-1.5">
                      <Icon name={METRIC_ICON[a.metric]} className="h-3.5 w-3.5 text-muted" />
                      Observed <strong className="font-semibold text-ink tabular">{a.observed}</strong>
                    </span>
                    <span>Condition <code className="rounded bg-surface-2 px-1 font-mono text-[11px]">{a.threshold}</code></span>
                    <span className="text-muted">Rule <span className="font-mono">{a.rule_id}</span></span>
                    {a.acknowledged && <Pill tone="good"><Icon name="check" className="h-3 w-3" />Acknowledged</Pill>}
                  </div>

                  <p className="mt-2.5 text-sm text-ink-2">{a.explanation}</p>
                  <p className="mt-1 text-xs text-muted">Source: {a.source}</p>

                  {aiText && (
                    <div className="mt-3 rounded-lg bg-accent/6 p-3 ring-1 ring-inset ring-accent/15">
                      <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-accent">
                        <Icon name="sparkles" className="h-3.5 w-3.5" />
                        {ex ? ex.mode_label : "AI explanation"}
                      </div>
                      {ex?.error && <div className="mb-1 text-xs text-crit">{ex.error}</div>}
                      <p className="text-sm whitespace-pre-wrap text-ink">{aiText}</p>
                    </div>
                  )}

                  <div className="mt-3 flex gap-2">
                    <button className="btn btn-sm" disabled={busy === a.id} onClick={() => explain(a)}>
                      <Icon name="sparkles" /> {busy === a.id ? "Working…" : aiText ? "Re-explain" : "Explain"}
                    </button>
                    {!a.acknowledged && (
                      <button className="btn btn-sm btn-ghost" disabled={busy === a.id} onClick={() => ack(a)}>
                        <Icon name="check" /> Acknowledge
                      </button>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
        {sorted.length > 40 && <p className="mt-3 text-xs text-muted">Showing 40 of {sorted.length}. Download a report for the full list.</p>}
      </Card>
    </div>
  );
}

/** Compact list for the overview tab. */
export function AlertsPreview({ alerts, loading, onViewAll }: { alerts: Alert[]; loading: boolean; onViewAll: () => void }) {
  const open = sortAlerts(alerts.filter((a) => !a.acknowledged));
  return (
    <Card icon="bell" title="Open alerts" subtitle={`${open.length} require review`} flush
          actions={<button className="btn btn-sm btn-ghost" onClick={onViewAll}>View all <Icon name="chevronRight" /></button>}>
      {loading && <div className="space-y-2 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>}
      {!loading && open.length === 0 && (
        <div className="p-5"><Empty icon="check" title="All clear">No open alerts for this patient.</Empty></div>
      )}
      <ul className="divide-y divide-line">
        {open.slice(0, 5).map((a) => (
          <li key={a.id} className="flex items-center gap-3 px-5 py-3">
            <span className={`h-8 w-1 shrink-0 rounded-full ${SEVERITY_ACCENT[a.severity]}`} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{a.message}</div>
              <div className="text-xs text-muted">Observed <span className="tabular">{a.observed}</span> · {fmtRelative(a.recorded_at)}</div>
            </div>
            <SeverityBadge severity={a.severity} />
          </li>
        ))}
      </ul>
    </Card>
  );
}

function RulesTable({ rules }: { rules: RulesConfig | null }) {
  if (!rules)
    return <div className="mb-4"><InlineError>Rules are unavailable — the Health Rules server is not connected.</InlineError></div>;
  return (
    <div className="animate-in mb-5 overflow-hidden rounded-xl border border-line">
      <div className="border-b border-line bg-surface-2/60 px-4 py-3 text-xs text-ink-2">
        <strong className="text-ink">Rule set v{rules.version}.</strong> {rules.disclaimer} Population: {rules.population}
      </div>
      <div className="max-h-80 overflow-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface text-left text-muted">
            <tr className="border-b border-line">
              <th className="px-4 py-2 font-medium">Rule</th>
              <th className="px-3 py-2 font-medium">Condition</th>
              <th className="px-3 py-2 font-medium">Severity</th>
              <th className="px-3 py-2 font-medium">Source</th>
              <th className="px-4 py-2 font-medium">Limitations</th>
            </tr>
          </thead>
          <tbody>
            {rules.rules.map((r) => (
              <tr key={r.id} className="border-b border-line align-top last:border-0">
                <td className="px-4 py-2 font-mono">{r.id}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  {r.conditions.map((c) => `${r.metric === "blood_pressure" ? (c.field === "value" ? "sys" : "dia") : r.metric} ${c.op} ${c.threshold}`).join(` ${r.match === "any" ? "or" : "and"} `)}
                </td>
                <td className="px-3 py-2"><SeverityBadge severity={r.severity} /></td>
                <td className="px-3 py-2 text-ink-2">{r.source}</td>
                <td className="px-4 py-2 text-ink-2">{r.limitations}</td>
              </tr>
            ))}
            {rules.trend_rules.map((r) => (
              <tr key={r.id} className="border-b border-line align-top last:border-0">
                <td className="px-4 py-2 font-mono">{r.id}</td>
                <td className="px-3 py-2">{r.metric} change ≥ {r.threshold} in {r.days} d</td>
                <td className="px-3 py-2"><SeverityBadge severity={r.severity} /></td>
                <td className="px-3 py-2 text-ink-2">{r.source}</td>
                <td className="px-4 py-2 text-ink-2">{r.limitations}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
