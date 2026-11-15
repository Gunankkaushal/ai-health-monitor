import { useEffect, useState } from "react";
import { api, errorMessage } from "../api";
import type { Patient } from "../types";
import { Avatar, Icon, InlineError, Pill } from "./ui";

/** Patient header: identity, key demographics and edit/delete actions. */
export function ProfileCard({ patient, onSaved, onDeleted }: {
  patient: Patient;
  onSaved: (p: Patient) => void;
  onDeleted: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [form, setForm] = useState(patient);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setForm(patient);
    setEditing(false);
    setConfirmDelete(false);
    setErr(null);
  }, [patient]);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const p = await api.put<Patient>(`/patients/${patient.id}`, {
        name: form.name, age: Number(form.age), sex: form.sex,
        height_cm: form.height_cm === null || String(form.height_cm) === "" ? null : Number(form.height_cm),
        notes: form.notes,
      });
      onSaved(p);
      setEditing(false);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.del(`/patients/${patient.id}`);
      onDeleted();
    } catch (e) {
      setErr(errorMessage(e));
      setBusy(false);
    }
  };

  const facts: [string, string][] = [
    ["Age", `${patient.age} yrs`],
    ["Sex", patient.sex === "unspecified" ? "—" : patient.sex],
    ["Height", patient.height_cm ? `${patient.height_cm} cm` : "—"],
    ["Patient ID", `#${String(patient.id).padStart(4, "0")}`],
  ];

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-4">
          <Avatar name={patient.name} size="lg" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-xl font-semibold tracking-tight">{patient.name}</h1>
              {patient.is_synthetic && <Pill title="This profile uses generated sample data">Sample data</Pill>}
              {!!patient.critical_alerts && (
                <Pill tone="crit"><Icon name="alertOctagon" className="h-3 w-3" />{patient.critical_alerts} critical</Pill>
              )}
            </div>
            <dl className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1 text-sm">
              {facts.map(([k, v]) => (
                <div key={k} className="flex gap-1.5">
                  <dt className="text-muted">{k}</dt>
                  <dd className={`font-medium text-ink tabular ${k === "Sex" ? "capitalize" : ""}`}>{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {confirmDelete ? (
            <>
              <span className="text-sm text-ink-2">Delete patient and all data?</span>
              <button className="btn btn-sm" onClick={() => setConfirmDelete(false)}>Cancel</button>
              <button className="btn btn-sm btn-danger-solid" onClick={remove} disabled={busy}>
                <Icon name="trash" /> Delete
              </button>
            </>
          ) : !editing && (
            <>
              <button className="btn btn-sm" onClick={() => setEditing(true)}><Icon name="edit" /> Edit profile</button>
              <button className="btn btn-sm btn-icon btn-danger" onClick={() => setConfirmDelete(true)}
                      aria-label="Delete patient" title="Delete patient">
                <Icon name="trash" />
              </button>
            </>
          )}
        </div>
      </div>

      {patient.notes && !editing && <p className="mt-3 max-w-3xl text-sm text-ink-2">{patient.notes}</p>}
      {err && !editing && <div className="mt-2"><InlineError>{err}</InlineError></div>}

      {editing && (
        <div className="animate-in mt-4 rounded-xl border border-line bg-surface p-4 shadow-card">
          <div className="grid gap-3 sm:grid-cols-6">
            <div className="sm:col-span-3">
              <label className="field-label" htmlFor="pf-name">Full name</label>
              <input id="pf-name" className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <label className="field-label" htmlFor="pf-age">Age</label>
              <input id="pf-age" className="input" inputMode="numeric" value={form.age}
                     onChange={(e) => setForm({ ...form, age: e.target.value as unknown as number })} />
            </div>
            <div>
              <label className="field-label" htmlFor="pf-h">Height (cm)</label>
              <input id="pf-h" className="input" inputMode="decimal" value={form.height_cm ?? ""}
                     onChange={(e) => setForm({ ...form, height_cm: e.target.value as unknown as number })} />
            </div>
            <div>
              <label className="field-label" htmlFor="pf-sex">Sex</label>
              <select id="pf-sex" className="input" value={form.sex} onChange={(e) => setForm({ ...form, sex: e.target.value })}>
                <option value="unspecified">Prefer not to say</option>
                <option value="female">Female</option>
                <option value="male">Male</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div className="sm:col-span-6">
              <label className="field-label" htmlFor="pf-notes">Notes</label>
              <input id="pf-notes" className="input" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </div>
          </div>
          {err && <div className="mt-3"><InlineError>{err}</InlineError></div>}
          <div className="mt-4 flex justify-end gap-2">
            <button className="btn btn-sm" onClick={() => setEditing(false)} disabled={busy}>Cancel</button>
            <button className="btn btn-sm btn-primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save changes"}</button>
          </div>
        </div>
      )}
    </div>
  );
}
