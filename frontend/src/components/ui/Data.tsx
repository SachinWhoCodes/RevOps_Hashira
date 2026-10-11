import { ArrowUpRight, type LucideIcon } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";
import { useCountUp } from "../../lib/hooks";
import { cn } from "../../lib/cn";
import type { Tone } from "./Badge";

// ---------------------------------------------------------------- stat tile

export function StatTile({
  label,
  value,
  format = (n) => Math.round(n).toLocaleString("en-IN"),
  icon: Icon,
  hint,
  tone = "neutral",
  to,
  delay = 0,
  children,
}: {
  label: string;
  value: number | null;
  format?: (n: number) => string;
  icon?: LucideIcon;
  hint?: ReactNode;
  tone?: Tone;
  to?: string;
  delay?: number;
  children?: ReactNode;
}) {
  const shown = useCountUp(value ?? 0);
  const iconTone = {
    neutral: "bg-subtle text-muted ring-line",
    accent: "bg-accent-soft text-accent-fg ring-accent/20",
    risk: "bg-risk-soft text-risk-fg ring-risk/20",
    warn: "bg-warn-soft text-warn-fg ring-warn/25",
    ok: "bg-ok-soft text-ok-fg ring-ok/20",
  }[tone];
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[13px] font-medium text-muted">
          {Icon && (
            <span className={cn("grid size-7 place-items-center rounded-lg ring-1", iconTone)}>
              <Icon aria-hidden className="size-[15px]" />
            </span>
          )}
          {label}
        </div>
        {to && <ArrowUpRight aria-hidden className="size-4 text-faint opacity-0 transition-all group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:opacity-100" />}
      </div>
      <div className="mt-3 text-[28px] leading-none font-semibold tracking-[-0.03em] text-ink">{value == null ? "—" : format(shown)}</div>
      {hint && <div className="mt-2 text-[12.5px] text-muted">{hint}</div>}
      {children}
    </>
  );
  const cls = "group block rounded-xl border border-line bg-panel p-5 shadow-xs animate-fade-up";
  const style = { animationDelay: `${delay}ms` };
  return to ? (
    <Link to={to} style={style} className={cn(cls, "transition-[border-color,box-shadow,transform] hover:-translate-y-0.5 hover:border-line-strong hover:shadow-md")}>
      {body}
    </Link>
  ) : (
    <div style={style} className={cls}>
      {body}
    </div>
  );
}

// ---------------------------------------------------------------- score ring

// Severity by the case thresholds: a risk case opens at 40 and closes below 30.
export function scoreTone(kind: "risk" | "opportunity", v: number): Tone {
  if (kind === "opportunity") return v >= 40 ? "ok" : v > 0 ? "accent" : "neutral";
  return v >= 40 ? "risk" : v >= 30 ? "warn" : v > 0 ? "neutral" : "neutral";
}

const ringStroke: Record<Tone, string> = {
  neutral: "stroke-faint",
  accent: "stroke-accent",
  risk: "stroke-risk",
  warn: "stroke-warn",
  ok: "stroke-ok",
};

export function ScoreRing({
  value,
  kind = "risk",
  size = 44,
  stroke = 4,
  label,
  className,
}: {
  value: number;
  kind?: "risk" | "opportunity";
  size?: number;
  stroke?: number;
  label?: string;
  className?: string;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(100, value));
  const tone = scoreTone(kind, v);
  return (
    <div
      className={cn("relative grid shrink-0 place-items-center", className)}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`${label ?? (kind === "risk" ? "Risk" : "Opportunity")} ${v} of 100`}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="stroke-line" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - v / 100)}
          className={cn(ringStroke[tone], "animate-ring-fill")}
          style={{ "--ring-c": `${c}` } as CSSProperties}
        />
      </svg>
      <span className={cn("absolute font-semibold tracking-[-0.02em] tabular-nums text-ink", size >= 64 ? "text-xl" : size >= 48 ? "text-[15px]" : "text-[13px]")}>{v}</span>
    </div>
  );
}

// A thin horizontal meter (0..max) with an optional marker (e.g. the case threshold).
export function Meter({ value, max = 100, tone = "accent", marker, className }: { value: number; max?: number; tone?: Tone; marker?: number; className?: string }) {
  const fill = { neutral: "bg-faint", accent: "bg-accent", risk: "bg-risk", warn: "bg-warn", ok: "bg-ok" }[tone];
  const track = { neutral: "bg-hover", accent: "bg-accent-soft", risk: "bg-risk-soft", warn: "bg-warn-soft", ok: "bg-ok-soft" }[tone];
  return (
    <div className={cn("relative h-1.5 w-full overflow-hidden rounded-full", track, className)}>
      <div className={cn("h-full origin-left rounded-full animate-grow-x", fill)} style={{ width: `${Math.max(0, Math.min(100, (value / max) * 100))}%` }} />
      {marker !== undefined && <div className="absolute inset-y-0 w-px bg-ink/40" style={{ left: `${(marker / max) * 100}%` }} />}
    </div>
  );
}

// ---------------------------------------------------------------- avatars

const HUES = [
  "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
  "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  "bg-teal-500/15 text-teal-700 dark:text-teal-300",
  "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  "bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-300",
];

function hueFor(seed: string) {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return HUES[h % HUES.length];
}

export function initials(name: string): string {
  const clean = name.replace(/@.*/, "").replace(/[._-]+/g, " ").trim();
  const parts = clean.split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts.length === 1 ? parts[0].slice(0, 2) : parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

const avatarSize = { xs: "size-5 text-[9px]", sm: "size-6 text-[10px]", md: "size-8 text-xs", lg: "size-10 text-sm", xl: "size-12 text-base" } as const;

// A person: round, initials, a stable colour per name (identity only, never status).
export function Avatar({ name, size = "sm", className }: { name: string; size?: keyof typeof avatarSize; className?: string }) {
  return (
    <span aria-hidden className={cn("inline-grid shrink-0 place-items-center rounded-full font-semibold", avatarSize[size], hueFor(name), className)}>
      {initials(name)}
    </span>
  );
}

// A company: rounded square, first letter.
export function AccountMark({ name, size = "md", className }: { name: string; size?: keyof typeof avatarSize; className?: string }) {
  return (
    <span aria-hidden className={cn("inline-grid shrink-0 place-items-center rounded-lg font-semibold ring-1 ring-inset ring-black/5 dark:ring-white/10", avatarSize[size], hueFor(name), className)}>
      {name.trim()[0]?.toUpperCase() ?? "?"}
    </span>
  );
}

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd className={cn("inline-flex h-5 min-w-5 items-center justify-center rounded border border-line bg-subtle px-1 font-sans text-[10.5px] font-medium text-muted shadow-[0_1px_0_var(--c-line)]", className)}>
      {children}
    </kbd>
  );
}
