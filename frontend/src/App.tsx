import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { AlertsPanel, AlertsPreview } from "./components/AlertsPanel";
import { ChatPanel } from "./components/ChatPanel";
import { PipelineCard, ReadingForm, SimulatorCard } from "./components/DataEntry";
import { PatientSidebar } from "./components/PatientSidebar";
import { ProfileCard } from "./components/ProfileCard";
import { SummaryPanel } from "./components/SummaryPanel";
import { ActivityPanel, AgentsInfo, McpPanel } from "./components/SystemPanels";
import { ReadingsTable, TrendChart, VitalsTiles } from "./components/Trends";
import { ResilienceApp } from "./resilience/ResilienceApp";
import { Empty, ErrorBox, Icon, type IconName, Skeleton, StatusDot } from "./components/ui";
import type {
  Activity,
  AddReadingsResponse,
  Alert,
  AppConfig,
  McpStatus,
  MetricKey,
  Patient,
  Reading,
  RulesConfig,
} from "./types";

function useLoad<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    loader()
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(errorMessage(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, loading, error, reload, setData };
}

type Theme = "light" | "dark";
function useTheme() {
  const initial = (): Theme => {
    const set = document.documentElement.dataset.theme;
    if (set === "light" || set === "dark") return set;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  };
  const [theme, setTheme] = useState<Theme>(initial);
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("ahm-theme", next);
    } catch {
      /* storage unavailable — theme still applies for this session */
    }
    setTheme(next);
  };
  return { theme, toggle };
}

type Tab = "overview" | "readings" | "alerts" | "assistant" | "reports" | "system";
const TABS: { key: Tab; label: string; icon: IconName }[] = [
  { key: "overview", label: "Overview", icon: "dashboard" },
  { key: "readings", label: "Readings", icon: "activity" },
  { key: "alerts", label: "Alerts", icon: "bell" },
  { key: "assistant", label: "Assistant", icon: "message" },
  { key: "reports", label: "Reports", icon: "file" },
  { key: "system", label: "System", icon: "server" },
];

