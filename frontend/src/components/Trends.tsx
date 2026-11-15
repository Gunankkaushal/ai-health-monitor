import { useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, errorMessage } from "../api";
import type { MetricKey, MetricMeta, Reading, RulesConfig } from "../types";
import {
  Card, Empty, fmtRelative, fmtTime, Icon, InlineError, METRIC_ICON, parseUtc, readingSeverity, Segmented, SourceTag,
} from "./ui";

const fmtValue = (m: MetricKey, r: Reading) => (m === "blood_pressure" ? `${r.value}/${r.value2}` : `${r.value}`);

function Sparkline({ values, tone }: { values: number[]; tone: string }) {
  if (values.length < 2) return <div className="h-8" />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${28 - ((v - min) / span) * 24}`).join(" ");
  return (
    <svg viewBox="0 0 100 32" preserveAspectRatio="none" className="h-8 w-full" aria-hidden>
      <polyline points={pts} fill="none" stroke={tone} strokeWidth={1.75} vectorEffect="non-scaling-stroke"
                strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

const STATUS_STYLE = {
  normal: { label: "Normal", cls: "text-good", line: "var(--st-good)" },
  info: { label: "Note", cls: "text-info", line: "var(--st-info)" },
  warning: { label: "Warning", cls: "text-warn", line: "var(--st-warning)" },
  critical: { label: "Critical", cls: "text-crit", line: "var(--st-critical)" },
} as const;

export function VitalsTiles({ metrics, readings, rules, selected, onSelect }: {
  metrics: MetricMeta[];
  readings: Reading[];
  rules: RulesConfig | null;
  selected: MetricKey;
  onSelect: (m: MetricKey) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6" role="tablist" aria-label="Choose metric to chart">
      {metrics.map((m) => {
        const series = readings.filter((r) => r.metric === m.key); // newest-first
        const latest = series[0];
        const prev = series[1];
        const status = readingSeverity(rules, latest);
        const st = status ? STATUS_STYLE[status] : null;
        const active = m.key === selected;
        const delta = latest && prev ? latest.value - prev.value : null;
        return (
          <button key={m.key} role="tab" aria-selected={active} onClick={() => onSelect(m.key)}
                  className={`group rounded-xl border bg-surface p-4 text-left shadow-card transition ${
                    active ? "border-accent ring-2 ring-accent/15" : "border-line hover:border-line-strong"}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-2 text-xs font-medium text-ink-2">
                <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-md ${active ? "bg-accent/12 text-accent" : "bg-surface-2 text-muted"}`}>
                  <Icon name={METRIC_ICON[m.key]} className="h-3.5 w-3.5" />
                </span>
                <span className="truncate">{m.label}</span>
              </span>
              {st && <span className={`text-[11px] font-semibold ${st.cls}`}>{st.label}</span>}
            </div>
            <div className="mt-3 flex items-baseline gap-1.5">
              <span className="text-2xl font-semibold tracking-tight tabular">{latest ? fmtValue(m.key, latest) : "—"}</span>
              <span className="text-xs text-muted">{m.unit}</span>
              {delta !== null && delta !== 0 && (
                <span className="ml-auto inline-flex items-center text-[11px] text-muted tabular">
                  <Icon name={delta > 0 ? "arrowUp" : "arrowDown"} className="h-3 w-3" />
                  {Math.abs(Math.round(delta * 10) / 10)}
                </span>
              )}
            </div>
            <div className="mt-2">
              <Sparkline values={series.slice(0, 20).map((r) => r.value).reverse()}
                         tone={st && status !== "normal" ? st.line : "var(--series-1)"} />
            </div>
            <div className="mt-1 truncate text-[11px] text-muted">{latest ? fmtRelative(latest.recorded_at) : "No readings"}</div>
          </button>
        );
      })}
    </div>
  );
}

const OP_LABEL: Record<string, string> = { ">": ">", ">=": "≥", "<": "<", "<=": "≤" };

export function TrendChart({ metric, meta, readings, rules, days, onDays }: {
  metric: MetricKey;
  meta: MetricMeta | undefined;
  readings: Reading[];
  rules: RulesConfig | null;
  days: number;
  onDays: (d: number) => void;
}) {
  const data = useMemo(
    () =>
      readings
        .filter((r) => r.metric === metric)
        .map((r) => ({ t: parseUtc(r.recorded_at).getTime(), value: r.value, value2: r.value2, source: r.source }))
        .sort((a, b) => a.t - b.t),
    [readings, metric],
  );

  // Warning thresholds come from the Health Rules server — drawn, never invented.
  const thresholds = useMemo(() => {
    if (!rules) return [];
    return rules.rules
      .filter((r) => r.metric === metric && r.severity === "warning")
      .flatMap((r) => r.conditions.map((c) => ({
        y: c.threshold,
        label: `${metric === "blood_pressure" ? (c.field === "value" ? "Sys " : "Dia ") : ""}${OP_LABEL[c.op]} ${c.threshold}`,
        id: r.id,
      })));
  }, [rules, metric]);

  const isBP = metric === "blood_pressure";
  const unit = meta?.unit ?? "";
  const stats = useMemo(() => {
    if (!data.length) return null;
    const vals = data.map((d) => d.value);
    return {
      min: Math.min(...vals),
      max: Math.max(...vals),
      avg: Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 10) / 10,
    };
  }, [data]);

  return (
    <Card
      icon={METRIC_ICON[metric]}
      title={`${meta?.label ?? metric} trend`}
      subtitle={rules ? "Dashed lines mark warning thresholds" : `Last ${days} days`}
      actions={
        <Segmented label="Time range" value={days} onChange={onDays}
                   options={[7, 30, 90].map((d) => ({ value: d, label: `${d}D` }))} />
      }
    >
      {data.length === 0 ? (
        <Empty icon="activity" title="No readings in this period">
          No {meta?.label.toLowerCase()} readings were recorded in the last {days} days.
        </Empty>
      ) : (
        <>
          {stats && (
            <dl className="mb-4 flex flex-wrap gap-x-8 gap-y-2">
              {([["Readings", data.length], [isBP ? "Avg systolic" : "Average", stats.avg], ["Min", stats.min], ["Max", stats.max]] as const).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-xs text-muted">{k}</dt>
                  <dd className="text-lg font-semibold tabular">
                    {v}{k !== "Readings" && <span className="ml-1 text-xs font-normal text-muted">{unit}</span>}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={data} margin={{ top: 8, right: 64, bottom: 0, left: -8 }}>
                <defs>
                  <linearGradient id="fill-1" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--series-1)" stopOpacity={0.18} />
                    <stop offset="100%" stopColor="var(--series-1)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis
                  dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]}
                  tickFormatter={(t) => new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                  stroke="var(--axis)" tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} minTickGap={24}
                />
                <YAxis
                  domain={["auto", "auto"]} width={48} tickLine={false} axisLine={false}
                  tick={{ fill: "var(--muted)", fontSize: 11 }}
                />
                <Tooltip
                  cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
                  contentStyle={{
                    background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10,
                    fontSize: 12, color: "var(--ink)", boxShadow: "var(--shadow-md)",
                  }}
                  labelStyle={{ color: "var(--muted)", marginBottom: 4 }}
                  labelFormatter={(t) => new Date(Number(t)).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                  formatter={(v, name) => [`${v} ${unit}`, name]}
                />
                {thresholds.map((th) => (
                  <ReferenceLine key={th.id + th.label} y={th.y} stroke="var(--st-warning)" strokeOpacity={0.7} strokeDasharray="4 4"
                                 label={{ value: th.label, position: "right", fill: "var(--muted)", fontSize: 10 }} />
                ))}
                <Area type="monotone" dataKey="value" name={isBP ? "Systolic" : meta?.label} stroke="var(--series-1)"
                      strokeWidth={2} fill="url(#fill-1)" dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
                      isAnimationActive={false} />
                {isBP && (
                  <Line type="monotone" dataKey="value2" name="Diastolic" stroke="var(--series-2)" strokeWidth={2}
                        dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }} isAnimationActive={false} />
                )}
                {isBP && <Legend verticalAlign="top" align="right" height={28} iconType="plainline"
                                 wrapperStyle={{ fontSize: 12, color: "var(--ink-2)" }} />}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </Card>
  );
}

export function ReadingsTable({ readings, patientId, onChanged }: {
  readings: Reading[];
  patientId: number;
  onChanged: () => void;
}) {
  const [filter, setFilter] = useState<string>("all");
  const [limit, setLimit] = useState(15);
  const [err, setErr] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<number | null>(null);
  const rows = readings.filter((r) => filter === "all" || r.metric === filter);

  const remove = async (id: number) => {
    setErr(null);
    try {
      await api.del(`/patients/${patientId}/readings/${id}`);
      setPendingDelete(null);
      onChanged();
    } catch (e) {
      setErr(errorMessage(e));
    }
  };

  return (
    <Card
      icon="list"
      title="Reading history"
      subtitle={`${rows.length} reading${rows.length === 1 ? "" : "s"} in the selected period`}
      flush
      actions={
        <select className="input input-sm !w-auto" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter metric">
          <option value="all">All metrics</option>
          {[...new Set(readings.map((r) => r.metric))].map((m) => (
            <option key={m} value={m}>{readings.find((r) => r.metric === m)?.label}</option>
          ))}
        </select>
      }
    >
      {err && <div className="px-5 pt-4"><InlineError>{err}</InlineError></div>}
      {rows.length === 0 ? (
        <div className="p-5"><Empty icon="list" title="No readings yet">Add a reading or run a simulation to populate history.</Empty></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line bg-surface-2/60 text-left text-xs font-medium text-muted">
                <th className="px-5 py-2.5 font-medium">Recorded</th>
                <th className="px-3 py-2.5 font-medium">Metric</th>
                <th className="px-3 py-2.5 text-right font-medium">Value</th>
                <th className="px-3 py-2.5 font-medium">Source</th>
                <th className="w-28 px-5 py-2.5" />
              </tr>
            </thead>
            <tbody className="tabular">
              {rows.slice(0, limit).map((r) => (
                <tr key={r.id} className="group border-b border-line last:border-0 hover:bg-surface-2/50">
                  <td className="px-5 py-2.5 whitespace-nowrap text-ink-2">{fmtTime(r.recorded_at)}</td>
                  <td className="px-3 py-2.5">
                    <span className="inline-flex items-center gap-2">
                      <Icon name={METRIC_ICON[r.metric]} className="h-3.5 w-3.5 text-muted" />
                      {r.label}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right font-medium whitespace-nowrap">{r.display}</td>
                  <td className="px-3 py-2.5"><SourceTag source={r.source} /></td>
                  <td className="px-5 py-2.5 text-right">
                    {pendingDelete === r.id ? (
                      <span className="inline-flex gap-1">
                        <button className="btn btn-sm btn-danger-solid" onClick={() => remove(r.id)}>Delete</button>
                        <button className="btn btn-sm btn-icon btn-ghost" onClick={() => setPendingDelete(null)} aria-label="Cancel">
                          <Icon name="x" />
                        </button>
                      </span>
                    ) : (
                      <button className="btn btn-sm btn-icon btn-ghost !text-muted hover:!text-crit"
                              onClick={() => setPendingDelete(r.id)} aria-label="Delete reading" title="Delete reading">
                        <Icon name="trash" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > limit && (
            <div className="border-t border-line px-5 py-3">
              <button className="btn btn-sm btn-ghost" onClick={() => setLimit((l) => l + 30)}>
                Show more <span className="text-muted">({rows.length - limit} remaining)</span>
              </button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
