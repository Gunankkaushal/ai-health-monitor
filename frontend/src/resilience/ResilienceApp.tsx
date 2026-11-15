import { useEffect, useState } from "react";
import { api, getActor, onActorChange, setActor } from "../api";
import { Callout, useAction, useApi } from "./common";
import { ErrorBox, Icon, type IconName } from "../components/ui";
import { ExperimentBuilder, ExperimentDetail, ExperimentList } from "./Experiments";
import { Findings } from "./Findings";
import { Journeys } from "./Journeys";
import { Overview } from "./Overview";
import { RunDetailView, RunList } from "./Runs";
import { Settings, type SettingsTab } from "./Settings";
import { Suites } from "./Suites";
import { Topology } from "./Topology";
import type { LabStatus } from "./types";

const NAV: { key: string; label: string; icon: IconName }[] = [
  { key: "overview", label: "Overview", icon: "dashboard" },
  { key: "topology", label: "Topology", icon: "server" },
  { key: "journeys", label: "Journeys", icon: "activity" },
  { key: "experiments", label: "Experiments", icon: "flask" },
  { key: "runs", label: "Runs", icon: "clock" },
  { key: "findings", label: "Findings", icon: "alertTriangle" },
  { key: "suites", label: "Suites & CI", icon: "list" },
  { key: "settings", label: "Settings", icon: "shield" },
];

const ACTORS = [
  ["morgan.author", "Experiment author"], ["devon.sre", "SRE approver"], ["kai.owner", "Product owner"],
  ["jordan.admin", "Tenant admin"], ["taylor.admin", "Tenant admin"], ["casey.auditor", "Auditor"], ["riley.viewer", "Viewer"],
];

/** Route is the part of the hash after "lab/", e.g. "runs/12" or "experiments/new?edge=…". */
export function ResilienceApp({ route, go }: { route: string; go: (p: string) => void }) {
  const [actorName, setActorName] = useState(getActor());
  useEffect(() => onActorChange(setActorName), []);
  const status = useApi<LabStatus>(`/resilience/status?as=${actorName}`, 3000);
  const act = useAction();

  const [pathPart, queryPart] = route.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  const section = parts[0] || "overview";
  const query = new URLSearchParams(queryPart ?? "");
  const ks = status.data?.kill_switch;

  const engage = async () => {
    const reason = window.prompt("Kill switch: stop every run in the tenant now. Reason?");
    if (reason === null) return;
    await act.run("ks", () => api.post("/resilience/killswitch/engage", { reason: reason || "manual" }));
    status.reload();
  };
  const release = async () => {
    await act.run("ks", () => api.post("/resilience/killswitch/release", { reason: "Re-armed after review" }));
    status.reload();
  };

  let page;
  if (section === "overview") page = <Overview go={go} />;
  else if (section === "topology") page = <Topology go={go} />;
  else if (section === "journeys") page = <Journeys id={parts[1]} go={go} />;
  else if (section === "experiments" && parts[1] === "new") page = <ExperimentBuilder key={route} query={query} go={go} />;
  else if (section === "experiments" && parts[2] === "edit") page = <ExperimentBuilder key={route} id={Number(parts[1])} query={query} go={go} />;
  else if (section === "experiments" && parts[1]) page = <ExperimentDetail key={parts[1]} id={Number(parts[1])} go={go} />;
  else if (section === "experiments") page = <ExperimentList go={go} />;
  else if (section === "runs" && parts[1]) page = <RunDetailView key={parts[1]} id={Number(parts[1])} go={go} />;
  else if (section === "runs") page = <RunList go={go} />;
  else if (section === "findings") page = <Findings go={go} />;
  else if (section === "suites") page = <Suites go={go} />;
  else page = <Settings tab={(parts[1] as SettingsTab) || "faults"} go={go} />;

  return (
    <div className="flex flex-1 flex-col lg:flex-row">
      <aside className="border-b border-line bg-surface lg:sticky lg:top-14 lg:h-[calc(100vh-3.5rem)] lg:w-60 lg:shrink-0 lg:border-r lg:border-b-0">
        <nav className="flex gap-1 overflow-x-auto p-2 lg:flex-col lg:p-3" aria-label="Resilience lab">
          {NAV.map((n) => {
            const active = section === n.key;
            return (
              <button key={n.key} onClick={() => go(n.key)} aria-current={active ? "page" : undefined}
                      className={`flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition ${active ? "bg-accent/10 text-accent" : "text-ink-2 hover:bg-surface-2 hover:text-ink"}`}>
                <Icon name={n.icon} /> {n.label}
              </button>
            );
          })}
        </nav>
        <div className="hidden space-y-3 border-t border-line p-4 lg:block">
          <div>
            <label className="field-label" htmlFor="actor">Acting as</label>
            <select id="actor" className="input input-sm" value={actorName} onChange={(e) => setActor(e.target.value)}>
              {ACTORS.map(([n, r]) => <option key={n} value={n}>{n} · {r}</option>)}
            </select>
            <p className="mt-1 text-[11px] text-muted">Asserted identity for role checks — not authentication.</p>
          </div>
          {status.data && (
            <dl className="space-y-1 text-xs">
              <div className="flex justify-between"><dt className="text-muted">Environment</dt><dd className="font-medium">{status.data.environment}</dd></div>
              <div className="flex justify-between"><dt className="text-muted">Policy</dt><dd className="font-medium">v{status.data.policy_version}</dd></div>
              <div className="flex justify-between"><dt className="text-muted">Fault library</dt><dd className="font-medium">v{status.data.library_version}</dd></div>
              <div className="flex justify-between"><dt className="text-muted">Active runs</dt><dd className="font-medium">{status.data.active_runs.length}</dd></div>
              <div className="flex justify-between"><dt className="text-muted">Model gateway</dt><dd className="font-medium">{status.data.model_mode === "groq" ? "active" : "inactive"}</dd></div>
            </dl>
          )}
        </div>
      </aside>

      <main className="min-w-0 flex-1 space-y-5 px-4 py-6 lg:px-8">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex-1">
            <h1 className="text-xl font-semibold tracking-tight">Resilience lab</h1>
            <p className="text-sm text-muted">Fault-injection experiments against the agents, MCP tools and model behind this product.</p>
          </div>
          <select className="input input-sm !w-auto lg:hidden" value={actorName} onChange={(e) => setActor(e.target.value)} aria-label="Acting as">
            {ACTORS.map(([n]) => <option key={n} value={n}>{n}</option>)}
          </select>
          {ks?.engaged ? (
            <button className="btn btn-sm" onClick={release} disabled={act.busy !== null}><Icon name="refresh" /> Release kill switch</button>
          ) : (
            <button className="btn btn-sm btn-danger" onClick={engage} disabled={act.busy !== null}><Icon name="alertOctagon" /> Kill switch</button>
          )}
        </div>
        {act.error && <ErrorBox message={act.error} />}
        {ks?.engaged && (
          <Callout tone="crit" title="Kill switch engaged">
            Every run was stopped and injectors fail open. Engaged by {ks.by}: {ks.reason}. Re-arming needs a tenant admin.
          </Callout>
        )}
        {status.error && <ErrorBox message={status.error} onRetry={status.reload} />}
        <div className="animate-in" key={`${section}-${actorName}`}>{page}</div>
      </main>
    </div>
  );
}
