import { BadgeCheck, FileText, Zap } from "lucide-react";
import type { ReactNode } from "react";
import type { ClaimEvidence, SignalHit } from "../api";
import { cn } from "../lib/cn";
import { chipText, polarityTone, signalIcon } from "../signals";
import { useEvidence } from "./EvidenceDrawer";
import { Tooltip, type Tone } from "./ui";

const chipTone: Record<Tone, string> = {
  neutral: "bg-hover text-muted ring-line",
  accent: "bg-accent-soft text-accent-fg ring-accent/20",
  risk: "bg-risk-soft text-risk-fg ring-risk/20",
  warn: "bg-warn-soft text-warn-fg ring-warn/25",
  ok: "bg-ok-soft text-ok-fg ring-ok/20",
};

// The icon for a signal type (looked up from a fixed table, so React sees a stable component).
export function SignalIcon({ type, className }: { type: string; className?: string }) {
  const Icon = signalIcon[type] ?? Zap;
  return <Icon aria-hidden className={className} />;
}

// One firing rule: icon (what kind) + tone (risk/urgency/positive/opportunity) + the numbers it computed.
export function SignalChip({ s, size = "sm" }: { s: SignalHit; size?: "sm" | "md" }) {
  const chip = (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md font-medium whitespace-nowrap ring-1 ring-inset",
        size === "md" ? "px-2 py-1 text-[12.5px]" : "px-1.5 py-[3px] text-[11.5px]",
        chipTone[polarityTone[s.polarity]],
      )}
    >
      <SignalIcon type={s.type} className={size === "md" ? "size-3.5" : "size-3"} />
      {chipText(s)}
    </span>
  );
  return s.summary ? <Tooltip content={s.summary}>{chip}</Tooltip> : chip;
}

// A citation: opens the original record (email, meeting, usage, CRM field) that a sentence rests on.
export function EvidenceChip({ e }: { e: Pick<ClaimEvidence, "eid" | "kind" | "id" | "label"> }) {
  const evidence = useEvidence();
  return (
    <Tooltip content={<span><span className="font-medium">{e.eid}</span> · {e.label}</span>}>
      <button
        onClick={() => evidence.open(e)}
        className="mx-0.5 inline-flex h-[18px] items-center rounded-[5px] bg-accent-soft px-1.5 align-[1px] text-[10.5px] font-semibold text-accent-fg ring-1 ring-accent/20 ring-inset transition-colors hover:bg-accent hover:text-white"
      >
        {e.eid}
      </button>
    </Tooltip>
  );
}

// A record link (e.g. "Email · 12 Sep"): the evidence list under a signal.
export function RecordChip({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded-md border border-line bg-panel px-1.5 py-0.5 text-[11.5px] text-muted transition-colors hover:border-accent/40 hover:text-accent-fg"
    >
      <FileText aria-hidden className="size-3" />
      {label}
    </button>
  );
}

// "Verified" seal for AI text that passed the checker; "rule-based" for templates.
export function VerifiedSeal({ verified, children }: { verified: boolean; children?: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-medium ring-1 ring-inset",
        verified ? "bg-ok-soft text-ok-fg ring-ok/20" : "bg-hover text-muted ring-line",
      )}
    >
      {verified && <BadgeCheck aria-hidden className="size-3.5" />}
      {children ?? (verified ? "Every sentence checked" : "Rule-based summary")}
    </span>
  );
}

// Observed / simulated / estimated / untested: every number Casefile reports carries one of these.
export function HonestyLabel({ label }: { label: string }) {
  const tone = label === "observed" ? "bg-ok-soft text-ok-fg ring-ok/20" : label === "simulated" ? "bg-warn-soft text-warn-fg ring-warn/25" : "bg-hover text-muted ring-line";
  const text: Record<string, string> = {
    observed: "Measured on real data or real tools",
    simulated: "Synthetic company: shows the method works, not real-world accuracy",
    estimated: "Calculated, not measured",
    untested: "Not tested yet",
  };
  return (
    <Tooltip content={text[label] ?? label}>
      <span className={cn("inline-flex cursor-help items-center rounded-full px-2 py-0.5 text-[11px] font-semibold tracking-wide uppercase ring-1 ring-inset", tone)}>{label}</span>
    </Tooltip>
  );
}
