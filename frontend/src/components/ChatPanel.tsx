import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../api";
import type { ChatResponse } from "../types";
import { Icon, InlineError, Markdown, Pill } from "./ui";

type Msg = { role: "user"; content: string } | { role: "assistant"; content: string; meta: ChatResponse };

const SUGGESTIONS: [string, string][] = [
  ["How are the readings trending?", "Trends across all vitals"],
  ["Are there any alerts I should know about?", "Open alerts and what they mean"],
  ["Any general wellness tips based on the data?", "Lifestyle suggestions"],
  ["Give me a summary of the last 30 days", "30-day overview"],
];

export function ChatPanel({ patientId, patientName, onActivity }: { patientId: number; patientName: string; onActivity: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMsgs([]);
    setErr(null);
  }, [patientId]);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [msgs, busy]);

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    if (message.length > 1000) return setErr("Please keep messages under 1000 characters.");
    setErr(null);
    setInput("");
    const history = msgs.map((m) => ({ role: m.role, content: m.content }));
    setMsgs((m) => [...m, { role: "user", content: message }]);
    setBusy(true);
    try {
      const res = await api.post<ChatResponse>(`/patients/${patientId}/chat`, { message, history });
      setMsgs((m) => [...m, { role: "assistant", content: res.answer, meta: res }]);
      onActivity();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const firstName = patientName.split(" ")[0];

  return (
    <section className="flex h-[calc(100vh-17rem)] min-h-[32rem] flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-card">
      <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent/10 text-accent"><Icon name="bot" /></span>
          <div>
            <h2 className="text-sm font-semibold tracking-tight">Health assistant</h2>
            <p className="text-xs text-muted">Answers are grounded in {firstName}'s stored readings and alerts</p>
          </div>
        </div>
        {msgs.length > 0 && (
          <button className="btn btn-sm btn-ghost" onClick={() => setMsgs([])} disabled={busy}>
            <Icon name="refresh" /> New chat
          </button>
        )}
      </header>

      <div ref={scrollRef} className="flex-1 space-y-5 overflow-y-auto px-5 py-5" aria-live="polite">
        {msgs.length === 0 && (
          <div className="mx-auto flex h-full max-w-xl flex-col items-center justify-center text-center">
            <span className="mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-accent/10 text-accent">
              <Icon name="sparkles" className="h-6 w-6" />
            </span>
            <h3 className="text-base font-semibold">Ask about {firstName}'s health data</h3>
            <p className="mt-1 text-sm text-muted">
              Questions are routed to the monitoring, alert, wellness and summary agents.
            </p>
            <div className="mt-6 grid w-full gap-2 sm:grid-cols-2">
              {SUGGESTIONS.map(([q, hint]) => (
                <button key={q} onClick={() => send(q)}
                        className="rounded-xl border border-line bg-surface px-4 py-3 text-left transition hover:border-line-strong hover:bg-surface-2">
                  <div className="text-sm font-medium text-ink">{q}</div>
                  <div className="mt-0.5 text-xs text-muted">{hint}</div>
                </button>
              ))}
            </div>
          </div>
        )}

        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-sm text-accent-ink">{m.content}</div>
            </div>
          ) : (
            <div key={i} className="flex gap-3">
              <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface-2 text-ink-2 ring-1 ring-line">
                <Icon name="bot" className="h-3.5 w-3.5" />
              </span>
              <div className="min-w-0 max-w-[90%] flex-1">
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <Pill tone={m.meta.mode === "groq" ? "info" : m.meta.mode === "demo-fallback" ? "warn" : "neutral"}>
                    {m.meta.mode_label}
                  </Pill>
                  <span className="text-[11px] text-muted">via {m.meta.routed_to.join(", ")}</span>
                </div>
                <div className="rounded-2xl rounded-tl-md border border-line bg-surface-2/60 px-4 py-3">
                  <Markdown text={m.content} />
                  {m.meta.agents.some((a) => a.error) && (
                    <p className="mt-2 text-xs text-crit">{m.meta.agents.filter((a) => a.error).map((a) => `${a.agent}: ${a.error}`).join(" · ")}</p>
                  )}
                </div>
                <details className="group mt-1.5 text-xs text-muted">
                  <summary className="inline-flex cursor-pointer list-none items-center gap-1 hover:text-ink">
                    <Icon name="chevronRight" className="h-3 w-3 transition group-open:rotate-90" />
                    Tools used
                  </summary>
                  <div className="mt-1.5 space-y-1 pl-4">
                    {m.meta.agents.map((a) => (
                      <div key={a.agent}>
                        <span className="font-medium text-ink-2">{a.agent}</span>:{" "}
                        {a.tool_calls.length
                          ? a.tool_calls.map((t) => <code key={t.tool} className="mr-1 rounded bg-surface-2 px-1 font-mono">{t.tool}</code>)
                          : "no tools"}
                      </div>
                    ))}
                  </div>
                </details>
              </div>
            </div>
          ),
        )}

        {busy && (
          <div className="flex items-center gap-3">
            <span className="grid h-7 w-7 place-items-center rounded-full bg-surface-2 text-ink-2 ring-1 ring-line">
              <Icon name="bot" className="h-3.5 w-3.5" />
            </span>
            <span className="inline-flex gap-1 rounded-2xl border border-line bg-surface-2/60 px-4 py-3" aria-label="Assistant is thinking">
              {[0, 150, 300].map((d) => (
                <span key={d} className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" style={{ animationDelay: `${d}ms` }} />
              ))}
            </span>
          </div>
        )}
      </div>

      <div className="border-t border-line bg-surface px-5 py-4">
        {err && <div className="mb-2"><InlineError>{err}</InlineError></div>}
        <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <textarea
            className="input max-h-40 min-h-[2.5rem] flex-1 resize-none" rows={1} placeholder="Ask about readings, trends or alerts…"
            value={input} maxLength={1000} aria-label="Message"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
          />
          <button className="btn btn-primary h-10 w-10 !p-0" disabled={busy || !input.trim()} aria-label="Send message">
            <Icon name="send" />
          </button>
        </form>
        <p className="mt-2 text-[11px] text-muted">
          The assistant provides general information only and does not diagnose or recommend treatment.
        </p>
      </div>
    </section>
  );
}
