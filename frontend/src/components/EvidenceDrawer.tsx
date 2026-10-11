import { useQuery } from "@tanstack/react-query";
import { ArrowDownLeft, ArrowUpRight, Calendar, EyeOff, Link2, Mail, ShieldAlert } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { api, type Evidence, type EvidenceRecord } from "../api";
import { dateTime, label } from "../format";
import { Badge, Button, Callout, DefinitionList, Drawer, ErrorNote, SectionLabel, Skeleton } from "./ui";

type Ctx = { open: (e: Pick<Evidence, "kind" | "id">) => void };
const DrawerContext = createContext<Ctx>({ open: () => undefined });
export const useEvidence = () => useContext(DrawerContext);

const labelTone = { accepted: "accent", possible: "neutral", quarantined: "risk" } as const;

// Bring a panel on the page into view and pulse a ring around it.
export function spotlight(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const ring = ["ring-2", "ring-accent", "ring-offset-4", "ring-offset-canvas"];
  el.classList.add(...ring);
  window.setTimeout(() => el.classList.remove(...ring), 1800);
}

function RecordView({ id, onClose }: { id: string; onClose: () => void }) {
  const rec = useQuery({ queryKey: ["evidence", id], queryFn: () => api<EvidenceRecord>(`/evidence/activity/${id}`) });
  const r = rec.data;
  const Kind = r?.type === "meeting" ? Calendar : Mail;
  return (
    <Drawer
      title="Original record"
      onClose={onClose}
      actions={
        r && (
          <Button size="xs" variant="ghost" icon={Link2} onClick={() => { onClose(); window.setTimeout(() => spotlight(`ev-${r.id}`), 60); }}>
            Show in timeline
          </Button>
        )
      }
    >
      {rec.isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-6 w-3/4" />
          <Skeleton className="h-40 w-full rounded-xl" />
        </div>
      )}
      {rec.error && <ErrorNote error={rec.error} />}
      {r && (
        <div className="space-y-5 animate-fade-in">
          {r.injection.flagged && (
            <Callout tone="risk" icon={ShieldAlert} title="Instructions aimed at software — not followed">
              {r.injection.reason}
              {r.injection.quote && <blockquote className="mt-2 border-l-2 border-risk/50 pl-3 text-[12.5px] text-ink italic">“{r.injection.quote}”</blockquote>}
            </Callout>
          )}
          <div>
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
              <span className="inline-flex items-center gap-1 font-medium text-ink capitalize">
                <Kind className="size-3.5" /> {r.type}
              </span>
              <span>·</span>
              {r.direction === "outbound" ? <ArrowUpRight className="size-3.5" /> : <ArrowDownLeft className="size-3.5" />}
              <span>{r.direction}</span>
              <span>·</span>
              <span>{dateTime(r.at)}</span>
              <Badge>{label(r.source)}</Badge>
              {r.cancelled && <Badge tone="warn">cancelled</Badge>}
            </div>
            <h3 className="mt-2 text-[17px] leading-snug font-semibold tracking-[-0.01em]">{r.subject ?? "(no subject)"}</h3>
          </div>
          <DefinitionList
            items={[
              ...(r.from ? ([["From", r.from]] as [string, string][]) : []),
              ...(r.to.length ? ([["To", r.to.join(", ")]] as [string, string][]) : []),
              ...(r.cc.length ? ([["Cc", r.cc.join(", ")]] as [string, string][]) : []),
              ["Linked by", `${label(r.match.method)} · ${Math.round(r.match.confidence * 100)}% confidence`],
            ]}
          />
          {r.labels.length > 0 && (
            <div className="space-y-2">
              <SectionLabel>What the AI reader found · quotes checked word for word</SectionLabel>
              {r.labels.map((l, i) => (
                <div key={i} className="rounded-lg border border-line bg-subtle px-3 py-2">
                  <Badge tone={labelTone[l.status]}>{label(l.label)} · {l.status}</Badge>
                  <p className="mt-1.5 text-[13px] text-ink italic">“{l.quote}”</p>
                </div>
              ))}
            </div>
          )}
          <div className="rounded-xl border border-line bg-subtle p-4">
            <pre className="font-sans text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink">{r.body}</pre>
          </div>
          {r.body_note && <p className="text-xs text-faint">{r.body_note}</p>}
          {r.hidden_text_present && (
            <Callout tone="warn" icon={EyeOff} title="Hidden text">
              This email contained text hidden from human readers. It was stripped and flagged.
            </Callout>
          )}
        </div>
      )}
    </Drawer>
  );
}

export function EvidenceDrawerProvider({ children }: { children: ReactNode }) {
  const [activityId, setActivityId] = useState<string | null>(null);
  const open = useCallback<Ctx["open"]>((e) => {
    if (e.kind === "activity") setActivityId(e.id);
    else if (e.kind === "usage") spotlight("usage-panel");
    // Contact evidence ids are "<contact id>:<what>": show that person in the relationship map.
    else if (e.kind === "contact") {
      const person = `person-${e.id.split(":")[0]}`;
      spotlight(document.getElementById(person) ? person : "people-panel");
    } else spotlight("deal-panel"); // CRM fields and deals
  }, []);
  const value = useMemo(() => ({ open }), [open]);
  return (
    <DrawerContext.Provider value={value}>
      {children}
      {activityId && <RecordView id={activityId} onClose={() => setActivityId(null)} />}
    </DrawerContext.Provider>
  );
}
