import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AccountRow } from "../api";
import { Badge, ErrorNote, Loading, PageHeader } from "../components/ui";
import { ago, money } from "../format";

type SortKey = "name" | "days_to_renewal" | "arr" | "last_activity_at";

export default function AccountsPage() {
  const [q, setQ] = useState("");
  const [showChurned, setShowChurned] = useState(false);
  const [sort, setSort] = useState<SortKey>("days_to_renewal");
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api<AccountRow[]>("/accounts") });

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = (accounts.data ?? []).filter(
      (a) =>
        (showChurned || a.status === "active") &&
        (!needle || a.name.toLowerCase().includes(needle) || a.domains.some((d) => d.includes(needle))),
    );
    const val = (a: AccountRow) => {
      const v = a[sort];
      if (v == null) return sort === "name" ? "" : Number.POSITIVE_INFINITY;
      return v;
    };
    return list.sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      if (sort === "arr" || sort === "last_activity_at") return av < bv ? 1 : av > bv ? -1 : 0;
      return av < bv ? -1 : av > bv ? 1 : 0;
    });
  }, [accounts.data, q, showChurned, sort]);

  const th = (key: SortKey, text: string) => (
    <th className="px-3 py-2 text-left font-medium">
      <button className={sort === key ? "text-ink" : "hover:text-ink"} onClick={() => setSort(key)}>
        {text}
        {sort === key ? " ↓" : ""}
      </button>
    </th>
  );

  return (
    <>
      <PageHeader
        title="Accounts"
        subtitle={accounts.data ? `${rows.length} shown · ${accounts.data.length} total` : undefined}
      >
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-sm text-muted">
            <input type="checkbox" checked={showChurned} onChange={(e) => setShowChurned(e.target.checked)} />
            Show churned
          </label>
          <input
            placeholder="Search name or domain"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="w-56 rounded-md border border-line bg-panel px-3 py-1.5 text-sm outline-none focus:border-accent"
          />
        </div>
      </PageHeader>
      {accounts.isLoading && <Loading />}
      {accounts.error && <ErrorNote error={accounts.error} />}
      {accounts.data && (
        <div className="overflow-x-auto rounded-lg border border-line bg-panel">
          <table className="w-full text-sm">
            <thead className="border-b border-line text-xs text-muted">
              <tr>
                {th("name", "Account")}
                <th className="px-3 py-2 text-left font-medium">Owner</th>
                {th("days_to_renewal", "Renewal")}
                {th("arr", "ARR")}
                {th("last_activity_at", "Last activity")}
                <th className="px-3 py-2 text-left font-medium">Next meeting</th>
                <th className="px-3 py-2 text-right font-medium">Emails 30d</th>
                <th className="px-3 py-2 text-right font-medium">Active users 7d</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id} className="border-b border-line last:border-0 hover:bg-canvas">
                  <td className="px-3 py-2">
                    <Link to={`/accounts/${a.id}`} className="font-medium text-ink hover:text-accent">
                      {a.name}
                    </Link>
                    <div className="text-xs text-faint">{a.domains.join(", ")}</div>
                  </td>
                  <td className="px-3 py-2 text-muted">{a.owner ?? "—"}</td>
                  <td className="px-3 py-2">
                    {a.status === "churned" ? (
                      <Badge tone="risk">churned</Badge>
                    ) : a.days_to_renewal == null ? (
                      <span className="text-faint">unknown</span>
                    ) : (
                      <Badge tone={a.days_to_renewal <= 60 ? "warn" : "neutral"}>in {a.days_to_renewal}d</Badge>
                    )}
                  </td>
                  <td className="px-3 py-2 tabular-nums">{money(a.arr)}</td>
                  <td className="px-3 py-2 text-muted">{ago(a.last_activity_at)}</td>
                  <td className="px-3 py-2 text-muted">{a.next_meeting_at ? ago(a.next_meeting_at) : <span className="text-faint">none booked</span>}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.emails_30d}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.active_users_7d ?? <span className="text-faint">no data</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
