import { useEffect, useRef, useState } from "react";
import type { PatternUnit, PatternVerdict } from "../../api";
import { cn } from "../../lib/cn";
import type { Tone } from "../ui";

// Kept (renewed, late) and lost (churned, downgraded) are states, so they wear the status tokens and
// always travel with a word; the client being compared is identity, so it wears the accent.
export const LOST_OUTCOMES = new Set(["churned", "downgraded"]);

export const outcomeText: Record<string, string> = {
  renewed: "Renewed",
  late: "Renewed late",
  downgraded: "Downgraded",
  churned: "Churned",
};

export const outcomeTone = (outcome: string): Tone => (LOST_OUTCOMES.has(outcome) ? "risk" : "ok");

export const verdictText: Record<PatternVerdict, string> = {
  lost_like: "Looks like lost renewals",
  mixed: "Mixed precedent",
  renewed_like: "Looks like kept renewals",
};

export const verdictTone: Record<PatternVerdict, Tone> = { lost_like: "risk", mixed: "neutral", renewed_like: "ok" };

// A pattern value in plain words, by its unit.
export function fmtValue(v: number | null | undefined, unit: PatternUnit, { mean = false } = {}): string {
  if (v == null) return "—";
  switch (unit) {
    case "score":
      return String(Math.round(v));
    case "pct":
      return `${v > 0 ? "+" : ""}${Math.round(v * 100)}%`;
    case "share":
      return `${Math.round(v * 100)}%`;
    case "days":
      return `${Math.round(v)} d`;
    case "count":
      return mean ? v.toFixed(1) : String(Math.round(v));
    case "flag":
      return mean ? `${Math.round(v * 100)}% yes` : v >= 0.5 ? "yes" : "no";
  }
}

// Width of an element, kept current with a ResizeObserver (charts draw at real pixels, never stretched).
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export function ChartTip({ x, width, children, top = 4 }: { x: number; width: number; children: React.ReactNode; top?: number }) {
  const left = Math.min(Math.max(x, 96), width - 96);
  return (
    <div
      className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-xs whitespace-nowrap shadow-md animate-fade-in"
      style={{ left, top }}
    >
      {children}
    </div>
  );
}

// A short coloured stroke that keys a series in tooltips and legends (line marks get line keys).
export function LineKey({ className, dashed = false, thick = false }: { className: string; dashed?: boolean; thick?: boolean }) {
  return (
    <svg width="16" height="8" aria-hidden className="shrink-0">
      <line x1="1" x2="15" y1="4" y2="4" strokeWidth={thick ? 3 : 2} strokeLinecap="round" strokeDasharray={dashed ? "3 3" : undefined} className={className} />
    </svg>
  );
}

export function DotKey({ className }: { className: string }) {
  return <span aria-hidden className={cn("inline-block size-2 shrink-0 rounded-full", className)} />;
}

// The nearest past cycles as a strip of dots: red = revenue lost, green = kept. Words beside it.
export function PrecedentDots({ outcomes, size = "sm", animate = false }: { outcomes: boolean[]; size?: "sm" | "md"; animate?: boolean }) {
  if (!outcomes.length) return <span className="text-[12px] text-faint">—</span>;
  const lost = outcomes.filter(Boolean).length;
  return (
    <span className="inline-flex items-center gap-2" aria-label={`${lost} of ${outcomes.length} most similar past cycles lost revenue`}>
      <span className={cn("inline-flex", size === "md" ? "gap-1" : "gap-[3px]")}>
        {[...outcomes].sort((a, b) => Number(b) - Number(a)).map((isLost, i) => (
          <span
            key={i}
            aria-hidden
            className={cn("rounded-full", size === "md" ? "size-2.5" : "size-2", isLost ? "bg-risk" : "bg-ok", animate && "animate-scale-in")}
            style={animate ? { animationDelay: `${i * 45}ms` } : undefined}
          />
        ))}
      </span>
      <span className="text-[12px] text-muted tabular-nums">
        {lost}/{outcomes.length} lost
      </span>
    </span>
  );
}

// ---------------------------------------------------------------- words for each number (charts explain themselves)

// Moving along a map axis in words: [when the number rises, when it falls].
export const directionWords: Record<string, [string, string]> = {
  risk: ["higher risk score", "lower risk score"],
  "usage.users_vs_6mo": ["usage growing vs 6 months ago", "usage falling vs 6 months ago"],
  "usage.users_vs_prior": ["usage growing vs last quarter", "usage falling vs last quarter"],
  "usage.seat_use": ["more seats in use", "fewer seats in use"],
  "eng.days_since_meeting": ["longer since a meeting", "met recently"],
  "eng.meetings_90d": ["more meetings", "fewer meetings"],
  "eng.customer_emails_60d": ["more emails from them", "fewer emails from them"],
  "crm.next_step_missing": ["no next step", "next step set"],
  "eng.buyer_met_60d": ["buyer engaged", "buyer not met"],
};
