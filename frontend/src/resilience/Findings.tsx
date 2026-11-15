import { useState } from "react";
import { api, downloadFile } from "../api";
import { Card, Empty, ErrorBox, Icon, Pill, Segmented, Skeleton } from "../components/ui";
import { SevPill, Table, useAction, useApi } from "./common";
import type { Finding } from "./types";

export function Findings({ go }: { go: (p: string) => void }) {
  const [view, setView] = useState<"active" | "resolved" | "all">("active");
  const { data, error, reload } = useApi<Finding[]>("/resilience/findings", 8000);
  const act = useAction();
  const [issue, setIssue] = useState<string | null>(null);
  if (error) return <ErrorBox message={error} onRetry={reload} />;

  const rows = (data ?? []).filter((f) => view === "all" || (view === "resolved" ? f.status === "resolved" : f.status !== "resolved"));
  const file = async (f: Finding) => {
    const r = await act.run(`file-${f.id}`, () => api.post<Finding & { issue_markdown: string }>(`/resilience/findings/${f.id}/file`));
    if (r) setIssue(r.issue_markdown);
    reload();
  };
  const resolve = async (f: Finding) => {
    await act.run(`res-${f.id}`, () => api.post(`/resilience/findings/${f.id}/resolve`, { reason: "Resolved from the findings view" }));
    reload();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented label="Finding filter" value={view} onChange={setView}
                   options={[{ value: "active", label: "Open & filed" }, { value: "resolved", label: "Resolved" }, { value: "all", label: "All" }]} />
        <span className="flex-1" />
        <button className="btn btn-sm" onClick={() => act.run("export", () => downloadFile("/resilience/findings/export", "resilience-findings.md"))}>
          <Icon name="download" /> Export as issues (.md)
        </button>
      </div>
      {act.error && <ErrorBox message={act.error} />}
      {issue && (
        <Card title="Issue text" subtitle="Paste into your tracker" actions={<button className="btn btn-sm btn-ghost" onClick={() => setIssue(null)}><Icon name="x" /></button>}>
          <pre className="max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-xs whitespace-pre-wrap">{issue}</pre>
        </Card>
      )}
      <Card icon="alertTriangle" title="Findings" subtitle="Deduplicated per edge, fault and failure kind. A later run that holds verifies and resolves them." flush>
        {!data ? <div className="p-5"><Skeleton className="h-40" /></div> : rows.length === 0 ? (
          <div className="p-5"><Empty icon="check" title="Nothing here">Findings appear when a run rejects its hypothesis.</Empty></div>
        ) : (
          <Table head={["Severity", "Weak path", "What happened", "Recommended guardrail", "Owner", "Status"]} minWidth={1100}>
            {rows.map((f) => (
              <tr key={f.id}>
                <td><SevPill severity={f.severity} /></td>
                <td className="text-xs">
                  <div className="font-medium text-ink">{f.title}</div>
                  <div className="mt-0.5 text-muted">{f.edge_key} · {f.fault_name}</div>
                  <button className="mt-0.5 text-accent hover:underline" onClick={() => go(`runs/${f.run_id}`)}>run-{String(f.run_id).padStart(4, "0")}</button>
                  {f.occurrences > 1 && <span className="text-muted"> · seen {f.occurrences}×</span>}
                </td>
                <td className="text-xs text-ink-2">{f.what_happened}</td>
                <td className="text-xs text-ink-2">{f.guardrail}</td>
                <td className="text-xs">{f.owner}</td>
                <td className="whitespace-nowrap">
                  {f.status === "resolved" ? <Pill tone="good">resolved{f.reference ? ` · ${f.reference}` : ""}</Pill> : (
                    <div className="flex flex-col items-start gap-1">
                      {f.status === "filed" ? <Pill tone="info">{f.reference}</Pill> :
                        <button className="btn btn-sm" onClick={() => file(f)} disabled={act.busy !== null}>File</button>}
                      <button className="text-xs text-muted hover:text-ink" onClick={() => resolve(f)} disabled={act.busy !== null}>Mark resolved</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