export default function App() {
  const config = useLoad(() => api.get<AppConfig>("/config"), []);
  const patients = useLoad(() => api.get<Patient[]>("/patients"), []);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [metric, setMetric] = useState<MetricKey>("heart_rate");
  const [days, setDays] = useState(30);
  const [tab, setTabState] = useState<Tab>(() => {
    const h = window.location.hash.slice(1);
    return TABS.some((t) => t.key === h) ? (h as Tab) : "overview";
  });
  const setTab = (t: Tab) => {
    setTabState(t);
    history.replaceState(null, "", `#${t}`);
  };
  const [drawer, setDrawer] = useState(false);
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const inLab = hash.startsWith("#lab");
  const goLab = useCallback((p: string) => {
    window.location.hash = `lab/${p}`;
    window.scrollTo({ top: 0 });
  }, []);
  const goPatients = () => {
    window.location.hash = tab;
  };
  const [pipeline, setPipeline] = useState<AddReadingsResponse | null>(null);
  const [mcp, setMcp] = useState<McpStatus | null>(null);
  const [mcpErr, setMcpErr] = useState<string | null>(null);
  const [rules, setRules] = useState<RulesConfig | null>(null);
  const { theme, toggle } = useTheme();

  useEffect(() => {
    if (selectedId === null && patients.data?.length) setSelectedId(patients.data[0].id);
  }, [patients.data, selectedId]);

  const readings = useLoad(
    () => (selectedId ? api.get<Reading[]>(`/patients/${selectedId}/readings?days=${days}`) : Promise.resolve([])),
    [selectedId, days],
  );
  const alerts = useLoad(
    () => (selectedId ? api.get<Alert[]>(`/patients/${selectedId}/alerts`) : Promise.resolve([])),
    [selectedId],
  );
  const activity = useLoad(
    () => (selectedId ? api.get<Activity[]>(`/activity?patient_id=${selectedId}&limit=60`) : Promise.resolve([])),
    [selectedId],
  );

  const loadMcp = useCallback(async () => {
    try {
      setMcp(await api.get<McpStatus>("/mcp/status"));
      setMcpErr(null);
    } catch (e) {
      setMcpErr(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    loadMcp();
    const id = setInterval(loadMcp, 10000);
    return () => clearInterval(id);
  }, [loadMcp]);

  useEffect(() => {
    if (mcp && mcp.connected > 0 && !rules)
      api.get<RulesConfig>("/rules").then(setRules).catch(() => setRules(null));
  }, [mcp, rules]);

  const patient = patients.data?.find((p) => p.id === selectedId) ?? null;
  const cfg = config.data;
  const openAlerts = (alerts.data ?? []).filter((a) => !a.acknowledged).length;
  const mcpHealthy = mcp ? mcp.connected === mcp.servers.length : null;

  const afterReadings = (r: AddReadingsResponse) => {
    setPipeline(r);
    readings.reload();
    alerts.reload();
    activity.reload();
    patients.reload();
    loadMcp();
  };

  const selectPatient = (id: number) => {
    setSelectedId(id);
    setPipeline(null);
    setDrawer(false);
  };

  const sidebar = (
    <PatientSidebar
      patients={patients.data ?? []}
      loading={patients.loading && !patients.data}
      error={patients.error}
      selectedId={selectedId}
      onSelect={selectPatient}
      onCreated={(p) => { patients.reload(); selectPatient(p.id); setTab("overview"); }}
      onRetry={patients.reload}
    />
  );

  return (
    <div className="flex min-h-screen flex-col">
      {/* ── Top bar ─────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-30 border-b border-line bg-surface/85 backdrop-blur">
        <div className="flex h-14 items-center gap-3 px-4 lg:px-6">
          {!inLab && (
            <button className="btn btn-ghost btn-icon lg:hidden" onClick={() => setDrawer(true)} aria-label="Open patient list">
              <Icon name="menu" />
            </button>
          )}
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-accent-ink">
              <Icon name="activity" className="h-4.5 w-4.5" strokeWidth={2.4} />
            </span>
            <div className="leading-tight">
              <div className="text-sm font-semibold tracking-tight">AI Health Monitor</div>
              <div className="hidden text-[11px] text-muted sm:block">Vitals monitoring &amp; intelligent alerts</div>
            </div>
          </div>

          <nav className="ml-2 flex rounded-lg bg-surface-2 p-0.5 ring-1 ring-inset ring-line sm:ml-4" aria-label="Sections">
            {([["patients", "Patients", "users"], ["lab", "Resilience lab", "flask"]] as const).map(([k, label, icon]) => {
              const active = (k === "lab") === inLab;
              return (
                <button key={k} onClick={() => (k === "lab" ? goLab("overview") : goPatients())} aria-current={active ? "page" : undefined}
                        className={`inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition sm:text-sm ${active ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink"}`}>
                  <Icon name={icon} className="h-3.5 w-3.5" /><span className={k === "lab" ? "hidden sm:inline" : ""}>{label}</span>
                  {k === "lab" && <span className="sm:hidden">Lab</span>}
                </button>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            {cfg && (
              <span className="hidden items-center gap-2 rounded-full border border-line px-3 py-1 text-xs text-ink-2 md:inline-flex"
                    title={cfg.mode === "groq" ? `Model: ${cfg.model}` : cfg.reason}>
                <Icon name="sparkles" className={`h-3.5 w-3.5 ${cfg.mode === "groq" ? "text-accent" : "text-muted"}`} />
                {cfg.mode === "groq" ? <>AI · <span className="font-medium text-ink">{cfg.model}</span></> : "AI offline · rule-based"}
              </span>
            )}
            {mcp && (
              <button onClick={() => setTab("system")}
                      className="hidden items-center gap-2 rounded-full border border-line px-3 py-1 text-xs text-ink-2 transition hover:bg-surface-2 sm:inline-flex"
                      title="Tool server status">
                <StatusDot tone={mcpHealthy ? "good" : "crit"} pulse={!mcpHealthy} />
                {mcpHealthy ? "All systems operational" : `${mcp.connected}/${mcp.servers.length} servers online`}
              </button>
            )}
            <button className="btn btn-ghost btn-icon" onClick={toggle}
                    aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
                    title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              <Icon name={theme === "dark" ? "sun" : "moon"} />
            </button>
          </div>
        </div>
      </header>

      {inLab ? <ResilienceApp route={hash.replace(/^#lab\/?/, "")} go={goLab} /> : (
      <div className="flex flex-1">
        {/* ── Sidebar (desktop) ─────────────────────────────────────────── */}
        <aside className="sticky top-14 hidden h-[calc(100vh-3.5rem)] w-72 shrink-0 border-r border-line bg-surface lg:block">
          {sidebar}
        </aside>

        {/* ── Sidebar (mobile drawer) ───────────────────────────────────── */}
        {drawer && (
          <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Patients">
            <div className="absolute inset-0 bg-black/40" onClick={() => setDrawer(false)} />
            <div className="animate-in absolute inset-y-0 left-0 w-80 max-w-[85vw] bg-surface shadow-pop">
              <button className="btn btn-ghost btn-icon absolute top-3 right-3 z-10" onClick={() => setDrawer(false)} aria-label="Close">
                <Icon name="x" />
              </button>
              {sidebar}
            </div>
          </div>
        )}

        {/* ── Main ──────────────────────────────────────────────────────── */}
        <main className="min-w-0 flex-1">
          {config.error && <div className="px-4 pt-5 lg:px-8"><ErrorBox message={config.error} onRetry={config.reload} /></div>}

          {!patient && patients.loading && (
            <div className="space-y-4 px-4 py-6 lg:px-8">
              <Skeleton className="h-12 w-72" />
              <Skeleton className="h-10 w-full max-w-xl" />
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6">
                {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-36" />)}
              </div>
            </div>
          )}
          {!patient && !patients.loading && !patients.error && (
            <div className="px-4 py-16 lg:px-8">
              <Empty icon="users" title="No patient selected">Select a patient from the list, or create a new profile to get started.</Empty>
            </div>
          )}

          {patient && cfg && (
            <>
              <div className="border-b border-line bg-surface px-4 pt-6 lg:px-8">
                <ProfileCard
                  patient={patient}
                  onSaved={() => patients.reload()}
                  onDeleted={() => { setSelectedId(null); patients.reload(); }}
                />
                <nav className="-mb-px mt-5 flex gap-1 overflow-x-auto" aria-label="Patient sections">
                  {TABS.map((t) => {
                    const active = tab === t.key;
                    return (
                      <button key={t.key} onClick={() => setTab(t.key)} aria-current={active ? "page" : undefined}
                              className={`inline-flex shrink-0 items-center gap-2 border-b-2 px-3 py-2.5 text-sm font-medium transition ${
                                active ? "border-accent text-ink" : "border-transparent text-muted hover:border-line-strong hover:text-ink"}`}>
                        <Icon name={t.icon} className={`h-4 w-4 ${active ? "text-accent" : ""}`} />
                        {t.label}
                        {t.key === "alerts" && openAlerts > 0 && (
                          <span className="rounded-full bg-crit/12 px-1.5 text-[11px] font-semibold text-crit tabular">{openAlerts}</span>
                        )}
                      </button>
                    );
                  })}
                </nav>
              </div>

              <div key={`${patient.id}-${tab}`} className={`animate-in space-y-5 px-4 lg:px-8 ${tab === "assistant" || tab === "reports" ? "pt-6" : "py-6"}`}>
                {pipeline && (tab === "overview" || tab === "readings") && (
                  <PipelineCard result={pipeline} onClose={() => setPipeline(null)} />
                )}

                {tab === "overview" && (
                  <>
                    {readings.error && <ErrorBox message={readings.error} onRetry={readings.reload} />}
                    {readings.loading && !readings.data ? (
                      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6">
                        {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-36" />)}
                      </div>
                    ) : (
                      <>
                        <VitalsTiles metrics={cfg.metrics} readings={readings.data ?? []} rules={rules}
                                     selected={metric} onSelect={setMetric} />
                        <TrendChart metric={metric} meta={cfg.metrics.find((m) => m.key === metric)}
                                    readings={readings.data ?? []} rules={rules} days={days} onDays={setDays} />
                      </>
                    )}
                    <div className="grid gap-5 xl:grid-cols-2">
                      <AlertsPreview alerts={alerts.data ?? []} loading={alerts.loading && !alerts.data} onViewAll={() => setTab("alerts")} />
                      <ActivityPanel compact items={activity.data ?? []} loading={activity.loading} error={activity.error}
                                     onRefresh={activity.reload} onViewAll={() => setTab("system")} />
                    </div>
                  </>
                )}

                {tab === "readings" && (
                  <>
                    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
                      <ReadingForm patientId={patient.id} metrics={cfg.metrics} onAdded={afterReadings} />
                      <SimulatorCard patientId={patient.id} onAdded={afterReadings} />
                    </div>
                    <ReadingsTable readings={readings.data ?? []} patientId={patient.id}
                                   onChanged={() => { readings.reload(); alerts.reload(); }} />
                  </>
                )}

                {tab === "alerts" && (
                  <AlertsPanel
                    patientId={patient.id}
                    alerts={alerts.data ?? []}
                    loading={alerts.loading && !alerts.data}
                    error={alerts.error}
                    rules={rules}
                    onChanged={() => { alerts.reload(); patients.reload(); activity.reload(); }}
                    onRetry={alerts.reload}
                  />
                )}

                {tab === "system" && (
                  <div className="grid gap-5 2xl:grid-cols-2">
                    <div className="space-y-5">
                      <McpPanel status={mcp} error={mcpErr} onChanged={() => { loadMcp(); setRules(null); }} />
                      <AgentsInfo agents={cfg.agents} />
                    </div>
                    <ActivityPanel items={activity.data ?? []} loading={activity.loading} error={activity.error}
                                   onRefresh={activity.reload} />
                  </div>
                )}
              </div>

              {/* Kept mounted so conversation and generated summary survive tab switches. */}
              <div className={tab === "assistant" ? "px-4 pb-6 lg:px-8" : "hidden"}>
                <ChatPanel patientId={patient.id} patientName={patient.name} onActivity={activity.reload} />
              </div>
              <div className={tab === "reports" ? "px-4 pb-6 lg:px-8" : "hidden"}>
                <SummaryPanel patientId={patient.id} onActivity={activity.reload} />
              </div>
            </>
          )}

          <footer className="px-4 pb-6 lg:px-8">
            <p className="flex items-start gap-2 border-t border-line pt-4 text-xs text-muted">
              <Icon name="shield" className="mt-px h-3.5 w-3.5 shrink-0" />
              For informational monitoring only. Not a medical device and does not provide diagnosis or treatment advice.
              In an emergency, contact local emergency services.
            </p>
          </footer>
        </main>
      </div>
      )}
    </div>
  );
}
