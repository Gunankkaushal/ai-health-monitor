import type { ReactNode, SVGProps } from "react";
import type { MetricKey, Reading, RulesConfig, Severity } from "../types";

/* ---------------------------------------------------------------------------
   Icons — inline stroke icons (24px grid) so the app has no icon dependency.
--------------------------------------------------------------------------- */
const PATHS = {
  activity: <path d="M22 12h-4l-3 9L9 3l-3 9H2" />,
  heart: <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" />,
  gauge: <><path d="m12 14 4-4" /><path d="M3.34 19a10 10 0 1 1 17.32 0" /></>,
  thermometer: <path d="M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z" />,
  droplet: <path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z" />,
  weight: <><circle cx="12" cy="5" r="3" /><path d="M6.5 8a2 2 0 0 0-1.905 1.46L2.1 18.5A2 2 0 0 0 4 21h16a2 2 0 0 0 1.925-2.54L19.4 9.5A2 2 0 0 0 17.48 8Z" /></>,
  moon: <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" /></>,
  bell: <><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></>,
  message: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  file: <><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" /><path d="M16 13H8M16 17H8M10 9H8" /></>,
  server: <><rect width="20" height="8" x="2" y="2" rx="2" /><rect width="20" height="8" x="2" y="14" rx="2" /><path d="M6 6h.01M6 18h.01" /></>,
  plus: <path d="M5 12h14M12 5v14" />,
  search: <><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></>,
  x: <path d="M18 6 6 18M6 6l12 12" />,
  check: <path d="M20 6 9 17l-5-5" />,
  alertTriangle: <><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /><path d="M12 9v4M12 17h.01" /></>,
  alertOctagon: <><path d="M7.86 2h8.28L22 7.86v8.28L16.14 22H7.86L2 16.14V7.86Z" /><path d="M12 8v4M12 16h.01" /></>,
  info: <><circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" /></>,
  refresh: <><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" /><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" /><path d="M16 16h5v5" /></>,
  download: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m7 10 5 5 5-5M12 15V3" /></>,
  edit: <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />,
  trash: <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronRight: <path d="m9 18 6-6-6-6" />,
  sparkles: <><path d="M12 3l1.9 5.8L20 10.5l-6.1 1.7L12 18l-1.9-5.8L4 10.5l6.1-1.7Z" /><path d="M5 3v4M3 5h4M19 17v4M17 19h4" /></>,
  cpu: <><rect width="16" height="16" x="4" y="4" rx="2" /><rect width="6" height="6" x="9" y="9" rx="1" /><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2" /></>,
  send: <><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>,
  dashboard: <><rect width="7" height="9" x="3" y="3" rx="1" /><rect width="7" height="5" x="14" y="3" rx="1" /><rect width="7" height="9" x="14" y="12" rx="1" /><rect width="7" height="5" x="3" y="16" rx="1" /></>,
  list: <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />,
  users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>,
  shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />,
  clock: <><circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" /></>,
  flask: <><path d="M9 3h6M10 3v6L4.6 18.4A1.8 1.8 0 0 0 6.2 21h11.6a1.8 1.8 0 0 0 1.6-2.6L14 9V3" /><path d="M7 15h10" /></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  bot: <><rect width="16" height="12" x="4" y="8" rx="2" /><path d="M12 8V4H8M2 14h2M20 14h2M15 13v2M9 13v2" /></>,
  arrowUp: <path d="M12 19V5M5 12l7-7 7 7" />,
  arrowDown: <path d="M12 5v14M19 12l-7 7-7-7" />,
  arrowRight: <path d="M5 12h14M12 5l7 7-7 7" />,
  minus: <path d="M5 12h14" />,
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className = "h-4 w-4", ...rest }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"
         strokeLinejoin="round" aria-hidden className={className} {...rest}>
      {PATHS[name]}
    </svg>
  );
}

export const METRIC_ICON: Record<MetricKey, IconName> = {
  heart_rate: "heart",
  blood_pressure: "gauge",
  temperature: "thermometer",
  spo2: "droplet",
  weight: "weight",
  sleep: "moon",
};

