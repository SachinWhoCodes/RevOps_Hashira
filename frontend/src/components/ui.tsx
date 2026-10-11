import { useEffect, type ReactNode } from "react";

type Tone = "neutral" | "accent" | "risk" | "warn" | "ok";

const tones: Record<Tone, string> = {
  neutral: "bg-canvas text-muted border-line",
  accent: "bg-accent-soft text-accent border-transparent",
  risk: "bg-risk-soft text-risk border-transparent",
  warn: "bg-warn-soft text-warn border-transparent",
  ok: "bg-ok-soft text-ok border-transparent",
};

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: Tone; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function Panel({ title, children, actions }: { title?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-panel">
      {title && (
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <p className="py-8 text-center text-sm text-faint">{what}…</p>;
}

export function ErrorNote({ error }: { error: unknown }) {
  return <p className="rounded-md bg-risk-soft px-3 py-2 text-sm text-risk">{error instanceof Error ? error.message : "Something went wrong"}</p>;
}

// A dialog for step-by-step guides. Esc or the backdrop closes it.
export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-line bg-panel shadow-xl">
        <header className="sticky top-0 flex items-center justify-between border-b border-line bg-panel px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button onClick={onClose} className="rounded-md px-2 text-lg leading-none text-muted hover:bg-canvas" aria-label="Close">×</button>
        </header>
        <div className="space-y-3 p-4 text-sm">{children}</div>
      </div>
    </div>
  );
}
