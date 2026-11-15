import { Card, ErrorBox, Icon, Pill, Segmented, Skeleton } from "../components/ui";
import { fmtMetric, ScoreValue, Table, useAction, useApi } from "./common";
import { api } from "../api";
import type { Journey, MetricMetaR } from "./types";

export function Journeys({ id, go }: { id?: string; go: (p: string) => void }) {
  const { data, error, reload } = useApi<{ journeys: Journey[]; metrics: Record<string, MetricMetaR>; score_formula: string }>("/resilience/journeys");
  const act = useAction();
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <Skeleton className="h-96" />;
  const j = data.journeys.find((x) => x.id === id) ?? data.journeys[0];

  const starter = async () => {
    const r = await act.run("starter", () => api.post<{ suite: { id: number } }>(`/resilience/journeys/${j.id}/starter-suite`));
    if (r) go("suites");
  };

  return (
    <div className="space-y-5">
      <Segmented label="Journey" value={j.id} onChange={(v) => go(`journeys/${v}`)}
                 options={data.journeys.map((x) => ({ value: x.id, label: x.name }))} />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{j.name} — every edge</h2>
          <p className="mt-0.5 text-sm text-muted">SLO: {j.slo} · owner {j.owner}</p>
          <p className="mt-1 max-w-3xl text-xs text-ink-2">Workload: {j.workload}</p>
        </div>
        <button className="btn btn-sm" onClick={starter} disabled={act.busy !== null}>
          <Icon name="flask" /> {act.busy ? "Creating…" : "Generate starter suite"}
        </button>
      </div>
      {act.error && <ErrorBox message={act.error} />}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="rounded-xl border border-line bg-surface p-4 shadow-card"><div className="text-xs text-muted">Journey score</div><div className="mt-1"><ScoreValue score={j.score} /></div></div>
        <div className="rounded-xl border border-line bg-surface p-4 shadow-card"><div className="text-xs text-muted">Edges never tested</div><div className={`mt-1 text-3xl font-semibold tabular ${j.never_tested ? "text-warn" : ""}`}>{j.never_tested}<span className="text-sm font-normal text-muted"> of {j.total_edges}</span></div></div>
        <div className="rounded-xl border border-line bg-surface p-4 shadow-card"><div className="text-xs text-muted">Of those, critical path</div><div className={`mt-1 text-3xl font-semibold tabular ${j.never_tested_critical ? "text-crit" : ""}`}>{j.never_tested_critical}</div></div>
        <div className="rounded-xl border border-line bg-surface p-4 shadow-card"><div className="text-xs text-muted">Write-path edges</div><div className="mt-1 text-3xl font-semibold tabular">{j.write_edges}</div></div>
      </div>

      {j.stages.map((st, i) => (
        <Card key={st.name} title={`Stage ${i + 1} · ${st.name}`} subtitle={st.detail} flush
              className={st.edges.some((e) => e.direction === "write") ? "!border-warn/40" : ""}>
          <Table head={["Edge", "R/W", "Faults that apply", "Coverage", "Edge score", ""]} minWidth={900}>
            {st.edges.map((e) => (
              <tr key={e.key}>
                <td>
                  <span className="font-mono text-xs text-muted">{e.id}</span>{" "}
                  <code className="font-mono text-xs">{e.tool === "llm" ? "Groq model" : e.tool}</code>
                  {j.critical_edges.includes(e.key) && <span className="ml-2"><Pill tone="info">critical</Pill></span>}
                </td>
                <td className={e.direction === "write" ? "font-semibold text-warn" : ""}>{e.direction === "write" ? "W" : "R"}</td>
                <td className="text-xs text-ink-2">{e.eligible_faults.length ? e.eligible_faults.join(" · ") : <span className="text-muted">none eligible in this mode</span>}</td>
                <td className={`text-xs ${e.coverage === "never tested" ? "text-warn" : e.last_verdict === "rejected" ? "text-crit" : "text-ink-2"}`}>
                  {e.last_run_id ? <button className="underline-offset-2 hover:underline" onClick={() => go(`runs/${e.last_run_id}`)}>{e.coverage}</button> : e.coverage}
                </td>
                <td className="tabular">{e.edge_score ?? "—"}</td>
                <td className="text-right">
                  {e.eligible_faults.length > 0 && (
                    <button className="btn btn-sm btn-ghost" onClick={() => go(`experiments/new?edge=${encodeURIComponent(e.key)}&journey=${j.id}`)}>
                      <Icon name="plus" /> Test
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      ))}

      <Card icon="shield" title="Steady state for this journey" subtitle="Default checks every experiment on this journey starts from">
        <ul className="grid gap-2 sm:grid-cols-2">
          {j.steady_state.map((c) => (
            <li key={c.metric} className="flex items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-sm">
              <span>{data.metrics[c.metric]?.label}</span>
              <span className="flex items-center gap-2 tabular text-ink-2">{c.op} {fmtMetric(data.metrics[c.metric], c.threshold)}
                {c.abort && <Pill tone="warn">aborts run</Pill>}</span>
            </li>
          ))}
        </ul>
        <p className="mt-4 rounded-lg bg-surface-2 p-3 text-xs leading-relaxed text-ink-2">
          <strong className="text-ink">How the scopes nest.</strong> The journey carries the SLO and the score. Each edge is one
          possible injection point. One experiment targets one edge with one fault, so a run attributes the damage to a single cause.
          A suite is the set of experiments covering the journey's edges — that is what gates a release. {data.score_formula}.
        </p>
      </Card>
    </div>
  );
}
