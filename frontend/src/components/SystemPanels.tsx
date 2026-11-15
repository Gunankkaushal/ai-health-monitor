import { useState } from "react";
import { api, errorMessage } from "../api";
import type { Activity, AgentInfo, McpStatus } from "../types";
import { Card, Empty, ErrorBox, fmtRelative, fmtTime, Icon, Pill, Skeleton, StatusDot } from "./ui";

const STATUS_TONE = { ok: "good", error: "crit", rejected: "warn", info: "neutral" } as const;
const STATUS_LABEL = { ok: "Success", error: "Error", rejected: "Rejected", info: "Info" } as const;

export function ActivityPanel({ items, loading, error, onRefresh, compact = false, onViewAll }: {
  items: Activity[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  compact?: boolean;
  onViewAll?: () => void;
}) {
  const shown = compact ? items.slice(0, 6) : items;
  return (
    <Card
      icon="clock"
      title={compact ? "Recent activity" : "Agent activity log"}
      subtitle={compact ? "Latest agent and tool calls" : "Every agent run and MCP tool call for this patient"}
      flush
      actions={
        compact && onViewAll ? (
          <button className="btn btn-sm btn-ghost" onClick={onViewAll}>View all <Icon name="chevronRight" /></button>
        ) : (
          <button className="btn btn-sm" onClick={onRefresh}><Icon name="refresh" /> Refresh</button>
        )
      }
    >
      {error && <div className="p-5"><ErrorBox message={error} onRetry={onRefresh} /></div>}
      {loading && items.length === 0 && <div className="space-y-2 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>}
      {!loading && !error && items.length === 0 && (
        <div className="p-5"><Empty icon="clock" title="No activity yet">Agent runs will appear here as they happen.</Empty></div>
      )}
      {shown.length > 0 && (
        <ol className={`divide-y divide-line ${compact ? "" : "max-h-[40rem] overflow-y-auto"}`}>
          {shown.map((a) => (
            <li key={a.id} className="flex gap-3 px-5 py-3">
              <span className="mt-1.5"><StatusDot tone={STATUS_TONE[a.status]} /></span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                  <span className="font-medium">{a.agent}</span>
                  {a.tool && <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-ink-2">{a.tool}</code>}
                  {!compact && a.status !== "ok" && <Pill tone={STATUS_TONE[a.status]}>{STATUS_LABEL[a.status]}</Pill>}
                </div>
                {a.detail && <p className="mt-0.5 truncate text-xs text-muted" title={a.detail}>{a.detail}</p>}
                {!compact && (a.server || a.duration_ms !== null) && (
                  <p className="mt-0.5 text-[11px] text-muted">
                    {a.server && <>{a.server}</>}
                    {a.server && a.duration_ms !== null && " · "}
                    {a.duration_ms !== null && <span className="tabular">{a.duration_ms} ms</span>}
                  </p>
                )}
              </div>
              <span className="shrink-0 text-xs text-muted" title={fmtTime(a.created_at)}>{fmtRelative(a.created_at)}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

export function McpPanel({ status, error, onChanged }: { status: McpStatus | null; error: string | null; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const reconnect = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.post("/mcp/reconnect");
      onChanged();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      icon="server"
      title="Tool servers"
      subtitle={status ? `${status.connected} of ${status.servers.length} connected · ${status.total_tools} tools available` : "Connection state"}
      actions={
        <button className="btn btn-sm" onClick={reconnect} disabled={busy}>
          <Icon name="refresh" className={busy ? "animate-spin" : ""} /> {busy ? "Reconnecting…" : "Reconnect"}
        </button>
      }
    >
      {(error || err) && <div className="mb-3"><ErrorBox message={(error || err)!} /></div>}
      {!status && !error && <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>}
      <ul className="space-y-3">
        {status?.servers.map((s) => {
          const tone = s.status === "connected" ? "good" : s.status === "starting" ? "warn" : "crit";
          const expanded = open === s.key;
          return (
            <li key={s.key} className="overflow-hidden rounded-xl border border-line">
              <button className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-surface-2/60"
                      onClick={() => setOpen(expanded ? null : s.key)} aria-expanded={expanded}>
                <StatusDot tone={tone} pulse={s.status === "starting"} />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">{s.name}</div>
                  <div className="truncate text-xs text-muted">{s.description}</div>
                </div>
                <div className="hidden text-right text-xs text-muted sm:block">
                  <div className="tabular">{s.tools.length} tools</div>
                  <div className="tabular">{s.calls} calls{s.failures ? ` · ${s.failures} failed` : ""}</div>
                </div>
                <Pill tone={tone}><span className="capitalize">{s.status}</span></Pill>
                <Icon name="chevronDown" className={`h-4 w-4 text-muted transition ${expanded ? "rotate-180" : ""}`} />
              </button>
              {s.error && <div className="border-t border-line bg-crit/6 px-4 py-2 text-xs text-crit">{s.error}</div>}
              {expanded && (
                <div className="animate-in border-t border-line bg-surface-2/40 px-4 py-3">
                  <dl className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
                    <div><dt className="text-muted">Transport</dt><dd className="font-medium">{s.transport}</dd></div>
                    <div><dt className="text-muted">Protocol</dt><dd className="font-medium">{s.protocol_version ?? "—"}</dd></div>
                    <div><dt className="text-muted">Restarts</dt><dd className="font-medium tabular">{s.restarts}</dd></div>
                  </dl>
                  {s.tools.length === 0 ? (
                    <p className="text-xs text-muted">No tools discovered.</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {s.tools.map((t) => (
                        <li key={t.name} className="text-xs">
                          <code className="font-mono font-medium text-ink">{t.name}</code>
                          <span className="text-muted"> — {t.description.split("\n")[0]}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

export function AgentsInfo({ agents }: { agents: AgentInfo[] }) {
  return (
    <Card icon="cpu" title="Agents" subtitle="Each agent runs with its own instructions and a restricted tool set">
      <ul className="grid gap-3 sm:grid-cols-2">
        {agents.map((a) => (
          <li key={a.key} className="rounded-xl border border-line p-4">
            <div className="flex items-center gap-2">
              <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent/10 text-accent"><Icon name="bot" className="h-3.5 w-3.5" /></span>
              <span className="text-sm font-semibold">{a.name}</span>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-ink-2">{a.responsibility}</p>
            <div className="mt-3 flex flex-wrap gap-1">
              {a.allowed_tools.map((t) => (
                <code key={t} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10.5px] text-ink-2">{t}</code>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
