import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export type Option<V extends string> = { value: V; label: ReactNode; count?: number; icon?: LucideIcon };

// Equal-width segments with a pill that slides under the active one (pure CSS: no measuring).
export function Segmented<V extends string>({
  options,
  value,
  onChange,
  size = "sm",
  className,
  label,
}: {
  options: Option<V>[];
  value: V;
  onChange: (v: V) => void;
  size?: "xs" | "sm";
  className?: string;
  label?: string;
}) {
  const index = Math.max(0, options.findIndex((o) => o.value === value));
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("relative inline-grid rounded-lg border border-line bg-subtle p-0.5", className)}
      style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
    >
      <span
        aria-hidden
        className="absolute top-0.5 bottom-0.5 left-0.5 rounded-md bg-panel shadow-sm ring-1 ring-line transition-transform duration-300 ease-[cubic-bezier(.16,1,.3,1)]"
        style={{ width: `calc((100% - 4px) / ${options.length})`, transform: `translateX(${index * 100}%)` }}
      />
      {options.map((o) => {
        const active = o.value === value;
        const Icon = o.icon;
        return (
          <button
            key={o.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className={cn(
              "relative z-10 inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors",
              size === "xs" ? "h-6 px-2 text-xs" : "h-7 px-3 text-[13px]",
              active ? "text-ink" : "text-muted hover:text-ink",
            )}
          >
            {Icon && <Icon aria-hidden className="size-3.5" />}
            {o.label}
            {o.count !== undefined && (
              <span className={cn("rounded px-1 text-[11px] tabular-nums", active ? "bg-accent-soft text-accent-fg" : "bg-hover text-faint")}>{o.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// Underlined tabs for switching sections of one page.
export function Tabs<V extends string>({ tabs, value, onChange, className }: { tabs: Option<V>[]; value: V; onChange: (v: V) => void; className?: string }) {
  return (
    <div role="tablist" className={cn("flex gap-1 overflow-x-auto border-b border-line", className)}>
      {tabs.map((t) => {
        const active = t.value === value;
        const Icon = t.icon;
        return (
          <button
            key={t.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.value)}
            className={cn(
              "group relative -mb-px inline-flex items-center gap-1.5 px-3 pt-1 pb-3 text-[13.5px] font-medium whitespace-nowrap transition-colors",
              active ? "text-ink" : "text-muted hover:text-ink",
            )}
          >
            {Icon && <Icon aria-hidden className="size-4" />}
            {t.label}
            {t.count !== undefined && <span className="rounded bg-hover px-1.5 text-[11px] text-faint tabular-nums">{t.count}</span>}
            <span
              aria-hidden
              className={cn(
                "absolute inset-x-2 bottom-0 h-0.5 origin-center rounded-full bg-accent transition-transform duration-300",
                active ? "scale-x-100" : "scale-x-0 group-hover:scale-x-50 group-hover:bg-line-strong",
              )}
            />
          </button>
        );
      })}
    </div>
  );
}
