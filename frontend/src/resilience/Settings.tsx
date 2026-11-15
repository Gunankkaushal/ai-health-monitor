import { useEffect, useState } from "react";
import { api, downloadFile } from "../api";
import { Card, ErrorBox, Icon, Pill, Skeleton } from "../components/ui";
import { Callout, Field, relTime, RiskPill, SectionLabel, Table, useAction, useApi } from "./common";
import type { Fault, LabStatus } from "./types";

export type SettingsTab = "faults" | "policy" | "interception" | "roles" | "guardrails" | "audit";
export const SETTINGS_TABS: { key: SettingsTab; label: string }[] = [
  { key: "faults", label: "Fault library" },
  { key: "policy", label: "Blast-radius policy" },
  { key: "interception", label: "Interception & discovery" },
  { key: "roles", label: "Roles & approvals" },
  { key: "guardrails", label: "Guardrails" },
  { key: "audit", label: "Audit log" },
];

export function Settings({ tab, go }: { tab: SettingsTab; go: (p: string) => void }) {
  return (
    <div className="space-y-5">
      <nav className="-mb-px flex gap-1 overflow-x-auto border-b border-line" aria-label="Settings sections">
        {SETTINGS_TABS.map((t) => (
          <button key={t.key} onClick={() => go(`settings/${t.key}`)}
                  className={`shrink-0 border-b-2 px-3 py-2 text-sm font-medium ${tab === t.key ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink"}`}>
            {t.label}
          </button>
        ))}
      </nav>
      {tab === "faults" && <FaultLibrary />}
      {tab === "policy" && <Policy />}
      {tab === "interception" && <Interception go={go} />}
      {tab === "roles" && <Roles />}
      {tab === "guardrails" && <Guardrails />}
      {tab === "audit" && <Audit />}
    </div>
  );
}

