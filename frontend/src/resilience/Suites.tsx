import { useState } from "react";
import { api } from "../api";
import { Card, Empty, ErrorBox, Icon, Pill, Skeleton } from "../components/ui";
import { Field, relTime, StatusPill, Table, useAction, useApi, VerdictPill } from "./common";
import type { Experiment, Journey, Suite, SuiteRun } from "./types";

const GATE_TONE = { passed: "good", warned: "warn", blocked: "crit" } as const;

export function Suites({ go }: { go: (p: string) => void }) {
  const suites = useApi<Suite[]>("/resilience/suites", 3000);
  const exps = useApi<Experiment[]>("/resilience/experiments");
  const journeys = useApi<{ journeys: Journey[] }>("/resilience/journeys");
  const act = useAction();
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", journey_id: "ingest", experiment_ids: [] as number[], gate_mode: "block", gate_threshold: 70 });
  const [openRun, setOpenRun] = useState<number | null>(null);
  const runDetail = useApi<SuiteRun>(openRun ? `/resilience/suite-runs/${openRun}` : null, 2000);

  if (suites.error) return <ErrorBox message={suites.error} onRetry={suites.reload} />;

  const create = async () => {
    const r = await act.run("create", () => api.post("/resilience/suites", form));
    if (r) {
      setCreating(false);
      suites.reload();
    }
  };
  const runSuite = async (s: Suite) => {
    const r = await act.run(`run-${s.id}`, () => api.post<SuiteRun>(`/resilience/suites/${s.id}/run`, { wait_for_window: true }));
    if (r) setOpenRun(r.id);
    suites.reload();
  };
  const jExps = (exps.data ?? []).filter((e) => e.journey_id === form.journey_id);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <p className="flex-1 text-sm text-muted">One suite per journey. A suite runs its experiments in sequence and gates the release on the journey score.</p>
        <button className="btn btn-sm btn-primary" onClick={() => setCreating((v) => !v)}><Icon name={creating ? "x" : "plus"} /> {creating ? "Cancel" : "New suite"}</button>
      </div>
      {act.error && <ErrorBox message={act.error} />}

      {creating && (
        <Card title="New suite">
          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Name"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Journey">
              <select className="input" value={form.journey_id} onChange={(e) => setForm({ ...form, journey_id: e.target.value, experiment_ids: [] })}>
                {journeys.data?.journeys.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}
              </select>
            </Field>
            <Field label="Gate">
              <select className="input" value={form.gate_mode} onChange={(e) => setForm({ ...form, gate_mode: e.target.value })}>
                <option value="block">Block release below</option><option value="warn">Warn below</option><option value="report">Report only</option>
              </select>
            </Field>
            <Field label="Threshold"><input className="input tabular" inputMode="numeric" value={form.gate_threshold} onChange={(e) => setForm({ ...form, gate_threshold: Number(e.target.value) || 0 })} /></Field>
          </div>
          <div className="mt-3">
            <div className="field-label">Experiments ({form.experiment_ids.length} selected)</div>
            {jExps.length === 0 ? <p className="text-sm text-muted">No experiments for this journey yet — use “Generate starter suite” on the Journeys page.</p> : (
              <div className="flex flex-wrap gap-2">
                {jExps.map((e) => {
                  const on = form.experiment_ids.includes(e.id);
                  return (
                    <button key={e.id} type="button" onClick={() => setForm({ ...form, experiment_ids: on ? form.experiment_ids.filter((x) => x !== e.id) : [...form.experiment_ids, e.id] })}
                            className={`rounded-full px-3 py-1 text-xs ring-1 ring-inset ${on ? "bg-accent/10 text-accent ring-accent/40" : "text-ink-2 ring-line hover:bg-surface-2"}`}>
                      {e.name}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <button className="btn btn-primary mt-4" onClick={create} disabled={!form.name.trim() || !form.experiment_ids.length || act.busy !== null}>Create suite</button>
        </Card>
      )}

      <Card icon="list" title="Suites" flush>
        {!suites.data ? <div className="p-5"><Skeleton className="h-32" /></div> : suites.data.length === 0 ? (
          <div className="p-5"><Empty icon="list" title="No suites yet">Create one, or generate a starter suite from a journey.</Empty></div>
        ) : (
          <Table head={["Suite", "Journey", "Experiments", "Gate", "Last result", ""]} minWidth={900}>
            {suites.data.map((s) => {
              const lr = s.last_run;
              return (
                <tr key={s.id}>
                  <td className="font-medium">{s.name}</td>
                  <td className="text-ink-2">{s.journey_id}</td>
                  <td className="tabular">{s.experiment_ids.length}</td>
                  <td className="text-xs">{s.gate_mode === "report" ? "Report only" : `${s.gate_mode === "block" ? "Block" : "Warn"} below ${s.gate_threshold}`}</td>
                  <td>
                    {!lr ? <span className="text-xs text-muted">never run</span> : (
                      <button className="flex items-center gap-2 text-left" onClick={() => setOpenRun(lr.id)}>
                        {lr.gate ? <Pill tone={GATE_TONE[lr.gate as keyof typeof GATE_TONE] ?? "neutral"}>{lr.score ?? "—"} · {lr.gate}</Pill> : <StatusPill status={lr.status === "waiting_window" ? "queued" : lr.status} />}
                        <span className="text-xs text-muted">{relTime(lr.created_at)}</span>
                      </button>
                    )}
                  </td>
                  <td className="text-right"><button className="btn btn-sm" onClick={() => runSuite(s)} disabled={act.busy !== null}><Icon name="activity" /> Run now</button></td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>

      {runDetail.data && (
        <Card title={`Suite run ${runDetail.data.id}`} subtitle={runDetail.data.detail || runDetail.data.status}
              actions={<button className="btn btn-sm btn-ghost" onClick={() => setOpenRun(null)}><Icon name="x" /></button>} flush>
          <Table head={["Run", "Experiment", "Status", "Verdict", "Score"]} minWidth={640}>
            {(runDetail.data.runs ?? []).map((r) => (
              <tr key={r.id} className="cursor-pointer hover:bg-surface-2/50" onClick={() => go(`runs/${r.id}`)}>
                <td className="font-mono text-xs">{r.label}</td><td>{r.name}</td>
                <td><StatusPill status={r.status} /></td><td><VerdictPill verdict={r.verdict} /></td><td className="tabular">{r.score ?? "—"}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        <Card icon="cpu" title="Pipeline step — release gate" subtitle="Standard library only; any CI runner with Python can call it">
          <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 font-mono text-xs leading-relaxed">{`# from backend/, against a running backend
python -m app.resilience.cli list
python -m app.resilience.cli run-suite --suite 1 \\
    --wait-for-window      # hold during blackout windows
# exit 0 = passed/warned, 1 = gate blocked, 2 = error`}</pre>
          <p className="mt-2 text-xs text-muted">--wait-for-window makes the pipeline hold while a blackout window is active instead of failing.</p>
        </Card>
        <Card icon="shield" title="What a suite run enforces">
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
            <li>Each experiment re-checks policy, approvals (24 h expiry), blackout windows and the kill switch</li>
            <li>Experiments run one at a time inside the concurrency ceiling</li>
            <li>The gate uses the journey score, so untested edges pull it down</li>
            <li>Every run, refusal and gate decision is in the audit log</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
