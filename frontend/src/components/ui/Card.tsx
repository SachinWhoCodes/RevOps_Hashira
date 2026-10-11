import { ArrowLeft, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";

// The one surface of the app: a panel with an optional header (title, description, actions).
export function Card({
  title,
  description,
  icon: Icon,
  actions,
  children,
  className,
  bodyClassName,
  id,
  flush = false,
  divided = true,
}: {
  title?: ReactNode;
  description?: ReactNode;
  icon?: LucideIcon;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
  flush?: boolean;
  divided?: boolean;
}) {
  const header = title || actions;
  return (
    <section id={id} className={cn("scroll-mt-24 rounded-xl border border-line bg-panel shadow-xs transition-shadow", className)}>
      {header && (
        <header className={cn("flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-5 py-3.5", divided && "border-b border-line")}>
          <div className="flex min-w-0 items-center gap-2.5">
            {Icon && (
              <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-subtle text-muted ring-1 ring-line">
                <Icon aria-hidden className="size-[15px]" />
              </span>
            )}
            <div className="min-w-0">
              {title && <h2 className="text-[14px] leading-5 font-semibold tracking-[-0.01em]">{title}</h2>}
              {description && <p className="text-[12.5px] leading-5 text-muted">{description}</p>}
            </div>
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn(!flush && (header && !divided ? "px-5 pb-5" : "p-5"), bodyClassName)}>{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  back,
  eyebrow,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  back?: { to: string; label: string };
  eyebrow?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="mb-6 animate-fade-up">
      {back && (
        <Link to={back.to} className="group mb-3 inline-flex items-center gap-1.5 text-[13px] text-muted transition-colors hover:text-ink">
          <ArrowLeft aria-hidden className="size-3.5 transition-transform group-hover:-translate-x-0.5" />
          {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          {eyebrow && <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">{eyebrow}</div>}
          <h1 className="text-[22px] leading-tight font-semibold tracking-[-0.025em] text-ink sm:text-[26px]">{title}</h1>
          {description && <p className="mt-1.5 max-w-3xl text-[14px] leading-relaxed text-muted">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

// Label/value pairs in a tidy two-column grid.
export function DefinitionList({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-2 text-[13px]", className)}>
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("text-[11px] font-semibold tracking-[0.06em] text-faint uppercase", className)}>{children}</div>;
}
