import { useState } from "react";
import { api, errorMessage } from "../api";
import type { Patient } from "../types";
import { Avatar, Empty, ErrorBox, Icon, InlineError, Skeleton } from "./ui";

export function PatientSidebar({ patients, loading, error, selectedId, onSelect, onCreated, onRetry }: {
  patients: Patient[];
  loading: boolean;
  error: string | null;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onCreated: (p: Patient) => void;
  onRetry: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState("");
  const shown = patients.filter((p) => p.name.toLowerCase().includes(q.trim().toLowerCase()));
  const needsAttention = patients.filter((p) => p.critical_alerts).length;

  return (
    <div className="flex h-full flex-col">
      <div className="px-4 pt-5 pb-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold tracking-tight">Patients</h2>
            <p className="text-xs text-muted">
              {patients.length} total{needsAttention ? ` · ${needsAttention} need attention` : ""}
            </p>
          </div>
          <button className={`btn btn-sm ${adding ? "" : "btn-primary"}`} onClick={() => setAdding((v) => !v)}
                  aria-expanded={adding}>
            <Icon name={adding ? "x" : "plus"} />
            {adding ? "Cancel" : "New"}
          </button>
        </div>
        <div className="relative mt-3">
          <Icon name="search" className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted" />
          <input className="input input-sm !pl-8" placeholder="Search patients" value={q}
                 onChange={(e) => setQ(e.target.value)} aria-label="Search patients" />
        </div>
      </div>

      {adding && (
        <div className="animate-in px-4 pb-3">
          <NewPatientForm onCreated={(p) => { setAdding(false); onCreated(p); }} />
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {loading && (
          <div className="space-y-2 px-2">
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14" />)}
          </div>
        )}
        {error && <div className="px-2"><ErrorBox message={error} onRetry={onRetry} /></div>}
        {!loading && !error && patients.length === 0 && (
          <div className="px-2"><Empty icon="users" title="No patients yet">Create a patient profile to start monitoring.</Empty></div>
        )}
        {!loading && patients.length > 0 && shown.length === 0 && (
          <p className="px-3 py-6 text-center text-sm text-muted">No patients match “{q}”.</p>
        )}
        <ul className="space-y-0.5">
          {shown.map((p) => {
            const active = p.id === selectedId;
            return (
              <li key={p.id}>
                <button
                  onClick={() => onSelect(p.id)}
                  aria-current={active}
                  className={`group relative flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left transition ${
                    active ? "bg-accent/10" : "hover:bg-surface-2"
                  }`}
                >
                  {active && <span className="absolute top-2 bottom-2 left-0 w-0.5 rounded-full bg-accent" />}
                  <Avatar name={p.name} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className={`truncate text-sm font-medium ${active ? "text-accent" : "text-ink"}`}>{p.name}</div>
                    <div className="truncate text-xs text-muted">
                      {p.age} yrs · <span className="capitalize">{p.sex === "unspecified" ? "—" : p.sex}</span>
                    </div>
                  </div>
                  {p.critical_alerts ? (
                    <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-crit px-1.5 text-[11px] font-semibold text-white"
                          title={`${p.critical_alerts} critical alert(s)`}>
                      {p.critical_alerts}
                    </span>
                  ) : p.open_alerts ? (
                    <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-warn/15 px-1.5 text-[11px] font-semibold text-warn"
                          title={`${p.open_alerts} open alert(s)`}>
                      {p.open_alerts}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function NewPatientForm({ onCreated }: { onCreated: (p: Patient) => void }) {
  const [form, setForm] = useState({ name: "", age: "", sex: "unspecified", height_cm: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const age = Number(form.age);
    if (!form.name.trim()) return setErr("Name is required.");
    if (form.age === "" || !Number.isInteger(age) || age < 0 || age > 120) return setErr("Age must be a whole number 0–120.");
    setBusy(true);
    try {
      const p = await api.post<Patient>("/patients", {
        name: form.name,
        age,
        sex: form.sex,
        height_cm: form.height_cm ? Number(form.height_cm) : null,
        notes: form.notes,
      });
      onCreated(p);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-line bg-surface-2 p-3.5">
      <div>
        <label className="field-label" htmlFor="np-name">Full name</label>
        <input id="np-name" className="input input-sm" value={form.name} autoFocus
               onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="field-label" htmlFor="np-age">Age</label>
          <input id="np-age" className="input input-sm" inputMode="numeric" value={form.age}
                 onChange={(e) => setForm({ ...form, age: e.target.value })} />
        </div>
        <div>
          <label className="field-label" htmlFor="np-h">Height (cm)</label>
          <input id="np-h" className="input input-sm" inputMode="decimal" value={form.height_cm}
                 onChange={(e) => setForm({ ...form, height_cm: e.target.value })} />
        </div>
      </div>
      <div>
        <label className="field-label" htmlFor="np-sex">Sex</label>
        <select id="np-sex" className="input input-sm" value={form.sex} onChange={(e) => setForm({ ...form, sex: e.target.value })}>
          <option value="unspecified">Prefer not to say</option>
          <option value="female">Female</option>
          <option value="male">Male</option>
          <option value="other">Other</option>
        </select>
      </div>
      {err && <InlineError>{err}</InlineError>}
      <button className="btn btn-primary btn-sm w-full" disabled={busy}>
        {busy ? "Creating…" : "Create patient"}
      </button>
    </form>
  );
}
