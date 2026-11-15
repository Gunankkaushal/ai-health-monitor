// Thin client for the FastAPI backend. No secrets live in the frontend.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Acting identity for the resilience lab (sent as X-Actor). Asserted, not authenticated — see Roles.
const ACTOR_KEY = "ahm-actor";
let actor = (() => {
  try {
    return localStorage.getItem(ACTOR_KEY) || "morgan.author";
  } catch {
    return "morgan.author";
  }
})();
const listeners = new Set<(a: string) => void>();

export function getActor() {
  return actor;
}
export function setActor(a: string) {
  actor = a;
  try {
    localStorage.setItem(ACTOR_KEY, a);
  } catch {
    /* storage unavailable — keep in memory */
  }
  listeners.forEach((l) => l(a));
}
export function onActorChange(fn: (a: string) => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** FastAPI `detail` may be a string, or {message, violations:[{rule,message}]} for policy refusals. */
function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; violations?: { rule: string; message: string }[] };
    const v = d.violations?.map((x) => `[${x.rule}] ${x.message}`).join(" · ");
    return [d.message, v].filter(Boolean).join(": ") || JSON.stringify(detail);
  }
  return String(detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", "X-Actor": actor, ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the backend. Is it running on port 8000? (use the start script)");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? detailText((body as { detail: unknown }).detail) : text;
    throw new ApiError(res.status, detail || `Request failed (${res.status})`);
  }
  return body as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T,>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  del: (path: string, body?: unknown) =>
    request<void>(path, { method: "DELETE", body: body === undefined ? undefined : JSON.stringify(body) }),
};

/** Download any GET endpoint that returns an attachment. */
export async function downloadFile(path: string, fallbackName: string) {
  const res = await fetch(`/api${path}`, { headers: { "X-Actor": actor } });
  if (!res.ok) {
    let msg = `Download failed (${res.status})`;
    try {
      msg = detailText((await res.json()).detail) ?? msg;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function downloadReport(patientId: number, format: "markdown" | "json" | "csv", days: number) {
  await downloadFile(`/patients/${patientId}/report?format=${format}&days=${days}`,
    `health-report.${format === "markdown" ? "md" : format}`);
}

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
