import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { cn } from "../../lib/cn";

// Page numbers with ellipses: 1 … 4 5 6 … 20 (always the first, last and two around the current).
function pages(current: number, count: number): (number | "…")[] {
  if (count <= 7) return Array.from({ length: count }, (_, i) => i + 1);
  const out: (number | "…")[] = [1];
  const lo = Math.max(2, current - 1);
  const hi = Math.min(count - 1, current + 1);
  if (lo > 2) out.push("…");
  for (let p = lo; p <= hi; p++) out.push(p);
  if (hi < count - 1) out.push("…");
  out.push(count);
  return out;
}

const stepBtn =
  "grid size-8 place-items-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-35";

export function Pagination({
  page,
  pageCount,
  onPage,
  total,
  pageSize,
  onPageSize,
  pageSizes = [10, 25, 50, 100],
  noun = "rows",
  filteredFrom,
}: {
  page: number;
  pageCount: number;
  onPage: (p: number) => void;
  total: number;
  pageSize: number;
  onPageSize?: (n: number) => void;
  pageSizes?: number[];
  noun?: string;
  filteredFrom?: number;
}) {
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3 text-[13px] text-muted">
      <div className="flex items-center gap-3">
        <span className="tabular-nums">
          {total === 0 ? `No ${noun}` : <><span className="font-medium text-ink">{from.toLocaleString()}–{to.toLocaleString()}</span> of {total.toLocaleString()} {noun}</>}
          {filteredFrom !== undefined && filteredFrom !== total && <span className="text-faint"> · filtered from {filteredFrom.toLocaleString()}</span>}
        </span>
        {onPageSize && (
          <label className="hidden items-center gap-1.5 sm:flex">
            <span className="text-faint">Rows</span>
            <select
              value={pageSize}
              onChange={(e) => onPageSize(Number(e.target.value))}
              className="h-7 rounded-md border border-line bg-panel px-1.5 text-[13px] text-ink outline-none focus:border-accent"
            >
              {pageSizes.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      {pageCount > 1 && (
        <div className="flex items-center gap-0.5">
          <button className={cn(stepBtn, "hidden sm:grid")} onClick={() => onPage(1)} disabled={page <= 1} aria-label="First page">
            <ChevronsLeft className="size-4" />
          </button>
          <button className={stepBtn} onClick={() => onPage(page - 1)} disabled={page <= 1} aria-label="Previous page">
            <ChevronLeft className="size-4" />
          </button>
          <span className="px-2 tabular-nums sm:hidden">
            {page} / {pageCount}
          </span>
          <div className="hidden items-center gap-0.5 sm:flex">
            {pages(page, pageCount).map((p, i) =>
              p === "…" ? (
                <span key={`e${i}`} className="w-6 text-center text-faint">…</span>
              ) : (
                <button
                  key={p}
                  onClick={() => onPage(p)}
                  aria-current={p === page ? "page" : undefined}
                  className={cn(
                    "h-8 min-w-8 rounded-lg px-2 tabular-nums transition-colors",
                    p === page ? "bg-ink font-medium text-canvas" : "text-muted hover:bg-hover hover:text-ink",
                  )}
                >
                  {p}
                </button>
              ),
            )}
          </div>
          <button className={stepBtn} onClick={() => onPage(page + 1)} disabled={page >= pageCount} aria-label="Next page">
            <ChevronRight className="size-4" />
          </button>
          <button className={cn(stepBtn, "hidden sm:grid")} onClick={() => onPage(pageCount)} disabled={page >= pageCount} aria-label="Last page">
            <ChevronsRight className="size-4" />
          </button>
        </div>
      )}
    </nav>
  );
}

// Keyset pagination for server-paged lists (newest first): no total, just "newer" and "older".
export function CursorPager({
  label,
  onNewer,
  onOlder,
  hasNewer,
  hasOlder,
  pageSize,
  onPageSize,
  pageSizes = [25, 50, 100, 200],
}: {
  label: React.ReactNode;
  onNewer: () => void;
  onOlder: () => void;
  hasNewer: boolean;
  hasOlder: boolean;
  pageSize: number;
  onPageSize: (n: number) => void;
  pageSizes?: number[];
}) {
  return (
    <nav aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3 text-[13px] text-muted">
      <div className="flex items-center gap-3">
        <span className="tabular-nums">{label}</span>
        <label className="hidden items-center gap-1.5 sm:flex">
          <span className="text-faint">Rows</span>
          <select
            value={pageSize}
            onChange={(e) => onPageSize(Number(e.target.value))}
            className="h-7 rounded-md border border-line bg-panel px-1.5 text-[13px] text-ink outline-none focus:border-accent"
          >
            {pageSizes.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="flex items-center gap-1">
        <button onClick={onNewer} disabled={!hasNewer} className="inline-flex h-8 items-center gap-1 rounded-lg px-2.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-35">
          <ChevronLeft className="size-4" /> Newer
        </button>
        <button onClick={onOlder} disabled={!hasOlder} className="inline-flex h-8 items-center gap-1 rounded-lg px-2.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-35">
          Older <ChevronRight className="size-4" />
        </button>
      </div>
    </nav>
  );
}
