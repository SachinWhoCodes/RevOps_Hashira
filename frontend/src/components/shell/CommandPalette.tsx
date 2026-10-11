import { useQuery } from "@tanstack/react-query";
import { CornerDownLeft, LogOut, Moon, Search, Sun, type LucideIcon } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api, type AccountRow } from "../../api";
import { cn } from "../../lib/cn";
import { useTheme } from "../../lib/theme";
import { AccountMark, Kbd } from "../ui";
import { NAV } from "./nav";

type Command = { id: string; group: string; label: string; sub?: string; icon?: LucideIcon; mark?: ReactNode; run: () => void };

function score(c: Command, needle: string): number {
  if (!needle) return 1;
  const l = c.label.toLowerCase();
  if (l.startsWith(needle)) return 3;
  if (l.split(/\s+/).some((w) => w.startsWith(needle))) return 2.5;
  if (l.includes(needle)) return 2;
  if (c.sub?.toLowerCase().includes(needle)) return 1;
  return 0;
}

// ⌘K: jump to any page or account, or flip the theme, without leaving the keyboard.
export function CommandPalette({ onClose, isAdmin, onSignOut }: { onClose: () => void; isAdmin: boolean; onSignOut: () => void }) {
  const navigate = useNavigate();
  const { dark, setChoice } = useTheme();
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api<AccountRow[]>("/accounts"), staleTime: 60_000 });
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const go = (to: string) => () => {
      navigate(to);
      onClose();
    };
    const pages: Command[] = NAV.filter((n) => !n.admin || isAdmin).map((n) => ({
      id: `p${n.to}`,
      group: "Pages",
      label: n.label,
      sub: n.hint,
      icon: n.icon,
      run: go(n.to),
    }));
    const accs: Command[] = (accounts.data ?? []).map((a) => ({
      id: `a${a.id}`,
      group: "Accounts",
      label: a.name,
      sub: [a.domains[0], a.owner].filter(Boolean).join(" · "),
      mark: <AccountMark name={a.name} size="sm" />,
      run: go(`/accounts/${a.id}`),
    }));
    const actions: Command[] = [
      { id: "theme", group: "Actions", label: dark ? "Switch to light theme" : "Switch to dark theme", icon: dark ? Sun : Moon, run: () => { setChoice(dark ? "light" : "dark"); onClose(); } },
      { id: "out", group: "Actions", label: "Sign out", icon: LogOut, run: () => { onClose(); onSignOut(); } },
    ];
    const ranked = (list: Command[], limit: number) =>
      list
        .map((c) => [c, score(c, needle)] as const)
        .filter(([, s]) => s > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, limit)
        .map(([c]) => c);
    return [...ranked(pages, needle ? 6 : 13), ...ranked(accs, needle ? 8 : 5), ...ranked(actions, 2)];
  }, [q, accounts.data, isAdmin, dark, navigate, onClose, setChoice, onSignOut]);

  const current = Math.min(active, Math.max(results.length - 1, 0));
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((current + 1) % Math.max(results.length, 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((current - 1 + results.length) % Math.max(results.length, 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      results[current]?.run();
    } else if (e.key === "Escape") {
      onClose();
    }
  };

  let lastGroup = "";
  return createPortal(
    <div className="fixed inset-0 z-[75] flex items-start justify-center px-4 pt-[12vh]" onKeyDown={onKey}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px] animate-fade-in" onClick={onClose} aria-hidden />
      <div role="dialog" aria-modal="true" aria-label="Search" className="relative w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-panel shadow-lg animate-scale-in">
        <div className="flex items-center gap-3 border-b border-line px-4">
          <Search className="size-[18px] text-faint" aria-hidden />
          <input
            autoFocus
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setActive(0);
            }}
            placeholder="Search accounts and pages…"
            aria-label="Search accounts and pages"
            className="h-14 flex-1 bg-transparent text-[15px] text-ink outline-none placeholder:text-faint"
          />
          <Kbd>Esc</Kbd>
        </div>
        <ul role="listbox" className="max-h-[52vh] overflow-y-auto p-2">
          {!results.length && <li className="px-3 py-10 text-center text-sm text-muted">Nothing matches “{q}”.</li>}
          {results.map((c, i) => {
            const header = c.group !== lastGroup ? c.group : null;
            lastGroup = c.group;
            const Icon = c.icon;
            return (
              <li key={c.id} role="presentation">
                {header && <div className="px-3 pt-2.5 pb-1.5 text-[11px] font-semibold tracking-wide text-faint uppercase">{header}</div>}
                <button
                  role="option"
                  aria-selected={i === current}
                  ref={i === current ? (el) => el?.scrollIntoView({ block: "nearest" }) : undefined}
                  onMouseMove={() => i !== current && setActive(i)}
                  onClick={c.run}
                  className={cn("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left", i === current ? "bg-hover" : "")}
                >
                  {c.mark ?? (Icon && <Icon aria-hidden className="size-4 shrink-0 text-muted" />)}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-ink">{c.label}</span>
                    {c.sub && <span className="block truncate text-xs text-faint">{c.sub}</span>}
                  </span>
                  {i === current && <CornerDownLeft aria-hidden className="size-3.5 text-faint" />}
                </button>
              </li>
            );
          })}
        </ul>
        <div className="flex items-center gap-4 border-t border-line bg-subtle px-4 py-2 text-[11.5px] text-faint">
          <span className="flex items-center gap-1"><Kbd>↑</Kbd><Kbd>↓</Kbd> move</span>
          <span className="flex items-center gap-1"><Kbd>↵</Kbd> open</span>
          <span className="ml-auto">{accounts.data ? `${accounts.data.length} accounts` : "Loading accounts…"}</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
