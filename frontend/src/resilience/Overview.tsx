import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Empty, ErrorBox, Icon, Skeleton } from "../components/ui";
import { relTime, ScoreValue, StatusPill, Table, useApi, VerdictPill } from "./common";
import type { Edge, Journey, RunSummary } from "./types";

interface OverviewData {
  journeys: (Journey & { open_findings: Record<string, number> })[];
  coverage: { edges_tested: number; edges_testable: number; edges_total: number; critical_untested: number; agents_exercised: number; agents_total: number };
  untested_critical: { edge: Edge; journey_ids: string[] }[];
  open_findings: Record<string, number>;
  recent_runs: RunSummary[];
  score_formula: string;
}

const LINE_COLORS = ["var(--series-1)", "var(--series-2)", "var(--st-good)", "var(--st-warning)"];

export function Overview({ go }: { go: (p: string) => void }) {
  const { data, error, reload } = useApi<OverviewData>("/resilience/overview", 10000);
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-40" />)}</div>;

  const series = data.journeys.filter((j) => j.trend.length);
  const points: Record<string, number | string>[] = [];
  series.forEach((j) => j.trend.forEach((p) => points.push({ run: p.run_id, [j.id]: p.score })));
  points.sort((a, b) => Number(a.run) - Number(b.run));

  return (
    <div className="space-y-5">
      <div>
        <div className="mb-2 text-[11px] font-semibold tracking-wider text-muted uppercase">Journeys — what the product promises</div>
        <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
          {data.journeys.map((j) => {
            const high = j.open_findings.high;
            return (
              <button key={j.id} onClick={() => go(`journeys/${j.id}`)}
                      className={`rounded-xl border bg-surface p-4 text-left shadow-card transition hover:border-line-strong ${high ? "border-crit/50" : "border-line"}`}>
                <div className="text-sm font-semibold">{j.name}</div>
                <div className="mt-0.5 line-clamp-2 text-xs text-muted">{j.slo}</div>
                <div className="mt-3"><ScoreValue score={j.score} /></div>
                <div className="mt-1 text-xs text-ink-2">
                  {high ? <span className="text-crit">{high} high finding{high > 1 ? "s" : ""} open</span>
                    : j.open_findings.medium ? `${j.open_findings.medium} medium finding(s) open` : "no high findings open"}
                </div>
                <div className="mt-2 text-xs text-muted">
                  {j.tested_edges} of {j.testable_edges} edges tested · last tested {relTime(j.last_tested)}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Card icon="shield" title="Coverage — what has never been tested" flush>
          <div className="grid grid-cols-3 gap-4 border-b border-line px-5 py-4">
            <div><div className="text-2xl font-semibold tabular">{data.coverage.edges_tested} <span className="text-base font-normal text-muted">of {data.coverage.edges_testable}</span></div><div className="text-xs text-muted">testable edges with a scored run</div></div>
            <div><div className={`text-2xl font-semibold tabular ${data.coverage.critical_untested ? "text-crit" : "text-good"}`}>{data.coverage.critical_untested}</div><div className="text-xs text-muted">critical-path edges never tested</div></div>
            <div><div className="text-2xl font-semibold tabular">{data.coverage.agents_exercised} <span className="text-base font-normal text-muted">of {data.coverage.agents_total}</span></div><div className="text-xs text-muted">agents exercised</div></div>
          </div>
          {data.untested_critical.length === 0 ? (
            <div className="p-5"><Empty icon="check" title="Every critical edge has been tested" /></div>
          ) : (
            <Table head={["Untested critical path", "Journeys", "Direction", ""]} minWidth={560}>
              {data.untested_critical.map(({ edge, journey_ids }) => (
                <tr key={`${edge.key}-${journey_ids[0]}`}>
                  <td><span className="font-mono text-xs text-muted">{edge.id}</span> {edge.agent} → <code className="font-mono text-xs">{edge.tool}</code></td>
                  <td className="text-ink-2">{journey_ids.join(", ")}</td>
                  <td className="capitalize">{edge.direction}</td>
                  <td className="text-right">
                    <button className="btn btn-sm" onClick={() => go(`experiments/new?edge=${encodeURIComponent(edge.key)}&journey=${journey_ids[0]}`)}>
                      <Icon name="plus" /> Experiment
                    </button>
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </Card>

        <div className="space-y-5">
          <Card icon="alertTriangle" title="Open findings">
            <div className="flex gap-6">
              {(["high", "medium", "low"] as const).map((s) => (
                <div key={s}>
                  <div className={`text-2xl font-semibold tabular ${s === "high" && data.open_findings.high ? "text-crit" : ""}`}>{data.open_findings[s]}</div>
                  <div className="text-xs text-muted capitalize">{s}</div>
                </div>
              ))}
            </div>
            <button className="btn btn-sm mt-4" onClick={() => go("findings")}>Open findings <Icon name="chevronRight" /></button>
          </Card>
          <Card icon="clock" title="Recent runs" flush>
            {data.recent_runs.length === 0 ? <div className="p-5 text-sm text-muted">No runs yet.</div> : (
              <ul className="divide-y divide-line">
                {data.recent_runs.slice(0, 5).map((r) => (
                  <li key={r.id}>
                    <button className="flex w-full items-center gap-3 px-5 py-2.5 text-left hover:bg-surface-2/60" onClick={() => go(`runs/${r.id}`)}>
                      <span className="font-mono text-xs text-muted">{r.label}</span>
                      <span className="min-w-0 flex-1 truncate text-sm">{r.name}</span>
                      {r.verdict ? <VerdictPill verdict={r.verdict} /> : <StatusPill status={r.status} />}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      <Card icon="activity" title="Resilience over time" subtitle="Journey score after each scored run">
        {points.length === 0 ? (
          <Empty icon="activity" title="No scored runs yet">Run an experiment to start the trend.</Empty>
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="run" tickFormatter={(v) => `run-${v}`} tick={{ fill: "var(--muted)", fontSize: 11 }} stroke="var(--axis)" tickLine={false} />
                <YAxis domain={[0, 100]} tick={{ fill: "var(--muted)", fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10, fontSize: 12, color: "var(--ink)" }}
                         labelFormatter={(v) => `run-${String(v).padStart(4, "0")}`} />
                {series.map((j, i) => (
                  <Line key={j.id} dataKey={j.id} name={j.name} stroke={LINE_COLORS[i % LINE_COLORS.length]} strokeWidth={2}
                        dot={{ r: 3 }} connectNulls isAnimationActive={false} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <p className="mt-3 text-xs text-muted">Score formula: {data.score_formula}</p>
      </Card>
    </div>
  );
}
