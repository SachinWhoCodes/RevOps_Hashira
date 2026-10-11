import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { cn } from "../../lib/cn";

type ToastTone = "ok" | "risk" | "warn" | "neutral";
type Toast = { id: number; tone: ToastTone; title: ReactNode; description?: ReactNode };
type Push = (t: Omit<Toast, "id">) => void;

const ToastContext = createContext<Push>(() => undefined);
export const useToast = () => useContext(ToastContext);

const icons = { ok: CircleCheck, risk: CircleAlert, warn: TriangleAlert, neutral: Info };
const iconTone = { ok: "text-ok-fg", risk: "text-risk-fg", warn: "text-warn-fg", neutral: "text-accent-fg" };

let next = 1;

// Short confirmations of what just happened ("3 lines approved"). Errors that need reading stay inline.
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback<Push>(
    (t) => {
      const id = next++;
      setToasts((list) => [...list.slice(-3), { ...t, id }]);
      window.setTimeout(() => dismiss(id), t.tone === "risk" ? 7000 : 4500);
    },
    [dismiss],
  );
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-[100] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2">
        {toasts.map((t) => {
          const Icon = icons[t.tone];
          return (
            <div key={t.id} role="status" className="pointer-events-auto flex items-start gap-3 rounded-xl border border-line bg-panel px-4 py-3 shadow-lg animate-scale-in">
              <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", iconTone[t.tone])} />
              <div className="min-w-0 flex-1 text-sm">
                <div className="font-medium text-ink">{t.title}</div>
                {t.description && <div className="mt-0.5 text-[13px] text-muted">{t.description}</div>}
              </div>
              <button onClick={() => dismiss(t.id)} aria-label="Dismiss" className="-mr-1 rounded-md p-1 text-faint hover:bg-hover hover:text-ink">
                <X className="size-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
