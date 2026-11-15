import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, errorMessage } from "../api";
import { Icon, Pill, type Tone } from "../components/ui";
import type { MetricMetaR } from "./types";

/** Load (and optionally poll) an API path. Keeps the last good data while reloading. */
export function useApi<T>(path: string | null, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const pathRef = useRef(path);
  pathRef.current = path;

  const load = useCallback(async () => {
    const p = pathRef.current;
    if (!p) return;
    setLoading(true);
    try {
      const d = await api.get<T>(p);
      if (pathRef.current === p) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (pathRef.current === p) setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setData(null);
    load();
    if (!pollMs || !path) return;
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [path, pollMs, load]);

  return { data, error, loading, reload: load, setData };
}

/** Run an async action with busy/error state. */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(key);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(errorMessage(e));
      return undefined;
    } finally {
      setBusy(null);
    }
  }, []);
  return { busy, error, run, setError };
}

export const VERDICT: Record<string, { label: string; tone: Tone }> = {
  held: { label: "Hypothesis held", tone: "good" },
  rejected: { label: "Hypothesis rejected", tone: "crit" },
  inconclusive: { label: "Inconclusive", tone: "warn" },
  dry_run_passed: { label: "Dry run passed", tone: "good" },
  dry_run_failed: { label: "Dry run failed", tone: "crit" },
};

export const STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  completed: { label: "Completed", tone: "neutral" },
  rolled_back: { label: "Rolled back", tone: "warn" },
  aborted: { label: "Aborted", tone: "warn" },
  refused: { label: "Refused", tone: "crit" },
  failed: { label: "Failed", tone: "crit" },
};

export function VerdictPill({ verdict }: { verdict: string | null }) {
  if (!verdict) return <span className="text-xs text-muted">—</span>;
  const v = VERDICT[verdict] ?? { label: verdict, tone: "neutral" as Tone };
  return <Pill tone={v.tone}>{v.label}</Pill>;
}

export function StatusPill({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <Pill tone={s.tone}>{s.label}</Pill>;
}

export function RiskPill({ risk }: { risk: string }) {
  return <Pill tone={risk === "high" ? "crit" : risk === "medium" ? "warn" : "neutral"}><span className="capitalize">{risk}</span></Pill>;
}

export function SevPill({ severity }: { severity: string }) {
  const tone: Tone = severity === "high" ? "crit" : severity === "medium" ? "warn" : "neutral";
  return <Pill tone={tone}><span className="capitalize">{severity}</span></Pill>;
}

export const APPLIC_TONE: Record<string, Tone> = {
  eligible: "good", not_applicable: "neutral", unavailable: "warn", disabled: "neutral", blocked: "crit", excluded: "warn",
};

export function ScoreValue({ score, size = "lg" }: { score: number | null | undefined; size?: "lg" | "md" }) {
  const cls = score == null ? "text-muted" : score >= 70 ? "text-good" : score >= 50 ? "text-warn" : "text-crit";
  return (
    <span className={`font-semibold tabular ${cls} ${size === "lg" ? "text-3xl" : "text-xl"}`}>
      {score ?? "—"}
      {score != null && <span className="ml-0.5 text-sm font-normal text-muted">/100</span>}
    </span>
  );
}

export function fmtMetric(meta: MetricMetaR | undefined, v: number | null | undefined) {
  if (v == null) return "—";
  if (!meta) return String(v);
  return meta.unit === "%" ? `${v}%` : meta.unit ? `${v} ${meta.unit}` : String(v);
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="mb-2 text-[11px] font-semibold tracking-wider text-muted uppercase">{children}</div>;
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div>
      <label className="field-label" htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <p className="mt-1 text-[11px] text-muted">{hint}</p>}
    </div>
  );
}

export function Callout({ tone = "info", title, children }: { tone?: "info" | "warn" | "crit" | "good"; title?: string; children: ReactNode }) {
  const cls = {
    info: "bg-info/6 ring-info/20 text-ink",
    warn: "bg-warn/8 ring-warn/30 text-ink",
    crit: "bg-crit/8 ring-crit/25 text-ink",
    good: "bg-good/8 ring-good/25 text-ink",
  }[tone];
  const icon = { info: "info", warn: "alertTriangle", crit: "alertOctagon", good: "check" } as const;
  const ic = { info: "text-info", warn: "text-warn", crit: "text-crit", good: "text-good" }[tone];
  return (
    <div className={`flex gap-2.5 rounded-lg px-3.5 py-3 text-sm ring-1 ring-inset ${cls}`}>
      <Icon name={icon[tone]} className={`mt-0.5 h-4 w-4 shrink-0 ${ic}`} />
      <div className="min-w-0">
        {title && <div className="font-semibold">{title}</div>}
        <div className={title ? "mt-0.5 text-ink-2" : ""}>{children}</div>
      </div>
    </div>
  );
}

export function Table({ head, children, minWidth = 640 }: { head: ReactNode[]; children: ReactNode; minWidth?: number }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" style={{ minWidth }}>
        <thead>
          <tr className="border-b border-line bg-surface-2/60 text-left text-xs text-muted">
            {head.map((h, i) => <th key={i} className="px-4 py-2.5 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="[&>tr]:border-b [&>tr]:border-line [&>tr:last-child]:border-0 [&_td]:px-4 [&_td]:py-2.5 [&_td]:align-top">
          {children}
        </tbody>
      </table>
    </div>
  );
}

export function KeyVal({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted">{k}</dt>
      <dd className="mt-0.5 text-sm font-medium tabular">{v}</dd>
    </div>
  );
}

export function relTime(iso: string | null | undefined) {
  if (!iso) return "never";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
