import { useState } from "react";
import { api } from "../api";
import { Card, Empty, ErrorBox, Icon, Skeleton } from "../components/ui";
import {
  Callout, fmtMetric, KeyVal, relTime, ScoreValue, SectionLabel, SevPill, StatusPill, Table, useAction, useApi, VerdictPill,
} from "./common";
import type { MetricMetaR, RunDetail, RunSummary } from "./types";

export function RunList({ go }: { go: (p: string) => void }) {
  const { data, error, reload } = useApi<RunSummary[]>("/resilience/runs?limit=100", 4000);
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  return (
    <Card icon="clock" title="Runs" subtitle="Every request, including refused ones" flush>
      {!data ? <div className="p-5"><Skeleton className="h-40" /></div> : data.length === 0 ? (
        <div className="p-5"><Empty icon="clock" title="No runs yet">Run an experiment to see it here.</Empty></div>
      ) : (
        <Table head={["Run", "Experiment", "Status", "Verdict", "Score", "Requested by", "When"]} minWidth={900}>
          {data.map((r) => (
            <tr key={r.id} className="cursor-pointer hover:bg-surface-2/50" onClick={() => go(`runs/${r.id}`)}>
              <td className="font-mono text-xs">{r.label}{r.dry_run ? " · dry" : ""}{r.suite_run_id ? " · suite" : ""}</td>
              <td><div className="text-sm">{r.name}</div>{r.status === "refused" && <div className="max-w-md truncate text-xs text-crit" title={r.reason}>{r.reason}</div>}</td>
              <td><StatusPill status={r.status} /></td>
              <td><VerdictPill verdict={r.verdict} /></td>
              <td className="tabular">{r.score ?? "—"}</td>
              <td className="text-xs text-ink-2">{r.requested_by}{r.break_glass && <span className="text-warn"> · break glass</span>}</td>
              <td className="text-xs text-muted">{relTime(r.created_at)}</td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

const PHASES = [
  { key: "baseline", label: "Baseline", detail: "steady state, no fault" },
  { key: "fault", label: "Inject", detail: "fault armed on the target edge" },
  { key: "recovery", label: "Recovery", detail: "fault removed, steady state again?" },
  { key: "done", label: "Analyse & clean up", detail: "verdict, findings, data removed" },
];

export function RunDetailView({ id, go }: { id: number; go: (p: string) => void }) {
  const { data, error, reload } = useApi<RunDetail>(`/resilience/runs/${id}`, 1000);
  const metrics = useApi<{ metrics: Record<string, MetricMetaR> }>("/resilience/journeys");
  const act = useAction();
  const [showIt, setShowIt] = useState(false);
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <Skeleton className="h-96" />;
  const M = metrics.data?.metrics ?? {};
  const live = data.status === "running" || data.status === "queued";
  const phaseIdx = PHASES.findIndex((p) => p.key === (data.phase === "observe" ? "fault" : data.phase));
  const it = data.results.iterations;
  const checks = data.results.checks ?? data.spec.steady_state ?? [];
  const agg = (phase: "baseline" | "fault" | "recovery", metric: string) => {
    const vals = (it?.[phase] ?? []).map((x) => x.metrics[metric]).filter((v): v is number => v != null);
    if (!vals.length) return null;
    if (M[metric]?.kind === "latency") return [...vals].sort((a, b) => a - b)[Math.min(vals.length - 1, Math.floor(vals.length * 0.95))];
    if (M[metric]?.kind === "count") return vals.reduce((a, b) => a + b, 0);
    return Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 10) / 10;
  };
  const abort = async () => {
    await act.run("abort", () => api.post(`/resilience/runs/${id}/abort`, { reason: "stopped from the live run view" }));
    reload();
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-muted">Runs / {data.label} · journey: {data.journey?.name}</div>
          <h2 className="text-lg font-semibold tracking-tight">{data.name}</h2>
          <p className="text-sm text-muted">
            {data.edge?.id} {data.edge?.agent} → {data.edge?.tool} · {data.dry_run ? "dry run (no fault)" : `${data.fault?.name} on ${data.spec.share_pct}% of calls`} ·
            patients {data.spec.patient_scope?.join(", ")} · requested by {data.requested_by}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill status={data.status} />
          {live && <button className="btn btn-danger-solid" onClick={abort} disabled={act.busy !== null}><Icon name="x" /> Abort run</button>}
          {data.experiment_id && !live && <button className="btn btn-sm" onClick={() => go(`experiments/${data.experiment_id}`)}>Experiment</button>}
        </div>
      </div>
      {act.error && <ErrorBox message={act.error} />}
      {data.break_glass && <Callout tone="warn" title="Break-glass run">Started without the required approvals: {data.break_glass}</Callout>}
      {data.status === "refused" && <Callout tone="crit" title="Refused before it started">{data.reason}</Callout>}

      {data.status !== "refused" && (
        <Card title="Progress">
          <div className="grid gap-2 sm:grid-cols-4">
            {PHASES.map((p, i) => {
              const cur = i === phaseIdx && live;
              const done = i < phaseIdx || !live;
              return (
                <div key={p.key} className={`rounded-lg border px-3 py-2.5 ${cur ? "border-accent bg-accent/6" : "border-line"}`}>
                  <div className="flex items-center gap-1.5 text-sm font-semibold">
                    {done && !cur ? <Icon name="check" className="h-3.5 w-3.5 text-good" /> : cur ? <span className="h-2 w-2 animate-pulse rounded-full bg-accent" /> : <span className="h-2 w-2 rounded-full bg-line-strong" />}
                    {p.label}
                  </div>
                  <div className="mt-0.5 text-xs text-muted">{cur ? "in progress · " : ""}{p.detail}
                    {p.key !== "done" && it?.[p.key as "baseline"] ? ` · ${it[p.key as "baseline"].length} iteration(s)` : ""}</div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {!live && data.status !== "refused" && (
        <div className={`rounded-xl border p-5 ${data.verdict === "held" || data.verdict === "dry_run_passed" ? "border-good/40 bg-good/5" : data.verdict === "rejected" ? "border-crit/40 bg-crit/5" : "border-warn/40 bg-warn/5"}`}>
          <div className="flex flex-wrap items-center gap-4">
            <VerdictPill verdict={data.verdict} />
            {data.score != null && <ScoreValue score={data.score} size="md" />}
            {data.results.recovered != null && <span className="text-sm text-ink-2">Recovery: {data.results.recovered ? `restored${data.results.recovery_time_s != null ? ` in ${data.results.recovery_time_s}s` : ""}` : "not restored"}</span>}
            {data.results.cleaned_up_readings ? <span className="text-sm text-muted">{data.results.cleaned_up_readings} experiment readings removed</span> : null}
          </div>
          <p className="mt-2 text-sm">{data.reason}</p>
          {data.results.score_breakdown && <p className="mt-1 text-xs text-muted">Steady in {Math.round(data.results.score_breakdown.steady_fraction * 100)}% of fault iterations · {data.results.score_breakdown.formula}</p>}
        </div>
      )}

      {checks.length > 0 && data.status !== "refused" && (
        <div>
          <SectionLabel>Steady state — live vs baseline</SectionLabel>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {checks.map((c) => {
              const base = agg("baseline", c.metric);
              const during = agg("fault", c.metric);
              const rec = agg("recovery", c.metric);
              const ok = during == null ? null : c.op === ">=" ? during >= c.threshold : during <= c.threshold;
              return (
                <div key={c.metric} className={`rounded-xl border bg-surface p-4 shadow-card ${ok === false ? "border-crit/50" : "border-line"}`}>
                  <div className="text-xs text-muted">{M[c.metric]?.label ?? c.metric}</div>
                  <div className={`mt-1 text-2xl font-semibold tabular ${ok === false ? "text-crit" : ""}`}>{fmtMetric(M[c.metric], during ?? base)}</div>
                  <div className="mt-1 text-xs text-ink-2">baseline {fmtMetric(M[c.metric], base)} · needs {c.op} {fmtMetric(M[c.metric], c.threshold)}</div>
                  <div className="text-xs text-muted">recovery {fmtMetric(M[c.metric], rec)}{c.abort ? " · aborts run" : ""}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-5">
          {data.results.cascade && (
            <Card icon="activity" title="Cascade — what the fault reached" flush>
              <Table head={["Node", "Effect observed", "First seen", "Hop"]} minWidth={640}>
                {data.results.cascade.map((r, i) => (
                  <tr key={i}>
                    <td className="font-medium">{r.node}</td>
                    <td className="text-ink-2">{r.effect}</td>
                    <td className="tabular text-xs text-muted">{r.first_seen_s != null ? `+${r.first_seen_s}s` : "—"}</td>
                    <td className="text-xs">{r.hop}</td>
                  </tr>
                ))}
              </Table>
              {data.results.fault_stats && (
                <dl className="grid grid-cols-2 gap-3 border-t border-line px-5 py-4 sm:grid-cols-4">
                  <KeyVal k="Calls faulted" v={`${data.results.fault_stats.injected} of ${data.results.fault_stats.target_calls}`} />
                  <KeyVal k="Retries" v={data.results.fault_stats.retries} />
                  <KeyVal k="Call amplification" v={data.results.fault_stats.amplification != null ? `${data.results.fault_stats.amplification}×` : "—"} />
                  <KeyVal k="Contained to target" v={data.results.fault_stats.contained ? "yes" : "no"} />
                </dl>
              )}
            </Card>
          )}

          {data.findings.length > 0 && (
            <Card icon="alertTriangle" title="Findings from this run" flush actions={<button className="btn btn-sm btn-ghost" onClick={() => go("findings")}>All findings <Icon name="chevronRight" /></button>}>
              <ul className="divide-y divide-line">
                {data.findings.map((f) => (
                  <li key={f.id} className="px-5 py-3">
                    <div className="flex flex-wrap items-center gap-2"><SevPill severity={f.severity} /><span className="text-sm font-medium">{f.title}</span></div>
                    <p className="mt-1 text-sm text-ink-2">{f.what_happened}</p>
                    <p className="mt-1 text-xs text-muted">Guardrail: {f.guardrail}</p>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {it && (
            <Card title="Iterations" subtitle="Every journey execution, by phase" flush
                  actions={<button className="btn btn-sm btn-ghost" onClick={() => setShowIt((v) => !v)}>{showIt ? "Hide" : "Show"}</button>}>
              {showIt && (
                <Table head={["#", "Phase", "Patient", "Result", "Latency", "Detail"]} minWidth={760}>
                  {(["baseline", "fault", "recovery"] as const).flatMap((ph) => it[ph].map((x) => (
                    <tr key={`${ph}-${x.i}`}>
                      <td className="tabular text-xs">{x.i}</td>
                      <td className="text-xs capitalize">{ph}</td>
                      <td className="text-xs">{x.patient_id}</td>
                      <td>{x.ok == null ? <span className="text-xs text-muted">skipped</span> : x.holds ? <span className="text-xs text-good">steady</span> : <span className="text-xs text-crit">breach</span>}</td>
                      <td className="tabular text-xs">{x.latency_ms != null ? `${x.latency_ms} ms` : "—"}</td>
                      <td className="text-xs text-ink-2">{x.note}{x.error && <span className="block text-crit">{x.error}</span>}</td>
                    </tr>
                  )))}
                </Table>
              )}
            </Card>
          )}
        </div>

        <Card icon="list" title="Event log">
          <ol className="space-y-2">
            {data.events.map((e, i) => (
              <li key={i} className="flex gap-3 text-xs">
                <span className="w-12 shrink-0 tabular text-muted">+{e.t_s}s</span>
                <span className="text-ink-2">{e.msg}</span>
              </li>
            ))}
            {data.events.length === 0 && <li className="text-sm text-muted">{live ? "Starting…" : "No events."}</li>}
          </ol>
        </Card>
      </div>
    </div>
  );
}
