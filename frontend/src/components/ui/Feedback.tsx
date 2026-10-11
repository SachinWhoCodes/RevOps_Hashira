import { CircleAlert, CircleCheck, Info, LoaderCircle, TriangleAlert, X, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import type { Tone } from "./Badge";

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle aria-hidden className={cn("size-4 animate-spin", className)} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("skeleton rounded-md", className)} />;
}

// Placeholder shaped like what is loading, so the page does not jump when data arrives.
export function Loading({ variant = "list", rows = 4, label = "Loading" }: { variant?: "list" | "cards" | "table" | "inline"; rows?: number; label?: string }) {
  if (variant === "inline") {
    return (
      <span role="status" className="inline-flex items-center gap-2 text-sm text-muted">
        <Spinner /> {label}…
      </span>
    );
  }
  if (variant === "cards") {
    return (
      <div role="status" aria-label={label} className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="rounded-xl border border-line bg-panel p-5">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="mt-4 h-7 w-32" />
            <Skeleton className="mt-3 h-3 w-40" />
          </div>
        ))}
      </div>
    );
  }
  return (
    <div role="status" aria-label={label} className={cn("space-y-3", variant === "table" && "p-4")}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-9 shrink-0 rounded-lg" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className={cn("h-3", i % 2 ? "w-1/2" : "w-2/3")} />
          </div>
        </div>
      ))}
    </div>
  );
}

const calloutTone: Record<Tone, { box: string; icon: LucideIcon; iconColor: string }> = {
  neutral: { box: "border-line bg-subtle text-ink", icon: Info, iconColor: "text-muted" },
  accent: { box: "border-accent/25 bg-accent-soft text-ink", icon: Info, iconColor: "text-accent-fg" },
  risk: { box: "border-risk/25 bg-risk-soft text-ink", icon: CircleAlert, iconColor: "text-risk-fg" },
  warn: { box: "border-warn/30 bg-warn-soft text-ink", icon: TriangleAlert, iconColor: "text-warn-fg" },
  ok: { box: "border-ok/25 bg-ok-soft text-ink", icon: CircleCheck, iconColor: "text-ok-fg" },
};

// A message block: what happened or what to know, with an optional action. Never colour alone: always an icon.
export function Callout({
  tone = "neutral",
  title,
  children,
  icon,
  action,
  onDismiss,
  className,
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  icon?: LucideIcon;
  action?: ReactNode;
  onDismiss?: () => void;
  className?: string;
}) {
  const t = calloutTone[tone];
  const Icon = icon ?? t.icon;
  return (
    <div role={tone === "risk" ? "alert" : "status"} className={cn("flex items-start gap-3 rounded-xl border px-4 py-3 text-sm animate-fade-in", t.box, className)}>
      <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", t.iconColor)} />
      <div className="min-w-0 flex-1">
        {title && <div className="font-medium">{title}</div>}
        {children && <div className={cn("text-[13px] leading-relaxed text-muted", title ? "mt-0.5" : undefined)}>{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
      {onDismiss && (
        <button onClick={onDismiss} aria-label="Dismiss" className="-mr-1 rounded-md p-1 text-faint hover:bg-hover hover:text-ink">
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}

export function ErrorNote({ error, title = "Something went wrong" }: { error: unknown; title?: string }) {
  return <Callout tone="risk" title={title}>{error instanceof Error ? error.message : String(error ?? "")}</Callout>;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  compact = false,
}: {
  icon?: LucideIcon;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center text-center animate-fade-in", compact ? "px-4 py-8" : "px-6 py-14")}>
      {Icon && (
        <div className="relative mb-4">
          <div className="absolute inset-0 rounded-2xl bg-accent/20 blur-xl" aria-hidden />
          <div className="relative grid size-12 place-items-center rounded-2xl border border-line bg-panel text-accent-fg shadow-sm">
            <Icon aria-hidden className="size-5" />
          </div>
        </div>
      )}
      <div className="text-[15px] font-semibold tracking-[-0.01em]">{title}</div>
      {description && <p className="mt-1 max-w-md text-sm text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
