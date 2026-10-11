import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";
import { api, type Evidence, type EvidenceRecord } from "../api";
import { dateTime, label } from "../format";
import { Badge, ErrorNote, Loading } from "./ui";

type Ctx = { open: (e: Pick<Evidence, "kind" | "id">) => void };
const DrawerContext = createContext<Ctx>({ open: () => undefined });
export const useEvidence = () => useContext(DrawerContext);

const labelTone = { accepted: "accent", possible: "neutral", quarantined: "risk" } as const;

function scrollTo(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("ring-2", "ring-accent");
  window.setTimeout(() => el.classList.remove("ring-2", "ring-accent"), 1800);
}

function Record({ id, onClose }: { id: string; onClose: () => void }) {
  const rec = useQuery({ queryKey: ["evidence", id], queryFn: () => api<EvidenceRecord>(`/evidence/activity/${id}`) });
  const r = rec.data;
  return (
    <aside className="fixed inset-y-0 right-0 z-40 flex w-full max-w-lg flex-col border-l border-line bg-panel shadow-xl">
      <header className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold">Original record</h2>
        <button onClick={onClose} className="rounded px-2 py-1 text-sm text-muted hover:bg-canvas" aria-label="Close">Close</button>
      </header>
      <div className="flex-1 overflow-y-auto px-4 py-3 text-sm">
        {rec.isLoading && <Loading />}
        {rec.error && <ErrorNote error={rec.error} />}
        {r && (
          <div className="space-y-3">
            {r.injection.flagged && (
              <div className="rounded-md bg-risk-soft px-3 py-2 text-risk">
                <div className="font-medium">Possible injection attempt — not followed</div>
                <div className="text-xs">{r.injection.reason}</div>
                {r.injection.quote && <blockquote className="mt-1 border-l-2 border-risk pl-2 text-xs italic">{r.injection.quote}</blockquote>}
              </div>
            )}
            <div>
              <div className="text-xs text-faint">{dateTime(r.at)} · {r.type} · {r.direction} · {r.source}{r.cancelled ? " · cancelled" : ""}</div>
              <div className="mt-0.5 font-medium">{r.subject ?? "(no subject)"}</div>
              {r.from && <div className="text-xs text-muted">From {r.from}</div>}
              {r.to.length > 0 && <div className="text-xs text-muted">To {r.to.join(", ")}</div>}
              {r.cc.length > 0 && <div className="text-xs text-muted">Cc {r.cc.join(", ")}</div>}
            </div>
            {r.labels.length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium text-muted">What the AI reader found (quotes checked word for word)</div>
                {r.labels.map((l, i) => (
                  <div key={i} className="flex flex-wrap items-center gap-1.5 text-xs">
                    <Badge tone={labelTone[l.status]}>{label(l.label)} · {l.status}</Badge>
                    <span className="italic text-muted">"{l.quote}"</span>
                  </div>
                ))}
              </div>
            )}
            <pre className="whitespace-pre-wrap rounded-md bg-canvas p-3 font-sans text-sm leading-relaxed">{r.body}</pre>
            {r.body_note && <p className="text-xs text-faint">{r.body_note}</p>}
            {r.hidden_text_present && <p className="text-xs text-warn">This email contained text hidden from human readers.</p>}
            <p className="text-xs text-faint">Linked to the account by {label(r.match.method)} ({Math.round(r.match.confidence * 100)}%).</p>
            <button onClick={() => { onClose(); window.setTimeout(() => scrollTo(`ev-${r.id}`), 50); }} className="text-xs text-accent hover:underline">
              Show in timeline
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

export function EvidenceDrawerProvider({ children }: { children: ReactNode }) {
  const [activityId, setActivityId] = useState<string | null>(null);
  const open: Ctx["open"] = (e) => {
    if (e.kind === "activity") setActivityId(e.id);
    else if (e.kind === "usage") scrollTo("usage-panel");
    // Contact evidence ids are "<contact id>:<what>": show that person in the relationship map.
    else if (e.kind === "contact") scrollTo(document.getElementById(`person-${e.id.split(":")[0]}`) ? `person-${e.id.split(":")[0]}` : "people-panel");
    else scrollTo("deal-panel"); // CRM fields and deals
  };
  return (
    <DrawerContext.Provider value={{ open }}>
      {children}
      {activityId && <Record id={activityId} onClose={() => setActivityId(null)} />}
    </DrawerContext.Provider>
  );
}