// ---------------------------------------------------------------------------- fault library
function FaultLibrary() {
  const { data, error, reload } = useApi<{ version: string; faults: Fault[] }>("/resilience/faults");
  const [sel, setSel] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const act = useAction();
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <Skeleton className="h-96" />;
  const f = data.faults.find((x) => x.id === sel) ?? data.faults[0];
  const toggle = async () => {
    await act.run("toggle", () => api.post(`/resilience/faults/${f.id}/toggle`, { enabled: !f.enabled, reason }));
    setReason("");
    reload();
  };
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      <Card title="Fault library" subtitle={`v${data.version} · ${data.faults.length} faults · ${data.faults.filter((x) => x.enabled).length} enabled for this tenant`} flush>
        <Table head={["Fault", "Category", "Interception", "Paths", "Risk", "Eligible edges", "Tenant"]} minWidth={860}>
          {data.faults.map((x) => (
            <tr key={x.id} className={`cursor-pointer ${x.id === f.id ? "bg-accent/6" : "hover:bg-surface-2/50"}`} onClick={() => setSel(x.id)}>
              <td className="font-medium">{x.name}</td>
              <td className="text-ink-2">{x.category}</td>
              <td className="text-xs">{x.interception === "mcp_proxy" ? "MCP proxy" : "Model gateway"}</td>
              <td className={x.paths.includes("write") ? "text-warn" : ""}>{x.paths.join(", ")}</td>
              <td><RiskPill risk={x.risk} /></td>
              <td className="tabular">{x.eligible_edges}</td>
              <td>{x.enabled ? <span className="text-xs text-good">enabled</span> : <Pill tone="neutral">disabled</Pill>}</td>
            </tr>
          ))}
        </Table>
        <p className="border-t border-line px-5 py-3 text-xs text-muted">
          The library is data (fault_library.json): “paths” and “requires” decide which faults each edge is offered, “params” renders the
          builder form, and risk feeds policy and approvals. Disabling a fault removes it from every builder and flags suites that use it.
        </p>
      </Card>
      <Card title={f.name} subtitle={`${f.id} · built in`}>
        <div className="space-y-4 text-sm">
          <div><SectionLabel>Simulates</SectionLabel><p className="text-ink-2">{f.simulates}</p></div>
          {f.params.length > 0 && (
            <div><SectionLabel>Parameters</SectionLabel>
              <ul className="space-y-1">{f.params.map((p) => (
                <li key={p.name} className="flex justify-between gap-2 text-xs"><span className="text-muted">{p.label}</span>
                  <span>{p.options ? p.options.join(" · ") : `${p.min}–${p.max} ${p.unit ?? ""}`} · default {String(p.default)}</span></li>))}</ul>
            </div>
          )}
          <div><SectionLabel>Mechanism</SectionLabel><p className="text-ink-2">{f.mechanism}</p></div>
          <div><SectionLabel>Reversal</SectionLabel><p className="text-ink-2">{f.reversal}</p></div>
          <div><SectionLabel>Signals watched</SectionLabel><ul className="list-disc pl-4 text-ink-2">{f.signals.map((s) => <li key={s}>{s}</li>)}</ul></div>
          <div><SectionLabel>Guardrails that always hold</SectionLabel><ul className="list-disc pl-4 text-ink-2">{f.guardrails.map((s) => <li key={s}>{s}</li>)}</ul></div>
          <Callout tone="warn" title="Known limitation">{f.limitation}</Callout>
          <div className="text-xs text-muted">Usage: {f.usage?.experiments ?? 0} experiment(s) across {f.usage?.journeys ?? 0} journey(s) · last run {f.usage?.last_run ?? "never"}</div>
          {!f.enabled && <Callout tone="info">Disabled: {f.disabled_reason}</Callout>}
          <div className="border-t border-line pt-3">
            <Field label={f.enabled ? "Reason for disabling" : "Reason for enabling"} hint="Tenant admins only · audited">
              <input className="input input-sm" value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            <button className={`btn btn-sm mt-2 w-full ${f.enabled ? "btn-danger" : ""}`} onClick={toggle} disabled={reason.trim().length < 3 || act.busy !== null}>
              {f.enabled ? "Disable for tenant" : "Enable for tenant"}
            </button>
            {act.error && <div className="mt-2"><ErrorBox message={act.error} /></div>}
          </div>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------- policy
interface PolicyVersionT { id: number; version: number | null; status: string; body: PolicyBody; proposed_by: string; approved_by: string; reason: string; created_at: string; published_at: string | null }
interface PolicyBody {
  ceilings: Record<string, Record<string, number | string>>;
  blackout_windows: { name: string; weekdays: number[]; start: string; end: string; scope: string }[];
  auto_rollback: Record<string, number | boolean>;
}
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Policy() {
  const { data, error, reload } = useApi<{ current: PolicyVersionT; environment: string; environments: string[]; labels: Record<string, { label: string; enforced_by: string }>; history: PolicyVersionT[]; active_blackout: { name: string } | null }>("/resilience/policy");
  const [draft, setDraft] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const act = useAction();
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <Skeleton className="h-96" />;
  const c = data.current.body;
  const drafts = data.history.filter((h) => h.status === "draft");
  const propose = async () => {
    let body: unknown;
    try {
      body = JSON.parse(draft ?? "");
    } catch {
      act.setError("The draft is not valid JSON.");
      return;
    }
    if (await act.run("propose", () => api.post("/resilience/policy/drafts", { body, reason }))) {
      setDraft(null);
      setReason("");
      reload();
    }
  };
  const decide = async (id: number, approve: boolean) => {
    await act.run(`d-${id}`, () => api.post(`/resilience/policy/drafts/${id}/${approve ? "approve" : "reject"}`, approve ? undefined : { reason: "Rejected in review" }));
    reload();
  };
  return (
    <div className="space-y-5">
      <p className="text-sm text-muted">Tenant-wide limits that hold regardless of what an experiment asks for. An experiment can be stricter, never looser.
        Policy v{data.current.version} in force since {relTime(data.current.published_at)} · this deployment is <strong className="text-ink">{data.environment}</strong>
        {data.active_blackout && <> · <span className="text-warn">blackout “{data.active_blackout.name}” active now</span></>}</p>
      <Card title="Ceilings by environment" flush>
        <Table head={["Limit", ...data.environments.map((e) => e === data.environment ? `${e} (this)` : e), "Enforced by"]} minWidth={760}>
          {Object.entries(c.ceilings).map(([k, per]) => (
            <tr key={k}>
              <td>{data.labels[k]?.label ?? k}</td>
              {data.environments.map((e) => <td key={e} className={`${per[e] === "never" ? "text-warn" : ""} ${e === data.environment ? "font-semibold" : ""} tabular`}>{String(per[e]).replace("_", " ")}</td>)}
              <td className="text-xs text-muted">{data.labels[k]?.enforced_by}</td>
            </tr>
          ))}
        </Table>
        <p className="border-t border-line px-5 py-3 text-xs text-muted">A request that exceeds a ceiling is refused at save time with the rule named, never silently clamped — clamping makes a run look stronger than it was. Sample patients are the sandbox; user-created patients are protected.</p>
      </Card>
      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Blackout windows (UTC)" flush>
          {c.blackout_windows.length === 0 ? <p className="p-5 text-sm text-muted">None.</p> : (
            <Table head={["Window", "When", "Scope"]} minWidth={420}>
              {c.blackout_windows.map((w) => <tr key={w.name}><td>{w.name}</td><td className="text-ink-2">{w.weekdays.map((d) => DAYS[d]).join(", ")} {w.start}–{w.end}</td><td>{w.scope}</td></tr>)}
            </Table>
          )}
        </Card>
        <Card title="Auto-rollback & kill switch">
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
            <li>Any abort-flagged steady-state check breached → rollback {c.auto_rollback.abort_metric_breach ? "(on)" : "(off)"}</li>
            <li>An iteration silent for {String(c.auto_rollback.telemetry_loss_s)} s counts as lost telemetry → rollback</li>
            <li>Hard stop at the experiment's fault duration</li>
            <li>Kill switch stops every run; injectors fail open; available to every role that can abort; re-arming needs a tenant admin</li>
          </ul>
        </Card>
      </div>
      <Card title="Change policy" subtitle="Versioned. A draft needs approval by a second tenant admin; the proposer cannot self-approve.">
        {drafts.length > 0 && (
          <div className="mb-4 space-y-2">
            {drafts.map((d) => (
              <div key={d.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-line px-3 py-2 text-sm">
                <Pill tone="warn">draft {d.id}</Pill><span className="flex-1">{d.reason} · by {d.proposed_by}</span>
                <button className="btn btn-sm" onClick={() => decide(d.id, true)} disabled={act.busy !== null}>Approve & publish</button>
                <button className="btn btn-sm btn-ghost" onClick={() => decide(d.id, false)} disabled={act.busy !== null}>Reject</button>
              </div>
            ))}
          </div>
        )}
        {draft === null ? (
          <button className="btn btn-sm" onClick={() => setDraft(JSON.stringify(c, null, 2))}><Icon name="edit" /> Propose a change</button>
        ) : (
          <div className="space-y-2">
            <textarea className="input font-mono text-xs" rows={16} value={draft} onChange={(e) => setDraft(e.target.value)} />
            <input className="input" placeholder="Reason for the change" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="flex gap-2">
              <button className="btn btn-sm btn-primary" onClick={propose} disabled={reason.trim().length < 3 || act.busy !== null}>Submit draft</button>
              <button className="btn btn-sm" onClick={() => setDraft(null)}>Cancel</button>
            </div>
          </div>
        )}
        {act.error && <div className="mt-3"><ErrorBox message={act.error} /></div>}
        <div className="mt-4 border-t border-line pt-3">
          <SectionLabel>History</SectionLabel>
          <ul className="space-y-1 text-xs text-ink-2">
            {data.history.map((h) => <li key={h.id}>{h.version ? `v${h.version}` : `draft ${h.id}`} · {h.status} · {h.proposed_by}{h.approved_by ? ` + ${h.approved_by}` : ""} · {h.reason}</li>)}
          </ul>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------- interception
function Interception({ go }: { go: (p: string) => void }) {
  const { data, error, reload } = useApi<{
    points: { key: string; mode: string; deployed_as: string; can_inject: string; coverage: string; status: string }[];
    sources: { source: string; establishes: string; trust: string; resolved: number }[];
    resolution_order: string[];
    telemetry_completeness_pct: number | null;
    telemetry_sample: number;
    overrides: { id: number; edge_key: string; fault_id: string; reason: string; review_by: string; created_by: string }[];
  }>("/resilience/interception");
  const act = useAction();
  const [ov, setOv] = useState({ edge_key: "", fault_id: "*", reason: "", review_by: "" });
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return <Skeleton className="h-96" />;
  const add = async () => {
    if (await act.run("ov", () => api.post(`/resilience/edges/${ov.edge_key}/overrides`, { fault_id: ov.fault_id, reason: ov.reason, review_by: ov.review_by }))) {
      setOv({ edge_key: "", fault_id: "*", reason: "", review_by: "" });
      reload();
    }
  };
  const remove = async (id: number) => {
    await act.run(`rm-${id}`, () => api.del(`/resilience/overrides/${id}`, { reason: "Removed from settings" }));
    reload();
  };
  return (
    <div className="space-y-5">
      <Card title="Interception points — where faults can be applied" flush>
        <Table head={["Mode", "Deployed as", "Can inject", "Coverage", "Status"]} minWidth={900}>
          {data.points.map((p) => (
            <tr key={p.key}><td className="font-medium">{p.mode}</td><td className="text-ink-2">{p.deployed_as}</td><td className="text-xs text-ink-2">{p.can_inject}</td><td>{p.coverage}</td>
              <td className={p.status === "connected" ? "text-good" : "text-warn"}>{p.status}</td></tr>
          ))}
        </Table>
        <p className="border-t border-line px-5 py-3 text-xs text-muted">A fault is only offered where an interception point that supports it is active. In rule-based mode the model gateway carries no traffic, so model faults are unavailable.</p>
      </Card>
      <Card title="Discovery sources — ranked by trust" flush>
        <Table head={["Source", "What it establishes", "Trust", "Edges resolved"]} minWidth={700}>
          {data.sources.map((s) => <tr key={s.source}><td>{s.source}</td><td className="text-ink-2">{s.establishes}</td><td className={s.trust === "Authoritative" ? "" : "text-warn"}>{s.trust}</td><td className="tabular">{s.resolved}</td></tr>)}
        </Table>
      </Card>
      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Applicability resolution order">
          <ol className="list-decimal space-y-1 pl-5 text-sm text-ink-2">{data.resolution_order.map((r) => <li key={r}>{r}</li>)}</ol>
          <p className="mt-3 text-xs text-muted">Steps 1–5 are deterministic and explainable. The ranking only changes order, never eligibility.</p>
          <button className="btn btn-sm mt-3" onClick={() => go("topology")}>Inspect an edge on the topology</button>
        </Card>
        <Card title="The quiet failure mode">
          <Callout tone="warn">Coverage is measured against discovered edges. A call that bypasses the toolbox is invisible, so it is neither tested nor counted as missing.</Callout>
          <p className="mt-3 text-sm">Telemetry completeness: <strong>{data.telemetry_completeness_pct ?? "—"}%</strong> of the last {data.telemetry_sample} tool calls carry server and timing.</p>
        </Card>
      </div>
      <Card title="Human overrides" subtitle="Exclude a fault from an edge, with a reason and a review date. Tenant admins only; audited." flush>
        {data.overrides.length > 0 && (
          <Table head={["Edge", "Fault", "Reason", "Review by", "By", ""]} minWidth={760}>
            {data.overrides.map((o) => (
              <tr key={o.id}><td className="font-mono text-xs">{o.edge_key}</td><td className="text-xs">{o.fault_id === "*" ? "all faults" : o.fault_id}</td><td className="text-ink-2">{o.reason}</td><td>{o.review_by || "—"}</td><td className="text-xs">{o.created_by}</td>
                <td><button className="btn btn-sm btn-ghost" onClick={() => remove(o.id)}>Remove</button></td></tr>
            ))}
          </Table>
        )}
        <div className="grid gap-2 p-5 sm:grid-cols-[1fr_1fr_2fr_140px_auto]">
          <input className="input input-sm" placeholder="edge key, e.g. alert:evaluate_readings" value={ov.edge_key} onChange={(e) => setOv({ ...ov, edge_key: e.target.value })} />
          <input className="input input-sm" placeholder="fault id or *" value={ov.fault_id} onChange={(e) => setOv({ ...ov, fault_id: e.target.value })} />
          <input className="input input-sm" placeholder="Reason" value={ov.reason} onChange={(e) => setOv({ ...ov, reason: e.target.value })} />
          <input className="input input-sm" placeholder="Review by (YYYY-MM-DD)" value={ov.review_by} onChange={(e) => setOv({ ...ov, review_by: e.target.value })} />
          <button className="btn btn-sm" onClick={add} disabled={!ov.edge_key || ov.reason.trim().length < 3}>Add</button>
        </div>
        {act.error && <div className="px-5 pb-5"><ErrorBox message={act.error} /></div>}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------- roles
function Roles() {
  const { data, error } = useApi<{
    roles: { key: string; label: string; group: string; permissions: string[] }[];
    permission_labels: Record<string, string>;
    directory: { name: string; role: string; role_label: string }[];
    approval_rules: { when: string; approvers: string; extra: string }[];
    approval_ttl_hours: number;
    identity_note: string;
  }>("/resilience/roles");
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Skeleton className="h-96" />;
  const perms = Object.keys(data.permission_labels);
  return (
    <div className="space-y-5">
      <Callout tone="warn" title="Identity is asserted, not authenticated">{data.identity_note}</Callout>
      <Card title="Roles" flush>
        <Table head={["Role", ...perms.map((p) => data.permission_labels[p]), "Group"]} minWidth={900}>
          {data.roles.map((r) => (
            <tr key={r.key}><td className="font-medium">{r.label}</td>
              {perms.map((p) => <td key={p} className={r.permissions.includes(p) ? "text-good" : "text-muted"}>{r.permissions.includes(p) ? "yes" : "no"}</td>)}
              <td className="font-mono text-xs">{r.group}</td></tr>
          ))}
        </Table>
        <p className="border-t border-line px-5 py-3 text-xs text-muted">Abort is granted to everyone who can see a run, including viewers. Stopping damage is never gated.</p>
      </Card>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card title="Approval rules" flush>
          <Table head={["When", "Approvers", "Extra"]} minWidth={560}>
            {data.approval_rules.map((r) => <tr key={r.when}><td>{r.when}</td><td>{r.approvers}</td><td className="text-ink-2">{r.extra}</td></tr>)}
          </Table>
          <p className="border-t border-line px-5 py-3 text-xs text-muted">Approvals expire after {data.approval_ttl_hours} h and apply to one experiment version. Editing the experiment resets them. An author cannot approve their own experiment; this is checked at request time.</p>
        </Card>
        <Card title="Directory">
          <ul className="space-y-1.5 text-sm">{data.directory.map((d) => <li key={d.name} className="flex justify-between gap-2"><span className="font-mono text-xs">{d.name}</span><span className="text-ink-2">{d.role_label}</span></li>)}</ul>
          <Callout tone="info" title="Break glass">A tenant admin can run without missing approvals during an incident, with a reason. It is logged as break-glass in the audit log.</Callout>
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- guardrails
function Guardrails() {
  const { data, error, reload } = useApi<{ current: LabStatus["guardrails"] }>("/resilience/guardrails");
  const [g, setG] = useState<LabStatus["guardrails"] | null>(null);
  const [reason, setReason] = useState("");
  const act = useAction();
  useEffect(() => {
    if (data) setG(data.current);
  }, [data]);
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!g) return <Skeleton className="h-64" />;
  const save = async () => {
    if (await act.run("save", () => api.put("/resilience/guardrails", { ...g, reason }))) {
      setReason("");
      reload();
    }
  };
  return (
    <Card title="Resilience guardrails" subtitle="Product behaviour that experiments verify. Turn one off to reproduce a failure, then turn it back on to verify the fix.">
      <div className="space-y-4">
        <label className="flex items-start gap-3">
          <input type="checkbox" className="mt-1" checked={g.retries} onChange={(e) => setG({ ...g, retries: e.target.checked })} />
          <span><span className="font-medium">Bounded retries for idempotent tools</span>
            <span className="block text-sm text-ink-2">Timeouts, unavailable servers and rate limits are retried with exponential backoff and jitter, honouring retry-after (capped at 2 s). Only tools whose MCP manifest declares them idempotent are retried.</span></span>
        </label>
        <Field label="Maximum attempts"><input className="input w-24 tabular" inputMode="numeric" value={g.max_attempts} onChange={(e) => setG({ ...g, max_attempts: Math.max(1, Math.min(5, Number(e.target.value) || 1)) })} /></Field>
        <label className="flex items-start gap-3">
          <input type="checkbox" className="mt-1" checked={g.output_validation} onChange={(e) => setG({ ...g, output_validation: e.target.checked })} />
          <span><span className="font-medium">Output contracts</span>
            <span className="block text-sm text-ink-2">Every tool result is checked against its documented shape. A malformed result degrades that agent's section instead of crashing the request, and an empty model answer falls back to the labelled rule-based answer.</span></span>
        </label>
        <Field label="Reason" hint="Tenant admins only · audited"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <button className="btn btn-primary" onClick={save} disabled={reason.trim().length < 3 || act.busy !== null}>Save guardrails</button>
        {act.error && <ErrorBox message={act.error} />}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------- audit
interface AuditEvent { seq: number; occurred_at: string; actor: string; on_behalf_of: string; event: string; subject: string; detail: string; reason: string; change_ref: string; hash: string }

function Audit() {
  const [f, setF] = useState({ event: "", actor: "", days: "7" });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const { data, error, reload } = useApi<{ events: AuditEvent[]; chain: { intact: boolean; checked: number; first_bad_seq: number | null; problem: string | null; verified_at: string }; vocabulary: string[] }>(`/resilience/audit?${qs}`);
  const act = useAction();
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Event type">
          <select className="input" value={f.event} onChange={(e) => setF({ ...f, event: e.target.value })}>
            <option value="">All events</option>
            {["run", "fault", "experiment", "policy", "killswitch", "edge", "finding", "suite", "gate", "guardrails"].map((p) => <option key={p} value={p}>{p}.*</option>)}
          </select>
        </Field>
        <Field label="Actor"><input className="input" placeholder="person or service" value={f.actor} onChange={(e) => setF({ ...f, actor: e.target.value })} /></Field>
        <Field label="Range">
          <select className="input" value={f.days} onChange={(e) => setF({ ...f, days: e.target.value })}>
            <option value="1">Last 24 h</option><option value="7">Last 7 days</option><option value="90">Last 90 days</option><option value="">All time</option>
          </select>
        </Field>
        <span className="flex-1" />
        <button className="btn btn-sm" onClick={() => act.run("csv", () => downloadFile(`/resilience/audit/export?format=csv&${qs}`, "audit.csv"))}><Icon name="download" /> CSV</button>
        <button className="btn btn-sm" onClick={() => act.run("json", () => downloadFile(`/resilience/audit/export?format=json&${qs}`, "audit.json"))}><Icon name="download" /> JSON</button>
      </div>
      {act.error && <ErrorBox message={act.error} />}
      {data && (
        data.chain.intact
          ? <Callout tone="good" title="Hash chain intact">{data.chain.checked} records verified {relTime(data.chain.verified_at)}. Append-only: the API has no edit or delete path.</Callout>
          : <Callout tone="crit" title="Hash chain broken">First bad record: #{data.chain.first_bad_seq} — {data.chain.problem}. A record was edited or deleted outside the application.</Callout>
      )}
      <Card title="Audit log" subtitle="Every action that changed what the lab did or was allowed to do" flush>
        {!data ? <div className="p-5"><Skeleton className="h-40" /></div> : (
          <Table head={["Seq", "When (UTC)", "Actor", "Event", "Subject", "Detail"]} minWidth={1000}>
            {data.events.map((e) => (
              <tr key={e.seq}>
                <td className="tabular text-xs text-muted">{e.seq}</td>
                <td className="text-xs whitespace-nowrap">{e.occurred_at.replace("T", " ").slice(0, 19)}</td>
                <td className="text-xs">{e.actor}{e.on_behalf_of && <span className="text-muted"> for {e.on_behalf_of}</span>}</td>
                <td><code className="font-mono text-xs">{e.event}</code></td>
                <td className="text-xs">{e.subject}</td>
                <td className="text-xs text-ink-2">{e.detail}{e.reason && <span className="block text-muted">reason: {e.reason}</span>}{e.change_ref && <span className="block text-muted">CHG {e.change_ref}</span>}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
