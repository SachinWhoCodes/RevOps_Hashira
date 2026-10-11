import { ArrowDown, ArrowUp, ArrowUpDown, ChevronRight, Columns3, Download, Rows3, Rows4, type LucideIcon } from "lucide-react";
import { Fragment, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { cn } from "../../lib/cn";
import { downloadCsv } from "../../lib/csv";
import { useLocalPref, useUrlBatch, useUrlState } from "../../lib/hooks";
import { Checkbox, EmptyState, ErrorNote, IconButton, Popover, SearchInput, Skeleton, Tooltip } from "../ui";
import { Pagination } from "./Pagination";

export type Column<T> = {
  id: string;
  header: string;
  cell: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number | null | undefined;
  csv?: (row: T) => string | number | null | undefined;
  align?: "left" | "right";
  className?: string;
  width?: string;
  hideable?: boolean;
  defaultHidden?: boolean;
  firstSort?: "asc" | "desc";
  info?: string;
};

type Dir = "asc" | "desc";

export type DataTableProps<T> = {
  id: string;
  rows: T[] | undefined;
  columns: Column<T>[];
  rowKey: (row: T) => string;
  loading?: boolean;
  error?: unknown;
  search?: { placeholder: string; text: (row: T) => string };
  filters?: ReactNode;
  defaultSort?: { id: string; dir: Dir };
  pageSizes?: number[];
  defaultPageSize?: number;
  rowHref?: (row: T) => string;
  empty?: { icon?: LucideIcon; title: string; description?: ReactNode; action?: ReactNode };
  csvName?: string;
  rowClassName?: (row: T) => string | undefined;
  expand?: (row: T) => ReactNode;
  noun?: string;
  urlPrefix?: string;
  title?: ReactNode;
  actions?: ReactNode;
};

function compare(a: string | number | null | undefined, b: string | number | null | undefined, dir: Dir) {
  // Missing values always sink to the bottom, whichever way the column is sorted.
  const am = a == null || a === "";
  const bm = b == null || b === "";
  if (am || bm) return am && bm ? 0 : am ? 1 : -1;
  const r = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
  return dir === "asc" ? r : -r;
}

// Every list that can grow uses this: search, sort, column choice, density, CSV of the current view,
// and pages. Search, sort and page live in the URL so a filtered view can be shared or bookmarked.
export function DataTable<T>({
  id,
  rows,
  columns,
  rowKey,
  loading,
  error,
  search,
  filters,
  defaultSort,
  pageSizes = [10, 25, 50, 100],
  defaultPageSize = 25,
  rowHref,
  empty,
  csvName,
  rowClassName,
  expand,
  noun = "rows",
  urlPrefix = "",
  title,
  actions,
}: DataTableProps<T>) {
  const navigate = useNavigate();
  const k = (name: string) => `${urlPrefix}${name}`;
  const batch = useUrlBatch();
  const [q] = useUrlState(k("q"), "");
  const [sortParam] = useUrlState(k("sort"), defaultSort ? `${defaultSort.id}.${defaultSort.dir}` : "");
  const [pageParam] = useUrlState(k("page"), "1");
  const [sizeParam] = useUrlState(k("size"), String(defaultPageSize));
  const [hidden, setHidden] = useLocalPref<string[]>(`casefile.cols.${id}`, columns.filter((c) => c.defaultHidden).map((c) => c.id));
  const [density, setDensity] = useLocalPref<"comfortable" | "compact">("casefile.density", "comfortable");
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const [sortId, sortDir] = sortParam.split(".") as [string, Dir | undefined];
  const pageSize = pageSizes.includes(Number(sizeParam)) ? Number(sizeParam) : defaultPageSize;
  const visible = columns.filter((c, i) => i === 0 || c.hideable === false || !hidden.includes(c.id));

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = (rows ?? []).filter((r) => !needle || !search || search.text(r).toLowerCase().includes(needle));
    const col = columns.find((c) => c.id === sortId);
    if (col?.sortValue && sortDir) {
      const val = col.sortValue;
      return [...list].sort((a, b) => compare(val(a), val(b), sortDir));
    }
    return list;
  }, [rows, q, search, columns, sortId, sortDir]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const page = Math.min(Math.max(1, Number(pageParam) || 1), pageCount);
  const shown = filtered.slice((page - 1) * pageSize, page * pageSize);

  const setSort = (c: Column<T>) => {
    if (!c.sortValue) return;
    const first = c.firstSort ?? "asc";
    const other: Dir = first === "asc" ? "desc" : "asc";
    const nextSort = sortId !== c.id ? `${c.id}.${first}` : sortDir === first ? `${c.id}.${other}` : defaultSort ? `${defaultSort.id}.${defaultSort.dir}` : "";
    batch({ [k("sort")]: nextSort === (defaultSort ? `${defaultSort.id}.${defaultSort.dir}` : "") ? null : nextSort, [k("page")]: null });
  };

  const exportCsv = () => {
    const cols = visible.filter((c) => c.csv);
    downloadCsv(`${csvName ?? id}-${new Date().toISOString().slice(0, 10)}`, cols.map((c) => c.header), filtered.map((r) => cols.map((c) => c.csv!(r))));
  };

  const pad = density === "compact" ? "py-2" : "py-3";
  const clickRow = (e: React.MouseEvent, r: T) => {
    if (!rowHref) return;
    if ((e.target as HTMLElement).closest("a,button,select,input,textarea,label,summary")) return;
    if (e.metaKey || e.ctrlKey) window.open(rowHref(r), "_blank");
    else navigate(rowHref(r));
  };

  return (
    <section className="overflow-hidden rounded-xl border border-line bg-panel shadow-xs animate-fade-up">
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <div className="text-[14px] font-semibold">{title}</div>
          {actions}
        </header>
      )}
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5">
        {search && (
          <SearchInput
            value={q}
            onChange={(v) => batch({ [k("q")]: v || null, [k("page")]: null })}
            placeholder={search.placeholder}
            className="w-full sm:w-64"
          />
        )}
        {filters}
        <div className="ml-auto flex items-center gap-0.5">
          {csvName && <IconButton icon={Download} label="Download this view as CSV" onClick={exportCsv} disabled={!filtered.length} />}
          {columns.length > 3 && (
            <Popover
              width={220}
              trigger={(p) => (
                <button {...p} aria-label="Choose columns" title="Choose columns" className="grid size-8 place-items-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink">
                  <Columns3 className="size-4" />
                </button>
              )}
            >
              <div className="px-2 pt-1 pb-2 text-[11px] font-semibold tracking-wide text-faint uppercase">Columns</div>
              <div className="space-y-1.5 px-2 pb-1">
                {columns.map((c, i) => (
                  <Checkbox
                    key={c.id}
                    label={c.header}
                    disabled={i === 0 || c.hideable === false}
                    checked={!hidden.includes(c.id) || i === 0 || c.hideable === false}
                    onChange={(v) => setHidden(v ? hidden.filter((h) => h !== c.id) : [...hidden, c.id])}
                  />
                ))}
              </div>
            </Popover>
          )}
          <IconButton
            icon={density === "compact" ? Rows3 : Rows4}
            label={density === "compact" ? "Comfortable rows" : "Compact rows"}
            onClick={() => setDensity(density === "compact" ? "comfortable" : "compact")}
          />
        </div>
      </div>

      {error ? (
        <div className="p-4">
          <ErrorNote error={error} />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[13.5px]">
            <thead>
              <tr className="border-b border-line bg-subtle">
                {expand && <th className="w-8" aria-label="Expand" />}
                {visible.map((c) => {
                  const active = sortId === c.id && !!sortDir;
                  const SortIcon = active ? (sortDir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
                  return (
                    <th
                      key={c.id}
                      scope="col"
                      style={c.width ? { width: c.width } : undefined}
                      aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : undefined}
                      className={cn("px-3.5 py-2.5 text-[12px] font-medium whitespace-nowrap text-muted", c.align === "right" ? "text-right" : "text-left")}
                    >
                      {c.sortValue ? (
                        <button
                          onClick={() => setSort(c)}
                          className={cn("group inline-flex items-center gap-1 transition-colors hover:text-ink", active && "text-ink", c.align === "right" && "flex-row-reverse")}
                        >
                          {c.header}
                          <SortIcon aria-hidden className={cn("size-3.5 transition-opacity", active ? "opacity-100" : "opacity-0 group-hover:opacity-60")} />
                        </button>
                      ) : c.info ? (
                        <Tooltip content={c.info}>
                          <span className="cursor-help border-b border-dotted border-faint">{c.header}</span>
                        </Tooltip>
                      ) : (
                        c.header
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody key={`${page}-${sortParam}-${q}`} className="animate-fade-in">
              {loading &&
                !rows &&
                Array.from({ length: 6 }, (_, i) => (
                  <tr key={i} className="border-b border-line last:border-0">
                    {expand && <td />}
                    {visible.map((c) => (
                      <td key={c.id} className={cn("px-3.5", pad)}>
                        <Skeleton className={cn("h-3.5", c.align === "right" ? "ml-auto w-12" : i % 2 ? "w-2/3" : "w-1/2")} />
                      </td>
                    ))}
                  </tr>
                ))}
              {shown.map((r) => {
                const key = rowKey(r);
                const isOpen = !!open[key];
                return (
                  <Fragment key={key}>
                    <tr
                      onClick={(e) => clickRow(e, r)}
                      className={cn(
                        "border-b border-line transition-colors last:border-0 hover:bg-hover/60",
                        rowHref && "cursor-pointer",
                        isOpen && "bg-hover/40",
                        rowClassName?.(r),
                      )}
                    >
                      {expand && (
                        <td className="pl-3">
                          <button
                            onClick={() => setOpen((o) => ({ ...o, [key]: !isOpen }))}
                            aria-expanded={isOpen}
                            aria-label={isOpen ? "Hide details" : "Show details"}
                            className="grid size-6 place-items-center rounded-md text-faint hover:bg-hover hover:text-ink"
                          >
                            <ChevronRight className={cn("size-4 transition-transform", isOpen && "rotate-90")} />
                          </button>
                        </td>
                      )}
                      {visible.map((c) => (
                        <td key={c.id} className={cn("px-3.5 align-middle", pad, c.align === "right" && "text-right tabular-nums", c.className)}>
                          {c.cell(r)}
                        </td>
                      ))}
                    </tr>
                    {expand && isOpen && (
                      <tr className="border-b border-line bg-subtle/60 last:border-0">
                        <td />
                        <td colSpan={visible.length} className="px-4 py-3 animate-fade-in">
                          {expand(r)}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          {rows && !filtered.length && (
            <EmptyState
              compact
              icon={empty?.icon}
              title={q ? `Nothing matches “${q}”` : (empty?.title ?? `No ${noun}`)}
              description={q ? "Try a different search or clear the filters." : empty?.description}
              action={q ? undefined : empty?.action}
            />
          )}
        </div>
      )}

      {rows && filtered.length > 0 && (
        <div className="border-t border-line">
          <Pagination
            page={page}
            pageCount={pageCount}
            onPage={(p) => batch({ [k("page")]: p === 1 ? null : String(p) })}
            total={filtered.length}
            filteredFrom={rows.length}
            pageSize={pageSize}
            pageSizes={pageSizes}
            onPageSize={(n) => batch({ [k("size")]: n === defaultPageSize ? null : String(n), [k("page")]: null })}
            noun={noun}
          />
        </div>
      )}
    </section>
  );
}
