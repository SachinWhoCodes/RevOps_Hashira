import { X, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/cn";

// Lock page scroll while an overlay is open, and give focus back to where it was on close.
function useOverlayFocus(panel: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus({ preventScroll: true });
    return () => {
      document.body.style.overflow = overflow;
      previous?.focus?.({ preventScroll: true });
    };
  }, [panel]);
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
}

export function Modal({
  title,
  description,
  icon: Icon,
  onClose,
  children,
  footer,
  size = "md",
}: {
  title: ReactNode;
  description?: ReactNode;
  icon?: LucideIcon;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  const panel = useRef<HTMLDivElement>(null);
  useOverlayFocus(panel);
  useEscape(onClose);
  const width = { sm: "max-w-md", md: "max-w-2xl", lg: "max-w-4xl" }[size];
  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-end justify-center p-0 sm:items-center sm:p-6">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px] animate-fade-in" onClick={onClose} aria-hidden />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        tabIndex={-1}
        className={cn(
          "relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-lg outline-none animate-scale-in sm:rounded-2xl",
          width,
        )}
      >
        <header className="flex items-start gap-3 border-b border-line px-6 py-4">
          {Icon && (
            <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent-fg">
              <Icon aria-hidden className="size-4" />
            </span>
          )}
          <div className="min-w-0 flex-1">
            <h2 className="text-[15px] font-semibold tracking-[-0.01em]">{title}</h2>
            {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
          </div>
          <button onClick={onClose} aria-label="Close" className="-mr-2 rounded-lg p-1.5 text-faint transition-colors hover:bg-hover hover:text-ink">
            <X className="size-4" />
          </button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5 text-sm leading-relaxed">{children}</div>
        {footer && <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-subtle px-6 py-3">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({ title, onClose, children, actions }: { title: ReactNode; onClose: () => void; children: ReactNode; actions?: ReactNode }) {
  const panel = useRef<HTMLElement>(null);
  useOverlayFocus(panel);
  useEscape(onClose);
  return createPortal(
    <div className="fixed inset-0 z-[60]">
      <div className="absolute inset-0 bg-black/30 animate-fade-in" onClick={onClose} aria-hidden />
      <aside
        ref={panel}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col border-l border-line bg-panel shadow-lg outline-none animate-slide-in-right"
      >
        <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <h2 className="text-[14px] font-semibold">{title}</h2>
          <div className="flex items-center gap-1">
            {actions}
            <button onClick={onClose} aria-label="Close" className="rounded-lg p-1.5 text-faint transition-colors hover:bg-hover hover:text-ink">
              <X className="size-4" />
            </button>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------- anchored layers

type Place = CSSProperties;

// Position a layer next to its anchor with fixed coordinates; open upwards when there is no room below.
function placeFor(anchor: DOMRect, align: "start" | "end", room: number): Place {
  const below = window.innerHeight - anchor.bottom;
  const up = below < room && anchor.top > below;
  const vertical: Place = up ? { bottom: window.innerHeight - anchor.top + 6 } : { top: anchor.bottom + 6 };
  const horizontal: Place = align === "end" ? { right: Math.max(8, window.innerWidth - anchor.right) } : { left: Math.max(8, anchor.left) };
  return { position: "fixed", ...vertical, ...horizontal, maxHeight: Math.max(160, (up ? anchor.top : below) - 16) };
}

export function Popover({
  trigger,
  children,
  align = "end",
  width = 320,
  room = 320,
  className,
}: {
  trigger: (props: { onClick: (e: React.MouseEvent<HTMLElement>) => void; "aria-expanded": boolean }) => ReactNode;
  children: ReactNode | ((close: () => void) => ReactNode);
  align?: "start" | "end";
  width?: number;
  room?: number;
  className?: string;
}) {
  const [place, setPlace] = useState<Place | null>(null);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const layer = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setPlace(null), []);
  const open = place !== null;

  // Follow the anchor while open; close on a click outside both the anchor and the layer, or on Escape.
  useEffect(() => {
    if (!open || !anchor) return;
    const reposition = () => setPlace(placeFor(anchor.getBoundingClientRect(), align, room));
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (layer.current?.contains(target) || anchor.contains(target)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, anchor, align, room, close]);

  const onClick = (e: React.MouseEvent<HTMLElement>) => {
    e.stopPropagation();
    if (open) return close();
    const el = e.currentTarget;
    setAnchor(el);
    setPlace(placeFor(el.getBoundingClientRect(), align, room));
  };

  return (
    <>
      {trigger({ onClick, "aria-expanded": place !== null })}
      {place &&
        createPortal(
          <div
            ref={layer}
            style={{ ...place, width: Math.min(width, window.innerWidth - 16) }}
            className={cn("z-[80] overflow-y-auto rounded-xl border border-line bg-panel p-1.5 shadow-lg animate-scale-in", className)}
            onClick={(e) => e.stopPropagation()}
          >
            {typeof children === "function" ? children(close) : children}
          </div>,
          document.body,
        )}
    </>
  );
}

export type MenuItem =
  | { label: ReactNode; icon?: LucideIcon; onSelect: () => void; tone?: "risk"; disabled?: boolean; hint?: ReactNode }
  | "divider";

export function Menu({
  trigger,
  items,
  align = "end",
  width = 240,
  header,
}: {
  trigger: Parameters<typeof Popover>[0]["trigger"];
  items: MenuItem[];
  align?: "start" | "end";
  width?: number;
  header?: ReactNode;
}) {
  return (
    <Popover trigger={trigger} align={align} width={width} room={items.length * 36 + 40}>
      {(close) => (
        <div role="menu" className="text-[13px]">
          {header && <div className="border-b border-line px-2.5 pt-1.5 pb-2.5">{header}</div>}
          {items.map((item, i) =>
            item === "divider" ? (
              <div key={i} className="my-1 h-px bg-line" />
            ) : (
              <button
                key={i}
                role="menuitem"
                disabled={item.disabled}
                onClick={() => {
                  close();
                  item.onSelect();
                }}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors disabled:opacity-50",
                  item.tone === "risk" ? "text-risk-fg hover:bg-risk-soft" : "text-ink hover:bg-hover",
                )}
              >
                {item.icon && <item.icon aria-hidden className="size-4 shrink-0 text-muted" />}
                <span className="flex-1">{item.label}</span>
                {item.hint && <span className="text-xs text-faint">{item.hint}</span>}
              </button>
            ),
          )}
        </div>
      )}
    </Popover>
  );
}

// A styled tooltip for short explanations (replaces slow native titles on the elements that matter).
export function Tooltip({ content, children, className }: { content: ReactNode; children: ReactNode; className?: string }) {
  const [pos, setPos] = useState<{ x: number; y: number; up: boolean } | null>(null);
  const show = (e: React.SyntheticEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const up = r.top > 90;
    const x = Math.min(Math.max(r.left + r.width / 2, 150), window.innerWidth - 150);
    setPos({ x, y: up ? r.top - 8 : r.bottom + 8, up });
  };
  const hide = () => setPos(null);
  return (
    <span className={cn("inline-flex", className)} onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}>
      {children}
      {pos &&
        createPortal(
          <span
            role="tooltip"
            style={{ left: pos.x, top: pos.y, transform: `translate(-50%, ${pos.up ? "-100%" : "0"})` }}
            className="pointer-events-none fixed z-[90] max-w-[280px] rounded-lg bg-ink px-2.5 py-1.5 text-xs leading-relaxed font-normal text-canvas shadow-lg animate-fade-in"
          >
            {content}
          </span>,
          document.body,
        )}
    </span>
  );
}
