import { useEffect, useState } from "react";
import { api, downloadReport, errorMessage } from "../api";
import type { SummaryResponse } from "../types";
import { Card, Empty, ErrorBox, Icon, Markdown, Segmented, Skeleton } from "./ui";

const FORMATS = [
  ["markdown", "Markdown", ".md"],
  ["json", "JSON", ".json"],
  ["csv", "CSV", ".csv"],
] as const;

export function SummaryPanel({ patientId, onActivity }: { patientId: number; onActivity: () => void }) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<SummaryResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [dl, setDl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setErr(null);
  }, [patientId]);

  const generate = async () => {
    setBusy(true);
    setErr(null);
    try {
      setData(await api.post<SummaryResponse>(`/patients/${patientId}/summary`, { days }));
      onActivity();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const download = async (fmt: "markdown" | "json" | "csv") => {
    setDl(fmt);
    setErr(null);
    try {
      await downloadReport(patientId, fmt, days);
      onActivity();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setDl(null);
    }
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
      <Card
        icon="file"
        title="Health summary"
        subtitle="Readings, trends and alerts for the selected period"
        actions={
          <>
            <Segmented label="Summary period" value={days} onChange={setDays}
                       options={[7, 30, 90].map((d) => ({ value: d, label: `${d} days` }))} />
            <button className="btn btn-sm btn-primary" onClick={generate} disabled={busy}>
              <Icon name="sparkles" /> {busy ? "Generating…" : data ? "Regenerate" : "Generate"}
            </button>
          </>
        }
      >
        {err && <div className="mb-4"><ErrorBox message={err} /></div>}
        {busy && (
          <div className="space-y-3">
            <Skeleton className="h-5 w-1/3" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        )}
        {!busy && !data && !err && (
          <Empty icon="file" title="No summary generated">
            Choose a period and generate a summary to review readings, trends and alerts together.
          </Empty>
        )}
        {!busy && data && (
          <div className="animate-in space-y-4">
            {data.ai_narrative && (
              <div className="rounded-xl bg-accent/6 p-4 ring-1 ring-inset ring-accent/15">
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-accent">
                  <Icon name="sparkles" className="h-3.5 w-3.5" /> Overview · {data.mode_label}
                </div>
                <p className="text-sm leading-relaxed whitespace-pre-wrap text-ink">{data.ai_narrative}</p>
              </div>
            )}
            {data.error && <p className="text-xs text-crit">{data.error}</p>}
            <div className="rounded-xl border border-line p-5">
              <Markdown text={data.summary.markdown} />
            </div>
          </div>
        )}
      </Card>

      <Card icon="download" title="Export report" subtitle={`Covers the last ${days} days`}>
        <ul className="space-y-2">
          {FORMATS.map(([f, label, ext]) => (
            <li key={f}>
              <button className="flex w-full items-center gap-3 rounded-lg border border-line px-3 py-2.5 text-left transition hover:bg-surface-2 disabled:opacity-50"
                      onClick={() => download(f)} disabled={dl !== null}>
                <span className="grid h-8 w-8 place-items-center rounded-md bg-surface-2 font-mono text-[10px] font-semibold text-ink-2 uppercase">
                  {ext.slice(1)}
                </span>
                <span className="flex-1">
                  <span className="block text-sm font-medium">{label}</span>
                  <span className="block text-xs text-muted">{dl === f ? "Preparing…" : `Download ${ext}`}</span>
                </span>
                <Icon name="download" className="h-4 w-4 text-muted" />
              </button>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
