import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export type Tone = "neutral" | "accent" | "risk" | "warn" | "ok";

const tones: Record<Tone, string> = {
  neutral: "bg-hover text-muted ring-line",
  accent: "bg-accent-soft text-accent-fg ring-accent/20",
  risk: "bg-risk-soft text-risk-fg ring-risk/20",
  warn: "bg-warn-soft text-warn-fg ring-warn/25",
  ok: "bg-ok-soft text-ok-fg ring-ok/20",
};

export const dotColor: Record<Tone, string> = {
  neutral: "bg-faint",
  accent: "bg-accent",
  risk: "bg-risk",
  warn: "bg-warn",
  ok: "bg-ok",
};

export function Badge({
  children,
  tone = "neutral",
  title,
  dot = false,
  icon: Icon,
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  title?: string;
  dot?: boolean;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-[3px] text-[11.5px] leading-none font-medium ring-1 ring-inset",
        tones[tone],
        title && "cursor-help",
        className,
      )}
    >
      {dot && <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", dotColor[tone])} />}
      {Icon && <Icon aria-hidden className="size-3 shrink-0" />}
      <span className="truncate">{children}</span>
    </span>
  );
}

// A small status dot with an optional live pulse (e.g. "executor running").
export function StatusDot({ tone, pulse = false, className }: { tone: Tone; pulse?: boolean; className?: string }) {
  const text = { neutral: "text-faint", accent: "text-accent", risk: "text-risk", warn: "text-warn", ok: "text-ok" }[tone];
  return (
    <span aria-hidden className={cn("relative inline-flex size-2 shrink-0 rounded-full", dotColor[tone], text, pulse && "animate-pulse-ring", className)} />
  );
}