/* ---------------------------------------------------------------------------
   Layout primitives
--------------------------------------------------------------------------- */
export function Card({ title, subtitle, icon, actions, children, className = "", bodyClassName = "p-5", flush = false }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: IconName;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  flush?: boolean;
}) {
  return (
    <section className={`rounded-xl border border-line bg-surface shadow-card ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="flex min-w-0 items-center gap-2.5">
            {icon && (
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-surface-2 text-ink-2">
                <Icon name={icon} />
              </span>
            )}
            <div className="min-w-0">
              {title && <h2 className="text-sm font-semibold tracking-tight text-ink">{title}</h2>}
              {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
            </div>
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : bodyClassName}>{children}</div>
    </section>
  );
}

export function Spinner({ label = "Loading…", className = "" }: { label?: string; className?: string }) {
  return (
    <div className={`flex items-center gap-2.5 text-sm text-muted ${className}`} role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-line-strong border-t-accent" />
      {label}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-surface-3 ${className}`} />;
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-crit/30 bg-crit/8 px-3.5 py-3 text-sm">
      <Icon name="alertOctagon" className="mt-0.5 h-4 w-4 shrink-0 text-crit" />
      <span className="flex-1 text-ink">{message}</span>
      {onRetry && (
        <button className="btn btn-sm shrink-0" onClick={onRetry}>
          <Icon name="refresh" /> Retry
        </button>
      )}
    </div>
  );
}

export function InlineError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="flex items-center gap-1.5 text-sm text-crit">
      <Icon name="alertOctagon" className="h-3.5 w-3.5 shrink-0" />
      {children}
    </p>
  );
}

