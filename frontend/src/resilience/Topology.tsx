import { useState } from "react";
import { Card, ErrorBox, Icon, Pill, Skeleton, StatusDot } from "../components/ui";
import { APPLIC_TONE, KeyVal, SectionLabel, Table, useApi } from "./common";
import type { Applicability, Edge } from "./types";

interface TopologyData {
  edges: Edge[];
  servers: { key: string; name: string; status: string; tools: string[] }[];
  agents: { key: string; name: string }[];
  model: { active: boolean; model: string };
  synced_at: string;
  suggestion: Edge | null;
  suggestion_basis: string;
}

export function Topology({ go }: { go: (p: string) => void }) {
  const [rescan, setRescan] = useState(0);
  const { data, error, reload, loading } = useApi<TopologyData>(`/resilience/topology?rescan=${rescan > 0}&n=${rescan}`);
  const [sel, setSel] = useState<string | null>(null);
  const edge = data?.edges.find((e) => e.key === (sel ?? data?.suggestion?.key)) ?? data?.edges[0];
  const detail = useApi<{ faults: Applicability[]; counts: Record<string, number> }>(edge ? `/resilience/edges/${edge.key}` : null);

  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <Skeleton className="h-96" />;

  const toolEdges = data.edges.filter((e) => e.kind === "mcp.tool");
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <p className="flex-1 text-sm text-muted">
          From agent allow-lists, the MCP tool manifest and response sampling · {data.agents.length} agents ·{" "}
          {data.servers.reduce((n, s) => n + s.tools.length, 0)} tools · {data.servers.length} MCP servers + model gateway ·{" "}
          {data.edges.length} edges
        </p>
        <button className="btn btn-sm" onClick={() => setRescan((n) => n + 1)} disabled={loading}>
          <Icon name="refresh" className={loading ? "animate-spin" : ""} /> Re-scan
        </button>
        <button className="btn btn-sm" onClick={() => go("journeys")}>List every edge</button>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card icon="dashboard" title="Agent graph" subtitle="Click an edge to inspect it" flush>
          <div className="grid gap-4 p-5 lg:grid-cols-[180px_minmax(0,1fr)_200px]">
            <div>
              <SectionLabel>Agents ({data.agents.length})</SectionLabel>
              <div className="space-y-2">
                {data.agents.map((a) => {
                  const active = edge?.agent_key === a.key;
                  const n = data.edges.filter((e) => e.agent_key === a.key);
                  return (
                    <div key={a.key} className={`rounded-lg border p-3 ${active ? "border-accent bg-accent/6" : "border-line"}`}>
                      <div className="text-sm font-semibold">{a.name}</div>
                      <div className="text-xs text-muted">{n.length} edges · {n.filter((e) => e.tested).length} tested</div>
                    </div>
                  );
                })}
              </div>
            </div>
            <div>
              <SectionLabel>Edges · agent → tool</SectionLabel>
              <div className="space-y-1">
                {data.edges.map((e) => {
                  const active = e.key === edge?.key;
                  return (
                    <button key={e.key} onClick={() => setSel(e.key)}
                            className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs transition ${active ? "bg-accent/10 ring-1 ring-accent/40" : "hover:bg-surface-2"}`}>
                      <span className="w-8 font-mono text-muted">{e.id}</span>
                      <span className="w-20 shrink-0 truncate text-ink-2">{e.agent.replace(" Agent", "")}</span>
                      <Icon name="arrowRight" className="h-3 w-3 shrink-0 text-muted" />
                      <code className="min-w-0 flex-1 truncate font-mono" title={e.tool}>{e.tool === "llm" ? "Groq model" : e.tool}</code>
                      {e.direction === "write" && <span className="shrink-0 text-[10px] font-semibold text-warn" title="write path">W</span>}
                      {e.critical && <span className="shrink-0 text-[10px] font-semibold text-info" title="critical path">CRIT</span>}
                      <StatusDot tone={e.tested ? "good" : e.connected ? "neutral" : "warn"} />
                    </button>
                  );
                })}
              </div>
              <p className="mt-3 text-[11px] text-muted">
                <StatusDot tone="good" /> tested · <StatusDot tone="neutral" /> never tested · <StatusDot tone="warn" /> interception inactive · <span className="font-semibold text-warn">W</span> write path · <span className="font-semibold text-info">CRIT</span> critical path
              </p>
            </div>
            <div>
              <SectionLabel>Systems</SectionLabel>
              <div className="space-y-2">
                {data.servers.map((s) => (
                  <div key={s.key} className={`rounded-lg border p-3 ${edge?.server === s.key ? "border-accent bg-accent/6" : "border-line"}`}>
                    <div className="flex items-center gap-2 text-sm font-semibold"><StatusDot tone={s.status === "connected" ? "good" : "crit"} />{s.name.replace(" MCP Server", "")}</div>
                    <div className="mt-1 text-[11px] text-muted">{s.tools.length} tools · stdio</div>
                  </div>
                ))}
                <div className={`rounded-lg border p-3 ${edge?.server === "groq" ? "border-accent bg-accent/6" : "border-line"}`}>
                  <div className="flex items-center gap-2 text-sm font-semibold"><StatusDot tone={data.model.active ? "good" : "warn"} />Model gateway</div>
                  <div className="mt-1 text-[11px] text-muted">{data.model.active ? data.model.model : "inactive · rule-based mode"}</div>
                </div>
              </div>
            </div>
          </div>
        </Card>

        {edge && (
          <Card icon="activity" title={`${edge.id} · ${edge.agent} → ${edge.tool === "llm" ? "model" : edge.tool}`}
                subtitle={`${edge.kind === "mcp.tool" ? "MCP tool call" : "Model inference"} · ${edge.direction} path · ${edge.owner}`}>
            <dl className="grid grid-cols-2 gap-3">
              <KeyVal k="Calls (7 days)" v={edge.stats?.calls ?? 0} />
              <KeyVal k="p95 latency" v={edge.stats?.p95_ms != null ? `${edge.stats.p95_ms} ms` : "—"} />
              <KeyVal k="Errors (7 days)" v={edge.stats?.errors ?? 0} />
              <KeyVal k="Idempotent" v={edge.idempotent ? "yes" : "no"} />
              <div className="col-span-2"><KeyVal k="Retry policy" v={edge.retry_policy} /></div>
              <div className="col-span-2"><KeyVal k="Journeys using this edge" v={edge.journeys.join(", ")} /></div>
            </dl>
            <div className="mt-4 border-t border-line pt-4">
              <SectionLabel>Faults on this edge</SectionLabel>
              {detail.data ? (
                <ul className="space-y-1.5">
                  {detail.data.faults.map((f) => (
                    <li key={f.fault_id} className="flex items-start gap-2 text-xs" title={f.reason}>
                      <Pill tone={APPLIC_TONE[f.status]}>{f.status.replace("_", " ")}</Pill>
                      <span className="min-w-0 flex-1"><span className="font-medium">{f.name}</span>
                        {f.status !== "eligible" && <span className="block text-muted">{f.reason}</span>}</span>
                    </li>
                  ))}
                </ul>
              ) : <Skeleton className="h-32" />}
            </div>
            {data.suggestion?.key === edge.key && (
              <div className="mt-4 rounded-lg bg-accent/6 p-3 text-xs ring-1 ring-inset ring-accent/15">
                <div className="mb-1 flex items-center gap-1.5 font-semibold text-accent"><Icon name="sparkles" className="h-3.5 w-3.5" />Highest unproven risk</div>
                {data.suggestion_basis}
              </div>
            )}
            <button className="btn btn-primary mt-4 w-full" onClick={() => go(`experiments/new?edge=${encodeURIComponent(edge.key)}&journey=${edge.journeys[0]}`)}
                    disabled={!detail.data?.faults.some((f) => f.status === "eligible")}>
              <Icon name="plus" /> Create experiment on this edge
            </button>
          </Card>
        )}
      </div>

      <Card icon="list" title="Classification provenance" subtitle="Where each attribute of each tool edge came from" flush>
        <Table head={["Edge", "Server", "Direction", "Idempotent", "Response", "Direction source", "Response source"]} minWidth={980}>
          {toolEdges.map((e) => (
            <tr key={e.key}>
              <td><span className="font-mono text-xs text-muted">{e.id}</span> {e.agent.replace(" Agent", "")} → <code className="font-mono text-xs">{e.tool}</code></td>
              <td className="text-ink-2">{e.server}</td>
              <td className="capitalize">{e.direction}</td>
              <td>{e.idempotent ? "yes" : "no"}</td>
              <td>{e.response}{e.timestamped ? " · timestamped" : ""}</td>
              <td className="text-xs text-muted">{e.provenance.direction}</td>
              <td className="text-xs text-muted">{e.provenance.response}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