export function Empty({ icon = "info", title, children }: { icon?: IconName; title?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line-strong px-6 py-10 text-center">
      <span className="mb-3 grid h-10 w-10 place-items-center rounded-full bg-surface-2 text-muted">
        <Icon name={icon} className="h-5 w-5" />
      </span>
      {title && <p className="text-sm font-medium text-ink">{title}</p>}
      {children && <p className="mt-1 max-w-sm text-sm text-muted">{children}</p>}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Status display — severity is never colour-only: icon + label always present.
--------------------------------------------------------------------------- */
const SEV: Record<Severity, { icon: IconName; cls: string; label: string }> = {
  critical: { icon: "alertOctagon", cls: "bg-crit/10 text-crit ring-crit/25", label: "Critical" },
  warning: { icon: "alertTriangle", cls: "bg-warn/12 text-warn ring-warn/30", label: "Warning" },
  info: { icon: "info", cls: "bg-info/10 text-info ring-info/25", label: "Info" },
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  const s = SEV[severity];
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${s.cls}`}>
      <Icon name={s.icon} className="h-3.5 w-3.5" />
      {s.label}
    </span>
  );
}

export const SEVERITY_ACCENT: Record<Severity, string> = {
  critical: "bg-crit",
  warning: "bg-warn",
  info: "bg-info",
};

export type Tone = "neutral" | "good" | "warn" | "crit" | "info";

export function Pill({ children, tone = "neutral", title }: { children: ReactNode; tone?: Tone; title?: string }) {
  const cls = {
    neutral: "bg-surface-2 text-ink-2 ring-line",
    good: "bg-good/10 text-good ring-good/25",
    warn: "bg-warn/12 text-warn ring-warn/30",
    crit: "bg-crit/10 text-crit ring-crit/25",
    info: "bg-info/10 text-info ring-info/25",
  }[tone];
  return (
    <span title={title}
          className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}>
      {children}
    </span>
  );
}

export function StatusDot({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  const bg = { neutral: "bg-muted", good: "bg-good", warn: "bg-warn", crit: "bg-crit", info: "bg-info" }[tone];
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0">
      {pulse && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-50 ${bg}`} />}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${bg}`} />
    </span>
  );
}

export function Avatar({ name, size = "md" }: { name: string; size?: "sm" | "md" | "lg" }) {
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "?";
  // Stable hue per name so avatars are distinguishable without meaning anything.
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  const dims = { sm: "h-8 w-8 text-xs", md: "h-10 w-10 text-sm", lg: "h-12 w-12 text-base" }[size];
  return (
    <span className={`grid shrink-0 place-items-center rounded-full font-semibold ${dims}`}
          style={{ background: `hsl(${h} 70% 50% / 0.14)`, color: `hsl(${h} 55% 45%)` }} aria-hidden>
      {initials}
    </span>
  );
}

/** Segmented control for small option sets (time ranges etc.). */
export function Segmented<T extends string | number>({ options, value, onChange, label }: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg bg-surface-2 p-0.5 ring-1 ring-inset ring-line">
      {options.map((o) => (
        <button key={String(o.value)} type="button" onClick={() => onChange(o.value)} aria-pressed={o.value === value}
                className={`h-7 rounded-md px-2.5 text-xs font-medium transition ${
                  o.value === value ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Formatting helpers
--------------------------------------------------------------------------- */
export function parseUtc(iso: string) {
  // Backend timestamps are UTC without suffix.
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
}

export function fmtTime(iso: string) {
  return parseUtc(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtRelative(iso: string) {
  const diff = (Date.now() - parseUtc(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return parseUtc(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function SourceTag({ source }: { source: string }) {
  if (source === "simulator") return <Pill tone="info">Simulated</Pill>;
  if (source === "seed") return <Pill>Sample</Pill>;
  if (source === "experiment") return <Pill tone="warn" title="Written by a resilience experiment; removed when the run ends">Experiment</Pill>;
  return <Pill tone="good">Manual</Pill>;
}

/* ---------------------------------------------------------------------------
   Rule evaluation for display — mirrors the server's documented rules so a
   tile can show whether its latest value is within thresholds.
--------------------------------------------------------------------------- */
const OPS: Record<string, (a: number, b: number) => boolean> = {
  ">": (a, b) => a > b, ">=": (a, b) => a >= b, "<": (a, b) => a < b, "<=": (a, b) => a <= b,
};

export function readingSeverity(rules: RulesConfig | null, reading: Reading | undefined): Severity | "normal" | null {
  if (!rules || !reading) return null;
  const rank = { normal: 0, info: 1, warning: 2, critical: 3 } as const;
  let worst: Severity | "normal" = "normal";
  for (const r of rules.rules) {
    if (r.metric !== reading.metric) continue;
    const hits = r.conditions.map((c) => {
      const v = c.field === "value2" ? reading.value2 : reading.value;
      return v !== null && (OPS[c.op]?.(v, c.threshold) ?? false);
    });
    const matched = r.match === "all" ? hits.every(Boolean) : hits.some(Boolean);
    if (matched && rank[r.severity] > rank[worst]) worst = r.severity;
  }
  return worst;
}

/* ---------------------------------------------------------------------------
   Minimal, safe markdown → React (headings, lists, quotes, bold, code).
   No raw HTML injection.
--------------------------------------------------------------------------- */
export function Markdown({ text }: { text: string }) {
  const inline = (s: string, key: number) => {
    const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_)/g);
    return (
      <span key={key}>
        {parts.map((p, i) => {
          if (p.startsWith("**") && p.endsWith("**")) return <strong key={i}>{p.slice(2, -2)}</strong>;
          if (p.startsWith("`") && p.endsWith("`")) return <code key={i}>{p.slice(1, -1)}</code>;
          if (p.length > 2 && p.startsWith("_") && p.endsWith("_")) return <em key={i}>{p.slice(1, -1)}</em>;
          return p;
        })}
      </span>
    );
  };
  const out: ReactNode[] = [];
  let list: ReactNode[] = [];
  const flush = () => {
    if (list.length) out.push(<ul key={`ul${out.length}`}>{list}</ul>);
    list = [];
  };
  text.split("\n").forEach((line, i) => {
    if (/^[-•] /.test(line)) {
      list.push(<li key={i}>{inline(line.slice(2), i)}</li>);
      return;
    }
    flush();
    if (line.startsWith("# ")) out.push(<h1 key={i}>{inline(line.slice(2), i)}</h1>);
    else if (line.startsWith("## ")) out.push(<h2 key={i}>{inline(line.slice(3), i)}</h2>);
    else if (line.startsWith("> ")) out.push(<blockquote key={i}>{inline(line.slice(2), i)}</blockquote>);
    else if (line.trim()) out.push(<p key={i} className="my-1.5">{inline(line, i)}</p>);
  });
  flush();
  return <div className="prose-sm text-sm leading-relaxed text-ink">{out}</div>;
}
